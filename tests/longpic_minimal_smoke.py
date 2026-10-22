"""极简画报（2026-10-21）验收：主题存在性 + 用图密度下限 + 兜底渲染带主题类。

老板拿《星落彩林间·藏地川西寻秋（极简版）》当样张定的版式，落点是三处：
  ① longpic_template.THEMES['minimal'] + _CSS_MINIMAL（模板兜底路径）
  ② longpic._FREE_PROMPT 的极简画报段落（真模型主路径）
  ③ 用图密度下限 photo_floor_for —— 照片是这套版式的主角，写死 4 张时真模型只放 6 张
本测试只跑纯函数与本地渲染，不联网、不调模型。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import longpic                      # noqa: E402
import longpic_template as T        # noqa: E402

fails = []


def check(name, cond, extra=''):
    if cond:
        print(f'  ok   {name}')
    else:
        print(f'  FAIL {name} {extra}')
        fails.append(name)


print('[1] 主题与 CSS')
check('THEMES 有 minimal', 'minimal' in T.THEMES)
check('DEFAULT_THEME = minimal', T.DEFAULT_THEME == 'minimal', T.DEFAULT_THEME)
css_min = T.build_css('minimal')
css_night = T.build_css('night')
check('minimal 有覆盖层', '.rc--minimal' in css_min)
check('minimal 底色 #111312', '#111312' in css_min)
check('minimal 零圆角', 'border-radius:0' in css_min)
check('minimal 宋体标题', 'Songti SC' in css_min)
check('minimal 照片满幅出血', 'calc(100% + 112px)' in css_min)
check('night 不受影响（无 rc--minimal 选择器）', 'rc--minimal' not in css_night)

print('[2] 用图密度下限 min(N, max(8, round(0.6N)))')
for n, want in [(0, 0), (3, 3), (6, 6), (8, 8), (10, 8), (14, 8), (16, 10), (20, 12), (30, 18)]:
    got = longpic.photo_floor_for(n)
    check(f'photo_floor_for({n}) == {want}', got == want, f'got={got}')

print('[3] 提示词接线')
p = longpic._FREE_PROMPT
check('提示词有 __PHOTO_N__ 占位', '__PHOTO_N__' in p)
check('提示词有 __PHOTO_FLOOR__ 占位', '__PHOTO_FLOOR__' in p)
check('旧的「至少用 4 张」已移除', '至少用 4 张' not in p)
check('首屏要求顶部压暗（品牌行可读）', '纯白' in p and '黑渐变' in p)
check('无价格时写「价格待定」而不是留空', '价格待定' in p)

print('[4] 兜底渲染带主题类')
master = {'title': '测试活动', 'occurrences': [], 'fees': {}, 'leaders': []}
out = longpic._mock(master, {}, None)
check('_mock 产出 html', bool(out.get('html')))
check('根节点带 rc--minimal', 'rc--minimal' in out.get('html', ''))

print()
if fails:
    print(f'FAILED {len(fails)}: {fails}')
    raise SystemExit(1)
print('ALL PASS longpic_minimal_smoke')
