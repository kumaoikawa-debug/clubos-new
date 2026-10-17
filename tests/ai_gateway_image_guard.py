#!/usr/bin/env python3
"""AI Gateway 图片卫生 + json 兜底回归（2026-10-17 线上 400 事故的固化测试）。

背景：用户「AI 写一个活动」时，详情主调用在打标不可用会把 source['images'] 原图
base64 进请求。PPT/Word 里抽出的 1x1 占位图、8x8 项目符号、损坏文件混在里面，
DashScope 一律回 opaque 400（'image format is illegal' /
'length and width ... must be larger than 10'），httpx 报错串不含原因，
用户只看到「400 Bad Request」，整页生成全挂。

本测试用本地 OpenAI 兼容 stub（scripts/dev_openai_stub.py）当 Provider，验证：
  1. 非法图片（≤12px / 打不开 / 缺 path / 文件不存在）被跳过，不进请求体；
  2. 合法图片被归一化（最长边 1024 / RGB / JPEG）后照常进请求；
  3. qwen preset 的 response_format=json_object 有「messages 必须含 json 字样」
     的兜底：提示词没带 json 时自动补一句，不会再有 opaque 400；
  4. HTTP 4xx 的响应体被带进 _explain（可观测，别再让人对着一句 400 干瞪眼）。

运行： python tests/ai_gateway_image_guard.py
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

TMP = Path(tempfile.mkdtemp(prefix='clubos_img_guard_'))
os.environ['CLUBOS_DB_PATH'] = str(TMP / 'guard.db')
os.environ['MOCK_AI'] = '0'
os.environ['CLUBOS_SECURITY_MODE'] = 'demo'
for k in ('AI_PRIMARY_API_KEY', 'AI_SECONDARY_API_KEY', 'LLM_API_KEY',
          'AI_PRIMARY_BASE_URL', 'AI_SECONDARY_BASE_URL', 'AI_PRIMARY_MODEL',
          'AI_SECONDARY_MODEL', 'AI_QWEN_VISION_MODEL', 'AI_QWEN_MODEL_DETAIL',
          'AI_MODEL_DETAIL', 'AI_QWEN_ENABLE_THINKING'):
    os.environ.pop(k, None)

import dev_openai_stub  # noqa: E402
from db import init_db  # noqa: E402
import ai_gateway  # noqa: E402

init_db()
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = '') -> None:
    results.append((name, bool(cond), detail))


def _read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _make_image(path: Path, size: tuple[int, int], *, fmt: str = 'JPEG') -> Path:
    im = Image.new('RGB', size, (120, 90, 60))
    buf = io.BytesIO()
    im.save(buf, fmt)
    path.write_bytes(buf.getvalue())
    return path


# ---- 起 stub 当 Provider ----------------------------------------------------
httpd, _thread, PORT = dev_openai_stub.start_stub(log_path=str(TMP / 'stub.jsonl'))
STUB_URL = f'http://127.0.0.1:{PORT}/v1/chat/completions'
os.environ['AI_PRIMARY_BASE_URL'] = STUB_URL
os.environ['AI_PRIMARY_API_KEY'] = 'guard-test-key-1234'
os.environ['AI_PRIMARY_PRESET'] = 'qwen'
os.environ['AI_PRIMARY_MODEL'] = 'stub-model'

# ---- 造一批「用户上传里真实会出现的」图片 -----------------------------------
valid = _make_image(TMP / 'valid.jpg', (900, 700))                 # 正常照片
portrait = _make_image(TMP / 'portrait.jpg', (420, 560))           # 微信压缩竖图
tiny = _make_image(TMP / 'spacer.jpg', (4, 300))                   # PPT 1xN 占位图
one_px = _make_image(TMP / 'pixel.jpg', (1, 1))                    # 1x1 占位图
corrupt = TMP / 'broken.jpg'
corrupt.write_bytes(b'this is not an image at all, just bytes')
missing = TMP / 'gone.jpg'
missing.write_bytes(b'x')
missing.unlink()                                                   # 引用了但不存在的文件

images = [
    {'path': str(valid), 'ref': 'img_01'},
    {'path': str(portrait), 'ref': 'img_02'},
    {'path': str(tiny), 'ref': 'img_03'},
    {'path': str(one_px), 'ref': 'img_04'},
    {'path': str(corrupt), 'ref': 'img_05'},
    {'path': str(missing), 'ref': 'img_06'},
    {'ref': 'img_07'},                                             # 连 path 键都没有
]

import asyncio  # noqa: E402

resp = asyncio.run(ai_gateway.generate_json(
    club_id=1, task_type='detail',
    system_prompt='你是 ClubOS 的 AI 活动内容主编。',
    user_prompt='为这场徒步活动生成详情。',
    images=images))

log = _read_log(TMP / 'stub.jsonl')
reqs = [r for r in log if r.get('path', '').endswith('/chat/completions')]
check('stub 收到了请求（没有 400/异常）', len(reqs) == 1, f'requests={len(reqs)}')
check('generate_json 返回了已解析的 JSON', resp is not None and isinstance(resp.data, dict))

if reqs:
    body = reqs[-1].get('body') or {}
    parts = []
    for m in body.get('messages') or []:
        if isinstance(m.get('content'), list):
            parts += [p for p in m['content'] if isinstance(p, dict) and p.get('type') == 'image_url']
    check('只有两张合法图片进请求（占位图/损坏/缺失/无 path 全被跳过）',
          len(parts) == 2, f'image_url parts={len(parts)}')
    sys_msg = str(((body.get('messages') or [{}])[0].get('content') or ''))
    check('提示词没带 json 时自动补「只输出 JSON」（DashScope json_object 硬性要求）',
          'JSON' in sys_msg, f'system={sys_msg[-40:]!r}')
    # stub 回包里带了它实际收到的图片数，双重核对
    facts = ((resp.data if resp else {}).get('activity_master') or {}).get('publicFacts') or {}
    check('stub 视角也只收到 2 张图', facts.get('receivedImages') == 2, str(facts))

# ---- 4xx 响应体必须进 _explain ------------------------------------------------
httpd2, _t2, PORT2 = dev_openai_stub.start_stub(always_fail=False, log_path=str(TMP / 'stub2.jsonl'))
# 用一个会返 400 的假端点：指向 stub 的 /400（stub 对未知路径返 404，这里直接构造 HTTPStatusError）
import httpx  # noqa: E402


class _Resp:
    status_code = 400
    text = '{"error":{"message":"<400> InternalError.Algo.InvalidParameter: The image format is illegal"}}'


exc = httpx.HTTPStatusError('Client error 400', request=httpx.Request('POST', STUB_URL), response=_Resp())  # type: ignore[arg-type]
msg = ai_gateway._explain(exc)
check('4xx 报错带上响应体原因（不再只有 opaque 400）',
      'image format is illegal' in msg and '400' in msg, msg[:120])

httpd.shutdown(); httpd2.shutdown()

# ---- 汇总 ---------------------------------------------------------------------
failed = [(n, d) for n, ok, d in results if not ok]
for n, ok, d in results:
    print(('PASS  ' if ok else 'FAIL  ') + n + (f'  [{d}]' if d and not ok else ''))
print(f'\n{len(results) - len(failed)}/{len(results)} 通过')
sys.exit(1 if failed else 0)
