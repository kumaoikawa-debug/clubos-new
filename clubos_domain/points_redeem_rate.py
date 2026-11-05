from __future__ import annotations
"""活动积分「现金换算比例」的唯一解析口径（2026-11-05）。

业务语义：`X 积分 = ¥1`。顾客报名时先把积分按这个比例折成现金，
再由活动规则里的「最多抵扣订单金额 N%」卡住现金上限。
    现金抵扣上限 = 订单金额 × N%
    所需积分上限 = 现金抵扣上限 × X
    实际抵扣现金 = 实际使用积分 / X
所以 X 越小（例：顶级会员 20 积分 = ¥1），同样的积分越值钱；
但「最多抵 N%」这道现金闸门始终不变，俱乐部成本可控。

生效优先级（前一级没设才往下一级退）：
    会员当前等级专属 > 俱乐部默认 > 平台全局默认(platform_settings) > 100

`0` 是哨兵值，含义是「跟随上一级」，不是「0 积分 = ¥1」：
  * 存量 clubs / club_member_tiers 升级后全部落 0，等价于升级前所有等级共用
    平台全局那个值，**存量行为零变化**；
  * 如果给 clubs 列写死默认 100，就会把平台曾经改过的全局比例（例如 50）
    悄悄顶成 100，那是无声的资损，绝对不能做。

边界：只管「俱乐部活动积分」（成本由俱乐部承担）。装备积分由总平台出钱、
准入与规则都归总平台，继续走 `gear_points_redeem_rate`，绝不在这里掺和。

报价（price-quote）与真实下单（create_activity_intent）必须调用同一个
`resolve()`，否则会出现「页面显示可抵 ¥30、下单按 0 抵扣」的经典漂移。
"""
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

FOLLOW = 0           # 跟随上一级默认
DEFAULT_RATE = 100   # 100 积分 = ¥1（历史默认）
MIN_RATE = 1
MAX_RATE = 10000     # 10000 积分 = ¥1，再高就没业务意义了


