from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any


class MerchandiseFinanceEngine:
    """Supplier A/P plus merchandise contribution-profit logic owned by ClubOS."""

    def _id(self, prefix: str) -> str:
        return prefix + "_" + uuid.uuid4().hex[:20]

    @staticmethod
    def _money(v: Any) -> float:
        return round(float(v or 0), 2)

    def _refresh_payable(self, c, payable_id: str) -> dict[str, Any]:
        r = c.execute("SELECT * FROM supplier_payables WHERE id=?", (payable_id,)).fetchone()
        if not r:
            raise LookupError("供应商应付不存在")
        d = dict(r)
        outstanding = max(
            0.0,
            self._money(d["original_amount"])
            - self._money(d["credit_applied"])
            - self._money(d["paid_amount"]),
        )
        status = (
            "paid"
            if outstanding <= 0.004
            else ("partial" if self._money(d["credit_applied"]) + self._money(d["paid_amount"]) > 0 else "open")
        )
        c.execute(
            "UPDATE supplier_payables SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, payable_id),
        )
        d = dict(c.execute("SELECT * FROM supplier_payables WHERE id=?", (payable_id,)).fetchone())
        d["outstandingAmount"] = round(outstanding, 2)
        return d

    def _apply_available_credits(self, c, *, supplier_id: int) -> float:
        applied = 0.0
        credits = [
            dict(x)
            for x in c.execute(
                "SELECT * FROM supplier_credits WHERE supplier_id=? AND status='available' ORDER BY created_at,id",
                (supplier_id,),
            ).fetchall()
        ]
        for cr in credits:
            remain = self._money(cr["amount"]) - self._money(cr["used_amount"])
            if remain <= 0.004:
                continue
            payables = [
                dict(x)
                for x in c.execute(
                    "SELECT * FROM supplier_payables WHERE supplier_id=? AND status IN ('open','partial') "
                    "ORDER BY COALESCE(due_at,created_at),created_at,id",
                    (supplier_id,),
                ).fetchall()
            ]
            for p in payables:
                out = max(
                    0.0,
                    self._money(p["original_amount"])
                    - self._money(p["credit_applied"])
                    - self._money(p["paid_amount"]),
                )
                take = min(remain, out)
                if take <= 0.004:
                    continue
                c.execute(
                    "INSERT OR IGNORE INTO supplier_credit_allocations(credit_id,payable_id,amount) VALUES(?,?,?)",
                    (cr["id"], p["id"], take),
                )
                c.execute(
                    "UPDATE supplier_credits SET used_amount=used_amount+?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (take, cr["id"]),
                )
                c.execute(
                    "UPDATE supplier_payables SET credit_applied=credit_applied+?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (take, p["id"]),
                )
                self._refresh_payable(c, p["id"])
                applied += take
                remain -= take
                if remain <= 0.004:
                    break
            used = self._money(c.execute("SELECT used_amount FROM supplier_credits WHERE id=?", (cr["id"],)).fetchone()[0])
            amt = self._money(cr["amount"])
            c.execute(
                "UPDATE supplier_credits SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                ("used" if used >= amt - 0.004 else "available", cr["id"]),
            )
        return round(applied, 2)

    def register_receipt_payable(self, c, *, receipt_id: str) -> dict[str, Any]:
        old = c.execute("SELECT * FROM supplier_payables WHERE receipt_id=?", (receipt_id,)).fetchone()
        if old:
            d = dict(old)
            d["outstandingAmount"] = round(
                max(0, float(d["original_amount"]) - float(d["credit_applied"]) - float(d["paid_amount"])), 2
            )
            return d
        r = c.execute(
            "SELECT r.*,s.payment_term_days FROM purchase_receipts r JOIN suppliers s ON s.id=r.supplier_id WHERE r.id=?",
            (receipt_id,),
        ).fetchone()
        if not r:
            raise LookupError("采购入库单不存在")
        amount = self._money(r["total_cost"])
        days = max(0, int(r["payment_term_days"] or 0))
        try:
            base = datetime.fromisoformat(str(r["received_at"]).replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            base = datetime.utcnow()
        due = (base + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        pid = self._id("spb")
        c.execute(
            "INSERT INTO supplier_payables(id,supplier_id,purchase_order_id,receipt_id,original_amount,status,due_at,note) "
            "VALUES(?,?,?,?,?,'open',?,?)",
            (pid, r["supplier_id"], r["purchase_order_id"], receipt_id, amount, due, f"采购入库 {receipt_id} 应付"),
        )
        self._apply_available_credits(c, supplier_id=int(r["supplier_id"]))
        return self._refresh_payable(c, pid)

    def create_credit(
        self, c, *, supplier_id: int, source_type: str, source_id: str, amount: float, note: str = ""
    ) -> dict[str, Any]:
        old = c.execute(
            "SELECT * FROM supplier_credits WHERE source_type=? AND source_id=?", (source_type, source_id)
        ).fetchone()
        if old:
            return {**dict(old), "idempotent": True}
        amt = self._money(amount)
        if amt <= 0:
            raise ValueError("供应商贷项金额必须大于 0")
        cid = self._id("scr")
        c.execute(
            "INSERT INTO supplier_credits(id,supplier_id,source_type,source_id,amount,status,note) "
            "VALUES(?,?,?,?,?,'available',?)",
            (cid, supplier_id, source_type, source_id, amt, note),
        )
        self._apply_available_credits(c, supplier_id=supplier_id)
        return dict(c.execute("SELECT * FROM supplier_credits WHERE id=?", (cid,)).fetchone())

    def create_payment(
        self,
        c,
        *,
        supplier_id: int,
        amount: float,
        payment_ref: str,
        payment_method: str = "",
        note: str = "",
        paid_by: str = "platform",
    ) -> dict[str, Any]:
        ref = str(payment_ref or "").strip()
        if not ref:
            raise ValueError("paymentRef required")
        old = c.execute("SELECT * FROM supplier_payments WHERE payment_ref=?", (ref,)).fetchone()
        if old:
            if int(old["supplier_id"]) != int(supplier_id):
                raise ValueError("paymentRef 已被其他供应商付款使用")
            return {**dict(old), "idempotent": True}
        self._apply_available_credits(c, supplier_id=supplier_id)
        payables = [
            dict(x)
            for x in c.execute(
                "SELECT * FROM supplier_payables WHERE supplier_id=? AND status IN ('open','partial') "
                "ORDER BY COALESCE(due_at,created_at),created_at,id",
                (supplier_id,),
            ).fetchall()
        ]
        outstanding = sum(
            max(
                0.0,
                self._money(p["original_amount"])
                - self._money(p["credit_applied"])
                - self._money(p["paid_amount"]),
            )
            for p in payables
        )
        amt = self._money(amount)
        if amt <= 0:
            raise ValueError("付款金额必须大于 0")
        if amt > outstanding + 0.004:
            raise ValueError(f"付款金额超过当前应付余额 {outstanding:.2f}")
        pid = self._id("spp")
        c.execute(
            "INSERT INTO supplier_payments(id,supplier_id,amount,payment_ref,payment_method,note,paid_by) VALUES(?,?,?,?,?,?,?)",
            (pid, supplier_id, amt, ref, payment_method, note, paid_by),
        )
        remain = amt
        alloc = []
        for p in payables:
            out = max(
                0.0,
                self._money(p["original_amount"])
                - self._money(p["credit_applied"])
                - self._money(p["paid_amount"]),
            )
            take = min(remain, out)
            if take <= 0.004:
                continue
            c.execute(
                "INSERT INTO supplier_payment_allocations(payment_id,payable_id,amount) VALUES(?,?,?)",
                (pid, p["id"], take),
            )
            c.execute(
                "UPDATE supplier_payables SET paid_amount=paid_amount+?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (take, p["id"]),
            )
            self._refresh_payable(c, p["id"])
            alloc.append({"payableId": p["id"], "amount": round(take, 2)})
            remain -= take
            if remain <= 0.004:
                break
        out = dict(c.execute("SELECT * FROM supplier_payments WHERE id=?", (pid,)).fetchone())
        out["allocations"] = alloc
        out["idempotent"] = False
        return out

    def supplier_summary(self, c) -> list[dict[str, Any]]:
        out = []
        for s0 in c.execute("SELECT * FROM suppliers ORDER BY status='active' DESC,name").fetchall():
            s = dict(s0)
            sid = int(s["id"])
            self._apply_available_credits(c, supplier_id=sid)
            p = c.execute(
                "SELECT COALESCE(SUM(original_amount),0),COALESCE(SUM(credit_applied),0),COALESCE(SUM(paid_amount),0),"
                "COALESCE(SUM(CASE WHEN status IN ('open','partial') AND due_at<CURRENT_TIMESTAMP "
                "THEN original_amount-credit_applied-paid_amount ELSE 0 END),0) FROM supplier_payables WHERE supplier_id=?",
                (sid,),
            ).fetchone()
            credit = c.execute(
                "SELECT COALESCE(SUM(amount-used_amount),0) FROM supplier_credits WHERE supplier_id=? AND status='available'",
                (sid,),
            ).fetchone()[0]
            variance = c.execute(
                "SELECT COALESCE(SUM(ri.price_variance),0) FROM purchase_receipt_items ri "
                "JOIN purchase_receipts r ON r.id=ri.receipt_id WHERE r.supplier_id=?",
                (sid,),
            ).fetchone()[0]
            original, credits, paid = self._money(p[0]), self._money(p[1]), self._money(p[2])
            outstanding = max(0.0, original - credits - paid)
            out.append(
                {
                    "supplierId": sid,
                    "supplierName": s["name"],
                    "supplierCode": s["code"],
                    "paymentTermDays": int(s.get("payment_term_days") or 0),
                    "totalPayables": original,
                    "creditsApplied": credits,
                    "paidAmount": paid,
                    "outstanding": round(outstanding, 2),
                    "overdue": self._money(p[3]),
                    "availableCredit": self._money(credit),
                    "purchasePriceVariance": self._money(variance),
                }
            )
        return out

    def supplier_account(self, c, *, supplier_id: int) -> dict[str, Any]:
        s = c.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        if not s:
            raise LookupError("供应商不存在")
        self._apply_available_credits(c, supplier_id=supplier_id)
        payables = []
        for x in c.execute(
            "SELECT p.*,r.received_at FROM supplier_payables p JOIN purchase_receipts r ON r.id=p.receipt_id "
            "WHERE p.supplier_id=? ORDER BY p.created_at DESC",
            (supplier_id,),
        ).fetchall():
            d = dict(x)
            d["outstandingAmount"] = round(
                max(0, float(d["original_amount"]) - float(d["credit_applied"]) - float(d["paid_amount"])), 2
            )
            payables.append(d)
        payments = [dict(x) for x in c.execute("SELECT * FROM supplier_payments WHERE supplier_id=? ORDER BY paid_at DESC,id DESC", (supplier_id,)).fetchall()]
        credits = [dict(x) for x in c.execute("SELECT * FROM supplier_credits WHERE supplier_id=? ORDER BY created_at DESC,id DESC", (supplier_id,)).fetchall()]
        summary = next((x for x in self.supplier_summary(c) if x["supplierId"] == supplier_id), None)
        return {"supplier": dict(s), "summary": summary, "payables": payables, "payments": payments, "credits": credits}

    def _restocked_qty(self, c, order_id: int, order_item_id: int, order_status: str) -> int:
        item=c.execute("SELECT product_id,quantity FROM gear_order_items WHERE id=?",(order_item_id,)).fetchone()
        if not item:return 0
        after_sales=int(c.execute(
            "SELECT COALESCE(SUM(ai.quantity),0) FROM after_sales_items ai JOIN after_sales_cases ac ON ac.id=ai.case_id "
            "WHERE ai.order_item_id=? AND ai.restocked=1 AND ac.status IN ('completed','refunded')",
            (order_item_id,),
        ).fetchone()[0] or 0)
        legacy=int(c.execute("SELECT COALESCE(SUM(quantity_delta),0) FROM inventory_movements WHERE product_id=? AND reference_type='gear_refund' AND reference_id=? AND quantity_delta>0",(item['product_id'],str(order_id))).fetchone()[0] or 0)
        return min(int(item['quantity'] or 0),after_sales+legacy)

    def order_profit(self, c, order_id: int) -> dict[str, Any]:
        o0 = c.execute("SELECT * FROM gear_orders WHERE id=?", (order_id,)).fetchone()
        if not o0:
            raise LookupError("装备订单不存在")
        o = dict(o0)
        items, cogs = [], 0.0
        for x0 in c.execute(
            "SELECT i.*,p.name product_name,p.sku FROM gear_order_items i JOIN products p ON p.id=i.product_id WHERE i.order_id=? ORDER BY i.id",
            (order_id,),
        ).fetchall():
            x = dict(x0)
            returned = self._restocked_qty(c, order_id, int(x["id"]), str(o["status"]))
            net_qty = max(0, int(x["quantity"]) - returned)
            line_cost = round(net_qty * float(x.get("unit_cost_snapshot") or 0), 2)
            cogs += line_cost
            x.update({"restockedQty": returned, "netCostQty": net_qty, "lineCogs": line_cost})
            items.append(x)
        gross = self._money(o["total"])
        refund_goods = min(gross, self._money(o.get("refunded_goods_total")))
        net_sales = max(0.0, gross - refund_goods)
        refund_ratio = min(1.0, refund_goods / gross) if gross else 0.0
        point_sub = self._money(o.get("platform_point_subsidy")) * (1 - refund_ratio)
        benefit_sub = 0.0 if str(o.get("status")) == "refunded" else self._money(o.get("platform_benefit_subsidy"))
        commission = self._money(c.execute("SELECT COALESCE(SUM(amount),0) FROM commission_ledger WHERE order_id=?", (order_id,)).fetchone()[0])
        shipping = self._money(o.get("shipping_cost"))
        packaging = self._money(o.get("packaging_cost"))
        cogs = round(cogs, 2)
        gross_profit = round(net_sales - cogs, 2)
        contribution = round(net_sales - cogs - point_sub - benefit_sub - commission - shipping - packaging, 2)
        return {
            "orderId": order_id,
            "sourceClubId": o["source_club_id"],
            "status": o["status"],
            "grossSales": gross,
            "refundGoods": refund_goods,
            "refundCash": self._money(o.get("refunded_cash_total")),
            "netSales": round(net_sales, 2),
            "cogs": cogs,
            "grossProfit": gross_profit,
            "grossMarginPct": round((gross_profit / net_sales * 100), 2) if net_sales else 0.0,
            "platformPointSubsidy": round(point_sub, 2),
            "platformBenefitSubsidy": round(benefit_sub, 2),
            "clubCommission": commission,
            "shippingCost": shipping,
            "packagingCost": packaging,
            "contributionProfit": contribution,
            "contributionMarginPct": round((contribution / net_sales * 100), 2) if net_sales else 0.0,
            "items": items,
        }

    def profit_summary(self, c) -> dict[str, Any]:
        ids = [int(x[0]) for x in c.execute("SELECT id FROM gear_orders ORDER BY id").fetchall()]
        orders = [self.order_profit(c, x) for x in ids]
        keys = [
            "grossSales",
            "refundGoods",
            "netSales",
            "cogs",
            "grossProfit",
            "platformPointSubsidy",
            "platformBenefitSubsidy",
            "clubCommission",
            "shippingCost",
            "packagingCost",
            "contributionProfit",
        ]
        sums = {k: round(sum(float(o[k] or 0) for o in orders), 2) for k in keys}
        sums["orderCount"] = len(orders)
        sums["grossMarginPct"] = round(sums["grossProfit"] / sums["netSales"] * 100, 2) if sums["netSales"] else 0.0
        sums["contributionMarginPct"] = round(sums["contributionProfit"] / sums["netSales"] * 100, 2) if sums["netSales"] else 0.0
        return sums

    def order_profits(self, c, *, limit: int = 200) -> list[dict[str, Any]]:
        ids = [int(x[0]) for x in c.execute("SELECT id FROM gear_orders ORDER BY id DESC LIMIT ?", (max(1, min(1000, int(limit))),)).fetchall()]
        return [self.order_profit(c, x) for x in ids]

    def product_profit(self, c) -> list[dict[str, Any]]:
        out = []
        for p0 in c.execute("SELECT * FROM products ORDER BY id").fetchall():
            p = dict(p0)
            gross, cogs, qty = 0.0, 0.0, 0
            for i0 in c.execute(
                "SELECT i.*,o.status FROM gear_order_items i JOIN gear_orders o ON o.id=i.order_id WHERE i.product_id=?",
                (p["id"],),
            ).fetchall():
                i = dict(i0)
                returned = self._restocked_qty(c, int(i["order_id"]), int(i["id"]), str(i["status"]))
                net = max(0, int(i["quantity"]) - returned)
                qty += net
                gross += net * float(i["unit_price"] or 0)
                cogs += net * float(i.get("unit_cost_snapshot") or 0)
            gp = round(gross - cogs, 2)
            out.append(
                {
                    "productId": p["id"],
                    "name": p["name"],
                    "sku": p["sku"],
                    "netUnits": qty,
                    "netSales": round(gross, 2),
                    "cogs": round(cogs, 2),
                    "grossProfit": gp,
                    "grossMarginPct": round(gp / gross * 100, 2) if gross else 0.0,
                    "currentAverageCost": self._money(p.get("average_cost")),
                }
            )
        return out
