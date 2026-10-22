"""详情风格家族（2026-10-22）验收：五套版式到齐 + 题材判定 + 稳定可复现 + 图片封顶闸门。

背景：2026-10-21 详情被统一成「极简画报」，用户反馈「所有活动详情都长成同一个样子」。
本测试守住四条底线：
  ① 五个家族都在，且**底色互不相同**（真的不是换名字）；
  ② 题材关键词有确定映射（观星→电影感、研学→杂志、颂钵→纸感、亲子→暖光、徒步→极简）；
  ③ 同一场活动永远同一套（可复现），只有明确要求「换风格」时才轮换；
  ④ promoHtml 出口对图片高度封顶（模型爱把竖图做成通栏，实测 1000px+）。
不联网、不调模型。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ai_engine as A   # noqa: E402

fails = []


def check(name, cond, extra=''):
    if cond:
        print(f'  ok   {name}')
    else:
        print(f'  FAIL {name} {extra}')
        fails.append(name)


print('[1] 家族齐备且真的不同')
order = A._DETAIL_STYLE_ORDER
check('5 个家族', len(order) == 5, order)
check('家族表与顺序表一致', set(order) == set(A._DETAIL_STYLES), list(A._DETAIL_STYLES))
bgs = {}
for k in order:
    st = A._DETAIL_STYLES[k]
    check(f'{k} 有 name/fit/spec', all(st.get(f) for f in ('name', 'fit', 'spec')))
    check(f'{k} spec 写了底色', '--bg' in st['spec'] or '底色' in st['spec'])
    check(f'{k} spec 写了禁止项', '**禁止**' in st['spec'])
    import re
    m = re.search(r'--bg:\s*(#[0-9A-Fa-f]{6})', st['spec'])
    bgs[k] = m.group(1).lower() if m else ''
check('五个家族底色互不相同', len(set(bgs.values())) == 5 and all(bgs.values()), bgs)

print('[2] 题材判定')
cases = [
    ('藏地寻秋·川西彩林观星三日', 'cinema'),
    ('游牧王朝的汉化之路：晋冀7天6晚人文研学', 'magazine'),
    ('兴福寺徒步 × 森林颂钵疗愈', 'paper'),
    ('周末亲子露营·山谷市集', 'warm'),
    ('2026藏地寻秋·川西3日轻徒步', 'minimal'),
]
for title, want in cases:
    got = A.pick_detail_style(source={'title': title})
    check(f'「{title[:14]}…」→ {want}', got == want, f'got={got}')

print('[3] 稳定与轮换')
t = '兴福寺徒步 × 森林颂钵疗愈'
runs = {A.pick_detail_style(source={'title': t}) for _ in range(5)}
check('同一标题五次同结果', len(runs) == 1, runs)
rot = [A.pick_detail_style(source={'title': t}, rotate=n) for n in range(5)]
check('rotate 0..4 覆盖五个家族', set(rot) == set(order), rot)
check('rotate 不重复', len(set(rot)) == 5, rot)
no_signal = {A.pick_detail_style(source={'title': f'第{i}场活动'}) for i in range(12)}
check('无信号标题会分散到多个家族', len(no_signal) >= 3, no_signal)

print('[4] 契约拼接')
c = A._promohtml_contract('paper')
check('含家族名', '纸感山野志' in c)
check('含用图密度', '用图密度' in c)
check('含图片封顶规则', '图片高度必须封顶' in c)
check('含留白规则', '留白只留一处' in c)
check('五个家族契约长度接近', len({len(A._promohtml_contract(k)) // 100 for k in order}) <= 2)

print('[5] 图片封顶闸门')
long_html = '<style>:root{--bg:#fff}</style><div>' + '<p>正文段落</p>' * 80 + '<img src="{media:img_01}" style="height:1100px"></div>'
detail = {'promoHtml': long_html}
A._finalize_promo_html(detail, {'img_01'})
out = detail['promoHtml']
check('合格稿保留', bool(out))
check('注入了封顶样式', 'clubos-img-cap' in out)
check('封顶值 620px', 'max-height:620px' in out)
again = A._cap_promo_imgs(out)
check('封顶幂等（不重复注入）', again.count('clubos-img-cap') == 1, again.count('clubos-img-cap'))
check('无图不注入', 'clubos-img-cap' not in A._cap_promo_imgs('<p>纯文字</p>'))
# 用图密度：可用 12 张、只放 1 张 → 判不合格（一图流敷衍稿）
thin = {'promoHtml': '<style>:root{--bg:#fff}</style>' + '<p>正文</p>' * 90 + '<img src="{{media:img_01}}">'}
A._finalize_promo_html(thin, {f'img_{i:02d}' for i in range(1, 13)})
check('一图流被拦下', thin['promoHtml'] == '')
dense = {'promoHtml': '<style>:root{--bg:#fff}</style>' + '<p>正文</p>' * 90 + ''.join(
    f'<img src="{{{{media:img_{i:02d}}}}}">' for i in range(1, 9))}
A._finalize_promo_html(dense, {f'img_{i:02d}' for i in range(1, 13)})
check('8 张图的稿件被采用', bool(dense['promoHtml']))

small = {'promoHtml': '<div>太短</div>'}
A._finalize_promo_html(small, set())
check('敷衍稿被清空回退 blocks', small['promoHtml'] == '')

print('[6] 单花括号 ref（真模型口径）')
# 2026-10-22 实坑：契约让模型写 <img src="{media:ref}"> 单花括号，而 _PROMO_PH 旧正则只认
# 双花括号 → 密度闸门把铺了 15 张图的真稿判成 refs=0/21 直接回退 blocks（act49 实测）。
# 判据必须与前端 shared.js resolveMediaRefsHtml 完全同口径：单/双花括号都认。
check('_PROMO_PH 认单花括号', A._PROMO_PH.search('<img src="{media:img_02}">') is not None)
check('_PROMO_PH 认双花括号', A._PROMO_PH.search('<img src="{{media:img_02}}">') is not None)
check('_PROMO_PH 认单引号', A._PROMO_PH.search("<img src='{media:img_02}'>") is not None)
single_dense = {'promoHtml': '<style>:root{--bg:#F7F3EA}</style>' + '<p>正文</p>' * 80 + ''.join(
    f"<img src='{{media:img_{i:02d}}}'>" if i % 2 else f'<img src="{{media:img_{i:02d}}}">'
    for i in range(1, 9))}
A._finalize_promo_html(single_dense, {f'img_{i:02d}' for i in range(1, 13)})
check('单花括号密稿被采用', bool(single_dense['promoHtml']))
single_thin = {'promoHtml': '<style>:root{--bg:#F7F3EA}</style>' + '<p>正文</p>' * 90 + "<img src='{media:img_01}'>"}
A._finalize_promo_html(single_thin, {f'img_{i:02d}' for i in range(1, 13)})
check('单花括号一图流仍被拦下', single_thin['promoHtml'] == '')
# 未知单花括号 ref 要被剥掉（与前端一致），不能漏删
d, dropped = A._drop_unknown_promo_refs("<img src='{media:img_99}'><img src='{media:img_02}'>", {'img_02'})
check('未知单花括号 ref 被剥', 'img_99' not in d and 'img_02' in d, dropped)

print()
if fails:
    print(f'FAILED {len(fails)}: {fails}')
    raise SystemExit(1)
print('ALL PASS detail_style_smoke')
