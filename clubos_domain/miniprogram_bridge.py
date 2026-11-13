"""小程序对接桥接层（v0.44，P2-3，路线图最后一项）。

参考「远拓旅游 SaaS · 配置管理 → 小程序对接」的形态，但**只补缺口、不重做台账**：

项目里已经有一整套小程序资产管理（`club_miniprogram` 认证资料、`club_mp_roles/staff`
管理助手、`club_mp_settings` 标准设置、图片上传、`/api/public/wechat/login` 一键登录），
schema 注释也写明「与微信开放平台真实对接（授权托管 / 代码上传 / 发布）是后续版本」。
P0~P2-2 新增的营销与配置能力散落在十几个 `/api/public/*` 端点里，小程序端**没有一个
统一的启动入口**，也不知道哪些能力开着、该跳哪里。本模块补的就是这一层桥接。

**四条边界（与 P0~P2-2 同纪律）**
1. **不编造页面**：能力清单只登记**代码里真实存在**的 API / H5 路径；小程序原生页
   `pages/...` 由俱乐部在后台「绑定」，默认一律为空并自动回落到真实 H5 / API 路径。
   绝不假装项目里有一套写好的小程序原生页面。
2. **不搬数据、不做第二套真相**：模块开关复用 settings_hub（回调注入）、小程序资料
   复用 `club_miniprogram`、设置复用 `club_mp_settings`，本模块只存「桥接编排」。
3. **零侵入结算**：桥接层全只读聚合，不写任何余额 / 订单 / payable / checkout。
4. **三铁律**：未配置 / 表缺失 / 回调异常 / 坏输入 → 静默回落默认，绝不阻断主流程。

**安全**：路径只接受站内相对路径与小程序 `pages/...` 形式，拒绝任何协议 / 外链 /
协议相对 URL；`{club_id}` 在服务端替换，绝不接受客户端传完整 URL（防开放重定向）。
密钥不入库、不进本模块：`secret_configured` 由 app.py 读环境变量后传布尔值。

DAO 函数只吃 cursor，由调用方在 `with conn() as c` 事务内调用，不自行 commit。
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timedelta, timezone

# ── 常量 ────────────────────────────────────────────────────────────────────
LAUNCH_MODES = ('native', 'h5')
DEFAULT_TABS = ('home', 'activity', 'mall', 'camp', 'bizIntro', 'me')
CAP_AUDIENCES = ('web', 'club')

MAX_PATH_LEN = 160
MAX_NOTE_LEN = 120
MAX_NOTICE_LEN = 200
MAX_SORT = 9999
MAX_CHECKS = 100
MAX_AUDIT = 200

# 小程序原生页：pages/xxx / subpackages/xxx，可带 query；拒绝协议 / 外链 / 反斜杠 / 空白。
_NATIVE_PATH_RE = re.compile(r'^(?:pages|subpackages)/[A-Za-z0-9_\-./]{1,120}(?:\?[A-Za-z0-9_\-.&=%{}]+)?$')
# 站内 H5 / API 回落：必须以 / 开头、单个斜杠、白名单前缀。
_FALLBACK_PREFIXES = ('/web', '/api/public/', '/api/club/', '/static/club/')

_INVALID = object()


def dumps(o) -> str:
    try:
        return json.dumps(o if o is not None else {}, ensure_ascii=False, separators=(',', ':'))
    except Exception:
        return '{}'


def loads(s, fallback=None):
    try:
        v = json.loads(s) if s else None
        return v if v is not None else (fallback if fallback is not None else {})
    except Exception:
        return fallback if fallback is not None else {}


def _int(v, default=0, lo=0, hi=10 ** 9):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n if lo <= n <= hi else default


def _str(v, limit=MAX_NOTE_LEN) -> str:
    try:
        return str(v or '').strip()[:limit]
    except Exception:
        return ''


def _now() -> int:
    return int(time.time())


def _ts_to_str(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).isoformat(timespec='seconds')
    except Exception:
        return ''


def business_now() -> str:
    """UTC+8 本地时间字符串，供后台展示。"""
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


# ── 能力清单（只登记真实存在的端点，2026-11-13 逐条核对过 app.py）────────────
# audience: web=小程序可见并进 bootstrap / club=后台对接台账，不进 bootstrap
# module:   settings_hub 模块 key，用于「总开关联动」；None 表示不受开关影响
# fallback: 未绑定小程序原生页时**真实可用**的 H5 / API 路径
# api:      能力真实端点（含 {club_id} / {activity_id} 模板，服务端替换 club_id）
GROUPS = (
    {'key': 'core', 'label': '基础与门户', 'order': 10},
    {'key': 'commerce', 'label': '交易与营销', 'order': 20},
    {'key': 'engagement', 'label': '互动玩法', 'order': 30},
    {'key': 'p2', 'label': '增长与权益', 'order': 40},
    {'key': 'admin', 'label': '后台对接', 'order': 50},
)

CAPABILITIES = (
    {'key': 'home', 'label': '小程序首页', 'group': 'core', 'audience': 'web',
     'phase': '既有', 'module': None, 'auth': 'public', 'icon': '🏠', 'order': 10,
     'fallback': '/web?club_id={club_id}', 'api': '/api/public/clubs/{club_id}/home-banners',
     'note': 'H5 首页；装修版面挂载点也在首页'},
    {'key': 'homeBanners', 'label': '首页装修位', 'group': 'core', 'audience': 'web',
     'phase': 'P0-2', 'module': 'pageLayout', 'auth': 'public', 'icon': '🖼️', 'order': 20,
     'fallback': '/api/public/clubs/{club_id}/home-banners',
     'api': '/api/public/clubs/{club_id}/home-banners',
     'note': '轮播 / 图文位；受装修器总开关联动'},
    {'key': 'settingsHub', 'label': '门户呈现偏好', 'group': 'core', 'audience': 'web',
     'phase': 'P1-4', 'module': None, 'auth': 'public', 'icon': '⚙️', 'order': 30,
     'fallback': '/api/public/clubs/{club_id}/settings-hub',
     'api': '/api/public/clubs/{club_id}/settings-hub',
     'note': '脱敏下发首页呈现方式与模块开关'},
    {'key': 'routes', 'label': '功能入口路由表', 'group': 'core', 'audience': 'web',
     'phase': 'P1-5', 'module': None, 'auth': 'public', 'icon': '🧭', 'order': 40,
     'fallback': '/api/public/clubs/{club_id}/routes/web',
     'api': '/api/public/clubs/{club_id}/routes/{scope}',
     'note': 'C 端导航宫格；scope 由服务端注入，不接受客户端 URL'},
    {'key': 'wechatLogin', 'label': '微信一键登录', 'group': 'core', 'audience': 'web',
     'phase': '既有', 'module': None, 'auth': 'public', 'icon': '🔑', 'order': 50,
     'fallback': '/api/public/wechat/login', 'api': '/api/public/wechat/login',
     'note': 'wx.login code → 会员 token；密钥走环境变量不入库'},

    {'key': 'activities', 'label': '活动与分类', 'group': 'commerce', 'audience': 'web',
     'phase': '既有', 'module': None, 'auth': 'public', 'icon': '⛰️', 'order': 110,
     'fallback': '/web?club_id={club_id}#view=wactivities',
     'api': '/api/public/clubs/{club_id}/activity-categories', 'note': '活动分类与列表'},
    {'key': 'priceQuote', 'label': '报价与营销活动', 'group': 'commerce', 'audience': 'web',
     'phase': 'P0-1', 'module': 'campaigns', 'auth': 'public', 'icon': '🏷️', 'order': 120,
     'fallback': '/web?club_id={club_id}#view=wactivities',
     'api': '/api/public/activities/{activity_id}/price-quote',
     'note': '首单立减 / 限时折扣作用在报价链路；{activity_id} 需业务侧注入'},
    {'key': 'storedValue', 'label': '会员储值', 'group': 'commerce', 'audience': 'web',
     'phase': 'P1-1', 'module': 'storedValue', 'auth': 'login', 'icon': '💰', 'order': 130,
     'fallback': '/api/public/clubs/{club_id}/stored-value/my',
     'api': '/api/public/clubs/{club_id}/stored-value/my', 'note': '余额 / 充值 / 规则'},
    {'key': 'groupBuy', 'label': '拼团', 'group': 'commerce', 'audience': 'web',
     'phase': 'P1-3', 'module': 'groupBuy', 'auth': 'public', 'icon': '👥', 'order': 140,
     'fallback': '/api/public/clubs/{club_id}/group-buys/campaigns',
     'api': '/api/public/clubs/{club_id}/group-buys/campaigns',
     'note': '拼团价只读试算，不改 payable'},
    {'key': 'storeProducts', 'label': '门店商品', 'group': 'commerce', 'audience': 'web',
     'phase': 'P0-3', 'module': 'stores', 'auth': 'public', 'icon': '🏬', 'order': 150,
     'fallback': '/api/public/clubs/{club_id}/store-products',
     'api': '/api/public/clubs/{club_id}/store-products', 'note': '门店 / 基地在售商品'},

    {'key': 'checkin', 'label': '每日签到', 'group': 'engagement', 'audience': 'web',
     'phase': 'P1-2', 'module': 'checkin', 'auth': 'login', 'icon': '📅', 'order': 210,
     'fallback': '/api/public/clubs/{club_id}/engagement/status',
     'api': '/api/public/clubs/{club_id}/engagement/status', 'note': '签到状态 / 连续天数'},
    {'key': 'lottery', 'label': '幸运抽奖', 'group': 'engagement', 'audience': 'web',
     'phase': 'P1-2', 'module': 'lottery', 'auth': 'login', 'icon': '🎡', 'order': 220,
     'fallback': '/api/public/clubs/{club_id}/engagement/config',
     'api': '/api/public/clubs/{club_id}/engagement/config', 'note': '抽奖规则与奖品'},
    {'key': 'stamp', 'label': '集章兑换', 'group': 'engagement', 'audience': 'web',
     'phase': 'P1-2', 'module': 'stamp', 'auth': 'login', 'icon': '🎯', 'order': 230,
     'fallback': '/api/public/clubs/{club_id}/engagement/config',
     'api': '/api/public/clubs/{club_id}/engagement/config', 'note': '集章规则与兑换'},

    {'key': 'affiliate', 'label': '推广中心', 'group': 'p2', 'audience': 'web',
     'phase': 'P2-1', 'module': 'affiliate', 'auth': 'login', 'icon': '🤝', 'order': 310,
     'fallback': '/api/public/clubs/{club_id}/affiliate/my',
     'api': '/api/public/clubs/{club_id}/affiliate/my', 'note': '推广码 / 佣金；未挂 checkout'},
    {'key': 'giftCard', 'label': '礼品卡', 'group': 'p2', 'audience': 'web',
     'phase': 'P2-2', 'module': 'giftCard', 'auth': 'login', 'icon': '🎁', 'order': 320,
     'fallback': '/api/public/clubs/{club_id}/gift-cards/my',
     'api': '/api/public/clubs/{club_id}/gift-cards/my',
     'note': '卡密兑换 / 余额；核销由显式端点触发'},

    {'key': 'notify', 'label': '通知通道', 'group': 'admin', 'audience': 'club',
     'phase': 'P0-4', 'module': 'notifyChannels', 'auth': 'club', 'icon': '🔔', 'order': 410,
     'fallback': '/api/club/{club_id}/notify', 'api': '/api/club/{club_id}/notify',
     'note': '模板 / 通道 / 站内信；仅后台侧，不进小程序 bootstrap'},
    {'key': 'storesAdmin', 'label': '门店管理', 'group': 'admin', 'audience': 'club',
     'phase': 'P0-3', 'module': 'stores', 'auth': 'club', 'icon': '🏕️', 'order': 420,
     'fallback': '/static/club/stores.html', 'api': '/static/club/stores.html',
     'note': '后台页；仅台账登记'},
    {'key': 'pageDecorator', 'label': '界面装修器', 'group': 'admin', 'audience': 'club',
     'phase': 'P0-2', 'module': 'pageLayout', 'auth': 'club', 'icon': '🎨', 'order': 430,
     'fallback': '/static/club/page-decorator.html', 'api': '/static/club/page-decorator.html',
     'note': '后台页；仅台账登记'},
)

CAP_BY_KEY = {x['key']: x for x in CAPABILITIES}
WEB_KEYS = tuple(x['key'] for x in CAPABILITIES if x['audience'] == 'web')
MODULE_KEYS = tuple(sorted({x['module'] for x in CAPABILITIES if x.get('module')}))


# ── 路径校验（安全核心）────────────────────────────────────────────────────
def normalize_page_path(v):
    """小程序原生页路径归一。

    返回 '' 表示「清除绑定」（回落真实 H5 / API），返回字符串表示合法绑定，
    返回 None 表示**非法输入**（协议 / 外链 / 协议相对 / 控制字符 / 过长）。
    绝不把客户端 URL 原样下发（防开放重定向）。
    """
    if v is None:
        return ''
    s = _str(v, MAX_PATH_LEN + 20)
    if not s:
        return ''
    if len(s) > MAX_PATH_LEN:
        return None
    low = s.lower()
    if ('://' in s or s.startswith('//') or '\\' in s or
            low.startswith(('javascript:', 'data:', 'vbscript:', 'file:')) or
            any(ch.isspace() for ch in s) or '<' in s or '>' in s):
        return None
    if not _NATIVE_PATH_RE.match(s):
        return None
    # 额外防「pages/../../」穿越
    if '..' in s or s.count('/') > 8:
        return None
    return s


def is_safe_fallback(path: str) -> bool:
    """回落路径必须是站内 /web 或 /api/public/ 前缀，且不含协议 / 空白。"""
    if not isinstance(path, str) or not path.startswith(_FALLBACK_PREFIXES):
        return False
    return not ('://' in path or '\\' in path or any(ch.isspace() for ch in path))


def substitute(path: str, club_id) -> str:
    """服务端替换 {club_id}；其余未知占位符原样保留（不猜、不编）。"""
    try:
        return str(path).replace('{club_id}', str(int(club_id)))
    except Exception:
        return str(path)


def resolve_path(cap: dict, binding: dict, cfg: dict, club_id) -> tuple:
    """返回 (path, pathKind, bound)。native 模式且已绑定 → 用绑定页；否则回落真实路径。"""
    b = binding or {}
    page = _str(b.get('page_path'), MAX_PATH_LEN)
    if (cfg or {}).get('launchMode') == 'native' and page and normalize_page_path(page) == page:
        return page, 'native', True
    fb = cap.get('fallback') or ''
    if not is_safe_fallback(fb):
        return '', 'none', False
    return substitute(fb, club_id), ('h5' if fb.startswith('/web') else 'api'), False


# ── 配置 ────────────────────────────────────────────────────────────────────
def default_config() -> dict:
    return {'enabled': True, 'launchMode': 'h5', 'defaultTab': 'home',
            'showDisabled': False, 'notice': '', 'maxPerRow': 4}


def normalize_config(payload, cur=None) -> dict:
    """字段级白名单：只覆盖显式传入的合法字段，未知 / 坏值静默丢弃。
    未配置时给默认（enabled 默认 True，避免一开新库就误关整个小程序对接）。"""
    out = dict(default_config())
    if isinstance(cur, dict):
        for k in out:
            if k in cur:
                out[k] = cur[k]
    if not isinstance(payload, dict):
        return out
    if 'enabled' in payload:
        out['enabled'] = bool(payload.get('enabled'))
    if 'launchMode' in payload:
        v = _str(payload.get('launchMode'), 20)
        if v in LAUNCH_MODES:
            out['launchMode'] = v
    if 'defaultTab' in payload:
        v = _str(payload.get('defaultTab'), 30)
        if v in DEFAULT_TABS:
            out['defaultTab'] = v
    if 'showDisabled' in payload:
        out['showDisabled'] = bool(payload.get('showDisabled'))
    if 'notice' in payload:
        out['notice'] = _str(payload.get('notice'), MAX_NOTICE_LEN)
    if 'maxPerRow' in payload:
        n = _int(payload.get('maxPerRow'), out['maxPerRow'], 2, 8)
        out['maxPerRow'] = n
    return out


def config_view(cfg) -> dict:
    c = dict(cfg or {})
    return {'enabled': bool(c.get('enabled', True)),
            'launchMode': c.get('launchMode') if c.get('launchMode') in LAUNCH_MODES else 'h5',
            'defaultTab': c.get('defaultTab') if c.get('defaultTab') in DEFAULT_TABS else 'home',
            'showDisabled': bool(c.get('showDisabled')),
            'notice': _str(c.get('notice'), MAX_NOTICE_LEN),
            'maxPerRow': _int(c.get('maxPerRow'), 4, 2, 8)}


def config_from_row(r) -> dict:
    if not r:
        return default_config()
    try:
        d = dict(r) if hasattr(r, 'keys') else {}
    except Exception:
        d = {}
    saved = loads(d.get('config_json'), {})
    if not isinstance(saved, dict):
        saved = {}
    out = normalize_config(saved)
    if 'enabled' in d and d.get('enabled') is not None:
        out['enabled'] = bool(d['enabled'])
    return out


def load_config(c, club_id) -> dict:
    try:
        r = c.execute('SELECT * FROM miniprogram_bridge_config WHERE club_id=?',
                      (int(club_id),)).fetchone()
        return config_from_row(r)
    except Exception:
        return default_config()


def save_config(c, club_id, cfg: dict, operator='') -> None:
    cfg = config_view(cfg)
    try:
        c.execute("""INSERT INTO miniprogram_bridge_config
                     (club_id,enabled,config_json,updated_at,updated_by)
                     VALUES(?,?,?,?,?)
                     ON CONFLICT(club_id) DO UPDATE SET
                       enabled=excluded.enabled, config_json=excluded.config_json,
                       updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
                  (int(club_id), 1 if cfg['enabled'] else 0, dumps(cfg),
                   business_now(), _str(operator, 60)))
    except Exception:
        pass


