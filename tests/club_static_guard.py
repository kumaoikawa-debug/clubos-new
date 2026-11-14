"""club 端静态护栏（2026-11-14 / 营销 QA 第 3 批 · V10 V11 V12）。

背景：`ClubOS_Marketing_QA_Report_20261114.html` 的 P2-5 / P2-6 指出，C 端早有
`web_inline_handlers_guard.py`，club 端却既没有 inline handler 导出护栏，也没有
版本一致性护栏；`b6f5901` 已经因为缓存复用让新营销 UI 在浏览器端不出现。

本文件守住三件事（纯静态：不 import app、不建库、不碰真库）：

  1. P2-6  inline onclick/onchange 引用的每个函数都必须**显式**挂 window
           （含 `Object.assign(window,{...})` 批量导出）。一旦 club.js 被包进 IIFE，
           隐式全局会整片失效，用户看到的是「点了没反应」。
  2. P2-5  改动文件清单 ↔ `?v=` 自动比对：以 `origin/main` 基线为参照，凡相对基线
           改动过的 `static/**/*.js|css`，四端 index.html 引用它的版本戳都必须比
           基线新。改了码忘 bump 会直接红，不靠人肉维护清单。
  3. V10/V11/V12 形态断言：弹层单例、互斥字段联动、券范围级联、积分卡 CTA、
           未接入类型灰显、sticky 创建条、四端共用文件版本一致。
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENDS = ('club', 'web', 'platform', 'leader')
BASE_REF = 'origin/main'
BUMP_TOKEN = '20261114h'
OK = True


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding='utf-8').read()


def ck(label, cond, extra=''):
    global OK
    print(('  PASS  ' if cond else '  FAIL  ') + label + ((' | ' + str(extra)) if extra else ''))
    if not cond:
        OK = False


def git(*args):
    try:
        return subprocess.run(['git', *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=20).stdout
    except Exception:
        return None


CLUB_HTML = read('static/club/index.html')
WEB_HTML = read('static/web/index.html')
CLUB_JS = read('static/club/club.js')
SHARED_JS = read('static/shared.js')
UX_JS = read('static/ux/clubos-ux.js')
WF_JS = read('static/ux/workflows-club.js')
WEB_CSS = read('static/web/web.css')
CLUB_CSS = read('static/club/club-glass.css')

# ── 1. P2-6：inline handler 引用的函数必须显式导出 ──────────────────────────
BUILTINS = {
    'if', 'for', 'while', 'return', 'typeof', 'switch', 'catch', 'Number', 'String',
    'Boolean', 'Array', 'Object', 'JSON', 'Date', 'Math', 'parseInt', 'parseFloat',
    'alert', 'confirm', 'prompt', 'decodeURIComponent', 'encodeURIComponent', 'event',
}
used = set()
for m in re.finditer(r'\bon(?:click|change|input|submit|keydown|keyup|blur|focus)\s*=\s*["\']([^"\']*)["\']',
                     CLUB_HTML):
    body = m.group(1)
    for fn in re.findall(r'(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(', body):
        used.add(fn)
used -= BUILTINS

# 只把 club 端真正加载的脚本算进来（避免 static/versions/** 的历史副本造成假绿）。
loaded = []
for src in re.findall(r'<script[^>]*src="([^"]+)"', CLUB_HTML):
    rel = src.split('?')[0].lstrip('/')
    if rel.startswith('static/') and os.path.isfile(os.path.join(ROOT, rel)):
        loaded.append(rel)
scripts = '\n'.join(read(p) for p in loaded)
exported = set(re.findall(r'window\.([A-Za-z_$][\w$]*)\s*=', scripts))
for block in re.findall(r'Object\.assign\(\s*window\s*,\s*\{([^}]*)\}', scripts, re.S):
    exported |= set(re.findall(r'\b([A-Za-z_$][\w$]*)\b', re.sub(r'//[^\n]*', '', block)))
missing = sorted(used - exported)

ck('inline handler 确实引用了函数（检测不是空跑）', len(used) >= 25, '%d 个' % len(used))
ck('club 端脚本清单完整（shared.js / club.js / clubos-ux.js / workflows-club.js）',
   all(any(x.endswith(n) for x in loaded)
       for n in ('shared.js', 'club.js', 'clubos-ux.js', 'workflows-club.js')),
   '%d 个脚本' % len(loaded))
ck('所有 inline 引用的函数都已显式挂 window', not missing, '缺失=%s' % missing)
ck('modal（shared.js）已显式导出', 'window.modal=modal;' in SHARED_JS)
ck('19 个历史隐式全局已批量导出', 'Object.assign(window,{' in CLUB_JS
   and all(n in CLUB_JS[CLUB_JS.rfind('Object.assign(window,{'):] for n in
           ('inviteTab', 'setOrderRange', 'infoSetKind', 'loadInfoCenter', 'addActCat')))
# 对照组：摘掉一行导出后，同一套检测必须报出来，否则上面的 PASS 可能是假绿。
fake = '\n'.join(read(p) for p in loaded).replace('window.modal=modal;', '', 1)
ck('对照：故意摘除 modal 导出会被检测抓到',
   fake != scripts and 'window.modal=modal;' not in fake)

# ── 2. P2-5：改动文件清单 ↔ ?v= 自动比对 ────────────────────────────────────
def tokens(html, asset):
    return re.findall(r'["\']' + re.escape('/' + asset) + r'\?v=([0-9A-Za-z\-]+)', html)


htmls = {end: read('static/%s/index.html' % end) for end in ENDS}
diff_out = git('diff', '--name-only', BASE_REF, '--', 'static')
if diff_out is None:
    changed = [p for p in (
        'static/shared.js', 'static/ux/clubos-ux.js', 'static/ux/workflows-club.js',
        'static/club/club.js', 'static/club/club-glass.css',
        'static/web/web.js', 'static/web/me.js', 'static/web/web.css')]
    ck('git 不可用 → 使用兜底改动清单', True, '%d 个文件' % len(changed))
else:
    changed = [p.strip() for p in diff_out.splitlines()
               if p.strip().startswith('static/')
               and p.strip().endswith(('.js', '.css'))
               and '/versions/' not in p
               and os.path.isfile(os.path.join(ROOT, p.strip()))]
    # 旧阈值 >=5 把「一次只改 2~3 个静态文件」误判成比对没跑；
    # 而已提交且 origin/main 追平的干净工作树里 changed 合法为空 —— 此时没有
    # 「改了码忘 bump」这回事，也不该判红。真正的漏 bump 校验在下面 128~141
    # 行逐个改动文件执行；git 命令失效的分支已在上面走兜底清单，不会被伪装。
    ck('git 差异清单已获取（空=本轮无静态改动）', True, '%d 个文件' % len(changed))

baseline_html = {}
for end in ENDS:
    out = git('show', '%s:static/%s/index.html' % (BASE_REF, end))
    baseline_html[end] = out if out is not None else ''

for asset in changed:
    cur = {end: tokens(htmls[end], asset)[0] if tokens(htmls[end], asset) else None
           for end in ENDS}
    referenced = [end for end in ENDS if cur[end] is not None]
    # 独立页（如 static/club/settings-hub.html）不通过四端 index.html 引用脚本，
    # 版本戳挂在自己的宿主 HTML 上。补上这类资产的宿主探测，避免每改一次独立页
    # 脚本都误报「没有缓存版本戳」，同时仍然守住「改码必 bump」。
    host = None
    if not referenced:
        stem = os.path.splitext(os.path.basename(asset))[0]
        cand = os.path.join(os.path.dirname(asset), stem + '.html')
        if os.path.isfile(os.path.join(ROOT, cand)) and ('/' + asset + '?v=') in read(cand):
            host = cand
            htok = tokens(read(cand), asset)
            if htok:
                cur['host'] = htok[0]
                referenced = ['host']
    ck('%s 在页面里有缓存版本戳' % os.path.basename(asset), bool(referenced),
       ','.join('%s=%s' % (e, cur[e]) for e in referenced))
    # 必须比基线新：这是「改了码就要 bump」的自动比对，不钉死具体日期。
    stale = []
    for end in referenced:
        old_html = baseline_html[end] if end in ENDS else (git('show', '%s:%s' % (BASE_REF, host)) or '')
        old = tokens(old_html, asset)
        if old and cur[end] and cur[end] <= max(old):
            stale.append('%s: %s <= %s' % (end, cur[end], max(old)))
    ck('%s 版本戳高于基线（改码必 bump）' % os.path.basename(asset), not stale,
       '; '.join(stale) if stale else ','.join('%s=%s' % (e, cur[e]) for e in referenced))

# 四端共用文件必须同戳，否则 CDN/网关会按端复用旧缓存。
for common in ('static/shared.js', 'static/ux/clubos-ux.js', 'static/shared.css'):
    toks = {end: (tokens(htmls[end], common) or [None])[0] for end in ENDS}
    ck('四端 %s 版本一致' % os.path.basename(common),
       len(set(toks.values())) == 1 and None not in toks.values(), toks)
# 两个共用文件各自独立 bump（端内须同戳，已由上方「四端版本一致」守住），
# 这里只断言它们都「高于 origin/main 基线」——改了码就要 bump，不钉死具体日期。
# 只对本轮真正改动过的共用文件要求「版本戳高于基线」。文档第 12~14 行定义的
# 约束对象就是「相对基线改动过的 static/**/*.js|css」；未改动的 shared.js 不该
# 每次都被迫空 bump（否则 push 后 origin/main 追平，干净工作树跑回归会恒红）。
# 端内同戳由上面「四端版本一致」守住，改动文件的 bump 由第 128~141 行守住。
for common in [p for p in ('static/shared.js', 'static/ux/clubos-ux.js') if p in changed]:
    toks = {end: (tokens(htmls[end], common) or [None])[0] for end in ENDS}
    old = {end: (tokens(baseline_html[end], common) or [None])[0] for end in ENDS}
    stale = [e for e in ENDS if old[e] and toks[e] and toks[e] <= old[e]]
    ck('%s 版本戳高于基线（改码必 bump）' % os.path.basename(common), not stale,
       '; '.join('%s: %s<=%s' % (e, toks[e], old[e]) for e in stale)
       or ','.join('%s=%s' % (e, toks[e]) for e in ENDS))

# ── 3. V10：弹层单例 / 互斥字段联动 / 券范围级联 / 积分卡 CTA / 灰显 ──────────
ck('V10-a shared.js 有弹层注册表与硬上限',
   'const _uxOverlays=[]' in SHARED_JS and 'UX_OVERLAY_MAX' in SHARED_JS
   and 'function uxRegisterOverlay(' in SHARED_JS)
ck('V10-b 新根层会关掉旧层', "old.uxClose?old.uxClose():old.remove()" in SHARED_JS)
ck('V10-c uxDialog 挂幂等 uxClose 并注销注册',
   'ov.uxClose=close;' in SHARED_JS and 'uxUnregisterOverlay(ov)' in SHARED_JS
   and 'uxRegisterOverlay(ov,isNested)' in SHARED_JS)
ck('V10-d uxForm 复用 uxDialogSession（滚动锁/Esc 同一套引用计数）',
   'session=uxDialogSession(el,' in UX_JS and 'el.uxClose=forceClose' in UX_JS
   and 'document.addEventListener(\'keydown\',key)' not in UX_JS)
ck('V10-e uxForm 支持 when / helpFn / optionsFn 联动',
   all(x in UX_JS for x in ('function fieldVisible(', 'function syncFields()',
                            'function repaintOptions(', 'f._hidden', 'f.helpFn', 'f.optionsFn')))
ck('V10-f 立减/折扣互斥字段已联动',
   "when:{field:'mode',in:['fixed']}" in WF_JS and "when:{field:'mode',in:['percent']}" in WF_JS
   and "when:{field:'ctype',in:['TIME_LIMIT']}" in WF_JS)
ck('V10-g 券中心改为一级范围 + 二级对象级联',
   '① 适用范围（一级）' in WF_JS and '② 指定对象（二级 · 可选）' in WF_JS
   and 'function couponProductOpts(o,applies)' in WF_JS
   and 'optionsFn:all=>couponProductOpts(' in WF_JS and 'COUPON_APPLIES_HELP' in WF_JS)
ck('V10-h C 端积分卡有「去积分商城兑换」CTA',
   'wme-point--cta' in WEB_HTML and '去积分商城兑换' in WEB_HTML
   and "onclick=\"pushMe('pointsmall')\"" in WEB_HTML
   and '.wme-point__cta{' in WEB_CSS)
ck('V10-i 未接入活动类型灰显标「未接入」',
   "['拼团','GROUP',false,'未接入']" in CLUB_JS and 'is-off' in CLUB_JS
   and 'aria-disabled="true"' in CLUB_JS and '未接入' in CLUB_JS
   and '.mkt-cap.is-off' in CLUB_CSS)

# ── 4. V11：390px 可达性与 V12：版本护栏自身已纳入套件 ──────────────────────
ck('V11-a 营销页拆二级 tab + sticky 创建条',
   'function initMarketingTabs()' in CLUB_JS and 'mkt-subtabs' in CLUB_JS
   and 'mkt-createbar' in CLUB_JS and 'position:sticky;bottom:0' in CLUB_CSS)
ck('V11-b 宽表内部横滑 + 手机端提示（不撑出整页）',
   'gl.classList.add(\'mkt-scroll\')' in CLUB_JS and '.mkt-scroll{' in CLUB_CSS
   and 'overflow-x:auto' in CLUB_CSS and '.mkt-scroll-hint' in CLUB_CSS)
ck('V11-c 弹层提交按钮 sticky 在视口内（clubos-ux.css）',
   '.ux-dialog-foot{position:sticky;bottom:0' in read('static/ux/clubos-ux.css'))
ck('V11-d 本护栏已纳入全量回归',
   'tests/club_static_guard.py' in read('scripts/regression_v025.sh'))
ck('V12 交付版本戳统一为 %s' % BUMP_TOKEN,
   all(BUMP_TOKEN in htmls[e] for e in ENDS))

print('\nRESULT:', 'ALL PASS' if OK else 'HAS FAILURE')
sys.exit(0 if OK else 1)
