"""C 端「业务介绍」可配置区（v0.29）。

俱乐部老板在俱乐部后台自定义这一块：很多俱乐部除了常规户外线路，还有团建、
研学、企业团、装备租赁等业务。这里的规则就三条：

1. **未配置 / enabled=False → C 端整节不渲染**。空壳板块比没有板块更伤信任：
   顾客看到「业务介绍」四个字下面空着，会以为这个俱乐部没在运营。
2. **图片必须走公开代理**。生产环境 `/static/uploads/*` 被封 404，所以公开视图里把
   `/static/uploads/x.jpg` 改写成 `/api/public/clubs/{id}/biz-media/uploads/x.jpg`；
   该代理只放行 `biz_section_json` 里**真实引用过**的文件（与活动媒体代理同一套白名单思路，
   不做「/static/uploads 通配开放」那种越权口子）。
3. **形状不对的输入静默丢弃，不做二次猜测**。没标题的条目、非 http(s) 又非 /static/ 的图片、
   超长文本一律裁掉或丢掉 —— 库里存进去的必须是可以直接渲染的东西。
"""
from __future__ import annotations

import json
from urllib.parse import quote

MAX_ITEMS = 8
TITLE_MAX = 40
INTRO_MAX = 200
DESC_MAX = 200
DEFAULT_TITLE = '业务介绍'

DEFAULT: dict = {'enabled': False, 'title': DEFAULT_TITLE, 'intro': '', 'items': []}


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


def normalize(payload: dict, current: dict | None = None) -> dict:
    """把后台提交的 payload 归一化成可落库的结构。

    `current` 是库里已有的配置 —— 缺失的键回退当前值，而不是回退默认值：
    后台只改标题时不能把 items 清空（`payload.get(k) or 默认` 这种写法会静默丢数据）。
    """
    base = dict(DEFAULT)
    if isinstance(current, dict):
        base.update({k: current.get(k, base.get(k)) for k in DEFAULT})
    p = payload if isinstance(payload, dict) else {}

    out = {
        'enabled': bool(p.get('enabled', base.get('enabled'))),
        'title': _text(p.get('title', base.get('title')), TITLE_MAX) or DEFAULT_TITLE,
        'intro': _text(p.get('intro', base.get('intro')), INTRO_MAX),
        'items': [],
    }
    if 'items' in p:
        raw = p.get('items') or []
        if not isinstance(raw, list):
            raise ValueError('items 必须是数组')
        for item in raw[:MAX_ITEMS]:
            if not isinstance(item, dict):
                continue
            title = _text(item.get('title'), TITLE_MAX)
            if not title:
                continue  # 没标题的条目渲染出来是一张空气卡，直接丢
            out['items'].append({
                'title': title,
                'desc': _text(item.get('desc'), DESC_MAX),
                'image': _image(item.get('image')),
            })
    else:
        out['items'] = [
            {'title': _text(i.get('title'), TITLE_MAX),
             'desc': _text(i.get('desc'), DESC_MAX),
             'image': _image(i.get('image'))}
            for i in (base.get('items') or []) if isinstance(i, dict) and _text(i.get('title'), TITLE_MAX)
        ]
    return out


def loads(raw) -> dict:
    """从库里存的 JSON 文本还原配置；坏数据当作「未配置」而不是抛异常。"""
    if not raw:
        return dict(DEFAULT)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return dict(DEFAULT)
    if not isinstance(data, dict):
        return dict(DEFAULT)
    return normalize(data, None)


def dumps(cfg: dict) -> str:
    return json.dumps(cfg, ensure_ascii=False)


def from_club(club: dict) -> dict:
    return loads(club.get('biz_section_json'))


def media_paths(cfg: dict) -> set[str]:
    """配置里真实引用过的 /static/ 路径集合 —— 公开代理的白名单就是它。"""
    out = set()
    for item in (cfg or {}).get('items') or []:
        img = str(item.get('image') or '')
        if img.startswith('/static/'):
            out.add(img)
    return out


def media_proxy_url(club_id: int, original: str, ext_ok: bool = True) -> str:
    """`/static/uploads/a.jpg` → `/api/public/clubs/1/biz-media/uploads/a.jpg`。"""
    body = str(original or '')[len('/static/'):]
    if not body or not ext_ok:
        return str(original or '')
    return '/api/public/clubs/%d/biz-media/%s' % (int(club_id), quote(body, safe='/'))


def public_json(club_id: int, club: dict) -> str | None:
    """C 端视图：返回 JSON 文本（含代理化图片地址）；未启用或没有条目 → None。

    返回 None 时前端整节隐藏 —— 这是「老板还没配」的唯一正确表达。
    """
    cfg = from_club(club)
    if not cfg.get('enabled') or not (cfg.get('items') or []):
        return None
    view = {
        'enabled': True,
        'title': cfg.get('title') or DEFAULT_TITLE,
        'intro': cfg.get('intro') or '',
        'items': [
            {
                'title': it.get('title') or '',
                'desc': it.get('desc') or '',
                'image': media_proxy_url(club_id, it.get('image') or '') if str(it.get('image') or '').startswith('/static/') else (it.get('image') or ''),
            }
            for it in cfg.get('items') or []
        ],
    }
    return json.dumps(view, ensure_ascii=False)
