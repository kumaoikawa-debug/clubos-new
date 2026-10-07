# -*- coding: utf-8 -*-
"""AI 宣传长图（longpic）—— 模型填内容，模板管排版。

═══ 演进史（每一版都是被用户打回来改的，留着免得再走回头路）═══
▸ v1（2026-10-07）：「让模型直接交付整段 HTML」。想法是"把设计权还给模型"。
▸ v2（2026-10-08 上午）：用户第二次截图「跟直接用大模型生成的差太多了」。
  诊断：**把设计权完全交给模型 = 每篇都像学生的第一版作业**（字号层级随意、间距忽大忽小）。
  于是给「设计系统 + 10 个组件库（带 HTML 片段）+ 密度验收」。有效，但仍是**模型自由排版**，
  版式上限取决于模型当天状态，同一份资料两次生成都不一样。
▸ v3（2026-10-08 晚，本版）：用户给了两份**标杆成品**并要求「学习这种排版方式…**避免乱排**」。
  拆开标杆源码发现决定性事实：**标杆不是模型排的，是一套手写模板** ——
  一份固定 CSS（十来个类）+ 两套配色主题 + 十来个固定组件，内容只是按组件填字段。
  质量差距的根因不是"提示词不够好"，而是**架构不对**：排版该是代码的责任，不是模型的。

故本版把排版权**收回**（架构见 `longpic_template.py`）：

    模型 → 结构化内容 JSON（theme / hero / sections[blocks] / signup）
    代码 → 用固定模板渲染 HTML（含 `<style>`，class 化）

好处：
  ① 版式不再随模型心情波动 —— 同一组件永远长同一个样，这就是"不乱排"的定义；
  ② 模型只需操心「写什么」，不用操心「怎么排」，反而写得更好（不必分心算字号）；
  ③ 安全面大幅收窄：模型不能再产出任意 HTML/属性，只能往预置字段填**纯文本**
     （`longpic_template` 统一 `html.escape`）。

安全与事实纪律（逐条保留，一条没少）：
- 事实边界由 `_fact_digest` + 文案契约把关（挖细节 ≠ 编细节）；
- 成本数据过 `scrub_cost_text`（公众号主视觉上印一行「人均 ¥3,806」同样是事故）；
- 图片只能用**真实照片**：media 里 kind='logo' 的、`image_caption` 判为不可用的
  （地图截图、别的活动的照片）根本不会出现在给模型的清单里；出口再按白名单二次过滤。
"""
from __future__ import annotations
import json, re
from typing import Any

import longpic_template as T
from ai_gateway import generate_json, record_mock_usage, GatewayResponse
from cost_guard import scrub_cost_text

# 图片占位符：模板只输出这个形态，出口会校验 ref 是否真实存在
_PH = re.compile(r'\{\{\s*media\s*:\s*([A-Za-z0-9_\-]+)\s*\}\}')

_SYSTEM = """你是一位资深公众号内容主编。老板给你一份活动方案和一批真实照片，
你要写出一篇**能直接发布的长图内容**：它会被套进一套已经定好的版式里（你不要管排版），
所以你只需要专心把「写什么」做到最好 —— 像给一个气质干净、克制的户外俱乐部写文案那样，
标准是「一本轻杂志的跨页」，不是「把资料填进表格」。"""

