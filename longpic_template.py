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
import random
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
/* ★ 2026-10-08：遮罩从「顶部 .78 全压」改成「上轻下重、中间通透」——
   实测原来的强度把照片洗成一片灰白，而"以图片为主视觉"是硬要求；
   标题可读性由 h1 的 text-shadow 保底。 */
.hero-shade{position:absolute;inset:0;background:linear-gradient(180deg,
  %(hero_fade)s.42) 0%%,%(hero_fade)s.10) 22%%,%(hero_fade)s0) 44%%,
  %(hero_fade)s0) 60%%,%(hero_fade)s.82) 88%%,%(bg)s 100%%)}
.hero-in{position:relative;z-index:2;height:100%%;display:flex;flex-direction:column;padding:50px 50px 60px}
.brandrow{display:flex;align-items:center;gap:16px}
.brandrow span{font-size:19px;letter-spacing:3.6px;color:%(primary)s;font-weight:600}
.hero-sp{flex:1}
.hero-chip{align-self:flex-start;font-size:20px;letter-spacing:2.8px;color:%(primary)s;
  border:1px solid %(chipbd)s;border-radius:999px;padding:9px 24px;margin-bottom:26px;
  background:%(hero_fade)s.6)}
.hero h1{font-size:66px;line-height:1.18;font-weight:900;letter-spacing:1.8px;color:%(ink)s;
  text-wrap:balance;
  text-shadow:0 2px 28px %(hero_fade)s.85),0 0 60px %(hero_fade)s.55)}
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
.params .d{flex:none;width:120px;font-size:22px;font-weight:700;color:%(accent)s;letter-spacing:1.8px;padding-top:3px}
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

/* ---------- fee（费用包含 / 不含，双栏） ----------
   2026-10-08 补：公众号长图要有「客人下单前必须知道」的信息，费用边界是第一位。
   ★ 渲染前 _blk_fee 会再过一遍内部成本词黑名单闸门（INTERNAL_COST），
     cost_guard 管 API 出口，这里管模板出口，两道闸互不替代。 */
.fee{margin-top:38px;display:flex;gap:18px}
.fee .col{flex:1;min-width:0;background:%(card)s;border:1px solid %(line2)s;border-radius:18px;padding:26px 24px}
.fee .hd{font-size:21px;font-weight:800;letter-spacing:2.2px;color:%(primary)s;margin-bottom:14px}
.fee .li{display:flex;gap:12px;font-size:21px;line-height:1.7;color:%(muted)s;padding:5px 0}
.fee .ico{flex:none;width:26px;font-weight:800}
.fee .yes .ico{color:%(primary)s}
.fee .no .ico{color:%(faint)s}

/* ---------- kit（出行装备清单） ---------- */
.kit{margin-top:38px;background:%(card)s;border:1px solid %(line2)s;border-radius:18px;padding:28px 30px 24px}
.kit .hd{font-size:22px;font-weight:800;letter-spacing:2.4px;color:%(primary)s;margin-bottom:18px}
.kit .ul{display:flex;flex-wrap:wrap;gap:12px 26px}
.kit .ul li{flex:1 1 44%%;display:flex;gap:13px;font-size:21px;line-height:1.6;color:%(ink)s;list-style:none}
.kit .ul li::before{content:'';flex:none;width:9px;height:9px;border-radius:50%%;background:%(accent)s;margin-top:11px}

/* ---------- prices（团期 / 价格表） ----------
   ★ 价格必须是**真实正数**：_real_price() 会把 0 / 空 / 占位符挡掉 ——
     素材没价时模型爱填 0，成品上印「¥0 / 人」比不写价格糟得多。 */
.prices{margin-top:38px}
.prices .row{display:flex;align-items:center;gap:22px;padding:24px 0;border-bottom:1px solid %(line2)s}
.prices .row:first-child{border-top:1px solid %(line2)s}
.prices .lb{flex:1;min-width:0}
.prices .lb .l1{font-size:25px;font-weight:700;color:%(ink)s;letter-spacing:.6px}
.prices .lb .l2{margin-top:5px;font-size:19px;color:%(faint)s;letter-spacing:1.6px}
.prices .pr{flex:none;text-align:right}
.prices .pr .v{font-size:38px;font-weight:900;color:%(primary)s;letter-spacing:.6px;line-height:1.1}
.prices .pr .v small{font-size:21px;font-weight:700;margin-left:3px}
.prices .pr .u{margin-top:4px;font-size:18px;color:%(faint)s;letter-spacing:1.6px}

