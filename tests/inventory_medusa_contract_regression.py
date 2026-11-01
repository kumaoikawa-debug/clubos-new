"""Offline contract checks only; this is not a real Medusa E2E test."""
import os,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
os.environ.update(CLUBOS_TEST_OPEN='1',MOCK_AI='1',COMMERCE_PROVIDER='local',CLUBOS_DB_PATH=str(Path(tempfile.mkdtemp())/'medusa-contract.db'))
import app
with app.conn() as c:
 c.execute("UPDATE product_variants SET commerce_variant_id='remote-A' WHERE product_id=1")
 c.execute("UPDATE product_variants SET commerce_variant_id='remote-B' WHERE product_id=2")
 va=c.execute('SELECT id FROM product_variants WHERE product_id=1').fetchone()[0];vb=c.execute('SELECT id FROM product_variants WHERE product_id=2').fetchone()[0]
 c.execute("INSERT INTO gear_orders(user_id,source_club_id,total,status,commerce_order_id,tracking_no) VALUES(1,1,100,'shipped','remote-order','TEST-TRACK')");oid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
 c.executemany('INSERT INTO gear_order_items(order_id,product_id,variant_id,quantity,unit_price) VALUES(?,?,?,?,10)',[(oid,1,va,2),(oid,2,vb,3)])
remote={'items':[{'id':'line-A','variant_id':'remote-A','quantity':2},{'id':'line-B','variant_id':'remote-B','quantity':3}],'fulfillments':[]}
assert app._medusa_fulfillment_items({'id':oid},remote)==[{'id':'line-A','quantity':2},{'id':'line-B','quantity':3}]
class FakeMedusa:
 fail=True
 def get_admin_order(self,oid):return remote
 def create_fulfillment(self,oid,items):
  f={'id':'fulfillment-1'};remote['fulfillments']=[f];return {'fulfillment':f}
 def create_shipment(self,oid,fid,items,**kw):
  if self.fail:raise RuntimeError('offline injected failure')
  remote['fulfillments'][0]['shipped_at']='test-confirmed'
app.commerce_provider=lambda:'medusa';app.MedusaClient=FakeMedusa
assert app._sync_order_shipment_to_medusa(oid)['status']=='error'
with app.conn() as c:assert c.execute('SELECT status FROM gear_orders WHERE id=?',(oid,)).fetchone()[0]=='shipped'
FakeMedusa.fail=False
assert app._sync_order_shipment_to_medusa(oid)['status']=='synced'
assert app._sync_order_shipment_to_medusa(oid)['status']=='synced'
print('MEDUSA CONTRACT OK 4 checks; offline fake only')
