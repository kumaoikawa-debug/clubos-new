"""成本数据闸门 —— 方案里的成本/报价数据，任何前端都不得显示（2026-10-06 用户要求）。

背景：这条链路此前是**自相矛盾**的。ai_engine 的提示词里明明写着「成本、供应商报价、门店 SOP、
话术禁区、内部沟通等内容属于内部资料，一律不得进入 C 端成品」，app.py 读取 activity 时也写着
「source_json 是内部原始资料（可能含成本、供应商报价），绝不出接口」—— 可 `_extract_structured`
却把这套成本表**主动抓成** `activity_master.fees`，前端再把它渲染成「费用说明 PRICE」卡片。
用户截图实锤：一份始祖鸟高客方案（第 15 页「14 — COST 活动费用明细」）上传后，页面上出现了
`人均费用 ¥3,806.55`、`合计（未含税）¥76,131.00`、`策划执行 10%` —— 这是俱乐部的成本底价，
摆给顾客看等于把利润结构摊在桌面上。用户明确要求：这种成本数据，前端一律不显示。

三道闸门（源头 → 出口 → 渲染），缺一层都会漏：

1. `redact_cost_text()`  解析期。喂给 AI 的文本里就不含成本行 —— 模型无从照抄，也无从据此编造
   「人均 3806 元」写进叙事文案。只保留「服务项名」白名单（车费/门票/氧气/摄影…），
   客户该知道含什么，但不该知道每项多少钱。
2. `scrub_cost_data()`   出口。真模型（live 模式）会自己往 master/detail 里写价格，落库与下发前
   统一洗一遍：成本键（人均费用/合计（未含税）/单价/小计/毛利…）整条剔除，成本措辞的文案清空。
3. `static/shared.js`    渲染。库里**已经存在**的老数据（改代码之前生成的）也一并不显示 ——
   清洗只对生成期生效，前端兜底才是对存量数据的保障。

判定原则（宁缺勿错）：
- 只认「明晃晃的成本信号」：成本/未含税/毛利/策划执行/单价/小计/税费/预算/结算/返点/提成…
- 成本表语境下（资料含「成本预估 / 未含税 / 单价 / 小计」），`人均费用` 一律按成本处理，
  绝不当作对外售价 —— 否则 C 端会拿成本价当售价卖。
- 公开报价语义（售价/报名费/会员价/每人…）不受影响。
"""
from __future__ import annotations
import re

# ---------------------------------------------------------------------------
# 词表
# ---------------------------------------------------------------------------

# 成本表语境标记：命中说明这份资料带的是内部成本/报价表，而不是对外报价。
COST_CONTEXT = (
    '成本', '未含税', '不含税', '未税', '毛利', '利润', '净利', '策划执行', '策划费',
    '单价', '小计', '税', '预算', '结算', '返点', '提成', '供应商', '报价单', '成本预估',
)

# 绝不该出现在顾客眼前的行标签（即便这一行没有金额）。
COST_LABELS = (
    '合计', '总计', '小计', '单价', '总价', '总费用', '人均成本', '成本',
    '毛利', '利润', '净利', '税金', '税费', '未含税', '不含税', '含税', '税后', '税前',
    '策划执行', '策划费', '管理费率', '报价', '报价单', '预算', '结算', '返点', '提成',
    '成本预估', '费用结构', '费用条目', '前期合计', '前期计调', '利润率',
)

# **看语境**才判为成本的行标签。
# `人均费用` 是这套判定里最容易误伤的一个：在成本明细表里它是「合计 ÷ 人数」推出来的内部单价
# （本方案 76,131 ÷ 20 = 3,806.55）；但在一条普通招募文案里「人均费用 288 元」就是对外售价，
# 户外圈天天这么写。所以它只有在**整份资料/整块数据带成本语境**（成本预估 / 未含税 / 单价 /
# 小计 / 毛利…）时才算成本，否则照旧当公开价格对待 —— 宁可不删，也不能把正常报价判成成本。
COST_LABELS_CONTEXTUAL = ('人均费用', '人均价', '人均单价', 'PRICE')

# 对外报价语义：命中且**不含**成本信号时，这一行允许保留（可能是俱乐部想公开的价格）。
PUBLIC_PRICE_LABELS = (
    '售价', '对外报价', '对外价', '报名费', '活动价格', '活动价', '会员价', '新客价',
    '每人', '起价', '成人价', '儿童价', '优惠价', '特惠价',
)

