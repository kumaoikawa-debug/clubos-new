# -*- coding: utf-8 -*-
"""经营者视角 · 总平台后台权限矩阵（2026-11-15）。

与主 E2E 不同：主 E2E 跑在 CLUBOS_TEST_OPEN=1 免登录内测模式，验的是「业务能不能跑」；
本套件必须在 CLUBOS_TEST_OPEN=0 下用真实会话令牌，验的是「谁能碰什么」。
经营者关心的风险：俱乐部/领队/C 端能不能看到平台佣金与利润、平台账号能不能反查俱乐部私域、
同角色跨俱乐部会不会串数据、财务岗能不能改商品/库存、匿名能不能触发出款。

环境纪律：独立 /tmp 临时库，不碰项目真库；不 push / 发布 / 部署。
"""
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(
    CLUBOS_TEST_OPEN='0',
    CLUBOS_SECURITY_MODE='demo',
    MOCK_AI='1',
    COMMERCE_PROVIDER='local',
    CLUBOS_DB_PATH=str(Path(tempfile.mkdtemp(prefix='clubos-perm-')) / 'auth.db'),
)

import app  # noqa: E402
from security_v025 import _hash  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(app.app)
ROWS = []
FAILS = []


def check(name, cond, detail='', sev='P1'):
    ROWS.append((name, bool(cond)))
    print(('  PASS  ' if cond else '  FAIL  ') + name + (f'   | {detail}' if detail else ''), flush=True)
    if not cond:
        FAILS.append(name)


HEADERS = {}


def make_session(key, role, club_id=None, dept=None, user_id=None):
    with app.conn() as db:
        db.execute(
            "INSERT INTO auth_accounts(username,password_hash,role,status,club_id,user_id,platform_department) "
            "VALUES(?,?,?,?,?,?,?)",
            ('qa-' + key, 'test-only', role, 'active', club_id, user_id, dept or ''))
        aid = int(db.execute('SELECT last_insert_rowid()').fetchone()[0])
        token = secrets.token_urlsafe(32)
        db.execute('INSERT INTO auth_sessions(token_hash,account_id,csrf_hash,expires_at) VALUES(?,?,?,?)',
                   (_hash(token), aid, _hash('qa-csrf'), int(time.time()) + 3600))
    HEADERS[key] = {'Authorization': 'Bearer ' + token}


def call(key, method, url, payload=None):
    h = HEADERS.get(key, {})
    return c.request(method, url, json=payload, headers=h)


# 复制一家 active 俱乐部作为「另一个租户」：get_identity 对 club/leader 角色会校验
# clubs 表存在且 status=active，否则令牌会被判为无效（401），无法做跨租户对比。
with app.conn() as db:
    cur = db.execute('SELECT * FROM clubs WHERE id=1')
    base = cur.fetchone()
    cols = [d[0] for d in cur.description]
    vals = list(base)
    vals[0] = 2
    db.execute('INSERT INTO clubs(%s) VALUES(%s)' % (','.join(cols), ','.join('?' for _ in cols)), vals)

# 造 7 类身份（'anon' 不在 HEADERS 中，即匿名请求不带头）
make_session('plat_all', 'platform', dept='all')
make_session('plat_fin', 'platform', dept='finance')
make_session('plat_ops', 'platform', dept='operations')
make_session('club1', 'club', club_id=1)
make_session('club2', 'club', club_id=2)
make_session('leader', 'leader', club_id=1, user_id=1)
make_session('member', 'member', user_id=1)

print('\n══════════ K 匿名与登录门 ══════════')
r = call('anon', 'get', '/api/platform/dashboard')
check('K1 匿名读平台经营概览 → 401', r.status_code == 401, f'status={r.status_code}')
r = call('anon', 'get', '/api/platform/commission-settlements')
check('K2 匿名读佣金结算流水 → 401', r.status_code == 401, f'status={r.status_code}')
r = call('anon', 'post', '/api/platform/settlements', {'clubId': 1, 'paymentRef': 'ANON'})
check('K3 匿名触发出款 → 401', r.status_code == 401, f'status={r.status_code}')
r = call('anon', 'get', '/platform')
# demo/内测模式（strict=False）页面静态壳直接放行，登录门由前端 /api/auth/me 承担；
# 生产 strict 模式则由中间件 401 → 303 /login。关键是不在静态壳里泄露经营数据。
check('K4 匿名打开平台页：静态壳不泄露经营数据（数据接口仍 401）',
      r.status_code in (200, 401, 303) and '远拓户外' not in r.text and '167.76' not in r.text,
      f'status={r.status_code} bodyLen={len(r.text)}（demo 前端登录门 / 生产 303）')

print('\n══════════ L 非平台角色不得碰平台数据 ══════════')
CASES = [
    ('club1', '/api/platform/clubs'),
    ('club1', '/api/platform/profit/summary'),
    ('club1', '/api/platform/commission-settlements'),
    ('club1', '/api/platform/settings-hub'),
    ('club2', '/api/platform/dashboard'),
    ('leader', '/api/platform/dashboard'),
    ('member', '/api/platform/dashboard'),
]
for i, (key, url) in enumerate(CASES, 1):
    r = call(key, 'get', url)
    check(f'L{i} {key} 读 {url.split("/api/platform/")[1]} → 403',
          r.status_code == 403, f'status={r.status_code}')

print('\n══════════ M 平台角色与岗位隔离 ══════════')
r = call('plat_all', 'get', '/api/platform/dashboard')
check('M1 平台全能岗读经营概览 → 200', r.status_code == 200, f'status={r.status_code}')
r = call('plat_all', 'get', '/api/platform/commission-settlements')
check('M2 平台全能岗读佣金结算流水 → 200(DEF-01端点鉴权正常)',
      r.status_code == 200 and isinstance(r.json(), list), f'status={r.status_code}')
r = call('plat_fin', 'get', '/api/platform/profit/summary')
check('M3 财务岗读利润 → 200', r.status_code == 200, f'status={r.status_code}')
r = call('plat_fin', 'post', '/api/platform/warehouse/count',
         {'productId': 1, 'locationId': 1, 'countedOnHand': 1})
check('M4 财务岗改库存盘点 → 403(岗位隔离)', r.status_code == 403, f'status={r.status_code}')
r = call('plat_ops', 'patch', '/api/platform/products/1', {'stock': 999})
check('M5 运营岗改商品库存 → 403(岗位隔离)', r.status_code == 403, f'status={r.status_code}')

print('\n══════════ N 租户隔离（俱乐部私域） ══════════')
r = call('plat_all', 'get', '/api/club/1/dashboard')
check('N1 平台账号反查俱乐部私域 → 403', r.status_code == 403, f'status={r.status_code}')
r = call('club2', 'get', '/api/club/1/dashboard')
check('N2 俱乐部2 访问俱乐部1 私域 → 403(跨租户)', r.status_code == 403, f'status={r.status_code}')
r = call('club1', 'get', '/api/club/1/dashboard')
check('N3 俱乐部1 访问本俱乐部私域未被 RBAC 拦截',
      r.status_code != 403, f'status={r.status_code}（404 表示通过鉴权但路由不存在，同样有效）')
r = call('club1', 'post', '/api/club/2/mall/settlement-requests', {})
check('N4 俱乐部1 向俱乐部2 提交结算申请 → 403', r.status_code == 403, f'status={r.status_code}')

print('\n' + '=' * 62)
print('权限矩阵 %d 项 | 失败 %d 项' % (len(ROWS), len(FAILS)))
for n in FAILS:
    print('  [P1] ' + n)
sys.exit(1 if FAILS else 0)
