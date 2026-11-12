# -*- coding: utf-8 -*-
"""俱乐部小程序资产管理冒烟（2026-10-26 用户需求三件套）。

① 小程序信息：认证信息（appid/主体/类目/实名/年审/支付品牌）+ 授权状态 +
   体验者；PUT 接受 camelCase（前端约定），GET 下发 camelCase。
② 小程序助手：员工管理（绑定/编辑权限/删除）+ 角色管理（系统预置 3 角色、
   权限点白名单、被占用角色禁删、系统角色禁删）。
③ 小程序标准设置：base/share/pay 三组 JSON，深合并默认值（新增项对存量
   俱乐部自动可见）、白名单外键丢弃、自定义支付方式。
另守住：头像/小程序码上传与登录态代理读、前端三个新 tab 与 club.js 版本。

production 模式真鉴权 + 临时库/临时 uploads，绝不碰真库。
"""
import io
import os
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix='clubos_mpsmoke_')
os.environ.update({
    'CLUBOS_DB_PATH': str(Path(tmp.name) / 'mp.sqlite'),
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


# 夹具：一个俱乐部 + 一个 club 管理员（真登录）
with conn() as c:
    c.execute("INSERT INTO clubs(name,status,plan) VALUES('小程序测试俱乐部','active','pro')")
    c.execute("INSERT INTO users(name,phone) VALUES('平台测试会员','13900002222')")
    c.execute('INSERT INTO auth_accounts(username,password_hash,role,club_id,user_id) VALUES(?,?,?,?,?)',
              ('mp-admin', hash_password('Secure-Test-Password-2026!'), 'club', 1, None))

c_admin = TestClient(app.app, base_url='https://testserver')
r = c_admin.post('/api/auth/login', json={'username': 'mp-admin', 'password': 'Secure-Test-Password-2026!'})
assert r.status_code == 200, r.text
CSRF = c_admin.cookies.get('clubos_csrf') or ''


def put(url, **kw):
    return c_admin.put(url, headers={'x-clubos-csrf': CSRF}, **kw)


def post(url, **kw):
    return c_admin.post(url, headers={'x-clubos-csrf': CSRF}, **kw)


def patch(url, **kw):
    return c_admin.patch(url, headers={'x-clubos-csrf': CSRF}, **kw)


def delete(url, **kw):
    return c_admin.delete(url, headers={'x-clubos-csrf': CSRF}, **kw)


# ── ① 小程序信息 ────────────────────────────────────────────────────────────
r = c_admin.get('/api/club/1/miniprogram')
check('GET 空信息返回全空骨架', r.status_code == 200 and r.json()['appName'] == '' and r.json()['serviceCategories'] == [])
r = put('/api/club/1/miniprogram', json={
    'appid': 'wxc014caaaa15c0fb2', 'appName': '远拓旅游Toptravel', 'appIntro': '户外活动',
    'subjectType': '企业', 'subjectName': '成都远拓户外旅游有限公司',
    'serviceCategories': ['旅游服务>旅行社', '户外运动>徒步'],
    'realnameStatus': '实名验证成功', 'annualReviewAt': '2027-09-02 10:58:40',
    'payBrandName': '远拓旅游', 'payBrandId': '18226',
    'currentVersion': '7.9.4', 'privacyStatus': '已配置', 'geoPermission': '已开通',
    'recordStatus': '已备案', 'releaseStatus': '已发布', 'authorized': True,
    'testers': [{'nickname': '小野', 'wxid': 'xy_001', 'boundAt': '2026-10-26 10:00'}]})
check('PUT camelCase 全字段成功', r.status_code == 200, r.text[:200])
info = r.json()['info']
check('信息回读正确', info['appName'] == '远拓旅游Toptravel' and info['appid'] == 'wxc014caaaa15c0fb2'
      and info['subjectName'] == '成都远拓户外旅游有限公司' and info['authorized'] is True
      and info['currentVersion'] == '7.9.4')
check('服务类目/体验者 JSON 往返', info['serviceCategories'] == ['旅游服务>旅行社', '户外运动>徒步']
      and info['testers'][0]['nickname'] == '小野')
r = put('/api/club/1/miniprogram', json={'serviceCategories': [f'类目{i}' for i in range(11)]})
check('服务类目超 10 个被拒', r.status_code == 400)
r = put('/api/club/1/miniprogram', json={'testers': [{'nickname': f't{i}', 'wxid': '', 'boundAt': ''} for i in range(25)]})
check('体验者超 20 个被截断到 20', r.status_code == 200 and len(r.json()['info']['testers']) == 20)

# 头像 / 小程序码上传 + 代理读
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
r = post('/api/club/1/miniprogram/image/avatar', files={'file': ('a.png', io.BytesIO(PNG), 'image/png')})
check('头像上传成功', r.status_code == 200 and '/miniprogram/avatar.png' in r.json()['url'], r.text[:150])
avatar_url = r.json()['url']
r = post('/api/club/1/miniprogram/image/qrcode', files={'file': ('q.png', io.BytesIO(PNG), 'image/png')})
check('小程序码上传成功', r.status_code == 200)
r = c_admin.get(avatar_url)
check('头像登录态代理可读', r.status_code == 200 and r.headers['content-type'].startswith('image/'))
r = c_admin.get('/api/club/1/miniprogram/avatar.txt')
check('代理读拒绝非图片扩展名', r.status_code == 404)
r = post('/api/club/1/miniprogram/image/badkind', files={'file': ('a.png', io.BytesIO(PNG), 'image/png')})
check('未知图片类型被拒', r.status_code == 400)
info = c_admin.get('/api/club/1/miniprogram').json()
check('GET 后头像/码 URL 已入 info', info['avatarUrl'].endswith('/avatar.png') and info['qrcodeUrl'].endswith('/qrcode.png'))

# 设置类图片（版权图标/转发封面/朋友圈封面）走上传端点，落 settings_json
r = post('/api/club/1/miniprogram/image/friendCover', files={'file': ('f.png', io.BytesIO(PNG), 'image/png')})
check('转发封面上传成功（kind 大小写不敏感）', r.status_code == 200, r.text[:150])
s = c_admin.get('/api/club/1/miniprogram/settings').json()['settings']
check('封面上传写入 settings.share.friendCover', s['share']['friendCover'].endswith('/friendcover.png'))
r = c_admin.get('/api/club/1/miniprogram/friendcover.png')
check('封面登录态代理可读', r.status_code == 200 and r.headers['content-type'].startswith('image/'))
r = put('/api/club/1/miniprogram/settings', json={'settings': {'share': {'friendCover': ''}}})
check('PUT 空值可清除封面', r.status_code == 200 and r.json()['settings']['share']['friendCover'] == '')

# ── ② 管理助手：角色 ────────────────────────────────────────────────────────
r = c_admin.get('/api/club/1/mp/roles')
d = r.json()
check('GET 角色自动预置 3 个系统角色', r.status_code == 200 and len(d['roles']) == 3
      and all(x['system'] for x in d['roles']), [x['name'] for x in d['roles']])
check('权限点清单 8 项下发', len(d['perms']) == 8)
sys_admin = [x for x in d['roles'] if x['name'] == '管理员'][0]
check('管理员角色拥有全部权限', len(sys_admin['perms']) == 8)
r = post('/api/club/1/mp/roles', json={'name': '客服', 'perms': ['registrations', 'members', 'hacker'], 'remark': '只管报名与会员'})
check('新建角色（白名单外权限点丢弃）', r.status_code == 200 and r.json()['role']['perms'] == ['registrations', 'members'])
rid = r.json()['role']['id']
r = patch(f'/api/club/1/mp/roles/{rid}', json={'name': '客服专员', 'perms': ['registrations']})
check('编辑角色成功', r.status_code == 200 and r.json()['role']['name'] == '客服专员' and r.json()['role']['perms'] == ['registrations'])
r = delete(f'/api/club/1/mp/roles/{sys_admin["id"]}')
check('系统预置角色禁删', r.status_code == 400)

# ── ② 管理助手：员工 ────────────────────────────────────────────────────────
r = post('/api/club/1/mp/staff', json={'name': 'oikawa', 'wxid': 'oikawa_wx', 'phone': '13811112222', 'roleId': rid, 'loginAccount': 'oikawa.wz', 'password': 'Init#2026'})
check('绑定员工成功', r.status_code == 200 and r.json()['staff']['roleName'] == '客服专员')
sid = r.json()['staff']['id']
r = delete(f'/api/club/1/mp/roles/{rid}')
check('被占用角色删除被拒', r.status_code == 400)
r = patch(f'/api/club/1/mp/staff/{sid}', json={'roleId': sys_admin['id']})
check('编辑员工权限（换角色）', r.status_code == 200 and r.json()['staff']['roleName'] == '管理员')
r = post('/api/club/1/mp/staff', json={'name': '坏手机号', 'phone': '12345'})
check('非法手机号被拒', r.status_code == 400)
r = post('/api/club/1/mp/staff', json={'name': '坏角色', 'roleId': 99999})
check('不存在的角色被拒', r.status_code == 400)
delete(f'/api/club/1/mp/staff/{sid}')
r = delete(f'/api/club/1/mp/roles/{rid}')
check('员工删除后角色可删', r.status_code == 200)

# ── ③ 小程序标准设置 ────────────────────────────────────────────────────────
r = c_admin.get('/api/club/1/miniprogram/settings')
d = r.json()
check('GET 设置深合并默认值', r.status_code == 200 and set(d['settings'].keys()) == {'base', 'share', 'pay'}
      and d['settings']['base']['firstAuthNickname'] is True and d['settings']['pay']['mixedPay'] is True)
r = put('/api/club/1/miniprogram/settings', json={'settings': {
    'base': {'forceGPS': True, 'privacyStyle': 'component', 'hack': 1},
    'share': {'friendTitle': '让山野·走进日常生活!'},
    'pay': {'matrix': {'signup': {'balance': True, 'wx': True}}, 'wxMchId': '1630023229'}}})
s = r.json()['settings']
check('PUT 三组部分更新成功', r.status_code == 200 and s['base']['forceGPS'] is True
      and s['base']['privacyStyle'] == 'component' and s['share']['friendTitle'] == '让山野·走进日常生活!'
      and s['pay']['matrix']['signup'] == {'balance': True, 'wx': True} and s['pay']['wxMchId'] == '1630023229')
check('白名单外键被丢弃', 'hack' not in s['base'])
check('未提交的组保留默认', s['base']['firstAuthNickname'] is True and s['pay']['mixedPay'] is True)
r = c_admin.get('/api/club/1/miniprogram/settings')
check('GET 二次读取持久化', r.json()['settings']['base']['forceGPS'] is True and r.json()['settings']['pay']['wxMchId'] == '1630023229')

# ── 权限：别的俱乐部管理员不能跨库读写 ────────────────────────────────────────
with conn() as c:
    c.execute("INSERT INTO clubs(name,status,plan) VALUES('另一家俱乐部','active','pro')")
    c.execute('INSERT INTO auth_accounts(username,password_hash,role,club_id,user_id) VALUES(?,?,?,?,?)',
              ('other-admin', hash_password('Secure-Test-Password-2026!'), 'club', 2, None))
other = TestClient(app.app, base_url='https://testserver')
other.post('/api/auth/login', json={'username': 'other-admin', 'password': 'Secure-Test-Password-2026!'})
ocsrf = other.cookies.get('clubos_csrf') or ''
r = other.put('/api/club/1/miniprogram', headers={'x-clubos-csrf': ocsrf}, json={'appName': '越权改'})
check('跨俱乐部写被拒', r.status_code == 403, r.status_code)
r = other.get('/api/club/1/mp/staff')
check('跨俱乐部读被拒', r.status_code == 403)

# ── 前端护栏 ────────────────────────────────────────────────────────────────
html = (ROOT / 'static/club/index.html').read_text(encoding='utf-8')
check('设置页有三个新 tab', all(f"setTab('{t}')" in html for t in ('mpinfo', 'mpstaff', 'mpset')))
check('三个 pane 骨架存在', all(f'id="pane-{p}"' in html for p in ('mpinfo', 'mpstaff', 'mpset')))
check('员工/角色双子tab与容器', 'mpStaffTabs' in html and 'id="mpStaffBody"' in html and 'id="mpRolesBody"' in html)
# 版本号每次发版都会变 —— 只断言「带缓存版本号」，别写死某个日期（2026-10-27 修）。
_pat = 'src="/static/club/club.js?v='
_idx = html.find(_pat)
_ver = html[(_idx + len(_pat)):_idx + len(_pat) + 14].split('"')[0] if _idx >= 0 else ''
check('club.js 带缓存版本号 %s' % (_ver or '缺失'), bool(_ver))
js = (ROOT / 'static/club/club.js').read_text(encoding='utf-8')
check('club.js 含三大板块函数', all(k in js for k in ('loadMpInfo', 'loadMpStaff', 'loadMpRoles', 'loadMpSettings', 'mpInfoEdit', 'saveMpInfo', 'mpStaffForm', 'mpRoleForm', 'saveMpSetGroup')))
check('club.js setTab pane 数组含新板块', "'mpinfo','mpstaff','mpset'" in js)
# 2026-10-26 用户反馈：认证状态/版本号/体验者等由微信侧自动同步，老板不手填
check('编辑表单已移除系统自动字段（实名/版本/备案/发布/体验者）',
      'realnameStatus' not in js[js.index('function mpInfoEdit'):js.index('async function saveMpInfo')]
      and 'currentVersion' not in js[js.index('function mpInfoEdit'):js.index('async function saveMpInfo')]
      and 'mpTesters' not in js[js.index('function mpInfoEdit'):js.index('async function saveMpInfo')])
check('小程序码系统自动生成（ClubOSQR 本地生成，无上传按钮）',
      'ClubOSQR.draw' in js and js.count("mpUploadImage('qrcode'") == 0)
check('头像自动回落俱乐部 Logo + 恢复自动', 'MP_LOGO' in js and 'avatarClear' in js and 'mpClearAvatar' in js)
check('自动同步态展示（不显示「未填写」语气）', '微信授权后自动同步' in js)
# 2026-10-26 用户反馈第二轮：图片全部本地上传（不是贴 URL）；修「编辑信息点不动」；合规流程梳理
check('设置图片走上传控件（imgField 上传+移除）',
      'const imgField=' in js and 'async function mpUploadSetImage' in js and 'async function mpClearSetImage' in js)
check('版权图标/转发封面/朋友圈封面用 imgField 渲染（无贴 URL 输入）',
      "imgField('copyrightIcon'" in js and "imgField('friendCover'" in js and "imgField('momentCover'" in js)
check('合规流程卡 7 步骤（含旅行社许可证/ICP/隐私指引提示）',
      '上线流程与合规' in js and '旅行社业务经营许可证' in js and '小程序备案（ICP）' in js
      and "step('1'" in js and js.count("step('") == 7)
check('编辑信息点击后滚动到表单（修「点不动」）',
      'scrollIntoView' in js[js.index('function mpInfoEdit'):js.index('async function saveMpInfo')])

print(f'\nALL PASS ({len(PASS)} checks)')
