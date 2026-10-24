"""物流履约适配层（2026-10-24）

为什么要有这一层，而不是直接在订单上写两个字段（carrier / tracking_no）：

1. **对接**：真实承运商（快递100 / 顺丰丰桥 / 京东物流）的接口形态各不相同，
   但平台需要的是同一件事——「建运单」和「查轨迹」。把它们收在一个 Provider
   接口后面，换服务商只改配置，不动订单表和前端。
2. **历史**：订单上的 carrier/tracking_no 只能保存最后一次填写的值。换承运商、
   补录单号、服务商回调都会把之前的信息覆盖掉，售后对账时查不到当时到底发给谁。
   运单单独一张表，一个订单一条，轨迹一条条记录，永远可回溯。
3. **幂等**：服务商回调最常见的脏数据是「同一条轨迹重复推送」。轨迹表用
   (shipment_id, event_time, description) 唯一约束兜底，重复推送不会写出两行。

内置两种 Provider：
· mock      —— 本地演示用，按发货后经过的时间推进轨迹，无需任何密钥；
· kuaidi100 —— 快递100 实时查询 API（需要 key + customer）。

接新服务商：继承 BaseProvider 实现 create_shipment / fetch_events /
parse_webhook，注册进 PROVIDERS 即可，其余代码无需改动。
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

# 承运商字典。code 是内部统一编码（也是 C 端展示的短码），
# express = 快递100 的 com 参数（接真实接口时直接用它，不用前端再映射一次）。
CARRIERS = [
    {'code': 'SF', 'name': '顺丰速运', 'express': 'shunfeng'},
    {'code': 'JD', 'name': '京东物流', 'express': 'jd'},
    {'code': 'ZTO', 'name': '中通快递', 'express': 'zhongtong'},
    {'code': 'YTO', 'name': '圆通速递', 'express': 'yuantong'},
    {'code': 'YUNDA', 'name': '韵达速递', 'express': 'yunda'},
    {'code': 'STO', 'name': '申通快递', 'express': 'shentong'},
    {'code': 'EMS', 'name': 'EMS / 邮政', 'express': 'ems'},
    {'code': 'JTSD', 'name': '极兔速递', 'express': 'jtexpress'},
    {'code': 'DBL', 'name': '德邦快递', 'express': 'debangwuliu'},
    {'code': 'OTHER', 'name': '其他承运商', 'express': ''},
]
CARRIER_BY_CODE = {c['code']: c for c in CARRIERS}

# 统一状态机：服务商返回的文本千奇百怪，落到平台只认这五种。
STATUS_CN = {
    'created': '已下单',
    'in_transit': '运输中',
    'out_for_delivery': '派送中',
    'delivered': '已签收',
    'exception': '异常',
}
_STATUS_ORDER = ['created', 'in_transit', 'out_for_delivery', 'delivered']

# 快递100 文本 → 统一状态。顺序敏感：「签收」要先于「派送」判断，
# 因为「已签收，签收人：本人」这类文案里也可能出现「派送」。
_STATUS_HINTS = [
    ('delivered', ('已签收', '已妥投', '签收人', '代收', '已送达')),
    ('exception', ('退回', '拒收', '异常', '失败', '疑难', '滞留')),
    ('out_for_delivery', ('派送', '派件', '正在投递', '待取件')),
    ('in_transit', ('运输', '发出', '到达', '离开', '转运', '分拣', '揽收', '已收件')),
]


class LogisticsError(Exception):
    """服务商调用失败。上层统一翻译成 502，不把底层栈暴露给前端。"""


def now_utc() -> str:
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')


def status_from_text(text: str, default: str = 'in_transit') -> str:
    t = str(text or '')
    for status, words in _STATUS_HINTS:
        if any(w in t for w in words):
            return status
    return default


def _parse_ts(value) -> datetime | None:
    """服务商给的时间格式五花八门，能解析就解析，解析不了返回 None（不抛）。"""
    s = str(value or '').strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M',
                '%Y/%m/%d %H:%M:%S', '%Y%m%d%H%M%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(s[:len(datetime.now().strftime(fmt))], fmt)
        except ValueError:
            continue
    return None


def _post_form(url: str, data: dict, timeout: float = 10.0) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method='POST',
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', 'replace')
    except Exception as exc:  # 网络/超时/DNS 一律归一，别让 urllib 的异常类型漏到路由层
        raise LogisticsError(f'请求物流服务商失败：{exc}') from exc
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise LogisticsError(f'物流服务商返回非 JSON：{raw[:120]}') from exc


class BaseProvider:
    """Provider 接口。子类至少实现 fetch_events；create_shipment 默认只落首条轨迹。"""

    code = 'base'
    name = '基础'

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    # ---- 建运单 -------------------------------------------------------
    def create_shipment(self, *, order_id: int, carrier: str, carrier_code: str,
                        tracking_no: str) -> dict:
        """返回 {status, events:[{event_time,status,description,location}]}。

        大多数快递公司不提供「下单」API（单号在线下就有了），所以默认实现
        只生成一条「已揽收」轨迹；真正需要电子面单的服务商在这里调它的下单接口。
        """
        return {
            'status': 'in_transit',
            'events': [{
                'event_time': now_utc(),
                'status': 'in_transit',
                'description': f'包裹已由{carrier or "承运商"}揽收',
                'location': '',
            }],
        }

    # ---- 查轨迹 -------------------------------------------------------
    def fetch_events(self, shipment: dict) -> list[dict]:
        return []

    # ---- 回调解析 -----------------------------------------------------
    def parse_webhook(self, payload: dict) -> list[dict]:
        """把服务商推送的报文转成事件列表；无法识别时返回 []。

        这里刻意做成「宽容解析」：快递公司推过来的外层键叫什么的都有
        （events / data / lastResult.data），字段名也有两套（time vs ftime、
        description vs context）。统一在这里认下来，接新承运商时才不用改报文，
        否则每接一家都要在回调入口加分支 —— 那正好是这层适配要消灭的东西。
        """
        if not isinstance(payload, dict):
            return []
        raw = payload.get('events')
        if not isinstance(raw, list):
            raw = payload.get('data')
        if not isinstance(raw, list):
            last = payload.get('lastResult')
            raw = (last or {}).get('data') if isinstance(last, dict) else None
        if not isinstance(raw, list):
            return []
        out = []
        for e in raw:
            if not isinstance(e, dict):
                continue
            desc = str(e.get('description') or e.get('context') or e.get('remark') or '').strip()
            if not desc:
                continue
            out.append({
                'event_time': str(e.get('event_time') or e.get('ftime') or e.get('time') or now_utc()),
                'status': str(e.get('status') or status_from_text(desc)),
                'description': desc,
                'location': str(e.get('location') or e.get('areaName') or ''),
            })
        return out


class MockProvider(BaseProvider):
    """内置模拟：按「发货后经过的时间」推进轨迹，不依赖任何外部服务。

    时间点是相对 shipped_at 的偏移，所以补录一张昨天的运单，同步时能直接
    看到完整链路 —— 演示和联调都靠这个特性，不需要真的等两天。
    """

    code = 'mock'
    name = '模拟物流（内置）'

    def create_shipment(self, *, order_id: int, carrier: str, carrier_code: str,
                        tracking_no: str) -> dict:
        """与 _STAGES 第 0 段完全一致（文案与时间都对齐）→ 随后同步同一条时会被唯一约束去重，
        时间线上不会冒出「包裹已揽收」「包裹已由顺丰揽收」两行几乎一样的记录。"""
        ts = now_utc()
        return {
            'status': 'in_transit',
            'events': [{'event_time': ts, 'status': 'in_transit',
                        'description': self._STAGES[0][2], 'location': ''}],
        }

    _STAGES = [
        (0, 'in_transit', '包裹已揽收'),
        (4, 'in_transit', '到达转运中心'),
        (14, 'in_transit', '离开转运中心，发往目的地'),
        (28, 'out_for_delivery', '到达派送网点，快递员正在派送'),
        (34, 'delivered', '已签收，感谢使用'),
    ]

    def fetch_events(self, shipment: dict) -> list[dict]:
        base = _parse_ts(shipment.get('shipped_at')) or _parse_ts(shipment.get('created_at'))
        if not base:
            return []
        elapsed_hours = (datetime.utcnow() - base).total_seconds() / 3600.0
        city = self.config.get('demoCity') or '目的地'
        out = []
        for hours, status, text in self._STAGES:
            if elapsed_hours + 1e-9 < hours:
                break
            out.append({
                'event_time': (base + timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S'),
                'status': status,
                'description': text,
                'location': city if hours >= 4 else '',
            })
        return out


class Kuaidi100Provider(BaseProvider):
    """快递100 实时查询（https://api.kuaidi100.com）。

    只需要两样东西：key（授权码）与 customer（企业编号）。签名规则：
    sign = MD5(param + key + customer)，全部大写。这是快递100 的公开约定，
    换别的服务商时照抄这个类的形状即可。
    """

    code = 'kuaidi100'
    name = '快递100'
    endpoint = 'https://poll.kuaidi100.com/poll/query.do'

    def _credentials(self):
        key = str(self.config.get('key') or os.getenv('KUAIDI100_KEY', '')).strip()
        customer = str(self.config.get('customer') or os.getenv('KUAIDI100_CUSTOMER', '')).strip()
        if not key or not customer:
            raise LogisticsError('快递100 未配置 key / customer，请先在「物流对接」里填写')
        return key, customer

    def fetch_events(self, shipment: dict) -> list[dict]:
        key, customer = self._credentials()
        carrier = CARRIER_BY_CODE.get(str(shipment.get('carrier_code') or '').upper())
        com = (carrier or {}).get('express') or ''
        if not com:
            raise LogisticsError('该承运商没有对应的快递100编码，无法查询轨迹')
        param = json.dumps({'com': com, 'num': str(shipment.get('tracking_no') or ''),
                            'resultv2': '4', 'phone': ''}, ensure_ascii=False)
        sign = hashlib.md5((param + key + customer).encode('utf-8')).hexdigest().upper()
        data = _post_form(self.endpoint, {'customer': customer, 'sign': sign, 'param': param})
        if str(data.get('status')) not in ('200', '201') and data.get('returnCode') not in ('200', None):
            raise LogisticsError(str(data.get('message') or data.get('returnMsg') or '查询失败'))
        out = []
        for item in data.get('data') or []:
            text = str(item.get('context') or '')
            out.append({
                'event_time': str(item.get('ftime') or item.get('time') or ''),
                'status': status_from_text(text),
                'description': text,
                'location': str(item.get('areaName') or ''),
            })
        return out

    def parse_webhook(self, payload: dict) -> list[dict]:
        # 快递100 订阅推送：{"status":"polling","lastResult":{"data":[{"ftime","context"}...]}}
        result = (payload or {}).get('lastResult') or {}
        out = []
        for item in result.get('data') or []:
            text = str(item.get('context') or '')
            out.append({
                'event_time': str(item.get('ftime') or ''),
                'status': status_from_text(text),
                'description': text,
                'location': str(item.get('areaName') or ''),
            })
        return out


PROVIDERS: dict[str, type[BaseProvider]] = {
    MockProvider.code: MockProvider,
    Kuaidi100Provider.code: Kuaidi100Provider,
}


def provider_list() -> list[dict]:
    return [{'code': code, 'name': cls.name} for code, cls in PROVIDERS.items()]


def get_provider(config: dict | None = None) -> BaseProvider:
    """按配置取 Provider。未知编码回落到 mock，并把原因写进 name 之外的字段，
    避免「填错服务商 → 整条发货链路 500」这种低级故障。"""
    cfg = config or {}
    code = str(cfg.get('provider') or 'mock').strip().lower()
    cls = PROVIDERS.get(code) or MockProvider
    return cls(cfg)


# ==========================================================================
# 持久化（全部接收连接，事务由调用方控制，和其余引擎保持同一风格）
# ==========================================================================

def get_shipment(c, order_id: int) -> dict | None:
    r = c.execute('SELECT * FROM logistics_shipments WHERE order_id=?', (int(order_id),)).fetchone()
    return dict(r) if r else None


def list_events(c, shipment_id: int, limit: int = 200) -> list[dict]:
    # 最新一条在最上面：运营看轨迹只关心「现在到哪了」。
    return [dict(r) for r in c.execute(
        'SELECT * FROM logistics_events WHERE shipment_id=? ORDER BY event_time DESC, id DESC LIMIT ?',
        (int(shipment_id), int(limit))).fetchall()]


def _apply_events(c, shipment_id: int, events: list[dict]) -> int:
    added = 0
    for e in events or []:
        desc = str(e.get('description') or '').strip()
        if not desc:
            continue
        try:
            # rowcount 只在 Cursor 上有意义：早先读的是 Connection.rowcount，
            # 那个属性不存在、一读就抛 AttributeError，被下面的 except 吞掉之后
            # 表现为「明明写进去了却报 added=0」。
            cur = c.execute('INSERT OR IGNORE INTO logistics_events(shipment_id,event_time,status,description,location)'
                            ' VALUES(?,?,?,?,?)',
                            (int(shipment_id), str(e.get('event_time') or now_utc()),
                             str(e.get('status') or ''), desc, str(e.get('location') or '')))
            if cur.rowcount > 0:
                added += 1
        except Exception:
            # 唯一约束冲突就是「已经推过这条」，属于正常情况，不该让整批回滚。
            pass
    return added


def _advance_status(c, shipment_id: int, events: list[dict]) -> None:
    """运单状态只向前走，不倒退。服务商偶尔会补发旧轨迹（比如从别的节点重推），
    按「状态序」比较可以避免已签收的运单被一条「运输中」打回去。"""
    cur = c.execute('SELECT status FROM logistics_shipments WHERE id=?', (int(shipment_id),)).fetchone()
    cur_status = (dict(cur).get('status') if cur else '') or 'created'
    best = cur_status
    for e in events or []:
        st = str(e.get('status') or '')
        if st in _STATUS_ORDER and (cur_status not in _STATUS_ORDER
                                    or _STATUS_ORDER.index(st) > _STATUS_ORDER.index(best)):
            best = st
    row = c.execute('SELECT MIN(event_time) first_at, MAX(event_time) last_at FROM logistics_events WHERE shipment_id=?',
                    (int(shipment_id),)).fetchone()
    first_at = (dict(row).get('first_at') if row else None) or None
    last_at = (dict(row).get('last_at') if row else None) or None
    delivered_at = None
    if best == 'delivered':
        d = c.execute("SELECT MAX(event_time) t FROM logistics_events WHERE shipment_id=? AND status='delivered'",
                      (int(shipment_id),)).fetchone()
        delivered_at = (dict(d).get('t') if d else None) or last_at
    c.execute('UPDATE logistics_shipments SET status=?, shipped_at=COALESCE(shipped_at,?),'
              ' delivered_at=COALESCE(?, delivered_at), last_sync_at=? , updated_at=CURRENT_TIMESTAMP'
              ' WHERE id=?',
              (best, first_at, delivered_at, now_utc(), int(shipment_id)))


def upsert_shipment(c, *, order_id: int, provider: str, carrier: str, carrier_code: str,
                    tracking_no: str, status: str = 'in_transit',
                    events: list[dict] | None = None, raw: dict | None = None) -> int:
    """建/换运单：一个订单只有一条运单（改单号就是换运单，轨迹清空重来）。

    清空轨迹是有意的 —— 换了承运商还把旧轨迹挂着，会拼出一条根本不存在的时间线。
    """
    existing = c.execute('SELECT id FROM logistics_shipments WHERE order_id=?', (int(order_id),)).fetchone()
    payload = json.dumps(raw, ensure_ascii=False) if raw is not None else None
    if existing:
        sid = int(dict(existing)['id'])
        c.execute('UPDATE logistics_shipments SET provider=?,carrier=?,carrier_code=?,tracking_no=?,'
                  'status=?,shipped_at=NULL,delivered_at=NULL,last_sync_at=?,raw_json=?,updated_at=CURRENT_TIMESTAMP'
                  ' WHERE id=?',
                  (provider, carrier, carrier_code, tracking_no, status or 'in_transit',
                   now_utc(), payload, sid))
        c.execute('DELETE FROM logistics_events WHERE shipment_id=?', (sid,))
    else:
        cur = c.execute('INSERT INTO logistics_shipments(order_id,provider,carrier,carrier_code,tracking_no,status,last_sync_at,raw_json)'
                        ' VALUES(?,?,?,?,?,?,?,?)',
                        (int(order_id), provider, carrier, carrier_code, tracking_no,
                         status or 'in_transit', now_utc(), payload))
        sid = int(cur.lastrowid)
    _apply_events(c, sid, events or [])
    _advance_status(c, sid, events or [])
    return sid


def append_events(c, shipment_id: int, events: list[dict]) -> dict:
    """追加一批轨迹（手动补录 / 回调 / 同步共用），返回新增条数与最新状态。"""
    added = _apply_events(c, shipment_id, events)
    _advance_status(c, shipment_id, events)
    cur = c.execute('SELECT status FROM logistics_shipments WHERE id=?', (int(shipment_id),)).fetchone()
    return {'added': added, 'status': (dict(cur).get('status') if cur else '') or ''}
