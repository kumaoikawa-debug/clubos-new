from __future__ import annotations
import json,re
from pathlib import Path
from typing import Any
from ai_gateway import generate_json, record_mock_usage, GatewayResponse
from image_caption import caption_media, caption_lines
from cost_guard import (scrub_cost_text, scrub_cost_data, sanitize_for_frontend, is_cost_row,
                        is_cost_key, has_cost_context, master_has_cost_evidence)

# 图片落盘根目录（与 app.py 的 STATIC 一致）。视觉打标要读真实文件，不能用 cwd 拼路径。
STATIC_DIR = Path(__file__).resolve().parent / 'static'

SYSTEM = """你是 ClubOS 的 AI 活动内容主编（Editorial Director），不是模板填充器。
老板/领队只负责提供真实资料和照片，你负责像资深户外活动策划、编辑、文案与视觉主编一起工作一样，直接产出可发布成品。

最高规则：
1. 默认先做，不确认。能从资料判断的直接判断；只有来源内部出现会导致事实错误且无法消解的冲突，才写入 blocking_conflicts。
2. 允许创造表达，不允许创造事实。不能杜撰日期、地点、价格、领队、住宿、天气、景观必然出现、用户反馈、名额紧迫、资质等。
3. 区分 C 端公开事实、内部经营资料、宣传素材。成本单价、供应商报价、员工 SOP、内部退款谈判、毛利等不得进入 C 端。
4. 不选择固定模板，不返回 Family / Variant / Magazine / Diary 等模板名。你要针对当前活动自己决定：从哪里开场、什么最值得卖、图片与文字各占多少、信息何时出现、页面如何收束。
5. block 是视觉原子，不是固定章节。任何 block 都可省略、重复、自由排序；数量由内容决定。
6. 图片必须引用提供的 media ref，不得编造不存在的图片。根据横竖比、内容语义和叙事作用决定大图、双图、拼图或连续铺陈。
7. AI Credits 是计费层，不得因此削弱输入、减少图片、缩短上下文、换弱模型或减少创作深度。
8. 输出严格 JSON，不要 Markdown，不要解释。
"""

BLOCK_TYPES = "hero|lead|narrative|statement|media|gallery|facts|timeline|info|quote|divider|cta|bigimage|imagetext|cards|numbercards|highlight|columns"

# 文案契约（2026-10-07 重写）。
# 旧契约要求「eyebrow 用 2~6 字场景词 / body 拆成一行一个 ≤30 字短句 / 每段都配一句对仗 pull」，
# 结果是每一节长得一模一样：场景小标题 + 四行诗 + 「A，B」式金句，一眼就是 AI 写的。
# 新契约反过来把这三条列为禁令，改为要求连贯段落、信息型小标题、以及「只有这场活动才有」的具体细节。
_WRITING_CONTRACT = """文案与排版契约（2026-10-07 第二版，用户反馈「接同样的大模型，写出来的还不如豆包/千问/ChatGPT/Gemini」后重写）：

★★★ 先说一条元规则，它排在所有规则前面：**本契约不给你任何例句。**
  上一版契约里写了「正例」和「反例」，本意是划范围，实际结果是它们被**逐字搬进了成品**：
  四个小节标题、金句、甚至被列为「反例」的句子，都原样出现在发布出去的内容里。
  同理，把「不要写某某词」这种话写进提示词，只会让那个词更容易出现。
  所以：**下面只给要求，不给范文。所有句子、标题、金句都必须由你自己从本次活动的
  真实细节里长出来，一个现成的短语都不要借。**

★★ 总纲：你不是在填模板，你是在写一篇让人读完就想报名的长文。读起来必须像一位熟悉这条线的
户外编辑亲手写的——有具体细节、有判断、有节奏变化。

- 禁令 A｜小节标题：不要用 2~6 字的泛化场景词、时间词或情绪词当标题（那种标题换到任何一场
  活动上都成立，等于没说）。headline 必须给出**信息或判断**：带上本次活动的数字、专名，
  或者一句明确的取舍与建议。

- 禁令 B｜一行一句的排版诗：body 是**连贯段落**，每段 2~4 句、60~180 字，句子里正常使用
  逗号、顿号、破折号与分号。段与段之间才换行，一节最多 2 段。
  绝对不要写成一行一个短句、靠换行制造「诗意」的样子。

- 禁令 C｜对仗金句：pull 金句完全可选；若写，全篇最多 2 处，且不许都用「A，B」对仗格式。
  宁可一句金句都不写，也不要凑。

- 禁令 D｜空话与可搬运的感慨：凡是**能够原样搬到别的活动上**的形容词、感慨与四字套话，
  一律不许出现。写风景就写它的高度、时刻、光线与你在那里做什么；写住宿就写它替你省掉了什么。
  自查办法：把你写的形容词拿出来，问「这句话换个活动还能不能用」——能用就删掉重写。

- 禁令 E｜每节同构：不要让每一节都是「小标题 + 一段描述 + 一张图」。段落长短、有没有配图、
  配几张图，都应该跟着内容走：有的章节两句话就够，有的可以写满一段；有的配图，有的不配。

- 禁令 F｜句式雷同（最容易露馅的一条）：不要反复使用固定的对仗句式与固定的开场方式
  （尤其是「否定 + 转折」型的强调句，以及每节都以同一类状语起头）。
  相邻小节的起句方式必须不同；发现自己连着两节在用同一个句模，就把其中一节改写成陈述句。

- 禁令 G｜不要给资料里没有的东西添细节。资料只写了品类，你就只能写到品类；资料没写的配饰、
  菜品、材料、器材、品牌、产地、参数、人名，一律不许出现。写不出出处的具体物件，就不写。

- 禁令 H｜不以时间叙事（2026-10-08 用户反馈）：宣传正文（lead / narrative / statement / facts
  的句子）**不许按钟点推进**。正文里不出现「HH:MM至HH:MM」这类钟点区间，也不用时刻当段落
  开头或行文线索——分钟级安排属于 itinerary / timeline（结构化时间线已经完整呈现，正文再复述
  一遍既重复又像排班表）。正文只写这段体验**是什么、好在哪**，几点几分读者自己去时间线里看。

- 禁令 I｜顾客视角（内部执行信息零出现，2026-10-08 用户反馈）：文案是写给**报名的人**看的，
  不是写给带队的人看的。资料里的运营/执行层信息一律不进正文：后勤如何携带与回收物资、
  工作人员怎么分工、车辆与供应商如何调度、内部集合签到流程、应急预案这类内容。资料本身是
  执行方案，你的任务是把执行方案**翻译成顾客的体验与获得**——让读者知道自己会经历什么、
  得到什么、需要自己准备什么；转不成顾客价值的内部细节，直接不写。

- 禁令 J｜信息类字段也是文案（2026-10-09 用户反馈，同样适用于**每一场活动**的介绍）：
  facts / info / cards / numbercards 里的文字，以及餐饮、装备、天气、路线、住宿、集合这类
  描述性内容，**同样是你要写的文案，同样不许照搬方案**。方案里这类内容往往写成
  「餐饮：户外牛肉汤锅（含精选黄牛腱肉、虾滑、肥牛等）」这样的参数串；品类名与数字保留，
  但那句话必须由你重新组织成顾客读得进去的表达：把括号里的堆料写成具体说法、把「含…等」
  这种清单腔改掉、把方案里的营销词换成人话。
  自查办法：把你写的这段和方案原文并排看，如果只是把冒号换成了逗号，那就是照搬，必须重写。

★ **不许照抄。** 这是本文档最重要的一条：
  实测「把方案原文整段喂给模型」时，成品里 14.5% 的 8 字片段能在原文里逐字找到，
  最长有一段连续 31 个字与原文一字不差 —— 读起来就是把 PPT 的景点说明翻译了一遍。
  硬指标：**成品里任何一句话，与原始资料原文的连续重合不得超过 12 个字。**
  若你发现自己在写「XX位于XX，海拔XX米，是XX」这种句子，说明你在搬运，不是在写。
  素材表里给的是**短语碎片**，把它们连成句子、补上视角与判断，才是你的工作。

★★ 细节的边界（与上一条同等重要，「挖细节」绝不等于「编细节」）：
  你写的具体内容，**只能来自资料里已经写过的东西**。以下全部属于编造，出现即不合格：
  资料里没有的机构名 / 资质名 / 证书名（哪怕它听起来非常合理）；资料里没有的材料、品类与
  工艺参数；资料里没有的经验年限、荣誉、设备数值；资料里没有的药品与装备配给；
  资料里没有的人数、名额与剩余席位。
  你可以做的是**把资料里已经写了的细节写得更好**：换更准确的动词、调整句子节奏、点明它为什么
  重要。表达是你的空间，事实不是。**任何你在资料里找不到出处的事实，都只写到资料里能看到的那一层。**

★ 资料是原料，不是成品：事实（数字 / 专名 / 工艺 / 时间）必须与资料完全一致，
  但**表达必须是你自己的**：重组语序、换动词、把碎片连成有呼吸的长句、点明它为什么值得专程去一趟。
  同样，也不要重复你自己上一段的话：相邻小节不要用相同的开场方式，也不要复用同一个形容词。

★ 每一节至少要含 1 个「只有这场活动才有」的具体事实：数字（海拔 / 时长 / 公里数 / 人数 / 年份）、
  专有名词（地名 / 酒店名 / 机构名 / 经文名 / 菜品名）、具体动作或机制（几点抵达、怎么上色）。
  一个章节里挖不到具体事实，就说明这节不该写。

★ 小节标题（headline）：8~22 字，写**判断句或信息句**，不是词组堆叠。数字和专名是好朋友。
★ eyebrow 字段可选，只在确实需要场景锚点时才用，且不要连续两节都用。
★ 全页穿插 1~2 个 statement（整页大字观点句，≤18 字）与至多 1 个 quote；金句必须从本次活动的
  真实体验里长出来（地名 / 海拔 / 动作 / 时刻），不许放之四海皆准。
★ lead 是开场引言：2~3 句，第一句给画面、最后一句给出发的理由。不要写成「本次活动旨在…」。"""


# ---------------------------------------------------------------------------
# 事实要点表（fact digest）
# ---------------------------------------------------------------------------
# 为什么需要它（2026-10-07）：
#   上一版为了「让推文拿到细节」，把方案原文**整段**塞进了写作 prompt。结果事与愿违 ——
#   原文的句子成了现成的抄袭原料，成品 14.5% 的 8 字片段能在原文里逐字命中，
#   最长一段连续 31 字与原文一字不差，读起来就是「PPT 景点说明的散文翻译」。
#   三轮对照实测（同一模型 qwen-max、同一份 PPT）：
#     · 把原文整段给模型                  → 8 字命中率 14.5%，最长连续 31 字
#     · 加「强制重写」要求 + 改写示范      → 8 字命中率 8.8%，但示范句被逐字搬走（59 字）
#     · 只给要点表（不给任何整句）         → 8 字命中率 3.3%，最长连续 17 字  ★
#   结论：**别把原句递给写作模型**。事实靠要点表传递，句子必须由模型自己组。
_DIGEST_SYSTEM = '你是活动资料整理助理，只做提取与压缩，不写文案、不做润色。'

_DIGEST_PROMPT = """把下面这份活动方案，压成一张「事实要点表」，交给文案同事使用。

- 只保留事实：数字（海拔/时长/距离/人数/年份/价格）、专有名词（地名/酒店名/机构名/体验名/
  菜名/经文名/装备名）、具体动作与体验机制（怎么抽打、几公里、多少度）。
- 钟点时刻（HH:MM）不收：分钟级安排由行程时间线承载，正文不以时间叙事（2026-10-08 用户反馈）。
- 内部执行信息不收：后勤携带与回收物资、工作人员分工、车辆与供应商调度、集合签到流程、
  应急预案——这些是给运营看的，顾客只关心自己会体验到什么、得到什么。
- 每条 ≤18 字；同组要点用「·」分隔成一行。
- **不许保留原句**：不得出现完整句子（不要主谓宾齐全的成句表达），不许出现「坐落于」「位于」
  「被誉为」「是……的」「集……于一体」这类结构；原文的形容词、排比与营销话术一律丢弃。
- 按【景观与体验】【住宿】【交通与服务】【时间与费用】分组；某组没内容就不写这一组。
- 资料里没写的，一个字都不许补。

只输出这张表，不要任何前言后语。

=== 方案资料 ==="""


async def _fact_digest(club_id: int, source_text: str) -> str:
    """把原始资料压成要点表（失败返回空串，由调用方回退到旧路径）。"""
    if not (source_text or '').strip():
        return ''
    try:
        gw = await generate_json(club_id=club_id, task_type='digest', system_prompt=_DIGEST_SYSTEM,
                                 user_prompt=_DIGEST_PROMPT + '\n' + source_text[:20000])
    except Exception:
        return ''
    if not gw or not gw.data:
        return ''
    data = gw.data
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, dict):
        # 模型可能返回 {"表": "..."} / {"points": [...]} 等形状，全部拍平成纯文本
        parts = []
        for v in data.values():
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                for it in v:
                    if isinstance(it, str):
                        parts.append(it)
                    elif isinstance(it, dict):
                        parts.append('·'.join(str(x) for x in it.values() if isinstance(x, (str, int, float))))
        return '\n'.join(p for p in parts if p).strip()
    return ''


# ---------------------------------------------------------------------------
# 「照抄原文」的机械闸门
# ---------------------------------------------------------------------------
# 提示词靠不住（契约里写「不许照抄」，成品里最长连续重合仍有 31 字），所以这里做**可测的**硬闸门：
# 逐块算它与原文的最长公共子串，超阈值的段落交回模型定向重写。指标可断言，不依赖模型自觉。
def _norm_cjk(s: str) -> str:
    return re.sub(r'[^\u4e00-\u9fa5A-Za-z0-9]', '', s or '')


def longest_common_span(a: str, b: str) -> int:
    """a 与 b 的最长公共子串长度（忽略标点与空白）。"""
    a, b = _norm_cjk(a), _norm_cjk(b)
    if not a or not b:
        return 0
    if len(a) > 4000 or len(b) > 40000:      # 防御：超长文本不做 O(n*m) 全量比对
        a, b = a[:4000], b[:40000]
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


# 要点表里属于「事实枚举」而不是「文案」的内容：价格、费用清单、日期、名额。
# 它们本来就该与方案一字不差（费用包含什么不能编、价格不能改），拿它们去判「照抄」
# 只会让模型白改写一遍，甚至把事实改歪。用户抱怨的是**描述性文字**被整句搬走。
_FACT_LIKE = re.compile(r'(费用包?含|费用不含|包含[:：]|不含[:：]|[0-9]+\s*元|积分|¥|/人|^\d{4}[-./年])')


def _echo_texts(b: dict[str, Any], deep: bool = False, limit: int = 12) -> list[tuple[str, int]]:
    """一个 block 里「本应由模型重写」的文本片段，以及各自的判定门槛。

    deep=True 时把要点表 / 卡片里的 value 也算进来（2026-10-09 用户反馈：详情里
    「餐饮：户外牛肉汤锅（含精选黄牛腱肉、虾滑、肥牛等）」这种参数串就是照搬方案原文，
    只查 headline/body 根本查不到它）。
    ★ 要点表的门槛要比正文**低**：正文是长段落，12 字连续重合才叫抄；而要点表本来就是短语，
    「徒步 + 颂钵冥想 + 自然拓染」这种 10 字串已经是明晃晃的照搬，用 12 字门槛会漏掉它。
    日期、里程这类更短的 value（< short）不参与判定——它们本来就该与资料一字不差。
    """
    keys = ('headline', 'title', 'body', 'text', 'subtitle', 'caption', 'pull')
    out = [(str(b.get(k) or ''), limit) for k in keys]
    if deep:
        short = max(6, limit - 4)
        for x in (b.get('items') if isinstance(b.get('items'), list) else []):
            v = x.get('value') if isinstance(x, dict) else (x if isinstance(x, str) else None)
            if not isinstance(v, str) or len(v.strip()) < short:
                continue
            if _FACT_LIKE.search(v):      # 价格 / 费用清单 / 日期：属于事实，不判照抄
                continue
            out.append((v, short))
        for x in (b.get('cards') if isinstance(b.get('cards'), list) else []):
            if isinstance(x, dict):
                out += [(str(x.get(k) or ''), limit) for k in ('title', 'text', 'body', 'content')]
    return [(t, lim) for t, lim in out if t.strip()]


def source_echo_blocks(blocks: list[dict[str, Any]], source_text: str, limit: int = 12,
                       deep: bool = False) -> list[int]:
    """返回「与原文连续重合 > limit 字」的 block 下标。deep=True 时连要点表一起查。"""
    if not source_text:
        return []
    bad = []
    for i, b in enumerate(blocks or []):
        if not isinstance(b, dict):
            continue
        for txt, lim in _echo_texts(b, deep, limit):
            if longest_common_span(txt, source_text) > lim:
                bad.append(i)
                break
    return bad


