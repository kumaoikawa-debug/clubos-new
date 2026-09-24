"""
Staging LIVE acceptance (DEMO mode) for ClubOS NEW v0.25.1 handoff.
Validates: three-end pages serve, four-role login, public read APIs, role endpoints,
and the two main business chains (Activity AI-generate->publish->public->signup,
and Gear order -> Gear Points) against a RUNNING ClubOS backend.

NOTE: demo mode intentionally does NOT enforce auth/RBAC (security_v025.authorize_request
returns None when CLUBOS_SECURITY_MODE!=production). Role gating / tenant isolation / CSRF /
disabled-account are covered by acceptance_prod_security.py (production mode).

Run:  CLUBOS_SECURITY_MODE=demo CLUBOS_DB_PATH=/tmp/clubos_staging/clubos.db \
      python scripts/acceptance_demo_live.py
The backend must already be running (default http://127.0.0.1:8000). Set BASE via env to override.
Set STAGING_PASSWORD to the provisioned demo password.

trust_env=False is required in the WorkBuddy sandbox because an HTTP proxy is injected into the
environment; without it httpx routes 127.0.0.1 through the proxy and gets 404.
"""
import os, sys, httpx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.getenv("BASE", "http://127.0.0.1:8000")
PW = os.getenv("STAGING_PASSWORD", "Staging-2026-ClubOS-demo!")
ROLES = {"platform_admin":"platform","club1_admin":"club","leader1":"leader","member1":"member"}
results = []
def rec(n, ok, d=""):
    results.append((n, "PASS" if ok else "FAIL", d)); print(("PASS " if ok else "FAIL ")+n+(f"  [{d}]" if d else ""))
def login(u):
    c=httpx.Client(base_url=BASE, timeout=20, follow_redirects=False, trust_env=False)
    r=c.post("/api/auth/login", json={"username":u,"password":PW}); csrf=c.cookies.get("clubos_csrf")
    if csrf: c.headers["x-clubos-csrf"]=csrf
    return c, r
for p in ["/login","/platform","/club","/web","/leader"]:
    r=httpx.get(BASE+p, follow_redirects=False, trust_env=False)
    rec(f"PAGE {p}", r.status_code==200, f"HTTP {r.status_code}")
clients={}
for u,role in ROLES.items():
    c,r=login(u); ok=r.status_code==200 and r.json().get("ok") is True
    rec(f"LOGIN {u} ({role})", ok, f"HTTP {r.status_code}")
    if ok: clients[u]=c
r=httpx.Client(base_url=BASE, trust_env=False).post("/api/auth/login", json={"username":"platform_admin","password":"wrong"})
rec("LOGIN wrong password -> 401", r.status_code==401, f"HTTP {r.status_code}")
for p in ["/api/public/clubs/1","/api/public/clubs/1/activities","/api/public/clubs/1/mall/products","/api/commerce/status","/api/health"]:
    r=httpx.get(BASE+p, follow_redirects=False, trust_env=False); rec(f"PUBLIC {p}", r.status_code==200, f"HTTP {r.status_code}")
if "club1_admin" in clients:
    c=clients["club1_admin"]
    r=c.post("/api/club/1/activities/ai-generate", data={"prompt":"2026年11月2日，成都周边青城山，轻徒步8公里，20人，198元/人。"})
    aid=r.json().get("activityId") if r.status_code==200 else None; rec("CHAIN-A ai-generate", bool(aid), f"aid={aid}")
    if aid:
        r=c.post(f"/api/club/1/activities/{aid}/publish"); rec("CHAIN-A publish", r.status_code==200, f"HTTP {r.status_code}")
        r=httpx.get(BASE+f"/api/public/activities/{aid}", trust_env=False)
        occ=((r.json().get("occurrences") or [{}])[0].get("id")) if r.status_code==200 else None; rec("CHAIN-A public+occurrence", bool(occ), f"occ={occ}")
        if occ and "member1" in clients:
            r=httpx.get(BASE+f"/api/public/activities/{aid}/price-quote?occurrence_id={occ}&user_id=1&club_points=0&gear_points=0", trust_env=False)
            rec("CHAIN-A price-quote", r.status_code==200 and r.json().get('payable') is not None, f"HTTP {r.status_code}")
            r=clients["member1"].post(f"/api/public/activities/{aid}/signup", json={"name":"林野","phone":"13800000001","occurrenceId":occ,"clubPoints":0,"gearPoints":0})
            rec("CHAIN-A signup", r.status_code==200 and r.json().get('registrationId'), f"HTTP {r.status_code}")
if "member1" in clients:
    r=clients["member1"].post("/api/public/clubs/1/gear-orders", json={"userId":1,"items":[{"productId":2,"quantity":1}]})
    gp=r.json().get("gearPointsEarned",0) if r.status_code==200 else 0; rec("CHAIN-B gear order + Gear Points", r.status_code==200 and gp>0, f"gp={gp}")
passed=sum(1 for _,s,_ in results if s=="PASS"); failed=sum(1 for _,s,_ in results if s=="FAIL")
print(f"\nDEMO LIVE ACCEPTANCE: TOTAL={len(results)} PASS={passed} FAIL={failed}")
for n,s,d in results:
    if s=="FAIL": print("  FAIL:",n,d)
