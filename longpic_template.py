# -*- coding: utf-8 -*-
"""宣传长图排版模板（longpic 的「版式底座」）。

═══ 为什么要有这个文件（2026-10-08）═══
老板给了两份**标杆成品**（直接用大模型按 PPT 生成的长图）：
  · `兴福寺徒步 × 森林颂钵`（浅色纸感）
  · `鱼子西日照金山 × 唐卡绘制`（深色夜感）
并说：「让 clubos 学习这种排版方式，在 clubos 中直接利用大模型生成这种宣传长图，**避免乱排**。」

对照标杆与 ClubOS 旧实现（模型直出行内样式 HTML），差距的根因很清楚 ——
**标杆不是"模型排的"，而是一套手写模板**：
  · 一份固定 CSS（十来条类），两套配色主题；
  · 十来种固定组件（hero / 数字栏 / 参数行 / 时间轴 / 圆标卡 / 图组 / 胶囊 / 团队 / 深色收尾）；
  · 内容只需按组件填字段，版式永不出错。

而旧实现「把设计权整包交给模型」→ 每次都在重新发明版式，字号层级、间距、图片尺寸全凭运气，
这就是"乱排"。故本版把排版权**收回**：

    模型只输出**结构化内容 JSON** → 本文件渲染成 HTML（模板负责全部样式）。

安全收益同样明显：模型不再产出任意 HTML/属性，只能往**预置字段**里填**纯文本**，
注入面从「整段 HTML」缩到「若干字符串」（本文件统一 `html.escape`）。

设计数值取自标杆（414px 画布）等比换算到 **750px 画布**（公众号长图标准宽度，×1.8116）。
"""
from __future__ import annotations

import html as _html
import re
from typing import Any, Iterable

# ══════════════════════════════════════════════════════════════════════
# 一、主题（两套，取自两份标杆）
# ══════════════════════════════════════════════════════════════════════
THEMES: dict[str, dict[str, str]] = {
    # 浅色「纸感」：白天户外 / 森林 / 徒步 / 城市周边
    'paper': {
        'bg':        '#F7F4ED',   # 纸底（暖米白）
        'ink':       '#1F2420',   # 正文（近黑绿）
        'primary':   '#3E6B4E',   # 主色（深森林绿）
        'accent':    '#B56B3E',   # 点缀（赭石铜）
        'muted':     '#4A554A',   # 次要正文
        'faint':     '#8B9689',   # 注释 / 图注
        'line':      'rgba(31,36,32,.10)',
        'line2':     'rgba(31,36,32,.08)',
        'dark':      '#1F2420',   # 收尾深色底
        'on_dark':   '#F7F4ED',   # 深色上的文字
        'on_dark2':  '#A8C4A0',   # 深色上的主色（浅一档）
        'on_dark3':  '#8B9689',
        'chipbg':    'rgba(62,107,78,.08)',
        'chipbd':    'rgba(62,107,78,.22)',
        'ph':        '#E8E4DA',   # 照片占位底色
        'hero_fade': 'rgba(247,244,237,',   # hero 渐隐用的底色（带 alpha 前缀）
        'card':      'rgba(62,107,78,.05)',
    },
    # 深色「夜感」：日照金山 / 星空 / 雪山 / 高原 / 藏地 / 篝火
    'night': {
        'bg':        '#0B0F14',
        'ink':       '#F2F0EA',
        'primary':   '#F2C878',   # 暖金
        'accent':    '#C99A4E',
        'muted':     '#C8D3C4',
        'faint':     '#8B9689',
        'line':      'rgba(255,255,255,.12)',
        'line2':     'rgba(255,255,255,.08)',
        'dark':      '#12161C',
        'on_dark':   '#F2F0EA',
        'on_dark2':  '#F2C878',
        'on_dark3':  '#8B9689',
        'chipbg':    'rgba(242,200,120,.10)',
        'chipbd':    'rgba(242,200,120,.28)',
        'ph':        '#141B23',
        'hero_fade': 'rgba(11,15,20,',
        'card':      'rgba(255,255,255,.05)',
    },
}

