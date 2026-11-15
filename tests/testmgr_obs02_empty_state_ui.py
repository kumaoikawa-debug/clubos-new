# -*- coding: utf-8 -*-
"""OBS-02 空态首次引导 · 双视口浏览器验证（2026-11-16）。"""
import os
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

BASE = os.getenv('CLUBOS_QA_BASE', 'http://127.0.0.1:8263')
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
R, F = [], []


def check(name, ok, detail=''):
    R.append(name)
    print(('PASS ' if ok else 'FAIL ') + name + ' | ' + str(detail)[:180], flush=True)
    if not ok:
        F.append(name)


# 静态：两处「点右上角…」指路型提示文案已被换成就地 CTA
js = urlopen(BASE + '/static/platform/platform.js?v=20261116a', timeout=10).read().decode()
check('静态：删除指路型提示文案「点右上角」', '点右上角' not in js,
      'occurrences=' + str(js.count('点右上角')))
check('静态：platEmpty 通用空态组件已定义', 'function platEmpty(' in js)

EXPECT = [
    ('supp-return', '暂无采购退货单', '去采购单', 'supp-po'),
    ('aftersales', '暂无售后单', '查看订单', 'orders'),
    ('an-health', '当前没有库存预警', None, None),
    ('pdash', '暂无待处理事项。', None, None),
]

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, executable_path=CHROME)
    for vp_name, vp in [('desktop1440', {'width': 1440, 'height': 900}),
                        ('mobile390', {'width': 390, 'height': 844})]:
        ctx = b.new_context(viewport=vp)
        pg = ctx.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.goto(BASE + '/platform', wait_until='domcontentloaded', timeout=30000)
        pg.wait_for_timeout(1700)

        has = pg.evaluate("typeof platEmpty==='function'")
        check(vp_name + ' platEmpty 已加载', has)
        if has:
            html = pg.evaluate("platEmpty('暂无售后单','查看订单',\"pgo('orders')\")")
            check(vp_name + ' 动作型空态生成 CTA 按钮',
                  'btn' in html and '查看订单' in html and "pgo('orders')" in html, html[:150])
            html2 = pg.evaluate("platEmpty('当前没有库存预警')")
            check(vp_name + ' 状态型空态不生成 CTA', '<button' not in html2, html2[:120])

        for view, text, cta, target in EXPECT:
            pg.evaluate("""(v)=>document.querySelector(`.nav button[data-view="${v}"]`)?.click()""", view)
            pg.wait_for_timeout(950)
            info = pg.evaluate("""()=>({
                empties:[...document.querySelectorAll('section.view.active .plat-empty')].map(x=>x.innerText.trim()),
                btns:[...document.querySelectorAll('section.view.active .plat-empty button')].map(x=>x.innerText.trim()),
                sw:document.documentElement.scrollWidth, cw:document.documentElement.clientWidth})""")
            check(vp_name + ' ' + view + ' 空态使用统一容器',
                  any(text in t for t in info['empties']), str(info['empties'])[:150])
            if cta:
                check(vp_name + ' ' + view + ' 出现 CTA「' + cta + '」', cta in info['btns'], str(info['btns']))
            else:
                check(vp_name + ' ' + view + ' 状态型空态无按钮', len(info['btns']) == 0, str(info['btns']))
            check(vp_name + ' ' + view + ' 无横向溢出',
                  info['sw'] <= info['cw'] + 1, f"{info['sw']}/{info['cw']}")

        # 真实点击：空态 CTA 必须能跳到目标板块
        pg.evaluate("""(v)=>document.querySelector(`.nav button[data-view="${v}"]`)?.click()""", 'supp-return')
        pg.wait_for_timeout(900)
        btn = pg.locator('section.view.active .plat-empty button:has-text("去采购单")')
        if btn.count():
            btn.first.click()
            pg.wait_for_timeout(900)
            active = pg.evaluate("document.querySelector('section.view.active')?.id")
            check(vp_name + ' 空态 CTA 点击跳到采购单', active == 'supp-po', active)
        else:
            check(vp_name + ' 空态 CTA 可点击', False, 'button missing')

        check(vp_name + ' 无脚本报错', not errs, str(errs)[:160])
        ctx.close()
    b.close()

print('\nOBS-02 空态引导验证 %d 项 | 失败 %d 项' % (len(R), len(F)), flush=True)
for n in F:
    print('  [FAIL] ' + n, flush=True)
raise SystemExit(1 if F else 0)