# ══════════════════════════════════════════════════════════════════════
# 内容 JSON 契约（模型只填内容；版式由 longpic_template 负责）
# ══════════════════════════════════════════════════════════════════════
_PROMPT = """## 你要做什么
把这场活动的资料，写成一份**长图内容 JSON**。系统会把它套进固定版式渲染成 750px 宽的长图。
**你只管内容，一个字的 HTML / CSS / 样式都不要写** —— 版式是系统的事，你写了反而会出错。

## 八条硬规则（不可违反）
1. **不创造事实**：只能用资料里出现过的日期、价格、地点、人名、机构、资质、装备、菜品、数字。
   资料没写的一个字都不许补（尤其不许编资质、名额、折扣、评价、奖项、头衔）。
   资料**本身**就不确定的（例如日期只写「10月」），把那个字段**留空**，不要写 `10.xx`、`待定`、`TBD`
   这类占位符 —— 它们会被原样印在成品上。
2. **图片只能用清单里的 ref**，写成字符串 `"img_07"`（不要写成 URL、不要加 `{{}}`）。
   清单里没有的 ref 一律不许用；同一张照片不要用两次。
3. 成本、供应商、毛利、内部 SOP 一律不得出现。
4. 只输出**一个 JSON 对象**，不要 Markdown 代码围栏，不要解释文字。
5. 所有文本字段都是**纯文本**（可以含换行 `\\n`）：不要写 `<b>` `<br>` 之类的标签。
6. **首屏 hero 与结尾 signup 必填**；`sections` 至少 5 节。
7. 数字（里程/爬升/时长/价格/人数）**照资料原样写**，不要估算、不要换算。
8. 图片清单里有可用照片时，全篇**至少用 4 张不同的**。

## JSON 结构（严格按这个来；带 * 的必填）
```json
{
  "title": "内部用的成品标题（活动名 · 宣传长图）",
  "theme": "paper 或 night（见下方选色规则）",
  "hero": {
    "brand": "顶部品牌行，如 icebreaker × 远拓户外（资料有才写）",
    "chip": "标题上方的小胶囊，一句定位，如「Natural Club · 品牌社群活动」",
    "title": "主标题：**每行 6~8 个字**（硬性，超过 9 个字会挤到下一行、版式就垮了），用 \\n 分成两行。写成有画面感的两小句",
    "accent": "主标题里要**高亮**的那 3~6 个字（必须是 title 里原样出现的一段）",
    "sub": "副标题，一行亮出核心玩法（如「森林徒步 × 颂钵音疗 × 自然拓染」）+ 一行地点",
    "date": "日期，只在资料里有**确切日期**时填（如 2026.09.19）；资料只写月份或写着「待定」就留**空字符串** —— 印在成品上的日期绝不能是 `10.xx` 这类占位符",
    "meta": "两行小字：第一行天数/往返，第二行地点链条（用 \\n 分）",
    "enLoc": "一行全大写英文地名，营造杂志感",
    "media": "首屏要用的最有现场感的照片 ref"
  },
  "sections": [
    {
      "eyebrow": "01 — VENUE",
      "title": "本节标题，**不超过 14 个字**（超了会只剩一个字掉到第二行，很难看）；要两行就用 \\n 显式断开，且两行字数接近（如 7/7），写成判断句或信息句",
      "lead": "本节导语 1~3 句（60~140 字），要有这场活动独有的具体信息",
      "blocks": [ ...见下方积木... ],
      "note": "可选：小字备注（如「具体行程以实际为准」）"
    }
  ],
  "signup": {
    "enRoll": "顶上一行英文小字，如 JOIN NATURAL CLUB",
    "title": "收尾大标题，两行，如「带走一身装备\\n和一天的森林」",
    "sub": "一行英文/日期落款",
    "form": [{"k":"时间","v":"2026 年 9 月 19 日（周六）","note":"1 天往返"}],
    "product": {"media":["img_20","img_21"],"price":"669","unit":"/ 人","inc":["包含内容 1","包含内容 2"]},
    "footBrand": "底部品牌行",
    "footEn": "最底一行全大写英文"
  }
}
```

## blocks 积木（每节里挑 1~3 个拼，**不要每节都一样**，整篇要有疏有密）
- `{"type":"stats","items":[{"v":"5 公里","k":"徒步里程"},{"v":"380 米","k":"累计爬升"}]}`
  关键数字并排（v 是 **数字+单位**，k 是 2~4 字标签）。1 节最多 4 个。
- `{"type":"params","items":[{"k":"形式","v":"徒步 + 颂钵冥想","note":"非原路往返"}]}`
  「左标签右内容」的参数行（形式/距离/爬升/路况/强度/温度/天气这类）。1 节最多 8 行。
- `{"type":"chips","items":["成都往返 1 天","大巴包车","130KM"]}` 圆角胶囊小标签。
- `{"type":"photos","items":[{"ref":"img_05","caption":"兴福寺 · 邛崃火井镇","size":"tall"}]}`
  照片。给 **1 张** 则整幅（`size`: `full` 中幅 / `tall` 竖幅 / `wide` 横幅）；
  给 **2 张** 则并排；给 **3 张** 则 1 大 2 小。同一个 items 里最多 3 张。
- `{"type":"timeline","items":[{"time":"08:30","what":"仁和新城集合出发","note":"大巴包车"}]}`
  **资料有明确时刻就用它**（替代「小标题+段落」平铺）。1 节最多 12 条。
- `{"type":"steps","items":[{"no":"01","title":"森林徒步","desc":"石笋寺—兴福寺 5 公里","tag":"5KM · 380M 爬升"}]}`
  圆形序号卡，用于「一天里的几种体验」「几件要准备的事」。1 节最多 6 个。
- `{"type":"team","items":[{"name":"野人","role":"中登协中级户外指导员","media":"img_09"}]}`
  团队成员（姓名 + 角色）。**`media` 只有在清单里明确写着那是「人物 / 教练 / 导师的正面或半身照」时才填**，
  清单里没有人物照就**不要填 media**（系统会显示姓氏首字，比把风景照塞进头像位体面）。1 节最多 8 人。
- `{"type":"quote","text":"一句值得单独放大、留在心里的话"}` 引语块（整篇最多 1~2 次）。

## 选色规则（theme）
- `"night"`：日照金山 / 星空 / 雪山 / 高原 / 藏地 / 唐卡 / 篝火 / 夜间 —— 与"暗调画面"相配；
- `"paper"`：其余全部（白天户外 / 森林 / 徒步 / 城市周边 / 古镇）—— **拿不准就用 paper**。

## 内容纪律（这些是"像人写的"与"像 AI 写的"的分界线）
- 小标题不要用「XX之旅」「XX招募」或 2~6 字的泛化情绪词（「日落之前」「山野之约」）。
  要写判断句 / 信息句（「10:30 进林区，边徒步边采集素材」）。
- 正文是**连贯段落**（60~140 字），不要一行一句靠换行装诗意。
- 任何能原样搬到别的活动上用的形容词、感慨、四字套话，一律删掉。
- 每节至少含 1 个**只有这场活动才有**的具体事实（数字 / 专名 / 动作 / 时刻）。
- 与资料原文的连续重合不得超过 12 个字（不要照抄方案原文）。
- **篇幅**：全篇正文合计 900~1500 字；不够就往资料里还有的具体安排上写
  （几点集合、车程多久、谁带队、要不要自备什么、雨天怎么办），**不要靠形容词凑**。

## 领队与保险这类信息，只能照实列，不要升格成卖点
- 领队只写**怎么带队**（人数、是否有随队医疗 / 专业向导），不要把内部花名、
  协会头衔、装备品牌签约（`SMA` / `Mammut 签约运动员` 之类）当卖点堆给消费者看 ——
  报名的人只关心「有没有人管、出了事怎么办」。
- 花名、简称一律**原样照抄**（资料写 `【笨笨】` 就写 `【笨笨】`），
  **不要去猜、去补全、去纠正**资料里疑似写错的名字或缩写。
- 保险只写公司名与保障级别，不要展开成条款解释。

## 可用照片（ref｜横竖｜画面里实际有什么）
__MEDIA__

## 活动事实锚点（Activity Master）
__MASTER__

## 方案事实要点表（碎片，不是成句；事实必须与它一致）
__DIGEST__

严格 JSON，不要 Markdown，不要解释。"""

