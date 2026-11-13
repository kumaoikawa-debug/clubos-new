"""低代码界面装修器（v0.34）。

参考「远拓旅游 SaaS · 界面设置」的组件化思路，把 C 端页面拆成可配置的组件序列，
俱乐部后台可自由拼装「轮播图 / 导航宫格 / 活动列表 / 营销入口 / 公告栏 / 自由容器」。

与 club_home 同源的三铁律（沿用其设计哲学，避免空壳板块）：
1. 未配置 / status!=PUBLISHED → 公开视图返回 None，前端整块回落到原 hardcode（home_banners / 默认活动流）；
2. 图片走公开代理（/static/uploads → /api/public/clubs/{id}/page-media/...），与 club_home.public 同源；
3. 形状不对的输入静默丢弃（没图轮播 / 非法目标一律丢），落库必须是可直接渲染的东西。

数据形状：
- 后端 `page_layouts(club_id, page_key, title, status, schema_json)`；`schema_json` 存 components 数组（JSON）。
- 组件 v1：`carousel` / `nav_grid` / `activity_list` / `marketing_entry` / `notice` / `free_container`。

本模块是**纯函数**（不依赖 conn / 框架），DB 取行逻辑放在 app.py 路由里，方便独立单元测试。
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote

PAGE_KEYS = ('club_home', 'activity_detail')
STATUS_VALUES = ('DRAFT', 'PUBLISHED')
COMPONENT_TYPES = ('carousel', 'nav_grid', 'activity_list', 'marketing_entry', 'notice', 'free_container')

MAX_COMPONENTS = 20
MAX_ITEMS = 12
TITLE_MAX = 40
SUBTITLE_MAX = 80
TEXT_MAX = 120
BADGE_MAX = 20
BUTTON_MAX = 20
LINK_MAX = 500

TARGET_TYPES = ('activity', 'product', 'page', 'external', 'marketing', 'route')
MARKETING_LINKS = ('member', 'points', 'coupon', 'activity', 'invite')  # 营销入口类型


def _text(value, limit: int) -> str:
    return str(value if value is not None else '').strip()[:limit]


def _image(value) -> str:
    """只接受 /static/ 相对路径与 http(s) 绝对地址；其余（含 javascript:）一律丢掉。"""
    s = str(value if value is not None else '').strip()
    if not s:
        return ''
    if s.startswith('/static/'):
        return s
    if s.startswith('http://') or s.startswith('https://'):
        return s
    return ''


def _route_key(value):
    """P1-5：只接受形状合法的路由 key；真实合法性由 app.py 的 urlMap 决定。"""
    s = str(value if value is not None else '').strip()
    if not s or len(s) > 60:
        return ''
    return s if re.fullmatch(r'[A-Za-z0-9._-]+', s) else ''


def _target(raw_type, raw_id):
    """归一化跳转目标。活动/商品/页面需要有效 id，否则降级为纯展示。"""
    tt = str(raw_type or '').strip()
    tid = None
    if tt not in TARGET_TYPES:
        return '', None
    if tt in ('activity', 'product', 'page'):
        try:
            tid = int(raw_id)
            if tid <= 0:
                tid = None
        except (TypeError, ValueError):
            tid = None
        if tid is None:
            tt = ''  # 选了类型却没给有效 id → 当纯展示，不报错打断保存
    return tt, tid


def _normalize_carousel(props: dict) -> dict:
    items = []
    for it in (props.get('items') or [])[:MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        img = _image(it.get('image'))
        if not img:
            continue  # 没图的轮播渲染出来是一块空气，直接丢
        tt, tid = _target(it.get('targetType'), it.get('targetId'))
        items.append({
            'image': img,
            'title': _text(it.get('title'), TITLE_MAX),
            'subtitle': _text(it.get('subtitle'), SUBTITLE_MAX),
            'badge': _text(it.get('badge'), BADGE_MAX),
            'buttonText': _text(it.get('buttonText'), BUTTON_MAX),
            'targetType': tt,
            'targetId': tid,
        })
        tk = _route_key(it.get('targetKey'))
        if tk:
            items[-1]['targetKey'] = tk
    return {'items': items}


def _normalize_nav_grid(props: dict) -> dict:
    items = []
    for it in (props.get('items') or [])[:8]:
        if not isinstance(it, dict):
            continue
        label = _text(it.get('label'), TITLE_MAX)
        if not label:
            continue
        tt, tid = _target(it.get('targetType'), it.get('targetId'))
        icon = _image(it.get('icon')) or _text(it.get('icon'), 8)  # 允许 emoji 文本当图标
        items.append({'icon': icon, 'label': label, 'targetType': tt, 'targetId': tid})
        tk = _route_key(it.get('targetKey'))
        if tk:
            items[-1]['targetKey'] = tk
    return {'items': items}


def _normalize_activity_list(props: dict) -> dict:
    f = props.get('filter_json') if isinstance(props.get('filter_json'), dict) else {}
    limit = 6
    try:
        limit = max(1, min(MAX_ITEMS, int(f.get('limit', 6))))
    except (TypeError, ValueError):
        limit = 6
    return {
        'title': _text(props.get('title'), TITLE_MAX),
        'filter_json': {
            'kind': _text(f.get('kind'), 20),
            'tag': _text(f.get('tag'), 20),
            'limit': limit,
        },
    }


def _normalize_marketing_entry(props: dict) -> dict:
    items = []
    for it in (props.get('items') or [])[:8]:
        if not isinstance(it, dict):
            continue
        label = _text(it.get('label'), TITLE_MAX)
        if not label:
            continue
        lt = str(it.get('link_type') or '').strip()
        if lt not in MARKETING_LINKS:
            lt = ''
        icon = _image(it.get('icon')) or _text(it.get('icon'), 8)
        items.append({'label': label, 'icon': icon, 'link_type': lt,
                      'link_value': _text(it.get('link_value'), LINK_MAX)})
    return {'title': _text(props.get('title'), TITLE_MAX), 'items': items}


def _normalize_notice(props: dict) -> dict:
    items = []
    raw = props.get('items')
    if isinstance(raw, list):
        for it in raw[:MAX_ITEMS]:
            if not isinstance(it, dict):
                continue
            text = _text(it.get('text'), TEXT_MAX)
            if not text:
                continue
            items.append({'text': text, 'link': _text(it.get('link'), LINK_MAX)})
    else:
        text = _text(props.get('text'), TEXT_MAX)
        if text:
            items.append({'text': text, 'link': _text(props.get('link'), LINK_MAX)})
    return {'items': items}


def _normalize_free_container(props: dict) -> dict:
    return {
        'title': _text(props.get('title'), TITLE_MAX),
        'image': _image(props.get('image')),
        'content': _text(props.get('content'), 500),
        'link': _text(props.get('link'), LINK_MAX),
    }


_NORMALIZERS = {
    'carousel': _normalize_carousel,
    'nav_grid': _normalize_nav_grid,
    'activity_list': _normalize_activity_list,
    'marketing_entry': _normalize_marketing_entry,
    'notice': _normalize_notice,
    'free_container': _normalize_free_container,
}


def normalize_component(type_name, props) -> dict | None:
    """归一化单个组件；非法类型 / 空组件返回 None（调用方据此丢弃）。"""
    if type_name not in COMPONENT_TYPES:
        return None
    props = props if isinstance(props, dict) else {}
    try:
        norm = _NORMALIZERS[type_name](props)
    except Exception:
        return None
    if norm is None:
        return None
    # 轮播 / 宫格 / 公告 没内容就别渲染（空壳板块比没有更伤信任）
    if type_name in ('carousel', 'nav_grid', 'notice') and not norm.get('items'):
        return None
    return {'type': type_name, 'props': norm}


def normalize_layout(payload: dict) -> dict:
    """归一化整个页面布局。返回 {page_key, title, status, components} 或抛 ValueError。"""
    if not isinstance(payload, dict):
        raise ValueError('布局配置必须是对象')
    pk = str(payload.get('page_key') or '').strip()
    if pk not in PAGE_KEYS:
        raise ValueError('无效的 page_key（仅支持 %s）' % '/'.join(PAGE_KEYS))
    title = _text(payload.get('title'), TITLE_MAX)
    status = str(payload.get('status') or 'DRAFT').strip().upper()
    if status not in STATUS_VALUES:
        status = 'DRAFT'
    comps = []
    raw = payload.get('components')
    if raw is not None:
        if not isinstance(raw, list):
            raise ValueError('components 必须是数组')
        for comp in raw[:MAX_COMPONENTS]:
            if not isinstance(comp, dict):
                continue
            norm = normalize_component(comp.get('type'), comp.get('props') or {})
            if norm:
                comps.append(norm)
    return {'page_key': pk, 'title': title, 'status': status, 'components': comps}


def loads(raw):
    """从库里存的 JSON 文本还原 components；坏数据当作空列表而非抛异常。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [c for c in (normalize_component(c.get('type'), c.get('props') or {}) for c in data) if c]