ECHO_REWRITE_SYSTEM = '你是中文户外旅行公众号的主编，擅长把「资料语言」改写成有人味的成稿句子。'


def _echo_rewrite_prompt(blocks: list[dict[str, Any]], idxs: list[int], source_text: str) -> str:
    picked = [{'i': i, 'headline': blocks[i].get('headline') or blocks[i].get('title') or '',
               'body': blocks[i].get('body') or blocks[i].get('text') or ''} for i in idxs]
    return f"""下面这些段落，是刚才从资料里改写出来的推文段落。问题：它们**几乎照抄了资料原句**。

请只改写被指出的这几段，输出结构与输入完全一致的 JSON 数组：
[{{"i":<原下标>,"headline":"","body":""}}]

改写要求：
- 事实（数字/专名/时间/海拔）一个都不许动，必须与资料一致。
- 句子必须重写：换视角、换语序、补上「所以呢」的判断或建议。
- 任一成品句与资料原文的**连续重合不得超过 12 个字**。
- 不许新增资料里没有的事实。
- 不要输出任何解释。

待改写段落：{json.dumps(picked, ensure_ascii=False)}

资料原文（只用于核对事实，**它的句子不许再出现在成品里**）：
{source_text[:8000]}"""


DETAIL_ECHO_SYSTEM = ('你是中文户外旅行公众号的主编，擅长把「方案语言」改写成有人味的成稿句子，'
                      '同时一个事实都不改。')

_DETAIL_ECHO_PROMPT = """下面这些段落，是刚从活动方案改写出来的 C 端详情内容。问题：它们**几乎照抄了方案原句**
（要点表里那种「餐饮：户外牛肉汤锅（含精选黄牛腱肉、虾滑、肥牛等）」的参数串也算照抄）。

请只改写被指出的这几块，输出结构与输入完全一致的 JSON 数组：
[{{"i":<原下标>,"headline":"","body":"","text":"","items":[{{"label":"","value":""}}]}}]

改写要求：
- 事实（数字 / 专名 / 时间 / 海拔 / 品类）一个都不许动，必须与方案完全一致；要点表的 label 原样保留。
- 文字必须重写：把参数串写成顾客读得进去的句子——口语、有画面、有判断；括号里的堆料改成具体说法，
  杜绝「含…等」这种清单腔，也不用方案里的营销词。
- 任一成品句与方案原文的**连续重合不得超过 12 个字**（只把冒号换成逗号不算重写）。
- 不许新增方案里没有的事实，也不许删掉方案里已有的事实。
- 不要输出任何解释。

待改写内容：{picked}

方案原文（只用于核对事实，**它的句子不许再出现在成品里**）：
{source}"""


async def _rewrite_echo_detail(club_id: int, detail: dict[str, Any], source_text: str) -> bool:
    """详情照抄闸门：命中「与原文连续重合 > 12 字」的 block，交回模型定向重写一次。

    为什么详情也要这一道（2026-10-09 用户反馈「这些文案不要照搬方案」）：
    渠道文案（公众号 / 小红书）早就有 source_echo_blocks + 定向重写，但**详情从来没有**，
    于是详情页里那些「活动形式 / 路线 / 天气 / 餐饮 / 装备」行成了照搬方案原文的漏网区。
    返回是否真的改到了内容；任何异常都吞掉——回炉失败就接受原稿，不能让整条生成链路挂掉。
    """
    if not isinstance(detail, dict) or not source_text:
        return False
    blocks = detail.get('blocks')
    if not isinstance(blocks, list) or not blocks:
        return False
    bad = source_echo_blocks(blocks, source_text, deep=True)
    if not bad:
        return False
    try:
        picked = []
        for i in bad:
            b = blocks[i] if isinstance(blocks[i], dict) else {}
            row = {'i': i, 'type': b.get('type') or 'narrative',
                   'headline': b.get('headline') or b.get('title') or '',
                   'body': b.get('body') or b.get('text') or ''}
            if isinstance(b.get('items'), list):
                row['items'] = [{'label': str((x.get('label') if isinstance(x, dict) else x) or ''),
                                 'value': str((x.get('value') if isinstance(x, dict) else '') or '')}
                                for x in b['items'] if isinstance(x, (dict, str))][:12]
            picked.append(row)
        fix = await generate_json(club_id=club_id, task_type='detail',
                                  system_prompt=DETAIL_ECHO_SYSTEM,
                                  user_prompt=_DETAIL_ECHO_PROMPT.format(
                                      picked=json.dumps(picked, ensure_ascii=False),
                                      source=source_text[:8000]))
        rows = fix.data if fix else None
        if isinstance(rows, dict):
            rows = rows.get('blocks') or rows.get('items')
        changed = False
        for row in (rows or []):
            if not isinstance(row, dict):
                continue
            i = row.get('i')
            if not isinstance(i, int) or not (0 <= i < len(blocks)) or not isinstance(blocks[i], dict):
                continue
            tgt = blocks[i]
            for k in ('headline', 'body', 'text'):
                v = row.get(k)
                if isinstance(v, str) and v.strip() and v.strip() != str(tgt.get(k) or '').strip():
                    tgt[k] = v.strip()
                    changed = True
            new_items = row.get('items')
            if isinstance(new_items, list) and new_items and isinstance(tgt.get('items'), list):
                # 只回填模型真改过的 value：label 与条目数量以原结构为准，
                # 免得模型在改写时顺手删掉一条（要点表少一行，顾客就少知道一件事）。
                for j, orig in enumerate(tgt['items']):
                    if j >= len(new_items) or not isinstance(orig, dict) or not isinstance(new_items[j], dict):
                        continue
                    nv = new_items[j].get('value')
                    if isinstance(nv, str) and nv.strip() and nv.strip() != str(orig.get('value') or '').strip():
                        orig['value'] = nv.strip()
                        changed = True
        return changed
    except Exception:
        return False


def _ensure_media_refs(blocks: list[dict[str, Any]], photo_refs: list[str]) -> list[dict[str, Any]]:
    """推文一张图都没引用时，按顺序把真实照片铺回叙述节。

    实测真实模型会整篇不给 mediaRefs（一篇纯文字的公众号推文既难看也违背常识），
    这属于模型偶发行为，不该让老板看到。图片只能来自 kind='photo' 的真实照片。
    """
    if not photo_refs or not isinstance(blocks, list):
        return blocks
    if any(isinstance(b, dict) and b.get('mediaRefs') for b in blocks):
        return blocks
    slots = [b for b in blocks if isinstance(b, dict)
             and b.get('type') in ('narrative', 'media', 'gallery', 'lead')]
    if not slots:
        return blocks
    k = 0
    for b in slots:
        if k >= len(photo_refs):
            break
        b['mediaRefs'] = [photo_refs[k]]
        k += 1
    return blocks



def _guess(text:str,patterns:list[str],default=''):
    for pat in patterns:
        m=re.search(pat,text,re.I)
        if m:return m.group(1).strip()
    return default


def _image_refs(source, n=None):
    refs=[x.get('ref') for x in source.get('images',[]) if x.get('ref')]
    return refs if n is None else refs[:n]


# 只有真实照片能进 C 端。品牌 logo / 字标 / 空白幻灯片底图 / 地图天气截图 / 扁平赞助商海报
# 会跟着 PPT 一起被抽出来，解析期已由 document_parser._image_kind 标成 kind='logo'。
# 这里按 ref 过滤。kind 缺失（老资料/PDF 渲染页）按照片处理，保持向后兼容。
def _photo_refs(source:dict[str,Any])->list[str]:
    imgs=[x for x in (source.get('images') or []) if isinstance(x,dict) and x.get('ref')]
    return [x['ref'] for x in imgs if (x.get('kind') or 'photo')=='photo']


# 「出行清单」由平台侧 renderPacking(master) 按 master.checklist 渲染（static/shared.js），
# 那一块才带商城匹配、平替与会员价。AI 若再出一块「装备建议」，同一份清单就会在页面上
# 出现两遍（2026-10-03 用户反馈「两个地方都有装备推荐」）。所以生成侧一律不出这类块，
# 标题命中下列词的 info 块由 _sanitize_blocks 在出口剔除——mock 与 live 两条路都收口。
_GEAR_BLOCK_TITLES=('装备建议','装备清单','出行清单','携带清单','携带物品','着装建议','行李清单',
                    '物品清单','建议装备','推荐装备','装备推荐','必备清单','自备物品','建议携带')


# ---------------------------------------------------------------------------
# 事实回检（2026-10-04 用户反馈「把方案包装成有吸引力的活动详情」后加的出口闸门）：
# 模型为了把文案写得好看，会顺手改写专名 —— 实测资料写「icebreaker」，成品音译成「冰破」。
# 顾客会认错品牌、会照着错地名导航，属事故级。提示词里已下保真铁律，这里再加一道出口回检：
#   ① 品牌名音译 → 直接还原成资料里的原始写法（映射明确，可自动修，已单测）；
#   ② 成品里出现、但资料里**查无此名**的地名 → 不猜、不替换，写进 uncertainties 让运营核对。
# ⚠ 事实澄清（别再被之前的错误结论带偏）：兴福寺方案详情里出现的「草寺庙」**不是模型编的**，
#   是 Slide 9 行程表格原文就写着「到达草寺庙后森林区域午餐」；模型忠实照抄了。
#   那属于「原文自己前后不一致」，正则判定不可靠（详见 _GEO_HEAD_STOP 下方说明），
#   已改为要求模型写进 uncertainties，不在这里判。
# ---------------------------------------------------------------------------
# 模型实测过的音译/意译别名 → 资料里的原始写法。命中就整体替换，不做模糊匹配。
_BRAND_ALIASES={'冰破':'icebreaker','破冰':'icebreaker','破冰者':'icebreaker',
                '冰破者':'icebreaker','始祖鸟':"ARC'TERYX",'乐斯菲斯':'TheNorthFace'}
# 地名后缀：用于从文本里抓「疑似地名」。只用来**发现**，不用来判定。
_GEO_TAIL=('寺','庙','山','峰','沟','湖','镇','村','坪','谷','坡','坝','溪','桥','关','寨')
# 只取「1 个汉字 + 后缀」的两字词做子串比对。不要贪心取 5 字前缀——中文没有词边界，
# 实测 `[\u4e00-\u9fa5]{1,5}(寺|庙)` 会把动词吞进去，把「徒步至草寺庙」整段当成地名，
# 于是真的「草寺」反而没被单独认出来。子串比对更稳：只要原文里出现过就不算编造。
_GEO_RE=re.compile(r'[\u4e00-\u9fa5](?:'+'|'.join(_GEO_TAIL)+')')
# 通用词：这些「汉字+后缀」在任何活动里都会出现，不是专名，不能报。
_GENERIC_GEO={'寺庙','山林','山河','山水','山村','山谷','山坡','溪流','桥梁','城镇','山峰',
              '湖畔','海边','江边','河岸','森林','公园','景区','营地','山野','山间','田间',
              '寺院','山地','山道','河道','沟谷','坡地','村镇','寨子','关隘','坪地'}
# 这些字几乎不会是专名首字，出现在「汉字+后缀」前面基本都是量词/修饰语/动词。
# 实测假阳性①：「有人夹起一片山药」里的「片山」被当成编造的地名报了出来。
# 实测假阳性②：「穿过寺庙与山林，沿着山道走到村镇」里的「与山/着山/到村/过寺」。
# 汉语没有词边界，动词 + 后缀就会拼出伪专名 —— 只能靠首字黑名单挡。
_GEO_HEAD_STOP=set('片座条道个处块段些这那整满半每各前后上下里外中间边旁对同全名位次'
                   '种群排列张幅点层步米公小大老新高深远近多少一二三四五六七八九十'
                   '与到着过走穿从向往在由经达去来回归出入沿顺登爬翻越看望有无是的了和'
                   '跟及或并而把被让使给自至又再还就才只都也很太更最行进抵绕靠邻近距奔'
                   '赴览游攀hofer是能会将可使须应被所对该其们时分秒年天周月'
                   '散漫环绕驱弥消吹笼锁压')
# 这些字是「散/漫/环/绕…」这类动词首字，几乎不会是专名首字；
# 它们 + 地理后缀只会拼出动词短语（驱散山间、漫山遍野、环山公路、绕山而行），不是地名。
# 实测假阳性③：「一锅暖意，驱散山间微寒」里的「散山」被当成编造地名报了出来。
_GEO_RIGHT_RELATIONAL=set('间中上下前后里畔边脚腰顶口头尾侧心面底内外东西南北端梢陲沿际')
# 地理后缀后紧跟这些「关系后缀」→ 整个「汉字+后缀」是方位短语而非专名。
# 例：「驱散山间微寒」→ 山后是「间」→ 散山是动词短语；「寺中/湖畔/村口/谷底/坡顶/桥下/关外/寨内」同理。
# 这是比首字黑名单更通用的护栏：只要后缀处于方位短语里，就绝不可能是独立地名。


# ---------------------------------------------------------------------------
# 「原文里的专名前后不一致」为什么**不做**正则自动检测（试过、已回退，别再走一遍）：
# 兴福寺方案实测：Slide 8「石笋寺-兴福寺」、Slide 9 表格「到达草寺庙后森林区域午餐」、
# Slide 2 又写兴福寺曾名「石庙子」。一台徒步活动同时有起点寺、终点寺、古称是**正常**的。
#   ① 第一版按「同一后缀下出现多个专名 = 笔误」判定 → 把正常的「石笋寺→兴福寺」判成笔误；
#   ② 第二版改成「列出原文所有专名让运营过目」→ 汉语没有词边界，正则把「午间在寺」
#      「起一片山」「协会高山」「客户沟」（来自"客户沟通"）都当专名列了出来，噪声比信号多。
# 根因：**专名识别需要分词/语义，正则做不到**。这种「是不是同一个地方的两种写法」是
# 判断题，交给模型（提示词里已要求它把原文可疑专名写进 uncertainties），不要用阈值硬猜。
# 保留确定性闸门只做两件有把握的事：品牌名还原（映射明确）、成品里查无出处的专名告警。
# ---------------------------------------------------------------------------


def restore_brand_names(text:str,source_text:str)->str:
    """把模型音译/意译的品牌名还原成资料里的原始写法。"""
    if not text or not source_text: return text
    out=str(text)
    # ★ 路径 / URL 不是文案，必须原样返回。上传的图片文件名里就带品牌中文名
    #   （…/extracted/2026始祖鸟高客_四川新都桥鱼子西方案_最新__image2.jpg），
    #   一旦被下面的映射改成「2026ARC'TERYX高客_…」，磁盘上根本没有这个文件，
    #   整个活动详情页的图片会全部 404（2026-10-07 实测事故，且每次重新生成都会复发）。
    if out.startswith(('/','http://','https://','data:','blob:')): return out
    for alias,canonical in _BRAND_ALIASES.items():
        if alias in out and canonical.lower() in source_text.lower():
            out=out.replace(alias,canonical)
    return out


def hallucinated_places(text:str,source_text:str)->list[str]:
    """成品里出现、但原始资料里查无此名的地名/寺庙名 —— 模型编造或改写过的。

    判据：成品里的「汉字+地理后缀」两字词，只要**不是原始资料的连续子串**，且不是
    「寺庙/山林」这类通用词，就认为是模型编出来的。子串比对而不是分词——中文没有
    词边界，靠正则切词会把「徒步至草寺庙」整段当成一个地名，反而漏掉真凶「草寺」。
    """
    src=str(source_text or '')
    if not src: return []
    t=str(text or '')
    out=[]
    for term in sorted(set(_GEO_RE.findall(t))):
        if term in _GENERIC_GEO: continue
        if term[0] in _GEO_HEAD_STOP: continue   # 「一片山药」里的「片山」不是地名
        if term in src: continue          # 原文出现过（子串即可）就不算编造
        # 地理后缀后紧跟关系后缀（间/中/上/下/畔/口/底…）→ 是方位短语不是专名。
        # 「驱散山间微寒」→ 山后是「间」→ 散山是动词短语，不能报编造地名。
        i=t.find(term)
        if i>=0 and i+len(term)<len(t) and t[i+len(term)] in _GEO_RIGHT_RELATIONAL:
            continue
        out.append(term)
    return out


def _map_strings(node:Any,fn)->Any:
    if isinstance(node,str): return fn(node)
    if isinstance(node,dict): return {k:_map_strings(v,fn) for k,v in node.items()}
    if isinstance(node,list): return [_map_strings(v,fn) for v in node]
    return node


