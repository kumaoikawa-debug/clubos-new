"""BUG-001 修复验证脚本：多 gear redemption 异常数据下全额退款账实一致性。

运行：./venv/bin/python qa_ecommerce/tests/e2e/reproduce-BUG-001.py
修复前（0873e01）：MISMATCH = True（上报 3500 / 实际入账 3000 / 少返还 500 分）
修复后（BUG-001 fixed）：MISMATCH = False，脚本零退出
  total redemptions points = 3500
  reported returnedRedeemedGearPoints = 3500
  actual refund_redeem_return ledger sum = 3500
根因（已修复）：clubos_domain/points.py reverse_gear_order() 中 `if already: break`
      位于 for r0 in reds 循环内部，第一条返还写入后即跳出，其余抵扣流水被跳过，
      返回值却仍按 sum(所有 reds) 求和，形成账实不符。
修复方式：改为「总抵扣额 - 已返还额 = 待返还差额」一次性入账，幂等基于流水 SUM，
      返回值取累计已返还额。同场景已固化为
      tests/ecommerce_gear_points_regression.py 的 TC-GP-019（含重入幂等断言）。
"""
import os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT)); sys.dont_write_bytecode = True
TMP = Path(tempfile.mkdtemp(prefix='clubos-multired-'))
os.environ.update(CLUBOS_TEST_OPEN='1', CLUBOS_SECURITY_MODE='demo', COMMERCE_PROVIDER='local',
                  MOCK_AI='1', CLUBOS_DB_PATH=str(TMP / 'test.db'))
import app
from fastapi.testclient import TestClient
c = TestClient(app.app, raise_server_exceptions=False)


def call(m, p, d=None, s=200):
    r = c.request(m, p, json=d)
    assert r.status_code == s, (m, p, r.status_code, r.text[:400])
    return r.json()


with app.conn() as db:
    db.execute("INSERT INTO user_addresses(user_id,consignee,phone,province,city,district,detail) "
               "VALUES(1,'测试','13800000001','四川','成都','高新','地址')")
    addr = db.execute('SELECT last_insert_rowid()').fetchone()[0]
call('POST', '/api/platform/suppliers', {'name': '多抵扣供应商', 'code': 'MR-TEST'})
pid = call('POST', '/api/platform/products',
           {'name': '多抵扣商品', 'price': 100, 'stock': 50, 'description': '完整',
            'image_url': '/static/uploads/products/gear-1-1.jpg', 'status': 'active'})['id']
with app.conn() as db:
    db.execute('INSERT OR REPLACE INTO gear_point_accounts(user_id,balance) VALUES(1,100000)')

quote = call('POST', '/api/public/clubs/1/gear-checkout',
             {'userId': 1, 'addressId': addr,
              'items': [{'productId': pid, 'quantity': 2}], 'gearPoints': 3000})
pay = call('POST', f'/api/public/checkouts/{quote["checkoutId"]}/pay', {'simulateSuccess': True})
oid = pay['result']['orderId']

# 注入第二条同订单 gear redemption（模拟历史脏数据 / 重复记账）
with app.conn() as db:
    db.execute("INSERT INTO point_redemptions(user_id,club_id,order_kind,order_id,point_type,"
               "points_used,cash_value,funding_owner) VALUES(1,1,'gear',?,'gear',?,?,?)",
               (oid, 500, 5.0, 'PLATFORM'))
    reds = [int(r[0]) for r in db.execute(
        'SELECT points_used FROM point_redemptions WHERE order_kind="gear" AND order_id=? '
        'AND point_type="gear" ORDER BY id', (oid,))]
    before = int(db.execute('SELECT balance FROM gear_point_accounts WHERE user_id=1').fetchone()[0])

out = call('POST', f'/api/platform/orders/{oid}/refund', {'reason': 'QA multi-redemption probe'})
with app.conn() as db:
    after = int(db.execute('SELECT balance FROM gear_point_accounts WHERE user_id=1').fetchone()[0])
    actual = sum(int(r[0]) for r in db.execute(
        "SELECT amount FROM gear_point_ledger WHERE user_id=1 AND type='refund_redeem_return' "
        "AND source_id=?", (str(oid),)))
reported = out['points']['returnedRedeemedGearPoints']

print('total redemptions points =', sum(reds))
print('reported returnedRedeemedGearPoints =', reported)
print('actual refund_redeem_return ledger sum =', actual)
print('short-changed points =', sum(reds) - actual)
print('balance before/after =', before, after, 'delta =', after - before)
print('I2 (返回值↔流水一致) VIOLATED =', reported != actual)
mismatch = reported != actual
print('MISMATCH =', mismatch)
assert not mismatch, 'BUG-001 回归：返回值与流水不一致'
assert sum(reds) == actual, 'BUG-001 回归：仍未全额返还'
print('BUG-001 FIX VERIFIED (TC-GP-019 scenario)')
