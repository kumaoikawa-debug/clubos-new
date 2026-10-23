# -*- coding: utf-8 -*-
"""装备积分（Gear Points）抵用资格与比例的判定回归（2026-10-24 用户设计纠正）。

用户原话
--------
> 「装备积分抵用不是由俱乐部来决定，而是应该由该俱乐部选用的什么套餐；
>   基础套餐没有折扣，Pro 和 Enterprise 可以抵扣积分，
>   但是需要由总平台针对不同俱乐部来设计不同的积分抵扣比例 %」

本测试锁住这套新模型的**每一条不变量**，防止回退到「俱乐部自己开关」的老模型：

① 套餐决定资格：Starter（基础套餐）没有装备积分抵用权益；
   Pro / Enterprise 有，且套餐自带一个「权益上限比例」。
② 总平台按俱乐部决定比例：同一个 Pro 套餐下，A 俱乐部 8%、B 俱乐部 20% 互不影响。
③ 套餐是权益上限：平台给某俱乐部设 80%，但该俱乐部是 Pro（上限 20%）
   → 实际生效被夹到 20%，平台不能凭空放大权益。
④ 平台可以单独关闭某个俱乐部的装备积分抵用（enabled=0），
   且该俱乐部即便有 Pro 套餐也不可抵用。
⑤ 清除单独设定 → 回到套餐默认比例。
⑥ 平台总开关关闭 → 所有俱乐部一律不可抵用。
⑦ 活动级字段彻底退出判定：`activities.accept_gear_points` 与
   `activities.gear_points_max_discount_amount` 都不再影响装备积分的可用性与上限。
⑧ 判定结果必须**同源下发**：C 端公开活动详情里的 gearPointsRule、
   俱乐部端活动详情里的 gearPointsRule、平台端 /api/platform/points-policy
   三处给出的比例必须一致 —— 否则「页面说能抵 20%，结账只抵 5%」就是真金白银的纠纷。

自带临时库隔离（CLUBOS_DB_PATH 指到临时目录），不碰项目真库。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix='clubos_gearpolicy_')
os.environ.update({
    'CLUBOS_DB_PATH': str(Path(tmp.name) / 'gear.sqlite'),
    'CLUBOS_SECURITY_MODE': 'demo',
    'MOCK_AI': '1',
    'COMMERCE_PROVIDER': 'local',
})
os.environ.setdefault('CLUBOS_TEST_OPEN', '1')

import db  # noqa: E402
import app as appmod  # noqa: E402
from clubos_domain.gear_points_policy import GearPointsPolicyService  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FAILS = []
PASS = []


def check(name, ok, detail=''):
    print(('  PASS  ' if ok else '  FAIL  ') + name + (f'   | {detail}' if detail else ''))
    if ok:
        PASS.append(name)
    else:
        FAILS.append(name)


db.init_db()
client = TestClient(appmod.app)

# ── 夹具：三个俱乐部，分别用 starter / pro / enterprise ────────────────────
with db.conn() as c:
    c.execute("INSERT INTO clubs(name,status,plan) VALUES('基础俱乐部','active','starter')")
    c.execute("INSERT INTO clubs(name,status,plan) VALUES('Pro 俱乐部','active','pro')")
    c.execute("INSERT INTO clubs(name,status,plan) VALUES('旗舰俱乐部','active','enterprise')")
    ids = [int(r['id']) for r in c.execute("SELECT id FROM clubs WHERE name IN ('基础俱乐部','Pro 俱乐部','旗舰俱乐部') ORDER BY id")]
    STARTER, PRO, ENTERPRISE = ids[0], ids[1], ids[2]

service = GearPointsPolicyService(appmod.setting)


def resolve(club_id):
    with db.conn() as c:
        return service.resolve(c, club_id)


def set_platform_setting(key, value):
    with db.conn() as c:
        c.execute('INSERT INTO platform_settings(key,value) VALUES(?,?) '
                  'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))


def set_plan_percent(code, pct):
    with db.conn() as c:
        c.execute('UPDATE ai_credit_plans SET gear_points_redeem_percent=? WHERE code=?', (float(pct), code))


print('== 夹具：套餐自带比例 starter=0 / pro=20 / enterprise=50 ==')
with db.conn() as c:
    plans = {r['code']: float(r['gear_points_redeem_percent'])
             for r in c.execute('SELECT code,gear_points_redeem_percent FROM ai_credit_plans')}
check('starter 套餐自带比例 0（基础套餐无折扣）', plans.get('starter') == 0.0, plans.get('starter'))
check('pro 套餐自带比例 20', plans.get('pro') == 20.0, plans.get('pro'))
check('enterprise 套餐自带比例 50', plans.get('enterprise') == 50.0, plans.get('enterprise'))

print('\n== 1. 套餐决定资格 ==')
p_starter = resolve(STARTER)
check('Starter 俱乐部不可抵用装备积分',
      p_starter.allowed is False and p_starter.redeem_percent == 0.0,
      f'allowed={p_starter.allowed} pct={p_starter.redeem_percent}')
check('Starter 的原因说清了是套餐不含权益',
      '套餐' in p_starter.reason, p_starter.reason)
p_pro = resolve(PRO)
check('Pro 俱乐部可抵用、生效比例 = 套餐自带 20%',
      p_pro.allowed is True and abs(p_pro.redeem_percent - 20.0) < 1e-6,
      f'allowed={p_pro.allowed} pct={p_pro.redeem_percent}')
check('Pro 没有单独设定时 has_club_override=False',
      p_pro.has_club_override is False, p_pro.has_club_override)
p_ent = resolve(ENTERPRISE)
check('Enterprise 俱乐部可抵用、生效比例 = 套餐自带 50%',
      p_ent.allowed is True and abs(p_ent.redeem_percent - 50.0) < 1e-6,
      f'allowed={p_ent.allowed} pct={p_ent.redeem_percent}')

print('\n== 2. 总平台按俱乐部设不同比例（互不影响） ==')
with db.conn() as c:
    service.set_club_policy(c, PRO, redeem_percent=8, note='Pro 俱乐部首批试点')
    service.set_club_policy(c, ENTERPRISE, redeem_percent=30, note='旗舰俱乐部大比例')
p_pro = resolve(PRO)
p_ent = resolve(ENTERPRISE)
check('Pro 俱乐部按平台设定的 8% 执行',
      p_pro.allowed is True and abs(p_pro.redeem_percent - 8.0) < 1e-6 and p_pro.has_club_override,
      f'pct={p_pro.redeem_percent} override={p_pro.has_club_override}')
check('Enterprise 俱乐部按平台设定的 30% 执行',
      p_ent.allowed is True and abs(p_ent.redeem_percent - 30.0) < 1e-6 and p_ent.has_club_override,
      f'pct={p_ent.redeem_percent} override={p_ent.has_club_override}')
check('同一套餐体系下不同俱乐部比例互不影响',
      abs(p_pro.redeem_percent - p_ent.redeem_percent) > 0.01,
      f'{p_pro.redeem_percent} vs {p_ent.redeem_percent}')

print('\n== 3. 套餐是权益上限：平台不能凭空放大 ==')
with db.conn() as c:
    service.set_club_policy(c, PRO, redeem_percent=80)
p_pro = resolve(PRO)
check('平台设 80% 但 Pro 套餐上限 20% → 实际生效夹到 20%',
      abs(p_pro.redeem_percent - 20.0) < 1e-6,
      f'实际 {p_pro.redeem_percent}（平台设 80 / 套餐上限 20）')

print('\n== 4. 基础套餐：平台单独设比例也不能凭空发放权益 ==')
with db.conn() as c:
    service.set_club_policy(c, STARTER, redeem_percent=30)
p_starter = resolve(STARTER)
check('Starter 俱乐部即使平台设 30% 仍然不可抵用',
      p_starter.allowed is False and p_starter.redeem_percent == 0.0,
      f'allowed={p_starter.allowed} pct={p_starter.redeem_percent}')

print('\n== 5. 平台单独关闭某个俱乐部 ==')
with db.conn() as c:
    service.set_club_policy(c, PRO, redeem_percent=0, enabled=False)
p_pro = resolve(PRO)
check('被单独关闭 → 立刻不可抵用',
      p_pro.allowed is False and p_pro.redeem_percent == 0.0,
      f'allowed={p_pro.allowed} pct={p_pro.redeem_percent}')
check('关闭原因说明是平台单独关闭',
      '单独关闭' in p_pro.reason, p_pro.reason)
check('关闭只影响这一个俱乐部（Enterprise 不受影响）',
      resolve(ENTERPRISE).allowed is True)

print('\n== 6. 清除单独设定 → 回到套餐默认比例 ==')
with db.conn() as c:
    service.clear_club_policy(c, PRO)
p_pro = resolve(PRO)
check('清除后回到 Pro 套餐默认 20%',
      p_pro.allowed is True and abs(p_pro.redeem_percent - 20.0) < 1e-6 and not p_pro.has_club_override,
      f'pct={p_pro.redeem_percent} override={p_pro.has_club_override}')
with db.conn() as c:
    service.clear_club_policy(c, ENTERPRISE)
    service.clear_club_policy(c, STARTER)

print('\n== 7. 平台总开关 ==')
set_platform_setting('gear_points_activity_redeem_enabled', '0')
p_pro = resolve(PRO)
check('总开关关闭 → 一律不可抵用',
      p_pro.allowed is False and p_pro.platform_enabled is False,
      f'allowed={p_pro.allowed} platformEnabled={p_pro.platform_enabled}')
set_platform_setting('gear_points_activity_redeem_enabled', '1')
check('总开关恢复 → 回到套餐默认 20%',
      abs(resolve(PRO).redeem_percent - 20.0) < 1e-6)

print('\n== 8. 活动级字段彻底退出判定 ==')
with db.conn() as c:
    r = c.execute("SELECT id FROM activities WHERE status='published' ORDER BY id LIMIT 1").fetchone()
    ACT = int(r['id']) if r else 0
if ACT:
    with db.conn() as c:
        c.execute('UPDATE activities SET accept_gear_points=0, gear_points_max_discount_amount=0 WHERE id=?', (ACT,))
    pol = appmod.activity_points_policy.from_activity(
        {'points_enabled': 1, 'accept_gear_points': 0, 'gear_points_max_discount_amount': 0},
        resolve(PRO))
    check('活动把装备积分关掉且金额上限设 0 → 平台规则仍然生效',
          pol.effective_accept_gear_points is True,
          f'acceptGearPoints={pol.effective_accept_gear_points}')
    check('生效比例来自平台（20%），不是活动里的 0',
          abs(pol.effective_gear_points_percent - 20.0) < 1e-6,
          pol.effective_gear_points_percent)
    with db.conn() as c:
        c.execute('UPDATE activities SET accept_gear_points=1, gear_points_max_discount_amount=NULL WHERE id=?', (ACT,))
else:
    check('库里有已发布活动可用于活动级断言', False, 'no published activity')

print('\n== 9. 平台 API：GET /api/platform/points-policy ==')
r = client.get('/api/platform/points-policy')
check('接口可访问', r.status_code == 200, r.status_code)
data = r.json()
check('返回总开关', 'gearPointsActivityRedeemEnabled' in data, list(data))
items = data.get('clubs') or []
by_id = {int(x['clubId']): x for x in items}
check('列表包含三个夹具俱乐部', {STARTER, PRO, ENTERPRISE}.issubset(set(by_id)), sorted(by_id))
check('Pro 俱乐部下发 planPercent=20 / redeemPercent=20',
      by_id[PRO]['planPercent'] == 20 and by_id[PRO]['redeemPercent'] == 20
      and by_id[PRO]['allowed'] is True,
      by_id[PRO])
check('Starter 俱乐部下发 allowed=False 且带原因',
      by_id[STARTER]['allowed'] is False and bool(by_id[STARTER]['reason']),
      by_id[STARTER])

print('\n== 10. 平台 API：PATCH /api/platform/points-policy/clubs/{id} ==')
r = client.patch(f'/api/platform/points-policy/clubs/{PRO}', json={'redeemPercent': 12, 'note': '第二批'})
check('按俱乐部设定比例成功', r.status_code == 200, (r.status_code, r.text[:200]))
body = r.json()
check('返回的实际生效比例 = 12%',
      abs(float(body.get('redeemPercent')) - 12.0) < 1e-6, body)
check('标记为已单独设定', body.get('hasClubOverride') is True, body)
check('设定后 resolve 也是 12%（不是只在 API 层生效）',
      abs(resolve(PRO).redeem_percent - 12.0) < 1e-6)

r = client.patch(f'/api/platform/points-policy/clubs/{PRO}', json={'redeemPercent': 99})
check('设 99% 被套餐上限夹到 20%',
      abs(float(r.json().get('redeemPercent')) - 20.0) < 1e-6, r.json())

r = client.patch(f'/api/platform/points-policy/clubs/{PRO}', json={'redeemPercent': 0, 'enabled': False})
check('关闭该俱乐部成功且 allowed=False',
      r.status_code == 200 and r.json().get('allowed') is False, r.text[:200])

r = client.patch(f'/api/platform/points-policy/clubs/{PRO}', json={'clearOverride': True})
check('清除单独设定 → 回到套餐默认 20%',
      r.status_code == 200 and abs(float(r.json().get('redeemPercent')) - 20.0) < 1e-6, r.text[:200])

r = client.patch(f'/api/platform/points-policy/clubs/{PRO}', json={})
check('不传比例 → 400（而不是静默写坏数据）', r.status_code == 400, r.status_code)
r = client.patch('/api/platform/points-policy/clubs/999999', json={'redeemPercent': 10})
check('俱乐部不存在 → 400（不是 500）', r.status_code == 400, r.status_code)

print('\n== 11. 三处下发必须同源（C 端 / 俱乐部端 / 平台端） ==')
with db.conn() as c:
    service.set_club_policy(c, PRO, redeem_percent=15)
p = resolve(PRO)
expected = p.redeem_percent
if ACT:
    club_id = None
    with db.conn() as c:
        r = c.execute('SELECT club_id FROM activities WHERE id=?', (ACT,)).fetchone()
        club_id = int(r['club_id'])
    # 换到 Pro 俱乐部名下，才能比对同一份规则
    with db.conn() as c:
        c.execute('UPDATE activities SET club_id=? WHERE id=?', (PRO, ACT))
    r = client.get(f'/api/club/{PRO}/activities/{ACT}')
    if r.status_code == 200:
        rule = (r.json().get('pointsPolicy') or {}).get('gearPointsRule') or {}
        check('俱乐部端下发的生效比例与判定一致',
              abs(float(rule.get('redeemPercent')) - expected) < 1e-6,
              f"{rule.get('redeemPercent')} vs {expected}")
        check('俱乐部端下发套餐名与权益上限',
              rule.get('planName') != '' and float(rule.get('planPercent')) == 20.0, rule)
    r = client.get(f'/api/public/activities/{ACT}')
    if r.status_code == 200:
        rule = (r.json().get('pointsPolicy') or {}).get('gearPointsRule') or {}
        check('C 端下发的生效比例与判定一致',
              abs(float(rule.get('redeemPercent')) - expected) < 1e-6,
              f"{rule.get('redeemPercent')} vs {expected}")
    r = client.get('/api/platform/points-policy')
    item = {int(x['clubId']): x for x in (r.json().get('clubs') or [])}.get(PRO) or {}
    check('平台端下发的生效比例与判定一致',
          abs(float(item.get('redeemPercent')) - expected) < 1e-6,
          f"{item.get('redeemPercent')} vs {expected}")

print('\n== 12. 静态断言：前端不得再暴露活动级装备积分开关 ==')
import io  # noqa: E402
club_js = io.open(ROOT / 'static/club/club.js', encoding='utf-8').read()
web_js = io.open(ROOT / 'static/web/web.js', encoding='utf-8').read()
plat_js = io.open(ROOT / 'static/platform/platform.js', encoding='utf-8').read()
plat_html = io.open(ROOT / 'static/platform/index.html', encoding='utf-8').read()

check('俱乐部端积分规则卡不出现装备积分开关控件',
      'ppGear' not in club_js and 'gearPointsEnabled' not in club_js,
      '俱乐部端不应出现装备积分开关')
check('俱乐部端提到 acceptGearPoints 只用于只读展示（不是 input 绑定）',
      'acceptGearPoints' not in club_js
      or not any(('input' in ln and 'acceptGearPoints' in ln) for ln in club_js.split('\n')),
      '出现了 input 与 acceptGearPoints 同行 → 说明做成了开关')
check('俱乐部端展示的是平台统一规则（只读）',
      'gearPointsRule' in club_js and '由总平台统一控制' in club_js)
check('俱乐部端保存积分规则的 payload 不含装备积分字段',
      'acceptGearPoints' not in club_js.split('function savePointsPolicy')[1].split('}')[0])
check('C 端文案说的是「总平台为该俱乐部设定的比例」',
      'gearPointsRedeemPercent' in web_js and '总平台已为本俱乐部设定' in web_js)
check('平台端有按俱乐部配置表', 'gearPolicyTable' in plat_html and 'gearPolicyTable' in plat_js)
check('平台端配置表是真表格（表头：俱乐部/套餐/是否可抵/生效比例/生效状态）',
      '<table class="table">' in plat_html
      and all(k in plat_html for k in ('是否可抵', '生效比例', '生效状态'))
      and 'id="gearPolicyTable"' in plat_html,
      'gearPolicyTable 应在 tbody 里')
check('平台端支持按俱乐部设比例与清除设定',
      'setClubGearPercent' in plat_js and 'resetClubGearPercent' in plat_js)
check('平台端不再有俱乐部侧的活动级装备积分开关',
      'acceptGearPoints' not in plat_js)

print()
if FAILS:
    print(f'FAILED {len(FAILS)}: ' + '; '.join(FAILS))
    sys.exit(1)
print(f'ALL PASS · 装备积分「套餐资格 + 平台按俱乐部比例」回归通过（{len(PASS)} 项）')
