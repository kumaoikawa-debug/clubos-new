# -*- coding: utf-8 -*-
"""图片描述（image captioning）—— 让写作模型不再「盲选图」。

为什么需要（2026-10-07 用户截图实证）：
  模型直出长图方向已验证可行，但模型选图时只拿到 ref + 宽高，**看不到图里是什么**。
  实测它把一张「路线地图截图」选成首屏大图，还选了一张**根本不属于本活动**的雪山攀登照
  （PPT 里混进的其它活动素材）。原因是它在 16 张 photo 里纯随机挑。

做法：用视觉模型（qwen-vl-max，经 generate_json(images=...) 自动路由）给每张照片生成
  一句中文描述 + 一个可用性判断，然后把「ref｜描述｜横竖」一起交给写作模型选图。
  **顺带把地图截图 / 其它活动的照片标成不可用**，写作模型就不会再选它们。

产物是每张图 60 字以内的中文描述，成本远低于让写作模型反复试错。
"""
from __future__ import annotations
import json, os
from pathlib import Path
from typing import Any

from ai_gateway import generate_json

# 视觉模型单次能吃多少张图：太多会稀释注意力、也拖慢；一轮 6 张比较稳
_BATCH = 6

# 提示词刻意写得很短（2026-10-07 实测）：
#   初版把「描述 + 判定标准 + 格式要求」塞了一大段，视觉模型每批都先失败一次、
#   output_tokens 只有 ~200 ——它只照做了「描述」，把最关键的 usable 判断丢掉了。
#   规则越少越长，模型越只做最容易的那部分。
_CAPTION_SYSTEM = """你是户外活动摄影的资料管理员。看图，然后用一句中文写出这张图里**实际有什么**。
你看图不是为了找美图，而是为了让文案同事知道哪张图能配哪句话。"""

_CAPTION_PROMPT = """这些图片来自同一份活动方案 PPT。逐张写一句中文描述（20~45字，只说画面里看得见的东西），
并判断它能不能用作**本次活动**的宣传配图。

本次活动：{ctx}

usable=false 的情形（从紧）：地图/路线图、行程表、纯文字页、logo、二维码、图表、
以及**明显不属于本次活动**的照片（与上述地点、地形、植被、季节明显不符，例如混入的其它线路）。

输出 JSON：{{"items":[{{"ref":"img_01","usable":true,"desc":"..."}}]}}
ref 必须原样抄回。只输出 JSON。"""


def _norm_ref(x: Any) -> str:
    return str(x or '').strip()


def _parse_items(data: Any) -> dict[str, dict[str, Any]]:
    """模型可能返回 {"items":[...]} / 直接 [...] / 每个对象一个键，全���拍平。

    实测坑：视觉模型看图后常先写一段「好的，我来描述这几张图：」再给 JSON。
    网关的 _extract_json 有正则兜底，但对**顶层数组**会失败并抛
    「模型未返回合法JSON」——所以这里先从原始文本里把 JSON 抠出来再解析。
    """
    raw = data
    if isinstance(raw, str):
        raw = _json_from_text(raw)
        if raw is None:
            return {}
    if isinstance(raw, dict):
        for k in ('items', 'images', 'captions', 'data', 'results', 'list'):
            v = raw.get(k)
            if isinstance(v, list) and v:
                raw = v
                break
        else:
            # {"img_01": {...}, "img_02": {...}} 这种形状
            if raw and all(isinstance(v, dict) for v in raw.values()):
                raw = [dict(v, ref=v.get('ref') or k) for k, v in raw.items()]
            else:
                raw = [raw]
    out: dict[str, dict[str, Any]] = {}
    for it in raw or []:
        if not isinstance(it, dict):
            continue
        ref = _norm_ref(it.get('ref') or it.get('id') or it.get('name'))
        if not ref:
            continue
        desc = str(it.get('desc') or it.get('caption') or it.get('description') or '').strip()
        # usable 只认明确的布尔值；模型漏字段时**默认可用**（与旧行为一致，不因描述缺失而全灭）
        usable = it.get('usable')
        if usable is None:
            usable = it.get('ok', it.get('isUsable', True))
        out[ref] = {'usable': bool(usable), 'desc': desc[:80]}
    return out


