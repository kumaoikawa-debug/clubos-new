# -*- coding: utf-8 -*-
"""设置中枢 UI/UX 优化 · 真 Chrome 专项验收（隔离实例 CLUBOS_GATE_BASE，默认 8124）。"""
import json, os, sys
from playwright.sync_api import sync_playwright

# 发布前门禁注入：实例地址 / 输出目录 / demo 口令均可覆盖，禁止硬编码临时路径。
B = os.getenv('CLUBOS_GATE_BASE', 'http://127.0.0.1:8124').rstrip('/')
OUT = os.getenv('CLUBOS_GATE_OUT', os.path.join('/tmp', 'clubos_hub_gate'))
PW = os.getenv('STAGING_PASSWORD', 'Staging-2026-ClubOS-demo!')
os.makedirs(OUT, exist_ok=True)
R = []

def ck(cid, title, cond, evi=''):
    R.append({'id': cid, 'title': title, 'ok': bool(cond), 'evidence': str(evi)[:400]})
    print(('PASS ' if cond else 'FAIL ') + cid + ' ' + title + ' | ' + str(evi)[:230], flush=True)

def login(pg, user):
    pg.goto(B + '/login', wait_until='domcontentloaded')
    return pg.evaluate("""async u=>{const r=await fetch('/api/auth/login',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u[0],password:u[1]})});return r.status}""", [user, PW])

def hub(pg, extra='club_id=1'):
    pg.goto(B + '/static/club/settings-hub.html?' + extra, wait_until='load')
    pg.wait_for_timeout(1200)

