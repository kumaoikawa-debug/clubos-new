# -*- coding: utf-8 -*-
"""AI 宣传长图（longpic）—— 让模型直接交付成品版式。

═══ 为什么要有这个渠道（2026-10-07）═══
用户截图反馈：「AI 宣传中心的微信推文始终达不到想要的效果，能不能把 ClubOS 的要求减少，
直接连大模型，直接让大模型做一份宣传微信长图」。

诊断（拉线上真实数据核对）：**文案已经不差**（开场是具体时刻、小标题是判断句、中间插真实照片），
差的是**排版天花板**——`static/channel-render.js` 把模型产出翻译成 12 种 block，
每种只有一套死样式；模型只有「选哪块」的权力，没有「长什么样」的权力。
最典型的事故：facts（日期/集合/交通/费用包含/装备建议）被塞进 **5 列表格**，
而「费用包含」「装备建议」的值是长串清单 → 窄列套长文本，页尾变成一张巨大的米色电子表格。

═══ 这个渠道的做法 ═══
模型**直接交付整段 HTML**（`{title, html}`），前端原样渲成 750px 竖版长图，可复制图文、可下载 PNG。

═══ 2026-10-08 二次返工（用户第二次截图："跟直接用大模型生成的内容和排版、美观度都差太多了"）═══
上一版只给了「8 条硬规则 + 版式工具箱」，并宣称「结构/语气/版式/配色/留白/字号层级全部交给模型」。
实测结论：**把设计权完全交给模型 = 每篇都像学生的第一版作业**——区块都齐，
但字号层级随意、间距忽大忽小、图片时有时无、留白不成节奏，整体没有"设计感"。
本版把「交给模型自由发挥」改成**给一套可执行的设计系统 + 组件库（带 HTML 片段）+ 密度验收**：
  · 设计系统：画布 / 字号层级 / 配色（1 主色 + 中性阶 + 1 深色）/ 间距节奏 / 圆角 / 分割线，全部给死数值；
  · 组件库 10 个（Hero / 导语 / 数字条 / 小标题 / 编号日程 / 信息卡 / 图文节奏 / 引语 / 团队 / 深色收尾），
    每个都带**可直接照抄的 HTML 片段**（模型对代码示例的遵从率远高于文字描述）；
  · 密度验收：整篇至少 8 块、正文 1300~1800 字、有照片就用 3 张以上、不留大片空白。
仍保留的安全约束一条没少：
- 事实边界照旧由 `_fact_digest` + 文案契约把关（挖细节 ≠ 编细节）；
- 成本数据照旧过 `scrub_cost_text`（公众号主视觉上印一行「人均 ¥3,806」同样是事故）；
- 图片只能用**真实照片**：media 里 kind='logo' 的、以及 `image_caption` 判为不可用的
  （地图截图、别的活动的照片），根本不会出现在给模型的清单里。
"""
from __future__ import annotations
import json, re
from typing import Any

from ai_gateway import generate_json, record_mock_usage, GatewayResponse
from cost_guard import scrub_cost_text

# 图片占位符：模型只准写这个形态，出口会校验 ref 是不是真实存在
_PH = re.compile(r'\{\{\s*media\s*:\s*([A-Za-z0-9_\-]+)\s*\}\}')

_SYSTEM = """你是一位资深公众号内容主编兼视觉设计。老板给你一份活动方案和一批真实照片，
你要**直接交付可以发布的成品**：一段自带排版的 HTML，它会被原样渲染成一张 750px 宽的竖版长图，
也会被复制进公众号编辑器。你要像给一个气质干净、克制的户外俱乐部做设计那样工作，
标准是「一本轻杂志的跨页」，不是「把资料排上去」。"""

