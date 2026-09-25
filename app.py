from __future__ import annotations
import uuid, os, io, csv, zipfile, shutil
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, quote, urlsplit
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body, Query, Header, Request
from fastapi.responses import FileResponse, StreamingResponse, PlainTextResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from db import init_db, conn, row, rows, jdump, jload, setting
from document_parser import save_uploads, parse_sources
from ai_engine import generate_activity, generate_channel
from ai_billing import ensure_credits, charge_credits
from ai_gateway import (gateway_status, AIGatewayError, platform_provider_config,
                        update_platform_provider_config, test_provider_connection)
from clubos_domain.club_analytics import ClubBusinessIntelligence
from clubos_domain import ClubOSPointsEngine, BookingEngine, CheckoutEngine, ActivityPointsPolicyService, ActivityRefundPolicyService, MembershipEngine, BenefitEngine, CommerceRefundEngine, PaymentLifecycleEngine, RefundLifecycleEngine, ParticipantService, ActivityExecutionService, CommissionSettlementEngine, AfterSalesEngine, ProcurementEngine, WarehouseEngine, MerchandiseFinanceEngine, CommerceAnalyticsEngine, AICreditEngine
from commerce_adapter import commerce_status, commerce_provider, MedusaClient
from payment_providers import PaymentAccountService, provider_for_account, merchant_order_no, provider_refund_no

ROOT=Path(__file__).resolve().parent
STATIC=ROOT/'static'
UPLOAD=STATIC/'uploads'
from security_v025 import (IS_PROD, sanitize_public_document, validate_production_config, install_tables, authorize_request,
    issue_session, get_identity, revoke_session, record_audit)
validate_production_config()
app=FastAPI(title='ClubOS NEW',version='0.25.0-production-hardening',
            docs_url=None if IS_PROD else '/docs',redoc_url=None if IS_PROD else '/redoc',
            openapi_url=None if IS_PROD else '/openapi.json')
init_db()
install_tables()
if IS_PROD:
    with conn() as _c:
        if _c.execute("SELECT COUNT(*) FROM payment_accounts WHERE enabled=1 AND provider='local'").fetchone()[0]:
            raise RuntimeError('Production refuses local/mock payment accounts; provision verified providers first')

@app.middleware('http')
async def security_boundary(request:Request,call_next):
    request_id=uuid.uuid4().hex
    identity=None
    try:
        identity=await authorize_request(request)
        response=await call_next(request)
    except HTTPException as e:
        if request.url.path in ('/','/platform','/club','/web','/leader') and e.status_code==401:
            response=RedirectResponse('/login',status_code=303)
        else:
            response=JSONResponse({'detail':e.detail},status_code=e.status_code)
    if IS_PROD:
        try:
            record_audit(request,identity,response.status_code,request_id)
        except Exception:
            # If the audit sink fails for a successful write, do not claim success to the caller.
            response=JSONResponse({'detail':'audit persistence unavailable; the operation may already have committed; reconcile with X-Request-ID before retry'},status_code=503)
        response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api/') else 'private, no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Content-Security-Policy']="frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
        response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
    response.headers['X-Request-ID']=request_id
    return response

@app.get('/login')
def login_page(): return FileResponse(STATIC/'login.html')

@app.post('/api/auth/login')
def login_account(request:Request,payload:dict=Body(...)):
    token,csrf,identity=issue_session(request,str(payload.get('username') or ''),str(payload.get('password') or ''))
    response=JSONResponse({'ok':True,'identity':identity})
    response.set_cookie('clubos_session',token,httponly=True,secure=IS_PROD,samesite='strict',max_age=3600,path='/')
    response.set_cookie('clubos_csrf',csrf,httponly=False,secure=IS_PROD,samesite='strict',max_age=3600,path='/')
    return response

@app.get('/api/auth/me')
def auth_me(request:Request):
    from security_v025 import public_identity
    identity,_=get_identity(request)
    if not identity: raise HTTPException(401,'authentication required')
    profile=public_identity(identity)
    if identity['role'] in ('member','leader') and identity.get('user_id'):
        with conn() as c:
            own=row(c.execute('SELECT name,phone FROM users WHERE id=?',(identity['user_id'],)))
        if own: profile.update({'name':own['name'],'phone':own['phone']})
    return profile

@app.post('/api/auth/logout')
def auth_logout(request:Request):
    from security_v025 import _hash
    identity,mode=get_identity(request)
    if mode=='cookie' and identity:
        csrf=request.headers.get('x-clubos-csrf','')
        if not csrf or not __import__('hmac').compare_digest(_hash(csrf),identity['csrf_hash']):
            raise HTTPException(403,'CSRF validation failed')
    revoke_session(request)
    response=JSONResponse({'ok':True})
    response.delete_cookie('clubos_session',path='/')
    response.delete_cookie('clubos_csrf',path='/')
    return response

app.mount('/static',StaticFiles(directory=STATIC),name='static')


def club_or_404(club_id:int):
    with conn() as c: x=row(c.execute('SELECT * FROM clubs WHERE id=?',(club_id,)))
    if not x: raise HTTPException(404,'俱乐部不存在')
    return x


def user_or_create(c,name:str,phone:str):
    u=row(c.execute('SELECT * FROM users WHERE phone=?',(phone,))) if phone else None
    if u:return u['id']
    c.execute('INSERT INTO users(name,phone) VALUES(?,?)',(name or '访客',phone)); return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])


def wallet_snapshot(c,user_id:int,club_id:int):
    m=row(c.execute('SELECT club_points_balance,level,lifetime_activity_spend,activity_count FROM club_members WHERE user_id=? AND club_id=?',(user_id,club_id))) or {'club_points_balance':0,'level':'非会员','lifetime_activity_spend':0,'activity_count':0}
    g=row(c.execute('SELECT balance FROM gear_point_accounts WHERE user_id=?',(user_id,))) or {'balance':0}
    return {'clubPoints':int(m['club_points_balance'] or 0),'gearPoints':int(g['balance'] or 0),'memberLevel':m['level'],'lifetimeActivitySpend':float(m.get('lifetime_activity_spend') or 0),'activityCount':int(m.get('activity_count') or 0)}

points_engine=ClubOSPointsEngine(setting)
activity_points_policy=ActivityPointsPolicyService(setting)
activity_refund_policy=ActivityRefundPolicyService()
membership_engine=MembershipEngine()
benefit_engine=BenefitEngine(wallet_snapshot)
participant_service=ParticipantService()
execution_service=ActivityExecutionService()
commission_engine=CommissionSettlementEngine(setting)
warehouse_engine=WarehouseEngine()
finance_engine=MerchandiseFinanceEngine()
analytics_engine=CommerceAnalyticsEngine(finance_engine,warehouse_engine)
ai_credit_engine=AICreditEngine()
club_bi=ClubBusinessIntelligence()
inventory_engine=ProcurementEngine(warehouse_engine,finance_engine)
booking_engine=BookingEngine(points_engine,wallet_snapshot,activity_points_policy,membership_engine,benefit_engine,participant_service)
checkout_engine=CheckoutEngine(points_engine,wallet_snapshot,setting,activity_points_policy,membership_engine,benefit_engine,participant_service,inventory_engine,ai_credit_engine)
commerce_refund_engine=CommerceRefundEngine(points_engine,benefit_engine,commission_engine,inventory_engine)
payment_lifecycle=PaymentLifecycleEngine()
payment_accounts=PaymentAccountService()
refund_lifecycle=RefundLifecycleEngine(points_engine,benefit_engine,membership_engine,commerce_refund_engine,activity_refund_policy,participant_service)
after_sales_engine=AfterSalesEngine(refund_lifecycle,inventory_engine)

# pages
@app.get('/')
def root(): return FileResponse(STATIC/'club'/'index.html')
@app.get('/club')
def club(): return FileResponse(STATIC/'club'/'index.html')
@app.get('/platform')
def platform(): return FileResponse(STATIC/'platform'/'index.html')
@app.get('/web')
def web(): return FileResponse(STATIC/'web'/'index.html')
@app.get('/leader')
def leader(): return FileResponse(STATIC/'leader'/'index.html')
@app.get('/api/health')
def health(): return {'ok':True,'product':'ClubOS NEW','version':'0.25-production-hardening'}

@app.get('/api/commerce/status')
def get_commerce_status():
    st=commerce_status()
    return st.__dict__


def _payment_public_base_url() -> str:
    return os.getenv('CLUBOS_PUBLIC_BASE_URL','http://localhost:8000').rstrip('/')


def _commerce_order_after_verified_payment(c, *, checkout_id:str, provider_payment_id:str|None, provider:str) -> str|None:
    """Return the commerce order id that ClubOS Domain should persist.

    Activity business is ClubOS-only and never creates a Medusa cart/order. For Gear
    in Medusa mode, an external payment (WeChat/Alipay/local test provider) is verified
    by ClubOS first, then the already-priced Medusa cart is completed into a real
    Medusa Order. Provider transaction IDs remain payment records; they are not used
    as Gear commerce_order_id.
    """
    intent=row(c.execute('SELECT * FROM checkout_intents WHERE id=?',(checkout_id,)))
    if not intent: raise LookupError('结算单不存在')
    if intent.get('commerce_order_id'):
        return str(intent['commerce_order_id'])
    if intent.get('kind')!='gear' or commerce_provider()!='medusa':
        return provider_payment_id
    cart_id=str(intent.get('commerce_cart_id') or '')
    if not cart_id:
        raise ValueError('Gear 结算单尚未创建 Medusa Cart，不能生成真实商城订单')
    completed=MedusaClient().complete_paid_cart(cart_id,provider_payment_id=provider_payment_id,provider=provider)
    order_id=str((completed.get('order') or {}).get('id') or '')
    if not order_id:
        raise ValueError('Medusa 未返回真实 Order ID')
    return order_id


def _payment_account_public(account:dict|None):
    if not account:return None
    return {k:account.get(k) for k in (('id','scope_type','scope_id','provider','channel','merchant_id','app_id','enabled','created_at','updated_at') if IS_PROD else ('id','scope_type','scope_id','provider','channel','merchant_id','app_id','credential_ref','enabled','metadata_json','created_at','updated_at'))}

@app.get('/api/club/{club_id}/payment-account')
def club_payment_account(club_id:int):
    club_or_404(club_id)
    with conn() as c:return _payment_account_public(payment_accounts.get_scope_account(c,scope_type='club',scope_id=club_id))

@app.patch('/api/club/{club_id}/payment-account')
def update_club_payment_account(club_id:int,payload:dict=Body(...)):
    if IS_PROD and str(payload.get('provider') or 'local')=='local':raise HTTPException(403,'local payment provider disabled')
    club_or_404(club_id)
    with conn() as c:
        try:
            account=payment_accounts.upsert(c,scope_type='club',scope_id=club_id,provider=str(payload.get('provider') or 'local'),channel=str(payload.get('channel') or 'mock'),merchant_id=payload.get('merchantId'),app_id=payload.get('appId'),credential_ref=str(payload.get('credentialRef') or ''),enabled=bool(payload.get('enabled',True)),metadata=payload.get('metadata') or {})
            return _payment_account_public(account)
        except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/platform/payment-account')
def platform_payment_account():
    with conn() as c:return _payment_account_public(payment_accounts.get_scope_account(c,scope_type='platform'))

@app.patch('/api/platform/payment-account')
def update_platform_payment_account(payload:dict=Body(...)):
    if IS_PROD and str(payload.get('provider') or 'local')=='local':raise HTTPException(403,'local payment provider disabled')
    with conn() as c:
        try:
            account=payment_accounts.upsert(c,scope_type='platform',scope_id=None,provider=str(payload.get('provider') or 'local'),channel=str(payload.get('channel') or 'mock'),merchant_id=payload.get('merchantId'),app_id=payload.get('appId'),credential_ref=str(payload.get('credentialRef') or ''),enabled=bool(payload.get('enabled',True)),metadata=payload.get('metadata') or {})
            return _payment_account_public(account)
        except ValueError as e: raise HTTPException(400,str(e))

# CLUB ADMIN
@app.get('/api/club/{club_id}/dashboard')
def club_dashboard(club_id:int):
    club=club_or_404(club_id)
    with conn() as c:
        act=c.execute('SELECT COUNT(*) FROM activities WHERE club_id=?',(club_id,)).fetchone()[0]
        reg=c.execute("SELECT COUNT(*) FROM registration_participants p JOIN registrations r ON r.id=p.registration_id WHERE r.club_id=? AND r.status='paid' AND p.status='active'",(club_id,)).fetchone()[0]
        reg_orders=c.execute("SELECT COUNT(*) FROM registrations WHERE club_id=? AND status='paid'",(club_id,)).fetchone()[0]
        mem=c.execute('SELECT COUNT(*) FROM club_members WHERE club_id=?',(club_id,)).fetchone()[0]
        gmv=c.execute('SELECT COALESCE(SUM(total),0) FROM gear_orders WHERE source_club_id=?',(club_id,)).fetchone()[0]
        comm=c.execute('SELECT COALESCE(SUM(amount),0) FROM commission_ledger WHERE club_id=?',(club_id,)).fetchone()[0]
        credits=row(c.execute('SELECT * FROM ai_credit_accounts WHERE club_id=?',(club_id,)))
    return {'club':club,'activityCount':act,'registrationCount':reg,'registrationOrderCount':reg_orders,'memberCount':mem,'gearGMV':gmv,'commission':comm,'credits':credits}

@app.get('/api/club/{club_id}/analytics')
def club_business_analytics(club_id:int,window_days:int=Query(30,alias='windowDays')):
    # Read only and strictly club-scoped. Production identity/RBAC is a separate gate.
    club_or_404(club_id)
    with conn() as c:
        try:return club_bi.dashboard(c,club_id=club_id,window_days=window_days)
        except ValueError as e:raise HTTPException(400,str(e))

@app.get('/api/club/{club_id}/activities')
def club_activities(club_id:int):
    with conn() as c:return rows(c.execute('SELECT id,title,status,event_date,location,price,capacity,created_at,cover FROM activities WHERE club_id=? ORDER BY id DESC',(club_id,)))

@app.post('/api/club/{club_id}/activities/ai-generate')
async def ai_generate(club_id:int,prompt:str=Form(''),files:list[UploadFile]=File(default=[])):
    club_or_404(club_id); ensure_credits(club_id,'detail')
    batch=UPLOAD/str(club_id)/uuid.uuid4().hex
    try:
        saved=save_uploads(files,batch,max_total_bytes=20*1024*1024 if IS_PROD else None)
        if IS_PROD:
            for item in saved:
                if item.suffix.lower() in ('.pptx','.docx'):
                    with zipfile.ZipFile(item) as archive:
                        members=archive.infolist()
                        if len(members)>1000 or sum(m.file_size for m in members)>100*1024*1024:
                            raise ValueError('office archive exceeds safe extraction budget')
                if item.suffix.lower()=='.pdf':
                    import fitz
                    with fitz.open(str(item)) as pdf:
                        if len(pdf)>120:raise ValueError('PDF exceeds 120-page processing limit')
        source=parse_sources(saved,prompt,STATIC)
    except (ValueError,zipfile.BadZipFile) as e:
        shutil.rmtree(batch,ignore_errors=True)
        raise HTTPException(413,str(e))
    try: result,usage=await generate_activity(club_id,source)
    except AIGatewayError as e: raise HTTPException(502,str(e))
    master=result['activity_master']; detail=result['detail']
    # Only genuine conflicts can block publish; generation itself still returns the result.
    charge_credits(club_id,'detail',usage.usage_id)
    title=master.get('title') or 'AI生成活动'
    with conn() as c:
        c.execute('INSERT INTO activities(club_id,title,status,event_date,location,price,capacity,activity_master_json,detail_json) VALUES(?,?,?,?,?,?,?,?,?)',(
            club_id,title,'draft',master.get('date',''),master.get('location',''),float(master.get('price') or 0),int(master.get('capacity') or 0),jdump(master),jdump(detail)))
        aid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        # If the source has one clear date/price, create an initial sellable occurrence automatically.
        if master.get('date') and master.get('date')!='待发布':
            c.execute('INSERT INTO activity_occurrences(activity_id,club_id,start_at,price,capacity,status,label) VALUES(?,?,?,?,?,?,?)',(
                aid,club_id,str(master.get('date')),float(master.get('price') or 0),int(master.get('capacity') or 0),'open','首发团期'))
    return {'activityId':aid,'activityMaster':master,'detail':detail,'source':{'files':source['files'],'imageCount':len(source['images']),'media':source['media_manifest']}}

@app.get('/api/club/{club_id}/activities/{activity_id}')
def get_activity(club_id:int,activity_id:int):
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        occ=rows(c.execute('SELECT * FROM activity_occurrences WHERE activity_id=? AND club_id=? ORDER BY start_at',(activity_id,club_id)))
    if not a: raise HTTPException(404,'活动不存在')
    a['activityMaster']=jload(a.pop('activity_master_json'),{});a['detail']=jload(a.pop('detail_json'),{});a['occurrences']=occ
    a['pointsPolicy']=activity_points_policy.from_activity(a).as_dict()
    a['refundPolicy']=activity_refund_policy.from_activity(a).as_dict()
    a['participantPolicy']=participant_service.from_activity(a).as_dict()
    return a

@app.get('/api/club/{club_id}/activities/{activity_id}/points-policy')
def get_activity_points_policy(club_id:int,activity_id:int):
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
    if not a: raise HTTPException(404,'活动不存在')
    return activity_points_policy.from_activity(a).as_dict()

@app.patch('/api/club/{club_id}/activities/{activity_id}/points-policy')
def update_activity_points_policy(club_id:int,activity_id:int,payload:dict=Body(...)):
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        current=activity_points_policy.from_activity(a)
        policy=activity_points_policy.normalize_update(payload,current)
        activity_points_policy.persist(c,activity_id,club_id,policy)
    return policy.as_dict()

@app.get('/api/club/{club_id}/activities/{activity_id}/refund-policy')
def get_activity_refund_policy(club_id:int,activity_id:int):
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
    if not a: raise HTTPException(404,'活动不存在')
    return activity_refund_policy.from_activity(a).as_dict()

@app.patch('/api/club/{club_id}/activities/{activity_id}/refund-policy')
def update_activity_refund_policy(club_id:int,activity_id:int,payload:dict=Body(...)):
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        current=activity_refund_policy.from_activity(a)
        try: policy=activity_refund_policy.normalize_update(payload,current)
        except ValueError as e: raise HTTPException(400,str(e))
        activity_refund_policy.persist(c,activity_id,club_id,policy)
    return policy.as_dict()


@app.get('/api/club/{club_id}/activities/{activity_id}/participant-policy')
def get_activity_participant_policy(club_id:int,activity_id:int):
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
    if not a: raise HTTPException(404,'活动不存在')
    return participant_service.from_activity(a).as_dict()

@app.patch('/api/club/{club_id}/activities/{activity_id}/participant-policy')
def update_activity_participant_policy(club_id:int,activity_id:int,payload:dict=Body(...)):
    with conn() as c:
        try:return participant_service.persist(c,activity_id=activity_id,club_id=club_id,payload=payload).as_dict()
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))

