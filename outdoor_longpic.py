"""两类长图共用视觉规则；内容与章节编排仍由模型决定。"""
import html
import re
from html.parser import HTMLParser


STYLE_PROMPT = """## 品牌气质与构图工具箱（风格规范，不是固定模板）
这是户外俱乐部长图的**设计原则**。它规定气质下限与安全边界，不规定这一场长什么样：
配色、深浅底、字号阶梯、章节顺序、构图组合，都由你根据这一场的照片与故事重新决定。

### 气质下限（必须守）
- 真实照片是主视觉，文字是节拍，不是反过来；自然、克制、有呼吸感，像一本户外摄影集的一页。
- 系统字体栈（Songti SC / STSong / serif 做标题，PingFang SC / Microsoft YaHei / sans-serif 做正文）；
  不用外部字体、不写 CSS url() 引外部图片、不用霓虹色 / 高饱和渐变 / 装饰插画 / 厚重阴影。
- 主标题与照片叠放时，叠字容器标记 data-photo-overlay，且**必须压暗**照片相应区域（≥55%），
  白字要一眼读得清；亮天空、雪山上不许放浅色小字。
- 深绿停顿区可标记 data-tone="forest"，浅色信息区可标记 data-tone="card"（只是可选语义，不是必填）。

### 构图工具箱（每场至少挑 3 种，自由组合，禁止固定顺序）
满幅出血大图开篇 / 深底色块题记页 / 大留白文字页 / 双图错落并排 / 左右图文双栏（文字至少 40% 宽）/
通栏大图与成段叙事交替 / 竖长图配横图咬合 / 数字事实页 / 单句引言页 / 收尾满幅照片 /
错落多图页间插一页纯文字。同一场里相邻两节不许同构，禁止每节编号、巨大中文序号、1-2-3 流水账。
严禁套「封面→题记→图文→图文→金句→结尾」这个固定顺序。

### 写作与事实
- 不创造事实：日期、价格、地点、人名、数字只取自资料；资料没有就一个字都不补。
- 不为凑章节或字数虚构情节；少图、无图、短记录可以更短。参考图只提供视觉语言，
  严禁照搬其中的活动名、品牌、地名、价格、赠品和口号；不要无意义的英文标签。

### 安全边界（代码会再兜一道）
750px 画布；用 section / h1 / h2 / p / figure / img / figcaption / ul / li / table 等语义标签；
照片必须是 img 且 src 写成占位符，不用 CSS 背景图；正文留在文档流中，仅封面允许局部叠字；
正文左右内边距约 56px、上下 64~96px；单图 400~720px，双图各约 48%；
禁止三列小图墙、横向溢出、负边距与旋转文字。"""


STYLE_CSS = """
#clubos-outdoor{--bg:#F4F1E8;--ink:#28342D;--deep:#29483B;--accent:#A6633E;
 box-sizing:border-box;width:750px;max-width:100%;margin:0;background:#F4F1E8!important;
 color:#28342D!important;font:26px/1.9 "PingFang SC","Microsoft YaHei",sans-serif;
 overflow-wrap:break-word;-webkit-font-smoothing:antialiased}
#clubos-outdoor *{box-sizing:border-box;min-width:0;max-width:100%;color:inherit!important;
 font-family:inherit!important;background:transparent!important;background-image:none!important;
 border-radius:0!important;box-shadow:none!important;text-shadow:none!important}
#clubos-outdoor h1,#clubos-outdoor h2,#clubos-outdoor h3{
 font-family:"Songti SC","STSong",serif!important;font-weight:600!important;
 line-height:1.4!important;letter-spacing:1px!important;text-wrap:balance}
#clubos-outdoor h1{font-size:64px!important;margin:0 0 28px}
#clubos-outdoor h2{font-size:40px!important;margin:0 0 24px}
#clubos-outdoor h3{font-size:30px!important}
#clubos-outdoor p,#clubos-outdoor li{font-size:26px!important;line-height:1.9!important}
#clubos-outdoor p{margin:0 0 24px}
#clubos-outdoor small,#clubos-outdoor figcaption{font-size:20px!important;line-height:1.7!important}
#clubos-outdoor figure{margin:0}
#clubos-outdoor img{display:block;max-width:100%;max-height:720px;object-fit:cover}
#clubos-outdoor table{width:100%;border-collapse:collapse;font-size:24px}
#clubos-outdoor td,#clubos-outdoor th{padding:18px 12px;border-bottom:1px solid #D9DDD3}
#clubos-outdoor [data-tone="forest"]{background:#29483B!important;color:#F4F1E8!important}
#clubos-outdoor [data-tone="card"]{background:#FFFDF8!important;border-radius:8px!important}
#clubos-outdoor [data-photo-overlay]{color:#FFFDF8!important;
 background:linear-gradient(0deg,rgba(20,32,25,.78),rgba(20,32,25,.12))!important}
#clubos-outdoor hr{border:0;border-top:1px solid #D9DDD3;margin:48px 0}
/* 结构化兜底也使用文档流封面，避免旧模板叠图与导出不一致。 */
#clubos-outdoor .rc{background:#F4F1E8!important;color:#28342D!important}
#clubos-outdoor .hero{height:auto!important;padding:0!important}
#clubos-outdoor .hero-bg{position:static!important;width:100%!important;height:560px!important;opacity:1!important}
#clubos-outdoor .hero-shade,#clubos-outdoor .hero-sp{display:none!important}
#clubos-outdoor .hero-in{position:static!important;height:auto!important;padding:64px 56px!important}
#clubos-outdoor .hero-chip,#clubos-outdoor .en-loc,#clubos-outdoor .en-roll{display:none!important}
#clubos-outdoor .brandrow .en{font-size:20px!important;letter-spacing:2px}
#clubos-outdoor .signup{background:#29483B!important;color:#F4F1E8!important}
#clubos-outdoor .quote{border-left:1px solid #A6633E!important}
"""


