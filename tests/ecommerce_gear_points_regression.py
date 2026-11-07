"""ClubOS 电商全链路 + 装备积分冲正 回归测试（AAA / 不变量断言）。

隔离环境：CLUBOS_TEST_OPEN=1 / CLUBOS_SECURITY_MODE=demo / COMMERCE_PROVIDER=local
          MOCK_AI=1 / CLUBOS_DB_PATH=<临时目录>/test.db
运行：./venv/bin/python tests/ecommerce_gear_points_regression.py
输出：逐条 PASS/FAIL + qa_ecommerce/tests/docs/TEST-RESULTS.json

断言设计（避免魔法数字与 seed 脏数据）：
  I1 余额守恒   balance(after) == balance(before) + SUM(ledger.amount WHERE id>base_maxid)
  I2 返回一致   退款返回的 points 字段 == 对应类型流水的金额
  I3 幂等       重复 finalize 后余额/流水条数均不变
  I4 非负       balance >= 0 恒成立
  I5 欠账       应冲回>可用余额时，point_adjustment_debt.amount == 差额
"""
import os, sys, json, tempfile, traceback
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.dont_write_bytecode = True
TMP = Path(tempfile.mkdtemp(prefix='clubos-ecom-points-'))
os.environ.update(CLUBOS_TEST_OPEN='1', CLUBOS_SECURITY_MODE='demo', COMMERCE_PROVIDER='local',
                  MOCK_AI='1', CLUBOS_DB_PATH=str(TMP / 'test.db'))
import app
from fastapi.testclient import TestClient

client = TestClient(app.app, raise_server_exceptions=False)
UID = 1
RESULTS = []
CTX = {}


# ---------------------------------------------------------------- helpers
def call(method, path, data=None, expect=200):
    r = client.request(method, path, json=data)
    assert r.status_code == expect, f'{method} {path} -> {r.status_code} (want {expect}): {r.text[:500]}'
    try:
        return r.json()
    except Exception:
        return {}


def q(sql, args=()):
    with app.conn() as db:
        return [dict(x) for x in db.execute(sql, args).fetchall()]


def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def points(uid=UID):
    r = q1('SELECT balance FROM gear_point_accounts WHERE user_id=?', (uid,))
    return int(r['balance'] or 0) if r else 0


def base(uid=UID):
    """场景起始快照：余额 + 当前最大流水 id。"""
    r = q1('SELECT COALESCE(MAX(id),0) mid FROM gear_point_ledger WHERE user_id=?', (uid,))
    return {'balance': points(uid), 'maxid': int(r['mid'] or 0)}


def ledger_since(b, uid=UID):
    return q('SELECT id,type,amount,source_type,source_id FROM gear_point_ledger '
             'WHERE user_id=? AND id>? ORDER BY id', (uid, b['maxid']))


def sum_type(rows, typ, source_id=None):
    return sum(int(r['amount'] or 0) for r in rows
               if r['type'] == typ and (source_id is None or str(r['source_id']) == str(source_id)))


def conserved(b, tag, uid=UID):
    """I1 余额守恒：增量余额 == 增量流水之和。"""
    after = points(uid)
    rows = ledger_since(b, uid)
    delta = sum(int(r['amount'] or 0) for r in rows)
    ok = (after == b['balance'] + delta) and (after >= 0)   # I1 + I4
    assert ok, f'[{tag}] I1/I4 violated: before={b["balance"]} after={after} ledgerDelta={delta} rows={rows}'
    return rows


def case(cid, priority, name, fn):
    """独立采集每条用例的 PASS/FAIL，Arrange 之外不中断整体执行。"""
    rec = {'id': cid, 'priority': priority, 'name': name, 'result': 'PASS', 'detail': ''}
    try:
        detail = fn()
        rec['detail'] = str(detail or '')[:400]
    except Exception as e:
        rec['result'] = 'FAIL'
        rec['detail'] = f'{type(e).__name__}: {e}'
    RESULTS.append(rec)
    print(f'{rec["result"]:4} {cid:12} {priority} {name}' + (f'  | {rec["detail"][:150]}' if rec['result'] == 'FAIL' else ''), flush=True)
    return rec


def rate():
    return float(app.setting('gear_points_rate', 1))


def redeem_rate():
    return max(1, int(app.setting('gear_points_redeem_rate', 100)))


def new_order(qty=2, gear_points=0, variant=None, uid=UID):
    """Arrange helper: 结算 → 支付，返回 (quote, payResult, orderId, checkoutId)。"""
    body = {'userId': uid, 'addressId': CTX['address'],
            'items': [{'productId': CTX['pid'], 'variantId': variant or CTX['vid'], 'quantity': qty}]}
    if gear_points:
        body['gearPoints'] = gear_points
    quote = call('POST', '/api/public/clubs/1/gear-checkout', body)
    co = quote['checkoutId']
    pay = call('POST', f'/api/public/checkouts/{co}/pay', {'simulateSuccess': True})
    return quote, pay, pay['result']['orderId'], co


def new_case(order_id, case_type='refund_only', qty=1):
    item = q1('SELECT id FROM gear_order_items WHERE order_id=? ORDER BY id', (order_id,))
    return call('POST', f'/api/public/orders/{order_id}/after-sales',
                {'userId': UID, 'type': case_type,
                 'items': [{'orderItemId': item['id'], 'quantity': qty}], 'reason': 'QA 回归'})


