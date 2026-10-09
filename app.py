from __future__ import annotations
import uuid, os, io, csv, re, zipfile, shutil
from sqlite3 import IntegrityError
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, quote, urlsplit
from PIL import Image
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body, Query, Header, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, PlainTextResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from db import init_db, conn, row, rows, jdump, jload, setting
from document_parser import save_uploads, parse_sources, _image_kind
from cost_guard import sanitize_for_frontend, master_has_cost_evidence
from ai_engine import (generate_activity, generate_channel, regenerate_detail, detail_outline,
                       _fact_digest)
from longpic import generate_longpic, build_fact_pack
from image_caption import caption_media, caption_lines, persist_captions
from ai_billing import ensure_credits, charge_credits, credit_cost
from ai_gateway import (gateway_status, AIGatewayError, platform_provider_config,
                        update_platform_provider_config, test_provider_connection, effective_gateway_mode)
from clubos_domain.club_analytics import ClubBusinessIntelligence
from clubos_domain import club_biz
from clubos_domain import ClubOSPointsEngine, BookingEngine, CheckoutEngine, ActivityPointsPolicyService, ActivityRefundPolicyService, MembershipEngine, BenefitEngine, CommerceRefundEngine, PaymentLifecycleEngine, RefundLifecycleEngine, ParticipantService, ActivityExecutionService, CommissionSettlementEngine, AfterSalesEngine, ProcurementEngine, WarehouseEngine, MerchandiseFinanceEngine, CommerceAnalyticsEngine, AICreditEngine, GearRecommendService, LeaderRecommendService
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

class _RevalidateStatic(StaticFiles):
    """静态资源强制协商缓存：响应带 Cache-Control: no-cache，浏览器每次都带 ETag
    回源验证，文件没变就 304（不费流量，变了立刻生效）。
    过去响应没有 Cache-Control，浏览器按启发式缓存直接吃本地旧 JS ——
    发布新代码后用户页面长期跑旧版本（多日团期上线当天就复现过）。"""
    def file_response(self,*a,**k):
        resp=super().file_response(*a,**k)
        resp.headers['Cache-Control']='no-cache'
        return resp

app.mount('/static',_RevalidateStatic(directory=STATIC),name='static')


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
gear_recommend=GearRecommendService()
leader_recommend=LeaderRecommendService()
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
    # 积分策略页要在列表里直接看到每场活动的积分规则，因此把 7 个积分列一起带出来，
    # 免得页面上为每一行再打一次 points-policy。
    with conn() as c:return rows(c.execute('''SELECT id,title,status,event_date,location,price,capacity,created_at,cover,
        points_enabled,earn_club_points,accept_club_points,club_points_max_discount_percent,
        accept_gear_points,gear_points_max_discount_amount,club_points_earn_rate_override
        FROM activities WHERE club_id=? ORDER BY id DESC''',(club_id,)))

# ===== 活动详情版本：生成 / 重新生成 / 恢复上一版 =====
# 老板对第一版不满意时必须能「换一版」，而换一版的前提是原始资料还在。
# 因此生成时把 source 落进 activities.source_json，并为每一版留一份不可变快照；
# detail_version_id 指向当前生效的那一版，「恢复」只换指针，不调 AI、不扣 Credits。
def _source_for_storage(source:dict)->dict:
    """落盘用的原始资料。不存本机绝对路径（换工作区/换机器就失效），读取时按 /static 反查磁盘。

    images 持久化 'kind'（photo/logo）：离线生成器的 _photo_refs 据此过滤品牌 logo / 空白图，
    避免详情页把 logo 当真实照片排进 hero/gallery（老板要求"详情页不出现品牌 logo"）。"""
    images=[{k:x.get(k) for k in ('ref','name','url','width','height','orientation','source','page','kind')}
            for x in source.get('images') or []]
    return {'text':source.get('text',''),'files':source.get('files') or [],'images':images,
            'media_manifest':source.get('media_manifest') or []}


def _source_from_storage(stored:dict|None)->dict|None:
    if not isinstance(stored,dict): return None
    images=[];missing=0
    for x in stored.get('images') or []:
        url=str(x.get('url') or '')
        path=None
        if url.startswith('/static/'):
            candidate=STATIC/url[len('/static/'):]
            if candidate.exists(): path=str(candidate)
        if not path: missing+=1; continue          # 图片已不在磁盘：不送视觉模型，但 ref 仍可被排版引用
        item=dict(x); item['path']=path
        # 老活动落盘时还没存 kind：这里按磁盘图片重算（photo/logo），保证 regenerate 走新分类逻辑。
        if item.get('kind') not in ('photo','logo'):
            try: item['kind']=_image_kind(Image.open(path))
            except Exception: item['kind']='photo'
        images.append(item)
    out=dict(stored); out['images']=images; out['_resolved']=len(images); out['_missing']=missing
    return out


def _activity_source(a:dict)->tuple[dict|None,str]:
    """重新生成要用的原始资料：(source, 来源)。
    老活动（本次升级前生成）没有落盘 source，退回用 Activity Master 里的资料摘要与媒体清单反推。"""
    stored=_source_from_storage(jload(a.get('source_json'),None))
    if stored and (str(stored.get('text') or '').strip() or stored.get('media_manifest')): return stored,'stored'
    master=jload(a.get('activity_master_json'),{}) or {}
    media=master.get('media') or []
    text=str(master.get('sourceSummary') or '')
    if not text.strip() and not media: return None,'missing'
    return ({'text':text,'images':[],'files':[],'media_manifest':media,
             '_resolved':0,'_missing':len(media)},'master_fallback')


def _repair_master_media(master:dict,source:dict|None)->dict:
    """把 master.media 归一成「ref → url 齐全」的清单再下发。

    模型只被允许「引用」ref，媒体条目本身必须以真实资料为准。live 模式曾把模型输出的
    ["img_01",...] 直接落库，于是前端 ref→url 映射为空：所有图片渲染成灰色占位块、头图变纯色。
    读取时按 source 的清单补齐 url，并把拿不到 url 的条目剔掉（留着只会变成灰块）；
    source 里没有被引用的图片也一并带上，头图才有可回退的照片。
    """
    if not isinstance(master,dict):return master
    catalog={}
    for key in ('media_manifest','images'):
        for x in (source or {}).get(key) or []:
            if not isinstance(x,dict) or not x.get('ref'):continue
            ref=str(x['ref']);cur=catalog.get(ref)
            if cur is None:
                if x.get('url'):catalog[ref]=dict(x)      # 没有 url 的条目进了清单也渲染不出来
                continue
            for k,v in x.items():
                if cur.get(k) in (None,'') and v not in (None,''):cur[k]=v
    out=[];seen=set()
    for x in master.get('media') or []:
        ref=str((x.get('ref') if isinstance(x,dict) else x) or '')
        if not ref or ref in seen:continue
        base=catalog.get(ref)
        if isinstance(x,dict):
            merged=dict(base) if base else {}
            merged.update({k:v for k,v in x.items() if v not in (None,'')})
            if not str(merged.get('url') or ''):continue
            out.append(merged);seen.add(ref)
        else:
            if not base:continue
            out.append(dict(base));seen.add(ref)
    for ref,x in catalog.items():
        if ref not in seen:out.append(dict(x));seen.add(ref)
    master['media']=out
    return master


from clubos_domain.leader_recommend import ACTIVITY_TYPES as _LEADER_ACTIVITY_TYPES
_LEADER_SPECIALTIES = tuple(_LEADER_ACTIVITY_TYPES.keys())


def _leader_roster(c,club_id:int)->list[dict]:
    """领队资源库名册，附带累计带队次数——判断"谁在真正带队"要看这个数。"""
    out=[]
    for r in rows(c.execute('SELECT * FROM club_leaders WHERE club_id=? ORDER BY (status!="active"),id',(club_id,))):
        d=dict(r)
        specs=jload(d.get('specialties'),[])
        d['specialties']=[str(x) for x in specs] if isinstance(specs,list) else []
        d['assignedCount']=int(c.execute('SELECT COUNT(*) FROM occurrence_leaders WHERE club_id=? AND leader_id=?',
                                         (club_id,r['id'])).fetchone()[0])
        out.append(d)
    return out


def _leader_history(c,club_id:int)->list[dict]:
    """历史带队记录：谁带过哪一场、那场活动在哪、叫什么。只统计仍挂在团期上的人。"""
    return rows(c.execute('''SELECT ol.leader_id, ol.occurrence_id, a.id AS activity_id, a.title, a.location
                             FROM occurrence_leaders ol
                             JOIN activity_occurrences o ON o.id=ol.occurrence_id
                             JOIN activities a ON a.id=o.activity_id
                             WHERE ol.club_id=? AND ol.leader_id IS NOT NULL''',(club_id,)))


def _leader_plan_for(c,club_id:int,activity:dict)->dict:
    """活动详情页的「带队领队」：每个团期已派谁，以及该派谁（带理由）。"""
    roster=_leader_roster(c,club_id)
    history=_leader_history(c,club_id)
    aid=activity.get('id')
    occ=rows(c.execute('SELECT id,label,start_at,status FROM activity_occurrences WHERE activity_id=? AND club_id=? ORDER BY start_at',(aid,club_id)))
    assigned={}
    for r in rows(c.execute('''SELECT ol.* FROM occurrence_leaders ol
                               JOIN activity_occurrences o ON o.id=ol.occurrence_id
                               WHERE o.activity_id=? AND ol.club_id=? ORDER BY ol.id''',(aid,club_id))):
        assigned.setdefault(int(r['occurrence_id']),[]).append(r)
    out=[]
    for o in occ:
        oid=int(o['id'])
        here=assigned.get(oid,[])
        out.append({
            'occurrenceId':oid,'label':o.get('label') or o.get('start_at'),'startAt':o.get('start_at'),
            'leaders':[{'assignmentId':x['id'],'leaderId':x.get('leader_id'),'name':x.get('name'),
                        'phone':x.get('phone'),'role':x.get('role')} for x in here],
            'recommendations':leader_recommend.recommend(
                roster,history,activity,
                assigned_leader_ids=[x.get('leader_id') for x in here if x.get('leader_id')],limit=3),
        })
    return {'roster':roster,'rosterCount':len(roster),
            'activeCount':sum(1 for r in roster if r.get('status')=='active'),
            'specialties':list(_LEADER_SPECIALTIES),'occurrences':out}


def _gear_plan_for(c,master:dict,club_id:int)->dict:
    """出行清单 × 商城在售装备 + 会员折扣。

    候选集合只含商城里 status='active' 的真实商品，推荐引擎不做任何商品条目的生成或补全；
    清单里没有对应装备时如实标注，不凑一个相近的顶上（与媒体清单同一条纪律）。

    会员价按本俱乐部在用的「最高档」会员折扣计算并随商品一起下发，让「加入会员更便宜」
    这件事在出行清单上直接看得见，而不是藏在会员中心里。
    """
    prods=rows(c.execute('SELECT id,name,price,stock,category,image_url FROM products WHERE status="active" ORDER BY id'))
    plan=gear_recommend.plan(prods,master).as_dict()
    tier=row(c.execute("""SELECT name,gear_discount FROM club_member_tiers
                          WHERE club_id=? AND status="active" AND gear_discount IS NOT NULL AND gear_discount<1
                          ORDER BY rank DESC,id DESC LIMIT 1""",(club_id,)))
    if tier:
        rate=float(tier['gear_discount']); tier_name=str(tier['name'])
        plan['memberDiscount']={'rate':round(rate,4),'discountZhe':round(rate*10,2),'tierName':tier_name,
                                'label':f'{tier_name} {rate*10:g} 折'}
        picks=[p for it in (plan.get('items') or []) for p in (it.get('matches') or [])]
        picks+=list(plan.get('extras') or [])
        for p in picks:
            base=round(float(p.get('price') or 0),2); mp=round(base*rate,2)
            p['memberPrice']=mp; p['memberSavings']=round(base-mp,2)
            p['memberDiscount']=round(rate,4); p['memberTierName']=tier_name
    return plan


def _next_version_no(c,activity_id:int)->int:
    return int(c.execute('SELECT COALESCE(MAX(version_no),0)+1 FROM activity_detail_versions WHERE activity_id=?',(activity_id,)).fetchone()[0])


def _reserve_detail_version(c,*,club_id:int,activity_id:int,origin:str,direction:str|None=None,
                            facts_refreshed:bool=False)->tuple[int,int]:
    """先占位、再生成：这是服务端的防重与防重复扣费闸门。

    并发对同一场活动触发「换一版」时，第二条请求会撞上 status='pending' 的偏索引而失败，
    于是不会真的再调一次 AI、也不会再扣一次 Credits（前端锁只挡得住同一个页面）。
    进程被中断留下的 pending 占位超过 10 分钟会在下次占位时被清掉。"""
    c.execute("DELETE FROM activity_detail_versions WHERE activity_id=? AND status='pending' AND created_at<datetime('now','-10 minutes')",(activity_id,))
    version_no=_next_version_no(c,activity_id)
    try:
        c.execute('''INSERT INTO activity_detail_versions(club_id,activity_id,version_no,origin,direction,facts_refreshed,
                     narrative,outline,detail_json,status) VALUES(?,?,?,?,?,?,?,?,?,?)''',(
            club_id,activity_id,version_no,origin,(direction or '').strip() or None,
            1 if facts_refreshed else 0,'','',"{}",'pending'))
    except IntegrityError:
        raise HTTPException(409,'这场活动正在生成新的一版，请等它出来后再换。')
    return int(c.execute('SELECT last_insert_rowid()').fetchone()[0]),version_no


