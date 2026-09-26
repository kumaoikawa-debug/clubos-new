from __future__ import annotations
from dataclasses import dataclass
from typing import Any


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
        # 装备商城会员折扣：不传表示「不改动」（避免误把已配置的折扣清成 NULL）
        gear_raw = payload.get('gearDiscount')
        if gear_raw in (None, ''):
            gear_discount = None
        else:
            gear_discount = float(gear_raw)
            if not 0 < gear_discount <= 1:
                raise ValueError('装备商城会员折扣必须在 (0, 1] 之间，例如 0.9 表示九折')
        if tier_id:
            exists = c.execute('SELECT id FROM club_member_tiers WHERE id=? AND club_id=?', (tier_id, club_id)).fetchone()
            if not exists:
                raise LookupError('会员等级不存在')
            c.execute('''UPDATE club_member_tiers SET name=?,rank=?,min_activity_spend=?,min_activity_count=?,
                         qualification_mode=?,benefits_json=?,gear_discount=COALESCE(?,gear_discount),
                         status=?,updated_at=CURRENT_TIMESTAMP
                         WHERE id=? AND club_id=?''',
                      (name,rank,spend,count,mode,json.dumps(benefits,ensure_ascii=False),gear_discount,status,tier_id,club_id))
            return tier_id
        c.execute('''INSERT INTO club_member_tiers(
            club_id,name,rank,min_activity_spend,min_activity_count,qualification_mode,benefits_json,gear_discount,status)
            VALUES(?,?,?,?,?,?,?,?,?)''',
                  (club_id,name,rank,spend,count,mode,json.dumps(benefits,ensure_ascii=False),
                   1.0 if gear_discount is None else gear_discount,status))
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
        tiers = [dict(r) for r in c.execute(
            "SELECT * FROM club_member_tiers WHERE club_id=? AND status='active' ORDER BY rank DESC,id DESC",
            (club_id,)).fetchall()]
        selected = None
        for tier in tiers:
            if self._qualifies(tier, spend=spend, count=count):
                selected = tier
                break
        if not selected:
            selected = {'id':None,'name':'普通会员'}
        c.execute('''UPDATE club_members SET level=?, current_tier_id=?, lifetime_activity_spend=?, activity_count=?,
                     tier_updated_at=CURRENT_TIMESTAMP WHERE club_id=? AND user_id=?''',
                  (selected['name'],selected.get('id'),spend,count,club_id,user_id))
        return MemberStats(activity_count=count,lifetime_activity_spend=spend,level=selected['name'],tier_id=selected.get('id'))

    def refresh_all(self, c, club_id: int) -> int:
        members = c.execute('SELECT user_id FROM club_members WHERE club_id=?',(club_id,)).fetchall()
        for m in members:
            self.refresh_member(c,club_id=club_id,user_id=int(m['user_id']))
        return len(members)