# 金额写法：¥/￥ 前缀，或「数字 + 元/块」后缀。
MONEY_RE = re.compile(r'[¥￥]\s*[0-9][0-9,]*(?:\.[0-9]+)?|[0-9][0-9,]*(?:\.[0-9]+)?\s*(?:元|块钱|块)')

# 「某项 · 小计」这种成本表行：短行 + 金额 + 明显的表格分隔符
_TABLE_SEP_RE = re.compile(r'[|｜\t]')

# 结构标记，[Slide N] / [Page N] / [文件: x] —— 分段用，本身不进内容。
_MARKER_RE = re.compile(r'^\s*\[(?:Slide|Page)\s*\d+\]\s*$|^\s*\[文件[:：][^\]]*\]\s*$')

# 服务项白名单：成本表里「客户该知道的项」，只留名字、不留价格。
# 与 ai_engine._extract_structured 里费用项的取词口径保持一致（那里也剔除了「策划执行」）。
ITEM_WHITELIST = (
    '保姆大巴', '越野中转车', '中转车', '车费', '唐卡体验', '唐卡', '特色餐', '工作餐',
    '午餐', '晚餐', '早餐', '餐食', '住宿', '门票', '清洁费', '饮用水', '氧气',
    '摄影', '领队', '向导', '教练', '保险', '急救', '医药', '装备租赁', '活动物料',
    '巴士', '越野车', '景区交通',
)

# 成本段落里出现这些词的行一律不要（即使没有数字）——它们是表头/组织词，不是服务项。
_HEADING_NOISE = (
    '活动费用', '费用明细', '费用说明', '成本', 'COST', 'PRICE', '项目', '内容', '数量',
    '单价', '小计', '合计', '费用条目', '费用结构', '备注', '序号', '明细', '预估',
)


def _norm(s: str) -> str:
    """归一化：去空白、全角括号、下划线、连字符，便于子串判定。"""
    return re.sub(r'[\s（）()_\-—·:：]', '', str(s or ''))


def has_cost_context(text: str) -> bool:
    """资料是否带成本表语境。"""
    t = str(text or '')
    return any(k in t for k in COST_CONTEXT)


def is_public_price_row(line: str) -> bool:
    """对外报价行：带公开价格语义，且不带任何成本信号。"""
    t = str(line or '')
    if not any(k in t for k in PUBLIC_PRICE_LABELS):
        return False
    return not any(k in t for k in ('成本', '未含税', '不含税', '毛利', '策划执行', '单价',
                                    '小计', '合计', '税费', '税金', '预算', '结算'))


def is_cost_row(line: str, context: bool | None = None) -> bool:
    """这一行是否属于「不该给顾客看」的成本行。

    命中优先级（成本标签比公开标签更具体、更权威，必须先判）：
    ① 带成本标签（合计/单价/毛利/策划执行…）；
    ② 带语境标签（人均费用…）且 `context=True`；
    ③ 公开报价行（售价/会员价/每人…）不带成本信号 → 放行（不是成本）；
    ④ 表格行里同时有金额和成本味的分隔结构。
    注意：`人均费用` 这类「看语境」的标签也含「人均」这个公开价词，必须先按 ①② 判成本，
    否则会被 `is_public_price_row` 误当成公开价、把成本价当售价抽走（见 _public_price 的坑）。
    """
    t = str(line or '').strip()
    if not t:
        return False
    if any(k in t for k in COST_LABELS):
        return True
    if context and any(k in t for k in COST_LABELS_CONTEXTUAL):
        return True
    if is_public_price_row(t):
        return False
    # 「活动费用明细 · 按 20 人报价」这类标题行：带「报价」已在上面命中；
    # 这里再兜一层「成本表语境 + 短行 + 金额」的表格行（如 "2,500"、"前期合计 6,921"）。
    if MONEY_RE.search(t) and len(t) <= 24 and _TABLE_SEP_RE.search(t):
        return True
    # 成本语境下，**只由金额构成的值**就是成本数字（facts 里的 "¥3,806.55"）。
    # 非成本语境不启用这条：那时一个孤零零的价格通常就是俱乐部想公开的价。
    if context and MONEY_RE.search(t):
        rest = MONEY_RE.sub('', t).strip(' ,，。.、；;:：-—·/人元起')
        if not rest:
            return True
    return False


