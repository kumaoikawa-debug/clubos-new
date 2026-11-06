"""外部品牌订单履约桥（2026-11-06 起）。

背景：镜像方案（peaktrace_catalog）打通了「上游上架 → ClubOS 可卖」，但订单层
是断的 —— 顾客买 PEAKTRACE 商品后本地照常扣 PT-MIRROR 镜像库存，上游却毫不知情，
两边各扣各库存、且没有任何一方负责发货。本模块补上缺的那半环：

    支付成功（checkout._finalize_gear）
      → create_for_gear_order()   纯外部/混合订单落 external_fulfillment_orders（仅 DB）
      → due_pushes()/push_orders() 把待推订单 POST /api/b2b/orders 推给上游
                                    （上游按 clubosOrderId 幂等，并扣上游可售库存）
      → 上游仓库在 PEAKTRACE 后台发货：回调 ClubOS webhook；pull 兜底轮询
      → apply_upstream()          写运单 + 回写 gear_orders.carrier/tracking_no/status
      → C 端「我的订单」自动看到物流（me.js 本就渲染 carrier/tracking_no）

分层纪律（与 peaktrace_catalog 一致）：
* 所有 DB 函数只拿调用方传入的 ``c``，模块本身不 import db —— 便于单测与复用。
* HTTP 函数（push_orders / pull_orders）绝不碰 DB；auto loop 先读、再发请求、
  再落库，网络等待不占用 SQLite 写事务。
* 推送幂等：上游按 clubosOrderId 去重，重复推送返回已有订单，不重复扣库存。
* token 全部放 JSON body（托管网关剥自定义头与查询串），两侧默认共用
  PEAKTRACE_API_TOKEN（本地联调默认 'peaktrace-admin'）。

边界：
* 「纯外部」订单（全部明细都是外部镜像商品）由供应商直接发货，平台端禁止
  本地拣货/打包/发货（app.py 409 防护）；「混合」订单的外部部分同样推上游，
  本地链路照旧走自营部分。
* 本表记录的是「上游要发的货」；本地镜像库存在结算时已扣，上游发货后不再
  重复扣本地。
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import httpx

ORIGIN = os.environ.get('PEAKTRACE_ORIGIN',
                        'https://peaktrace-h5.app.workbuddy.host').rstrip('/')
PROVIDER = 'PEAKTRACE'

PUSH_PENDING = 'pending'
PUSH_PUSHED = 'pushed'
PUSH_FAILED = 'failed'
FUL_AWAIT = 'awaiting_shipment'
FUL_SHIPPED = 'shipped'
FUL_DELIVERED = 'delivered'


def _token() -> str:
    return os.environ.get('PEAKTRACE_API_TOKEN', 'peaktrace-admin')


def webhook_token() -> str:
    """上游回调 ClubOS 用的共享 token（与推送同一把，简单可核对）。"""
    return os.environ.get('EXTERNAL_FULFILLMENT_WEBHOOK_TOKEN', '').strip() or _token()


def external_items(c, order_id: int) -> list[dict]:
    """订单里属于外部品牌镜像商品的明细（带上游 SKU key，用于上游扣库存）。"""
    out = []
    for r in c.execute("""SELECT i.variant_id,i.quantity,p.name,p.source,p.inventory_mode,
                                 m.external_product_id,m.external_sku_id
                          FROM gear_order_items i
                          JOIN products p ON p.id=i.product_id
                          LEFT JOIN external_product_mappings m
                            ON m.variant_id=i.variant_id AND m.external_sku_id<>''
                          WHERE i.order_id=?""", (int(order_id),)):
        # 镜像商品两处标记：products.source='external_api'（目录镜像）；
        # inventory_mode 不属于自营（external_mirror 等）也算外部。二者任一即外部。
        is_external = (r['source'] == 'external_api'
                       or (r['inventory_mode'] or '') not in ('', 'platform_owned'))
        if not is_external:
            continue
        out.append({'variantId': r['variant_id'], 'quantity': int(r['quantity'] or 0),
                    'name': r['name'],
                    'externalProductId': r['external_product_id'],
                    'externalSku': r['external_sku_id']})
    return out


def order_external_mode(c, order_id: int) -> str | None:
    """'pure'=全部明细外部（禁止本地履约）/ 'mixed'=部分外部 / None=纯自营。"""
    rows = c.execute("""SELECT COUNT(*) n,
                               SUM(CASE WHEN p.source='external_api'
                                         OR (p.inventory_mode IS NOT NULL AND p.inventory_mode NOT IN ('','platform_owned'))
                                        THEN 1 ELSE 0 END) ext
                        FROM gear_order_items i JOIN products p ON p.id=i.product_id
                        WHERE i.order_id=?""", (int(order_id),)).fetchone()
    n = int(rows['n'] or 0)
    if not n:
        return None
    ext = int(rows['ext'] or 0)
    if not ext:
        return None
    return 'pure' if ext == n else 'mixed'


def create_for_gear_order(c, order_id: int) -> dict | None:
    """支付成功后挂钩：有外部明细的订单建履约记录（幂等、仅 DB、绝不出网）。

    任何异常都不该打断支付收尾 —— 调用方（_finalize_gear）自行 try/except。
    """
    try:
        items = external_items(c, order_id)
        if not items:
            return None
        o = c.execute('SELECT * FROM gear_orders WHERE id=?', (int(order_id),)).fetchone()
        if not o:
            return None
        brand = c.execute("SELECT id FROM product_brands WHERE lower(name)='peaktrace'").fetchone()
        payload = {
            'clubosOrderId': int(order_id),
            'source': 'clubos',
            'placedAt': o['created_at'],
            'consignee': {'name': o['shipping_consignee'] or '',
                          'phone': o['shipping_phone'] or '',
                          'address': o['shipping_address'] or ''},
            'items': [{'sku': it['externalSku'] or '', 'externalProductId': it['externalProductId'],
                       'name': it['name'], 'quantity': it['quantity']} for it in items],
        }
        c.execute("""INSERT INTO external_fulfillment_orders(order_id,brand_id,payload_json)
                     VALUES(?,?,?) ON CONFLICT(order_id) DO NOTHING""",
                  (int(order_id), brand['id'] if brand else None,
                   json.dumps(payload, ensure_ascii=False)))
        rec = c.execute('SELECT * FROM external_fulfillment_orders WHERE order_id=?',
                        (int(order_id),)).fetchone()
        return dict(rec) if rec else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 推送（三段式：读 → 发 → 落）
# ---------------------------------------------------------------------------
def due_pushes(c, limit: int = 20) -> list[dict]:
    """待推送记录（pending 或 failed），payload 已随行。"""
    out = []
    for r in c.execute(f"""SELECT * FROM external_fulfillment_orders
                           WHERE push_status IN ('{PUSH_PENDING}','{PUSH_FAILED}')
                           ORDER BY id LIMIT ?""", (int(limit),)):
        d = dict(r)
        try:
            d['_payload'] = json.loads(d.get('payload_json') or '{}')
        except Exception:
            d['_payload'] = None
        if d['_payload']:
            out.append(d)
    return out


def push_orders(records: list[dict], *, origin: str | None = None, timeout: float = 15.0) -> list[dict]:
    """纯 HTTP 阶段：把每条待推记录 POST 给上游 /api/b2b/orders。绝不抛错。"""
    base = (origin or ORIGIN).rstrip('/')
    results = []
    with httpx.Client(timeout=timeout) as client:
        for rec in records:
            payload = dict(rec['_payload'])
            payload['token'] = _token()
            try:
                r = client.post(base + '/api/b2b/orders', json=payload)
                if r.status_code == 200:
                    d = r.json() if isinstance(r.json(), dict) else {}
                    results.append({'id': rec['id'], 'ok': True,
                                    'externalOrderId': d.get('order', {}).get('id') if d.get('order') else None,
                                    'idempotent': bool(d.get('idempotent'))})
                else:
                    detail = ''
                    try:
                        detail = str((r.json() or {}).get('error') or '')
                    except Exception:
                        pass
                    results.append({'id': rec['id'], 'ok': False,
                                    'error': f'HTTP {r.status_code} {detail}'.strip()})
            except Exception as e:
                results.append({'id': rec['id'], 'ok': False,
                                'error': type(e).__name__ + ' ' + str(e)[:120]})
    return results


def apply_push_results(c, results: list[dict]) -> dict:
    """落推送结果：成功 → pushed；失败 → failed（保留错误信息供平台端查看）。"""
    pushed = failed = 0
    for res in results:
        if res.get('ok'):
            c.execute("""UPDATE external_fulfillment_orders
                         SET push_status=?,push_error=NULL,pushed_at=CURRENT_TIMESTAMP,
                             external_order_id=COALESCE(?,external_order_id),
                             push_attempts=push_attempts+1,updated_at=CURRENT_TIMESTAMP
                         WHERE id=?""", (PUSH_PUSHED, res.get('externalOrderId'), int(res['id'])))
            pushed += 1
        else:
            c.execute("""UPDATE external_fulfillment_orders
                         SET push_status=?,push_error=?,push_attempts=push_attempts+1,
                             updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                      (PUSH_FAILED, str(res.get('error') or '推送失败')[:300], int(res['id'])))
            failed += 1
    return {'pushed': pushed, 'failed': failed}


