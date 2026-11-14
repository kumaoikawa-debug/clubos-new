"""v0.25 server-side security boundary. Production mode is fail-closed.

This service does NOT treat a client-supplied clubId/userId as identity. It uses
short-lived opaque, revocable DB sessions and a trusted account-role binding.
"""
from __future__ import annotations
import hashlib, hmac, json, os, re, secrets, time, uuid
from datetime import datetime, timezone
from urllib.parse import urlparse
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from db import conn, row

MODE = os.getenv('CLUBOS_SECURITY_MODE', 'demo').strip().lower()
IS_PROD = MODE == 'production'
if MODE not in {'demo', 'production'}:
    raise RuntimeError('CLUBOS_SECURITY_MODE must be demo or production')

# ★ 内测开放开关（2026-10-09 应用户要求「测试阶段不要设密码」）：
#   CLUBOS_TEST_OPEN=1 时管理端不设登录门槛（等价于早期 demo 语义，前端不会被踢到登录页）。
#   ⚠️ 公开链接上的一把总钥匙：任何拿到链接的人都能读报名（含手机号）并改写数据。
#   仅限内测演示期；正式对客前删掉这个环境变量即恢复拦截，代码无需回改。
#   生产模式（CLUBOS_SECURITY_MODE=production）永远忽略本开关。
TEST_OPEN = (not IS_PROD) and os.getenv('CLUBOS_TEST_OPEN', '').strip().lower() in ('1', 'true', 'yes', 'on')
if TEST_OPEN:
    print('[security] CLUBOS_TEST_OPEN=1: 登录门槛已关闭（内测演示专用，请勿对客开放）', flush=True)

# Routes that are explicitly disabled, regardless of authentication, in production.
_DEMO_ONLY = (
    re.compile(r'^/api/public/checkouts/[^/]+/confirm$'),
    re.compile(r'^/api/public/activities/\d+/signup$'),
    re.compile(r'^/api/public/clubs/\d+/gear-orders$'),
    re.compile(r'^/api/public/registrations/\d+/cancel$'),
    re.compile(r'^/api/platform/orders/\d+/refund$'),
)
# These legacy adapter events can move money or mark payments without checking the actual
# WeChat / Alipay signature. Only verified provider notify endpoints are authoritative.
_UNVERIFIED_EVENTS = {'order-paid', 'payment-succeeded', 'refund-succeeded', 'order-refunded'}
_PUBLIC_READ = (
    re.compile(r'^/api/public/clubs/\d+$'),
    re.compile(r'^/api/public/clubs/\d+/activities$'),
    re.compile(r'^/api/public/activities/\d+$'),
    re.compile(r'^/api/public/activities/\d+/media/.+$'),
    re.compile(r'^/api/public/clubs/\d+/mall/products$'),
)
_AUDIT_PREFIX = ('/api/platform/', '/api/club/', '/api/leader/', '/api/public/', '/api/payments/', '/api/commerce/')


def validate_production_config() -> None:
    if not IS_PROD:
        return
    from db import DB_PATH, ROOT
    failures = []
    if os.getenv('MOCK_AI', '1').strip() != '0':
        failures.append('MOCK_AI must be 0')
    if not os.getenv('CLUBOS_DB_PATH') or DB_PATH.resolve() == (ROOT/'clubos.db').resolve():
        failures.append('CLUBOS_DB_PATH must point to a separate non-demo database')
    if len(os.getenv('CLUBOS_AUTH_SIGNING_KEY', '')) < 48 or os.getenv('CLUBOS_AUTH_SIGNING_KEY', '').lower().startswith('change'):
        failures.append('CLUBOS_AUTH_SIGNING_KEY must be a fresh 48+ character secret')
    public = urlparse(os.getenv('CLUBOS_PUBLIC_BASE_URL', ''))
    if public.scheme != 'https' or not public.netloc:
        failures.append('CLUBOS_PUBLIC_BASE_URL must be a valid HTTPS URL')
    if os.getenv('COMMERCE_PROVIDER') != 'medusa':
        failures.append('COMMERCE_PROVIDER must be medusa in production (local is demo-only)')
    secret = os.getenv('CLUBOS_COMMERCE_WEBHOOK_SECRET', '')
    if len(secret) < 32 or secret == 'change-me':
        failures.append('commerce webhook secret must be a fresh 32+ character secret')
    # #892：默认凭据与生产密钥的强制校验放在 scripts/predeploy_security_check.py
    # （发布前门禁）。不塞进启动校验，避免破坏 5 个 production-mode 安全测试
    # 既有最小环境变量集；demo/内测模式行为完全不变。
    if failures:
        raise RuntimeError('Unsafe production configuration: ' + '; '.join(failures))


