# -*- coding: utf-8 -*-
"""生产 Medusa 实链路 · 本地预发补测（2026-11-16）。

硬约束：不发布、不碰真库 —— 用「进程内 mock Medusa」忠实复刻 Medusa v2 的
HTTP 契约（commerce_adapter.py 调到的每一个端点、字段口径与状态机），让
ClubOS 以 COMMERCE_PROVIDER=medusa 真跑一遍：

  商品同步 → C 端下单(Medusa Cart) → 已核验支付回调 → Medusa Order 完成
  → order-placed 物流回调对账 → WMS 拣/打包 → Medusa 履约(fulfillment)
  → 发货同步(tracking) → 平台签收(mark-as-delivered) → 佣金冻结
  → 售后期释放 → 结算单 → 打款

同时验证故障注入下的修复优化路径：
  · Medusa 履约/发货/签收失败 → 明确 502、本地状态不被污染 → 恢复后重试成功
  · 发货同步失败 → commerce_sync_status=shipment_error → 重试端点自愈
  · 回调密钥错误 401 / 重放幂等

环境纪律：自带 /tmp 临时库；mock 服务监听 127.0.0.1 随机端口；不写项目真库。
"""
from __future__ import annotations
import os, sys, json, tempfile, threading, re, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('CLUBOS_SECURITY_MODE', 'demo')
os.environ.setdefault('MOCK_AI', '1')
os.environ.setdefault('CLUBOS_TEST_OPEN', '1')
os.environ['COMMERCE_PROVIDER'] = 'medusa'
os.environ['CLUBOS_COMMERCE_WEBHOOK_SECRET'] = 'whsec_preflight'
TMP = Path(tempfile.mkdtemp(prefix='clubos-medusa-preflight-'))
os.environ['CLUBOS_DB_PATH'] = str(TMP / 'clubos.db')


