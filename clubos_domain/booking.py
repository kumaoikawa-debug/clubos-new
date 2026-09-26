from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

@dataclass
class BookingQuote:
    occurrence: dict
    wallet: dict
    points: dict
    points_policy: dict
    # 用户不做任何输入时，这张钱包在本单能抵掉多少。让 C 端「确认式」抵扣有据可依，
    # 而不是把「该抵多少」当成用户的算术题。
    max_redeemable: dict | None = None

class BookingEngine:
    """ClubOS activity-booking domain layer.

    Open-source booking engines may provide availability, holds, cart/order and refund
    primitives. ClubOS owns: occurrence identity, club ownership, point funding rules,
    registration records, and the business mapping between them.
    """
    def __init__(self, points_engine, wallet_snapshot: Callable, activity_points_policy, membership_engine=None, benefit_engine=None, participant_service=None):
        self.points = points_engine
        self.wallet_snapshot = wallet_snapshot
        self.activity_points_policy = activity_points_policy
        self.membership = membership_engine
        self.benefits = benefit_engine
        self.participants = participant_service

    def _quote_with_policy(self, *, activity: dict, amount: float, wallet: dict,
                           requested_club_points: int, requested_gear_points: int):
        policy = self.activity_points_policy.from_activity(activity)
        caps = dict(
            allow_club_points=policy.effective_accept_club_points,
            club_points_max_discount_percent=policy.club_points_max_discount_percent,
            allow_gear_points=policy.effective_accept_gear_points,
            gear_points_max_discount_amount=policy.gear_points_max_discount_amount,
        )
        q = self.points.quote(
            amount=amount,
            club_points_balance=wallet['clubPoints'],
            gear_points_balance=wallet['gearPoints'],
            requested_club_points=requested_club_points,
            requested_gear_points=requested_gear_points,
            **caps,
        )
        # Same caps, unbounded request → the ceiling the C-end offers the customer to confirm.
        max_redeemable = self.points.max_redeemable(
            amount=amount,
            club_points_balance=wallet['clubPoints'],
            gear_points_balance=wallet['gearPoints'],
            **caps,
        )
        return q, policy, max_redeemable

    def quote(self, c, *, activity_id:int, occurrence_id:int, user_id:int,
              requested_club_points:int=0, requested_gear_points:int=0, participant_count:int=1) -> BookingQuote:
        activity=c.execute('SELECT * FROM activities WHERE id=? AND status="published"',(activity_id,)).fetchone()
        if not activity:
            raise ValueError('活动不存在或不可售')
        activity=dict(activity)
        occ=c.execute('SELECT * FROM activity_occurrences WHERE id=? AND activity_id=? AND status="open"',(occurrence_id,activity_id)).fetchone()
        if not occ:
            raise ValueError('团期不存在或不可售')
        occ=dict(occ)
        participant_count=max(1,int(participant_count or 1))
        if int(occ['sold']) + participant_count > int(occ['capacity']):
            raise ValueError('该团期剩余名额不足')
        wallet=self.wallet_snapshot(c,user_id,int(occ['club_id']))
        q, policy, max_redeemable = self._quote_with_policy(
            activity=activity,
            amount=float(occ['price'])*participant_count,
            wallet=wallet,
            requested_club_points=requested_club_points,
            requested_gear_points=requested_gear_points,
        )
        return BookingQuote(occurrence=occ,wallet=wallet,points=q.as_dict(),points_policy=policy.as_dict(),
                            max_redeemable=max_redeemable)

    def book(self, c, *, activity:dict, occurrence:dict, user_id:int,
             requested_club_points:int=0, requested_gear_points:int=0,
             commerce_order_id:str|None=None, booking_ref:str|None=None,
             participants:list[dict]|None=None, participant_policy:dict|None=None):
        """Legacy direct booking seam kept for local/demo compatibility."""
        participants=list(participants or [])
        participant_count=max(1,len(participants))
        if int(occurrence['sold']) + participant_count > int(occurrence['capacity']):
            raise OverflowError('该团期剩余名额不足')
        wallet=self.wallet_snapshot(c,user_id,int(activity['club_id']))
        q, policy, _ = self._quote_with_policy(
            activity=activity,
            amount=float(occurrence['price'])*participant_count,
            wallet=wallet,
            requested_club_points=requested_club_points,
            requested_gear_points=requested_gear_points,
        )
        import json
        snapshot=json.dumps(policy.as_dict(),ensure_ascii=False)
        participant_policy=participant_policy or (self.participants.from_activity(activity).as_dict() if self.participants else {})
        c.execute("""INSERT INTO registrations(activity_id,occurrence_id,club_id,user_id,status,original_amount,club_point_discount,platform_point_subsidy,amount,commerce_order_id,booking_ref,points_policy_snapshot_json,participant_count,participant_policy_snapshot_json)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
            activity['id'],occurrence['id'],activity['club_id'],user_id,'paid',float(occurrence['price'])*participant_count,q.club_point_discount,q.platform_point_subsidy,q.payable,commerce_order_id,booking_ref,snapshot,participant_count,json.dumps(participant_policy,ensure_ascii=False)))
        rid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        c.execute('UPDATE activity_occurrences SET sold=sold+? WHERE id=?',(participant_count,occurrence['id']))
        participant_ids=[]
        if self.participants:
            participant_ids=self.participants.create_for_registration(c,registration_id=rid,activity=activity,occurrence=occurrence,participants=participants,payer_user_id=user_id,actor='legacy_signup')
        self.points.redeem_for_activity(c,club_id=int(activity['club_id']),user_id=user_id,registration_id=rid,quote=q)
        earned=self.points.earn_activity_points(
            c, club_id=int(activity['club_id']), user_id=user_id, registration_id=rid, cash_paid=q.payable,
            enabled=policy.effective_earn_club_points, rate_override=policy.club_points_earn_rate_override,
        )
        if self.participants and participant_ids:
            reg_alloc=c.execute('SELECT * FROM registrations WHERE id=?',(rid,)).fetchone()
            self.participants.create_financial_allocations(
                c,registration_id=rid,participant_ids=participant_ids,registration=dict(reg_alloc),
                club_points_used=int(q.club_points_used or 0),gear_points_used=int(q.gear_points_used or 0),club_points_earned=earned)
        if self.membership:
            self.membership.refresh_member(c,club_id=int(activity['club_id']),user_id=user_id)
        return rid,q,earned,policy.as_dict(),participant_ids

    def cancel(self,c,*,registration_id:int):
        r=c.execute('SELECT * FROM registrations WHERE id=?',(registration_id,)).fetchone()
        if not r: raise LookupError('报名记录不存在')
        r=dict(r)
        if r['status'] in ('cancelled','refunded'):
            return {'ok':True,'alreadyCancelled':True}
        self.points.reverse_activity_registration(c,registration=r)
        if self.benefits:
            self.benefits.restore_order_vouchers(c,order_kind='activity',order_id=registration_id)
        c.execute('UPDATE registrations SET status="refunded" WHERE id=?',(registration_id,))
        if r.get('occurrence_id'):
            c.execute('UPDATE activity_occurrences SET sold=MAX(sold-?,0) WHERE id=?',(max(1,int(r.get('participant_count') or 1)),r['occurrence_id']))
        if self.membership:
            self.membership.refresh_member(c,club_id=int(r['club_id']),user_id=int(r['user_id']))
        return {'ok':True,'registrationId':registration_id,'status':'refunded'}
