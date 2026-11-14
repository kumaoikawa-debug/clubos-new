# -*- coding: utf-8 -*-
"""设置中枢 P1 修复 · 真 Chrome 定向验收（隔离实例 CLUBOS_GATE_BASE，默认 8124）。"""
import json, os, sys, time
from playwright.sync_api import sync_playwright

# 发布前门禁注入：实例地址 / 输出目录 / demo 口令均可覆盖，禁止硬编码临时路径。
B = os.getenv('CLUBOS_GATE_BASE', 'http://127.0.0.1:8124').rstrip('/')
OUT = os.getenv('CLUBOS_GATE_OUT', os.path.join('/tmp', 'clubos_hub_gate'))
PW = os.getenv('STAGING_PASSWORD', 'Staging-2026-ClubOS-demo!')
os.makedirs(OUT, exist_ok=True)
R = []
def ck(cid, title, cond, evi=''):
    R.append({'id': cid, 'title': title, 'ok': bool(cond), 'evidence': str(evi)[:400]})
    print(('PASS ' if cond else 'FAIL ') + cid + ' ' + title + ' | ' + str(evi)[:220], flush=True)

def login(pg, user):
    pg.goto(B + '/login', wait_until='domcontentloaded')
    r = pg.evaluate("""async u=>{const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u[0],password:u[1]})});return r.status}""", [user, PW])
    return r

def hub(pg, q='club_id=1'):
    pg.goto(B + '/static/club/settings-hub.html?' + q, wait_until='load')
    pg.wait_for_timeout(1100)

