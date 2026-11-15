"""手机端俱乐部/总平台侧栏抽屉交互验收（2026-11-15 P0 防回归）。

用户实拍：抽屉展开后菜单整片「点不动」。根因是 ux-drawer-scrim 挂在 body 下
(z-index:31)，而 .app{position:relative;z-index:1} 是独立层叠上下文，遮罩把整个
.app（包括 z-index:32 的 sidebar）盖住，elementFromPoint 命中 scrim 而非菜单。

本脚本用 390x844 触屏真浏览器，对两端都验证：
  遮罩挂载位置 / 菜单中心命中 / tap 后关抽屉并切视图 / 点遮罩与关闭按钮关闭 /
  不自动唤起软键盘 / 抽屉内滚动不穿透 / 关闭后滚动位置恢复 / 无横向溢出 / 桌面端回归。
"""
import json, os, sys
from playwright.sync_api import sync_playwright

B = os.getenv('CLUBOS_GATE_BASE', 'http://127.0.0.1:8124').rstrip('/')
OUT = os.getenv('CLUBOS_GATE_OUT', os.path.join('/tmp', 'clubos_hub_gate'))
PW = os.getenv('STAGING_PASSWORD', 'Staging-2026-ClubOS-demo!')
os.makedirs(OUT, exist_ok=True)

R = []


def ck(name, ok, ev=None):
    R.append({'name': name, 'ok': bool(ok), 'ev': ev})
    print(('[PASS] ' if ok else '[FAIL] ') + name + ' :: ' + json.dumps(ev, ensure_ascii=False)[:420], flush=True)


def login(pg, user):
    pg.goto(B + '/login', wait_until='domcontentloaded', timeout=30000)
    st = pg.evaluate("""async p=>{const r=await fetch('/api/auth/login',
      {method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({username:p[0],password:p[1]})});return r.status}""", [user, PW])
    return st


# 注意：Playwright 的 locator.click() 会先 scrollIntoViewIfNeeded，把已滚到中途的
# 页面拉回顶部，导致「开抽屉前 scrollY」基线被测试工具自己改掉（实测 420 -> 0）。
# 汉堡按钮一律用 DOM .click() 触发；菜单项与关闭按钮仍保留真实 touch tap 验证。

def hit_info(pg, view):
    return pg.evaluate("""(v)=>{
      const e=document.querySelector('.nav button[data-view="'+v+'"]');
      if(!e)return {found:false};
      const r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
      const el=document.elementFromPoint(x,y);
      return {found:true,x:Math.round(x),y:Math.round(y),
        hit:el?el.tagName+'.'+String(el.className||''):null,
        inside:e.contains(el)||el===e,
        chain:document.elementsFromPoint(x,y).slice(0,4)
          .map(z=>z.tagName+'.'+String(z.className||'').slice(0,36))};
    }""", view)


def state(pg):
    return pg.evaluate("""()=>{
      const sb=document.querySelector('.sidebar'),sc=document.querySelector('.ux-drawer-scrim');
      return {open:sb.classList.contains('ux-open'),inert:sb.inert,
        scrimInApp:!!sc&&sc.parentElement===document.querySelector('.app'),
        scrimZ:sc?getComputedStyle(sc).zIndex:null, sbZ:getComputedStyle(sb).zIndex,
        active:[...document.querySelectorAll('.view.active')].map(v=>v.id).slice(0,3),
        hash:location.hash, crumb:(document.getElementById('uxCurrentView')||{}).textContent||'',
        focused:(document.activeElement||{}).id||document.activeElement?.tagName||'',
        scrollY:Math.round(scrollY), bodyPos:getComputedStyle(document.body).position,
        overflow:document.documentElement.scrollWidth>innerWidth+1};
    }""")


