# -*- coding: utf-8 -*-
"""员工权限模块 · 真实浏览器验收（2026-11-16）。

双端后台新视图 × 桌面 1440 / 手机 390：导航可达、列表与权限树渲染、
添加员工弹窗可开可关、无横向溢出、无前端报错。独立临时库 + 端口，不碰真库。
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 8265
BASE = f'http://127.0.0.1:{PORT}'
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
env = os.environ.copy()
env.update(CLUBOS_TEST_OPEN='1', CLUBOS_SECURITY_MODE='demo', MOCK_AI='1', COMMERCE_PROVIDER='local',
           CLUBOS_DB_PATH=str(Path(tempfile.mkdtemp(prefix='clubos-staff-ui-')) / 'ui.db'))
proc = subprocess.Popen([str(ROOT / 'venv/bin/python'), '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', str(PORT), '--log-level', 'warning'],
                        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ROWS, ERRORS = [], []
try:
    for _ in range(60):
        try:
            with urlopen(BASE + '/platform', timeout=2):
                break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError('server did not start')

    def rec(name, ok, detail=''):
        ROWS.append((name, bool(ok)))
        print(('PASS ' if ok else 'FAIL ') + name + (f' | {detail}' if detail else ''), flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True)
        for page_name, view, row_sel, tree_sel in [
            ('platform', 'staff', '#platStaffRows', '#platStaffTree'),
            ('club', 'staffperms', '#clubStaffRows', '#clubStaffTree'),
        ]:
            for vp_name, width, height in [('desktop', 1440, 900), ('mobile', 390, 844)]:
                label = f'{page_name}/{view}@{vp_name}'
                ctx = browser.new_context(viewport={'width': width, 'height': height}, device_scale_factor=1)
                pg = ctx.new_page()
                pg.on('pageerror', lambda e: ERRORS.append(f'{label}: {e}'))
                pg.goto(BASE + f'/{page_name}', wait_until='domcontentloaded')
                pg.wait_for_timeout(800)
                if vp_name == 'mobile':
                    pg.locator('.ux-mobile-toggle').first.click()
                    pg.wait_for_timeout(250)
                pg.locator(f'.nav button[data-view="{view}"]').first.click()
                pg.wait_for_timeout(1200)
                active = pg.evaluate("()=>document.querySelector('section.view.active')?.id||''")
                rec(f'{label} 视图激活', active == view, active)
                nav = pg.locator(f'.nav button[data-view="{view}"]')
                rec(f'{label} 侧栏入口可见', nav.count() == 1 and nav.first.is_visible())
                rows_text = pg.locator(row_sel).inner_text() if pg.locator(row_sel).count() else ''
                rec(f'{label} 员工列表渲染', pg.locator(row_sel).count() > 0 and len(rows_text.strip()) > 0, rows_text[:70])
                cards = pg.locator(tree_sel + ' .plat-staff-group').count()
                rec(f'{label} 权限树渲染', cards >= 4, f'groups={cards}')
                pg.get_by_role('button', name='＋ 添加员工').first.click()
                pg.wait_for_timeout(300)
                dlg = pg.locator('.ux-dialog:visible')
                rec(f'{label} 添加员工弹窗可开', dlg.count() > 0 and '登录账号' in dlg.first.inner_text())
                if page_name == 'club':
                    rec(f'{label} 权限勾选框渲染', dlg.first.locator('.club-staff-check input').count() >= 10)
                cancel = dlg.first.locator('[data-cancel]')
                if cancel.count():
                    cancel.first.click()
                pg.wait_for_timeout(200)
                rec(f'{label} 弹窗可关闭', pg.locator('.ux-dialog:visible').count() == 0)
                ov = pg.evaluate("()=>({sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth})")
                rec(f'{label} 无横向溢出', ov['sw'] <= ov['cw'] + 2, str(ov))
                if vp_name == 'desktop':
                    pg.screenshot(path=str(Path(tempfile.gettempdir()) / f'clubos_staff_{page_name}_desktop.png'), full_page=False)
                ctx.close()
        browser.close()
finally:
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()

rec('浏览器无前端异常', not ERRORS, ' | '.join(ERRORS[:3]))
print('\n────', flush=True)
print(f'STAFF UI: {sum(1 for _, ok in ROWS if ok)}/{len(ROWS)}', flush=True)
if any(not ok for _, ok in ROWS):
    print('FAILED: ' + ' | '.join(n for n, ok in ROWS if not ok), flush=True)
    sys.exit(1)
print('STAFF UI OK', flush=True)