# ── 回退用：模型结构化输出失败时，退回"直出 HTML"老路（见文件头 v1/v2 说明）──
_FALLBACK_SYSTEM = _SYSTEM
_FALLBACK_PROMPT = """## 你的产出
一段 HTML 片段（不是整页），会被渲染成 750px 宽的长图。

## 硬规则
1. **不创造事实**：只能用资料里出现过的日期、价格、地点、人名、机构、资质、装备、菜品、数字。
2. 图片只能这样写：`<img src="{{media:img_07}}" alt="" style="width:100%;display:block">`。
   不要写任何其它 src，不要写 http 链接；清单里没有的 ref 一律不许用。
3. 成本、供应商、毛利、内部 SOP 一律不得出现。
4. **样式全部写在行内** `style="..."`；不要 `class`、不要 `<style>`、不要外链。
5. 只允许这些标签：`section div p h1 h2 h3 strong em img figure figcaption ul ol li blockquote hr span`。
6. 只输出一个 JSON 对象，两个键：title 与 html。html 里不要 `<html>/<head>/<body>`。
7. **首屏**必须是一张满幅大图压标题；**结尾**必须是一块深色报名信息区。
8. 按 750px 宽设计（正文 17px / 行高 1.9）。

## 版式要求（照这套数值走）
- 画布 750px，左右边距 40px，底色纯白。
- 首屏主标题 40~44px/700；区块小标题 21~23px/700；正文 17px/1.9；强调数字 34~40px/700；
  眉标 12px + `letter-spacing:2px`。
- 配色：1 主色（墨绿 `#245743`）+ 中性阶（正文 `#3f3f3f`、次要 `#8a8a8a`、分隔线 `#ececec`、
  浅底 `#f6f7f5`）+ 一块深色 `#1d2b26`（只用于收尾）。不要第二种彩色。
- 区块间 44~52px，圆角统一 10px，分割线只用于数据条或大区块之间。
- **整篇至少用满 8 块**，正文 1300~1800 字，有照片至少用 3 张，不留大片空白。
- **绝对不要**把「费用包含/装备建议」做成横向多列表格，用圆点列表或浅底信息卡。

## 选图
不要用画面主体是文字 / logo / 海报 / PPT 页面 / 表格截图的图；同一张照片不要用两次。

## 别写成 AI 腔
- 小标题不要用「XX之旅」「XX招募」或泛化情绪词；要写判断句或信息句。
- 正文是连贯段落（每段 2~4 句、60~180 字），不要一行一句。
- 与资料原文的连续重合不得超过 12 个字。

## 可用照片（ref｜横竖｜画面里实际有什么）
__MEDIA__

## 活动事实锚点（Activity Master）
__MASTER__

## 方案事实要点表（碎片，不是成句；事实必须与它一致）
__DIGEST__

严格 JSON，不要 Markdown，不要解释。"""

