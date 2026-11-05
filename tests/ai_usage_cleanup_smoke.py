"""回归测试：清理「生成失败且未扣费」的智能用量记录（2026-11-05）。

背景：主视觉模型从 wanx-v1 升到 wanx2.1-t2i-plus 时，seed=-1 触发 400，
线上留了一条 status='error'、credits_charged=0 的用量记录。它没扣任何 Credits、
不是账单，却混在明细里并把成功率算低。现在提供手动清理入口。

硬闸门（资损/审计相关，必须锁死）：
  只删本俱乐部 status='error' AND COALESCE(credits_charged,0)=0 的记录。
  成功记录、pending 记录、已扣费的失败记录（可能对账/退款）、其它俱乐部记录
  一律不动，保证计费审计链完整。

隔离方式同 points_redeem_rate_smoke.py：app.py 在模块级调 init_db()，
必须在 import 之前把 CLUBOS_DB_PATH 指到临时库。
"""
import os, sys, tempfile, pathlib, re

sys.path.insert(0, '.')
os.environ['CLUBOS_DB_PATH'] = os.path.join(
    tempfile.mkdtemp(prefix='clubos-usage-cleanup-'), 'clubos.db')
os.environ.setdefault('CLUBOS_SECURITY_MODE', 'demo')
os.environ.setdefault('MOCK_AI', '1')
# /api/club/* 写接口在 demo 模式下仍要求登录态。
os.environ.setdefault('CLUBOS_TEST_OPEN', '1')

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
ROOT = pathlib.Path('.')

with db.conn() as c:
    clubs = [int(r['id']) for r in
             c.execute('SELECT id FROM clubs ORDER BY id LIMIT 2').fetchall()]
# ai_usage_records.club_id 有外键，跨俱乐部隔离必须用一个真实存在的第二俱乐部；
# 临时库 seed 通常只建 club1，缺就补一个（clubs 除 name 外都有默认值）。
if len(clubs) < 2:
    with db.conn() as c:
        cur = c.execute("INSERT INTO clubs(name) VALUES('用量清理测试·第二俱乐部')")
        clubs.append(int(cur.lastrowid))
C1, C2 = clubs[0], clubs[1]

# 临时库，清掉 seed 可能写入的用量记录，保证断言不受夹具漂移影响。
with db.conn() as c:
    c.execute('DELETE FROM ai_usage_records')


def insert(club, status, credits, tag):
    with db.conn() as c:
        cur = c.execute('''INSERT INTO ai_usage_records
            (club_id,task_type,request_id,provider,model,status,input_tokens,
             output_tokens,provider_cost,credits_charged,error,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)''',
            (club, 'hero', f'cleanup-probe-{tag}', 'dashscope', 'wanx2.1-t2i-plus',
             status, 0, 0, 0.0, credits,
             'parameters.seed must be [0,4294967290]' if status == 'error' else None))
        return int(cur.lastrowid)


print('\n== 1. 夹具：五类记录（含跨俱乐部 / 已扣费 / pending） ==')
IDS = {
    'success': insert(C1, 'success', 10, 'success'),
    'unbilled_error': insert(C1, 'error', 0, 'unbilled'),
    'charged_error': insert(C1, 'error', 5, 'charged'),
    'pending': insert(C1, 'pending', 0, 'pending'),
    'other_club': insert(C2, 'error', 0, 'otherclub'),
}
print('   ids =', IDS)
with db.conn() as c:
    total = c.execute('SELECT COUNT(*) FROM ai_usage_records').fetchone()[0]
check('夹具 5 条全部落库', total == 5, total)

print('\n== 2. 调用清理端点 ==')
r = client.delete(f'/api/club/{C1}/ai/usage/unbilled-failures')
check('DELETE 返回 200', r.status_code == 200, r.status_code)
body = r.json() if r.status_code == 200 else {}
check('返回 ok=True', body.get('ok') is True, body)
check('★ 只删了 1 条（未计费的 error）', body.get('deleted') == 1, body.get('deleted'))

print('\n== 3. 审计链完整性：四类必须保留 ==')
with db.conn() as c:
    remain = {int(x['id']) for x in
              c.execute('SELECT id FROM ai_usage_records').fetchall()}
for key in ('success', 'charged_error', 'pending', 'other_club'):
    check(f'保留 {key}', IDS[key] in remain, IDS[key])
check('★ 已清理未计费失败记录', IDS['unbilled_error'] not in remain, IDS['unbilled_error'])
check('剩余恰好 4 条', len(remain) == 4, len(remain))

print('\n== 4. 幂等：重复清理不误删 ==')
r2 = client.delete(f'/api/club/{C1}/ai/usage/unbilled-failures')
check('第二次返回 200', r2.status_code == 200, r2.status_code)
check('第二次 deleted=0', (r2.json() or {}).get('deleted') == 0, r2.json())
with db.conn() as c:
    after = c.execute('SELECT COUNT(*) FROM ai_usage_records').fetchone()[0]
check('总条数仍为 4', after == 4, after)

print('\n== 5. 明细 / 汇总 / 404 边界 ==')
lst = client.get(f'/api/club/{C1}/ai/usage')
rows = lst.json() if lst.status_code == 200 else []
check('明细接口 200', lst.status_code == 200, lst.status_code)
check('明细不再含已清理记录',
      all(int(x.get('id') or 0) != IDS['unbilled_error'] for x in rows), len(rows))
check('明细仍含成功记录',
      any(int(x.get('id') or 0) == IDS['success'] for x in rows))
sm = client.get(f'/api/club/{C1}/ai/usage/summary')
sd = sm.json() if sm.status_code == 200 else {}
check('汇总接口 200', sm.status_code == 200, sm.status_code)
check('汇总口径未虚构成功（success=1）', sd.get('success') == 1, sd.get('success'))
r404 = client.delete('/api/club/99999999/ai/usage/unbilled-failures')
check('不存在俱乐部返回 404', r404.status_code == 404, r404.status_code)

print('\n== 6. 前端与回归链路守门 ==')
html = (ROOT / 'static/club/index.html').read_text()
js = (ROOT / 'static/club/club.js').read_text()
app_src = (ROOT / 'app.py').read_text()
reg = (ROOT / 'scripts/regression_v025.sh').read_text()
m = re.search(r'/static/club/club\.js\?v=([0-9a-z]+)', html)
check('club.js 缓存版本 bump 到 20261105c',
      bool(m) and m.group(1) == '20261105c', m.group(1) if m else None)
check('后端端点已注册',
      "@app.delete('/api/club/{club_id}/ai/usage/unbilled-failures')" in app_src)
check('端点 SQL 带 credits 硬闸门',
      "COALESCE(credits_charged,0)=0" in app_src)
check('按钮挂 inline onclick', 'onclick="clearUnbilledUsageFailures()"' in js)
check('★ 函数显式挂 window（IIFE 里裸 function 找不到）',
      'window.clearUnbilledUsageFailures=async function' in js)
check('清理走二次确认', "confirmText:'确认清理',danger:true" in js)
check('账本页不给删除按钮（仅 usage）', "key==='usage'" in js)
check('明细标注「未计费失败」', "'未计费失败'" in js)
check('已纳入全量回归', 'python tests/ai_usage_cleanup_smoke.py' in reg)

print('\n' + ('ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'))
sys.exit(1 if FAILS else 0)
