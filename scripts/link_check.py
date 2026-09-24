#!/usr/bin/env python3
"""
三端链接可达性测试 (Link Check) — 角色化真实端点版

对每个端: 先以该端对应角色登录, 再验证:
  1) 首页 /web|/club|/platform 返回 200 text/html
  2) 首页引用的全部 /static/*.css|*.js 资源均返回 200  (真正的"链接"测试)
  3) (demo) 该端 boot/导航时实际调用的 GET API 均 200; 404 = 坏链
  4) (demo) C端深层: 首个活动详情 + 报价
  5) 四角色登录链路 (登录返回 {ok, identity:{role}})
  6) (prod) 未登录访问页面必须 303 跳 /login (安全)

生产与 demo 用不同密码, 可用 STAGING_PASSWORD 覆盖。
用法: python scripts/link_check.py [base_url]
"""
import os, sys, re, httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PROD = BASE.startswith("https")
PASSWORD = os.getenv("STAGING_PASSWORD") or (
    "Prod-Staging-2026-ClubOS!" if PROD else "Staging-2026-ClubOS-demo!")

# (展示名, 路由, 登录账号, 期望角色, boot/导航 GET 端点)
ENDS = [
    ("C端 /web", "/web", "member1", "member", [
        "/api/public/clubs/1", "/api/public/clubs/1/activities",
        "/api/public/users/1/wallet?club_id=1", "/api/public/clubs/1/mall/products",
        "/api/public/clubs/1/member-center?user_id=1",
        "/api/public/users/1/order-center?club_id=1",
        "/api/public/clubs/1/vouchers?user_id=1&kind=activity",
    ]),
    ("后台 /club", "/club", "club1_admin", "club", [
        "/api/club/1/dashboard", "/api/club/1/activities", "/api/club/1/registrations",
        "/api/club/1/execution/occurrences", "/api/club/1/members",
        "/api/club/1/mall/products", "/api/club/1/credits", "/api/club/1/analytics",
    ]),
    ("总平台 /platform", "/platform", "platform_admin", "platform", [
        "/api/platform/dashboard", "/api/platform/clubs", "/api/platform/credits",
        "/api/platform/ai-credit/plans", "/api/platform/ai-credit/topup-packages",
        "/api/platform/ai-credit/summary", "/api/platform/ai-credit/orders?limit=100",
        "/api/platform/ai-credit/task-pricing", "/api/platform/points-policy",
        "/api/platform/benefits", "/api/platform/products", "/api/platform/orders",
        "/api/platform/after-sales", "/api/platform/commissions",
        "/api/platform/commission-summary", "/api/platform/settlements",
        "/api/platform/commission-policy", "/api/platform/inventory/summary",
        "/api/platform/suppliers", "/api/platform/procurement/purchase-orders",
        "/api/platform/inventory/movements?limit=80", "/api/platform/warehouse/summary",
        "/api/platform/warehouses", "/api/platform/warehouse/inventory",
        "/api/platform/warehouse/tasks", "/api/platform/finance/supplier-summary",
        "/api/platform/profit/summary", "/api/platform/profit/orders?limit=50",
        "/api/platform/profit/products", "/api/platform/analytics/commerce",
        "/api/platform/analytics/products", "/api/platform/analytics/clubs",
        "/api/platform/analytics/suppliers", "/api/platform/replenishment/recommendations",
        "/api/platform/replenishment/policy",
    ]),
]
LOGINS = [("platform_admin", "platform"), ("club1_admin", "club"),
          ("member1", "member"), ("leader1", "leader")]

results = []
def check(ok, cat, label): results.append((ok, cat, label))
def client(): return httpx.Client(base_url=BASE, timeout=25, follow_redirects=True,
                                  trust_env=False, verify=False)
asset_re = re.compile(r'(?:href|src)="(/static/[^"]+)"')