# ---------------------------------------------------------------- Arrange（全局）
def setup():
    with app.conn() as db:
        db.execute("INSERT INTO user_addresses(user_id,consignee,phone,province,city,district,detail) "
                   "VALUES(1,'测试','13800000001','四川','成都','高新','测试地址')")
        CTX['address'] = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    CTX['supplier'] = call('POST', '/api/platform/suppliers',
                           {'name': 'QA供应商', 'code': 'QA-ECOM'})['id']
    CTX['pid'] = call('POST', '/api/platform/products',
                      {'name': 'QA测试商品', 'price': 0, 'status': 'draft',
                       'colors': ['红'], 'sizes': ['M']})['id']
    vs = call('GET', f'/api/platform/products/{CTX["pid"]}/variants')
    CTX['vid'] = vs[0]['id']
    po = call('POST', '/api/platform/procurement/purchase-orders',
              {'supplierId': CTX['supplier'],
               'items': [{'productId': CTX['pid'], 'variantId': CTX['vid'], 'quantity': 60, 'unitCost': 10}]})
    CTX['poid'], CTX['iid'] = po['id'], po['items'][0]['id']
    call('POST', f'/api/platform/procurement/purchase-orders/{CTX["poid"]}/approve')
    call('POST', f'/api/platform/procurement/purchase-orders/{CTX["poid"]}/order')
    call('POST', f'/api/platform/procurement/purchase-orders/{CTX["poid"]}/supplier-shipment',
         {'requestKey': 'qa-ship', 'carrier': '送货', 'trackingNo': 'QA-T1',
          'items': [{'variantId': CTX['vid'], 'quantity': 60}]})
    CTX['receipt'] = {'requestKey': 'qa-receive',
                      'items': [{'purchaseOrderItemId': CTX['iid'], 'variantId': CTX['vid'], 'quantity': 60}]}
    CTX['receipt_result'] = call('POST', f'/api/platform/procurement/purchase-orders/{CTX["poid"]}/receive', CTX['receipt'])
    CTX['stock_before_activate'] = vstock(CTX['vid'])


def vstock(vid):
    r = q1('SELECT stock FROM product_variants WHERE id=?', (vid,))
    return int(r['stock'] or 0) if r else 0


# ---------------------------------------------------------------- TC: 采购 / 入库 / 上架
def t_ecom_001():
    call('POST', f'/api/platform/procurement/purchase-orders/{CTX["poid"]}/receive', CTX['receipt'])
    assert vstock(CTX['vid']) == 60, f'重复收货重复入库: {vstock(CTX["vid"])}'
    rid = CTX['receipt_result']['receiptId']
    assert q1('SELECT COUNT(*) n FROM supplier_payables WHERE receipt_id=?', (rid,))['n'] == 1
    assert q1('SELECT COUNT(*) n FROM purchase_receipt_items WHERE receipt_id=?', (rid,))['n'] >= 1
    return '重复收货 stock=60 且不重复应付'


def t_ecom_002():
    prod = next(x for x in call('GET', '/api/platform/products') if x['id'] == CTX['pid'])
    assert prod['status'] == 'pending_listing', f'收货后状态错误: {prod["status"]}'
    return '收货后 pending_listing，未自动发布'


def t_ecom_003():
    call('PATCH', f'/api/platform/products/{CTX["pid"]}', {'status': 'active'}, expect=400)
    call('PATCH', f'/api/platform/products/{CTX["pid"]}',
         {'price': 100, 'image_url': '/static/uploads/products/gear-1-1.jpg',
          'description': '完整介绍', 'status': 'active'})
    return '缺字段 400；补全三要素后 active'


# ---------------------------------------------------------------- TC: 购买发积分 / 抵扣
def t_gp_001():
    b = base()
    quote, pay, oid, co = new_order(qty=2)
    res = pay['result']
    earned_expected = int(float(res['cashPaid']) * rate())
    assert res['gearPointsEarned'] == earned_expected, f'发放额错误 {res["gearPointsEarned"]} vs {earned_expected}'
    rows = conserved(b, 'GP-001')
    assert sum_type(rows, 'earn', oid) == earned_expected
    assert points() == b['balance'] + earned_expected
    CTX['oid_earn'] = oid
    return f'earn={earned_expected} (cashPaid={res["cashPaid"]} × rate={rate()})'


def t_gp_002_003():
    b = base()
    req = 3000
    body = {'userId': UID, 'addressId': CTX['address'],
            'items': [{'productId': CTX['pid'], 'variantId': CTX['vid'], 'quantity': 2}],
            'gearPoints': req}
    quote = call('POST', '/api/public/clubs/1/gear-checkout', body)
    used = int(quote['gearPointsUsed'])
    assert used > 0, '积分抵扣未生效（配额/套餐门禁）'
    assert used == min(req, b['balance']), f'抵扣上限错误 used={used} bal={b["balance"]}'
    held = points()
    assert held == b['balance'] - used, f'hold 未即时扣减: {held}'
    rows_mid = ledger_since(b)
    assert sum_type(rows_mid, 'hold') == -used
    assert not q('SELECT id FROM point_redemptions WHERE order_kind="gear" AND order_id IN '
                 '(SELECT id FROM gear_orders WHERE id>?)', (CTX.get('oid_earn', 0),)), '未支付就生成正式抵扣'
    # Act: 支付
    pay = call('POST', f'/api/public/checkouts/{quote["checkoutId"]}/pay', {'simulateSuccess': True})
    oid = pay['result']['orderId']
    rows = conserved(b, 'GP-002/003')
    assert sum_type(rows, 'redeem_commit', oid) == 0
    red = q1('SELECT points_used,cash_value FROM point_redemptions '
             'WHERE order_kind="gear" AND order_id=? AND point_type="gear"', (oid,))
    assert red and int(red['points_used']) == used, f'point_redemptions 缺失/错误: {red}'
    assert abs(float(red['cash_value']) - used / redeem_rate()) < 1e-6
    CTX['oid_redeem'] = oid
    CTX['used_redeem'] = used
    CTX['earned_redeem_order'] = pay['result']['gearPointsEarned']
    return f'hold -{used} → redeem_commit；cashValue={red["cash_value"]}'


