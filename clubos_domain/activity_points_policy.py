from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

from clubos_domain.gear_points_policy import GearPointsPolicy


def _bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _num(v: Any, default=None):
    if v is None or v == "":
        return default
    return float(v)


@dataclass(frozen=True)
class ActivityPointsPolicy:
    enabled: bool = True
    earn_club_points: bool = True
    accept_club_points: bool = True
    club_points_max_discount_percent: float = 100.0
    #: 活动级「允许装备积分抵扣」开关 —— 2026-10-24 起**不再参与判定**。
    #: 装备积分是总平台出钱，准入权归平台（套餐资格 + 按俱乐部的比例），
    #: 俱乐部自己开/关只会让同一套权益在不同俱乐部完全失控。字段保留只为兼容旧数据与历史快照。
    accept_gear_points: bool = True
    #: 活动级装备积分抵扣金额上限 —— 同上，不再参与判定（保留兼容）。
    gear_points_max_discount_amount: float | None = None
    club_points_earn_rate_override: float | None = None
    platform_gear_points_allowed: bool = True
    #: 总平台解析出的装备积分规则（资格 + 生效比例）。由调用方注入。
    gear_points_policy: GearPointsPolicy = GearPointsPolicy()

    @property
    def effective_accept_club_points(self) -> bool:
        return self.enabled and self.accept_club_points

    @property
    def effective_accept_gear_points(self) -> bool:
        """装备积分能否用于本场活动。

        只看两件事：活动整体是否参与积分（enabled）、以及总平台给这个俱乐部的规则。
        活动自己那份 accept_gear_points 开关不再有话语权。
        """
        return bool(self.enabled and self.platform_gear_points_allowed and self.gear_points_policy.allowed)

    @property
    def effective_gear_points_percent(self) -> float:
        """本场活动装备积分最多能抵订单金额的百分之几（0 = 不可抵）。"""
        if not self.effective_accept_gear_points:
            return 0.0
        return max(0.0, min(100.0, float(self.gear_points_policy.redeem_percent)))

    @property
    def effective_earn_club_points(self) -> bool:
        return self.enabled and self.earn_club_points

    def as_dict(self) -> dict[str, Any]:
        gp = self.gear_points_policy
        return {
            "enabled": self.enabled,
            "earnClubPoints": self.earn_club_points,
            "acceptClubPoints": self.accept_club_points,
            "clubPointsMaxDiscountPercent": round(self.club_points_max_discount_percent, 4),
            "acceptGearPoints": self.accept_gear_points,
            "gearPointsMaxDiscountAmount": self.gear_points_max_discount_amount,
            "clubPointsEarnRateOverride": self.club_points_earn_rate_override,
            "platformGearPointsAllowed": self.platform_gear_points_allowed,
            # 装备积分规则全部来自总平台：C 端与俱乐部端都据此展示，不再由活动自己声明。
            "gearPointsRule": {
                "allowed": bool(gp.allowed),
                "redeemPercent": round(self.effective_gear_points_percent, 4),
                "planCode": gp.plan_code,
                "planName": gp.plan_name,
                "planPercent": round(gp.plan_percent, 4),
                "hasClubOverride": bool(gp.has_club_override),
                "clubPercent": round(gp.club_percent, 4),
                "reason": gp.reason,
            },
            "effective": {
                "earnClubPoints": self.effective_earn_club_points,
                "acceptClubPoints": self.effective_accept_club_points,
                "acceptGearPoints": self.effective_accept_gear_points,
                "gearPointsRedeemPercent": round(self.effective_gear_points_percent, 4),
            },
        }


