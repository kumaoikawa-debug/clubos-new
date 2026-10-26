from __future__ import annotations
from dataclasses import dataclass
from typing import Any

# 会员开通方式（2026-10-26）：
# CONDITION = 条件开通 —— 达到俱乐部设定条件（消费金额/参加次数）后自动成为会员；
# PURCHASE  = 购买开通 —— 俱乐部自定义会员等级卡价格与有效期，用户付费购买。
ACCESS_MODES = ('CONDITION', 'PURCHASE')


@dataclass
class MemberStats:
    activity_count: int
    lifetime_activity_spend: float
    level: str
    tier_id: int | None


class MembershipEngine:
    """Club-scoped membership tiers.

    ClubOS owns member qualification. Open-source customer-tier examples are references only;
    they must not collapse membership across clubs or mix Club Points with Gear Points.
    """

    def ensure_default_tiers(self, c, club_id: int):
        exists = c.execute('SELECT COUNT(*) FROM club_member_tiers WHERE club_id=?', (club_id,)).fetchone()[0]
        if exists:
            return
        # gear_discount = 装备商城会员折扣率（1.0 无折扣 / 0.95 九五折 / 0.9 九折）
        tiers = [
            (club_id, '普通会员', 0, 0, 0, 'ANY', '["基础会员权益"]', 1.0),
            (club_id, '银卡会员', 10, 500, 2, 'ANY', '["会员活动优先报名","专属积分福利"]', 0.95),
            (club_id, '金卡会员', 20, 2000, 5, 'ANY', '["高阶会员活动","专属福利兑换"]', 0.90),
        ]
        c.executemany('''INSERT INTO club_member_tiers(
            club_id,name,rank,min_activity_spend,min_activity_count,qualification_mode,benefits_json,gear_discount)
            VALUES(?,?,?,?,?,?,?,?)''', tiers)

    def list_tiers(self, c, club_id: int) -> list[dict[str, Any]]:
        self.ensure_default_tiers(c, club_id)
        return [dict(r) for r in c.execute(
            'SELECT * FROM club_member_tiers WHERE club_id=? ORDER BY rank,id', (club_id,)
        ).fetchall()]

    def upsert_tier(self, c, *, club_id: int, payload: dict[str, Any], tier_id: int | None = None) -> int:
        name = str(payload.get('name') or '').strip()
        if not name:
            raise ValueError('会员等级名称不能为空')
        rank = max(0, int(payload.get('rank') or 0))
        spend = max(0.0, float(payload.get('minActivitySpend') or 0))
        count = max(0, int(payload.get('minActivityCount') or 0))
        mode = str(payload.get('qualificationMode') or 'ANY').upper()
        if mode not in {'ANY', 'ALL'}:
            raise ValueError('qualificationMode must be ANY or ALL')
        import json
        benefits = payload.get('benefits') or []
        status = str(payload.get('status') or 'active')
        # 开通方式（2026-10-26）：购买型必须定价；条件型的价格/有效期不生效。
        access_mode = str(payload.get('accessMode') or 'CONDITION').strip().upper()
        if access_mode not in ACCESS_MODES:
            raise ValueError('开通方式只能是条件开通或购买开通')
        price = max(0.0, float(payload.get('price') or 0))
        valid_months = max(1, int(payload.get('validMonths') or 12))
        if access_mode == 'PURCHASE' and price <= 0:
            raise ValueError('购买型会员卡必须设置售价')
        # 装备折扣自 2026-10-23 起不再属于会员等级：等级只定义「怎么升级」，
        # 折扣由总平台按俱乐部订阅的套餐统一控制（ai_credit_plans.gear_discount）。
        # 老客户端传来的 gearDiscount 一律忽略，绝不落库。
        if tier_id:
            exists = c.execute('SELECT id FROM club_member_tiers WHERE id=? AND club_id=?', (tier_id, club_id)).fetchone()
            if not exists:
                raise LookupError('会员等级不存在')
            c.execute('''UPDATE club_member_tiers SET name=?,rank=?,min_activity_spend=?,min_activity_count=?,
                         qualification_mode=?,benefits_json=?,access_mode=?,price=?,valid_months=?,
                         status=?,updated_at=CURRENT_TIMESTAMP
                         WHERE id=? AND club_id=?''',
                      (name,rank,spend,count,mode,json.dumps(benefits,ensure_ascii=False),
                       access_mode,price,valid_months,status,tier_id,club_id))
            return tier_id
        c.execute('''INSERT INTO club_member_tiers(
            club_id,name,rank,min_activity_spend,min_activity_count,qualification_mode,benefits_json,
            access_mode,price,valid_months,status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                  (club_id,name,rank,spend,count,mode,json.dumps(benefits,ensure_ascii=False),
                   access_mode,price,valid_months,status))
        return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])

    def _qualifies(self, tier: dict[str, Any], *, spend: float, count: int) -> bool:
        spend_req = float(tier.get('min_activity_spend') or 0)
        count_req = int(tier.get('min_activity_count') or 0)
        checks = []
        if spend_req > 0:
            checks.append(spend >= spend_req)
        if count_req > 0:
            checks.append(count >= count_req)
        if not checks:
            return True
        if str(tier.get('qualification_mode') or 'ANY').upper() == 'ALL':
            return all(checks)
        return any(checks)

    def refresh_member(self, c, *, club_id: int, user_id: int) -> MemberStats:
        self.ensure_default_tiers(c, club_id)
        c.execute('INSERT OR IGNORE INTO club_members(club_id,user_id) VALUES(?,?)', (club_id,user_id))
        # v0.14: after single-participant refunds, membership spend follows active participant
        # allocations instead of the original order total. Platform-funded Gear Points/benefits
        # still count as spend; club-funded discounts do not.
        regs=[dict(r) for r in c.execute('''SELECT * FROM registrations WHERE club_id=? AND user_id=? AND status='paid' ''',(club_id,user_id)).fetchall()]
        count=0; spend=0.0
        for reg in regs:
            allocs=[dict(a) for a in c.execute('''SELECT a.* FROM participant_financial_allocations a
                JOIN registration_participants p ON p.id=a.participant_id
                WHERE a.registration_id=? AND p.status='active' ''',(reg['id'],)).fetchall()]
            if allocs:
                count += 1
                spend += sum(float(a.get('cash_paid') or 0)+float(a.get('platform_point_subsidy') or 0)+float(a.get('platform_benefit_subsidy') or 0) for a in allocs)
            else:
                # Legacy fallback for registrations without participant allocations.
                active=c.execute("SELECT COUNT(*) FROM registration_participants WHERE registration_id=? AND status='active'",(reg['id'],)).fetchone()[0]
                if active:
                    count += 1
                    spend += float(reg.get('amount') or 0)+float(reg.get('platform_point_subsidy') or 0)+float(reg.get('platform_benefit_subsidy') or 0)
        # 条件开通只看「条件型」等级（2026-10-26）：购买型等级不参与自动升级，
        # 否则买了卡的人会被消费条件重新降档。
        tiers = [dict(r) for r in c.execute(
            "SELECT * FROM club_member_tiers WHERE club_id=? AND status='active' AND access_mode='CONDITION' ORDER BY rank DESC,id DESC",
            (club_id,)).fetchall()]
        selected = None
        for tier in tiers:
            if self._qualifies(tier, spend=spend, count=count):
                selected = tier
                break
        # 购买开通：有效期内购买的会员卡生效 —— 取「条件达标档」与「购买档」中更高的一档。
        purchase = self.active_purchase(c, club_id=club_id, user_id=user_id)
        if purchase:
            ptier = c.execute('SELECT * FROM club_member_tiers WHERE id=?',(int(purchase['tier_id']),)).fetchone()
            if ptier and str(dict(ptier).get('status') or 'active')=='active':
                ptier=dict(ptier)
                if selected is None or int(ptier.get('rank') or 0) >= int(selected.get('rank') or 0):
                    selected = ptier
        if not selected:
            selected = {'id':None,'name':'普通会员'}
        c.execute('''UPDATE club_members SET level=?, current_tier_id=?, lifetime_activity_spend=?, activity_count=?,
                     tier_updated_at=CURRENT_TIMESTAMP WHERE club_id=? AND user_id=?''',
                  (selected['name'],selected.get('id'),spend,count,club_id,user_id))
        return MemberStats(activity_count=count,lifetime_activity_spend=spend,level=selected['name'],tier_id=selected.get('id'))

    # ── 购买会员（2026-10-26）─────────────────────────────────────────────
    @staticmethod
    def _now() -> str:
        import datetime
        return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def active_purchase(self, c, *, club_id: int, user_id: int) -> dict[str, Any] | None:
        """用户当前有效的购买会员（可能多张卡，取最晚到期的一张）。"""
        row = c.execute('''SELECT * FROM member_orders
                           WHERE club_id=? AND user_id=? AND status='paid' AND valid_to>=?
                           ORDER BY valid_to DESC, id DESC LIMIT 1''',(club_id,user_id,self._now())).fetchone()
        return dict(row) if row else None

    def purchasable_tiers(self, c, club_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in c.execute(
            "SELECT * FROM club_member_tiers WHERE club_id=? AND status='active' AND access_mode='PURCHASE' ORDER BY rank,id",
            (club_id,)).fetchall()]

    def purchase_tier(self, c, *, club_id: int, user_id: int, tier_id: int, note: str | None = None) -> dict[str, Any]:
        """购买会员卡：校验可售 → 写购买订单 → 重算会员等级。"""
        tier = c.execute('SELECT * FROM club_member_tiers WHERE id=? AND club_id=?',(int(tier_id),club_id)).fetchone()
        if not tier:
            raise LookupError('会员等级不存在')
        tier = dict(tier)
        if str(tier.get('status') or 'active') != 'active' or str(tier.get('access_mode') or 'CONDITION') != 'PURCHASE':
            raise ValueError('该等级不支持购买开通')
        price = max(0.0, float(tier.get('price') or 0))
        months = max(1, int(tier.get('valid_months') or 12))
        import datetime
        start = datetime.datetime.now()
        end = start
        # 月份加法用日历口径：1 月 31 日买 1 个月 = 2 月 28/29 日到期，不做 31→31 的幻数日。
        month = end.month - 1 + months
        year = end.year + month // 12
        month = month % 12 + 1
        day = min(end.day, [31,29 if year%4==0 and (year%100!=0 or year%400==0) else 28,
                            31,30,31,30,31,31,30,31,30,31][month-1])
        end = datetime.datetime(year, month, day, end.hour, end.minute, end.second)
        c.execute('''INSERT INTO member_orders(club_id,user_id,tier_id,tier_name,amount,months,status,valid_from,valid_to,note)
                     VALUES(?,?,?,?,?,?,?,?,?,?)''',
                  (club_id,user_id,int(tier_id),tier['name'],price,months,'paid',
                   start.strftime('%Y-%m-%d %H:%M:%S'),end.strftime('%Y-%m-%d %H:%M:%S'),note))
        oid = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        self.refresh_member(c, club_id=club_id, user_id=user_id)
        order = dict(c.execute('SELECT * FROM member_orders WHERE id=?',(oid,)).fetchone())
        return {'orderId':oid,'order':order,'tier':tier}

    def refresh_all(self, c, club_id: int) -> int:
        members = c.execute('SELECT user_id FROM club_members WHERE club_id=?',(club_id,)).fetchall()
        for m in members:
            self.refresh_member(c,club_id=club_id,user_id=int(m['user_id']))
        return len(members)
