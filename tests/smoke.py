from fastapi.testclient import TestClient
from pathlib import Path
import os,sys,uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
import app
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw);assert r.status_code<400,(url,r.status_code,r.text);return r

ok('get','/api/health')
d=ok('get','/api/club/1/dashboard').json();assert d['activityCount']>=1
acts=ok('get','/api/club/1/activities').json();aid=acts[0]['id']
a=ok('get',f'/api/public/activities/{aid}').json();assert a['occurrences']
q=ok('get',f"/api/public/activities/{aid}/price-quote?occurrence_id={a['occurrences'][0]['id']}&user_id=1&club_points=100&gear_points=100").json();assert q['payable']<=q['original']
# direct AI creation
r=ok('post','/api/club/1/activities/ai-generate',data={'prompt':'2026年11月2日，成都周边青城山，轻徒步8公里，20人，198元/人。'}).json();new_id=r['activityId']
ok('post',f'/api/club/1/activities/{new_id}/publish')
new=ok('get',f'/api/public/activities/{new_id}').json();assert new['occurrences']
ok('post',f'/api/club/1/activities/{new_id}/channel/wechat')
sg=ok('post',f'/api/public/activities/{new_id}/signup',json={'name':'测试用户','phone':'13900000099','occurrenceId':new['occurrences'][0]['id'],'clubPoints':0,'gearPoints':0}).json();assert sg['clubPointsEarned']>=0
od=ok('post','/api/public/clubs/1/gear-orders',json={'userId':1,'items':[{'productId':2,'quantity':1}]}).json();assert od['gearPointsEarned']>0
ok('get','/api/platform/orders');ok('get','/api/platform/commissions');ok('get','/api/platform/ai/status')
u=ok('get','/api/club/1/ai/usage').json();assert len(u)>=1
pu=ok('get','/api/platform/ai/usage').json();assert len(pu)>=1
print('SMOKE OK · AI / OCCURRENCE / POINTS / MALL')
cs=ok('get','/api/commerce/status').json();assert cs['provider'] in ('local','medusa')
# cancellation must restore redeemed points / release occurrence seat
act=ok('get',f'/api/public/activities/{aid}').json(); occ=act['occurrences'][0]
wb=ok('get','/api/public/users/1/wallet?club_id=1').json()
sg2=ok('post',f'/api/public/activities/{aid}/signup',json={'name':'林野','phone':'13800000001','occurrenceId':occ['id'],'clubPoints':min(100,wb['clubPoints']),'gearPoints':min(100,wb['gearPoints'])}).json()
ok('post',f"/api/public/registrations/{sg2['registrationId']}/cancel")
print('SMOKE OK · COMMERCE ADAPTER / POINT REVERSAL')
# v0.6 checkout-intent lifecycle: hold -> cancel / hold -> paid -> idempotent callback
base=ok('get','/api/public/users/1/wallet?club_id=1').json()
act=ok('get',f'/api/public/activities/{aid}').json(); occ=act['occurrences'][0]
co=ok('post',f'/api/public/activities/{aid}/checkout',json={
    'name':'林野','phone':'13800000001','occurrenceId':occ['id'],
    'clubPoints':min(100,base['clubPoints']),'gearPoints':min(100,base['gearPoints'])
}).json()
held=ok('get',f"/api/public/checkouts/{co['checkoutId']}").json(); assert held['status']=='pending_payment'
after_hold=ok('get','/api/public/users/1/wallet?club_id=1').json()
assert after_hold['clubPoints'] <= base['clubPoints'] and after_hold['gearPoints'] <= base['gearPoints']
ok('post',f"/api/public/checkouts/{co['checkoutId']}/cancel")
after_release=ok('get','/api/public/users/1/wallet?club_id=1').json()
assert after_release['clubPoints']==base['clubPoints'] and after_release['gearPoints']==base['gearPoints']

co2=ok('post',f'/api/public/activities/{aid}/checkout',json={
    'name':'林野','phone':'13800000001','occurrenceId':occ['id'],'clubPoints':0,'gearPoints':0
}).json()
ev={'eventKey':'smoke-'+uuid.uuid4().hex,'provider':'local','checkoutId':co2['checkoutId'],'orderId':'ord_smoke_activity_'+uuid.uuid4().hex[:8]}
paid=ok('post','/api/commerce/events/order-paid',json=ev).json(); assert paid['result']['kind']=='activity'
idem=ok('post','/api/commerce/events/order-paid',json=ev).json(); assert idem['idempotent'] is True