DEFAULT_THEME = 'paper'

# ══════════════════════════════════════════════════════════════════════
# 二、样式表（一份固定 CSS，配色走 CSS 变量 —— 换主题只换变量）
# ══════════════════════════════════════════════════════════════════════
_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{background:%(bg)s}
.rc{width:750px;margin:0 auto;background:%(bg)s;color:%(ink)s;
  font-family:"Noto Sans SC","PingFang SC","Helvetica Neue",Arial,sans-serif;
  -webkit-font-smoothing:antialiased;overflow:hidden}
.rc .en{font-family:"Montserrat","Helvetica Neue",Arial,sans-serif}
.rc .serif{font-family:"Noto Serif SC","Songti SC",serif}
.rc img{display:block}
/* ★ 照片一律用 <img> 而不是 CSS background-image（2026-10-08 关键决定）：
   导出走 SVG foreignObject → SVG 转 img 时**不会加载任何外部资源**，
   所以 `static/channel-render.js` 的 inlineImages() 必须把图片内联成 data URI ——
   而它只处理 <img>。用 background-image 的话导出图会整块空白（"下方全白"的另一种形态）。
   固定高度 + object-fit:cover 的视觉结果与 background-size:cover 等价。 */
.ph{width:100%%;display:block;object-fit:cover;background:%(ph)s;border-radius:18px}

/* ---------- hero ---------- */
.hero{position:relative;height:1050px}
.hero-bg{position:absolute;inset:0;width:100%%;height:100%%;object-fit:cover;object-position:center 42%%}
.hero-shade{position:absolute;inset:0;background:linear-gradient(180deg,
  %(hero_fade)s.78) 0%%,%(hero_fade)s.20) 20%%,%(hero_fade)s0) 40%%,
  %(hero_fade)s0) 58%%,%(hero_fade)s.72) 86%%,%(bg)s 100%%)}
.hero-in{position:relative;z-index:2;height:100%%;display:flex;flex-direction:column;padding:50px 50px 60px}
.brandrow{display:flex;align-items:center;gap:16px}
.brandrow span{font-size:19px;letter-spacing:3.6px;color:%(primary)s;font-weight:600}
.hero-sp{flex:1}
.hero-chip{align-self:flex-start;font-size:20px;letter-spacing:2.8px;color:%(primary)s;
  border:1px solid %(chipbd)s;border-radius:999px;padding:9px 24px;margin-bottom:26px;
  background:%(hero_fade)s.6)}
.hero h1{font-size:66px;line-height:1.18;font-weight:900;letter-spacing:1.8px;color:%(ink)s;
  text-wrap:balance;
  text-shadow:0 2px 24px %(hero_fade)s.75),0 0 54px %(hero_fade)s.45)}
.hero h1 em{font-style:normal;color:%(primary)s}
.hero .sub{margin-top:22px;font-size:25px;color:%(muted)s;letter-spacing:1px;line-height:1.7}
.hero .meta{margin-top:38px;display:flex;align-items:flex-start;gap:22px}
.hero .date{font-size:27px;font-weight:700;letter-spacing:2px;color:#fff;background:%(primary)s;
  border-radius:10px;padding:12px 26px;white-space:nowrap}
.hero .limit{font-size:21px;color:%(muted)s;letter-spacing:1.6px;line-height:1.6;padding-top:4px}
.hero .en-loc{margin-top:24px;font-size:16px;letter-spacing:5.4px;color:%(faint)s}

/* ---------- section frame ---------- */
.sec{padding:78px 50px 0}
.s-eyebrow{display:flex;align-items:center;gap:18px;margin-bottom:22px}
.s-eyebrow .no{font-size:20px;letter-spacing:4.4px;color:%(accent)s;font-weight:600;white-space:nowrap}
.s-eyebrow .line{flex:1;height:1px;background:%(line)s}
.s-title{font-size:45px;font-weight:800;letter-spacing:1.4px;line-height:1.3;color:%(ink)s;
  text-wrap:balance}
