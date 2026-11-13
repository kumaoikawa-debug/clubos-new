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

import hashlib
import json
import os
from datetime import datetime
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

ORIGIN = os.environ.get('PEAKTRACE_ORIGIN',
                        'https://peaktrace-h5.app.workbuddy.host').rstrip('/')
# 2026-11-13：托管网关对所有 app 启用 sharecode 门禁（不带码一律 403「链接不完整」页）。
# 服务器到服务器的 B2B 调用必须把上游 app 的 sharecode 拼到 URL 上才能过网关；
# 业务 token 仍走 JSON body 不受影响。码可由环境变量覆盖（上游换码时无需改代码）。
UPSTREAM_SHARECODE = os.environ.get('PEAKTRACE_SHARECODE', 'Q7CFCS2XP7').strip()


def _upstream_url(base: str, path: str) -> str:
    """构造过网关的上游 URL：path + ?sharecode=...（已带 query 时用 & 追加）。"""
    url = base + path
    if UPSTREAM_SHARECODE:
        url += ('&' if '?' in url else '?') + 'sharecode=' + UPSTREAM_SHARECODE
    return url
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



def _ref_from_row(c, o) -> str:
    """生成/读取本订单的全局唯一渠道单号。

    ★ 绝不能用裸 order_id：库重置或数据迁移后自增 id 会复用，而上游云端
    commerce 文档不随重发清空，旧 B2B 单会被幂等命中（#625 串单事故）。
    优先用已落库的 external_ref；建记录前用 gear_orders.commerce_order_id
    里的随机段派生（确定性，保证 create → push → pull 全程同一个号）。
    """
    try:
        rec = c.execute('SELECT external_ref FROM external_fulfillment_orders WHERE order_id=?',
                        (int(o['id']),)).fetchone()
    except Exception:
        rec = None
    if rec and str(rec['external_ref'] or '').strip():
        return str(rec['external_ref']).strip()
    raw = str(o['commerce_order_id'] or '').strip()
    token = raw.rsplit('_', 1)[-1] if raw else uuid4().hex
    return 'CB-%s-%s' % (int(o['id']), token[:16].upper())