# 出口清洗：这些标签/属性即便提示词禁止，也一律从产物里剜掉。
# 内容会被塞进 iframe srcdoc 与剪贴板，不能带着脚本走。
_BAD_TAGS = re.compile(r'<\s*/?\s*(script|link|iframe|object|embed|form|input|button|video|audio|base|meta)\b[^>]*>',
                       re.I)
_EVENT_ATTR = re.compile(r'\son[a-z]+\s*=\s*"[^"]*"|\son[a-z]+\s*=\s*\'[^\']*\'|\son[a-z]+\s*=\s*[^\s>]+',
                         re.I)
_JS_URL = re.compile(r'(?:href|src)\s*=\s*"\s*(?:javascript|data|vbscript):[^"]*"\s*', re.I)


def sanitize_html(html: str) -> str:
    """把**模型直出**的 HTML 收敛成「只能安全渲染」的子集。

    ★ 注意：本函数只用于回退路径（模型直出 HTML）。模板渲染出来的 HTML 是**我们自己的**，
      含 `<style>`，绝不能过这里（会被剜掉样式）。
    """
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
    html = _IMG_ANY.sub(lambda m: m.group(0) if not re.search(r'src\s*=\s*(["\'])\s*\1', m.group(0)) else '',
                        html)
    return html, dropped


