#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成详情（promoHtml）在 C 端不得横向溢出 / 字图错排（2026-10-26 用户截图）。

模型直出的 promoHtml 是任意 CSS：4 个大数字的 .stat-row{display:flex} 在 ~316px 的窄容器里
子项 min-width:auto 缩不下去 → 撑出右侧，iframe 内部能左右拖动、数字被裁成一串。
这里守住修复的关键形状（纯静态断言，不改任何文件）：
 ① promoHtmlDoc 的底座把 html 与 body 一起锁死横向、*{min-width:0}、媒体限宽；
 ② 承载 iframe 加 scrolling="no"（即便仍略宽也只能裁、不能拖）；
 ③ fitPromoDoc：只对真正溢出的横向 flex 行换行 + 同档大字号统一缩小；纯数字不拆行；
 ④ 四端引用的 shared.js 版本号一致（改版必升，防浏览器吃旧缓存）。
"""
import io, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(ROOT, 'static', 'shared.js'), encoding='utf-8').read()

FAILED = []


def ck(name, cond, detail=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (('   | ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILED.append(name)


print('── promoHtml 响应式护栏 ──')
# ① 底座
ck('promoHtmlDoc：html 与 body 一起 overflow-x:hidden', 'html,body{' in src and 'overflow-x:hidden' in src)
ck('promoHtmlDoc：锁 max-width:100%', 'max-width:100%' in src)
ck('promoHtmlDoc：*{min-width:0}（允许 flex 子项收缩）', 'box-sizing:border-box;min-width:0' in src)
ck('promoHtmlDoc：媒体元素 max-width:100%!important', 'img,figure,video,iframe,svg,table,pre{max-width:100%!important}' in src)
# ② iframe 不能拖
ck('mountPromoHtml：iframe 带 scrolling="no"', 'scrolling="no"' in src)
# ③ fitPromoDoc
ck('fitPromoDoc 已定义', 'function fitPromoDoc(' in src)
ck('溢出 flex 行会换行', "el.style.flexWrap='wrap'" in src)
ck('纵向 column 不按行处理', "indexOf('column')" in src)
ck('大字号统一缩小（同一行同档）', 'maxFs*0.9' in src and 'minRatio*0.96' in src)
ck('纯数字不拆行（防 3200 竖排）', '纯数字不拆' in src)
ck('归一后重新量高（refreshPromoIframe）', 'function refreshPromoIframe(' in src and 'fitPromoDoc(ifr)' in src)
ck('setupPromoIframe 走 refreshPromoIframe', 'refreshPromoIframe(ifr)' in src)

# ④ 四端 shared.js 版本一致
vers = set()
for end in ('web', 'club', 'leader', 'platform'):
    h = io.open(os.path.join(ROOT, 'static', end, 'index.html'), encoding='utf-8').read()
    m = re.search(r'shared\.js\?v=([0-9a-z]+)', h)
    if m:
        vers.add(m.group(1))
ck('四端 shared.js 版本号一致', len(vers) == 1 and vers != {''}, vers)

print('\nRESULT:', 'ALL PASS' if not FAILED else 'HAS FAILURE (%d)' % len(FAILED))
sys.exit(0 if not FAILED else 1)