def apply_cost_guard(data:dict[str,Any])->bool:
    """出口成本闸门：把成本/报价数据从成品里彻底摘掉（含价格归零），返回是否清洗过。

    为什么需要这一层：解析期已经不再把成本行喂给模型（document_parser.redact_cost_text），
    但 live 模式下模型仍可能自己写出价格、或把「人均 3806 元」算进叙事文案；而库里
    **已经存在**的老活动更是带着完整成本表（用户截图里那张「费用说明 PRICE」卡片就是）。
    所以成品在离开引擎前必须再过一遍 —— 见 cost_guard.sanitize_for_frontend。
    """
    if not isinstance(data,dict): return False
    mast=data.get('activity_master'); det=data.get('detail')
    if not isinstance(mast,dict): return False
    m,d,changed,_cd=sanitize_for_frontend(mast,det)
    data['activity_master']=m
    if isinstance(det,dict) and isinstance(d,dict): data['detail']=d
    return changed


def apply_fact_guard(data:dict[str,Any],source_text:str)->list[str]:
    """出口事实回检：① 品牌名音译还原成资料原始写法；② 资料里查无此名的地名收集成告警。

    返回可疑地名列表，并同步写进 master['uncertainties']（俱乐部端可见，便于运营核对）。
    internalData 是内部经营资料，**不做任何改写**，避免把运营口径改坏。
    """
    # ★ 成本闸门先跑，且不受 source_text 是否为空影响：成本数据不许进入成品的判定
    #   与被上传资料的完整性无关（见 cost_guard 模块文首）。
    apply_cost_guard(data)
    src=str(source_text or '')
    if not src or not isinstance(data,dict): return []
    def fix(t:str)->str: return restore_brand_names(t,src)
    det=data.get('detail'); mast=data.get('activity_master')
    if isinstance(det,dict): data['detail']=_map_strings(det,fix)
    if isinstance(mast,dict):
        for k in list(mast.keys()):
            # internalData 是运营口径；media 是磁盘文件的映射（ref→url），两者都不是文案，
            # 做品牌名替换只会把 url 改坏（见 restore_brand_names 的说明）。
            if k in ('internalData','media'): continue
            mast[k]=_map_strings(mast[k],fix)
    # 收集可疑地名（只看 C 端可见部分）
    parts=[]
    def collect(n):
        if isinstance(n,str): parts.append(n)
        elif isinstance(n,dict):
            for kk,vv in n.items():
                if kk=='internalData': continue
                collect(vv)
        elif isinstance(n,list):
            for vv in n: collect(vv)
    collect(data.get('detail') or {})
    if isinstance(mast,dict): collect({k:v for k,v in mast.items() if k!='internalData'})
    suspects=set()
    for t in parts: suspects.update(hallucinated_places(t,src))
    out=sorted(suspects)
    notes=[f'地名「{s}」在原始方案里查无此名，疑似改写/编造，请核对' for s in out]
    # 「原文自己前后写法不一致」不在这里判（正则做不到专名识别，试过两版都退回，见文首说明）。
    # 那件事由模型在 uncertainties 里写出来 —— 提示词已明确要求。
    if notes and isinstance(mast,dict):
        un=mast.get('uncertainties')
        if not isinstance(un,list): un=[]
        for note in notes:
            if note not in un: un.append(note)
        mast['uncertainties']=un
    return out


# ---------------------------------------------------------------------------
# 文本清洗：结构标记 / 行内噪声
#
# 上传的方案里，[Slide N] / [Page N] / [文件: xxx] 只是解析骨架，用来切分段落；
# 它们绝不能出现在 C 端成品里。以前没有任何一处清洗，于是行程的最后一格把
# 「[Slide 9] 餐食安排 …[Slide 12] 活动形式&参与人员安排…」整段倒进了页面。
# ---------------------------------------------------------------------------
_MARKER_RE=re.compile(r'\[(?:Slide|Page)\s*\d+\]|\[文件[:：][^\]]*\]')

# 方案里的小节标题（不是地点）。用于否决「把标题当地点」这类错误抽取。
_HEADING_BLOCKLIST={
    '目的地介绍','活动内容','着装建议','场地介绍','天气&交通','行程安排','行程安排&活动人员构成',
    '餐食安排','活动费用','执行保障','活动形式','活动形式&参与人员安排','参与人员安排',
    '介绍&活动亮点','活动亮点','活动安排','费用说明','注意事项','活动须知','活动说明','活动概述',
    '活动介绍','Q&A','参与达人','参与达人list','THANKS','目录',
}
# 只属于内部经营/执行侧的标记。命中这些词的行不进入 C 端成品。
_INTERNAL_MARKERS=('话术','禁区','门店领取','小票','收银','导购','店员','成本','毛利','返点','提成',
                   '结算','供应商','据实','不对外','内部','询价','档期','排期确认','同步至','经手')
# 「写给编辑自己看」的元话语。block 是给读者看的成品，这些字符串一旦上屏就是事故。
_META_PHRASES=('AI应','AI根据','应根据照片','你的任务','占位','示例','待补','TODO','模板','block(')

_KNOWN_PLACES=['山王坪','鱼子西','新都桥','蓥华山','毕棚沟','四姑娘山','达古冰川','青城山','牛背山',
               '海螺沟','木格措','塔公','丹巴','党岭','莫斯卡','格聂','冷噶错','子梅','稻城','亚丁',
               '色达','贡嘎','雨崩','哈巴','长坪沟','双桥沟','折多山','雅拉','红岩顶','达瓦更扎']


def _strip_markers(s:str)->str:
    return _MARKER_RE.sub('',s or '')


def _clean(s:str,limit:int=0)->str:
    """单行清洗：去结构标记、压空白、去首尾标点。用于「解析后的一格短文本」。"""
    s=_strip_markers(s or '')
    s=re.sub(r'\s+',' ',s).strip()
    s=s.strip('|·、，,；;。 ')
    if limit and len(s)>limit:
        s=s[:limit].rstrip('，,、；;。 ')+'…'
    return s


def _clean_text(s:str)->str:
    """多行清洗：保留换行（narrative.body 用 \\n 分句），只去结构标记与多余空行。"""
    s=_strip_markers(s or '')
    s=re.sub(r'[ \t]+',' ',s)
    s=re.sub(r'\n\s*\n+','\n',s)
    return s.strip()


# 宣传正文的钟点区间前缀（「14:20至15:00，…」）：分钟级安排属于 itinerary/timeline，
# 正文以时刻开头是在复述排班表（2026-10-08 用户反馈）。只剥段落开头，不碰句中引用的时间。
_CLOCK_RANGE_RE=re.compile(r'^\s*\d{1,2}[:：]\d{2}\s*[至到~～\-—–]\s*\d{1,2}[:：]\d{2}\s*[,，、：:]?\s*')

def _strip_clock_narrative(s:str)->str:
    """逐段剥掉正文字段开头的「HH:MM至HH:MM，」前缀；模型漏听禁令 H 时的出口兜底。"""
    if not s or '\n' not in s and not _CLOCK_RANGE_RE.match(s): return s
    return '\n'.join(_CLOCK_RANGE_RE.sub('',p) if p.strip() else p for p in s.split('\n'))


def _is_internal(s:str)->bool:
    return any(k in (s or '') for k in _INTERNAL_MARKERS)


def _looks_like_heading(s:str)->bool:
    """判断一个候选串是不是「小节标题」而不是内容（用于否决错误的地点抽取）。"""
    t=_clean(s)
    if not t:return True
    if t.replace(' ','') in {h.replace(' ','') for h in _HEADING_BLOCKLIST}:return True
    # 很短、且不含数字与地理词 → 更像标题而不是地点
    return len(t)<=8 and not re.search(r'[0-9]|省|市|区|县|镇|山|湖|沟|村|岛|园|寺|峰|湾|海|川|营',t)


def _list_items(s:str,limit:int=12)->list[str]:
    """把「A、B、C；D」这类清单串拆成条目，去掉序号、括注与「等用品」这类尾巴。"""
    out=[]
    for part in re.split(r'[、,，/;；\s]+',_clean(s)):
        p=re.sub(r'[（(][^）)]*[）)]','',part)
        p=re.sub(r'^(?:穿着|可备|带上|携带|自备|备好|需备|备)\s*','',p)
        p=re.sub(r'^(?:等用品|等等|等)\s*$','',p)
        p=re.sub(r'(?:等用品|等等)$','',p)
        p=re.sub(r'^[0-9]+[.、)）]?','',p).strip(' ·。.')
        if 2<=len(p)<=14 and p not in out:out.append(p)
        if len(out)>=limit:break
    return out


# 服务条目的「主题」归类。同一个主题只留一条：画像里写好的版本在前、优先保留，
# 抽取版里语义重复的那条丢弃，避免出现「保障：PICC 户外活动意外险」与
# 「保障：户外活动意外险（PICC）」两条几乎同义的话并排显示。
_SERVICE_TOPICS=(('保险','意外险','PICC'),('瑜伽',),('户外团队','持证','向导','导游'),('住宿','酒店'),
                 ('唐卡',),('藏装','藏服'),('糌粑','酥油茶'),('大巴','交通','车费'),('摄影','跟拍'))


def _merge_services(items)->list[str]:
    out=[]; used=set()
    for it in items:
        it=_clean(it)
        if not it or _is_internal(it): continue
        topic=None
        for i,keys in enumerate(_SERVICE_TOPICS):
            if any(k in it for k in keys): topic=i; break
        if topic is not None:
            if topic in used: continue
            used.add(topic)
        elif it in out: continue
        out.append(it)
    return out


def _merge_checklist(items)->list[str]:
    """装备清单去重：'徒步鞋' 与 '防水防滑的徒步鞋' 是同一件东西，只留先出现的那个。"""
    out=[]
    for raw in items:
        c=_clean(raw)
        if not (2<=len(c)<=14): continue
        if any(c in o or o in c for o in out): continue
        out.append(c)
    return out


def _parse_itinerary(raw:str)->list[dict[str,str]]:
    """解析行程。

    优先按「时间 | 项目 | 内容」表格行解析（PPT/Word 里行程最常见的写法）：
    表格天然以行分界，不会越界吞掉后面的章节。旧实现只用「HH:MM-HH:MM」正则扫全文，
    表格最后一行会一路吃到文档结尾，把 [Slide 9]~[Slide 12] 的原文整段塞进行程——
    这是「活动详情乱、没逻辑」最直观的一处。
    """
    items=[]
    rng=re.compile(r'^\d{1,2}:\d{2}\s*[-–—~～至到]\s*\d{1,2}:\d{2}$')
    one=re.compile(r'^\d{1,2}:\d{2}$')
    for line in raw.split('\n'):
        line=line.strip()
        if not line or line.startswith('[') or _is_internal(line):continue
        if '|' not in line:continue
        cells=[c for c in (_clean(x) for x in line.split('|')) if c]
        if len(cells)<2 or not (rng.match(cells[0]) or one.match(cells[0])):continue
        body='：'.join([cells[1]]+([' '.join(cells[2:])] if len(cells)>2 else []))
        body=_clean(body,80)
        if body:items.append({'time':cells[0],'content':body})
    if items:return items
    # 兜底：没有表格时按「时间段 + 同行描述」扫，描述止于行尾，绝不跨行。
    seg=re.compile(r'(\d{1,2}):(\d{2})\s*[-–—~–至到]\s*(\d{1,2}):(\d{2})\s*([^\n]*)')
    for m in seg.finditer(raw):
        desc=_clean(re.split(r'\s{2,}|\[',_clean(m.group(5)))[0],80)
        if not desc:continue
        items.append({'time':f'{int(m.group(1))}:{m.group(2)}–{int(m.group(3))}:{m.group(4)}','content':desc})
    return items


_LIST_SECTION_MARKERS=('装备建议','着装建议','装备清单','携带清单','必备清单','自备物品','出行清单','需携带','需准备')
_HIGHLIGHT_SKIP=('穿着','携带','自备','建议：','费用包含','包含：')


def _extract_highlights(raw:str,limit:int=5)->list[dict[str,str]]:
    """抽取介绍页里的编号条目（1. 2. 3. …）——这是方案里最「能卖」的原生内容。

    只取「目的地介绍 / 活动亮点」这类描述性条目：一旦进入装备·清单类小节就停止，
    否则会把「1. 穿着防水防滑的徒步鞋、…」这种清单项当成景点看点。
    """
    out=[]
    for line in raw.split('\n'):
        s=_clean(line)
        if not s or _is_internal(s):continue
        if any(k in s for k in _LIST_SECTION_MARKERS):break
        m=re.match(r'^[0-9]{1,2}\s*[.、)）]\s*(.{2,70})',s)
        if not m:continue
        desc=_clean(m.group(1),60)
        if any(k in desc for k in _HIGHLIGHT_SKIP) or desc.count('、')>=2:continue
        title=_clean(re.split(r'[（(]',desc)[0],22)
        if len(title)<2:continue
        out.append({'title':title,'desc':desc})
        if len(out)>=limit:break
    return out


def _collect_list_block(raw:str,labels:tuple[str,...],limit:int=14)->list[str]:
    """从「XX装备建议：」这类小节里，按行收集到下一个标题为止，再拆成清单条目。"""
    lines=raw.split('\n')
    for i,line in enumerate(lines):
        if any(l in line for l in labels):
            buf=[]
            for nxt in lines[i+1:i+9]:
                s=nxt.strip()
                if not s:break
                if len(s)<=12 and not re.match(r'^[0-9]',s) and not re.search(r'[、，。;；]',s):break
                buf.append(s)
            if buf:return _list_items(' '.join(buf),limit)
    return []


def _extract_location(raw:str)->str:
    """地点抽取。核心是「绝不把小节标题当地点」——这是旧版把 location 写成
    「介绍&活动亮点」的直接原因（它只是 Slide 2 的标题）。"""
    for pat in (r'(?:活动地点|目的地|地点|地址)[：:]\s*([^\n|]{2,30})',):
        m=re.search(pat,raw)
        if m:
            v=_clean(m.group(1))
            if v and not _looks_like_heading(v):return v
    m=re.search(r'([\u4e00-\u9fa5]{2,8}?(?:省|市|州|区|县))[\u4e00-\u9fa5]{0,8}?'
                r'([\u4e00-\u9fa5]{2,8}?(?:镇|乡|景区|公园|山|湖|沟|村|岛))',raw)
    if m:
        v=_clean(m.group(0))
        if v and not _looks_like_heading(v):return v
    for p in _KNOWN_PLACES:
        if p in raw:return p
    m=re.search(r'([\u4e00-\u9fa5]{2,6}(?:营地|古镇|景区|公园|山|湖|沟|湾|岛))',raw)
    if m:
        v=_clean(m.group(1))
        if v and not _looks_like_heading(v):return v
    return ''


def _public_price(raw:str)->str:
    """只在「对外报价」语境里取价格 —— 成本表里的数字不当售价。

    2026-10-06 用户要求：方案里的成本数据前端一律不显示。此前这里把 `人均费用` 也当售价抓
    （一份始祖鸟高客方案的成本是 ¥3,806.55/人 = 76,131 ÷ 20，抓成 price 后 C 端卡片直接
    按成本价开卖）。现在逐行判定：命中成本行（人均费用/合计/未含税/单价…）就跳过，
    继续往下找真正的公开价格（售价 / 会员价 / 每人 288 元 / 498 元每次）。
    整份资料没有任何公开价格时返回空串，价格留空由俱乐部自己定。
    """
    for ln in str(raw or '').split('\n'):
        if is_cost_row(ln, has_cost_context(raw)):
            continue
        for pat in _PRICE_PATTERNS:
            m=pat.search(ln)
            if m:
                return m.group(1)
    return ''


_PRICE_PATTERNS=(
    # 「售价 ¥498」「会员价 498」「每人 288 元」「人均 498」
    re.compile(r'(?:售价|价格|会员价|新客价|优惠价|每人|人均)[^\n\d]{0,8}?([0-9][0-9,]*(?:\.[0-9]+)?)'),
    # 套餐式报价：「498 元/次」「298/人」
    re.compile(r'([0-9][0-9,]{1,5})\s*元?\s*/\s*(?:次|人|位|场|份)'),
)


