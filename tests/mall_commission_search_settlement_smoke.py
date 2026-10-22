# 商城佣金日期搜索 + 俱乐部结算账户（2026-10-22 用户截图反馈两件事）：
# ①「佣金明细太多了，要按日期搜索」——订单/佣金流水接口 date_from/date_to 过滤（UTC+8 对齐），
#    非法格式与 from>to 一律 400；
# ②「俱乐部结算佣金，需要提供法人卡或对公账户才行」——club_settlement_accounts 增改查（脱敏），
#    平台结算无账户 409、有账户才放行并回带打款账户摘要，结算记录可核对打到了哪张卡。
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
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_commission_test.db')
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

# ── 造数据：俱乐部 1 三笔不同日期的商城订单（created_at 手工指定 UTC 值）──
# A: UTC 10-21 20:30 → 本地(+8) 10-22（验证时区边界：UTC 日期选 10-21 筛不到，选 10-22 才筛得到）
# B: UTC 10-10 10:00 → 本地 10-10
# C: UTC 09-30 23:00 → 本地 10-01
with app.conn() as dbc:
    uid=dbc.execute('SELECT id FROM users ORDER BY id LIMIT 1').fetchone()['id']
    seeds=[('2026-10-21 20:30:00',699,55.92),('2026-10-10 10:00:00',299,29.9),('2026-09-30 23:00:00',699,55.92)]
    order_ids=[]
    for ts,total,comm in seeds:
        cur=dbc.execute("INSERT INTO gear_orders(user_id,source_club_id,total,cash_paid,status,club_commission,created_at) VALUES(?,?,?,?,?,?,?)",
                        (uid,1,total,total,'paid',comm,ts))
        oid=int(cur.lastrowid if hasattr(cur,'lastrowid') else dbc.execute('SELECT last_insert_rowid()').fetchone()[0])
        order_ids.append(oid)
        dbc.execute("INSERT INTO commission_ledger(club_id,order_id,amount,status,ledger_type,created_at) VALUES(?,?,?,?,?,?)",
                    (1,oid,comm,'available','earn',ts))
    # 俱乐部 2 一笔 available 流水（用于「无账户不准结算」的 409 验证）
    cid2=dbc.execute('SELECT id FROM clubs WHERE id=2').fetchone()
    if not cid2:
        dbc.execute("INSERT INTO clubs(id,name) VALUES(2,'结算测试俱乐部')")
    # 俱乐部 3：无账户俱乐部（用于「新建时敏感字段留空被拒」）
    if not dbc.execute('SELECT id FROM clubs WHERE id=3').fetchone():
        dbc.execute("INSERT INTO clubs(id,name) VALUES(3,'留空测试俱乐部')")
    cur=dbc.execute("INSERT INTO gear_orders(user_id,source_club_id,total,cash_paid,status,club_commission,created_at) VALUES(?,?,?,?,?,?,?)",
                    (uid,2,299,299,'paid','29.9','2026-10-15 12:00:00'))
    oid2=int(cur.lastrowid if hasattr(cur,'lastrowid') else dbc.execute('SELECT last_insert_rowid()').fetchone()[0])
    dbc.execute("INSERT INTO commission_ledger(club_id,order_id,amount,status,ledger_type,created_at) VALUES(?,?,?,?,?,?)",
                (2,oid2,29.9,'available','earn','2026-10-15 12:00:00'))
    dbc.commit()

# ── 1) 订单按日期搜索 ──
all_orders=ok('get','/api/club/1/mall/orders').json()
check('无参数返回全部 3 笔',len(all_orders)==3,[o['id'] for o in all_orders])
r=ok('get','/api/club/1/mall/orders?date_from=2026-10-22&date_to=2026-10-22').json()
check('筛本地 10-22 只得 UTC 10-21 20:30 那笔（+8 边界）',len(r)==1 and r[0]['id']==order_ids[0],[o['id'] for o in r])
r=ok('get','/api/club/1/mall/orders?date_from=2026-10-01&date_to=2026-10-10').json()
check('10-01~10-10 得两笔',len(r)==2 and {o['id'] for o in r}=={order_ids[1],order_ids[2]},[o['id'] for o in r])
r=ok('get','/api/club/1/mall/orders?date_from=2026-10-22').json()
check('只给 from（10-22 起）得 1 笔',len(r)==1,[o['id'] for o in r])
r=ok('get','/api/club/1/mall/orders?date_from=2026-11-01').json()
check('未来日期空结果',len(r)==0,r)
r=c.get('/api/club/1/mall/orders?date_from=2026-10-10&date_to=2026-10-01')
check('from>to 拒绝（400）',r.status_code==400,r.text)
r=c.get('/api/club/1/mall/orders?date_from=2026/10/01')
check('非法日期格式拒绝（400）',r.status_code==400,r.text)
r=c.get('/api/club/1/mall/orders?date_from=2026-13-01')
check('非法月日拒绝（400）',r.status_code==400,r.text)