# ── 绑定（俱乐部编排覆盖）──────────────────────────────────────────────────
def normalize_binding(payload, cur=None):
    """归一一条绑定覆盖。非法 pagePath 返回 None（调用方 400），坏输入静默丢弃。"""
    out = {'page_path': '', 'enabled': True, 'hidden': None,
           'sort_order': 0, 'note': ''}
    if isinstance(cur, dict):
        p = _str(cur.get('page_path'), MAX_PATH_LEN)
        out['page_path'] = p if normalize_page_path(p) == p else ''
        out['enabled'] = bool(cur.get('enabled', True))
        h = cur.get('hidden')
        out['hidden'] = None if h is None else bool(h)
        out['sort_order'] = _int(cur.get('sort_order'), 0, 0, MAX_SORT)
        out['note'] = _str(cur.get('note'), MAX_NOTE_LEN)
    if not isinstance(payload, dict):
        return out
    if 'pagePath' in payload:
        p = normalize_page_path(payload.get('pagePath'))
        if p is None:
            return None
        out['page_path'] = p
    if 'enabled' in payload:
        out['enabled'] = bool(payload.get('enabled'))
    if 'hidden' in payload:
        h = payload.get('hidden')
        out['hidden'] = None if h is None else bool(h)
    if 'sort' in payload:
        out['sort_order'] = _int(payload.get('sort'), out['sort_order'], 0, MAX_SORT)
    if 'note' in payload:
        out['note'] = _str(payload.get('note'), MAX_NOTE_LEN)
    return out


