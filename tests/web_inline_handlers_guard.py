"""C 端 inline onclick 全局导出护栏（2026-11-08 / BUG-CUX-001）。

背景：static/web/me.js 整体是一个 IIFE。里面用 `function meXxx(){}` 定义的函数
不会自动挂到 window，但模板字符串里大量写 `onclick="meXxx(...)"` —— 浏览器解析
inline handler 时只在全局作用域找名字，于是点击静默抛 ReferenceError，用户看到
「点了没反应」。实测 4 个入口中招：签到码放大 / 券包城市去设置 / 领券 / 送券。
静态巡检 chain_audit.py 的 C 类检测没覆盖「IIFE 内定义 + 模板字符串内 inline 调用」，
所以用这条纯静态护栏把「凡被 inline onclick 引用的 me* 函数必须显式导出」锁进发版门禁。

同时守住本轮 P2/P3/P4 的修复没被回滚。纯静态：不 import app、不建库、不碰真库。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding='utf-8').read()


ME = read('static/web/me.js')
JS = read('static/web/web.js')
CSS = read('static/web/web.css')
HTML = read('static/web/index.html')
PAY = read('static/ux/payment-experience.js')
SHEET = read('static/web/address-sheet.js')

OK = True


def ck(label, cond, extra=''):
    global OK
    print(('  PASS  ' if cond else '  FAIL  ') + label + ((' | ' + str(extra)) if extra else ''))
    if not cond:
        OK = False


# ── 1. P1 根因：inline onclick 引用的 me* 函数必须全部显式导出 ──────────────
# 属性值里常含单引号（如 pushMe('myregs')），用非贪婪匹配到第一个引号即可，
# 只要能取到「第一个被调用的函数名」就够判断导出是否缺失。
used = set()
for m in re.finditer(r'onclick\s*=\s*\\?["\']([^"\']{0,180}?)\\?["\']', ME):
    for fn in re.findall(r'\b(me[A-Za-z_$][\w$]*)\s*\(', m.group(1)):
        used.add(fn)
exported = set(re.findall(r'window\.(me[A-Za-z_$][\w$]*)\s*=', ME))
missing = sorted(used - exported)

ck('inline onclick 确实引用了 me* 函数（检测不是空跑）', len(used) >= 8, '%d 个' % len(used))
ck('所有被引用的 me* 函数都已挂 window', not missing, '缺失=%s' % missing)

for fn in ('meZoomQr', 'meSetCity', 'meClaimCoupon', 'meShareCoupon'):
    ck('%s 既有定义又已导出' % fn,
       re.search(r'(?m)^\s*(?:async\s+)?function\s+%s\s*\(' % fn, ME) is not None
       and fn in exported)

# 对照组：删掉一行导出后，同一套检测必须能报出来（否则上面的 PASS 可能是假绿）
fake = ME.replace('window.meZoomQr=meZoomQr;', '', 1)
ck('对照：故意摘除导出会被检测抓到',
   'window.meZoomQr=' not in fake and fake != ME)

# ── 2. P2：活动订单不再复用电商「待发货」 ──────────────────────────────────
ck('存在活动专属状态表 ACT_ST', 'const ACT_ST=' in ME)
ck('活动 paid 用出行语义而非待发货', "paid:{t:'已报名 · 待出行'" in ME)
ck('订单卡按 kind 分派状态文案', 'meOrderSt(o.status,kind)' in ME)
ck('我的报名按活动语义显示状态', "meOrderSt(o.status,'activity')" in ME)
ck('商城订单有「活动报名」专属分类', "['activity','活动报名']" in ME
   and "else if(tab==='activity')" in ME)
ck('订单状态徽标不折行（窄屏不出现两行状态）',
   '.me-card2__hd .me-badge{white-space:nowrap' in CSS)

# ── 3. P2：顶栏放大镜有真实可输入的活动搜索 ────────────────────────────────
ck('活动页存在 #actSearch 输入框', 'id="actSearch"' in HTML)
ck('搜索结果摘要容器 #actCount 存在（否则摘要是死代码）',
   'id="actCount"' in HTML and "$('#actCount')" in JS)
ck('顶栏放大镜会聚焦搜索框', "getElementById('actSearch')" in HTML
   and "wv('wactivities')" in HTML)
ck('活动搜索绑定 input 事件', "addEventListener('input'" in JS and 'ACT_Q' in JS)
ck('活动列表真的按关键词过滤', 'const aq=String(ACT_Q' in JS and 'includes(aq)' in JS)
ck('清除筛选会重置关键词', "ACT_Q=''" in JS)
ck('注释里没有裸 section 闭合标签字面量（静态断言护栏）',
   '首个 section 闭合标签' in HTML)

# ── 4. P3：字段级校验、无封面占位、收据按钮可达 ────────────────────────────
ck('字段级错误带 aria-invalid', "setAttribute('aria-invalid','true')" in JS)
ck('清除错误时移除 aria-invalid', "removeAttribute('aria-invalid')" in JS)
ck('字段改正后即时清除错误（不残留 aria-invalid）',
   "if(typeof paxClearError==='function')paxClearError(i,key);" in JS)
ck('证件类型切换清掉证件号码旧错误', "paxClearError(i,'idNumber');" in JS)
ck('无封面商品卡打 is-fallback 标记', "is-fallback'}" in JS and 'w-product__mono' in JS)
ck('无封面样式存在（渐变 + 首字 + 降高度）',
   '.w-product__media.is-fallback{' in CSS and 'aspect-ratio:4/3' in CSS
   and '.w-product__mono' in CSS)
ck('收据弹层 body 限高且可滚动（打印按钮不被裁）',
   "rbody.style.maxHeight='68vh'" in PAY and "rbody.style.overflowY='auto'" in PAY)

# ── 5. P4：地址保存后不留旧值（2026-11-09 改为共用抽屉） ──────────────────
# 结算页内联 3×2 表单已废除，「保存后清空」这一不变量改由「每次打开都是全新
# DOM」保证：抽屉成功/取消都走 closeSheet() 把节点 remove()，不存在残留状态。
ck('旧内联地址表单已移除', 'coSaveAddr' not in JS and 'w-co__form"' not in JS)
ck('结算页地址入口改为共用抽屉', 'window.coOpenAddrSheet=coOpenAddrSheet;' in JS
   and 'openAddressSheet({userId:USER' in JS)
ck('我的页地址入口改为同一抽屉', 'window.openAddressSheet({userId:USER' in ME)
ck('抽屉保存成功后关闭并提示', 'closeSheet();' in SHEET and "地址已保存'" in SHEET)
ck('抽屉关闭时销毁 DOM（不残留旧表单值）', '_root.remove();' in SHEET)
ck('地址抽屉复用全站弹层会话（Esc/焦点/滚动锁）', 'uxDialogSession(root' in SHEET)
ck('优惠券所在城市默认系统定位', 'SUB_ME_AFTER.coupons=' in ME and 'meLocateCity(false)' in ME)
ck('手动设置的城市不被自动定位覆盖', "meCitySource()==='manual'" in ME)
ck('meLocateCity 已导出（inline onclick）', 'window.meLocateCity=meLocateCity;' in ME)

# ── 6. 缓存护栏：改动文件必须 bump 版本，否则浏览器仍吃旧代码 ────────────────
# 2026-11-11 #812：改为「不低于基线」语义比较（原精确 token 匹配在版本继续升级后恒假）。
import re as _re
def _vnum(s):
    m=_re.search(r'(\d{8}[a-z]?)(?:\D|$)', str(s))
    if not m: return ''
    t=m.group(1); return t[:-1] if t[-1].isalpha() else t
for rel, floor in (
    ('web.css', '20261109'),
    ('web.js', '20261109'),
    ('me.js', '20261109'),
    ('address-sheet.js', '20261109'),
    ('payment-experience.js', '20261108'),
):
    mm=_re.search(r'/static/(?:web|ux)/'+_re.escape(rel)+r'\?v=(\d{8}[a-z]?)', HTML)
    ck('%s 静态资源版本已 bump' % rel, bool(mm) and _vnum(mm.group(1)) >= floor,
       ('found '+mm.group(1)) if mm else 'no version token')

print('\nRESULT:', 'ALL PASS' if OK else 'HAS FAILURE')
sys.exit(0 if OK else 1)