# ── 2) 佣金流水同样可搜 ──
r=ok('get','/api/club/1/mall/commissions').json()
check('佣金流水无参 3 条',len(r)==3,len(r))
r=ok('get','/api/club/1/mall/commissions?date_from=2026-10-22&date_to=2026-10-22').json()
check('佣金流水按日期过滤',len(r)==1 and r[0]['order_id']==order_ids[0],r)
r=ok('get','/api/platform/commissions?club_id=1&date_from=2026-10-01&date_to=2026-10-31').json()
check('平台端佣金流水按日期过滤',len(r)==3,len(r))
r=ok('get','/api/platform/commissions?date_from=2026-10-22&date_to=2026-10-22').json()
check('平台端全俱乐部按日期过滤（本地 10-22）',len(r)==1 and r[0]['order_id']==order_ids[0],r)

# ── 3) 结算账户：无账户时平台不准结算（俱乐部 2）──
r=c.post('/api/platform/settlements',json={'clubId':2,'paymentRef':'NO-ACCT-TEST'})
check('无结算账户结算被拒（409）',r.status_code==409 and '结算账户' in r.text,(r.status_code,r.text))

# ── 4) 俱乐部 1 结算账户：增/查/脱敏/改 ──
a=ok('get','/api/club/1/settlement-account').json()
check('初始无账户',a=={'exists':False},a)
r=c.post('/api/club/1/settlement-account',json={'accountType':'legal','holder':'张法人','legalName':'张法人','legalIdNo':'510100199001010021','bankName':'中国工商银行','bankBranch':'成都高新支行','cardNo':'6222021234567890123','note':'佣金打款'})
check('合法法人卡建档 200',r.status_code==200,r.text)
a=r.json()
check('建档回带脱敏卡号（后四位）',a['cardNoMasked'].endswith('0123') and '*' in a['cardNoMasked'],a['cardNoMasked'])
check('建档回带脱敏身份证',a['legalIdNoMasked'].startswith('510100') and a['legalIdNoMasked'].endswith('0021') and '*' in a['legalIdNoMasked'],a['legalIdNoMasked'])
check('账户类型中文',a['accountTypeCn']=='法人银行卡',a['accountTypeCn'])
check('回带不含完整卡号/身份证明文','6222021234567890123' not in r.text and '510100199001010021' not in r.text,r.text[:200])
a2=ok('get','/api/club/1/settlement-account').json()
check('GET 能查到已建档账户',a2['exists'] and a2['holder']=='张法人',a2)
r=ok('post','/api/club/1/settlement-account',json={'accountType':'corporate','holder':'成都远拓旅游有限公司','legalName':'张法人','legalIdNo':'510100199001010021','bankName':'中国银行','cardNo':'123456789012'}).json()
check('改到对公账户（upsert）',r['accountType']=='corporate' and r['holder']=='成都远拓旅游有限公司' and r['cardNoMasked'].endswith('9012'),r)
a3=ok('get','/api/club/1/settlement-account').json()
check('GET 反映 upsert 后的值',a3['accountTypeCn']=='对公账户',a3)
# 4b) 修改时卡号/身份证留空＝保留原值（GET 只回脱敏值，防止 ******** 被存回库）
r=ok('post','/api/club/1/settlement-account',json={'accountType':'corporate','holder':'成都远拓旅游有限公司','legalName':'张法人','legalIdNo':'','bankName':'中国银行','bankBranch':'','cardNo':''}).json()
check('修改留空保留原卡号（后四位 9012）',r['cardNoMasked'].endswith('9012'),r)
check('修改留空保留原身份证',r['legalIdNoMasked'].endswith('0021'),r)
with app.conn() as dbc:
    raw=dbc.execute("SELECT card_no FROM club_settlement_accounts WHERE club_id=1").fetchone()['card_no']