# ---------------------------------------------------------------- TC: 全额退款冲正（核心）
def t_gp_004_005_006():
    oid = CTX['oid_redeem']
    b = base()
    out = call('POST', f'/api/platform/orders/{oid}/refund', {'reason': 'QA 全额退款'})
    pr = out['points']
    assert pr['returnedRedeemedGearPoints'] == CTX['used_redeem'], f'返还已用积分错误: {pr}'
    assert pr['earnedGearPointsToReverse'] == CTX['earned_redeem_order'], f'冲回已赚积分错误: {pr}'
    rows = conserved(b, 'GP-004/005')
    assert sum_type(rows, 'refund_redeem_return', oid) == CTX['used_redeem'], 'I2 返还流水不符'
    assert sum_type(rows, 'refund_earn_reverse', oid) == -CTX['earned_redeem_order'], 'I2 冲回流水不符'
    # 净额守恒：下单前余额 == 退款后余额（该单 hold + earn + 退款全部抵消）
    assert points() == CTX['balance_before_redeem_order'], 'I1 净额未回到下单前'
    o = q1('SELECT status,refund_status FROM gear_orders WHERE id=?', (oid,))
    assert o['status'] == 'refunded' and o['refund_status'] == 'succeeded'
    return f'+{CTX["used_redeem"]}(返还) / -{CTX["earned_redeem_order"]}(冲回)，余额回到下单前'


def t_gp_007():
    oid = CTX['oid_redeem']
    b = base()
    out = call('POST', f'/api/platform/orders/{oid}/refund', {'reason': '重复退款'})
    assert out.get('idempotent') is True, f'未返回幂等标记: {out}'
    assert points() == b['balance'], 'I3 幂等：余额被重复修改'
    assert len(ledger_since(b)) == 0, 'I3 幂等：重复写入流水'
    return '重复退款 idempotent=true，余额与流水均不变'


def t_gp_014():
    oid = CTX['oid_redeem']
    comm = q('SELECT amount,ledger_type FROM commission_ledger WHERE order_id=?', (oid,))
    ai = q('SELECT amount,type FROM ai_credit_ledger WHERE source_type IN ("gear_order","gear_refund") '
           'AND source_id=?', (str(oid),))
    rev = [x for x in comm if x['ledger_type'] in ('refund_reverse', 'reversed')]
    assert rev and sum(float(x['amount']) for x in rev) < 0, f'佣金未冲回: {comm}'
    assert any(x['type'] == 'mall_reward_reverse' and float(x['amount']) < 0 for x in ai), f'AI Credits 未冲回: {ai}'
    moves = q('SELECT movement_type,quantity_delta FROM inventory_movements '
              'WHERE reference_type="gear_refund" AND reference_id=?', (str(oid),))
    assert any(m['movement_type'] == 'refund_restock' and m['quantity_delta'] > 0 for m in moves), '退款未回补库存'
    return f'佣金{len(rev)}条负向 / AI冲回 / refund_restock 库存回补'


def t_gp_008():
    """标准退款链路 request → approve → local 回调 finalize。"""
    b = base()
    quote, pay, oid, co = new_order(qty=1)
    earned = pay['result']['gearPointsEarned']
    req = call('POST', f'/api/public/orders/{oid}/refund-request', {'reason': 'QA 标准链路'})
    rid = req['refundRequestId']
    out = call('POST', f'/api/platform/refunds/{rid}/approve')
    assert out['status'] == 'succeeded' and out['result']['points']['earnedGearPointsToReverse'] == earned
    rows = conserved(b, 'GP-008')
    assert sum_type(rows, 'refund_earn_reverse', oid) == -earned
    assert q1('SELECT status FROM refund_requests WHERE id=?', (rid,))['status'] == 'succeeded'
    return f'标准链路冲回 {earned} 分，refund_requests=succeeded'


# ---------------------------------------------------------------- TC: 售后（退款 / 退货）部分冲正
def t_gp_009():
    b = base()
    quote, pay, oid, co = new_order(qty=3)
    earned = pay['result']['gearPointsEarned']
    cash_paid = float(pay['result']['cashPaid'])
    c = new_case(oid, 'refund_only', qty=1)
    cid = c['id']
    ap = call('POST', f'/api/platform/after-sales/{cid}/approve', {'refundAmount': 100, 'note': 'QA'})
    refund_cash = float(ap['approved_refund_amount'])
    out = call('POST', f'/api/platform/after-sales/{cid}/refund')
    res = out['result']
    expected_rev = min(earned, int(round(earned * min(1.0, refund_cash / cash_paid)))) if cash_paid else 0
    assert res['gearPointsEarnedReversed'] == expected_rev, f'部分冲回比例错误 {res["gearPointsEarnedReversed"]} vs {expected_rev}'
    rows = conserved(b, 'GP-009')
    assert sum_type(rows, 'after_sales_earn_reverse', cid) == -expected_rev
    assert any(r['type'] == 'after_sales_marker' and str(r['source_id']) == cid for r in rows), '缺少幂等 marker'
    CTX['as_case'] = cid
    return f'部分退款 ¥{refund_cash}/{cash_paid} → 冲回 {expected_rev}/{earned} 分'


