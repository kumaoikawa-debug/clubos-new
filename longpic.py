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
import json, re, traceback
from typing import Any

import longpic_template as T
from ai_gateway import generate_json, record_mock_usage, GatewayResponse
from cost_guard import scrub_cost_text, is_cost_row

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
> ‼️ **本说明里出现的一切具体文字（品牌名、社群名、地名、日期、金额、菜品、人名等）都只是「格式示意」，绝对不许出现在成品里。**
> 成品里每一个具体信息都必须来自上方真实资料；凡你自己不知道该填什么，就用本场资料里的说法，**不要搬示例**。
> 尤其 `hero.chip` / `signup.enRoll` 这类"定位语"，每场活动都要现写一套，不许套用任何固定说法。

## JSON 结构（严格按这个来；带 * 的必填）
```json
{
  "title": "内部用的成品标题（活动名 · 宣传长图）",
  "theme": "paper 或 night（见下方选色规则）",
  "hero": {
    "brand": "顶部品牌行：主办方 / 联名品牌（形如「A × B」）。资料里有真实联名品牌就写它；**没有联名就把「主办俱乐部」的名字写进来**；两者都没有才留空。不要照抄示例里的任何品牌名",
    "chip": "标题上方的小胶囊：**本场活动专属**的一句定位（约 8~14 字），形如「主办方 · 活动性质」。主办方用资料里的联名品牌或「主办俱乐部」名，活动性质取本场玩法（徒步 / 探洞 / 颂钵 / 唐卡…）。**每场活动的 chip 都必须由你自己现写、彼此不同**；严禁复用本说明里的措辞，也严禁把**任何别的活动 / 社群的名字**搬进来",
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
    "enRoll": "顶上一行英文小字，2~4 个词，与本场活动定位对应（如 JOIN US / EXPLORE THE WILD）。**必须自己现写，不要照抄任何示例文字，也不要写成别的活动的名字**",
    "title": "收尾大标题，两行，如「带走一身装备\\n和一天的森林」",
    "sub": "一行英文/日期落款",
    "form": [{"k":"时间","v":"2026 年 9 月 19 日（周六）","note":"1 天往返"}],
    "product": {"media":["img_20","img_21"],"price":"669","unit":"/ 人","inc":["包含内容 1","包含内容 2"]},
    "footBrand": "底部品牌行",
    "footEn": "最底一行全大写英文"
  }
}
```
> `product` **可省略**：只有资料里写了**对外售价 / 会员价**时才给，且 `price` 必须是资料上那个正数
> （**不确定就不要写、不要填 0 或占位符** —— 成品上出现「¥0 / 人」比不写价格糟得多）。
> `inc` 只抄资料里**写给客人的**「费用包含」条目；**不要**把内部成本明细
> （人天成本、车费单价、工作餐、物料费、服务费之类）搬进来。

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
- `{"type":"prices","items":[{"label":"10月24日 · 标准团","date":"2026-10-24","price":"498","unit":"/ 人","note":"余 13 位"}]}`
  **团期 / 价格表**。下方「报名必用数据」里有 occurrences 就**必须**用它，多个团期全部列出；
  `price` 只写真实正数（若某团期价格为 0 或不明，就别写 price 字段改用 note 说明）。
- `{"type":"fee","inc":["户外牛肉汤锅午餐","专业领队服务"],"exc":["个人消费","往返大交通"]}`
  **费用包含 / 不含**（双栏）。下方数据里有 `fees` 就必须用它；
  `inc` / `exc` 只抄写给客人的条目，**内部成本明细（车费单价、工作餐、物料费、服务费）一条都不要进**。
- `{"type":"kit","title":"自备装备","items":["防水防滑徒步鞋","透气速干衣裤","登山杖"]}`
  **出行装备清单**。数据里有 checklist 就用它（最多 14 条，照抄不改写）。

## 报名必用数据（系统从报名系统 / 活动方案里取出的**真实数据**，不是示例）
__FACTS__