with sync_playwright() as w:
    browser = w.chromium.launch(channel='chrome', headless=True)
    for end, user, view in (('club', 'club1_admin', 'actcats'),
                            ('platform', 'platform_admin', 'products')):
        ctx = browser.new_context(ignore_https_errors=True,
                                  viewport={'width': 390, 'height': 844},
                                  device_scale_factor=2, is_mobile=True, has_touch=True)
        pg = ctx.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)[:140]))
        st = login(pg, user)
        ck('%s: 管理账号登录成功' % end, st in (200, 201), {'status': st})
        pg.goto(B + '/' + end + '?club_id=1', wait_until='domcontentloaded', timeout=30000)
        pg.wait_for_timeout(1800)

        toggle = pg.locator('.ux-mobile-toggle')
        ck('%s: 手机视口有汉堡按钮' % end, toggle.count() == 1, {'count': toggle.count()})
        pg.evaluate("()=>document.querySelector('.ux-mobile-toggle').click()")
        pg.wait_for_timeout(500)
        s = state(pg)
        ck('%s: 抽屉展开且 sidebar 非 inert' % end, s['open'] and not s['inert'], s)
        ck('%s: 遮罩挂在 .app 内（与侧栏同层叠上下文）' % end, s['scrimInApp'],
           {'scrimInApp': s['scrimInApp'], 'scrimZ': s['scrimZ'], 'sbZ': s['sbZ']})
        ck('%s: 手机端打开抽屉不自动聚焦搜索框（不弹软键盘）' % end,
           s['focused'] != 'uxNavSearch', {'focused': s['focused']})

        h = hit_info(pg, view)
        ck('%s: 菜单中心 elementFromPoint 命中菜单本身（核心硬锁）' % end,
           h.get('inside') is True, h)

        # 关闭按钮：可见、命中区达标、点击能关
        cb = pg.locator('.ux-drawer-close')
        cbinfo = pg.evaluate("""()=>{const e=document.querySelector('.ux-drawer-close');
          if(!e)return null;const r=e.getBoundingClientRect();const s=getComputedStyle(e);
          return {w:Math.round(r.width),h:Math.round(r.height),display:s.display,
            hit:(()=>{const x=r.x+r.width/2,y=r.y+r.height/2;const t=document.elementFromPoint(x,y);
              return e.contains(t)||t===e})()};}""")
        ck('%s: 抽屉内有可见且可点的关闭按钮（>=40px）' % end,
           cbinfo and cbinfo['display'] != 'none' and cbinfo['w'] >= 40 and cbinfo['h'] >= 40
           and cbinfo['hit'], cbinfo)
        if cb.count() == 1:
            cb.tap()
            pg.wait_for_timeout(400)
            ck('%s: 点关闭按钮能收起抽屉' % end, not state(pg)['open'], state(pg))

        # 主流程：重新打开 -> tap 目标菜单 -> 关抽屉 + 切视图
        pg.evaluate("()=>document.querySelector('.ux-mobile-toggle').click()")
        pg.wait_for_timeout(400)
        before = state(pg)
        btn = pg.locator('.nav button[data-view="%s"]' % view)
        ck('%s: 目标菜单存在且可见' % end, btn.count() == 1 and btn.first.is_visible(),
           {'count': btn.count(), 'view': view})
        if btn.count() == 1:
            btn.first.tap(timeout=10000)
            pg.wait_for_timeout(600)
            after = state(pg)
            ck('%s: tap 菜单后抽屉自动关闭' % end, not after['open'],
               {'before': before['open'], 'after': after['open']})
            ck('%s: tap 菜单后目标视图激活且面包屑更新' % end,
               view in after['active'] or view in after['hash'],
               {'active': after['active'], 'hash': after['hash'], 'crumb': after['crumb']})

        # 点遮罩关闭
        pg.evaluate("()=>document.querySelector('.ux-mobile-toggle').click()")
        pg.wait_for_timeout(350)
        pg.touchscreen.tap(360, 420)
        pg.wait_for_timeout(350)
        ck('%s: 点右侧遮罩可关闭抽屉' % end, not state(pg)['open'], state(pg))

        # 制造真实滚动位置（两端首页高度不同，临时 min-height 保证可滚），验证
        # 「滚到中途打开抽屉 → 关闭后不跳回顶部」+ 抽屉内滚动不穿透 body。
        pg.evaluate("()=>document.documentElement.style.minHeight='2000px'")
        pg.evaluate("()=>window.scrollTo({top:420,behavior:'instant'})")
        pg.wait_for_timeout(250)
        y_before = pg.evaluate("()=>Math.round(scrollY)")
        pg.evaluate("()=>document.querySelector('.ux-mobile-toggle').click()")
        pg.wait_for_timeout(350)
        scroll = pg.evaluate("""()=>{const sb=document.querySelector('.sidebar');
          sb.scrollTop=260;
          return {top:sb.scrollTop,max:sb.scrollHeight-sb.clientHeight,
            bodyPos:getComputedStyle(document.body).position,bodyTop:document.body.style.top,
            overscroll:getComputedStyle(sb).overscrollBehaviorY};}""")
        ck('%s: 抽屉内可独立滚动且 body 保持滚动锁' % end,
           scroll['top'] > 100 and scroll['max'] > 100
           and scroll['bodyPos'] == 'fixed' and scroll['overscroll'] == 'contain'
           and scroll['bodyTop'].startswith('-'), scroll)
        # 收起后必须回到打开前的滚动位置（html 有 scroll-behavior:smooth 时最容易丢）
        pg.evaluate("()=>document.querySelector('.ux-drawer-close').click()")
        pg.wait_for_timeout(650)
        y_after = pg.evaluate("()=>Math.round(scrollY)")
        pg.evaluate("()=>document.documentElement.style.removeProperty('min-height')")
        ck('%s: 滚到中途开关抽屉后滚动位置不丢失' % end,
           y_before >= 100 and abs(y_after - y_before) <= 3,
           {'before': y_before, 'after': y_after})

        ck('%s: 手机端无横向溢出' % end, not state(pg)['overflow'],
           {'overflow': state(pg)['overflow']})
        ck('%s: 无 JS 运行时报错' % end, len(errs) == 0, errs[-3:])
        ctx.close()

    # 桌面端回归：关闭按钮隐藏、侧栏常驻、菜单仍可点
    for end, user, view in (('club', 'club1_admin', 'analytics'),
                            ('platform', 'platform_admin', 'clubs')):
        ctx = browser.new_context(ignore_https_errors=True, viewport={'width': 1280, 'height': 900})
        pg = ctx.new_page()
        login(pg, user)
        pg.goto(B + '/' + end + '?club_id=1', wait_until='domcontentloaded', timeout=30000)
        pg.wait_for_timeout(1500)
        d = pg.evaluate("""()=>{const c=document.querySelector('.ux-drawer-close'),
          sb=document.querySelector('.sidebar');
          return {closeDisplay:c?getComputedStyle(c).display:'missing',
            sbX:Math.round(sb.getBoundingClientRect().x),
            navs:[...document.querySelectorAll('.nav button[data-view]')].filter(b=>b.offsetParent!==null).length,
            toggleVisible:(()=>{const t=document.querySelector('.ux-mobile-toggle');
              return t?getComputedStyle(t).display!=='none':false})()};}""")
        ck('%s: 桌面端关闭按钮隐藏、侧栏常驻' % end,
           d['closeDisplay'] == 'none' and d['sbX'] >= 0 and d['navs'] >= 8, d)
        ctx.close()
    browser.close()

s = {'pass': sum(1 for x in R if x['ok']), 'fail': sum(1 for x in R if not x['ok']), 'items': R}
json.dump(s, open(os.path.join(OUT, 'results_mobile_nav_verify.json'), 'w'), ensure_ascii=False, indent=2)
print('\n=== MOBILE NAV VERIFY %d PASS / %d FAIL ===' % (s['pass'], s['fail']), flush=True)
sys.exit(0 if s['fail'] == 0 else 1)
