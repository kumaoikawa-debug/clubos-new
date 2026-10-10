from __future__ import annotations
import base64
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from db import conn, setting


class AIGatewayError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 平台统一大模型接入（中国大陆部署默认：DeepSeek 文本 + 通义千问 视觉）
#
# 产品策略：
#   1. 大模型只由【总平台】接入并持有密钥；俱乐部没有任何模型配置入口；
#   2. 总平台把真实调用（token / provider 成本）折算成 AI Credits 发放给俱乐部；
#   3. 各端俱乐部按任务消耗 Credits 使用生成能力。
#
# 两家国内厂商都提供 OpenAI 兼容的 /chat/completions 接口，
# 因此下方 base_url 均指向官方兼容端点，无需额外适配层。
# ---------------------------------------------------------------------------
PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    'deepseek': {
        'label': 'DeepSeek 深度求索',
        'region': 'cn',
        'base_url': 'https://api.deepseek.com/v1/chat/completions',
        'default_model': 'deepseek-chat',
        'vision_model': '',
        'supports_vision': False,
        'doc': 'https://platform.deepseek.com',
    },
    'qwen': {
        'label': '通义千问 · 阿里云百炼（DashScope 兼容模式）',
        'region': 'cn',
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
        'default_model': 'qwen-plus',
        'vision_model': 'qwen3-vl-plus',   # qwen-vl-max 已被阿里下线（404 model_not_found），勿再回用
        'supports_vision': True,
        'doc': 'https://help.aliyun.com/zh/model-studio/',
    },
    'openai': {
        'label': 'OpenAI（仅境外部署使用）',
        'region': 'global',
        'base_url': 'https://api.openai.com/v1/chat/completions',
        'default_model': 'gpt-4o-mini',
        'vision_model': 'gpt-4o-mini',
        'supports_vision': True,
        'doc': 'https://platform.openai.com',
    },
    'custom': {
        'label': '自定义 OpenAI 兼容端点',
        'region': 'any',
        'base_url': '',
        'default_model': '',
        'vision_model': '',
        'supports_vision': False,
        'doc': '',
    },
}

DEFAULT_PRIMARY_PRESET = 'deepseek'
DEFAULT_SECONDARY_PRESET = 'qwen'
AI_PROVIDERS_SETTING_KEY = 'ai_providers_json'

# ---------------------------------------------------------------------------
# 运行模式：总平台后台可控，填完 Key 即可真实调用，不需要改服务器环境变量。
#
#   auto（默认）  沿用环境变量 MOCK_AI —— 未接入时保持演示行为
#   live          强制真实调用已配置的 Provider（总平台填了 Key 就选它）
#   mock          强制演示模式（仅非生产环境可用，用于离线演示）
#
# 生产环境（CLUBOS_SECURITY_MODE=production）永远不接受 mock：
# 后台把模式切回 mock 会被拒绝，且已存的 mock 会被忽略、回退到环境变量口径，
# 保持 v0.25「生产必须 fail-closed」的安全边界。
# ---------------------------------------------------------------------------
AI_MODE_AUTO = 'auto'
AI_MODE_LIVE = 'live'
AI_MODE_MOCK = 'mock'
AI_MODES = (AI_MODE_AUTO, AI_MODE_LIVE, AI_MODE_MOCK)


def _is_prod() -> bool:
    """是否生产模式。优先复用 security_v025 的判定，避免两处口径分叉；
    独立脚本（未加载 app）时按同一环境变量判定。"""
    mod = sys.modules.get('security_v025')
    if mod is not None:
        return bool(getattr(mod, 'IS_PROD', False))
    return os.getenv('CLUBOS_SECURITY_MODE', 'demo').strip().lower() == 'production'


def saved_gateway_mode() -> str:
    """总平台后台保存的运行模式；未保存过则为 None。"""
    mode = str(_db_ai_config().get('mode') or '').strip().lower()
    return mode if mode in AI_MODES else None


def effective_gateway_mode() -> tuple[str, str]:
    """返回 (mode, source)，mode ∈ {'live','mock'}。

    优先级：总平台后台配置 > 环境变量 MOCK_AI。
    生产环境忽略后台的 mock，防止把线上切回演示模式。
    """
    saved = saved_gateway_mode()
    if saved == AI_MODE_LIVE:
        return 'live', 'platform'
    if saved == AI_MODE_MOCK and not _is_prod():
        return 'mock', 'platform'
    env_mock = os.getenv('MOCK_AI', '1').strip() != '0'
    return ('mock' if env_mock else 'live'), 'env'


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    default_model: str
    enabled: bool = True
    role: str = 'primary'
    preset: str = 'custom'
    label: str = ''
    region: str = 'any'
    vision_model: str = ''
    supports_vision: bool = False
    key_source: str = 'none'


