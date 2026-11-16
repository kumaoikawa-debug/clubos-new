"""总平台模型接入纠错 + 接口配置标准说明专项（2026-11-16）。

修复背景：
  用户把服务商切成「通义千问」，文本模型仍留着 deepseek-chat，DashScope 返回
  404 model_not_found；同时「接口配置」页没有标准数据说明，运营不知道上游该吐什么 JSON。

本测试全部使用 /tmp 隔离库，不碰项目真库，不打真实模型。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix='clubos_ai_cfg_ux_')
os.environ['CLUBOS_DB_PATH'] = os.path.join(TMP, 'clubos.db')
os.environ['CLUBOS_TEST_OPEN'] = '1'
os.environ['COMMERCE_PROVIDER'] = 'local'
os.environ['MOCK_AI'] = '1'
os.environ.pop('CLUBOS_SECURITY_MODE', None)
for _k in ('AI_PRIMARY_API_KEY', 'AI_SECONDARY_API_KEY', 'LLM_API_KEY',
           'AI_PRIMARY_BASE_URL', 'AI_SECONDARY_BASE_URL', 'AI_PRIMARY_MODEL',
           'AI_SECONDARY_MODEL', 'AI_PRIMARY_PRESET', 'AI_SECONDARY_PRESET'):
    os.environ.pop(_k, None)

import db  # noqa: E402
db.init_db()

from fastapi.testclient import TestClient  # noqa: E402
from app import app  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = '') -> None:
    RESULTS.append((name, bool(ok), detail))


client = TestClient(app)

# ---------------------------------------------------------------- 后端护栏 ---
r = client.patch('/api/platform/ai/providers', json={
    'mode': 'live',
    'primary': {'preset': 'qwen', 'model': 'deepseek-chat',
                'visionModel': 'qwen3-vl-plus', 'apiKey': 'sk-test-do-not-log'},
})
check('千问 + deepseek-chat 错配被 400 拦截', r.status_code == 400, f'status={r.status_code} {r.text[:160]}')
check('错配提示说明服务商与模型不匹配', '服务商是' in r.text and 'deepseek-chat' in r.text, r.text[:240])

r = client.patch('/api/platform/ai/providers', json={
    'mode': 'live',
    'primary': {'preset': 'qwen', 'model': 'qwen3.8-max',
                'visionModel': 'qwen3-vl-plus', 'apiKey': 'sk-test-do-not-log'},
    'secondary': {'preset': 'deepseek', 'model': 'deepseek-chat', 'apiKey': 'sk-secondary-test'},
})
check('千问配置正确模型保存 200', r.status_code == 200, f'status={r.status_code} {r.text[:160]}')
body = r.json()
view = (body.get('saved') or {}).get('primary') or {}
check('密钥不回显明文', 'sk-test-do-not-log' not in r.text and 'sk-secondary-test' not in r.text)
check('密钥只回掩码', str(view.get('apiKeyMasked') or '').startswith('sk-t'), str(view.get('apiKeyMasked')))
eff = (body.get('effective') or {}).get('providers') or []
check('生效端点为 DashScope 兼容端点',
      any(p.get('role') == 'primary' and 'dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
          in str(p.get('baseUrl')) for p in eff), str(eff)[:240])

# 切服务商时若没显式传 baseUrl，必须跟着切，不能拿新 key 打旧端点。
r = client.patch('/api/platform/ai/providers', json={'primary': {'preset': 'qwen'}})
check('仅切服务商也能保存', r.status_code == 200, f'status={r.status_code} {r.text[:160]}')
p0 = ((r.json().get('effective') or {}).get('providers') or [{}])[0]
check('切服务商后端点自动归一', 'dashscope.aliyuncs.com' in str(p0.get('baseUrl')), str(p0))

r = client.patch('/api/platform/ai/providers', json={'primary': {'preset': 'custom', 'model': 'my-model'}})
check('自定义端点缺地址被拒绝', r.status_code == 400, f'status={r.status_code}')
check('自定义端点提示填完整地址', '接口地址' in r.text, r.text[:200])

# 前面保存过 mode=live，这里显式回到 auto 才会跟随 MOCK_AI=1（也顺手验证模式切换）。
r = client.patch('/api/platform/ai/providers', json={'mode': 'auto'})
check('运行模式可切回跟随环境变量', r.status_code == 200, f'status={r.status_code}')
r = client.post('/api/platform/ai/providers/test', json={'role': 'primary'})
check('mock 模式自检仍给出明确提示', r.status_code == 200 and r.json().get('ok') is False and 'mock' in r.text,
      r.text[:180])

# --------------------------------------------------- 接口配置标准说明数据 ---
from clubos_domain import integration_config as icfg  # noqa: E402

with db.conn() as c:
    spec = icfg.public(c)
guide = spec.get('standardGuide') or {}
check('接口配置返回标准数据说明', bool(guide.get('title')) and bool(guide.get('steps')))
check('标准说明含总目录 JSON 示例',
      any('products' in str(e.get('response')) and 'categories' in str(e.get('response'))
          for e in guide.get('examples') or []))
check('标准说明含库存 JSON 示例',
      any('inventoryPath' in str(e.get('request')) for e in guide.get('examples') or []))
check('标准说明含令牌说明', 'tokenField' in str(guide))
check('接口配置返回分组说明', len(spec.get('groupGuide') or {}) >= 4, str(spec.get('groupGuide'))[:200])
check('商品总目录 help 已给出路径示例', '/api/b2b/catalog' in str(spec.get('fields')))
check('接口令牌 help 已给出 body 示例', '{"token":' in str(spec.get('fields')))

# ----------------------------------------------------------- 静态 UI 断言 ---
ai_js = (ROOT / 'static/ux/workflows-platform-ai.js').read_text(encoding='utf-8')
check('模型弹窗改为自定义 uxDialog', 'uxDialog({title:\'模型接入（总平台独占）\'' in ai_js)
check('模型弹窗有服务商联动', 'applyPreset' in ai_js and 'ai_${role}_preset' in ai_js)
check('模型弹窗可填接口地址', 'ai_${role}_url' in ai_js)
check('模型弹窗提示错配', '与服务商不匹配' in ai_js)
check('自检结果落页面而非只弹 toast', 'aiProviderTestResult' in ai_js and 'testResultHtml' in ai_js)
check('旧的 deepseek-chat 固定默认值已移除', "value:sp.model||'deepseek-chat'" not in ai_js)
check('备用视觉模型不再写已下线 qwen-vl-max', 'qwen-vl-max' not in ai_js)

api_js = (ROOT / 'static/ux/workflows-platform-integration.js').read_text(encoding='utf-8')
check('接口页渲染标准说明卡', 'guideHtml(spec)' in api_js and 'plat-api__guide' in api_js)
check('接口页渲染分组说明', 'plat-api__groupnote' in api_js)
check('接口页不再用旧分组调用', 'grouped(spec.fields, spec.config)' not in api_js)

css = (ROOT / 'static/platform/platform.css').read_text(encoding='utf-8')
check('CSS 含标准说明样式', '.plat-api__guidebody' in css and '.plat-api__steps' in css)
check('CSS 含模型接入弹窗样式', '.plat-ai-form' in css and '.plat-ai-test' in css)

# --------------------------------------------------------------------- 汇总 ---
print('=' * 72)
for name, ok, detail in RESULTS:
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'   [{detail}]' if detail and not ok else ''))
passed = sum(1 for _, ok, _ in RESULTS if ok)
print('=' * 72)
print(f'PASS={passed} FAIL={len(RESULTS) - passed} TOTAL={len(RESULTS)}')
raise SystemExit(0 if passed == len(RESULTS) else 1)