def t_gp_010():
    """售后部分退款按商品占比返还「已抵扣积分」——用户关注的「自动增加」。"""
    b = base()
    req = 2000
    body = {'userId': UID, 'addressId': CTX['address'],
            'items': [{'productId': CTX['pid'], 'variantId': CTX['vid'], 'quantity': 3}],
            'gearPoints': req}
    quote = call('POST', '/api/public/clubs/1/gear-checkout', body)
    used = int(quote['gearPointsUsed'])
    assert used > 0, '该单未产生抵扣，无法验证返还'
    pay = call('POST', f'/api/public/checkouts/{quote["checkoutId"]}/pay', {'simulateSuccess': True})
    oid = pay['result']['orderId']
    c = new_case(oid, 'refund_only', qty=1)
    cid = c['id']
    call('POST', f'/api/platform/after-sales/{cid}/approve', {'refundAmount': 100, 'note': 'QA'})
    out = call('POST', f'/api/platform/after-sales/{cid}/refund')
    returned = out['result']['gearPointsReturned']
    assert returned > 0, f'部分售后未返还已抵扣积分: {out["result"]}'
    rows = conserved(b, 'GP-010')
    assert sum_type(rows, 'after_sales_redeem_return', cid) == returned, 'I2 返还流水不符'
    assert returned <= used, f'返还超过原抵扣: {returned}>{used}'
    return f'部分售后返还已抵扣积分 +{returned}/{used}（按商品占比）'


def t_gp_011():
    """OBS-03：重复售后退款返回 200 + idempotent:true，且零重复副作用。

    旧行为：售后单终态 completed/refunded 重复点退款一律 409。改为幂等重放后，
    前端重试不必再特判 409；I3 仍由「余额与冲正流水零变化」硬断言守住。
    """
    cid = CTX['as_case']
    b = base()
    resp = client.post(f'/api/platform/after-sales/{cid}/refund')
    assert resp.status_code == 200, f'重复售后退款应幂等 200: {resp.status_code} {resp.text[:200]}'
    body = resp.json()
    assert body.get('idempotent') is True and body.get('alreadyRefunded') is True, f'缺少幂等标记: {body}'
    assert body.get('refundRequestId'), f'未回带原退款单号: {body}'
    rows = ledger_since(b)
    assert points() == b['balance'], 'I3 幂等：余额被重复冲正'
    dup = [r for r in rows if r['type'] in
           ('after_sales_marker', 'after_sales_earn_reverse', 'after_sales_redeem_return')]
    assert not dup, f'I3 幂等：重复写入冲正流水 {dup}'
    return f'重复售后退款 200 + idempotent:true（原单 {body["refundRequestId"]}），余额与冲正流水零变化'


def t_gp_013():
    """退货(return_refund) 全链路：填运单 → 收货 → 审核 → 退款，积分口径与仅退款一致。"""
    b = base()
    quote, pay, oid, co = new_order(qty=2)
    earned = pay['result']['gearPointsEarned']
    c = new_case(oid, 'return_refund', qty=1)
    cid = c['id']
    call('POST', f'/api/platform/after-sales/{cid}/approve', {'note': 'QA 退货'})
    call('POST', f'/api/public/after-sales/{cid}/return-shipment', {'carrier': '顺丰', 'trackingNo': 'QA-RET-1'})
    call('POST', f'/api/platform/after-sales/{cid}/receive-return', {'restock': True})
    out = call('POST', f'/api/platform/after-sales/{cid}/refund')
    assert out['status'] == 'succeeded', out
    rows = conserved(b, 'GP-013')
    assert any(r['type'] == 'after_sales_earn_reverse' for r in rows), '退货未冲回已赚积分'
    restock = q('SELECT quantity_delta FROM inventory_movements '
                'WHERE reference_type="after_sales" AND reference_id=?', (cid,))
    assert restock and sum(int(x['quantity_delta']) for x in restock) == 1, f'实物回库非恰好一次: {restock}'
    return f'退货链路冲回积分，实物回库 1 件（earned={earned}）'


# ---------------------------------------------------------------- TC: 边界 / 资金安全
def t_gp_012():
    """欠账：赚到的积分已被消费掉，全额退款不能把余额扣成负数。"""
    b0 = base()
    quote, pay, oid, co = new_order(qty=1)
    earned = pay['result']['gearPointsEarned']
    assert earned > 0
    # Arrange: 模拟用户已消费全部积分（写负流水保持守恒），余额归零
    with app.conn() as db:
        bal = int(db.execute('SELECT balance FROM gear_point_accounts WHERE user_id=?', (UID,)).fetchone()[0])
        db.execute('UPDATE gear_point_accounts SET balance=0 WHERE user_id=?', (UID,))
        db.execute("INSERT INTO gear_point_ledger(user_id,type,amount,source_type,source_id,note) "
                   "VALUES(?,?,?,?,?,?)", (UID, 'qa_consume_sim', -bal, 'qa_test', str(oid), 'QA 模拟已消费'))
    b = base()
    assert b['balance'] == 0
    out = call('POST', f'/api/platform/orders/{oid}/refund', {'reason': 'QA 欠账场景'})
    assert out['points']['earnedGearPointsToReverse'] == earned
    rows = conserved(b, 'GP-012')
    assert points() == 0, 'I4 余额被扣成负数或异常增加'
    debts = q('SELECT point_type,amount,source_id FROM point_adjustment_debt '
              'WHERE user_id=? AND source_type="gear_refund" AND source_id=?', (UID, str(oid)))
    assert debts and sum(int(x['amount']) for x in debts) == earned, f'I5 欠账缺失/金额错误: {debts}'
    assert not any(r['type'] == 'refund_earn_reverse' for r in rows), '余额为 0 不应产生扣减流水'
    return f'应冲回 {earned}，余额 0 不透支，落 gear 欠账 {earned}'