with sync_playwright() as w:
    b = w.chromium.launch(channel='chrome', headless=True)
    ctx = b.new_context(viewport={'width': 1280, 'height': 1000})
    ctx.new_cdp_session(ctx.new_page()).send('Network.setCacheDisabled', {'cacheDisabled': True})
    pg = ctx.new_page()
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    print('login club1_admin =', login(pg, 'club1_admin'), flush=True)
    hub(pg)

    # ── BUG-HUB-001 复选框可点 ────────────────────────────────────────────
    box = pg.evaluate("""()=>{const e=document.querySelector('#prefs input[type=checkbox]');
       if(!e)return null;const r=e.getBoundingClientRect();return {w:r.width,h:r.height,key:e.dataset.key};}""")
    ck('P1-01', '「允许外发通知」复选框渲染宽度 > 8px', box and box['w'] > 8, box)
    clicked = True
    try:
        pg.locator('#prefs input[type=checkbox]').first.click(timeout=4000)
    except Exception as e:
        clicked = False
        print('click err', e, flush=True)
    state = pg.evaluate("""()=>{const e=document.querySelector('#prefs input[type=checkbox]');
       return e?{checked:e.checked,dirty:e.dataset.dirty||''}:null}""")
    ck('P1-02', '真实鼠标点击可切换勾选并标记 dirty', clicked and state and state['dirty'] == '1', state)

    # ── BUG-HUB-003 前端：俱乐部账号看不到平台层级 ────────────────────────
    scope = pg.evaluate("""()=>({opts:[...document.querySelectorAll('#scope option')].map(o=>o.value),
        note:(document.querySelector('#scopeNote')||{}).textContent||''})""")
    ck('P1-03', '俱乐部账号下拉只有本俱乐部，且有权限说明',
       scope['opts'] == ['club'] and '总平台' in scope['note'], scope)

    # ── BUG-HUB-004 切层级 dirty 二次确认 ─────────────────────────────────
    pg.evaluate("""()=>{const s=document.querySelector('#scope');
       if(![...s.options].some(o=>o.value==='platform')){const o=document.createElement('option');o.value='platform';o.textContent='平台默认（测试注入）';s.appendChild(o)}
       s.value='platform';s.dispatchEvent(new Event('change',{bubbles:true}));}""")
    pg.wait_for_timeout(700)
    dlg = pg.evaluate("""()=>{const d=[...document.querySelectorAll('.ux-dialog')].filter(x=>x.offsetParent!==null);
       return d.length?{text:d[0].textContent.slice(0,160),cancel:!!d[0].querySelector('[data-cancel]'),ok:!!d[0].querySelector('[data-ok]')}:null}""")
    ck('P1-04', '有未保存改动时切层级弹出统一确认层', dlg and '放弃未保存' in dlg['text'] and dlg['cancel'], dlg)
    pg.locator('.ux-dialog [data-cancel]').first.click()
    pg.wait_for_timeout(350)
    after_cancel = pg.evaluate("""()=>({scope:document.querySelector('#scope').value,
        dirty:document.querySelectorAll('.hub-ctl[data-dirty]').length})""")
    ck('P1-05', '取消后层级还原且改动保留', after_cancel['scope'] == 'club' and after_cancel['dirty'] >= 1, after_cancel)

    # 确认放弃 → 走 loadAll（平台层级 403）→ 顺带验 BUG-HUB-005
    pg.evaluate("""()=>{const s=document.querySelector('#scope');s.value='platform';s.dispatchEvent(new Event('change',{bubbles:true}));}""")
    pg.wait_for_timeout(600)
    pg.locator('.ux-dialog [data-ok]').first.click()
    pg.wait_for_timeout(1100)
    err_state = pg.evaluate("""()=>({stats:document.querySelector('#stats').textContent.trim(),
        mods:document.querySelector('#modules').textContent.trim(),
        audit:document.querySelector('#audit').textContent.trim(),
        prefs:document.querySelector('#prefs').textContent.trim(),
        retry:!!document.querySelector('#retryLoad')})""")
    ck('P1-06', '读取失败时统计/卡片/审计全部清空，不残留上一层级数据',
       '读取失败' in err_state['stats'] and '已清空' in err_state['mods']
       and '不可用' in err_state['audit'] and err_state['retry'], err_state)
    ck('P1-07', '失败态提供重试按钮', err_state['retry'], err_state['retry'])

    # ── BUG-HUB-006 总开关关闭后卡片显示「中枢已关闭」 ────────────────────
    pg.evaluate("""async()=>{await fetch('/api/club/1/settings-hub',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({changes:{'module.pageLayout':false}})});}""")
    hub(pg)
    badge = pg.evaluate("""()=>{const cards=[...document.querySelectorAll('#modules .mod')];
       const t=cards.find(c=>c.querySelector('.mod__t')&&c.querySelector('.mod__t').textContent.includes('界面装修器'));
       return {card:t?t.textContent.slice(0,140):null,stats:document.querySelector('#stats').textContent.trim(),
               closedCount:[...document.querySelectorAll('#modules .badge')].filter(x=>x.textContent.includes('中枢已关闭')).length};}""")
    ck('P1-08', '关闭总开关后卡片 badge 显示「中枢已关闭」',
       badge['closedCount'] >= 1 and badge['card'] and '中枢已关闭' in badge['card'], badge)

    # 还原配置
    pg.evaluate("""async()=>{await fetch('/api/club/1/settings-hub/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});}""")

    # ── BUG-HUB-007 连续 6 次刷新导航状态唯一（必须用匿名会员上下文）──────
    ctx3 = b.new_context(viewport={'width': 390, 'height': 844})
    pg3 = ctx3.new_page()
    c_errs = []
    pg3.on('pageerror', lambda e: c_errs.append(str(e)))
    states = []
    total_btns = []
    for i in range(6):
        pg3.goto(B + '/web?club_id=1', wait_until='domcontentloaded')
        pg3.wait_for_timeout(2000)
        d = pg3.evaluate("""()=>({total:document.querySelectorAll('.web-nav button').length,
            vis:[...document.querySelectorAll('.web-nav button')]
                .filter(x=>getComputedStyle(x).display!=='none').map(x=>x.dataset.wv)})""")
        states.append(tuple(d['vis']))
        total_btns.append(d['total'])
    uniq = sorted(set(states))
    ck('P1-09', '连续 6 次刷新底部导航状态唯一（无异步竞态）',
       len(uniq) == 1 and min(total_btns) == 6 and len(uniq[0]) >= 4,
       {'runs': [list(s) for s in states], 'totalBtns': total_btns, 'unique': [list(s) for s in uniq]})
    ck('P1-10', 'C 端无 JS 运行时错误', not c_errs, c_errs[:3])
    ctx3.close()

    # ── BUG-HUB-003 平台账号仍可见平台层级 ────────────────────────────────
    ctx2 = b.new_context(viewport={'width': 1280, 'height': 1000})
    pg2 = ctx2.new_page()
    print('login platform_admin =', login(pg2, 'platform_admin'), flush=True)
    hub(pg2)
    opts2 = pg2.evaluate("()=>[...document.querySelectorAll('#scope option')].map(o=>o.value)")
    me = pg2.evaluate("async()=>{const r=await fetch('/api/auth/me');return (await r.json()).role}")
    ck('P1-11', '平台账号仍保留平台默认层级', me == 'platform' and 'platform' in opts2, {'role': me, 'opts': opts2})

    ctx.close(); ctx2.close(); b.close()

s = {'pass': sum(1 for x in R if x['ok']), 'fail': sum(1 for x in R if not x['ok']), 'items': R}
json.dump(s, open(os.path.join(OUT, 'results_p1_verify.json'), 'w'), ensure_ascii=False, indent=2)
print('\n=== P1 VERIFY %d PASS / %d FAIL ===' % (s['pass'], s['fail']), flush=True)
sys.exit(0 if s['fail'] == 0 else 1)