/* ══════════════════════════════════════════════════════════════════
   版式变体（「随机排版」的正确做法：手写若干套，由**代码**抽签，
   而不是把排版权交回模型 —— 见文件头"为什么要有这个文件"）
   每个变体都是固定 CSS + 已验过安全，抽到哪一个都不会难看。
   ══════════════════════════════════════════════════════════════════ */
/* hero A / 现状：整屏大图 + 渐隐 + 标题压在画面下 1/3（标杆同款） */
/* hero B：图在上半部（带下圆角），信息沉到下方留白 —— 照片偏暗时更清楚 */
.hero.v-card .hero-bg{height:56%%;bottom:auto;border-radius:0 0 36px 36px}
.hero.v-card .hero-shade{height:56%%;bottom:auto;border-radius:0 0 36px 36px}
.hero.v-card .hero-in{justify-content:flex-end;padding-bottom:64px}
.hero.v-card .hero-sp{display:none}
/* hero C：封面式（图作淡底纹，大字居中）—— 杂志封面感，适合照片不多时
   ★ 不透明度 0.16→0.30（2026-10-08 实机看：0.16 时照片几乎看不见，
     而"以图片为主视觉"是老板的硬要求）；同时恢复一层很轻的渐隐，
     保证居中大标题在任何照片上都读得清。 */
.hero.v-cover .hero-bg{opacity:.42}
.hero.v-cover .hero-shade{background:linear-gradient(180deg,
  %(hero_fade)s.52) 0%%,%(hero_fade)s.20) 46%%,%(hero_fade)s.62) 100%%)}
.hero.v-cover .hero-in{justify-content:center;text-align:center;align-items:center}
.hero.v-cover .hero-sp{display:none}
.hero.v-cover .hero-chip{align-self:center}
.hero.v-cover h1{font-size:74px}
.hero.v-cover .meta{justify-content:center}
.hero.v-cover .brandrow{justify-content:center}
/* stats 变体：卡片式（默认是无分隔线的横排数字栏） */
.stats.v-cards{gap:14px}
.stats.v-cards .stat{background:%(card)s;border:1px solid %(line2)s;border-radius:16px;padding:20px 18px}
.stats.v-cards .stat+.stat{border-left:none;padding-left:18px}
/* 图组变体：主次并排（2 图时不再永远等宽） / 内缩留白（大图呼吸感） */
.duo.v-mj .ph:first-child{flex:1.55}
.duo.v-inset{margin-left:28px;margin-right:28px}
.photo-full.v-inset{margin-left:28px;margin-right:28px;border-radius:26px}
.photo-tall.v-inset{margin-left:28px;margin-right:28px;border-radius:26px}

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


# 标题折行阈值（详见 wrap_title 的说明）：按字号/字距反算 750px 画布一行最多几个字
_HERO_TITLE_MAX = 9      # 66px + 1.8px 字距，画布内容宽 650px
_SEC_TITLE_MAX = 14      # 45px + 1.4px
_SIGNUP_TITLE_MAX = 13   # 47px + 2.7px


def build_css(theme: str) -> str:
    v = THEMES.get(theme) or THEMES[DEFAULT_THEME]
    return _CSS % v


# ══════════════════════════════════════════════════════════════════════
# 三、渲染工具
# ══════════════════════════════════════════════════════════════════════
def _e(x: Any) -> str:
    """转义为纯文本（模型只填文本，绝不产出标签）。"""
    return _html.escape(clean_placeholder(x), quote=True)


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


def _real_price(x: Any) -> bool:
    """只有**真实正数**售价才渲染价格行。

    ★ 2026-10-08 实测踩到的坑：素材里没有对外售价时，模型会填 `"price": 0`，
      于是成品上出现一行「**¥0 / 人**」——比不写价格糟得多（客人以为是免费）。
    """
    t = str(x if x is not None else '').strip()
    if not t:
        return False
    m = re.search(r'\d+(?:\.\d+)?', t)
    if not m:
        return False
    try:
        return float(m.group(0)) > 0
    except ValueError:
        return False


