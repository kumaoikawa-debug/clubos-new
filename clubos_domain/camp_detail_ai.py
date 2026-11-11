"""营地 v4.3 #814 · AI 图文详情生成。

四类对象（profile / pitch / rental / store）共用一套素材工作台：老板上传商品照片与
PDF / Word / PPT / 图片资料，在一个文本框里写说明，系统产出结构化图文详情。

#814 修复的三件事
------------------
1. 旧版 prompt 只有「可用图片数量」一个整数，图片从未被读取；现在每张图都先经
   `camp_ai_kit.describe_image()` 生成画面观察，观察结果进入 prompt，并作为 `media[]`
   下发给 C 端当配图（线上 mock 模式同样生效，不依赖视觉模型 Key）。
2. 旧版 mock 把老板原文前 80 字直接塞进「实地概览」，且 `description` 被写进
   `camp_profiles.intro_text` 压在 C 端头图上；现在 `description` 是短句，段落内容由
   「画面观察 + 原文事实 + 系统硬参数」分别组装，不再复制原文。
3. 引入版式模板库 `camp_ai_kit.TEMPLATES`，模板 id / 每段 layout 随结果落库，
   C 端按 layout 渲染整版大图 / 左图右文 / 多图网格 / 纯文字。

硬约束不变：价格、押金、容量、退改规则只从已知事实带入，模型不得编造；
`media[]` 只下发公开可访问的 URL，绝不外泄服务器本地路径。
"""
from __future__ import annotations

import json
from typing import Any

from ai_gateway import generate_json, record_mock_usage, effective_gateway_mode
from clubos_domain import camp_ai_kit as kit

KIND_CN = {'pitch': '营位 / 房间', 'rental': '装备租赁', 'store': '营地小站商品', 'profile': '营地'}

STRUCTURE = {
    'subtitle': '', 'highlights': [], 'specs': [], 'facilities': [],
    'policies': [], 'description': '', 'sections': [], 'coverIndex': 0,
    'media': [], 'templateId': kit.DEFAULT_TEMPLATE, 'visualSummary': '', 'aiMode': 'local',
}

SYSTEM_PROMPT = """你是户外营地的内容编辑，负责把营地老板提供的原始素材整理成一份**可直接上架的高标准商品图文详情**。

写作要求：
- 中文、简洁、具体、有场景感；不要营销套话、不要「欢迎来到」「您将体验到」这类 AI 腔，不要感叹号堆砌。
- 只使用给定资料中的事实。价格、押金、可住人数、退改规则等已在「已知事实」里给全，禁止自行编造或推算；资料没提到的一律不写，或写「以现场公示为准」。
- 「资料图片观察」是系统对每张照片的真实画面分析，**必须据此选图与写场景描述**：哪张是开阔湖景、哪张是设施特写，就让对应段落引用对应序号，不要随机编号。
- 不要出现成本、毛利、内部结算等经营数据；不要出现其他平台、竞品或联系方式。
- 图片只能按 mediaRefs 的序号引用，序号从 0 开始且必须小于「可用媒体清单」的条数；纯文字段落填空数组。
- 段落 sections 控制在 3~6 段，每段 headline 不超过 14 字，body 60~160 字。
- highlights 3~5 条，每条不超过 12 字；specs 4~8 条（label/value 都要短）；facilities 4~8 条；policies 2~4 条（label/text）。
- description 只写一句 40 字以内的列表页短简介，**不要把规则原文、退订条款整段抄进来**（长内容放 sections）。

严格只输出如下 JSON（不要代码块、不要解释）：
{"subtitle":"一句话卖点","highlights":["..."],"specs":[{"label":"","value":""}],"facilities":["..."],"policies":[{"label":"","text":""}],"description":"40字内短简介","sections":[{"headline":"","body":"","mediaRefs":[0]}],"coverIndex":0}"""

_SAFE_PREFIX = ('/static/', '/uploads/', 'https://', 'data:image/')


def _clean_media(media: Any) -> list[dict]:
    """只保留 C 端可渲染字段，杜绝本地绝对路径 / 像素统计外泄。"""
    out = []
    for m in (media or [])[:20]:
        if not isinstance(m, dict):
            continue
        u = str(m.get('url') or '').strip()
        ok = u.startswith(_SAFE_PREFIX) or (
            u.startswith('/api/public/clubs/')
            and ('/camp-media-file/' in u or '/camp-src-file/' in u))
        if not ok:
            continue
        foc = m.get('focal') if isinstance(m.get('focal'), dict) else None
        out.append({
            'url': u,
            'name': str(m.get('name') or '')[:80],
            'caption': str(m.get('caption') or '')[:220],
            'source': 'gallery' if m.get('source') == 'gallery' else 'reference',
            'tags': [str(x)[:20] for x in (m.get('tags') or [])][:6],
            'focal': {'x': float(foc.get('x', .5)), 'y': float(foc.get('y', .5))} if foc else None,
        })
    return out