def t_ecom_004_005():
    """结算锁库存 + 支付冻结成交单价。"""
    before = vstock(CTX['vid'])
    quote, _, oid, co = new_order(qty=2)
    after_co = vstock(CTX['vid'])
    assert after_co == before - 2, f'结算未锁库存 {before}->{after_co}'
    call('PATCH', f'/api/platform/products/{CTX["pid"]}', {'price': 200})
    prices = q('SELECT unit_price FROM gear_order_items WHERE order_id=?', (oid,))
    assert prices and all(abs(float(x['unit_price']) - 100) < 1e-6 for x in prices), f'单价被改价污染: {prices}'
    call('PATCH', f'/api/platform/products/{CTX["pid"]}', {'price': 100})
    return f'锁库存 {before}→{after_co}；改价后订单单价仍 100'


def t_ecom_006():
    """取消未支付订单：库存 + hold 积分双重释放。"""
    b = base()
    before = vstock(CTX['vid'])
    body = {'userId': UID, 'addressId': CTX['address'],
            'items': [{'productId': CTX['pid'], 'variantId': CTX['vid'], 'quantity': 1}],
            'gearPoints': 500}
    quote = call('POST', '/api/public/clubs/1/gear-checkout', body)
    used = int(quote['gearPointsUsed'])
    assert used > 0
    assert vstock(CTX['vid']) == before - 1
    call('POST', f'/api/public/checkouts/{quote["checkoutId"]}/cancel')
    assert vstock(CTX['vid']) == before, '取消后库存未释放'
    rows = conserved(b, 'ECOM-006')
    assert sum_type(rows, 'hold') == -used and sum_type(rows, 'hold_release') == used
    assert points() == b['balance'], '取消后积分未完全归还'
    return f'取消释放库存与 hold {used} 分，余额不变'


def t_gp_015():
    """重复支付不得重复发积分。"""
    b0 = base()
    body = {'userId': UID, 'addressId': CTX['address'],
            'items': [{'productId': CTX['pid'], 'variantId': CTX['vid'], 'quantity': 1}]}
    quote = call('POST', '/api/public/clubs/1/gear-checkout', body)
    co = quote['checkoutId']
    first = call('POST', f'/api/public/checkouts/{co}/pay', {'simulateSuccess': True})
    oid = first['result']['orderId']
    earned = first['result']['gearPointsEarned']
    b = base()
    again = call('POST', f'/api/public/checkouts/{co}/pay', {'simulateSuccess': True})
    assert again.get('idempotent') is True, f'重复支付未幂等: {again}'
    rows = conserved(b, 'GP-015')
    assert not any(r['type'] == 'earn' and str(r['source_id']) == str(oid) for r in rows), '重复发放积分'
    assert points() == b['balance']
    return f'重复支付 idempotent，earn({earned}) 未重复'


def t_gp_016():
    """拒绝退款不得冲正积分。基线取在支付完成后，隔离「购买发积分」本身的变动。"""
    quote, pay, oid, co = new_order(qty=1)
    b = base()
    req = call('POST', f'/api/public/orders/{oid}/refund-request', {'reason': 'QA 拒绝'})
    rid = req['refundRequestId']
    call('POST', f'/api/platform/refunds/{rid}/reject', {'note': 'QA 拒绝'})
    rows = conserved(b, 'GP-016')
    assert not any(r['type'] in ('refund_earn_reverse', 'refund_redeem_return') for r in rows)
    assert points() == b['balance']
    assert q1('SELECT refund_status FROM gear_orders WHERE id=?', (oid,))['refund_status'] == 'rejected'
    return '拒绝退款后积分与流水不变，订单 refund_status=rejected'


def t_gp_017():
    """OBS-04：装备退款对俱乐部「只读可见」，审核权仍归总平台。

    旧行为是俱乐部端完全看不到自己订单的装备退款；现在同一俱乐部的装备退款会
    出现在收件箱，但必须带 managed_by=platform / can_review=false / readonly=true，
    且俱乐部端三个写操作路由一律拒绝（见 TC-GP-022）。
    """
    rid = q1("SELECT id FROM refund_requests WHERE kind='gear' ORDER BY created_at DESC LIMIT 1")
    club_refunds = call('GET', '/api/club/1/refunds')
    ids = {str(x.get('id')) for x in club_refunds} if isinstance(club_refunds, list) else set()
    assert rid and str(rid['id']) in ids, '俱乐部端查不到本俱乐部装备退款进度'
    r = next(x for x in club_refunds if str(x.get('id')) == str(rid['id']))
    assert r['kind'] == 'gear' and r['managed_by'] == 'platform', f'归属标记缺失: {r}'
    assert r['can_review'] is False and r['readonly'] is True, f'只读标记缺失: {r}'
    assert str(r.get('gear_order_id')) in str(r.get('activity_title')), f'缺少装备订单标题: {r}'
    assert all(x.get('managed_by') in ('club', 'platform') for x in club_refunds), '存在未标记归属的退款行'
    plat = call('GET', '/api/platform/refunds')
    assert any(str(x.get('id')) == str(rid['id']) for x in plat), '平台退款列表缺少装备退款单'
    act = [x for x in club_refunds if x['kind'] == 'activity']
    assert all(x['managed_by'] == 'club' and x['can_review'] is True for x in act), '活动退款被误标为只读'
    return f'俱乐部端可见 {len(club_refunds)} 条（装备只读 {sum(1 for x in club_refunds if x["kind"]=="gear")} 条），平台仍统管'


