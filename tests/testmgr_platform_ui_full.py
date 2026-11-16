# -*- coding: utf-8 -*-
"""经营者视角 · 总平台后台全量浏览器验收（2026-11-15 修复后）。

覆盖：24 个经营视图 × 桌面 1440 / 手机 390 逐个打开 + 真实网络请求全扫描
     + 经营者真实点击流（确认打款 / 标记签收 / 手机抽屉）
     + 共用 clubos-ux.js 的 club/web/leader 跨端回归
数据源：8262 隔离实例（/tmp/clubos_full_ui.db，内含真实成交/佣金/待打款结算单/已发货订单）。
"""
import json
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

BASE = os.getenv('CLUBOS_QA_BASE', 'http://127.0.0.1:8262')
FIXTURE_DB = Path(os.getenv('CLUBOS_QA_DB', '/tmp/clubos_full_ui.db'))

def reset_click_flow_fixture():
    """点击流夹具复位：把上一轮已消费的待打款/待签收数据恢复到可操作前置状态。"""
    if not FIXTURE_DB.exists():
        return
    with sqlite3.connect(str(FIXTURE_DB)) as db:
        db.execute("UPDATE commission_settlements SET status='requested', payment_ref='', paid_at=NULL")
        db.execute("UPDATE commission_ledger SET status='available', settlement_id=NULL, settled_at=NULL WHERE ledger_type='earn' AND status='settled'")
        db.execute("UPDATE gear_orders SET status='shipped', delivered_at=NULL, commerce_sync_status='shipped' WHERE status='delivered'")

reset_click_flow_fixture()
ROOT = str(Path(__file__).resolve().parents[1])
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
VIEWS = ['pdash', 'clubs', 'credits-acct', 'credits-plans', 'credits-topup', 'ai-config', 'api-config',
         'ppoints', 'pbenefits', 'products', 'supp-po', 'supp-move', 'supp-return', 'warehouse',
         'fin-settle', 'fin-profit', 'costbom', 'an-pending', 'an-health', 'an-biz',
         'orders', 'aftersales', 'commissions', 'pksettle', 'staff']
BAD = ['加载失败', 'is not a function', 'Cannot read', 'Internal Server Error', 'undefined is not']

R = {'checks': [], 'views': [], 'api_paths': set(), 'api_failures': [], 'page_errors': [], 'shots': []}


def rec(name, ok, detail='', vp=''):
    R['checks'].append({'name': name, 'ok': bool(ok), 'detail': str(detail)[:220], 'viewport': vp})
    print(('PASS ' if ok else 'FAIL ') + name + ' | ' + str(detail)[:190], flush=True)


def api(path):
    with urlopen(BASE + path, timeout=10) as f:
        return json.loads(f.read().decode())


def section_text(page):
    return page.evaluate("""()=>{const a=document.querySelector('section.view.active');
        return a?a.innerText:(document.querySelector('main')?.innerText||'')}""")


def overflow(page):
    return page.evaluate("""()=>({sw:document.documentElement.scrollWidth,
        cw:document.documentElement.clientWidth})""")


def attach(page, label):
    page.on('pageerror', lambda e: R['page_errors'].append(f'{label}: {e}'))
    page.on('request', lambda r: R['api_paths'].add(urlparse(r.url).path) if '/api/' in r.url else None)
    page.on('response', lambda r: R['api_failures'].append({'vp': label, 'status': r.status, 'url': r.url})
            if r.status >= 400 and '/api/' in r.url else None)


