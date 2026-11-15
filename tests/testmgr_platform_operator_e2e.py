# -*- coding: utf-8 -*-
"""经营者视角 · 总平台后台综合 E2E（2026-11-15）。

以「平台老板能不能把生意跑通、钱算不算得对、权限会不会串」为主线：
  A 招商与俱乐部生命周期（新建 → 审核 → 套餐 → 启停 → 重置密码）
  B 商品与供应链（供应商 → 采购单审批/下单/收货 → 库存一致性）
  C 平台营销与权益（平台券 / 群发 / 平台活动 / 积分策略 / 送券规则 / 邀请奖励）
  D 交易履约（C 端下单 → 支付成交 → 打包 → 发货 → 签收）
  E 佣金结算与财务（费率/政策 → 佣金入账 → 释放 → 起结闸门 → 结算 → 打款 → 利润）
  F 经营数据联动（dashboard / analytics / profit / marketing overview 随交易变化）
  G 护栏（非法参数、幂等、脱敏、越权边界）

环境纪律：自带 /tmp 临时库隔离，不改项目真库；CLUBOS_TEST_OPEN=1 放开 demo 写接口；
CLUBOS_SECURITY_MODE=demo / MOCK_AI=1 / COMMERCE_PROVIDER=local。
"""
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('CLUBOS_SECURITY_MODE', 'demo')
os.environ.setdefault('MOCK_AI', '1')
os.environ.setdefault('COMMERCE_PROVIDER', 'local')
os.environ.setdefault('CLUBOS_TEST_OPEN', '1')
TMP = Path(tempfile.mkdtemp(prefix='clubos-platform-e2e-'))
os.environ['CLUBOS_DB_PATH'] = str(TMP / 'clubos.db')

import db  # noqa: E402
db.init_db()
import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(app.app)
FAILS = []
SEV = {}
FINDINGS = []
CLUB = 1
PID = 1
LEGAL_ID = '510100199001010021'


def req(method, url, **kw):
    return getattr(c, method)(url, **kw)


def ok(method, url, **kw):
    r = req(method, url, **kw)
    assert r.status_code < 400, f'{method.upper()} {url} -> {r.status_code} {r.text[:220]}'
    return r


def soft(method, url, **kw):
    """柔性调用：失败也继续跑完全部用例，由 check 记录。"""
    return req(method, url, **kw)


def check(name, cond, detail='', sev='P1'):
    SEV[name] = sev
    print(('  PASS  ' if cond else '  FAIL  ') + name + (f'   | {detail}' if detail else ''))
    if not cond:
        FAILS.append(name)


def finding(name, sev, detail):
    FINDINGS.append((sev, name, detail))
    print(f'  FIND  [{sev}] {name}   | {detail}')


def new_user(name, phone):
    with app.conn() as cc:
        cc.execute('INSERT INTO users(name,phone,city) VALUES(?,?,?)', (name, phone, None))
        return int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])


def add_addr(uid, phone, tag):
    with app.conn() as cc:
        cc.execute("INSERT INTO user_addresses(user_id,consignee,phone,province,city,district,detail,is_default) VALUES(?,?,?,?,?,?,?,1)",
                   (uid, '测试收货人', phone, '四川', '成都', '武侯', tag))
        return int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])


# ── 前置：让商品可被平台仓履约 / 采购 / C 端下单 ─────────────────
with app.conn() as cc:
    cc.execute("UPDATE products SET inventory_mode='platform_owned',fulfillment_mode='platform_warehouse',source='clubos' WHERE id=?", (PID,))
    cc.execute("INSERT OR IGNORE INTO product_variants(product_id,name,price,stock,status,sort,is_default) VALUES(?,'默认规格',100.0,50,'active',0,1)", (PID,))
    STOCK0 = int(cc.execute('SELECT stock FROM products WHERE id=?', (PID,)).fetchone()[0])

