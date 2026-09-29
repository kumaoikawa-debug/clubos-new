from pathlib import Path
from datetime import datetime, timedelta
import os, sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v13_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

h=ok('get','/api/health').json(); assert h['version'].startswith(('0.13','0.14','0.15','0.16','0.17','0.18','0.19','0.20','0.21','0.22','0.23','0.24','0.25'))
aid=ok('get','/api/public/clubs/1/activities').json()[0]['id']
# execution-test occurrence
start=(datetime.now()+timedelta(days=3)).replace(microsecond=0).isoformat(sep=' ')
oid=ok('post',f'/api/club/1/activities/{aid}/occurrences',json={'startAt':start,'price':260,'capacity':6,'label':'v0.13执行测试团'}).json()['id']
ok('post',f'/api/club/1/activities/{aid}/publish')

participants=[
 {'name':'执行甲','phone':'13810000001','relationToPayer':'本人','idType':'身份证','idNumber':'510100199001010001','emergencyContactName':'甲家属','emergencyContactPhone':'13910000001'},
 {'name':'执行乙','phone':'13810000002','relationToPayer':'朋友','idType':'身份证','idNumber':'510100199001010002','emergencyContactName':'乙家属','emergencyContactPhone':'13910000002'},
]
co=ok('post',f'/api/public/activities/{aid}/checkout',json={'name':'执行甲','phone':'13810000001','occurrenceId':oid,'participants':participants}).json()
paid=ok('post',f"/api/public/checkouts/{co['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()['result']
rid=paid['registrationId']
ps=ok('get',f'/api/public/registrations/{rid}/participants').json()['participants']
assert len(ps)==2 and all(x['form_status']=='complete' for x in ps)

# setup meeting/leader/groups
st=ok('patch',f'/api/club/1/occurrences/{oid}/execution/settings',json={'meetingTime':'07:50','meetingLocation':'万象城门店','emergencyPhone':'400-000-001','leaderNote':'核对保险与人数后再出发'}).json()
assert st['meeting_location']=='万象城门店'
leader=ok('post',f'/api/club/1/occurrences/{oid}/leaders',json={'name':'领队小野','phone':'13820000001','role':'主领队'}).json()
assert leader['name']=='领队小野'
car1=ok('post',f'/api/club/1/occurrences/{oid}/groups',json={'groupType':'vehicle','name':'1号车','capacity':2,'leaderName':'司机A'}).json()
grp=ok('post',f'/api/club/1/occurrences/{oid}/groups',json={'groupType':'leader','name':'A组','capacity':6,'leaderName':'领队小野'}).json()
for p in ps:
    ok('post',f"/api/club/1/execution-groups/{car1['id']}/assign",json={'participantId':p['id']})
    ok('post',f"/api/club/1/execution-groups/{grp['id']}/assign",json={'participantId':p['id']})

# 保险自动化：支付成功时已自动投保（无需手工批量提交）；逐人保单号仍可由手动覆盖
with app.conn() as dbc:
    ps_auto=[dict(r) for r in dbc.execute('SELECT * FROM registration_participants WHERE registration_id=?',(rid,)).fetchall()]
assert all(x['insurance_status']=='insured' for x in ps_auto),[x['insurance_status'] for x in ps_auto]
assert all((x.get('insurance_policy_no') or '').startswith('MOCK-POL-') for x in ps_auto)
# 手动批量投保作为「投保失败」重试兜底：先把一人置 failed 再批量重投
ok('patch',f"/api/club/1/participants/{ps[0]['id']}/insurance",json={'status':'failed'})
batch=ok('post',f'/api/club/1/occurrences/{oid}/insurance/batch-submit',json={'participantIds':[ps[0]['id']]}).json()
assert batch['updated']==1 and batch['failed']==0
for i,p in enumerate(ps,1):
    ok('patch',f"/api/club/1/participants/{p['id']}/insurance",json={'status':'insured','provider':'示例户外险','policyNo':f'V13-{i:03d}'})

# notice lifecycle
n=ok('post',f'/api/club/1/occurrences/{oid}/notices',json={'title':'集合通知','content':'07:50门店集合，请勿迟到。','channel':'wechat'}).json()
assert n['status']=='draft'
n2=ok('post',f"/api/club/1/occurrences/{oid}/notices/{n['id']}/send").json()
assert n2['status']=='sent'

# check-in + no show using club/leader sides
ok('patch',f"/api/club/1/occurrences/{oid}/participants/{ps[0]['id']}/checkin",json={'status':'checked_in'})
ok('patch',f"/api/leader/occurrences/{oid}/participants/{ps[1]['id']}/checkin?club_id=1",json={'status':'no_show','note':'电话确认未到'})

d=ok('get',f'/api/club/1/occurrences/{oid}/execution').json()
assert d['summary']['participantCount']==2
assert d['summary']['incompleteCount']==0
assert d['summary']['insurancePendingCount']==0
assert d['summary']['checkedInCount']==1
assert d['summary']['noShowCount']==1
assert d['summary']['unassignedVehicleCount']==0
assert len(d['leaders'])==1 and len(d['groups'])==2 and len(d['notices'])==1

# leader brief excludes commerce but includes execution necessities
lb=ok('get',f'/api/leader/occurrences/{oid}?club_id=1').json()
assert lb['settings']['meeting_time']=='07:50' and len(lb['participants'])==2

# state machine cannot go backwards
assert ok('post',f'/api/club/1/occurrences/{oid}/execution/status',json={'status':'departed'}).json()['status']=='departed'
assert ok('post',f'/api/leader/occurrences/{oid}/status?club_id=1',json={'status':'in_progress'}).json()['status']=='in_progress'
assert ok('post',f'/api/club/1/occurrences/{oid}/execution/status',json={'status':'completed'}).json()['status']=='completed'
bad=c.post(f'/api/club/1/occurrences/{oid}/execution/status',json={'status':'preparing'})
assert bad.status_code==409

# CSV insurance manifest contains participant and policy data
csvr=c.get(f'/api/club/1/occurrences/{oid}/insurance/export.csv')
assert csvr.status_code==200 and '执行甲' in csvr.text and 'V13-001' in csvr.text

# audit log exists for execution actions
with app.conn() as dbc:
    events=dbc.execute('SELECT COUNT(*) FROM execution_event_logs WHERE occurrence_id=?',(oid,)).fetchone()[0]
assert events>=8

print('SMOKE V0.13 OK · PREP DASHBOARD / LEADERS / GROUPS / INSURANCE / NOTICES / CHECKIN / EXECUTION STATES / LEADER MOBILE')