# 内部成本口径的词，绝不进「费用包含」清单。
# 依据（用户铁律）：成本数据不得出现在任何前端 —— 这是最后一道兜底，
# 提示词已先要求"只抄资料里写给客人的费用包含，不要搬内部成本明细"。
_INTERNAL_COST = re.compile(r'工作餐|员工餐|内部|成本|毛利|摊销|策划执行|物料费|服务费|佣金|分成')


def _lst(x: Any) -> list:
    if x is None:
        return []
    if isinstance(x, list):
        return [i for i in x if i not in (None, '', {})]
    return [x]


# ──────────────────────────────────────────────────────────────────────
# 两处**确定性兜底**（2026-10-08 实测后加的）：提示词写了、模型仍会漏的两件事，
# 改成代码保证 —— 因为它们直接决定"这张图能不能发出去"。
# ──────────────────────────────────────────────────────────────────────
# ① 占位符日期：资料只写「10 月（日期待定）」时，模型会把 `10 月 xx 日` 原样搬进报名区，
#    而这是**要印在宣传图上的**。凡短字段里出现占位符，改写成诚实的「月份 · 日期待定」。
#    ★ 只碰**短字段**（≤28 字）：正文长句里出现"待定"是正常表达
#      （"具体日期待定，出行前 1-2 天建群通知"），整句截断是第一版的 bug，已修。
_PH_TOKEN = re.compile(r'(?:[xX]{2,}|待定|TBD|\?\?|__+)')
_PH_TAIL = re.compile(r'[\s—\-–~～·、,，/]+$')


def clean_placeholder(x: Any) -> str:
    t = '' if x is None else str(x)
    if len(t) > 28 or not _PH_TOKEN.search(t):
        return t                                   # 原样返回（保住换行，交给 nl2br）
    head = _PH_TAIL.sub('', _PH_TOKEN.split(t)[0]).strip()
    if not head:
        return '待定'
    if ('年' in head or '月' in head) and not re.search(r'\d\s*$', head):
        return head + ' · 日期待定'
    return head


# ② 超长标题折行：750px 画布上主标题**一行只放得下 9 个字**（66px + 字距），
#    模型常无视「每行 6~8 字」、写成一行 13 个字 → 挤出一个孤字、版式就垮。
#    这里做确定性断行：优先断在中点的标点处，其次中点硬断。
_TITLE_BREAKERS = '，,、：:；;· 　'


def wrap_title(t: Any, maxlen: int) -> str:
    t = str(t if t is not None else '').strip()
    if '\n' in t or len(t) <= maxlen:
        return t
    mid = len(t) // 2
    best = None
    for i, ch in enumerate(t):
        if ch in _TITLE_BREAKERS and abs(i - mid) <= maxlen // 2 + 1:
            if best is None or abs(i - mid) < abs(best - mid):
                best = i
    if best is not None and 0 < best < len(t) - 1:
        return t[:best + 1].rstrip() + '\n' + t[best + 1:].lstrip()
    return t[:mid] + '\n' + t[mid:]


def wrap_lines(x: Any, maxlen: int) -> str:
    """整段多行标题：逐行按 maxlen 折行，结果仍用 \\n 分（内部可能有多个断点）。"""
    raw = '' if x is None else str(x)
    return '\n'.join(wrap_title(l, maxlen) for l in raw.split('\n'))


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
class Ctx:
    """一次渲染的上下文：图片白名单 + **版式抽签结果**。

    ★ 关于「随机排版」：2026-10-08 老板提「随机排版能力不够」。随机**不能**交回模型里做
      （那正是 v1/v2「乱排」的根因），正确做法是**代码手写若干套变体 → 由种子抽签**：
      抽到哪一套都是验过的好版式，只是「这一次长这样」。种子 = 活动 id + 生成轮次，
      所以同一场活动重生成会换一套版式、而预览/导出始终是同一套（可复现）。
    """
    __slots__ = ('allowed', 'hero', 'duo', 'photo', 'stats', 'photos')

    def __init__(self, allowed: set[str] | None = None, seed: int = 0,
                 photo_order: list[str] | None = None):
        r = random.Random(int(seed) & 0x7FFFFFFF)
        self.allowed = set(allowed or ())
        # 有序清单：首屏兜底要取"第一张"（set 取不出顺序，会导致每次生成挑到不同的图）
        self.photos = [x for x in (photo_order if photo_order is not None else sorted(self.allowed)) if x]
        self.hero = r.choice(['', 'v-card', 'v-cover'])     # '' = 标杆原版（渐隐压图）
        self.duo = r.choice(['', 'v-mj', 'v-inset'])
        self.photo = r.choice(['', 'v-inset'])
        self.stats = r.choice(['', 'v-cards'])