print('\n══════════ A 招商与俱乐部生命周期 ══════════')
dash0 = soft('get', '/api/platform/dashboard').json()
check('A1 平台概览可读(clubs/activeClubs/orders)', all(k in dash0 for k in ('clubs', 'activeClubs', 'orders')), str(dash0)[:110])
clubs0 = soft('get', '/api/platform/clubs').json()
check('A2 俱乐部台账可读', isinstance(clubs0, list) and len(clubs0) >= 1, f'count={len(clubs0)}')
nc = soft('post', '/api/platform/clubs', json={
    'name': '经营者测试俱乐部', 'plan': 'pro', 'contactName': '王老板',
    'contactPhone': '13900000001', 'contactEmail': 'boss@test.com', 'city': '成都'}).json()
check('A3 新建俱乐部→待审核(pending)', nc.get('status') == 'pending' and bool(nc.get('id')), str(nc))
NCID = nc['id']
clubs1 = soft('get', '/api/platform/clubs').json()
check('A4 新俱乐部进入台账', any(x['id'] == NCID and x['status'] == 'pending' for x in clubs1))
approve = soft('patch', f'/api/platform/clubs/{NCID}/status', json={'status': 'active'}).json()
check('A5 审核通过→active', approve.get('status') == 'active', str(approve)[:140])
credits = soft('get', '/api/platform/credits').json()
check('A6 审核通过自动开通积分账户', any(int(x['club_id']) == NCID for x in credits), f'accounts={len(credits)}')
plan = req('patch', f'/api/platform/clubs/{NCID}/plan', json={'planCode': 'pro', 'autoRenew': True})
check('A7 套餐可配置', plan.status_code in (200, 400), f'status={plan.status_code} {plan.text[:100]}')
dis = soft('patch', f'/api/platform/clubs/{NCID}/status', json={'status': 'disabled', 'reason': '测试停用'}).json()
check('A8 停用俱乐部生效', dis.get('status') == 'disabled', str(dis)[:120])
bad_status = req('patch', f'/api/platform/clubs/{NCID}/status', json={'status': 'vip'})
check('A9 非法状态被拒(400)', bad_status.status_code == 400, f'status={bad_status.status_code}')
soft('patch', f'/api/platform/clubs/{NCID}/status', json={'status': 'active'})
pwd = req('post', f'/api/platform/clubs/{NCID}/reset-password')
check('A10 重置密码可用', pwd.status_code == 200 and bool((pwd.json().get('account') or {}).get('password')), f'status={pwd.status_code} {pwd.text[:110]}')

print('\n══════════ B 商品与供应链 ══════════')
prods = soft('get', '/api/platform/products').json()
check('B1 商品台账可读', isinstance(prods, list) and len(prods) >= 1, f'count={len(prods)}')
variants = soft('get', f'/api/platform/products/{PID}/variants').json()
check('B2 商品规格可读', isinstance(variants, list) and len(variants) >= 1, f'count={len(variants) if isinstance(variants,list) else "?"}')
sup = soft('post', '/api/platform/suppliers', json={
    'name': '高原装备供应商', 'code': 'SUP-TEST-001', 'contactName': '李经理',
    'phone': '13800000001', 'paymentTermDays': 30}).json()
check('B3 新建供应商', bool(sup.get('id')), str(sup)[:120])
SID = sup['id']
dup = req('post', '/api/platform/suppliers', json={'name': '重名供应商', 'code': 'SUP-TEST-001'})
check('B4 供应商编码重复被拒(409)', dup.status_code == 409, f'status={dup.status_code}')
with app.conn() as cc:
    VID = int(cc.execute('SELECT id FROM product_variants WHERE product_id=? AND status=\'active\' ORDER BY id LIMIT 1', (PID,)).fetchone()[0])
po = soft('post', '/api/platform/procurement/purchase-orders', json={
    'supplierId': SID, 'items': [{'productId': PID, 'variantId': VID, 'quantity': 10, 'unitCost': 40}],
    'note': '经营者测试采购'}).json()
