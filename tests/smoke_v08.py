from pathlib import Path
import os, sys, tempfile, uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v08_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

h=ok('get','/api/health').json(); assert h['version'].startswith(('0.8','0.9','0.10','0.11','0.12','0.13','0.14','0.15','0.16','0.17','0.18','0.19','0.20','0.21','0.22','0.23','0.24','0.25'))
# Club-scoped member tiers exist and are independently managed.
tiers=ok('get','/api/club/1/membership/tiers').json(); assert len(tiers)>=3
# Two paid activity registrations qualify seed user for silver by activity count (ANY rule).
a=ok('get','/api/public/clubs/1/activities').json()[0]
detail=ok('get',f"/api/public/activities/{a['id']}").json(); occ=detail['occurrences'][0]
reg_ids=[]
for i in range(2):
    co=ok('post',f"/api/public/activities/{a['id']}/checkout",json={'name':'林野','phone':'13800000001','occurrenceId':occ['id'],'clubPoints':0,'gearPoints':0}).json()
    paid=ok('post',f"/api/public/checkouts/{co['checkoutId']}/confirm",json={'commerceOrderId':f'ord_member_{i}'}).json()
    reg_ids.append(paid['registrationId'])
mc=ok('get','/api/public/clubs/1/member-center?user_id=1').json(); assert mc['wallet']['memberLevel']=='银卡会员',mc
# Refund/cancel one registration recalculates tier downward.
ok('post',f"/api/public/registrations/{reg_ids[0]}/cancel")
mc2=ok('get','/api/public/clubs/1/member-center?user_id=1').json(); assert mc2['wallet']['memberLevel']=='普通会员',mc2

# Club benefits can only consume Club Points.
bad=c.post('/api/club/1/benefits',json={'title':'错误福利','pointsType':'gear','pointsCost':10})
assert bad.status_code==400,bad.text
club_b=ok('post','/api/club/1/benefits',json={'title':'俱乐部专属补给券','pointsType':'club','pointsCost':200,'benefitType':'service','stock':10}).json()['benefitId']
w0=ok('get','/api/public/users/1/wallet?club_id=1').json()
r1=ok('post',f'/api/public/clubs/1/benefits/{club_b}/redeem',json={'userId':1}).json()
assert r1['fundingOwner']=='CLUB' and r1['pointsType']=='club'
w1=ok('get','/api/public/users/1/wallet?club_id=1').json(); assert w1['clubPoints']==w0['clubPoints']-200

# Platform benefits can only consume Gear Points and can be shown inside a club member center.
pb=ok('post','/api/platform/benefits',json={'title':'平台测试福利','pointsType':'gear','pointsCost':500,'cashValue':5,'benefitType':'gear_coupon','stock':10}).json()['benefitId']
w2=ok('get','/api/public/users/1/wallet?club_id=1').json()
r2=ok('post',f'/api/public/clubs/1/benefits/{pb}/redeem',json={'userId':1}).json()
assert r2['fundingOwner']=='PLATFORM' and r2['pointsType']=='gear'
w3=ok('get','/api/public/users/1/wallet?club_id=1').json(); assert w3['gearPoints']==w2['gearPoints']-500

# Full Gear refund reverses redeemed Gear Points, earned Gear Points, commission, AI reward and inventory.
prod=ok('get','/api/platform/products').json()[0]
stock_before=prod['stock']
credits_before=ok('get','/api/club/1/credits').json()['account']['balance']
wallet_before=ok('get','/api/public/users/1/wallet?club_id=1').json()['gearPoints']
use=min(100,wallet_before)
gc=ok('post','/api/public/clubs/1/gear-checkout',json={'userId':1,'items':[{'productId':prod['id'],'quantity':2}],'gearPoints':use}).json()
gp=ok('post',f"/api/public/checkouts/{gc['checkoutId']}/confirm",json={'commerceOrderId':'ord_refund_v08'}).json()
order_id=gp['orderId']; assert gp['clubCommission']>0
stock_after_sale=[x for x in ok('get','/api/platform/products').json() if x['id']==prod['id']][0]['stock']; assert stock_after_sale==stock_before-2
rf=ok('post',f'/api/platform/orders/{order_id}/refund',json={'reason':'smoke full refund'}).json(); assert rf['status']=='refunded' and rf['commissionReversed']>0
# idempotent second call
rf2=ok('post',f'/api/platform/orders/{order_id}/refund',json={'reason':'again'}).json(); assert rf2.get('idempotent') is True
stock_final=[x for x in ok('get','/api/platform/products').json() if x['id']==prod['id']][0]['stock']; assert stock_final==stock_before
wallet_final=ok('get','/api/public/users/1/wallet?club_id=1').json()['gearPoints']; assert wallet_final==wallet_before,(wallet_before,wallet_final)
credits_final=ok('get','/api/club/1/credits').json()['account']['balance']; assert credits_final==credits_before,(credits_before,credits_final)
comm=ok('get','/api/platform/commissions').json(); net=sum(float(x['amount']) for x in comm if x['order_id']==order_id); assert abs(net)<0.001,net
print('SMOKE V0.8 OK · MEMBERSHIP / CLUB+GEAR BENEFITS / FULL REFUND REVERSALS')