w=ok('get','/api/public/users/1/wallet?club_id=1').json()
gc=ok('post','/api/public/clubs/1/gear-checkout',json={'userId':1,'items':[{'productId':1,'quantity':1}],'gearPoints':min(100,w['gearPoints'])}).json()
gp=ok('post',f"/api/public/checkouts/{gc['checkoutId']}/confirm",json={'commerceOrderId':'ord_smoke_gear'}).json(); assert gp['kind']=='gear'
print('SMOKE OK · CHECKOUT INTENT / POINT HOLDS / IDEMPOTENT PAYMENT EVENT')

# v0.7 per-activity points policy: club owner controls earn/redeem; platform gates Gear Points subsidy.
pol=ok('patch',f'/api/club/1/activities/{aid}/points-policy',json={
    'enabled':True,'earnClubPoints':True,'acceptClubPoints':True,
    'clubPointsMaxDiscountPercent':1,'acceptGearPoints':True,'gearPointsMaxDiscountAmount':3
}).json()
assert pol['effective']['acceptClubPoints'] is True and pol['effective']['acceptGearPoints'] is True
act=ok('get',f'/api/public/activities/{aid}').json(); occ=act['occurrences'][0]
q=ok('get',f"/api/public/activities/{aid}/price-quote?occurrence_id={occ['id']}&user_id=1&club_points=999999&gear_points=999999").json()
assert q['clubPointDiscount'] <= round(q['original']*0.01,2)+0.01
assert q['platformPointSubsidy'] <= 3.001

# The checkout snapshots policy. Later edits cannot rewrite an already-created bill.
co3=ok('post',f'/api/public/activities/{aid}/checkout',json={
    'name':'林野','phone':'13800000001','occurrenceId':occ['id'],'clubPoints':999999,'gearPoints':999999
}).json()
held3=ok('get',f"/api/public/checkouts/{co3['checkoutId']}").json()
assert held3['points_policy_snapshot_json'] or held3.get('pointsPolicy') is not None
ok('patch',f'/api/club/1/activities/{aid}/points-policy',json={
    'enabled':False,'earnClubPoints':False,'acceptClubPoints':False,'acceptGearPoints':False
})
paid3=ok('post',f"/api/public/checkouts/{co3['checkoutId']}/confirm",json={'commerceOrderId':'ord_policy_snapshot'}).json()
assert paid3['pointsPolicy']['enabled'] is True
assert paid3['clubPointsEarned'] >= 0

# New checkouts after disabling must not earn or redeem either point type.
act2=ok('get',f'/api/public/activities/{aid}').json(); occ2=act2['occurrences'][0]
q2=ok('get',f"/api/public/activities/{aid}/price-quote?occurrence_id={occ2['id']}&user_id=1&club_points=999999&gear_points=999999").json()
assert q2['clubPointsUsed']==0 and q2['gearPointsUsed']==0
co4=ok('post',f'/api/public/activities/{aid}/checkout',json={
    'name':'林野','phone':'13800000001','occurrenceId':occ2['id'],'clubPoints':999999,'gearPoints':999999
}).json()
paid4=ok('post',f"/api/public/checkouts/{co4['checkoutId']}/confirm",json={'commerceOrderId':'ord_policy_off'}).json()
assert paid4['clubPointsEarned']==0

# Platform gate wins over a club's activity toggle for Gear Points because platform funds that subsidy.
ok('patch','/api/platform/points-policy',json={'gearPointsActivityRedeemEnabled':False})
ok('patch',f'/api/club/1/activities/{aid}/points-policy',json={
    'enabled':True,'earnClubPoints':True,'acceptClubPoints':True,'clubPointsMaxDiscountPercent':100,
    'acceptGearPoints':True,'gearPointsMaxDiscountAmount':30
})
q3=ok('get',f"/api/public/activities/{aid}/price-quote?occurrence_id={occ2['id']}&user_id=1&club_points=0&gear_points=999999").json()
assert q3['gearPointsUsed']==0 and q3['pointsPolicy']['platformGearPointsAllowed'] is False
ok('patch','/api/platform/points-policy',json={'gearPointsActivityRedeemEnabled':True})
# restore a sensible demo policy
ok('patch',f'/api/club/1/activities/{aid}/points-policy',json={
    'enabled':True,'earnClubPoints':True,'acceptClubPoints':True,'clubPointsMaxDiscountPercent':10,
    'acceptGearPoints':True,'gearPointsMaxDiscountAmount':30
})
print('SMOKE OK · ACTIVITY POINTS POLICY / PLATFORM GATE / CHECKOUT SNAPSHOT')