def _hidden_sql(v):
    return None if v is None else (1 if v else 0)


def load_bindings(c, club_id) -> dict:
    try:
        rows = c.execute('SELECT * FROM miniprogram_bridge_bindings WHERE club_id=?',
                         (int(club_id),)).fetchall()
    except Exception:
        return {}
    out = {}
    for r in rows or []:
        try:
            d = dict(r)
            out[_str(d.get('capability_key'), 60)] = d
        except Exception:
            continue
    return out


def save_binding(c, club_id, key: str, b: dict, operator='') -> bool:
    if key not in CAP_BY_KEY or not isinstance(b, dict):
        return False
    try:
        cur = c.execute("""INSERT INTO miniprogram_bridge_bindings
                           (club_id,capability_key,page_path,enabled,hidden,sort_order,note,updated_at,updated_by)
                           VALUES(?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(club_id,capability_key) DO UPDATE SET
                             page_path=excluded.page_path, enabled=excluded.enabled,
                             hidden=excluded.hidden, sort_order=excluded.sort_order,
                             note=excluded.note, updated_at=excluded.updated_at,
                             updated_by=excluded.updated_by""",
                        (int(club_id), key, _str(b.get('page_path'), MAX_PATH_LEN),
                         1 if b.get('enabled', True) else 0, _hidden_sql(b.get('hidden')),
                         _int(b.get('sort_order'), 0, 0, MAX_SORT),
                         _str(b.get('note'), MAX_NOTE_LEN), business_now(), _str(operator, 60)))
        return True
    except Exception:
        return False


