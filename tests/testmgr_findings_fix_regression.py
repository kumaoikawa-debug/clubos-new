# -*- coding: utf-8 -*-
"""F-01～F-05 / F-07 / F-08 修复专项回归（2026-11-16）。

验证口径：
  * F-01～F-05：异常输入必须返回明确的 4xx 业务错误，不得抛出未捕获异常（生产 500）。
  * F-07：/api/platform/uploads/image 只声明一次；源码声明数 == 运行时注册数。
  * F-08：产品官网 /home 在 demo(TEST_OPEN=0) 与 production(strict) 下都匿名可读；
          后台页的 OBS-03 防缓存/防索引头不回归，官网不被误加 noindex。

环境纪律：/tmp 隔离库，不碰项目真库；不 push / 发布 / 部署。
"""
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB = Path(tempfile.mkdtemp()) / 'findings_fix.db'
os.environ.update(
    CLUBOS_DB_PATH=str(DB),
    CLUBOS_SECURITY_MODE='demo',
    MOCK_AI='1',
    COMMERCE_PROVIDER='local',
    CLUBOS_TEST_OPEN='0',
)

import db  # noqa: E402
db.init_db()
import app  # noqa: E402
import security_v025  # noqa: E402
from security_v025 import _hash  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  PASS ' if cond else '  FAIL ') + name + ('  | ' + str(detail) if detail else ''), flush=True)


# raise_server_exceptions=False：未捕获异常会变成 500 响应，便于断言「没有漏出的异常」
c = TestClient(app.app, raise_server_exceptions=False)

# 平台管理员真实会话
with app.conn() as d:
    d.execute(
        "INSERT INTO auth_accounts(username,password_hash,role,status,club_id,user_id,platform_department) "
        "VALUES(?,?,?,?,?,?,?)",
        ('qa-findings-fix', 'test-only', 'platform', 'active', None, None, 'all'))
    aid = int(d.execute('SELECT last_insert_rowid()').fetchone()[0])
    token = secrets.token_urlsafe(32)
    d.execute('INSERT INTO auth_sessions(token_hash,account_id,csrf_hash,expires_at) VALUES(?,?,?,?)',
              (_hash(token), aid, _hash('qa-csrf'), int(time.time()) + 7200))
H = {'Authorization': 'Bearer ' + token}
security_v025.TEST_OPEN = False

print('\n════════ F-01～F-05 异常输入契约 ════════', flush=True)
CASES = [
    ('F-01 credits/adjust 空 body → 400', 'POST', '/api/platform/credits/adjust', {}, 400, 'clubId'),
    ('F-01 credits/adjust 非法 clubId → 400', 'POST', '/api/platform/credits/adjust',
     {'clubId': 'abc', 'amount': 1}, 400, '整数'),
    ('F-02 supplier-shipment 不存在采购单 → 404', 'POST',
     '/api/platform/procurement/purchase-orders/999999999/supplier-shipment', {}, 404, '采购单不存在'),
    ('F-03 warehouse/count 空 body → 404', 'POST', '/api/platform/warehouse/count', {}, 404, '商品不存在'),
    ('F-04 warehouse/move 空 body → 404', 'POST', '/api/platform/warehouse/move', {}, 404, '商品不存在'),
    ('F-05 warehouse/reconcile 不存在商品 → 404', 'POST',
     '/api/platform/warehouse/reconcile/999999999', {}, 404, '商品不存在'),
]
for name, method, path, body, expect, hint in CASES:
    r = c.request(method, path, json=body, headers=H)
    check(name, r.status_code == expect and hint in r.text,
          'status=%s %s' % (r.status_code, r.text[:90]))

# 正常路径未被误伤：合法参数的积分调整应可到达业务层（2xx 或业务 4xx，绝不 5xx）
r = c.post('/api/platform/credits/adjust', json={'clubId': 1, 'amount': 1, 'note': 'QA'}, headers=H)
check('F-01 合法参数仍可到达业务层（无 5xx）', r.status_code < 500,
      'status=%s %s' % (r.status_code, r.text[:90]))

print('\n════════ F-07 路由声明去重 ════════', flush=True)
src = (ROOT / 'app.py').read_text(encoding='utf-8')
pairs = re.findall(r"@app\.(get|post|put|patch|delete)\('(/api/platform/[^']+)'", src)
routes = {(m, r.path) for r in app.app.routes
          if getattr(r, 'path', '').startswith('/api/platform')
          for m in (getattr(r, 'methods', None) or set()) if m in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}}