## ★ 顾客下单前必须看到的信息（缺一块，这张图就不合格）
下面 5 项，**只要上方数据里有，就必须写进成品**，且**只能照抄上面的数据**、不得改写数字或编补：
1. **团期与价格** → 用 `prices` 组件逐个列出（label / 日期 / 价格 / 余位都要用真值）。
2. **费用包含 / 不含** → 用 `fee` 组件照抄 `fees` 里的条目。
3. **自备装备** → 用 `kit` 组件照抄 `checklist`。
4. **带队阵容** → 用 `team`（有姓名/资质时）或 `params`（只有人数配置时，如 staffRatio 写「领队 3 人」）
   写清**有几位领队、什么资质、有没有随队保障**。只写怎么带队，不写花名堂，
   也不许把内部后勤人数当成专业卖点堆给客人。
5. **报名信息**（signup.form）→ 时间 / 地点 / 集合点 / 名额，全部取自上方数据。
这 5 项可以分布在各自的 section 里，**不要把五项挤进同一节**，也不要堆成一张密密麻麻的大表格。

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

## 报名必用数据（团期价格 / 领队 / 费用 / 装备，真实数据，必须写进成品）
__FACTS__

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
    # ★ 2026-10-08：加入 fee / kit / prices 三个「顾客必看」组件后，验收口径必须同步，
    #    否则模型照新契约写得再好也会被判 component blocks<4 → 退回旧的"模型直出 HTML"路径，
    #    新组件一个都上不了（实测踩到，成品里价格/领队全没了）。
    rich = sum(rep.get(k, 0) for k in ('stats', 'params', 'timeline', 'steps', 'team', 'quote', 'chips',
                                       'fee', 'kit', 'prices'))
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
                           digest: str = '', fact_pack: dict[str, Any] | None = None,
                           seed: int = 0) -> tuple[dict[str, Any], GatewayResponse]:
    """返回 ({"title","html","usedRefs","droppedRefs","route"}, gateway_response)。"""
    master = activity_master if isinstance(activity_master, dict) else {}
    pack = fact_pack if isinstance(fact_pack, dict) else {}
    media_lines = '\n'.join(caption_lines or []) or '（本次活动没有可用照片，不要使用任何图片）'
    fact_note = (f'## 方案事实要点表（碎片，不是成句；事实必须与它一致）\n{digest}\n'
                 if digest else
                 f'## 原始方案资料（第一手资料，细节最全）\n{str(source_text or "")[:12000]}\n')

    def _fill(tpl: str) -> str:
        return (tpl.replace('__MEDIA__', media_lines)
                   .replace('__MASTER__', json.dumps(master, ensure_ascii=False)[:9000])
                   .replace('__DIGEST__', fact_note)
                   .replace('__FACTS__', facts_prompt(fact_pack)))

    allowed = _allowed_refs(caption_lines)
    has_photos = bool(allowed)
    # 有序照片清单：给首屏兜底用（set 取不出"第一张"）
    photo_order = [str(l).split('｜', 1)[0].strip() for l in (caption_lines or []) if str(l).strip()]
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
        # ① 先用真实数据补齐「顾客必看」的内容，再做结构验收
        #    （顺序不能反：补齐本身会加 sections，先判断可能被误判成"太单薄"）
        doc, _added = ensure_required_sections(doc, pack, photo_order)
        rep = _doc_quality(doc, allowed)
        ok, why = _doc_ok(rep, has_photos)
        if ok:
            # ② 文案质检：挑出 AI 腔 / 照抄 / 占位符，带问题清单让模型改一次。
            #    只改一次（重试是有成本的，且改坏了还不如原稿 —— 见下面的采用条件）。
            doc_before_fix = doc
            issues = quality_report(doc, str(source_text or ''))
            if issues:
                gwf = await generate_json(
                    club_id=club_id, task_type='longpic', system_prompt=_SYSTEM,
                    user_prompt=(_REWRITE_PROMPT
                                 .replace('__ISSUES__', '\n'.join('- ' + i for i in issues))
                                 .replace('__DOC__', json.dumps(doc, ensure_ascii=False)[:16000])))
                fixed = _extract_doc(gwf.data) if gwf and isinstance(gwf.data, dict) else None
                if fixed:
                    fixed, _ = ensure_required_sections(fixed, pack, photo_order)
                    # 只有"确实改好了"才采用（以问题变少为准；否则保留原稿）
                    if len(quality_report(fixed, str(source_text or ''))) < len(issues):
                        doc = fixed
            # ★ 渲染失败**不能静默吞掉**（2026-10-08 教训：吞了之后只知道"成品是空的"，
            #   查了半天不知道是渲染炸了）。做法：打印堆栈给人看，并依次退回到
            #   ① 改写后的稿子（若失败）② 改写前的原稿 —— 原稿至少是验过能渲染的。
            for cand_doc in ((doc, doc_before_fix) if doc is not doc_before_fix else (doc,)):
                try:
                    cand_html = T.render(scrub_doc_text(cand_doc), allowed, cover_url, seed, photo_order)
                except Exception:
                    traceback.print_exc()
                    continue
                if cand_html:
                    html, doc = cand_html, cand_doc
                    break
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
    html = scrub_html_sentences(html) if route == 'template' else scrub_cost_text(html)

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





