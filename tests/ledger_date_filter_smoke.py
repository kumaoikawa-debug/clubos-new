# -*- coding: utf-8 -*-
"""Credits 流水 / AI 用量明细的日期筛选 + 分页 + 区间汇总冒烟测试（2026-10-23 用户反馈）。

覆盖：
  ① /credits/ledger 返回 items/total/summary，且 total 与 items 口径一致；
  ② date_from/date_to 按 +8 本地日历日过滤（含「今天」「往前第 N 天」两条边界）；
  ③ offset/limit 分页不重不漏；
  ④ 非法日期格式、from>to 一律 400；
  ⑤ 区间汇总 income/expense/net 与写入值相符；
  ⑥ /ai/usage 日期过滤 + 分页（默认 limit=100 的旧行为保留）；
  ⑦ /ai/usage/summary 汇总与 byTask 拆分；
  ⑧ 旧端点 /credits 未被破坏（仍回 ledger 数组，前端余额/套餐继续可用）。
"""
import sys as _sys; _sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'tests'))
import _identity_patch  # noqa: F401
from pathlib import Path
import os, sys, sqlite3
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['MOCK_AI'] = '1'
os.environ.setdefault('CLUBOS_TEST_OPEN', '1')
os.environ['CLUBOS_DB_PATH'] = str(ROOT / 'clubos_ledger_test.db')
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

