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
提示词只留 8 条硬规则（不编事实 / 图片只用占位符 / 不出现成本 / 样式必须行内 /
只用白名单标签 / 只输出 JSON / 首屏大图 / 按 750px 设计），
**结构、语气、版式、配色、留白、字号层级全部交给模型**。
出口返回 `{title, html}`，前端把 html 渲成 750px 竖版长图，可复制图文、可下载 PNG。

同场活动的其他安全约束一条没少：
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
也会被复制进公众号编辑器。你要像给一个气质干净、克制的户外俱乐部做设计那样工作。"""

_PROMPT = """## 你的产出
一段 HTML 片段（不是整页），会被渲染成 750px 宽的长图。

## 只有这 8 条硬规则，其余（结构/语气/版式/配色/留白/字号层级）全部由你自己决定
1. **不创造事实**：只能用资料里出现过的日期、价格、地点、人名、机构、资质、装备、菜品、数字。
   资料没写的，一个字都不许补（尤其不许编资质、名额、折扣、评价）。
2. 图片只能这样写：`<img src="{{media:img_07}}" alt="" style="...">`。
   不要写任何其它 src，不要写 http 链接。
3. 成本、供应商、毛利、内部 SOP 一律不得出现。
4. **样式全部写在行内** `style="..."`；不要 `class`、不要 `<style>`、不要外链
   （公众号编辑器会把这两样剥掉）。
5. 只允许这些标签：`section div p h1 h2 h3 strong em img figure figcaption ul ol li blockquote hr span`
6. 只输出一个 JSON 对象，两个键：title 与 html。html 里不要 `<html>/<head>/<body>`。
7. 正文 1000~1500 字。**首屏必须是一张大图 + 压在图上的标题**；结尾必须给报名信息。
8. 按 750px 宽设计：正文 17px、行高 1.9；靠留白和层级制造呼吸感，**不要满屏堆字**。

## 版式手法（**以下四块都必须出现**，缺一不可；不要只挑其中几样，也不要退回成"小标题+段落"平铺）
下面 ① 数据条、② 编号日程 是**硬性骨架**（数字/文字换成这场活动的），③④⑤ 也必须做：

**① 关键数字条**（判断句小标题之后紧接一条，几乎每篇都该有）：把资料里最抓人的 3~4 个数字并排放，
数字大、标签小。凑不满 3 个才跳过。
```
<div style="display:flex;border-top:1px solid #e8e8e8;border-bottom:1px solid #e8e8e8;padding:26px 0;margin:0 0 44px">
  <div style="flex:1;text-align:center;border-right:1px solid #eee">
    <div style="font-size:36px;font-weight:700;color:#245743;line-height:1">5<span style="font-size:16px;margin-left:3px">公里</span></div>
    <div style="font-size:13px;color:#999;margin-top:9px">徒步里程</div>
  </div>
  <div style="flex:1;text-align:center;border-right:1px solid #eee">
    <div style="font-size:36px;font-weight:700;color:#245743;line-height:1">380<span style="font-size:16px;margin-left:3px">米</span></div>
    <div style="font-size:13px;color:#999;margin-top:9px">累计爬升</div>
  </div>
  <div style="flex:1;text-align:center">
    <div style="font-size:36px;font-weight:700;color:#245743;line-height:1">50<span style="font-size:16px;margin-left:3px">分钟</span></div>
    <div style="font-size:13px;color:#999;margin-top:9px">颂钵音疗</div>
  </div>
</div>
```
**② 编号节点**（把一天的几个环节排成"节目单"，比四段并列正文好读得多）：
```
<div style="display:flex;gap:16px;margin:0 0 34px">
  <div style="font-size:15px;font-weight:700;color:#245743;line-height:1.7;min-width:30px">01</div>
  <div style="flex:1">
    <div style="font-size:19px;font-weight:600;margin:0 0 8px">10:30 林间徒步与植物采集</div>
    <div style="font-size:16px;color:#555;line-height:1.85">「这里写 1~2 句这个环节实际发生什么」</div>
  </div>
</div>
```

**③ 信息卡**：集合 / 交通 / 费用包含 / 装备建议放进带浅色底与圆角的卡片，用「·」圆点列表，**绝对不要**做成横向多列表格（列一多字一长就成一张难看的电子表格）。
**④ 图文节奏**：不要每节都"小标题 + 段落 + 一整幅图"地机械重复 —— 有的一节配整幅大图、有的两张并排、有的只放卡片不配图，**有疏有密**才像一本杂志。
**⑤ 收尾区（硬性）**：报名信息单独成块，用**深底白字**（如 `#1d2b26` 深绿底 + 白字）明显区别于正文，不要只用浅底左边框。
**★ 硬性清单（不满足就重做）**：① 数据条必须出现（3~4 个并排大数字，数字≥32px、主色 #245743）；② 当日日程必须用**编号节点**（时间戳 + 标题 + 1~2 句短描述），用本活动真实时刻（如 08:30 / 10:30 / 13:30）填，绝不要写成 h2+段落；③ 费用包含 / 装备建议必须做成信息卡（圆点列表，禁表格）；④ 报名信息必须做成深色收尾区。

## 选图
- **不要用**画面主体是文字/logo/海报/PPT 页面/表格截图的图（哪怕它就在清单里，
  这种图放进长图就像插了一页 PPT）；也不要同一张照片用两次。
- 优先选画面里有人的、有真实环境的照片；首屏选最有现场感的那张。

## 篇幅
- 正文总量 1200 字上下。不够就往资料里还有的具体安排上写（几点集合、车程多久、谁带队、
  要不要自备什么、雨天怎么办），**不要靠形容词和感慨凑字数**。

## 配色（收敛，不要彩虹）
- 全篇最多 2 个色相：一个主色（标题 / 编号 / 竖线 / 强调）+ 一个中性色。
  户外题材建议主色用低饱和的墨绿（如 `#245743`、`#2f6b52`）。
- 正文用近黑灰（`#333` / `#3f3f3f`），页面底色保持白或极浅灰。
- 只允许收尾区出现一块深色；其余地方靠留白和字号层级造层次，不靠色块。

## 领队与保险这类信息，只能照实列，不要升格成卖点
- 领队一行只写**怎么带队**（人数、是否有随队医疗/专业向导），不要把内部花名、
  协会头衔、装备品牌签约（`SMA`/`Mammut 签约运动员` 之类）当卖点堆给消费者看 ——
  报名的人只关心"有没有人管、出了事怎么办"。
- 花名、简称一律**原样照抄**（资料写 `【笨笨】` 就写 `【笨笨】`），
  但**不要去猜、去补全、去纠正**资料里疑似写错的名字或缩写。
- 保险只写公司名与保障级别，不要展开成条款解释。

## 可用照片（ref｜横竖｜画面里实际有什么）
__MEDIA__

## 活动事实锚点（Activity Master）
__MASTER__

## 方案事实要点表（碎片，不是成句；事实必须与它一致）
__DIGEST__

## 别写成 AI 腔
- 小标题不要用「XX之旅」「XX招募」或 2~6 字的泛化场景词/情绪词；要写判断句或信息句。
- 正文是**连贯段落**（每段 2~4 句、60~180 字），不要写成一行一句、靠换行装诗意的样子。
- 任何能原样搬到别的活动上用的形容词、感慨、四字套话，一律删掉。
- 每节至少含 1 个只有这场活动才有的具体事实（数字/专名/动作/时刻）。
- 与资料原文的连续重合不得超过 12 个字。

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