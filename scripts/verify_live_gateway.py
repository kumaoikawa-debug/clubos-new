#!/usr/bin/env python3
"""验证 ClubOS AI Gateway 的 live 链路端到端可用（结构/契约层）。

⚠️ 本脚本用本地 OpenAI 兼容 stub 作为"模型"，因此它验证的是**链路**：
   鉴权头透传、请求体合规、响应解析（含 Markdown 包裹 JSON 的兜底）、
   usage 记账、视觉路由、主备 failover、缺少视觉能力时 fail-closed、无 Provider 时的明确报错。

   它 **不** 代表任何真实模型的生成质量。真实质量对照见 scripts/compare_mock_vs_live.py，
   那一份需要真实 API Key 才能跑。

用法： python scripts/verify_live_gateway.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

TMP = Path(tempfile.mkdtemp(prefix='clubos_live_verify_'))
os.environ['CLUBOS_DB_PATH'] = str(TMP / 'verify.db')
os.environ['MOCK_AI'] = '0'
os.environ['CLUBOS_SECURITY_MODE'] = 'demo'
for k in ('AI_PRIMARY_API_KEY', 'AI_SECONDARY_API_KEY', 'LLM_API_KEY',
          'AI_PRIMARY_BASE_URL', 'AI_SECONDARY_BASE_URL', 'AI_PRIMARY_MODEL', 'AI_SECONDARY_MODEL'):
    os.environ.pop(k, None)

import dev_openai_stub  # noqa: E402
from db import init_db, conn, rows  # noqa: E402
import ai_gateway  # noqa: E402

PNG_1PX = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/AF+7b0hAAAAAElFTkSuQmCC'
)

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


def _usage_rows() -> list[dict]:
    with conn() as c:
        return rows(c.execute(
            'SELECT task_type,provider,model,status,input_tokens,output_tokens,provider_cost,error '
            'FROM ai_usage_records ORDER BY id'
        ))


def main() -> int:
    init_db()

    ok_log = TMP / 'ok.jsonl'
    fail_log = TMP / 'fail.jsonl'
    _ok_srv, _ok_thr, ok_port = dev_openai_stub.start_stub(0, log_path=ok_log)
    _fail_srv, _fail_thr, fail_port = dev_openai_stub.start_stub(0, always_fail=True, log_path=fail_log)

    ok_url = f'http://127.0.0.1:{ok_port}/v1/chat/completions'
    fail_url = f'http://127.0.0.1:{fail_port}/v1/chat/completions'

    os.environ.update({
        'AI_PRIMARY_PRESET': 'deepseek', 'AI_PRIMARY_ENABLED': '1',
        'AI_PRIMARY_API_KEY': 'stub-key-primary', 'AI_PRIMARY_BASE_URL': ok_url,
        'AI_PRIMARY_MODEL': 'deepseek-chat',
        'AI_SECONDARY_PRESET': 'qwen', 'AI_SECONDARY_ENABLED': '1',
        'AI_SECONDARY_API_KEY': 'stub-key-secondary', 'AI_SECONDARY_BASE_URL': ok_url,
        'AI_SECONDARY_MODEL': 'qwen-plus', 'AI_SECONDARY_VISION_MODEL': 'qwen-vl-max',
        'AI_ALLOW_FAILOVER': '1',
    })

    # ---- 1. Provider 解析与状态 ----
    provs = ai_gateway._providers()
    check('解析出 2 个 Provider 且主用在前', len(provs) == 2 and provs[0].role == 'primary',
          f'{[(p.role, p.preset, p.default_model) for p in provs]}')
    check('主用=deepseek，备用=qwen',
          len(provs) == 2 and provs[0].preset == 'deepseek' and provs[1].preset == 'qwen',
          f'{[(p.role, p.preset) for p in provs]}')
    st = ai_gateway.gateway_status()
    check('gateway_status: live 模式', st['mode'] == 'live', st['mode'])
    check('gateway_status: 文本路由=deepseek', st['routing']['text'] == 'deepseek', str(st['routing']))
    check('gateway_status: 视觉路由=qwen', st['routing']['vision'] == 'qwen' and st['routing']['hasVision'],
          str(st['routing']))
    check('gateway_status: 平台独占声明', st['owner'] == 'platform' and st['policy']['clubCanConfigureModel'] is False,
          str(st['policy']))

    # ---- 2. 纯文本调用：真的发出 HTTP、透传密钥、正确解析 ----
    import asyncio
    resp = asyncio.run(ai_gateway.generate_json(
        club_id=1, task_type='detail', system_prompt='SYS', user_prompt='USER 青城山'))
    check('纯文本调用返回已解析的 JSON dict', isinstance(resp.data, dict) and 'activity_master' in resp.data,
          str(list(resp.data.keys())))
    check('Markdown ```json 包裹的响应被正确解析 (兜底分支)', resp.data['activity_master'].get('title', '').startswith('STUB｜'),
          str(resp.data.get('activity_master', {}).get('title')))
    check('Provider/模型记录为 deepseek / deepseek-chat',
          resp.provider == 'deepseek' and resp.model == 'deepseek-chat',
          f'{resp.provider} / {resp.model}')
    check('usage tokens 来自响应体 (321/123)',
          resp.input_tokens == 321 and resp.output_tokens == 123,
          f'in={resp.input_tokens} out={resp.output_tokens}')

    log = _read_log(ok_log)
    hits = [r for r in log if r['path'].startswith('/v1/chat/completions')]
    check('stub 确实收到了出站请求（说明不是本地短路）', len(hits) >= 1, f'hits={len(hits)}')
    check('Authorization: Bearer 被正确透传（主用 key）',
          bool(hits) and hits[-1]['authorization'] == 'Bearer stub-key-primary',
          hits[-1]['authorization'] if hits else 'none')
    body = hits[-1]['body'] if hits else {}
    check('请求体合规 (system+user, temperature)', len(body.get('messages') or []) == 2 and 'temperature' in body,
          f"msgs={len(body.get('messages') or [])} keys={sorted(body.keys())}")

    # ---- 3. usage 记账 ----
    ur = _usage_rows()
    check('usage 落库为 success / deepseek / deepseek-chat',
          any(r['status'] == 'success' and r['provider'] == 'deepseek' and r['model'] == 'deepseek-chat' for r in ur),
          json.dumps(ur[-1:] if ur else [], ensure_ascii=False))

    # ---- 4. 视觉路由：带图必须切到具备视觉能力的 Provider ----
    img = TMP / 'pic.png'
    img.write_bytes(PNG_1PX)
    before = len(_read_log(ok_log))
    resp_v = asyncio.run(ai_gateway.generate_json(
        club_id=1, task_type='detail', system_prompt='SYS', user_prompt='USER 带图',
        images=[{'path': str(img)}]))
    vlog = _read_log(ok_log)[before:]
    check('带图任务路由到 qwen 的视觉模型 qwen-vl-max',
          resp_v.model == 'qwen-vl-max' and resp_v.provider == 'qwen',
          f'{resp_v.provider} / {resp_v.model}')
    vbody = (vlog[-1]['body'] if vlog else {})
    vparts = []
    for m in (vbody.get('messages') or []):
        if isinstance(m.get('content'), list):
            vparts.extend(p for p in m['content'] if isinstance(p, dict))
    check('请求体携带 image_url（图片没有被静默丢弃）',
          any(p.get('type') == 'image_url' for p in vparts), f'parts={[p.get("type") for p in vparts]}')
    check('stub 侧确认收到图片', bool(vlog) and vlog[-1]['body'] is not None)

    # ---- 5. failover：主用失败 → 切备用（平台显式开关下） ----
    os.environ['AI_PRIMARY_BASE_URL'] = fail_url
    before_f = len(_read_log(fail_log))
    resp_f = asyncio.run(ai_gateway.generate_json(
        club_id=1, task_type='detail', system_prompt='SYS', user_prompt='USER failover'))
    check('主用 500 时自动 failover 到备用并成功',
          resp_f.provider == 'qwen', f'{resp_f.provider} / {resp_f.model}')
    check('失败的主用调用被记为 failed（可观测）',
          len(_read_log(fail_log)) > before_f, f'primary_hits={len(_read_log(fail_log))}')
    ur2 = _usage_rows()
    check('usage 同时存在 deepseek=failed 与 qwen=success',
          any(r['status'] == 'failed' and r['provider'] == 'deepseek' for r in ur2)
          and any(r['status'] == 'success' and r['provider'] == 'qwen' for r in ur2),
          f"{[(r['provider'], r['status']) for r in ur2]}")

    # ---- 6. 关掉 failover 后主用失败应直接抛错 ----
    os.environ['AI_ALLOW_FAILOVER'] = '0'
    try:
        asyncio.run(ai_gateway.generate_json(
            club_id=1, task_type='detail', system_prompt='SYS', user_prompt='USER no-failover'))
        check('关闭 failover 后主用失败应抛 AIGatewayError', False, '未抛错')
    except ai_gateway.AIGatewayError as exc:
        check('关闭 failover 后主用失败应抛 AIGatewayError', True, str(exc)[:80])
    os.environ['AI_ALLOW_FAILOVER'] = '1'
    os.environ['AI_PRIMARY_BASE_URL'] = ok_url

    # ---- 7. fail-closed：只有文本模型却来了图片任务 ----
    os.environ['AI_SECONDARY_ENABLED'] = '0'
    try:
        asyncio.run(ai_gateway.generate_json(
            club_id=1, task_type='detail', system_prompt='SYS', user_prompt='USER', images=[{'path': str(img)}]))
        check('只有文本模型时图片任务应 fail-closed', False, '未抛错')
    except ai_gateway.AIGatewayError as exc:
        check('只有文本模型时图片任务应 fail-closed', '视觉' in str(exc), str(exc)[:90])
    os.environ['AI_SECONDARY_ENABLED'] = '1'

    # ---- 8. 无任何 Provider 时给出可读中文报错 ----
    os.environ['AI_PRIMARY_ENABLED'] = '0'
    os.environ['AI_SECONDARY_ENABLED'] = '0'
    try:
        asyncio.run(ai_gateway.generate_json(
            club_id=1, task_type='detail', system_prompt='SYS', user_prompt='USER'))
        check('无可用 Provider 时应给出明确中文报错', False, '未抛错')
    except ai_gateway.AIGatewayError as exc:
        check('无可用 Provider 时应给出明确中文报错', '总平台' in str(exc), str(exc)[:90])
    os.environ['AI_PRIMARY_ENABLED'] = '1'
    os.environ['AI_SECONDARY_ENABLED'] = '1'

    # ---- 9. 平台连通性自检 ----
    ping = asyncio.run(ai_gateway.test_provider_connection('primary'))
    check('平台连通性自检 primary 通过', ping.get('ok') is True, json.dumps(ping, ensure_ascii=False)[:120])

    # ---- 输出 ----
    passed = sum(1 for _, ok, _ in results if ok)
    print('=' * 74)
    print('ClubOS AI Gateway — live 链路契约验证（本地 stub，非真实模型质量）')
    print('=' * 74)
    for name, ok, detail in results:
        print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'   | {detail}' if detail and not ok else ''))
    print('-' * 74)
    print(f'总计 {passed}/{len(results)} PASS')
    print(f'临时 DB: {os.environ["CLUBOS_DB_PATH"]}')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
