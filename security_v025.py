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


def public_identity(account: dict) -> dict:
    return {'accountId':account['id'],'username':account['username'],'role':account['role'],
            'clubId':account.get('club_id'),'userId':account.get('user_id')}


def get_identity(request: Request) -> tuple[dict | None, str | None]:
    bearer = request.headers.get('authorization','')
    token = bearer[7:].strip() if bearer.lower().startswith('bearer ') else request.cookies.get('clubos_session','')
    mode = 'bearer' if bearer.lower().startswith('bearer ') else 'cookie'
    if not token: return None, None
    with conn() as c:
        account = row(c.execute('''SELECT a.*,s.csrf_hash FROM auth_sessions s JOIN auth_accounts a ON a.id=s.account_id
           WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND a.status='active' ''',(_hash(token),int(time.time()))))
        if account and account['role'] in ('club','leader'):
            club = row(c.execute('SELECT status FROM clubs WHERE id=?',(account['club_id'],)))
            if not club or club['status']!='active': return None, None
    return (account,mode) if account else (None,None)


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
    for key in ('userId','user_id'):
        if key in payload and str(payload[key])!=str(uid): _forbidden()
        if key in request.query_params and str(request.query_params[key])!=str(uid): _forbidden()
    if re.search(r'^/api/public/activities/\d+/checkout$', path):
        with conn() as c:
            user = row(c.execute('SELECT phone FROM users WHERE id=?',(uid,)))
        if not user or not user['phone'] or str(payload.get('phone','')) != str(user['phone']): _forbidden('payer identity mismatch')
    if re.search(r'^/api/public/club-applications',path): _forbidden('application details are not public')
    if request.method in ('POST','PATCH','PUT','DELETE'):
        club_match=re.match(r'^/api/public/clubs/(\d+)/(?:gear-checkout|benefits)',path)
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
    """
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
    if path=='/api/public/club-applications' and method=='POST':
        if strict:
            public_rate_limit(request,'club-application',3,3600)
            return None
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
