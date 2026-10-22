# 会员深度管理（2026-10-22 用户截图反馈：「点进去之后，应该查看到所有相关该用户的信息」，
# 同时需要可以手动调整(增减)积分、给该用户赠送优惠券(营销里已生成的)）。
# 覆盖：会员详情聚合（个人信息/订单/券/流水）、积分手工增减（含负数/越界/非法入参/流水落账）、
#       赠券（零积分、成本俱乐部承担、库存扣减、售罄 409、非本俱乐部券 409、非会员 404）、
#       赠出的券立刻出现在 C 端可用福利券里。
import sys as _sys; _sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'tests'))
import _identity_patch  # noqa: F401
from pathlib import Path
from datetime import datetime, timedelta
import os, sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ.setdefault('CLUBOS_TEST_OPEN','1')
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_member_test.db')
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

# ── 准备：一场已发布活动 + 一笔已支付报名（checkout 会自动建档 club_members）──
aid=ok('get','/api/public/clubs/1/activities').json()[0]['id']
start=(datetime.now()+timedelta(days=5)).replace(microsecond=0).isoformat(sep=' ')
oid=ok('post',f'/api/club/1/activities/{aid}/occurrences',json={'startAt':start,'price':260,'capacity':6,'label':'会员管理测试团'}).json()['id']
ok('post',f'/api/club/1/activities/{aid}/publish')
participants=[{'name':'深管会员','phone':'13800000031','relationToPayer':'本人','idType':'身份证','idNumber':'510100199001010021','emergencyContactName':'家属','emergencyContactPhone':'13900000031'}]
co=ok('post',f'/api/public/activities/{aid}/checkout',json={'name':'深管会员','phone':'13800000031','occurrenceId':oid,'participants':participants}).json()
paid=ok('post',f"/api/public/checkouts/{co['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()['result']
rid=paid['registrationId']
with app.conn() as dbc:
    uid=dbc.execute('SELECT user_id FROM registrations WHERE id=?',(rid,)).fetchone()[0]
check('报名已建档为会员',uid>0,uid)

# ── 1) 会员详情聚合 ──
d=ok('get',f'/api/club/1/members/{uid}').json()
for k in ('member','tier','activityOrders','gearOrders','clubLedger','gearLedger','vouchers'):
    check(f'详情含 {k}',k in d,list(d.keys()))
check('详情：姓名与手机',d['member']['name']=='深管会员' and d['member']['phone']=='13800000031',d['member'])
check('详情：装备积分字段存在',isinstance(d['member'].get('gear_points'),int),d['member'].get('gear_points'))
check('详情：历史订单 1 笔且带参加人',len(d['activityOrders'])==1 and d['activityOrders'][0]['participants'][0]['name']=='深管会员',d['activityOrders'])
r=c.get('/api/club/1/members/999999')
check('非会员查详情 404',r.status_code==404,r.text)

# ── 2) 积分手工调整（活动积分）──
base=d['member']['club_points_balance']
r=ok('post',f'/api/club/1/members/{uid}/points',json={'kind':'club','amount':500,'note':'投诉补偿'}).json()
check('活动积分 +500',r['balance']==base+500 and r['amount']==500,r)
r=ok('post',f'/api/club/1/members/{uid}/points',json={'kind':'club','amount':-200,'note':'纠错扣减'}).json()
check('活动积分 -200',r['balance']==base+300,r)
r=c.post(f'/api/club/1/members/{uid}/points',json={'kind':'club','amount':-999999,'note':'越界测试'})
check('扣成负数被拒（409）',r.status_code==409,r.text)
r=c.post(f'/api/club/1/members/{uid}/points',json={'kind':'club','amount':0,'note':'零'})
check('调整 0 被拒（400）',r.status_code==400,r.text)
r=c.post(f'/api/club/1/members/{uid}/points',json={'kind':'bad','amount':10})
check('非法积分类型被拒（400）',r.status_code==400,r.text)
r=c.post(f'/api/club/1/members/{uid}/points',json={'kind':'club','amount':2000000})
check('超大调整被拒（400）',r.status_code==400,r.text)
with app.conn() as dbc:
    led=dbc.execute("SELECT type,amount,note FROM club_point_ledger WHERE club_id=1 AND user_id=? AND type='manual_adjust' ORDER BY id",(uid,)).fetchall()
check('积分流水：两条手工调整',len(led)==2 and led[0]['amount']==500 and led[1]['amount']==-200,[dict(x) for x in led])
check('流水带原因',led[0]['note']=='投诉补偿',led[0]['note'])

# ── 3) 积分手工调整（装备积分，账号不存在时自动建档）──
r=ok('post',f'/api/club/1/members/{uid}/points',json={'kind':'gear','amount':300,'note':'平台活动赠送'}).json()
check('装备积分 +300（自动建档）',r['balance']==300,r)
r=ok('post',f'/api/club/1/members/{uid}/points',json={'kind':'gear','amount':-100,'note':'纠错'}).json()
check('装备积分 -100',r['balance']==200,r)
r=c.post(f'/api/club/1/members/{uid}/points',json={'kind':'gear','amount':-999999})
check('装备积分扣成负数被拒（409）',r.status_code==409,r.text)
with app.conn() as dbc:
    gled=dbc.execute("SELECT type,amount FROM gear_point_ledger WHERE user_id=? AND type='manual_adjust' ORDER BY id",(uid,)).fetchall()
check('装备积分流水两条',len(gled)==2 and gled[0]['amount']==300,[dict(x) for x in gled])

# ── 4) 赠券：俱乐部在营销里已生成的抵扣券 ──
bid=ok('post','/api/club/1/benefits',json={'title':'会员日 ¥50 抵扣券','benefitType':'activity_coupon','pointsCost':3000,'cashValue':50,'stock':2,'description':'赠券测试'}).json()['benefitId']
g=ok('post',f'/api/club/1/members/{uid}/vouchers',json={'benefitId':bid,'note':'老会员回馈'}).json()
check('赠券成功',g['ok'] and g['voucherCode'].startswith('BEN-') and g['title']=='会员日 ¥50 抵扣券',g)
with app.conn() as dbc:
    red=dbc.execute('SELECT * FROM benefit_redemptions WHERE id=?',(g['redemptionId'],)).fetchone()
    red=dict(red)
    stock=dbc.execute('SELECT stock FROM member_benefits WHERE id=?',(bid,)).fetchone()[0]
check('赠券零积分、俱乐部承担',red['points_spent']==0 and red['funding_owner']=='CLUB' and red['status']=='issued',red)
check('赠券扣库存 2→1',stock==1,stock)
check('赠券带说明',red.get('gift_note')=='老会员回馈',red.get('gift_note'))
# 再赠一张把库存清掉，第三张必须 409
ok('post',f'/api/club/1/members/{uid}/vouchers',json={'benefitId':bid})
r=c.post(f'/api/club/1/members/{uid}/vouchers',json={'benefitId':bid})
check('售罄后再赠被拒（409）',r.status_code==409,r.text)
r=c.post(f'/api/club/1/members/{uid}/vouchers',json={'benefitId':999999})
check('赠送不存在的券被拒（404）',r.status_code==404,r.text)
r=c.post(f'/api/club/1/members/999999/vouchers',json={'benefitId':bid})
check('给非会员赠券被拒（404）',r.status_code==404,r.text)
# 平台创建的券不能由俱乐部赠（直插一条 PLATFORM 福利）
with app.conn() as dbc:
    dbc.execute("INSERT INTO member_benefits(owner_type,owner_club_id,target_club_id,title,benefit_type,points_type,points_cost,cash_value,stock,status) VALUES('PLATFORM',NULL,NULL,'平台补贴券','activity_coupon','gear',0,20,NULL,'active')")
    pid=dbc.execute("SELECT last_insert_rowid()").fetchone()[0]
r=c.post(f'/api/club/1/members/{uid}/vouchers',json={'benefitId':pid})
check('平台券不能由俱乐部赠送（409）',r.status_code==409,r.text)

# ── 5) 赠出的券进 C 端可用福利券（顾客付款时能抵扣）──
vs=ok('get',f'/api/public/clubs/1/vouchers?user_id={uid}&kind=activity').json()
codes=[v['voucher_code'] for v in vs]
check('赠券立刻出现在 C 端可用券里',g['voucherCode'] in codes,codes)

# ── 6) 详情里能看到获赠标记与流水 ──
d2=ok('get',f'/api/club/1/members/{uid}').json()
gifted=[v for v in d2['vouchers'] if v['voucher_code']==g['voucherCode']]
check('详情：获赠券带 0 积分与说明',len(gifted)==1 and gifted[0]['points_spent']==0 and gifted[0].get('gift_note')=='老会员回馈',gifted)
check('详情：活动流水含手工调整',any(l['type']=='manual_adjust' and l['amount']==500 for l in d2['clubLedger']),d2['clubLedger'][:2])

print(f'\nALL {len(PASS)} CHECKS PASSED')
