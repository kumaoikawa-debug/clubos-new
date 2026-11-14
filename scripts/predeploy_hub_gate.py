#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ClubOS 发布前 · 设置中枢回归门禁（2026-11-14）。

串联 4 份真浏览器 / 端到端验收脚本，并**独立重算** 4 条不可绕过的硬锁：

  G1_PREF_WIDTH       4 个偏好控件实测 width > 8px（BUG-HUB-001 复选框被压成 0px）
  G2_OUTBOUND_POLICY  关总开关后，不传 hub 的任意业务通知一律 hub-outbound-disabled
                      （BUG-HUB-002 漏传参数即可绕过「允许外发通知」；站内信不受限）
  G3_NAV_STABLE       匿名会员连续 6 次刷新底部导航状态唯一（BUG-HUB-007 异步竞态）
  G4_SCOPE_DIRTY      有 dirty 时切层级弹二次确认，取消后层级还原且改动保留（BUG-HUB-004）

为什么硬锁不复用脚本结果：脚本本身可能被改弱、被跳过，甚至输出 PASS 却没真正断言。
门禁自己从 DOM / 业务代码重算一遍，任一 FAIL 即退出 1，阻断发布。

用法（发布前统一入口会自动调用本脚本）：
  venv/bin/python scripts/predeploy_hub_gate.py

环境变量：
  CLUBOS_GATE_BASE   隔离实例地址，默认 http://127.0.0.1:8124（须先按发布纪律起实例）
  CLUBOS_GATE_OUT    证据输出目录，默认 /tmp/clubos_hub_gate
  STAGING_PASSWORD   demo 口令，沿用项目 scripts/ 下既有默认值

退出码：0 = 4 份脚本全绿且 4 条硬锁全过；1 = 任一 FAIL
        （实例不可达 / Chrome 不可用 / 脚本无退出码 / 结果 JSON 缺失一律视为 FAIL，不静默跳过）。