def reset_binding(c, club_id, key: str) -> bool:
    try:
        cur = c.execute('DELETE FROM miniprogram_bridge_bindings WHERE club_id=? AND capability_key=?',
                        (int(club_id), key))
        return bool(getattr(cur, 'rowcount', 0))
    except Exception:
        return False


def binding_view(cap: dict, b) -> dict:
    """单条能力的编排视图（后台用）：带默认 / 覆盖 / 三态 hidden。"""
    d = {}
    if b is not None:
        try:
            d = dict(b)
        except Exception:
            d = {}
    h = d.get('hidden') if d else None
    p = _str(d.get('page_path'), MAX_PATH_LEN) if d else ''
    return {
        'key': cap['key'], 'label': cap['label'], 'group': cap['group'],
        'phase': cap['phase'], 'module': cap.get('module'), 'icon': cap['icon'],
        'defaultOrder': cap['order'], 'defaultFallback': cap['fallback'],
        'pagePath': p if normalize_page_path(p) == p else '',
        'enabled': bool(d.get('enabled', True)) if d else True,
        'hidden': None if h is None else bool(h),
        'hiddenOverridden': bool(d and 'hidden' in d and h is not None),
        'sort': _int(d.get('sort_order'), 0, 0, MAX_SORT) if d else 0,
        'note': _str(d.get('note'), MAX_NOTE_LEN) if d else '',
        'customized': bool(d),
        'updatedAt': d.get('updated_at') or '',
        'updatedBy': d.get('updated_by') or '',
    }


