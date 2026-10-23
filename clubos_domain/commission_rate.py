"""俱乐部佣金率策略（2026-10-24 用户反馈）。

背景：装备商城的佣金率原本只有「商品级」一个维度（products.commission_rate，
默认 8%），所有俱乐部拿同一个比例。但实际经营里，每个俱乐部的渠道能力、
合作深度、议价能力都不同，佣金率应该由**总平台按俱乐部单独设定**。

判定链（一条都不能跳）：
    平台总开关（commission_enabled）→ 俱乐部专属费率 → 商品默认费率
  实际生效费率 = 俱乐部专属费率（若已单独设定且启用）
                否则 商品自己的 commission_rate（平台给商品的默认佣金率）

关键设计：**成交时快照费率**。
    佣金一旦产生就写进 commission_ledger / gear_orders.club_commission，
    之后平台改费率不影响历史订单。退款冲回必须用「当初成交时的费率」，
    不能重读商品当前费率 —— 否则平台改一次费率，历史订单的退款冲回金额
    就会漂移（这是改造前 commerce_refund.py 里真实存在的隐患）。
    因此 gear_order_items 增加 commission_rate_snapshot 列存成交时费率。

商品级费率仍然保留：它是「该商品给所有俱乐部的默认佣金率」，
俱乐部专属费率是「这个俱乐部的谈判结果」，后者优先级更高。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_RATE = 1.0          # 100% —— 佣金不可能超过订单金额本身
DEFAULT_RATE = 0.08     # 与 products.commission_rate 的库默认值一致


@dataclass(frozen=True)
class ClubCommissionRate:
    """某个俱乐部的佣金率解析结果。"""

    club_id: int
    rate: float = DEFAULT_RATE            # 实际生效费率
    source: str = "product_default"       # club_override | product_default
    enabled: bool = True                  # 平台是否允许发放佣金
    has_club_override: bool = False       # 是否单独设定过
    club_rate: float = 0.0                # 单独设定的费率（未设定时为 0）
    product_rate: float = DEFAULT_RATE    # 商品默认费率（参考值）
    note: str = ""                        # 平台填写的备注（如「年度合作协议 2026」）
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "clubId": self.club_id,
            "rate": round(self.rate, 6),
            "ratePercent": round(self.rate * 100, 2),
            "source": self.source,
            "enabled": self.enabled,
            "hasClubOverride": self.has_club_override,
            "clubRate": round(self.club_rate, 6),
            "clubRatePercent": round(self.club_rate * 100, 2),
            "productRate": round(self.product_rate, 6),
            "productRatePercent": round(self.product_rate * 100, 2),
            "note": self.note,
            "reason": self.reason,
        }


class ClubCommissionRateService:
    """总平台按俱乐部设定佣金率。

    数据落在 club_commission_rates 表（每个俱乐部一行）。
    """

    def __init__(self, setting_getter):
        self.setting = setting_getter

    # ── 平台总开关 ────────────────────────────────────────────────
    def platform_enabled(self) -> bool:
        raw = self.setting("commission_enabled", "1")
        return str(raw).strip().lower() not in ("0", "false", "off", "no")

    # ── 读取 ─────────────────────────────────────────────────────
    def club_override(self, c, club_id: int) -> dict[str, Any] | None:
        row = c.execute(
            "SELECT club_id,rate,enabled,note,updated_at FROM club_commission_rates WHERE club_id=?",
            (int(club_id),),
        ).fetchone()
        if not row:
            return None
        return {
            "clubId": int(row["club_id"]),
            "rate": float(row["rate"] or 0),
            "enabled": bool(row["enabled"]),
            "note": str(row["note"] or ""),
            "updatedAt": str(row["updated_at"] or ""),
        }

    def resolve(self, c, club_id: int, product_rate: float | None = None) -> ClubCommissionRate:
        """解析某俱乐部在某商品（或默认商品）上的实际生效佣金率。"""
        club_id = int(club_id or 0)
        prod_rate = DEFAULT_RATE if product_rate is None else float(product_rate or 0)

        if not self.platform_enabled():
            return ClubCommissionRate(
                club_id=club_id, rate=0.0, source="platform_disabled",
                enabled=False, product_rate=prod_rate,
                reason="总平台已关闭俱乐部佣金发放",
            )

        override = self.club_override(c, club_id)
        if override and override["enabled"] and override["rate"] > 0:
            return ClubCommissionRate(
                club_id=club_id,
                rate=override["rate"],
                source="club_override",
                enabled=True,
                has_club_override=True,
                club_rate=override["rate"],
                product_rate=prod_rate,
                note=override["note"],
                reason="总平台已为该俱乐部单独设定佣金率",
            )

        if override and not override["enabled"]:
            return ClubCommissionRate(
                club_id=club_id, rate=0.0, source="club_disabled",
                enabled=False, has_club_override=True,
                club_rate=override["rate"], product_rate=prod_rate,
                note=override["note"],
                reason="总平台已单独关闭该俱乐部的佣金",
            )

        return ClubCommissionRate(
            club_id=club_id, rate=prod_rate, source="product_default",
            enabled=True, product_rate=prod_rate,
            reason="沿用商品默认佣金率（该俱乐部未单独设定）",
        )

    def rate_for_product(self, c, club_id: int, product_rate: float) -> float:
        """下单场景：拿到某商品在某俱乐部下的实际生效费率。"""
        return self.resolve(c, club_id, product_rate).rate

    # ── 写入 ─────────────────────────────────────────────────────
    def set_club_rate(
        self, c, club_id: int, *, rate: float, enabled: bool = True, note: str = ""
    ) -> dict[str, Any]:
        club_id = int(club_id)
        if not c.execute("SELECT 1 FROM clubs WHERE id=?", (club_id,)).fetchone():
            raise LookupError("俱乐部不存在")
        rate = float(rate)
        if rate < 0 or rate > MAX_RATE:
            raise ValueError("佣金率必须在 0 与 1（0%~100%）之间")
        if enabled and rate <= 0:
            raise ValueError("启用佣金时佣金率必须大于 0")
        c.execute(
            """INSERT INTO club_commission_rates(club_id,rate,enabled,note,updated_at)
               VALUES(?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(club_id) DO UPDATE SET
                 rate=excluded.rate, enabled=excluded.enabled,
                 note=excluded.note, updated_at=CURRENT_TIMESTAMP""",
            (club_id, rate, 1 if enabled else 0, str(note or "")),
        )
        return self.club_override(c, club_id) or {}

    def clear_club_rate(self, c, club_id: int) -> dict[str, Any]:
        """清除单独设定 → 回到商品默认费率。"""
        club_id = int(club_id)
        c.execute("DELETE FROM club_commission_rates WHERE club_id=?", (club_id,))
        return {"clubId": club_id, "cleared": True}

    def set_platform_enabled(self, c, enabled: bool) -> dict[str, Any]:
        c.execute(
            "INSERT INTO platform_settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("commission_enabled", "1" if enabled else "0"),
        )
        return {"enabled": bool(enabled)}