def is_cost_key(key: str, context: bool | None = None) -> bool:
    """字典键是否属于成本键（如 activity_master.fees['人均费用']）。

    同样成本标签优先于公开标签：`人均费用` 含「人均」这个公开价词，若先判 is_public_price_row
    会误判成公开键、漏洗。所以先查 COST_LABELS / 语境版，最后才放行公开标签。
    """
    k = _norm(key)
    if not k:
        return False
    if any(_norm(x) in k for x in COST_LABELS):
        return True
    if context and any(_norm(x) in k for x in COST_LABELS_CONTEXTUAL):
        return True
    if is_public_price_row(k):
        return False
    return False



# ---------------------------------------------------------------------------
# 闸门 1：解析期 —— 从喂给 AI 的文本里剔掉成本
# ---------------------------------------------------------------------------

def _segment_is_cost_sheet(seg: str) -> bool:
    """一个段落（通常是一页）是不是成本明细页。

    判据要能认出「14 — COST 活动费用明细」这种页：它由标题 + 一堆单元格散行组成，
    每格一行、没有自然句。所以看两个信号：成本标签出现次数、金额出现次数。
    """
    hits = sum(1 for k in COST_LABELS if k in seg)
    money = len(MONEY_RE.findall(seg))
    if hits >= 2:
        return True
    if hits >= 1 and money >= 2:
        return True
    return False


def _keep_item_names(seg: str, ctx: bool = False) -> list[str]:
    """成本页里只捞回「服务项名」：短、无数字、命中白名单、不是表头。

    这是有意的信息保留 —— 顾客该看到「含门票、含氧气、含全程摄影」，但不该看到它们的单价。
    白名单是受控的，不做任何自由取词，避免把成本表的表头当成服务项带出去。
    """
    out: list[str] = []
    for raw in seg.split('\n'):
        t = raw.strip()
        if not t or len(t) > 10:
            continue
        if re.search(r'[0-9]', t):
            continue                       # 带数字的（20 人 / 2,500 / 10%）一律不要
        if any(k in t for k in _HEADING_NOISE):
            continue
        if is_cost_row(t, ctx):
            continue
        if t.startswith(('D1', 'D2', 'Day', 'DAY', '第')):
            continue
        for name in ITEM_WHITELIST:
            if name in t and name not in out:
                out.append(name)
                break
    return out


def redact_cost_text(text: str) -> tuple[str, dict]:
    """解析期清洗：返回 (可直接喂给 AI 的文本, 统计)。

    按 `[Slide N]` / `[Page N]` 分段。整段是成本明细页 → 只留服务项名；
    其余段落 → 逐行删掉成本行（夹在正常内容里的「人均费用 ¥288」也一并消失）。
    统计用于落日志与自检：`costLines` 是被删掉的行数，`costBlocks` 是判定为成本页的段数。
    """
    src = str(text or '')
    if not src.strip():
        return src, {'costLines': 0, 'costBlocks': 0}

    # 整份资料的语境：带成本信号时，`人均费用` 这种「看语境」的标签也按成本处理。
    doc_ctx = has_cost_context(src)

    lines = src.split('\n')
    # 切段：结构标记单独成段头，只做分段用，原样保留（下游 _strip_markers 会处理）。
    segments: list[list[str]] = [[]]
    for ln in lines:
        if _MARKER_RE.match(ln) and segments[-1]:
            segments.append([])
        segments[-1].append(ln)

    cost_lines = 0
    cost_blocks = 0
    out_segments: list[str] = []
    for seg_lines in segments:
        seg = '\n'.join(seg_lines)
        if seg.strip() and _segment_is_cost_sheet(seg):
            cost_blocks += 1
            cost_lines += sum(1 for x in seg_lines if x.strip())
            head = next((x for x in seg_lines if _MARKER_RE.match(x)), '')
            kept = _keep_item_names(seg, True)
            if kept:
                out_segments.append((head + '\n' if head else '') + '\n'.join(kept))
            elif head:
                out_segments.append(head)
            continue
        kept_lines = []
        for ln in seg_lines:
            t = ln.strip()
            # 成本行整行丢弃；空行保留（下游会压掉多余空行）
            if t and is_cost_row(t, doc_ctx):
                cost_lines += 1
                continue
            kept_lines.append(ln)
        # 段内已经出现「成本表」信号时，把该段里「纯数字行」也清掉 ——
        # 成本表的单元格是散行（49,850 / 2,500 / 80），它们单独看不含标签，但组合起来就是报价底价。
        if _segment_is_cost_sheet(seg):
            cost_blocks += 1
            kept_lines = [ln for ln in kept_lines
                          if not re.fullmatch(r'\s*[¥￥]?\s*[0-9][0-9,]*(?:\.[0-9]+)?\s*(?:元)?\s*%?\s*', ln)]
        out_segments.append('\n'.join(kept_lines))

    cleaned = '\n'.join(out_segments)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned, {'costLines': cost_lines, 'costBlocks': cost_blocks}


