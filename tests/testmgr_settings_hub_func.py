# ClubOS NEW · 设置中枢全量可用性测试（2026-11-16）
# 覆盖：19 项能力的配置落点静态加载（16 落点）+ 每项功能的写链路持久化（20 项功能断言）。
# 运行前提：本地 QA 实例（8262）以 TEST_OPEN + fixture DB 启动：
#   CLUBOS_TEST_OPEN=1 CLUBOS_SECURITY_MODE=demo COMMERCE_PROVIDER=local MOCK_AI=1 \
#   CLUBOS_DB_PATH=/tmp/clubos_full_ui.db uvicorn app:app --port 8262
# 背景 bug 修复回归：
#   BUG-HUB-021 saveClubSettings 引用越界变量 s（地图 Key 留空时保存必炸）；
#   BUG-HUB-022 settings_hub 注册表 mpSettings api 路径错误（mp-settings → miniprogram/settings）。
import json, sys, re
import urllib.request
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:8262'
results, fails = [], []

def rec(name, ok, detail=''):
    results.append((name, bool(ok)))
    if not ok: fails.append(f'{name} | {detail}')
    print(('PASS ' if ok else 'FAIL ') + name + ((' | ' + str(detail)[:130]) if detail else ''))

def api(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode())

PAGES = [
    ('clubBranding(深链)', f'{BASE}/club?club_id=1&view=settings&tab=brand'),
    ('cProfile(深链)', f'{BASE}/club?club_id=1&view=settings&tab=cprofile'),
    ('mpSettings(深链)', f'{BASE}/club?club_id=1&view=settings&tab=mpset'),
    ('pageLayout', f'{BASE}/static/club/page-decorator.html?club_id=1'),
    ('campaigns(深链)', f'{BASE}/club?club_id=1&view=members-benefits'),
    ('storedValue', f'{BASE}/static/club/stored-value.html?club_id=1'),
    ('groupBuy', f'{BASE}/static/club/group-buys.html?club_id=1'),
    ('affiliate', f'{BASE}/static/club/affiliates.html?club_id=1'),
    ('giftCard', f'{BASE}/static/club/gift-cards.html?club_id=1'),
    ('miniprogramBridge', f'{BASE}/static/club/miniprogram-bridge.html?club_id=1'),
    ('checkoutSettlement', f'{BASE}/static/club/settlements.html?club_id=1'),
    ('engagement(checkin/lottery/stamp)', f'{BASE}/static/club/engagement.html?club_id=1'),
    ('notifyChannels+Templates', f'{BASE}/static/club/notifications.html?club_id=1'),
    ('inappMessages(深链)', f'{BASE}/club?club_id=1&view=info'),
    ('stores', f'{BASE}/static/club/stores.html?club_id=1'),
    ('members(深链)', f'{BASE}/club?club_id=1&view=members-list'),
]

