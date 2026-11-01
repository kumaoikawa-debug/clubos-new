# -*- coding: utf-8 -*-
"""会员开通方式 / 俱乐部营销（折扣券+消费送券）/ 积分商城 回归（2026-10-26 用户文档需求）。

锁住的东西，按「改坏了会怎样」排序：

① 会员开通方式：条件开通（消费/次数自动升级）与购买开通（自定义会员卡价格+有效期）。
   购买型等级不参与自动升级；有效期内购买卡生效（取更高档）。
   前提：俱乐部必须先开通「购买会员」。
② 会员购买订单：购买写 member_orders、立即生效、俱乐部可查；未开通时 C 端 409。
③ 折扣券：只有活动券支持折扣形态；结算按「剩余金额 ×(100-折扣)/100」立减；
   折扣力度 1~99 之外要拦。
④ 消费送券：单笔活动实付满门槛自动送券；只能送本俱乐部活动券且券须有库存；
   每人限送次数；没库存静默跳过，绝不打断支付主流程。
⑤ 积分商城：优惠券类兑换即发券到券包；活动/服务/实物生成核销码并可核销；
   积分不足 409；库存递减；兑完 409。
⑥ 前端护栏：版本号、C 端可用积分入口、会员卡购买入口、俱乐部营销区块、核销入口。

自带临时库隔离，不碰项目真库。
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault('CLUBOS_TEST_OPEN', '1')
os.environ.setdefault('MOCK_AI', '1')
os.environ.setdefault('COMMERCE_PROVIDER', 'local')

TMP = Path(tempfile.mkdtemp(prefix='clubos-member-mall-smoke-'))
os.environ['CLUBOS_DB_PATH'] = str(TMP / 'clubos.db')

import db  # noqa: E402
db.init_db()
import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(app.app)
FAILS = []


def ok(cond, label, extra=''):
    print(('  PASS  ' if cond else '  FAIL  ') + label + (f'   | {extra}' if extra else ''))
    if not cond:
        FAILS.append(label)


def main():
    with db.conn() as cc:
        cc.execute("INSERT INTO users(name,phone,city) VALUES('购买会员用户','13700000001','成都')")
        uid = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
        cc.execute("INSERT INTO users(name,phone,city) VALUES('商城用户','13700000002','成都')")
        uid2 = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
        cc.execute("INSERT INTO club_members(club_id,user_id,level,club_points_balance) VALUES(1,?, '普通会员',2000)", (uid,))
        cc.execute("INSERT INTO club_members(club_id,user_id,level,club_points_balance) VALUES(1,?, '普通会员',800)", (uid2,))

    print('\n── ① 会员开通方式 ──')
    r = c.post('/api/club/1/membership/tiers', json={
        'name': '年卡会员', 'rank': 50, 'accessMode': 'PURCHASE',
        'price': 399, 'validMonths': 12, 'benefits': ['全年活动优先报名']})
    ok(r.status_code == 200, '创建购买型等级（¥399 / 12 个月）', r.text[:120])
    buy_tier = r.json().get('tierId')
    bad = c.post('/api/club/1/membership/tiers', json={
        'name': '坏卡', 'rank': 60, 'accessMode': 'PURCHASE', 'price': 0})
    ok(bad.status_code == 400, '购买型等级不给价 → 400', bad.text[:80])

    # 未开通购买会员：C 端可购列表为空、直接购买 409
    r = c.get(f'/api/public/clubs/1/membership/purchasable?user_id={uid}')
    ok(r.status_code == 200 and r.json().get('enabled') is False, '未开通时 purchasable.enabled=False')
    r = c.post('/api/public/clubs/1/membership/purchase', json={'userId': uid, 'tierId': buy_tier})
    ok(r.status_code == 409, '未开通时购买 → 409', r.text[:80])

    r = c.post('/api/club/1/membership/purchase-enabled', json={'enabled': True})
    ok(r.status_code == 200 and r.json().get('enabled') is True, '俱乐部开通「购买会员」')

    r = c.get(f'/api/public/clubs/1/membership/purchasable?user_id={uid}')
    j = r.json()
    ok(r.status_code == 200 and j.get('enabled') and any(int(t['id']) == int(buy_tier) for t in j.get('tiers') or []),
       '开通后 C 端能看到可购会员卡')

    r = c.post('/api/public/clubs/1/membership/purchase', json={'userId': uid, 'tierId': buy_tier})
    ok(r.status_code == 200 and (r.json().get('member') or {}).get('level') == '年卡会员',
       '购买成功并立即升级为「年卡会员」', r.text[:150])
    ok((r.json().get('order') or {}).get('valid_to', '') > (r.json().get('order') or {}).get('valid_from', ''),
       '购买订单带有效期')
    r = c.get(f'/api/public/clubs/1/membership/purchasable?user_id={uid}')
    ok((r.json().get('activePurchase') or {}).get('tier_id') == buy_tier, 'activePurchase 返回当前有效卡')
    r = c.get('/api/club/1/member-orders')
    ok(r.status_code == 200 and len(r.json()) >= 1, '俱乐部端可查购买订单')

    # 购买型等级不参与自动升级：消费条件不会把年卡降档（refresh 后仍是年卡）
    r = c.get(f'/api/public/clubs/1/member-center?user_id={uid}')
    ok((r.json().get('member') or {}).get('level') == '年卡会员', 'refresh 后购买等级不降档')
    ok(((r.json().get('membershipPurchase') or {}).get('enabled')) is True, 'member-center 下发 membershipPurchase')

    print('\n── ② 折扣券 ──')
    r = c.post('/api/club/1/benefits', json={
        'title': '活动 9 折券', 'benefitType': 'activity_coupon', 'mode': 'discount',
        'discountPercent': 90, 'pointsCost': 200, 'stock': 10, 'pointsType': 'club'})
    ok(r.status_code == 200, '创建俱乐部折扣券（9 折）', r.text[:120])
    disc_coupon = r.json().get('benefitId')
    bad = c.post('/api/club/1/benefits', json={
        'title': '装备折扣券', 'benefitType': 'gear_coupon', 'mode': 'discount',
        'discountPercent': 90, 'pointsType': 'club'})
    ok(bad.status_code == 400, '折扣券不能涉及装备 → 400', bad.text[:80])
    bad = c.post('/api/club/1/benefits', json={
        'title': '力度异常', 'benefitType': 'activity_coupon', 'mode': 'discount',
        'discountPercent': 100, 'pointsType': 'club'})
    ok(bad.status_code == 400, '折扣 100 → 400', bad.text[:80])

    # 用活动积分把折扣券兑换到手，再验结算口径
    r = c.post(f'/api/public/clubs/1/benefits/{disc_coupon}/redeem', json={'userId': uid})
    ok(r.status_code == 200, '活动积分兑换折扣券到手', r.text[:120])
    code = r.json().get('voucherCode')
    with db.conn() as cc:
        q = app.benefit_engine.quote_vouchers(cc, voucher_codes=[code], user_id=uid, club_id=1,
                                              kind='activity', amount_available=200.0)
    ok(abs(q['totalDiscount'] - 20.0) < 0.01, '200 元订单用 9 折券 → 立减 20', str(q))
    ok(q['applied'] and q['applied'][0].get('fundingOwner') == 'CLUB', '折扣券成本由俱乐部承担')

    print('\n── ③ 消费送券 ──')
    r = c.post('/api/club/1/benefits', json={
        'title': '满 100 减 20 券', 'benefitType': 'activity_coupon', 'mode': 'threshold',
        'thresholdAmount': 100, 'cashValue': 20, 'pointsCost': 100, 'stock': 5, 'pointsType': 'club'})
    gift_coupon = r.json().get('benefitId')
    ok(r.status_code == 200, '创建待赠送的满减券（库存 5）')
    r = c.post('/api/club/1/coupon-gift-rules', json={
        'benefitId': gift_coupon, 'thresholdAmount': 100, 'perUserLimit': 1})
    ok(r.status_code == 200, '创建消费送券规则（满 100 送）', r.text[:100])
    bad = c.post('/api/club/1/coupon-gift-rules', json={'benefitId': 99999, 'thresholdAmount': 100})
    ok(bad.status_code == 404, '赠送不存在的券 → 404')

    # 没消费 → 不送
    with db.conn() as cc:
        g0 = app.benefit_engine.process_coupon_gifts(cc, club_id=1, user_id=uid, cash_paid=0)
    ok(not g0, '0 元消费不触发赠送')
    # 满 100 → 送一张
    with db.conn() as cc:
        g1 = app.benefit_engine.process_coupon_gifts(cc, club_id=1, user_id=uid, cash_paid=150)
    ok(len(g1) == 1 and g1[0].get('fundingOwner') == 'CLUB', '150 元消费触发赠送，成本俱乐部承担', str(g1))
    # 每人限 1 次 → 第二笔不再送
    with db.conn() as cc:
        g2 = app.benefit_engine.process_coupon_gifts(cc, club_id=1, user_id=uid, cash_paid=300)
    ok(not g2, '每人限送 1 次，第二笔不再送')
    # 换个用户送，把库存耗光后规则静默跳过（uid2 开头已入会）
    with db.conn() as cc:
        g3 = app.benefit_engine.process_coupon_gifts(cc, club_id=1, user_id=uid2, cash_paid=200)
    ok(len(g3) == 1, '第二个用户正常触发')
    with db.conn() as cc:
        cc.execute('UPDATE member_benefits SET stock=0 WHERE id=?', (gift_coupon,))
        g4 = app.benefit_engine.process_coupon_gifts(cc, club_id=1, user_id=uid2, cash_paid=500)
    ok(not g4, '券库存为 0 → 规则静默跳过，不报错')

    print('\n── ④ 积分商城 ──')
    r = c.post('/api/club/1/points-mall', json={
        'itemType': 'coupon', 'refId': gift_coupon, 'title': '20 元券兑换', 'pointsCost': 300, 'stock': 5})
    ok(r.status_code == 200, '上架优惠券兑换商品（300 积分）', r.text[:100])
    coupon_item = r.json().get('itemId')
    r = c.post('/api/club/1/points-mall', json={
        'itemType': 'goods', 'title': '露营灯', 'pointsCost': 500, 'stock': 3, 'description': '到店领取'})
    ok(r.status_code == 200, '上架实物兑换商品（500 积分）')
    goods_item = r.json().get('itemId')
    bad = c.post('/api/club/1/points-mall', json={'itemType': 'coupon', 'title': '无关联', 'pointsCost': 100})
    ok(bad.status_code == 400, '优惠券兑换不关联券 → 400', bad.text[:80])

    r = c.get(f'/api/public/clubs/1/points-mall?user_id={uid2}')
    j = r.json()
    ok(r.status_code == 200 and len(j.get('items') or []) == 2, 'C 端积分商城返回商品列表')
    ok((j.get('wallet') or {}).get('clubPoints') == 800, '商城页带当前积分')

    r = c.post(f'/api/public/clubs/1/points-mall/{coupon_item}/redeem', json={'userId': uid2})
    ok(r.status_code == 200 and (r.json().get('coupon') or {}).get('voucherCode'),
       '优惠券类兑换 → 券直接进券包', r.text[:150])
    ok((r.json().get('wallet') or {}).get('clubPoints') == 500, '兑换后积分扣减 800→500')
    r = c.post(f'/api/public/clubs/1/points-mall/{goods_item}/redeem', json={'userId': uid2})
    ok(r.status_code == 200 and str(r.json().get('code') or '').startswith('PM-'),
       '实物类兑换 → 生成 PM- 核销码', r.text[:120])
    verify_code = r.json().get('code')
    ok((r.json().get('wallet') or {}).get('clubPoints') == 0, '积分扣减 500→0')
    r = c.post(f'/api/public/clubs/1/points-mall/{goods_item}/redeem', json={'userId': uid2})
    ok(r.status_code == 409, '积分不足兑换 → 409', r.text[:80])

    r = c.post('/api/club/1/points-mall/verify', json={'code': verify_code})
    ok(r.status_code == 200 and (r.json().get('redemption') or {}).get('status') == 'verified', '俱乐部核销兑换码')
    r = c.post('/api/club/1/points-mall/verify', json={'code': verify_code})
    ok(r.status_code == 409, '重复核销 → 409', r.text[:80])
    r = c.get('/api/club/1/points-mall/redemptions')
    ok(r.status_code == 200 and len(r.json()) >= 2, '俱乐部可查兑换记录')

    print('\n── ⑤ 前端护栏 ──')
    club_html = (ROOT / 'static/club/index.html').read_text(encoding='utf-8')
    ok('俱乐部营销 · 优惠券' in club_html, 'club 端「俱乐部福利」已改「俱乐部营销 · 优惠券」')
    ok('addClubCoupon' in club_html and 'addGiftRule' in club_html and 'addMallItem' in club_html,
       '营销三入口挂上（券/送券规则/积分商城）')
    ok('purchaseEnabled' in club_html and 'memberOrderList' in club_html, '购买会员开关 + 购买订单区块')
    club_js = (ROOT / 'static/club/club.js').read_text(encoding='utf-8')
    ok('accessMode' in club_js and 'validMonths' in club_js, '等级编辑带开通方式/价格/有效期')
    # 版本号每次发版都会变 —— 只断言「带版本号且不是旧的硬编码」，避免回归必红（2026-10-27）。
    _m = re.search(r'src="/static/club/club\.js\?v=([0-9A-Za-z]+)"', club_html)
    ok(bool(_m), 'club.js 带缓存版本号 %s' % (_m.group(1) if _m else '缺失'))
    wf = (ROOT / 'static/ux/workflows-club.js').read_text(encoding='utf-8')
    ok('verifyMallCode' in wf and 'discountPercent' in wf, 'workflows-club 带核销与折扣券表单')
    web_html = (ROOT / 'static/web/index.html').read_text(encoding='utf-8')
    ok("pushMe('pointsmall')" in web_html, 'C 端「可用积分」点击进积分商城')
    _m2 = re.search(r'src="/static/web/me\.js\?v=([0-9A-Za-z]+)(?:&[^" ]*)?"', web_html)
    ok(bool(_m2), 'me.js 带缓存版本号 %s' % (_m2.group(1) if _m2 else '缺失'))
    me_js = (ROOT / 'static/web/me.js').read_text(encoding='utf-8')
    ok('SUB_ME.pointsmall' in me_js and 'meMallPaint' in me_js, '积分商城二级页实现')
    ok('meBuyTier' in me_js and 'membershipPurchase' in me_js, '会员卡购买入口')
    cf = (ROOT / 'static/ux/coupon-faces.js').read_text(encoding='utf-8')
    ok("mode === 'discount'" in cf, '券面支持折扣形态')
    app_py = (ROOT / 'app.py').read_text(encoding='utf-8')
    ok('process_coupon_gifts' in (ROOT / 'clubos_domain/checkout.py').read_text(encoding='utf-8'),
       '支付成功挂消费送券钩子')
    ok('points-mall/verify' in app_py and 'member-orders' in app_py, '新端点齐备')

    print('\n══════════════════════════════')
    print(f'共 {len(FAILS)} 项 FAIL' if FAILS else 'ALL PASS')
    if FAILS:
        for f in FAILS:
            print('  - ' + f)
        sys.exit(1)


if __name__ == '__main__':
    main()
