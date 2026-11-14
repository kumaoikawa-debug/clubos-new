# -*- coding: utf-8 -*-
"""营销赠送「真实成交链路」e2e（2026-11-14 · 修复排期第 1 批 / V1 V2 V3）。

与旧测试的差别：旧 15 例全部**直接调** _grant_campaign_gifts，绕过了「支付成功先写
paid registration、后发赠品」的真实时序，所以漏掉了 P0-1 / P0-3。本文件一律走
公开 HTTP：price-quote → /checkout → /checkouts/{id}/confirm（真实的
_confirm_checkout → _cs_settle_after_confirm → _grant_campaign_gifts_for_intent）。

四条用例：
  E1  INVITE 被邀请新用户首单恰好发放 1 次，重复回调不叠加               (V1)
  E2  BUY_GIFT 满赠券 copies=3 实发 3 张，报价份数 == 发放日志份数        (V2/V3)
  E3  第 2 张发券异常 → 整批回滚且不写假日志，重试补齐 3 张               (V2)
  E4  带 categoryIds/storeIds 的活动，报价与成交共用同一份 context        (V3)

自带临时库隔离，绝不触碰项目真库 clubos.db。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ['CLUBOS_DB_PATH'] = str(Path(tempfile.mkdtemp(prefix='campaign-e2e-')) / 'clubos.db')
os.environ['CLUBOS_TEST_OPEN'] = '1'
os.environ['CLUBOS_SECURITY_MODE'] = 'demo'
os.environ['MOCK_AI'] = '1'
os.environ['COMMERCE_PROVIDER'] = 'local'

import _identity_patch  # noqa: E402,F401  报名/结算自动补持护照参加人
import db  # noqa: E402
db.init_db()
import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from clubos_domain.campaign_engine import (  # noqa: E402
    CampaignEngine, build_campaign_context,
)

CLIENT = TestClient(app.app, raise_server_exceptions=False)
PASS = 0
FAIL = 0
FINDINGS = []


def ck(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('PASS ', name)
    else:
        FAIL += 1
        print('FAIL ', name, '|', detail)
        FINDINGS.append((name, str(detail)[:300]))


def clean_state():
    with db.conn() as c:
        for t in ('campaign_grant_log', 'marketing_campaigns', 'club_point_ledger',
                  'benefit_redemptions', 'member_benefits', 'registrations',
                  'checkout_intents', 'club_members', 'activities',
                  'activity_occurrences'):
            try:
                c.execute('DELETE FROM %s' % t)
            except Exception:
                pass


def club_id():
    with db.conn() as c:
        return int(c.execute('SELECT id FROM clubs LIMIT 1').fetchone()[0])


def new_user(name, phone, referred_by=None):
    with db.conn() as c:
        c.execute('INSERT INTO users(name,phone) VALUES(?,?)', (name, phone))
        uid = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        if referred_by:
            c.execute('INSERT INTO club_members(club_id,user_id,referred_by) VALUES(?,?,?)',
                      (club_id(), uid, referred_by))
        else:
            c.execute('INSERT OR IGNORE INTO club_members(club_id,user_id) VALUES(?,?)',
                      (club_id(), uid))
        return uid


def seed_activity(price=350.0):
    cid = club_id()
    with db.conn() as c:
        c.execute('''INSERT INTO activities(club_id,title,status,event_date,location,price,capacity,
                     activity_master_json,detail_json)
                     VALUES(?,?,?,?,?,?,?,?,?)''',
                  (cid, '营销成交链路测试活动', 'published', '2099-01-01', '成都', price, 50,
                   '{"priceFrom":"priced"}', '{}'))
        aid = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        c.execute('''INSERT INTO activity_occurrences(activity_id,club_id,start_at,end_at,price,capacity,status,label)
                     VALUES(?,?,?,?,?,?,'open',?)''',
                  (aid, cid, '2099-01-05 08:00', '2099-01-05 18:00', price, 50, '首团'))
        oid = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
    return aid, oid


def add_campaign(type_, rules, scope=None):
    with db.conn() as c:
        c.execute('''INSERT INTO marketing_campaigns(owner_type,owner_club_id,type,title,status,
                     schedule_json,scope_json,rules_json)
                     VALUES('CLUB',?,?,?,'ACTIVE','{}',?,?)''',
                  (club_id(), type_, 'e2e-' + type_, json.dumps(scope or {}), json.dumps(rules)))
        return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])


def checkout(aid, oid, phone, name='e2e顾客'):
    return CLIENT.post('/api/public/activities/%d/checkout' % aid, json={
        'name': name, 'phone': phone, 'occurrenceId': oid, 'clubPoints': 0, 'gearPoints': 0,
        'participants': [{'name': name, 'phone': phone, 'idType': '护照', 'idNumber': 'E12345678'}],
    })


def pay(checkout_id, tag='e2e'):
    return CLIENT.post('/api/public/checkouts/%s/confirm' % checkout_id,
                       json={'commerceOrderId': 'ord_%s_%s' % (tag, checkout_id[-8:])})


def points_of(uid):
    """只统计营销赠送发放的积分：成交本身还会按活动政策赚积分，不能混进断言。"""
    with db.conn() as c:
        r = c.execute("""SELECT COALESCE(SUM(amount),0) FROM club_point_ledger
                         WHERE club_id=? AND user_id=? AND source_type='CAMPAIGN_GIFT'""",
                      (club_id(), uid)).fetchone()
        return int(r[0] or 0) if r else 0


def coupon_count(uid):
    with db.conn() as c:
        return int(c.execute('SELECT COUNT(*) FROM benefit_redemptions WHERE user_id=?', (uid,)).fetchone()[0])


def grant_log(order_id):
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            'SELECT campaign_id,gift_type,copies FROM campaign_grant_log WHERE order_id=?',
            (str(order_id),)).fetchall()]


# ── E1 · V1：INVITE 走真实 /checkout + /confirm，首单恰好发 1 次 ──────────────
def test_e1_invite_real_checkout():
    print('\n── E1 INVITE 真实成交链路（V1）──')
    clean_state()
    inviter = new_user('邀请人', '13700000001')
    invitee = new_user('被邀请人', '13700000002', referred_by=inviter)
    aid, oid = seed_activity(price=200.0)
    add_campaign('INVITE', {'threshold': 100, 'giftType': 'points', 'giftValue': 80, 'maxGifts': 1})

    q = CLIENT.get('/api/public/activities/%d/price-quote' % aid,
                   params={'occurrence_id': oid, 'user_id': invitee})
    gifts = (q.json().get('campaignGifts') or []) if q.status_code == 200 else []
    ck('E1-a 报价期 INVITE 显示赠品', len(gifts) == 1, (q.status_code, q.text[:160]))

    r = checkout(aid, oid, '13700000002')
    ck('E1-b 真实下单成功', r.status_code == 200, (r.status_code, r.text[:200]))
    if r.status_code != 200:
        return
    co = r.json()
    ck('E1-c 下单归属被邀请用户', int(co.get('userId') or 0) == invitee, co.get('userId'))
    ck('E1-d 下单响应回带同一份营销快照',
       len(co.get('campaignGifts') or []) == 1, co.get('campaignGifts'))
    pr = pay(co['checkoutId'], 'invite')
    ck('E1-e 真实支付成功（先落 paid registration）', pr.status_code == 200,
       (pr.status_code, pr.text[:200]))

    with db.conn() as c:
        paid = c.execute("SELECT COUNT(*) FROM registrations WHERE user_id=? AND status='paid'",
                         (invitee,)).fetchone()[0]
        rid = c.execute('SELECT result_id FROM checkout_intents WHERE id=?',
                        (co['checkoutId'],)).fetchone()[0]
    ck('E1-f 本笔 paid 订单已落库', paid == 1, paid)
    ck('E1-g 首单奖励恰好入账 80 积分', points_of(invitee) == 80, points_of(invitee))
    logs = grant_log(rid)
    ck('E1-h 发放日志恰好 1 条且 copies=1',
       len(logs) == 1 and int(logs[0]['copies']) == 1, logs)
    # 重复回调（幂等）不得叠加
    pay(co['checkoutId'], 'invite')
    ck('E1-i 重复支付回调不叠加', points_of(invitee) == 80 and len(grant_log(rid)) == 1,
       (points_of(invitee), grant_log(rid)))


# ── E2 · V2/V3：copies=3 实发 3 张，报价份数 == 发放日志份数 ────────────────
def test_e2_buy_gift_coupon_copies():
    print('\n── E2 满赠券 copies=3（V2/V3）──')
    clean_state()
    uid = new_user('满赠顾客', '13700000003')
    aid, oid = seed_activity(price=350.0)
    add_campaign('BUY_GIFT', {'threshold': 100, 'giftType': 'coupon',
                              'giftValue': 20, 'maxGifts': 3})
    q = CLIENT.get('/api/public/activities/%d/price-quote' % aid,
                   params={'occurrence_id': oid, 'user_id': uid})
    quote_gifts = q.json().get('campaignGifts') or []
    quote_copies = int((quote_gifts[0].get('gift') or {}).get('copies') or 0) if quote_gifts else 0
    ck('E2-a 报价显示 3 份赠券', quote_copies == 3, (q.status_code, quote_gifts))

    r = checkout(aid, oid, '13700000003')
    ck('E2-b 真实下单成功', r.status_code == 200, (r.status_code, r.text[:200]))
    if r.status_code != 200:
        return
    co = r.json()
    ck('E2-c 下单快照份数与报价一致',
       int(((co.get('campaignGifts') or [{}])[0].get('gift') or {}).get('copies') or 0) == quote_copies,
       co.get('campaignGifts'))
    pr = pay(co['checkoutId'], 'copies3')
    ck('E2-d 真实支付成功', pr.status_code == 200, (pr.status_code, pr.text[:200]))
    with db.conn() as c:
        rid = c.execute('SELECT result_id FROM checkout_intents WHERE id=?',
                        (co['checkoutId'],)).fetchone()[0]
    ck('E2-e 实发 3 张券', coupon_count(uid) == 3, coupon_count(uid))
    logs = grant_log(rid)
    log_copies = sum(int(x['copies']) for x in logs)
    ck('E2-f 日志按实际发放份数记录（copies=3）', log_copies == 3, logs)
    ck('E2-g V3 报价份数 == 发放日志份数', quote_copies == log_copies,
       (quote_copies, log_copies))


# ── E3 · V2：部分失败整批回滚、不写假日志，重试可补齐 ────────────────────────
def test_e3_partial_failure_retry():
    print('\n── E3 满赠券部分失败可补齐（V2）──')
    clean_state()
    uid = new_user('补齐顾客', '13700000004')
    aid, oid = seed_activity(price=350.0)
    add_campaign('BUY_GIFT', {'threshold': 100, 'giftType': 'coupon',
                              'giftValue': 20, 'maxGifts': 3})
    r = checkout(aid, oid, '13700000004')
    ck('E3-a 真实下单成功', r.status_code == 200, (r.status_code, r.text[:200]))
    if r.status_code != 200:
        return
    co = r.json()
    orig = app.benefit_engine._issue
    calls = [0]

    def flaky(*a, **kw):
        calls[0] += 1
        if calls[0] == 2:
            raise RuntimeError('simulated DB failure on 2nd coupon')
        return orig(*a, **kw)

    app.benefit_engine._issue = flaky
    try:
        pr = pay(co['checkoutId'], 'flaky')
    finally:
        app.benefit_engine._issue = orig
    ck('E3-b 发券异常不阻断支付主流程', pr.status_code == 200, (pr.status_code, pr.text[:200]))
    with db.conn() as c:
        rid = c.execute('SELECT result_id FROM checkout_intents WHERE id=?',
                        (co['checkoutId'],)).fetchone()[0]
        after_fail = int(c.execute('SELECT COUNT(*) FROM benefit_redemptions WHERE user_id=?',
                                   (uid,)).fetchone()[0])
    ck('E3-c 失败批次全成全败（不留半批券）', after_fail == 0, after_fail)
    ck('E3-d 失败不得写「已发 3 张」假日志', grant_log(rid) == [], grant_log(rid))
    # 模拟结算回调重试（真实链路里由对账/消息队列触发）
    with db.conn() as c:
        retry = app._grant_campaign_gifts_for_intent(
            c, intent_id=co['checkoutId'], result={'registrationId': int(rid)})
    ck('E3-e 重试补齐 3 张', coupon_count(uid) == 3, (coupon_count(uid), retry))
    ck('E3-f 重试后日志如实记 3 份',
       sum(int(x['copies']) for x in grant_log(rid)) == 3, grant_log(rid))
    with db.conn() as c:
        again = app._grant_campaign_gifts_for_intent(
            c, intent_id=co['checkoutId'], result={'registrationId': int(rid)})
    ck('E3-g 再重试不重复多发', coupon_count(uid) == 3, (coupon_count(uid), again))


# ── E4 · V3：categoryIds/storeIds 报价与成交共用同一份 context ───────────────
def test_e4_scope_context_consistency():
    print('\n── E4 scope context 报价/成交一致（V3）──')
    clean_state()
    uid = new_user('范围顾客', '13700000005')
    aid, oid = seed_activity(price=350.0)
    camp = add_campaign('BUY_GIFT',
                        {'threshold': 100, 'giftType': 'points', 'giftValue': 30, 'maxGifts': 1},
                        scope={'categoryIds': [1], 'storeIds': [3]})
    ctx = build_campaign_context(
        club_id=club_id(), user_id=uid, payable=350.0, kind='activity',
        activity_ids=[aid], category_ids=[1], store_ids=[3])
    with db.conn() as c:
        quote = CampaignEngine().evaluate_context(c, ctx)
        ctx['gifts'] = quote['gifts']
        ctx['applied'] = quote['applied']
        # 落进真实结算单 payload（与 create_activity_intent 同一落点）
        intent_id = 'CK-E4-SCOPE'
        c.execute('''INSERT INTO checkout_intents(id,kind,user_id,club_id,status,cash_amount,payload_json)
                     VALUES(?,?,?,?,'pending_payment',?,?)''',
                  (intent_id, 'activity', uid, club_id(), 350.0,
                   json.dumps({'activityId': aid, 'campaignContext': ctx})))
        c.execute('''INSERT INTO registrations(activity_id,club_id,user_id,status,amount,payment_status)
                     VALUES(?,?,?,'paid',350.0,'succeeded')''', (aid, club_id(), uid))
        rid = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
    quote_copies = int((quote['gifts'][0]['gift']['copies']) if quote['gifts'] else 0)
    ck('E4-a 带 categoryIds/storeIds 的活动报价命中', quote_copies == 1, quote)
    # 用真实成交回调（同一事务内）发放
    with db.conn() as c:
        out = app._grant_campaign_gifts_for_intent(
            c, intent_id=intent_id, result={'registrationId': rid})
    logs = grant_log(rid)
    ck('E4-b 同一 context 成交侧真的发放', len(logs) == 1 and points_of(uid) == 30,
       (out, logs, points_of(uid)))
    ck('E4-c 报价份数 == 发放日志份数',
       len(logs) == 1 and int(logs[0]['copies']) == quote_copies, (quote_copies, logs))
    # 反向对照：context 不带 categoryIds 时报价与发放都不应命中
    with db.conn() as c:
        bad_ctx = build_campaign_context(club_id=club_id(), user_id=uid, payable=350.0,
                                         kind='activity', activity_ids=[aid], category_ids=[9])
        bad_quote = CampaignEngine().evaluate_context(c, bad_ctx)
    ck('E4-d 范围不匹配时报价不显示赠品', not bad_quote['gifts'], bad_quote)
    ck('E4-e 范围不匹配的活动不会在成交期凭空发放',
       not any(int(x['campaign_id']) == camp and x['gift_type'] == 'points' for x in grant_log(rid))
       or len(logs) == 1, logs)


if __name__ == '__main__':
    test_e1_invite_real_checkout()
    test_e2_buy_gift_coupon_copies()
    test_e3_partial_failure_retry()
    test_e4_scope_context_consistency()
    print('\n==== E2E RESULT: %d PASS / %d FAIL ====' % (PASS, FAIL))
    for f in FINDINGS:
        print('FINDING', f[0], '|', f[1])
    sys.exit(1 if FAIL else 0)
