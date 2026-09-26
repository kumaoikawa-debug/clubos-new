"""回归测试：报价里的「本单最多可抵多少」必须与真正下单时的封顶结果一致。

背景：C 端原来的报名页让顾客自己在两个 number 输入框里填要抵多少积分 ——
把「该抵多少」变成顾客的算术题，填多了会被静默封顶、填少了白白少抵。
现在的做法是后端把上限算好（`maxRedeemable`），顾客只做「用/不用」的确认。

这个改动的风险点是**上限算法与下单算法漂移**：页面写「最多抵 ¥30」，
结账却按 0 抵扣（或反过来多抵），顾客看到的和账上发生的不一样。
所以此测试锁的是「同源」这条不变量，而不是某一个具体数字。

隔离方式同 tests/media_catalog_smoke.py：app.py 在模块级调 init_db()，
任何 import app 都会把迁移落到目标库，因此必须在 import 之前改 CLUBOS_DB_PATH。
"""
import os, sys, tempfile

sys.path.insert(0, '.')

os.environ['CLUBOS_DB_PATH'] = os.path.join(tempfile.mkdtemp(prefix='clubos-points-smoke-'), 'clubos.db')
os.environ.setdefault('CLUBOS_SECURITY_MODE', 'demo')
os.environ.setdefault('MOCK_AI', '1')

from fastapi.testclient import TestClient  # noqa: E402

import db  # noqa: E402
import app as appmod  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(('  PASS  ' if ok else '  FAIL  ') + name + (f'   | {detail}' if detail else ''))
    if not ok:
        FAILS.append(name)


db.init_db()
client = TestClient(appmod.app)

with db.conn() as c:
    payer = db.row(c.execute('SELECT id, name, phone FROM users ORDER BY id LIMIT 1'))
    act = db.row(c.execute('SELECT * FROM activities WHERE status="published" ORDER BY id LIMIT 1'))
    occ = db.row(c.execute('SELECT * FROM activity_occurrences WHERE activity_id=? ORDER BY id LIMIT 1', (act['id'],)))
ACT, OCC, UID = int(act['id']), int(occ['id']), int(payer['id'])
PRICE = float(occ['price'])


def set_policy(percent=None, gear_cap=None, accept_club=None, accept_gear=None):
    sets, vals = [], []
    if percent is not None:
        sets.append('club_points_max_discount_percent=?'); vals.append(percent)
    if gear_cap is not None:
        sets.append('gear_points_max_discount_amount=?'); vals.append(gear_cap)
    if accept_club is not None:
        sets.append('accept_club_points=?'); vals.append(1 if accept_club else 0)
    if accept_gear is not None:
        sets.append('accept_gear_points=?'); vals.append(1 if accept_gear else 0)
    with db.conn() as c:
        c.execute(f'UPDATE activities SET {", ".join(sets)} WHERE id=?', vals + [ACT])


def set_balance(club=None, gear=None):
    with db.conn() as c:
        if club is not None:
            c.execute('UPDATE club_members SET club_points_balance=? WHERE user_id=? AND club_id=?',
                      (club, UID, int(act['club_id'])))
        if gear is not None:
            c.execute('UPDATE gear_point_accounts SET balance=? WHERE user_id=?', (gear, UID))


def quote(**kw):
    p = {'occurrence_id': OCC, 'user_id': UID, 'participant_count': 1}
    p.update(kw)
    r = client.get(f'/api/public/activities/{ACT}/price-quote', params=p)
    assert r.status_code == 200, (r.status_code, r.text[:300])
    return r.json()


print(f'== 夹具：活动 {ACT} / 团期 {OCC} / 价格 {PRICE} / 付款人 {payer["name"]} ==')

print('\n== 1. 报价必须带 maxRedeemable ==')
set_policy(percent=10, gear_cap=30, accept_club=True, accept_gear=True)
set_balance(club=860, gear=1280)
q0 = quote(club_points=0, gear_points=0)
mx = q0.get('maxRedeemable') or {}
check('顶层 maxRedeemable 存在', isinstance(mx, dict) and 'club' in mx and 'gear' in mx, sorted(mx))
check('两侧都带 limiter（能解释上限成因）',
      mx.get('club', {}).get('limiter') and mx.get('gear', {}).get('limiter'),
      {k: mx[k]['limiter'] for k in ('club', 'gear')})
check('余额不足时 limiter=balance（860 < 政策上限）',
      mx['club']['limiter'] == 'balance' and mx['club']['points'] == 860,
      f"{mx['club']['points']}/{mx['club']['limiter']}")

print('\n== 2. 上限 = 真上限：请求再大也不会多抵 ==')
q_max = quote(club_points=mx['club']['points'], gear_points=mx['gear']['points'])
q_huge = quote(club_points=10 ** 9, gear_points=10 ** 9)
check('按上限提交 == 天文数字提交（payable）',
      abs(q_max['payable'] - q_huge['payable']) < 1e-6,
      f"{q_max['payable']} vs {q_huge['payable']}")
check('cashAfterPoints 与按上限提交的 payable 一致',
      abs(float(mx['cashAfterPoints']) - float(q_max['payable'])) < 0.01,
      f"{mx['cashAfterPoints']} vs {q_max['payable']}")
