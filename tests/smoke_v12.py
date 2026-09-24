from pathlib import Path
from datetime import datetime, timedelta
import os, sys, uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v12_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

h=ok('get','/api/health').json(); assert h['version'].startswith(('0.12','0.13','0.14','0.15','0.16','0.17','0.18','0.19','0.20','0.21','0.22','0.23','0.24','0.25'))
activity=ok('get','/api/public/clubs/1/activities').json()[0]
aid=activity['id']

# Club configures participant form policy. Pay now / supplement later is allowed.
pp=ok('patch',f'/api/club/1/activities/{aid}/participant-policy',json={
    'maxParticipantsPerOrder':6,
    'allowIncompleteAtCheckout':True,
    'insuranceRequired':True,
    'allowParticipantReplacement':True,
    'replacementCutoffHours':24
}).json()
assert pp['maxParticipantsPerOrder']==6 and pp['insuranceRequired'] is True

# Create a 5-seat occurrence at ¥200/person, enough time for self-service transfer.
start=(datetime.now()+timedelta(days=5)).replace(microsecond=0).isoformat(sep=' ')
oid=ok('post',f'/api/club/1/activities/{aid}/occurrences',json={'startAt':start,'price':200,'capacity':5,'label':'v0.12多人报名团'}).json()['id']
ok('post',f'/api/club/1/activities/{aid}/publish')

participants=[
    {'name':'林野','phone':'13800000001','relationToPayer':'本人'},
    {'name':'小林','phone':'13800000002','relationToPayer':'家人'},
    {'name':'朋友A','phone':'13800000003','relationToPayer':'朋友'},
]
# Quote is now per participant, not per payer/order.
quote=ok('get',f'/api/public/activities/{aid}/price-quote?occurrence_id={oid}&user_id=1&participant_count=3').json()
assert quote['participantCount']==3
assert float(quote['original'])==600.0, quote

co=ok('post',f'/api/public/activities/{aid}/checkout',json={
    'name':'林野','phone':'13800000001','occurrenceId':oid,
    'clubPoints':0,'gearPoints':0,'participants':participants
}).json()
assert co['participantCount']==3 and float(co['original'])==600.0
paid=ok('post',f"/api/public/checkouts/{co['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()
result=paid['result']; rid=result['registrationId']
assert result['participantCount']==3 and len(result['participantIds'])==3

# One order occupies three seats.
with app.conn() as dbc:
    reg=app.row(dbc.execute('SELECT * FROM registrations WHERE id=?',(rid,)))
    occ=app.row(dbc.execute('SELECT * FROM activity_occurrences WHERE id=?',(oid,)))
assert int(reg['participant_count'])==3
assert int(occ['sold'])==3

ps=ok('get',f'/api/public/registrations/{rid}/participants').json()
assert ps['participantCount']==3 and len(ps['participants'])==3
assert ps['completeCount']==0  # IDs/emergency contacts intentionally deferred.

# Supplement one attendee's required pre-departure data.
pid=ps['participants'][1]['id']
upd=ok('patch',f'/api/public/registrations/{rid}/participants/{pid}',json={
    'idType':'身份证','idNumber':'510100199001010001',
    'emergencyContactName':'林先生','emergencyContactPhone':'13900000000'
}).json()
assert upd['form_status']=='complete'

# Direct identity edits through supplement endpoint are forbidden; replacement uses an explicit audit path.
bad=c.patch(f'/api/public/registrations/{rid}/participants/{pid}',json={'name':'偷偷改名'})
assert bad.status_code==400
repl=ok('post',f'/api/public/registrations/{rid}/participants/{pid}/replace',json={
    'name':'朋友B','phone':'13800000004','relationToPayer':'朋友','reason':'原同行人临时有事'
}).json()
assert repl['name']=='朋友B' and int(repl['replacement_count'])==1 and repl['insurance_status']=='pending'

# Club can handle insurance without changing payer/order ownership.
ins=ok('patch',f"/api/club/1/participants/{pid}/insurance",json={
    'status':'insured','provider':'示例户外险','policyNo':'POL-V12-001'
}).json()
assert ins['insurance_status']=='insured' and ins['insurance_policy_no']=='POL-V12-001'

# Club list is participant-aware and occurrence manifest is directly usable for leader/insurance workflows.
regs=ok('get','/api/club/1/registrations').json()
rr=next(x for x in regs if x['id']==rid)
assert int(rr['active_participants'])==3
manifest=ok('get',f'/api/club/1/occurrences/{oid}/participants').json()
assert len(manifest)==3

# Capacity is participant-based: only two seats remain, so another 3-person checkout must fail.
over=c.post(f'/api/public/activities/{aid}/checkout',json={
    'name':'第二付款人','phone':'13800000009','occurrenceId':oid,
    'participants':[{'name':'甲','phone':'13800000101'},{'name':'乙','phone':'13800000102'},{'name':'丙','phone':'13800000103'}]
})
assert over.status_code==409, over.text

# Unified order center carries attendee details and completion status.
center=ok('get',f"/api/public/users/{result.get('userId',1) or 1}/order-center?club_id=1").json()
# payer user may be seed user 1; locate order directly from database ownership for deterministic test.
with app.conn() as dbc:
    uid=int(app.row(dbc.execute('SELECT user_id FROM registrations WHERE id=?',(rid,)))['user_id'])
center=ok('get',f'/api/public/users/{uid}/order-center?club_id=1').json()
order=next(x for x in center['activityOrders'] if x['id']==rid)
assert order['participantCount']==3 and len(order['participants'])==3

# Full refund releases all three participant seats, not just one order slot.
# Make policy fully refundable for this test.
ok('patch',f'/api/club/1/activities/{aid}/refund-policy',json={'enabled':True,'rules':[{'minHoursBefore':0,'cashRefundPercent':100,'label':'测试全退'}],'afterStartCashRefundPercent':0})
ref=ok('post',f'/api/public/registrations/{rid}/refund-request',json={'reason':'整单取消'}).json()
ok('post',f"/api/club/1/refunds/{ref['refundRequestId']}/approve")
ok('post','/api/commerce/events/refund-succeeded',json={
    'eventKey':'v12-'+uuid.uuid4().hex,'refundRequestId':ref['refundRequestId'],'provider':'local','providerRefundId':'refund_v12'
})
with app.conn() as dbc:
    occ2=app.row(dbc.execute('SELECT * FROM activity_occurrences WHERE id=?',(oid,)))
assert int(occ2['sold'])==0

# Backward compatibility: no participants array => payer becomes one attendee and price remains one seat.
start2=(datetime.now()+timedelta(days=7)).replace(microsecond=0).isoformat(sep=' ')
oid2=ok('post',f'/api/club/1/activities/{aid}/occurrences',json={'startAt':start2,'price':180,'capacity':3,'label':'单人兼容团'}).json()['id']
co2=ok('post',f'/api/public/activities/{aid}/checkout',json={'name':'单人用户','phone':'13800000088','occurrenceId':oid2}).json()
assert co2['participantCount']==1 and float(co2['original'])==180.0
paid2=ok('post',f"/api/public/checkouts/{co2['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()['result']
ps2=ok('get',f"/api/public/registrations/{paid2['registrationId']}/participants").json()
assert ps2['participantCount']==1 and ps2['participants'][0]['name']=='单人用户'

print('SMOKE V0.12 OK · PAYER≠PARTICIPANT / MULTI-PERSON PRICING+CAPACITY / SUPPLEMENT / TRANSFER / INSURANCE / REFUND SEAT RELEASE')