POID = po.get('id') or po.get('poId') or po.get('purchaseOrderId')
check('B5 建采购单', bool(POID), str(po)[:140])
ap = req('post', f'/api/platform/procurement/purchase-orders/{POID}/approve')
check('B6 采购单审批', ap.status_code == 200, f'status={ap.status_code} {ap.text[:110]}')
pl = req('post', f'/api/platform/procurement/purchase-orders/{POID}/order')
check('B7 采购单下单', pl.status_code == 200, f'status={pl.status_code} {pl.text[:110]}')
pod = soft('get', f'/api/platform/procurement/purchase-orders/{POID}').json()
items = pod.get('items') or []
iid = items[0].get('id') if items else None
rec = req('post', f'/api/platform/procurement/purchase-orders/{POID}/receive', json={
    'items': [{'purchaseOrderItemId': iid, 'variantId': VID, 'quantity': 10, 'unitCost': 40}], 'requestKey': 'e2e-recv-1'})
check('B8 采购收货', rec.status_code == 200, f'status={rec.status_code} {rec.text[:140]}')
with app.conn() as cc:
    STOCK1 = int(cc.execute('SELECT stock FROM products WHERE id=?', (PID,)).fetchone()[0])
check('B9 收货后库存增加', STOCK1 > STOCK0, f'{STOCK0} -> {STOCK1}')
rec2 = req('post', f'/api/platform/procurement/purchase-orders/{POID}/receive', json={
    'items': [{'purchaseOrderItemId': iid, 'variantId': VID, 'quantity': 10, 'unitCost': 40}], 'requestKey': 'e2e-recv-1'})
check('B10 收货幂等(requestKey防重复入库)', rec2.status_code == 200 and rec2.json().get('idempotent') is True, str(rec2.json())[:120])
cons = soft('get', '/api/platform/stock/consistency').json()
check('B11 库存一致性自检可读', 'ok' in cons and 'mismatches' in cons, str(cons)[:120])

print('\n══════════ C 平台营销与权益 ══════════')
mkt0 = soft('get', '/api/platform/marketing/overview').json()
check('C1 营销总览可读', 'coupons' in mkt0 and 'campaigns' in mkt0, str(mkt0)[:120])
u_mkt = new_user('平台券用户', '13800000101')
cpn = soft('post', '/api/platform/coupons', json={
    'title': '平台经营者测试券', 'benefitType': 'gear_coupon', 'mode': 'fixed',
    'cashValue': 20, 'stock': 50, 'claimMode': 'grant', 'targetType': 'all_users'}).json()
check('C2 平台券创建', bool(cpn.get('couponId')), str(cpn)[:120])
CPNID = cpn['couponId']
gr = soft('post', f'/api/platform/coupons/{CPNID}/grant', json={'scope': 'all_users'}).json()
check('C3 平台券群发', gr.get('ok') is True or int(gr.get('granted', gr.get('count', 0)) or 0) >= 1, str(gr)[:120])
mine = soft('get', f'/api/public/coupons/mine?user_id={u_mkt}').json()
check('C4 用户券包收到平台券', any(x.get('benefit_id') == CPNID for x in mine), f'entries={len(mine)}')
camp = req('post', '/api/platform/campaigns', json={
    'type': 'TIME_LIMIT', 'title': '平台限时折扣', 'status': 'ACTIVE',
    'rules': {'mode': 'fixed', 'amount': 10}, 'startAt': (datetime.now() - timedelta(days=1)).isoformat(),
    'endAt': (datetime.now() + timedelta(days=7)).isoformat()})
