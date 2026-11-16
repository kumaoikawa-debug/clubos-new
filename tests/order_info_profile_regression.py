# 订单信息台 / 资料补全专项回归（2026-11-16 用户截图反馈）
# 覆盖：执行端「资料待补」→ C 端醒目补全入口 → 后台代客补资料/变更参加人 →
#       会员详情订单可点开 → 经营数据下「订单信息」两类列表 → 改价/退款登记全链路留痕。
import sys as _sys; _sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'tests'))
import _identity_patch  # noqa: F401
from pathlib import Path
from datetime import datetime, timedelta
import os, sys, hashlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='local'
os.environ.setdefault('CLUBOS_TEST_OPEN','1')
DB=ROOT/'clubos_order_info_test.db'
os.environ['CLUBOS_DB_PATH']=str(DB)
try: DB.unlink()
except FileNotFoundError: pass

import app
from db import init_db
from fastapi.testclient import TestClient
init_db()
c=TestClient(app.app)

PASS=[]
def check(name,cond,detail=''):
    assert cond,f'FAIL {name} {detail}'
    PASS.append(name); print('  ok -',name)
def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

# 真实库副本幂等：新审计表在 executescript 阶段 CREATE IF NOT EXISTS，不能因旧库缺列而崩。
h1=hashlib.sha256(DB.read_bytes()).hexdigest()
init_db()
h2=hashlib.sha256(DB.read_bytes()).hexdigest()
check('init_db 双跑幂等',h1==h2,(h1,h2))
with app.conn() as db:
    check('订单操作留痕表已建',db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='club_order_admin_logs'").fetchone()[0]==1)

# ── 建一场有「资料待补」的真实报名 ──
aid=ok('get','/api/public/clubs/1/activities').json()[0]['id']
start=(datetime.now()+timedelta(days=5)).replace(microsecond=0).isoformat(sep=' ')
oid=ok('post',f'/api/club/1/activities/{aid}/occurrences',json={'startAt':start,'price':260,'capacity':6,'label':'订单信息台测试团'}).json()['id']
ok('post',f'/api/club/1/activities/{aid}/publish')
co=ok('post',f'/api/public/activities/{aid}/checkout',json={
    'name':'付款人甲','phone':'13800000001','occurrenceId':oid,'participants':[
      {'name':'参加人甲','phone':'13800000011','relationToPayer':'本人','idType':'身份证','idNumber':'510100199001010005'}
    ]}).json()
paid=ok('post',f"/api/public/checkouts/{co['checkoutId']}/pay",json={'provider':'local','simulateSuccess':True}).json()['result']
rid=paid['registrationId']
ps=ok('get',f'/api/public/registrations/{rid}/participants').json()['participants']
pid=ps[0]['id']
check('缺紧急联系人＝资料待补',ps[0]['form_status']=='incomplete',ps[0])

# ── C 端补全：证件 + 紧急联系人 + 备注；已有姓名不得直接改 ──
r=c.patch(f'/api/public/registrations/{rid}/participants/{pid}',json={'name':'换个人'})
check('C 端不能直接改已有姓名（须走转名额）',r.status_code==400,r.text)
ok('patch',f'/api/public/registrations/{rid}/participants/{pid}',json={
    'idType':'身份证','idNumber':'510100199001010005','emergencyContactName':'甲家属',
    'emergencyContactPhone':'13900000011','notes':'素食，不上高海拔'})
ps2=ok('get',f'/api/public/registrations/{rid}/participants').json()['participants']
check('C 端补全后资料完整且备注落库',ps2[0]['form_status']=='complete' and ps2[0]['notes']=='素食，不上高海拔',ps2[0])

# 老订单姓名/手机号为空：C 端允许「填空式」补齐，非空则拒绝
with app.conn() as db:
    db.execute("""INSERT INTO registration_participants(registration_id,activity_id,occurrence_id,club_id,payer_user_id,
                  name,phone,id_type,id_number,form_status,insurance_status,status)
                  VALUES(?,?,?,?,?,'','','身份证','510100199001010013','incomplete','pending','active')""",
               (rid,aid,oid,1,1))
    blank_pid=int(db.execute('SELECT last_insert_rowid()').fetchone()[0])
ok('patch',f'/api/public/registrations/{rid}/participants/{blank_pid}',json={'name':'补名参加人','phone':'13800000012','emergencyContactName':'补家属'})
with app.conn() as db:
    b=db.execute('SELECT name,phone,form_status FROM registration_participants WHERE id=?',(blank_pid,)).fetchone()
check('老订单空姓名/手机号可补齐',b['name']=='补名参加人' and b['phone']=='13800000012',tuple(b))

# ── 后台代客改证件 / 备注 / 改价都能留痕 ──
prof=ok('patch',f'/api/club/1/registrations/{rid}/participants/{pid}/profile',json={
    'name':'参加人甲（后台修正）','phone':'13800000011','idType':'身份证','idNumber':'510100199001010005',
    'notes':'后台改：不辣','reason':'用户电话口述修正'}).json()
check('后台可改姓名/证件/备注',prof['name'].endswith('后台修正）') and prof['notes']=='后台改：不辣',prof)
with app.conn() as db:
    logs=db.execute("SELECT * FROM club_order_admin_logs WHERE order_kind='activity' AND action='profile_update'").fetchall()
check('后台补资料写入订单操作留痕',len(logs)==1 and '电话口述' in (logs[0]['reason'] or ''),[dict(x) for x in logs])

# 后台改价不受 C 端「姓名已存在」闸门影响，且同步参加人分摊
before=ok('get',f'/api/club/1/registrations/{rid}/participants').json()['registration']['amount']
price=ok('patch',f'/api/club/1/operation-orders/activity/{rid}/price',json={'amount':188,'reason':'线下补收改账面'}).json()
check('后台改价返回前后金额',price['after']['amount']==188.0,price)
with app.conn() as db:
    amt=db.execute('SELECT amount FROM registrations WHERE id=?',(rid,)).fetchone()[0]
    alloc_sum=db.execute("""SELECT COALESCE(SUM(cash_paid),0) FROM participant_financial_allocations a
                            JOIN registration_participants p ON p.id=a.participant_id WHERE p.registration_id=?""",(rid,)).fetchone()[0]
    plog=db.execute("SELECT reason FROM club_order_admin_logs WHERE action='price_adjust'").fetchone()
check('改价落到订单 + 分摊同步 + 留痕',amt==188.0 and round(float(alloc_sum or 0),2)==188.0 and plog and plog[0]=='线下补收改账面',(amt,alloc_sum,plog))

# ── 后台变更参加人：越过 24h 自助闸门，但仍校验证件 ──
with app.conn() as db:
    db.execute('UPDATE activity_occurrences SET start_at=? WHERE id=?',
               ((datetime.now()-timedelta(hours=2)).isoformat(sep=' '),oid))
bad=c.post(f'/api/club/1/registrations/{rid}/participants/{pid}/replace',json={
    'name':'新参加人','phone':'13800000019','idType':'身份证','idNumber':'123','reason':'现场换人'})
check('后台变更参加人仍校验证件',bad.status_code==409,bad.text)
rep=ok('post',f'/api/club/1/registrations/{rid}/participants/{pid}/replace',json={
    'name':'现场新参加人','phone':'13800000019','relationToPayer':'同行人','idType':'身份证',
    'idNumber':'510100199001010005','reason':'现场身份证核对后换人'}).json()
check('后台可越过自助闸门换人',rep['name']=='现场新参加人',rep)
with app.conn() as db:
    n=db.execute("SELECT COUNT(*) FROM club_order_admin_logs WHERE action='participant_replace'").fetchone()[0]
check('变更参加人留痕',n==1,n)

# ── 会员详情订单详情 / 订单信息台列表 ──
detail=ok('get',f'/api/club/1/operation-orders/activity/{rid}').json()
check('二级详情含姓名/证件/备注/付款',detail['participants'][0]['name']=='现场新参加人' and 'allocation' in detail['participants'][0],detail['participants'][0])
check('二级详情带操作留痕',len(detail['adminLogs'])>=3,[x['action'] for x in detail['adminLogs']])
listing=ok('get','/api/club/1/operation-orders?kind=activity').json()
row=next((x for x in listing['items'] if x['orderId']==rid),None)
check('活动订单进订单信息台',row and row['buyerPhone']=='13800000001' and row['incompleteCount']==2,row)
check('汇总含待补人数与金额',listing['summary']['amount']==188.0 and listing['summary']['incompleteCount']==2,(listing['summary'],before))

# 营地订单：直接造一张真实结构订单，验证列表/详情/改价/预订人/线下退款登记
with app.conn() as db:
    db.execute("""INSERT INTO camp_orders(club_id,user_id,order_no,status,goods_amount,payable,deposit_amount,total,
                  guest_name,guest_phone,created_at,paid_at)
                  VALUES(1,1,'CAMP-OI-001','paid',300,300,50,350,'营地顾客','13700000001',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
    camp_id=int(db.execute("SELECT id FROM camp_orders WHERE order_no='CAMP-OI-001'").fetchone()[0])
    db.execute("""INSERT INTO camp_order_items(order_id,club_id,kind,ref_id,title,unit_price,qty,subtotal,status)
                  VALUES(?,?, 'pitch',1,'测试营位',300,1,300,'paid')""",(camp_id,1))
camp_list=ok('get','/api/club/1/operation-orders?kind=camp').json()
check('营地订单进订单信息台',any(x['orderId']==camp_id for x in camp_list['items']),camp_list['items'])
camp_detail=ok('get',f'/api/club/1/operation-orders/camp/{camp_id}').json()
check('营地详情带商品与客户',camp_detail['items'][0]['title']=='测试营位' and camp_detail['guestName']=='营地顾客',camp_detail)
ok('patch',f'/api/club/1/operation-orders/camp/{camp_id}/price',json={'amount':260,'reason':'线下差价调整'})
with app.conn() as db:
    o=db.execute('SELECT payable,total FROM camp_orders WHERE id=?',(camp_id,)).fetchone()
check('营地改价同步总额=应付+押金',o['payable']==260.0 and o['total']==310.0,tuple(o))
ok('patch',f'/api/club/1/operation-orders/camp/{camp_id}/guest',json={'name':'营地顾客乙','phone':'13700000002','reason':'换联系人'})
refund=ok('post',f'/api/club/1/operation-orders/camp/{camp_id}/refund-record',json={'amount':100,'reason':'线下转账退回，凭证 12345'}).json()
check('营地线下退款登记明确不调支付渠道',refund['recorded']['channel']=='offline',refund)
camp_detail2=ok('get',f'/api/club/1/operation-orders/camp/{camp_id}').json()
check('营地操作全部可倒查',[x['action'] for x in camp_detail2['adminLogs']]==['refund_offline_record','guest_update','price_adjust'],[x['action'] for x in camp_detail2['adminLogs']])

# 后台发起活动退款：变更参加人测试曾把团期改到过去，这里恢复未来时间再走退款规则
with app.conn() as db:
    db.execute('UPDATE activity_occurrences SET start_at=? WHERE id=?',
               ((datetime.now()+timedelta(days=5)).isoformat(sep=' '),oid))
ref=ok('post',f'/api/club/1/operation-orders/activity/{rid}/refund-request',json={'reason':'后台代客取消'}).json()
check('后台活动退款进入申请态',ref['status']=='requested',ref)
refunds=ok('get','/api/club/1/refunds').json()
check('退款审核可见后台代客申请',any(x.get('refundRequestId')==ref['refundRequestId'] or x.get('id')==ref['refundRequestId'] for x in refunds),refunds[:1])

# 静态前端入口硬锁：C 端待补提醒 + 后台订单信息导航 + 二级详情函数
for path,needle in [
    ('static/web/web.js','w-prep-alert'),
    ('static/web/web.js','立即补全'),
    ('static/club/index.html','data-view="order-info"'),
    ('static/ux/order-info-workflows.js','openOrderInfo'),
    ('static/club/club.js',"openOrderInfo('activity',${o.id})"),
]:
    text=(ROOT/path).read_text(encoding='utf-8')
    check(f'静态入口存在 {needle}',needle in text,path)

print(f'\nALL PASS / {len(PASS)} checks')