# ── 造数：直接写库，created_at 显式指定，才能测「跨天」过滤 ─────────────────
def cst_stamp(day_offset, hour=12):
    """返回 (UTC 时间戳字符串, 本地(+8)日期字符串)：CST 墙钟 day_offset 天前的 hour 点。"""
    now = datetime.now(timezone.utc)
    cst = (now + timedelta(hours=8)).replace(hour=hour, minute=0, second=0, microsecond=0)
    cst = cst + timedelta(days=day_offset)
    return (cst - timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S'), cst.strftime('%Y-%m-%d')

today_ts, today = cst_stamp(0)
yday_ts, yday = cst_stamp(-1)
old_ts, old_day = cst_stamp(-40)

db = sqlite3.connect(os.environ['CLUBOS_DB_PATH'])
# 种子库自带 2 条演示账本（订阅 1200 / 商城奖励 400）与若干用量行；本测试要断言绝对条数，
# 先清空 club 1 的这两张表——只清测试库（CLUBOS_DB_PATH 指到 clubos_ledger_test.db），
# 否则「今天有几笔」会被种子数据的 CURRENT_TIMESTAMP 搅乱。
db.execute('DELETE FROM ai_credit_ledger WHERE club_id=1')
db.execute('DELETE FROM ai_usage_records WHERE club_id=1')
rows = [
    # (type, amount, source_type, note, created_at)
    ('topup', 600, 'order', '充值 600 Credits', yday_ts),
    ('consume', -30, 'ai_usage', 'detail AI生成', yday_ts),
    ('consume', -30, 'ai_usage', 'detail AI生成', today_ts),
    ('mall_reward', 200, 'gear_order', '装备商城增长奖励', today_ts),
    ('consume', -50, 'ai_usage', '历史消耗', old_ts),
]
db.executemany('INSERT INTO ai_credit_ledger(club_id,type,amount,source_type,note,created_at) VALUES(1,?,?,?,?,?)', rows)
usage = [
    ('detail', 'qwen3.8-max', 'qwen', 'success', 1000, 500, 0.012, 30, yday_ts),
    ('detail', 'qwen3.8-max', 'qwen', 'success', 2000, 800, 0.02, 30, today_ts),
    ('wechat', 'qwen3.8-max', 'qwen', 'failed', 300, 0, 0.003, 0, today_ts),
    ('poster', 'qwen3-vl-plus', 'qwen', 'success', 500, 120, 0.004, 0, today_ts),
    ('detail', 'qwen3.8-max', 'qwen', 'success', 900, 400, 0.01, 40, old_ts),
]
db.executemany('INSERT INTO ai_usage_records(club_id,task_type,request_id,provider,model,status,'
               'input_tokens,output_tokens,provider_cost,credits_charged,created_at) '
               'VALUES(1,?,?,?,?,?,?,?,?,?,?)',
               [(t, f'req-{i}-' + ts, p, m, s, it, ot, pc, cc, ts)
                for i, (t, m, p, s, it, ot, pc, cc, ts) in enumerate(usage)])
db.commit(); db.close()

# ── 1) 全区间：结构 + total 口径 ────────────────────────────────────────────
r = ok('get', '/api/club/1/credits/ledger?limit=100')
L = r.json()
check('ledger returns items/total/summary', isinstance(L.get('items'), list) and 'total' in L and 'summary' in L, repr(L)[:200])
check('ledger total == items count (all)', L['total'] == len(L['items']) == 5, f"total={L['total']} items={len(L['items'])}")
check('ledger summary count == total', L['summary']['count'] == 5, repr(L['summary']))
check('ledger summary income = 800', L['summary']['income'] == 800, repr(L['summary']))
check('ledger summary expense = 110', L['summary']['expense'] == 110, repr(L['summary']))
check('ledger summary net = 690', L['summary']['net'] == 690, repr(L['summary']))

# ── 2) 按日过滤：今天只应有 2 笔（-30 / mall_reward 200）─────────────────
r = ok('get', f'/api/club/1/credits/ledger?date_from={today}&date_to={today}&limit=100')
L = r.json()
check('today ledger total == 2', L['total'] == 2, f"total={L['total']} today={today} items={[x.get('created_at') for x in L['items']]}")
check('today ledger income 200 / expense 30', L['summary']['income'] == 200 and L['summary']['expense'] == 30, repr(L['summary']))

# 昨天
r = ok('get', f'/api/club/1/credits/ledger?date_from={yday}&date_to={yday}&limit=100')
L = r.json()
check('yesterday ledger total == 2', L['total'] == 2, f"total={L['total']} yday={yday}")
check('yesterday net = 570', L['summary']['net'] == 570, repr(L['summary']))

# 跨天区间：昨天到今天 = 4 笔，40 天前那笔被排除（这正是「别一直往下拉」的核心）
r = ok('get', f'/api/club/1/credits/ledger?date_from={yday}&date_to={today}&limit=100')
L = r.json()
check('range yday..today total == 4', L['total'] == 4, f"total={L['total']}")
check('old 40d record excluded', all(x['created_at'] != old_ts for x in L['items']), 'old record leaked')

# ── 3) 分页不重不漏 ────────────────────────────────────────────────────────
p1 = ok('get', '/api/club/1/credits/ledger?limit=2&offset=0').json()
p2 = ok('get', '/api/club/1/credits/ledger?limit=2&offset=2').json()
p3 = ok('get', '/api/club/1/credits/ledger?limit=2&offset=4').json()
ids = [x['id'] for x in p1['items'] + p2['items'] + p3['items']]
check('paging covers all 5 without dup', len(ids) == 5 and len(set(ids)) == 5, repr(ids))
check('paging keeps total stable', p1['total'] == p2['total'] == p3['total'] == 5, 'total drifted')
check('paging page size respected', len(p1['items']) == 2 and len(p3['items']) == 1, f"{len(p1['items'])}/{len(p3['items'])}")

# ── 4) 非法入参 ────────────────────────────────────────────────────────────
check('bad date format -> 400', c.get('/api/club/1/credits/ledger?date_from=2026-13-40').status_code == 400)
check('from > to -> 400', c.get('/api/club/1/credits/ledger?date_from=2026-10-20&date_to=2026-10-01').status_code == 400)
check('usage bad date -> 400', c.get('/api/club/1/ai/usage?date_to=abc').status_code == 400)

# ── 5) AI 用量：日期过滤 + 分页 ────────────────────────────────────────────
r = ok('get', '/api/club/1/ai/usage?limit=100')
U = r.json()
check('usage is list (old contract kept)', isinstance(U, list), repr(type(U)))
check('usage all == 5', len(U) == 5, f'len={len(U)}')

r = ok('get', f'/api/club/1/ai/usage?date_from={today}&date_to={today}&limit=100')
check('usage today == 3', len(r.json()) == 3, f"len={len(r.json())}")
r = ok('get', f'/api/club/1/ai/usage?date_from={yday}&date_to={yday}&limit=100')
check('usage yesterday == 1', len(r.json()) == 1, f"len={len(r.json())}")

u1 = ok('get', '/api/club/1/ai/usage?limit=2&offset=0').json()
u2 = ok('get', '/api/club/1/ai/usage?limit=2&offset=2').json()
uids = [x['id'] for x in u1 + u2]
check('usage paging no dup', len(set(uids)) == 4, repr(uids))
check('usage limit clamps to <=200', len(ok('get', '/api/club/1/ai/usage?limit=99999').json()) <= 200)

# ── 6) 用量汇总 + byTask ───────────────────────────────────────────────────
r = ok('get', f'/api/club/1/ai/usage/summary?date_from={yday}&date_to={today}')
S = r.json()
check('summary calls == 4', S.get('calls') == 4, repr(S)[:300])
check('summary success == 3 / failed == 1', S.get('success') == 3 and S.get('failed') == 1, repr(S)[:300])
check('summary credits == 60', S.get('credits') == 60, repr(S)[:300])
check('summary inputTokens == 3800', S.get('inputTokens') == 3800, repr(S)[:300])
check('summary outputTokens == 1420', S.get('outputTokens') == 1420, repr(S)[:300])
check('summary byTask is list', isinstance(S.get('byTask'), list) and len(S['byTask']) >= 3, repr(S.get('byTask'))[:300])
byd = {b['task']: b for b in S['byTask']}
check('byTask detail calls == 2 / credits 60', byd.get('detail', {}).get('calls') == 2 and byd.get('detail', {}).get('credits') == 60, repr(byd.get('detail')))
check('byTask wechat failed 1', byd.get('wechat', {}).get('calls') == 1 and byd.get('wechat', {}).get('success') == 0, repr(byd.get('wechat')))
check('byTask sorted by calls desc', S['byTask'][0]['calls'] >= S['byTask'][-1]['calls'], repr([b['calls'] for b in S['byTask']]))

# ── 7) 旧端点 /credits 未被破坏 ────────────────────────────────────────────
r = ok('get', '/api/club/1/credits')
D = r.json()
check('/credits still returns ledger array', isinstance(D.get('ledger'), list), repr(list(D.keys()))[:300])
check('/credits still has account/subscription', 'account' in D and 'subscription' in D, repr(list(D.keys()))[:300])

print('PYTHON CHECKS:', len(PASS), 'passed')
