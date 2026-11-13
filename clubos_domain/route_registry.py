"""功能路由表（v0.41，P1-5）：把四端散落的页面 / 锚点 / 能力端点收敛成一张可编排的注册表。

参考「远拓旅游 SaaS · 配置管理」的「页面路由 / 导航管理」形态：运营在一个地方看到
「系统里有哪些可去的地方」，能改名字、改顺序、隐藏，并且这个改动真实影响 C 端入口。

**关键设计：不搬数据、不做第二套真相、不编造页面。**
- 注册表只登记**真实存在**的路径：C 端 7 个 view / 既有 hash 锚点 / 深链模板 / P0-P1 公开 API /
  后台页面。没有独立 C 端页面的新模块登记为 kind='api'，绝不假造一个能跳的 URL。
- 可用性联动复用两处既有真相：settings_hub 的模块总开关（注入回调）、
  clubs.module_switches_json 的 C 端 tab 开关（由 app.py 解析后传入）。本模块不查这两张表。
- 俱乐部只存「编排覆盖」（隐藏 / 排序 / 改名 / 图标 / 自定义外链），注册表本身是代码常量。
- 三铁律：覆盖表缺失 / 数据损坏 / 回调异常 → 静默回落到默认，绝不因配置读取失败阻断业务。

**安全**：公开端点只下发 auth='public' 的项；含 {club_id} 等占位符的模板在服务端替换，
不接受客户端传 URL（防开放重定向）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

SCOPES = ('web', 'club', 'platform', 'leader')
SCOPE_LABELS = {
    'web': 'C 端（H5 / 小程序）',
    'club': '俱乐部后台',
    'platform': '平台后台',
    'leader': '领队端',
}
PUBLIC_SCOPES = ('web',)  # 只有 C 端入口允许匿名读取

# kind: view=C 端真实视图 / anchor=既有 hash 锚点 / deep=需参数深链模板
#       api=能力端点（无独立页面）/ page=后台或平台页面
# auth: public=可匿名访问 / login=需登录 / club=俱乐部权限 / platform=平台权限
# navigable: 是否允许作为装修器导航宫格的跳转目标
# module: 关联 settings_hub 模块 key（开关联动）
# tab: 关联 clubs.module_switches_json 的键（C 端 tab 显隐联动）
GROUPS = (
    {'key': 'web.main', 'scope': 'web', 'label': '主导航', 'order': 10},
    {'key': 'web.member', 'scope': 'web', 'label': '会员与权益', 'order': 20},
    {'key': 'web.feature', 'scope': 'web', 'label': '新增能力（API）', 'order': 30},
    {'key': 'web.deep', 'scope': 'web', 'label': '深链模板', 'order': 40},
    {'key': 'club.core', 'scope': 'club', 'label': '日常经营', 'order': 10},
    {'key': 'club.marketing', 'scope': 'club', 'label': '营销与玩法', 'order': 20},
    {'key': 'club.config', 'scope': 'club', 'label': '配置与治理', 'order': 30},
    {'key': 'club.entity', 'scope': 'club', 'label': '实体与组织', 'order': 40},
    {'key': 'platform.core', 'scope': 'platform', 'label': '平台总览', 'order': 10},
    {'key': 'leader.core', 'scope': 'leader', 'label': '带队与结算', 'order': 10},
)

ROUTES = (
    # ── web：主导航（真实 view，path 的 #view=<id> 由 web.js 解析后调 wviewShow）──
    {'key': 'web.home', 'label': '首页', 'scope': 'web', 'group': 'web.main',
     'path': '/web?club_id={club_id}', 'kind': 'view', 'auth': 'public', 'icon': '🏠',
     'order': 10, 'tab': 'home', 'navigable': True, 'visible': True,
     'note': 'C 端首页；装修版面挂载点 #homeDecorated 也在这里'},
    {'key': 'web.activities', 'label': '活动列表', 'scope': 'web', 'group': 'web.main',
     'path': '/web?club_id={club_id}#view=wactivities', 'kind': 'view', 'auth': 'public',
     'icon': '⛰️', 'order': 20, 'tab': 'activity', 'navigable': True, 'visible': True,
     'note': '活动分类 / 地区筛选 / 瀑布流'},
    {'key': 'web.camp', 'label': '营地', 'scope': 'web', 'group': 'web.main',
     'path': '/web?club_id={club_id}#view=wcamp', 'kind': 'view', 'auth': 'public',
     'icon': '🏕️', 'order': 30, 'tab': 'camp', 'navigable': True, 'visible': False,
     'note': '默认隐藏：与活动共用一个 tab 槽，由经营业态开关决定'},
    {'key': 'web.mall', 'label': '装备商城', 'scope': 'web', 'group': 'web.main',
     'path': '/web?club_id={club_id}#view=wmall', 'kind': 'view', 'auth': 'public',
     'icon': '🎒', 'order': 40, 'navigable': True, 'visible': True, 'note': '装备与商品列表'},
    {'key': 'web.ability', 'label': '户外能力', 'scope': 'web', 'group': 'web.main',
     'path': '/web?club_id={club_id}#view=wability', 'kind': 'view', 'auth': 'public',
     'icon': '🧭', 'order': 50, 'tab': 'bizIntro', 'navigable': True, 'visible': True,
     'note': '团建 / 研学 / 企业团等业务介绍'},
    {'key': 'web.me', 'label': '我的', 'scope': 'web', 'group': 'web.main',
     'path': '/web?club_id={club_id}#view=wme', 'kind': 'view', 'auth': 'login',
     'icon': '👤', 'order': 60, 'navigable': True, 'visible': True, 'note': '会员中心'},

    # ── web：既有 hash 锚点（沿用 P0-2 装修器 marketing_entry / targetHref 的约定）──
    {'key': 'web.memberAnchor', 'label': '会员中心锚点', 'scope': 'web', 'group': 'web.member',
     'path': '/web?club_id={club_id}#member', 'kind': 'anchor', 'auth': 'public',
     'icon': '🪪', 'order': 70, 'navigable': True, 'visible': True,
     'note': '既有 marketing_entry 锚点，不是独立页面'},
    {'key': 'web.pointsAnchor', 'label': '积分锚点', 'scope': 'web', 'group': 'web.member',
     'path': '/web?club_id={club_id}#points', 'kind': 'anchor', 'auth': 'login',
     'icon': '⭐', 'order': 80, 'navigable': True, 'visible': True, 'note': '既有营销入口锚点'},
    {'key': 'web.couponAnchor', 'label': '优惠券锚点', 'scope': 'web', 'group': 'web.member',
     'path': '/web?club_id={club_id}#coupon', 'kind': 'anchor', 'auth': 'login',
     'icon': '🎟️', 'order': 90, 'navigable': True, 'visible': True, 'note': '既有营销入口锚点'},
    {'key': 'web.inviteAnchor', 'label': '邀请锚点', 'scope': 'web', 'group': 'web.member',
     'path': '/web?club_id={club_id}#invite', 'kind': 'anchor', 'auth': 'login',
     'icon': '🤝', 'order': 100, 'navigable': True, 'visible': True, 'note': '既有营销入口锚点'},
    {'key': 'web.gearAnchor', 'label': '装备锚点', 'scope': 'web', 'group': 'web.member',
     'path': '/web?club_id={club_id}#gear', 'kind': 'anchor', 'auth': 'public',
     'icon': '🛒', 'order': 110, 'navigable': True, 'visible': True,
     'note': 'targetHref(product) 既有落点'},

    # ── web：P0/P1 新增能力。目前只有 API，没有独立 C 端页面 → 不编造可跳 URL ──
    {'key': 'web.storedValue', 'label': '会员储值', 'scope': 'web', 'group': 'web.feature',
     'path': '/api/public/clubs/{club_id}/stored-value/my', 'kind': 'api', 'auth': 'login',
     'icon': '💰', 'order': 120, 'module': 'storedValue', 'navigable': False, 'visible': True,
     'note': 'P1-1；C 端入口待接入，当前登记 API 并受中枢模块开关联动'},
    {'key': 'web.checkin', 'label': '每日签到', 'scope': 'web', 'group': 'web.feature',
     'path': '/api/public/clubs/{club_id}/engagement/overview', 'kind': 'api', 'auth': 'login',
     'icon': '📅', 'order': 130, 'module': 'checkin', 'navigable': False, 'visible': True,
     'note': 'P1-2；受中枢 checkin 开关联动'},
    {'key': 'web.lottery', 'label': '幸运抽奖', 'scope': 'web', 'group': 'web.feature',
     'path': '/api/public/clubs/{club_id}/engagement/overview', 'kind': 'api', 'auth': 'login',
     'icon': '🎡', 'order': 140, 'module': 'lottery', 'navigable': False, 'visible': True,
     'note': 'P1-2；受中枢 lottery 开关联动'},
    {'key': 'web.stamp', 'label': '集章兑换', 'scope': 'web', 'group': 'web.feature',
     'path': '/api/public/clubs/{club_id}/engagement/overview', 'kind': 'api', 'auth': 'login',
     'icon': '📮', 'order': 150, 'module': 'stamp', 'navigable': False, 'visible': True,
     'note': 'P1-2；受中枢 stamp 开关联动'},
    {'key': 'web.groupBuy', 'label': '拼团广场', 'scope': 'web', 'group': 'web.feature',
     'path': '/api/public/clubs/{club_id}/group-buys/teams', 'kind': 'api', 'auth': 'public',
     'icon': '👥', 'order': 160, 'navigable': False, 'visible': True,
     'note': 'P1-3；拼团价只读展示，不改 payable'},
    {'key': 'web.inbox', 'label': '站内信', 'scope': 'web', 'group': 'web.feature',
     'path': '/api/club/{club_id}/inapp-messages', 'kind': 'api', 'auth': 'login',
     'icon': '✉️', 'order': 170, 'navigable': False, 'visible': True,
     'note': 'P0-4；需登录身份，公开端点不下发此项'},
    {'key': 'web.pageDecorated', 'label': '装修版面', 'scope': 'web', 'group': 'web.feature',
     'path': '/api/public/clubs/{club_id}/page/club_home', 'kind': 'api', 'auth': 'public',
     'icon': '🎨', 'order': 180, 'module': 'pageLayout', 'navigable': False, 'visible': True,
     'note': 'P0-2；中枢「首页呈现方式」也作用于此项'},
    {'key': 'web.affiliate', 'label': '推广中心', 'scope': 'web', 'group': 'web.feature',
     'path': '/api/public/clubs/{club_id}/affiliate/my', 'kind': 'api', 'auth': 'login',
     'icon': '🤝', 'order': 185, 'module': 'affiliate', 'navigable': False, 'visible': True,
     'note': 'P2-1；C 端推广码 / 佣金查询，受中枢分销开关联动'},
    {'key': 'web.giftCard', 'label': '礼品卡', 'scope': 'web', 'group': 'web.feature',
     'path': '/api/public/clubs/{club_id}/gift-cards/my', 'kind': 'api', 'auth': 'login',
     'icon': '🎁', 'order': 187, 'module': 'giftCard', 'navigable': False, 'visible': True,
     'note': 'P2-2；卡密兑换 / 余额查询，受中枢礼品卡开关联动'},
    {'key': 'web.miniprogramBootstrap', 'label': '小程序启动配置', 'scope': 'web', 'group': 'web.feature',
     'path': '/api/public/clubs/{club_id}/miniprogram/bootstrap', 'kind': 'api', 'auth': 'public',
     'icon': '📱', 'order': 189, 'module': 'miniprogramBridge', 'navigable': False, 'visible': True,
     'note': 'P2-3；一次下发资料 / 设置 / 能力清单 / 导航宫格，受中枢小程序对接开关联动'},

    # ── web：深链模板（需要参数，不作为导航目标）──
    {'key': 'web.activityDetail', 'label': '活动详情', 'scope': 'web', 'group': 'web.deep',
     'path': '/web?club_id={club_id}&activity={id}', 'kind': 'deep', 'auth': 'public',
     'icon': '🔗', 'order': 190, 'navigable': False, 'visible': True,
     'note': 'startWeb() 解析 ?activity= 并直接打开详情；需填 id'},
    {'key': 'web.productDetail', 'label': '商品详情', 'scope': 'web', 'group': 'web.deep',
     'path': '/web?club_id={club_id}#pd={id}', 'kind': 'deep', 'auth': 'public',
     'icon': '🔗', 'order': 200, 'navigable': False, 'visible': True,
     'note': 'startWeb() 解析 #pd= 后切到装备页并打开详情；需填 id'},

    # ── club：后台页面（真实文件）──
    {'key': 'club.dashboard', 'label': '工作台', 'scope': 'club', 'group': 'club.core',
     'path': '/static/club/index.html', 'kind': 'page', 'auth': 'club', 'icon': '📊',
     'order': 10, 'navigable': False, 'visible': True, 'note': '俱乐部主后台（多 view 单页）'},
    {'key': 'club.activities', 'label': '活动中心', 'scope': 'club', 'group': 'club.core',
     'path': '/static/club/index.html?view=activities', 'kind': 'page', 'auth': 'club',
     'icon': '⛰️', 'order': 20, 'navigable': False, 'visible': True, 'note': '主后台内 view'},
    {'key': 'club.members', 'label': '会员列表', 'scope': 'club', 'group': 'club.core',
     'path': '/static/club/index.html?view=members-list', 'kind': 'page', 'auth': 'club',
     'icon': '👥', 'order': 30, 'navigable': False, 'visible': True, 'note': '主后台内 view'},
    {'key': 'club.points', 'label': '积分策略', 'scope': 'club', 'group': 'club.marketing',
     'path': '/static/club/index.html?view=points', 'kind': 'page', 'auth': 'club',
     'icon': '⭐', 'order': 40, 'navigable': False, 'visible': True, 'note': '主后台内 view'},
    {'key': 'club.campaigns', 'label': '营销活动引擎', 'scope': 'club', 'group': 'club.marketing',
     'path': '/api/club/{club_id}/campaigns', 'kind': 'api', 'auth': 'club', 'icon': '🎯',
     'order': 50, 'navigable': False, 'visible': True, 'note': 'P0-1；折扣作用在报价链路'},
    {'key': 'club.pageDecorator', 'label': '界面装修器', 'scope': 'club', 'group': 'club.config',
     'path': '/static/club/page-decorator.html', 'kind': 'page', 'auth': 'club', 'icon': '🎨',
     'order': 60, 'module': 'pageLayout', 'navigable': False, 'visible': True, 'note': 'P0-2'},
    {'key': 'club.storedValue', 'label': '储值管理', 'scope': 'club', 'group': 'club.marketing',
     'path': '/static/club/stored-value.html', 'kind': 'page', 'auth': 'club', 'icon': '💰',
     'order': 70, 'module': 'storedValue', 'navigable': False, 'visible': True, 'note': 'P1-1'},
    {'key': 'club.engagement', 'label': '互动玩法', 'scope': 'club', 'group': 'club.marketing',
     'path': '/static/club/engagement.html', 'kind': 'page', 'auth': 'club', 'icon': '🎡',
     'order': 80, 'navigable': False, 'visible': True, 'note': 'P1-2；签到 / 抽奖 / 集章'},
    {'key': 'club.groupBuys', 'label': '拼团管理', 'scope': 'club', 'group': 'club.marketing',
     'path': '/static/club/group-buys.html', 'kind': 'page', 'auth': 'club', 'icon': '👥',
     'order': 90, 'navigable': False, 'visible': True, 'note': 'P1-3'},
    {'key': 'club.affiliates', 'label': '分销员', 'scope': 'club', 'group': 'club.marketing',
     'path': '/static/club/affiliates.html', 'kind': 'page', 'auth': 'club', 'icon': '🤝',
     'order': 95, 'module': 'affiliate', 'navigable': False, 'visible': True,
     'note': 'P2-1；推广关系 / 佣金 / 结算单'},
    {'key': 'club.giftCards', 'label': '礼品卡', 'scope': 'club', 'group': 'club.marketing',
     'path': '/static/club/gift-cards.html', 'kind': 'page', 'auth': 'club', 'icon': '🎁',
     'order': 97, 'module': 'giftCard', 'navigable': False, 'visible': True,
     'note': 'P2-2；批次 / 卡密 / 兑换 / 余额核销'},
    {'key': 'club.miniprogramBridge', 'label': '小程序对接', 'scope': 'club', 'group': 'club.config',
     'path': '/static/club/miniprogram-bridge.html', 'kind': 'page', 'auth': 'club', 'icon': '📱',
     'order': 99, 'module': 'miniprogramBridge', 'navigable': False, 'visible': True,
     'note': 'P2-3；能力绑定 / 启动配置 / 对接就绪度检查'},
    {'key': 'club.settlements', 'label': '结算编排', 'scope': 'club', 'group': 'club.config',
     'path': '/static/club/settlements.html', 'kind': 'page', 'auth': 'club', 'icon': '🧾',
     'order': 101, 'module': 'checkoutSettlement', 'navigable': False, 'visible': True,
     'note': 'P3；订单级拼团立减 / 礼品卡抵扣 / 分销佣金流水与释放重试'},
    {'key': 'club.commerceScope', 'label': '营销适用范围', 'scope': 'club', 'group': 'club.config',
     'path': '/api/club/{club_id}/commerce-scope', 'kind': 'api', 'auth': 'club', 'icon': '🚧',
     'order': 103, 'module': 'checkoutSettlement', 'navigable': False, 'visible': True,
     'note': 'P4；只读边界自检：仅俱乐部活动 / 营地产品，不含总平台装备销售'},
    {'key': 'club.settingsHub', 'label': '设置中枢', 'scope': 'club', 'group': 'club.config',
     'path': '/static/club/settings-hub.html', 'kind': 'page', 'auth': 'club', 'icon': '⚙️',
     'order': 100, 'navigable': False, 'visible': True, 'note': 'P1-4；能力开关与偏好'},
    {'key': 'club.routes', 'label': '路由表', 'scope': 'club', 'group': 'club.config',
     'path': '/static/club/routes.html', 'kind': 'page', 'auth': 'club', 'icon': '🗺️',
     'order': 110, 'navigable': False, 'visible': True, 'note': 'P1-5；本页'},
    {'key': 'club.stores', 'label': '门店 / 基地', 'scope': 'club', 'group': 'club.entity',
     'path': '/static/club/stores.html', 'kind': 'page', 'auth': 'club', 'icon': '📍',
     'order': 120, 'navigable': False, 'visible': True, 'note': 'P0-3'},
    {'key': 'club.notificationTemplates', 'label': '消息模板', 'scope': 'club',
     'group': 'club.config', 'path': '/api/club/{club_id}/notification-templates',
     'kind': 'api', 'auth': 'club', 'icon': '✉️', 'order': 130,
     'navigable': False, 'visible': True, 'note': 'P0-4'},

    # ── platform / leader ──
    {'key': 'platform.dashboard', 'label': '平台总览', 'scope': 'platform',
     'group': 'platform.core', 'path': '/static/platform/index.html', 'kind': 'page',
     'auth': 'platform', 'icon': '🏛️', 'order': 10, 'navigable': False, 'visible': True,
     'note': '平台运营后台'},
    {'key': 'platform.campaigns', 'label': '全站营销活动', 'scope': 'platform',
     'group': 'platform.core', 'path': '/api/platform/campaigns', 'kind': 'api',
     'auth': 'platform', 'icon': '🎯', 'order': 20, 'navigable': False, 'visible': True,
     'note': 'P0-1 平台视角'},
    {'key': 'platform.notificationTemplates', 'label': '平台默认模板', 'scope': 'platform',
     'group': 'platform.core', 'path': '/api/platform/notification-templates', 'kind': 'api',
     'auth': 'platform', 'icon': '✉️', 'order': 30, 'navigable': False, 'visible': True,
     'note': 'P0-4；club_id=0 兜底模板'},
    {'key': 'leader.dashboard', 'label': '带队工作台', 'scope': 'leader',
     'group': 'leader.core', 'path': '/static/leader/index.html', 'kind': 'page',
     'auth': 'login', 'icon': '🧭', 'order': 10, 'navigable': False, 'visible': True,
     'note': '领队端'},
)

ROUTE_BY_KEY = {r['key']: r for r in ROUTES}
PUBLIC_VIEW_KEYS = ('whome', 'wactivities', 'wcamp', 'wmall', 'wability', 'wme', 'wleaders')
LABEL_MAX = 40
ICON_MAX = 16
URL_MAX = 500
SORT_MIN, SORT_MAX = 0, 9999
MAX_CHANGES = 100


def dumps(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def loads(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def now_str() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


def _text(v, limit):
    return str(v if v is not None else '').strip()[:limit]


def resolve(key: str):
    return ROUTE_BY_KEY.get(str(key or '').strip())


def keys_for_scope(scope: str) -> list:
    return [r['key'] for r in ROUTES if r['scope'] == scope]


def registry_view(scope=None, navigable_only=False) -> dict:
    """注册表视图（无 SQL、无俱乐部数据）。给后台渲染表单 / 装修器下拉用。"""
    s = str(scope or '').strip()
    if s and s not in SCOPES:
        raise ValueError('未知端：%s' % s)
    items = [r for r in ROUTES if (not s or r['scope'] == s)]
    if navigable_only:
        items = [r for r in items if r['navigable']]
    out = []
    for r in sorted(items, key=lambda x: (x['scope'], x['order'], x['key'])):
        out.append({k: r.get(k) for k in (
            'key', 'label', 'scope', 'group', 'path', 'kind', 'auth',
            'icon', 'order', 'module', 'tab', 'navigable', 'visible', 'note')})
    groups = [dict(g) for g in sorted(GROUPS, key=lambda x: (x['scope'], x['order']))
              if not s or g['scope'] == s]
    return {
        'scopes': [{'key': k, 'label': SCOPE_LABELS[k], 'count': len(keys_for_scope(k))}
                   for k in SCOPES],
        'groups': groups,
        'routes': out,
        'publicViewKeys': list(PUBLIC_VIEW_KEYS),
        'summary': {'total': len(out), 'navigable': sum(1 for r in out if r['navigable'])},
    }


def path_for(key: str, club_id: int, **params) -> str:
    """把 {club_id} / {id} 占位符替换成真实值；未知 key 返回空串。绝不拼接客户端 URL。"""
    r = resolve(key)
    if not r:
        return ''
    p = r['path']
    vals = {'club_id': int(club_id)}
    vals.update({k: _text(v, 40) for k, v in params.items()})
    try:
        return p.format(**vals)[:URL_MAX]
    except (KeyError, IndexError, ValueError):
        # deep 模板缺参数 → 返回原模板（调用方据此判断不可跳转），不抛异常
        return p[:URL_MAX]


# ── 编排覆盖校验 ────────────────────────────────────────────────────────────
def normalize_override(raw, scope: str) -> dict:
    """归一化单条覆盖输入；非法 → ValueError（由路由转 400）。"""
    if not isinstance(raw, dict):
        raise ValueError('每条覆盖必须是对象')
    key = str(raw.get('key') or '').strip()
    r = resolve(key)
    if not r:
        raise ValueError('未知路由 key：%s' % key)
    if r['scope'] != scope:
        raise ValueError('路由 %s 不属于端 %s' % (key, scope))
    out = {'key': key}
    if 'hidden' in raw:
        v = raw['hidden']
        if v is None:
            # None = 清除覆盖，回落代码注册表的默认显隐（三态可逆）
            out['hidden'] = None
        elif isinstance(v, str):
            if v.strip().lower() not in ('true', '1', 'yes', 'false', '0', 'no'):
                raise ValueError('hidden 必须是布尔值或 null')
            out['hidden'] = v.strip().lower() in ('true', '1', 'yes')
        elif isinstance(v, bool):
            out['hidden'] = v
        elif isinstance(v, int) and v in (0, 1):
            out['hidden'] = bool(v)
        else:
            raise ValueError('hidden 必须是布尔值或 null')
    if 'sort' in raw and raw['sort'] is not None and raw['sort'] != '':
        try:
            v = int(raw['sort'])
        except (TypeError, ValueError):
            raise ValueError('sort 必须是整数')
        # 不用夹逼：越界直接拒绝（延续 P1-2 / P1-3 教训）
        if v < SORT_MIN or v > SORT_MAX:
            raise ValueError('sort 超出范围 %d~%d' % (SORT_MIN, SORT_MAX))
        out['sort'] = v
    for src, dst, lim in (('label', 'label', LABEL_MAX), ('icon', 'icon', ICON_MAX)):
        if src in raw:
            out[dst] = _text(raw[src], lim)
    if 'customUrl' in raw:
        u = _text(raw['customUrl'], URL_MAX)
        # 只允许站内相对路径 / 完整 http(s)；禁 javascript: / data: / 协议相对 //evil
        if u and not (u.startswith('/') and not u.startswith('//')) \
                and not u.startswith('http://') and not u.startswith('https://'):
            raise ValueError('customUrl 只允许站内路径或 http(s) 绝对地址')
        out['customUrl'] = u
    if len(out) == 1:
        raise ValueError('路由 %s 没有需要更新的字段' % key)
    return out


def normalize_changes(payload, scope: str) -> list:
    """把 {scope, changes:[...]} 归一化成覆盖列表；重复 key 以最后一条为准。"""
    if not isinstance(payload, dict):
        raise ValueError('请求体必须是对象')
    raw = payload.get('changes')
    if raw is None:
        raw = payload.get('items')
    if not isinstance(raw, list) or not raw:
        raise ValueError('changes 必须是非空数组')
    if len(raw) > MAX_CHANGES:
        raise ValueError('一次最多更新 %d 条' % MAX_CHANGES)
    merged = {}
    for it in raw:
        n = normalize_override(it, scope)
        merged.setdefault(n['key'], {}).update(n)
    return [merged[k] for k in sorted(merged)]


def overrides_from_rows(rows) -> dict:
    """把 DB 行转成 {key: {...}}；坏行静默丢弃。"""
    out = {}
    for r in rows or []:
        try:
            d = dict(r) if hasattr(r, 'keys') else None
            if not d:
                continue
            k = str(d.get('route_key') or '')
            if k not in ROUTE_BY_KEY:
                continue
            # hidden 是三态：NULL=未覆盖（回落 visibleByDefault），0/1=显式覆盖
            item = {'key': k}
            if d.get('hidden') is not None:
                item['hidden'] = bool(d['hidden'])
            if d.get('sort_order') is not None:
                item['sort'] = int(d['sort_order'])
            for src, dst in (('label', 'label'), ('icon', 'icon'), ('custom_url', 'customUrl')):
                if d.get(src):
                    item[dst] = str(d[src])
            out[k] = item
        except Exception:
            continue
    return out


# ── 生效计算（核心：真实消费两处既有开关）───────────────────────────────────
def effective(club_id: int, scope: str, overrides=None,
              module_enabled=None, tab_switches=None, public_only=False,
              navigable_only=False) -> dict:
    """生成该端最终可用入口。全只读、异常安全。

    module_enabled: callable(module_key) -> bool（由 app.py 注入 settings_hub 开关）
    tab_switches:   dict（由 app.py 从 clubs.module_switches_json 解析后传入）
    """
    if scope not in SCOPES:
        raise ValueError('未知端：%s' % scope)
    ov = overrides if isinstance(overrides, dict) else {}
    tabs = tab_switches if isinstance(tab_switches, dict) else {}

    def _module_ok(k):
        if not k:
            return True
        if module_enabled is None:
            return True
        try:
            return bool(module_enabled(k))
        except Exception:
            return True  # 三铁律：开关读取失败绝不误关业务

    items = []
    for r in ROUTES:
        if r['scope'] != scope:
            continue
        if public_only and r['auth'] != 'public':
            continue
        if navigable_only and not r['navigable']:
            continue
        o = ov.get(r['key']) or {}
        hv = o.get('hidden', not r['visible'])
        # hv is None 表示「覆盖已清除」→ 回落注册表默认显隐，不能被 bool(None) 洗成显示
        hidden = bool(not r['visible']) if hv is None else bool(hv)
        tab_ok = True
        if r.get('tab') and tabs:
            # 只在 tab 开关里显式出现该键时才判断；缺省不误关
            if r['tab'] in tabs:
                tab_ok = bool(tabs[r['tab']])
        mod_ok = _module_ok(r.get('module'))
        enabled = (not hidden) and tab_ok and mod_ok
        url = o.get('customUrl') or path_for(r['key'], club_id)
        # 未替换干净的 deep 模板不对外下发可跳 URL
        if '{' in url and '}' in url:
            url = ''
        items.append({
            'key': r['key'],
            'label': o.get('label') or r['label'],
            'group': r['group'],
            'kind': r['kind'],
            'auth': r['auth'],
            'icon': o.get('icon') or r['icon'],
            'order': int(o.get('sort', r['order'])),
            'navigable': bool(r['navigable']),
            'module': r.get('module'),
            'tab': r.get('tab'),
            'url': url,
            'customUrl': o.get('customUrl') or '',
            'enabled': enabled,
            'hidden': bool(o.get('hidden')),
            'hiddenOverridden': ('hidden' in o and o.get('hidden') is not None),
            'visibleByDefault': bool(r['visible']),
            'reason': ('hidden' if hidden else
                       'tab-off' if not tab_ok else
                       'module-off' if not mod_ok else None),
            'customized': bool(o),
        })
    items.sort(key=lambda x: (x['order'], x['key']))
    enabled_items = [i for i in items if i['enabled']]
    return {
        'clubId': int(club_id),
        'scope': scope,
        'items': items,
        'enabled': enabled_items,
        'urlMap': {i['key']: i['url'] for i in enabled_items if i['url']},
        'summary': {
            'total': len(items),
            'enabled': len(enabled_items),
            'hidden': sum(1 for i in items if i['reason'] == 'hidden'),
            'disabledByTab': sum(1 for i in items if i['reason'] == 'tab-off'),
            'disabledByModule': sum(1 for i in items if i['reason'] == 'module-off'),
            'customized': sum(1 for i in items if i['customized']),
        },
    }


def public_view(eff: dict) -> dict:
    """C 端脱敏视图：只留渲染导航必需的字段，去掉开关原因 / 权限 / 配置痕迹。"""
    return {
        'clubId': eff['clubId'],
        'scope': eff['scope'],
        'items': [{k: i[k] for k in ('key', 'label', 'group', 'kind', 'icon', 'order', 'url')}
                  for i in eff['enabled'] if i['url']],
        'count': len([i for i in eff['enabled'] if i['url']]),
    }


def audit_view(rows) -> list:
    out = []
    for r in rows or []:
        try:
            d = dict(r) if hasattr(r, 'keys') else None
            if not d:
                continue
            out.append({
                'id': d.get('id'), 'scope': d.get('scope'), 'routeKey': d.get('route_key'),
                'action': d.get('action'), 'detail': loads(d.get('detail_json')),
                'operator': d.get('operator'), 'createdAt': d.get('created_at'),
            })
        except Exception:
            continue
    return out