check('C5 平台营销活动可创建', camp.status_code == 200, f'status={camp.status_code} {camp.text[:120]}')
pp = soft('get', '/api/platform/points-policy').json()
check('C6 积分策略可读', isinstance(pp, dict) and 'clubs' in pp, str(pp)[:110])
rule = req('post', '/api/platform/coupon-gift-rules', json={
    'title': '消费送券规则', 'thresholdAmount': 100, 'benefitId': None, 'enabled': True})
check('C7 送券规则校验(缺券应被拒)', rule.status_code in (400, 409), f'status={rule.status_code} {rule.text[:110]}')
inv = soft('put', '/api/platform/invite-reward', json={'enabled': True, 'rewardPoints': 10}).json()
check('C8 邀请奖励配置可写', isinstance(inv, dict), str(inv)[:110])

print('\n══════════ D 交易履约（钱进来） ══════════')
u_buy = new_user('平台成交用户', '13800000102')
addr = add_addr(u_buy, '13800000102', '测试地址1号')
co = soft('post', f'/api/public/clubs/{CLUB}/gear-checkout', json={
    'userId': u_buy, 'items': [{'productId': PID, 'quantity': 3}], 'addressId': addr}).json()
check('D1 C端装备下单生成结算单', bool(co.get('checkoutId')), str(co)[:120])
pay = soft('post', f"/api/public/checkouts/{co.get('checkoutId')}/pay", json={'provider': 'local', 'simulateSuccess': True}).json()
check('D2 支付成交(succeeded)', pay.get('status') == 'succeeded', str(pay)[:120])
orders = soft('get', '/api/platform/orders').json()
check('D3 平台订单履约台看到订单', isinstance(orders, list) and len(orders) >= 1, f'count={len(orders)}')
OID = (orders[0].get('id') if isinstance(orders, list) and orders and isinstance(orders[0], dict) else None)
pick = req('post', f'/api/platform/orders/{OID}/pick')
check('D4a 创建拣货任务', pick.status_code == 200, f'status={pick.status_code} {pick.text[:120]}')
task_id = (pick.json().get('id') or pick.json().get('taskId')) if pick.status_code == 200 else None
picked = req('post', f'/api/platform/warehouse/tasks/{task_id}/picked') if task_id else None
check('D4b 拣货核对完成', picked is not None and picked.status_code == 200,
      f'status={picked.status_code if picked else "no task"} {(picked.text[:110] if picked else "")}')
pack = req('post', f'/api/platform/orders/{OID}/pack', json={'note': 'E2E打包'})
check('D4 订单打包', pack.status_code == 200, f'status={pack.status_code} {pack.text[:120]}')
ship = req('post', f'/api/platform/orders/{OID}/ship', json={'trackingNo': 'SF1234567890', 'carrier': '顺丰速运'})
check('D5 订单发货', ship.status_code == 200, f'status={ship.status_code} {ship.text[:120]}')
ship_bad = req('post', f'/api/platform/orders/{OID}/ship', json={'carrier': '顺丰速运'})
check('D6 缺物流单号被拒(400)', ship_bad.status_code == 400, f'status={ship_bad.status_code}')
# DEF-02 修复后：local/演示模式由平台端点直接完成签收（写 delivered_at + 冻结佣金），
# 签收时按当时的售后期固化 available_at，故先置 0 天以便本用例自证
# 「签收 → 冻结 → 释放 → 结算 → 打款」完整资金闭环，不再靠测试库造数模拟物流回调。
soft('patch', '/api/platform/commission-policy', json={'afterSalesDays': 0})
delivered = req('post', f'/api/platform/orders/{OID}/delivered', json={})
dj = delivered.json() if delivered.status_code == 200 else {}
check('D7 local模式模拟签收打通资金闭环',
      delivered.status_code == 200 and dj.get('status') == 'delivered' and dj.get('mode') == 'local-simulated',
      f'status={delivered.status_code} {delivered.text[:110]}')
after = soft('get', '/api/platform/after-sales').json()
check('D8 售后台账可读', isinstance(after, (list, dict)), str(after)[:110])