def _blk_stats(b: dict, ctx: Ctx) -> str:
    items = _lst(b.get('items'))[:4]
    cells = []
    for it in items:
        v, k = _s(it, 'v'), _s(it, 'k')
        if not (v or k):
            continue
        cells.append('<div class="stat"><div class="v">%s</div><div class="k">%s</div></div>'
                     % (_e(v), _e(k)))
    cls = ('stats ' + ctx.stats).strip()
    return '<div class="%s">%s</div>' % (cls, ''.join(cells)) if cells else ''


def _blk_params(b: dict, ctx: Ctx) -> str:
    rows = []
    for it in _lst(b.get('items'))[:8]:
        d, p, note = _s(it, 'k') or _s(it, 'd'), _s(it, 'v'), _s(it, 'note')
        if not (d or p):
            continue
        tail = ' <i>· %s</i>' % _e(note) if note else ''
        rows.append('<div class="r"><div class="d">%s</div><div class="p">%s%s</div></div>'
                    % (_e(d), _nl2br(p), tail))
    return '<div class="params">%s</div>' % ''.join(rows) if rows else ''


def _blk_chips(b: dict, ctx: Ctx) -> str:
    items = [_s(x) if not isinstance(x, dict) else _s(x, 'text') for x in _lst(b.get('items'))[:8]]
    items = [i for i in items if i.strip()]
    return ('<div class="chips">%s</div>'
            % ''.join('<span class="chip">%s</span>' % _e(i) for i in items)) if items else ''


def _blk_photos(b: dict, ctx: Ctx) -> str:
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
    alw = ctx.allowed
    out = ''
    if len(shots) == 1:
        cls = {'full': 'photo-full', 'tall': 'photo-tall', 'wide': 'photo-wide'}.get(shots[0][2], 'photo-full')
        if ctx.photo:
            cls += ' ' + ctx.photo
        box = _img(shots[0][0], alw, cls)
        if box:
            out = box + ('<div class="cap en">%s</div>' % _e(shots[0][1]) if shots[0][1].strip() else '')
    elif len(shots) == 2:
        pair = ''.join(_img(r, alw) for r, _, _ in shots)
        if pair.count('<img') >= 2:
            out = '<div class="duo %s">%s</div>' % (ctx.duo, pair)
            if shots[0][1]:
                out += '<div class="cap en">%s</div>' % _e(shots[0][1])
        else:
            out = _pic(shots[0][0], alw, 'full', shots[0][1]) or _pic(shots[1][0], alw, 'full', shots[1][1])
    else:
        head = _pic(shots[0][0], alw, 'full', shots[0][1])
        trio = ''.join(_img(r, alw) for r, _, _ in shots[1:4])
        out = head + ('<div class="trio">%s</div>' % trio if trio.count('<img') >= 2 else '')
    return out


def _blk_timeline(b: dict, ctx: Ctx) -> str:
    rows = []
    for it in _lst(b.get('items'))[:8]:
        t, w, note = _s(it, 'time'), _s(it, 'what') or _s(it, 'text'), _s(it, 'note')
        if not (t or w):
            continue
        tail = ' <i>· %s</i>' % _e(note) if note else ''
        rows.append('<div class="t"><div class="time en">%s</div><div class="what">%s%s</div></div>'
                    % (_e(t), _nl2br(w), tail))
    return '<div class="tl">%s</div>' % ''.join(rows) if rows else ''


