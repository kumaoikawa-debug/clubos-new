# -*- coding: utf-8 -*-
"""业务介绍入口并入「经营业态开关」专项验证（本地隔离实例，不改真库）。

验证目标：只搬入口，不搬功能。
- 顶栏不再出现独立的「业务介绍」按钮；
- 「经营业态开关」页内出现「业态开关 / 业务介绍」两个子页；
- 原业务介绍的 DOM id、开关、文案、条目、保存接口全部照常工作；
- 旧深链 ?view=settings&tab=biz 自动落到经营业态开关并展开业务介绍。
"""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = os.getenv('CLUBOS_GATE_BASE', 'http://127.0.0.1:8266').rstrip('/')
PW = os.getenv('STAGING_PASSWORD', 'Staging-2026-ClubOS-demo!')
OUT = os.getenv('CLUBOS_GATE_OUT', '/tmp/biz_entry_merge')
os.makedirs(OUT, exist_ok=True)

R = []
def ck(name, ok, evi=''):
    R.append((name, bool(ok), str(evi)[:200]))
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'   [{evi}]' if not ok and evi else ''), flush=True)

html = (ROOT / 'static/club/index.html').read_text(encoding='utf-8')
js = (ROOT / 'static/club/club.js').read_text(encoding='utf-8')
ck('顶栏移除独立业务介绍按钮', "setTab('biz')" not in html)
ck('原 pane-biz 已并入', 'id="pane-biz"' not in html)
ck('子 tab 容器存在', 'id="modswSubTabs"' in html and 'data-modsub="biz"' in html)
for ident in ('bizStateTag', 'bizForm', 'bizItems', 'modswForm'):
    ck('保留 DOM id ' + ident, f'id="{ident}"' in html)
ck('子 tab 切换函数存在', 'window.modSwSubTab' in js and 'MODSW_SUB_LAST' in js)
ck('旧深链 biz 兼容', 'legacyBiz' in js and "name==='biz'" in js)

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    ctx = browser.new_context(viewport={'width': 1440, 'height': 1000})
    pg = ctx.new_page()
    errors = []
    pg.on('pageerror', lambda e: errors.append(str(e)))

    pg.goto(BASE + '/login', wait_until='domcontentloaded')
    status = pg.evaluate("""async pw => {
      const r = await fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({username:'club1_admin',password:pw})}); return r.status;
    }""", PW)
    ck('俱乐部端登录成功', status == 200, status)

    pg.goto(BASE + '/club?club_id=1', wait_until='domcontentloaded')
    pg.locator('.nav button[data-view="settings"]').click()
    pg.wait_for_selector('#setTabs', timeout=15000)
    tabs = pg.locator('#setTabs .set-tab').all_inner_texts()
    ck('设置顶栏无业务介绍入口', '业务介绍' not in tabs, tabs)
    ck('设置顶栏保留经营业态开关', '经营业态开关' in tabs, tabs)

    pg.locator('#setTabs .set-tab', has_text='经营业态开关').click()
    pg.wait_for_selector('#modswForm input[data-ms="bizIntro"]', timeout=15000)
    subs = pg.locator('#modswSubTabs .set-tab').all_inner_texts()
    ck('页内子 tab 齐全', subs == ['业态开关', '业务介绍'], subs)
    ck('默认展开业态开关', pg.locator('#modswSub-sw').is_visible() and not pg.locator('#modswSub-biz').is_visible())
    ck('业态开关四项仍在', pg.locator('#modswForm input[data-ms]').count() == 4,
       pg.locator('#modswForm input[data-ms]').count())
    ck('业务介绍业态开关仍在', pg.locator('#modswForm input[data-ms="bizIntro"]').count() == 1)
    pg.screenshot(path=OUT + '/sw_tab.png', full_page=True)

    pg.locator('#modswSubTabs .set-tab', has_text='业务介绍').click()
    pg.wait_for_selector('#bizForm #bizEnabled', timeout=15000)
    ck('业务介绍子页展开', pg.locator('#modswSub-biz').is_visible() and not pg.locator('#modswSub-sw').is_visible())
    ck('原展示开关仍渲染', pg.locator('#bizForm #bizEnabled').count() == 1)
    ck('原标题输入仍渲染', pg.locator('#bizForm #bizTitle').count() == 1)
    ck('原简介输入仍渲染', pg.locator('#bizForm #bizIntro').count() == 1)
    tag = pg.locator('#bizStateTag').inner_text()
    ck('原状态标签仍渲染', ('已开启' in tag or '已关闭' in tag), tag)
    ck('原业务条目区仍渲染', pg.locator('#bizItems').count() == 1)
    pg.screenshot(path=OUT + '/biz_tab.png', full_page=True)

    # 入口搬了，功能必须照常：添加业务弹窗 + 保存开关与文案
    pg.locator('button:has-text("＋ 添加业务")').click()
    pg.wait_for_selector('.ux-dialog #bizDlgTitle', timeout=10000)
    ck('添加业务弹窗仍可打开', pg.locator('.ux-dialog #bizDlgTitle').count() == 1)
    cancel = pg.locator('.ux-dialog [data-cancel]').last
    if cancel.count():
        cancel.click()
        pg.wait_for_timeout(250)

    pg.locator('#bizForm #bizTitle').fill('业务介绍 · 入口整合验证')
    pg.locator('#bizForm button:has-text("保存开关与文案")').click()
    pg.wait_for_timeout(1200)
    saved = pg.evaluate("async () => (await (await fetch('/api/club/1/biz-section')).json())")
    ck('原保存接口仍写穿', saved.get('title') == '业务介绍 · 入口整合验证', saved.get('title'))

    pg.locator('#modswSubTabs .set-tab', has_text='业态开关').click()
    pg.wait_for_timeout(300)
    ck('切回业态开关正常', pg.locator('#modswSub-sw').is_visible() and pg.locator('#modswForm input[data-ms="bizIntro"]').count() == 1)

    # 旧深链兼容：过去收藏的 ?tab=biz 仍落到业务介绍
    pg.goto(BASE + '/club?club_id=1&view=settings&tab=biz', wait_until='domcontentloaded')
    pg.wait_for_selector('#modswSub-biz #bizEnabled', timeout=15000)
    ck('旧深链落到经营业态开关', 'active' in (pg.locator('#setTabs .set-tab', has_text='经营业态开关').get_attribute('class') or ''))
    ck('旧深链展开业务介绍子页', pg.locator('#modswSub-biz').is_visible())
    ck('旧深链高亮业务介绍子 tab', 'active' in (pg.locator('#modswSubTabs .set-tab', has_text='业务介绍').get_attribute('class') or ''))
    pg.screenshot(path=OUT + '/deep_link.png', full_page=True)

    for w, h in ((1440, 1000), (390, 844)):
        pg.set_viewport_size({'width': w, 'height': h})
        pg.wait_for_timeout(250)
        over = pg.evaluate('document.documentElement.scrollWidth - document.documentElement.clientWidth')
        ck(f'{w}px 无横向溢出', over <= 1, over)
    ck('浏览器无未捕获异常', not errors, ' | '.join(errors[:3]))
    browser.close()

passed = sum(1 for _, ok, _ in R if ok)
print('=' * 72)
print(f'PASS={passed} FAIL={len(R)-passed} TOTAL={len(R)}')
raise SystemExit(0 if passed == len(R) else 1)
