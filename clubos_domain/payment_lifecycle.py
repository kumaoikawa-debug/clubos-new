from __future__ import annotations
import json, uuid
from typing import Any


class PaymentLifecycleEngine:
    """Provider-neutral payment attempt state machine.

    v0.15 adds merchant-order/account/channel tracking so callbacks from WeChat Pay or
    Alipay can be mapped back to the exact ClubOS checkout without exposing ClubOS's
    longer internal checkout id to provider length constraints.
    """

    def _id(self) -> str:
        return "pay_" + uuid.uuid4().hex

    def start(self, c, *, checkout_id: str, provider: str, provider_payment_id: str | None = None,
              payment_account_id: int | None = None, channel: str | None = None,
              merchant_order_no: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        intent = c.execute('SELECT * FROM checkout_intents WHERE id=?', (checkout_id,)).fetchone()
        if not intent:
            raise LookupError('结算单不存在')
        intent = dict(intent)
        if intent['status'] == 'paid':
            return {'ok': True, 'checkoutId': checkout_id, 'status': 'paid', 'idempotent': True}
        if intent['status'] in ('cancelled', 'expired', 'refunded'):
            raise ValueError(f"当前结算状态不能支付: {intent['status']}")
        attempt_id = self._id()
        c.execute('''INSERT INTO payment_attempts(
            id,checkout_intent_id,provider,payment_account_id,channel,merchant_order_no,
            provider_payment_id,status,amount,metadata_json)
            VALUES(?,?,?,?,?,?,?,?,?,?)''', (
            attempt_id, checkout_id, provider, payment_account_id, channel, merchant_order_no,
            provider_payment_id, 'processing', float(intent['cash_amount'] or 0),
            json.dumps(metadata or {}, ensure_ascii=False)
        ))
        c.execute('''UPDATE checkout_intents SET payment_status='processing',updated_at=CURRENT_TIMESTAMP
                     WHERE id=?''', (checkout_id,))
        return {
            'ok': True, 'paymentAttemptId': attempt_id, 'checkoutId': checkout_id,
            'provider': provider, 'paymentAccountId': payment_account_id, 'channel': channel,
            'merchantOrderNo': merchant_order_no, 'amount': float(intent['cash_amount'] or 0), 'status': 'processing'
        }

    def attach_provider_result(self, c, *, payment_attempt_id: str, provider_payment_id: str | None,
                               action: dict[str, Any] | None = None, raw: dict[str, Any] | None = None):
        c.execute('''UPDATE payment_attempts SET provider_payment_id=COALESCE(?,provider_payment_id),
                     payment_action_json=?,provider_payload_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                  (provider_payment_id, json.dumps(action or {}, ensure_ascii=False),
                   json.dumps(raw or {}, ensure_ascii=False), payment_attempt_id))

    def succeed(self, c, *, checkout_id: str, provider: str, provider_payment_id: str | None = None,
                payment_attempt_id: str | None = None, raw: dict[str, Any] | None = None,
                merchant_order_no: str | None = None, payment_account_id: int | None = None):
        if payment_attempt_id:
            c.execute('''UPDATE payment_attempts SET status='succeeded',provider_payment_id=COALESCE(?,provider_payment_id),
                         provider_payload_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                      (provider_payment_id, json.dumps(raw or {}, ensure_ascii=False), payment_attempt_id))
        else:
            attempt_id = self._id()
            intent = c.execute('SELECT cash_amount FROM checkout_intents WHERE id=?', (checkout_id,)).fetchone()
            if not intent:
                raise LookupError('结算单不存在')
            c.execute('''INSERT INTO payment_attempts(id,checkout_intent_id,provider,payment_account_id,merchant_order_no,
                         provider_payment_id,status,amount,provider_payload_json)
                         VALUES(?,?,?,?,?,?,?,?,?)''', (
                attempt_id, checkout_id, provider, payment_account_id, merchant_order_no,
                provider_payment_id, 'succeeded', float(intent[0] or 0), json.dumps(raw or {}, ensure_ascii=False)
            ))
        c.execute('''UPDATE checkout_intents SET payment_status='succeeded',paid_at=COALESCE(paid_at,CURRENT_TIMESTAMP),
                     updated_at=CURRENT_TIMESTAMP WHERE id=?''', (checkout_id,))

    def fail(self, c, *, checkout_id: str, provider: str, reason: str = '',
             provider_payment_id: str | None = None, payment_attempt_id: str | None = None,
             raw: dict[str, Any] | None = None) -> dict[str, Any]:
        if payment_attempt_id:
            c.execute('''UPDATE payment_attempts SET status='failed',provider_payment_id=COALESCE(?,provider_payment_id),
                         failure_reason=?,provider_payload_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                      (provider_payment_id, reason, json.dumps(raw or {}, ensure_ascii=False), payment_attempt_id))
        else:
            aid = self._id()
            intent = c.execute('SELECT cash_amount FROM checkout_intents WHERE id=?', (checkout_id,)).fetchone()
            if not intent:
                raise LookupError('结算单不存在')
            c.execute('''INSERT INTO payment_attempts(id,checkout_intent_id,provider,provider_payment_id,status,amount,
                         failure_reason,provider_payload_json) VALUES(?,?,?,?,?,?,?,?)''',
                      (aid, checkout_id, provider, provider_payment_id, 'failed', float(intent[0] or 0), reason,
                       json.dumps(raw or {}, ensure_ascii=False)))
        c.execute('''UPDATE checkout_intents SET payment_status='failed',updated_at=CURRENT_TIMESTAMP
                     WHERE id=? AND status='pending_payment' ''', (checkout_id,))
        return {'ok': True, 'checkoutId': checkout_id, 'paymentStatus': 'failed', 'retryable': True, 'reason': reason}

    def by_merchant_order(self, c, merchant_order_no: str):
        r = c.execute('''SELECT * FROM payment_attempts WHERE merchant_order_no=? ORDER BY created_at DESC,id DESC LIMIT 1''',
                      (merchant_order_no,)).fetchone()
        return dict(r) if r else None

    def succeeded_for_checkout(self, c, checkout_id: str):
        r = c.execute("SELECT * FROM payment_attempts WHERE checkout_intent_id=? AND status='succeeded' ORDER BY updated_at DESC,id DESC LIMIT 1",
                      (checkout_id,)).fetchone()
        return dict(r) if r else None

    def attempts(self, c, checkout_id: str):
        out=[]
        for x in c.execute('SELECT * FROM payment_attempts WHERE checkout_intent_id=? ORDER BY created_at DESC,id DESC', (checkout_id,)).fetchall():
            d=dict(x)
            d['paymentAction']=json.loads(d.get('payment_action_json') or '{}')
            out.append(d)
        return out
