from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

@dataclass
class PointQuote:
    original_amount: float
    club_points_requested: int
    club_points_used: int
    club_point_discount: float
    gear_points_requested: int
    gear_points_used: int
    platform_point_subsidy: float
    payable: float
    club_funding_cost: float
    platform_funding_cost: float

    def as_dict(self):
        d = asdict(self)
        return {
            'original': round(d['original_amount'], 2),
            'clubPointsRequested': d['club_points_requested'],
            'clubPointsUsed': d['club_points_used'],
            'clubPointDiscount': round(d['club_point_discount'], 2),
            'gearPointsRequested': d['gear_points_requested'],
            'gearPointsUsed': d['gear_points_used'],
            'platformPointSubsidy': round(d['platform_point_subsidy'], 2),
            'payable': round(d['payable'], 2),
            'funding': {
                'club': round(d['club_funding_cost'], 2),
                'platform': round(d['platform_funding_cost'], 2),
            }
        }

class ClubOSPointsEngine:
    """ClubOS domain rules around points.

    This is deliberately separate from any open-source loyalty implementation.
    Open source provides persistence/ledger primitives; these rules remain ClubOS-owned.
    """

    def __init__(self, setting_getter):
        self.setting = setting_getter

    def quote(self, *, amount: float, club_points_balance: int, gear_points_balance: int,
              requested_club_points: int = 0, requested_gear_points: int = 0,
              allow_club_points: bool = True, club_points_max_discount_percent: float = 100.0,
              allow_gear_points: bool = True, gear_points_max_discount_amount: float | None = None) -> PointQuote:
        """Quote cash payable while respecting ClubOS activity-specific point rules.

        Balances alone never grant permission to redeem. The caller supplies the effective
        Activity Points Policy (including the platform Gear Points gate).
        """
        amount = max(0.0, float(amount))
        cp_req = max(0, int(requested_club_points or 0))
        gp_req = max(0, int(requested_gear_points or 0))

        club_rate = max(1, int(self.setting('club_points_redeem_rate', 100)))
        gear_rate = max(1, int(self.setting('gear_points_redeem_rate', 100)))

        if allow_club_points:
            percent = max(0.0, min(100.0, float(club_points_max_discount_percent or 0)))
            cp_cap_cash = amount * percent / 100.0
            cp_cap_points = max(0, int(cp_cap_cash * club_rate + 1e-9))
            cp = min(cp_req, max(0, int(club_points_balance or 0)), cp_cap_points)
        else:
            cp = 0
        club_discount = min(amount, cp / club_rate)

        remaining = max(0.0, amount - club_discount)
        if allow_gear_points:
            gp_cap_cash = remaining if gear_points_max_discount_amount is None else min(remaining, max(0.0, float(gear_points_max_discount_amount)))
            gp_cap_points = max(0, int(gp_cap_cash * gear_rate + 1e-9))
            gp = min(gp_req, max(0, int(gear_points_balance or 0)), gp_cap_points)
        else:
            gp = 0
        gear_subsidy = min(remaining, gp / gear_rate)
        payable = max(0.0, amount - club_discount - gear_subsidy)

        return PointQuote(
            original_amount=amount,
            club_points_requested=cp_req,
            club_points_used=cp,
            club_point_discount=club_discount,
            gear_points_requested=gp_req,
            gear_points_used=gp,
            platform_point_subsidy=gear_subsidy,
            payable=payable,
            club_funding_cost=club_discount,
            platform_funding_cost=gear_subsidy,
        )

    def club_points_earned_from_activity(self, cash_paid: float, rate_override: float | None = None) -> int:
        rate = float(self.setting('club_points_rate', 1)) if rate_override is None else max(0.0, float(rate_override))
        return max(0, int(float(cash_paid) * rate))

    def gear_points_earned_from_gear_order(self, cash_paid: float) -> int:
        return max(0, int(float(cash_paid) * float(self.setting('gear_points_rate', 1))))

    def redeem_for_activity(self, c, *, club_id: int, user_id: int, registration_id: int,
                            quote: PointQuote):
        if quote.club_points_used:
            c.execute('UPDATE club_members SET club_points_balance=club_points_balance-? WHERE club_id=? AND user_id=?',
                      (quote.club_points_used, club_id, user_id))
            c.execute('INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                      (club_id,user_id,'redeem',-quote.club_points_used,'activity_registration',str(registration_id),'活动积分抵扣；成本由俱乐部承担'))
            c.execute('INSERT INTO point_redemptions(user_id,club_id,order_kind,order_id,point_type,points_used,cash_value,funding_owner) VALUES(?,?,?,?,?,?,?,?)',
                      (user_id,club_id,'activity',registration_id,'club',quote.club_points_used,quote.club_point_discount,'CLUB'))

        if quote.gear_points_used:
            c.execute('UPDATE gear_point_accounts SET balance=balance-? WHERE user_id=?',
                      (quote.gear_points_used,user_id))
            c.execute('INSERT INTO gear_point_ledger(user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)',
                      (user_id,'redeem',-quote.gear_points_used,'activity_registration',str(registration_id),'装备积分抵扣活动；成本由总平台承担'))
            c.execute('INSERT INTO point_redemptions(user_id,club_id,order_kind,order_id,point_type,points_used,cash_value,funding_owner) VALUES(?,?,?,?,?,?,?,?)',
                      (user_id,club_id,'activity',registration_id,'gear',quote.gear_points_used,quote.platform_point_subsidy,'PLATFORM'))

    def earn_activity_points(self, c, *, club_id: int, user_id: int, registration_id: int, cash_paid: float,
                             enabled: bool = True, rate_override: float | None = None) -> int:
        if not enabled:
            return 0
        earned = self.club_points_earned_from_activity(cash_paid, rate_override=rate_override)
        if earned:
            c.execute('UPDATE club_members SET club_points_balance=club_points_balance+? WHERE club_id=? AND user_id=?',
                      (earned,club_id,user_id))
            c.execute('INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                      (club_id,user_id,'earn',earned,'activity_registration',str(registration_id),'活动消费获得活动积分；成本由俱乐部承担'))
        return earned

    def earn_gear_points(self, c, *, user_id: int, gear_order_id: int, cash_paid: float) -> int:
        pts = self.gear_points_earned_from_gear_order(cash_paid)
        c.execute('INSERT OR IGNORE INTO gear_point_accounts(user_id,balance) VALUES(?,0)',(user_id,))
        if pts:
            c.execute('UPDATE gear_point_accounts SET balance=balance+? WHERE user_id=?',(pts,user_id))
            c.execute('INSERT INTO gear_point_ledger(user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)',
                      (user_id,'earn',pts,'gear_order',str(gear_order_id),'装备消费获得装备积分；成本由总平台承担'))
        return pts

    def hold_points(self, c, *, intent_id: str, user_id: int, club_id: int|None, club_points: int=0, gear_points: int=0):
        """Reserve points before payment. Held points are removed from spendable balance immediately,
        then either consumed on successful payment or restored on cancellation/expiry.
        """
        club_points=max(0,int(club_points or 0)); gear_points=max(0,int(gear_points or 0))
        if club_points:
            if club_id is None: raise ValueError('Club Points require club_id')
            bal=c.execute('SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?',(club_id,user_id)).fetchone()
            if not bal or int(bal[0])<club_points: raise ValueError('活动积分余额不足')
            c.execute('UPDATE club_members SET club_points_balance=club_points_balance-? WHERE club_id=? AND user_id=?',(club_points,club_id,user_id))
            c.execute('INSERT INTO point_holds(intent_id,user_id,club_id,point_type,points,status,funding_owner) VALUES(?,?,?,?,?,?,?)',
                      (intent_id,user_id,club_id,'club',club_points,'held','CLUB'))
            c.execute('INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                      (club_id,user_id,'hold',-club_points,'checkout_intent',intent_id,'支付前锁定活动积分'))
        if gear_points:
            bal=c.execute('SELECT balance FROM gear_point_accounts WHERE user_id=?',(user_id,)).fetchone()
            if not bal or int(bal[0])<gear_points: raise ValueError('装备积分余额不足')
            c.execute('UPDATE gear_point_accounts SET balance=balance-? WHERE user_id=?',(gear_points,user_id))
            c.execute('INSERT INTO point_holds(intent_id,user_id,club_id,point_type,points,status,funding_owner) VALUES(?,?,?,?,?,?,?)',
                      (intent_id,user_id,club_id,'gear',gear_points,'held','PLATFORM'))
            c.execute('INSERT INTO gear_point_ledger(user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)',
                      (user_id,'hold',-gear_points,'checkout_intent',intent_id,'支付前锁定装备积分'))

    def release_holds(self,c,*,intent_id:str):
        holds=c.execute('SELECT * FROM point_holds WHERE intent_id=? AND status="held"',(intent_id,)).fetchall()
        for h in holds:
            h=dict(h); pts=int(h['points'])
            if h['point_type']=='club':
                c.execute('UPDATE club_members SET club_points_balance=club_points_balance+? WHERE club_id=? AND user_id=?',(pts,h['club_id'],h['user_id']))
                c.execute('INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                          (h['club_id'],h['user_id'],'hold_release',pts,'checkout_intent',intent_id,'取消/过期，释放活动积分'))
            else:
                c.execute('UPDATE gear_point_accounts SET balance=balance+? WHERE user_id=?',(pts,h['user_id']))
                c.execute('INSERT INTO gear_point_ledger(user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)',
                          (h['user_id'],'hold_release',pts,'checkout_intent',intent_id,'取消/过期，释放装备积分'))
            c.execute('UPDATE point_holds SET status="released",updated_at=CURRENT_TIMESTAMP WHERE id=?',(h['id'],))

    def consume_holds(self,c,*,intent_id:str):
        c.execute('UPDATE point_holds SET status="consumed",updated_at=CURRENT_TIMESTAMP WHERE intent_id=? AND status="held"',(intent_id,))

    def materialize_redemptions(self,c,*,intent_id:str,order_kind:str,order_id:int,club_id:int|None):
        """Convert consumed holds into final redemption audit rows. Balance was already reserved at intent creation."""
        holds=c.execute('SELECT * FROM point_holds WHERE intent_id=? AND status="consumed"',(intent_id,)).fetchall()
        intent=c.execute('SELECT * FROM checkout_intents WHERE id=?',(intent_id,)).fetchone()
        if not intent: return
        intent=dict(intent)
        for h in holds:
            h=dict(h); pts=int(h['points'])
            if h['point_type']=='club':
                cash=float(intent.get('club_point_discount') or 0); funding='CLUB'
                c.execute('INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                          (h['club_id'],h['user_id'],'redeem_commit',0,order_kind,str(order_id),'已支付，活动积分锁定转为正式抵扣'))
            else:
                cash=float(intent.get('platform_point_subsidy') or 0); funding='PLATFORM'
                c.execute('INSERT INTO gear_point_ledger(user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)',
                          (h['user_id'],'redeem_commit',0,order_kind,str(order_id),'已支付，装备积分锁定转为正式抵扣'))
            c.execute('INSERT INTO point_redemptions(user_id,club_id,order_kind,order_id,point_type,points_used,cash_value,funding_owner) VALUES(?,?,?,?,?,?,?,?)',
                      (h['user_id'],club_id,order_kind,order_id,h['point_type'],pts,cash,funding))

    def reverse_activity_registration(self, c, *, registration: dict[str,Any]):
        """Reverse point movements when a paid registration is cancelled/refunded.
        Keeps the two funding owners separate. Prototype behavior floors reversed earned
        club points at zero if the user has already spent them; production should move
        any shortfall into a points debt account.
        """
        rid=int(registration['id']); club_id=int(registration['club_id']); user_id=int(registration['user_id'])
        redemptions = c.execute('SELECT * FROM point_redemptions WHERE order_kind="activity" AND order_id=?',(rid,)).fetchall()
        for r in redemptions:
            r=dict(r)
            pts=int(r['points_used'])
            if r['point_type']=='club':
                c.execute('UPDATE club_members SET club_points_balance=club_points_balance+? WHERE club_id=? AND user_id=?',(pts,club_id,user_id))
                c.execute('INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                          (club_id,user_id,'refund_reverse',pts,'activity_refund',str(rid),'退款返还活动积分'))
            elif r['point_type']=='gear':
                c.execute('UPDATE gear_point_accounts SET balance=balance+? WHERE user_id=?',(pts,user_id))
                c.execute('INSERT INTO gear_point_ledger(user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)',
                          (user_id,'refund_reverse',pts,'activity_refund',str(rid),'退款返还装备积分'))

        earned_row=c.execute('SELECT COALESCE(SUM(amount),0) earned FROM club_point_ledger WHERE source_type="activity_registration" AND source_id=? AND type="earn"',(str(rid),)).fetchone()
        earned=int(earned_row[0] or 0)
        if earned:
            bal_row=c.execute('SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?',(club_id,user_id)).fetchone()
            balance=int(bal_row[0] or 0)
            deduct=min(balance,earned)
            if deduct:
                c.execute('UPDATE club_members SET club_points_balance=club_points_balance-? WHERE club_id=? AND user_id=?',(deduct,club_id,user_id))
                c.execute('INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                          (club_id,user_id,'refund_reverse',-deduct,'activity_refund',str(rid),'退款冲回活动消费所得积分'))
            shortfall=earned-deduct
            if shortfall:
                c.execute('INSERT INTO point_adjustment_debt(user_id,club_id,point_type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                          (user_id,club_id,'club',shortfall,'activity_refund',str(rid),'用户已消费部分应冲回积分，记录为积分欠账'))

    def reverse_activity_participant(self, c, *, registration: dict[str,Any], participant: dict[str,Any], allocation: dict[str,Any]):
        """Reverse the participant's allocated share only.

        Club/Gear Points are divisible and restored by allocation. Earned Club Points are
        reversed by the participant's allocation; any already-spent shortfall becomes debt.
        Order-level vouchers are handled separately because a single voucher is not split/reissued
        until the last active participant exits.
        """
        rid=int(registration['id']); pid=int(participant['id']); club_id=int(registration['club_id']); user_id=int(registration['user_id'])
        source=f"{rid}:{pid}"
        already=c.execute('SELECT 1 FROM club_point_ledger WHERE type="participant_refund_marker" AND source_type="participant_refund" AND source_id=? LIMIT 1',(source,)).fetchone()
        if already:
            return {'idempotent':True,'clubPointsReturned':0,'gearPointsReturned':0,'clubPointsEarnedReversed':0}
        cp=int(allocation.get('club_points_used') or 0); gp=int(allocation.get('gear_points_used') or 0); earned=int(allocation.get('club_points_earned') or 0)
        if cp:
            c.execute('UPDATE club_members SET club_points_balance=club_points_balance+? WHERE club_id=? AND user_id=?',(cp,club_id,user_id))
            c.execute('INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                      (club_id,user_id,'participant_refund_return',cp,'participant_refund',source,'单参加人退款返还分摊的活动积分'))
        if gp:
            c.execute('UPDATE gear_point_accounts SET balance=balance+? WHERE user_id=?',(gp,user_id))
            c.execute('INSERT INTO gear_point_ledger(user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)',
                      (user_id,'participant_refund_return',gp,'participant_refund',source,'单参加人退款返还分摊的装备积分'))
        reversed_earned=0
        if earned:
            balrow=c.execute('SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?',(club_id,user_id)).fetchone()
            balance=int(balrow[0] or 0) if balrow else 0
            deduct=min(balance,earned)
            if deduct:
                c.execute('UPDATE club_members SET club_points_balance=club_points_balance-? WHERE club_id=? AND user_id=?',(deduct,club_id,user_id))
                c.execute('INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                          (club_id,user_id,'participant_refund_earn_reverse',-deduct,'participant_refund',source,'单参加人退款冲回该参加人分摊的活动消费积分'))
                reversed_earned=deduct
            shortfall=earned-deduct
            if shortfall:
                c.execute('INSERT INTO point_adjustment_debt(user_id,club_id,point_type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                          (user_id,club_id,'club',shortfall,'participant_refund',source,'单参加人退款时已消费部分应冲回积分，记录欠账'))
        c.execute('INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                  (club_id,user_id,'participant_refund_marker',0,'participant_refund',source,'单参加人退款积分处理完成标记'))
        return {'clubPointsReturned':cp,'gearPointsReturned':gp,'clubPointsEarnedReversed':earned,'clubPointsDebt':max(0,earned-reversed_earned)}

    def reverse_gear_order(self, c, *, order: dict[str,Any]):
        """Reverse Gear Points for a fully refunded gear order.

        - returns Gear Points used as payment
        - reverses Gear Points earned from the paid cash amount
        - records a points debt when earned points have already been spent
        This method is idempotent at the caller/order-status layer.
        """
        oid=int(order['id']); user_id=int(order['user_id'])
        # Return Gear Points previously redeemed on this order.
        reds=c.execute('SELECT * FROM point_redemptions WHERE order_kind="gear" AND order_id=? AND point_type="gear"',(oid,)).fetchall()
        for r0 in reds:
            r=dict(r0); pts=int(r['points_used'] or 0)
            already=c.execute('SELECT 1 FROM gear_point_ledger WHERE type="refund_redeem_return" AND source_type="gear_refund" AND source_id=? LIMIT 1',(str(oid),)).fetchone()
            if already: break
            if pts:
                c.execute('UPDATE gear_point_accounts SET balance=balance+? WHERE user_id=?',(pts,user_id))
                c.execute('INSERT INTO gear_point_ledger(user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)',
                          (user_id,'refund_redeem_return',pts,'gear_refund',str(oid),'装备退款返还原订单使用的装备积分'))

        # Reverse Gear Points earned from this order.
        earned_row=c.execute('SELECT COALESCE(SUM(amount),0) FROM gear_point_ledger WHERE source_type="gear_order" AND source_id=? AND type="earn"',(str(oid),)).fetchone()
        earned=int(earned_row[0] or 0)
        already_earn_reverse=c.execute('SELECT 1 FROM gear_point_ledger WHERE type="refund_earn_reverse" AND source_type="gear_refund" AND source_id=? LIMIT 1',(str(oid),)).fetchone()
        if earned and not already_earn_reverse:
            balrow=c.execute('SELECT balance FROM gear_point_accounts WHERE user_id=?',(user_id,)).fetchone()
            balance=int(balrow[0] or 0) if balrow else 0
            deduct=min(balance,earned)
            if deduct:
                c.execute('UPDATE gear_point_accounts SET balance=balance-? WHERE user_id=?',(deduct,user_id))
                c.execute('INSERT INTO gear_point_ledger(user_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)',
                          (user_id,'refund_earn_reverse',-deduct,'gear_refund',str(oid),'装备退款冲回本订单产生的装备积分'))
            shortfall=earned-deduct
            if shortfall:
                c.execute('INSERT INTO point_adjustment_debt(user_id,club_id,point_type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?,?)',
                          (user_id,None,'gear',shortfall,'gear_refund',str(oid),'退款时用户已消费部分装备积分，记录积分欠账'))
        return {'returnedRedeemedGearPoints':sum(int(dict(r)['points_used'] or 0) for r in reds), 'earnedGearPointsToReverse':earned}

points_domain_rules = {
    'CLUB_POINTS': 'club-scoped; earned by activity consumption; club-funded; redeemable only inside the same club',
    'GEAR_POINTS': 'platform-scoped; earned by platform gear purchases; platform-funded; redeemable in platform-approved gear/benefit scenarios',
    'AI_CREDITS': 'B-end club asset; separate from C-end points; used only for AI calls',
}
