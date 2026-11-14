# -*- coding: utf-8 -*-
"""设置中枢缺陷修复 + UI/UX 优化后 · 四端真浏览器完整验证（隔离实例 CLUBOS_GATE_BASE，默认 8124）。"""
import json, os, sys, re
from playwright.sync_api import sync_playwright

# 发布前门禁注入：实例地址 / 输出目录 / demo 口令均可覆盖，禁止硬编码临时路径。
B = os.getenv('CLUBOS_GATE_BASE', 'http://127.0.0.1:8124').rstrip('/')
OUT = os.getenv('CLUBOS_GATE_OUT', os.path.join('/tmp', 'clubos_hub_gate'))
PW = os.getenv('STAGING_PASSWORD', 'Staging-2026-ClubOS-demo!')
os.makedirs(OUT, exist_ok=True)
R = []

def ck(cid, title, cond, evi=''):
    R.append({'id': cid, 'title': title, 'ok': bool(cond), 'evidence': str(evi)[:360]})
    print(('PASS ' if cond else 'FAIL ') + cid + ' ' + title + ' | ' + str(evi)[:210], flush=True)

def login(pg, user):
    pg.goto(B + '/login', wait_until='domcontentloaded')
    return pg.evaluate("""async u=>{const r=await fetch('/api/auth/login',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u[0],password:u[1]})});return r.status}""", [user, PW])

# 端 / 入口 / 需要登录的账号 / 期望的最小导航控件
# 领队端是「场次为空」的单页空态，导航控件天然只有 1 个退出按钮，不能用后台端阈值衡量。
ENDS = [
    ('club',     '/club?club_id=1',     'club1_admin',    '.nav button[data-view]', 10, 80),
    ('platform', '/platform',          'platform_admin', '.nav button[data-view]', 10, 80),
    ('web',      '/web?club_id=1',     None,             '.web-nav button, .web-nav a, [data-nav]', 3, 80),
    ('leader',   '/leader?club_id=1',  'leader1',        'button, [data-view], .nav button', 1, 50),
]

with sync_playwright() as w:
    b = w.chromium.launch(channel='chrome', headless=True)
    for name, url, user, sel, minimum, min_body in ENDS:
        ctx = b.new_context(viewport={'width': 1280, 'height': 900})
        ctx.new_cdp_session(ctx.new_page()).send('Network.setCacheDisabled', {'cacheDisabled': True})
        pg = ctx.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        if user:
            ck('E-%s-1' % name, '%s 端后台登录成功' % name, login(pg, user) == 200)
        resp = pg.goto(B + url, wait_until='load')
        pg.wait_for_timeout(1500)
        dom = pg.evaluate("""s=>({title:document.title,url:location.pathname,
          navs:document.querySelectorAll(s).length,
          body:(document.body.innerText||'').trim().length,
          scripts:[...document.querySelectorAll('script[src]')].map(x=>x.getAttribute('src')),
          overflow:document.documentElement.scrollWidth>innerWidth+1})""", sel)
        ck('E-%s-2' % name, '%s 端页面 200 且主体渲染' % name,
           resp is not None and resp.status == 200 and dom['navs'] >= minimum and dom['body'] > min_body
           and '安全登录' not in dom['title'],
           {'status': resp.status if resp else None, 'navs': dom['navs'], 'bodyLen': dom['body'], 'title': dom['title']})
        ck('E-%s-3' % name, '%s 端静态脚本均带版本戳' % name,
           len(dom['scripts']) > 0 and all(('?v=' in s) for s in dom['scripts']), dom['scripts'])
        ck('E-%s-4' % name, '%s 端无 JS 运行时错误' % name, not errs, errs[:3])
        # 各端入口 index.html 的版本戳与设置中枢独立页互不干扰
        if name == 'club':
            hub = ctx.new_page()
            hub.goto(B + '/static/club/settings-hub.html?club_id=1', wait_until='load')
            hub.wait_for_timeout(1200)
            hd = hub.evaluate("""()=>({cards:document.querySelectorAll('#modules .mod').length,
              prefs:document.querySelectorAll('#prefs .hub-ctl').length,
              hubbar:!!document.querySelector('#hubbar'),
              token:[...document.querySelectorAll('script[src]')].map(x=>x.getAttribute('src'))
                .find(s=>s.includes('settings-hub.js'))||''})""")
            ck('E-club-5', '设置中枢独立页加载 19 卡 / 4 偏好 / 吸顶条 / 带缓存版本戳',
               hd['cards'] == 19 and hd['prefs'] == 4 and hd['hubbar']
               and bool(re.search(r'settings-hub\.js\?v=[0-9a-z]{6,}', hd['token'])), hd)
        ctx.close()
    b.close()

s = {'pass': sum(1 for x in R if x['ok']), 'fail': sum(1 for x in R if not x['ok']), 'items': R}
json.dump(s, open(os.path.join(OUT, 'results_four_end_verify.json'), 'w'), ensure_ascii=False, indent=2)
print('\n=== FOUR-END VERIFY %d PASS / %d FAIL ===' % (s['pass'], s['fail']), flush=True)
sys.exit(0 if s['fail'] == 0 else 1)
