"""C 端「我的」页文案配置：联系我们 / 关于俱乐部（2026-10-23）。

顾客在 C 端「我的」里点开的两个页面，原来是**写死在前端**的：
「服务时间 9:00 - 21:00」「户外俱乐部一站式服务平台」「版本 v0.25」「官网 topoutdoor.cn」——
全是占位文案。不同俱乐部当然有自己的电话、微信、营业时间、门店地址和品牌故事，
所以这两页必须由俱乐部老板在后台自己定义。

规则三条（与 club_biz 同一套思路）：

1. **没填就不渲染，绝不编造**。空着的「服务时间」不会回退成 9:00-21:00：
   顾客照着假时段打过去没人接，比不显示更伤信任。前端只在值非空时才出这一行。
2. **只收能直接渲染的扁平结构**。所有字段都是短文本 + 一个亮点标签数组；
   地址/官网这类值做形状校验（官网只认 http(s)），形状不对就丢弃。
3. **未配置 = 合法状态**。`public_view` 返回空字典，前端回落到「只有名字 + 版本」
   的最小页面；这比拿默认文案把页面填满更诚实。

数据形状：
    {
      "contact": {"phone","wechat","email","hours","address","note"},
      "about":   {"tagline","intro","website","since","highlights":[...]}
    }
"""
from __future__ import annotations

import json
import re

MAX_HIGHLIGHTS = 6

# 各字段上限：都是「一行能读完」的量级，超出即裁剪（不让一条超长文案把页面顶爆）
LIMITS = {
    'phone': 30,
    'wechat': 40,
    'email': 80,
    'hours': 60,
    'address': 80,
    'note': 300,
    'tagline': 40,
    'intro': 1500,
    'website': 120,
    'since': 12,
}
HIGHLIGHT_MAX = 24

DEFAULT: dict = {
    'contact': {'phone': '', 'wechat': '', 'email': '', 'hours': '', 'address': '', 'note': ''},
    'about': {'tagline': '', 'intro': '', 'website': '', 'since': '', 'highlights': []},
}

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _text(value, limit: int) -> str:
    """统一成单行/多行文本：去掉首尾空白、把 \r\n 归一成 \n、按上限裁剪。"""
    s = str(value if value is not None else '')
    s = s.replace('\r\n', '\n').replace('\r', '\n').strip()
    return s[:limit]


def _one_line(value, limit: int) -> str:
    """单行字段（电话/微信/时间/地址）：内部换行压成空格。"""
    return re.sub(r'\s+', ' ', _text(value, limit * 2)).strip()[:limit]


def _website(value) -> str:
    s = _one_line(value, LIMITS['website'])
    if not s:
        return ''
    if s.startswith('http://') or s.startswith('https://'):
        return s
    # 用户习惯直接写域名：补 https://，但不接受 javascript: 这类伪协议
    if re.match(r'^[a-z0-9-]+(\.[a-z0-9-]+)+([/?#].*)?$', s, re.I):
        return 'https://' + s
    return ''


def _email(value) -> str:
    s = _one_line(value, LIMITS['email'])
    return s if _EMAIL_RE.match(s) else ''


def _phone(value) -> str:
    """电话：保留数字/+/-/空格，其余（含 tel: 前缀、括号）去掉，避免 tel: 链接坏掉。"""
    s = _one_line(value, LIMITS['phone'])
    if not s:
        return ''
    cleaned = re.sub(r'[^0-9+\-\s]', '', s).strip()
    # 至少 5 位数字才算一个电话号码，否则视为没填（防止「无」这种噪声变成假号码）
    return cleaned if len(re.sub(r'\D', '', cleaned)) >= 5 else ''


def _highlights(value) -> list:
    """亮点标签：允许传数组或换行/顿号分隔的字符串；空项丢弃、去重、截断到 6 条。"""
    raw = []
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = re.split(r'[\n、,，;；]+', value)
    out = []
    for item in raw:
        t = _one_line(item, HIGHLIGHT_MAX * 2)
        if not t:
            continue
        t = t[:HIGHLIGHT_MAX]
        if t not in out:
            out.append(t)
        if len(out) >= MAX_HIGHLIGHTS:
            break
    return out