# ---------------------------------------------------------------------------
# 拉取（轮询兜底：上游发货但 webhook 未达 / 失败时）
# ---------------------------------------------------------------------------
def due_pulls(c, limit: int = 20) -> list[dict]:
    """已推送且本地尚未「已签收」的记录，轮询上游订单状态。

    必须同时轮 awaiting_shipment 与 shipped：上游标记签收（completed）若没走
    webhook 回调（未配 CLUBOS_ORIGIN / 回调失败），shipped 订单只能靠这里推进到
    delivered，否则状态会永久卡在 shipped。
    """
    return [dict(r) for r in c.execute(f"""SELECT * FROM external_fulfillment_orders
                                            WHERE push_status='{PUSH_PUSHED}'
                                              AND fulfill_status IN ('{FUL_AWAIT}','{FUL_SHIPPED}')
                                            ORDER BY id LIMIT ?""", (int(limit),))]


def pull_orders(records: list[dict], *, origin: str | None = None, timeout: float = 15.0) -> list[dict]:
    base = (origin or ORIGIN).rstrip('/')
    results = []
    with httpx.Client(timeout=timeout) as client:
        for rec in records:
            try:
                r = client.post(base + '/api/b2b/orders/get',
                                json={'token': _token(), 'clubosOrderId': int(rec['order_id'])})
                if r.status_code == 200:
                    d = r.json() if isinstance(r.json(), dict) else {}
                    o = d.get('order')
                    if not o:
                        results.append({'id': rec['id'], 'orderId': rec['order_id'], 'found': False})
                        continue
                    exp = o.get('express') or {}
                    results.append({'id': rec['id'], 'orderId': rec['order_id'], 'found': True,
                                    'status': str(o.get('status') or ''),
                                    'carrier': str(exp.get('company') or ''),
                                    'trackingNo': str(exp.get('no') or ''),
                                    'externalOrderId': o.get('id')})
                else:
                    results.append({'id': rec['id'], 'orderId': rec['order_id'], 'found': False,
                                    'error': f'HTTP {r.status_code}'})
            except Exception as e:
                results.append({'id': rec['id'], 'orderId': rec['order_id'], 'found': False,
                                'error': type(e).__name__ + ' ' + str(e)[:120]})
    return results