# ══════════════════════════════════════════════════════════════════════
# 成本清洗（★★ 2026-10-08 重大坑，改之前先读这段注释）
# ══════════════════════════════════════════════════════════════════════
# 成品整张变空，查到最后是这一行：`scrub_cost_text(整篇 HTML)`。
# 那个函数的第一步是 `is_cost_row(整段)` —— 只要整段里出现**任意**一个 COST_LABELS 词
# （「报价」「预算」「单价」「结算」「成本」… 全是宣传文案里可能顺手出现的词），
# 整段就被判成「一行成本明细」→ 直接返回 ''。
# 于是：加入价格 / 团期 / 人员配置之后的长图，第一次渲染成功 17638 字符，出口洗成 0。
#
# 正确做法分成两层，粒度都不可以再粗：
#   ① **内容层** —— 渲染前把 doc 里每条文案字符串单独过一把（一条就是一行，正是
#       scrub_cost_text 的设计粒度）；
#   ② **HTML 层** —— 只按句删，**永不整体判定**。
# 这样清洗能力一点没削弱（成本句照样被删），但不会再发生「整篇被误判」。
_SCRUB_HTML_SPLIT = re.compile(r'(?<=[。；;\n>])')
_SCRUB_SKIP_KEYS = frozenset({'type', 'media', 'ref', 'refs', 'date', 'no', 'unit', 'price',
                              'theme', 'qr', 'label', 'accent', 'size', 'eyebrow'})


def scrub_doc_text(obj: Any) -> Any:
    """内容层清洗：递归处理 doc 里的每条文案（跳过结构化键，别把 block type 洗没了）。"""
    if isinstance(obj, str):
        return scrub_cost_text(obj)
    if isinstance(obj, list):
        return [scrub_doc_text(i) for i in obj]
    if isinstance(obj, dict):
        return {k: (obj[k] if k in _SCRUB_SKIP_KEYS else scrub_doc_text(obj[k])) for k in obj}
    return obj


def scrub_html_sentences(html: str) -> str:
    """HTML 层清洗：按句删成本句，**不做整体判定**（见上面那段注释）。"""
    parts = _SCRUB_HTML_SPLIT.split(str(html or ''))
    kept = [p for p in parts if p.strip() and not is_cost_row(p)]
    return ''.join(kept)