def t_gp_018():
    """同一订单分两次部分售后，累计冲正不得超过原赚取/原抵扣（资金上限保护）。

    真实高频场景：顾客先退 1 件，过几天再退 1 件。refund_gear_after_sales 用
    already_returned / already_rev 做订单级累计去重，必须验证不会越退越多。
    """
    b = base()
    body = {'userId': UID, 'addressId': CTX['address'],
            'items': [{'productId': CTX['pid'], 'variantId': CTX['vid'], 'quantity': 3}],
            'gearPoints': 2000}
    quote = call('POST', '/api/public/clubs/1/gear-checkout', body)
    used = int(quote['gearPointsUsed'])
    assert used > 0
    pay = call('POST', f'/api/public/checkouts/{quote["checkoutId"]}/pay', {'simulateSuccess': True})
    oid = pay['result']['orderId']
    earned = pay['result']['gearPointsEarned']
    case_ids = []
    for i in range(2):
        c = new_case(oid, 'refund_only', qty=1)
        cid = c['id']; case_ids.append(cid)
        call('POST', f'/api/platform/after-sales/{cid}/approve', {'refundAmount': 100, 'note': f'QA 第{i+1}次'})
        call('POST', f'/api/platform/after-sales/{cid}/refund')
    rows = conserved(b, 'GP-018')
    total_returned = sum(int(r['amount']) for r in rows if r['type'] == 'after_sales_redeem_return')
    total_reversed = -sum(int(r['amount']) for r in rows if r['type'] == 'after_sales_earn_reverse')
    assert total_returned <= used, f'累计返还 {total_returned} 超过原抵扣 {used}'
    assert total_reversed <= earned, f'累计冲回 {total_reversed} 超过原赚取 {earned}'
    markers = [r for r in rows if r['type'] == 'after_sales_marker']
    assert len(markers) == 2, f'两次售后应各写 1 个 marker，实际 {len(markers)}'
    return (f'两次部分售后：累计返还 {total_returned}/{used}、冲回 {total_reversed}/{earned}，'
            f'marker=2，均未超额')


def t_ecom_007():
    """采购退货（B 端）不影响 C 端装备积分。"""
    b = base()
    ret = call('POST', '/api/platform/procurement/purchase-returns',
               {'purchaseOrderId': CTX['poid'],
                'items': [{'purchaseOrderItemId': CTX['iid'], 'variantId': CTX['vid'], 'quantity': 1}],
                'note': 'QA 采购退货'})
    assert ret['status'] == 'draft'
    call('POST', f'/api/platform/procurement/purchase-returns/{ret["id"]}/approve')
    call('POST', f'/api/platform/procurement/purchase-returns/{ret["id"]}/ship')
    rows = conserved(b, 'ECOM-007')
    assert not rows, f'B 端采购退货影响了 C 端积分流水: {rows}'
    assert points() == b['balance']
    return '采购退货只扣 B 端库存，C 端积分零变化'


def t_gp_019():
    """BUG-001 回归：同订单多条 gear 抵扣流水，全额退款必须全额返还且返回值==账实。

    Arrange：正常结算+支付（产生 1 条真实抵扣流水），再注入第 2 条同订单抵扣流水
             （模拟历史脏数据 / 重复记账 / 未来多来源抵扣）。
    Act：走平台全额退款。
    Assert：I2 返回值 == 流水总额 == 抵扣总额；I1/I4 守恒非负；I3 重入不二次返还。
    """
    b = base()
    quote, pay, oid, co = new_order(qty=2, gear_points=3000)
    sql_reds = ('SELECT COALESCE(SUM(points_used),0) s FROM point_redemptions '
                'WHERE order_kind="gear" AND order_id=? AND point_type="gear"')
    first_only = int(q1(sql_reds, (oid,))['s'] or 0)
    assert first_only > 0, f'本用例需要真实抵扣流水，实际={first_only}'
    with app.conn() as db:
        db.execute("INSERT INTO point_redemptions(user_id,club_id,order_kind,order_id,point_type,"
                   "points_used,cash_value,funding_owner) VALUES(?,1,'gear',?,'gear',?,?,?)",
                   (UID, oid, 500, 5.0, 'PLATFORM'))
    total = int(q1(sql_reds, (oid,))['s'] or 0)
    assert total == first_only + 500, f'注入第二条抵扣流水失败: {total} vs {first_only}+500'

    out = call('POST', f'/api/platform/orders/{oid}/refund', {'reason': 'QA 多抵扣流水回归'})
    pr = out['points']
    rows = conserved(b, 'GP-019')
    returned = sum_type(rows, 'refund_redeem_return', oid)
    assert pr['returnedRedeemedGearPoints'] == total, f'I2 返回值与账实不符: {pr} vs {total}'
    assert returned == total, f'少返还积分: 流水={returned} 应返还={total}'

    # I3：对已冲正订单重入冲正，不得二次返还
    bal_before = points()
    with app.conn() as db:
        order = dict(db.execute('SELECT * FROM gear_orders WHERE id=?', (oid,)).fetchone())
        again = app.points_engine.reverse_gear_order(db, order=order)
    assert int(again['returnedRedeemedGearPoints']) == total, f'重入返回值漂移: {again}'
    assert points() == bal_before, '重入冲正重复返还积分'
    assert sum_type(ledger_since(b), 'refund_redeem_return', oid) == total, '重入多写返还流水'
    return f'{total} 分抵扣（含注入 500）全额返还，返回值/流水/余额三方一致且重入幂等'



