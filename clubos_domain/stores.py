"""门店 / 基地 / 分队实体（v0.35，P0-3）。

户外俱乐部常有多据点（总部 / 分部 / 活动基地 / 分队）。参考「远拓旅游 SaaS · 门店设置」，
把"门店"从现有纯权限维度 store_scope（this_club/all_clubs/specified）升级为**真实实体**，
作为活动、营销活动、商品、权益等"门店维度"的统一挂靠点。

三铁律（沿用 club_home / page_layout 的设计哲学）：
1. 未配置门店 → 视为单店（俱乐部自身），所有跨店查询回落 club 维度；
2. 字段非法 / 超长静默裁剪；
3. 坐标等敏感字段仅当合法时保存。

本模块是**纯函数**（不依赖 conn / 框架），DB 取行逻辑放在 app.py 路由里，方便独立单元测试。
"""
from __future__ import annotations

import json

NAME_MAX = 40
ADDR_MAX = 120
TEXT_MAX = 200
PHONE_MAX = 20
LAT_MIN, LAT_MAX = -90.0, 90.0
LNG_MIN, LNG_MAX = -180.0, 180.0
STATUS_VALUES = ('ACTIVE', 'INACTIVE')


def _text(v, limit: int) -> str:
    return str(v if v is not None else '').strip()[:limit]


def _coord(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _norm_geo(v):
    if not isinstance(v, dict):
        return None
    lat = _coord(v.get('lat'))
    lng = _coord(v.get('lng'))
    if lat is None or lng is None:
        return None
    if not (LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX):
        return None
    return {'lat': round(lat, 6), 'lng': round(lng, 6), 'label': _text(v.get('label'), TEXT_MAX)}


def _norm_hours(v):
    if not isinstance(v, list):
        return []
    out = []
    for h in v[:7]:
        if not isinstance(h, dict):
            continue
        day = h.get('day')
        if not isinstance(day, int) or day < 0 or day > 6:
            continue
        out.append({
            'day': day,
            'open': _text(h.get('open'), 5),
            'close': _text(h.get('close'), 5),
            'closed': bool(h.get('closed')),
        })
    return out


def _norm_notice(v):
    if not isinstance(v, dict):
        return None
    return {'title': _text(v.get('title'), NAME_MAX), 'content': _text(v.get('content'), 500)}


def normalize_store(payload: dict, current: dict | None = None) -> dict:
    """归一化门店字段，返回可直接落库的列字典。name 必填；非法输入抛 ValueError。"""
    if not isinstance(payload, dict):
        raise ValueError('门店配置必须是对象')
    base = current if isinstance(current, dict) else {}
    out = {
        'name': _text(payload.get('name', base.get('name')), NAME_MAX),
        'address': _text(payload.get('address', base.get('address')), ADDR_MAX),
        'phone': _text(payload.get('phone', base.get('phone')), PHONE_MAX),
        'geo_json': _norm_geo(payload.get('geo_json', base.get('geo_json'))),
        'hours_json': _norm_hours(payload.get('hours_json', base.get('hours_json'))),
        'notice_json': _norm_notice(payload.get('notice_json', base.get('notice_json'))),
        'status': str(payload.get('status', base.get('status') or 'ACTIVE')).strip().upper(),
    }
    if not out['name']:
        raise ValueError('门店名称必填')
    if out['status'] not in STATUS_VALUES:
        out['status'] = 'ACTIVE'
    return out


def dumps(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def loads(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}