# ---------------------------------------------------------------------------
# 闸门 2：出口 —— 递归清洗已成形的数据（模型写出来的也算）
# ---------------------------------------------------------------------------

def scrub_cost_text(value: str, context: bool | None = None) -> str:
    """洗一段文案：整体是成本行就清空；长文里按句删掉成本句。

    `context=True` 时，「看语境」的标签（人均费用…）也一并当作成本。
    """
    s = str(value or '')
    if not s.strip():
        return s
    if is_cost_row(s, context):
        return ''
    parts = re.split(r'(?<=[。；;\n])', s)
    kept = [p for p in parts if p.strip() and not is_cost_row(p, context)]
    out = ''.join(kept).strip()
    # 删剩的部分若只是标点/空白，等同清空
    return '' if not re.search(r'[\u4e00-\u9fa5A-Za-z0-9]', out) else out


def scrub_cost_data(data, context: bool | None = None):
    """递归清洗：剔除成本键、清空成本文案。

    `internalData` 是内部经营资料（俱乐部/平台侧编辑用），保持原样 —— 与 apply_fact_guard
    的口径一致：内部字段不做任何改写，避免把运营口径改坏。

    `context` 传 None 时自动从数据本身推断；显式传 True 用于「同一份 payload 里别处已证明
    这是成本数据」的场景 —— 成本语境是整份 payload 共享的，master 证明了就不必让 detail 再证明一遍。
    返回 (清洗后的数据, 是否发生过改动)。
    """
    if not isinstance(data, (dict, list)):
        return data, False
    if context is None:
        context = has_cost_context(_flatten_text(data))
    return _scrub(data, bool(context))



def _flatten_text(node, acc=None):
    """把数据结构里的所有字符串拼成一段文本，用于判断「这块数据是否带成本语境」。"""
    if acc is None:
        acc = []
    if isinstance(node, dict):
        for k, v in node.items():
            acc.append(str(k))
            _flatten_text(v, acc)
    elif isinstance(node, list):
        for v in node:
            _flatten_text(v, acc)
    elif isinstance(node, str):
        acc.append(node)
    return '\n'.join(acc)


def _empty(v) -> bool:
    """清洗后是否已经什么都不剩（用于把空壳键/空壳块一并摘掉）。"""
    return v is None or v == '' or (isinstance(v, (list, dict)) and not v)


def _content(v) -> bool:
    """节点里还留有可展示的内容（非空字符串 / 非空容器）。"""
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict)):
        return bool(v)
    return v is not None


# 「结构键」：它们只声明这个块是什么，本身不构成内容。判断一个块是否被洗成空壳时要排除。
_STRUCT_KEYS = ('type', 'layout', 'template', 'origin')

# 「标签键」：facts/info 里 `{label,value}` 这类成对结构。标签一旦被判定成成本，
# 同一个 item 的 value 也必须一起丢 —— 否则会留下 {'value':'10%'} 这种没有标签的孤儿值，
# 前端照样渲染出「10%」，等于把策划执行费率换个姿势漏出去。
_LABEL_KEYS = ('label', 'name', 'title', 'key', 'itemname', 'labelname')


