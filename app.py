from __future__ import annotations
import uuid, os, io, csv, re, json, zipfile, shutil, asyncio, secrets, hmac, hashlib, base64, subprocess, time, logging
logger=logging.getLogger('clubos')
from sqlite3 import IntegrityError
from datetime import datetime, timedelta, date
from pathlib import Path
from urllib.parse import unquote, quote, urlsplit
import urllib.request, urllib.error
from PIL import Image
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body, Query, Header, Request, BackgroundTasks, Response
from fastapi.responses import FileResponse, StreamingResponse, PlainTextResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db import init_db, conn, row, rows, jdump, jload, setting
from document_parser import save_uploads, parse_sources, _image_kind, IMAGE_EXTS as _IMAGE_UPLOAD_EXTS
from cost_guard import sanitize_for_frontend, master_has_cost_evidence
from cost_guard import redact_cost_text
from link_source import fetch_link, LinkError
from ai_engine import (generate_activity, generate_channel, regenerate_detail, detail_outline,
                       _fact_digest, pick_cover_media, generate_recap, pick_detail_style,
                       _DETAIL_STYLES)
from image_gen import generate_hero, save_hero_image, build_hero_prompt
from longpic import generate_longpic, build_fact_pack
from image_caption import caption_media, caption_lines, persist_captions
from ocr import ocr_image_text
from ai_billing import ensure_credits, charge_credits, credit_cost
from ai_gateway import (gateway_status, AIGatewayError, platform_provider_config, _record_usage,
                        update_platform_provider_config, test_provider_connection, effective_gateway_mode)
from clubos_domain.club_analytics import ClubBusinessIntelligence
from clubos_domain import club_biz
from clubos_domain import club_home
from clubos_domain import c_profile
from clubos_domain.gear_points_policy import GearPointsPolicyService
from clubos_domain.commission_rate import ClubCommissionRateService
from clubos_domain.product_catalog import ProductCatalogService
from clubos_domain.points_redeem_rate import (resolve as resolve_club_points_redeem_rate,
                                              describe as describe_club_points_redeem_rate,
                                              validate_rate as validate_points_redeem_rate)
from clubos_domain import ClubOSPointsEngine, BookingEngine, CheckoutEngine, ActivityPointsPolicyService, ActivityRefundPolicyService, MembershipEngine, BenefitEngine, CommerceRefundEngine, PaymentLifecycleEngine, RefundLifecycleEngine, ParticipantService, ActivityExecutionService, CommissionSettlementEngine, AfterSalesEngine, ProcurementEngine, WarehouseEngine, MerchandiseFinanceEngine, CommerceAnalyticsEngine, AICreditEngine, GearRecommendService, LeaderRecommendService
from commerce_adapter import commerce_status, commerce_provider, MedusaClient
from payment_providers import PaymentAccountService, provider_for_account, merchant_order_no, provider_refund_no
import logistics_engine
from clubos_domain import stock_control as stock_control
from clubos_domain import peaktrace_catalog
from clubos_domain import external_fulfillment

ROOT=Path(__file__).resolve().parent
STATIC=ROOT/'static'
UPLOAD=STATIC/'uploads'
from security_v025 import (IS_PROD, sanitize_public_document, validate_production_config, install_tables, authorize_request,
    issue_session, get_identity, revoke_session, record_audit, hash_password)
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
        request.state.identity=identity
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
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Content-Security-Policy']="frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
        response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
    # ★ 动态 JSON 一律禁缓存，且**不再只写在 IS_PROD 分支里**（2026-10-10 用户反馈
    #   「后台重新生成一版之后 C 端还是旧版」）：线上跑的是 demo/内测模式，原先的
    #   /api/ no-store 只在 IS_PROD 生效 —— 于是活动详情 JSON 响应**一个缓存头都不带**，
    #   浏览器与前置网关就可能按启发式策略留一份旧 JSON，后台换版后顾客刷新拿到的还是上一版。
    #   这条必须放在 IS_PROD 之外、且放在可能重建 response 的审计分支之后。
    response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api/') else 'private, no-store'
    response.headers['X-Request-ID']=request_id
    return response

@app.get('/login')
def login_page():
    # 内测开放模式（CLUBOS_TEST_OPEN=1）下没有登录这回事，直接送进俱乐部后台，
    # 避免老书签/跳转把测试同学挡在登录页。
    from security_v025 import TEST_OPEN
    if TEST_OPEN: return RedirectResponse('/club', status_code=307)
    return FileResponse(STATIC/'login.html')

@app.post('/api/auth/login')
def login_account(request:Request,payload:dict=Body(...)):
    token,csrf,identity=issue_session(request,str(payload.get('username') or ''),str(payload.get('password') or ''))
    response=JSONResponse({'ok':True,'identity':identity,'token':token,'csrf':csrf})
    response.set_cookie('clubos_session',token,httponly=True,secure=IS_PROD,samesite='strict',max_age=3600,path='/')
    response.set_cookie('clubos_csrf',csrf,httponly=False,secure=IS_PROD,samesite='strict',max_age=3600,path='/')
    return response

@app.get('/api/auth/me')
def auth_me(request:Request):
    from security_v025 import public_identity, TEST_OPEN, test_open_identity
    identity,_=get_identity(request)
    # 内测开放模式：即使浏览器残留过期会话 cookie，也返回一个可用的俱乐部身份，
    # 避免前端被弹回登录页（CLUBOS_TEST_OPEN=1，仅内测）。
    if not identity and TEST_OPEN: identity=test_open_identity()
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

def public_user_id(request:Request,supplied=None):
    identity=getattr(request.state,'identity',None)
    if identity and identity.get('role')=='member':
        uid=identity.get('user_id')
        if uid is None or (supplied is not None and str(supplied)!=str(uid)):
            raise HTTPException(403,'permission denied')
        return int(uid)
    if IS_PROD: raise HTTPException(401,'authentication required')
    try: return int(supplied if supplied is not None else 1)
    except (TypeError,ValueError): raise HTTPException(400,'用户编号须为整数')

class MembershipPurchase(BaseModel):
    userId:int|None=None
    tierId:int=Field(gt=0,strict=True)


def user_or_create(c,name:str,phone:str):
    u=row(c.execute('SELECT * FROM users WHERE phone=?',(phone,))) if phone else None
    if u:return u['id']
    c.execute('INSERT INTO users(name,phone) VALUES(?,?)',(name or '访客',phone)); return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])


def _wechat_jscode2session(appid:str, secret:str, code:str) -> dict:
    """调用微信 jscode2session 换取 openid（含 unionid）。失败抛可读 ValueError。

    注意：appid 来自 club_miniprogram 登记，secret 不入库、从环境变量取
    （WECHAT_MINIPROGRAM_SECRET 或 WECHAT_MINIPROGRAM_SECRET_<club_id>），避免密钥落库。
    """
    url=('https://api.weixin.qq.com/sns/jscode2session?appid=%s&secret=%s'
         '&js_code=%s&grant_type=authorization_code') % (quote(appid),quote(secret),quote(code))
    try:
        req=urllib.request.Request(url, headers={'User-Agent':'ClubOS/1.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data=json.loads(resp.read().decode('utf-8'))
    except urllib.error.URLError:
        raise ValueError('微信登录服务暂时不可用（网络异常）')
    except Exception:
        raise ValueError('微信登录服务返回异常')
    if data.get('errcode'):
        raise ValueError('微信登录失败：%s' % data.get('errmsg','errcode=%s'%data.get('errcode')))
    if not data.get('openid'):
        raise ValueError('微信登录未返回 openid')
    return data


def _member_uid_from_request(request) -> int | None:
    """仅信任经 get_identity 验过的会员会话 token（X-ClubOS-Token 等穿透网关的载体），
    绝不读请求体里的 userId —— 否则可被越权把订单挂到他人账户。

    用于「微信登录用户下单归属同一会员」：有合法会员 token 时用其 user_id，
    否则回落到手机号 user_or_create（匿名/手机号流程不变）。
    """
    try:
        identity,_=get_identity(request)
    except Exception:
        return None
    if identity and identity.get('role')=='member' and identity.get('user_id'):
        try: return int(identity['user_id'])
        except (TypeError,ValueError): return None
    return None


def wallet_snapshot(c,user_id:int,club_id:int):
    m=row(c.execute('SELECT club_points_balance,level,lifetime_activity_spend,activity_count FROM club_members WHERE user_id=? AND club_id=?',(user_id,club_id))) or {'club_points_balance':0,'level':'非会员','lifetime_activity_spend':0,'activity_count':0}
    g=row(c.execute('SELECT balance FROM gear_point_accounts WHERE user_id=?',(user_id,))) or {'balance':0}
    return {'clubPoints':int(m['club_points_balance'] or 0),'gearPoints':int(g['balance'] or 0),'memberLevel':m['level'],'lifetimeActivitySpend':float(m.get('lifetime_activity_spend') or 0),'activityCount':int(m.get('activity_count') or 0)}

points_engine=ClubOSPointsEngine(setting)
activity_points_policy=ActivityPointsPolicyService(setting)
# 装备积分抵用规则（2026-10-24）：套餐决定资格，总平台按俱乐部设定比例。
gear_points_policy=GearPointsPolicyService(setting)
activity_refund_policy=ActivityRefundPolicyService()
membership_engine=MembershipEngine()
benefit_engine=BenefitEngine(wallet_snapshot)
# 会员生命周期钩子（2026-10-26）：新会员入会自动发「邀请优惠券」，升级到设定等级自动发「升级赠券」。
# 防重由 benefits 层负责（tier_reward_log 唯一索引 / referral 每人一张），异常在 membership 层兜底。
membership_engine.on_member_created=benefit_engine.welcome_new_member
membership_engine.on_upgrade=benefit_engine.process_tier_rewards
# 积分商城（2026-10-26）：俱乐部用活动积分上架优惠券/活动/服务/实物兑换。
from clubos_domain.points_mall import PointsMallEngine
points_mall_engine=PointsMallEngine(benefit_engine)
participant_service=ParticipantService()
execution_service=ActivityExecutionService()
commission_engine=CommissionSettlementEngine(setting)
# 外部履约桥：已签收 → 冻结佣金（域模块不能反向 import app 的 commission_engine 实例，这里注入）
external_fulfillment.on_delivered=lambda c,order_id: commission_engine.freeze_order(c,order_id=order_id)
# 俱乐部佣金率（2026-10-24）：总平台按俱乐部单独设定；商品费率是默认基准。
commission_rate_service=ClubCommissionRateService(setting)
catalog_service=ProductCatalogService()
warehouse_engine=WarehouseEngine()
finance_engine=MerchandiseFinanceEngine()
analytics_engine=CommerceAnalyticsEngine(finance_engine,warehouse_engine)
ai_credit_engine=AICreditEngine()
gear_recommend=GearRecommendService()
leader_recommend=LeaderRecommendService()
club_bi=ClubBusinessIntelligence()
inventory_engine=ProcurementEngine(warehouse_engine,finance_engine)
booking_engine=BookingEngine(points_engine,wallet_snapshot,activity_points_policy,membership_engine,benefit_engine,participant_service,gear_points_policy)
checkout_engine=CheckoutEngine(points_engine,wallet_snapshot,setting,activity_points_policy,membership_engine,benefit_engine,participant_service,inventory_engine,ai_credit_engine,gear_points_policy,commission_rate_service)
# 营地会员权益定价层（2026-11-11）：把营地商品金额编排进活动会员/积分/券引擎，
# 不新建规则、不改 CheckoutEngine.confirm()，只复用同一套口径。
from clubos_domain.camp_pricing import (CampPricingEngine, read_config as camp_read_benefits_config,
                                        CAMP_BENEFITS_WRITABLE)
camp_pricing=CampPricingEngine(booking_engine=booking_engine,checkout_engine=checkout_engine,
                               benefit_engine=benefit_engine,points_engine=points_engine,
                               wallet_snapshot=wallet_snapshot,setting=setting,
                               gear_points_policy=gear_points_policy)
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
@app.get('/home')
def home():
    # 产品官网（营销落地页，static/site/index.html 自包含单文件）。
    # 表单同源直连 /api/public/club-applications（无需外部代理）。
    # no-cache 协商缓存：官网改版后立刻生效，不必 bump 版本号。
    return FileResponse(STATIC/'site'/'index.html', headers={'Cache-Control':'no-cache'})
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
    with conn() as c:return rows(c.execute('''SELECT id,title,status,event_date,location,city,price,capacity,created_at,cover,
        points_enabled,earn_club_points,accept_club_points,club_points_max_discount_percent,
        accept_gear_points,gear_points_max_discount_amount,club_points_earn_rate_override,
        member_discount_enabled
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
            'media_manifest':source.get('media_manifest') or [],
            # 链接只存 URL + 标题作溯源（正文与配图已进 text / images，不依赖它再抓一次）。
            'link':source.get('link') or {}}


def _source_link_info(srcv:dict)->dict|None:
    """落盘 source 里的「链接来源」。兼容早期只存 URL 字符串的写法。"""
    l=srcv.get('link') if isinstance(srcv,dict) else None
    if isinstance(l,str): l={'url':l}
    if not isinstance(l,dict): return None
    url=str(l.get('url') or '').strip()
    if not url: return None
    return {'url':url,'title':str(l.get('title') or '').strip()[:120]}


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


def _strip_recap_media(master:dict)->dict:
    """宣发渠道的媒体池剔除「活动回顾」事后上传的现场照片（source='recap'）。
    招募物料里混进活动结束才有的照片（全员合影、领队花絮等）是语义事故；
    回顾内容自己渲染时不受影响（ref 直接落在 master.media 里）。"""
    if isinstance(master,dict) and isinstance(master.get('media'),list):
        master['media']=[m for m in master['media']
                         if not(isinstance(m,dict) and m.get('source')=='recap')]
    return master


_FULL_DATE_RE=re.compile(r'(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})')
_PART_DATE_RE=re.compile(r'(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?')

def _activity_last_date(*texts)->'date|None':
    """从活动日期文案解析「最后一天」，判断活动是否已结束。

    支持「2026年10月30日—11月1日」「2026-10-30~11-01」「2026.10.30」等写法；
    缺年份的后半段（—11月1日）继承上一个已解析的年份。解析不出来返回 None。
    与前端 club.js 的 actLastDate() 保持同一套规则，两边要一起改。
    """
    last=None;year=None
    for t in texts:
        s=str(t or '')
        if not s:continue
        for m in _FULL_DATE_RE.finditer(s):
            try:cand=date(int(m.group(1)),int(m.group(2)),int(m.group(3)))
            except ValueError:continue
            year=int(m.group(1))
            if last is None or cand>last:last=cand
        if year is not None:
            # 「—11月1日」这类缺年份的后半段；完整日期会被上面先匹配，重复构造无害
            for m in _PART_DATE_RE.finditer(s):
                try:cand=date(year,int(m.group(1)),int(m.group(2)))
                except ValueError:continue
                # 只接受与已有日期相差 <400 天的（防止把零散数字误当日期拉飞）
                if last is not None and abs((cand-last).days)>=400:continue
                if last is None or cand>last:last=cand
    return last


from clubos_domain.leader_recommend import ACTIVITY_TYPES as _LEADER_ACTIVITY_TYPES
_LEADER_SPECIALTIES = tuple(_LEADER_ACTIVITY_TYPES.keys())


def _leader_years(payload:dict)->int:
    """带队年限：只接受 0-60 的整数。填错（空串 / 乱写）落 0，前端就不印「N 年经验」，
    绝不让一个手滑的数字变成对外宣称的资历。"""
    try: y=int(float(payload.get('yearsExp') or 0))
    except (TypeError,ValueError): y=0
    return max(0,min(60,y))


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
    # 2026-10-27 口碑与咨询待办：俱乐部端名册要能一眼看出「谁口碑好」「谁的咨询还没回」。
    stats=_leader_stats(c,club_id,[x['id'] for x in out])
    pending={}
    for r in rows(c.execute('SELECT leader_id,COUNT(*) cnt FROM leader_inquiries WHERE club_id=? AND status="new" GROUP BY leader_id',(club_id,))):
        pending[int(r['leader_id'] or 0)]=int(r['cnt'] or 0)
    for d in out:
        s=stats.get(int(d['id'])) or {}
        d['ratingAvg']=s.get('ratingAvg')
        d['ratingCount']=int(s.get('ratingCount') or 0)
        d['newInquiries']=pending.get(int(d['id']),0)
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


def _gear_plan_for(c,master:dict,club_id:int,user_id:int|None=None,public_view:bool=False)->dict:
    """出行清单 × 商城在售装备 + 会员价。

    候选集合只含商城里 status='active' 的真实商品，推荐引擎不做任何商品条目的生成或补全；
    清单里没有对应装备时如实标注，不凑一个相近的顶上（与媒体清单同一条纪律）。

    ★ 2026-11-04：要享受会员价必须先注册成为**该俱乐部**的会员。结算层
    （checkout._member_gear_discount）一直按 club_members 判定，展示层以前却对所有人
    下发会员价。这里按 user_id 补上同一条判定，结果随商品下发，前端据此印入会引导。
    套餐名（Pro / Enterprise）是后台运营概念，public_view=True 时一律不露出。
    """
    prods=rows(c.execute('SELECT id,name,price,stock,category,image_url,description FROM products WHERE status="active" ORDER BY id'))
    plan=gear_recommend.plan(prods,master).as_dict()
    eligible=False
    if user_id:
        try:
            eligible=c.execute('SELECT 1 FROM club_members WHERE club_id=? AND user_id=?',
                               (club_id,int(user_id))).fetchone() is not None
        except Exception:
            eligible=False
    plan['memberEligible']=bool(eligible)
    rate,plan_name,_=ai_credit_engine.plan_gear_rate(c,club_id)
    if rate<1:
        plan['memberDiscount']={'rate':round(rate,4),'discountZhe':round(rate*10,2),'planName':plan_name,
                                'tierName':plan_name,'label':f'{plan_name} 套餐 {rate*10:g} 折'}
        picks=[]
        for it in (plan.get('items') or []):
            picks+=list(it.get('matches') or [])
            picks+=list(it.get('substitutes') or [])
        picks+=list(plan.get('extras') or [])
        for p in picks:
            base=round(float(p.get('price') or 0),2); mp=round(base*rate,2)
            p['memberPrice']=mp; p['memberSavings']=round(base-mp,2)
            p['memberDiscount']=round(rate,4); p['memberTierName']=plan_name
            p['memberEligible']=bool(eligible)
    if public_view:
        md=plan.get('memberDiscount') or {}
        for k in ('planName','tierName','label','planCode'):
            md.pop(k,None)
        for it in (plan.get('items') or []):
            for p in list(it.get('matches') or [])+list(it.get('substitutes') or []):
                p.pop('memberTierName',None)
        for p in (plan.get('extras') or []):
            p.pop('memberTierName',None)
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


def _auto_cover_if_empty(c,*,activity_id:int,master:dict,detail:dict|None=None)->str|None:
    """活动还没有封面时，从它**自己的素材**里挑一张补上；已有封面一律不动。

    为什么需要这一步：`activities.cover` 是独立字段，全项目只有「上传封面」接口会写它，
    AI 生成流程从头到尾不碰 —— 于是素材里明明躺着一整个 PPT 抽出来的几十张照片，
    活动列表却挂着「未设封面」，C 端卡片只能显示品牌渐变兜底（用户 2026-10-17 截图实证）。
    这里只做「别让新活动光着上架」的兜底：**绝不覆盖人手动上传或手动选过的那张**，
    用户的决定永远优先于启发式打分。
    """
    r=c.execute('SELECT cover FROM activities WHERE id=?',(activity_id,)).fetchone()
    if r and str(r['cover'] or ''):return None
    m=pick_cover_media(master,detail)
    url=str((m or {}).get('url') or '')
    if not url:return None
    c.execute('UPDATE activities SET cover=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(url,activity_id))
    return url


def _finalize_detail_version(c,*,version_id:int,activity_id:int,detail:dict,master:dict,credits:int=0,
                             gateway:str|None=None,model:str|None=None)->None:
    c.execute('''UPDATE activity_detail_versions SET narrative=?,outline=?,detail_json=?,master_json=?,credits_charged=?,
                 gateway_mode=?,gateway_model=?,status='ready' WHERE id=?''',(
        str(detail.get('coreSellingIdea') or ''),detail_outline(detail),jdump(detail),jdump(master),credits,
        gateway,model,version_id))
    c.execute('UPDATE activities SET detail_version_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(version_id,activity_id))
    # 封面不是详情的一部分，但「AI 明明生成了整篇图文、列表里却没有一张图」是老板第一眼
    # 就会看到的问题。落版时顺手补一次（已有封面则跳过），新生成的活动从此自带封面。
    _auto_cover_if_empty(c,activity_id=activity_id,master=master,detail=detail)


def _release_detail_version(c,version_id:int)->None:
    """生成失败时撤掉占位：没扣费、没落库，版本号也还给下一次。"""
    c.execute('DELETE FROM activity_detail_versions WHERE id=? AND status=\'pending\'',(version_id,))


async def _run_generate_job(club_id:int,job_id:int,source:dict,refresh_facts:bool=False,direction:str='',city:str='',link_info:dict|None=None):
    """后台执行真模型生成（live 模式）。任何异常都落进 job.error，绝不能静默丢任务。"""
    city_val=str(city or '').strip()[:40] or None
    try:
        with conn() as c:
            c.execute('UPDATE ai_generate_jobs SET status="running",stage="generating",started_at=CURRENT_TIMESTAMP WHERE id=?',(job_id,))
        # 图内文字 OCR 增强（2026-10-29）：链接正文偏薄但配图很多（如微信长图推文）时，
        # 文字烤在图里、静态/渲染都抓不到，这里调视觉模型把图内文字读出来并入正文，
        # 活动要素（日期/价格/地点）才完整。任何失败都静默跳过 —— 读不出只是内容少一点，
        # 不能让整条生成挂掉。
        if link_info and len((link_info.get('text') or '').strip()) < 200 and link_info.get('images'):
            try:
                _ocr = await ocr_image_text(club_id, list(link_info['images']))
                if _ocr:
                    _base = source.get('text') or ''
                    source['text'] = (_base + '\n\n【以下文字提取自推文配图（OCR）】\n' + _ocr).strip()
            except Exception:
                pass
        result,usage=await generate_activity(club_id,source,direction=direction)
        master=result['activity_master']; detail=result['detail']
        if city_val: master['city']=city_val
        with conn() as c:
            c.execute('UPDATE ai_generate_jobs SET stage="saving" WHERE id=?',(job_id,))
        charge_credits(club_id,'detail',usage.usage_id)
        title=master.get('title') or 'AI生成活动'
        with conn() as c:
            _pd=_club_points_defaults(c,club_id)
            c.execute('INSERT INTO activities(club_id,title,status,event_date,location,city,price,capacity,activity_master_json,detail_json,source_json,points_enabled,earn_club_points,accept_club_points,club_points_max_discount_percent,member_discount_enabled) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                club_id,title,'draft',master.get('date',''),master.get('location',''),city_val,float(master.get('price') or 0),int(master.get('capacity') or 0),jdump(master),jdump(detail),jdump(_source_for_storage(source)),_pd[0],_pd[1],_pd[2],_pd[3],1))
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
async def ai_generate(club_id:int,background_tasks:BackgroundTasks,prompt:str=Form(''),city:str=Form(''),link:str=Form(''),files:list[UploadFile]=File(default=[])):
    club_or_404(club_id); ensure_credits(club_id,'detail')
    city_val=str(city or '').strip()[:40] or None
    # 链接入口（2026-10-28）：老板手上常常只有一条别家写好的推文 / 自家活动页 URL。
    # 链接和上传资料是并列关系，也可以一起给（文章正文 + 补几张实拍）。
    link_raw=str(link or '').strip()
    if not link_raw and not str(prompt or '').strip() and not files:
        raise HTTPException(400,'先给点材料：填一句话、传一份资料，或者贴一条活动链接。')
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
        # 不支持的格式过去被静默丢掉：老板传了几张 HEIC 手机照，系统只字不提，
        # 他只会以为「系统读错文件了」。save_uploads 现在把每个跳过的文件名如实记下来
        # （.doc/.ppt/.wps 里的 zip 伪装者会被按真实格式救回，不算跳过）。
        _skipped=[]
        saved=save_uploads(files,batch,max_total_bytes=20*1024*1024 if IS_PROD else None,skipped_out=_skipped)
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
        # 链接 → 正文 + 配图（2026-10-28）。配图落进同一批目录，交给 parse_sources
        # 统一做图片元数据 / 打标 / media_manifest，两条入口最终长成同一个 source。
        link_info=None
        prompt_for_ai=prompt
        if link_raw:
            link_info=fetch_link(link_raw,batch/'link')
            saved=saved+list(link_info['images'])
            _link_text,_link_cost=redact_cost_text(link_info['text'])
            # 成本闸门对链接正文同样生效：方案的网页版一样可能有「费用明细」页。
            _head=f"[链接: {link_info['title'] or link_info['finalUrl']}]\n{link_info['url']}\n{_link_text}"
            _p=str(prompt or '').strip()
            prompt_for_ai=(_p+'\n\n'+_head).strip() if _p else _head
        source=parse_sources(saved,prompt_for_ai,STATIC)
        source['link']={'url':link_info['finalUrl'],'title':link_info['title']} if link_info else {}
    except LinkError as e:
        shutil.rmtree(batch,ignore_errors=True)
        raise HTTPException(422,str(e))
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
        _errors=source.get('readErrors') or []
        if _errors:
            _en='、'.join(str(e.get('name') or '文件') for e in _errors[:3])
            raise HTTPException(422,f'《{_en}》没能打开（文件可能已损坏或被加密）。'
                                    '请用 Office 另存一份后重新上传，或直接补一句活动说明'
                                    '（名称/日期/地点/人数）让 AI 先生成初稿。')
        _names='、'.join(str(f.get('name') or '方案') for f in _doc_files[:3])
        raise HTTPException(422,f'《{_names}》里没有可读取的文字内容：这份方案的文字可能全部做成了图片。'
                                'AI 无法按方案生成，请补一句活动说明（名称/日期/地点/人数），'
                                '或换一份带文字的方案再试。')
    _text_len=len((source.get('text') or '').strip())
    _doc_exts={'.pptx','.docx','.pdf','.txt','.md'}
    _doc_files=[f for f in (source.get('files') or []) if str(f.get('ext') or '') in _doc_exts]
    summary={'files':source['files'],'imageCount':len(source['images']),'media':source['media_manifest'],
             'textLength':_text_len,'docFiles':_doc_files,'noText':_text_len==0,'skippedFiles':_skipped,
             'readErrors':source.get('readErrors') or [],
             # 前端据此告诉老板「这条链接读到了什么」，别让人以为贴了链接却被忽略。
             'link':({'url':link_info['finalUrl'],'title':link_info['title'],
                      'textLength':len(link_info['text']),'imageCount':len(link_info['images']),
                      'truncated':link_info['truncated']} if link_info else None)}
    if not live_mode:
        # mock：秒回，保持旧的同步契约，回归脚本直接拿 activityId。
        try: result,usage=await generate_activity(club_id,source)
        except AIGatewayError as e: raise HTTPException(502,str(e))
        master=result['activity_master']; detail=result['detail']
        if city_val: master['city']=city_val
        charge_credits(club_id,'detail',usage.usage_id)
        title=master.get('title') or 'AI生成活动'
        with conn() as c:
            _pd=_club_points_defaults(c,club_id)
            c.execute('INSERT INTO activities(club_id,title,status,event_date,location,city,price,capacity,activity_master_json,detail_json,source_json,points_enabled,earn_club_points,accept_club_points,club_points_max_discount_percent,member_discount_enabled) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                club_id,title,'draft',master.get('date',''),master.get('location',''),city_val,float(master.get('price') or 0),int(master.get('capacity') or 0),jdump(master),jdump(detail),jdump(_source_for_storage(source)),_pd[0],_pd[1],_pd[2],_pd[3],1))
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
    background_tasks.add_task(_run_generate_job,club_id,jid,source,city=city_val,link_info=link_info)
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
    city=str(payload.get('city') or '').strip()[:40] or None
    master={'title':title,'date':event_date or '待定','location':location or '待定','city':city or '',
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
        _pd=_club_points_defaults(c,club_id)
        cur=c.execute('INSERT INTO activities(club_id,title,status,event_date,location,city,price,capacity,activity_master_json,detail_json,points_enabled,earn_club_points,accept_club_points,club_points_max_discount_percent,member_discount_enabled) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (club_id,title,'draft',event_date,location,city,price,capacity,jdump(master),jdump(detail),_pd[0],_pd[1],_pd[2],_pd[3],1))
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
        # 「这份详情是按哪些资料做出来的」必须如实回给俱乐部端：一次上传「方案 + 额外照片」
        # 时，光看成品无法判断 AI 到底读了哪份文件（2026-10-09 用户反馈）。
        # 判定依据是落盘的 source_json（原始资料本身），不是模型输出 —— 模型从不回填这些字段。
        _srcv=jload(a.get('source_json'),None) or {}
        _plain=str(_srcv.get('text') or '')
        _rebuilt=('text' not in _srcv) or str(_srcv.get('note') or '').startswith('reconstructed')
        _sfiles=[f for f in (_srcv.get('files') or []) if isinstance(f,dict)]
        _dexts={'.pptx','.docx','.pdf','.txt','.md'}
        a['sourceInfo']={
            'available':bool(_srcv) and not _rebuilt,
            'reconstructed':bool(_srcv) and _rebuilt,
            'hasPlanText':bool(_plain.strip()),
            'textLength':len(_plain.strip()),
            'planFiles':[str(f.get('name') or '') for f in _sfiles if str(f.get('ext') or '') in _dexts],
            'photoFiles':[str(f.get('name') or '') for f in _sfiles if str(f.get('ext') or '') not in _dexts],
            'imageCount':len(_srcv.get('images') or []),
            'photoCount':len([x for x in (_srcv.get('media_manifest') or [])
                              if isinstance(x,dict) and str(x.get('kind') or 'photo')!='logo']),
            # 链接来源（2026-10-28）：只回标题 + 字数，让俱乐部端能说「本场按链接生成」。
            'link':_source_link_info(_srcv),
        }
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
    # 活动分类归属（2026-11-06）：后台详情页多选用。
    with conn() as c:
        a['categoryIds']=[int(r['category_id']) for r in rows(c.execute(
            'SELECT category_id FROM activity_category_links WHERE activity_id=? ORDER BY category_id',(activity_id,)))]
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
    # 装备积分规则由总平台解析（套餐资格 + 按俱乐部的比例），俱乐部端只读展示。
    with conn() as _gpc:
        _gp=gear_points_policy.resolve(_gpc,int(a.get('club_id') or 0))
    a['pointsPolicy']=activity_points_policy.from_activity(a,_gp).as_dict()
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


def _persist_regen_detail(*,club_id:int,activity_id:int,version_id:int,source:dict,old_master:dict,old_row:dict,
                          detail:dict,result:dict,refresh:bool,usage)->tuple[int,dict]:
    """「换一版」生成成功后的统一落库（同步 mock 路径与 live 后台任务共用）：
    校验 blocks → 扣费 → 按 refresh 分支写回 master → 写 detail_json → finalize 版本。
    返回 (实扣 Credits, 生效后的 master)。失败时撤版本占位再抛（没扣费就没落库，C 端保持上一版）。"""
    blocks=[b for b in detail.get('blocks') or [] if isinstance(b,dict) and b.get('type')]
    if not blocks:
        with conn() as c: _release_detail_version(c,version_id)
        # 没扣费、没落库：宁可保留上一版，也不要让 C 端详情页变空。
        raise RuntimeError('AI 这一版没有返回可用的内容结构，已保留上一版；换个方向再试一次。')
    detail['blocks']=blocks
    new_master=result.get('activity_master') if refresh else old_master
    new_master=new_master if isinstance(new_master,dict) and new_master else old_master
    try:
        cost=charge_credits(club_id,'detail',usage.usage_id)
    except Exception:
        with conn() as c: _release_detail_version(c,version_id)
        raise
    with conn() as c:
        if refresh:
            c.execute('UPDATE activities SET activity_master_json=?,title=?,event_date=?,location=?,price=?,capacity=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(
                jdump(new_master),new_master.get('title') or old_row['title'],new_master.get('date') or old_row['event_date'],
                new_master.get('location') or old_row['location'],float(new_master.get('price') or 0),int(new_master.get('capacity') or 0),activity_id))
        else:
            # ★ 「换一版」也会给照片补上视觉打标（画面描述 + 主题标签）。不写回去，下一版又要
            #   重新调一遍视觉模型、重新花一次钱（每张图一次调用，几十张就是几十次）。
            #   只在「确实新打上了标」时才写，且事实字段一个都不碰。
            _oldref={str(x.get('ref')):x for x in old_master.get('media') or [] if isinstance(x,dict)}
            _gained=any(isinstance(x,dict) and x.get('desc')
                        and not (_oldref.get(str(x.get('ref'))) or {}).get('desc')
                        for x in new_master.get('media') or [])
            if _gained:
                c.execute('UPDATE activities SET activity_master_json=?,source_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                          (jdump(new_master),jdump(_source_for_storage(source)),activity_id))
        c.execute('UPDATE activities SET detail_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(jdump(detail),activity_id))
        _finalize_detail_version(c,version_id=version_id,activity_id=activity_id,detail=detail,master=new_master,
                                 credits=cost,gateway=usage.provider,model=usage.model)
    return cost,new_master


async def _run_detail_regen_job(*,club_id:int,job_id:int,activity_id:int,version_id:int,version_no:int,
                                source:dict,master:dict,previous:dict,old_row:dict,direction:str,refresh:bool):
    """后台执行真模型「换一版」（live 模式）。版本占位由请求方提前保留；任何异常都落 job.error，
    绝不能静默丢任务。与 _run_generate_job 同一纪律。"""
    try:
        with conn() as c:
            c.execute('UPDATE ai_generate_jobs SET status="running",stage="generating",started_at=CURRENT_TIMESTAMP WHERE id=?',(job_id,))
        try:
            result,usage=await regenerate_detail(club_id,source,master,direction=direction,
                                                 previous_detail=previous,version_no=version_no,refresh_facts=refresh)
        except Exception:
            with conn() as c: _release_detail_version(c,version_id)
            raise
        with conn() as c:
            c.execute('UPDATE ai_generate_jobs SET stage="saving" WHERE id=?',(job_id,))
        detail=result.get('detail') or {}
        cost,_new_master=_persist_regen_detail(club_id=club_id,activity_id=activity_id,version_id=version_id,source=source,
                                   old_master=master,old_row=old_row,detail=detail,result=result,
                                   refresh=refresh,usage=usage)
        with conn() as c:
            c.execute('UPDATE ai_generate_jobs SET status="done",activity_id=?,summary_json=?,finished_at=CURRENT_TIMESTAMP WHERE id=?',(
                activity_id,jdump({'kind':'detail-regen','activityId':activity_id,'versionNo':version_no,'creditsCharged':cost}),job_id))
    except Exception as e:
        with conn() as c:
            c.execute('UPDATE ai_generate_jobs SET status="failed",error=?,finished_at=CURRENT_TIMESTAMP WHERE id=?',(str(e)[:800] or e.__class__.__name__,job_id))


@app.post('/api/club/{club_id}/activities/{activity_id}/detail-regenerate')
async def activity_detail_regenerate(club_id:int,activity_id:int,background_tasks:BackgroundTasks,payload:dict=Body(default={})):
    """重新生成活动详情（老板对第一版不满意时的「换一版」）。

    与 AI 宣发同一套计费：先校验额度，生成成功才扣 Credits（失败一律不扣）。
    默认只重做叙事与排版——事实冻结，因此团期、价格政策、报名数据都不会被 AI 改写；
    refreshFacts=true 时才允许用同一份原始资料复核标题/日期/地点/价格/人数。

    live 模式下真模型要跑几分钟，同步响应会被网关空闲超时掐断（与 ai-generate 同一教训；
    2026-10-15 线上实测：95s 处 504、uvicorn 随断连取消任务、版本占位永远 pending）。
    所以 live 一律改后台任务：先占位版本（保留 409 防重闸门）、挂 BackgroundTasks、
    秒回 {jobId,versionNo} 由前端轮询 ai-generate/{jobId}；mock 秒回，保持同步不破坏回归脚本。
    """
    club_or_404(club_id); ensure_credits(club_id,'detail')
    direction=str(payload.get('direction') or '')[:800]
    refresh=bool(payload.get('refreshFacts'))
    live=effective_gateway_mode()[0]=='live'
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        source,origin=_activity_source(a)
        if not source:
            raise HTTPException(409,'这场活动没有留下可复用的原始资料，无法重新生成；请用「AI 发活动」重新上传资料。')
        master=_repair_master_media(jload(a['activity_master_json'],{}) or {},source)
        previous=jload(a['detail_json'],{}) or {}
        # 先占位：并发的第二条请求会在这里被挡下，而不是各调一次 AI、各扣一次 Credits。
        version_id,version_no=_reserve_detail_version(c,club_id=club_id,activity_id=activity_id,
                                                      origin='ai-regenerate',direction=direction,facts_refreshed=refresh)
    old_row={'title':a['title'],'event_date':a['event_date'],'location':a['location'],
             'price':a['price'],'capacity':a['capacity']}
    if live:
        with conn() as c:
            c.execute('INSERT INTO ai_generate_jobs(club_id,status,stage,summary_json) VALUES(?,?,?,?)',(
                club_id,'queued','queued',jdump({'kind':'detail-regen','activityId':activity_id,'versionNo':version_no})))
            job_id=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        background_tasks.add_task(_run_detail_regen_job,club_id=club_id,job_id=job_id,activity_id=activity_id,
                                  version_id=version_id,version_no=version_no,source=source,master=master,
                                  previous=previous,old_row=old_row,direction=direction,refresh=refresh)
        return {'activityId':activity_id,'versionId':version_id,'versionNo':version_no,'jobId':job_id,'async':True}
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
    try:
        cost,new_master=_persist_regen_detail(club_id=club_id,activity_id=activity_id,version_id=version_id,source=source,
                                   old_master=master,old_row=old_row,detail=detail,result=result,
                                   refresh=refresh,usage=usage)
    except RuntimeError as e:
        raise HTTPException(502,str(e))
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
        # 装备积分规则由总平台解析（套餐资格 + 按俱乐部的比例），俱乐部端只读展示。
        _gp=gear_points_policy.resolve(c,int(club_id))
    return activity_points_policy.from_activity(a,_gp).as_dict()

@app.patch('/api/club/{club_id}/activities/{activity_id}/points-policy')
def update_activity_points_policy(club_id:int,activity_id:int,payload:dict=Body(...)):
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        # 同上：注入平台规则，保存后返回的「C 端实际生效」才与真实判定一致
        # （否则俱乐部保存完看到的是「装备积分抵扣：否」，会以为平台把它关了）。
        _gp=gear_points_policy.resolve(c,int(club_id))
        current=activity_points_policy.from_activity(a,_gp)
        policy=activity_points_policy.normalize_update(payload,current)
        activity_points_policy.persist(c,activity_id,club_id,policy)
    return policy.as_dict()

def _club_points_defaults(c, club_id):
    """读俱乐部的「新活动默认积分规则」，返回 (enabled, earn, accept, percent)。

    2026-10-31：activities 那四个积分列的表级默认值写死 1/1/1/100，俱乐部改不动，
    于是每场新活动都默认「参与积分 + 可抵 100%」（截图里全场 100% 就是这么来的）。
    新建活动时显式读俱乐部配置写进去，运营在「积分策略」页设一次即可全场生效。
    读不到（老库还没跑迁移 / 俱乐部不存在 / 值非法）一律退回原表级默认值 ——
    加功能绝不能把建活动搞失败。
    """
    try:
        r = c.execute('''SELECT default_points_enabled, default_earn_club_points,
                         default_accept_club_points, default_club_points_max_discount_percent
                         FROM clubs WHERE id=?''', (int(club_id),)).fetchone()
    except Exception:
        return (1, 1, 1, 100.0)
    if not r:
        return (1, 1, 1, 100.0)

    def _b(v):
        try:
            return 1 if int(v) else 0
        except Exception:
            return 1

    try:
        pct = float(r['default_club_points_max_discount_percent'])
    except Exception:
        pct = 100.0
    return (_b(r['default_points_enabled']), _b(r['default_earn_club_points']),
            _b(r['default_accept_club_points']), max(0.0, min(100.0, pct)))

@app.get('/api/club/{club_id}/points-default')
def get_club_points_default(club_id:int):
    """俱乐部级「新活动默认积分规则」：新建活动时继承这四个值。"""
    with conn() as c:
        en, earn, acc, pct = _club_points_defaults(c, club_id)
    return {'enabled': bool(en), 'earnClubPoints': bool(earn),
            'acceptClubPoints': bool(acc), 'clubPointsMaxDiscountPercent': pct}

@app.patch('/api/club/{club_id}/points-default')
def update_club_points_default(club_id:int, payload:dict=Body(...)):
    """保存默认规则。**只影响之后新建的活动**，存量活动一律不动 ——
    否则运营改一次默认值，就把历史活动那些「单独调过」的设置全冲掉了。"""
    try:
        pct = float(payload.get('clubPointsMaxDiscountPercent', 100) or 0)
    except Exception:
        pct = 100.0
    pct = max(0.0, min(100.0, pct))
    vals = (1 if payload.get('enabled') else 0,
            1 if payload.get('earnClubPoints') else 0,
            1 if payload.get('acceptClubPoints') else 0,
            pct)
    with conn() as c:
        cur = c.execute('''UPDATE clubs SET default_points_enabled=?, default_earn_club_points=?,
                           default_accept_club_points=?, default_club_points_max_discount_percent=?
                           WHERE id=?''', vals + (int(club_id),))
        if cur.rowcount == 0:
            raise HTTPException(404, '俱乐部不存在')
    return {'ok': True, 'enabled': bool(vals[0]), 'earnClubPoints': bool(vals[1]),
            'acceptClubPoints': bool(vals[2]), 'clubPointsMaxDiscountPercent': pct}

# ── 活动积分「现金换算比例」X 积分 = ¥1（2026-11-05）──────────────────────
# 与上面的「新活动默认规则」分开：那四个是**活动级**开关，会继承到新活动；
# 换算比例是**俱乐部 / 会员级**的资金口径，不属于任何一场活动。
# 生效优先级：会员当前等级专属 > 俱乐部默认 > 平台全局默认 > 100。
@app.get('/api/club/{club_id}/points-redeem-rate')
def get_club_points_redeem_rate(club_id:int):
    """积分策略页「积分现金换算比例」卡片：平台默认 / 俱乐部设定 / 各等级生效一览。"""
    with conn() as c:
        return describe_club_points_redeem_rate(c, club_id=int(club_id), user_id=None, setting=setting)

@app.patch('/api/club/{club_id}/points-redeem-rate')
def update_club_points_redeem_rate(club_id:int, payload:dict=Body(...)):
    """设俱乐部默认换算比例；`pointsRedeemRate=0` 表示跟随平台全局默认。

    只改 clubs 一行，不碰任何活动、不碰任何会员等级 —— 各等级自己的专属比例
    在「客户会员 → 等级会员 / 付费会员卡」里配，优先级高于这里。
    装备积分由平台出钱，不受此设置影响。
    """
    try:
        rate = validate_points_redeem_rate(payload.get('pointsRedeemRate'))
    except ValueError as e:
        raise HTTPException(400, str(e))
    with conn() as c:
        cur = c.execute('UPDATE clubs SET points_redeem_rate=? WHERE id=?', (rate, int(club_id)))
        if cur.rowcount == 0:
            raise HTTPException(404, '俱乐部不存在')
        return {'ok': True, **describe_club_points_redeem_rate(
            c, club_id=int(club_id), user_id=None, setting=setting)}

@app.post('/api/club/{club_id}/activities/points-policy/bulk')
def bulk_update_activity_points_policy(club_id:int, payload:dict=Body(...)):
    """批量改多场活动的积分规则（「积分策略」页勾选后一次刷）。

    走一个事务、发一个请求：几十场活动逐条 PATCH 会让运营盯着进度条，
    中途失败还会留下「改了一半」的烂摊子。
    装备积分字段一律不碰（准入归总平台），只写俱乐部活动积分那四个。
    """
    ids = payload.get('activityIds') or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, '请至少勾选一场活动')
    try:
        pct = float(payload.get('clubPointsMaxDiscountPercent', 100) or 0)
    except Exception:
        pct = 100.0
    pct = max(0.0, min(100.0, pct))
    enabled = 1 if payload.get('enabled') else 0
    earn = 1 if payload.get('earnClubPoints') else 0
    accept = 1 if payload.get('acceptClubPoints') else 0
    clean = []
    for x in ids[:500]:
        try:
            clean.append(int(x))
        except Exception:
            continue
    if not clean:
        raise HTTPException(400, '活动 ID 无效')
    updated = 0
    with conn() as c:
        for aid in clean:
            cur = c.execute('''UPDATE activities SET points_enabled=?, earn_club_points=?,
                               accept_club_points=?, club_points_max_discount_percent=?,
                               updated_at=CURRENT_TIMESTAMP
                               WHERE id=? AND club_id=?''',
                            (enabled, earn, accept, pct, aid, int(club_id)))
            updated += cur.rowcount
    return {'ok': True, 'updated': updated, 'requested': len(clean)}

@app.get('/api/club/{club_id}/activities/{activity_id}/member-discount')
def get_activity_member_discount(club_id:int,activity_id:int):
    with conn() as c:
        a=row(c.execute('SELECT member_discount_enabled FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
    return {'enabled':bool(a['member_discount_enabled'])}

@app.patch('/api/club/{club_id}/activities/{activity_id}/member-discount')
def update_activity_member_discount(club_id:int,activity_id:int,payload:dict=Body(...)):
    """活动级「参与会员优惠」开关（2026-10-26）：会员等级折扣生效的前提条件之一。"""
    with conn() as c:
        a=row(c.execute('SELECT id FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        c.execute('UPDATE activities SET member_discount_enabled=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?',
                  (1 if payload.get('enabled') else 0,activity_id,club_id))
    return {'ok':True,'enabled':bool(payload.get('enabled'))}

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

@app.post('/api/club/{club_id}/activities/{activity_id}/republish')
def republish_activity(club_id:int,activity_id:int):
    """重新发布 / 同步到 C 端：把「当前这一版详情」正式对顾客生效。

    为什么要有这个动作（2026-10-10 用户反馈「后台重新生成一版之后，C 端还是旧版，
    需要增加重新换版之后再次发布的功能」）：
      · 事实层面 C 端本来就是实时读库（public_activity 直接读 activities.detail_json），
        换版后两端数据一致 —— 实测俱乐部端与 C 端返回的 detail 逐字节相同；
      · 但老板**在界面上看不出「到底同步了没有」**：C 端页面已经开在手机上/另一个标签页里，
        它不会自己变；顾客手里的旧页面更是如此。
    所以这里把「发布」做成一等公民：一个显式、可点、有回执的动作。
      · 保证 activities.status='published'（草稿活动点它＝发布当前这一版）；
      · 刷新 updated_at —— C 端详情接口会把它当版本戳回给页面，页面据此提示「有新版，点我刷新」；
      · 回执带上当前版本号与带版本参数的 C 端地址，前端直接展示「已同步到第 N 版」。
    只动 status / updated_at，绝不改写内容 —— 文案与排版归 detail-regenerate 管。
    """
    with conn() as c:
        a=row(c.execute('SELECT id,status,detail_json,activity_master_json,detail_version_id FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        master=jload(a['activity_master_json'],{}) or {}
        if master.get('blocking_conflicts'): raise HTTPException(409,'存在必须解决的事实冲突，暂不能发布')
        detail=jload(a['detail_json'],{}) or {}
        blocks=[b for b in detail.get('blocks') or [] if isinstance(b,dict) and b.get('type')]
        if not blocks:
            raise HTTPException(409,'这一版还没有可用的详情内容，先点「重新生成 / 换一版」再同步。')
        c.execute('UPDATE activities SET status="published",updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?',(activity_id,club_id))
        ver=row(c.execute("SELECT version_no FROM activity_detail_versions WHERE id=? AND activity_id=? AND club_id=? AND status='ready'",
                          (a['detail_version_id'],activity_id,club_id)))
        stamp=row(c.execute('SELECT updated_at FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
    _vid=int(a['detail_version_id'] or 0)
    return {'ok':True,'status':'published','detailVersionId':_vid,
            'versionNo':(ver or {}).get('version_no'),'blockCount':len(blocks),
            'alreadyPublished':(a['status']=='published'),
            'updatedAt':(stamp or {}).get('updated_at'),
            # v= 只是缓存破冰参数（C 端不按它选版本，内容永远是库里的最新版）
            'publicUrl':'/web?club_id=%d&activity=%d&v=%d'%(club_id,activity_id,_vid)}

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
        if 'city' in payload: fields['city']=str(payload.get('city') or '').strip()[:40] or None
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
        if 'city' in fields: master['city']=fields['city'] or ''
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
        # 手工建的活动（没有走 AI 生成）也可能带着素材却一直没有封面，编辑保存时补一次。
        _auto_cover_if_empty(c,activity_id=activity_id,master=master,detail=detail)
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

@app.post('/api/club/{club_id}/activities/{activity_id}/cover/auto')
def auto_activity_cover(club_id:int,activity_id:int):
    """从活动自己的素材里挑一张当封面（老板主动点的，允许覆盖已有封面）。

    与落版时的自动兜底是同一个挑选函数（pick_cover_media）：优先沿用详情首图，
    否则按「风景主题加分 / 餐食人像手作减分 / 大图优先」打分。素材里只有 logo、
    图表或尺寸过小的图时**宁可返回 422 让人手动上传，也不推一张劣质图当门面**。
    """
    club_or_404(club_id)
    with conn() as c:
        a=row(c.execute('SELECT id,activity_master_json,detail_json FROM activities WHERE id=? AND club_id=?',
                        (activity_id,club_id)))
        if not a:raise HTTPException(404,'活动不存在')
        master=jload(a.get('activity_master_json'),{}) or {}
        detail=jload(a.get('detail_json'),{}) or {}
        m=pick_cover_media(master,detail)
        url=str((m or {}).get('url') or '')
        if not url:
            raise HTTPException(422,'这份资料里没有可用作封面的照片（只有 logo/图表，或图片尺寸太小）。请手动上传一张。')
        c.execute('UPDATE activities SET cover=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?',(url,activity_id,club_id))
    return {'cover':url,'ref':m.get('ref'),'desc':str(m.get('desc') or '')[:100]}


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

# ══ AI 宣传长图：异步作业表 ══
# 长图管线（视觉打标 + 要点表 + 排版生成）整体要跑 1~4 分钟，同步 HTTP 必被托管网关
# 55 秒掐断（浏览器收 504，用户以为失败——2026-10-11 用户再次反馈后根治）。
# 现在点击后立即返回 {pending:true}，后台 asyncio 任务跑完落库，前端轮询 status 端点。
# 进程内存表即可：重启丢失作业 = 状态回 idle，前端超时兜底提示去内容列表看，无脏数据。
_LONGPIC_JOBS: dict[tuple[int, int], dict] = {}

async def _longpic_job(club_id:int,activity_id:int,master:dict,detail:dict,
                       source_text:str,cover_url:str|None,seed:int,persist_master:dict|None=None):
    """persist_master：落库用的完整 master（含 source='recap' 的回顾照片）。
    生成用 master 是剥离过回顾照片的版本；若直接把它落库，会把老板上传的
    回顾照片从活动资料里整批删掉（旧回顾成品的图片随之失效，2026-10-18 实测复现）。"""
    job=_LONGPIC_JOBS.setdefault((club_id,activity_id),{'status':'running'})
    try:
        caps=await caption_media(club_id,master)
        digest=await _fact_digest(club_id,source_text) if source_text else ''
        fact_pack=build_fact_pack(club_id,activity_id,master)
        try:
            content,usage=await generate_longpic(club_id,master,detail,cover_url=cover_url,
                                                 source_text=source_text,
                                                 caption_lines=caption_lines(master,caps),
                                                 digest=digest,
                                                 fact_pack=fact_pack,seed=seed)
        except AIGatewayError as e:
            job.update(status='error',error=str(e)); return
        if not content.get('html'):
            job.update(status='error',error='模型没有返回可用的排版内容，请再试一次。'); return
        store=persist_master if persist_master is not None else master
        persist_captions(store,caps)
        with conn() as c:
            c.execute('UPDATE activities SET activity_master_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND club_id=?',
                      (jdump(store),activity_id,club_id))
            cur=c.execute('INSERT INTO content_assets(club_id,activity_id,channel,title,body_json) VALUES(?,?,?,?,?)',(
                club_id,activity_id,'longpic',content.get('title'),jdump(content)))
            asset_id=cur.lastrowid
        charge_credits(club_id,'longpic',usage.usage_id)
        job.update(status='done',assetId=asset_id,title=content.get('title'))
    except Exception as e:  # 兜底：任何未预期异常都要让前端退出轮询，不能永远转圈
        job.update(status='error',error=f'生成过程出现异常：{e}')

@app.get('/api/club/{club_id}/activities/{activity_id}/channel/longpic/status')
def longpic_status(club_id:int,activity_id:int):
    return _LONGPIC_JOBS.get((club_id,activity_id)) or {'status':'idle'}


# ══════════════════════════════════════════════════════════════════════
# 服务端 HTML → 真实 PNG（2026-11-05 新增，解决两个老痛点）
#   ① 微信分享带不走 iframe：把长图 / 活动详情宣传区块栅格化成一张真图，
#      分享卡片与下载都拿真文件，不再是「预览在 iframe 里、发出去是空白」。
#   ② 前端那条 SVG foreignObject 客户端导出链路常年「下载后只有文字 / 照片全空」
#      （#529/#542 反复炸），服务端 Chromium 真渲染一次性根除。
#
# 渲染器：scripts/html_to_image.cjs（调用本机已装的 node Playwright + Chromium）。
# 媒体解析：把 {{media:ref}} / 俱乐部代理 URL / /static/uploads 裸链 统一改成
#           本地 file:// 绝对路径，Chromium 同源加载。
# ══════════════════════════════════════════════════════════════════════
import html as _html_mod  # 仅用于避免与变量名冲突时的占位（保留可读）

_ROOT = os.path.dirname(os.path.abspath(__file__))
_HTML2IMG_JS = os.path.join(_ROOT, 'scripts', 'html_to_image.cjs')
_MEDIA_PROXY_SPLIT = re.compile(r'/media/([^"\'\s>]+)')
_MEDIA_COVER_RE = re.compile(r'/api/club/\d+/activities/\d+/cover')
_MEDIA_REF_IMG = re.compile(
    r'(<img\b[^>]*?\bsrc=(["\']))\{\{?\s*media\s*:\s*([A-Za-z0-9_\-]+)\s*\}?\}\2')
_MEDIA_REF_BARE = re.compile(r'\{\{?\s*media\s*:\s*([A-Za-z0-9_\-]+)\s*\}?\}')
_MEDIA_PROXY_IMG = re.compile(
    r'(<img\b[^>]*?\bsrc=(["\']))(/api/club/\d+/activities/\d+/media/[^"\'\s>]+)\2')


def _file_url_of(u: str, root: str) -> str | None:
    """把任意媒体地址规约成 file:// 绝对路径（仅本地渲染用）。"""
    if not u:
        return None
    if u.startswith('file://'):
        return u
    if u.startswith('/static/uploads/'):
        return 'file://' + os.path.join(root, u.lstrip('/'))
    return None


def _resolve_media_to_file(html: str, master: dict | None, root: str,
                           cover_static: str | None = None) -> str:
    """把长图 / 详情 HTML 里的媒体引用改写成 file://，供 Chromium 本地渲染。

    支持的形态（与前端 resolveLongpic / mediaUrl 对齐）：
      · <img src="{{media:ref}}"> / <img src="{media:ref}">  → 走 master 媒体池
      · <img src="/api/club/.../media/<tail>">              → 俱乐部代理路由
      · 裸 {{media:ref}} 占位符（不在 img 里）               → 解析不到就删掉，不引坏链
      · /api/club/.../cover                                 → 活动封面文件
    """
    master = master if isinstance(master, dict) else {}
    ref_map: dict[str, str] = {}
    for m in (master.get('media') or []):
        if isinstance(m, dict) and m.get('ref') and m.get('url'):
            ref_map[str(m['ref'])] = str(m['url'])

    def repl_ref_img(mm: re.Match) -> str:
        f = _file_url_of(ref_map.get(mm.group(3)), root)
        return (mm.group(1) + f) if f else mm.group(0)  # 解析不到就保留原样，不造破图

    html = _MEDIA_REF_IMG.sub(repl_ref_img, html)

    def repl_proxy_img(mm: re.Match) -> str:
        tail = mm.group(3).split('/media/', 1)[1]
        f = _file_url_of('/static/' + tail, root)
        return (mm.group(1) + f) if f else mm.group(0)

    html = _MEDIA_PROXY_IMG.sub(repl_proxy_img, html)

    # 裸占位符：解析到就整段删掉（保留前后文字），解析不到保留原样
    html = _MEDIA_REF_BARE.sub(
        lambda mm: '' if _file_url_of(ref_map.get(mm.group(1)), root) else mm.group(0), html)

    if cover_static:
        cov = _file_url_of('/static/' + cover_static.lstrip('/'), root)
        if cov:
            html = _MEDIA_COVER_RE.sub(lambda _: cov, html)
    return html


def render_html_to_png(html: str, out_path: str, *, width: int = 750, scale: int = 2,
                      root: str | None = None) -> str:
    """把一段 HTML 片段渲染成 750px 宽、deviceScaleFactor=scale 的整页 PNG。"""
    root = root or _ROOT
    # 已经是完整文档就不二次包裹，否则包一层 750px 宽的骨架（含中文回退字体）
    stripped = html.strip()
    is_full = stripped.lower().startswith('<!doctype') or stripped.lower().startswith('<html')
    if is_full:
        body = html
    else:
        body = ('<!doctype html><html><head><meta charset="utf-8">'
                '<style>html,body{margin:0;padding:0;width:%dpx;background:#fff;'
                'font-family:-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;'
                '-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}'
                'img{max-width:100%%;height:auto;display:block}section{display:block}</style>'
                '</head><body>' % width) + html + '</body></html>'
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + '.src.html'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(body)
    try:
        env = dict(os.environ)
        env.pop('NODE_OPTIONS', None)  # 去掉 WorkBuddy 的 fs/network shim，避免干扰 chromium
        proc = subprocess.run(
            ['node', _HTML2IMG_JS, '--html', tmp, '--out', out_path,
             '--width', str(width), '--scale', str(scale)],
            capture_output=True, text=True, timeout=150, env=env)
        if proc.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError('html_to_image 失败: ' + (proc.stderr or proc.stdout or '').strip()[:600])
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return out_path


@app.post('/api/club/{club_id}/activities/{activity_id}/render-longpic')
def render_longpic_image(club_id: int, activity_id: int):
    """把已生成的宣传长图渲染成真实 PNG（服务端），返回可下载的图片地址。"""
    with conn() as c:
        a = row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?', (activity_id, club_id)))
        if not a:
            raise HTTPException(404, '活动不存在')
        asset = row(c.execute(
            "SELECT * FROM content_assets WHERE club_id=? AND activity_id=? AND channel='longpic' "
            "ORDER BY id DESC LIMIT 1", (club_id, activity_id)))
        if not asset:
            raise HTTPException(400, '该活动还没有生成宣传长图，请先点「AI 生成」。')
        body = jload(asset['body_json'], {}) if asset['body_json'] else {}
        html = body.get('html') or ''
        if not html:
            raise HTTPException(400, '长图内容为空，无法渲染。')
        master = jload(a['activity_master_json'], {}) if a.get('activity_master_json') else {}
        cover = a.get('cover')
        html = _resolve_media_to_file(html, master, _ROOT,
                                     ('/static/uploads/' + cover) if cover else None)
        out_dir = os.path.join(_ROOT, 'static', 'uploads', str(club_id), 'promos')
        os.makedirs(out_dir, exist_ok=True)
        fname = 'longpic_%d_%d.png' % (activity_id, asset['id'])
        out_path = os.path.join(out_dir, fname)
        render_html_to_png(html, out_path, width=750, scale=2, root=_ROOT)
        img_url = ('/api/club/%d/activities/%d/shared-image/%s' % (club_id, activity_id, fname))
        body['imageUrl'] = img_url
        with conn() as c2:
            c2.execute('UPDATE content_assets SET body_json=? WHERE id=?', (jdump(body), asset['id']))
    return {'imageUrl': img_url, 'assetId': asset['id']}


@app.post('/api/club/{club_id}/activities/{activity_id}/render-promo')
def render_promo_image(club_id: int, activity_id: int):
    """把活动详情的「宣传区块(promoHtml)」渲染成真实分享图（解决微信分享带不走 iframe）。"""
    with conn() as c:
        a = row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?', (activity_id, club_id)))
        if not a:
            raise HTTPException(404, '活动不存在')
        detail = jload(a['detail_json'], {}) if a.get('detail_json') else {}
        html = detail.get('promoHtml') or ''
        if not html:
            raise HTTPException(400, '该活动详情还没有宣传区块(promoHtml)。')
        master = jload(a['activity_master_json'], {}) if a.get('activity_master_json') else {}
        cover = a.get('cover')
        html = _resolve_media_to_file(html, master, _ROOT,
                                     ('/static/uploads/' + cover) if cover else None)
        out_dir = os.path.join(_ROOT, 'static', 'uploads', str(club_id), 'promos')
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
        fname = 'promo_%d_%s.png' % (activity_id, ts)
        out_path = os.path.join(out_dir, fname)
        render_html_to_png(html, out_path, width=750, scale=2, root=_ROOT)
        img_url = ('/api/club/%d/activities/%d/shared-image/%s' % (club_id, activity_id, fname))
    return {'imageUrl': img_url}


@app.get('/api/club/{club_id}/activities/{activity_id}/shared-image/{fname}')
def shared_image(club_id: int, activity_id: int, fname: str):
    """流出渲染好的分享图（长图 / promo）。俱乐部鉴权路由，prod 不走 /static/uploads 裸链。"""
    if not re.match(r'^[A-Za-z0-9_\-]+\.png$', fname or ''):
        raise HTTPException(400, '非法的文件名')
    p = os.path.join(_ROOT, 'static', 'uploads', str(club_id), 'promos', fname)
    if not os.path.exists(p):
        raise HTTPException(404, '图片不存在')
    return FileResponse(p, media_type='image/png', filename=fname)


# ══ 活动主视觉：AI 一键生成（通义万相，复用 DashScope Key）══
# 解决「没上传照片就撑不起图文」：零上传也能生成一张符合调性的户外主视觉，
# 落盘为活动封面，详情页封面大图 / 长图 / promoHtml 都能直接吃到。
_HERO_JOBS: dict[tuple[int, int], dict] = {}


async def _hero_job(club_id: int, activity_id: int, usage_id: int | None = None):
    job = _HERO_JOBS.setdefault((club_id, activity_id), {'status': 'running'})
    try:
        with conn() as c:
            a = row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',
                              (activity_id, club_id)))
            if not a:
                raise RuntimeError('活动不存在')
        # 风格家族 → 调性词，让主视觉与这场活动已派发的版面同调
        try:
            master0 = jload(a['activity_master_json'], {}) if a.get('activity_master_json') else {}
            sk = pick_detail_style(master=master0)
            style_name = (_DETAIL_STYLES.get(sk) or {}).get('name', '')
        except Exception:
            style_name = ''
        master = jload(a['activity_master_json'], {}) if a.get('activity_master_json') else {}
        prompt = build_hero_prompt(master, style_name)
        urls = await generate_hero(prompt, size='1024*1280', n=1)
        rel = await save_hero_image(club_id, urls[0], _ROOT)
        with conn() as c:
            c.execute('UPDATE activities SET cover=? WHERE id=? AND club_id=?',
                      (rel, activity_id, club_id))
            # 同步进资料媒体池，promoHtml / 长图也能引用这张 AI 主视觉
            try:
                mm = jload(a['activity_master_json'], {}) if a.get('activity_master_json') else {}
                media = mm.get('media') or []
                media.append({'ref': 'ai-hero', 'url': '/static/uploads/' + rel,
                              'kind': 'photo', 'source': 'ai'})
                mm['media'] = media
                c.execute('UPDATE activities SET activity_master_json=? WHERE id=? AND club_id=?',
                          (jdump(mm), activity_id, club_id))
            except Exception:
                pass
        job.update(status='done', imageUrl=('/static/uploads/' + rel))
        # 生成成功 → 扣 Credits（默认 ai_cost_hero=10，与 poster 同级），
        # 并回填 usage 记录状态 / 平台成本（通义万相 Plus ¥0.2/张）。
        try:
            if usage_id:
                charge_credits(club_id, 'hero', usage_id)
                with conn() as c:
                    c.execute('UPDATE ai_usage_records SET status=?,provider_cost=? WHERE id=?',
                              ('success', 0.2, usage_id))
        except Exception:
            pass
    except Exception as e:  # 任何异常都要让前端退出轮询，不能永远转圈
        job.update(status='error', error=str(e))
        try:
            if usage_id:
                with conn() as c:
                    c.execute('UPDATE ai_usage_records SET status=?,error=? WHERE id=?',
                              ('error', str(e)[:400], usage_id))
        except Exception:
            pass


@app.post('/api/club/{club_id}/activities/{activity_id}/generate-hero')
def generate_hero_endpoint(club_id: int, activity_id: int, background_tasks: BackgroundTasks):
    """后台一键生成活动主视觉海报（异步）。返回 jobId，前端轮询 /status。

    计费：先 ensure_credits 校验俱乐部启用 + AI Credits 余额，再落一条 pending 的
    ai_usage_records 记录（usage_id）交给后台作业；作业成功才 charge_credits。
    """
    club_or_404(club_id); ensure_credits(club_id, 'hero')
    usage_id = _record_usage(club_id=club_id, task_type='hero',
                             request_id='hero-' + uuid.uuid4().hex,
                             provider='dashscope', model='wanx2.1-t2i-plus', status='pending')
    background_tasks.add_task(_hero_job, club_id, activity_id, usage_id)
    return {'jobId': '%d-%d' % (club_id, activity_id), 'status': 'queued'}


@app.get('/api/club/{club_id}/activities/{activity_id}/generate-hero/status')
def generate_hero_status(club_id: int, activity_id: int):
    return _HERO_JOBS.get((club_id, activity_id)) or {'status': 'idle'}


# ══ 活动回顾：异步作业表（与长图同理，live 真模型常超 55s 网关上限）══
_RECAP_JOBS: dict[tuple[int, int], dict] = {}

async def _recap_job(club_id:int,activity_id:int,master:dict,note:str,entries:list[dict],layout_seed:str=''):
    # 独立回顾 activity_id 为 None（解耦、不绑定系统活动）；作业 key 用 (club_id, activity_id or 0) 保持一致
    job=_RECAP_JOBS.setdefault((club_id,activity_id or 0),{'status':'running'})
    try:
        # ★ 2026-11-04：先做视觉打标，给每张现场照片补一句**画面描述**（desc）。
        #   没有它，图注位只能显示文件名/哈希（用户截图里那行「b6598b6b69…」乱码），
        #   写作模型也看不到照片内容，写出来的段落与照片对不上。
        #   打标失败只降级为「无描述」，绝不让整条回顾生成挂掉。
        try:
            caps=await caption_media(club_id,master)
            try:
                persist_captions(master,caps)
                # 打标结果要落回活动资料，否则下次生成又当成"没打标"重跑一遍。
                if activity_id:
                    with conn() as c:
                        c.execute('UPDATE activities SET activity_master_json=?,updated_at=CURRENT_TIMESTAMP '
                                  'WHERE id=? AND club_id=?',(jdump(master),activity_id,club_id))
            except Exception: pass
        except Exception: pass
        by_ref={str(x.get('ref')):x for x in (master.get('media') or []) if isinstance(x,dict)}
        for e in entries:
            mm=by_ref.get(str(e.get('ref')))
            if mm:
                d=str(mm.get('desc') or '').strip()
                if d:e['desc']=d
                if mm.get('usable') is not None:e['usable']=mm.get('usable')
        try: content,usage=await generate_recap(club_id,master,note,entries,layout_seed)
        except AIGatewayError as e:
            job.update(status='error',error=str(e)); return
        charge_credits(club_id,'recap',usage.usage_id)
        with conn() as c:
            # content_assets.activity_id 允许 NULL（独立回顾）；按活动生成的仍填真实活动 id
            act_val=activity_id if (activity_id and activity_id>0) else None
            cur=c.execute('INSERT INTO content_assets(club_id,activity_id,channel,title,body_json) VALUES(?,?,?,?,?)',(
                club_id,act_val,'recap',content.get('title') or '',jdump(content)))
            asset_id=cur.lastrowid
        job.update(status='done',assetId=asset_id,title=content.get('title'),
                   skipped=job.get('skipped') or [])
    except Exception as e:  # 兜底：任何未预期异常都要让前端退出轮询
        job.update(status='error',error=f'生成过程出现异常：{e}')

@app.post('/api/club/{club_id}/activities/{activity_id}/channel/{channel}')
async def channel_generate(club_id:int,activity_id:int,channel:str):
    # recap（活动回顾）2026-10-18 起不再是宣发渠道：它属于「活动结束之后」的独立流程，
    # 素材是事后上传的现场照片与文案，走 POST .../activities/{id}/recap。
    if channel not in {'wechat','xhs','poster','longpic'}: raise HTTPException(400,'unsupported channel')
    ensure_credits(club_id,channel)
    with conn() as c:a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
    if not a: raise HTTPException(404,'活动不存在')
    master=jload(a['activity_master_json'],{});detail=jload(a['detail_json'],{})
    master=_strip_recap_media(master)
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
        # 落库用 master_full 持有 media 列表的独立副本（_strip_recap_media 是原地替换，
        # 浅引用会被一起剥掉）；生成用剥离回顾照片的版本。
        master_full=dict(master)
        if isinstance(master.get('media'),list): master_full['media']=list(master['media'])
        master=_strip_recap_media(master)   # 生成用：剥离回顾照片
        # 已有作业在跑：直接回当前状态，绝不并发重跑（防止双倍 Credits + 双份成品）。
        prev=_LONGPIC_JOBS.get((club_id,activity_id))
        if prev and prev.get('status')=='running':
            return {'pending':True,'channel':'longpic','activityId':activity_id,
                    'status':'running','note':'该活动的长图正在生成中，请等待完成。'}
        with conn() as _c:
            _prev=_c.execute('SELECT COUNT(*) FROM content_assets WHERE club_id=? AND activity_id=? '
                             "AND channel='longpic'",(club_id,activity_id)).fetchone()[0]
        seed=activity_id*997+int(_prev or 0)
        _LONGPIC_JOBS[(club_id,activity_id)]={'status':'running','started':datetime.utcnow().isoformat()}
        asyncio.create_task(_longpic_job(club_id,activity_id,master,detail,
                                         source_text,cover_url,seed,persist_master=master_full))
        return {'pending':True,'channel':'longpic','activityId':activity_id,'status':'running'}

    try: content,usage=await generate_channel(club_id,master,detail,channel,cover_url=cover_url,source_text=source_text)
    except AIGatewayError as e: raise HTTPException(502,str(e))
    charge_credits(club_id,channel,usage.usage_id)
    with conn() as c:c.execute('INSERT INTO content_assets(club_id,activity_id,channel,title,body_json) VALUES(?,?,?,?,?)',(
        club_id,activity_id,channel,content.get('title') or (content.get('titleOptions') or [''])[0],jdump(content)))
    return content


@app.post('/api/club/{club_id}/activities/{activity_id}/recap')
async def recap_generate(club_id:int,activity_id:int,note:str=Form(''),
                         files:list[UploadFile]=File(default=[])):
    """活动回顾（2026-10-18 独立流程，替换原渠道版 recap）。

    逻辑（老板定的）：回顾不是宣发渠道——活动结束之后，老板上传现场照片 + 写几句
    基础文案，系统再生成回顾。硬闸：
    ① 活动必须已结束（按日期文案解析「最后一天」< 今天；解析不出来就拒绝，不猜）；
    ② 至少要有 1 张现场照片或 1 段现场记录——回顾只写真事，没有素材就明说，不编造。
    照片入库 master.media（source='recap'），渲染走 /api/club/.../media/ 白名单；
    宣发渠道的媒体池会剔除这批照片（_strip_recap_media）。
    生成走异步作业（与长图同理：live 真模型带完整写作契约常超 55s，同步必被网关掐断），
    本端点只做校验 + 落盘 + 起任务，立即返回 {pending:true}，前端轮询 /recap/status。
    """
    club_or_404(club_id); ensure_credits(club_id,'recap')
    with conn() as c:
        a=row(c.execute('SELECT * FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
    if not a: raise HTTPException(404,'活动不存在')
    master=_repair_master_media(jload(a['activity_master_json'],{}) or {},_activity_source(a)[0])
    last=_activity_last_date(a.get('event_date'),(master or {}).get('date'))
    if last is None:
        _raw=str(a.get('event_date') or (master or {}).get('date') or '').strip() or '未填写'
        raise HTTPException(422,f'无法从活动日期（{_raw}）判断这场活动是否已结束。'
                                '活动回顾只能给「已结束」的活动生成，请先把活动日期补充为具体日期。')
    if last>=date.today():
        raise HTTPException(422,f'这场活动还没结束（{last.isoformat()}），还不能生成回顾。'
                                '活动结束后，上传现场照片和基础文案再来生成。')
    note=(note or '').strip()
    img_files=[f for f in (files or [])
               if Path(f.filename or '').suffix.lower() in _IMAGE_UPLOAD_EXTS]
    if not img_files and not note:
        raise HTTPException(422,'回顾必须有真实素材：请至少上传 1 张现场照片，'
                                '或填写几句现场记录（人数、天气、路线、高光时刻……）。')
    recap_entries=[];_skipped=[]
    if img_files:
        batch=UPLOAD/str(club_id)/uuid.uuid4().hex
        try:
            saved=save_uploads(img_files,batch,
                               max_total_bytes=20*1024*1024 if IS_PROD else None,
                               skipped_out=_skipped)
        except ValueError as e:
            shutil.rmtree(batch,ignore_errors=True)
            raise HTTPException(413,str(e))
        # ★ ref 编号要接着这场活动已有的回顾照片往后排：每批都从 rcap_01 起会撞号，
        #   ref→url 映射里旧图被新图顶掉，旧回顾成品的图片会串成新照片（实测复现过）。
        _rcap_base=sum(1 for m in (master.get('media') or [])
                       if isinstance(m,dict) and str(m.get('ref') or '').startswith('rcap_'))
        for i,p in enumerate(saved,1):
            w=h=None
            try:
                with Image.open(p) as im:w,h=im.size
            except Exception:pass
            recap_entries.append({'ref':f'rcap_{_rcap_base+i:02d}','url':f'/static/uploads/{club_id}/{batch.name}/{p.name}',
                                  'kind':'photo','source':'recap','name':p.name,
                                  # ★ 2026-11-04：desc 原来写的是文件名（p.stem），后果有两个：
                                  #   ① 打标模块把"已有描述"当成已完成 → **从不生成画面描述**；
                                  #   ② 没有描述时图注位只能显示文件名 → 用户看到一串哈希乱码。
                                  #   现在留空，交给视觉打标补；打标失败则留空（前端不出图注）。
                                  'desc':'','width':w,'height':h})
        if not recap_entries and not note:
            _names='、'.join(str(x or '文件') for x in _skipped[:3]) or '上传的文件'
            raise HTTPException(422,f'{_names}都没能作为照片读入。请换用 jpg/png 等常见图片格式，'
                                    '或直接填写几句现场记录。')
    # 现场照片进 master.media：club 端媒体代理白名单=master.media，不入库就渲染不出来。
    if recap_entries:
        _have={str(m.get('url')) for m in (master.get('media') or []) if isinstance(m,dict)}
        for e in recap_entries:
            if e['url'] not in _have:master.setdefault('media',[]).append(e)
        with conn() as c:
            c.execute('UPDATE activities SET activity_master_json=?,updated_at=CURRENT_TIMESTAMP '
                      'WHERE id=? AND club_id=?',(jdump(master),activity_id,club_id))
    # 已有作业在跑：直接回当前状态，绝不并发重跑（防止双倍 Credits + 双份成品）。
    prev=_RECAP_JOBS.get((club_id,activity_id))
    if prev and prev.get('status')=='running':
        return {'pending':True,'channel':'recap','activityId':activity_id,
                'status':'running','note':'这场活动的回顾正在生成中，请等待完成。'}
    _RECAP_JOBS[(club_id,activity_id)]={'status':'running','started':datetime.utcnow().isoformat(),
                                        'skipped':[str(x) for x in _skipped]}
    asyncio.create_task(_recap_job(club_id,activity_id,master,note,recap_entries))
    return {'pending':True,'channel':'recap','activityId':activity_id,'status':'running'}


@app.get('/api/club/{club_id}/activities/{activity_id}/recap/status')
def recap_status(club_id:int,activity_id:int):
    return _RECAP_JOBS.get((club_id,activity_id)) or {'status':'idle'}


# ══ 活动回顾（解耦版，2026-10-30 起）：不绑定系统里的活动 ══
# 老板的新逻辑：俱乐部管理人员直接输入关键活动信息（名称/日期/地点/规模）+ 现场照片 + 基础文案，
# 系统就自动生成活动回顾，无需先建一场「已结束的活动」。素材门槛不变（至少 1 张照片或 1 段记录）。
# 落库到 content_assets(activity_id=0)，与按活动生成的回顾区分；状态走 /api/club/{club_id}/recap/status。
@app.post('/api/club/{club_id}/recap')
async def recap_generate_standalone(club_id:int,title:str=Form(''),event_date:str=Form(''),
                                    location:str=Form(''),scale:str=Form(''),note:str=Form(''),
                                    files:list[UploadFile]=File(default=[])):
    """活动回顾（解耦版）：不挑活动，直接填关键信息 + 现场照片，AI 自动生成回顾。"""
    club_or_404(club_id); ensure_credits(club_id,'recap')
    title=(title or '').strip()
    if not title:
        raise HTTPException(422,'请填写活动名称（生成回顾需要的标题）。')
    note=(note or '').strip()
    img_files=[f for f in (files or [])
               if Path(f.filename or '').suffix.lower() in _IMAGE_UPLOAD_EXTS]
    if not img_files and not note:
        raise HTTPException(422,'回顾必须有真实素材：请至少上传 1 张现场照片，'
                                '或填写几句现场记录（人数、天气、路线、高光时刻……）。')
    # 用自由输入拼一个最小 master：generate_recap 只读 title/date/location/capacity/duration
    master={'title':title,'date':(event_date or '').strip(),'location':(location or '').strip(),
            'capacity':(scale or '').strip() or None,'duration':None,'media':[]}
    recap_entries=[];_skipped=[]
    if img_files:
        batch=UPLOAD/str(club_id)/uuid.uuid4().hex
        try:
            saved=save_uploads(img_files,batch,
                               max_total_bytes=20*1024*1024 if IS_PROD else None,
                               skipped_out=_skipped)
        except ValueError as e:
            shutil.rmtree(batch,ignore_errors=True)
            raise HTTPException(413,str(e))
        for i,p in enumerate(saved,1):
            w=h=None
            try:
                with Image.open(p) as im:w,h=im.size
            except Exception:pass
            recap_entries.append({'ref':f'rcap_{i:02d}','url':f'/static/uploads/{club_id}/{batch.name}/{p.name}',
                                  'kind':'photo','source':'recap','name':p.name,
                                  # 同上：desc 留空，交给视觉打标（用文件名会挡住打标并变成乱码图注）
                                  'desc':'','width':w,'height':h})
        if not recap_entries and not note:
            _names='、'.join(str(x or '文件') for x in _skipped[:3]) or '上传的文件'
            raise HTTPException(422,f'{_names}都没能作为照片读入。请换用 jpg/png 等常见图片格式，'
                                    '或直接填写几句现场记录。')
    if recap_entries:
        master['media']=recap_entries
    # activity_id=0 表示独立回顾；与按活动生成的 job 互不干扰（活动 id 从 1 起）
    key=(club_id,0)
    prev=_RECAP_JOBS.get(key)
    if prev and prev.get('status')=='running':
        return {'pending':True,'channel':'recap','activityId':0,
                'status':'running','note':'这场回顾正在生成中，请等待完成。'}
    _RECAP_JOBS[key]={'status':'running','started':datetime.utcnow().isoformat(),
                      'skipped':[str(x) for x in _skipped]}
    # 独立回顾没有活动：把自由输入的最小 master 直接传进去（不回查活动库），activity_id=None
    asyncio.create_task(_recap_job(club_id,None,master,note,recap_entries))
    return {'pending':True,'channel':'recap','activityId':0,'status':'running'}


@app.get('/api/club/{club_id}/recap/status')
def recap_status_standalone(club_id:int):
    return _RECAP_JOBS.get((club_id,0)) or {'status':'idle'}


@app.post('/api/club/{club_id}/recap/retypeset')
async def recap_retypeset(club_id:int,asset_id:int=Form(0)):
    """「换一版排版」：用**上一次同一批素材**（现场照片 + 现场记录 + 事实锚点）再跑一次 AI，
    只换版式、不改素材，不要求用户重新上传照片。版式种子让每次的结构真的不一样。
    与首次生成共用 (club_id,0) 作业与 /recap/status 轮询，天然防并发、防双份计费。"""
    club_or_404(club_id); ensure_credits(club_id,'recap')
    with conn() as c:
        if asset_id:
            r=row(c.execute("SELECT * FROM content_assets WHERE id=? AND club_id=? AND channel='recap'",(asset_id,club_id)))
        else:
            r=row(c.execute("SELECT * FROM content_assets WHERE club_id=? AND channel='recap' ORDER BY id DESC LIMIT 1",(club_id,)))
    if not r: raise HTTPException(404,'没有可换版的回顾，请先生成一份活动回顾。')
    body=jload(r['body_json'],{}) or {}
    media=[m for m in (body.get('media') or []) if isinstance(m,dict) and m.get('ref')]
    note=str(body.get('note') or '')
    if not media and not note:
        raise HTTPException(422,'这份回顾没有留存原始素材，请重新上传照片生成一次再换版。')
    facts=body.get('facts') if isinstance(body.get('facts'),dict) else {}
    master={'title':str(body.get('title') or r['title'] or ''),
            'date':facts.get('date'),'location':facts.get('location'),
            'capacity':facts.get('capacity'),'duration':facts.get('duration'),
            'media':list(media)}
    key=(club_id,0)
    prev=_RECAP_JOBS.get(key)
    if prev and prev.get('status')=='running':
        return {'pending':True,'channel':'recap','activityId':0,'status':'running','note':'正在生成中，请稍候。'}
    seed=uuid.uuid4().hex[:8]
    _RECAP_JOBS[key]={'status':'running','started':datetime.utcnow().isoformat(),
                      'skipped':[],'retypeset':True}
    asyncio.create_task(_recap_job(club_id,None,master,note,media,seed))
    return {'pending':True,'channel':'recap','activityId':0,'status':'running'}


# ══ 活动回顾（含独立回顾）现场照片的俱乐部鉴权媒体路由 ══
# 回顾照片物理上在 UPLOAD/{club}/...，生产环境 /static/uploads/* 整体 404（防外链 + 防越权）。
# 活动绑定的回顾走 /api/club/{club}/activities/{id}/media；独立回顾没有活动（activity_id=0），
# 这里专门给回顾照片一个俱乐部会话鉴权的取回口，白名单=本俱乐部所有回顾内容的现场照片 url。
@app.get('/api/club/{club_id}/recap-media/{asset_path:path}')
def recap_media(club_id:int,asset_path:str):
    club_or_404(club_id)
    original='/static/'+unquote(asset_path)
    file_path=_safe_media_path(original)
    if not file_path:raise HTTPException(404,'not found')
    # 白名单=本俱乐部所有回顾（含独立回顾与按活动生成）内容 JSON 里登记的现场照片 url
    allowed=set()
    with conn() as c:
        rws=rows(c.execute("SELECT body_json FROM content_assets WHERE club_id=? AND channel='recap'",(club_id,)))
    for r in rws:
        try:
            b=jload(r['body_json'],{}) or {}
            for m in (b.get('media') or []):
                if isinstance(m,dict) and m.get('url'): allowed.add(str(m['url']))
        except Exception: pass
    if original not in allowed or not file_path.is_file():raise HTTPException(404,'not found')
    response=FileResponse(file_path)
    response.headers['Cache-Control']='private, max-age=60'
    return response

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
_CLUB_SETTINGS_FIELDS=('name','slogan','city','contact_name','contact_phone','insurance_coverage_wan')

@app.get('/api/club/{club_id}/settings')
def club_settings_get(club_id:int):
    x=club_or_404(club_id)
    return {'name':x['name'],'slogan':x.get('slogan') or '','city':x.get('city') or '',
            'contactName':x.get('contact_name') or '','contactPhone':x.get('contact_phone') or '',
            'logoUrl':x.get('logo_url') or '','insuranceCoverageWan':float(x.get('insurance_coverage_wan') or 0)}

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
    # 保险保额是数值字段（单位：万），与上面的字符串字段分开校验：空/0 表示「未配置」
    if 'insurance_coverage_wan' in fields:
        try:
            _wan=float(str(fields['insurance_coverage_wan'] or '0') or '0')
        except (TypeError,ValueError):
            raise HTTPException(400,'保险保额必须是数字（单位：万）')
        if _wan<0 or _wan>9999: raise HTTPException(400,'保险保额超出合理范围')
        fields['insurance_coverage_wan']=_wan
    if not fields: raise HTTPException(400,'没有需要更新的字段')
    with conn() as c:
        sets=','.join(f'{k}=?' for k in fields)
        c.execute(f'UPDATE clubs SET {sets} WHERE id=?',(*fields.values(),club_id))
    return {'ok':True,'updated':sorted(fields.keys())}

# ── C 端「我的」页文案：联系我们 / 关于俱乐部 ────────────────────────────────
# 顾客点开的两页原来全是前端写死的占位文案（9:00-21:00、v0.25、topoutdoor.cn）。
# 现在由俱乐部自己定义；未填的字段不下发（见 c_profile.public_view），
# C 端只在有值时渲染那一行 —— 不拿默认值编造营业时间。
@app.get('/api/club/{club_id}/c-profile')
def club_c_profile_get(club_id:int):
    x=club_or_404(club_id)
    return {'profile':c_profile.from_club(x),'slogan':x.get('slogan') or '',
            'contactPhone':x.get('contact_phone') or ''}

@app.put('/api/club/{club_id}/c-profile')
def club_c_profile_put(club_id:int,payload:dict=Body(...)):
    x=club_or_404(club_id)
    try: cfg=c_profile.normalize(payload,c_profile.from_club(x))
    except ValueError as e: raise HTTPException(400,str(e))
    with conn() as c:
        c.execute('UPDATE clubs SET c_profile_json=? WHERE id=?',(c_profile.dumps(cfg),club_id))
    return {'ok':True,'profile':cfg}

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


# ── 俱乐部小程序资产管理：小程序信息 / 管理助手 / 标准设置（2026-10-26）───────
# 每个租用 ClubOS 的俱乐部都会有自己的微信小程序；认证信息（appid/主体/类目/
# 年审/支付品牌）、授权状态、体验者、管理助手（员工+角色权限）与标准设置
# （基础/转发/支付）在此集中管理。
# 说明：与微信开放平台的真实对接（授权托管、代码上传、版本发布、员工微信侧
# 绑定）是后续版本；当前是「线下登记 + 后台台账管理」模型，字段与微信公开
# 平台一一对应，方便后续直接迁移。
MP_PERMS={'activities':'活动管理','registrations':'报名管理','content':'内容生成',
          'members':'会员管理','mall':'商城查看','finance':'数据与结算',
          'settings':'俱乐部设置','miniprogram':'小程序管理'}
_MP_SYSTEM_ROLES=(('管理员',sorted(MP_PERMS.keys()),'拥有全部管理权限（系统预置）'),
                  ('活动运营',('activities','registrations','content','members'),'日常发团与报名、内容运营'),
                  ('内容编辑',('content',),'只负责宣发内容'))
_MP_INFO_TEXT=('appid','app_name','app_intro','subject_type','subject_name',
               'realname_status','qualification_status','annual_review_at',
               'pay_brand_name','pay_brand_id','current_version','privacy_status',
               'geo_permission','record_status','release_status')
_MP_INFO_LEN={'appid':64,'app_name':40,'app_intro':300,'subject_type':20,'subject_name':80,
              'realname_status':20,'qualification_status':20,'annual_review_at':32,
              'pay_brand_name':60,'pay_brand_id':32,'current_version':20,'privacy_status':20,
              'geo_permission':20,'record_status':20,'release_status':20}
# 前端/调用方统一发 camelCase；这里归一成 DB snake_case 列
_MP_INFO_CAMEL={'appName':'app_name','appIntro':'app_intro','subjectType':'subject_type',
                'subjectName':'subject_name','realnameStatus':'realname_status',
                'qualificationStatus':'qualification_status','annualReviewAt':'annual_review_at',
                'payBrandName':'pay_brand_name','payBrandId':'pay_brand_id',
                'currentVersion':'current_version','privacyStatus':'privacy_status',
                'geoPermission':'geo_permission','recordStatus':'record_status',
                'releaseStatus':'release_status'}

def _mp_get(c,club_id):
    return row(c.execute('SELECT * FROM club_miniprogram WHERE club_id=?',(club_id,)))

def _mp_public(x):
    """DB 行 → 下发 dict（camelCase + JSON 列解析；无行给全空骨架）。"""
    if not x: x={}
    def _jl(v,fb):
        try:
            v=json.loads(v) if isinstance(v,str) else (v or fb)
            return v if isinstance(v,list) else fb
        except Exception: return fb
    return {'appid':x.get('appid') or '','appName':x.get('app_name') or '',
            'appIntro':x.get('app_intro') or '','avatarUrl':x.get('avatar_url') or '',
            'qrcodeUrl':x.get('qrcode_url') or '','subjectType':x.get('subject_type') or '',
            'subjectName':x.get('subject_name') or '',
            'serviceCategories':_jl(x.get('service_categories_json'),[]),
            'realnameStatus':x.get('realname_status') or '','qualificationStatus':x.get('qualification_status') or '',
            'annualReviewAt':x.get('annual_review_at') or '','payBrandName':x.get('pay_brand_name') or '',
            'payBrandId':x.get('pay_brand_id') or '','currentVersion':x.get('current_version') or '',
            'privacyStatus':x.get('privacy_status') or '','geoPermission':x.get('geo_permission') or '',
            'recordStatus':x.get('record_status') or '','releaseStatus':x.get('release_status') or '',
            'authorized':bool(x.get('authorized')),
            'testers':_jl(x.get('testers_json'),[]),'updatedAt':x.get('updated_at') or ''}

def _mp_seed_roles(c,club_id):
    """该俱乐部还没有角色时，预置 3 个系统角色（幂等：只看有没有任何角色）。"""
    if c.execute('SELECT 1 FROM club_mp_roles WHERE club_id=? LIMIT 1',(club_id,)).fetchone(): return
    for name,perms,remark in _MP_SYSTEM_ROLES:
        c.execute('INSERT INTO club_mp_roles(club_id,name,perms_json,remark,system) VALUES(?,?,?,?,1)',
                  (club_id,name,json.dumps(list(perms),ensure_ascii=False),remark))

def _mp_role_public(r):
    try: perms=json.loads(r['perms_json']) if isinstance(r['perms_json'],str) else (r['perms_json'] or [])
    except Exception: perms=[]
    return {'id':int(r['id']),'name':r['name'],'perms':[p for p in perms if p in MP_PERMS],
            'remark':r['remark'] or '','system':bool(r['system']),'createdAt':r['created_at']}

@app.get('/api/club/{club_id}/miniprogram')
def club_miniprogram_get(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        return _mp_public(_mp_get(c,club_id))

@app.put('/api/club/{club_id}/miniprogram')
def club_miniprogram_put(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    # camelCase → snake_case 归一（前端统一发 camelCase；snake_case 兼容保留）
    for camel,snake in _MP_INFO_CAMEL.items():
        if camel in payload and snake not in payload:
            payload[snake]=payload[camel]
    fields={}
    for f in _MP_INFO_TEXT:
        if f not in payload: continue
        v=str(payload.get(f) or '').strip()
        if len(v)>_MP_INFO_LEN[f]: raise HTTPException(400,f'{f} 超长（上限 {_MP_INFO_LEN[f]} 字）')
        fields[f]=v or None
    if 'authorized' in payload: fields['authorized']=1 if payload.get('authorized') else 0
    if payload.get('avatarClear'):
        # 「恢复自动」：清掉自定义头像，前端回落到俱乐部 Logo（2026-10-26 用户反馈：
        # 老板设置 Logo 后小程序头像应该自动就有，自定义头像只是可覆盖项）
        fields['avatar_url']=None
    if 'serviceCategories' in payload:
        cats=[str(x).strip()[:60] for x in (payload.get('serviceCategories') or []) if str(x).strip()]
        if len(cats)>10: raise HTTPException(400,'服务类目最多 10 个')
        fields['service_categories_json']=json.dumps(cats,ensure_ascii=False)
    if 'testers' in payload:
        ts=[]
        for t in (payload.get('testers') or [])[:20]:
            if not isinstance(t,dict): continue
            nick=str(t.get('nickname') or '').strip()[:30]
            if not nick: continue
            ts.append({'nickname':nick,'wxid':str(t.get('wxid') or '').strip()[:64],
                       'boundAt':str(t.get('boundAt') or '').strip()[:32]})
        fields['testers_json']=json.dumps(ts,ensure_ascii=False)
    if not fields: raise HTTPException(400,'没有需要更新的字段')
    with conn() as c:
        if not _mp_get(c,club_id):
            c.execute('INSERT INTO club_miniprogram(club_id) VALUES(?)',(club_id,))
        sets=','.join(f'{k}=?' for k in fields)
        c.execute(f"UPDATE club_miniprogram SET {sets},updated_at=CURRENT_TIMESTAMP WHERE club_id=?",
                  (*fields.values(),club_id))
        out=_mp_public(_mp_get(c,club_id))
    return {'ok':True,'info':out}

# kind → 存储：avatar/qrcode 落 club_miniprogram 列；版权图标/转发封面落
# club_mp_settings.settings_json 对应字段（2026-10-26 用户反馈：设置里的图片
# 必须是上传，不能贴 URL）。
_MP_IMG_COLUMN={'avatar':'avatar_url','qrcode':'qrcode_url'}
_MP_IMG_SETTING={'copyrighticon':('base','copyrightIcon'),
                 'friendcover':('share','friendCover'),
                 'momentcover':('share','momentCover')}

def _mp_settings_load(c,club_id):
    r=row(c.execute('SELECT settings_json FROM club_mp_settings WHERE club_id=?',(club_id,)))
    try: saved=json.loads(r['settings_json']) if r and isinstance(r['settings_json'],str) else {}
    except Exception: saved={}
    return saved if isinstance(saved,dict) else {}

def _mp_settings_merge(saved):
    out={}
    for grp,defaults in _MP_SETTINGS_DEFAULTS.items():
        src=saved.get(grp) if isinstance(saved.get(grp),dict) else {}
        out[grp]={**defaults,**src}
    return out

@app.post('/api/club/{club_id}/miniprogram/image/{kind}')
async def club_mp_image_upload(club_id:int,kind:str,file:UploadFile=File(...)):
    """上传小程序图片（头像/码/版权图标/转发封面）。固定文件名覆盖写，URL 不变
    （前端加时间戳破缓存）。kind 大小写不敏感。"""
    club_or_404(club_id)
    k=kind.lower()
    col=_MP_IMG_COLUMN.get(k)
    set_target=_MP_IMG_SETTING.get(k)
    if not col and not set_target:
        raise HTTPException(400,'kind 仅支持 avatar / qrcode / copyrightIcon / friendCover / momentCover')
    real=Path(file.filename or '').suffix.lower()
    if real not in _PUBLIC_IMAGE_EXT: raise HTTPException(400,'仅支持图片文件：png/jpg/jpeg/webp/gif')
    data=await file.read()
    if len(data)>5*1024*1024: raise HTTPException(413,'图片过大（上限 5MB）')
    dest=UPLOAD/str(club_id)/'mp'
    dest.mkdir(parents=True,exist_ok=True)
    (dest/f'{k}{real}').write_bytes(data)
    for stale in dest.glob(f'{k}.*'):
        if stale.suffix.lower()!=real: stale.unlink(missing_ok=True)
    url=f'/api/club/{club_id}/miniprogram/{k}{real}'
    with conn() as c:
        if col:
            if not _mp_get(c,club_id):
                c.execute('INSERT INTO club_miniprogram(club_id) VALUES(?)',(club_id,))
            c.execute(f'UPDATE club_miniprogram SET {col}=?,updated_at=CURRENT_TIMESTAMP WHERE club_id=?',(url,club_id))
        else:
            grp,field=set_target
            saved=_mp_settings_load(c,club_id)
            saved.setdefault(grp,{})[field]=url
            blob=json.dumps(saved,ensure_ascii=False)
            if len(blob)>64*1024: raise HTTPException(400,'设置内容过大')
            c.execute('''INSERT INTO club_mp_settings(club_id,settings_json,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)
                         ON CONFLICT(club_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=CURRENT_TIMESTAMP''',
                      (club_id,blob))
    return {'url':url}

@app.get('/api/club/{club_id}/miniprogram/{kind}.{ext}')
def club_mp_image(club_id:int,kind:str,ext:str):
    """登录态代理读小程序图片（形状校验后按路由参数拼路径，不掺任意片段）。"""
    k=kind.lower()
    if k not in _MP_IMG_COLUMN and k not in _MP_IMG_SETTING: raise HTTPException(404,'不存在的图片类型')
    with conn() as c:
        if k in _MP_IMG_COLUMN:
            x=_mp_get(c,club_id)
            url=(x or {}).get(_MP_IMG_COLUMN[k]) if x else None
        else:
            saved=_mp_settings_load(c,club_id)
            grp,field=_MP_IMG_SETTING[k]
            url=(saved.get(grp) or {}).get(field) if isinstance(saved.get(grp),dict) else None
    if not url: raise HTTPException(404,'尚未设置该图片')
    m=re.match(r'^/api/club/(\d+)/miniprogram/([a-z]+)(\.[a-z0-9]{2,5})$',str(url),re.I)
    if not m or int(m.group(1))!=int(club_id) or m.group(2).lower()!=k: raise HTTPException(404,'尚未设置该图片')
    ext='.'+str(ext).lower().lstrip('.')
    if ext not in _PUBLIC_IMAGE_EXT: raise HTTPException(404,'不支持的图片格式')
    fp=UPLOAD/f'{int(club_id)}'/'mp'/f'{k}{ext}'
    if not fp.is_file(): raise HTTPException(404,'图片文件已丢失')
    return FileResponse(fp,headers={'Cache-Control':'no-store'})

# ── 小程序标准设置（基础 / 转发 / 支付三组，JSON 归一）────────────────────────
_MP_SETTINGS_DEFAULTS={
    'base':{'copyrightIcon':'','privacyStyle':'wx','firstAuthNickname':True,'homeFavoriteTip':True,
            'showClubFavorite':False,'forceGPS':False,'listMode':'default','showMonthlySales':False,
            'recentPurchase':True,'phonePrefix':'中国大陆','phoneRequired':True},
    'share':{'friendTitle':'','friendCover':'','momentTitle':'','momentCover':'','groupShare':False},
    'pay':{'matrix':{'signup':{'balance':False,'wx':True},'mall':{'balance':True,'wx':True},
                     'topup':{'balance':False,'wx':True},'stored':{'balance':False,'wx':False}},
           'mixedPay':True,'customWays':[],'wxMchId':''}}

@app.get('/api/club/{club_id}/miniprogram/settings')
def club_mp_settings_get(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        r=row(c.execute('SELECT settings_json FROM club_mp_settings WHERE club_id=?',(club_id,)))
    try: saved=json.loads(r['settings_json']) if r and isinstance(r['settings_json'],str) else {}
    except Exception: saved={}
    if not isinstance(saved,dict): saved={}
    # 深合并默认值：新增的设置项对存量俱乐部自动可见，不用补数据
    out={}
    for grp,defaults in _MP_SETTINGS_DEFAULTS.items():
        src=saved.get(grp) if isinstance(saved.get(grp),dict) else {}
        out[grp]={**defaults,**src}
    return {'settings':out,'updatedAt':(r or {}).get('updated_at') if r else ''}

@app.put('/api/club/{club_id}/miniprogram/settings')
def club_mp_settings_put(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    body=payload.get('settings') if isinstance(payload.get('settings'),dict) else payload
    with conn() as c:
        r=row(c.execute('SELECT settings_json FROM club_mp_settings WHERE club_id=?',(club_id,)))
        try: saved=json.loads(r['settings_json']) if r and isinstance(r['settings_json'],str) else {}
        except Exception: saved={}
        if not isinstance(saved,dict): saved={}
        for grp,defaults in _MP_SETTINGS_DEFAULTS.items():
            if grp not in body: continue
            incoming=body.get(grp)
            if not isinstance(incoming,dict): raise HTTPException(400,f'{grp} 组设置必须是对象')
            merged={**defaults,**saved.get(grp,{})} if isinstance(saved.get(grp),dict) else dict(defaults)
            for k,v in incoming.items():
                if k not in defaults: continue   # 白名单外键直接丢弃，不报错
                merged[k]=v
            saved[grp]=merged
        blob=json.dumps(saved,ensure_ascii=False)
        if len(blob)>64*1024: raise HTTPException(400,'设置内容过大')
        c.execute('''INSERT INTO club_mp_settings(club_id,settings_json,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)
                     ON CONFLICT(club_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=CURRENT_TIMESTAMP''',
                  (club_id,blob))
        out,_=None,None
        cur=row(c.execute('SELECT settings_json,updated_at FROM club_mp_settings WHERE club_id=?',(club_id,)))
    try: saved=json.loads(cur['settings_json'])
    except Exception: saved={}
    result={}
    for grp,defaults in _MP_SETTINGS_DEFAULTS.items():
        src=saved.get(grp) if isinstance(saved.get(grp),dict) else {}
        result[grp]={**defaults,**src}
    return {'ok':True,'settings':result}

# ── 小程序管理助手：角色管理 ─────────────────────────────────────────────────
@app.get('/api/club/{club_id}/mp/roles')
def club_mp_roles_get(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        _mp_seed_roles(c,club_id)
        data=rows(c.execute('SELECT * FROM club_mp_roles WHERE club_id=? ORDER BY system DESC,id',(club_id,)))
    return {'roles':[_mp_role_public(r) for r in data],'perms':MP_PERMS}

@app.post('/api/club/{club_id}/mp/roles')
def club_mp_roles_add(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    name=str(payload.get('name') or '').strip()
    if not name: raise HTTPException(400,'角色名称必填')
    if len(name)>20: raise HTTPException(400,'角色名称过长（上限 20 字）')
    perms=[p for p in (payload.get('perms') or []) if p in MP_PERMS]
    remark=str(payload.get('remark') or '').strip()[:100]
    with conn() as c:
        cur=c.execute('INSERT INTO club_mp_roles(club_id,name,perms_json,remark) VALUES(?,?,?,?)',
                      (club_id,name,json.dumps(perms,ensure_ascii=False),remark))
        rid=int(cur.lastrowid)
        r=row(c.execute('SELECT * FROM club_mp_roles WHERE id=?',(rid,)))
    return {'ok':True,'role':_mp_role_public(r)}

@app.patch('/api/club/{club_id}/mp/roles/{role_id}')
def club_mp_roles_update(club_id:int,role_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        r=row(c.execute('SELECT * FROM club_mp_roles WHERE id=? AND club_id=?',(role_id,club_id)))
        if not r: raise HTTPException(404,'角色不存在')
        name=str(payload.get('name') or '').strip() if 'name' in payload else r['name']
        if not name: raise HTTPException(400,'角色名称必填')
        if len(name)>20: raise HTTPException(400,'角色名称过长（上限 20 字）')
        perms=None
        if 'perms' in payload:
            perms=[p for p in (payload.get('perms') or []) if p in MP_PERMS]
            # 兜底：系统「管理员」角色不允许被摘成空权限（保底小程序管理权）
            if r['system'] and r['name']=='管理员' and not perms: perms=list(MP_PERMS.keys())
        remark=str(payload.get('remark') if 'remark' in payload else (r['remark'] or '')).strip()[:100]
        c.execute('UPDATE club_mp_roles SET name=?,perms_json=?,remark=? WHERE id=?',
                  (name,json.dumps(perms if perms is not None else json.loads(r['perms_json'] or '[]'),ensure_ascii=False),remark,role_id))
        r2=row(c.execute('SELECT * FROM club_mp_roles WHERE id=?',(role_id,)))
    return {'ok':True,'role':_mp_role_public(r2)}

@app.delete('/api/club/{club_id}/mp/roles/{role_id}')
def club_mp_roles_delete(club_id:int,role_id:int):
    club_or_404(club_id)
    with conn() as c:
        r=row(c.execute('SELECT * FROM club_mp_roles WHERE id=? AND club_id=?',(role_id,club_id)))
        if not r: raise HTTPException(404,'角色不存在')
        if r['system']: raise HTTPException(400,'系统预置角色不能删除')
        used=c.execute('SELECT COUNT(*) FROM club_mp_staff WHERE club_id=? AND role_id=?',(club_id,role_id)).fetchone()[0]
        if used: raise HTTPException(400,f'仍有 {used} 名员工使用该角色，请先调整他们的角色')
        c.execute('DELETE FROM club_mp_roles WHERE id=?',(role_id,))
    return {'ok':True}

# ── 小程序管理助手：员工管理 ─────────────────────────────────────────────────
@app.get('/api/club/{club_id}/mp/staff')
def club_mp_staff_get(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        _mp_seed_roles(c,club_id)
        data=rows(c.execute('''SELECT s.*,r.name role_name FROM club_mp_staff s
                               LEFT JOIN club_mp_roles r ON r.id=s.role_id
                               WHERE s.club_id=? ORDER BY s.id''',(club_id,)))
        roles=rows(c.execute('SELECT * FROM club_mp_roles WHERE club_id=? ORDER BY system DESC,id',(club_id,)))
    return {'staff':[{'id':int(x['id']),'name':x['name'],'wxid':x['wxid'] or '','phone':x['phone'] or '',
                      'avatarUrl':x['avatar_url'] or '','roleId':x['role_id'],'roleName':x['role_name'] or '',
                      'status':x['status'],'boundAt':x['bound_at']} for x in data],
            'roles':[_mp_role_public(r) for r in roles],'perms':MP_PERMS}

@app.post('/api/club/{club_id}/mp/staff')
def club_mp_staff_add(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    name=str(payload.get('name') or '').strip()
    if not name: raise HTTPException(400,'员工姓名必填')
    if len(name)>20: raise HTTPException(400,'员工姓名过长（上限 20 字）')
    phone=str(payload.get('phone') or '').strip()[:20]
    if phone and not re.match(r'^1[3-9]\d{9}$',phone): raise HTTPException(400,'手机号格式不正确')
    wxid=str(payload.get('wxid') or '').strip()[:64]
    role_id=payload.get('roleId')
    with conn() as c:
        if role_id is not None:
            if not c.execute('SELECT 1 FROM club_mp_roles WHERE id=? AND club_id=?',(int(role_id),club_id)).fetchone():
                raise HTTPException(400,'角色不存在')
        cur=c.execute('INSERT INTO club_mp_staff(club_id,name,wxid,phone,role_id) VALUES(?,?,?,?,?)',
                      (club_id,name,wxid or None,phone or None,int(role_id) if role_id is not None else None))
        sid=int(cur.lastrowid)
        r=row(c.execute('''SELECT s.*,r.name role_name FROM club_mp_staff s
                           LEFT JOIN club_mp_roles r ON r.id=s.role_id WHERE s.id=?''',(sid,)))
    return {'ok':True,'staff':{'id':sid,'name':name,'wxid':wxid,'phone':phone,
            'roleId':r['role_id'],'roleName':r['role_name'] or '','status':r['status'],'boundAt':r['bound_at']}}

@app.patch('/api/club/{club_id}/mp/staff/{staff_id}')
def club_mp_staff_update(club_id:int,staff_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        r=row(c.execute('SELECT * FROM club_mp_staff WHERE id=? AND club_id=?',(staff_id,club_id)))
        if not r: raise HTTPException(404,'员工不存在')
        name=r['name'];phone=r['phone'];wxid=r['wxid'];role_id=r['role_id'];status=r['status']
        if 'name' in payload:
            name=str(payload.get('name') or '').strip()
            if not name: raise HTTPException(400,'员工姓名必填')
            if len(name)>20: raise HTTPException(400,'员工姓名过长（上限 20 字）')
        if 'phone' in payload:
            phone=str(payload.get('phone') or '').strip()[:20] or None
            if phone and not re.match(r'^1[3-9]\d{9}$',phone): raise HTTPException(400,'手机号格式不正确')
        if 'wxid' in payload: wxid=str(payload.get('wxid') or '').strip()[:64] or None
        if 'roleId' in payload:
            role_id=payload.get('roleId')
            if role_id is not None:
                if not c.execute('SELECT 1 FROM club_mp_roles WHERE id=? AND club_id=?',(int(role_id),club_id)).fetchone():
                    raise HTTPException(400,'角色不存在')
                role_id=int(role_id)
        if 'status' in payload:
            status=str(payload.get('status') or '')
            if status not in ('active','disabled'): raise HTTPException(400,'status 不合法')
        c.execute('UPDATE club_mp_staff SET name=?,phone=?,wxid=?,role_id=?,status=? WHERE id=?',
                  (name,phone,wxid,role_id,status,staff_id))
        r2=row(c.execute('''SELECT s.*,r.name role_name FROM club_mp_staff s
                            LEFT JOIN club_mp_roles r ON r.id=s.role_id WHERE s.id=?''',(staff_id,)))
    return {'ok':True,'staff':{'id':staff_id,'name':name,'phone':phone or '','wxid':wxid or '',
            'roleId':role_id,'roleName':r2['role_name'] or '','status':status,'boundAt':r2['bound_at']}}

@app.delete('/api/club/{club_id}/mp/staff/{staff_id}')
def club_mp_staff_delete(club_id:int,staff_id:int):
    club_or_404(club_id)
    with conn() as c:
        if not c.execute('SELECT 1 FROM club_mp_staff WHERE id=? AND club_id=?',(staff_id,club_id)).fetchone():
            raise HTTPException(404,'员工不存在')
        c.execute('DELETE FROM club_mp_staff WHERE id=?',(staff_id,))
    return {'ok':True}

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
        lid=c.execute('''INSERT INTO club_leaders(club_id,name,phone,role,specialties,base_city,avatar_url,status,note,
                                                  bio,years_exp,cert_verified)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(
            club_id,name,str(payload.get('phone') or '').strip() or None,
            str(payload.get('role') or '').strip() or '领队',
            jdump(_leader_specs(payload.get('specialties'))),
            str(payload.get('baseCity') or '').strip() or None,
            _normalize_avatar_url(payload.get('avatarUrl')),
            str(payload.get('status') or 'active'),str(payload.get('note') or '').strip() or None,
            str(payload.get('bio') or '').strip() or None,_leader_years(payload),
            1 if payload.get('certVerified') else 0)).lastrowid
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
        if 'bio' in payload: fields['bio']=str(payload.get('bio') or '').strip() or None
        if 'yearsExp' in payload: fields['years_exp']=_leader_years(payload)
        if 'certVerified' in payload: fields['cert_verified']=1 if payload.get('certVerified') else 0
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


@app.get('/api/club/{club_id}/leader-inbox')
def club_leader_inbox(club_id:int,limit:int=60):
    """俱乐部端「领队咨询箱 + 领队评价」。

    C 端点「咨询领队」留的问题落在这里（带手机号，俱乐部要能回电）；评价是顾客打的分。
    咨询默认只看未处理，避免几十条历史把待办冲下去 —— 传 status='all' 才看全部。
    """
    club_or_404(club_id)
    lim=max(1,min(200,int(limit or 60)))
    with conn() as c:
        inquiries=[{'id':int(x['id']),'leaderId':int(x['leader_id'] or 0),
                    'leaderName':str(x.get('leader_name') or ''),
                    'activityId':int(x['activity_id'] or 0) if x.get('activity_id') else 0,
                    'activityTitle':str(x.get('activity_title') or ''),
                    'name':str(x.get('name') or ''),'phone':str(x.get('phone') or ''),
                    'message':str(x.get('message') or ''),'status':str(x.get('status') or 'new'),
                    'createdAt':x.get('created_at'),'handledAt':x.get('handled_at')}
                   for x in rows(c.execute('''SELECT iq.*,l.name AS leader_name,a.title AS activity_title
                                              FROM leader_inquiries iq
                                              LEFT JOIN club_leaders l ON l.id=iq.leader_id
                                              LEFT JOIN activities a ON a.id=iq.activity_id
                                              WHERE iq.club_id=? ORDER BY (iq.status!='new'),iq.id DESC LIMIT ?''',(club_id,lim)))]
        reviews=[{'id':int(x['id']),'leaderId':int(x['leader_id']),'leaderName':str(x.get('leader_name') or ''),
                  'author':str(x.get('author') or '匿名山友'),'rating':int(x['rating'] or 0),
                  'comment':str(x.get('comment') or ''),'createdAt':x.get('created_at')}
                 for x in rows(c.execute('''SELECT rv.*,l.name AS leader_name FROM leader_reviews rv
                                            LEFT JOIN club_leaders l ON l.id=rv.leader_id
                                            WHERE rv.club_id=? AND rv.status='visible'
                                            ORDER BY rv.id DESC LIMIT ?''',(club_id,lim)))]
    return {'inquiries':inquiries,'reviews':reviews,
            'newCount':sum(1 for x in inquiries if x['status']=='new')}


@app.post('/api/club/{club_id}/leader-inquiries/{inquiry_id}/handled')
def club_leader_inquiry_handled(club_id:int,inquiry_id:int,payload:dict=Body(default={})):
    """标记咨询已回复 / 改回未处理。只做状态留痕，不发短信 —— 回复靠俱乐部真人打电话。"""
    club_or_404(club_id)
    handled=payload.get('handled')
    handled=True if handled is None else bool(handled)
    with conn() as c:
        r=row(c.execute('SELECT id FROM leader_inquiries WHERE id=? AND club_id=?',(inquiry_id,club_id)))
        if not r: raise HTTPException(404,'咨询不存在')
        # handled_at 用 datetime('now') 现算，而不是把 'CURRENT_TIMESTAMP' 当字符串塞进去
        # （后者会存成一段字面文本，页面上一看就是脏数据）。
        c.execute("UPDATE leader_inquiries SET status=?,handled_at=? WHERE id=? AND club_id=?",
                  ('handled' if handled else 'new',
                   datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') if handled else None,
                   inquiry_id,club_id))
    return {'ok':True,'status':'handled' if handled else 'new'}


@app.get('/api/club/{club_id}/notice-summary')
def club_notice_summary(club_id:int):
    """俱乐部端「有新东西没人看」的轻量计数（只 COUNT，不拉列表）。

    存在的理由：领队咨询落在「俱乐部设置 → 领队资源库」的折叠块里，俱乐部不进那一页就
    等于没人看见 —— C 端顾客留了问题却永远等不到电话。侧边栏角标 / 标签页标题要每
    60 秒刷一次，所以这里只做两次 COUNT：每次拉 60 条咨询 + 评价列表太重。
    """
    club_or_404(club_id)
    with conn() as c:
        iq=row(c.execute('SELECT COUNT(*) AS n, COALESCE(MAX(id),0) AS latest FROM leader_inquiries '
                         'WHERE club_id=? AND status="new"',(club_id,))) or {}
        rv=row(c.execute('SELECT COUNT(*) AS n FROM leader_reviews WHERE club_id=? AND status="visible"',(club_id,))) or {}
    return {'leaderInquiries':int(iq.get('n') or 0),
            'latestInquiryId':int(iq.get('latest') or 0),
            'leaderReviews':int(rv.get('n') or 0)}


# ─────────────────────────────────────────────────────────────────────────────
# 2026-10-29 统一收件箱（俱乐部端「信息」栏目）：合并线路咨询 + 领队咨询 + 企微轻量通知
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 2026-11-05 站内咨询对话：从「只留痕 + 线下电话」升级为可往来沟通
#   · 顾客提交咨询时拿一个随机 access_token，凭它认领自己的会话（不依赖完整登录改造）；
#   · 首条消息就是原始留言，之后顾客可追问、领队/后台可回复，四端读同一份时间线；
#   · 顾客追问 → status 回 new（红点重现）；领队/后台回复 → status=handled + 顾客未读+1。
# ─────────────────────────────────────────────────────────────────────────────
_INQUIRY_TABLES = {'route': 'route_inquiries', 'leader': 'leader_inquiries'}

def _inquiry_table(kind: str):
    t = _INQUIRY_TABLES.get(kind)
    if not t:
        raise HTTPException(400, '未知咨询类型')
    return t

def _inquiry_token_check(c, kind: str, inquiry_id: int, token: str, club_id: int | None = None):
    # C 端公开读写的唯一归属凭证。老数据没有 token → 一律 404，防猜 id 看别人手机号。
    table = _inquiry_table(kind)
    t = str(token or '').strip()
    if not t:
        raise HTTPException(403, '缺少会话凭证')
    if club_id:
        r = row(c.execute(f'SELECT * FROM {table} WHERE id=? AND club_id=? AND access_token=?',
                          (inquiry_id, club_id, t)))
    else:
        r = row(c.execute(f'SELECT * FROM {table} WHERE id=? AND access_token=?',
                          (inquiry_id, t)))
    if not r:
        raise HTTPException(404, '咨询不存在或凭证不匹配')
    return r

def _inquiry_messages(c, club_id: int, kind: str, inquiry_id: int, limit: int = 100):
    rs = rows(c.execute('SELECT id,sender,body,created_at FROM inquiry_messages '
                        'WHERE club_id=? AND kind=? AND inquiry_id=? ORDER BY id LIMIT ?',
                        (club_id, kind, inquiry_id, max(1, min(300, int(limit or 100))))))
    return [{'id': int(x['id']), 'sender': str(x['sender']), 'body': str(x['body'] or ''),
             'createdAt': x.get('created_at')} for x in rs]

def _append_inquiry_message(c, club_id: int, kind: str, inquiry_id: int,
                            sender: str, body: str):
    # 写一条消息并同步咨询状态。sender: customer | leader | club
    table = _inquiry_table(kind)
    r = row(c.execute(f'SELECT id FROM {table} WHERE id=? AND club_id=?', (inquiry_id, club_id)))
    if not r:
        raise HTTPException(404, '咨询不存在')
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    mid = c.execute('INSERT INTO inquiry_messages(club_id,kind,inquiry_id,sender,body,created_at) '
                    'VALUES(?,?,?,?,?,?)', (club_id, kind, inquiry_id, sender, body, now)).lastrowid
    if sender == 'customer':
        c.execute(f'UPDATE {table} SET status=?, last_message_at=?, unread_customer=0 '
                  f'WHERE id=? AND club_id=?', ('new', now, inquiry_id, club_id))
    else:
        c.execute(f'UPDATE {table} SET status=?, handled_at=?, last_message_at=?, '
                  f'unread_customer=1 WHERE id=? AND club_id=?',
                  ('handled', now, now, inquiry_id, club_id))
    return int(mid), now

def _inquiry_thread_counts(c, club_id: int, kind: str, ids: list):
    # 给列表批量算回复数 / 最后消息时间，避免逐条 N+1
    if not ids:
        return {}
    qs = ','.join('?' for _ in ids)
    rs = rows(c.execute(f'SELECT inquiry_id,COUNT(*) AS n,MAX(created_at) AS last '
                        f'FROM inquiry_messages WHERE club_id=? AND kind=? '
                        f'AND inquiry_id IN ({qs}) AND sender!=? GROUP BY inquiry_id',
                        (club_id, kind, *ids, 'customer')))
    return {int(x['inquiry_id']): {'replyCount': int(x['n'] or 0),
                                   'lastMessageAt': x.get('last')} for x in rs}


def _club_wecom_webhook(c, club_id:int):
    """该俱乐部是否配置了可用的企微群机器人 Webhook。没有就返回 None（不推送）。"""
    r=row(c.execute('SELECT wecom_webhook,wecom_enabled FROM club_integrations WHERE club_id=?',(club_id,)))
    if r and r.get('wecom_enabled') and r.get('wecom_webhook'):
        return str(r['wecom_webhook']).strip()
    return None

def _push_inquiry_to_wecom(webhook:str, kind_label:str, data:dict):
    """企微群机器人 Webhook 推送（urllib 实现，静默失败 —— 通知是锦上添花，绝不阻断业务）。"""
    try:
        lines=[f"**新的{kind_label}咨询**"]
        if data.get('name'): lines.append(f"顾客：{data.get('name')}")
        lines.append(f"电话：{data.get('phone','')}")
        if data.get('activityTitle'): lines.append(f"线路：{data.get('activityTitle')}")
        if data.get('leaderName'): lines.append(f"领队：{data.get('leaderName')}")
        lines.append(f"问题：{data.get('message','')}")
        if data.get('backendLink'): lines.append(f"[在后台「信息」查看]({data.get('backendLink')})")
        body=json.dumps({'msgtype':'markdown','markdown':{'content':'\n'.join(lines)}},
                        ensure_ascii=False).encode('utf-8')
        req=urllib.request.Request(webhook, data=body, headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
    except Exception:
        # 任何网络/格式失败都静默吞掉，不影响主流程
        pass


@app.get('/api/club/{club_id}/inquiries')
def club_inquiries(club_id:int, kind:str='all', status:str='all', q:str='', limit:int=200):
    """俱乐部端「信息」统一收件箱：合并线路咨询 + 领队咨询。

    kind: all|route|leader；status: all|new|handled；q: 模糊搜姓名/电话/内容。
    排序：未处理置顶，再按 id 倒序（最新的在最上）。newCount 给侧栏红点用。
    """
    club_or_404(club_id)
    lim=max(1,min(500,int(limit or 200)))
    qk=(q or '').strip().lower()
    with conn() as c:
        routes=[dict(x) for x in rows(c.execute('''SELECT iq.*, a.title AS activity_title, NULL AS leader_name
                                                   FROM route_inquiries iq
                                                   LEFT JOIN activities a ON a.id=iq.activity_id
                                                   WHERE iq.club_id=?''',(club_id,)))]
        leaders=[dict(x) for x in rows(c.execute('''SELECT iq.*, l.name AS leader_name, a.title AS activity_title
                                                    FROM leader_inquiries iq
                                                    LEFT JOIN club_leaders l ON l.id=iq.leader_id
                                                    LEFT JOIN activities a ON a.id=iq.activity_id
                                                    WHERE iq.club_id=?''',(club_id,)))]
    items=[]
    for x in routes:
        items.append({'id':int(x['id']),'kind':'route','leaderId':0,'leaderName':'',
                      'activityId':int(x['activity_id'] or 0) if x.get('activity_id') else 0,
                      'activityTitle':str(x.get('activity_title') or ''),
                      'name':str(x.get('name') or ''),'phone':str(x.get('phone') or ''),
                      'message':str(x.get('message') or ''),'status':str(x.get('status') or 'new'),
                      'createdAt':x.get('created_at'),'handledAt':x.get('handled_at')})
    for x in leaders:
        items.append({'id':int(x['id']),'kind':'leader',
                      'leaderId':int(x['leader_id'] or 0),'leaderName':str(x.get('leader_name') or ''),
                      'activityId':int(x['activity_id'] or 0) if x.get('activity_id') else 0,
                      'activityTitle':str(x.get('activity_title') or ''),
                      'name':str(x.get('name') or ''),'phone':str(x.get('phone') or ''),
                      'message':str(x.get('message') or ''),'status':str(x.get('status') or 'new'),
                      'createdAt':x.get('created_at'),'handledAt':x.get('handled_at')})
    # 对话计数：给每条咨询挂回复数与最后消息时间（一次 GROUP BY，不是逐条查）
    with conn() as c:
        for k in ('route','leader'):
            ids=[i['id'] for i in items if i['kind']==k]
            cnt=_inquiry_thread_counts(c,club_id,k,ids)
            for i in items:
                if i['kind']==k:
                    i.update(cnt.get(i['id']) or {'replyCount':0,'lastMessageAt':None})
    if kind and kind!='all':
        items=[i for i in items if i['kind']==kind]
    if status and status!='all':
        items=[i for i in items if i['status']==status]
    if qk:
        items=[i for i in items if qk in (i['name']+i['phone']+i['message']).lower()]
    new_count=sum(1 for i in items if i['status']=='new')
    total=len(items)
    items.sort(key=lambda i:(i['status']!='new', -i['id']))
    items=items[:lim]
    return {'items':items,'newCount':new_count,'total':total}


@app.post('/api/club/{club_id}/inquiries/{kind}/{inquiry_id}/handled')
def club_inquiry_handled(club_id:int, kind:str, inquiry_id:int, payload:dict=Body(default={})):
    """统一标记咨询已回复 / 改回未处理（route / leader 通用）。只做状态留痕，回复靠真人打电话。"""
    club_or_404(club_id)
    if kind not in ('route','leader'): raise HTTPException(400,'未知咨询类型')
    table='route_inquiries' if kind=='route' else 'leader_inquiries'
    handled=payload.get('handled')
    handled=True if handled is None else bool(handled)
    with conn() as c:
        r=row(c.execute(f'SELECT id FROM {table} WHERE id=? AND club_id=?',(inquiry_id,club_id)))
        if not r: raise HTTPException(404,'咨询不存在')
        c.execute(f"UPDATE {table} SET status=?, handled_at=? WHERE id=? AND club_id=?",
                  ('handled' if handled else 'new',
                   datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') if handled else None,
                   inquiry_id,club_id))
    return {'ok':True,'status':'handled' if handled else 'new'}


@app.get('/api/club/{club_id}/inquiries/{kind}/{inquiry_id}/thread')
def club_inquiry_thread(club_id:int, kind:str, inquiry_id:int):
    # 后台「信息」弹窗拉一条咨询的完整往来时间线（含顾客追问与历次回复）
    club_or_404(club_id)
    table=_inquiry_table(kind)
    with conn() as c:
        r=row(c.execute(f'SELECT id FROM {table} WHERE id=? AND club_id=?',(inquiry_id,club_id)))
        if not r: raise HTTPException(404,'咨询不存在')
        msgs=_inquiry_messages(c,club_id,kind,inquiry_id)
    return {'ok':True,'messages':msgs,'replyCount':sum(1 for m in msgs if m['sender']!='customer')}

@app.post('/api/club/{club_id}/inquiries/{kind}/{inquiry_id}/messages')
def club_inquiry_reply(club_id:int, kind:str, inquiry_id:int, payload:dict=Body(...)):
    # 俱乐部后台在「信息」里直接回复顾客（代领队回复也走这里）
    club_or_404(club_id)
    body=str(payload.get('message') or '').strip()
    if len(body)<2: raise HTTPException(400,'回复内容太短')
    if len(body)>800: raise HTTPException(400,'回复最多 800 字')
    with conn() as c:
        mid,now=_append_inquiry_message(c,club_id,kind,inquiry_id,'club',body)
        msgs=_inquiry_messages(c,club_id,kind,inquiry_id)
    return {'ok':True,'messageId':mid,'createdAt':now,'messages':msgs}

@app.get('/api/club/{club_id}/integrations')
def club_integrations_get(club_id:int):
    """读取俱乐部企微集成配置（群机器人 Webhook）。"""
    club_or_404(club_id)
    with conn() as c:
        r=row(c.execute('SELECT wecom_webhook,wecom_enabled FROM club_integrations WHERE club_id=?',(club_id,)))
    return {'wecomWebhook':str(r['wecom_webhook'] or '') if r else '',
            'wecomEnabled':bool(r['wecom_enabled']) if r else False}

@app.post('/api/club/{club_id}/integrations')
def club_integrations_save(club_id:int, payload:dict=Body(default={})):
    """保存俱乐部企微集成配置。webhook 必须 http(s)；enabled 开关。"""
    club_or_404(club_id)
    webhook=str(payload.get('wecomWebhook') or '').strip()
    enabled=bool(payload.get('wecomEnabled'))
    if webhook and not re.match(r'^https?://', webhook):
        raise HTTPException(400,'企微 Webhook 必须是 http(s) 链接')
    now=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    with conn() as c:
        r=row(c.execute('SELECT id FROM club_integrations WHERE club_id=?',(club_id,)))
        if r:
            c.execute('UPDATE club_integrations SET wecom_webhook=?,wecom_enabled=?,updated_at=? WHERE club_id=?',
                      (webhook or None, 1 if enabled else 0, now, club_id))
        else:
            c.execute('INSERT INTO club_integrations(club_id,wecom_webhook,wecom_enabled,updated_at) VALUES(?,?,?,?)',
                      (club_id, webhook or None, 1 if enabled else 0, now))
    return {'ok':True}


@app.post('/api/club/{club_id}/integrations/test')
def club_integrations_test(club_id:int):
    """向已配置的企微群机器人发一条测试消息，验证 Webhook 连通。"""
    club_or_404(club_id)
    with conn() as c:
        webhook=_club_wecom_webhook(c,club_id)
    if not webhook: raise HTTPException(400,'请先填写并保存可用的企微 Webhook')
    _push_inquiry_to_wecom(webhook, '【测试】',
        {'name':'测试', 'phone':'—',
         'message':'企业微信通知已连通 ✅（这是一条来自 ClubOS 的测试消息）',
         'backendLink':'/?view=info'})
    return {'ok':True}


def _leader_self(c,club_id:int,request:Request,leader_id:int=0):
    """把当前登录身份解析成 club_leaders 里的一条领队记录。

    口径与 /api/leader/occurrences 一致：账号绑定的 users.phone ↔ 领队名册的 phone
    （同一份域数据，不再造第二套绑定关系）。内测（TEST_OPEN）允许用 leader_id 指定，
    因为演示库里通常没有真正的领队账号；生产环境一律只认身份，不接受 URL 传 id。
    """
    from security_v025 import TEST_OPEN
    identity=getattr(request.state,'identity',None) or None
    if identity and identity.get('role')=='leader':
        uid=identity.get('userId')
        ph=row(c.execute('SELECT phone FROM users WHERE id=?',(uid,))) if uid else None
        phone=str((ph or {}).get('phone') or '').strip()
        if not phone: return None
        return row(c.execute('SELECT * FROM club_leaders WHERE club_id=? AND REPLACE(COALESCE(phone,"")," ","")=? '
                             'ORDER BY (status!="active"),id LIMIT 1',(club_id,phone.replace(' ',''))))
    if TEST_OPEN and leader_id:
        return row(c.execute('SELECT * FROM club_leaders WHERE id=? AND club_id=?',(int(leader_id),club_id)))
    return None


@app.get('/api/leader/inquiries')
def leader_my_inquiries(request:Request,club_id:int=1,leader_id:int=0,limit:int=30):
    """领队本人看到的顾客咨询（C 端点「咨询领队」留的问题）。

    只返回「问我的」那几条，且带顾客留下的称呼与手机号 —— 领队要回电，没有号码等于没接到。
    这里不做已读/未读两套状态：领队和俱乐部共用 status=new/handled，谁先处理都不会重复提醒。
    """
    lim=max(1,min(100,int(limit or 30)))
    with conn() as c:
        me=_leader_self(c,club_id,request,leader_id)
        if not me: raise HTTPException(403,'请用领队账号登录后查看自己的咨询')
        lid=int(me['id'])
        rs=rows(c.execute('''SELECT iq.*,a.title AS activity_title
                             FROM leader_inquiries iq
                             LEFT JOIN activities a ON a.id=iq.activity_id
                             WHERE iq.club_id=? AND iq.leader_id=?
                             ORDER BY (iq.status!='new'),iq.id DESC LIMIT ?''',(club_id,lid,lim)))
        cnt=_inquiry_thread_counts(c,club_id,'leader',[int(x['id']) for x in rs])
        out=[]
        for x in rs:
            iid=int(x['id']); meta=cnt.get(iid) or {'replyCount':0,'lastMessageAt':None}
            out.append({'id':iid,
              'name':str(x.get('name') or '山友'),
              'phone':str(x.get('phone') or ''),
              'message':str(x.get('message') or ''),
              'status':str(x.get('status') or 'new'),
              'activityTitle':str(x.get('activity_title') or ''),
              'createdAt':x.get('created_at'),'handledAt':x.get('handled_at'),
              'replyCount':meta['replyCount'],'lastMessageAt':meta['lastMessageAt']})
    return {'leader':{'leaderId':lid,'name':str(me.get('name') or '领队'),'role':str(me.get('role') or '领队')},
            'inquiries':out,'newCount':sum(1 for x in out if x['status']=='new')}


@app.post('/api/leader/inquiries/{inquiry_id}/handled')
def leader_inquiry_handled(request:Request,inquiry_id:int,payload:dict=Body(default={}),club_id:int=1,leader_id:int=0):
    """领队自己把咨询标成已回复 —— 领队回完电话就地收口，不用等俱乐部管理员再点一次。"""
    handled=payload.get('handled')
    handled=True if handled is None else bool(handled)
    with conn() as c:
        me=_leader_self(c,club_id,request,leader_id)
        if not me: raise HTTPException(403,'请用领队账号登录')
        r=row(c.execute('SELECT id FROM leader_inquiries WHERE id=? AND club_id=? AND leader_id=?',
                        (inquiry_id,club_id,int(me['id']))))
        if not r: raise HTTPException(404,'这条咨询不属于你')
        c.execute('UPDATE leader_inquiries SET status=?,handled_at=? WHERE id=? AND club_id=? AND leader_id=?',
                  ('handled' if handled else 'new',
                   datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') if handled else None,
                   inquiry_id,club_id,int(me['id'])))
    return {'ok':True,'status':'handled' if handled else 'new'}


@app.get('/api/leader/inquiries/{inquiry_id}/thread')
def leader_inquiry_thread(request:Request,inquiry_id:int,club_id:int=1,leader_id:int=0):
    # 领队执行端拉自己这条咨询的完整往来时间线
    with conn() as c:
        me=_leader_self(c,club_id,request,leader_id)
        if not me: raise HTTPException(403,'请用领队账号登录后查看自己的咨询')
        r=row(c.execute('SELECT id FROM leader_inquiries WHERE id=? AND club_id=? AND leader_id=?',
                        (inquiry_id,club_id,int(me['id']))))
        if not r: raise HTTPException(404,'这条咨询不属于你')
        msgs=_inquiry_messages(c,club_id,'leader',inquiry_id)
    return {'ok':True,'messages':msgs,'replyCount':sum(1 for m in msgs if m['sender']!='customer')}

@app.post('/api/leader/inquiries/{inquiry_id}/messages')
def leader_inquiry_reply(request:Request,inquiry_id:int,payload:dict=Body(...),
                         club_id:int=1,leader_id:int=0):
    # 领队在 /leader 直接回复顾客 —— 同步到 C 端「我的咨询」与后台「信息」
    body=str(payload.get('message') or '').strip()
    if len(body)<2: raise HTTPException(400,'回复内容太短')
    if len(body)>800: raise HTTPException(400,'回复最多 800 字')
    with conn() as c:
        me=_leader_self(c,club_id,request,leader_id)
        if not me: raise HTTPException(403,'请用领队账号登录')
        r=row(c.execute('SELECT id FROM leader_inquiries WHERE id=? AND club_id=? AND leader_id=?',
                        (inquiry_id,club_id,int(me['id']))))
        if not r: raise HTTPException(404,'这条咨询不属于你')
        mid,now=_append_inquiry_message(c,club_id,'leader',inquiry_id,'leader',body)
        msgs=_inquiry_messages(c,club_id,'leader',inquiry_id)
    return {'ok':True,'messageId':mid,'createdAt':now,'messages':msgs}

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


# ═══ 活动分类（2026-11-06）：后台手动自定义类别 + 活动↔类别多对多 ═══════════
# 替代 C 端按标题正则的自动分类（ACT_THEMES）：类别由俱乐部在后台维护
# （增删 / 改名 / 换图 / 排序 / 停用），活动在详情页勾选归属（多选）。
# 图片走与领队头像同一条纪律：库里存**俱乐部域代理地址**（生产 /static/uploads 被封死），
# C 端经公开路由改写后访问；俱乐部端直接用俱乐部域地址（生产 public/* 只放行 member）。
_CATIMG_URL_RE=re.compile(r'^/api/club/(\d+)/activity-categories/(\d+)/image(\.\w+)$')

def _activity_categories_payload(c,club_id:int,include_inactive:bool=False,pub:bool=False):
    where='' if include_inactive else ' AND active=1'
    cats=rows(c.execute(f'SELECT id,name,image_url,sort_order,active FROM activity_categories WHERE club_id=?{where} ORDER BY sort_order,id',(club_id,)))
    counts={r['category_id']:r['n'] for r in rows(c.execute(
        '''SELECT l.category_id AS category_id,COUNT(*) AS n FROM activity_category_links l
           JOIN activities a ON a.id=l.activity_id AND a.club_id=? AND a.status='published'
           GROUP BY l.category_id''',(club_id,)))}
    out=[]
    for r in cats:
        cid=int(r['id'])
        m=_CATIMG_URL_RE.match(str(r.get('image_url') or ''))
        img=None
        if m and int(m.group(1))==club_id and int(m.group(2))==cid:
            img=(f'/api/public/activity-categories/{cid}/image{m.group(3)}' if pub else m.group(0))
        out.append({'id':cid,'name':str(r.get('name') or ''),'imageUrl':img,
                    'sortOrder':int(r.get('sort_order') or 0),'active':bool(r.get('active')),
                    'count':int(counts.get(cid,0))})
    return out

def _club_category_or_404(c,club_id:int,cat_id:int):
    r=row(c.execute('SELECT id FROM activity_categories WHERE id=? AND club_id=?',(cat_id,club_id)))
    if not r: raise HTTPException(404,'分类不存在')
    return r

@app.get('/api/club/{club_id}/activity-categories')
def club_activity_categories(club_id:int):
    """后台分类管理列表：含停用项与每类已发布活动数（含停用活动的活动不计数）。"""
    club_or_404(club_id)
    with conn() as c:
        return _activity_categories_payload(c,club_id,include_inactive=True,pub=False)

@app.post('/api/club/{club_id}/activity-categories')
def create_activity_category(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    name=str(payload.get('name') or '').strip()
    if not name: raise HTTPException(400,'分类名称不能为空')
    if len(name)>20: raise HTTPException(400,'分类名称最长 20 个字')
    with conn() as c:
        dup=row(c.execute('SELECT id FROM activity_categories WHERE club_id=? AND name=?',(club_id,name)))
        if dup: raise HTTPException(400,'已存在同名分类')
        mx=row(c.execute('SELECT COALESCE(MAX(sort_order),-1) AS m FROM activity_categories WHERE club_id=?',(club_id,)))
        cur=c.execute('INSERT INTO activity_categories(club_id,name,sort_order,active) VALUES(?,?,?,1)',
                      (club_id,name,int(mx['m'])+1))
        cat_id=cur.lastrowid
    return {'id':cat_id,'name':name}

@app.patch('/api/club/{club_id}/activity-categories/{cat_id}')
def update_activity_category(club_id:int,cat_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        _club_category_or_404(c,club_id,cat_id)
        fields={}
        if 'name' in payload:
            name=str(payload.get('name') or '').strip()
            if not name: raise HTTPException(400,'分类名称不能为空')
            if len(name)>20: raise HTTPException(400,'分类名称最长 20 个字')
            dup=row(c.execute('SELECT id FROM activity_categories WHERE club_id=? AND name=? AND id!=?',(club_id,name,cat_id)))
            if dup: raise HTTPException(400,'已存在同名分类')
            fields['name']=name
        if 'sortOrder' in payload:
            try: fields['sort_order']=int(payload.get('sortOrder'))
            except (TypeError,ValueError): raise HTTPException(400,'排序必须是整数')
        if 'active' in payload: fields['active']=1 if payload.get('active') else 0
        if fields:
            sets=','.join(f'{k}=?' for k in fields)
            c.execute(f'UPDATE activity_categories SET {sets} WHERE id=? AND club_id=?',(*fields.values(),cat_id,club_id))
    return {'ok':True}

@app.delete('/api/club/{club_id}/activity-categories/{cat_id}')
def delete_activity_category(club_id:int,cat_id:int):
    club_or_404(club_id)
    with conn() as c:
        _club_category_or_404(c,club_id,cat_id)
        c.execute('DELETE FROM activity_category_links WHERE category_id=?',(cat_id,))
        c.execute('DELETE FROM activity_categories WHERE id=? AND club_id=?',(cat_id,club_id))
    return {'ok':True}

@app.post('/api/club/{club_id}/activity-categories/{cat_id}/image')
@app.post('/api/club/{club_id}/activity-categories/{cat_id}/image.{ext}')
async def upload_activity_category_image(club_id:int,cat_id:int,file:UploadFile=File(...),ext:str=''):
    """上传/替换分类磁贴图。落盘 uploads/{club}/categories/{cat_id}/image{ext}，覆盖写。"""
    club_or_404(club_id)
    real=Path(file.filename or '').suffix.lower()
    if real not in _PUBLIC_IMAGE_EXT: raise HTTPException(400,'仅支持图片文件：png/jpg/jpeg/webp/gif')
    if ext and '.'+str(ext).lower().lstrip('.')!=real:
        raise HTTPException(400,'路径里的扩展名与文件后缀不一致')
    data=await file.read()
    if len(data) > 5*1024*1024: raise HTTPException(413,'图片过大（上限 5MB）')
    with conn() as c:
        _club_category_or_404(c,club_id,cat_id)
    dest=UPLOAD/str(club_id)/'categories'/str(cat_id)
    dest.mkdir(parents=True,exist_ok=True)
    (dest/f'image{real}').write_bytes(data)
    for stale in dest.glob('image.*'):
        if stale.suffix.lower()!=real: stale.unlink(missing_ok=True)
    url=f'/api/club/{club_id}/activity-categories/{cat_id}/image{real}'
    with conn() as c:
        c.execute('UPDATE activity_categories SET image_url=? WHERE id=? AND club_id=?',(url,cat_id,club_id))
    return {'imageUrl':url}

def _serve_category_image(club_id:int,cat_id:int,ext:str):
    with conn() as c:
        r=row(c.execute('SELECT image_url FROM activity_categories WHERE id=? AND club_id=?',(cat_id,club_id)))
    if not r: raise HTTPException(404,'分类不存在')
    m=_CATIMG_URL_RE.match(str(r['image_url'] or ''))
    if not m or int(m.group(1))!=int(club_id) or int(m.group(2))!=int(cat_id):
        raise HTTPException(404,'该分类还没有设置图片')
    ext='.'+str(ext).lower().lstrip('.')
    if ext not in _PUBLIC_IMAGE_EXT: raise HTTPException(404,'不支持的图片格式')
    fp=UPLOAD/str(int(club_id))/'categories'/str(int(cat_id))/f'image{ext}'
    if not fp.is_file(): raise HTTPException(404,'分类图片文件已丢失')
    return FileResponse(fp)

@app.get('/api/club/{club_id}/activity-categories/{cat_id}/image.{ext}')
def club_activity_category_image(club_id:int,cat_id:int,ext:str):
    return _serve_category_image(club_id,cat_id,ext)

@app.get('/api/public/activity-categories/{cat_id}/image.{ext}')
def public_activity_category_image(cat_id:int,ext:str):
    """C 端分类磁贴图：公开代理（顾客没有 club 角色，走不了俱乐部域那条）。"""
    with conn() as c:
        r=row(c.execute('SELECT club_id FROM activity_categories WHERE id=?',(cat_id,)))
    if not r: raise HTTPException(404,'分类不存在')
    return _serve_category_image(int(r['club_id']),cat_id,ext)

@app.patch('/api/club/{club_id}/activities/{activity_id}/categories')
def set_activity_categories(club_id:int,activity_id:int,payload:dict=Body(...)):
    """整组替换活动的分类归属（多选，全量提交）。categoryIds=[] 表示清除全部分类。"""
    club_or_404(club_id)
    ids=payload.get('categoryIds')
    if not isinstance(ids,list): raise HTTPException(400,'categoryIds 必须是数组')
    try: ids=sorted({int(x) for x in ids})
    except (TypeError,ValueError): raise HTTPException(400,'categoryIds 必须是整数数组')
    with conn() as c:
        a=row(c.execute('SELECT id FROM activities WHERE id=? AND club_id=?',(activity_id,club_id)))
        if not a: raise HTTPException(404,'活动不存在')
        if ids:
            own={r['id'] for r in rows(c.execute(
                f'SELECT id FROM activity_categories WHERE club_id=? AND id IN ({",".join("?"*len(ids))})',(club_id,*ids)))}
            bad=[i for i in ids if i not in own]
            if bad: raise HTTPException(400,'存在不属于本俱乐部的分类')
        c.execute('DELETE FROM activity_category_links WHERE activity_id=?',(activity_id,))
        for i in ids:
            c.execute('INSERT OR IGNORE INTO activity_category_links(activity_id,category_id) VALUES(?,?)',(activity_id,i))
    return {'ok':True,'categoryIds':ids}


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
def leader_occurrences(request:Request,club_id:int=1,leader_id:int=0):
    with conn() as c:
        out=[]
        identity=getattr(request.state,'identity',None)
        assigned=None
        if identity and identity.get('role')=='leader':
            assigned={r[0] for r in c.execute('SELECT occurrence_id FROM occurrence_leaders WHERE club_id=? AND phone=(SELECT phone FROM users WHERE id=?)',(club_id,identity.get('user_id')))}
        for it in execution_service.list_occurrences(c,club_id=club_id):
            if assigned is not None and it['id'] not in assigned: continue
            row={k:it.get(k) for k in _LEADER_OCC_FIELDS}
            ls=execution_service.list_leaders(c,int(it['id']))
            if leader_id and not any(int(l.get('leader_id') or 0)==leader_id for l in ls): continue
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

@app.post('/api/leader/occurrences/{occurrence_id}/checkin-by-token')
def leader_checkin_by_token(occurrence_id:int,payload:dict=Body(...),club_id:int=1):
    """扫码核销：领队端 jsQR 解出 token 后调此接口标记已签到。

    双重校验：① token 签名有效；② token 的活动/俱乐部归属与当前接口一致。
    actor='leader_scan' 与领队手动签到（actor='leader'）区分开，便于事后追溯。
    """
    token=str(payload.get('token') or '').strip()
    club_id_t,occ_id_t,pid_t,ok=_checkin_verify(token)
    if not ok: raise HTTPException(400,'签到码无效或已失效')
    if int(club_id_t)!=int(club_id) or int(occ_id_t)!=int(occurrence_id):
        raise HTTPException(403,'签到码与当前活动不匹配')
    with conn() as c:
        try:
            return execution_service.checkin(c,club_id=club_id,occurrence_id=occurrence_id,
                                             participant_id=pid_t,status='checked_in',
                                             actor='leader_scan',note='扫码签到')
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/public/participants/{participant_id}/checkin-qr')
def public_participant_checkin_qr(participant_id:int,user_id:int=0,club_id:int=1):
    """C 端「我的报名」里每个参加人领到的签到二维码（SVG）。

    仅本人（且参加人处于 active、报名已支付）可见，避免误领/冒领。
    二维码内容 = HMAC 签名 token，领队扫码后调 checkin-by-token 核销。
    """
    with conn() as c:
        p=c.execute('SELECT p.*,r.user_id,r.status reg_status FROM registration_participants p '
                    'JOIN registrations r ON r.id=p.registration_id '
                    'WHERE p.id=? AND p.club_id=?',(participant_id,club_id)).fetchone()
        if not p: raise HTTPException(404,'参加人不存在')
        if int(p['user_id'])!=int(user_id): raise HTTPException(403,'无权查看该签到码')
        if p['status']!='active' or p['reg_status']!='paid': raise HTTPException(409,'该参加人当前不可签到')
        token=_checkin_sign(club_id,p['occurrence_id'],participant_id)
        svg=_qr_svg(token)
    return Response(content=svg,media_type='image/svg+xml')

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

# ── 购买会员（2026-10-26）────────────────────────────────────────────────
# 前提：俱乐部开通了「购买会员」功能（clubs.membership_purchase_enabled）。
# 俱乐部自定义会员等级卡（价格 + 有效期 + 权益），C 端付费购买，购买订单俱乐部可查。
@app.get('/api/club/{club_id}/membership/purchase-enabled')
def club_membership_purchase_enabled(club_id:int):
    x=club_or_404(club_id)
    return {'enabled':bool(x.get('membership_purchase_enabled'))}

@app.post('/api/club/{club_id}/membership/purchase-enabled')
def club_membership_purchase_toggle(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    enabled=1 if payload.get('enabled') else 0
    with conn() as c:
        c.execute('UPDATE clubs SET membership_purchase_enabled=? WHERE id=?',(enabled,club_id))
    return {'ok':True,'enabled':bool(enabled)}

@app.get('/api/club/{club_id}/member-orders')
def club_member_orders(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        return rows(c.execute('''SELECT o.*, u.name user_name, u.phone user_phone
                                 FROM member_orders o JOIN users u ON u.id=o.user_id
                                 WHERE o.club_id=? ORDER BY o.id DESC LIMIT 200''',(club_id,)))

@app.get('/api/public/clubs/{club_id}/membership/purchasable')
def public_purchasable_tiers(request:Request,club_id:int,user_id:int|None=None):
    """C 端可购买的会员卡 + 当前用户的有效购买记录（俱乐部未开通时返回空列表）。"""
    club_or_404(club_id)
    user_id=public_user_id(request,user_id)
    with conn() as c:
        x=club_or_404(club_id)
        if not x.get('membership_purchase_enabled'):
            return {'enabled':False,'tiers':[],'activePurchase':None,'paymentAvailable':not IS_PROD}
        tiers=membership_engine.purchasable_tiers(c,club_id)
        purchase=membership_engine.active_purchase(c,club_id=club_id,user_id=user_id)
    return {'enabled':True,'tiers':tiers,'activePurchase':purchase,'paymentAvailable':not IS_PROD}

@app.post('/api/public/clubs/{club_id}/membership/purchase')
def public_purchase_membership(request:Request,club_id:int,payload:MembershipPurchase):
    """购买会员卡：写购买订单并立即生效（演示支付通道；接微信支付后在此换真实收单）。"""
    club_or_404(club_id)
    user_id=public_user_id(request,payload.userId)
    tier_id=payload.tierId
    if IS_PROD: raise HTTPException(503,'会员购买暂未开放，支付接入完成后恢复；当前会员权益不受影响')
    with conn() as c:
        x=club_or_404(club_id)
        if not x.get('membership_purchase_enabled'):
            raise HTTPException(409,'该俱乐部未开通购买会员功能')
        try:
            result=membership_engine.purchase_tier(c,club_id=club_id,user_id=user_id,tier_id=tier_id)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
        member=row(c.execute('''SELECT m.*,u.name,u.phone FROM club_members m JOIN users u ON u.id=m.user_id
                                WHERE m.club_id=? AND m.user_id=?''',(club_id,user_id)))
        wallet=wallet_snapshot(c,user_id,club_id)
    return {'ok':True,**result,'member':member,'wallet':wallet}

@app.get('/api/club/{club_id}/gear-discount')
def club_gear_discount(club_id:int):
    """本俱乐部当前生效的装备商城会员折扣（只读）。

    折扣由总平台按俱乐部订阅的套餐统一控制（2026-10-23），会员等级不参与；
    俱乐部端只用于展示（商城会员价、等级页说明），无任何修改入口。"""
    club_or_404(club_id)
    with conn() as c:
        rate,name,code=ai_credit_engine.plan_gear_rate(c,club_id)
    return {'rate':round(rate,4),'discountZhe':round(rate*10,2),'planName':name,'planCode':code,
            'label':(f'{name} 套餐 {rate*10:g} 折' if rate<1 else '无折扣（按原价）')}

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

# ── 消费送券（2026-10-26）────────────────────────────────────────────────
# 单笔活动消费满 X 元自动赠送某张券；只能送本俱乐部的活动券且券须已有库存。
@app.get('/api/club/{club_id}/coupon-gift-rules')
def club_coupon_gift_rules(club_id:int):
    club_or_404(club_id)
    with conn() as c: return benefit_engine.gift_rules(c,club_id=club_id)

@app.post('/api/club/{club_id}/coupon-gift-rules')
def club_add_coupon_gift_rule(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        try: rid=benefit_engine.create_gift_rule(c,club_id=club_id,payload=payload)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True,'ruleId':rid}

@app.patch('/api/club/{club_id}/coupon-gift-rules/{rule_id}')
def club_update_coupon_gift_rule(club_id:int,rule_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        try: benefit_engine.update_gift_rule(c,club_id=club_id,rule_id=rule_id,payload=payload)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True}

@app.delete('/api/club/{club_id}/coupon-gift-rules/{rule_id}')
def club_delete_coupon_gift_rule(club_id:int,rule_id:int):
    club_or_404(club_id)
    with conn() as c: benefit_engine.delete_gift_rule(c,club_id=club_id,rule_id=rule_id)
    return {'ok':True}

# ── 积分商城（2026-10-26）────────────────────────────────────────────────
# 俱乐部用活动积分上架兑换商品：优惠券（需提前在优惠券中设计好）/ 活动 / 服务 / 实物。
@app.get('/api/club/{club_id}/points-mall')
def club_points_mall(club_id:int):
    club_or_404(club_id)
    with conn() as c: return points_mall_engine.list_items(c,club_id=club_id)

@app.post('/api/club/{club_id}/points-mall')
def club_points_mall_add(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        try: iid=points_mall_engine.create_item(c,club_id=club_id,payload=payload)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True,'itemId':iid}

@app.patch('/api/club/{club_id}/points-mall/{item_id}')
def club_points_mall_update(club_id:int,item_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        try: points_mall_engine.update_item(c,club_id=club_id,item_id=item_id,payload=payload)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True}

@app.delete('/api/club/{club_id}/points-mall/{item_id}')
def club_points_mall_delete(club_id:int,item_id:int):
    club_or_404(club_id)
    with conn() as c: points_mall_engine.delete_item(c,club_id=club_id,item_id=item_id)
    return {'ok':True}

@app.get('/api/club/{club_id}/points-mall/redemptions')
def club_points_mall_redemptions(club_id:int):
    club_or_404(club_id)
    with conn() as c: return points_mall_engine.redemption_records(c,club_id=club_id)

@app.post('/api/club/{club_id}/points-mall/verify')
def club_points_mall_verify(club_id:int,payload:dict=Body(...)):
    """核销兑换码（活动/服务/实物类履约）。"""
    club_or_404(club_id)
    with conn() as c:
        try: r=points_mall_engine.verify(c,club_id=club_id,code=str(payload.get('code') or ''),operator='club')
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))
    return {'ok':True,'redemption':r}

@app.get('/api/public/clubs/{club_id}/points-mall')
def public_points_mall(request:Request,club_id:int,user_id:int|None=None):
    """C 端积分商城：可兑换商品 + 我的兑换记录 + 当前积分。"""
    club_or_404(club_id)
    user_id=public_user_id(request,user_id)
    with conn() as c:
        items=points_mall_engine.list_items(c,club_id=club_id,include_inactive=False)
        redemptions=points_mall_engine.my_redemptions(c,club_id=club_id,user_id=user_id)
        wallet=wallet_snapshot(c,user_id,club_id)
        # 2026-11-05：积分商城页头「X 积分 = ¥1」按该会员当前等级动态展示。
        # 注意商城的 points_cost 是固定积分标价、不按现金比换算，所以文案只说明
        # 「报名抵扣」的换算，避免让顾客以为兑换商品也在按这个比例折钱。
        rate=resolve_club_points_redeem_rate(c,club_id=int(club_id),user_id=int(user_id),setting=setting)
    return {'items':items,'redemptions':redemptions,'wallet':wallet,'pointsRedeemRate':rate}

@app.post('/api/public/clubs/{club_id}/points-mall/{item_id}/redeem')
def public_points_mall_redeem(request:Request,club_id:int,item_id:int,payload:dict=Body(...)):
    user_id=public_user_id(request,payload.get('userId'))
    with conn() as c:
        try:
            result=points_mall_engine.redeem(c,club_id=club_id,item_id=item_id,user_id=user_id)
        except LookupError as e: raise HTTPException(404,str(e))
        except OverflowError as e: raise HTTPException(409,str(e))
        except ValueError as e: raise HTTPException(409,str(e))
        wallet=wallet_snapshot(c,user_id,club_id)
    return {'ok':True,**result,'wallet':wallet}

# ---- 升级赠券 / 拉新邀请 / 券转赠（2026-10-26）--------------------------------

# 2026-10-27 升级奖励 v2：一个等级一条规则，可只送券 / 只送积分 / 两者都给。
# 老接口 /tier-gift-rules 保留但转发到新表（老客户端与老用例继续可用）。

@app.get('/api/club/{club_id}/tier-reward-rules')
def club_tier_reward_rules(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        return benefit_engine.tier_reward_rules(c,club_id=club_id)

@app.post('/api/club/{club_id}/tier-reward-rules')
def club_create_tier_reward_rule(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        try:
            rid=benefit_engine.create_tier_reward_rule(c,club_id=club_id,payload=payload)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True,'ruleId':rid}

@app.patch('/api/club/{club_id}/tier-reward-rules/{rule_id}')
def club_update_tier_reward_rule(club_id:int,rule_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        try:
            benefit_engine.update_tier_reward_rule(c,club_id=club_id,rule_id=rule_id,payload=payload)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True}

@app.delete('/api/club/{club_id}/tier-reward-rules/{rule_id}')
def club_delete_tier_reward_rule(club_id:int,rule_id:int):
    club_or_404(club_id)
    with conn() as c:
        benefit_engine.delete_tier_reward_rule(c,club_id=club_id,rule_id=rule_id)
    return {'ok':True}

@app.get('/api/club/{club_id}/tier-gift-rules')
def club_tier_gift_rules(club_id:int):
    """兼容入口：2026-10-27 起真身是 tier-reward-rules。"""
    club_or_404(club_id)
    with conn() as c:
        return benefit_engine.tier_reward_rules(c,club_id=club_id)

@app.post('/api/club/{club_id}/tier-gift-rules')
def club_create_tier_gift_rule(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        try:
            rid=benefit_engine.create_tier_reward_rule(c,club_id=club_id,payload=payload)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True,'ruleId':rid}

@app.patch('/api/club/{club_id}/tier-gift-rules/{rule_id}')
def club_update_tier_gift_rule(club_id:int,rule_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        try:
            benefit_engine.update_tier_reward_rule(c,club_id=club_id,rule_id=rule_id,payload=payload)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True}

@app.delete('/api/club/{club_id}/tier-gift-rules/{rule_id}')
def club_delete_tier_gift_rule(club_id:int,rule_id:int):
    club_or_404(club_id)
    with conn() as c:
        benefit_engine.delete_tier_reward_rule(c,club_id=club_id,rule_id=rule_id)
    return {'ok':True}

@app.get('/api/club/{club_id}/referral-coupon')
def club_referral_coupon(club_id:int):
    """俱乐部拉新设定：注册会员自动发放的「邀请优惠券」是哪张。"""
    club_or_404(club_id)
    with conn() as c:
        cfg=row(c.execute('SELECT referral_coupon_benefit_id FROM clubs WHERE id=?',(club_id,)))
        bid=int((cfg or {}).get('referral_coupon_benefit_id') or 0)
        b=benefit_engine._coupon_row(c,bid) if bid else None
        pool=rows(c.execute('''SELECT id,title,mode,cash_value,threshold_amount,discount_percent,stock,status
                               FROM member_benefits WHERE owner_type='CLUB' AND owner_club_id=? AND benefit_type='activity_coupon'
                               ORDER BY id DESC''',(club_id,)))
    return {'benefitId':bid,'benefit':({'id':b['id'],'title':b['title']} if b else None),'pool':pool}

@app.patch('/api/club/{club_id}/referral-coupon')
def club_set_referral_coupon(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    bid=int(payload.get('benefitId') or 0)
    if bid:
        with conn() as c:
            b=row(c.execute('''SELECT id FROM member_benefits WHERE id=? AND owner_type='CLUB' AND owner_club_id=?
                               AND benefit_type='activity_coupon' ''',(bid,club_id)))
            if not b: raise HTTPException(400,'只能选择本俱乐部创建的活动优惠券作为邀请券')
            c.execute('UPDATE clubs SET referral_coupon_benefit_id=? WHERE id=?',(bid,club_id))
    else:
        with conn() as c:
            c.execute('UPDATE clubs SET referral_coupon_benefit_id=0 WHERE id=?',(club_id,))
    return {'ok':True,'benefitId':bid}

@app.get('/api/public/clubs/{club_id}/membership/invite-info')
def public_invite_info(request:Request,club_id:int,user_id:int|None=None):
    """C 端邀请卡：我的邀请码 + 已邀请人数 + 奖励说明。"""
    club_or_404(club_id)
    user_id=public_user_id(request,user_id)
    with conn() as c:
        code=membership_engine.ensure_invite_code(c,club_id=club_id,user_id=user_id)
        invited=rows(c.execute('''SELECT m.user_id,u.name,m.level,m.created_at
                                  FROM club_members m JOIN users u ON u.id=m.user_id
                                  WHERE m.club_id=? AND m.referred_by=? ORDER BY m.id DESC LIMIT 20''',(club_id,user_id)))
        cfg=row(c.execute('SELECT referral_coupon_benefit_id FROM clubs WHERE id=?',(club_id,)))
        b=benefit_engine._coupon_row(c,int((cfg or {}).get('referral_coupon_benefit_id') or 0)) if (cfg or {}).get('referral_coupon_benefit_id') else None
    return {'inviteCode':code,'invitedCount':len(invited),'invited':invited,
            'reward':{'title':b['title'],'mode':b.get('mode'),'cashValue':float(b.get('cash_value') or 0),
                      'discountPercent':float(b.get('discount_percent') or 0)} if b else None}

@app.post('/api/public/clubs/{club_id}/membership/join')
def public_join_with_invite(request:Request,club_id:int,payload:dict=Body(...)):
    """新会员填邀请码入会：记录邀请关系，双方自动获赠「邀请优惠券」（若俱乐部已设定）。"""
    club_or_404(club_id)
    user_id=public_user_id(request,payload.get('userId'))
    with conn() as c:
        try:
            info=membership_engine.join_with_invite(c,club_id=club_id,user_id=user_id,invite_code=str(payload.get('inviteCode') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
        gifts=[]
        if not info.get('alreadyBound'):
            cfg=row(c.execute('SELECT referral_coupon_benefit_id FROM clubs WHERE id=?',(club_id,)))
            if (cfg or {}).get('referral_coupon_benefit_id'):
                b=benefit_engine._coupon_row(c,int(cfg['referral_coupon_benefit_id']))
                if b:
                    try:
                        for uid,note in ((user_id,'好友邀请注册赠送'),(int(info['inviterUserId']),'成功邀请好友注册赠送')):
                            gifts.append(benefit_engine._issue(c,b,uid,club_id,funding='CLUB',note=note))
                    except Exception:
                        pass
        wallet=wallet_snapshot(c,user_id,club_id)
    return {'ok':True,**info,'gifts':gifts,'wallet':wallet}

@app.post('/api/public/clubs/{club_id}/coupons/transfer')
def public_transfer_coupon(request:Request,club_id:int,payload:dict=Body(...)):
    """券转赠：好友输入券码领取（接收方须已是本俱乐部会员，券未使用未过期且允许分享）。"""
    club_or_404(club_id)
    to_user_id=public_user_id(request,payload.get('toUserId'))
    with conn() as c:
        try:
            result=benefit_engine.transfer_coupon(c,club_id=club_id,code=str(payload.get('code') or ''),to_user_id=to_user_id)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
        wallet=wallet_snapshot(c,to_user_id,club_id)
    return {'ok':True,**result,'wallet':wallet}

# ---- 会员深度管理（2026-10-22 用户截图反馈：点进会员要看全量信息 + 调积分 + 赠券）----
# 脱敏纪律同 _public_leaders：这里全是俱乐部自己建档/收单的数据，俱乐部后台可见；
# 但 C 端公开接口绝不复用本函数——给领队的备注、内部成本一律不出俱乐部边界。

@app.get('/api/club/{club_id}/members/{user_id}')
def club_member_detail(club_id:int,user_id:int):
    club_or_404(club_id)
    with conn() as c:
        member=row(c.execute('''SELECT m.*,u.name,u.phone,u.created_at AS user_created_at
                                FROM club_members m JOIN users u ON u.id=m.user_id
                                WHERE m.club_id=? AND m.user_id=?''',(club_id,user_id)))
        if not member: raise HTTPException(404,'该用户还不是本俱乐部会员')
        # 先确认会员存在再刷新：refresh_member 对陌生 user_id 会 INSERT，撞外键约束。
        membership_engine.refresh_member(c,club_id=club_id,user_id=user_id)
        member=row(c.execute('''SELECT m.*,u.name,u.phone,u.created_at AS user_created_at
                                FROM club_members m JOIN users u ON u.id=m.user_id
                                WHERE m.club_id=? AND m.user_id=?''',(club_id,user_id)))
        g=row(c.execute('SELECT balance FROM gear_point_accounts WHERE user_id=?',(user_id,)))
        member['gear_points']=int((g or {}).get('balance') or 0)
        tiers=membership_engine.list_tiers(c,club_id)
        tier=next((t for t in tiers if t.get('id')==member.get('current_tier_id')),None)
        activity_orders=rows(c.execute('''SELECT r.id,r.occurrence_id,r.status,r.payment_status,r.refund_status,
                                                 r.amount,r.participant_count,r.created_at,a.title,a.location,a.event_date
                                          FROM registrations r JOIN activities a ON a.id=r.activity_id
                                          WHERE r.user_id=? AND r.club_id=? ORDER BY r.id DESC LIMIT 50''',(user_id,club_id)))
        for o in activity_orders:
            o['participants']=rows(c.execute('SELECT name,phone,status FROM registration_participants WHERE registration_id=? ORDER BY id',(o['id'],)))
        gear_orders=rows(c.execute('''SELECT o.*,(SELECT GROUP_CONCAT(COALESCE(p.name,'已下架商品')||' x'||i.quantity,'；')
                                          FROM gear_order_items i LEFT JOIN products p ON p.id=i.product_id
                                          WHERE i.order_id=o.id) AS items_text
                                   FROM gear_orders o WHERE o.user_id=? ORDER BY o.id DESC LIMIT 50''',(user_id,)))
        club_ledger=rows(c.execute('SELECT * FROM club_point_ledger WHERE club_id=? AND user_id=? ORDER BY id DESC LIMIT 30',(club_id,user_id)))
        gear_ledger=rows(c.execute('SELECT * FROM gear_point_ledger WHERE user_id=? ORDER BY id DESC LIMIT 30',(user_id,)))
        vouchers=rows(c.execute('''SELECT r.*,b.title,b.benefit_type,b.description
                                   FROM benefit_redemptions r JOIN member_benefits b ON b.id=r.benefit_id
                                   WHERE r.user_id=? AND r.display_club_id=? ORDER BY r.id DESC LIMIT 50''',(user_id,club_id)))
        # 装备折扣随俱乐部订阅套餐（平台控制，等级不参与）：C 端会员中心直接展示这一个值。
        grate,gname,gcode=ai_credit_engine.plan_gear_rate(c,club_id)
        gear_discount={'rate':round(grate,4),'discountZhe':round(grate*10,2),'planName':gname,'planCode':gcode}
    return {'member':member,'tier':tier,'activityOrders':activity_orders,'gearOrders':gear_orders,
            'clubLedger':club_ledger,'gearLedger':gear_ledger,'vouchers':vouchers,'gearDiscount':gear_discount}

@app.post('/api/club/{club_id}/members/{user_id}/points')
def club_adjust_member_points(club_id:int,user_id:int,payload:dict=Body(...)):
    """手工增减会员**活动积分**。正数加、负数扣；每次调整必写积分流水（type=manual_adjust），
    余额不允许被调成负数。
    装备积分是 ClubOS 总平台体系积分（成本由平台承担），俱乐部无权调整（2026-10-23 用户明确）：
    kind=gear 一律 403，只有平台侧能改 gear_point_accounts。"""
    club_or_404(club_id)
    kind=str(payload.get('kind') or 'club').lower()
    if kind=='gear': raise HTTPException(403,'装备积分由 ClubOS 总平台统一调整，俱乐部只能调整活动积分')
    if kind!='club': raise HTTPException(400,'积分类型只能是 club（活动积分）；装备积分由总平台调整')
    try: amount=int(payload.get('amount') or 0)
    except (TypeError,ValueError): raise HTTPException(400,'调整数量必须是整数')
    if amount==0: raise HTTPException(400,'调整数量不能为 0')
    if abs(amount)>1000000: raise HTTPException(400,'单次调整幅度不能超过 1,000,000')
    note=str(payload.get('note') or '').strip()[:200] or '俱乐部手工调整'
    with conn() as c:
        mem=row(c.execute('SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?',(club_id,user_id)))
        if not mem: raise HTTPException(404,'该用户还不是本俱乐部会员')
        newbal=int(mem['club_points_balance'] or 0)+amount
        if newbal<0: raise HTTPException(409,f'调整后活动积分为 {newbal}，不能为负；请先调小扣减数量')
        c.execute('UPDATE club_members SET club_points_balance=? WHERE club_id=? AND user_id=?',(newbal,club_id,user_id))
        c.execute('''INSERT INTO club_point_ledger(club_id,user_id,type,amount,source_type,source_id,note)
                     VALUES(?,?,?,?,?,?,?)''',(club_id,user_id,'manual_adjust',amount,'manual','',note))
    return {'ok':True,'kind':'club','amount':amount,'balance':newbal}

@app.post('/api/club/{club_id}/members/{user_id}/vouchers')
def club_gift_member_voucher(club_id:int,user_id:int,payload:dict=Body(...)):
    """把本俱乐部在营销里已生成的优惠券直接赠给会员（零积分，成本由俱乐部承担）。
    赠出的券立刻出现在顾客 C 端「可用福利券」里，报名付款时可直接抵扣。"""
    club_or_404(club_id)
    try: benefit_id=int(payload.get('benefitId'))
    except (TypeError,ValueError): raise HTTPException(400,'请选择要赠送的优惠券')
    note=str(payload.get('note') or '').strip()[:200] or '俱乐部赠送'
    with conn() as c:
        mem=row(c.execute('SELECT id FROM club_members WHERE club_id=? AND user_id=?',(club_id,user_id)))
        if not mem: raise HTTPException(404,'该用户还不是本俱乐部会员')
        b=row(c.execute('SELECT * FROM member_benefits WHERE id=?',(benefit_id,)))
        if not b or b['status']!='active': raise HTTPException(404,'优惠券不存在或已下架')
        # 平台券也允许俱乐部转发给会员（2026-10-24）：营销活动由俱乐部发起、
        # 成本仍由总平台承担 —— 券的 funding_owner 保持 PLATFORM，结算照旧进平台补贴账。
        if b['owner_type']=='CLUB' and int(b['owner_club_id'] or 0)!=club_id:
            raise HTTPException(409,'只能赠送本俱乐部创建的优惠券')
        if b['owner_type']=='PLATFORM':
            if b['benefit_type']!='activity_coupon':
                raise HTTPException(409,'平台券只有活动报名券可以转发给会员')
            if b['target_club_id'] is not None and int(b['target_club_id'])!=club_id:
                raise HTTPException(409,'该平台券未向本俱乐部开放')
            # 城市定向券是给「该城市用户」的定向投放，俱乐部转发会越权（把券发给
            # 不在该城市的人）—— 城市定向只能由总平台自己发。
            if b['target_club_id'] is None and str(b.get('target_type') or 'all_clubs')=='city':
                raise HTTPException(409,'城市定向的券不支持俱乐部转发，请由总平台发放')
            if int(b.get('points_cost') or 0)>0:
                raise HTTPException(409,'该平台券需积分兑换，不能直接转发')
        if b['stock'] is not None:
            if int(b['stock'])<=0: raise HTTPException(409,'该优惠券库存不足')
            c.execute('UPDATE member_benefits SET stock=stock-1,updated_at=CURRENT_TIMESTAMP WHERE id=?',(benefit_id,))
        code='BEN-'+secrets.token_hex(4).upper()
        c.execute('''INSERT INTO benefit_redemptions(benefit_id,user_id,display_club_id,point_type,points_spent,
                     funding_owner,cash_value,status,voucher_code,gift_note) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                  (benefit_id,user_id,club_id,b['points_type'],0,
                   ('CLUB' if b['owner_type']=='CLUB' else 'PLATFORM'),
                   float(b['cash_value'] or 0),'issued',code,note))
        rid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
    return {'ok':True,'redemptionId':rid,'voucherCode':code,'title':b['title'],
            'benefitType':b['benefit_type'],'cashValue':float(b['cash_value'] or 0)}

@app.get('/api/club/{club_id}/credits')
def credits(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        st=ai_credit_engine.statement(c,club_id=club_id)
        st['topupPackages']=ai_credit_engine.topup_packages(c)
        # trial 体验套餐不进俱乐部自助订阅列表（2026-11-03）：它只随入驻审核通过
        # 由平台自动开通，挂在这里会诱导俱乐部手动「订阅」一个不收费的档位。
        st['plans']=[p for p in ai_credit_engine.plans(c) if p.get('code')!='trial']
        st['pendingOrders']=[x for x in ai_credit_engine.orders(c,club_id=club_id,limit=50) if x['status']=='pending']
        return {'account':st['account'],'ledger':st['ledger'][:60],'subscription':st['subscription'],'unresolvedDebt':st['unresolvedDebt'],
                'creditsConsumed':st['creditsConsumed'],'successfulCalls':st['successfulCalls'],'topupPackages':st['topupPackages'],
                'plans':st['plans'],'pendingOrders':st['pendingOrders'],'period':st['period']}

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

@app.post('/api/club/{club_id}/subscription/plan')
def club_subscribe_plan(club_id:int,payload:dict=Body(...)):
    """俱乐部自助订阅/更换 AI Credits 套餐（2026-10-24 用户问「俱乐部要订阅 SaaS 在哪里订」——
    此前只有平台能代分配，俱乐部端没有入口，逻辑没通）。

    与平台代分配（PATCH /api/platform/clubs/{id}/plan）走同一套账务 assign_plan：
    订阅即刻生效并切换月度额度口径，同时生成当月「待付款」账单；
    账单必须由总平台确认收款（confirm-paid）后 Credits 才真正入账——
    俱乐部不能自己确认付款，「钱货两讫」的闸门仍然握在平台手里。
    """
    club=club_or_404(club_id)
    if club.get('status')!='active': raise HTTPException(403,'俱乐部当前未启用，不能订阅套餐')
    code=str(payload.get('planCode') or '').strip()
    if not code: raise HTTPException(400,'planCode 必填')
    with conn() as c:
        try:return ai_credit_engine.assign_plan(c,club_id=club_id,plan_code=code,
                                                auto_renew=bool(payload.get('autoRenew',True)))
        except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/club/{club_id}/ai/usage')
def club_ai_usage(club_id:int,date_from:str|None=None,date_to:str|None=None,limit:int=100,offset:int=0):
    """AI 用量明细。2026-10-23 用户反馈：原来固定最近 100 条、不能按日期筛，
    历史翻不到、页面只能一直往下拉。这里加 date_from/date_to（YYYY-MM-DD，
    本地 +8 日历日，口径与商城订单/佣金完全一致）+ 分页。
    默认 limit=100/offset=0，旧调用方（含冒烟测试）行为不变。"""
    club_or_404(club_id)
    lo,hi=_date_range(date_from,date_to)
    lim=max(1,min(int(limit or 100),200)); off=max(0,int(offset or 0))
    sql=('SELECT id,task_type,provider,model,status,input_tokens,output_tokens,provider_cost,'
         'credits_charged,created_at FROM ai_usage_records WHERE club_id=?')
    args=[club_id]
    if lo: sql+=" AND date(created_at,'+8 hours')>=?"; args.append(lo)
    if hi: sql+=" AND date(created_at,'+8 hours')<=?"; args.append(hi)
    # 按发生时间倒序（同秒再按 id），前端「按日分组」后组内才是最新在上；
    # 只按 id 排的话，赶时间插入的历史数据会出现在今天那一组的中间。
    sql+=' ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?'; args+= [lim,off]
    with conn() as c: return rows(c.execute(sql,args))

@app.get('/api/club/{club_id}/ai/usage/summary')
def club_ai_usage_summary(club_id:int,date_from:str|None=None,date_to:str|None=None):
    """同一日期区间的用量汇总：调用次数/成功率/token/扣费，并按任务类型拆开。
    只给明细的话，用户看得出「花了多少」却看不出「花在哪一步」，超额时不知道该调哪类用量。"""
    club_or_404(club_id)
    lo,hi=_date_range(date_from,date_to)
    sql=('SELECT task_type,status,input_tokens,output_tokens,provider_cost,credits_charged '
         'FROM ai_usage_records WHERE club_id=?')
    args=[club_id]
    if lo: sql+=" AND date(created_at,'+8 hours')>=?"; args.append(lo)
    if hi: sql+=" AND date(created_at,'+8 hours')<=?"; args.append(hi)
    with conn() as c: recs=rows(c.execute(sql,args))
    tasks={}
    for x in recs:
        k=str(x.get('task_type') or 'other')
        b=tasks.setdefault(k,{'task':k,'calls':0,'success':0,'inputTokens':0,
                              'outputTokens':0,'credits':0,'providerCost':0.0})
        b['calls']+=1
        if str(x.get('status') or '')=='success': b['success']+=1
        b['inputTokens']+=int(x.get('input_tokens') or 0)
        b['outputTokens']+=int(x.get('output_tokens') or 0)
        b['credits']+=int(x.get('credits_charged') or 0)
        b['providerCost']=round(b['providerCost']+float(x.get('provider_cost') or 0),8)
    calls=len(recs); success=sum(1 for x in recs if str(x.get('status') or '')=='success')
    return {'calls':calls,'success':success,'failed':calls-success,
            'inputTokens':sum(int(x.get('input_tokens') or 0) for x in recs),
            'outputTokens':sum(int(x.get('output_tokens') or 0) for x in recs),
            'credits':sum(int(x.get('credits_charged') or 0) for x in recs),
            'providerCost':round(sum(float(x.get('provider_cost') or 0) for x in recs
                                     if str(x.get('status') or '')=='success'),8),
            'byTask':sorted(tasks.values(),key=lambda b:(-b['calls'],-b['credits']))}

@app.delete('/api/club/{club_id}/ai/usage/unbilled-failures')
def club_ai_usage_clear_unbilled_failures(club_id:int):
    """清理「生成失败且未扣费」的用量噪声记录。

    只删本俱乐部 status='error' 且 COALESCE(credits_charged,0)=0 的记录：
    这类是调用失败时留下的痕迹，没有产生任何 Credits 扣费或平台成本，
    但会混进用量明细、把成功率算低（例如主视觉模型参数升级期间的一次 400）。
    成功记录、pending 记录、以及已扣费的失败记录（可能涉及对账/退款）一律不动，
    保证计费审计链完整。前端在「智能用量明细」提供二次确认后的手动清理入口。
    """
    club_or_404(club_id)
    with conn() as c:
        cur=c.execute("""DELETE FROM ai_usage_records
                         WHERE club_id=? AND status='error'
                           AND COALESCE(credits_charged,0)=0""",(int(club_id),))
        return {'ok':True,'deleted':cur.rowcount}

@app.get('/api/club/{club_id}/credits/ledger')
def club_credit_ledger(club_id:int,date_from:str|None=None,date_to:str|None=None,limit:int=50,offset:int=0):
    """Credits 流水（2026-10-23 用户反馈）。旧的 GET /credits 是「当月账本前 60 条」：
    跨月翻不到、也不能按日期筛，条目一多就退化成无限下拉。这条独立端点按任意区间
    分页，并回带区间汇总（入账/消耗/净变化/笔数）——前端据此做「按日分组 + 加载更多」。
    旧端点保持原样，避免影响余额/套餐/待付款账单那一套。"""
    club_or_404(club_id)
    lo,hi=_date_range(date_from,date_to)
    lim=max(1,min(int(limit or 50),200)); off=max(0,int(offset or 0))
    where=' WHERE club_id=?'; args=[club_id]
    if lo: where+=" AND date(created_at,'+8 hours')>=?"; args.append(lo)
    if hi: where+=" AND date(created_at,'+8 hours')<=?"; args.append(hi)
    with conn() as c:
        total=int(c.execute('SELECT COUNT(*) FROM ai_credit_ledger'+where,args).fetchone()[0] or 0)
        agg=c.execute('SELECT COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0),'
                      'COALESCE(SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END),0),'
                      'COALESCE(SUM(amount),0) FROM ai_credit_ledger'+where,args).fetchone()
        items=rows(c.execute('SELECT * FROM ai_credit_ledger'+where+
                             ' ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?',args+[lim,off]))
    return {'items':items,'total':total,'limit':lim,'offset':off,
            'summary':{'income':int(agg[0] or 0),'expense':int(agg[1] or 0),
                       'net':int(agg[2] or 0),'count':total}}

# 商品列表/详情共用的列：品牌与品类用子查询带出名字，避免每个调用点各写一遍 JOIN。
# 保留 category 文本列是因为老接口（列表副标题、Medusa 同步）还在读它 ——
# 迁移只回填 category_id，不删文本，两边同时可用。
PROD_COLS = """id,name,sku,price,stock,status,image_url,cover_url,catalog_view,category,brand_id,category_id,gender,season,series,material,is_featured,
               source,inventory_mode,fulfillment_mode,
               (SELECT source_type FROM product_brands b WHERE b.id=products.brand_id) brand_source_type,
               (SELECT name FROM product_brands b WHERE b.id=products.brand_id) brand_name,
               (SELECT name FROM product_categories ct WHERE ct.id=products.category_id) category_name,
               (SELECT sort FROM product_categories ct WHERE ct.id=products.category_id) category_sort"""


def _parse_catalog_view(raw):
    """catalog_view 落库的是 JSON 数组字符串 [srcW,srcH,cropX,cropY,cropW,cropH]（PEAKTRACE
    合成图头图裁切区）。解析成数值数组下发（前端 prodImgHtml 按 SVG viewBox 裁切）；
    解析失败返回 None，前端退回普通整图渲染 —— 坏数据只降级展示，不打接口。"""
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if isinstance(v, list) and len(v) == 6 and all(isinstance(n, (int, float)) for n in v):
        return v
    return None


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
    for r in c.execute(f"SELECT id,product_id,name,sku,price,stock,sort,is_default,color,color_hex,size,barcode FROM product_variants WHERE product_id IN ({ph}) AND status='active' ORDER BY product_id,sort,id",ids).fetchall():
        variants.setdefault(int(r['product_id']),[]).append({
            'id':int(r['id']),'name':r['name'],'sku':r['sku'],
            'price':float(r['price']) if r['price'] is not None else None,
            'stock':int(r['stock'] or 0),'isDefault':int(r['is_default'] or 0),
            # 颜色尺码是 SKU 的两个维度（存量规格可能都为空 —— 那时 C 端仍按 name 一维显示）
            'color':r['color'],'colorHex':r['color_hex'],'size':r['size'],'barcode':r['barcode']})
    return images,variants

def _with_extras(c, product_list:list[dict]):
    if not product_list: return product_list
    images,variants=_product_extras(c,[x['id'] for x in product_list])
    # 已完成库存镜像的外部商品可售；其余外部品牌仍只展示（库存不外露）。
    try:
        mirrored_ids={int(r[0]) for r in c.execute("""SELECT DISTINCT product_id FROM external_product_mappings
                        WHERE external_sku_id='' AND external_sync_status='stock_synced'""")}
    except Exception:
        mirrored_ids=set()
    out=[]
    for x in product_list:
        d=dict(x); pid=int(x['id'])
        gal=images.get(pid) or []
        cover=x.get('cover_url') or None
        # 头图优先作为第一张：C 端卡片 / 详情首图都读它；图集里若已有同 url 则不重复。
        all_imgs=[]; seen=set()
        if cover:
            all_imgs.append({'url':cover,'cover':True}); seen.add(cover)
        for g in gal:
            u=g['url']
            if u and u not in seen:
                all_imgs.append({'url':u}); seen.add(u)
        if not all_imgs and x.get('image_url'):
            all_imgs.append({'url':x['image_url']})
        d['images']=all_imgs
        d['coverUrl']=cover or (all_imgs[0]['url'] if all_imgs else None)
        # 合成图头图裁切区：C 端列表/详情头图按它裁出干净单视图（2026-11-04）。
        d['catalogView']=_parse_catalog_view(x.get('catalog_view'))
        d['variants']=variants.get(pid) or []
        mirror_ok=(d.get('inventory_mode')=='external_mirror'
                   and d.get('fulfillment_mode')=='platform_warehouse' and pid in mirrored_ids)
        d['externalMirrored']=mirror_ok
        external=not mirror_ok and (d.get('source')=='external_api' or d.get('inventory_mode')!='platform_owned' or d.get('brand_source_type')=='external_api')
        d['displayOnly']=external
        d['purchaseAvailable']=not external
        if external:
            d['stock']=None
            for v in d['variants']:v['stock']=None
        out.append(d)
    return out

@app.get('/api/club/{club_id}/mall/products')
def club_products(club_id:int, *, brand_id:int|None=None, category_id:int|None=None, gender:str|None=None):
    """C 端商城列表。

    三个筛选项走 SQL 而不是前端过滤：商品池是全局的（不按俱乐部切分），
    传全量再在前端筛，等于每次进商城都把整个商品池拉到手机上。
    """
    club_or_404(club_id)
    where=['status="active"']; args=[]
    if brand_id: where.append('brand_id=?'); args.append(int(brand_id))
    if category_id: where.append('category_id=?'); args.append(int(category_id))
    if gender: where.append('gender=?'); args.append(catalog_service.normalize_gender(gender))
    with conn() as c:
        items=rows(c.execute('SELECT %s FROM products WHERE %s ORDER BY id DESC' % (PROD_COLS,' AND '.join(where)),args))
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

def _date_range(date_from:str|None,date_to:str|None)->tuple[str|None,str|None]:
    """日期范围过滤参数（YYYY-MM-DD，本地日期）：格式非法一律 400，且 from>to 也拒。
    返回 (lo,hi)，None 表示不设界。"""
    def _norm(v,label):
        v=str(v or '').strip()
        if not v: return None
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',v): raise HTTPException(400,f'{label} 日期格式应为 YYYY-MM-DD')
        try: date(int(v[:4]),int(v[5:7]),int(v[8:10]))
        except ValueError: raise HTTPException(400,f'{label} 不是有效日期')
        return v
    lo=_norm(date_from,'开始'); hi=_norm(date_to,'结束')
    if lo and hi and lo>hi: raise HTTPException(400,'开始日期不能晚于结束日期')
    return lo,hi

@app.get('/api/club/{club_id}/mall/orders')
def club_orders(club_id:int,date_from:str|None=None,date_to:str|None=None):
    # 带上买家与商品明细：俱乐部端点开订单要能看到「卖了哪几件、各多少」，
    # 只回一行 total 的话「商城订单详情」只能是空壳。
    # date_from/date_to（YYYY-MM-DD，本地日期）：DB 时间戳是 UTC（SQLite CURRENT_TIMESTAMP
    # 恒为 UTC，线上服务器还是 GMT），用户按本地日期筛，先 +8 小时再比。
    lo,hi=_date_range(date_from,date_to)
    with conn() as c:
        sql='''SELECT o.id,o.total,o.status,o.tracking_no,o.carrier,o.club_commission,o.after_sales_status,o.created_at,o.shipping_consignee,o.shipping_phone,o.shipping_address,u.name buyer
                 FROM gear_orders o JOIN users u ON u.id=o.user_id WHERE o.source_club_id=?'''
        args=[club_id]
        if lo: sql+=" AND date(o.created_at,'+8 hours')>=?"; args.append(lo)
        if hi: sql+=" AND date(o.created_at,'+8 hours')<=?"; args.append(hi)
        sql+=' ORDER BY o.id DESC'
        orders=rows(c.execute(sql,args))
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
        summary['months']=commission_engine.monthly(c,club_id=club_id)
        # 当前生效佣金率（2026-10-24）：佣金率由总平台按俱乐部设定，俱乐部只读。
        # 俱乐部必须知道自己拿多少比例 —— 结算单上写「佣金 ¥X」但不知道比例是多少，
        # 对不上账时会怀疑平台算错。费率在成交时快照，改费率不追溯历史订单。
        _mode=c.execute("SELECT commission_rate,COUNT(*) n FROM products GROUP BY commission_rate ORDER BY n DESC LIMIT 1").fetchone()
        _prod_rate=float(_mode['commission_rate']) if _mode else 0.08
        _rate=commission_rate_service.resolve(c,club_id,_prod_rate)
        summary['commissionRate']=round(_rate.rate,6)
        summary['commissionRatePercent']=round(_rate.rate*100,2)
        summary['commissionRateSource']=_rate.source
        summary['commissionRateReason']=_rate.reason
        # 参考「推广费结算」模式：把起结门槛与「现在能不能申请」一并下发，
        # 前端据此渲染申请按钮的可点性，而不是让俱乐部点了才吃 400。
        threshold=float(summary['policy'].get('minSettlement',100) or 0)
        summary['threshold']=round(threshold,2)
        acct=row(c.execute("SELECT id FROM club_settlement_accounts WHERE club_id=? AND status='active'",(club_id,)))
        summary['accountExists']=bool(acct)
        if summary['payableNow']>=threshold and summary['payableNow']>0:
            summary['canRequest']=True; summary['blockReason']=''
        elif not acct and summary['payableNow']>0:
            summary['canRequest']=False; summary['blockReason']='请先登记结算账户（法人卡/对公账户）'
        elif summary['payableNow']<=0:
            summary['canRequest']=False; summary['blockReason']='暂无可申请佣金：订单签收并过售后期后自动转入'
        else:
            summary['canRequest']=False; summary['blockReason']=f'未满 ¥{threshold:g} 起结门槛，还差 ¥{threshold-summary["payableNow"]:,.2f}'
        return summary

@app.get('/api/club/{club_id}/mall/commissions')
def club_commission_ledger(club_id:int,status:str|None=None,date_from:str|None=None,date_to:str|None=None):
    club_or_404(club_id)
    lo,hi=_date_range(date_from,date_to)
    with conn() as c:
        commission_engine.release_matured(c,club_id=club_id)
        sql='''SELECT l.*,o.status order_status,o.refund_status,o.delivered_at
               FROM commission_ledger l JOIN gear_orders o ON o.id=l.order_id WHERE l.club_id=?'''
        args=[club_id]
        if status:
            sql+=' AND l.status=?'; args.append(status)
        if lo: sql+=" AND date(l.created_at,'+8 hours')>=?"; args.append(lo)
        if hi: sql+=" AND date(l.created_at,'+8 hours')<=?"; args.append(hi)
        sql+=' ORDER BY l.id DESC'
        return rows(c.execute(sql,args))

@app.get('/api/club/{club_id}/mall/settlements')
def club_commission_settlements(club_id:int):
    club_or_404(club_id)
    with conn() as c:return commission_engine.settlements(c,club_id=club_id)

@app.post('/api/club/{club_id}/mall/settlement-requests')
def club_request_settlement(club_id:int,payload:dict=Body(default={})):
    """俱乐部主动申请佣金结算（参考「保险推广费结算」模式，2026-10-24）。

    门槛/账户/流水三道闸都在引擎里：可申请净额须达到起结门槛（默认 ¥100）、
    必须已登记结算账户、且当前没有进行中的申请。申请成功后对应佣金流水
    进入 reserved（预留在该结算单上），平台核对账户打款（confirm_payout）后转已结算。
    """
    club=club_or_404(club_id)
    if club.get('status')!='active': raise HTTPException(403,'俱乐部当前未启用，不能申请结算')
    with conn() as c:
        try:return commission_engine.request_settlement(c,club_id=club_id,note=str(payload.get('note') or ''))
        except ValueError as e: raise HTTPException(409,str(e))

# ── 佣金结算账户（法人卡 / 对公账户，2026-10-22 用户反馈）────────────────────
# 平台给俱乐部结算佣金的前置条件：没提供账户就不能结算。一个俱乐部一条 active
# 账户（club_id UNIQUE），可改不可删——打款凭据要留痕。下发一律脱敏。
_SETTLEMENT_ACCOUNT_TYPES={'legal':'法人银行卡','corporate':'对公账户'}

def _mask_card(no:str)->str:
    no=str(no or '')
    return ('*'*(len(no)-4)+no[-4:]) if len(no)>4 else no

def _mask_id(no:str)->str:
    no=str(no or '')
    return (no[:6]+'*'*8+no[-4:]) if len(no)>=14 else '*'*len(no)

def _settlement_account_public(a:dict)->dict:
    return {'exists':True,'accountType':a['account_type'],'accountTypeCn':_SETTLEMENT_ACCOUNT_TYPES.get(a['account_type'],a['account_type']),
            'holder':a['holder'],'legalName':a['legal_name'],'legalIdNoMasked':_mask_id(a['legal_id_no']),
            'bankName':a['bank_name'],'bankBranch':a.get('bank_branch') or '','cardNoMasked':_mask_card(a['card_no']),
            'note':a.get('note') or '','updatedAt':a.get('updated_at') or ''}

@app.get('/api/club/{club_id}/settlement-account')
def club_get_settlement_account(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        a=row(c.execute("SELECT * FROM club_settlement_accounts WHERE club_id=? AND status='active'",(club_id,)))
        return _settlement_account_public(a) if a else {'exists':False}

@app.post('/api/club/{club_id}/settlement-account')
def club_save_settlement_account(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    at=str(payload.get('accountType') or '').strip()
    if at not in _SETTLEMENT_ACCOUNT_TYPES: raise HTTPException(400,'账户类型只能是「法人银行卡」或「对公账户」')
    def _txt(key,label,mn,mx):
        v=str(payload.get(key) or '').strip()
        if not (mn<=len(v)<=mx): raise HTTPException(400,f'{label}长度应在 {mn}-{mx} 个字符')
        return v
    holder=_txt('holder','户名',2,60)
    legal_name=_txt('legalName','法人姓名',2,30)
    bank_name=_txt('bankName','开户行',2,60)
    bank_branch=str(payload.get('bankBranch') or '').strip()[:60]
    note=str(payload.get('note') or '').strip()[:200]
    # 卡号/身份证：GET 只回脱敏值，前端修改时这两格留空＝保留原值（防止把 ******** 存回库）。
    with conn() as c:
        existing=row(c.execute("SELECT * FROM club_settlement_accounts WHERE club_id=? AND status='active'",(club_id,)))
    legal_id=str(payload.get('legalIdNo') or '').strip().upper()
    if not legal_id:
        if not existing: raise HTTPException(400,'法人身份证号应为 18 位（末位可为 X）')
        legal_id=existing['legal_id_no']
    elif not re.fullmatch(r'\d{17}[\dX]',legal_id): raise HTTPException(400,'法人身份证号应为 18 位（末位可为 X）')
    card_no=re.sub(r'\s+','',str(payload.get('cardNo') or ''))
    if not card_no:
        if not existing: raise HTTPException(400,'卡号/账号应为 10-25 位数字')
        card_no=existing['card_no']
    elif not re.fullmatch(r'\d{10,25}',card_no): raise HTTPException(400,'卡号/账号应为 10-25 位数字')
    with conn() as c:
        c.execute('''INSERT INTO club_settlement_accounts(club_id,account_type,holder,legal_name,legal_id_no,bank_name,bank_branch,card_no,note)
                     VALUES(?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(club_id) DO UPDATE SET account_type=excluded.account_type,holder=excluded.holder,
                       legal_name=excluded.legal_name,legal_id_no=excluded.legal_id_no,bank_name=excluded.bank_name,
                       bank_branch=excluded.bank_branch,card_no=excluded.card_no,note=excluded.note,status='active',
                       updated_at=CURRENT_TIMESTAMP''',(club_id,at,holder,legal_name,legal_id,bank_name,bank_branch,card_no,note))
        a=row(c.execute("SELECT * FROM club_settlement_accounts WHERE club_id=? AND status='active'",(club_id,)))
    return _settlement_account_public(a)

@app.get('/api/platform/settlement-accounts')
def platform_settlement_accounts(club_id:int|None=None):
    """平台侧查看各俱乐部结算账户（脱敏）；不带 club_id 时返回全部俱乐部（含未提供的）。"""
    with conn() as c:
        sql='''SELECT cl.id club_id,cl.name club_name,a.* FROM clubs cl
               LEFT JOIN club_settlement_accounts a ON a.club_id=cl.id AND a.status='active' WHERE 1=1'''
        args=[]
        if club_id is not None: sql+=' AND cl.id=?'; args.append(club_id)
        out=[]
        for r in rows(c.execute(sql+' ORDER BY cl.id',args)):
            item={'clubId':r['club_id'],'clubName':r['club_name'],'exists':bool(r.get('id'))}
            if r.get('id'): item['account']=_settlement_account_public(r)
            out.append(item)
        return out


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
    # 经营业态开关（首页/活动/营地/业务介绍）：C 端 tab 显隐 + 后台「营地经营」栏目显隐的单一数据源。
    out.pop('module_switches_json',None)
    out['module_switches']=module_switches_public(x)
    # 首页运营轮播：未配置或关闭 → None，C 端回落默认活动轮播（不破坏旧行为）。
    # 图片同样在这里换成公开代理地址。
    out.pop('home_banners_json',None)
    out['home_banners']=club_home.public_json(club_id,x)
    # 演示环境走的是 dict(x) 全量下发，内部列要显式摘掉（生产走白名单，天然不含）
    out.pop('c_profile_json',None)
    out['biz_section']=club_biz.public_json(club_id,x)
    # 我的页文案（联系我们 / 关于）：只下发有值的字段，生产/演示同一套白名单，
    # 避免「演示环境能显示、线上 401/缺字段」这种环境差异bug。
    out['profile']=c_profile.public_view(x)
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

@app.post('/api/club/{club_id}/biz-section/image')
async def upload_club_biz_image(club_id:int,file:UploadFile=File(...)):
    """上传业务介绍配图（C 端「户外能力」图文卡的图）。

    - 落 `uploads/{club}/biz/{uuid}{ext}`，**原样存盘不重编码**：这一屏的图就是拿来当
      正文看的，用户明确要传长图（海报/价目表），重压缩会改尺寸、掉画质。
    - 返回两个地址：`url` 是落库形状（`/static/uploads/...`，club_biz._image 只认它），
      `preview` 是俱乐部域代理（生产环境 /static/uploads/* 被 security_v025 直接 404，
      编辑弹窗里拿裸链预览等于一张破图 —— 与领队头像代理同一个理由）。
    """
    club_or_404(club_id)
    real=Path(file.filename or '').suffix.lower()
    if real not in _PUBLIC_IMAGE_EXT: raise HTTPException(400,'仅支持图片文件：png/jpg/jpeg/webp/gif')
    data=await file.read()
    if len(data) > 10*1024*1024: raise HTTPException(413,'图片过大（上限 10MB）')
    # 内容校验：PIL 打不开的一律当坏文件拒掉，别把垃圾写进 uploads 再让 C 端破图。
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
    except Exception:
        raise HTTPException(400,'图片文件损坏或格式不受支持')
    dest=UPLOAD/str(club_id)/'biz'
    dest.mkdir(parents=True, exist_ok=True)
    name=uuid.uuid4().hex+real
    (dest/name).write_bytes(data)
    url=f'/static/uploads/{club_id}/biz/{name}'
    preview=f'/api/club/{club_id}/biz-media/uploads/{club_id}/biz/{name}'
    return {'url':url,'preview':preview,'bytes':len(data)}

@app.get('/api/club/{club_id}/biz-media/{asset_path:path}')
def club_biz_media(club_id:int,asset_path:str):
    """俱乐部域业务配图代理：编辑弹窗里即时预览刚上传的图（带登录态）。

    作用域只有 `uploads/{club}/biz/` 一个目录（里面只会有本接口上传的业务配图），
    加上 _safe_media_path 的 `..` 拦截，双重收口 —— 不是 uploads 通配代理。
    """
    club_or_404(club_id)
    rel=unquote(asset_path)
    if not rel.startswith(f'uploads/{club_id}/biz/'):
        raise HTTPException(404,'not found')
    file_path=_safe_media_path('/static/'+rel)
    if not file_path or not file_path.is_file():raise HTTPException(404,'not found')
    return FileResponse(file_path)

@app.get('/api/club/{club_id}/home-banners')
def get_club_home_banners(club_id:int):
    return club_home.from_club(club_or_404(club_id))

@app.patch('/api/club/{club_id}/home-banners')
def update_club_home_banners(club_id:int,payload:dict=Body(...)):
    x=club_or_404(club_id)
    try: cfg=club_home.normalize(payload,club_home.from_club(x))
    except ValueError as e: raise HTTPException(400,str(e))
    with conn() as c:
        c.execute('UPDATE clubs SET home_banners_json=? WHERE id=?',(club_home.dumps(cfg),club_id))
    return cfg

@app.get('/api/public/clubs/{club_id}/home-media/{asset_path:path}')
def public_club_home_media(club_id:int,asset_path:str):
    # 只放行「首页轮播配置里真实引用过」的文件，不是对 /static/uploads 通配开放。
    x=club_or_404(club_id)
    if IS_PROD and x['status']!='active':raise HTTPException(404,'not found')
    original='/static/'+unquote(asset_path)
    if original not in club_home.media_paths(club_home.from_club(x)):raise HTTPException(404,'not found')
    file_path=_safe_media_path(original)
    if not file_path or not file_path.is_file():raise HTTPException(404,'not found')
    response=FileResponse(file_path)
    response.headers['Cache-Control']='public, max-age=300'
    return response

@app.get('/api/public/clubs/{club_id}/home-banners')
def public_club_home_banners(club_id:int):
    if IS_PROD and club_or_404(club_id)['status']!='active':raise HTTPException(404,'not found')
    return club_home.public_view(club_id,club_or_404(club_id)) or {}

@app.post('/api/club/{club_id}/home-banners/image')
async def upload_club_home_image(club_id:int,file:UploadFile=File(...)):
    """上传首页轮播配图（C 端首页运营 banner）。

    - 落 `uploads/{club}/home/{uuid}{ext}`，**原样存盘不重编码**。
    - 返回两个地址：`url` 是落库形状（`/static/uploads/...`，club_home._image 只认它），
      `preview` 是俱乐部域代理（生产环境 /static/uploads/* 被 security_v025 直接 404）。
    """
    club_or_404(club_id)
    real=Path(file.filename or '').suffix.lower()
    if real not in _PUBLIC_IMAGE_EXT: raise HTTPException(400,'仅支持图片文件：png/jpg/jpeg/webp/gif')
    data=await file.read()
    if len(data) > 10*1024*1024: raise HTTPException(413,'图片过大（上限 10MB）')
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
    except Exception:
        raise HTTPException(400,'图片文件损坏或格式不受支持')
    dest=UPLOAD/str(club_id)/'home'
    dest.mkdir(parents=True, exist_ok=True)
    name=uuid.uuid4().hex+real
    (dest/name).write_bytes(data)
    url=f'/static/uploads/{club_id}/home/{name}'
    preview=f'/api/club/{club_id}/home-media/uploads/{club_id}/home/{name}'
    return {'url':url,'preview':preview,'bytes':len(data)}

@app.get('/api/club/{club_id}/home-media/{asset_path:path}')
def club_home_media(club_id:int,asset_path:str):
    """俱乐部域首页轮播配图代理：编辑弹窗里即时预览刚上传的图（带登录态）。

    作用域只有 `uploads/{club}/home/` 一个目录，加上 _safe_media_path 的 `..` 拦截，双重收口。
    """
    club_or_404(club_id)
    rel=unquote(asset_path)
    if not rel.startswith(f'uploads/{club_id}/home/'):
        raise HTTPException(404,'not found')
    file_path=_safe_media_path('/static/'+rel)
    if not file_path or not file_path.is_file():raise HTTPException(404,'not found')
    return FileResponse(file_path)

@app.get('/api/public/clubs/{club_id}/activities')
def public_activities(club_id:int):
    if IS_PROD and club_or_404(club_id)['status']!='active':raise HTTPException(404,'not found')
    with conn() as c:
        acts=rows(c.execute('SELECT id,title,event_date,location,city,price,capacity,cover,activity_master_json FROM activities WHERE club_id=? AND status="published" ORDER BY id DESC',(club_id,)))
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
    # 活动分类（2026-11-06 后台自定义）：每场活动挂上所属类别 id 数组，
    # C 端磁贴点击后按它过滤（不再前端按标题正则猜）。
    if acts:
        ids=[int(x['id']) for x in acts]
        ph=','.join('?'*len(ids))
        cmap={}
        with conn() as c:
            for r in rows(c.execute(f'SELECT activity_id,category_id FROM activity_category_links WHERE activity_id IN ({ph})',ids)):
                cmap.setdefault(int(r['activity_id']),[]).append(int(r['category_id']))
        for a in acts: a['categoryIds']=cmap.get(int(a['id']),[])
    return acts


@app.get('/api/public/clubs/{club_id}/activity-categories')
def public_activity_categories(club_id:int):
    """C 端活动分类磁贴数据：仅启用项，附每类**已发布**活动数（0 场也下发，
    由前端决定禁用样式 —— 与旧自动分类「0 场禁用」口径一致）。"""
    if IS_PROD and club_or_404(club_id)['status']!='active':raise HTTPException(404,'not found')
    with conn() as c:
        return _activity_categories_payload(c,club_id,include_inactive=False,pub=True)


@app.get('/api/public/clubs/{club_id}/leaders')
def public_club_leaders(club_id:int):
    """C 端「领队团队专区」：列出资源库里全部 status='active' 的领队。

    与活动详情的「带队领队」（`_public_leaders`，只含**已经排到团期上**的人）不同，这里是
    俱乐部录入的**整本领队名册** —— 录入即显示，不需要逐个排班。这样顾客在 C 端能直接看到
    俱乐部的带队团队（大头像 · 头衔 · 擅长 · 常驻城市 · 年限 · ★评分），最贴近用户参考的
    同行版式，也解决了「明明录了领队、活动页却一片空白」的困惑。

    字段走白名单，与 `public_leader` / `_public_leaders` 同一条脱敏纪律：
    绝不下发手机号（`phone`）、内部备注（`note`）、带队执行备注。评分 / 评价数 / 带队场次
    全部来自真实记录（`_leader_stats`），一条都没有时前端印「暂无评分」，不给假分。
    """
    if IS_PROD and club_or_404(club_id)['status']!='active':raise HTTPException(404,'not found')
    with conn() as c:
        rs=rows(c.execute('SELECT id,name,role,specialties,base_city,avatar_url,bio,years_exp,cert_verified '
                          'FROM club_leaders WHERE club_id=? AND status="active" ORDER BY id',(club_id,)))
        out=[]
        for r in rs:
            specs=jload(r.get('specialties'),[])
            lid=int(r['id'])
            out.append({
                'leaderId':lid,
                'name':str(r.get('name') or '领队').strip() or '领队',
                'role':str(r.get('role') or '').strip() or '领队',
                'credential':str(r.get('role') or '').strip() or None,
                'specialties':[str(x).strip() for x in specs if str(x).strip()] if isinstance(specs,list) else [],
                'baseCity':str(r.get('base_city') or '').strip() or None,
                'avatarUrl':_public_leader_avatar_url(club_id,lid,r.get('avatar_url')),
                'bio':str(r.get('bio') or '').strip() or None,
                'yearsExp':int(r.get('years_exp') or 0),
                'certVerified':bool(r.get('cert_verified'))})
        stats=_leader_stats(c,club_id,[x['leaderId'] for x in out])
    for x in out:
        s=stats.get(int(x['leaderId'])) or {}
        x['ratingAvg']=s.get('ratingAvg')
        x['ratingCount']=int(s.get('ratingCount') or 0)
        x['leadCount']=int(s.get('leadCount') or 0)
    return out


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
                                      l.role AS credential,l.specialties,l.base_city,l.avatar_url,
                                      l.bio,l.years_exp,l.cert_verified
                               FROM occurrence_leaders ol
                               JOIN activity_occurrences o ON o.id=ol.occurrence_id
                               LEFT JOIN club_leaders l ON l.id=ol.leader_id
                               WHERE o.activity_id=? AND ol.club_id=? AND o.status="open"
                               ORDER BY o.start_at,ol.id''',(activity_id,club_id))):
        name=str(r.get('name') or '').strip() or '领队'
        lid=int(r['leader_id']) if r.get('leader_id') else 0
        key=(lid,name)
        if key in seen:continue
        seen.add(key)
        specs=jload(r.get('specialties'),[])
        out.append({
            # leaderId 是「点开领队主页」的唯一钥匙（0 = 执行端手打的临时增援，没有档案，
            # 前端就不给点击，也不给评分入口 —— 评一个查不到的人没有意义）。
            'leaderId':lid,
            'name':name,
            'role':str(r.get('role') or '').strip() or '领队',
            'credential':str(r.get('credential') or '').strip() or None,
            'specialties':[str(x).strip() for x in specs if str(x).strip()] if isinstance(specs,list) else [],
            'baseCity':str(r.get('base_city') or '').strip() or None,
            'avatarUrl':_public_leader_avatar_url(club_id,r.get('leader_id'),r.get('avatar_url')),
            'bio':str(r.get('bio') or '').strip() or None,
            'yearsExp':int(r.get('years_exp') or 0),
            'certVerified':bool(r.get('cert_verified'))})
    # 评分 / 评价数 / 带队场次：全部来自真实数据（顾客评的 + 排班记录），一条都没有时
    # 前端印「暂无评分」而不是给个漂亮的假分。
    stats=_leader_stats(c,club_id,[x['leaderId'] for x in out])
    for x in out:
        s=stats.get(int(x['leaderId'] or 0)) or {}
        x['ratingAvg']=s.get('ratingAvg')
        x['ratingCount']=int(s.get('ratingCount') or 0)
        x['leadCount']=int(s.get('leadCount') or 0)
    return out


def _leader_stats(c,club_id:int,leader_ids=None)->dict:
    """领队口碑与带队量：{leader_id: {ratingAvg, ratingCount, leadCount}}。

    三个数都必须来自真实记录：ratingAvg 是顾客评分的算术平均（没有评价就是 None，
    前端印「暂无评分」），leadCount 是排班表里去重后的团期数。
    绝不按「加入名册多久」推算年限、也不给新领队垫一个 5.0 的初始分。
    """
    ids=[int(i) for i in (leader_ids or []) if int(i or 0)]
    if leader_ids is not None and not ids: return {}
    where=''
    params=[club_id]
    if ids:
        where=' AND leader_id IN (%s)'%','.join('?'*len(ids))
        params+=ids
    out={}
    for i in ids: out[i]={'ratingAvg':None,'ratingCount':0,'leadCount':0}
    # 无 GROUP BY 的聚合在零行时仍返回一行 leader_id=NULL 的幻影行，必须跳过。
    for r in rows(c.execute('SELECT leader_id,AVG(rating) AS avg_rating,COUNT(*) AS cnt '
                            'FROM leader_reviews WHERE club_id=? AND status="visible"'+where,tuple(params))):
        if r['leader_id'] is None: continue
        lid=int(r['leader_id'])
        out.setdefault(lid,{'ratingAvg':None,'ratingCount':0,'leadCount':0})
        out[lid]['ratingAvg']=round(float(r['avg_rating'] or 0),2)
        out[lid]['ratingCount']=int(r['cnt'] or 0)
    for r in rows(c.execute('SELECT leader_id,COUNT(DISTINCT occurrence_id) AS cnt FROM occurrence_leaders '
                            'WHERE club_id=? AND leader_id IS NOT NULL'+where,tuple(params))):
        if r['leader_id'] is None: continue
        lid=int(r['leader_id'])
        out.setdefault(lid,{'ratingAvg':None,'ratingCount':0,'leadCount':0})
        out[lid]['leadCount']=int(r['cnt'] or 0)
    return out


def _public_trip_info(c,order:dict)->dict|None:
    """C 端订单卡里的「出行指引」：集合信息 + 带队人 + 已发送通知 + 本人分车。

    顾客出发前要问的几件事都在这答：几点在哪集合、临时电话打哪个、这团谁带、
    我在几号车、俱乐部发了什么新通知。数据全部来自团期执行表
    （occurrence_execution_settings / occurrence_leaders / activity_notices /
    execution_groups），俱乐部没填的项就不出现 —— 不拿默认值凑一个看起来很满的卡片。

    两条边界（与 _public_leaders 同一条纪律）：
    1. 通知只下发 status='sent' 的。草稿是俱乐部还在改的内容，顾客看到半成品比看不到更糟；
    2. 领队手机号不下发 —— 那是俱乐部内部联络方式，应急电话才是给顾客的那个。
    给领队的执行备注（leader_note）同样不下发：那是写给内部人的。
    """
    occ_id=order.get('occurrence_id')
    if not occ_id: return None
    st=row(c.execute('SELECT * FROM occurrence_execution_settings WHERE occurrence_id=?',(occ_id,))) or {}
    notices=[{'id':int(n['id']),'title':str(n['title'] or ''),'content':str(n['content'] or ''),'sentAt':n.get('sent_at')}
             for n in rows(c.execute("SELECT id,title,content,sent_at FROM activity_notices WHERE occurrence_id=? AND status='sent' ORDER BY id",(occ_id,)))]
    # 带队人带 leader_id 与公开头像地址：C 端「我的报名」的出行指引要点开头像进领队主页
    # 再咨询（2026-10-29 用户反馈「领队功能还是没实现，要显示头像、还能咨询领队」）。
    # 手机号依旧不下发 —— 那是俱乐部内部联络方式，给顾客的是应急电话。
    leaders=[]
    for _tl in rows(c.execute('''SELECT ol.leader_id,ol.name,ol.role,ol.club_id,l.avatar_url
                                 FROM occurrence_leaders ol
                                 LEFT JOIN club_leaders l ON l.id=ol.leader_id
                                 WHERE ol.occurrence_id=? ORDER BY ol.id''',(occ_id,))):
        leaders.append({
            'name':str(_tl['name'] or '').strip() or '领队',
            'role':str(_tl.get('role') or '').strip() or '领队',
            'leaderId':int(_tl['leader_id']) if _tl.get('leader_id') else 0,
            'avatarUrl':_public_leader_avatar_url(int(_tl.get('club_id') or 0),_tl.get('leader_id'),_tl.get('avatar_url')),
        })
    vehicles={int(r['participant_id']):str(r['name'] or '') for r in rows(c.execute('''SELECT pa.participant_id,g.name FROM participant_group_assignments pa
        JOIN execution_groups g ON g.id=pa.group_id WHERE pa.occurrence_id=? AND pa.group_type='vehicle' ''',(occ_id,))) if r.get('name')}
    trip={}
    if str(st.get('meeting_time') or '').strip() or str(st.get('meeting_location') or '').strip():
        trip['meeting']={'time':str(st.get('meeting_time') or '').strip() or None,
                         'location':str(st.get('meeting_location') or '').strip() or None}
    if str(st.get('emergency_phone') or '').strip():
        trip['emergencyPhone']=str(st['emergency_phone']).strip()
    if leaders: trip['leaders']=leaders
    if notices: trip['notices']=notices
    if vehicles: trip['vehicles']={str(k):v for k,v in vehicles.items()}
    return trip or None


def _public_execution(c,occurrence_id:int,club_id:int)->dict:
    """C 端活动详情页「出行指引」：集合/应急 + 已发送通知 + 分组（按团期维度）。

    与订单出行指引(_public_trip_info)同源，但面向**还没报名、正在看活动**的顾客：
    - 通知只下发 status='sent'（草稿是俱乐部在改的半成品，顾客不该看到）；
    - 领队手机号/内部备注不下发（与 _public_leaders 同纪律）；
    - 列出本团期所有车/组（让顾客知道「这次会怎么编队」），本人具体分到哪台车
      在订单出行指引里看（那是带 participant 维度的事）。
    俱乐部没填的项就不出现 —— 不拿默认值凑一个看着很满的卡片。
    """
    st=row(c.execute('SELECT * FROM occurrence_execution_settings WHERE occurrence_id=?',(occurrence_id,))) or {}
    notices=[{'id':int(n['id']),'title':str(n['title'] or ''),'content':str(n['content'] or ''),'sentAt':n.get('sent_at')}
             for n in rows(c.execute("SELECT id,title,content,sent_at FROM activity_notices WHERE occurrence_id=? AND status='sent' ORDER BY id",(occurrence_id,)))]
    # assigned_count 不是真列，是 list_groups 里的相关子查询算出来的；这里照抄同一套算法，
    # 直接 SELECT assigned_count 会在运行期报「no such column」把整个详情接口打挂。
    groups=[{'name':str(g['name'] or ''),'groupType':str(g.get('group_type') or ''),
             'capacity':g.get('capacity'),'assignedCount':int(g.get('assigned_count') or 0),
             'leaderName':str(g.get('leader_name') or '')}
            for g in rows(c.execute('''SELECT g.name,g.group_type,g.capacity,g.leader_name,
                        (SELECT COUNT(*) FROM participant_group_assignments a WHERE a.group_id=g.id) assigned_count
                        FROM execution_groups g WHERE g.occurrence_id=? ORDER BY g.group_type,g.id''',(occurrence_id,)))]
    trip={}
    if str(st.get('meeting_time') or '').strip() or str(st.get('meeting_location') or '').strip():
        trip['meeting']={'time':str(st.get('meeting_time') or '').strip() or None,
                         'location':str(st.get('meeting_location') or '').strip() or None}
    if str(st.get('emergency_phone') or '').strip():
        trip['emergencyPhone']=str(st['emergency_phone']).strip()
    if notices: trip['notices']=notices
    if groups: trip['groups']=groups
    return trip


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


_RATER_KEY_RE=re.compile(r'^[A-Za-z0-9:_\-]{6,64}$')


def _public_cover_url(activity_id:int,cover)->str|None:
    """活动封面在 C 端必须走活动域代理（生产环境 /static/uploads 被封死）。"""
    cov=str(cover or '')
    if not cov: return None
    if cov.startswith('/static/'):
        return '/api/public/activities/%d/media/%s'%(int(activity_id),quote(cov[len('/static/'):],safe='/'))
    return cov


def _leader_or_404(c,leader_id:int)->dict:
    r=row(c.execute('SELECT * FROM club_leaders WHERE id=?',(leader_id,)))
    if not r: raise HTTPException(404,'领队不存在')
    club_id=int(r['club_id'])
    if IS_PROD and club_or_404(club_id)['status']!='active': raise HTTPException(404,'not found')
    # 停用的领队不对外开主页，除非他还挂在某个已发布活动的团期上（那就得能点开，
    # 否则活动详情里那张卡会变成死链）。这样既不开「遍历 id 看全站领队」的口子，
    # 也不会让正在带队的卡点不动。
    if str(r.get('status') or 'active')!='active':
        alive=c.execute('''SELECT 1 FROM occurrence_leaders ol
                           JOIN activity_occurrences o ON o.id=ol.occurrence_id
                           JOIN activities a ON a.id=o.activity_id
                           WHERE ol.leader_id=? AND ol.club_id=? AND a.status="published" LIMIT 1''',
                        (leader_id,club_id)).fetchone()
        if not alive: raise HTTPException(404,'领队不存在')
    return r


@app.get('/api/public/leaders/{leader_id}')
def public_leader(leader_id:int):
    """C 端领队主页：卡面点进去看到的那一屏。

    只下发顾客需要判断「这个人靠不靠谱」的信息：头衔/擅长/常驻/年限/简介/评分，
    TA 近期带的已发布活动（可跳回活动详情报名），以及最近 20 条评价。
    手机号、内部备注（note）、带队执行备注一律不下发 —— 与 _public_leaders 同一条纪律。
    """
    with conn() as c:
        r=_leader_or_404(c,leader_id)
        club_id=int(r['club_id'])
        stats=_leader_stats(c,club_id,[leader_id]).get(int(leader_id)) or {}
        reviews=[{'author':str(x.get('author') or '').strip() or '匿名山友',
                  'rating':int(x['rating'] or 0),
                  'comment':str(x.get('comment') or '').strip() or None,
                  'createdAt':x.get('created_at')}
                 for x in rows(c.execute('SELECT author,rating,comment,created_at FROM leader_reviews '
                                         'WHERE club_id=? AND leader_id=? AND status="visible" '
                                         'ORDER BY id DESC LIMIT 20',(club_id,leader_id)))]
        acts=[]
        for a in rows(c.execute('''SELECT a.id,a.title,a.location,a.event_date,a.cover,MIN(o.start_at) AS next_start
                                   FROM occurrence_leaders ol
                                   JOIN activity_occurrences o ON o.id=ol.occurrence_id AND o.status="open"
                                   JOIN activities a ON a.id=o.activity_id AND a.status="published"
                                   WHERE ol.club_id=? AND ol.leader_id=? AND o.start_at>=date("now")
                                   GROUP BY a.id ORDER BY next_start LIMIT 4''',(club_id,leader_id))):
            acts.append({'id':int(a['id']),'title':str(a['title'] or ''),'location':str(a.get('location') or ''),
                         'nextStart':a.get('next_start'),'cover':_public_cover_url(int(a['id']),a.get('cover'))})
        specs=jload(r.get('specialties'),[])
    return {'leaderId':int(leader_id),'name':str(r.get('name') or '领队'),
            'role':str(r.get('role') or '').strip() or '领队',
            'credential':str(r.get('role') or '').strip() or None,
            'specialties':[str(x).strip() for x in specs if str(x).strip()] if isinstance(specs,list) else [],
            'baseCity':str(r.get('base_city') or '').strip() or None,
            'avatarUrl':_public_leader_avatar_url(club_id,leader_id,r.get('avatar_url')),
            'bio':str(r.get('bio') or '').strip() or None,
            'yearsExp':int(r.get('years_exp') or 0),
            'certVerified':bool(r.get('cert_verified')),
            'ratingAvg':stats.get('ratingAvg'),'ratingCount':int(stats.get('ratingCount') or 0),
            'leadCount':int(stats.get('leadCount') or 0),
            'activities':acts,'reviews':reviews}


@app.post('/api/public/leaders/{leader_id}/reviews')
def public_leader_review(leader_id:int,payload:dict=Body(...)):
    """顾客给领队评分（1-5 星 + 一句话）。

    一人一评：同一个 rater_key 再提交 = 改自己的那条，不叠数（否则一个人能把分数刷上去）。
    评分不要求登录 —— C 端会员登录在有网关的线上环境本来就不稳定，逼人登录等于关掉这个功能；
    去重靠浏览器本地生成并带过来的 rater_key（会员登录时前端会换成 u:<userId>）。
    """
    try: rating=int(payload.get('rating') or 0)
    except (TypeError,ValueError): rating=0
    if not 1<=rating<=5: raise HTTPException(400,'请选择 1-5 星')
    comment=str(payload.get('comment') or '').strip()[:200] or None
    author=str(payload.get('author') or '').strip()[:20] or None
    key=str(payload.get('raterKey') or '').strip()
    if not _RATER_KEY_RE.match(key): raise HTTPException(400,'评价标识缺失，请刷新页面后重试')
    try: activity_id=int(payload.get('activityId') or 0) or None
    except (TypeError,ValueError): activity_id=None
    with conn() as c:
        r=_leader_or_404(c,leader_id)
        club_id=int(r['club_id'])
        # 关联活动只做归属校验，不做「必须参加过才能评」的硬门槛（到场与否系统未必知道，
        # 拦死会把真实评价挡在门外）；脏数据靠这条 club_id 校验挡住。
        if activity_id and not c.execute('SELECT 1 FROM activities WHERE id=? AND club_id=?',
                                         (activity_id,club_id)).fetchone():
            activity_id=None
        ex=c.execute('SELECT id FROM leader_reviews WHERE club_id=? AND leader_id=? AND rater_key=?',
                     (club_id,leader_id,key)).fetchone()
        if ex:
            c.execute('UPDATE leader_reviews SET rating=?,comment=?,author=?,activity_id=COALESCE(?,activity_id),'
                      'updated_at=CURRENT_TIMESTAMP WHERE id=?',(rating,comment,author,activity_id,int(ex['id'])))
            updated=True
        else:
            c.execute('INSERT INTO leader_reviews(club_id,leader_id,activity_id,rater_key,author,rating,comment) '
                      'VALUES(?,?,?,?,?,?,?)',(club_id,leader_id,activity_id,key,author,rating,comment))
            updated=False
        stats=_leader_stats(c,club_id,[leader_id]).get(int(leader_id)) or {}
    return {'ok':True,'updated':updated,'ratingAvg':stats.get('ratingAvg'),
            'ratingCount':int(stats.get('ratingCount') or 0)}


@app.post('/api/public/leaders/{leader_id}/inquiries')
def public_leader_inquiry(leader_id:int,payload:dict=Body(...),background_tasks:BackgroundTasks=None):
    """「咨询领队」：顾客问的都是「这条线累不累 / 我能不能去 / 档期还有没有」这类具体问题。

    落到 leader_inquiries 里，俱乐部端「信息」统一收件箱能看到并标记已回复。
    这里不做自动客服 —— 没有真人回答的「已收到」比不回更让人失望。
    """
    phone=re.sub(r'[^0-9+]','',str(payload.get('phone') or ''))
    if len(phone)<6 or len(phone)>20: raise HTTPException(400,'请填写可联系上的手机号')
    message=str(payload.get('message') or '').strip()
    if len(message)<4: raise HTTPException(400,'把想咨询的问题写具体一点（至少 4 个字）')
    if len(message)>500: raise HTTPException(400,'咨询内容最多 500 字')
    name=str(payload.get('name') or '').strip()[:20] or None
    try: activity_id=int(payload.get('activityId') or 0) or None
    except (TypeError,ValueError): activity_id=None
    with conn() as c:
        r=_leader_or_404(c,leader_id)
        club_id=int(r['club_id'])
        if activity_id and not c.execute('SELECT 1 FROM activities WHERE id=? AND club_id=?',
                                         (activity_id,club_id)).fetchone():
            activity_id=None
        token=secrets.token_urlsafe(24)
        iq=c.execute('INSERT INTO leader_inquiries(club_id,leader_id,activity_id,name,phone,message,access_token) '
                     'VALUES(?,?,?,?,?,?,?)',(club_id,leader_id,activity_id,name,phone,message,token)).lastrowid
        c.execute('INSERT INTO inquiry_messages(club_id,kind,inquiry_id,sender,body) '
                  'VALUES(?,?,?,?,?)',(club_id,'leader',iq,'customer',message))
        webhook=_club_wecom_webhook(c,club_id)
        a=row(c.execute('SELECT title FROM activities WHERE id=?',(activity_id,))) if activity_id else None
    if webhook and background_tasks is not None:
        background_tasks.add_task(_push_inquiry_to_wecom, webhook, '领队',
            {'name':name,'phone':phone,'message':message,
             'leaderName':str(r.get('name') or ''),
             'activityTitle':str(a.get('title') or '') if a else '',
             'backendLink':'/?view=info'})
    return {'ok':True,'inquiryId':int(iq),'accessToken':token,
            'leader':str(r.get('name') or ''),'kind':'leader'}


@app.post('/api/public/activities/{activity_id}/inquiries')
def public_route_inquiry(activity_id:int,payload:dict=Body(...),background_tasks:BackgroundTasks=None):
    """「咨询线路」：顾客在 C 端活动详情点「咨询这条线路」留下的问题。

    落到 route_inquiries，进俱乐部端「信息」统一收件箱。同样不做自动客服。
    """
    phone=re.sub(r'[^0-9+]','',str(payload.get('phone') or ''))
    if len(phone)<6 or len(phone)>20: raise HTTPException(400,'请填写可联系上的手机号')
    message=str(payload.get('message') or '').strip()
    if len(message)<4: raise HTTPException(400,'把想咨询的问题写具体一点（至少 4 个字）')
    if len(message)>500: raise HTTPException(400,'咨询内容最多 500 字')
    name=str(payload.get('name') or '').strip()[:20] or None
    with conn() as c:
        a=row(c.execute('SELECT id,club_id,title FROM activities WHERE id=? AND status="published"',(activity_id,)))
        if not a: raise HTTPException(404,'活动不存在或未发布')
        club_id=int(a['club_id'])
        token=secrets.token_urlsafe(24)
        iq=c.execute('INSERT INTO route_inquiries(club_id,activity_id,name,phone,message,access_token) '
                     'VALUES(?,?,?,?,?,?)',(club_id,activity_id,name,phone,message,token)).lastrowid
        c.execute('INSERT INTO inquiry_messages(club_id,kind,inquiry_id,sender,body) '
                  'VALUES(?,?,?,?,?)',(club_id,'route',iq,'customer',message))
        webhook=_club_wecom_webhook(c,club_id)
    if webhook and background_tasks is not None:
        background_tasks.add_task(_push_inquiry_to_wecom, webhook, '线路',
            {'name':name,'phone':phone,'message':message,
             'activityTitle':str(a.get('title') or ''),'backendLink':'/?view=info'})
    return {'ok':True,'inquiryId':int(iq),'accessToken':token,'kind':'route'}


@app.post('/api/public/inquiries/unread')
def public_inquiries_unread(payload:dict=Body(...)):
    # C 端「有没有人回复我」的批量探测：一次请求查完本机所有会话。
    # 与 public_inquiry_thread 的关键区别：这里**绝不清除** unread_customer ——
    # 只有顾客真正点开对话（GET thread）才算已读，轮询因此不会把提醒吃掉。
    src = payload.get('items') if isinstance(payload, dict) else None
    if not isinstance(src, list): src = []
    out = []
    with conn() as c:
        for x in src[:50]:
            if not isinstance(x, dict): continue
            try:
                kind = str(x.get('kind') or '')
                iid = int(x.get('id') or 0)
                token = str(x.get('token') or '')
                if kind not in _INQUIRY_TABLES or iid <= 0 or not token: continue
                r = _inquiry_token_check(c, kind, iid, token)
                club_id = int(r['club_id'])
                msgs = _inquiry_messages(c, club_id, kind, iid)
                last = msgs[-1] if msgs else None
                l = row(c.execute('SELECT name FROM club_leaders WHERE id=?', (r.get('leader_id'),))) if r.get('leader_id') else None
                a = row(c.execute('SELECT title FROM activities WHERE id=?', (r.get('activity_id'),))) if r.get('activity_id') else None
                out.append({
                    'kind': kind, 'id': iid,
                    'unread': bool(int(r.get('unread_customer') or 0)) and bool(last and last['sender'] != 'customer'),
                    'replyCount': sum(1 for m in msgs if m['sender'] != 'customer'),
                    'lastSender': (last or {}).get('sender', ''),
                    'lastBody': str((last or {}).get('body') or '')[:120],
                    'lastAt': str((last or {}).get('createdAt') or ''),
                    'leaderName': str(l.get('name') or '') if l else '',
                    'activityTitle': str(a.get('title') or '') if a else '',
                })
            except (HTTPException, ValueError, TypeError, KeyError, AttributeError):
                continue
    return {'ok': True, 'items': out, 'unreadCount': sum(1 for x in out if x['unread'])}


@app.get('/api/public/inquiries/{kind}/{inquiry_id}')
def public_inquiry_thread(kind:str, inquiry_id:int, token:str=''):
    # C 端「我的咨询」凭 access_token 拉自己的往来消息；读到即清未读
    with conn() as c:
        r=_inquiry_token_check(c,kind,inquiry_id,token)
        club_id=int(r['club_id'])
        msgs=_inquiry_messages(c,club_id,kind,inquiry_id)
        a=row(c.execute('SELECT title FROM activities WHERE id=?',(r.get('activity_id'),))) if r.get('activity_id') else None
        l=row(c.execute('SELECT name FROM club_leaders WHERE id=?',(r.get('leader_id'),))) if r.get('leader_id') else None
        c.execute(f'UPDATE {_inquiry_table(kind)} SET unread_customer=0 WHERE id=? AND club_id=?',
                  (inquiry_id,club_id))
    return {
        'ok':True,'id':int(r['id']),'kind':kind,
        'clubId':club_id,'name':str(r.get('name') or '匿名山友'),
        'phone':str(r.get('phone') or ''),
        'activityTitle':str(a.get('title') or '') if a else '',
        'leaderName':str(l.get('name') or '') if l else '',
        'status':str(r.get('status') or 'new'),
        'createdAt':r.get('created_at'),'lastMessageAt':r.get('last_message_at'),
        'messages':msgs,'replyCount':sum(1 for m in msgs if m['sender']!='customer'),
    }

@app.post('/api/public/inquiries/{kind}/{inquiry_id}/messages')
def public_inquiry_followup(kind:str, inquiry_id:int, payload:dict=Body(...),
                            token:str='', background_tasks:BackgroundTasks=None):
    # 顾客在同一条咨询下追问 → status 回 new，领队端/后台红点重现，企微可继续通知
    body=str(payload.get('message') or '').strip()
    if len(body)<2: raise HTTPException(400,'把追问写具体一点')
    if len(body)>500: raise HTTPException(400,'追问最多 500 字')
    with conn() as c:
        r=_inquiry_token_check(c,kind,inquiry_id,token)
        club_id=int(r['club_id'])
        mid,now=_append_inquiry_message(c,club_id,kind,inquiry_id,'customer',body)
        msgs=_inquiry_messages(c,club_id,kind,inquiry_id)
        webhook=_club_wecom_webhook(c,club_id)
    if webhook and background_tasks is not None:
        background_tasks.add_task(_push_inquiry_to_wecom, webhook, '顾客追问',
            {'name':r.get('name'),'phone':r.get('phone'),'message':body,
             'backendLink':'/?view=info'})
    return {'ok':True,'messageId':mid,'createdAt':now,'messages':msgs}

@app.get('/api/public/activities/{activity_id}')
def public_activity(activity_id:int, user_id:int|None=None):
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
    with conn() as _gc:a['gearRecommendations']=_gear_plan_for(_gc,a['activityMaster'],int(a['club_id']),user_id=user_id,public_view=True)
    # 带队领队：顾客挑团期时最想知道「谁带」，俱乐部端排好班之后这里必须看得见
    with conn() as _lc:a['leaders']=_public_leaders(_lc,activity_id,int(a['club_id']))
    # 出行指引（按团期）：集合/应急 + 已发送通知 + 分组 —— 后台执行页填的内容同步到 C 端活动页。
    # 与订单出行指引(_public_trip_info)同源，但面向「还没报名、正在看活动」的顾客（不含本人分车）。
    with conn() as _ec:
        for _o in (a.get('occurrences') or []):
            _oid=int(_o.get('id') or 0)
            if _oid: _o['execution']=_public_execution(_ec,_oid,int(a['club_id']))
    cov=a.get('cover')
    if cov and str(cov).startswith('/static/'):
        a['cover']='/api/public/activities/%d/media/%s'%(activity_id,quote(str(cov)[len('/static/'):],safe='/'))
    with conn() as _gpc:
        _gp=gear_points_policy.resolve(_gpc,int(a.get('club_id') or 0))
    a['pointsPolicy']=activity_points_policy.from_activity(a,_gp).as_dict()
    a['refundPolicy']=activity_refund_policy.from_activity(a).as_dict()
    a['participantPolicy']=participant_service.from_activity(a).as_dict()
    # 活动团期「保障」提示：专业户外运动保险保额（俱乐部后台可配，单位：万），C 端展示「价值 X 万」。
    with conn() as _ic:
        _club=row(_ic.execute('SELECT insurance_coverage_wan FROM clubs WHERE id=?',(int(a.get('club_id') or 0),)))
    a['insuranceCoverageWan']=float((_club or {}).get('insurance_coverage_wan') or 0)
    # 品牌 logo / 字标 / 空白底图属俱乐部内部素材；无论 PROD 还是 demo，C 端公开接口一律不曝光。
    # 仅放行 kind='photo' 的真实照片（kind 缺失的按照片处理，避免误删）。
    _m=a.get('activityMaster')
    if isinstance(_m,dict) and isinstance(_m.get('media'),list):
        _m['media']=[m for m in _m['media']
                     if isinstance(m,dict) and str(m.get('kind','')).lower() not in ('logo',)]
    # ★ 版本戳（2026-10-10 用户反馈「后台重新生成一版，C 端还是旧版」）：C 端页面常在手里开着，
    #   靠这两个字段就能自查「后台已经换了新的一版」，不用逼顾客懂什么叫强刷。
    #   detailVersionId 随每次「重新生成 / 换一版」变；updatedAt 换版与重新发布都会变。
    a['detailVersionId']=int(a.get('detail_version_id') or 0)
    a['updatedAt']=a.get('updated_at')
    if IS_PROD:
        # Public activity is not a dump of private activity_master_json (internalData).
        a={k:v for k,v in a.items() if k in ('id','club_id','title','event_date','location','price','capacity','status','cover','activityMaster','detail','occurrences','pointsPolicy','refundPolicy','participantPolicy','gearRecommendations','leaders','detailVersionId','updatedAt','insuranceCoverageWan')}
        # 领队白名单：与 _public_leaders 同一条脱敏纪律（手机号/内部备注绝不下发），
        # 但展示所需的字段要全部放行，否则前端卡片会变成「静态不可点」—— leaderId 没了就打不开
        # 领队主页、years_exp/cert_verified/rating 没了就少印年限/资质/评分（用户反馈「领队信息没显示完」）。
        _LEADER_PUBLIC_KEYS={'name','role','credential','specialties','baseCity','avatarUrl',
                             'leaderId','yearsExp','certVerified','ratingAvg','ratingCount','bio','leadCount'}
        a['leaders']=[{k:v for k,v in x.items() if k in _LEADER_PUBLIC_KEYS}
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
            a['detail']=sanitize_public_document({k:v for k,v in detail.items() if k in ('blocks','title','summary','subtitle','promoHtml')})
        safe_occ_keys={'id','activity_id','club_id','start_at','end_at','price','capacity','sold','remaining','status','label','execution'}
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
            # 会员等级活动折扣（2026-10-26）：试算与下单同口径 —— 折扣先于券，券基于折后金额判断门槛。
            _a=row(c.execute('SELECT * FROM activities WHERE id=?',(activity_id,)))
            _md,_mrate=(checkout_engine._member_activity_discount(c,activity=dict(_a),user_id=user_id,
                        amount=float(q.points['payable'])) if _a else (0.0,0.0))
            # 无折扣也要下发键（0），C 端与测试都按「键存在、值为 0」判定不折扣。
            q.points['memberDiscount']=_md; q.points['memberDiscountRate']=_mrate
            if _md>0:
                q.points['payable']=round(float(q.points['payable'])-_md,2)
            benefits=benefit_engine.quote_vouchers(c,voucher_codes=codes,user_id=user_id,club_id=int(q.occurrence['club_id']),kind='activity',amount_available=float(q.points['payable']))
        except ValueError as e:
            raise HTTPException(409,str(e))
    out=q.points; out['wallet']=q.wallet; out['occurrence']=q.occurrence; out['pointsPolicy']=q.points_policy
    # 2026-11-05：本次真正生效的「X 积分 = ¥1」。同时也在 pointsPolicy 与
    # maxRedeemable.club.rate 里，三处同源，C 端可任选一处展示，绝不写死 100。
    out['pointsRedeemRate']=int(q.points_policy.get('pointsRedeemRate') or 100)
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
def create_activity_checkout(request:Request,activity_id:int,payload:dict=Body(...)):
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
        # 微信登录用户（带会员 token）下单归属同一会员；否则回落手机号建号（匿名/手机号流程不变）。
        uid=_member_uid_from_request(request) or user_or_create(c,name,phone)
        c.execute('INSERT OR IGNORE INTO club_members(club_id,user_id) VALUES(?,?)',(a['club_id'],uid))
        c.execute('INSERT OR IGNORE INTO gear_point_accounts(user_id,balance) VALUES(?,0)',(uid,))
        try:
            participants,participant_policy=participant_service.normalize_for_checkout(activity=a,payer_name=name,payer_phone=phone,participants=participant_input)
            intent_id,q,policy_snapshot,benefits=checkout_engine.create_activity_intent(c,activity=a,occurrence=occ,user_id=uid,requested_club_points=req_cp,requested_gear_points=req_gp,voucher_codes=voucher_codes,participants=participants,participant_policy=participant_policy.as_dict())
        except (OverflowError,ValueError) as e: raise HTTPException(409,str(e))
        _mi=row(c.execute('SELECT member_discount,member_discount_rate FROM checkout_intents WHERE id=?',(intent_id,)))
    # Activity commerce is intentionally ClubOS Domain only. Medusa is Gear-only.
    return {'ok':True,'checkoutId':intent_id,'userId':uid,**q.as_dict(),'pointsPolicy':policy_snapshot,'benefits':benefits,
            'memberDiscount':float(_mi['member_discount'] or 0),'memberDiscountRate':float(_mi['member_discount_rate'] or 0) if _mi else 0,
            'commerceProvider':'clubos-domain','commerceCartId':None,'commerceNote':None,'nextAction':'PAY',
            'participantCount':len(participants),'participantPolicy':participant_policy.as_dict()}


@app.post('/api/public/wechat/login')
def wechat_login(payload:dict=Body(...)):
    """微信小程序一键登录：wx.login 的 code → 微信 jscode2session → openid → 会员会话。

    返回 { token, userId, openid, unionid, clubId }，小程序用 token 走 X-ClubOS-Token 头
    调用 /api/public/*（穿透托管网关剥离 Cookie/Authorization 的限制）。
    密钥不入库：appid 来自 club_miniprogram 登记，secret 取环境变量
    WECHAT_MINIPROGRAM_SECRET（或按俱乐部覆盖 WECHAT_MINIPROGRAM_SECRET_<club_id>）。
    """
    from security_v025 import _hash
    club_id=int(payload.get('clubId') or 1)
    code=(payload.get('code') or '').strip()
    if not code: raise HTTPException(400,'缺少微信登录 code')
    with conn() as c:
        club=row(c.execute('SELECT id,status FROM clubs WHERE id=?',(club_id,)))
        if not club or club['status']!='active': raise HTTPException(404,'俱乐部不存在或未启用')
        mp=_mp_get(c,club_id) or {}
        appid=mp.get('appid') or ''
        if not appid: raise HTTPException(400,'该俱乐部未配置微信小程序 appid')
        secret=os.getenv('WECHAT_MINIPROGRAM_SECRET_%d'%club_id) or os.getenv('WECHAT_MINIPROGRAM_SECRET','')
        if not secret: raise HTTPException(400,'微信小程序登录密钥未配置（需设置 WECHAT_MINIPROGRAM_SECRET）')
        try:
            wx=_wechat_jscode2session(appid,secret,code)
        except ValueError as e:
            raise HTTPException(400,str(e))
        openid=wx['openid']; unionid=wx.get('unionid') or ''
        u=row(c.execute('SELECT id FROM users WHERE mp_openid=?',(openid,)))
        if not u:
            c.execute('INSERT INTO users(name,mp_openid) VALUES(?,?)',('微信用户',openid))
            uid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        else:
            uid=int(u['id'])
        # 确保俱乐部会员关系与装备积分账户存在（解锁积分/等级等权益）。
        c.execute('INSERT OR IGNORE INTO club_members(club_id,user_id) VALUES(?,?)',(club_id,uid))
        c.execute('INSERT OR IGNORE INTO gear_point_accounts(user_id,balance) VALUES(?,0)',(uid,))
        # 会员登录账号按 俱乐部+openid 唯一；复用既有或新建。
        username='mp_%d_%s'%(club_id,openid)
        acct=row(c.execute('SELECT * FROM auth_accounts WHERE username=? AND status="active"',(username,)))
        now=int(time.time())
        if not acct:
            c.execute('INSERT INTO auth_accounts(username,password_hash,role,club_id,user_id,status) VALUES(?,?,?,?,?,?)',
                      (username,'','member',club_id,uid,'active'))
            acct_id=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        else:
            acct_id=int(acct['id'])
            c.execute('UPDATE auth_accounts SET user_id=? WHERE id=?',(uid,acct_id))
        # 签发会员会话（30 天；小程序体验更顺，管理端是 1h）。
        token=secrets.token_urlsafe(48); csrf=secrets.token_urlsafe(32)
        c.execute('UPDATE auth_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE account_id=? AND revoked_at IS NULL',(acct_id,))
        c.execute('INSERT INTO auth_sessions(token_hash,account_id,csrf_hash,expires_at) VALUES(?,?,?,?)',
                  (_hash(token),acct_id,_hash(csrf),now+86400*30))
    return {'ok':True,'token':token,'userId':uid,'openid':openid,'unionid':unionid,
            'clubId':club_id,'expiresAt':now+86400*30}


def _gear_address_snapshot(c,*,user_id:int,address_id):
    """装备是实物、由平台统一发货：结算单必须落到一个真实存在的收货地址。
    只认地址簿里的 id（按 user_id 归属校验），不认前端传来的地址文本 ——
    否则谁都可以往结算单里写一个不属于自己的地址。"""
    if not address_id: raise HTTPException(400,'请先添加收货地址')
    try: aid=int(address_id)
    except (TypeError,ValueError): raise HTTPException(400,'收货地址不存在')
    a=row(c.execute('SELECT * FROM user_addresses WHERE id=? AND user_id=?',(aid,user_id)))
    if not a: raise HTTPException(400,'收货地址不存在')
    return {'consignee':a['consignee'],'phone':a['phone'],
            'province':a['province'] or '','city':a['city'] or '',
            'district':a['district'] or '','detail':a['detail'] or ''}

@app.post('/api/public/clubs/{club_id}/gear-checkout')
def create_gear_checkout(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id); user_id=int(payload.get('userId',1)); items=payload.get('items',[]); req_gp=max(0,int(payload.get('gearPoints',0))); voucher_codes=payload.get('voucherCodes') or []
    if not items: raise HTTPException(400,'items required')
    with conn() as c:
        c.execute('INSERT OR IGNORE INTO gear_point_accounts(user_id,balance) VALUES(?,0)',(user_id,))
        checkout_engine.expire_gear(c)
        resolved=[]
        for it in items:
            p=row(c.execute('SELECT * FROM products WHERE id=? AND status="active"',(int(it['productId']),)))
            if not p: raise HTTPException(400,'商品不可售')
            _owned_product(c,p['id'])
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
                p['commerce_variant_id']=v.get('commerce_variant_id')
                if v['price'] is not None: p['price']=float(v['price'])
                stock=int(v['stock'] or 0)
            else:
                stock=int(p['stock'] or 0)
            if stock<q: raise HTTPException(409,f"{p['name']}{(' · '+p['_variantName']) if p.get('_variantName') else ''} 库存不足")
            resolved.append((p,q))
        try: intent_id,q,ipayload,benefits=checkout_engine.create_gear_intent(c,club_id=club_id,user_id=user_id,items=items,resolved_products=resolved,requested_gear_points=req_gp,voucher_codes=voucher_codes,address=_gear_address_snapshot(c,user_id=user_id,address_id=payload.get('addressId')))
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
        if intent['status']!='pending_payment':raise HTTPException(409,'结算单已取消或过期，请重新下单')
        if intent['kind']=='gear' and commerce_provider()=='medusa' and not intent.get('commerce_cart_id'):raise HTTPException(409,'商品规格与 Medusa 尚未完成同步，暂不能付款')
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

# ── 自助签到：C 端出码 + 执行端扫码核销（2026-10-30）──────────────────────
# 设计：服务端用 HMAC-SHA256 对 "club.occurrence.participant" 签名生成 token，
# 二维码由服务端用 qrcode 生成 SVG —— 前端零 QR 库依赖、单一真相源。
# 领队端用 vendored jsQR 解码 token，调 checkin-by-token 核销；签名 + 活动归属双重校验。
_CHECKIN_HASH = hashlib.sha256

def _checkin_secret():
    # 稳定签名密钥：环境变量优先；否则首启随机生成并持久化到 platform_settings。
    env = os.environ.get('CLUBOS_CHECKIN_SECRET')
    if env: return env
    key = 'checkin_secret'
    with conn() as c:
        r = c.execute('SELECT value FROM platform_settings WHERE key=?',(key,)).fetchone()
        if r and r[0]: return r[0]
        s = secrets.token_hex(32)
        c.execute('INSERT OR REPLACE INTO platform_settings(key,value) VALUES(?,?)',(key,s))
        return s

def _checkin_sign(club_id, occurrence_id, participant_id):
    msg = f"{int(club_id)}.{int(occurrence_id)}.{int(participant_id)}"
    sig = hmac.new(_checkin_secret().encode(), msg.encode(), _CHECKIN_HASH).hexdigest()[:16]
    return f"{msg}.{sig}"

def _checkin_verify(token):
    # 返回 (club_id, occurrence_id, participant_id, valid)
    try:
        parts = str(token or '').split('.')
        if len(parts) != 4: return (None, None, None, False)
        club_id, occurrence_id, participant_id, sig = parts
        msg = f"{club_id}.{occurrence_id}.{participant_id}"
        exp = hmac.new(_checkin_secret().encode(), msg.encode(), _CHECKIN_HASH).hexdigest()[:16]
        ok = hmac.compare_digest(exp, sig)
        return (int(club_id), int(occurrence_id), int(participant_id), ok)
    except Exception:
        return (None, None, None, False)

def _qr_svg(data):
    # 延迟导入：qrcode 是新增依赖，缺省时不拖垮整个应用启动。
    import qrcode
    from qrcode.image.svg import SvgImage
    img = qrcode.make(data, image_factory=SvgImage, box_size=8, border=2,
                      error_correction=qrcode.constants.ERROR_CORRECT_M)
    out = img.to_string()
    return out.decode('utf-8') if isinstance(out, bytes) else out

# 该装饰器曾误贴在下面的 _attach_checkin_status(c, participants) 上，导致这个接口
# 实际注册成了「需要 query 参数 c + participants」的辅助函数，C 端「我的参加人」
# 恒返回 422。装饰器归位到真正的路由函数。
@app.get('/api/public/registrations/{registration_id}/participants')
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
    """俱乐部端退款收件箱（活动退款可审核 + 装备退款只读可见）。

    OBS-04：实物电商的退款审核权仍归总平台 `/api/platform/refunds`，但同一俱乐部
    产生的装备退款必须能在本端查到进度，否则客服面对顾客无从回答。装备行统一打
    `managed_by='platform'` / `can_review=False`，前端不给审核按钮；俱乐部端三个写
    操作路由由 `_club_activity_refund_or_404` 守住，装备退款一律 409 拒写。
    """
    club_or_404(club_id)
    with conn() as c:
        activity=rows(c.execute('''SELECT rr.*,r.original_amount registration_original_amount,r.amount paid_cash,
            a.title activity_title,u.name user_name,u.phone,o.label occurrence_label,o.start_at,
            p.name participant_name,p.phone participant_phone
            FROM refund_requests rr
            JOIN registrations r ON r.id=rr.registration_id
            JOIN activities a ON a.id=r.activity_id
            JOIN users u ON u.id=r.user_id
            LEFT JOIN activity_occurrences o ON o.id=r.occurrence_id
            LEFT JOIN registration_participants p ON p.id=rr.participant_id
            WHERE rr.club_id=? AND rr.kind='activity' ORDER BY rr.created_at DESC''',(club_id,)))
        gear=rows(c.execute('''SELECT rr.*,go.total order_total,go.cash_paid paid_cash,
            go.status gear_order_status,go.refund_status gear_refund_status,
            u.name user_name,u.phone,
            (SELECT GROUP_CONCAT(p.name,'、') FROM gear_order_items goi
               JOIN products p ON p.id=goi.product_id WHERE goi.order_id=rr.gear_order_id) gear_titles
            FROM refund_requests rr
            LEFT JOIN gear_orders go ON go.id=rr.gear_order_id
            LEFT JOIN users u ON u.id=rr.user_id
            WHERE rr.club_id=? AND rr.kind='gear' ORDER BY rr.created_at DESC''',(club_id,)))
    for r in activity:
        r['managed_by']='club'; r['can_review']=True
    for r in gear:
        r['activity_title']=f"装备订单 #{r.get('gear_order_id') or '—'}"
        r['managed_by']='platform'; r['can_review']=False; r['readonly']=True
    out=activity+gear
    out.sort(key=lambda x:(str(x.get('created_at') or ''),str(x.get('id') or '')),reverse=True)
    return out

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


def _club_activity_refund_or_404(c,club_id:int,refund_id:str):
    """俱乐部端写操作闸门：装备退款归总平台审核，本端只读（OBS-04）。"""
    rr=row(c.execute('SELECT * FROM refund_requests WHERE id=? AND club_id=?',(refund_id,club_id)))
    if not rr: raise HTTPException(404,'退款申请不存在')
    if rr.get('kind')!='activity': raise HTTPException(409,'装备退款由总平台审核，俱乐部端仅可查看')
    return rr


@app.post('/api/club/{club_id}/refunds/{refund_id}/approve')
def club_approve_refund(club_id:int,refund_id:str):
    club_or_404(club_id)
    with conn() as c:
        _club_activity_refund_or_404(c,club_id,refund_id)
        try: refund_lifecycle.approve(c,refund_id=refund_id,approver=f'club:{club_id}')
        except ValueError as e: raise HTTPException(409,str(e))
    return _submit_refund_to_provider(refund_id)

@app.post('/api/club/{club_id}/refunds/{refund_id}/retry-provider')
def club_retry_refund_provider(club_id:int,refund_id:str):
    club_or_404(club_id)
    with conn() as c:
        _club_activity_refund_or_404(c,club_id,refund_id)
    return _submit_refund_to_provider(refund_id)

@app.post('/api/club/{club_id}/refunds/{refund_id}/reject')
def club_reject_refund(club_id:int,refund_id:str,payload:dict=Body(default={})):
    club_or_404(club_id)
    with conn() as c:
        _club_activity_refund_or_404(c,club_id,refund_id)
        try:return refund_lifecycle.reject(c,refund_id=refund_id,approver=f'club:{club_id}',note=str(payload.get('note') or ''))
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/public/clubs/{club_id}/mall/products')
def public_products(club_id:int, brand_id:int|None=None, category_id:int|None=None, gender:str|None=None):
    if IS_PROD and club_or_404(club_id)['status']!='active':raise HTTPException(404,'not found')
    return club_products(club_id, brand_id=brand_id, category_id=category_id, gender=gender)

@app.get('/api/public/clubs/{club_id}/mall/products/{product_id}')
def public_product(club_id:int, product_id:int, user_id:int|None=None):
    # 详情页要能被单独打开（刷新、分享出去的链接），所以不能只靠列表缓存。
    if IS_PROD and club_or_404(club_id)['status']!='active':raise HTTPException(404,'not found')
    with conn() as c:
        p=row(c.execute('SELECT %s,description FROM products WHERE id=? AND status="active"' % PROD_COLS,(product_id,)))
        if not p: raise HTTPException(404,'商品不存在')
        out=_with_extras(c,[p])[0]
        # 颜色 / 尺码两个维度单独下发：C 端要用它们画「先选颜色、再选尺码」的两级选择器。
        # 只有一维有值时就不下发（老商品只有 name），前端按老的一维列表画，不至于开天窗。
        vs=out.get('variants') or []
        colors=[];sizes=[]
        for v in vs:
            if v.get('color') and v['color'] not in colors: colors.append(v['color'])
            if v.get('size') and v['size'] not in sizes: sizes.append(v['size'])
        out['colorOptions']=[{'name':n,'hex':next((v.get('colorHex') for v in vs if v.get('color')==n),None)} for n in colors]
        out['sizeOptions']=sizes
        # 会员价要能被看见：会员中心写着「装备商城 X 折 —— 在装备商城里直接看到会员价」，
        # 商品页却只显示原价，等于承诺了一个看不见的价格。带 user_id 时把会员价一并回传。
        if user_id and not out.get('displayOnly'):
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
            _owned_product(c,p['id'])
            q=max(1,int(it.get('quantity',1)))
            if p['stock']<q:raise HTTPException(400,f"{p['name']} 库存不足")
            # 佣金率按俱乐部设定（2026-10-24）：与 checkout._finalize_gear 同一口径。
            prate=float(p['commission_rate'])
            if commission_rate_service is not None:
                prate=commission_rate_service.rate_for_product(c,int(club_id),prate)
            p['_commissionRate']=prate
            total+=p['price']*q;commission+=p['price']*q*prate;resolved.append((p,q))
        c.execute('INSERT INTO gear_orders(user_id,source_club_id,total,cash_paid,platform_point_subsidy,status,club_commission) VALUES(?,?,?,?,?,?,?)',(user_id,club_id,total,total,0,'paid',commission));oid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        for p,q in resolved:
            c.execute('INSERT INTO gear_order_items(order_id,product_id,quantity,unit_price,unit_cost_snapshot,commission_rate_snapshot) VALUES(?,?,?,?,?,?)',(oid,p['id'],q,p['price'],float(p.get('average_cost') or 0),float(p.get('_commissionRate') or 0)));inventory_engine.sale_outbound(c,product_id=int(p['id']),quantity=q,order_id=oid,actor_type='legacy_direct_order')
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

@app.get('/api/public/users/{user_id}/camp-contacts')
def public_camp_contacts(user_id:int,club_id:int=0):
    """营地 v4.1 #811：预订联系人历史——该用户营地订单里真实用过的联系人，最近优先。
    C 端确认订单页据此自动回填/点选联系人，避免每次手输姓名手机号。"""
    sql=("SELECT guest_name name,guest_phone phone,MAX(created_at) last_used "
         "FROM camp_orders WHERE user_id=? AND status!='cancelled' "
         "AND guest_name IS NOT NULL AND TRIM(guest_name)<>'' "
         "AND guest_phone IS NOT NULL AND TRIM(guest_phone)<>'' ")
    params=[user_id]
    if int(club_id)>0:
        sql+='AND club_id=? '; params.append(int(club_id))
    sql+='GROUP BY guest_name,guest_phone ORDER BY last_used DESC LIMIT 5'
    with conn() as c:return rows(c.execute(sql,params))

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
        # 装备折扣随俱乐部订阅套餐（平台控制，等级不参与）：C 端会员中心直接展示这一个值。
        grate,gname,gcode=ai_credit_engine.plan_gear_rate(c,club_id)
        gear_discount={'rate':round(grate,4),'discountZhe':round(grate*10,2),'planName':gname,'planCode':gcode}
        # 购买会员（2026-10-26）：俱乐部开通后 C 端展示可购买的会员卡与当前有效卡。
        club_row=c.execute('SELECT membership_purchase_enabled FROM clubs WHERE id=?',(club_id,)).fetchone()
        purchase_enabled=bool(club_row and club_row['membership_purchase_enabled'])
        purchasable=membership_engine.purchasable_tiers(c,club_id) if purchase_enabled else []
        active_purchase=membership_engine.active_purchase(c,club_id=club_id,user_id=user_id) if purchase_enabled else None
        # 2026-11-05：该会员「当前等级」生效的积分现金比，C 端会员中心据此显示
        # 「X 积分 = ¥1」；等级升级后这里会自动变成更高等级的比例。
        rate_info=describe_club_points_redeem_rate(c,club_id=int(club_id),user_id=int(user_id),setting=setting)
    # 2026-11-04：注册成为俱乐部会员即自动成为总平台会员（触发器 trg_club_member_platform 落库），
    # 这里把标识回给 C 端，便于「我的」展示而不暴露后台套餐。
    with conn() as _pc:
        _u=row(_pc.execute('SELECT platform_member FROM users WHERE id=?',(user_id,)))
    is_platform_member=bool(_u and _u.get('platform_member'))
    return {'member':member,'wallet':wallet,'benefits':benefits,'redemptions':redemptions,'tiers':tiers,
            'isPlatformMember':is_platform_member,
            'pointsRedeemRate':rate_info['effectiveRate'],
            'pointsRedeemRateInfo':{k:rate_info[k] for k in ('platformRate','clubRate','clubEffectiveRate','effectiveRate','source','currentTier')},
            'activityHistory':history,'gearOwned':owned,'gearDiscount':gear_discount,
            'membershipPurchase':{'enabled':purchase_enabled,'tiers':purchasable,'activePurchase':active_purchase,'paymentAvailable':not IS_PROD}}

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

def _address_full(a:dict)->str:
    return ' '.join([str(a.get(k) or '') for k in ('province','city','district','detail') if a.get(k)])

def _validate_address(payload:dict)->dict:
    consignee=str(payload.get('consignee') or '').strip()
    phone=str(payload.get('phone') or '').strip()
    province=str(payload.get('province') or '').strip()
    city=str(payload.get('city') or '').strip()
    district=str(payload.get('district') or '').strip()
    detail=str(payload.get('detail') or '').strip()
    if not consignee: raise HTTPException(400,'请填写收货人姓名')
    if not re.match(r'^1\d{10}$',phone): raise HTTPException(400,'请填写 11 位手机号')
    if not (province and city) or not detail: raise HTTPException(400,'请填写完整的省市区与详细地址')
    return {'consignee':consignee,'phone':phone,'province':province,'city':city,'district':district,'detail':detail}

@app.get('/api/public/users/{user_id}/addresses')
def user_addresses(user_id:int):
    with conn() as c:
        out=rows(c.execute('SELECT * FROM user_addresses WHERE user_id=? ORDER BY is_default DESC,id DESC',(user_id,)))
        for a in out: a['full']=_address_full(a)
        return out

@app.post('/api/public/users/{user_id}/addresses')
def user_add_address(user_id:int,payload:dict=Body(...)):
    v=_validate_address(payload)
    with conn() as c:
        # 第一条地址自动成为默认地址：没有默认地址时确认订单页会逼用户每次重选。
        first=c.execute('SELECT COUNT(*) FROM user_addresses WHERE user_id=?',(user_id,)).fetchone()[0]==0
        is_default=1 if (first or payload.get('isDefault')) else 0
        if is_default:
            c.execute('UPDATE user_addresses SET is_default=0 WHERE user_id=?',(user_id,))
        c.execute('''INSERT INTO user_addresses(user_id,consignee,phone,province,city,district,detail,is_default)
                     VALUES(?,?,?,?,?,?,?,?)''',(user_id,v['consignee'],v['phone'],v['province'],v['city'],v['district'],v['detail'],is_default))
        aid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        a=row(c.execute('SELECT * FROM user_addresses WHERE id=?',(aid,)))
        a['full']=_address_full(a)
        return a

@app.patch('/api/public/users/{user_id}/addresses/{address_id}')
def user_update_address(user_id:int,address_id:int,payload:dict=Body(...)):
    with conn() as c:
        a=row(c.execute('SELECT * FROM user_addresses WHERE id=? AND user_id=?',(address_id,user_id)))
        if not a: raise HTTPException(404,'地址不存在')
        fields=['consignee','phone','province','city','district','detail']
        sets=[];vals=[]
        if any(k in payload for k in fields):
            merged={**a,**{k:payload[k] for k in fields if k in payload}}
            v=_validate_address(merged)
            for k in fields: sets.append(f'{k}=?');vals.append(v[k])
        if payload.get('isDefault'):
            c.execute('UPDATE user_addresses SET is_default=0 WHERE user_id=?',(user_id,))
            sets.append('is_default=1')
        if sets:
            c.execute(f'UPDATE user_addresses SET {", ".join(sets)},updated_at=CURRENT_TIMESTAMP WHERE id=?',vals+[address_id])
        out=row(c.execute('SELECT * FROM user_addresses WHERE id=?',(address_id,)))
        out['full']=_address_full(out)
        return out

@app.delete('/api/public/users/{user_id}/addresses/{address_id}')
def user_delete_address(user_id:int,address_id:int):
    with conn() as c:
        a=row(c.execute('SELECT * FROM user_addresses WHERE id=? AND user_id=?',(address_id,user_id)))
        if not a: raise HTTPException(404,'地址不存在')
        c.execute('DELETE FROM user_addresses WHERE id=?',(address_id,))
        # 删掉的正好是默认地址时，把剩下最早的一条补成默认，避免用户回到「没有默认地址」的状态。
        if a['is_default']:
            left=row(c.execute('SELECT id FROM user_addresses WHERE user_id=? ORDER BY id LIMIT 1',(user_id,)))
            if left: c.execute('UPDATE user_addresses SET is_default=1 WHERE id=?',(left['id'],))
    return {'ok':True}

def _refund_progress(kind:str,status:str|None):
    st=str(status or 'none')
    if st in ('requested','partial_requested'): return {'step':'requested','label':'退款申请已提交','detail':'等待俱乐部审核' if kind=='activity' else '等待 ClubOS 平台审核'}
    if st in ('processing','partial_processing'): return {'step':'processing','label':'退款处理中','detail':'已审核通过，等待支付渠道退款'}
    if st=='partial': return {'step':'partial','label':'已完成部分退款','detail':'订单仍有其他参加人有效'}
    if st=='succeeded': return {'step':'succeeded','label':'退款完成','detail':'资金与相关积分/福利已按规则处理'}
    if st=='rejected': return {'step':'rejected','label':'退款未通过','detail':'可查看审核说明'}
    return {'step':'none','label':'无退款申请','detail':''}

def _return_window_state(order: dict, ef: dict | None):
    """C 端「7 天内可申请退换货」提示：返回 (窗口天数, 剩余天数)。

    基准与 after_sales._assert_within_return_window 完全一致（确认收货时间优先，
    外部代发订单取物流妥投时间）。无基准 / 关闭窗口时返回 (0, None)。
    """
    try: limit=int(os.environ.get('AFTER_SALES_WINDOW_DAYS','7'))
    except Exception: limit=7
    if limit<=0: return (0,None)
    base=str(order.get('delivered_at') or '').strip() or str((ef or {}).get('fulfilled_at') or '').strip()
    if not base: return (limit,None)
    from datetime import datetime, timezone
    try:
        dt=datetime.fromisoformat(base.replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        left=limit-(datetime.now(timezone.utc)-dt).total_seconds()/86400.0
        return (limit,max(0,int(left+0.999)))
    except Exception:
        return (limit,None)


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
            # 出行指引：集合信息 / 带队人 / 已发送通知 / 本人分车（2026-10-22 用户反馈
            # 「分车、活动通知保存之后能同步到 C 端用户吗」）。俱乐部没填执行数据时返回 None。
            x['trip']=_public_trip_info(c,x)
            a=row(c.execute('SELECT * FROM activities WHERE id=?',(x['activity_id'],)))
            occ=row(c.execute('SELECT * FROM activity_occurrences WHERE id=?',(x['occurrence_id'],))) if x.get('occurrence_id') else None
            # Full-order refund is available only before any participant-level refund begins.
            partial_exists=any(str(p.get('refund_status') or 'none') in ('requested','processing','succeeded') or p.get('status')=='refunded' for p in x.get('participants',[]))
            if x['status']=='paid' and str(x.get('refund_status') or 'none') in ('none','rejected') and not partial_exists:
                x['refundQuote']=activity_refund_policy.evaluate(activity=a,occurrence=occ,cash_paid=float(x.get('amount') or 0)) if a else None
            else: x['refundQuote']=None
            for p in x.get('participants',[]):
                # 自助签到码：每个参加人一个 HMAC 签名 token（C 端据此向 checkin-qr 换二维码）。
                p['checkinToken']=_checkin_sign(x.get('club_id',club_id), p.get('occurrence_id') or x.get('occurrence_id'), p.get('id'))
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
            # 外部履约桥随行：让 C 端区分「物流已妥投·待确认收货」与「已签收」
            ef=row(c.execute('SELECT push_status,fulfill_status,carrier,tracking_no,external_order_id,fulfilled_at,external_ref FROM external_fulfillment_orders WHERE order_id=?',(x['id'],)))
            x['externalFulfillment']=({'pushStatus':ef['push_status'],'fulfillStatus':ef['fulfill_status'],
                'carrier':ef['carrier'],'trackingNo':ef['tracking_no'],'externalOrderId':ef['external_order_id'],
                'fulfilledAt':ef['fulfilled_at'],'externalRef':ef['external_ref']} if ef else None)
            # 退换货申请窗口（默认签收/妥投后 7 天）：后端算好剩余天数下发，C 端只展示
            x['returnWindowLimitDays'],x['returnWindowDays']=_return_window_state(x,ef)
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

def _complete_receipt(c, order_id: int) -> dict:
    """订单「确认收货」统一收口：shipped → delivered + 冻结佣金 + 对齐外部履约表。

    被三处复用：C 端 confirm-receipt 端点、平台 PATCH delivered、10 天自动确认循环
    (_auto_confirm_loop)。幂等：已是 delivered 原样返回，不重复冻结。
    """
    o = row(c.execute('SELECT * FROM gear_orders WHERE id=?', (int(order_id),)))
    if not o:
        raise LookupError('装备订单不存在')
    if o['status'] == 'delivered':
        return {'ok': True, 'orderId': order_id, 'status': 'delivered', 'idempotent': True}
    if o['status'] != 'shipped':
        raise ValueError(f"当前订单状态({o['status']})不能确认收货")
    c.execute("UPDATE gear_orders SET status='delivered',delivered_at=COALESCE(delivered_at,CURRENT_TIMESTAMP) WHERE id=?",(order_id,))
    commission = commission_engine.freeze_order(c, order_id=order_id)
    # 外部履约表对齐：用户确认收货后，供应商侧物流也已妥投，统一标记 delivered。
    try:
        c.execute("UPDATE external_fulfillment_orders SET fulfill_status=?,updated_at=CURRENT_TIMESTAMP WHERE order_id=? AND fulfill_status!='delivered'",
                  (external_fulfillment.FUL_DELIVERED, int(order_id)))
    except Exception:
        pass
    return {'ok': True, 'orderId': order_id, 'status': 'delivered', 'commission': commission}


@app.post('/api/public/orders/{order_id}/confirm-receipt')
def confirm_receipt(order_id:int,payload:dict=Body(default={})):
    # C 端「确认收货」：仅会员本人可操作，且仅在已发货(shipped)时允许签收 → delivered，
    # 同时冻结该订单的佣金（进入售后保障期）。生产环境签收应由平台端 PATCH delivered 完成，
    # 这里给 C 端用户一个自助签收出口，避免订单永远停在 shipped。
    user_id=int(payload.get('userId') or 0)
    if not user_id: raise HTTPException(400,'userId required')
    with conn() as c:
        o=row(c.execute('SELECT user_id,status FROM gear_orders WHERE id=?',(order_id,)))
        if not o: raise HTTPException(404,'装备订单不存在')
        if int(o.get('user_id') or 0)!=user_id: raise HTTPException(403,'该订单不属于当前用户')
        try:
            res=_complete_receipt(c, order_id)
        except ValueError as e:
            raise HTTPException(409,str(e))
    return res

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
    carrier=str(payload.get('carrier') or ''); tracking=str(payload.get('trackingNo') or '')
    with conn() as c:
        try:out=after_sales_engine.submit_return(c,case_id=case_id,carrier=carrier,tracking_no=tracking)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))
    out['externalReturnSync']=_push_external_return(case_id,out,action='return_shipped',carrier=carrier,tracking=tracking)
    return out

@app.post('/api/public/orders/{order_id}/refund-request')
def request_gear_refund(order_id:int,payload:dict=Body(default={})):
    with conn() as c:
        try:return refund_lifecycle.request_gear(c,order_id=order_id,requester='c_end_user',reason=str(payload.get('reason') or '用户申请装备退款'))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

# PLATFORM POINT SUBSIDY POLICY: 装备积分用于活动报名抵扣时，成本由总平台承担，
# 所以**准入权与比例都归总平台**（2026-10-24 用户明确：不是由俱乐部自己决定，
# 而是由该俱乐部订阅的套餐决定；基础套餐无此项权益，Pro/Enterprise 有，
# 比例再由总平台针对不同俱乐部单独设定）。
@app.get('/api/platform/points-policy')
def platform_points_policy():
    """平台积分补贴总开关 + 各俱乐部的装备积分抵用现状。"""
    enabled=str(setting('gear_points_activity_redeem_enabled','1')).strip().lower() in {'1','true','yes','on'}
    with conn() as c:
        clubs=rows(c.execute("""SELECT cl.id,cl.name,cl.status,
                                     COALESCE(s.plan_code,cl.plan) plan_code
                              FROM clubs cl
                              LEFT JOIN club_ai_subscriptions s ON s.club_id=cl.id AND s.status='active'
                              ORDER BY cl.id"""))
        plans={p['code']:p for p in ai_credit_engine.plans(c,include_inactive=True)}
        items=[]
        for cl in clubs:
            pol=gear_points_policy.resolve(c,int(cl['id'])).as_dict()
            plan=plans.get(cl['plan_code']) or {}
            items.append({
                'clubId':int(cl['id']),
                'clubName':cl['name'],
                'clubStatus':cl['status'],
                'planCode':cl['plan_code'] or '',
                'planName':str(plan.get('name') or ''),
                'planPercent':round(float(plan.get('gear_points_redeem_percent') or 0),2),
                'allowed':bool(pol['allowed']),
                'redeemPercent':pol['redeemPercent'],
                'hasClubOverride':bool(pol['hasClubOverride']),
                'clubPercent':pol['clubPercent'],
                'reason':pol['reason'],
            })
    return {'gearPointsActivityRedeemEnabled':enabled,'clubs':items}

@app.patch('/api/platform/points-policy')
def update_platform_points_policy(payload:dict=Body(...)):
    enabled=bool(payload.get('gearPointsActivityRedeemEnabled',True))
    with conn() as c:
        c.execute('INSERT INTO platform_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  ('gear_points_activity_redeem_enabled','1' if enabled else '0'))
    return {'gearPointsActivityRedeemEnabled':enabled}

@app.patch('/api/platform/points-policy/clubs/{club_id}')
def update_platform_club_gear_points_policy(club_id:int,payload:dict=Body(...)):
    """总平台按俱乐部设定装备积分抵用比例。

    `redeemPercent` = 该俱乐部单笔最多可抵**订单金额**的百分之几；
    实际生效比例 = min(这里设的值, 该俱乐部套餐自带的比例) —— 套餐是权益上限，
    平台只能在这个额度内分配，不能凭空放大。
    传 `clearOverride: true` 表示清除单独设定、回到套餐默认比例。
    """
    with conn() as c:
        try:
            if bool(payload.get('clearOverride')):
                return gear_points_policy.clear_club_policy(c,club_id)
            pct=payload.get('redeemPercent')
            if pct is None:
                raise ValueError('请填写抵扣比例')
            return gear_points_policy.set_club_policy(
                c,club_id,
                redeem_percent=float(pct),
                enabled=bool(payload.get('enabled',True)),
                note=str(payload.get('note') or ''),
            )
        except ValueError as e:
            raise HTTPException(400,str(e))

@app.post('/api/public/club-applications')
def public_club_application(payload:dict=Body(...)):
    name=str(payload.get('name') or '').strip()
    if not name: raise HTTPException(400,'俱乐部名称必填')
    # 2026-11-03 官网申请新增必填邮箱：审核通过后后台用它给俱乐部发账户密码与使用说明。
    email=str(payload.get('email') or '').strip()
    if not email: raise HTTPException(400,'邮箱必填，用于接收账户密码与使用说明')
    import re as _re
    if not _re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$',email): raise HTTPException(400,'邮箱格式不正确')
    with conn() as c:
        # source='website'：官网「申请体验」提交的申请（server.js 同源代理转发）。
        # 平台端「俱乐部入驻」按它区分来源并单列官网申请明细（platform_club_applications）。
        c.execute('''INSERT INTO clubs(name,status,plan,contact_name,contact_phone,contact_email,city,application_note,business_license_ref,source)
                     VALUES(?,?,?,?,?,?,?,?,?,'website')''',(name,'pending','starter',payload.get('contactName'),payload.get('contactPhone'),email,payload.get('city'),payload.get('applicationNote'),payload.get('businessLicenseRef')))
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
        data=rows(c.execute('''SELECT cl.*,COALESCE(ac.balance,0) ai_credits,COALESCE(ac.monthly_quota,0) monthly_quota,
          s.plan_code subscription_plan,s.status subscription_status,s.current_period_end,
          COALESCE((SELECT SUM(d.amount-d.resolved_amount) FROM ai_credit_adjustment_debt d WHERE d.club_id=cl.id AND d.resolved=0),0) ai_credit_debt,
          COALESCE(acc.username,'') account_username
          FROM clubs cl LEFT JOIN ai_credit_accounts ac ON ac.club_id=cl.id
          LEFT JOIN club_ai_subscriptions s ON s.club_id=cl.id
          LEFT JOIN (SELECT club_id,username FROM auth_accounts WHERE role='club' AND status='active') acc ON acc.club_id=cl.id
          ORDER BY cl.id DESC'''))
    # 总平台要看到俱乐部 logo：clubs.logo_url 存的是登录态代理地址
    # （/api/club/{id}/settings/logo.png），那是「某个俱乐部自己的」路由，
    # 平台管理员访问会被归属校验挡下。这里改用平台专属代理（见
    # platform_club_logo：/api/platform/ 前缀在 security_v025 里已要求
    # platform 角色，且它不看 status —— 待审核俱乐部的 logo 也要看得见，
    # 否则审核时看到的是一片占位色块）。没有 logo 显式写空串，
    # 前端据此画首字母占位而不是裂图。
    for x in data:
        m=_LOGO_URL_RE.match(str(x.get('logo_url') or ''))
        x['logoUrl']=f'/api/platform/clubs/{x["id"]}/logo{m.group(2)}' if m else ''
    return data


@app.get('/api/platform/club-applications')
def platform_club_applications(status:str='all',source:str='all'):
    """俱乐部入驻申请明细（2026-10-26）。

    官网「申请体验」表单（server.js 代理到 /api/public/club-applications）与
    平台手动「新增入驻」都写 clubs 表，用 clubs.source 区分来源：
      website  = 官网申请（潜在客户线索）
      platform = 平台手动新增（运营代录入）
    这里是平台端「官网申请明细」面板的专属数据源：带状态统计 + 按来源/状态
    筛选，待处理排最前、其余按申请时间倒序，方便运营优先处理新线索。
    """
    with conn() as c:
        conds=[];args=[]
        if source in ('website','platform'):
            conds.append('cl.source=?');args.append(source)
        if status in ('pending','active','disabled','rejected'):
            conds.append('cl.status=?');args.append(status)
        where=('WHERE '+' AND '.join(conds)) if conds else ''
        data=rows(c.execute(f'''SELECT cl.id,cl.name,cl.status,cl.plan,cl.contact_name,cl.contact_phone,cl.contact_email,
                   cl.city,cl.application_note,cl.source,cl.created_at,cl.reviewed_at,cl.reviewed_by,cl.disabled_reason,cl.logo_url
                   FROM clubs cl {where}
                   ORDER BY CASE WHEN cl.status='pending' THEN 0 ELSE 1 END, cl.id DESC''',args))
        stat_sql=f"SELECT status,COUNT(*) n FROM clubs cl {where} GROUP BY status"
        stats={r['status']:int(r['n']) for r in rows(c.execute(stat_sql,args))}
        total=int(c.execute(f'SELECT COUNT(*) FROM clubs cl {where}',args).fetchone()[0])
    # logoUrl 与 platform_clubs 同款：把登录态代理地址换算成平台专属代理
    # （待审核俱乐部的 logo 也要看得见）。
    for x in data:
        m=_LOGO_URL_RE.match(str(x.get('logo_url') or ''))
        x['logoUrl']=f'/api/platform/clubs/{x["id"]}/logo{m.group(2)}' if m else ''
    return {'items':data,'stats':stats,'total':total}

@app.get('/api/platform/clubs/{club_id}/logo.{ext}')
def platform_club_logo(club_id:int,ext:str):
    """总平台查看任意俱乐部 logo（含待审核/已停用）—— 仅平台角色可访问。"""
    return _serve_club_logo(club_id,ext)

@app.post('/api/platform/clubs')
def platform_add_club(payload:dict=Body(...)):
    name=str(payload.get('name') or '').strip()
    if not name: raise HTTPException(400,'俱乐部名称必填')
    with conn() as c:
        c.execute('''INSERT INTO clubs(name,status,plan,contact_name,contact_phone,contact_email,city,application_note,business_license_ref,source)
                     VALUES(?,?,?,?,?,?,?,?,?,'platform')''',(name,'pending',payload.get('plan','pro'),payload.get('contactName'),payload.get('contactPhone'),str(payload.get('contactEmail') or '').strip(),payload.get('city'),payload.get('applicationNote'),payload.get('businessLicenseRef')))
        cid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0]);ai_credit_engine.ensure_account(c,cid)
    return {'id':cid,'status':'pending'}

def _gen_temp_password() -> str:
    """生成 16 位高强度临时密码（仅含无歧义字符，满足 hash_password 的 ≥14 位要求）。"""
    alphabet='abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return ''.join(secrets.choice(alphabet) for _ in range(16))

def _ensure_club_account(c, club_id:int, *, force_new:bool=False) -> dict:
    """确保俱乐部存在 role='club' 的后台账户。
       · 已存在且不强制重置 → 返回 {username, password:None}（不泄露旧密码）；
       · 首次开通 → 新建并返回一次性明文密码；
       · force_new=True（重置密码）→ 总是生成新密码并返回。
       返回结构：{'username','password'(可能 None),'created'(bool),'reset'(bool)}。"""
    acct=row(c.execute("SELECT * FROM auth_accounts WHERE role='club' AND club_id=? AND status='active'",(club_id,)))
    if acct and not force_new:
        return {'username':acct['username'],'password':None,'created':False,'reset':False}
    pwd=_gen_temp_password();phash=hash_password(pwd);uname=f'club{club_id}'
    if acct:
        c.execute('UPDATE auth_accounts SET password_hash=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(phash,acct['id']))
        return {'username':acct['username'],'password':pwd,'created':False,'reset':True}
    try:
        c.execute("INSERT INTO auth_accounts(username,password_hash,role,club_id,status) VALUES(?,?,?,?,?)",(uname,phash,'club',club_id,'active'))
    except IntegrityError:
        uname=f'club{club_id}_{secrets.token_hex(2)}'
        c.execute("INSERT INTO auth_accounts(username,password_hash,role,club_id,status) VALUES(?,?,?,?,?)",(uname,phash,'club',club_id,'active'))
    return {'username':uname,'password':pwd,'created':True,'reset':False}

@app.patch('/api/platform/clubs/{club_id}/status')
def platform_club_status(club_id:int,payload:dict=Body(...)):
    status=str(payload.get('status') or '')
    if status not in {'pending','active','disabled','rejected'}: raise HTTPException(400,'status 不合法')
    # 2026-11-04：account 只在 status=='active' 分支里赋值，其他状态（pending/disabled/rejected）
    # 走到下面的 `if account:` 会抛 UnboundLocalError → 500。默认 None，非开通分支就不返回它。
    account=None
    with conn() as c:
        if not c.execute('SELECT 1 FROM clubs WHERE id=?',(club_id,)).fetchone(): raise HTTPException(404,'俱乐部不存在')
        reason=str(payload.get('reason') or '')
        c.execute('''UPDATE clubs SET status=?,disabled_reason=?,reviewed_at=CASE WHEN ? IN ('active','rejected') THEN CURRENT_TIMESTAMP ELSE reviewed_at END,
                     reviewed_by=CASE WHEN ? IN ('active','rejected') THEN 'platform' ELSE reviewed_by END WHERE id=?''',(status,reason,status,status,club_id))
        if status=='active':
            # 2026-11-03 审核通过自动开通「体验套餐」（trial）：100 积分 · 3 天体验期 ·
            # 无装备折扣/无装备积分 · 仅基础功能。只对「当前没有生效订阅」的俱乐部发放
            # —— 重新启用曾被停用的老俱乐部时，保留它原有的付费套餐不被覆盖。
            # grant 幂等（source_id 固定 trial_{club_id}），重复点击不会重复发积分。
            if payload.get('assignTrial'):
                _sub=c.execute('SELECT status FROM club_ai_subscriptions WHERE club_id=?',(club_id,)).fetchone()
                if not _sub or _sub['status']!='active':
                    ai_credit_engine.assign_plan(c,club_id=club_id,plan_code='trial',auto_renew=False,period_days=3,create_order=False,allow_trial=True)
                    ai_credit_engine.grant(c,club_id=club_id,amount=100,ledger_type='trial',source_type='trial_grant',source_id=f'trial_{club_id}',note='体验套餐开通赠送积分（3天体验期）')
            c.execute("UPDATE club_ai_subscriptions SET status='active',updated_at=CURRENT_TIMESTAMP WHERE club_id=? AND status='paused'",(club_id,))
            # 2026-11-03 审核通过自动为俱乐部开通后台账户（role='club'），初始密码一次性
            # 返回给平台运营，由人工通过邮件/微信发给俱乐部（本系统不存明文密码，无法事后找回）。
            account=_ensure_club_account(c,club_id)
        elif status in {'disabled','rejected'}: c.execute("UPDATE club_ai_subscriptions SET status='paused',updated_at=CURRENT_TIMESTAMP WHERE club_id=? AND status='active'",(club_id,))
    res={'ok':True,'status':status}
    if account: res['account']=account
    return res

@app.post('/api/platform/clubs/{club_id}/reset-password')
def platform_club_reset_password(club_id:int):
    """为俱乐部重置后台账户密码（无邮件自动发送：返回新明文密码，由运营手动发给俱乐部）。
       幂等：账户不存在则一并创建。仅平台角色可访问。"""
    with conn() as c:
        if not c.execute('SELECT 1 FROM clubs WHERE id=?',(club_id,)).fetchone(): raise HTTPException(404,'俱乐部不存在')
        acc=_ensure_club_account(c,club_id,force_new=True)
    return {'ok':True,'account':acc}

@app.patch('/api/platform/clubs/{club_id}')
def platform_club_update(club_id:int,payload:dict=Body(...)):
    """编辑俱乐部企业资料与基础信息（平台运营补录公司名称/法人/地址等，便于管理）。
       仅平台角色可访问；密码永不在此接口返回，忘记密码走 reset-password。"""
    with conn() as c:
        if not c.execute('SELECT 1 FROM clubs WHERE id=?',(club_id,)).fetchone(): raise HTTPException(404,'俱乐部不存在')
        allowed=('name','contact_name','contact_phone','contact_email','city','company_name','legal_person','company_address')
        fields={k:str(payload[k] or '').strip() for k in allowed if k in payload}
        if not fields: raise HTTPException(400,'没有可更新的字段')
        if 'name' in fields and not fields['name']: raise HTTPException(400,'俱乐部名称不能为空')
        setsql=','.join(f'{k}=?' for k in fields)
        c.execute(f'UPDATE clubs SET {setsql} WHERE id=?',list(fields.values())+[club_id])
    return {'ok':True,'updated':fields}

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

@app.delete('/api/platform/ai-credit/plans/{code}')
def platform_ai_credit_plan_delete(code:str):
    """删除套餐。仍被俱乐部订阅占用的套餐会被拒绝（引擎里判定）。"""
    with conn() as c:
        try:return ai_credit_engine.delete_plan(c,code)
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
    allowed=['title','description','points_cost','cash_value','stock','status','target_club_id','benefit_type','applies_to'];sets=[];vals=[]
    for k in allowed:
        if k in payload: sets.append(f'{k}=?'); vals.append(payload[k])
    with conn() as c:
        b=row(c.execute("SELECT * FROM member_benefits WHERE id=? AND owner_type='PLATFORM'",(benefit_id,)))
        if not b: raise HTTPException(404,'平台福利不存在')
        if sets:
            vals.append(benefit_id); c.execute(f"UPDATE member_benefits SET {', '.join(sets)},updated_at=CURRENT_TIMESTAMP WHERE id=?",vals)
    return {'ok':True}

# ── 优惠券体系（2026-10-24）────────────────────────────────────────────────
# 两种用途（活动报名券 / 装备购买券）× 两种形态（直接抵 / 满多少减多少）
# × 四种投放范围（全部俱乐部 / 全部 C 端用户 / 指定俱乐部 / 指定城市）。
# 成本一律总平台承担：核销时 funding_owner=PLATFORM，结算进平台补贴账。

# PATCH 驼峰 → 列名（POST 走 benefits.py 的驼峰约定，PATCH  historically 是列名，
# 两边都收，避免「弹层发了但没落库」这类静默失败）
_COUPON_PATCH_ALIAS = {
    'benefitType': 'benefit_type', 'cashValue': 'cash_value', 'thresholdAmount': 'threshold_amount',
    'targetType': 'target_type', 'targetCity': 'target_city', 'targetClubId': 'target_club_id',
    'claimMode': 'claim_mode', 'imageKey': 'image_key', 'validFrom': 'valid_from',
    'validTo': 'valid_to', 'perUserLimit': 'per_user_limit', 'shareEnabled': 'share_enabled',
    'pointsCost': 'points_cost', 'pointsType': 'points_type',
}

@app.get('/api/platform/coupon-images')
def platform_coupon_images():
    """内置通用券图模板（不做图片上传，后台建券时挑一款即可）。"""
    return {'keys': list(benefit_engine.COUPON_IMAGE_KEYS),
            'modes': list(benefit_engine.COUPON_MODES),
            'targetTypes': list(benefit_engine.TARGET_TYPES),
            'claimModes': list(benefit_engine.CLAIM_MODES)}


@app.get('/api/platform/coupons')
def platform_coupons():
    with conn() as c:
        out = rows(c.execute("""SELECT * FROM member_benefits
                                WHERE owner_type='PLATFORM' AND benefit_type IN ('activity_coupon','gear_coupon')
                                ORDER BY id DESC"""))
        for x in out:
            x['stats'] = benefit_engine.coupon_stats(c, int(x['id']))
        return out


@app.post('/api/platform/coupons')
def platform_add_coupon(payload: dict = Body(...)):
    with conn() as c:
        try:
            bid = benefit_engine.create_platform_benefit(c, payload=payload)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {'ok': True, 'couponId': bid, 'voucherCode': None}


@app.patch('/api/platform/coupons/{coupon_id}')
def platform_update_coupon(coupon_id: int, payload: dict = Body(...)):
    # 兼容两种提交口味：后端列名（snake_case）与前端惯用驼峰。旧版「改投放」弹层
    # 发的是驼峰，被 allowed 白名单静默丢弃 —— 表现为「改了投放没反应」。
    payload = {_COUPON_PATCH_ALIAS.get(k, k): v for k, v in (payload or {}).items()}
    allowed = ['title', 'description', 'status', 'stock', 'cash_value', 'mode', 'threshold_amount',
               'image_key', 'valid_from', 'valid_to', 'claim_mode', 'target_type', 'target_city',
               'target_club_id', 'per_user_limit', 'share_enabled', 'applies_to']
    sets = []
    vals = []
    for k in allowed:
        if k in payload:
            v = payload[k]
            if k == 'target_club_id':
                v = int(v) if v not in (None, '') else None
            elif k == 'per_user_limit' and v not in (None, ''):
                v = max(1, int(v))
            sets.append(f'{k}=?')
            vals.append(v)
    with conn() as c:
        b = row(c.execute("SELECT * FROM member_benefits WHERE id=? AND owner_type='PLATFORM'", (coupon_id,)))
        if not b:
            raise HTTPException(404, '优惠券不存在')
        # 生效前的合并态校验（与 _create 一致：投放范围必须配对，否则会出现
        # 「target_type=club 但没选俱乐部」这种谁都领不到的券）
        merged = dict(b)
        for k in allowed:
            if k in payload:
                merged[k] = payload[k]
        if merged.get('target_type') == 'club':
            tcid = merged.get('target_club_id')
            if tcid in (None, '', 0):
                raise HTTPException(400, '定向俱乐部投放必须选择俱乐部')
            club_or_404(int(tcid))
        elif merged.get('target_type') == 'city':
            if not str(merged.get('target_city') or '').strip():
                raise HTTPException(400, '按城市投放必须选择城市')
        if sets:
            vals.append(coupon_id)
            c.execute(f"UPDATE member_benefits SET {', '.join(sets)},updated_at=CURRENT_TIMESTAMP WHERE id=?", vals)
        return {'ok': True, 'stats': benefit_engine.coupon_stats(c, coupon_id)}


@app.delete('/api/platform/coupons/{coupon_id}')
def platform_delete_coupon(coupon_id: int):
    """删除优惠券（2026-10-25）。

    只允许删「一张都没发出去」的券：benefit_redemptions 里一旦有记录，C 端券包
    是按 benefit_id JOIN 券面渲染的，删了主表就会留下孤儿券（点进去白屏）。
    这种券正确的操作是「下架」。所以这里先查发放数，非零直接 409 说明原因。
    """
    with conn() as c:
        b = row(c.execute("SELECT * FROM member_benefits WHERE id=? AND owner_type='PLATFORM'", (coupon_id,)))
        if not b:
            raise HTTPException(404, '优惠券不存在')
        if b.get('benefit_type') not in ('activity_coupon', 'gear_coupon'):
            raise HTTPException(400, '该记录不是优惠券，请到福利管理里操作')
        stats = benefit_engine.coupon_stats(c, coupon_id)
        issued = int(stats.get('issued') or 0)
        if issued:
            raise HTTPException(409, f'已有 {issued} 张券被领取（含已核销 {int(stats.get("used") or 0)} 张），不能删除；如需停止投放请改为下架')
        c.execute('DELETE FROM member_benefits WHERE id=?', (coupon_id,))
        return {'ok': True, 'deletedId': coupon_id}


@app.post('/api/platform/coupons/{coupon_id}/grant')
def platform_grant_coupon(coupon_id: int, payload: dict = Body(default={})):
    """定向群发：全部 C 端用户 / 指定俱乐部成员。成本总平台承担。"""
    scope = str(payload.get('scope') or 'all_users')
    with conn() as c:
        b = row(c.execute("SELECT * FROM member_benefits WHERE id=? AND owner_type='PLATFORM'", (coupon_id,)))
        if not b:
            raise HTTPException(404, '优惠券不存在')
        if scope == 'club':
            cid = int(payload.get('clubId') or 0)
            if not cid:
                raise HTTPException(400, '请选择要投放的俱乐部')
            club_or_404(cid)
            uids = [int(r[0]) for r in c.execute(
                'SELECT DISTINCT user_id FROM club_members WHERE club_id=?', (cid,)).fetchall()]
        elif scope == 'city':
            city = str(payload.get('city') or '').strip()
            if not city:
                raise HTTPException(400, '请选择投放城市')
            uids = [int(r[0]) for r in c.execute(
                'SELECT id FROM users WHERE city=?', (city,)).fetchall()]
        else:
            uids = [int(r[0]) for r in c.execute('SELECT id FROM users').fetchall()]
        try:
            res = benefit_engine.grant_coupon(c, benefit_id=coupon_id, user_ids=uids,
                                              club_id=int(payload.get('clubId') or 0))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {'ok': True, **res, 'stats': benefit_engine.coupon_stats(c, coupon_id)}


# ── C 端：领券中心 / 我的券包 / 分享 / 注册城市 ──────────────────────────────

@app.get('/api/public/coupons/claimable')
def public_claimable_coupons(request:Request,user_id:int|None=None,club_id:int=0,city:str=''):
    """领券中心：当前用户可以领的券（城市定向按注册城市圈人）。"""
    user_id=public_user_id(request,user_id)
    with conn() as c:
        u = row(c.execute('SELECT * FROM users WHERE id=?', (user_id,)))
        user_city = str((u or {}).get('city') or '').strip() or str(city or '').strip()
        return benefit_engine.claimable_coupons(c, user_id=user_id, city=user_city, club_id=club_id)


@app.post('/api/public/coupons/{coupon_id}/claim')
def public_claim_coupon(request:Request,coupon_id:int,payload:dict=Body(default={})):
    user_id=public_user_id(request,payload.get('userId'))
    with conn() as c:
        u = row(c.execute('SELECT * FROM users WHERE id=?', (user_id,)))
        user_city = str((u or {}).get('city') or '').strip() or str(payload.get('city') or '').strip()
        try:
            res = benefit_engine.claim_coupon(c, benefit_id=coupon_id, user_id=user_id,
                                              city=user_city, club_id=int(payload.get('clubId') or 0))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {'ok': True, **res}


@app.get('/api/public/coupons/mine')
def public_my_coupons(request:Request,user_id:int|None=None):
    """我的券包：跨俱乐部的全部券（平台券 + 俱乐部券）。"""
    user_id=public_user_id(request,user_id)
    with conn() as c:
        out = benefit_engine.user_redemptions(c, user_id=user_id, club_id=0)
        now = benefit_engine._now()
        for x in out:
            x['expired'] = benefit_engine._expired(x, now=now)
        return out


@app.get('/api/public/coupons/{code}')
def public_coupon_share(code: str):
    """分享详情：按券码查券（不暴露领取者隐私）。"""
    with conn() as c:
        x = benefit_engine.coupon_by_code(c, code)
        if not x:
            raise HTTPException(404, '券码不存在')
        x['expired'] = benefit_engine._expired(x)
        return x


@app.get('/api/public/users/{user_id}/profile')
def public_user_profile(user_id: int):
    """用户基本信息（名字 + 注册城市）。手机号不返回：这是公开接口。"""
    with conn() as c:
        u = row(c.execute('SELECT id,name,city,avatar_url,wechat_avatar_url FROM users WHERE id=?', (user_id,)))
        if not u:
            raise HTTPException(404, '用户不存在')
        return {'id':u['id'],'name':u['name'],'city':u.get('city') or '',
                'avatarUrl':u.get('avatar_url') or '','wechatAvatarUrl':u.get('wechat_avatar_url') or ''}


@app.post('/api/public/users/{user_id}/city')
def public_set_user_city(user_id: int, payload: dict = Body(...)):
    """设置注册城市（城市定向投放按它圈人）。"""
    city = str(payload.get('city') or '').strip()[:24]
    with conn() as c:
        u = row(c.execute('SELECT id FROM users WHERE id=?', (user_id,)))
        if not u:
            raise HTTPException(404, '用户不存在')
        c.execute('UPDATE users SET city=? WHERE id=?', (city or None, user_id))
    return {'ok': True, 'city': city}


_USER_AVATAR_URL_RE=re.compile(r'^/api/public/users/(\d+)/avatar(\.[a-z0-9]{2,5})$', re.I)

@app.post('/api/public/users/{user_id}/avatar')
def public_set_user_avatar(user_id:int, payload:dict=Body(...)):
    """C 端顾客设置自己的头像（自定义上传 / 用微信头像 / 恢复默认）。

    - avatarData: data:image/...;base64,... —— 解码 + PIL 校验 + 裁成 1:1 后落盘
      uploads/users/{id}/avatar{ext}，库里只存活动域代理地址（同领队头像口径，
      生产环境 /static/uploads/* 被封死，裸链是死链）。
    - useWechat:true —— 把已授权的 wechat_avatar_url 复制到 avatar_url（微信头像自动同步入口）。
    - clear:true —— 头像置空，前端回退到首字母。
    """
    with conn() as c:
        u=row(c.execute('SELECT id,avatar_url,wechat_avatar_url FROM users WHERE id=?',(user_id,)))
        if not u: raise HTTPException(404,'用户不存在')
        if payload.get('clear'):
            c.execute('UPDATE users SET avatar_url=NULL WHERE id=?',(user_id,))
            return {'avatarUrl':''}
        if payload.get('useWechat'):
            wx=str(u.get('wechat_avatar_url') or '')
            if not wx: raise HTTPException(409,'还没有微信头像可用，请先在微信中授权')
            c.execute('UPDATE users SET avatar_url=? WHERE id=?',(wx,user_id))
            return {'avatarUrl':wx}
        data=str(payload.get('avatarData') or '')
        m=re.match(r'^data:(image/[a-z0-9.+-]+);base64,', data)
        if not m: raise HTTPException(400,'头像数据格式不正确')
        mime=m.group(1).lower()
        ext_map={'image/png':'.png','image/jpeg':'.jpg','image/jpg':'.jpg','image/webp':'.webp','image/gif':'.gif'}
        ext=ext_map.get(mime)
        if not ext: raise HTTPException(400,'不支持的图片格式')
        try:
            raw=base64.b64decode(data.split(',',1)[1], validate=False)
        except Exception:
            raise HTTPException(400,'头像解码失败')
        if len(raw)>3*1024*1024: raise HTTPException(413,'头像不能超过 3MB')
        try:
            Image.open(io.BytesIO(raw)).verify()
            im=Image.open(io.BytesIO(raw)).convert('RGB')
        except Exception:
            raise HTTPException(400,'头像不是有效的图片文件')
        side=min(im.size)
        box=((im.width-side)//2,(im.height-side)//2,(im.width-side)//2+side,(im.height-side)//2+side)
        im=im.crop(box).resize((256,256))
        dest=UPLOAD/'users'/f'{int(user_id)}'; dest.mkdir(parents=True, exist_ok=True)
        for stale in dest.glob('avatar.*'):
            try: stale.unlink()
            except OSError: pass
        fp=dest/f'avatar{ext}'
        save_ext='PNG' if ext=='.png' else ('JPEG' if ext in ('.jpg','.jpeg') else ('WEBP' if ext=='.webp' else 'GIF'))
        im.save(fp, save_ext)
        url=f'/api/public/users/{int(user_id)}/avatar{ext}'
        c.execute('UPDATE users SET avatar_url=? WHERE id=?',(url,user_id))
        return {'avatarUrl':url}

@app.get('/api/public/users/{user_id}/avatar.{ext}')
def public_user_avatar(user_id:int, ext:str):
    """C 端顾客头像（公开）。磁盘定位与扩展名校验复用 _serve_user_avatar：
    先按形状校验库里存的 URL，再用路由参数拼路径 —— 即便库里被写入畸形值也跳出不了 uploads。"""
    with conn() as c:
        r=row(c.execute('SELECT avatar_url FROM users WHERE id=?',(user_id,)))
    if not r: raise HTTPException(404,'用户不存在')
    return _serve_user_avatar(int(user_id), ext)

def _serve_user_avatar(user_id:int, ext:str):
    with conn() as c:
        r=row(c.execute('SELECT avatar_url FROM users WHERE id=?',(user_id,)))
    if not r: raise HTTPException(404,'用户不存在')
    m=_USER_AVATAR_URL_RE.match(str(r['avatar_url'] or ''))
    if not m or int(m.group(1))!=int(user_id):
        raise HTTPException(404,'还没有设置头像')
    ext='.'+str(ext).lower().lstrip('.')
    if ext not in _PUBLIC_IMAGE_EXT: raise HTTPException(404,'不支持的图片格式')
    fp=UPLOAD/'users'/f'{int(user_id)}'/f'avatar{ext}'
    if not fp.is_file(): raise HTTPException(404,'头像文件已丢失')
    return FileResponse(fp)


@app.get('/api/platform/commerce/status')
def platform_commerce_status():
    st=commerce_status()
    return {'provider':st.provider,'configured':st.configured,'reachable':st.reachable,'url':st.url,'note':st.note}

@app.get('/api/platform/products')
def platform_products(brand_id:int|None=None, category_id:int|None=None,
                      gender:str|None=None, q:str|None=None, status:str|None=None):
    # 规格数与图数一起带出来：后台列表要能一眼看出哪些商品还没维护多图/规格，
    # 逐个商品回查会退化成 N+1。
    where=['1=1']; args=[]
    # 筛选走 SQL：商品池是全局的，多品牌之后一定会有几百个 SKU，
    # 全量拉到后台再筛会把页面拖死，也让「按品牌看货」这个最常用动作变得不可用。
    if brand_id: where.append('p.brand_id=?'); args.append(int(brand_id))
    if category_id: where.append('p.category_id=?'); args.append(int(category_id))
    if gender: where.append('p.gender=?'); args.append(catalog_service.normalize_gender(gender))
    if status: where.append('p.status=?'); args.append(str(status))
    if q:
        kw=str(q).strip()
        if kw:
            where.append("(p.name LIKE ? OR p.sku LIKE ? OR IFNULL(p.series,'') LIKE ?)")
            args += ['%'+kw+'%','%'+kw+'%','%'+kw+'%']
    sql='''SELECT p.*,
        (SELECT name FROM product_brands b WHERE b.id=p.brand_id) brand_name,
        (SELECT name FROM product_categories ct WHERE ct.id=p.category_id) category_name,
        (SELECT name FROM product_categories pc WHERE pc.id=(SELECT parent_id FROM product_categories ct WHERE ct.id=p.category_id)) parent_category_name,
        (SELECT COUNT(*) FROM product_variants v WHERE v.product_id=p.id AND v.status='active') variants_count,
        (SELECT COUNT(*) FROM product_images i WHERE i.product_id=p.id) images_count,
        (SELECT COALESCE(SUM(on_order),0) FROM warehouse_inventory wi WHERE wi.product_id=p.id) on_order
      FROM products p WHERE %s ORDER BY p.id DESC''' % ' AND '.join(where)
    with conn() as c:
        out=rows(c.execute(sql,args))
        # 进销存 V1：派生库存状态（与库存状态机分离，商品上架仍可显示售罄）
        for r in out:
            r['inventory_status']=_inventory_status(r)
        return out


def _inventory_status(p:dict) -> str:
    """根据汇总库存 / 补货点 / 在途，派生库存状态（与商品上架状态分离）。"""
    stock=int(p.get('stock') or 0); rp=int(p.get('reorder_point') or 0); oo=int(p.get('on_order') or 0)
    if stock<=0: return 'out_of_stock'
    if oo>0 and stock<=rp: return 'restocking'
    if stock<=rp: return 'low_stock'
    return 'in_stock'


def _resync_product_stock(c, product_id:int):
    """把 products.stock 重算成各在售规格库存之和。

    它现在是冗余字段（真源在 product_variants），只要规格有任何增删改都要跟着变，
    否则后台列表看到的汇总和 C 端详情页看到的规格明细会对不上。
    """
    total=c.execute("SELECT COALESCE(SUM(stock),0) s FROM product_variants WHERE product_id=? AND status='active'",(int(product_id),)).fetchone()['s']
    c.execute('UPDATE products SET stock=? WHERE id=?',(int(total),int(product_id)))
    return int(total)


def _mirror_pool_delta(c,product_id:int,delta:int,*,ref:str,note:str=''):
    _owned_product(c,product_id)
    for v in rows(c.execute('SELECT id,stock FROM product_variants WHERE product_id=?',(product_id,))):
        actual=int(c.execute('SELECT COALESCE(SUM(on_hand-reserved-unsellable),0) FROM warehouse_sku_balances WHERE variant_id=?',(v['id'],)).fetchone()[0])
        diff=int(v['stock'])-actual
        if diff:
            c.execute('UPDATE product_variants SET stock=? WHERE id=?',(actual,v['id']))
            inventory_engine.adjust_stock(c,product_id=product_id,variant_id=v['id'],quantity_delta=diff,reason=note or '规格期初调整',reference_type='variant_pool_edit',reference_id=ref)

def _resync_product_stock_warehouse_aware(c, product_id:int):
    """V1.2：有 WMS 时按真源反转口径收口（可售=派生），否则退回汇总重算。"""
    if inventory_engine.warehouse:
        from clubos_domain.product_stock import resync_sellable_from_wms as _rsync
        _rsync(c,product_id=product_id)
    else:
        _resync_product_stock(c,product_id)

@app.get('/api/platform/stock/consistency')
def platform_stock_consistency():
    """库存一致性自检（V1.2 真源反转口径）。

    V1.2 起：warehouse_inventory.on_hand 是实物真源，product_variants.stock 是
    可售派生（= Σon_hand − Σreserved − Σunsellable）。本端点比对：
      ① products.stock 是否等于在售规格库存之和（列表汇总冗余校验）；
      ② 各规格可售之和是否等于 WMS 可售总额（实物 − 锁定 − 不可售）。
    返回所有不一致的商品与差值；修复由各触发点的 resync_sellable_from_wms 负责。
    """
    with conn() as c:
        rows_out=[]
        for p in c.execute('SELECT id,name,sku,stock,status FROM products ORDER BY id').fetchall():
            p=dict(p)
            variant_sum=int(c.execute("SELECT COALESCE(SUM(stock),0) FROM product_variants WHERE product_id=? AND status='active'",(p['id'],)).fetchone()[0])
            wms_sellable=int(c.execute('SELECT COALESCE(SUM(on_hand-reserved-unsellable),0) FROM warehouse_inventory WHERE product_id=?',(p['id'],)).fetchone()[0])
            wms_sum=int(c.execute('SELECT COALESCE(SUM(on_hand),0) FROM warehouse_inventory WHERE product_id=?',(p['id'],)).fetchone()[0])
            diff_v=variant_sum-int(p['stock'] or 0)
            diff_w=variant_sum-max(0,wms_sellable)
            if diff_v!=0 or diff_w!=0:
                rows_out.append({'id':p['id'],'name':p['name'],'sku':p['sku'],'status':p['status'],
                                 'productsStock':int(p['stock'] or 0),'variantSum':variant_sum,
                                 'wmsSum':wms_sum,'wmsSellable':max(0,wms_sellable),
                                 'diffVsProducts':diff_v,'diffVsWms':diff_w})
        return {'ok':True,'mismatches':rows_out,'count':len(rows_out)}


@app.get('/api/platform/products/{product_id}/variants')
def platform_variants(product_id:int):
    with conn() as c:
        return rows(c.execute("SELECT id,product_id,name,sku,price,stock,status,sort,is_default,color,color_hex,size,barcode,settlement_price FROM product_variants WHERE product_id=? ORDER BY sort,id",(product_id,)))

@app.post('/api/platform/products/{product_id}/variants')
def add_product_variant(product_id:int, payload:dict=Body(...)):
    name=str(payload.get('name') or '').strip()
    # 颜色 × 尺码都给了就自动拼显示名（"炭灰 / M"）：只要求手填 name 的话，
    # 运营填的颜色和 name 迟早对不上，而 C 端按钮显示的是 name。
    color=catalog_service.normalize_dimension(payload.get('color'))
    size=catalog_service.normalize_dimension(payload.get('size'))
    if not name and (color or size): name=' / '.join([x for x in (color,size) if x])
    if not name: raise HTTPException(400,'规格名不能为空，例如 S / M / L、炭灰')
    with conn() as c:
        p=c.execute('SELECT id,brand_id,category_id FROM products WHERE id=?',(product_id,)).fetchone()
        if not p: raise HTTPException(404,'商品不存在')
        price=payload.get('price'); price=float(price) if price not in (None,'') else None
        stock=max(0,int(payload.get('stock') or 0))
        barcode=catalog_service.normalize_dimension(payload.get('barcode'))
        color_hex=catalog_service.normalize_color_hex(payload.get('colorHex'))
        # SKU 自动生成（品牌-品类-商品-颜色-尺码）。手填在多品牌下必然撞号，
        # 撞号在采购入库和库存流水里是查不出的数据事故。
        sku=catalog_service.ensure_unique_sku(c,
            catalog_service.build_sku(c,product_id=int(product_id),brand_id=p['brand_id'],
                                      category_id=p['category_id'],color=color or '',size=size or ''))
        # 商品原本只有迁移时生成的那个「默认」规格：加了真实规格后它就该退场，
        # 否则 C 端会把它和 S/M/L 并排显示成四个规格。它的库存转给第一个新规格，
        # 不让库存凭空消失 —— 运营随后再按规格分配。
        dflt=c.execute("SELECT id,stock FROM product_variants WHERE product_id=? AND is_default=1 AND status='active'",(product_id,)).fetchone()
        others=c.execute("SELECT COUNT(*) n FROM product_variants WHERE product_id=? AND status='active' AND is_default=0",(product_id,)).fetchone()['n']
        moved=0
        if dflt and others==0:
            moved=int(dflt['stock'] or 0)
            c.execute("UPDATE product_variants SET status='inactive',stock=0 WHERE id=?",(dflt['id'],))
        effective=int(stock or 0) or moved
        mx=c.execute("SELECT COALESCE(MAX(sort),0) m FROM product_variants WHERE product_id=?",(product_id,)).fetchone()['m']
        # V1.2 固定结算价（品牌 fixed_price 模式逐 SKU 结算用）
        settlement_price=max(0.0,float(payload.get('settlementPrice') or 0))
        # stock 落 0，期初库存随后统一走 adjust_stock（实物台账 → 可售派生）链路，
        # 不再直写规格 —— 直写会让 Σvariant 与 WMS 脱钩（真源反转口径）。
        c.execute('INSERT INTO product_variants(product_id,name,sku,price,stock,sort,status,color,color_hex,size,barcode,settlement_price) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  (product_id,name,sku,price,0,int(mx)+1,'active',color,color_hex,size,barcode,settlement_price))
        vid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        # V1.2：规格池净变化镜像到 WMS 实物台账 —— 占位规格退场是池内转移，WMS 净 −moved；
        # 期初 effective 由 adjust_stock 落实物。两条合计 = 池净变化，账实永远对齐。
        _mirror_pool_delta(c,product_id,-moved,ref=f'variant:{vid}:retire',note='占位规格退场，库存转移')
        if effective:
            try:
                inventory_engine.adjust_stock(c,product_id=product_id,quantity_delta=effective,reason='新增规格期初库存',
                                              actor_type='platform',reference_type='variant_opening',reference_id=str(vid),variant_id=vid)
            except ValueError as e: raise HTTPException(409,str(e))
        _resync_product_stock_warehouse_aware(c,product_id)
        return {'ok':True,'id':vid,'movedStock':moved}

@app.patch('/api/platform/products/{product_id}/variants/{variant_id}')
def patch_product_variant(product_id:int, variant_id:int, payload:dict=Body(...)):
    with conn() as c:
        v=c.execute('SELECT * FROM product_variants WHERE id=? AND product_id=?',(variant_id,product_id)).fetchone()
        if not v: raise HTTPException(404,'规格不存在')
        sets=[];vals=[]
        for k,col in (('name','name'),('sku','sku'),('sort','sort'),('status','status')):
            if payload.get(k) is not None: sets.append(col+'=?');vals.append(payload[k])
        for k,col in (('color','color'),('size','size'),('barcode','barcode')):
            if payload.get(k) is not None: sets.append(col+'=?');vals.append(catalog_service.normalize_dimension(payload[k]))
        if payload.get('colorHex') is not None:
            sets.append('color_hex=?');vals.append(catalog_service.normalize_color_hex(payload['colorHex']))
        if payload.get('price') is not None:
            raw=payload['price']; sets.append('price=?');vals.append(float(raw) if raw!='' else None)
        if payload.get('settlementPrice') is not None:
            sets.append('settlement_price=?');vals.append(max(0.0,float(payload.get('settlementPrice') or 0)))  # V1.2 固定结算价
        # V1.2 真源反转：规格库存不再直写 —— 按差量走 adjust_stock（实物台账+流水+派生收口）。
        stock_delta=0
        if payload.get('stock') is not None:
            stock_delta=max(0,int(payload['stock']))-int(v['stock'] or 0)
            if stock_delta:
                try:
                    inventory_engine.adjust_stock(c,product_id=product_id,quantity_delta=stock_delta,reason='规格库存调整',
                                                  actor_type='platform',reference_type='variant_stock_edit',
                                                  reference_id=str(variant_id),variant_id=variant_id)
                except ValueError as e: raise HTTPException(409,str(e))
        if not sets and stock_delta==0: raise HTTPException(400,'没有要修改的字段')
        vals+= [variant_id]
        c.execute('UPDATE product_variants SET '+(', '.join(sets))+' WHERE id=?',vals)
        _resync_product_stock(c,product_id)
        return {'ok':True}

@app.delete('/api/platform/products/{product_id}/variants/{variant_id}')
def delete_product_variant(product_id:int, variant_id:int):
    with conn() as c:
        if c.execute('SELECT 1 FROM warehouse_sku_balances WHERE variant_id=?',(variant_id,)).fetchone() or c.execute('SELECT 1 FROM gear_order_items WHERE variant_id=?',(variant_id,)).fetchone():
            raise HTTPException(409,'该 SKU 有库存或交易历史，请停用保留记录，不能删除')
        v=c.execute('SELECT * FROM product_variants WHERE id=? AND product_id=?',(variant_id,product_id)).fetchone()
        if not v: raise HTTPException(404,'规格不存在')
        c.execute('DELETE FROM product_variants WHERE id=?',(variant_id,))
        # 删到只剩 0 个规格时补回一个「默认」：可售库存派生自 WMS 实物（真源反转），
        # 一个规格都没有的商品在 C 端会读不到库存、直接变成缺货。
        left=c.execute("SELECT COUNT(*) n FROM product_variants WHERE product_id=? AND status='active'",(product_id,)).fetchone()['n']
        if left==0:
            p=c.execute('SELECT sku FROM products WHERE id=?',(product_id,)).fetchone()
            # V1.2：stock 先落 0，规格池空缺由紧随的派生收口从 WMS 可售总额回填
            #（实物没消失，删规格只是池内转移 —— 旧口径里靠 products.stock 旧值承接）。
            c.execute("INSERT INTO product_variants(product_id,name,sku,stock,sort,status,is_default) VALUES(?,?,?,0,0,'active',1)",
                      (product_id,'默认',p['sku']))
        _resync_product_stock_warehouse_aware(c,product_id)
        return {'ok':True,'restoredDefault':left==0}

# ── 商品图片：头图 + 详情图集（本地批量上传 + 服务端落盘）──────────────────
#   旧逻辑只有「配图片地址」文本框（POST /images 传 url），运营得自己先想办法把图
#   传到某个地方再粘地址，体验割裂。现在改成：直接从本地选文件 → 服务端落盘到
#   static/uploads/platform/ → 写库。头图（cover_url）与图集（product_images）解耦，
#   头图优先作为 C 端首图，可单独设也可从图集里「设为主图」。
async def _save_platform_upload(file:UploadFile)->str|None:
    """落盘一张平台上传的图片，返回可访问 URL；非图片 / 超限返回 None。"""
    ext=Path(file.filename or '').suffix.lower()
    if ext not in _PUBLIC_IMAGE_EXT: return None
    data=await file.read()
    if len(data) > 10*1024*1024: return None
    batch=UPLOAD/'platform'/uuid.uuid4().hex
    batch.mkdir(parents=True, exist_ok=True)
    dest=batch/f'img{ext}'
    dest.write_bytes(data)
    return f'/static/uploads/platform/{batch.name}/img{ext}'

@app.post('/api/platform/uploads/image')
async def upload_platform_image(file:UploadFile=File(...)):
    # 商品「介绍/详情」里的插图：只落盘并返回 URL，不挂进商品图集。
    # 与 /images/batch 的区别：那个会追加进 product_images，C 端顶部轮播会再出现一次；
    # 介绍里的插图不该占用主图轮播，所以单独走这个接口。
    url=await _save_platform_upload(file)
    if not url: raise HTTPException(400,'只支持图片文件（png/jpg/jpeg/webp/gif），单张上限 10MB')
    return {'url':url}

@app.get('/api/platform/products/{product_id}/images')
def platform_product_images(product_id:int):
    with conn() as c:
        p=row(c.execute('SELECT cover_url FROM products WHERE id=?',(product_id,)))
        if not p: raise HTTPException(404,'商品不存在')
        imgs=rows(c.execute('SELECT id,product_id,url,sort FROM product_images WHERE product_id=? ORDER BY sort,id',(product_id,)))
        return {'images':imgs,'cover':p['cover_url']}

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
        if n==0:
            c.execute('UPDATE products SET image_url=? WHERE id=?',(url,product_id))
            if not c.execute('SELECT cover_url FROM products WHERE id=?',(product_id,)).fetchone()['cover_url']:
                c.execute('UPDATE products SET cover_url=? WHERE id=?',(url,product_id))
        return {'ok':True,'id':iid}

@app.post('/api/platform/products/{product_id}/cover')
async def upload_product_cover(product_id:int, file:UploadFile=File(...)):
    """头图：直接本地选文件上传，服务端落盘并写回 cover_url + image_url。"""
    with conn() as c:
        if not c.execute('SELECT id FROM products WHERE id=?',(product_id,)).fetchone():
            raise HTTPException(404,'商品不存在')
    url=await _save_platform_upload(file)
    if not url: raise HTTPException(400,'只支持图片文件（png/jpg/jpeg/webp/gif），单张上限 10MB')
    with conn() as c:
        c.execute('UPDATE products SET cover_url=?,image_url=? WHERE id=?',(url,url,product_id))
    return {'cover':url}

@app.post('/api/platform/products/{product_id}/images/batch')
async def upload_product_images_batch(product_id:int, files:list[UploadFile]=File(...)):
    """详情图集：本地批量选多张 → 一次性落盘并追加到图集；若还没有头图则首张顶上。"""
    with conn() as c:
        p=row(c.execute('SELECT id,cover_url FROM products WHERE id=?',(product_id,)))
        if not p: raise HTTPException(404,'商品不存在')
    urls=[]
    for f in files:
        u=await _save_platform_upload(f)
        if u: urls.append(u)
    if not urls: raise HTTPException(400,'没有可保存的图片文件（只支持 png/jpg/jpeg/webp/gif，单张上限 10MB）')
    inserted=0; cover=None
    with conn() as c:
        n=c.execute('SELECT COUNT(*) n FROM product_images WHERE product_id=?',(product_id,)).fetchone()['n']
        for i,u in enumerate(urls):
            c.execute('INSERT INTO product_images(product_id,url,sort) VALUES(?,?,?)',(product_id,u,int(n+i)))
            inserted+=1
        # 还没有头图时，首张详情图自动顶成头图（含 image_url 兜底）。
        if not p['cover_url']:
            cover=urls[0]
            c.execute('UPDATE products SET cover_url=?,image_url=? WHERE id=?',(cover,cover,product_id))
    return {'inserted':inserted,'cover':cover,'urls':urls}

@app.patch('/api/platform/products/{product_id}/images/{image_id}')
def patch_product_image(product_id:int, image_id:int, payload:dict=Body(...)):
    """单张图操作：setCover=把这张设为主图（写 cover_url）；否则按 sort 重排。"""
    with conn() as c:
        r=row(c.execute('SELECT id,url FROM product_images WHERE id=? AND product_id=?',(image_id,product_id)))
        if not r: raise HTTPException(404,'图片不存在')
        if payload.get('setCover'):
            c.execute('UPDATE products SET cover_url=?,image_url=? WHERE id=?',(r['url'],r['url'],product_id))
        elif 'sort' in payload and payload['sort'] is not None:
            c.execute('UPDATE product_images SET sort=? WHERE id=?',(int(payload['sort']),image_id))
        return {'ok':True}

@app.delete('/api/platform/products/{product_id}/images/{image_id}')
def delete_product_image(product_id:int, image_id:int):
    with conn() as c:
        r=row(c.execute('SELECT url FROM product_images WHERE id=? AND product_id=?',(image_id,product_id)))
        if not r: raise HTTPException(404,'图片不存在')
        c.execute('DELETE FROM product_images WHERE id=? AND product_id=?',(image_id,product_id))
        # 删掉的是当前头图 → 头图回落到图集第一张（都没有就清空）。
        if r['url']==c.execute('SELECT cover_url FROM products WHERE id=?',(product_id,)).fetchone()['cover_url']:
            first=c.execute('SELECT url FROM product_images WHERE product_id=? ORDER BY sort,id LIMIT 1',(product_id,)).fetchone()
            new_cover=first['url'] if first else None
            c.execute('UPDATE products SET cover_url=?,image_url=? WHERE id=?',(new_cover,new_cover,product_id))
        return {'ok':True}


# ==========================================================================
# 商品目录：品牌 / 品类（2026-10-24）
# 多品牌上架要求品牌与品类是字典而不是自由文本 —— 否则同一个品牌会被写成
# 好几种写法，按品牌筛选永远筛不全。字典的增删改集中在这里，商品只存外键。
# ==========================================================================
@app.get('/api/platform/brands')
def platform_brands():
    with conn() as c: return catalog_service.list_brands(c, include_inactive=True)

def _peaktrace_auto_sync_enabled():
    return os.environ.get('PEAKTRACE_AUTO_SYNC','0')=='1'


def _peaktrace_auto_loop():
    """可选自动同步（PEAKTRACE_AUTO_SYNC=1）：上游上架/调价/改库存后无需人工点按钮。

    守护线程、异常吞掉并继续下一轮；默认关闭，避免测试进程与未配置的实例访问外网。
    PEAKTRACE_SYNC_TTL 控制目录轮询间隔（默认 900 秒）；
    PEAKTRACE_FULFILL_TTL 控制外部履约推送/轮询间隔（默认 120 秒 —— 发货状态
    用户在等，比目录同步急得多）。两个循环合并跑一个线程，互不阻塞失败。
    """
    import time
    time.sleep(20)   # 启动缓冲：等数据库初始化与网络就绪
    catalog_ttl=max(60,int(os.environ.get('PEAKTRACE_SYNC_TTL','900')))
    fulfill_ttl=max(30,int(os.environ.get('PEAKTRACE_FULFILL_TTL','120')))
    next_catalog=0.0
    while True:
        now=time.time()
        if now>=next_catalog:
            try:
                payload=peaktrace_catalog.fetch_catalog()
                with conn() as c:
                    peaktrace_catalog.sync_catalog(c,payload)
            except Exception as e:
                print('[peaktrace] auto sync skipped:',type(e).__name__,str(e)[:160])
            next_catalog=now+catalog_ttl
        # 外部履约桥：待推订单推上游 + 已推订单拉发货状态（各自独立失败）
        try:
            with conn() as c: due=external_fulfillment.due_pushes(c)
            outs=external_fulfillment.push_orders(due)
            with conn() as c: external_fulfillment.apply_push_results(c,outs)
        except Exception as e:
            print('[peaktrace] fulfillment push skipped:',type(e).__name__,str(e)[:160])
        try:
            with conn() as c: pulls=external_fulfillment.due_pulls(c)
            outs=external_fulfillment.pull_orders(pulls)
            with conn() as c: external_fulfillment.apply_pull_results(c,outs)
        except Exception as e:
            print('[peaktrace] fulfillment pull skipped:',type(e).__name__,str(e)[:160])
        try:
            time.sleep(fulfill_ttl)
        except Exception:
            time.sleep(120)


if _peaktrace_auto_sync_enabled():
    import threading
    threading.Thread(target=_peaktrace_auto_loop,name='peaktrace-mirror-sync',daemon=True).start()


def _auto_confirm_enabled() -> bool:
    """默认开启 10 天自动确认；AUTO_CONFIRM_RECEIPT_DAYS=0 关闭。"""
    try:
        return int(os.environ.get('AUTO_CONFIRM_RECEIPT_DAYS', '10')) > 0
    except ValueError:
        return True


def _auto_confirm_loop():
    """自动确认收货（默认开，AUTO_CONFIRM_RECEIPT_DAYS 天；设 0 关闭）。

    与 PEAKTRACE 自动同步 (_peaktrace_auto_loop) 解耦：后者受 PEAKTRACE_AUTO_SYNC
    门控且生产环境关闭，不能依赖它推进订单终态。本循环独立常驻，扫描所有
    status='shipped' 且 shipped_at 超过阈值的订单（自营与 PEAKTRACE 代发一视同仁），
    调用 _complete_receipt 完成签收 + 冻结佣金。

    注意：PEAKTRACE 代发的「物流妥投」(fulfill_status=delivered) 只是待确认，订单主
    状态仍是 shipped，同样会落入本扫描；这样即便用户忘了点「确认收货」，10 天后系统
    也会自动确认，交易闭环不会卡死。
    """
    import time
    time.sleep(30)  # 启动缓冲
    while True:
        try:
            if _auto_confirm_enabled():
                days = max(1, int(os.environ.get('AUTO_CONFIRM_RECEIPT_DAYS', '10')))
                mod = f'-{days} days'
                with conn() as c:
                    due = rows(c.execute(
                        """SELECT id FROM gear_orders
                           WHERE status='shipped' AND shipped_at IS NOT NULL
                             AND shipped_at <= datetime('now', ?)""", (mod,)))
                for r in due:
                    try:
                        with conn() as c2:
                            _complete_receipt(c2, int(r['id']))
                        print(f'[auto-confirm] order {r["id"]} -> delivered (auto)')
                    except Exception as e:
                        print('[auto-confirm] skipped', r['id'], type(e).__name__, str(e)[:120])
        except Exception as e:
            print('[auto-confirm] loop error:', type(e).__name__, str(e)[:160])
        try:
            time.sleep(max(300, int(os.environ.get('AUTO_CONFIRM_INTERVAL', '3600'))))
        except Exception:
            time.sleep(3600)


if _auto_confirm_enabled():
    import threading
    threading.Thread(target=_auto_confirm_loop, name='auto-confirm-receipt', daemon=True).start()


@app.get('/api/platform/brands/peaktrace/status')
def platform_peaktrace_status():
    """给平台端显示「上次同步时间 / 已接通商品数」，不需要访问上游。"""
    with conn() as c:
        m=row(c.execute("""SELECT COUNT(*) n,MAX(last_inventory_sync_at) last_at
                            FROM external_product_mappings
                            WHERE external_sku_id='' AND external_sync_status='stock_synced'"""))
        gone=row(c.execute("""SELECT COUNT(*) n FROM external_product_mappings
                               WHERE external_sku_id='' AND external_sync_status='catalog_unavailable'"""))
    return {'ok':True,'origin':peaktrace_catalog.ORIGIN,
            'inventoryConnected':True,'autoSync':_peaktrace_auto_sync_enabled(),
            'syncedProducts':int((m or {}).get('n') or 0),'lastSyncAt':(m or {}).get('last_at'),
            'upstreamRemoved':int((gone or {}).get('n') or 0)}


@app.post('/api/platform/brands/peaktrace/sync')
def platform_sync_peaktrace():
    """PEAKTRACE 主数据全量镜像：上游上架什么就卖什么，资料与库存都以上游为准。"""
    try: payload=peaktrace_catalog.fetch_catalog()
    except Exception as e:
        raise HTTPException(502,'PEAKTRACE 商品数据读取失败，请稍后重试；本地商品未改动（%s）' % type(e).__name__)
    with conn() as c:
        try:return peaktrace_catalog.sync_catalog(c,payload)
        except ValueError as e:raise HTTPException(409,str(e))

@app.post('/api/platform/brands')
def platform_add_brand(payload:dict=Body(...)):
    with conn() as c:
        try:
            bid=catalog_service.create_brand(c,name=str(payload.get('name') or ''),
                                             logo_url=payload.get('logoUrl'),
                                             sort=payload.get('sort') or 0,
                                             name_en=payload.get('nameEn'),
                                             intro=payload.get('intro'),
                                             industry=payload.get('industry'),
                                             official_website=payload.get('officialWebsite'),
                                             country=payload.get('country'),
                                             contact_name=payload.get('contactName'),
                                             contact_phone=payload.get('contactPhone'),
                                             cooperation_status=payload.get('cooperationStatus') or 'pending',
                                             source_type=payload.get('sourceType') or 'platform_self',
                                             mall_display_status=payload.get('mallDisplayStatus') or 'show',
                                             settlement_mode=payload.get('settlementMode'),
                                             settlement_ratio=payload.get('settlementRatio'))
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True,'id':bid}

@app.patch('/api/platform/brands/{brand_id}')
def platform_patch_brand(brand_id:int, payload:dict=Body(...)):
    with conn() as c:
        try:
            catalog_service.update_brand(c,brand_id,name=payload.get('name'),
                                         logo_url=payload.get('logoUrl'),
                                         sort=payload.get('sort'),status=payload.get('status'),
                                         name_en=payload.get('nameEn'),
                                         intro=payload.get('intro'),
                                         industry=payload.get('industry'),
                                         official_website=payload.get('officialWebsite'),
                                         country=payload.get('country'),
                                         contact_name=payload.get('contactName'),
                                         contact_phone=payload.get('contactPhone'),
                                         cooperation_status=payload.get('cooperationStatus'),
                                         source_type=payload.get('sourceType'),
                                         mall_display_status=payload.get('mallDisplayStatus'),
                                         settlement_mode=payload.get('settlementMode'),
                                         settlement_ratio=payload.get('settlementRatio'))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True}

@app.delete('/api/platform/brands/{brand_id}')
def platform_delete_brand(brand_id:int):
    """删品牌会把引用它的商品的 brand_id 置空（不删商品）。

    悬空外键比「删不掉」危险得多：列表品牌列一片空白、筛选也筛不到，
    而商品本身看起来一切正常。
    """
    with conn() as c:
        try: out=catalog_service.delete_brand(c,brand_id)
        except LookupError as e: raise HTTPException(404,str(e))
    return {'ok':True,**out}

# ───────────────────────── 供应商↔品牌关联（进销存 V1） ─────────────────────────
@app.get('/api/platform/brands/{brand_id}/suppliers')
def platform_brand_suppliers(brand_id:int):
    with conn() as c:
        if not c.execute('SELECT id FROM product_brands WHERE id=?',(brand_id,)).fetchone():
            raise HTTPException(404,'品牌不存在')
        return rows(c.execute("""SELECT bs.id,bs.brand_id,bs.supplier_id,bs.is_primary,bs.note,bs.created_at,
                                 s.code,s.name,s.contact_name,s.phone,s.status supplier_status
                                FROM brand_suppliers bs JOIN suppliers s ON s.id=bs.supplier_id
                                WHERE bs.brand_id=? ORDER BY bs.is_primary DESC,bs.id""",(brand_id,)))

@app.post('/api/platform/brands/{brand_id}/suppliers')
def platform_brand_add_supplier(brand_id:int, payload:dict=Body(...)):
    sid=int(payload.get('supplierId') or 0)
    if sid<=0: raise HTTPException(400,'supplierId 必填')
    is_primary=1 if payload.get('isPrimary') else 0
    note=str(payload.get('note') or '')[:200]
    with conn() as c:
        if not c.execute('SELECT id FROM product_brands WHERE id=?',(brand_id,)).fetchone():
            raise HTTPException(404,'品牌不存在')
        if not c.execute('SELECT id FROM suppliers WHERE id=?',(sid,)).fetchone():
            raise HTTPException(404,'供应商不存在')
        if c.execute('SELECT id FROM brand_suppliers WHERE brand_id=? AND supplier_id=?',(brand_id,sid)).fetchone():
            raise HTTPException(409,'该供应商已关联到本品牌')
        # 设为主供应商时，先把同品牌其它关联降级
        if is_primary:
            c.execute('UPDATE brand_suppliers SET is_primary=0 WHERE brand_id=?',(brand_id,))
        c.execute('INSERT INTO brand_suppliers(brand_id,supplier_id,is_primary,note) VALUES(?,?,?,?)',
                  (brand_id,sid,is_primary,note))
        new_id=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
    # 注意：last_insert_rowid 必须在 with 块内取 —— conn() 退出即关闭连接，
    # 出块后再用 c 会抛 ProgrammingError 打成 500。
    return {'ok':True,'id':new_id}

@app.delete('/api/platform/brands/{brand_id}/suppliers/{supplier_id}')
def platform_brand_remove_supplier(brand_id:int, supplier_id:int):
    with conn() as c:
        c.execute('DELETE FROM brand_suppliers WHERE brand_id=? AND supplier_id=?',(brand_id,supplier_id))
    return {'ok':True}

@app.get('/api/platform/suppliers/{supplier_id}/brands')
def platform_supplier_brands(supplier_id:int):
    """入库弹窗「品牌按供应商选」的数据源（2026-11-01 用户反馈）：
    采购单已选定供应商，品牌下拉只应出现该供应商在品牌管理里关联的品牌，
    而不是全量品牌池 —— 货是谁供的，品牌就该是谁家的。反查 brand_suppliers。"""
    with conn() as c:
        if not c.execute('SELECT id FROM suppliers WHERE id=?',(supplier_id,)).fetchone():
            raise HTTPException(404,'供应商不存在')
        return rows(c.execute("""SELECT bs.id,bs.is_primary,bs.note,
                                 b.id AS brand_id,b.name,b.slug,b.status
                                FROM brand_suppliers bs JOIN product_brands b ON b.id=bs.brand_id
                                WHERE bs.supplier_id=?
                                ORDER BY bs.is_primary DESC,b.name""",(supplier_id,)))

# ───────────────────────── 品牌结算（进销存 V1，结构 + 查看 + 生成草稿） ─────────────────────────
@app.get('/api/platform/brands/{brand_id}/settlements')
def platform_brand_settlements(brand_id:int, status:str|None=None):
    with conn() as c:
        if not c.execute('SELECT id FROM product_brands WHERE id=?',(brand_id,)).fetchone():
            raise HTTPException(404,'品牌不存在')
        q='SELECT * FROM brand_settlements WHERE brand_id=?'; args=[brand_id]
        if status: q+=' AND status=?'; args.append(status)
        items=rows(c.execute(q+' ORDER BY id DESC',args))
        summary=dict(c.execute('SELECT COALESCE(SUM(sales_amount),0) sales,COALESCE(SUM(settlement_amount),0) settle,COUNT(*) n FROM brand_settlements WHERE brand_id=?',(brand_id,)).fetchone())
        return {'items':items,'summary':summary}

@app.post('/api/platform/brands/{brand_id}/settlements/generate')
def platform_brand_settlement_generate(brand_id:int, payload:dict=Body(...)):
    """按时间段聚合该品牌下所有商品的销售额，生成一张草稿结算单（V1 不自动打款）。

    V1.2 结算模式（品牌档案里配置）：
      - sales_ratio（按销售额比例）：品牌应结 = 销售额 × settlement_ratio%；
        ratio 未配置（0）时回落到请求里的 platformFeeRate 老口径（settle = sales × (1−fee)）。
      - fixed_price（固定结算价）：品牌应结 = Σ(件数 × 规格结算价 settlement_price)，
        规格没设结算价的行按成交单价快照兜底。
    """
    pf=float(payload.get('platformFeeRate') or 0.08)
    period_from=str(payload.get('periodFrom') or '').strip() or None
    period_to=str(payload.get('periodTo') or '').strip() or None
    with conn() as c:
        b=c.execute('SELECT * FROM product_brands WHERE id=?',(brand_id,)).fetchone()
        if not b: raise HTTPException(404,'品牌不存在')
        wc=['oi.product_id IN (SELECT id FROM products WHERE brand_id=?)']; wa=[brand_id]
        if period_from: wc.append("o.created_at>=?"); wa.append(period_from)
        if period_to: wc.append("o.created_at<=?"); wa.append(period_to)
        sales=float(c.execute(f"""SELECT COALESCE(SUM(oi.unit_price*oi.quantity),0)
                                  FROM gear_order_items oi JOIN gear_orders o ON o.id=oi.order_id
                                  WHERE {' AND '.join(wc)}""",wa).fetchone()[0])
        mode=str(b['settlement_mode'] or 'sales_ratio')
        if mode=='fixed_price':
            settled=float(c.execute(f"""SELECT COALESCE(SUM(oi.quantity * COALESCE(NULLIF(v.settlement_price,0), oi.unit_price, 0)),0)
                                  FROM gear_order_items oi
                                  JOIN gear_orders o ON o.id=oi.order_id
                                  LEFT JOIN product_variants v ON v.id=oi.variant_id
                                  WHERE {' AND '.join(wc)}""",wa).fetchone()[0])
            settle=round(settled,2)
        else:
            ratio=float(b['settlement_ratio'] or 0)
            if ratio>0: settle=round(sales*ratio/100,2)
            else: settle=round(sales*(1.0-pf),2)
        fee=round(sales-settle,2)
        ts=datetime.utcnow().strftime('%Y%m%d%H%M%S')
        no='BS-%d-%s' % (brand_id, ts)
        c.execute("""INSERT INTO brand_settlements(brand_id,settlement_no,period_from,period_to,sales_amount,platform_fee,settlement_amount,status)
                     VALUES(?,?,?,?,?,?,?,?)""",(brand_id,no,period_from,period_to,sales,fee,settle,'draft'))
        return {'ok':True,'id':int(c.execute('SELECT last_insert_rowid()').fetchone()[0]),'sales_amount':sales,'platform_fee':fee,'settlement_amount':settle,'mode':mode}

@app.patch('/api/platform/brand-settlements/{settlement_id}')
def platform_brand_settlement_patch(settlement_id:int, payload:dict=Body(...)):
    allowed={'status','settlement_amount','platform_fee','sales_amount','note','settled_at'}
    sets=[]; vals=[]
    with conn() as c:
        s=c.execute('SELECT * FROM brand_settlements WHERE id=?',(settlement_id,)).fetchone()
        if not s: raise HTTPException(404,'结算单不存在')
        for k in allowed:
            if k in payload and payload[k] is not None:
                if k=='status' and payload[k] not in ('draft','confirmed','paid'):
                    raise HTTPException(400,'status 只能是 draft / confirmed / paid')
                sets.append(f'{k}=?'); vals.append(payload[k])
        if not sets: return {'ok':True}
        c.execute('UPDATE brand_settlements SET %s WHERE id=?' % ','.join(sets), vals+[settlement_id])
    return {'ok':True}

@app.post('/api/platform/uploads/image')
async def platform_upload_image(file:UploadFile=File(...)):
    """平台端通用图片上传（2026-10-26）：品牌 Logo 等先传图拿到 URL，
    再随表单一起落库 —— 新建品牌还没有 id，不能走「建完再传」的路线。"""
    url=await _save_platform_upload(file)
    if not url: raise HTTPException(400,'只支持图片文件（png/jpg/jpeg/webp/gif），单张上限 10MB')
    return {'url':url}


@app.get('/api/platform/product-categories')
def platform_product_categories():
    with conn() as c: return catalog_service.list_categories(c, include_inactive=True)

@app.post('/api/platform/product-categories')
def platform_add_category(payload:dict=Body(...)):
    with conn() as c:
        try:
            cid=catalog_service.create_category(c,name=str(payload.get('name') or ''),
                                                parent_id=payload.get('parentId'),
                                                sort=payload.get('sort') or 0)
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True,'id':cid}

@app.patch('/api/platform/product-categories/{category_id}')
def platform_patch_category(category_id:int, payload:dict=Body(...)):
    with conn() as c:
        try:
            catalog_service.update_category(c,category_id,name=payload.get('name'),
                                            parent_id=payload.get('parentId'),
                                            sort=payload.get('sort'),status=payload.get('status'))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True}

@app.delete('/api/platform/product-categories/{category_id}')
def platform_delete_category(category_id:int):
    with conn() as c:
        try: out=catalog_service.delete_category(c,category_id)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
    return {'ok':True,**out}


# ==========================================================================
# CSV 批量导入 / 导出（P3）
#
# 一行 = 一个 SKU。为什么不是一行一个商品：运营手里的表本来就是「采购明细」式
# 的（一色一码一行），让他先把表 pivot 成商品维度再导入，等于把工具的成本转嫁给人。
# 导入按「货号 + 商品名」归组，同组的多行自动并成同一商品的多个规格。
#
# ★ 导入是「逐个商品独立提交」：单行出错只跳过那一行并记进 errors，
#   不能因为第 30 行写错就让前 29 行白干 —— 一张几百行的表，全有或全无是没法用的。
# ==========================================================================
CSV_HEADER = ['商品名','货号','品牌','品类','人群','季节','系列','面料','零售价','佣金率%','补货点',
              '颜色','色值','尺码','条码','库存','规格价','介绍']


@app.get('/api/platform/products/export')
def platform_export_products(brand_id:int|None=None, category_id:int|None=None):
    import csv as _csv, io as _io
    where=['1=1']; args=[]
    if brand_id: where.append('p.brand_id=?'); args.append(int(brand_id))
    if category_id: where.append('p.category_id=?'); args.append(int(category_id))
    buf=_io.StringIO(); w=_csv.writer(buf)
    w.writerow(CSV_HEADER)
    with conn() as c:
        for p in c.execute(("""SELECT p.*,(SELECT name FROM product_brands b WHERE b.id=p.brand_id) brand_name,
                               (SELECT name FROM product_categories ct WHERE ct.id=p.category_id) category_name
                               FROM products p WHERE %s ORDER BY p.id""" % ' AND '.join(where)),args).fetchall():
            vs=c.execute("SELECT * FROM product_variants WHERE product_id=? ORDER BY sort,id",(int(p['id']),)).fetchall()
            for v in vs or [None]:
                w.writerow([p['name'],p['sku'],p['brand_name'] or '',p['category_name'] or '',
                            p['gender'] or 'unisex',p['season'] or '',p['series'] or '',p['material'] or '',
                            p['price'],round(float(p['commission_rate'] or 0)*100,2),p['reorder_point'] or 0,
                            (v['color'] if v else '') or '',(v['color_hex'] if v else '') or '',
                            (v['size'] if v else '') or '',(v['barcode'] if v else '') or '',
                            (v['stock'] if v else 0),(v['price'] if v and v['price'] is not None else ''),
                            p['description'] or ''])
    from fastapi.responses import Response as _Response
    return _Response(content=buf.getvalue(), media_type='text/csv; charset=utf-8',
                     headers={'Content-Disposition':'attachment; filename="products.csv"'})


@app.post('/api/platform/products/import')
def platform_import_products(payload:dict=Body(...)):
    """CSV 批量导入。

    createMissing 控制「表里写了库里没有的品牌/品类」时是否自动创建。默认开 ——
    要运营先去另一个页面把 20 个品牌建好再回来导入，这个流程根本跑不起来；
    但新建的字典项会如实回传，让运营知道库里多了什么。
    """
    import csv as _csv, io as _io
    text=str(payload.get('csv') or '')
    create_missing=bool(payload.get('createMissing',True))
    if not text.strip(): raise HTTPException(400,'CSV 内容为空')
    try:
        rd=_csv.DictReader(_io.StringIO(text))
        raw=[r for r in rd if any((v or '').strip() for v in r.values())]
    except Exception as e:
        raise HTTPException(400,'CSV 解析失败：%s' % str(e)[:120])
    if not raw: raise HTTPException(400,'没有解析出有效数据行')
    if '商品名' not in (rd.fieldnames or []): raise HTTPException(400,'缺少「商品名」列')

    with conn() as c:
        for r in raw:
            if str(r.get('库存') or '').strip():
                p0=c.execute('SELECT id FROM products WHERE sku=?',(str(r.get('货号') or '').strip(),)).fetchone()
                v=c.execute("SELECT stock FROM product_variants WHERE product_id=? AND IFNULL(color,'')=? AND IFNULL(size,'')=?",(p0['id'],r.get('颜色') or '',r.get('尺码') or '')).fetchone() if p0 else None
                if int(float(r.get('库存') or 0)) != (int(v['stock']) if v else 0):
                    raise HTTPException(409,'CSV 只导入商品资料；库存变更请在仓库按 SKU 验收或调整')
    created_products=0; updated_products=0; created_variants=0; errors=[]; new_dict={'brands':[], 'categories':[]}
    with conn() as c:
        # 先按货号归组：货号为空的按商品名归组（同名的合并成一个商品）
        groups={}
        for i,r in enumerate(raw,2):   # 行号从 2 起（第 1 行是表头），报错要对得上 Excel
            name=str(r.get('商品名') or '').strip()
            if not name:
                errors.append({'row':i,'msg':'商品名为空'}); continue
            key=(str(r.get('货号') or '').strip() or '\u0001'+name)
            groups.setdefault(key,[]).append((i,r,name))

        for key,rows in groups.items():
            head_i,head,name=rows[0]
            try:
                price=None
                for _i,r,_n in rows:   # rows 里是 (行号, 行数据, 商品名) 三元组
                    pv=str(r.get('零售价') or '').strip()
                    if pv:
                        price=float(pv); break
                if price is None:
                    errors.append({'row':head_i,'msg':'零售价为空'}); continue

                brand_name=str(head.get('品牌') or '').strip()
                cat_name=str(head.get('品类') or '').strip()
                brand_id=cat_id=None
                if brand_name:
                    b=c.execute('SELECT id FROM product_brands WHERE name=?',(brand_name,)).fetchone()
                    if b: brand_id=int(b['id'])
                    elif create_missing:
                        brand_id=catalog_service.create_brand(c,name=brand_name); new_dict['brands'].append(brand_name)
                    else:
                        errors.append({'row':head_i,'msg':'品牌「%s」不存在' % brand_name}); continue
                if cat_name:
                    ct=c.execute('SELECT id FROM product_categories WHERE name=? AND IFNULL(parent_id,0)=0',(cat_name,)).fetchone()
                    if ct: cat_id=int(ct['id'])
                    elif create_missing:
                        cat_id=catalog_service.create_category(c,name=cat_name); new_dict['categories'].append(cat_name)
                    else:
                        errors.append({'row':head_i,'msg':'品类「%s」不存在' % cat_name}); continue

                sku=str(head.get('货号') or '').strip()
                existing=c.execute('SELECT id FROM products WHERE sku=?',(sku,)).fetchone() if sku else None
                if existing:
                    pid=int(existing['id']); updated_products+=1
                    c.execute("""UPDATE products SET name=?,price=?,brand_id=?,category_id=?,category=?,gender=?,season=?,series=?,material=?,description=? WHERE id=?""",
                              (name,price,brand_id,cat_id,cat_name or None,
                               catalog_service.normalize_gender(head.get('人群')),
                               catalog_service.normalize_season(head.get('季节')),
                               catalog_service.normalize_series(head.get('系列')),
                               catalog_service.normalize_material(head.get('面料')),
                               str(head.get('介绍') or ''),pid))
                else:
                    sku=sku or ('GEAR-%d' % int(__import__('time').time()))
                    c.execute("""INSERT INTO products(name,sku,price,stock,status,category,commission_rate,reorder_point,description,
                                 brand_id,category_id,gender,season,series,material)
                                 VALUES(?,?,?,?,'active',?,?,?,?,?,?,?,?,?,?)""",
                              (name,sku,price,0,cat_name or None,
                               (float(head.get('佣金率%') or 8)/100) if str(head.get('佣金率%') or '').strip() else 0.08,
                               int(head.get('补货点') or 5) if str(head.get('补货点') or '').strip() else 5,
                               str(head.get('介绍') or ''),
                               brand_id,cat_id,
                               catalog_service.normalize_gender(head.get('人群')),
                               catalog_service.normalize_season(head.get('季节')),
                               catalog_service.normalize_series(head.get('系列')),
                               catalog_service.normalize_material(head.get('面料'))))
                    pid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0]); created_products+=1

                mx=c.execute("SELECT COALESCE(MAX(sort),0) m FROM product_variants WHERE product_id=?",(pid,)).fetchone()['m']
                for _i,r,_n in rows:   # rows 里是 (行号, 行数据, 商品名) 三元组
                    color=catalog_service.normalize_dimension(r.get('颜色'))
                    size=catalog_service.normalize_dimension(r.get('尺码'))
                    if not color and not size: continue
                    exist=c.execute("""SELECT id FROM product_variants WHERE product_id=?
                                       AND IFNULL(color,'')=? AND IFNULL(size,'')=?""",(pid,color or '',size or '')).fetchone()
                    stock=int(float(r.get('库存') or 0))
                    vp=str(r.get('规格价') or '').strip()
                    vp=float(vp) if vp else None
                    if exist:
                        c.execute("UPDATE product_variants SET stock=?,price=COALESCE(?,price),color_hex=COALESCE(?,color_hex),barcode=COALESCE(?,barcode) WHERE id=?",
                                  (stock,vp,catalog_service.normalize_color_hex(r.get('色值')),
                                   catalog_service.normalize_dimension(r.get('条码')),int(exist['id'])))
                        continue
                    mx+=1
                    vsku=catalog_service.ensure_unique_sku(c,
                        catalog_service.build_sku(c,product_id=pid,brand_id=brand_id,category_id=cat_id,
                                                  color=color or '',size=size or ''))
                    c.execute("""INSERT INTO product_variants(product_id,name,sku,price,stock,sort,status,color,color_hex,size,barcode)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                              (pid,' / '.join([x for x in (color,size) if x]),vsku,vp,stock,mx,'active',
                               color,catalog_service.normalize_color_hex(r.get('色值')),size,
                               catalog_service.normalize_dimension(r.get('条码'))))
                    created_variants+=1
                # 导入进来的真实规格要顶掉迁移生成的「默认」占位规格，
                # 否则 C 端会把「默认」和真实颜色尺码并排显示
                if created_variants:
                    d=c.execute("SELECT id FROM product_variants WHERE product_id=? AND is_default=1 AND status='active'",(pid,)).fetchone()
                    if d:
                        c.execute("UPDATE product_variants SET status='inactive',stock=0 WHERE id=?",(int(d['id']),))
                _resync_product_stock(c,pid)
            except Exception as e:
                errors.append({'row':head_i,'msg':str(e)[:120]})
    return {'ok':True,'createdProducts':created_products,'updatedProducts':updated_products,
            'createdVariants':created_variants,'errors':errors[:50],'errorCount':len(errors),
            'newBrands':new_dict['brands'],'newCategories':new_dict['categories']}


def _parse_color_dims(colors, sizes):
    """解析 颜色 / 尺码 两个维度，支持「每个颜色配自己的尺码」。

    返回 ([(颜色名, 色值, 该颜色专属尺码|None)], 全局尺码列表)。

    2026-10-26 用户反馈：现实里每个颜色的尺码并不一样（黑色 S/M/L、白色只有 M/L），
    旧口径「颜色列表 × 一条全局尺码串」的笛卡尔积会凭空生成一堆 0 库存的假 SKU。
    所以颜色项带 sizes 键时以它为准（空列表＝该颜色只有均码，不与全局尺码相乘）；
    不带该键才回落到全局 sizes —— 旧客户端 / CSV / 批量弹层都还是那个形状。
    """
    global_sizes=[]
    for x in (sizes or []):
        nm=catalog_service.normalize_dimension(x)
        if nm and nm not in global_sizes: global_sizes.append(nm)
    out=[]
    for x in (colors or []):
        own=None
        if isinstance(x,dict):
            nm=catalog_service.normalize_dimension(x.get('name'))
            hx=catalog_service.normalize_color_hex(x.get('hex'))
            if 'sizes' in x:
                own=[]
                for s in (x.get('sizes') or []):
                    ns=catalog_service.normalize_dimension(s)
                    if ns and ns not in own: own.append(ns)
        else:
            nm=catalog_service.normalize_dimension(x); hx=None
        if nm: out.append((nm,hx,own))
    return out,global_sizes


# ==========================================================================
# SKU 矩阵：颜色 × 尺码 一次生成所有组合
# ==========================================================================
@app.post('/api/platform/products/{product_id}/variants/matrix')
def generate_variant_matrix(product_id:int, payload:dict=Body(...)):
    """按 颜色 × 尺码 批量建规格。

    逐个加规格在 2 色 × 4 码上就要点 8 次、填 8 遍价格，运营一定会漏。
    矩阵一次生成，随后再逐个微调价格/库存/条码。

    已存在的组合（同色同码）跳过而不是覆盖 —— 重复提交不该把已经调好的
    库存和条码重置回默认值。
    """
    colors=payload.get('colors') or []
    sizes=payload.get('sizes') or []
    if not colors and not sizes: raise HTTPException(400,'至少给一个颜色或一个尺码')
    price=payload.get('price'); price=float(price) if price not in (None,'') else None
    stock=max(0,int(payload.get('stock') or 0))
    sku_stock=_sku_stock_map(payload)
    with conn() as c:
        p=c.execute('SELECT id,brand_id,category_id FROM products WHERE id=?',(product_id,)).fetchone()
        if not p: raise HTTPException(404,'商品不存在')
        # V1.2：池内变化前先记期初（占位退场/新规格期初都算池内变化，最后按差镜像 WMS）
        pool_before=sum(int(x['stock'] or 0) for x in c.execute("SELECT stock FROM product_variants WHERE product_id=? AND status='active'",(product_id,)).fetchall())
        color_list,size_list=_parse_color_dims(colors,sizes)
        if not color_list: color_list=[(None,None,None)]
        if not size_list: size_list=[None]
        mx=c.execute("SELECT COALESCE(MAX(sort),0) m FROM product_variants WHERE product_id=?",(product_id,)).fetchone()['m']
        # 商品原本只有迁移生成的那个「默认」规格，加真实矩阵后它就该退场，
        # 否则 C 端会把「默认」和「炭灰/M」并排显示成 9 个规格。
        dflt=c.execute("SELECT id,stock FROM product_variants WHERE product_id=? AND is_default=1 AND status='active'",(product_id,)).fetchone()
        others=c.execute("SELECT COUNT(*) n FROM product_variants WHERE product_id=? AND status='active' AND is_default=0",(product_id,)).fetchone()['n']
        moved=0
        if dflt and others==0:
            moved=int(dflt['stock'] or 0)
            c.execute("UPDATE product_variants SET status='inactive',stock=0 WHERE id=?",(dflt['id'],))
        created,skipped=0,0
        for cn,ch,own in color_list:
            for sz in (size_list if own is None else own) or [None]:
                exist=c.execute("""SELECT id FROM product_variants WHERE product_id=?
                                   AND IFNULL(color,'')=? AND IFNULL(size,'')=?""",
                                (product_id,cn or '',sz or '')).fetchone()
                if exist: skipped+=1; continue
                name=' / '.join([x for x in (cn,sz) if x]) or '默认'
                sku=catalog_service.ensure_unique_sku(c,
                    catalog_service.build_sku(c,product_id=int(product_id),brand_id=p['brand_id'],
                                              category_id=p['category_id'],color=cn or '',size=sz or ''))
                mx+=1
                # 追加规格的库存落点：优先 skuStock 逐格取，没传才统一用 stock
                vstock=int((sku_stock.get(cn or '') or {}).get(sz or '',0)) if sku_stock else stock
                c.execute("""INSERT INTO product_variants(product_id,name,sku,price,stock,sort,status,color,color_hex,size)
                             VALUES(?,?,?,?,?,?,?,?,?,?)""",
                          (product_id,name,sku,price,vstock,mx,'active',cn,ch,sz))
                created+=1
        # 占位规格的库存转给第一个新建的规格，不让库存凭空消失
        if moved and created:
            first=c.execute("""SELECT id FROM product_variants WHERE product_id=? AND status='active'
                               ORDER BY sort,id LIMIT 1""",(product_id,)).fetchone()
            if first:
                c.execute('UPDATE product_variants SET stock=? WHERE id=?',(moved,int(first['id'])))
        # V1.2 真源反转：矩阵批量建规格带来的池净变化（Σ新规格期初）镜像到 WMS 实物台账
        pool_after=sum(int(x['stock'] or 0) for x in c.execute("SELECT stock FROM product_variants WHERE product_id=? AND status='active'",(product_id,)).fetchall())
        _mirror_pool_delta(c,product_id,pool_after-pool_before,ref=f'matrix:{product_id}:{created}',note='矩阵建规格期初库存镜像')
        _resync_product_stock_warehouse_aware(c,product_id)
    return {'ok':True,'created':created,'skipped':skipped,'movedStock':moved}


def _sync_product_to_medusa(product_id:int, *, create_if_missing:bool=True):
    if commerce_provider()!='medusa':
        return {'ok':False,'skipped':True,'reason':'COMMERCE_PROVIDER is not medusa'}
    with conn() as c:
        p=row(c.execute('SELECT * FROM products WHERE id=?',(product_id,)))
        vs=rows(c.execute("SELECT * FROM product_variants WHERE product_id=? AND status='active' ORDER BY sort,id",(product_id,)))
    if not p: raise HTTPException(404,'商品不存在')
    if p.get('source')=='external_api' or p.get('inventory_mode')!='platform_owned':
        return {'ok':True,'skipped':True,'reason':'外部品牌仅同步商品展示资料'}
    client=MedusaClient()
    try:
        if p.get('commerce_product_id') and p.get('commerce_variant_id'):
            client.update_platform_product(product_id=str(p['commerce_product_id']),variant_id=str(p['commerce_variant_id']),
                                           name=p['name'],price=float(p['price']),status=p['status'],image_url=p.get('image_url'))
            inv=p.get('commerce_inventory_item_id')
            if not inv:
                full=client.get_admin_product(str(p['commerce_product_id']))
                inv=client._inventory_item_id(full,str(p['commerce_variant_id']))
            # Per-SKU stock is projected below after all mappings are checked.

            result={'product_id':p['commerce_product_id'],'variant_id':p['commerce_variant_id'],'inventory_item_id':inv}
        else:
            if not create_if_missing: raise RuntimeError('商品尚未绑定 Medusa')
            result=client.create_platform_product(name=p['name'],sku=p['sku'],price=float(p['price']),stock=int(p['stock']),
                                                  status=p['status'],image_url=p.get('image_url'),category=p.get('category'),variants=vs)
        full=client.get_admin_product(str(result['product_id'])) or {}
        mapped=[]
        for v in vs:
            remote=next((x for x in full.get('variants',[]) if x.get('sku')==v['sku'] or x.get('id')==v.get('commerce_variant_id')),None)
            if not remote and len(vs)==1 and len(full.get('variants',[]))==1:remote=full['variants'][0]
            if not remote:raise RuntimeError('Medusa 缺少 SKU '+str(v['sku'])+'，请先核对远端规格；禁止将多规格合并到默认规格')
            iid=client._inventory_item_id(full,remote['id'])
            if not iid:raise RuntimeError('Medusa SKU 未关联库存项')
            with conn() as c:
                physical=int(c.execute('SELECT COALESCE(SUM(on_hand-unsellable),0) FROM warehouse_sku_balances WHERE variant_id=? AND location_id IN (SELECT id FROM warehouse_locations WHERE zone_type="sellable" AND status="active")',(v['id'],)).fetchone()[0])
            client.update_platform_product(product_id=str(result['product_id']),variant_id=str(remote['id']),name=p['name'],price=float(v['price'] if v['price'] is not None else p['price']),status=p['status'],image_url=p.get('image_url'))
            client.set_inventory(inventory_item_id=str(iid),stock=physical)
            mapped.append((remote['id'],v['id']))
        with conn() as c:
            c.executemany('UPDATE product_variants SET commerce_variant_id=? WHERE id=?',mapped)
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
    """新建商品（可一次带上颜色 × 尺码矩阵）。

    维度（品牌 / 品类 / 人群 / 季节 / 系列 / 面料）与 SKU 矩阵在同一个事务里落库：
    先建商品再补规格的话，中途失败会留下一个「没有 SKU、C 端显示缺货」的半成品商品。

    前端单页编辑器已经卡过必填，但接口是可以直接调的：少一道校验就有人能建出
    「名字为空、价格 0」的商品，然后一路同步到商城。
    """
    name = str(payload.get('name') or '').strip()
    if not name:
        raise HTTPException(400, '请填写商品名称')
    if float(payload.get('price') or 0) < 0:
        raise HTTPException(400, '请填写有效零售价')
    # 款号不填就自动生成：sku 列有唯一约束，两个空款号商品第二个必撞 409
    #（采购页快捷建商品不填款号，之前第二条路直接被堵死）。
    sku = str(payload.get('sku') or '').strip() or \
        ('GEAR-' + datetime.utcnow().strftime('%Y%m%d%H%M%S') + '-' + uuid.uuid4().hex[:4])
    # 期初库存自动汇总（2026-10-26 用户反馈）：给了 skuStock 就按 颜色×尺码 逐格求和，
    # 不再让手填总数与规格明细打架；没给映射才信任 payload.stock（旧客户端 / CSV 导入）。
    sku_stock_raw=payload.get('skuStock') if isinstance(payload.get('skuStock'),dict) else {}
    if sku_stock_raw:
        initial_stock=0
        for _mins in sku_stock_raw.values():
            if isinstance(_mins,dict):
                for _q in _mins.values():
                    try: initial_stock+=max(0,int(_q or 0))
                    except Exception: pass
    else:
        initial_stock=max(0,int(payload.get('stock',0) or 0))
    warehouse=str(payload.get('warehouse') or '').strip() or None
    brand_id=payload.get('brandId') or payload.get('brand_id')
    category_id=payload.get('categoryId') or payload.get('category_id')
    gender=catalog_service.normalize_gender(payload.get('gender'))
    # 品类已结构化，但 category 文本列不能丢：C 端副标题、Medusa 同步、老统计都还在读它。
    # 建商品时按所选品类把它填上，两边永远一致，不需要人工维护第二份。
    with conn() as c:
        if brand_id:
            brand=c.execute('SELECT source_type FROM product_brands WHERE id=?',(brand_id,)).fetchone()
            if brand and brand[0]=='external_api':payload={**payload,'source':'external_api','inventoryMode':'external_api','fulfillmentMode':'supplier_fulfillment'}
        cat_name=None
        if category_id:
            r=c.execute('SELECT name FROM product_categories WHERE id=?',(int(category_id),)).fetchone()
            cat_name=r['name'] if r else None
        category=str(payload.get('category') or cat_name or '').strip() or None
        # 款号唯一键冲突会直接冒 sqlite3.IntegrityError → 前端只看到一句 Internal Server Error。
        # 单页编辑器鼓励手填款号，撞号是常态而不是异常，必须翻成一句人话（409）。
        try:
            c.execute("""INSERT INTO products(name,sku,price,stock,status,image_url,category,commission_rate,reorder_point,description,
                     brand_id,category_id,gender,season,series,material,warehouse,commerce_sync_status,
                     source,inventory_mode,fulfillment_mode,owner_id,is_featured)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (name,sku,float(payload.get('price') or 0),0,'draft',payload.get('image_url'),
                       category,payload.get('commission_rate',0.08),
                       max(0,int(payload.get('reorder_point',payload.get('reorderPoint',5)) or 0)),
                       str(payload.get('description') or ''),
                       int(brand_id) if brand_id else None,
                       int(category_id) if category_id else None,
                       gender,
                       catalog_service.normalize_season(payload.get('season')),
                       catalog_service.normalize_series(payload.get('series')),
                       catalog_service.normalize_material(payload.get('material')),
                       warehouse,
                       'pending' if commerce_provider()=='medusa' else 'local',
                       str(payload.get('source') or 'platform_self'),
                       str(payload.get('inventoryMode') or payload.get('inventory_mode') or 'platform_owned'),
                       str(payload.get('fulfillmentMode') or payload.get('fulfillment_mode') or 'platform_warehouse'),
                       int(payload.get('ownerId')) if payload.get('ownerId') else None,
                       1 if (payload.get('is_featured') or payload.get('isFeatured')) else 0))
        except IntegrityError:
            raise HTTPException(409, f'款号「{payload.get("sku") or "(空)"}」已被占用，请换一个')
        pid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        created=_build_variant_matrix(c,pid,brand_id=brand_id,category_id=category_id,payload=payload,initial_stock=initial_stock)
        external=(payload.get('source')=='external_api' or (payload.get('inventoryMode') or payload.get('inventory_mode') or 'platform_owned')!='platform_owned')
        if external and initial_stock:raise HTTPException(400,'外部库存不能手填期初或进入平台仓，请等待品牌 API 接通')
        if not external:
            for v in rows(c.execute('SELECT id,stock FROM product_variants WHERE product_id=?',(pid,))):
                if v['stock']:
                    c.execute('UPDATE product_variants SET stock=0 WHERE id=?',(v['id'],))
                    inventory_engine.adjust_stock(c,product_id=pid,variant_id=v['id'],quantity_delta=v['stock'],reason='自营商品期初入库',reference_type='product_opening',reference_id=f'{pid}:{v["id"]}')
        if _norm_product_status(payload.get('status'))=='active':
            _validate_product_publish(c,pid)
            c.execute("UPDATE products SET status='active' WHERE id=?",(pid,))
    out={'id':pid,'variantsCreated':created}
    if commerce_provider()=='medusa':
        out['commerce']=_sync_product_to_medusa(pid)
    return out


def _sku_stock_map(payload:dict) -> dict[str,dict[str,int]]:
    """解析前端传来的 per-SKU 期初库存：{颜色:{尺码:数量}}。

    键一律走 normalize_dimension，与 color_list/size_list 的归一口径一致 ——
    前端传「黑色」，落库也是「黑色」，查表时两边都对得上；防一手大小写/空格漂移。
    空键保留为 ''：对应「只填了尺码没填颜色」的默认色行，与 color_list 里
    (None,None) 的回落分支用 `cn or ''` 查表对齐。
    """
    raw=payload.get('skuStock')
    if not isinstance(raw,dict): return {}
    out={}
    for ck,mins in raw.items():
        nk=catalog_service.normalize_dimension(ck) or ''
        if not isinstance(mins,dict): continue
        row={}
        for sk,q in mins.items():
            ns=catalog_service.normalize_dimension(sk) or ''
            try: q=int(q or 0)
            except Exception: q=0
            if q>0: row[ns]=q
        if row: out[nk]=row
    return out


def _build_variant_matrix(c, pid:int, *, brand_id, category_id, payload:dict, initial_stock:int=0) -> int:
    """按 colors × sizes 建规格；没给矩阵就退回建一个「默认」规格承接库存。

    库存真源在规格层，没有规格的商品在 C 端读不到库存、直接显示缺货 ——
    所以「不填矩阵」不等于「不建规格」。
    2026-10-26 起支持 skuStock（{颜色:{尺码:数量}}）：期初库存按颜色×尺码
    逐格落库；没传的键回落 0，完全没传 skuStock 才走旧口径（全部压第一个组合）。
    """
    colors=payload.get('colors') or []
    sizes=payload.get('sizes') or []
    sku_stock=_sku_stock_map(payload)
    if not colors and not sizes:
        sku=catalog_service.ensure_unique_sku(c,
            catalog_service.build_sku(c,product_id=int(pid),brand_id=brand_id,category_id=category_id))
        c.execute("INSERT INTO product_variants(product_id,name,sku,stock,sort,status,is_default) VALUES(?,?,?,?,?,'active',1)",
                  (pid,'默认',sku,initial_stock,0))
        return 1
    color_list,size_list=_parse_color_dims(colors,sizes)
    if not color_list: color_list=[(None,None,None)]
    if not size_list: size_list=[None]
    price=payload.get('price')
    n=0; sort=0
    for cn,ch,own in color_list:
        for sz in (size_list if own is None else own) or [None]:
            name=' / '.join([x for x in (cn,sz) if x]) or '默认'
            sku=catalog_service.ensure_unique_sku(c,
                catalog_service.build_sku(c,product_id=int(pid),brand_id=brand_id,category_id=category_id,
                                          color=cn or '',size=sz or ''))
            sort+=1
            # 期初库存落点：优先按 skuStock 的颜色×尺码逐格取；没传映射才用旧口径
            #（全部压第一个组合，均分会让每个 SKU 只有几件，还得手工并回来）。
            if sku_stock:
                vstock=int((sku_stock.get(cn or '') or {}).get(sz or '',0))
            else:
                vstock=initial_stock if n==0 else 0
            c.execute("""INSERT INTO product_variants(product_id,name,sku,price,stock,sort,status,color,color_hex,size)
                         VALUES(?,?,?,?,?,?,?,?,?,?)""",
                      (pid,name,sku,float(price) if price is not None and float(price)>0 else None,
                       vstock,sort,'active',cn,ch,sz))
            n+=1
    return n

@app.post('/api/platform/products/{product_id}/commerce-link')
def platform_link_product(product_id:int,payload:dict=Body(...)):
    product_id_ext=payload.get('commerceProductId'); variant_id=payload.get('commerceVariantId'); inventory_id=payload.get('commerceInventoryItemId')
    if not variant_id: raise HTTPException(400,'commerceVariantId required')
    with conn() as c:
        p=row(c.execute('SELECT id FROM products WHERE id=?',(product_id,)))
        if not p: raise HTTPException(404,'商品不存在')
        c.execute("UPDATE products SET commerce_product_id=?,commerce_variant_id=?,commerce_inventory_item_id=?,commerce_sync_status='linked',commerce_sync_error=NULL,last_commerce_sync_at=CURRENT_TIMESTAMP WHERE id=?",
                  (product_id_ext,variant_id,inventory_id,product_id))
        vs=rows(c.execute("SELECT id FROM product_variants WHERE product_id=? AND status='active'",(product_id,)))
        local_vid=payload.get('variantId') or (vs[0]['id'] if len(vs)==1 else None)
        if not local_vid:raise HTTPException(409,'多规格绑定请指定本地 variantId')
        if not c.execute('SELECT 1 FROM product_variants WHERE id=? AND product_id=?',(local_vid,product_id)).fetchone():raise HTTPException(409,'SKU 不属于该商品')
        c.execute('UPDATE product_variants SET commerce_variant_id=? WHERE id=?',(variant_id,local_vid))
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

def _owned_product(c,pid):
    try:return stock_control.owned(c,pid)
    except ValueError as e:raise HTTPException(409,str(e))

def _validate_product_publish(c,pid,changes=None):
    p=row(c.execute('SELECT * FROM products WHERE id=?',(pid,)))
    if not p:raise HTTPException(404,'商品不存在')
    p.update(changes or {})
    display_only=False
    mirrored=False
    try:stock_control.owned(c,pid)
    except ValueError as e:
        display_only=bool(c.execute("SELECT 1 FROM external_product_mappings WHERE product_id=? AND external_sync_status='catalog_only'",(pid,)).fetchone())
        if not display_only:raise HTTPException(409,str(e))
    else:
        mirrored=stock_control.mirrored(c,p)
    if c.execute("""SELECT 1 FROM external_product_mappings WHERE product_id=? AND external_sku_id=''
                    AND external_sync_status='catalog_unavailable'""",(pid,)).fetchone():
        raise HTTPException(409,'该商品已在 PEAKTRACE 下架，请在品牌后台重新上架后同步')
    missing=[]
    if not (p.get('image_url') or p.get('cover_url') or c.execute('SELECT 1 FROM product_images WHERE product_id=?',(pid,)).fetchone()):missing.append('商品图片')
    if not str(p.get('description') or '').strip():missing.append('商品详情')
    if float(p.get('price') or 0)<=0:missing.append('有效零售价')
    # 镜像商品库存以上游为准：上游暂时 0 件也允许上架（C 端显示缺货），不逼运营补验收单
    if not display_only and not mirrored and not c.execute('SELECT 1 FROM warehouse_sku_balances WHERE product_id=? AND on_hand>0',(pid,)).fetchone():missing.append('已验收入库的 SKU')
    if c.execute("SELECT 1 FROM stock_reconciliation WHERE product_id=? AND status='pending'",(pid,)).fetchone():missing.append('历史库存实盘核对')
    if missing:raise HTTPException(400,'上架前请补齐：'+'、'.join(missing))

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
            if r['action']=='on':_validate_product_publish(c,r['product_id'])
            new_status='active' if r['action']=='on' else 'inactive'
            c.execute('UPDATE products SET status=? WHERE id=?',(new_status,r['product_id']))
    return {'ok':True,'requestId':request_id,'status':decision,'productStatus':new_status}

# ── 商品状态兼容（进销存 V1.2）───────────────────────────────────────────────
# 新 7 态：draft / pending_inbound / partially_received / pending_listing / active /
# inactive / archived。历史上出现过（或外部同步可能写入）的别名统一归一：
# 已知别名映射，未知值保留原样透传（前端按原文显示，绝不静默猜状态）。
_PRODUCT_STATUS_ALIASES={
    'on_sale':'active','online':'active','published':'active','in_stock':'active','selling':'active',
    'off':'inactive','offline':'inactive','unlisted':'inactive','delisted':'inactive',
    'removed':'inactive','off_shelf':'inactive','discontinued':'inactive',
    'incoming':'pending_inbound','inbound_pending':'pending_inbound','to_inbound':'pending_inbound',
    'partial':'partially_received','partial_inbound':'partially_received',
    'wait_list':'pending_listing','to_list':'pending_listing','pre_list':'pending_listing',
}

def _norm_product_status(v)->str:
    s=str(v or '').strip().lower()
    if not s: return 'draft'
    return _PRODUCT_STATUS_ALIASES.get(s,s)

@app.patch('/api/platform/products/{product_id}')
def platform_update_product(product_id:int,payload:dict=Body(...)):
    allowed=['name','price','status','image_url','category','commission_rate','reorder_point','description',
             'brand_id','category_id','gender','season','series','material','warehouse',
             'source','inventory_mode','fulfillment_mode','owner_id','is_featured'];sets=[];vals=[]
    # 主推款开关：前端传 is_featured（也兼容 isFeatured / featured 驼峰），一律收敛成 0/1
    if 'isFeatured' in payload and 'is_featured' not in payload: payload={**payload,'is_featured':payload.get('isFeatured')}
    if 'featured' in payload and 'is_featured' not in payload: payload={**payload,'is_featured':payload.get('featured')}
    if 'reorderPoint' in payload and 'reorder_point' not in payload: payload={**payload,'reorder_point':payload.get('reorderPoint')}
    if 'inventoryMode' in payload and 'inventory_mode' not in payload: payload={**payload,'inventory_mode':payload.get('inventoryMode')}
    if 'fulfillmentMode' in payload and 'fulfillment_mode' not in payload: payload={**payload,'fulfillment_mode':payload.get('fulfillmentMode')}
    if 'ownerId' in payload and 'owner_id' not in payload: payload={**payload,'owner_id':payload.get('ownerId')}
    with conn() as c:
        p=row(c.execute('SELECT * FROM products WHERE id=?',(product_id,)))
        if not p: raise HTTPException(404,'商品不存在')
        # 进销存 V1.1（任务书 6.1/9）：上架前校验 —— 图 / 详情 / 售价三道闸。
        # 待上架工作流把商品推进到 pending_listing 之后，上架不再是无脑翻转：
        # 没图没介绍没价格的商品流到 C 端就是一张空白卡片。
        # V1.2 状态兼容：比较前先归一，老状态别名（on_sale/offline/...）同样被拦住。
        if _norm_product_status(payload.get('status') or '')=='active' and p['status']!='active':
            _validate_product_publish(c,product_id,payload)
        if 'stock' in payload:
            if p.get('inventory_mode')=='external_mirror':
                raise HTTPException(409,'该商品库存以 PEAKTRACE 为准，请在品牌后台调整后重新同步')
            try: inventory_engine.set_absolute_stock(c,product_id=product_id,new_stock=max(0,int(payload.get('stock') or 0)),reason=str(payload.get('stockReason') or '平台手工修改库存'))
            except ValueError as e: raise HTTPException(409,str(e))
        for k in ('source','inventory_mode','fulfillment_mode'):
            if k in payload and payload[k]!=p.get(k) and c.execute('SELECT 1 FROM warehouse_inventory WHERE product_id=? AND (on_hand>0 OR reserved>0)',(product_id,)).fetchone():
                raise HTTPException(409,'已有仓储事实的商品不能直接切换库存归属，请先对账处理')
        # 前端传的是 brandId / categoryId（驼峰），这里统一成列名再进 allowed 循环
        if 'brandId' in payload and 'brand_id' not in payload: payload={**payload,'brand_id':payload.get('brandId')}
        if 'categoryId' in payload and 'category_id' not in payload: payload={**payload,'category_id':payload.get('categoryId')}
        for k in allowed:
            if k in payload:
                v=payload[k]
                # 空字符串代表「清空这个维度」，必须落成 NULL ——
                # 外键列写空字符串在 SQLite 里不会报错，但永远 JOIN 不到任何品牌。
                if k in ('brand_id','category_id','owner_id'): v=int(v) if v not in (None,'',0) else None
                if k=='gender': v=catalog_service.normalize_gender(v)
                if k=='status': v=_norm_product_status(v)  # V1.2 状态兼容：别名归一后才落库
                if k=='season': v=catalog_service.normalize_season(v)
                if k=='series': v=catalog_service.normalize_series(v)
                if k=='material': v=catalog_service.normalize_material(v)
                if k=='is_featured': v=1 if (v is True or str(v) in ('1','true','True','yes','on')) else 0
                sets.append(f'{k}=?');vals.append(v)
        # 品类换了就把 category 文本列一起改：两边不一致会让后台筛选和 C 端副标题打架
        if 'category_id' in payload:
            cn=None
            if payload.get('category_id'):
                r=c.execute('SELECT name FROM product_categories WHERE id=?',(int(payload['category_id']),)).fetchone()
                cn=r['name'] if r else None
            if 'category' not in payload:
                sets.append('category=?');vals.append(cn)
        if sets:
            sync='pending' if commerce_provider()=='medusa' else p.get('commerce_sync_status','local')
            c.execute(f'UPDATE products SET {", ".join(sets)},commerce_sync_status=? WHERE id=?',vals+[sync,product_id])
    if commerce_provider()=='medusa': return {'ok':True,'commerce':_sync_product_to_medusa(product_id)}
    return {'ok':True}


@app.get('/api/platform/orders')
def platform_orders(club_id:int|None=Query(None), date_from:str=Query(''), date_to:str=Query(''),
                    user:str=Query(''), phone:str=Query(''), status:str=Query('')):
    # packed 让平台端订单行能判断「还该不该显示打包按钮」—— 打包结果只落在 warehouse_tasks，
    # 订单行自己看不出来，否则打完包按钮还挂在那里。
    #
    # 筛选（2026-10-24）：俱乐部 / 时间 / 用户名 / 手机号。全部走 SQL 而不是拉回来在前端过滤，
    # 订单量上千以后前端过滤既慢又筛不全（只筛当前已经渲染的那一页）。
    where, values = [], []
    if club_id:
        where.append('o.source_club_id=?'); values.append(int(club_id))
    # 时间按 GMT+8 判定：DB 存 UTC，运营说的「今天」是本地零点到零点。
    # 直接比较 created_at 的日期会让早上 8 点前的订单落到前一天。
    if date_from:
        where.append("date(o.created_at,'+8 hours')>=date(?)"); values.append(date_from)
    if date_to:
        where.append("date(o.created_at,'+8 hours')<=date(?)"); values.append(date_to)
    if (user or '').strip():
        where.append('u.name LIKE ?'); values.append('%' + user.strip() + '%')
    if (phone or '').strip():
        # 订单收货手机号优先，没填收货信息的（历史订单）退回账号手机号，
        # 否则运营按客户手机号搜会漏掉一批只有账号没有地址的单。
        where.append("(COALESCE(o.shipping_phone,'') LIKE ? OR COALESCE(u.phone,'') LIKE ?)")
        p = '%' + phone.strip() + '%'; values.extend([p, p])
    if (status or '').strip():
        where.append('o.status=?'); values.append(status.strip())
    sql = ('SELECT o.*,u.name buyer,u.phone buyer_phone,cl.name source_club FROM gear_orders o '
           'JOIN users u ON u.id=o.user_id JOIN clubs cl ON cl.id=o.source_club_id'
           + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY o.id DESC')
    with conn() as c:
        packed={r['order_id'] for r in rows(c.execute("SELECT DISTINCT order_id FROM warehouse_tasks WHERE task_type='pack' AND status='packed'"))}
        out=rows(c.execute(sql, values))
        # 拣货进展随行下发：行内按「未建任务→拣货中→已拣货」渲染下一步按钮，
        # 否则运营点完「创建拣货」看不到任何变化，也不知道要去仓储 WMS 点「完成拣货」。
        picks={}
        for r in rows(c.execute("SELECT order_id,status,id FROM warehouse_tasks WHERE task_type='pick' AND status IN ('pending','picked')")):
            picks[r['order_id']]=r
        for x in out:
            x['packed']=x['id'] in packed
            p=picks.get(x['id'])
            x['pickState']=p['status'] if p else None
            x['pickTaskId']=p['id'] if p else None
        # 外部履约桥（2026-11-06）随行下发：externalMode 告诉前端这单是纯外部
        # （PEAKTRACE 代发，禁本地发货）/混合；externalFulfillment 是供应商侧进度。
        eff={r['order_id']:r for r in rows(c.execute('SELECT * FROM external_fulfillment_orders'))}
        modes={}
        ids=[x['id'] for x in out]
        for chunk in (ids[i:i+100] for i in range(0,len(ids),100)):
            ph=','.join('?'*len(chunk))
            for r in rows(c.execute(f"""SELECT i.order_id,COUNT(*) n,
                        SUM(CASE WHEN p.source='external_api' OR (p.inventory_mode IS NOT NULL
                                  AND p.inventory_mode NOT IN ('','platform_owned')) THEN 1 ELSE 0 END) ext
                        FROM gear_order_items i JOIN products p ON p.id=i.product_id
                        WHERE i.order_id IN ({ph}) GROUP BY i.order_id""",chunk)):
                modes[r['order_id']]='pure' if r['n']==r['ext'] else 'mixed'
        for x in out:
            x['externalMode']=modes.get(x['id'])
            r=eff.get(x['id'])
            x['externalFulfillment']=({'pushStatus':r['push_status'],'fulfillStatus':r['fulfill_status'],
                'carrier':r['carrier'],'trackingNo':r['tracking_no'],
                'pushError':r['push_error'],'externalOrderId':r['external_order_id'],
                'externalRef':r['external_ref'],'pullError':r['pull_error']} if r else None)
        return out

@app.get('/api/platform/orders/filters')
def platform_order_filters():
    """订单筛选器的候选项：俱乐部下拉 + 状态枚举。

    单独做轻量接口而不是复用 /api/platform/clubs：后者联了 AI 额度、
    订阅与欠费三张表，只为填一个下拉框太重。
    """
    with conn() as c:
        clubs=[{'id':r['id'],'name':r['name']} for r in rows(c.execute(
            "SELECT id,name FROM clubs ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,id DESC"))]
        # value 是英文状态值（直接进 ?status= 查询），label 是下拉里给人看的中文。
        statuses=[{'value':k,'label':v} for k,v in (('paid','已付款'),('shipped','已发货'),
                                                     ('delivered','已签收'),('refunded','已退款'))]
    return {'clubs':clubs,'statuses':statuses}

@app.patch('/api/platform/orders/{order_id}')
def platform_update_order(order_id:int,payload:dict=Body(...)):
    if payload.get('status')=='shipped':return platform_create_shipment(order_id,{'trackingNo':payload.get('tracking_no'),'carrier':payload.get('carrier')})
    if payload.get('status')=='delivered':
        with conn() as c:
            o=row(c.execute('SELECT * FROM gear_orders WHERE id=?',(order_id,)))
            if not o or o['status'] not in ('shipped','delivered'):raise HTTPException(409,'只能确认已发货订单签收')
    elif payload.get('status') is not None:raise HTTPException(409,'订单状态必须通过对应业务动作推进')
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
                # 外部履约表对齐：平台确认签收后，供应商侧物流统一标记已妥投。
                try: c.execute("UPDATE external_fulfillment_orders SET fulfill_status=? WHERE order_id=? AND fulfill_status!='delivered'",
                               (external_fulfillment.FUL_DELIVERED, order_id))
                except Exception: pass
    return {'ok':True,'commission':commission}


# ==========================================================================
# 物流履约（2026-10-24）
# 设计见 logistics_engine.py 顶部注释。这一层只做三件事：
#   ① 配置读写（服务商 / 密钥 / 回调 token）
#   ② 建运单、查轨迹、手动补录 —— 全部经过 Provider 适配层
#   ③ 回调入口：承运商 push 过来时按 tracking_no 落到对应运单
# 订单上的 carrier/tracking_no 两列继续写，是为了兼容既有列表、导出和
# C 端订单页 —— 它们读的是这两列，不读运单表。
# ==========================================================================
LOGISTICS_CONFIG_KEY = 'logistics_provider_json'


def _logistics_config() -> dict:
    cfg = jload(setting(LOGISTICS_CONFIG_KEY, '{}'), {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    # 环境变量兜底：容器化部署时密钥只放环境里，不落库。
    cfg.setdefault('provider', (os.getenv('CLUBOS_LOGISTICS_PROVIDER', 'mock') or 'mock').strip())
    if not cfg.get('key'):
        cfg['key'] = os.getenv('KUAIDI100_KEY', '')
    if not cfg.get('customer'):
        cfg['customer'] = os.getenv('KUAIDI100_CUSTOMER', '')
    return cfg


def _save_logistics_config(cfg: dict) -> None:
    with conn() as c:
        c.execute('INSERT INTO platform_settings(key,value) VALUES(?,?) '
                  'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  (LOGISTICS_CONFIG_KEY, jdump(cfg)))


@app.get('/api/platform/logistics/config')
def platform_logistics_config():
    cfg = _logistics_config()
    code = str(cfg.get('provider') or 'mock').lower()
    return {
        'provider': code,
        'providerName': (logistics_engine.PROVIDERS.get(code) or logistics_engine.MockProvider).name,
        'providers': logistics_engine.provider_list(),
        'carriers': logistics_engine.CARRIERS,
        'statuses': [{'value': k, 'label': v} for k, v in logistics_engine.STATUS_CN.items()],
        'hasKey': bool(cfg.get('key')),
        'customer': cfg.get('customer', ''),
        'webhookPath': f'/api/webhooks/logistics/{code}',
        'tokenSet': bool(cfg.get('token')),
    }


@app.patch('/api/platform/logistics/config')
def platform_logistics_config_update(payload: dict = Body(...)):
    cfg = _logistics_config()
    if 'provider' in payload:
        code = str(payload.get('provider') or '').strip().lower()
        if code not in logistics_engine.PROVIDERS:
            raise HTTPException(400, '未知物流服务商')
        cfg['provider'] = code
    for key in ('key', 'customer', 'token'):
        if key in payload:
            cfg[key] = str(payload.get(key) or '').strip()
    _save_logistics_config(cfg)
    return {'ok': True, 'provider': cfg.get('provider')}


def _shipment_payload(order_id: int) -> dict:
    with conn() as c:
        s = logistics_engine.get_shipment(c, order_id)
        if not s:
            o = row(c.execute('SELECT carrier,tracking_no,status FROM gear_orders WHERE id=?', (order_id,)))
            return {'shipment': None, 'events': [],
                    'legacy': {'carrier': (o or {}).get('carrier') or '',
                               'trackingNo': (o or {}).get('tracking_no') or '',
                               'orderStatus': (o or {}).get('status') or ''}}
        return {'shipment': s, 'events': logistics_engine.list_events(c, s['id'])}


def _apply_order_progress(c, order_id: int, shipment: dict):
    """运单「已签收」时把订单推到 delivered 并冻结佣金，与手工「标记签收」等价。

    发货时不在这里写订单：创建运单的端点已经把 status/carrier/tracking_no 写好了。

    ★ 外部（PEAKTRACE 代发）订单例外：物流妥投 ≠ 用户确认收货。供应商回传的「已签收」
    只推进外部履约表的 fulfill_status（在 external_fulfillment.apply_upstream 内处理），
    绝不能在这里把 gear_orders 直接置 delivered —— 否则会越过 C 端「确认收货」按钮、
    绕过 10 天自动确认，让订单提前终态。外部订单的完成一律走 _complete_receipt
    （confirm-receipt 端点 / 平台 PATCH delivered / 自动确认循环）。
    """
    if (shipment or {}).get('status') != 'delivered':
        return None
    # 外部代发（纯外部 / 混合）：物流妥投不自动终结本地订单，等用户确认或自动确认。
    if external_fulfillment.order_external_mode(c, order_id) in ('pure', 'mixed'):
        return None
    o = row(c.execute('SELECT status FROM gear_orders WHERE id=?', (order_id,)))
    if not o or o['status'] in ('delivered', 'refunded'):
        return None
    c.execute("UPDATE gear_orders SET status='delivered',delivered_at=COALESCE(delivered_at,CURRENT_TIMESTAMP)"
              " WHERE id=?", (order_id,))
    try:
        return commission_engine.freeze_order(c, order_id=order_id)
    except Exception:
        return None


@app.get('/api/platform/logistics/carriers')
def platform_logistics_carriers():
    return logistics_engine.CARRIERS


def _guard_external_order(c,order_id:int):
    """纯外部（PEAKTRACE 代发）订单禁止本地拣货/打包/发货。

    货根本不进本地仓：镜像库存只在结算时扣账，物理发货由供应商完成。
    放行的话运营会拿着从未存在的本地库存去拣货，或者填一个与供应商
    平行的运单 —— 顾客端就会出现两条互相矛盾的物流。
    混合订单不拦：自营部分仍需本地履约。
    """
    mode=external_fulfillment.order_external_mode(c,order_id)
    if mode=='pure':
        raise HTTPException(409,'PEAKTRACE 代发订单：商品由供应商直接发货，请等待供应商回传物流（可用「同步外部履约」手动拉取）')


@app.post('/api/platform/orders/{order_id}/shipment')
def platform_create_shipment(order_id: int, payload: dict = Body(...)):
    """发货：建运单 + 写订单。承运商既接受编码也接受名称（运营可能直接填中文）。"""
    with conn() as c:_guard_external_order(c,order_id)
    tracking = str(payload.get('trackingNo') or '').strip()
    if not tracking:
        raise HTTPException(400, '请填写物流单号')
    code = str(payload.get('carrierCode') or '').strip().upper()
    carrier = str(payload.get('carrier') or '').strip()
    if code:
        meta = logistics_engine.CARRIER_BY_CODE.get(code)
        if not meta:
            raise HTTPException(400, '未知承运商编码')
        carrier = carrier or meta['name']
    else:
        # 只给了中文名：反查编码，查不到按「其他」处理，不阻断发货。
        meta = next((c for c in logistics_engine.CARRIERS if c['name'] == carrier), None)
        code = (meta or {}).get('code', 'OTHER')
    with conn() as c:
        o = row(c.execute('SELECT id,status FROM gear_orders WHERE id=?', (order_id,)))
    if not o:
        raise HTTPException(404, '装备订单不存在')
    if o['status'] in ('shipped','delivered'):
        with conn() as c:
            old=logistics_engine.get_shipment(c,order_id)
            if old and old['tracking_no']==tracking:return {'ok':True,'idempotent':True,'shipmentId':old['id'],'commerceSync':_sync_order_shipment_to_medusa(order_id)}
        raise HTTPException(409,'订单已发货，物流更正需单独登记，不能再次出库')
    if o['status'] not in ('paid','processing','confirmed'):
        raise HTTPException(409, '已退款订单不能发货')
    with conn() as c:
        try:warehouse_engine.validate_dispatch(c,order_id)
        except ValueError as e:raise HTTPException(409,str(e))
        if not c.execute("SELECT 1 FROM warehouse_tasks WHERE order_id=? AND task_type='pack' AND status='packed'",(order_id,)).fetchone():raise HTTPException(409,'请先完成拣货和打包')
    cfg = _logistics_config()
    provider = logistics_engine.get_provider(cfg)
    try:
        result = provider.create_shipment(order_id=order_id, carrier=carrier,
                                          carrier_code=code, tracking_no=tracking)
    except logistics_engine.LogisticsError as exc:
        raise HTTPException(502, f'物流服务商返回失败：{exc}')
    with conn() as c:
        try:warehouse_engine.dispatch_order(c,order_id=order_id,actor_type='platform')
        except ValueError as e:raise HTTPException(409,str(e))
        sid = logistics_engine.upsert_shipment(
            c, order_id=order_id, provider=provider.code, carrier=carrier, carrier_code=code,
            tracking_no=tracking, status=result.get('status') or 'in_transit',
            events=result.get('events') or [], raw=result)
        c.execute("UPDATE gear_orders SET carrier=?,tracking_no=?,status='shipped',"
                  "shipped_at=COALESCE(shipped_at,CURRENT_TIMESTAMP)"
                  " WHERE id=? AND status NOT IN ('delivered','refunded')", (carrier, tracking, order_id))
        s = logistics_engine.get_shipment(c, order_id)
        _apply_order_progress(c, order_id, s or {})
    return {'ok': True, 'shipmentId': sid, 'carrier': carrier, 'carrierCode': code,'commerceSync':_sync_order_shipment_to_medusa(order_id),
            'trackingNo': tracking, 'status': (s or {}).get('status') or 'in_transit'}


@app.get('/api/platform/orders/{order_id}/shipment')
def platform_order_shipment(order_id: int):
    return _shipment_payload(order_id)


@app.post('/api/platform/orders/{order_id}/shipment/events')
def platform_append_shipment_events(order_id: int, payload: dict = Body(...)):
    """手动补录一条轨迹（服务商还没对接、或线下临时改派时用）。"""
    desc = str(payload.get('description') or '').strip()
    if not desc:
        raise HTTPException(400, '请填写轨迹描述')
    with conn() as c:
        s = logistics_engine.get_shipment(c, order_id)
        if not s:
            raise HTTPException(404, '该订单还没有运单，请先填写物流单号')
        ev = {'event_time': str(payload.get('eventTime') or logistics_engine.now_utc()),
              'status': str(payload.get('status') or logistics_engine.status_from_text(desc)),
              'description': desc, 'location': str(payload.get('location') or '')}
        out = logistics_engine.append_events(c, s['id'], [ev])
        cur = logistics_engine.get_shipment(c, order_id)
        _apply_order_progress(c, order_id, cur or {})
    return {'ok': True, **out}


@app.post('/api/platform/orders/{order_id}/shipment/sync')
def platform_sync_shipment(order_id: int):
    """主动向服务商拉一次轨迹（回调不可达时的兜底，也是「刷新轨迹」按钮的实现）。"""
    with conn() as c:
        s = logistics_engine.get_shipment(c, order_id)
    if not s:
        raise HTTPException(404, '该订单还没有运单')
    cfg = _logistics_config()
    provider = logistics_engine.get_provider({**cfg, 'provider': s.get('provider') or cfg.get('provider')})
    try:
        events = provider.fetch_events(s)
    except logistics_engine.LogisticsError as exc:
        raise HTTPException(502, f'查询物流轨迹失败：{exc}')
    with conn() as c:
        out = logistics_engine.append_events(c, s['id'], events)
        cur = logistics_engine.get_shipment(c, order_id)
        _apply_order_progress(c, order_id, cur or {})
        timeline = logistics_engine.list_events(c, s['id'])
    return {'ok': True, 'added': out['added'], 'status': out['status'], 'events': timeline}


@app.post('/api/webhooks/logistics/{provider_code}')
async def logistics_webhook(provider_code: str, request: Request):
    """承运商回调入口（无会话，凭 token 校验）。

    安全口径：配置里没有 token 时一律拒绝 —— 回调写的是轨迹数据，
    开放等于让任何人往任意运单里塞「已签收」。
    """
    code = str(provider_code or '').strip().lower()
    if code not in logistics_engine.PROVIDERS:
        raise HTTPException(404, '未知物流服务商')
    cfg = _logistics_config()
    expected = str(cfg.get('token') or '').strip()
    if not expected:
        raise HTTPException(403, '物流回调未启用（未配置 token）')
    got = (request.headers.get('x-clubos-token') or request.query_params.get('token') or '').strip()
    if not got or not secrets.compare_digest(got, expected):
        raise HTTPException(401, 'token 校验失败')
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, 'invalid JSON')
    provider = logistics_engine.get_provider({**cfg, 'provider': code})
    events = provider.parse_webhook(body if isinstance(body, dict) else {})
    tracking = str((body or {}).get('trackingNo') or (body or {}).get('nu') or '').strip()
    if not tracking and isinstance(body, dict):
        tracking = str(((body.get('lastResult') or {}).get('nu')) or '').strip()
    if not tracking:
        raise HTTPException(400, '缺少运单号')
    with conn() as c:
        s = row(c.execute('SELECT * FROM logistics_shipments WHERE tracking_no=? ORDER BY id DESC LIMIT 1',
                          (tracking,)))
        if not s:
            return {'ok': False, 'reason': 'unknown tracking_no'}
        out = logistics_engine.append_events(c, s['id'], events)
        cur = logistics_engine.get_shipment(c, s['order_id'])
        _apply_order_progress(c, s['order_id'], cur or {})
    return {'ok': True, 'added': out['added'], 'status': out['status']}


def _medusa_fulfillment_items(local_order:dict,medusa_order:dict):
    with conn() as c:
        items=rows(c.execute('SELECT i.*,v.commerce_variant_id FROM gear_order_items i LEFT JOIN product_variants v ON v.id=i.variant_id WHERE i.order_id=?',(local_order['id'],)))
    remaining={};by_product={}
    for it in items:
        key=str(it.get('commerce_variant_id') or '')
        if key:remaining[key]=remaining.get(key,0)+int(it['quantity'])
        by_product.setdefault(str(it['product_id']),[]).append(it)
    out=[];matched=0
    for remote in medusa_order.get('items') or []:
        vid=str(remote.get('variant_id') or (remote.get('variant') or {}).get('id') or '')
        if vid and vid in remaining:
            q=min(remaining[vid],int(remote.get('quantity') or remaining[vid]));remaining[vid]-=q
        else:
            pid=str((remote.get('metadata') or {}).get('clubos_product_id') or '')
            candidates=by_product.get(pid,[])
            if len(candidates)==1:q=int(candidates[0]['quantity']);by_product[pid]=[]
            elif len(items)==1 and len(medusa_order.get('items') or [])==1:q=int(items[0]['quantity'])
            else:raise HTTPException(409,'Medusa 订单缺少准确 SKU 对应关系，请核对后同步')
        if q:out.append({'id':remote['id'],'quantity':q});matched+=q
    if matched!=sum(int(it['quantity']) for it in items):raise HTTPException(409,'Medusa SKU 履约数量与本地订单不一致')
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
        try:return warehouse_engine.move_stock(c,product_id=int(payload.get('productId') or 0),from_location_id=int(payload.get('fromLocationId') or 0),to_location_id=int(payload.get('toLocationId') or 0),quantity=int(payload.get('quantity') or 0),note=str(payload.get('note') or '仓内移库'),variant_id=payload.get('variantId'))
        except ValueError as e:raise HTTPException(409,str(e))

@app.post('/api/platform/warehouse/count')
def platform_warehouse_count(payload:dict=Body(...)):
    with conn() as c:
        try:out=warehouse_engine.count_location(c,product_id=int(payload.get('productId') or 0),location_id=int(payload.get('locationId') or 0),counted_on_hand=int(payload.get('countedOnHand') or 0),note=str(payload.get('note') or '库位盘点'),variant_id=payload.get('variantId'))
        except ValueError as e:raise HTTPException(409,str(e))
    out['commerceSync']=_safe_sync_inventory_products([int(payload.get('productId') or 0)])
    return out

@app.get('/api/platform/warehouse/tasks')
def platform_warehouse_tasks(status:str|None=None):
    with conn() as c:return warehouse_engine.tasks(c,status=status)

@app.post('/api/platform/orders/{order_id}/pick')
def platform_order_pick(order_id:int):
    with conn() as c:
        _guard_external_order(c,order_id)
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
        _guard_external_order(c,order_id)
        try:return warehouse_engine.pack_order(c,order_id=order_id,note=str(payload.get('note') or ''))
        except ValueError as e:raise HTTPException(409,str(e))

def _ensure_wms_order_reservation(c,order_id:int):
    items=rows(c.execute('SELECT product_id,variant_id,SUM(quantity) quantity FROM gear_order_items WHERE order_id=? GROUP BY product_id,variant_id',(order_id,)))
    for it in items:
        v=stock_control.sku(c,it['product_id'],it.get('variant_id'))
        r=c.execute("SELECT 1 FROM warehouse_sku_reservations WHERE order_id=? AND variant_id=? AND status IN ('reserved','dispatched')",(order_id,v['id'])).fetchone()
        if not r:inventory_engine.sale_outbound(c,product_id=it['product_id'],variant_id=v['id'],quantity=it['quantity'],order_id=order_id,actor_type='wms_compat')

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

def _sync_order_shipment_to_medusa(order_id:int):
    if commerce_provider()!='medusa':return {'status':'local'}
    try:
        with conn() as c:o=row(c.execute('SELECT * FROM gear_orders WHERE id=?',(order_id,)))
        if not o or o['status'] not in ('shipped','delivered'):raise ValueError('本地订单尚未实际发货')
        if not o.get('commerce_order_id'):raise ValueError('订单未关联 Medusa')
        client=MedusaClient();remote=client.get_admin_order(str(o['commerce_order_id']));items=_medusa_fulfillment_items(o,remote)
        fulfillments=remote.get('fulfillments') or []
        f=next((f for f in fulfillments if f.get('id')==o.get('commerce_fulfillment_id')),None)
        if not f and fulfillments:f=fulfillments[0]
        if not f:f=(client.create_fulfillment(str(o['commerce_order_id']),items).get('fulfillment') or {})
        fid=f.get('id')
        if not fid:raise ValueError('Medusa 未返回履约凭证，请核对后重试')
        with conn() as c:c.execute('UPDATE gear_orders SET commerce_fulfillment_id=? WHERE id=?',(fid,order_id))
        if not f.get('shipped_at') and not f.get('shipments'):
            client.create_shipment(str(o['commerce_order_id']),str(fid),items,tracking_number=o['tracking_no'])
        with conn() as c:c.execute("UPDATE gear_orders SET commerce_sync_status='shipped',commerce_sync_error=NULL WHERE id=?",(order_id,))
        return {'status':'synced'}
    except Exception as e:
        with conn() as c:c.execute("UPDATE gear_orders SET commerce_sync_status='shipment_error',commerce_sync_error=? WHERE id=?",(str(e),order_id))
        return {'status':'error','message':str(e)}

@app.post('/api/platform/orders/{order_id}/ship')
def platform_ship_order(order_id:int,payload:dict=Body(...)):
    return {**platform_create_shipment(order_id,payload),'status':'shipped'}


# ==========================================================================
# 外部履约桥（2026-11-06）：PEAKTRACE 代发订单的推送 / 拉取 / 回调
# 自动链路在 _peaktrace_auto_loop；这里是运营手动兜底 + 供应商主动回调。
# ==========================================================================
@app.post('/api/platform/external-fulfillment/sync')
def platform_external_fulfillment_sync():
    """手动同步：把待推订单推给供应商 + 拉取已推订单的发货状态。

    三段式（读 → HTTP → 落库）在这里拼装，网络等待不占用 SQLite 写事务。
    """
    with conn() as c:
        due=external_fulfillment.due_pushes(c)
    outs=external_fulfillment.push_orders(due)
    with conn() as c:
        push=external_fulfillment.apply_push_results(c,outs)
        pulls=external_fulfillment.due_pulls(c)
    pull_outs=external_fulfillment.pull_orders(pulls)
    with conn() as c:
        pull=external_fulfillment.apply_pull_results(c,pull_outs)
    return {'ok':True,'push':push,'pull':pull}


# ==========================================================================
# PEAKTRACE 销售结账单（2026-11-06 / #638–#639）
#
# v1 只是「代发订单流水」：全量行一把梭，推送失败 / 还没发货的订单也被算进
# 「应付 PEAKTRACE」，财务照着这张表付款会多付。v2 按真实财务对账单口径重做：
#   · 账期：日期区间 + 日期基准（下单日 / 推送日 / 妥投日）。账单号由账期与
#     筛选口径稳定派生，同一份账单可反复导出、引用、核对。
#   · 可结算资格（billable）：已推送成功 + 上游已妥投 + 主订单确实发过货 +
#     主订单未退款/售后。「妥投但 shipped_at 为空」正是 #625 串单的指纹，
#     必须排除，否则会把旧单的妥投当成新单的结算依据。
#   · 结算状态：pending 待结算 / settled 已结算 / void 已冲销。出账后批量
#     标 settled，下期账单默认不再计入，避免重复付款。
#   · 合计拆分：流水合计 vs 本期可结算（未结）vs 累计已结算；另有分组小计、
#     人民币大写金额、金额构成（单价 × 数量）、异常标记，便于逐笔对账。
#   · 出账审计：下载 / 打印 / 出具账单 / 标记状态都落 pk_settlement_audit，
#     含账期与筛选条件快照，争议时可回溯。
# 兼容：不带任何参数时仍返回 {rate, orders, totals}，旧调用方无感。
# ==========================================================================
PK_SETTLE_STATUSES = ('pending', 'settled', 'void')
PK_SETTLE_CN = {'pending': '待结算', 'settled': '已结算', 'void': '已冲销'}
PK_FULFILL_CN = {'awaiting_shipment': '待发货', 'shipped': '已发货', 'delivered': '已妥投'}
PK_PUSH_CN = {'pending': '待推送', 'pushed': '已推送', 'failed': '推送失败'}
PK_DATE_BY = {'order': '下单日', 'pushed': '推送日', 'fulfilled': '妥投日'}
PK_GROUP_BY = {'none': '不分组', 'brand': '按品牌', 'club': '按俱乐部', 'date': '按日期',
               'month': '按月份', 'settle_status': '按结算状态', 'fulfill_status': '按履约状态'}
PK_AUDIT_CN = {'export': '下载 CSV', 'print': '打印账单', 'issue': '出具账单', 'mark': '标记结算状态'}


def _pk_digits_cn(n):
    """整数转中文大写（账单合计用），分段处理 万/亿/兆 与中间的零。"""
    d = '零壹贰叁肆伍陆柒捌玖'
    small = ['', '拾', '佰', '仟']
    big = ['', '万', '亿', '兆']
    if n <= 0:
        return '零'
    parts = []
    x, gi = int(n), 0
    while x > 0:
        parts.append((x % 10000, gi)); x //= 10000; gi += 1
    res = ''
    for k in range(len(parts) - 1, -1, -1):
        val, idx = parts[k]
        seg, zero = '', False
        for i, ch in enumerate(str(val).zfill(4)):
            v = int(ch)
            if v == 0:
                zero = True
            else:
                if zero and seg: seg += '零'
                seg += d[v] + small[3 - i]
                zero = False
        if seg:
            if res and val < 1000: res += '零'
            res += seg + big[idx]
    return res or '零'


def _rmb_upper(n):
    """人民币金额大写，打印账单签字页与 CSV 抬头共用。"""
    n = round(float(n or 0) + 1e-9, 2)
    if n == 0:
        return '零元整'
    sign = '负' if n < 0 else ''
    a = abs(n); yuan = int(a); cents = int(round((a - yuan) * 100))
    jiao, fen = divmod(cents, 10)
    d = '零壹贰叁肆伍陆柒捌玖'
    s = sign + _pk_digits_cn(yuan) + '元'
    if jiao == 0 and fen == 0: return s + '整'
    if jiao == 0: return s + '零' + d[fen] + '分'
    return s + d[jiao] + '角' + (d[fen] + '分' if fen else '整')


def _pk_date_ok(v):
    try:
        date.fromisoformat(v); return True
    except Exception:
        return False


def _pk_filters(params):
    """把 query/body 归一化成筛选条件，非法值一律落回安全默认。"""
    def s(k, d=''):
        v = params.get(k, d)
        return str(v if v is not None else '').strip()
    def f(k):
        try:
            v = params.get(k, '')
            return float(v) if str(v).strip() != '' else None
        except (TypeError, ValueError):
            return None
    d = {'date_from': s('date_from'), 'date_to': s('date_to'),
         'date_by': s('date_by', 'order') or 'order', 'brand': s('brand'), 'club': s('club'),
         'settle_status': s('settle_status', 'all') or 'all',
         'fulfill_status': s('fulfill_status', 'all') or 'all',
         'push_status': s('push_status', 'all') or 'all', 'q': s('q'),
         'min_amount': f('min_amount'), 'max_amount': f('max_amount'),
         'group_by': s('group_by', 'none') or 'none',
         'billable': str(s('billable')).lower() in ('1', 'true', 'yes')}
    if d['date_by'] not in PK_DATE_BY: d['date_by'] = 'order'
    if d['group_by'] not in PK_GROUP_BY: d['group_by'] = 'none'
    for k, allowed in (('settle_status', PK_SETTLE_STATUSES),
                       ('fulfill_status', tuple(PK_FULFILL_CN)),
                       ('push_status', tuple(PK_PUSH_CN))):
        if d[k] not in (*allowed, 'all'): d[k] = 'all'
    if not _pk_date_ok(d['date_from']): d['date_from'] = ''
    if not _pk_date_ok(d['date_to']): d['date_to'] = ''
    return d


def _pk_filter_desc(f):
    """筛选条件的人话描述，写进 CSV 抬头和打印页，避免账单脱离口径。"""
    parts = []
    if f['date_from'] or f['date_to']:
        parts.append('账期 %s ~ %s（%s）' % (f['date_from'] or '不限', f['date_to'] or '不限', PK_DATE_BY[f['date_by']]))
    for k, label in (('brand', '品牌'), ('club', '俱乐部'), ('q', '关键词')):
        if f[k]: parts.append('%s=%s' % (label, f[k]))
    for k, label, cn in (('settle_status', '结算', PK_SETTLE_CN),
                         ('fulfill_status', '履约', PK_FULFILL_CN),
                         ('push_status', '推送', PK_PUSH_CN)):
        if f[k] != 'all': parts.append('%s=%s' % (label, cn.get(f[k], f[k])))
    if f['min_amount'] is not None or f['max_amount'] is not None:
        parts.append('应付 %s ~ %s' % (f['min_amount'] if f['min_amount'] is not None else '不限',
                                      f['max_amount'] if f['max_amount'] is not None else '不限'))
    if f['billable']: parts.append('仅可结算')
    return '；'.join(parts) or '全部代发订单'


def _pk_date_of(r, by):
    v = r['orderCreated'] if by == 'order' else (r['pushedAt'] if by == 'pushed' else r['fulfilledAt'])
    return (v or '')[:10]


def _pk_build(params):
    """销售结账单唯一数据源：GET / export / 出具账单 / 审计全部走这里，口径不会漂。"""
    f = _pk_filters(params)
    rate = float(os.environ.get('PEAKTRACE_SETTLEMENT_RATE', '1.0') or '1.0')
    with conn() as c:
        base = rows(c.execute("""SELECT e.*, b.name brand_name, b.settlement_mode, b.settlement_ratio,
                                        g.user_id, g.total, g.created_at order_created, g.status order_status,
                                        g.shipped_at, g.delivered_at, g.refunded_at, g.after_sales_status,
                                        g.tracking_no order_tracking_no, g.carrier order_carrier,
                                        u.name buyer, cl.name club_name
                                 FROM external_fulfillment_orders e
                                 LEFT JOIN product_brands b ON b.id=e.brand_id
                                 LEFT JOIN gear_orders g ON g.id=e.order_id
                                 LEFT JOIN users u ON u.id=g.user_id
                                 LEFT JOIN clubs cl ON cl.id=g.source_club_id
                                 ORDER BY e.id DESC"""))
        allrows = []
        for e in base:
            oid = int(e['order_id'])
            payload = jload(e.get('payload_json') or '{}', {})
            pitems = payload.get('items') or []
            psku = {}
            for p in pitems:
                psku.setdefault(str(p.get('name') or ''), str(p.get('sku') or ''))
            # 金额一律以 gear_order_items 的成交单价 × 数量为真源；payload 只补上游 SKU。
            dbitems = rows(c.execute("""SELECT i.unit_price,i.quantity,p.name product_name,p.source,p.inventory_mode
                                         FROM gear_order_items i JOIN products p ON p.id=i.product_id
                                         WHERE i.order_id=? ORDER BY i.id""", (oid,)))
            items, ext_sub, qty = [], 0.0, 0
            for it in dbitems:
                is_ext = (it['source'] == 'external_api'
                          or (it['inventory_mode'] or '') not in ('', 'platform_owned'))
                if not is_ext:
                    continue
                up = float(it['unit_price'] or 0); qn = int(it['quantity'] or 0)
                sub = round(up * qn, 2)
                ext_sub += sub; qty += qn
                nm = str(it['product_name'] or '')
                sku = psku.get(nm, '')
                if not sku and len(items) < len(pitems):
                    sku = str(pitems[len(items)].get('sku') or '')
                items.append({'sku': sku, 'name': nm, 'unitPrice': up,
                              'quantity': qn, 'subtotal': sub})
            br = float(e.get('settlement_ratio') or 0)
            brand_mode = (e.get('settlement_mode') == 'sales_ratio' and br > 0)
            use_ratio = br if brand_mode else rate
            payable = round(ext_sub * use_ratio, 2)
            settle_status = str(e.get('settle_status') or 'pending')
            if settle_status not in PK_SETTLE_STATUSES: settle_status = 'pending'
            fulfill_status = str(e.get('fulfill_status') or 'awaiting_shipment')
            push_status = str(e.get('push_status') or 'pending')
            order_status = str(e.get('order_status') or '')
            # 可结算资格：四个硬条件，任一不满足都不该进入付款合计。
            blocked = []
            if push_status != 'pushed': blocked.append('未成功推送供应商')
            if fulfill_status != 'delivered': blocked.append('上游未妥投')
            if not (e.get('shipped_at') or ''): blocked.append('主订单无发货时间')
            if order_status in ('refunded', 'cancelled', 'after_sales') or (e.get('refunded_at') or ''):
                blocked.append('主订单已退款/售后')
            if settle_status == 'void': blocked.append('已冲销')
            anomalies = []
            if push_status == 'failed': anomalies.append('推送失败')
            if fulfill_status == 'delivered' and not (e.get('shipped_at') or ''):
                anomalies.append('妥投但无发货时间（疑似串单）')
            if e.get('pull_error'): anomalies.append('状态同步异常')
            if not e.get('external_order_id'): anomalies.append('缺上游单号')
            allrows.append({
                'orderId': oid,
                'externalRef': e.get('external_ref') or '',
                'externalOrderId': e.get('external_order_id') or '',
                'brand': e.get('brand_name') or 'PEAKTRACE', 'brandId': e.get('brand_id'),
                'buyer': e.get('buyer') or '', 'club': e.get('club_name') or '',
                'items': items, 'itemCount': len(items), 'quantity': qty,
                'externalSubtotal': round(ext_sub, 2),
                'settlementRate': use_ratio,
                'settlementRateSource': 'brand' if brand_mode else 'global',
                'payable': payable,
                'settleStatus': settle_status, 'settleStatusCn': PK_SETTLE_CN[settle_status],
                'settledAt': e.get('settled_at') or '', 'settleNote': e.get('settle_note') or '',
                'pushStatus': push_status, 'pushStatusCn': PK_PUSH_CN.get(push_status, push_status),
                'fulfillStatus': fulfill_status, 'fulfillStatusCn': PK_FULFILL_CN.get(fulfill_status, fulfill_status),
                'pushError': e.get('push_error') or '', 'pullError': e.get('pull_error') or '',
                'carrier': e.get('carrier') or e.get('order_carrier') or '',
                'trackingNo': e.get('tracking_no') or e.get('order_tracking_no') or '',
                'pushedAt': e.get('pushed_at') or '', 'fulfilledAt': e.get('fulfilled_at') or '',
                'shippedAt': e.get('shipped_at') or '', 'updatedAt': e.get('updated_at') or '',
                'orderCreated': e.get('order_created') or '', 'orderStatus': order_status,
                'billable': not blocked, 'blockedReasons': blocked, 'anomalies': anomalies,
            })
        q = f['q'].lower()
        kept = []
        for r in allrows:
            if f['date_from'] or f['date_to']:
                dv = _pk_date_of(r, f['date_by'])
                if not dv: continue
                if f['date_from'] and dv < f['date_from']: continue
                if f['date_to'] and dv > f['date_to']: continue
            if f['brand'] and r['brand'] != f['brand']: continue
            if f['club'] and r['club'] != f['club']: continue
            if f['settle_status'] != 'all' and r['settleStatus'] != f['settle_status']: continue
            if f['fulfill_status'] != 'all' and r['fulfillStatus'] != f['fulfill_status']: continue
            if f['push_status'] != 'all' and r['pushStatus'] != f['push_status']: continue
            if f['billable'] and not r['billable']: continue
            if f['min_amount'] is not None and r['payable'] < f['min_amount']: continue
            if f['max_amount'] is not None and r['payable'] > f['max_amount']: continue
            if q:
                hay = ' '.join([str(r['orderId']), r['externalRef'], r['externalOrderId'],
                                r['brand'], r['club'], r['buyer'], r['trackingNo']]
                               + [str(i.get('sku') or '') + ' ' + str(i.get('name') or '') for i in r['items']]).lower()
                if q not in hay: continue
            kept.append(r)

    cnt = len(kept)
    sub = round(sum(r['externalSubtotal'] for r in kept), 2)
    pay = round(sum(r['payable'] for r in kept), 2)
    qty = sum(r['quantity'] for r in kept)
    billable_rows = [r for r in kept if r['billable']]
    pending_pay = round(sum(r['payable'] for r in billable_rows if r['settleStatus'] == 'pending'), 2)
    settled_pay = round(sum(r['payable'] for r in billable_rows if r['settleStatus'] == 'settled'), 2)
    void_pay = round(sum(r['payable'] for r in kept if r['settleStatus'] == 'void'), 2)
    by_status = {}
    for k, cn in PK_SETTLE_CN.items():
        sel = [r for r in kept if r['settleStatus'] == k]
        by_status[k] = {'label': cn, 'count': len(sel),
                        'payable': round(sum(r['payable'] for r in sel), 2)}

    groups = []
    gby = f['group_by']
    if gby != 'none':
        buckets = {}
        for r in kept:
            if gby == 'brand': key = r['brand'] or '未标注品牌'
            elif gby == 'club': key = r['club'] or '未归属俱乐部'
            elif gby == 'date': key = _pk_date_of(r, f['date_by']) or '无日期'
            elif gby == 'month': key = (_pk_date_of(r, f['date_by']) or '')[:7] or '无日期'
            elif gby == 'settle_status': key = r['settleStatusCn']
            else: key = r['fulfillStatusCn']
            b = buckets.setdefault(key, {'key': key, 'count': 0, 'quantity': 0,
                                         'externalSubtotal': 0.0, 'payable': 0.0, 'billablePayable': 0.0})
            b['count'] += 1; b['quantity'] += r['quantity']
            b['externalSubtotal'] += r['externalSubtotal']; b['payable'] += r['payable']
            if r['billable']: b['billablePayable'] += r['payable']
        groups = [{'key': b['key'], 'count': b['count'], 'quantity': b['quantity'],
                   'externalSubtotal': round(b['externalSubtotal'], 2),
                   'payable': round(b['payable'], 2),
                   'billablePayable': round(b['billablePayable'], 2)}
                  for b in sorted(buckets.values(), key=lambda x: x['key'])]

    all_dates = [r['orderCreated'][:10] for r in allrows if r['orderCreated']]
    period_from = f['date_from'] or (min(all_dates) if all_dates else '')
    period_to = f['date_to'] or (max(all_dates) if all_dates else '')
    # 账单号只由「账期 + 影响订单集合的筛选」决定。group_by 纯展示（不改集合），
    # 不能进 hash —— 否则同一份账单切一下分组就换号，财务没法引用同一张单。
    seed = json.dumps({'from': period_from, 'to': period_to,
                       **{k: f[k] for k in ('date_by', 'brand', 'club', 'settle_status',
                                            'fulfill_status', 'push_status', 'q',
                                            'min_amount', 'max_amount', 'billable')}},
                      ensure_ascii=False, sort_keys=True, default=str)
    token = hashlib.sha256(seed.encode('utf-8')).hexdigest()[:6].upper()
    compact = lambda d: (d or '00000000').replace('-', '')
    no = 'PK-%s-%s-%s' % (compact(period_from), compact(period_to), token)
    statement = {
        'no': no, 'periodFrom': period_from, 'periodTo': period_to,
        'dateBy': f['date_by'], 'dateByCn': PK_DATE_BY[f['date_by']],
        'groupByCn': PK_GROUP_BY[gby],
        'generatedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'currency': 'CNY', 'rate': rate,
        'payer': 'ClubOS 总平台（远拓户外）', 'payee': 'PEAKTRACE',
        'title': 'PEAKTRACE 销售结账单',
        'filterDesc': _pk_filter_desc(f),
        'payableInWords': _rmb_upper(pending_pay),
        'totalInWords': _rmb_upper(pay),
    }
    totals = {
        'count': cnt, 'externalSubtotal': sub, 'payable': pay, 'quantity': qty,
        'billableCount': len(billable_rows),
        'billablePayable': round(sum(r['payable'] for r in billable_rows), 2),
        'pendingPayable': pending_pay, 'settledPayable': settled_pay, 'voidPayable': void_pay,
        'byStatus': by_status, 'amountInWords': _rmb_upper(pay),
    }
    return {
        'rate': rate, 'orders': kept, 'totals': totals, 'statement': statement,
        'filters': {
            'brands': sorted({r['brand'] for r in allrows if r['brand']}),
            'clubs': sorted({r['club'] for r in allrows if r['club']}),
            'settleStatuses': [{'value': k, 'label': v} for k, v in PK_SETTLE_CN.items()],
            'fulfillStatuses': [{'value': k, 'label': v} for k, v in PK_FULFILL_CN.items()],
            'pushStatuses': [{'value': k, 'label': v} for k, v in PK_PUSH_CN.items()],
            'dateBy': [{'value': k, 'label': v} for k, v in PK_DATE_BY.items()],
            'groupBy': [{'value': k, 'label': v} for k, v in PK_GROUP_BY.items()],
        },
        'applied': f, 'groups': groups,
    }


def _pk_audit(c, action, data, note='', operator='platform', *, count=None, payable=None, subtotal=None):
    """出账留痕：只增不改，含账期、筛选快照、笔数与金额。"""
    s, t = data['statement'], data['totals']
    c.execute("""INSERT INTO pk_settlement_audit
                 (action,statement_no,period_from,period_to,filters_json,order_count,
                  external_subtotal,payable,operator,note)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (action, s['no'], s['periodFrom'], s['periodTo'], jdump(data['applied']),
               int(t['count'] if count is None else count),
               float(t['externalSubtotal'] if subtotal is None else subtotal),
               float(t['payable'] if payable is None else payable), operator, note))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


@app.get('/api/platform/external-fulfillment/settlement')
def platform_external_settlement(request: Request):
    """PEAKTRACE 销售结账单：账期 / 多维筛选 / 分组小计 / 可结算口径 / 账单抬头。"""
    return _pk_build(dict(request.query_params))


@app.get('/api/platform/external-fulfillment/settlement/export')
def platform_external_settlement_export(request: Request):
    """按当前筛选导出账单 CSV（UTF-8 BOM，Excel 直开不乱码），并落一条出账审计。"""
    params = dict(request.query_params)
    fmt = str(params.pop('format', 'csv') or 'csv').lower()
    if fmt != 'csv':
        raise HTTPException(400, '仅支持 format=csv')
    data = _pk_build(params)
    s, t, f = data['statement'], data['totals'], data['applied']
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(['PEAKTRACE 销售结账单'])
    w.writerow(['账单号', s['no'], '币种', s['currency'], '生成时间', s['generatedAt']])
    w.writerow(['账期', '%s ~ %s' % (s['periodFrom'], s['periodTo']), '日期口径', s['dateByCn'], '分组', s['groupByCn']])
    w.writerow(['付款方', s['payer'], '收款方', s['payee'], '全局结算比例', s['rate']])
    w.writerow(['筛选口径', s['filterDesc']])
    w.writerow(['笔数', t['count'], '件数', t['quantity'], '外部零售小计', t['externalSubtotal']])
    w.writerow(['本期可结算(未结)', t['pendingPayable'], '累计已结算', t['settledPayable'], '流水应付合计', t['payable']])
    w.writerow(['应付金额大写', s['payableInWords']])
    w.writerow([])
    w.writerow(['序号', 'ClubOS 订单号', '渠道单号', '上游订单号', '账期日期', '俱乐部', '买家', '品牌',
                '商品明细（SKU·名称·单价×数量=小计）', '件数', '外部零售小计', '结算比例', '比例来源',
                '应付 PEAKTRACE', '结算状态', '结算时间', '推送状态', '履约状态', '承运商', '运单号',
                '推送时间', '妥投时间', '是否可结算', '不可结算原因', '异常'])
    for n, r in enumerate(data['orders'], 1):
        detail = '；'.join('%s·%s·¥%.2f×%d=¥%.2f' % (i['sku'] or '-', i['name'],
                          i['unitPrice'], i['quantity'], i['subtotal']) for i in r['items'])
        w.writerow([n, r['orderId'], r['externalRef'], r['externalOrderId'],
                    _pk_date_of(r, f['date_by']), r['club'], r['buyer'], r['brand'], detail,
                    r['quantity'], r['externalSubtotal'], r['settlementRate'],
                    '品牌专属' if r['settlementRateSource'] == 'brand' else '全局默认',
                    r['payable'], r['settleStatusCn'], r['settledAt'],
                    r['pushStatusCn'], r['fulfillStatusCn'], r['carrier'], r['trackingNo'],
                    r['pushedAt'], r['fulfilledAt'], '是' if r['billable'] else '否',
                    '；'.join(r['blockedReasons']), '；'.join(r['anomalies'])])
    w.writerow([])
    w.writerow(['合计', '', '', '', '', '', '', '', '', t['quantity'], t['externalSubtotal'],
                '', '', t['payable'], '', '', '', '', '', '', '', '', '', '', ''])
    w.writerow(['本期可结算(未结)', t['pendingPayable'], '累计已结算', t['settledPayable'],
                '已冲销', t['voidPayable'], '大写', s['payableInWords']])
    if data['groups']:
        w.writerow([])
        w.writerow(['分组小计（%s）' % s['groupByCn'], '笔数', '件数', '外部零售小计', '应付', '其中可结算'])
        for g in data['groups']:
            w.writerow([g['key'], g['count'], g['quantity'], g['externalSubtotal'],
                        g['payable'], g['billablePayable']])
    with conn() as c:
        _pk_audit(c, 'export', data, '下载 CSV 账单')
    return Response(content=('\ufeff' + buf.getvalue()).encode('utf-8'),
                    media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="pk_settlement_%s.csv"' % s['no']})


@app.get('/api/platform/external-fulfillment/settlement/audit')
def platform_external_settlement_audit(limit: int = 30):
    """出账记录：谁 / 何时 / 按什么口径导出了哪一份账单。"""
    limit = max(1, min(200, int(limit or 30)))
    with conn() as c:
        rs = rows(c.execute('SELECT * FROM pk_settlement_audit ORDER BY id DESC LIMIT ?', (limit,)))
    return {'items': [{
        'id': r['id'], 'action': r['action'], 'actionCn': PK_AUDIT_CN.get(r['action'], r['action']),
        'statementNo': r['statement_no'], 'periodFrom': r['period_from'], 'periodTo': r['period_to'],
        'filters': jload(r['filters_json'] or '{}', {}), 'orderCount': r['order_count'],
        'externalSubtotal': r['external_subtotal'], 'payable': r['payable'],
        'operator': r['operator'], 'note': r['note'], 'createdAt': r['created_at'],
    } for r in rs]}


@app.post('/api/platform/external-fulfillment/settlement/audit')
async def platform_external_settlement_audit_log(request: Request):
    """打印 / 出具账单留痕。action='issue' 时把本批「可结算且待结算」的行标为已结算。"""
    body = await request.json()
    action = str(body.get('action') or '').strip()
    if action not in ('print', 'issue'):
        raise HTTPException(400, '未知出账动作')
    params = body.get('filters') or {}
    if not isinstance(params, dict):
        raise HTTPException(400, 'filters 格式错误')
    clean = {k: ('1' if v is True else '0' if v is False else v)
             for k, v in params.items() if v not in (None, '')}
    data = _pk_build(clean)
    note = str(body.get('note') or '')[:200]
    issued = 0
    with conn() as c:
        if action == 'issue':
            ids = [r['orderId'] for r in data['orders'] if r['billable'] and r['settleStatus'] == 'pending']
            if ids:
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                c.executemany("""UPDATE external_fulfillment_orders
                                 SET settle_status='settled',settled_at=?,settle_note=?,
                                     updated_at=CURRENT_TIMESTAMP WHERE order_id=?""",
                              [(now, note or ('出具账单 ' + data['statement']['no']), i) for i in ids])
                issued = len(ids)
        aid = _pk_audit(c, action, data, note or ('出具 %d 笔' % issued if action == 'issue' else '打印账单'))
    return {'ok': True, 'auditId': aid, 'statementNo': data['statement']['no'], 'issued': issued}


@app.post('/api/platform/external-fulfillment/settlement/mark')
async def platform_external_settlement_mark(request: Request):
    """批量修正结算状态（已结算 / 已冲销 / 撤销回待结算）。"""
    body = await request.json()
    status = str(body.get('status') or '').strip()
    if status not in PK_SETTLE_STATUSES:
        raise HTTPException(400, '未知结算状态')
    raw = body.get('orderIds') or []
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, '请先选择要标记的订单')
    try:
        ids = sorted({int(x) for x in raw})
    except (TypeError, ValueError):
        raise HTTPException(400, '订单号格式错误')
    note = str(body.get('note') or '')[:200]
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with conn() as c:
        found = rows(c.execute('SELECT order_id FROM external_fulfillment_orders WHERE order_id IN (%s)'
                               % ','.join('?' * len(ids)), ids))
        found_ids = {int(r['order_id']) for r in found}
        missing = [i for i in ids if i not in found_ids]
        if missing:
            raise HTTPException(404, '订单不存在：' + ','.join(map(str, missing[:5])))
        c.executemany("""UPDATE external_fulfillment_orders
                         SET settle_status=?,settled_at=?,settle_note=?,updated_at=CURRENT_TIMESTAMP
                         WHERE order_id=?""",
                      [(status, now if status != 'pending' else '', note, i) for i in ids])
    data = _pk_build({})
    marked = [r for r in data['orders'] if r['orderId'] in found_ids]
    with conn() as c:
        aid = _pk_audit(c, 'mark', data,
                        '批量标记「%s」%d 笔：%s' % (PK_SETTLE_CN[status], len(ids), ','.join(map(str, ids[:12]))),
                        count=len(ids),
                        payable=round(sum(r['payable'] for r in marked), 2),
                        subtotal=round(sum(r['externalSubtotal'] for r in marked), 2))
    return {'ok': True, 'updated': len(ids), 'status': status, 'auditId': aid}


# ==========================================================================
# 平台后台「新增未读」提醒（2026-11-06 / #620）：订单履约 / 售后管理 / PEAKTRACE 结算
#
# 需求口径：「两端后台有新增订单或信息需要有一个提示」。
# 存量待办数（如待发货 3 单）不等于「新增未读」——前者一直挂着，后者是
# 「自上次点开这个栏目以来，又发生了什么」。所以这里用事件账本 + 水位：
#   · 每次 summary 把业务表当前状态折算成去重事件键，INSERT OR IGNORE 补登；
#   · 未读数 = id > 该栏目水位 的事件数；
#   · 点开栏目 → mark-read 把水位推到当前最大事件 id。
# 事件键里带状态，因此「新订单」「发货」「妥投」「售后审核」「B2B 重新发货」
# 都会各自产生一条新事件；不改动任何既有写接口。
# ==========================================================================
PLATFORM_NOTICE_CHANNELS = ('orders', 'aftersales', 'pksettle')


def _platform_notice_candidates(c):
    """从业务表现态派生事件键。返回 [(channel, source, event_key, kind, title, at)]。"""
    ev = []
    # 订单履约：新订单 / 主订单状态推进 / 新物流单号（只看近 30 天，避免扫全表）
    for o in rows(c.execute("""SELECT id, status, created_at, shipped_at, delivered_at, tracking_no
                               FROM gear_orders
                               WHERE created_at >= datetime('now','-30 day')""")):
        oid = int(o['id'])
        created = o.get('created_at') or ''
        changed = o.get('delivered_at') or o.get('shipped_at') or created
        ev.append(('orders', 'gear_orders', f'order:{oid}:new', 'new_order',
                   f'新订单 #{oid}', created))
        ev.append(('orders', 'gear_orders', f'order:{oid}:status:{o.get("status") or ""}',
                   'order_status', f'订单 #{oid} 状态：{o.get("status") or "-"}', changed))
        if o.get('tracking_no'):
            ev.append(('orders', 'gear_orders', f'order:{oid}:tracking:{o["tracking_no"]}',
                       'order_tracking', f'订单 #{oid} 有新物流单号', changed))
    # PEAKTRACE 代发：同步给上游、履约状态、物流单号、结算清单新增行
    for e in rows(c.execute("""SELECT id, order_id, push_status, fulfill_status, tracking_no,
                                      created_at, updated_at, fulfilled_at
                               FROM external_fulfillment_orders""")):
        eid, oid = int(e['id']), int(e['order_id'])
        at = e.get('updated_at') or e.get('created_at') or ''
        ev.append(('orders', 'external_fulfillment_orders', f'efo:{eid}:new',
                   'fulfillment_new', f'代发订单 #{oid} 已同步 PEAKTRACE', e.get('created_at') or at))
        ev.append(('orders', 'external_fulfillment_orders',
                   f'efo:{eid}:fulfill:{e.get("fulfill_status") or ""}',
                   'fulfillment_status', f'代发订单 #{oid} 履约：{e.get("fulfill_status") or "-"}', at))
        if e.get('tracking_no'):
            ev.append(('orders', 'external_fulfillment_orders',
                       f'efo:{eid}:tracking:{e["tracking_no"]}',
                       'fulfillment_tracking', f'代发订单 #{oid} 物流已更新', at))
        ev.append(('pksettle', 'external_fulfillment_orders',
                   f'efo:{eid}:settlement:{e.get("push_status") or ""}',
                   'settlement_line', f'结算清单新增：代发订单 #{oid}', e.get('created_at') or at))
        if e.get('fulfilled_at'):
            ev.append(('pksettle', 'external_fulfillment_orders', f'efo:{eid}:settled',
                       'settlement_settled', f'代发订单 #{oid} 已妥投，可结算', e.get('fulfilled_at')))
    # 售后：新申请 / 状态推进 / PEAKTRACE 退换货回传
    for x in rows(c.execute("""SELECT id, order_id, status, created_at, updated_at, external_sync_status
                               FROM after_sales_cases
                               WHERE created_at >= datetime('now','-30 day')""")):
        cid = str(x['id'])
        at = x.get('updated_at') or x.get('created_at') or ''
        ev.append(('aftersales', 'after_sales_cases', f'as:{cid}:new',
                   'aftersales_new', f'新的售后申请 {cid}（订单 #{x["order_id"]}）', x.get('created_at') or at))
        ev.append(('aftersales', 'after_sales_cases', f'as:{cid}:status:{x.get("status") or ""}',
                   'aftersales_status', f'售后单 {cid} 状态：{x.get("status") or "-"}', at))
        if x.get('external_sync_status'):
            ev.append(('aftersales', 'after_sales_cases',
                       f'as:{cid}:b2b:{x["external_sync_status"]}',
                       'aftersales_b2b', f'售后单 {cid} 的 PEAKTRACE 同步状态：{x["external_sync_status"]}', at))
    return ev


def _materialize_platform_notices(c):
    """补登事件 + 给首次访问的栏目设基线（历史数据不当成「新」）。"""
    evs = _platform_notice_candidates(c)
    if evs:
        c.executemany("""INSERT OR IGNORE INTO platform_notice_events
                         (channel,source,event_key,kind,title,created_at)
                         VALUES(?,?,?,?,?,COALESCE(NULLIF(?,''),CURRENT_TIMESTAMP))""",
                      [(ch, src, key, kind, title, at or '') for ch, src, key, kind, title, at in evs])
    mx = int(c.execute('SELECT COALESCE(MAX(id),0) FROM platform_notice_events').fetchone()[0])
    c.executemany("""INSERT OR IGNORE INTO platform_notice_reads(channel,last_event_id,read_at)
                     VALUES(?,?,CURRENT_TIMESTAMP)""",
                  [(ch, mx) for ch in PLATFORM_NOTICE_CHANNELS])
    return mx


def _platform_notice_counts(c):
    out = {ch: 0 for ch in PLATFORM_NOTICE_CHANNELS}
    for r in rows(c.execute("""SELECT e.channel AS channel, COUNT(*) AS n
                               FROM platform_notice_events e
                               LEFT JOIN platform_notice_reads r ON r.channel=e.channel
                               WHERE e.id > COALESCE(r.last_event_id,0)
                               GROUP BY e.channel""")):
        if r['channel'] in out:
            out[r['channel']] = int(r['n'] or 0)
    return out


@app.get('/api/platform/notices/summary')
def platform_notices_summary():
    """侧栏角标 60 秒轮询的轻量计数。只补登事件 + COUNT，不拉订单/售后明细。"""
    with conn() as c:
        _materialize_platform_notices(c)
        counts = _platform_notice_counts(c)
        latest = rows(c.execute("""SELECT channel,kind,title,created_at FROM platform_notice_events
                                   ORDER BY id DESC LIMIT 5"""))
    return {'channels': counts, 'total': sum(counts.values()), 'latest': latest,
            'channelsLabel': {'orders': '订单履约', 'aftersales': '售后管理', 'pksettle': 'PEAKTRACE 结算'}}


@app.post('/api/platform/notices/read')
def platform_notices_read(payload: dict = Body(default={})):
    """点开栏目即已读：只推进该栏目水位，不删事件、不清别人的未读。"""
    ch = str(payload.get('channel') or '').strip()
    if ch != 'all' and ch not in PLATFORM_NOTICE_CHANNELS:
        raise HTTPException(400, '未知栏目')
    targets = PLATFORM_NOTICE_CHANNELS if ch == 'all' else (ch,)
    with conn() as c:
        _materialize_platform_notices(c)
        mx = int(c.execute('SELECT COALESCE(MAX(id),0) FROM platform_notice_events').fetchone()[0])
        c.executemany("""INSERT INTO platform_notice_reads(channel,last_event_id,read_at)
                         VALUES(?,?,CURRENT_TIMESTAMP)
                         ON CONFLICT(channel) DO UPDATE SET
                           last_event_id=excluded.last_event_id, read_at=CURRENT_TIMESTAMP""",
                      [(x, mx) for x in targets])
        counts = _platform_notice_counts(c)
    return {'ok': True, 'channel': ch, 'lastEventId': mx,
            'channels': counts, 'total': sum(counts.values())}


@app.post('/api/webhooks/external-fulfillment')
async def external_fulfillment_webhook(request:Request):
    """供应商（PEAKTRACE）发货/签收回调入口（无会话，凭 token 校验）。

    token 口径与外部履约推送一致：JSON body.token（托管网关剥自定义头与查询串）。
    没配置 token 时一律拒绝 —— 回调写的是发货状态，开放等于让任何人改物流。
    """
    try: body=await request.json()
    except Exception: raise HTTPException(400,'invalid JSON')
    if not isinstance(body,dict): raise HTTPException(400,'invalid JSON')
    expected=external_fulfillment.webhook_token()
    got=str(body.get('token') or '').strip()
    if not expected or not got or not secrets.compare_digest(got,expected):
        raise HTTPException(401,'token 校验失败')
    with conn() as c:
        try: return external_fulfillment.handle_webhook(c,body)
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

def _push_external_return(case_id:str, case:dict, *, action:str,
                          carrier:str='', tracking:str='') -> dict | None:
    """ClubOS → PEAKTRACE：审核同意 / 用户填退货运单时同步售后单。

    仅「退货退款 / 换货」且订单是 PEAKTRACE 代发时才推。HTTP 在 DB 事务外发，
    结果落 after_sales_cases.external_sync_status 供平台端查看与重试。
    """
    if case.get('case_type') not in ('return_refund','exchange'):
        return None
    with conn() as c:
        if not external_fulfillment.is_external_order(c,int(case['order_id'])):
            return None
        payload=external_fulfillment.build_return_payload(
            c,case,action=action,return_carrier=carrier,return_tracking_no=tracking)
    res=external_fulfillment.push_return(payload)
    with conn() as c:
        external_fulfillment.mark_return_sync(c,case_id,res)
    return {'ok':bool(res.get('ok')),'error':res.get('error')}


@app.post('/api/webhooks/external-fulfillment/after-sales')
async def external_fulfillment_after_sales_webhook(request:Request):
    """PEAKTRACE → ClubOS：确认收到退货 / 换货重新发货。

    上游确认顾客退货后重新发货，回调把换货运单写回售后单；C 端订单卡随即
    显示「换货物流」。纯外部订单跳过本地入库/出库（两端共享库存，不单独建仓）。
    """
    try: body=await request.json()
    except Exception: raise HTTPException(400,'invalid JSON')
    if not isinstance(body,dict): raise HTTPException(400,'invalid JSON')
    expected=external_fulfillment.webhook_token()
    got=str(body.get('token') or '').strip()
    if not expected or not got or not secrets.compare_digest(got,expected):
        raise HTTPException(401,'token 校验失败')
    case_id=str(body.get('clubosCaseId') or '').strip()
    action=str(body.get('action') or '').strip()
    if not case_id: raise HTTPException(400,'缺少 clubosCaseId')
    if action not in ('return_received','exchange_shipped'):
        raise HTTPException(400,'未知 action')
    carrier=str(body.get('carrier') or '').strip()
    tracking=str(body.get('trackingNo') or body.get('tracking_no') or '').strip()
    if action=='exchange_shipped' and not tracking:
        raise HTTPException(400,'重发回调缺少运单号')
    with conn() as c:
        try: case=after_sales_engine.get(c,case_id)
        except LookupError as e: raise HTTPException(404,str(e))
        if not external_fulfillment.is_external_order(c,int(case['order_id'])):
            raise HTTPException(409,'该售后单不是 PEAKTRACE 代发订单')
        if case['status']=='completed':
            return {'ok':True,'caseId':case_id,'status':'completed','action':action,'idempotent':True}
        if action=='exchange_shipped' and case['case_type']!='exchange':
            raise HTTPException(409,'该售后单不是换货单')
        if case['status'] not in ('awaiting_return','return_in_transit','exchange_pending_shipment'):
            raise HTTPException(409,f"当前售后状态({case['status']})不能同步退换货进展")
        try:
            # 上游已确认收到退货：先推进到 exchange_pending_shipment（本地不入库）
            if case['status']=='return_in_transit':
                after_sales_engine.receive_return(c,case_id=case_id,restock=False,
                                                  note='PEAKTRACE 已确认收到退货')
                case=after_sales_engine.get(c,case_id)
            if action=='exchange_shipped':
                out=after_sales_engine.ship_exchange(
                    c,case_id=case_id,carrier=carrier or external_fulfillment.PROVIDER,
                    tracking_no=tracking)
            else:
                out=case
            c.execute("""UPDATE after_sales_cases SET external_sync_status=?,external_sync_error=NULL,
                         updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                      ('return_received' if action=='return_received' else 'reshipped',case_id))
        except ValueError as e: raise HTTPException(409,str(e))
    return {'ok':True,'caseId':case_id,'status':out.get('status'),'action':action}


@app.post('/api/platform/orders/{order_id}/shipment/sync-commerce')
def platform_retry_shipment_commerce(order_id:int):
    return _sync_order_shipment_to_medusa(order_id)

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
        # OBS-03：重复点击 / 网络重试对同一售后单发起退款属于幂等重放，不应报 409。
        # 仅「确实完成过资产冲正」的终态短路；pending_review / awaiting_return /
        # rejected / cancelled 等非法状态仍按状态机 409 拒绝。
        if case['status'] in ('completed','refunded'):
            rid0=str(case.get('refund_request_id') or '')
            rr0=row(c.execute('SELECT * FROM refund_requests WHERE id=?',(rid0,))) if rid0 else None
            marker=c.execute("SELECT 1 FROM gear_point_ledger WHERE type='after_sales_marker' "
                             "AND source_type='gear_after_sales' AND source_id=? LIMIT 1",(case_id,)).fetchone()
            if rid0 or marker:
                return {'ok':True,'caseId':case_id,'status':case['status'],'idempotent':True,
                        'alreadyRefunded':True,'refundRequestId':rid0 or None,
                        'refundStatus':rr0.get('status') if rr0 else None,
                        'cashAmount':float(rr0.get('cash_amount') or 0) if rr0 else 0.0,
                        'message':'该售后单已完成退款，未重复发起'}
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
        try:out=after_sales_engine.approve(c,case_id=case_id,reviewer='platform',approved_refund_amount=payload.get('refundAmount'),note=str(payload.get('note') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))
    # ClubOS 同意退换 → 订单/售后单同步给 PEAKTRACE（HTTP 在事务外，失败不回滚审核）
    out['externalReturnSync']=_push_external_return(case_id,out,action='approved')
    return out

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

@app.get('/api/platform/commission-rates')
def platform_commission_rates():
    """总平台按俱乐部设定的佣金率一览（2026-10-24）。

    生效口径 = 俱乐部专属费率（若单独设定）否则商品默认费率。
    商品默认费率取当前商品的常见值（众数），用于让平台看清「基准是多少」。
    """
    with conn() as c:
        clubs=rows(c.execute('SELECT id,name,status FROM clubs ORDER BY id'))
        # 商品默认费率取众数；没有商品时用 8% 这一库默认值
        mode=c.execute("SELECT commission_rate,COUNT(*) n FROM products GROUP BY commission_rate ORDER BY n DESC LIMIT 1").fetchone()
        product_default=float(mode['commission_rate']) if mode else 0.08
        out=[]
        for cl in clubs:
            r=commission_rate_service.resolve(c,int(cl['id']),product_default)
            d=r.as_dict()
            d.update({'clubId':int(cl['id']),'clubName':str(cl['name'] or ''),'clubStatus':str(cl['status'] or '')})
            out.append(d)
        return {'enabled':commission_rate_service.platform_enabled(),
                'productDefaultRate':round(product_default,6),
                'productDefaultRatePercent':round(product_default*100,2),
                'clubs':out}


@app.patch('/api/platform/commission-rates/{club_id}')
def platform_set_commission_rate(club_id:int,payload:dict=Body(...)):
    """设定 / 清除某个俱乐部的专属佣金率。

    clearOverride=true → 清除单独设定，回到商品默认费率。
    rate 以百分数传入（如 12 表示 12%），避免 0.12 这类小数被误读。
    """
    with conn() as c:
        if not c.execute('SELECT 1 FROM clubs WHERE id=?',(club_id,)).fetchone():
            raise HTTPException(404,'俱乐部不存在')
        try:
            if bool(payload.get('clearOverride')):
                commission_rate_service.clear_club_rate(c,club_id)
            else:
                raw=payload.get('ratePercent',payload.get('rate'))
                if raw is None or raw=='':
                    raise HTTPException(400,'ratePercent 必填')
                rate=float(raw)
                # 兼容两种入参：传 12 视为 12%，传 0.12 视为 12%
                if rate>1: rate=rate/100.0
                commission_rate_service.set_club_rate(
                    c,club_id,rate=rate,
                    enabled=bool(payload.get('enabled',True)),
                    note=str(payload.get('note') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(400,str(e))
        r=commission_rate_service.resolve(c,club_id)
        return {'ok':True,**r.as_dict()}


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
        def _with_account(s:dict)->dict:
            s['hasAccount']=bool(row(c.execute("SELECT id FROM club_settlement_accounts WHERE club_id=? AND status='active'",(s['clubId'],))))
            return s
        if club_id is not None:
            if not row(c.execute('SELECT id FROM clubs WHERE id=?',(club_id,))): raise HTTPException(404,'俱乐部不存在')
            return _with_account(commission_engine.summary(c,club_id=club_id))
        clubs=rows(c.execute('SELECT id,name FROM clubs ORDER BY id'))
        return [_with_account({**commission_engine.summary(c,club_id=int(cl['id'])),'clubName':cl['name']}) for cl in clubs]

@app.get('/api/platform/commissions')
def platform_commissions(club_id:int|None=None,status:str|None=None,date_from:str|None=None,date_to:str|None=None):
    lo,hi=_date_range(date_from,date_to)
    with conn() as c:
        commission_engine.release_matured(c,club_id=club_id)
        sql='''SELECT l.*,cl.name club_name,o.status order_status,o.refund_status,o.delivered_at
               FROM commission_ledger l JOIN clubs cl ON cl.id=l.club_id JOIN gear_orders o ON o.id=l.order_id WHERE 1=1'''
        args=[]
        if club_id is not None: sql+=' AND l.club_id=?'; args.append(club_id)
        if status: sql+=' AND l.status=?'; args.append(status)
        if lo: sql+=" AND date(l.created_at,'+8 hours')>=?"; args.append(lo)
        if hi: sql+=" AND date(l.created_at,'+8 hours')<=?"; args.append(hi)
        sql+=' ORDER BY l.id DESC'
        return rows(c.execute(sql,args))

@app.get('/api/platform/settlements')
def platform_settlements(club_id:int|None=None):
    with conn() as c:
        out=commission_engine.settlements(c,club_id=club_id)
        # 回带打款账户摘要（脱敏）：结算记录要能核对「钱打到了哪张卡」。
        accts={int(a['club_id']):a for a in rows(c.execute("SELECT * FROM club_settlement_accounts WHERE status='active'"))}
        for s in out:
            a=accts.get(int(s.get('club_id') or 0))
            s['payee']=({'holder':a['holder'],'bankName':a['bank_name'],'cardNoMasked':_mask_card(a['card_no']),
                         'accountTypeCn':_SETTLEMENT_ACCOUNT_TYPES.get(a['account_type'],a['account_type'])} if a else None)
        return out

@app.get('/api/platform/settlements/preview')
def platform_settlement_preview(club_id:int):
    with conn() as c:
        if not row(c.execute('SELECT id FROM clubs WHERE id=?',(club_id,))): raise HTTPException(404,'俱乐部不存在')
        p=commission_engine.preview(c,club_id=club_id)
        acct=row(c.execute("SELECT * FROM club_settlement_accounts WHERE club_id=? AND status='active'",(club_id,)))
        p['account']=_settlement_account_public(acct) if acct else {'exists':False}
        return p

@app.post('/api/platform/settlements')
def platform_create_settlement(payload:dict=Body(...)):
    try: club_id=int(payload.get('clubId'))
    except Exception: raise HTTPException(400,'clubId required')
    with conn() as c:
        if not row(c.execute('SELECT id FROM clubs WHERE id=?',(club_id,))): raise HTTPException(404,'俱乐部不存在')
        # 2026-10-22 用户反馈：俱乐部结算佣金需要先提供法人卡或对公账户——没有账户一律不准结算。
        acct=row(c.execute("SELECT id,holder,bank_name,card_no,account_type FROM club_settlement_accounts WHERE club_id=? AND status='active'",(club_id,)))
        if not acct: raise HTTPException(409,'该俱乐部尚未提供结算账户（法人卡/对公账户）：请通知俱乐部在后台「商城 · 结算账户」补登后再结算')
        try:
            r=commission_engine.settle_available(c,club_id=club_id,payment_ref=str(payload.get('paymentRef') or ''),note=str(payload.get('note') or ''),created_by='platform')
        except ValueError as e: raise HTTPException(409,str(e))
        # 回带脱敏账户摘要，平台结算记录里能核对打到了哪张卡（只留后四位）。
        r['payee']={'accountTypeCn':_SETTLEMENT_ACCOUNT_TYPES.get(acct['account_type'],acct['account_type']),
                    'holder':acct['holder'],'bankName':acct['bank_name'],'cardNoMasked':_mask_card(acct['card_no'])}
        return r

@app.post('/api/platform/settlements/{settlement_id}/payout')
def platform_confirm_settlement_payout(settlement_id:str,payload:dict=Body(...)):
    """平台确认打款：俱乐部申请的结算单（requested）→ paid，预留佣金流水转已结算。

    平台侧的动作语义与 settle_available 不同：钱对应的流水在俱乐部申请时就已锁定，
    这里只补打款凭证——不复算、不重新圈流水，避免打款金额与申请金额对不上。
    """
    with conn() as c:
        try:return commission_engine.confirm_payout(c,settlement_id=settlement_id,
                                                    payment_ref=str(payload.get('paymentRef') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
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

@app.get('/api/platform/cost-bom')
def platform_cost_bom_get():
    with conn() as c:return finance_engine.get_bom(c)

@app.post('/api/platform/cost-bom')
def platform_cost_bom_save(payload:dict=Body(...)):
    lines=payload.get('lines') or []
    for ln in lines:
        ct=str(ln.get('calcType') or 'fixed')
        if ct not in ('fixed','per_item','percent_net','percent_cogs'):raise HTTPException(400,'不支持的计算方式: '+ct)
        cat=str(ln.get('category') or 'other')
        if cat not in ('logistics','packaging','other'):raise HTTPException(400,'不支持的成本类别: '+cat)
    with conn() as c:
        return finance_engine.save_bom(c,name=str(payload.get('name') or '默认成本清单'),note=str(payload.get('note') or ''),enabled=bool(payload.get('enabled',True)),lines=lines)

@app.get('/api/platform/cost-bom/preview')
def platform_cost_bom_preview(order_id:int|None=None):
    with conn() as c:
        if order_id is None:
            r=c.execute('SELECT id FROM gear_orders ORDER BY id LIMIT 1').fetchone()
            if not r:return {'orderId':None,'costSource':'none','shipping':0.0,'packaging':0.0,'otherCost':0.0,'contributionProfit':0.0,'bomLines':[]}
            order_id=int(r[0])
        try:p=finance_engine.order_profit(c,order_id)
        except LookupError:raise HTTPException(404,'装备订单不存在')
        return {'orderId':order_id,'costSource':p['costSource'],'shipping':p['shippingCost'],'packaging':p['packagingCost'],'otherCost':p['otherCost'],'contributionProfit':p['contributionProfit'],'bomLines':p['bomLines']}

@app.patch('/api/platform/orders/{order_id}/operating-costs')
def platform_order_operating_costs(order_id:int,payload:dict=Body(...)):
    shipping=max(0,float(payload.get('shippingCost') or 0));packaging=max(0,float(payload.get('packagingCost') or 0))
    with conn() as c:
        if not row(c.execute('SELECT id FROM gear_orders WHERE id=?',(order_id,))):raise HTTPException(404,'装备订单不存在')
        # 手动录入即视为覆盖 BOM（#641）：置 override=1 使其始终优先于自动核算
        c.execute('UPDATE gear_orders SET shipping_cost=?,packaging_cost=?,cost_bom_override=1 WHERE id=?',(shipping,packaging,order_id))
        return finance_engine.order_profit(c,order_id)

@app.post('/api/platform/orders/{order_id}/cost-override')
def platform_order_cost_override(order_id:int,payload:dict=Body(default={})):
    shipping=max(0,float(payload.get('shippingCost') or 0));packaging=max(0,float(payload.get('packagingCost') or 0))
    with conn() as c:
        if not row(c.execute('SELECT id FROM gear_orders WHERE id=?',(order_id,))):raise HTTPException(404,'装备订单不存在')
        c.execute('UPDATE gear_orders SET shipping_cost=?,packaging_cost=?,cost_bom_override=1 WHERE id=?',(shipping,packaging,order_id))
        return finance_engine.order_profit(c,order_id)

@app.post('/api/platform/orders/{order_id}/cost-reset')
def platform_order_cost_reset(order_id:int):
    with conn() as c:
        if not row(c.execute('SELECT id FROM gear_orders WHERE id=?',(order_id,))):raise HTTPException(404,'装备订单不存在')
        # 恢复 BOM 自动核算（#641）：清掉手动覆盖标记，order_profit 将按启用模板重算
        c.execute('UPDATE gear_orders SET cost_bom_override=0 WHERE id=?',(order_id,))
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

@app.delete('/api/platform/suppliers/{supplier_id}')
def platform_delete_supplier(supplier_id:int):
    with conn() as c:
        try:return inventory_engine.delete_supplier(c,supplier_id)
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

def _operator_label(request):
    """把当前登录身份解析成单据上可读的操作人标签。
    有真实用户（user_id）时显示「姓名（角色）」；平台/系统类身份显示中文角色名。
    这样采购单电子单据才能留存「是哪个用户采购 / 审核 / 确认 / 入库」。"""
    ROLE_CN={'platform_admin':'平台管理员','platform':'平台系统','club_admin':'俱乐部管理员',
             'club':'俱乐部','member':'会员','leader':'领队','system':'系统'}
    ident=getattr(request.state,'identity',None) or {}
    uid=ident.get('user_id')
    if uid:
        try:
            with conn() as c:
                u=row(c.execute('SELECT name, role FROM users WHERE id=?',(uid,)))
            if u and u.get('name'):
                return f"{u['name']}（{ROLE_CN.get(u.get('role') or '',u.get('role') or '用户')}）"
        except Exception:
            pass
    # 无 user_id 的平台 / 系统账号（如 demo 的 platform_admin）：退回中文角色名，避免落「platform」裸串。
    uname=ident.get('username')
    if uname: return ROLE_CN.get(uname,uname)
    role=ident.get('role') or 'platform'
    return ROLE_CN.get(role,role)

@app.post('/api/platform/procurement/purchase-orders')
def platform_create_purchase_order(request:Request,payload:dict=Body(...)):
    with conn() as c:
        try:return inventory_engine.create_purchase_order(c,supplier_id=int(payload.get('supplierId') or 0),items=payload.get('items') or [],expected_at=payload.get('expectedAt'),note=str(payload.get('note') or ''),created_by=_operator_label(request))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.get('/api/platform/procurement/purchase-orders/{po_id}')
def platform_purchase_order_detail(po_id:str):
    with conn() as c:
        try:return inventory_engine.get_purchase_order(c,po_id)
        except LookupError as e: raise HTTPException(404,str(e))

@app.post('/api/platform/procurement/purchase-orders/{po_id}/approve')
def platform_approve_purchase_order(request:Request,po_id:str):
    with conn() as c:
        try:return inventory_engine.approve_purchase_order(c,po_id=po_id,approved_by=_operator_label(request))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/platform/procurement/purchase-orders/{po_id}/order')
def platform_place_purchase_order(request:Request,po_id:str):
    with conn() as c:
        try:return inventory_engine.place_purchase_order(c,po_id=po_id,ordered_by=_operator_label(request))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/platform/procurement/purchase-orders/{po_id}/cancel')
def platform_cancel_purchase_order(po_id:str,payload:dict=Body(default={})):
    with conn() as c:
        try:return inventory_engine.cancel_purchase_order(c,po_id=po_id,note=str(payload.get('note') or ''))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

@app.post('/api/platform/procurement/purchase-orders/{po_id}/receive')
def platform_receive_purchase_order(request:Request,po_id:str,payload:dict=Body(...)):
    with conn() as c:
        try:out=inventory_engine.receive_purchase_order(c,po_id=po_id,items=payload.get('items') or [],received_by=_operator_label(request),note=str(payload.get('note') or ''),location_id=payload.get('locationId'),request_key=payload.get('requestKey'))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))
    product_ids=out.pop('productIds',[])
    out['commerceSync']=_safe_sync_inventory_products(product_ids)
    return out

@app.get('/api/platform/procurement/receipts')
def platform_purchase_receipts(po_id:str|None=None,supplier_id:int|None=None):
    with conn() as c:return inventory_engine.list_receipts(c,po_id=po_id,supplier_id=supplier_id)

@app.get('/api/platform/procurement/purchase-orders/{po_id}/exceptions')
def platform_po_exceptions(po_id:str):
    with conn() as c:
        try:return inventory_engine.list_po_exceptions(c,po_id=po_id)
        except LookupError as e: raise HTTPException(404,str(e))

@app.post('/api/platform/procurement/purchase-orders/{po_id}/exceptions')
def platform_register_po_exception(po_id:str,payload:dict=Body(...)):
    with conn() as c:
        try:return inventory_engine.register_po_exception(c,po_id=po_id,
            item_id=int(payload.get('purchaseOrderItemId') or 0),
            exception_type=str(payload.get('exceptionType') or ''),
            quantity=int(payload.get('quantity') or 0),
            note=str(payload.get('note') or ''),variant_id=payload.get('variantId'),resolution=str(payload.get('resolution') or 'closed'))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))

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
        try:out=inventory_engine.adjust_stock(c,product_id=pid,quantity_delta=delta,reason=str(payload.get('reason') or '平台库存调整'),actor_type='platform',variant_id=payload.get('variantId'))
        except LookupError as e: raise HTTPException(404,str(e))
        except ValueError as e: raise HTTPException(409,str(e))
    product_ids=out.pop('productIds',[])
    out['commerceSync']=_safe_sync_inventory_products(product_ids)
    return out


@app.get('/api/platform/stock/reconciliation')
def platform_stock_reconciliation():
    with conn() as c:
        return rows(c.execute("SELECT r.*,p.name,p.sku FROM stock_reconciliation r JOIN products p ON p.id=r.product_id WHERE r.status='pending'"))

@app.post('/api/platform/warehouse/reconcile/{product_id}')
def platform_reconcile_stock(product_id:int,payload:dict=Body(...)):
    with conn() as c:
        _owned_product(c,product_id)
        old=rows(c.execute('SELECT * FROM warehouse_inventory WHERE product_id=?',(product_id,)))
        if any(r['reserved'] for r in old):raise HTTPException(409,'历史已锁库存必须先逐单核对；本入口仅拆分未锁定历史余额，不更改订单事实')
        supplied=payload.get('balances') or [];totals={};seen=set()
        for r in supplied:
            vid=int(r.get('variantId') or 0);lid=int(r.get('locationId') or 0);q=int(r.get('onHand') or 0);u=int(r.get('unsellable') or 0)
            if (vid,lid) in seen or min(q,u)<0 or u>q:raise HTTPException(400,'SKU 库位重复或数量无效')
            if not c.execute('SELECT 1 FROM product_variants WHERE id=? AND product_id=?',(vid,product_id)).fetchone():raise HTTPException(400,'SKU 不属于该商品')
            if not any(x['location_id']==lid for x in old):raise HTTPException(400,'只能拆分原有库位余额')
            seen.add((vid,lid));a=totals.setdefault(lid,[0,0]);a[0]+=q;a[1]+=u
        if any(totals.get(r['location_id'],[0,0])!=[r['on_hand'],r['unsellable']] for r in old):raise HTTPException(409,'逐 SKU 合计必须与原实物/不可售余额相等；差异请先实盘核实')
        if not str(payload.get('note') or '').strip():raise HTTPException(400,'请填写实盘核对凭证')
        if c.execute('SELECT 1 FROM warehouse_sku_balances WHERE product_id=?',(product_id,)).fetchone():raise HTTPException(409,'该商品已经有 SKU 实物账，不能重复拆分')
        for r in supplied:c.execute('INSERT INTO warehouse_sku_balances(product_id,variant_id,location_id,on_hand,unsellable) VALUES(?,?,?,?,?)',(product_id,r['variantId'],r['locationId'],r['onHand'],r.get('unsellable',0)))
        c.execute("UPDATE stock_reconciliation SET status='resolved',note=?,resolved_at=CURRENT_TIMESTAMP WHERE product_id=?",(payload['note'],product_id))
        stock_control.sync(c,product_id)
    return {'ok':True}

@app.post('/api/platform/procurement/purchase-orders/{po_id}/supplier-shipment')
def platform_supplier_shipment(po_id:str,payload:dict=Body(...)):
    with conn() as c:
        po=inventory_engine.get_purchase_order(c,po_id)
        if po['status'] not in ('ordered','partially_received'):raise HTTPException(409,'只有已下单采购可登记供应商发货')
        key=str(payload.get('requestKey') or '').strip();tracking=str(payload.get('trackingNo') or '').strip()
        if not key or not tracking:raise HTTPException(400,'请提供本次发货凭证和请求键')
        old=c.execute('SELECT * FROM purchase_shipments WHERE request_key=?',(key,)).fetchone()
        if old:
            if old['purchase_order_id']!=po_id:raise HTTPException(409,'请求键属于另一采购单')
            return {'ok':True,'idempotent':True,'id':old['id']}
        plans={z['variantId']:z['quantity'] for it in po['items'] for z in it['skuItems']};sent={}
        for x in c.execute('SELECT items_json FROM purchase_shipments WHERE purchase_order_id=?',(po_id,)):
            for z in jload(x[0],[]):sent[z['variantId']]=sent.get(z['variantId'],0)+z['quantity']
        lineitems=payload.get('items') or [];seen=set()
        if not lineitems:raise HTTPException(400,'请选择本次发出的 SKU 和数量')
        for z in lineitems:
            vid=int(z.get('variantId') or 0);q=int(z.get('quantity') or 0)
            if vid in seen or vid not in plans or q<=0 or sent.get(vid,0)+q>plans[vid]:raise HTTPException(409,'供应商 SKU 发货数量超出采购计划或重复')
            seen.add(vid)
        c.execute('INSERT INTO purchase_shipments(purchase_order_id,request_key,carrier,tracking_no,items_json,shipped_at) VALUES(?,?,?,?,?,?)',(po_id,key,str(payload.get('carrier') or ''),tracking,jdump(lineitems),str(payload.get('shippedAt') or datetime.utcnow().isoformat())))
    return {'ok':True}


# ==========================================================================
# 营地模块 API（ClubOS 营地经营 / 营位房态 / 装备租赁 / 营地小站 / 活动票）
# 2026-11-10。全部 club 域：营位/租赁/小站/活动票都是俱乐部/营地自己运营的业态，
# 与平台商城(products / gear_orders)严格隔离。支付/结算复用统一订单层（club 商户号）。
# ==========================================================================

# ---- 经营业态开关（首页 / 活动 / 营地 / 业务介绍）—— 单一数据源 ----
_MODSW_DEFAULT={'home':True,'activity':True,'camp':False,'bizIntro':True}
def module_switches_public(x):
    """C 端读取：解析 clubs.module_switches_json；缺省按 _MODSW_DEFAULT（camp 默认关，老俱乐部不突变）。"""
    raw=x.get('module_switches_json')
    try: cfg=jload(raw) if raw else {}
    except Exception: cfg={}
    if not isinstance(cfg,dict): cfg={}
    return {k:bool(cfg.get(k,_MODSW_DEFAULT[k])) for k in _MODSW_DEFAULT}

@app.get('/api/club/{club_id}/module-switches')
def get_club_module_switches(club_id:int):
    return module_switches_public(club_or_404(club_id))

@app.patch('/api/club/{club_id}/module-switches')
def patch_club_module_switches(club_id:int,payload:dict=Body(...)):
    x=club_or_404(club_id)
    cur=module_switches_public(x)
    for k in _MODSW_DEFAULT:
        if k in payload: cur[k]=bool(payload[k])
    with conn() as c:
        c.execute('UPDATE clubs SET module_switches_json=? WHERE id=?',(jdump(cur),club_id))
    return cur

def _camp_profile_out(r):
    if not r: return r
    # 统一用 _camp_arr 解析 JSON 列：jdump(None) 落库是字符串 'null'，
    # json.loads('null') 得到 None —— 本地那个只判 str 的 _arr 会把 None 当数组用，
    # 新建营地（没传 gallery/facilities/rules）直接 500。
    raw_gal=_camp_arr(r.get('gallery_json'))
    gal=[x for x in (_camp_safe_url(u) for u in raw_gal) if x]
    cover=_camp_safe_url(r.get('cover_url')) or (gal[0] if gal else '')
    foc=_camp_focal_list(r.get('media_focal_json'),len(gal))
    ai=jload(r.get('ai_detail_json'), None)
    ai=ai if isinstance(ai,dict) else {}
    # #814 修正：slogan 只能是真正的短 tagline（来自 AI 短卖点），
    # 绝不能再拿长 intro_text 截断后塞进头图——长介绍统一走 camp-info 独立页。
    short=str(ai.get('subtitle') or '').strip().replace('\n',' ')[:40]
    return {'id':r['id'],'clubId':r['club_id'],'name':r['name'],'introText':r.get('intro_text'),
            'subtitle':str(ai.get('subtitle') or '')[:60],'slogan':short,
            'aiDetail':ai or None,'detailAvailable':bool(ai.get('sections')),
            'gallery':raw_gal,'coverUrl':cover,'focalPoints':foc,
            'coverFocal':_camp_cover_focal(cover,gal,foc),
            'facilities':_camp_arr(r.get('facilities_json')),'rules':_camp_arr(r.get('rules_json')),
            'contactPhone':r.get('contact_phone'),'address':r.get('address'),
            'geoLat':r.get('geo_lat'),'geoLng':r.get('geo_lng'),'status':r.get('status'),
            'createdAt':r.get('created_at')}

# ---- 营地档案（camp_profiles）----
@app.get('/api/club/{club_id}/camps')
def club_camp_list(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        return [_camp_profile_out(r) for r in rows(c.execute('SELECT * FROM camp_profiles WHERE club_id=? ORDER BY id',(club_id,)))]

@app.get('/api/public/clubs/{club_id}/camps')
def public_camp_list(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        return [_camp_profile_out(r) for r in rows(c.execute('SELECT * FROM camp_profiles WHERE club_id=? AND status="active" ORDER BY id',(club_id,)))]

@app.post('/api/club/{club_id}/camps')
def club_camp_create(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        c.execute('''INSERT INTO camp_profiles(club_id,name,intro_text,gallery_json,facilities_json,rules_json,contact_phone,address,geo_lat,geo_lng,status)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                  (club_id,payload.get('name'),payload.get('introText'),jdump(payload.get('gallery')),
                   jdump(payload.get('facilities')),jdump(payload.get('rules')),payload.get('contactPhone'),
                   payload.get('address'),payload.get('geoLat'),payload.get('geoLng'),payload.get('status','active')))
        nid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        return _camp_profile_out(row(c.execute('SELECT * FROM camp_profiles WHERE id=?',(nid,))))

@app.patch('/api/club/{club_id}/camps/{cid:int}')
def club_camp_update(club_id:int,cid:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        if not row(c.execute('SELECT id FROM camp_profiles WHERE id=? AND club_id=?',(cid,club_id))): raise HTTPException(404,'camp not found')
        flds={'name':'name','introText':'intro_text','gallery':'gallery_json','facilities':'facilities_json',
              'rules':'rules_json','contactPhone':'contact_phone','address':'address','geoLat':'geo_lat','geoLng':'geo_lng','status':'status'}
        sets=[];args=[]
        for pk,fk in flds.items():
            if pk in payload:
                v=payload[pk]; 
                if fk.endswith('_json'): v=jdump(v)
                sets.append(f'{fk}=?'); args.append(v)
        if sets:
            args+= [cid,club_id]
            c.execute(f"UPDATE camp_profiles SET {','.join(sets)} WHERE id=? AND club_id=?",args)
        return _camp_profile_out(row(c.execute('SELECT * FROM camp_profiles WHERE id=?',(cid,))))

# ---- 营位（camp_pitches）----
@app.get('/api/club/{club_id}/pitches')
def club_pitch_list(club_id:int,camp_id:int=0):
    club_or_404(club_id)
    with conn() as c:
        return rows(c.execute('SELECT * FROM camp_pitches WHERE club_id=? AND camp_id=? ORDER BY code',(club_id,camp_id)))

@app.get('/api/public/clubs/{club_id}/pitches')
def public_pitch_list(club_id:int,camp_id:int=0):
    """C 端只读在售营位（白名单列，绝不外泄内部成本/备注）。v3 追加详情增强字段 camelCase 别名。"""
    club_or_404(club_id)
    with conn() as c:
        return [_camp_goods_out(r,'pitch') for r in rows(c.execute('''SELECT id,camp_id,code,name,pitch_type,is_overnight,capacity,
                                        base_price,weekend_price,holiday_price,attributes_json,
                                        cover_url,gallery_json,specs_json,facilities_json,policies_json,
                                        subtitle,description,detail_text,original_price,unit_label,
                                        media_focal_json
                                 FROM camp_pitches
                                 WHERE club_id=? AND camp_id=? AND status="active" ORDER BY code''',(club_id,camp_id)))]

@app.post('/api/club/{club_id}/pitches')
def club_pitch_create(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        c.execute('''INSERT INTO camp_pitches(club_id,camp_id,code,name,pitch_type,is_overnight,capacity,attributes_json,base_price,weekend_price,holiday_price,status,
                     cover_url,gallery_json,specs_json,facilities_json,policies_json,subtitle,description,detail_text,original_price,unit_label,
                     promo_enabled,member_discount_enabled,points_enabled)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (club_id,payload.get('campId',0),payload.get('code'),payload.get('name'),payload.get('pitchType','tent'),
                   int(bool(payload.get('isOvernight',1))),payload.get('capacity',1),jdump(payload.get('attributes')),
                   payload.get('basePrice',0),payload.get('weekendPrice'),payload.get('holidayPrice'),payload.get('status','active'),
                   payload.get('coverUrl'),jdump(payload.get('gallery')),jdump(payload.get('specs')),
                   jdump(payload.get('facilities')),jdump(payload.get('policies')),payload.get('subtitle'),
                   payload.get('description'),payload.get('detailText'),payload.get('originalPrice',0),payload.get('unitLabel'),
                   # v4.1 #810 产品级权益开关（默认全开）
                   int(bool(payload.get('promoEnabled',1))),int(bool(payload.get('memberDiscountEnabled',1))),
                   int(bool(payload.get('pointsEnabled',1)))))
        nid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        return row(c.execute('SELECT * FROM camp_pitches WHERE id=?',(nid,)))

@app.patch('/api/club/{club_id}/pitches/{pid:int}')
def club_pitch_update(club_id:int,pid:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        if not row(c.execute('SELECT id FROM camp_pitches WHERE id=? AND club_id=?',(pid,club_id))): raise HTTPException(404,'pitch not found')
        flds={'campId':'camp_id','code':'code','name':'name','pitchType':'pitch_type','isOvernight':'is_overnight',
              'capacity':'capacity','attributes':'attributes_json','basePrice':'base_price','weekendPrice':'weekend_price',
              'holidayPrice':'holiday_price','status':'status',
              # v3 商品详情增强
              'coverUrl':'cover_url','gallery':'gallery_json','specs':'specs_json',
              'facilities':'facilities_json','policies':'policies_json','subtitle':'subtitle',
              'description':'description','detailText':'detail_text','originalPrice':'original_price','unitLabel':'unit_label',
              # v4.1 #810 产品级权益开关
              'promoEnabled':'promo_enabled','memberDiscountEnabled':'member_discount_enabled','pointsEnabled':'points_enabled'}
        sets=[];args=[]
        for pk,fk in flds.items():
            if pk in payload:
                v=payload[pk]
                if fk=='is_overnight': v=int(bool(v))
                elif fk.endswith('_json'): v=jdump(v)
                elif fk.endswith('_enabled'): v=int(bool(v))
                sets.append(f'{fk}=?'); args.append(v)
        if sets:
            args+=[pid,club_id]; c.execute(f"UPDATE camp_pitches SET {','.join(sets)} WHERE id=? AND club_id=?",args)
        return row(c.execute('SELECT * FROM camp_pitches WHERE id=?',(pid,)))

@app.delete('/api/club/{club_id}/pitches/{pid:int}')
def club_pitch_delete(club_id:int,pid:int):
    club_or_404(club_id)
    with conn() as c:
        c.execute('DELETE FROM camp_pitches WHERE id=? AND club_id=?',(pid,club_id))
    return {'ok':True}

# ---- 房态/可用（date-range）：营位 × 日期 —— 营位按日期管房态，过夜跨日锁 ----
def _camp_day_price(pitch, day_str, override):
    if override is not None: return override
    import datetime as _dt
    try: d=_dt.date.fromisoformat(day_str)
    except Exception: return pitch['base_price'] or 0
    if d.weekday()>=5:  # 周六/周日
        return pitch['weekend_price'] if pitch['weekend_price'] is not None else (pitch['base_price'] or 0)
    return pitch['base_price'] or 0

@app.get('/api/public/clubs/{club_id}/camp-availability')
def public_camp_availability(club_id:int,camp_id:int=0,start:str='',end:str='',days:int=30):
    club_or_404(club_id)
    import datetime as _dt
    if not start: start=_dt.date.today().isoformat()
    try: _s=_dt.date.fromisoformat(start)
    except Exception: raise HTTPException(400,'start 格式应为 YYYY-MM-DD')
    if not end: end=(_s+_dt.timedelta(days=max(days,1)-1)).isoformat()
    try: _e=_dt.date.fromisoformat(end)
    except Exception: raise HTTPException(400,'end 格式应为 YYYY-MM-DD')
    rng=[(_s+_dt.timedelta(days=i)).isoformat() for i in range((_e-_s).days+1)]
    with conn() as c:
        pitches=rows(c.execute('SELECT * FROM camp_pitches WHERE club_id=? AND camp_id=? AND status="active"',(club_id,camp_id)))
        # pending 订单只保留 30 分钟支付窗口，超时自动释放房态（酒店式防死锁）；
        # 与 public_camp_booking_create 的占用判断必须保持完全一致的口径。
        occ=rows(c.execute('''SELECT pitch_id,check_in,check_out,is_day_use FROM camp_pitch_bookings
                              WHERE club_id=? AND camp_id=? AND (status IN ("paid","checked_in")
                                    OR (status="pending" AND created_at>=datetime('now','-30 minutes')))''',(club_id,camp_id)))
        blk={}
        for r in rows(c.execute('SELECT pitch_id,day,status,price_override FROM camp_pitch_status WHERE club_id=? AND camp_id=?',(club_id,camp_id))):
            blk[(r['pitch_id'],r['day'])]=r
    out=[]
    for p in pitches:
        days_out=[]
        for d in rng:
            key=(p['id'],d)
            ov=blk[key]['price_override'] if key in blk else None
            if key in blk and blk[key]['status']=='blocked':
                days_out.append({'day':d,'status':'blocked','price':_camp_day_price(p,d,ov)}); continue
            booked=False
            for o in occ:
                if o['pitch_id']!=p['id']: continue
                if o['is_day_use']:
                    if o['check_in']==d: booked=True; break
                elif o['check_in']<=d<o['check_out']: booked=True; break
            days_out.append({'day':d,'status':'booked' if booked else 'available','price':_camp_day_price(p,d,ov)})
        out.append({'pitchId':p['id'],'code':p['code'],'name':p['name'],'pitchType':p['pitch_type'],
                    'isOvernight':p['is_overnight'],'capacity':p['capacity'],'days':days_out})
    return {'clubId':club_id,'campId':camp_id,'start':start,'end':end,'pitches':out}

# ---- 营位预订（酒店式跨日锁；防超售）----
@app.post('/api/public/clubs/{club_id}/camp-bookings')
def public_camp_booking_create(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    import datetime as _dt
    pid=int(payload.get('pitchId') or 0); check_in=str(payload.get('checkIn') or ''); check_out=str(payload.get('checkOut') or '')
    is_day=1 if payload.get('isDayUse') else 0
    qty=int(payload.get('qty') or 1)
    if not pid or not check_in: raise HTTPException(400,'缺少 pitchId / checkIn')
    if is_day: check_out=check_in
    try: _ci=_dt.date.fromisoformat(check_in); _co=_dt.date.fromisoformat(check_out)
    except Exception: raise HTTPException(400,'日期格式应为 YYYY-MM-DD')
    if (not is_day) and _co<=_ci: raise HTTPException(400,'checkOut 必须晚于 checkIn')
    nights=1 if is_day else (_co-_ci).days
    with conn() as c:
        p=row(c.execute('SELECT * FROM camp_pitches WHERE id=? AND club_id=?',(pid,club_id)))
        if not p: raise HTTPException(404,'营位不存在')
        if p['status']!='active': raise HTTPException(409,'该营位暂停预订')
        rng=[(_ci+_dt.timedelta(days=i)).isoformat() for i in range(nights)] if not is_day else [check_in]
        blk=rows(c.execute('SELECT day,status,price_override FROM camp_pitch_status WHERE club_id=? AND pitch_id=?',(club_id,pid)))
        blocked={b['day']:b['status'] for b in blk}
        ovmap={b['day']:b['price_override'] for b in blk if b['price_override'] is not None}
        occ=rows(c.execute('''SELECT check_in,check_out,is_day_use FROM camp_pitch_bookings
                              WHERE club_id=? AND pitch_id=? AND (status IN ("paid","checked_in")
                                    OR (status="pending" AND created_at>=datetime('now','-30 minutes')))''',(club_id,pid)))
        for d in rng:
            if blocked.get(d)=='blocked': raise HTTPException(409,f'{d} 该营位已封锁/维护')
            for o in occ:
                if o['is_day_use']:
                    if o['check_in']==d: raise HTTPException(409,f'{d} 该营位已被预订')
                elif o['check_in']<=d<o['check_out']: raise HTTPException(409,f'{d} 该营位已被预订')
        payable=round(sum(_camp_day_price(p,d,ovmap.get(d)) for d in rng)*qty,2)
        deposit=round(float(payload.get('deposit') or 0),2)
        total=round(payable+deposit,2)
        c.execute('''INSERT INTO camp_pitch_bookings(club_id,camp_id,pitch_id,check_in,check_out,is_day_use,guest_name,guest_phone,qty,nights,payable,deposit,total,status,user_id)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (club_id,p.get('camp_id',0),pid,check_in,check_out,is_day,payload.get('guestName'),payload.get('guestPhone'),
                   qty,nights,payable,deposit,total,'pending',payload.get('userId')))
        nid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        return row(c.execute('SELECT * FROM camp_pitch_bookings WHERE id=?',(nid,)))

@app.get('/api/club/{club_id}/camp-bookings')
def club_camp_booking_list(club_id:int,camp_id:int=0,status:str=''):
    club_or_404(club_id)
    with conn() as c:
        sql='SELECT * FROM camp_pitch_bookings WHERE club_id=? AND camp_id=?'; args=[club_id,camp_id]
        if status: sql+=' AND status=?'; args.append(status)
        return rows(c.execute(sql+' ORDER BY check_in DESC,id DESC',args))

# ---- 营地档案删除（级联清掉其营位/房态/预订，避免孤儿 camp_id）----
@app.delete('/api/club/{club_id}/camps/{cid:int}')
def club_camp_delete(club_id:int,cid:int):
    club_or_404(club_id)
    with conn() as c:
        if not row(c.execute('SELECT id FROM camp_profiles WHERE id=? AND club_id=?',(cid,club_id))): raise HTTPException(404,'camp not found')
        c.execute('DELETE FROM camp_pitch_status WHERE club_id=? AND camp_id=?',(club_id,cid))
        c.execute('DELETE FROM camp_pitch_bookings WHERE club_id=? AND camp_id=?',(club_id,cid))
        c.execute('DELETE FROM camp_pitches WHERE club_id=? AND camp_id=?',(club_id,cid))
        c.execute('DELETE FROM camp_profiles WHERE id=? AND club_id=?',(cid,club_id))
    return {'ok':True}

# ---- 营位房态手工覆盖（camp_pitch_status：封锁 / 解锁 / 价目覆盖）----
# 与活动名额(remaining 计数)不同，营位是「固定资源 + 日期房态网格」：后台可针对某个营位
# 某一天封锁（维护 / 满员）或临时改价；过夜跨日锁由 camp_pitch_bookings 实时推导。
@app.get('/api/club/{club_id}/pitch-status')
def club_pitch_status_list(club_id:int,camp_id:int=0,pitch_id:int=0):
    club_or_404(club_id)
    with conn() as c:
        sql='SELECT * FROM camp_pitch_status WHERE club_id=?'; args=[club_id]
        if camp_id: sql+=' AND camp_id=?'; args.append(camp_id)
        if pitch_id: sql+=' AND pitch_id=?'; args.append(pitch_id)
        return rows(c.execute(sql+' ORDER BY day,pitch_id',args))

@app.post('/api/club/{club_id}/pitch-status')
def club_pitch_status_upsert(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    import datetime as _dt
    cid=int(payload.get('campId',0)); pid=int(payload.get('pitchId',0)); day=str(payload.get('day') or '')
    status=str(payload.get('status') or 'available'); pov=payload.get('priceOverride',None)
    if not pid or not day: raise HTTPException(400,'缺少 pitchId / day')
    try: _dt.date.fromisoformat(day)
    except Exception: raise HTTPException(400,'day 格式应为 YYYY-MM-DD')
    with conn() as c:
        if status=='clear' or (status=='available' and pov in (None,'',0)):
            c.execute('DELETE FROM camp_pitch_status WHERE club_id=? AND camp_id=? AND pitch_id=? AND day=?',(club_id,cid,pid,day))
            return {'ok':True,'deleted':True}
        povv=None if pov in (None,'',0) else float(pov)
        existing=row(c.execute('SELECT id FROM camp_pitch_status WHERE club_id=? AND camp_id=? AND pitch_id=? AND day=?',(club_id,cid,pid,day)))
        if existing:
            c.execute('UPDATE camp_pitch_status SET status=?,price_override=? WHERE club_id=? AND camp_id=? AND pitch_id=? AND day=?',(status,povv,club_id,cid,pid,day))
        else:
            c.execute('INSERT INTO camp_pitch_status(club_id,camp_id,pitch_id,day,status,price_override) VALUES(?,?,?,?,?,?)',(club_id,cid,pid,day,status,povv))
        return row(c.execute('SELECT * FROM camp_pitch_status WHERE club_id=? AND camp_id=? AND pitch_id=? AND day=?',(club_id,cid,pid,day)))

# ---- 营地装备租赁（camp_rental_items，club 域）----
@app.get('/api/club/{club_id}/rental-items')
def club_rental_list(club_id:int,camp_id:int=0):
    club_or_404(club_id)
    with conn() as c:
        return rows(c.execute('SELECT * FROM camp_rental_items WHERE club_id=? AND camp_id=? ORDER BY id',(club_id,camp_id)))

@app.get('/api/public/clubs/{club_id}/rental-items')
def public_rental_list(club_id:int,camp_id:int=0):
    club_or_404(club_id)
    with conn() as c:
        return [_camp_goods_out(r,'rental') for r in rows(c.execute(
            'SELECT * FROM camp_rental_items WHERE club_id=? AND camp_id=? AND status="active" ORDER BY id',(club_id,camp_id)))]

@app.post('/api/club/{club_id}/rental-items')
def club_rental_create(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        c.execute('''INSERT INTO camp_rental_items(club_id,camp_id,name,cover_url,description,daily_price,deposit,available_qty,attributes_json,status,
                     gallery_json,specs_json,facilities_json,policies_json,subtitle,detail_text,original_price,unit_label,
                     pricing_json,promo_enabled,member_discount_enabled,points_enabled)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (club_id,payload.get('campId',0),payload.get('name'),payload.get('coverUrl'),payload.get('description'),
                   payload.get('dailyPrice',0),payload.get('deposit',0),payload.get('availableQty',0),jdump(payload.get('attributes')),payload.get('status','active'),
                   jdump(payload.get('gallery')),jdump(payload.get('specs')),jdump(payload.get('facilities')),
                   jdump(payload.get('policies')),payload.get('subtitle'),payload.get('detailText'),
                   payload.get('originalPrice',0),payload.get('unitLabel'),
                   # #822 自定义计费项
                   jdump(payload.get('pricing')),
                   # v4.1 #810 产品级权益开关（默认全开）
                   int(bool(payload.get('promoEnabled',1))),int(bool(payload.get('memberDiscountEnabled',1))),
                   int(bool(payload.get('pointsEnabled',1)))))
        nid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        return row(c.execute('SELECT * FROM camp_rental_items WHERE id=?',(nid,)))

@app.patch('/api/club/{club_id}/rental-items/{rid:int}')
def club_rental_update(club_id:int,rid:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        if not row(c.execute('SELECT id FROM camp_rental_items WHERE id=? AND club_id=?',(rid,club_id))): raise HTTPException(404,'rental not found')
        flds={'campId':'camp_id','name':'name','coverUrl':'cover_url','description':'description','dailyPrice':'daily_price',
              'deposit':'deposit','availableQty':'available_qty','attributes':'attributes_json','status':'status',
              # #822 自定义计费项（项目 + 价格 + 时间单位）
              'pricing':'pricing_json',
              # v3 商品详情增强
              'gallery':'gallery_json','specs':'specs_json','facilities':'facilities_json',
              'policies':'policies_json','subtitle':'subtitle','detailText':'detail_text',
              'originalPrice':'original_price','unitLabel':'unit_label',
              # v4.1 #810 产品级权益开关
              'promoEnabled':'promo_enabled','memberDiscountEnabled':'member_discount_enabled','pointsEnabled':'points_enabled'}
        sets=[];args=[]
        for pk,fk in flds.items():
            if pk in payload:
                v=payload[pk]
                if fk.endswith('_json'): v=jdump(v)
                elif fk.endswith('_enabled'): v=int(bool(v))
                sets.append(f'{fk}=?'); args.append(v)
        if sets:
            args+=[rid,club_id]; c.execute(f"UPDATE camp_rental_items SET {','.join(sets)} WHERE id=? AND club_id=?",args)
        return row(c.execute('SELECT * FROM camp_rental_items WHERE id=?',(rid,)))

@app.delete('/api/club/{club_id}/rental-items/{rid:int}')
def club_rental_delete(club_id:int,rid:int):
    club_or_404(club_id)
    with conn() as c:
        c.execute('DELETE FROM camp_rental_items WHERE id=? AND club_id=?',(rid,club_id))
    return {'ok':True}

# ---- 营地小站（camp_store_products，club 自营零售）----
@app.get('/api/club/{club_id}/store-products')
def club_store_list(club_id:int,camp_id:int=0):
    club_or_404(club_id)
    with conn() as c:
        return rows(c.execute('SELECT * FROM camp_store_products WHERE club_id=? AND camp_id=? ORDER BY id',(club_id,camp_id)))

@app.get('/api/public/clubs/{club_id}/store-products')
def public_store_list(club_id:int,camp_id:int=0):
    club_or_404(club_id)
    with conn() as c:
        return [_camp_goods_out(r,'store') for r in rows(c.execute(
            'SELECT * FROM camp_store_products WHERE club_id=? AND camp_id=? AND status="active" ORDER BY id',(club_id,camp_id)))]

@app.post('/api/club/{club_id}/store-products')
def club_store_create(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        c.execute('''INSERT INTO camp_store_products(club_id,camp_id,name,cover_url,price,stock,category,status,
                     gallery_json,specs_json,facilities_json,policies_json,subtitle,description,detail_text,original_price,unit_label,
                     promo_enabled,member_discount_enabled,points_enabled)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (club_id,payload.get('campId',0),payload.get('name'),payload.get('coverUrl'),payload.get('price',0),
                   payload.get('stock',0),payload.get('category'),payload.get('status','active'),
                   jdump(payload.get('gallery')),jdump(payload.get('specs')),jdump(payload.get('facilities')),
                   jdump(payload.get('policies')),payload.get('subtitle'),payload.get('description'),
                   payload.get('detailText'),payload.get('originalPrice',0),payload.get('unitLabel'),
                   # v4.1 #810 产品级权益开关（默认全开）
                   int(bool(payload.get('promoEnabled',1))),int(bool(payload.get('memberDiscountEnabled',1))),
                   int(bool(payload.get('pointsEnabled',1)))))
        nid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        return row(c.execute('SELECT * FROM camp_store_products WHERE id=?',(nid,)))

@app.patch('/api/club/{club_id}/store-products/{sid:int}')
def club_store_update(club_id:int,sid:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        if not row(c.execute('SELECT id FROM camp_store_products WHERE id=? AND club_id=?',(sid,club_id))): raise HTTPException(404,'product not found')
        flds={'campId':'camp_id','name':'name','coverUrl':'cover_url','price':'price','stock':'stock','category':'category','status':'status',
              # v3 商品详情增强
              'gallery':'gallery_json','specs':'specs_json','facilities':'facilities_json',
              'policies':'policies_json','subtitle':'subtitle','description':'description',
              'detailText':'detail_text','originalPrice':'original_price','unitLabel':'unit_label',
              # v4.1 #810 产品级权益开关
              'promoEnabled':'promo_enabled','memberDiscountEnabled':'member_discount_enabled','pointsEnabled':'points_enabled'}
        sets=[];args=[]
        for pk,fk in flds.items():
            if pk in payload:
                v=payload[pk]
                if fk.endswith('_json'): v=jdump(v)
                elif fk.endswith('_enabled'): v=int(bool(v))
                sets.append(f'{fk}=?'); args.append(v)
        if sets:
            args+=[sid,club_id]; c.execute(f"UPDATE camp_store_products SET {','.join(sets)} WHERE id=? AND club_id=?",args)
        return row(c.execute('SELECT * FROM camp_store_products WHERE id=?',(sid,)))

@app.delete('/api/club/{club_id}/store-products/{sid:int}')
def club_store_delete(club_id:int,sid:int):
    club_or_404(club_id)
    with conn() as c:
        c.execute('DELETE FROM camp_store_products WHERE id=? AND club_id=?',(sid,club_id))
    return {'ok':True}

# ---- 营地分区与图标（#826：营地老板自己定义 C 端那排小图标）----------------
# 以前 C 端营地主页顶部的分区入口是前端写死的：全部营位 / 帐篷区 / 房车区 /
# 木屋区 / 日间营 / 装备租赁 / 营地小站 / 联系客服。老板改不了 —— 他的营地明明是
# 「湖边露营 + 篝火区 + 淋浴区」，前端还是给他帐篷和房车。这里把它变成后台可配置项：
#   key     分区标识（后端生成，客户端只读）
#   name    分区显示名（老板自己写）
#   target  分区动作：all / 营位类型(tent,rv,cabin,day) / rental / store / service
#   icon    内置图标库 key（'' 表示用 emoji）
#   emoji   自定义 emoji（老板最省事的自定义方式，不用上传图片）
#   visible 是否在 C 端显示
# 存 clubs.camp_zones_json（俱乐部级，一份配置对旗下全部营地生效）。
CAMP_ZONE_ICONS=['all','tent','rv','cabin','day','rental','store','service','tree','water',
                  'fire','wifi','parking','coffee','toilet','shop','mountain','sun','moon','bed','star','map']
_CAMP_PITCH_TYPE_CN={'tent':'帐篷位','rv':'房车位','cabin':'木屋/舱房','day':'日间营位'}
_CAMP_PITCH_TYPE_ZONE={'tent':('tent','帐篷区'),'rv':('rv','房车区'),'cabin':('cabin','木屋区'),'day':('day','日间营')}

def _camp_zone_defaults(c,club_id):
    """默认分区 = 全部营位 + 实际存在的营位类型 + 装备租赁 / 营地小站 / 联系客服。"""
    types=[]
    for r in rows(c.execute('SELECT DISTINCT pitch_type FROM camp_pitches WHERE club_id=? AND status="active"',(club_id,))):
        t=str(r.get('pitch_type') or 'tent')
        if t not in types: types.append(t)
    zs=[{'key':'all','name':'全部营位','target':'all','icon':'all','emoji':'','visible':True}]
    for t in types:
        icon,label=_CAMP_PITCH_TYPE_ZONE.get(t,(t,_CAMP_PITCH_TYPE_CN.get(t,t)))
        zs.append({'key':'t_'+t,'name':label,'target':t,'icon':icon,'emoji':'','visible':True})
    zs+=[{'key':'rental','name':'装备租赁','target':'rental','icon':'rental','emoji':'','visible':True},
         {'key':'store','name':'营地小站','target':'store','icon':'store','emoji':'','visible':True},
         {'key':'service','name':'联系客服','target':'service','icon':'service','emoji':'','visible':True}]
    for i,z in enumerate(zs): z['order']=i
    return zs

def _camp_zones_read(c,club_id):
    """读配置 + 只补「新增的营位类型」。
    已配置的分区完全按老板的来（含 visible=false / 已删除）；只把当前存在、
    但配置里没有对应 target 的新营位类型补进列表，否则 C 端会出现
    「湖边帐篷」+「帐篷区」这类重复入口，并把后面的入口挤出 slice(0,8)。"""
    raw=row(c.execute('SELECT camp_zones_json FROM clubs WHERE id=?',(club_id,)))
    saved=[]
    if raw and raw.get('camp_zones_json'):
        try:
            v=json.loads(raw['camp_zones_json'])
            if isinstance(v,list): saved=v
        except Exception: saved=[]
    defs=_camp_zone_defaults(c,club_id)
    dm={z['key']:z for z in defs}
    out=[];seen=set()
    for i,z in enumerate(saved):
        if not isinstance(z,dict): continue
        k=str(z.get('key') or '').strip()[:32]
        if not k or k in seen: continue
        seen.add(k)
        d=dm.get(k,{})
        tgt=str(z.get('target') or d.get('target') or '').strip()[:20]
        out.append({'key':k,
                    'name':str(z.get('name') or d.get('name') or k)[:20],
                    'target':tgt,
                    'icon':str(z.get('icon') if z.get('icon') is not None else (d.get('icon') or ''))[:24],
                    'emoji':str(z.get('emoji') or '')[:8],
                    'visible':bool(z.get('visible',True)),
                    'order':i})
    # 只自动补「老板还没覆盖的新营位类型」。固定入口（all/rental/store/service）
    # 与老板删掉/隐藏的项都不该被重新塞回来；老板常用自定义 key 配同一个营位
    # 类型，所以按 target 去重，而不是只比 key。
    saved_targets={str(x.get('target') or '').strip() for x in out}
    for z in defs:
        if not z['key'].startswith('t_'): continue
        if z['target'] in saved_targets or z['key'] in seen: continue
        z=dict(z); z['order']=len(out); out.append(z); seen.add(z['key'])
        saved_targets.add(z['target'])
    out.sort(key=lambda x:x.get('order',0))
    return out

@app.get('/api/club/{club_id}/camp-zones')
def club_camp_zones(club_id:int):
    club_or_404(club_id)
    with conn() as c:
        return {'ok':True,'zones':_camp_zones_read(c,club_id),'iconKeys':CAMP_ZONE_ICONS}

@app.patch('/api/club/{club_id}/camp-zones')
def club_camp_zones_patch(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    zs=payload.get('zones')
    if not isinstance(zs,list): raise HTTPException(400,'zones 必须是数组')
    if len(zs)>20: raise HTTPException(400,'分区最多 20 个')
    out=[];seen=set()
    for i,z in enumerate(zs):
        if not isinstance(z,dict): continue
        k=str(z.get('key') or '').strip()[:32]
        if not k or k in seen: continue
        seen.add(k)
        out.append({'key':k,
                    'name':str(z.get('name') or '').strip()[:20] or k,
                    'target':str(z.get('target') or '').strip()[:20],
                    'icon':str(z.get('icon') or '').strip()[:24],
                    'emoji':str(z.get('emoji') or '').strip()[:8],
                    'visible':bool(z.get('visible',True)),
                    'order':i})
    with conn() as c:
        c.execute('UPDATE clubs SET camp_zones_json=? WHERE id=?',(jdump(out),club_id))
    return {'ok':True,'zones':out}

@app.get('/api/public/clubs/{club_id}/camp-zones')
def public_camp_zones(club_id:int):
    """C 端只读分区配置（白名单字段，不下发任何内部数据）。"""
    club_or_404(club_id)
    with conn() as c:
        return {'ok':True,'zones':_camp_zones_read(c,club_id)}

# ---- 营地活动票（camp_activity_tickets，电子票+核销）----
@app.get('/api/club/{club_id}/tickets')
def club_ticket_list(club_id:int,camp_id:int=0):
    club_or_404(club_id)
    with conn() as c:
        return rows(c.execute('SELECT * FROM camp_activity_tickets WHERE club_id=? AND camp_id=? ORDER BY id',(club_id,camp_id)))

@app.get('/api/public/clubs/{club_id}/tickets')
def public_ticket_list(club_id:int,camp_id:int=0):
    club_or_404(club_id)
    with conn() as c:
        return rows(c.execute('SELECT * FROM camp_activity_tickets WHERE club_id=? AND camp_id=? AND status="open" ORDER BY id',(club_id,camp_id)))

@app.post('/api/club/{club_id}/tickets')
def club_ticket_create(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        c.execute('''INSERT INTO camp_activity_tickets(club_id,camp_id,title,event_date,price,capacity,sold,entry_type,valid_from,valid_to,status)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                  (club_id,payload.get('campId',0),payload.get('title'),payload.get('eventDate'),payload.get('price',0),
                   payload.get('capacity',0),0,payload.get('entryType','single'),payload.get('validFrom'),payload.get('validTo'),payload.get('status','open')))
        nid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        return row(c.execute('SELECT * FROM camp_activity_tickets WHERE id=?',(nid,)))

@app.patch('/api/club/{club_id}/tickets/{tid:int}')
def club_ticket_update(club_id:int,tid:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        if not row(c.execute('SELECT id FROM camp_activity_tickets WHERE id=? AND club_id=?',(tid,club_id))): raise HTTPException(404,'ticket not found')
        flds={'campId':'camp_id','title':'title','eventDate':'event_date','price':'price','capacity':'capacity','entryType':'entry_type','validFrom':'valid_from','validTo':'valid_to','status':'status'}
        sets=[];args=[]
        for pk,fk in flds.items():
            if pk in payload:
                sets.append(f'{fk}=?'); args.append(payload[pk])
        if sets:
            args+=[tid,club_id]; c.execute(f"UPDATE camp_activity_tickets SET {','.join(sets)} WHERE id=? AND club_id=?",args)
        return row(c.execute('SELECT * FROM camp_activity_tickets WHERE id=?',(tid,)))

@app.delete('/api/club/{club_id}/tickets/{tid:int}')
def club_ticket_delete(club_id:int,tid:int):
    club_or_404(club_id)
    with conn() as c:
        c.execute('DELETE FROM camp_activity_tickets WHERE id=? AND club_id=?',(tid,club_id))
    return {'ok':True}

# ---- 营地结算骨架（复用统一结算层 checkout_intents，kind='camp_booking'）----
# 设计：不修改 CheckoutEngine.confirm —— 那是活动报名 / 装备商城共用的支付核心，
# 改动回归面过大。camp_booking 走独立确认端点，但用同一张 checkout_intents 存结算单，
# 便于后续接支付渠道时沿用 /api/public/checkouts/{id} 的查询与对账视图。
# 生产环境禁止前端直调 confirm，必须由已验证的支付渠道回调触发；
# 本地 / 测试环境可直调做联调。真实微信支付接入后只需把触发点换成
# /api/payments/{provider}/{account}/notify 的已验证回调，业务状态机无需改动。
@app.post('/api/public/clubs/{club_id}/camp-checkout')
def create_camp_checkout(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    bid=int(payload.get('bookingId') or 0)
    if not bid: raise HTTPException(400,'缺少 bookingId')
    with conn() as c:
        # 只允许 30 分钟支付窗口内的 pending 预订创建结算单（与房态占用口径一致）
        b=row(c.execute('''SELECT * FROM camp_pitch_bookings WHERE id=? AND club_id=?
                           AND status="pending" AND created_at>=datetime('now','-30 minutes')''',(bid,club_id)))
        if not b: raise HTTPException(409,'预订不存在、已支付或已超时释放，请重新预订')
        if b.get('order_id'):
            old=row(c.execute('SELECT * FROM checkout_intents WHERE id=?',(b['order_id'],)))
            if old and old['status']=='pending_payment':
                return {'ok':True,'checkoutId':old['id'],'status':'pending_payment',
                        'amount':old['cash_amount'],'bookingId':bid,'idempotent':True,'nextAction':'PAY'}
        iid='camp_'+uuid.uuid4().hex[:16]
        total=round(float(b['total'] or 0),2)
        c.execute('''INSERT INTO checkout_intents(id,kind,user_id,club_id,status,original_amount,
                       cash_amount,payload_json,payment_status)
                     VALUES(?,?,?,?,?,?,?,?,?)''',
                  (iid,'camp_booking',b['user_id'] or 1,club_id,'pending_payment',total,total,
                   jdump({'bookingId':bid,'campId':b['camp_id'],'pitchId':b['pitch_id'],
                          'checkIn':b['check_in'],'checkOut':b['check_out'],
                          'guestName':b['guest_name'],'total':total}),'pending'))
        c.execute('UPDATE camp_pitch_bookings SET order_id=? WHERE id=? AND club_id=?',(iid,bid,club_id))
        return {'ok':True,'checkoutId':iid,'status':'pending_payment','amount':total,
                'bookingId':bid,'nextAction':'PAY'}

@app.post('/api/public/camp-checkouts/{checkout_id}/confirm')
def confirm_camp_checkout(checkout_id:str,payload:dict=Body(default={})):
    if IS_PROD and not payload.get('providerVerified'):
        raise HTTPException(403,'生产环境须由已验证的支付渠道回调确认')
    with conn() as c:
        it=row(c.execute('SELECT * FROM checkout_intents WHERE id=?',(checkout_id,)))
        if not it or it['kind']!='camp_booking': raise HTTPException(404,'营地结算单不存在')
        if it['status']=='paid':
            return {'ok':True,'checkoutId':checkout_id,'status':'paid','amount':it['cash_amount'],'idempotent':True}
        if it['status']!='pending_payment': raise HTTPException(409,'结算单已取消或已完成')
        meta=jload(it.get('payload_json'),{}); bid=int(meta.get('bookingId') or 0)
        b=row(c.execute('SELECT * FROM camp_pitch_bookings WHERE id=? AND club_id=?',(bid,it['club_id'])))
        if not b: raise HTTPException(409,'对应营地预订不存在')
        if b['status'] not in ('pending','paid'): raise HTTPException(409,'预订已取消，无法确认支付')
        if b['status']=='pending' and row(c.execute('''SELECT 1 FROM camp_pitch_bookings
                       WHERE id=? AND status="pending" AND created_at<datetime('now','-30 minutes')''',(bid,))):
            raise HTTPException(409,'支付超时，房态已释放，请重新预订')
        c.execute('''UPDATE checkout_intents SET status="paid",payment_status="succeeded",
                     paid_at=CURRENT_TIMESTAMP,result_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                  (str(bid),checkout_id))
        c.execute('''UPDATE camp_pitch_bookings SET status="paid"
                     WHERE id=? AND club_id=? AND status="pending"''',(bid,it['club_id']))
        return {'ok':True,'checkoutId':checkout_id,'bookingId':bid,'status':'paid','amount':it['cash_amount']}

@app.post('/api/public/camp-checkouts/{checkout_id}/cancel')
def cancel_camp_checkout(checkout_id:str):
    with conn() as c:
        it=row(c.execute('SELECT * FROM checkout_intents WHERE id=?',(checkout_id,)))
        if not it or it['kind']!='camp_booking': raise HTTPException(404,'营地结算单不存在')
        if it['status']=='cancelled':
            return {'ok':True,'checkoutId':checkout_id,'status':'cancelled','idempotent':True}
        if it['status']=='paid': raise HTTPException(409,'已支付订单请走售后流程')
        meta=jload(it.get('payload_json'),{}); bid=int(meta.get('bookingId') or 0)
        c.execute('''UPDATE checkout_intents SET status="cancelled",cancelled_at=CURRENT_TIMESTAMP,
                     updated_at=CURRENT_TIMESTAMP WHERE id=?''',(checkout_id,))
        c.execute('''UPDATE camp_pitch_bookings SET status="cancelled"
                     WHERE id=? AND club_id=? AND status="pending"''',(bid,it['club_id']))
        return {'ok':True,'checkoutId':checkout_id,'bookingId':bid,'status':'cancelled'}


# ==========================================================================
# 营地模块 v3（2026-11-11）：统一商品订单 + 会员权益打通 + 商品详情增强
# --------------------------------------------------------------------------
# 用户 6 点需求中的第 6 点（最重要）：营地里的「房间/营位、租用装备、小站商品」
# 必须与活动共用同一套会员体系。实现方式是新增 camp_pricing 编排层，把金额送进
# 既有 booking_engine / checkout_engine / benefit_engine / points_engine，
# 不新建规则、不修改 CheckoutEngine.confirm()，活动业务零改动。
#
# 另外：活动票（camp_activity_tickets）表与接口保留兼容旧数据，但前端一律不再展示，
# 营地活动复用原「活动」模块。
# ==========================================================================

def _camp_safe_url(u):
    s=str(u or '').strip()
    # 只放行 https 与本站静态/上传路径，避免后台误填 javascript: 之类注入 C 端。
    if s.startswith('https://') or s.startswith('/static/') or s.startswith('/uploads/') or s.startswith('data:image/'):
        return s
    # 2026-11-11 营地 v4：本地上传的素材走公开代理（生产 /static/uploads/* 被网关挡），
    # 只放行 camp-media-file 这一种形状，避免把 /api/public 变成任意路径白名单。
    if s.startswith('/api/public/clubs/') and ('/camp-media-file/' in s or '/camp-src-file/' in s):
        return s
    return ''

def _camp_arr(v):
    if isinstance(v,list): return v
    if isinstance(v,str):
        try:
            x=json.loads(v)
            return x if isinstance(x,list) else []
        except Exception: return []
    return []

# #822 装备租赁「租金」自定义：老板可以自己定义计费项目、价格与时间单位
# （按小时 / 按半天 / 按天 / 按晚 / 按次 / 按件），一个装备可以配多种计费方式。
_CAMP_RENTAL_UNITS={'hour':'小时','halfday':'半天','day':'天','night':'晚','times':'次','item':'件'}

def _camp_rental_plans(r):
    """租赁计费项列表；老数据（没配 pricing_json）回落到原来的「日租金」，行为不变。"""
    out=[]
    for p in _camp_arr(r.get('pricing_json')):
        if not isinstance(p,dict): continue
        nm=str(p.get('name') or '').strip()[:20]
        try: pr=round(float(p.get('price') or 0),2)
        except (TypeError,ValueError): pr=0.0
        u=str(p.get('unit') or 'day').strip()[:12]
        if u not in _CAMP_RENTAL_UNITS: u='day'
        if not nm and pr<=0: continue
        out.append({'name':nm or '租金','price':pr,'unit':u,'unitLabel':_CAMP_RENTAL_UNITS[u]})
    if not out:
        pr=round(float(r.get('daily_price') or 0),2)
        out=[{'name':'租金','price':pr,'unit':'day','unitLabel':'天'}]
    return out

def _camp_goods_out(r,kind=None):
    """列表/详情统一序列化：保留全部旧 snake_case 键（旧前端不破），追加契约 §5 的 camelCase 别名。"""
    d=dict(r)
    gal=[_camp_safe_url(x) for x in _camp_arr(r.get('gallery_json'))]
    gal=[x for x in gal if x]
    cover=_camp_safe_url(r.get('cover_url')) or (gal[0] if gal else '')
    spec=_camp_arr(r.get('specs_json')); fac=_camp_arr(r.get('facilities_json')); pol=_camp_arr(r.get('policies_json'))
    # #813：与 gallery 同序的焦点数组 + 封面焦点，C 端据此设置 object-position
    foc=_camp_focal_list(r.get('media_focal_json'),len(gal))
    d.update({
        'coverUrl':cover,'gallery':gal,
        'focalPoints':foc,'coverFocal':_camp_cover_focal(cover,gal,foc),
        'specs':spec,'facilities':fac,'policies':pol,
        'subtitle':r.get('subtitle') or '',
        'description':r.get('description') or '',
        'detailText':r.get('detail_text') or '',
        'originalPrice':round(float(r.get('original_price') or 0),2),
        'unitLabel':r.get('unit_label') or '',
    })
    d.pop('pricing_json',None)
    # #822：租赁商品下发计费项（项目 + 价格 + 单位），C 端据此让顾客选「按小时 / 按天 …」
    if kind=='rental':
        d['pricing']=_camp_rental_plans(r)
    return d

def _camp_public_quote(q):
    """剥掉内部计算键（_ 开头）后交给 C 端；内部键只存在服务端快照与账本入参里。"""
    out={k:v for k,v in q.items() if not k.startswith('_')}
    out['items']=[{k:v for k,v in dict(x).items() if not k.startswith('_')} for x in (q.get('items') or [])]
    return out

def _camp_flag(v):
    """v4.1 #810：产品级权益开关解析。NULL/None/缺列=开（兼容旧行），0=关，其余非零=开。"""
    if v is None: return 1
    try: return 1 if int(v)!=0 else 0
    except (TypeError,ValueError): return 1

def _camp_line(c,club_id,raw):
    """服务端重算单行价格 —— 绝不信任前端传来的任何金额。返回规范行（带 _ 内部键）。"""
    import datetime as _dt
    kind=str(raw.get('kind') or '')
    if kind not in ('pitch','rental','store'): raise HTTPException(400,'商品类型 kind 必须是 pitch / rental / store')
    rid=int(raw.get('id') or 0)
    qty=max(1,min(20,int(raw.get('qty') or 1)))
    if not rid: raise HTTPException(400,'缺少商品 id')
    ci=str(raw.get('checkIn') or ''); co=str(raw.get('checkOut') or '')
    if ci:
        try: _ci=_dt.date.fromisoformat(ci)
        except Exception: raise HTTPException(400,'checkIn 格式应为 YYYY-MM-DD')
        if _ci<_dt.date.today(): raise HTTPException(400,'入住日期不能早于今天')
    if co and kind!='store':
        try: _dt.date.fromisoformat(co)
        except Exception: raise HTTPException(400,'checkOut 格式应为 YYYY-MM-DD')

    if kind=='pitch':
        p=row(c.execute('SELECT * FROM camp_pitches WHERE id=? AND club_id=? AND status="active"',(rid,club_id)))
        if not p: raise HTTPException(404,'营位不存在或已下架')
        if not ci: raise HTTPException(400,'预订营位需要 checkIn')
        is_day=1 if (raw.get('isDayUse') or ci==co or not p['is_overnight']) else 0
        _ci=_dt.date.fromisoformat(ci)
        if is_day:
            rng=[ci]; co=ci; nights=1
        else:
            if not co: raise HTTPException(400,'过夜营位需要 checkOut')
            _co=_dt.date.fromisoformat(co)
            if _co<=_ci: raise HTTPException(400,'离营日期必须晚于入住日期')
            if (_co-_ci).days>60: raise HTTPException(400,'单次预订最多 60 晚')
            nights=(_co-_ci).days
            rng=[(_ci+_dt.timedelta(days=i)).isoformat() for i in range(nights)]
        # 房态占用：与 public_camp_booking_create / camp-availability 完全同口径（pending 仅 30 分钟）。
        blk={b['day']:b for b in rows(c.execute('SELECT day,status,price_override FROM camp_pitch_status WHERE club_id=? AND pitch_id=?',(club_id,rid)))}
        ovmap={d:b['price_override'] for d,b in blk.items() if b['price_override'] is not None}
        occ=rows(c.execute('''SELECT check_in,check_out,is_day_use FROM camp_pitch_bookings
                              WHERE club_id=? AND pitch_id=? AND (status IN ("paid","checked_in")
                                    OR (status="pending" AND created_at>=datetime('now','-30 minutes')))''',(club_id,rid)))
        for d in rng:
            if blk.get(d,{}).get('status')=='blocked': raise HTTPException(409,f'{d} 该营位已封锁/维护')
            for o in occ:
                if o['is_day_use']:
                    if o['check_in']==d: raise HTTPException(409,f'{d} 该营位已被预订')
                elif o['check_in']<=d<o['check_out']: raise HTTPException(409,f'{d} 该营位已被预订')
        unit=round(sum(_camp_day_price(p,d,ovmap.get(d)) for d in rng),2)
        subtotal=round(unit*qty,2)
        # 押金由前端带（离营退还），钳制上限防止脏数据；不参与折扣与积分抵扣。
        deposit=round(max(0.0,min(100000.0,float(raw.get('deposit') or 0))),2)
        spec=f"{({'tent':'帐篷位','rv':'房车位','cabin':'木屋/舱房','day':'日间营位'}).get(p['pitch_type'],p['pitch_type'])} · 适用 {p['capacity'] or 1} 人"
        return {'kind':'pitch','id':rid,'title':p['name'] or '营位','coverUrl':_camp_safe_url(p.get('cover_url')),
                'spec':spec,'unitLabel':'/晚' if p['is_overnight'] else '/天','unitPrice':unit,'qty':qty,
                'days':nights,'subtotal':subtotal,'deposit':deposit,'checkIn':ci,'checkOut':co,
                'isDayUse':is_day,'campId':p['camp_id'],'_rng':rng,
                # v4.1 #810 产品级权益开关（NULL/缺列兜底=开）
                'promoEnabled':_camp_flag(p['promo_enabled']),'memberEnabled':_camp_flag(p['member_discount_enabled']),
                'pointsEnabled':_camp_flag(p['points_enabled'])}

    if kind=='rental':
        r=row(c.execute('SELECT * FROM camp_rental_items WHERE id=? AND club_id=? AND status="active"',(rid,club_id)))
        if not r: raise HTTPException(404,'租赁装备不存在或已下架')
        if ci:
            _ci=_dt.date.fromisoformat(ci)
            if co:
                _co=_dt.date.fromisoformat(co)
            else:
                _co=_ci+_dt.timedelta(days=max(1,int(raw.get('days') or 1)))
            days=max(1,min(60,(_co-_ci).days)) if _co>_ci else 1
            co=_co.isoformat()
        else:
            days=max(1,min(60,int(raw.get('days') or 1)))
            ci=co=''
        # 按日期区间占用数量：只算未过期的 pending 与已支付的订单行。
        occupied=0
        if ci:
            occupied=int((c.execute('''SELECT COALESCE(SUM(i.qty),0) FROM camp_order_items i
                     JOIN camp_orders o ON o.id=i.order_id
                     WHERE i.kind="rental" AND i.ref_id=? AND i.club_id=?
                       AND i.status IN ("pending_payment","paid")
                       AND (i.status="paid" OR o.created_at>=datetime('now','-30 minutes'))
                       AND i.check_in < ? AND i.check_out > ?''',(rid,club_id,co,ci)).fetchone() or [0])[0] or 0)
        avail=max(0,int(r['available_qty'] or 0))-occupied
        if qty>avail: raise HTTPException(409,f'该装备所选日期仅剩 {avail} 件可租')
        # #822：按老板自定义的计费项计价（项目 + 价格 + 时间单位：按小时 / 按天 / 按次…）。
        # 没配计费项的老装备仍走原来的「日租金 × 天数 × 件数」，行为一字不变。
        custom=bool(_camp_arr(r.get('pricing_json')))
        plans=_camp_rental_plans(r)
        plan=plans[0]
        pi=raw.get('plan')
        if pi is not None and str(pi)!='':
            try:
                j=int(pi)
                if 0<=j<len(plans): plan=plans[j]
            except (TypeError,ValueError): pass
        unit=round(float(plan.get('price') or 0),2)
        if raw.get('units') is not None:
            try: units=max(1,min(720,int(raw.get('units') or 1)))
            except (TypeError,ValueError): units=1
        else:
            # 按天 / 按晚 / 按半天 → 默认取天数；按小时 / 按次 → 默认 1
            units=days if plan.get('unit') in ('day','night','halfday') else 1
        subtotal=round(unit*units*qty,2)
        dep=round(float(r['deposit'] or 0)*qty,2)
        plan_label=(plan.get('name') or '租金')+' · '+(plan.get('unitLabel') or '天')
        return {'kind':'rental','id':rid,'title':r['name'] or '租赁装备','coverUrl':_camp_safe_url(r.get('cover_url')),
                'spec':plan_label if custom else ((r['description'] or '')[:60]),
                'unitLabel':'/'+(plan.get('unitLabel') or '天'),'unitPrice':unit,'qty':qty,'units':units,
                'days':days,'subtotal':subtotal,'deposit':dep,'checkIn':ci,'checkOut':co,
                'isDayUse':0,'campId':r['camp_id'],
                'promoEnabled':_camp_flag(r['promo_enabled']),'memberEnabled':_camp_flag(r['member_discount_enabled']),
                'pointsEnabled':_camp_flag(r['points_enabled'])}

    s=row(c.execute('SELECT * FROM camp_store_products WHERE id=? AND club_id=? AND status="active"',(rid,club_id)))
    if not s: raise HTTPException(404,'小站商品不存在或已下架')
    # 未支付的 pending 行先占库存（30 分钟窗口），与营位房态同节奏。
    held=int((c.execute('''SELECT COALESCE(SUM(i.qty),0) FROM camp_order_items i
                 JOIN camp_orders o ON o.id=i.order_id
                 WHERE i.kind="store" AND i.ref_id=? AND i.club_id=?
                   AND i.status="pending_payment"
                   AND o.status="pending_payment" AND o.created_at>=datetime('now','-30 minutes')''',
                 (rid,club_id)).fetchone() or [0])[0] or 0)
    if qty>max(0,int(s['stock'] or 0))-held:
        raise HTTPException(409,f'库存不足，当前可购 {max(0,int(s["stock"] or 0)-held)} 件')
    unit=round(float(s['price'] or 0),2)
    return {'kind':'store','id':rid,'title':s['name'] or '商品','coverUrl':_camp_safe_url(s.get('cover_url')),
            'spec':s['category'] or '营地自营','unitLabel':'/件','unitPrice':unit,'qty':qty,
            'days':1,'subtotal':round(unit*qty,2),'deposit':0.0,'checkIn':'','checkOut':'',
            'isDayUse':0,'campId':s['camp_id'],
            'promoEnabled':_camp_flag(s['promo_enabled']),'memberEnabled':_camp_flag(s['member_discount_enabled']),
            'pointsEnabled':_camp_flag(s['points_enabled'])}

def _camp_build_quote(c,club_id,payload):
    """报价与下单共用同一段计算，杜绝「页面显示 9 折、下单按原价」的漂移。"""
    raw=payload.get('items')
    if not isinstance(raw,list) or not raw: raise HTTPException(400,'items 不能为空')
    if len(raw)>20: raise HTTPException(400,'单次订单最多 20 行商品')
    lines=[_camp_line(c,club_id,x) for x in raw]
    uid=int(payload.get('userId') or 0) or None
    codes=[str(x).strip() for x in (payload.get('voucherCodes') or []) if str(x).strip()]
    try:
        q=camp_pricing.quote(c,club_id=club_id,user_id=uid,items=lines,
                             club_points=int(payload.get('clubPoints') or 0),
                             gear_points=int(payload.get('gearPoints') or 0),
                             voucher_codes=codes)
    except ValueError as e:
        raise HTTPException(409,str(e))
    pol=q.get('_policy') or {}
    _ov=pol.get('clubPointsEarnRateOverride')
    q['_earnRateOverride']=float(_ov) if _ov is not None else None
    return q,lines,codes

def _camp_expire_stale(c,club_id):
    """懒过期：清理超过 30 分钟未支付的订单，释放积分/券 hold、房态与库存。"""
    stale=rows(c.execute('''SELECT * FROM camp_orders WHERE club_id=? AND status="pending_payment"
                            AND created_at<datetime('now','-30 minutes')''',(club_id,)))
    for o in stale:
        try: camp_pricing.release(c,checkout_id=o['checkout_id'] or '')
        except Exception: logger.exception('camp order release failed on expire: %s',o['id'])
        c.execute('''UPDATE camp_pitch_bookings SET status="cancelled"
                     WHERE club_id=? AND camp_order_id=? AND status="pending"''',(club_id,o['id']))
        c.execute('''UPDATE checkout_intents SET status="cancelled",cancelled_at=CURRENT_TIMESTAMP,
                     updated_at=CURRENT_TIMESTAMP WHERE id=? AND status="pending_payment"''',(o['checkout_id'],))
        c.execute('UPDATE camp_order_items SET status="cancelled" WHERE order_id=?',(o['id'],))
        c.execute('''UPDATE camp_orders SET status="expired",cancelled_at=CURRENT_TIMESTAMP WHERE id=?''',(o['id'],))
    return len(stale)

def _camp_order_out(c,o,with_items=True):
    d={'id':o['id'],'orderNo':o['order_no'],'status':o['status'],
       'goodsAmount':o['goods_amount'],'memberDiscount':o['member_discount'],
       'clubPointDiscount':o['club_point_discount'],'gearPointDiscount':o['gear_point_discount'],
       'voucherDiscount':o['voucher_discount'],'payable':o['payable'],
       'depositAmount':o['deposit_amount'],'total':o['total'],'pointsEarned':o['points_earned'],
       'guestName':o['guest_name'] or '','guestPhone':o['guest_phone'] or '',
       'campId':o['camp_id'],'userId':o['user_id'],
       'createdAt':o['created_at'],'paidAt':o['paid_at'],'cancelledAt':o['cancelled_at'],
       'depositHeld':float(o['deposit_held'] or 0),'depositRefunded':float(o['deposit_refunded'] or 0),
       'depositForfeit':float(o['deposit_forfeit'] or 0),'finalAmount':float(o['final_amount'] or 0),
       'checkedInAt':o['checked_in_at'] or '','checkedOutAt':o['checked_out_at'] or '',
       'settledAt':o['settled_at'] or '','checkinNote':o['checkin_note'] or '',
       'checkoutNote':o['checkout_note'] or '',
       'redeemCode':o['redeem_code'] or '','redeemedAt':o['redeemed_at'] or '',
       'redeemedBy':o['redeemed_by'] or ''}
    if with_items:
        its=[]
        for r in rows(c.execute('SELECT * FROM camp_order_items WHERE order_id=? ORDER BY id',(o['id'],))):
            its.append({'kind':r['kind'],'id':r['ref_id'],'title':r['title'] or '',
                        'coverUrl':r['cover_url'] or '','spec':r['spec'] or '',
                        'unitLabel':r['unit_label'] or '','unitPrice':r['unit_price'],
                        'qty':r['qty'],'days':r['days'],'subtotal':r['subtotal'],
                        'deposit':r['deposit'],'checkIn':r['check_in'] or '',
                        'checkOut':r['check_out'] or '','isDayUse':r['is_day_use'],
                        'status':r['status']})
        d['items']=its
    return d

# ---- 会员权益开关（C 端只读 / 后台读写） ----
@app.get('/api/public/clubs/{club_id}/camp-benefits')
def public_camp_benefits(club_id:int,user_id:int=0,userId:int=0):
    # 兼容早期契约里的 camelCase 别名，避免未知查询参数被静默忽略导致会员信息丢失
    uid=user_id or userId or None
    club_or_404(club_id)
    with conn() as c:
        return camp_pricing.overview(c,club_id=club_id,user_id=uid)

@app.get('/api/club/{club_id}/camp-benefits')
def club_camp_benefits_get(club_id:int):
    club_or_404(club_id)
    with conn() as c: return camp_read_benefits_config(c,club_id)

@app.patch('/api/club/{club_id}/camp-benefits')
def club_camp_benefits_patch(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        cfg=camp_read_benefits_config(c,club_id)
        for k in CAMP_BENEFITS_WRITABLE:
            if k not in payload: continue
            if k=='clubPointsMaxDiscountPercent':
                try: cfg[k]=max(0.0,min(100.0,float(payload[k])))
                except (TypeError,ValueError): raise HTTPException(400,'抵扣比例必须是 0~100 的数字')
            else: cfg[k]=bool(payload[k])
        c.execute('UPDATE clubs SET camp_benefits_json=? WHERE id=?',(jdump(cfg),club_id))
        return cfg

# ---- 统一报价（只读；C 端确认订单页与下单共用） ----
@app.post('/api/public/clubs/{club_id}/camp-quote')
def public_camp_quote(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        _camp_expire_stale(c,club_id)
        q,_,_=_camp_build_quote(c,club_id,payload)
        return _camp_public_quote(q)

# ---- 统一订单：创建 / 详情 / 我的订单 / 支付 / 取消 ----
@app.post('/api/public/clubs/{club_id}/camp-orders')
def public_camp_order_create(club_id:int,payload:dict=Body(...)):
    club_or_404(club_id)
    with conn() as c:
        _camp_expire_stale(c,club_id)
        q,lines,codes=_camp_build_quote(c,club_id,payload)
        uid=int(payload.get('userId') or 0) or 1
        guest=str(payload.get('guestName') or '').strip()[:60]
        phone=str(payload.get('guestPhone') or '').strip()[:30]
        if not guest or not phone: raise HTTPException(400,'请填写联系人姓名与手机号')
        camp_id=lines[0].get('campId',0) if lines else 0
        order_no='CP'+_dt_date_compact()+uuid.uuid4().hex[:6].upper()
        rcode=_camp_redeem_code_gen(c)
        cur=c.execute('''INSERT INTO camp_orders(club_id,camp_id,user_id,order_no,status,
              goods_amount,member_discount,member_discount_rate,club_points_used,gear_points_used,
              club_point_discount,gear_point_discount,voucher_discount,voucher_codes_json,
              payable,deposit_amount,total,points_earned,points_redeem_rate,
              guest_name,guest_phone,redeem_code,quote_snapshot_json)
              VALUES(?,?,?,?,"pending_payment",?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (club_id,camp_id,uid,order_no,
             q['goodsAmount'],q['memberDiscount'],q['memberDiscountRate'],q['clubPointsUsed'],q['gearPointsUsed'],
             q['clubPointDiscount'],q['gearPointDiscount'],q['voucherDiscount'],jdump(codes),
             q['payable'],q['depositAmount'],q['total'],q['pointsEarned'],q['pointsRedeemRate'],
             guest,phone,rcode,jdump(q)))
        oid=int(cur.lastrowid)
        for ln in lines:
            c.execute('''INSERT INTO camp_order_items(order_id,club_id,kind,ref_id,title,cover_url,spec,unit_label,
                  unit_price,qty,days,check_in,check_out,is_day_use,subtotal,deposit,status)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"pending_payment")''',
                (oid,club_id,ln['kind'],ln['id'],ln['title'],ln['coverUrl'],ln['spec'],ln['unitLabel'],
                 ln['unitPrice'],ln['qty'],ln['days'],ln['checkIn'],ln['checkOut'],ln['isDayUse'],
                 ln['subtotal'],ln['deposit']))
        # 营位行同步写 camp_pitch_bookings：房态网格仍是唯一占用真源，避免新旧订单互相超售。
        for ln in lines:
            if ln['kind']!='pitch': continue
            c.execute('''INSERT INTO camp_pitch_bookings(club_id,camp_id,pitch_id,check_in,check_out,is_day_use,
                  guest_name,guest_phone,qty,nights,payable,deposit,total,status,user_id,camp_order_id)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,"pending",?,?)''',
                (club_id,ln['campId'],ln['id'],ln['checkIn'],ln['checkOut'],ln['isDayUse'],guest,phone,
                 ln['qty'],ln['days'],ln['subtotal'],ln['deposit'],
                 round(ln['subtotal']+ln['deposit'],2),uid,oid))
        # 小站商品：下单即占库存（pending 30 分钟窗口），取消/过期自动释放。
        for ln in lines:
            if ln['kind']!='store': continue
            upd=c.execute('''UPDATE camp_store_products SET stock=stock-?
                             WHERE id=? AND club_id=? AND stock>=?''',(ln['qty'],ln['id'],club_id,ln['qty']))
            if upd.rowcount!=1: raise HTTPException(409,'库存刚刚发生变化，请重新确认订单')
        # 统一结算单：沿用 checkout_intents，账本字段写全，供 materialize_redemptions 读取。
        iid='camp_'+uuid.uuid4().hex[:16]
        c.execute('''INSERT INTO checkout_intents(id,kind,user_id,club_id,status,original_amount,cash_amount,
              club_points_reserved,gear_points_reserved,club_point_discount,platform_point_subsidy,
              club_benefit_discount,platform_benefit_subsidy,member_discount,member_discount_rate,
              benefit_redemption_ids_json,payload_json,points_policy_snapshot_json,payment_status)
              VALUES(?,?,?,?,"pending_payment",?,?,?,?,?,?,?,?,?,?,?,?,?,"pending")''',
            (iid,'camp_order',uid,club_id,q['goodsAmount'],q['total'],
             q['clubPointsUsed'],q['gearPointsUsed'],q['clubPointDiscount'],q['gearPointDiscount'],
             q.get('_voucherClubDiscount',0),q.get('_voucherPlatformSubsidy',0),
             q['memberDiscount'],q['memberDiscountRate'],
             jdump([a.get('redemptionId') for a in q.get('vouchersApplied',[])]),
             jdump({'orderId':oid,'orderNo':order_no,'campId':camp_id,'total':q['total']}),
             jdump(q.get('_policy') or {})))
        c.execute('UPDATE camp_orders SET checkout_id=? WHERE id=?',(iid,oid))
        try:
            camp_pricing.hold(c,checkout_id=iid,user_id=uid,club_id=club_id,quote=q,voucher_codes=codes)
        except ValueError as e:
            raise HTTPException(409,str(e))
        return {'ok':True,'orderId':oid,'orderNo':order_no,'checkoutId':iid,
                'redeemCode':rcode,'status':'pending_payment','total':q['total'],'expiresInSeconds':1800,'nextAction':'PAY'}

def _dt_date_compact():
    import datetime as _dt
    return _dt.date.today().strftime('%Y%m%d')

@app.get('/api/public/camp-orders/{order_id}')
def public_camp_order_get(order_id:int,user_id:int=0,userId:int=0):
    uid=user_id or userId
    with conn() as c:
        o=row(c.execute('SELECT * FROM camp_orders WHERE id=?',(order_id,)))
        if not o: raise HTTPException(404,'营地订单不存在')
        if uid and int(o['user_id'] or 0)!=int(uid): raise HTTPException(404,'营地订单不存在')
        _camp_expire_stale(c,o['club_id'])
        o=row(c.execute('SELECT * FROM camp_orders WHERE id=?',(order_id,)))
        return {'ok':True,'order':_camp_order_out(c,o)}

@app.get('/api/public/clubs/{club_id}/camp-orders')
def public_camp_order_list(club_id:int,user_id:int=0,userId:int=0,limit:int=30):
    uid=user_id or userId
    club_or_404(club_id)
    with conn() as c:
        _camp_expire_stale(c,club_id)
        sql='SELECT * FROM camp_orders WHERE club_id=?'; args=[club_id]
        if uid: sql+=' AND user_id=?'; args.append(int(uid))
        os_=rows(c.execute(sql+' ORDER BY id DESC LIMIT ?',args+[max(1,min(100,int(limit)))]))
        return {'ok':True,'orders':[_camp_order_out(c,o) for o in os_]}

@app.get('/api/club/{club_id}/camp-orders')
def club_camp_order_list(club_id:int,status:str='',limit:int=100):
    club_or_404(club_id)
    with conn() as c:
        _camp_expire_stale(c,club_id)
        sql='SELECT * FROM camp_orders WHERE club_id=?'; args=[club_id]
        if status: sql+=' AND status=?'; args.append(status)
        return {'ok':True,'orders':[_camp_order_out(c,o) for o in
                rows(c.execute(sql+' ORDER BY id DESC LIMIT ?',args+[max(1,min(300,int(limit)))]))]}

@app.get('/api/club/{club_id}/camp-orders/{order_id}')
def club_camp_order_detail(club_id:int,order_id:int):
    # #808：订单详情抽屉数据源（顾客信息 / 明细 / 押金 / 履约时间线），仅后台可见。
    club_or_404(club_id)
    with conn() as c:
        o=row(c.execute('SELECT * FROM camp_orders WHERE id=? AND club_id=?',(order_id,club_id)))
        if not o: raise HTTPException(404,'营地订单不存在')
        return {'ok':True,'order':_camp_order_out(c,o)}

@app.post('/api/public/camp-orders/{order_id}/pay')
def public_camp_order_pay(order_id:int,payload:dict=Body(default={})):
    # 与既有 camp-checkouts/confirm 同口径：生产环境必须由已验证的支付渠道回调触发。
    if IS_PROD and not (payload or {}).get('providerVerified'):
        raise HTTPException(403,'生产环境须由已验证的支付渠道回调确认')
    with conn() as c:
        o=row(c.execute('SELECT * FROM camp_orders WHERE id=?',(order_id,)))
        if not o: raise HTTPException(404,'营地订单不存在')
        if o['status']=='paid':
            return {'ok':True,'orderId':order_id,'status':'paid','total':o['total'],
                    'pointsEarned':o['points_earned'],'idempotent':True,'nextAction':'ORDERS'}
        if o['status']!='pending_payment': raise HTTPException(409,'订单已取消或已超时，请重新下单')
        if row(c.execute('''SELECT 1 FROM camp_orders WHERE id=?
                            AND created_at<datetime('now','-30 minutes')''',(order_id,))):
            raise HTTPException(409,'支付超时，库存已释放，请重新下单')
        q=jload(o['quote_snapshot_json'],{}) or {}
        earned=camp_pricing.finalize(c,checkout_id=o['checkout_id'],order_id=order_id,
                                     club_id=o['club_id'],user_id=o['user_id'],quote=q)
        c.execute('''UPDATE camp_orders SET status="paid",paid_at=CURRENT_TIMESTAMP,points_earned=? WHERE id=?''',
                  (earned,order_id))
        c.execute('UPDATE camp_order_items SET status="paid" WHERE order_id=?',(order_id,))
        c.execute('''UPDATE camp_pitch_bookings SET status="paid"
                     WHERE club_id=? AND camp_order_id=? AND status="pending"''',(o['club_id'],order_id))
        c.execute('''UPDATE checkout_intents SET status="paid",payment_status="succeeded",
                     paid_at=CURRENT_TIMESTAMP,result_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                  (str(order_id),o['checkout_id']))
        return {'ok':True,'orderId':order_id,'orderNo':o['order_no'],'status':'paid',
                'total':o['total'],'pointsEarned':earned,'nextAction':'ORDERS'}

@app.post('/api/public/camp-orders/{order_id}/cancel')
def public_camp_order_cancel(order_id:int):
    with conn() as c:
        o=row(c.execute('SELECT * FROM camp_orders WHERE id=?',(order_id,)))
        if not o: raise HTTPException(404,'营地订单不存在')
        if o['status']=='cancelled': return {'ok':True,'orderId':order_id,'status':'cancelled','idempotent':True}
        if o['status']=='paid': raise HTTPException(409,'已支付订单请走售后流程')
        if o['status']=='expired': return {'ok':True,'orderId':order_id,'status':'expired','idempotent':True}
        camp_pricing.release(c,checkout_id=o['checkout_id'] or '')
        for it in rows(c.execute('SELECT * FROM camp_order_items WHERE order_id=?',(order_id,))):
            if it['kind']=='store':
                c.execute('UPDATE camp_store_products SET stock=stock+? WHERE id=? AND club_id=?',
                          (it['qty'],it['ref_id'],o['club_id']))
        c.execute('''UPDATE camp_pitch_bookings SET status="cancelled"
                     WHERE club_id=? AND camp_order_id=? AND status="pending"''',(o['club_id'],order_id))
        c.execute('''UPDATE checkout_intents SET status="cancelled",cancelled_at=CURRENT_TIMESTAMP,
                     updated_at=CURRENT_TIMESTAMP WHERE id=? AND status="pending_payment"''',(o['checkout_id'],))
        c.execute('UPDATE camp_order_items SET status="cancelled" WHERE order_id=?',(order_id,))
        c.execute('''UPDATE camp_orders SET status="cancelled",cancelled_at=CURRENT_TIMESTAMP WHERE id=?''',(order_id,))
        return {'ok':True,'orderId':order_id,'status':'cancelled'}

# ---- 履约两步状态机（后台）：确认入住（登记押金） / 办理退房（退押金 + 最终结算）----
# 状态流转：paid → checked_in（登记押金 deposit_held）→ completed（退押金 deposit_refunded + settled_at）。
# 仅后台可调；生产环境同样走后台登录态（CLUBOS_TEST_OPEN 下免登录）。房态带与订单列表据此联动。
@app.patch('/api/club/{club_id}/camp-orders/{order_id}/checkin')
def club_camp_order_checkin(club_id:int,order_id:int,payload:dict=Body(default={})):
    club_or_404(club_id)
    with conn() as c:
        o=row(c.execute('SELECT * FROM camp_orders WHERE id=? AND club_id=?',(order_id,club_id)))
        if not o: raise HTTPException(404,'营地订单不存在')
        if o['status']=='checked_in': return {'ok':True,'orderId':order_id,'status':'checked_in',
                                              'depositHeld':float(o['deposit_held'] or o['deposit_amount'] or 0),
                                              'idempotent':True}
        if o['status']=='completed': raise HTTPException(409,'订单已完成退房，不能重复入住')
        if o['status']!='paid': raise HTTPException(409,'仅「已支付」订单可办理入住')
        note=str((payload or {}).get('note') or '')
        # #808：押金可手动调整（默认取订单押金）；退房扣减与退还一律以实收押金 deposit_held 为基数。
        dep_req=(payload or {}).get('depositHeld')
        held=round(float(dep_req),2) if dep_req is not None and float(dep_req or 0)>0 \
            else round(float(o['deposit_amount'] or 0),2)
        c.execute('''UPDATE camp_orders SET status="checked_in",checked_in_at=CURRENT_TIMESTAMP,
                     deposit_held=?,checkin_note=? WHERE id=?''',(held,note,order_id))
        c.execute('''UPDATE camp_pitch_bookings SET status="checked_in"
                     WHERE club_id=? AND camp_order_id=? AND status="paid"''',(club_id,order_id))
        return {'ok':True,'orderId':order_id,'status':'checked_in',
                'depositHeld':held,'checkinNote':note}

@app.patch('/api/club/{club_id}/camp-orders/{order_id}/checkout')
def club_camp_order_checkout(club_id:int,order_id:int,payload:dict=Body(default={})):
    club_or_404(club_id)
    with conn() as c:
        o=row(c.execute('SELECT * FROM camp_orders WHERE id=? AND club_id=?',(order_id,club_id)))
        if not o: raise HTTPException(404,'营地订单不存在')
        if o['status']=='completed': return {'ok':True,'orderId':order_id,'status':'completed','idempotent':True}
        if o['status']!='checked_in': raise HTTPException(409,'仅「已入住」订单可办理退房')
        note=str((payload or {}).get('note') or '')
        # #808：押金基数=实收押金 deposit_held（入住时可手动调整）；forfeit 仅在显式传入小于押金时记扣减。
        dep=float(o['deposit_held'] or o['deposit_amount'] or 0)
        forfeit=round(min(dep,max(0.0,float((payload or {}).get('depositForfeit') or 0))),2)
        refunded=round(dep-forfeit,2); final=round(float(o['total'] or 0),2)
        c.execute('''UPDATE camp_orders SET status="completed",checked_out_at=CURRENT_TIMESTAMP,settled_at=CURRENT_TIMESTAMP,
                     deposit_refunded=?,deposit_forfeit=?,final_amount=?,checkout_note=? WHERE id=?''',
                  (refunded,forfeit,final,note,order_id))
        c.execute('''UPDATE camp_pitch_bookings SET status="completed"
                     WHERE club_id=? AND camp_order_id=? AND status="checked_in"''',(club_id,order_id))
        return {'ok':True,'orderId':order_id,'status':'completed',
                'depositRefunded':refunded,'depositForfeit':forfeit,'finalAmount':final,'checkoutNote':note}

@app.get('/api/club/{club_id}/camp-pitch-bookings')
def club_camp_pitch_bookings(club_id:int,pitch_id:int=0,date:str=''):
    club_or_404(club_id)
    with conn() as c:
        sql='SELECT b.*,o.order_no,o.status AS order_status,o.guest_name,o.guest_phone,o.total,o.deposit_amount '
        sql+='FROM camp_pitch_bookings b LEFT JOIN camp_orders o ON o.id=b.camp_order_id WHERE b.club_id=?'
        args=[club_id]
        if pitch_id: sql+=' AND b.pitch_id=?'; args.append(pitch_id)
        if date:
            sql+=' AND b.check_in<=? AND b.check_out>?'; args+= [date,date]
        recs=rows(c.execute(sql+' ORDER BY b.check_in DESC, b.id DESC',args))
        return {'ok':True,'bookings':[{k:v for k,v in dict(r).items()} for r in recs]}

# ---- 营地 v4.1 #812：订单核销码（工作人员手机后台扫码/输码核销） ----
def _camp_redeem_code_gen(c):
    """8 位大写核销码（字母表去掉易混淆的 0/O/1/I），查重后返回。"""
    import secrets as _s
    alpha='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    for _ in range(50):
        code=''.join(_s.choice(alpha) for _ in range(8))
        if not c.execute('SELECT 1 FROM camp_orders WHERE redeem_code=?',(code,)).fetchone():
            return code
    raise HTTPException(500,'核销码生成失败，请重试')

def _camp_redeem_apply(c,club_id:int,o,by:str=''):
    """核销语义：到店核销确认（独立字段，不改变 paid/checked_in/completed 状态机）。
    待支付/已取消/已过期不可核销；已核销幂等拒绝（409）。"""
    st=o['status']
    if st=='pending_payment': raise HTTPException(409,'订单尚未支付，不能核销')
    if st in ('cancelled','expired'): raise HTTPException(409,'订单已取消或已过期，不能核销')
    if o['redeemed_at']: raise HTTPException(409,'该订单已于 '+str(o['redeemed_at'])+' 核销，请勿重复操作')
    c.execute("UPDATE camp_orders SET redeemed_at=datetime('now','localtime'),redeemed_by=? WHERE id=?",
              (str(by or '')[:40],o['id']))
    return row(c.execute('SELECT * FROM camp_orders WHERE id=?',(o['id'],)))

@app.post('/api/club/{club_id}/camp-orders/redeem')
def club_camp_order_redeem_by_code(club_id:int,payload:dict=Body(default={})):
    """扫码核销：前端把二维码解码出的核销码 POST 上来，按码找本俱乐部订单并核销。"""
    club_or_404(club_id)
    code=str((payload or {}).get('code') or '').strip().upper()
    if not code: raise HTTPException(400,'缺少核销码')
    with conn() as c:
        o=row(c.execute('SELECT * FROM camp_orders WHERE club_id=? AND redeem_code=?',(club_id,code)))
        if not o: raise HTTPException(404,'核销码无效：本俱乐部没有该订单')
        o=_camp_redeem_apply(c,club_id,o,by=(payload or {}).get('by') or '')
        return {'ok':True,'order':_camp_order_out(c,o)}

@app.post('/api/club/{club_id}/camp-orders/{order_id}/redeem')
def club_camp_order_redeem(club_id:int,order_id:int,payload:dict=Body(default={})):
    """按订单号核销（详情抽屉按钮场景）；传 code 时做码单双校验。"""
    club_or_404(club_id)
    with conn() as c:
        o=row(c.execute('SELECT * FROM camp_orders WHERE id=? AND club_id=?',(order_id,club_id)))
        if not o: raise HTTPException(404,'营地订单不存在')
        code=str((payload or {}).get('code') or '').strip().upper()
        if code and o['redeem_code'] and code!=o['redeem_code']:
            raise HTTPException(400,'核销码与订单不匹配')
        o=_camp_redeem_apply(c,club_id,o,by=(payload or {}).get('by') or '')
        return {'ok':True,'order':_camp_order_out(c,o)}

# ---- 商品详情（高标准详情页数据源） ----
@app.get('/api/public/clubs/{club_id}/camp-products/{kind}/{pid}')
def public_camp_product_detail(club_id:int,kind:str,pid:int):
    club_or_404(club_id)
    tbl={'pitch':'camp_pitches','rental':'camp_rental_items','store':'camp_store_products'}.get(kind)
    if not tbl: raise HTTPException(404,'商品类型不存在')
    with conn() as c:
        r=row(c.execute(f'SELECT * FROM {tbl} WHERE id=? AND club_id=? AND status="active"',(pid,club_id)))
        if not r: raise HTTPException(404,'商品不存在或已下架')
        d=_camp_goods_out(r,kind)
        if kind=='pitch':
            price=round(float(r['base_price'] or 0),2); stock=0; daily=0.0; dep=0.0
            unit=d['unitLabel'] or ('/晚' if r['is_overnight'] else '/天')
        elif kind=='rental':
            price=round(float(r['daily_price'] or 0),2); stock=max(0,int(r['available_qty'] or 0))
            daily=price; dep=round(float(r['deposit'] or 0),2); unit=d['unitLabel'] or '/天'
            # #822：自定义计费项 —— 详情页默认展示第一项（按小时 / 按天…），由顾客自己挑
            plans=d.get('pricing') or []
            if plans:
                price=round(float(plans[0].get('price') or 0),2)
                unit='/'+(plans[0].get('unitLabel') or '天')
        else:
            price=round(float(r['price'] or 0),2); stock=max(0,int(r['stock'] or 0))
            daily=0.0; dep=0.0; unit=d['unitLabel'] or '/件'
        return {'ok':True,'kind':kind,'id':pid,'name':r['name'] or '',
                'subtitle':d['subtitle'],'coverUrl':d['coverUrl'],'gallery':d['gallery'],
                'focalPoints':d['focalPoints'],'coverFocal':d['coverFocal'],
                'price':price,'originalPrice':d['originalPrice'],'unitLabel':unit,
                'specs':d['specs'],'facilities':d['facilities'],'policies':d['policies'],
                'description':d['description'],'detailText':d['detailText'],
                'aiDetail':jload(r.get('ai_detail_json'), None),
                'stock':stock,'dailyPrice':daily,'deposit':dep,
                # #822：租赁计费项（项目 + 价格 + 单位），C 端让顾客直接点选
                'pricing':(d.get('pricing') if kind=='rental' else None),
                'isOvernight':bool(r['is_overnight']) if kind=='pitch' else False,
                'capacity':int(r['capacity'] or 0) if kind=='pitch' else 0,
                'pitchType':r['pitch_type'] if kind=='pitch' else ''}


# ===========================================================================
# 营地 v4（2026-11-11）：本地素材工作台 + AI 图文详情
# 四类对象（营地档案 / 营位 / 装备租赁 / 营地小站）共用同一套上传与生成链路。
# 老板只做两件事：上传本地照片（≤5 张、服务端按选定比例统一裁剪）+
# 在一个文本框里写照片说明与经营规则；规格/设施/须知/图文详情全部由模型生成。
# ===========================================================================
from clubos_domain.camp_detail_ai import generate_camp_detail as _camp_ai_detail
from clubos_domain import camp_ai_kit as _camp_ai_kit   # #814 资料图片视觉观察 + 版式模板库

_CAMP_TARGET_TABLE = {
    'profile': 'camp_profiles', 'pitch': 'camp_pitches',
    'rental': 'camp_rental_items', 'store': 'camp_store_products',
}
_CAMP_MEDIA_RATIOS = {'4:3': (1600, 1200), '1:1': (1400, 1400), '16:9': (1600, 900)}
_CAMP_MEDIA_MAX = 5


def _camp_media_public(club_id: int, url: str) -> str:
    name = str(url or '').rsplit('/', 1)[-1]
    return f'/api/public/clubs/{club_id}/camp-media-file/uploads/{club_id}/camp/{name}'


# ---- #813 图片手动裁剪 + 焦点自动适配 --------------------------------------
def _camp_focal_clamp(v, lo=0.05, hi=0.95):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.5
    if x != x:  # NaN
        return 0.5
    return max(lo, min(hi, x))


def _camp_parse_crop(raw):
    """归一化裁剪框 [x,y,w,h]（0~1）；非法一律返回 None，回落到「按焦点 cover 裁」。"""
    try:
        arr = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(arr, (list, tuple)) or len(arr) != 4:
            return None
        x, y, w, h = [float(v) for v in arr]
        if not (0.0 < w <= 1.0 and 0.0 < h <= 1.0 and 0.0 <= x < 1.0 and 0.0 <= y < 1.0):
            return None
        if x + w > 1.0001 or y + h > 1.0001:
            return None
        return [x, y, min(1.0 - x, w), min(1.0 - y, h)]
    except Exception:
        return None


def _camp_auto_focal(im):
    """自动适配最佳显示范围：低分辨率梯度能量找主体，避开大片平滑天空/白墙。

    天空是低纹理区（梯度小），地景/建筑/人物纹理丰富（梯度大）。按行求能量质心并
    对下半区加权（户外照片主体通常在下 2/3），输出可直接给 object-position 的焦点。
    """
    try:
        small = im.convert('L').resize((64, 64), Image.LANCZOS)
        px = list(small.getdata())
        row_e, col_e = [], []
        for y in range(64):
            row_e.append(sum(abs(px[y * 64 + x] - px[y * 64 + x + 1]) for x in range(63)) + 1.0)
        for x in range(64):
            col_e.append(sum(abs(px[y * 64 + x] - px[(y + 1) * 64 + x]) for y in range(63)) + 1.0)
        rw = [row_e[i] ** 2 * (1.0 + 0.55 * (i / 63.0)) for i in range(64)]
        cw = [col_e[i] ** 2 for i in range(64)]
        fy = sum(rw[i] * (i + 0.5) for i in range(64)) / sum(rw) / 64.0
        fx = sum(cw[i] * (i + 0.5) for i in range(64)) / sum(cw) / 64.0
        return (_camp_focal_clamp(fx, 0.2, 0.8), _camp_focal_clamp(fy, 0.2, 0.82))
    except Exception:
        return (0.5, 0.5)


def _camp_render_crop(im, ratio, crop, fx, fy):
    """先按手动裁剪框取景（或按焦点 cover 裁），再统一输出到目标尺寸。"""
    from PIL import ImageOps
    w, h = _CAMP_MEDIA_RATIOS[ratio]
    box = _camp_parse_crop(crop)
    used_manual = False
    if box:
        x, y, bw, bh = box
        iw, ih = im.size
        left, top = int(round(x * iw)), int(round(y * ih))
        right, bottom = int(round((x + bw) * iw)), int(round((y + bh) * ih))
        if right - left >= 8 and bottom - top >= 8:
            im = im.crop((left, top, right, bottom))
            used_manual = True
    # 手动取景后居中收口；否则用焦点控制 cover 裁的位置
    im = ImageOps.fit(im, (w, h), method=Image.LANCZOS,
                      centering=(0.5, 0.5) if used_manual else (fx, fy))
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=86, optimize=True)
    return buf.getvalue(), w, h


def _camp_source_bytes(im):
    """保留一份压缩后的原图，供事后重新手动裁剪（不改变构图）。"""
    cp = im.copy()
    cp.thumbnail((2000, 2000), Image.LANCZOS)
    buf = io.BytesIO()
    cp.save(buf, 'JPEG', quality=88, optimize=True)
    return buf.getvalue()


def _camp_focal_list(raw, n):
    """与 gallery 同序的焦点数组；老数据缺列时全部回落 0.5/0.5。"""
    arr = _camp_arr(raw)
    out = []
    for i in range(max(0, n)):
        v = arr[i] if i < len(arr) and isinstance(arr[i], dict) else {}
        out.append({'x': _camp_focal_clamp(v.get('x', 0.5)), 'y': _camp_focal_clamp(v.get('y', 0.5))})
    return out


def _camp_cover_focal(cover, gal, foc):
    if not foc:
        return {'x': 0.5, 'y': 0.5}
    try:
        i = gal.index(cover) if cover else 0
    except ValueError:
        i = 0
    return foc[i] if 0 <= i < len(foc) else foc[0]


def _camp_media_sync(c, club_id: int, kind: str, tid: int):
    """素材表是上传真源，gallery_json / cover_url / media_focal_json 是 C 端渲染真源。"""
    tbl = _CAMP_TARGET_TABLE[kind]
    assets = rows(c.execute('''SELECT * FROM camp_media_assets
                               WHERE club_id=? AND target_kind=? AND target_id=?
                               ORDER BY sort_no,id''', (club_id, kind, tid)))
    urls = [_camp_media_public(club_id, a['url']) for a in assets]
    cover = next((_camp_media_public(club_id, a['url']) for a in assets if a['is_cover']), urls[0] if urls else '')
    foc = jdump([{'x': _camp_focal_clamp(a['focal_x']), 'y': _camp_focal_clamp(a['focal_y'])} for a in assets])
    c.execute(f'UPDATE {tbl} SET gallery_json=?, cover_url=?, media_focal_json=? WHERE id=? AND club_id=?',
              (jdump(urls), cover, foc, tid, club_id))
    return urls, cover


@app.post('/api/club/{club_id}/camp-media')
async def upload_camp_media(club_id: int, target_kind: str = Form(...), target_id: int = Form(0),
                            ratio: str = Form('4:3'), file: UploadFile = File(...),
                            crop: str = Form(''), focal_x: float = Form(0.5),
                            focal_y: float = Form(0.5), auto_fit: int = Form(0)):
    """#813 本地图片上传：手动裁剪框 / 焦点 / 一键自动适配；保留原图供事后重裁。"""
    from PIL import ImageOps
    club_or_404(club_id)
    if target_kind not in _CAMP_TARGET_TABLE:
        raise HTTPException(400, '对象类型必须是 profile / pitch / rental / store')
    if ratio not in _CAMP_MEDIA_RATIOS:
        raise HTTPException(400, '比例必须是 4:3 / 1:1 / 16:9')
    real = Path(file.filename or '').suffix.lower()
    if real not in _PUBLIC_IMAGE_EXT:
        raise HTTPException(400, '仅支持图片文件：png/jpg/jpeg/webp/gif')
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, '图片过大（上限 10MB）')
    fx, fy = _camp_focal_clamp(focal_x), _camp_focal_clamp(focal_y)
    box = _camp_parse_crop(crop)
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            im = ImageOps.exif_transpose(im).convert('RGB')
            if auto_fit:
                fx, fy = _camp_auto_focal(im)
                box = None
            out, w, h = _camp_render_crop(im, ratio, box, fx, fy)
            src = _camp_source_bytes(im)
    except Exception:
        raise HTTPException(400, '图片文件损坏或格式不受支持')
    dest = UPLOAD / str(club_id) / 'camp'
    dest.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex + '.jpg'
    src_name = uuid.uuid4().hex + '_src.jpg'
    (dest / name).write_bytes(out)
    (dest / src_name).write_bytes(src)
    url = f'/static/uploads/{club_id}/camp/{name}'
    src_url = f'/static/uploads/{club_id}/camp/{src_name}'
    preview = f'/api/club/{club_id}/camp-media-file/uploads/{club_id}/camp/{name}'
    with conn() as c:
        cnt = int(c.execute('''SELECT COUNT(*) FROM camp_media_assets
                               WHERE club_id=? AND target_kind=? AND target_id=?''',
                            (club_id, target_kind, target_id)).fetchone()[0])
        if cnt >= _CAMP_MEDIA_MAX:
            raise HTTPException(409, f'每个对象最多上传 {_CAMP_MEDIA_MAX} 张图片，请先删除后再上传')
        c.execute('''INSERT INTO camp_media_assets(club_id,target_kind,target_id,url,preview,ratio,
                       width,height,bytes,sort_no,is_cover,src_url,crop_json,focal_x,focal_y,auto_fit,rev)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)''',
                  (club_id, target_kind, target_id, url, preview, ratio, w, h, len(out), cnt,
                   1 if cnt == 0 else 0, src_url, jdump(box) if box else None,
                   fx, fy, 1 if auto_fit else 0))
        asset_id = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        urls, cover = _camp_media_sync(c, club_id, target_kind, target_id)
    return {'ok': True, 'id': asset_id, 'url': url, 'preview': preview,
            'publicUrl': _camp_media_public(club_id, url), 'ratio': ratio,
            'width': w, 'height': h, 'bytes': len(out), 'count': len(urls),
            'gallery': urls, 'cover': cover,
            'focalX': fx, 'focalY': fy, 'autoFit': bool(auto_fit), 'crop': box or []}


@app.get('/api/club/{club_id}/camp-media-list')
def club_camp_media_list(club_id: int, target_kind: str, target_id: int = 0):
    club_or_404(club_id)
    if target_kind not in _CAMP_TARGET_TABLE:
        raise HTTPException(400, '对象类型不存在')
    with conn() as c:
        a = rows(c.execute('''SELECT * FROM camp_media_assets
                              WHERE club_id=? AND target_kind=? AND target_id=?
                              ORDER BY sort_no,id''', (club_id, target_kind, target_id)))
        return {'ok': True, 'max': _CAMP_MEDIA_MAX, 'ratios': list(_CAMP_MEDIA_RATIOS),
                'items': [{'id': x['id'], 'url': x['url'], 'preview': x['preview'],
                           'publicUrl': _camp_media_public(club_id, x['url']),
                           'srcPreview': f'/api/club/{club_id}/camp-media/{x["id"]}/source',
                           'ratio': x['ratio'], 'width': x['width'], 'height': x['height'],
                           'focalX': _camp_focal_clamp(x['focal_x']),
                           'focalY': _camp_focal_clamp(x['focal_y']),
                           'crop': _camp_parse_crop(x['crop_json']) or [],
                           'autoFit': bool(x['auto_fit']), 'rev': int(x['rev'] or 1),
                           'sortNo': x['sort_no'], 'isCover': bool(x['is_cover'])} for x in a]}


@app.get('/api/club/{club_id}/camp-media/{asset_id}/source')
def club_camp_media_source(club_id: int, asset_id: int):
    """#813 读取保留的原图，供后台裁剪弹层拖拽取景（成品已裁过，不能拿来重裁）。"""
    from fastapi.responses import FileResponse
    with conn() as c:
        a = row(c.execute('SELECT * FROM camp_media_assets WHERE id=? AND club_id=?', (asset_id, club_id)))
    if not a:
        raise HTTPException(404, '素材不存在')
    p = _safe_media_path(a['src_url'] or a['url'])
    if not p or not p.is_file():
        raise HTTPException(404, '原图不存在')
    r = FileResponse(p)
    r.headers['Cache-Control'] = 'private, max-age=300'
    return r


@app.post('/api/club/{club_id}/camp-media/{asset_id}/crop')
def club_camp_media_crop(club_id: int, asset_id: int, payload: dict = Body(default={})):
    """#813 事后重新手动裁剪 / 一键自动适配：从原图重出成品，URL 换新以破浏览器缓存。"""
    from PIL import ImageOps
    p = payload or {}
    ratio = str(p.get('ratio') or '4:3')
    if ratio not in _CAMP_MEDIA_RATIOS:
        raise HTTPException(400, '比例必须是 4:3 / 1:1 / 16:9')
    box = _camp_parse_crop(p.get('crop'))
    fx, fy = _camp_focal_clamp(p.get('focalX', 0.5)), _camp_focal_clamp(p.get('focalY', 0.5))
    auto = bool(p.get('autoFit'))
    with conn() as c:
        a = row(c.execute('SELECT * FROM camp_media_assets WHERE id=? AND club_id=?', (asset_id, club_id)))
    if not a:
        raise HTTPException(404, '素材不存在')
    src = _safe_media_path(a['src_url'] or a['url'])
    if not src or not src.is_file():
        raise HTTPException(404, '原图不存在，请删除后重新上传')
    try:
        with Image.open(src) as im:
            im.load()
            im = ImageOps.exif_transpose(im).convert('RGB')
            if auto:
                fx, fy = _camp_auto_focal(im)
                box = None
            out, w, h = _camp_render_crop(im, ratio, box, fx, fy)
    except Exception:
        raise HTTPException(400, '原图损坏，无法重新裁剪')
    dest = UPLOAD / str(club_id) / 'camp'
    dest.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex + '.jpg'
    (dest / name).write_bytes(out)
    url = f'/static/uploads/{club_id}/camp/{name}'
    preview = f'/api/club/{club_id}/camp-media-file/uploads/{club_id}/camp/{name}'
    with conn() as c:
        c.execute('''UPDATE camp_media_assets SET url=?,preview=?,ratio=?,width=?,height=?,bytes=?,
                     crop_json=?,focal_x=?,focal_y=?,auto_fit=?,rev=rev+1 WHERE id=? AND club_id=?''',
                  (url, preview, ratio, w, h, len(out), jdump(box) if box else None,
                   fx, fy, 1 if auto else 0, asset_id, club_id))
        rev = int(row(c.execute('SELECT rev FROM camp_media_assets WHERE id=?', (asset_id,)))['rev'] or 1)
        urls, cover = _camp_media_sync(c, club_id, a['target_kind'], a['target_id'])
    # 旧成品不再被任何地方引用，清掉；原图必须保留供再次裁剪
    try:
        old = _safe_media_path(a['url'])
        if old and old.is_file() and old != src:
            old.unlink()
    except Exception:
        pass
    return {'ok': True, 'id': asset_id, 'url': url, 'preview': preview,
            'publicUrl': _camp_media_public(club_id, url), 'ratio': ratio,
            'focalX': fx, 'focalY': fy, 'autoFit': auto, 'crop': box or [],
            'rev': rev, 'gallery': urls, 'cover': cover}


@app.post('/api/club/{club_id}/camp-media/{asset_id}/move')
def club_camp_media_move(club_id: int, asset_id: int, payload: dict = Body(default={})):
    """#809：素材排序（dir=left/right 与相邻素材交换位置）；首张不可左移、末张不可右移。"""
    direction = str((payload or {}).get('dir') or '')
    if direction not in ('left', 'right'):
        raise HTTPException(400, 'dir 必须是 left / right')
    with conn() as c:
        a = row(c.execute('SELECT * FROM camp_media_assets WHERE id=? AND club_id=?', (asset_id, club_id)))
        if not a:
            raise HTTPException(404, '素材不存在')
        kind, tid = a['target_kind'], a['target_id']
        ordered = rows(c.execute('''SELECT id FROM camp_media_assets
                                    WHERE club_id=? AND target_kind=? AND target_id=? ORDER BY sort_no,id''',
                                 (club_id, kind, tid)))
        ids = [x['id'] for x in ordered]
        i = ids.index(asset_id)
        j = i - 1 if direction == 'left' else i + 1
        if j < 0 or j >= len(ids):
            return {'ok': True, 'moved': False, 'sortNo': i}
        ids[i], ids[j] = ids[j], ids[i]
        # sort_no 有 (club,kind,target,sort_no) 联合 UNIQUE 约束：先整体挪到负数区避开中间态冲突
        for k, mid in enumerate(ids):
            c.execute('UPDATE camp_media_assets SET sort_no=? WHERE id=?', (k - 1000000, mid))
        for k, mid in enumerate(ids):
            c.execute('UPDATE camp_media_assets SET sort_no=? WHERE id=?', (k, mid))
        _, cover = _camp_media_sync(c, club_id, kind, tid)
        return {'ok': True, 'moved': True, 'cover': cover}


@app.post('/api/club/{club_id}/camp-media/{asset_id}/cover')
def club_camp_media_cover(club_id: int, asset_id: int, payload: dict = Body(default={})):
    kind = str((payload or {}).get('targetKind') or '')
    tid = int((payload or {}).get('targetId') or 0)
    with conn() as c:
        a = row(c.execute('SELECT * FROM camp_media_assets WHERE id=? AND club_id=?', (asset_id, club_id)))
        if not a:
            raise HTTPException(404, '素材不存在')
        kind = kind or a['target_kind']; tid = tid or a['target_id']
        c.execute('''UPDATE camp_media_assets SET is_cover=0
                     WHERE club_id=? AND target_kind=? AND target_id=?''', (club_id, kind, tid))
        c.execute('UPDATE camp_media_assets SET is_cover=1 WHERE id=?', (asset_id,))
        _, cover = _camp_media_sync(c, club_id, kind, tid)
        return {'ok': True, 'cover': cover}


@app.delete('/api/club/{club_id}/camp-media/{asset_id}')
def club_camp_media_delete(club_id: int, asset_id: int):
    with conn() as c:
        a = row(c.execute('SELECT * FROM camp_media_assets WHERE id=? AND club_id=?', (asset_id, club_id)))
        if not a:
            raise HTTPException(404, '素材不存在')
        kind, tid = a['target_kind'], a['target_id']
        c.execute('DELETE FROM camp_media_assets WHERE id=?', (asset_id,))
        left = rows(c.execute('''SELECT id FROM camp_media_assets
                                 WHERE club_id=? AND target_kind=? AND target_id=? ORDER BY sort_no,id''',
                              (club_id, kind, tid)))
        for i, x in enumerate(left):
            c.execute('UPDATE camp_media_assets SET sort_no=? WHERE id=?', (i, x['id']))
        if left and not c.execute('''SELECT 1 FROM camp_media_assets
                                     WHERE club_id=? AND target_kind=? AND target_id=? AND is_cover=1''',
                                  (club_id, kind, tid)).fetchone():
            c.execute('UPDATE camp_media_assets SET is_cover=1 WHERE id=?', (left[0]['id'],))
        urls, cover = _camp_media_sync(c, club_id, kind, tid)
        return {'ok': True, 'count': len(urls), 'gallery': urls, 'cover': cover}


def _camp_media_serve(club_id: int, asset_path: str, *, public: bool):
    from fastapi.responses import FileResponse
    original = '/static/' + unquote(asset_path)
    with conn() as c:
        hit = c.execute('SELECT 1 FROM camp_media_assets WHERE club_id=? AND url=?',
                        (club_id, original)).fetchone()
    if not hit:
        raise HTTPException(404, '素材不存在')
    p = _safe_media_path(original)
    if not p or not p.is_file():
        raise HTTPException(404, '素材不存在')
    r = FileResponse(p)
    r.headers['Cache-Control'] = 'public, max-age=300' if public else 'private, max-age=300'
    return r


@app.get('/api/club/{club_id}/camp-media-file/{asset_path:path}')
def club_camp_media_file(club_id: int, asset_path: str):
    """后台编辑弹窗即时预览（带登录态）。"""
    return _camp_media_serve(club_id, asset_path, public=False)


@app.get('/api/public/clubs/{club_id}/camp-media-file/{asset_path:path}')
def public_camp_media_file(club_id: int, asset_path: str):
    """C 端公开读取：只放行素材表里真实引用过的文件，不是对 uploads 通配开放。"""
    x = club_or_404(club_id)
    if IS_PROD and x['status'] != 'active':
        raise HTTPException(404, 'not found')
    return _camp_media_serve(club_id, asset_path, public=True)

@app.get('/api/club/{club_id}/camp-src-file/{asset_path:path}')
def club_camp_src_file(club_id: int, asset_path: str):
    """#814 后台预览：上传的参考资料图片（PDF/PPT 预览页亦登记在此表）。"""
    return _camp_src_serve(club_id, asset_path, public=False)


@app.get('/api/public/clubs/{club_id}/camp-src-file/{asset_path:path}')
def public_camp_src_file(club_id: int, asset_path: str):
    """#814 C 端公开读取：只放行生成时登记过的资料图，防止任意读 uploads。"""
    x = club_or_404(club_id)
    if IS_PROD and x['status'] != 'active':
        raise HTTPException(404, 'not found')
    return _camp_src_serve(club_id, asset_path, public=True)


def _camp_src_serve(club_id: int, asset_path: str, *, public: bool):
    from fastapi.responses import FileResponse
    original = '/static/' + unquote(asset_path)
    with conn() as c:
        hit = c.execute('SELECT 1 FROM camp_ai_source_assets WHERE club_id=? AND url=?',
                        (club_id, original)).fetchone()
    if not hit:
        raise HTTPException(404, '资料图片不存在')
    p = _safe_media_path(original)
    if not p or not p.is_file():
        raise HTTPException(404, '资料图片不存在')
    r = FileResponse(p)
    r.headers['Cache-Control'] = 'public, max-age=300' if public else 'private, max-age=300'
    return r



def _camp_ai_base(c, club_id: int, kind: str, tid: int):
    tbl = _CAMP_TARGET_TABLE[kind]
    r = row(c.execute(f'SELECT * FROM {tbl} WHERE id=? AND club_id=?', (tid, club_id)))
    if not r:
        return None
    d = dict(r)
    hard = {}
    if kind == 'pitch':
        hard = {'营位类型': d.get('pitch_type') or '营位', '可住人数': f"{int(d.get('capacity') or 0)} 人",
                '平日价': f"¥{round(float(d.get('base_price') or 0),2)}",
                '计价单位': d.get('unit_label') or ('/晚' if d.get('is_overnight') else '/天')}
    elif kind == 'rental':
        hard = {'日租金': f"¥{round(float(d.get('daily_price') or 0),2)}",
                '押金': f"¥{round(float(d.get('deposit') or 0),2)}",
                '可租数量': f"{int(d.get('available_qty') or 0)} 件",
                '计价单位': d.get('unit_label') or '/天'}
    elif kind == 'store':
        hard = {'售价': f"¥{round(float(d.get('price') or 0),2)}",
                '库存': f"{int(d.get('stock') or 0)} 件", '计价单位': d.get('unit_label') or '/件'}
    return {'name': d.get('name') or '', 'hardSpecs': hard,
            'facilities': _camp_arr(d.get('facilities_json'))}


# ---- #814 资料图片：统一媒体池 + 本地画面观察 --------------------------------
def _camp_src_public(club_id: int, p: Path) -> str:
    try:
        rel = p.relative_to(STATIC).as_posix()
    except Exception:
        return ''
    return f'/api/public/clubs/{club_id}/camp-src-file/{rel}'


def _camp_build_media(c, club_id: int, kind: str, tid: int, job_id: str,
                      assets: list, source_images: list) -> tuple[list, list]:
    """商品照片（gallery 真源）+ 本次上传的资料图片 → 统一媒体池。

    每张图都经 camp_ai_kit.describe_image() 生成画面观察：观察结果既是写作素材，
    也作为 media[].caption 下发 C 端。资料图同时登记进 camp_ai_source_assets，
    才能被公开代理白名单放行（旧版资料图只落 camp_src/，C 端永远取不到）。
    """
    media, paths = [], []
    for a in assets:
        fp = UPLOAD / str(club_id) / 'camp' / str(a['url']).rsplit('/', 1)[-1]
        if not fp.is_file():
            continue
        obs = _camp_ai_kit.describe_image(fp, source='gallery', name=fp.name) or {}
        media.append({
            'url': _camp_media_public(club_id, a['url']), 'name': fp.name,
            'source': 'gallery', 'caption': str(obs.get('caption') or ''),
            'tags': obs.get('tags') or [], 'usable': True,
            'focal': {'x': _camp_focal_clamp(a['focal_x']), 'y': _camp_focal_clamp(a['focal_y'])},
        })
        paths.append(str(fp))
    seen = set()
    for raw in source_images:
        pp = Path(str(raw))
        if not pp.is_file() or pp.suffix.lower() not in _IMAGE_UPLOAD_EXTS:
            continue
        key = str(pp)
        if key in seen:
            continue
        seen.add(key)
        obs = _camp_ai_kit.describe_image(pp, source='reference')
        if not obs:
            continue
        url = _camp_src_public(club_id, pp)
        if not url:
            continue
        try:
            stored = '/static/' + pp.relative_to(STATIC).as_posix()
        except Exception:
            continue
        c.execute("""INSERT OR IGNORE INTO camp_ai_source_assets
                     (club_id,target_kind,target_id,job_id,url,name,kind,caption,tags_json)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                  (club_id, kind, tid, job_id, stored, pp.name, 'image',
                   obs['caption'], jdump(obs['tags'])))
        media.append({
            'url': url, 'name': pp.name, 'source': 'reference',
            'caption': obs['caption'], 'tags': obs['tags'],
            'focal': obs['focal'], 'usable': obs['usable'],
        })
        paths.append(str(pp))
    return media, paths


async def _run_camp_detail_job(job_id: str, club_id: int, kind: str, tid: int,
                               source_text: str, assets: list, saved_paths: list,
                               template_id: str):
    from document_parser import parse_sources
    try:
        with conn() as c:
            c.execute('UPDATE camp_detail_jobs SET status="running" WHERE id=?', (job_id,))
        # #814：parse_sources 不再重复接收 source_text（旧版文本框内容被拼了两遍）；
        # 资料里的图片（含 PDF/PPT 内页渲染图）全部取出做画面观察。
        extra_text, source_images = '', []
        if saved_paths:
            src = parse_sources([Path(p) for p in saved_paths], '', STATIC)
            extra_text = str(src.get('text') or '')
            source_images = [m.get('path') for m in (src.get('images') or []) if m.get('path')]
        with conn() as c:
            base = _camp_ai_base(c, club_id, kind, tid) or {'name': '', 'hardSpecs': {}, 'facilities': []}
            media, media_paths = _camp_build_media(c, club_id, kind, tid, job_id,
                                                   assets, source_images)
        result, usage = await _camp_ai_detail(
            club_id=club_id, target_kind=kind, base=base,
            source_text=(source_text + '\n\n' + extra_text).strip(),
            media_paths=media_paths, media=media, template_id=template_id)
        try:
            charge_credits(club_id, 'detail', getattr(usage, 'usage_id', None))
        except Exception:
            pass
        with conn() as c:
            c.execute("""UPDATE camp_detail_jobs SET status="done",result_json=?,
                         finished_at=CURRENT_TIMESTAMP WHERE id=?""", (jdump(result), job_id))
            # 模板样本：后续可据此统计各版式的实际使用与效果，逐步沉淀成学习样本
            c.execute("""INSERT INTO camp_ai_template_samples
                         (club_id,target_kind,target_id,template_id,job_id,media_count,summary)
                         VALUES (?,?,?,?,?,?,?)""",
                      (club_id, kind, tid, _camp_ai_kit.get_template(template_id)['id'], job_id,
                       len(result.get('media') or []), str(result.get('visualSummary') or '')[:200]))
    except Exception as e:
        with conn() as c:
            c.execute("""UPDATE camp_detail_jobs SET status="failed",error=?,
                         finished_at=CURRENT_TIMESTAMP WHERE id=?""", (str(e)[:300], job_id))


@app.post('/api/club/{club_id}/camp-ai/generate')
async def camp_ai_generate(club_id: int, background_tasks: BackgroundTasks,
                           target_kind: str = Form(...), target_id: int = Form(0),
                           prompt: str = Form(''), template: str = Form('classic'),
                           files: list[UploadFile] = File(default=[])):
    """一个文本框 + 已上传照片 + 可选资料文件 + 版式模板 → 异步生成结构化图文详情。"""
    from document_parser import save_uploads
    club_or_404(club_id)
    if target_kind not in _CAMP_TARGET_TABLE:
        raise HTTPException(400, '对象类型不存在')
    ensure_credits(club_id, 'detail')
    tid_tpl = _camp_ai_kit.get_template(template)['id']
    with conn() as c:
        running = c.execute("""SELECT COUNT(*) FROM camp_detail_jobs
                               WHERE club_id=? AND status IN ("queued","running")""", (club_id,)).fetchone()[0]
        if running:
            raise HTTPException(409, '已有一次 AI 生成正在进行中，请等它完成再提交新的')
        assets = rows(c.execute("""SELECT * FROM camp_media_assets
                                   WHERE club_id=? AND target_kind=? AND target_id=? ORDER BY sort_no,id""",
                                (club_id, target_kind, target_id)))
    media_paths = [str(UPLOAD / str(club_id) / 'camp' / str(a['url']).rsplit('/', 1)[-1]) for a in assets]
    media_paths = [p for p in media_paths if Path(p).is_file()]
    saved = []
    if files:
        batch = UPLOAD / str(club_id) / 'camp_src' / uuid.uuid4().hex
        saved = [str(p) for p in save_uploads(files, batch, max_total_bytes=20 * 1024 * 1024 if IS_PROD else None)]
    if not str(prompt or '').strip() and not media_paths and not saved:
        raise HTTPException(400, '先上传照片、资料文件，或在文本框里写几句说明')
    job_id = 'campai_' + uuid.uuid4().hex[:16]
    with conn() as c:
        c.execute("""INSERT INTO camp_detail_jobs(id,club_id,target_kind,target_id,status,
                       source_text,media_json,template_id) VALUES(?,?,?,?,"queued",?,?,?)""",
                  (job_id, club_id, target_kind, target_id, str(prompt or '')[:6000],
                   jdump(media_paths + saved), tid_tpl))
    background_tasks.add_task(_run_camp_detail_job, job_id, club_id, target_kind, target_id,
                              str(prompt or '')[:6000], assets, saved, tid_tpl)
    return {'ok': True, 'jobId': job_id, 'status': 'queued',
            'mediaCount': len(media_paths), 'templateId': tid_tpl}


@app.get('/api/club/{club_id}/camp-ai/jobs/{job_id}')
def camp_ai_job_status(club_id: int, job_id: str):
    with conn() as c:
        j = row(c.execute('SELECT * FROM camp_detail_jobs WHERE id=? AND club_id=?', (job_id, club_id)))
        if not j:
            raise HTTPException(404, '生成任务不存在')
        return {'ok': True, 'jobId': job_id, 'status': j['status'],
                'result': jload(j['result_json'], None) if j['status'] == 'done' else None,
                'error': j['error'] or ''}


@app.post('/api/club/{club_id}/camp-ai/apply')
def camp_ai_apply(club_id: int, payload: dict = Body(...)):
    """把生成结果写回对象：一个 AI 结果同时填齐规格 / 设施 / 须知 / 简介 / 图文详情。"""
    p = payload or {}
    kind = str(p.get('targetKind') or '')
    tid = int(p.get('targetId') or 0)
    detail = p.get('detail') if isinstance(p.get('detail'), dict) else None
    if kind not in _CAMP_TARGET_TABLE:
        raise HTTPException(400, '对象类型不存在')
    if detail is None:
        jid = str(p.get('jobId') or '')
        with conn() as c:
            j = row(c.execute('SELECT * FROM camp_detail_jobs WHERE id=? AND club_id=?', (jid, club_id)))
        if not j or j['status'] != 'done':
            raise HTTPException(409, '生成结果尚未就绪')
        detail = jload(j['result_json'], {}) or {}
    specs = [{'label': str(s.get('label') or ''), 'value': str(s.get('value') or '')}
             for s in (detail.get('specs') or []) if isinstance(s, dict)]
    facilities = [str(x) for x in (detail.get('facilities') or [])]
    policies = [{'label': str(s.get('label') or ''), 'text': str(s.get('text') or '')}
                for s in (detail.get('policies') or []) if isinstance(s, dict)]
    tbl = _CAMP_TARGET_TABLE[kind]
    with conn() as c:
        if not row(c.execute(f'SELECT 1 FROM {tbl} WHERE id=? AND club_id=?', (tid, club_id))):
            raise HTTPException(404, '对象不存在')
        if kind == 'profile':
            # #814：intro_text 是头图上的一句话 slogan，绝不能再写整段规则/说明。
            # 完整介绍留在 ai_detail_json，由 C 端独立「营地详细介绍」页消费。
            short = str(detail.get('subtitle') or detail.get('description') or '').strip()
            short = short.replace('\n', ' ')[:40]
            c.execute("""UPDATE camp_profiles SET intro_text=?,facilities_json=?,rules_json=?,
                         ai_detail_json=? WHERE id=? AND club_id=?""",
                      (short, jdump(facilities), jdump(policies), jdump(detail), tid, club_id))
        else:
            c.execute(f"""UPDATE {tbl} SET subtitle=?,description=?,detail_text=?,specs_json=?,
                          facilities_json=?,policies_json=?,ai_detail_json=? WHERE id=? AND club_id=?""",
                      (str(detail.get('subtitle') or '')[:80], str(detail.get('description') or '')[:60],
                       jdump(detail.get('sections') or []), jdump(specs), jdump(facilities),
                       jdump(policies), jdump(detail), tid, club_id))
        return {'ok': True, 'targetKind': kind, 'targetId': tid,
                'specs': len(specs), 'facilities': len(facilities), 'policies': len(policies)}


# ---- #814 版式模板：可选、可列举、可沉淀使用样本（供后续学习与迭代） ----------
@app.get('/api/club/{club_id}/camp-ai/templates')
def camp_ai_templates(club_id: int):
    club_or_404(club_id)
    with conn() as c:
        counts = {r['template_id']: r['n'] for r in rows(c.execute(
            """SELECT template_id,COUNT(*) n FROM camp_ai_template_samples
               WHERE club_id=? GROUP BY template_id""", (club_id,)))}
    out = []
    for t in _camp_ai_kit.list_templates():
        t = dict(t)
        t['samples'] = int(counts.get(t['id'], 0))
        out.append(t)
    return {'ok': True, 'default': _camp_ai_kit.DEFAULT_TEMPLATE, 'templates': out}


@app.get('/api/club/{club_id}/camp-ai/templates/samples')
def camp_ai_template_samples(club_id: int, limit: int = 20):
    """模板学习样本：每次生成留一条（模板 / 对象 / 识别图片数 / 画面摘要）。"""
    club_or_404(club_id)
    with conn() as c:
        return {'ok': True, 'items': [dict(r) for r in rows(c.execute(
            """SELECT id,target_kind,target_id,template_id,job_id,media_count,summary,created_at
               FROM camp_ai_template_samples WHERE club_id=?
               ORDER BY id DESC LIMIT ?""", (club_id, max(1, min(100, limit)))))]}