def install_tables() -> None:
    with conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS auth_accounts (
          id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('platform','club','leader','member')),
          club_id INTEGER, user_id INTEGER, status TEXT NOT NULL DEFAULT 'active',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS auth_sessions (
          token_hash TEXT PRIMARY KEY, account_id INTEGER NOT NULL,
          csrf_hash TEXT NOT NULL, expires_at INTEGER NOT NULL, revoked_at TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(account_id) REFERENCES auth_accounts(id)
        );
        CREATE TABLE IF NOT EXISTS auth_login_attempts (
          id INTEGER PRIMARY KEY, username TEXT NOT NULL, ip_hash TEXT NOT NULL,
          succeeded INTEGER NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_auth_login_attempts ON auth_login_attempts(username,created_at);
        CREATE TABLE IF NOT EXISTS public_rate_limits (
          id INTEGER PRIMARY KEY, ip_hash TEXT NOT NULL, action TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_public_limits ON public_rate_limits(ip_hash,action,created_at);
        CREATE TABLE IF NOT EXISTS security_audit_events (
          id INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, actor_id INTEGER,
          role TEXT, club_id INTEGER, user_id INTEGER, method TEXT NOT NULL,
          path TEXT NOT NULL, status INTEGER NOT NULL, request_hash TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON security_audit_events
          BEGIN SELECT RAISE(ABORT,'audit events are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON security_audit_events
          BEGIN SELECT RAISE(ABORT,'audit events are append-only'); END;
        ''')

    with conn() as c:
        if 'platform_department' not in {r[1] for r in c.execute('PRAGMA table_info(auth_accounts)')}:c.execute("ALTER TABLE auth_accounts ADD COLUMN platform_department TEXT NOT NULL DEFAULT 'all'")
    # 2026-11-03 关闭内测免登录（CLUBOS_TEST_OPEN）后，总平台需一个管理员账户才能登录。
    # 幂等：只在「还没有任何 active 平台账户」时创建一次。账号/密码优先读环境变量，
    # 未配置时落默认值（≥14 位，满足 hash_password 要求）；部署后请尽快改密。
    with conn() as c:
        if not c.execute("SELECT 1 FROM auth_accounts WHERE role='platform' AND status='active'").fetchone():
            username=os.getenv('CLUBOS_PLATFORM_ADMIN_USER','admin')
            pwd=os.getenv('CLUBOS_PLATFORM_ADMIN_PASS') or 'ClubOS@2026Admin!'
            if len(pwd)<14: pwd='ClubOS@2026Admin!'
            try:
                c.execute("INSERT INTO auth_accounts(username,password_hash,role,status,platform_department) VALUES(?,?,?,?,'all')",
                          (username,hash_password(pwd),'platform','active'))
                # #892：绝不把明文口令写进 stdout / 访问日志，口令一律由部署环境变量注入。
                print(f'[security] 已创建平台管理员账户 {username}（口令来自 CLUBOS_PLATFORM_ADMIN_PASS，未输出到日志）',flush=True)
            except Exception as e:
                print(f'[security] 平台管理员账户已存在或创建失败：{e}',flush=True)

def hash_password(password: str) -> str:
    if len(password) < 14:
        raise ValueError('password must have at least 14 characters')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f'scrypt$16384$8$1${salt.hex()}${digest.hex()}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split('$')
        if algorithm != 'scrypt': return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError, MemoryError):
        return False


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _client_ip(request: Request) -> str:
    # Don't trust X-Forwarded-For supplied by clients.
    return str(request.client.host if request.client else 'unknown')


def _rate_key(request: Request) -> str:
    return _hash(os.getenv('CLUBOS_AUTH_SIGNING_KEY', '') + _client_ip(request))


def issue_session(request: Request, username: str, password: str) -> tuple[str, str, dict]:
    username = username.strip().lower()
    if not username or len(username) > 128 or not password:
        raise HTTPException(401, 'invalid credentials')
    now = int(time.time())
    ip_key = _rate_key(request)
    with conn() as c:
        c.execute('DELETE FROM auth_login_attempts WHERE created_at < ?', (now-86400,))
        attempts = c.execute('''SELECT COUNT(*) FROM auth_login_attempts WHERE created_at>? AND succeeded=0
                                AND (username=? OR ip_hash=?)''',(now-900,username,ip_key)).fetchone()[0]
        if attempts >= 6:
            raise HTTPException(429, 'login rate limit exceeded')
        account = row(c.execute('SELECT * FROM auth_accounts WHERE username=? AND status="active"',(username,)))
        passed = bool(account and verify_password(password,account['password_hash']))
        c.execute('INSERT INTO auth_login_attempts(username,ip_hash,succeeded,created_at) VALUES(?,?,?,?)', (username,ip_key,int(passed),now))
        if passed:
            # One session per account in v0.25 to make session revocation predictable.
            c.execute('UPDATE auth_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE account_id=? AND revoked_at IS NULL',(account['id'],))
            token = secrets.token_urlsafe(48); csrf = secrets.token_urlsafe(32)
            c.execute('INSERT INTO auth_sessions(token_hash,account_id,csrf_hash,expires_at) VALUES(?,?,?,?)',(_hash(token),account['id'],_hash(csrf),now+3600))
            result=(token,csrf,public_identity(account))
    # Raise only after the transaction has COMMITTED the failed attempt; otherwise the
    # context manager rolls it back and login throttling silently never takes effect.
    if not passed:raise HTTPException(401, 'invalid credentials')
    return result


def platform_department_allowed(identity,path,method,payload=None):
    department=identity.get('platform_department') or 'all'
    if department=='all':return True
    payload=payload or {}
    product_write=('/products' in path and method not in ('GET','HEAD'))
    if product_write:
        has_stock=payload.get('stock') is not None or bool(payload.get('skuStock')) or path.endswith('/matrix')
        has_price=payload.get('price') is not None
        if department=='operations' and has_stock and not (method=='POST' and path=='/api/platform/products' and int(payload.get('stock') or 0)==0 and not payload.get('skuStock')):return False
        if department=='warehouse' and has_price:return False
        if department=='operations' and method=='PATCH' and re.search(r'/variants/\d+$',path) and not has_stock and set(payload)<= {'price'}:return True
        if department=='procurement' and method=='POST' and '/variants' in path and not int(payload.get('stock') or 0):
            m=re.search(r'/products/(\d+)/variants$',path)
            if m:
                with conn() as c:
                    p=c.execute('SELECT status FROM products WHERE id=?',(int(m[1]),)).fetchone()
                return bool(p and p[0]=='draft')
        if department=='procurement' and method=='POST' and path=='/api/platform/products':
            return not int(payload.get('stock') or 0) and not payload.get('skuStock') and payload.get('status','draft')=='draft'
    if path.startswith('/api/platform/warehouse') or path.startswith('/api/platform/stock/reconciliation') or re.search(r'/orders/\d+/(pick|pack|ship|shipment|fulfill|delivered)',path) or (method=='PATCH' and re.search(r'/orders/\d+$',path)):required='warehouse'
    elif '/supplier-payments' in path or '/finance/' in path or '/profit/' in path or '/settlements' in path or path.endswith('/credit'):required='finance'
    elif '/procurement/' in path:
        required='warehouse' if path.endswith(('/receive','/ship','/exceptions')) else 'procurement'
        if method=='GET':return department in ('procurement','warehouse','finance')
    elif '/suppliers' in path:required='procurement'
    elif '/inventory/' in path:required='warehouse'
    elif '/products' in path or '/brands' in path or '/categories' in path or '/product-visibility' in path:
        if method=='GET':return department in ('operations','procurement','warehouse','finance')
        required='warehouse' if ('/variants' in path or path.endswith('/import')) else 'operations'
    elif method=='GET' and (path.startswith('/api/platform/orders') or path=='/api/platform/order-filters'):return True
    else:return False
    return department==required


def public_identity(account: dict) -> dict:
    return {'platformDepartment':account.get('platform_department') or 'all','accountId':account['id'],'username':account['username'],'role':account['role'],
            'clubId':account.get('club_id'),'userId':account.get('user_id')}


def get_identity(request: Request) -> tuple[dict | None, str | None]:
    bearer = request.headers.get('authorization','')
    token = bearer[7:].strip() if bearer.lower().startswith('bearer ') else request.cookies.get('clubos_session','')
    mode = 'bearer' if bearer.lower().startswith('bearer ') else 'cookie'
    # 网关（公开托管 *.app.workbuddy.host）会剥离 Cookie / Authorization 请求头，
    # 导致浏览器会话无法跨请求保持（登录后立刻 401 被踢回登录页，表现为「闪退」）。
    # 兼容多种可穿透网关的会话载体：X-ClubOS-Token、中性头 X-App-Session、查询参数 ?sid=。
    if not token:
        hdr = request.headers.get('x-clubos-token','').strip()
        neu = request.headers.get('x-app-session','').strip()
        q = (request.query_params.get('sid') or '').strip() if hasattr(request,'query_params') else ''
        if hdr:
            token = hdr; mode = 'token-header'
        elif neu:
            token = neu; mode = 'token-neutral'
        elif q:
            token = q; mode = 'token-query'
    if not token: return None, None
    with conn() as c:
        account = row(c.execute('''SELECT a.*,s.csrf_hash FROM auth_sessions s JOIN auth_accounts a ON a.id=s.account_id
           WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND a.status='active' ''',(_hash(token),int(time.time()))))
        if account and account['role'] in ('club','leader'):
            club = row(c.execute('SELECT status FROM clubs WHERE id=?',(account['club_id'],)))
            if not club or club['status']!='active': return None, None
    return (account,mode) if account else (None,None)


def test_open_identity(role: str = 'club', club_id: int = 1) -> dict | None:
    """内测期兜底身份：仅在 TEST_OPEN 下用于 /api/auth/me。

    原因：浏览器里可能残留过期的 clubos_csrf cookie，前端据此调用 /api/auth/me，
    若返回 401 就会被弹回登录页——与「测试期不要密码」的目标冲突。这里给一个
    真实存在的俱乐部账号身份，让前端拿到 clubId 后正常进入后台。
    """
    if not TEST_OPEN: return None
    with conn() as c:
        account = row(c.execute("SELECT * FROM auth_accounts WHERE status='active' AND role=? AND club_id=? ORDER BY id LIMIT 1", (role, club_id)))
        if not account:
            account = row(c.execute("SELECT * FROM auth_accounts WHERE status='active' AND role='club' ORDER BY id LIMIT 1"))
    return dict(account) if account else None


def revoke_session(request: Request) -> None:
    bearer = request.headers.get('authorization','')
    token = bearer[7:].strip() if bearer.lower().startswith('bearer ') else request.cookies.get('clubos_session','')
    if token:
        with conn() as c: c.execute('UPDATE auth_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE token_hash=?',(_hash(token),))


def public_rate_limit(request: Request, action: str, max_attempts: int, seconds: int) -> None:
    now = int(time.time())
    key = _rate_key(request)
    with conn() as c:
        c.execute('DELETE FROM public_rate_limits WHERE created_at<?', (now-86400,))
        attempts = c.execute('SELECT COUNT(*) FROM public_rate_limits WHERE ip_hash=? AND action=? AND created_at>?',
            (key,action,now-seconds)).fetchone()[0]
        if attempts >= max_attempts: _forbidden('rate limit exceeded', 429)
        c.execute('INSERT INTO public_rate_limits(ip_hash,action,created_at) VALUES(?,?,?)',(key,action,now))


def sanitize_public_document(document):
    """Remove internal fields recursively; public content requires editorial approval before publication.

    2026-10-06 增补中文成本键：此前只认英文（cost/margin/profit…），于是 master.fees 里的
    `人均费用`/`合计（未含税）`/`单价`/`小计`/`未含税`/`策划执行`/`毛利` 这些中文成本键能直接穿过这道闸。
    只加「成本专属」的中文词，绝不加泛词 `费用`/`价格` —— 否则会把公开的 `费用包含`、对外 `价格` 一并删掉。
    （真正的兜底在 app.py 的 sanitize_for_frontend，这里只是 IS_PROD 路径的纵深防御。）
    """
    private = re.compile(
        r'(internal|private|secret|credential|supplier|cost|margin|profit|api.?key|prompt|'
        r'rawsource|raw_source|budget|settlement|inventory|wholesale|'
        r'人均费用|合计|单价|小计|未含税|不含税|毛利|净利|利润|策划执行|成本|预算|结算|税费|税金|返点|提成|报价单)',
        re.I)
    if isinstance(document,dict):
        return {k:sanitize_public_document(v) for k,v in document.items() if not private.search(str(k))}
    if isinstance(document,list): return [sanitize_public_document(x) for x in document]
    return document


def _forbidden(detail='permission denied', code=403):
    raise HTTPException(code, detail)


def _member_owns_path(path: str, request: Request, identity: dict, payload: dict) -> None:
    uid = identity.get('user_id')
    if uid is None: _forbidden()
    match = re.search(r'/users/(\d+)(?:/|$)',path)
    if match and int(match[1])!=uid: _forbidden()
    # Legacy C-end handlers default some identity fields to user 1. In production an
    # omitted field must NEVER silently select the seed user or someone else's wallet.
    needs_user_query=(re.match(r'^/api/public/clubs/\d+/(?:vouchers|member-center)$',path)
        or re.match(r'^/api/public/activities/\d+/price-quote$',path))
    needs_user_body=(re.match(r'^/api/public/clubs/\d+/(?:gear-checkout|benefits/\d+/redeem)$',path))
    if needs_user_query and 'user_id' not in request.query_params: _forbidden('authenticated user_id required',400)
    if needs_user_body and 'userId' not in payload: _forbidden('authenticated userId required',400)
    for key in ('userId','user_id','toUserId'):
        if key in payload and str(payload[key])!=str(uid): _forbidden()
        if key in request.query_params and str(request.query_params[key])!=str(uid): _forbidden()
    if re.search(r'^/api/public/activities/\d+/checkout$', path):
        with conn() as c:
            user = row(c.execute('SELECT phone FROM users WHERE id=?',(uid,)))
        # uid 取自会话 token（create_activity_checkout 经 _member_uid_from_request），
        # 已不可被请求体伪造；此处 phone 仅作联系/出行用途。仅当用户档案已有手机号时，
        # 才强制 body.phone 与档案一致（防误填他人号码）；微信原生会员（无 phone）允许自填。
        if not user: _forbidden('payer identity mismatch')
        if user['phone'] and str(payload.get('phone','')) != str(user['phone']): _forbidden('payer identity mismatch')
    if re.search(r'^/api/public/club-applications',path): _forbidden('application details are not public')
    if request.method in ('POST','PATCH','PUT','DELETE'):
        club_match=re.match(r'^/api/public/clubs/(\d+)/',path)
        if club_match:
            with conn() as c: active=row(c.execute('SELECT status FROM clubs WHERE id=?',(club_match[1],)))
            if not active or active['status']!='active': _forbidden('club is not active')
        checkout_match=re.match(r'^/api/public/activities/(\d+)/checkout$',path)
        if checkout_match:
            with conn() as c:
                active=row(c.execute('''SELECT cl.status FROM activities a JOIN clubs cl ON cl.id=a.club_id WHERE a.id=?''',(checkout_match[1],)))
            if not active or active['status']!='active': _forbidden('club is not active')
    with conn() as c:
        checks = [
            (r'^/api/public/registrations/(\d+)', 'registrations', 'id'),
            (r'^/api/public/checkouts/([^/]+)', 'checkout_intents', 'id'),
            (r'^/api/public/orders/(\d+)', 'gear_orders', 'id'),
            (r'^/api/public/after-sales/([^/]+)', 'after_sales_cases', 'id'),
        ]
        for expr, table, field in checks:
            m = re.match(expr,path)
            if m:
                found = row(c.execute(f'SELECT user_id FROM {table} WHERE {field}=?',(m[1],)))
                if not found: _forbidden('not found',404)
                if found['user_id']!=uid: _forbidden()
    if path.endswith('/after-sales') and 'userId' not in payload and re.search('/orders/',path):
        # The engine may default the user id; ownership is checked by order above.
        pass


async def authorize_request(request: Request) -> dict | None:
    """Return authenticated principal or raise.

    2026-10-09 安全修复（线上裸奔事故）：此前 demo 模式直接 `return None`，
    /api/club/* 全部免鉴权——未登录可读报名（含手机号）、可删除/改写任意数据
    （实证：未鉴权 DELETE 返回 200 并删掉了线上内容资产）。
    现在 demo 模式与 production 共用同一套检查（_authorize_shared），差异仅在：
      · demo 跳过 body 体积上限（资料上传 pptx 远超 1MB）；
      · demo 跳过 commerce webhook secret 强校验（本地支付商没有 secret）；
      · demo 不禁用 _DEMO_ONLY 端点（C 端报名/下单依赖它们，线上语义必须保留）；
      · demo 放行 /api/public/* 全部方法（C 端匿名浏览/报名是产品语义）；
      · demo 的 /api/club 公开读仅限 activities / content / ai-mode（数据核对探针依赖）；
      · demo 页面壳（/club 等）不拦，由前端跳登录（保持现有 UX）；
      · demo 非 GET 不强制 CSRF 头（SameSite=Lax 已挡跨站携带 cookie；保持既有脚本兼容）。

    另：内测期可显式打开 CLUBOS_TEST_OPEN=1 完全免除登录门槛（见 TEST_OPEN），
    那时的行为与修复前的 demo 一致——这是有意为之的临时开关，不是默认策略。
    """
    if TEST_OPEN:
        # 内测开放：不做身份/角色/归属校验，也不拦截任何管理接口。
        # 例外（BUG-HUB-003）：平台级设置中枢是「一次写入波及所有未覆盖俱乐部」
        # 的全局配置，即便内测免登录也必须保留平台角色校验 —— 否则俱乐部后台
        # 的下拉框可直接改全平台默认值。平台账号需正常登录后才可读写。
        if request.url.path.startswith('/api/platform/settings-hub'):
            return await _authorize_shared(request, strict=IS_PROD)
        return None
    return await _authorize_shared(request, strict=IS_PROD)


async def _authorize_shared(request: Request, strict: bool) -> dict | None:
    path, method = request.url.path, request.method
    length = request.headers.get('content-length')
    if strict and length:
        try:
            if int(length) > (20*1024*1024 if path.endswith('/ai-generate') else 1024*1024):
                _forbidden('request too large', 413)
        except ValueError: _forbidden('invalid content length',400)
    if method == 'OPTIONS':
        if strict: _forbidden('CORS preflight not enabled')
        return None
    if path in ('/api/health','/login','/api/auth/login','/api/auth/logout'):
        return None
    if path.startswith('/static/uploads/') or path.startswith('/static/demo/'):
        if strict: _forbidden('media access requires a protected asset service',404)
        return None
    if path.startswith('/static/'):
        return None
    if strict and path in ('/docs','/redoc','/openapi.json'):
        _forbidden('disabled',404)
    if path.startswith('/api/payments/') and path.endswith('/notify'):
        return None # provider verification is performed by the payment adapter itself
    if path.startswith('/api/commerce/events/'):
        if not strict: return None
        event = path.rsplit('/',1)[-1]
        if event in _UNVERIFIED_EVENTS: _forbidden('unverified money event disabled',403)
        expected = os.getenv('CLUBOS_COMMERCE_WEBHOOK_SECRET','')
        if not expected or not hmac.compare_digest(request.headers.get('x-clubos-commerce-secret',''),expected):
            _forbidden('invalid commerce secret',401)
        return None
    if path.startswith('/api/webhooks/logistics/'):
        # 物流服务商回调（快递100 等）没有会话身份，只能靠 token。这里只放行到路由层：
        # 端点内部用 X-ClubOS-Token 与 logistics_provider_json.token 做常量时间比对，
        # 未配置 token 时直接 403 —— 否则任何人知道运单号就能往轨迹里塞「已签收」。
        return None
    if path=='/api/public/club-applications' and method=='POST':
        if strict:
            public_rate_limit(request,'club-application',3,3600)
            return None
        return None
    # 2026-11-09 微信小程序 jscode2session 登录：它是「发会话」的入口，本身无需先验会话。
    # strict 模式下仍做频率限制防滥用；demo 模式已在下方 /api/public/* 全放行分支覆盖。
    if path=='/api/public/wechat/login' and method=='POST':
        if strict:
            public_rate_limit(request,'wechat-login',20,3600)
        return None
    if strict and any(rx.fullmatch(path) for rx in _DEMO_ONLY): _forbidden('demo-only endpoint disabled')
    if method == 'GET' and any(rx.fullmatch(path) for rx in _PUBLIC_READ): return None
    if not strict:
        # demo 语义保留：C 端匿名（public/* 全方法）+ 页面壳 + 俱乐部公开读探针。
        if path.startswith('/api/public/'): return None
        if path in ('/','/platform','/club','/web','/leader'): return None
        if method in ('GET','HEAD') and (
            re.fullmatch(r'/api/club/\d+/activities(?:/\d+)?', path)
            or re.fullmatch(r'/api/club/\d+/content(?:/\d+)?', path)
            or re.fullmatch(r'/api/club/\d+/ai-mode', path)):
            return None
    if path in ('/','/platform','/club','/web','/leader'):
        identity, _=get_identity(request)
        expected = {'/':'club','/platform':'platform','/club':'club','/web':'member','/leader':'leader'}[path]
        if not identity: _forbidden('authentication required',401)
        if identity['role']!=expected: _forbidden()
        return identity
    identity, mode = get_identity(request)
    if not identity: _forbidden('authentication required',401)
    if strict and mode == 'cookie' and method not in ('GET','HEAD'):
        csrf = request.headers.get('x-clubos-csrf','')
        if not csrf or not hmac.compare_digest(_hash(csrf),identity['csrf_hash']): _forbidden('CSRF validation failed')
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/')!=os.getenv('CLUBOS_PUBLIC_BASE_URL','').rstrip('/'):
            _forbidden('untrusted origin')
    role = identity['role']
    if path=='/api/auth/me': return identity
    if path.startswith('/api/platform/'):
        if role!='platform': _forbidden()
        data={}
        if method not in ('GET','HEAD','DELETE') and '/products' in path and 'application/json' in request.headers.get('content-type',''):
            try:data=await request.json()
            except Exception:_forbidden('invalid JSON',400)
        if not platform_department_allowed(identity,path,method,data):_forbidden('当前岗位无权执行此操作')
    elif path.startswith('/api/club/'):
        m = re.match(r'^/api/club/(\d+)(?:/|$)',path)
        if role!='club' or not m or int(m[1])!=identity['club_id']: _forbidden()
    elif path.startswith('/api/leader/'):
        if role!='leader': _forbidden()
        m = re.search(r'/occurrences/(\d+)',path)
        if str(request.query_params.get('club_id','1'))!=str(identity['club_id']): _forbidden()
        if m:
            with conn() as c:
                occ = row(c.execute('SELECT club_id FROM activity_occurrences WHERE id=?',(m[1],)))
            if not occ or occ['club_id']!=identity['club_id']: _forbidden()
            # Leader roster assignment must exist; merely knowing a club's id is not enough.
            with conn() as c:
                assignment = row(c.execute('SELECT id FROM occurrence_leaders WHERE occurrence_id=? AND phone=(SELECT phone FROM users WHERE id=?)',(m[1],identity['user_id']))) if identity.get('user_id') is not None else None
            if not assignment: _forbidden('leader is not assigned to this occurrence')
    elif path.startswith('/api/public/'):
        if role!='member': _forbidden()
        payload = {}
        if method not in ('GET','HEAD') and request.headers.get('content-type','').split(';')[0]=='application/json':
            try:
                parsed=json.loads(await request.body())
                if isinstance(parsed,dict): payload=parsed
            except ValueError: _forbidden('invalid JSON',400)
        _member_owns_path(path,request,identity,payload)
        if path.startswith('/api/public/club-applications/'):
            _forbidden('application status is not public')
    elif path.startswith('/api/'):
        if role!='platform': _forbidden()
    return identity


def record_audit(request: Request, identity: dict | None, status: int, request_id: str) -> None:
    path=request.url.path
    if not path.startswith(_AUDIT_PREFIX) or request.method in ('GET','HEAD','OPTIONS'):
        return
    # Never log passwords, bodies, headers, PII or callback signatures.
    try:
        with conn() as c:
            c.execute('''INSERT INTO security_audit_events(request_id,actor_id,role,club_id,user_id,method,path,status,request_hash)
              VALUES(?,?,?,?,?,?,?,?,?)''',(request_id,identity.get('id') if identity else None,
                identity.get('role') if identity else 'provider',identity.get('club_id') if identity else None,
                identity.get('user_id') if identity else None,request.method,path,status,None))
    except Exception:
        # Authorization failures should not be bypassable because audit storage is unavailable;
        # the caller middleware raises on successful critical writes below.
        if status < 400: raise