def _blk_steps(b: dict, ctx: Ctx) -> str:
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


def _blk_team(b: dict, ctx: Ctx) -> str:
    rows = []
    for it in _lst(b.get('items'))[:8]:
        name, role, ref = _s(it, 'name'), _s(it, 'role'), _s(it, 'media') or _s(it, 'ref')
        if not name:
            continue
        ava = ('<img class="ava" src="{{media:%s}}" alt="">' % _e(ref)
               if ref and (not ctx.allowed or ref in ctx.allowed)
               else '<div class="ava ava-init">%s</div>' % _e(name[0]))
        rows.append('<div class="tm">%s<div><div class="name">%s</div><div class="role">%s</div></div></div>'
                    % (ava, _e(name), _nl2br(role)))
    return '<div class="team">%s</div>' % ''.join(rows) if rows else ''


def _blk_quote(b: dict, ctx: Ctx) -> str:
    t = _s(b, 'text') or _s(b, 'v')
    return '<div class="quote"><p>%s</p></div>' % _nl2br(t) if t.strip() else ''


def _blk_fee(b: dict, ctx: Ctx) -> str:
    """费用包含 / 费用不含（双栏）。

    ★ 顾客在长图里最常找的就是「到底含什么」。这里除了渲染，**再过一遍内部成本词闸门**
      —— 数据源可能被脏数据污染，而这是要印出去的东西（依据：成本数据不得出现在任何前端）。
    """
    def clean(xs: list) -> list[str]:
        out = []
        for i in xs[:9]:
            t = _s(i)
            if t.strip() and not _INTERNAL_COST.search(t):
                out.append(t)
        return out

    inc = clean(_lst(b.get('inc') or b.get('include')))
    exc = clean(_lst(b.get('exc') or b.get('exclude') or b.get('no')))
    if not (inc or exc):
        return ''

    def col(cls: str, hd: str, items: list[str], yes: bool) -> str:
        if not items:
            return ''
        lis = ''.join('<div class="li"><div class="ico">%s</div><div>%s</div></div>'
                      % ('✓' if yes else '✕', _e(i)) for i in items)
        return '<div class="col %s"><div class="hd">%s</div>%s</div>' % (cls, hd, lis)

    inner = col('yes', '费用包含', inc, True) + col('no', '费用不含', exc, False)
    return '<div class="fee">%s</div>' % inner if inner else ''


def _blk_kit(b: dict, ctx: Ctx) -> str:
    """出行装备清单（务必自带什么）。"""
    items = [_s(i) for i in _lst(b.get('items'))[:14]]
    items = [i for i in items if i.strip()]
    if not items:
        return ''
    hd = _s(b, 'title') or '出行装备清单'
    return ('<div class="kit"><div class="hd">%s</div><ul class="ul">%s</ul></div>'
            % (_e(hd), ''.join('<li>%s</li>' % _e(i) for i in items)))


def _blk_prices(b: dict, ctx: Ctx) -> str:
    """团期 / 价格表。

    ★ 只有**真实正数**价格才渲染金额（_real_price）—— 素材没有售价时模型爱填 0，
      成品上印「¥0 / 人」等于告诉客人免费，是事故。
      若一个真价都没有，整块不渲染（只剩日期的价目表没有意义，交给 form 去说）。
    """
    rows, real = [], 0
    for it in _lst(b.get('items'))[:6]:
        label = _s(it, 'label') or _s(it, 'k')
        date, note = _s(it, 'date'), _s(it, 'note')
        unit = _s(it, 'unit') or '/ 人'
        price = _s(it, 'price') or _s(it, 'v')
        if not (label or date or price):
            continue
        l1 = label or date or '团期'
        l2 = ' · '.join(x for x in [date if label else '', note] if x)
        pv = ''
        if _real_price(price):
            real += 1
            pv = '<div class="v"><small>¥</small>%s <small>%s</small></div>' % (_e(price), _e(unit))
        rows.append(
            '<div class="row"><div class="lb"><div class="l1">%s</div>%s</div><div class="pr">%s</div></div>'
            % (_e(l1), '<div class="l2">%s</div>' % _e(l2) if l2 else '', pv))
    if not rows or not real:
        return ''
    return '<div class="prices">%s</div>' % ''.join(rows)