def apply_pull_results(c, results: list[dict]) -> dict:
    updated = 0
    for res in results:
        c.execute('UPDATE external_fulfillment_orders SET last_pull_at=CURRENT_TIMESTAMP WHERE id=?',
                  (int(res['id']),))
        if res.get('found') and res.get('status') in ('shipped', 'completed') and res.get('trackingNo'):
            apply_upstream(c, int(res['orderId']),
                           carrier=res.get('carrier') or PROVIDER,
                           tracking_no=res['trackingNo'],
                           status=('delivered' if res['status'] == 'completed' else 'shipped'),
                           external_order_id=res.get('externalOrderId'))
            updated += 1
    return {'polled': len(results), 'updated': updated}


# ---------------------------------------------------------------------------
# 状态回写（webhook 与 pull 共用）
# ---------------------------------------------------------------------------
# 「已签收 → 冻结佣金」由宿主注入（commission_engine 实例在 app.py，域模块不能反向 import）。
# app.py 启动时：external_fulfillment.on_delivered = lambda c, order_id: commission_engine.freeze_order(c, order_id=order_id)
on_delivered = None  # type: ignore[assignment]


def apply_upstream(c, order_id: int, *, carrier: str, tracking_no: str,
                   status: str = 'shipped', events: list[dict] | None = None,
                   external_order_id: str | None = None) -> dict:
    """把上游发货/签收结果写回本地：运单 + gear_orders + 履约记录。

    carrier/tracking_no 写 gear_orders 两列是刻意的：平台列表、C 端 me.js 读的
    就是这两列（logistics_engine 的运单表只是轨迹明细）。
    """
    from importlib import import_module
    logistics_engine = import_module('logistics_engine')
    o = c.execute('SELECT * FROM gear_orders WHERE id=?', (int(order_id),)).fetchone()
    if not o:
        raise LookupError('订单不存在')
    status = 'delivered' if str(status or '').strip().lower() == 'delivered' else 'shipped'
    carrier = str(carrier or PROVIDER).strip() or PROVIDER
    tracking_no = str(tracking_no or '').strip()
    if status == 'shipped' and not tracking_no:
        raise ValueError('缺少运单号')
    # 已是终态的幂等保护：delivered 不回退，refunded 不覆盖
    if o['status'] == 'refunded':
        raise ValueError('订单已退款，不再同步供应商物流')
    events = events or [{'time': None, 'desc': ('供应商已发货，等待物流更新' if status == 'shipped'
                                                else '买家已签收（供应商回传）')}]
    logistics_engine.upsert_shipment(
        c, order_id=int(order_id), provider=PROVIDER, carrier=carrier, carrier_code='',
        tracking_no=tracking_no or (o['tracking_no'] or ''),
        status=('delivered' if status == 'delivered' else 'in_transit'), events=events)
    if tracking_no and status == 'delivered':
        c.execute('UPDATE gear_orders SET carrier=?,tracking_no=? WHERE id=?',
                  (carrier, tracking_no, int(order_id)))
    elif tracking_no and o['status'] not in ('delivered', 'refunded'):
        # 迟到的 shipped 回调不得改写已签收/已退款订单的运单号
        c.execute('UPDATE gear_orders SET carrier=?,tracking_no=? WHERE id=?',
                  (carrier, tracking_no, int(order_id)))
    if status == 'delivered':
        c.execute("""UPDATE gear_orders SET status='delivered',
                     delivered_at=COALESCE(delivered_at,CURRENT_TIMESTAMP) WHERE id=?""",
                  (int(order_id),))
        c.execute("""UPDATE external_fulfillment_orders SET fulfill_status=?,carrier=?,tracking_no=?,
                     external_order_id=COALESCE(?,external_order_id),updated_at=CURRENT_TIMESTAMP
                     WHERE order_id=?""", (FUL_DELIVERED, carrier, tracking_no, external_order_id, int(order_id)))
        if on_delivered:
            try:
                on_delivered(c, int(order_id))
            except Exception:
                pass
    else:
        c.execute("""UPDATE gear_orders SET status=CASE WHEN status IN ('paid','processing')
                     THEN 'shipped' ELSE status END,
                     shipped_at=COALESCE(shipped_at,CURRENT_TIMESTAMP) WHERE id=?""", (int(order_id),))
        # 迟到的 shipped 回调不得把已 delivered 的履约记录降级回去
        c.execute("""UPDATE external_fulfillment_orders SET fulfill_status=?,carrier=?,tracking_no=?,
                     external_order_id=COALESCE(?,external_order_id),updated_at=CURRENT_TIMESTAMP
                     WHERE order_id=? AND fulfill_status!='delivered'""",
                  (FUL_SHIPPED, carrier, tracking_no, external_order_id, int(order_id)))
    return {'ok': True, 'orderId': int(order_id), 'fulfillStatus': status}


