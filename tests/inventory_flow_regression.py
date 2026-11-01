"""Full local SKU flow with isolated DB; no real payment or carrier calls."""
import os,sys,tempfile,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
TMP=Path(tempfile.mkdtemp(prefix='clubos-inventory-regression-'))
os.environ.update(CLUBOS_TEST_OPEN='1',CLUBOS_SECURITY_MODE='demo',COMMERCE_PROVIDER='local',MOCK_AI='1',CLUBOS_DB_PATH=str(TMP/'test.db'))
import app
from fastapi.testclient import TestClient
c=TestClient(app.app,raise_server_exceptions=False)
checks=[]
def call(method,path,data=None,status=200):
 r=c.request(method,path,json=data)
 assert r.status_code==status,(method,path,r.status_code,r.text[:800])
 return r.json()
def check(name,condition):
 assert condition,name
 checks.append(name);print('PASS',name,flush=True)
def stock(pid):
 with app.conn() as db:return dict(db.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone())['stock']
with app.conn() as db:
 db.execute("INSERT INTO user_addresses(user_id,consignee,phone,province,city,district,detail) VALUES(1,'测试','13800000001','四川','成都','高新','测试地址')");address=db.execute('SELECT last_insert_rowid()').fetchone()[0]
supplier=call('POST','/api/platform/suppliers',{'name':'SKU供应商','code':'SKU-TEST'})['id']
pid=call('POST','/api/platform/products',{'name':'规格采购测试','price':0,'status':'draft','colors':['红','黑'],'sizes':['S','M']})['id']
vs=call('GET',f'/api/platform/products/{pid}/variants');red=vs[0]['id'];black=vs[-1]['id']
po=call('POST','/api/platform/procurement/purchase-orders',{'supplierId':supplier,'items':[{'productId':pid,'variantId':red,'quantity':10,'unitCost':10},{'productId':pid,'variantId':black,'quantity':10,'unitCost':100}]});poid=po['id'];iid=po['items'][0]['id']
check('SKU plan preserved',len(po['items'][0]['skuItems'])==2)
call('POST',f'/api/platform/procurement/purchase-orders/{poid}/approve');call('POST',f'/api/platform/procurement/purchase-orders/{poid}/order')
call('POST',f'/api/platform/procurement/purchase-orders/{poid}/supplier-shipment',{'requestKey':'ship1','carrier':'送货','trackingNo':'SEND-1','items':[{'variantId':red,'quantity':10},{'variantId':black,'quantity':10}]})
receipt={'requestKey':'receive1','items':[{'purchaseOrderItemId':iid,'variantId':red,'quantity':10},{'purchaseOrderItemId':iid,'variantId':black,'quantity':10}]}
r=call('POST',f'/api/platform/procurement/purchase-orders/{poid}/receive',receipt)
call('POST',f'/api/platform/procurement/purchase-orders/{poid}/receive',receipt)
check('receipt retry no duplicate stock',stock(pid)==20)
with app.conn() as db:
 check('receipt retry no duplicate payable',db.execute('SELECT COUNT(*) FROM supplier_payables WHERE receipt_id=?',(r['receiptId'],)).fetchone()[0]==1)
 check('receipt retains SKU',db.execute('SELECT COUNT(DISTINCT variant_id) FROM purchase_receipt_items WHERE receipt_id=?',(r['receiptId'],)).fetchone()[0]==2)
check('receipt does not publish',next(x for x in call('GET','/api/platform/products') if x['id']==pid)['status']=='pending_listing')
call('PATCH',f'/api/platform/products/{pid}',{'status':'active'},400)
call('PATCH',f'/api/platform/products/{pid}',{'price':100,'image_url':'/static/uploads/products/gear-1-1.jpg','description':'完整介绍','status':'active'})
checkout=call('POST','/api/public/clubs/1/gear-checkout',{'userId':1,'addressId':address,'items':[{'productId':pid,'variantId':red,'quantity':1},{'productId':pid,'variantId':black,'quantity':1}]})['checkoutId']
check('checkout locks both SKU',stock(pid)==18)
call('PATCH',f'/api/platform/products/{pid}',{'price':200})
payment=call('POST',f'/api/public/checkouts/{checkout}/pay',{'simulateSuccess':True});oid=payment['result']['orderId']
with app.conn() as db:
 check('frozen line prices',all(r[0]==100 for r in db.execute('SELECT unit_price FROM gear_order_items WHERE order_id=?',(oid,))))
 check('payment transfers hold without double reserve',db.execute("SELECT SUM(quantity) FROM warehouse_sku_reservations WHERE order_id=? AND status='reserved'",(oid,)).fetchone()[0]==2)
