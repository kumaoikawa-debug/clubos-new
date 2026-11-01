import os,sys,tempfile,time,secrets
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
os.environ.update(CLUBOS_TEST_OPEN='0',CLUBOS_SECURITY_MODE='demo',MOCK_AI='1',COMMERCE_PROVIDER='local',CLUBOS_DB_PATH=str(Path(tempfile.mkdtemp())/'auth.db'))
import app
from security_v025 import _hash
from fastapi.testclient import TestClient
c=TestClient(app.app);headers={}
with app.conn() as db:
 for dept in ('operations','warehouse','procurement','finance'):
  db.execute("INSERT INTO auth_accounts(username,password_hash,role,platform_department) VALUES(?,'test-only','platform',?)",('test-'+dept,dept));aid=db.execute('SELECT last_insert_rowid()').fetchone()[0];token=secrets.token_urlsafe(32)
  db.execute('INSERT INTO auth_sessions(token_hash,account_id,csrf_hash,expires_at) VALUES(?,?,?,?)',(_hash(token),aid,_hash('test'),int(time.time())+3600));headers[dept]={'Authorization':'Bearer '+token}
def check(dept,method,url,data,status):
 r=c.request(method,url,json=data,headers=headers[dept]);assert r.status_code==status,(dept,url,r.status_code,r.text[:500]);return r.json()
check('operations','PATCH','/api/platform/products/1',{'stock':999},403)
check('operations','PATCH','/api/platform/products/1',{'stock':0},403)
check('operations','POST','/api/platform/warehouse/count',{'productId':1,'locationId':1,'countedOnHand':1},403)
check('warehouse','PATCH','/api/platform/products/1',{'price':1},403)
check('finance','POST','/api/platform/warehouse/count',{},403)
check('operations','PATCH','/api/platform/products/1',{'price':123},200)
p=check('procurement','POST','/api/platform/products',{'name':'采购草稿','status':'draft','price':0},200)
check('procurement','POST',f'/api/platform/products/{p["id"]}/variants',{'name':'红M','stock':0},200)
check('procurement','POST',f'/api/platform/products/{p["id"]}/variants',{'name':'黑M','stock':9},403)
check('warehouse','GET','/api/platform/warehouse/inventory',None,200)
print('INVENTORY PERMISSIONS OK 10 real authenticated request checks')
