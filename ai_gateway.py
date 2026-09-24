from __future__ import annotations
import base64
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from db import conn


class AIGatewayError(RuntimeError):
    pass


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    default_model: str
    enabled: bool = True


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


def _providers() -> list[ProviderConfig]:
    # 兼容 v0.1 的 LLM_* 环境变量，同时正式采用平台级 AI Gateway 配置。
    primary = ProviderConfig(
        name=os.getenv('AI_PRIMARY_NAME', 'primary'),
        base_url=os.getenv('AI_PRIMARY_BASE_URL') or os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1/chat/completions'),
        api_key=os.getenv('AI_PRIMARY_API_KEY') or os.getenv('LLM_API_KEY', ''),
        default_model=os.getenv('AI_PRIMARY_MODEL') or os.getenv('LLM_MODEL', 'gpt-5.6'),
        enabled=os.getenv('AI_PRIMARY_ENABLED', '1') == '1',
    )
    secondary = ProviderConfig(
        name=os.getenv('AI_SECONDARY_NAME', 'secondary'),
        base_url=os.getenv('AI_SECONDARY_BASE_URL', ''),
        api_key=os.getenv('AI_SECONDARY_API_KEY', ''),
        default_model=os.getenv('AI_SECONDARY_MODEL', ''),
        enabled=os.getenv('AI_SECONDARY_ENABLED', '0') == '1',
    )
    return [p for p in (primary, secondary) if p.enabled and p.base_url and p.api_key]


def _model_for_task(provider: ProviderConfig, task_type: str) -> str:
    # 路由由任务类型决定，不由俱乐部剩余 Credits 决定。
    key = f'AI_MODEL_{task_type.upper()}'
    if provider.name == os.getenv('AI_SECONDARY_NAME', 'secondary'):
        key = f'AI_SECONDARY_MODEL_{task_type.upper()}'
    return os.getenv(key) or provider.default_model


def gateway_status() -> dict[str, Any]:
    mock = os.getenv('MOCK_AI', '1') == '1'
    providers = _providers()
    return {
        'mode': 'mock' if mock else 'live',
        'principle': 'credits_only_bill; never_reduce_model_quality',
        'providers': [
            {'name': p.name, 'configured': bool(p.api_key), 'baseUrl': p.base_url, 'defaultModel': p.default_model}
            for p in providers
        ],
        'failoverEnabled': os.getenv('AI_ALLOW_FAILOVER', '1') == '1',
    }


def _estimate_tokens(text: str) -> int:
    # 仅用于 mock / 无 usage 返回时的成本观测，不参与上下文裁剪。
    return max(1, len(text) // 3)


def _cost_for(provider_name: str, input_tokens: int, output_tokens: int) -> float:
    # 运营可自行配置每百万 token 成本；这只是平台成本核算，不影响前台 Credits 定价。
    pfx = re.sub(r'[^A-Z0-9]+', '_', provider_name.upper())
    in_rate = float(os.getenv(f'AI_{pfx}_INPUT_USD_PER_1M', '0') or 0)
    out_rate = float(os.getenv(f'AI_{pfx}_OUTPUT_USD_PER_1M', '0') or 0)
    return round((input_tokens * in_rate + output_tokens * out_rate) / 1_000_000, 8)


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
    平台统一 AI Gateway：鉴权/路由/容灾/用量记录。
    这里绝不根据 Credits 余额裁剪资料、减少图片、降低模型或套固定模板。
    """
    if os.getenv('MOCK_AI', '1') == '1':
        return None

    providers = _providers()
    if not providers:
        raise AIGatewayError('平台尚未配置可用的大模型 Provider')

    allow_failover = os.getenv('AI_ALLOW_FAILOVER', '1') == '1'
    if not allow_failover:
        providers = providers[:1]

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
        model = _model_for_task(provider, task_type)
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
            async with httpx.AsyncClient(timeout=float(os.getenv('AI_TIMEOUT_SECONDS', '240'))) as client:
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
            provider_cost = _cost_for(provider.name, input_tokens, output_tokens)
            usage_id = _record_usage(
                club_id=club_id, task_type=task_type, request_id=request_id,
                provider=provider.name, model=model, status='success',
                input_tokens=input_tokens, output_tokens=output_tokens, provider_cost=provider_cost,
            )
            return GatewayResponse(data, usage_id, provider.name, model, input_tokens, output_tokens, provider_cost, request_id)
        except Exception as exc:
            last_error = exc
            _record_usage(
                club_id=club_id, task_type=task_type, request_id=request_id,
                provider=provider.name, model=model, status='failed', error=str(exc)[:1000],
            )
            # 只有平台显式配置 secondary 才 failover；绝不因 Credits 余额自动降级。
            if idx == len(providers) - 1:
                break

    raise AIGatewayError(f'AI Gateway 调用失败: {last_error}')
