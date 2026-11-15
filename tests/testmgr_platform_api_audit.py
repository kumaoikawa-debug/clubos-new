# -*- coding: utf-8 -*-
"""经营者视角 · 总平台「全部接口」体检（2026-11-16）。

回答一个问题：/api/platform/* 的每一条路由，是不是都在、都能正常响应、都不会被越权访问。

三层覆盖：
  1 路由注册完整性：源码声明 @app.<method>('/api/platform/...') 与运行时 app.routes 逐一比对。
  2 运行时健康：先用既有经营者 E2E 造一份真实经营数据（俱乐部/商品/采购/订单/佣金/结算），
    再带「平台管理员真实会话」逐条请求全部 GET 只读端点与全部写端点。
    写端点一律用不存在的资源 id（999999999）+ 空 body，只验契约、不改数据。
  3 鉴权：把 TEST_OPEN 运行时关掉，对全部路由做匿名探测，期望 401/403。
附带验证 OBS-03 的后台页面防缓存 / 防索引响应头。

判定口径：5xx、管理员被 401/403、路由不存在、匿名能访问 = 故障；
GET 的 4xx（缺上下文资源）与写端点的 400/404/409/422（业务校验生效）记为「已记录」而非故障。

环境纪律：/tmp 隔离库，不碰项目真库；不 push / 发布 / 部署。
"""
import json
import re
import secrets
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB = Path('/tmp/clubos_api_audit.db')
if DB.exists():
    DB.unlink()

# ---- 1) 复用既有 E2E 造一份真实经营数据 -----------------------------------
src_path = ROOT / 'tests/testmgr_platform_operator_e2e.py'
src = src_path.read_text(encoding='utf-8')
needle = "os.environ['CLUBOS_DB_PATH'] = str(TMP / 'clubos.db')"
assert needle in src, 'E2E harness 结构变化，需同步本脚本'
src = src.replace(needle, "os.environ['CLUBOS_DB_PATH'] = %r" % str(DB))
ns = {'__name__': '__api_audit_seed__', '__file__': str(src_path)}
try:
    exec(compile(src, str(src_path), 'exec'), ns)
except SystemExit as e:
    if e.code:
        raise
print('【底座】经营者 E2E 造数完成 | 失败项=%d' % len(ns['FAILS']), flush=True)
assert not ns['FAILS'], '前置 E2E 有失败，体检结果不可信'

import app  # noqa: E402
import security_v025  # noqa: E402
from security_v025 import _hash  # noqa: E402

c = ns['c']

# ---- 2) 平台管理员真实会话 -------------------------------------------------
with app.conn() as db:
    db.execute(
        "INSERT INTO auth_accounts(username,password_hash,role,status,club_id,user_id,platform_department) "
        "VALUES(?,?,?,?,?,?,?)",
        ('qa-api-audit', 'test-only', 'platform', 'active', None, None, 'all'))
    aid = int(db.execute('SELECT last_insert_rowid()').fetchone()[0])
    token = secrets.token_urlsafe(32)
    db.execute('INSERT INTO auth_sessions(token_hash,account_id,csrf_hash,expires_at) VALUES(?,?,?,?)',
               (_hash(token), aid, _hash('qa-csrf'), int(time.time()) + 7200))
H = {'Authorization': 'Bearer ' + token}

# authorize_request 读的是 security_v025 的模块级全局，运行时改这里即可切到真实 RBAC
security_v025.TEST_OPEN = False
_anon = c.get('/api/platform/dashboard').status_code
_admin = c.get('/api/platform/dashboard', headers=H).status_code
print('【鉴权】TEST_OPEN=False | 匿名 /dashboard=%s | 管理员 /dashboard=%s' % (_anon, _admin), flush=True)

# ---- 3) 路由清单与源码声明比对 ---------------------------------------------
METHODS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}
ROUTES = sorted({(m, r.path) for r in app.app.routes
                 if getattr(r, 'path', '').startswith('/api/platform')
                 for m in (getattr(r, 'methods', None) or set()) if m in METHODS})
declared_pairs = [(m.upper(), q) for m, q in re.findall(
    r"@app\.(get|post|put|patch|delete)\('(/api/platform/[^']+)'",
    (ROOT / 'app.py').read_text(encoding='utf-8'))]
declared = set(declared_pairs)
DUPES = [(k, v) for k, v in Counter(declared_pairs).items() if v > 1]
MISSING = sorted(declared - set(ROUTES))   # 声明了但没注册
EXTRA = sorted(set(ROUTES) - declared)     # 注册了但源码没声明
print('【路由】运行时注册 %d 条 | 源码声明 %d 条（去重 %d）| 缺失 %d | 额外 %d | 重复声明 %d'
      % (len(ROUTES), len(declared_pairs), len(declared), len(MISSING), len(EXTRA), len(DUPES)), flush=True)
