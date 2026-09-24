"""Contract test for real club-scoped BI, cross-period refunds, and non-leaking data."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import json, os, sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.update(MOCK_AI='1',COMMERCE_PROVIDER='local',CLUBOS_DB_PATH=str(ROOT/'clubos_v24_test.db'))
Path(os.environ['CLUBOS_DB_PATH']).unlink(missing_ok=True)
import app
from fastapi.testclient import TestClient
client=TestClient(app.app)
now=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
old=(datetime.now(timezone.utc)-timedelta(days=50)).strftime('%Y-%m-%d %H:%M:%S')

def run(sql,args=()):
    with app.conn() as db:
        cursor=db.execute(sql,args)
        return cursor.lastrowid

def get(cid,days=30):
    x=client.get(f'/api/club/{cid}/analytics?windowDays={days}')
    assert x.status_code==200,x.text
    return x.json()

with app.conn() as c:
    c.execute("INSERT INTO clubs(name,status) VALUES('BI Club B PRIVATE','active')")
    cid2=int(c.execute('SELECT last_insert_rowid()').fetchone()[0]);cid1=1
    user=int(c.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()[0])
    c.execute("INSERT INTO activities(club_id,title,status,price,activity_master_json,detail_json) VALUES(?,?,?,?,?,?)",(cid1,'CLUB A EXCLUSIVE','published',100,'{}','{}'))
    a1=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
    c.execute("INSERT INTO activities(club_id,title,status,price,activity_master_json,detail_json) VALUES(?,?,?,?,?,?)",(cid2,'PRIVATE CLUB B ACTIVITY','published',9900,'{}','{}'))
    a2=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
    def reg(cid,aid,amount,created):
        c.execute("INSERT INTO registrations(activity_id,club_id,user_id,status,amount,original_amount,created_at) VALUES(?,?,?,'paid',?,?,?)",(aid,cid,user,amount,amount,created))
        return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
    past=reg(cid1,a1,60,old)
    first=reg(cid1,a1,100,now)
    second=reg(cid1,a1,200,now)
    foreign=reg(cid2,a2,9900,now)
    c.execute("INSERT INTO refund_requests(id,kind,registration_id,user_id,club_id,status,cash_amount,refunded_at) VALUES('bi-old','activity',?,?,?,'succeeded',20,?)",(past,user,cid1,now))
    c.execute("INSERT INTO refund_requests(id,kind,registration_id,user_id,club_id,status,cash_amount,refunded_at) VALUES('bi-partial','activity',?,?,?,'succeeded',25,?)",(first,user,cid1,now))
    c.execute("INSERT INTO refund_requests(id,kind,registration_id,user_id,club_id,status,cash_amount) VALUES('bi-pending','activity',?,?,?,'requested',5000)",(second,user,cid1))
    c.execute("INSERT INTO refund_requests(id,kind,registration_id,user_id,club_id,status,cash_amount,refunded_at) VALUES('bi-foreign','activity',?,?,?,'succeeded',9000,?)",(foreign,user,cid2,now))
    c.execute("INSERT INTO gear_orders(user_id,source_club_id,total,cash_paid,created_at) VALUES(?,?,?,?,?)",(user,cid1,80,80,now)); g1=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
    c.execute("INSERT INTO gear_orders(user_id,source_club_id,total,cash_paid,created_at) VALUES(?,?,?,?,?)",(user,cid2,9999,9999,now)); g2=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
    c.execute("INSERT INTO commission_ledger(club_id,order_id,amount,ledger_type,created_at) VALUES(?,?,?,?,?)",(cid1,g1,8,'earn',now))
    c.execute("INSERT INTO commission_ledger(club_id,order_id,amount,ledger_type,created_at) VALUES(?,?,?,?,?)",(cid1,g1,-2,'after_sales_reverse',now))
    c.execute("INSERT INTO commission_ledger(club_id,order_id,amount,ledger_type,created_at) VALUES(?,?,?,?,?)",(cid2,g2,1000,'earn',now))
    c.execute("INSERT INTO ai_usage_records(club_id,task_type,request_id,provider,model,status,provider_cost,credits_charged,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(cid1,'detail','bi-ai1','mock','mock','success',0.01,30,now))
    c.execute("INSERT INTO ai_usage_records(club_id,task_type,request_id,provider,model,status,provider_cost,credits_charged,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(cid2,'detail','bi-ai2','mock','mock','success',999,1000,now))
    c.execute("INSERT INTO ai_credit_ledger(club_id,type,amount,created_at) VALUES(?,'consume',-30,?)",(cid1,now))
    c.execute("INSERT INTO ai_credit_ledger(club_id,type,amount,created_at) VALUES(?,'consume',-1000,?)",(cid2,now))

x=get(1)
assert x['activity']['bookingOrders']==2,x
assert x['activity']['paidCashInWindow']==300,x
assert x['activity']['confirmedRefundCashInWindow']==45,x
assert x['activity']['netCashFlowInWindow']==255,x
assert x['activity']['bookingCohortRefundCash']==25,x
assert x['activity']['bookingCohortNetCash']==275,x
assert x['activity']['repeatPayingUsers']==1,x
assert x['activity']['partialRefundOrders']==1,x
assert x['commerce']['attributedGMV']==80,x
assert x['commerce']['commissionNetMovement']==6,x
assert x['ai']['creditsConsumedInWindow']==30,x
assert x['ai']['successfulCalls']==1,x
assert len(x['activities'])==1 and x['activities'][0]['title']=='CLUB A EXCLUSIVE',x
assert all('providerCost' not in str(v) for v in x.keys())
assert 'PRIVATE CLUB B' not in json.dumps(x) and '9999' not in json.dumps(x)
y=get(cid2)
assert y['activity']['paidCashInWindow']==9900 and y['commerce']['attributedGMV']==9999
assert 'CLUB A EXCLUSIVE' not in json.dumps(y)
assert get(1,7)['activity']['confirmedRefundCashInWindow']==45
for wrong in ('0','6','366','abc'):
    res=client.get(f'/api/club/1/analytics?windowDays={wrong}')
    assert res.status_code in (400,422),res.text
assert client.get('/api/club/99999/analytics').status_code==404
print('SMOKE V0.24 OK · CLUB-SCOPED BI / CROSS-PERIOD REFUNDS / COHORT / PARTIAL REFUND / COMMISSION / AI COST PRIVACY / TENANT FILTERS')