check('库里的卡号没被脱敏值污染',raw=='123456789012',raw)
r=c.post('/api/club/3/settlement-account',json={'accountType':'legal','holder':'新俱乐部','legalName':'新法人','legalIdNo':'','bankName':'工行','cardNo':''})
check('无账户俱乐部新建时卡号留空被拒（400）',r.status_code==400,r.text)
r=c.post('/api/club/1/settlement-account',json={'accountType':'wechat','holder':'x','legalName':'y','legalIdNo':'510100199001010021','bankName':'z','cardNo':'123456789012345'})
check('非法账户类型 400',r.status_code==400,r.text)
r=c.post('/api/club/1/settlement-account',json={'accountType':'legal','holder':'张法人','legalName':'张法人','legalIdNo':'510100199001010021','bankName':'工行','cardNo':'6222abcd'})
check('卡号含字母 400',r.status_code==400,r.text)
r=c.post('/api/club/1/settlement-account',json={'accountType':'legal','holder':'张法人','legalName':'张法人','legalIdNo':'510100199001010021','bankName':'工行','cardNo':'6222'})
check('卡号过短 400',r.status_code==400,r.text)
r=c.post('/api/club/1/settlement-account',json={'accountType':'legal','holder':'张法人','legalName':'张法人','legalIdNo':'51010019900101','bankName':'工行','cardNo':'6222021234567890123'})
check('身份证格式错 400',r.status_code==400,r.text)
r=c.post('/api/club/1/settlement-account',json={'accountType':'legal','holder':'','legalName':'张法人','legalIdNo':'510100199001010021','bankName':'工行','cardNo':'6222021234567890123'})
check('户名为空 400',r.status_code==400,r.text)

# ── 5) 平台侧查看与汇总 ──
lst=ok('get','/api/platform/settlement-accounts').json()
me=[x for x in lst if x['clubId']==1][0]
check('平台列表含俱乐部 1 账户（脱敏）',me['exists'] and me['account']['cardNoMasked'].endswith('9012') and '123456789012' not in str(me),me)
check('平台列表含未提供的俱乐部 2',[x for x in lst if x['clubId']==2][0]['exists'] is False,lst)
sum1=ok('get','/api/platform/commission-summary?club_id=1').json()
check('汇总 hasAccount=true',sum1['hasAccount'] is True,sum1['hasAccount'])
sum2=ok('get','/api/platform/commission-summary?club_id=2').json()
check('俱乐部 2 hasAccount=false',sum2['hasAccount'] is False,sum2['hasAccount'])
pv=ok('get','/api/platform/settlements/preview?club_id=1').json()
check('预览带账户摘要',pv['account']['exists'] and pv['account']['holder']=='成都远拓旅游有限公司',pv['account'])

# ── 6) 有账户后正常结算 + 幂等 + 打款账户回带 ──
before=ok('get','/api/platform/commission-summary?club_id=1').json()
r=ok('post','/api/platform/settlements',json={'clubId':1,'paymentRef':'CST-TEST-20261022','note':'平台佣金结算'}).json()
check('结算成功（净额 141.74=55.92*2+29.9）',abs(float(r['netAmount'])-141.74)<0.01 and r['entryCount']==3,r)
check('结算回带打款账户摘要',r['payee']['holder']=='成都远拓旅游有限公司' and r['payee']['cardNoMasked'].endswith('9012'),r.get('payee'))
r2=ok('post','/api/platform/settlements',json={'clubId':1,'paymentRef':'CST-TEST-20261022'}).json()
check('同凭证号幂等（不重复结算）',r2.get('idempotent') is True,r2)
after=ok('get','/api/platform/commission-summary?club_id=1').json()
check('可结算余额已清零',float(after['payableNow'])==0 and float(after['settled'])>=141.74,(after['payableNow'],after['settled']))
with app.conn() as dbc:
    st=dbc.execute("SELECT COUNT(*) n FROM commission_ledger WHERE club_id=1 AND status='settled'").fetchone()['n']
check('三条流水全部 settled',st==3,st)
sett=ok('get','/api/platform/settlements?club_id=1').json()
rec=[s for s in sett if s['payment_ref']=='CST-TEST-20261022'][0]
check('结算记录可核对打款账户',rec['payee'] and rec['payee']['cardNoMasked'].endswith('9012'),rec.get('payee'))

# ── 7) 俱乐部端结算列表同样可见 ──
cs=ok('get','/api/club/1/mall/settlements').json()
check('俱乐部端结算列表 1 条',len(cs)==1 and abs(float(cs[0]['net_amount'])-141.74)<0.01,cs)

print(f'\n=== ALL {len(PASS)} CHECKS PASSED ===')