@dataclass
class GatewayResponse:
    data: dict[str, Any]
    usage_id: int
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    provider_cost: float
    request_id: str


def _extract_json(txt: str) -> dict[str, Any]:
    try:
        return json.loads(txt)
    except Exception:
        m = re.search(r'\{.*\}', txt, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    raise AIGatewayError('模型未返回合法JSON')


# ---------------------------------------------------------------------------
# 配置解析：总平台后台配置（DB）优先，环境变量兜底
# ---------------------------------------------------------------------------
def _preset(name: str | None) -> dict[str, Any]:
    return PROVIDER_PRESETS.get(str(name or '').strip().lower(), {})


def _db_ai_config() -> dict[str, Any]:
    """总平台在后台保存的模型接入配置；仅总平台可写。"""
    try:
        raw = setting(AI_PROVIDERS_SETTING_KEY)
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _truthy(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {'0', 'false', 'no', 'off', ''}


def _httpx_client(timeout: float) -> httpx.AsyncClient:
    """AI 调用专用 HTTP 客户端，默认忽略环境变量里的 HTTP_PROXY / HTTPS_PROXY。

    服务器/本机残留的代理变量会把模型请求交给一个根本不认识该端点的代理，
    症状是毫无信息量的 `ProxyError: 502 Bad Gateway`，运维极难定位。
    确需经代理出网时显式设置 AI_TRUST_ENV=1，并确认代理真的能访问模型端点。

    连接阶段单独限时（AI_CONNECT_TIMEOUT_SECONDS，默认 10 秒）：模型端点不可达时
    应当快速失败并给出原因，而不是让俱乐部前台跟着挂满整个读取超时（默认 240 秒）。
    """
    total = float(timeout or 240)
    connect = float(os.getenv('AI_CONNECT_TIMEOUT_SECONDS', '10') or 10)
    limits = httpx.Timeout(total, connect=connect)
    return httpx.AsyncClient(timeout=limits, trust_env=os.getenv('AI_TRUST_ENV', '0').strip() == '1')


def _explain(exc: Exception) -> str:
    """把底层网络异常翻译成运维看得懂的说明。"""
    # httpx 的超时类异常 str() 常为空串，用类名兜底，别让日志只剩一条破折号。
    detail = str(exc).strip() or type(exc).__name__
    if isinstance(exc, httpx.ProxyError):
        return (f'请求被环境变量里的代理接管了（{detail}）。AI 调用默认直连；'
                f'若确实需要走代理，请设置 AI_TRUST_ENV=1 并确认代理可访问模型端点，'
                f'否则请清掉服务器上的 HTTP_PROXY/HTTPS_PROXY。')
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError)):
        return (f'无法连接模型端点（{detail}）。请依次检查：服务器出网是否放行该域名、'
                f'防火墙/安全组、DNS 解析，以及 baseUrl 是否写对。')
    return detail


def _provider_from_spec(role: str, spec: dict[str, Any], *, env_prefix: str, default_preset: str) -> ProviderConfig:
    preset_name = str(spec.get('preset') or os.getenv(f'{env_prefix}_PRESET') or default_preset).strip().lower()
    preset = _preset(preset_name)
    saved_key = spec.get('apiKey')
    env_key = os.getenv(f'{env_prefix}_API_KEY', '')
    api_key = str(saved_key if saved_key else env_key)
    return ProviderConfig(
        name=str(spec.get('name') or os.getenv(f'{env_prefix}_NAME') or role),
        role=role,
        preset=preset_name,
        label=str(preset.get('label') or preset_name),
        region=str(preset.get('region') or 'any'),
        base_url=str(spec.get('baseUrl') or os.getenv(f'{env_prefix}_BASE_URL') or preset.get('base_url') or ''),
        api_key=api_key,
        default_model=str(spec.get('model') or os.getenv(f'{env_prefix}_MODEL') or preset.get('default_model') or ''),
        vision_model=str(spec.get('visionModel') or os.getenv(f'{env_prefix}_VISION_MODEL') or preset.get('vision_model') or ''),
        supports_vision=_truthy(spec.get('supportsVision', preset.get('supports_vision', False)), False),
        enabled=_truthy(spec.get('enabled', os.getenv(f'{env_prefix}_ENABLED', '1')), True),
        key_source='platform' if saved_key else ('env' if env_key else 'none'),
    )