call('POST',f'/api/platform/orders/{oid}/shipment',{'carrierCode':'SF','trackingNo':'ORDER-1'},409)
pick=call('POST',f'/api/platform/orders/{oid}/pick');call('POST',f'/api/platform/orders/{oid}/pack',{},409)
call('POST',f'/api/platform/warehouse/tasks/{pick["id"]}/picked');call('POST',f'/api/platform/orders/{oid}/pack',{})
call('POST',f'/api/platform/orders/{oid}/shipment',{'carrierCode':'SF','trackingNo':'ORDER-1'})
call('POST',f'/api/platform/orders/{oid}/shipment',{'carrierCode':'SF','trackingNo':'ORDER-1'})
with app.conn() as db:
 counts=db.execute('SELECT SUM(on_hand),SUM(reserved) FROM warehouse_sku_balances WHERE product_id=?',(pid,)).fetchone()
 check('page shipment dispatch exactly once',tuple(counts)==(18,0) and stock(pid)==18)
 before=db.execute('SELECT unit_cost_snapshot FROM gear_order_items WHERE order_id=? ORDER BY id',(oid,)).fetchall()
 check('SKU cost snapshot',sorted(r[0] for r in before)==[10,100])
call('POST','/api/platform/products/import',{'csv':f'商品名,货号,颜色,尺码,库存,零售价\n规格采购测试,{next(p for p in call("GET","/api/platform/products") if p["id"]==pid)["sku"]},红,S,99,200\n'},409)
# External catalog is a draft-only boundary until actual brand API exists.
ext=call('POST','/api/platform/products',{'name':'外部品牌','price':100,'source':'external_api','inventoryMode':'external_api','fulfillmentMode':'supplier_fulfillment'})['id']
call('PATCH',f'/api/platform/products/{ext}',{'status':'active'},409)
call('POST','/api/platform/procurement/purchase-orders',{'supplierId':supplier,'items':[{'productId':ext,'quantity':1,'unitCost':1}]},409)
with app.conn() as db:check('external WMS remains empty',not db.execute('SELECT 1 FROM warehouse_inventory WHERE product_id=?',(ext,)).fetchone())
# Last unit is held at checkout, cancelled holds are released.
last=call('POST','/api/platform/products',{'name':'最后一件','price':100,'stock':1,'description':'完整介绍','image_url':'/static/uploads/products/gear-1-1.jpg','status':'active'})['id']
request={'userId':1,'addressId':address,'items':[{'productId':last,'quantity':1}]}
co=call('POST','/api/public/clubs/1/gear-checkout',request)['checkoutId']
call('POST','/api/public/clubs/1/gear-checkout',request,409)
call('POST',f'/api/public/checkouts/{co}/cancel');check('cancel restores sellable',stock(last)==1)
# Concurrent customers compete for one physical unit, not one unit per process.
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=2) as pool:
 responses=list(pool.map(lambda _:c.post('/api/public/clubs/1/gear-checkout',json=request),range(2)))
check('concurrent last unit has one winner',sorted(r.status_code for r in responses)==[200,409])
for r in responses:
 if r.status_code==200:call('POST',f'/api/public/checkouts/{r.json()["checkoutId"]}/cancel')
# Quarantine stock is physical but cannot be sold until quality-confirmed transfer.
with app.conn() as db:
 wh=app.warehouse_engine;defs=wh.ensure_defaults(db);vid=db.execute('SELECT id FROM product_variants WHERE product_id=?',(last,)).fetchone()[0]
 wh.move_stock(db,product_id=last,variant_id=vid,from_location_id=defs['defaultLocation']['id'],to_location_id=defs['returnLocation']['id'],quantity=1)
check('quarantine excluded from sales',stock(last)==0)
call('POST','/api/public/clubs/1/gear-checkout',request,409)
with app.conn() as db:wh.move_stock(db,product_id=last,variant_id=vid,from_location_id=defs['returnLocation']['id'],to_location_id=defs['defaultLocation']['id'],quantity=1)
check('quality-approved transfer restores sellable',stock(last)==1)
co=call('POST','/api/public/clubs/1/gear-checkout',request)['checkoutId']
with app.conn() as db:db.execute("UPDATE checkout_intents SET created_at=datetime('now','-1 hour') WHERE id=?",(co,))
co2=call('POST','/api/public/clubs/1/gear-checkout',request)['checkoutId']
with app.conn() as db:check('expired checkout released before new sale',db.execute('SELECT status FROM checkout_intents WHERE id=?',(co,)).fetchone()[0]=='expired')
call('POST',f'/api/public/checkouts/{co2}/cancel')
# No API may create stock by a refund after actual shipment.
with app.conn() as db:
 app.inventory_engine.refund_restock(db,product_id=pid,variant_id=red,quantity=1,order_id=oid)
 check('financial refund is not physical return',db.execute('SELECT SUM(on_hand) FROM warehouse_sku_balances WHERE product_id=?',(pid,)).fetchone()[0]==18)