def _fingerprint(o, items) -> str:
    """业务指纹：上游幂等命中时用来判断是不是同一笔订单（拒绝对不上还复用）。"""
    raw = json.dumps({
        'placedAt': str(o['created_at'] or ''),
        'consignee': [str(o['shipping_consignee'] or ''), str(o['shipping_phone'] or ''),
                      str(o['shipping_address'] or '')],
        'items': [[str(it.get('externalInventoryId') or it.get('externalSku') or ''),
                   int(it.get('quantity') or 0)] for it in items],
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]


def _ref_for_rec(rec) -> str:
    """轮询用：优先 external_ref 列，存量行退回 payload 快照，最后确定性兜底。"""
    ref = str(rec.get('external_ref') or '').strip()
    if ref:
        return ref
    try:
        ref = str((json.loads(rec.get('payload_json') or '{}')).get('clubosOrderId') or '').strip()
    except Exception:
        pass
    return ref or ('CB-%s-LEGACY' % rec.get('order_id'))


def _parse_ts(s):
    raw = str(s or '').strip()
    if not raw:
        return None
    raw = raw.replace('T', ' ').rstrip('Z').split('.')[0]
    try:
        return datetime.strptime(raw[:19], '%Y-%m-%d %H:%M:%S')
    except Exception:
        return None


def external_items(c, order_id: int) -> list[dict]:
    """订单里属于外部品牌镜像商品的明细（带上游 SKU key，用于上游扣库存）。

    ★ 上游库存 key 用 external_inventory_id（`productId|spec`，如 `gongga|M`）——
    external_sku_id 存的是裸尺寸（sync_catalog 以规格名建 variant 级映射），
    拿它当 sku 上游匹配不到库存位，曾导致线上推送 409「上游库存不足：M」。
    """
    out = []
    for r in c.execute("""SELECT i.variant_id,i.quantity,p.name,p.source,p.inventory_mode,
                                 m.external_product_id,m.external_sku_id,m.external_inventory_id
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
                    'externalSku': r['external_sku_id'],
                    'externalInventoryId': r['external_inventory_id']})
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
        payload = build_payload(c, int(order_id))
        if not payload:
            return None
        brand = c.execute("SELECT id FROM product_brands WHERE lower(name)='peaktrace'").fetchone()
        c.execute("""INSERT INTO external_fulfillment_orders(order_id,brand_id,payload_json,external_ref)
                     VALUES(?,?,?,?) ON CONFLICT(order_id) DO NOTHING""",
                  (int(order_id), brand['id'] if brand else None,
                   json.dumps(payload, ensure_ascii=False), payload['clubosOrderId']))
        rec = c.execute('SELECT * FROM external_fulfillment_orders WHERE order_id=?',
                        (int(order_id),)).fetchone()
        return dict(rec) if rec else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 推送（三段式：读 → 发 → 落）
# ---------------------------------------------------------------------------
def due_pushes(c, limit: int = 20) -> list[dict]:
    """待推送记录（pending 或 failed），payload 已随行。

    failed 行的 payload_json 实时重建：快照可能来自旧版本代码（如 sku 格式
    修复前的裸尺寸），不重建则重推永远带着坏 key。
    """
    out = []
    for r in c.execute(f"""SELECT * FROM external_fulfillment_orders
                           WHERE push_status IN ('{PUSH_PENDING}','{PUSH_FAILED}')
                           ORDER BY id LIMIT ?""", (int(limit),)):
        d = dict(r)
        try:
            d['_payload'] = json.loads(d.get('payload_json') or '{}')
        except Exception:
            d['_payload'] = None
        if d.get('push_status') == PUSH_FAILED:
            try:
                rebuilt = build_payload(c, int(d['order_id']))
                if rebuilt:
                    d['_payload'] = rebuilt
                    c.execute('UPDATE external_fulfillment_orders SET payload_json=? WHERE id=?',
                              (json.dumps(rebuilt, ensure_ascii=False), int(d['id'])))
            except Exception:
                pass
        if d['_payload']:
            out.append(d)
    return out


def build_payload(c, order_id: int) -> dict | None:
    """构造推送 payload（create_for_gear_order 与 failed 重推共用）。"""
    items = external_items(c, order_id)
    if not items:
        return None
    o = c.execute('SELECT * FROM gear_orders WHERE id=?', (int(order_id),)).fetchone()
    if not o:
        return None
    ref = _ref_from_row(c, o)
    return {
        'clubosOrderId': ref,
        'clubosOrderNumericId': int(order_id),
        'orderFingerprint': _fingerprint(o, items),
        'source': 'clubos',
        'placedAt': o['created_at'],
        'consignee': {'name': o['shipping_consignee'] or '',
                      'phone': o['shipping_phone'] or '',
                      'address': o['shipping_address'] or ''},
        'items': [{'sku': (it['externalInventoryId'] or it['externalSku'] or ''),
                   'externalProductId': it['externalProductId'],
                   'name': it['name'], 'quantity': it['quantity']} for it in items],
    }


def push_orders(records: list[dict], *, origin: str | None = None, timeout: float = 15.0) -> list[dict]:
    """纯 HTTP 阶段：把每条待推记录 POST 给上游 /api/b2b/orders。绝不抛错。"""
    base = (origin or ORIGIN).rstrip('/')
    results = []
    with httpx.Client(timeout=timeout) as client:
        for rec in records:
            payload = dict(rec['_payload'])
            payload['token'] = _token()
            try:
                r = client.post(_upstream_url(base, '/api/b2b/orders'), json=payload)
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
                r = client.post(_upstream_url(base, '/api/b2b/orders/get'),
                                json={'token': _token(),
                                      'clubosOrderId': _ref_for_rec(rec),
                                      'clubosOrderNumericId': int(rec['order_id'])})
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
                                    'externalOrderId': o.get('id'),
                                    'createdAt': str(o.get('createdAt') or o.get('placedAt') or '')})
                else:
                    results.append({'id': rec['id'], 'orderId': rec['order_id'], 'found': False,
                                    'error': f'HTTP {r.status_code}'})
            except Exception as e:
                results.append({'id': rec['id'], 'orderId': rec['order_id'], 'found': False,
                                'error': type(e).__name__ + ' ' + str(e)[:120]})
    return results


def apply_pull_results(c, results: list[dict]) -> dict:
    """轮询结果落库。★ #625：落库前必须验明上游订单确实属于本单，否则拒绝串单。"""
    updated = 0
    rejected = 0
    for res in results:
        c.execute('UPDATE external_fulfillment_orders SET last_pull_at=CURRENT_TIMESTAMP WHERE id=?',
                  (int(res['id']),))
        if not (res.get('found') and res.get('status') in ('shipped', 'completed') and res.get('trackingNo')):
            continue
        rec = c.execute('SELECT * FROM external_fulfillment_orders WHERE id=?', (int(res['id']),)).fetchone()
        if rec is None:
            rejected += 1
            continue
        # 守卫一：上游订单号必须与本地已登记的一致（防止拉到别的订单）
        known = str(rec['external_order_id'] or '').strip()
        got = str(res.get('externalOrderId') or '').strip()
        if known and got and known != got:
            c.execute('UPDATE external_fulfillment_orders SET pull_error=? WHERE id=?',
                      ('上游订单号不匹配，疑似串单，已忽略', int(res['id'])))
            rejected += 1
            continue
        # 守卫二：上游订单创建时间不能早于 ClubOS 订单（历史单状态不得套到新单）
        upstream_at = _parse_ts(res.get('createdAt'))
        local_row = c.execute('SELECT created_at FROM gear_orders WHERE id=?',
                              (int(res['orderId']),)).fetchone()
        local_at = _parse_ts(local_row['created_at'] if local_row else '')
        if upstream_at and local_at and (local_at - upstream_at).total_seconds() > 600:
            c.execute('UPDATE external_fulfillment_orders SET pull_error=? WHERE id=?',
                      ('上游订单早于本单创建时间，疑似历史单，已忽略', int(res['id'])))
            rejected += 1
            continue
        try:
            apply_upstream(c, int(res['orderId']),
                           carrier=res.get('carrier') or PROVIDER,
                           tracking_no=res['trackingNo'],
                           status=('delivered' if res['status'] == 'completed' else 'shipped'),
                           external_order_id=res.get('externalOrderId'))
            c.execute('UPDATE external_fulfillment_orders SET pull_error=NULL WHERE id=?', (int(res['id']),))
            updated += 1
        except (ValueError, LookupError) as e:
            c.execute('UPDATE external_fulfillment_orders SET pull_error=? WHERE id=?',
                      (str(e)[:300], int(res['id'])))
            rejected += 1
    return {'polled': len(results), 'updated': updated, 'rejected': rejected}


# ---------------------------------------------------------------------------
# 状态回写（webhook 与 pull 共用）
# ---------------------------------------------------------------------------
# 佣金冻结不再挂在「上游物流妥投」上（那样会越过 C 端「确认收货」、绕过 10 天自动确认）。
# 现改为仅在订单**被确认收货**时冻结 —— 由宿主 app.py 通过 _complete_receipt 统一收口
# （C 端 confirm-receipt 端点 / 平台 PATCH delivered / 自动确认循环三处复用）。
# on_delivered 钩子保留向后兼容，但当前上游妥投路径不再调用它。
on_delivered = None  # type: ignore[assignment]


def apply_upstream(c, order_id: int, *, carrier: str, tracking_no: str,
                   status: str = 'shipped', events: list[dict] | None = None,
                   external_order_id: str | None = None) -> dict:
    """把上游发货/妥投结果写回本地：运单 + gear_orders + 履约记录。

    ★ 关键语义（2026-11-06 修订）：**物流妥投 ≠ 用户确认收货**。
    * status='shipped'（上游发货）：订单主状态推进到 shipped（待确认收货），
      履约表 fulfill_status=shipped，并写 gear_orders.carrier/tracking_no。
    * status='delivered'（上游物流妥投）：**只**把履约表 fulfill_status 置为 delivered
      （已妥投），订单主状态保持 shipped（待用户确认收货）—— 绝不冻结佣金、绝不写
      delivered_at。佣金与终态在用户「确认收货」(app.confirm_receipt) 或 10 天自动确认
      (app._auto_confirm_loop) 时才落。否则会出现「货刚到、系统就显示已完成订单」的错乱，
      且 C 端「确认收货」按钮会被终态吞掉。

    carrier/tracking_no 写 gear_orders 两列是刻意的：平台列表、C 端 me.js 读的
    就是这两列（logistics_engine 的运单表只是轨迹明细）。
    """
    from importlib import import_module
    logistics_engine = import_module('logistics_engine')
    o = c.execute('SELECT * FROM gear_orders WHERE id=?', (int(order_id),)).fetchone()
    if not o:
        raise LookupError('订单不存在')
    is_delivered = str(status or '').strip().lower() == 'delivered'
    carrier = str(carrier or PROVIDER).strip() or PROVIDER
    tracking_no = str(tracking_no or '').strip()
    if (not is_delivered) and not tracking_no:
        raise ValueError('缺少运单号')
    # 已是终态的幂等保护：用户已确认收货(delivered) / 已退款 不覆盖主状态
    if o['status'] == 'refunded':
        raise ValueError('订单已退款，不再同步供应商物流')
    # —— 上游物流妥投：只推进履约表的「物流妥投」态，不触碰订单主状态 ——
    if is_delivered:
        # ★ #625 串单守卫：妥投必须先发货。主订单仍停在 paid/processing 且无
        # shipped_at，说明这条妥投来自别的订单（典型：上游历史单被幂等复用），
        # 直接拒绝，绝不把旧运单号写进新订单。
        if (o['status'] or '') in ('paid', 'processing') and not (o['shipped_at'] or ''):
            raise ValueError('订单尚未发货，拒绝上游妥投回传（疑似串单）')
        events = events or [{'time': None, 'desc': '物流已送达，待您确认收货'}]
        logistics_engine.upsert_shipment(
            c, order_id=int(order_id), provider=PROVIDER, carrier=carrier, carrier_code='',
            tracking_no=tracking_no or (o['tracking_no'] or ''),
            status='delivered', events=events)
        if tracking_no:
            c.execute('UPDATE gear_orders SET carrier=?,tracking_no=? WHERE id=?',
                      (carrier, tracking_no, int(order_id)))
        # 若用户已先行确认收货或退款，不回退主状态；仍记下供应商物流已妥投。
        c.execute("""UPDATE external_fulfillment_orders SET fulfill_status=?,carrier=?,tracking_no=?,
                       external_order_id=COALESCE(?,external_order_id),fulfilled_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                       WHERE order_id=?""", (FUL_DELIVERED, carrier, tracking_no, external_order_id, int(order_id)))
        return {'ok': True, 'orderId': int(order_id), 'fulfillStatus': FUL_DELIVERED,
                'orderStatus': (o['status'] or 'shipped')}
    # —— 上游发货：推进订单到 shipped + 履约表 shipped ——
    events = events or [{'time': None, 'desc': '供应商已发货，等待物流更新'}]
    logistics_engine.upsert_shipment(
        c, order_id=int(order_id), provider=PROVIDER, carrier=carrier, carrier_code='',
        tracking_no=tracking_no or (o['tracking_no'] or ''),
        status='in_transit', events=events)
    if tracking_no and o['status'] not in ('delivered', 'refunded'):
        c.execute('UPDATE gear_orders SET carrier=?,tracking_no=? WHERE id=?',
                  (carrier, tracking_no, int(order_id)))
    c.execute("""UPDATE gear_orders SET status=CASE WHEN status IN ('paid','processing')
                     THEN 'shipped' ELSE status END,
                     shipped_at=COALESCE(shipped_at,CURRENT_TIMESTAMP) WHERE id=?""", (int(order_id),))
    # 迟到的 shipped 回调不得把已 delivered 的履约记录降级回去
    c.execute("""UPDATE external_fulfillment_orders SET fulfill_status=?,carrier=?,tracking_no=?,
                     external_order_id=COALESCE(?,external_order_id),updated_at=CURRENT_TIMESTAMP
                     WHERE order_id=? AND fulfill_status!='delivered'""",
              (FUL_SHIPPED, carrier, tracking_no, external_order_id, int(order_id)))
    return {'ok': True, 'orderId': int(order_id), 'fulfillStatus': FUL_SHIPPED}


def handle_webhook(c, payload: dict) -> dict:
    """供应商发货回调（token 校验在 app.py 完成）。未知订单 → LookupError。"""
    # #625：上游回调带的是全局唯一渠道单号，优先用数字 id 反查，再按 ref 查；
    # 纯数字 ref 是升级前的老单，仅作兼容兜底。
    rec = None
    numeric = payload.get('clubosOrderNumericId')
    if numeric not in (None, ''):
        try:
            rec = c.execute('SELECT * FROM external_fulfillment_orders WHERE order_id=?',
                            (int(numeric),)).fetchone()
        except (TypeError, ValueError):
            rec = None
    ref = str(payload.get('clubosOrderId') or '').strip()
    if rec is None and ref:
        rec = c.execute('SELECT * FROM external_fulfillment_orders WHERE external_ref=?', (ref,)).fetchone()
    if rec is None and ref.isdigit():
        rec = c.execute('SELECT * FROM external_fulfillment_orders WHERE order_id=?', (int(ref),)).fetchone()
    if not rec:
        raise LookupError('未找到该订单的外部履约记录')
    oid = int(rec['order_id'])
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

# ---------------------------------------------------------------------------
# 退换货 B2B 同步（2026-11-06）
# ---------------------------------------------------------------------------
# 业务闭环：C 端 7 天内申请 → ClubOS 审核同意 / 用户填退货运单 → 推给 PEAKTRACE
# → 顾客把货退回 PEAKTRACE → 上游确认收货并重新发货 → 回调 ClubOS 售后 webhook。
# 与订单推送一样：token 放 JSON body，HTTP 阶段绝不碰 DB、绝不抛错。
AFTER_SALES_PATH = '/api/b2b/returns'


def is_external_order(c, order_id: int) -> bool:
    """该订单是否由 PEAKTRACE 代发（有外部履约记录 = 货在供应商仓）。"""
    try:
        return c.execute('SELECT 1 FROM external_fulfillment_orders WHERE order_id=?',
                         (int(order_id),)).fetchone() is not None
    except Exception:
        return False


def build_return_payload(c, case: dict, *, action: str,
                         return_carrier: str = '', return_tracking_no: str = '') -> dict:
    """把 ClubOS 售后单翻译成上游 B2B 退换货单（sku 口径与订单推送一致）。"""
    order_id = int(case['order_id'])
    rec = c.execute('SELECT * FROM external_fulfillment_orders WHERE order_id=?',
                    (order_id,)).fetchone()
    items = []
    for it in (case.get('items') or []):
        m = None
        if it.get('variant_id'):
            try:
                m = c.execute("""SELECT external_inventory_id,external_sku_id
                                 FROM external_product_mappings
                                 WHERE variant_id=? AND external_sku_id<>'' LIMIT 1""",
                              (it['variant_id'],)).fetchone()
            except Exception:
                m = None
        sku = ((m['external_inventory_id'] if m else None)
               or (m['external_sku_id'] if m else None)
               or it.get('sku') or '')
        items.append({'sku': sku, 'name': it.get('product_name') or '',
                      'quantity': int(it.get('quantity') or 1)})
    return {
        'clubosCaseId': str(case['id']),
        # 退换货渠道单号仅作展示/关联（上游幂等按 caseId），不参与撞号
        'clubosOrderId': ((rec['external_ref'] if rec else None) or ('CB-%d-LEGACY' % order_id)),
        'externalOrderId': (rec['external_order_id'] if rec else None),
        'caseType': case.get('case_type'),
        'reason': case.get('reason') or '',
        'action': action,  # approved | return_shipped
        'items': items,
        'returnCarrier': return_carrier,
        'returnTrackingNo': return_tracking_no,
        'token': _token(),
    }


def push_return(payload: dict, *, origin: str | None = None, timeout: float = 10.0) -> dict:
    """纯 HTTP：把售后单同步给 PEAKTRACE /api/b2b/returns。绝不抛错。"""
    base = (origin or ORIGIN).rstrip('/')
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(_upstream_url(base, AFTER_SALES_PATH), json=payload)
            if r.status_code == 200:
                data = r.json()
                return {'ok': True, 'response': data if isinstance(data, dict) else {}}
            detail = ''
            try:
                detail = str((r.json() or {}).get('error') or '')
            except Exception:
                pass
            return {'ok': False, 'error': f'HTTP {r.status_code} {detail}'.strip()}
    except Exception as e:
        return {'ok': False, 'error': type(e).__name__ + ' ' + str(e)[:120]}


def mark_return_sync(c, case_id: str, result: dict) -> None:
    """把同步结果落到售后单上（平台端可看到「已同步 / 同步失败」并重试）。"""
    try:
        c.execute("""UPDATE after_sales_cases SET external_sync_status=?,external_sync_error=?,
                     updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                  ('synced' if result.get('ok') else 'failed',
                   None if result.get('ok') else str(result.get('error') or '')[:300],
                   str(case_id)))
    except Exception:
        pass
