from __future__ import annotations
import uuid, json
from typing import Any


class RefundLifecycleEngine:
    """Unified refund workflow for activity registrations and Gear orders.

    v0.11 adds activity-owned refund policies. Activity policies decide the cash refund
    percentage based on time-to-start. Points/vouchers are still non-cash assets and are
    fully restored only after the refund succeeds; earned activity points are fully reversed.
    Gear refunds remain full-order refunds owned by the platform.
    """

    def __init__(self, points_engine, benefit_engine, membership_engine, gear_refund_engine, activity_refund_policy=None, participant_service=None):
        self.points = points_engine
        self.benefits = benefit_engine
        self.membership = membership_engine
        self.gear_refund = gear_refund_engine
        self.activity_refund_policy = activity_refund_policy
        self.participants = participant_service

    def _id(self) -> str:
        return 'rfd_' + uuid.uuid4().hex

    def _checkout_for_result(self, c, kind: str, result_id: int):
        return c.execute('SELECT * FROM checkout_intents WHERE kind=? AND result_id=? ORDER BY created_at DESC LIMIT 1',
                         (kind, str(result_id))).fetchone()

    def request_activity(self, c, *, registration_id: int, requester: str, reason: str = '') -> dict[str, Any]:
        reg_row = c.execute('SELECT * FROM registrations WHERE id=?', (registration_id,)).fetchone()
        if not reg_row:
            raise LookupError('报名记录不存在')
        reg = dict(reg_row)
        if reg['status'] == 'refunded' or reg.get('refund_status') == 'succeeded':
            existing = c.execute("SELECT * FROM refund_requests WHERE kind='activity' AND registration_id=? ORDER BY created_at DESC LIMIT 1", (registration_id,)).fetchone()
            return {'ok': True, 'status': 'refunded', 'idempotent': True, 'refundRequestId': (existing['id'] if existing else None)}
        existing = c.execute("SELECT * FROM refund_requests WHERE kind='activity' AND registration_id=? AND COALESCE(refund_scope,'full')='full' AND status IN ('requested','processing') ORDER BY created_at DESC LIMIT 1", (registration_id,)).fetchone()
        if existing:
            ex = dict(existing)
            return {
                'ok': True, 'refundRequestId': ex['id'], 'status': ex['status'], 'idempotent': True,
                'cashAmount': float(ex.get('cash_amount') or 0),
                'refundPercent': float(ex.get('refund_percent') or 0),
                'retainedCashAmount': float(ex.get('retained_cash_amount') or 0),
            }
        if reg['status'] != 'paid':
            raise ValueError(f"当前报名状态不能申请退款: {reg['status']}")
        partial_done=c.execute("SELECT COUNT(*) FROM registration_participants WHERE registration_id=? AND status='refunded'",(registration_id,)).fetchone()[0]
        partial_pending=c.execute("SELECT COUNT(*) FROM registration_participants WHERE registration_id=? AND refund_status IN ('requested','processing')",(registration_id,)).fetchone()[0]
        if partial_done or partial_pending:
            raise ValueError('该订单已有参加人部分退款记录，请对剩余参加人逐一申请退款')

        activity_row = c.execute('SELECT * FROM activities WHERE id=?', (reg['activity_id'],)).fetchone()
        occurrence_row = c.execute('SELECT * FROM activity_occurrences WHERE id=?', (reg['occurrence_id'],)).fetchone() if reg.get('occurrence_id') else None
        if not activity_row:
            raise LookupError('活动不存在')
        activity = dict(activity_row)
        occurrence = dict(occurrence_row) if occurrence_row else None
        if self.activity_refund_policy:
            decision = self.activity_refund_policy.evaluate(
                activity=activity, occurrence=occurrence, cash_paid=float(reg.get('amount') or 0)
            )
        else:
            decision = {
                'eligible': True, 'cashRefundPercent': 100,
                'cashRefundAmount': round(float(reg.get('amount') or 0), 2),
                'retainedCashAmount': 0, 'matchedRule': '兼容规则：全额退款',
                'policy': {'enabled': True}, 'nonCashRestoration': 'FULL_ON_SUCCESS'
            }
        if not decision.get('eligible'):
            raise ValueError(decision.get('reason') or '当前时间不符合本活动退款规则')

        checkout = self._checkout_for_result(c, 'activity', registration_id)
        refund_id = self._id()
        cash_amount = float(decision.get('cashRefundAmount') or 0)
        original_cash = float(reg.get('amount') or 0)
        refund_percent = float(decision.get('cashRefundPercent') or 0)
        retained = float(decision.get('retainedCashAmount') or 0)
        policy_snapshot = json.dumps(decision, ensure_ascii=False)
        c.execute('''INSERT INTO refund_requests(
                     id,kind,checkout_intent_id,registration_id,user_id,club_id,status,reason,
                     cash_amount,original_cash_amount,refund_percent,retained_cash_amount,policy_snapshot_json,policy_label,
                     commerce_order_id,requested_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
            refund_id, 'activity', checkout['id'] if checkout else None, registration_id, reg['user_id'], reg['club_id'],
            'requested', reason, cash_amount, original_cash, refund_percent, retained, policy_snapshot,
            str(decision.get('matchedRule') or decision.get('reason') or ''), reg.get('commerce_order_id'), requester
        ))
        c.execute('''UPDATE registrations SET refund_status='requested',refund_request_id=?,refund_cash_amount=?,
                     refund_percent=?,retained_cash_amount=?,refund_policy_snapshot_json=? WHERE id=?''',
                  (refund_id, cash_amount, refund_percent, retained, policy_snapshot, registration_id))
        if checkout:
            c.execute("UPDATE checkout_intents SET refund_status='requested',updated_at=CURRENT_TIMESTAMP WHERE id=?", (checkout['id'],))
        return {
            'ok': True, 'refundRequestId': refund_id, 'kind': 'activity', 'status': 'requested',
            'cashAmount': cash_amount, 'originalCashAmount': original_cash, 'refundPercent': refund_percent,
            'retainedCashAmount': retained, 'policyDecision': decision,
        }

    def request_activity_participant(self, c, *, registration_id: int, participant_id: int, requester: str, reason: str = '') -> dict[str, Any]:
        if not self.participants:
            raise RuntimeError('participant service unavailable')
        reg_row=c.execute('SELECT * FROM registrations WHERE id=?',(registration_id,)).fetchone()
        if not reg_row: raise LookupError('报名记录不存在')
        reg=dict(reg_row)
        if reg['status']!='paid': raise ValueError(f"当前报名状态不能申请单人退款: {reg['status']}")
        full_pending=c.execute("SELECT 1 FROM refund_requests WHERE kind='activity' AND registration_id=? AND COALESCE(refund_scope,'full')='full' AND status IN ('requested','processing') LIMIT 1",(registration_id,)).fetchone()
        if full_pending: raise ValueError('整单退款正在处理中，不能再申请单个参加人退出')
        p_row=c.execute('SELECT * FROM registration_participants WHERE id=? AND registration_id=?',(participant_id,registration_id)).fetchone()
        if not p_row: raise LookupError('参加人不存在')
        participant=dict(p_row)
        if participant['status']=='refunded' or participant.get('refund_status')=='succeeded':
            existing=c.execute("SELECT * FROM refund_requests WHERE kind='activity' AND participant_id=? ORDER BY created_at DESC LIMIT 1",(participant_id,)).fetchone()
            return {'ok':True,'status':'refunded','idempotent':True,'refundRequestId':existing['id'] if existing else None}
        existing=c.execute("SELECT * FROM refund_requests WHERE kind='activity' AND participant_id=? AND status IN ('requested','processing') ORDER BY created_at DESC LIMIT 1",(participant_id,)).fetchone()
        if existing:
            ex=dict(existing); return {'ok':True,'refundRequestId':ex['id'],'status':ex['status'],'idempotent':True,'cashAmount':float(ex.get('cash_amount') or 0),'refundPercent':float(ex.get('refund_percent') or 0),'retainedCashAmount':float(ex.get('retained_cash_amount') or 0)}
        if participant['status']!='active': raise ValueError('当前参加人状态不可申请退出')
        allocation=self.participants.financial_allocation(c,participant_id)
        if not allocation: raise ValueError('未找到参加人的交易分摊记录')
        activity_row=c.execute('SELECT * FROM activities WHERE id=?',(reg['activity_id'],)).fetchone()
        occurrence_row=c.execute('SELECT * FROM activity_occurrences WHERE id=?',(reg['occurrence_id'],)).fetchone() if reg.get('occurrence_id') else None
        if not activity_row: raise LookupError('活动不存在')
        activity=dict(activity_row); occurrence=dict(occurrence_row) if occurrence_row else None
        decision=self.activity_refund_policy.evaluate(activity=activity,occurrence=occurrence,cash_paid=float(allocation.get('cash_paid') or 0)) if self.activity_refund_policy else {'eligible':True,'cashRefundPercent':100,'cashRefundAmount':float(allocation.get('cash_paid') or 0),'retainedCashAmount':0,'matchedRule':'兼容规则：全额退款','policy':{'enabled':True}}
        if not decision.get('eligible'): raise ValueError(decision.get('reason') or '当前时间不符合本活动退款规则')
        checkout=self._checkout_for_result(c,'activity',registration_id)
        refund_id=self._id(); cash_amount=float(decision.get('cashRefundAmount') or 0); original_cash=float(allocation.get('cash_paid') or 0); pct=float(decision.get('cashRefundPercent') or 0); retained=float(decision.get('retainedCashAmount') or 0)
        policy_snapshot=json.dumps(decision,ensure_ascii=False)
        c.execute('''INSERT INTO refund_requests(
          id,kind,checkout_intent_id,registration_id,participant_id,refund_scope,user_id,club_id,status,reason,
          cash_amount,original_cash_amount,refund_percent,retained_cash_amount,policy_snapshot_json,policy_label,
          allocated_original_amount,allocated_club_points,allocated_gear_points,allocated_club_point_discount,
          allocated_platform_point_subsidy,allocated_club_benefit_discount,allocated_platform_benefit_subsidy,
          allocated_club_points_earned,commerce_order_id,requested_by)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
          refund_id,'activity',checkout['id'] if checkout else None,registration_id,participant_id,'participant',reg['user_id'],reg['club_id'],'requested',reason,
          cash_amount,original_cash,pct,retained,policy_snapshot,str(decision.get('matchedRule') or decision.get('reason') or ''),
          float(allocation.get('original_amount') or 0),int(allocation.get('club_points_used') or 0),int(allocation.get('gear_points_used') or 0),float(allocation.get('club_point_discount') or 0),
          float(allocation.get('platform_point_subsidy') or 0),float(allocation.get('club_benefit_discount') or 0),float(allocation.get('platform_benefit_subsidy') or 0),int(allocation.get('club_points_earned') or 0),
          reg.get('commerce_order_id'),requester))
        c.execute("UPDATE registration_participants SET refund_status='requested',refund_request_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(refund_id,participant_id))
        c.execute("UPDATE registrations SET refund_status='partial_requested' WHERE id=? AND refund_status NOT IN ('requested','processing')",(registration_id,))
        return {'ok':True,'refundRequestId':refund_id,'kind':'activity','refundScope':'participant','participantId':participant_id,'participantName':participant.get('name'),'status':'requested','cashAmount':cash_amount,'originalCashAmount':original_cash,'refundPercent':pct,'retainedCashAmount':retained,
                'pointsToRestore':{'club':int(allocation.get('club_points_used') or 0),'gear':int(allocation.get('gear_points_used') or 0)},
                'benefitAllocation':{'club':float(allocation.get('club_benefit_discount') or 0),'platform':float(allocation.get('platform_benefit_subsidy') or 0)},'policyDecision':decision}

    def request_gear(self, c, *, order_id: int, requester: str, reason: str = '') -> dict[str, Any]:
        order_row = c.execute('SELECT * FROM gear_orders WHERE id=?', (order_id,)).fetchone()
        if not order_row:
            raise LookupError('装备订单不存在')
        order = dict(order_row)
        if order['status'] == 'refunded' or order.get('refund_status') == 'succeeded':
            existing = c.execute("SELECT * FROM refund_requests WHERE kind='gear' AND gear_order_id=? ORDER BY created_at DESC LIMIT 1", (order_id,)).fetchone()
            return {'ok': True, 'status': 'refunded', 'idempotent': True, 'refundRequestId': (existing['id'] if existing else None)}
        existing = c.execute("SELECT * FROM refund_requests WHERE kind='gear' AND gear_order_id=? AND status IN ('requested','processing') ORDER BY created_at DESC LIMIT 1", (order_id,)).fetchone()
        if existing:
            return {'ok': True, 'refundRequestId': existing['id'], 'status': existing['status'], 'idempotent': True}
        if order['status'] not in ('paid', 'shipped', 'delivered', 'completed', 'after_sales'):
            raise ValueError(f"当前装备订单状态不能申请退款: {order['status']}")
        checkout = self._checkout_for_result(c, 'gear', order_id)
        refund_id = self._id()
        cash_amount = float(order['cash_paid'] or 0)
        c.execute('''INSERT INTO refund_requests(id,kind,checkout_intent_id,gear_order_id,user_id,club_id,status,reason,
                     cash_amount,original_cash_amount,refund_percent,retained_cash_amount,commerce_order_id,requested_by)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
            refund_id, 'gear', checkout['id'] if checkout else None, order_id, order['user_id'], order['source_club_id'],
            'requested', reason, cash_amount, cash_amount, 100, 0, order.get('commerce_order_id'), requester
        ))
        c.execute("UPDATE gear_orders SET refund_status='requested',refund_request_id=?,after_sales_status='requested' WHERE id=?", (refund_id, order_id))
        if checkout:
            c.execute("UPDATE checkout_intents SET refund_status='requested',updated_at=CURRENT_TIMESTAMP WHERE id=?", (checkout['id'],))
        return {'ok': True, 'refundRequestId': refund_id, 'kind': 'gear', 'status': 'requested', 'cashAmount': cash_amount}

    def approve(self, c, *, refund_id: str, approver: str) -> dict[str, Any]:
        rr_row = c.execute('SELECT * FROM refund_requests WHERE id=?', (refund_id,)).fetchone()
        if not rr_row:
            raise LookupError('退款申请不存在')
        rr = dict(rr_row)
        if rr['status'] == 'processing':
            return {'ok': True, 'refundRequestId': refund_id, 'status': 'processing', 'idempotent': True,
                    'cashAmount': float(rr['cash_amount'] or 0)}
        if rr['status'] == 'succeeded':
            return {'ok': True, 'refundRequestId': refund_id, 'status': 'succeeded', 'idempotent': True}
        if rr['status'] != 'requested':
            raise ValueError(f"当前退款状态不能审核通过: {rr['status']}")
        c.execute("UPDATE refund_requests SET status='processing',approved_by=?,approved_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?", (approver, refund_id))
        if rr['kind'] == 'activity':
            if str(rr.get('refund_scope') or 'full')=='participant' and rr.get('participant_id'):
                c.execute("UPDATE registration_participants SET refund_status='processing',updated_at=CURRENT_TIMESTAMP WHERE id=?",(rr['participant_id'],))
                c.execute("UPDATE registrations SET refund_status='partial_processing' WHERE id=?",(rr['registration_id'],))
            else:
                c.execute("UPDATE registrations SET refund_status='processing' WHERE id=?", (rr['registration_id'],))
        else:
            c.execute("UPDATE gear_orders SET refund_status='processing',after_sales_status='refund_processing' WHERE id=?", (rr['gear_order_id'],))
        if rr.get('checkout_intent_id') and not (rr['kind']=='activity' and str(rr.get('refund_scope') or 'full')=='participant'):
            c.execute("UPDATE checkout_intents SET status='refund_pending',refund_status='processing',updated_at=CURRENT_TIMESTAMP WHERE id=?", (rr['checkout_intent_id'],))
        return {
            'ok': True, 'refundRequestId': refund_id, 'status': 'processing', 'cashAmount': float(rr['cash_amount'] or 0),
            'originalCashAmount': float(rr.get('original_cash_amount') or rr['cash_amount'] or 0),
            'refundPercent': float(rr.get('refund_percent') or 0),
            'retainedCashAmount': float(rr.get('retained_cash_amount') or 0),
            'commerceOrderId': rr.get('commerce_order_id'),
            'nextAction': 'CONFIRM_PROVIDER_REFUND' if float(rr['cash_amount'] or 0) > 0 else 'FINALIZE_NO_CASH_REFUND'
        }

    def reject(self, c, *, refund_id: str, approver: str, note: str = '') -> dict[str, Any]:
        rr_row = c.execute('SELECT * FROM refund_requests WHERE id=?', (refund_id,)).fetchone()
        if not rr_row:
            raise LookupError('退款申请不存在')
        rr = dict(rr_row)
        if rr['status'] == 'rejected':
            return {'ok': True, 'refundRequestId': refund_id, 'status': 'rejected', 'idempotent': True}
        if rr['status'] != 'requested':
            raise ValueError(f"当前退款状态不能拒绝: {rr['status']}")
        c.execute("UPDATE refund_requests SET status='rejected',approved_by=?,decision_note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (approver, note, refund_id))
        if rr['kind'] == 'activity':
            if str(rr.get('refund_scope') or 'full')=='participant' and rr.get('participant_id'):
                c.execute("UPDATE registration_participants SET refund_status='rejected',updated_at=CURRENT_TIMESTAMP WHERE id=?",(rr['participant_id'],))
                refunded=c.execute("SELECT COUNT(*) FROM registration_participants WHERE registration_id=? AND status='refunded'",(rr['registration_id'],)).fetchone()[0]
                c.execute("UPDATE registrations SET refund_status=? WHERE id=?",('partial' if refunded else 'none',rr['registration_id']))
            else:
                c.execute("UPDATE registrations SET refund_status='rejected' WHERE id=?", (rr['registration_id'],))
        else:
            c.execute("UPDATE gear_orders SET refund_status='rejected',after_sales_status='refund_rejected' WHERE id=?", (rr['gear_order_id'],))
        if rr.get('checkout_intent_id') and not (rr['kind']=='activity' and str(rr.get('refund_scope') or 'full')=='participant'):
            c.execute("UPDATE checkout_intents SET status='paid',refund_status='rejected',updated_at=CURRENT_TIMESTAMP WHERE id=?", (rr['checkout_intent_id'],))
        return {'ok': True, 'refundRequestId': refund_id, 'status': 'rejected'}

    def finalize(self, c, *, refund_id: str, provider_refund_id: str | None = None,
                 provider: str = 'local') -> dict[str, Any]:
        rr_row = c.execute('SELECT * FROM refund_requests WHERE id=?', (refund_id,)).fetchone()
        if not rr_row:
            raise LookupError('退款申请不存在')
        rr = dict(rr_row)
        if rr['status'] == 'succeeded':
            return {'ok': True, 'refundRequestId': refund_id, 'status': 'succeeded', 'idempotent': True}
        if rr['status'] not in ('processing', 'requested'):
            raise ValueError(f"当前退款状态不能完成: {rr['status']}")
        if rr['kind'] == 'activity':
            if str(rr.get('refund_scope') or 'full')=='participant' and rr.get('participant_id'):
                result = self._finalize_activity_participant(c, rr)
            else:
                result = self._finalize_activity(c, rr)
        elif rr['kind'] == 'gear':
            if str(rr.get('refund_scope') or 'full')=='after_sales' and rr.get('after_sales_case_id'):
                result = self.gear_refund.refund_gear_after_sales(c, case_id=str(rr['after_sales_case_id']), reason='after_sales_refund')
            else:
                result = self.gear_refund.refund_gear_order(c, order_id=int(rr['gear_order_id']), reason='unified_refund_lifecycle')
        else:
            raise ValueError('unknown refund kind')
        c.execute('''UPDATE refund_requests SET status='succeeded',provider=?,provider_refund_id=?,refunded_at=CURRENT_TIMESTAMP,
                     updated_at=CURRENT_TIMESTAMP WHERE id=?''', (provider, provider_refund_id, refund_id))
        update_checkout=True
        if rr['kind']=='activity' and str(rr.get('refund_scope') or 'full')=='participant':
            update_checkout=bool(result.get('orderFullyRefunded'))
        if rr.get('checkout_intent_id') and update_checkout:
            if rr['kind']=='gear' and str(rr.get('refund_scope') or 'full')=='after_sales' and not bool(result.get('orderFullyRefunded')):
                c.execute("UPDATE checkout_intents SET refund_status='partial',updated_at=CURRENT_TIMESTAMP WHERE id=?", (rr['checkout_intent_id'],))
            else:
                c.execute("UPDATE checkout_intents SET status='refunded',refund_status='succeeded',refunded_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?", (rr['checkout_intent_id'],))
        return {'ok': True, 'refundRequestId': refund_id, 'status': 'succeeded', 'result': result}

    def _finalize_activity_participant(self, c, rr: dict[str, Any]) -> dict[str, Any]:
        reg_row=c.execute('SELECT * FROM registrations WHERE id=?',(rr['registration_id'],)).fetchone()
        p_row=c.execute('SELECT * FROM registration_participants WHERE id=? AND registration_id=?',(rr['participant_id'],rr['registration_id'])).fetchone()
        if not reg_row or not p_row: raise LookupError('报名记录或参加人不存在')
        reg=dict(reg_row); participant=dict(p_row)
        if participant['status']=='refunded':
            return {'registrationId':int(reg['id']),'participantId':int(participant['id']),'status':'refunded','idempotent':True,'orderFullyRefunded':reg['status']=='refunded'}
        if reg['status']!='paid' or participant['status']!='active': raise ValueError('当前订单/参加人状态不能完成部分退款')
        allocation=self.participants.financial_allocation(c,int(participant['id'])) if self.participants else None
        if not allocation: raise ValueError('缺少参加人交易分摊')
        point_result=self.points.reverse_activity_participant(c,registration=reg,participant=participant,allocation=allocation)
        cash=float(rr.get('cash_amount') or 0); retained=float(rr.get('retained_cash_amount') or 0)
        c.execute("UPDATE registration_participants SET status='refunded',refund_status='succeeded',refunded_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",(participant['id'],))
        c.execute("UPDATE participant_financial_allocations SET refund_cash_amount=?,retained_cash_amount=?,refunded_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE participant_id=?",(cash,retained,participant['id']))
        c.execute('DELETE FROM participant_group_assignments WHERE participant_id=?',(participant['id'],))
        if reg.get('occurrence_id'):
            c.execute('UPDATE activity_occurrences SET sold=MAX(sold-1,0) WHERE id=?',(reg['occurrence_id'],))
        c.execute('''UPDATE registrations SET partial_refund_cash_total=partial_refund_cash_total+?,
                     partial_refund_retained_cash_total=partial_refund_retained_cash_total+?,partial_refund_count=partial_refund_count+1 WHERE id=?''',(cash,retained,reg['id']))
        active=int(c.execute("SELECT COUNT(*) FROM registration_participants WHERE registration_id=? AND status='active'",(reg['id'],)).fetchone()[0])
        vouchers_restored=0; fully=active==0
        if fully:
            if self.benefits: vouchers_restored=self.benefits.restore_order_vouchers(c,order_kind='activity',order_id=int(reg['id']))
            totals=c.execute('SELECT COALESCE(SUM(refund_cash_amount),0),COALESCE(SUM(retained_cash_amount),0) FROM participant_financial_allocations WHERE registration_id=?',(reg['id'],)).fetchone()
            c.execute("UPDATE registrations SET status='refunded',refund_status='succeeded',refund_cash_amount=?,retained_cash_amount=?,refunded_at=CURRENT_TIMESTAMP WHERE id=?",(float(totals[0] or 0),float(totals[1] or 0),reg['id']))
        else:
            c.execute("UPDATE registrations SET refund_status='partial' WHERE id=?",(reg['id'],))
        if self.membership:
            self.membership.refresh_member(c,club_id=int(reg['club_id']),user_id=int(reg['user_id']))
        return {'registrationId':int(reg['id']),'participantId':int(participant['id']),'participantName':participant.get('name'),'status':'refunded',
                'cashRefundAmount':cash,'refundPercent':float(rr.get('refund_percent') or 0),'retainedCashAmount':retained,
                'points':point_result,'benefitAllocation':{'club':float(allocation.get('club_benefit_discount') or 0),'platform':float(allocation.get('platform_benefit_subsidy') or 0)},
                'voucherPolicy':'RESTORE_ONLY_WHEN_ALL_PARTICIPANTS_REFUNDED','benefitVouchersRestored':vouchers_restored,
                'remainingParticipants':active,'orderFullyRefunded':fully}

    def _finalize_activity(self, c, rr: dict[str, Any]) -> dict[str, Any]:
        reg_row = c.execute('SELECT * FROM registrations WHERE id=?', (rr['registration_id'],)).fetchone()
        if not reg_row:
            raise LookupError('报名记录不存在')
        reg = dict(reg_row)
        if reg['status'] == 'refunded':
            return {'registrationId': reg['id'], 'status': 'refunded', 'idempotent': True}
        if reg['status'] != 'paid':
            raise ValueError(f"当前报名状态不能完成退款: {reg['status']}")
        # v0.11: cancellation fee only applies to cash. Non-cash assets are fully restored.
        self.points.reverse_activity_registration(c, registration=reg)
        restored = self.benefits.restore_order_vouchers(c, order_kind='activity', order_id=int(reg['id'])) if self.benefits else 0
        c.execute("UPDATE registration_participants SET status='refunded',refund_status='succeeded',refunded_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE registration_id=? AND status='active'",(reg['id'],))
        pct=max(0.0,min(100.0,float(rr.get('refund_percent') or 0)))
        for ar in c.execute('SELECT * FROM participant_financial_allocations WHERE registration_id=? AND refunded_at IS NULL ORDER BY participant_id',(reg['id'],)).fetchall():
            ar=dict(ar); rc=round(float(ar.get('cash_paid') or 0)*pct/100.0,2); rt=round(float(ar.get('cash_paid') or 0)-rc,2)
            c.execute('UPDATE participant_financial_allocations SET refund_cash_amount=?,retained_cash_amount=?,refunded_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?',(rc,rt,ar['id']))
        c.execute('DELETE FROM participant_group_assignments WHERE participant_id IN (SELECT id FROM registration_participants WHERE registration_id=?)',(reg['id'],))
        c.execute('''UPDATE registrations SET status='refunded',refund_status='succeeded',
                     refund_cash_amount=?,refund_percent=?,retained_cash_amount=?,
                     refund_policy_snapshot_json=COALESCE(refund_policy_snapshot_json,?),refunded_at=CURRENT_TIMESTAMP WHERE id=?''',
                  (float(rr.get('cash_amount') or 0), float(rr.get('refund_percent') or 0),
                   float(rr.get('retained_cash_amount') or 0), rr.get('policy_snapshot_json'), reg['id']))
        if reg.get('occurrence_id'):
            c.execute('UPDATE activity_occurrences SET sold=MAX(sold-?,0) WHERE id=?', (max(1,int(reg.get('participant_count') or 1)), reg['occurrence_id']))
        if self.membership:
            self.membership.refresh_member(c, club_id=int(reg['club_id']), user_id=int(reg['user_id']))
        return {
            'registrationId': int(reg['id']), 'status': 'refunded', 'benefitVouchersRestored': restored,
            'cashRefundAmount': float(rr.get('cash_amount') or 0),
            'refundPercent': float(rr.get('refund_percent') or 0),
            'retainedCashAmount': float(rr.get('retained_cash_amount') or 0),
        }

    def list_for_club(self, c, club_id: int):
        return [dict(x) for x in c.execute('SELECT * FROM refund_requests WHERE club_id=? ORDER BY created_at DESC', (club_id,)).fetchall()]

    def list_all(self, c):
        return [dict(x) for x in c.execute('SELECT * FROM refund_requests ORDER BY created_at DESC').fetchall()]