def _json_from_text(txt: str) -> Any:
    """从可能夹带说明文字的模型输出里抠出 JSON（对象或数组都试）。"""
    import re as _re
    t = str(txt or '').strip()
    t = _re.sub(r'^```(?:json)?|```$', '', t, flags=_re.M).strip()
    for pattern in (r'\{.*\}', r'\[.*\]'):
        m = _re.search(pattern, t, _re.S)
        if not m:
            continue
        try:
            return json.loads(m.group(0))
        except Exception:
            continue
    return None


def _local_file(m: dict[str, Any], static_root: Path | None) -> Path | None:
    """把 media 条目解析成本机上真实存在的图片路径。

    ★ 为什么不能直接用 m['path']（2026-10-07 实测踩坑）：
      线上落库的 path 是**容器内绝对路径** `/workspace/static/uploads/...`，
      在本地机器上永远不存在；曾经直接 read_bytes 抛 FileNotFoundError。
      而 url 是 `/static/uploads/...` 这样的相对路径，拼上项目根目录就是真实文件。
      所以顺序是：url → 本地磁盘（最快，不走网络）→ path → 都没有才下载。
    """
    url = str(m.get('url') or '').strip()
    root = Path(static_root) if static_root else Path(__file__).resolve().parent
    if url.startswith('/static/'):
        p = root / url.lstrip('/')
        if p.is_file():
            return p
    p2 = str(m.get('path') or '').strip()
    if p2:
        p = Path(p2)
        if p.is_file():
            return p
        # 容器路径 → 砍掉 /workspace 前缀再试一次（同一份代码在容器里跑时前缀可能不同）
        if '/workspace/' in p2:
            p = root / p2.split('/workspace/', 1)[1].lstrip('/')
            if p.is_file():
                return p
    return None


def activity_context(master: dict[str, Any]) -> str:
    """从 Activity Master 里摘出「这是什么地方、什么季节、什么活动」。

    没有它，视觉模型无法判断某张照片**是不是这场活动的**（实测它把雪山攀登照
    判成了可用）。所以要明确告诉它比对的基准。
    """
    m = master if isinstance(master, dict) else {}
    bits = [str(m.get('title') or '').strip()]
    for k in ('destination', 'location', 'date'):
        v = str(m.get(k) or '').strip()
        if v and v not in ' '.join(bits):
            bits.append(v)
    pf = m.get('publicFacts') or {}
    if isinstance(pf, dict):
        for k in ('highlight', 'activities', 'bestSeasonForColor', 'ecologicalFeatures'):
            v = pf.get(k)
            if isinstance(v, str) and v.strip():
                bits.append(v.strip())
            elif isinstance(v, list) and v:
                bits.append('、'.join(str(x) for x in v[:6] if x))
    out = '；'.join(b for b in bits if b)[:400]
    return out or '（资料未提供活动信息，只判断是否为地图/表格/logo/纯文字页）'