# ══════════════════════════════════════════════════════════════════════
# 报名必用数据（FACT PACK）
# ══════════════════════════════════════════════════════════════════════
def build_fact_pack(club_id: int, activity_id: int, master: dict[str, Any] | None = None) -> dict[str, Any]:
    """把「顾客下单前要知道的东西」从库里捞出来，喂给模型 + 用于兜底补齐。

    ★ 2026-10-08 老板反馈「公众号长图里没有领队信息、没有活动价格」。
      根因不是模型不肯写，而是**这些字段从来没进过提示词**：
        · 团期价格    → activity_occurrences（price / start_at / label / 余位）
        · 领队        → occurrence_leaders join club_leaders
        · 人员配置    → master.publicFacts.staffRatio / master.services
        · 费用含不含  → master.fees
        · 自备装备    → master.checklist
        · 交通集合    → master.publicFacts.transport
      全部都在库里，只是从来没和长图管线连过线。这一次接通。
    """
    master = master if isinstance(master, dict) else {}
    pf = master.get('publicFacts') if isinstance(master.get('publicFacts'), dict) else {}
    pack: dict[str, Any] = {
        'occurrences': [], 'leaders': [], 'fees': {'inc': [], 'exc': []}, 'checklist': [],
        'club': '',
        'staffRatio': str(pf.get('staffRatio') or ''),
        'services': [str(x) for x in (master.get('services') or []) if str(x).strip()],
        'gather': str(pf.get('transport') or ''),
    }
    try:
        from db import conn as _dbconn, rows as _dbrows          # 延迟导入：避免模块环
        with _dbconn() as c:
            # 主办俱乐部名：master 里没有这一项，模型就无从知道"这是谁办的活动"，
            # 品牌行/chip 只能瞎写（2026-10-09 用户反馈「长图印了别的活动/社群的名字」）。
            _cl = _dbrows(c.execute('SELECT name FROM clubs WHERE id=?', (club_id,)))
            if _cl:
                pack['club'] = str(_cl[0].get('name') or '').strip()
            occ = _dbrows(c.execute(
                'SELECT id,start_at,end_at,price,capacity,sold,label,status '
                'FROM activity_occurrences WHERE activity_id=? AND club_id=? ORDER BY start_at',
                (activity_id, club_id)))
            for o in occ:
                if str(o.get('status') or 'open') != 'open':
                    continue
                pack['occurrences'].append({
                    'label': str(o.get('label') or '').strip(),
                    'date': str(o.get('start_at') or '')[:10],
                    'price': float(o.get('price') or 0),
                    'capacity': int(o.get('capacity') or 0),
                    'sold': int(o.get('sold') or 0)})
            lead = _dbrows(c.execute(
                'SELECT ol.name, ol.role FROM occurrence_leaders ol '
                'JOIN activity_occurrences ao ON ao.id = ol.occurrence_id '
                'WHERE ao.activity_id=? AND ol.club_id=?', (activity_id, club_id)))
            seen = set()
            for l in lead:
                nm = str(l.get('name') or '').strip()
                if not nm or nm in seen:
                    continue
                seen.add(nm)
                pack['leaders'].append({'name': nm, 'role': str(l.get('role') or '领队').strip()})
    except Exception:
        pass
    fees = master.get('fees') if isinstance(master.get('fees'), dict) else {}
    pack['fees'] = {
        'inc': [str(x).strip() for x in (fees.get('包含') or []) if str(x).strip()],
        'exc': [str(x).strip() for x in (fees.get('不含') or []) if str(x).strip()],
    }
    pack['checklist'] = [str(x).strip() for x in (master.get('checklist') or []) if str(x).strip()]
    return pack


