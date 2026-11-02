# -*- coding: utf-8 -*-
"""俱乐部入驻申请来源链路冒烟（2026-10-26 用户需求）。

背景：官网「申请体验」表单（clubos-site → server.js → POST /api/public/club-applications）
与平台手动「新增入驻」（POST /api/platform/clubs）都写 clubs 表。此前平台端
无法区分来源，也没有专门的申请明细视图。

守住四件事：
① 来源列：clubs.source —— 官网申请='website'，平台新增='platform'，
   直插存量行默认 'platform'（db._run_compat_migrations 补列带 DEFAULT）。
② GET /api/platform/clubs 下发 source（主表来源徽标数据源）。
③ GET /api/platform/club-applications 明细接口：items+stats+total、
   待处理排最前、?status=/?source= 筛选、logoUrl 字段存在、仅平台角色可读。
④ 前端护栏：平台端 index.html 有官网申请明细面板（plat-apply-panel /
   applyRows / applyFilters），platform.js 有 loadApplications /
   renderApplications / applyFilterSet / 官网徽标，platform.css 有
   plat-apply-stat，版本号升到 20261027a。

自带临时库隔离（CLUBOS_DB_PATH + app.UPLOAD 指临时目录），绝不碰真库。
production 模式真鉴权（含 CSRF），公开接口按线上语义匿名放行（IP 限速 3/h，
本测试只发 1 次）。
"""
import os
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix='clubos_apply_')
os.environ.update({
    'CLUBOS_DB_PATH': str(Path(tmp.name) / 'apply.sqlite'),
    'CLUBOS_SECURITY_MODE': 'production',
    'MOCK_AI': '0',
    'COMMERCE_PROVIDER': 'medusa',
    'CLUBOS_PUBLIC_BASE_URL': 'https://testserver',
    'CLUBOS_AUTH_SIGNING_KEY': secrets.token_urlsafe(56),
    'CLUBOS_COMMERCE_WEBHOOK_SECRET': secrets.token_urlsafe(40),
})
os.environ.pop('CLUBOS_TEST_OPEN', None)

import app  # noqa: E402
from db import conn  # noqa: E402
from security_v025 import hash_password  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

UPLOAD_TMP = Path(tmp.name) / 'uploads'
UPLOAD_TMP.mkdir(parents=True, exist_ok=True)
app.UPLOAD = UPLOAD_TMP

PASS = []


def check(name, cond, detail=''):
    assert cond, f'FAIL {name} {detail}'
    PASS.append(name)
    print('  ok -', name)


def login(username):
    c = TestClient(app.app, base_url='https://testserver')
    r = c.post('/api/auth/login', json={'username': username, 'password': 'Secure-Test-Password-2026!'})
    assert r.status_code == 200, (username, r.status_code, r.text)
    return c


class Session:
    """production 模式下 cookie 写操作必须带 x-clubos-csrf（security_v025 硬校验）。"""

    def __init__(self, client):
        self.c = client
        self.csrf = client.cookies.get('clubos_csrf') or ''

    def get(self, url):
        return self.c.get(url)

    def post(self, url, **kw):
        return self.c.post(url, headers={'x-clubos-csrf': self.csrf}, **kw)


# ── 夹具：存量俱乐部不带 source（验证 ADD COLUMN 默认值）+ 三角色账号 ─────────
with conn() as c:
    c.execute("INSERT INTO clubs(name,status,plan) VALUES('存量老俱乐部','active','pro')")
    c.execute("INSERT INTO users(name,phone) VALUES('平台测试会员','13900002222')")
    c.execute('INSERT INTO auth_accounts(username,password_hash,role,club_id,user_id) VALUES(?,?,?,?,?)',
              ('plat-admin', hash_password('Secure-Test-Password-2026!'), 'platform', None, None))
    c.execute('INSERT INTO auth_accounts(username,password_hash,role,club_id,user_id) VALUES(?,?,?,?,?)',
              ('plat-club', hash_password('Secure-Test-Password-2026!'), 'club', 1, None))

platform = Session(login('plat-admin'))
club_role = Session(login('plat-club'))
anon = TestClient(app.app, base_url='https://testserver')

# ── ① 官网申请：公开接口匿名提交，source=website ────────────────────────────
r = anon.post('/api/public/club-applications', json={
    'name': '官网体验俱乐部', 'contactName': '王体验', 'contactPhone': '13811112222',
    'city': '成都', 'applicationNote': '每月 4 场徒步，现在用微信群接龙'})
check('官网公开接口匿名提交成功', r.status_code == 200, r.text[:200])
web_id = int(r.json()['applicationId'])
with conn() as c:
    row = c.execute('SELECT source,status,plan,contact_name FROM clubs WHERE id=?', (web_id,)).fetchone()