def _visual_summary(media: list[dict]) -> str:
    gal = sum(1 for m in media if m.get('source') == 'gallery')
    ref = len(media) - gal
    tags = []
    for m in media:
        for t in (m.get('tags') or []):
            if t not in tags:
                tags.append(t)
    return (f'已识别 {gal} 张商品照片 + {ref} 张资料图片，共 {len(media)} 张可用；'
            f'画面关键词：{"、".join(tags[:6]) if tags else "实景"}。')


def _norm(x: Any, *, media_count: int, base: dict, media: list[dict] | None = None,
          template_id: Any = None, ai_mode: str = 'local') -> dict:
    """把模型输出收敛成前端可渲染的确定形状；任何脏字段都在这里兜住。"""
    d = x if isinstance(x, dict) else {}
    out = dict(STRUCTURE)
    out['subtitle'] = str(d.get('subtitle') or '')[:60]
    # 短简介硬截断：防止旧模型 / 旧任务把整段规则灌进头图
    out['description'] = str(d.get('description') or '').strip().replace('\n', ' ')[:60]

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
        body = str(s.get('body') or '').strip()
        head = str(s.get('headline') or '').strip()[:20]
        if head or body:
            secs.append({'headline': head, 'body': body[:400],
                         'mediaRefs': s.get('mediaRefs') or []})
    out['sections'] = kit.apply_blueprint(secs, template_id, media_count)
    ci = d.get('coverIndex')
    out['coverIndex'] = int(ci) if isinstance(ci, (int, float)) and 0 <= int(ci) < media_count else 0
    hard = [{'label': k, 'value': str(v)} for k, v in (base.get('hardSpecs') or {}).items()
            if v not in (None, '', 0)]
    have = {s['label'] for s in out['specs']}
    out['specs'] = (out['specs'] + [h for h in hard if h['label'] not in have])[:8]
    if not out['subtitle']:
        out['subtitle'] = str(base.get('name') or '')
    if not out['description']:
        out['description'] = str(base.get('name') or '') + ' · 详情以现场公示为准'
    out['media'] = _clean_media(media)
    out['templateId'] = kit.get_template(template_id)['id']
    out['visualSummary'] = _visual_summary(out['media'])
    out['aiMode'] = 'model' if ai_mode == 'model' else 'local'
    return out


def _first_scene(media: list[dict]) -> str:
    for m in media:
        cap = str(m.get('caption') or '')
        if cap:
            return cap.split('；')[0].split('：')[-1][:70]
    return ''


