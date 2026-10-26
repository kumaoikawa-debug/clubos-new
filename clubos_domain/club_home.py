"""C 端首页「运营轮播」可配置区（v0.32）。

俱乐部老板在俱乐部后台自定义 C 端首页顶部那一块轮播：替换掉原本写死的「最近 5 场活动」
hero，换成自己运营想推的图。每条 banner 可以：

1. **自定义图片**（上传或填 http(s) 外链）；
2. **自定义文案层**：标题 / 副标题 / 角标 / 按钮文案（都可空，纯图片 banner 也允许）；
3. **自定义跳转目标**：点图跳到「小程序内指定活动」或「指定商品」（targetType=activity|product）。

设计三条铁律（与 club_biz 同一套思路）：

1. **未配置 / enabled=False → C 端整块回落默认活动轮播**。空壳轮播比「写死活动轮播」更糟，
   所以 `public_json` 在没启用或没条目时返回 None，前端据此回落到现有 hero 逻辑（不破坏旧行为）。
2. **图片必须走公开代理**。生产环境 `/static/uploads/*` 被封 404，公开视图里把
   `/static/uploads/x.jpg` 改写成 `/api/public/clubs/{id}/home-media/uploads/x.jpg`；
   代理端只放行 `home_banners_json` 里**真实引用过**的文件（白名单，不是 /static/uploads 通配）。
3. **形状不对的输入静默丢弃**。没图的条目（纯文字 banner 没意义）、非 http(s) 又非 /static/ 的图片、
   超长文本一律裁掉或丢掉 —— 落库的必须是能直接渲染的东西。
"""
from __future__ import annotations

import json
from urllib.parse import quote

MAX_ITEMS = 8
TITLE_MAX = 40
SUBTITLE_MAX = 80
BADGE_MAX = 20
BUTTON_MAX = 20
INTERVAL_MIN = 2        # 自动轮播最短 2 秒，避免闪到看不清
INTERVAL_MAX = 30       # 最长 30 秒，避免老板设成几年一换
INTERVAL_DEFAULT = 5    # 默认 5 秒

TARGET_TYPES = ('activity', 'product')

DEFAULT: dict = {'enabled': False, 'interval': INTERVAL_DEFAULT, 'items': []}


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


def _clamp_interval(value) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return INTERVAL_DEFAULT
    return max(INTERVAL_MIN, min(INTERVAL_MAX, v))


def normalize(payload: dict, current: dict | None = None) -> dict:
    """把后台提交的 payload 归一化成可落库的结构。

    `current` 是库里已有配置 —— 缺失的键回退当前值而非默认值（后台只改间隔时不能把 items 清空）。
    """
    base = dict(DEFAULT)
    if isinstance(current, dict):
        base.update({k: current.get(k, base.get(k)) for k in ('enabled', 'interval', 'items')})
        if not isinstance(base.get('items'), list):
            base['items'] = []
    p = payload if isinstance(payload, dict) else {}

    out = {
        'enabled': bool(p.get('enabled', base.get('enabled'))),
        'interval': _clamp_interval(p.get('interval', base.get('interval'))),
        'items': [],
    }
    if 'items' in p:
        raw = p.get('items') or []
        if not isinstance(raw, list):
            raise ValueError('items 必须是数组')
        for item in raw[:MAX_ITEMS]:
            if not isinstance(item, dict):
                continue
            image = _image(item.get('image'))
            if not image:
                continue  # 没图的 banner 渲染出来是一块空气，直接丢
            tt = str(item.get('targetType') or '').strip()
            if tt not in TARGET_TYPES:
                tt = ''
            tid = None
            if tt:
                try:
                    tid = int(item.get('targetId'))
                    if tid <= 0:
                        tid = None
                except (TypeError, ValueError):
                    tid = None
                if tid is None:
                    tt = ''  # 选了目标类型但没给有效 id → 当纯图片，不报错打断保存
            out['items'].append({
                'image': image,
                'title': _text(item.get('title'), TITLE_MAX),
                'subtitle': _text(item.get('subtitle'), SUBTITLE_MAX),
                'badge': _text(item.get('badge'), BADGE_MAX),
                'buttonText': _text(item.get('buttonText'), BUTTON_MAX),
                'targetType': tt,
                'targetId': tid,
            })
    else:
        out['items'] = [
            {
                'image': _image(i.get('image')),
                'title': _text(i.get('title'), TITLE_MAX),
                'subtitle': _text(i.get('subtitle'), SUBTITLE_MAX),
                'badge': _text(i.get('badge'), BADGE_MAX),
                'buttonText': _text(i.get('buttonText'), BUTTON_MAX),
                'targetType': str(i.get('targetType') or '') if str(i.get('targetType') or '') in TARGET_TYPES else '',
                'targetId': (int(i['targetId']) if (isinstance(i.get('targetId'), int) or (isinstance(i.get('targetId'), str) and str(i.get('targetId')).isdigit())) and int(i['targetId']) > 0 else None),
            }
            for i in (base.get('items') or []) if isinstance(i, dict) and _image(i.get('image'))
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
    return loads(club.get('home_banners_json'))


def media_paths(cfg: dict) -> set[str]:
    """配置里真实引用过的 /static/ 路径集合 —— 公开代理的白名单就是它。"""
    out = set()
    for item in (cfg or {}).get('items') or []:
        img = str(item.get('image') or '')
        if img.startswith('/static/'):
            out.add(img)
    return out


def media_proxy_url(club_id: int, original: str, ext_ok: bool = True) -> str:
    """`/static/uploads/a.jpg` → `/api/public/clubs/1/home-media/uploads/a.jpg`。"""
    body = str(original or '')[len('/static/'):]
    if not body or not ext_ok:
        return str(original or '')
    return '/api/public/clubs/%d/home-media/%s' % (int(club_id), quote(body, safe='/'))


def public_json(club_id: int, club: dict) -> str | None:
    """C 端视图：返回 JSON 文本（含代理化图片地址、间隔、条目）；未启用或没有条目 → None。

    返回 None 时前端整块回落到默认活动轮播 —— 这是「老板还没配」的唯一正确表达。
    """
    cfg = from_club(club)
    if not cfg.get('enabled') or not (cfg.get('items') or []):
        return None
    view = {
        'enabled': True,
        'interval': cfg.get('interval') or INTERVAL_DEFAULT,
        'items': [
            {
                'image': media_proxy_url(club_id, it.get('image') or '') if str(it.get('image') or '').startswith('/static/') else (it.get('image') or ''),
                'title': it.get('title') or '',
                'subtitle': it.get('subtitle') or '',
                'badge': it.get('badge') or '',
                'buttonText': it.get('buttonText') or '',
                'targetType': it.get('targetType') or '',
                'targetId': it.get('targetId'),
            }
            for it in cfg.get('items') or []
        ],
    }
    return json.dumps(view, ensure_ascii=False)


def public_view(club_id: int, club: dict) -> dict | None:
    """与 public_json 同源，但返回解析后的 dict（公开 API 端点用，避免前端再 JSON.parse）。"""
    s = public_json(club_id, club)
    if not s:
        return None
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return None