check('官网申请 source=website', dict(row)['source'] == 'website', dict(row))
check('官网申请默认 pending + starter', dict(row)['status'] == 'pending' and dict(row)['plan'] == 'starter', dict(row))

# ── ② 平台手动新增：source=platform ─────────────────────────────────────────
r = platform.post('/api/platform/clubs', json={'name': '平台代录入俱乐部', 'contactName': '李运营',
                                               'contactPhone': '13733334444', 'city': '昆明'})
check('平台新增入驻成功', r.status_code == 200, r.text[:200])
plat_id = int(r.json()['id'])
with conn() as c:
    row = c.execute('SELECT source FROM clubs WHERE id=?', (plat_id,)).fetchone()
check('平台新增 source=platform', dict(row)['source'] == 'platform', dict(row))
with conn() as c:
    row = c.execute('SELECT source FROM clubs WHERE id=1').fetchone()
check('存量直插行默认 source=platform', dict(row)['source'] == 'platform', dict(row))

# ── ③ /api/platform/clubs 下发 source ───────────────────────────────────────
clubs = platform.get('/api/platform/clubs').json()
by_id = {x['id']: x for x in clubs}
check('列表含 source 字段', all('source' in x for x in clubs))
check('列表里官网申请 source=website', by_id[web_id]['source'] == 'website')

# ── ④ 明细接口：items/stats/筛选/排序/权限 ──────────────────────────────────
r = platform.get('/api/platform/club-applications')
check('明细接口 200', r.status_code == 200, r.text[:200])
d = r.json()
check('明细含 items/stats/total', isinstance(d.get('items'), list) and isinstance(d.get('stats'), dict) and 'total' in d)
check('明细共 3 条', d['total'] == 3, d['total'])
ids = [x['id'] for x in d['items']]
check('待处理排最前', set(ids[:2]) == {web_id, plat_id}, ids)
check('stats 统计正确', d['stats'].get('pending') == 2 and d['stats'].get('active') == 1, d['stats'])
check('明细含 logoUrl 字段', all('logoUrl' in x for x in d['items']))
check('明细字段齐全（备注/来源/时间）',
      all(k in d['items'][0] for k in ('application_note', 'source', 'created_at', 'contact_name', 'contact_phone', 'city')))

d2 = platform.get('/api/platform/club-applications?source=website').json()
check('?source=website 只回官网申请', [x['id'] for x in d2['items']] == [web_id] and d2['total'] == 1, d2['total'])
d3 = platform.get('/api/platform/club-applications?status=pending').json()
check('?status=pending 只回待处理', all(x['status'] == 'pending' for x in d3['items']) and d3['total'] == 2, d3['total'])
d4 = platform.get('/api/platform/club-applications?status=rejected').json()
check('?status=rejected 空列表', d4['total'] == 0 and d4['items'] == [], d4)

check('俱乐部角色读明细被拒', club_role.get('/api/platform/club-applications').status_code == 403)
check('匿名读明细被拒', anon.get('/api/platform/club-applications').status_code == 401)

# ── ⑤ 前端护栏 ──────────────────────────────────────────────────────────────
html = (ROOT / 'static/platform/index.html').read_text(encoding='utf-8')
check('index.html 有官网申请明细面板', all(k in html for k in ('plat-apply-panel', 'id="applyRows"', 'id="applyFilters"', 'id="applyStats"')))
check('index.html 有筛选按钮组', all(f'data-af="{s}"' in html for s in ('all', 'pending', 'active', 'rejected', 'disabled')))
import re as _re_v
_mjs = _re_v.search(r'/static/platform/platform\.js\?v=([0-9A-Za-z]+)', html)
_mcss = _re_v.search(r'/static/platform/platform\.css\?v=([0-9A-Za-z]+)', html)
check('platform.js 带版本号', bool(_mjs and _mjs.group(1)))
# css 与 js 各自独立 bump，不再要求同批同号；各自带 v= 即可
check('platform.css 带版本号', bool(_mcss and _mcss.group(1)),
      '%s' % (_mcss.group(1) if _mcss else None))

js = (ROOT / 'static/platform/platform.js').read_text(encoding='utf-8')
check('platform.js 有明细加载/渲染/筛选', all(k in js for k in ('loadApplications', 'renderApplications', 'applyFilterSet')))
check('platform.js 明细走新接口', '/api/platform/club-applications' in js)
check('platform.js 主表官网徽标', js.count("官网申请</span>") >= 2)
check('platform.js 待处理排最前由后端排序,前端纯渲染', "plat-apply-note" in js)

css = (ROOT / 'static/platform/platform.css').read_text(encoding='utf-8')
check('platform.css 有明细样式', all(k in css for k in ('.plat-apply-panel', '.plat-apply-stat', '.plat-apply-filters')))

print(f'\nALL PASS ({len(PASS)} checks)')