def _local_detail(*, base: dict, source_text: str, media: list[dict],
                  template_id: Any) -> dict:
    """无视觉模型时的本地组装：画面观察 + 原文事实 + 硬参数，绝不复制整段原文。"""
    facts = kit.extract_facts(source_text)
    name = str(base.get('name') or '营地商品')
    usable = [i for i, m in enumerate(media) if m.get('usable', True)]
    gal = [i for i in usable if media[i].get('source') == 'gallery']
    ref = [i for i in usable if media[i].get('source') != 'gallery']
    scene = _first_scene(media)
    tags = []
    for m in media:
        for t in (m.get('tags') or []):
            if t not in tags:
                tags.append(t)

    hard = [{'label': k, 'value': str(v)} for k, v in (base.get('hardSpecs') or {}).items()
            if v not in (None, '', 0)]
    specs = (hard + facts['specs'])[:8]
    facilities = ([str(x) for x in (base.get('facilities') or [])] + facts['facilities'])[:8]
    facilities = list(dict.fromkeys(facilities))
    policies = facts['policies'] or [
        {'label': '预订说明', 'text': '下单后锁定库存 30 分钟，超时未支付自动释放。'},
        {'label': '退改规则', 'text': '未支付订单可直接取消；已支付订单请联系营地处理，具体以现场公示为准。'}]

    spec_txt = '；'.join(f"{s['label']} {s['value']}" for s in specs[:5]) or '规格与可用数量以本页参数为准，到店后由工作人员协助确认。'
    fac_txt = ('现场可见 ' + '、'.join(facilities[:5]) + '。') if facilities else '设施配置以本页「设施与服务」为准。'
    rule_txt = '规则要点：' + ('；'.join(p['text'] for p in policies[:2])
                            or '详细规则以营地现场公示为准。')
    scene_txt = (f"照片里是{scene}。" if scene else '现场照片已上传，画面以 C 端实景图廊为准。')

    def body_for(headline: str, idx: int) -> str:
        if re_search(headline, '概览|一眼|实拍|实景|看见'):
            # #814：概览只讲画面，绝不再把老板写的规则/参数原文粘进来
            return (scene_txt
                    + ' 图片来自本次上传的实景资料，拍摄范围与当季植被以到店所见为准。')[:400]
        if re_search(headline, '规格|配置|空间|参数'):
            return (f'{spec_txt}。{fac_txt}' if facilities else spec_txt + '。')[:400]
        if re_search(headline, '体验|使用|设施|服务'):
            return (fac_txt + (f' 画面标签：{"、".join(tags[:4])}。' if tags else '')
                    + ' 具体开放情况请到店咨询工作人员。')[:400]
        return (f'{rule_txt} 请在预订日期携带订单号或预留手机号到店办理。')[:400]

    bp = kit.get_template(template_id)['blueprint']
    pool = (gal[:2] + ref[:2] + gal[2:] + ref[2:]) or usable
    cursor = 0
    sections = []
    for i, b in enumerate(bp):
        n = int(b['maxImages'])
        refs = []
        if n and pool:
            for _ in range(n):
                refs.append(pool[cursor % len(pool)])
                cursor += 1
        sections.append({'headline': b['headline'], 'body': body_for(b['headline'], i),
                         'layout': b['layout'], 'mediaRefs': refs})

    subtitle = (name + (' · ' + tags[0] if tags else ''))[:60]
    description = (name + (' · ' + scene[:26] if scene else ' · 实景已上传')
                   + '，详情见营地介绍')[:60]
    highlights = (tags[:3] + facilities[:2] + ['规则清晰'])
    highlights = list(dict.fromkeys([h for h in highlights if h]))[:5]

    return _norm({
        'subtitle': subtitle, 'highlights': highlights, 'specs': specs,
        'facilities': facilities, 'policies': policies, 'description': description,
        'sections': sections, 'coverIndex': 0,
    }, media_count=len(media), base=base, media=media,
        template_id=template_id, ai_mode='local')


def re_search(s: str, pattern: str) -> bool:
    import re
    return bool(re.search(pattern, s or ''))


async def generate_camp_detail(*, club_id: int, target_kind: str, base: dict,
                               source_text: str, media_paths: list[str],
                               media: list[dict] | None = None,
                               template_id: Any = kit.DEFAULT_TEMPLATE) -> tuple[dict, Any]:
    """返回 (结构化详情, GatewayResponse-like usage)。"""
    media = media or []
    media_count = len(media)
    base = dict(base or {})
    base['name'] = base.get('name') or KIND_CN.get(target_kind, '营地商品')
    tid = kit.get_template(template_id)['id']
    task = 'camp_detail'
    clean_media = _clean_media(media)
    prompt = json.dumps({
        '对象类型': KIND_CN.get(target_kind, '营地商品'),
        '已知事实': {k: v for k, v in base.items() if k != 'hardSpecs'},
        '硬参数': base.get('hardSpecs') or {},
        '老板提供的素材说明（一个文本框）': (source_text or '')[:6000],
        '可用图片数量': media_count,
        '资料图片观察（系统对每张照片的真实画面分析）': kit.observation_text(media),
        '可用媒体清单（mediaRefs 只能引用这里的序号）': [
            {'序号': i, '来源': '商品照片' if m.get('source') == 'gallery' else '上传资料',
             '画面': str(m.get('caption') or '')[:120]}
            for i, m in enumerate(clean_media)],
        '版式要求': kit.template_prompt(tid),
    }, ensure_ascii=False)
    user = (f"请为「{base['name']}」生成{KIND_CN.get(target_kind, '营地商品')}图文详情。\n"
            f"以下是已知事实、老板说明、资料图片观察与版式要求：\n{prompt}")

    usage = None
    raw = None
    mode = effective_gateway_mode()[0]
    if mode != 'mock' and media_paths:
        try:
            resp = await generate_json(
                club_id=club_id, task_type=task,
                system_prompt=SYSTEM_PROMPT, user_prompt=user,
                images=[{'path': p} for p in media_paths[:8]],
            )
            if resp is not None:
                raw = resp.data if isinstance(resp.data, dict) else None
                usage = resp
        except Exception:
            raw = None  # 无视觉 provider / 模型超时：保留本地画面分析，不让老板看到 500
    if raw is not None:
        out = _norm(raw, media_count=media_count, base=base, media=media,
                    template_id=tid, ai_mode='model')
        return out, usage
    out = _local_detail(base=base, source_text=source_text, media=media, template_id=tid)
    if usage is None:
        usage = record_mock_usage(club_id, task, user, out)
    return out, usage
