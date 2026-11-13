"""营销适用范围（业务硬边界，2026-11-13 用户明确）。

ClubOS 的营销能力（统一营销活动 / 储值 / 互动玩法 / 拼团 / 分销佣金 /
礼品卡 / 结算编排 / 小程序桥接的营销入口）**只覆盖俱乐部活动与营地产品**：

  允许：活动报名 registrations、营位预订 camp_booking、营地统一订单
        camp_orders / camp_store_products / camp_activity_tickets。
  禁止：总平台装备销售 —— gear_orders / gear-checkout / products /
        product_variants / mall_* / points-mall / Medusa 装备链路。

这条边界必须在「写入方向」硬生效：核销礼品卡余额、入账分销佣金、生成
结算编排记录、判定首单资格，都不能引用或作用于装备订单。装备链路自身的
积分/券/库存/履约逻辑不受本模块影响，也绝不被本模块改写。

设计说明：clubos_domain 下各模块由测试用 importlib 单文件加载，因此本模块
不反向 import 任何业务模块；各模块复制白名单常量，由
tests/test_commerce_scope.py 断言三处常量始终一致。
"""
from __future__ import annotations

# ── 允许范围 ─────────────────────────────────────────────────────────────────
CLUB_ORDER_TYPES = (
    'registration',    # 俱乐部活动报名（P3 结算编排唯一已接的订单类型）
    'camp_booking',    # 营位预订结算单 checkout_intents.kind='camp_booking'
    'camp_order',      # 营地统一订单（营位 + 小站商品 + 活动票）
    'custom',          # 俱乐部自定义活动 / 营地订单（兼容既有后台录入）
)

# ── 禁止范围：总平台装备销售 ─────────────────────────────────────────────────
PLATFORM_GEAR_ORDER_TYPES = (
    'gear_order', 'gear_orders', 'gear_checkout', 'gear_order_item',
    'mall_order', 'mall_orders', 'point_mall_order', 'points_mall_order',
    'points_mall', 'medusa_order', 'equipment', 'equipment_order',
    'platform_order', 'shop_order', 'product_order',
)

# 模糊别名 / 常见拼写，命中即视为装备（防止用别名绕过白名单）
_GEAR_TOKENS = ('gear', 'mall', 'medusa', 'equipment', 'shop_order',
                'platform_order', 'product_order')

SCOPE_NOTE = ('营销能力仅适用于俱乐部活动与营地产品，不适用于总平台装备销售'
              '（gear_orders / mall / points-mall / Medusa）。')

# 首单资格只看「俱乐部活动 / 营地」成交记录；不得查 gear_orders / member_orders。
FIRST_ORDER_PROBES = (
    "SELECT 1 FROM registrations WHERE club_id=? AND user_id=? "
    "AND status IN ('paid','completed','confirmed') LIMIT 1",
    "SELECT 1 FROM camp_orders WHERE club_id=? AND user_id=? "
    "AND status IN ('paid','completed') LIMIT 1",
)


def normalize(value) -> str:
    """归一订单类型：小写、去空白、连字符/空格转下划线。坏输入返回空串。"""
    try:
        return str(value or '').strip().lower().replace('-', '_').replace(' ', '_')
    except Exception:
        return ''


def is_gear_order_type(value) -> bool:
    """是否属于总平台装备销售（含别名 / 前缀变体）。"""
    v = normalize(value)
    if not v:
        return False
    if v in PLATFORM_GEAR_ORDER_TYPES:
        return True
    return any(tok in v for tok in _GEAR_TOKENS)


def is_allowed_order_type(value) -> bool:
    """是否允许进入营销体系写入方向。装备类型一律 False。"""
    v = normalize(value)
    if not v or is_gear_order_type(v):
        return False
    return v in CLUB_ORDER_TYPES


def check_order_type(value) -> dict:
    """统一判定，供 app.py 端点返回明确错误原因。

    reason: ok / missing-order-type / out-of-scope-gear / unknown-order-type
    """
    v = normalize(value)
    if not v:
        return {'ok': False, 'reason': 'missing-order-type', 'value': '', 'note': SCOPE_NOTE}
    if is_gear_order_type(v):
        return {'ok': False, 'reason': 'out-of-scope-gear', 'value': v, 'note': SCOPE_NOTE}
    if v not in CLUB_ORDER_TYPES:
        return {'ok': False, 'reason': 'unknown-order-type', 'value': v, 'note': SCOPE_NOTE}
    return {'ok': True, 'reason': 'ok', 'value': v, 'note': SCOPE_NOTE}


def scope_summary() -> dict:
    """给后台 / 就绪度检查的只读视图。"""
    return {
        'note': SCOPE_NOTE,
        'allowedOrderTypes': list(CLUB_ORDER_TYPES),
        'blockedOrderTypes': list(PLATFORM_GEAR_ORDER_TYPES),
        'firstOrderTables': ['registrations', 'camp_orders'],
        'gearCheckoutWired': False,
    }
