"""v0.25 negative-security tests: separate roles, ownership, CSRF and demo-bypass gates.
No external providers are mocked as real runtime; this exercises the ClubOS HTTP boundary.
"""
from pathlib import Path
from urllib.parse import quote
import os, sys, secrets, json, tempfile, io
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
tmp=tempfile.TemporaryDirectory(prefix='clubos_v025_')
os.environ.update({
    'CLUBOS_DB_PATH':str(Path(tmp.name)/'v25.sqlite'),
    'CLUBOS_SECURITY_MODE':'production',
    'MOCK_AI':'0','COMMERCE_PROVIDER':'medusa',
    'CLUBOS_PUBLIC_BASE_URL':'https://testserver',
    'CLUBOS_AUTH_SIGNING_KEY':secrets.token_urlsafe(56),
    'CLUBOS_COMMERCE_WEBHOOK_SECRET':secrets.token_urlsafe(40),
})
import app
from db import conn
from security_v025 import hash_password
from fastapi.testclient import TestClient
client=TestClient(app.app,base_url='https://testserver')
demo_image=next((ROOT/'static'/'demo'/'icebreaker').glob('*.png'))
demo_rel=demo_image.relative_to(ROOT/'static').as_posix()
with conn() as c:
    assert c.execute('SELECT COUNT(*) FROM clubs').fetchone()[0]==0, 'no production demo clubs'
    assert c.execute('SELECT COUNT(*) FROM payment_accounts').fetchone()[0]==0, 'no local production payments'
    c.executemany("INSERT INTO clubs(name,status,plan) VALUES(?,'active','pro')", [('SecClub A',),('SecClub B',)])
    c.executemany('INSERT INTO users(name,phone) VALUES(?,?)',[('User A','13999910001'),('User B','13999910002')])
    c.execute('INSERT INTO activities(club_id,title,status,event_date,location,price,capacity,activity_master_json,detail_json) VALUES(1,?,?,?,?,?,?,?,?)',
       ('Sec Activity A','published','2026-10-20','Chengdu',100,20,json.dumps({'internalData':{'internalCost':9999},'fees':{'included':'门票','cost':9999},'itinerary':[{'time':'08:00','content':'集合'}],'media':[{'ref':'img_01','url':'/static/'+demo_rel}]}),json.dumps({'blocks':[{'type':'media','mediaRefs':['img_01']}]})))
    c.execute('INSERT INTO activity_occurrences(activity_id,club_id,start_at,end_at,price,capacity,status,label) VALUES(1,1,?,?,?,?,?,?)',('2026-10-20 08:00','2026-10-20 18:00',100,20,'open','day'))
    c.execute("INSERT INTO activity_occurrences(activity_id,club_id,start_at,end_at,price,capacity,status,label) VALUES(1,1,'2026-10-21 08:00','2026-10-21 18:00',100,20,'open','unassigned')")
    c.execute('INSERT INTO registrations(activity_id,club_id,user_id,original_amount,amount) VALUES(1,1,1,100,100)')
    c.execute('INSERT INTO gear_orders(user_id,source_club_id,total,cash_paid) VALUES(1,1,100,100)')
    c.execute("INSERT INTO checkout_intents(id,kind,user_id,club_id) VALUES('SEC-CHECKOUT-A','gear',1,1)")
    c.execute("INSERT INTO after_sales_cases(id,order_id,user_id,source_club_id,case_type,status) VALUES('SEC-CASE-A',1,1,1,'refund_only','pending_review')")
    c.execute("INSERT INTO occurrence_leaders(occurrence_id,club_id,name,phone) VALUES(1,1,'Leader A','13999910001')")
    for username,role,club_id,user_id in [('platform25','platform',None,None),('club-a25','club',1,None),('club-b25','club',2,None),('member-a25','member',None,1),('member-b25','member',None,2),('leader-a25','leader',1,1)]:
        c.execute('INSERT INTO auth_accounts(username,password_hash,role,club_id,user_id) VALUES(?,?,?,?,?)',(username,hash_password('Secure-Test-Password-2026!'),role,club_id,user_id))

def login(name):
    c=TestClient(app.app,base_url='https://testserver')
    r=c.post('/api/auth/login',json={'username':name,'password':'Secure-Test-Password-2026!'})
    assert r.status_code==200,(name,r.status_code,r.text)
    return c,c.cookies.get('clubos_csrf')