def normalize(payload: dict, current: dict | None = None) -> dict:
    """把后台提交的 payload 归一化成可落库结构。

    `current` 是库里已有的配置：payload 里没出现的键**保留当前值**，而不是清空 ——
    后台可能只提交其中一块（比如只改「关于」），不能顺带把「联系我们」抹掉。
    """
    base = {
        'contact': dict(DEFAULT['contact']),
        'about': dict(DEFAULT['about'], highlights=[]),
    }
    if isinstance(current, dict):
        for block in ('contact', 'about'):
            cur = current.get(block)
            if isinstance(cur, dict):
                if block == 'contact':
                    base['contact'].update({k: _one_line(cur.get(k), LIMITS[k]) for k in DEFAULT['contact']})
                else:
                    base['about'].update({
                        'tagline': _one_line(cur.get('tagline'), LIMITS['tagline']),
                        'intro': _text(cur.get('intro'), LIMITS['intro']),
                        'website': _website(cur.get('website')),
                        'since': _one_line(cur.get('since'), LIMITS['since']),
                        'highlights': _highlights(cur.get('highlights')),
                    })

    p = payload if isinstance(payload, dict) else {}
    out = {'contact': dict(base['contact']), 'about': dict(base['about'])}

    contact = p.get('contact')
    if isinstance(contact, dict):
        for k in ('phone', 'wechat', 'hours', 'address', 'note'):
            if k in contact:
                out['contact'][k] = _one_line(contact.get(k), LIMITS[k]) if k != 'note' else _text(contact.get(k), LIMITS['note'])
        if 'email' in contact:
            out['contact']['email'] = _email(contact.get('email'))
        if 'phone' in contact:
            out['contact']['phone'] = _phone(contact.get('phone'))
    elif contact is not None:
        raise ValueError('contact 必须是对象')

    about = p.get('about')
    if isinstance(about, dict):
        if 'tagline' in about:
            out['about']['tagline'] = _one_line(about.get('tagline'), LIMITS['tagline'])
        if 'intro' in about:
            out['about']['intro'] = _text(about.get('intro'), LIMITS['intro'])
        if 'website' in about:
            out['about']['website'] = _website(about.get('website'))
        if 'since' in about:
            out['about']['since'] = _one_line(about.get('since'), LIMITS['since'])
        if 'highlights' in about:
            out['about']['highlights'] = _highlights(about.get('highlights'))
    elif about is not None:
        raise ValueError('about 必须是对象')

    return out


def loads(raw) -> dict:
    """从库里存的 JSON 还原；坏数据当作「未配置」，不让一条脏记录把设置页打挂。"""
    if not raw:
        return normalize({}, None)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return normalize({}, None)
    if not isinstance(data, dict):
        return normalize({}, None)
    try:
        return normalize(data, None)
    except ValueError:
        return normalize({}, None)


def dumps(cfg: dict) -> str:
    return json.dumps(cfg, ensure_ascii=False)


def from_club(club: dict) -> dict:
    return loads(club.get('c_profile_json'))


def is_empty(cfg: dict) -> bool:
    c = (cfg or {}).get('contact') or {}
    a = (cfg or {}).get('about') or {}
    if any(str(c.get(k) or '').strip() for k in DEFAULT['contact']):
        return False
    if any(str(a.get(k) or '').strip() for k in ('tagline', 'intro', 'website', 'since')):
        return False
    return not (a.get('highlights') or [])


def public_view(club: dict) -> dict:
    """C 端视图：只下发**真的有值**的字段（空字符串一律不出现）。

    「关于」的一句话简介没单独填时，用品牌口号兜底：两个字段想说的是同一件事，
    让老板少填一次；口号也是空的那就不显示这一行。
    """
    cfg = from_club(club)
    contact = {k: v for k, v in (cfg.get('contact') or {}).items() if str(v or '').strip()}
    about = {k: v for k, v in (cfg.get('about') or {}).items() if str(v or '').strip()}
    if not about.get('tagline'):
        slogan = str(club.get('slogan') or '').strip()
        if slogan:
            about['tagline'] = slogan
    if not about.get('highlights'):
        about.pop('highlights', None)
    return {'contact': contact, 'about': about}
