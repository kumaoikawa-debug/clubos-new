# 执行页领队资源库指派 + C 端「出行指引」同步（2026-10-22 用户反馈两件事）：
#   1) 活动执行里的领队应该跟俱乐部领队资源库同步，直接在里面选、也可以增加；
#   2) 分车 / 活动通知保存之后要能同步到 C 端用户。
# 覆盖：leaderId 指派回填名册信息、重复指派 400、当场建档入册再排、
#       order-center 的 trip 下发（集合/应急/带队/sent 通知/分车），
#       草稿通知与领队手机号不下发。
import sys as _sys; _sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'tests'))
import _identity_patch  # noqa: F401
from pathlib import Path
from datetime import datetime, timedelta
import os, sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_trip_test.db')
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c=TestClient(app.app)

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

PASS=[]
def check(name,cond,detail=''):
    assert cond,f'FAIL {name} {detail}'
    PASS.append(name); print('  ok -',name)

aid=ok('get','/api/public/clubs/1/activities').json()[0]['id']
start=(datetime.now()+timedelta(days=4)).replace(microsecond=0).isoformat(sep=' ')
oid=ok('post',f'/api/club/1/activities/{aid}/occurrences',json={'startAt':start,'price':260,'capacity':6,'label':'出行指引测试团'}).json()['id']
ok('post',f'/api/club/1/activities/{aid}/publish')

