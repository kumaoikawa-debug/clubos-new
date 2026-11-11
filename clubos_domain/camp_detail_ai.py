"""营地 v4 · AI 图文详情生成（2026-11-11）

设计意图
--------
营位 / 装备租赁 / 营地小站 / 营地档案四类对象共用一套「素材工作台」：
老板只上传本地照片（≤5 张）并在**一个文本框**里写照片说明、文件要点与经营规则，
模型直接产出结构化的图文详情，不再需要手填规格 / 设施 / 须知 / 简介 / 图文详情五个表单。

与活动详情 AI 的关系
--------------------
沿用 `ai_gateway.generate_json` 的统一鉴权 / 路由 / 容灾 / 用量记录（task_type='camp_detail'），
以及 `ai_billing` 的 Credits 口径；但**不复用活动文案契约**——活动是行程叙事，
营地商品是「规格 + 设施 + 规则 + 场景段落」的商品详情，两者提示词与输出形状都不同。

硬约束
------
1. 绝不编造价格、押金、容量、退改规则：这些是已知事实，只从 base_info 带入；
   模型只在给定资料范围内组织语言，资料没写的写「以现场公示为准」。
2. 图片引用只用 mediaRefs 索引（0-based），不返回外链、不生成图片 URL。
3. mock 模式（无模型 Key）也要返回完整可渲染结构，保证本地/回归可跑。
"""
from __future__ import annotations

import json
from typing import Any

from ai_gateway import generate_json, record_mock_usage, effective_gateway_mode

KIND_CN = {'pitch': '营位 / 房间', 'rental': '装备租赁', 'store': '营地小站商品', 'profile': '营地'}

STRUCTURE = {
    'subtitle': '', 'highlights': [], 'specs': [], 'facilities': [],
    'policies': [], 'description': '', 'sections': [], 'coverIndex': 0,
}

SYSTEM_PROMPT = """你是户外营地的内容编辑，负责把营地老板提供的原始素材整理成一份**可直接上架的高标准商品图文详情**。

写作要求：
- 中文、简洁、具体、有场景感；不要营销套话、不要「欢迎来到」「您将体验到」这类 AI 腔，不要感叹号堆砌。
- 只使用给定资料中的事实。价格、押金、可住人数、退改规则等已在「已知事实」里给全，禁止自行编造或推算；资料没提到的一律不写，或写「以现场公示为准」。
- 不要出现成本、毛利、内部结算等经营数据；不要出现其他平台、竞品或联系方式。
- 图片只能按 mediaRefs 的序号引用，序号从 0 开始，不得超过提供的图片总数；没有合适的图就填空数组。
- 段落 sections 控制在 3~6 段，每段 headline 不超过 14 字，body 60~160 字，按「整体印象 → 空间/规格 → 使用体验 → 规则与提示」组织。
- highlights 3~5 条，每条不超过 12 字；specs 4~8 条（label/value 都要短）；facilities 4~8 条；policies 2~4 条（label/text）。

严格只输出如下 JSON（不要代码块、不要解释）：
{"subtitle":"一句话卖点","highlights":["..."],"specs":[{"label":"","value":""}],"facilities":["..."],"policies":[{"label":"","text":""}],"description":"列表页用的 40~80 字简介","sections":[{"headline":"","body":"","mediaRefs":[0]}],"coverIndex":0}"""


def _norm(x: Any, *, media_count: int, base: dict) -> dict:
    """把模型输出收敛成前端可渲染的确定形状；任何脏字段都在这里兜住。"""
    d = x if isinstance(x, dict) else {}
    out = dict(STRUCTURE)
    out['subtitle'] = str(d.get('subtitle') or '')[:60]
    out['description'] = str(d.get('description') or '')[:300]

    def strlist(v, limit, maxlen):
        if not isinstance(v, list):
            return []
        return [str(i).strip()[:maxlen] for i in v if str(i).strip()][:limit]

    out['highlights'] = strlist(d.get('highlights'), 5, 20)
    out['facilities'] = strlist(d.get('facilities'), 8, 40)
    out['specs'] = [
        {'label': str(s.get('label') or '')[:20], 'value': str(s.get('value') or '')[:60]}
        for s in (d.get('specs') or []) if isinstance(s, dict) and (s.get('label') or s.get('value'))
    ][:8]
    out['policies'] = [
        {'label': str(s.get('label') or '')[:20], 'text': str(s.get('text') or '')[:200]}
        for s in (d.get('policies') or []) if isinstance(s, dict) and (s.get('label') or s.get('text'))
    ][:4]
    secs = []
    for s in (d.get('sections') or []):
        if not isinstance(s, dict):
            continue
        refs = [int(i) for i in (s.get('mediaRefs') or [])
                if isinstance(i, (int, float)) and 0 <= int(i) < media_count][:3]
        body = str(s.get('body') or '').strip()
        head = str(s.get('headline') or '').strip()[:20]
        if head or body:
            secs.append({'headline': head, 'body': body[:400], 'mediaRefs': refs})
    out['sections'] = secs[:6]
    ci = d.get('coverIndex')
    out['coverIndex'] = int(ci) if isinstance(ci, (int, float)) and 0 <= int(ci) < media_count else 0
    # 已知事实永不由模型改写：规格里补上系统里的硬参数，避免老板忘写导致详情页没信息。
    hard = [{'label': k, 'value': v} for k, v in (base.get('hardSpecs') or {}).items() if v not in (None, '', 0)]
    have = {s['label'] for s in out['specs']}
    out['specs'] = (out['specs'] + [h for h in hard if h['label'] not in have])[:8]
    if not out['subtitle']:
        out['subtitle'] = str(base.get('name') or '')
    if not out['description']:
        out['description'] = str(base.get('name') or '') + ' · 详情以现场公示为准'
    return out