def facts_prompt(pack: dict[str, Any] | None) -> str:
    """事实包 → 给模型看的一段紧凑文本（碎片，不是成句，避免它照抄）。"""
    pack = pack if isinstance(pack, dict) else {}
    out: list[str] = []
    if pack.get('club'):
        out.append('· 主办俱乐部（品牌行 / chip 的主办方就用它，除非资料里有联名品牌）：%s' % pack['club'])
    occ = [o for o in (pack.get('occurrences') or []) if isinstance(o, dict)]
    if occ:
        out.append('· 团期与价格（真值，照抄，不得估算）：')
        for o in occ:
            head = ' '.join(x for x in [str(o.get('label') or ''), str(o.get('date') or '')] if x) or '团期'
            money = ('¥%g / 人' % float(o['price'])) if float(o.get('price') or 0) > 0 else '价格未定'
            tail = ''
            cap = int(o.get('capacity') or 0)
            if cap > 0:
                tail = ' · 名额 %d 人，余 %d 位' % (cap, max(cap - int(o.get('sold') or 0), 0))
            out.append('  - %s｜%s%s' % (head, money, tail))
    ld = [l for l in (pack.get('leaders') or []) if isinstance(l, dict) and l.get('name')]
    if ld:
        out.append('· 带队阵容：' + '；'.join('%s（%s）' % (l['name'], l.get('role') or '领队') for l in ld[:8]))
    if pack.get('staffRatio'):
        out.append('· 人员配置（原文）：%s' % pack['staffRatio'])
    if pack.get('services'):
        out.append('· 服务内容（原文）：' + '；'.join(str(x) for x in pack['services'][:8]))
    fee = pack.get('fees') or {}
    if fee.get('inc'):
        out.append('· 费用包含：' + '；'.join(str(x) for x in fee['inc'][:9]))
    if fee.get('exc'):
        out.append('· 费用不含：' + '；'.join(str(x) for x in fee['exc'][:9]))
    if pack.get('checklist'):
        out.append('· 自备装备清单：' + '；'.join(str(x) for x in pack['checklist'][:14]))
    if pack.get('gather'):
        out.append('· 交通与集合（原文）：%s' % pack['gather'])
    return '\n'.join(out) or '（这场活动暂时没有额外的结构化数据，团期 / 领队 / 费用都不要编）'


# ★★ 代码兜底（与 longpic_template 里那三处兜底同源的手法）
_REQ_PROBES = {'team': 'team', 'prices': 'prices', 'price': 'prices', 'fee': 'fee', 'cost': 'fee',
               'kit': 'kit', 'checklist': 'kit', 'gear': 'kit'}