_PROMPT = """## 你的产出
一段 HTML 片段（不是整页），会被渲染成 750px 宽的长图。

## 八条硬规则（不可违反）
1. **不创造事实**：只能用资料里出现过的日期、价格、地点、人名、机构、资质、装备、菜品、数字。
   资料没写的一个字都不许补（尤其不许编资质、名额、折扣、评价、奖项、头衔）。
2. 图片只能这样写：`<img src="{{media:img_07}}" alt="" style="...">`。不要写任何其它 src，
   不要写 http 链接；清单里没有的 ref 一律不许用。
3. 成本、供应商、毛利、内部 SOP 一律不得出现。
4. **样式全部写在行内** `style="..."`；不要 `class`、不要 `<style>`、不要外链
   （公众号编辑器会把这两样剥掉）。
5. 只允许这些标签：`section div p h1 h2 h3 strong em img figure figcaption ul ol li blockquote hr span`。
6. 只输出一个 JSON 对象，两个键：title 与 html。html 里不要 `<html>/<head>/<body>`。
7. **首屏**必须是一张满幅大图 + 压在图上的标题与副标题；**结尾**必须是一块报名信息（深色块）。
8. 按 750px 宽设计（正文 17px / 行高 1.9）。

## 设计系统（这是「好看」与「业余」的分界线 —— 照这套数值走，不要自由发挥）
- **画布**：宽 750px；左右安全边距 40px；页面底色纯白。
- **字号层级**：首屏主标题 40~44px / 行高 1.25 / 字重 700；区块小标题 21~23px / 字重 700；
  正文 17px / 行高 1.9；强调数字 34~40px / 字重 700；注释与落款 13px；
  眉标（eyebrow）12px、`letter-spacing:2px`。
- **配色**：全篇只用 **1 个主色**（户外用低饱和墨绿 `#245743`）+ 中性色阶
  （正文 `#3f3f3f`、次要 `#8a8a8a`、分隔线 `#ececec`、浅底 `#f6f7f5`）
  + 一块深色 `#1d2b26`（**只用在收尾**）。不要彩虹、不要第二种彩色。
- **间距节奏**：区块之间 44~52px；区块内元素间距 16~28px；小标题与正文之间 12px。
  用 margin 造间距，**不要用空 `<p>` 或 `<br>` 凑高度**。
- **圆角**：卡片与配图 10px，全篇统一；满幅首屏图 0。
- **分割线**：`1px solid #ececec`，只用于数据条上下或大区块之间，不要每段都加线。

## 组件库（按活动实际有什么来选；**整篇至少用满 8 块**，不够就按资料补块）
**① 首屏 Hero（必做）**：满幅图 + 深色渐变遮罩 + 白字大标题 + 副标题（日期·地点）+ 一行眉标。
```
<figure style="position:relative;margin:0;line-height:0">
  <img src="{{media:img_02}}" alt="" style="width:100%;height:auto;display:block">
  <div style="position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.12),rgba(0,0,0,.62))"></div>
  <div style="position:absolute;left:40px;right:40px;bottom:38px;line-height:1.3;color:#fff">
    <div style="font-size:12px;letter-spacing:2px;opacity:.85;margin-bottom:12px">眉标一行</div>
    <div style="font-size:42px;font-weight:700">活动主标题</div>
    <div style="font-size:15px;margin-top:14px;opacity:.9">日期 · 地点</div>
  </div>
</figure>
```
**② 导语**：紧接首屏，2~3 句讲清「这是什么活动、为什么值得来」，要有这场活动独有的具体信息。
**③ 关键数字条（必做）**：3~4 个抓人的数字并排（数字≥34px、主色、标签小灰字）。
```
<div style="display:flex;border-top:1px solid #ececec;border-bottom:1px solid #ececec;padding:26px 0;margin:44px 0">
  <div style="flex:1;text-align:center;border-right:1px solid #ececec">
    <div style="font-size:36px;font-weight:700;color:#245743;line-height:1">5<span style="font-size:15px;margin-left:3px">公里</span></div>
    <div style="font-size:13px;color:#8a8a8a;margin-top:10px">徒步里程</div>
  </div>
  <div style="flex:1;text-align:center;border-right:1px solid #ececec">
    <div style="font-size:36px;font-weight:700;color:#245743;line-height:1">380<span style="font-size:15px;margin-left:3px">米</span></div>
    <div style="font-size:13px;color:#8a8a8a;margin-top:10px">累计爬升</div>
  </div>
  <div style="flex:1;text-align:center">
    <div style="font-size:36px;font-weight:700;color:#245743;line-height:1">50<span style="font-size:15px;margin-left:3px">分钟</span></div>
    <div style="font-size:13px;color:#8a8a8a;margin-top:10px">音疗时长</div>
  </div>
</div>
```
**④ 区块小标题**：写成判断句 / 信息句（如「10:30 进林区，边徒步边采集素材」），
不要「XX之旅」「XX招募」这类空词，也不要 2~6 字的泛化情绪词。
**⑤ 编号日程（资料有明确时刻就必做）**：01/02/03 + 时间 + 标题 + 1~2 句实际内容。
```
<div style="display:flex;gap:16px;margin:0 0 28px">
  <div style="font-size:14px;font-weight:700;color:#245743;line-height:1.9;min-width:28px">01</div>
  <div style="flex:1">
    <div style="font-size:17px;font-weight:600;line-height:1.5;margin:0 0 6px">08:30 集合出发</div>
    <div style="font-size:15px;color:#5a5a5a;line-height:1.85">「1~2 句这个环节实际发生什么」</div>
  </div>
</div>
```
**⑥ 信息卡**：集合 / 交通 / 费用包含 / 装备建议 → 浅底圆角卡 + 「·」圆点列表，
**绝对不要**做成横向多列表格（列一多、字一长就成一张难看的电子表格）。
```
<div style="background:#f6f7f5;border-radius:10px;padding:24px 26px;margin:28px 0">
  <div style="font-size:15px;font-weight:700;color:#245743;margin:0 0 12px">装备建议</div>
  <div style="font-size:15px;color:#5a5a5a;line-height:2">· 防水防滑徒步鞋<br>· 透气速干衣裤<br>· …</div>
</div>
```
**⑦ 图文节奏**：整幅大图 / 两张并排 / 纯卡片无图，**交替出现，同一种版式不连用两次**；
不要每节都「小标题 + 段落 + 一整幅图」，有疏有密才像一本杂志。
**⑧ 引语 / 强调块（可选）**：一句关键话用大字号 + 左侧 `3px` 主色竖线。
**⑨ 人物 / 团队（资料有领队 / 教练时）**：一行姓名 + 角色，只写「怎么带队」，不吹资质头衔。
**⑩ 深色收尾区（必做）**：`#1d2b26` 深底白字，放报名方式 / 名额 / 日期 / 集合 / 二维码位。
```
<section style="background:#1d2b26;color:#fff;padding:44px 40px">
  <div style="font-size:12px;letter-spacing:2px;opacity:.7">SIGN UP</div>
  <div style="font-size:24px;font-weight:700;margin:10px 0 18px">报名信息</div>
  <div style="font-size:15px;line-height:2;opacity:.92">日期：…<br>集合：…<br>名额：…</div>
</section>
```

## ★ 硬性验收（不满足就重做）
① 首屏满幅图压标题；② 关键数字条（3~4 个并排大数字、主色）；
③ 有明确时刻就用**编号日程**（绝不要 h2+段落平铺）；④ 费用包含 / 装备建议做成**信息卡**（禁表格）；
⑤ 深色收尾区；⑥ **整篇至少 8 个内容块、正文 1300~1800 字**，且每一块都必须有实际信息；
⑦ 全篇不留大片空白（区块靠 margin 紧凑相接，不要靠空段落撑开）。

## 选图
- **不要用**画面主体是文字 / logo / 海报 / PPT 页面 / 表格截图的图（哪怕它在清单里，
  这种图放进长图就像插了一页 PPT）；同一张照片不要用两次。
- 优先选画面里有人的、有真实环境的照片；首屏选最有现场感的那张。
- **清单里有可用照片时，至少用 3 张**（首屏 1 张 + 中段 2 张起），照片是长图的骨架。

## 篇幅
- 正文总量 1300~1800 字。不够就往资料里还有的具体安排上写（几点集合、车程多久、谁带队、
  要不要自备什么、雨天怎么办），**不要靠形容词和感慨凑字数**。

## 别写成 AI 腔
- 小标题不要用「XX之旅」「XX招募」或 2~6 字的泛化场景词 / 情绪词；要写判断句或信息句。
- 正文是**连贯段落**（每段 2~4 句、60~180 字），不要写成一行一句、靠换行装诗意的样子。
- 任何能原样搬到别的活动上用的形容词、感慨、四字套话，一律删掉。
- 每节至少含 1 个只有这场活动才有的具体事实（数字 / 专名 / 动作 / 时刻）。
- 与资料原文的连续重合不得超过 12 个字。

## 领队与保险这类信息，只能照实列，不要升格成卖点
- 领队一行只写**怎么带队**（人数、是否有随队医疗 / 专业向导），不要把内部花名、
  协会头衔、装备品牌签约（`SMA` / `Mammut 签约运动员` 之类）当卖点堆给消费者看 ——
  报名的人只关心「有没有人管、出了事怎么办」。
- 花名、简称一律**原样照抄**（资料写 `【笨笨】` 就写 `【笨笨】`），
  但**不要去猜、去补全、去纠正**资料里疑似写错的名字或缩写。
- 保险只写公司名与保障级别，不要展开成条款解释。

## 可用照片（ref｜横竖｜画面里实际有什么）
__MEDIA__

## 活动事实锚点（Activity Master）
__MASTER__

## 方案事实要点表（碎片，不是成句；事实必须与它一致）
__DIGEST__

严格 JSON，不要 Markdown，不要解释。"""

