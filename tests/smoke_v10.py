from pathlib import Path
import os, sys, uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v10_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

h=ok('get','/api/health').json(); assert h['version'].startswith(('0.10','0.11','0.12','0.13','0.14','0.15','0.16','0.17','0.18','0.19','0.20','0.21','0.22','0.23','0.24','0.25'))
activity=ok('get','/api/public/clubs/1/activities').json()[0]
detail=ok('get',f"/api/public/activities/{activity['id']}").json(); occ=detail['occurrences'][0]

# PAYMENT: checkout -> processing -> failed -> retry -> paid.
wallet0=ok('get','/api/public/users/1/wallet?club_id=1').json()
co=ok('post',f"/api/public/activities/{activity['id']}/checkout",json={
    'name':'林野','phone':'13800000001','occurrenceId':occ['id'],'clubPoints':100,'gearPoints':100
}).json()
checkout_id=co['checkoutId']
pay1=ok('post',f'/api/public/checkouts/{checkout_id}/pay',json={'provider':'local'}).json()
assert pay1['paymentStatus']=='processing'
chk=ok('get',f'/api/public/checkouts/{checkout_id}').json(); assert chk['payment_status']=='processing'
fail_event='payfail-'+uuid.uuid4().hex
ok('post','/api/commerce/events/payment-failed',json={
    'eventKey':fail_event,'checkoutId':checkout_id,'provider':'local','paymentAttemptId':pay1['paymentAttemptId'],'reason':'用户取消支付'
})
chk=ok('get',f'/api/public/checkouts/{checkout_id}').json(); assert chk['status']=='pending_payment' and chk['payment_status']=='failed'
assert any(x['status']=='failed' for x in chk['paymentAttempts'])
# Failed attempt keeps the points held so retry uses the same frozen quote.
assert all(x['status']=='held' for x in chk['holds'])

pay2=ok('post',f'/api/public/checkouts/{checkout_id}/pay',json={'provider':'local','simulateSuccess':True}).json()
assert pay2['paymentStatus']=='succeeded' and pay2['result']['kind']=='activity'
rid=pay2['result']['registrationId']
chk=ok('get',f'/api/public/checkouts/{checkout_id}').json(); assert chk['status']=='paid' and chk['payment_status']=='succeeded' and chk['paid_at']
with app.conn() as dbc:
    reg=app.row(dbc.execute('SELECT * FROM registrations WHERE id=?',(rid,)))
assert reg['status']=='paid' and reg['payment_status']=='succeeded' and reg['refund_status']=='none'

# ACTIVITY REFUND: request -> club approve -> provider callback -> domain reversal.
rr=ok('post',f'/api/public/registrations/{rid}/refund-request',json={'reason':'临时有事'}).json()
assert rr['status']=='requested'
refund_id=rr['refundRequestId']
with app.conn() as dbc:
    reg=app.row(dbc.execute('SELECT * FROM registrations WHERE id=?',(rid,)))
assert reg['refund_status']=='requested'
ap=ok('post',f'/api/club/1/refunds/{refund_id}/approve').json(); assert ap['status']=='processing'
chk=ok('get',f'/api/public/checkouts/{checkout_id}').json(); assert chk['status']=='refund_pending' and chk['refund_status']=='processing'
ev='refund-act-'+uuid.uuid4().hex
rf=ok('post','/api/commerce/events/refund-succeeded',json={
    'eventKey':ev,'refundRequestId':refund_id,'provider':'local','providerRefundId':'pr_activity_1'
}).json()
assert rf['result']['status']=='succeeded'
# duplicate provider callback is idempotent
idem=ok('post','/api/commerce/events/refund-succeeded',json={
    'eventKey':ev,'refundRequestId':refund_id,'provider':'local','providerRefundId':'pr_activity_1'
}).json(); assert idem['idempotent'] is True
with app.conn() as dbc:
    reg=app.row(dbc.execute('SELECT * FROM registrations WHERE id=?',(rid,)))
assert reg['status']=='refunded' and reg['refund_status']=='succeeded' and reg['refunded_at']
chk=ok('get',f'/api/public/checkouts/{checkout_id}').json(); assert chk['status']=='refunded' and chk['refund_status']=='succeeded' and chk['refunded_at']

# Rejection leaves the paid registration active.
co_rej=ok('post',f"/api/public/activities/{activity['id']}/checkout",json={
    'name':'林野','phone':'13800000001','occurrenceId':occ['id'],'clubPoints':0,'gearPoints':0
}).json()
p_rej=ok('post',f"/api/public/checkouts/{co_rej['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()
rid_rej=p_rej['result']['registrationId']
rr_rej=ok('post',f'/api/public/registrations/{rid_rej}/refund-request',json={'reason':'测试拒绝'}).json()
rej=ok('post',f"/api/club/1/refunds/{rr_rej['refundRequestId']}/reject",json={'note':'不符合退款条件'}).json(); assert rej['status']=='rejected'
with app.conn() as dbc:
    reg_rej=app.row(dbc.execute('SELECT * FROM registrations WHERE id=?',(rid_rej,)))
assert reg_rej['status']=='paid' and reg_rej['refund_status']=='rejected'

# GEAR REFUND uses the same lifecycle, but platform approves it.
prod=ok('get','/api/platform/products').json()[0]
gco=ok('post','/api/public/clubs/1/gear-checkout',json={'userId':1,'items':[{'productId':prod['id'],'quantity':1}],'gearPoints':100}).json()
gpay=ok('post',f"/api/public/checkouts/{gco['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()
order_id=gpay['result']['orderId']
grr=ok('post',f'/api/public/orders/{order_id}/refund-request',json={'reason':'尺码不合适'}).json(); assert grr['status']=='requested'
gap=ok('post',f"/api/platform/refunds/{grr['refundRequestId']}/approve").json(); assert gap['status']=='processing'
gev='refund-gear-'+uuid.uuid4().hex
grf=ok('post','/api/commerce/events/refund-succeeded',json={
    'eventKey':gev,'refundRequestId':grr['refundRequestId'],'provider':'local','providerRefundId':'pr_gear_1'
}).json()
assert grf['result']['status']=='succeeded'
with app.conn() as dbc:
    go=app.row(dbc.execute('SELECT * FROM gear_orders WHERE id=?',(order_id,)))
assert go['status']=='refunded' and go['refund_status']=='succeeded'
gchk=ok('get',f"/api/public/checkouts/{gco['checkoutId']}").json(); assert gchk['status']=='refunded' and gchk['refund_status']=='succeeded'

print('SMOKE V0.10 OK · PAYMENT ATTEMPTS / RETRY / ACTIVITY REFUND / GEAR REFUND / PROVIDER CALLBACKS')