def dumps(components: list) -> str:
    return json.dumps(components, ensure_ascii=False)


def media_paths(schema) -> set:
    """配置里真实引用过的 /static/ 路径集合 —— 公开代理的白名单就是它。"""
    comps = schema if isinstance(schema, list) else (loads(schema) if isinstance(schema, str) else [])
    out = set()
    for comp in comps:
        if not isinstance(comp, dict):
            continue
        props = comp.get('props') or {}
        t = comp.get('type')
        if t == 'carousel':
            for it in props.get('items') or []:
                img = str(it.get('image') or '')
                if img.startswith('/static/'):
                    out.add(img)
        elif t == 'nav_grid':
            for it in props.get('items') or []:
                ic = str(it.get('icon') or '')
                if ic.startswith('/static/'):
                    out.add(ic)
        elif t == 'marketing_entry':
            for it in props.get('items') or []:
                ic = str(it.get('icon') or '')
                if ic.startswith('/static/'):
                    out.add(ic)
        elif t == 'free_container':
            img = str(props.get('image') or '')
            if img.startswith('/static/'):
                out.add(img)
    return out


def media_proxy_url(club_id: int, original: str) -> str:
    """`/static/uploads/a.jpg` → `/api/public/clubs/1/page-media/uploads/a.jpg`。"""
    if not str(original or '').startswith('/static/'):
        return str(original or '')
    body = str(original)[len('/static/'):]
    return '/api/public/clubs/%d/page-media/%s' % (int(club_id), quote(body, safe='/'))


