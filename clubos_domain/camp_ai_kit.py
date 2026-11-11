"""营地 v4.3 #814：资料图片视觉理解 + 可复用版式模板库。

背景（用户 2026-11-11 三张截图实证）
------------------------------------
旧链路里，老板上传的 10 张 JPG 参考资料只被 `document_parser.image_meta()` 取了宽高，
`camp_detail_ai.py` 的 prompt 里只有「可用图片数量」一个整数；线上又跑在 mock 模式，
于是生成结果必然是模板套话，照片对内容零贡献，更不可能成为 C 端配图。

本模块补两件事：

1. **本地视觉观察**：不依赖任何外部视觉模型（线上 mock 也能工作），用像素统计给每张图
   生成可读的中文画面描述、场景标签、主色、方向与主体焦点；这些描述会进入写作 prompt，
   也会作为 `media[].caption` 下发到 C 端，让「照片有没有被读」变成可验证的事实。
   有视觉模型时，原图字节仍会经 `ai_gateway.generate_json(images=...)` 送模型复核。
2. **版式模板**：把「段落骨架 + 每段配图版式」从硬编码里抽出来，做成可列举、可选择、
   可沉淀样本的模板库；模板 id 随生成结果落库，C 端按 layout 渲染不同版式。

硬约束：本地观察只描述「看得见的画面」，绝不推断价格 / 容量 / 规则；这些事实仍只来自
老板文本框与系统硬参数。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image

# ── 版式模板库 ────────────────────────────────────────────────────────────────
# layout: full=整版大图 / split=左图右文 / grid=多图网格 / text=纯文字
TEMPLATES: dict[str, dict] = {
    'classic': {
        'id': 'classic', 'name': '经典图文', 'desc': '概览大图 → 左图右文讲规格 → 多图讲体验 → 纯文字讲规则',
        'blueprint': [
            {'headline': '实地概览', 'layout': 'full', 'maxImages': 2},
            {'headline': '空间与规格', 'layout': 'split', 'maxImages': 1},
            {'headline': '使用体验', 'layout': 'grid', 'maxImages': 3},
            {'headline': '规则与提示', 'layout': 'text', 'maxImages': 0},
        ],
    },
    'magazine': {
        'id': 'magazine', 'name': '杂志大图', 'desc': '首屏整版大图，后续以双图拼排为主，适合风景好的营地',
        'blueprint': [
            {'headline': '一眼看见', 'layout': 'full', 'maxImages': 1},
            {'headline': '现场实景', 'layout': 'grid', 'maxImages': 3},
            {'headline': '空间与设施', 'layout': 'split', 'maxImages': 1},
            {'headline': '到店须知', 'layout': 'text', 'maxImages': 0},
        ],
    },
    'spec': {
        'id': 'spec', 'name': '参数优先', 'desc': '规格与设施靠前，配图克制，适合装备租赁与小站商品',
        'blueprint': [
            {'headline': '规格与配置', 'layout': 'split', 'maxImages': 1},
            {'headline': '现场实拍', 'layout': 'grid', 'maxImages': 3},
            {'headline': '使用说明', 'layout': 'text', 'maxImages': 0},
            {'headline': '租借与退改', 'layout': 'text', 'maxImages': 0},
        ],
    },
}
DEFAULT_TEMPLATE = 'classic'
LAYOUTS = {'full', 'split', 'grid', 'text'}


def get_template(tid: Any) -> dict:
    return TEMPLATES.get(str(tid or '').strip(), TEMPLATES[DEFAULT_TEMPLATE])


def list_templates() -> list[dict]:
    return [{'id': t['id'], 'name': t['name'], 'desc': t['desc'],
             'sections': [{'headline': b['headline'], 'layout': b['layout'],
                           'maxImages': b['maxImages']} for b in t['blueprint']]}
            for t in TEMPLATES.values()]


def template_prompt(tid: Any) -> str:
    t = get_template(tid)
    lines = [f"版式模板「{t['name']}」：{t['desc']}。",
             '请按下列段落顺序与标题组织 sections，并为每段挑最贴题的图片序号：']
    for i, b in enumerate(t['blueprint']):
        lines.append(f"  第{i + 1}段 headline 建议「{b['headline']}」，"
                     f"最多引 {b['maxImages']} 张图（0 表示纯文字）。")
    lines.append('段落标题可微调，但顺序与配图密度要贴合模板；正文只写资料里有的事实。')
    return '\n'.join(lines)


def apply_blueprint(sections: list, tid: Any, media_count: int) -> list[dict]:
    """给每段补 layout 并按模板配额裁剪 mediaRefs（旧数据也能安全重放）。"""
    bp = get_template(tid)['blueprint']
    out = []
    for i, s in enumerate(sections or []):
        if not isinstance(s, dict):
            continue
        spec = bp[i] if i < len(bp) else {'layout': 'text', 'maxImages': 0}
        layout = str(s.get('layout') or spec['layout'])
        if layout not in LAYOUTS:
            layout = spec['layout']
        refs = [int(x) for x in (s.get('mediaRefs') or [])
                if isinstance(x, (int, float)) and 0 <= int(x) < media_count][:max(0, int(spec['maxImages']) or 3)]
        d = dict(s)
        d['layout'] = layout
        d['mediaRefs'] = refs
        out.append(d)
    return out[:6]


# ── 本地视觉观察（纯 PIL，无外部模型依赖） ────────────────────────────────────
def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def auto_focal(im: Image.Image) -> tuple[float, float]:
    """与 #813 后端 `_camp_auto_focal` 同口径：梯度能量找主体，下半区加权避开大片天空。"""
    try:
        small = im.convert('L').resize((64, 64), Image.LANCZOS)
        px = list(small.getdata())
        row_e, col_e = [], []
        for y in range(64):
            row_e.append(sum(abs(px[y * 64 + x] - px[y * 64 + x + 1]) for x in range(63)) + 1.0)
        for x in range(64):
            col_e.append(sum(abs(px[y * 64 + x] - px[(y + 1) * 64 + x]) for y in range(63)) + 1.0)
        rw = [row_e[i] ** 2 * (1.0 + 0.55 * (i / 63.0)) for i in range(64)]
        cw = [col_e[i] ** 2 for i in range(64)]
        fy = sum(rw[i] * (i + 0.5) for i in range(64)) / sum(rw) / 64.0
        fx = sum(cw[i] * (i + 0.5) for i in range(64)) / sum(cw) / 64.0
        return (_clamp(fx, 0.2, 0.8), _clamp(fy, 0.2, 0.82))
    except Exception:
        return (0.5, 0.5)


