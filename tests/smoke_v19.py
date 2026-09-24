from pathlib import Path
import os, sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v19_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

def bad(method,url,code=409,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code==code,(url,r.status_code,r.text)
    return r

def amount(v): return round(float(v or 0),2)

h=ok('get','/api/health').json(); assert h['version'].startswith(('0.19','0.20','0.21','0.22','0.23','0.24','0.25'))

# 0) Existing v0.18 on-hand quantities become explicit migration opening balances.
with app.conn() as db:
    products=[dict(x) for x in db.execute('SELECT * FROM products ORDER BY id').fetchall()]
    opening=[dict(x) for x in db.execute("SELECT * FROM inventory_movements WHERE movement_type='opening_balance' ORDER BY product_id").fetchall()]
assert len(opening)==len(products)==4
assert all(int(m['quantity_delta'])==int(p['stock']) for m,p in zip(opening,products))

# 1) Supplier master + product sourcing relationship, including MOQ and primary supplier.
sup=ok('post','/api/platform/suppliers',json={
    'name':'成都山野装备制造','code':'SUP-V19-CD01','contactName':'张采购','phone':'13800138000',
    'paymentTerms':'月结30天','leadTimeDays':7,'note':'v0.19 regression supplier'
}).json()
sid=int(sup['id']); assert sup['status']=='active' and sup['code']=='SUP-V19-CD01'
link=ok('post','/api/platform/products/1/suppliers',json={
    'supplierId':sid,'supplierSku':'CD-SHELL-01','purchasePrice':120,'minOrderQty':2,'leadTimeDays':5,'isPrimary':True
}).json()
assert link['is_primary']==1 and amount(link['purchase_price'])==120 and link['min_order_qty']==2
links=ok('get','/api/platform/products/1/suppliers').json(); assert links[0]['supplier_id']==sid
bad('post','/api/platform/procurement/purchase-orders',json={'supplierId':sid,'items':[{'productId':1,'quantity':1}]})

# 2) Draft -> approved -> ordered. Stock must not move until a physical receipt is recorded.
with app.conn() as db: stock0=int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])
po=ok('post','/api/platform/procurement/purchase-orders',json={
    'supplierId':sid,'expectedAt':'2026-10-05','note':'首批秋冬软壳补货','items':[{'productId':1,'quantity':10}]
}).json()
poid=po['id']; poi=int(po['items'][0]['id'])
assert po['status']=='draft' and amount(po['total_amount'])==1200 and po['outstandingQty']==10
with app.conn() as db: assert int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])==stock0
po=ok('post',f'/api/platform/procurement/purchase-orders/{poid}/approve').json(); assert po['status']=='approved'
po=ok('post',f'/api/platform/procurement/purchase-orders/{poid}/order').json(); assert po['status']=='ordered'
with app.conn() as db: assert int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])==stock0

# 3) Partial receipt changes real stock, creates GRN + movement, and establishes first known average cost.
r1=ok('post',f'/api/platform/procurement/purchase-orders/{poid}/receive',json={
    'items':[{'purchaseOrderItemId':poi,'quantity':4}],'note':'第一车到货'
}).json()
assert r1['status']=='partially_received' and amount(r1['receivedCost'])==480 and r1['purchaseOrder']['outstandingQty']==6
with app.conn() as db:
    p1=dict(db.execute('SELECT * FROM products WHERE id=1').fetchone())
    mv=dict(db.execute("SELECT * FROM inventory_movements WHERE reference_type='purchase_receipt' AND reference_id=?",(r1['receiptId'],)).fetchone())
assert int(p1['stock'])==stock0+4 and amount(p1['average_cost'])==120 and amount(p1['last_purchase_cost'])==120
assert mv['movement_type']=='purchase_inbound' and int(mv['quantity_delta'])==4 and int(mv['stock_before'])==stock0 and int(mv['stock_after'])==stock0+4
# Over-receipt is rejected and doesn't mutate stock.
bad('post',f'/api/platform/procurement/purchase-orders/{poid}/receive',json={'items':[{'purchaseOrderItemId':poi,'quantity':7}]})
with app.conn() as db: assert int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])==stock0+4

# 4) Final receipt closes PO. Receipts are immutable inbound facts and visible in the procurement ledger.
r2=ok('post',f'/api/platform/procurement/purchase-orders/{poid}/receive',json={'items':[{'purchaseOrderItemId':poi,'quantity':6}],'note':'尾货到齐'}).json()
assert r2['status']=='received' and r2['purchaseOrder']['outstandingQty']==0
with app.conn() as db:
    p1=dict(db.execute('SELECT * FROM products WHERE id=1').fetchone())
assert int(p1['stock'])==stock0+10 and amount(p1['average_cost'])==120
receipts=ok('get',f'/api/platform/procurement/receipts?po_id={poid}').json(); assert len(receipts)==2

# 5) Manual stock count adjustment is an auditable movement, never a silent number edit.
adj=ok('post','/api/platform/inventory/adjustments',json={'productId':1,'quantityDelta':-3,'reason':'月末盘点：破损3件'}).json()
assert adj['stockBefore']==stock0+10 and adj['stockAfter']==stock0+7
moves=ok('get','/api/platform/inventory/movements?product_id=1').json()
assert any(x['movement_type']=='manual_out' and int(x['quantity_delta'])==-3 for x in moves)