# 模型直出路线的「安全护栏」：只保证画布不溢出、图片不变形、文字可读。
# ★ 不带任何 !important 通配覆盖 —— 配色、字号、构图、留白由模型为这一场决定。
#   STYLE_PROMPT 是提示词里的软规范，STYLE_CSS 只服务结构化模板兜底，两者不能混用：
#   把模板的强覆盖套到模型稿上，等于让模型做的设计全部作废（2026-11-04 线上实测）。
MODEL_CSS = """
#clubos-outdoor{--bg:#F4F1E8;--ink:#28342D;--deep:#29483B;--accent:#A6633E;
 width:750px;max-width:100%;margin:0;box-sizing:border-box;
 background:#F4F1E8;color:#28342D;
 font:26px/1.9 "PingFang SC","Microsoft YaHei",sans-serif;
 overflow-wrap:break-word;-webkit-font-smoothing:antialiased}
#clubos-outdoor *{box-sizing:border-box}
#clubos-outdoor img{display:block;max-width:100%;max-height:760px;height:auto;object-fit:cover}
#clubos-outdoor h1,#clubos-outdoor h2,#clubos-outdoor h3{
 font-family:"Songti SC","STSong",serif;font-weight:600;line-height:1.4}
#clubos-outdoor table{width:100%;border-collapse:collapse}
#clubos-outdoor [data-tone="forest"]{background:#29483B;color:#F4F1E8}
#clubos-outdoor [data-tone="card"]{background:#FFFDF8}
#clubos-outdoor [data-photo-overlay]{color:#FFFDF8;
 background:linear-gradient(0deg,rgba(20,32,25,.78),rgba(20,32,25,.12))}
"""


def apply_style(fragment, *, strict=False):
    """给 750px 长图套外壳。

    strict=False（默认）：模型直出 HTML / 回顾直出 HTML，只加安全护栏，
        **不剥 !important、不覆盖模型样式**，让每场设计真正生效。
    strict=True：结构化模板兜底，沿用 STYLE_CSS 强覆盖把旧模板收敛成文档流版式。
    """
    text = str(fragment or '')
    if strict:
        text = re.sub(r'!\s*important\b', '', text, flags=re.I)
        return '<section id="clubos-outdoor">' + text + '</section><style>' + STYLE_CSS + '</style>'
    return '<section id="clubos-outdoor">' + text + '</section><style>' + MODEL_CSS + '</style>'