def _allowed_refs(caption_lines: list[str] | None) -> set[str]:
    allowed: set[str] = set()
    for line in (caption_lines or []):
        ref = str(line).split('｜', 1)[0].strip()
        if ref:
            allowed.add(ref)
    return allowed


# ══════════════════════════════════════════════════════════════════════
# 结构化内容 doc 的提取与校验
# ══════════════════════════════════════════════════════════════════════
def _extract_doc(data: Any) -> dict | None:
    """从模型返回里找出那份内容 doc。容错：模型有时把它包在 doc/content/longpic 键下。"""
    if not isinstance(data, dict):
        return None
    for key in ('doc', 'content', 'longpic', 'result', 'data'):
        v = data.get(key)
        if isinstance(v, dict) and ('hero' in v or 'sections' in v):
            return v
    if 'hero' in data or 'sections' in data:
        return data
    return None


def _doc_quality(doc: dict, allowed: set[str]) -> dict[str, int]:
    """渲染一遍并数组件 —— 用于判断"这份内容是丰满的还是敷衍的"。"""
    try:
        html = T.render(doc, allowed)
    except Exception:
        return {}
    return T.structure_report(html)


def _doc_ok(rep: dict[str, int], has_photos: bool) -> tuple[bool, str]:
    """验收：标杆水准的判断标准（不达标就回退到直出 HTML 老路）。

    这些阈值来自两份标杆成品的实测结构（hero + 7~8 节 + 深色收尾 + 十来个组件）。
    """
    if not rep:
        return False, 'render failed'
    if rep.get('hero', 0) < 1:
        return False, 'no hero'
    if rep.get('signup', 0) < 1:
        return False, 'no signup'
    if rep.get('sections', 0) < 5:
        return False, 'sections=%d < 5' % rep.get('sections', 0)
    rich = sum(rep.get(k, 0) for k in ('stats', 'params', 'timeline', 'steps', 'team', 'quote', 'chips'))
    if rich < 4:
        return False, 'component blocks=%d < 4' % rich
    if has_photos and rep.get('images_used', 0) < 3:
        return False, 'images=%d < 3' % rep.get('images_used', 0)
    return True, 'ok'