本脚本不修改产品代码、不触碰线上；对隔离实例的写操作仅限于「制造 dirty 再取消」，
不保存任何配置。
"""
from __future__ import annotations

import importlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE_DIR = Path(__file__).resolve().parent / 'hub_gate'
BASE = os.getenv('CLUBOS_GATE_BASE', 'http://127.0.0.1:8124').rstrip('/')
OUT = os.getenv('CLUBOS_GATE_OUT', os.path.join('/tmp', 'clubos_hub_gate'))
PW = os.getenv('STAGING_PASSWORD', 'Staging-2026-ClubOS-demo!')

# 4 份脚本 -> 各自的结果 JSON。跑前先删旧 JSON，防止「上次跑绿的残留」造成假绿。
SCRIPTS = (
    ('p1_verify.py', 'results_p1_verify.json', 'P1 真浏览器定向验收'),
    ('p2_verify.py', 'results_p2_verify.json', 'P2 端到端验收'),
    ('uiux_verify.py', 'results_uiux_verify.json', 'UI/UX 专项验收'),
    ('four_end_verify.py', 'results_four_end_verify.json', '四端真浏览器验证'),
)

LOCKS = ('G1_PREF_WIDTH', 'G2_OUTBOUND_POLICY', 'G3_NAV_STABLE', 'G4_SCOPE_DIRTY')
LABEL = {
    'G1_PREF_WIDTH': '4 个偏好控件 width > 8px',
    'G2_OUTBOUND_POLICY': '关总开关后任意业务通知 reason=hub-outbound-disabled',
    'G3_NAV_STABLE': '连续 6 次刷新 nav 状态唯一',
    'G4_SCOPE_DIRTY': '切层级有 dirty 时二次确认',
}
RESULTS: list[tuple[str, str, object]] = []


def rec(level: str, name: str, detail: object = '') -> None:
    RESULTS.append((level, name, detail))
    txt = str(detail).replace('\n', ' ')
    print('[%s] %s%s' % (level, name, (' :: ' + txt[:320]) if detail != '' else ''), flush=True)


def read(rel: str) -> str:
    p = ROOT / rel
    return io.open(p, encoding='utf-8').read() if p.is_file() else ''


def connectivity() -> tuple[bool, str]:
    """发布前隔离实例必须真的活着；不连通就当 FAIL，不允许降级跳过。"""
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(BASE + '/login', timeout=6) as r:
            return r.status == 200, '%s /login HTTP %s' % (BASE, r.status)
    except Exception as e:
        return False, ('%s 不可达：%s；请先按发布纪律起隔离实例（uvicorn app:app '
                       '--host 127.0.0.1 --port 8124 + 带 seed 的临时库），'
                       '或用 CLUBOS_GATE_BASE 指向已就绪的实例' % (BASE, e))


def run_script(name: str, json_name: str) -> tuple[bool, str]:
    path = GATE_DIR / name
    jpath = os.path.join(OUT, json_name)
    if not path.is_file():
        return False, '脚本缺失：%s' % path
    if os.path.exists(jpath):
        os.remove(jpath)
    env = dict(os.environ)
    env.update({'CLUBOS_GATE_BASE': BASE, 'CLUBOS_GATE_OUT': OUT,
                'STAGING_PASSWORD': PW})
    try:
        p = subprocess.run([sys.executable, str(path)], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, timeout=600)
    except Exception as e:
        return False, '执行异常: %s' % e
    lines = [x for x in (p.stdout or '').strip().splitlines() if x.strip()]
    last = lines[-1] if lines else '(无输出)'
    ok = p.returncode == 0
    detail = 'rc=%d · %s' % (p.returncode, last[:150])
    if os.path.isfile(jpath):
        try:
            data = json.load(io.open(jpath, encoding='utf-8'))
            npass, nfail = data.get('pass', -1), data.get('fail', -1)
            ok = ok and nfail == 0 and npass > 0
            detail += ' · JSON %s PASS / %s FAIL' % (npass, nfail)
        except Exception as e:
            ok, detail = False, detail + ' · 结果 JSON 解析失败: %s' % e
    else:
        ok, detail = False, detail + ' · 本轮未产出结果 JSON（可能中途崩溃）'
    if not ok and (p.stderr or '').strip():
        detail += ' · stderr: ' + (p.stderr.strip().splitlines() or [''])[-1][:150]
    return ok, detail


# ── 浏览器公共件 ────────────────────────────────────────────────────────────
def _ctx(b, width=1280, height=1000):
    ctx = b.new_context(viewport={'width': width, 'height': height})
    # 禁缓存：Playwright daemon 常驻，改完静态文件后极易拿到旧版 JS 造成假绿。
    ctx.new_cdp_session(ctx.new_page()).send('Network.setCacheDisabled',
                                             {'cacheDisabled': True})
    return ctx


def _login(pg, user):
    pg.goto(BASE + '/login', wait_until='domcontentloaded')
    return pg.evaluate("""async u=>{const r=await fetch('/api/auth/login',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:u[0],password:u[1]})});return r.status}""", [user, PW])


def G1_pref_width(b):
    """4 个通用偏好控件都必须有可点击的实测宽度（不只是第一个 checkbox）。"""
    ctx = _ctx(b)
    pg = ctx.new_page()
    try:
        st = _login(pg, 'club1_admin')
        pg.goto(BASE + '/static/club/settings-hub.html?club_id=1', wait_until='load')
        pg.wait_for_timeout(1200)
        d = pg.evaluate("""()=>[...document.querySelectorAll('#prefs .hub-ctl')].map(e=>{
          const r=e.getBoundingClientRect();
          return {key:e.dataset.key||'', w:Math.round(r.width*10)/10,
                  vis:e.offsetParent!==null, tag:e.tagName};})""")
        ok = st == 200 and len(d) == 4 and all(x['w'] > 8 and x['vis'] for x in d)
        return ok, {'login': st, 'count': len(d), 'controls': d}
    finally:
        ctx.close()


def G3_nav_stable(b):
    """匿名会员（不能用后台登录态）连续 6 次刷新，可见导航必须只有一种状态。"""
    ctx = _ctx(b, 390, 844)
    pg = ctx.new_page()
    try:
        states, totals = [], []
        for _ in range(6):
            pg.goto(BASE + '/web?club_id=1', wait_until='domcontentloaded')
            pg.wait_for_timeout(2000)
            d = pg.evaluate("""()=>({total:document.querySelectorAll('.web-nav button').length,
              vis:[...document.querySelectorAll('.web-nav button')]
                .filter(x=>getComputedStyle(x).display!=='none').map(x=>x.dataset.wv)})""")
            states.append(tuple(d['vis']))
            totals.append(d['total'])
        uniq = sorted(set(states))
        # 护栏：不能因为页面没加载（可见导航恒为空）而误判「状态唯一」。
        ok = (len(uniq) == 1 and min(totals) >= 4 and len(uniq[0]) >= 4)
        return ok, {'runs': [list(x) for x in states], 'totalBtns': totals,
                    'unique': [list(x) for x in uniq]}
    finally:
        ctx.close()


def G4_scope_dirty(b):
    """有未保存改动时切层级必须弹统一确认层，取消后层级还原且改动保留。"""
    ctx = _ctx(b)
    pg = ctx.new_page()
    try:
        st = _login(pg, 'club1_admin')
        pg.goto(BASE + '/static/club/settings-hub.html?club_id=1', wait_until='load')
        pg.wait_for_timeout(1200)
        # 真实点击布尔开关制造 dirty（change 事件会标记 data-dirty）。
        pg.locator('#prefs input[type=checkbox].hub-ctl').first.click(timeout=5000)
        pg.wait_for_timeout(250)
        dirty = pg.evaluate("()=>document.querySelectorAll('.hub-ctl[data-dirty]').length")
        # 俱乐部账号看不到平台层级，注入一个 option 才能走「切层级」分支。
        pg.evaluate("""()=>{const s=document.querySelector('#scope');
          if(![...s.options].some(o=>o.value==='platform')){
            const o=document.createElement('option');o.value='platform';
            o.textContent='平台默认（门禁注入）';s.appendChild(o)}
          s.value='platform';s.dispatchEvent(new Event('change',{bubbles:true}));}""")
        pg.wait_for_timeout(700)
        dlg = pg.evaluate("""()=>{const d=[...document.querySelectorAll('.ux-dialog')]
          .filter(x=>x.offsetParent!==null);
          return d.length?{text:d[0].textContent.slice(0,160),
            cancel:!!d[0].querySelector('[data-cancel]'),
            ok:!!d[0].querySelector('[data-ok]')}:null}""")
        ev = {'login': st, 'dirty': dirty, 'dialog': dlg}
        ok = st == 200 and dirty >= 1 and bool(dlg) \
            and '放弃未保存' in dlg['text'] and dlg['cancel']
        if dlg and dlg['cancel']:
            pg.locator('.ux-dialog [data-cancel]').first.click()
            pg.wait_for_timeout(350)
            after = pg.evaluate("""()=>({scope:document.querySelector('#scope').value,
              dirty:document.querySelectorAll('.hub-ctl[data-dirty]').length})""")
            ev['afterCancel'] = after
            ok = ok and after['scope'] == 'club' and after['dirty'] >= 1
        return ok, ev
    finally:
        ctx.close()


def _gate_db():
    """G2 专用内存库：只建 dispatch 真正会读的表，不 import app、不碰任何真库。"""
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE settings_hub_values (club_id INTEGER NOT NULL, key TEXT NOT NULL,
                 value_json TEXT, updated_at TEXT, UNIQUE(club_id, key))""")
    c.execute("""CREATE TABLE notification_channels (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 club_id INTEGER NOT NULL, type TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
                 config_json TEXT, created_at TEXT, updated_at TEXT)""")
    c.execute("""CREATE TABLE notification_templates (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 club_id INTEGER NOT NULL DEFAULT 0, code TEXT NOT NULL,
                 channel TEXT NOT NULL DEFAULT 'inapp', title TEXT, body TEXT,
                 enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT, updated_at TEXT)""")
    c.execute("""CREATE TABLE inapp_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user_id INTEGER, club_id INTEGER NOT NULL, code TEXT, title TEXT, body TEXT,
                 is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT)""")
    c.execute("""CREATE TABLE club_integrations (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 club_id INTEGER NOT NULL, type TEXT, config_json TEXT)""")
    for t in ('wechat', 'email', 'sms', 'inapp'):
        c.execute("INSERT INTO notification_channels(club_id,type,enabled,config_json) "
                  "VALUES(1,?,1,'{}')", (t,))
    codes = ('signup', 'engagement_reward', 'group_buy_success', 'gift_card_redeemed')
    for code in codes:
        c.execute("INSERT INTO notification_templates(club_id,code,channel,title,body,enabled) "
                  "VALUES(1,?,'wechat','门禁标题','门禁正文',1)", (code,))
    c.execute("INSERT INTO notification_templates(club_id,code,channel,title,body,enabled) "
              "VALUES(1,'__gate_inapp__','inapp','站内','站内',1)")
    return c, codes


