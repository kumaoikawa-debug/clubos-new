"""装备积分（Gear Points）抵用资格与比例的判定规则。

背景（2026-10-24 用户指出的设计错误）
--------------------------------------
原先「装备积分能不能抵活动报名」由**俱乐部自己在每场活动上开开关**
（`activities.accept_gear_points`），平台只有一个全局总开关。结果：

* 同一套权益在不同俱乐部差别极大，俱乐部自己就能开/关，成本全落在总平台头上。
* 平台无法按俱乐部差异化定价 —— 同一个「Pro 套餐」下，A 俱乐部能抵 100%、B 俱乐部一分抵不了。

正确模型（用户明确要求）
-----------------------
1. **套餐决定资格**：俱乐部订阅什么套餐，就有没有装备积分抵用这项权益。
   基础套餐（Starter）没有；Pro / Enterprise 有。
2. **总平台按俱乐部决定比例**：在套餐资格之内，总平台可以针对不同俱乐部
   设计不同的「单笔最多可抵订单金额的百分之几」。
3. **活动级字段不再参与判定**：`activities.accept_gear_points` 不再是准入条件
   （保留字段仅为兼容旧数据与历史快照，避免老库读不出来）。

判定顺序（一条都不能跳）
-----------------------
    平台总开关 → 套餐资格 → 平台按俱乐部的比例 → 得出「实际生效比例」
    实际生效比例 = 0 或平台总开关关闭 ⇒ 完全不可抵用
    实际生效比例 > 0 ⇒ 允许抵用，且单笔抵扣上限 = 订单金额 × 比例

比例口径：抵扣**订单金额**的百分比（与「Club Points 抵扣上限 %」口径一致），
不是抵扣「扣除 Club Points 之后的剩余额」的百分比 —— 两者在同时用两种积分时会
产生口径漂移，顾客看到「最多抵 ¥30」结果被算成 ¥12。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "GearPointsPolicy",
    "GearPointsPolicyService",
    "MAX_REDEEM_PERCENT",
]

# 平台允许配置的上限。单笔最多抵订单金额的 100% 已经是极端值，
# 再往上没有业务意义（且会让「实付 0 元」的活动看起来像 bug），直接卡死。
MAX_REDEEM_PERCENT = 100.0


def _num(v: Any, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class GearPointsPolicy:
    """某个俱乐部当前的装备积分抵用规则（已解析完毕，可直接用于报价）。"""

    allowed: bool = False
    #: 单笔最多可抵订单金额的百分比（0 表示不可抵用）
    redeem_percent: float = 0.0
    #: 平台总开关是否开启
    platform_enabled: bool = True
    #: 俱乐部当前订阅的套餐 code（可能为空）
    plan_code: str = ""
    plan_name: str = ""
    #: 套餐自带的资格比例（= ai_credit_plans.gear_points_redeem_percent）
    plan_percent: float = 0.0
    #: 平台是否针对该俱乐部单独设定了比例
    has_club_override: bool = False
    club_percent: float = 0.0
    #: 不可用时给前端看的白话原因
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "redeemPercent": round(self.redeem_percent, 4),
            "platformEnabled": self.platform_enabled,
            "planCode": self.plan_code,
            "planName": self.plan_name,
            "planPercent": round(self.plan_percent, 4),
            "hasClubOverride": self.has_club_override,
            "clubPercent": round(self.club_percent, 4),
            "reason": self.reason,
        }


class GearPointsPolicyService:
    """解析「某俱乐部当前能不能用装备积分抵用、最多抵多少」。

    所有报价链路（公开报价 / 报名 / 下单）必须走这里，
    否则「C 端看到的最多可抵」和「结算真的扣掉的」会漂移 —— 那是真金白银的纠纷。
    """

    def __init__(self, setting_getter, plan_lookup=None):
        #: setting_getter(key, default) -> str
        self.setting = setting_getter
        #: plan_lookup(c, club_id) -> dict|None，返回俱乐部当前套餐的
        #: {code, name, gear_points_redeem_percent}；留空则用默认 SQL。
        self.plan_lookup = plan_lookup or self._default_plan_lookup

    # ---------- 平台总开关 ----------

    def platform_enabled(self) -> bool:
        raw = str(self.setting("gear_points_activity_redeem_enabled", "1")).strip().lower()
        return raw in {"1", "true", "yes", "on"}

    # ---------- 套餐 ----------

    @staticmethod
    def _default_plan_lookup(c, club_id: int) -> dict[str, Any] | None:
        r = c.execute(
            """SELECT p.code,p.name,p.gear_points_redeem_percent
               FROM clubs cl
               LEFT JOIN club_ai_subscriptions s ON s.club_id=cl.id AND s.status='active'
               LEFT JOIN ai_credit_plans p ON p.code=COALESCE(s.plan_code,cl.plan)
               WHERE cl.id=?""",
            (club_id,),
        ).fetchone()
        if r is None:
            return None
        return {
            "code": str(r["code"] or ""),
            "name": str(r["name"] or ""),
            "gear_points_redeem_percent": _num(
                r["gear_points_redeem_percent"] if "gear_points_redeem_percent" in r.keys() else 0
            ),
        }

    # ---------- 平台按俱乐部的单独设定 ----------

    def club_override(self, c, club_id: int) -> dict[str, Any] | None:
        r = c.execute(
            "SELECT redeem_percent,enabled,note FROM club_gear_points_policies WHERE club_id=?",
            (club_id,),
        ).fetchone()
        if r is None:
            return None
        return {
            "redeem_percent": max(0.0, min(MAX_REDEEM_PERCENT, _num(r["redeem_percent"]))),
            "enabled": bool(r["enabled"]),
            "note": str(r["note"] or ""),
        }

    # ---------- 主判定 ----------

    def resolve(self, c, club_id: int) -> GearPointsPolicy:
        club_id = int(club_id)

        if not self.platform_enabled():
            return GearPointsPolicy(
                allowed=False,
                platform_enabled=False,
                reason="总平台已关闭装备积分抵用活动报名",
            )

        plan = self.plan_lookup(c, club_id) or {}
        plan_code = str(plan.get("code") or "")
        plan_name = str(plan.get("name") or "")
        plan_percent = max(0.0, min(MAX_REDEEM_PERCENT, _num(plan.get("gear_points_redeem_percent"))))

        # 套餐没给资格 → 无论平台怎么设比例都没用（基础套餐没有这项权益）。
        if not plan_code or plan_percent <= 0:
            return GearPointsPolicy(
                allowed=False,
                platform_enabled=True,
                plan_code=plan_code,
                plan_name=plan_name,
                plan_percent=plan_percent,
                reason=(
                    f"当前套餐「{plan_name or plan_code or '未知'}」不包含装备积分抵用权益"
                    if plan_code else "该俱乐部尚未订阅任何套餐，暂无装备积分抵用权益"
                ),
            )

        override = self.club_override(c, club_id)
        if override and not override["enabled"]:
            return GearPointsPolicy(
                allowed=False,
                platform_enabled=True,
                plan_code=plan_code,
                plan_name=plan_name,
                plan_percent=plan_percent,
                has_club_override=True,
                club_percent=override["redeem_percent"],
                reason="总平台已单独关闭该俱乐部的装备积分抵用",
            )

        if override:
            # 平台针对该俱乐部单独设定的比例；不允许超过套餐自带比例 ——
            # 套餐是「权益上限」，平台只能在这个额度内分配，不能凭空放大。
            club_percent = min(override["redeem_percent"], plan_percent)
            if club_percent <= 0:
                return GearPointsPolicy(
                    allowed=False,
                    platform_enabled=True,
                    plan_code=plan_code,
                    plan_name=plan_name,
                    plan_percent=plan_percent,
                    has_club_override=True,
                    club_percent=0.0,
                    reason="总平台已单独关闭该俱乐部的装备积分抵用",
                )
            return GearPointsPolicy(
                allowed=True,
                redeem_percent=club_percent,
                platform_enabled=True,
                plan_code=plan_code,
                plan_name=plan_name,
                plan_percent=plan_percent,
                has_club_override=True,
                club_percent=club_percent,
                reason="按总平台为该俱乐部设定的比例执行",
            )

        # 没有单独设定 → 用套餐默认比例。
        return GearPointsPolicy(
            allowed=True,
            redeem_percent=plan_percent,
            platform_enabled=True,
            plan_code=plan_code,
            plan_name=plan_name,
            plan_percent=plan_percent,
            has_club_override=False,
            club_percent=0.0,
            reason="按套餐默认比例执行",
        )

    # ---------- 平台侧写入 ----------

    def set_club_policy(self, c, club_id: int, *, redeem_percent: float, enabled: bool = True,
                        note: str = "") -> dict[str, Any]:
        club_id = int(club_id)
        club = c.execute("SELECT id FROM clubs WHERE id=?", (club_id,)).fetchone()
        if not club:
            raise ValueError("俱乐部不存在")
        pct = max(0.0, min(MAX_REDEEM_PERCENT, _num(redeem_percent)))
        if pct > 0 and not enabled:
            # 关掉的同时给了比例 → 以「关闭」为准，避免出现「设了比例但生效不了」的糊涂状态。
            pct = 0.0
        c.execute(
            """INSERT INTO club_gear_points_policies(club_id,redeem_percent,enabled,note,updated_at)
               VALUES(?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(club_id) DO UPDATE SET redeem_percent=excluded.redeem_percent,
                 enabled=excluded.enabled,note=excluded.note,updated_at=CURRENT_TIMESTAMP""",
            (club_id, pct, 1 if enabled and pct > 0 else 0, str(note or "")),
        )
        return self.resolve(c, club_id).as_dict()

    def clear_club_policy(self, c, club_id: int) -> dict[str, Any]:
        """清除单独设定 → 回到套餐默认比例。"""
        c.execute("DELETE FROM club_gear_points_policies WHERE club_id=?", (int(club_id),))
        return self.resolve(c, club_id).as_dict()