.s-lead{margin-top:20px;font-size:26px;line-height:1.85;color:%(muted)s}
.s-lead b{color:%(primary)s;font-weight:700}
.s-note{margin-top:18px;font-size:22px;line-height:1.8;color:%(faint)s}

/* ---------- stats（数字栏） ---------- */
.stats{display:flex;margin-top:40px}
.stat{flex:1}
.stat+.stat{border-left:1px solid %(line)s;padding-left:28px}
.stat .v{font-size:38px;font-weight:800;color:%(primary)s;letter-spacing:.9px;line-height:1.3}
.stat .k{margin-top:7px;font-size:19px;color:%(faint)s;letter-spacing:2.2px}

/* ---------- photos ---------- */
.photo-full{height:455px;margin-top:38px}
.photo-tall{height:600px;margin-top:38px}
.photo-wide{height:400px;margin-top:38px}
.duo{display:flex;gap:18px;margin-top:38px}
.duo .ph{flex:1;min-width:0;height:470px}
.trio{display:flex;gap:18px;margin-top:18px}
.trio .ph{flex:1;min-width:0;height:300px}
.cap{margin-top:14px;font-size:18px;letter-spacing:3.6px;color:%(faint)s}

/* ---------- params（左右分栏参数行） ---------- */
.params{margin-top:34px}
.params .r{display:flex;gap:22px;align-items:flex-start;padding:25px 0}
.params .r+.r{border-top:1px solid %(line2)s}
.params .d{flex:none;width:90px;font-size:22px;font-weight:700;color:%(accent)s;letter-spacing:1.8px;padding-top:3px}
.params .p{flex:1;font-size:24px;line-height:1.7;color:%(ink)s}
.params .p i{font-style:normal;color:%(faint)s;font-size:20px}
.chips{display:flex;flex-wrap:wrap;gap:14px;margin-top:28px}
.chip{font-size:19px;letter-spacing:1.8px;color:%(primary)s;background:%(chipbg)s;
  border:1px solid %(chipbd)s;border-radius:999px;padding:9px 20px}

/* ---------- timeline ---------- */
.tl{margin-top:34px;position:relative;padding-left:32px}
.tl::before{content:'';position:absolute;left:5px;top:14px;bottom:14px;width:1px;background:%(line)s}
.tl .t{position:relative;padding:13px 0;display:flex;gap:26px;align-items:baseline}
.tl .t::before{content:'';position:absolute;left:-32px;top:25px;width:12px;height:12px;border-radius:50%%;
  background:%(bg)s;border:2px solid %(accent)s}
.tl .time{flex:none;width:156px;font-size:21px;font-weight:700;color:%(accent)s;letter-spacing:1px}
.tl .what{font-size:24px;line-height:1.65;color:%(ink)s}
.tl .what i{font-style:normal;font-size:20px;color:%(faint)s}

/* ---------- steps（圆形序号卡） ---------- */
.steps{margin-top:38px;display:flex;flex-direction:column;gap:30px}
.step{display:flex;gap:26px;align-items:flex-start}
.step-ico{flex:none;width:76px;height:76px;border-radius:50%%;background:%(primary)s;color:%(bg)s;
  display:flex;align-items:center;justify-content:center;font-size:27px;font-weight:800}
.step-body{flex:1}
.step-title{font-size:29px;font-weight:800;color:%(ink)s;letter-spacing:.9px}
.step-desc{margin-top:9px;font-size:23px;line-height:1.75;color:%(muted)s}
.step-tag{display:inline-block;margin-top:13px;font-size:18px;letter-spacing:1.8px;color:%(accent)s;
  border:1px solid %(chipbd)s;border-radius:999px;padding:6px 17px}

/* ---------- team ---------- */
.team{margin-top:38px;display:flex;flex-wrap:wrap;gap:22px}
.tm{flex:1 1 45%%;display:flex;gap:22px;align-items:center}
.tm .ava{width:101px;height:101px;border-radius:50%%;object-fit:cover;
  background:%(ph)s;flex:none;border:3px solid %(chipbd)s}
/* 没有人物照时的头像位：底色 + 姓氏首字，比一个空灰圆圈体面得多
   （实测模型会把风景照塞进头像位，那更像 bug；提示词也要求"没有人物照就别填 media"） */
