from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any


class CommerceAnalyticsEngine:
    """Real-time Commerce BI and replenishment recommendations for ClubOS Platform.

    Analytics is derived from ClubOS domain facts: Gear orders, WMS stock, procurement,
    supplier relationships and the v0.21 profit ledger. Recommendations never mutate
    inventory. A purchase order is created only through the explicit platform action.
    """

    DEFAULT_POLICY = {
        "salesWindowDays": 30,
        "targetCoverDays": 30,
        "safetyDays": 7,
        "slowMovingDays": 60,
    }
    SETTING_KEYS = {
        "salesWindowDays": "replenishment_sales_window_days",
        "targetCoverDays": "replenishment_target_cover_days",
        "safetyDays": "replenishment_safety_days",
        "slowMovingDays": "replenishment_slow_moving_days",
    }

    def __init__(self, finance_engine, warehouse_engine):
        self.finance = finance_engine
        self.warehouse = warehouse_engine

    @staticmethod
    def _money(v: Any) -> float:
        return round(float(v or 0), 2)

    @staticmethod
    def _int(v: Any) -> int:
        try:
            return int(v or 0)
        except Exception:
            return 0

    def policy(self, c) -> dict[str, int]:
        out: dict[str, int] = {}
        for public, key in self.SETTING_KEYS.items():
            r = c.execute("SELECT value FROM platform_settings WHERE key=?", (key,)).fetchone()
            try:
                out[public] = int(r[0]) if r else int(self.DEFAULT_POLICY[public])
            except Exception:
                out[public] = int(self.DEFAULT_POLICY[public])
        return out

    def update_policy(self, c, payload: dict[str, Any]) -> dict[str, int]:
        limits = {
            "salesWindowDays": (7, 180),
            "targetCoverDays": (7, 120),
            "safetyDays": (0, 60),
            "slowMovingDays": (30, 365),
        }
        current = self.policy(c)
        for public, (lo, hi) in limits.items():
            if public not in payload:
                continue
            try:
                value = int(payload[public])
            except Exception:
                raise ValueError(f"{public} 必须为整数")
            if value < lo or value > hi:
                raise ValueError(f"{public} 必须在 {lo}~{hi} 之间")
            c.execute(
                "INSERT INTO platform_settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (self.SETTING_KEYS[public], str(value)),
            )
            current[public] = value
        return current

    def _orders_since(self, c, window_days: int) -> list[dict[str, Any]]:
        rows = c.execute(
            "SELECT id,source_club_id,created_at FROM gear_orders "
            "WHERE created_at>=datetime('now', ?) ORDER BY id",
            (f"-{max(1, int(window_days))} days",),
        ).fetchall()
        return [dict(x) for x in rows]

    def _product_sales_window(self, c, window_days: int) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for o in self._orders_since(c, window_days):
            profit = self.finance.order_profit(c, int(o["id"]))
            for item in profit["items"]:
                pid = int(item["product_id"])
                q = max(0, int(item.get("netCostQty") or 0))
                rec = out.setdefault(pid, {"units": 0, "grossSales": 0.0, "cogs": 0.0, "orderIds": set()})
                rec["units"] += q
                rec["grossSales"] += q * float(item.get("unit_price") or 0)
                rec["cogs"] += q * float(item.get("unit_cost_snapshot") or 0)
                if q:
                    rec["orderIds"].add(int(o["id"]))
        return out

    def _physical(self, c, product_id: int) -> tuple[int, int, int]:
        r = c.execute(
            "SELECT COALESCE(SUM(on_hand),0),COALESCE(SUM(reserved),0),"
            "COALESCE(SUM(on_hand-reserved),0) FROM warehouse_inventory WHERE product_id=?",
            (product_id,),
        ).fetchone()
        return self._int(r[0]), self._int(r[1]), self._int(r[2])

    def _primary_supplier(self, c, product_id: int) -> dict[str, Any] | None:
        r = c.execute(
            """SELECT ps.*,s.name supplier_name,s.code supplier_code,
                      CASE WHEN ps.lead_time_days>0 THEN ps.lead_time_days ELSE s.lead_time_days END effective_lead_time
               FROM product_suppliers ps JOIN suppliers s ON s.id=ps.supplier_id
               WHERE ps.product_id=? AND ps.status='active' AND s.status='active'
               ORDER BY ps.is_primary DESC,ps.id DESC LIMIT 1""",
            (product_id,),
        ).fetchone()
        return dict(r) if r else None

    def _inbound_open(self, c, product_id: int) -> int:
        r = c.execute(
            """SELECT COALESCE(SUM(CASE WHEN poi.quantity_ordered>poi.quantity_received
                          THEN poi.quantity_ordered-poi.quantity_received ELSE 0 END),0)
               FROM purchase_order_items poi JOIN purchase_orders po ON po.id=poi.purchase_order_id
               WHERE poi.product_id=? AND po.status IN ('ordered','partially_received')""",
            (product_id,),
        ).fetchone()
        return self._int(r[0])

    def product_analytics(self, c, *, window_days: int | None = None) -> list[dict[str, Any]]:
        policy = self.policy(c)
        window = max(1, int(window_days or policy["salesWindowDays"]))
        recent = self._product_sales_window(c, window)
        out: list[dict[str, Any]] = []
        now = datetime.utcnow()
        for p0 in c.execute("SELECT * FROM products ORDER BY id").fetchall():
            p = dict(p0)
            pid = int(p["id"])
            sales = recent.get(pid, {"units": 0, "grossSales": 0.0, "cogs": 0.0, "orderIds": set()})
            units = int(sales["units"])
            daily = units / float(window)
            on_hand, reserved, physical_available = self._physical(c, pid)
            # products.stock is the canonical sellable quantity. Keep both values visible
            # and use the lower one defensively for replenishment decisions.
            sellable = max(0, int(p.get("stock") or 0))
            available = min(sellable, max(0, physical_available)) if on_hand or reserved else sellable
            inbound = self._inbound_open(c, pid)
            supplier = self._primary_supplier(c, pid)
            lead = int((supplier or {}).get("effective_lead_time") or 0)
            moq = max(1, int((supplier or {}).get("min_order_qty") or 1))
            target_days = lead + int(policy["safetyDays"]) + int(policy["targetCoverDays"])
            target_stock = int(math.ceil(daily * target_days)) if daily > 0 else 0
            raw_need = max(0, target_stock - available - inbound)
            recommended = max(raw_need, moq) if raw_need > 0 and supplier else 0
            days_cover = round(available / daily, 1) if daily > 0 else None
            stockout_days = int(math.floor(available / daily)) if daily > 0 else None
            last_sale = c.execute(
                "SELECT MAX(o.created_at) FROM gear_order_items i JOIN gear_orders o ON o.id=i.order_id "
                "WHERE i.product_id=?",
                (pid,),
            ).fetchone()[0]
            last_sale_age = None
            if last_sale:
                try:
                    last_sale_age = max(0, (now - datetime.fromisoformat(str(last_sale))).days)
                except Exception:
                    pass
            product_age = None
            try:
                product_age = max(0, (now - datetime.fromisoformat(str(p.get("created_at") or ""))).days)
            except Exception:
                pass
            slow = available > 0 and (
                (last_sale_age is not None and last_sale_age >= int(policy["slowMovingDays"]))
                or (last_sale_age is None and product_age is not None and product_age >= int(policy["slowMovingDays"]))
            )
            reorder_point = max(0, int(p.get("reorder_point") or 0))
            if available <= 0 and inbound <= 0:
                health, reason = "stockout", "可售库存为 0 且无在途采购"
            elif daily > 0 and (available + inbound) < daily * (lead + int(policy["safetyDays"])):
                health, reason = "at_risk", "库存无法覆盖交期与安全期"
            elif raw_need > 0:
                health, reason = "replenish", "按销量与目标覆盖天数需要补货"
            elif slow:
                health, reason = "slow_moving", "超过慢动销阈值仍有库存"
            elif available <= reorder_point:
                health, reason = "low_stock", "低于人工补货点；近期销量不足以自动下单" if daily <= 0 else "低于人工补货点"
            else:
                health, reason = "healthy", "库存覆盖正常"
            if not supplier and raw_need > 0:
                health, reason = "needs_supplier", "需要补货但未配置可用供应商"
            gp = round(float(sales["grossSales"]) - float(sales["cogs"]), 2)
            out.append({
                "productId": pid,
                "name": p["name"],
                "sku": p["sku"],
                "status": p["status"],
                "windowDays": window,
                "soldUnits": units,
                "orderCount": len(sales["orderIds"]),
                "salesVelocityPerDay": round(daily, 3),
                "windowSales": round(float(sales["grossSales"]), 2),
                "windowCogs": round(float(sales["cogs"]), 2),
                "windowGrossProfit": gp,
                "windowGrossMarginPct": round(gp / float(sales["grossSales"]) * 100, 2) if sales["grossSales"] else 0.0,
                "sellableStock": sellable,
                "onHand": on_hand,
                "reserved": reserved,
                "available": available,
                "inboundOpenQty": inbound,
                "inventoryValue": round(on_hand * float(p.get("average_cost") or 0), 2),
                "averageCost": round(float(p.get("average_cost") or 0), 2),
                "reorderPoint": reorder_point,
                "daysCover": days_cover,
                "estimatedStockoutDays": stockout_days,
                "lastSaleAt": last_sale,
                "lastSaleAgeDays": last_sale_age,
                "health": health,
                "healthReason": reason,
                "primarySupplierId": int(supplier["supplier_id"]) if supplier else None,
                "primarySupplier": supplier.get("supplier_name") if supplier else None,
                "leadTimeDays": lead,
                "minOrderQty": moq if supplier else None,
                "purchasePrice": round(float(supplier.get("purchase_price") or 0), 2) if supplier else None,
                "targetStock": target_stock,
                "recommendedReorderQty": recommended,
            })
        health_rank = {"stockout": 0, "at_risk": 1, "needs_supplier": 2, "replenish": 3, "low_stock": 4, "slow_moving": 5, "healthy": 6}
        return sorted(out, key=lambda x: (health_rank.get(x["health"], 99), -int(x["recommendedReorderQty"]), x["productId"]))

    def replenishment(self, c, *, window_days: int | None = None) -> list[dict[str, Any]]:
        return [x for x in self.product_analytics(c, window_days=window_days) if x["health"] != "healthy"]

    def club_analytics(self, c, *, window_days: int | None = None) -> list[dict[str, Any]]:
        policy = self.policy(c)
        window = max(1, int(window_days or policy["salesWindowDays"]))
        clubs = {int(x["id"]): dict(x) for x in c.execute("SELECT * FROM clubs ORDER BY id").fetchall()}
        agg: dict[int, dict[str, Any]] = {}
        for o in self._orders_since(c, window):
            cid = int(o["source_club_id"])
            p = self.finance.order_profit(c, int(o["id"]))
            a = agg.setdefault(cid, {"orders": 0, "gross": 0.0, "net": 0.0, "refund": 0.0, "contribution": 0.0})
            a["orders"] += 1
            a["gross"] += float(p["grossSales"] or 0)
            a["net"] += float(p["netSales"] or 0)
            a["refund"] += float(p["refundGoods"] or 0)
            a["contribution"] += float(p["contributionProfit"] or 0)
        out = []
        for cid, a in agg.items():
            commission = self._money(c.execute(
                "SELECT COALESCE(SUM(amount),0) FROM commission_ledger WHERE club_id=? AND created_at>=datetime('now',?)",
                (cid, f"-{window} days"),
            ).fetchone()[0])
            gross = round(a["gross"], 2)
            out.append({
                "clubId": cid,
                "clubName": clubs.get(cid, {}).get("name", f"Club {cid}"),
                "orderCount": int(a["orders"]),
                "grossSales": gross,
                "netSales": round(a["net"], 2),
                "refundGoods": round(a["refund"], 2),
                "refundRatePct": round(a["refund"] / gross * 100, 2) if gross else 0.0,
                "aov": round(a["net"] / a["orders"], 2) if a["orders"] else 0.0,
                "commission": commission,
                "platformContributionProfit": round(a["contribution"], 2),
            })
        return sorted(out, key=lambda x: (-x["netSales"], x["clubId"]))

    def supplier_analytics(self, c) -> list[dict[str, Any]]:
        finance_map = {int(x["supplierId"]): x for x in self.finance.supplier_summary(c)}
        out = []
        for s0 in c.execute("SELECT * FROM suppliers ORDER BY status='active' DESC,name").fetchall():
            s = dict(s0); sid = int(s["id"])
            ordered = self._int(c.execute(
                "SELECT COALESCE(SUM(i.quantity_ordered),0) FROM purchase_order_items i JOIN purchase_orders po ON po.id=i.purchase_order_id WHERE po.supplier_id=? AND po.status!='cancelled'",
                (sid,),
            ).fetchone()[0])
            received = self._int(c.execute(
                "SELECT COALESCE(SUM(i.quantity),0) FROM purchase_receipt_items i JOIN purchase_receipts r ON r.id=i.receipt_id WHERE r.supplier_id=?",
                (sid,),
            ).fetchone()[0])
            returned = self._int(c.execute(
                "SELECT COALESCE(SUM(i.quantity),0) FROM purchase_return_items i JOIN purchase_returns r ON r.id=i.purchase_return_id WHERE r.supplier_id=? AND r.status IN ('shipped','credited')",
                (sid,),
            ).fetchone()[0])
            lead = c.execute(
                "SELECT AVG(julianday(r.received_at)-julianday(po.ordered_at)) FROM purchase_receipts r JOIN purchase_orders po ON po.id=r.purchase_order_id WHERE r.supplier_id=? AND po.ordered_at IS NOT NULL",
                (sid,),
            ).fetchone()[0]
            receipts_with_due = self._int(c.execute(
                "SELECT COUNT(*) FROM purchase_receipts r JOIN purchase_orders po ON po.id=r.purchase_order_id WHERE r.supplier_id=? AND po.expected_at IS NOT NULL",
                (sid,),
            ).fetchone()[0])
            on_time = self._int(c.execute(
                "SELECT COUNT(*) FROM purchase_receipts r JOIN purchase_orders po ON po.id=r.purchase_order_id WHERE r.supplier_id=? AND po.expected_at IS NOT NULL AND datetime(r.received_at)<=datetime(po.expected_at)",
                (sid,),
            ).fetchone()[0])
            f = finance_map.get(sid, {})
            out.append({
                "supplierId": sid,
                "supplierName": s["name"],
                "supplierCode": s["code"],
                "activeSkuCount": self._int(c.execute("SELECT COUNT(*) FROM product_suppliers WHERE supplier_id=? AND status='active'", (sid,)).fetchone()[0]),
                "orderedUnits": ordered,
                "receivedUnits": received,
                "fillRatePct": round(received / ordered * 100, 2) if ordered else 0.0,
                "averageLeadDays": round(float(lead), 1) if lead is not None else None,
                "onTimeReceiptRatePct": round(on_time / receipts_with_due * 100, 2) if receipts_with_due else None,
                "returnedUnits": returned,
                "returnRatePct": round(returned / received * 100, 2) if received else 0.0,
                "purchasePriceVariance": self._money(f.get("purchasePriceVariance")),
                "outstandingPayable": self._money(f.get("outstanding")),
                "overduePayable": self._money(f.get("overdue")),
            })
        return out

    def dashboard(self, c, *, window_days: int | None = None) -> dict[str, Any]:
        policy = self.policy(c)
        window = max(1, int(window_days or policy["salesWindowDays"]))
        products = self.product_analytics(c, window_days=window)
        finance = self.finance.profit_summary(c)
        window_orders = [self.finance.order_profit(c, int(x["id"])) for x in self._orders_since(c, window)]
        window_contribution = round(sum(float(x["contributionProfit"] or 0) for x in window_orders), 2)
        window_net_sales = round(sum(float(x["netSales"] or 0) for x in window_orders), 2)
        wh = self.warehouse.summary(c)
        inventory_value = round(sum(float(x["inventoryValue"] or 0) for x in products), 2)
        sold_units = sum(int(x["soldUnits"] or 0) for x in products)
        daily_units = sold_units / float(window)
        total_available = sum(int(x["available"] or 0) for x in products)
        risk = sum(1 for x in products if x["health"] in ("stockout", "at_risk", "replenish", "needs_supplier"))
        slow = [x for x in products if x["health"] == "slow_moving"]
        cases = self._int(c.execute("SELECT COUNT(*) FROM after_sales_cases WHERE created_at>=datetime('now',?)", (f"-{window} days",)).fetchone()[0])
        after_sales_orders = self._int(c.execute("SELECT COUNT(DISTINCT order_id) FROM after_sales_cases WHERE created_at>=datetime('now',?)", (f"-{window} days",)).fetchone()[0])
        order_count = self._int(c.execute("SELECT COUNT(*) FROM gear_orders WHERE created_at>=datetime('now',?)", (f"-{window} days",)).fetchone()[0])
        registrations = self._int(c.execute("SELECT COUNT(*) FROM registrations WHERE created_at>=datetime('now',?)", (f"-{window} days",)).fetchone()[0])
        window_gross = round(sum(float(x["windowSales"] or 0) for x in products), 2)
        return {
            "windowDays": window,
            "grossSales": window_gross,
            "orderCount": order_count,
            "soldUnits": sold_units,
            "afterSalesCases": cases,
            "afterSalesOrderCount": after_sales_orders,
            "afterSalesRatePct": round(after_sales_orders / order_count * 100, 2) if order_count else 0.0,
            "inventoryValue": inventory_value,
            "onHand": wh["onHand"],
            "reserved": wh["reserved"],
            "available": total_available,
            "inventoryDaysCover": round(total_available / daily_units, 1) if daily_units > 0 else None,
            "stockRiskSkuCount": risk,
            "slowMovingSkuCount": len(slow),
            "slowMovingInventoryValue": round(sum(float(x["inventoryValue"] or 0) for x in slow), 2),
            "windowContributionProfit": window_contribution,
            "windowContributionMarginPct": round(window_contribution / window_net_sales * 100, 2) if window_net_sales else 0.0,
            "allTimeContributionProfit": finance["contributionProfit"],
            "allTimeContributionMarginPct": finance["contributionMarginPct"],
            "activityRegistrations": registrations,
            "gearGmvPer100Registrations": round(window_gross / registrations * 100, 2) if registrations else None,
            "policy": policy,
        }