r=client.get('/api/platform/credits');assert r.status_code==401,r.text
assert client.get('/api/club/1/analytics').status_code==401
assert client.get('/api/public/clubs/1').status_code==200
public_act=client.get('/api/public/activities/1').json()
assert 'internalData' not in str(public_act) and 'cost' not in public_act['activityMaster']['fees']
assert public_act['activityMaster']['itinerary'][0]['content']=='集合'
media_url=public_act['activityMaster']['media'][0]['url']
assert media_url.startswith('/api/public/activities/1/media/')
assert client.get(media_url).status_code==200
assert client.get('/static/'+quote(demo_rel,safe='/')).status_code==404
assert client.get('/api/public/activities/1/media/uploads/unreferenced.png').status_code==404
assert client.get('/api/public/activities/1/media/'+quote(demo_rel.replace('demo/','demo/nope/'),safe='/')).status_code==404
assert client.get('/api/public/registrations/1/refund-quote').status_code==401
assert client.get('/api/public/club-applications/1').status_code==401
assert client.get('/static/uploads/nonexistent').status_code==404
assert client.post('/api/public/checkouts/SEC-CHECKOUT-A/confirm',json={}).status_code==403
assert client.post('/api/commerce/events/order-paid',json={}).status_code==403
assert client.post('/api/commerce/events/refund-succeeded',json={}).status_code==403
assert client.post('/api/commerce/events/order-placed',json={}).status_code==401
assert client.get('/docs').status_code==404
assert client.get('/',follow_redirects=False).status_code==303
platform,pc=login('platform25');cluba,ac=login('club-a25');clubb,bc=login('club-b25');membera,mc=login('member-a25');memberb,_=login('member-b25');leader,lc=login('leader-a25')
assert platform.get('/api/platform/credits').status_code==200
assert membera.get('/api/auth/me').json()['phone']=='13999910001'
assert clubb.get('/api/auth/me').json()['clubId']==2
assert platform.get('/api/club/1/analytics').status_code==403
assert cluba.get('/api/club/1/analytics').status_code==200
assert cluba.get('/api/club/2/analytics').status_code==403
assert clubb.get('/api/club/1/members').status_code==403
assert cluba.get('/api/platform/suppliers').status_code==403
assert membera.get('/api/public/users/1/wallet').status_code==200
assert memberb.get('/api/public/clubs/1/member-center').status_code==400
assert memberb.get('/api/public/clubs/1/vouchers').status_code==400
assert memberb.get('/api/public/activities/1/price-quote?occurrence_id=1').status_code==400
assert memberb.post('/api/public/clubs/1/gear-checkout',json={'items':[]},headers={'X-ClubOS-CSRF':memberb.cookies.get('clubos_csrf')}).status_code==400
assert membera.get('/api/public/users/2/wallet').status_code==403
assert membera.get('/api/public/registrations/1/refund-quote').status_code==200
assert memberb.get('/api/public/registrations/1/refund-quote').status_code==403
assert memberb.get('/api/public/checkouts/SEC-CHECKOUT-A').status_code==403
assert memberb.get('/api/public/after-sales/SEC-CASE-A').status_code==403
assert memberb.post('/api/public/orders/1/after-sales',json={'userId':2,'type':'refund_only'}).status_code==403
assert membera.post('/api/public/clubs/1/gear-checkout',json={'userId':2,'items':[]}).status_code==403
assert membera.get('/api/public/activities/1/price-quote?occurrence_id=1&user_id=2').status_code==403
assert memberb.post('/api/public/activities/1/checkout',json={'name':'Fake','phone':'13999910001','occurrenceId':1}).status_code==403
assert membera.post('/api/public/activities/1/signup',json={}).status_code==403
assert leader.get('/api/leader/occurrences/1?club_id=1').status_code==200
assert leader.get('/api/leader/occurrences/1?club_id=2').status_code==403
assert leader.get('/api/leader/occurrences/2?club_id=1').status_code==403
# Cookie request without CSRF must be denied before the handler runs.
r=platform.patch('/api/platform/points-policy',json={'gearPointsActivityRedeemEnabled':False})
assert r.status_code==403,r.text
r=platform.patch('/api/platform/points-policy',headers={'X-ClubOS-CSRF':pc},json={'gearPointsActivityRedeemEnabled':False})
assert r.status_code==200,r.text
assert platform.patch('/api/platform/points-policy',headers={'X-ClubOS-CSRF':pc,'Origin':'https://evil.example'},json={}).status_code==403
assert platform.get('/api/auth/me').json()['role']=='platform'
assert platform.post('/api/auth/logout',headers={'X-ClubOS-CSRF':pc}).status_code==200
assert platform.get('/api/platform/credits').status_code==401
with conn() as c:
    audit=c.execute("SELECT method,path,status,role FROM security_audit_events WHERE path='/api/platform/points-policy' ORDER BY id").fetchall()
    assert len(audit)>=3 and any(a['status']==200 and a['role']=='platform' for a in audit) and audit[-1]['status']==403,audit
    try:
        c.execute('DELETE FROM security_audit_events')
        assert False,'append-only audit deletion accepted'
    except __import__('sqlite3').IntegrityError:pass
    c.execute("UPDATE clubs SET status='disabled' WHERE id=1")
assert cluba.get('/api/club/1/analytics').status_code==401


# Defense against chunked/missing Content-Length: parser checks actual bytes too.
from document_parser import save_uploads
try:
    save_uploads([SimpleNamespace(filename='too-large.txt',file=io.BytesIO(b'X'*32))],Path(tmp.name)/'oversize',max_total_bytes=16)
    assert False,'file size guard not enforced'
except ValueError:pass

# A missing production prerequisite must fail closed at startup preflight.
from security_v025 import validate_production_config
os.environ['MOCK_AI']='1'
try:
    validate_production_config()
    assert False,'production accepted MOCK_AI=1'
except RuntimeError:pass
finally:os.environ['MOCK_AI']='0'
# The seventh failed login from the same source is rate-limited.
for _ in range(6):
    assert client.post('/api/auth/login',json={'username':'guess-bogus','password':'wrong'}).status_code==401
assert client.post('/api/auth/login',json={'username':'guess-bogus','password':'wrong'}).status_code==429
print('SMOKE V0.25 OK: production no demo seed / role RBAC / tenant & member ownership / leader assignment / CSRF / session revocation / disabled clubs / demo money bypass blocked / audit')