def _mock_detail(*, base: dict, source_text: str, media_count: int) -> dict:
    """mock 模式：不调模型，也产出结构完整、可直接渲染的详情（本地/回归用）。"""
    name = str(base.get('name') or '营地商品')
    txt = (source_text or '').strip()
    lead = (txt[:80] + ('…' if len(txt) > 80 else '')) if txt else '照片与规则已上传，等待 AI 生成图文详情。'
    hard = [{'label': k, 'value': str(v)} for k, v in (base.get('hardSpecs') or {}).items() if v not in (None, '', 0)]
    return _norm({
        'subtitle': name,
        'highlights': [x for x in (base.get('highlights') or [])][:4] or ['实景拍摄', '规则清晰', '到店即用'],
        'specs': hard,
        'facilities': [str(x) for x in (base.get('facilities') or [])][:6],
        'policies': [{'label': '预订说明', 'text': '下单后锁定库存 30 分钟，超时未支付自动释放。'},
                     {'label': '退改规则', 'text': '未使用订单请提前联系营地处理，具体以现场公示为准。'}],
        'description': name + '｜' + lead,
        'sections': [
            {'headline': '实地概览', 'body': lead, 'mediaRefs': list(range(min(media_count, 2)))},
            {'headline': '使用与规格', 'body': '规格与可用数量以本页参数为准，到店后由营地工作人员协助确认。',
             'mediaRefs': [2] if media_count > 2 else []},
            {'headline': '预订须知', 'body': '请按预订日期到店，携带订单号或预留手机号办理；详细规则以营地现场公示为准。',
             'mediaRefs': []},
        ],
        'coverIndex': 0,
    }, media_count=media_count, base=base)


async def generate_camp_detail(*, club_id: int, target_kind: str, base: dict,
                               source_text: str, media_paths: list[str]) -> tuple[dict, Any]:
    """返回 (结构化详情, GatewayResponse-like usage)。mock 模式自行记录用量。"""
    media_count = len(media_paths)
    base = dict(base or {})
    base['name'] = base.get('name') or KIND_CN.get(target_kind, '营地商品')
    task = 'camp_detail'
    prompt = json.dumps({
        '对象类型': KIND_CN.get(target_kind, '营地商品'),
        '已知事实': {k: v for k, v in base.items() if k != 'hardSpecs'},
        '硬参数': base.get('hardSpecs') or {},
        '老板提供的素材说明（一个文本框）': (source_text or '')[:6000],
        '可用图片数量': media_count,
    }, ensure_ascii=False)
    user = (f"请为「{base['name']}」生成{KIND_CN.get(target_kind,'营地商品')}图文详情。\n"
            f"以下是已知事实、老板写的说明与可用图片数量：\n{prompt}")

    usage = None
    raw = None
    if effective_gateway_mode()[0] != 'mock':
        try:
            resp = await generate_json(
                club_id=club_id, task_type=task,
                system_prompt=SYSTEM_PROMPT, user_prompt=user,
                images=[{'path': p} for p in media_paths],
            )
            if resp is not None:
                raw = resp.data if isinstance(resp.data, dict) else None
                usage = resp
        except Exception:
            raw = None  # 模型失败降级到 mock 结构，绝不把 500 抛给营地老板
    if raw is None:
        out = _mock_detail(base=base, source_text=source_text, media_count=media_count)
        usage = record_mock_usage(club_id, task, user, out)
    else:
        out = _norm(raw, media_count=media_count, base=base)
    return out, usage