_BLOCK_RENDER = {
    'stats': _blk_stats,
    'params': _blk_params,
    'chips': _blk_chips,
    'photos': _blk_photos,
    'timeline': _blk_timeline,
    'steps': _blk_steps,
    'team': _blk_team,
    'quote': _blk_quote,
    # ↓ 2026-10-08 补：客人下单前要找的东西（费用边界 / 自备装备 / 团期价格）
    'fee': _blk_fee,
    'kit': _blk_kit,
    'prices': _blk_prices,
    # 别名容错：模型不照 block type 写是常态（这次 eth, 上一次换成 soan）
    'cost': _blk_fee,
    'checklist': _blk_kit,
    'gear': _blk_kit,
    'price': _blk_prices,
    'occurrences': _blk_prices,
}


def _render_blocks(blocks: Iterable, ctx: Ctx) -> str:
    out = []
    for b in _lst(blocks):
        if not isinstance(b, dict):
            continue
        fn = _BLOCK_RENDER.get(str(b.get('type') or '').strip().lower())
        if not fn:
            continue
        try:
            piece = fn(b, ctx)
        except Exception:
            piece = ''
        if piece:
            out.append(piece)
    return ''.join(out)


# ══════════════════════════════════════════════════════════════════════
# 五、整篇渲染
# ══════════════════════════════════════════════════════════════════════
def _render_hero(h: dict, ctx: Ctx, cover_url: str | None = None) -> str:
    if not isinstance(h, dict):
        h = {}
    allowed = ctx.allowed
    ref = _s(h, 'media') or _s(h, 'ref')
    bg = ''
    if ref and (not allowed or ref in allowed):
        bg = '{{media:%s}}' % _e(ref)
    elif cover_url:
        bg = _e(cover_url)
    elif ctx.photos:
        # ★ 2026-10-08 实测：模型有时**不给首屏选图**，而首屏是固定 1050px 高的容器，
        #   没有图就变成一大片空白（用户第一眼看到的是一张"坏图"）。
        #   没有封面就退回清单第一张 —— 宁可换一张图，也不能空着。
        bg = '{{media:%s}}' % _e(ctx.photos[0])
    bg_html = '<img class="hero-bg" src="%s" alt="">' % bg if bg else '<div class="hero-bg"></div>'

    brand = _s(h, 'brand')
    brand_html = '<div class="brandrow"><span class="en">%s</span></div>' % _e(brand) if brand else ''

    chip = _s(h, 'chip')
    chip_html = '<div class="hero-chip">%s</div>' % _e(chip) if chip else ''

    # 主标题：支持显式换行（`\n`）+ accent 高亮（accent 必须是标题里原样出现的一段）
    raw_title = str(h.get('title') or '')
    wrapped = wrap_lines(raw_title, _HERO_TITLE_MAX)      # ★ 确定性折行（一行 9 字）
    lines = [l for l in wrapped.split('\n')]
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

    hero_cls = ('hero ' + ctx.hero).strip()
    return ('<section class="%s">%s<div class="hero-shade"></div>'
            '<div class="hero-in">%s<div class="hero-sp"></div>%s<h1>%s</h1>%s%s%s</div></section>'
            % (hero_cls, bg_html, brand_html, chip_html, title_html, sub_html, meta_html, en_html))


def _render_section(s: dict, ctx: Ctx) -> str:
    if not isinstance(s, dict):
        return ''
    eyebrow = _s(s, 'eyebrow') or _s(s, 'no')
    eye_html = ('<div class="s-eyebrow"><span class="no en">%s</span><i class="line"></i></div>' % _e(eyebrow)
                if eyebrow else '')
    title = _s(s, 'title')
    title_html = ('<div class="s-title">%s</div>' % _nl2br(wrap_lines(title, _SEC_TITLE_MAX))
                  if title else '')
    lead = _s(s, 'lead') or _s(s, 'text')
    lead_html = '<p class="s-lead">%s</p>' % _nl2br(lead) if lead else ''
    body = _render_blocks(s.get('blocks'), ctx)
    note = _s(s, 'note')
    note_html = '<p class="s-note">%s</p>' % _nl2br(note) if note else ''
    inner = eye_html + title_html + lead_html + body + note_html
    return '<section class="sec">%s</section>' % inner if inner.strip() else ''