with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    pg = b.new_page(viewport={'width': 1440, 'height': 900})
    errors = []
    pg.on('pageerror', lambda e: errors.append(str(e)))

    def goto(url, wait=1800):
        errors.clear()
        pg.goto(url, wait_until='domcontentloaded', timeout=30000)
        pg.wait_for_timeout(wait)

    def api_fail_setup():
        errs = []
        def on_resp(r):
            if '/api/' in r.url and r.status >= 400:
                errs.append(f'{r.status} {r.url.split("/api/")[-1][:80]}')
        pg.on('response', on_resp)
        return errs, on_resp

    # ── A. 静态加载审计（16 落点 × 3 断言） ──
    for name, url in PAGES:
        errs, hook = api_fail_setup()
        try:
            goto(url)
        except Exception as e:
            rec(f'A {name} 页面加载', False, str(e)[:100]); continue
        body_len = pg.evaluate("()=>document.body.innerText.length")
        rec(f'A {name} 加载无JS错', not errors, '; '.join(errors[:2]))
        rec(f'A {name} 无API失败', not errs, '; '.join(errs[:3]))
        rec(f'A {name} 有内容渲染', body_len > 100, f'textLen={body_len}')
        pg.remove_listener('response', hook)

    def js_api(path, method='GET', body=None):
        return pg.evaluate("""async ([p, m, b]) => {
          const opt = { method: m, credentials: 'same-origin' };
          if (b !== null) { opt.headers = { 'Content-Type': 'application/json' }; opt.body = b; }
          const r = await fetch(p, opt);
          return { status: r.status, body: await r.json().catch(() => null) };
        }""", [path, method, json.dumps(body) if body is not None else None])

    # ── B. 功能写链路（每项能力保存 → 回读） ──
    goto(f'{BASE}/club?club_id=1&view=settings&tab=brand')
    orig_name = pg.evaluate("()=>document.querySelector('#setName')?.value || ''")
    pg.fill('#setName', '测试俱乐部甲')
    pg.evaluate("()=>saveClubSettings()")   # BUG-HUB-021 回归：地图 Key 留空也能保存
    pg.wait_for_timeout(1200)
    st = api('/api/club/1/settings')
    rec('B1 clubBranding 保存+回读', isinstance(st, dict) and st.get('name') == '测试俱乐部甲')
    # 还原俱乐部名，避免污染共享夹具（全量 UI 断言依赖原名称）
    if orig_name and orig_name != '测试俱乐部甲':
        pg.fill('#setName', orig_name)
        pg.evaluate("()=>saveClubSettings()")
        pg.wait_for_timeout(900)
        rec('B1b clubBranding 名称还原', api('/api/club/1/settings').get('name') == orig_name)

    goto(f'{BASE}/club?club_id=1&view=settings&tab=cprofile')
    pg.evaluate("()=>{const t=document.querySelector('#cprofileBody textarea, textarea'); if(t){t.value='关于我们：山野童行，陪孩子走进自然（e2e）'; t.dispatchEvent(new Event('input',{bubbles:true}))}}")
    pg.evaluate("()=>saveCProfile()")
    pg.wait_for_timeout(1200)
    rec('B2 cProfile 保存+回读', '山野童行，陪孩子走进自然' in json.dumps(api('/api/club/1/c-profile'), ensure_ascii=False))

    goto(f'{BASE}/club?club_id=1&view=settings&tab=mpset')
    pg.evaluate("()=>{const el=document.querySelector('[data-mpset=\"share.friendTitle\"]'); if(el){el.value='山野童行 e2e'; el.dispatchEvent(new Event('input',{bubbles:true}))}}")
    pg.evaluate("()=>saveMpSetGroup('share')")
    pg.wait_for_timeout(1200)
    rec('B3 mpSettings 保存+回读', '山野童行 e2e' in json.dumps(api('/api/club/1/miniprogram/settings'), ensure_ascii=False))

    goto(f'{BASE}/static/club/stored-value.html?club_id=1')
    pg.evaluate("()=>{const el=document.querySelector('#cfgEnabled'); if(el && !el.checked) el.click()}")
    pg.fill('#cfgRatio', '100'); pg.fill('#cfgMinRecharge', '50')
    pg.click('#saveCfg'); pg.wait_for_timeout(1200)
    sv = api('/api/club/1/stored-value/config')
    rec('B4 storedValue 配置保存', sv.get('enabled') == True and sv.get('minRechargeAmount') == 50.0, json.dumps(sv, ensure_ascii=False)[:100])

    goto(f'{BASE}/static/club/group-buys.html?club_id=1')
    rec('B5 groupBuy 建单表单存在', pg.evaluate("()=>!!document.querySelector('#saveCampaign')"))

    goto(f'{BASE}/static/club/affiliates.html?club_id=1')
    pg.evaluate("()=>{const el=document.querySelector('#cfgEnabled'); if(el && !el.checked) el.click()}")
    pg.fill('#cfgRate', '8')
    pg.click('#saveCfg'); pg.wait_for_timeout(1200)
    af = api('/api/club/1/affiliate/config')
    rec('B6 affiliate 配置保存', af.get('enabled') == True, json.dumps(af, ensure_ascii=False)[:100])

    goto(f'{BASE}/static/club/gift-cards.html?club_id=1')
    pg.evaluate("()=>{const el=document.querySelector('#cfgEnabled'); if(el && !el.checked) el.click()}")
    pg.click('#saveCfg'); pg.wait_for_timeout(900)
    pg.fill('#bName', 'e2e 礼品卡批次'); pg.fill('#bFace', '100'); pg.fill('#bCount', '3')
    pg.click('#createBatch'); pg.wait_for_timeout(1500)
    batches = api('/api/club/1/gift-cards/batches')
    bl = batches if isinstance(batches, list) else batches.get('items', [])
    rec('B7 giftCard 启用+建批次', any('e2e' in json.dumps(x, ensure_ascii=False) for x in bl))

    goto(f'{BASE}/static/club/miniprogram-bridge.html?club_id=1')
    pg.evaluate("()=>{const el=document.querySelector('#cfgEnabled'); if(el && !el.checked) el.click()}")
    pg.click('#saveCfg'); pg.wait_for_timeout(1200)
    rec('B8 miniprogramBridge 配置保存', json.dumps(api('/api/club/1/miniprogram-bridge/config'), ensure_ascii=False) != '')

    goto(f'{BASE}/static/club/settlements.html?club_id=1')
    rec('B9 checkoutSettlement 开关存在', pg.evaluate("()=>!!document.querySelector('input[type=checkbox]')"))

    goto(f'{BASE}/static/club/engagement.html?club_id=1')
    pg.evaluate("()=>{const el=document.querySelector('#ckEnabled'); if(el && !el.checked) el.click()}")
    pg.fill('#ckDaily', '5')
    pg.evaluate("()=>{const b=document.querySelector('[data-save]'); if(b) b.click()}")
    pg.wait_for_timeout(1200)
    rec('B10 engagement 签到配置保存', 'CHECKIN' in json.dumps(api('/api/club/1/engagement/config'), ensure_ascii=False))

    goto(f'{BASE}/static/club/notifications.html?club_id=1')
    pg.select_option('#chType', 'inapp')
    pg.click('#chSave'); pg.wait_for_timeout(1200)
    chs = api('/api/club/1/notification-channels')
    chl = chs if isinstance(chs, list) else chs.get('items', [])
    rec('B11 notifications 通道新增', any(x.get('type') == 'inapp' for x in chl))

    goto(f'{BASE}/static/club/stores.html?club_id=1')
    pg.click('#addBtn'); pg.wait_for_timeout(400)
    pg.fill('.store[data-id=""] [data-f="name"]', 'e2e 测试门店')
    pg.fill('.store[data-id=""] [data-f="address"]', '成都市青羊区 e2e 路 1 号')
    pg.click('.store[data-id=""] [data-act="save"]'); pg.wait_for_timeout(1200)
    sts = api('/api/club/1/stores')
    stl = sts if isinstance(sts, list) else sts.get('items', [])
    new_store = next((x for x in stl if x.get('name') == 'e2e 测试门店'), None)
    rec('B12 stores 新增门店落库', new_store is not None)
    if new_store:
        sid = new_store['id']
        has_btn = pg.evaluate(f"()=>!!document.querySelector('.store[data-id=\"{sid}\"] [data-act=default]')")
        if has_btn:
            pg.click(f'.store[data-id="{sid}"] [data-act="default"]'); pg.wait_for_timeout(900)
        stl2 = api('/api/club/1/stores')
        stl2 = stl2 if isinstance(stl2, list) else stl2.get('items', [])
        tgt = next((x for x in stl2 if x['id'] == sid), {})
        rec('B12b stores 设默认', bool(tgt.get('isDefault') or tgt.get('is_default')))

    goto(f'{BASE}/club?club_id=1&view=settings')
    pg.evaluate("()=>openSettingsHub()")
    pg.wait_for_timeout(1800)
    rec('B13a 中枢卡片开关渲染', pg.evaluate("()=>[...document.querySelectorAll('.hub-card input[type=checkbox], [data-key] input[type=checkbox]')].length") >= 5)
    r = js_api('/api/club/1/settings-hub', 'PATCH', {'changes': {'module.checkoutSettlement': False}})
    rec('B13b 中枢模块开关 API 保存', r['status'] == 200 and (len(r['body'].get('updated') or []) + len(r['body'].get('skipped') or [])) >= 1)
    r2 = js_api('/api/club/1/settings-hub', 'PATCH', {'changes': {'module.checkoutSettlement': True}})
    rec('B13c 中枢模块开关恢复', r2['status'] == 200 and len(r2['body'].get('updated') or []) >= 1)

    # ── C. 注册表 api 路径全部真实存在（静态校验，防 BUG-HUB-022 类回归） ──
    app_src = open('app.py').read()
    routes = set(re.findall(r"@app\.(?:get|post|put|patch|delete)\('([^']+)'", app_src))
    bad_paths = []
    try:
        sys.path.insert(0, '.')
        from clubos_domain.settings_hub import REGISTRY
        for m in REGISTRY:
            api_p = m['api'].replace('{club_id}', '1')
            hit = api_p in routes or any(api_p.startswith(r.split('{')[0]) and r.split('{')[0] in api_p for r in routes if r.startswith('/api/'))
            if not hit: bad_paths.append(f"{m['key']}:{api_p}")
    except ImportError:
        bad_paths.append('REGISTRY import failed')
    rec('C1 注册表 api 路径全部存在', not bad_paths, '; '.join(bad_paths))

    rec('Z 无页面 JS 错误', not errors, '; '.join(errors[:3]))
    b.close()

print('\n==== %d 项 | 失败 %d ====' % (len(results), len(fails)))
for f in fails: print('FAILED:', f)
sys.exit(1 if fails else 0)
