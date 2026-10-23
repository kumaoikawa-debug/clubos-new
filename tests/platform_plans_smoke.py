# -*- coding: utf-8 -*-
"""总平台端三项改动的冒烟测试（2026-10-23 用户截图反馈三件事）。

①「俱乐部入驻」列表要显示俱乐部 logo，并支持手动增减 AI Credits：
   - GET /api/platform/clubs 下发 logoUrl（平台专属代理地址，无 logo 显式空串）；
   - GET /api/platform/clubs/{id}/logo.{ext} 能取到图，且**只有平台角色**能取
     （俱乐部角色/会员角色/匿名一律拒绝 —— 这是审核入口，不能变成公共资源）；
   - 快捷加减 Credits 走既有 POST /api/platform/credits/adjust（±100 一键），
     余额不足会形成欠账（debtCreated）。

②「分配套餐」从下拉改成卡片平铺 —— 后端契约不变（PATCH .../plan），
   前端必须是 uxDialog + plat-pick-grid 三卡平铺，不能退回 <select>。

③ AI 套餐可增删：
   - POST /api/platform/ai-credit/plans 新建；
   - DELETE /api/platform/ai-credit/plans/{code} 真删除；
   - **被俱乐部订阅占用的套餐绝不能硬删**（订阅表 plan_code 指向它，硬删会让
     「俱乐部 Credits 账户」页的套餐列变成空指针），后端必须 400 + 说人话；
   - 已下架（inactive）的套餐允许删。

④ 附带守住「套餐/账单列表 DOM 嵌套少一个 </div>」这个渲染 bug：
   少闭合会让后续条目被塞进上一个 flex 容器，文字被压成竖排单字。

自带临时库隔离（CLUBOS_DB_PATH 指到临时目录），并把 app.UPLOAD 指向临时目录
（logo 落盘路径改写），绝不碰项目真库与真 uploads。
"""
import io
import os
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp = tempfile.TemporaryDirectory(prefix='clubos_plat_')
os.environ.update({
    'CLUBOS_DB_PATH': str(Path(tmp.name) / 'plat.sqlite'),
    'CLUBOS_SECURITY_MODE': 'production',   # 真角色校验：平台 logo 代理必须挡住非平台角色
    'MOCK_AI': '0',                         # production 模式强制：不得用 mock 冒充真实 Provider
    'COMMERCE_PROVIDER': 'medusa',
    'CLUBOS_PUBLIC_BASE_URL': 'https://testserver',
    'CLUBOS_AUTH_SIGNING_KEY': secrets.token_urlsafe(56),
    'CLUBOS_COMMERCE_WEBHOOK_SECRET': secrets.token_urlsafe(40),
})
os.environ.pop('CLUBOS_TEST_OPEN', None)     # 本测试要真鉴权，不能开内测兜底

import app  # noqa: E402
from db import conn  # noqa: E402
from security_v025 import hash_password  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# logo 落盘目录改写到临时区：app.UPLOAD 是模块级全局，_serve_club_logo 运行时读取，
# 替换后既不污染项目 static/uploads，也不怕删错真实 logo。
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
    """production 模式下 cookie 写操作必须带 x-clubos-csrf（security_v025 的硬校验）。"""

    def __init__(self, client):
        self.c = client
        self.csrf = client.cookies.get('clubos_csrf') or ''

    def get(self, url):
        return self.c.get(url)

    def post(self, url, **kw):
        return self.c.post(url, headers={'x-clubos-csrf': self.csrf}, **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers={'x-clubos-csrf': self.csrf}, **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers={'x-clubos-csrf': self.csrf}, **kw)