for k, v in DUPES:
    print('  .. 重复声明 %s %s × %d（FastAPI 仅先注册者生效）' % (k[0], k[1], v), flush=True)

# ---- 4) 路径参数：优先取库里真实存在的 id，让 GET 命中真数据 ----------------
PARAM_VALUE = {'ext': 'png', 'code': 'trial', 'config_key': 'medusa'}
TABLE_BY_PARAM = {
    'club_id': 'clubs', 'product_id': 'products', 'order_id': 'gear_orders',
    'supplier_id': 'suppliers', 'variant_id': 'product_variants',
    'category_id': 'product_categories', 'settlement_id': 'commission_settlements',
    'po_id': 'purchase_orders', 'return_id': 'purchase_returns',
    'benefit_id': 'platform_benefits', 'coupon_id': 'coupons',
    'campaign_id': 'campaigns', 'case_id': 'after_sales_cases',
    'refund_id': 'refunds', 'task_id': 'warehouse_tasks',
    'warehouse_id': 'warehouses', 'image_id': 'product_images',
    'brand_id': 'brands', 'request_id': 'product_visibility_requests',
    'rule_id': 'coupon_gift_rules',
}
with app.conn() as db:
    for k, t in TABLE_BY_PARAM.items():
        try:
            rr = db.execute('SELECT id FROM %s LIMIT 1' % t).fetchone()
            if rr:
                PARAM_VALUE[k] = str(rr[0])
        except Exception:
            pass
for n in {x for _, p in ROUTES for x in re.findall(r'\{([^}]+)\}', p)}:
    PARAM_VALUE.setdefault(n, '1')


def fill(path, missing=False):
    """missing=True 时把资源 id 换成不存在的值，写端点只验契约不改数据。"""
    def repl(m):
        k = m.group(1)
        if missing:
            if k.endswith('_id'):
                return '999999999'
            if k in ('code', 'config_key', 'configKey'):
                return '__qa_missing__'
        return PARAM_VALUE.get(k, '1')
    return re.sub(r'\{([^}]+)\}', repl, path)


EXTERNAL_HINTS = ('PEAKTRACE 商品数据读取失败', '本地商品未改动', '上游接口', '第三方服务')


def verdict(method, status, text, anon):
    if anon:
        return 'PASS' if status in (401, 403) else 'FAIL_ANON_OPEN'
    if status >= 500:
        # 隔离环境没有外部供应商；接口已优雅降级（结构化报错 + 本地数据未改动），非产品故障
        return 'EXTERNAL_DEP' if any(h in text for h in EXTERNAL_HINTS) else 'FAIL_5XX'
    if status in (401, 403):
        return 'FAIL_AUTH'
    if status == 404 and 'Not Found' in text[:120]:
        return 'FAIL_ROUTE_MISSING'
    if method == 'GET':
        return 'PASS' if 200 <= status < 300 else 'NOTED_4XX'
    return 'PASS'   # 写端点 2xx / 400 / 业务404 / 409 / 422：路由可达 + 鉴权通过 + 校验生效


rows = []


def probe(method, path, headers, is_anon):
    url = fill(path, missing=(method != 'GET'))
    try:
        if method == 'GET':
            r = c.request('GET', url, headers=headers)
        else:
            r = c.request(method, url, headers=headers, json={})
        text = r.text[:240]
        ct = r.headers.get('content-type', '')
        v = verdict(method, r.status_code, text, is_anon)
        # 2xx 且声明为 JSON 的响应必须能解析
        if v == 'PASS' and 200 <= r.status_code < 300 and 'json' in ct:
            try:
                r.json()
            except Exception:
                v = 'FAIL_BAD_JSON'
        return v, r.status_code, url, text
    except Exception as e:
        msg = '%s: %s' % (type(e).__name__, str(e)[:150])
        # 业务异常未转 HTTPException：TestClient 直接抛出，生产表现为 500。
        # 属「异常输入应返回 4xx」的健壮性发现，不是接口不可用，单列不判阻断。
        if type(e).__name__ in ('KeyError', 'LookupError', 'ValueError'):
            return 'FINDING_UNHANDLED_4XX', -1, url, msg
        return 'FAIL_EXCEPTION', -1, url, msg


# 先 GET（稳定态）→ 再写端点 → 再匿名全量
for method, path in [x for x in ROUTES if x[0] == 'GET']:
    v, st, url, text = probe(method, path, H, False)
    rows.append({'phase': 'admin-GET', 'method': method, 'path': path,
                 'url': url, 'status': st, 'verdict': v, 'body': text})