def _extract_structured(source:dict[str,Any])->dict[str,Any]:
    """从原始资料文本里「抽取」结构化事实：行程、费用、服务、清单、人数、价格、地点、亮点。

    严格只做抽取、不做创造：抽不到就留空，由 _mock_activity 回落到既有启发式。
    这样即使没有接通大模型（演示引擎/离线），上传的 PPT/Word 方案里的行程与费用明细
    也会真实出现在活动详情里，而不是被固定模板忽略——这正是「上传了方案却完全没按方案」的根因之一。
    """
    raw=_strip_markers((source or {}).get('text','') or '')
    out={'title':'','date':'','location':'','price':0,'capacity':0,'distance':'',
         'itinerary':[],'days':[],'fees':{},'services':[],'checklist':[],'highlights':[]}
    if not raw.strip(): return out

    out['itinerary']=_parse_itinerary(raw)
    out['highlights']=_extract_highlights(raw)
    day_marks=[(m.start(),int(re.search(r'\d+',m.group(0)).group(0)))
               for m in re.finditer(r'(?:DAY|Day|D|第)\s*(\d+)\s*(?:天|日)?',raw)]
    out['days']=sorted({n for _,n in day_marks if n>0})

    # ---- 距离 / 人数 / 人均价格 / 合计 / 地点 / 日期 / 标题 ----
    d=re.search(r'([0-9]+(?:\.[0-9]+)?)\s*(?:km|KM|Km|公里)\s*(?:轻徒步|徒步|环线|线路|往返)?',raw)
    if d: out['distance']=_clean(d.group(1))+'km'

    cap=re.search(r'(?:活动人数|成行人数|人数|规模)\s*[：:]?\s*(\d{1,3})\s*人',raw) \
        or re.search(r'(\d{1,3})\s*人\s*(?:整车成行|成行|规模)',raw)
    if not cap:
        # 口语化写法兜底：「带 12 个会员去…」「限 20 人」——只认 2~3 位、且后缀明确是人，
        # 避免把「6 公里」「人均 288 元」误当人数，也排除「10 人一桌 / 4 人一车」这类计量单位。
        for m in re.finditer(r'(\d{2,3})\s*(?:个|位|名)?\s*(?:会员|人)(?!均|民|数|一?\s*(?:桌|围|锅|份|车|排|房))',raw):
            n=int(m.group(1))
            if 2<=n<=500: cap=m; break
    if cap: out['capacity']=int(cap.group(1))
    # ---- 价格：只在「对外报价」语境里取，成本表里的数字一律不当售价 ----
    per=_public_price(raw)
    if per:
        try: out['price']=float(per.replace(',',''))
        except ValueError: pass
    out['location']=_extract_location(raw)
    dts=re.findall(r'(20\d{2})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?',raw)
    if dts: out['date']=f'{dts[0][0]}-{int(dts[0][1]):02d}-{int(dts[0][2]):02d}'
    # 标题：优先「X天X夜」行程名；再退回资料里写明的活动名称。
    # 不再用「目的地名兜底」——那会把行政地名当标题，交给 _mock_activity 组装更干净。
    tit=re.search(r'([\u4e00-\u9fa5]{2,12}?[两二三四]天[一二三四]夜)',raw)
    if not tit: tit=re.search(r'(?:活动名称|活动主题|主题)[：:]?\s*([^\n]{2,24})',raw)
    if tit: out['title']=_clean(tit.group(1)).strip('·')

    # ---- 费用说明：只写顾客该知道的项，**只写项名、不写金额** ----
    # 2026-10-06 用户要求：方案里的成本数据前端一律不显示。此前这里把成本表抓成了 fees：
    # 人均费用（= 合计 ÷ 人数）、合计（未含税）、按人数报价、以及「本表为方案成本预估」的备注，
    # 前端再渲染成「费用说明 PRICE」卡片 —— 等于把俱乐部的成本底价和利润结构摊给顾客看。
    # 现在只留「费用包含」这一项服务范围（车费/门票/氧气/摄影…），价格与成本一概不写。
    items=[]
    for kw in ['车费','保姆大巴','越野中转车','中转车','午餐','晚餐','特色餐','住宿','门票','唐卡体验','唐卡',
               '饮用水','氧气','摄影','领队','保险','工作餐']:
        if kw in raw and kw not in items: items.append(kw)
    if items: out['fees']['费用包含']='、'.join(items[:14])

    # ---- 费用不含：顾客自理项，定性不定量（绝不写金额/成本） ----
    # 与「费用包含」对称：资料里常单列一栏「费用不含」（单房差、往返大交通、个人消费…）。
    # 这些不是成本底价，是顾客该提前知道的边界，属于公开信息，可保留；只抓定性项名，
    # 任何带「元 / ¥ / 数字金额」的片段一律丢弃（成本铁律不变）。
    no_items=[]
    for nkw in ['单房差','往返大交通','往返交通','个人消费','自费项目','行李托运','骑马','缆车','小费',
                '温泉','景区内自费','氧气自费','保险自理','门票自理','签证','机票']:
        if nkw in raw and nkw not in no_items: no_items.append(nkw)
    m=re.search(r'费用不含[：:]\s*([^\n]{0,200})',raw)
    if m:
        for it in re.split(r'[、，,；;]+',m.group(1)):
            it=_clean(it).strip('。. ')
            if it and len(it)<=12 and it not in no_items and '元' not in it and '¥' not in it and not re.search(r'\d',it):
                no_items.append(it)
    if no_items:
        # 去重：剔除被其他项完整包含的短串（如「温泉」⊂「温泉自费」），避免重复列项
        kept=[]
        for it in no_items[:10]:
            if not any(it!=o and it in o for o in no_items): kept.append(it)
        out['fees']['费用不含']=kept

    # ---- 服务：只写资料里明确提到的，不凭空补充；命中内部标记的一律丢弃 ----
    services=[]
    def _add(s):
        s=_clean(s)
        if s and not _is_internal(s) and s not in services: services.append(s)
    if ('保姆大巴' in raw) or ('旅游营运' in raw): _add('交通：正规旅游保姆大巴（持证旅游营运资质）')
    if '赞巴明镜酒店' in raw: _add('住宿：新都桥·赞巴明镜酒店（两晚连住不挪行李）')
    elif '住宿' in raw: _add('住宿：'+((out['location'] or '目的地'))+'当地安排')
    if '唐卡' in raw: _add('体验：国家非遗唐卡绘制')
    if ('藏装' in raw) or ('藏服' in raw): _add('体验：康巴藏装换装打卡')
    if ('糌粑' in raw) or ('酥油茶' in raw): _add('体验：酥油茶 / 手工糌粑品鉴')
    if ('PICC' in raw) or ('户外保险' in raw) or ('意外险' in raw): _add('保障：户外活动意外险（PICC）')
    elif '保险' in raw: _add('保障：含户外旅游保险')
    if '瑜伽' in raw: _add('体验：户外瑜伽（垫子与活动道具由主办方提供）')
    if ('持证' in raw) and (('户外' in raw) or ('导游' in raw) or ('向导' in raw)): _add('保障：专业持证户外工作人员 / 导游全程随行')
    if ('专业户外' in raw) or ('户外团队' in raw): _add('保障：专业户外团队全程随行')
    if '摄影' in raw: _add('服务：全程摄影师跟拍')
    out['services']=services

    # ---- 出行清单：优先「装备建议 / 着装建议」小节，其次通用清单标题 ----
    out['checklist']=_collect_list_block(raw,('装备建议','着装建议','装备清单','携带物品','携带清单','必备清单','自备物品','出行清单','需携带','需准备'))
    return out


# ---------------------------------------------------------------------------
# 目的地画像：离线演示引擎的「编辑判断」。
#
# 接通真实大模型时不会用到；它存在的意义是——在没有模型的环境里（本地演示、
# 模型不可用时的兜底），也必须按真实资料产出有叙事、有吸引力的成品，
# 而不是把 PPT 原文倒进页面。所有内容均取自用户上传方案的公开事实。
# ---------------------------------------------------------------------------
_DESTINATION_PROFILES=[
    {
        'keys':['山王坪'],
        'title':'山王坪赏秋｜森林瑜伽 × 5km 林海徒步',
        'location':'重庆 · 南川 · 山王坪',
        'distance':'5km 轻徒步',
        'idea':'从北城天街出发约两小时，走进中国首批森林氧吧：上午在多片树林之间做一场森林瑜伽，下午沿 5 公里步道穿过「平分秋色」的双色杉林。',
        'understanding':'城市近郊的一日自然体验。真正的卖点是「两小时车程就能抵达的秋天」，而不是徒步强度——所以开场先给季节与森林，再交代瑜伽与路线，最后才收束到费用与保障。',
        'beats':[
            {'eyebrow':'两小时之外','headline':'车程两小时，气温先降下来',
             'body':'山王坪海拔约 1300 米，森林覆盖率 96%，是中国首批森林氧吧。\n十月底，这里的杉林刚开始变色。',
             'pull':'把周末交给森林'},
            {'eyebrow':'平分秋色','headline':'一条公路，两边是不同的季节',
             'body':'一侧水杉正在转金黄，另一侧柳杉四季常绿。\n登上林海观景塔，两种颜色在脚下泾渭分明。',
             'pull':'一边是秋，一边是春'},
            {'eyebrow':'林间瑜伽','headline':'先让身体安静，再开始走路',
             'body':'瑜伽场地藏在多片树林之间，可按当天光线任选一处。\n垫子与道具都由主办方备好，你只需要带上自己。',
             'pull':'呼吸先于脚步'},
        ],
        'statements':['两小时车程，走进一整片秋天。'],
        'checklist':['徒步鞋','速干衣裤','防晒外套','单日徒步小背包','防晒帽','登山杖','充电宝','雨衣','护唇膏'],
        'services':['体验：森林瑜伽（瑜伽垫与活动道具由主办方提供）','保障：PICC 户外活动意外险','保障：专业户外团队全程随行'],
    },
    {
        'keys':['蓥华山'],
        'title':'MOVE TO NATURAL｜蓥华山徒步 × 户外瑜伽',
        'location':'四川 · 什邡 · 蓥华山',
        'distance':'6km 轻徒步',
        'idea':'先把身体打开，再走进森林。不是把瑜伽和徒步简单拼在一起，而是让一整天从呼吸、伸展自然过渡到山野行走。',
        'understanding':'品牌会员自然体验日：上午户外瑜伽建立身体状态，午后完成约六公里轻徒步；重点不是「打卡景区」，而是完整的一日自然节奏。',
        'beats':[
            {'eyebrow':'上午｜打开身体','headline':'先不急着走',
             'body':'抵达之后，先让身体从城市节奏里松开。\n呼吸、伸展、草地与山风，构成这一天的第一部分。',
             'pull':'呼吸先于脚步'},
            {'eyebrow':'午后｜走进森林','headline':'再把自己交给山野',
             'body':'午饭以后进入蓥华山步道，完成约六公里轻徒步。\n路线不长，刚好够一整天完整地落在身体上。',
             'pull':'六公里，刚好'},
        ],
        'statements':['先用一小时把身体打开，再用六公里把自己交给森林。'],
        'checklist':['徒步鞋','速干衣裤','防晒外套','瑜伽服','单日徒步小背包','防晒帽','登山杖','充电宝'],
        'services':['体验：户外瑜伽（垫子与活动道具由主办方提供）','保障：含户外旅游保险','保障：专业户外团队全程随行'],
    },
    {
        'keys':['鱼子西','新都桥'],
        'title':'川西两天一夜｜新都桥 × 鱼子西',
        'location':'四川 · 康定 · 新都桥 · 鱼子西',
        'distance':'',
        'idea':'把两天交给川西：雪山观景、藏地体验与高品质休息不抢戏，而是共同组成一段完整的高原旅行。',
        'understanding':'目的地与体验并重的两日高品质户外旅行，应以景观期待与旅程节奏驱动，而不是罗列景点。',
        'beats':[
            {'eyebrow':'两天一夜','headline':'高原把节奏放慢了',
             'body':'两天时间不赶路，把景观、体验与休息都放进去。\n第一天的路程本身就是风景。',
             'pull':'慢慢走，看得更多'},
            {'eyebrow':'光落下来','headline':'川西最动人的从来不是地名',
             'body':'一路不断变化的光、雪山、草原，和停下来的那些片刻，才是这趟行程真正被记住的部分。',
             'pull':'记住的是光'},
        ],
        'statements':['真正让人记住川西的，不是一个地名，而是一路不断变化的光。'],
        'checklist':['冲锋衣','抓绒外套','徒步鞋','保暖帽','手套','墨镜','防晒霜','保温杯','登山杖','充电宝'],
        'services':['住宿：新都桥当地安排','保障：含户外旅游保险','保障：专业户外团队全程随行'],
    },
]


def _profile_for(text:str)->dict[str,Any]|None:
    for p in _DESTINATION_PROFILES:
        if any(k in text for k in p['keys']): return p
    return None


def _forms(text:str)->list[str]:
    return [k for k in ('徒步','登山','瑜伽','露营','骑行','桨板','皮划艇','漂流','滑雪','探洞',
                        '温泉','观星','摄影','越野','溯溪') if k in text]


def _generic_idea(text:str,location:str,st:dict[str,Any])->str:
    place=location or '目的地'
    forms=_forms(text); hl=st.get('highlights') or []
    if hl and forms:
        return f'{place}，{hl[0]["title"]}。一天里安排{"、".join(forms[:2])}，不赶行程。'
    if hl: return f'{place}，{hl[0]["title"]}。'
    if forms: return f'{place}的一日{forms[0]}，把周末交给山野。'
    return f'{place}的户外一日，从集合出发到返程都已经安排好。'


def _generic_understanding(text:str,location:str,st:dict[str,Any],hl:list[dict[str,str]])->str:
    place=location or '目的地'
    kind='、'.join(_forms(text)[:2]) or '户外体验'
    if hl: return f'以{place}的{hl[0]["title"]}为核心卖点，由{kind}串起一天的节奏。'
    return f'一场以{kind}为主线的户外一日行程，卖点是现场体验，而不是路线数据。'


def _generic_beats(hl:list[dict[str,str]],text:str,location:str)->list[dict[str,str]]:
    place=location or '这里'
    beats=[]
    if hl:
        beats.append({'eyebrow':'为什么是这里','headline':_clean(hl[0]['title'],16),'body':hl[0]['desc'],'pull':''})
    if len(hl)>1:
        beats.append({'eyebrow':'现场看点','headline':'值得为它出发',
                      'body':'\n'.join(h['title'] for h in hl[1:4]),'pull':''})
    forms=_forms(text)
    if forms:
        beats.append({'eyebrow':'怎么玩','headline':'一天这样安排',
                      'body':f'以{forms[0]}为主线，其余时间留给休息、吃饭和看风景。','pull':''})
    if not beats:
        beats.append({'eyebrow':'出发','headline':f'去{place}走一趟',
                      'body':f'{place}的这一天，从集合出发到返程都安排好了。','pull':''})
    return beats


def _split_lines(txt:str)->list[str]:
    """把模型写成一段多行文本的条目拆成行（去项目符号，最多 8 条）。"""
    return [s.strip().lstrip('-·•').strip()
            for s in re.split(r'[\n；;]+', str(txt or '')) if s.strip()][:8]


def _lines_to_items(txt:str)->list[dict[str,str]]:
    """「标签：值」逐行还原成 facts 的条目；没有冒号的行整行当值。"""
    rows=[]
    for s in _split_lines(txt):
        m=re.match(r'^([^：:]{1,14})[：:]\s*(.+)$', s)
        rows.append({'label':m.group(1).strip(),'value':m.group(2).strip()} if m else {'label':'','value':s})
    return rows


def _sanitize_blocks(blocks:list[dict[str,Any]],allowed_refs:set[str]|None=None)->list[dict[str,Any]]:
    """最终防线：清掉残留的结构标记、「写给编辑自己看」的元话语，以及不该上屏的图片。

    block 是给 C 端读者看的成品。[Slide 9]、「AI应根据照片自己判断哪些瞬间值得放大」
    这类字符串一旦上屏，就是最刺眼的事故；品牌 logo / 字标 / 空白底图被当成活动照片，
    同样是事故。这里统一收口，任何出口都不许漏。

    allowed_refs：允许出现在成品里的图片 ref 集合（真实照片）。传 None 表示不按图片过滤。
    """
    out=[]
    for b in blocks or []:
        if not isinstance(b,dict): continue
        btype=str(b.get('type') or '')
        # 「出行清单」已由平台 renderPacking(master) 按 master.checklist 渲染（带商城匹配与会员价）。
        # 生成侧再出一块「装备建议」= 同一份清单在页面上出现两遍，这里直接剔除。
        if btype=='info':
            ttl=str(b.get('title') or b.get('headline') or '')
            if any(g in ttl for g in _GEAR_BLOCK_TITLES): continue
        nb={}
        for k,v in b.items():
            if isinstance(v,str): nb[k]=_clean_text(v)
            elif isinstance(v,list):
                nv=[]
                for it in v:
                    if isinstance(it,dict): nv.append({kk:(_clean_text(vv) if isinstance(vv,str) else vv) for kk,vv in it.items()})
                    elif isinstance(it,str): nv.append(_clean_text(it))
                    else: nv.append(it)
                nb[k]=nv
            else: nb[k]=v
        # 正文 prose 字段再过一道「钟点叙事」兜底（禁令 H）；只碰 block 级正文，
        # 不碰 timeline items 的 time/text（那里的时刻是结构化行程，本来就该有）。
        for k in ('body','text','subtitle'):
            if isinstance(nb.get(k),str): nb[k]=_strip_clock_narrative(nb[k])
        # 图片只留真实照片；纯图片块被清空后整块丢弃（文字块保留，只是没有配图）
        if allowed_refs is not None and isinstance(nb.get('mediaRefs'),list):
            nb['mediaRefs']=[r for r in nb['mediaRefs'] if r in allowed_refs]
            if btype in ('media','gallery') and not nb['mediaRefs']: continue
        texts=[str(nb.get(k) or '') for k in ('headline','subtitle','body','text')]
        if any(p in t for t in texts for p in _META_PHRASES): continue
        # ★ 结构兜底（2026-10-09，实测 qwen3-max 会这么写）：模型把要点表写成一段多行文本放进 body，
        #   而前端 facts / info 只认 items —— 结果是页面上出现一个**空白块**，整块内容凭空消失。
        #   这里把「标签：值」逐行还原成条目，宁可见到朴素的一行，也不要一整块空白。
        if btype in ('facts','info') and not (isinstance(nb.get('items'),list) and nb['items']):
            raw=str(nb.get('body') or nb.get('text') or nb.get('caption') or '')
            if btype=='facts':
                rows=_lines_to_items(raw)
                if rows:
                    nb['items']=rows
                    nb.pop('body',None); nb.pop('text',None)
            else:
                rows=_split_lines(raw)
                if rows:
                    nb['items']=rows
                    nb.pop('body',None)
                    # info 的标题前端读 title（headline 是别的块的字段），别让「费用说明」丢掉
                    if not nb.get('title') and nb.get('headline'): nb['title']=nb['headline']
        out.append(nb)
    return out