def _providers() -> list[ProviderConfig]:
    """返回已启用且配置完整的 Provider（主用在前，备用在后）。"""
    cfg = _db_ai_config()
    primary = _provider_from_spec('primary', cfg.get('primary') or {}, env_prefix='AI_PRIMARY',
                                  default_preset=DEFAULT_PRIMARY_PRESET)
    secondary = _provider_from_spec('secondary', cfg.get('secondary') or {}, env_prefix='AI_SECONDARY',
                                    default_preset=DEFAULT_SECONDARY_PRESET)

    # v0.1 兼容：未显式配置 AI_PRIMARY_* / 平台配置时，允许沿用 LLM_*
    if not primary.api_key:
        primary.api_key = os.getenv('LLM_API_KEY', '')
        if primary.api_key:
            primary.key_source = 'env'
    if not (cfg.get('primary') or {}).get('baseUrl') and not os.getenv('AI_PRIMARY_BASE_URL'):
        primary.base_url = os.getenv('LLM_BASE_URL') or primary.base_url
    if not os.getenv('AI_PRIMARY_MODEL') and not (cfg.get('primary') or {}).get('model'):
        primary.default_model = os.getenv('LLM_MODEL') or primary.default_model

    return [p for p in (primary, secondary) if p.enabled and p.base_url and p.api_key]


def _allow_failover() -> bool:
    cfg = _db_ai_config()
    if 'allowFailover' in cfg:
        return _truthy(cfg.get('allowFailover'), True)
    return os.getenv('AI_ALLOW_FAILOVER', '1') == '1'


def _model_for_task(provider: ProviderConfig, task_type: str, *, has_images: bool = False) -> str:
    """路由由任务类型 + 是否带图决定，绝不因 Credits 余额降低模型。"""
    task = str(task_type or '').upper()
    preset = (provider.preset or '').upper()
    if has_images:
        candidates = [f'AI_{preset}_VISION_MODEL']
        if provider.role == 'secondary':
            candidates.append('AI_SECONDARY_VISION_MODEL')
        candidates.append('AI_VISION_MODEL')
        for key in candidates:
            value = os.getenv(key)
            if value:
                return value
        return provider.vision_model or provider.default_model
    if provider.role == 'secondary':
        value = os.getenv(f'AI_SECONDARY_MODEL_{task}')
        if value:
            return value
    for key in (f'AI_{preset}_MODEL_{task}', f'AI_MODEL_{task}'):
        value = os.getenv(key)
        if value:
            return value
    return provider.default_model


def _candidates(*, has_images: bool) -> list[ProviderConfig]:
    providers = _providers()
    if not providers:
        raise AIGatewayError(
            '总平台尚未配置可用的大模型 Provider。请在总平台「AI 模型接入」中启用 DeepSeek / 通义千问并填入 API Key。'
        )
    if has_images:
        vision = [p for p in providers if p.supports_vision and (p.vision_model or p.default_model)]
        if not vision:
            raise AIGatewayError(
                '本次生成包含图片理解，但当前平台模型都不具备视觉能力；请在总平台启用通义千问视觉模型（当前 qwen3-vl-plus）。'
            )
        providers = vision
    if not _allow_failover():
        providers = providers[:1]
    return providers


def gateway_status() -> dict[str, Any]:
    mode, mode_source = effective_gateway_mode()
    mock = mode == 'mock'
    providers = _providers()
    text_provider = providers[0] if providers else None
    vision_provider = next((p for p in providers if p.supports_vision), None)
    return {
        'mode': 'mock' if mock else 'live',
        'modeSource': mode_source,
        'modeLockedByProduction': _is_prod(),
        'savedMode': saved_gateway_mode() or AI_MODE_AUTO,
        'region': 'cn',
        'owner': 'platform',
        'principle': 'platform_owned_models; credits_only_bill; never_reduce_model_quality',
        'routing': {
            'text': text_provider.preset if text_provider else None,
            'vision': vision_provider.preset if vision_provider else None,
            'hasVision': bool(vision_provider),
        },
        'policy': {
            'clubCanConfigureModel': False,
            'clubUsageMeteredBy': 'ai_credits',
            'clubSeesApiKey': False,
        },
        'configSource': 'platform' if _db_ai_config() else 'env',
        'providers': [
            {
                'name': p.name,
                'role': p.role,
                'preset': p.preset,
                'label': p.label,
                'region': p.region,
                'configured': bool(p.api_key),
                'baseUrl': p.base_url,
                'defaultModel': p.default_model,
                'visionModel': p.vision_model,
                'supportsVision': p.supports_vision,
                'keySource': p.key_source,
            }
            for p in providers
        ],
        'failoverEnabled': _allow_failover(),
    }


