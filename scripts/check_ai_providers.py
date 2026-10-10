"""AI 接入策略自检（国内 Provider + 平台独占 + 积分计费）。

验证两件事：
  1. 中国大陆默认接入 DeepSeek（文本）+ 通义千问（视觉），且带图任务自动走视觉模型；
  2. 模型接入只属于总平台：俱乐部没有任何模型配置端点，密钥不会下发给浏览器。

运行：CLUBOS_DB_PATH 由脚本自行指向临时库，无需外部依赖。
    python scripts/check_ai_providers.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix='clubos_ai_check_')
os.environ['CLUBOS_DB_PATH'] = os.path.join(TMP, 'check.db')
os.environ['COMMERCE_PROVIDER'] = 'local'
os.environ['MOCK_AI'] = '1'
os.environ.pop('CLUBOS_SECURITY_MODE', None)
for _k in ('AI_PRIMARY_API_KEY', 'AI_SECONDARY_API_KEY', 'LLM_API_KEY',
           'AI_PRIMARY_BASE_URL', 'AI_SECONDARY_BASE_URL', 'AI_PRIMARY_MODEL', 'AI_SECONDARY_MODEL'):
    os.environ.pop(_k, None)

from fastapi.testclient import TestClient  # noqa: E402
import ai_gateway  # noqa: E402
from app import app  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = '') -> None:
    RESULTS.append((name, bool(ok), detail))


client = TestClient(app)

# ---------------------------------------------------------------------------
# Phase A：Provider 解析与路由（纯环境变量，无平台配置）
# ---------------------------------------------------------------------------
presets = ai_gateway.PROVIDER_PRESETS
check('预设包含 deepseek', 'deepseek' in presets)
check('预设包含 qwen（通义千问）', 'qwen' in presets)
check('deepseek 端点正确', 'api.deepseek.com' in presets['deepseek']['base_url'], presets['deepseek']['base_url'])
check('qwen 使用 DashScope 兼容端点', 'dashscope.aliyuncs.com' in presets['qwen']['base_url'], presets['qwen']['base_url'])
check('deepseek 不带视觉能力', presets['deepseek']['supports_vision'] is False)
check('qwen 具备视觉能力', presets['qwen']['supports_vision'] is True)

check('未配置密钥时无可用 Provider', ai_gateway._providers() == [], str(ai_gateway._providers()))

os.environ['AI_PRIMARY_API_KEY'] = 'sk-deepseek-test'
os.environ['AI_SECONDARY_API_KEY'] = 'sk-qwen-test'
providers = ai_gateway._providers()
check('配置密钥后解析出 2 个 Provider', len(providers) == 2, str([p.preset for p in providers]))
check('主用为 DeepSeek', providers and providers[0].preset == 'deepseek' and providers[0].role == 'primary')
check('备用为通义千问', len(providers) > 1 and providers[1].preset == 'qwen' and providers[1].role == 'secondary')

text_model = ai_gateway._model_for_task(providers[0], 'detail')
vision_model = ai_gateway._model_for_task(providers[1], 'detail', has_images=True)
check('文本任务走 deepseek-chat', text_model == 'deepseek-chat', text_model)
check('图片任务走 qwen3-vl-plus', vision_model == 'qwen3-vl-plus', vision_model)

vision_candidates = ai_gateway._candidates(has_images=True)
check('带图时仅选择视觉 Provider', all(p.supports_vision for p in vision_candidates) and vision_candidates,
      str([p.preset for p in vision_candidates]))
text_candidates = ai_gateway._candidates(has_images=False)
check('纯文本时主用优先', text_candidates and text_candidates[0].preset == 'deepseek',
      str([p.preset for p in text_candidates]))

os.environ['AI_SECONDARY_ENABLED'] = '0'
os.environ.pop('AI_SECONDARY_API_KEY', None)
try:
    ai_gateway._candidates(has_images=True)
    check('无视觉模型时明确报错', False, '未抛错')
except ai_gateway.AIGatewayError as exc:
    check('无视觉模型时明确报错', '视觉' in str(exc), str(exc))
os.environ['AI_SECONDARY_ENABLED'] = '1'
os.environ['AI_SECONDARY_API_KEY'] = 'sk-qwen-test'

status = ai_gateway.gateway_status()
check('状态标记 region=cn', status.get('region') == 'cn')
check('状态标记 owner=platform', status.get('owner') == 'platform')
check('状态禁止俱乐部配置模型', status.get('policy', {}).get('clubCanConfigureModel') is False)
check('状态路由文本=deepseek', status.get('routing', {}).get('text') == 'deepseek')
check('状态路由视觉=qwen', status.get('routing', {}).get('vision') == 'qwen')

# ---------------------------------------------------------------------------
# Phase B：平台独占的配置读写（HTTP）
# ---------------------------------------------------------------------------
r = client.get('/api/platform/ai/providers')
body = r.json()
check('GET /api/platform/ai/providers 200', r.status_code == 200, f'status={r.status_code}')
check('平台接口返回预设清单', len(body.get('presets') or []) >= 2)
check('平台接口返回接入策略说明', '总平台' in str(body.get('note') or ''))

r = client.patch('/api/platform/ai/providers', json={
    'primary': {'preset': 'deepseek', 'model': 'deepseek-reasoner', 'apiKey': 'sk-platform-secret-abcdef'},
    'secondary': {'preset': 'qwen', 'model': 'qwen-max', 'visionModel': 'qwen3-vl-plus'},
    'allowFailover': True,
})
check('PATCH 保存模型配置 200', r.status_code == 200, f'status={r.status_code} body={r.text[:160]}')
saved = (r.json().get('saved') or {}).get('primary') or {}
check('平台配置已生效', saved.get('model') == 'deepseek-reasoner', str(saved))
check('密钥以掩码返回', 'sk-platform-secret-abcdef' not in r.text, '密钥泄漏')
check('密钥掩码格式正确', str(saved.get('apiKeyMasked') or '').startswith('sk-p'), str(saved.get('apiKeyMasked')))
check('平台配置为最优来源', r.json().get('effective', {}).get('configSource') == 'platform')
check('平台配置覆盖后主用模型生效', ai_gateway._providers()[0].default_model == 'deepseek-reasoner')

r = client.patch('/api/platform/ai/providers', json={'primary': {'preset': 'not-a-real-provider'}})
check('非法 Provider 被拒绝（400）', r.status_code == 400, f'status={r.status_code}')

r = client.post('/api/platform/ai/providers/test', json={'role': 'primary'})
check('连通性自检在 mock 模式给出明确提示',
      r.status_code == 200 and r.json().get('ok') is False and 'mock' in str(r.json()),
      r.text[:160])

club_probe = client.get('/api/club/1/ai/providers')
check('俱乐部端没有模型配置端点', club_probe.status_code in (404, 405), f'status={club_probe.status_code}')
club_patch = client.patch('/api/club/1/ai/providers', json={'primary': {'preset': 'openai'}})
check('俱乐部端无法写入模型配置', club_patch.status_code in (404, 405), f'status={club_patch.status_code}')

# ---------------------------------------------------------------------------
print('=' * 72)
passed = 0
for name, ok, detail in RESULTS:
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'   [{detail}]' if (detail and not ok) else ''))
    passed += 1 if ok else 0
print('=' * 72)
print(f'PASS={passed} FAIL={len(RESULTS) - passed} TOTAL={len(RESULTS)}')
raise SystemExit(0 if passed == len(RESULTS) else 1)