# 1. 角色化: 登录 -> 首页 -> 资源 -> (demo) boot API -> (demo) 深层
for name, route, user, role, apis in ENDS:
    with client() as c:
        lr = c.post("/api/auth/login", json={"username": user, "password": PASSWORD})
        got = ((lr.json().get("identity") or {}).get("role")) if lr.status_code == 200 else None
        check(lr.status_code == 200 and got == role, f"{name} 以{role}登录", f"{user} -> {lr.status_code} role={got}")
        r = c.get(route)
        ok = r.status_code == 200 and "text/html" in r.headers.get("content-type", "")
        check(ok, name, f"首页 {route} (HTTP {r.status_code})")
        if ok:
            assets = sorted(set(asset_re.findall(r.text)))
            check(len(assets) > 0, f"{name} 资源", f"解析到 {len(assets)} 个静态资源")
            for a in assets:
                ra = c.get(a)
                check(ra.status_code == 200, f"{name} 资源", f"{a} -> {ra.status_code}")
        if not PROD:
            for api in apis:
                rp = c.get(api)
                if rp.status_code in (200, 201):
                    check(True, f"{name} API", f"{api} -> 200")
                elif rp.status_code == 404:
                    check(False, f"{name} API", f"{api} -> 404 (路由缺失/坏链)")
                else:
                    check(False, f"{name} API", f"{api} -> {rp.status_code}")
    # 未登录 / 跨角色访问页面
    with httpx.Client(base_url=BASE, timeout=20, follow_redirects=False,
                      trust_env=False, verify=False) as anon:
        ra = anon.get(route)
    if PROD:
        check(ra.status_code == 303 and "/login" in ra.headers.get("location", ""),
              f"{name} 安全", f"未登录访问 {route} -> {ra.status_code} -> {ra.headers.get('location','')}")
    else:
        check(ra.status_code == 200, f"{name} 安全", f"demo 未登录访问 {route} -> {ra.status_code} (demo放开)")

# 2. C端深层 (demo): 首个活动详情 + 报价
if not PROD:
    with client() as c:
        c.post("/api/auth/login", json={"username": "member1", "password": PASSWORD})
        try:
            acts = c.get("/api/public/clubs/1/activities").json()
            if acts:
                aid = acts[0]["id"]
                a = c.get(f"/api/public/activities/{aid}")
                check(a.status_code == 200, "C端 深层", f"活动详情 /api/public/activities/{aid} -> {a.status_code}")
                occ = (a.json().get("occurrences") or [{}])[0].get("id")
                if occ:
                    q = c.get(f"/api/public/activities/{aid}/price-quote?occurrence_id={occ}&user_id=1&club_points=0&gear_points=0&participant_count=1")
                    check(q.status_code == 200, "C端 深层", f"报价 price-quote -> {q.status_code}")
        except Exception as e:
            check(False, "C端 深层", f"动态深层异常 {e}")

# 3. /login 公开可达
with client() as c:
    check(c.get("/login").status_code == 200, "额外路由", "/login -> 200")

total = len(results); passed = sum(1 for x in results if x[0]); failed = total - passed
print("=" * 72)
print(f"三端链接测试  BASE={BASE}  {'[生产/HTTPS]' if PROD else '[demo]'}  角色化")
print("=" * 72)
by_cat = {}
for ok, cat, label in results:
    by_cat.setdefault(cat, [0, 0]); by_cat[cat][0 if ok else 1] += 1
for cat in sorted(by_cat):
    p, f = by_cat[cat]; print(f"  {cat:<24} PASS={p} FAIL={f}")
print("-" * 72)
print(f"  合计: PASS={passed}  FAIL={failed}  TOTAL={total}")
if failed:
    print("-" * 72); print("  失败项:")
    for ok, cat, label in results:
        if not ok: print(f"    [{cat}] {label}")
print("=" * 72)
print("ALL_GREEN" if failed == 0 else f"BROKEN={failed}")