# 出口清洗：这些标签/属性即便提示词禁止，也一律从模型产物里剜掉。
# 模型的 HTML 会被塞进 iframe srcdoc 与剪贴板，不能带着脚本走。
_BAD_TAGS = re.compile(r'<\s*/?\s*(script|style|link|iframe|object|embed|form|input|button|video|audio|base|meta)\b[^>]*>',
                       re.I)
_EVENT_ATTR = re.compile(r'\son[a-z]+\s*=\s*"[^"]*"|\son[a-z]+\s*=\s*\'[^\']*\'|\son[a-z]+\s*=\s*[^\s>]+',
                         re.I)
_JS_URL = re.compile(r'(?:href|src)\s*=\s*"\s*(?:javascript|data|vbscript):[^"]*"\s*', re.I)


def sanitize_html(html: str) -> str:
    """把模型 HTML 收敛成「只能安全渲染」的子集。"""
    t = str(html or '')
    t = _BAD_TAGS.sub('', t)
    t = _EVENT_ATTR.sub('', t)
    t = _JS_URL.sub('', t)
    return t.strip()


def _used_refs(html: str) -> list[str]:
    seen: list[str] = []
    for m in _PH.finditer(html or ''):
        r = m.group(1)
        if r not in seen:
            seen.append(r)
    return seen


