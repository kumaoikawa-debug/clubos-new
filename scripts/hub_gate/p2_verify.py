# -*- coding: utf-8 -*-
"""设置中枢 P2 修复 · 端到端验收（隔离实例 CLUBOS_GATE_BASE，默认 8124，真 Chrome + API）。"""
import json, os, sys, http.cookiejar, urllib.request, urllib.error
from playwright.sync_api import sync_playwright

# 发布前门禁注入：实例地址 / 输出目录 / demo 口令均可覆盖，禁止硬编码临时路径。
B = os.getenv('CLUBOS_GATE_BASE', 'http://127.0.0.1:8124').rstrip('/')
OUT = os.getenv('CLUBOS_GATE_OUT', os.path.join('/tmp', 'clubos_hub_gate'))
PW = os.getenv('STAGING_PASSWORD', 'Staging-2026-ClubOS-demo!')
os.makedirs(OUT, exist_ok=True)
R = []
def ck(cid, title, cond, evi=''):
    R.append({'id': cid, 'title': title, 'ok': bool(cond), 'evidence': str(evi)[:360]})
    print(('PASS ' if cond else 'FAIL ') + cid + ' ' + title + ' | ' + str(evi)[:200], flush=True)

def opener(user=None):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), urllib.request.ProxyHandler({}))
    if user:
        r = urllib.request.Request(B + '/api/auth/login', method='POST',
            data=json.dumps({'username': user, 'password': PW}).encode(),
            headers={'Content-Type': 'application/json'})
        op.open(r, timeout=10).read()
    return op, cj