# ── 生效视图（能力清单 + 绑定 + 模块开关联动）──────────────────────────────
def _module_on(module_enabled, key) -> bool:
    """回调异常 / 未注入 → True（三铁律：绝不误关业务）。"""
    if not key:
        return True
    if module_enabled is None:
        return True
    try:
        return bool(module_enabled(key))
    except Exception:
        return True


def effective(c, club_id, *, module_enabled=None, include_club=False) -> dict:
    """一屏编排：每个能力的开关状态、路径、绑定、不可用原因。全只读、异常安全。"""
    cfg = load_config(c, club_id)
    binds = load_bindings(c, club_id)
    items = []
    for cap in CAPABILITIES:
        if cap['audience'] == 'club' and not include_club:
            continue
        b = binds.get(cap['key'])
        bv = binding_view(cap, b)
        mod_ok = _module_on(module_enabled, cap.get('module'))
        bridge_ok = bool(cfg.get('enabled', True))
        b_enabled = bv['enabled']
        hidden = bool(bv['hidden']) if bv['hidden'] is not None else False
        enabled = bool(bridge_ok and b_enabled and mod_ok and not hidden)
        reason = (None if enabled else
                  'bridge-off' if not bridge_ok else
                  'binding-off' if not b_enabled else
                  'hidden' if hidden else 'module-off')
        path, path_kind, bound = resolve_path(cap, b or {}, cfg, club_id)
        items.append({
            'key': cap['key'], 'label': cap['label'], 'group': cap['group'],
            'audience': cap['audience'], 'phase': cap['phase'], 'module': cap.get('module'),
            'auth': cap['auth'], 'icon': cap['icon'], 'note': cap['note'],
            'order': bv['sort'] or cap['order'], 'defaultOrder': cap['order'],
            'enabled': enabled, 'reason': reason,
            'moduleEnabled': mod_ok, 'bindingEnabled': b_enabled,
            'hidden': hidden, 'hiddenOverridden': bv['hiddenOverridden'],
            'path': path, 'pathKind': path_kind, 'nativeBound': bound,
            'api': substitute(cap['api'], club_id),
            'pagePath': bv['pagePath'], 'customized': bv['customized'],
            'requiresLogin': cap['auth'] == 'login',
            'bindNote': bv['note'],
        })
    items.sort(key=lambda x: (x['order'], x['key']))
    enabled = [i for i in items if i['enabled']]
    return {
        'clubId': int(club_id), 'config': config_view(cfg),
        'groups': list(GROUPS), 'items': items, 'enabled': enabled,
        'summary': {
            'total': len(items), 'enabled': len(enabled),
            'disabledByModule': sum(1 for i in items if i['reason'] == 'module-off'),
            'disabledByBinding': sum(1 for i in items if i['reason'] == 'binding-off'),
            'hidden': sum(1 for i in items if i['reason'] == 'hidden'),
            'nativeBound': sum(1 for i in items if i['nativeBound']),
            'customized': sum(1 for i in items if i['customized']),
            'loginRequired': sum(1 for i in items if i['requiresLogin']),
        },
    }


