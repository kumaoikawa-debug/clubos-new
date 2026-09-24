from pathlib import Path
import os, sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v09_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

h=ok('get','/api/health').json(); assert h['version'].startswith(('0.9','0.10','0.11','0.12','0.13','0.14','0.15','0.16','0.17','0.18','0.19','0.20','0.21','0.22','0.23','0.24','0.25'))
activity=ok('get','/api/public/clubs/1/activities').json()[0]
detail=ok('get',f"/api/public/activities/{activity['id']}").json(); occ=detail['occurrences'][0]

# 1) Redeem a club-funded activity voucher and a platform-funded activity voucher.
club_bid=ok('post','/api/club/1/benefits',json={
    'title':'俱乐部活动20元券','pointsType':'club','pointsCost':200,'cashValue':20,'benefitType':'activity_coupon','stock':10
}).json()['benefitId']
platform_activity_bid=ok('post','/api/platform/benefits',json={
    'title':'平台活动30元补贴券','pointsType':'gear','pointsCost':300,'cashValue':30,'benefitType':'activity_coupon','stock':10
}).json()['benefitId']
platform_gear_bid=ok('post','/api/platform/benefits',json={
    'title':'平台装备25元券','pointsType':'gear','pointsCost':300,'cashValue':25,'benefitType':'gear_coupon','stock':10
}).json()['benefitId']

club_v=ok('post',f'/api/public/clubs/1/benefits/{club_bid}/redeem',json={'userId':1}).json()
pa_v=ok('post',f'/api/public/clubs/1/benefits/{platform_activity_bid}/redeem',json={'userId':1}).json()
pg_v=ok('post',f'/api/public/clubs/1/benefits/{platform_gear_bid}/redeem',json={'userId':1}).json()
assert club_v['fundingOwner']=='CLUB' and pa_v['fundingOwner']=='PLATFORM' and pg_v['fundingOwner']=='PLATFORM'

# Eligible voucher API is context-aware.
av=ok('get','/api/public/clubs/1/vouchers?user_id=1&kind=activity').json()
gv=ok('get','/api/public/clubs/1/vouchers?user_id=1&kind=gear').json()
assert {x['voucher_code'] for x in av} >= {club_v['voucherCode'],pa_v['voucherCode']}
assert pg_v['voucherCode'] in {x['voucher_code'] for x in gv}
assert club_v['voucherCode'] not in {x['voucher_code'] for x in gv}

# 2) Quote applies both point discounts and voucher funding without collapsing owners.
vcodes=f"{club_v['voucherCode']},{pa_v['voucherCode']}"
q=ok('get',f"/api/public/activities/{activity['id']}/price-quote?occurrence_id={occ['id']}&user_id=1&club_points=100&gear_points=100&voucher_codes={vcodes}").json()
assert round(q['clubPointDiscount'],2)==1.00,q
assert round(q['platformPointSubsidy'],2)==1.00,q
assert round(q['benefits']['clubDiscount'],2)==20.00,q
assert round(q['benefits']['platformSubsidy'],2)==30.00,q
assert round(q['payable'],2)==446.00,q

# 3) Checkout holds vouchers; cancellation releases them.
co=ok('post',f"/api/public/activities/{activity['id']}/checkout",json={
    'name':'林野','phone':'13800000001','occurrenceId':occ['id'],'clubPoints':100,'gearPoints':100,
    'voucherCodes':[club_v['voucherCode'],pa_v['voucherCode']]
}).json()
chk=ok('get',f"/api/public/checkouts/{co['checkoutId']}").json()
assert round(chk['club_benefit_discount'],2)==20 and round(chk['platform_benefit_subsidy'],2)==30
assert len(chk['vouchers'])==2 and all(x['status']=='held' for x in chk['vouchers'])
ok('post',f"/api/public/checkouts/{co['checkoutId']}/cancel")
av2=ok('get','/api/public/clubs/1/vouchers?user_id=1&kind=activity').json()
assert {club_v['voucherCode'],pa_v['voucherCode']} <= {x['voucher_code'] for x in av2}

# 4) Payment consumes vouchers and writes funding fields to registration.
co2=ok('post',f"/api/public/activities/{activity['id']}/checkout",json={
    'name':'林野','phone':'13800000001','occurrenceId':occ['id'],'clubPoints':100,'gearPoints':100,
    'voucherCodes':[club_v['voucherCode'],pa_v['voucherCode']]
}).json()
paid=ok('post',f"/api/public/checkouts/{co2['checkoutId']}/confirm",json={'commerceOrderId':'ord_v09_activity'}).json()
assert round(paid['cashPaid'],2)==446 and round(paid['clubBenefitDiscount'],2)==20 and round(paid['platformBenefitSubsidy'],2)==30
rid=paid['registrationId']
mc=ok('get','/api/public/clubs/1/member-center?user_id=1').json()
used={x['voucher_code']:x['status'] for x in mc['redemptions']}
assert used[club_v['voucherCode']]=='used' and used[pa_v['voucherCode']]=='used'
# Membership spend counts cash + platform subsidies, but not club-funded discounts.
with app.conn() as dbc:
    reg=app.row(dbc.execute('SELECT * FROM registrations WHERE id=?',(rid,)))
assert round(reg['club_benefit_discount'],2)==20 and round(reg['platform_benefit_subsidy'],2)==30
assert round(reg['amount']+reg['platform_point_subsidy']+reg['platform_benefit_subsidy'],2)==477

# Full activity cancellation re-issues used vouchers, not their original redeemed points.
ok('post',f'/api/public/registrations/{rid}/cancel')
av3=ok('get','/api/public/clubs/1/vouchers?user_id=1&kind=activity').json()
assert {club_v['voucherCode'],pa_v['voucherCode']} <= {x['voucher_code'] for x in av3}

# 5) Gear voucher enters gear checkout, reduces cash, and is restored on full refund.
prod=ok('get','/api/platform/products').json()[0]
wallet_before_gear_checkout=ok('get','/api/public/users/1/wallet?club_id=1').json()['gearPoints']
gco=ok('post','/api/public/clubs/1/gear-checkout',json={
    'userId':1,'items':[{'productId':prod['id'],'quantity':1}],'gearPoints':100,'voucherCodes':[pg_v['voucherCode']]
}).json()
assert round(gco['benefits']['platformSubsidy'],2)==25
gpaid=ok('post',f"/api/public/checkouts/{gco['checkoutId']}/confirm",json={'commerceOrderId':'ord_v09_gear'}).json()
assert round(gpaid['platformBenefitSubsidy'],2)==25
order_id=gpaid['orderId']
mc2=ok('get','/api/public/clubs/1/member-center?user_id=1').json()
status={x['voucher_code']:x['status'] for x in mc2['redemptions']}
assert status[pg_v['voucherCode']]=='used'
rf=ok('post',f'/api/platform/orders/{order_id}/refund',json={'reason':'v09 benefit refund'}).json()
assert rf['benefitVouchersRestored']==1
mc3=ok('get','/api/public/clubs/1/member-center?user_id=1').json()
status2={x['voucher_code']:x['status'] for x in mc3['redemptions']}
assert status2[pg_v['voucherCode']]=='issued'
wallet_after_refund=ok('get','/api/public/users/1/wallet?club_id=1').json()['gearPoints']
assert wallet_after_refund==wallet_before_gear_checkout,(wallet_before_gear_checkout,wallet_after_refund)

print('SMOKE V0.9 OK · BENEFIT VOUCHERS -> ACTIVITY/GEAR CHECKOUT -> FUNDING -> REFUND RESTORE')
