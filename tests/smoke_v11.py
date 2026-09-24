from pathlib import Path
from datetime import datetime, timedelta
import os, sys, uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v11_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

h=ok('get','/api/health').json(); assert h['version'].startswith(('0.11','0.12','0.13','0.14','0.15','0.16','0.17','0.18','0.19','0.20','0.21','0.22','0.23','0.24','0.25'))
activity=ok('get','/api/public/clubs/1/activities').json()[0]
aid=activity['id']

# Configure a 3-band activity refund policy.
policy=ok('patch',f'/api/club/1/activities/{aid}/refund-policy',json={
    'enabled':True,
    'rules':[
        {'minHoursBefore':72,'cashRefundPercent':100,'label':'出发前72小时及以上'},
        {'minHoursBefore':24,'cashRefundPercent':80,'label':'出发前24-72小时'},
        {'minHoursBefore':0,'cashRefundPercent':50,'label':'出发前24小时以内'},
    ],
    'afterStartCashRefundPercent':0,
    'note':'测试退款规则'
}).json()
assert policy['enabled'] is True and len(policy['rules'])==3

# Create a near-term occurrence so the 0-24h / 50% band matches.
start=(datetime.now()+timedelta(hours=12)).replace(microsecond=0).isoformat(sep=' ')
oid=ok('post',f'/api/club/1/activities/{aid}/occurrences',json={'startAt':start,'price':400,'capacity':5,'label':'v0.11退款测试团'}).json()['id']
# Ensure activity published (seed may already be published).
ok('post',f'/api/club/1/activities/{aid}/publish')

co=ok('post',f'/api/public/activities/{aid}/checkout',json={
    'name':'林野','phone':'13800000001','occurrenceId':oid,'clubPoints':100,'gearPoints':100
}).json()
paid=ok('post',f"/api/public/checkouts/{co['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()
rid=paid['result']['registrationId']

quote=ok('get',f'/api/public/registrations/{rid}/refund-quote').json()
assert quote['eligible'] is True
assert quote['cashRefundPercent']==50.0, quote

rr=ok('post',f'/api/public/registrations/{rid}/refund-request',json={'reason':'临时行程冲突'}).json()
assert rr['refundPercent']==50.0
assert abs(rr['cashAmount'] - rr['originalCashAmount']*0.5) < 0.01
refund_id=rr['refundRequestId']

# Editing the activity policy after request must not mutate the already-created refund request.
ok('patch',f'/api/club/1/activities/{aid}/refund-policy',json={
    'enabled':True,
    'rules':[{'minHoursBefore':0,'cashRefundPercent':10,'label':'新规则仅退10%'}],
    'afterStartCashRefundPercent':0,
    'note':'申请之后修改'
})
with app.conn() as dbc:
    snap=app.row(dbc.execute('SELECT * FROM refund_requests WHERE id=?',(refund_id,)))
assert float(snap['refund_percent'])==50.0

# Club approval returns the frozen 50% refund amount; provider callback finalizes domain reversals.
ap=ok('post',f'/api/club/1/refunds/{refund_id}/approve').json()
assert ap['status']=='processing' and ap['refundPercent']==50.0
rf=ok('post','/api/commerce/events/refund-succeeded',json={
    'eventKey':'v11-'+uuid.uuid4().hex,'refundRequestId':refund_id,'provider':'local','providerRefundId':'refund_v11_partial'
}).json()
assert rf['result']['result']['refundPercent']==50.0
with app.conn() as dbc:
    reg=app.row(dbc.execute('SELECT * FROM registrations WHERE id=?',(rid,)))
assert reg['status']=='refunded'
assert float(reg['refund_percent'])==50.0
assert float(reg['retained_cash_amount']) > 0

# Disable refund on a new registration: user must not be allowed to self-request.
ok('patch',f'/api/club/1/activities/{aid}/refund-policy',json={
    'enabled':False,
    'rules':[{'minHoursBefore':0,'cashRefundPercent':100,'label':'unused'}],
    'afterStartCashRefundPercent':0,
    'note':'特殊活动不可退'
})
start2=(datetime.now()+timedelta(days=10)).replace(microsecond=0).isoformat(sep=' ')
oid2=ok('post',f'/api/club/1/activities/{aid}/occurrences',json={'startAt':start2,'price':300,'capacity':5,'label':'不可退测试团'}).json()['id']
co2=ok('post',f'/api/public/activities/{aid}/checkout',json={'name':'林野','phone':'13800000001','occurrenceId':oid2,'clubPoints':0,'gearPoints':0}).json()
paid2=ok('post',f"/api/public/checkouts/{co2['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()
rid2=paid2['result']['registrationId']
r=c.post(f'/api/public/registrations/{rid2}/refund-request',json={'reason':'测试不可退'})
assert r.status_code==409, r.text

# Create one Gear order and verify unified C-end order center shows both business lines.
prod=ok('get','/api/platform/products').json()[0]
gco=ok('post','/api/public/clubs/1/gear-checkout',json={'userId':1,'items':[{'productId':prod['id'],'quantity':1}],'gearPoints':0}).json()
gpaid=ok('post',f"/api/public/checkouts/{gco['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()
order_id=gpaid['result']['orderId']
center=ok('get','/api/public/users/1/order-center?club_id=1').json()
assert any(x['id']==rid for x in center['activityOrders'])
assert any(x['id']==order_id for x in center['gearOrders'])
assert center['allOrders']
# Refunded activity exposes progress and frozen refund detail in the order center.
ro=next(x for x in center['activityOrders'] if x['id']==rid)
assert ro['refundProgress']['step']=='succeeded'
assert float(ro['request_refund_percent'])==50.0

print('SMOKE V0.11 OK · ACTIVITY REFUND POLICY / PARTIAL CASH REFUND / POLICY SNAPSHOT / NO-REFUND / UNIFIED ORDER CENTER')