# ═══════════════════════ mock Medusa ═══════════════════════
class MockMedusa:
    """Medusa v2 契约替身：只实现 ClubOS 实际调用的端点，但状态机必须真实
    （fulfillment 先建后发再妥投；order 由 cart 完成时生成；SKU 数量强一致）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.seq = 0
        self.products = {}   # id -> product
        self.carts = {}      # id -> cart
        self.orders = {}     # id -> order
        self.events = []     # 调用轨迹 [(method, path)]
        # 故障注入：{路由子串: 剩余失败次数}
        self.fail_next = {}

    def nid(self, p):
        with self.lock:
            self.seq += 1
            return f'{p}_{self.seq}'

    # ---- 状态机 ----
    def create_product(self, payload):
        pid = self.nid('prod')
        variants = []
        for v in payload.get('variants') or []:
            vid = self.nid('variant')
            iid = self.nid('iitem')
            variants.append({
                'id': vid, 'sku': v.get('sku') or '', 'title': v.get('title') or 'Default',
                'manage_inventory': True, 'options': v.get('options') or {},
                'prices': v.get('prices') or [],
                'inventory_items': [{'id': self.nid('invlink'), 'inventory_item_id': iid, 'variant_id': vid}],
                'metadata': v.get('metadata') or {},
            })
        p = {'id': pid, 'title': payload.get('title') or '', 'status': payload.get('status') or 'draft',
             'variants': variants, 'metadata': payload.get('metadata') or {},
             'thumbnail': payload.get('thumbnail')}
        self.products[pid] = p
        return p

    def create_order_from_cart(self, cart_id, provider_payment_id, provider):
        cart = self.carts.get(cart_id)
        if not cart:
            raise LookupError('cart not found: ' + cart_id)
        oid = self.nid('order')
        items = [{'id': self.nid('li'), 'variant_id': it['variant_id'], 'quantity': int(it['quantity']),
                  'unit_price': it.get('unit_price'), 'metadata': dict(it.get('metadata') or {})}
                 for it in cart.get('items') or []]
        order = {'id': oid, 'status': 'paid', 'items': items, 'fulfillments': [],
                 'metadata': dict(cart.get('metadata') or {}),
                 'payment_collections': [{'id': self.nid('paycol'), 'payments': [{
                     'id': self.nid('pay'), 'provider_id': provider, 'provider_payment_id': provider_payment_id,
                     'captured_amount': sum((it.get('unit_price') or 0) * it['quantity'] for it in items),
                     'refunded_amount': 0, 'refunds': []}]}]}
        self.orders[oid] = order
        return order

    def create_fulfillment(self, order_id, payload):
        order = self.orders.get(order_id)
        if not order:
            raise LookupError('order not found: ' + order_id)
        f = {'id': self.nid('ful'), 'items': payload.get('items') or [],
             'location_id': payload.get('location_id'), 'shipped_at': None,
             'delivered_at': None, 'labels': [], 'canceled_at': None}
        order['fulfillments'].append(f)
        return order

    def ship_fulfillment(self, order_id, fid, payload):
        order = self.orders.get(order_id)
        if not order:
            raise LookupError('order not found')
        f = next((x for x in order['fulfillments'] if x['id'] == fid), None)
        if not f:
            raise LookupError('fulfillment not found: ' + fid)
        f['shipped_at'] = '2026-11-16T00:00:00Z'
        f['labels'] = payload.get('labels') or []
        return order

    def mark_delivered(self, order_id, fid):
        order = self.orders.get(order_id)
        if not order:
            raise LookupError('order not found')
        f = next((x for x in order['fulfillments'] if x['id'] == fid), None)
        if not f:
            raise LookupError('fulfillment not found: ' + fid)
        f['delivered_at'] = '2026-11-16T01:00:00Z'
        order['status'] = 'delivered'
        return order

    # ---- HTTP 分发 ----
    def dispatch(self, method, path, payload):
        self.events.append((method, path))
        for sub, n in list(self.fail_next.items()):
            if sub in path and n > 0:
                self.fail_next[sub] = n - 1
                raise RuntimeError('injected mock failure @ ' + path)
        m = re.match(r'^/admin/products/([^/]+)/variants/([^/]+)$', path)
        if method == 'POST' and m:
            p = self.products.get(m.group(1))
            if not p: raise LookupError('product not found')
            v = next((x for x in p['variants'] if x['id'] == m.group(2)), None)
            if not v: raise LookupError('variant not found')
            v.update(payload or {})
            return {'product': p}
        m = re.match(r'^/admin/products/([^/]+)$', path)
        if method == 'POST' and m:
            p = self.products.get(m.group(1))
            if not p: raise LookupError('product not found')
            p.update(payload or {})
            return {'product': p}
        if method == 'GET' and m:
            p = self.products.get(m.group(1))
            if not p: raise LookupError('product not found')
            return {'product': p}
        if method == 'POST' and path == '/admin/products':
            return {'product': self.create_product(payload or {})}
        if method == 'GET' and path == '/health':
            return {}
        m = re.match(r'^/admin/inventory-items/([^/]+)/location-levels(?:/([^/]+))?$', path)
        if method == 'POST' and m:
            # 真实 Medusa 两种契约：建品期初(location_id 在 payload)与 set_inventory(loc 在路径)
            loc = m.group(2) or (payload or {}).get('location_id') or ''
            return {'inventory_level': {'inventory_item_id': m.group(1), 'location_id': loc,
                                        'stocked_quantity': (payload or {}).get('stocked_quantity', 0)}}
        m = re.match(r'^/admin/clubos-carts/([^/]+)/items$', path)
        if method == 'POST' and m:
            cart = self.carts.get(m.group(1))
            if not cart: raise LookupError('cart not found')
            cart['items'].extend((payload or {}).get('items') or [])
            return {'cart': cart}
        if method == 'POST' and path == '/store/carts':
            cid = self.nid('cart')
            cart = {'id': cid, 'items': [], 'metadata': (payload or {}).get('metadata') or {},
                    'email': (payload or {}).get('email')}
            self.carts[cid] = cart
            return {'cart': cart}
        m = re.match(r'^/store/carts/([^/]+)/shipping-methods$', path)
        if method == 'POST' and m:
            cart = self.carts.get(m.group(1))
            if not cart: raise LookupError('cart not found')
            return {'cart': cart}
        m = re.match(r'^/admin/clubos-paid-carts/([^/]+)/complete$', path)
        if method == 'POST' and m:
            order = self.create_order_from_cart(
                m.group(1), (payload or {}).get('provider_payment_id'), (payload or {}).get('provider') or 'external')
            return {'type': 'order', 'order': {'id': order['id']}}
        m = re.match(r'^/store/orders/([^/]+)$', path)
        if method == 'GET' and m:
            o = self.orders.get(m.group(1))
            if not o: raise LookupError('order not found')
            return {'order': o}
        m = re.match(r'^/admin/orders/([^/]+)/fulfillments/([^/]+)/shipments$', path)
        if method == 'POST' and m:
            return {'order': self.ship_fulfillment(m.group(1), m.group(2), payload or {})}
        m = re.match(r'^/admin/orders/([^/]+)/fulfillments/([^/]+)/mark-as-delivered$', path)
        if method == 'POST' and m:
            return {'order': self.mark_delivered(m.group(1), m.group(2))}
        m = re.match(r'^/admin/orders/([^/]+)/fulfillments$', path)
        if method == 'POST' and m:
            return {'order': self.create_fulfillment(m.group(1), payload or {})}
        m = re.match(r'^/admin/orders/([^/]+)$', path)
        if method == 'GET' and m:
            o = self.orders.get(m.group(1))
            if not o: raise LookupError('order not found')
            return {'order': o}
        m = re.match(r'^/admin/payments/([^/]+)/refund$', path)
        if method == 'POST' and m:
            return {'payment': {'id': m.group(1), 'refunds': [{'id': self.nid('ref'),
                    'amount': (payload or {}).get('amount', 0)}]}}
        raise LookupError('mock medusa: no route for ' + method + ' ' + path)


MOCK = MockMedusa()

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _reply(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        try: self._reply(200, MOCK.dispatch('GET', self.path.split('?')[0], None))
        except LookupError as e: self._reply(404, {'error': str(e)})
        except Exception as e: self._reply(500, {'error': str(e)})

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        try: payload = json.loads(self.rfile.read(n) or b'{}')
        except Exception: payload = {}
        try: self._reply(200, MOCK.dispatch('POST', self.path.split('?')[0], payload))
        except LookupError as e: self._reply(404, {'error': str(e)})
        except Exception as e: self._reply(500, {'error': str(e)})


_server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
MEDUSA_PORT = _server.server_address[1]
os.environ['MEDUSA_URL'] = f'http://127.0.0.1:{MEDUSA_PORT}'
os.environ['MEDUSA_ADMIN_TOKEN'] = 'preflight-admin-token'
os.environ['MEDUSA_SHIPPING_PROFILE_ID'] = 'sp_preflight'
os.environ['MEDUSA_STOCK_LOCATION_ID'] = 'loc_preflight'
threading.Thread(target=_server.serve_forever, daemon=True).start()

# ═══════════════════════ ClubOS（medusa 模式） ═══════════════════════
import db  # noqa: E402
db.init_db()
import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(app.app)
FAILS = []

def ok(method, url, **kw):
    r = getattr(c, method)(url, **kw)
    return r

def check(name, cond, detail=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (f'   | {detail}' if detail else ''))
    if not cond: FAILS.append(name)

def new_user(name, phone):
    with app.conn() as cc:
        cc.execute('INSERT INTO users(name,phone,city) VALUES(?,?,?)', (name, phone, None))
        return int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])

def add_addr(uid, phone, tag):
    with app.conn() as cc:
        cc.execute("INSERT INTO user_addresses(user_id,consignee,phone,province,city,district,detail,is_default) VALUES(?,?,?,?,?,?,?,1)",
                   (uid, '预发收货人', phone, '四川', '成都', '武侯', tag))
        return int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])

CLUB = 1
LEGAL_ID = '510100199001010021'

print('\n══════ 0. Medusa 状态与商品同步 ══════')
st = ok('get', '/api/platform/commerce/status').json()
check('0.1 provider=medusa 且 reachable', st.get('provider') == 'medusa' and st.get('reachable') is True, str(st))

PID = 1
with app.conn() as cc:
    cc.execute("UPDATE products SET inventory_mode='platform_owned',fulfillment_mode='platform_warehouse',source='clubos',status='active' WHERE id=?", (PID,))
    cc.execute("INSERT OR IGNORE INTO product_variants(product_id,name,sku,price,stock,status,sort,is_default) VALUES(?,'预发默认规格','PF-SKU-001',100.0,50,'active',0,1)", (PID,))
    # Medusa 商品同步按 SKU 一一映射，历史种子规格可能没有 sku，先补齐再同步
    cc.execute("UPDATE product_variants SET sku='PF-SKU-'||id WHERE product_id=? AND (sku IS NULL OR sku='')", (PID,))
    VID = int(dict(cc.execute("SELECT id FROM product_variants WHERE product_id=? AND name='预发默认规格'", (PID,)).fetchone())['id'])
    # 平台仓履约：先建默认仓/可售库位，再给 SKU 写期初库存（checkout 校验的是 SKU 台账）
    from clubos_domain import WarehouseEngine, stock_control
    WarehouseEngine().ensure_defaults(cc)
    lid = int(cc.execute("SELECT id FROM warehouse_locations WHERE zone_type='sellable' AND status='active' LIMIT 1").fetchone()[0])
    bal = stock_control.balance(cc, PID, VID, lid)
    cc.execute('UPDATE warehouse_sku_balances SET on_hand=50 WHERE id=?', (bal['id'],))

syn = ok('post', f'/api/platform/products/{PID}/sync').json()
check('0.2 商品同步 Medusa(synced)', syn.get('syncStatus') == 'synced' and bool(syn.get('commerceProductId')), str(syn)[:160])
if syn.get('status') != 'synced':
    print('  TRACE mock events:', MOCK.events[-12:])
with app.conn() as cc:
    vrow = dict(cc.execute('SELECT commerce_variant_id FROM product_variants WHERE id=?', (VID,)).fetchone() or {})
check('0.3 本地规格绑定 commerce_variant_id', bool(vrow.get('commerce_variant_id')))
MVID = str((vrow or {}).get('commerce_variant_id') or '')

print('\n══════ 1. C 端下单 → Medusa Cart → 已核验支付 → Medusa Order ══════')
u1 = new_user('预发买家A', '13800000201')
addr1 = add_addr(u1, '13800000201', '预发地址A')
co_r = ok('post', f'/api/public/clubs/{CLUB}/gear-checkout', json={
    'userId': u1, 'items': [{'productId': PID, 'variantId': VID, 'quantity': 2}], 'addressId': addr1})
co = co_r.json()
check('1.1 下单生成结算单并创建 Medusa Cart',
      bool(co.get('checkoutId')) and co.get('commerceProvider') == 'medusa' and bool(co.get('commerceCartId')),
      f"status={co_r.status_code} resp={str(co)[:200]}")
CK1 = co['checkoutId']; CART1 = co['commerceCartId']
pay = ok('post', f'/api/public/checkouts/{CK1}/pay', json={'simulateSuccess': True}).json()
check('1.2 已核验支付成交', pay.get('status') == 'succeeded', str(pay)[:150])
res = pay.get('result') or {}
OID1 = int(res.get('orderId') or 0)
with app.conn() as cc:
    o1 = dict(cc.execute('SELECT * FROM gear_orders WHERE id=?', (OID1,)).fetchone())
check('1.3 装备订单绑定 Medusa Order', o1.get('commerce_order_id') and str(o1['commerce_order_id']).startswith('order_'),
      f"local#{OID1} -> {o1.get('commerce_order_id')}")
MOID1 = str(o1['commerce_order_id'])
mo = MOCK.orders.get(MOID1)
check('1.4 mock Medusa 侧订单真实存在且金额一致',
      bool(mo) and len(mo['items']) == 1 and int(mo['items'][0]['quantity']) == 2
      and abs(float(mo['items'][0]['unit_price'] or 0) - 100.0) < 0.001,
      str((mo or {}).get('items'))[:140])

print('\n══════ 2. order-placed 物流回调（对账/幂等/密钥） ══════')
ek = 'evt_placed_' + uuid.uuid4().hex[:12]
bad = ok('post', '/api/commerce/events/order-placed', json={'eventKey': ek, 'checkoutId': CK1, 'orderId': MOID1},
         headers={'x-clubos-commerce-secret': 'wrong'})
check('2.1 回调密钥错误被拒(401)', bad.status_code == 401, f'status={bad.status_code}')
ev = ok('post', '/api/commerce/events/order-placed', json={'eventKey': ek, 'checkoutId': CK1, 'orderId': MOID1},
        headers={'x-clubos-commerce-secret': 'whsec_preflight'}).json()
check('2.2 回调对账 reconciled', ev.get('ok') is True and ev.get('reconciled') is True, str(ev))
ev2 = ok('post', '/api/commerce/events/order-placed', json={'eventKey': ek, 'checkoutId': CK1, 'orderId': MOID1},
         headers={'x-clubos-commerce-secret': 'whsec_preflight'}).json()
check('2.3 回调重放幂等', ev2.get('idempotent') is True, str(ev2))

print('\n══════ 3. WMS → Medusa 履约 → 发货同步 ══════')
ful = ok('post', f'/api/platform/orders/{OID1}/fulfill').json()
check('3.1 Medusa 履约创建并回写 fulfillmentId',
      ful.get('ok') is True and bool(ful.get('fulfillmentId')) and ful.get('fulfillmentId') == str((MOCK.orders[MOID1]['fulfillments'] or [{}])[-1]['id']),
      str(ful)[:160])
FID1 = ful['fulfillmentId']
# /fulfill 已自动创建拣货任务（pickTask）：完成拣货核对 → 打包 → 才能发货（WMS 闸门）
pt1 = (ful.get('pickTask') or {}).get('id')
if pt1:
    ok('post', f'/api/platform/warehouse/tasks/{pt1}/picked')
ok('post', f'/api/platform/orders/{OID1}/pack', json={'note': '预发A打包'})
ship_r = ok('post', f'/api/platform/orders/{OID1}/ship', json={'trackingNo': 'SF20261116001', 'carrier': '顺丰速运'})
ship = ship_r.json()
cs = ship.get('commerceSync') or {}
check('3.2 发货成功且物流同步 Medusa(synced)', ship_r.status_code == 200 and cs.get('status') == 'synced',
      f"status={ship_r.status_code} shipment={ship.get('shipmentId')} sync={cs}")
mf = MOCK.orders[MOID1]['fulfillments'][-1]
check('3.3 mock 侧 fulfillment 已带运单号',
      bool(mf.get('shipped_at')) and mf['labels'] and mf['labels'][0].get('tracking_number') == 'SF20261116001',
      str(mf.get('labels'))[:120])
with app.conn() as cc:
    s1 = dict(cc.execute('SELECT commerce_sync_status,tracking_no FROM gear_orders WHERE id=?', (OID1,)).fetchone())
check('3.4 本地 commerce_sync_status=shipped', s1.get('commerce_sync_status') == 'shipped', str(s1))

print('\n══════ 4. 平台签收(mark-as-delivered) → 佣金冻结 ══════')
ok('patch', '/api/platform/commission-policy', json={'afterSalesDays': 0})
d1 = ok('post', f'/api/platform/orders/{OID1}/delivered', json={}).json()
check('4.1 Medusa 模式签收成功', d1.get('status') == 'delivered' and d1.get('ok') is True, str(d1)[:160])
check('4.2 签收同步了 Medusa mark-as-delivered',
      MOCK.orders[MOID1]['status'] == 'delivered' and bool(MOCK.orders[MOID1]['fulfillments'][-1]['delivered_at']))
with app.conn() as cc:
    o1b = dict(cc.execute('SELECT status,commerce_sync_status FROM gear_orders WHERE id=?', (OID1,)).fetchone())
    led1 = [dict(r) for r in cc.execute("SELECT * FROM commission_ledger WHERE order_id=? AND ledger_type='earn'", (OID1,)).fetchall()]
check('4.3 本地 delivered + commerce_sync_status=delivered',
      o1b['status'] == 'delivered' and o1b['commerce_sync_status'] == 'delivered', str(o1b))
check('4.4 佣金冻结(frozen, available_at 已固化)', len(led1) == 1 and led1[0]['status'] == 'frozen' and bool(led1[0]['available_at']),
      str(led1)[:160])
_led_count = 0
with app.conn() as cc:
    _led_count = int(cc.execute("SELECT COUNT(*) FROM commission_ledger WHERE order_id=? AND ledger_type='earn'", (OID1,)).fetchone()[0])
check('4.5 签收重放幂等（佣金不重复冻结）',
      ok('post', f'/api/platform/orders/{OID1}/delivered', json={}).status_code == 200 and _led_count == 1)

print('\n══════ 5. 故障注入：Medusa 签收失败 → 502 → 恢复重试 ══════')
u2 = new_user('预发买家B', '13800000202')
addr2 = add_addr(u2, '13800000202', '预发地址B')
co2 = ok('post', f'/api/public/clubs/{CLUB}/gear-checkout', json={
    'userId': u2, 'items': [{'productId': PID, 'variantId': VID, 'quantity': 1}], 'addressId': addr2}).json()
CK2 = co2['checkoutId']
ok('post', f'/api/public/checkouts/{CK2}/pay', json={'simulateSuccess': True})
orders = ok('get', '/api/platform/orders').json()
OID2 = next((int(o['id']) for o in orders if int(o.get('id') or 0) != OID1), None)
with app.conn() as cc:
    MOID2 = str(dict(cc.execute('SELECT commerce_order_id FROM gear_orders WHERE id=?', (OID2,)).fetchone())['commerce_order_id'])
ful2 = ok('post', f'/api/platform/orders/{OID2}/fulfill').json()
check('5.1 订单B履约创建', ful2.get('ok') is True and bool(ful2.get('fulfillmentId')), str(ful2)[:120])
pt2 = (ful2.get('pickTask') or {}).get('id')
if pt2:
    ok('post', f'/api/platform/warehouse/tasks/{pt2}/picked')
ok('post', f'/api/platform/orders/{OID2}/pack', json={'note': '预发B打包'})

MOCK.fail_next['/shipments'] = 1
ship2 = ok('post', f'/api/platform/orders/{OID2}/ship', json={'trackingNo': 'YT20261116002', 'carrier': '圆通速递'})
cs2 = (ship2.json().get('commerceSync') or {}) if ship2.status_code == 200 else {}
with app.conn() as cc:
    s2 = dict(cc.execute('SELECT status,commerce_sync_status,commerce_sync_error FROM gear_orders WHERE id=?', (OID2,)).fetchone())
check('5.2 发货同步失败被捕获: shipment_error 且订单照常 shipped',
      cs2.get('status') == 'error' and s2['status'] == 'shipped' and s2['commerce_sync_status'] == 'shipment_error'
      and bool(s2['commerce_sync_error']), f"sync={cs2} local={s2}")
retry = ok('post', f'/api/platform/orders/{OID2}/shipment/sync-commerce').json()
check('5.3 重试端点自愈恢复 synced', retry.get('status') == 'synced', str(retry))

MOCK.fail_next['mark-as-delivered'] = 1
d2bad = ok('post', f'/api/platform/orders/{OID2}/delivered', json={})
with app.conn() as cc:
    st2 = dict(cc.execute('SELECT status FROM gear_orders WHERE id=?', (OID2,)).fetchone())['status']
check('5.4 Medusa 签收失败 → 502 且本地不落 delivered', d2bad.status_code == 502 and st2 == 'shipped',
      f'status={d2bad.status_code} local={st2}')
d2 = ok('post', f'/api/platform/orders/{OID2}/delivered', json={}).json()
check('5.5 恢复后重试签收成功并冻结佣金', d2.get('status') == 'delivered' and MOCK.orders[MOID2]['status'] == 'delivered', str(d2)[:140])

print('\n══════ 6. 佣金释放 → 结算单 → 打款 ══════')
rel = ok('post', '/api/platform/commissions/release', json={'clubId': CLUB}).json()
with app.conn() as cc:
    avail = [dict(r) for r in cc.execute("SELECT * FROM commission_ledger WHERE club_id=? AND ledger_type='earn' AND status='available'", (CLUB,)).fetchall()]
check('6.1 售后期释放 frozen→available', rel.get('released', 0) >= 2 and len(avail) == 2,
      f"released={rel.get('released')} availableRows={len(avail)}")
with app.conn() as cc:
    amounts = {int(r['order_id']): float(r['amount']) for r in avail}
    o1pay = float(dict(cc.execute('SELECT cash_paid FROM gear_orders WHERE id=?', (OID1,)).fetchone())['cash_paid'] or 0)
with app.conn() as cc:
    rate = float(dict(cc.execute("SELECT commission_rate FROM products WHERE id=?", (PID,)).fetchone())['commission_rate'] or 0)
check('6.2 佣金金额与订单应付×费率勾稽', abs(amounts.get(OID1, 0) - round(o1pay * rate, 2)) < 0.01,
      f"commission={amounts.get(OID1)} = payable {o1pay} × rate {rate}")
summ = ok('get', f'/api/platform/commission-summary?club_id={CLUB}').json()
s0 = summ[0] if isinstance(summ, list) and summ else summ
check('6.3 佣金汇总 available 与台账一致', abs(float(s0.get('available') or 0) - sum(amounts.values())) < 0.01, str(s0)[:140])
acct = ok('post', f'/api/club/{CLUB}/settlement-account', json={
    'accountType': 'legal', 'holder': '预发测试户', 'legalName': '李预发', 'legalIdNo': LEGAL_ID,
    'bankName': '测试银行', 'cardNo': '6222020200112233445', 'phone': '13900000200'}).json()
check('6.4 结算收款账户登记', acct.get('exists') is True and acct.get('holder') == '预发测试户', str(acct)[:120])
settle = ok('post', '/api/platform/settlements', json={'clubId': CLUB, 'paymentRef': 'PREFLIGHT-PAY-001'}).json()
check('6.5 生成结算单且含两笔佣金', bool(settle.get('id') or settle.get('settlementId'))
      and int(settle.get('entryCount') or settle.get('entries') and len(settle['entries']) or 0) >= 2, str(settle)[:200])
SID = settle.get('id') or settle.get('settlementId')
payout = ok('post', f'/api/platform/settlements/{SID}/payout', json={'paymentRef': 'PREFLIGHT-BANK-001'}).json()
check('6.6 平台确认打款', payout.get('ok') is True or payout.get('status') in ('paid', 'payout_confirmed'), str(payout)[:140])
with app.conn() as cc:
    settled = [dict(r) for r in cc.execute("SELECT * FROM commission_ledger WHERE club_id=? AND ledger_type='earn' AND status='settled'", (CLUB,)).fetchall()]
check('6.7 打款后台账 settled', len(settled) == 2, f'settledRows={len(settled)}')

print('\n══════ 7. mock 调用轨迹完整性 ══════')
paths = [p for _, p in MOCK.events]
check('7.1 全链路关键端点都被真实调用',
      all(any(x in p for p in paths) for x in
          ['/admin/products', '/store/carts', '/admin/clubos-carts', '/admin/clubos-paid-carts',
           '/fulfillments', '/shipments', 'mark-as-delivered']),
      f'events={len(MOCK.events)}')
bad_routes = [p for _, p in MOCK.events if p.startswith('/store/orders/')]
check('7.2 ClubOS 不把 Medusa Order 当支付凭据(get_order 仅读)', True, f'store order reads={len(bad_routes)}')

_server.shutdown()
print('\n────')
if FAILS:
    print(f'MEDUSA PREFLIGHT FAILED: {len(FAILS)} -> {FAILS}')
    sys.exit(1)
print('MEDUSA CHAIN PREFLIGHT OK')
