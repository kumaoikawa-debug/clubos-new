from pathlib import Path
from datetime import datetime, timedelta
import os, sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v14_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

h=ok('get','/api/health').json(); assert h['version'].startswith(('0.14','0.15','0.16','0.17','0.18','0.19','0.20','0.21','0.22','0.23','0.24','0.25'))
aid=ok('get','/api/public/clubs/1/activities').json()[0]['id']
start=(datetime.now()+timedelta(days=7)).replace(microsecond=0).isoformat(sep=' ')
oid=ok('post',f'/api/club/1/activities/{aid}/occurrences',json={'startAt':start,'price':300,'capacity':6,'label':'v0.14多人部分退款团'}).json()['id']
ok('post',f'/api/club/1/activities/{aid}/publish')
# cash full refund before start to make participant allocation assertions deterministic.
ok('patch',f'/api/club/1/activities/{aid}/refund-policy',json={'enabled':True,'rules':[{'minHoursBefore':0,'cashRefundPercent':100,'label':'出发前全退'}],'afterStartCashRefundPercent':0})

# Create and redeem a club-funded activity coupon so benefit allocation participates in the split.
bid=ok('post','/api/club/1/benefits',json={'title':'v14活动30元券','description':'部分退款分摊测试','pointsType':'club','pointsCost':100,'benefitType':'activity_coupon','cashValue':30,'stock':20}).json()['benefitId']
red=ok('post',f'/api/public/clubs/1/benefits/{bid}/redeem',json={'userId':1}).json()
voucher=red['voucherCode']

participants=[
 {'name':'林野','phone':'13800000001','relationToPayer':'本人','idType':'身份证','idNumber':'510100199001010005'},
 {'name':'同行乙','phone':'13814000002','relationToPayer':'朋友','idType':'身份证','idNumber':'510100199001010013'},
 {'name':'同行丙','phone':'13814000003','relationToPayer':'朋友','idType':'身份证','idNumber':'510100199001010312'},
]
co=ok('post',f'/api/public/activities/{aid}/checkout',json={
    'name':'林野','phone':'13800000001','occurrenceId':oid,'participants':participants,
    'clubPoints':300,'gearPoints':300,'voucherCodes':[voucher]
}).json()
assert co['participantCount']==3
paid=ok('post',f"/api/public/checkouts/{co['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()['result']
rid=paid['registrationId']
assert paid['participantCount']==3
ps=ok('get',f'/api/public/registrations/{rid}/participants').json()['participants']
assert len(ps)==3
# deterministic equal allocation: 300 gross, 288 cash, 100 club pts, 100 gear pts, 10 club benefit per person.
for p in ps:
    assert abs(float(p['allocated_original_amount'])-300)<0.01
    assert abs(float(p['allocated_cash_paid'])-288)<0.01
    assert int(p['allocated_club_points_used'])==100
    assert int(p['allocated_gear_points_used'])==100
    assert abs(float(p['allocated_club_benefit_discount'])-10)<0.01
    assert int(p['allocated_club_points_earned'])==288

with app.conn() as dbc:
    sold0=dbc.execute('SELECT sold FROM activity_occurrences WHERE id=?',(oid,)).fetchone()[0]
assert sold0==3

# Refund the first participant only.
p1=ps[0]
quote=ok('get',f"/api/public/registrations/{rid}/participants/{p1['id']}/refund-quote").json()
assert quote['eligible'] and abs(float(quote['cashRefundAmount'])-288)<0.01
req=ok('post',f"/api/public/registrations/{rid}/participants/{p1['id']}/refund-request",json={'reason':'林野临时退出'}).json()
assert req['refundScope']=='participant' and req['pointsToRestore']=={'club':100,'gear':100}
ap=ok('post',f"/api/club/1/refunds/{req['refundRequestId']}/approve").json()
assert ap['status']=='processing'
ok('post','/api/commerce/events/refund-succeeded',json={'eventKey':'v14-p1','refundRequestId':req['refundRequestId'],'provider':'local','providerRefundId':'pr_1'})

state=ok('get',f'/api/public/registrations/{rid}/participants').json()
allp=state['participants']; p1a=next(x for x in allp if x['id']==p1['id'])
assert p1a['status']=='refunded' and p1a['refund_status']=='succeeded'
assert state['participantCount']==2 and state['refundedParticipantCount']==1
with app.conn() as dbc:
    reg=dict(dbc.execute('SELECT * FROM registrations WHERE id=?',(rid,)).fetchone())
    sold=dbc.execute('SELECT sold FROM activity_occurrences WHERE id=?',(oid,)).fetchone()[0]
    vstatus=dbc.execute('SELECT status FROM benefit_redemptions WHERE voucher_code=?',(voucher,)).fetchone()[0]
assert reg['status']=='paid' and reg['refund_status']=='partial' and sold==2
assert vstatus=='used'  # order-level voucher stays consumed while anyone remains.

# After one participant refund, full-order refund is intentionally blocked; remaining participants refund individually.
bad=c.post(f'/api/public/registrations/{rid}/refund-request',json={'reason':'尝试整单退'})
assert bad.status_code==409

# Refund the remaining participants. Last participant restores the original order-level voucher and closes the order.
for idx,p in enumerate(allp[1:],2):
    reqx=ok('post',f"/api/public/registrations/{rid}/participants/{p['id']}/refund-request",json={'reason':f'同行{idx}退出'}).json()
    ok('post',f"/api/club/1/refunds/{reqx['refundRequestId']}/approve")
    out=ok('post','/api/commerce/events/refund-succeeded',json={'eventKey':f'v14-p{idx}','refundRequestId':reqx['refundRequestId'],'provider':'local','providerRefundId':f'pr_{idx}'}).json()

with app.conn() as dbc:
    reg=dict(dbc.execute('SELECT * FROM registrations WHERE id=?',(rid,)).fetchone())
    sold=dbc.execute('SELECT sold FROM activity_occurrences WHERE id=?',(oid,)).fetchone()[0]
    vstatus=dbc.execute('SELECT status FROM benefit_redemptions WHERE voucher_code=?',(voucher,)).fetchone()[0]
    cp=dbc.execute('SELECT club_points_balance FROM club_members WHERE club_id=1 AND user_id=1').fetchone()[0]
    gp=dbc.execute('SELECT balance FROM gear_point_accounts WHERE user_id=1').fetchone()[0]
    allocations=[dict(x) for x in dbc.execute('SELECT * FROM participant_financial_allocations WHERE registration_id=? ORDER BY participant_id',(rid,)).fetchall()]
assert reg['status']=='refunded' and reg['refund_status']=='succeeded'
assert reg['partial_refund_count']==3 and abs(float(reg['partial_refund_cash_total'])-864)<0.01
assert sold==0 and vstatus=='issued'
assert cp==760  # 860 initial -100 coupon; all checkout points restored and all earned points reversed.
assert gp==1280
assert all(x['refunded_at'] for x in allocations)

# Order center keeps the participant refund history visible.
oc=ok('get','/api/public/users/1/order-center?club_id=1').json()
order=next(x for x in oc['activityOrders'] if x['id']==rid)
assert order['status']=='refunded' and order['refundedParticipantCount']==3 and len(order['participants'])==3
assert all(p['status']=='refunded' for p in order['participants'])

print('SMOKE V0.14 OK · PARTICIPANT ALLOCATION / SINGLE-PERSON REFUND / POINT SPLIT / BENEFIT SPLIT / SEAT RELEASE / LAST-PERSON CLOSEOUT')
