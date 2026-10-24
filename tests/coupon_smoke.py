# -*- coding: utf-8 -*-
"""优惠券体系回归（2026-10-24 用户需求：总平台发券 / 定向投放 / 券图 / 分享）。

锁住的东西，按「改坏了会怎样」排序：

① 券形态：活动券（报名抵用）与装备券（装备购买抵用）分开；满减券必须给门槛，
   无门槛券的抵扣基数由后台自由设定。形态写错 → 用户在结算页被拒付。
② 投放范围：全部俱乐部 / 全部 C 端用户 / 指定俱乐部 / 指定城市（按注册城市圈人）。
   范围判错 → 券发给了不该拿的人，或者该拿的人领不到。
③ 领取与群发：claim 是用户自己领（城市营销），grant 是后台定向发；
   每人限领、库存、过期都要拦住。
④ 核销：满减券在订单金额不足时必须给出明确报错，而不是默默抵 0；
   平台券的抵扣一律记平台补贴（成本总平台承担）。
⑤ 平台活动券跨俱乐部可用（成都区域的用户在哪个俱乐部报名都能用），
   但定向到某个俱乐部的券只在该俱乐部有效。
⑥ 券图与分享：内置通用券图模板、券码可查可分享。

自带临时库隔离，不碰项目真库。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault('CLUBOS_TEST_OPEN', '1')
os.environ.setdefault('MOCK_AI', '1')
os.environ.setdefault('COMMERCE_PROVIDER', 'local')

TMP = Path(tempfile.mkdtemp(prefix='clubos-coupon-smoke-'))
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
        cc.execute("INSERT INTO users(name,phone,city) VALUES('成都用户','13900001111','成都')")
        u_cd = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
        cc.execute("INSERT INTO users(name,phone,city) VALUES('重庆用户','13900002222','重庆')")
        u_cq = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
        cc.execute("INSERT INTO users(name,phone,city) VALUES('无城市用户','13900003333',NULL)")
        u_none = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
        cc.execute("INSERT INTO club_members(club_id,user_id,level,club_points_balance) VALUES(1,?, '普通会员',0)", (u_cd,))

    print('\n── ① 建券：用途 / 形态 / 投放范围 ──')
    r = c.post('/api/platform/coupons', json={
        'title': '成都区域活动券', 'benefitType': 'activity_coupon', 'mode': 'fixed',
        'cashValue': 30, 'stock': 100, 'claimMode': 'claim', 'targetType': 'city',
        'targetCity': '成都', 'imageKey': 'sunrise', 'perUserLimit': 1,
        'validFrom': '2026-01-01', 'validTo': '2026-12-31'})
    ok(r.status_code == 200, '建券成功（活动券 / 无门槛 / 城市投放）', r.text[:120])
    city_coupon = r.json().get('couponId') if r.status_code == 200 else None

    r2 = c.post('/api/platform/coupons', json={
        'title': '装备满减券', 'benefitType': 'gear_coupon', 'mode': 'threshold',
        'thresholdAmount': 200, 'cashValue': 50, 'stock': 50, 'claimMode': 'grant',
        'targetType': 'all_users', 'imageKey': 'forest'})
    ok(r2.status_code == 200, '建券成功（装备券 / 满 200 减 50 / 全量发放）', r2.text[:120])
    gear_coupon = r2.json().get('couponId') if r2.status_code == 200 else None

    bad = c.post('/api/platform/coupons', json={'title': '坏券', 'benefitType': 'gear_coupon',
                                                'mode': 'threshold', 'cashValue': 10})
    ok(bad.status_code == 400, '满减券不给门槛 → 400', bad.text[:80])
    bad2 = c.post('/api/platform/coupons', json={'title': '坏券2', 'benefitType': 'gear_coupon',
                                                 'mode': 'fixed', 'cashValue': 10,
                                                 'targetType': 'city'})
    ok(bad2.status_code == 400, '城市投放不给城市 → 400', bad2.text[:80])
    bad3 = c.post('/api/platform/coupons', json={'title': '坏券3', 'benefitType': 'gear_coupon',
                                                 'mode': 'fixed', 'cashValue': 0})
    ok(bad3.status_code == 400, '抵扣金额为 0 → 400', bad3.text[:80])

    with db.conn() as cc:
        row = cc.execute('SELECT * FROM member_benefits WHERE id=?', (city_coupon,)).fetchone()
    ok(row['mode'] == 'fixed' and row['threshold_amount'] == 0, '无门槛券门槛存 0')
    ok(row['target_type'] == 'city' and row['target_city'] == '成都', '城市投放范围落库')
    ok(row['claim_mode'] == 'claim' and row['image_key'] == 'sunrise', '领取方式与券图落库')
    ok(row['points_cost'] == 0, '平台真券不设积分门槛')

    print('\n── ② 城市定向：按注册城市圈人 ──')
    claim_cd = c.get(f'/api/public/coupons/claimable?user_id={u_cd}').json()
    claim_cq = c.get(f'/api/public/coupons/claimable?user_id={u_cq}').json()
    ok(any(x['id'] == city_coupon for x in claim_cd), '成都用户能看到城市券')
    ok(not any(x['id'] == city_coupon for x in claim_cq), '重庆用户看不到成都券')
    deny = c.post(f'/api/public/coupons/{city_coupon}/claim', json={'userId': u_cq})
    ok(deny.status_code == 400, '重庆用户直接领取 → 400', deny.text[:80])
    nocity = c.post(f'/api/public/coupons/{city_coupon}/claim', json={'userId': u_none})
    ok(nocity.status_code == 400, '未设城市的用户领不到城市券', nocity.text[:80])

    print('\n── ③ 领取：券码 / 限领 / 券包 ──')
    got = c.post(f'/api/public/coupons/{city_coupon}/claim', json={'userId': u_cd})
    ok(got.status_code == 200, '成都用户领取成功', got.text[:120])
    code = got.json().get('voucherCode') if got.status_code == 200 else ''
    ok(str(code).startswith('CPN-'), '券码形如 CPN-XXXX', code)
    again = c.post(f'/api/public/coupons/{city_coupon}/claim', json={'userId': u_cd})
    ok(again.status_code == 400, '同一人超限再领 → 400', again.text[:80])
    mine = c.get(f'/api/public/coupons/mine?user_id={u_cd}').json()
    ok(any(x['voucher_code'] == code for x in mine), '我的券包能看到刚领的券')
    ok(all('image_key' in x for x in mine), '券包带券图字段（分享卡要渲染）')

    print('\n── ④ 券图模板与分享 ──')
    imgs = c.get('/api/platform/coupon-images').json()
    ok(len(imgs.get('keys', [])) >= 6, '内置通用券图模板 ≥6 款', str(imgs.get('keys')))
    ok(set(imgs.get('modes', [])) == {'fixed', 'threshold'}, '券形态枚举齐全')
    share = c.get(f'/api/public/coupons/{code}')
    ok(share.status_code == 200, '按券码可查（分享链接）', share.text[:100])
    ok(share.json().get('title') == '成都区域活动券', '分享页含券名')
    ok(c.get('/api/public/coupons/NOPE').status_code == 404, '不存在的券码 → 404')

    print('\n── ⑤ 群发：全量用户 / 指定俱乐部 ──')
    gr = c.post(f'/api/platform/coupons/{gear_coupon}/grant', json={'scope': 'all_users'})
    ok(gr.status_code == 200 and gr.json().get('issued', 0) >= 3, '全量群发成功', gr.text[:120])
    ok(gr.json().get('stats', {}).get('issued', 0) >= 3, '统计里已发数同步')
    gr2 = c.post(f'/api/platform/coupons/{gear_coupon}/grant', json={'scope': 'club', 'clubId': 1})
    ok(gr2.status_code == 200, '按俱乐部定向群发成功', gr2.text[:120])
    ok(gr2.json().get('skipped', 0) >= 1, '每人限领：已领过的人被跳过', gr2.text[:120])
    # 定向俱乐部的券要记在该俱乐部名下（display_club_id），平台全量券是 NULL（通用）。
    r_dir = c.post('/api/platform/coupons', json={
        'title': '定向某俱乐部的装备券', 'benefitType': 'gear_coupon', 'mode': 'fixed',
        'cashValue': 15, 'stock': 10, 'claimMode': 'grant', 'targetType': 'club',
        'targetClubId': 1})
    dir_coupon = r_dir.json().get('couponId')
    gr3 = c.post(f'/api/platform/coupons/{dir_coupon}/grant', json={'scope': 'club', 'clubId': 1})
    with db.conn() as cc:
        club_holders = cc.execute(
            "SELECT COUNT(*) FROM benefit_redemptions WHERE benefit_id=? AND display_club_id=1",
            (dir_coupon,)).fetchone()[0]
        null_holders = cc.execute(
            "SELECT COUNT(*) FROM benefit_redemptions WHERE benefit_id=? AND display_club_id IS NULL",
            (gear_coupon,)).fetchone()[0]
    ok(club_holders >= 1, '定向俱乐部的券记在该俱乐部名下', str(club_holders))
    ok(null_holders >= 1, '平台全量券不绑俱乐部（display_club_id=NULL）', str(null_holders))

    print('\n── ⑥ 核销：门槛 / 平台承担成本 / 跨俱乐部 ──')
    with db.conn() as cc:
        engine = app.benefit_engine
        issued = cc.execute("""SELECT r.* FROM benefit_redemptions r WHERE r.benefit_id=?
                               AND r.user_id=? AND r.status='issued' LIMIT 1""",
                            (gear_coupon, u_cd)).fetchone()
        vcode = issued['voucher_code']
        try:
            engine.quote_vouchers(cc, voucher_codes=[vcode], user_id=u_cd, club_id=1,
                                   kind='gear', amount_available=100)
            low = None
        except ValueError as e:
            low = str(e)
        ok(low is not None and '满' in low, '满减券金额不足 → 明确报错', str(low))
        quoted = engine.quote_vouchers(cc, voucher_codes=[vcode], user_id=u_cd, club_id=1,
                                        kind='gear', amount_available=300)
        ok(abs(quoted['platformSubsidy'] - 50) < 0.01, '金额达标 → 平台补贴 50', str(quoted))
        ok(abs(quoted['totalDiscount'] - 50) < 0.01, '总抵扣 50')
        # 城市活动券跨俱乐部可用：成都在 1 号俱乐部领的券，在 2 号俱乐部报名也能用。
        with db.conn() as cc2:
            cc2.execute("INSERT INTO clubs(name,status,plan) VALUES('成都另一家俱乐部','active','pro')")
            club2 = int(cc2.execute('SELECT last_insert_rowid()').fetchone()[0])
            cross = engine.eligible_vouchers(cc2, user_id=u_cd, club_id=club2, kind='activity')
        ok(any(x['voucher_code'] == code for x in cross), '城市活动券跨俱乐部可用')

    print('\n── ⑦ 定向俱乐部的券只在本俱乐部有效 ──')
    r3 = c.post('/api/platform/coupons', json={
        'title': 'A 俱乐部专属券', 'benefitType': 'activity_coupon', 'mode': 'fixed',
        'cashValue': 20, 'stock': 10, 'claimMode': 'grant', 'targetType': 'club',
        'targetClubId': 1})
    club_coupon = r3.json().get('couponId')
    gr3 = c.post(f'/api/platform/coupons/{club_coupon}/grant', json={'scope': 'club', 'clubId': 1})
    ok(gr3.status_code == 200 and gr3.json().get('issued', 0) >= 1, '定向俱乐部发券成功', gr3.text[:120])
    with db.conn() as cc:
        engine = app.benefit_engine
        in1 = engine.eligible_vouchers(cc, user_id=u_cd, club_id=1, kind='activity')
        with db.conn() as cc2:
            in2 = engine.eligible_vouchers(cc2, user_id=u_cd, club_id=2, kind='activity')
    # 注意：x 是兑换记录行，券模板 id 在 x['benefit_id']，别拿 x['id'] 比。
    ok(any(x['benefit_id'] == club_coupon for x in in1), '本俱乐部能看到定向券')
    ok(not any(x['benefit_id'] == club_coupon for x in in2), '别的俱乐部看不到定向券')
    ok(any(x['benefit_id'] == city_coupon for x in in2), '城市券在别的俱乐部依然可用')

    print('\n── ⑧ 用户城市设置 ──')
    setc = c.post(f'/api/public/users/{u_none}/city', json={'city': '成都'})
    ok(setc.status_code == 200, '设置注册城市成功', setc.text[:80])
    nowclaim = c.post(f'/api/public/coupons/{city_coupon}/claim', json={'userId': u_none})
    ok(nowclaim.status_code == 200, '设置城市后即可领该城市券', nowclaim.text[:80])
    again2 = c.post(f'/api/public/coupons/{city_coupon}/claim', json={'userId': u_none})
    ok(again2.status_code == 400, '再领一次 → 每人限领 1 张', again2.text[:80])
    with db.conn() as cc:
        city_val = cc.execute('SELECT city FROM users WHERE id=?', (u_none,)).fetchone()[0]
    ok(city_val == '成都', '城市已落库')

    print('\n' + ('ALL PASS' if not FAILS else 'FAILED: %s' % FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