def capabilities_view(eff: dict, *, user_id=None, show_disabled=None) -> list:
    """C 端能力清单：脱敏 + 按登录态标记；默认不下发被关闭的能力。"""
    cfg = (eff or {}).get('config') or default_config()
    show = bool(cfg.get('showDisabled')) if show_disabled is None else bool(show_disabled)
    logged = user_id is not None
    out = []
    for i in (eff or {}).get('items', []):
        if i['audience'] != 'web':
            continue
        if not i['enabled'] and not show:
            continue
        out.append({
            'key': i['key'], 'label': i['label'], 'group': i['group'],
            'icon': i['icon'], 'order': i['order'], 'phase': i['phase'],
            'path': i['path'], 'pathKind': i['pathKind'],
            'api': i['api'], 'enabled': i['enabled'],
            'requiresLogin': i['requiresLogin'],
            'locked': bool(i['requiresLogin'] and not logged),
        })
    return out


def navigation_view(eff: dict) -> list:
    """小程序首页宫格：只给已启用、有路径的能力，字段最小化。"""
    return [{'key': i['key'], 'label': i['label'], 'icon': i['icon'],
             'order': i['order'], 'path': i['path'], 'pathKind': i['pathKind'],
             'requiresLogin': i['requiresLogin']}
            for i in (eff or {}).get('enabled', [])
            if i['audience'] == 'web' and i['path']]


def _mp_public_fields(mp_info) -> dict:
    """小程序资料只下发展示字段；绝不含 secret / 主体证件等敏感项。"""
    x = mp_info if isinstance(mp_info, dict) else {}
    keep = ('appid', 'appName', 'avatarUrl', 'qrcodeUrl', 'currentVersion',
            'releaseStatus', 'authorized', 'privacyStatus')
    return {k: (bool(x.get(k)) if k == 'authorized' else (x.get(k) or ''))
            for k in keep}


def _settings_public_fields(mp_settings) -> dict:
    """标准设置只挑小程序渲染用得上的少量键，坏数据一律回落空。"""
    x = mp_settings if isinstance(mp_settings, dict) else {}
    out = {}
    for k in ('siteName', 'primaryColor', 'shareTitle', 'shareCover',
              'copyright', 'customerPhone'):
        v = x.get(k)
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
    return out


def bootstrap(c, club_id, *, module_enabled=None, mp_info=None, mp_settings=None,
              user_id=None, now=None) -> dict:
    """小程序冷启动一次拿全：资料 + 设置 + 能力清单 + 导航宫格 + 汇总。

    全只读；任何子查询异常都只影响该字段，不抛到调用方。
    """
    eff = effective(c, club_id, module_enabled=module_enabled)
    cfg = eff['config']
    caps = capabilities_view(eff, user_id=user_id)
    nav = navigation_view(eff)
    return {
        'clubId': int(club_id),
        'enabled': bool(cfg['enabled']),
        'launchMode': cfg['launchMode'],
        'defaultTab': cfg['defaultTab'],
        'notice': cfg['notice'],
        'maxPerRow': cfg['maxPerRow'],
        'app': _mp_public_fields(mp_info),
        'settings': _settings_public_fields(mp_settings),
        'capabilities': caps,
        'navigation': nav,
        'summary': {
            'total': eff['summary']['total'],
            'enabled': eff['summary']['enabled'],
            'delivered': len(caps),
            'navigation': len(nav),
            'nativeBound': eff['summary']['nativeBound'],
            'loginRequired': eff['summary']['loginRequired'],
        },
        'loggedIn': user_id is not None,
        'generatedAt': _ts_to_str(now if now is not None else _now()),
    }