# ---------------------------------------------------------------------------
# 总平台模型接入配置读写（只有平台角色可访问，见 app.py 路由）
# ---------------------------------------------------------------------------
def _mask_key(key: str) -> str:
    if not key:
        return ''
    if len(key) <= 10:
        return '***'
    return f'{key[:4]}***{key[-4:]}'


def _spec_view(role: str, spec: dict[str, Any], effective: ProviderConfig | None) -> dict[str, Any]:
    preset = str(spec.get('preset') or (effective.preset if effective else '') or '')
    return {
        'role': role,
        'preset': preset,
        'baseUrl': spec.get('baseUrl') or (effective.base_url if effective else ''),
        'model': spec.get('model') or (effective.default_model if effective else ''),
        'visionModel': spec.get('visionModel') or (effective.vision_model if effective else ''),
        'enabled': _truthy(spec.get('enabled', True), True),
        'apiKeyMasked': _mask_key(str(spec.get('apiKey') or '')),
        'apiKeySource': ('platform' if spec.get('apiKey') else (effective.key_source if effective else 'none')),
    }


def platform_provider_config() -> dict[str, Any]:
    cfg = _db_ai_config()
    effective = {p.role: p for p in _providers()}
    return {
        'presets': [
            {
                'code': code,
                'label': meta.get('label', code),
                'region': meta.get('region', 'any'),
                'baseUrl': meta.get('base_url', ''),
                'defaultModel': meta.get('default_model', ''),
                'visionModel': meta.get('vision_model', ''),
                'supportsVision': bool(meta.get('supports_vision')),
                'doc': meta.get('doc', ''),
            }
            for code, meta in PROVIDER_PRESETS.items()
        ],
        'saved': {
            'primary': _spec_view('primary', cfg.get('primary') or {}, effective.get('primary')),
            'secondary': _spec_view('secondary', cfg.get('secondary') or {}, effective.get('secondary')),
            'allowFailover': cfg.get('allowFailover'),
            'mode': saved_gateway_mode() or AI_MODE_AUTO,
        },
        'effective': gateway_status(),
        'note': '大模型只由总平台接入并结算；俱乐部按任务消耗 AI Credits，不需要也无法配置模型或密钥。',
    }


def update_platform_provider_config(payload: dict[str, Any]) -> dict[str, Any]:
    """总平台保存模型接入配置。缺省字段保持原值；apiKey 传 null 表示清空为使用环境变量。"""
    if not isinstance(payload, dict):
        raise ValueError('请求体必须是对象')
    cfg = _db_ai_config()
    for role in ('primary', 'secondary'):
        block = payload.get(role)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise ValueError(f'{role} 必须是对象')
        current = dict(cfg.get(role) or {})
        for field in ('preset', 'baseUrl', 'model', 'visionModel'):
            if field in block:
                value = block.get(field)
                current[field] = '' if value is None else str(value).strip()
        if 'preset' in block and current.get('preset') and current['preset'] not in PROVIDER_PRESETS:
            raise ValueError(f'不支持的 Provider 预设: {current["preset"]}')
        if 'enabled' in block:
            current['enabled'] = _truthy(block.get('enabled'), True)
        if 'apiKey' in block:
            value = block.get('apiKey')
            if value is None or str(value).strip() == '':
                current.pop('apiKey', None)
            else:
                current['apiKey'] = str(value).strip()
        if current.get('baseUrl') and not str(current['baseUrl']).startswith(('http://', 'https://')):
            raise ValueError(f'{role} baseUrl 必须以 http(s):// 开头')
        cfg[role] = current
    if 'allowFailover' in payload:
        cfg['allowFailover'] = _truthy(payload.get('allowFailover'), True)
    if 'mode' in payload:
        mode = str(payload.get('mode') or '').strip().lower()
        if mode not in AI_MODES:
            raise ValueError(f'mode 只能是 {"/".join(AI_MODES)}')
        if mode == AI_MODE_MOCK and _is_prod():
            raise ValueError('生产环境不允许切回 mock 演示模式')
        cfg['mode'] = mode
    with conn() as c:
        c.execute(
            'INSERT INTO platform_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (AI_PROVIDERS_SETTING_KEY, json.dumps(cfg, ensure_ascii=False)),
        )
    return platform_provider_config()


