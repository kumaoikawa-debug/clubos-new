"""回归测试：活动积分「现金换算比例」X 积分 = ¥1（2026-11-05）。

业务规则：
  1. 先换算、再按百分比抵扣 —— 百分比算的是**现金**上限，比例只决定
     「多少积分能抵满」，绝不能改变「最多抵多少钱」这道成本闸门。
  2. 生效优先级：会员当前等级专属 > 俱乐部默认 > 平台全局默认 > 100。
  3. `0` = 跟随上一级。存量库升级后全部落 0，所以「存量行为零变化」：
     平台全局若曾被改成 50，升级后仍是 50，不会被悄悄顶成 100。
  4. 装备积分由总平台出钱，仍走 gear_points_redeem_rate(100:1)，不受影响。
  5. 报价(price-quote) 与下单(checkout) 必须用同一个解析结果（同源）。

隔离方式同 points_max_redeemable_smoke.py：app.py 在模块级调 init_db()，
必须在 import 之前把 CLUBOS_DB_PATH 指到临时库。
"""
import os, sys, tempfile, json

sys.path.insert(0, '.')
os.environ['CLUBOS_DB_PATH'] = os.path.join(tempfile.mkdtemp(prefix='clubos-rate-smoke-'), 'clubos.db')
os.environ.setdefault('CLUBOS_SECURITY_MODE', 'demo')
os.environ.setdefault('MOCK_AI', '1')
# /api/club/* 写接口在 demo 模式下仍要求登录态；本测试只验积分算法，开内测兜底。
os.environ.setdefault('CLUBOS_TEST_OPEN', '1')

from fastapi.testclient import TestClient  # noqa: E402
import db  # noqa: E402
import app as appmod  # noqa: E402
from clubos_domain.points_redeem_rate import resolve, describe, validate_rate  # noqa: E402

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
CID, ACT, OCC, UID = int(act['club_id']), int(act['id']), int(occ['id']), int(payer['id'])
PRICE = float(occ['price'])
CAP = round(PRICE * 0.10, 2)  # 活动规则「最多抵 10%」→ 现金上限 ¥49.80

# 固定夹具：活动只开活动积分、最多抵 10%；关掉会员折扣以免污染 payable 断言。
with db.conn() as c:
    c.execute('''UPDATE activities SET points_enabled=1,accept_club_points=1,
                 club_points_max_discount_percent=10,member_discount_enabled=0 WHERE id=?''', (ACT,))
    c.execute('UPDATE club_members SET club_points_balance=100000 WHERE club_id=? AND user_id=?', (CID, UID))
    c.execute('UPDATE gear_point_accounts SET balance=100000 WHERE user_id=?', (UID,))
TIERS = {}
with db.conn() as c:
    for t in c.execute('SELECT id,name,rank FROM club_member_tiers WHERE club_id=? ORDER BY rank,id', (CID,)).fetchall():
        TIERS[t['name']] = int(t['id'])


