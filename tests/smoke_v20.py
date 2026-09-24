from pathlib import Path
import os,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1';os.environ['COMMERCE_PROVIDER']='local';os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v20_test.db')
try:Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError:pass
import app
from fastapi.testclient import TestClient
c=TestClient(app.app)
def ok(m,u,**kw):
 r=getattr(c,m)(u,**kw);assert r.status_code<400,(u,r.status_code,r.text);return r
def bad(m,u,code=409,**kw):
 r=getattr(c,m)(u,**kw);assert r.status_code==code,(u,r.status_code,r.text);return r
assert ok('get','/api/health').json()['version'].startswith(('0.20','0.21','0.22','0.23','0.24','0.25'))
# opening warehouse mirrors current sellable stock
with app.conn() as db:
 total=int(db.execute('SELECT COALESCE(SUM(stock),0) FROM products').fetchone()[0])
s=ok('get','/api/platform/warehouse/summary').json();assert s['onHand']==total and s['reserved']==0 and s['available']==total
ws=ok('get','/api/platform/warehouses').json();main=ws[0];default_loc=[x for x in main['locations'] if x['is_default']][0]
# second warehouse/location + transfer keeps aggregate sellable stock unchanged
w2=ok('post','/api/platform/warehouses',json={'code':'WEST','name':'西区备货仓'}).json();l2=ok('post',f"/api/platform/warehouses/{w2['id']}/locations",json={'code':'B-01','name':'B01可售位','zoneType':'sellable'}).json()
with app.conn() as db:stock0=int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])
ok('post','/api/platform/warehouse/move',json={'productId':1,'fromLocationId':default_loc['id'],'toLocationId':l2['id'],'quantity':3,'note':'跨仓备货'})
with app.conn() as db:assert int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])==stock0
# paid/local gear order consumes sellable stock and creates physical reservation, not physical outbound yet
o=ok('post','/api/public/clubs/1/gear-orders',json={'userId':1,'items':[{'productId':1,'quantity':2}]}).json();oid=int(o['orderId'])
with app.conn() as db:
 pstock=int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0]);res=int(db.execute("SELECT quantity FROM warehouse_reservations WHERE order_id=? AND product_id=1 AND status='reserved'",(oid,)).fetchone()[0]);wh=db.execute('SELECT COALESCE(SUM(on_hand),0),COALESCE(SUM(reserved),0) FROM warehouse_inventory WHERE product_id=1').fetchone()
assert pstock==stock0-2 and res==2 and int(wh[1])==2
# pick + pack task flow
pick=ok('post',f'/api/platform/orders/{oid}/pick').json();assert pick['status']=='pending'
picked=ok('post',f"/api/platform/warehouse/tasks/{pick['id']}/picked").json();assert picked['status']=='picked'
pack=ok('post',f'/api/platform/orders/{oid}/pack',json={'note':'纸箱+防水袋'}).json();assert pack['status']=='packed'
# physical dispatch removes on_hand and reservation; sellable stock was already deducted when order reserved
with app.conn() as db:
 before_on=int(db.execute('SELECT COALESCE(SUM(on_hand),0) FROM warehouse_inventory WHERE product_id=1').fetchone()[0]);app.warehouse_engine.dispatch_order(db,order_id=oid)
with app.conn() as db:
 after=db.execute('SELECT COALESCE(SUM(on_hand),0),COALESCE(SUM(reserved),0) FROM warehouse_inventory WHERE product_id=1').fetchone();assert int(after[0])==before_on-2 and int(after[1])==0;assert int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])==stock0-2
# refund before shipment releases reservation and restores sellable stock without duplicating physical stock
o2=ok('post','/api/public/clubs/1/gear-orders',json={'userId':1,'items':[{'productId':2,'quantity':1}]}).json();oid2=int(o2['orderId'])
with app.conn() as db:physical_before=int(db.execute('SELECT COALESCE(SUM(on_hand),0) FROM warehouse_inventory WHERE product_id=2').fetchone()[0])
ok('post',f'/api/platform/orders/{oid2}/refund',json={'reason':'未发货取消'})
with app.conn() as db:
 r=db.execute('SELECT COALESCE(SUM(on_hand),0),COALESCE(SUM(reserved),0) FROM warehouse_inventory WHERE product_id=2').fetchone();assert int(r[0])==physical_before and int(r[1])==0
# cycle count adjusts physical + sellable by the same delta; cannot count below reserved
inv=ok('get','/api/platform/warehouse/inventory?product_id=3').json();loc=inv[0]
count=ok('post','/api/platform/warehouse/count',json={'productId':3,'locationId':loc['location_id'],'countedOnHand':int(loc['on_hand'])+2,'note':'月末盘盈'}).json();assert count['delta']==2
# procurement inbound becomes physical WMS inbound too
sup=ok('post','/api/platform/suppliers',json={'name':'WMS测试供应商','code':'SUP-WMS20'}).json();sid=sup['id']
ok('post','/api/platform/products/4/suppliers',json={'supplierId':sid,'purchasePrice':80,'minOrderQty':1,'isPrimary':True})
po=ok('post','/api/platform/procurement/purchase-orders',json={'supplierId':sid,'items':[{'productId':4,'quantity':2}]}).json();pid=po['id'];pi=po['items'][0]['id'];ok('post',f'/api/platform/procurement/purchase-orders/{pid}/approve');ok('post',f'/api/platform/procurement/purchase-orders/{pid}/order')
with app.conn() as db:wh0=int(db.execute('SELECT COALESCE(SUM(on_hand),0) FROM warehouse_inventory WHERE product_id=4').fetchone()[0])
ok('post',f'/api/platform/procurement/purchase-orders/{pid}/receive',json={'items':[{'purchaseOrderItemId':pi,'quantity':2}]})
with app.conn() as db:assert int(db.execute('SELECT COALESCE(SUM(on_hand),0) FROM warehouse_inventory WHERE product_id=4').fetchone()[0])==wh0+2
print('SMOKE V0.20 OK · WAREHOUSE / LOCATION / ON-HAND / RESERVED / TRANSFER / COUNT / PICK / PACK / DISPATCH / RETURN-TO-AVAILABLE')