async def test_provider_connection(role: str = 'primary') -> dict[str, Any]:
    """总平台“连通性自检”：真实向该 Provider 发一次最小请求。"""
    role = str(role or 'primary')
    if role not in {'primary', 'secondary'}:
        return {'ok': False, 'error': 'role 只能是 primary 或 secondary'}
    if effective_gateway_mode()[0] == 'mock':
        return {
            'ok': False,
            'mode': 'mock',
            'error': '当前为 mock 演示模式，不会真实调用模型；请在总平台「AI 模型接入」里切换到「真实调用」，或设置 MOCK_AI=0 并配置密钥。',
        }
    matched = [p for p in _providers() if p.role == role]
    if not matched:
        return {'ok': False, 'error': f'{role} 未启用或缺少 API Key'}
    provider = matched[0]
    model = _model_for_task(provider, 'ping')
    try:
        async with _httpx_client(float(os.getenv('AI_TIMEOUT_SECONDS', '240'))) as client:
            r = await client.post(
                provider.base_url,
                headers={'Authorization': f'Bearer {provider.api_key}', 'Content-Type': 'application/json'},
                json={'model': model, 'messages': [{'role': 'user', 'content': 'ping'}], 'max_tokens': 8},
            )
            r.raise_for_status()
            raw = r.json()
        sample = ((raw.get('choices') or [{}])[0].get('message') or {}).get('content', '')
        return {'ok': True, 'role': role, 'preset': provider.preset, 'model': model, 'sample': str(sample)[:60]}
    except Exception as exc:
        return {'ok': False, 'role': role, 'preset': provider.preset, 'model': model, 'error': _explain(exc)[:400]}