class ActivityPointsPolicyService:
    """ClubOS-owned activity points policy.

    The open-source commerce/loyalty layer may persist balances and orders, but it must
    not decide whether an activity earns or accepts Club Points / Gear Points.
    """

    def __init__(self, setting_getter):
        self.setting = setting_getter

    def platform_gear_allowed(self) -> bool:
        return _bool(self.setting("gear_points_activity_redeem_enabled", "1"), True)

    def from_activity(self, activity: dict[str, Any],
                      gear_points_policy: GearPointsPolicy | None = None) -> ActivityPointsPolicy:
        """组装这场活动的积分规则。

        `gear_points_policy` 是总平台解析出来的装备积分规则（资格 + 生效比例）。
        传 None 表示调用方没有解析（例如只想看活动积分部分），此时装备积分一律不可用 ——
        宁可少给权益，也绝不能让「没查过」变成「默认放行」。
        """
        enabled = _bool(activity.get("points_enabled"), True)
        return ActivityPointsPolicy(
            enabled=enabled,
            earn_club_points=_bool(activity.get("earn_club_points"), True),
            accept_club_points=_bool(activity.get("accept_club_points"), True),
            club_points_max_discount_percent=max(0.0, min(100.0, _num(activity.get("club_points_max_discount_percent"), 100.0))),
            accept_gear_points=_bool(activity.get("accept_gear_points"), True),
            gear_points_max_discount_amount=(None if activity.get("gear_points_max_discount_amount") is None else max(0.0, _num(activity.get("gear_points_max_discount_amount"), 0.0))),
            club_points_earn_rate_override=(None if activity.get("club_points_earn_rate_override") is None else max(0.0, _num(activity.get("club_points_earn_rate_override"), 0.0))),
            platform_gear_points_allowed=self.platform_gear_allowed(),
            gear_points_policy=gear_points_policy or GearPointsPolicy(
                allowed=False, reason="未解析总平台的装备积分规则"),
        )

    def normalize_update(self, payload: dict[str, Any], current: ActivityPointsPolicy | None = None) -> ActivityPointsPolicy:
        base = current or ActivityPointsPolicy(platform_gear_points_allowed=self.platform_gear_allowed())
        # 装备积分的准入与比例归总平台，俱乐部改不动 —— 这里即使 payload 带了相关键也忽略。
        p = ActivityPointsPolicy(
            enabled=_bool(payload.get("enabled"), base.enabled),
            earn_club_points=_bool(payload.get("earnClubPoints"), base.earn_club_points),
            accept_club_points=_bool(payload.get("acceptClubPoints"), base.accept_club_points),
            club_points_max_discount_percent=max(0.0, min(100.0, _num(payload.get("clubPointsMaxDiscountPercent"), base.club_points_max_discount_percent))),
            # 装备积分：准入与比例归总平台，俱乐部改不动 —— payload 里带了也忽略，
            # 一律跟随 base（DB 现值），避免出现「后台改了但平台规则没变」的假象。
            accept_gear_points=base.accept_gear_points,
            gear_points_max_discount_amount=base.gear_points_max_discount_amount,
            club_points_earn_rate_override=(
                base.club_points_earn_rate_override
                if "clubPointsEarnRateOverride" not in payload
                else (None if payload.get("clubPointsEarnRateOverride") in (None, "") else max(0.0, _num(payload.get("clubPointsEarnRateOverride"), 0.0)))
            ),
            platform_gear_points_allowed=self.platform_gear_allowed(),
            gear_points_policy=base.gear_points_policy,
        )
        return p

    def persist(self, c, activity_id: int, club_id: int, policy: ActivityPointsPolicy):
        c.execute(
            '''UPDATE activities SET
               points_enabled=?, earn_club_points=?, accept_club_points=?,
               club_points_max_discount_percent=?, accept_gear_points=?,
               gear_points_max_discount_amount=?, club_points_earn_rate_override=?,
               updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND club_id=?''',
            (
                1 if policy.enabled else 0,
                1 if policy.earn_club_points else 0,
                1 if policy.accept_club_points else 0,
                float(policy.club_points_max_discount_percent),
                1 if policy.accept_gear_points else 0,
                policy.gear_points_max_discount_amount,
                policy.club_points_earn_rate_override,
                activity_id,
                club_id,
            ),
        )