def scan_views(browser, label, w, h):
    ctx = browser.new_context(viewport={'width': w, 'height': h})
    page = ctx.new_page()
    attach(page, label)
    page.goto(BASE + '/platform', wait_until='domcontentloaded', timeout=30000)
    page.wait_for_timeout(1800)
    rec(f'{label} 平台后台首屏可打开', page.locator('section.view.active').count() == 1,
        'navButtons=' + str(page.locator('button[data-view]').count()), label)
    rec(f'{label} 导航入口覆盖 {len(VIEWS)} 个视图', page.locator('button[data-view]').count() == len(VIEWS),
        'count=' + str(page.locator('button[data-view]').count()), label)

    for v in VIEWS:
        btn = page.locator(f'button[data-view="{v}"]')
        if btn.count() == 0:
            rec(f'{label} 视图 {v} 存在导航入口', False, 'missing', label)
            continue
        page.evaluate("""(v)=>{const b=document.querySelector(`button[data-view="${v}"]`);if(b)b.click()}""", v)
        page.wait_for_timeout(680)
        active = page.evaluate("""()=>{const a=document.querySelector('section.view.active');return a?a.id:null}""")
        txt = section_text(page)
        hits = [k for k in BAD if k in txt]
        ov = overflow(page)
        row = {'vp': label, 'view': v, 'active': active, 'textLen': len(txt),
               'bad': hits, 'sw': ov['sw'], 'cw': ov['cw']}
        R['views'].append(row)
        rec(f'{label} 视图 {v} 渲染', active == v and not hits,
            f'active={active} textLen={len(txt)} bad={hits}', label)
        rec(f'{label} 视图 {v} 无整页横向溢出', ov['sw'] <= ov['cw'] + 1,
            f"sw={ov['sw']} cw={ov['cw']}", label)

    # 导航徽章净文本
    nav = page.evaluate("""()=>{const b=document.querySelector('button[data-view="orders"] .nav-label');
        const n=document.querySelectorAll('button[data-view="orders"] .plat-notice-badge').length;
        return {label:b?b.textContent.trim():null,badges:n}}""")
    rec(f'{label} 菜单名不含角标数字', nav['label'] == '订单履约', nav, label)
    rec(f'{label} 角标不重复', nav['badges'] <= 1, nav, label)

    # 手机抽屉
    if label.startswith('mobile'):
        toggle = page.locator('.ux-mobile-toggle')
        rec(f'{label} 汉堡按钮存在', toggle.count() >= 1, 'count=' + str(toggle.count()), label)
        if toggle.count():
            toggle.first.click()
            page.wait_for_timeout(350)
            opened = page.evaluate("""()=>!!document.querySelector('aside.sidebar.ux-open')""")
            rec(f'{label} 抽屉可打开', opened, 'ux-open=' + str(opened), label)
            page.evaluate("""()=>{const b=document.querySelector('button[data-view="pdash"]');if(b)b.click()}""")
            page.wait_for_timeout(450)
            closed = page.evaluate("""()=>!document.querySelector('aside.sidebar.ux-open')""")
            rec(f'{label} 点导航后抽屉自动收起', closed, 'closed=' + str(closed), label)
        page.screenshot(path=f'{ROOT}/PlatformFullQA_{label}_drawer.png', full_page=False)
        R['shots'].append(f'PlatformFullQA_{label}_drawer.png')

    # 关键视图截图
    for v, tag in [('pdash', 'pdash'), ('commissions', 'commissions'), ('orders', 'orders'), ('fin-profit', 'profit')]:
        page.evaluate("""(v)=>{const b=document.querySelector(`button[data-view="${v}"]`);if(b)b.click()}""", v)
        page.wait_for_timeout(700)
        p = f'{ROOT}/PlatformFullQA_{label}_{tag}.png'
        page.screenshot(path=p, full_page=False)
        R['shots'].append(f'PlatformFullQA_{label}_{tag}.png')
    ctx.close()


