from pathlib import Path
import os, sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v18_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

def amount(v): return round(float(v or 0),2)

h=ok('get','/api/health').json(); assert h['version'].startswith(('0.18','0.19','0.20','0.21','0.22','0.23','0.24','0.25'))

# A) refund-only partial after-sales: one unit out of a two-unit order.
o=ok('post','/api/public/clubs/1/gear-orders',json={'userId':1,'items':[{'productId':1,'quantity':2}]}).json()
oid=int(o['orderId'])
with app.conn() as db:
    item=dict(db.execute('SELECT * FROM gear_order_items WHERE order_id=?',(oid,)).fetchone())
    stock_before=int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])
case=ok('post',f'/api/public/orders/{oid}/after-sales',json={
    'userId':1,'type':'refund_only','reason':'其中一件有瑕疵','evidenceUrls':['https://example.invalid/photo.jpg'],
    'items':[{'orderItemId':item['id'],'quantity':1}]
}).json()
cid=case['id']; assert case['status']=='pending_review' and amount(case['requested_goods_amount'])==699
approved=ok('post',f'/api/platform/after-sales/{cid}/approve',json={}).json(); assert approved['status']=='approved_pending_refund'
start=ok('post',f'/api/platform/after-sales/{cid}/refund').json(); assert start['status']=='succeeded'
with app.conn() as db:
    rr=dict(db.execute('SELECT * FROM refund_requests WHERE after_sales_case_id=?',(cid,)).fetchone())
    rid=rr['id']; assert rr['refund_scope']=='after_sales' and amount(rr['cash_amount'])==699
assert start['result']['orderFullyRefunded'] is False
with app.conn() as db:
    order=dict(db.execute('SELECT * FROM gear_orders WHERE id=?',(oid,)).fetchone())
    stock_after=int(db.execute('SELECT stock FROM products WHERE id=1').fetchone()[0])
    neg=amount(db.execute("SELECT COALESCE(SUM(amount),0) FROM commission_ledger WHERE order_id=? AND ledger_type='after_sales_reverse'",(oid,)).fetchone()[0])
assert order['status']=='paid' and order['refund_status']=='partial' and amount(order['refunded_goods_total'])==699
assert stock_after==stock_before  # refund-only does not restock
assert neg==-55.92

# B) return + refund: return logistics -> platform receives -> stock restored -> full refund.
o2=ok('post','/api/public/clubs/1/gear-orders',json={'userId':1,'items':[{'productId':2,'quantity':1}]}).json(); oid2=int(o2['orderId'])
with app.conn() as db:
    item2=dict(db.execute('SELECT * FROM gear_order_items WHERE order_id=?',(oid2,)).fetchone())
    pstock_before=int(db.execute('SELECT stock FROM products WHERE id=2').fetchone()[0])
case2=ok('post',f'/api/public/orders/{oid2}/after-sales',json={'userId':1,'type':'return_refund','reason':'不合适','items':[{'orderItemId':item2['id'],'quantity':1}]}).json(); cid2=case2['id']
a2=ok('post',f'/api/platform/after-sales/{cid2}/approve',json={}).json(); assert a2['status']=='awaiting_return'
rship=ok('post',f'/api/public/after-sales/{cid2}/return-shipment',json={'carrier':'顺丰','trackingNo':'SFV18001'}).json(); assert rship['status']=='return_in_transit'
recv=ok('post',f'/api/platform/after-sales/{cid2}/receive-return',json={'restock':True}).json(); assert recv['status']=='approved_pending_refund'
with app.conn() as db: assert int(db.execute('SELECT stock FROM products WHERE id=2').fetchone()[0])==pstock_before+1
start2=ok('post',f'/api/platform/after-sales/{cid2}/refund').json(); assert start2['status']=='succeeded' and start2['result']['orderFullyRefunded'] is True
with app.conn() as db:
    order2=dict(db.execute('SELECT * FROM gear_orders WHERE id=?',(oid2,)).fetchone())
    case2db=dict(db.execute('SELECT * FROM after_sales_cases WHERE id=?',(cid2,)).fetchone())
assert order2['status']=='refunded' and order2['after_sales_status']=='refunded' and case2db['status']=='refunded'

# C) exchange flow never creates a refund; returned goods may restock and replacement gets outbound tracking.
o3=ok('post','/api/public/clubs/1/gear-orders',json={'userId':1,'items':[{'productId':3,'quantity':1}]}).json(); oid3=int(o3['orderId'])
with app.conn() as db:
    item3=dict(db.execute('SELECT * FROM gear_order_items WHERE order_id=?',(oid3,)).fetchone())
case3=ok('post',f'/api/public/orders/{oid3}/after-sales',json={'userId':1,'type':'exchange','reason':'换一个颜色','items':[{'orderItemId':item3['id'],'quantity':1}]}).json(); cid3=case3['id']
a3=ok('post',f'/api/platform/after-sales/{cid3}/approve',json={}).json(); assert a3['status']=='awaiting_return' and amount(a3['approved_refund_amount'])==0
ok('post',f'/api/public/after-sales/{cid3}/return-shipment',json={'carrier':'京东物流','trackingNo':'JDRET18'})
rec3=ok('post',f'/api/platform/after-sales/{cid3}/receive-return',json={'restock':True}).json(); assert rec3['status']=='exchange_pending_shipment'
ship3=ok('post',f'/api/platform/after-sales/{cid3}/exchange-ship',json={'carrier':'京东物流','trackingNo':'JDEX18'}).json(); assert ship3['status']=='completed' and ship3['exchange_tracking_no']=='JDEX18'
with app.conn() as db:
    assert db.execute('SELECT COUNT(*) FROM refund_requests WHERE after_sales_case_id=?',(cid3,)).fetchone()[0]==0
    o3db=dict(db.execute('SELECT * FROM gear_orders WHERE id=?',(oid3,)).fetchone())
assert o3db['status']=='paid' and o3db['after_sales_status']=='completed'

# D) platform and club visibility: platform can operate; club is read-only and sees only attributed cases.
plat=ok('get','/api/platform/after-sales').json(); assert {cid,cid2,cid3}.issubset({x['id'] for x in plat})
club=ok('get','/api/club/1/mall/after-sales').json(); assert {cid,cid2,cid3}.issubset({x['id'] for x in club})
usr=ok('get','/api/public/users/1/after-sales').json(); assert {cid,cid2,cid3}.issubset({x['id'] for x in usr})

print('SMOKE V0.18 OK · REFUND-ONLY / RETURN-REFUND / EXCHANGE / PARTIAL REVERSAL / RETURN LOGISTICS / RESTOCK / PLATFORM-OWNED AFTER-SALES')