def _scene(px: list, w: int, h: int) -> tuple[str, list[str], dict]:
    """从 64×64 RGB 像素里统计颜色分区、亮度与边缘，给出可读场景判断。"""
    n = float(w * h)
    blue = green = warm = pale = dark = 0
    upper_blue = lower_blue = 0.0
    sat_sum = val_sum = 0.0
    for i, (r, g, b) in enumerate(px):
        mx, mn = max(r, g, b), min(r, g, b)
        v = mx / 255.0
        s = 0.0 if mx == 0 else (mx - mn) / mx
        sat_sum += s
        val_sum += v
        upper = i < (w * h // 2)
        if v < 0.22:
            dark += 1
        elif s < 0.16 and v > 0.72:
            pale += 1
        elif b > r * 1.12 and b > g * 1.05 and b > 70:
            blue += 1
            if upper:
                upper_blue += 1
            else:
                lower_blue += 1
        elif g > r * 1.08 and g > b * 1.05:
            green += 1
        elif r > b * 1.18 and r > g * 1.05:
            warm += 1
    nb, ng, nw, np_, nd = blue / n, green / n, warm / n, pale / n, dark / n
    # 水平梯度均值：区分「开阔平缓的天空/水面」与「细节密集的树林/设施」
    lum = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in px]
    pairs = [(y, x) for y in range(0, h, 2) for x in range(w - 1)]
    edge = sum(abs(lum[y * w + x] - lum[y * w + x + 1]) for y, x in pairs) / max(1, len(pairs))
    stats = {'blue': round(nb, 3), 'green': round(ng, 3), 'warm': round(nw, 3),
             'pale': round(np_, 3), 'dark': round(nd, 3),
             'saturation': round(sat_sum / n, 3), 'brightness': round(val_sum / n, 3),
             'edge': round(edge, 2), 'upperBlue': round(upper_blue / n, 3),
             'lowerBlue': round(lower_blue / n, 3)}
    if nd > 0.55:
        return '夜景或弱光画面', ['夜景', '弱光'], stats
    if stats['upperBlue'] > 0.16 and stats['upperBlue'] > stats['lowerBlue'] * 1.15:
        return '开阔外景，上半部是大面积蓝天，下半部为地景', ['天空', '外景', '开阔'], stats
    if nb > 0.24 and stats['lowerBlue'] > 0.10:
        return '水岸或湖景，蓝色水面与天光占比较高', ['水面', '湖景'], stats
    if ng > 0.30:
        return '林地或草地，绿色植被是画面主体', ['植被', '林地', '草地'], stats
    if nw > 0.30:
        return '木构、土路或暖光场景，暖色占比高', ['暖色', '木构', '营地设施'], stats
    if np_ > 0.48 and edge > 6:
        return '浅底高对比画面，疑似设施特写或含文字的资料图', ['设施特写', '图文资料'], stats
    if np_ > 0.48:
        return '明亮浅色画面，主体偏建筑或装备', ['浅色', '装备'], stats
    return '常规外景照片，色彩与构图较均衡', ['实景'], stats