def _finalize_detail_version(c,*,version_id:int,activity_id:int,detail:dict,master:dict,credits:int=0,
                             gateway:str|None=None,model:str|None=None)->None:
    c.execute('''UPDATE activity_detail_versions SET narrative=?,outline=?,detail_json=?,master_json=?,credits_charged=?,
                 gateway_mode=?,gateway_model=?,status='ready' WHERE id=?''',(
        str(detail.get('coreSellingIdea') or ''),detail_outline(detail),jdump(detail),jdump(master),credits,
        gateway,model,version_id))
    c.execute('UPDATE activities SET detail_version_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(version_id,activity_id))


def _release_detail_version(c,version_id:int)->None:
    """生成失败时撤掉占位：没扣费、没落库，版本号也还给下一次。"""
    c.execute('DELETE FROM activity_detail_versions WHERE id=? AND status=\'pending\'',(version_id,))


async def _run_generate_job(club_id:int,job_id:int,source:dict,refresh_facts:bool=False,direction:str=''):
    """后台执行真模型生成（live 模式）。任何异常都落进 job.error，绝不能静默丢任务。"""
    try:
        with conn() as c:
            c.execute('UPDATE ai_generate_jobs SET status="running",stage="generating",started_at=CURRENT_TIMESTAMP WHERE id=?',(job_id,))
        result,usage=await generate_activity(club_id,source,direction=direction)
        master=result['activity_master']; detail=result['detail']
        with conn() as c:
            c.execute('UPDATE ai_generate_jobs SET stage="saving" WHERE id=?',(job_id,))
        charge_credits(club_id,'detail',usage.usage_id)
        title=master.get('title') or 'AI生成活动'
        with conn() as c:
            c.execute('INSERT INTO activities(club_id,title,status,event_date,location,price,capacity,activity_master_json,detail_json,source_json) VALUES(?,?,?,?,?,?,?,?,?,?)',(
                club_id,title,'draft',master.get('date',''),master.get('location',''),float(master.get('price') or 0),int(master.get('capacity') or 0),jdump(master),jdump(detail),jdump(_source_for_storage(source))))
            aid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
            vid,_n=_reserve_detail_version(c,club_id=club_id,activity_id=aid,origin='ai-generate')
            _finalize_detail_version(c,version_id=vid,activity_id=aid,detail=detail,master=master,
                                     credits=credit_cost('detail'),gateway=usage.provider,model=usage.model)
            if master.get('date') and master.get('date')!='待发布':
                c.execute('INSERT INTO activity_occurrences(activity_id,club_id,start_at,price,capacity,status,label) VALUES(?,?,?,?,?,?,?)',(
                    aid,club_id,str(master.get('date')),float(master.get('price') or 0),int(master.get('capacity') or 0),'open','首发团期'))
            c.execute('UPDATE ai_generate_jobs SET status="done",activity_id=?,finished_at=CURRENT_TIMESTAMP WHERE id=?',(aid,job_id))
    except Exception as e:
        with conn() as c:
            c.execute('UPDATE ai_generate_jobs SET status="failed",error=?,finished_at=CURRENT_TIMESTAMP WHERE id=?',(str(e)[:800] or e.__class__.__name__,job_id))

@app.get('/api/club/{club_id}/activities/ai-generate/{job_id}')
def ai_generate_job(club_id:int,job_id:int):
    """前端轮询生成进度。只回本俱乐部的任务，字段白名单防泄漏。"""
    club_or_404(club_id)
    with conn() as c:
        j=row(c.execute('SELECT id,status,stage,error,activity_id,summary_json,created_at,finished_at FROM ai_generate_jobs WHERE id=? AND club_id=?',(job_id,club_id)))
    if not j: raise HTTPException(404,'任务不存在')
    return {'jobId':j['id'],'status':j['status'],'stage':j['stage'],'error':j['error'] or '',
            'activityId':j['activity_id'],'source':jload(j['summary_json'],{}),
            'createdAt':j['created_at'],'finishedAt':j['finished_at']}

@app.post('/api/club/{club_id}/activities/ai-generate')
async def ai_generate(club_id:int,background_tasks:BackgroundTasks,prompt:str=Form(''),files:list[UploadFile]=File(default=[])):
    club_or_404(club_id); ensure_credits(club_id,'detail')
    # live 模式下真模型要跑几分钟，同步响应会被网关空闲超时掐断（2026-09-28 实测：
    # 上传 14MB 请求体 11s 就能完整过网关，吞吐根本不是瓶颈；死的是几分钟的同步等待）。
    # 所以 live 一律改异步任务；mock 秒回，保持同步不破坏回归脚本。
    live_mode=effective_gateway_mode()[0]=='live'
    if live_mode:
        with conn() as c:
            pending=c.execute('SELECT COUNT(*) FROM ai_generate_jobs WHERE club_id=? AND status IN ("queued","running")',(club_id,)).fetchone()[0]
        if pending: raise HTTPException(409,'已有一次 AI 生成正在进行中，请等它完成（或刷新页面查看结果）再提交新的。')
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
    # 上传了「文档类」资料，却一个字都没读出来 —— 必须当场说清楚，绝不能静默产出成品。
    # 这类资料（PPT/Word/PDF）的正文若被做成图片（设计稿式方案），提取文本就是空的；
    # 以前这里不检查，于是系统照样"成功"生成一份全是通用占位文案的活动，用户白等一次、
    # 还搭上一次 Credits，且完全不知道方案根本没被读到（实测复现过：资源上传成功、
    # 图片提取正常、文本为空、标题落到兜底值）。只传照片、不传文档是合法用法，不在此列。
    _doc_exts={'.pptx','.docx','.pdf','.txt','.md'}
    _doc_files=[f for f in (source.get('files') or []) if str(f.get('ext') or '') in _doc_exts]
    if _doc_files and not (source.get('text') or '').strip():
        shutil.rmtree(batch,ignore_errors=True)
        _names='、'.join(str(f.get('name') or '方案') for f in _doc_files[:3])
        raise HTTPException(422,f'《{_names}》里没有可读取的文字内容：这份方案的文字可能全部做成了图片。'
                                'AI 无法按方案生成，请补一句活动说明（名称/日期/地点/人数），'
                                '或换一份带文字的方案再试。')
    _text_len=len((source.get('text') or '').strip())
    _doc_exts={'.pptx','.docx','.pdf','.txt','.md'}
    _doc_files=[f for f in (source.get('files') or []) if str(f.get('ext') or '') in _doc_exts]
    summary={'files':source['files'],'imageCount':len(source['images']),'media':source['media_manifest'],
             'textLength':_text_len,'docFiles':_doc_files,'noText':_text_len==0}
    if not live_mode:
        # mock：秒回，保持旧的同步契约，回归脚本直接拿 activityId。
        try: result,usage=await generate_activity(club_id,source)
        except AIGatewayError as e: raise HTTPException(502,str(e))
        master=result['activity_master']; detail=result['detail']
        charge_credits(club_id,'detail',usage.usage_id)
        title=master.get('title') or 'AI生成活动'
        with conn() as c:
            c.execute('INSERT INTO activities(club_id,title,status,event_date,location,price,capacity,activity_master_json,detail_json,source_json) VALUES(?,?,?,?,?,?,?,?,?,?)',(
                club_id,title,'draft',master.get('date',''),master.get('location',''),float(master.get('price') or 0),int(master.get('capacity') or 0),jdump(master),jdump(detail),jdump(_source_for_storage(source))))
            aid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
            vid,_n=_reserve_detail_version(c,club_id=club_id,activity_id=aid,origin='ai-generate')
            _finalize_detail_version(c,version_id=vid,activity_id=aid,detail=detail,master=master,
                                     credits=credit_cost('detail'),gateway=usage.provider,model=usage.model)
            if master.get('date') and master.get('date')!='待发布':
                c.execute('INSERT INTO activity_occurrences(activity_id,club_id,start_at,price,capacity,status,label) VALUES(?,?,?,?,?,?,?)',(
                    aid,club_id,str(master.get('date')),float(master.get('price') or 0),int(master.get('capacity') or 0),'open','首发团期'))
        return {'activityId':aid,'activityMaster':master,'detail':detail,'gatewayMode':'mock','source':summary}
    # live：入队 + 后台生成，前端拿 jobId 轮询。
    with conn() as c:
        cur=c.execute('INSERT INTO ai_generate_jobs(club_id,status,stage,summary_json) VALUES(?,"queued","queued",?)',(club_id,jdump(summary)))
        jid=int(cur.lastrowid)
    background_tasks.add_task(_run_generate_job,club_id,jid,source)
    return {'jobId':jid,'stage':'queued','source':summary}

@app.post('/api/club/{club_id}/activities')
def create_activity_manual(club_id:int,payload:dict=Body(...)):
    """手工新建活动（草稿）—— 不经过 AI、不消耗 Credits 的第二条创建路径。

    与 ai-generate 的区别：这里只落「活动事实」（名称/日期/地点/价格/名额/一句话介绍），
    生成一份最小可渲染的 Activity Master + detail（facts 区块），让 C 端与俱乐部端立刻
    能编辑、能加团期、能发布。之后可用 PATCH /activities/{id} 补经营细节，
    或补一张封面；没有原始资料时 canRegenerate 为 False（本就无档可依），属预期。
    """
    club_or_404(club_id)
    title=str(payload.get('title') or '').strip()
    if not title: raise HTTPException(400,'活动名称不能为空')
    title=title[:80]
    event_date=str(payload.get('eventDate') or '').strip() or None
    location=str(payload.get('location') or '').strip() or None
    try: price=float(payload.get('price') or 0)
    except (TypeError,ValueError): raise HTTPException(400,'价格必须是数字')
    if price<0: raise HTTPException(400,'价格不能为负数')
    try: capacity=int(payload.get('capacity') or 0)
    except (TypeError,ValueError): raise HTTPException(400,'名额必须是整数')
    if capacity<0: raise HTTPException(400,'名额不能为负数')
    summary=str(payload.get('summary') or '').strip()[:300]
    # 团期 start_at 至少要能解析出「日期+时间」（C 端 _occRange 依赖 HH:mm），只给日期时补一个默认出发时间。
    occ_start=event_date
    if occ_start and not re.search(r'\d{1,2}:\d{2}',occ_start): occ_start+=' 08:00'
    master={'title':title,'date':event_date or '待定','location':location or '待定',
            'price':price,'capacity':capacity,'checklist':[],'createdVia':'manual'}
    detail={'title':title,'summary':summary,'blocks':[]}
    if summary: detail['blocks'].append({'type':'lead','text':summary})
    detail['blocks'].append({'type':'facts','items':[
        {'label':'日期','value':event_date or '待定'},
        {'label':'地点','value':location or '待定'},
        {'label':'价格','value':(f'¥{price:g}' if price else '待定')},
        {'label':'名额','value':(f'{capacity} 人' if capacity else '待定')},
    ]})
    with conn() as c:
        cur=c.execute('INSERT INTO activities(club_id,title,status,event_date,location,price,capacity,activity_master_json,detail_json) VALUES(?,?,?,?,?,?,?,?,?)',
            (club_id,title,'draft',event_date,location,price,capacity,jdump(master),jdump(detail)))
        aid=int(cur.lastrowid)
        if occ_start:
            c.execute('INSERT INTO activity_occurrences(activity_id,club_id,start_at,price,capacity,status,label) VALUES(?,?,?,?,?,?,?)',
                (aid,club_id,occ_start,price,capacity,'open','首发团期'))
    return {'ok':True,'activityId':aid}

@app.get('/api/club/{club_id}/ai-mode')
def club_ai_mode(club_id:int):
    """俱乐部端只读：当前 AI 生成走真实大模型还是演示引擎。
    创建弹窗用它明示，避免老板误以为演示模板就是真实模型生成的内容。"""
    club_or_404(club_id)
    mode,origin=effective_gateway_mode()
    return {'mode':mode,'origin':origin}

@app.get('/api/club/{club_id}/activities/{activity_id}')
def get_activity(club_id:int,activity_id:int):
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        occ=rows(c.execute('SELECT * FROM activity_occurrences WHERE activity_id=? AND club_id=? ORDER BY start_at',(activity_id,club_id)))
        # 能否「换一版」不只看有没有落盘 source：升级前生成的老活动可以用 Activity Master
        # 反推出一份最小原始资料，同样能换叙事/排版，所以判定必须与 _activity_source 一致。
        _src,_origin=_activity_source(a)
        has_source=_origin!='missing'
        versions=rows(c.execute('''SELECT id,version_no,origin,direction,facts_refreshed,created_at,credits_charged,gateway_mode,gateway_model,outline
                                   FROM activity_detail_versions WHERE activity_id=? AND club_id=? AND status='ready' ORDER BY version_no DESC''',(activity_id,club_id)))
        _master=_repair_master_media(jload(a.get('activity_master_json'),{}),_src)
        # detail 必须是排版 JSON（detail_json，含 blocks[]），不是 Activity Master。
        # 早期这里错写成读 activity_master_json：后台预览拿到的是没有 blocks 的 Master，
        # 于是头图 / 正文图片 / 出行清单全渲染不出来（C 端 public_activity 一直是对的）。
        _detail=jload(a.pop('detail_json',None),{}) or {}
        if not [b for b in (_detail.get('blocks') or []) if isinstance(b,dict) and b.get('type')] and a.get('detail_version_id'):
            # activities.detail_json 正常情况下与当前版本同步；万一不同步（老数据 / 中断的写入），
            # 退回当前版本快照，保证后台预览永远不会是空的。
            _dv=row(c.execute("SELECT detail_json FROM activity_detail_versions WHERE id=? AND activity_id=? AND club_id=? AND status='ready'",
                              (a['detail_version_id'],activity_id,club_id)))
            if _dv: _detail=jload(_dv['detail_json'],{}) or {}
        _gear=_gear_plan_for(c,_master,club_id)   # 清单×商城推荐要在连接关闭前把在售商品与会员折扣查出来
        _leaders=_leader_plan_for(c,club_id,{**a,'id':activity_id})   # 领队排班建议（同样在连接关闭前取）
    # source_json 是内部原始资料（可能含成本、供应商报价），绝不出接口。
    a.pop('source_json',None)
    a.pop('activity_master_json',None)
    a['activityMaster']=_master;a['detail']=_detail;a['occurrences']=occ
    # ★ 成本闸门（2026-10-06）：俱乐部端也是前端。结构化 master 一律不含成本（原始成本底价
    # 留在 source_json，该字段任何前端都不下发），避免俱乐部端预览/再保存把成本带出去。
    # 没有成本键的活动（如手动建、仅填了公开售价）不受影响 —— price/费用包含 等公开字段原样保留。
    a['activityMaster'], a['detail'], _cost_changed, cost_derived = sanitize_for_frontend(
        a.get('activityMaster') or {}, a.get('detail') or {})
    # 价格「待定」判定同 public_activity（非粘性：俱乐部定价后 priceFrom='priced' 即不再归零）。
    _pending=(a.get('activityMaster') or {}).get('priceFrom')=='pending' or (cost_derived and (a.get('activityMaster') or {}).get('priceFrom')!='priced')
    if _pending:
        try:
            if float(a.get('price') or 0) > 0:
                a['price'] = 0
        except (TypeError, ValueError):
            pass
        a['priceFrom'] = 'pending'
        for _oc in (a.get('occurrences') or []):
            try:
                if isinstance(_oc, dict) and float(_oc.get('price') or 0) > 0:
                    _oc['price'] = 0
            except (TypeError, ValueError):
                pass
    a['gearRecommendations']=_gear
    a['leaderPlan']=_leaders
    current=a.get('detail_version_id')
    for v in versions: v['isCurrent']=v['id']==current
    cur=next((v for v in versions if v['isCurrent']),None)
    a['detailVersion']={'currentId':current,'versionNo':(cur or {}).get('version_no'),'count':len(versions),
        'latestNo':versions[0]['version_no'] if versions else 0,'createdAt':(cur or {}).get('created_at'),
        'origin':(cur or {}).get('origin'),'versions':versions,'canRegenerate':has_source,
        'cost':credit_cost('detail'),'gatewayMode':effective_gateway_mode()[0]}
    a['pointsPolicy']=activity_points_policy.from_activity(a).as_dict()
    a['refundPolicy']=activity_refund_policy.from_activity(a).as_dict()
    a['participantPolicy']=participant_service.from_activity(a).as_dict()
    return a


@app.get('/api/club/{club_id}/activities/{activity_id}/detail-versions')
def activity_detail_versions(club_id:int,activity_id:int):
    with conn() as c:
        a=row(c.execute('SELECT id,detail_version_id,source_json,activity_master_json FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        vs=rows(c.execute('''SELECT id,version_no,origin,direction,facts_refreshed,narrative,outline,credits_charged,
                                    gateway_mode,gateway_model,created_at,detail_json
                             FROM activity_detail_versions WHERE activity_id=? AND club_id=? AND status='ready' ORDER BY version_no DESC''',(activity_id,club_id)))
        can_regen=_activity_source(a)[1]!='missing'
    for v in vs:
        detail=jload(v.pop('detail_json'),{}) or {}
        blocks=detail.get('blocks') or []
        v['isCurrent']=v['id']==a.get('detail_version_id')
        v['blockCount']=len([b for b in blocks if isinstance(b,dict) and b.get('type')])
        v['blockTypes']=[str(b.get('type')) for b in blocks if isinstance(b,dict) and b.get('type')]
    return {'currentId':a.get('detail_version_id'),'versions':vs,'cost':credit_cost('detail'),
            'gatewayMode':effective_gateway_mode()[0],'canRegenerate':can_regen,
            'nextVersionNo':(vs[0]['version_no']+1 if vs else 1)}


@app.post('/api/club/{club_id}/activities/{activity_id}/detail-regenerate')
async def activity_detail_regenerate(club_id:int,activity_id:int,payload:dict=Body(default={})):
    """重新生成活动详情（老板对第一版不满意时的「换一版」）。

    与 AI 宣发同一套计费：先校验额度，生成成功才扣 Credits（失败一律不扣）。
    默认只重做叙事与排版——事实冻结，因此团期、价格政策、报名数据都不会被 AI 改写；
    refreshFacts=true 时才允许用同一份原始资料复核标题/日期/地点/价格/人数。
    """
    club_or_404(club_id); ensure_credits(club_id,'detail')
    direction=str(payload.get('direction') or '')[:800]
    refresh=bool(payload.get('refreshFacts'))
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        # 先占位：并发的第二条请求会在这里被挡下，而不是各调一次 AI、各扣一次 Credits。
        version_id,version_no=_reserve_detail_version(c,club_id=club_id,activity_id=activity_id,
                                                      origin='ai-regenerate',direction=direction,facts_refreshed=refresh)
    source,origin=_activity_source(a)
    if not source:
        with conn() as c: _release_detail_version(c,version_id)
        raise HTTPException(409,'这场活动没有留下可复用的原始资料，无法重新生成；请用「AI 发活动」重新上传资料。')
    master=_repair_master_media(jload(a['activity_master_json'],{}) or {},source)
    previous=jload(a['detail_json'],{}) or {}
    try:
        result,usage=await regenerate_detail(club_id,source,master,direction=direction,
                                             previous_detail=previous,version_no=version_no,refresh_facts=refresh)
    except AIGatewayError as e:
        with conn() as c: _release_detail_version(c,version_id)
        raise HTTPException(502,str(e))
    except Exception:
        with conn() as c: _release_detail_version(c,version_id)
        raise
    detail=result.get('detail') or {}
    blocks=[b for b in detail.get('blocks') or [] if isinstance(b,dict) and b.get('type')]
    if not blocks:
        # 没扣费、没落库：宁可保留上一版，也不要让 C 端详情页变空。
        with conn() as c: _release_detail_version(c,version_id)
        raise HTTPException(502,'AI 这一版没有返回可用的内容结构，已保留上一版；换个方向再试一次。')
    detail['blocks']=blocks
    new_master=result.get('activity_master') if refresh else master
    new_master=new_master if isinstance(new_master,dict) and new_master else master
    try:
        cost=charge_credits(club_id,'detail',usage.usage_id)
    except Exception:
        with conn() as c: _release_detail_version(c,version_id)
        raise
    with conn() as c:
        if refresh:
            c.execute('UPDATE activities SET activity_master_json=?,title=?,event_date=?,location=?,price=?,capacity=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(
                jdump(new_master),new_master.get('title') or a['title'],new_master.get('date') or a['event_date'],
                new_master.get('location') or a['location'],float(new_master.get('price') or 0),int(new_master.get('capacity') or 0),activity_id))
        c.execute('UPDATE activities SET detail_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(jdump(detail),activity_id))
        _finalize_detail_version(c,version_id=version_id,activity_id=activity_id,detail=detail,master=new_master,
                                 credits=cost,gateway=usage.provider,model=usage.model)
    return {'activityId':activity_id,'versionId':version_id,'versionNo':version_no,'activityMaster':new_master,'detail':detail,
            'source':origin,'factsRefreshed':refresh,
            'ai':{'mode':effective_gateway_mode()[0],'provider':usage.provider,'model':usage.model},
            'usage':{'creditsCharged':cost}}


@app.post('/api/club/{club_id}/activities/{activity_id}/detail-versions/{version_id}/restore')
def activity_detail_version_restore(club_id:int,activity_id:int,version_id:int):
    """恢复到指定版本：只挪指针，不调用 AI，因此不消耗 Credits。"""
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        v=row(c.execute("SELECT * FROM activity_detail_versions WHERE id=? AND activity_id=? AND club_id=? AND status='ready'",(version_id,activity_id,club_id)))
        if not v: raise HTTPException(404,'版本不存在')
        detail=jload(v['detail_json'],{}) or {}
        if not [b for b in detail.get('blocks') or [] if isinstance(b,dict) and b.get('type')]:
            raise HTTPException(409,'这一版没有可用的内容结构，不能恢复。')
        master=_repair_master_media(jload(v['master_json'],None) or jload(a['activity_master_json'],{}) or {},_activity_source(a)[0])
        facts_changed=jdump(master)!=jdump(jload(a['activity_master_json'],{}) or {})
        c.execute('UPDATE activities SET detail_json=?,activity_master_json=?,detail_version_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(
            jdump(detail),jdump(master),version_id,activity_id))
        if facts_changed:
            c.execute('UPDATE activities SET title=?,event_date=?,location=?,price=?,capacity=? WHERE id=?',(
                master.get('title') or a['title'],master.get('date') or a['event_date'],master.get('location') or a['location'],
                float(master.get('price') or 0),int(master.get('capacity') or 0),activity_id))
    return {'activityId':activity_id,'versionId':version_id,'versionNo':v['version_no'],
            'factsRestored':facts_changed,'activityMaster':master,'detail':detail}

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
        _sync_activity_price_from_occurrences(c,club_id=club_id,activity_id=activity_id)
    return {'id':oid}

# ---------------------------------------------------------------------------
# 团期的修改 / 删除
#
# 2026-10-07 用户截图实证的缺口：团期只有 POST（新增）没有 PATCH/DELETE。
# 「首发团期」这类由活动创建时自动生成的团期，在俱乐部端只显示
# 「首发团期 ¥0 · 已售 0/20」一行纯文本，**没有任何入口能改出发时间与价格**
# —— 老板想调档期、调价、调名额，只能删掉整个活动重做。
#
# 这里补齐两条路由，并守住三条纪律：
#   ① 名额不得低于已售人数（否则「已售 N / 名额 M」自相矛盾，也会让超卖判定失真）；
#   ② 已有未取消报名的团期不许删除（留孤儿报名会让报名管理、保险、分车全部对不上）；
#      删除时把历史报名（已取消/已退款）的 occurrence_id 置空，保留财务留痕而不是连报名一起删。
#   ③ 「定过价」要解除价格待定粘性：成本表来源的活动（priceFrom != 'priced'）在读取时
#      会把团期价归零、C 端显示「价格待定」，报价/结算接口还会 409 拦截（见
#      _public_price_pending / public_activity）。只在团期被定了正价时不置 'priced'，
#      老板就会遇到「改了价格但还是 ¥0、还是报不了名」——等于白改。
# ---------------------------------------------------------------------------
def _sync_activity_price_from_occurrences(c, *, club_id:int, activity_id:int) -> bool:
    """把活动级公开价对齐到团期价，并解除「价格待定」粘性。

    顶层 activities.price 是 C 端列表卡片与详情页头部的展示价，团期 price 才是
    真正用于报价/下单的价格（两者是独立字段）。老板只改团期价时，若顶层价停在
    0 且 master.priceFrom 不是 'priced'，C 端会一直显示「价格待定」并拒绝报名。
    规则：对外展示价 = 本活动**未取消**团期里的最低正价（即「¥xxx 起」）。
    这里刻意**不**保留「老板在活动基本信息里填过的价」——实测那会让页头出现
    「头部 ¥3,280 / 团期 ¥2,980」这种自相矛盾的展示（改团期价理应是改对外价；
    团期价才是真实成交价）。没有正价团期时保持不动，让「价格待定」继续生效。
    """
    a=row(c.execute('SELECT price,activity_master_json FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
    if not a: return False
    prices=[float(r['price']) for r in c.execute(
        "SELECT price FROM activity_occurrences WHERE activity_id=? AND club_id=? AND COALESCE(status,'open')!='cancelled'",
        (activity_id,club_id)).fetchall() if float(r['price'] or 0)>0]
    if not prices: return False                        # 全是 0，维持「价格待定」
    best=min(prices)
    master=jload(a.get('activity_master_json'),{}) or {}
    if (master.get('priceFrom')=='priced') and float(a.get('price') or 0)==best and float(master.get('price') or 0)==best:
        return False                                   # 已一致，不写库也不动 updated_at
    master['price']=best; master['priceFrom']='priced'
    c.execute('UPDATE activities SET price=?,activity_master_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?',
              (best,jdump(master),activity_id,club_id))
    return True


@app.patch('/api/club/{club_id}/occurrences/{occurrence_id}')
def update_occurrence(club_id:int,occurrence_id:int,payload:dict=Body(...)):
    with conn() as c:
        o=row(c.execute('SELECT * FROM activity_occurrences WHERE id=? AND club_id=?',(occurrence_id,club_id)))
        if not o: raise HTTPException(404,'团期不存在')
        fields={}
        if 'startAt' in payload:
            v=str(payload.get('startAt') or '').strip()
            if not v: raise HTTPException(400,'出发时间不能为空')
            fields['start_at']=v
        if 'endAt' in payload:
            v=str(payload.get('endAt') or '').strip()
            fields['end_at']=v or None
        if 'price' in payload:
            try: p=float(payload.get('price'))
            except (TypeError,ValueError): raise HTTPException(400,'价格必须是数字')
            if p<0: raise HTTPException(400,'价格不能为负数')
            fields['price']=p
        if 'capacity' in payload:
            try: cap=int(payload.get('capacity'))
            except (TypeError,ValueError): raise HTTPException(400,'名额必须是整数')
            if cap<0: raise HTTPException(400,'名额不能为负数')
            sold=int(o.get('sold') or 0)
            if cap<sold: raise HTTPException(400,f'该团期已有 {sold} 人报名，名额不能少于 {sold}')
            fields['capacity']=cap
        if 'label' in payload:
            fields['label']=str(payload.get('label') or '').strip() or None
        if 'status' in payload:
            st=str(payload.get('status') or '').strip()
            if st not in ('open','closed','cancelled'): raise HTTPException(400,'团期状态只支持 open / closed / cancelled')
            fields['status']=st
        if not fields: raise HTTPException(400,'没有需要更新的字段')
        start=fields.get('start_at') or o.get('start_at')
        end=fields.get('end_at',o.get('end_at'))
        if end and start and str(end)<=str(start): raise HTTPException(400,'结束时间必须晚于出发时间')
        sets=','.join([f'{k}=?' for k in fields])
        c.execute(f'UPDATE activity_occurrences SET {sets} WHERE id=? AND club_id=?',
                  (*fields.values(),occurrence_id,club_id))
        priced=_sync_activity_price_from_occurrences(c,club_id=club_id,activity_id=int(o['activity_id']))
    return {'ok':True,'updated':sorted(fields.keys()),'priced':priced}


@app.delete('/api/club/{club_id}/occurrences/{occurrence_id}')
def delete_occurrence(club_id:int,occurrence_id:int):
    with conn() as c:
        o=row(c.execute('SELECT * FROM activity_occurrences WHERE id=? AND club_id=?',(occurrence_id,club_id)))
        if not o: raise HTTPException(404,'团期不存在')
        live=int(c.execute("SELECT COUNT(*) FROM registrations WHERE occurrence_id=? AND club_id=? AND status NOT IN ('cancelled','refunded')",
                           (occurrence_id,club_id)).fetchone()[0])
        if live: raise HTTPException(409,f'该团期还有 {live} 笔未取消的报名，不能删除。请先处理报名，或把团期改为「停止报名」。')
        # 执行域数据随团期整条清理（与「删除活动」同一份表清单）
        for t in ('participant_checkins','participant_group_assignments','execution_event_logs',
                  'activity_notices','execution_groups','occurrence_leaders','occurrence_execution_settings'):
            c.execute(f'DELETE FROM {t} WHERE occurrence_id=?',(occurrence_id,))
        # 已取消/已退款的报名是历史留痕，保留记录但解除团期关联（不连报名一起删）
        c.execute('UPDATE registrations SET occurrence_id=NULL WHERE occurrence_id=? AND club_id=?',(occurrence_id,club_id))
        c.execute('DELETE FROM activity_occurrences WHERE id=? AND club_id=?',(occurrence_id,club_id))
        _sync_activity_price_from_occurrences(c,club_id=club_id,activity_id=int(o['activity_id']))
    return {'ok':True,'deleted':occurrence_id}

@app.post('/api/club/{club_id}/activities/{activity_id}/publish')
def publish_activity(club_id:int,activity_id:int):
    with conn() as c:
        a=row(c.execute('SELECT activity_master_json FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        master=jload(a['activity_master_json'],{})
        if master.get('blocking_conflicts'): raise HTTPException(409,'存在必须解决的事实冲突，暂不能发布')
        c.execute('UPDATE activities SET status="published",updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?',(activity_id,club_id))
    return {'ok':True}

# ---------------------------------------------------------------------------
# 活动基本信息的编辑 / 删除
#
# 纪律：这里只改「运营事实字段」（名称 / 日期 / 地点 / 价格 / 名额）。
# AI 生成的叙事与版式属于 detail，不在这里手改——要换文案请走「换一版」。
# 事实改了之后，detail.blocks 里 facts 区块上的同名字段一并同步，避免预览页
# 出现「页头写着 10月24日、facts 里还是 11月2日」这种自相矛盾。
# ---------------------------------------------------------------------------
_FACT_LABELS = {
    'date': ('DATE', '日期', '时间', '出发'),
    'location': ('PLACE', '地点', '目的地', '位置'),
    'price': ('PRICE', 'FEE', '费用', '价格', '人均'),
}


def _sync_detail_facts(detail: dict, a: dict) -> bool:
    """把活动事实（日期/地点/价格）同步进 detail.blocks 的 facts 区块。

    只认 facts 区块里语义明确的标签，其余区块（叙事、图集、行程）不动——
    那些是 AI 的内容，事实同步不该顺手把它们改掉。返回是否有改动。
    """
    want = {
        'date': str(a.get('event_date') or '').strip(),
        'location': str(a.get('location') or '').strip(),
        'price': (f'¥{float(a.get("price") or 0):g}' if a.get('price') else ''),
    }
    changed = False
    for b in (detail or {}).get('blocks') or []:
        if not isinstance(b, dict) or b.get('type') != 'facts':
            continue
        for it in b.get('items') or []:
            if not isinstance(it, dict):
                continue
            label = str(it.get('label') or '')
            for key, names in _FACT_LABELS.items():
                if not want[key] or not any(n in label for n in names):
                    continue
                if str(it.get('value') or '') != want[key]:
                    it['value'] = want[key]
                    changed = True
                break
    return changed


@app.patch('/api/club/{club_id}/activities/{activity_id}')
def update_activity(club_id:int,activity_id:int,payload:dict=Body(...)):
    """编辑活动运营字段：名称 / 日期 / 地点 / 价格 / 名额。"""
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        fields={}
        if 'title' in payload:
            t=str(payload.get('title') or '').strip()
            if not t: raise HTTPException(400,'活动名称不能为空')
            fields['title']=t[:80]
        if 'eventDate' in payload: fields['event_date']=str(payload.get('eventDate') or '').strip() or None
        if 'location' in payload: fields['location']=str(payload.get('location') or '').strip() or None
        if 'price' in payload:
            try: v=float(payload.get('price'))
            except (TypeError,ValueError): raise HTTPException(400,'价格必须是数字')
            if v<0: raise HTTPException(400,'价格不能为负数')
            fields['price']=v
        if 'capacity' in payload:
            try: v=int(payload.get('capacity'))
            except (TypeError,ValueError): raise HTTPException(400,'名额必须是整数')
            if v<0: raise HTTPException(400,'名额不能为负数')
            fields['capacity']=v
        # 出行清单是老板可以手动调整的运营事实（AI 只给初稿）：每行一项，写回 Activity Master。
        # 它不是 activities 表的列，不进 SQL fields，只在下面写 master。
        checklist_raw=payload.get('checklist')
        checklist=None
        if checklist_raw is not None:
            if not isinstance(checklist_raw,list): raise HTTPException(400,'checklist 必须是字符串数组')
            checklist=[str(x).strip() for x in checklist_raw if str(x).strip()][:30]
        if not fields and checklist is None: raise HTTPException(400,'没有需要更新的字段')
        merged={**a,**fields}
        # Activity Master 是「事实」的权威载体，改了活动行就要一并对齐；
        # 否则 C 端详情与后台预览会出现两套日期/价格。
        master=jload(a.get('activity_master_json'),{}) or {}
        if 'title' in fields: master['title']=fields['title']
        if 'event_date' in fields: master['date']=fields['event_date'] or ''
        if 'location' in fields: master['location']=fields['location'] or ''
        if 'price' in fields:
            master['price']=fields['price']
            # 俱乐部主动设定了对外售价 → 标记 'priced'，解除「成本表来源→待定」的粘性判定，
            # 否则 C 端每次读取都会把俱乐部刚填的价格打回 0（详见 public_activity/get_activity）。
            master['priceFrom']='priced'
        if 'capacity' in fields: master['capacity']=fields['capacity']
        if checklist is not None: master['checklist']=checklist
        # 只更新「当前工作副本」；历史版本快照保持不可变（恢复某一版时会随之恢复其文案）。
        detail=jload(a.get('detail_json'),{}) or {}
        _sync_detail_facts(detail,merged)
        sets=','.join([f'{k}=?' for k in fields]+['activity_master_json=?','detail_json=?','updated_at=CURRENT_TIMESTAMP'])
        c.execute(f'UPDATE activities SET {sets} WHERE id=? AND club_id=?',
                  (*fields.values(),jdump(master),jdump(detail),activity_id,club_id))
    return {'ok':True,'updated':sorted(list(fields.keys())+(['checklist'] if checklist is not None else []))}


@app.delete('/api/club/{club_id}/activities/{activity_id}')
def delete_activity(club_id:int,activity_id:int):
    """删除活动（连同其团期与执行数据）。

    资金/履约护栏：只要还有一笔「未取消」的报名就拒绝删除——那些报名关联着
    支付、保险与退款流程，不能随活动一起消失。这类活动应当先处理完报名记录。
    """
    with conn() as c:
        a=row(c.execute('SELECT id,title FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        # 只有「未取消且未退款」的报名才算牵涉资金/履约。
        # refunded 是全额退款后的终态：钱已退完、保险与履约都已结清，没有可损失的东西，
        # 继续把它算进 live 会让「活动刚被全额退款就删不掉」—— 实测就是这条把测试数据清理卡死了
        # （先取消报名再删，状态变 refunded 依然 409，等于删除按钮对已退款活动永久失效）。
        live=int(c.execute("SELECT COUNT(*) FROM registrations WHERE activity_id=? AND club_id=? AND status NOT IN ('cancelled','refunded')",(activity_id,club_id)).fetchone()[0])
        if live: raise HTTPException(409,f'该活动还有 {live} 笔未取消的报名，不能删除。请先在报名管理里处理（取消 / 退款）后再删。')
        occ=[int(r['id']) for r in c.execute('SELECT id FROM activity_occurrences WHERE activity_id=? AND club_id=?',(activity_id,club_id)).fetchall()]
        if occ:
            marks=','.join('?'*len(occ))
            for t in ('participant_checkins','participant_group_assignments','execution_event_logs',
                      'activity_notices','execution_groups','occurrence_leaders','occurrence_execution_settings'):
                c.execute(f'DELETE FROM {t} WHERE occurrence_id IN ({marks})',occ)
        # 参与者财务分摊与变更日志同样引用 registrations / registration_participants，
        # 但这两张表没有 activity_id / club_id，只能按报名与参与者 id 删 —— 必须在
        # 下面那轮删除之前清掉。漏掉它们的后果是整条删除抛 FOREIGN KEY constraint failed
        # （线上表现为 500）。这个洞长期存在却没暴露：以前只要有未取消的报名就被 409
        # 挡在前面，根本走不到这里；把 refunded 从护栏判据里去掉之后才第一次踩到。
        reg_ids=[int(r['id']) for r in c.execute('SELECT id FROM registrations WHERE activity_id=? AND club_id=?',(activity_id,club_id)).fetchall()]
        if reg_ids:
            rm=','.join('?'*len(reg_ids))
            part_ids=[int(r['id']) for r in c.execute(f'SELECT id FROM registration_participants WHERE registration_id IN ({rm})',reg_ids).fetchall()]
            pm=','.join('?'*len(part_ids))
            for t in ('participant_financial_allocations','participant_change_logs'):
                c.execute(f'DELETE FROM {t} WHERE registration_id IN ({rm})',reg_ids)
                if part_ids: c.execute(f'DELETE FROM {t} WHERE participant_id IN ({pm})',part_ids)
        for t in ('registration_participants','registrations','content_assets','activity_detail_versions'):
            c.execute(f'DELETE FROM {t} WHERE activity_id=? AND club_id=?',(activity_id,club_id))
        c.execute('DELETE FROM activity_occurrences WHERE activity_id=? AND club_id=?',(activity_id,club_id))
        c.execute('DELETE FROM activities WHERE id=? AND club_id=?',(activity_id,club_id))
    return {'ok':True,'deleted':activity_id}


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
    if channel not in {'wechat','xhs','poster','recap','longpic'}: raise HTTPException(400,'unsupported channel')
    ensure_credits(club_id,channel)
    with conn() as c:a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
    if not a: raise HTTPException(404,'活动不存在')
    master=jload(a['activity_master_json'],{});detail=jload(a['detail_json'],{})
    # 渠道内容必须能看到第一手资料：原始方案里的具体数字 / 专名 / 动作才是好文案的素材库。
    # 只给模型看抽取后的 master/detail，公众号推文就只能把已压缩的事实再压缩一遍。
    source_text=str(jload(a['source_json'],{}).get('text') or '')
    # Feed the uploaded cover into channel generation as the main visual.
    # Use the club-scoped proxy URL (not the raw /static/uploads path, which is
    # forbidden in prod) so it resolves inside the club session.
    cover_url=f'/api/club/{club_id}/activities/{activity_id}/cover' if a.get('cover') else None

    # ══「AI 宣传长图」走独立管线 ══
    # ★ 排版权在代码侧，不在模型侧（2026-10-08 定案，反转过一次，别再改回去）：
    #   模型只输出「内容 JSON」（theme/hero/sections[blocks]/signup），
    #   由 longpic_template.py 用固定 CSS + 组件渲染 HTML。理由：标杆成品本身就是
    #   一套手写模板 + 两套配色主题，模型自由排版只会「同一份资料两次生成两个样」。
    #   （早期"模型直出整段 HTML"的实现作为**回退路径**保留在 longpic.py，模型结构化失败时用。）
    if channel=='longpic':
        master=_repair_master_media(master or {},_activity_source(a)[0])
        # ① 先让视觉模型给每张照片写描述并判断能不能用 —— 否则模型是「盲选图」，
        #    实测它会把路线地图截图选成首屏大图，还会选一张别的活动的雪山攀登照。
        caps=await caption_media(club_id,master)
        # ② 要点表只给碎片不给整句：上一轮实测把原文整段喂给写作模型时，
        #    成品 8 字片段 14.5% 能在原文里逐字命中（最长连续 31 字），读起来像 PPT 译文。
        digest=await _fact_digest(club_id,source_text) if source_text else ''
        # ③ 「顾客下单前要看的东西」往往都不在 detail 里，而在库里各班各组：
        #    团期价格 → activity_occurrences，领队 → occurrence_leaders，
        #    费用边界 / 自备装备 / 人员配置 → master.fees / checklist / publicFacts。
        #    ★ 这些字段此前**从来没进过长图提示词**，所以长图上既没有领队也没有价格
        #      （2026-10-08 老板反馈后要做的第一处接线）。
        fact_pack=build_fact_pack(club_id,activity_id,master)
        # ④ 版式种子：同一场活动每重新生成一次换一套版式；单次生成内预览与导出同源。
        with conn() as _c:
            _prev=_c.execute('SELECT COUNT(*) FROM content_assets WHERE club_id=? AND activity_id=? '
                             "AND channel='longpic'",(club_id,activity_id)).fetchone()[0]
        seed=activity_id*997+int(_prev or 0)
        try:
            content,usage=await generate_longpic(club_id,master,detail,cover_url=cover_url,
                                                 source_text=source_text,
                                                 caption_lines=caption_lines(master,caps),
                                                 digest=digest,
                                                 fact_pack=fact_pack,seed=seed)
        except AIGatewayError as e: raise HTTPException(502,str(e))
        if not content.get('html'):
            raise HTTPException(502,'模型没有返回可用的排版内容，请再试一次。')
        # ③ 描述写回 master：下次生成同一场活动不再重复跑视觉模型
        persist_captions(master,caps)
        with conn() as c:
            c.execute('UPDATE activities SET activity_master_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?',
                      (jdump(master),activity_id,club_id))
            c.execute('INSERT INTO content_assets(club_id,activity_id,channel,title,body_json) VALUES(?,?,?,?,?)',(
                club_id,activity_id,channel,content.get('title'),jdump(content)))
        charge_credits(club_id,channel,usage.usage_id)
        return content

    try: content,usage=await generate_channel(club_id,master,detail,channel,cover_url=cover_url,source_text=source_text)
    except AIGatewayError as e: raise HTTPException(502,str(e))
    charge_credits(club_id,channel,usage.usage_id)
    with conn() as c:c.execute('INSERT INTO content_assets(club_id,activity_id,channel,title,body_json) VALUES(?,?,?,?,?)',(
        club_id,activity_id,channel,content.get('title') or (content.get('titleOptions') or [''])[0],jdump(content)))
    return content

@app.get('/api/club/{club_id}/content')
def content_list(club_id:int):
    with conn() as c:return rows(c.execute('SELECT id,activity_id,channel,title,created_at FROM content_assets WHERE club_id=? ORDER BY id DESC',(club_id,)))

@app.get('/api/club/{club_id}/content/{asset_id}')
def content_detail(club_id:int,asset_id:int):
    """已生成渠道内容的完整正文。没有这个接口，内容中心列表只能是死记录——
    老板想再看一眼上周生成的公众号图文时无内容可渲染。严格按 club 归属过滤。"""
    with conn() as c:
        a=row(c.execute('SELECT id,activity_id,channel,title,body_json,created_at FROM content_assets WHERE id=? AND club_id=?',(asset_id,club_id)))
    if not a: raise HTTPException(404,'内容不存在')
    a['content']=jload(a.pop('body_json'),{})
    return a

@app.delete('/api/club/{club_id}/content/{asset_id}')
def content_delete(club_id:int,asset_id:int):
    """删除已生成的宣发内容（过期物料清理，用户 2026-10-09 提出）。

    只删 content_assets 这一条记录本身——不碰活动、报名、积分账本；
    海报/长图是渲染时现合成的，没有落盘的成品文件需要清理。
    严格按 club 归属过滤，跨俱乐部删除一律 404。"""
    with conn() as c:
        a=row(c.execute('SELECT id FROM content_assets WHERE id=? AND club_id=?',(asset_id,club_id)))
        if not a: raise HTTPException(404,'内容不存在或已删除')
        c.execute('DELETE FROM content_assets WHERE id=? AND club_id=?',(asset_id,club_id))
    return {'ok':True}

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

@app.delete('/api/club/{club_id}/occurrences/{occurrence_id}/leaders/{assignment_id}')
def remove_occurrence_leader(club_id:int,occurrence_id:int,assignment_id:int):
    with conn() as c:
        try:return execution_service.remove_leader(c,club_id=club_id,occurrence_id=occurrence_id,assignment_id=assignment_id)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))


# ---------------------------------------------------------------------------
# 领队资源库：俱乐部自己的领队名册
#
# 此前每场活动只能手打「姓名 + 电话」，没有名册就既无法复用同一个人，
# 也回答不了「这条线路以前是谁带的」——而这正是"按活动自动推荐领队"的前提。
# ---------------------------------------------------------------------------
def _leader_specs(raw)->list[str]:
    if isinstance(raw,str): raw=[x.strip() for x in raw.replace('，',',').replace('、',',').split(',')]
    if not isinstance(raw,(list,tuple)): return []
    seen=[]; 
    for x in raw:
        t=str(x or '').strip()
        if t and t not in seen: seen.append(t)
    return seen


# ── 俱乐部设置：品牌 DIY（名称 / logo / slogan）+ 联系方式 ────────────────────
# 订阅 SaaS 的俱乐部在这里自助包装自己的前端：C 端顶栏的名字、logo、首页品牌条
# 都从这里取（public_club 白名单下发，见 _club_public_brand）。
_CLUB_SETTINGS_FIELDS=('name','slogan','city','contact_name','contact_phone')

@app.get('/api/club/{club_id}/settings')
def club_settings_get(club_id:int):
    x=club_or_404(club_id)
    return {'name':x['name'],'slogan':x.get('slogan') or '','city':x.get('city') or '',
            'contactName':x.get('contact_name') or '','contactPhone':x.get('contact_phone') or '',
            'logoUrl':x.get('logo_url') or ''}

@app.put('/api/club/{club_id}/settings')
def club_settings_put(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    fields={}
    for f in _CLUB_SETTINGS_FIELDS:
        if f not in payload: continue
        v=str(payload.get(f) or '').strip()
        if f=='name' and not v: raise HTTPException(400,'俱乐部名称不能为空')
        if f=='name' and len(v)>40: raise HTTPException(400,'俱乐部名称过长（上限 40 字）')
        if f=='slogan' and len(v)>60: raise HTTPException(400,'品牌口号过长（上限 60 字）')
        fields[f]=v or None
    if 'logo_url' in payload:
        # 仅供「移除 logo」置空；设置新值必须走上传端点（它负责落盘和带扩展名）
        fields['logo_url']=str(payload.get('logo_url') or '').strip() or None
    if not fields: raise HTTPException(400,'没有需要更新的字段')
    with conn() as c:
        sets=','.join(f'{k}=?' for k in fields)
        c.execute(f'UPDATE clubs SET {sets} WHERE id=?',(*fields.values(),club_id))
    return {'ok':True,'updated':sorted(fields.keys())}

@app.post('/api/club/{club_id}/settings/logo')
@app.post('/api/club/{club_id}/settings/logo.{ext}')
async def club_settings_logo_upload(club_id:int,file:UploadFile=File(...),ext:str=''):
    """上传/替换俱乐部 logo。落 uploads/{club}/brand/logo{ext}，DB 存俱乐部域代理地址。"""
    club_or_404(club_id)
    real=Path(file.filename or '').suffix.lower()
    if real not in _PUBLIC_IMAGE_EXT: raise HTTPException(400,'仅支持图片文件：png/jpg/jpeg/webp/gif')
    if ext and '.'+str(ext).lower().lstrip('.')!=real:
        raise HTTPException(400,'路径里的扩展名与文件后缀不一致')
    data=await file.read()
    if len(data) > 5*1024*1024: raise HTTPException(413,'图片过大（上限 5MB）')
    dest=UPLOAD/str(club_id)/'brand'
    dest.mkdir(parents=True, exist_ok=True)
    (dest/f'logo{real}').write_bytes(data)
    for stale in dest.glob('logo.*'):
        if stale.suffix.lower()!=real: stale.unlink(missing_ok=True)
    url=f'/api/club/{club_id}/settings/logo{real}'
    with conn() as c:
        c.execute('UPDATE clubs SET logo_url=? WHERE id=?',(url,club_id))
    return {'logoUrl':url}

_LOGO_URL_RE=re.compile(r'^/api/club/(\d+)/settings/logo(\.[a-z0-9]{2,5})$', re.I)

def _serve_club_logo(club_id:int,ext:str):
    """把 clubs.logo_url 还原成磁盘上的那张图（形状校验后按路由参数拼路径，不掺任意片段）。"""
    with conn() as c:
        r=row(c.execute('SELECT logo_url FROM clubs WHERE id=?',(club_id,)))
    if not r or not r['logo_url']: raise HTTPException(404,'该俱乐部还没有设置 logo')
    m=_LOGO_URL_RE.match(str(r['logo_url']))
    if not m or int(m.group(1))!=int(club_id): raise HTTPException(404,'该俱乐部还没有设置 logo')
    ext='.'+str(ext).lower().lstrip('.')
    if ext not in _PUBLIC_IMAGE_EXT: raise HTTPException(404,'不支持的图片格式')
    fp=UPLOAD/f'{int(club_id)}'/'brand'/f'logo{ext}'
    if not fp.is_file(): raise HTTPException(404,'logo 文件已丢失')
    # no-store：logo 固定文件名覆盖写，URL 不带版本号 —— 不禁缓存的话换图后
    # 浏览器一直吃旧图（2026-10-09 用户反馈「上传之后一直换不了」）。
    return FileResponse(fp, headers={'Cache-Control':'no-store'})

@app.get('/api/club/{club_id}/settings/logo.{ext}')
def club_settings_logo(club_id:int,ext:str):
    """后台/登录态代理。"""
    return _serve_club_logo(club_id,ext)

@app.get('/api/public/clubs/{club_id}/logo.{ext}')
def public_club_logo(club_id:int,ext:str):
    """C 端公开代理（无登录态）。生产只放行 active 俱乐部。"""
    x=club_or_404(club_id)
    if IS_PROD and x['status']!='active':raise HTTPException(404,'not found')
    return _serve_club_logo(club_id,ext)


@app.get('/api/club/{club_id}/leaders')
def club_leaders_list(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        return {'leaders':_leader_roster(c,club_id),'specialties':list(_LEADER_SPECIALTIES)}


@app.post('/api/club/{club_id}/leaders')
def club_leader_add(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    name=str(payload.get('name') or '').strip()
    if not name: raise HTTPException(400,'领队姓名不能为空')
    with conn() as c:
        dup=c.execute('SELECT id FROM club_leaders WHERE club_id=? AND name=?',(club_id,name)).fetchone()
        if dup: raise HTTPException(409,f'资源库里已经有「{name}」了')
        lid=c.execute('''INSERT INTO club_leaders(club_id,name,phone,role,specialties,base_city,avatar_url,status,note)
                        VALUES(?,?,?,?,?,?,?,?,?)''',(
            club_id,name,str(payload.get('phone') or '').strip() or None,
            str(payload.get('role') or '').strip() or '领队',
            jdump(_leader_specs(payload.get('specialties'))),
            str(payload.get('baseCity') or '').strip() or None,
            _normalize_avatar_url(payload.get('avatarUrl')),
            str(payload.get('status') or 'active'),str(payload.get('note') or '').strip() or None)).lastrowid
    return {'id':int(lid)}


@app.patch('/api/club/{club_id}/leaders/{leader_id}')
def club_leader_update(club_id:int,leader_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        r=row(c.execute('SELECT * FROM club_leaders WHERE id=? AND club_id=?',(leader_id,club_id)))
        if not r: raise HTTPException(404,'领队不存在')
        fields={}
        if 'name' in payload:
            n=str(payload.get('name') or '').strip()
            if not n: raise HTTPException(400,'领队姓名不能为空')
            if c.execute('SELECT id FROM club_leaders WHERE club_id=? AND name=? AND id!=?',(club_id,n,leader_id)).fetchone():
                raise HTTPException(409,f'资源库里已经有「{n}」了')
            fields['name']=n
        if 'phone' in payload: fields['phone']=str(payload.get('phone') or '').strip() or None
        if 'role' in payload: fields['role']=str(payload.get('role') or '').strip() or '领队'
        if 'baseCity' in payload: fields['base_city']=str(payload.get('baseCity') or '').strip() or None
        if 'status' in payload: fields['status']=str(payload.get('status') or 'active')
        if 'note' in payload: fields['note']=str(payload.get('note') or '').strip() or None
        if 'specialties' in payload: fields['specialties']=jdump(_leader_specs(payload.get('specialties')))
        if 'avatarUrl' in payload: fields['avatar_url']=_normalize_avatar_url(payload.get('avatarUrl'))
        if not fields: raise HTTPException(400,'没有需要更新的字段')
        sets=','.join(f'{k}=?' for k in fields)
        c.execute(f'UPDATE club_leaders SET {sets},updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?',
                  (*fields.values(),leader_id,club_id))
    return {'ok':True,'updated':sorted(fields.keys())}


@app.delete('/api/club/{club_id}/leaders/{leader_id}')
def club_leader_delete(club_id:int,leader_id:int):
    """删除领队。带过队的只停用（带队历史要留痕），从没带过队的才真删。"""
    club_or_404(club_id)
    with conn() as c:
        r=row(c.execute('SELECT id,name FROM club_leaders WHERE id=? AND club_id=?',(leader_id,club_id)))
        if not r: raise HTTPException(404,'领队不存在')
        used=int(c.execute('SELECT COUNT(*) FROM occurrence_leaders WHERE club_id=? AND leader_id=?',(club_id,leader_id)).fetchone()[0])
        if used:
            c.execute('UPDATE club_leaders SET status="inactive",updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?',(leader_id,club_id))
            return {'ok':True,'mode':'deactivated','assignments':used}
        c.execute('DELETE FROM club_leaders WHERE id=? AND club_id=?',(leader_id,club_id))
    return {'ok':True,'mode':'deleted'}


@app.post('/api/club/{club_id}/leaders/{leader_id}/avatar')
@app.post('/api/club/{club_id}/leaders/{leader_id}/avatar.{ext}')
async def club_leader_avatar_upload(club_id:int,leader_id:int,file:UploadFile=File(...),ext:str=''):
    """上传/替换领队头像。旧文件不清，覆盖写同名文件即可。

    落盘路径固定在 uploads/{club}/leaders/{leader_id}/avatar{ext}，
    返回的是俱乐部域代理地址而不是裸 /static/uploads 链 —— 后者在生产环境
    被 security_v025 直接 404，写进去等于存了一条打不开的地址。

    两条路径都注册：前端按「不带扩展名」拼更容易读，但返回的一定带真实扩展名。
    路径上带的扩展名必须与文件后缀一致，否则读的时候按路径找会得到 404，
    存了一条自己读不回来的地址。
    """
    club_or_404(club_id)
    real=Path(file.filename or '').suffix.lower()
    if real not in _PUBLIC_IMAGE_EXT: raise HTTPException(400,'仅支持图片文件：png/jpg/jpeg/webp/gif')
    if ext and '.'+str(ext).lower().lstrip('.')!=real:
        raise HTTPException(400,'路径里的扩展名与文件后缀不一致')
    data=await file.read()
    if len(data) > 5*1024*1024: raise HTTPException(413,'图片过大（上限 5MB）')
    with conn() as c:
        r=row(c.execute('SELECT id,name FROM club_leaders WHERE id=? AND club_id=?',(leader_id,club_id)))
        if not r: raise HTTPException(404,'领队不存在')
    dest=UPLOAD/str(club_id)/'leaders'/str(leader_id)
    dest.mkdir(parents=True, exist_ok=True)
    (dest/f'avatar{real}').write_bytes(data)
    # 覆盖写会让旧扩展名的残file变成孤儿，顺手清掉，避免磁盘上堆积
    for stale in dest.glob('avatar.*'):
        if stale.suffix.lower()!=real: stale.unlink(missing_ok=True)
    # URL 必须带扩展名：反解回磁盘时要靠它找真实文件，
    # 存成 /avatar（无扩展名）会让代理永远返回「头像文件已丢失」。
    url=f'/api/club/{club_id}/leaders/{leader_id}/avatar{real}'
    with conn() as c:
        c.execute('UPDATE club_leaders SET avatar_url=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?',
                  (url,leader_id,club_id))
    return {'avatarUrl':url}


@app.get('/api/club/{club_id}/leaders/{leader_id}/avatar.{ext}')
def club_leader_avatar(club_id:int,leader_id:int,ext:str):
    """俱乐部域头像代理：后台、领队执行端走这条（带登录态）。

    路径里必须带扩展名 —— 落盘文件是按扩展名命名的（换图可能是 .jpg 覆盖
    .png），URL 不带就没法定位；而且动态路由少了这一段，带扩展名的请求会
    连路由都匹配不上，直接落到 404。
    """
    return _serve_leader_avatar(club_id,leader_id,ext)


_AVATAR_URL_RE=re.compile(r'^/api/club/(\d+)/leaders/(\d+)/avatar(\.[a-z0-9]{2,5})$', re.I)


def _serve_leader_avatar(club_id:int, leader_id:int, ext:str):
    """把 club_leaders.avatar_url 还原成磁盘上的那张图。

    不能走 _safe_media_path：那个函数只认 /static/ 下 uploads/ 或 demo/ 开头的
    路径，而这里存的是 /api/club/... 代理地址，反解成 /static/1/leaders/... 会被
    直接拒掉，代理永远返回「头像文件已丢失」。所以这里改成：先按形状校验 URL，
    再拿路由参数拼路径 —— 路径里不掺 avatar_url 的任意片段，
    即便库里被写入畸形值也跳出不了 uploads。
    """
    with conn() as c:
        r=row(c.execute('SELECT avatar_url FROM club_leaders WHERE id=? AND club_id=?',(leader_id,club_id)))
    if not r: raise HTTPException(404,'领队不存在')
    m=_AVATAR_URL_RE.match(str(r['avatar_url'] or ''))
    if not m or int(m.group(1))!=int(club_id) or int(m.group(2))!=int(leader_id):
        raise HTTPException(404,'该领队还没有设置头像')
    ext='.'+str(ext).lower().lstrip('.')
    if ext not in _PUBLIC_IMAGE_EXT: raise HTTPException(404,'不支持的图片格式')
    fp=UPLOAD/f'{int(club_id)}'/f'leaders'/f'{int(leader_id)}'/f'avatar{ext}'
    if not fp.is_file(): raise HTTPException(404,'头像文件已丢失')
    return FileResponse(fp)


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
#
# 领队落地页（2026-10-04 用户实测）：裸开 /leader 只回一句「缺少 occurrence 参数」，
# 领队在车上/山里单手打开就是一个死页面 —— 必须有个「今天我要带哪几场」的入口。
# 这里复用俱乐部的 list_occurrences（同一份域数据），但**走字段白名单**：
# 领队不需要 price/sold/capacity 这类经营字段（leader_brief 也刻意不含经营数据）。
_LEADER_OCC_FIELDS=('id','activity_id','activity_title','activity_location','start_at','end_at',
                    'label','execution_status','named_participants','insurance_pending','checked_in')

@app.get('/api/leader/occurrences')
def leader_occurrences(club_id:int=1,leader_id:int=0):
    with conn() as c:
        out=[]
        for it in execution_service.list_occurrences(c,club_id=club_id):
            row={k:it.get(k) for k in _LEADER_OCC_FIELDS}
            ls=execution_service.list_leaders(c,int(it['id']))
            # 带 leader_id 时只回「我的场」——生产环境应按登录领队过滤。
            if leader_id: ls=[l for l in ls if int(l.get('leader_id') or 0)==leader_id]
            row['leaders']=[{'name':l.get('name'),'role':l.get('role'),'phone':l.get('phone'),
                             'avatar_url':l.get('avatar_url')} for l in ls]
            out.append(row)
        return out

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

def _product_extras(c, ids:list[int]):
    """批量取商品的规格与图集，一次查完再按 product_id 分组。

    抽出来是因为列表接口一次要带几十个商品，逐个查会退化成 N+1。
    字段刻意压平（variants 直接给 price/stock 而不是 Medusa 那种 prices[] 嵌套）——
    将来 COMMERCE_PROVIDER 切到 medusa 时由适配层负责转换，前端不用改。
    """
    images={}; variants={}
    ids=[int(i) for i in ids if i]
    if not ids: return images,variants
    ph=','.join('?'*len(ids))
    for r in c.execute(f'SELECT id,product_id,url FROM product_images WHERE product_id IN ({ph}) ORDER BY product_id,sort,id',ids).fetchall():
        images.setdefault(int(r['product_id']),[]).append({'url':r['url']})
    for r in c.execute(f"SELECT id,product_id,name,sku,price,stock,sort,is_default FROM product_variants WHERE product_id IN ({ph}) AND status='active' ORDER BY product_id,sort,id",ids).fetchall():
        variants.setdefault(int(r['product_id']),[]).append({
            'id':int(r['id']),'name':r['name'],'sku':r['sku'],
            'price':float(r['price']) if r['price'] is not None else None,
            'stock':int(r['stock'] or 0),'isDefault':int(r['is_default'] or 0)})
    return images,variants

def _with_extras(c, product_list:list[dict]):
    if not product_list: return product_list
    images,variants=_product_extras(c,[x['id'] for x in product_list])
    out=[]
    for x in product_list:
        d=dict(x); pid=int(x['id'])
        imgs=images.get(pid) or ([{'url':x['image_url']}] if x.get('image_url') else [])
        d['images']=imgs; d['variants']=variants.get(pid) or []
        out.append(d)
    return out

@app.get('/api/club/{club_id}/mall/products')
def club_products(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        items=rows(c.execute('SELECT id,name,sku,price,stock,status,image_url,category FROM products WHERE status="active" ORDER BY id DESC'))
        return _with_extras(c,items)

@app.get('/api/club/{club_id}/mall/inventory')
def club_mall_inventory(club_id:int):
    """俱乐部端「装备管理」用：要能在架/下架之间切换着看，所以这里不过滤 status，
    只给俱乐部自己。C 端仍然走 club_products（只取 active），两边不互相污染。"""
    club_or_404(club_id)
    with conn() as c:
        items=rows(c.execute('SELECT id,name,sku,price,stock,status,category,image_url,commission_rate FROM products ORDER BY id DESC'))
        reqs=rows(c.execute('SELECT * FROM club_product_visibility_requests WHERE club_id=? ORDER BY id DESC',(club_id,)))
    by_req={}
    for r in reqs: by_req.setdefault(r['product_id'],[]).append(r)
    for it in items:
        mine=by_req.get(it['id']) or []
        it['requests']=mine
        it['pendingRequest']=next((x for x in mine if x['status']=='pending'),None)
    return items

@app.post('/api/club/{club_id}/mall/products/{product_id}/visibility')
def club_product_visibility_request(club_id:int,product_id:int,payload:dict=Body(...)):
    """俱乐部提交上下架申请。商品归总平台所有（products 表没有 club 归属列），
    俱乐部直接改 status 就是绕过总平台，所以这里只落一条待处理申请。"""
    club_or_404(club_id)
    action=str(payload.get('action') or '').strip()
    if action not in {'on','off'}: raise HTTPException(400,'申请类型只能是 on/off')
    with conn() as c:
        p=row(c.execute('SELECT id,name,status FROM products WHERE id=?',(product_id,)))
        if not p: raise HTTPException(404,'商品不存在')
        want='active' if action=='on' else 'inactive'
        if str(p.get('status') or '')==want:
            raise HTTPException(409,'该商品当前已经是这个状态，无需重复申请')
        pending=row(c.execute("""SELECT id FROM club_product_visibility_requests
                                 WHERE club_id=? AND product_id=? AND status='pending' ORDER BY id DESC LIMIT 1""",(club_id,product_id)))
        if pending: raise HTTPException(409,'这件商品已有一条待处理的申请，请先等总平台处理')
        c.execute("""INSERT INTO club_product_visibility_requests(club_id,product_id,action,note)
                     VALUES(?,?,?,?)""",(club_id,product_id,action,str(payload.get('note') or '')[:200]))
        rid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    return {'ok':True,'requestId':rid,'productName':p.get('name')}

@app.get('/api/club/{club_id}/mall/visibility-requests')
def club_mall_visibility_requests(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        return rows(c.execute("""SELECT r.*,p.name product_name,p.sku
                                 FROM club_product_visibility_requests r JOIN products p ON p.id=r.product_id
                                 WHERE r.club_id=? ORDER BY r.id DESC""",(club_id,)))

@app.get('/api/club/{club_id}/mall/orders')
def club_orders(club_id:int):
    # 带上买家与商品明细：俱乐部端点开订单要能看到「卖了哪几件、各多少」，
    # 只回一行 total 的话「商城订单详情」只能是空壳。
    with conn() as c:
        orders=rows(c.execute('SELECT o.id,o.total,o.status,o.tracking_no,o.carrier,o.club_commission,o.after_sales_status,o.created_at,u.name buyer FROM gear_orders o JOIN users u ON u.id=o.user_id WHERE o.source_club_id=? ORDER BY o.id DESC',(club_id,)))
        items=rows(c.execute('''SELECT i.order_id,i.product_id,i.variant_name,i.quantity,i.unit_price,i.unit_cost_snapshot,p.name product_name
                                FROM gear_order_items i LEFT JOIN products p ON p.id=i.product_id
                                WHERE i.order_id IN (SELECT id FROM gear_orders WHERE source_club_id=?)''',(club_id,)))
        by={}
        for it in items: by.setdefault(it['order_id'],[]).append(it)
        for o in orders: o['items']=by.get(o['id'],[])
        return orders

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
        # 品牌白名单（俱乐部设置 DIY 下发）：logo 以公开代理地址给出 ——
        # DB 里存的 /api/club/... 代理要登录态，C 端拿去是 401/404。
        out={k:x.get(k) for k in ('id','name','city','slogan')}
        if x.get('logo_url'):
            m=_LOGO_URL_RE.match(str(x['logo_url']))
            if m: out['logoUrl']=f'/api/public/clubs/{club_id}/logo{m.group(2)}'
    else:
        out=dict(x)
        if x.get('logo_url'):
            m=_LOGO_URL_RE.match(str(x['logo_url']))
            if m: out['logoUrl']=f'/api/public/clubs/{club_id}/logo{m.group(2)}'
    # 业务介绍区：未配置或关闭 → None，C 端整节隐藏（空壳板块比没有板块更伤信任）。
    # 图片在这里就换成公开代理地址（生产 /static/uploads/* 是 404）。
    out.pop('biz_section_json',None)
    out['biz_section']=club_biz.public_json(club_id,x)
    return out

@app.get('/api/club/{club_id}/biz-section')
def get_club_biz_section(club_id:int):
    return club_biz.from_club(club_or_404(club_id))

@app.patch('/api/club/{club_id}/biz-section')
def update_club_biz_section(club_id:int,payload:dict=Body(...)):
    x=club_or_404(club_id)
    try: cfg=club_biz.normalize(payload,club_biz.from_club(x))
    except ValueError as e: raise HTTPException(400,str(e))
    with conn() as c:
        c.execute('UPDATE clubs SET biz_section_json=? WHERE id=?',(club_biz.dumps(cfg),club_id))
    return cfg

@app.get('/api/public/clubs/{club_id}/biz-media/{asset_path:path}')
def public_club_biz_media(club_id:int,asset_path:str):
    # 只放行「业务介绍配置里真实引用过」的文件，不是对 /static/uploads 通配开放。
    x=club_or_404(club_id)
    if IS_PROD and x['status']!='active':raise HTTPException(404,'not found')
    original='/static/'+unquote(asset_path)
    if original not in club_biz.media_paths(club_biz.from_club(x)):raise HTTPException(404,'not found')
    file_path=_safe_media_path(original)
    if not file_path or not file_path.is_file():raise HTTPException(404,'not found')
    response=FileResponse(file_path)
    response.headers['Cache-Control']='public, max-age=300'
    return response

@app.get('/api/public/clubs/{club_id}/activities')
def public_activities(club_id:int):
    if IS_PROD and club_or_404(club_id)['status']!='active':raise HTTPException(404,'not found')
    with conn() as c:
        acts=rows(c.execute('SELECT id,title,event_date,location,price,capacity,cover,activity_master_json FROM activities WHERE club_id=? AND status="published" ORDER BY id DESC',(club_id,)))
    for a in acts:
        # ★ 成本闸门（2026-10-06）：成本表来源且俱乐部未定价（priceFrom 非 'priced'）的活动，
        # 顶层 price 是「合计÷人数」推出来的内部单价，绝不能当对外售价摆上列表卡。铁证判定后归零
        # 并打 priceFrom='pending'（前端显示「价格待定」）。俱乐部定价后 priceFrom='priced'，不再归零。
        try:
            _m=jload(a.pop('activity_master_json',None),{}) or {}
            _pending=(_m.get('priceFrom')=='pending') or (master_has_cost_evidence(_m) and _m.get('priceFrom')!='priced')
            if _pending:
                if float(a.get('price') or 0) > 0:
                    a['price']=0
                a['priceFrom']='pending'
        except (TypeError, ValueError):
            pass
        cov=a.get('cover')
        if cov and str(cov).startswith('/static/'):
            a['cover']='/api/public/activities/%d/media/%s'%(a['id'],quote(str(cov)[len('/static/'):],safe='/'))
    return acts

def _public_leader_avatar_url(club_id:int,leader_id,avatar_url):
    """把俱乐部域头像地址改写成 C 端可访问的公开代理地址。

    C 端顾客没有 club 角色，`/api/club/{c}/leaders/{id}/avatar.png` 那条根本取不到；
    而头像又必须回代理地址（生产环境 /static/uploads/* 被封死，裸链就是死链）。
    形状不对（库里是空值或畸形值）就回 None，前端退回首字占位，不做二次猜测。
    """
    m=_AVATAR_URL_RE.match(str(avatar_url or ''))
    if not m or int(m.group(1))!=int(club_id) or int(m.group(2))!=int(leader_id):return None
    return '/api/public/leaders/%d/avatar%s'%(int(leader_id),m.group(3))


def _public_leaders(c,activity_id:int,club_id:int)->list:
    """C 端活动详情的「带队领队」：**活动级**的一份去重名单，只含**已经指派**的人。

    为什么是活动级而不是按团期分组：顾客在报名页挑的是「这场活动谁带」，团期只是同一场
    活动的不同日期；同一个人被派到两个团期就印两遍，卡片区立刻变成名字堆。团期维度的
    排班（谁带哪一场）属于领队端 / 俱乐部执行视图的信息，不必搬到 C 端报名页。

    字段走白名单，只挑明确要展示的列（不做 SELECT *，免得以后加列就顺手泄漏出去）：
      姓名 / 指派角色 / 名册身份 / 擅长标签 / 常驻城市 / 头像
    手机号是俱乐部内部联络信息，俱乐部端 _leader_plan_for 带 phone 是给经营者看的，
    这里绝不下发；也**不下发推荐名单**（顾客不需要知道「本来还考虑过谁」）。

    执行端手工填人名的临时增援没有 leader_id，关联不上名册 → 后四项如实为空，
    前端缺哪项就不印哪项，不拿别的字段顶出一个看着很满的卡片。
    """
    seen=set();out=[]
    for r in rows(c.execute('''SELECT ol.leader_id,ol.name,ol.role,
                                      l.role AS credential,l.specialties,l.base_city,l.avatar_url
                               FROM occurrence_leaders ol
                               JOIN activity_occurrences o ON o.id=ol.occurrence_id
                               LEFT JOIN club_leaders l ON l.id=ol.leader_id
                               WHERE o.activity_id=? AND ol.club_id=? AND o.status="open"
                               ORDER BY o.start_at,ol.id''',(activity_id,club_id))):
        name=str(r.get('name') or '').strip() or '领队'
        key=(int(r['leader_id']) if r.get('leader_id') else 0,name)
        if key in seen:continue
        seen.add(key)
        specs=jload(r.get('specialties'),[])
        out.append({
            'name':name,
            'role':str(r.get('role') or '').strip() or '领队',
            'credential':str(r.get('credential') or '').strip() or None,
            'specialties':[str(x).strip() for x in specs if str(x).strip()] if isinstance(specs,list) else [],
            'baseCity':str(r.get('base_city') or '').strip() or None,
            'avatarUrl':_public_leader_avatar_url(club_id,r.get('leader_id'),r.get('avatar_url'))})
    return out


@app.get('/api/public/leaders/{leader_id}/avatar.{ext}')
def public_leader_avatar(leader_id:int,ext:str):
    """C 端领队头像。顾客没有 club 角色，走不了俱乐部域那条代理，所以单开一条公开的。

    磁盘定位与扩展名校验完全复用 _serve_leader_avatar：先按形状校验库里存的 URL，
    再用路由参数拼路径 —— 公开端点也不能变成读任意文件的口子。
    """
    with conn() as c:
        r=row(c.execute('SELECT club_id FROM club_leaders WHERE id=?',(leader_id,)))
    if not r: raise HTTPException(404,'领队不存在')
    return _serve_leader_avatar(int(r['club_id']),leader_id,ext)


@app.get('/api/public/activities/{activity_id}')
def public_activity(activity_id:int):
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND status="published"',(activity_id,)))
        occ=rows(c.execute('SELECT *,MAX(capacity-sold,0) remaining FROM activity_occurrences WHERE activity_id=? AND status="open" ORDER BY start_at',(activity_id,)))
    if not a: raise HTTPException(404,'活动不存在或未发布')
    if IS_PROD and club_or_404(int(a['club_id']))['status']!='active':raise HTTPException(404,'not found')
    _pub_src,_pub_origin=_activity_source(a)
    a.pop('source_json',None)   # 原始资料属俱乐部内部资料，C 端一律不下发
    a['activityMaster']=_repair_master_media(jload(a.pop('activity_master_json'),{}),_pub_src);a['detail']=jload(a.pop('detail_json'),{});a['occurrences']=occ
    # ★ 成本闸门（2026-10-06）：C 端是顾客视角，成本底价/内部报价一律不可见。
    # 解析期已不再把成本喂给模型、出口也已清洗，这里再兜底一遍 —— 确保**库里已存在的老活动**
    # （改代码前生成、master 里还留着 人均费用/合计（未含税）/price=3806.55 的那批）也不泄露。
    # 成本来源的 master 其 price 归零并打 priceFrom='pending'（前端显示「价格待定」），
    # 绝不拿成本价当售价卖。原始成本底价留在 source_json（任何前端都不下发），俱乐部仍可追溯。
    a['activityMaster'], a['detail'], _cost_changed, cost_derived = sanitize_for_frontend(
        a.get('activityMaster') or {}, a.get('detail') or {})
    # ★ 价格是否「待定」：成本表来源且俱乐部尚未定价（priceFrom 非 'priced'）时，顶层 price 与
    # 每个团期 price 一并归零，避免把成本价当售价（老活动库里就存着 3806.55）。俱乐部一旦在编辑端
    # 设定真实售价，更新接口会把 master.priceFrom 置为 'priced'，这里便不再归零（非粘性）。
    _pending=(a.get('activityMaster') or {}).get('priceFrom')=='pending' or (cost_derived and (a.get('activityMaster') or {}).get('priceFrom')!='priced')
    if _pending:
        try:
            if float(a.get('price') or 0) > 0:
                a['price'] = 0
        except (TypeError, ValueError):
            pass
        a['priceFrom'] = 'pending'
        for _oc in (a.get('occurrences') or []):
            try:
                if isinstance(_oc, dict) and float(_oc.get('price') or 0) > 0:
                    _oc['price'] = 0
            except (TypeError, ValueError):
                pass
    # 出行清单 × 商城在售装备：让 C 端报名页能直接把清单变成可下单的装备推荐
    with conn() as _gc:a['gearRecommendations']=_gear_plan_for(_gc,a['activityMaster'],int(a['club_id']))
    # 带队领队：顾客挑团期时最想知道「谁带」，俱乐部端排好班之后这里必须看得见
    with conn() as _lc:a['leaders']=_public_leaders(_lc,activity_id,int(a['club_id']))
    cov=a.get('cover')
    if cov and str(cov).startswith('/static/'):
        a['cover']='/api/public/activities/%d/media/%s'%(activity_id,quote(str(cov)[len('/static/'):],safe='/'))
    a['pointsPolicy']=activity_points_policy.from_activity(a).as_dict()
    a['refundPolicy']=activity_refund_policy.from_activity(a).as_dict()
    a['participantPolicy']=participant_service.from_activity(a).as_dict()
    # 品牌 logo / 字标 / 空白底图属俱乐部内部素材；无论 PROD 还是 demo，C 端公开接口一律不曝光。
    # 仅放行 kind='photo' 的真实照片（kind 缺失的按照片处理，避免误删）。
    _m=a.get('activityMaster')
    if isinstance(_m,dict) and isinstance(_m.get('media'),list):
        _m['media']=[m for m in _m['media']
                     if isinstance(m,dict) and str(m.get('kind','')).lower() not in ('logo',)]
    if IS_PROD:
        # Public activity is not a dump of private activity_master_json (internalData).
        a={k:v for k,v in a.items() if k in ('id','club_id','title','event_date','location','price','capacity','status','cover','activityMaster','detail','occurrences','pointsPolicy','refundPolicy','participantPolicy','gearRecommendations','leaders')}
        # 领队只放行展示必需的六样：手机号一类内部联络信息即便将来被写进这张表，也出不去。
        # leaders 已经是活动级的扁平数组（见 _public_leaders），逐项过白名单即可。
        a['leaders']=[{k:v for k,v in x.items()
                       if k in ('name','role','credential','specialties','baseCity','avatarUrl')}
                      for x in (a.get('leaders') or [])]
        master=a.get('activityMaster') or {}
        if isinstance(master,dict):
            public_keys={'title','date','location','price','capacity','itinerary','fees','checklist','services','media'}
            master=sanitize_public_document({k:v for k,v in master.items() if k in public_keys})
            approved=[]
            for media in master.get('media',[]):
                if not isinstance(media,dict):continue
                if str(media.get('kind','')).lower()=='logo':continue   # 双保险：logo 在前一步已剔除，这里再拦一道
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

def _normalize_avatar_url(value) -> str|None:
    """收下头像地址，但只放行「领队头像代理」这一种形状。

    生产环境 security_v025 直接封死 /static/uploads/*，存裸链等于存了一条
    在俱乐部端和领队执行端都打不开的路径。这里把来源收敛到 club 域头像代理，
    任何别的写法（裸 uploads 路径、外部 URL、不带扩展名的地址）一律淘汰成
    None，让前端退回姓名首字占位 —— 宁可少一张图，也不能存一条读不回来的地址。
    """
    s=str(value or '').strip()
    if not s or s.lower().startswith(('http://','https://','data:','//')):
        return None
    if not re.fullmatch(r'/api/club/\d+/leaders/\d+/avatar\.[a-z0-9]{2,5}', s, re.I):
        return None
    return s

@app.get('/api/public/activities/{activity_id}/media/{asset_path:path}')
def public_activity_media(activity_id:int,asset_path:str):
    # Published-activity allowlist, NOT general access to /static/uploads.
    # Only files explicitly referenced in the published editorial master are readable.
    with conn() as c:
        a=row(c.execute("""SELECT a.activity_master_json,a.source_json,a.cover,cl.status club_status
            FROM activities a JOIN clubs cl ON cl.id=a.club_id
            WHERE a.id=? AND a.status='published' """,(activity_id,)))
    if not a or a['club_status']!='active':raise HTTPException(404,'not found')
    original='/static/'+unquote(asset_path)
    file_path=_safe_media_path(original)
    if not file_path:raise HTTPException(404,'not found')
    # 白名单必须与详情页看到的同一份清单：媒体 url 缺失时这里会误判 404，C 端整页图片打不开。
    master=_repair_master_media(jload(a['activity_master_json'],{}),_activity_source(a)[0])
    allowed={str(m.get('url')) for m in master.get('media',[]) if isinstance(m,dict) and str(m.get('kind','')).lower()!='logo'}
    cover=str(a.get('cover') or '')
    if cover: allowed.add(cover)
    if original not in allowed or not file_path.is_file():raise HTTPException(404,'not found')
    response=FileResponse(file_path)
    response.headers['Cache-Control']='public, max-age=300'
    return response

@app.get('/api/club/{club_id}/activities/{activity_id}/media/{asset_path:path}')
def club_activity_media(club_id:int,activity_id:int,asset_path:str):
    """俱乐部后台自己的活动媒体读取（海报合成要把品牌 logo 盖上去，而公开代理按设计
       永远不曝光 kind='logo'，也不服务未发布活动）。会话安全由 /api/club/ 中间件统一把守
       （role=club 且 path club_id 必须等于会话 club）；白名单=该活动 master.media + cover，
       不是对 /static/uploads 通配开放。"""
    with conn() as c:
        a=row(c.execute('SELECT activity_master_json,source_json,cover FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
    if not a:raise HTTPException(404,'not found')
    original='/static/'+unquote(asset_path)
    file_path=_safe_media_path(original)
    if not file_path:raise HTTPException(404,'not found')
    master=_repair_master_media(jload(a['activity_master_json'],{}),_activity_source(a)[0])
    allowed={str(m.get('url')) for m in master.get('media',[]) if isinstance(m,dict) and m.get('url')}
    cover=str(a.get('cover') or '')
    if cover: allowed.add(cover)
    if original not in allowed or not file_path.is_file():raise HTTPException(404,'not found')
    response=FileResponse(file_path)
    response.headers['Cache-Control']='private, max-age=60'
    return response

def _public_price_pending(c, activity_id:int, master:dict|None=None) -> bool:
    """这个活动当前有没有「可对外报的价格」。判定口径与 public_activity/get_activity 完全一致。

    ★为什么报价/下单也必须查这条：这两条链路直接读 activity_occurrences.price，
    绕过了展示层清洗。老活动的团期价就是从成本表推出来的内部单价（=合计÷人数），
    一旦放行，顾客会在「订单摘要」里看到 ¥3,806.55 并真的按这个价下单 ——
    这是把俱乐部底价当对外售价卖出去，比多显示一行文案严重得多。
    没有对外售价时正确做法是**不报价、不可下单**，而不是拿成本价兜底。
    """
    if master is None:
        master = jload(row(c.execute('SELECT activity_master_json FROM activities WHERE id=?',(activity_id,)))['activity_master_json'],{}) or {}
    if (master.get('priceFrom') or '') == 'priced':
        return False                      # 俱乐部已明确定价 → 正常卖
    return master_has_cost_evidence(master) or (master.get('priceFrom') == 'pending')


@app.get('/api/public/activities/{activity_id}/price-quote')
def price_quote(activity_id:int,occurrence_id:int=Query(...),user_id:int=1,club_points:int=0,gear_points:int=0,voucher_codes:str='',participant_count:int=1):
    codes=[x.strip() for x in str(voucher_codes or '').split(',') if x.strip()]
    with conn() as c:
        # 成本闸门：没有对外售价就不报价。宁可让顾客看到「价格待定，请联系俱乐部」，
        # 也绝不能把成本底价当成售价报出去（这条链路不经过展示层清洗）。
        if _public_price_pending(c, activity_id):
            raise HTTPException(409,'该活动价格待定，请联系俱乐部咨询')
        try:
            q=booking_engine.quote(c,activity_id=activity_id,occurrence_id=occurrence_id,user_id=user_id,requested_club_points=club_points,requested_gear_points=gear_points,participant_count=max(1,participant_count))
            benefits=benefit_engine.quote_vouchers(c,voucher_codes=codes,user_id=user_id,club_id=int(q.occurrence['club_id']),kind='activity',amount_available=float(q.points['payable']))
        except ValueError as e:
            raise HTTPException(409,str(e))
    out=q.points; out['wallet']=q.wallet; out['occurrence']=q.occurrence; out['pointsPolicy']=q.points_policy
    # C 端不再让顾客自己填要抵多少积分：把「这张钱包在本单最多能抵多少」直接算好交给前端，
    # 顾客只做「用/不用」的确认。算法与真正下单时的封顶逻辑同源（points_engine.max_redeemable）。
    out['maxRedeemable']=q.max_redeemable
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
        # 成本闸门（与 price-quote 同口径）：老活动的团期价可能是成本表推出来的内部单价，
        # 这里不放行就等于按俱乐部底价成交。
        if _public_price_pending(c, activity_id, jload(a.get('activity_master_json'),{}) or {}):
            raise HTTPException(409,'该活动价格待定，请联系俱乐部咨询')
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
            q=max(1,int(it.get('quantity',1))); p=dict(p)
            # 规格：传了 variantId 就必须属于这个商品；没传就取默认规格。
            # 库存已经下沉到规格层，所以库存校验一律读规格的 stock —— 继续读
            # products.stock 会让「某个规格已售罄、商品汇总还有货」的商品被超卖。
            vid=it.get('variantId')
            v=row(c.execute('SELECT * FROM product_variants WHERE id=? AND product_id=? AND status="active"',(int(vid),int(it['productId'])))) if vid else \
              row(c.execute('SELECT * FROM product_variants WHERE product_id=? AND status="active" ORDER BY is_default DESC,sort,id LIMIT 1',(int(it['productId']),)))
            if vid and not v: raise HTTPException(400,'规格不存在或已下架')
            if v:
                p['_variantId']=int(v['id']); p['_variantName']=v['name']
                if v['price'] is not None: p['price']=float(v['price'])
                stock=int(v['stock'] or 0)
            else:
                stock=int(p['stock'] or 0)
            if stock<q: raise HTTPException(409,f"{p['name']}{(' · '+p['_variantName']) if p.get('_variantName') else ''} 库存不足")
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
        # 成本闸门：demo 直报端点同样不能按成本价成交
        if _public_price_pending(c, activity_id, jload(a.get('activity_master_json'),{}) or {}):
            raise HTTPException(409,'该活动价格待定，请联系俱乐部咨询')
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
def _attach_checkin_status(c,participants):
    """给参加人补上签到状态（就地改字典，返回同一批对象）。

    C 端以前完全看不到自己签上没签上，只能等工作人员口头告知 —— 顾客付了钱、
    人也到了现场，理应能自己确认这件事，而不是全靠人传话。
    注意：自助扫码签到是另一件事（要 C 端出码 + 执行端扫码两侧配套），这里只做「看得见」。
    """
    pids=[int(p['id']) for p in (participants or []) if p.get('id') is not None]
    if not pids: return participants
    ph=','.join('?'*len(pids))
    def _latest(where_extra,args):
        return {int(r['participant_id']):dict(r) for r in c.execute(
            f'''SELECT ci.* FROM participant_checkins ci
                JOIN (SELECT participant_id,MAX(id) AS mid FROM participant_checkins
                      WHERE participant_id IN ({ph}){where_extra} GROUP BY participant_id) m
                  ON ci.id=m.mid''',args)}
    # 「签到」默认是出发集合（departure）—— 执行端统计已签到人数也是按这个口径，
    # C 端不能按另一套口径显示，否则会出现「我这显示已签到、他那显示没到」。
    latest=_latest(" AND checkin_type='departure'",pids)
    fallback=_latest('',pids)
    for p in (participants or []):
        rec=latest.get(int(p['id'])) or fallback.get(int(p['id']))
        p['checkinStatus']=(rec or {}).get('status') or 'not_checked_in'
        p['checkedAt']=(rec or {}).get('checked_at')
        p['checkinType']=(rec or {}).get('checkin_type')
    return participants

def public_registration_participants(registration_id:int):
    with conn() as c:
        reg=row(c.execute('SELECT * FROM registrations WHERE id=?',(registration_id,)))
        if not reg: raise HTTPException(404,'报名记录不存在')
        out={'registrationId':registration_id,'participantPolicy':jload(reg.get('participant_policy_snapshot_json'),{}),
             **participant_service.summary_for_registration(c,registration_id)}
        _attach_checkin_status(c,out.get('participants') or [])
        return out

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
        # 生产环境保留确定性的 local/demo 生命周期兼容：approval 进入 processing 后由真实渠道的
        # refund-succeeded 事件回调完成。非生产（demo/local）环境直接 finalize，让装备积分回退、
        # 订单进入终态，避免售后退款卡在 local_waiting 永远不完成（与 platform_refund_order 行为一致）。
        if IS_PROD:
            with conn() as c:
                c.execute("UPDATE refund_requests SET provider_status='local_waiting',updated_at=CURRENT_TIMESTAMP WHERE id=?",(refund_id,))
            return {'ok':True,'refundRequestId':refund_id,'status':'processing','providerStatus':'local_waiting','provider':'local',
                    'cashAmount':float(rr.get('cash_amount') or 0),'originalCashAmount':float(rr.get('original_cash_amount') or rr.get('cash_amount') or 0),
                    'refundPercent':float(rr.get('refund_percent') or 0),'retainedCashAmount':float(rr.get('retained_cash_amount') or 0),
                    'nextAction':'SIMULATE_PROVIDER_REFUND_CALLBACK'}
        with conn() as c:
            out=refund_lifecycle.finalize(c,refund_id=refund_id,provider_refund_id='local_after_sales',provider='local')
        return {**out,'providerStatus':'succeeded','provider':'local'}
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

@app.get('/api/public/clubs/{club_id}/mall/products/{product_id}')
def public_product(club_id:int, product_id:int, user_id:int|None=None):
    # 详情页要能被单独打开（刷新、分享出去的链接），所以不能只靠列表缓存。
    if IS_PROD and club_or_404(club_id)['status']!='active':raise HTTPException(404,'not found')
    with conn() as c:
        p=row(c.execute('SELECT id,name,sku,price,stock,status,image_url,category FROM products WHERE id=? AND status="active"',(product_id,)))
        if not p: raise HTTPException(404,'商品不存在')
        out=_with_extras(c,[p])[0]
        # 会员价要能被看见：会员中心写着「装备商城 X 折 —— 在装备商城里直接看到会员价」，
        # 商品页却只显示原价，等于承诺了一个看不见的价格。带 user_id 时把会员价一并回传。
        if user_id:
            rate,_=checkout_engine._member_gear_discount(c,club_id=club_id,user_id=int(user_id),gross=float(out.get('price') or 0))
            if rate<1:
                out['gearDiscount']=rate
                out['memberPrice']=round(float(out.get('price') or 0)*rate,2)
                for v in (out.get('variants') or []):
                    if v.get('price') is not None:
                        v['memberPrice']=round(float(v['price'])*rate,2)
        return out

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
        # C 端「户外能力」页的数据只能来自真实存在的记录：报名履历 + 买过的装备。
        # 没有 skill/ability 表，所以这里不造能力值，只把真实消费记录交出去，
        # 页面上怎么归纳是前端的展示问题。
        history=rows(c.execute('''SELECT r.id,r.activity_id,r.amount,r.status,r.participant_count,r.created_at,r.refund_status,
                                         a.title,a.location,a.event_date,a.cover
                                  FROM registrations r JOIN activities a ON a.id=r.activity_id
                                  WHERE r.user_id=? AND r.club_id=? ORDER BY r.id DESC LIMIT 50''',(user_id,club_id)))
        owned=rows(c.execute('''SELECT i.product_id,i.quantity,i.unit_price,i.variant_name,p.name product_name,p.category,p.image_url
                                FROM gear_order_items i JOIN gear_orders o ON o.id=i.order_id
                                LEFT JOIN products p ON p.id=i.product_id
                                WHERE o.user_id=? ORDER BY o.id DESC LIMIT 50''',(user_id,)))
    return {'member':member,'wallet':wallet,'benefits':benefits,'redemptions':redemptions,'tiers':tiers,
            'activityHistory':history,'gearOwned':owned}

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
        # checkout_intents 与 registrations / gear_orders 之间没有外键，只有 user_id 能串起来。
        # C 端「继续支付 / 取消订单」需要结算单号，所以在这里按 kind + payload 内容把待支付单对回订单，
        # 否则用户关掉支付面板后这笔单会永久停在 pending_payment，界面上没有任何可操作的出口。
        pending_intents=rows(c.execute("SELECT * FROM checkout_intents WHERE user_id=? AND status='pending_payment' ORDER BY created_at DESC",(user_id,)))
        def _pending_summary(it,subject=''):
            return {'checkoutId':it['id'],'kind':it['kind'],'subject':subject,
                    'cashAmount':float(it['cash_amount'] or 0),
                    'paymentStatus':str(it.get('payment_status') or 'pending'),
                    'createdAt':it.get('created_at')}
        for x in activity_orders:
            ps=participant_service.summary_for_registration(c,int(x['id']))
            x.update(ps)
            _attach_checkin_status(c,x.get('participants') or [])
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
        # 待支付单在 confirming 之前不会落一行 registrations / gear_orders，所以它不会出现在
        # 上面两个列表里 —— 这正是它会卡死的根因。直接从结算单解析，不能挂在订单行下（那样必然查不到）。
        pending_orders=[]
        for it in pending_intents:
            payload=jload(it['payload_json'],{})
            subject=''
            if it['kind']=='activity':
                aid=payload.get('activityId')
                ra=row(c.execute('SELECT title FROM activities WHERE id=?',(aid,))) if aid else None
                subject=(ra or {}).get('title') or ''
            elif it['kind']=='gear':
                names=[]
                for p in (payload.get('items') or []):
                    pid=p.get('productId')
                    if not pid or pid in names:continue
                    rp=row(c.execute('SELECT name FROM products WHERE id=?',(pid,))) or {}
                    if rp.get('name'):names.append(rp['name'])
                subject=' / '.join(names)
            pending_orders.append({**_pending_summary(it,subject),'orderKind':it['kind']})
        pending_orders.sort(key=lambda y:str(y.get('createdAt') or ''),reverse=True)
        combined=[]
        for x in activity_orders: combined.append({'kind':'activity','createdAt':x.get('created_at'),'order':x})
        for x in gear_orders: combined.append({'kind':'gear','createdAt':x.get('created_at'),'order':x})
        combined.sort(key=lambda y:str(y.get('createdAt') or ''),reverse=True)
    return {'activityOrders':activity_orders,'gearOrders':gear_orders,'allOrders':combined,'pendingOrders':pending_orders}

@app.get('/api/public/users/{user_id}/orders')
def user_orders(user_id:int):
    # Backward-compatible gear-only endpoint. New C-end should use /order-center.
    with conn() as c:return rows(c.execute('SELECT id,source_club_id,total,cash_paid,status,tracking_no,carrier,after_sales_status,payment_status,refund_status,created_at FROM gear_orders WHERE user_id=? ORDER BY id DESC',(user_id,)))

@app.post('/api/public/orders/{order_id}/confirm-receipt')
def confirm_receipt(order_id:int,payload:dict=Body(default={})):
    # C 端「确认收货」：仅会员本人可操作，且仅在已发货(shipped)时允许签收 → delivered，
    # 同时冻结该订单的佣金（进入售后保障期）。生产环境签收应由平台端 PATCH delivered 完成，
    # 这里给 C 端用户一个自助签收出口，避免订单永远停在 shipped。
    user_id=int(payload.get('userId') or 0)
    if not user_id: raise HTTPException(400,'userId required')
    with conn() as c:
        o=row(c.execute('SELECT * FROM gear_orders WHERE id=?',(order_id,)))
        if not o: raise HTTPException(404,'装备订单不存在')
        if int(o.get('user_id') or 0)!=user_id: raise HTTPException(403,'该订单不属于当前用户')
        if o['status']=='delivered':
            return {'ok':True,'orderId':order_id,'status':'delivered','idempotent':True}
        if o['status']!='shipped':
            raise HTTPException(409,f"当前订单状态({o['status']})不能确认收货")
        c.execute("UPDATE gear_orders SET status='delivered',delivered_at=CURRENT_TIMESTAMP WHERE id=?",(order_id,))
        commission=commission_engine.freeze_order(c,order_id=order_id)
    return {'ok':True,'orderId':order_id,'status':'delivered','commission':commission}

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
    # 规格数与图数一起带出来：后台列表要能一眼看出哪些商品还没维护多图/规格，
    # 逐个商品回查会退化成 N+1。
    with conn() as c:return rows(c.execute('''SELECT p.*,
        (SELECT COUNT(*) FROM product_variants v WHERE v.product_id=p.id AND v.status='active') variants_count,
        (SELECT COUNT(*) FROM product_images i WHERE i.product_id=p.id) images_count
      FROM products p ORDER BY p.id DESC'''))


def _resync_product_stock(c, product_id:int):
    """把 products.stock 重算成各在售规格库存之和。

    它现在是冗余字段（真源在 product_variants），只要规格有任何增删改都要跟着变，
    否则后台列表看到的汇总和 C 端详情页看到的规格明细会对不上。
    """
    total=c.execute("SELECT COALESCE(SUM(stock),0) s FROM product_variants WHERE product_id=? AND status='active'",(int(product_id),)).fetchone()['s']
    c.execute('UPDATE products SET stock=? WHERE id=?',(int(total),int(product_id)))
    return int(total)

@app.get('/api/platform/products/{product_id}/variants')
def platform_variants(product_id:int):
    with conn() as c:
        return rows(c.execute("SELECT id,product_id,name,sku,price,stock,status,sort,is_default FROM product_variants WHERE product_id=? ORDER BY sort,id",(product_id,)))

@app.post('/api/platform/products/{product_id}/variants')
def add_product_variant(product_id:int, payload:dict=Body(...)):
    name=str(payload.get('name') or '').strip()
    if not name: raise HTTPException(400,'规格名不能为空，例如 S / M / L')
    with conn() as c:
        if not c.execute('SELECT id FROM products WHERE id=?',(product_id,)).fetchone():
            raise HTTPException(404,'商品不存在')
        price=payload.get('price'); price=float(price) if price not in (None,'') else None
        stock=max(0,int(payload.get('stock') or 0))
        # 商品原本只有迁移时生成的那个「默认」规格：加了真实规格后它就该退场，
        # 否则 C 端会把它和 S/M/L 并排显示成四个规格。它的库存转给第一个新规格，
        # 不让库存凭空消失 —— 运营随后再按规格分配。
        dflt=c.execute("SELECT id,stock FROM product_variants WHERE product_id=? AND is_default=1 AND status='active'",(product_id,)).fetchone()
        others=c.execute("SELECT COUNT(*) n FROM product_variants WHERE product_id=? AND status='active' AND is_default=0",(product_id,)).fetchone()['n']
        moved=0
        if dflt and others==0:
            moved=int(dflt['stock'] or 0)
            c.execute("UPDATE product_variants SET status='inactive',stock=0 WHERE id=?",(dflt['id'],))
            stock=stock or moved
        mx=c.execute("SELECT COALESCE(MAX(sort),0) m FROM product_variants WHERE product_id=?",(product_id,)).fetchone()['m']
        c.execute('INSERT INTO product_variants(product_id,name,sku,price,stock,sort,status) VALUES(?,?,?,?,?,?,?)',
                  (product_id,name,(payload.get('sku') or None),price,stock,int(mx)+1,'active'))
        vid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        _resync_product_stock(c,product_id)
        return {'ok':True,'id':vid,'movedStock':moved}

@app.patch('/api/platform/products/{product_id}/variants/{variant_id}')
def patch_product_variant(product_id:int, variant_id:int, payload:dict=Body(...)):
    with conn() as c:
        v=c.execute('SELECT * FROM product_variants WHERE id=? AND product_id=?',(variant_id,product_id)).fetchone()
        if not v: raise HTTPException(404,'规格不存在')
        sets=[];vals=[]
        for k,col in (('name','name'),('sku','sku'),('sort','sort'),('status','status')):
            if payload.get(k) is not None: sets.append(col+'=?');vals.append(payload[k])
        if payload.get('price') is not None:
            raw=payload['price']; sets.append('price=?');vals.append(float(raw) if raw!='' else None)
        if payload.get('stock') is not None:
            sets.append('stock=?');vals.append(max(0,int(payload['stock'])))
        if not sets: raise HTTPException(400,'没有要修改的字段')
        vals+= [variant_id]
        c.execute('UPDATE product_variants SET '+(', '.join(sets))+' WHERE id=?',vals)
        _resync_product_stock(c,product_id)
        return {'ok':True}

@app.delete('/api/platform/products/{product_id}/variants/{variant_id}')
def delete_product_variant(product_id:int, variant_id:int):
    with conn() as c:
        v=c.execute('SELECT * FROM product_variants WHERE id=? AND product_id=?',(variant_id,product_id)).fetchone()
        if not v: raise HTTPException(404,'规格不存在')
        c.execute('DELETE FROM product_variants WHERE id=?',(variant_id,))
        # 删到只剩 0 个规格时补回一个「默认」：库存真源在规格层，
        # 一个规格都没有的商品在 C 端会读不到库存、直接变成缺货。
        left=c.execute("SELECT COUNT(*) n FROM product_variants WHERE product_id=? AND status='active'",(product_id,)).fetchone()['n']
        if left==0:
            p=c.execute('SELECT sku,stock FROM products WHERE id=?',(product_id,)).fetchone()
            c.execute("INSERT INTO product_variants(product_id,name,sku,stock,sort,status,is_default) VALUES(?,?,?,?,?,'active',1)",
                      (product_id,'默认',p['sku'],int(p['stock'] or 0),0))
        _resync_product_stock(c,product_id)
        return {'ok':True,'restoredDefault':left==0}

@app.get('/api/platform/products/{product_id}/images')
def platform_product_images(product_id:int):
    with conn() as c:
        return rows(c.execute('SELECT id,product_id,url,sort FROM product_images WHERE product_id=? ORDER BY sort,id',(product_id,)))

@app.post('/api/platform/products/{product_id}/images')
def add_product_image(product_id:int, payload:dict=Body(...)):
    url=str(payload.get('url') or '').strip()
    if not url: raise HTTPException(400,'图片地址不能为空')
    with conn() as c:
        if not c.execute('SELECT id FROM products WHERE id=?',(product_id,)).fetchone():
            raise HTTPException(404,'商品不存在')
        n=c.execute('SELECT COUNT(*) n FROM product_images WHERE product_id=?',(product_id,)).fetchone()['n']
        c.execute('INSERT INTO product_images(product_id,url,sort) VALUES(?,?,?)',(product_id,url,int(n)))
        iid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        # 第一张图同时写回 products.image_url：老接口（列表卡、Medusa 同步）读的是它，
        # 不回写的话后台卡片和 C 端详情会显示成两个不同的图。
        if n==0: c.execute('UPDATE products SET image_url=? WHERE id=?',(url,product_id))
        return {'ok':True,'id':iid}

@app.delete('/api/platform/products/{product_id}/images/{image_id}')
def delete_product_image(product_id:int, image_id:int):
    with conn() as c:
        c.execute('DELETE FROM product_images WHERE id=? AND product_id=?',(image_id,product_id))
        first=c.execute('SELECT url FROM product_images WHERE product_id=? ORDER BY sort,id LIMIT 1',(product_id,)).fetchone()
        c.execute('UPDATE products SET image_url=? WHERE id=?',(first['url'] if first else None,product_id))
        return {'ok':True}


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

@app.get('/api/platform/product-visibility-requests')
def platform_product_visibility_requests(status:str|None=None):
    """总平台处理俱乐部提交的上下架申请。只有这里能真正改 products.status，
    C 端商城的可见性随之变化 —— 这是俱乐部改不动但确实生效的那一步。"""
    with conn() as c:
        q='''SELECT r.*,cl.name club_name,p.name product_name,p.sku,p.status product_status
             FROM club_product_visibility_requests r
             JOIN clubs cl ON cl.id=r.club_id JOIN products p ON p.id=r.product_id'''
        args=[]
        if status: q+=' WHERE r.status=?'; args.append(status)
        return rows(c.execute(q+' ORDER BY r.id DESC',args))

@app.post('/api/platform/product-visibility-requests/{request_id}/decide')
def platform_decide_product_visibility(request_id:int,payload:dict=Body(...)):
    decision=str(payload.get('decision') or '').strip()
    if decision not in {'approved','rejected'}: raise HTTPException(400,'decision 只能是 approved / rejected')
    with conn() as c:
        r=row(c.execute('SELECT * FROM club_product_visibility_requests WHERE id=?',(request_id,)))
        if not r: raise HTTPException(404,'申请不存在')
        if r['status']!='pending':
            return {'ok':True,'requestId':request_id,'status':r['status'],'productStatus':None,'note':'该申请已处理过，未重复处理'}
        c.execute("""UPDATE club_product_visibility_requests SET status=?,decided_note=?,decided_at=CURRENT_TIMESTAMP WHERE id=?""",
                  (decision,str(payload.get('note') or '')[:200],request_id))
        new_status=None
        if decision=='approved':
            new_status='active' if r['action']=='on' else 'inactive'
            c.execute('UPDATE products SET status=? WHERE id=?',(new_status,r['product_id']))
    return {'ok':True,'requestId':request_id,'status':decision,'productStatus':new_status}

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
    # packed 让平台端订单行能判断「还该不该显示打包按钮」—— 打包结果只落在 warehouse_tasks，
    # 订单行自己看不出来，否则打完包按钮还挂在那里。
    with conn() as c:
        packed={r['order_id'] for r in rows(c.execute("SELECT DISTINCT order_id FROM warehouse_tasks WHERE task_type='pack' AND status='packed'"))}
        out=rows(c.execute('SELECT o.*,u.name buyer,cl.name source_club FROM gear_orders o JOIN users u ON u.id=o.user_id JOIN clubs cl ON cl.id=o.source_club_id ORDER BY o.id DESC'))
        for x in out:x['packed']=x['id'] in packed
        return out

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


@app.post('/api/insurance/webhook')
def insurance_webhook(payload:dict=Body(...), x_insurance_webhook_secret:str|None=Header(default=None)):
    """保险公司异步承保/退保回执。幂等：按 policyNo 回写参加人与 insurance_jobs；重复回调安全。

    适用场景：真实保险方在投保/退保受理后立即返回保单号、随后异步回执确认承保/退保成功。
    Mock 提供方同步承保，不会触发本接口；本接口作为对账与异步确认的安全兜底。
    """
    expected=os.getenv('INSURANCE_WEBHOOK_SECRET','').strip()
    if expected and x_insurance_webhook_secret != expected: raise HTTPException(401,'invalid insurance webhook secret')
    policy_no=str(payload.get('policyNo') or '').strip()
    action=str(payload.get('action') or '').strip().lower()
    status=str(payload.get('status') or '').strip().lower()
    if not policy_no or action not in ('enroll','cancel') or status not in ('success','failed'):
        raise HTTPException(400,'policyNo, action(enroll|cancel), status(success|failed) required')
    effective_at=str(payload.get('effectiveAt') or '') or None
    expire_at=str(payload.get('expireAt') or '') or None
    premium=float(payload.get('premium') or 0) or None
    with conn() as c:
        participants=[dict(r) for r in c.execute('SELECT * FROM registration_participants WHERE insurance_policy_no=?',(policy_no,)).fetchall()]
        if not participants:
            return {'ok':True,'idempotent':True,'note':'no matching participant'}
        for p in participants:
            pid=int(p['id'])
            if action=='enroll' and status=='success':
                c.execute('''UPDATE registration_participants SET insurance_status='insured',
                    effective_at=COALESCE(?,effective_at),expire_at=COALESCE(?,expire_at),
                    premium_amount=COALESCE(?,premium_amount),insured_at=COALESCE(insured_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                    (effective_at,expire_at,premium,pid))
            elif action=='enroll' and status=='failed':
                c.execute("UPDATE registration_participants SET insurance_status='failed',updated_at=CURRENT_TIMESTAMP WHERE id=?",(pid,))
            elif action=='cancel' and status=='success':
                c.execute("UPDATE registration_participants SET insurance_status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE id=?",(pid,))
            elif action=='cancel' and status=='failed':
                c.execute("UPDATE registration_participants SET insurance_status='cancel_failed',updated_at=CURRENT_TIMESTAMP WHERE id=?",(pid,))
        c.execute('''UPDATE insurance_jobs SET status=?,provider_payload_json=COALESCE(?,provider_payload_json),updated_at=CURRENT_TIMESTAMP
                    WHERE policy_no=? AND action=?''',
                  ('done' if status=='success' else 'failed', jdump(payload), policy_no, action))
    return {'ok':True,'policyNo':policy_no,'action':action,'status':status,'participants':len(participants)}


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
