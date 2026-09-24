from __future__ import annotations
import json, uuid
from dataclasses import dataclass
from typing import Any

@dataclass
class CheckoutIntent:
    id: str
    kind: str
    status: str
    original_amount: float
    cash_amount: float
    club_points_reserved: int
    gear_points_reserved: int
    club_point_discount: float
    platform_point_subsidy: float

class CheckoutEngine:
    """Transactional seam between ClubOS domain rules and an external commerce engine.

    Medusa owns cart/order/payment/fulfillment primitives. ClubOS owns activity occurrences,
    two points systems, vouchers/benefits, funding owners, registration/member records,
    commission attribution and AI-credit rewards.
    """
    def __init__(self, points_engine, wallet_snapshot, setting_getter, activity_points_policy,
                 membership_engine=None, benefit_engine=None, participant_service=None, inventory_engine=None, ai_credit_engine=None):
        self.points = points_engine
        self.wallet_snapshot = wallet_snapshot
        self.setting = setting_getter
        self.activity_points_policy = activity_points_policy
        self.membership = membership_engine
        self.benefits = benefit_engine
        self.participants = participant_service
        self.inventory = inventory_engine
        self.ai_credits = ai_credit_engine

    def _new_id(self):
        return "chk_" + uuid.uuid4().hex

    def create_activity_intent(self, c, *, activity:dict, occurrence:dict, user_id:int,
                               requested_club_points:int=0, requested_gear_points:int=0,
                               voucher_codes:list[str]|None=None, participants:list[dict]|None=None,
                               participant_policy:dict|None=None):
        participants = list(participants or [])
        participant_count = max(1, len(participants))
        if int(occurrence['sold']) + participant_count > int(occurrence['capacity']):
            raise OverflowError(f"该团期剩余名额不足，当前需要 {participant_count} 个名额")
        wallet = self.wallet_snapshot(c, user_id, int(activity['club_id']))
        policy = self.activity_points_policy.from_activity(activity)
        original_amount = float(occurrence['price']) * participant_count
        quote = self.points.quote(
            amount=original_amount,
            club_points_balance=wallet['clubPoints'],
            gear_points_balance=wallet['gearPoints'],
            requested_club_points=requested_club_points,
            requested_gear_points=requested_gear_points,
            allow_club_points=policy.effective_accept_club_points,
            club_points_max_discount_percent=policy.club_points_max_discount_percent,
            allow_gear_points=policy.effective_accept_gear_points,
            gear_points_max_discount_amount=policy.gear_points_max_discount_amount,
        )
        intent_id=self._new_id()
        benefit_quote={'clubDiscount':0.0,'platformSubsidy':0.0,'totalDiscount':0.0,'applied':[]}
        if self.benefits and voucher_codes:
            benefit_quote=self.benefits.hold_vouchers(
                c,intent_id=intent_id,voucher_codes=voucher_codes,user_id=user_id,
                club_id=int(activity['club_id']),kind='activity',amount_available=quote.payable)
        cash_amount=max(0.0,float(quote.payable)-float(benefit_quote['totalDiscount']))
        policy_snapshot=policy.as_dict()
        participant_policy = participant_policy or (self.participants.from_activity(activity).as_dict() if self.participants else {})
        payload={'activityId':int(activity['id']),'occurrenceId':int(occurrence['id']),'participants':participants}
        redemption_ids=[x['redemptionId'] for x in benefit_quote['applied']]
        c.execute("""INSERT INTO checkout_intents(
          id,kind,user_id,club_id,status,original_amount,cash_amount,club_points_reserved,
          gear_points_reserved,club_point_discount,platform_point_subsidy,club_benefit_discount,
          platform_benefit_subsidy,benefit_redemption_ids_json,payload_json,points_policy_snapshot_json,
          participant_count,participant_policy_snapshot_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
            intent_id,'activity',user_id,int(activity['club_id']),'pending_payment',
            original_amount,cash_amount,quote.club_points_used,quote.gear_points_used,
            quote.club_point_discount,quote.platform_point_subsidy,benefit_quote['clubDiscount'],
            benefit_quote['platformSubsidy'],json.dumps(redemption_ids),json.dumps(payload,ensure_ascii=False),
            json.dumps(policy_snapshot,ensure_ascii=False),participant_count,json.dumps(participant_policy,ensure_ascii=False)))
        self.points.hold_points(c,intent_id=intent_id,user_id=user_id,club_id=int(activity['club_id']),
                               club_points=quote.club_points_used,gear_points=quote.gear_points_used)
        quote.payable=cash_amount
        quote.club_funding_cost=float(quote.club_funding_cost)+float(benefit_quote['clubDiscount'])
        quote.platform_funding_cost=float(quote.platform_funding_cost)+float(benefit_quote['platformSubsidy'])
        return intent_id, quote, policy_snapshot, benefit_quote

    def create_gear_intent(self,c,*,club_id:int,user_id:int,items:list[dict],resolved_products:list[tuple[dict,int]],
                           requested_gear_points:int=0,voucher_codes:list[str]|None=None):
        original=sum(float(p['price'])*q for p,q in resolved_products)
        wallet=self.wallet_snapshot(c,user_id,club_id)
        # Club Points never pay for platform gear. Gear Points are platform funded.
        quote=self.points.quote(amount=original,club_points_balance=0,gear_points_balance=wallet['gearPoints'],
                                requested_club_points=0,requested_gear_points=requested_gear_points,
                                allow_club_points=False,allow_gear_points=True)
        intent_id=self._new_id()
        benefit_quote={'clubDiscount':0.0,'platformSubsidy':0.0,'totalDiscount':0.0,'applied':[]}
        if self.benefits and voucher_codes:
            benefit_quote=self.benefits.hold_vouchers(
                c,intent_id=intent_id,voucher_codes=voucher_codes,user_id=user_id,
                club_id=club_id,kind='gear',amount_available=quote.payable)
        if benefit_quote['clubDiscount']:
            raise ValueError('装备订单不能使用俱乐部承担的福利券')
        cash_amount=max(0.0,float(quote.payable)-float(benefit_quote['platformSubsidy']))
        payload={'items':[{'productId':int(p['id']),'quantity':int(q),'unitPrice':float(p['price']),
                           'commerceVariantId':p.get('commerce_variant_id')} for p,q in resolved_products]}
        redemption_ids=[x['redemptionId'] for x in benefit_quote['applied']]
        c.execute('''INSERT INTO checkout_intents(
          id,kind,user_id,club_id,status,original_amount,cash_amount,club_points_reserved,
          gear_points_reserved,club_point_discount,platform_point_subsidy,club_benefit_discount,
          platform_benefit_subsidy,benefit_redemption_ids_json,payload_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
            intent_id,'gear',user_id,club_id,'pending_payment',original,cash_amount,0,quote.gear_points_used,
            0,quote.platform_point_subsidy,0,benefit_quote['platformSubsidy'],json.dumps(redemption_ids),
            json.dumps(payload,ensure_ascii=False)))
        self.points.hold_points(c,intent_id=intent_id,user_id=user_id,club_id=None,club_points=0,gear_points=quote.gear_points_used)
        quote.payable=cash_amount
        quote.platform_funding_cost=float(quote.platform_funding_cost)+float(benefit_quote['platformSubsidy'])
        return intent_id,quote,payload,benefit_quote

    def attach_commerce(self,c,*,intent_id:str,cart_id:str|None=None,order_id:str|None=None):
        c.execute('UPDATE checkout_intents SET commerce_cart_id=COALESCE(?,commerce_cart_id), commerce_order_id=COALESCE(?,commerce_order_id), updated_at=CURRENT_TIMESTAMP WHERE id=?',
                  (cart_id,order_id,intent_id))

    def cancel_intent(self,c,*,intent_id:str,reason='cancelled'):
        intent=c.execute('SELECT * FROM checkout_intents WHERE id=?',(intent_id,)).fetchone()
        if not intent: raise LookupError('结算单不存在')
        intent=dict(intent)
        if intent['status'] in ('cancelled','expired'):
            return {'ok':True,'alreadyCancelled':True}
        if intent['status']=='paid':
            raise ValueError('已支付结算单应走退款流程')
        self.points.release_holds(c,intent_id=intent_id)
        if self.benefits:
            self.benefits.release_vouchers(c,intent_id=intent_id)
        c.execute("UPDATE checkout_intents SET status=?,payment_status='cancelled',cancelled_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",(reason,intent_id))
        return {'ok':True,'checkoutId':intent_id,'status':reason}

    def confirm(self,c,*,intent_id:str,commerce_order_id:str|None=None):
        intent=c.execute('SELECT * FROM checkout_intents WHERE id=?',(intent_id,)).fetchone()
        if not intent: raise LookupError('结算单不存在')
        intent=dict(intent)
        if intent['status']=='paid':
            return self._result_from_paid(c,intent)
        if intent['status']!='pending_payment':
            raise ValueError(f"当前状态不能确认支付: {intent['status']}")
        payload=json.loads(intent['payload_json'] or '{}')
        self.points.consume_holds(c,intent_id=intent_id)
        if intent['kind']=='activity':
            result=self._finalize_activity(c,intent,payload,commerce_order_id)
        elif intent['kind']=='gear':
            result=self._finalize_gear(c,intent,payload,commerce_order_id)
        else:
            raise ValueError('unknown checkout kind')
        c.execute("UPDATE checkout_intents SET status='paid',payment_status='succeeded',paid_at=COALESCE(paid_at,CURRENT_TIMESTAMP), commerce_order_id=COALESCE(?,commerce_order_id), updated_at=CURRENT_TIMESTAMP WHERE id=?",
                  (commerce_order_id,intent_id))
        return result

    def _finalize_activity(self,c,intent,payload,commerce_order_id):
        occ=c.execute('SELECT * FROM activity_occurrences WHERE id=? AND status="open"',(int(payload['occurrenceId']),)).fetchone()
        activity=c.execute('SELECT * FROM activities WHERE id=? AND status="published"',(int(payload['activityId']),)).fetchone()
        if not occ or not activity: raise ValueError('活动或团期已不可售')
        occ=dict(occ); activity=dict(activity)
        participant_count=max(1,int(intent.get('participant_count') or len(payload.get('participants') or []) or 1))
        if int(occ['sold']) + participant_count > int(occ['capacity']): raise OverflowError('该团期剩余名额不足')

        policy_snapshot=json.loads(intent.get('points_policy_snapshot_json') or '{}')
        if not policy_snapshot:
            policy_snapshot=self.activity_points_policy.from_activity(activity).as_dict()
        participant_policy_snapshot=json.loads(intent.get('participant_policy_snapshot_json') or '{}')
        if not participant_policy_snapshot and self.participants:
            participant_policy_snapshot=self.participants.from_activity(activity).as_dict()
        effective=policy_snapshot.get('effective') or {}
        c.execute("""INSERT INTO registrations(activity_id,occurrence_id,club_id,user_id,status,original_amount,
                     club_point_discount,platform_point_subsidy,club_benefit_discount,platform_benefit_subsidy,
                     benefit_redemption_ids_json,amount,commerce_order_id,booking_ref,points_policy_snapshot_json,payment_status,
                     participant_count,participant_policy_snapshot_json)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
            activity['id'],occ['id'],activity['club_id'],intent['user_id'],'paid',intent['original_amount'],
            intent['club_point_discount'],intent['platform_point_subsidy'],intent.get('club_benefit_discount',0),
            intent.get('platform_benefit_subsidy',0),intent.get('benefit_redemption_ids_json') or '[]',
            intent['cash_amount'],commerce_order_id,intent['id'],json.dumps(policy_snapshot,ensure_ascii=False),'succeeded',
            participant_count,json.dumps(participant_policy_snapshot,ensure_ascii=False)))
        rid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        c.execute('UPDATE activity_occurrences SET sold=sold+? WHERE id=?',(participant_count,occ['id']))
        participant_ids=[]
        if self.participants:
            participant_ids=self.participants.create_for_registration(
                c,registration_id=rid,activity=activity,occurrence=occ,participants=payload.get('participants') or [],
                payer_user_id=int(intent['user_id']))
        self.points.materialize_redemptions(c,intent_id=intent['id'],order_kind='activity',order_id=rid,club_id=int(activity['club_id']))
        if self.benefits:
            self.benefits.consume_vouchers(c,intent_id=intent['id'],order_kind='activity',order_id=rid)
        earned=self.points.earn_activity_points(
            c, club_id=int(activity['club_id']), user_id=int(intent['user_id']), registration_id=rid,
            cash_paid=float(intent['cash_amount']), enabled=bool(effective.get('earnClubPoints', False)),
            rate_override=policy_snapshot.get('clubPointsEarnRateOverride'),
        )
        if self.participants and participant_ids:
            reg_alloc=c.execute('SELECT * FROM registrations WHERE id=?',(rid,)).fetchone()
            self.participants.create_financial_allocations(
                c,registration_id=rid,participant_ids=participant_ids,registration=dict(reg_alloc),
                club_points_used=int(intent.get('club_points_reserved') or 0),gear_points_used=int(intent.get('gear_points_reserved') or 0),
                club_points_earned=earned)
        member=None
        if self.membership:
            member=self.membership.refresh_member(c,club_id=int(activity['club_id']),user_id=int(intent['user_id']))
        c.execute('UPDATE checkout_intents SET result_id=? WHERE id=?',(str(rid),intent['id']))
        result={'ok':True,'kind':'activity','checkoutId':intent['id'],'registrationId':rid,'clubPointsEarned':earned,
                'cashPaid':intent['cash_amount'],'commerceOrderId':commerce_order_id,'pointsPolicy':policy_snapshot,
                'clubBenefitDiscount':float(intent.get('club_benefit_discount') or 0),
                'platformBenefitSubsidy':float(intent.get('platform_benefit_subsidy') or 0),
                'memberLevel':(member.level if member else None),'participantCount':participant_count,'participantIds':participant_ids}
        # Persist the confirmed result so async payment polling (get_checkout) can hand back the
        # same receipt shape the synchronous /pay path returns.
        c.execute('UPDATE checkout_intents SET result_json=? WHERE id=?',(json.dumps(result,ensure_ascii=False),intent['id']))
        return result

    def _finalize_gear(self,c,intent,payload,commerce_order_id):
        resolved=[]; commission=0.0
        for item in payload.get('items',[]):
            p=c.execute('SELECT * FROM products WHERE id=? AND status="active"',(int(item['productId']),)).fetchone()
            if not p: raise ValueError('商品已不可售')
            p=dict(p); q=max(1,int(item['quantity']))
            if int(p['stock'])<q: raise OverflowError(f"{p['name']} 库存不足")
            commission += float(p['price'])*q*float(p['commission_rate'])
            resolved.append((p,q))
        c.execute('''INSERT INTO gear_orders(user_id,source_club_id,total,cash_paid,platform_point_subsidy,
                     platform_benefit_subsidy,benefit_redemption_ids_json,status,club_commission,commerce_order_id,payment_status)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                  (intent['user_id'],intent['club_id'],intent['original_amount'],intent['cash_amount'],intent['platform_point_subsidy'],
                   intent.get('platform_benefit_subsidy',0),intent.get('benefit_redemption_ids_json') or '[]',
                   'paid',commission,commerce_order_id,'succeeded'))
        oid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        for p,q in resolved:
            c.execute('INSERT INTO gear_order_items(order_id,product_id,quantity,unit_price,unit_cost_snapshot) VALUES(?,?,?,?,?)',(oid,p['id'],q,p['price'],float(p.get('average_cost') or 0)))
            if self.inventory:
                self.inventory.sale_outbound(c,product_id=int(p['id']),quantity=q,order_id=oid,actor_type='checkout')
            else:
                c.execute('UPDATE products SET stock=stock-? WHERE id=?',(q,p['id']))
        self.points.materialize_redemptions(c,intent_id=intent['id'],order_kind='gear',order_id=oid,club_id=int(intent['club_id']))
        if self.benefits:
            self.benefits.consume_vouchers(c,intent_id=intent['id'],order_kind='gear',order_id=oid)
        c.execute('INSERT INTO commission_ledger(club_id,order_id,amount,status) VALUES(?,?,?,?)',(intent['club_id'],oid,commission,'pending'))
        earned=self.points.earn_gear_points(c,user_id=int(intent['user_id']),gear_order_id=oid,cash_paid=float(intent['cash_amount']))
        reward=int(float(intent['cash_amount'])/1000*int(self.setting('mall_ai_reward_per_1000_gmv',20)))
        if reward>0:
            if self.ai_credits:
                self.ai_credits.grant(c,club_id=int(intent['club_id']),amount=reward,ledger_type='mall_reward',source_type='gear_order',source_id=str(oid),note='装备商城销售奖励')
            else:
                c.execute('UPDATE ai_credit_accounts SET balance=balance+? WHERE club_id=?',(reward,intent['club_id']))
                c.execute('INSERT INTO ai_credit_ledger(club_id,type,amount,source_type,source_id,note) VALUES(?,?,?,?,?,?)',
                          (intent['club_id'],'mall_reward',reward,'gear_order',str(oid),'装备商城销售奖励'))
        c.execute('UPDATE checkout_intents SET result_id=? WHERE id=?',(str(oid),intent['id']))
        result={'ok':True,'kind':'gear','checkoutId':intent['id'],'orderId':oid,'gearPointsEarned':earned,
                'clubCommission':round(commission,2),'clubAIReward':reward,'cashPaid':intent['cash_amount'],
                'platformPointSubsidy':intent['platform_point_subsidy'],
                'platformBenefitSubsidy':float(intent.get('platform_benefit_subsidy') or 0),
                'commerceOrderId':commerce_order_id}
        # Persist the confirmed result so async payment polling (get_checkout) can hand back the
        # same receipt shape the synchronous /pay path returns.
        c.execute('UPDATE checkout_intents SET result_json=? WHERE id=?',(json.dumps(result,ensure_ascii=False),intent['id']))
        return result

    def _result_from_paid(self,c,intent):
        return {'ok':True,'checkoutId':intent['id'],'kind':intent['kind'],'status':'paid','resultId':intent.get('result_id'),
                'commerceOrderId':intent.get('commerce_order_id'),'idempotent':True}
