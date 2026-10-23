from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import uuid


@dataclass(frozen=True)
class CommissionPolicy:
    after_sales_days: int = 7
    # 起结门槛（元）：可申请佣金不足时不允许发起结算——对齐主流推广费/佣金结算
    # 平台的做法（满 100 才能提现），避免平台被碎片化的小额打款淹没。
    min_settlement: float = 100.0


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
        try:
            minimum = float(self.setting("commission_min_settlement", 100) or 100)
        except Exception:
            minimum = 100.0
        return CommissionPolicy(after_sales_days=max(0, min(days, 90)),
                                min_settlement=max(0.0, minimum))

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
            "policy": {"afterSalesDays": self.policy().after_sales_days,
                       "minSettlement": round(self.policy().min_settlement, 2)},
            "pending": round(float(by_status.get("pending", 0) or 0), 2),
            "frozen": round(float(by_status.get("frozen", 0) or 0), 2),
            "available": round(available, 2),
            "reserved": round(float(by_status.get("reserved", 0) or 0), 2),
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

        # reserved（俱乐部已申请、平台待打款）视同已结算：这笔佣金已经承诺要打给俱乐部，
        # 退款不能把它从结算单里抽回来，只能进「下期负向冲抵」，与 settled 同一语义。
        settled_earn = sum(float(r.get("amount") or 0) for r in earn_rows
                           if r.get("status") in ("settled", "reserved"))
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

    # ── 俱乐部主动申请结算（2026-10-24 用户参考「保险推广费结算」模式）─────────
    # 参考模型的三段式：可申请金额（带起结门槛）→ 俱乐部发起申请 → 平台核对账户打款。
    # 申请单与结算单共用 commission_settlements 表：status='requested' 表示「俱乐部
    # 已申请、等平台打款」，平台 confirm_payout 填 payment_ref 后转 'paid'。
    # 申请即把对应佣金流水标为 'reserved'（预留在结算单上）——preview 只取
    # status='available'，所以预留中的流水不会被重复申请/重复结算。

    def request_settlement(self, c, *, club_id: int, note: str = "") -> dict[str, Any]:
        policy = self.policy()
        # 幂等在前：已有进行中的申请单（requested）时直接返回它——此时可申请流水
        # 已全部 reserved、preview 必然为空，先查流水会把重复申请误判成「无流水」。
        open_req = c.execute(
            "SELECT * FROM commission_settlements WHERE club_id=? AND status='requested' ORDER BY id DESC LIMIT 1",
            (club_id,),
        ).fetchone()
        if open_req:
            return {**dict(open_req), "idempotent": True}
        preview = self.preview(c, club_id=club_id)
        net = float(preview["netAmount"] or 0)
        if not preview["entries"]:
            raise ValueError("当前没有可申请结算的佣金流水；佣金在订单签收并过售后期后转入可申请")
        if net < policy.min_settlement:
            raise ValueError(f"可申请佣金 ¥{net:,.2f} 未达到起结门槛 ¥{policy.min_settlement:g}，继续累计后再来申请")
        acct = c.execute(
            "SELECT id FROM club_settlement_accounts WHERE club_id=? AND status='active'", (club_id,)
        ).fetchone()
        if not acct:
            raise ValueError("请先在「商城 · 结算账户」登记法人卡或对公账户，平台才能打款")
        sid = "cst_" + uuid.uuid4().hex[:20]
        # payment_ref 在 schema 里 NOT NULL UNIQUE：申请单先用占位凭证号 pending:{sid}
        # （天然唯一），平台确认打款时被真实凭证号原位替换——不做表重建迁移。
        c.execute(
            """INSERT INTO commission_settlements(
                 id,club_id,gross_amount,deduction_amount,net_amount,entry_count,status,payment_ref,note,created_by)
               VALUES(?,?,?,?,?,?,'requested',?,?,?)""",
            (sid, club_id, preview["grossAmount"], preview["deductionAmount"], net,
             preview["entryCount"], f"pending:{sid}", note or "俱乐部主动申请结算", f"club:{club_id}"),
        )
        ids = [int(x["id"]) for x in preview["entries"]]
        marks = ",".join("?" for _ in ids)
        c.execute(
            f"""UPDATE commission_ledger
                SET status='reserved',settlement_id=?
                WHERE id IN ({marks}) AND status='available' AND settlement_id IS NULL""",
            [sid, *ids],
        )
        row = c.execute("SELECT * FROM commission_settlements WHERE id=?", (sid,)).fetchone()
        return {**dict(row), "idempotent": False}

    def confirm_payout(self, c, *, settlement_id: str, payment_ref: str) -> dict[str, Any]:
        s = c.execute("SELECT * FROM commission_settlements WHERE id=?", (settlement_id,)).fetchone()
        if not s:
            raise LookupError("结算单不存在")
        s = dict(s)
        if s["status"] == "paid":
            return {**s, "idempotent": True}
        if s["status"] != "requested":
            raise ValueError("当前结算单状态不可确认打款")
        payment_ref = str(payment_ref or "").strip()
        if not payment_ref:
            raise ValueError("paymentRef 必填")
        dup = c.execute("SELECT id FROM commission_settlements WHERE payment_ref=?", (payment_ref,)).fetchone()
        if dup and str(dup[0]) != settlement_id:
            raise ValueError("paymentRef 已被其他结算单使用")
        c.execute(
            "UPDATE commission_settlements SET status='paid',payment_ref=?,paid_at=CURRENT_TIMESTAMP WHERE id=?",
            (payment_ref, settlement_id),
        )
        c.execute(
            "UPDATE commission_ledger SET status='settled',settled_at=CURRENT_TIMESTAMP WHERE settlement_id=? AND status='reserved'",
            (settlement_id,),
        )
        return {**dict(c.execute("SELECT * FROM commission_settlements WHERE id=?", (settlement_id,)).fetchone()),
                "idempotent": False}

    def monthly(self, c, *, club_id: int, limit: int = 12) -> list[dict[str, Any]]:
        """按 +8 本地日历月汇总佣金：产生 / 冲抵 / 已结算 / 未结，用于结算页的按月明细表。

        归月口径用 COALESCE(available_at, created_at)——佣金真正「归属」可支配的时间点，
        与申请门槛看的是同一批钱，避免「这个月产生的佣金下个月才可申请」造成的对账困惑。
        """
        self.release_matured(c, club_id=club_id)
        rs = c.execute(
            """SELECT substr(COALESCE(available_at,created_at),1,7) ym,
                      ROUND(COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0),2) earned,
                      ROUND(COALESCE(SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END),0),2) reversed,
                      ROUND(COALESCE(SUM(CASE WHEN status='settled' THEN amount ELSE 0 END),0),2) settled,
                      ROUND(COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0)
                           -COALESCE(SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END),0),2) net
               FROM commission_ledger
               WHERE club_id=? AND ledger_type IN ('earn','refund_reverse')
               GROUP BY ym ORDER BY ym DESC LIMIT ?""",
            (club_id, max(1, min(int(limit), 36))),
        ).fetchall()
        return [dict(r) for r in rs]