def _mock_activity(source:dict[str,Any]):
    """Offline demo only. It demonstrates editorial judgement, not template families.

    Live mode delegates structure decisions to the configured model. 未接入大模型时，
    这里必须靠「目的地画像 + 资料抽取的事实」产出有开场、有叙事、有事实、有保障的成品；
    任何情况下都不得把 [Slide N]、「AI应根据照片…」这类中间字符串泄露到 C 端。
    """
    text=_strip_markers(source.get('text','') or '')
    # 只用真实照片：品牌 logo / 字标 / 空白底图 / 截图在解析期已被标成 kind='logo'。
    # 不做「一张照片都没有就退回全部图片」的兜底——那等于把品牌 logo 又放回头图，
    # 正是用户明确要求不要出现的东西；没有照片时 hero 交给平台封面/底色兜底。
    imgs=_photo_refs(source)
    st=_extract_structured(source)
    prof=_profile_for(text) or {}
    hl=st.get('highlights') or []

    # ---- 事实：画像 > 资料抽取 > 兜底组装（绝不用小节标题当地点/标题）----
    location=prof.get('location') or st.get('location') or ''
    title=prof.get('title') or st.get('title') or ''
    if not title:
        forms=_forms(text)
        if location and forms: title=f'{location}｜{forms[0]}'
        elif location: title=location
        elif forms: title=f'周末{forms[0]}计划'
        else: title='周末自然计划'
    date=st.get('date') or _guess(text,[r'(20\d{2}[./年-]\s*\d{1,2}[./月-]\s*\d{1,2}日?)',r'(\d{1,2}月\d{1,2}日)'],'待发布')
    distance=prof.get('distance') or st.get('distance') or ''
    price_val=float(st['price']) if st.get('price') else 0.0
    # 人数只在资料里明确写了才对外展示：默认 30 只是给团期用的可编辑初值，不是活动事实，
    # 直接印进「服务与保障」等于替老板对外承诺了一个成行人数。
    cap_extracted=bool(st.get('capacity'))
    capacity=_guess(text,[r'(?:人数|规模)[^\n]{0,12}?([0-9]{1,3})\s*(?:人|组)'],'30')
    cap_val=int(st['capacity']) if cap_extracted else (int(capacity) if str(capacity).isdigit() else 30)
    price_text=('¥'+('%g'%st['price'])) if st.get('price') else ''

    idea=prof.get('idea') or _generic_idea(text,location,st)
    understanding=prof.get('understanding') or _generic_understanding(text,location,st,hl)

    blocks=[]
    blocks.append({'type':'hero','headline':title,'kicker':location or '户外活动','subtitle':idea,'mediaRefs':imgs[:1]})
    blocks.append({'type':'lead','text':idea})

    facts=[]
    if date and date!='待发布': facts.append({'label':'DATE','value':date})
    if distance: facts.append({'label':'HIKE','value':distance})
    if location: facts.append({'label':'PLACE','value':location})
    if price_text: facts.append({'label':'PRICE','value':price_text})
    if facts: blocks.append({'type':'facts','items':facts[:4]})

    remaining=imgs[1:]
    if remaining[:4]: blocks.append({'type':'gallery','mediaRefs':remaining[:4]})

    # 大数字统计卡：把距离/人数/费用做成视觉冲击，而不是塞进 facts 条（2026-10-08 新组件）。
    _nc=[]
    if distance: _nc.append({'value':distance,'label':'单日里程'})
    if cap_extracted: _nc.append({'value':str(cap_val)+' 人','label':'成行人数'})
    if price_text: _nc.append({'value':price_text,'label':'费用起'})
    if len(_nc)>=2: blocks.append({'type':'numbercards','items':_nc[:3]})
    # 整版大图：照片足够时铺一张跨页式的"呼吸大瞬间"（2026-10-08 新组件）。
    if len(imgs)>=7 and remaining[6:7]: blocks.append({'type':'bigimage','mediaRefs':remaining[6:7],'caption':'往期同线路实拍'})

    # ---- 叙事：画像给「编辑判断」，没有画像时用资料里抽取的真实亮点 ----
    beats=prof.get('beats') or _generic_beats(hl,text,location)
    def _narr(b):
        return {'type':'narrative','eyebrow':b.get('eyebrow',''),'headline':b.get('headline',''),
                'body':b.get('body',''),'pull':b.get('pull','')}
    if beats: blocks.append(_narr(beats[0]))
    stmts=prof.get('statements') or []
    if stmts: blocks.append({'type':'statement','text':stmts[0]})
    for i,b in enumerate(beats[1:3]):
        if i==0 and remaining[4:6]: blocks.append({'type':'media','mediaRefs':remaining[4:6],'layout':'pair'})
        blocks.append(_narr(b))

    # ---- 事实信息：行程 / 服务与保障 / 装备建议 ----
    if st.get('itinerary'):
        blocks.append({'type':'timeline','title':'行程安排',
                       'items':[{'time':it['time'],'text':it['content']} for it in st['itinerary'][:16]]})

    services=_merge_services(list(prof.get('services') or [])+list(st.get('services') or []))
    info=[]
    # 成行人数是活动事实、可以对外；「人均费用」是成本推导出来的内部单价，一律不写
    # （2026-10-06 用户要求：成本数据前端不显示。此前这里把 ¥3,806.55 印进了「服务与保障」）。
    if cap_extracted: info.append('成行人数 '+str(cap_val)+' 人')
    info+=services
    if info: blocks.append({'type':'info','title':'服务与保障','items':info[:10]})

    # 出行清单已由平台侧 renderPacking(master) 按 master.checklist 渲染（带商城匹配 / 平替 /
    # 会员价），这里绝不另出一块「装备建议」——否则同一份清单在页面上出现两遍。
    # checklist 仍然写进 master，供平台那一块使用。
    checklist=_merge_checklist(list(prof.get('checklist') or [])+list(st.get('checklist') or []))

    if remaining[6:]: blocks.append({'type':'media','mediaRefs':remaining[6:10],'layout':'mosaic'})

    master={
        'title':title,'date':date,'location':location,
        'price':price_val,
        'capacity':cap_val,
        'publicFacts':{'date':date,'location':location,'price':price_text,'distance':distance},
        'itinerary':st.get('itinerary') or [],
        'fees':st.get('fees') or {},
        'checklist':checklist,
        'services':services,
        'internalData':{},'uncertainties':[],'blocking_conflicts':[],
        'media':media_catalog(source),
        'sourceSummary':text[:8000]
    }
    detail={
        'activityUnderstanding':understanding,
        'coreSellingIdea':idea,
        'editorialIntent':{'opening':'由当前活动最强的出发动机决定','visualWeight':'由素材质量决定','template':'NONE'},
        'blocks':_sanitize_blocks(blocks)
    }
    data={'activity_master':master,'detail':detail}
    apply_cost_guard(data)   # 成本闸门：mock 也不豁免（成品的最后一道门）
    return data


def media_catalog(source:dict[str,Any],master:dict[str,Any]|None=None)->list[dict[str,Any]]:
    """权威媒体清单：ref → url 只能来自真实上传/磁盘资料，模型无权定义媒体条目。

    live 模式下模型会照着 prompt 里的清单回写 media，但它只会写 ["img_01",...] 这种纯 ref，
    于是前端 mediaMap 拿不到任何 url：所有图片退化成灰色占位块、头图退化成纯色块。
    这里以 source 的媒体清单为准重建，模型在 blocks 里只被允许「引用」ref。
    """
    # kind 由解析期判定（photo / logo）：它既让 live 提示词能要求「只用 photo」，
    # 也让前端在需要时能知道某张图是品牌标而不是照片。缺失按 photo 处理。
    keys=('ref','name','url','width','height','orientation','source','page','kind','desc','usable','tags')
    catalog={}
    def add(x):
        if not isinstance(x,dict):return
        ref=str(x.get('ref') or '').strip()
        if not ref:return
        cur=catalog.get(ref)
        if cur is None:
            if not str(x.get('url') or '').strip():return   # 没有 url 的条目对渲染毫无价值，不能进清单
            catalog[ref]={k:x.get(k) for k in keys if x.get(k) is not None}
            return
        # 同一个 ref 会在 media_manifest / images / 模型输出里各出现一次，缺什么补什么，而不是丢掉整条。
        for k in keys:
            if cur.get(k) in (None,'') and x.get(k) not in (None,''):cur[k]=x[k]
    for x in (source or {}).get('media_manifest') or []:add(x)
    for x in (source or {}).get('images') or []:add(x)
    for x in (master or {}).get('media') or []:add(x)   # 老数据里已带 url 的条目照样保留
    return list(catalog.values())


def detail_outline(detail:dict[str,Any])->str:
    """「换一版」用的上一版结构摘要：只给 block 顺序与短标题，让模型知道别重复，又不至于照抄。"""
    parts=[]
    for b in (detail or {}).get('blocks') or []:
        if not isinstance(b,dict): continue
        kind=str(b.get('type') or '?')
        head=re.sub(r'\s+',' ',str(b.get('headline') or b.get('title') or b.get('text') or '')).strip()[:36]
        parts.append(f'{kind}({head})' if head else kind)
    return ' → '.join(parts)


def _revision_block(direction:str,previous_detail:dict[str,Any]|None,version_no:int)->str:
    """换版要求段落。第一版且没有额外要求时不出现，避免干扰「少问先做」。"""
    direction=(direction or '').strip()
    outline=detail_outline(previous_detail or {})
    if version_no<=1 and not direction: return ''
    lines=[f'\n这是第 {version_no} 版，不是第一版。老板对上一版不满意，成品必须明显不同：开场方式、图片与文字的权重、区块顺序都要换，标题与句子不得与上一版重复。']
    if outline: lines.append(f'上一版的页面结构（只用于避免重复，不要照抄）：{outline}')
    if direction: lines.append(f'老板的换版要求（必须满足，但依然不得创造事实）：{direction}')
    else: lines.append('老板没有指定方向：请自己换一个最能促成报名的切入角度重做。')
    return '\n'.join(lines)+'\n'


def _rotate_layout(blocks:list[dict[str,Any]],variant:int)->list[dict[str,Any]]:
    """演示模式（mock）专用：按版本号做真实的结构轮换。

    真实调用时版式由模型自己决定；这里只是让没接通大模型的环境也能看到「换一版确实换了」，
    而不是反复吐出同一份成品。轮换只调整区块顺序与图片权重，不编造任何事实。
    """
    if variant<=0 or not blocks: return blocks
    out=list(blocks)
    def pull(kind):
        for i,b in enumerate(out):
            if isinstance(b,dict) and b.get('type')==kind: return out.pop(i)
    if variant%3==1:
        for kind,pos in (('facts',1),('statement',2)):
            b=pull(kind)
            if b: out.insert(min(pos,len(out)),b)
    elif variant%3==2:
        for b in out:
            refs=b.get('mediaRefs') if isinstance(b,dict) else None
            if isinstance(b,dict) and b.get('type')=='gallery' and refs and len(refs)>2:
                b['mediaRefs']=refs[:2]
        b=pull('statement')
        if b: out.insert(1,b)
    else:
        b=pull('gallery')
        if b: out.insert(1,b)
    # 兜底：若上面的定向轮换因为该模板缺少对应 block 而没有任何变化，
    # 做一次确定性整体循环移位，保证 mock 模式下「换一版」肉眼可见地不同，
    # 且不编造任何事实、不改动事实区块的内容，只换顺序。
    if out == list(blocks) and len(out) > 1:
        k = variant % len(out)
        if k:
            out = out[k:] + out[:k]
    return out


# ── 图文对应（2026-10-09 用户反馈：文案在讲酒店，配图却是火锅和烧烤）───────────
# 根因：生成详情时模型拿到的媒体清单只有 img_01/宽高/来源，**看不到画面里是什么**，
# 配图等于随机抽。修法分三层：① 视觉打标（image_caption）把「画面里有什么」变成
# 文字清单；② 提示词按主题标签规定「讲什么配什么」；③ 出口再跑一次确定性对题检查，
# 明显错配就换成更对题的未用图，找不到就撤掉这张图（宁可只有文字，也不配错图）。

_PHOTO_MATCH_RULES = """
★★ 图文对应铁律（2026-10-09 用户反馈：文案在讲酒店，配的却是火锅和烧烤）：
- 每张图必须和它所在区块的文字讲**同一件事**。选图只按上面清单里的主题标签判断：
  讲住宿只配「住宿」的图；讲餐饮只配「餐食」；讲唐卡只配「唐卡与绘画」；讲藏装只配
  「人像与藏装」；讲徒步配「徒步与队伍」；讲雪山/日照/云海配「山景与天象」；讲吃住之外的
  森林花海配「森林与植被」。
- gallery / media 图片组是「某一段文字的多张实拍」，必须紧跟它所说明的那段内容，组内主题一致。
  不要把餐食图摆到住宿段落下面当氛围图。
- ★ 证书 / 证件 / 资格证 / 文件 / 截图 / 二维码类照片**不是活动照片**，禁止给任何区块配图。
  文字里讲老师、领队、教练、资质，也**不许**配「某人的证书」——顾客看到陌生人的证件照只会
  觉得场合错了；资质用文字陈述即可，一张都不要配。
- 一张图全篇只出现一次：同一个 ref 不许出现在两个区块里（gallery 之间也不行）。
- **找不到对得上的图就别给这个区块配图**。整块只有文字，也比配错图好：顾客看到
  「讲酒店配火锅」，只会认为这家俱乐部不专业。
- 图注（caption）只写画面上真实存在的东西，不要写画面里没有的承诺。
"""

# 视觉编排契约：把「排版自由铁律」的软建议升级为硬指标。
# 模型对硬性/否定约束服从度高、对软建议容易忽略 —— 之前只有软建议导致 31 张图只用了 5 张。
_LAYOUT_CONTRACT = """
★★ 视觉编排硬指标（与「排版自由铁律」同方向，但这是**必须做到**的下限，不是建议）：
照片是这场招募详情的主角。可用照片越多，越要把它们铺满页面，做成画报，而不是堆文字。
1. **用图密度下限**：可用照片共 N 张时，整篇详情**至少引用 min(N, max(8, round(N*0.6))) 张不同的照片**
   （每张 ref 全篇只出现一次，「用图数」＝被引用的不同 ref 数量）。照片多却只用几张＝偷懒，必须铺开。
2. **视觉块占比下限**：blocks 中至少 1/3 必须是带图或强视觉组件 —— bigimage / imagetext / gallery /
   media / numbercards / cards 加起来不少于 block 总数的 1/3。禁止从头到尾都是「标题 + 正文 + 图」的同一种节奏。
3. **首屏视觉锚点**：开篇 lead / 第一个 narrative 之后，尽早安排一个 bigimage（整版铺满大图）或 imagetext，
   让顾客第一屏就看到画面，不要一上来三屏纯文字。
4. **gallery 不少于 2 组**：至少做 2 个 gallery 区块，每组 3~6 张**同主题**实拍，分别挂在不同的行程 / 体验 /
   住宿段落下面（gallery 是消耗照片最快、最像画报的手法）。
5. **numbercards 至少 1 组**：用大数字突出 距离 / 海拔 / 天数 / 人数 / 累计爬升 等硬指标，做成 numbercards。
   每张卡**必须有真实数字**（value/number 字段），不许只给一句 label 没有数字；数字取自资料，不许编造。
6. **主题维度覆盖**：资料里出现的维度（目的地风貌 / 行程 / 住宿 / 体验活动 / 后勤须知 / 报名方式）都要有对应
   区块，不要只写行程和费用两块就结束。
7. **节奏交替**：禁止连续超过 3 个纯文字 block 不配图；图文穿插，每 2~3 个文字块就插一个视觉块或 gallery。
8. **信息只讲一遍**（2026-10-10 用户反馈：服务清单下面又来一个「关于交通与后勤」重复讲同样的内容）：
   服务承诺 / 费用包含 / 交通接送 / 住宿安排 / 高原须知 / 证件要求，每个主题全篇只允许出现在**一个**区块里。
   facts 与 info（出发前知道）严禁重复同一主题——写 info 前先检查前面 blocks 已经讲过什么，只补充还没讲过的信息。
9. **timeline 只写节奏，不抄行程**：timeline 块的 value 是「行程节奏 / 高原适应策略」的说明文字
   （headline + text），**不要**在 timeline.items 里逐条复制行程时刻——逐条行程由页面结构化的
   「详细行程 ITINERARY」区完整呈现，正文再排一份就是同页两个行程板块。仅当原始资料里根本没有
   逐日行程时，才允许 timeline 携带 items 充当唯一时刻表。
以上 9 条是下限，达成后鼓励更丰富。若可用照片不足 N*0.6 张，就以「全部可用照片」为下限（绝不要求超过实际拥有）。"""