# Direct product stock PATCH is retained for compatibility but now also writes the ledger.
ok('patch','/api/platform/products/1',json={'stock':stock0+8,'stockReason':'兼容入口盘盈1件'})
moves=ok('get','/api/platform/inventory/movements?product_id=1').json()
assert any(x['movement_type']=='manual_in' and x['note']=='兼容入口盘盈1件' for x in moves)

# 6) Sales and refunds are part of the same inventory ledger.
with app.conn() as db: before_sale=int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])
go=ok('post','/api/public/clubs/1/gear-orders',json={'userId':1,'items':[{'productId':1,'quantity':2}]}).json(); oid=int(go['orderId'])
with app.conn() as db:
    after_sale=int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])
    sale=dict(db.execute("SELECT * FROM inventory_movements WHERE product_id=1 AND reference_type='gear_order' AND reference_id=?",(str(oid),)).fetchone())
assert after_sale==before_sale-2 and sale['movement_type']=='sale_outbound' and int(sale['quantity_delta'])==-2
ref=ok('post',f'/api/platform/orders/{oid}/refund',json={'reason':'v0.19 inventory refund test'}).json(); assert ref['status']=='refunded'
with app.conn() as db:
    after_refund=int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])
    rm=dict(db.execute("SELECT * FROM inventory_movements WHERE product_id=1 AND reference_type='gear_refund' AND reference_id=?",(str(oid),)).fetchone())
assert after_refund==before_sale and rm['movement_type']=='refund_restock' and int(rm['quantity_delta'])==2

# 7) Return/refund physical receiving uses the same ledger, while refund-only still doesn't restock.
go2=ok('post','/api/public/clubs/1/gear-orders',json={'userId':1,'items':[{'productId':2,'quantity':1}]}).json(); oid2=int(go2['orderId'])
with app.conn() as db:
    oi=dict(db.execute('SELECT * FROM gear_order_items WHERE order_id=?',(oid2,)).fetchone())
    before_return=int(db.execute('SELECT stock FROM products WHERE id=2').fetchone()[0])
as_case=ok('post',f'/api/public/orders/{oid2}/after-sales',json={'userId':1,'type':'return_refund','reason':'尺码不合适','items':[{'orderItemId':oi['id'],'quantity':1}]}).json()
cid=as_case['id']
ok('post',f'/api/platform/after-sales/{cid}/approve',json={})
ok('post',f'/api/public/after-sales/{cid}/return-shipment',json={'carrier':'顺丰','trackingNo':'SFV19001'})
recv=ok('post',f'/api/platform/after-sales/{cid}/receive-return',json={'restock':True}).json(); assert recv['status']=='approved_pending_refund'
with app.conn() as db:
    after_return=int(db.execute('SELECT stock FROM products WHERE id=2').fetchone()[0])
    am=dict(db.execute("SELECT * FROM inventory_movements WHERE product_id=2 AND reference_type='after_sales' AND reference_id=?",(cid,)).fetchone())
assert after_return==before_return+1 and am['movement_type']=='after_sales_return'

# 8) Supply-chain dashboards expose the source-of-truth inventory and open procurement state.
summary=ok('get','/api/platform/inventory/summary').json()
assert summary['skuCount']>=4 and summary['units']>0 and summary['inventoryValue']>=0 and summary['openPurchaseOrders']==0
suppliers=ok('get','/api/platform/suppliers').json(); assert any(x['id']==sid and x['product_count']>=1 for x in suppliers)
pos=ok('get','/api/platform/procurement/purchase-orders').json(); assert any(x['id']==poid and x['status']=='received' for x in pos)


# 9) A real warehouse receipt is not rolled back when Medusa inventory sync is temporarily unavailable.
po_sync=ok('post','/api/platform/procurement/purchase-orders',json={
    'supplierId':sid,'items':[{'productId':3,'quantity':2,'unitCost':88}],'note':'Medusa sync failure isolation'
}).json(); psid=po_sync['id']; psi=int(po_sync['items'][0]['id'])
ok('post',f'/api/platform/procurement/purchase-orders/{psid}/approve')
ok('post',f'/api/platform/procurement/purchase-orders/{psid}/order')
with app.conn() as db: stock_sync_before=int(db.execute('SELECT stock FROM products WHERE id=3').fetchone()[0])
orig_provider,orig_sync=app.commerce_provider,app._sync_product_to_medusa
app.commerce_provider=lambda:'medusa'
def fail_sync(product_id,*,create_if_missing=True):
    from fastapi import HTTPException
    raise HTTPException(502,'simulated Medusa outage')
app._sync_product_to_medusa=fail_sync
try:
    sync_receipt=ok('post',f'/api/platform/procurement/purchase-orders/{psid}/receive',json={'items':[{'purchaseOrderItemId':psi,'quantity':1}]}).json()
finally:
    app.commerce_provider,app._sync_product_to_medusa=orig_provider,orig_sync
assert sync_receipt['commerceSync'][0]['status']=='error'
with app.conn() as db:
    assert int(db.execute('SELECT stock FROM products WHERE id=3').fetchone()[0])==stock_sync_before+1
    assert db.execute("SELECT COUNT(*) FROM inventory_movements WHERE reference_id=? AND movement_type='purchase_inbound'",(sync_receipt['receiptId'],)).fetchone()[0]==1

print('SMOKE V0.19 OK · SUPPLIER / PRODUCT SOURCING / PO APPROVAL / PARTIAL+FINAL RECEIPT / COST / INVENTORY LEDGER / SALES+RETURN STOCK TRACE')
