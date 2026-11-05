from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Any
from .points_redeem_rate import validate_rate, coerce_rate, FOLLOW

logger = logging.getLogger(__name__)

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

    # 生命周期钩子（2026-10-26）：由 app.py 装配时注入 benefits 引擎的方法，
    # 签名均为 (conn, *, club_id, user_id)。内部由调用方兜 try/except，钩子异常不阻断会员主流程。
    on_member_created = None   # 新会员首次入会 → 发「邀请优惠券」（拉新）
    on_upgrade = None          # 升级到新等级 → 发「升级赠券」

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
        # 活动报名折扣（2026-10-26）：90 = 9 折；0 = 该等级不折扣。是否生效还取决于活动
        # 自己的 member_discount_enabled 开关（活动是前提条件）。
        adp = payload.get('activityDiscountPercent')
        activity_discount = 0.0 if adp in (None, '') else min(99.0, max(1.0, float(adp)))
        # 2026-11-05 等级专属积分现金比（X 积分 = ¥1，0=跟随俱乐部生效值）。
        # ★ 缺省时绝不覆盖：老前端 / 测试只要 PATCH 一次等级（改个名字），
        #   就把运营辛苦配的「顶级 20 积分 = ¥1」清成跟随，是很难查的资损。
        #   所以只有显式带 `pointsRedeemRate` 键才写，编辑时读回原值。
        if 'pointsRedeemRate' in payload:
            points_rate = validate_rate(payload.get('pointsRedeemRate'))
        elif tier_id:
            try:
                old = c.execute('SELECT points_redeem_rate FROM club_member_tiers WHERE id=? AND club_id=?',
                                (tier_id, club_id)).fetchone()
                points_rate = coerce_rate(old[0]) if old else FOLLOW
            except Exception:
                points_rate = FOLLOW
        else:
            points_rate = FOLLOW
        if tier_id:
            exists = c.execute('SELECT id FROM club_member_tiers WHERE id=? AND club_id=?', (tier_id, club_id)).fetchone()
            if not exists:
                raise LookupError('会员等级不存在')
            c.execute('''UPDATE club_member_tiers SET name=?,rank=?,min_activity_spend=?,min_activity_count=?,
                         qualification_mode=?,benefits_json=?,access_mode=?,price=?,valid_months=?,
                         activity_discount_percent=?,points_redeem_rate=?,status=?,updated_at=CURRENT_TIMESTAMP
                         WHERE id=? AND club_id=?''',
                      (name,rank,spend,count,mode,json.dumps(benefits,ensure_ascii=False),
                       access_mode,price,valid_months,activity_discount,points_rate,status,tier_id,club_id))
            return tier_id
        c.execute('''INSERT INTO club_member_tiers(
            club_id,name,rank,min_activity_spend,min_activity_count,qualification_mode,benefits_json,
            access_mode,price,valid_months,activity_discount_percent,points_redeem_rate,status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (club_id,name,rank,spend,count,mode,json.dumps(benefits,ensure_ascii=False),
                   access_mode,price,valid_months,activity_discount,points_rate,status))
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
        existed = c.execute('SELECT current_tier_id FROM club_members WHERE club_id=? AND user_id=?',
                            (club_id, user_id)).fetchone()
        is_new_member = existed is None
        old_tier_id = int(existed['current_tier_id']) if (existed and existed['current_tier_id']) else None
        if is_new_member:
            c.execute('INSERT INTO club_members(club_id,user_id) VALUES(?,?)', (club_id,user_id))
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
        # 生命周期钩子（2026-10-26）：新会员入会 → 邀请券；升级到更高等级 → 升级赠券。
        # 回调异常不阻断会员主流程；防重交给回调内部（tier_gift_log 唯一索引）。
        new_tier_id = selected.get('id')
        try:
            if is_new_member and self.on_member_created is not None:
                self.on_member_created(c, club_id=club_id, user_id=user_id)
            if (self.on_upgrade is not None and new_tier_id
                    and new_tier_id != old_tier_id and self._rank_of(c, new_tier_id) > self._rank_of(c, old_tier_id)):
                self.on_upgrade(c, club_id=club_id, user_id=user_id, tier_id=int(new_tier_id))
        except Exception:
            logger.exception('membership lifecycle hook failed (club=%s user=%s)', club_id, user_id)
        return MemberStats(activity_count=count,lifetime_activity_spend=spend,level=selected['name'],tier_id=selected.get('id'))

    @staticmethod
    def _rank_of(c, tier_id) -> int:
        if not tier_id:
            return -1
        row = c.execute('SELECT rank FROM club_member_tiers WHERE id=?', (int(tier_id),)).fetchone()
        return int(row['rank']) if row else -1

    # ── 拉新邀请（2026-10-26）────────────────────────────────────────────
    def ensure_invite_code(self, c, *, club_id: int, user_id: int) -> str:
        """取会员专属邀请码；没有就生成一个（IV + 6 位 hex），冲突重试。"""
        row = c.execute('SELECT invite_code FROM club_members WHERE club_id=? AND user_id=?',
                        (club_id, user_id)).fetchone()
        if row and row['invite_code']:
            return row['invite_code']
        import secrets
        for _ in range(5):
            code = 'IV' + secrets.token_hex(3).upper()
            clash = c.execute('SELECT 1 FROM club_members WHERE invite_code=?', (code,)).fetchone()
            if clash:
                continue
            c.execute('UPDATE club_members SET invite_code=? WHERE club_id=? AND user_id=?',
                      (code, club_id, user_id))
            return code
        raise RuntimeError('邀请码生成失败，请重试')

    def join_with_invite(self, c, *, club_id: int, user_id: int, invite_code: str) -> dict[str, Any]:
        """新会员填邀请码入会：记 referred_by，返回邀请人信息。已被邀请过 → 幂等返回。"""
        code = str(invite_code or '').strip().upper()
        if not code:
            raise ValueError('请填写邀请码')
        inviter = c.execute('''SELECT club_id,user_id,level FROM club_members
                               WHERE invite_code=? AND club_id=?''', (code, club_id)).fetchone()
        if not inviter:
            raise LookupError('邀请码不存在，请核对后重试')
        if int(inviter['user_id']) == int(user_id):
            raise ValueError('不能填写自己的邀请码')
        self.refresh_member(c, club_id=club_id, user_id=user_id)  # 确保已是会员
        cur = c.execute('SELECT referred_by FROM club_members WHERE club_id=? AND user_id=?',
                        (club_id, user_id)).fetchone()
        if cur and cur['referred_by']:
            inv = c.execute('SELECT level,invite_code FROM club_members WHERE club_id=? AND user_id=?',
                            (club_id, int(cur['referred_by']))).fetchone()
            return {'inviterUserId': int(cur['referred_by']), 'inviterLevel': inv['level'] if inv else None,
                    'alreadyBound': True}
        c.execute('UPDATE club_members SET referred_by=? WHERE club_id=? AND user_id=?',
                  (int(inviter['user_id']), club_id, user_id))
        return {'inviterUserId': int(inviter['user_id']), 'inviterLevel': inviter['level'],
                'alreadyBound': False}

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