def handle_webhook(c, payload: dict) -> dict:
    """供应商发货回调（token 校验在 app.py 完成）。未知订单 → LookupError。"""
    oid = payload.get('clubosOrderId')
    try:
        oid = int(oid)
    except (TypeError, ValueError):
        raise ValueError('缺少 clubosOrderId')
    rec = c.execute('SELECT * FROM external_fulfillment_orders WHERE order_id=?', (oid,)).fetchone()
    if not rec:
        raise LookupError('未找到该订单的外部履约记录')
    carrier = str(payload.get('carrier') or '').strip()
    tracking = str(payload.get('trackingNo') or payload.get('tracking_no') or '').strip()
    status = str(payload.get('status') or 'shipped')
    if status == 'shipped' and not tracking:
        raise ValueError('缺少运单号')
    events = [{'time': payload.get('time'), 'desc': str(payload.get('message') or
               ('供应商已发货，等待物流更新' if status != 'delivered' else '买家已签收（供应商回传）'))}]
    return apply_upstream(c, oid, carrier=carrier or PROVIDER, tracking_no=tracking,
                          status=status, events=events,
                          external_order_id=str(payload.get('externalOrderId') or '') or None)


def sync_once(c, *, origin: str | None = None) -> dict:
    """手动兜底入口：推一批 + 拉一批（HTTP 不在 DB 事务内的三段式由调用方拼装，
    这里提供「已在事务内」的便捷组合，供平台端手动同步按钮直接调用）。"""
    due = due_pushes(c)
    pushed = apply_push_results(c, push_orders(due, origin=origin)) if due else {'pushed': 0, 'failed': 0}
    pulls = due_pulls(c)
    pulled = apply_pull_results(c, pull_orders(pulls, origin=origin)) if pulls else {'polled': 0, 'updated': 0}
    return {'push': pushed, 'pull': pulled}
