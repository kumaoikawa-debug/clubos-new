# -*- coding: utf-8 -*-
"""员工权限分配管理 · 端到端回归（2026-11-16）。

覆盖总平台 / 俱乐部两端：员工账户 CRUD、岗位/视图分配、真实登录、岗位门禁、
受限员工写操作拦截、停用/删除后会话失效、防自锁护栏。
环境纪律：独立 /tmp 临时库；CLUBOS_TEST_OPEN=0；不 push / 发布 / 部署 / 碰真库。
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
    CLUBOS_PLATFORM_ADMIN_USER='staffadmin',
    CLUBOS_PLATFORM_ADMIN_PASS='StaffTest@2026Admin!',
    CLUBOS_DB_PATH=str(Path(tempfile.mkdtemp(prefix='clubos-staff-')) / 'auth.db'),
)

import app  # noqa: E402
from security_v025 import _hash  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(app.app)
ROWS, FAILS = [], []
H = {}


def check(name, cond, detail=''):
    ROWS.append((name, bool(cond)))
    print(('  PASS  ' if cond else '  FAIL  ') + name + (f'   | {detail}' if detail else ''), flush=True)
    if not cond:
        FAILS.append(name)


def call(key, method, url, payload=None):
    return c.request(method, url, json=payload, headers=H.get(key, {}))


def login(username, password):
    r = c.post('/api/auth/login', json={'username': username, 'password': password})
    if r.status_code != 200:
        return r.status_code, None
    return r.status_code, r.json()


def make_session(key, role, club_id=None):
    with app.conn() as db:
        db.execute("INSERT INTO auth_accounts(username,password_hash,role,status,club_id) VALUES(?,?,?,'active',?)",
                   ('qa-' + key, 'test-only', role, club_id))
        aid = int(db.execute('SELECT last_insert_rowid()').fetchone()[0])
        token = secrets.token_urlsafe(32)
        db.execute('INSERT INTO auth_sessions(token_hash,account_id,csrf_hash,expires_at) VALUES(?,?,?,?)',
                   (_hash(token), aid, _hash('qa-csrf'), int(time.time()) + 3600))
    H[key] = {'Authorization': 'Bearer ' + token}


print('══════ A. 总平台 · 员工账户与岗位 ══════', flush=True)
st, admin = login('staffadmin', 'StaffTest@2026Admin!')
check('A1 平台管理员真实登录', st == 200 and admin['identity']['platformDepartment'] == 'all', str(st))
H['admin'] = {'Authorization': 'Bearer ' + admin['token']}
r = call('admin', 'GET', '/api/platform/staff-accounts')
check('A2 员工列表可读', r.status_code == 200 and any(x['username'] == 'staffadmin' for x in r.json()), str(r.status_code))
r = call('admin', 'GET', '/api/platform/staff-perm-tree')
check('A3 岗位权限树可读', r.status_code == 200 and len(r.json().get('tree') or []) >= 4, str(r.status_code))
r = call('admin', 'POST', '/api/platform/staff-accounts',
         {'username': 'ops.zhang', 'password': 'StaffOps@20261116', 'displayName': '张运营', 'department': 'operations'})
check('A4 新建运营岗员工', r.status_code == 200 and r.json()['department'] == 'operations', f'{r.status_code} {r.text[:100]}')
OID = r.json().get('id') if r.status_code == 200 else None
st, ops = login('ops.zhang', 'StaffOps@20261116')
check('A5 员工用独立账号登录成功', st == 200 and ops['identity']['platformDepartment'] == 'operations', str(st))
H['ops'] = {'Authorization': 'Bearer ' + ops['token']}
check('A6 非管理员无权管理员工', call('ops', 'GET', '/api/platform/staff-accounts').status_code == 403)
check('A7 运营岗不能改库存（岗位门禁仍生效）',
      call('ops', 'POST', '/api/platform/products', {'name': 'X', 'stock': 5}).status_code == 403)
r = call('admin', 'PATCH', f'/api/platform/staff-accounts/{OID}', {'displayName': '张运营2', 'department': 'finance'})
check('A8 编辑姓名/调整岗位', r.status_code == 200 and r.json()['department'] == 'finance', f'{r.status_code} {r.text[:100]}')
r = call('admin', 'PATCH', f'/api/platform/staff-accounts/{OID}', {'password': 'StaffNew@20261116'})
st, _ = login('ops.zhang', 'StaffNew@20261116')
check('A9 重置密码后新口令可登录', r.status_code == 200 and st == 200, f'patch={r.status_code} login={st}')
r = call('admin', 'PATCH', f'/api/platform/staff-accounts/{OID}', {'status': 'disabled'})
st, _ = login('ops.zhang', 'StaffNew@20261116')
check('A10 停用后无法登录', r.status_code == 200 and st == 401, f'patch={r.status_code} login={st}')
check('A11 不能停用自己', call('admin', 'PATCH', f"/api/platform/staff-accounts/{admin['identity']['accountId']}", {'status': 'disabled'}).status_code == 400)
check('A12 不能删除自己', call('admin', 'DELETE', f"/api/platform/staff-accounts/{admin['identity']['accountId']}").status_code == 400)
r = call('admin', 'POST', '/api/platform/staff-accounts',
         {'username': 'root2', 'password': 'StaffRoot@20261116', 'displayName': '备用管理员', 'department': 'all'})
check('A13 可新增超级管理员', r.status_code == 200, f'{r.status_code} {r.text[:100]}')
RID = r.json().get('id') if r.status_code == 200 else None
check('A14 登录账号重复被拒', call('admin', 'POST', '/api/platform/staff-accounts',
      {'username': 'root2', 'password': 'StaffRoot@20261116', 'displayName': '重名', 'department': 'all'}).status_code == 409)
check('A15 删除员工', call('admin', 'DELETE', f'/api/platform/staff-accounts/{OID}').status_code == 200)
check('A16 删除后旧会话立即失效', call('ops', 'GET', '/api/auth/me').status_code == 401)
if RID:
    call('admin', 'DELETE', f'/api/platform/staff-accounts/{RID}')

print('══════ B. 俱乐部 · 员工账户与视图权限 ══════', flush=True)
make_session('owner', 'club', 1)
r = call('owner', 'GET', '/api/club/1/staff-perm-tree')
check('B1 模块权限树可读', r.status_code == 200 and len(r.json().get('groups') or []) >= 4, f'{r.status_code} {r.text[:100]}')
r = call('owner', 'POST', '/api/club/1/staff-accounts',
         {'username': 'mall.staff', 'password': 'ClubStaff@20261116', 'displayName': '商城专员', 'views': ['mall-orders']})
check('B2 新建受限员工（仅商城订单）', r.status_code == 200 and r.json()['views'] == ['mall-orders'], f'{r.status_code} {r.text[:140]}')
CID = r.json().get('id') if r.status_code == 200 else None
check('B3 未知视图被拒', call('owner', 'POST', '/api/club/1/staff-accounts',
      {'username': 'bad.view', 'password': 'ClubStaff@20261116', 'displayName': '错误', 'views': ['no-such-view']}).status_code == 400)
st, staff = login('mall.staff', 'ClubStaff@20261116')
check('B4 员工登录并回传视图白名单', st == 200 and staff['identity'].get('staffViews') == ['mall-orders'], f'{st} {str(staff)[:100]}')
H['staff'] = {'Authorization': 'Bearer ' + staff['token']}
check('B5 受限员工无权管理员工', call('staff', 'GET', '/api/club/1/staff-accounts').status_code == 403)
check('B6 受限员工可读取后台数据', call('staff', 'GET', '/api/club/1/settings').status_code == 200)
check('B7 越权写被拦截（设置不在白名单）',
      call('staff', 'PATCH', '/api/club/1/settings', {'name': '越权改名'}).status_code == 403)
r = call('owner', 'PATCH', f'/api/club/1/staff-accounts/{CID}', {'views': ['mall-orders', 'settings']})
check('B8 增授模块权限', r.status_code == 200 and 'settings' in r.json()['views'], f'{r.status_code} {r.text[:100]}')
check('B9 授权后同一写操作放行', call('staff', 'PATCH', '/api/club/1/settings', {}).status_code != 403)
r = call('owner', 'PATCH', f'/api/club/1/staff-accounts/{CID}', {'password': 'ClubNew@20261116'})
st, _ = login('mall.staff', 'ClubNew@20261116')
check('B10 重置密码后新口令可登录', r.status_code == 200 and st == 200, f'patch={r.status_code} login={st}')
r = call('owner', 'PATCH', f'/api/club/1/staff-accounts/{CID}', {'status': 'disabled'})
st, _ = login('mall.staff', 'ClubNew@20261116')
check('B11 停用后无法登录', r.status_code == 200 and st == 401, f'patch={r.status_code} login={st}')
check('B12 老板账户不在员工列表（不可被误删）',
      all(x['username'] != 'qa-owner' for x in call('owner', 'GET', '/api/club/1/staff-accounts').json()))
check('B13 删除员工', call('owner', 'DELETE', f'/api/club/1/staff-accounts/{CID}').status_code == 200)
check('B14 删除后旧会话失效', call('staff', 'GET', '/api/auth/me').status_code == 401)

print('\n────', flush=True)
print(f'STAFF MANAGEMENT: {sum(1 for _, ok in ROWS if ok)}/{len(ROWS)}', flush=True)
if FAILS:
    print('FAILED: ' + ' | '.join(FAILS), flush=True)
    sys.exit(1)
print('STAFF MANAGEMENT OK', flush=True)