def ensure_required_sections(doc: dict[str, Any], pack: dict[str, Any] | None,
                            allowed_order: list[str] | None = None) -> tuple[dict[str, Any], list[str]]:
    """模型漏写「顾客必看」内容时，用**真实数据**补齐对应 section。

    ★ 为什么必须代码兜底：「提示词写了」≠「模型照做」。而这几项是**能不能发出去**的问题 ——
      一张没有价格、没有领队、没有费用边界的长图，客人看完还得回头问一句「多少钱」。
      宁可这一节是朴素的表格，也不能没有。
    """
    pack = pack if isinstance(pack, dict) else {}
    if not isinstance(doc, dict) or not pack:
        return doc, []
    secs = [s for s in (doc.get('sections') or []) if isinstance(s, dict)]
    have = {'team': False, 'prices': False, 'fee': False, 'kit': False}
    for s in secs:
        for b in (s.get('blocks') or []):
            if not isinstance(b, dict):
                continue
            kind = _REQ_PROBES.get(str(b.get('type') or '').strip().lower())
            if kind:
                have[kind] = True
    # 「已经写过了就别再补」★ judgement method：翻整篇（含导语正文），
    # 只要 staffRatio / 任一 service 的名字已经出现在标题或正文里，就算覆盖。
    # 2026-10-08 实测：原来只在 params 组件的 k/v 里找，结果模型把它写在第 4 节导语
    # 「配备户外领队、瑜伽老师、摄影师及后勤保障人员」，兜底没认出来，
    # 又补了两节一模一样的「带队与保障」—— 成品里出现三处重复。
    # 首屏必须有图：模型漏选时用清单第一张补上（封面由调用方另行兜底）
    order = [x for x in (allowed_order or []) if x]
    hero = doc.get('hero') if isinstance(doc.get('hero'), dict) else None
    if hero is not None and order and not str(hero.get('media') or hero.get('ref') or '').strip():
        doc = dict(doc)
        hero = dict(hero)
        hero['media'] = order[0]
        doc['hero'] = hero
        added.append('首屏配图')

    if not have['team']:
        blob = json.dumps(secs, ensure_ascii=False)
        for hint in [str(pack.get('staffRatio') or '')] + [str(x) for x in (pack.get('services') or [])]:
            if hint and hint.strip() and hint.strip() in blob:
                have['team'] = True
                break

    added: list[str] = []

    def add(no: str, title: str, lead: str, blocks: list) -> None:
        secs.append({'eyebrow': '%s' % no, 'title': title, 'lead': lead, 'blocks': blocks})
        added.append(title)

    idx = len(secs) + 1

    # 1) 带队阵容：有真名用 team，只有人数配置用 params
    if not have['team']:
        ld = [l for l in (pack.get('leaders') or []) if isinstance(l, dict) and l.get('name')][:8]
        if ld:
            add('%02d — TEAM' % idx, '谁带你进山',
                '这几位领队全程跟队，负责路线把控与安全保障。',
                [{'type': 'team', 'items': [{'name': l['name'], 'role': l.get('role') or '领队'} for l in ld]}])
            idx += 1
        elif pack.get('staffRatio') or pack.get('services'):
            items = []
            if pack.get('staffRatio'):
                items.append({'k': '人员配置', 'v': str(pack['staffRatio'])})
            if pack.get('services'):
                items.append({'k': '服务保障', 'v': '；'.join(str(x) for x in pack['services'][:6])})
            add('%02d — TEAM' % idx, '带队与保障', '这一趟的人员配置与服务内容如下。',
                [{'type': 'params', 'items': items}])
            idx += 1

    # 2) 团期与价格（只列真价；无真价就不补，避免出现「¥0 / 人」）
    if not have['prices']:
        real = [o for o in (pack.get('occurrences') or [])
                if isinstance(o, dict) and float(o.get('price') or 0) > 0][:6]
        if real:
            items = []
            for o in real:
                tail = ''
                cap = int(o.get('capacity') or 0)
                if cap > 0:
                    tail = '名额 %d 人 · 余 %d 位' % (cap, max(cap - int(o.get('sold') or 0), 0))
                items.append({'label': str(o.get('label') or '').strip() or '团期',
                              'date': str(o.get('date') or ''),
                              'price': '%g' % float(o['price']), 'unit': '/ 人', 'note': tail})
            add('%02d — ENROLL' % idx, '团期与费用', '可选团期与对应价格如下。',
                [{'type': 'prices', 'items': items}])
            idx += 1

    # 3) 费用包含 / 不含（内部成本条目由模板 _blk_fee 再过一道黑名闸）
    if not have['fee']:
        fee = pack.get('fees') or {}
        if fee.get('inc') or fee.get('exc'):
            blk = {'type': 'fee'}
            if fee.get('inc'):
                blk['inc'] = [str(x) for x in fee['inc'][:9]]
            if fee.get('exc'):
                blk['exc'] = [str(x) for x in fee['exc'][:9]]
            add('%02d — FEE' % idx, '费用包含什么', '报名前请把费用边界看清楚。', [blk])
            idx += 1

    # 4) 自备装备
    if not have['kit']:
        kit = [str(x) for x in (pack.get('checklist') or []) if str(x).strip()][:14]
        if kit:
            add('%02d — KIT' % idx, '自备装备', '这些装备请自行准备齐全。',
                [{'type': 'kit', 'title': '自备装备清单', 'items': kit}])
            idx += 1

    if added:
        doc = dict(doc)
        doc['sections'] = secs
    return doc, added