def set_platform(rate):
    with db.conn() as c:
        c.execute("INSERT INTO platform_settings(key,value) VALUES('club_points_redeem_rate',?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(rate),))


def set_club(rate):
    r = client.patch(f'/api/club/{CID}/points-redeem-rate', json={'pointsRedeemRate': rate})
    assert r.status_code == 200, (r.status_code, r.text[:300])
    return r.json()


def set_tier(tier_id, rate):
    with db.conn() as c:
        c.execute('UPDATE club_member_tiers SET points_redeem_rate=? WHERE id=? AND club_id=?', (rate, tier_id, CID))


def set_current_tier(tier_id):
    with db.conn() as c:
        c.execute('UPDATE club_members SET current_tier_id=? WHERE club_id=? AND user_id=?', (tier_id, CID, UID))


def quote(**kw):
    p = {'occurrence_id': OCC, 'user_id': UID, 'participant_count': 1,
         'club_points': 10 ** 8, 'gear_points': 0}
    p.update(kw)
    r = client.get(f'/api/public/activities/{ACT}/price-quote', params=p)
    assert r.status_code == 200, (r.status_code, r.text[:300])
    return r.json()


print(f'== 夹具：活动 {ACT} / 团期 {OCC} / 价格 ¥{PRICE} / 10% 现金上限 ¥{CAP} ==')

print('\n== 1. 存量行为零变化：默认 100:1，平台全局改动仍生效 ==')
set_platform(100)
set_club(0)
set_tier(TIERS['普通会员'], 0)
set_tier(TIERS['银卡会员'], 0)
set_tier(TIERS['金卡会员'], 0)
set_current_tier(TIERS['普通会员'])
with db.conn() as c:
    check('clubs.points_redeem_rate 存量默认 = 0（跟随），不是写死 100',
          c.execute('SELECT points_redeem_rate FROM clubs WHERE id=?', (CID,)).fetchone()[0] == 0)
    check('俱乐部默认比例接口返回 source=platform',
          client.get(f'/api/club/{CID}/points-redeem-rate').json()['source'] == 'platform')
q = quote()
check('默认生效 100:1', q['pointsRedeemRate'] == 100 and q['maxRedeemable']['club']['rate'] == 100,
      q['pointsRedeemRate'])
check('100:1 时积分上限 = 现金上限 × 100', q['clubPointsUsed'] == int(CAP * 100 + 1e-9),
      f"{q['clubPointsUsed']} vs {int(CAP * 100 + 1e-9)}")
check('现金抵扣严格等于 10% 上限', abs(q['clubPointDiscount'] - CAP) < 0.01, q['clubPointDiscount'])

set_platform(50)
q50 = quote()
check('平台全局改成 50 后仍生效（升级没把它顶成 100）',
      q50['pointsRedeemRate'] == 50 and q50['maxRedeemable']['club']['rate'] == 50,
      q50['pointsRedeemRate'])
check('50:1 时只需一半积分抵满同一个现金上限',
      q50['clubPointsUsed'] == int(CAP * 50 + 1e-9), q50['clubPointsUsed'])
check('★ 现金上限不随比例变化（成本闸门不变）',
      abs(q50['clubPointDiscount'] - CAP) < 0.01, q50['clubPointDiscount'])

print('\n== 2. 俱乐部默认覆盖平台全局 ==')
d = set_club(80)
check('保存后 clubRate=80 / clubEffectiveRate=80 / source=club',
      d['clubRate'] == 80 and d['clubEffectiveRate'] == 80 and d['source'] == 'club',
      {k: d[k] for k in ('platformRate', 'clubRate', 'clubEffectiveRate', 'source')})
q80 = quote()
check('报价生效 80:1', q80['pointsRedeemRate'] == 80, q80['pointsRedeemRate'])
check('80:1 所需积分 = ¥49.8 × 80', q80['clubPointsUsed'] == int(CAP * 80 + 1e-9), q80['clubPointsUsed'])
check('现金上限仍是 ¥49.8', abs(q80['clubPointDiscount'] - CAP) < 0.01, q80['clubPointDiscount'])

print('\n== 3. 会员等级差异化：普通 100 / 高级 50 / 顶级 20 ==')
set_club(100)  # 俱乐部默认 100，等级各自覆盖
set_tier(TIERS['普通会员'], 100)
set_tier(TIERS['银卡会员'], 50)
set_tier(TIERS['金卡会员'], 20)
for name, rate in (('普通会员', 100), ('银卡会员', 50), ('金卡会员', 20)):
    set_current_tier(TIERS[name])
    qr = quote()
    check(f'{name} 生效 {rate}:1',
          qr['pointsRedeemRate'] == rate and qr['maxRedeemable']['club']['rate'] == rate
          and qr['pointsPolicy']['pointsRedeemRate'] == rate,
          f"top={qr['pointsRedeemRate']} mx={qr['maxRedeemable']['club']['rate']} pol={qr['pointsPolicy'].get('pointsRedeemRate')}")
    check(f'{name} 所需积分 = ¥{CAP} × {rate}',
          qr['clubPointsUsed'] == int(CAP * rate + 1e-9),
          f"{qr['clubPointsUsed']} vs {int(CAP * rate + 1e-9)}")
    check(f'{name} 现金抵扣仍封顶 ¥{CAP}', abs(qr['clubPointDiscount'] - CAP) < 0.01, qr['clubPointDiscount'])
    check(f'{name} 装备积分仍是平台 100:1（不受等级影响）',
          qr['maxRedeemable']['gear']['rate'] == 100, qr['maxRedeemable']['gear']['rate'])

print('\n== 4. 各等级生效一览（后台卡片数据） ==')
d = client.get(f'/api/club/{CID}/points-redeem-rate').json()
eff = {t['name']: t['effectiveRate'] for t in d['tiers']}
check('一览返回 100 / 50 / 20',
      eff.get('普通会员') == 100 and eff.get('银卡会员') == 50 and eff.get('金卡会员') == 20, eff)
check('等级专属 source=tier',
      all(t['source'] == 'tier' for t in d['tiers']), [t['source'] for t in d['tiers']])

print('\n== 5. 报价与真实下单同源（顶级会员 20:1） ==')
set_current_tier(TIERS['金卡会员'])
qm = quote()
mx = qm['maxRedeemable']['club']
r = client.post(f'/api/public/activities/{ACT}/checkout', json={
    'name': payer['name'], 'phone': payer['phone'], 'occurrenceId': OCC,
    'clubPoints': mx['points'], 'gearPoints': 0,
    'participants': [{'name': payer['name'], 'phone': payer['phone'],
                      'idType': '护照', 'idNumber': 'E12345678'}],
})
check('下单成功', r.status_code == 200, r.status_code if r.status_code != 200 else r.text[:200])
if r.status_code == 200:
    o = r.json()
    check('实扣积分 = 报价上限', int(o['clubPointsUsed']) == int(mx['points']),
          f"{o['clubPointsUsed']} vs {mx['points']}")
    check('实扣现金 = 报价现金上限 ¥49.8',
          abs(float(o['clubPointDiscount']) - CAP) < 0.01, o['clubPointDiscount'])
    with db.conn() as c:
        snap = c.execute('SELECT points_policy_snapshot_json FROM checkout_intents ORDER BY rowid DESC LIMIT 1').fetchone()[0]
    check('成交快照记录 pointsRedeemRate=20（日后改比例不改旧账口径）',
          json.loads(snap or '{}').get('pointsRedeemRate') == 20, snap[:160] if snap else '')

print('\n== 6. C 端会员中心 / 积分商城下发当前等级比例 ==')
# member-center 会 refresh_member 重算等级：把「普通会员」（无条件达标档）设成 20，
# 刷新后仍会落到这一档，才能稳定断言。
set_tier(TIERS['普通会员'], 20)
set_current_tier(TIERS['普通会员'])
mc = client.get(f'/api/public/clubs/{CID}/member-center', params={'user_id': UID})
check('member-center 下发 20', mc.status_code == 200 and mc.json().get('pointsRedeemRate') == 20,
      mc.json().get('pointsRedeemRate'))
pm = client.get(f'/api/public/clubs/{CID}/points-mall', params={'user_id': UID})
check('points-mall 下发 20', pm.status_code == 200 and pm.json().get('pointsRedeemRate') == 20,
      pm.json().get('pointsRedeemRate'))

print('\n== 7. 入参校验 & 等级 PATCH 不误清空 ==')
for bad, label in ((10.5, '小数'), (-1, '负数'), (99999, '超上限'), ('abc', '非数字')):
    rr = client.patch(f'/api/club/{CID}/points-redeem-rate', json={'pointsRedeemRate': bad})
    check(f'拒绝 {label} ({bad}) → 400', rr.status_code == 400, rr.status_code)
check('填 0 = 跟随平台 → 200',
      client.patch(f'/api/club/{CID}/points-redeem-rate', json={'pointsRedeemRate': 0}).status_code == 200)
# 通过 API 给金卡设 20，再用「不带 pointsRedeemRate」的普通 PATCH 改名，比例不能被清掉
tier3 = TIERS['金卡会员']
payload = {'name': '金卡会员', 'rank': 20, 'minActivitySpend': 2000, 'minActivityCount': 5,
           'qualificationMode': 'ANY', 'benefits': ['高阶会员活动'],
           'accessMode': 'CONDITION', 'price': 0, 'validMonths': 12,
           'activityDiscountPercent': 90, 'pointsRedeemRate': 20}
check('等级 API 保存 20 成功',
      client.patch(f'/api/club/{CID}/membership/tiers/{tier3}', json=payload).status_code == 200)
bare = dict(payload); bare.pop('pointsRedeemRate'); bare['name'] = '金卡会员（改名）'
check('不带比例字段的 PATCH 成功',
      client.patch(f'/api/club/{CID}/membership/tiers/{tier3}', json=bare).status_code == 200)
with db.conn() as c:
    left = c.execute('SELECT points_redeem_rate FROM club_member_tiers WHERE id=?', (tier3,)).fetchone()[0]
check('★ 普通 PATCH 不会把已配的 20 清成跟随', left == 20, left)

print('\n== 8. 老库缺列时安全回退（不抛异常） ==')
set_platform(50)
set_club(0)
with db.conn() as c:
    c.execute('ALTER TABLE clubs DROP COLUMN points_redeem_rate')
    c.execute('ALTER TABLE club_member_tiers DROP COLUMN points_redeem_rate')
try:
    with db.conn() as c:
        legacy = resolve(c, club_id=CID, user_id=UID, setting=db.setting)
        dd = describe(c, club_id=CID, user_id=None, setting=db.setting)
    check('缺列时回到平台全局 50，行为零变化', legacy == 50, legacy)
    check('describe() 缺列不抛异常且 tiers 为空', dd['tiers'] == [] and dd['effectiveRate'] == 50,
          (len(dd['tiers']), dd['effectiveRate']))
    ql = quote()
    check('缺列时报价仍可用（不 500）', ql['pointsRedeemRate'] == 50, ql['pointsRedeemRate'])
except Exception as e:
    check('缺列时安全回退', False, repr(e))
# 恢复：init_db 会把两列补回来，后续若有测试不受影响
db.init_db()
for label, val, ok in (('0 合法', 0, True), ('20 合法', 20, True), ('10000 合法', 10000, True),
                       ('10001 非法', 10001, False)):
    try:
        validate_rate(val); got = True
    except ValueError:
        got = False
    check(f'validate_rate {label}', got is ok, got)

print('\n' + ('ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'))
sys.exit(1 if FAILS else 0)