# ── 个人态聚合（只读，逐项异常独立回落）────────────────────────────────────
def _one(c, sql, args, default=0):
    try:
        r = c.execute(sql, args).fetchone()
        return int(r[0] or 0) if r else default
    except Exception:
        return default


def my_view(c, club_id, user_id, *, module_enabled=None) -> dict:
    """登录会员的桥接「我的」聚合：各处余额只读取、不写入、不改 payable。

    与 P2-2 同纪律：礼品卡余额与储值余额分列，绝不相加成一个可疑的「总余额」。
    """
    uid = _int(user_id, 0, 1, 10 ** 12)
    cid = int(club_id)
    out = {'clubId': cid, 'userId': uid, 'amountUnit': 'cent', 'balances': {},
           'affiliate': None, 'enabled': {}, 'notice': ''}
    if not uid:
        return out
    out['balances'] = {
        'storedValueCents': _one(c, 'SELECT balance_cents FROM stored_value_accounts WHERE club_id=? AND user_id=?', (cid, uid)),
        'giftCardCents': _one(c, 'SELECT balance_cents FROM gift_card_accounts WHERE club_id=? AND user_id=?', (cid, uid)),
        'clubPoints': _one(c, 'SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?', (cid, uid)),
    }
    try:
        r = c.execute('SELECT code,status FROM affiliate_members WHERE club_id=? AND user_id=?',
                      (cid, uid)).fetchone()
        if r:
            out['affiliate'] = {'code': r[0] or '', 'status': r[1] or ''}
    except Exception:
        pass
    for k in ('storedValue', 'giftCard', 'affiliate', 'checkin'):
        out['enabled'][k] = _module_on(module_enabled, k)
    return out


# ── 对接就绪度（健康检查）──────────────────────────────────────────────────
# key / label / weight / required；required 未过 → ready=False（BLOCKED）
CHECKS = (
    {'key': 'appid', 'label': '小程序 appid 已登记', 'weight': 20, 'required': True},
    {'key': 'secret', 'label': '登录密钥已配置（环境变量）', 'weight': 20, 'required': True},
    {'key': 'authorized', 'label': '小程序已授权', 'weight': 15, 'required': True},
    {'key': 'bridgeEnabled', 'label': '桥接总开关已开启', 'weight': 15, 'required': True},
    {'key': 'releaseVersion', 'label': '已登记当前版本', 'weight': 10, 'required': False},
    {'key': 'moduleCoverage', 'label': '至少一个营销模块已开启', 'weight': 10, 'required': False},
    {'key': 'nativePaths', 'label': '原生页路径绑定', 'weight': 10, 'required': False},
)
TOTAL_WEIGHT = sum(x['weight'] for x in CHECKS)


def check_readiness(c, club_id, *, secret_configured=False, module_enabled=None,
                    now=None) -> dict:
    """输出对接就绪度：逐项 ok / detail、score、level、missing。密钥只接收布尔值。"""
    cfg = load_config(c, club_id)
    binds = load_bindings(c, club_id)
    mp = {}
    try:
        r = c.execute('SELECT * FROM club_miniprogram WHERE club_id=?', (int(club_id),)).fetchone()
        if r:
            mp = dict(r)
    except Exception:
        mp = {}
    appid = _str(mp.get('appid'), 64)
    authorized = bool(mp.get('authorized'))
    version = _str(mp.get('current_version'), 30)
    mods_on = [k for k in MODULE_KEYS if _module_on(module_enabled, k)]
    native_bound = [k for k, b in binds.items()
                    if k in CAP_BY_KEY and _str(b.get('page_path'), MAX_PATH_LEN)]

    values = {
        'appid': (bool(appid), appid or '未登记 appid'),
        'secret': (bool(secret_configured), '已配置' if secret_configured else
                   '需设置 WECHAT_MINIPROGRAM_SECRET 或按俱乐部覆盖'),
        'authorized': (authorized, '已授权' if authorized else '未授权（线下登记状态）'),
        'bridgeEnabled': (bool(cfg.get('enabled', True)),
                          '开启' if cfg.get('enabled', True) else '桥接已关闭'),
        'releaseVersion': (bool(version), version or '未登记版本号'),
        'moduleCoverage': (bool(mods_on), '%d 个模块开启' % len(mods_on)),
        'nativePaths': (True if cfg.get('launchMode') != 'native' else bool(native_bound),
                        ('h5 回落模式，无需绑定' if cfg.get('launchMode') != 'native'
                         else '%d 个能力已绑定原生页' % len(native_bound))),
    }
    results, missing, score = [], [], 0
    for spec in CHECKS:
        ok, detail = values.get(spec['key'], (False, '未知'))
        if ok:
            score += spec['weight']
        elif spec['required']:
            missing.append(spec['label'])
        results.append({'key': spec['key'], 'label': spec['label'],
                        'weight': spec['weight'], 'required': spec['required'],
                        'ok': bool(ok), 'detail': detail})
    score = int(round(score * 100.0 / TOTAL_WEIGHT)) if TOTAL_WEIGHT else 0
    ready = not missing
    level = 'READY' if (ready and score >= 90) else ('PARTIAL' if ready else 'BLOCKED')
    return {
        'clubId': int(club_id), 'ready': ready, 'score': score, 'level': level,
        'checks': results, 'missing': missing,
        'summary': {'passed': sum(1 for x in results if x['ok']),
                    'total': len(results), 'requiredMissing': len(missing),
                    'nativeBound': len(native_bound), 'modulesOn': len(mods_on),
                    'launchMode': cfg.get('launchMode', 'h5')},
        'checkedAt': _ts_to_str(now if now is not None else _now()),
    }