def _render_signup(s: dict, ctx: Ctx) -> str:
    if not isinstance(s, dict):
        s = {}
    parts = []
    en_roll = _s(s, 'enRoll') or _s(s, 'en_roll') or 'JOIN US'
    parts.append('<div class="en-roll en">%s</div>' % _e(en_roll))
    title = _s(s, 'title')
    if title:
        parts.append('<h2>%s</h2>' % _nl2br(wrap_lines(title, _SIGNUP_TITLE_MAX)))
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
        pics = ''.join(_img(r, ctx.allowed) for r in refs if r.strip())
        pics = pics if pics.count('<img') >= 2 else ''
        price, unit = _s(prod, 'price'), _s(prod, 'unit') or '/ 人'
        inc = [_s(i) for i in _lst(prod.get('inc'))]
        inc = [i for i in inc if i.strip() and not _INTERNAL_COST.search(i)]
        if pics or price or inc:
            inc_html = '<div class="inc">%s</div>' % '<br>'.join('· ' + _e(i) for i in inc) if inc else ''
            txt = ''
            if _real_price(price):
                txt = '<div class="price"><small>¥</small>%s <small>%s</small></div>' % (_e(price), _e(unit))
            prod_html = '<div class="product">%s<div class="txt">%s%s</div></div>' % (pics, txt, inc_html)
            if pics or txt:
                parts.append(prod_html)

    # 二维码：只有调用方真的提供了二维码 ref 才渲染（否则会留一块破图占位）
    if s.get('qr') and '__QR__' in ctx.allowed:
        parts.append('<div class="qrwrap"><div class="qrcard"><img src="{{media:__QR__}}" alt=""></div>'
                     '<div class="scan">扫码报名咨询</div>'
                     '<div class="scan-tip">名额有限 · 先到先得</div></div>')

    fb, fe = _s(s, 'footBrand'), _s(s, 'footEn')
    if fb:
        parts.append('<div class="foot"><span>%s</span></div>' % _e(fb))
    if fe:
        parts.append('<div class="foot-en en">%s</div>' % _e(fe))
    return '<section class="signup">%s</section>' % ''.join(parts)


def render(doc: dict, allowed_refs: set[str] | None = None, cover_url: str | None = None,
           seed: int = 0, photo_order: list[str] | None = None) -> str:
    """把模型产出的内容 JSON 渲染成完整长图 HTML（含样式）。

    doc = {title, theme, hero, sections[], signup}；所有字段容错，缺了就不渲染那一块。

    seed：版式抽签种子 —— 同一场活动每次「重新生成」换一个种子就换一套版式，
    但**一次生成内**预览和导出必须同源，所以种子由调用方决定、不在这里随机。
    """
    doc = doc if isinstance(doc, dict) else {}
    ctx = Ctx(allowed_refs, seed, photo_order)
    theme = str(doc.get('theme') or '').strip().lower()
    if theme not in THEMES:
        theme = DEFAULT_THEME

    body = [_render_hero(doc.get('hero'), ctx, cover_url)]
    for s in _lst(doc.get('sections')):
        body.append(_render_section(s, ctx))
    body.append(_render_signup(doc.get('signup'), ctx))

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
        'hero': n(r'class="hero[ "]'),
        'sections': n(r'class="sec"'),
        'stats': n(r'class="stats"'),
        'params': n(r'class="params"'),
        'timeline': n(r'class="tl"'),
        'steps': n(r'class="steps"'),
        'team': n(r'class="team"'),
        'quote': n(r'class="quote"'),
        'photos': n(r'class="ph[ "]'),
        'chips': n(r'class="chip"'),
        'fee': n(r'class="fee"'),
        'kit': n(r'class="kit"'),
        'prices': n(r'class="prices"'),
        'signup': n(r'class="signup"'),
        'images_used': len(set(re.findall(r'\{\{media:([A-Za-z0-9_\-]+)\}\}', html or ''))),
    }