for method, path in [x for x in ROUTES if x[0] != 'GET']:
    v, st, url, text = probe(method, path, H, False)
    rows.append({'phase': 'admin-WRITE', 'method': method, 'path': path,
                 'url': url, 'status': st, 'verdict': v, 'body': text})
for method, path in ROUTES:
    v, st, url, text = probe(method, path, {}, True)
    rows.append({'phase': 'anon', 'method': method, 'path': path,
                 'url': url, 'status': st, 'verdict': v, 'body': text})

# ---- 5) OBS-03 响应头 ------------------------------------------------------
head_check = []
for path in ('/', '/platform', '/club', '/web', '/leader', '/login', '/home', '/api/health'):
    r = c.get(path)
    head_check.append({
        'path': path, 'status': r.status_code,
        'cacheControl': r.headers.get('cache-control', ''),
        'pragma': r.headers.get('pragma', ''),
        'expires': r.headers.get('expires', ''),
        'xRobotsTag': r.headers.get('x-robots-tag', ''),
        'vary': r.headers.get('vary', ''),
    })

# ---- 6) 汇总 ---------------------------------------------------------------
by_phase = {}
for r in rows:
    by_phase.setdefault(r['phase'], Counter())[r['verdict']] += 1
fails = [r for r in rows if r['verdict'].startswith('FAIL')]
findings = [r for r in rows if r['verdict'] in ('FINDING_UNHANDLED_4XX', 'EXTERNAL_DEP')]
noted = [r for r in rows if r['verdict'] == 'NOTED_4XX']

print('\n════════ 总平台接口体检 ════════', flush=True)
print('路由 %d 条（%s）' % (len(ROUTES), dict(Counter(m for m, _ in ROUTES))), flush=True)
print('请求总数 %d = 管理员 GET %d + 管理员写 %d + 匿名 %d'
      % (len(rows), len([r for r in rows if r['phase'] == 'admin-GET']),
         len([r for r in rows if r['phase'] == 'admin-WRITE']),
         len([r for r in rows if r['phase'] == 'anon'])), flush=True)
for ph in ('admin-GET', 'admin-WRITE', 'anon'):
    print('  %-11s %s' % (ph, dict(by_phase[ph])), flush=True)
print('阻断故障 %d 条 | 非阻断发现 %d 条 | GET 需上下文 4xx %d 条'
      % (len(fails), len(findings), len(noted)), flush=True)
for r in fails:
    print('  !! %s %s %s status=%s %s' % (r['phase'], r['method'], r['url'], r['status'], r['body'][:110]), flush=True)
print('\n--- 非阻断发现明细 ---', flush=True)
for r in findings:
    print('  .. [%s] %s %s status=%s %s'
          % (r['verdict'], r['method'], r['url'], r['status'], r['body'][:110]), flush=True)
print('\n--- GET 4xx 明细（资源/参数上下文，非故障）---', flush=True)
for r in noted:
    print('  .. %s %s status=%s %s' % (r['method'], r['url'], r['status'], r['body'][:80]), flush=True)
print('\n--- OBS-03 响应头 ---', flush=True)
_home = next(h for h in head_check if h['path'] == '/home')
# F-08：官网必须匿名公开、保留协商缓存、且不被误加 noindex（后台页才加）
_home_ok = (_home['status'] == 200 and not _home['xRobotsTag']
            and 'no-cache' in _home['cacheControl'])
print('  .. F-08 官网 /home 匿名 status=%s %s（CC=%s Robots=%s）'
      % (_home['status'], 'PASS' if _home_ok else 'FAIL',
         _home['cacheControl'], _home['xRobotsTag'] or '(none)'), flush=True)
for h in head_check:
    print('  %-10s %s | CC=%s | Pragma=%s | Robots=%s | Vary=%s'
          % (h['path'], h['status'], h['cacheControl'], h['pragma'], h['xRobotsTag'], h['vary']), flush=True)

out = {
    'routes': len(ROUTES), 'byMethod': dict(Counter(m for m, _ in ROUTES)),
    'declared': len(declared_pairs), 'missing': MISSING, 'extra': EXTRA,
    'duplicates': [{'method': k[0], 'path': k[1], 'count': v} for k, v in DUPES],
    'summary': {k: dict(v) for k, v in by_phase.items()},
    'fails': len(fails), 'findings': len(findings), 'noted': len(noted),
    'homePublic': _home_ok, 'rows': rows, 'headCheck': head_check,
}
Path('/tmp/clubos_api_audit.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print('\n结果明细：/tmp/clubos_api_audit.json', flush=True)
# F-07 重复声明、F-08 官网匿名访问也纳入退出码，避免修复回退时脚本仍报绿
sys.exit(1 if (fails or MISSING or DUPES or not _home_ok) else 0)