def req(op, path, method='GET', body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(B + path, data=data, method=method,
                               headers={'Content-Type': 'application/json'})
    try: resp = op.open(r, timeout=15)
    except urllib.error.HTTPError as e: resp = e
    txt = resp.read().decode('utf-8', 'replace')
    try: j = json.loads(txt)
    except Exception: j = txt
    return resp.status, j

club, club_cj = opener('club1_admin')
plat, _ = opener('platform_admin')
anon, _ = opener()
CB, PB = '/api/club/1/settings-hub', '/api/platform/settings-hub'

# 清场
req(club, CB + '/reset', 'POST', {}); req(plat, PB + '/reset', 'POST', {})

# ── BUG-HUB-009 统计口径 ──────────────────────────────────────────────────
req(plat, PB, 'PATCH', {'changes': {'notify.quietHoursStart': 20}})
a = req(club, CB)[1]['summary']['overriddenPreferences']
req(club, CB, 'PATCH', {'changes': {'notify.quietHoursStart': 21}})
b = req(club, CB)[1]['summary']['overriddenPreferences']
d = req(plat, PB)[1]['summary']['overriddenPreferences']
ck('P2-09', '「本层级覆盖」只统计本层级自己写的覆盖', a == 0 and b == 1 and d == 1,
   {'平台默认后俱乐部': a, '俱乐部覆盖后': b, '平台自己': d})
ck('P2-09b', '快照下发 layer 字段，前端可识别当前层级',
   req(club, CB)[1].get('layer') == 'club' and req(plat, PB)[1].get('layer') == 'platform', '')

# ── BUG-HUB-010 RESET 审计操作人 ──────────────────────────────────────────
req(club, CB, 'PATCH', {'changes': {'notify.quietHoursStart': 21}})
prev = req(club, CB + '/audit')[1].get('items', [])
max_id = max([x.get('id') or 0 for x in prev], default=0)
req(club, CB + '/reset', 'POST', {})
aud = req(club, CB + '/audit')[1].get('items', [])
# audit_list 有 50 条上限，用 id 而不是条数差来识别本轮新增
fresh = [x for x in aud if (x.get('id') or 0) > max_id]
resets = [x for x in fresh if x['action'] == 'RESET']
ck('P2-10', '本轮 RESET 审计记录写入操作人', bool(resets) and all(x.get('operator') for x in resets),
   {'newRows': len(fresh), 'operators': [x.get('operator') for x in resets[:3]]})

# ── BUG-HUB-017 用户侧文案 ────────────────────────────────────────────────
req(club, CB, 'PATCH', {'changes': {'module.checkin': False}})
st, body = req(anon, '/api/public/clubs/1/engagement/checkin', 'POST', {'userId': 1})
txt = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
ck('P2-17', '关闭后会员侧报错不再出现「设置中枢」后台术语',
   st == 403 and '该功能暂未开放' in txt and '设置中枢' not in txt, {'status': st, 'body': txt[:160]})

# ── BUG-HUB-008 C 端消费脱敏视图（先造好状态）────────────────────────────
req(club, CB, 'PATCH', {'changes': {'notify.outboundEnabled': False, 'module.storedValue': False}})
st_pub, pub = req(anon, '/api/public/clubs/1/settings-hub')
ck('P2-08a', '公开脱敏视图下发 9 个模块开关与通知策略',
   st_pub == 200 and len(pub.get('modules', {})) == 9 and 'notify' in pub
   and pub['modules'].get('storedValue') is False,
   {'modules': len(pub.get('modules', {})), 'notify': pub.get('notify')})

with sync_playwright() as w:
    b = w.chromium.launch(channel='chrome', headless=True)
    ctx = b.new_context(viewport={'width': 390, 'height': 844})
    pg = ctx.new_page()
    calls, errs = [], []
    pg.on('request', lambda r: calls.append(r.url) if 'settings-hub' in r.url else None)
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto(B + '/web?club_id=1', wait_until='domcontentloaded')
    pg.wait_for_timeout(2400)
    dom = pg.evaluate("""()=>({policy:window.ClubOSHubPolicy,
        note:(document.getElementById('hubNotifyNote')||{}),
        noteHidden:(document.getElementById('hubNotifyNote')||{}).hidden,
        noteText:(document.getElementById('hubNotifyNote')||{}).textContent||''})""")
    ck('P2-08b', 'C 端确实请求了设置中枢脱敏视图', len(calls) >= 1, calls[:3])
    ck('P2-08c', '会员「我的」页显示外发关闭提示',
       dom['policy'] is not None and dom['noteHidden'] is False and '关闭外发' in dom['noteText'],
       {'hidden': dom['noteHidden'], 'text': dom['noteText'][:100]})
    gated = pg.evaluate("""async()=>{
        const el=document.createElement('button');el.dataset.hubModule='storedValue';el.textContent='储值入口';
        document.body.appendChild(el);await window.applyHubPolicy();
        return {display:el.style.display,off:el.dataset.hubOff,title:el.title};}""")
    ck('P2-08d', '关闭模块后 data-hub-module 入口被隐藏并标记',
       gated['display'] == 'none' and gated['off'] == '1' and '暂未开放' in gated['title'], gated)
    ck('P2-08e', 'C 端无 JS 运行时错误', not errs, errs[:3])
    ctx.close()

    # ── 管理端 UI/行为 ───────────────────────────────────────────────────
    ctx2 = b.new_context(viewport={'width': 1280, 'height': 1000})
    # 同账号二次登录会吊销 API opener 的会话，这里直接复用已登录 Cookie。
    ctx2.add_cookies([{'name': c.name, 'value': c.value, 'domain': '127.0.0.1', 'path': c.path or '/'}
                      for c in club_cj])
    pg2 = ctx2.new_page()
    native_dlg = []
    pg2.on('dialog', lambda d: (native_dlg.append(d.message), d.dismiss()))
    def hub():
        pg2.goto(B + '/static/club/settings-hub.html?club_id=1', wait_until='load')
        pg2.wait_for_timeout(1200)
    hub()

    # BUG-HUB-011 深链参数
    pages = pg2.evaluate("""()=>[...document.querySelectorAll('#modules button[data-page]')].map(b=>b.dataset.page)""")
    spa = [p for p in pages if '/static/club/index.html' in p]
    ck('P2-11', '6 个 SPA 入口都带 view= 定位参数',
       len(spa) == 6 and all('view=' in p for p in spa), spa)
    # SPA 实际激活目标视图
    pg2.goto(B + '/static/club/index.html?club_id=1&view=members-list', wait_until='load')
    pg2.wait_for_timeout(1600)
    act = pg2.evaluate("""()=>({active:(document.querySelector('.view.active')||{}).id||'',
        nav:(document.querySelector('.nav button.active')||{}).dataset?.view||''})""")
    ck('P2-11b', '深链 ?view= 首屏真正激活目标视图', act['active'] == 'members-list', act)
    pg2.goto(B + '/static/club/index.html?club_id=1&view=settings&tab=cprofile', wait_until='load')
    pg2.wait_for_timeout(1800)
    tab = pg2.evaluate("""()=>({active:(document.querySelector('.view.active')||{}).id||'',
        tabOn:(document.querySelector('#setTabs .set-tab.active')||{}).getAttribute?.('onclick')||'',
        pane:(document.getElementById('pane-cprofile')||{}).style?.display})""")
    ck('P2-11c', '深链 ?tab= 首屏真正激活设置子页',
       tab['active'] == 'settings' and 'cprofile' in tab['tabOn'], tab)
    hub()

    # BUG-HUB-012 审计中文标签
    atext = pg2.evaluate("()=>document.querySelector('#audit').textContent")
    ck('P2-12', '审计表显示中文标签而非原始 key',
       'notify.quietHoursStart' not in atext and ('免打扰' in atext or '总开关' in atext), atext[:200])

    # BUG-HUB-013 重置用统一弹层
    pg2.locator('#resetAll').click()
    pg2.wait_for_timeout(600)
    dlg = pg2.evaluate("""()=>{const d=[...document.querySelectorAll('.ux-dialog')].filter(x=>x.offsetParent!==null);
        return d.length?d[0].textContent.slice(0,220):null}""")
    ck('P2-13', '重置改用全站统一弹层且说明作用域 / 回落目标',
       dlg and '重置当前层级' in dlg and '回落' in dlg and not native_dlg, {'dlg': dlg, 'native': native_dlg})
    pg2.locator('.ux-dialog [data-cancel]').first.click()
    pg2.wait_for_timeout(300)

    # BUG-HUB-014 保存成功提示不被覆写
    pg2.evaluate("""()=>{const e=document.querySelector('#prefs input[type=number]');
        e.value='21';e.dataset.dirty='1';e.dispatchEvent(new Event('change',{bubbles:true}));}""")
    pg2.locator('#savePrefs').click()
    pg2.wait_for_timeout(1500)
    hint = pg2.evaluate("()=>document.querySelector('#saveHint').textContent")
    ck('P2-14', '保存后成功提示在 loadAll 重绘后仍保留', '已保存' in hint, hint)

    # BUG-HUB-015 清空数字输入不得落库 0
    before = req(club, CB)[1]['preferences']
    q0 = next(x['value'] for x in before if x['key'] == 'notify.quietHoursStart')
    pg2.evaluate("""()=>{const e=document.querySelector('#prefs input[type=number]');
        e.value='';e.dataset.dirty='1';e.dispatchEvent(new Event('change',{bubbles:true}));}""")
    pg2.locator('#savePrefs').click()
    pg2.wait_for_timeout(900)
    hint2 = pg2.evaluate("()=>document.querySelector('#saveHint').textContent")
    q1 = next(x['value'] for x in req(club, CB)[1]['preferences'] if x['key'] == 'notify.quietHoursStart')
    ck('P2-15', '清空数字输入被拒绝保存，服务端值不被洗成 0',
       '请修正' in hint2 and q1 == q0 and q1 != 0, {'hint': hint2, 'before': q0, 'after': q1})

    # BUG-HUB-016 返回入口 + 同标签跳转
    back = pg2.evaluate("""()=>({href:document.getElementById('backToAdmin')?.href||'',
        text:document.getElementById('backToAdmin')?.textContent||''})""")
    ck('P2-16', '独立页有返回后台面包屑且沿用 query',
       '返回俱乐部后台' in back['text'] and '/club?' in back['href'] and 'club_id=1' in back['href'], back)
    popup = []
    target = pg2.evaluate("""()=>{const b=[...document.querySelectorAll('#modules button[data-page]')]
        .find(x=>x.dataset.page.includes('page-decorator'));return b?b.dataset.page:''}""")
    pg2.on('popup', lambda p: popup.append(p.url))
    if target:
        pg2.locator('#modules button[data-page*="page-decorator"]').first.click()
        pg2.wait_for_timeout(900)
    ck('P2-16b', '「前往配置」默认同标签跳转且不弹新窗口',
       bool(target) and not popup and 'page-decorator' in pg2.url, {'url': pg2.url, 'popups': popup})

    ctx2.close(); b.close()

# 清场
req(club, CB + '/reset', 'POST', {}); req(plat, PB + '/reset', 'POST', {})
s = {'pass': sum(1 for x in R if x['ok']), 'fail': sum(1 for x in R if not x['ok']), 'items': R}
json.dump(s, open(os.path.join(OUT, 'results_p2_verify.json'), 'w'), ensure_ascii=False, indent=2)
print('\n=== P2 VERIFY %d PASS / %d FAIL ===' % (s['pass'], s['fail']), flush=True)
sys.exit(0 if s['fail'] == 0 else 1)
