from pathlib import Path
import os,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1';os.environ['COMMERCE_PROVIDER']='local';os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v22_test.db')
try:Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError:pass
import app
from fastapi.testclient import TestClient
c=TestClient(app.app)
def ok(m,u,**kw):
 r=getattr(c,m)(u,**kw);assert r.status_code<400,(u,r.status_code,r.text);return r
def bad(m,u,code=400,**kw):
 r=getattr(c,m)(u,**kw);assert r.status_code==code,(u,r.status_code,r.text);return r
assert ok('get','/api/health').json()['version'].startswith(('0.22','0.23','0.24','0.25'))
# Policy is platform-owned, bounded and persistent.
p=ok('patch','/api/platform/replenishment/policy',json={'salesWindowDays':7,'targetCoverDays':60,'safetyDays':10,'slowMovingDays':90}).json()
assert p=={'salesWindowDays':7,'targetCoverDays':60,'safetyDays':10,'slowMovingDays':90}
bad('patch','/api/platform/replenishment/policy',code=400,json={'salesWindowDays':2})
# Primary sourcing relation provides lead time / MOQ for replenishment.
sup=ok('post','/api/platform/suppliers',json={'name':'v22补货供应商','code':'SUP-V22','leadTimeDays':10}).json();sid=int(sup['id'])
ok('post','/api/platform/products/1/suppliers',json={'supplierId':sid,'purchasePrice':120,'minOrderQty':5,'leadTimeDays':10,'isPrimary':True})
# Generate real paid Gear demand through ClubOS order flow. It also reserves WMS stock.
orders=[]
for _ in range(5):
 orders.append(int(ok('post','/api/public/clubs/1/gear-orders',json={'userId':1,'items':[{'productId':1,'quantity':2}]}).json()['orderId']))
products=ok('get','/api/platform/analytics/products?window_days=7').json();x=next(z for z in products if z['productId']==1)
assert x['soldUnits']==10 and x['salesVelocityPerDay']>1 and x['primarySupplierId']==sid and x['recommendedReorderQty']>=5
assert x['health'] in ('replenish','at_risk','stockout')
rec_before=int(x['recommendedReorderQty'])
# A committed inbound PO reduces the recommendation; draft/approved alone would not count as in-transit.
po=ok('post','/api/platform/procurement/purchase-orders',json={'supplierId':sid,'items':[{'productId':1,'quantity':10}],'note':'v22在途测试'}).json();poid=po['id']
ok('post',f'/api/platform/procurement/purchase-orders/{poid}/approve');ok('post',f'/api/platform/procurement/purchase-orders/{poid}/order')
x2=next(z for z in ok('get','/api/platform/analytics/products?window_days=7').json() if z['productId']==1)
assert x2['inboundOpenQty']==10 and x2['recommendedReorderQty']<=rec_before
# Explicit replenishment action creates a DRAFT PO only; it never auto-orders or mutates stock.
with app.conn() as db: stock_before=int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])
r=ok('post','/api/platform/replenishment/products/1/create-po',json={}).json();rpo=r['purchaseOrder']
assert rpo['status']=='draft' and rpo['created_by']=='replenishment' and '智能补货' in (rpo['note'] or '')
with app.conn() as db: assert int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])==stock_before
# Dashboard / club / supplier analytics are driven by real domain facts.
d=ok('get','/api/platform/analytics/commerce?window_days=7').json();assert d['orderCount']>=5 and d['soldUnits']>=10 and 'inventoryValue' in d and 'stockRiskSkuCount' in d
clubs=ok('get','/api/platform/analytics/clubs?window_days=7').json();cl=next(z for z in clubs if z['clubId']==1);assert cl['orderCount']>=5 and cl['grossSales']>0
ss=ok('get','/api/platform/analytics/suppliers').json();sx=next(z for z in ss if z['supplierId']==sid);assert sx['activeSkuCount']>=1 and sx['orderedUnits']>=10
recs=ok('get','/api/platform/replenishment/recommendations?window_days=7').json();assert any(z['productId']==1 for z in recs)
print('SMOKE V0.22 OK · COMMERCE BI / PRODUCT+CLUB+SUPPLIER ANALYTICS / STOCK HEALTH / DAYS COVER / REPLENISHMENT / DRAFT PO')
