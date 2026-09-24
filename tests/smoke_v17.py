from pathlib import Path
import os, sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v17_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

def amount(v): return round(float(v),2)

h=ok('get','/api/health').json(); assert h['version'].startswith(('0.17','0.18','0.19','0.20','0.21','0.22','0.23','0.24','0.25'))
policy=ok('patch','/api/platform/commission-policy',json={'afterSalesDays':0}).json(); assert policy['afterSalesDays']==0

# 1) A paid Gear order starts pending. Delivery freezes it; maturity releases it to available.
o1=ok('post','/api/public/clubs/1/gear-orders',json={'userId':1,'items':[{'productId':1,'quantity':1}]}).json()
id1=int(o1['orderId']); comm1=amount(o1['clubCommission']); assert comm1==55.92
with app.conn() as db:
    l1=dict(db.execute("SELECT * FROM commission_ledger WHERE order_id=? AND ledger_type='earn'",(id1,)).fetchone())
assert l1['status']=='pending'

delivered=ok('patch',f'/api/platform/orders/{id1}',json={'status':'delivered'}).json()
assert delivered['commission']['status']=='frozen'
with app.conn() as db:
    l1=dict(db.execute("SELECT * FROM commission_ledger WHERE order_id=? AND ledger_type='earn'",(id1,)).fetchone())
assert l1['status']=='frozen' and l1['available_at']
rel=ok('post','/api/platform/commissions/release',json={'clubId':1}).json(); assert rel['released']>=1
preview=ok('get','/api/platform/settlements/preview?club_id=1').json()
assert amount(preview['grossAmount'])==comm1 and amount(preview['deductionAmount'])==0 and amount(preview['netAmount'])==comm1

# 2) Platform records an external payout atomically; ledger becomes settled. paymentRef is idempotent.
s1=ok('post','/api/platform/settlements',json={'clubId':1,'paymentRef':'BANK-V17-001','note':'首笔佣金结算'}).json()
assert s1['status']=='paid' and amount(s1['netAmount'])==comm1 and s1['entryCount']==1
s1dup=ok('post','/api/platform/settlements',json={'clubId':1,'paymentRef':'BANK-V17-001','note':'重复回调'}).json()
assert s1dup['id']==s1['id'] and s1dup['idempotent'] is True
with app.conn() as db:
    l1=dict(db.execute("SELECT * FROM commission_ledger WHERE order_id=? AND ledger_type='earn'",(id1,)).fetchone())
assert l1['status']=='settled' and l1['settlement_id']==s1['id'] and l1['settled_at']

# 3) Refund before settlement: pending/frozen/available commission is reversed, never payable.
o2=ok('post','/api/public/clubs/1/gear-orders',json={'userId':1,'items':[{'productId':2,'quantity':1}]}).json()
id2=int(o2['orderId']); comm2=amount(o2['clubCommission']); assert comm2==29.9
ok('patch',f'/api/platform/orders/{id2}',json={'status':'delivered'})
ok('post','/api/platform/commissions/release',json={'clubId':1})
r2=ok('post',f'/api/platform/orders/{id2}/refund',json={'reason':'结算前退款'}).json()
assert amount(r2['commissionReversed'])==comm2 and amount(r2['commissionFutureOffset'])==0
with app.conn() as db:
    earn2=dict(db.execute("SELECT * FROM commission_ledger WHERE order_id=? AND ledger_type='earn'",(id2,)).fetchone())
    rev2=dict(db.execute("SELECT * FROM commission_ledger WHERE order_id=? AND ledger_type='refund_reverse'",(id2,)).fetchone())
assert earn2['status']=='reversed' and rev2['status']=='reversed' and amount(rev2['amount'])==-comm2

# 4) Refund after settlement: preserve historical paid settlement and create next-period negative offset.
o3=ok('post','/api/public/clubs/1/gear-orders',json={'userId':1,'items':[{'productId':1,'quantity':1}]}).json()
id3=int(o3['orderId']); comm3=amount(o3['clubCommission'])
ok('patch',f'/api/platform/orders/{id3}',json={'status':'delivered'})
ok('post','/api/platform/commissions/release',json={'clubId':1})
s2=ok('post','/api/platform/settlements',json={'clubId':1,'paymentRef':'BANK-V17-002'}).json(); assert amount(s2['netAmount'])==comm3
r3=ok('post',f'/api/platform/orders/{id3}/refund',json={'reason':'结算后退款'}).json()
assert amount(r3['commissionReversed'])==comm3 and amount(r3['commissionFutureOffset'])==comm3
summary=ok('get','/api/club/1/mall/commission-summary').json()
assert amount(summary['available'])==-comm3 and amount(summary['carryDebt'])==comm3 and amount(summary['payableNow'])==0
with app.conn() as db:
    earn3=dict(db.execute("SELECT * FROM commission_ledger WHERE order_id=? AND ledger_type='earn'",(id3,)).fetchone())
    rev3=dict(db.execute("SELECT * FROM commission_ledger WHERE order_id=? AND ledger_type='refund_reverse'",(id3,)).fetchone())
assert earn3['status']=='settled' and rev3['status']=='available' and amount(rev3['amount'])==-comm3

# 5) New positive commission automatically offsets the prior negative balance in the next settlement.
o4=ok('post','/api/public/clubs/1/gear-orders',json={'userId':1,'items':[{'productId':1,'quantity':2}]}).json()
id4=int(o4['orderId']); comm4=amount(o4['clubCommission']); assert comm4==111.84
ok('patch',f'/api/platform/orders/{id4}',json={'status':'delivered'})
ok('post','/api/platform/commissions/release',json={'clubId':1})
p4=ok('get','/api/platform/settlements/preview?club_id=1').json()
assert amount(p4['grossAmount'])==comm4 and amount(p4['deductionAmount'])==comm3 and amount(p4['netAmount'])==amount(comm4-comm3)
s3=ok('post','/api/platform/settlements',json={'clubId':1,'paymentRef':'BANK-V17-003','note':'自动抵扣上期退款佣金'}).json()
assert amount(s3['grossAmount'])==comm4 and amount(s3['deductionAmount'])==comm3 and amount(s3['netAmount'])==55.92

final_summary=ok('get','/api/platform/commission-summary?club_id=1').json()
assert amount(final_summary['available'])==0 and amount(final_summary['carryDebt'])==0
settlements=ok('get','/api/club/1/mall/settlements').json(); assert len(settlements)==3
ledger=ok('get','/api/club/1/mall/commissions').json(); assert any(x['ledger_type']=='refund_reverse' for x in ledger)

print('SMOKE V0.17 OK · COMMISSION PENDING -> FROZEN -> AVAILABLE -> SETTLED / REFUND REVERSAL / NEXT-PERIOD OFFSET')
