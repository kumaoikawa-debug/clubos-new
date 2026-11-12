# -*- coding: utf-8 -*-
"""会员逻辑第二批回归（2026-10-26 用户四条新需求）：

① 会员活动折扣：等级配折扣 + 活动开「参与会员优惠」双闸生效；
   折扣先于券计算（券基于折后金额）；结算单落 member_discount 快照。
② 升级赠券：达到设定等级自动送券；tier_gift_log 唯一防重，重复重算不重复发。
③ 拉新邀请：邀请码绑定，双方各得邀请券；重复绑定幂等；邀请码不存在 404。
④ 券转赠：好友输券码领取（须是会员、券未用未过期、允许分享）。
⑤ 券面自定义：face {c1,c2,ink} 落库下发；非法 hex 被丢弃；前端设计器护栏。

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

TMP = Path(tempfile.mkdtemp(prefix='clubos-member-v2-smoke-'))
os.environ['CLUBOS_DB_PATH'] = str(TMP / 'clubos.db')

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _identity_patch  # noqa: E402,F401  报名/结算自动补持护照参加人
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


def seed_activity(club_id=1, price=200.0, member_discount=1):
    """最小可售活动 + 未来团期。"""
    with db.conn() as cc:
        cc.execute('''INSERT INTO activities(club_id,title,status,event_date,location,price,capacity,
                      member_discount_enabled,activity_master_json,detail_json)
                      VALUES(?,?,?,?,?,?,?,?,?,?)''',
                   (club_id, '会员折扣测试活动', 'published', '2099-01-01', '成都', price, 30,
                    member_discount, '{"priceFrom":"priced"}', '{}'))
        aid = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
        cc.execute('''INSERT INTO activity_occurrences(activity_id,club_id,start_at,end_at,price,capacity,status,label)
                      VALUES(?,?,?,?,?,?, 'open',?)''',
                   (aid, club_id, '2099-01-05 08:00', '2099-01-05 18:00', price, 30, '首团'))
        oid = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
    return aid, oid


def main():
    # 种子用户：uid1 邀请人（老会员）、uid2 折扣测试用户、uid3 新会员、uid4 非会员
    # ★ demo 种子里可能已有用户，绝不能硬编码 user id —— 插入后立即取真实 id。
    with db.conn() as cc:
        ids = {}
        for var, name, phone in (('uid1', '邀请人老张', '13600000001'), ('uid2', '会员测试小李', '13600000002'),
                                 ('uid3', '新会员小王', '13600000003'), ('uid4', '路人小赵', '13600000004')):
            cc.execute("INSERT INTO users(name,phone,city) VALUES(?,?,'成都')", (name, phone))
            ids[var] = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
    uid1, uid2, uid3, uid4 = ids['uid1'], ids['uid2'], ids['uid3'], ids['uid4']
    with db.conn() as cc:
        for u in (uid1, uid2, uid3):
            cc.execute("INSERT OR IGNORE INTO club_members(club_id,user_id,level) VALUES(1,?,'普通会员')", (u,))

    print('\n── ① 会员活动折扣 ──')
    r = c.post('/api/club/1/membership/tiers', json={
        'name': '金卡8折', 'rank': 30, 'accessMode': 'CONDITION',
        'minActivitySpend': 500, 'minActivityCount': 0, 'activityDiscountPercent': 80})
    ok(r.status_code == 200, '创建 8 折条件等级', r.text[:100])
    gold_tier = r.json().get('tierId')
    aid, oid = seed_activity(member_discount=1)
    # uid2 未升级（普通会员）→ 无折扣
    q = c.get(f'/api/public/activities/{aid}/price-quote?occurrence_id={oid}&user_id={uid2}')
    ok(q.status_code == 200 and float(q.json().get('memberDiscount') or 0) == 0,
       '普通会员报价无折扣', str(q.json().get('memberDiscount')))
    # uid2 消费达标 → 升级金卡
    with db.conn() as cc:
        cc.execute("INSERT INTO registrations(activity_id,occurrence_id,club_id,user_id,status,original_amount,amount,payment_status) "
                   "VALUES(?,?,1,?,'paid',600,600,'succeeded')", (aid, oid, uid2))
        _reg = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
        cc.execute("INSERT INTO registration_participants(registration_id,activity_id,occurrence_id,club_id,payer_user_id,name,status) "
                   "VALUES(?,?,?,?,?,?, 'active')", (_reg, aid, oid, 1, uid2, '会员测试小李'))
        app.membership_engine.refresh_member(cc, club_id=1, user_id=uid2)
    r = c.get(f'/api/club/1/members/{uid2}')
    ok(r.status_code == 200 and (r.json().get('member') or {}).get('level') == '金卡8折',
       'uid2 消费达标升级为金卡8折', r.text[:100])
    q = c.get(f'/api/public/activities/{aid}/price-quote?occurrence_id={oid}&user_id={uid2}')
    body = q.json()
    ok(q.status_code == 200 and float(body.get('memberDiscount') or 0) == 40.0,
       '金卡会员报价立减 40（200 × 8 折）', f"memberDiscount={body.get('memberDiscount')} payable={body.get('payable')}")
    ok(float(body.get('payable') or 0) == 160.0, '报价应付 = 160')
    # 活动不开「参与会员优惠」→ 无折扣（前提条件双闸）
    aid2, oid2 = seed_activity(member_discount=0)
    q = c.get(f'/api/public/activities/{aid2}/price-quote?occurrence_id={oid2}&user_id={uid2}')
    ok(float(q.json().get('memberDiscount') or 0) == 0, '活动未开「参与会员优惠」→ 无折扣')
    # 结算单落 member_discount 快照
    ck = c.post(f'/api/public/activities/{aid}/checkout', json={
        'name': '会员测试小李', 'phone': '13600000002', 'occurrenceId': oid})
    ck_uid = int(ck.json().get('userId') or 0) if ck.status_code == 200 else 0
    ok(ck.status_code == 200 and ck_uid == uid2, '结算复用已有会员账号（不新建）', ck.text[:100])
    ok(ck.status_code == 200 and float(ck.json().get('memberDiscount') or 0) == 40.0,
       '下单结算返回 memberDiscount=40', ck.text[:150])
    with db.conn() as cc:
        intent = cc.execute("SELECT member_discount,member_discount_rate,cash_amount FROM checkout_intents WHERE id=?",
                            (ck.json().get('checkoutId'),)).fetchone()
        ok(intent and float(intent['member_discount']) == 40.0 and float(intent['member_discount_rate']) == 80.0,
           'checkout_intents 落折扣快照（40 / 8 折）', str(dict(intent) if intent else None))
        ok(intent and float(intent['cash_amount']) == 160.0, '结算 cash_amount = 160（折后价）')

    print('\n── ② 升级赠券 ──')
    r = c.post('/api/club/1/benefits', json={
        'title': '金卡专享直抵券', 'benefitType': 'activity_coupon', 'mode': 'fixed',
        'cashValue': 25, 'pointsCost': 10, 'stock': 5, 'pointsType': 'club', 'claimMode': 'grant'})
    ok(r.status_code == 200, '创建金卡赠券（库存 5）', r.text[:100])
    gift_coupon = r.json().get('id') or r.json().get('benefitId')
    r = c.post('/api/club/1/tier-gift-rules', json={'tierId': gold_tier, 'benefitId': gift_coupon})
    ok(r.status_code == 200, '绑定升级赠券：达到金卡8折送券', r.text[:100])
    # 再触发一次升级判定（(uid2) 已在金卡，首次升级发生在 ① 的 refresh_member —— 当时规则还没建，
    # 这次重新触发不会重复送：直接调 refresh 验证幂等；再用另一位新达标用户验证「达标即送」）
    with db.conn() as cc:
        cc.execute("INSERT INTO users(name,phone,city) VALUES('达标新会员','13600000005','成都')")
        uid5 = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
        cc.execute("INSERT INTO club_members(club_id,user_id,level) VALUES(1,?,'普通会员')", (uid5,))
        cc.execute("INSERT INTO registrations(activity_id,occurrence_id,club_id,user_id,status,original_amount,amount,payment_status) "
                   "VALUES(?,?,'1',?,'paid',600,600,'succeeded')", (aid, oid, uid5))
        _reg = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
        cc.execute("INSERT INTO registration_participants(registration_id,activity_id,occurrence_id,club_id,payer_user_id,name,status) "
                   "VALUES(?,?,?,?,?,?, 'active')", (_reg, aid, oid, 1, uid5, '达标新会员'))
        app.membership_engine.refresh_member(cc, club_id=1, user_id=uid5)
        log = cc.execute('SELECT COUNT(*) FROM tier_reward_log WHERE user_id=? AND tier_id=?', (uid5, gold_tier)).fetchone()[0]
        ok(log == 1, '新达标会员升级 → tier_reward_log 落 1 条')
        issued = cc.execute("SELECT COUNT(*) FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id "
                            "WHERE r.user_id=? AND b.id=? AND r.gift_note LIKE '达到%'", (uid5, gift_coupon)).fetchone()[0]
        ok(issued == 1, '升级赠券自动到账（1 张）')
        app.membership_engine.refresh_member(cc, club_id=1, user_id=uid5)
        app.membership_engine.refresh_member(cc, club_id=1, user_id=uid5)
        log2 = cc.execute('SELECT COUNT(*) FROM tier_reward_log WHERE user_id=? AND tier_id=?', (uid5, gold_tier)).fetchone()[0]
        issued2 = cc.execute("SELECT COUNT(*) FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id "
                             "WHERE r.user_id=? AND b.id=? AND r.gift_note LIKE '达到%'", (uid5, gift_coupon)).fetchone()[0]
        ok(log2 == 1 and issued2 == 1, '重复重算不重复发（防重唯一索引生效）')
    rules = c.get('/api/club/1/tier-gift-rules').json()
    ok(any(x.get('tier_id') == gold_tier for x in rules), '俱乐部端可见升级赠券规则')

    print('\n── ③ 拉新邀请 ──')
    r = c.post('/api/club/1/benefits', json={
        'title': '邀请专享券', 'benefitType': 'activity_coupon', 'mode': 'threshold',
        'thresholdAmount': 100, 'cashValue': 20, 'pointsCost': 10, 'stock': 10,
        'pointsType': 'club', 'claimMode': 'grant'})
    ok(r.status_code == 200, '创建邀请专享券', r.text[:100])
    referral_coupon = r.json().get('benefitId')
    r = c.patch('/api/club/1/referral-coupon', json={'benefitId': referral_coupon})
    ok(r.status_code == 200 and r.json().get('benefitId') == referral_coupon, '设定俱乐部邀请券', r.text[:80])
    info = c.get(f'/api/public/clubs/1/membership/invite-info?user_id={uid1}').json()
    code = info.get('inviteCode') or ''
    ok(code.startswith('IV'), '老会员拿到邀请码', code)
    rwd = info.get('reward') or {}
    ok(rwd.get('inviteeTitle') == '邀请专享券' and rwd.get('inviterTitle') == '邀请专享券'
       and rwd.get('inviterPoints') == 0, '邀请卡下发奖励说明（降级：双方同券、无积分）')
    r = c.post('/api/public/clubs/1/membership/join', json={'userId': uid3, 'inviteCode': code})
    body = r.json()
    ok(r.status_code == 200 and body.get('alreadyBound') is False, '新会员填码绑定成功', r.text[:120])
    ok(len(body.get('gifts') or []) == 2, '双方各得一张邀请券', str(len(body.get('gifts') or [])))
    with db.conn() as cc:
        ref = cc.execute('SELECT referred_by FROM club_members WHERE club_id=1 AND user_id=?', (uid3,)).fetchone()
        ok(ref and int(ref['referred_by']) == uid1, 'referred_by 落库')
    r = c.post('/api/public/clubs/1/membership/join', json={'userId': uid3, 'inviteCode': code})
    ok(r.status_code == 200 and r.json().get('alreadyBound') is True and not r.json().get('gifts'),
       '重复绑定幂等（不再发券）', r.text[:100])
    r = c.post('/api/public/clubs/1/membership/join', json={'userId': uid3, 'inviteCode': 'IVNOPE00'})
    ok(r.status_code == 404, '邀请码不存在 → 404', r.text[:80])

    print('\n── ④ 券转赠 ──')
    with db.conn() as cc:
        share_code = cc.execute("SELECT voucher_code FROM benefit_redemptions WHERE user_id=? AND gift_note='好友邀请注册赠送' LIMIT 1", (uid3,)).fetchone()
        share_code = share_code[0] if share_code else None
    ok(bool(share_code), 'uid3 有一张可转赠的邀请券', str(share_code))
    r = c.post('/api/public/clubs/1/coupons/transfer', json={'code': share_code, 'toUserId': uid1})
    ok(r.status_code == 200 and r.json().get('ok'), '好友输码领取成功（转给 uid1）', r.text[:100])
    with db.conn() as cc:
        owner = cc.execute('SELECT user_id,gift_note FROM benefit_redemptions WHERE voucher_code=?', (share_code,)).fetchone()
        ok(owner and int(owner['user_id']) == uid1 and owner['gift_note'] == '好友转赠', '券归属转移 + 标记好友转赠')
    # 转给非会员 → 400
    with db.conn() as cc:
        another = cc.execute("SELECT voucher_code FROM benefit_redemptions WHERE user_id=? AND gift_note='好友转赠' LIMIT 1", (uid1,)).fetchone()
        another = another[0] if another else None
    r = c.post('/api/public/clubs/1/coupons/transfer', json={'code': another, 'toUserId': uid4})
    ok(r.status_code == 400, '接收方不是会员 → 400', r.text[:80])
    r = c.post('/api/public/clubs/1/coupons/transfer', json={'code': 'CPN-NOPE0000', 'toUserId': uid1})
    ok(r.status_code == 404, '券码不存在 → 404')

    print('\n── ⑤ 券面自定义 ──')
    r = c.post('/api/club/1/benefits', json={
        'title': '自定义券面券', 'benefitType': 'activity_coupon', 'mode': 'fixed', 'cashValue': 15,
        'pointsCost': 10, 'stock': 3, 'pointsType': 'club', 'claimMode': 'grant',
        'face': {'c1': '#ff0000', 'c2': '#00ff00', 'ink': '#ffffff'}})
    ok(r.status_code == 200, '创建自定义券面券', r.text[:100])
    face_bid = r.json().get('benefitId')
    with db.conn() as cc:
        fj = cc.execute('SELECT face_json FROM member_benefits WHERE id=?', (face_bid,)).fetchone()
        ok(fj and fj['face_json'] and all(k in fj['face_json'] for k in ('c1', 'c2', 'ink')),
           'face_json 落库（自动大写 #RRGGBB）', str(fj['face_json'] if fj else None))
    r = c.post('/api/club/1/benefits', json={
        'title': '坏色值券', 'benefitType': 'activity_coupon', 'mode': 'fixed', 'cashValue': 15,
        'pointsCost': 10, 'stock': 3, 'pointsType': 'club', 'claimMode': 'grant',
        'face': {'c1': 'red', 'c2': '#12345'}})
    ok(r.status_code == 200, '非法色值不报错（安静处理）', r.text[:80])
    with db.conn() as cc:
        fj = cc.execute('SELECT face_json FROM member_benefits WHERE title=?', ('坏色值券',)).fetchone()
        ok(fj and not fj['face_json'], '非法 hex 全部丢弃 → face_json 为空')
    with db.conn() as cc:
        cc.execute("INSERT INTO users(name,phone,city) VALUES('券面试用户','13600000006','成都')")
        uid6 = int(cc.execute('SELECT last_insert_rowid()').fetchone()[0])
        cc.execute("INSERT INTO club_members(club_id,user_id,level) VALUES(1,?,'普通会员')", (uid6,))
        cc.execute("INSERT INTO benefit_redemptions(benefit_id,user_id,display_club_id,point_type,points_spent,funding_owner,cash_value,status,voucher_code) "
                   "VALUES(?,?,1,'club',0,'CLUB',15,'issued','CPN-FACETEST1')", (face_bid, uid6))
    mine = c.get(f'/api/public/coupons/mine?user_id={uid6}').json()
    row = next((x for x in mine if x.get('voucher_code') == 'CPN-FACETEST1'), None)
    ok(row is not None and bool(row.get('face_json')), '券包下发 face_json', str((row or {}).get('face_json')))

    print('\n── ⑥ 前端护栏 ──')
    js = (ROOT / 'static' / 'ux' / 'workflows-club.js').read_text()
    ok('cpnFaceDialog' in js and 'addTierGift' in js and 'setReferralCoupon' in js and 'activityDiscountPercent' in js,
       'workflows-club.js：设计器/升级赠券/拉新/等级折扣字段')
    me = (ROOT / 'static' / 'web' / 'me.js').read_text()
    ok("SUB_ME.invite" in me and 'meClaimByCode' in me and 'meFillInviteCode' in me and 'face_json' in me,
       'me.js：邀请页/输码领券/券面 face')
    cf = (ROOT / 'static' / 'ux' / 'coupon-faces.js').read_text()
    ok('uxCouponFaceColors' in cf and 'sakura' in cf and 'ux-cpn__wm' in cf,
       'coupon-faces.js：配色解析/新模板/水印')
    cssf = (ROOT / 'static' / 'ux' / 'coupon-faces.css').read_text()
    ok('ux-cpn-designer' in cssf and 'ux-cpn__wm' in cssf, 'coupon-faces.css：设计器样式/水印样式')
    clubjs = (ROOT / 'static' / 'club' / 'club.js').read_text()
    ok('tierRewardList' in clubjs and 'tierRewardOf' in clubjs and 'activity_discount_percent' in clubjs,
       'club.js：升级奖励总览/等级折扣')
    webjs = (ROOT / 'static' / 'web' / 'web.js').read_text()
    ok('memberDiscount' in webjs, 'web.js：订单摘要会员折扣行')
    clubhtml = (ROOT / 'static' / 'club' / 'index.html').read_text()
    ok('tierRewardList' in clubhtml and 'cardList' in clubhtml and 'inviteTabs' in clubhtml and 'coupon-faces.js?v=' in clubhtml,
       'club/index.html：升级奖励卡 + 会员卡块 + 邀请奖励三 Tab + 设计器脚本引用')
    webhtml = (ROOT / 'static' / 'web' / 'index.html').read_text()
    ok("pushMe('invite')" in webhtml and 'me.js?v=' in webhtml, 'web/index.html：邀请入口 + 带版本号')
    plathtml = (ROOT / 'static' / 'platform' / 'index.html').read_text()
    ok('coupon-faces.js?v=' in plathtml, 'platform/index.html：券面带版本号')

    print('\n' + '=' * 60)
    if FAILS:
        print(f'FAILED: {len(FAILS)} 项')
        for f in FAILS:
            print('  - ' + f)
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