# 主题词表：只用于「判断图文是不是在讲两件事」。同类词命中即视为同一主题。
# ★ 只用**具体名词 + 不会出现在视觉描述里的散文动词**：视觉模型给的标签里就有「住宿」，
#   一旦它也当成关键词，帐篷照（标签=住宿）就会命中酒店段落——而酒店配上帐篷同样是错配。
#   所以「住宿」这个抽象类别名本身不放进来；放的是「住进 / 入住 / 下榻」这类只在文案里出现的动词，
#   它们不会出现在图片描述里，却能帮对题检查认出「讲住宿的段落」，从而把雪山图换成真酒店图。
_TOPIC_WORDS=(
    ('住宿',('酒店','民宿','客房','房间','标间','大床','床铺','床头','大堂','前台','客栈','别墅','庭院','阳台','木屋','浴室','洗手间',
             '住进','入住','下榻','山庄','房型','度假','夜宿','过夜','落脚','安顿','歇脚','度假村','营房','标房')),
    ('营地与帐篷',('帐篷','营地','露营','睡袋','天幕','营地灯')),
    ('餐食',('火锅','餐','饭','菜','烤肉','烧烤','美食','食物','碗','盘','糌粑','甜茶','奶茶','茶壶','早餐','午餐','晚餐','野餐','汤锅','点心','咖啡','水果','牛肉',
             '用餐','品尝','享用','风味','佳肴','盛宴','伙食','野炊','围炉','舌尖')),
    ('唐卡与绘画',('唐卡','绘制','画笔','颜料','佛像','经书','手绘','描线','上色','造像','素描')),
    ('人像与藏装',('人像','合影','藏装','服饰','穿着','拍照','女子','男子','游客','人群','背影','笑脸','自拍')),
    ('山景与天象',('雪山','云海','银河','星空','日落','日出','金山','日照','经幡','峡谷','冰川','彩虹','湖','海子')),
    ('森林与植被',('森林','树林','落叶','草甸','草原','苔藓','彩林','花海','草地')),
    ('徒步与队伍',('徒步','登山','行走','小路','栈道','背包','爬升','公路','队伍','队列','山脊','小径')),
    ('动物',('牦牛','马匹','牛羊','羊群','小狗','鸟群')),
    ('交通',('大巴','巴士','汽车','飞机','火车','缆车','座椅','车厢')),
    ('手作与体验',('手作','制作','捏','揉','非遗','手工艺','抽打','搅拌')),
    ('建筑与寺院',('寺院','寺庙','白塔','经堂','碉楼','门楼','白墙','屋顶')),
)

# 主题权重：当文字里出现了「具体体验」主题（住宿/餐食/唐卡/人像/手作/建筑），而某张图
# 也命中了这个具体主题时，给它额外加分 —— 用来在「文字既讲住宿又顺带提了湖/雪山」这类
# 同场出现多个主题的段落里，让真酒店图压过只讲风光的雪山图（纠正「讲酒店配雪山」）。
# 只加分、不扣分：绝不让风光图被冤枉撤掉（判据不足时宁可不动）。
_CONCRETE_TOPICS={'住宿','餐食','唐卡与绘画','人像与藏装','手作与体验','建筑与寺院'}

# ── 文档类照片黑名单（2026-10-10 用户反馈：唐卡段落配了一张教练证书）─────────────
# 证书 / 证件 / 文件截图不是活动照片。模型会做字面联想——「在非遗老师指导下」配一张
# 「教练证书」，顾客看到的是陌生人的证件照，直接判不专业。这类图**无论打标是否成功、
# 无论模型怎么选**，一律不得进入详情页配图（三道闸：打标计入 unusable、匹配分 -1000、
# 出口对题硬删），与打标成败解耦——打标失败的回退路径也曾把证书图放进来（实测踩过）。
_DOC_PHOTO_RE=re.compile(
    r'证书|证件|身份证|护照|扫描件|扫描|打印件|合同|票据|收据|名片|营业执照|许可证'
    r'|资格证|资质证|教练证|导游证|文件|表格|表单|二维码|条形码|截图|日程表|行程表')

def _block_text(b:dict[str,Any])->str:
    return ' '.join(str(b.get(k) or '') for k in ('headline','body','text','title','caption','pull'))

def _topic_set(s:str)->set[str]:
    t=str(s or '')
    return {name for name,words in _TOPIC_WORDS if any(w in t for w in words)}

def _bigrams(s:str)->set[str]:
    t=re.sub(r'[^\u4e00-\u9fa5]','',str(s or ''))
    return {t[i:i+2] for i in range(len(t)-1)}

def _img_text(m:dict[str,Any])->str:
    tags=m.get('tags') if isinstance(m.get('tags'),list) else []
    return (str(m.get('desc') or '')+' '+' '.join(str(x) for x in tags)).strip()

def _match_score(text_topics:set[str],text_bg:set[str],img:dict[str,Any])->int|None:
    """图与这段文字的匹配分：主题同类最重，字面重合次之，主题互斥直接扣分。

    **没有打标信息的图返回 None**（未知），调用方必须把它当「中性」处理：
    既不能因为「没描述」就把这张图撤掉，也不能把它当候选换进来 ——
    否则打标失败时会把整页图片全删光（2026-10-09 实测踩过）。
    """
    info=_img_text(img)
    if not info:
        return None
    # ★ 文档类照片（证书/证件/文件截图）永远不是候选：-1000 让它在任何段落都是
    #   「明显错配」，既不会被换进来，也会被出口对题撤掉（2026-10-10 教练证书实锤）。
    if _DOC_PHOTO_RE.search(info):
        return -1000
    it=_topic_set(info)
    bg=_bigrams(info)
    score=2*len(bg&text_bg)
    if text_topics and (it&text_topics):score+=4
    if text_topics and it and not (it&text_topics):score-=5
    # ★ 具体体验主题命中再加成（2026-10-10 修复「讲酒店配雪山」）：
    # 只加分不扣分 —— 让真酒店图在同场多主题段落里压过只讲风光的图，但绝不冤枉风光图。
    # +5 是经验值：足够在「文字既讲住宿又顺带提了湖/雪山/森林」的长段落里压过靠字面重合刷分的风光图，
    # 又不至于大过头的误换（判据仍要求 best≥cur+3 才动手）。
    matched=it&text_topics
    if matched&_CONCRETE_TOPICS:score+=5
    return score

def _align_block_media(blocks:list[dict[str,Any]],catalog:list[dict[str,Any]],
                       allowed:set[str]|None=None)->list[dict[str,Any]]:
    """出口对题检查：全篇去重 + 明显错配就换图/撤图。

    保守策略（重要）：只有当**文字里有明确主题词**、且当前图与之主题互斥（或存在明显更对题的
    未用图）时才动手。没有主题词的区块（纯氛围 gallery）只做去重，不擅自重排 —— 判据不足时
    宁可不动，否则会把本来合理的排版搅乱（与 longpic 的 `_html_ok` 同一条教训：过严会误杀好成品）。
    """
    if not blocks:return blocks
    by_ref={str(m.get('ref')):m for m in (catalog or []) if isinstance(m,dict) and m.get('ref')}
    allow=set(allowed or [])
    def ok_ref(r):return (not allow) or (r in allow)
    # ★ 文档类照片硬删（2026-10-10）：打标说不可用（usable=False）或画面描述像证书/证件/
    #   文件截图的图，无论 allowed 怎么给、模型怎么选，一律不进成品。这是与打标成败
    #   解耦的最后一道闸——打标失败的回退路径曾把教练证书放进唐卡段落（用户截图实锤）。
    def bad_ref(r):
        m=by_ref.get(r)
        if not m:return False
        if m.get('usable') is False:return True
        return bool(_DOC_PHOTO_RE.search(str(m.get('desc') or '')))
    # ① 去重：同一 ref 全篇只保留第一次出现的位置
    used:set[str]=set()
    for b in blocks:
        if not isinstance(b,dict):continue
        refs=b.get('mediaRefs')
        if not isinstance(refs,list):continue
        keep=[]
        for r in refs:
            r=str(r)
            if ok_ref(r) and r not in used and not bad_ref(r):
                used.add(r);keep.append(r)
        b['mediaRefs']=keep
    # ② 对题：文字区块按自身主题，纯图片区块按「紧邻的上一段文字」的主题（读者就是这么理解的）
    pool=[str(m.get('ref')) for m in by_ref.values()]
    for i,b in enumerate(blocks):
        if not isinstance(b,dict):continue
        refs=list(b.get('mediaRefs') or [])
        if not refs:continue
        txt=_block_text(b)
        tt=_topic_set(txt)
        if not tt:
            for j in range(i-1,max(-1,i-3),-1):        # 往前最多看两块
                prev=_topic_set(_block_text(blocks[j]))
                if prev:tt=prev;txt=_block_text(blocks[j]);break
        if not tt:continue                              # 主题判据不足：不动
        tb=_bigrams(txt)
        mine=set(refs)
        out=[]
        for r in refs:
            cur=_match_score(tt,tb,by_ref.get(r,{}))
            best,bs=None,None
            for ref in pool:
                if ref in used:continue
                s=_match_score(tt,tb,by_ref.get(ref,{}))
                if s is None:continue                   # 没有打标信息的图不当候选（未知不等于合适）
                if bs is None or s>bs:bs,best=s,ref
            if cur is None:
                out.append(r);continue                  # 这张没有打标信息：保持不动
            if best is not None and bs>=cur+3:
                used.add(best);out.append(best);continue  # 存在明显更对题的图 → 换掉
            if cur<0 and (bs is None or bs<=0):
                continue                                # 明显错配且没有更对题的图 → 撤掉这张
            out.append(r)
        # ★ 本块最终没留下的图要从「已占用」里释放：否则一个被整块撤掉的 gallery
        #   会把 6 张照片永久锁死，后面的段落明明该用它们却拿不到（顺序效应，实测踩过）。
        for r in mine:
            if r not in out:used.discard(r)
        b['mediaRefs']=out
    return blocks