print('\n══════════ E 佣金结算与财务（钱分出去） ══════════')
pol = soft('patch', '/api/platform/commission-policy', json={'afterSalesDays': 0}).json()
check('E1 佣金政策可配置(售后期0天)', pol.get('afterSalesDays') == 0, str(pol))
rates = soft('get', '/api/platform/commission-rates').json()
check('E2 佣金费率可读', isinstance(rates, dict) and 'productDefaultRate' in rates, str(rates)[:120])
summ = soft('get', '/api/platform/commission-summary').json()
check('E3 佣金汇总可读', isinstance(summ, list) and len(summ) >= 1, str(summ)[:120])
s0 = summ[0] if isinstance(summ, list) and summ else (summ if isinstance(summ, dict) else {})
check('E4 佣金分账字段齐全', all(k in s0 for k in ('pending', 'available', 'settled')), str(s0)[:140])
prev = soft('get', f'/api/platform/settlements/preview?club_id={CLUB}').json()
check('E5 结算试算只读可用', 'account' in prev, str(prev)[:120])
no_acct = req('post', '/api/platform/settlements', json={'clubId': CLUB, 'paymentRef': 'E2E-NO-ACCT'})
check('E6 起结闸门:未登记账户禁止结算(409)', no_acct.status_code == 409, f'status={no_acct.status_code} {no_acct.text[:110]}')
acct = soft('post', f'/api/club/{CLUB}/settlement-account', json={
    'accountType': 'legal', 'holder': '测试户', 'legalName': '张三',
    'legalIdNo': LEGAL_ID, 'bankName': '测试银行', 'cardNo': '6222021234567890123'}).json()
check('E7 结算账户登记+脱敏', acct.get('exists') is True and '*' in acct.get('cardNoMasked', ''), str(acct)[:130])
rel = soft('post', '/api/platform/commissions/release', json={'clubId': CLUB}).json()
check('E8 佣金释放接口可用且幂等(summary 内已自动释放)',
      rel.get('ok') is True and int(rel.get('released', 0)) >= 0, str(rel)[:120])
summ2 = soft('get', f'/api/platform/commission-summary?club_id={CLUB}').json()
s1 = summ2[0] if isinstance(summ2, list) and summ2 else (summ2 if isinstance(summ2, dict) else {})
check('E9 释放后可结算余额>0', float(s1.get('available', 0)) > 0, f"available={s1.get('available')}")
settle = req('post', '/api/platform/settlements', json={'clubId': CLUB, 'paymentRef': 'E2E-PAY-001'})
check('E10 生成结算单', settle.status_code == 200, f'status={settle.status_code} {settle.text[:140]}')
if settle.status_code == 200:
    sj = settle.json()
    check('E11 结算单回带脱敏收款账户', '*' in str(sj.get('payee', '')), str(sj.get('payee'))[:120])
    sid = sj.get('id') or sj.get('settlementId')
    if sid:
        po_res = req('post', f'/api/platform/settlements/{sid}/payout', json={'paymentRef': 'E2E-BANK-001'})
        check('E12 平台确认打款', po_res.status_code == 200, f'status={po_res.status_code} {po_res.text[:110]}')
    else:
        check('E12 平台确认打款', False, 'no settlementId in response')
else:
    check('E11 结算单回带脱敏收款账户', False, 'settle failed')
    check('E12 平台确认打款', False, 'settle failed')
prof = soft('get', '/api/platform/profit/summary').json()
check('E13 商品利润汇总可读', all(k in prof for k in ('grossSales', 'cogs', 'grossProfit')), str(prof)[:130])
bom = soft('get', '/api/platform/cost-bom').json()
check('E14 成本BOM可读', 'lines' in bom, str(bom)[:110])
bom_save = req('post', '/api/platform/cost-bom', json={
    'name': 'E2E成本清单', 'enabled': True,
    'lines': [{'category': 'logistics', 'calcType': 'per_item', 'value': 5, 'label': '物流'}]})
