from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any


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
    accept_gear_points: bool = True
    gear_points_max_discount_amount: float | None = None
    club_points_earn_rate_override: float | None = None
    platform_gear_points_allowed: bool = True

    @property
    def effective_accept_club_points(self) -> bool:
        return self.enabled and self.accept_club_points

    @property
    def effective_accept_gear_points(self) -> bool:
        return self.enabled and self.accept_gear_points and self.platform_gear_points_allowed

    @property
    def effective_earn_club_points(self) -> bool:
        return self.enabled and self.earn_club_points

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "earnClubPoints": self.earn_club_points,
            "acceptClubPoints": self.accept_club_points,
            "clubPointsMaxDiscountPercent": round(self.club_points_max_discount_percent, 4),
            "acceptGearPoints": self.accept_gear_points,
            "gearPointsMaxDiscountAmount": self.gear_points_max_discount_amount,
            "clubPointsEarnRateOverride": self.club_points_earn_rate_override,
            "platformGearPointsAllowed": self.platform_gear_points_allowed,
            "effective": {
                "earnClubPoints": self.effective_earn_club_points,
                "acceptClubPoints": self.effective_accept_club_points,
                "acceptGearPoints": self.effective_accept_gear_points,
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

    def from_activity(self, activity: dict[str, Any]) -> ActivityPointsPolicy:
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
        )

    def normalize_update(self, payload: dict[str, Any], current: ActivityPointsPolicy | None = None) -> ActivityPointsPolicy:
        base = current or ActivityPointsPolicy(platform_gear_points_allowed=self.platform_gear_allowed())
        p = ActivityPointsPolicy(
            enabled=_bool(payload.get("enabled"), base.enabled),
            earn_club_points=_bool(payload.get("earnClubPoints"), base.earn_club_points),
            accept_club_points=_bool(payload.get("acceptClubPoints"), base.accept_club_points),
            club_points_max_discount_percent=max(0.0, min(100.0, _num(payload.get("clubPointsMaxDiscountPercent"), base.club_points_max_discount_percent))),
            accept_gear_points=_bool(payload.get("acceptGearPoints"), base.accept_gear_points),
            gear_points_max_discount_amount=(
                base.gear_points_max_discount_amount
                if "gearPointsMaxDiscountAmount" not in payload
                else (None if payload.get("gearPointsMaxDiscountAmount") in (None, "") else max(0.0, _num(payload.get("gearPointsMaxDiscountAmount"), 0.0)))
            ),
            club_points_earn_rate_override=(
                base.club_points_earn_rate_override
                if "clubPointsEarnRateOverride" not in payload
                else (None if payload.get("clubPointsEarnRateOverride") in (None, "") else max(0.0, _num(payload.get("clubPointsEarnRateOverride"), 0.0)))
            ),
            platform_gear_points_allowed=self.platform_gear_allowed(),
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
