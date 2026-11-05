# -*- coding: utf-8 -*-
"""通义万相（DashScope）文生图能力 —— 复用平台后台已配的 DashScope Key。

为活动一键生成「主视觉海报 / 封面」，解决「没上传照片就撑不起图文」的短板：
过去 图文 完全依赖用户上传的实拍图，资料薄、无图时详情/长图/宣传区都很空；
接上出图后，即使零上传也能生成一张符合活动调性的户外主视觉。

设计要点（对照 ai_gateway）：
· 复用 `ai_gateway._providers()` 拿到的 DashScope Key（与 Qwen 文本模型同一把 key，无需新申请）。
· 复用 `ai_gateway._httpx_client()`：默认忽略 HTTP_PROXY/HTTPS_PROXY，避免 502 代理吞请求。
· 通义万相是异步任务：POST 拿到 task_id → 轮询 /api/v1/tasks/{id} 直到 SUCCEEDED → 取结果图 URL → 下载落盘。
· 落盘到 static/uploads/{club_id}/hero/，返回相对 static/uploads 的路径（与 activities.cover 同口径）。
"""
import os
import json
import asyncio
import random
import uuid

import httpx

from ai_gateway import _providers, _httpx_client, _explain

DASHSCOPE_IMG_ENDPOINT = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis'
DASHSCOPE_TASK_ENDPOINT = 'https://dashscope.aliyuncs.com/api/v1/tasks'

# 户外主视觉的通用负向提示词：挡掉 AI 水印、文字、畸形、过曝，保证成品能直接做封面。
DEFAULT_NEGATIVE = (
    'watermark, text, logo, words, letters, signature, deformed, extra limbs, '
    'bad anatomy, low resolution, overexposed, oversaturated, neon, flat illustration'
)


def _dashscope_key() -> str:
    """取 DashScope Key：优先 preset=qwen 或 base_url 含 dashscope 的供应商，否则取主用 key。"""
    providers = _providers()
    for p in providers:
        preset = (p.preset or '').lower()
        if preset == 'qwen' or 'dashscope' in (p.base_url or '').lower():
            if p.api_key:
                return p.api_key
    if providers and providers[0].api_key:
        return providers[0].api_key
    return ''


def build_hero_prompt(master: dict, style_name: str = '') -> str:
    """根据活动资料 + 风格家族，拼一段中文户外主视觉提示词（真实信息只在资料里有时才写）。"""
    m = master if isinstance(master, dict) else {}
    title = (m.get('title') or '').strip()
    loc = (m.get('location') or '').strip()
    theme = (m.get('theme') or m.get('category') or '').strip()
    season = (m.get('season') or '').strip()

    scene = title or loc or theme or '户外旅行'
    env_hint = ''
    if loc:
        env_hint += '地点：' + loc + '。'
    if season:
        env_hint += '季节：' + season + '。'
    if style_name:
        env_hint += '版面调性：' + style_name + '。'

    return (
        '一张户外俱乐部活动的主视觉海报照片，主题是「' + scene + '」。'
        + (env_hint + ' ' if env_hint else '')
        + '电影感、克制、真实摄影质感，主视觉是人与自然的关系：广阔的自然地貌 + 一个微小的人物剪影或营地，'
        '留白充足，上方与下方预留放标题的空间。低饱和、高级户外杂志编辑风格，'
        '配色以深森林绿、墨黑、暖陶土金为主，柔和晨昏光线。4k、高细节、photorealistic。'
        '不要任何文字、不要 logo、不要水印。'
    )


async def generate_hero(prompt: str, *, negative_prompt: str = '', size: str = '1024*1280',
                        n: int = 1, timeout: float = 150) -> list[str]:
    """生成主视觉，返回结果图 URL 列表（DashScope 临时 CDN 地址，需尽快下载落盘）。"""
    api_key = _dashscope_key()
    if not api_key:
        raise RuntimeError(
            '未配置 DashScope / 通义万相 的 API Key——请在总平台后台「AI 接入」里填写 Qwen/DashScope 的 Key。')
    headers = {
        'Authorization': 'Bearer ' + api_key,
        'Content-Type': 'application/json',
        'X-DashScope-Async': 'enable',
    }
    body = {
        'model': 'wanx2.1-t2i-plus',
        'input': {
            'prompt': prompt,
            'negative_prompt': negative_prompt or DEFAULT_NEGATIVE,
        },
        # 注意：wanx2.1 校验 seed ∈ [0, 4294967290]，-1 会被 400 拒绝；用区间内随机种子保留多样性。
        'parameters': {'size': size, 'n': n, 'prompt_extend': True,
                       'seed': random.randint(0, 4294967290)},
    }
    async with _httpx_client(timeout) as client:
        try:
            r = await client.post(DASHSCOPE_IMG_ENDPOINT, headers=headers, json=body)
        except Exception as e:  # 网络层异常翻译成可读错误
            raise RuntimeError('通义万相请求失败：' + _explain(e))
        if r.status_code >= 400:
            raise RuntimeError('通义万相请求失败 %s: %s' % (r.status_code, r.text[:400]))
        try:
            data = r.json()
        except Exception:
            raise RuntimeError('通义万相返回非 JSON：' + r.text[:400])
        task_id = (data.get('output') or {}).get('task_id')
        if not task_id:
            raise RuntimeError('通义万相未返回 task_id：' + json.dumps(data, ensure_ascii=False)[:400])

        # 轮询任务（最多 ~5 分钟）
        for _ in range(150):
            await asyncio.sleep(2)
            try:
                tr = await client.get(DASHSCOPE_TASK_ENDPOINT + '/' + task_id,
                                     headers={'Authorization': 'Bearer ' + api_key})
            except Exception as e:
                raise RuntimeError('通义万相轮询失败：' + _explain(e))
            if tr.status_code >= 400:
                raise RuntimeError('通义万相轮询失败 %s: %s' % (tr.status_code, tr.text[:400]))
            td = tr.json()
            st = (td.get('output') or {}).get('task_status')
            if st == 'SUCCEEDED':
                results = (td.get('output') or {}).get('results') or []
                urls = [x.get('url') for x in results if x.get('url')]
                if not urls:
                    raise RuntimeError('通义万相任务成功但未返回图片地址：'
                                       + json.dumps(td.get('output') or {}, ensure_ascii=False)[:400])
                return urls
            if st == 'FAILED':
                raise RuntimeError('通义万相生成失败：'
                                  + json.dumps(td.get('output') or {}, ensure_ascii=False)[:400])
        raise RuntimeError('通义万相生成超时（>5 分钟）')


async def save_hero_image(club_id: int, image_url: str, root: str) -> str:
    """下载生成图并落盘到 static/uploads/{club_id}/hero/，返回相对 static/uploads 的路径。"""
    out_dir = os.path.join(root, 'static', 'uploads', str(club_id), 'hero')
    os.makedirs(out_dir, exist_ok=True)
    fname = 'hero_%s.png' % uuid.uuid4().hex
    out_path = os.path.join(out_dir, fname)
    async with _httpx_client(60) as client:
        try:
            r = await client.get(image_url)
        except Exception as e:
            raise RuntimeError('下载生成图失败：' + _explain(e))
        if r.status_code >= 400:
            raise RuntimeError('下载生成图失败 %s' % r.status_code)
        with open(out_path, 'wb') as f:
            f.write(r.content)
    return os.path.join(str(club_id), 'hero', fname)