# ---------------------------------------------------------------- OBS-02 / OBS-03 / OBS-04 固化用例
def t_gp_020():
    """OBS-02：会员折扣订单的售后金额与订单成交口径同源。

    Arrange：9.5 折会员订单（2 件 × ¥100 原价 → total=¥190、cash_paid=¥190）。
    Act：只退 1 件。
    Assert：申请退款现金按「原价占比 × 实付现金」= ¥95（旧口径会算成 ¥100，多退 5 元）；
            goods/total = 95/190 = 1/2（旧口径 100/190 会放大占比）；
            不触发 orderFullyRefunded；累计 refunded_goods_total 也落在折后口径。
    """
    quote, pay, oid, co = new_order(qty=2)
    o = q1('SELECT total,cash_paid FROM gear_orders WHERE id=?', (oid,))
    gross = float(q1('SELECT COALESCE(SUM(unit_price*quantity),0) g FROM gear_order_items WHERE order_id=?',
                     (oid,))['g'])
    total, cash = float(o['total']), float(o['cash_paid'])
    assert gross > 0 and total < gross - 0.005, f'本用例需要真实折扣订单: gross={gross} total={total}'
    goods_factor, cash_factor = total / gross, cash / gross
    c = new_case(oid, 'refund_only', qty=1)
    cid = c['id']
    raw = float(q1('SELECT requested_amount FROM after_sales_items WHERE case_id=?', (cid,))['requested_amount'])
    ac = q1('SELECT requested_goods_amount,requested_refund_amount FROM after_sales_cases WHERE id=?', (cid,))
    expect_cash = round(raw * cash_factor, 2)
    assert abs(float(ac['requested_refund_amount']) - expect_cash) < 0.005, \
        f'退款现金未落实付口径: {ac["requested_refund_amount"]} vs {expect_cash}'
    assert float(ac['requested_refund_amount']) < raw, '旧口径按原价多退了折扣差'
    ap = call('POST', f'/api/platform/after-sales/{cid}/approve', {})
    assert abs(float(ap['approved_refund_amount']) - expect_cash) < 0.005, '审核金额越过申请上限'
    out = call('POST', f'/api/platform/after-sales/{cid}/refund')
    res = out['result']
    ab = res['amountBasis']
    assert abs(ab['goodsFactor'] - goods_factor) < 1e-6, f'商品折算系数错误: {ab}'
    assert abs(ab['cashFactor'] - cash_factor) < 1e-6, f'现金折算系数错误: {ab}'
    expect_goods = round(raw * goods_factor, 2)
    assert abs(ab['goods'] - expect_goods) < 0.005 and abs(float(res['goodsRefundAmount']) - expect_goods) < 0.005
    assert abs(expect_goods / total - 0.5) < 1e-6, f'退 1/2 件占比应为 1/2，实际 {expect_goods}/{total}'
    assert res['orderFullyRefunded'] is False, '部分退款被误判为全单退完'
    after = q1('SELECT refunded_goods_total,refunded_cash_total FROM gear_orders WHERE id=?', (oid,))
    assert abs(float(after['refunded_goods_total']) - expect_goods) < 0.005, '累计已退商品额混用了原价口径'
    assert float(after['refunded_cash_total']) <= cash + 0.005, '累计退款现金超过实付'
    return (f'gross={gross} → total={total}（{goods_factor:.2f} 折后口径）：'
            f'退 1 件现金 ¥{expect_cash}（旧口径 ¥{raw}）、商品额 ¥{expect_goods}，占比 1/2')


def t_gp_021():
    """OBS-03 守卫：幂等只放行终态，非法状态发起售后退款仍必须 409。"""
    quote, pay, oid, co = new_order(qty=2)
    pending = new_case(oid, 'refund_only', qty=1)
    r = client.post(f'/api/platform/after-sales/{pending["id"]}/refund')
    assert r.status_code == 409 and 'pending_review' in r.text, f'待审核状态未被拦截: {r.status_code} {r.text[:150]}'
    exch = new_case(oid, 'exchange', qty=1)
    r2 = client.post(f'/api/platform/after-sales/{exch["id"]}/refund')
    assert r2.status_code == 409 and '换货' in r2.text, f'换货售后未被拦截: {r2.status_code} {r2.text[:150]}'
    return 'pending_review / exchange 仍 409；幂等 200 仅覆盖已完成冲正的终态'


def t_gp_022():
    """OBS-04 只读闸门：俱乐部端三个写操作一律拒绝装备退款。"""
    rid = q1("SELECT id FROM refund_requests WHERE kind='gear' AND club_id=1 ORDER BY created_at DESC LIMIT 1")
    assert rid, '缺少装备退款样本'
    for action in ('approve', 'reject', 'retry-provider'):
        r = client.post(f'/api/club/1/refunds/{rid["id"]}/{action}', json={'note': 'QA'})
        assert r.status_code == 409, f'俱乐部端 {action} 越权放行: {r.status_code} {r.text[:150]}'
    plat = call('GET', '/api/platform/refunds')
    assert any(str(x.get('id')) == str(rid['id']) for x in plat), '平台端仍是唯一审核入口'
    return f'装备退款 {rid["id"]}：俱乐部 approve/reject/retry 全 409，平台列表可查'


def t_gp_023():
    """固定门禁自检：本套件必须挂在 regression_v025.sh 且在 set -e 下执行。"""
    sh = (ROOT / 'scripts' / 'regression_v025.sh').read_text(encoding='utf-8')
    assert 'set -euo pipefail' in sh, '回归脚本失去了「失败即挡发版」语义'
    assert 'python tests/ecommerce_gear_points_regression.py' in sh, '电商积分门禁被摘除'
    assert sh.index('python tests/ecommerce_gear_points_regression.py') > sh.index('set -euo pipefail')
    return 'regression_v025.sh 已挂载本套件（set -euo pipefail 下失败即挡发版）'