# ---------------------------------------------------------------------------
# 用量记录 / 成本观测
# ---------------------------------------------------------------------------
def _estimate_tokens(text: str) -> int:
    # 仅用于 mock / 无 usage 返回时的成本观测，不参与上下文裁剪。
    return max(1, len(text) // 3)


def _cost_for(provider_name: str, input_tokens: int, output_tokens: int, *, preset: str = '') -> float:
    """平台成本核算（只影响平台毛利观测，绝不影响前台 Credits 定价与模型质量）。

    优先按国内厂商常见的人民币每百万 token 报价（CNY/1M），按基准汇率折算成
    USD 存储，以兼容既有 ai_usage_records.provider_cost 口径。
    """
    prefixes = [p for p in (re.sub(r'[^A-Z0-9]+', '_', str(preset).upper()),
                            re.sub(r'[^A-Z0-9]+', '_', str(provider_name).upper())) if p]
    for pfx in prefixes:
        in_cny = float(os.getenv(f'AI_{pfx}_INPUT_CNY_PER_1M', '0') or 0)
        out_cny = float(os.getenv(f'AI_{pfx}_OUTPUT_CNY_PER_1M', '0') or 0)
        if in_cny or out_cny:
            rate = float(os.getenv('AI_USD_CNY_RATE', '7.2') or 7.2)
            cny = (input_tokens * in_cny + output_tokens * out_cny) / 1_000_000
            return round(cny / rate, 8) if rate else 0.0
    for pfx in prefixes:
        in_rate = float(os.getenv(f'AI_{pfx}_INPUT_USD_PER_1M', '0') or 0)
        out_rate = float(os.getenv(f'AI_{pfx}_OUTPUT_USD_PER_1M', '0') or 0)
        if in_rate or out_rate:
            return round((input_tokens * in_rate + output_tokens * out_rate) / 1_000_000, 8)
    return 0.0


def _record_usage(*, club_id: int, task_type: str, request_id: str, provider: str, model: str,
                  status: str, input_tokens: int = 0, output_tokens: int = 0,
                  provider_cost: float = 0, error: str | None = None) -> int:
    with conn() as c:
        c.execute(
            '''INSERT INTO ai_usage_records(
                 club_id,task_type,request_id,provider,model,status,input_tokens,output_tokens,
                 provider_cost,credits_charged,error
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (club_id, task_type, request_id, provider, model, status, input_tokens, output_tokens,
             provider_cost, 0, error),
        )
        return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])


def record_mock_usage(club_id: int, task_type: str, prompt: str, output: dict[str, Any]) -> GatewayResponse:
    request_id = f'mock_{uuid.uuid4().hex}'
    input_tokens = _estimate_tokens(prompt)
    output_tokens = _estimate_tokens(json.dumps(output, ensure_ascii=False))
    usage_id = _record_usage(
        club_id=club_id, task_type=task_type, request_id=request_id,
        provider='mock', model='mock-full-capability', status='success',
        input_tokens=input_tokens, output_tokens=output_tokens, provider_cost=0,
    )
    return GatewayResponse(output, usage_id, 'mock', 'mock-full-capability', input_tokens, output_tokens, 0, request_id)


async def generate_json(*, club_id: int, task_type: str, system_prompt: str, user_prompt: str,
                        images: list[dict[str, Any]] | None = None) -> GatewayResponse | None:
    """
    总平台统一 AI Gateway：鉴权/路由/容灾/用量记录。

    策略（与产品文档一致）：
      - 模型只由平台接入，俱乐部只消耗 Credits；
      - 带图任务必须走具备视觉能力的 Provider（默认通义千问 qwen3-vl-plus）；
      - 绝不根据 Credits 余额裁剪资料、减少图片、降低模型或套固定模板。
    """
    if effective_gateway_mode()[0] == 'mock':
        return None

    has_images = bool(images)
    providers = _candidates(has_images=has_images)

    content: list[dict[str, Any]] = [{'type': 'text', 'text': user_prompt}]
    # 默认不限制用户上传图片数量。若未来某 Provider 有硬限制，应在该 Provider adapter 内显式处理，
    # 不能因为计费或套餐偷偷减少上下文。
    for img in (images or []):
        p = Path(img['path'])
        ext = p.suffix.lower().replace('.', '') or 'jpeg'
        mime_ext = 'jpeg' if ext in {'jpg', 'jpeg'} else ext
        data = base64.b64encode(p.read_bytes()).decode()
        content.append({'type': 'image_url', 'image_url': {'url': f'data:image/{mime_ext};base64,{data}'}})

    last_error: Exception | None = None
    for idx, provider in enumerate(providers):
        model = _model_for_task(provider, task_type, has_images=has_images)
        request_id = uuid.uuid4().hex
        started = time.time()
        try:
            payload = {
                'model': model,
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': content},
                ],
                'temperature': float(os.getenv('AI_TEMPERATURE', '0.8')),
            }
            async with _httpx_client(float(os.getenv('AI_TIMEOUT_SECONDS', '240'))) as client:
                r = await client.post(
                    provider.base_url,
                    headers={'Authorization': f'Bearer {provider.api_key}', 'Content-Type': 'application/json'},
                    json=payload,
                )
                r.raise_for_status()
                raw = r.json()
            txt = raw['choices'][0]['message']['content']
            data = _extract_json(txt)
            u = raw.get('usage') or {}
            input_tokens = int(u.get('prompt_tokens') or u.get('input_tokens') or _estimate_tokens(system_prompt + user_prompt))
            output_tokens = int(u.get('completion_tokens') or u.get('output_tokens') or _estimate_tokens(txt))
            provider_cost = _cost_for(provider.name, input_tokens, output_tokens, preset=provider.preset)
            usage_id = _record_usage(
                club_id=club_id, task_type=task_type, request_id=request_id,
                provider=provider.preset or provider.name, model=model, status='success',
                input_tokens=input_tokens, output_tokens=output_tokens, provider_cost=provider_cost,
            )
            return GatewayResponse(data, usage_id, provider.preset or provider.name, model,
                                   input_tokens, output_tokens, provider_cost, request_id)
        except Exception as exc:
            last_error = exc
            _record_usage(
                club_id=club_id, task_type=task_type, request_id=request_id,
                provider=provider.preset or provider.name, model=model, status='failed', error=_explain(exc)[:1000],
            )
            # 只有平台显式允许 failover 才切换备用 Provider；绝不因 Credits 余额自动降级。
            if idx == len(providers) - 1:
                break

    raise AIGatewayError(f'AI Gateway 调用失败: {_explain(last_error) if last_error else "未知错误"}')