.tm .ava-init{display:flex;align-items:center;justify-content:center;font-size:38px;font-weight:800;
  color:%(primary)s;background:%(chipbg)s;border:3px solid %(chipbd)s}
.tm .name{font-size:25px;font-weight:700;color:%(ink)s}
.tm .role{margin-top:4px;font-size:18px;color:%(faint)s;letter-spacing:1.4px;line-height:1.5}

/* ---------- quote ---------- */
.quote{margin-top:38px;padding:32px 38px;border-left:6px solid %(primary)s;background:%(card)s;border-radius:0 18px 18px 0}
.quote p{font-size:28px;line-height:1.7;font-weight:700;color:%(ink)s;letter-spacing:.6px}

/* ---------- signup（深色收尾） ---------- */
.signup{margin-top:84px;padding:72px 50px 60px;background:%(dark)s;color:%(on_dark)s}
.signup .en-roll{text-align:center;font-size:16px;letter-spacing:7.2px;color:%(on_dark2)s;margin-bottom:26px}
.signup h2{text-align:center;font-size:47px;font-weight:900;letter-spacing:2.7px;line-height:1.3}
.signup .en2{text-align:center;margin-top:12px;font-size:18px;letter-spacing:4.5px;color:%(on_dark3)s}
.form{margin-top:48px}
.form .f{display:flex;padding:24px 4px;align-items:baseline;gap:26px}
.form .f+.f{border-top:1px solid rgba(255,255,255,.12)}
.form .k{flex:none;width:104px;font-size:20px;letter-spacing:3.6px;color:%(on_dark2)s}
.form .v{font-size:24px;color:%(on_dark)s;line-height:1.6}
.form .v i{font-style:normal;font-size:20px;color:%(on_dark3)s}
.product{margin-top:48px;display:flex;gap:22px;align-items:stretch}
.product .ph{flex:1;min-width:0;height:340px;border-radius:22px}
.product .txt{flex:1.2;display:flex;flex-direction:column;justify-content:center}
.product .txt .price{font-size:51px;font-weight:900;color:%(on_dark2)s;line-height:1.1}
.product .txt .price small{font-size:24px;font-weight:600}
.product .txt .inc{margin-top:18px;font-size:22px;line-height:1.8;color:%(muted)s}
.qrwrap{margin-top:58px;display:flex;flex-direction:column;align-items:center}
.qrcard{width:283px;height:283px;border-radius:26px;background:#fff;padding:16px}
.qrcard img{width:100%%;height:100%%;object-fit:cover;border-radius:14px}
.qrwrap .scan{margin-top:26px;font-size:23px;letter-spacing:3.6px;color:%(on_dark2)s;font-weight:700}
.qrwrap .scan-tip{margin-top:9px;font-size:19px;color:%(on_dark3)s;letter-spacing:1.8px}
.foot{margin-top:62px;padding-top:44px;border-top:1px solid rgba(255,255,255,.14);
  display:flex;align-items:center;justify-content:center;gap:18px}
.foot span{font-size:17px;letter-spacing:4px;color:%(on_dark2)s}
.foot-en{margin-top:22px;text-align:center;font-size:15px;letter-spacing:5.4px;color:%(on_dark3)s;padding-bottom:12px}
"""


def build_css(theme: str) -> str:
    v = THEMES.get(theme) or THEMES[DEFAULT_THEME]
    return _CSS % v


# ══════════════════════════════════════════════════════════════════════
# 三、渲染工具
# ══════════════════════════════════════════════════════════════════════
def _e(x: Any) -> str:
    """转义为纯文本（模型只填文本，绝不产出标签）。"""
    return _html.escape(str(x if x is not None else ''), quote=True)


# ★ 唯一的富文本例外：`<b>…</b>`（2026-10-08）。
#   标杆里正文有「黑森林环抱」这种**局部加粗强调**，纯文本做不到。做法：
#   先整体转义（吃掉一切标签），再把 `&lt;b&gt;` / `&lt;/b&gt;` 还原成真标签 ——
#   于是模型只能获得这一个无语义的加粗能力，没有任何属性/事件可下手，注入面为零。
_RICH_B = re.compile(r'&lt;(/?)b&gt;', re.I)


def _rich(x: Any) -> str:
    return _RICH_B.sub(lambda m: '</b>' if m.group(1) else '<b>', _e(x))


def _nl2br(x: Any) -> str:
    """多行文本：支持换行分行 + `<b>` 局部加粗（转义之后再还原，安全）。"""
    return _rich(x).replace('\n', '<br>')


def _txt(x: Any) -> str:
    """纯文本，换行折成空格（用于会被 <em> 包夹的标题）。"""
    return ' '.join(str(x if x is not None else '').split())


def _lst(x: Any) -> list:
    if x is None:
        return []
    if isinstance(x, list):
        return [i for i in x if i not in (None, '', {})]
    return [x]


def _s(x: Any, key: str = '', default: str = '') -> str:
    if isinstance(x, dict):
        v = x.get(key, default)
    else:
        v = x if not key else default
    return '' if v is None else str(v)


def _img(ref: str, allowed: set[str], cls: str = 'ph') -> str:
    """照片块（`<img>` 形态，见上面 CSS 注释里说明的原因）。
    容器高度由 height 类决定（写在 CSS 里），图没载进来也不会塌成 0 高。"""
    r = str(ref or '').strip()
    if not r or (allowed and r not in allowed):
        return ''
    return '<img class="%s" src="{{media:%s}}" alt="">' % (_e(cls), _e(r))


def _pic(ref: str, allowed: set[str], size: str = 'full', caption: str = '') -> str:
    cls = {'full': 'photo-full', 'tall': 'photo-tall', 'wide': 'photo-wide'}.get(size, 'photo-full')
    box = _img(ref, allowed, cls)
    if not box:
        return ''
    out = box
    if str(caption or '').strip():
        out += '<div class="cap en">%s</div>' % _e(caption)
    return out


# ══════════════════════════════════════════════════════════════════════
# 四、内容块渲染（每个 block 的版式是固定的，模型只填内容）
# ══════════════════════════════════════════════════════════════════════
def _blk_stats(b: dict, allowed: set[str]) -> str:
    items = _lst(b.get('items'))[:4]
    cells = []
    for it in items:
        v, k = _s(it, 'v'), _s(it, 'k')
        if not (v or k):
            continue
        cells.append('<div class="stat"><div class="v">%s</div><div class="k">%s</div></div>'
                     % (_e(v), _e(k)))
    return '<div class="stats">%s</div>' % ''.join(cells) if cells else ''


def _blk_params(b: dict, allowed: set[str]) -> str:
    rows = []
    for it in _lst(b.get('items'))[:8]:
        d, p, note = _s(it, 'k') or _s(it, 'd'), _s(it, 'v'), _s(it, 'note')
        if not (d or p):
            continue
        tail = ' <i>· %s</i>' % _e(note) if note else ''
        rows.append('<div class="r"><div class="d">%s</div><div class="p">%s%s</div></div>'
                    % (_e(d), _nl2br(p), tail))
    return '<div class="params">%s</div>' % ''.join(rows) if rows else ''


def _blk_chips(b: dict, allowed: set[str]) -> str:
    items = [_s(x) if not isinstance(x, dict) else _s(x, 'text') for x in _lst(b.get('items'))[:8]]
    items = [i for i in items if i.strip()]
    return ('<div class="chips">%s</div>'
            % ''.join('<span class="chip">%s</span>' % _e(i) for i in items)) if items else ''


def _blk_photos(b: dict, allowed: set[str]) -> str:
    items = _lst(b.get('items'))
    if not items:
        return ''
    shots = []
    for it in items:
        if isinstance(it, dict):
            shots.append((_s(it, 'ref') or _s(it, 'media'), _s(it, 'caption'), _s(it, 'size') or 'full'))
        else:
            shots.append((_s(it), '', 'full'))
    shots = [s for s in shots if s[0].strip()]
    if not shots:
        return ''
    out = ''
    if len(shots) == 1:
        out = _pic(shots[0][0], allowed, shots[0][2], shots[0][1])
    elif len(shots) == 2:
        pair = ''.join(_img(r, allowed) for r, _, _ in shots)
        if pair.count('<img') >= 2:
            out = '<div class="duo">%s</div>' % pair
            if shots[0][1]:
                out += '<div class="cap en">%s</div>' % _e(shots[0][1])
        else:
            out = _pic(shots[0][0], allowed, 'full', shots[0][1]) or _pic(shots[1][0], allowed, 'full', shots[1][1])
    else:
        head = _pic(shots[0][0], allowed, 'full', shots[0][1])
        trio = ''.join(_img(r, allowed) for r, _, _ in shots[1:4])
        out = head + ('<div class="trio">%s</div>' % trio if trio.count('<img') >= 2 else '')
    return out


def _blk_timeline(b: dict, allowed: set[str]) -> str:
    rows = []
    for it in _lst(b.get('items'))[:12]:
        t, w, note = _s(it, 'time'), _s(it, 'what') or _s(it, 'text'), _s(it, 'note')
        if not (t or w):
            continue
        tail = ' <i>· %s</i>' % _e(note) if note else ''
        rows.append('<div class="t"><div class="time en">%s</div><div class="what">%s%s</div></div>'
                    % (_e(t), _nl2br(w), tail))
    return '<div class="tl">%s</div>' % ''.join(rows) if rows else ''


def _blk_steps(b: dict, allowed: set[str]) -> str:
    rows = []
    for i, it in enumerate(_lst(b.get('items'))[:6], 1):
        no = _s(it, 'no') or ('%02d' % i)
        title, desc, tag = _s(it, 'title'), _s(it, 'desc') or _s(it, 'text'), _s(it, 'tag')
        if not (title or desc):
            continue
        rows.append(
            '<div class="step"><div class="step-ico en">%s</div><div class="step-body">'
            '<div class="step-title">%s</div>%s%s</div></div>' % (
                _e(no), _e(title),
                '<div class="step-desc">%s</div>' % _nl2br(desc) if desc else '',
                '<div class="step-tag">%s</div>' % _e(tag) if tag else ''))
    return '<div class="steps">%s</div>' % ''.join(rows) if rows else ''


def _blk_team(b: dict, allowed: set[str]) -> str:
    rows = []
    for it in _lst(b.get('items'))[:8]:
        name, role, ref = _s(it, 'name'), _s(it, 'role'), _s(it, 'media') or _s(it, 'ref')
        if not name:
            continue
        ava = ('<img class="ava" src="{{media:%s}}" alt="">' % _e(ref)
               if ref and (not allowed or ref in allowed)
               else '<div class="ava ava-init">%s</div>' % _e(name[0]))
        rows.append('<div class="tm">%s<div><div class="name">%s</div><div class="role">%s</div></div></div>'
                    % (ava, _e(name), _e(role)))
    return '<div class="team">%s</div>' % ''.join(rows) if rows else ''


def _blk_quote(b: dict, allowed: set[str]) -> str:
    t = _s(b, 'text') or _s(b, 'v')
    return '<div class="quote"><p>%s</p></div>' % _nl2br(t) if t.strip() else ''


_BLOCK_RENDER = {
    'stats': _blk_stats,
    'params': _blk_params,
    'chips': _blk_chips,
    'photos': _blk_photos,
    'timeline': _blk_timeline,
    'steps': _blk_steps,
    'team': _blk_team,
    'quote': _blk_quote,
}


def _render_blocks(blocks: Iterable, allowed: set[str]) -> str:
    out = []
    for b in _lst(blocks):
        if not isinstance(b, dict):
            continue
        fn = _BLOCK_RENDER.get(str(b.get('type') or '').strip().lower())
        if not fn:
            continue
        try:
            piece = fn(b, allowed)
        except Exception:
            piece = ''
        if piece:
            out.append(piece)
    return ''.join(out)


# ══════════════════════════════════════════════════════════════════════
# 五、整篇渲染
# ══════════════════════════════════════════════════════════════════════
def _render_hero(h: dict, allowed: set[str], cover_url: str | None) -> str:
    if not isinstance(h, dict):
        h = {}
    ref = _s(h, 'media') or _s(h, 'ref')
    bg = ''
    if ref and (not allowed or ref in allowed):
        bg = '{{media:%s}}' % _e(ref)
    elif cover_url:
        bg = _e(cover_url)
    bg_html = '<img class="hero-bg" src="%s" alt="">' % bg if bg else '<div class="hero-bg"></div>'

    brand = _s(h, 'brand')
    brand_html = '<div class="brandrow"><span class="en">%s</span></div>' % _e(brand) if brand else ''

    chip = _s(h, 'chip')
    chip_html = '<div class="hero-chip">%s</div>' % _e(chip) if chip else ''

    # 主标题：支持显式换行（`\n`）+ accent 高亮（accent 必须是标题里原样出现的一段）
    raw_title = str(h.get('title') or '')
    lines = [l for l in raw_title.split('\n')]
    lines = lines if any(l.strip() for l in lines) else [raw_title]
    esc_lines = [_rich(l.strip()) for l in lines if l.strip()]
    accent = _txt(h.get('accent'))
    if accent and esc_lines:
        a = _e(accent)
        for i, l in enumerate(esc_lines):
            if a in l:
                esc_lines[i] = l.replace(a, '<em>%s</em>' % a, 1)
                break
    title_html = '<br>'.join(esc_lines) or _rich(raw_title)

    sub = _s(h, 'sub')
    sub_html = '<div class="sub">%s</div>' % _nl2br(sub) if sub else ''

    date, meta = _s(h, 'date'), _s(h, 'meta') or _s(h, 'limit')
    meta_html = ''
    if date or meta:
        meta_html = '<div class="meta">%s%s</div>' % (
            '<div class="date en">%s</div>' % _e(date) if date else '',
            '<div class="limit">%s</div>' % _nl2br(meta) if meta else '')

    en_loc = _s(h, 'enLoc') or _s(h, 'en_loc')
    en_html = '<div class="en-loc en">%s</div>' % _e(en_loc) if en_loc else ''

    return ('<section class="hero">%s<div class="hero-shade"></div>'
            '<div class="hero-in">%s<div class="hero-sp"></div>%s<h1>%s</h1>%s%s%s</div></section>'
            % (bg_html, brand_html, chip_html, title_html, sub_html, meta_html, en_html))


def _render_section(s: dict, allowed: set[str]) -> str:
    if not isinstance(s, dict):
        return ''
    eyebrow = _s(s, 'eyebrow') or _s(s, 'no')
    eye_html = ('<div class="s-eyebrow"><span class="no en">%s</span><i class="line"></i></div>' % _e(eyebrow)
                if eyebrow else '')
    title = _s(s, 'title')
    title_html = '<div class="s-title">%s</div>' % _nl2br(title) if title else ''
    lead = _s(s, 'lead') or _s(s, 'text')
    lead_html = '<p class="s-lead">%s</p>' % _nl2br(lead) if lead else ''
    body = _render_blocks(s.get('blocks'), allowed)
    note = _s(s, 'note')
    note_html = '<p class="s-note">%s</p>' % _nl2br(note) if note else ''
    inner = eye_html + title_html + lead_html + body + note_html
    return '<section class="sec">%s</section>' % inner if inner.strip() else ''


def _render_signup(s: dict, allowed: set[str]) -> str:
    if not isinstance(s, dict):
        s = {}
    parts = []
    en_roll = _s(s, 'enRoll') or _s(s, 'en_roll') or 'JOIN US'
    parts.append('<div class="en-roll en">%s</div>' % _e(en_roll))
    title = _s(s, 'title')
    if title:
        parts.append('<h2>%s</h2>' % _nl2br(title))
    sub = _s(s, 'sub')
    if sub:
        parts.append('<div class="en2 en">%s</div>' % _e(sub))

    rows = []
    for it in _lst(s.get('form'))[:6]:
        k, v, note = _s(it, 'k'), _s(it, 'v'), _s(it, 'note')
        if not (k or v):
            continue
        tail = ' <i>· %s</i>' % _e(note) if note else ''
        rows.append('<div class="f"><div class="k">%s</div><div class="v">%s%s</div></div>'
                    % (_e(k), _nl2br(v), tail))
    if rows:
        parts.append('<div class="form">%s</div>' % ''.join(rows))

    prod = s.get('product')
    if isinstance(prod, dict):
        refs = [_s(r) for r in _lst(prod.get('media'))][:2]
        pics = ''.join(_img(r, allowed) for r in refs if r.strip())
        pics = pics if pics.count('<img') >= 2 else ''
        price, unit = _s(prod, 'price'), _s(prod, 'unit') or '/ 人'
        inc = [_s(i) for i in _lst(prod.get('inc'))]
        inc = [i for i in inc if i.strip()]
        if pics or price or inc:
            inc_html = '<div class="inc">%s</div>' % '<br>'.join('· ' + _e(i) for i in inc) if inc else ''
            txt = ''
            if price:
                txt = '<div class="price"><small>¥</small>%s <small>%s</small></div>' % (_e(price), _e(unit))
            prod_html = '<div class="product">%s<div class="txt">%s%s</div></div>' % (pics, txt, inc_html)
            if pics or txt:
                parts.append(prod_html)

    # 二维码：只有调用方真的提供了二维码 ref 才渲染（否则会留一块破图占位）
    if s.get('qr') and '__QR__' in allowed:
        parts.append('<div class="qrwrap"><div class="qrcard"><img src="{{media:__QR__}}" alt=""></div>'
                     '<div class="scan">扫码报名咨询</div>'
                     '<div class="scan-tip">名额有限 · 先到先得</div></div>')

    fb, fe = _s(s, 'footBrand'), _s(s, 'footEn')
    if fb:
        parts.append('<div class="foot"><span>%s</span></div>' % _e(fb))
    if fe:
        parts.append('<div class="foot-en en">%s</div>' % _e(fe))
    return '<section class="signup">%s</section>' % ''.join(parts)


def render(doc: dict, allowed_refs: set[str] | None = None, cover_url: str | None = None) -> str:
    """把模型产出的内容 JSON 渲染成完整长图 HTML（含样式）。

    doc = {title, theme, hero, sections[], signup}；所有字段容错，缺了就不渲染那一块。
    """
    doc = doc if isinstance(doc, dict) else {}
    allowed = set(allowed_refs or set())
    theme = str(doc.get('theme') or '').strip().lower()
    if theme not in THEMES:
        theme = DEFAULT_THEME

    body = [_render_hero(doc.get('hero'), allowed, cover_url)]
    for s in _lst(doc.get('sections')):
        body.append(_render_section(s, allowed))
    body.append(_render_signup(doc.get('signup'), allowed))

    html = ''.join(x for x in body if x)
    # 二维码：模板里占位，调用方若无二维码则整块已自带占位图（前端会替换 __QR__ 或按需删）
    return ('<style>%s</style><div class="rc">%s</div>' % (build_css(theme), html))


# ══════════════════════════════════════════════════════════════════════
# 六、质量自检（用于"模型产物是否达到标杆水准"的客观判断）
# ══════════════════════════════════════════════════════════════════════
def structure_report(html: str) -> dict[str, int]:
    """数一数渲染结果里各组件出现了几块 —— 用于验收「有没有乱排 / 是不是太单薄」。"""
    def n(pat: str) -> int:
        return len(re.findall(pat, html or '', re.I))
    return {
        'hero': n(r'class="hero"'),
        'sections': n(r'class="sec"'),
        'stats': n(r'class="stats"'),
        'params': n(r'class="params"'),
        'timeline': n(r'class="tl"'),
        'steps': n(r'class="steps"'),
        'team': n(r'class="team"'),
        'quote': n(r'class="quote"'),
        'photos': n(r'class="ph[ "]'),
        'chips': n(r'class="chip"'),
        'signup': n(r'class="signup"'),
        'images_used': len(set(re.findall(r'\{\{media:([A-Za-z0-9_\-]+)\}\}', html or ''))),
    }