async def caption_media(club_id: int, master: dict[str, Any],
                        static_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """给 master['media'] 里的 photo 补 desc / usable。返回 ref → {desc, usable}。

    任何失败都**静默降级为「无描述」**：写不出描述只是选图变差，不能让整条生成链路挂掉。
    """
    media = [m for m in (master.get('media') or []) if isinstance(m, dict)]
    photos = [m for m in media
              if (m.get('kind') or 'photo') == 'photo'
              and str(m.get('url') or '').strip()
              and int(m.get('width') or 0) >= 600]
    if not photos:
        return {}

    # 已有描述的复用（同一场活动反复生成不该重复花钱）
    cached: dict[str, dict[str, Any]] = {}
    todo: list[dict[str, Any]] = []
    for m in photos:
        ref = _norm_ref(m.get('ref'))
        if m.get('desc') and m.get('usable') is not None:
            cached[ref] = {'usable': bool(m.get('usable')), 'desc': str(m.get('desc'))[:80]}
        else:
            todo.append(m)
    if not todo:
        return cached

    ctx = activity_context(master)
    for i in range(0, len(todo), _BATCH):
        chunk = todo[i:i + _BATCH]
        payload: list[dict[str, Any]] = []
        batch_media: list[dict[str, Any]] = []
        tmp_files: list[Path] = []
        for m in chunk:
            local = _local_file(m, static_root)
            if local is not None:
                payload.append({'path': str(local)})
                batch_media.append(m)
                continue
            # 磁盘上没有（远程库 / 换了机器）→ 下载到临时目录再喂给模型
            u = str(m.get('url') or '')
            if not u:
                continue
            try:
                import httpx
                full = u if u.startswith('http') else 'http://127.0.0.1:8000' + u
                r = httpx.get(full, timeout=30, follow_redirects=True)
                if r.status_code != 200 or not r.content:
                    continue
                tmp = Path('/tmp') / ('clubos_cap_%s_%s' % (os.getpid(), _norm_ref(m.get('ref'))))
                tmp.write_bytes(r.content)
                tmp_files.append(tmp)
                payload.append({'path': str(tmp)})
                batch_media.append(m)
            except Exception:
                continue
        if not payload:
            continue
        prompt = ('请为这 %d 张图逐张写描述并判断可用性。图片与 ref 的对应顺序如下（务必原样使用这些 ref）：\n%s'
                 % (len(payload), '\n'.join(_norm_ref(m.get('ref')) for m in batch_media)))
        prompt = _CAPTION_PROMPT.replace('{ctx}', ctx) + '\n' + prompt
        gw = None
        # 视觉模型比文本模型更容易不按 JSON 格式回话；失败重试一次并追加更硬的格式要求
        for attempt in range(2):
            try:
                gw = await generate_json(club_id=club_id, task_type='detail',
                                         system_prompt=_CAPTION_SYSTEM,
                                         user_prompt=prompt if not attempt else prompt + '\n\n只输出 JSON，不要任何解释文字。',
                                         images=payload)
            except Exception:
                gw = None
            if gw:
                break
        for t in tmp_files:
            try:
                t.unlink()
            except Exception:
                pass
        if not gw:
            continue
        parsed = _parse_items(gw.data)
        # ref 全军覆没但条目数对得上 → 按顺序对齐（模型偶尔会用 index/序号代替 ref）
        if not any(_norm_ref(m.get('ref')) in parsed for m in batch_media):
            seq = list(parsed.values())
            if len(seq) == len(batch_media):
                for m, cap in zip(batch_media, seq):
                    parsed[_norm_ref(m.get('ref'))] = cap
        for m in batch_media:
            ref = _norm_ref(m.get('ref'))
            if ref in parsed:
                cached[ref] = parsed[ref]
                # 写回 master：同一活动的后续生成直接复用，不再重复调用视觉模型
                m['desc'] = parsed[ref]['desc']
                m['usable'] = parsed[ref]['usable']
    return cached


def caption_lines(master: dict[str, Any], caps: dict[str, dict[str, Any]]) -> list[str]:
    """把描述整理成给写作模型看的清单行。不可用的图**不列出**，从源头断掉选错图的可能。"""
    lines: list[str] = []
    for m in master.get('media') or []:
        if not isinstance(m, dict) or (m.get('kind') or 'photo') != 'photo':
            continue
        ref = _norm_ref(m.get('ref'))
        w, h = int(m.get('width') or 0), int(m.get('height') or 0)
        if w < 600:
            continue
        cap = caps.get(ref) or {}
        usable = cap.get('usable', m.get('usable', True))
        desc = str(cap.get('desc') or m.get('desc') or '').strip()
        if not usable:
            continue          # 地图截图 / 别的活动的照片：不给模型看，它就不会选
        orient = '竖图' if h >= w else '横图'
        lines.append(f'{ref}｜{orient}｜{desc or "（暂无描述）"}')
    return lines


def persist_captions(master: dict[str, Any], caps: dict[str, dict[str, Any]]) -> None:
    """把描述写回 media 条目（调用方负责落库 master_json）。"""
    for m in master.get('media') or []:
        if not isinstance(m, dict):
            continue
        ref = _norm_ref(m.get('ref'))
        cap = caps.get(ref)
        if cap:
            m['desc'] = cap.get('desc') or m.get('desc') or ''
            m['usable'] = bool(cap.get('usable'))
    # 清理临时下载的文件
    for f in Path('/tmp').glob('clubos_cap_*'):
        try:
            f.unlink()
        except Exception:
            pass