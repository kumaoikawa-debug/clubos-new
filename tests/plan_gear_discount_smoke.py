# -*- coding: utf-8 -*-
"""装备折扣归属迁移 + 积分权限边界的冒烟测试（2026-10-23 用户反馈两件事）。

① 装备折扣从「俱乐部会员等级」迁到「平台订阅套餐」：
   - ai_credit_plans.gear_discount 由总平台控制（平台可改、俱乐部不可改）；
   - 会员折扣 = 俱乐部当前生效套餐的折扣（需为本俱乐部会员），等级不再参与；
   - 俱乐部等级接口收到 gearDiscount 一律忽略，绝不落库；
   - 一次性迁移回填 starter 1.0 / pro 0.95 / enterprise 0.90（platform_settings 标记防重跑）。
② 俱乐部端积分调整只允许活动积分：kind=gear 一律 403，装备积分只能由总平台调整。

自带临时库隔离（CLUBOS_DB_PATH 指到 clubos_plan_gear_test.db），不需要外部传入。
"""
import sys as _sys; _sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'tests'))
import _identity_patch  # noqa: F401
from pathlib import Path
import os, sys, sqlite3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['MOCK_AI'] = '1'
os.environ.setdefault('CLUBOS_TEST_OPEN', '1')
os.environ['CLUBOS_DB_PATH'] = str(ROOT / 'clubos_plan_gear_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c = TestClient(app.app)

PASS = []
def check(name, cond, detail=''):
    assert cond, f'FAIL {name} {detail}'
    PASS.append(name); print('  ok -', name)

def ok(method, url, **kw):
    r = getattr(c, method)(url, **kw)
    assert r.status_code < 400, (url, r.status_code, r.text)
    return r

def db():
    d = sqlite3.connect(os.environ['CLUBOS_DB_PATH']); d.row_factory = sqlite3.Row
    return d

# ── 1) 套餐种子自带装备折扣档位 ────────────────────────────────────────────
plans = {p['code']: p for p in ok('get', '/api/platform/ai-credit/plans').json()}
check('套餐种子含 gear_discount 字段', all('gear_discount' in p for p in plans.values()), list(plans))
check('Starter 无折扣 1.0', float(plans['starter']['gear_discount']) == 1.0, plans['starter']['gear_discount'])
check('Pro 9.5 折', abs(float(plans['pro']['gear_discount']) - 0.95) < 1e-9, plans['pro']['gear_discount'])
check('Enterprise 9 折', abs(float(plans['enterprise']['gear_discount']) - 0.90) < 1e-9, plans['enterprise']['gear_discount'])

# 迁移一次性标记已落
con = db()
check('迁移标记 plan_gear_discount_backfilled 已写',
      con.execute("SELECT 1 FROM platform_settings WHERE key='plan_gear_discount_backfilled'").fetchone() is not None)

# ── 2) 俱乐部只读端点：折扣=当前订阅套餐（club1 = pro）──────────────────────
gd = ok('get', '/api/club/1/gear-discount').json()
check('club1 折扣率=0.95', abs(gd['rate'] - 0.95) < 1e-9, gd)
check('club1 折扣文案 9.5 折', abs(gd['discountZhe'] - 9.5) < 1e-9, gd)
check('club1 折扣来自套餐 Pro', gd['planName'] == 'Pro', gd)

# ── 3) 结算口径：会员按套餐折扣、非会员原价、等级不再参与 ──────────────────
con.execute('INSERT INTO users(name,phone) VALUES(?,?)', ('套餐折扣测试员', '13900001111'))
con.commit(); uid = con.execute('SELECT last_insert_rowid()').fetchone()[0]
con.execute('INSERT INTO club_members(club_id,user_id) VALUES(1,?)', (uid,)); con.commit()
with app.conn() as cc:
    rate, disc = app.checkout_engine._member_gear_discount(cc, club_id=1, user_id=int(uid), gross=100.0)
check('会员结算折扣=套餐 0.95', abs(rate - 0.95) < 1e-9 and abs(disc - 5.0) < 1e-9, (rate, disc))
with app.conn() as cc:
    rate2, disc2 = app.checkout_engine._member_gear_discount(cc, club_id=1, user_id=999999, gross=100.0)
check('非会员原价', rate2 == 1.0 and disc2 == 0.0, (rate2, disc2))

# 会员中心带回 gearDiscount
mc = ok('get', f'/api/public/clubs/1/member-center?user_id={uid}').json()
check('member-center 带 gearDiscount', abs(float(mc['gearDiscount']['rate']) - 0.95) < 1e-9, mc.get('gearDiscount'))

# ── 4) 平台改套餐折扣立刻生效；俱乐部等级传 gearDiscount 被忽略 ─────────────
ok('post', '/api/platform/ai-credit/plans', json={'code': 'pro', 'name': 'Pro', 'monthlyFee': 699,
                                                 'monthlyCredits': 10000, 'gearDiscount': 0.9, 'status': 'active'})
gd2 = ok('get', '/api/club/1/gear-discount').json()
check('平台改套餐后折扣变 9 折', abs(gd2['rate'] - 0.90) < 1e-9, gd2)
r = c.post('/api/platform/ai-credit/plans', json={'code': 'pro', 'gearDiscount': 1.5})
check('非法折扣被拒（400）', r.status_code == 400, r.text)
# 不传 gearDiscount = 不改动（仍是 0.9，不会被清回默认）
ok('post', '/api/platform/ai-credit/plans', json={'code': 'pro', 'name': 'Pro', 'monthlyFee': 699,
                                                 'monthlyCredits': 10000, 'status': 'active'})
gd3 = ok('get', '/api/club/1/gear-discount').json()
check('不传折扣时保留原值', abs(gd3['rate'] - 0.90) < 1e-9, gd3)

tiers = ok('get', '/api/club/1/membership/tiers').json()
gold = next(t for t in tiers if t['name'] == '金卡会员')
old_gear = gold.get('gear_discount')
r = ok('patch', f"/api/club/1/membership/tiers/{gold['id']}",
       json={'name': '金卡会员', 'rank': 20, 'minActivitySpend': 2000, 'minActivityCount': 5,
             'qualificationMode': 'ANY', 'gearDiscount': 0.5, 'benefits': []})
con2 = db()
after = con2.execute('SELECT gear_discount FROM club_member_tiers WHERE id=?', (gold['id'],)).fetchone()[0]
check('等级接口忽略 gearDiscount（未写成 0.5）', after != 0.5, (old_gear, after))

# ── 5) 俱乐部调积分：只允许活动积分，gear 一律 403 ─────────────────────────
base = con2.execute('SELECT club_points_balance FROM club_members WHERE club_id=1 AND user_id=?', (uid,)).fetchone()[0]
r = ok('post', f'/api/club/1/members/{uid}/points', json={'kind': 'club', 'amount': 500, 'note': '补偿'}).json()
check('活动积分 +500 正常', r['balance'] == int(base) + 500 and r['kind'] == 'club', r)
r = c.post(f'/api/club/1/members/{uid}/points', json={'kind': 'gear', 'amount': 300})
check('调装备积分被拒（403）', r.status_code == 403, r.text)
r = c.post(f'/api/club/1/members/{uid}/points', json={'kind': 'gear', 'amount': -100})
check('扣装备积分同样被拒（403）', r.status_code == 403, r.text)
con3 = db()
check('装备积分未被俱乐部改动',
      con3.execute('SELECT COUNT(*) FROM gear_point_ledger WHERE user_id=? AND type="manual_adjust"', (uid,)).fetchone()[0] == 0)
r = c.post(f'/api/club/1/members/{uid}/points', json={'kind': 'bad', 'amount': 10})
check('其它非法类型仍 400', r.status_code == 400, r.text)

# ── 6) 俱乐部自助订阅 SaaS 套餐（2026-10-24「在哪里订」逻辑闭环）─────────────
con4 = db()
row = con4.execute('SELECT id FROM clubs WHERE id NOT IN (SELECT club_id FROM club_ai_subscriptions) ORDER BY id LIMIT 1').fetchone()
if row is None:
    con4.execute("INSERT INTO clubs(name,status,plan) VALUES('订阅测试俱乐部','active','starter')")
    con4.commit()
    scid = con4.execute('SELECT last_insert_rowid()').fetchone()[0]
else:
    scid = row[0]

r = ok('post', f'/api/club/{scid}/subscription/plan', json={'planCode': 'starter'}).json()
check('自助订阅：订阅即刻 active', r['subscription']['status'] == 'active' and r['subscription']['plan_code'] == 'starter', r)
check('自助订阅：自动生成当月待付款账单', r['billingOrder']['status'] == 'pending' and r['billingOrder']['order_type'] == 'subscription', r)
first_order = r['billingOrder']['id']
check('订阅账单金额=套餐月费', abs(float(r['billingOrder']['amount']) - float(plans['starter']['monthly_fee'])) < 1e-9, r['billingOrder'])
con5 = db()
check('clubs.plan 同步为 starter', con5.execute('SELECT plan FROM clubs WHERE id=?', (scid,)).fetchone()[0] == 'starter')
check('月度额度同步为套餐额度',
      con5.execute('SELECT monthly_quota FROM ai_credit_accounts WHERE club_id=?', (scid,)).fetchone()[0] == int(plans['starter']['monthly_credits']))

r2 = ok('post', f'/api/club/{scid}/subscription/plan', json={'planCode': 'starter'}).json()
check('同月同套餐重复订阅幂等（同一张账单）', r2['billingOrder']['id'] == first_order, (first_order, r2['billingOrder']['id']))

r3 = ok('post', f'/api/club/{scid}/subscription/plan', json={'planCode': 'pro'}).json()
check('同月换套餐：订阅切到 pro', r3['subscription']['plan_code'] == 'pro', r3['subscription'])
check('同月换套餐：当月账单原位改写为 pro（金额同 pro 月费）',
      r3['billingOrder']['id'] == first_order and r3['billingOrder']['plan_code'] == 'pro'
      and abs(float(r3['billingOrder']['amount']) - float(plans['pro']['monthly_fee'])) < 1e-9, r3['billingOrder'])

r = ok('get', f'/api/club/{scid}/credits').json()
check('/credits 返回可订阅套餐列表（含折扣）', isinstance(r.get('plans'), list) and all('gear_discount' in p for p in r['plans']), len(r.get('plans') or []))
pend = [o for o in r['pendingOrders'] if o['order_type'] == 'subscription']
check('当月只挂一张订阅账单且为 pro', len(pend) == 1 and pend[0]['plan_code'] == 'pro' and pend[0]['status'] == 'pending',
      [(o['plan_code'], o['status']) for o in pend])

# 收款闸门仍在平台手里：俱乐部账单由平台 confirm-paid 后 Credits 才入账
before_bal = con5.execute('SELECT balance FROM ai_credit_accounts WHERE club_id=?', (scid,)).fetchone()[0]
pro_order = next(o['id'] for o in pend if o['plan_code'] == 'pro')
ok('post', f'/api/platform/ai-credit/orders/{pro_order}/confirm-paid', json={'paymentRef': f'sub-{pro_order}'})
con6 = db()
after_bal = con6.execute('SELECT balance FROM ai_credit_accounts WHERE club_id=?', (scid,)).fetchone()[0]
check('平台确认收款后 Credits 入账', after_bal == before_bal + int(plans['pro']['monthly_credits']), (before_bal, after_bal))
check('账单状态转 paid', con6.execute('SELECT status FROM ai_credit_orders WHERE id=?', (pro_order,)).fetchone()[0] == 'paid')

r = c.post(f'/api/club/{scid}/subscription/plan', json={'planCode': 'nope'})
check('未知套餐 400', r.status_code == 400, r.text)
r = c.post(f'/api/club/{scid}/subscription/plan', json={})
check('缺 planCode 400', r.status_code == 400, r.text)

print(f'PYTHON CHECKS: {len(PASS)} passed')