def _scrub(node, ctx=False):
    """内部递归：`ctx` 为整块数据的成本语境，决定「人均费用」这类标签算不算成本。

    只清理**被洗过**的节点：`{'type':'divider'}` 这种本来就无内容的结构块不会被误删。
    """
    if isinstance(node, dict):
        out = {}
        changed = False
        label_dropped = False
        for k, v in node.items():
            if str(k) == 'internalData':
                out[k] = v                      # 内部资料不动
                continue
            if is_cost_key(k, ctx):
                changed = True
                if str(k).lower() in _LABEL_KEYS:
                    label_dropped = True        # 标签是成本 → 整条 item 一起走
                continue                        # 成本键整条剔除
            nv, ch = _scrub(v, ctx)
            changed = changed or ch
            if ch and _empty(nv):
                # 值被洗空（如 info.items 里唯一那条「人均费用 ¥3,806.55」）→ 键也去掉，
                # 否则前端会渲染出一个只有标签、没有值的空行。
                changed = True
                if str(k).lower() in _LABEL_KEYS:
                    label_dropped = True    # 标签本身被洗空 → 整条 item 是成本项
                continue
            out[k] = nv
        if label_dropped:
            # {'label':'策划执行','value':'10%'} —— 标签已判定为成本，整条丢弃，
            # 否则会剩下一个没有标签的 '10%' 被渲染出来。
            return '', True
        if changed and not any(_content(v) for k, v in out.items() if k not in _STRUCT_KEYS):
            # 洗成空壳的块整块丢弃：facts 的 items 被清空后只剩 {"type":"facts"}，
            # 留着前端会渲染出一个空的「关键事实条」骨架
            return '', True
        return out, changed
    if isinstance(node, list):
        out = []
        changed = False
        for v in node:
            nv, ch = _scrub(v, ctx)
            changed = changed or ch
            if ch and _empty(nv):
                changed = True                  # 清空后的空块不再占位
                continue
            out.append(nv)
        return out, changed
    if isinstance(node, str):
        nv = scrub_cost_text(node, ctx)
        return nv, nv != node
    return node, False


def sanitize_for_frontend(master, detail=None):
    """下发前的统一闸门：清洗成本数据 + 把「成本表来源」的价格归零。

    - master.fees 里的成本行（合计（未含税）/人均费用/备注含成本预估）整条剔除；
    - master.price 若被判定为成本推导（fees 里留有成本键 = 铁证）→ 归零并打 `priceFrom='pending'`，
      前端显示「价格待定」，等俱乐部自己定价。
      宁可让价格空着，也绝不能把成本价当售价卖 —— 用户截图那份方案的成本是 ¥3,806.55/人，
      而对外售价必然更高，把这行数字当售价展示等于直接亏掉整个利润。

    返回 (master, detail, changed, cost_derived)。detail 可为 dict/None。
    `cost_derived` 是「这份活动来自成本表」的铁证判定 —— 调用方（app.py）需要它来把
    **顶层 activities.price 与每个团期 price** 一并归零（那些字段不经过 master，若不处理，
    列表卡 / 详情 hero / 报名下单仍会把成本价当售价露出）。
    """
    if not isinstance(master, dict):
        return master, detail, False, False
    cost_derived = master_has_cost_evidence(master)   # 必须在清洗前判定
    master, changed = scrub_cost_data(master, cost_derived or None)
    if cost_derived:
        try:
            # 俱乐部一旦为这份活动设定了真实对外售价（priceFrom='priced'），就不再是「待定」，
            # 不要再把它归零 —— 否则俱乐部定价后 C 端每次读取都会把价格打回 0（粘性 bug）。
            if master.get('priceFrom') != 'priced' and float(master.get('price') or 0) > 0:
                master['price'] = 0
                master['priceFrom'] = 'pending'
                changed = True
        except (TypeError, ValueError):
            pass
    if isinstance(detail, (dict, list)):
        # 语境继承：master 已证明这份数据来自成本表，detail 不再单独判断一次
        detail, c2 = scrub_cost_data(detail, cost_derived or None)
        changed = changed or c2
    return master, detail, changed, cost_derived


def master_has_cost_evidence(master) -> bool:
    """master 里是否存在「这份活动的数据来自成本表」的铁证。

    用途：**价格归零判断**。一份从成本表生成的活动，`master.price` 是「合计 ÷ 人数」推出来的
    内部单价（本方案 76,131 ÷ 20 = 3,806.55），绝不能当对外售价卖；而 fees 里若还留着成本键
    （合计（未含税）/人均费用/单价…），就是最直接的证据 —— 改代码之前生成的老活动正是这个样子，
    它们在库里存着 `price=3806.55`，必须靠这个判据把价格一并归零。
    """
    if not isinstance(master, dict):
        return False
    ctx = has_cost_context(_flatten_text(master))
    fees = master.get('fees')
    stack = [fees] if fees is not None else []
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if is_cost_key(k, ctx):
                    return True
                if isinstance(v, str) and is_cost_row(v, ctx):
                    return True
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, str) and is_cost_row(cur, ctx):
            return True
    return False

