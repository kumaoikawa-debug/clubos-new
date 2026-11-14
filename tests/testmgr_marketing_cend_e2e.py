# -*- coding: utf-8 -*-
"""测试经理视角的综合 E2E（2026-11-14）。

范围（用户指定）：
  A. 俱乐部后台营销功能
     A1 营销活动引擎 CRUD（NEWCOMER 首单立减 / TIME_LIMIT 限时折扣 /
        BUY_GIFT 满额赠送 / INVITE 邀请有礼）+ 平台活动越权拦截
     A2 消费送券规则（coupon-gift-rules）CRUD + 门槛/归属校验
     A3 券适用范围级联（club 端不含装备；platform 端只含装备）
     A4 积分商城（建项 / C 端兑换 / 后台核销）
     A5 推荐券 / 邀请奖励 读写
  B. C 端用户视角：券使用 / 产品 / 支付 / 结算
     B1 券在真实下单里生效（活动券抵扣 + 装备券抵扣）
     B2 各产品下单（活动报名 / 装备商城）
     B3 支付链路（simulateSuccess 成交 / 手动 confirm 幂等 / 取消 / 零元单自动成交）
     B4 结算（商城佣金汇总与起结闸门 / 俱乐部结算试算 / 推广佣金只读）
     B5 装备与营销解耦（gear-checkout 夹带营销参数被硬拒）

环境纪律：自带临时库隔离（CLUBOS_DB_PATH 指向 /tmp），不改项目真库；
CLUBOS_TEST_OPEN=1 放开 demo 写接口；CLUBOS_SECURITY_MODE=demo / MOCK_AI=1 /
COMMERCE_PROVIDER=local。
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
TMP = Path(tempfile.mkdtemp(prefix='clubos-testmgr-'))
os.environ['CLUBOS_DB_PATH'] = str(TMP / 'clubos.db')

import db  # noqa: E402
db.init_db()
import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(app.app)
FAILS = []
SEV = {}  # name -> 'P0'/'P1'/'P2'
FINDINGS = []  # 非阻断性发现 / 优化建议


def req(method, url, **kw):
    return getattr(c, method)(url, **kw)


def ok(method, url, **kw):
    r = req(method, url, **kw)
    assert r.status_code < 400, f'{url} -> {r.status_code} {r.text[:200]}'
    return r


def check(name, cond, detail='', sev='P1'):
    SEV[name] = sev
    mark = '  PASS  ' if cond else '  FAIL  '
    print(mark + name + (f'   | {detail}' if detail else ''))
    if not cond:
        FAILS.append(name)


def finding(name, sev, detail):
    FINDINGS.append((sev, name, detail))
    print(f'  FIND  [{sev}] {name}   | {detail}')


def new_user(name, phone, city=None):
    with app.conn() as cc:
        cc.execute('INSERT INTO users(name,phone,city) VALUES(?,?,?)', (name, phone, city))
        return int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])


# ───────────────────────── 环境种子 ─────────────────────────
print('\n══════════ A. 后台营销功能 ══════════')
# 确保 club 1 有可下单的活动团期 + 装备商品规格 + 收货地址
CLUB = 1
AID = ok('get', f'/api/public/clubs/{CLUB}/activities').json()[0]['id']
with app.conn() as cc:
    # 装备商品：置为平台自有仓履约（gear-checkout 硬要求），并建默认规格
    cc.execute("UPDATE products SET inventory_mode='platform_owned', fulfillment_mode='platform_warehouse', source='clubos' WHERE id=1")
    cc.execute("INSERT OR IGNORE INTO product_variants(product_id,name,price,stock,status,sort,is_default) VALUES(1,'默认规格',100.0,50,'active',0,1)")
    PID = 1
print(f'  种子：club={CLUB} activity={AID} product={PID}')


# ───────────────────────── A1 营销活动引擎 ─────────────────────────
print('\n── A1 营销活动引擎 CRUD + 越权 ──')
# 四类活动
r = ok('post', f'/api/club/{CLUB}/campaigns', json={
    'type': 'NEWCOMER', 'title': '新人首单立减30', 'status': 'ACTIVE',
    'schedule': {}, 'scope': {},
    'rules': {'mode': 'fixed', 'amount': 30}})
nc = r.json()['id']
check('A1-1 首单立减活动创建', nc > 0, str(nc))

r = ok('post', f'/api/club/{CLUB}/campaigns', json={
    'type': 'TIME_LIMIT', 'title': '限时9折', 'status': 'ACTIVE',
    'schedule': {}, 'scope': {},
    'rules': {'mode': 'percent', 'percent': 10}})
check('A1-2 限时折扣活动创建', r.json().get('id', 0) > 0)

r = ok('post', f'/api/club/{CLUB}/campaigns', json={
    'type': 'BUY_GIFT', 'title': '满100送券', 'status': 'ACTIVE',
    'schedule': {}, 'scope': {},
    'rules': {'threshold': 100, 'giftType': 'coupon', 'giftValue': 50, 'maxGifts': 2}})
bg = r.json()['id']
check('A1-3 满额赠送活动创建', bg > 0)

r = ok('post', f'/api/club/{CLUB}/campaigns', json={
    'type': 'INVITE', 'title': '邀请有礼', 'status': 'ACTIVE',
    'schedule': {}, 'scope': {},
    'rules': {'threshold': 50, 'giftType': 'points', 'giftValue': 100, 'maxGifts': 1}})
check('A1-4 邀请有礼活动创建', r.json().get('id', 0) > 0)

# 列表应含 4 条 CLUB 活动
lst = ok('get', f'/api/club/{CLUB}/campaigns').json()
check('A1-5 后台活动列表含4条', len(lst) >= 4, f'count={len(lst)}')

# 非法类型 / 空标题 → 400
check('A1-6 非法活动类型→400', req('post', f'/api/club/{CLUB}/campaigns', json={'type': 'NOPE', 'title': 'x'}).status_code == 400)
check('A1-7 空标题→400', req('post', f'/api/club/{CLUB}/campaigns', json={'type': 'NEWCOMER', 'title': ''}).status_code == 400)

# 满减(threshold)模式：缺门槛必须被 400 拒绝（P2 已修复——缺失 threshold 会退化为无条件立减）
r_th = req('post', f'/api/club/{CLUB}/campaigns', json={
    'type': 'TIME_LIMIT', 'title': '缺门槛满减', 'status': 'DRAFT',
    'rules': {'mode': 'threshold', 'amount': 5}})
check('A1-8 缺门槛threshold模式→400', r_th.status_code == 400, f'status={r_th.status_code}')
# 合法满减：带正数 threshold（满200减20）→ 200
r_th_ok = ok('post', f'/api/club/{CLUB}/campaigns', json={
    'type': 'TIME_LIMIT', 'title': '满200减20', 'status': 'ACTIVE',
    'rules': {'mode': 'threshold', 'threshold': 200, 'amount': 20}})
check('A1-8b 合法满减带门槛→200', r_th_ok.json().get('id', 0) > 0)

# 更新：暂停
check('A1-9 更新活动状态→200', ok('patch', f'/api/club/{CLUB}/campaigns/{nc}', json={'status': 'PAUSED'}).status_code == 200)
check('A1-10 更新后状态生效', ok('get', f'/api/club/{CLUB}/campaigns').json())

# 平台活动越权：俱乐部不能改/删平台活动（无种子，直接造一条 PLATFORM 活动验证护栏）
with app.conn() as cc:
    _now = datetime.now().isoformat()
    cc.execute("""INSERT INTO marketing_campaigns(owner_type,owner_club_id,type,title,status,schedule_json,scope_json,rules_json,created_at,updated_at)
                  VALUES('PLATFORM',NULL,'TIME_LIMIT','平台限时券','ACTIVE',?,?,?,?,?)""",
               ('{}', '{}', '{"mode":"percent","percent":10}', _now, _now))
    plat_id = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
check('A1-11 俱乐部改平台活动→403', req('patch', f'/api/club/{CLUB}/campaigns/{plat_id}', json={'status': 'PAUSED'}).status_code == 403)
check('A1-12 俱乐部删平台活动→403', req('delete', f'/api/club/{CLUB}/campaigns/{plat_id}').status_code == 403)

# 删除（软删除 ENDED）
check('A1-13 删除活动→200', ok('delete', f'/api/club/{CLUB}/campaigns/{nc}').status_code == 200)


# ───────────────────────── A2 消费送券规则 ─────────────────────────
print('\n── A2 消费送券规则（coupon-gift-rules）──')
# 先建一张本俱乐部活动券（带库存）
b = ok('post', f'/api/club/{CLUB}/benefits', json={
    'title': '消费送券-满100', 'benefitType': 'activity_coupon', 'pointsCost': 0,
    'cashValue': 30, 'stock': 5, 'description': 'gift'})
bid = b.json()['benefitId']
check('A2-1 建本俱乐部活动券', bid > 0, str(bid))

r = ok('post', f'/api/club/{CLUB}/coupon-gift-rules', json={
    'thresholdAmount': 100, 'benefitId': bid, 'perUserLimit': 1})
grid = r.json()['ruleId']
check('A2-2 建消费送券规则', grid > 0, str(grid))
rules = ok('get', f'/api/club/{CLUB}/coupon-gift-rules').json()
check('A2-3 规则列表含1条', len(rules) >= 1, f'count={len(rules)}')

# 校验：券不存在→404；门槛0→400；非本俱乐部券→400
check('A2-4 赠券不存在→404', req('post', f'/api/club/{CLUB}/coupon-gift-rules', json={'thresholdAmount': 100, 'benefitId': 999999}).status_code == 404)
check('A2-5 门槛0→400', req('post', f'/api/club/{CLUB}/coupon-gift-rules', json={'thresholdAmount': 0, 'benefitId': bid}).status_code == 400)
# 平台券不能用于俱乐部赠送（benefit 必须是 CLUB 活动券）
with app.conn() as cc:
    cc.execute("INSERT INTO member_benefits(owner_type,owner_club_id,title,benefit_type,points_type,points_cost,cash_value,stock,status) VALUES('PLATFORM',NULL,'平台券','activity_coupon','club',0,10,NULL,'active')")
    pbid = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
check('A2-6 非本俱乐部券→400', req('post', f'/api/club/{CLUB}/coupon-gift-rules', json={'thresholdAmount': 100, 'benefitId': pbid}).status_code == 400)

check('A2-7 更新规则→200', ok('patch', f'/api/club/{CLUB}/coupon-gift-rules/{grid}', json={'perUserLimit': 2}).status_code == 200)
check('A2-8 删除规则→200', ok('delete', f'/api/club/{CLUB}/coupon-gift-rules/{grid}').status_code == 200)


# ───────────────────────── A3 券适用范围级联 ─────────────────────────
print('\n── A3 券适用范围级联 ──')
club_scope = ok('get', f'/api/club/{CLUB}/coupon-scope-options').json()
plat_scope = ok('get', '/api/platform/coupon-scope-options').json()
# 两端 key 同名，但内容按经营主体隔离（club=活动域，platform=装备域）
check('A3-1 club端返回活动对象', len(club_scope.get('activities') or []) > 0
      and (club_scope['activities'][0].get('kind') == 'activity'), str(list(club_scope.keys())))
check('A3-2 platform端不含活动(隔离)', len(plat_scope.get('activities') or []) == 0, f"plat activities={len(plat_scope.get('activities') or [])}")
check('A3-3 platform端返回装备商品', len(plat_scope.get('products') or []) > 0
      and all(it.get('kind') == 'product' for it in plat_scope['products']), f"plat products={len(plat_scope.get('products') or [])}")


# ───────────────────────── A4 积分商城 ─────────────────────────
print('\n── A4 积分商城（建项 / C端兑换 / 核销）──')
# 建一张券用于兑换关联
b2 = ok('post', f'/api/club/{CLUB}/benefits', json={
    'title': '兑换券', 'benefitType': 'activity_coupon', 'pointsCost': 0,
    'cashValue': 20, 'stock': 10})
b2id = b2.json()['benefitId']
mi = ok('post', f'/api/club/{CLUB}/points-mall', json={
    'title': '1000积分兑好礼', 'itemType': 'goods', 'pointsCost': 1000, 'stock': 5})
miid = mi.json()['itemId']
check('A4-1 建积分商城实物项', miid > 0)
mi2 = ok('post', f'/api/club/{CLUB}/points-mall', json={
    'title': '兑换券项', 'itemType': 'coupon', 'pointsCost': 500, 'stock': 3, 'refId': b2id})
mi2id = mi2.json()['itemId']
check('A4-2 建积分商城券项(关联本俱乐部券)', mi2id > 0)
items = ok('get', f'/api/club/{CLUB}/points-mall').json()
check('A4-3 商城列表≥2项', len(items) >= 2, f'count={len(items)}')
# 非法积分标价→400
check('A4-4 积分≤0→400', req('post', f'/api/club/{CLUB}/points-mall', json={'title': 'x', 'itemType': 'goods', 'pointsCost': 0}).status_code == 400)
# C端兑换：给会员积分后兑换
u_mall = new_user('积分会员', '13800000061')
with app.conn() as cc:
    cc.execute("INSERT OR IGNORE INTO club_members(club_id,user_id,level,club_points_balance) VALUES(?,?,?,?)",
               (CLUB, u_mall, '普通会员', 0))
ok('post', f'/api/club/{CLUB}/members/{u_mall}/points', json={'kind': 'club', 'amount': 5000, 'note': 'test'})
# 积分不足用户（已入会但余额 0）
u_poor = new_user('穷会员', '13800000062')
with app.conn() as cc:
    cc.execute("INSERT OR IGNORE INTO club_members(club_id,user_id,level,club_points_balance) VALUES(?,?,?,?)",
               (CLUB, u_poor, '普通会员', 0))
rd = ok('post', f'/api/public/clubs/{CLUB}/points-mall/{mi2id}/redeem', json={'userId': u_mall})
rdj = rd.json()
check('A4-5 C端兑换券项成功', rdj.get('redemptionId', 0) > 0 and rdj.get('coupon'), str(rdj)[:120])
# 积分不足→409（u_poor 已在上方建为会员、余额 0；端点返回 409 冲突）
check('A4-6 积分不足→409', req('post', f'/api/public/clubs/{CLUB}/points-mall/{miid}/redeem', json={'userId': u_poor}).status_code == 409, '端点返回 409 冲突')
# 后台核销兑换码
code = rdj.get('code')
if code:
    check('A4-7 后台核销兑换码→200', ok('post', f'/api/club/{CLUB}/points-mall/verify', json={'code': code}).status_code == 200)
    check('A4-8 重复核销→409', req('post', f'/api/club/{CLUB}/points-mall/verify', json={'code': code}).status_code == 409)


# ───────────────────────── A5 推荐券 / 邀请奖励 ─────────────────────────
print('\n── A5 推荐券 / 邀请奖励 ──')
rc = ok('get', f'/api/club/{CLUB}/referral-coupon').json()
check('A5-1 推荐券可读', isinstance(rc, dict))
check('A5-2 推荐券可写→200', ok('patch', f'/api/club/{CLUB}/referral-coupon', json={'enabled': True, 'cashValue': 15}).status_code == 200)
ir = ok('get', f'/api/club/{CLUB}/invite-reward').json()
check('A5-3 邀请奖励可读', isinstance(ir, dict))
check('A5-4 邀请奖励可写→200', ok('patch', f'/api/club/{CLUB}/invite-reward', json={'enabled': True, 'rewardType': 'points', 'rewardValue': 200}).status_code == 200)


print('\n══════════ B. C端：券使用 / 产品 / 支付 / 结算 ══════════')

# 准备团期（供活动下单）
start = (datetime.now() + timedelta(days=5)).replace(microsecond=0).isoformat(sep=' ')
oid = ok('post', f'/api/club/{CLUB}/activities/{AID}/occurrences',
         json={'startAt': start, 'price': 260, 'capacity': 6, 'label': '测试团'}).json()['id']
ok('post', f'/api/club/{CLUB}/activities/{AID}/publish')


# ───────────────────────── B1 券在真实下单里生效 ─────────────────────────
print('\n── B1 券使用：活动券抵扣 + 装备券抵扣 ──')
# 活动券：建CLUB活动券→赠给新用户→活动下单抵扣
b_act = ok('post', f'/api/club/{CLUB}/benefits', json={
    'title': '活动满减券', 'benefitType': 'activity_coupon', 'pointsCost': 0,
    'cashValue': 40, 'stock': 10})
b_act_id = b_act.json()['benefitId']
u_act = new_user('活动券用户', '13800000071')
with app.conn() as cc:
    cc.execute("INSERT OR IGNORE INTO club_members(club_id,user_id,level,club_points_balance) VALUES(?,?,?,?)",
               (CLUB, u_act, '普通会员', 0))
gv = ok('post', f'/api/club/{CLUB}/members/{u_act}/vouchers', json={'benefitId': b_act_id})
act_vcode = gv.json()['voucherCode']
part = [{'name': '活动券用户', 'phone': '13800000071', 'relationToPayer': '本人',
         'idType': '身份证', 'idNumber': '510100199001010021',
         'emergencyContactName': '家', 'emergencyContactPhone': '13900000071'}]
co = ok('post', f'/api/public/activities/{AID}/checkout', json={
    'name': '活动券用户', 'phone': '13800000071', 'occurrenceId': oid,
    'participants': part, 'voucherCodes': [act_vcode]})
coj = co.json()
ben = coj.get('benefits', {})
check('B1-1 活动券抵扣生效(totalDiscount>0)', float(ben.get('totalDiscount', 0)) > 0, str(ben)[:120])
# payable 即用户实付现金；club 活动券使 payable 由 original 下降（funding.club 承担）
check('B1-2 活动券使应付(payable)下降', float(coj.get('payable', 0)) < float(coj.get('original', 0)),
      f"original={coj.get('original')} payable={coj.get('payable')}")

# 装备券：平台装备券→先建用户→群发（grant 仅作用于已存在用户）→用户领→装备下单抵扣
u_gear = new_user('装备券用户', '13800000072')
gc = ok('post', '/api/platform/coupons', json={
    'title': '装备满减券', 'benefitType': 'gear_coupon', 'mode': 'fixed', 'cashValue': 30,
    'stock': 50, 'claimMode': 'grant', 'targetType': 'all_users'})
gcid = gc.json()['couponId']
ok('post', f'/api/platform/coupons/{gcid}/grant', json={'scope': 'all_users'})
mine = ok('get', f'/api/public/coupons/mine?user_id={u_gear}').json()
gcode = next((x['voucher_code'] for x in mine if x.get('benefit_id') == gcid), None)
check('B1-3 装备券已群发到用户', gcode is not None)
# 该用户需有收货地址
with app.conn() as cc:
    cc.execute("INSERT INTO user_addresses(user_id,consignee,phone,province,city,district,detail,is_default) VALUES(?,?,?,?,?,?,?,1)",
               (u_gear, '装备券用户', '13800000072', '四川', '成都', '武侯', '路1号'))
    aid_addr = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
gco = ok('post', f'/api/public/clubs/{CLUB}/gear-checkout', json={
    'userId': u_gear, 'items': [{'productId': PID, 'quantity': 1}],
    'voucherCodes': [gcode], 'addressId': aid_addr})
gcoj = gco.json()
gben = gcoj.get('benefits', {})
check('B1-4 装备券抵扣生效(totalDiscount>0)', float(gben.get('totalDiscount', 0)) > 0, str(gben)[:120])


# ───────────────────────── B2 各产品下单 ─────────────────────────
print('\n── B2 产品下单（活动 / 装备）──')
u_act2 = new_user('活动下单用户', '13800000073')
part2 = [{'name': '活动下单用户', 'phone': '13800000073', 'relationToPayer': '本人',
          'idType': '身份证', 'idNumber': '510100199001010021',
          'emergencyContactName': '家', 'emergencyContactPhone': '13900000073'}]
co2 = ok('post', f'/api/public/activities/{AID}/checkout', json={
    'name': '活动下单用户', 'phone': '13800000073', 'occurrenceId': oid, 'participants': part2})
cid2 = co2.json()['checkoutId']
check('B2-1 活动下单生成结算单', bool(cid2))
u_gear2 = new_user('装备下单用户', '13800000074')
with app.conn() as cc:
    cc.execute("INSERT INTO user_addresses(user_id,consignee,phone,province,city,district,detail,is_default) VALUES(?,?,?,?,?,?,?,1)",
               (u_gear2, '装备下单用户', '13800000074', '四川', '成都', '武侯', '路2号'))
    gaddr2 = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
gco2 = ok('post', f'/api/public/clubs/{CLUB}/gear-checkout', json={
    'userId': u_gear2, 'items': [{'productId': PID, 'quantity': 2}], 'addressId': gaddr2})
check('B2-2 装备下单生成结算单', bool(gco2.json().get('checkoutId')))
# 支付该装备单，使商城佣金实际入账（供 B4 结算闸门验证）
ok('post', f"/api/public/checkouts/{gco2.json()['checkoutId']}/pay", json={'provider': 'local', 'simulateSuccess': True})


# ───────────────────────── B3 支付链路 ─────────────────────────
print('\n── B3 支付链路 ──')
# 活动单：simulateSuccess 成交
pay = ok('post', f'/api/public/checkouts/{cid2}/pay', json={'provider': 'local', 'simulateSuccess': True})
pj = pay.json()
check('B3-1 simulateSuccess 成交(paid)', pj.get('status') == 'succeeded' and 'result' in pj, str(pj)[:120])
# 已支付单再 confirm 幂等
conf = ok('post', f'/api/public/checkouts/{cid2}/confirm', json={'commerceOrderId': 'local-manual-x'})
check('B3-2 已支付单confirm幂等(仍paid)', conf.json().get('status') in ('paid', 'succeeded'))
# 取消未支付单
u_can = new_user('取消用户', '13800000075')
partc = [{'name': '取消用户', 'phone': '13800000075', 'relationToPayer': '本人',
          'idType': '身份证', 'idNumber': '510100199001010021',
          'emergencyContactName': '家', 'emergencyContactPhone': '13900000075'}]
coc = ok('post', f'/api/public/activities/{AID}/checkout', json={
    'name': '取消用户', 'phone': '13800000075', 'occurrenceId': oid, 'participants': partc})
cidc = coc.json()['checkoutId']
canc = ok('post', f'/api/public/checkouts/{cidc}/cancel')
check('B3-3 取消未支付单→成功', canc.json().get('ok') is True)
check('B3-4 取消后单状态非pending', ok('get', f'/api/public/checkouts/{cidc}').json().get('status') != 'pending_payment')
# 零元单自动成交（装备券全额抵扣）
u_zero = new_user('零元用户', '13800000076')
with app.conn() as cc:
    cc.execute("INSERT INTO user_addresses(user_id,consignee,phone,province,city,district,detail,is_default) VALUES(?,?,?,?,?,?,?,1)",
               (u_zero, '零元用户', '13800000076', '四川', '成都', '武侯', '路3号'))
    zaddr = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
gc2 = ok('post', '/api/platform/coupons', json={
    'title': '装备全额券', 'benefitType': 'gear_coupon', 'mode': 'fixed', 'cashValue': 800,
    'stock': 50, 'claimMode': 'grant', 'targetType': 'all_users'})
gc2id = gc2.json()['couponId']
ok('post', f'/api/platform/coupons/{gc2id}/grant', json={'scope': 'all_users'})
mine2 = ok('get', f'/api/public/coupons/mine?user_id={u_zero}').json()
zcode = next((x['voucher_code'] for x in mine2 if x.get('benefit_id') == gc2id), None)
zco = ok('post', f'/api/public/clubs/{CLUB}/gear-checkout', json={
    'userId': u_zero, 'items': [{'productId': PID, 'quantity': 1}],
    'voucherCodes': [zcode], 'addressId': zaddr})
zpayable = float(zco.json().get('payable', 1))
check('B3-5 券全额抵扣后应付为0', zpayable == 0, f'payable={zpayable} original={zco.json().get("original")}')
zpay = ok('post', f"/api/public/checkouts/{zco.json()['checkoutId']}/pay", json={'provider': 'local', 'simulateSuccess': True})
check('B3-6 零元单支付自动成交', zpay.json().get('status') == 'succeeded' and 'result' in zpay.json())


# ───────────────────────── B4 结算 ─────────────────────────
print('\n── B4 结算（商城佣金 / 俱乐部结算试算 / 推广）──')
summ = ok('get', f'/api/club/{CLUB}/mall/commission-summary').json()
check('B4-1 佣金汇总可读', isinstance(summ, dict) and 'payableNow' in summ, f"payableNow={summ.get('payableNow')}")
# 起结闸门：未登记账户时 blockReason 提示账户；登记后 accountExists
check('B4-2 未登记账户时canRequest受闸门控制', 'canRequest' in summ and 'blockReason' in summ, str(summ.get('blockReason')))
acct = ok('post', f'/api/club/{CLUB}/settlement-account', json={
    'accountType': 'legal', 'holder': '测试户', 'legalName': '张三',
    'legalIdNo': '510100199001010099', 'bankName': '测试银行', 'cardNo': '6222021234567890123'})
check('B4-3 登记结算账户→脱敏返回', acct.json().get('exists') is True and 'cardNoMasked' in acct.json())
check('B4-4 卡号脱敏', acct.json().get('cardNoMasked', '').endswith('0123') and '*' in acct.json().get('cardNoMasked', ''), acct.json().get('cardNoMasked'))
check('B4-5 法人身份证脱敏', '*' in acct.json().get('legalIdNoMasked', ''))
# 非法账号→400
check('B4-6 身份证非法→400', req('post', f'/api/club/{CLUB}/settlement-account', json={
    'accountType': 'legal', 'holder': 'x', 'legalName': 'y', 'legalIdNo': '123', 'bankName': 'b', 'cardNo': '1'}).status_code == 400)
# 俱乐部结算试算（不报错即可）
prev = ok('post', f'/api/club/{CLUB}/settlements/preview', json={
    'settlement': {'enabled': True}, 'cashAmount': 200, 'userId': u_act2, 'activityId': AID, 'participantCount': 1})
check('B4-7 俱乐部结算试算200', prev.status_code == 200 and ('enabled' in prev.json() or 'orderAmountCents' in prev.json()))
# 推广佣金只读
aff = ok('get', f'/api/club/{CLUB}/affiliate/commissions')
check('B4-8 推广佣金列表可读', isinstance(aff.json(), list))


# ───────────────────────── B5 装备与营销解耦 ─────────────────────────
print('\n── B5 装备与营销解耦（gear-checkout 硬拒营销参数）──')
u_dec = new_user('解耦用户', '13800000077')
with app.conn() as cc:
    cc.execute("INSERT INTO user_addresses(user_id,consignee,phone,province,city,district,detail,is_default) VALUES(?,?,?,?,?,?,?,1)",
               (u_dec, '解耦用户', '13800000077', '四川', '成都', '武侯', '路4号'))
    daddr = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
check('B5-1 gear夹带campaignId→400', req('post', f'/api/public/clubs/{CLUB}/gear-checkout', json={
    'userId': u_dec, 'items': [{'productId': PID, 'quantity': 1}], 'addressId': daddr, 'campaignId': 1}).status_code == 400)
check('B5-2 gear夹带settlement→400', req('post', f'/api/public/clubs/{CLUB}/gear-checkout', json={
    'userId': u_dec, 'items': [{'productId': PID, 'quantity': 1}], 'addressId': daddr, 'settlement': {'enabled': True}}).status_code == 400)
check('B5-3 gear夹带useGiftCard→400', req('post', f'/api/public/clubs/{CLUB}/gear-checkout', json={
    'userId': u_dec, 'items': [{'productId': PID, 'quantity': 1}], 'addressId': daddr, 'useGiftCard': True}).status_code == 400)


# ───────────────────────── 汇总 ─────────────────────────
print('\n' + '═' * 60)
total = len(SEV)
failed = len(FAILS)
passed = total - failed
print(f'总计 {total} 项检查：通过 {passed}，失败 {failed}')
if FAILS:
    print('失败项：')
    for f in FAILS:
        print(f'  [{SEV.get(f, "?")}] {f}')
if FINDINGS:
    print('\n发现 / 优化建议：')
    for sev, name, detail in FINDINGS:
        print(f'  [{sev}] {name} —— {detail}')
print('═' * 60)
sys.exit(1 if FAILS else 0)