class _Content(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.text = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('style', 'script'):
            self.hidden += 1
        if tag == 'img':
            self.images.append(dict(attrs).get('src') or '')

    def handle_endtag(self, tag):
        if tag in ('style', 'script'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def content_text(fragment):
    doc = _Content()
    doc.feed(fragment)
    return '\n'.join(doc.text)


def photos_valid(fragment, allowed, minimum, extra_urls=()):
    doc = _Content()
    doc.feed(fragment)
    seen = set()
    for src in doc.images:
        match = re.fullmatch(r'\{\{?\s*media\s*:\s*([\w-]+)\s*\}?\}', src.strip())
        ref = match.group(1) if match else src
        if not ((match and ref in allowed) or src in extra_urls) or ref in seen:
            return False
        seen.add(ref)
    return len(seen) >= minimum


def recap_fallback_html(data, master):
    """兜底也要像一份被编辑过的现场记录：真实素材 + 有节奏的版面，不虚构任何事实。

    2026-11-04 实坑：旧兜底把标题与照片平铺成「白底文档」，用户实测「内容差」。
    现在组织成「题记页 → 若干现场段落（图文交替、左右偏置）→ 现场相册 → 克制收尾」，
    文字仍全部取自真实 blocks / note / facts，只做编排，不补故事、口号或招募话术。
    """
    esc = lambda v: html.escape(str(v or ''))
    refs = [m['ref'] for m in data.get('media', []) if m.get('ref')]
    used = set()

    def photo(ref, height=520):
        if ref not in refs or ref in used:
            return ''
        used.add(ref)
        return ('<figure style="margin:0"><img src="{{media:%s}}" alt="" '
                'style="width:100%%;height:%dpx;object-fit:cover;display:block"></figure>'
                % (esc(ref), height))

    title = esc(data.get('title') or master.get('title') or '活动回顾')
    meta = ' · '.join(esc(master[k]) for k in ('date', 'location', 'capacity') if master.get(k))

    # ① 题记页：深绿整幅色块，主标题 + 真实事实行
    pieces = [
        '<section data-tone="forest" style="padding:110px 56px 96px">',
        '<p style="font-size:15px;letter-spacing:6px;margin:0 0 28px;opacity:.75">现 场 记 录</p>',
        '<h1 style="font-family:\'Songti SC\',\'STSong\',serif;font-size:64px;line-height:1.35;'
        'letter-spacing:2px;margin:0 0 32px">' + title + '</h1>',
    ]
    if meta:
        pieces.append('<p style="font-size:20px;letter-spacing:2px;margin:0;opacity:.85">' + meta + '</p>')
    pieces.append('</section>')

    # ② 现场段落：真实句子配真实照片，左右交替偏置，避免每节同构
    paragraphs = []
    for block in data.get('blocks') or []:
        if not isinstance(block, dict):
            continue
        head = str(block.get('headline') or '').strip()
        body = str(block.get('body') or block.get('text') or '').strip()
        if head and head != title:
            paragraphs.append(('h2', head))
        for line in body.splitlines():
            if line.strip():
                paragraphs.append(('p', line.strip()))
        for ref in block.get('mediaRefs') or []:
            paragraphs.append(('img', ref))

    cursor = 0
    for kind, value in paragraphs:
        if kind == 'img':
            pieces.append(photo(value, 680 if cursor % 3 == 0 else 520))
            cursor += 1
        elif kind == 'h2':
            pieces.append('<section style="padding:72px 56px 0">'
                          '<h2 style="font-family:\'Songti SC\',\'STSong\',serif;font-size:40px;'
                          'margin:0 0 24px">' + esc(value) + '</h2></section>')
        else:
            bias = 'padding-left:120px' if cursor % 2 else 'padding-right:120px'
            pieces.append('<section style="padding:28px 56px;' + bias + '">'
                          '<p style="font-size:26px;line-height:1.95;margin:0">' + esc(value) + '</p></section>')

    # ③ 余下照片铺成通栏相册，收尾克制（不喊口号）
    left = [r for r in refs if r not in used]
    if left:
        pieces.append('<section style="padding:72px 56px 0">'
                      '<p style="font-size:15px;letter-spacing:6px;color:#A6633E;margin:0 0 24px">现 场 相 册</p>'
                      '</section>')
        for r in left:
            pieces.append(photo(r, 560))
    pieces.append('<section style="padding:96px 56px 120px;text-align:center">'
                  '<p style="font-family:\'Songti SC\',\'STSong\',serif;font-size:28px;'
                  'color:#29483B;margin:0">以上，是本场留下的现场记录。</p></section>')
    return apply_style(''.join(pieces))