_IMG_ANY = re.compile(r'<img\b[^>]*>', re.I)


def _drop_unknown_refs(html: str, allowed: set[str]) -> tuple[str, list[str]]:
    """把不在白名单里的图片 ref 剔掉，返回 (清洗后的 html, 被剔掉的 ref)。

    ★ 踩过的坑（2026-10-07 首轮实测）：原先只把 {{media:未知ref}} 这段**文字**替换成空串，
      结果留下 `<img src="" ...>` 的破图标签 —— 老板看到的是一张裂图，不是没图。
      现在改成：**凡是 src 最终为空的 <img> 整个标签删掉**。
    """
    dropped: list[str] = []

    def repl(m: re.Match) -> str:
        ref = m.group(1)
        if ref in allowed:
            return m.group(0)
        dropped.append(ref)
        return ''

    html = _PH.sub(repl, html)
    # 无论上面怎么改，最后再扫一遍：src 为空的 <img> 一律整段删除
    html = _IMG_ANY.sub(lambda m: m.group(0) if not re.search(r'src\s*=\s*(["\'])\s*\1', m.group(0)) else '',
                        html)
    return html, dropped


_FIGURE_OPEN = re.compile(r'<figure\b[^>]*>', re.I)


def _ensure_hero(html: str, caption_lines: list[str], allowed: set[str]) -> str:
    """保证首屏有一张真实照片。

    ★ 2026-10-07 实测踩到的坑：模型给首屏挑的那张图**常被判为不可用**（它会挑最好看的，
      而最好看的往往正是混进方案里的别家活动照）。剔掉之后有两种烂尾：

      1. 整篇一张图都没有 → 顶部纯文字，不像成品；
      2. 更隐蔽的一种：模型常把标题用 `position:absolute` **压在图上**。图一删，
         那行白字标题就浮在白底上，**完全看不见** —— 肉眼以为"标题丢了"。

      所以这里不是"追加一张图"，而是**把图塞回首屏那个空 figure 内部**：
      模型写的 absolute 是相对 figure 定位的，图回去了，压图排版才恢复原样。
      找不到 figure 才退化成整块前置。
    """
    if not allowed:
        return html
    if _IMG_ANY.search(html[:600] if len(html) > 600 else html):
        return html  # 首屏本来就有图

    first = str(caption_lines[0]).split('｜', 1)[0].strip() if caption_lines else ''
    if not first or first not in allowed:
        return html
    img = (f'<img src="{{{{media:{first}}}}}" alt="" '
           f'style="width:100%;height:auto;display:block">')

    m = _FIGURE_OPEN.search(html)
    if m:
        # 插进 figure 的开头：紧跟 <figure ...> 之后，它就是该 figure 的第一张图
        at = m.end()
        return html[:at] + img + html[at:]

    hero = f'<figure style="margin:0 0 8px">{img}</figure>'
    return hero + html