@app.post('/api/club/{club_id}/activities/{activity_id}/occurrences')
def add_occurrence(club_id:int,activity_id:int,payload:dict=Body(...)):
    with conn() as c:
        a=row(c.execute('SELECT id FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        c.execute('INSERT INTO activity_occurrences(activity_id,club_id,start_at,end_at,price,capacity,status,label) VALUES(?,?,?,?,?,?,?,?)',(
            activity_id,club_id,payload['startAt'],payload.get('endAt'),float(payload.get('price',0)),int(payload.get('capacity',0)),payload.get('status','open'),payload.get('label','团期')))
        oid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
    return {'id':oid}

@app.post('/api/club/{club_id}/activities/{activity_id}/publish')
def publish_activity(club_id:int,activity_id:int):
    with conn() as c:
        a=row(c.execute('SELECT activity_master_json FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        master=jload(a['activity_master_json'],{})
        if master.get('blocking_conflicts'): raise HTTPException(409,'存在必须解决的事实冲突，暂不能发布')
        c.execute('UPDATE activities SET status="published",updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?',(activity_id,club_id))
    return {'ok':True}

@app.post('/api/club/{club_id}/activities/{activity_id}/cover')
async def upload_activity_cover(club_id:int, activity_id:int, file:UploadFile=File(...)):
    # Club-scoped; ownership enforced by matching club_id on the row.
    club_or_404(club_id)
    with conn() as c:
        a=row(c.execute('SELECT id,club_id FROM activities WHERE id=?',(activity_id,)))
    if not a or a['club_id']!=club_id: raise HTTPException(404,'活动不存在')
    ext=Path(file.filename or '').suffix.lower()
    if ext not in _PUBLIC_IMAGE_EXT: raise HTTPException(400,'仅支持图片文件：png/jpg/jpeg/webp/gif')
    data=await file.read()
    if len(data) > 10*1024*1024: raise HTTPException(413,'图片过大（上限 10MB）')
    batch=UPLOAD/str(club_id)/uuid.uuid4().hex
    batch.mkdir(parents=True, exist_ok=True)
    dest=batch/f'cover{ext}'
    dest.write_bytes(data)
    url=f'/static/uploads/{club_id}/{batch.name}/cover{ext}'
    with conn() as c:
        c.execute('UPDATE activities SET cover=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?',(url,activity_id,club_id))
    return {'cover':url}

@app.get('/api/club/{club_id}/activities/{activity_id}/cover')
def club_activity_cover(club_id:int, activity_id:int):
    # Club-scoped, ownership-enforced; works for drafts and published alike.
    club_or_404(club_id)
    with conn() as c:
        a=row(c.execute('SELECT club_id,cover FROM activities WHERE id=?',(activity_id,)))
    if not a or a['club_id']!=club_id or not a['cover']: raise HTTPException(404,'not found')
    fp=_safe_media_path(str(a['cover']))
    if not fp or not fp.is_file(): raise HTTPException(404,'not found')
    return FileResponse(fp)

@app.post('/api/club/{club_id}/activities/{activity_id}/channel/{channel}')
async def channel_generate(club_id:int,activity_id:int,channel:str):
    if channel not in {'wechat','xhs','poster','recap'}: raise HTTPException(400,'unsupported channel')
    ensure_credits(club_id,channel)
    with conn() as c:a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
    if not a: raise HTTPException(404,'活动不存在')
    master=jload(a['activity_master_json'],{});detail=jload(a['detail_json'],{})
    try: content,usage=await generate_channel(club_id,master,detail,channel)
    except AIGatewayError as e: raise HTTPException(502,str(e))
    charge_credits(club_id,channel,usage.usage_id)
    with conn() as c:c.execute('INSERT INTO content_assets(club_id,activity_id,channel,title,body_json) VALUES(?,?,?,?,?)',(
        club_id,activity_id,channel,content.get('title') or (content.get('titleOptions') or [''])[0],jdump(content)))
    return content

@app.get('/api/club/{club_id}/content')
def content_list(club_id:int):
    with conn() as c:return rows(c.execute('SELECT id,activity_id,channel,title,created_at FROM content_assets WHERE club_id=? ORDER BY id DESC',(club_id,)))

@app.get('/api/club/{club_id}/registrations')
def registrations(club_id:int):
    with conn() as c:return rows(c.execute('''SELECT r.*,a.title activity_title,u.name,u.phone,o.label occurrence_label,o.start_at,
        (SELECT COUNT(*) FROM registration_participants p WHERE p.registration_id=r.id AND p.status='active') active_participants,
        (SELECT COUNT(*) FROM registration_participants p WHERE p.registration_id=r.id AND p.status='active' AND p.form_status='complete') complete_participants,
        (SELECT COUNT(*) FROM registration_participants p WHERE p.registration_id=r.id AND p.status='active' AND p.insurance_status IN ('pending','submitted','failed')) insurance_pending
        FROM registrations r JOIN activities a ON a.id=r.activity_id JOIN users u ON u.id=r.user_id
        LEFT JOIN activity_occurrences o ON o.id=r.occurrence_id WHERE r.club_id=? ORDER BY r.id DESC''',(club_id,)))


@app.get('/api/club/{club_id}/registrations/{registration_id}/participants')
def club_registration_participants(club_id:int,registration_id:int):
    with conn() as c:
        reg=row(c.execute('SELECT * FROM registrations WHERE id=? AND club_id=?',(registration_id,club_id)))
        if not reg: raise HTTPException(404,'报名记录不存在')
        participants=participant_service.list_for_registration(c,registration_id)
        changes=rows(c.execute('SELECT * FROM participant_change_logs WHERE registration_id=? ORDER BY id DESC',(registration_id,)))
    return {'registration':reg,'participants':participants,'changes':changes}

@app.get('/api/club/{club_id}/occurrences/{occurrence_id}/participants')
def club_occurrence_participants(club_id:int,occurrence_id:int):
    with conn() as c:
        occ=row(c.execute('SELECT * FROM activity_occurrences WHERE id=? AND club_id=?',(occurrence_id,club_id)))
        if not occ: raise HTTPException(404,'团期不存在')
        return participant_service.list_for_occurrence(c,club_id=club_id,occurrence_id=occurrence_id)

@app.patch('/api/club/{club_id}/participants/{participant_id}/insurance')
def club_update_participant_insurance(club_id:int,participant_id:int,payload:dict=Body(...)):
    with conn() as c:
        try:return participant_service.update_insurance(c,participant_id=participant_id,club_id=club_id,payload=payload)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

# v0.13 ACTIVITY EXECUTION CENTER
@app.get('/api/club/{club_id}/execution/occurrences')
def execution_occurrences(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        return execution_service.list_occurrences(c,club_id=club_id)

@app.get('/api/club/{club_id}/occurrences/{occurrence_id}/execution')
def occurrence_execution(club_id:int,occurrence_id:int):
    with conn() as c:
        try:return execution_service.dashboard(c,club_id=club_id,occurrence_id=occurrence_id)
        except LookupError as e: raise HTTPException(404,str(e))

@app.patch('/api/club/{club_id}/occurrences/{occurrence_id}/execution/settings')
def update_execution_settings(club_id:int,occurrence_id:int,payload:dict=Body(...)):
    with conn() as c:
        try:return execution_service.update_settings(c,club_id=club_id,occurrence_id=occurrence_id,payload=payload)
        except LookupError as e: raise HTTPException(404,str(e))

@app.post('/api/club/{club_id}/occurrences/{occurrence_id}/execution/status')
def update_execution_status(club_id:int,occurrence_id:int,payload:dict=Body(...)):
    with conn() as c:
        try:return execution_service.set_status(c,club_id=club_id,occurrence_id=occurrence_id,status=str(payload.get('status') or ''),actor=f'club:{club_id}',note=str(payload.get('note') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/club/{club_id}/occurrences/{occurrence_id}/leaders')
def add_occurrence_leader(club_id:int,occurrence_id:int,payload:dict=Body(...)):
    with conn() as c:
        try:return execution_service.add_leader(c,club_id=club_id,occurrence_id=occurrence_id,payload=payload)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))

@app.post('/api/club/{club_id}/occurrences/{occurrence_id}/groups')
def add_execution_group(club_id:int,occurrence_id:int,payload:dict=Body(...)):
    with conn() as c:
        try:return execution_service.add_group(c,club_id=club_id,occurrence_id=occurrence_id,payload=payload)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/club/{club_id}/execution-groups/{group_id}/assign')
def assign_execution_group(club_id:int,group_id:int,payload:dict=Body(...)):
    with conn() as c:
        try:return execution_service.assign_group(c,club_id=club_id,group_id=group_id,participant_id=int(payload.get('participantId')))
        except LookupError as e: raise HTTPException(404,str(e))
        except (ValueError,TypeError) as e: raise HTTPException(409,str(e))

@app.post('/api/club/{club_id}/occurrences/{occurrence_id}/notices')
def create_execution_notice(club_id:int,occurrence_id:int,payload:dict=Body(...)):
    with conn() as c:
        try:return execution_service.create_notice(c,club_id=club_id,occurrence_id=occurrence_id,payload=payload)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))

@app.post('/api/club/{club_id}/occurrences/{occurrence_id}/notices/{notice_id}/send')
def send_execution_notice(club_id:int,occurrence_id:int,notice_id:int):
    with conn() as c:
        try:return execution_service.send_notice(c,club_id=club_id,occurrence_id=occurrence_id,notice_id=notice_id,actor=f'club:{club_id}')
        except LookupError as e: raise HTTPException(404,str(e))

@app.patch('/api/club/{club_id}/occurrences/{occurrence_id}/participants/{participant_id}/checkin')
def club_participant_checkin(club_id:int,occurrence_id:int,participant_id:int,payload:dict=Body(...)):
    with conn() as c:
        try:return execution_service.checkin(c,club_id=club_id,occurrence_id=occurrence_id,participant_id=participant_id,status=str(payload.get('status') or 'checked_in'),actor=f'club:{club_id}',note=str(payload.get('note') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/club/{club_id}/occurrences/{occurrence_id}/insurance/batch-submit')
def club_insurance_batch_submit(club_id:int,occurrence_id:int,payload:dict=Body(default={})):
    with conn() as c:
        try:return execution_service.batch_insurance_submit(c,club_id=club_id,occurrence_id=occurrence_id,participant_ids=list(payload.get('participantIds') or []),provider=str(payload.get('provider') or ''))
        except LookupError as e: raise HTTPException(404,str(e))

@app.get('/api/club/{club_id}/occurrences/{occurrence_id}/insurance/export.csv')
def club_insurance_export(club_id:int,occurrence_id:int):
    with conn() as c:
        try:
            d=execution_service.dashboard(c,club_id=club_id,occurrence_id=occurrence_id)
        except LookupError as e: raise HTTPException(404,str(e))
    out=io.StringIO(); w=csv.writer(out)
    w.writerow(['姓名','手机号','证件类型','证件号码','紧急联系人','紧急联系人手机','保险状态','保险公司','保单号'])
    for p in d['participants']:
        w.writerow([p.get('name',''),p.get('phone',''),p.get('id_type',''),p.get('id_number',''),p.get('emergency_contact_name',''),p.get('emergency_contact_phone',''),p.get('insurance_status',''),p.get('insurance_provider',''),p.get('insurance_policy_no','')])
    data='\ufeff'+out.getvalue()
    return StreamingResponse(iter([data.encode('utf-8')]),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="insurance-occurrence-{occurrence_id}.csv"'})

# Leader mobile execution view. Demo uses club_id query; production must replace with authenticated leader assignment.
@app.get('/api/leader/occurrences/{occurrence_id}')
def leader_occurrence(occurrence_id:int,club_id:int=1):
    with conn() as c:
        try:return execution_service.leader_brief(c,club_id=club_id,occurrence_id=occurrence_id)
        except LookupError as e: raise HTTPException(404,str(e))

@app.patch('/api/leader/occurrences/{occurrence_id}/participants/{participant_id}/checkin')
def leader_participant_checkin(occurrence_id:int,participant_id:int,payload:dict=Body(...),club_id:int=1):
    with conn() as c:
        try:return execution_service.checkin(c,club_id=club_id,occurrence_id=occurrence_id,participant_id=participant_id,status=str(payload.get('status') or 'checked_in'),actor='leader',note=str(payload.get('note') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/leader/occurrences/{occurrence_id}/status')
def leader_update_status(occurrence_id:int,payload:dict=Body(...),club_id:int=1):
    with conn() as c:
        try:return execution_service.set_status(c,club_id=club_id,occurrence_id=occurrence_id,status=str(payload.get('status') or ''),actor='leader',note=str(payload.get('note') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/club/{club_id}/members')
def members(club_id:int):
    with conn() as c:
        membership_engine.refresh_all(c,club_id)
        return rows(c.execute('''SELECT m.*,u.name,u.phone,COALESCE(g.balance,0) gear_points FROM club_members m JOIN users u ON u.id=m.user_id LEFT JOIN gear_point_accounts g ON g.user_id=u.id WHERE m.club_id=? ORDER BY m.id DESC''',(club_id,)))

@app.get('/api/club/{club_id}/membership/tiers')
def club_membership_tiers(club_id:int):
    club_or_404(club_id)
    with conn() as c:return membership_engine.list_tiers(c,club_id)

@app.post('/api/club/{club_id}/membership/tiers')
def club_add_membership_tier(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        try: tid=membership_engine.upsert_tier(c,club_id=club_id,payload=payload)
        except ValueError as e: raise HTTPException(400,str(e))
        membership_engine.refresh_all(c,club_id)
    return {'ok':True,'tierId':tid}

@app.patch('/api/club/{club_id}/membership/tiers/{tier_id}')
def club_update_membership_tier(club_id:int,tier_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        try: membership_engine.upsert_tier(c,club_id=club_id,payload=payload,tier_id=tier_id)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
        membership_engine.refresh_all(c,club_id)
    return {'ok':True,'tierId':tier_id}

@app.post('/api/club/{club_id}/membership/recalculate')
def club_recalculate_membership(club_id:int):
    club_or_404(club_id)
    with conn() as c: count=membership_engine.refresh_all(c,club_id)
    return {'ok':True,'membersUpdated':count}

@app.get('/api/club/{club_id}/benefits')
def club_benefits(club_id:int):
    club_or_404(club_id)
    with conn() as c:return benefit_engine.list_for_club(c,club_id=club_id,include_inactive=True)

@app.post('/api/club/{club_id}/benefits')
def club_add_benefit(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        try: bid=benefit_engine.create_club_benefit(c,club_id=club_id,payload=payload)
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True,'benefitId':bid}

@app.get('/api/club/{club_id}/benefits/redemptions')
def club_benefit_redemptions(club_id:int):
    club_or_404(club_id)
    with conn() as c:return rows(c.execute('''SELECT r.*,b.title,u.name user_name FROM benefit_redemptions r
        JOIN member_benefits b ON b.id=r.benefit_id JOIN users u ON u.id=r.user_id
        WHERE r.display_club_id=? ORDER BY r.id DESC''',(club_id,)))

@app.get('/api/club/{club_id}/credits')
def credits(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        st=ai_credit_engine.statement(c,club_id=club_id)
        st['topupPackages']=ai_credit_engine.topup_packages(c)
        st['pendingOrders']=[x for x in ai_credit_engine.orders(c,club_id=club_id,limit=50) if x['status']=='pending']
        return {'account':st['account'],'ledger':st['ledger'][:60],'subscription':st['subscription'],'unresolvedDebt':st['unresolvedDebt'],
                'creditsConsumed':st['creditsConsumed'],'successfulCalls':st['successfulCalls'],'topupPackages':st['topupPackages'],'pendingOrders':st['pendingOrders'],'period':st['period']}

@app.get('/api/club/{club_id}/credits/statement')
def club_credit_statement(club_id:int,period:str|None=None):
    club_or_404(club_id)
    with conn() as c:
        st=ai_credit_engine.statement(c,club_id=club_id,period_key=period)
        st.pop('providerCostUsd',None);st.pop('providerCostCny',None);st.pop('usdCnyAccountingRate',None)
        return st

@app.post('/api/club/{club_id}/credits/topups')
def club_credit_topup(club_id:int,payload:dict=Body(...)):
    club=club_or_404(club_id)
    if club.get('status')!='active': raise HTTPException(403,'俱乐部当前未启用，不能创建充值订单')
    with conn() as c:
        try:return ai_credit_engine.create_topup_order(c,club_id=club_id,package_code=str(payload.get('packageCode') or ''))
        except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/club/{club_id}/ai/usage')
def club_ai_usage(club_id:int):
    club_or_404(club_id)
    with conn() as c:return rows(c.execute('SELECT id,task_type,provider,model,status,input_tokens,output_tokens,provider_cost,credits_charged,created_at FROM ai_usage_records WHERE club_id=? ORDER BY id DESC LIMIT 100',(club_id,)))

@app.get('/api/club/{club_id}/mall/products')
def club_products(club_id:int):
    club_or_404(club_id)
    with conn() as c:return rows(c.execute('SELECT id,name,sku,price,stock,status,image_url,category FROM products WHERE status="active" ORDER BY id DESC'))

@app.get('/api/club/{club_id}/mall/orders')
def club_orders(club_id:int):
    with conn() as c:return rows(c.execute('SELECT id,total,status,tracking_no,carrier,club_commission,after_sales_status,created_at FROM gear_orders WHERE source_club_id=? ORDER BY id DESC',(club_id,)))

@app.get('/api/club/{club_id}/mall/commission-summary')
def club_commission_summary(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        summary=commission_engine.summary(c,club_id=club_id)
        summary['recentSettlements']=commission_engine.settlements(c,club_id=club_id)[:10]
        return summary

@app.get('/api/club/{club_id}/mall/commissions')
def club_commission_ledger(club_id:int,status:str|None=None):
    club_or_404(club_id)
    with conn() as c:
        commission_engine.release_matured(c,club_id=club_id)
        sql='''SELECT l.*,o.status order_status,o.refund_status,o.delivered_at
               FROM commission_ledger l JOIN gear_orders o ON o.id=l.order_id WHERE l.club_id=?'''
        args=[club_id]
        if status:
            sql+=' AND l.status=?'; args.append(status)
        sql+=' ORDER BY l.id DESC'
        return rows(c.execute(sql,args))

@app.get('/api/club/{club_id}/mall/settlements')
def club_commission_settlements(club_id:int):
    club_or_404(club_id)
    with conn() as c:return commission_engine.settlements(c,club_id=club_id)


@app.get('/api/club/{club_id}/mall/after-sales')
def club_after_sales_readonly(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        ids=c.execute('SELECT id FROM after_sales_cases WHERE source_club_id=? ORDER BY created_at DESC',(club_id,)).fetchall()
        return [after_sales_engine.get(c,x['id']) for x in ids]

# PUBLIC / C-END
@app.get('/api/public/clubs/{club_id}')
def public_club(club_id:int):
    x=club_or_404(club_id)
    if IS_PROD:
        if x['status']!='active':raise HTTPException(404,'not found')
        return {k:x.get(k) for k in ('id','name','city')}
    return x

@app.get('/api/public/clubs/{club_id}/activities')
def public_activities(club_id:int):
    if IS_PROD and club_or_404(club_id)['status']!='active':raise HTTPException(404,'not found')
    with conn() as c:
        acts=rows(c.execute('SELECT id,title,event_date,location,price,capacity,cover FROM activities WHERE club_id=? AND status="published" ORDER BY id DESC',(club_id,)))
    for a in acts:
        cov=a.get('cover')
        if cov and str(cov).startswith('/static/'):
            a['cover']='/api/public/activities/%d/media/%s'%(a['id'],quote(str(cov)[len('/static/'):],safe='/'))
    return acts

@app.get('/api/public/activities/{activity_id}')
def public_activity(activity_id:int):
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND status="published"',(activity_id,)))
        occ=rows(c.execute('SELECT *,MAX(capacity-sold,0) remaining FROM activity_occurrences WHERE activity_id=? AND status="open" ORDER BY start_at',(activity_id,)))
    if not a: raise HTTPException(404,'活动不存在或未发布')
    if IS_PROD and club_or_404(int(a['club_id']))['status']!='active':raise HTTPException(404,'not found')
    a['activityMaster']=jload(a.pop('activity_master_json'),{});a['detail']=jload(a.pop('detail_json'),{});a['occurrences']=occ
    cov=a.get('cover')
    if cov and str(cov).startswith('/static/'):
        a['cover']='/api/public/activities/%d/media/%s'%(activity_id,quote(str(cov)[len('/static/'):],safe='/'))
    a['pointsPolicy']=activity_points_policy.from_activity(a).as_dict()
    a['refundPolicy']=activity_refund_policy.from_activity(a).as_dict()
    a['participantPolicy']=participant_service.from_activity(a).as_dict()
    if IS_PROD:
        # Public activity is not a dump of private activity_master_json (internalData).
        a={k:v for k,v in a.items() if k in ('id','club_id','title','event_date','location','price','capacity','status','cover','activityMaster','detail','occurrences','pointsPolicy','refundPolicy','participantPolicy')}
        master=a.get('activityMaster') or {}
        if isinstance(master,dict):
            public_keys={'title','date','location','price','capacity','itinerary','fees','checklist','services','media'}
            master=sanitize_public_document({k:v for k,v in master.items() if k in public_keys})
            approved=[]
            for media in master.get('media',[]):
                if not isinstance(media,dict):continue
                source=str(media.get('url',''))
                if _safe_media_path(source):
                    safe={k:v for k,v in media.items() if k in ('ref','width','height','alt','url')}
                    safe['url']=f'/api/public/activities/{activity_id}/media/'+quote(source[len('/static/'):],safe='/')
                    approved.append(safe)
            master['media']=approved
            a['activityMaster']=master
        detail=a.get('detail') or {}
        if isinstance(detail,dict):
            a['detail']=sanitize_public_document({k:v for k,v in detail.items() if k in ('blocks','title','summary','subtitle')})
        safe_occ_keys={'id','activity_id','club_id','start_at','end_at','price','capacity','sold','remaining','status','label'}
        a['occurrences']=[{k:v for k,v in occurrence.items() if k in safe_occ_keys} for occurrence in a['occurrences']]
    return a


_PUBLIC_IMAGE_EXT={'.png','.jpg','.jpeg','.webp','.gif'}
def _safe_media_path(source:str) -> Path|None:
    if not source.startswith('/static/') or '?' in source or '#' in source or '\\' in source:
        return None
    rel=source[len('/static/'):]
    if not (rel.startswith('uploads/') or rel.startswith('demo/')):
        return None
    if any(bit in ('','.','..') for bit in rel.split('/')):
        return None
    candidate=(STATIC/rel).resolve()
    if not candidate.is_relative_to(STATIC.resolve()) or candidate.suffix.lower() not in _PUBLIC_IMAGE_EXT:
        return None
    return candidate

@app.get('/api/public/activities/{activity_id}/media/{asset_path:path}')
def public_activity_media(activity_id:int,asset_path:str):
    # Published-activity allowlist, NOT general access to /static/uploads.
    # Only files explicitly referenced in the published editorial master are readable.
    with conn() as c:
        a=row(c.execute("""SELECT a.activity_master_json,a.cover,cl.status club_status
            FROM activities a JOIN clubs cl ON cl.id=a.club_id
            WHERE a.id=? AND a.status='published' """,(activity_id,)))
    if not a or a['club_status']!='active':raise HTTPException(404,'not found')
    original='/static/'+unquote(asset_path)
    file_path=_safe_media_path(original)
    if not file_path:raise HTTPException(404,'not found')
    master=jload(a['activity_master_json'],{})
    allowed={str(m.get('url')) for m in master.get('media',[]) if isinstance(m,dict)}
    cover=str(a.get('cover') or '')
    if cover: allowed.add(cover)
    if original not in allowed or not file_path.is_file():raise HTTPException(404,'not found')
    response=FileResponse(file_path)
    response.headers['Cache-Control']='public, max-age=300'
    return response

@app.get('/api/public/activities/{activity_id}/price-quote')
def price_quote(activity_id:int,occurrence_id:int=Query(...),user_id:int=1,club_points:int=0,gear_points:int=0,voucher_codes:str='',participant_count:int=1):
    codes=[x.strip() for x in str(voucher_codes or '').split(',') if x.strip()]
    with conn() as c:
        try:
            q=booking_engine.quote(c,activity_id=activity_id,occurrence_id=occurrence_id,user_id=user_id,requested_club_points=club_points,requested_gear_points=gear_points,participant_count=max(1,participant_count))
            benefits=benefit_engine.quote_vouchers(c,voucher_codes=codes,user_id=user_id,club_id=int(q.occurrence['club_id']),kind='activity',amount_available=float(q.points['payable']))
        except ValueError as e:
            raise HTTPException(409,str(e))
    out=q.points; out['wallet']=q.wallet; out['occurrence']=q.occurrence; out['pointsPolicy']=q.points_policy
    out['benefits']=benefits
    out['participantCount']=max(1,int(participant_count or 1))
    out['unitPrice']=float(q.occurrence['price'])
    out['payable']=round(max(0,float(out['payable'])-float(benefits['totalDiscount'])),2)
    out['funding']['club']=round(float(out['funding']['club'])+float(benefits['clubDiscount']),2)
    out['funding']['platform']=round(float(out['funding']['platform'])+float(benefits['platformSubsidy']),2)
    return out

@app.post('/api/public/activities/{activity_id}/checkout')
def create_activity_checkout(activity_id:int,payload:dict=Body(...)):
    name=payload.get('name','访客'); phone=payload.get('phone',''); occurrence_id=int(payload.get('occurrenceId') or 0)
    req_cp=max(0,int(payload.get('clubPoints',0))); req_gp=max(0,int(payload.get('gearPoints',0))); voucher_codes=payload.get('voucherCodes') or []
    participant_input=payload.get('participants') if isinstance(payload.get('participants'),list) else None
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND status="published"',(activity_id,)))
        if not a: raise HTTPException(404,'活动不可报名')
        occ=row(c.execute('SELECT * FROM activity_occurrences WHERE id=? AND activity_id=? AND status="open"',(occurrence_id,activity_id))) if occurrence_id else None
        if not occ: raise HTTPException(400,'请选择有效团期')
        uid=user_or_create(c,name,phone)
        c.execute('INSERT OR IGNORE INTO club_members(club_id,user_id) VALUES(?,?)',(a['club_id'],uid))
        c.execute('INSERT OR IGNORE INTO gear_point_accounts(user_id,balance) VALUES(?,0)',(uid,))
        try:
            participants,participant_policy=participant_service.normalize_for_checkout(activity=a,payer_name=name,payer_phone=phone,participants=participant_input)
            intent_id,q,policy_snapshot,benefits=checkout_engine.create_activity_intent(c,activity=a,occurrence=occ,user_id=uid,requested_club_points=req_cp,requested_gear_points=req_gp,voucher_codes=voucher_codes,participants=participants,participant_policy=participant_policy.as_dict())
        except (OverflowError,ValueError) as e: raise HTTPException(409,str(e))
    # Activity commerce is intentionally ClubOS Domain only. Medusa is Gear-only.
    return {'ok':True,'checkoutId':intent_id,'userId':uid,**q.as_dict(),'pointsPolicy':policy_snapshot,'benefits':benefits,
            'commerceProvider':'clubos-domain','commerceCartId':None,'commerceNote':None,'nextAction':'PAY',
            'participantCount':len(participants),'participantPolicy':participant_policy.as_dict()}

@app.post('/api/public/clubs/{club_id}/gear-checkout')
def create_gear_checkout(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id); user_id=int(payload.get('userId',1)); items=payload.get('items',[]); req_gp=max(0,int(payload.get('gearPoints',0))); voucher_codes=payload.get('voucherCodes') or []
    if not items: raise HTTPException(400,'items required')
    with conn() as c:
        c.execute('INSERT OR IGNORE INTO gear_point_accounts(user_id,balance) VALUES(?,0)',(user_id,))
        resolved=[]
        for it in items:
            p=row(c.execute('SELECT * FROM products WHERE id=? AND status="active"',(int(it['productId']),)))
            if not p: raise HTTPException(400,'商品不可售')
            q=max(1,int(it.get('quantity',1)))
            if int(p['stock'])<q: raise HTTPException(409,f"{p['name']} 库存不足")
            resolved.append((p,q))
        try: intent_id,q,ipayload,benefits=checkout_engine.create_gear_intent(c,club_id=club_id,user_id=user_id,items=items,resolved_products=resolved,requested_gear_points=req_gp,voucher_codes=voucher_codes)
        except ValueError as e: raise HTTPException(409,str(e))
    cart_id=None; commerce_note=None
    if commerce_provider()=='medusa':
        missing=[x for x in ipayload['items'] if not x.get('commerceVariantId')]
        if missing:
            commerce_note='部分商品尚未绑定 Medusa variant，需总平台先同步商品'
        else:
            try:
                medusa_items=[{'variant_id':x['commerceVariantId'],'quantity':x['quantity'],'unit_price':x['unitPrice'],'product_id':x['productId']} for x in ipayload['items']]
                cart=MedusaClient().prepare_gear_checkout(
                    intent_id=intent_id,items=medusa_items,
                    platform_subsidy=float(q.platform_point_subsidy)+float(benefits['platformSubsidy']),
                    metadata={'source_club_id':str(club_id),'user_id':str(user_id)},
                    email=payload.get('email'),shipping_address=payload.get('shippingAddress'),
                    shipping_option_id=payload.get('shippingOptionId'))
                cart_id=cart.get('id') if cart else None
                with conn() as c: checkout_engine.attach_commerce(c,intent_id=intent_id,cart_id=cart_id)
            except Exception as e: commerce_note=str(e)
    return {'ok':True,'checkoutId':intent_id,**q.as_dict(),'benefits':benefits,'commerceProvider':commerce_provider(),'commerceCartId':cart_id,'commerceNote':commerce_note,'nextAction':'PAY'}

@app.get('/api/public/checkouts/{checkout_id}')
def get_checkout(checkout_id:str):
    with conn() as c:
        x=row(c.execute('SELECT * FROM checkout_intents WHERE id=?',(checkout_id,)))
        holds=rows(c.execute('SELECT point_type,points,status,funding_owner FROM point_holds WHERE intent_id=? ORDER BY id',(checkout_id,)))
        vouchers=rows(c.execute('''SELECT r.id,r.voucher_code,r.status,r.funding_owner,r.cash_value,b.title,b.benefit_type FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id WHERE r.held_checkout_id=? OR (r.used_order_kind IS NOT NULL AND r.id IN (SELECT value FROM json_each(COALESCE((SELECT benefit_redemption_ids_json FROM checkout_intents WHERE id=?),'[]')))) ORDER BY r.id''',(checkout_id,checkout_id)))
        attempts=payment_lifecycle.attempts(c,checkout_id)
    if not x: raise HTTPException(404,'结算单不存在')
    x['payload']=jload(x.pop('payload_json'),{}); x['holds']=holds; x['vouchers']=vouchers; x['paymentAttempts']=attempts
    x['pointsPolicy']=jload(x.get('points_policy_snapshot_json'),{}) if x.get('points_policy_snapshot_json') else None
    x['participantPolicy']=jload(x.get('participant_policy_snapshot_json'),{}) if x.get('participant_policy_snapshot_json') else None
    # Async payment (real WeChat JSAPI / QR) polls this endpoint after the provider confirms.
    # Hand back the same confirmation receipt the synchronous /pay path returns so the C-end
    # success screen works identically in both flows.
    if x['status']=='paid' and x.get('result_json'):
        x['result']=jload(x.pop('result_json'),{})
    else:
        x.pop('result_json',None)
    return x

@app.post('/api/public/checkouts/{checkout_id}/pay')
def start_checkout_payment(checkout_id:str,payload:dict=Body(default={})):
    if IS_PROD and payload.get('simulateSuccess'):raise HTTPException(403,'simulated payment disabled')
    metadata=payload.get('metadata') or {}
    with conn() as c:
        intent=row(c.execute('SELECT * FROM checkout_intents WHERE id=?',(checkout_id,)))
        if not intent: raise HTTPException(404,'结算单不存在')
        if intent['status']=='paid':
            return {'ok':True,'checkoutId':checkout_id,'status':'paid','idempotent':True}
        # Zero-cash orders never leave ClubOS and still use the same idempotent confirm path.
        if float(intent.get('cash_amount') or 0)<=0:
            attempt=payment_lifecycle.start(c,checkout_id=checkout_id,provider='clubos-zero',channel='zero',merchant_order_no=merchant_order_no(),metadata=metadata)
            oid='zero_'+uuid.uuid4().hex[:16]
            commerce_order_id=_commerce_order_after_verified_payment(c,checkout_id=checkout_id,provider_payment_id=oid,provider='clubos-zero')
            payment_lifecycle.succeed(c,checkout_id=checkout_id,provider='clubos-zero',provider_payment_id=oid,payment_attempt_id=attempt.get('paymentAttemptId'),raw={'zeroCash':True})
            result=checkout_engine.confirm(c,intent_id=checkout_id,commerce_order_id=commerce_order_id)
            return {**attempt,'status':'succeeded','paymentStatus':'succeeded','result':result,'paymentAction':{'type':'none'}}
        try:
            account=payment_accounts.resolve_for_checkout(c,intent)
        except ValueError as e:
            raise HTTPException(409,str(e))
        mo=merchant_order_no()
        attempt=payment_lifecycle.start(c,checkout_id=checkout_id,provider=str(account['provider']),payment_account_id=int(account['id']),channel=str(account.get('channel') or ''),merchant_order_no=mo,metadata=metadata)
        attempt_id=attempt['paymentAttemptId']
        subject='ClubOS活动报名' if intent['kind']=='activity' else 'ClubOS装备商城'
        if intent['kind']=='activity':
            payload_json=jload(intent.get('payload_json'),{})
            a=row(c.execute('SELECT title FROM activities WHERE id=?',(payload_json.get('activityId'),))) if payload_json.get('activityId') else None
            if a: subject=str(a['title'])[:120]
    provider=provider_for_account(account)
    notify_base=_payment_public_base_url()
    notify_url=f"{notify_base}/api/payments/{account['provider']}/{account['id']}/notify"
    return_url=str(payload.get('returnUrl') or f"{notify_base}/web?checkout={checkout_id}")
    try:
        created=provider.create_payment(account=account,merchant_order_no=mo,amount=float(intent['cash_amount']),subject=subject,notify_url=notify_url,return_url=return_url,metadata=metadata)
    except Exception as e:
        with conn() as c:
            payment_lifecycle.fail(c,checkout_id=checkout_id,provider=str(account['provider']),reason=str(e),payment_attempt_id=attempt_id,raw={'error':str(e)})
        raise HTTPException(502,f'支付渠道下单失败: {e}')
    with conn() as c:
        payment_lifecycle.attach_provider_result(c,payment_attempt_id=attempt_id,provider_payment_id=created.provider_payment_id,action=created.action,raw=created.raw)
        # Local provider remains an explicit demo path; production providers only succeed by verified provider callback.
        if str(account['provider'])=='local' and bool(payload.get('simulateSuccess',False)) and not IS_PROD:
            pid=created.provider_payment_id or ('local_'+uuid.uuid4().hex[:16])
            commerce_order_id=_commerce_order_after_verified_payment(c,checkout_id=checkout_id,provider_payment_id=pid,provider='local')
            payment_lifecycle.succeed(c,checkout_id=checkout_id,provider='local',provider_payment_id=pid,payment_attempt_id=attempt_id,raw=created.raw or {})
            result=checkout_engine.confirm(c,intent_id=checkout_id,commerce_order_id=commerce_order_id)
            return {**attempt,'status':'succeeded','paymentStatus':'succeeded','paymentAction':created.action,'result':result}
    return {**attempt,'paymentStatus':'processing','paymentAction':created.action,'providerPaymentId':created.provider_payment_id,'nextAction':'COMPLETE_PROVIDER_PAYMENT'}

@app.post('/api/public/checkouts/{checkout_id}/confirm')
def confirm_checkout(checkout_id:str,payload:dict=Body(default={})):
    # Demo/manual confirmation endpoint. Production payment provider/webhook should call the same idempotent domain function.
    order_id=payload.get('commerceOrderId')
    with conn() as c:
        try:
            commerce_order_id=_commerce_order_after_verified_payment(c,checkout_id=checkout_id,provider_payment_id=order_id,provider='local-manual')
            payment_lifecycle.succeed(c,checkout_id=checkout_id,provider='local-manual',provider_payment_id=order_id,raw=payload)
            return checkout_engine.confirm(c,intent_id=checkout_id,commerce_order_id=commerce_order_id)
        except LookupError as e: raise HTTPException(404,str(e))
        except (ValueError,OverflowError) as e: raise HTTPException(409,str(e))

@app.post('/api/public/checkouts/{checkout_id}/cancel')
def cancel_checkout(checkout_id:str):
    with conn() as c:
        try:return checkout_engine.cancel_intent(c,intent_id=checkout_id)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

def _payment_account_by_id(c, account_id:int):
    a=row(c.execute('SELECT * FROM payment_accounts WHERE id=? AND enabled=1',(account_id,)))
    if not a: raise HTTPException(404,'支付账户不存在或已停用')
    return a


def _record_payment_event(c, *, event_key:str, provider:str, event_type:str, account_id:int,
                          checkout_id:str|None, merchant_no:str|None, transaction_id:str|None,
                          verified:bool, payload:dict):
    existing=row(c.execute('SELECT * FROM payment_events WHERE event_key=?',(event_key,)))
    if existing:return False
    c.execute("""INSERT INTO payment_events(event_key,provider,event_type,payment_account_id,checkout_intent_id,
                 merchant_order_no,provider_transaction_id,verified,payload_json) VALUES(?,?,?,?,?,?,?,?,?)""",
              (event_key,provider,event_type,account_id,checkout_id,merchant_no,transaction_id,1 if verified else 0,jdump(payload)))
    return True


@app.post('/api/payments/{provider_name}/{account_id}/notify')
async def provider_payment_notify(provider_name:str,account_id:int,request:Request):
    """Verified provider callback endpoint for WeChat Pay v3 and Alipay.

    Only a verified callback may turn a cash checkout into paid. Browser return URLs never
    finalize money state.
    """
    with conn() as c: account=_payment_account_by_id(c,account_id)
    if str(account.get('provider'))!=provider_name:
        raise HTTPException(400,'支付渠道与账户不匹配')
    provider=provider_for_account(account)
    if provider_name=='wechatpay_v3':
        raw=await request.body()
        try: verified=provider.verify_callback(dict(request.headers),raw)
        except Exception as e: raise HTTPException(401,f'微信支付回调验签失败: {e}')
        envelope=verified['envelope']; resource=verified['resource']; event_key=str(envelope.get('id') or '')
        if not event_key: raise HTTPException(400,'微信支付通知缺少事件ID')
        if resource.get('out_refund_no'):
            refund_no=str(resource.get('out_refund_no') or '')
            with conn() as c:
                existing=row(c.execute('SELECT * FROM payment_events WHERE event_key=?',(event_key,)))
                if existing:return {'code':'SUCCESS','message':'成功'}
                rr=row(c.execute('SELECT * FROM refund_requests WHERE provider_refund_no=?',(refund_no,)))
                if not rr: raise HTTPException(404,'找不到对应退款申请')
                status=str(resource.get('refund_status') or '').upper()
                c.execute("""UPDATE refund_requests SET provider_status=?,provider_refund_id=COALESCE(?,provider_refund_id),
                             provider_payload_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                          (status,str(resource.get('refund_id') or '') or None,jdump(resource),rr['id']))
                result=None
                if status=='SUCCESS':
                    result=refund_lifecycle.finalize(c,refund_id=rr['id'],provider_refund_id=str(resource.get('refund_id') or refund_no),provider='wechatpay_v3')
                _record_payment_event(c,event_key=event_key,provider='wechatpay_v3',event_type='refund.'+status.lower(),account_id=account_id,
                                      checkout_id=rr.get('checkout_intent_id'),merchant_no=str(resource.get('out_trade_no') or ''),transaction_id=str(resource.get('transaction_id') or ''),verified=True,payload=resource)
            return {'code':'SUCCESS','message':'成功','result':result}
        merchant_no=str(resource.get('out_trade_no') or '')
        trade_state=str(resource.get('trade_state') or '').upper()
        transaction_id=str(resource.get('transaction_id') or '')
        with conn() as c:
            existing=row(c.execute('SELECT * FROM payment_events WHERE event_key=?',(event_key,)))
            if existing:return {'code':'SUCCESS','message':'成功'}
            attempt=payment_lifecycle.by_merchant_order(c,merchant_no)
            if not attempt or int(attempt.get('payment_account_id') or 0)!=account_id:
                raise HTTPException(404,'找不到对应支付尝试')
            total_cents=int(((resource.get('amount') or {}).get('total') or 0))
            if total_cents!=int(round(float(attempt.get('amount') or 0)*100)):
                raise HTTPException(409,'支付金额校验失败')
            result=None
            if trade_state=='SUCCESS':
                commerce_order_id=_commerce_order_after_verified_payment(c,checkout_id=attempt['checkout_intent_id'],provider_payment_id=transaction_id or merchant_no,provider='wechatpay_v3')
                payment_lifecycle.succeed(c,checkout_id=attempt['checkout_intent_id'],provider='wechatpay_v3',provider_payment_id=transaction_id or None,
                                          payment_attempt_id=attempt['id'],raw=resource)
                result=checkout_engine.confirm(c,intent_id=attempt['checkout_intent_id'],commerce_order_id=commerce_order_id)
            elif trade_state in ('CLOSED','PAYERROR','REVOKED'):
                payment_lifecycle.fail(c,checkout_id=attempt['checkout_intent_id'],provider='wechatpay_v3',reason=trade_state,
                                       provider_payment_id=transaction_id or None,payment_attempt_id=attempt['id'],raw=resource)
            _record_payment_event(c,event_key=event_key,provider='wechatpay_v3',event_type='payment.'+trade_state.lower(),account_id=account_id,
                                  checkout_id=attempt['checkout_intent_id'],merchant_no=merchant_no,transaction_id=transaction_id,verified=True,payload=resource)
        return {'code':'SUCCESS','message':'成功','result':result}
    if provider_name=='alipay':
        form=dict(await request.form())
        try: verified=provider.verify_callback(form)
        except Exception as e: raise HTTPException(401,f'支付宝回调验签失败: {e}')
        if account.get('app_id') and str(verified.get('app_id') or '')!=str(account.get('app_id')):
            raise HTTPException(409,'支付宝app_id不匹配')
        merchant_no=str(verified.get('out_trade_no') or '')
        trade_no=str(verified.get('trade_no') or '')
        trade_status=str(verified.get('trade_status') or '')
        event_key=str(verified.get('notify_id') or f"{trade_no}:{trade_status}")
        with conn() as c:
            existing=row(c.execute('SELECT * FROM payment_events WHERE event_key=?',(event_key,)))
            if existing:return PlainTextResponse('success')
            attempt=payment_lifecycle.by_merchant_order(c,merchant_no)
            if not attempt or int(attempt.get('payment_account_id') or 0)!=account_id:
                raise HTTPException(404,'找不到对应支付尝试')
            if abs(float(verified.get('total_amount') or 0)-float(attempt.get('amount') or 0))>0.001:
                raise HTTPException(409,'支付金额校验失败')
            result=None
            if trade_status in ('TRADE_SUCCESS','TRADE_FINISHED'):
                commerce_order_id=_commerce_order_after_verified_payment(c,checkout_id=attempt['checkout_intent_id'],provider_payment_id=trade_no or merchant_no,provider='alipay')
                payment_lifecycle.succeed(c,checkout_id=attempt['checkout_intent_id'],provider='alipay',provider_payment_id=trade_no or None,
                                          payment_attempt_id=attempt['id'],raw=verified)
                result=checkout_engine.confirm(c,intent_id=attempt['checkout_intent_id'],commerce_order_id=commerce_order_id)
            elif trade_status=='TRADE_CLOSED':
                payment_lifecycle.fail(c,checkout_id=attempt['checkout_intent_id'],provider='alipay',reason=trade_status,provider_payment_id=trade_no or None,payment_attempt_id=attempt['id'],raw=verified)
            _record_payment_event(c,event_key=event_key,provider='alipay',event_type='payment.'+trade_status.lower(),account_id=account_id,
                                  checkout_id=attempt['checkout_intent_id'],merchant_no=merchant_no,transaction_id=trade_no,verified=True,payload=verified)
        return PlainTextResponse('success')
    raise HTTPException(400,'该支付渠道没有生产回调处理器')


@app.post('/api/commerce/events/order-paid')
def commerce_order_paid(payload:dict=Body(...), x_clubos_commerce_secret:str|None=Header(default=None)):
    """Idempotent payment-event seam used by the Medusa order.placed subscriber."""
    expected=os.getenv('CLUBOS_COMMERCE_WEBHOOK_SECRET','').strip()
    if expected and x_clubos_commerce_secret != expected:
        raise HTTPException(401,'invalid commerce webhook secret')
    event_key=str(payload.get('eventKey') or payload.get('eventId') or '')
    checkout_id=str(payload.get('checkoutId') or '')
    order_id=str(payload.get('orderId') or '')
    provider=str(payload.get('provider') or commerce_provider())
    if not event_key or not checkout_id: raise HTTPException(400,'eventKey and checkoutId required')
    with conn() as c:
        existing=row(c.execute('SELECT * FROM commerce_events WHERE event_key=?',(event_key,)))
        if existing: return {'ok':True,'idempotent':True,'eventKey':event_key}
        try:
            payment_lifecycle.succeed(c,checkout_id=checkout_id,provider=provider,provider_payment_id=order_id or None,raw=payload)
            result=checkout_engine.confirm(c,intent_id=checkout_id,commerce_order_id=order_id or None)
        except LookupError as e: raise HTTPException(404,str(e))
        except (ValueError,OverflowError) as e: raise HTTPException(409,str(e))
        c.execute('INSERT INTO commerce_events(event_key,provider,event_type,checkout_intent_id,commerce_order_id,payload_json) VALUES(?,?,?,?,?,?)',
                  (event_key,provider,'order.paid',checkout_id,order_id,jdump(payload)))
    return {'ok':True,'eventKey':event_key,'result':result}

@app.post('/api/commerce/events/order-placed')
def commerce_order_placed(payload:dict=Body(...), x_clubos_commerce_secret:str|None=Header(default=None)):
    """Reconcile a Medusa Order after it has been created.

    This callback is never payment authority. A Medusa order cannot independently mark
    a ClubOS checkout paid or award Gear Points/commission/AI Credits.
    """
    expected=os.getenv('CLUBOS_COMMERCE_WEBHOOK_SECRET','').strip()
    if expected and x_clubos_commerce_secret != expected: raise HTTPException(401,'invalid commerce webhook secret')
    event_key=str(payload.get('eventKey') or payload.get('eventId') or '')
    checkout_id=str(payload.get('checkoutId') or '')
    order_id=str(payload.get('orderId') or '')
    if not event_key or not checkout_id or not order_id: raise HTTPException(400,'eventKey, checkoutId and orderId required')
    with conn() as c:
        existing=row(c.execute('SELECT * FROM commerce_events WHERE event_key=?',(event_key,)))
        if existing:return {'ok':True,'idempotent':True,'eventKey':event_key}
        intent=row(c.execute('SELECT * FROM checkout_intents WHERE id=?',(checkout_id,)))
        if not intent: raise HTTPException(404,'结算单不存在')
        reconciled=False; reason=None
        if intent.get('kind')!='gear':
            reason='non_gear_checkout_ignored'
        elif intent.get('payment_status')!='succeeded':
            reason='payment_not_verified'
        elif intent.get('commerce_order_id') and str(intent.get('commerce_order_id'))!=order_id:
            raise HTTPException(409,'Medusa Order 与已记录 commerce_order_id 不一致')
        else:
            c.execute('UPDATE checkout_intents SET commerce_order_id=COALESCE(commerce_order_id,?),updated_at=CURRENT_TIMESTAMP WHERE id=?',(order_id,checkout_id))
            if intent.get('status')=='paid' and intent.get('result_id'):
                c.execute('UPDATE gear_orders SET commerce_order_id=COALESCE(commerce_order_id,?) WHERE id=?',(order_id,int(intent['result_id'])))
            reconciled=True
        c.execute('INSERT INTO commerce_events(event_key,provider,event_type,checkout_intent_id,commerce_order_id,payload_json) VALUES(?,?,?,?,?,?)',
                  (event_key,'medusa','order.placed',checkout_id,order_id,jdump(payload)))
    return {'ok':True,'eventKey':event_key,'reconciled':reconciled,'reason':reason}

@app.post('/api/commerce/events/payment-succeeded')
def commerce_payment_succeeded(payload:dict=Body(...), x_clubos_commerce_secret:str|None=Header(default=None)):
    # Provider-neutral alias. Medusa/WeChat/Alipay adapters can all target this event contract.
    expected=os.getenv('CLUBOS_COMMERCE_WEBHOOK_SECRET','').strip()
    if expected and x_clubos_commerce_secret != expected: raise HTTPException(401,'invalid commerce webhook secret')
    event_key=str(payload.get('eventKey') or payload.get('eventId') or '')
    checkout_id=str(payload.get('checkoutId') or '')
    order_id=str(payload.get('orderId') or payload.get('providerPaymentId') or '')
    provider=str(payload.get('provider') or commerce_provider())
    if not event_key or not checkout_id: raise HTTPException(400,'eventKey and checkoutId required')
    with conn() as c:
        existing=row(c.execute('SELECT * FROM commerce_events WHERE event_key=?',(event_key,)))
        if existing:return {'ok':True,'idempotent':True,'eventKey':event_key}
        try:
            commerce_order_id=_commerce_order_after_verified_payment(c,checkout_id=checkout_id,provider_payment_id=order_id or None,provider=provider)
            payment_lifecycle.succeed(c,checkout_id=checkout_id,provider=provider,provider_payment_id=order_id or None,payment_attempt_id=payload.get('paymentAttemptId'),raw=payload)
            result=checkout_engine.confirm(c,intent_id=checkout_id,commerce_order_id=commerce_order_id)
        except LookupError as e: raise HTTPException(404,str(e))
        except (ValueError,OverflowError) as e: raise HTTPException(409,str(e))
        c.execute('INSERT INTO commerce_events(event_key,provider,event_type,checkout_intent_id,commerce_order_id,payload_json) VALUES(?,?,?,?,?,?)',
                  (event_key,provider,'payment.succeeded',checkout_id,order_id,jdump(payload)))
    return {'ok':True,'eventKey':event_key,'result':result}

@app.post('/api/commerce/events/payment-failed')
def commerce_payment_failed(payload:dict=Body(...), x_clubos_commerce_secret:str|None=Header(default=None)):
    expected=os.getenv('CLUBOS_COMMERCE_WEBHOOK_SECRET','').strip()
    if expected and x_clubos_commerce_secret != expected: raise HTTPException(401,'invalid commerce webhook secret')
    event_key=str(payload.get('eventKey') or payload.get('eventId') or '')
    checkout_id=str(payload.get('checkoutId') or '')
    provider=str(payload.get('provider') or commerce_provider())
    if not event_key or not checkout_id: raise HTTPException(400,'eventKey and checkoutId required')
    with conn() as c:
        existing=row(c.execute('SELECT * FROM commerce_events WHERE event_key=?',(event_key,)))
        if existing:return {'ok':True,'idempotent':True,'eventKey':event_key}
        try:
            result=payment_lifecycle.fail(c,checkout_id=checkout_id,provider=provider,reason=str(payload.get('reason') or 'provider_payment_failed'),provider_payment_id=payload.get('providerPaymentId'),payment_attempt_id=payload.get('paymentAttemptId'),raw=payload)
        except LookupError as e: raise HTTPException(404,str(e))
        c.execute('INSERT INTO commerce_events(event_key,provider,event_type,checkout_intent_id,commerce_order_id,payload_json) VALUES(?,?,?,?,?,?)',
                  (event_key,provider,'payment.failed',checkout_id,str(payload.get('orderId') or ''),jdump(payload)))
    return {'ok':True,'eventKey':event_key,'result':result}

@app.post('/api/public/activities/{activity_id}/signup')
def signup(activity_id:int,payload:dict=Body(...)):
    if IS_PROD: raise HTTPException(403,'demo-only endpoint disabled')
    name=payload.get('name','访客');phone=payload.get('phone','');occurrence_id=int(payload.get('occurrenceId') or 0)
    req_cp=max(0,int(payload.get('clubPoints',0)));req_gp=max(0,int(payload.get('gearPoints',0)))
    participant_input=payload.get('participants') if isinstance(payload.get('participants'),list) else None
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND status="published"',(activity_id,)))
        if not a: raise HTTPException(404,'活动不可报名')
        occ=row(c.execute('SELECT * FROM activity_occurrences WHERE id=? AND activity_id=? AND status="open"',(occurrence_id,activity_id))) if occurrence_id else None
        if not occ: raise HTTPException(400,'请选择有效团期')
        uid=user_or_create(c,name,phone)
        c.execute('INSERT OR IGNORE INTO club_members(club_id,user_id) VALUES(?,?)',(a['club_id'],uid))
        c.execute('INSERT OR IGNORE INTO gear_point_accounts(user_id,balance) VALUES(?,0)',(uid,))
        commerce_order_id=payload.get('commerceOrderId')
        booking_ref=payload.get('bookingRef')
        try:
            participants,participant_policy=participant_service.normalize_for_checkout(activity=a,payer_name=name,payer_phone=phone,participants=participant_input)
            rid,q,earned,policy_snapshot,participant_ids=booking_engine.book(c,activity=a,occurrence=occ,user_id=uid,requested_club_points=req_cp,requested_gear_points=req_gp,commerce_order_id=commerce_order_id,booking_ref=booking_ref,participants=participants,participant_policy=participant_policy.as_dict())
        except (OverflowError,ValueError) as e:
            raise HTTPException(409,str(e))
    return {'ok':True,'registrationId':rid,'userId':uid,**q.as_dict(),'clubPointsEarned':earned,'pointsPolicy':policy_snapshot,'commerceOrderId':commerce_order_id,'bookingRef':booking_ref,'participantCount':len(participants),'participantIds':participant_ids}


@app.get('/api/public/registrations/{registration_id}/participants')
def public_registration_participants(registration_id:int):
    with conn() as c:
        reg=row(c.execute('SELECT * FROM registrations WHERE id=?',(registration_id,)))
        if not reg: raise HTTPException(404,'报名记录不存在')
        return {'registrationId':registration_id,'participantPolicy':jload(reg.get('participant_policy_snapshot_json'),{}),
                **participant_service.summary_for_registration(c,registration_id)}

@app.patch('/api/public/registrations/{registration_id}/participants/{participant_id}')
def public_update_participant(registration_id:int,participant_id:int,payload:dict=Body(...)):
    with conn() as c:
        p=row(c.execute('SELECT * FROM registration_participants WHERE id=? AND registration_id=?',(participant_id,registration_id)))
        if not p: raise HTTPException(404,'参加人不存在')
        supplement={k:v for k,v in payload.items() if k in {'idType','idNumber','emergencyContactName','emergencyContactPhone','notes'}}
        if not supplement: raise HTTPException(400,'补充资料接口不允许直接更换姓名/手机号；请使用转名额功能')
        try:return participant_service.update(c,participant_id=participant_id,payload=supplement,actor_type='c_end')
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/public/registrations/{registration_id}/participants/{participant_id}/replace')
def public_replace_participant(registration_id:int,participant_id:int,payload:dict=Body(...)):
    with conn() as c:
        p=row(c.execute('SELECT * FROM registration_participants WHERE id=? AND registration_id=?',(participant_id,registration_id)))
        if not p: raise HTTPException(404,'参加人不存在')
        try:return participant_service.replace(c,participant_id=participant_id,payload=payload,actor_type='c_end',reason=str(payload.get('reason') or '用户自助转名额'))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/public/registrations/{registration_id}/participants/{participant_id}/refund-quote')
def participant_refund_quote(registration_id:int,participant_id:int):
    with conn() as c:
        reg=row(c.execute('SELECT * FROM registrations WHERE id=?',(registration_id,)))
        p=row(c.execute('SELECT * FROM registration_participants WHERE id=? AND registration_id=?',(participant_id,registration_id)))
        if not reg or not p: raise HTTPException(404,'报名记录或参加人不存在')
        if p.get('status')!='active': return {'eligible':False,'reason':'该参加人已退出或不可退款'}
        if p.get('refund_status') in ('requested','processing'): return {'eligible':False,'reason':'该参加人退款正在处理中'}
        a=row(c.execute('SELECT * FROM activities WHERE id=?',(reg['activity_id'],)))
        occ=row(c.execute('SELECT * FROM activity_occurrences WHERE id=?',(reg['occurrence_id'],))) if reg.get('occurrence_id') else None
        try: alloc=participant_service.financial_allocation(c,participant_id)
        except LookupError as e: raise HTTPException(404,str(e))
        if not a or not alloc: raise HTTPException(404,'活动或参加人分摊不存在')
        decision=activity_refund_policy.evaluate(activity=a,occurrence=occ,cash_paid=float(alloc.get('cash_paid') or 0))
        decision['participantId']=participant_id; decision['participantName']=p.get('name')
        decision['allocation']={
            'originalAmount':float(alloc.get('original_amount') or 0),'cashPaid':float(alloc.get('cash_paid') or 0),
            'clubPointsUsed':int(alloc.get('club_points_used') or 0),'gearPointsUsed':int(alloc.get('gear_points_used') or 0),
            'clubBenefitDiscount':float(alloc.get('club_benefit_discount') or 0),'platformBenefitSubsidy':float(alloc.get('platform_benefit_subsidy') or 0),
            'clubPointsEarned':int(alloc.get('club_points_earned') or 0),
        }
        return decision

@app.post('/api/public/registrations/{registration_id}/participants/{participant_id}/refund-request')
def request_participant_refund(registration_id:int,participant_id:int,payload:dict=Body(default={})):
    with conn() as c:
        try:return refund_lifecycle.request_activity_participant(c,registration_id=registration_id,participant_id=participant_id,requester='c_end_user',reason=str(payload.get('reason') or '参加人退出'))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/public/registrations/{registration_id}/refund-quote')
def registration_refund_quote(registration_id:int):
    with conn() as c:
        reg=row(c.execute('SELECT * FROM registrations WHERE id=?',(registration_id,)))
        if not reg: raise HTTPException(404,'报名记录不存在')
        a=row(c.execute('SELECT * FROM activities WHERE id=?',(reg['activity_id'],)))
        occ=row(c.execute('SELECT * FROM activity_occurrences WHERE id=?',(reg['occurrence_id'],))) if reg.get('occurrence_id') else None
        if not a: raise HTTPException(404,'活动不存在')
        decision=activity_refund_policy.evaluate(activity=a,occurrence=occ,cash_paid=float(reg.get('amount') or 0))
    return decision

@app.post('/api/public/registrations/{registration_id}/refund-request')
def request_registration_refund(registration_id:int,payload:dict=Body(default={})):
    with conn() as c:
        try:return refund_lifecycle.request_activity(c,registration_id=registration_id,requester='c_end_user',reason=str(payload.get('reason') or '用户申请退款'))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/public/registrations/{registration_id}/cancel')
def cancel_registration(registration_id:int):
    if IS_PROD: raise HTTPException(403,'demo-only endpoint disabled')
    # Legacy/demo-only shortcut retained for old regression tests. Formal v0.11 production
    # flow is refund-quote -> refund-request -> club approval -> provider refund callback.
    with conn() as c:
        try:return booking_engine.cancel(c,registration_id=registration_id)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/club/{club_id}/refunds')
def club_refunds(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        return rows(c.execute('''SELECT rr.*,r.original_amount registration_original_amount,r.amount paid_cash,
            a.title activity_title,u.name user_name,u.phone,o.label occurrence_label,o.start_at,
            p.name participant_name,p.phone participant_phone
            FROM refund_requests rr
            JOIN registrations r ON r.id=rr.registration_id
            JOIN activities a ON a.id=r.activity_id
            JOIN users u ON u.id=r.user_id
            LEFT JOIN activity_occurrences o ON o.id=r.occurrence_id
            LEFT JOIN registration_participants p ON p.id=rr.participant_id
            WHERE rr.club_id=? AND rr.kind='activity' ORDER BY rr.created_at DESC''',(club_id,)))

def _mirror_refund_to_commerce(refund_id:str) -> dict:
    """Mirror a provider-confirmed Gear cash refund into Medusa bookkeeping.

    Provider/ClubOS remains the payment truth. A Medusa sync failure is recorded for
    reconciliation and retry; it never rolls back an already-successful money refund.
    """
    with conn() as c:
        rr=row(c.execute('SELECT * FROM refund_requests WHERE id=?',(refund_id,)))
        if not rr: raise LookupError('退款申请不存在')
        if rr.get('kind')!='gear' or float(rr.get('cash_amount') or 0)<=0 or not rr.get('commerce_order_id') or commerce_provider()!='medusa':
            c.execute("UPDATE refund_requests SET commerce_refund_status='not_required',commerce_refund_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",(refund_id,))
            return {'ok':True,'status':'not_required'}
        if rr.get('commerce_refund_status')=='synced':
            return {'ok':True,'status':'synced','idempotent':True,'commerceRefundId':rr.get('commerce_refund_id')}
        order_id=str(rr['commerce_order_id']); amount=float(rr.get('cash_amount') or 0)
        c.execute("UPDATE refund_requests SET commerce_refund_status='syncing',commerce_refund_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",(refund_id,))
    try:
        mirrored=MedusaClient().mirror_order_refund(order_id,amount,note=f'ClubOS refund {refund_id}')
        refund_ids=mirrored.get('refund_ids') or []
        mirror_id=','.join(str(x) for x in refund_ids if x) or None
        with conn() as c:
            c.execute("UPDATE refund_requests SET commerce_refund_status='synced',commerce_refund_id=COALESCE(?,commerce_refund_id),commerce_refund_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",(mirror_id,refund_id))
        return {'ok':True,'status':'synced','commerceRefundId':mirror_id,**mirrored}
    except Exception as e:
        with conn() as c:
            c.execute("UPDATE refund_requests SET commerce_refund_status='error',commerce_refund_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(str(e),refund_id))
        return {'ok':False,'status':'error','error':str(e),'retryable':True}


def _finalize_provider_refund(refund_id:str, *, provider_refund_id:str|None, provider:str) -> dict:
    with conn() as c:
        result=refund_lifecycle.finalize(c,refund_id=refund_id,provider_refund_id=provider_refund_id,provider=provider)
        rr=row(c.execute('SELECT after_sales_case_id FROM refund_requests WHERE id=?',(refund_id,))) or {}
        if rr.get('after_sales_case_id'):
            after_sales_engine.mark_refunded(c,case_id=str(rr['after_sales_case_id']))
    mirror=_mirror_refund_to_commerce(refund_id)
    return {**result,'commerceMirror':mirror}

def _submit_refund_to_provider(refund_id:str):
    with conn() as c:
        rr=row(c.execute('SELECT * FROM refund_requests WHERE id=?',(refund_id,)))
        if not rr: raise HTTPException(404,'退款申请不存在')
        if rr['status']=='succeeded':
            mirror=_mirror_refund_to_commerce(refund_id)
            return {'ok':True,'refundRequestId':refund_id,'status':'succeeded','idempotent':True,'commerceMirror':mirror}
        if rr['status']!='processing': raise HTTPException(409,f"当前退款状态不能提交渠道: {rr['status']}")
        if float(rr.get('cash_amount') or 0)<=0:
            return refund_lifecycle.finalize(c,refund_id=refund_id,provider_refund_id='no_cash',provider='clubos')
        checkout_id=str(rr.get('checkout_intent_id') or '')
        attempt=payment_lifecycle.succeeded_for_checkout(c,checkout_id) if checkout_id else None
        if not attempt:
            raise HTTPException(409,'找不到原支付成功记录，无法原路退款')
        account_id=int(attempt.get('payment_account_id') or 0)
        if not account_id:
            # legacy local/manual payment
            account=payment_accounts.resolve_for_checkout(c,row(c.execute('SELECT * FROM checkout_intents WHERE id=?',(checkout_id,))))
            account_id=int(account['id'])
        else:
            account=_payment_account_by_id(c,account_id)
        refund_no=str(rr.get('provider_refund_no') or provider_refund_no())
        c.execute('''UPDATE refund_requests SET payment_account_id=?,provider=?,provider_refund_no=?,provider_status='submitting',updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                  (account_id,str(account['provider']),refund_no,refund_id))
        merchant_no=str(attempt.get('merchant_order_no') or '')
        provider_tx=str(attempt.get('provider_payment_id') or '') or None
        original_amount=float(attempt.get('amount') or 0)
        refund_amount=float(rr.get('cash_amount') or 0)
        reason=str(rr.get('reason') or '用户退款')
    if str(account.get('provider'))=='local':
        # Keep deterministic local/demo lifecycle backward compatible: approval moves to processing,
        # then tests/demo may send the existing commerce refund-succeeded event.
        with conn() as c:
            c.execute("UPDATE refund_requests SET provider_status='local_waiting',updated_at=CURRENT_TIMESTAMP WHERE id=?",(refund_id,))
        return {'ok':True,'refundRequestId':refund_id,'status':'processing','providerStatus':'local_waiting','provider':'local',
                'cashAmount':float(rr.get('cash_amount') or 0),'originalCashAmount':float(rr.get('original_cash_amount') or rr.get('cash_amount') or 0),
                'refundPercent':float(rr.get('refund_percent') or 0),'retainedCashAmount':float(rr.get('retained_cash_amount') or 0),
                'nextAction':'SIMULATE_PROVIDER_REFUND_CALLBACK'}
    provider=provider_for_account(account)
    notify_url=f"{_payment_public_base_url()}/api/payments/{account['provider']}/{account['id']}/notify"
    try:
        result=provider.create_refund(account=account,merchant_order_no=merchant_no,provider_transaction_id=provider_tx,
                                      provider_refund_no=refund_no,refund_amount=refund_amount,original_amount=original_amount,
                                      reason=reason,notify_url=notify_url)
    except Exception as e:
        with conn() as c:
            c.execute("UPDATE refund_requests SET provider_status='error',provider_payload_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(jdump({'error':str(e)}),refund_id))
        raise HTTPException(502,f'退款渠道提交失败: {e}')
    with conn() as c:
        c.execute('''UPDATE refund_requests SET provider_status=?,provider_refund_id=COALESCE(?,provider_refund_id),provider_payload_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                  (result.status,result.provider_refund_id,jdump(result.raw or {}),refund_id))
        provider_name=str(account['provider'])
        provider_status=result.status
        provider_refund_id=result.provider_refund_id
    if provider_status=='succeeded':
        return _finalize_provider_refund(refund_id,provider_refund_id=provider_refund_id or refund_no,provider=provider_name)
    if provider_status=='failed':
        return {'ok':False,'refundRequestId':refund_id,'status':'processing','providerStatus':'failed','retryable':True,'provider':provider_name}
    return {'ok':True,'refundRequestId':refund_id,'status':'processing','providerStatus':provider_status,'providerRefundNo':refund_no,'providerRefundId':provider_refund_id,'provider':provider_name}


@app.post('/api/club/{club_id}/refunds/{refund_id}/approve')
def club_approve_refund(club_id:int,refund_id:str):
    club_or_404(club_id)
    with conn() as c:
        rr=row(c.execute("SELECT * FROM refund_requests WHERE id=? AND kind='activity' AND club_id=?",(refund_id,club_id)))
        if not rr: raise HTTPException(404,'活动退款申请不存在')
        try: refund_lifecycle.approve(c,refund_id=refund_id,approver=f'club:{club_id}')
        except ValueError as e: raise HTTPException(409,str(e))
    return _submit_refund_to_provider(refund_id)

@app.post('/api/club/{club_id}/refunds/{refund_id}/retry-provider')
def club_retry_refund_provider(club_id:int,refund_id:str):
    club_or_404(club_id)
    with conn() as c:
        rr=row(c.execute("SELECT * FROM refund_requests WHERE id=? AND kind='activity' AND club_id=?",(refund_id,club_id)))
        if not rr: raise HTTPException(404,'活动退款申请不存在')
    return _submit_refund_to_provider(refund_id)

@app.post('/api/club/{club_id}/refunds/{refund_id}/reject')
def club_reject_refund(club_id:int,refund_id:str,payload:dict=Body(default={})):
    club_or_404(club_id)
    with conn() as c:
        rr=row(c.execute("SELECT * FROM refund_requests WHERE id=? AND kind='activity' AND club_id=?",(refund_id,club_id)))
        if not rr: raise HTTPException(404,'活动退款申请不存在')
        try:return refund_lifecycle.reject(c,refund_id=refund_id,approver=f'club:{club_id}',note=str(payload.get('note') or ''))
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/public/clubs/{club_id}/mall/products')
def public_products(club_id:int):
    if IS_PROD and club_or_404(club_id)['status']!='active':raise HTTPException(404,'not found')
    return club_products(club_id)

@app.post('/api/public/clubs/{club_id}/gear-orders')
def create_gear_order(club_id:int,payload:dict=Body(...)):
    if IS_PROD: raise HTTPException(403,'demo-only endpoint disabled')
    user_id=int(payload.get('userId',1));items=payload.get('items',[])
    if not items:raise HTTPException(400,'items required')
    with conn() as c:
        total=0;commission=0;resolved=[]
        for it in items:
            p=row(c.execute('SELECT * FROM products WHERE id=? AND status="active"',(int(it['productId']),)))
            if not p:raise HTTPException(400,'商品不可售')
            q=max(1,int(it.get('quantity',1)))
            if p['stock']<q:raise HTTPException(400,f"{p['name']} 库存不足")
            total+=p['price']*q;commission+=p['price']*q*p['commission_rate'];resolved.append((p,q))
        c.execute('INSERT INTO gear_orders(user_id,source_club_id,total,cash_paid,platform_point_subsidy,status,club_commission) VALUES(?,?,?,?,?,?,?)',(user_id,club_id,total,total,0,'paid',commission));oid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        for p,q in resolved:
            c.execute('INSERT INTO gear_order_items(order_id,product_id,quantity,unit_price,unit_cost_snapshot) VALUES(?,?,?,?,?)',(oid,p['id'],q,p['price'],float(p.get('average_cost') or 0)));inventory_engine.sale_outbound(c,product_id=int(p['id']),quantity=q,order_id=oid,actor_type='legacy_direct_order')
        c.execute('INSERT INTO commission_ledger(club_id,order_id,amount,status) VALUES(?,?,?,?)',(club_id,oid,commission,'pending'))
        pts=points_engine.earn_gear_points(c,user_id=user_id,gear_order_id=oid,cash_paid=total)
        reward=int(total/1000*int(setting('mall_ai_reward_per_1000_gmv',20)))
        if reward>0:
            ai_credit_engine.grant(c,club_id=club_id,amount=reward,ledger_type='mall_reward',source_type='gear_order',source_id=str(oid),note='装备商城销售奖励')
    return {'ok':True,'orderId':oid,'total':total,'gearPointsEarned':pts,'clubCommission':round(commission,2),'clubAIReward':reward}

@app.get('/api/public/clubs/{club_id}/vouchers')
def public_vouchers(club_id:int,user_id:int=1,kind:str='activity'):
    club_or_404(club_id)
    with conn() as c:
        try:return benefit_engine.eligible_vouchers(c,user_id=user_id,club_id=club_id,kind=kind)
        except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/public/clubs/{club_id}/member-center')
def public_member_center(club_id:int,user_id:int=1):
    club_or_404(club_id)
    with conn() as c:
        membership_engine.refresh_member(c,club_id=club_id,user_id=user_id)
        member=row(c.execute('''SELECT m.*,u.name,u.phone FROM club_members m JOIN users u ON u.id=m.user_id
                               WHERE m.club_id=? AND m.user_id=?''',(club_id,user_id)))
        wallet=wallet_snapshot(c,user_id,club_id)
        benefits=benefit_engine.list_for_club(c,club_id=club_id,include_inactive=False)
        redemptions=benefit_engine.user_redemptions(c,user_id=user_id,club_id=club_id)
        tiers=membership_engine.list_tiers(c,club_id)
    return {'member':member,'wallet':wallet,'benefits':benefits,'redemptions':redemptions,'tiers':tiers}

@app.post('/api/public/clubs/{club_id}/benefits/{benefit_id}/redeem')
def public_redeem_benefit(club_id:int,benefit_id:int,payload:dict=Body(...)):
    user_id=int(payload.get('userId',1))
    with conn() as c:
        try:
            result=benefit_engine.redeem(c,benefit_id=benefit_id,user_id=user_id,display_club_id=club_id)
            return {'ok':True,**result,'wallet':wallet_snapshot(c,user_id,club_id)}
        except LookupError as e: raise HTTPException(404,str(e))
        except OverflowError as e: raise HTTPException(409,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/public/users/{user_id}/wallet')
def wallet(user_id:int,club_id:int=1):
    with conn() as c:return wallet_snapshot(c,user_id,club_id)

def _refund_progress(kind:str,status:str|None):
    st=str(status or 'none')
    if st in ('requested','partial_requested'): return {'step':'requested','label':'退款申请已提交','detail':'等待俱乐部审核' if kind=='activity' else '等待 ClubOS 平台审核'}
    if st in ('processing','partial_processing'): return {'step':'processing','label':'退款处理中','detail':'已审核通过，等待支付渠道退款'}
    if st=='partial': return {'step':'partial','label':'已完成部分退款','detail':'订单仍有其他参加人有效'}
    if st=='succeeded': return {'step':'succeeded','label':'退款完成','detail':'资金与相关积分/福利已按规则处理'}
    if st=='rejected': return {'step':'rejected','label':'退款未通过','detail':'可查看审核说明'}
    return {'step':'none','label':'无退款申请','detail':''}

@app.get('/api/public/users/{user_id}/order-center')
def user_order_center(user_id:int,club_id:int=1):
    with conn() as c:
        activity_orders=rows(c.execute('''SELECT r.*,a.title activity_title,a.location,o.label occurrence_label,o.start_at,o.end_at,
            rr.status request_status,rr.reason refund_reason,rr.decision_note,rr.cash_amount requested_refund_cash,
            rr.original_cash_amount,rr.refund_percent request_refund_percent,rr.retained_cash_amount request_retained_cash,
            rr.policy_label,rr.created_at refund_requested_at,rr.updated_at refund_updated_at
            FROM registrations r JOIN activities a ON a.id=r.activity_id
            LEFT JOIN activity_occurrences o ON o.id=r.occurrence_id
            LEFT JOIN refund_requests rr ON rr.id=r.refund_request_id
            WHERE r.user_id=? AND r.club_id=? ORDER BY r.created_at DESC,r.id DESC''',(user_id,club_id)))
        gear_orders=rows(c.execute('''SELECT g.*,cl.name source_club,rr.status request_status,rr.reason refund_reason,
            rr.decision_note,rr.cash_amount requested_refund_cash,rr.created_at refund_requested_at,rr.updated_at refund_updated_at
            FROM gear_orders g JOIN clubs cl ON cl.id=g.source_club_id
            LEFT JOIN refund_requests rr ON rr.id=g.refund_request_id
            WHERE g.user_id=? ORDER BY g.created_at DESC,g.id DESC''',(user_id,)))
        for x in activity_orders:
            ps=participant_service.summary_for_registration(c,int(x['id']))
            x.update(ps)
            a=row(c.execute('SELECT * FROM activities WHERE id=?',(x['activity_id'],)))
            occ=row(c.execute('SELECT * FROM activity_occurrences WHERE id=?',(x['occurrence_id'],))) if x.get('occurrence_id') else None
            # Full-order refund is available only before any participant-level refund begins.
            partial_exists=any(str(p.get('refund_status') or 'none') in ('requested','processing','succeeded') or p.get('status')=='refunded' for p in x.get('participants',[]))
            if x['status']=='paid' and str(x.get('refund_status') or 'none') in ('none','rejected') and not partial_exists:
                x['refundQuote']=activity_refund_policy.evaluate(activity=a,occurrence=occ,cash_paid=float(x.get('amount') or 0)) if a else None
            else: x['refundQuote']=None
            for p in x.get('participants',[]):
                if x['status']=='paid' and p.get('status')=='active' and str(p.get('refund_status') or 'none') not in ('requested','processing') and a:
                    alloc_cash=float(p.get('allocated_cash_paid') or 0)
                    pq=activity_refund_policy.evaluate(activity=a,occurrence=occ,cash_paid=alloc_cash)
                    pq['participantId']=p.get('id'); pq['participantName']=p.get('name')
                    p['refundQuote']=pq
                else: p['refundQuote']=None
                p['refundProgress']=_refund_progress('activity',p.get('refund_status'))
            x['refundProgress']=_refund_progress('activity',x.get('refund_status'))
            x['orderKind']='activity'
        for x in gear_orders:
            x['items']=rows(c.execute('''SELECT i.*,p.name product_name,p.sku FROM gear_order_items i JOIN products p ON p.id=i.product_id WHERE i.order_id=? ORDER BY i.id''',(x['id'],)))
            x['afterSalesCases']=after_sales_engine.list_for_order(c,int(x['id']))
            x['refundProgress']=_refund_progress('gear',x.get('refund_status'))
            x['orderKind']='gear'
        combined=[]
        for x in activity_orders: combined.append({'kind':'activity','createdAt':x.get('created_at'),'order':x})
        for x in gear_orders: combined.append({'kind':'gear','createdAt':x.get('created_at'),'order':x})
        combined.sort(key=lambda y:str(y.get('createdAt') or ''),reverse=True)
    return {'activityOrders':activity_orders,'gearOrders':gear_orders,'allOrders':combined}

@app.get('/api/public/users/{user_id}/orders')
def user_orders(user_id:int):
    # Backward-compatible gear-only endpoint. New C-end should use /order-center.
    with conn() as c:return rows(c.execute('SELECT id,source_club_id,total,cash_paid,status,tracking_no,carrier,after_sales_status,payment_status,refund_status,created_at FROM gear_orders WHERE user_id=? ORDER BY id DESC',(user_id,)))

@app.post('/api/public/orders/{order_id}/after-sales')
def after_sales(order_id:int,payload:dict=Body(default={})):
    user_id=int(payload.get('userId') or 0)
    if not user_id: raise HTTPException(400,'userId required')
    with conn() as c:
        try:return after_sales_engine.create(c,order_id=order_id,user_id=user_id,case_type=str(payload.get('type') or 'refund_only'),
            items=payload.get('items') or [],reason=str(payload.get('reason') or ''),evidence_urls=payload.get('evidenceUrls') or [],note=str(payload.get('note') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/public/users/{user_id}/after-sales')
def public_user_after_sales(user_id:int):
    with conn() as c:return after_sales_engine.list_for_user(c,user_id)

@app.get('/api/public/after-sales/{case_id}')
def public_after_sales_detail(case_id:str):
    with conn() as c:
        try:return after_sales_engine.get(c,case_id)
        except LookupError as e: raise HTTPException(404,str(e))

@app.post('/api/public/after-sales/{case_id}/return-shipment')
def public_after_sales_return(case_id:str,payload:dict=Body(...)):
    with conn() as c:
        try:return after_sales_engine.submit_return(c,case_id=case_id,carrier=str(payload.get('carrier') or ''),tracking_no=str(payload.get('trackingNo') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/public/orders/{order_id}/refund-request')
def request_gear_refund(order_id:int,payload:dict=Body(default={})):
    with conn() as c:
        try:return refund_lifecycle.request_gear(c,order_id=order_id,requester='c_end_user',reason=str(payload.get('reason') or '用户申请装备退款'))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

# PLATFORM POINT SUBSIDY POLICY: the platform pays Gear Points used on activities,
# so it owns the global permission gate. Clubs still decide per activity whether to accept it.
@app.get('/api/platform/points-policy')
def platform_points_policy():
    enabled=str(setting('gear_points_activity_redeem_enabled','1')).strip().lower() in {'1','true','yes','on'}
    return {'gearPointsActivityRedeemEnabled':enabled}

@app.patch('/api/platform/points-policy')
def update_platform_points_policy(payload:dict=Body(...)):
    enabled=bool(payload.get('gearPointsActivityRedeemEnabled',True))
    with conn() as c:
        c.execute('INSERT INTO platform_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  ('gear_points_activity_redeem_enabled','1' if enabled else '0'))
    return {'gearPointsActivityRedeemEnabled':enabled}

@app.post('/api/public/club-applications')
def public_club_application(payload:dict=Body(...)):
    name=str(payload.get('name') or '').strip()
    if not name: raise HTTPException(400,'俱乐部名称必填')
    with conn() as c:
        c.execute('''INSERT INTO clubs(name,status,plan,contact_name,contact_phone,city,application_note,business_license_ref)
                     VALUES(?,?,?,?,?,?,?,?)''',(name,'pending','starter',payload.get('contactName'),payload.get('contactPhone'),payload.get('city'),payload.get('applicationNote'),payload.get('businessLicenseRef')))
        cid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0]);ai_credit_engine.ensure_account(c,cid)
    return {'applicationId':cid,'status':'pending'}

@app.get('/api/public/club-applications/{application_id}')
def public_club_application_status(application_id:int):
    with conn() as c:
        r=row(c.execute('SELECT id,name,status,city,created_at,reviewed_at,disabled_reason FROM clubs WHERE id=?',(application_id,)))
    if not r: raise HTTPException(404,'入驻申请不存在')
    return r

# PLATFORM ADMIN: onboarding + AI credits + full gear mall only.
@app.get('/api/platform/dashboard')
def platform_dashboard():
    with conn() as c:
        ai=ai_credit_engine.platform_summary(c)
        return {'clubs':c.execute('SELECT COUNT(*) FROM clubs').fetchone()[0],
                'activeClubs':c.execute('SELECT COUNT(*) FROM clubs WHERE status="active"').fetchone()[0],
                'pendingClubs':c.execute('SELECT COUNT(*) FROM clubs WHERE status="pending"').fetchone()[0],
                'aiCreditBalance':c.execute('SELECT COALESCE(SUM(balance),0) FROM ai_credit_accounts').fetchone()[0],
                'gearGMV':c.execute('SELECT COALESCE(SUM(total),0) FROM gear_orders').fetchone()[0],
                'orders':c.execute('SELECT COUNT(*) FROM gear_orders').fetchone()[0], 'aiBilling':ai}

@app.get('/api/platform/clubs')
def platform_clubs():
    with conn() as c:
        return rows(c.execute('''SELECT cl.*,COALESCE(ac.balance,0) ai_credits,COALESCE(ac.monthly_quota,0) monthly_quota,
          s.plan_code subscription_plan,s.status subscription_status,s.current_period_end,
          COALESCE((SELECT SUM(d.amount-d.resolved_amount) FROM ai_credit_adjustment_debt d WHERE d.club_id=cl.id AND d.resolved=0),0) ai_credit_debt
          FROM clubs cl LEFT JOIN ai_credit_accounts ac ON ac.club_id=cl.id
          LEFT JOIN club_ai_subscriptions s ON s.club_id=cl.id ORDER BY cl.id DESC'''))

@app.post('/api/platform/clubs')
def platform_add_club(payload:dict=Body(...)):
    name=str(payload.get('name') or '').strip()
    if not name: raise HTTPException(400,'俱乐部名称必填')
    with conn() as c:
        c.execute('''INSERT INTO clubs(name,status,plan,contact_name,contact_phone,city,application_note,business_license_ref)
                     VALUES(?,?,?,?,?,?,?,?)''',(name,'pending',payload.get('plan','pro'),payload.get('contactName'),payload.get('contactPhone'),payload.get('city'),payload.get('applicationNote'),payload.get('businessLicenseRef')))
        cid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0]);ai_credit_engine.ensure_account(c,cid)
    return {'id':cid,'status':'pending'}

@app.patch('/api/platform/clubs/{club_id}/status')
def platform_club_status(club_id:int,payload:dict=Body(...)):
    status=str(payload.get('status') or '')
    if status not in {'pending','active','disabled','rejected'}: raise HTTPException(400,'status 不合法')
    with conn() as c:
        if not c.execute('SELECT 1 FROM clubs WHERE id=?',(club_id,)).fetchone(): raise HTTPException(404,'俱乐部不存在')
        reason=str(payload.get('reason') or '')
        c.execute('''UPDATE clubs SET status=?,disabled_reason=?,reviewed_at=CASE WHEN ? IN ('active','rejected') THEN CURRENT_TIMESTAMP ELSE reviewed_at END,
                     reviewed_by=CASE WHEN ? IN ('active','rejected') THEN 'platform' ELSE reviewed_by END WHERE id=?''',(status,reason,status,status,club_id))
        if status=='active': c.execute("UPDATE club_ai_subscriptions SET status='active',updated_at=CURRENT_TIMESTAMP WHERE club_id=? AND status='paused'",(club_id,))
        elif status in {'disabled','rejected'}: c.execute("UPDATE club_ai_subscriptions SET status='paused',updated_at=CURRENT_TIMESTAMP WHERE club_id=? AND status='active'",(club_id,))
    return {'ok':True,'status':status}

@app.patch('/api/platform/clubs/{club_id}/plan')
def platform_club_plan(club_id:int,payload:dict=Body(...)):
    with conn() as c:
        try:return ai_credit_engine.assign_plan(c,club_id=club_id,plan_code=str(payload.get('planCode') or ''),auto_renew=bool(payload.get('autoRenew',True)))
        except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/platform/credits')
def platform_credits():
    with conn() as c:
        base=rows(c.execute('''SELECT a.*,cl.name,cl.status,s.plan_code,s.status subscription_status,s.current_period_end,
           COALESCE((SELECT SUM(d.amount-d.resolved_amount) FROM ai_credit_adjustment_debt d WHERE d.club_id=a.club_id AND d.resolved=0),0) debt
           FROM ai_credit_accounts a JOIN clubs cl ON cl.id=a.club_id LEFT JOIN club_ai_subscriptions s ON s.club_id=a.club_id ORDER BY cl.id'''))
        return base

@app.post('/api/platform/credits/adjust')
def platform_credit_adjust(payload:dict=Body(...)):
    cid=int(payload['clubId']);amount=int(payload['amount']);note=payload.get('note','平台调整')
    with conn() as c:return ai_credit_engine.adjust(c,club_id=cid,amount=amount,note=note)

@app.get('/api/platform/ai-credit/plans')
def platform_ai_credit_plans():
    with conn() as c:return ai_credit_engine.plans(c,include_inactive=True)

@app.post('/api/platform/ai-credit/plans')
def platform_ai_credit_plan_upsert(payload:dict=Body(...)):
    with conn() as c:
        try:return ai_credit_engine.upsert_plan(c,payload)
        except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/platform/ai-credit/topup-packages')
def platform_ai_credit_topup_packages():
    with conn() as c:return ai_credit_engine.topup_packages(c,include_inactive=True)

@app.post('/api/platform/ai-credit/topup-packages')
def platform_ai_credit_topup_package_upsert(payload:dict=Body(...)):
    with conn() as c:
        try:return ai_credit_engine.upsert_topup_package(c,payload)
        except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/platform/ai-credit/orders')
def platform_ai_credit_orders(limit:int=200):
    with conn() as c:return ai_credit_engine.orders(c,limit=limit)

@app.post('/api/platform/ai-credit/orders/{order_id}/confirm-paid')
def platform_ai_credit_confirm_paid(order_id:str,payload:dict=Body(...)):
    with conn() as c:
        try:return ai_credit_engine.confirm_order_paid(c,order_id=order_id,payment_ref=str(payload.get('paymentRef') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))

@app.post('/api/platform/ai-credit/monthly-roll')
def platform_ai_credit_monthly_roll(payload:dict=Body(default={})):
    with conn() as c:return {'orders':ai_credit_engine.monthly_roll(c,period_key=payload.get('period'))}

@app.get('/api/platform/ai-credit/summary')
def platform_ai_credit_summary(period:str|None=None):
    with conn() as c:return ai_credit_engine.platform_summary(c,period_key=period)

@app.get('/api/platform/ai-credit/task-pricing')
def platform_ai_credit_task_pricing():
    with conn() as c:return ai_credit_engine.task_pricing(c)

@app.patch('/api/platform/ai-credit/task-pricing')
def platform_ai_credit_task_pricing_update(payload:dict=Body(...)):
    with conn() as c:
        try:return ai_credit_engine.update_task_pricing(c,payload)
        except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/platform/ai-credit/clubs/{club_id}/statement')
def platform_ai_credit_club_statement(club_id:int,period:str|None=None):
    with conn() as c:return ai_credit_engine.statement(c,club_id=club_id,period_key=period)
@app.get('/api/platform/ai/status')
def platform_ai_status():return gateway_status()
@app.get('/api/platform/ai/usage')
def platform_ai_usage():
    with conn() as c:return rows(c.execute('SELECT u.*,cl.name club_name FROM ai_usage_records u JOIN clubs cl ON cl.id=u.club_id ORDER BY u.id DESC LIMIT 300'))

# 模型接入只允许总平台维护：平台配置 -> 折算 AI Credits -> 各端俱乐部按任务消耗。
@app.get('/api/platform/ai/providers')
def platform_ai_providers():return platform_provider_config()

@app.patch('/api/platform/ai/providers')
def platform_ai_providers_update(payload:dict=Body(...)):
    try:return update_platform_provider_config(payload)
    except ValueError as e: raise HTTPException(400,str(e))

@app.post('/api/platform/ai/providers/test')
async def platform_ai_providers_test(payload:dict=Body(...)):
    return await test_provider_connection(str(payload.get('role') or 'primary'))
@app.get('/api/platform/benefits')
def platform_benefits():
    with conn() as c:return rows(c.execute("SELECT * FROM member_benefits WHERE owner_type='PLATFORM' ORDER BY id DESC"))

@app.post('/api/platform/benefits')
def platform_add_benefit(payload:dict=Body(...)):
    with conn() as c:
        try: bid=benefit_engine.create_platform_benefit(c,payload=payload)
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True,'benefitId':bid}

@app.patch('/api/platform/benefits/{benefit_id}')
def platform_update_benefit(benefit_id:int,payload:dict=Body(...)):
    allowed=['title','description','points_cost','cash_value','stock','status','target_club_id','benefit_type'];sets=[];vals=[]
    for k in allowed:
        if k in payload: sets.append(f'{k}=?'); vals.append(payload[k])
    with conn() as c:
        b=row(c.execute("SELECT * FROM member_benefits WHERE id=? AND owner_type='PLATFORM'",(benefit_id,)))
        if not b: raise HTTPException(404,'平台福利不存在')
        if sets:
            vals.append(benefit_id); c.execute(f"UPDATE member_benefits SET {', '.join(sets)},updated_at=CURRENT_TIMESTAMP WHERE id=?",vals)
    return {'ok':True}

@app.get('/api/platform/commerce/status')
def platform_commerce_status():
    st=commerce_status()
    return {'provider':st.provider,'configured':st.configured,'reachable':st.reachable,'url':st.url,'note':st.note}

@app.get('/api/platform/products')
def platform_products():
    with conn() as c:return rows(c.execute('SELECT * FROM products ORDER BY id DESC'))


def _sync_product_to_medusa(product_id:int, *, create_if_missing:bool=True):
    if commerce_provider()!='medusa':
        return {'ok':False,'skipped':True,'reason':'COMMERCE_PROVIDER is not medusa'}
    with conn() as c:
        p=row(c.execute('SELECT * FROM products WHERE id=?',(product_id,)))
    if not p: raise HTTPException(404,'商品不存在')
    client=MedusaClient()
    try:
        if p.get('commerce_product_id') and p.get('commerce_variant_id'):
            client.update_platform_product(product_id=str(p['commerce_product_id']),variant_id=str(p['commerce_variant_id']),
                                           name=p['name'],price=float(p['price']),status=p['status'],image_url=p.get('image_url'))
            inv=p.get('commerce_inventory_item_id')
            if not inv:
                full=client.get_admin_product(str(p['commerce_product_id']))
                inv=client._inventory_item_id(full,str(p['commerce_variant_id']))
            if inv:
                client.set_inventory(inventory_item_id=str(inv),stock=int(p['stock']))
            result={'product_id':p['commerce_product_id'],'variant_id':p['commerce_variant_id'],'inventory_item_id':inv}
        else:
            if not create_if_missing: raise RuntimeError('商品尚未绑定 Medusa')
            result=client.create_platform_product(name=p['name'],sku=p['sku'],price=float(p['price']),stock=int(p['stock']),
                                                  status=p['status'],image_url=p.get('image_url'),category=p.get('category'))
        with conn() as c:
            c.execute("UPDATE products SET commerce_product_id=?,commerce_variant_id=?,commerce_inventory_item_id=?,commerce_sync_status='synced',commerce_sync_error=NULL,last_commerce_sync_at=CURRENT_TIMESTAMP WHERE id=?",
                      (result.get('product_id'),result.get('variant_id'),result.get('inventory_item_id'),product_id))
        return {'ok':True,'productId':product_id,'commerceProductId':result.get('product_id'),'commerceVariantId':result.get('variant_id'),
                'commerceInventoryItemId':result.get('inventory_item_id'),'syncStatus':'synced'}
    except Exception as e:
        with conn() as c:
            c.execute("UPDATE products SET commerce_sync_status='error',commerce_sync_error=?,last_commerce_sync_at=CURRENT_TIMESTAMP WHERE id=?",(str(e),product_id))
        raise HTTPException(502,f'Medusa商品同步失败: {e}')

def _safe_sync_inventory_products(product_ids:list[int]|set[int]):
    ids=sorted({int(x) for x in product_ids if x})
    if not ids:return []
    if commerce_provider()!='medusa':return [{'productId':x,'status':'local'} for x in ids]
    out=[]
    for pid in ids:
        try:
            r=_sync_product_to_medusa(pid,create_if_missing=False);out.append({'productId':pid,'status':'synced','result':r})
        except HTTPException as e:
            out.append({'productId':pid,'status':'error','error':str(e.detail)})
        except Exception as e:
            out.append({'productId':pid,'status':'error','error':str(e)})
    return out

@app.post('/api/platform/products')
def platform_add_product(payload:dict=Body(...)):
    initial_stock=max(0,int(payload.get('stock',0) or 0))
    with conn() as c:
        c.execute("INSERT INTO products(name,sku,price,stock,status,image_url,category,commission_rate,reorder_point,commerce_sync_status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (payload['name'],payload['sku'],payload['price'],0,payload.get('status','active'),payload.get('image_url'),payload.get('category'),payload.get('commission_rate',0.08),max(0,int(payload.get('reorder_point',payload.get('reorderPoint',5)) or 0)),'pending' if commerce_provider()=='medusa' else 'local'))
        pid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        if initial_stock:
            inventory_engine.adjust_stock(c,product_id=pid,quantity_delta=initial_stock,reason='新商品期初库存',actor_type='platform',reference_type='product_opening',reference_id=str(pid))
    out={'id':pid}
    if commerce_provider()=='medusa':
        out['commerce']=_sync_product_to_medusa(pid)
    return out

@app.post('/api/platform/products/{product_id}/commerce-link')
def platform_link_product(product_id:int,payload:dict=Body(...)):
    product_id_ext=payload.get('commerceProductId'); variant_id=payload.get('commerceVariantId'); inventory_id=payload.get('commerceInventoryItemId')
    if not variant_id: raise HTTPException(400,'commerceVariantId required')
    with conn() as c:
        p=row(c.execute('SELECT id FROM products WHERE id=?',(product_id,)))
        if not p: raise HTTPException(404,'商品不存在')
        c.execute("UPDATE products SET commerce_product_id=?,commerce_variant_id=?,commerce_inventory_item_id=?,commerce_sync_status='linked',commerce_sync_error=NULL,last_commerce_sync_at=CURRENT_TIMESTAMP WHERE id=?",
                  (product_id_ext,variant_id,inventory_id,product_id))
    return {'ok':True,'productId':product_id,'commerceProductId':product_id_ext,'commerceVariantId':variant_id,'commerceInventoryItemId':inventory_id}

@app.post('/api/platform/products/{product_id}/sync')
def platform_sync_product(product_id:int):
    return _sync_product_to_medusa(product_id)

@app.patch('/api/platform/products/{product_id}')
def platform_update_product(product_id:int,payload:dict=Body(...)):
    allowed=['name','price','status','image_url','category','commission_rate','reorder_point'];sets=[];vals=[]
    if 'reorderPoint' in payload and 'reorder_point' not in payload: payload={**payload,'reorder_point':payload.get('reorderPoint')}
    with conn() as c:
        p=row(c.execute('SELECT * FROM products WHERE id=?',(product_id,)))
        if not p: raise HTTPException(404,'商品不存在')
        if 'stock' in payload:
            try: inventory_engine.set_absolute_stock(c,product_id=product_id,new_stock=max(0,int(payload.get('stock') or 0)),reason=str(payload.get('stockReason') or '平台手工修改库存'))
            except ValueError as e: raise HTTPException(409,str(e))
        for k in allowed:
            if k in payload:sets.append(f'{k}=?');vals.append(payload[k])
        if sets:
            sync='pending' if commerce_provider()=='medusa' else p.get('commerce_sync_status','local')
            c.execute(f'UPDATE products SET {", ".join(sets)},commerce_sync_status=? WHERE id=?',vals+[sync,product_id])
    if commerce_provider()=='medusa': return {'ok':True,'commerce':_sync_product_to_medusa(product_id)}
    return {'ok':True}


@app.get('/api/platform/orders')
def platform_orders():
    with conn() as c:return rows(c.execute('SELECT o.*,u.name buyer,cl.name source_club FROM gear_orders o JOIN users u ON u.id=o.user_id JOIN clubs cl ON cl.id=o.source_club_id ORDER BY o.id DESC'))

@app.patch('/api/platform/orders/{order_id}')
def platform_update_order(order_id:int,payload:dict=Body(...)):
    allowed=['status','tracking_no','carrier','after_sales_status'];sets=[];vals=[]
    for k in allowed:
        if k in payload:sets.append(f'{k}=?');vals.append(payload[k])
    commission=None
    if sets:
        vals.append(order_id)
        with conn() as c:
            existing=row(c.execute('SELECT id FROM gear_orders WHERE id=?',(order_id,)))
            if not existing: raise HTTPException(404,'装备订单不存在')
            c.execute(f'UPDATE gear_orders SET {", ".join(sets)} WHERE id=?',vals)
            if payload.get('status')=='delivered':
                c.execute('UPDATE gear_orders SET delivered_at=COALESCE(delivered_at,CURRENT_TIMESTAMP) WHERE id=?',(order_id,))
                commission=commission_engine.freeze_order(c,order_id=order_id)
    return {'ok':True,'commission':commission}


def _medusa_fulfillment_items(local_order:dict, medusa_order:dict):
    with conn() as c:
        local_items=rows(c.execute('SELECT * FROM gear_order_items WHERE order_id=? ORDER BY id',(local_order['id'],)))
    by_product={str(x['product_id']):int(x['quantity']) for x in local_items}
    out=[]
    for item in (medusa_order or {}).get('items') or []:
        md=item.get('metadata') or {}; pid=str(md.get('clubos_product_id') or '')
        if pid and pid in by_product:
            out.append({'id':item['id'],'quantity':by_product[pid]})
    if not out and len(local_items)==len((medusa_order or {}).get('items') or []):
        out=[{'id':mi['id'],'quantity':int(li['quantity'])} for li,mi in zip(local_items,medusa_order.get('items') or [])]
    if not out: raise HTTPException(409,'无法把 ClubOS 装备明细映射到 Medusa Order items')
    return out

@app.get('/api/platform/warehouse/summary')
def platform_warehouse_summary():
    with conn() as c:return warehouse_engine.summary(c)

@app.get('/api/platform/warehouses')
def platform_warehouses():
    with conn() as c:return warehouse_engine.list_warehouses(c)

@app.post('/api/platform/warehouses')
def platform_create_warehouse(payload:dict=Body(...)):
    with conn() as c:
        try:return warehouse_engine.create_warehouse(c,payload)
        except ValueError as e:raise HTTPException(409,str(e))

@app.post('/api/platform/warehouses/{warehouse_id}/locations')
def platform_create_location(warehouse_id:int,payload:dict=Body(...)):
    with conn() as c:
        try:return warehouse_engine.create_location(c,warehouse_id,payload)
        except LookupError as e:raise HTTPException(404,str(e))
        except ValueError as e:raise HTTPException(409,str(e))

@app.get('/api/platform/warehouse/inventory')
def platform_warehouse_inventory(warehouse_id:int|None=None,product_id:int|None=None):
    with conn() as c:return warehouse_engine.inventory(c,warehouse_id=warehouse_id,product_id=product_id)

@app.post('/api/platform/warehouse/move')
def platform_warehouse_move(payload:dict=Body(...)):
    with conn() as c:
        try:return warehouse_engine.move_stock(c,product_id=int(payload.get('productId') or 0),from_location_id=int(payload.get('fromLocationId') or 0),to_location_id=int(payload.get('toLocationId') or 0),quantity=int(payload.get('quantity') or 0),note=str(payload.get('note') or '仓内移库'))
        except ValueError as e:raise HTTPException(409,str(e))

@app.post('/api/platform/warehouse/count')
def platform_warehouse_count(payload:dict=Body(...)):
    with conn() as c:
        try:out=warehouse_engine.count_location(c,product_id=int(payload.get('productId') or 0),location_id=int(payload.get('locationId') or 0),counted_on_hand=int(payload.get('countedOnHand') or 0),note=str(payload.get('note') or '库位盘点'))
        except ValueError as e:raise HTTPException(409,str(e))
    out['commerceSync']=_safe_sync_inventory_products([int(payload.get('productId') or 0)])
    return out

@app.get('/api/platform/warehouse/tasks')
def platform_warehouse_tasks(status:str|None=None):
    with conn() as c:return warehouse_engine.tasks(c,status=status)

@app.post('/api/platform/orders/{order_id}/pick')
def platform_order_pick(order_id:int):
    with conn() as c:
        try:return warehouse_engine.create_pick_task(c,order_id=order_id)
        except ValueError as e:raise HTTPException(409,str(e))

@app.post('/api/platform/warehouse/tasks/{task_id}/picked')
def platform_mark_picked(task_id:str):
    with conn() as c:
        try:return warehouse_engine.mark_picked(c,task_id)
        except LookupError as e:raise HTTPException(404,str(e))

@app.post('/api/platform/orders/{order_id}/pack')
def platform_order_pack(order_id:int,payload:dict=Body(default={})):
    with conn() as c:
        try:return warehouse_engine.pack_order(c,order_id=order_id,note=str(payload.get('note') or ''))
        except ValueError as e:raise HTTPException(409,str(e))

def _ensure_wms_order_reservation(c,order_id:int):
    items=rows(c.execute('SELECT product_id,quantity FROM gear_order_items WHERE order_id=?',(order_id,)))
    for it in items:
        r=c.execute("SELECT 1 FROM warehouse_reservations WHERE order_id=? AND product_id=? AND status IN ('reserved','dispatched')",(order_id,it['product_id'])).fetchone()
        if not r:
            inventory_engine.sale_outbound(c,product_id=int(it['product_id']),quantity=int(it['quantity']),order_id=order_id,actor_type='wms_compat')

@app.post('/api/platform/orders/{order_id}/fulfill')
def platform_fulfill_order(order_id:int):
    if commerce_provider()!='medusa': raise HTTPException(409,'当前未启用 Medusa')
    with conn() as c: o=row(c.execute('SELECT * FROM gear_orders WHERE id=?',(order_id,)))
    if not o: raise HTTPException(404,'装备订单不存在')
    if not o.get('commerce_order_id'): raise HTTPException(409,'订单尚未关联 Medusa Order')
    with conn() as c:
        try:_ensure_wms_order_reservation(c,order_id)
        except ValueError as e:raise HTTPException(409,str(e))
    client=MedusaClient(); mo=client.get_admin_order(str(o['commerce_order_id']))
    items=_medusa_fulfillment_items(o,mo)
    try: result=client.create_fulfillment(str(o['commerce_order_id']),items)
    except Exception as e: raise HTTPException(502,f'Medusa履约创建失败: {e}')
    f=result.get('fulfillment') or {}; fid=f.get('id')
    if not fid:
        fresh=client.get_admin_order(str(o['commerce_order_id'])) or {}; fs=fresh.get('fulfillments') or []; fid=(fs[-1] or {}).get('id') if fs else None
    with conn() as c:
        c.execute("UPDATE gear_orders SET commerce_fulfillment_id=?,commerce_sync_status='fulfilled',status='processing' WHERE id=?",(fid,order_id))
        try: pick=warehouse_engine.create_pick_task(c,order_id=order_id)
        except Exception: pick=None
    return {'ok':True,'orderId':order_id,'commerceOrderId':o['commerce_order_id'],'fulfillmentId':fid,'pickTask':pick}

@app.post('/api/platform/orders/{order_id}/ship')
def platform_ship_order(order_id:int,payload:dict=Body(...)):
    if commerce_provider()!='medusa': raise HTTPException(409,'当前未启用 Medusa')
    tracking=str(payload.get('trackingNo') or '').strip(); carrier=str(payload.get('carrier') or '').strip()
    if not tracking: raise HTTPException(400,'trackingNo required')
    with conn() as c:o=row(c.execute('SELECT * FROM gear_orders WHERE id=?',(order_id,)))
    if not o: raise HTTPException(404,'装备订单不存在')
    if not o.get('commerce_order_id') or not o.get('commerce_fulfillment_id'): raise HTTPException(409,'请先创建 Medusa Fulfillment')
    with conn() as c:
        try:
            warehouse_engine.validate_dispatch(c,order_id)
            warehouse_engine.pack_order(c,order_id=order_id,note='发货前自动完成拣货/打包')
        except ValueError as e: raise HTTPException(409,str(e))
    client=MedusaClient(); mo=client.get_admin_order(str(o['commerce_order_id'])); items=_medusa_fulfillment_items(o,mo)
    try: client.create_shipment(str(o['commerce_order_id']),str(o['commerce_fulfillment_id']),items,tracking_number=tracking,tracking_url=payload.get('trackingUrl'),label_url=payload.get('labelUrl'))
    except Exception as e: raise HTTPException(502,f'Medusa发货失败: {e}')
    with conn() as c:
        warehouse_engine.dispatch_order(c,order_id=order_id,actor_type='platform')
        c.execute("UPDATE gear_orders SET tracking_no=?,carrier=?,status='shipped',commerce_sync_status='shipped',shipped_at=CURRENT_TIMESTAMP WHERE id=?",(tracking,carrier,order_id))
    return {'ok':True,'orderId':order_id,'status':'shipped','trackingNo':tracking,'carrier':carrier}

@app.post('/api/platform/orders/{order_id}/delivered')
def platform_mark_delivered(order_id:int):
    if commerce_provider()!='medusa': raise HTTPException(409,'当前未启用 Medusa')
    with conn() as c:o=row(c.execute('SELECT * FROM gear_orders WHERE id=?',(order_id,)))
    if not o: raise HTTPException(404,'装备订单不存在')
    if not o.get('commerce_order_id') or not o.get('commerce_fulfillment_id'): raise HTTPException(409,'缺少 Medusa fulfillment')
    try: MedusaClient().mark_delivered(str(o['commerce_order_id']),str(o['commerce_fulfillment_id']))
    except Exception as e: raise HTTPException(502,f'Medusa签收同步失败: {e}')
    with conn() as c:
        c.execute("UPDATE gear_orders SET status='delivered',commerce_sync_status='delivered',delivered_at=CURRENT_TIMESTAMP WHERE id=?",(order_id,))
        commission=commission_engine.freeze_order(c,order_id=order_id)
    return {'ok':True,'orderId':order_id,'status':'delivered','commission':commission}

def _start_after_sales_refund(case_id:str):
    with conn() as c:
        try: case=after_sales_engine.get(c,case_id)
        except LookupError as e: raise HTTPException(404,str(e))
        if case['case_type']=='exchange': raise HTTPException(409,'换货售后不执行退款')
        if case['status'] not in ('approved_pending_refund','refund_processing'):
            raise HTTPException(409,f"当前售后状态不能发起退款: {case['status']}")
        if case.get('refund_request_id'):
            refund_id=str(case['refund_request_id'])
        else:
            order=row(c.execute('SELECT * FROM gear_orders WHERE id=?',(case['order_id'],)))
            checkout=row(c.execute("SELECT * FROM checkout_intents WHERE kind='gear' AND result_id=? ORDER BY created_at DESC LIMIT 1",(str(case['order_id']),)))
            refund_id='rfd_'+uuid.uuid4().hex[:20]
            amount=float(case.get('approved_refund_amount') or 0)
            original=float(order.get('cash_paid') or 0)
            pct=(amount/original*100.0) if original else 0.0
            c.execute('''INSERT INTO refund_requests(id,kind,checkout_intent_id,gear_order_id,refund_scope,user_id,club_id,status,reason,
                         cash_amount,original_cash_amount,refund_percent,retained_cash_amount,commerce_order_id,after_sales_case_id,requested_by)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (refund_id,'gear',checkout['id'] if checkout else None,case['order_id'],'after_sales',case['user_id'],case['source_club_id'],
                       'requested',case.get('reason') or '装备售后退款',amount,original,pct,max(0.0,original-amount),order.get('commerce_order_id'),case_id,'platform_after_sales'))
            after_sales_engine.attach_refund(c,case_id=case_id,refund_request_id=refund_id)
        rr=row(c.execute('SELECT * FROM refund_requests WHERE id=?',(refund_id,)))
        if rr['status']=='requested': refund_lifecycle.approve(c,refund_id=refund_id,approver='platform_after_sales')
        legacy_no_checkout=not bool(rr.get('checkout_intent_id'))
    if legacy_no_checkout:
        # Compatibility for demo / pre-checkout Gear orders. Real paid checkout orders
        # must always refund through the bound payment provider.
        if IS_PROD:raise HTTPException(403,'local refund disabled')
        return _finalize_provider_refund(refund_id,provider_refund_id='local_after_sales',provider='local')
    return _submit_refund_to_provider(refund_id)

@app.get('/api/platform/after-sales')
def platform_after_sales(status:str|None=None):
    with conn() as c:return after_sales_engine.list_platform(c,status)

@app.get('/api/platform/after-sales/{case_id}')
def platform_after_sales_detail(case_id:str):
    with conn() as c:
        try:return after_sales_engine.get(c,case_id)
        except LookupError as e: raise HTTPException(404,str(e))

@app.post('/api/platform/after-sales/{case_id}/approve')
def platform_after_sales_approve(case_id:str,payload:dict=Body(default={})):
    with conn() as c:
        try:return after_sales_engine.approve(c,case_id=case_id,reviewer='platform',approved_refund_amount=payload.get('refundAmount'),note=str(payload.get('note') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/platform/after-sales/{case_id}/reject')
def platform_after_sales_reject(case_id:str,payload:dict=Body(default={})):
    with conn() as c:
        try:return after_sales_engine.reject(c,case_id=case_id,reviewer='platform',note=str(payload.get('note') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/platform/after-sales/{case_id}/receive-return')
def platform_after_sales_receive(case_id:str,payload:dict=Body(default={})):
    with conn() as c:
        try: out=after_sales_engine.receive_return(c,case_id=case_id,restock=bool(payload.get('restock',True)),note=str(payload.get('note') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))
    out['commerceSync']=_safe_sync_inventory_products(out.get('restockedProductIds') or [])
    return out

@app.post('/api/platform/after-sales/{case_id}/refund')
def platform_after_sales_refund(case_id:str):
    return _start_after_sales_refund(case_id)

@app.post('/api/platform/after-sales/{case_id}/exchange-ship')
def platform_after_sales_exchange_ship(case_id:str,payload:dict=Body(...)):
    with conn() as c:
        try:return after_sales_engine.ship_exchange(c,case_id=case_id,carrier=str(payload.get('carrier') or ''),tracking_no=str(payload.get('trackingNo') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/platform/refunds')
def platform_refunds():
    with conn() as c:return refund_lifecycle.list_all(c)

@app.post('/api/platform/refunds/{refund_id}/approve')
def platform_approve_refund(refund_id:str):
    with conn() as c:
        rr=row(c.execute("SELECT * FROM refund_requests WHERE id=? AND kind='gear'",(refund_id,)))
        if not rr: raise HTTPException(404,'装备退款申请不存在')
        try: refund_lifecycle.approve(c,refund_id=refund_id,approver='platform')
        except ValueError as e: raise HTTPException(409,str(e))
    return _submit_refund_to_provider(refund_id)

@app.post('/api/platform/refunds/{refund_id}/retry-provider')
def platform_retry_refund_provider(refund_id:str):
    with conn() as c:
        rr=row(c.execute("SELECT * FROM refund_requests WHERE id=? AND kind='gear'",(refund_id,)))
        if not rr: raise HTTPException(404,'装备退款申请不存在')
    return _submit_refund_to_provider(refund_id)

@app.post('/api/platform/refunds/{refund_id}/reject')
def platform_reject_refund(refund_id:str,payload:dict=Body(default={})):
    with conn() as c:
        rr=row(c.execute("SELECT * FROM refund_requests WHERE id=? AND kind='gear'",(refund_id,)))
        if not rr: raise HTTPException(404,'装备退款申请不存在')
        try:return refund_lifecycle.reject(c,refund_id=refund_id,approver='platform',note=str(payload.get('note') or ''))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/platform/orders/{order_id}/refund')
def platform_refund_order(order_id:int,payload:dict=Body(default={})):
    # Backward-compatible local shortcut. Production should request -> approve -> provider callback.
    with conn() as c:
        try:
            req=refund_lifecycle.request_gear(c,order_id=order_id,requester='platform_legacy',reason=str(payload.get('reason') or 'platform full refund'))
            if req.get('status')=='refunded': return {'ok':True,'orderId':order_id,'status':'refunded','idempotent':True}
            refund_lifecycle.approve(c,refund_id=req['refundRequestId'],approver='platform_legacy')
            out=refund_lifecycle.finalize(c,refund_id=req['refundRequestId'],provider_refund_id='local_legacy',provider='local')
            result=out.get('result') or {}
            return {**result,'refundRequestId':req['refundRequestId']}
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/commerce/events/refund-succeeded')
def commerce_refund_succeeded(payload:dict=Body(...), x_clubos_commerce_secret:str|None=Header(default=None)):
    expected=os.getenv('CLUBOS_COMMERCE_WEBHOOK_SECRET','').strip()
    if expected and x_clubos_commerce_secret != expected: raise HTTPException(401,'invalid commerce webhook secret')
    event_key=str(payload.get('eventKey') or payload.get('eventId') or '')
    refund_id=str(payload.get('refundRequestId') or '')
    if not event_key or not refund_id: raise HTTPException(400,'eventKey and refundRequestId required')
    with conn() as c:
        existing=row(c.execute('SELECT * FROM commerce_events WHERE event_key=?',(event_key,)))
        rr=row(c.execute('SELECT * FROM refund_requests WHERE id=?',(refund_id,)))
        if not rr: raise HTTPException(404,'退款申请不存在')
    if existing:
        # A duplicate provider callback is also a safe reconciliation opportunity if
        # the original money refund succeeded while the Medusa mirror was unavailable.
        mirror=_mirror_refund_to_commerce(refund_id)
        return {'ok':True,'idempotent':True,'eventKey':event_key,'commerceMirror':mirror}
    try:
        result=_finalize_provider_refund(
            refund_id,
            provider_refund_id=str(payload.get('providerRefundId') or ''),
            provider=str(payload.get('provider') or commerce_provider()),
        )
    except LookupError as e: raise HTTPException(404,str(e))
    except ValueError as e: raise HTTPException(409,str(e))
    with conn() as c:
        rr=row(c.execute('SELECT * FROM refund_requests WHERE id=?',(refund_id,))) or {}
        c.execute('INSERT INTO commerce_events(event_key,provider,event_type,checkout_intent_id,commerce_order_id,payload_json) VALUES(?,?,?,?,?,?)',
                  (event_key,str(payload.get('provider') or commerce_provider()),'refund.succeeded',rr.get('checkout_intent_id'),rr.get('commerce_order_id'),jdump(payload)))
    return {'ok':True,'eventKey':event_key,'result':result}


@app.post('/api/commerce/events/order-refunded')
def commerce_order_refunded(payload:dict=Body(...), x_clubos_commerce_secret:str|None=Header(default=None)):
    # Backward-compatible Medusa gear-refund event. New integrations should use refund-succeeded with refundRequestId.
    expected=os.getenv('CLUBOS_COMMERCE_WEBHOOK_SECRET','').strip()
    if expected and x_clubos_commerce_secret != expected: raise HTTPException(401,'invalid commerce webhook secret')
    event_key=str(payload.get('eventKey') or payload.get('eventId') or '')
    local_order_id=int(payload.get('localOrderId') or 0)
    if not event_key or not local_order_id: raise HTTPException(400,'eventKey and localOrderId required')
    with conn() as c:
        existing=row(c.execute('SELECT * FROM commerce_events WHERE event_key=?',(event_key,)))
        if existing:return {'ok':True,'idempotent':True,'eventKey':event_key}
        try:
            req=refund_lifecycle.request_gear(c,order_id=local_order_id,requester='commerce_event',reason='provider refund event')
            if req.get('status')!='refunded':
                refund_lifecycle.approve(c,refund_id=req['refundRequestId'],approver='commerce_event')
                result=refund_lifecycle.finalize(c,refund_id=req['refundRequestId'],provider_refund_id=str(payload.get('providerRefundId') or payload.get('orderId') or ''),provider=str(payload.get('provider') or commerce_provider()))
            else:
                result=req
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))
        c.execute('INSERT INTO commerce_events(event_key,provider,event_type,commerce_order_id,payload_json) VALUES(?,?,?,?,?)',
                  (event_key,str(payload.get('provider') or commerce_provider()),'order.refunded',str(payload.get('orderId') or ''),jdump(payload)))
    return {'ok':True,'eventKey':event_key,'result':result}

@app.get('/api/platform/commission-policy')
def platform_commission_policy():
    p=commission_engine.policy()
    return {'afterSalesDays':p.after_sales_days}

@app.patch('/api/platform/commission-policy')
def platform_update_commission_policy(payload:dict=Body(...)):
    try: days=max(0,min(90,int(payload.get('afterSalesDays'))))
    except Exception: raise HTTPException(400,'afterSalesDays must be an integer between 0 and 90')
    with conn() as c:
        c.execute('INSERT INTO platform_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('commission_after_sales_days',str(days)))
    return {'ok':True,'afterSalesDays':days}

@app.post('/api/platform/commissions/release')
def platform_release_commissions(payload:dict=Body(default={})):
    club_id=payload.get('clubId')
    with conn() as c:
        if club_id is not None and not row(c.execute('SELECT id FROM clubs WHERE id=?',(int(club_id),))): raise HTTPException(404,'俱乐部不存在')
        return commission_engine.release_matured(c,club_id=int(club_id) if club_id is not None else None)

@app.get('/api/platform/commission-summary')
def platform_commission_summary(club_id:int|None=None):
    with conn() as c:
        if club_id is not None:
            if not row(c.execute('SELECT id FROM clubs WHERE id=?',(club_id,))): raise HTTPException(404,'俱乐部不存在')
            return commission_engine.summary(c,club_id=club_id)
        clubs=rows(c.execute('SELECT id,name FROM clubs ORDER BY id'))
        return [{**commission_engine.summary(c,club_id=int(cl['id'])),'clubName':cl['name']} for cl in clubs]

@app.get('/api/platform/commissions')
def platform_commissions(club_id:int|None=None,status:str|None=None):
    with conn() as c:
        commission_engine.release_matured(c,club_id=club_id)
        sql='''SELECT l.*,cl.name club_name,o.status order_status,o.refund_status,o.delivered_at
               FROM commission_ledger l JOIN clubs cl ON cl.id=l.club_id JOIN gear_orders o ON o.id=l.order_id WHERE 1=1'''
        args=[]
        if club_id is not None: sql+=' AND l.club_id=?'; args.append(club_id)
        if status: sql+=' AND l.status=?'; args.append(status)
        sql+=' ORDER BY l.id DESC'
        return rows(c.execute(sql,args))

@app.get('/api/platform/settlements')
def platform_settlements(club_id:int|None=None):
    with conn() as c:return commission_engine.settlements(c,club_id=club_id)

@app.get('/api/platform/settlements/preview')
def platform_settlement_preview(club_id:int):
    with conn() as c:
        if not row(c.execute('SELECT id FROM clubs WHERE id=?',(club_id,))): raise HTTPException(404,'俱乐部不存在')
        return commission_engine.preview(c,club_id=club_id)

@app.post('/api/platform/settlements')
def platform_create_settlement(payload:dict=Body(...)):
    try: club_id=int(payload.get('clubId'))
    except Exception: raise HTTPException(400,'clubId required')
    with conn() as c:
        if not row(c.execute('SELECT id FROM clubs WHERE id=?',(club_id,))): raise HTTPException(404,'俱乐部不存在')
        try:
            return commission_engine.settle_available(c,club_id=club_id,payment_ref=str(payload.get('paymentRef') or ''),note=str(payload.get('note') or ''),created_by='platform')
        except ValueError as e: raise HTTPException(409,str(e))




# v0.22 · Commerce Analytics & Replenishment
@app.get('/api/platform/analytics/commerce')
def platform_commerce_analytics(window_days:int|None=None):
    with conn() as c:return analytics_engine.dashboard(c,window_days=window_days)

@app.get('/api/platform/analytics/products')
def platform_product_analytics(window_days:int|None=None):
    with conn() as c:return analytics_engine.product_analytics(c,window_days=window_days)

@app.get('/api/platform/analytics/clubs')
def platform_club_analytics(window_days:int|None=None):
    with conn() as c:return analytics_engine.club_analytics(c,window_days=window_days)

@app.get('/api/platform/analytics/suppliers')
def platform_supplier_analytics():
    with conn() as c:return analytics_engine.supplier_analytics(c)

@app.get('/api/platform/replenishment/policy')
def platform_replenishment_policy():
    with conn() as c:return analytics_engine.policy(c)

@app.patch('/api/platform/replenishment/policy')
def platform_update_replenishment_policy(payload:dict=Body(...)):
    with conn() as c:
        try:return analytics_engine.update_policy(c,payload)
        except ValueError as e:raise HTTPException(400,str(e))

@app.get('/api/platform/replenishment/recommendations')
def platform_replenishment_recommendations(window_days:int|None=None):
    with conn() as c:return analytics_engine.replenishment(c,window_days=window_days)

@app.post('/api/platform/replenishment/products/{product_id}/create-po')
def platform_create_replenishment_po(product_id:int,payload:dict=Body(default={})):
    with conn() as c:
        rec=next((x for x in analytics_engine.product_analytics(c,window_days=payload.get('windowDays')) if int(x['productId'])==int(product_id)),None)
        if not rec:raise HTTPException(404,'商品不存在')
        if not rec.get('primarySupplierId'):raise HTTPException(409,'该商品未配置可用供应商')
        qty=int(payload.get('quantity') or rec.get('recommendedReorderQty') or 0)
        if qty<=0:raise HTTPException(409,'当前没有自动补货数量；如需人工补货请使用采购单')
        expected=(datetime.utcnow()+timedelta(days=max(0,int(rec.get('leadTimeDays') or 0)))).strftime('%Y-%m-%d')
        try:
            po=inventory_engine.create_purchase_order(c,supplier_id=int(rec['primarySupplierId']),items=[{'productId':product_id,'quantity':qty}],expected_at=expected,note=f"v0.22 智能补货 · {rec['healthReason']} · {rec['windowDays']}天销量 {rec['soldUnits']} 件",created_by='replenishment')
            return {'ok':True,'recommendation':rec,'purchaseOrder':po}
        except LookupError as e:raise HTTPException(404,str(e))
        except ValueError as e:raise HTTPException(409,str(e))


# v0.21 · Supplier Settlement & Merchandise Profit
@app.get('/api/platform/finance/supplier-summary')
def platform_supplier_finance_summary():
    with conn() as c:return finance_engine.supplier_summary(c)

@app.get('/api/platform/finance/suppliers/{supplier_id}')
def platform_supplier_finance_detail(supplier_id:int):
    with conn() as c:
        try:return finance_engine.supplier_account(c,supplier_id=supplier_id)
        except LookupError as e:raise HTTPException(404,str(e))

@app.post('/api/platform/finance/supplier-payments')
def platform_supplier_payment(payload:dict=Body(...)):
    with conn() as c:
        try:return finance_engine.create_payment(c,supplier_id=int(payload.get('supplierId') or 0),amount=float(payload.get('amount') or 0),payment_ref=str(payload.get('paymentRef') or ''),payment_method=str(payload.get('paymentMethod') or ''),note=str(payload.get('note') or ''),paid_by='platform')
        except LookupError as e:raise HTTPException(404,str(e))
        except ValueError as e:raise HTTPException(409,str(e))

@app.get('/api/platform/profit/summary')
def platform_profit_summary():
    with conn() as c:return finance_engine.profit_summary(c)

@app.get('/api/platform/profit/orders')
def platform_order_profits(limit:int=200):
    with conn() as c:return finance_engine.order_profits(c,limit=limit)

@app.get('/api/platform/profit/products')
def platform_product_profits():
    with conn() as c:return finance_engine.product_profit(c)

@app.patch('/api/platform/orders/{order_id}/operating-costs')
def platform_order_operating_costs(order_id:int,payload:dict=Body(...)):
    shipping=max(0,float(payload.get('shippingCost') or 0));packaging=max(0,float(payload.get('packagingCost') or 0))
    with conn() as c:
        if not row(c.execute('SELECT id FROM gear_orders WHERE id=?',(order_id,))):raise HTTPException(404,'装备订单不存在')
        c.execute('UPDATE gear_orders SET shipping_cost=?,packaging_cost=? WHERE id=?',(shipping,packaging,order_id))
        return finance_engine.order_profit(c,order_id)

@app.get('/api/platform/procurement/purchase-returns')
def platform_purchase_returns(supplier_id:int|None=None,status:str|None=None):
    with conn() as c:return inventory_engine.list_purchase_returns(c,supplier_id=supplier_id,status=status)

@app.post('/api/platform/procurement/purchase-returns')
def platform_create_purchase_return(payload:dict=Body(...)):
    with conn() as c:
        try:return inventory_engine.create_purchase_return(c,po_id=str(payload.get('purchaseOrderId') or ''),items=payload.get('items') or [],note=str(payload.get('note') or ''),created_by='platform')
        except LookupError as e:raise HTTPException(404,str(e))
        except ValueError as e:raise HTTPException(409,str(e))

@app.get('/api/platform/procurement/purchase-returns/{return_id}')
def platform_purchase_return_detail(return_id:str):
    with conn() as c:
        try:return inventory_engine.get_purchase_return(c,return_id)
        except LookupError as e:raise HTTPException(404,str(e))

@app.post('/api/platform/procurement/purchase-returns/{return_id}/approve')
def platform_approve_purchase_return(return_id:str):
    with conn() as c:
        try:return inventory_engine.approve_purchase_return(c,return_id=return_id,approved_by='platform')
        except LookupError as e:raise HTTPException(404,str(e))
        except ValueError as e:raise HTTPException(409,str(e))

@app.post('/api/platform/procurement/purchase-returns/{return_id}/ship')
def platform_ship_purchase_return(return_id:str):
    with conn() as c:
        try:out=inventory_engine.ship_purchase_return(c,return_id=return_id,actor_type='platform')
        except LookupError as e:raise HTTPException(404,str(e))
        except ValueError as e:raise HTTPException(409,str(e))
    pids=out.pop('productIds',[]);out['commerceSync']=_safe_sync_inventory_products(pids);return out

@app.post('/api/platform/procurement/purchase-returns/{return_id}/credit')
def platform_credit_purchase_return(return_id:str,payload:dict=Body(default={})):
    with conn() as c:
        try:return inventory_engine.credit_purchase_return(c,return_id=return_id,credit_amount=payload.get('creditAmount'),note=str(payload.get('note') or ''))
        except LookupError as e:raise HTTPException(404,str(e))
        except ValueError as e:raise HTTPException(409,str(e))

# v0.19 · Supplier / Procurement / Inbound Inventory
@app.get('/api/platform/suppliers')
def platform_suppliers(status:str|None=None):
    with conn() as c:return inventory_engine.list_suppliers(c,status=status)

@app.post('/api/platform/suppliers')
def platform_create_supplier(payload:dict=Body(...)):
    with conn() as c:
        try:return inventory_engine.create_supplier(c,payload)
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/platform/suppliers/{supplier_id}')
def platform_supplier_detail(supplier_id:int):
    with conn() as c:
        try:return inventory_engine.get_supplier(c,supplier_id)
        except LookupError as e: raise HTTPException(404,str(e))

@app.patch('/api/platform/suppliers/{supplier_id}')
def platform_update_supplier(supplier_id:int,payload:dict=Body(...)):
    with conn() as c:
        try:return inventory_engine.update_supplier(c,supplier_id,payload)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/platform/products/{product_id}/suppliers')
def platform_product_suppliers(product_id:int):
    with conn() as c:
        try:return inventory_engine.product_suppliers(c,product_id)
        except LookupError as e: raise HTTPException(404,str(e))

@app.post('/api/platform/products/{product_id}/suppliers')
def platform_link_supplier(product_id:int,payload:dict=Body(...)):
    supplier_id=int(payload.get('supplierId') or 0)
    if not supplier_id: raise HTTPException(400,'supplierId required')
    with conn() as c:
        try:return inventory_engine.upsert_product_supplier(c,product_id=product_id,supplier_id=supplier_id,payload=payload)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/platform/procurement/purchase-orders')
def platform_purchase_orders(status:str|None=None,supplier_id:int|None=None):
    with conn() as c:return inventory_engine.list_purchase_orders(c,status=status,supplier_id=supplier_id)

@app.post('/api/platform/procurement/purchase-orders')
def platform_create_purchase_order(payload:dict=Body(...)):
    with conn() as c:
        try:return inventory_engine.create_purchase_order(c,supplier_id=int(payload.get('supplierId') or 0),items=payload.get('items') or [],expected_at=payload.get('expectedAt'),note=str(payload.get('note') or ''),created_by='platform')
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/platform/procurement/purchase-orders/{po_id}')
def platform_purchase_order_detail(po_id:str):
    with conn() as c:
        try:return inventory_engine.get_purchase_order(c,po_id)
        except LookupError as e: raise HTTPException(404,str(e))

@app.post('/api/platform/procurement/purchase-orders/{po_id}/approve')
def platform_approve_purchase_order(po_id:str):
    with conn() as c:
        try:return inventory_engine.approve_purchase_order(c,po_id=po_id,approved_by='platform')
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/platform/procurement/purchase-orders/{po_id}/order')
def platform_place_purchase_order(po_id:str):
    with conn() as c:
        try:return inventory_engine.place_purchase_order(c,po_id=po_id)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/platform/procurement/purchase-orders/{po_id}/cancel')
def platform_cancel_purchase_order(po_id:str,payload:dict=Body(default={})):
    with conn() as c:
        try:return inventory_engine.cancel_purchase_order(c,po_id=po_id,note=str(payload.get('note') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/platform/procurement/purchase-orders/{po_id}/receive')
def platform_receive_purchase_order(po_id:str,payload:dict=Body(...)):
    with conn() as c:
        try:out=inventory_engine.receive_purchase_order(c,po_id=po_id,items=payload.get('items') or [],received_by='platform',note=str(payload.get('note') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))
    product_ids=out.pop('productIds',[])
    out['commerceSync']=_safe_sync_inventory_products(product_ids)
    return out

@app.get('/api/platform/procurement/receipts')
def platform_purchase_receipts(po_id:str|None=None,supplier_id:int|None=None):
    with conn() as c:return inventory_engine.list_receipts(c,po_id=po_id,supplier_id=supplier_id)

@app.get('/api/platform/inventory/summary')
def platform_inventory_summary():
    with conn() as c:return inventory_engine.inventory_summary(c)

@app.get('/api/platform/inventory/movements')
def platform_inventory_movements(product_id:int|None=None,movement_type:str|None=None,limit:int=200):
    with conn() as c:return inventory_engine.list_movements(c,product_id=product_id,movement_type=movement_type,limit=limit)

@app.post('/api/platform/inventory/adjustments')
def platform_inventory_adjustment(payload:dict=Body(...)):
    pid=int(payload.get('productId') or 0);delta=int(payload.get('quantityDelta') or 0)
    with conn() as c:
        try:out=inventory_engine.adjust_stock(c,product_id=pid,quantity_delta=delta,reason=str(payload.get('reason') or '平台库存调整'),actor_type='platform')
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))
    product_ids=out.pop('productIds',[])
    out['commerceSync']=_safe_sync_inventory_products(product_ids)
    return out