participants=[
 {'name':'出行甲','phone':'13800000011','relationToPayer':'本人','idType':'身份证','idNumber':'510100199001010005','emergencyContactName':'甲家属','emergencyContactPhone':'13900000011'},
 {'name':'出行乙','phone':'13800000012','relationToPayer':'朋友','idType':'身份证','idNumber':'510100199001010013','emergencyContactName':'乙家属','emergencyContactPhone':'13900000012'},
]
co=ok('post',f'/api/public/activities/{aid}/checkout',json={'name':'出行甲','phone':'13800000011','occurrenceId':oid,'participants':participants}).json()
paid=ok('post',f"/api/public/checkouts/{co['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()['result']
rid=paid['registrationId']
with app.conn() as dbc:
    uid=dbc.execute('SELECT user_id FROM registrations WHERE id=?',(rid,)).fetchone()[0]
ps=ok('get',f'/api/public/registrations/{rid}/participants').json()['participants']
check('两名参加人已建档',len(ps)==2,[p['name'] for p in ps])

# ── 1) 领队资源库：先建档，再从执行页按 leaderId 指派（姓名/电话以名册为准）──
lid=ok('post','/api/club/1/leaders',json={'name':'资源库领队','phone':'13800000099','role':'领队','specialties':['徒步']}).json()['id']
check('资源库建档返回 id',isinstance(lid,int) and lid>0,lid)
asg=ok('post',f'/api/club/1/occurrences/{oid}/leaders',json={'leaderId':lid,'role':'主领队'}).json()
check('按 leaderId 指派：姓名电话回填名册',asg['name']=='资源库领队' and asg['phone']=='13800000099',asg)
check('指派记录带 leader_id（可关联名册）',asg.get('leader_id')==lid,asg.get('leader_id'))
r=c.post(f'/api/club/1/occurrences/{oid}/leaders',json={'leaderId':lid})
check('同一领队重复指派被拒（400）',r.status_code==400,r.text)
# 资源库没有的 id 不能指派（防止把野记录塞进团期）
r=c.post(f'/api/club/1/occurrences/{oid}/leaders',json={'leaderId':999999})
check('指派不存在的名册人被拒（404）',r.status_code==404,r.text)

# ── 2) 集合与应急信息 ──
st=ok('patch',f'/api/club/1/occurrences/{oid}/execution/settings',json={'meetingTime':'07:50','meetingLocation':'天府广场','emergencyPhone':'400-000-001','leaderNote':'内部备注：核对保险'}).json()
check('集合信息已保存',st['meeting_location']=='天府广场' and st['emergency_phone']=='400-000-001')

# ── 3) 通知：一条草稿 + 一条已发送 ──
draft=ok('post',f'/api/club/1/occurrences/{oid}/notices',json={'title':'草稿通知','content':'还在改，别发给顾客','channel':'manual'}).json()
sent=ok('post',f'/api/club/1/occurrences/{oid}/notices',json={'title':'出发提醒','content':'明早 07:50 天府广场集合，带好证件。','channel':'manual'}).json()
ok('post',f'/api/club/1/occurrences/{oid}/notices/{sent["id"]}/send')

# ── 4) 分车：只给甲分车，乙不分配 ──
car=ok('post',f'/api/club/1/occurrences/{oid}/groups',json={'groupType':'vehicle','name':'1号车','capacity':2,'note':'车上发水'}).json()
ok('post',f"/api/club/1/execution-groups/{car['id']}/assign",json={'participantId':ps[0]['id']})

# ── 5) C 端订单中心：trip 下发核对 ──
oc=ok('get',f'/api/public/users/{uid}/order-center?club_id=1').json()
order=next(x for x in oc['activityOrders'] if x['id']==rid)
trip=order.get('trip')
check('trip 已下发',isinstance(trip,dict),trip)
check('trip 含集合时间地点',trip['meeting']=={'time':'07:50','location':'天府广场'},trip.get('meeting'))
check('trip 含应急电话',trip['emergencyPhone']=='400-000-001',trip.get('emergencyPhone'))
check('trip 含带队人（来自名册指派）',[l['name'] for l in trip['leaders']]==['资源库领队'],trip.get('leaders'))
check('trip 带队人不含手机号',all('phone' not in l for l in trip['leaders']),trip.get('leaders'))
check('trip 不含给领队的内部备注','leaderNote' not in trip,'不该下发内部备注')
titles=[n['title'] for n in trip.get('notices') or []]
check('已发送通知下发、草稿隔离',titles==['出发提醒'],titles)
check('通知带发送时间',all(n.get('sentAt') for n in trip['notices']),trip.get('notices'))
check('分车按参加人映射',trip['vehicles']=={str(ps[0]['id']):'1号车'},trip.get('vehicles'))

# ── 6) 边界：没有执行数据的团期不下发 trip ──
oid2=ok('post',f'/api/club/1/activities/{aid}/occurrences',json={'startAt':(datetime.now()+timedelta(days=9)).replace(microsecond=0).isoformat(sep=' '),'price':260,'capacity':6,'label':'空执行团'}).json()['id']
co2=ok('post',f'/api/public/activities/{aid}/checkout',json={'name':'出行丙','phone':'13800000013','occurrenceId':oid2,'participants':[{'name':'出行丙','phone':'13800000013','relationToPayer':'本人','idType':'身份证','idNumber':'510100199001010021','emergencyContactName':'丙家属','emergencyContactPhone':'13900000013'}]}).json()
paid2=ok('post',f"/api/public/checkouts/{co2['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()['result']
rid2=paid2['registrationId']
with app.conn() as dbc:
    uid2=dbc.execute('SELECT user_id FROM registrations WHERE id=?',(rid2,)).fetchone()[0]
oc2=ok('get',f'/api/public/users/{uid2}/order-center?club_id=1').json()
order2=next(x for x in oc2['activityOrders'] if x['id']==rid2)
check('无执行数据的团期 trip=None（前端整块不渲染）',order2.get('trip') is None,order2.get('trip'))

# ── 7) 撤下领队后 trip 带队人同步消失 ──
asg_id=ok('get',f'/api/club/1/occurrences/{oid}/execution').json()['leaders'][0]['id']
ok('delete',f'/api/club/1/occurrences/{oid}/leaders/{asg_id}')
oc3=ok('get',f'/api/public/users/{uid}/order-center?club_id=1').json()
order3=next(x for x in oc3['activityOrders'] if x['id']==rid)
check('撤下领队后 C 端带队人清空','leaders' not in (order3.get('trip') or {}),order3.get('trip'))

print('\nALL PASS execution_trip_smoke (%d checks)'%len(PASS))