async def generate_longpic(club_id: int, activity_master: dict[str, Any],
                           detail: dict[str, Any], *, cover_url: str | None = None,
                           source_text: str = '', caption_lines: list[str] | None = None,
                           digest: str = '') -> tuple[dict[str, Any], GatewayResponse]:
    """返回 ({"title","html","usedRefs"}, gateway_response)。"""
    master = activity_master if isinstance(activity_master, dict) else {}
    media_lines = '\n'.join(caption_lines or []) or '（本次活动没有可用照片，不要使用任何图片）'

    # 要点表由调用方算好传入；拿不到就退回原始资料（宁可承担照抄风险也要出稿）
    fact_note = (f'## 方案事实要点表（碎片，不是成句；事实必须与它一致）\n{digest}\n'
                 if digest else
                 f'## 原始方案资料（第一手资料，细节最全）\n{str(source_text or "")[:12000]}\n')

    prompt = (_PROMPT
              .replace('__MEDIA__', media_lines)
              .replace('__MASTER__', json.dumps(master, ensure_ascii=False)[:9000])
              .replace('__DIGEST__', fact_note))
    cover_note = f'活动官方封面（已上传的主视觉，可用作首图）：{cover_url}\n' if cover_url else ''

    gw = await generate_json(club_id=club_id, task_type='longpic',
                             system_prompt=_SYSTEM,
                             user_prompt=(cover_note + prompt) if cover_note else prompt)
    if not gw:
        return _mock(master, detail, cover_url), record_mock_usage(club_id, 'longpic', prompt,
                                                                   {'title': master.get('title', '')})

    data = gw.data if isinstance(gw.data, dict) else {}
    html = str(data.get('html') or data.get('body') or data.get('content') or '')
    html = sanitize_html(html)
    if not html:
        # 模型没给 html：交回给调用方，前端会提示重试，不落一个空成品
        return {}, gw

    # 只允许引用清单里出现过的 ref（= kind=photo、宽度达标、且视觉模型判为可用的那些）
    allowed = set()
    for line in (caption_lines or []):
        ref = str(line).split('｜', 1)[0].strip()
        if ref:
            allowed.add(ref)
    html, dropped = _drop_unknown_refs(html, allowed)
    html = _ensure_hero(html, caption_lines or [], allowed)

    # 成本闸门与详情共用一道：模型可能把内部报价写进主视觉
    html = scrub_cost_text(html)

    title = str(data.get('title') or master.get('title') or '活动宣传长图').strip()
    return {'title': title, 'html': html, 'usedRefs': _used_refs(html), 'droppedRefs': dropped}, gw


def _mock(master: dict[str, Any], detail: dict[str, Any], cover_url: str | None) -> dict[str, Any]:
    """mock 模式（演示 / 无模型配置）下的最小可用成品，避免内容中心出现空预览。"""
    title = str((master or {}).get('title') or '活动宣传长图')
    facts = []
    for k, label in (('date', '日期'), ('location', '地点')):
        v = str((master or {}).get(k) or '').strip()
        if v:
            facts.append(f'<p style="margin:0 0 8px;font-size:15px;color:#555;">{label}：{v}</p>')
    body = (f'<section style="padding:48px 32px;">'
            f'<h1 style="font-size:30px;line-height:1.35;margin:0 0 20px;">{title}</h1>'
            f'<div style="font-size:17px;line-height:1.9;color:#333;">{"".join(facts) or "AI 未接入，暂无法生成宣传长图。"}</div>'
            f'</section>')
    return {'title': title, 'html': body, 'usedRefs': []}