declared = {(m.upper(), p) for m, p in pairs}
dupes = [k for k, v in __import__('collections').Counter(pairs).items() if v > 1]
check('F-07 源码声明无重复', len(pairs) == len(set(pairs)) and not dupes,
      '声明 %d / 去重 %d' % (len(pairs), len(set(pairs))))
check('F-07 uploads/image 只声明一次',
      sum(1 for m, p in pairs if p == '/api/platform/uploads/image') == 1)
check('F-07 声明数 == 运行时注册数', declared == routes,
      '声明 %d / 注册 %d / 缺失 %d / 额外 %d'
      % (len(declared), len(routes), len(declared - routes), len(routes - declared)))

print('\n════════ F-08 官网公开访问（demo, TEST_OPEN=0）════════', flush=True)
home = c.get('/home')
check('F-08 /home 匿名 200', home.status_code == 200, 'status=%s' % home.status_code)
check('F-08 /home 保留协商缓存且无 noindex',
      'no-cache' in home.headers.get('cache-control', '')
      and not home.headers.get('x-robots-tag'),
      'CC=%s Robots=%s' % (home.headers.get('cache-control'), home.headers.get('x-robots-tag')))
back = c.get('/platform')
check('F-08 后台页 OBS-03 防索引头未回归',
      'noindex' in back.headers.get('x-robots-tag', '')
      and 'no-store' in back.headers.get('cache-control', ''),
      'CC=%s Robots=%s' % (back.headers.get('cache-control'), back.headers.get('x-robots-tag')))
check('F-08 平台数据接口匿名仍 401', c.get('/api/platform/dashboard').status_code in (401, 403),
      'status=%s' % c.get('/api/platform/dashboard').status_code)

# production(strict) 真实模式：独立进程，避免污染本进程的 demo 配置
print('\n════════ F-08 production(strict) 真实模式 ════════', flush=True)
PROD_DB = Path(tempfile.mkdtemp()) / 'findings_prod.db'
prod_env = dict(os.environ)
prod_env.update(
    CLUBOS_DB_PATH=str(PROD_DB),
    CLUBOS_SECURITY_MODE='production',
    MOCK_AI='0',
    COMMERCE_PROVIDER='medusa',
    CLUBOS_AUTH_SIGNING_KEY='qa-' + secrets.token_urlsafe(48),
    CLUBOS_COMMERCE_WEBHOOK_SECRET='qa-' + secrets.token_urlsafe(32),
    CLUBOS_PUBLIC_BASE_URL='https://qa.clubos.example.com',
)
prod_env.pop('CLUBOS_TEST_OPEN', None)
child = r'''
import json, sys
sys.path.insert(0, %r)
import db; db.init_db()
import app
from fastapi.testclient import TestClient
c = TestClient(app.app, raise_server_exceptions=False)
paths = ['/home', '/platform', '/club', '/api/platform/dashboard',
         '/api/public/club-applications']
print('@@JSON@@' + json.dumps({
    p: {'get': c.get(p, follow_redirects=False).status_code,
        'post': (c.post(p, json={}, follow_redirects=False).status_code if 'applications' in p else None)}
    for p in paths
}))
''' % str(ROOT)
proc = subprocess.run([sys.executable, '-c', child], env=prod_env,
                      capture_output=True, text=True, cwd=str(ROOT), timeout=180)
line = next((x for x in proc.stdout.splitlines() if x.startswith('@@JSON@@')), '')
if not line:
    print('  子进程输出:', proc.stdout[-500:], proc.stderr[-800:], flush=True)
    check('F-08 production 子进程可启动', False, 'no @@JSON@@ marker')
else:
    data = json.loads(line[len('@@JSON@@'):])
    check('F-08 strict 模式 /home 匿名 200', data['/home']['get'] == 200,
          'status=%s' % data['/home']['get'])
    check('F-08 strict 模式 /platform 匿名仍被拦',
          data['/platform']['get'] in (401, 403, 303, 307),
          'status=%s' % data['/platform']['get'])
    check('F-08 strict 模式平台数据接口匿名仍被拒',
          data['/api/platform/dashboard']['get'] in (401, 403),
          'status=%s' % data['/api/platform/dashboard']['get'])
    check('F-08 strict 模式「申请体验」表单端点未被鉴权拦截',
          data['/api/public/club-applications']['post'] not in (401, 403),
          'status=%s' % data['/api/public/club-applications']['post'])

print('\n════════ 合计 ════════', flush=True)
print('PASS %d / %d%s' % (len(PASS), len(PASS) + len(FAIL),
                         '' if not FAIL else '  FAIL: ' + ', '.join(FAIL)), flush=True)
sys.exit(1 if FAIL else 0)
