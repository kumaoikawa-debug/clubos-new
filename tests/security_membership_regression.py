"""Production checks for membership identity, payment gates and leader lists."""
import os, secrets, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.update(CLUBOS_SECURITY_MODE='production', CLUBOS_TEST_OPEN='0', MOCK_AI='0',
                  COMMERCE_PROVIDER='medusa', CLUBOS_DB_PATH=str(Path(tempfile.mkdtemp(prefix='clubos-security-'))/'test.db'),
                  CLUBOS_PUBLIC_BASE_URL='https://testserver',
                  CLUBOS_AUTH_SIGNING_KEY=secrets.token_urlsafe(56),
                  CLUBOS_COMMERCE_WEBHOOK_SECRET=secrets.token_urlsafe(40))
import app
from db import conn
from security_v025 import hash_password
from fastapi.testclient import TestClient

with conn() as c:
    c.executemany("INSERT INTO clubs(name,status,membership_purchase_enabled) VALUES(?,'active',1)", [('Test A',),('Test B',)])
    c.executemany('INSERT INTO users(name,phone) VALUES(?,?)', [('Member A','13900009001'),('Member B','13900009002'),('Leader','13900009003')])
    c.executemany('INSERT INTO club_members(club_id,user_id,club_points_balance) VALUES(1,?,?)', [(1,1000),(2,500)])
    c.execute("INSERT INTO club_member_tiers(club_id,name,rank,access_mode,price) VALUES(1,'Paid card',50,'PURCHASE',399)")
    c.execute("INSERT INTO points_mall_items(club_id,item_type,title,points_cost,stock) VALUES(1,'goods','Test item',100,10)")
    for name,role,uid,cid in [('member','member',2,None),('leader','leader',3,1),('club','club',None,1)]:
        c.execute('INSERT INTO auth_accounts(username,password_hash,role,user_id,club_id) VALUES(?,?,?,?,?)',
                  (name,hash_password('Test-only-password-2026!'),role,uid,cid))
    c.execute("INSERT INTO activities(club_id,title,status,event_date,location,price,capacity,activity_master_json,detail_json) VALUES(1,'Test hike','published','2099-01-01','Test',100,20,'{}','{}')")
    for i in (1,2):
        c.execute("INSERT INTO activity_occurrences(activity_id,club_id,start_at,price,capacity,status,label) VALUES(1,1,'2099-01-01 08:00',100,20,'open',?)",(str(i),))
    c.execute("INSERT INTO occurrence_leaders(occurrence_id,club_id,name,phone) VALUES(1,1,'Assigned','13900009003')")
    c.execute("INSERT INTO occurrence_leaders(occurrence_id,club_id,name,phone) VALUES(2,1,'Other','13900009004')")

def login(name):
    client=TestClient(app.app,base_url='https://testserver',raise_server_exceptions=False)
    assert client.post('/api/auth/login',json={'username':name,'password':'Test-only-password-2026!'}).status_code==200
    client.headers['x-clubos-csrf']=client.cookies['clubos_csrf']
    return client

member,leader,club=login('member'),login('leader'),login('club')
failures=[]
def check(label,condition):
    print(('PASS ' if condition else 'FAIL ')+label)
    if not condition: failures.append(label)

r=member.get('/api/public/clubs/1/points-mall')
check('omitted query uses authenticated wallet',r.status_code==200 and r.json()['wallet']['clubPoints']==500)
r=member.post('/api/public/clubs/1/points-mall/1/redeem',json={})
check('omitted body uses authenticated wallet',r.status_code==200 and r.json()['wallet']['clubPoints']==400)
with conn() as c:
    check('other member balance unchanged',c.execute('SELECT club_points_balance FROM club_members WHERE user_id=1').fetchone()[0]==1000)
    check('redemption belongs to actor',c.execute('SELECT user_id FROM points_mall_redemptions').fetchone()[0]==2)
check('explicit foreign query denied',member.get('/api/public/clubs/1/points-mall?user_id=1').status_code==403)
check('explicit foreign body denied',member.post('/api/public/clubs/1/points-mall/1/redeem',json={'userId':1}).status_code==403)
check('coupon recipient cannot spoof identity',member.post('/api/public/clubs/1/coupons/transfer',json={'toUserId':1,'code':'TEST'}).status_code==403)
for body in ({'tierId':1},{'tierId':1,'userId':2}):
    check('production purchase unavailable',member.post('/api/public/clubs/1/membership/purchase',json=body).status_code==503)
check('malformed tier rejected',member.post('/api/public/clubs/1/membership/purchase',json={'tierId':'x'}).status_code==422)
with conn() as c: check('no unpaid paid orders',c.execute('SELECT COUNT(*) FROM member_orders').fetchone()[0]==0)
r=member.get('/api/public/clubs/1/membership/purchasable')
check('production catalog exposes payment availability',r.status_code==200 and r.json().get('paymentAvailable') is False)
check('anonymous personal data denied',TestClient(app.app,base_url='https://testserver').get('/api/public/clubs/1/points-mall').status_code==401)
check('leader list scoped to assignment',[x['id'] for x in leader.get('/api/leader/occurrences?club_id=1').json()]==[1])
check('unassigned detail denied',leader.get('/api/leader/occurrences/2?club_id=1').status_code==403)
check('club isolation preserved',club.get('/api/club/2/members').status_code==403)
check('member cannot manage platform',member.get('/api/platform/orders').status_code==403)
with conn() as c: c.execute("UPDATE clubs SET status='disabled' WHERE id=1")
check('disabled club cannot redeem',member.post('/api/public/clubs/1/points-mall/1/redeem',json={}).status_code==403)
assert not failures, failures
print('PRODUCTION MEMBERSHIP SECURITY PASS')