async def _prepare_photo_picker(club_id:int,source:dict[str,Any],catalog:list[dict[str,Any]],
                               master:dict[str,Any]|None=None)->dict[str,Any]:
    """给候选照片做视觉打标，产出「按画面内容选图」的清单。

    返回 {'lines':[...], 'allowed':set(ref), 'ok':bool}。
    ok=False 表示打标不可用（无照片 / 模型不支持视觉 / 大面积失败），调用方退回旧行为
    （直接把图片喂给模型），**绝不能因为打标失败让整页缺图**。
    """
    photo_refs=[str(m.get('ref')) for m in catalog
                if (m.get('kind') or 'photo')=='photo' and str(m.get('url') or '').strip()]
    if not photo_refs:
        return {'lines':[],'allowed':set(),'ok':False}
    ctx=dict(master or {})
    if not ctx.get('title') or not ctx.get('location'):
        try:st=_extract_structured(source)
        except Exception:st={}
        for k in ('title','location','date'):
            if not ctx.get(k):ctx[k]=st.get(k) or ''
        if not ctx.get('publicFacts'):ctx['publicFacts']=st.get('publicFacts') or {}
    holder={'media':catalog,'title':ctx.get('title'),'location':ctx.get('location'),
            'date':ctx.get('date'),'publicFacts':ctx.get('publicFacts') or {}}
    try:
        caps=await caption_media(club_id,holder,STATIC_DIR)
    except Exception:
        caps={}
    if not caps:
        return {'lines':[],'allowed':set(photo_refs),'ok':False}
    # 写回 source 的媒体条目：同一活动「换一版」时直接复用，不再重复调用视觉模型
    for bucket in ('media_manifest','images'):
        for x in source.get(bucket) or []:
            if not isinstance(x,dict):continue
            cap=caps.get(str(x.get('ref') or ''))
            if cap:
                x['desc']=cap.get('desc');x['usable']=cap.get('usable')
                if cap.get('tags'):x['tags']=cap['tags']
    for m in catalog:
        cap=caps.get(str(m.get('ref') or ''))
        if cap:
            m['desc']=cap.get('desc');m['usable']=cap.get('usable')
            if cap.get('tags'):m['tags']=cap['tags']
    # ★ 「换一版」路径的 master.media 也要补上（2026-10-09 实测踩坑）：只写 source 的话，
    #   app.py 判断「有没有新打上标」看的是 master.media，永远为假 → 每次换版都重新调一遍
    #   视觉模型、重新花一次钱（act39 实测第二轮白跑了 1 次调用）。
    for m in (master or {}).get('media') or []:
        if not isinstance(m,dict):continue
        cap=caps.get(str(m.get('ref') or ''))
        if cap:
            m['desc']=cap.get('desc');m['usable']=cap.get('usable')
            if cap.get('tags'):m['tags']=cap['tags']
    lines=caption_lines({'media':catalog},caps)
    if len(lines)<max(3,len(photo_refs)//2):     # 打标基本失败：退回旧行为，别让整页缺图
        return {'lines':[],'allowed':set(photo_refs),'ok':False}
    unusable={r for r,c in caps.items()
              if not c.get('usable') or _DOC_PHOTO_RE.search(str(c.get('desc') or ''))}
    return {'lines':lines,'allowed':set(photo_refs)-unusable,'ok':True}

def _photo_block(pick:dict[str,Any],media_json:str)->str:
    """给提示词的图片段：打标成功给「按画面内容」的清单，失败退回原始媒体 JSON。"""
    if not pick.get('ok'):
        return ('媒体清单（只能引用这些 ref；kind=\'photo\' 才是真实照片）：\n'+media_json)
    return ('可用照片清单（**选图只能从这份清单里挑**；格式：ref｜横竖｜主题标签｜画面内容）：\n'
            +'\n'.join(pick['lines'])
            +'\n\n清单里没有出现的 ref 是品牌 logo / 空白底图 / 地图截图 / 不属于本次活动的照片，一律不得引用。')

async def generate_activity(club_id:int,source:dict[str,Any],*,direction:str='',
                            previous_detail:dict[str,Any]|None=None,
                            version_no:int=1)->tuple[dict[str,Any],GatewayResponse]:
    catalog=media_catalog(source,None)
    # 视觉打标：让模型「看得见图里是什么」。不做这一步，选图就等于随机抽（2026-10-09 图文错配的根因）。
    pick=await _prepare_photo_picker(club_id,source,catalog)
    photo_block=_photo_block(pick,json.dumps(source.get('media_manifest',[]),ensure_ascii=False))
    prompt=f"""你收到的是老板/领队提供的完整活动原始资料。直接完成两件事：
A. 提取 Activity Master（事实与业务真相）；
B. 像内容主编一样生成 C 端招募详情的动态 block 方案。
{_revision_block(direction,previous_detail,version_no)}
原始资料（保留原始上下文）：
{source.get('text','')}

{photo_block}

★★ 多份资料怎么分工（2026-10-09 用户要求：方案 + 额外照片可以一起丢给你，由你自行识别组合）：
- 「原始资料」按 [文件: 名称] 分段。**有正文的那份就是活动方案**，是事实的唯一来源：
  日期 / 地点 / 行程 / 费用 / 人数 / 价格只从方案正文取。
- 媒体清单里每条都带 source（这张图出自哪个文件）：source=方案文件名的是方案里嵌的图，
  source=照片文件名的是老板另外补的实拍照片。两类都可用，也都优先选 kind='photo' 的。
- 额外上传的照片只提供画面，**不提供事实**：不许从照片里推导价格、人数、行程或地名。
  它们最适合用在封面 / 首屏 hero / bigimage / gallery / imagetext 上，把成品做得更像画报。
- 若一份文件全是图片、读不到正文，就把它当纯图片素材用，**不要为它编造文字内容**；
  事实仍以有正文的那份方案为准。一个字都没有的场合才允许按用户输入的那句话生成初稿。
- 不需要用户说明「哪份是方案」：你自己按「有正文 / 只有图」判断即可。

图片选用铁律（2026-10-03 用户反馈）：
- 只能引用 kind='photo' 的 ref。kind='logo' 的是品牌标志 / 字标横幅 / 空白幻灯片底图 /
  地图与天气截图 / 扁平赞助商海报——它们是资料的排版残留，不是活动照片，出现在成品里就是事故。
- 品牌 logo 一律不得出现在详情页任何位置（hero / gallery / media 都不行）。活动确实需要品牌
  标识时，那是「单独上传 logo / 封面」的事，不由你从资料里挑图。
- 没有可用照片时不要硬排图，用文字把吸引力撑起来。
{_PHOTO_MATCH_RULES}

★★ 事实保真铁律（2026-10-04 用户反馈：包装要好看，但**不许编**）：
- 专有名词一律逐字照抄原文，禁止音译、意译、美化或凭空生成。**资料里没出现过的地名/
  寺庙名/山峰名/路线名，一个字都不许出现在成品里** —— 编出来的地名顾客会按它导航，是事故。
  原文怎么写就怎么写，哪怕你自己觉得它写错了：行程表格写「草寺庙」就照抄「草寺庙」，
  不许擅自改成别处出现过的「石笋寺」。
- 品牌名保留原始写法。资料写「icebreaker」就写「icebreaker」，**不要音译成「冰破」、
  意译成「破冰者」**，也不要随意大小写。品牌名写错是商务事故。
- 数字（距离/累计爬升/时长/价格/积分/人数/海拔）必须与原文**完全一致**，不得四舍五入、
  不得换算、不得凭印象补。原文没有的数字就不要写在 facts/timeline 里。
- 行程地点若原文只给了「A-B」两个端点（如「石笋寺-兴福寺」），就照抄这两个端点，
  不要自行推导中间站点。
- 若原文**自己前后不一致**（同一份资料里 Slide 8 写「石笋寺」、行程表格里又写「草寺庙」），
  你照抄不改，同时把疑虑写进 activity_master.uncertainties，例如：
  「原文对同一地点出现两种写法：石笋寺 / 草寺庙，请确认正式名称」。没有疑虑就留空数组。
  你不负责判定哪个写法对，只负责把矛盾原样交还给运营。
- 标题：从方案里提炼一个**有吸引力的活动名**（≤20 字，如「兴福寺徒步 × 森林颂钵」），
  **不要照抄用户提的需求描述**（那种长句子是给 AI 看的，不是给顾客看的标题）。

装备清单不要重复（2026-10-03 用户反馈）：
- 严禁输出 title 为「装备建议 / 出行清单 / 装备清单 / 携带清单 / 着装建议」之类的 info block。
  平台会在详情页下方单独渲染「出行清单」（带商城匹配、平替与会员价）。你再出一块，
  同一份清单就会在页面上出现两遍。checklist 只写进 activity_master，不要做成 block。

字段硬性契约（违反会导致前端渲染成空）：
- itinerary 必须是数组，每项形如 {{"time":"Day 1 08:00-12:00","text":"成都集合出发 → 康定城区"}}。
  逐日行程要完整展开成多条（半天到一天一条），绝不能省略成 {{"day":"Day 1","schedule":[...]}} 这类嵌套形状。
  行程是户外活动的核心事实：资料里有几天就写几天，一条都不能丢。
- checklist 必须是「具体装备品类」字符串数组（如 冲锋衣、抓绒外套、登山杖、头灯、防晒帽、保温杯、雨衣、充电宝、急救包），
  根据本次活动的地点海拔、季节、天数与难度推导；不要输出「高原适应准备」「防晒防寒装备」这类抽象分类——
  它们无法对应到商城在售装备。证件、个人药品这类非装备个人物品放在最后，最多两三项。
- 若资料含逐日行程，detail.blocks 必须包含一个 timeline block（items=[{{"time":"...","text":"..."}}]，与 itinerary 同形状）。
- 方案里的 [Slide N] / [Page N] 只是解析用的页码骨架，绝不能出现在任何 C 端字段里。

★★ 成本数据铁律（2026-10-06 用户反馈，最高优先级）：
- 方案里的「活动费用明细 / COST」页是俱乐部的**成本底价**（单价、小计、合计（未含税）、人均费用、
  策划执行 10%、税费、毛利…）。这类数字**一个都不许出现在成品里** —— 不在 fees、不在正文、
  不在 narrative/statement/info/facts 的任何一句话里，也不要改写成「人均约 3800 元」「成本约 7.6 万」
  这种模糊说法。顾客看到成本价等于把利润结构摊在桌上。
- fees 只允许写「费用包含」「费用不含」两项（都是顾客该知道的服务范围与自理项），
  **只写项名、不写金额**；「费用不含」是顾客自理项（如 单房差、往返大交通、个人消费…），同样定性不定量。
  禁止输出 人均费用 / 合计 / 合计（未含税）/ 单价 / 小计 / 按人数报价 / 策划执行 / 备注 这类键，
  也禁止在「费用不含」里写任何金额或成本数字（成本铁律不变：成本底价一律不进 C 端）。
- price 只在方案里出现**明确的对外报价**（售价 / 报名费 / 会员价 / 每人 288 元）时才填；
  资料里只有成本明细页时，price 留 0，由俱乐部自己定价 —— 绝不要把成本均价当对外售价填进去。
- 成本、供应商报价、门店 SOP、话术禁区、内部沟通等内容属于内部资料，一律不得进入 C 端成品。

{_WRITING_CONTRACT}

返回 JSON：
{{
  "activity_master":{{
    "title":"","date":"","location":"","price":0,"capacity":0,
    "publicFacts":{{}},"itinerary":[],"fees":{{}},"checklist":[],"services":[],
    "internalData":{{}},"uncertainties":[],"blocking_conflicts":[],"media":[]
  }},
  "detail":{{
    "activityUnderstanding":"",
    "coreSellingIdea":"",
    "editorialIntent":{{"opening":"","visualWeight":"","reason":""}},
    "blocks":[
      {{"type":"{BLOCK_TYPES}","eyebrow":"narrative 用场景眉题","headline":"可选","body":"可选，可用 \\n 分段","pull":"可选金句(≤14字)","text":"可选","mediaRefs":["img_01"]}}
    ]
  }}
}}

block 语义（全部可自由省略 / 重复 / 排序，不要机械凑章节）：
hero=首屏（C 端会被封面取代，仅后台预览用）；lead=短引言；narrative=图文叙事（layout 可为 text-top/image-left/image-right/full）；statement=强观点短句；media=单图/双图/拼图/整版大图（layout: single/pair/mosaic/grid/full）；gallery=图片组；facts=关键事实条；timeline=时间线；info=必要决策信息；quote=引用；divider=节奏分隔；bigimage=整版铺满大图 + 可选叠字标题/图注（最适合做画报式跨页图）；imagetext=杂志左右图文（layout: left/right）；cards=图标+标题+要点 的卡片网格（把「为什么值得/包含什么」做成视觉块）；numbercards=大数字统计卡（距离/海拔/天数/人数用大字号突出）；highlight=高亮提示框（强调一句关键承诺或须知）；columns=双栏长文（无图时的多段排版）。
★★ 排版自由铁律（2026-10-08 用户要求：第一段要像公众号长图文一样美观、每版都不同）：
   C 端招募详情是一场活动的主视觉宣传，按「公众号长图文」的标准做 —— 多变的版式、大图铺陈、
   图文穿插、卡片化要点。主动组合 bigimage / imagetext / numbercards / cards 这类视觉组件，
   避免从头到尾都是同一套「标题 + 正文 + 图」。照片多时优先做视觉编排，照片少时用文字与卡片把吸引力撑起来。
{_LAYOUT_CONTRACT}
如果没有真正事实冲突，blocking_conflicts 必须为空，直接完成成品。"""
    # 打标成功时不再把几十张原图塞进请求：清单里已经有「画面里是什么」，
    # 模型按文字选图更准、更快、更省 —— 38 张原图 base64 进去只会稀释注意力。
    gw=await generate_json(club_id=club_id,task_type='detail',system_prompt=SYSTEM,user_prompt=prompt,
                           images=None if pick['ok'] else source.get('images'))
    photos=pick['allowed'] or set(_photo_refs(source))
    if gw:
        data=gw.data if isinstance(gw.data,dict) else {}
        # 模型只负责「引用」ref，媒体条目本身必须以真实上传/磁盘资料为准。
        # 直接采信模型写的 media，会让前端 ref→url 映射表为空：整页图片退化成灰色占位块、头图退化成纯色。
        master=data.get('activity_master') if isinstance(data.get('activity_master'),dict) else {}
        catalog=media_catalog(source,master)
        if catalog or not isinstance(master.get('media'),list):master['media']=catalog
        data['activity_master']=master
        if isinstance(data.get('detail'),dict):
            # 提示词已要求只用照片，这里再兜一层：模型若引用了品牌 logo / 空白图 / 地图截图，直接剔除
            data['detail']['blocks']=_sanitize_blocks(data['detail'].get('blocks') or [],allowed_refs=photos)
            # 出口对题检查：去重 + 明显错配就换图/撤图（提示词之外的最后一道保证）
            data['detail']['blocks']=_align_block_media(data['detail']['blocks'],catalog,photos)
        # 事实回检：品牌名音译还原 + 编造地名告警（模型包装文案时会顺手改专名）
        apply_fact_guard(data,str(source.get('text') or ''))
        # 照抄闸门（2026-10-09 用户反馈「这些文案不要照搬方案原文」）：详情此前从没有这一道，
        # 要点表里「餐饮：户外牛肉汤锅（含精选黄牛腱肉、虾滑、肥牛等）」这类参数串就原样上了页。
        # 命中「与原文连续重合 > 12 字」的段落交回模型定向重写一次，与渠道生成同一套手法。
        await _rewrite_echo_detail(club_id, data.get('detail'), str(source.get('text') or ''))
        return data,gw
    data=_mock_activity(source)
    data['detail']['blocks']=_sanitize_blocks(_rotate_layout(data['detail'].get('blocks') or [],max(0,version_no-1)),allowed_refs=photos)
    return data,record_mock_usage(club_id,'detail',prompt,data)


async def regenerate_detail(club_id:int,source:dict[str,Any],master:dict[str,Any],*,direction:str='',
                            previous_detail:dict[str,Any]|None=None,version_no:int=2,
                            refresh_facts:bool=False)->tuple[dict[str,Any],GatewayResponse]:
    """重新生成活动详情（老板对第一版不满意时的「换一版」）。

    默认 refresh_facts=False：只重做叙事与排版，Activity Master 作为只读事实传入。这样换一版
    不会让标题/日期/地点/价格/人数悄悄漂移，也不会碰到团期、价格政策与报名数据。
    refresh_facts=True 时才允许模型用同一份原始资料重新提取事实（例如第一版把日期或价格读错了）。
    """
    if refresh_facts:
        data,usage=await generate_activity(club_id,source,direction=direction,
                                           previous_detail=previous_detail,version_no=version_no)
        return {'activity_master':data.get('activity_master') or master,'detail':data.get('detail') or {}},usage
    catalog=media_catalog(source,master)
    # 打标结果已写进 master.media（首次生成时落库）→ 换一版直接复用，不再重复调用视觉模型；
    # 老活动第一次换版时 master 里没有 desc，这里会补一次打标。
    pick=await _prepare_photo_picker(club_id,source,catalog,master)
    photo_block=_photo_block(pick,json.dumps(source.get('media_manifest',[]),ensure_ascii=False))
    # ★ master.media 里那一串 img_01…img_38 **不能**原样进提示词（2026-10-09 实测踩坑）：
    #   换一版时模型看见这串 ref，就会绕开「可用照片清单」自己挑，实测把一张没有画面描述、
    #   根本不在清单里的图（img_09）塞进了唐卡段落。事实只读 ≠ 媒体清单可读，媒体只能走 photo_block。
    master_facts={k:v for k,v in (master or {}).items() if k!='media'}
    master_facts['mediaCount']=len((master or {}).get('media') or [])
    prompt=f"""Activity Master（事实，只读；不要重新提取，也不要修改其中任何一项）：
{json.dumps(master_facts,ensure_ascii=False)}
{_revision_block(direction,previous_detail,version_no)}
你的任务：只重新创作 C 端招募详情的 block 方案（页面叙事、图片节奏、区块顺序）。
不得新增事实：日期、地点、价格、人数、行程、费用、出行清单、领队、资质一律以 Activity Master 与原始资料为准。

原始资料（只用于取用真实细节，不是重新提取事实）：
{source.get('text','')}

{photo_block}

图片与清单铁律（与首次生成同一标准）：
- 只能引用 kind='photo' 的 ref。kind='logo' 的是品牌标志 / 字标横幅 / 空白底图 / 截图 /
  扁平赞助商海报，一律不得出现在详情页任何位置；品牌标识只能靠「单独上传 logo / 封面」解决。
- 严禁输出 title 为「装备建议 / 出行清单 / 装备清单 / 携带清单 / 着装建议」之类的 info block：
  平台会在详情页下方单独渲染「出行清单」，重复出块会让同一份清单出现两遍。
{_PHOTO_MATCH_RULES}

{_WRITING_CONTRACT}
- 方案里的 [Slide N] / [Page N] 只是解析用的页码骨架，绝不能出现在任何 C 端字段里。

返回 JSON：
{{
  "detail":{{
    "activityUnderstanding":"",
    "coreSellingIdea":"",
    "editorialIntent":{{"opening":"","visualWeight":"","reason":""}},
    "blocks":[
      {{"type":"{BLOCK_TYPES}","eyebrow":"narrative 用场景眉题","headline":"可选","body":"可选，可用 \\n 分段","pull":"可选金句(≤14字)","text":"可选","mediaRefs":["img_01"]}}
    ]
  }}
}}

block 语义：
hero=首屏；lead=短引言；narrative=图文叙事；statement=强观点短句；media=单图/双图/拼图；gallery=图片组；facts=关键事实条；timeline=时间线；info=必要决策信息；quote=引用；divider=节奏。
任何 block 都可省略、重复、自由排序。不要为了「结构完整」机械凑章节。
{_LAYOUT_CONTRACT}
（密度下限优先于「照片少时不要硬凑」：只要资料里有可用照片就按下限铺满，只有真的没有可用照片时才用文字撑。）"""
    gw=await generate_json(club_id=club_id,task_type='detail',system_prompt=SYSTEM,user_prompt=prompt,
                           images=None if pick['ok'] else source.get('images'))
    photos=pick['allowed'] or set(_photo_refs(source))
    if gw:
        payload=gw.data or {}
        detail=payload.get('detail') if isinstance(payload.get('detail'),dict) else payload
        if isinstance(detail,dict):
            detail['blocks']=_sanitize_blocks(detail.get('blocks') or [],allowed_refs=photos)
            detail['blocks']=_align_block_media(detail['blocks'],catalog,photos)
        # 事实回检（换一版同样会改写专名，不能只在首次生成时守）
        apply_fact_guard({'activity_master':master if isinstance(master,dict) else {},'detail':detail},
                         str(source.get('text') or ''))
        # 「换一版」同样要过照抄闸门：只守首次生成的话，第二版又会把方案原句搬回来。
        await _rewrite_echo_detail(club_id, detail, str(source.get('text') or ''))
        return {'activity_master':master,'detail':detail},gw
    data=_mock_activity(source)
    detail=data['detail']
    detail['blocks']=_sanitize_blocks(_rotate_layout(detail.get('blocks') or [],max(0,version_no-1)),allowed_refs=photos)
    if direction.strip(): detail['directionNote']=direction.strip()
    return {'activity_master':master,'detail':detail},record_mock_usage(club_id,'detail',prompt,data)


def normalize_channel_blocks(data:dict[str,Any])->dict[str,Any]:
    """把模型返回的各种「形状」归一到 blocks 数组。

    为什么需要：前端只认 data.blocks。实测 qwen-max 会自作主张返回
    {"title","lead":{...},"narrative":[{headline,body,mediaRefs}...],"facts":{...},"closing":{quote,cta}}，
    形状一变，blocks 为空 → 内容中心显示「已生成」但预览整篇空白，用户只会说「又坏了」。
    与其指望提示词约束住模型，不如在出口做一次结构归一化，把内容救回来。
    """
    if not isinstance(data,dict): return data
    blk=data.get('blocks')
    if isinstance(blk,list) and blk: return data
    out=[]
    def add(btype,src):
        if src is None: return
        if isinstance(src,list):
            for it in src: add(btype,it)
            return
        b={'type':btype}
        if isinstance(src,str):
            t=src.strip()
            if not t: return
            b['text']=t
        elif isinstance(src,dict):
            for k in ('eyebrow','headline','title','subtitle','body','text','pull','summary'):
                v=src.get(k)
                if isinstance(v,str) and v.strip(): b[k]=v.strip()
            for k in ('mediaRefs','items'):
                v=src.get(k)
                if isinstance(v,list) and v: b[k]=v
            if not any(k in b for k in ('headline','title','body','text','items')): return
        else:
            return
        out.append(b)
    add('lead',data.get('lead') or data.get('intro') or data.get('opening'))
    add('narrative',data.get('narrative') or data.get('narratives')
        or data.get('sections') or data.get('story') or data.get('paragraphs'))
    add('statement',data.get('statement') or data.get('statements'))
    add('gallery',data.get('gallery') or data.get('images'))
    add('facts',data.get('facts') or data.get('info'))
    add('quote',data.get('quote'))
    for key in ('timeline',):
        add('timeline',data.get(key))
    closing=data.get('closing')
    if isinstance(closing,dict):
        add('quote',closing.get('quote'))
        add('cta',closing.get('cta') or closing.get('action'))
    else:
        add('cta',closing or data.get('cta'))
    if out:
        data['blocks']=out
        # 顶层摘要：前端标题下方的导语，缺了就从前几段里挑一句
        if not data.get('summary'):
            for b in out:
                if b['type'] in ('lead','narrative') and b.get('text'):
                    data['summary']=b['text'][:60]; break
    return data


def normalize_poster(data:dict[str,Any],fallback_title:str='')->dict[str,Any]:
    """把模型返回的任意形状归一成海报字段（headline/subheadline/facts/sellingPoints/cta/mediaRefs）。

    为什么需要（2026-10-09 用户截图实证）：海报 brief 要的是 headline/subheadline/facts/sellingPoints，
    但提示词里那段「输出结构（强制）」当时是**所有渠道共用**的 {"title","summary","blocks"}（公众号那一套），
    模型老老实实照着后者输出 → 前端拿不到 headline / sellingPoints / facts，
    海报上就只剩「活动标题 + 一行灰字（地点·价格·名额）+ 一个大白二维码方块」，
    老板的原话是「一点设计感都没有」。这里在出口做一次归一：新形状原样放行，旧形状也救得回来。
    """
    if not isinstance(data,dict): data={}
    out=dict(data)
    def s(v):
        if isinstance(v,str): return v.strip()
        if isinstance(v,(int,float)): return str(v)
        if isinstance(v,dict):
            for k in ('text','value','label','title','content','desc','name','body'):
                if isinstance(v.get(k),str) and v[k].strip(): return v[k].strip()
        return ''
    blocks=[b for b in (data.get('blocks') or []) if isinstance(b,dict)]
    def first_text(types):
        for b in blocks:
            if types and (b.get('type') or '') not in types: continue
            for k in ('headline','title','text','body','summary','content'):
                if isinstance(b.get(k),str) and b[k].strip(): return b[k].strip()
        return ''
    headline=s(data.get('headline') or data.get('title') or data.get('headlineMain')) or fallback_title
    sub=s(data.get('subheadline') or data.get('subtitle') or data.get('summary')) or first_text(('lead','statement'))
    facts=[]
    for x in (data.get('facts') or data.get('info') or data.get('factList') or []):
        t=s(x)
        if t and t not in facts: facts.append(t)
    if not facts:
        for b in blocks:
            if (b.get('type') or '')!='facts': continue
            for it in (b.get('items') or []):
                t=s(it)
                if t and t not in facts: facts.append(t)
    pts=[]
    for x in (data.get('sellingPoints') or data.get('selling_points') or data.get('highlights') or
              data.get('points') or []):
        t=s(x)
        if t and t not in pts: pts.append(t)
    if not pts:
        # 旧形状（blocks）里没有卖点字段，就拿各节小标题当卖点 —— 它们是模型自己写的判断句
        for b in blocks:
            if (b.get('type') or '') not in ('narrative','statement','info','lead','media','gallery'): continue
            t=s(b.get('headline') or b.get('title') or b.get('eyebrow'))
            if t and len(t)<=20 and t not in pts: pts.append(t)
    cta=s(data.get('cta') or data.get('action')) or '扫码报名'
    refs=[x for x in (data.get('preferredMediaRefs') or data.get('mediaRefs') or [])
          if isinstance(x,str) and x.strip()]
    if not refs:
        for b in blocks:
            for r in (b.get('mediaRefs') or []):
                if isinstance(r,str) and r.strip() and r not in refs: refs.append(r)
    out['headline']=headline
    out['subheadline']=sub
    out['facts']=facts[:4]
    out['sellingPoints']=pts[:5]
    out['cta']=cta
    out['preferredMediaRefs']=refs[:3]
    return out


async def generate_channel(club_id:int,activity_master:dict[str,Any],detail:dict[str,Any],channel:str,
                           cover_url:str|None=None,source_text:str='')->tuple[dict[str,Any],GatewayResponse]:
    labels={'wechat':'微信公众号','xhs':'小红书','poster':'活动招募海报','recap':'活动回顾'}
    cover_note=f"活动官方封面（已上传的主视觉，优先用作首图 / 海报主图）：{cover_url}\n" if cover_url else ''
    # 2026-10-07 二次返工：上一版把方案原文**整段**喂给写作模型，本意是「让推文拿到细节」，
    # 实际效果相反 —— 原句成了抄袭原料（8 字命中率 14.5%、最长连续照抄 31 字），
    # 推文读起来就是 PPT 景点说明的散文版。现在改为：先压成「事实要点表」，
    # 写作阶段**只给要点、不给整句**，把组句的工作还给模型。
    digest = await _fact_digest(club_id, source_text) if source_text else ''
    if digest:
        fact_note = (f"\n事实素材表（同事已把方案压成碎片，**没有一句可以直接用**；"
                     f"事实必须与它完全一致，句子必须你自己写）：\n{digest}\n")
    else:
        # 要点表生成失败时退回旧路径，宁可承担照抄风险也要出稿
        fact_note = (f"\n原始方案全文（第一手资料，细节最全）：\n{source_text}\n" if source_text else '')
    briefs={
      'wechat':"""wechat（公众号图文）：这是**独立成篇的长文**，不是活动详情的删减版，也不是素材的复述。
★ 你写的是「这两天参与者身上会发生什么」，**不是「介绍景点」**。
★ 素材表给的是**碎片词条**，把它们连成句子、补上视角与判断，才是你的工作。

- 篇幅：正文 1000~1400 字，7~9 节。
- 结构：title（≤26 字，有画面或有判断，不要「XX之旅」「XX招募」这种）→
  lead（2~3 句，用一个**具体时刻**开场：几点、在哪里、看到什么；不抒情、不喊口号）→
  4~6 节 narrative（headline 是一句判断或建议，尽量含数字或专名；body 两段：
  第一段写现场发生的事，第二段写**为什么这个安排值得**）→
  一节 facts（时间 / 交通 / 住宿 / 费用包含）→ 一节 cta（报名收尾）。
- **每一节都必须回答「所以呢」**：少走回头路 / 高反更轻 / 这件东西能带回家 / 不用自己操心。
  只描述、不判断的章节，直接删掉不要写。
- 图片：mediaRefs 只引真实照片（kind='photo' 的 ref），贴在对应叙述之后，一节 0~2 张，
  不要全堆到文末；一篇推文里一张图都不给同样是失败。
- 禁止：把素材表的词条原样堆成段落；「本次活动的亮点在于」式公文句；空话套话；
  编造资料里没有的日期 / 价格 / 名额 / 资质 / 用户评价。
- ★ 上面**没有给你任何句式范例**，这是故意的：实测任何例句都会被原样搬进成品。句子自己写。""",
      'xhs':"""xhs（小红书图文）：titleOptions[]（3 个，≤20 字，带钩子）、hook（首句抓人）、
body（真实口语，可换行，带 emoji 但要克制）、tags[]（6~10 个）、imageSequence[]（真实照片 ref 或 url）。
不要写成公文或硬广。""",
      'poster':"""poster（活动招募海报）：headline（≤14 字，一句就能立住）、subheadline、
facts[]（时间 / 地点 / 价格 / 人数，只写真实值）、sellingPoints[]（3~5 条，每条带具体细节）、
cta、preferredMediaRefs[]（真实照片 ref）。""",
      'recap':"""recap（活动回顾）：只有提供真实 actualActivityData / 现场素材时才能叙述实际发生事件；
资料不足时明确返回 needsActualData=true，绝不编造。""",
    }
    # 只把「编辑判断」交给渠道生成，**不传 detail 的 blocks**。
    # 实测：把整个 detail 成品塞进 prompt，模型会直接照抄它的标题与段落（qwen-max 尤其明显），
    # 结果公众号图文变成详情页的换皮版。渠道内容必须自己从原始资料重新写。
    detail_view={k:v for k,v in (detail or {}).items()
                 if k in ('activityUnderstanding','coreSellingIdea','editorialIntent') and v} \
                if isinstance(detail,dict) else {}
    # 「输出结构（强制）」必须按渠道给 —— ★ 2026-10-09：此前所有渠道共用公众号那一套 {title,summary,blocks}，
    # 模型照抄，海报渠道拿回来的是空字段（headline/facts/sellingPoints 全无），
    # 成品退化成「活动标题 + 一行灰字 + 一个大白二维码」（老板截图投诉「一点设计感都没有」）。
    # 海报要的是它自己那套字段，别再让模型猜。
    structures={
        'wechat':'{"title":"","summary":"","blocks":[{"type":"lead|narrative|statement|media|gallery|facts|timeline|quote|cta","headline":"","body":"","mediaRefs":[],"items":[{"label":"","value":""}]}]}',
        'recap':'{"title":"","summary":"","blocks":[{"type":"lead|narrative|statement|media|gallery|facts|timeline|quote|cta","headline":"","body":"","mediaRefs":[],"items":[{"label":"","value":""}]}]}',
        'xhs':'{"titleOptions":["","",""],"hook":"","body":"","tags":[],"imageSequence":[]}',
        'poster':'{"headline":"","subheadline":"","facts":[],"sellingPoints":[],"cta":"","preferredMediaRefs":[]}',
    }
    structure_notes={
        'wechat':'正文**必须全部放进 blocks 数组**。不要自作主张换成 lead / narrative / facts / closing 这类顶层键——前端只读 blocks，形状一变，整篇内容就会渲染成空白页。',
        'recap':'正文**必须全部放进 blocks 数组**，形状与公众号一致。',
        'xhs':'**不要返回 blocks**，只按上面的字段名输出。',
        'poster':('facts 只写真实值（时间 / 地点 / 价格 / 人数），每条 ≤ 10 字；'
                  'sellingPoints 3~5 条，每条 ≤ 12 字、带具体细节（数字或专名）、彼此不重复；'
                  'headline ≤ 12 字；subheadline ≤ 20 字，是一句能立住的主张；'
                  'preferredMediaRefs 只填真实照片的 ref。**不要返回 blocks。**'),
    }
    prompt=f"""基于同一场活动，重新创作 {labels.get(channel,channel)} 原生内容。不是活动详情删减版。
{cover_note}
Activity Master（事实锚点）：{json.dumps(activity_master,ensure_ascii=False)}
详情页的编辑判断（只用来理解这场活动的定位，**禁止复用它的句子、小标题与结构**——本渠道是重新写一遍）：{json.dumps(detail_view,ensure_ascii=False)}
{fact_note}
{_WRITING_CONTRACT}

本渠道要求：
{briefs.get(channel,'')}
输出结构（强制；字段名不得改动）：
{structures.get(channel,structures['wechat'])}
{structure_notes.get(channel,'')}
严格 JSON，不要 Markdown，不要解释。"""
    gw=await generate_json(club_id=club_id,task_type=channel,system_prompt=SYSTEM,user_prompt=prompt)
    # 渠道成品（海报 / 小红书九宫格）同样只能用真实照片：品牌 logo 与空白底图不能上去
    photos={m.get('ref') for m in activity_master.get('media',[]) if isinstance(m,dict)
            and m.get('ref') and (m.get('kind') or 'photo')=='photo'}
    photo_seq=[m.get('ref') for m in activity_master.get('media',[]) if isinstance(m,dict)
               and m.get('ref') and (m.get('kind') or 'photo')=='photo']
    if gw:
        data=gw.data or {}
        # 模型自创结构（lead/narrative/facts/closing）时把内容救回 blocks，别让前端渲染空白
        if channel in ('wechat','recap'): data=normalize_channel_blocks(data)
        elif channel=='poster': data=normalize_poster(data,str(activity_master.get('title') or ''))
        if cover_url: data['coverUrl']=cover_url
        if isinstance(data.get('blocks'),list):
            # ① 照抄闸门：与原文连续重合超限的段落交回模型定向重写一次（详见 source_echo_blocks 注释）
            if source_text and channel in ('wechat','xhs'):
                bad=source_echo_blocks(data['blocks'],source_text)
                if bad:
                    try:
                        fix=await generate_json(club_id=club_id,task_type=channel,
                                                system_prompt=ECHO_REWRITE_SYSTEM,
                                                user_prompt=_echo_rewrite_prompt(data['blocks'],bad,source_text))
                        fixed=(fix.data if fix else None)
                        if isinstance(fixed,dict): fixed=fixed.get('blocks') or fixed.get('items')
                        for item in (fixed or []):
                            if not isinstance(item,dict): continue
                            i=item.get('i')
                            if isinstance(i,int) and 0<=i<len(data['blocks']):
                                for k in ('headline','body'):
                                    if isinstance(item.get(k),str) and item[k].strip():
                                        data['blocks'][i][k]=item[k].strip()
                    except Exception:
                        pass   # 回炉失败就接受原稿，不能让整条生成链路挂掉
            # ② 图片兜底：模型整篇没引用照片时按顺序铺图，别让老板拿到一篇纯文字推文
            data['blocks']=_ensure_media_refs(data['blocks'],photo_seq)
            # ③ 只允许真实照片的 ref：模型若引用了品牌 logo / 空白图的 ref，直接剔除
            data['blocks']=_sanitize_blocks(data['blocks'],allowed_refs=photos)
        # 公众号推文 / 海报同样不许出现成本数据：主视觉文案里印一行「人均 ¥3,806」同样是事故
        data,_changed=scrub_cost_data(data)
        # 与详情共用同一道事实防线：渠道文案一样会把品牌名音译掉（icebreaker → 冰破），
        # 以前这道检查只在详情上跑，推文里的错品牌名会直接发出去。
        if source_text:
            data=_map_strings(data,lambda t:restore_brand_names(t,source_text))
        return data,gw
    title=activity_master.get('title','活动');idea=detail.get('coreSellingIdea','')
    gallery=[m.get('ref') for m in activity_master.get('media',[]) if isinstance(m,dict)
             and m.get('ref') and (m.get('kind') or 'photo')=='photo']
    if channel=='wechat':data={'title':title,'summary':idea,'coverUrl':cover_url,'blocks':detail.get('blocks',[])[:6]+[{'type':'cta','headline':'查看活动详情并报名'}]}
    elif channel=='xhs':data={'titleOptions':[title,f"周末去{activity_master.get('location','山里')}，这次不赶行程"],'hook':idea,'body':idea+'\n\n具体日期、费用和报名信息见活动详情。','tags':['户外','周末去哪儿','自然'],'imageSequence':([cover_url] if cover_url else [])+gallery[:8],'coverUrl':cover_url}
    elif channel=='poster':
        # 演示模式也要给足版式需要的字段，否则海报只剩标题 + 一行灰字（看不出设计）
        _facts=[str(x) for x in (activity_master.get('date'),activity_master.get('location')) if x]
        try:
            _p=activity_master.get('price')
            if _p not in (None,''): _facts.append('¥%g / 人'%float(_p))
        except (TypeError,ValueError): pass
        if activity_master.get('capacity'): _facts.append('限 %s 人'%activity_master['capacity'])
        _pts=[]
        for x in (activity_master.get('services') or []):
            t=x if isinstance(x,str) else ((x.get('name') or x.get('title') or '') if isinstance(x,dict) else '')
            t=str(t).strip()
            if t and len(t)<=12 and t not in _pts: _pts.append(t)
        if not _pts and idea: _pts=[idea[:12]]
        data={'headline':title,'subheadline':idea or str(activity_master.get('location') or ''),
              'facts':_facts[:4],'sellingPoints':_pts[:4],'cta':'扫码报名',
              'preferredMediaRefs':([cover_url] if cover_url else [])+gallery[:1],'coverUrl':cover_url}
    else:data={'needsActualData':True,'title':f'{title}｜活动回顾','message':'请上传现场照片或领队记录后再生成真实活动回顾。'}
    return data,record_mock_usage(club_id,channel,prompt,data)