def describe_image(path: Any, *, source: str = 'reference', name: str = '') -> dict | None:
    """读一张图 → 中文画面描述 + 场景标签 + 主体焦点。失败返回 None，不抛给上层。"""
    try:
        p = Path(path)
        with Image.open(p) as _im:
            im = _im.convert('RGB')
            W, H = im.size
            small = im.resize((64, 64), Image.LANCZOS)
            px = list(small.getdata())
        if not px or W < 1 or H < 1:
            return None
        scene, tags, stats = _scene(px, 64, 64)
        fx, fy = auto_focal(im)
        orient = '横幅' if W >= H * 1.15 else ('竖幅' if H >= W * 1.15 else '方幅')
        color = ('冷色为主' if stats['blue'] > stats['warm'] else '暖色为主') if max(stats['blue'], stats['warm']) > 0.15 else '中性色调'
        texture = '细节丰富' if stats['edge'] > 8 else ('画面平缓' if stats['edge'] < 3 else '层次适中')
        caption = (f'{orient}照片（{W}×{H}）：{scene}；{color}、{texture}。'
                   f'建议取景焦点在画面 {round(fx * 100)}% / {round(fy * 100)}% 处。')
        # 纯色 / 过小 / 极端低信息量的图不适合做 C 端配图
        usable = W >= 320 and H >= 240 and stats['saturation'] > 0.03 and stats['brightness'] > 0.12
        return {'name': name or p.name, 'path': str(p), 'source': source,
                'width': W, 'height': H, 'caption': caption, 'tags': tags,
                'stats': stats, 'focal': {'x': round(fx, 4), 'y': round(fy, 4)},
                'usable': bool(usable)}
    except Exception:
        return None


def describe_paths(paths: list, *, source: str = 'reference', limit: int = 16) -> list[dict]:
    out = []
    for p in list(paths or [])[:limit]:
        d = describe_image(p, source=source)
        if d:
            out.append(d)
    return out


def observation_text(observations: list) -> str:
    """把画面观察写成写作模型能读的事实清单（不含任何推测的经营数据）。"""
    if not observations:
        return '（本次没有可用的照片资料）'
    lines = []
    for i, o in enumerate(observations):
        lines.append(f"[图 {i}] 来源：{'商品照片' if o.get('source') == 'gallery' else '上传资料'}｜"
                     f"文件：{o.get('name', '')}｜观察：{o.get('caption', '')}｜"
                     f"标签：{'、'.join(o.get('tags') or [])}")
    return '\n'.join(lines)


# ── 文本框事实抽取（只搬运老板写过的字，不编造） ──────────────────────────────
FACILITY_WORDS = ('独立卫浴', '卫生间', '淋浴', '热水', '空调', '充电桩', '电源', '取电', '水电',
                  '停车位', '停车场', '帐篷', '天幕', '木台', '木屋', '烧烤', '篝火', 'wifi', 'WiFi',
                  '冰箱', '桌椅', '照明', '储物柜', '宠物', '无障碍')


def _clean_rule(s: str) -> str:
    """去掉老板随手粘的格式噪声（序号 / 星号项目符 / 标签前缀），但保留 8*8 这类尺寸乘号。"""
    s = re.sub(r'^\s*\d+\s*[、.．)）]\s*', '', s)
    s = re.sub(r'^\s*预订说明\s*[:：]\s*', '', s)
    s = re.sub(r'^\s*[*＊]+\s*', '', s)
    s = re.sub(r'([:：])\s*[*＊]+\s*', r'\1', s)
    s = re.sub(r'\s+', ' ', s).strip(' ，,；;')
    return s[:180]


def extract_facts(text: Any) -> dict:
    """从老板的说明里抽出规则 / 尺寸 / 容量 / 设施；全部来自原文，不做推断。"""
    t = re.sub(r'\s+', ' ', str(text or ''))
    policies, specs, facilities, notes = [], [], [], []
    for raw in re.split(r'[。；;\n]+', t):
        s = raw.strip(' ，,')
        if len(s) < 2:
            continue
        if re.search(r'退订|取消|退款|违约|不退|改期|提前\s*\d+\s*天|违约金|定金|押金规则', s):
            label = '退改规则' if re.search(r'退订|取消|退款|违约|不退|改期|违约金', s) else '预订说明'
            policies.append({'label': label, 'text': _clean_rule(s)})
        m = re.search(r'\d+(?:\.\d+)?\s*[\*xX×]\s*\d+(?:\.\d+)?\s*(?:米|m|㎡|平米)?', s)
        if m and not any(x['label'] == '场地尺寸' for x in specs):
            specs.append({'label': '场地尺寸', 'value': m.group(0)})
        m2 = re.search(r'(?:最多|可)?(?:接待|可住|容纳)\s*[“"\']?([^，,。；;\s]{1,6})[”"\']?\s*(?:位|人)', s)
        if m2 and not any(x['label'] == '接待人数' for x in specs):
            specs.append({'label': '接待人数', 'value': m2.group(1) + ' 位'})
        for w in FACILITY_WORDS:
            if w in s and w not in facilities:
                facilities.append(w)
        if len(s) > 12 and not re.search(r'退订|取消|退款|违约', s):
            notes.append(s[:100])
    return {'policies': policies[:4], 'specs': specs[:6],
            'facilities': facilities[:8], 'notes': notes[:6], 'raw': t[:600]}