def record_check(c, club_id, result: dict, operator='') -> bool:
    try:
        c.execute("""INSERT INTO miniprogram_bridge_checks
                     (club_id,ready,score,level,missing_json,detail_json,operator,created_at)
                     VALUES(?,?,?,?,?,?,?,?)""",
                  (int(club_id), 1 if result.get('ready') else 0,
                   _int(result.get('score'), 0, 0, 100), _str(result.get('level'), 20),
                   dumps(result.get('missing') or []), dumps(result.get('checks') or []),
                   _str(operator, 60), business_now()))
        return True
    except Exception:
        return False


def check_view(r) -> dict:
    try:
        d = dict(r)
    except Exception:
        return {}
    return {'id': d.get('id'), 'clubId': d.get('club_id'),
            'ready': bool(d.get('ready')), 'score': _int(d.get('score'), 0, 0, 100),
            'level': d.get('level') or '',
            'missing': loads(d.get('missing_json'), []),
            'checks': loads(d.get('detail_json'), []),
            'operator': d.get('operator') or '', 'createdAt': d.get('created_at') or ''}


def list_checks(c, club_id, limit=30) -> list:
    n = _int(limit, 30, 1, MAX_CHECKS)
    try:
        rows = c.execute('SELECT * FROM miniprogram_bridge_checks WHERE club_id=? ORDER BY id DESC LIMIT ?',
                         (int(club_id), n)).fetchall()
    except Exception:
        return []
    return [check_view(r) for r in (rows or [])]


# ── 审计 ────────────────────────────────────────────────────────────────────
def audit(c, club_id, action: str, detail: dict, operator='') -> bool:
    try:
        c.execute("""INSERT INTO miniprogram_bridge_audit
                     (club_id,action,detail_json,operator,created_at) VALUES(?,?,?,?,?)""",
                  (int(club_id), _str(action, 40), dumps(detail or {}),
                   _str(operator, 60), business_now()))
        return True
    except Exception:
        return False


def audit_view(rows) -> list:
    out = []
    for r in rows or []:
        try:
            d = dict(r)
            out.append({'id': d.get('id'), 'action': d.get('action') or '',
                        'detail': loads(d.get('detail_json'), {}),
                        'operator': d.get('operator') or '',
                        'createdAt': d.get('created_at') or ''})
        except Exception:
            continue
    return out


def list_audit(c, club_id, limit=50) -> list:
    n = _int(limit, 50, 1, MAX_AUDIT)
    try:
        rows = c.execute('SELECT * FROM miniprogram_bridge_audit WHERE club_id=? ORDER BY id DESC LIMIT ?',
                         (int(club_id), n)).fetchall()
    except Exception:
        return []
    return audit_view(rows)


def platform_overview(c, rows) -> dict:
    """平台侧汇总：把每家俱乐部的最新一次检查结果聚合成一屏。只读。"""
    items = []
    for r in rows or []:
        try:
            d = dict(r)
        except Exception:
            continue
        items.append({'clubId': d.get('club_id'), 'clubName': d.get('name') or '',
                      'ready': bool(d.get('ready')), 'score': _int(d.get('score'), 0, 0, 100),
                      'level': d.get('level') or 'UNCHECKED',
                      'checkedAt': d.get('created_at') or ''})
    checked = [x for x in items if x['checkedAt']]
    return {'clubs': items,
            'summary': {'total': len(items), 'checked': len(checked),
                        'ready': sum(1 for x in checked if x['ready']),
                        'blocked': sum(1 for x in checked if x['level'] == 'BLOCKED'),
                        'avgScore': (int(round(sum(x['score'] for x in checked) / len(checked)))
                                     if checked else 0)}}
