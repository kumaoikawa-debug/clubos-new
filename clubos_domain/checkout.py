from __future__ import annotations
import json, uuid, logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from clubos_domain.product_stock import variant_of, write_variant_stock

logger = logging.getLogger("checkout")

def occurrence_start_deadline(raw):
    """把团期出发时间解析成 datetime。

    只写了日期没写时刻的团期（如 '2026-09-19'）按当天 23:59:59 计 —— 当天还能报名，
    不该被当成过期；写了时刻的就按时刻算。解析不出来返回 None（无法判断就放行，
    交给人工兜底，而不是把正常团期误杀）。
    """
    s=str(raw or '').strip()
    if not s: return None
    s=s.replace('T',' ')
    for fmt in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M','%Y-%m-%d'):
        try:
            d=datetime.strptime(s,fmt)
            if fmt=='%Y-%m-%d': return d.replace(hour=23,minute=59,second=59)
            return d
        except ValueError:
            continue
    try: return datetime.fromisoformat(str(raw).strip())
    except Exception: return None

def occurrence_expired(occurrence,now=None):
    """团期是否已经出发。名额之外必须再校验时间 ——

    之前只校验剩余名额：一场 9 月出发的团期到 10 月仍然能下单付款，
    顾客付完钱立刻看到「活动已开始，不可退款」：钱付了、活动错过了、还退不了。
    这是一条真金白银的漏斗，不是显示问题。
    """
    d=occurrence_start_deadline((occurrence or {}).get('start_at'))
    if d is None: return False
    return d < (now or datetime.now())

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

    def _member_gear_discount(self,c,*,club_id:int,user_id:int,gross:float=0.0):
        """返回 (折扣率, 折扣金额)。

        会员等级是按俱乐部运营的，装备折扣 gear_discount 也配在俱乐部等级上
        （1.0 无折扣 / 0.95 九五折）。查不到等级、或配了非法值，一律按原价 ——
        宁可不打折，也不能自己造一个折扣出来。
        """
        try:
            r=c.execute('''SELECT t.gear_discount AS gd FROM club_members m
                           JOIN club_member_tiers t ON t.id=m.current_tier_id
                           WHERE m.user_id=? AND m.club_id=? AND t.status='active' ''',(user_id,club_id)).fetchone()
            rate=1.0
            if r is not None:
                raw=r['gd'] if 'gd' in r.keys() else (r[0] if len(r) else None)
                if raw is not None:
                    v=float(raw)
                    if 0<v<=1: rate=v
            gross=float(gross or 0.0)
            return rate,(round(gross*(1.0-rate),2) if rate<1 else 0.0)
        except Exception as e:
            logger.warning("member gear discount lookup failed (club=%s user=%s): %s",club_id,user_id,e)
            return 1.0,0.0

    def create_activity_intent(self, c, *, activity:dict, occurrence:dict, user_id:int,
                               requested_club_points:int=0, requested_gear_points:int=0,
                               voucher_codes:list[str]|None=None, participants:list[dict]|None=None,
                               participant_policy:dict|None=None):
        participants = list(participants or [])
        participant_count = max(1, len(participants))
        if occurrence_expired(occurrence):
            raise ValueError("该团期已出发，无法再报名；请选择其他团期或联系俱乐部")
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
        # 会员等级装备折扣：俱乐部后台按等级配置了 gear_discount（1.0 无折扣 / 0.95 九五折）。
        # 之前这里根本没读它 —— C 端会员中心白纸黑字写着「装备商城 9.5 折」、
        # 商品页也写着「会员折扣自动生效」，结算却按原价收钱，
        # 等于向顾客承诺了一个并不存在的价格。折扣先于积分抵扣生效：
        # 先按会员价降价，再在会员价上抵积分，否则「先抵积分再打折」会少折一份。
        rate,member_discount=self._member_gear_discount(c,club_id=club_id,user_id=user_id,gross=original)
        original=round(original-member_discount,2)
        wallet=self.wallet_snapshot(c,user_id,club_id)
        # Club Points never pay for platform gear. Gear Points are platform funded.
        quote=self.points.quote(amount=original,club_points_balance=0,gear_points_balance=wallet['gearPoints'],
                                requested_club_points=0,requested_gear_points=requested_gear_points,
                                allow_club_points=False,allow_gear_points=True)
        intent_id=self._new_id()
        benefit_quote={'clubDiscount':0.0,'platformSubsidy':0.0,'totalDiscount':0.0,'applied':[],
                       # 会员等级折扣由平台承担：装备订单本来就禁止用「俱乐部承担」的福利券
                       # （见下面的 clubDiscount 校验），俱乐部佣金仍按商品原价计提，
                       # 俱乐部不会因自己把等级折扣设得大方而做到亏本。
                       # 单独列出来是为了让 C 端把它显示成一行「会员折扣」，而不是混在券里说不清。
                       'memberGearDiscount':member_discount,'memberGearDiscountRate':rate,
                       'grossAmount':round(original+member_discount,2)}
        if self.benefits and voucher_codes:
            benefit_quote=self.benefits.hold_vouchers(
                c,intent_id=intent_id,voucher_codes=voucher_codes,user_id=user_id,
                club_id=club_id,kind='gear',amount_available=quote.payable)
        if benefit_quote['clubDiscount']:
            raise ValueError('装备订单不能使用俱乐部承担的福利券')
        cash_amount=max(0.0,float(quote.payable)-float(benefit_quote['platformSubsidy']))
        payload={'items':[{'productId':int(p['id']),'quantity':int(q),'unitPrice':float(p['price']),
                           'variantId':p.get('_variantId'),'variantName':p.get('_variantName'),
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
        # 建单到付款之间可能跨过出发时间（结算单不会自动过期），这里再拦一次。
        if occurrence_expired(occ): raise ValueError('该团期已出发，无法完成报名；请联系俱乐部处理')
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
        # 保险自动化：支付成功后自动向保险方投保（失败只置 failed + 写审计，不阻断主流程）
        if self.participants and participant_ids:
            try:
                from clubos_domain.insurance import InsuranceOrchestrator
                InsuranceOrchestrator().enroll_pending_for_registration(
                    c, registration_id=rid, occurrence=occ, activity=activity)
            except Exception as _e:
                logger.warning("auto-enroll insurance failed registration=%s: %s", rid, _e)
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
            # 下单时选定的规格要一路跟到落库：价格按规格价记，库存按规格扣。
            # 不跟的话顾客选了贵一档的规格、订单和佣金却按商品基础价记账。
            vid=item.get('variantId')
            v=c.execute('SELECT * FROM product_variants WHERE id=? AND product_id=?',(int(vid),int(item['productId']))).fetchone() if vid else \
              c.execute('SELECT * FROM product_variants WHERE product_id=? AND status="active" ORDER BY is_default DESC,sort,id LIMIT 1',(int(item['productId']),)).fetchone()
            if v:
                p['_variantId']=int(v['id']); p['_variantName']=v['name']
                if v['price'] is not None: p['price']=float(v['price'])
                stock=int(v['stock'] or 0)
            else:
                stock=int(p['stock'] or 0)
            if stock<q: raise OverflowError(f"{p['name']}{(' · '+p['_variantName']) if p.get('_variantName') else ''} 库存不足")
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
            c.execute('INSERT INTO gear_order_items(order_id,product_id,variant_id,variant_name,quantity,unit_price,unit_cost_snapshot) VALUES(?,?,?,?,?,?,?)',(oid,p['id'],p.get('_variantId'),p.get('_variantName'),q,p['price'],float(p.get('average_cost') or 0)))
            if self.inventory:
                self.inventory.sale_outbound(c,product_id=int(p['id']),quantity=q,order_id=oid,actor_type='checkout',variant_id=p.get('_variantId'))
            else:
                # 兜底分支也写规格层：products.stock 已是冗余字段，只改它会被下一次汇总重算抹掉
                _v=variant_of(c,product_id=int(p['id']),variant_id=p.get('_variantId'))
                if _v: write_variant_stock(c,product_id=int(p['id']),variant_id=int(_v['id']),new_stock=max(0,int(_v['stock'] or 0)-q))
                else: c.execute('UPDATE products SET stock=stock-? WHERE id=?',(q,p['id']))
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
        # 会员折扣回传给 C 端：顾客在小票上要看到「原价多少、会员省了多少、实付多少」，
        # 否则九折打了等于没打 —— 他只会看到一个数字，不知道自己占到了会员的便宜。
        gross=round(sum(float(p['price'])*q for p,q in resolved),2)
        member_cut=round(max(0.0,gross-float(intent['original_amount'] or 0)),2)
        result={'ok':True,'kind':'gear','checkoutId':intent['id'],'orderId':oid,'gearPointsEarned':earned,
                'clubCommission':round(commission,2),'clubAIReward':reward,'cashPaid':intent['cash_amount'],
                'grossAmount':gross,'memberGearDiscount':member_cut,
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