def _proxy_icon(club_id, icon):
    return media_proxy_url(club_id, icon) if str(icon or '').startswith('/static/') else icon


def _attach_route(items, route_urls):
    """P1-5：把装修器里选中的路由 key 解析成真实 URL。

    只在服务端传入 route_urls（已按模块开关 / tab 开关 / 隐藏过滤）时生效；
    被关闭的 key 不在 map 里 → targetUrl 为空 → C 端降级为不可点击。
    不传 route_urls 时行为与 v0.34 完全一致。
    """
    if not isinstance(route_urls, dict):
        return items
    out = []
    for i in items or []:
        d = dict(i)
        k = str(d.get('targetKey') or '')
        if k:
            d['targetUrl'] = route_urls.get(k, '') or ''
        out.append(d)
    return out


def render_public(club_id: int, schema, route_urls=None) -> list | None:
    """把落库 schema 转成公开渲染结构：图片走代理、结构清理。无组件返回 None。

    route_urls 是 P1-5 新增的**可选**参数（{routeKey: url}），不传时零行为变化。
    """
    comps = loads(schema) if isinstance(schema, str) else (schema or [])
    if not comps:
        return None
    out = []
    for comp in comps:
        t = comp.get('type')
        props = comp.get('props') or {}
        p = dict(props)
        if t == 'carousel':
            p['items'] = [dict(i, image=media_proxy_url(club_id, i.get('image'))) for i in props.get('items') or []]
            p['items'] = _attach_route(p['items'], route_urls)
        elif t in ('nav_grid', 'marketing_entry'):
            p['items'] = [dict(i, icon=_proxy_icon(club_id, i.get('icon'))) for i in props.get('items') or []]
            if t == 'nav_grid':
                p['items'] = _attach_route(p['items'], route_urls)
        elif t == 'free_container':
            if str(p.get('image') or '').startswith('/static/'):
                p['image'] = media_proxy_url(club_id, p.get('image'))
        out.append({'type': t, 'props': p})
    return out
