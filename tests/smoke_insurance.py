from pathlib import Path
from datetime import datetime, timedelta
import os, sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ['INSURANCE_PROVIDER']='mock'
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_insurance_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
from clubos_domain.insurance import InsuranceOrchestrator, effective_window
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

def dbrows(sql,*a):
    with app.conn() as dbc:
        return [dict(r) for r in dbc.execute(sql,a).fetchall()]

# ---- 1. 活动 + 未来团期 + 报名支付 → 自动投保 ----
aid=ok('get','/api/public/clubs/1/activities').json()[0]['id']
start=(datetime.now()+timedelta(days=3)).replace(microsecond=0).isoformat(sep=' ')
oid=ok('post',f'/api/club/1/activities/{aid}/occurrences',json={'startAt':start,'price':260,'capacity':6,'label':'保险自动化-未来团'}).json()['id']
ok('post',f'/api/club/1/activities/{aid}/publish')
participants=[{'name':'保险甲','phone':'13830000001','relationToPayer':'本人','idType':'身份证','idNumber':'510100199001010304','emergencyContactName':'甲家属','emergencyContactPhone':'13930000001'}]
co=ok('post',f'/api/public/activities/{aid}/checkout',json={'name':'保险甲','phone':'13830000001','occurrenceId':oid,'participants':participants}).json()
paid=ok('post',f"/api/public/checkouts/{co['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()['result']
rid=paid['registrationId']

ps=dbrows('SELECT * FROM registration_participants WHERE registration_id=?',rid)
assert len(ps)==1, ps
p=ps[0]
assert p['insurance_status']=='insured', p['insurance_status']
assert (p['insurance_policy_no'] or '').startswith('MOCK-POL-'), p['insurance_policy_no']
exp_eff=datetime.fromisoformat(start).isoformat()
exp_exp=(datetime.fromisoformat(start)+timedelta(days=1)).isoformat()
assert p['effective_at']==exp_eff, (p['effective_at'],exp_eff)      # 生效窗口对齐团期出发
assert p['expire_at']==exp_exp, (p['expire_at'],exp_exp)
assert float(p['premium_amount'] or 0)>0
jobs=dbrows("SELECT * FROM insurance_jobs WHERE registration_id=? AND action='enroll'",rid)
assert len(jobs)==1 and jobs[0]['status']=='done'

# ---- 2. 全额退款 → 自动退保（出发前，俱乐部承担保费 → 不退保费）----
req=ok('post',f'/api/public/registrations/{rid}/refund-request',json={'reason':'测试退款'}).json()
rfid=req['refundRequestId']
ok('post',f'/api/club/1/refunds/{rfid}/approve')
# local 渠道需模拟渠道回调完成 finalize（真实链路：保险随退款 finalize 自动退保）
ok('post','/api/commerce/events/refund-succeeded',json={'eventKey':'ins-test-1','refundRequestId':rfid,'providerRefundId':'prov-1'})
p2=dbrows('SELECT * FROM registration_participants WHERE registration_id=?',rid)[0]
assert p2['insurance_status']=='cancelled', p2['insurance_status']
assert float(p2['premium_refunded'] or 0)==0.0  # club 承担，不退保费
cjobs=dbrows("SELECT * FROM insurance_jobs WHERE registration_id=? AND action='cancel'",rid)
assert len(cjobs)==1 and cjobs[0]['status']=='done'

# ---- 3. 出发后不退保（已出发团期退款 → skipped，保险保留）----
start_past=(datetime.now()-timedelta(days=3)).replace(microsecond=0).isoformat(sep=' ')
oid2=ok('post',f'/api/club/1/activities/{aid}/occurrences',json={'startAt':start_past,'price':260,'capacity':6,'label':'保险自动化-过去团'}).json()['id']
ok('post',f'/api/club/1/activities/{aid}/publish')
co2=ok('post',f'/api/public/activities/{aid}/checkout',json={'name':'保险乙','phone':'13830000002','occurrenceId':oid2,'participants':[{'name':'保险乙','phone':'13830000002','relationToPayer':'本人','idType':'身份证','idNumber':'510100199001010312','emergencyContactName':'乙家属','emergencyContactPhone':'13930000002'}]}).json()
rid2=ok('post',f"/api/public/checkouts/{co2['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()['result']['registrationId']
with app.conn() as dbc:
    occ2=dict(dbc.execute('SELECT * FROM activity_occurrences WHERE id=?',(oid2,)).fetchone())
    p2b=dict(dbc.execute('SELECT * FROM registration_participants WHERE registration_id=?',(rid2,)).fetchone())
    res=InsuranceOrchestrator().cancel_for_participant(dbc, participant=p2b, occurrence=occ2, bearer='club')
assert res.get('skipped') is True and res.get('reason')=='departed', res
p2c=dbrows('SELECT * FROM registration_participants WHERE registration_id=?',rid2)[0]
assert p2c['insurance_status']=='insured', p2c['insurance_status']  # 已出发 → 不退保

# ---- 4. Webhook 契约：无匹配保单幂等；坏参数拒绝；secret 校验 ----
wh=ok('post','/api/insurance/webhook',json={'policyNo':'NONEXIST-POL','action':'enroll','status':'success'})
assert wh.json().get('idempotent') is True
r400=c.post('/api/insurance/webhook',json={'policyNo':'X'})  # 缺 action/status
assert r400.status_code==400, r400.status_code
os.environ['INSURANCE_WEBHOOK_SECRET']='topsecret'
r401=c.post('/api/insurance/webhook',json={'policyNo':'X','action':'enroll','status':'success'})
assert r401.status_code==401, r401.status_code

print('SMOKE INSURANCE OK · 自动投保 / 自动退保(出发前) / 出发后不退 / Webhook契约')
