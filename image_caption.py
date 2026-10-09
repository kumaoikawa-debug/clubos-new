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
from urllib.parse import unquote
from PIL import Image
import json, os, re
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
给 2~4 个主题标签，并判断它能不能用作**本次活动**的宣传配图。

本次活动的约定主题标签（优先从这里选，确实都不是时才自造）：
住宿 / 餐食 / 唐卡与绘画 / 人像与藏装 / 雪山与山景 / 云海与天空 / 森林与秋色 / 徒步与队伍 /
手作与制作 / 动物 / 交通工具 / 建筑与寺院 / 文字海报 / 其它

tags 的用途：写作模型要靠它把「讲什么」的段落配「是什么」的图（讲酒店的段落只能配住宿图，
讲唐卡的只能配唐卡图）。所以标签必须写画面里**真实存在**的东西，不要写「美景」「氛围」这类空词。

本次活动：{ctx}

usable=false 的情形（从紧）：地图/路线图、行程表、纯文字页、logo、二维码、图表、
以及**明显不属于本次活动**的照片（与上述地点、地形、植被、季节明显不符，例如混入的其它线路）。

输出 JSON：{{"items":[{{"ref":"img_01","usable":true,"tags":["住宿","建筑与寺院"],"desc":"..."}}]}}
ref 必须原样抄回。只输出 JSON。"""

# 送进视觉模型前统一降采样：一张 4000×6000 的手机原图 base64 后有几 MB，
# 六张一批经常把请求拖到十几秒甚至超时，而打标只需要「看得出画面里是什么」。
# 缩到最长边 1024、JPEG q82 后视觉判断质量不变，token 与耗时都降一个量级。
_SHRINK_MAX = 1024
_SHRINK_Q = 82


def _shrink(path: Path) -> Path:
    """把图片压到最长边 _SHRINK_MAX 再喂模型；任何失败都原样返回。"""
    try:
        with Image.open(path) as im:
            im = im.convert('RGB')
            w, h = im.size
            scale = _SHRINK_MAX / float(max(w, h) or 1)
            if scale < 1:
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
            out = Path('/tmp') / ('clubos_cap_s_%s_%s.jpg' % (os.getpid(), abs(hash(str(path))) % 10 ** 8))
            im.save(out, 'JPEG', quality=_SHRINK_Q, optimize=True)
            return out
    except Exception:
        return path


def _norm_ref(x: Any) -> str:
    return str(x or '').strip()


def _parse_tags(v: Any) -> list[str]:
    """tags 归一：数组 / 「住宿、餐食」这种顿号串 / 单个字符串，都收成 2~4 个短标签。"""
    raw: list[str] = []
    if isinstance(v, list):
        raw = [str(x) for x in v]
    elif isinstance(v, str):
        raw = [p for p in re.split(r'[、,，/\|]+', v)]
    out: list[str] = []
    for x in raw:
        t = str(x or '').strip()
        if t and t not in out and len(t) <= 10:
            out.append(t)
    return out[:4]


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
        tags = _parse_tags(it.get('tags') or it.get('labels') or it.get('topics'))
        # usable 只认明确的布尔值；模型漏字段时**默认可用**（与旧行为一致，不因描述缺失而全灭）
        usable = it.get('usable')
        if usable is None:
            usable = it.get('ok', it.get('isUsable', True))
        out[ref] = {'usable': bool(usable), 'desc': desc[:80], 'tags': tags}
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


def _static_paths(root: Path, url: str) -> list[Path]:
    """把 /static/... 的相对 url 解析成候选本地路径。

    ★ 2026-10-09 修掉一个「两处约定不一致」的老坑：_ensure_media_meta 把 static_root
      当成 **static 目录**（root + 'uploads/...'），_local_file 却当成 **项目根**
      （root + 'static/uploads/...'）。同一个参数两种含义，调用方无论传哪个都会有一半
      功能坏掉；传错时 _local_file 返回 None，视觉打标就整批静默降级（实测：act39 的
      28 张照片一张都没打上标签，对图逻辑随之失效）。这里改为「两种都试」，谁的组合
      真实存在就用谁，调用方传项目根或 static 目录都能工作。
    """
    rel = url[len('/static/'):] if url.startswith('/static/') else url.lstrip('/')
    cands = [root / rel]
    if rel.startswith('static/'):
        cands.append(root / rel[len('static/'):])
    else:
        cands.append(root / 'static' / rel)
    cands.append(root.parent / rel)
    return cands


def _local_file(m: dict[str, Any], static_root: Path | None) -> Path | None:
    """把 media 条目解析成本机上真实存在的图片路径。

    ★ 为什么不能直接用 m['path']（2026-10-07 实测踩坑）：
      线上落库的 path 是**容器内绝对路径** `/workspace/static/uploads/...`，
      在本地机器上永远不存在；曾经直接 read_bytes 抛 FileNotFoundError。
      而 url 是 `/static/uploads/...` 这样的相对路径，拼上项目目录就是真实文件。
      所以顺序是：url → 本地磁盘（最快，不走网络）→ path → 都没有才下载。
    """
    url = str(m.get('url') or '').strip()
    root = Path(static_root) if static_root else Path(__file__).resolve().parent
    if url.startswith('/static/'):
        for p in _static_paths(root, url):
            if p.is_file():
                return p
    p2 = str(m.get('path') or '').strip()
    if p2:
        p = Path(p2)
        if p.is_file():
            return p
        # 容器路径 → 砍掉 /workspace 前缀再试一次（同一份代码在容器里跑时前缀可能不同）
        if '/workspace/' in p2:
            tail = p2.split('/workspace/', 1)[1]
            for cand in _static_paths(root, '/' + tail):
                if cand.is_file():
                    return cand
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


def _ensure_media_meta(media: list[dict[str, Any]], static_root: Path | None = None) -> None:
    """给缺 metam 的照片补 width / height / orientation / kind（就地改写）。

    ★ 2026-10-08 实测：早期入库的活动（如 act1）media 只有 {ref,name,url}，
      没有尺寸也没有 kind。而 caption_media 用 `width >= 600` 过滤「真正的照片」来
      排除小图标 —— 缺 size 就等于全被判为"不是照片"，结果**整张长图一张图都没有**。
      用户说「AI 生成能力不行」，有一部分根因其实在这里：不是模型不选图，是没图可选。
      所以这里先把元数据补齐（读磁盘，成本很低），过滤条件本身保持不变 ——
      小图/装饰图照样会被剔除。
    """
    # 默认按「本文件所在目录 / static」定位，不依赖进程 cwd（曾被 cwd 坑过）
    root = Path(static_root) if static_root else Path(__file__).resolve().parent / 'static'
    for m in media:
        if int(m.get('width') or 0) > 0:
            continue
        u = str(m.get('url') or '').strip()
        if not u.startswith('/static/'):
            continue
        try:
            rel = unquote(u)
            fp = next((p for p in _static_paths(root, rel) if p.is_file()), None)
            if fp is None:
                continue
            with Image.open(fp) as im:
                w, h = im.size
            m['width'], m['height'] = int(w), int(h)
            m.setdefault('orientation', 'landscape' if w >= h else 'portrait')
            # 文件名里带 logo / 标识 的，别当照片（否则可能被选成长图首屏大图）
            nm = str(m.get('name') or '').lower()
            m.setdefault('kind', 'logo' if ('logo' in nm or '标识' in nm) else 'photo')
        except Exception:
            continue


async def caption_media(club_id: int, master: dict[str, Any],
                        static_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """给 master['media'] 里的 photo 补 desc / usable / tags。返回 ref → {desc, usable, tags}。

    任何失败都**静默降级为「无描述」**：写不出描述只是选图变差，不能让整条生成链路挂掉。
    """
    media = [m for m in (master.get('media') or []) if isinstance(m, dict)]
    _ensure_media_meta(media, static_root)          # ★ 缺尺寸的历史数据先补齐
    # ★ 阈值曾经是「宽 >= 600」，把「竖构图但宽度不到 600」的正常照片整批漏掉
    #   （act39 的 6 张 538×744 / 387×547… 就是这么被跳过的：既不进清单、也拿不到描述，
    #   最后只能被模型凭 ref 乱塞进正文哪一段）。改判「短边 >= 240」——
    #   这个尺寸以下才是 logo / 二维码 / 图标这类不该当配图的东西。
    def _big_enough(m: dict[str, Any]) -> bool:
        w = int(m.get('width') or 0)
        h = int(m.get('height') or 0)
        if w and h:
            return min(w, h) >= 240
        return max(w, h) >= 600

    photos = [m for m in media
              if (m.get('kind') or 'photo') == 'photo'
              and str(m.get('url') or '').strip()
              and _big_enough(m)]
    if not photos:
        return {}

    # 已有描述的复用（同一场活动反复生成不该重复花钱）
    cached: dict[str, dict[str, Any]] = {}
    todo: list[dict[str, Any]] = []
    for m in photos:
        ref = _norm_ref(m.get('ref'))
        if m.get('desc') and m.get('usable') is not None:
            cached[ref] = {'usable': bool(m.get('usable')), 'desc': str(m.get('desc'))[:80],
                           'tags': _parse_tags(m.get('tags'))}
        else:
            todo.append(m)
    if not todo:
        return cached

    ctx = activity_context(master)
    # 按批并发：一批 6 张图，38 张照片串行要跑 7 轮、每轮十几秒，会把整条生成拖到几分钟。
    # 视觉调用之间互不依赖，并发跑（默认 4 路）耗时降到约 1/3，失败照样单批降级。
    import asyncio

    chunks = [todo[i:i + _BATCH] for i in range(0, len(todo), _BATCH)]
    concurrency = max(1, int(os.getenv('AI_CAPTION_CONCURRENCY', '4') or 4))

    async def _one(chunk: list[dict[str, Any]]) -> None:
        payload: list[dict[str, Any]] = []
        batch_media: list[dict[str, Any]] = []
        tmp_files: list[Path] = []
        for m in chunk:
            local = _local_file(m, static_root)
            if local is not None:
                sh = _shrink(local)
                if sh is not local:
                    tmp_files.append(sh)
                payload.append({'path': str(sh)})
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
                payload.append({'path': str(_shrink(tmp))})
                batch_media.append(m)
            except Exception:
                continue
        if not payload:
            return
        prompt = ('请为这 %d 张图逐张写描述、给主题标签并判断可用性。图片与 ref 的对应顺序如下（务必原样使用这些 ref）：\n%s'
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
            return
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
                if parsed[ref].get('tags'):
                    m['tags'] = parsed[ref]['tags']

    for i in range(0, len(chunks), concurrency):
        await asyncio.gather(*[_one(c) for c in chunks[i:i + concurrency]],
                             return_exceptions=True)
    return cached


def caption_lines(master: dict[str, Any], caps: dict[str, dict[str, Any]]) -> list[str]:
    """把描述整理成给写作模型看的清单行。不可用的图**不列出**，从源头断掉选错图的可能。"""
    # ★ 这里也要补一次元数据：caption_lines 是「出图清单」的公共入口，
    #   只要有 width 缺失的图就会整批被下面的 `w < 600` 过滤掉 —— 而元数据缺失
    #   是历史数据常有的事（act1 的 18 张图全都没有 width，结果长图一张图都没有）。
    _ensure_media_meta([m for m in (master.get('media') or []) if isinstance(m, dict)])
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
        tags = _parse_tags(cap.get('tags') or m.get('tags'))
        head = ((' / '.join(tags)) + '｜') if tags else ''
        # 行格式：img_07｜竖图｜住宿 / 建筑与寺院｜雪山脚下的藏式酒店客房，白墙木窗
        # 主题标签放在描述前面：模型选图主要靠「主题对不对」，描述是它写图注、核对细节用的。
        lines.append(f'{ref}｜{orient}｜{head}{desc or "（暂无描述）"}')
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
            if cap.get('tags'):
                m['tags'] = cap['tags']
    # 清理临时下载的文件
    for f in Path('/tmp').glob('clubos_cap_*'):
        try:
            f.unlink()
        except Exception:
            pass