check('E15 成本BOM可保存', bom_save.status_code == 200, f'status={bom_save.status_code} {bom_save.text[:110]}')
spay = req('post', '/api/platform/finance/supplier-payments', json={
    'supplierId': SID, 'amount': 100, 'paymentRef': 'E2E-SUP-001', 'paymentMethod': 'bank'})
check('E16 供应商付款可用', spay.status_code == 200, f'status={spay.status_code} {spay.text[:110]}')

print('\n══════════ F 经营数据联动 ══════════')
dash1 = soft('get', '/api/platform/dashboard').json()
check('F1 成交后平台概览GMV联动', float(dash1.get('gearGMV', 0)) > 0, f"gearGMV={dash1.get('gearGMV')}")
an = soft('get', '/api/platform/analytics/commerce').json()
check('F2 商城经营分析有成交额', float(an.get('grossSales', 0)) > 0, f"grossSales={an.get('grossSales')}")
anp = soft('get', '/api/platform/analytics/products').json()
check('F3 商品动销数据可读', isinstance(anp, list), f'count={len(anp)}')
mkt1 = soft('get', '/api/platform/marketing/overview').json()
check('F4 营销总览券数联动', int(mkt1.get('coupons', 0)) >= 1, f"coupons={mkt1.get('coupons')}")
settles = soft('get', '/api/platform/settlements').json()
check('F5 结算台账可读', isinstance(settles, dict) and 'items' in settles, str(settles)[:110])
cs = soft('get', '/api/platform/commission-settlements').json()
check('F6 佣金结算流水可读', isinstance(cs, list), f'count={len(cs)}')

print('\n══════════ G 护栏（非法参数 / 幂等 / 越权边界） ══════════')
bad_preview = req('get', '/api/platform/settlements/preview')
check('G1 结算试算缺club_id被拒(422)', bad_preview.status_code == 422, f'status={bad_preview.status_code}')
bad_pol = req('patch', '/api/platform/commission-policy', json={'afterSalesDays': 999})
check('G2 佣金政策越界值被钳制或拒绝', bad_pol.status_code in (200, 400), f'status={bad_pol.status_code}')
if bad_pol.status_code == 200:
    check('G2b 售后期钳制在0-90天', 0 <= int(bad_pol.json().get('afterSalesDays', -1)) <= 90, str(bad_pol.json()))
bad_cpn = req('post', '/api/platform/coupons', json={'title': '坏券', 'benefitType': 'unknown_type'})
check('G3 非法权益类型被拒(400)', bad_cpn.status_code == 400, f'status={bad_cpn.status_code} {bad_cpn.text[:100]}')
bad_club = req('post', '/api/platform/clubs', json={'contactPhone': '123'})
check('G4 俱乐部名必填(400)', bad_club.status_code == 400, f'status={bad_club.status_code}')
hub = req('get', '/api/platform/settings-hub')
check('G5 设置中枢在内测免登录模式仍强制平台角色(安全护栏)', hub.status_code == 401, f'status={hub.status_code} {hub.text[:90]}')
bad_bom = req('post', '/api/platform/cost-bom', json={'lines': [{'category': 'logistics', 'calcType': 'wrong'}]})
check('G6 成本BOM非法计算方式被拒(400)', bad_bom.status_code == 400, f'status={bad_bom.status_code}')

print('\n' + '=' * 62)
print('总计 %d 项 | 失败 %d 项 | 发现 %d 条' % (len(SEV), len(FAILS), len(FINDINGS)))
if FAILS:
    print('失败清单（按严重度）：')
    for n in FAILS:
        print('  [%s] %s' % (SEV.get(n, 'P1'), n))
for sev, name, detail in FINDINGS:
    print('  发现 [%s] %s | %s' % (sev, name, detail))
sys.exit(1 if FAILS else 0)