# Exchange returns one physical unit then sends one replacement, once.
with app.conn() as db:
 item=db.execute('SELECT id FROM gear_order_items WHERE order_id=? AND variant_id=?',(oid,red)).fetchone()[0]
 case=app.after_sales_engine.create(db,order_id=oid,user_id=1,case_type='exchange',items=[{'orderItemId':item,'quantity':1}]);cid=case['id']
 app.after_sales_engine.approve(db,case_id=cid)
 app.after_sales_engine.submit_return(db,case_id=cid,carrier='测试',tracking_no='RETURN-1')
 app.after_sales_engine.receive_return(db,case_id=cid,restock=True)
 app.after_sales_engine.ship_exchange(db,case_id=cid,carrier='测试',tracking_no='EXCHANGE-1')
 app.after_sales_engine.ship_exchange(db,case_id=cid,carrier='测试',tracking_no='EXCHANGE-1')
 check('exchange dispatch exactly once',db.execute('SELECT SUM(on_hand) FROM warehouse_sku_balances WHERE product_id=?',(pid,)).fetchone()[0]==18)
# Legacy multi-SKU inventory remains unchanged and requires an actual allocation confirmation.
legacy=call('POST','/api/platform/products',{'name':'历史多规格','price':0,'colors':['红','黑'],'sizes':['M']})['id']
legacy_vs=call('GET',f'/api/platform/products/{legacy}/variants')
with app.conn() as db:
 lid=app.warehouse_engine.ensure_defaults(db)['defaultLocation']['id']
 db.execute('INSERT OR REPLACE INTO warehouse_inventory(product_id,location_id,on_hand) VALUES(?,?,10)',(legacy,lid))
 for v,q in zip(legacy_vs,[6,4]):db.execute('UPDATE product_variants SET stock=? WHERE id=?',(q,v['id']))
 db.execute('UPDATE products SET stock=10 WHERE id=?',(legacy,))
import db as module
module.init_db()
with app.conn() as db:
 check('legacy sizes not guessed',list(r[0] for r in db.execute('SELECT stock FROM product_variants WHERE product_id=? ORDER BY id',(legacy,)))==[6,4])
 check('legacy ambiguity quarantined',db.execute("SELECT status FROM stock_reconciliation WHERE product_id=?",(legacy,)).fetchone()[0]=='pending')
call('POST',f'/api/platform/warehouse/reconcile/{legacy}',{'balances':[{'variantId':v['id'],'locationId':lid,'onHand':q} for v,q in zip(legacy_vs,[6,4])],'note':'按实盘确认红6黑4'})
check('legacy reconciliation preserves physical total',stock(legacy)==10)
with app.conn() as db:check('migration foreign keys intact',not db.execute('PRAGMA foreign_key_check').fetchall())
# Shortage registration may keep transit open; explicit closure completes the PO.
po2=call('POST','/api/platform/procurement/purchase-orders',{'supplierId':supplier,'items':[{'productId':last,'variantId':vid,'quantity':2,'unitCost':20}]});po2id=po2['id'];i2=po2['items'][0]['id']
call('POST',f'/api/platform/procurement/purchase-orders/{po2id}/approve');call('POST',f'/api/platform/procurement/purchase-orders/{po2id}/order')
call('POST',f'/api/platform/procurement/purchase-orders/{po2id}/receive',{'requestKey':'exception-receipt','items':[{'purchaseOrderItemId':i2,'variantId':vid,'quantity':1}]})
e={'purchaseOrderItemId':i2,'variantId':vid,'quantity':1,'exceptionType':'shortage','resolution':'pending'}
ex=call('POST',f'/api/platform/procurement/purchase-orders/{po2id}/exceptions',e)
check('pending supplier replacement keeps transit',ex['outstandingQty']==1 and ex['status']=='partially_received')
e['resolution']='closed';ex=call('POST',f'/api/platform/procurement/purchase-orders/{po2id}/exceptions',e)
check('confirmed closure completes without fake receipt',ex['outstandingQty']==0 and ex['receivedQty']==1 and ex['status']=='received')
# Multi-SKU purchase returns retain exact size and do not auto-credit.
ret=call('POST','/api/platform/procurement/purchase-returns',{'purchaseOrderId':poid,'items':[{'purchaseOrderItemId':iid,'variantId':black,'quantity':1}],'note':'黑M采购退回'})
check('purchase return stops at draft',ret['status']=='draft')
check('purchase return uses ordered SKU cost',ret['total_amount']==100)
call('POST',f'/api/platform/procurement/purchase-returns/{ret["id"]}/approve')
call('POST',f'/api/platform/procurement/purchase-returns/{ret["id"]}/ship')
with app.conn() as db:check('purchase return removes selected SKU only',db.execute('SELECT stock FROM product_variants WHERE id=?',(black,)).fetchone()[0]==8 and db.execute('SELECT stock FROM product_variants WHERE id=?',(red,)).fetchone()[0]==9)
# Restart is idempotent for SKU facts.
import db as module
module.init_db();check('restart preserves SKU counts',stock(pid)==17)
from security_v025 import platform_department_allowed
check('operations cannot count',not platform_department_allowed({'platform_department':'operations'},'/api/platform/warehouse/count','POST'))
print('INVENTORY FLOW OK',len(checks),'checks',TMP,flush=True)
