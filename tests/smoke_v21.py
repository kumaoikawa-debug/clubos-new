from pathlib import Path
import os,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1';os.environ['COMMERCE_PROVIDER']='local';os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v21_test.db')
try:Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError:pass
import app
from fastapi.testclient import TestClient
c=TestClient(app.app)
def ok(m,u,**kw):
 r=getattr(c,m)(u,**kw);assert r.status_code<400,(u,r.status_code,r.text);return r
def bad(m,u,code=409,**kw):
 r=getattr(c,m)(u,**kw);assert r.status_code==code,(u,r.status_code,r.text);return r
def money(v):return round(float(v or 0),2)
assert ok('get','/api/health').json()['version'].startswith(('0.21','0.22','0.23','0.24','0.25'))
# Supplier term + sourcing
sup=ok('post','/api/platform/suppliers',json={'name':'v21财务供应商','code':'SUP-V21','paymentTerms':'月结30天','paymentTermDays':30,'leadTimeDays':5}).json();sid=int(sup['id']);assert sup['payment_term_days']==30
ok('post','/api/platform/products/1/suppliers',json={'supplierId':sid,'purchasePrice':100,'minOrderQty':1,'isPrimary':True})
# PO locked at 100; actual receipts 110 and 95 create +40 / -30 price variance and actual A/P.
po=ok('post','/api/platform/procurement/purchase-orders',json={'supplierId':sid,'items':[{'productId':1,'quantity':10}],'note':'v21价差测试'}).json();poid=po['id'];pi=int(po['items'][0]['id'])
ok('post',f'/api/platform/procurement/purchase-orders/{poid}/approve');ok('post',f'/api/platform/procurement/purchase-orders/{poid}/order')
r1=ok('post',f'/api/platform/procurement/purchase-orders/{poid}/receive',json={'items':[{'purchaseOrderItemId':pi,'quantity':4,'unitCost':110}]}).json();assert money(r1['receivedCost'])==440 and money(r1['supplierPayable']['original_amount'])==440
with app.conn() as db:
 ri=dict(db.execute('SELECT * FROM purchase_receipt_items WHERE receipt_id=?',(r1['receiptId'],)).fetchone());assert money(ri['ordered_unit_cost'])==100 and money(ri['price_variance'])==40
# Partial supplier payment allocated to oldest payable, idempotent by paymentRef.
pay=ok('post','/api/platform/finance/supplier-payments',json={'supplierId':sid,'amount':200,'paymentRef':'BANK-V21-001','paymentMethod':'bank'}).json();assert money(pay['amount'])==200
pay2=ok('post','/api/platform/finance/supplier-payments',json={'supplierId':sid,'amount':200,'paymentRef':'BANK-V21-001','paymentMethod':'bank'}).json();assert pay2['idempotent'] is True
bad('post','/api/platform/finance/supplier-payments',json={'supplierId':sid,'amount':99999,'paymentRef':'BANK-OVER'})
r2=ok('post',f'/api/platform/procurement/purchase-orders/{poid}/receive',json={'items':[{'purchaseOrderItemId':pi,'quantity':6,'unitCost':95}]}).json();assert money(r2['receivedCost'])==570
fs=ok('get','/api/platform/finance/supplier-summary').json();f=next(x for x in fs if x['supplierId']==sid);assert money(f['totalPayables'])==1010 and money(f['paidAmount'])==200 and money(f['purchasePriceVariance'])==10 and money(f['outstanding'])==810
# Purchase return physically leaves WMS and supplier credit auto-offsets oldest outstanding payable.
with app.conn() as db:stock_before=int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])
pr=ok('post','/api/platform/procurement/purchase-returns',json={'purchaseOrderId':poid,'items':[{'purchaseOrderItemId':pi,'quantity':2}],'note':'退回2件'}).json();rid=pr['id'];assert money(pr['total_amount'])==200
ok('post',f'/api/platform/procurement/purchase-returns/{rid}/approve');ok('post',f'/api/platform/procurement/purchase-returns/{rid}/ship');cr=ok('post',f'/api/platform/procurement/purchase-returns/{rid}/credit',json={'note':'供应商确认贷项'}).json();assert cr['status']=='credited' and money(cr['credit_amount'])==200
with app.conn() as db:assert int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])==stock_before-2
f=next(x for x in ok('get','/api/platform/finance/supplier-summary').json() if x['supplierId']==sid);assert money(f['creditsApplied'])==200 and money(f['outstanding'])==610
# Gear order snapshots cost at order time.
with app.conn() as db:cost_at_sale=float(db.execute('SELECT average_cost FROM products WHERE id=1').fetchone()[0])
go=ok('post','/api/public/clubs/1/gear-orders',json={'userId':1,'items':[{'productId':1,'quantity':1}]}).json();oid=int(go['orderId'])
p1=ok('get',f'/api/platform/profit/orders?limit=20').json();profit1=next(x for x in p1 if x['orderId']==oid);assert money(profit1['cogs'])==money(cost_at_sale)
# Record real logistics cost and ensure contribution profit includes it.
prof=ok('patch',f'/api/platform/orders/{oid}/operating-costs',json={'shippingCost':18,'packagingCost':4}).json();expected=money(prof['netSales']-prof['cogs']-prof['platformPointSubsidy']-prof['platformBenefitSubsidy']-prof['clubCommission']-22);assert money(prof['contributionProfit'])==expected
# A later expensive procurement changes current average cost but must not rewrite historical order COGS.
po2=ok('post','/api/platform/procurement/purchase-orders',json={'supplierId':sid,'items':[{'productId':1,'quantity':1,'unitCost':300}]}).json();po2id=po2['id'];pi2=int(po2['items'][0]['id']);ok('post',f'/api/platform/procurement/purchase-orders/{po2id}/approve');ok('post',f'/api/platform/procurement/purchase-orders/{po2id}/order');ok('post',f'/api/platform/procurement/purchase-orders/{po2id}/receive',json={'items':[{'purchaseOrderItemId':pi2,'quantity':1,'unitCost':300}]})
profit2=next(x for x in ok('get','/api/platform/profit/orders?limit=20').json() if x['orderId']==oid);assert money(profit2['cogs'])==money(profit1['cogs']) and money(profit2['shippingCost'])==18
summary=ok('get','/api/platform/profit/summary').json();assert 'contributionProfit' in summary and 'grossMarginPct' in summary
products=ok('get','/api/platform/profit/products').json();assert any(x['productId']==1 for x in products)
print('SMOKE V0.21 OK · SUPPLIER A/P / PAYMENT / PRICE VARIANCE / PURCHASE RETURN CREDIT / COST SNAPSHOT / ORDER PROFIT')
