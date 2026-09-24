#!/usr/bin/env python3
"""本地 OpenAI 兼容 stub —— 仅用于 ClubOS AI Gateway 的「链路/契约」验证与离线联调。

⚠️ 它不是模型，不产生任何真实创作内容。它按固定规则回一份结构合法的 JSON，
   用来验证这些**工程契约**是否成立：

   1. Authorization: Bearer <key> 是否被正确透传（不同 Provider 用不同 key）；
   2. 请求体是否合规（model / messages / temperature / image_url）；
   3. 响应解析是否健壮（这里故意用 Markdown ```json 代码块包裹，逼 Gateway 走 _extract_json 兜底）；
   4. usage 是否被正确记账（ai_usage_records 的 provider / model / tokens）；
   5. 视觉路由是否生效（带图任务必须落到具备视觉能力的 Provider）；
   6. 主 Provider 失败时是否按平台开关 failover 到备用 Provider；
   7. 缺少视觉能力时是否 fail-closed（明确报错而不是静默丢图）。

   **禁止**用它得出的内容去评价"真实模型的生成质量"——
   那是 scripts/compare_mock_vs_live.py 的职责，需要真实 API Key。

用法：
   python scripts/dev_openai_stub.py --port 8799              # 前台常驻
   python scripts/dev_openai_stub.py --port 8799 --always-fail  # 永返 500（测 failover）
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_LOG = Path(os.getenv('CLUBOS_STUB_LOG', '/tmp/clubos_stub_log.jsonl'))


def _sanitize(req: dict) -> dict:
    """把 base64 图片替换成占位符，避免日志被撑爆。"""
    out = dict(req)
    msgs = []
    for m in req.get('messages') or []:
        m2 = dict(m)
        content = m2.get('content')
        if isinstance(content, list):
            new_content = []
            for part in content:
                if isinstance(part, dict) and part.get('type') == 'image_url':
                    url = str((part.get('image_url') or {}).get('url') or '')
                    new_content.append({'type': 'image_url', 'image_url': {'url': f'<base64 len={len(url)}>'}})
                else:
                    new_content.append(part)
            m2['content'] = new_content
        msgs.append(m2)
    out['messages'] = msgs
    return out


def _count_images(req: dict) -> int:
    n = 0
    for m in req.get('messages') or []:
        content = m.get('content')
        if isinstance(content, list):
            n += sum(1 for p in content if isinstance(p, dict) and p.get('type') == 'image_url')
    return n


def _json_content(req: dict) -> str:
    model = str(req.get('model') or 'stub-model')
    image_count = _count_images(req)
    payload = {
        'activity_master': {
            'title': f'STUB｜{model}',
            'date': '2026-10-25',
            'location': 'STUB 地点',
            'price': 298,
            'capacity': 20,
            'publicFacts': {'stubModel': model, 'receivedImages': image_count, 'vision': image_count > 0},
            'itinerary': [], 'fees': {}, 'checklist': [], 'services': [],
            'internalData': {}, 'uncertainties': [], 'blocking_conflicts': [], 'media': [],
        },
        'detail': {
            'activityUnderstanding': f'STUB 收到 model={model}, images={image_count}',
            'coreSellingIdea': 'STUB 内容（非真实生成）',
            'editorialIntent': {'opening': 'stub', 'visualWeight': 'stub', 'template': 'NONE'},
            'blocks': [
                {'type': 'hero', 'headline': 'STUB HERO'},
                {'type': 'lead', 'text': 'stub lead'},
                {'type': 'cta', 'headline': 'stub cta'},
            ],
        },
    }
    # 故意包一层 Markdown 代码块：验证 Gateway 的 _extract_json 兜底分支
    return '```json\n' + json.dumps(payload, ensure_ascii=False) + '\n```'


class _Handler(BaseHTTPRequestHandler):
    server_version = 'ClubOSDevStub/0.1'
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):  # 静音访问日志
        return

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip('/').endswith('/models'):
            self._send(200, {'object': 'list', 'data': [{'id': 'stub-model', 'object': 'model'}]})
            return
        self._send(404, {'error': {'message': 'stub: not found'}})

    def do_POST(self):
        cfg = self.server.cfg  # type: ignore[attr-defined]
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b''
        try:
            req = json.loads(raw.decode('utf-8') or '{}')
        except Exception:
            req = {}
        auth = self.headers.get('Authorization') or ''
        with open(cfg['log'], 'a', encoding='utf-8') as fh:
            fh.write(json.dumps({
                'ts': time.time(),
                'path': self.path,
                'authorization': auth,
                'content_type': self.headers.get('Content-Type') or '',
                'body': _sanitize(req) if isinstance(req, dict) else req,
            }, ensure_ascii=False) + '\n')

        if self.path.rstrip('/').endswith('/models'):
            self._send(200, {'object': 'list', 'data': [{'id': 'stub-model', 'object': 'model'}]})
            return

        token = auth.split(' ', 1)[1].strip() if auth.lower().startswith('bearer ') else ''
        if len(token) < 4:
            self._send(401, {'error': {'message': 'stub: missing or invalid bearer token',
                                       'type': 'invalid_request_error'}})
            return
        if cfg['always_fail']:
            self._send(500, {'error': {'message': 'stub: forced failure (failover test)',
                                       'type': 'server_error'}})
            return

        content = _json_content(req if isinstance(req, dict) else {})
        self._send(200, {
            'id': 'chatcmpl-stub-' + uuid.uuid4().hex[:12],
            'object': 'chat.completion',
            'created': int(time.time()),
            'model': (req.get('model') if isinstance(req, dict) else None) or 'stub-model',
            'choices': [{
                'index': 0,
                'message': {'role': 'assistant', 'content': content},
                'finish_reason': 'stop',
            }],
            'usage': {'prompt_tokens': 321, 'completion_tokens': 123, 'total_tokens': 444},
        })


def start_stub(port: int = 0, *, always_fail: bool = False, log_path: str | os.PathLike | None = None):
    """启动 stub（默认随机端口、daemon 线程）。返回 (server, thread, port)。"""
    log = Path(log_path or DEFAULT_LOG)
    log.parent.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer(('127.0.0.1', port), _Handler)
    httpd.cfg = {'always_fail': bool(always_fail), 'log': str(log)}  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, httpd.server_address[1]


def main() -> int:
    ap = argparse.ArgumentParser(description='ClubOS local OpenAI-compatible dev stub (link-level testing only)')
    ap.add_argument('--port', type=int, default=8799)
    ap.add_argument('--always-fail', action='store_true', help='always return HTTP 500 (failover testing)')
    ap.add_argument('--log', default=str(DEFAULT_LOG))
    args = ap.parse_args()

    httpd, _thread, port = start_stub(args.port, always_fail=args.always_fail, log_path=args.log)
    print(f'[stub] listening on http://127.0.0.1:{port}/v1/chat/completions  (always_fail={args.always_fail})')
    print(f'[stub] request log -> {args.log}')
    print('[stub] NOTE: this is a contract stub, NOT a model. Do not judge generation quality from it.')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
