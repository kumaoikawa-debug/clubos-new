"""
Production-mode SECURITY acceptance for ClubOS NEW v0.25.1 handoff.
Validates the server-side security boundary that demo mode intentionally leaves open:
4-role login over HTTPS (secure cookies), wrong-password 401, unauth 401, role gating
(platform/club/member cross-denied), cross-tenant club isolation, CSRF enforcement,
disabled-account denial, demo-money bypass endpoints disabled, public read allowed,
and login rate limiting.

Run against a PRODUCTION-mode backend (CLUBOS_SECURITY_MODE=production, real signing key,
HTTPS, COMMERCE_PROVIDER=medusa). Default BASE=https://127.0.0.1:8001 (self-signed -> verify=False).
Set STAGING_PASSWORD to the provisioned prod password.

trust_env=False is required in the WorkBuddy sandbox (see acceptance_demo_live.py note).
"""
import os, sys, sqlite3, httpx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.getenv("BASE", "https://127.0.0.1:8001")
PW = os.getenv("STAGING_PASSWORD", "Prod-Staging-2026-ClubOS!")
ROLES = {"platform_admin":"platform","club1_admin":"club","leader1":"leader","member1":"member"}
# The login rate limit is IP-scoped (hash(signing_key+client_ip)); a previous run's bad-attempt
# loop can poison the whole IP. Reset attempt counters so this run starts clean.
_db = os.environ.get("CLUBOS_DB_PATH", "/tmp/clubos_prod/clubos.db")
try:
    _con = sqlite3.connect(_db)
    _con.execute("DELETE FROM auth_login_attempts")
    _con.execute("DELETE FROM public_rate_limits")
    _con.commit(); _con.close()
except Exception:
    pass
results = []
def rec(n, ok, d=""):
    results.append((n, "PASS" if ok else "FAIL", d)); print(("PASS " if ok else "FAIL ")+n+(f"  [{d}]" if d else ""))
def login(u):
    c=httpx.Client(base_url=BASE, timeout=20, follow_redirects=False, trust_env=False, verify=False)
    r=c.post("/api/auth/login", json={"username":u,"password":PW}); csrf=c.cookies.get("clubos_csrf")
    if csrf: c.headers["x-clubos-csrf"]=csrf
    return c, r
clients={}
for u,role in ROLES.items():
    c,r=login(u); ok=r.status_code==200 and r.json().get("ok") is True
    rec(f"PROD LOGIN {u} ({role})", ok, f"HTTP {r.status_code}")
    if ok: clients[u]=c
r=httpx.Client(base_url=BASE, trust_env=False, verify=False).post("/api/auth/login", json={"username":"platform_admin","password":"wrong"})
rec("PROD wrong password -> 401", r.status_code==401, f"HTTP {r.status_code}")
r=httpx.get(BASE+"/api/club/1/dashboard", follow_redirects=False, trust_env=False, verify=False)
rec("PROD UNAUTH /api/club/1/dashboard -> 401", r.status_code==401, f"HTTP {r.status_code}")
if "platform_admin" in clients:
    r=clients["platform_admin"].get("/api/club/1/dashboard"); rec("PROD GATE platform->club denied (403)", r.status_code==403, f"HTTP {r.status_code}")
if "club1_admin" in clients:
    r=clients["club1_admin"].get("/api/platform/clubs"); rec("PROD GATE club->platform denied (403)", r.status_code==403, f"HTTP {r.status_code}")
    r=clients["club1_admin"].get("/api/club/1/dashboard"); rec("PROD club1 own dashboard allowed (200)", r.status_code==200, f"HTTP {r.status_code}")
if "member1" in clients:
    r=clients["member1"].get("/api/club/1/dashboard"); rec("PROD GATE member->club denied (403)", r.status_code==403, f"HTTP {r.status_code}")
if "platform_admin" in clients:
    r=clients["platform_admin"].post("/api/platform/clubs", json={"name":"测试俱乐部B","contactName":"B","contactPhone":"13900000002","city":"上海","applicationNote":"隔离测试"})
    club2=r.json().get("id") if r.status_code==200 else None; rec("PROD platform create club 2", bool(club2), f"id={club2}")
    if club2 and "club1_admin" in clients:
        r=clients["club1_admin"].get(f"/api/club/{club2}/dashboard"); rec("PROD ISOLATION club1->club2 denied (403)", r.status_code==403, f"HTTP {r.status_code}")
c=httpx.Client(base_url=BASE, trust_env=False, verify=False); c.post("/api/auth/login", json={"username":"member1","password":PW})
r=c.post("/api/auth/logout"); rec("PROD CSRF missing on logout -> 403", r.status_code==403, f"HTTP {r.status_code}")
db=os.environ.get("CLUBOS_DB_PATH","/tmp/clubos_prod/clubos.db")
con=sqlite3.connect(db); con.execute("UPDATE auth_accounts SET status='disabled' WHERE username='club1_admin'"); con.commit(); con.close()
r=httpx.Client(base_url=BASE, trust_env=False, verify=False).post("/api/auth/login", json={"username":"club1_admin","password":PW})
rec("PROD disabled account login -> 401", r.status_code==401, f"HTTP {r.status_code}")
con=sqlite3.connect(db); con.execute("UPDATE auth_accounts SET status='active' WHERE username='club1_admin'"); con.commit(); con.close()
r=httpx.Client(base_url=BASE, trust_env=False, verify=False).post(BASE+"/api/public/checkouts/none/confirm", json={"commerceOrderId":"x"})
rec("PROD demo-money bypass denied (403)", r.status_code==403, f"HTTP {r.status_code}")
r=httpx.get(BASE+"/api/public/clubs/1", follow_redirects=False, trust_env=False, verify=False)
rec("PROD PUBLIC /api/public/clubs/1 (200)", r.status_code==200, f"HTTP {r.status_code}")
c=httpx.Client(base_url=BASE, trust_env=False, verify=False); codes=[c.post("/api/auth/login", json={"username":"platform_admin","password":"bad"+str(i)}).status_code for i in range(7)]
rec("PROD login rate limit -> 429", 429 in codes, f"codes={codes}")
passed=sum(1 for _,s,_ in results if s=="PASS"); failed=sum(1 for _,s,_ in results if s=="FAIL")
print(f"\nPROD SECURITY ACCEPTANCE: TOTAL={len(results)} PASS={passed} FAIL={failed}")
for n,s,d in results:
    if s=="FAIL": print("  FAIL:",n,d)