# ── 夹具 ────────────────────────────────────────────────────────────────────
PNG = (b'\x89PNG\r\n\x1a\n' + b'\x00' * 64)
with conn() as c:
    c.execute("INSERT INTO clubs(name,status,plan,logo_url) VALUES('带 Logo 俱乐部','active','pro','/api/club/1/settings/logo.png')")
    c.execute("INSERT INTO clubs(name,status,plan) VALUES('无 Logo 俱乐部','active','pro')")
    c.execute("INSERT INTO clubs(name,status,plan,logo_url) VALUES('待审核俱乐部','pending','starter','/api/club/3/settings/logo.png')")
    c.execute("INSERT INTO users(name,phone) VALUES('平台测试会员','13900002222')")
    for username, role, club_id, user_id in [
        ('plat-admin', 'platform', None, None),
        ('plat-club', 'club', 1, None),
        ('plat-member', 'member', None, 1),
    ]:
        c.execute('INSERT INTO auth_accounts(username,password_hash,role,club_id,user_id) VALUES(?,?,?,?,?)',
                  (username, hash_password('Secure-Test-Password-2026!'), role, club_id, user_id))
    c.execute("INSERT INTO club_ai_subscriptions(club_id,plan_code,status,current_period_start,current_period_end,"
              "auto_renew,monthly_fee_snapshot,monthly_credits_snapshot,created_at,updated_at) "
              "VALUES(1,'pro','active','2026-10-01','2026-11-01',1,699,10000,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    # 积分账户行：平台「手动增减 Credits」是改 ai_credit_accounts.balance，
    # 没有账户行时列表里根本看不到这个俱乐部（测试要有可加减的对象）。
    c.execute("INSERT INTO ai_credit_accounts(club_id,balance,monthly_quota) VALUES(1,676,10000)")

# logo 落盘：club1 有文件，club3 有 logo_url 但文件缺失（模拟「图丢了」）
(UPLOAD_TMP / '1' / 'brand').mkdir(parents=True, exist_ok=True)
(UPLOAD_TMP / '1' / 'brand' / 'logo.png').write_bytes(PNG)
(UPLOAD_TMP / '3' / 'brand').mkdir(parents=True, exist_ok=True)

platform = Session(login('plat-admin'))
club = Session(login('plat-club'))
member = Session(login('plat-member'))
anon = TestClient(app.app, base_url='https://testserver')

# ── ① 俱乐部 logo：下发 + 平台专属代理 ─────────────────────────────────────
clubs = platform.get('/api/platform/clubs').json()
check('/api/platform/clubs 返回列表', isinstance(clubs, list) and len(clubs) == 3, len(clubs))
by_id = {x['id']: x for x in clubs}
check('每个俱乐部都带 logoUrl 字段', all('logoUrl' in x for x in clubs), list(by_id))
check('有 logo 的俱乐部下发平台代理地址',
      by_id[1]['logoUrl'] == '/api/platform/clubs/1/logo.png', by_id[1]['logoUrl'])
check('无 logo 的俱乐部 logoUrl 为空串（前端画首字母占位，不是裂图）',
      by_id[2]['logoUrl'] == '', repr(by_id[2]['logoUrl']))
check('待审核俱乐部的 logo 也要下发（审核时要看得见）',
      by_id[3]['logoUrl'] == '/api/platform/clubs/3/logo.png', by_id[3]['logoUrl'])

r = platform.get('/api/platform/clubs/1/logo.png')
check('平台 logo 代理返回图片', r.status_code == 200 and r.content == PNG,
      (r.status_code, r.headers.get('content-type'), len(r.content)))
check('平台 logo 代理带 no-store（换 logo 后浏览器不吃旧图）',
      'no-store' in (r.headers.get('cache-control') or '').lower(), r.headers.get('cache-control'))
r = platform.get('/api/platform/clubs/2/logo.png')
check('没 logo 的俱乐部代理 404', r.status_code == 404, r.status_code)
r = platform.get('/api/platform/clubs/3/logo.png')
check('logo_url 存在但文件丢失 → 404（前端退占位块）', r.status_code == 404, r.status_code)
r = platform.get('/api/platform/clubs/1/logo.txt')
check('不支持的扩展名 404', r.status_code == 404, r.status_code)

check('俱乐部角色取平台 logo 被拒（403）',
      club.get('/api/platform/clubs/1/logo.png').status_code == 403,
      club.get('/api/platform/clubs/1/logo.png').status_code)
check('会员角色取平台 logo 被拒（403）',
      member.get('/api/platform/clubs/1/logo.png').status_code == 403,
      member.get('/api/platform/clubs/1/logo.png').status_code)
check('匿名取平台 logo 被拒（401）',
      anon.get('/api/platform/clubs/1/logo.png').status_code == 401,
      anon.get('/api/platform/clubs/1/logo.png').status_code)
check('平台 logo 代理不受俱乐部归属限制（平台要看全部俱乐部）',
      platform.get('/api/platform/clubs/2/logo.png').status_code == 404)

# ── ② 手动增减 AI Credits（快捷 ±100 与表单调整共用同一接口）────────────────
before = next(x for x in platform.get('/api/platform/credits').json() if x['club_id'] == 1)['balance']
r = platform.post('/api/platform/credits/adjust', json={'clubId': 1, 'amount': 100, 'note': '平台快捷补发'})
check('快捷 +100 成功', r.status_code == 200, r.status_code)
after = next(x for x in platform.get('/api/platform/credits').json() if x['club_id'] == 1)['balance']
check('余额精确 +100', int(after) == int(before) + 100, (before, after))
r = platform.post('/api/platform/credits/adjust', json={'clubId': 1, 'amount': -100, 'note': '平台快捷扣减'})
check('快捷 −100 成功', r.status_code == 200, r.status_code)
after2 = next(x for x in platform.get('/api/platform/credits').json() if x['club_id'] == 1)['balance']
check('余额精确回到原值', int(after2) == int(before), (before, after2))
r = platform.post('/api/platform/credits/adjust', json={'clubId': 1, 'amount': -999999, 'note': '扣到欠账'})
check('超额扣减记欠账而不是报错', r.status_code == 200 and int(r.json().get('debtCreated') or 0) > 0, r.text)

# ── ③ 套餐增删 ─────────────────────────────────────────────────────────────
r = platform.post('/api/platform/ai-credit/plans', json={
    'code': 'team', 'name': 'Team', 'monthlyFee': 99, 'monthlyCredits': 1000,
    'description': '给小团队', 'status': 'active', 'gearDiscount': 1})
check('新建套餐成功', r.status_code == 200, r.text)
plans = {x['code']: x for x in platform.get('/api/platform/ai-credit/plans').json()}
check('新套餐出现在列表里', 'team' in plans and plans['team']['name'] == 'Team', list(plans))
check('新套餐默认无折扣（不自己造折扣）', abs(float(plans['team']['gear_discount']) - 1.0) < 1e-9,
      plans['team']['gear_discount'])

r = platform.post('/api/platform/ai-credit/plans', json={
    'code': 'team', 'name': 'Team 已下架', 'monthlyFee': 99, 'monthlyCredits': 1000,
    'status': 'inactive', 'gearDiscount': 1})
check('下架套餐成功', r.status_code == 200, r.text)
plans = {x['code']: x for x in platform.get('/api/platform/ai-credit/plans').json()}
check('下架后仍在列表但 status=inactive（前端灰化 + 不可新分配）',
      plans['team']['status'] == 'inactive', plans['team']['status'])
r = platform.post('/api/platform/ai-credit/plans', json={'code': 'team', 'name': 'Team', 'monthlyFee': 99,
                                                       'monthlyCredits': 1000, 'status': 'active', 'gearDiscount': 1})
r = platform.delete('/api/platform/ai-credit/plans/team')
check('删除未被占用的套餐成功', r.status_code == 200 and r.json().get('deleted') is True, r.text)
plans = {x['code']: x for x in platform.get('/api/platform/ai-credit/plans').json()}
check('删除后不再出现在列表', 'team' not in plans, list(plans))

# 占用保护：有 active 订阅的套餐不能硬删
r = platform.delete('/api/platform/ai-credit/plans/pro')
check('删除被订阅占用的套餐被拒（400）', r.status_code == 400, (r.status_code, r.text))
check('拒绝时说人话（告诉运营怎么办）',
      '俱乐部' in r.text and ('改绑' in r.text or '下架' in r.text), r.text)
plans = {x['code']: x for x in platform.get('/api/platform/ai-credit/plans').json()}
check('被拒后套餐仍在（数据没被误删）', 'pro' in plans, list(plans))

# paused 订阅同样算占用
with conn() as c:
    c.execute("INSERT INTO club_ai_subscriptions(club_id,plan_code,status,current_period_start,current_period_end,"
              "auto_renew,monthly_fee_snapshot,monthly_credits_snapshot,created_at,updated_at) "
              "VALUES(2,'starter','paused','2026-10-01','2026-11-01',1,299,3000,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
r = platform.delete('/api/platform/ai-credit/plans/starter')
check('paused 订阅也占用套餐（删除被拒 400）', r.status_code == 400, (r.status_code, r.text))

r = platform.delete('/api/platform/ai-credit/plans/nope')
check('删除不存在的套餐 → 400 且不是 500', r.status_code == 400, (r.status_code, r.text))
check('删除不存在时报「套餐不存在」', '不存在' in r.text, r.text)

# 分配套餐（卡片平铺弹窗的提交端点）依然可用
r = platform.patch('/api/platform/clubs/2/plan', json={'planCode': 'enterprise', 'autoRenew': True})
check('分配套餐成功（弹窗提交端点）', r.status_code == 200, r.text)
check('分配后生成待付款账单',
      r.json()['billingOrder']['status'] == 'pending', r.json().get('billingOrder'))
r = platform.patch('/api/platform/clubs/2/plan', json={'planCode': 'not-exist'})
check('分配不存在的套餐被拒（400）', r.status_code == 400, (r.status_code, r.text))

# ── ④ 静态断言：前端形态 + DOM 嵌套配平 ────────────────────────────────────
PLATFORM_JS = io.open(ROOT / 'static/platform/platform.js', encoding='utf-8').read()
MORE_JS = io.open(ROOT / 'static/ux/workflows-platform-more.js', encoding='utf-8').read()
INDEX_HTML = io.open(ROOT / 'static/platform/index.html', encoding='utf-8').read()
PLATFORM_CSS = io.open(ROOT / 'static/platform/platform.css', encoding='utf-8').read()


def template_region(js, marker):
    """取某个 innerHTML= 赋值到 .join('') 之间的模板字面量区域。"""
    i = js.index(marker)
    j = js.index(".join('')", i)
    return js[i:j]


def div_balance(js, marker):
    seg = template_region(js, marker)
    return seg.count('<div'), seg.count('</div>')


for marker, label in [("$('#aiPlanList').innerHTML=", '套餐列表'),
                       ("$('#aiBillingOrders').innerHTML=", 'AI 账单列表')]:
    o, c_ = div_balance(PLATFORM_JS, marker)
    check(f'{label}模板 div 配平（少闭合会让文字竖排）', o == c_, f'<div={o} </div={c_}')

check('套餐列表用卡片版式（.plat-plan）而非旧 list-row',
      "plat-plan" in template_region(PLATFORM_JS, "$('#aiPlanList').innerHTML=")
      and 'list-row' not in template_region(PLATFORM_JS, "$('#aiPlanList').innerHTML="))
check('套餐卡上有编辑/下架/删除三个动作',
      all(k in template_region(PLATFORM_JS, "$('#aiPlanList').innerHTML=")
          for k in ('editPlan(', 'togglePlan(', 'deletePlan(')))
check('充值包额度不重复显示（不再出现两遍 Credits 名称）',
      'Credits 额度' in template_region(PLATFORM_JS, "$('#aiTopupList').innerHTML="))

# 俱乐部列表：logo + 快捷加减积分
check('俱乐部列表渲染 logo 容器', 'plat-club-logo' in PLATFORM_JS)
check('俱乐部列表渲染快捷加减 Credits（±100）',
      'quickCredits(' in PLATFORM_JS and '-100' in PLATFORM_JS and '+100' in PLATFORM_JS)
check('logo 裂图走事件委托兜底（表格整块重渲染，逐个绑 onerror 会漏）',
      "document.addEventListener('error',platLogoFallback,true)" in PLATFORM_JS)

# 生效层：assignPlan 必须是卡片平铺，不能是 <select> 下拉
i = MORE_JS.index('window.assignPlan=')
seg_assign = MORE_JS[i:i + 2600]
check('assignPlan 在生效层 workflows-platform-more.js 里（后加载，覆盖 platform.js）',
      'window.assignPlan=' in MORE_JS)
check('assignPlan 用 uxDialog 自定义 body 平铺卡片', 'uxDialog(' in seg_assign)
check('assignPlan 用 plat-pick-grid 卡片网格', 'plat-pick-grid' in seg_assign)
check('assignPlan 不再用 <select> 下拉', '<select' not in seg_assign and "type:'select'" not in seg_assign)
check('当前订阅的套餐标「当前套餐」', '当前套餐' in seg_assign)
check('确认按钮初始 disabled（没选不许提交）', 'disabled' in seg_assign)
for fn in ('window.editPlan=', 'window.togglePlan=', 'window.deletePlan='):
    check(f'{fn} 存在（套餐增删改入口）', fn in MORE_JS)
check('deletePlan 走 DELETE 方法', "method:'DELETE'" in MORE_JS)

# 覆盖陷阱：platform.js 里不能残留同名 window.xxx 实现（会被后加载文件覆盖、白写）
for fn in ('window.assignPlan=', 'window.editPlan=', 'window.togglePlan=', 'window.deletePlan='):
    check(f'platform.js 不重复定义 {fn}', fn not in PLATFORM_JS)

# 样式与资源引用
check('platform.css 已存在', len(PLATFORM_CSS) > 0)
for cls in ('.plat-pick-grid', '.plat-pick', '.plat-plan-grid', '.plat-plan', '.plat-club-logo__img',
            '.plat-club-logo__ph', '.plat-credits-ops'):
    check(f'platform.css 定义 {cls}', cls in PLATFORM_CSS)
check('index.html 引入 platform.css', '/static/platform/platform.css' in INDEX_HTML)
check('platform.css 在共享样式之后加载（样式优先级：后加载胜）',
      INDEX_HTML.index('/static/platform/platform.css') > INDEX_HTML.index('/static/ux/clubos-polish.css'))
check('platform.js 与 workflows-platform-more.js 引用同一个新版本号',
      'platform.js?v=20261023b' in INDEX_HTML and 'workflows-platform-more.js?v=20261023b' in INDEX_HTML)
check('平台端仍引用共享样式（未被误删）', '/static/shared.css' in INDEX_HTML)
# 套餐区必须占整行：挤在 g2 半栏里时 3 个档位只能纵向排 3 行，正是「操作不方便」的观感来源
check('订阅套餐区占整行（不挤在半栏）',
      '<div class="card section"><div class="panel-title"><div><h3>订阅套餐</h3>' in INDEX_HTML)
check('充值包区占整行', '<h3>充值包</h3>' in INDEX_HTML)
check('套餐区不再被 grid g2 包裹',
      'grid g2' not in INDEX_HTML[INDEX_HTML.index('id="aiPlanList"') - 1200:INDEX_HTML.index('id="aiPlanList"')],
      'aiPlanList 前 1200 字符内仍有 grid g2')

print('\nPLATFORM PLANS SMOKE: %d checks passed' % len(PASS))