def operator_flows(browser):
    """经营者真实资金/履约操作（会改数据，故放在全视图扫描之后，仅桌面执行）。"""
    label = 'desktop1440'
    ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
    page = ctx.new_page()
    attach(page, label + '-flow')
    page.goto(BASE + '/platform', wait_until='domcontentloaded', timeout=30000)
    page.wait_for_timeout(1600)

    # ① 佣金结算：结算记录应渲染真实数据
    page.evaluate("""()=>document.querySelector('button[data-view="commissions"]').click()""")
    page.wait_for_timeout(1500)
    txt = section_text(page)
    rec('经营者看到真实结算记录', '远拓户外' in txt and '167.76' in txt, txt[:150].replace('\n', ' '), label)
    rec('结算记录显示「待打款」状态', '待打款' in txt or '俱乐部已申请' in txt, 'state text found', label)
    rec('结算记录无加载失败', not any(k in txt for k in BAD), 'clean', label)
    pay_btn = page.locator('#settlementList button:has-text("确认打款")')
    rec('「确认打款」按钮可点击', pay_btn.count() >= 1, 'count=' + str(pay_btn.count()), label)

    before = api('/api/platform/commission-settlements')
    st_before = before[0]['status'] if before else None
    if pay_btn.count():
        pay_btn.first.click()
        page.wait_for_timeout(500)
        inp = page.locator('#uxf_ref')
        rec('打款弹窗要求填写银行凭证号', inp.count() == 1, 'inputCount=' + str(inp.count()), label)
        if inp.count():
            inp.fill('QA-BANK-20261115')
            page.locator('[data-submit]').first.click()
            page.wait_for_timeout(1800)
            after = api('/api/platform/commission-settlements')
            st_after = after[0]['status'] if after else None
            rec('点击确认打款后结算单转为已打款', st_before == 'requested' and st_after == 'paid',
                f'{st_before} -> {st_after}', label)
            rec('打款凭证号已记录', any('QA-BANK-20261115' in str(x.get('payment_ref')) for x in after),
                [x.get('payment_ref') for x in after], label)
    rec('打款后页面无报错弹窗', not any(k in section_text(page) for k in BAD), 'clean', label)

    # ② 订单履约：标记签收
    page.evaluate("""()=>document.querySelector('button[data-view="orders"]').click()""")
    page.wait_for_timeout(1400)
    otxt = section_text(page)
    rec('订单台显示已发货订单', '2097' in otxt or 'SF' in otxt, otxt[:150].replace('\n', ' '), label)
    dbtn = page.locator('button:has-text("标记签收")')
    rec('「标记签收」按钮存在', dbtn.count() >= 1, 'count=' + str(dbtn.count()), label)
    if dbtn.count():
        dbtn.first.click()
        page.wait_for_timeout(450)
        ok = page.locator('.ux-dialog [data-ok]')
        rec('签收二次确认弹窗出现', ok.count() >= 1, 'count=' + str(ok.count()), label)
        if ok.count():
            ok.first.click()
            page.wait_for_timeout(1600)
            orders = api('/api/platform/orders')
            st = orders[0].get('status') if isinstance(orders, list) and orders else None
            rec('签收后订单状态推进为 delivered', st == 'delivered', f'status={st}', label)
    ctx.close()


def cross_apps(browser):
    """clubos-ux.js 四端共用，回归另外三端页面可打开且无脚本报错。"""
    ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
    for path, name in [('/club', '俱乐部后台'), ('/web', 'C端前台'), ('/leader', '领队端')]:
        page = ctx.new_page()
        errs = []
        page.on('pageerror', lambda e, bucket=errs: bucket.append(str(e)))
        page.goto(BASE + path, wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(1200)
        rec(f'共用UX跨端：{name}可打开且无脚本异常', not errs and page.locator('body').count() == 1,
            f'errors={errs[:2]}', 'desktop1440')
        page.close()
    ctx.close()


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=CHROME)
    scan_views(browser, 'desktop1440', 1440, 900)
    scan_views(browser, 'mobile390', 390, 844)
    operator_flows(browser)
    cross_apps(browser)
    browser.close()

# 汇总网络契约
fails = [x for x in R['api_failures'] if x['status'] >= 500]
soft4xx = [x for x in R['api_failures'] if 400 <= x['status'] < 500]
rec('全视图 API 无 5xx/服务端异常', len(fails) == 0, fails[:5], 'both')
rec('全视图 API 无 4xx 契约错误', len(soft4xx) == 0, soft4xx[:5], 'both')
rec('全视图零脚本 pageerror', len(R['page_errors']) == 0, R['page_errors'][:5], 'both')
rec('覆盖 API 端点数 ≥ 40', len(R['api_paths']) >= 40, len(R['api_paths']), 'both')
rec(f'{len(VIEWS)} 视图 × 双视口全部扫描完成', len(R['views']) == len(VIEWS) * 2, len(R['views']), 'both')

R['api_paths'] = sorted(R['api_paths'])
total = len(R['checks'])
failed = [x for x in R['checks'] if not x['ok']]
with open('/tmp/clubos_full_ui_result.json', 'w', encoding='utf-8') as f:
    json.dump(R, f, ensure_ascii=False, indent=2)
print('\n' + '=' * 62)
print('浏览器全量验收 %d 项 | 失败 %d 项 | API 端点 %d 个 | 截图 %d 张'
      % (total, len(failed), len(R['api_paths']), len(R['shots'])))
for x in failed:
    print('  FAIL ' + x['name'] + ' | ' + x['detail'])
