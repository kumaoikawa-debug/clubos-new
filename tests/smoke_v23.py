from pathlib import Path
import os,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1';os.environ['COMMERCE_PROVIDER']='local';os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v23_test.db')
try:Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError:pass
import app
from fastapi.testclient import TestClient
c=TestClient(app.app)
def ok(m,u,**kw):
 r=getattr(c,m)(u,**kw);assert r.status_code<400,(u,r.status_code,r.text);return r
def bad(m,u,code,**kw):
 r=getattr(c,m)(u,**kw);assert r.status_code==code,(u,r.status_code,r.text);return r
assert ok('get','/api/health').json()['version'].startswith(('0.23','0.24','0.25'))
# Commercial catalogs exist and are platform-editable.
plans=ok('get','/api/platform/ai-credit/plans').json();assert {x['code'] for x in plans}>={'starter','pro','enterprise'}
packs=ok('get','/api/platform/ai-credit/topup-packages').json();assert any(x['code']=='T1000' for x in packs)
pricing=ok('patch','/api/platform/ai-credit/task-pricing',json={'detail':30,'wechat':12,'xhs':8,'poster':6,'recap':16}).json();assert pricing['detail']==30
# Public self-application is pending-only and exposes status without opening operating access.
applic=ok('post','/api/public/club-applications',json={'name':'v23公开申请','contactName':'申请人','contactPhone':'13800000023','city':'成都'}).json();assert applic['status']=='pending'
assert ok('get',f"/api/public/club-applications/{applic['applicationId']}").json()['status']=='pending'
# Onboarding creates PENDING club with no operating rights.
club=ok('post','/api/platform/clubs',json={'name':'v23待审核俱乐部','contactName':'测试负责人','contactPhone':'13900000023','city':'成都','applicationNote':'v23 test'}).json();cid=int(club['id']);assert club['status']=='pending'
bad('post',f'/api/club/{cid}/activities/ai-generate',403,data={'prompt':'测试活动'})
# Assigning a plan creates a billing order, but DOES NOT grant credits before payment.
assigned=ok('patch',f'/api/platform/clubs/{cid}/plan',json={'planCode':'starter','autoRenew':True}).json();bill=assigned['billingOrder'];assert bill['status']=='pending' and bill['credits']==3000
with app.conn() as db: assert int(db.execute('SELECT balance FROM ai_credit_accounts WHERE club_id=?',(cid,)).fetchone()[0])==0
# Approval enables operations. Payment confirmation grants the subscription credits exactly once.
ok('patch',f'/api/platform/clubs/{cid}/status',json={'status':'active'})
paid=ok('post',f"/api/platform/ai-credit/orders/{bill['id']}/confirm-paid",json={'paymentRef':'V23-SUB-001'}).json();assert paid['grant']['granted']==3000 and paid['grant']['netToBalance']==3000
idem=ok('post',f"/api/platform/ai-credit/orders/{bill['id']}/confirm-paid",json={'paymentRef':'V23-SUB-001'}).json();assert idem['idempotent'] is True
# AI succeeds at full mock capability and charges only after successful generation.
before=ok('get',f'/api/club/{cid}/credits').json()['account']['balance']
r=ok('post',f'/api/club/{cid}/activities/ai-generate',data={'prompt':'2026年11月8日成都周边轻徒步，20人，199元'}).json();assert r['activityId']>0
after=ok('get',f'/api/club/{cid}/credits').json()['account']['balance'];assert before-after==30
# A negative platform adjustment never silently drives wallet below zero; remainder becomes auditable debt.
adj=ok('post','/api/platform/credits/adjust',json={'clubId':cid,'amount':-3500,'note':'v23欠账测试'}).json();assert adj['debtCreated']>0
state=ok('get',f'/api/club/{cid}/credits').json();assert state['account']['balance']==0 and state['unresolvedDebt']==530
# Future top-up is granted through a paid order; debt is recovered first and only the net enters available balance.
topup=ok('post',f'/api/club/{cid}/credits/topups',json={'packageCode':'T1000'}).json();assert topup['status']=='pending' and topup['credits']==1000
tr=ok('post',f"/api/platform/ai-credit/orders/{topup['id']}/confirm-paid",json={'paymentRef':'V23-TOP-001'}).json();assert tr['grant']['debtRecovered']==530 and tr['grant']['netToBalance']==470
state=ok('get',f'/api/club/{cid}/credits').json();assert state['account']['balance']==470 and state['unresolvedDebt']==0
assert any(x['type']=='debt_recovery' and x['amount']==-530 for x in state['ledger'])
# Monthly roll is idempotent for subscription+period; no duplicate monthly bill is created.
roll=ok('post','/api/platform/ai-credit/monthly-roll',json={}).json();assert any(int(x['club_id'])==cid for x in roll['orders'])
with app.conn() as db:
 period=app.ai_credit_engine._month_key();cnt=int(db.execute("SELECT COUNT(*) FROM ai_credit_orders WHERE club_id=? AND order_type='subscription' AND period_key=?",(cid,period)).fetchone()[0]);assert cnt==1
# Club statement hides provider cost; platform retains cost/revenue observability.
club_stmt=ok('get',f'/api/club/{cid}/credits/statement').json();assert 'providerCostUsd' not in club_stmt and club_stmt['creditsConsumed']>=30
platform_stmt=ok('get',f'/api/platform/ai-credit/clubs/{cid}/statement').json();assert 'providerCostUsd' in platform_stmt and platform_stmt['cashRevenueCny']>=398
summary=ok('get','/api/platform/ai-credit/summary').json();assert summary['cashRevenueCny']>=398 and summary['activeSubscriptions']>=1
# Disable pauses new AI usage/top-up creation while preserving historical balance and statements.
ok('patch',f'/api/platform/clubs/{cid}/status',json={'status':'disabled','reason':'v23停用测试'})
bad('post',f'/api/club/{cid}/activities/ai-generate',403,data={'prompt':'不应执行'})
bad('post',f'/api/club/{cid}/credits/topups',403,json={'packageCode':'T1000'})
stmt=ok('get',f'/api/club/{cid}/credits/statement').json();assert stmt['account']['balance']==470
print('SMOKE V0.23 OK · CLUB ONBOARDING / PLAN BILLING / TOPUP / DEBT RECOVERY / AI CHARGE / COST+REVENUE OBSERVABILITY / DISABLE GATE')