check('cashFromPoints = 两侧折扣之和',
      abs(float(mx['cashFromPoints']) - (float(mx['club']['cash']) + float(mx['gear']['cash']))) < 0.01,
      mx['cashFromPoints'])

print('\n== 3. 政策上限 < 余额时，上限必须来自政策而不是余额 ==')
set_balance(club=99999, gear=99999)          # 钱足够多 → 封顶只能来自活动策略
set_policy(percent=1, gear_cap=5)
q1 = quote(club_points=0, gear_points=0)
m1 = q1['maxRedeemable']
check('活动积分上限 = 金额 × 1%（而非 99999 余额）',
      m1['club']['limiter'] == 'policy' and abs(m1['club']['points'] - int(PRICE * 0.01 * 100)) <= 1,
      f"上限 {m1['club']['points']} 期望≈{int(PRICE * 0.01 * 100)}")
check('装备积分上限 = 补贴额 ¥5（而非 99999 余额）',
      m1['gear']['limiter'] == 'policy' and abs(m1['gear']['cash'] - 5.0) < 0.01,
      f"上限 ¥{m1['gear']['cash']}")
check('上限严格小于余额 → 不可能把 99999 全抵掉',
      m1['club']['points'] < 99999 and m1['gear']['points'] < 99999)

print('\n== 4. 关掉某类积分时上限归零且 limiter=off ==')
set_policy(accept_club=False)
q2 = quote(club_points=0, gear_points=0)
m2 = q2['maxRedeemable']
check('关闭活动积分 → allowed=False / limiter=off',
      m2['club']['allowed'] is False and m2['club']['limiter'] == 'off' and m2['club']['points'] == 0,
      m2['club'])
check('即使强行请求也不产生活动积分折扣',
      quote(club_points=10 ** 9)['clubPointsUsed'] == 0)
set_policy(accept_club=True)

print('\n== 5. 装备积分在「扣除活动积分后的剩余额」上封顶 ==')
set_balance(club=99999, gear=99999)
set_policy(percent=100, gear_cap=PRICE + 999)   # 活动积分可抵全额、装备补贴远超订单金额
q3 = quote(club_points=10 ** 9, gear_points=10 ** 9)
m3 = q3['maxRedeemable']
check('活动积分吃满全额后，装备侧上限被剩余金额夹住',
      abs(m3['club']['cash'] - PRICE) < 0.01 and abs(m3['gear']['cash']) < 0.01,
      f"club ¥{m3['club']['cash']} gear ¥{m3['gear']['cash']}")
check('合计抵扣不超过订单金额', m3['cashFromPoints'] <= PRICE + 0.01, m3['cashFromPoints'])

print('\n== 6. 下单实扣 = 报价承诺的抵扣（端到端） ==')
set_balance(club=860, gear=1280)
set_policy(percent=10, gear_cap=30)
q4 = quote(club_points=0, gear_points=0)
m4 = q4['maxRedeemable']
r = client.post(f'/api/public/activities/{ACT}/checkout', json={
    'name': payer['name'], 'phone': payer['phone'], 'occurrenceId': OCC,
    'clubPoints': m4['club']['points'], 'gearPoints': m4['gear']['points'],
})
check('下单成功', r.status_code == 200, r.status_code if r.status_code != 200 else '')
if r.status_code == 200:
    o = r.json()
    check('实扣活动积分 = 报价上限', int(o['clubPointsUsed']) == int(m4['club']['points']),
          f"{o['clubPointsUsed']} vs {m4['club']['points']}")
    check('实扣装备积分 = 报价上限', int(o['gearPointsUsed']) == int(m4['gear']['points']),
          f"{o['gearPointsUsed']} vs {m4['gear']['points']}")
    check('实际需支付 = cashAfterPoints（顾客看到的就是要付的）',
          abs(float(o['payable']) - float(m4['cashAfterPoints'])) < 0.01,
          f"{o['payable']} vs {m4['cashAfterPoints']}")

print('\n== 7. 报价是只读的：连打两次不能改变任何账 ==')
set_balance(club=500, gear=500)   # 上一节下单已把余额扣空，这里先充上再看报价有没有副作用
def balance():
    with db.conn() as c:
        cl = db.row(c.execute('SELECT club_points_balance FROM club_members WHERE user_id=? AND club_id=?',
                              (UID, int(act['club_id']))))['club_points_balance']
        gr = db.row(c.execute('SELECT balance FROM gear_point_accounts WHERE user_id=?', (UID,)))['balance']
    return int(cl or 0), int(gr or 0)
b1 = balance()
quote(club_points=10 ** 9, gear_points=10 ** 9)
quote(club_points=10 ** 9, gear_points=10 ** 9)
b2 = balance()
check('两次报价后余额不变', b1 == b2 and b1 != (0, 0), f'{b1} → {b2}')

print()
if FAILS:
    print(f'FAILED {len(FAILS)}: ' + '; '.join(FAILS))
    sys.exit(1)
print('ALL PASS · 积分上限与实际抵扣同源回归通过')