def coerce_rate(value: Any) -> int:
    """把库里的值安全变成「0=跟随 / 正数=有效比例」。任何脏数据一律退回跟随。

    解析层永远不能抛异常：报价和下单是收钱链路，宁可退回平台默认，
    也不能因为某个俱乐部填了个奇怪的值让顾客付不了款。
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return FOLLOW
    if n == FOLLOW:
        return FOLLOW
    if n < MIN_RATE or n > MAX_RATE:
        return FOLLOW
    return n


def validate_rate(value: Any) -> int:
    """API 入参校验。`0`/空 = 跟随上一级；`1~MAX_RATE` = 显式比例；其余抛 ValueError。

    这里要严格：写配置时把 `10.5` 悄悄截成 `10`、或者把 `-1` 存进库，
    比直接报错难查得多。
    """
    if value is None or value == '':
        return FOLLOW
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'积分换算比例必须是整数（{MIN_RATE}~{MAX_RATE}），填 0 表示跟随上一级默认')
    n = int(f)
    if f != n:
        raise ValueError('积分换算比例必须是整数，不接受小数')
    if n == FOLLOW:
        return FOLLOW
    if n < MIN_RATE or n > MAX_RATE:
        raise ValueError(f'积分换算比例需在 {MIN_RATE}~{MAX_RATE} 之间')
    return n


def _has_column(c, table: str, column: str) -> bool:
    """老库（没跑过本轮迁移）不能因为缺列就报错，退回跟随即可。"""
    try:
        return any(r[1] == column for r in c.execute(f'PRAGMA table_info({table})').fetchall())
    except Exception:
        return False


def _platform_rate(setting: Callable[[str, Any], Any]) -> int:
    try:
        rate = coerce_rate(setting('club_points_redeem_rate', DEFAULT_RATE))
    except Exception:
        logger.exception('club_points_redeem_rate lookup failed; falling back to %s', DEFAULT_RATE)
        return DEFAULT_RATE
    return rate or DEFAULT_RATE


def _club_rate(c, club_id: int) -> int:
    """俱乐部显式设定值；0 = 未设定（跟随平台）。"""
    if not _has_column(c, 'clubs', 'points_redeem_rate'):
        return FOLLOW
    try:
        r = c.execute('SELECT points_redeem_rate FROM clubs WHERE id=?', (int(club_id),)).fetchone()
        return coerce_rate(r[0]) if r else FOLLOW
    except Exception:
        logger.exception('clubs.points_redeem_rate lookup failed (club=%s)', club_id)
        return FOLLOW


def _member_tier_rate(c, club_id: int, user_id: int) -> int:
    """会员「当前等级」的专属比例；0 / 无等级 / 查询失败 = 跟随俱乐部。"""
    if not (_has_column(c, 'club_member_tiers', 'points_redeem_rate')
            and _has_column(c, 'club_members', 'current_tier_id')):
        return FOLLOW
    try:
        r = c.execute('''SELECT t.points_redeem_rate
                         FROM club_members m
                         JOIN club_member_tiers t ON t.id=m.current_tier_id AND t.club_id=m.club_id
                         WHERE m.club_id=? AND m.user_id=?''',
                      (int(club_id), int(user_id))).fetchone()
        return coerce_rate(r[0]) if r else FOLLOW
    except Exception:
        logger.exception('tier points_redeem_rate lookup failed (club=%s user=%s)', club_id, user_id)
        return FOLLOW


def resolve(c, *, club_id: int, user_id: int | None,
            setting: Callable[[str, Any], Any]) -> int:
    """返回本次订单真正生效的「X 积分 = ¥1」，恒为正整数。

    报价（BookingEngine）与下单（CheckoutEngine）都必须传同一个
    `c / club_id / user_id / setting`，保证同源。
    `user_id=None` 表示没有会员上下文（后台读俱乐部默认生效值）。
    """
    platform = _platform_rate(setting)
    club = _club_rate(c, club_id)
    tier = _member_tier_rate(c, club_id, user_id) if user_id is not None else FOLLOW
    return tier or club or platform or DEFAULT_RATE


def describe(c, *, club_id: int, user_id: int | None,
             setting: Callable[[str, Any], Any]) -> dict:
    """给后台「积分策略」页的完整口径说明：各级原值 + 生效值 + 来源 + 等级一览。

    只用于展示，不参与任何金额计算；计算一律走 `resolve()`。
    """
    platform = _platform_rate(setting)
    club = _club_rate(c, club_id)
    base = club or platform                       # 不含等级覆盖时的俱乐部默认生效值
    base_source = 'club' if club else 'platform'

    tiers: list[dict] = []
    if _has_column(c, 'club_member_tiers', 'points_redeem_rate'):
        try:
            rows = c.execute('''SELECT id,name,rank,access_mode,status,points_redeem_rate
                                FROM club_member_tiers WHERE club_id=? ORDER BY rank,id''',
                             (int(club_id),)).fetchall()
            for t in rows:
                t = dict(t)
                tr = coerce_rate(t.get('points_redeem_rate'))
                tiers.append({
                    'tierId': int(t['id']),
                    'name': t['name'],
                    'rank': int(t.get('rank') or 0),
                    'accessMode': str(t.get('access_mode') or 'CONDITION'),
                    'status': str(t.get('status') or 'active'),
                    'pointsRedeemRate': tr,
                    'effectiveRate': tr or base,
                    'source': 'tier' if tr else base_source,
                })
        except Exception:
            logger.exception('tier rate overview failed (club=%s)', club_id)

    current_tier = None
    effective = base
    source = base_source
    if user_id is not None:
        try:
            m = c.execute('''SELECT m.current_tier_id, t.name, t.points_redeem_rate
                             FROM club_members m
                             LEFT JOIN club_member_tiers t ON t.id=m.current_tier_id AND t.club_id=m.club_id
                             WHERE m.club_id=? AND m.user_id=?''',
                          (int(club_id), int(user_id))).fetchone()
            if m and m['current_tier_id']:
                tr = coerce_rate(m['points_redeem_rate'])
                current_tier = {'tierId': int(m['current_tier_id']), 'name': m['name'],
                                'pointsRedeemRate': tr}
                if tr:
                    effective, source = tr, 'tier'
        except Exception:
            logger.exception('current tier lookup failed (club=%s user=%s)', club_id, user_id)

    return {
        'platformRate': platform,
        'clubRate': club,
        'clubEffectiveRate': base,
        'effectiveRate': effective,
        'source': source,
        'currentTier': current_tier,
        'tiers': tiers,
        'minRate': MIN_RATE,
        'maxRate': MAX_RATE,
        'follow': FOLLOW,
    }
