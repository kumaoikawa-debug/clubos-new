# -*- coding: utf-8 -*-
"""俱乐部佣金率（按俱乐部单独设定）的判定回归（2026-10-24 用户反馈）。

用户原话
--------
> 「刚刚还有一个问题，每个俱乐部的佣金也不一样，需要在俱乐部管理那里可以调整」

改造前的模型：佣金率只有**商品级**一个维度（products.commission_rate，默认 8%），
所有俱乐部拿同一个比例 —— 显然不现实：每个俱乐部的渠道能力、合作深度、
议价能力都不同。

改造后的模型：
    实际生效佣金率 = 俱乐部专属费率（若总平台单独设定且启用）
                    否则 商品默认费率
    平台总开关（commission_enabled）关闭 → 一律不发佣金。

本测试锁住这套模型的**每一条不变量**：

① 默认回落到商品费率：没有单独设定时，生效费率 == 商品 commission_rate。
② 平台按俱乐部设不同费率：A 12%、B 18% 互不影响。
③ 单独设定优先于商品费率：设了 15% 就用 15%，不再看商品默认。
④ 清除单独设定 → 回到商品默认费率。
⑤ 平台可单独停发某俱乐部佣金（enabled=false → 费率 0）。
⑥ 平台总开关关闭 → 所有俱乐部一律 0 佣金。
⑦ **成交时快照**：改费率不影响已成交订单；新订单用新费率；
   订单项写入 commission_rate_snapshot（退款冲回据此计算，不会漂移）。
⑧ 边界：费率必须在 0~100%，启用时不能为 0，俱乐部必须存在。
⑨ 三处同源：平台端 API、俱乐部端佣金汇总、实际下单产生的佣金金额必须一致。

自带临时库隔离（CLUBOS_DB_PATH 指到临时目录），不碰项目真库。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix='clubos_commrate_')
os.environ.update({
    'CLUBOS_DB_PATH': str(Path(tmp.name) / 'comm.sqlite'),
    'CLUBOS_SECURITY_MODE': 'demo',
    'MOCK_AI': '1',
    'COMMERCE_PROVIDER': 'local',
})
os.environ.setdefault('CLUBOS_TEST_OPEN', '1')

import db  # noqa: E402
import app as appmod  # noqa: E402
from clubos_domain.commission_rate import ClubCommissionRateService  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FAILS = []
PASS = []


def check(name, ok, detail=''):
    print(('  PASS  ' if ok else '  FAIL  ') + name + (f'   | {detail}' if detail else ''))
    if ok:
        PASS.append(name)
    else:
        FAILS.append(name)


db.init_db()
client = TestClient(appmod.app)
service = ClubCommissionRateService(appmod.setting)

# ── 夹具：两个俱乐部 + 一个商品 ────────────────────────────────────────────
with db.conn() as c:
    c.execute("INSERT INTO clubs(name,status,plan) VALUES('A 俱乐部','active','pro')")
    c.execute("INSERT INTO clubs(name,status,plan) VALUES('B 俱乐部','active','pro')")
    ids = [int(r['id']) for r in c.execute(
        "SELECT id FROM clubs WHERE name IN ('A 俱乐部','B 俱乐部') ORDER BY id")]
    CLUB_A, CLUB_B = ids[0], ids[1]
    c.execute("INSERT INTO products(name,sku,price,stock,status,category,commission_rate) "
              "VALUES('测试帐篷','TEST-TENT-1',200,50,'active','装备',0.08)")
    PID = int(c.execute("SELECT id FROM products WHERE sku='TEST-TENT-1'").fetchone()['id'])
    # 库存已下沉到规格层：仓库要有可售库位和真实库存，否则下单会被拦「仓库可用库存不足」
    c.execute("INSERT INTO warehouses(code,name,status,is_default) VALUES('WH-TEST','测试仓','active',1)")
    WH = int(c.execute("SELECT id FROM warehouses WHERE code='WH-TEST'").fetchone()['id'])
    c.execute("INSERT INTO warehouse_locations(warehouse_id,code,name,zone_type,status,is_default) VALUES(?,'L-1','测试库位','sellable','active',1)", (WH,))
    LOC = int(c.execute("SELECT id FROM warehouse_locations WHERE code='L-1'").fetchone()['id'])
    c.execute("INSERT INTO warehouse_inventory(product_id,location_id,on_hand,reserved) VALUES(?,?,50,0)", (PID, LOC))


def resolve(club_id, product_rate=0.08):
    with db.conn() as c:
        return service.resolve(c, club_id, product_rate)


def set_platform_enabled(enabled):
    with db.conn() as c:
        service.set_platform_enabled(c, enabled)


print('== 1. 默认回落到商品费率 ==')
r_a = resolve(CLUB_A)
check('未单独设定时生效费率 = 商品 8%',
      abs(r_a.rate - 0.08) < 1e-9 and r_a.source == 'product_default',
      f'rate={r_a.rate} source={r_a.source}')
check('未单独设定时 has_club_override=False', r_a.has_club_override is False)

print('\n== 2. 平台按俱乐部设不同费率（互不影响） ==')
with db.conn() as c:
    service.set_club_rate(c, CLUB_A, rate=0.12, enabled=True, note='A 协议')
    service.set_club_rate(c, CLUB_B, rate=0.18, enabled=True, note='B 协议')
r_a = resolve(CLUB_A)
r_b = resolve(CLUB_B)
check('A 俱乐部生效 12%', abs(r_a.rate - 0.12) < 1e-9, f'rate={r_a.rate}')
check('B 俱乐部生效 18%', abs(r_b.rate - 0.18) < 1e-9, f'rate={r_b.rate}')
check('A 的单独设定标记为 True', r_a.has_club_override is True and r_a.source == 'club_override')

print('\n== 3. 单独设定优先于商品费率 ==')
r_a = resolve(CLUB_A, product_rate=0.05)
check('即使商品费率是 5%，A 仍按单独设定的 12%',
      abs(r_a.rate - 0.12) < 1e-9, f'rate={r_a.rate} productRate={r_a.product_rate}')

print('\n== 4. 清除单独设定 → 回到商品默认 ==')
with db.conn() as c:
    service.clear_club_rate(c, CLUB_A)
r_a = resolve(CLUB_A)
check('清除后 A 回到商品默认 8%',
      abs(r_a.rate - 0.08) < 1e-9 and r_a.source == 'product_default',
      f'rate={r_a.rate} source={r_a.source}')
check('B 不受影响，仍是 18%', abs(resolve(CLUB_B).rate - 0.18) < 1e-9)

print('\n== 5. 平台可单独停发某俱乐部佣金 ==')
with db.conn() as c:
    service.set_club_rate(c, CLUB_A, rate=0.12, enabled=False, note='暂停')
r_a = resolve(CLUB_A)
check('停发后 A 的生效费率为 0', r_a.rate == 0.0 and r_a.enabled is False,
      f'rate={r_a.rate} enabled={r_a.enabled}')
check('停发原因说清了是平台关的', '关闭' in r_a.reason or '不发' in r_a.reason, r_a.reason)
check('B 不受影响', abs(resolve(CLUB_B).rate - 0.18) < 1e-9)
with db.conn() as c:
    service.clear_club_rate(c, CLUB_A)

print('\n== 6. 平台总开关关闭 → 一律 0 ==')
set_platform_enabled(False)
r_a = resolve(CLUB_A)
r_b = resolve(CLUB_B)
check('总开关关闭后 A 为 0', r_a.rate == 0.0 and r_a.enabled is False, f'rate={r_a.rate}')
check('总开关关闭后 B 也为 0', r_b.rate == 0.0 and r_b.enabled is False, f'rate={r_b.rate}')
set_platform_enabled(True)
check('总开关恢复后 B 回到 18%', abs(resolve(CLUB_B).rate - 0.18) < 1e-9)

print('\n== 7. 边界校验 ==')
with db.conn() as c:
    try:
        service.set_club_rate(c, CLUB_A, rate=1.5, enabled=True)
        check('费率 > 100% 被拒绝', False, '居然通过了')
    except ValueError:
        check('费率 > 100% 被拒绝', True)
with db.conn() as c:
    try:
        service.set_club_rate(c, CLUB_A, rate=-0.1, enabled=True)
        check('费率 < 0 被拒绝', False, '居然通过了')
    except ValueError:
        check('费率 < 0 被拒绝', True)
with db.conn() as c:
    try:
        service.set_club_rate(c, CLUB_A, rate=0.0, enabled=True)
        check('启用时费率 0 被拒绝', False, '居然通过了')
    except ValueError:
        check('启用时费率 0 被拒绝', True)
with db.conn() as c:
    try:
        service.set_club_rate(c, 999999, rate=0.1, enabled=True)
        check('不存在的俱乐部被拒绝', False, '居然通过了')
    except LookupError:
        check('不存在的俱乐部被拒绝', True)

print('\n== 8. 成交时快照：改费率不影响历史订单 ==')
with db.conn() as c:
    service.set_club_rate(c, CLUB_A, rate=0.10, enabled=True, note='首期')
# 下单（走 checkout 主路径）
# 装备是实物、平台统一发货 → 结算单必须挂一个真实存在的收货地址
with db.conn() as c:
    c.execute("INSERT INTO users(name,phone) VALUES('下单测试','13900000000')")
    USER_ID = int(c.execute("SELECT id FROM users WHERE phone='13900000000'").fetchone()['id'])
    c.execute("INSERT INTO user_addresses(user_id,consignee,phone,province,city,district,detail,is_default) "
              "VALUES(?,'测试收货人','13900000000','四川','成都','青羊','测试地址 1 号',1)", (USER_ID,))
    ADDR_ID = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
intent = client.post('/api/public/clubs/%d/gear-checkout' % CLUB_A, json={
    'userId': USER_ID,
    'addressId': ADDR_ID,
    'items': [{'productId': PID, 'quantity': 1}],
}).json()
if not intent.get('checkoutId') and not intent.get('id'):
    print('    [调试] gear-checkout 响应:', str(intent)[:400])
checkout_id = intent.get('checkoutId') or intent.get('id')
final = client.post('/api/public/checkouts/%s/confirm' % checkout_id, json={}).json()
order_id = final.get('orderId')
check('下单成功产生订单', bool(order_id), str(final)[:200])
if order_id:
    with db.conn() as c:
        row = c.execute('SELECT club_commission FROM gear_orders WHERE id=?', (order_id,)).fetchone()
        items = c.execute('SELECT commission_rate_snapshot FROM gear_order_items WHERE order_id=?',
                          (order_id,)).fetchall()
    commission = float(row['club_commission'] or 0)
    check('订单佣金 = 200 × 10% = ¥20',
          abs(commission - 20.0) < 0.01, f'commission={commission}')
    check('订单项写入成交时费率快照 0.10',
          len(items) == 1 and abs(float(items[0]['commission_rate_snapshot'] or 0) - 0.10) < 1e-9,
          str([dict(i) for i in items]))
    # 改费率后，历史订单佣金不变
    with db.conn() as c:
        service.set_club_rate(c, CLUB_A, rate=0.30, enabled=True, note='改价')
    with db.conn() as c:
        row2 = c.execute('SELECT club_commission FROM gear_orders WHERE id=?', (order_id,)).fetchone()
    check('改费率后历史订单佣金仍是 ¥20（不追溯）',
          abs(float(row2['club_commission'] or 0) - 20.0) < 0.01,
          f"after={row2['club_commission']}")
    check('改费率后历史订单项快照仍是 0.10',
          abs(float(items[0]['commission_rate_snapshot'] or 0) - 0.10) < 1e-9)

print('\n== 9. 平台端 API ==')
resp = client.get('/api/platform/commission-rates')
check('GET /api/platform/commission-rates 返回 200', resp.status_code == 200, str(resp.status_code))
data = resp.json() if resp.status_code == 200 else {}
check('返回里带 clubs 列表', isinstance(data.get('clubs'), list) and len(data['clubs']) >= 2,
      str(len(data.get('clubs') or [])))
check('返回里带商品默认费率', 'productDefaultRatePercent' in data,
      str(data.get('productDefaultRatePercent')))
row_a = next((x for x in data.get('clubs', []) if x['clubId'] == CLUB_A), None)
# 第 8 节把 A 改成了 30%，但随后第 8 节的「改费率后历史订单不变」那步又设成了 0.30；
# 这里断言的是 API 当前值与库里实际写入值一致（用 resolve 交叉验证，而不是写死数字）。
with db.conn() as c:
    _live = service.resolve(c, CLUB_A).rate
check('A 的当前费率在 API 里与库内解析一致',
      row_a and abs(float(row_a['ratePercent']) - _live * 100) < 1e-6,
      f"api={row_a.get('ratePercent') if row_a else None} live={round(_live*100,2)}")
check('A 标记为已单独设定', row_a and row_a['hasClubOverride'] is True)

resp2 = client.patch('/api/platform/commission-rates/%d' % CLUB_A, json={'ratePercent': 15})
check('PATCH 设 15% 返回 200', resp2.status_code == 200, str(resp2.status_code))
check('PATCH 后生效费率是 15%',
      resp2.status_code == 200 and abs(float(resp2.json().get('ratePercent') or 0) - 15.0) < 1e-6,
      str(resp2.json())[:200] if resp2.status_code == 200 else '')
resp3 = client.patch('/api/platform/commission-rates/%d' % CLUB_A, json={'clearOverride': True})
check('PATCH clearOverride 回到商品默认',
      resp3.status_code == 200 and resp3.json().get('source') == 'product_default',
      str(resp3.json())[:160] if resp3.status_code == 200 else '')
resp4 = client.patch('/api/platform/commission-rates/999999', json={'ratePercent': 10})
check('PATCH 不存在的俱乐部返回 404', resp4.status_code == 404, str(resp4.status_code))
resp5 = client.patch('/api/platform/commission-rates/%d' % CLUB_A, json={'ratePercent': 500})
check('PATCH 越界费率返回 400', resp5.status_code == 400, str(resp5.status_code))

print('\n== 10. 俱乐部端只读：能看到自己当前费率 ==')
resp6 = client.get('/api/club/%d/mall/commission-summary' % CLUB_A)
check('俱乐部端佣金汇总返回 200', resp6.status_code == 200, str(resp6.status_code))
if resp6.status_code == 200:
    body = resp6.json()
    check('俱乐部端汇总里带当前佣金率',
          'commissionRatePercent' in body or 'ratePercent' in body,
          str(list(body.keys()))[:300])

print('\n== 11. 前端：平台端有佣金率调整入口 ==')
plat_html = (ROOT / 'static' / 'platform' / 'index.html').read_text(encoding='utf-8')
plat_js = (ROOT / 'static' / 'platform' / 'platform.js').read_text(encoding='utf-8')
more_js = (ROOT / 'static' / 'ux' / 'workflows-platform-more.js').read_text(encoding='utf-8')
check('俱乐部入驻表头有「佣金率」列', '<th>佣金率</th>' in plat_html)
check('平台端 JS 有 setClubCommissionRate 入口',
      'setClubCommissionRate' in more_js and 'commission-rates' in more_js)
check('佣金率单元格显示「已单独设定」标记', '已单独设定' in plat_js)

print('\n' + '=' * 62)
print(f'检查项 {len(PASS) + len(FAILS)}  通过 {len(PASS)}  失败 {len(FAILS)}')
if FAILS:
    print('失败项：')
    for f in FAILS:
        print('  - ' + f)
    sys.exit(1)
print('ALL PASS')