# ══════════════════════════════════════════════════════════════════════
# 文案质检（不合格就让模型改一次 —— "文案处理能力"的落地）
# ══════════════════════════════════════════════════════════════════════
# AI 腔套话黑名单：这些句子搬到任何一场活动上都能用，等于什么都没说。
_CLIQUE = re.compile(
    r'不仅能|还能让你|让我们一起|邂逅一场|仿佛一幅|是一场与|远离喧嚣|亲近自然|释放压力|'
    r'心灵之旅|治愈之旅|完美融合|不容错过|还在等什么|解锁|绝美秘境|必打卡|氛围感拉满|不负韶华')
_PH_IN_TEXT = re.compile(r'(?:[xX]{2,}|TBD|[?]{2}|__+)')




_REWRITE_PROMPT = """下面是你刚写完的一版长图内容 JSON，以及一份**问题清单**。
你要做的是**改稿**：只改清单里点出的问题，**其它内容一个字都不要动**。

## 硬性要求
1. 改完输出**完整的新 JSON**（结构与原来完全一致，字段一个都不能少）。
2. 不许因为改稿把团期价格、带队阵容、费用包含、装备清单这些内容改丢 ——
   它们是整篇里最有用的部分。
3. 替换掉的段落必须**换成本场活动独有的具体事实**（时刻 / 数字 / 专名 / 动作），
   不许用另一句漂亮话顶替那句套话 —— 那就是换个姿势的废话。
4. 只输出 JSON，不要 Markdown 代码围栏，不要解释。

## 问题清单
__ISSUES__

## 原 JSON
__DOC__"""

def quality_report(doc: dict[str, Any], source_text: str = '') -> list[str]:
    """给成品挑毛病，返回问题清单（空＝合格）。

    三件事：**说清楚、不像 AI、没照抄**。
    """
    issues: list[str] = []
    if not isinstance(doc, dict):
        return ['doc 不是对象']
    secs = [s for s in (doc.get('sections') or []) if isinstance(s, dict)]
    texts: list[str] = []
    for i, s in enumerate(secs, 1):
        lead = str(s.get('lead') or s.get('text') or '').strip()
        title = str(s.get('title') or '').strip()
        if title and len(title) > 20:
            issues.append('第%d节标题 %d 字 > 20，会折行难看，请压到 14 字内' % (i, len(title)))
        if lead:
            texts.append(lead)
            if len(lead) < 40 and i <= 3:
                issues.append('第%d节导语只有 %d 字，太薄；补这场活动独有的具体安排' % (i, len(lead)))
            if _CLIQUE.search(lead):
                issues.append('第%d节导语有 AI 腔套话（搬到别场活动也通用），换成具体事实' % i)
            if _PH_IN_TEXT.search(lead):
                issues.append('第%d节导语还留着占位符，删掉或改成诚实表述' % i)
        for b in (s.get('blocks') or []):
            if not isinstance(b, dict):
                continue
            for k in ('text', 'desc', 'what'):
                v = str(b.get(k) or '')
                if v:
                    texts.append(v)
                    if _CLIQUE.search(v):
                        issues.append('第%d节里有 AI 腔套话：“%s”换成具体动作或数字' % (i, v[:18]))
                    if _PH_IN_TEXT.search(v):
                        issues.append('第%d节里有占位符：“%s”删掉' % (i, v[:18]))
    # 照抄原文：连续 12 字以上是红线
    src = str(source_text or '')
    if src:
        for t in texts:
            t = re.sub(r'\s+', '', t)
            for n in range(12, min(len(t), 40) + 1, 2):
                frag = t[:n]
                if len(frag) >= 12 and frag in re.sub(r'\s+', '', src):
                    issues.append('有一段与方案原文连续重合超 12 字：“%s”，必须改写' % frag[:16])
                    break
    hero = doc.get('hero') if isinstance(doc.get('hero'), dict) else {}
    for k in ('sub', 'meta'):
        v = str(hero.get(k) or '')
        if v and _PH_IN_TEXT.search(v):
            issues.append('首屏 %s 里有占位符，删掉' % k)
    # 去重：同一条问题可能出现多次
    seen, uniq = set(), []
    for it in issues:
        key = re.sub(r'\d+', '#', it)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq[:8]

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
