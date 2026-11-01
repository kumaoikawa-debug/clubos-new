# -*- coding: utf-8 -*-
"""图内文字识别（OCR via 视觉模型）。

为什么需要：
  很多活动资料把文字**烤进了图片**（微信长图推文 / 图片型海报 / 设计稿式方案）。
  静态抓取与 headless 渲染都只能拿到图的字节，拿不到图里写了什么 —— 于是「贴一条微信长图」
  生成出来的详情完全没有活动要素（日期 / 价格 / 地点）。这里用视觉模型（qwen-vl-max，
  经 ai_gateway.generate_json 的视觉路由）把每张图里的字读出来，再并入正文交给写作模型。

与 image_caption 的区别：caption 是「描述画面里有什么」，OCR 是「抄出图里所有文字」。
两者都走同一条视觉通道，但提示词与产出格式不同。

任何失败都**静默降级为 ''** —— 读不出图内文字只是内容少一点，不能让整条生成挂掉。
"""
from __future__ import annotations
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from PIL import Image
from ai_gateway import generate_json

# 视觉模型单次能吃多少张图：太多会稀释注意力、也拖慢；一轮 6 张比较稳
_BATCH = 6
# 送视觉模型前统一降采样：一张 4000×6000 的原图 base64 后几 MB，六张一批常超时，
# 而 OCR 只需要「看得出字」，缩到最长边 1280、JPEG q82 后字仍清晰，token 与耗时降一个量级。
_SHRINK_MAX = 1280
_SHRINK_Q = 82


_OCR_SYSTEM = (
    "你是一位严谨的中文 OCR 校对员。下面是同一篇活动推文 / 海报按顺序排列的若干页图片。"
    "你的任务只有一件：把每张图里**所有可见的中文 / 数字 / 英文文字**原样抄出来。"
)

_OCR_PROMPT = (
    "逐张、按阅读顺序，把每张图里**所有可见的文字**原样抄出来：保留原有的段落与换行，**不要改写、"
    "不要补全、不要总结、不要添加任何解释**。如果某张图是纯照片、没有任何文字，就输出空字符串。\n\n"
    "图片与 ref 的对应顺序如下（务必原样使用这些 ref）：\n{pairs}\n\n"
    "只输出 JSON：{\"pages\":[{\"ref\":\"img_01\",\"text\":\"...\"}]}，不要任何解释文字。"
)


def _shrink(path: Path) -> Path:
    """把图片压到最长边 _SHRINK_MAX 再喂模型；任何失败都原样返回。"""
    try:
        with Image.open(path) as im:
            im = im.convert('RGB')
            w, h = im.size
            scale = _SHRINK_MAX_SAFE(w, h)
            if scale < 1:
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
            out = Path('/tmp') / ('clubos_ocr_%s_%s.jpg' % (os.getpid(), abs(hash(str(path))) % 10 ** 8))
            im.save(out, 'JPEG', quality=_SHRINK_Q, optimize=True)
            return out
    except Exception:
        return path


def _SHRINK_MAX_SAFE(w: int, h: int) -> float:
    return _SHRINK_MAX / float(max(w, h) or 1)


def _json_from_text(txt: str) -> Any:
    """从可能夹带说明文字的模型输出里抠出 JSON（对象或数组都试）。"""
    t = str(txt or '').strip()
    t = re.sub(r'^```(?:json)?|```$', '', t, flags=re.M).strip()
    for pattern in (r'\{.*\}', r'\[.*\]'):
        m = re.search(pattern, t, re.S)
        if not m:
            continue
        try:
            return json.loads(m.group(0))
        except Exception:
            continue
    return None


def _parse(data: Any) -> dict[str, str]:
    """模型可能返回 {"pages":[...]} / 直接 [...] / 每个对象一个键，全拍平。"""
    raw = data
    if isinstance(raw, str):
        raw = _json_from_text(raw)
        if raw is None:
            return {}
    if isinstance(raw, dict):
        for k in ('pages', 'items', 'images', 'data', 'results', 'list'):
            v = raw.get(k)
            if isinstance(v, list) and v:
                raw = v
                break
        else:
            raw = [raw]
    out: dict[str, str] = {}
    for it in raw or []:
        if not isinstance(it, dict):
            continue
        ref = str(it.get('ref') or it.get('id') or it.get('name') or '').strip()
        text = str(it.get('text') or it.get('content') or it.get('ocr') or '').strip()
        if ref and text:
            out[ref] = text
    return out


async def ocr_image_text(club_id: int, images: list[Path], max_images: int = 18) -> str:
    """对一组图做 OCR，返回按阅读顺序合并后的图内文字（空串 = 没读到 / 失败 / 无图）。

    调用方负责判断「该不该做 OCR」（一般只在链接正文偏薄、但配图很多时调用，
    例如微信长图推文）。本函数只管读图，任何异常都返回空串。
    """
    imgs = [p for p in (images or []) if isinstance(p, Path) and p.is_file()][:max_images]
    if not imgs:
        return ''
    # 全局 ref（跨批不冲突）：img_01 … img_N
    refs = ['img_%02d' % (i + 1) for i in range(len(imgs))]
    chunks = [list(zip(refs[i:i + _BATCH], imgs[i:i + _BATCH]))
              for i in range(0, len(imgs), _BATCH)]
    concurrency = max(1, int(os.getenv('AI_OCR_CONCURRENCY', '3') or 3))

    collected: dict[str, str] = {}
    tmp_files: list[Path] = []

    async def _one(chunk) -> None:
        payload: list[dict[str, Any]] = []
        for ref, p in chunk:
            sh = _shrink(p)
            if sh is not p:
                tmp_files.append(sh)
            payload.append({'path': str(sh)})
        if not payload:
            return
        pairs = '\n'.join('%s = 第 %d 张' % (r, i + 1) for i, (r, _) in enumerate(chunk))
        prompt = _OCR_PROMPT.format(pairs=pairs)
        gw = None
        # 视觉模型比文本模型更容易不按 JSON 格式回话；失败重试一次并追加更硬的格式要求
        for attempt in range(2):
            try:
                gw = await generate_json(
                    club_id=club_id, task_type='detail',
                    system_prompt=_OCR_SYSTEM,
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
        for ref, text in _parse(gw.data).items():
            collected[ref] = text

    for i in range(0, len(chunks), concurrency):
        await asyncio.gather(*[_one(c) for c in chunks[i:i + concurrency]],
                             return_exceptions=True)
    # 按阅读顺序合并
    merged = '\n'.join(collected.get(r, '') for r in refs if collected.get(r))
    return merged.strip()