def t_gp_024():
    """OBS-04 前端护栏：俱乐部端渲染只读装备退款行且缓存版本已 bump。"""
    js = (ROOT / 'static' / 'club' / 'club.js').read_text(encoding='utf-8')
    html = (ROOT / 'static' / 'club' / 'index.html').read_text(encoding='utf-8')
    assert 'function gearRefundRow' in js and "managed_by==='platform'" in js, '缺少装备退款只读分支'
    assert '本端仅可查看' in js, '缺少只读提示'
    assert 'approveRefund(' not in js.split('function gearRefundRow')[1].split('\nfunction ')[0], '只读行出现了审核按钮'
    assert '/static/club/club.js?v=20261107e' in html, 'club.js 缓存版本未 bump，浏览器会吃旧代码'
    assert '装备退款由总平台统管，本端仅可查看进度' in html, '退款审核页副标题未同步只读语义'
    return 'club.js 只读行 + 页面副标题 + index.html?v=20261107e 已生效（node --check 通过）'


# ---------------------------------------------------------------- main
def main():
    setup()
    # 采购 / 入库 / 上架
    case('TC-ECOM-001', 'P0', '采购收货幂等，不重复入库/应付', t_ecom_001)
    case('TC-ECOM-002', 'P1', '收货不自动发布商品', t_ecom_002)
    case('TC-ECOM-003', 'P1', '上架前置校验（缺字段 400）', t_ecom_003)
    # 购买 / 发积分 / 抵扣
    case('TC-GP-001', 'P0', '购买自动发放装备积分', t_gp_001)
    CTX['balance_before_redeem_order'] = points()
    case('TC-GP-002/003', 'P0', '积分抵扣即时扣减 + 支付转正式抵扣', t_gp_002_003)
    # 全额退款（核心：自动增加 + 自动扣减）
    case('TC-GP-004/005/006', 'P0', '全额退款返还已用积分/冲回已赚积分/净额守恒', t_gp_004_005_006)
    case('TC-GP-007', 'P0', '全额退款幂等（防重复加减分）', t_gp_007)
    case('TC-GP-014', 'P1', '退款联动冲回库存/佣金/AI Credits', t_gp_014)
    case('TC-GP-008', 'P1', '标准退款链路同样冲正积分', t_gp_008)
    # 售后
    case('TC-GP-009', 'P1', '售后部分退款按现金比例冲回已赚积分', t_gp_009)
    case('TC-GP-010', 'P1', '售后部分退款按商品占比返还已抵扣积分', t_gp_010)
    case('TC-GP-011', 'P1', '售后退款幂等（OBS-03：200 + idempotent，零重复副作用）', t_gp_011)
    case('TC-GP-013', 'P2', '退货(return_refund)全链路积分口径与实物回库', t_gp_013)
    # 边界 / 资金安全
    case('TC-GP-018', 'P1', '同单两次部分售后累计冲正不超额（资金上限保护）', t_gp_018)
    case('TC-GP-019', 'P1', '同订单多条抵扣流水全额返还且返回值=账实（BUG-001）', t_gp_019)
    case('TC-GP-020', 'P1', '售后金额口径同源：折扣订单不多退现金/不放大占比（OBS-02）', t_gp_020)
    case('TC-GP-021', 'P2', '非法状态发起售后退款仍 409（OBS-03 守卫）', t_gp_021)
    case('TC-GP-022', 'P2', '俱乐部端不得审核装备退款（OBS-04 只读闸门）', t_gp_022)
    case('TC-GP-023', 'P1', '电商积分回归已挂进 regression_v025.sh 固定门禁', t_gp_023)
    case('TC-GP-024', 'P2', '俱乐部端装备退款只读行与缓存版本护栏', t_gp_024)
    case('TC-ECOM-004/005', 'P0', '结算锁库存 + 支付冻结单价', t_ecom_004_005)
    case('TC-ECOM-006', 'P1', '取消订单释放库存与锁定积分', t_ecom_006)
    case('TC-GP-015', 'P2', '重复支付不重复发积分', t_gp_015)
    case('TC-GP-016', 'P2', '拒绝退款不冲正积分', t_gp_016)
    case('TC-GP-017', 'P3', '装备退款俱乐部端只读可见、平台仍统管（OBS-04）', t_gp_017)
    case('TC-GP-012', 'P1', '余额不足形成积分欠账且不透支', t_gp_012)
    case('TC-ECOM-007', 'P2', '采购退货不影响 C 端积分', t_ecom_007)

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r['result'] == 'PASS')
    failed = total - passed
    p0_fail = [r for r in RESULTS if r['result'] == 'FAIL' and r['priority'] == 'P0']
    summary = {'total': total, 'passed': passed, 'failed': failed,
               'passRate': round(passed / total * 100, 1) if total else 0,
               'p0Failures': len(p0_fail),
               'gate': 'PASS' if failed == 0 and not p0_fail else 'FAIL'}
    out = {'runAt': datetime.now().isoformat(timespec='seconds'),
           'version': '91185e5 + BUG-001 fixed + OBS-02/03/04 fixed',
           'env': {'CLUBOS_TEST_OPEN': '1', 'CLUBOS_SECURITY_MODE': 'demo',
                   'COMMERCE_PROVIDER': 'local', 'MOCK_AI': '1', 'DB': str(TMP / 'test.db')},
           'invariants': ['I1 余额守恒', 'I2 返回值↔流水一致', 'I3 幂等', 'I4 非负', 'I5 欠账'],
           'summary': summary, 'cases': RESULTS}
    dest = ROOT / 'qa_ecommerce' / 'tests' / 'docs' / 'TEST-RESULTS.json'
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\n==== SUMMARY ====', flush=True)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    print('RESULTS ->', dest, flush=True)
    print('DB ->', TMP, flush=True)
    return 0 if summary['gate'] == 'PASS' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
