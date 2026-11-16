"""后台统一订单信息台（活动订单 / 营地订单）。

设计边界（2026-11-16）：
- 只读列表把两类订单压成同一形状，前端才能用一张表查、筛、点详情；
- 所有人工写操作（改价 / 代客补资料 / 变更参加人 / 线下退款登记）一律先留审计，
  再改业务表。改价只修正 ClubOS 账面金额，不调用支付渠道，也不伪造支付流水；
- 活动订单的正式退款仍走 RefundLifecycleEngine（申请 → 审核 → 渠道回调）；
  营地订单尚未接入原路退款，后台只能登记线下退款，UI 必须显式说明这一点。
"""
from __future__ import annotations

import json
from typing import Any

ORDER_KINDS = {"activity", "camp"}


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class OrderOpsService:
    def log(self, c, *, club_id: int, order_kind: str, order_id: int, action: str,
            before: dict[str, Any] | None = None, after: dict[str, Any] | None = None,
            reason: str = "", actor: str = "club") -> int:
        c.execute("""INSERT INTO club_order_admin_logs
                     (club_id,order_kind,order_id,action,before_json,after_json,reason,actor)
                     VALUES(?,?,?,?,?,?,?,?)""",
                  (int(club_id), order_kind, int(order_id), action,
                   _j(before) if before is not None else None,
                   _j(after) if after is not None else None,
                   str(reason or "").strip() or None, str(actor or "club")))
        return int(c.execute("SELECT last_insert_rowid()").fetchone()[0])

    def admin_logs(self, c, *, club_id: int, order_kind: str, order_id: int, limit: int = 100) -> list[dict[str, Any]]:
        out = []
        for r in c.execute("""SELECT * FROM club_order_admin_logs
                              WHERE club_id=? AND order_kind=? AND order_id=?
                              ORDER BY id DESC LIMIT ?""",
                           (club_id, order_kind, order_id, max(1, min(200, int(limit))))).fetchall():
            d = dict(r)
            for key in ("before", "after"):
                raw = d.pop(f"{key}_json", None)
                try:
                    d[key] = json.loads(raw) if raw else None
                except Exception:
                    d[key] = raw
            out.append(d)
        return out

    # ---------- 列表 ----------
    def list(self, c, *, club_id: int, kind: str, query: str = "", status: str = "",
             date_from: str = "", date_to: str = "", limit: int = 300) -> dict[str, Any]:
        if kind not in ORDER_KINDS:
            raise ValueError("订单类型无效")
        kw = f"%{str(query or '').strip()}%"
        args: list[Any] = [club_id]
        where = ""
        created_col = "r.created_at" if kind == "activity" else "o.created_at"
        status_col = "r.status" if kind == "activity" else "o.status"
        if str(query or "").strip():
            if kind == "activity":
                where += " AND (CAST(r.id AS TEXT) LIKE ? OR u.name LIKE ? OR IFNULL(u.phone,'') LIKE ? OR a.title LIKE ?)"
            else:
                where += " AND (o.order_no LIKE ? OR IFNULL(o.guest_name,'') LIKE ? OR IFNULL(o.guest_phone,'') LIKE ? OR IFNULL(u.name,'') LIKE ?)"
            args += [kw, kw, kw, kw]
        if str(status or "").strip():
            where += f" AND {status_col}=?"
            args.append(str(status).strip())
        if str(date_from or "").strip():
            where += f" AND date({created_col})>=?"
            args.append(str(date_from).strip()[:10])
        if str(date_to or "").strip():
            where += f" AND date({created_col})<=?"
            args.append(str(date_to).strip()[:10])

        items: list[dict[str, Any]] = []
        if kind == "activity":
            sql = """SELECT r.id, 'activity' order_kind, 'ACT-'||r.id order_no,
                     a.title title, COALESCE(o.label,o.start_at,'') subtitle,
                     u.name buyer, u.phone buyer_phone, r.amount, r.status, r.refund_status,
                     r.created_at, r.participant_count,
                     (SELECT COUNT(*) FROM registration_participants p
                       WHERE p.registration_id=r.id AND p.status='active' AND p.form_status!='complete') incomplete_count,
                     r.club_point_discount, r.platform_point_subsidy
                     FROM registrations r
                     JOIN activities a ON a.id=r.activity_id
                     JOIN users u ON u.id=r.user_id
                     LEFT JOIN activity_occurrences o ON o.id=r.occurrence_id
                     WHERE r.club_id=?""" + where + " ORDER BY r.id DESC LIMIT ?"
            for r in c.execute(sql, args + [max(1, min(500, int(limit)))]).fetchall():
                d = dict(r)
                items.append({
                    "orderKind": "activity", "orderId": int(d["id"]), "orderNo": d["order_no"],
                    "title": d["title"] or "活动报名", "subtitle": d["subtitle"] or "",
                    "buyer": d["buyer"] or "", "buyerPhone": d["buyer_phone"] or "",
                    "amount": float(d["amount"] or 0), "status": d["status"],
                    "refundStatus": d["refund_status"] or "none", "createdAt": d["created_at"],
                    "participantCount": int(d["participant_count"] or 0),
                    "incompleteCount": int(d["incomplete_count"] or 0),
                    "needsAttention": bool(int(d["incomplete_count"] or 0) or d["refund_status"] in ("requested", "processing")),
                })
        else:
            sql = """SELECT o.id, 'camp' order_kind, o.order_no,
                     (SELECT GROUP_CONCAT(i.title,'、') FROM camp_order_items i WHERE i.order_id=o.id) title,
                     o.guest_name, o.guest_phone, o.status, o.total, o.payable, o.deposit_amount,
                     o.created_at, COALESCE(u.name,'') user_name, COALESCE(u.phone,'') user_phone
                     FROM camp_orders o LEFT JOIN users u ON u.id=o.user_id
                     WHERE o.club_id=? AND o.status!='deleted'""" + where + " ORDER BY o.id DESC LIMIT ?"
            for r in c.execute(sql, args + [max(1, min(500, int(limit)))]).fetchall():
                d = dict(r)
                title = str(d["title"] or "").strip()
                items.append({
                    "orderKind": "camp", "orderId": int(d["id"]), "orderNo": d["order_no"],
                    "title": (title[:60] + "…") if len(title) > 60 else (title or f"营地订单 #{d['id']}"),
                    "subtitle": f"押金 {float(d['deposit_amount'] or 0):.2f} 元 · 应付 {float(d['payable'] or 0):.2f} 元",
                    "buyer": d["guest_name"] or d["user_name"] or "", "buyerPhone": d["guest_phone"] or d["user_phone"] or "",
                    "amount": float(d["total"] or 0), "status": d["status"], "refundStatus": "none",
                    "createdAt": d["created_at"], "participantCount": 1, "incompleteCount": 0,
                    "needsAttention": False,
                })
        summary = {
            "count": len(items),
            "amount": round(sum(float(x["amount"]) for x in items), 2),
            "paidAmount": round(sum(float(x["amount"]) for x in items if x["status"] in ("paid", "completed")), 2),
            "incompleteCount": sum(int(x["incompleteCount"]) for x in items),
            "refundPendingCount": sum(1 for x in items if x["refundStatus"] in ("requested", "processing")),
        }
        return {"items": items, "summary": summary}

    # ---------- 详情 ----------
    def activity_detail(self, c, *, club_id: int, order_id: int, participant_service) -> dict[str, Any]:
        r = c.execute("""SELECT r.*, a.title activity_title, a.location, a.event_date,
                         o.label occurrence_label, o.start_at, o.end_at,
                         u.name buyer_name, u.phone buyer_phone, u.id buyer_user_id
                         FROM registrations r JOIN activities a ON a.id=r.activity_id
                         JOIN users u ON u.id=r.user_id
                         LEFT JOIN activity_occurrences o ON o.id=r.occurrence_id
                         WHERE r.id=? AND r.club_id=?""", (order_id, club_id)).fetchone()
        if not r:
            raise LookupError("活动订单不存在")
        d = dict(r)
        participants = participant_service.list_for_registration(c, order_id)
        for p in participants:
            try:
                p["allocation"] = participant_service.financial_allocation(c, int(p["id"]))
            except Exception:
                p["allocation"] = None
        changes = [dict(x) for x in c.execute("""SELECT * FROM participant_change_logs
                                                 WHERE registration_id=? ORDER BY id DESC LIMIT 100""", (order_id,)).fetchall()]
        return {
            "orderKind": "activity", "orderId": order_id,
            "registration": {k: d.get(k) for k in (
                "id", "activity_id", "occurrence_id", "club_id", "user_id", "status", "amount",
                "original_amount", "club_point_discount", "platform_point_subsidy",
                "club_benefit_discount", "platform_benefit_subsidy", "payment_status",
                "refund_status", "refund_cash_amount", "refund_percent", "partial_refund_cash_total",
                "booking_ref", "commerce_order_id", "created_at")},
            "activityTitle": d.get("activity_title") or "", "location": d.get("location") or "",
            "eventDate": d.get("event_date") or "", "occurrenceLabel": d.get("occurrence_label") or d.get("start_at") or "",
            "buyer": {"id": d.get("buyer_user_id"), "name": d.get("buyer_name") or "", "phone": d.get("buyer_phone") or ""},
            "participants": participants, "changes": changes,
            "adminLogs": self.admin_logs(c, club_id=club_id, order_kind="activity", order_id=order_id),
        }

    def camp_detail(self, c, *, club_id: int, order_id: int, camp_out) -> dict[str, Any]:
        r = c.execute("""SELECT o.*, COALESCE(u.name,'') buyer_name, COALESCE(u.phone,'') buyer_phone
                         FROM camp_orders o LEFT JOIN users u ON u.id=o.user_id
                         WHERE o.id=? AND o.club_id=? AND o.status!='deleted'""", (order_id, club_id)).fetchone()
        if not r:
            raise LookupError("营地订单不存在")
        o = dict(r)
        d = camp_out(c, o)
        d.update({
            "orderKind": "camp", "orderId": order_id,
            "buyer": {"id": o.get("user_id"), "name": o.get("buyer_name") or "", "phone": o.get("buyer_phone") or ""},
            "adminLogs": self.admin_logs(c, club_id=club_id, order_kind="camp", order_id=order_id),
        })
        return d

    # ---------- 写操作 ----------
    def adjust_price(self, c, *, club_id: int, kind: str, order_id: int, amount: float,
                     reason: str, participant_service=None, actor: str = "club") -> dict[str, Any]:
        if kind not in ORDER_KINDS:
            raise ValueError("订单类型无效")
        try:
            value = round(float(amount), 2)
        except (TypeError, ValueError):
            raise ValueError("金额格式不正确")
        if value < 0:
            raise ValueError("金额不能为负")
        reason = str(reason or "").strip()
        if len(reason) < 2:
            raise ValueError("请填写改价原因（至少 2 个字）")
        if kind == "activity":
            r = c.execute("SELECT * FROM registrations WHERE id=? AND club_id=?", (order_id, club_id)).fetchone()
            if not r:
                raise LookupError("活动订单不存在")
            before = {"amount": float(r["amount"] or 0)}
            c.execute("UPDATE registrations SET amount=? WHERE id=?", (value, order_id))
            # 现金实收变了，参加人分摊必须同步，否则单人退款按旧分摊算会退超。
            if participant_service is not None:
                pids = [int(x["id"]) for x in c.execute(
                    "SELECT id FROM registration_participants WHERE registration_id=? ORDER BY id", (order_id,)).fetchall()]
                if pids:
                    participant_service.ensure_financial_allocations(c, order_id)
                    splits = participant_service._split_money(value, len(pids))
                    originals = participant_service._split_money(r["original_amount"], len(pids))
                    for pid, cash, original in zip(pids, splits, originals):
                        # 老订单可能只给首个参加人建过分摊；改价时把后来补录的参加人一并补齐，
                        # 否则单人退款拿不到自己的分摊，或全单汇总对不上账面金额。
                        c.execute("""INSERT OR IGNORE INTO participant_financial_allocations
                                     (registration_id,participant_id,original_amount,cash_paid)
                                     VALUES(?,?,?,?)""", (order_id, pid, original, cash))
                        c.execute("""UPDATE participant_financial_allocations
                                     SET original_amount=?,cash_paid=? WHERE participant_id=?""",
                                  (original, cash, pid))
            after = {"amount": value}
        else:
            r = c.execute("SELECT * FROM camp_orders WHERE id=? AND club_id=? AND status!='deleted'", (order_id, club_id)).fetchone()
            if not r:
                raise LookupError("营地订单不存在")
            before = {"payable": float(r["payable"] or 0), "total": float(r["total"] or 0)}
            total = round(value + float(r["deposit_amount"] or 0), 2)
            c.execute("UPDATE camp_orders SET payable=?, total=? WHERE id=?", (value, total, order_id))
            after = {"payable": value, "total": total}
        self.log(c, club_id=club_id, order_kind=kind, order_id=order_id, action="price_adjust",
                 before=before, after=after, reason=reason, actor=actor)
        return {"ok": True, "before": before, "after": after}

    def record_offline_refund(self, c, *, club_id: int, order_id: int, amount: float,
                              reason: str, actor: str = "club") -> dict[str, Any]:
        """营地订单线下退款登记：只留痕，明确不调用支付渠道。"""
        try:
            value = round(float(amount), 2)
        except (TypeError, ValueError):
            raise ValueError("金额格式不正确")
        if value <= 0:
            raise ValueError("退款金额须大于 0")
        reason = str(reason or "").strip()
        if len(reason) < 2:
            raise ValueError("请填写退款登记原因（至少 2 个字）")
        o = c.execute("SELECT id,total,status FROM camp_orders WHERE id=? AND club_id=? AND status!='deleted'",
                      (order_id, club_id)).fetchone()
        if not o:
            raise LookupError("营地订单不存在")
        if o["status"] != "paid":
            raise ValueError("仅已支付营地订单可登记退款")
        if value > float(o["total"] or 0) + 0.01:
            raise ValueError("退款金额不能超过订单总额")
        after = {"refundAmount": value, "channel": "offline", "note": reason}
        self.log(c, club_id=club_id, order_kind="camp", order_id=order_id, action="refund_offline_record",
                 before={"total": float(o["total"] or 0)}, after=after, reason=reason, actor=actor)
        return {"ok": True, "recorded": after, "note": "已登记线下退款；本操作不调用支付渠道，请另行完成实际退款。"}

    def update_camp_guest(self, c, *, club_id: int, order_id: int, name: str,
                          phone: str, reason: str, actor: str = "club") -> dict[str, Any]:
        o = c.execute("SELECT * FROM camp_orders WHERE id=? AND club_id=? AND status!='deleted'", (order_id, club_id)).fetchone()
        if not o:
            raise LookupError("营地订单不存在")
        before = {"guestName": o["guest_name"] or "", "guestPhone": o["guest_phone"] or ""}
        after = {"guestName": str(name or "").strip(), "guestPhone": str(phone or "").strip()}
        if not after["guestName"]:
            raise ValueError("预订人姓名不能为空")
        c.execute("UPDATE camp_orders SET guest_name=?,guest_phone=? WHERE id=?",
                  (after["guestName"], after["guestPhone"], order_id))
        self.log(c, club_id=club_id, order_kind="camp", order_id=order_id, action="guest_update",
                 before=before, after=after, reason=reason, actor=actor)
        return {"ok": True, "before": before, "after": after}
