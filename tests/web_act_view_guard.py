"""回归测试：C 端从首页点进活动详情，必须真的看得见。

背景：openAct 把详情写进 #publicDetail，而 #publicDetail 属于 #wactivities ——
首页时那个 section 还是 display:none。openAct 只隐藏了 #activityList、从没切视图，
于是详情渲染得再完整也整段隐形：从首页 hero 的「即刻探索」、「本月精选」卡点进去，
用户看到的就是「点了没反应」。
从底部「活动」tab 先到列表、再点卡片反而是正常的 —— 所以这个 bug 只在首页路径复现，
也正因如此它躲过了此前所有验证（那些都在活动视图里操作）。

真机 DOM 行为由 agent-browser 探针验证；这里用静态断言守住「openAct 必须先把视图
切到 wactivities」这一步，避免有人加逻辑时又把它删掉。纯静态：不 import app、
不建库、绝不碰项目真库 clubos.db。
"""
import io, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = io.open(os.path.join(ROOT, 'static/web/web.js'), encoding='utf-8').read()
HTML = io.open(os.path.join(ROOT, 'static/web/index.html'), encoding='utf-8').read()

OK = True
def ck(label, cond, extra=''):
    global OK
    print(('  PASS  ' if cond else '  FAIL  ') + label + ((' | ' + str(extra)) if extra else ''))
    if not cond: OK = False

# ── 结构前提 ────────────────────────────────────────────────────────────────
# #publicDetail 与 #activityList 必须同在 #wactivities 里：一个负责让位、一个显示详情。
# 若把详情塞进 #activityList 内部，隐藏列表会连详情一起隐藏（另一种"看不见"）。
sec = HTML[HTML.index('id="wactivities"'):]
sec = sec[:sec.index('</section>')]
ck('#wactivities section 同时容纳列表与详情', 'id="activityList"' in sec and 'id="publicDetail"' in sec)
seg = sec[sec.index('id="activityList"'):sec.index('id="publicDetail"')]
ck('#publicDetail 不是 #activityList 的子节点（div 配平）',
   seg.count('<div') == seg.count('</div>'), 'div %d/%d' % (seg.count('<div'), seg.count('</div>')))

# ── 切视图这一步必须存在，且在写详情之前 ────────────────────────────────────
ck('存在纯视图切换函数 wviewShow', 'function wviewShow(' in JS)
wv_i = JS.index('function wv(')
ck('wv 复用 wviewShow（切视图只有一份实现）',
   'wviewShow(id,b)' in JS[wv_i:wv_i + 320])

oa_i = JS.index('async function openAct(')
head = JS[oa_i:oa_i + 320]
ck('openAct 开头就切视图', "wviewShow('wactivities')" in head)
ck('切视图发生在写 #publicDetail 之前',
   JS.index("wviewShow('wactivities')", oa_i) < JS.index('#publicDetail', oa_i))

# ── 三个首页入口都必须走 openAct（否则又是"点了没反应"）──────────────────────
# 窗口取到下一个顶层 function 之前，比按固定字符数截断可靠（heroSlide 的模板跨十几行）。
for fn, tag in [('heroSlide', 'hero「即刻探索」'), ('miniCard', '「本月精选」卡')]:
    i = JS.index('function %s(' % fn)
    nxt = JS.find('\nfunction ', i + 1)
    seg2 = JS[i:nxt if nxt > 0 else i + 2000]
    ck('%s 的入口调用 openAct' % tag, 'openAct(' in seg2, '%d 字符' % len(seg2))

print('\nRESULT:', 'ALL PASS' if OK else 'HAS FAILURE')
sys.exit(0 if OK else 1)