def G2_outbound_policy():
    """关总开关后，业务调用点「不传 hub」也必须被拦；站内信不受限。

    静态锁防「把 dispatch 改回不传 hub 就不启用策略」；动态锁用真实业务 code ×
    3 个外发通道逐一验 reason，任何一条漏网都算 FAIL。
    """
    src = read('clubos_domain/notifications.py')
    app = read('app.py')
    static_ok = all(x in src for x in (
        '_policy_from_db', '_policy_from_db(c, club_id) if hub is None',
        "'hub-outbound-disabled'", "channel != 'inapp'"))
    caller_ok = (app.count('notif_mod.dispatch(') >= 3
                 and all(x in app for x in ("'engagement_reward'",
                                            "'group_buy_success'",
                                            "'gift_card_redeemed'")))
    ev: dict = {'staticDispatchGuard': static_ok, 'businessCallers': caller_ok}
    if not static_ok or not caller_ok:
        return False, ev
    try:
        sys.path.insert(0, str(ROOT))
        nf = importlib.import_module('clubos_domain.notifications')
        c, codes = _gate_db()
        c.execute("INSERT INTO settings_hub_values(club_id,key,value_json,updated_at) "
                  "VALUES(1,?,?,datetime('now'))",
                  ('notify.outboundEnabled', json.dumps(False)))
        cases = []
        for code in codes:
            for ch in ('wechat', 'email', 'sms'):
                r = nf.dispatch(c, club_id=1, code=code, ctx={'name': '门禁'},
                                user_id=7, channel_override=ch)
                cases.append({'code': code, 'channel': ch, 'reason': r.get('reason')})
        inapp = nf.dispatch(c, club_id=1, code='__gate_inapp__',
                            user_id=7).get('reason')
        ev.update({'cases': cases, 'inapp': inapp,
                   'policyRead': nf._policy_from_db(c, 1)})
        all_blocked = all(x['reason'] == 'hub-outbound-disabled' for x in cases)
        return all_blocked and inapp == 'inapp-queued', ev
    except Exception as e:
        ev['error'] = str(e)
        return False, ev


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    alive, why = connectivity()
    rec('PASS' if alive else 'FAIL', '门禁实例可达', why)

    # ① 4 份脚本：退出码 + 本轮新产出的结果 JSON 双重校验
    for name, jname, label in SCRIPTS:
        if not alive:
            rec('FAIL', '脚本 %s · %s' % (name, label), '实例不可达，未执行（视为 FAIL）')
        else:
            ok, detail = run_script(name, jname)
            rec('PASS' if ok else 'FAIL', '脚本 %s · %s' % (name, label), detail)

    # ② G2 纯后端，实例不可达也要跑（它不依赖服务）
    ok2, ev2 = G2_outbound_policy()
    rec('PASS' if ok2 else 'FAIL', 'G2_OUTBOUND_POLICY ' + LABEL['G2_OUTBOUND_POLICY'], ev2)

    # ③ G1 / G3 / G4 真浏览器硬锁
    b = None
    pw = None
    if alive:
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            b = pw.chromium.launch(channel='chrome', headless=True)
        except Exception as e:
            rec('FAIL', '真 Chrome 可用', str(e)[:200])
    for marker, fn in (('G1_PREF_WIDTH', G1_pref_width),
                       ('G3_NAV_STABLE', G3_nav_stable),
                       ('G4_SCOPE_DIRTY', G4_scope_dirty)):
        if not alive or b is None:
            rec('FAIL', marker + ' ' + LABEL[marker], '实例/浏览器不可用，未执行（视为 FAIL）')
            continue
        try:
            ok, ev = fn(b)
            rec('PASS' if ok else 'FAIL', marker + ' ' + LABEL[marker], ev)
        except Exception as e:
            rec('FAIL', marker + ' ' + LABEL[marker], '执行异常: %s' % e)
    if b is not None:
        b.close()
    if pw is not None:
        pw.stop()

    n_fail = sum(1 for x in RESULTS if x[0] == 'FAIL')
    n_pass = sum(1 for x in RESULTS if x[0] == 'PASS')
    summary = {'base': BASE, 'pass': n_pass, 'fail': n_fail,
               'items': [{'level': l, 'name': n, 'detail': str(d)[:600]}
                         for l, n, d in RESULTS]}
    with io.open(os.path.join(OUT, 'predeploy_hub_gate_result.json'), 'w',
                 encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print('\nRESULT: PASS=%d FAIL=%d -> %s' %
          (n_pass, n_fail, 'BLOCK RELEASE' if n_fail else 'OK TO PROCEED'), flush=True)
    sys.exit(1 if n_fail else 0)


if __name__ == '__main__':
    main()