# ══════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════
async def generate_longpic(club_id: int, activity_master: dict[str, Any],
                           detail: dict[str, Any], *, cover_url: str | None = None,
                           source_text: str = '', caption_lines: list[str] | None = None,
                           digest: str = '') -> tuple[dict[str, Any], GatewayResponse]:
    """返回 ({"title","html","usedRefs","droppedRefs","route"}, gateway_response)。"""
    master = activity_master if isinstance(activity_master, dict) else {}
    media_lines = '\n'.join(caption_lines or []) or '（本次活动没有可用照片，不要使用任何图片）'
    fact_note = (f'## 方案事实要点表（碎片，不是成句；事实必须与它一致）\n{digest}\n'
                 if digest else
                 f'## 原始方案资料（第一手资料，细节最全）\n{str(source_text or "")[:12000]}\n')

    def _fill(tpl: str) -> str:
        return (tpl.replace('__MEDIA__', media_lines)
                   .replace('__MASTER__', json.dumps(master, ensure_ascii=False)[:9000])
                   .replace('__DIGEST__', fact_note))

    allowed = _allowed_refs(caption_lines)
    has_photos = bool(allowed)
    cover_note = f'活动官方封面（已上传的主视觉，可用作首图）：{cover_url}\n' if cover_url else ''

    # ── 主路径：模型输出内容 JSON → 模板渲染 ──
    gw = await generate_json(club_id=club_id, task_type='longpic',
                             system_prompt=_SYSTEM,
                             user_prompt=(cover_note + _fill(_PROMPT)))
    if not gw:
        return _mock(master, detail, cover_url), record_mock_usage(club_id, 'longpic', _PROMPT,
                                                                   {'title': master.get('title', '')})

    data = gw.data if isinstance(gw.data, dict) else {}
    doc = _extract_doc(data)
    html, route, dropped = '', 'template', []

    if doc:
        rep = _doc_quality(doc, allowed)
        ok, why = _doc_ok(rep, has_photos)
        if ok:
            html = T.render(doc, allowed, cover_url)
        # 质量不够：如果模型同时给了现成 html 就用它，否则走回退
        if not html:
            cand = str(data.get('html') or data.get('body') or '')
            if cand and len(cand) > 400:
                html, route = sanitize_html(cand), 'model-html'
            else:
                route = 'fallback(%s)' % why

    if not html:
        # ── 回退路径：模型直出 HTML（老实现，保证"有产出"）──
        gw2 = await generate_json(club_id=club_id, task_type='longpic',
                                  system_prompt=_FALLBACK_SYSTEM,
                                  user_prompt=(cover_note + _fill(_FALLBACK_PROMPT)))
        if gw2 and isinstance(gw2.data, dict):
            cand = str(gw2.data.get('html') or gw2.data.get('body') or gw2.data.get('content') or '')
            cand = sanitize_html(cand)
            if cand:
                html, route, gw = cand, 'model-html', gw2
                data = gw2.data

    if not html:
        # 两条路都没产出：交回给调用方，前端会提示重试，不落一个空成品
        return {}, gw

    # 出口闸门（两条路径共用）
    html, dropped = _drop_unknown_refs(html, allowed)
    if route == 'model-html':
        html = _ensure_hero(html, caption_lines or [], allowed)
    html = scrub_cost_text(html)

    title = str(data.get('title') or master.get('title') or '活动宣传长图').strip()
    return {'title': title, 'html': html, 'usedRefs': _used_refs(html),
            'droppedRefs': dropped, 'route': route}, gw


_FIGURE_OPEN = re.compile(r'<figure\b[^>]*>', re.I)


def _ensure_hero(html: str, caption_lines: list[str], allowed: set[str]) -> str:
    """（回退路径专用）保证首屏有一张真实照片。

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
        at = m.end()
        return html[:at] + img + html[at:]

    hero = f'<figure style="margin:0 0 8px">{img}</figure>'
    return hero + html


def _mock(master: dict[str, Any], detail: dict[str, Any], cover_url: str | None) -> dict[str, Any]:
    """mock 模式（演示 / 无模型配置）下的最小可用成品，避免内容中心出现空预览。

    用真实模板渲染，所以演示环境也能看到成品版式（而不是一段裸文字）。
    """
    title = str((master or {}).get('title') or '活动宣传长图')
    doc = {
        'title': title,
        'theme': 'paper',
        'hero': {
            'brand': 'ClubOS · 活动宣传',
            'chip': 'AI 宣传长图',
            'title': title,
            'sub': str((master or {}).get('location') or ''),
            'date': str((master or {}).get('date') or ''),
            'meta': '演示模式 · 未接入模型时不生成真实内容',
            'enLoc': 'CLUBOS ACTIVITY',
        },
        'sections': [{
            'eyebrow': '01 — PREVIEW',
            'title': 'AI 未接入，这是版式预览。',
            'lead': '配置好模型后，这里会被真实的行程、数字与照片填满。',
            'blocks': [{'type': 'params', 'items': [
                {'k': '日期', 'v': str((master or {}).get('date') or '—')},
                {'k': '地点', 'v': str((master or {}).get('location') or '—')},
            ]}],
        }],
        'signup': {'enRoll': 'JOIN US', 'title': '报名信息', 'form': []},
    }
    html = T.render(doc, set(), cover_url)
    return {'title': title, 'html': html, 'usedRefs': [], 'route': 'mock'}
