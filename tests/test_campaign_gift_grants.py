"""P6 营销赠送发放闭环 e2e（临时库 + 真实 app 导入）。

覆盖：
  1. BUY_GIFT 满额赠送三类赠品实际发放（积分 / 礼品卡额度 / 券）与幂等；
  2. INVITE 邀请有礼（被邀请 + 本俱乐部首单）发放；
  3. 成交回调统一编排 `_cs_settle_after_confirm` 在普通订单（未 opt-in 结算）也触发发放；
  4. 营销适用范围硬边界：装备 checkout_intents 不触发任何发放。

运行：CLUBOS_DB_PATH 指向临时库，import app 时 init_db 完成建表 + 播种。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix='campaign_gift_grants_')
os.environ['CLUBOS_DB_PATH'] = os.path.join(_tmp, 'test.db')
os.environ['CLUBOS_TEST_OPEN'] = '1'
os.environ['MOCK_AI'] = '0'
os.environ.setdefault('CLUBOS_SECURITY_MODE', 'demo')

import app  # noqa: E402  必须在设置 DB_PATH 之后导入
from db import conn  # noqa: E402
from clubos_domain.campaign_engine import CampaignEngine  # noqa: E402

PASS = 0
FAIL = 0


def ck(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('PASS ', name)
    else:
        FAIL += 1
        print('FAIL ', name, detail)


def seed_club_user(c, *, invited_by=None):
    """造一个俱乐部 + 一个全新用户 + 会员关系，返回 (club_id, user_id)。"""
    club = c.execute('SELECT id FROM clubs LIMIT 1').fetchone()
    if not club:
        c.execute("INSERT INTO clubs(name,status) VALUES('测试俱乐部','active')")
        club_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    else:
        club_id = club['id']
    c.execute("INSERT INTO users(name,phone) VALUES(?,?)",
              ('营销发放测试用户', '1390000%d' % (int(c.execute('SELECT COUNT(*) FROM users').fetchone()[0]) + 1)))
    uid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    if invited_by:
        c.execute('INSERT INTO club_members(club_id,user_id,referred_by) VALUES(?,?,?)',
                  (club_id, uid, invited_by))
    else:
        c.execute('INSERT OR IGNORE INTO club_members(club_id,user_id) VALUES(?,?)', (club_id, uid))
    return club_id, uid


def seed_activity(c, club_id, *, price=200.0):
    c.execute("""INSERT INTO activities(club_id,title,status,price,capacity,activity_master_json,detail_json)
                 VALUES(?,?,?,?,?,?,?)""",
              (club_id, '营销发放测试活动', 'published', price, 20, '{}', '{}'))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def add_campaign(c, club_id, type_, rules_json):
    c.execute("""INSERT INTO marketing_campaigns(owner_type,owner_club_id,type,title,status,schedule_json,scope_json,rules_json)
                 VALUES('CLUB',?,?,?,?,?,?,?)""",
              (club_id, type_, '营销发放-%s' % type_, 'ACTIVE', '{}', '{}', rules_json))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def clean_state(c):
    """清空营销发放相关的可变状态，保证每个测试独立、不互相污染（临时库单文件共享）。"""
    for t in ('campaign_grant_log', 'marketing_campaigns', 'club_point_ledger',
              'gift_card_ledger', 'gift_card_accounts', 'benefit_redemptions',
              'member_benefits', 'registrations', 'checkout_intents', 'club_members'):
        c.execute('DELETE FROM %s' % t)


def _grant(c, *, club_id, user_id, payable, activity_ids=None, order_id='O-X', order_kind='activity'):
    return app._grant_campaign_gifts(c, club_id=club_id, user_id=user_id, payable=payable,
                                     activity_ids=activity_ids, order_id=order_id, order_kind=order_kind)


def test_buy_gift_points_grant_and_idempotent():
    with conn() as c:
        clean_state(c)
        club_id, uid = seed_club_user(c)
        seed_activity(c, club_id)
        add_campaign(c, club_id, 'BUY_GIFT', '{"threshold":100,"giftType":"points","giftValue":50}')
        out = _grant(c, club_id=club_id, user_id=uid, payable=200.0, activity_ids=[1], order_id='O-PTS-1')
        bal = c.execute('SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?',
                        (club_id, uid)).fetchone()[0]
        ck('BUY_GIFT points 发放入账', bal == 50, 'balance=%s' % bal)
        ck('BUY_GIFT points 记流水', c.execute(
            'SELECT 1 FROM club_point_ledger WHERE club_id=? AND user_id=? AND source_type="CAMPAIGN_GIFT" AND source_id="O-PTS-1"',
            (club_id, uid)).fetchone() is not None)
        ck('BUY_GIFT points 幂等日志', c.execute(
            'SELECT 1 FROM campaign_grant_log WHERE club_id=? AND campaign_id=? AND order_id="O-PTS-1" AND gift_type="points"',
            (club_id, out['granted'][0]['campaignId'])).fetchone() is not None)
        # 重复发放 → 不重复加积分
        _grant(c, club_id=club_id, user_id=uid, payable=200.0, activity_ids=[1], order_id='O-PTS-1')
        bal2 = c.execute('SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?',
                         (club_id, uid)).fetchone()[0]
        ck('BUY_GIFT points 重复发放不叠加', bal2 == 50, 'balance2=%s' % bal2)


def test_buy_gift_gift_card_grant():
    with conn() as c:
        clean_state(c)
        club_id, uid = seed_club_user(c)
        seed_activity(c, club_id)
        add_campaign(c, club_id, 'BUY_GIFT', '{"threshold":100,"giftType":"gift_card","giftValue":30}')
        out = _grant(c, club_id=club_id, user_id=uid, payable=150.0, order_id='O-GC-1')
        bal = c.execute('SELECT balance_cents FROM gift_card_accounts WHERE club_id=? AND user_id=?',
                        (club_id, uid)).fetchone()[0]
        ck('BUY_GIFT gift_card 发放 3000 分', bal == 3000, 'balance_cents=%s' % bal)
        ck('BUY_GIFT gift_card 记流水', c.execute(
            'SELECT 1 FROM gift_card_ledger WHERE club_id=? AND user_id=? AND source_type="CAMPAIGN_GIFT" AND source_id="O-GC-1"',
            (club_id, uid)).fetchone() is not None)
        _grant(c, club_id=club_id, user_id=uid, payable=150.0, order_id='O-GC-1')
        bal2 = c.execute('SELECT balance_cents FROM gift_card_accounts WHERE club_id=? AND user_id=?',
                         (club_id, uid)).fetchone()[0]
        ck('BUY_GIFT gift_card 重复发放不叠加', bal2 == 3000, 'balance2=%s' % bal2)


def test_buy_gift_coupon_grant():
    with conn() as c:
        clean_state(c)
        club_id, uid = seed_club_user(c)
        seed_activity(c, club_id)
        add_campaign(c, club_id, 'BUY_GIFT', '{"threshold":100,"giftType":"coupon","giftValue":20,"maxGifts":2}')
        before = c.execute('SELECT COUNT(*) FROM benefit_redemptions').fetchone()[0]
        out = _grant(c, club_id=club_id, user_id=uid, payable=250.0, order_id='O-CPN-1')
        red = c.execute("SELECT r.* FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id "
                        "WHERE r.user_id=? AND r.display_club_id=? AND b.title LIKE '%赠送券%'",
                        (uid, club_id)).fetchall()
        ck('BUY_GIFT coupon 发 2 张', len(red) == 2, 'issued=%s reds=%s granted=%s' % (len(red), before, out))
        ck('BUY_GIFT coupon 面额正确', all(float(r['cash_value']) == 20.0 for r in red))


def test_invite_gift_for_invited_first_order():
    with conn() as c:
        clean_state(c)
        club_id, inviter = seed_club_user(c)
        club_id, uid = seed_club_user(c, invited_by=inviter)
        seed_activity(c, club_id)
        add_campaign(c, club_id, 'INVITE', '{"threshold":100,"giftType":"points","giftValue":80}')
        out = _grant(c, club_id=club_id, user_id=uid, payable=200.0, order_id='O-INV-1')
        bal = c.execute('SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?',
                        (club_id, uid)).fetchone()[0]
        ck('INVITE 被邀请首单发放积分', bal == 80, 'balance=%s out=%s' % (bal, out))


def test_invite_skips_non_invited():
    with conn() as c:
        clean_state(c)
        club_id, uid = seed_club_user(c)
        seed_activity(c, club_id)
        add_campaign(c, club_id, 'INVITE', '{"threshold":100,"giftType":"points","giftValue":80}')
        out = _grant(c, club_id=club_id, user_id=uid, payable=200.0, order_id='O-INV-2')
        bal = c.execute('SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?',
                        (club_id, uid)).fetchone()[0]
        ck('INVITE 未邀请不发放', bal == 0 and not out['granted'], 'out=%s' % out)


def test_confirm_hook_grants_without_settlement_opt_in():
    """普通活动订单（未 opt-in 结算，无 checkout_settlements 记录）成交后也发放。"""
    with conn() as c:
        clean_state(c)
        club_id, uid = seed_club_user(c)
        aid = seed_activity(c, club_id)
        add_campaign(c, club_id, 'BUY_GIFT', '{"threshold":100,"giftType":"points","giftValue":60}')
        c.execute("""INSERT INTO checkout_intents(id,kind,user_id,club_id,status,cash_amount,payload_json)
                     VALUES('CK-HOOK-1','activity',?,?,'pending_payment',200.0,'{}')""", (uid, club_id))
        c.execute("""INSERT INTO registrations(id,activity_id,occurrence_id,club_id,user_id,status,original_amount,amount,payment_status)
                     VALUES(?,?,NULL,?,?,'paid',200.0,200.0,'succeeded')""", (900001, aid, club_id, uid))
        out = app._cs_settle_after_confirm(c, intent_id='CK-HOOK-1', result={'registrationId': 900001})
        bal = c.execute('SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?',
                        (club_id, uid)).fetchone()[0]
        ck('成交回调普通订单也发放', bal == 60, 'balance=%s out=%s' % (bal, out))
        ck('成交回调发放幂等日志按订单号', c.execute(
            'SELECT 1 FROM campaign_grant_log WHERE club_id=? AND order_id="900001" AND gift_type="points"',
            (club_id,)).fetchone() is not None)


def test_gear_intent_never_grants():
    """营销适用范围硬边界：装备 checkout_intents 不触发发放。"""
    with conn() as c:
        clean_state(c)
        club_id, uid = seed_club_user(c)
        add_campaign(c, club_id, 'BUY_GIFT', '{"threshold":100,"giftType":"points","giftValue":50}')
        c.execute("""INSERT INTO checkout_intents(id,kind,user_id,club_id,status,cash_amount,payload_json)
                     VALUES('CK-GEAR-1','gear',?,?,'pending_payment',200.0,'{}')""", (uid, club_id))
        out = app._grant_campaign_gifts_for_intent(c, intent_id='CK-GEAR-1', result={})
        ck('装备订单不触发发放', out.get('skipped') and out.get('reason') == 'not-club-order', 'out=%s' % out)
        bal = c.execute('SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?',
                        (club_id, uid)).fetchone()[0]
        ck('装备订单无积分入账', bal == 0, 'balance=%s' % bal)


if __name__ == '__main__':
    test_buy_gift_points_grant_and_idempotent()
    test_buy_gift_gift_card_grant()
    test_buy_gift_coupon_grant()
    test_invite_gift_for_invited_first_order()
    test_invite_skips_non_invited()
    test_confirm_hook_grants_without_settlement_opt_in()
    test_gear_intent_never_grants()
    print('---')
    print('PASS %d  FAIL %d' % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
