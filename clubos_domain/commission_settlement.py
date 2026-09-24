from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import uuid


@dataclass(frozen=True)
class CommissionPolicy:
    after_sales_days: int = 7


class CommissionSettlementEngine:
    """ClubOS-owned Gear commission lifecycle and settlement ledger.

    Medusa owns commerce primitives only. sourceClub attribution, commission maturity,
    payout and refund offsets remain ClubOS Domain rules.
    """

    def __init__(self, setting_getter):
        self.setting = setting_getter

    def policy(self) -> CommissionPolicy:
        try:
            days = int(float(self.setting("commission_after_sales_days", 7) or 7))
        except Exception:
            days = 7
        return CommissionPolicy(after_sales_days=max(0, min(days, 90)))

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        raw = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            try:
                dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    @staticmethod
    def _fmt(dt: datetime) -> str:
        return dt.replace(microsecond=0).isoformat(sep=" ")

    def freeze_order(self, c, *, order_id: int, delivered_at: str | None = None) -> dict[str, Any]:
        order = c.execute("SELECT * FROM gear_orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            raise LookupError("装备订单不存在")
        order = dict(order)
        if order.get("status") == "refunded":
            return {"ok": True, "orderId": order_id, "status": "refunded", "changed": 0}

        base = self._parse_dt(delivered_at or order.get("delivered_at")) or datetime.utcnow()
        available_at = base + timedelta(days=self.policy().after_sales_days)
        cur = c.execute(
            """UPDATE commission_ledger
               SET status='frozen', frozen_at=COALESCE(frozen_at,CURRENT_TIMESTAMP), available_at=?
               WHERE order_id=? AND ledger_type='earn' AND status='pending'""",
            (self._fmt(available_at), order_id),
        )
        return {
            "ok": True,
            "orderId": order_id,
            "status": "frozen",
            "changed": int(cur.rowcount or 0),
            "availableAt": self._fmt(available_at),
            "afterSalesDays": self.policy().after_sales_days,
        }

    def _backfill_delivered_pending(self, c, *, club_id: int | None = None) -> int:
        sql = """SELECT DISTINCT o.id,o.delivered_at
                 FROM gear_orders o
                 JOIN commission_ledger l ON l.order_id=o.id
                 WHERE o.status IN ('delivered','completed')
                   AND l.ledger_type='earn' AND l.status='pending'"""
        args: list[Any] = []
        if club_id is not None:
            sql += " AND o.source_club_id=?"
            args.append(club_id)
        changed = 0
        for r in c.execute(sql, args).fetchall():
            out = self.freeze_order(c, order_id=int(r["id"]), delivered_at=r["delivered_at"])
            changed += int(out.get("changed") or 0)
        return changed

    def release_matured(self, c, *, club_id: int | None = None, now: datetime | None = None) -> dict[str, Any]:
        frozen = self._backfill_delivered_pending(c, club_id=club_id)
        now_text = self._fmt(now or datetime.utcnow())
        sql = """UPDATE commission_ledger
                 SET status='available'
                 WHERE ledger_type='earn' AND status='frozen'
                   AND available_at IS NOT NULL AND available_at<=?
                   AND NOT EXISTS (
                     SELECT 1 FROM refund_requests rr
                     WHERE rr.gear_order_id=commission_ledger.order_id
                       AND rr.kind='gear' AND rr.status IN ('requested','processing')
                   )
                   AND EXISTS (
                     SELECT 1 FROM gear_orders go
                     WHERE go.id=commission_ledger.order_id AND go.status!='refunded'
                   )"""
        args: list[Any] = [now_text]
        if club_id is not None:
            sql += " AND club_id=?"
            args.append(club_id)
        cur = c.execute(sql, args)
        return {"ok": True, "frozenBackfilled": frozen, "released": int(cur.rowcount or 0), "asOf": now_text}

    def summary(self, c, *, club_id: int) -> dict[str, Any]:
        self.release_matured(c, club_id=club_id)
        by_status = {
            str(r["status"]): round(float(r["amount"] or 0), 2)
            for r in c.execute(
                "SELECT status,COALESCE(SUM(amount),0) amount FROM commission_ledger WHERE club_id=? GROUP BY status",
                (club_id,),
            ).fetchall()
        }
        earned = float(c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM commission_ledger WHERE club_id=? AND ledger_type='earn'",
            (club_id,),
        ).fetchone()[0] or 0)
        reversed_amt = abs(float(c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM commission_ledger WHERE club_id=? AND ledger_type='refund_reverse'",
            (club_id,),
        ).fetchone()[0] or 0))
        available = float(by_status.get("available", 0) or 0)
        return {
            "clubId": club_id,
            "policy": {"afterSalesDays": self.policy().after_sales_days},
            "pending": round(float(by_status.get("pending", 0) or 0), 2),
            "frozen": round(float(by_status.get("frozen", 0) or 0), 2),
            "available": round(available, 2),
            "settled": round(float(by_status.get("settled", 0) or 0), 2),
            "reversedNet": round(float(by_status.get("reversed", 0) or 0), 2),
            "lifetimeEarned": round(earned, 2),
            "lifetimeReversed": round(reversed_amt, 2),
            "payableNow": round(max(0.0, available), 2),
            "carryDebt": round(max(0.0, -available), 2),
        }

    def preview(self, c, *, club_id: int) -> dict[str, Any]:
        summary = self.summary(c, club_id=club_id)
        entries = [dict(r) for r in c.execute(
            """SELECT l.*,o.status order_status,o.refund_status,o.delivered_at
               FROM commission_ledger l JOIN gear_orders o ON o.id=l.order_id
               WHERE l.club_id=? AND l.status='available' AND l.settlement_id IS NULL
               ORDER BY l.id""",
            (club_id,),
        ).fetchall()]
        gross = sum(float(x["amount"] or 0) for x in entries if float(x["amount"] or 0) > 0)
        deductions = abs(sum(float(x["amount"] or 0) for x in entries if float(x["amount"] or 0) < 0))
        net = gross - deductions
        return {
            **summary,
            "entryCount": len(entries),
            "grossAmount": round(gross, 2),
            "deductionAmount": round(deductions, 2),
            "netAmount": round(net, 2),
            "entries": entries,
        }

    def settle_available(
        self,
        c,
        *,
        club_id: int,
        payment_ref: str,
        note: str = "",
        created_by: str = "platform",
    ) -> dict[str, Any]:
        payment_ref = str(payment_ref or "").strip()
        if not payment_ref:
            raise ValueError("paymentRef required")
        existing = c.execute("SELECT * FROM commission_settlements WHERE payment_ref=?", (payment_ref,)).fetchone()
        if existing:
            existing=dict(existing)
            if int(existing.get("club_id") or 0) != int(club_id):
                raise ValueError("paymentRef 已被其他俱乐部结算使用")
            return {**existing, "idempotent": True}

        preview = self.preview(c, club_id=club_id)
        entries = preview["entries"]
        net = float(preview["netAmount"] or 0)
        if not entries:
            raise ValueError("当前没有可结算佣金流水")
        if net <= 0:
            raise ValueError("当前可结算净额不大于 0；负向冲抵需等待后续佣金覆盖")

        sid = "cst_" + uuid.uuid4().hex[:20]
        c.execute(
            """INSERT INTO commission_settlements(
                 id,club_id,gross_amount,deduction_amount,net_amount,entry_count,status,payment_ref,note,created_by,paid_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (
                sid,
                club_id,
                preview["grossAmount"],
                preview["deductionAmount"],
                preview["netAmount"],
                preview["entryCount"],
                "paid",
                payment_ref,
                note,
                created_by,
            ),
        )
        ids = [int(x["id"]) for x in entries]
        marks = ",".join("?" for _ in ids)
        c.execute(
            f"""UPDATE commission_ledger
                SET status='settled',settlement_id=?,settled_at=CURRENT_TIMESTAMP
                WHERE id IN ({marks}) AND status='available' AND settlement_id IS NULL""",
            [sid, *ids],
        )
        return {
            "id": sid,
            "clubId": club_id,
            "status": "paid",
            "grossAmount": preview["grossAmount"],
            "deductionAmount": preview["deductionAmount"],
            "netAmount": preview["netAmount"],
            "entryCount": preview["entryCount"],
            "paymentRef": payment_ref,
            "note": note,
            "idempotent": False,
        }

    def reverse_order(self, c, *, order_id: int, club_id: int, note: str = "装备订单全额退款，冲回俱乐部佣金") -> dict[str, Any]:
        earn_rows = [dict(r) for r in c.execute(
            "SELECT * FROM commission_ledger WHERE order_id=? AND ledger_type='earn' ORDER BY id",
            (order_id,),
        ).fetchall()]
        earned = sum(max(0.0, float(r.get("amount") or 0)) for r in earn_rows)
        reversed_total = abs(float(c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM commission_ledger WHERE order_id=? AND ledger_type='refund_reverse'",
            (order_id,),
        ).fetchone()[0] or 0))
        to_reverse = max(0.0, earned - reversed_total)
        if to_reverse <= 0:
            return {"reversed": 0.0, "futureOffset": 0.0, "idempotent": True}

        settled_earn = sum(float(r.get("amount") or 0) for r in earn_rows if r.get("status") == "settled")
        unsettled_earn = max(0.0, earned - settled_earn)
        unsettled_reverse = min(to_reverse, unsettled_earn)
        settled_reverse = max(0.0, to_reverse - unsettled_reverse)

        if unsettled_reverse > 0:
            c.execute(
                """UPDATE commission_ledger SET status='reversed'
                   WHERE order_id=? AND ledger_type='earn' AND status IN ('pending','frozen','available')""",
                (order_id,),
            )
            c.execute(
                """INSERT INTO commission_ledger(club_id,order_id,amount,status,ledger_type,note)
                   VALUES(?,?,?,?,?,?)""",
                (club_id, order_id, -unsettled_reverse, "reversed", "refund_reverse", note),
            )

        if settled_reverse > 0:
            c.execute(
                """INSERT INTO commission_ledger(club_id,order_id,amount,status,ledger_type,note)
                   VALUES(?,?,?,?,?,?)""",
                (club_id, order_id, -settled_reverse, "available", "refund_reverse",
                 note + "；原佣金已结算，本笔进入下期负向冲抵"),
            )

        return {
            "reversed": round(to_reverse, 2),
            "futureOffset": round(settled_reverse, 2),
            "idempotent": False,
        }

    def settlements(self, c, *, club_id: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT s.*,cl.name club_name FROM commission_settlements s JOIN clubs cl ON cl.id=s.club_id"
        args: list[Any] = []
        if club_id is not None:
            sql += " WHERE s.club_id=?"
            args.append(club_id)
        sql += " ORDER BY s.created_at DESC,s.id DESC"
        return [dict(r) for r in c.execute(sql, args).fetchall()]