with sync_playwright() as w:
    b = w.chromium.launch(channel='chrome', headless=True)

    # ── 桌面端：俱乐部管理员 ─────────────────────────────────────────────
    ctx = b.new_context(viewport={'width': 1280, 'height': 1000})
    ctx.new_cdp_session(ctx.new_page()).send('Network.setCacheDisabled', {'cacheDisabled': True})
    pg = ctx.new_page()
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    ck('UI-00', '管理员登录成功', login(pg, 'club1_admin') == 200)
    hub(pg)

    sticky = pg.evaluate("""()=>{const h=document.querySelector('#hubbar');
      const s=getComputedStyle(h); return {pos:s.position,top:s.top,z:s.zIndex,
        save:!!h.querySelector('#savePrefs'),reset:!!h.querySelector('#resetAll'),hint:!!h.querySelector('#saveHint')};}""")
    ck('UI-01', '吸顶操作条含保存/重置/提示且 position:sticky',
       sticky['pos'] == 'sticky' and sticky['save'] and sticky['reset'] and sticky['hint'], sticky)

    pg.evaluate("()=>window.scrollTo(0,document.body.scrollHeight)")
    pg.wait_for_timeout(400)
    top = pg.evaluate("""()=>{const h=document.querySelector('#hubbar').getBoundingClientRect();
      const b=document.querySelector('#savePrefs').getBoundingClientRect();
      return {barTop:Math.round(h.top),saveTop:Math.round(b.top),saveW:Math.round(b.width),
        vh:innerHeight,scrollY:Math.round(scrollY)};}""")
    fab = pg.evaluate("""()=>{const f=document.querySelector('body>.logout-fab');
      return f?{disp:getComputedStyle(f).display,vis:f.getBoundingClientRect().width}:null;}""")
    ck('UI-23', '独立页不显示遮挡内容的全局退出登录浮标',
       fab and fab['disp'] == 'none' and fab['vis'] == 0, fab)

    ck('UI-02', '滚到页面底部后保存按钮仍在视口内',
       top['scrollY'] > 200 and 0 <= top['barTop'] <= 2 and 0 <= top['saveTop'] < top['vh'] and top['saveW'] > 40, top)
    pg.evaluate("()=>window.scrollTo(0,0)")
    pg.wait_for_timeout(250)

    sw = pg.evaluate("""()=>{const a=document.querySelector('#prefs input.hub-switch');
      const m=document.querySelector('#modules input.mod-ctl.hub-switch');
      const box=x=>x?{w:Math.round(x.getBoundingClientRect().width),h:Math.round(x.getBoundingClientRect().height),
        appearance:getComputedStyle(x).appearance}:null;
      return {pref:box(a),mod:box(m),count:document.querySelectorAll('#modules input.mod-ctl.hub-switch').length};}""")
    ck('UI-03', '布尔偏好与模块总开关统一为同一开关样式',
       sw['pref'] and sw['mod'] and sw['pref']['w'] == sw['mod']['w'] == 38 and sw['count'] == 9, sw)

    # 未保存改动常驻提示
    pg.locator('#prefs input.hub-switch').first.click()
    pg.wait_for_timeout(200)
    dirty = pg.evaluate("""()=>({chip:document.querySelector('#dirtyChip').textContent.trim(),
      hidden:document.querySelector('#dirtyChip').hidden,body:document.body.classList.contains('has-dirty')})""")
    ck('UI-04', '有未保存改动时吸顶条显示数量与状态',
       dirty['chip'] == '1 项未保存' and not dirty['hidden'] and dirty['body'], dirty)
    pg.locator('#savePrefs').click()
    pg.wait_for_timeout(1300)
    clean = pg.evaluate("""()=>({hidden:document.querySelector('#dirtyChip').hidden,
      body:document.body.classList.contains('has-dirty'),hint:document.querySelector('#saveHint').textContent})""")
    ck('UI-05', '保存后未保存提示自动消失', clean['hidden'] and not clean['body'] and '已保存' in clean['hint'], clean)

    # 统计卡点击筛选
    total = pg.evaluate("()=>document.querySelectorAll('#modules .mod').length")
    pg.locator('#stats .stat[data-filter="todo"]').click()
    pg.wait_for_timeout(350)
    todo = pg.evaluate("""()=>({vis:[...document.querySelectorAll('#modules .mod')].filter(x=>x.style.display!=='none').length,
      chip:document.querySelector('#modFilters .chip.active').dataset.filter,
      note:document.querySelector('#filterNote').textContent.trim()})""")
    ck('UI-06', '点「需处理」统计卡筛出非生效卡片并同步 chip 状态',
       todo['chip'] == 'todo' and 0 < todo['vis'] < total and '筛选出' in todo['note'], {**todo, 'total': total})

    pg.locator('#modFilters .chip[data-filter="all"]').click()
    pg.fill('#modSearch', '装修')
    pg.wait_for_timeout(300)
    search = pg.evaluate("""()=>({vis:[...document.querySelectorAll('#modules .mod')].filter(x=>x.style.display!=='none').length,
      note:document.querySelector('#filterNote').textContent.trim(),
      labels:[...document.querySelectorAll('#modules .mod')].filter(x=>x.style.display!=='none').map(x=>x.querySelector('.mod__t').textContent.trim())})""")
    ck('UI-07', '关键词搜索收敛卡片并给出结果数',
       0 < search['vis'] < total and '筛选出' in search['note'] and '装修' in search['note'], search)
    pg.locator('#clearFilter').click()
    pg.wait_for_timeout(250)
    cleared = pg.evaluate("""()=>({vis:[...document.querySelectorAll('#modules .mod')].filter(x=>x.style.display!=='none').length,
      q:document.querySelector('#modSearch').value,chip:document.querySelector('#modFilters .chip.active').dataset.filter})""")
    ck('UI-08', '清除筛选恢复全部 19 张卡', cleared['vis'] == 19 and cleared['q'] == '' and cleared['chip'] == 'all', cleared)

    # 分组折叠
    grp = pg.evaluate("()=>document.querySelectorAll('#modules .grp').length")
    pg.locator('#toggleGroups').click()
    pg.wait_for_timeout(250)
    collapsed = pg.evaluate("""()=>({c:document.querySelectorAll('#modules .grp.collapsed').length,
      hidden:document.querySelector('#modules .mods')?getComputedStyle(document.querySelector('#modules .mods')).display:'',
      btn:document.querySelector('#toggleGroups').textContent.trim(),
      domCards:document.querySelectorAll('#modules .mod').length})""")
    ck('UI-09', '一键折叠全部分组且卡片仍留在 DOM',
       collapsed['c'] == grp == 5 and collapsed['hidden'] == 'none' and collapsed['btn'] == '展开全部分组'
       and collapsed['domCards'] == 19, collapsed)
    pg.locator('#modules .grp__t[data-group]').first.click()
    pg.wait_for_timeout(200)
    one = pg.evaluate("()=>({c:document.querySelectorAll('#modules .grp.collapsed').length})")
    ck('UI-10', '单个分组可独立展开', one['c'] == grp - 1, one)
    pg.locator('#toggleGroups').click()
    pg.wait_for_timeout(200)
    reopened = pg.evaluate("()=>document.querySelectorAll('#modules .grp.collapsed').length")
    ck('UI-11', '可一键重新展开全部分组', reopened == 0, reopened)

    # 统计「本层级覆盖」跳到偏好并高亮
    pg.locator('#stats .stat[data-jump="prefs"]').click()
    pg.wait_for_timeout(900)
    jump = pg.evaluate("""()=>({y:Math.round(scrollY),
      cardTop:Math.round(document.querySelector('#prefsCard').getBoundingClientRect().top),
      flash:document.querySelectorAll('#prefs .prow.flash').length})""")
    ck('UI-12', '点「本层级覆盖」定位到偏好区', jump['y'] > 0 and abs(jump['cardTop']) < 160, jump)

    # 徽章语义配色
    badges = pg.evaluate("""()=>({todo:document.querySelectorAll('#modules .badge.todo').length,
      off:document.querySelectorAll('#modules .badge.off').length,
      on:document.querySelectorAll('#modules .badge.on').length,
      todoColor:getComputedStyle(document.querySelector('#modules .badge.todo')||document.body).color})""")
    ck('UI-13', '「未配置」使用中性待办色而非故障红',
       badges['todo'] + badges['off'] + badges['on'] == 19 and badges['todoColor'] != 'rgb(176, 58, 46)', badges)

    # 审计表吸顶表头 + 空态
    audit = pg.evaluate("""()=>{const th=document.querySelector('#audit thead th');
      return {sticky:th?getComputedStyle(th).position:'',cols:document.querySelectorAll('#audit th').length,
        rows:document.querySelectorAll('#audit tbody tr').length};}""")
    ck('UI-14', '审计表 7 列且表头在滚动容器内吸顶', audit['sticky'] == 'sticky' and audit['cols'] == 7, audit)

    pg.evaluate("()=>window.scrollTo(0,0)")
    pg.wait_for_timeout(1800)  # 等偏好高亮自动褪去，截图用于视觉走查
    pg.screenshot(path=os.path.join(OUT, 'uiux_01_桌面全页.png'), full_page=True)
    pg.locator('#stats .stat[data-filter="todo"]').click()
    pg.wait_for_timeout(300)
    pg.screenshot(path=os.path.join(OUT, 'uiux_02_筛选态.png'), full_page=True)

    # 状态未知：注入 probeError 验证诊断详情
    def inject_probe(route):
        resp = route.fetch()
        data = json.loads(resp.text())
        if data.get('modules'):
            data['modules'][0]['configured'] = None
            data['modules'][0]['probeError'] = 'no such table: marketing_campaigns（诊断注入）'
            data['summary']['unknownModules'] = data['summary'].get('unknownModules', 0) + 1
        route.fulfill(response=resp, json=data)
    pg.route('**/api/club/1/settings-hub', inject_probe)
    hub(pg)
    probe = pg.evaluate("""()=>({unknown:document.querySelectorAll('#modules .badge.unknown').length,
      title:(document.querySelector('#modules .badge.unknown')||{}).title||'',
      details:document.querySelectorAll('#modules details.mod__err').length,
      code:(document.querySelector('#modules .mod__err code')||{}).textContent||''})""")
    ck('UI-15', '状态未知可展开查看后端探测错误',
       probe['unknown'] >= 1 and '诊断注入' in probe['title'] and probe['details'] >= 1 and '诊断注入' in probe['code'], probe)
    pg.screenshot(path=os.path.join(OUT, 'uiux_03_状态未知诊断.png'), full_page=True)
    pg.unroute('**/api/club/1/settings-hub')
    ck('UI-16', '桌面端无 JS 运行时错误', not errs, errs)
    ctx.close()

    # ── 平台层级强调 ─────────────────────────────────────────────────────
    ctx2 = b.new_context(viewport={'width': 1280, 'height': 1000})
    pg2 = ctx2.new_page()
    errs2 = []
    pg2.on('pageerror', lambda e: errs2.append(str(e)))
    ck('UI-17', '总平台账号登录成功', login(pg2, 'platform_admin') == 200)
    hub(pg2)
    pg2.select_option('#scope', 'platform')
    pg2.wait_for_timeout(1300)
    plat = pg2.evaluate("""()=>({body:document.body.classList.contains('is-platform'),
      impact:!document.querySelector('#scopeImpact').hidden,
      chip:document.querySelector('#hubbarScope').textContent.trim(),
      reset:document.querySelector('#resetAll').textContent.trim(),
      hint:document.querySelector('#scopeHint').textContent.trim()})""")
    ck('UI-18', '平台层级有整页影响范围强调且首屏读取成功',
       plat['body'] and plat['impact'] and '平台默认' in plat['chip'] and '平台' in plat['reset']
       and '读取失败' not in plat['hint'], plat)
    pg2.screenshot(path=os.path.join(OUT, 'uiux_04_平台层级强调.png'), full_page=True)
    ck('UI-19', '平台端无 JS 运行时错误', not errs2, errs2)
    ctx2.close()

    # ── 手机端 ───────────────────────────────────────────────────────────
    ctx3 = b.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True,
                         device_scale_factor=2)
    pg3 = ctx3.new_page()
    errs3 = []
    pg3.on('pageerror', lambda e: errs3.append(str(e)))
    login(pg3, 'club1_admin')
    hub(pg3)
    mob = pg3.evaluate("""()=>{const bar=document.querySelector('#hubbar').getBoundingClientRect();
      const save=document.querySelector('#savePrefs').getBoundingClientRect();
      const mods=getComputedStyle(document.querySelector('#modules .mods')).gridTemplateColumns;
      return {barW:Math.round(bar.width),saveW:Math.round(save.width),saveBottom:Math.round(save.bottom),
        mods:mods,scrollW:document.documentElement.scrollWidth,vw:innerWidth,
        cards:document.querySelectorAll('#modules .mod').length,
        overflow:document.documentElement.scrollWidth>innerWidth+1};}""")
    ck('UI-20', '手机端无横向溢出、卡片单列且保存按钮可点',
       not mob['overflow'] and mob['saveW'] > 60 and mob['saveBottom'] < 844 and mob['cards'] == 19
       and ' ' not in mob['mods'].strip(), mob)
    pg3.evaluate("()=>window.scrollTo(0,document.body.scrollHeight)")
    pg3.wait_for_timeout(400)
    mob2 = pg3.evaluate("""()=>({barTop:Math.round(document.querySelector('#hubbar').getBoundingClientRect().top),
      saveTop:Math.round(document.querySelector('#savePrefs').getBoundingClientRect().top),
      scrollY:Math.round(scrollY)})""")
    ck('UI-21', '手机端长滚动后吸顶条仍固定顶部、保存按钮可见',
       mob2['scrollY'] > 200 and 0 <= mob2['barTop'] <= 2 and 0 <= mob2['saveTop'] < 844, mob2)
    pg3.evaluate("()=>window.scrollTo(0,0)")
    pg3.screenshot(path=os.path.join(OUT, 'uiux_05_手机端全页.png'), full_page=True)
    ck('UI-22', '手机端无 JS 运行时错误', not errs3, errs3)
    ctx3.close()
    b.close()

s = {'pass': sum(1 for x in R if x['ok']), 'fail': sum(1 for x in R if not x['ok']), 'items': R}
json.dump(s, open(os.path.join(OUT, 'results_uiux_verify.json'), 'w'), ensure_ascii=False, indent=2)
print('\n=== UI/UX VERIFY %d PASS / %d FAIL ===' % (s['pass'], s['fail']), flush=True)
sys.exit(0 if s['fail'] == 0 else 1)
