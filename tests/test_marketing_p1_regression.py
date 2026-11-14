# -*- coding: utf-8 -*-
"""修复排期第 2 批验收用例：V4 / V5 / V6 / V7 / V8（2026-11-14）。

V4  俱乐部对 PLATFORM 活动 PATCH/DELETE 返回 403/404，状态不被改
V5  PATCH 活动类型正确落库（规则不合法则明确 400），不允许「200 但没改」
V6  TIME_LIMIT 起止时段 + 本地时区（UTC+8）判定，窗口外不生效
V7  settings-hub 的 campaigns 模块返回真实数值，unknownModules=0
V8  C 端结算明细与订单详情可见活动优惠名称、金额与赠品明细

自带临时库隔离，绝不触碰项目真库 clubos.db。
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ['CLUBOS_DB_PATH'] = str(Path(tempfile.mkdtemp(prefix='campaign-p1-')) / 'clubos.db')
os.environ['CLUBOS_TEST_OPEN'] = '1'
os.environ['CLUBOS_SECURITY_MODE'] = 'demo'
os.environ['MOCK_AI'] = '1'
os.environ['COMMERCE_PROVIDER'] = 'local'

import _identity_patch  # noqa: E402,F401
import db  # noqa: E402
db.init_db()
import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from clubos_domain.campaign_engine import CampaignEngine, _schedule_boundary  # noqa: E402
from clubos_domain import settings_hub  # noqa: E402

CLIENT = TestClient(app.app, raise_server_exceptions=False)
CN_TZ = timezone(timedelta(hours=8))
PASS = FAIL = 0
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


def cid():
    with db.conn() as c:
        return int(c.execute('SELECT id FROM clubs LIMIT 1').fetchone()[0])


def clean():
    with db.conn() as c:
        for t in ('campaign_grant_log', 'marketing_campaigns', 'club_point_ledger',
                  'benefit_redemptions', 'member_benefits', 'registrations',
                  'checkout_intents', 'activities', 'activity_occurrences', 'users'):
            try:
                c.execute('DELETE FROM %s' % t)
            except Exception:
                pass


def new_user(phone='13800000001'):
    with db.conn() as c:
        c.execute('INSERT INTO users(name,phone) VALUES(?,?)', ('P1顾客', phone))
        return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])


def seed_activity(price=300.0):
    with db.conn() as c:
        c.execute('''INSERT INTO activities(club_id,title,status,event_date,location,price,capacity,
                     activity_master_json,detail_json)
                     VALUES(?,?,?,?,?,?,?,?,?)''',
                  (cid(), 'P1时段测试活动', 'published', '2099-01-01', '成都', price, 50,
                   '{"priceFrom":"priced"}', '{}'))
        aid = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        c.execute('''INSERT INTO activity_occurrences(activity_id,club_id,start_at,end_at,price,capacity,status,label)
                     VALUES(?,?,?,?,?,?,'open',?)''',
                  (aid, cid(), '2099-01-05 08:00', '2099-01-05 18:00', price, 50, '首团'))
        oid = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
    return aid, oid


def post_campaign(payload):
    return CLIENT.post('/api/club/%d/campaigns' % cid(), json=payload)


# ── V4 · PLATFORM 活动越权 ───────────────────────────────────────────────────
def test_v4_platform_campaign_permission():
    print('\n── V4 PLATFORM 活动权限边界 ──')
    clean()
    with db.conn() as c:
        c.execute('''INSERT INTO marketing_campaigns(owner_type,owner_club_id,type,title,status,
                     schedule_json,scope_json,rules_json)
                     VALUES('PLATFORM',NULL,'BUY_GIFT','平台活动','ACTIVE','{}','{}',?)''',
                  (json.dumps({'threshold': 10, 'giftType': 'points', 'giftValue': 5}),))
        pid = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
    r = CLIENT.patch('/api/club/%d/campaigns/%d' % (cid(), pid), json={'title': '越权改名'})
    ck('V4-a 俱乐部 PATCH 平台活动返回 403（非 500）', r.status_code == 403,
       (r.status_code, r.text[:160]))
    r2 = CLIENT.delete('/api/club/%d/campaigns/%d' % (cid(), pid))
    ck('V4-b 俱乐部 DELETE 平台活动返回 403', r2.status_code == 403,
       (r2.status_code, r2.text[:160]))
    with db.conn() as c:
        row = c.execute('SELECT status,title FROM marketing_campaigns WHERE id=?', (pid,)).fetchone()
    ck('V4-c 平台活动状态与名称未被修改',
       row['status'] == 'ACTIVE' and row['title'] == '平台活动', dict(row))


# ── V5 · PATCH 类型 ──────────────────────────────────────────────────────────
def test_v5_patch_type():
    print('\n── V5 PATCH 活动类型 ──')
    clean()
    r = post_campaign({'type': 'NEWCOMER', 'title': '类型测试', 'status': 'ACTIVE',
                       'schedule': {}, 'scope': {},
                       'rules': {'mode': 'fixed', 'amount': 10}})
    ck('V5-a 创建 NEWCOMER 成功', r.status_code == 200, (r.status_code, r.text[:160]))
    if r.status_code != 200:
        return
    new_id = int(r.json().get('id'))
    r2 = CLIENT.patch('/api/club/%d/campaigns/%d' % (cid(), new_id),
                      json={'type': 'TIME_LIMIT', 'title': '类型已改'})
    lst = CLIENT.get('/api/club/%d/campaigns' % cid()).json()
    cur = next((x for x in lst if int(x.get('id') or 0) == new_id), {})
    ck('V5-b PATCH 返回 200', r2.status_code == 200, (r2.status_code, r2.text[:160]))
    ck('V5-c type 真的落库为 TIME_LIMIT', cur.get('type') == 'TIME_LIMIT', cur.get('type'))
    ck('V5-d 同一次 PATCH 的 title 也生效', cur.get('title') == '类型已改', cur.get('title'))
    # 规则与新类型不兼容时必须明确 400，而不是静默落库一个跑不起来的活动
    r3 = CLIENT.patch('/api/club/%d/campaigns/%d' % (cid(), new_id),
                      json={'type': 'BUY_GIFT'})
    ck('V5-e 改类型但规则不合法 → 明确 400', r3.status_code == 400,
       (r3.status_code, r3.text[:160]))


# ── V6 · 时段与本地时区 ──────────────────────────────────────────────────────
def test_v6_schedule_local_timezone():
    print('\n── V6 TIME_LIMIT 时段 / 本地时区 ──')
    clean()
    uid = new_user('13800000002')
    aid, oid = seed_activity(price=300.0)
    now = int(datetime.now(CN_TZ).timestamp())
    local = datetime.fromtimestamp(now, tz=CN_TZ)
    cur_min = local.hour * 60 + local.minute

    def quote():
        r = CLIENT.get('/api/public/activities/%d/price-quote' % aid,
                       params={'occurrence_id': oid, 'user_id': uid})
        return r.json() if r.status_code == 200 else {}

    # ① 过期窗口：不生效
    out = post_campaign({'type': 'TIME_LIMIT', 'title': '过期窗口', 'status': 'ACTIVE',
                         'schedule': {'startAt': now - 7200, 'endAt': now - 3600},
                         'scope': {}, 'rules': {'mode': 'fixed', 'amount': 30}})
    ck('V6-a 创建过期窗口活动', out.status_code == 200, out.text[:160])
    q = quote()
    ck('V6-b 窗口外不生效（无活动优惠）', float(q.get('campaignDiscount') or 0) == 0,
       q.get('campaignDiscount'))
    # ② 生效窗口：生效
    with db.conn() as c:
        c.execute("UPDATE marketing_campaigns SET schedule_json=? WHERE type='TIME_LIMIT'",
                  (json.dumps({'startAt': now - 3600, 'endAt': now + 3600}),))
    q = quote()
    ck('V6-c 窗口内生效（立减 30）', float(q.get('campaignDiscount') or 0) == 30,
       q.get('campaignDiscount'))
    # ③ 本地时段：包含当前北京时刻 → 生效；包含当前 UTC 时刻但排除北京时刻 → 不生效
    with db.conn() as c:
        c.execute("UPDATE marketing_campaigns SET schedule_json=? WHERE type='TIME_LIMIT'",
                  (json.dumps({'timeFrom': max(0, cur_min - 60), 'timeTo': min(1439, cur_min + 60)}),))
    q = quote()
    ck('V6-d 每日时段按北京时间命中', float(q.get('campaignDiscount') or 0) == 30,
       (cur_min, q.get('campaignDiscount')))
    utc_min = datetime.fromtimestamp(now, tz=timezone.utc).hour * 60
    with db.conn() as c:
        c.execute("UPDATE marketing_campaigns SET schedule_json=? WHERE type='TIME_LIMIT'",
                  (json.dumps({'timeFrom': max(0, utc_min - 30), 'timeTo': min(1439, utc_min + 30)}),))
    q = quote()
    ck('V6-e 用 UTC 时段错窗不再误生效', float(q.get('campaignDiscount') or 0) == 0,
       (utc_min, cur_min, q.get('campaignDiscount')))
    # ④ 边界字符串按本地时区解析
    s = _schedule_boundary('2026-11-14 20:00')
    expect = int(datetime(2026, 11, 14, 20, 0, tzinfo=CN_TZ).timestamp())
    ck('V6-f 本地时间字符串按 UTC+8 解析', s == expect, (s, expect))
    e = _schedule_boundary('2026-11-14', is_end=True)
    expect_e = int(datetime(2026, 11, 14, 23, 59, 59, tzinfo=CN_TZ).timestamp())
    ck('V6-g 纯日期的 endAt 取当天 23:59:59', e == expect_e, (e, expect_e))
    # ⑤ 空 schedule 语义明确：引擎长期生效 + 后台列表标注
    ck('V6-h 空 schedule 引擎语义为长期生效', CampaignEngine()._schedule_ok({}, now) is True)
    js = (ROOT / 'static/club/club.js').read_text(encoding='utf-8')
    ck('V6-i 后台列表明确标注「未设时段 · 长期生效」', '未设时段 · 长期生效' in js)
    wf = (ROOT / 'static/ux/workflows-club.js').read_text(encoding='utf-8')
    ck('V6-j 创建表单不再写死 schedule:{}', "status:'ACTIVE',schedule,scope:{},rules" in wf)


# ── V7 · settings-hub campaigns probe ───────────────────────────────────────
def test_v7_settings_hub_campaigns():
    print('\n── V7 设置中枢营销模块 ──')
    clean()
    post_campaign({'type': 'BUY_GIFT', 'title': '中枢计数', 'status': 'ACTIVE',
                   'schedule': {}, 'scope': {},
                   'rules': {'threshold': 100, 'giftType': 'points', 'giftValue': 10}})
    r = CLIENT.get('/api/club/%d/settings-hub' % cid())
    ck('V7-a settings-hub 返回 200', r.status_code == 200, (r.status_code, r.text[:160]))
    if r.status_code != 200:
        return
    data = r.json()
    mods = {m['key']: m for m in data.get('modules', [])}
    m = mods.get('campaigns') or {}
    ck('V7-b campaigns.configured 是真实布尔值（不是 None）', m.get('configured') is True, m)
    ck('V7-c campaigns.count 返回真实数值 1', m.get('count') == 1, m.get('count'))
    _unknown = (data.get('summary') or {}).get('unknownModules')
    ck('V7-d unknownModules=0', _unknown is not None and int(_unknown) == 0,
       data.get('summary'))
    with db.conn() as c:
        broken = settings_hub.probe_selfcheck(c, cid())
    ck('V7-e 所有 probe SQL 自检通过', broken == [], broken)


# ── V8 · C 端营销明细 ────────────────────────────────────────────────────────
def test_v8_c_side_marketing_detail():
    print('\n── V8 C 端活动优惠 / 赠品明细 ──')
    clean()
    uid = new_user('13800000003')
    aid, oid = seed_activity(price=300.0)
    post_campaign({'type': 'TIME_LIMIT', 'title': '周末立减20', 'status': 'ACTIVE',
                   'schedule': {}, 'scope': {}, 'rules': {'mode': 'fixed', 'amount': 20}})
    post_campaign({'type': 'BUY_GIFT', 'title': '满200送50积分', 'status': 'ACTIVE',
                   'schedule': {}, 'scope': {},
                   'rules': {'threshold': 200, 'giftType': 'points', 'giftValue': 50, 'maxGifts': 1}})
    r = CLIENT.post('/api/public/activities/%d/checkout' % aid, json={
        'name': 'P1顾客', 'phone': '13800000003', 'occurrenceId': oid,
        'clubPoints': 0, 'gearPoints': 0,
        'participants': [{'name': 'P1顾客', 'phone': '13800000003',
                          'idType': '护照', 'idNumber': 'E12345678'}]})
    ck('V8-a 真实下单成功', r.status_code == 200, (r.status_code, r.text[:200]))
    if r.status_code != 200:
        return
    co = r.json()
    ck('V8-b 下单响应下发活动优惠与赠品',
       float(co.get('campaignDiscount') or 0) == 20 and len(co.get('campaignGifts') or []) == 1,
       (co.get('campaignDiscount'), co.get('campaignGifts')))
    pr = CLIENT.post('/api/public/checkouts/%s/confirm' % co['checkoutId'],
                     json={'commerceOrderId': 'ord_p1_v8'})
    ck('V8-c 真实支付成功', pr.status_code == 200, (pr.status_code, pr.text[:200]))
    oc = CLIENT.get('/api/public/users/%d/order-center?club_id=%d' % (uid, cid()))
    ck('V8-d order-center 返回 200', oc.status_code == 200, (oc.status_code, oc.text[:160]))
    if oc.status_code != 200:
        return
    orders = oc.json().get('activityOrders') or []
    o = orders[0] if orders else {}
    ck('V8-e 订单详情带活动优惠金额', float(o.get('campaignDiscount') or 0) == 20,
       o.get('campaignDiscount'))
    ck('V8-f 订单详情带活动名称',
       any('周末立减20' in str(x.get('title')) for x in (o.get('campaignsApplied') or [])),
       o.get('campaignsApplied'))
    ck('V8-g 订单详情带实发赠品明细',
       any(int(x.get('copies') or 0) >= 1 for x in (o.get('grantedGifts') or [])),
       o.get('grantedGifts'))
    web = (ROOT / 'static/web/web.js').read_text(encoding='utf-8')
    me = (ROOT / 'static/web/me.js').read_text(encoding='utf-8')
    ck('V8-h C 端 JS 渲染 campaignDiscount/campaignsApplied/campaignGifts',
       all(k in web for k in ('campaignDiscount', 'campaignsApplied', 'campaignGifts')),
       [k for k in ('campaignDiscount', 'campaignsApplied', 'campaignGifts') if k not in web])
    ck('V8-i 订单详情渲染「本单获得」', '本单获得' in me and 'grantedGifts' in me)


if __name__ == '__main__':
    test_v4_platform_campaign_permission()
    test_v5_patch_type()
    test_v6_schedule_local_timezone()
    test_v7_settings_hub_campaigns()
    test_v8_c_side_marketing_detail()
    print('\n==== P1 RESULT: %d PASS / %d FAIL ====' % (PASS, FAIL))
    for f in FINDINGS:
        print('FINDING', f[0], '|', f[1])
    sys.exit(1 if FAIL else 0)
