from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any


class AICreditEngine:
    """Commercial AI Credits ledger for ClubOS Platform.

    Credits are billing units only. They never choose the model, truncate context,
    reduce image count, or otherwise lower generation quality.
    """

    DEFAULT_PLANS = [
        # 末位是装备商城会员折扣（gear_discount）：折扣是套餐资产、由总平台控制，
        # 俱乐部会员等级不再参与（2026-10-23）。Starter 无折扣 / Pro 9.5 折 / Enterprise 9 折。
        ("starter", "Starter", 299.0, 3000, "适合刚开始使用 ClubOS AI 的俱乐部", 10, 1.0),
        ("pro", "Pro", 699.0, 10000, "适合持续运营活动与内容的俱乐部", 20, 0.95),
        ("enterprise", "Enterprise", 0.0, 30000, "组织级额度；价格由平台另行约定", 30, 0.90),
    ]
    DEFAULT_TOPUPS = [
        ("T1000", "1,000 Credits", 99.0, 1000, 10),
        ("T5000", "5,000 Credits", 399.0, 5000, 20),
        ("T12000", "12,000 Credits", 799.0, 12000, 30),
    ]

    @staticmethod
    def _month_key(d: date | None = None) -> str:
        d = d or date.today()
        return f"{d.year:04d}-{d.month:02d}"

    @staticmethod
    def _period_bounds(period_key: str) -> tuple[str, str]:
        y, m = [int(x) for x in period_key.split("-")]
        start = date(y, m, 1)
        if m == 12:
            nxt = date(y + 1, 1, 1)
        else:
            nxt = date(y, m + 1, 1)
        return start.isoformat(), nxt.isoformat()

    def seed_defaults(self, c) -> None:
        for code, name, fee, credits, description, sort_order, gear_discount in self.DEFAULT_PLANS:
            c.execute(
                """INSERT OR IGNORE INTO ai_credit_plans(code,name,monthly_fee,monthly_credits,gear_discount,description,status,sort_order)
                   VALUES(?,?,?,?,?,?,'active',?)""",
                (code, name, fee, credits, gear_discount, description, sort_order),
            )
        for code, name, amount, credits, sort_order in self.DEFAULT_TOPUPS:
            c.execute(
                """INSERT OR IGNORE INTO ai_credit_topup_packages(code,name,amount,credits,status,sort_order)
                   VALUES(?,?,?,?,'active',?)""",
                (code, name, amount, credits, sort_order),
            )

    def ensure_account(self, c, club_id: int) -> dict[str, Any]:
        r = c.execute("SELECT * FROM ai_credit_accounts WHERE club_id=?", (club_id,)).fetchone()
        if not r:
            c.execute("INSERT INTO ai_credit_accounts(club_id,balance,monthly_quota) VALUES(?,?,?)", (club_id, 0, 0))
            r = c.execute("SELECT * FROM ai_credit_accounts WHERE club_id=?", (club_id,)).fetchone()
        return dict(r)

    def _unresolved_debt(self, c, club_id: int) -> int:
        r = c.execute(
            "SELECT COALESCE(SUM(amount-resolved_amount),0) FROM ai_credit_adjustment_debt WHERE club_id=? AND resolved=0",
            (club_id,),
        ).fetchone()
        return max(0, int(r[0] or 0))

    def _recover_debt(self, c, *, club_id: int, available_credit: int, source_type: str, source_id: str) -> int:
        remaining = max(0, int(available_credit))
        recovered = 0
        debts = c.execute(
            "SELECT * FROM ai_credit_adjustment_debt WHERE club_id=? AND resolved=0 ORDER BY id",
            (club_id,),
        ).fetchall()
        for d0 in debts:
            if remaining <= 0:
                break
            d = dict(d0)
            outstanding = max(0, int(d.get("amount") or 0) - int(d.get("resolved_amount") or 0))
            if outstanding <= 0:
                c.execute(
                    "UPDATE ai_credit_adjustment_debt SET resolved=1,resolved_at=COALESCE(resolved_at,CURRENT_TIMESTAMP) WHERE id=?",
                    (d["id"],),
                )
                continue
            take = min(remaining, outstanding)
            new_resolved = int(d.get("resolved_amount") or 0) + take
            done = new_resolved >= int(d.get("amount") or 0)
            c.execute(
                "UPDATE ai_credit_adjustment_debt SET resolved_amount=?,resolved=?,resolved_at=CASE WHEN ?=1 THEN CURRENT_TIMESTAMP ELSE resolved_at END WHERE id=?",
                (new_resolved, 1 if done else 0, 1 if done else 0, d["id"]),
            )
            c.execute(
                """INSERT INTO ai_credit_ledger(club_id,type,amount,source_type,source_id,note)
                   VALUES(?,?,?,?,?,?)""",
                (club_id, "debt_recovery", -take, source_type, source_id, f"自动偿还 AI Credits 欠账 #{d['id']}"),
            )
            remaining -= take
            recovered += take
        return recovered

    def grant(
        self,
        c,
        *,
        club_id: int,
        amount: int,
        ledger_type: str,
        source_type: str,
        source_id: str,
        note: str,
        idempotent: bool = True,
    ) -> dict[str, Any]:
        amount = int(amount or 0)
        if amount <= 0:
            return {"granted": 0, "debtRecovered": 0, "netToBalance": 0}
        self.ensure_account(c, club_id)
        if idempotent:
            r = c.execute(
                "SELECT id,amount FROM ai_credit_ledger WHERE club_id=? AND type=? AND source_type=? AND source_id=? LIMIT 1",
                (club_id, ledger_type, source_type, source_id),
            ).fetchone()
            if r:
                return {"granted": int(r[1]), "debtRecovered": 0, "netToBalance": 0, "idempotent": True}
        c.execute(
            """INSERT INTO ai_credit_ledger(club_id,type,amount,source_type,source_id,note)
               VALUES(?,?,?,?,?,?)""",
            (club_id, ledger_type, amount, source_type, source_id, note),
        )
        recovered = self._recover_debt(
            c, club_id=club_id, available_credit=amount, source_type=source_type, source_id=source_id
        )
        net = max(0, amount - recovered)
        if net:
            c.execute("UPDATE ai_credit_accounts SET balance=balance+? WHERE club_id=?", (net, club_id))
        return {"granted": amount, "debtRecovered": recovered, "netToBalance": net}

    def debit_or_debt(
        self,
        c,
        *,
        club_id: int,
        amount: int,
        ledger_type: str,
        source_type: str,
        source_id: str,
        note: str,
    ) -> dict[str, Any]:
        amount = max(0, int(amount or 0))
        self.ensure_account(c, club_id)
        bal = int(c.execute("SELECT balance FROM ai_credit_accounts WHERE club_id=?", (club_id,)).fetchone()[0] or 0)
        deduct = min(bal, amount)
        debt = amount - deduct
        if deduct:
            c.execute("UPDATE ai_credit_accounts SET balance=balance-? WHERE club_id=?", (deduct, club_id))
            c.execute(
                "INSERT INTO ai_credit_ledger(club_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)",
                (club_id, ledger_type, -deduct, source_type, source_id, note),
            )
        if debt:
            c.execute(
                "INSERT INTO ai_credit_adjustment_debt(club_id,amount,resolved_amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)",
                (club_id, debt, 0, source_type, source_id, note + "；余额不足形成欠账"),
            )
        return {"deducted": deduct, "debtCreated": debt}

    def adjust(self, c, *, club_id: int, amount: int, note: str, adjustment_id: str | None = None) -> dict[str, Any]:
        aid = adjustment_id or f"adj_{uuid.uuid4().hex}"
        amount = int(amount or 0)
        if amount > 0:
            result = self.grant(
                c,
                club_id=club_id,
                amount=amount,
                ledger_type="adjustment",
                source_type="platform_adjustment",
                source_id=aid,
                note=note,
            )
        elif amount < 0:
            result = self.debit_or_debt(
                c,
                club_id=club_id,
                amount=-amount,
                ledger_type="adjustment",
                source_type="platform_adjustment",
                source_id=aid,
                note=note,
            )
        else:
            result = {"adjusted": 0}
        result["adjustmentId"] = aid
        return result

    def plans(self, c, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM ai_credit_plans"
        if not include_inactive:
            q += " WHERE status='active'"
        q += " ORDER BY sort_order,code"
        return [dict(x) for x in c.execute(q).fetchall()]

    def topup_packages(self, c, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM ai_credit_topup_packages"
        if not include_inactive:
            q += " WHERE status='active'"
        q += " ORDER BY sort_order,code"
        return [dict(x) for x in c.execute(q).fetchall()]

    def upsert_plan(self, c, payload: dict[str, Any]) -> dict[str, Any]:
        code = str(payload.get("code") or "").strip().lower()
        if not code:
            raise ValueError("套餐 code 必填")
        name = str(payload.get("name") or code).strip()
        fee = max(0.0, float(payload.get("monthlyFee") or 0))
        credits = max(0, int(payload.get("monthlyCredits") or 0))
        status = str(payload.get("status") or "active")
        if status not in {"active", "inactive"}:
            raise ValueError("套餐状态错误")
        # 装备商城折扣（平台资产）：不传表示「不改动」，避免误把已配好的折扣清回 1.0。
        gear_raw = payload.get("gearDiscount")
        gear_discount = None
        if gear_raw not in (None, ""):
            gear_discount = float(gear_raw)
            if not 0 < gear_discount <= 1:
                raise ValueError("装备折扣必须在 (0,1] 之间，1 表示无折扣，0.9 表示九折")
        existing = c.execute("SELECT code FROM ai_credit_plans WHERE code=?", (code,)).fetchone()
        if existing:
            # 更新：gear_discount 用 COALESCE 保留旧值（None = 不改动）。
            c.execute(
                """UPDATE ai_credit_plans SET name=?,monthly_fee=?,monthly_credits=?,
                     gear_discount=COALESCE(?,gear_discount),description=?,status=?,sort_order=?,
                     updated_at=CURRENT_TIMESTAMP WHERE code=?""",
                (name, fee, credits, gear_discount, str(payload.get("description") or ""), status,
                 int(payload.get("sortOrder") or 100), code),
            )
        else:
            # 新建：不传折扣按 1.0（无折扣）建档 —— 新套餐默认不打折，绝不自己造折扣。
            c.execute(
                """INSERT INTO ai_credit_plans(code,name,monthly_fee,monthly_credits,gear_discount,description,status,sort_order,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                (code, name, fee, credits, 1.0 if gear_discount is None else gear_discount,
                 str(payload.get("description") or ""), status, int(payload.get("sortOrder") or 100)),
            )
        return dict(c.execute("SELECT * FROM ai_credit_plans WHERE code=?", (code,)).fetchone())

    def delete_plan(self, c, code: str) -> dict[str, Any]:
        """删除套餐。

        安全约束（2026-10-23 用户要求「套餐可以增删」时补上）：
        · 有俱乐部订阅中的套餐 **绝不硬删** —— 订阅表 plan_code 指向它，硬删会
          让「俱乐部 Credits 账户」页的套餐列变成空指针（LEFT JOIN 兜底成 NULL，
          顾客端也可能取不到套餐名）。这种情况直接拒绝，让平台先改绑再删。
        · 没有订阅的套餐可以真删；已下架（status='inactive'）的套餐也允许删
          （下架本身就是「不再售卖」的意思，留着只是垃圾数据）。
        """
        code = str(code or "").strip().lower()
        if not code:
            raise ValueError("套餐 code 必填")
        row_ = c.execute("SELECT * FROM ai_credit_plans WHERE code=?", (code,)).fetchone()
        if not row_:
            raise ValueError("套餐不存在")
        used = c.execute(
            """SELECT COUNT(*) FROM club_ai_subscriptions
               WHERE plan_code=? AND (status='active' OR status='paused')""",
            (code,),
        ).fetchone()[0]
        if used:
            raise ValueError(
                f"还有 {used} 个俱乐部在用这个套餐，不能直接删除；"
                "请先把这些俱乐部改绑到其它套餐，或把本套餐下架（inactive）。"
            )
        c.execute("DELETE FROM ai_credit_plans WHERE code=?", (code,))
        return {"code": code, "deleted": True}

    def plan_gear_rate(self, c, club_id: int) -> tuple[float, str, str]:
        """该俱乐部当前生效套餐的装备折扣。返回 (折扣率, 套餐名, 套餐code)。

        装备折扣是平台资产（2026-10-23 起）：由俱乐部订阅的套餐决定，会员等级不再参与。
        优先取 active 订阅的 plan_code，退回 clubs.plan 兜底；查不到或值非法一律
        1.0（不打折）—— 宁可不打折，也绝不自己造一个折扣出来。
        """
        try:
            r = c.execute(
                """SELECT p.gear_discount AS gd,p.name AS pn,p.code AS pc FROM clubs cl
                   LEFT JOIN club_ai_subscriptions s ON s.club_id=cl.id AND s.status='active'
                   LEFT JOIN ai_credit_plans p ON p.code=COALESCE(s.plan_code,cl.plan)
                   WHERE cl.id=?""",(club_id,)).fetchone()
        except Exception:
            return 1.0, "", ""
        rate, name, code = 1.0, "", ""
        if r is not None:
            raw = r["gd"] if "gd" in r.keys() else None
            if raw is not None:
                try:
                    v = float(raw)
                    if 0 < v <= 1: rate = v
                except (TypeError, ValueError): pass
            name = str(r["pn"] or ""); code = str(r["pc"] or "")
        return rate, name, code

    def upsert_topup_package(self, c, payload: dict[str, Any]) -> dict[str, Any]:
        code = str(payload.get("code") or "").strip().upper()
        if not code:
            raise ValueError("充值包 code 必填")
        name = str(payload.get("name") or code).strip()
        amount = max(0.0, float(payload.get("amount") or 0))
        credits = max(1, int(payload.get("credits") or 0))
        status = str(payload.get("status") or "active")
        if status not in {"active", "inactive"}:
            raise ValueError("充值包状态错误")
        c.execute(
            """INSERT INTO ai_credit_topup_packages(code,name,amount,credits,status,sort_order,updated_at)
               VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(code) DO UPDATE SET name=excluded.name,amount=excluded.amount,credits=excluded.credits,
                 status=excluded.status,sort_order=excluded.sort_order,updated_at=CURRENT_TIMESTAMP""",
            (code, name, amount, credits, status, int(payload.get("sortOrder") or 100)),
        )
        return dict(c.execute("SELECT * FROM ai_credit_topup_packages WHERE code=?", (code,)).fetchone())

    def subscription(self, c, club_id: int) -> dict[str, Any] | None:
        r = c.execute(
            """SELECT s.*,p.name plan_name,p.description plan_description
               FROM club_ai_subscriptions s LEFT JOIN ai_credit_plans p ON p.code=s.plan_code WHERE s.club_id=?""",
            (club_id,),
        ).fetchone()
        return dict(r) if r else None

    def assign_plan(self, c, *, club_id: int, plan_code: str, auto_renew: bool = True) -> dict[str, Any]:
        plan = c.execute("SELECT * FROM ai_credit_plans WHERE code=? AND status='active'", (plan_code,)).fetchone()
        if not plan:
            raise ValueError("AI Credits 套餐不存在或已停用")
        period = self._month_key()
        start, end = self._period_bounds(period)
        c.execute(
            """INSERT INTO club_ai_subscriptions(club_id,plan_code,status,current_period_start,current_period_end,
                 auto_renew,monthly_fee_snapshot,monthly_credits_snapshot,updated_at)
               VALUES(?,?, 'active',?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(club_id) DO UPDATE SET plan_code=excluded.plan_code,status='active',
                 current_period_start=excluded.current_period_start,current_period_end=excluded.current_period_end,
                 auto_renew=excluded.auto_renew,monthly_fee_snapshot=excluded.monthly_fee_snapshot,
                 monthly_credits_snapshot=excluded.monthly_credits_snapshot,updated_at=CURRENT_TIMESTAMP""",
            (club_id, plan_code, start, end, 1 if auto_renew else 0, float(plan["monthly_fee"]), int(plan["monthly_credits"])),
        )
        c.execute("UPDATE clubs SET plan=? WHERE id=?", (plan_code, club_id))
        # 账户行可能还不存在（自助订阅的俱乐部此前从没碰过 AI Credits）：
        # 只 UPDATE 会对不存在的行静默 no-op，月度额度就丢了——必须 upsert。
        c.execute(
            """INSERT INTO ai_credit_accounts(club_id,balance,monthly_quota) VALUES(?,0,?)
               ON CONFLICT(club_id) DO UPDATE SET monthly_quota=excluded.monthly_quota""",
            (club_id, int(plan["monthly_credits"])),
        )
        order = self.create_subscription_order(c, club_id=club_id, period_key=period)
        return {"subscription": self.subscription(c, club_id), "billingOrder": order}

    def create_subscription_order(self, c, *, club_id: int, period_key: str | None = None) -> dict[str, Any]:
        period = period_key or self._month_key()
        sub = self.subscription(c, club_id)
        if not sub or sub.get("status") != "active":
            raise ValueError("俱乐部尚未开通 AI Credits 套餐")
        # 唯一索引 idx_ai_credit_subscription_period 保证每月只有一张订阅账单——
        # 这是刻意的账务不变量，不在它之外硬塞第二张。自助订阅引入后「同月换套餐」
        # 成为俱乐部的随手动作，语义按账单状态分两支：
        #   · 同套餐 → 幂等返回原账单；
        #   · 未付款 + 换套餐 → 账单**原位改写**为当前订阅套餐（金额/额度/备注），
        #     避免同月挂着一张口径对不上的待付款账单；
        #   · 已付款 + 换套餐 → 明确拒绝（差额要走平台），绝不静默错账。
        existing = c.execute(
            "SELECT * FROM ai_credit_orders WHERE club_id=? AND order_type='subscription' AND period_key=? LIMIT 1",
            (club_id, period),
        ).fetchone()
        if existing:
            if existing["plan_code"] == sub["plan_code"]:
                return dict(existing)
            if existing["status"] == "paid":
                raise ValueError(
                    f"{period} 的套餐账单已付款；本月内换套餐的差额请联系总平台处理，下月账单将按新套餐计费"
                )
            c.execute(
                """UPDATE ai_credit_orders SET plan_code=?,credits=?,amount=?,note=? WHERE id=?""",
                (sub["plan_code"], int(sub["monthly_credits_snapshot"]),
                 float(sub["monthly_fee_snapshot"]), f"{period} AI Credits 套餐 {sub['plan_code']}", existing["id"]),
            )
            return dict(c.execute("SELECT * FROM ai_credit_orders WHERE id=?", (existing["id"],)).fetchone())
        oid = f"aio_{uuid.uuid4().hex}"
        c.execute(
            """INSERT INTO ai_credit_orders(id,club_id,order_type,plan_code,credits,amount,period_key,status,note)
               VALUES(?,?,?,?,?,?,?,'pending',?)""",
            (oid, club_id, "subscription", sub["plan_code"], int(sub["monthly_credits_snapshot"]),
             float(sub["monthly_fee_snapshot"]), period, f"{period} AI Credits 套餐 {sub['plan_code']}"),
        )
        return dict(c.execute("SELECT * FROM ai_credit_orders WHERE id=?", (oid,)).fetchone())

    def create_topup_order(self, c, *, club_id: int, package_code: str) -> dict[str, Any]:
        pkg = c.execute(
            "SELECT * FROM ai_credit_topup_packages WHERE code=? AND status='active'", (package_code,)
        ).fetchone()
        if not pkg:
            raise ValueError("充值包不存在或已停用")
        oid = f"aio_{uuid.uuid4().hex}"
        c.execute(
            """INSERT INTO ai_credit_orders(id,club_id,order_type,package_code,credits,amount,status,note)
               VALUES(?,?,?,?,?,?,'pending',?)""",
            (oid, club_id, "topup", pkg["code"], int(pkg["credits"]), float(pkg["amount"]), f"购买 {pkg['name']}"),
        )
        return dict(c.execute("SELECT * FROM ai_credit_orders WHERE id=?", (oid,)).fetchone())

    def confirm_order_paid(self, c, *, order_id: str, payment_ref: str) -> dict[str, Any]:
        o0 = c.execute("SELECT * FROM ai_credit_orders WHERE id=?", (order_id,)).fetchone()
        if not o0:
            raise LookupError("AI Credits 账单不存在")
        o = dict(o0)
        if o["status"] == "paid":
            return {"order": o, "idempotent": True}
        if o["status"] != "pending":
            raise ValueError("当前账单状态不可确认付款")
        if not payment_ref.strip():
            raise ValueError("paymentRef 必填")
        same = c.execute("SELECT id FROM ai_credit_orders WHERE payment_ref=?", (payment_ref.strip(),)).fetchone()
        if same and str(same[0]) != order_id:
            raise ValueError("paymentRef 已被其他 AI Credits 账单使用")
        ledger_type = "subscription" if o["order_type"] == "subscription" else "topup"
        grant = self.grant(
            c,
            club_id=int(o["club_id"]),
            amount=int(o["credits"]),
            ledger_type=ledger_type,
            source_type="ai_credit_order",
            source_id=order_id,
            note=o.get("note") or f"AI Credits {o['order_type']}",
        )
        c.execute(
            "UPDATE ai_credit_orders SET status='paid',payment_ref=?,paid_at=CURRENT_TIMESTAMP WHERE id=?",
            (payment_ref.strip(), order_id),
        )
        if o["order_type"] == "subscription" and o.get("period_key"):
            start, end = self._period_bounds(o["period_key"])
            c.execute(
                "UPDATE club_ai_subscriptions SET current_period_start=?,current_period_end=?,updated_at=CURRENT_TIMESTAMP WHERE club_id=?",
                (start, end, int(o["club_id"])),
            )
        return {"order": dict(c.execute("SELECT * FROM ai_credit_orders WHERE id=?", (order_id,)).fetchone()), "grant": grant}

    def monthly_roll(self, c, *, period_key: str | None = None) -> list[dict[str, Any]]:
        period = period_key or self._month_key()
        out = []
        clubs = c.execute(
            "SELECT club_id FROM club_ai_subscriptions WHERE status='active' AND auto_renew=1 ORDER BY club_id"
        ).fetchall()
        for x in clubs:
            out.append(self.create_subscription_order(c, club_id=int(x[0]), period_key=period))
        return out

    def orders(self, c, *, club_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if club_id is None:
            q = """SELECT o.*,cl.name club_name FROM ai_credit_orders o JOIN clubs cl ON cl.id=o.club_id
                   ORDER BY o.created_at DESC LIMIT ?"""
            return [dict(x) for x in c.execute(q, (max(1, int(limit)),)).fetchall()]
        return [dict(x) for x in c.execute(
            "SELECT * FROM ai_credit_orders WHERE club_id=? ORDER BY created_at DESC LIMIT ?",
            (club_id, max(1, int(limit))),
        ).fetchall()]

    def task_pricing(self, c) -> dict[str, int]:
        mapping = {
            "detail": "ai_cost_detail", "wechat": "ai_cost_wechat", "xhs": "ai_cost_xhs",
            "poster": "ai_cost_poster", "recap": "ai_cost_recap",
        }
        out = {}
        for k, setting_key in mapping.items():
            r = c.execute("SELECT value FROM platform_settings WHERE key=?", (setting_key,)).fetchone()
            out[k] = int(r[0]) if r else 0
        return out

    def update_task_pricing(self, c, payload: dict[str, Any]) -> dict[str, int]:
        mapping = {
            "detail": "ai_cost_detail", "wechat": "ai_cost_wechat", "xhs": "ai_cost_xhs",
            "poster": "ai_cost_poster", "recap": "ai_cost_recap",
        }
        for k, setting_key in mapping.items():
            if k not in payload:
                continue
            v = int(payload[k])
            if v < 0 or v > 100000:
                raise ValueError(f"{k} Credits 定价不合法")
            c.execute(
                "INSERT INTO platform_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (setting_key, str(v)),
            )
        return self.task_pricing(c)

    def statement(self, c, *, club_id: int, period_key: str | None = None) -> dict[str, Any]:
        period = period_key or self._month_key()
        start, end = self._period_bounds(period)
        account = self.ensure_account(c, club_id)
        sub = self.subscription(c, club_id)
        debt = self._unresolved_debt(c, club_id)
        ledger = [dict(x) for x in c.execute(
            "SELECT * FROM ai_credit_ledger WHERE club_id=? AND created_at>=? AND created_at<? ORDER BY id DESC",
            (club_id, start, end),
        ).fetchall()]
        usage = [dict(x) for x in c.execute(
            "SELECT * FROM ai_usage_records WHERE club_id=? AND created_at>=? AND created_at<? ORDER BY id DESC",
            (club_id, start, end),
        ).fetchall()]
        orders = [dict(x) for x in c.execute(
            "SELECT * FROM ai_credit_orders WHERE club_id=? AND created_at>=? AND created_at<? ORDER BY created_at DESC",
            (club_id, start, end),
        ).fetchall()]
        issued = sum(max(0, int(x.get("amount") or 0)) for x in ledger)
        consumed = abs(sum(min(0, int(x.get("amount") or 0)) for x in ledger if x.get("type") == "consume"))
        recovered = abs(sum(min(0, int(x.get("amount") or 0)) for x in ledger if x.get("type") == "debt_recovery"))
        rewards = sum(max(0, int(x.get("amount") or 0)) for x in ledger if x.get("type") == "mall_reward")
        provider_cost_usd = round(sum(float(x.get("provider_cost") or 0) for x in usage if x.get("status") == "success"), 8)
        fx = c.execute("SELECT value FROM platform_settings WHERE key='ai_usd_cny_rate'").fetchone()
        usd_cny = float(fx[0]) if fx else 7.2
        revenue_cny = round(sum(float(x.get("amount") or 0) for x in orders if x.get("status") == "paid"), 2)
        return {
            "period": period,
            "account": account,
            "subscription": sub,
            "unresolvedDebt": debt,
            "creditsIssued": issued,
            "creditsConsumed": consumed,
            "creditsDebtRecovered": recovered,
            "mallRewardCredits": rewards,
            "successfulCalls": sum(1 for x in usage if x.get("status") == "success"),
            "failedCalls": sum(1 for x in usage if x.get("status") == "failed"),
            "inputTokens": sum(int(x.get("input_tokens") or 0) for x in usage),
            "outputTokens": sum(int(x.get("output_tokens") or 0) for x in usage),
            "providerCostUsd": provider_cost_usd,
            "providerCostCny": round(provider_cost_usd * usd_cny, 4),
            "usdCnyAccountingRate": usd_cny,
            "cashRevenueCny": revenue_cny,
            "ledger": ledger,
            "usage": usage[:100],
            "orders": orders,
        }

    def platform_summary(self, c, *, period_key: str | None = None) -> dict[str, Any]:
        period = period_key or self._month_key()
        start, end = self._period_bounds(period)
        revenue = float(c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM ai_credit_orders WHERE status='paid' AND paid_at>=? AND paid_at<?",
            (start, end),
        ).fetchone()[0] or 0)
        provider_usd = float(c.execute(
            "SELECT COALESCE(SUM(provider_cost),0) FROM ai_usage_records WHERE status='success' AND created_at>=? AND created_at<?",
            (start, end),
        ).fetchone()[0] or 0)
        fx = c.execute("SELECT value FROM platform_settings WHERE key='ai_usd_cny_rate'").fetchone()
        usd_cny = float(fx[0]) if fx else 7.2
        consumed = abs(int(c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM ai_credit_ledger WHERE type='consume' AND created_at>=? AND created_at<?",
            (start, end),
        ).fetchone()[0] or 0))
        grants = int(c.execute(
            "SELECT COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0) FROM ai_credit_ledger WHERE created_at>=? AND created_at<?",
            (start, end),
        ).fetchone()[0] or 0)
        debt = int(c.execute(
            "SELECT COALESCE(SUM(amount-resolved_amount),0) FROM ai_credit_adjustment_debt WHERE resolved=0",
        ).fetchone()[0] or 0)
        pending = int(c.execute("SELECT COUNT(*) FROM ai_credit_orders WHERE status='pending'").fetchone()[0])
        return {
            "period": period,
            "cashRevenueCny": round(revenue, 2),
            "providerCostUsd": round(provider_usd, 8),
            "providerCostCny": round(provider_usd * usd_cny, 4),
            "accountingGrossProfitCny": round(revenue - provider_usd * usd_cny, 4),
            "creditsGranted": grants,
            "creditsConsumed": consumed,
            "outstandingDebt": debt,
            "pendingBillingOrders": pending,
            "activeSubscriptions": int(c.execute("SELECT COUNT(*) FROM club_ai_subscriptions WHERE status='active'").fetchone()[0]),
        }
