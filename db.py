from __future__ import annotations
import json, sqlite3, os
from pathlib import Path
from contextlib import contextmanager

ROOT=Path(__file__).resolve().parent
DB_PATH=Path(os.getenv('CLUBOS_DB_PATH', str(ROOT/'clubos.db')))
SCHEMA=ROOT/'schema.sql'

@contextmanager
def conn():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON')
    try:
        yield c; c.commit()
    finally:c.close()

def rows(cur):return [dict(r) for r in cur.fetchall()]
def row(cur):
    r=cur.fetchone(); return dict(r) if r else None

def _demo_media():
    base='/static/demo/icebreaker/'
    names=['image11.png','image8.png','image13.png','image31.png','image24.png','image28.jpeg','image9.png','image7.png','image12.png','image10.png','image23.png','image27.png','image15.png','image29.jpeg','image14.png','image19.png','image17.png','image16.png']
    out=[]
    for i,n in enumerate(names,1):
        # Extractor prefixes files with source stem; locate by suffix at runtime.
        found=list((ROOT/'static'/'demo'/'icebreaker').glob('*_'+n))
        if found:
            out.append({'ref':f'img_{i:02d}','name':found[0].name,'url':base+found[0].name})
    return out


def _ensure_column(c, table, column, ddl):
    cols={r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _backfill_v019_opening_inventory(c):
    # Preserve pre-v0.19 on-hand stock as an auditable opening balance. This is not
    # fabricated supplier history: it is explicitly marked as a migration balance.
    for p in c.execute("SELECT id,stock,average_cost FROM products WHERE inventory_mode='platform_owned' AND source!='external_api'").fetchall():
        key=f"v019-opening:{p['id']}"
        if not c.execute('SELECT 1 FROM inventory_movements WHERE product_id=?',(p['id'],)).fetchone():
            qty=int(p['stock'] or 0)
            c.execute('''INSERT INTO inventory_movements(product_id,movement_type,quantity_delta,stock_before,stock_after,unit_cost,total_cost,reference_type,reference_id,note,actor_type,idempotency_key)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(p['id'],'opening_balance',qty,0,qty,float(p['average_cost'] or 0),round(qty*float(p['average_cost'] or 0),2),'migration','v0.19','v0.19 升级时承接历史库存余额','system',key))



def _backfill_v020_warehouse(c):
    from clubos_domain.warehouse import WarehouseEngine
    WarehouseEngine().backfill_opening(c)


def _backfill_v021_finance(c):
    from clubos_domain.merchandise_finance import MerchandiseFinanceEngine
    finance=MerchandiseFinanceEngine()
    c.execute("""UPDATE gear_order_items SET unit_cost_snapshot=COALESCE((SELECT average_cost FROM products p WHERE p.id=gear_order_items.product_id),0)
                 WHERE unit_cost_snapshot IS NULL""")
    for r in c.execute('SELECT id FROM purchase_receipts ORDER BY received_at,id').fetchall():
        finance.register_receipt_payable(c,receipt_id=str(r['id']))

def _backfill_plan_gear_discount(c):
    """装备折扣从「俱乐部会员等级」迁到「平台订阅套餐」（2026-10-23 用户明确：
    会员等级及规则不涉及装备折扣，折扣程度由该俱乐部订阅的套餐决定，总平台控制）。

    一次性回填（platform_settings 标记防重跑）：Starter 无折扣 / Pro 9.5 折 / Enterprise 9 折，
    正好对齐原「普通/银卡/金卡」三档。平台之后随时可以在套餐管理里改，迁移绝不再碰。
    """
    _ensure_column(c,'ai_credit_plans','gear_discount','gear_discount REAL NOT NULL DEFAULT 1.0')
    done=c.execute("SELECT value FROM platform_settings WHERE key='plan_gear_discount_backfilled'").fetchone()
    if done: return
    for code,rate in (('starter',1.0),('pro',0.95),('enterprise',0.90)):
        c.execute('UPDATE ai_credit_plans SET gear_discount=?,updated_at=CURRENT_TIMESTAMP WHERE code=?',(rate,code))
    c.execute("INSERT INTO platform_settings(key,value) VALUES('plan_gear_discount_backfilled','1') "
              "ON CONFLICT(key) DO UPDATE SET value='1'")


def _backfill_plan_gear_points_percent(c):
    """装备积分抵用从「俱乐部活动自己决定」迁到「俱乐部订阅的套餐决定 + 平台按俱乐部设比例」
    （2026-10-24 用户明确：装备积分抵用不是由俱乐部决定，而应由该俱乐部选用的套餐决定；
    基础套餐没有这项权益，Pro/Enterprise 可以抵扣，但比例由总平台针对不同俱乐部单独设计）。

    一次性回填（platform_settings 标记防重跑）：
    · Starter = 0% → 该套餐俱乐部完全没有装备积分抵用资格。
    · Pro = 20% / Enterprise = 50% → 具备资格，比例是「平台未按俱乐部单独设定时」的默认值，
      平台之后可以在「积分补贴」页按俱乐部改比例，迁移绝不再碰。
    """
    _ensure_column(c,'ai_credit_plans','gear_points_redeem_percent',
                   'gear_points_redeem_percent REAL NOT NULL DEFAULT 0')
    done=c.execute("SELECT value FROM platform_settings WHERE key='plan_gear_points_percent_backfilled'").fetchone()
    if done: return
    for code,pct in (('starter',0.0),('pro',20.0),('enterprise',50.0)):
        c.execute('UPDATE ai_credit_plans SET gear_points_redeem_percent=?,updated_at=CURRENT_TIMESTAMP WHERE code=?',
                  (pct,code))
    c.execute("INSERT INTO platform_settings(key,value) VALUES('plan_gear_points_percent_backfilled','1') "
              "ON CONFLICT(key) DO UPDATE SET value='1'")


def _backfill_v023_ai_credits(c):
    from clubos_domain.ai_credits import AICreditEngine
    # seed_defaults 会写 gear_discount 列（装备折扣随套餐，2026-10-23）——
    # 存量库必须先补列再播种，否则 INSERT 直接炸。
    _ensure_column(c,'ai_credit_plans','gear_discount','gear_discount REAL NOT NULL DEFAULT 1.0')
    # seed_defaults 的 INSERT 会写 gear_points_redeem_percent 列（装备积分抵用资格，
    # 2026-10-24）。补列必须排在 seed_defaults 之前 —— 排在后面的话存量库上
    # 直接 OperationalError: table ai_credit_plans has no column named
    # gear_points_redeem_percent，线上启动即 500。
    _ensure_column(c,'ai_credit_plans','gear_points_redeem_percent',
                   'gear_points_redeem_percent REAL NOT NULL DEFAULT 0')
    eng=AICreditEngine(); eng.seed_defaults(c)
    c.execute("INSERT OR IGNORE INTO platform_settings(key,value) VALUES('ai_usd_cny_rate','7.2')")
    # Preserve legacy club plan labels by mapping known plan codes. Existing balances are untouched.
    for cr in c.execute('SELECT id,plan,status FROM clubs').fetchall():
        cid=int(cr['id']); plan=str(cr['plan'] or 'pro').lower()
        eng.ensure_account(c,cid)
        if plan not in {'starter','pro','enterprise'}:
            plan='pro'
        if not c.execute('SELECT 1 FROM club_ai_subscriptions WHERE club_id=?',(cid,)).fetchone():
            pr=c.execute('SELECT * FROM ai_credit_plans WHERE code=?',(plan,)).fetchone()
            if pr:
                from datetime import date
                period=eng._month_key(); start,end=eng._period_bounds(period)
                c.execute('''INSERT INTO club_ai_subscriptions(club_id,plan_code,status,current_period_start,current_period_end,auto_renew,monthly_fee_snapshot,monthly_credits_snapshot)
                             VALUES(?,?,?,?,?,?,?,?)''',(cid,plan,'active' if cr['status']=='active' else 'paused',start,end,1,float(pr['monthly_fee']),int(pr['monthly_credits'])))
                c.execute('UPDATE ai_credit_accounts SET monthly_quota=? WHERE club_id=?',(int(pr['monthly_credits']),cid))


def _backfill_v027_variants(c):
    """给存量商品补一个「默认」规格承接原库存，并把 image_url 补进图集。

    库存真源从 products.stock 下沉到 product_variants.stock 之后，存量商品从来没有
    规格这个概念 —— 不回填的话，凡是新写的按规格读库存的链路都会读到 0，
    商品会在 C 端集体变成「暂时缺货」。所以给每个还没有规格的商品建一个
    is_default=1、stock 等于原 products.stock 的规格。按 product_id 判空，重复执行幂等。

    图集同理：详情页只读 product_images，不回填的话存量商品一张图都没有
    （它们只有 products.image_url 单图）。补进去后单图商品的轮播自然退化成一张。
    """
    for p in c.execute('SELECT id,sku,stock,image_url FROM products').fetchall():
        pid=p['id']
        if not c.execute('SELECT 1 FROM product_variants WHERE product_id=?',(pid,)).fetchone():
            c.execute('INSERT INTO product_variants(product_id,name,sku,stock,is_default,sort,status) VALUES(?,?,?,?,?,?,?)',
                      (pid,'默认',p['sku'],int(p['stock'] or 0),1,0,'active'))
        if p['image_url'] and not c.execute('SELECT 1 FROM product_images WHERE product_id=?',(pid,)).fetchone():
            c.execute('INSERT INTO product_images(product_id,url,sort) VALUES(?,?,?)',(pid,p['image_url'],0))

def _backfill_v028_leader_avatar(c):
    """给 club_leaders 补 avatar_url 列。

    存量领队一律没有头像，这里只加列不编造数据 —— 前端渲染时按「有头像用头像、
    没有就用姓名首字生成的占位圆」降级，不要给老领队硬塞一张假图。
    可空 + 无回填，所以本函数本身幂等（_ensure_column 已判列存在）。
    """
    _ensure_column(c,'club_leaders','avatar_url','avatar_url TEXT')

def _backfill_v030_catalog(c):
    """把 products.category 的自由文本收敛成真正的品类字典（2026-10-24）。

    多品牌上架要求「品牌 / 品类 / 人群」是结构化的。存量商品只有 category 文本
    （"服装" / "徒步装备" / "背包"），这里一次性把它们建成一级品类并回填
    category_id —— 不删原文本列：老接口、C 端副标题、Medusa 同步都还在读它，
    贸然清空会让存量商品在列表里品类变空白。

    品牌不回填：库里从来没有品牌这个概念，凭商品名猜品牌等于编造数据，
    一律留空由运营认领。
    """
    done = c.execute("SELECT value FROM platform_settings WHERE key='catalog_backfilled_v030'").fetchone()
    for p in c.execute('SELECT id,category FROM products').fetchall():
        name = str(p['category'] or '').strip()
        if not name:
            continue
        ct = c.execute('SELECT id FROM product_categories WHERE name=? AND IFNULL(parent_id,0)=0', (name,)).fetchone()
        if not ct:
            c.execute('INSERT INTO product_categories(name,slug,parent_id,sort,status) VALUES(?,?,?,?,?)',
                      (name, name.upper()[:12], None, 0, 'active'))
            cid = int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        else:
            cid = int(ct['id'])
        c.execute('UPDATE products SET category_id=? WHERE id=? AND category_id IS NULL', (cid, int(p['id'])))
    if not done:
        c.execute("INSERT INTO platform_settings(key,value) VALUES('catalog_backfilled_v030','1') "
                  "ON CONFLICT(key) DO UPDATE SET value='1'")


def _run_compat_migrations(c):
    # ★ 2026-11-04：新列必须排在所有写它们的 INSERT / UPDATE 之前。
    # 2026-11-05 咨询对话：两条老咨询表补「会话凭据 + 最后消息时间 + 顾客未读」。
    # 存量行 access_token 为 NULL → C 端无法认领（安全默认），后台/领队端仍可回复。
    for _t in ('leader_inquiries','route_inquiries'):
        _ensure_column(c,_t,'access_token','access_token TEXT')
        _ensure_column(c,_t,'last_message_at','last_message_at TEXT')
        _ensure_column(c,_t,'unread_customer','unread_customer INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'products','is_featured','is_featured INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'users','platform_member','platform_member INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'users','platform_member_since','platform_member_since TEXT')
    # 「注册成为该俱乐部会员 → 自动成为总平台会员」用触发器兜住所有写入路径
    # （报名 / 邀请码 / 买会员卡 / 会员中心 refresh_member），不漏任何一个调用点。
    c.execute("""CREATE TRIGGER IF NOT EXISTS trg_club_member_platform
        AFTER INSERT ON club_members
        BEGIN
          UPDATE users SET platform_member=1,
            platform_member_since=COALESCE(platform_member_since,CURRENT_TIMESTAMP)
          WHERE id=NEW.user_id;
        END""")
    # 存量会员回填（幂等；新库里 trigger 已经处理，这条只影响历史数据）
    c.execute("""UPDATE users SET platform_member=1,
        platform_member_since=COALESCE(platform_member_since,created_at)
        WHERE platform_member=0 AND id IN (SELECT user_id FROM club_members)""")
    # v0.29 俱乐部设置：品牌 DIY（logo / slogan），名字本列就有
    _ensure_column(c,'clubs','logo_url','logo_url TEXT')
    _ensure_column(c,'clubs','slogan','slogan TEXT')
    _ensure_column(c,'clubs','contact_name','contact_name TEXT')
    _ensure_column(c,'clubs','contact_phone','contact_phone TEXT')
    # 2026-11-03 官网申请表新增必填邮箱：后台审核通过后用它发账户密码与使用说明。
    _ensure_column(c,'clubs','contact_email',"contact_email TEXT NOT NULL DEFAULT ''")
    _ensure_column(c,'clubs','city','city TEXT')
    # 活动城市（2026-10-23 用户反馈）：SaaS 面向全国，后台新建活动要选城市、C 端要按城市筛选。
    # 历史活动没有该列 → 前端按 location 文本兜底推导（static/ux/clubos-cities.js），不回写库。
    _ensure_column(c,'activities','city','city TEXT')
    _ensure_column(c,'clubs','application_note','application_note TEXT')
    _ensure_column(c,'clubs','business_license_ref','business_license_ref TEXT')
    _ensure_column(c,'clubs','reviewed_at','reviewed_at TEXT')
    # 2026-10-31 俱乐部级「新活动默认积分规则」：新建活动继承这四个值，
    # 运营在「积分策略」页设一次即全场生效，不用逐场进活动详情改。
    # 默认值沿用 activities 的历史默认（全开 + 可抵 100%），存量俱乐部升级后行为不变。
    _ensure_column(c,'clubs','default_points_enabled','default_points_enabled INTEGER NOT NULL DEFAULT 1')
    _ensure_column(c,'clubs','default_earn_club_points','default_earn_club_points INTEGER NOT NULL DEFAULT 1')
    _ensure_column(c,'clubs','default_accept_club_points','default_accept_club_points INTEGER NOT NULL DEFAULT 1')
    _ensure_column(c,'clubs','default_club_points_max_discount_percent','default_club_points_max_discount_percent REAL NOT NULL DEFAULT 100')
    # 2026-11-05 活动积分现金换算比例（X 积分 = ¥1）：俱乐部级默认，0 = 跟随平台全局默认。
    # 补列必须排在 _run_compat_migrations 里（init_db 第一个调用），因为下面的会员等级
    # upsert 与积分策略 PATCH 都会写这两列，存量库启动晚一步就撞 OperationalError。
    # 存量行落 0 = 完全沿用平台全局值，行为零变化，绝不把平台历史配置顶成 100。
    _ensure_column(c,'clubs','points_redeem_rate','points_redeem_rate INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'clubs','reviewed_by','reviewed_by TEXT')
    _ensure_column(c,'clubs','disabled_reason','disabled_reason TEXT')
    # 入驻来源（2026-10-26）：官网「申请体验」表单提交的申请与平台手动
    # 「新增入驻」共用 clubs 表，用 source 区分（website / platform），
    # 平台端「俱乐部入驻」里据此显示来源徽标并单列官网申请明细。
    # 存量行 ADD COLUMN 自动落默认值 'platform'（历史数据无来源概念）。
    # 注意：public_club_application / platform_add_club 的 INSERT 会写该列，
    # 补列必须留在 _run_compat_migrations（init_db 第一个调用）里，天然在前。
    _ensure_column(c,'clubs','source',"source TEXT NOT NULL DEFAULT 'platform'")
    # 2026-11-03 俱乐部企业资料（需求：后台详情展示公司名称/法人/公司地址以便管理）。
    # 存量行 ADD COLUMN 自动落空串；补列天然排在所有写这些列的 UPDATE 之前。
    _ensure_column(c,'clubs','company_name',"company_name TEXT NOT NULL DEFAULT ''")
    _ensure_column(c,'clubs','legal_person',"legal_person TEXT NOT NULL DEFAULT ''")
    _ensure_column(c,'clubs','company_address',"company_address TEXT NOT NULL DEFAULT ''")
    _ensure_column(c,'ai_credit_adjustment_debt','resolved_amount','resolved_amount INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'ai_credit_adjustment_debt','resolved_at','resolved_at TEXT')
    _ensure_column(c,'registrations','commerce_order_id','commerce_order_id TEXT')
    _ensure_column(c,'registrations','booking_ref','booking_ref TEXT')
    _ensure_column(c,'registrations','points_policy_snapshot_json','points_policy_snapshot_json TEXT')
    _ensure_column(c,'registrations','club_benefit_discount','club_benefit_discount REAL NOT NULL DEFAULT 0')
    # 2026-11-05 活动团期「保障」提示：专业户外运动保险保额（单位：万），俱乐部后台可配置。
    # C 端活动详情展示「专业户外运动保险价值 X 万」，未配置则不显示该行。
    # 存量行 ADD COLUMN 落 0 = 未配置，行为零变化。
    _ensure_column(c,'clubs','insurance_coverage_wan','insurance_coverage_wan REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'registrations','platform_benefit_subsidy','platform_benefit_subsidy REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'registrations','benefit_redemption_ids_json',"benefit_redemption_ids_json TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(c,'benefit_redemptions','gift_note','gift_note TEXT')
    # v0.31 优惠券体系（2026-10-24）：member_benefits 扩成「券模板」——
    # 形态（直接抵 / 满减）、门槛、有效期、投放范围（全平台 / 指定俱乐部 / 指定城市）、
    # 通用券图、领取方式（后台发放 / 用户领取）、每人上限、已领计数、能否分享。
    # 全部可空/有默认值 → 存量库老券继续按原语义工作，不需要数据回填。
    _ensure_column(c,'member_benefits','mode',"mode TEXT NOT NULL DEFAULT 'fixed'")
    _ensure_column(c,'member_benefits','threshold_amount','threshold_amount REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'member_benefits','image_key',"image_key TEXT NOT NULL DEFAULT 'sunrise'")
    _ensure_column(c,'member_benefits','valid_from','valid_from TEXT')
    _ensure_column(c,'member_benefits','valid_to','valid_to TEXT')
    _ensure_column(c,'member_benefits','claim_mode',"claim_mode TEXT NOT NULL DEFAULT 'grant'")
    _ensure_column(c,'member_benefits','target_type',"target_type TEXT NOT NULL DEFAULT 'all_clubs'")
    _ensure_column(c,'member_benefits','target_city','target_city TEXT')
    _ensure_column(c,'member_benefits','per_user_limit','per_user_limit INTEGER NOT NULL DEFAULT 1')
    _ensure_column(c,'member_benefits','claimed_count','claimed_count INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'member_benefits','share_enabled','share_enabled INTEGER NOT NULL DEFAULT 1')
    # 用户注册城市：城市定向投放（成都区域的用户在 11 月都能领一张券）要按它圈人。
    _ensure_column(c,'users','city','city TEXT')
    # 2026-10-31 C 端头像：自定义上传（avatar_url）+ 微信授权头像（wechat_avatar_url，自动同步入口）
    _ensure_column(c,'users','avatar_url','avatar_url TEXT')
    _ensure_column(c,'users','wechat_avatar_url','wechat_avatar_url TEXT')
    _ensure_column(c,'activities','points_enabled','points_enabled INTEGER NOT NULL DEFAULT 1')
    _ensure_column(c,'activities','earn_club_points','earn_club_points INTEGER NOT NULL DEFAULT 1')
    _ensure_column(c,'activities','accept_club_points','accept_club_points INTEGER NOT NULL DEFAULT 1')
    _ensure_column(c,'activities','club_points_max_discount_percent','club_points_max_discount_percent REAL NOT NULL DEFAULT 100')
    _ensure_column(c,'activities','accept_gear_points','accept_gear_points INTEGER NOT NULL DEFAULT 1')
    _ensure_column(c,'activities','gear_points_max_discount_amount','gear_points_max_discount_amount REAL')
    _ensure_column(c,'activities','club_points_earn_rate_override','club_points_earn_rate_override REAL')
    _ensure_column(c,'activities','refund_enabled','refund_enabled INTEGER NOT NULL DEFAULT 1')
    _ensure_column(c,'activities','refund_policy_json','refund_policy_json TEXT')
    _ensure_column(c,'activities','participant_form_policy_json','participant_form_policy_json TEXT')
    _ensure_column(c,'activities','cover','cover TEXT')
    _ensure_column(c,'club_members','current_tier_id','current_tier_id INTEGER')
    _ensure_column(c,'club_members','lifetime_activity_spend','lifetime_activity_spend REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'club_members','activity_count','activity_count INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'club_members','tier_updated_at','tier_updated_at TEXT')
    _ensure_column(c,'products','commerce_product_id','commerce_product_id TEXT')
    _ensure_column(c,'products','commerce_variant_id','commerce_variant_id TEXT')
    _ensure_column(c,'products','commerce_inventory_item_id','commerce_inventory_item_id TEXT')
    _ensure_column(c,'products','commerce_sync_status',"commerce_sync_status TEXT NOT NULL DEFAULT 'local'")
    _ensure_column(c,'products','commerce_sync_error','commerce_sync_error TEXT')
    _ensure_column(c,'products','last_commerce_sync_at','last_commerce_sync_at TEXT')
    _ensure_column(c,'products','average_cost','average_cost REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'products','last_purchase_cost','last_purchase_cost REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'products','last_inbound_at','last_inbound_at TEXT')
    _ensure_column(c,'products','reorder_point','reorder_point INTEGER NOT NULL DEFAULT 5')
    _ensure_column(c,'suppliers','payment_term_days','payment_term_days INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'purchase_receipt_items','ordered_unit_cost','ordered_unit_cost REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'purchase_receipt_items','price_variance','price_variance REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'gear_order_items','unit_cost_snapshot','unit_cost_snapshot REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'gear_orders','shipping_cost','shipping_cost REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'gear_orders','packaging_cost','packaging_cost REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'gear_orders','commerce_order_id','commerce_order_id TEXT')
    _ensure_column(c,'gear_orders','commerce_cart_id','commerce_cart_id TEXT')
    _ensure_column(c,'gear_orders','commerce_fulfillment_id','commerce_fulfillment_id TEXT')
    _ensure_column(c,'gear_orders','commerce_sync_status',"commerce_sync_status TEXT NOT NULL DEFAULT 'local'")
    _ensure_column(c,'gear_orders','shipped_at','shipped_at TEXT')
    _ensure_column(c,'gear_orders','delivered_at','delivered_at TEXT')
    _ensure_column(c,'gear_orders','cash_paid','cash_paid REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'gear_orders','platform_point_subsidy','platform_point_subsidy REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'gear_orders','platform_benefit_subsidy','platform_benefit_subsidy REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'gear_orders','benefit_redemption_ids_json',"benefit_redemption_ids_json TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(c,'gear_orders','refunded_at','refunded_at TEXT')
    _ensure_column(c,'gear_orders','refunded_cash_total','refunded_cash_total REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'gear_orders','refunded_goods_total','refunded_goods_total REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'refund_requests','after_sales_case_id','after_sales_case_id TEXT')
    # 电商购买链路补全：商品图文描述 / 订单收货信息 / 用户地址簿。
    # 装备是实物、由平台统一发货，结算单里没有收货信息，整条购买链就断在
    # 「付了钱货不知道寄到哪」。三处都可空 —— 历史订单没有地址仍然合法。
    _ensure_column(c,'products','description','description TEXT')
    _ensure_column(c,'gear_orders','shipping_consignee','shipping_consignee TEXT')
    _ensure_column(c,'gear_orders','shipping_phone','shipping_phone TEXT')
    _ensure_column(c,'gear_orders','shipping_address','shipping_address TEXT')
    # v0.32 C 端首页运营轮播：俱乐部后台自定义首页顶部 banner（自定义图 / 轮播间隔 /
    # 跳转活动或商品）。可空 → 存量库老俱乐部直接回落默认活动轮播，无需回填。
    _ensure_column(c,'clubs','home_banners_json','home_banners_json TEXT')
    # v0.30 商品目录（多品牌 / 服饰标准模式）：品牌与品类是独立字典，商品只存外键。
    # 人群（gender）默认 unisex 而不是 NULL：登山杖、背包这类根本不分男女的装备
    # 不该为了必填被硬塞一个性别，而 NULL 会让「按人群筛选」多出一个说不清的第四态。
    _ensure_column(c,'products','brand_id','brand_id INTEGER')
    _ensure_column(c,'products','category_id','category_id INTEGER')
    _ensure_column(c,'products','gender',"gender TEXT NOT NULL DEFAULT 'unisex'")
    _ensure_column(c,'products','season','season TEXT')
    _ensure_column(c,'products','series','series TEXT')
    _ensure_column(c,'products','material','material TEXT')
    # 仓库（2026-10-26 用户反馈）：这批货存在哪个仓。纯台账文本，不做仓库主数据 ——
    # 平台现阶段就一个自建仓 + 代发，先让运营能把「放哪儿」记下来。
    _ensure_column(c,'products','warehouse','warehouse TEXT')
    # 头图（cover_url）：商品的主视觉，C 端卡片 / 详情页首图都优先用它；
    # 与 product_images 图集解耦 —— 头图不一定是图集里的某一张，运营可单独设。
    # 可空 + 无回填，幂等（_ensure_column 已判列存在）。
    _ensure_column(c,'products','cover_url','cover_url TEXT')
    # PEAKTRACE 合成图头图裁切（2026-11-04）：官网商品图是「多视图合成图」（正面/背面/
    # 细节拼一张），catalog_view=[srcW,srcH,cropX,cropY,cropW,cropH] 描述头图在合成图里的
    # 裁切区。同步时原样落库（JSON 数组字符串，可空），前端按 SVG viewBox 只露裁切区，
    # 复刻官网渲染（见 shared.js prodImgHtml）。可空 + 无回填，幂等。
    _ensure_column(c,'products','catalog_view','catalog_view TEXT')
    # 颜色 × 尺码：规格从「一维的 name」升级成「颜色 + 尺码两个维度」，
    # 2 色 × 3 码要生成 6 个 SKU，而不是 6 个混在一维里的名字。
    # 存量规格的颜色尺码留空（不猜），C 端按老的一维 name 显示，功能不受影响。
    _ensure_column(c,'product_variants','color','color TEXT')
    _ensure_column(c,'product_variants','color_hex','color_hex TEXT')
    _ensure_column(c,'product_variants','size','size TEXT')
    _ensure_column(c,'product_variants','barcode','barcode TEXT')
    # 进销存 V1（2026-10-31）：规格层补采购成本 / 规格属性 / 库存与履约模式
    _ensure_column(c,'product_variants','spec_json','spec_json TEXT')
    _ensure_column(c,'product_variants','purchase_cost','purchase_cost REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'product_variants','inventory_mode',"inventory_mode TEXT NOT NULL DEFAULT 'platform_owned'")
    _ensure_column(c,'product_variants','fulfillment_mode',"fulfillment_mode TEXT NOT NULL DEFAULT 'platform_warehouse'")
    # 商品来源与库存/履约模式（外部品牌扩展地基，不写死 PEAKTRACE）
    _ensure_column(c,'products','source',"source TEXT NOT NULL DEFAULT 'platform_self'")
    _ensure_column(c,'products','inventory_mode',"inventory_mode TEXT NOT NULL DEFAULT 'platform_owned'")
    _ensure_column(c,'products','fulfillment_mode',"fulfillment_mode TEXT NOT NULL DEFAULT 'platform_warehouse'")
    _ensure_column(c,'products','owner_id','owner_id INTEGER')
    # 订单明细：混合订单分流预埋（V1 全默认自营）
    _ensure_column(c,'gear_order_items','sku_id','sku_id INTEGER')
    _ensure_column(c,'gear_order_items','supplier_id','supplier_id INTEGER')
    _ensure_column(c,'gear_order_items','inventory_mode',"inventory_mode TEXT NOT NULL DEFAULT 'platform_owned'")
    _ensure_column(c,'gear_order_items','fulfillment_mode',"fulfillment_mode TEXT NOT NULL DEFAULT 'platform_warehouse'")
    _ensure_column(c,'gear_order_items','warehouse_id','warehouse_id INTEGER')
    # WMS：在途 / 不可售数量（5.2 库存数量关系）
    _ensure_column(c,'warehouse_inventory','on_order','on_order INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'warehouse_inventory','unsellable','unsellable INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'gear_orders','commerce_sync_error','commerce_sync_error TEXT')
    _ensure_column(c,'warehouse_movements','variant_id','variant_id INTEGER')
    _ensure_column(c,'purchase_order_exceptions','variant_id','variant_id INTEGER')
    _ensure_column(c,'purchase_order_exceptions','resolution',"resolution TEXT NOT NULL DEFAULT 'closed'")
    _ensure_column(c,'purchase_order_items','variant_id','variant_id INTEGER')
    _ensure_column(c,'purchase_order_items','sku_plan_json','sku_plan_json TEXT')
    _ensure_column(c,'purchase_receipt_items','variant_id','variant_id INTEGER')
    _ensure_column(c,'purchase_receipts','request_key','request_key TEXT')
    _ensure_column(c,'purchase_receipts','request_hash','request_hash TEXT')
    # 采购单「确认下单」操作人留痕（2026-11-02：单据需留存采购/审核/确认/入库各环节操作人）。
    _ensure_column(c,'purchase_orders','ordered_by','ordered_by TEXT')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_receipt_request ON purchase_receipts(request_key)')
    _ensure_column(c,'purchase_return_items','variant_id','variant_id INTEGER')
    # 品牌档案补全 + 来源 / 商城展示状态
    _ensure_column(c,'product_brands','name_en','name_en TEXT')
    _ensure_column(c,'product_brands','intro','intro TEXT')
    _ensure_column(c,'product_brands','industry','industry TEXT')
    _ensure_column(c,'product_brands','official_website','official_website TEXT')
    _ensure_column(c,'product_brands','country','country TEXT')
    _ensure_column(c,'product_brands','contact_name','contact_name TEXT')
    _ensure_column(c,'product_brands','contact_phone','contact_phone TEXT')
    _ensure_column(c,'product_brands','cooperation_status',"cooperation_status TEXT NOT NULL DEFAULT 'pending'")
    _ensure_column(c,'product_brands','source_type',"source_type TEXT NOT NULL DEFAULT 'platform_self'")
    _ensure_column(c,'product_brands','mall_display_status',"mall_display_status TEXT NOT NULL DEFAULT 'show'")
    # 进销存 V1.2（2026-10-31 第二批）：品牌结算模式配置（按销售额比例 / 固定结算价）
    #   sales_ratio：品牌应结 = 销售额 × settlement_ratio%（0 = 沿用结算单生成时的 platformFeeRate 口径）
    #   fixed_price：品牌应结 = Σ(件数 × 规格 settlement_price)，逐 SKU 在规格里定价
    _ensure_column(c,'product_brands','settlement_mode',"settlement_mode TEXT NOT NULL DEFAULT 'sales_ratio'")
    _ensure_column(c,'product_brands','settlement_ratio','settlement_ratio REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'product_variants','settlement_price','settlement_price REAL NOT NULL DEFAULT 0')
    # 商品状态兼容（V1.2）：历史库里可能存着旧状态机的别名值，统一归一到新 7 态。
    # 只映射确认的别名，未知值保留原样（前端按原文显示，不猜）。
    c.execute("UPDATE products SET status='active' WHERE status IN ('on_sale','online','published','in_stock','selling')")
    c.execute("UPDATE products SET status='inactive' WHERE status IN ('off','offline','unlisted','delisted','removed','off_shelf')")
    c.execute("UPDATE products SET status='pending_inbound' WHERE status IN ('incoming','inbound_pending','to_inbound')")
    c.execute("UPDATE products SET status='partially_received' WHERE status IN ('partial','partial_inbound')")
    # 进销存 V1 新表：供应商↔品牌关联 / 品牌结算 / 外部品牌通用映射 / 外部库存同步日志
    c.execute('''CREATE TABLE IF NOT EXISTS brand_suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, brand_id INTEGER NOT NULL, supplier_id INTEGER NOT NULL,
        is_primary INTEGER NOT NULL DEFAULT 0, note TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(brand_id,supplier_id),
        FOREIGN KEY(brand_id) REFERENCES product_brands(id) ON DELETE CASCADE,
        FOREIGN KEY(supplier_id) REFERENCES suppliers(id) ON DELETE CASCADE)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_brand_suppliers_brand ON brand_suppliers(brand_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_brand_suppliers_supplier ON brand_suppliers(supplier_id)')
    c.execute('''CREATE TABLE IF NOT EXISTS brand_settlements (
        id INTEGER PRIMARY KEY AUTOINCREMENT, brand_id INTEGER NOT NULL, settlement_no TEXT,
        period_from TEXT, period_to TEXT, sales_amount REAL NOT NULL DEFAULT 0,
        platform_fee REAL NOT NULL DEFAULT 0, settlement_amount REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'draft', settled_at TEXT, note TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(brand_id) REFERENCES product_brands(id))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_brand_settlements_brand ON brand_settlements(brand_id,status,id)')
    c.execute('''CREATE TABLE IF NOT EXISTS external_product_mappings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, brand_id INTEGER NOT NULL,
        external_product_id TEXT, external_sku_id TEXT, external_inventory_id TEXT,
        product_id INTEGER, variant_id INTEGER, external_sync_status TEXT NOT NULL DEFAULT 'pending',
        last_inventory_sync_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(brand_id,external_product_id,external_sku_id),
        FOREIGN KEY(brand_id) REFERENCES product_brands(id) ON DELETE CASCADE,
        FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL,
        FOREIGN KEY(variant_id) REFERENCES product_variants(id) ON DELETE SET NULL)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_external_mappings_brand ON external_product_mappings(brand_id)')
    c.execute('''CREATE TABLE IF NOT EXISTS external_inventory_sync_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mapping_id INTEGER NOT NULL,
        synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, result TEXT NOT NULL DEFAULT 'ok',
        snapshot_json TEXT, error TEXT,
        FOREIGN KEY(mapping_id) REFERENCES external_product_mappings(id) ON DELETE CASCADE)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_external_sync_logs_mapping ON external_inventory_sync_logs(mapping_id,synced_at)')
    # 外部履约桥（2026-11-06）：PEAKTRACE 等外部品牌「代发货」订单的桥接表。
    # 镜像方案（peaktrace_catalog）只同步了目录与库存，订单层此前零集成：顾客买了
    # PEAKTRACE 商品后两边各扣各库存、没人发货。本表把需要供应商发货的订单挂起来：
    # 支付成功建记录（pending）→ 自动推上游（pushed）→ 上游发货回传（shipped/delivered）。
    # 一个订单只有一条履约记录（UNIQUE order_id），幂等由 ON CONFLICT 保证。
    c.execute('''CREATE TABLE IF NOT EXISTS external_fulfillment_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL UNIQUE,
        brand_id INTEGER,
        external_order_id TEXT,
        push_status TEXT NOT NULL DEFAULT 'pending',
        push_attempts INTEGER NOT NULL DEFAULT 0,
        push_error TEXT,
        pushed_at TEXT,
        fulfill_status TEXT NOT NULL DEFAULT 'awaiting_shipment',
        carrier TEXT, tracking_no TEXT,
        payload_json TEXT,
        last_pull_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(order_id) REFERENCES gear_orders(id) ON DELETE CASCADE)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_effo_push ON external_fulfillment_orders(push_status,fulfill_status,id)')
    # 结算清单需要供应商「妥投时间」（2026-11-06）：单独记账，避免与订单确认收货时间混淆。
    _ensure_column(c, 'external_fulfillment_orders', 'fulfilled_at', 'fulfilled_at TEXT')
    # 售后单 ↔ PEAKTRACE 退换货同步状态（#618）
    _ensure_column(c, 'after_sales_cases', 'external_sync_status', 'external_sync_status TEXT')
    _ensure_column(c, 'after_sales_cases', 'external_sync_error', 'external_sync_error TEXT')
    # 进销存 V1.1（2026-10-31）：采购拒收/异常登记（任务书 4.2③）。
    # 未到货量公式 = ordered - received - rejected：异常量不参与未到货，采购单才能正常收完。
    c.execute('''CREATE TABLE IF NOT EXISTS purchase_order_exceptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, purchase_order_id TEXT NOT NULL,
        purchase_order_item_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
        exception_type TEXT NOT NULL, quantity INTEGER NOT NULL,
        note TEXT, registered_by TEXT NOT NULL DEFAULT 'platform',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(purchase_order_item_id) REFERENCES purchase_order_items(id),
        FOREIGN KEY(product_id) REFERENCES products(id))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_po_exceptions_po ON purchase_order_exceptions(purchase_order_id,purchase_order_item_id)')
    # 老商品规格回填（2026-10-31，进销存 V1）：库存真源已下沉到规格层，
    # 但历史种子/老库存在「商品有 stock、无任何规格行」的数据 —— 汇总重算一跑
    # 就会把商品库存抹成 0，双账本自检也永远不一致。这里为无规格商品补一条
    # 默认规格（继承商品价格与库存），回填后商品汇总与规格之和自然对齐。
    c.execute('''INSERT INTO product_variants(product_id,name,sku,price,stock,status,sort,is_default)
                 SELECT p.id,p.name,p.sku,p.price,p.stock,'active',0,1 FROM products p
                 WHERE NOT EXISTS(SELECT 1 FROM product_variants v WHERE v.product_id=p.id)''')
    c.execute('''UPDATE products SET stock=(
                     SELECT COALESCE(SUM(v.stock),0) FROM product_variants v
                     WHERE v.product_id=products.id AND v.status='active')''')
    # 物流履约（2026-10-24）：运单与轨迹分两张表。
    # 为什么独立于点单上的 carrier/tracking_no 两列：那两列只能存「最后一次填的值」，
    # 换承运商、补录轨迹、服务商回调都会把历史抹掉。运单表按住 order_id 唯一，
    # 轨迹表按 (shipment_id,event_time,description) 唯一做幂等 —— 服务商重复推送
    # 同一条轨迹不会写出两行（这是接真实 API 时最常见的脏数据来源）。
    c.execute('''CREATE TABLE IF NOT EXISTS logistics_shipments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL UNIQUE,
        provider TEXT NOT NULL DEFAULT 'mock',
        carrier TEXT NOT NULL DEFAULT '',
        carrier_code TEXT NOT NULL DEFAULT '',
        tracking_no TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'in_transit',
        shipped_at TEXT,
        delivered_at TEXT,
        last_sync_at TEXT,
        raw_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_logistics_shipments_tracking ON logistics_shipments(tracking_no)')
    c.execute('''CREATE TABLE IF NOT EXISTS logistics_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shipment_id INTEGER NOT NULL,
        event_time TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        location TEXT NOT NULL DEFAULT '',
        raw_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(shipment_id,event_time,description)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_logistics_events_shipment ON logistics_events(shipment_id,event_time DESC,id DESC)')
    c.execute('''CREATE TABLE IF NOT EXISTS user_addresses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        consignee TEXT NOT NULL,
        phone TEXT NOT NULL,
        province TEXT NOT NULL DEFAULT '',
        city TEXT NOT NULL DEFAULT '',
        district TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        is_default INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_user_addresses_user ON user_addresses(user_id,is_default DESC,id DESC)')
    # v0.27 库存下沉到规格：流水与订单明细要能指到具体规格。
    # 都可空 —— 无规格商品（以及 v0.27 之前的历史流水）variant_id 为 NULL 仍然合法。
    _ensure_column(c,'inventory_movements','variant_id','variant_id INTEGER')
    _ensure_column(c,'gear_order_items','variant_id','variant_id INTEGER')
    # 成交时佣金率快照（2026-10-24）：佣金率按俱乐部设定后会变动，
    # 退款冲回必须用成交当时的费率。历史订单用商品当前费率回填一次（近似值，
    # 因为历史数据本来就没有独立快照），之后新订单一律写真实费率。
    _ensure_column(c,'gear_order_items','commission_rate_snapshot','commission_rate_snapshot REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'gear_order_items','variant_name','variant_name TEXT')
    c.execute('''CREATE TABLE IF NOT EXISTS after_sales_cases (
      id TEXT PRIMARY KEY, order_id INTEGER NOT NULL, user_id INTEGER NOT NULL, source_club_id INTEGER NOT NULL,
      case_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending_review', reason TEXT, evidence_json TEXT NOT NULL DEFAULT '[]',
      note TEXT, decision_note TEXT, requested_goods_amount REAL NOT NULL DEFAULT 0, requested_refund_amount REAL NOT NULL DEFAULT 0,
      approved_refund_amount REAL NOT NULL DEFAULT 0, refund_request_id TEXT, reviewed_by TEXT, reviewed_at TEXT, return_carrier TEXT,
      return_tracking_no TEXT, returned_at TEXT, received_at TEXT, exchange_carrier TEXT, exchange_tracking_no TEXT, exchange_shipped_at TEXT,
      completed_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS after_sales_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, order_item_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
      requested_amount REAL NOT NULL DEFAULT 0, restocked INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_after_sales_order ON after_sales_cases(order_id,created_at DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_after_sales_user ON after_sales_cases(user_id,created_at DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_after_sales_status ON after_sales_cases(status,created_at DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_after_sales_items_case ON after_sales_items(case_id)')
    _ensure_column(c,'commission_ledger','ledger_type',"ledger_type TEXT NOT NULL DEFAULT 'earn'")
    _ensure_column(c,'commission_ledger','note','note TEXT')
    _ensure_column(c,'commission_ledger','frozen_at','frozen_at TEXT')
    _ensure_column(c,'commission_ledger','available_at','available_at TEXT')
    _ensure_column(c,'commission_ledger','settlement_id','settlement_id TEXT')
    _ensure_column(c,'commission_ledger','settled_at','settled_at TEXT')
    c.execute('''CREATE TABLE IF NOT EXISTS commission_settlements (
      id TEXT PRIMARY KEY,
      club_id INTEGER NOT NULL,
      gross_amount REAL NOT NULL DEFAULT 0,
      deduction_amount REAL NOT NULL DEFAULT 0,
      net_amount REAL NOT NULL DEFAULT 0,
      entry_count INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'paid',
      payment_ref TEXT NOT NULL UNIQUE,
      note TEXT,
      created_by TEXT NOT NULL DEFAULT 'platform',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      paid_at TEXT,
      FOREIGN KEY (club_id) REFERENCES clubs(id)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_commission_ledger_club_status ON commission_ledger(club_id,status,created_at)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_commission_ledger_order ON commission_ledger(order_id,ledger_type)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_commission_settlements_club ON commission_settlements(club_id,created_at DESC)')
    # 俱乐部佣金结算账户（法人卡/对公账户，2026-10-22 用户反馈）：平台结算佣金的前置条件，
    # 没提供账户就不能结算（app.py platform_create_settlement 强校验）。
    c.execute('''CREATE TABLE IF NOT EXISTS club_settlement_accounts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      club_id INTEGER NOT NULL UNIQUE,
      account_type TEXT NOT NULL DEFAULT 'legal',
      holder TEXT NOT NULL,
      legal_name TEXT NOT NULL,
      legal_id_no TEXT NOT NULL,
      bank_name TEXT NOT NULL,
      bank_branch TEXT,
      card_no TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'active',
      note TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (club_id) REFERENCES clubs(id)
    )''')
    # v0.17 normalize legacy pre-settlement refunds: v0.16 wrote an audit reversal row but
    # left the original earn row pending. There were no settlements before v0.17, so these
    # refunded earns are safe to mark reversed during compatibility migration.
    c.execute("""UPDATE commission_ledger SET status='reversed'
                 WHERE ledger_type='earn' AND status IN ('pending','frozen','available')
                   AND order_id IN (SELECT id FROM gear_orders WHERE status='refunded')
                   AND EXISTS (SELECT 1 FROM commission_ledger r WHERE r.order_id=commission_ledger.order_id AND r.ledger_type='refund_reverse')""")
    _ensure_column(c,'checkout_intents','result_id','result_id TEXT')
    _ensure_column(c,'checkout_intents','points_policy_snapshot_json','points_policy_snapshot_json TEXT')
    _ensure_column(c,'checkout_intents','club_benefit_discount','club_benefit_discount REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'checkout_intents','platform_benefit_subsidy','platform_benefit_subsidy REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'checkout_intents','benefit_redemption_ids_json',"benefit_redemption_ids_json TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(c,'checkout_intents','payment_status',"payment_status TEXT NOT NULL DEFAULT 'pending'")
    _ensure_column(c,'checkout_intents','refund_status',"refund_status TEXT NOT NULL DEFAULT 'none'")
    _ensure_column(c,'checkout_intents','paid_at','paid_at TEXT')
    _ensure_column(c,'checkout_intents','cancelled_at','cancelled_at TEXT')
    _ensure_column(c,'checkout_intents','refunded_at','refunded_at TEXT')
    _ensure_column(c,'checkout_intents','participant_count','participant_count INTEGER NOT NULL DEFAULT 1')
    _ensure_column(c,'checkout_intents','participant_policy_snapshot_json','participant_policy_snapshot_json TEXT')
    _ensure_column(c,'checkout_intents','result_json','result_json TEXT')
    _ensure_column(c,'registrations','payment_status',"payment_status TEXT NOT NULL DEFAULT 'succeeded'")
    _ensure_column(c,'registrations','refund_status',"refund_status TEXT NOT NULL DEFAULT 'none'")
    _ensure_column(c,'registrations','refund_request_id','refund_request_id TEXT')
    _ensure_column(c,'registrations','refund_cash_amount','refund_cash_amount REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'registrations','refund_percent','refund_percent REAL')
    _ensure_column(c,'registrations','retained_cash_amount','retained_cash_amount REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'registrations','refund_policy_snapshot_json','refund_policy_snapshot_json TEXT')
    _ensure_column(c,'registrations','refunded_at','refunded_at TEXT')
    _ensure_column(c,'registrations','participant_count','participant_count INTEGER NOT NULL DEFAULT 1')
    _ensure_column(c,'registrations','participant_policy_snapshot_json','participant_policy_snapshot_json TEXT')
    _ensure_column(c,'registrations','partial_refund_cash_total','partial_refund_cash_total REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'registrations','partial_refund_retained_cash_total','partial_refund_retained_cash_total REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'registrations','partial_refund_count','partial_refund_count INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'registration_participants','refund_status',"refund_status TEXT NOT NULL DEFAULT 'none'")
    _ensure_column(c,'registration_participants','refund_request_id','refund_request_id TEXT')
    _ensure_column(c,'registration_participants','refunded_at','refunded_at TEXT')
    _ensure_column(c,'gear_orders','payment_status',"payment_status TEXT NOT NULL DEFAULT 'succeeded'")
    _ensure_column(c,'gear_orders','refund_status',"refund_status TEXT NOT NULL DEFAULT 'none'")
    _ensure_column(c,'gear_orders','refund_request_id','refund_request_id TEXT')
    _ensure_column(c,'benefit_redemptions','held_checkout_id','held_checkout_id TEXT')
    _ensure_column(c,'benefit_redemptions','used_order_kind','used_order_kind TEXT')
    _ensure_column(c,'benefit_redemptions','used_order_id','used_order_id TEXT')
    _ensure_column(c,'refund_requests','original_cash_amount','original_cash_amount REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'refund_requests','refund_percent','refund_percent REAL')
    _ensure_column(c,'refund_requests','retained_cash_amount','retained_cash_amount REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'refund_requests','policy_snapshot_json','policy_snapshot_json TEXT')
    _ensure_column(c,'refund_requests','policy_label','policy_label TEXT')
    _ensure_column(c,'refund_requests','participant_id','participant_id INTEGER')
    _ensure_column(c,'refund_requests','refund_scope',"refund_scope TEXT NOT NULL DEFAULT 'full'")
    _ensure_column(c,'refund_requests','allocated_original_amount','allocated_original_amount REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'refund_requests','allocated_club_points','allocated_club_points INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'refund_requests','allocated_gear_points','allocated_gear_points INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'refund_requests','allocated_club_point_discount','allocated_club_point_discount REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'refund_requests','allocated_platform_point_subsidy','allocated_platform_point_subsidy REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'refund_requests','allocated_club_benefit_discount','allocated_club_benefit_discount REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'refund_requests','allocated_platform_benefit_subsidy','allocated_platform_benefit_subsidy REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'refund_requests','allocated_club_points_earned','allocated_club_points_earned INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'activity_occurrences','execution_status',"execution_status TEXT NOT NULL DEFAULT 'preparing'")
    _ensure_column(c,'activity_occurrences','execution_updated_at','execution_updated_at TEXT')
    _ensure_column(c,'payment_attempts','payment_account_id','payment_account_id INTEGER')
    _ensure_column(c,'payment_attempts','channel','channel TEXT')
    _ensure_column(c,'payment_attempts','merchant_order_no','merchant_order_no TEXT')
    _ensure_column(c,'payment_attempts','payment_action_json',"payment_action_json TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(c,'refund_requests','payment_account_id','payment_account_id INTEGER')
    _ensure_column(c,'refund_requests','provider_refund_no','provider_refund_no TEXT')
    _ensure_column(c,'refund_requests','provider_status','provider_status TEXT')
    _ensure_column(c,'refund_requests','provider_payload_json',"provider_payload_json TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(c,'refund_requests','commerce_refund_status',"commerce_refund_status TEXT NOT NULL DEFAULT 'not_required'")
    _ensure_column(c,'refund_requests','commerce_refund_id','commerce_refund_id TEXT')
    _ensure_column(c,'refund_requests','commerce_refund_error','commerce_refund_error TEXT')
    # 2026-10-26 会员开通方式（购买/条件）+ 券折扣形态：补列必须排在任何会写这些列的
    # INSERT（种子等级、建券、建规则）之前 —— 存量库启动才不会撞 OperationalError。
    _ensure_column(c,'club_member_tiers','access_mode',"access_mode TEXT NOT NULL DEFAULT 'CONDITION'")
    _ensure_column(c,'club_member_tiers','price','price REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'club_member_tiers','valid_months','valid_months INTEGER NOT NULL DEFAULT 12')
    _ensure_column(c,'member_benefits','discount_percent','discount_percent REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'clubs','membership_purchase_enabled','membership_purchase_enabled INTEGER NOT NULL DEFAULT 0')
    # 2026-10-26 第二批（会员折扣/升级赠券/拉新/券面自定义）：同样必须排在种子 INSERT 之前。
    _ensure_column(c,'club_member_tiers','activity_discount_percent','activity_discount_percent REAL NOT NULL DEFAULT 0')
    # 2026-11-05 会员等级专属积分现金换算比例：0 = 跟随俱乐部生效值，正数 = 等级专属。
    # 存量等级全部落 0，等价于「升级前所有等级都用同一个换算率」，行为零变化。
    _ensure_column(c,'club_member_tiers','points_redeem_rate','points_redeem_rate INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'activities','member_discount_enabled','member_discount_enabled INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'member_benefits','face_json','face_json TEXT')
    _ensure_column(c,'clubs','referral_coupon_benefit_id','referral_coupon_benefit_id INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'club_members','invite_code','invite_code TEXT')
    _ensure_column(c,'club_members','referred_by','referred_by INTEGER')
    _ensure_column(c,'checkout_intents','member_discount','member_discount REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'checkout_intents','member_discount_rate','member_discount_rate REAL NOT NULL DEFAULT 0')
    c.execute('''CREATE TABLE IF NOT EXISTS tier_gift_rules (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      club_id INTEGER NOT NULL,
      tier_id INTEGER NOT NULL,
      benefit_id INTEGER NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(club_id) REFERENCES clubs(id),
      FOREIGN KEY(tier_id) REFERENCES club_member_tiers(id),
      FOREIGN KEY(benefit_id) REFERENCES member_benefits(id))''')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_tier_gift_rules_tier ON tier_gift_rules(club_id,tier_id)')
    c.execute('''CREATE TABLE IF NOT EXISTS tier_gift_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      club_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL,
      tier_id INTEGER NOT NULL,
      benefit_id INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(club_id) REFERENCES clubs(id),
      FOREIGN KEY(user_id) REFERENCES users(id))''')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_tier_gift_log_unique ON tier_gift_log(user_id,tier_id)')
    # 2026-10-27 升级奖励 v2（用户反馈「达到等级条件之后可以获得优惠券或某种奖励，也可以赠送积分」）：
    # 老表 tier_gift_rules.benefit_id 是 NOT NULL + 外键，纯积分奖励塞不进去；SQLite 改不了约束，
    # 故新建 tier_reward_rules（benefit_id 可空 + points_award）并把老数据一次性搬过来（幂等）。
    # 老表 tier_gift_rules / tier_gift_log 从此冻结只读，真身是 tier_reward_*。
    c.execute('''CREATE TABLE IF NOT EXISTS tier_reward_rules (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      club_id INTEGER NOT NULL,
      tier_id INTEGER NOT NULL,
      benefit_id INTEGER,
      points_award INTEGER NOT NULL DEFAULT 0,
      enabled INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(club_id) REFERENCES clubs(id),
      FOREIGN KEY(tier_id) REFERENCES club_member_tiers(id),
      FOREIGN KEY(benefit_id) REFERENCES member_benefits(id))''')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_tier_reward_rules_tier ON tier_reward_rules(club_id,tier_id)')
    c.execute('''CREATE TABLE IF NOT EXISTS tier_reward_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      club_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL,
      tier_id INTEGER NOT NULL,
      benefit_id INTEGER,
      points_award INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(user_id,tier_id))''')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_tier_reward_log_unique ON tier_reward_log(user_id,tier_id)')
    c.execute('''INSERT OR IGNORE INTO tier_reward_rules(club_id,tier_id,benefit_id,points_award,enabled)
                 SELECT club_id,tier_id,benefit_id,0,enabled FROM tier_gift_rules''')
    c.execute('''INSERT OR IGNORE INTO tier_reward_log(club_id,user_id,tier_id,benefit_id,points_award)
                 SELECT club_id,user_id,tier_id,benefit_id,0 FROM tier_gift_log''')
    # 2026-10-27 C 端领队卡「介绍得更丰富一点」+ 可咨询 / 可评分（用户要求）：
    # 卡面要多出「资质 / 常驻城市 · N 年经验 / ★评分」，点开还能看简介、TA 带的近期活动、
    # 顾客评价，并直接留咨询。三列为空/0 时前端不印，不做任何猜测性填充。
    _ensure_column(c,'club_leaders','bio','bio TEXT')
    _ensure_column(c,'club_leaders','years_exp','years_exp INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'club_leaders','cert_verified','cert_verified INTEGER NOT NULL DEFAULT 0')
    # 评价与咨询两张表：rater_key 做「一人一评」去重（会员用 u:<id>，游客用本机随机键）。
    # 全部 CREATE TABLE IF NOT EXISTS，老库首次启动即建好，不需要回填。
    c.execute('''CREATE TABLE IF NOT EXISTS leader_reviews (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      club_id INTEGER NOT NULL,
      leader_id INTEGER NOT NULL,
      activity_id INTEGER,
      user_id INTEGER,
      rater_key TEXT NOT NULL,
      author TEXT,
      rating INTEGER NOT NULL,
      comment TEXT,
      status TEXT NOT NULL DEFAULT 'visible',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(club_id) REFERENCES clubs(id),
      FOREIGN KEY(leader_id) REFERENCES club_leaders(id))''')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_leader_reviews_once ON leader_reviews(club_id,leader_id,rater_key)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_leader_reviews_leader ON leader_reviews(leader_id,status)')
    c.execute('''CREATE TABLE IF NOT EXISTS leader_inquiries (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      club_id INTEGER NOT NULL,
      leader_id INTEGER,
      activity_id INTEGER,
      name TEXT,
      phone TEXT NOT NULL,
      message TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'new',
      source TEXT NOT NULL DEFAULT 'web',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      handled_at TEXT,
      FOREIGN KEY(club_id) REFERENCES clubs(id))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_leader_inquiries_club ON leader_inquiries(club_id,status,id)')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_club_members_invite_code ON club_members(invite_code) WHERE invite_code IS NOT NULL')
    c.execute('CREATE INDEX IF NOT EXISTS idx_benefit_redemptions_checkout ON benefit_redemptions(held_checkout_id,status)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_benefit_redemptions_order ON benefit_redemptions(used_order_kind,used_order_id,status)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_participants_refund ON registration_participants(registration_id,refund_status)')
    c.execute('''CREATE TABLE IF NOT EXISTS participant_financial_allocations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      registration_id INTEGER NOT NULL,
      participant_id INTEGER NOT NULL UNIQUE,
      original_amount REAL NOT NULL DEFAULT 0,
      cash_paid REAL NOT NULL DEFAULT 0,
      club_points_used INTEGER NOT NULL DEFAULT 0,
      club_point_discount REAL NOT NULL DEFAULT 0,
      gear_points_used INTEGER NOT NULL DEFAULT 0,
      platform_point_subsidy REAL NOT NULL DEFAULT 0,
      club_benefit_discount REAL NOT NULL DEFAULT 0,
      platform_benefit_subsidy REAL NOT NULL DEFAULT 0,
      club_points_earned INTEGER NOT NULL DEFAULT 0,
      refund_cash_amount REAL NOT NULL DEFAULT 0,
      retained_cash_amount REAL NOT NULL DEFAULT 0,
      refunded_at TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(registration_id) REFERENCES registrations(id),
      FOREIGN KEY(participant_id) REFERENCES registration_participants(id)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_participant_alloc_registration ON participant_financial_allocations(registration_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_refund_requests_participant ON refund_requests(participant_id,status)')
    # v0.12 attendee model: backfill one participant for legacy activity orders so old data remains operable.
    legacy=c.execute('''SELECT r.id,r.activity_id,r.occurrence_id,r.club_id,r.user_id,u.name,u.phone
        FROM registrations r JOIN users u ON u.id=r.user_id
        WHERE NOT EXISTS (SELECT 1 FROM registration_participants p WHERE p.registration_id=r.id)''').fetchall()
    for r in legacy:
        if not r['occurrence_id']:
            continue
        c.execute('''INSERT INTO registration_participants(
            registration_id,activity_id,occurrence_id,club_id,payer_user_id,linked_user_id,name,phone,relation_to_payer,
            form_status,insurance_status,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (r['id'],r['activity_id'],r['occurrence_id'],r['club_id'],r['user_id'],r['user_id'],r['name'],r['phone'],'本人','incomplete','pending','active'))
    # v0.14 backfill deterministic per-participant financial allocations for legacy paid registrations.
    def split_int(total,n):
        total=max(0,int(total or 0)); n=max(1,int(n)); q,r=divmod(total,n); return [q+(1 if i<r else 0) for i in range(n)]
    def split_money(total,n):
        cents=max(0,int(round(float(total or 0)*100))); vals=split_int(cents,n); return [v/100 for v in vals]
    regs=c.execute('''SELECT r.* FROM registrations r WHERE EXISTS (SELECT 1 FROM registration_participants p WHERE p.registration_id=r.id)
                      AND NOT EXISTS (SELECT 1 FROM participant_financial_allocations a WHERE a.registration_id=r.id)''').fetchall()
    for rr in regs:
        rd=dict(rr); ps=c.execute('SELECT id FROM registration_participants WHERE registration_id=? ORDER BY id',(rd['id'],)).fetchall(); n=len(ps)
        if not n: continue
        cp=c.execute('SELECT COALESCE(SUM(points_used),0) FROM point_redemptions WHERE order_kind="activity" AND order_id=? AND point_type="club"',(rd['id'],)).fetchone()[0]
        gp=c.execute('SELECT COALESCE(SUM(points_used),0) FROM point_redemptions WHERE order_kind="activity" AND order_id=? AND point_type="gear"',(rd['id'],)).fetchone()[0]
        earned=c.execute('SELECT COALESCE(SUM(amount),0) FROM club_point_ledger WHERE source_type="activity_registration" AND source_id=? AND type="earn"',(str(rd['id']),)).fetchone()[0]
        parts={
            'original':split_money(rd.get('original_amount'),n),'cash':split_money(rd.get('amount'),n),
            'cp':split_int(cp,n),'cpd':split_money(rd.get('club_point_discount'),n),
            'gp':split_int(gp,n),'gps':split_money(rd.get('platform_point_subsidy'),n),
            'cbd':split_money(rd.get('club_benefit_discount'),n),'pbs':split_money(rd.get('platform_benefit_subsidy'),n),
            'earned':split_int(earned,n),
        }
        for i,pr in enumerate(ps):
            c.execute('''INSERT OR IGNORE INTO participant_financial_allocations(
              registration_id,participant_id,original_amount,cash_paid,club_points_used,club_point_discount,
              gear_points_used,platform_point_subsidy,club_benefit_discount,platform_benefit_subsidy,club_points_earned)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(rd['id'],pr['id'],parts['original'][i],parts['cash'][i],parts['cp'][i],parts['cpd'][i],parts['gp'][i],parts['gps'][i],parts['cbd'][i],parts['pbs'][i],parts['earned'][i]))
    c.execute('INSERT OR IGNORE INTO platform_settings(key,value) VALUES(?,?)',('gear_points_activity_redeem_enabled','1'))
    c.execute('INSERT OR IGNORE INTO platform_settings(key,value) VALUES(?,?)',('commission_after_sales_days','7'))
    c.execute('INSERT OR IGNORE INTO platform_settings(key,value) VALUES(?,?)',('commission_enabled','1'))
    # 活动详情的「重新生成 / 换一版」需要两样东西：生成时的原始资料（没有它就无法重做），
    # 以及当前指向的版本（用于回退）。原始资料只存库内，任何对外接口都必须 pop 掉。
    _ensure_column(c,'activities','source_json','source_json TEXT')
    _ensure_column(c,'activities','detail_version_id','detail_version_id INTEGER')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS activity_detail_versions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      club_id INTEGER NOT NULL,
      activity_id INTEGER NOT NULL,
      version_no INTEGER NOT NULL,
      origin TEXT NOT NULL DEFAULT 'ai-generate',
      direction TEXT,
      facts_refreshed INTEGER NOT NULL DEFAULT 0,
      narrative TEXT,
      outline TEXT,
      detail_json TEXT NOT NULL,
      master_json TEXT,
      credits_charged INTEGER NOT NULL DEFAULT 0,
      gateway_mode TEXT,
      gateway_model TEXT,
      status TEXT NOT NULL DEFAULT 'ready',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (activity_id) REFERENCES activities(id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_detail_versions_no ON activity_detail_versions(activity_id,version_no);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_detail_pending ON activity_detail_versions(activity_id) WHERE status='pending';
    ''')
    _ensure_column(c,'activity_detail_versions','status',"status TEXT NOT NULL DEFAULT 'ready'")
    # v0.27：领队资源库与团期领队的关联列。
    # ★ club_member_tiers.gear_discount（装备折扣）已于 2026-10-23 弃用：
    #   折扣改由 ai_credit_plans.gear_discount（俱乐部订阅套餐）决定，等级列保留但不再读写。
    _ensure_column(c,'occurrence_leaders','leader_id','leader_id INTEGER')
    _backfill_detail_versions(c)
    _backfill_v019_opening_inventory(c)
    _backfill_v020_warehouse(c)
    _backfill_v021_finance(c)
    _backfill_v023_ai_credits(c)
    _backfill_plan_gear_discount(c)  # 必须在套餐 seed 之后：给已有套餐按档位回填装备折扣
    _backfill_media_urls(c)
    # v0.28 保险自动化：参加人参保/退保时间窗口 + 保费 + 审计表
    _ensure_column(c, 'registration_participants', 'effective_at', 'effective_at TEXT')
    _ensure_column(c, 'registration_participants', 'expire_at', 'expire_at TEXT')
    _ensure_column(c, 'registration_participants', 'premium_amount', 'premium_amount REAL NOT NULL DEFAULT 0')
    _ensure_column(c, 'registration_participants', 'premium_refunded', 'premium_refunded REAL NOT NULL DEFAULT 0')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS insurance_jobs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      club_id INTEGER NOT NULL,
      participant_id INTEGER NOT NULL,
      registration_id INTEGER NOT NULL,
      occurrence_id INTEGER NOT NULL,
      action TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'processing',
      effective_at TEXT,
      expire_at TEXT,
      premium_amount REAL NOT NULL DEFAULT 0,
      provider TEXT,
      policy_no TEXT,
      error TEXT,
      provider_payload_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (participant_id) REFERENCES registration_participants(id)
    );
    CREATE INDEX IF NOT EXISTS idx_insurance_jobs_participant ON insurance_jobs(participant_id, action, status);
    CREATE INDEX IF NOT EXISTS idx_insurance_jobs_registration ON insurance_jobs(registration_id, action, status);
    ''')
    # v0.29 C 端「业务介绍」可配置区：俱乐部自定义（团建/研学/企业团、租装备……），
    # C 端户外能力页读它；未配置 = NULL → 前端整节隐藏（空壳板块比没有更伤信任）。
    _ensure_column(c, 'clubs', 'biz_section_json', 'biz_section_json TEXT')
    # C 端「我的」页文案（联系我们 / 关于俱乐部）：原来这两页的内容全是前端写死的占位
    # 文案，现在由俱乐部在后台自定义；未配置 = NULL = 前端只出「名字 + 版本」的最小页。
    _ensure_column(c, 'clubs', 'c_profile_json', 'c_profile_json TEXT')


def _backfill_media_urls(c):
    """修复 live 模式下丢失 url 的媒体清单。

    ai_engine 的 live 分支曾把模型输出的 media（["img_01",...] 这种纯 ref）直接当作 master 落库，
    导致 ref→url 映射表为空：A 端预览与 C 端详情里所有图片都渲染成灰色占位块，头图退化成纯色。
    这里以 activities.source_json 的媒体清单为准补回 url；没有原始资料可依据的行保持原样
    （宁可不改，也不凭空编造一个 url）。幂等：内容没有任何变化就不写库。
    """
    def catalog_of(source):
        catalog={}
        if not isinstance(source,dict):return catalog
        for key in ('media_manifest','images'):
            for x in source.get(key) or []:
                if not isinstance(x,dict):continue
                ref=str(x.get('ref') or ''); url=str(x.get('url') or '')
                if ref and url and ref not in catalog:catalog[ref]=x
        return catalog

    def merge(rows,catalog):
        """返回修好的列表；没有可修的地方返回 None（=不动这一行）。"""
        if not isinstance(rows,list) or not rows:return None
        out=[];changed=False
        for x in rows:
            if isinstance(x,dict):
                ref=str(x.get('ref') or '')
                if str(x.get('url') or ''):out.append(x);continue
                base=catalog.get(ref)
                if not base:out.append(x);continue
                item=dict(x)
                item.update({k:base.get(k) for k in ('url','name','width','height','orientation','source','page') if base.get(k) is not None})
                out.append(item);changed=True
            else:
                base=catalog.get(str(x or ''))
                out.append(dict(base) if base else {'ref':str(x or '')})
                changed=changed or bool(base)
        return out if changed else None

    def repair(blob,catalog):
        try:data=json.loads(blob) if isinstance(blob,str) else blob
        except (TypeError,ValueError):return None
        if not isinstance(data,dict):return None
        rows=data.get('media')
        if not isinstance(rows,list) or not rows:return None
        if all(isinstance(x,dict) and str(x.get('url') or '') for x in rows):return None   # 已经完好，不必动
        fixed=merge(rows,catalog)
        if not fixed:return None
        data['media']=fixed
        return data

    for a in c.execute('SELECT id,source_json,activity_master_json FROM activities').fetchall():
        try:source=json.loads(a['source_json']) if a['source_json'] else None
        except (TypeError,ValueError):source=None
        data=repair(a['activity_master_json'],catalog_of(source))
        if data:c.execute('UPDATE activities SET activity_master_json=? WHERE id=?',(json.dumps(data,ensure_ascii=False),a['id']))
    # 版本快照里的 master 同样要修，否则恢复某一版会把坏的媒体清单又写回当前活动。
    rows=c.execute('SELECT v.id,v.activity_id,v.master_json,a.source_json FROM activity_detail_versions v '
                   'JOIN activities a ON a.id=v.activity_id WHERE v.master_json IS NOT NULL').fetchall()
    for v in rows:
        try:source=json.loads(v['source_json']) if v['source_json'] else None
        except (TypeError,ValueError):source=None
        data=repair(v['master_json'],catalog_of(source))
        if data:c.execute('UPDATE activity_detail_versions SET master_json=? WHERE id=?',(json.dumps(data,ensure_ascii=False),v['id']))


def _backfill_detail_versions(c):
    """把升级前就已经存在的活动详情，回填成「第 1 版」快照。

    这次升级之前生成的活动只有 activities.detail_json，没有版本快照。如果不回填，
    老板点一次「换一版」之后，最初那一版就被覆盖、再也回不去了——正好和「版本历史」的
    承诺相反。origin='legacy' 标明这一版来自升级回填，不是真的有调用过 AI。
    整个过程幂等：只处理「一行版本都没有」的活动，重复启动不会重复插入。
    """
    c.execute('''INSERT INTO activity_detail_versions(
                   club_id,activity_id,version_no,origin,direction,facts_refreshed,
                   detail_json,master_json,credits_charged,status,created_at)
                 SELECT a.club_id,a.id,1,'legacy','',0,a.detail_json,a.activity_master_json,0,
                        'ready',COALESCE(NULLIF(a.created_at,''),CURRENT_TIMESTAMP)
                 FROM activities a
                 WHERE a.detail_json IS NOT NULL AND a.detail_json NOT IN ('','{}')
                   AND NOT EXISTS(SELECT 1 FROM activity_detail_versions v WHERE v.activity_id=a.id)''')
    # 版本指针没指到任何一版的，指到当前最新的一版，保证「当前版本」永远存在。
    c.execute('''UPDATE activities SET detail_version_id=(
                   SELECT v.id FROM activity_detail_versions v
                   WHERE v.activity_id=activities.id AND v.status='ready'
                   ORDER BY v.version_no DESC LIMIT 1)
                 WHERE detail_version_id IS NULL
                   AND EXISTS(SELECT 1 FROM activity_detail_versions v
                              WHERE v.activity_id=activities.id AND v.status='ready')''')


def _seed_v08_defaults(c):
    clubs=c.execute('SELECT id FROM clubs').fetchall()
    for cr in clubs:
        club_id=int(cr[0])
        if c.execute('SELECT COUNT(*) FROM club_member_tiers WHERE club_id=?',(club_id,)).fetchone()[0]==0:
            c.executemany("""INSERT INTO club_member_tiers(club_id,name,rank,min_activity_spend,min_activity_count,qualification_mode,benefits_json) VALUES(?,?,?,?,?,?,?)""",[
                (club_id,'普通会员',0,0,0,'ANY','["基础会员权益"]'),
                (club_id,'银卡会员',10,500,2,'ANY','["会员活动优先报名","专属积分福利"]'),
                (club_id,'金卡会员',20,2000,5,'ANY','["高阶会员活动","专属福利兑换"]'),
            ])
    # Demo benefits only illustrate funding ownership. Platform benefits can be displayed by all clubs when target_club_id is NULL.
    if os.getenv('CLUBOS_SECURITY_MODE','demo')!='production' and c.execute('SELECT COUNT(*) FROM member_benefits').fetchone()[0]==0 and clubs:
        first_club=int(clubs[0][0])
        c.execute("""INSERT INTO member_benefits(owner_type,owner_club_id,target_club_id,title,description,benefit_type,points_type,points_cost,cash_value,stock,status)
                     VALUES('CLUB',?,?,?,?,?,?,?,?,?,?)""",
                  (first_club,first_club,'老会员活动优先名额','俱乐部提供的会员权益，仅使用本俱乐部活动积分兑换。','service','club',300,0,50,'active'))
        c.execute("""INSERT INTO member_benefits(owner_type,owner_club_id,target_club_id,title,description,benefit_type,points_type,points_cost,cash_value,stock,status)
                     VALUES('PLATFORM',NULL,NULL,?,?,?,?,?,?,?,?)""",
                  ('装备商城 ¥10 抵扣福利','ClubOS 平台承担成本，可显示在俱乐部会员中心。','gear_coupon','gear',1000,10,200,'active'))


def _seed_v15_payment_defaults(c):
    c.execute("INSERT INTO payment_accounts(scope_type,scope_id,provider,channel,credential_ref,enabled) SELECT 'platform',NULL,'local','mock','platform_local',1 WHERE NOT EXISTS (SELECT 1 FROM payment_accounts WHERE scope_type='platform')")
    for cr in c.execute('SELECT id FROM clubs').fetchall():
        cid=int(cr[0])
        c.execute("INSERT INTO payment_accounts(scope_type,scope_id,provider,channel,credential_ref,enabled) SELECT 'club',?,'local','mock',?,1 WHERE NOT EXISTS (SELECT 1 FROM payment_accounts WHERE scope_type='club' AND scope_id=?)",(cid,f'club_{cid}_local',cid))

def _migrate_v031_coupon_club_nullable(c):
    """平台直发的券没有归属俱乐部 → benefit_redemptions.display_club_id 必须允许 NULL。

    原来是 NOT NULL + 外键指向 clubs(id)，插 display_club_id=0 直接撞外键
    （FOREIGN KEY constraint failed）。SQLite 不能直接改列，只能重建表；
    这张表只存券记录（线上目前 0 行），照搬现结构 + 放宽这一列即可。
    重建前必须先 commit：PRAGMA foreign_keys 在事务里是空操作。
    """
    row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='benefit_redemptions'").fetchone()
    if not row:
        return
    sql = row[0] or ''
    if 'display_club_id INTEGER NOT NULL' not in sql:
        return  # 已经是可空（或表还没建）
    cols = [r[1] for r in c.execute('PRAGMA table_info(benefit_redemptions)')]
    if not cols:
        return
    c.commit()
    c.execute('PRAGMA foreign_keys=OFF')
    try:
        new_sql = sql.replace('display_club_id INTEGER NOT NULL', 'display_club_id INTEGER')
        new_sql = new_sql.replace('CREATE TABLE benefit_redemptions', 'CREATE TABLE benefit_redemptions_v031', 1)
        c.execute(new_sql)
        c.execute('INSERT INTO benefit_redemptions_v031(%s) SELECT %s FROM benefit_redemptions'
                  % (','.join(cols), ','.join(cols)))
        c.execute('DROP TABLE benefit_redemptions')
        c.execute('ALTER TABLE benefit_redemptions_v031 RENAME TO benefit_redemptions')
    finally:
        c.execute('PRAGMA foreign_keys=ON')
    c.commit()
    # 重建表会把索引一起带走，这里补回来。
    for idx in ("CREATE INDEX IF NOT EXISTS idx_benefit_redemptions_user "
                "ON benefit_redemptions(user_id,display_club_id,created_at DESC)",
                "CREATE INDEX IF NOT EXISTS idx_benefit_redemptions_checkout "
                "ON benefit_redemptions(held_checkout_id,status)",
                "CREATE INDEX IF NOT EXISTS idx_benefit_redemptions_order "
                "ON benefit_redemptions(used_order_kind,used_order_id,status)"):
        c.execute(idx)
    c.commit()


def _migrate_content_assets_activity_nullable(c):
    """活动回顾解耦（2026-10-30）：独立回顾不绑定系统活动，content_assets.activity_id 必须允许 NULL。

    原来是 NOT NULL + 外键指向 activities(id)，插 activity_id=0 直接撞外键（FOREIGN KEY constraint failed）。
    SQLite 不能直接改列，只能重建表；这张表存已生成的渠道/回顾内容，照搬现结构 + 放宽这一列即可，
    数据全部带过去。重建前必须先 commit：PRAGMA foreign_keys 在事务里是空操作。
    """
    row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='content_assets'").fetchone()
    if not row:
        return
    sql = row[0] or ''
    if 'activity_id INTEGER NOT NULL' not in sql:
        return  # 已经是可空（或表还没建）
    cols = [r[1] for r in c.execute('PRAGMA table_info(content_assets)')]
    if not cols:
        return
    c.commit()
    c.execute('PRAGMA foreign_keys=OFF')
    try:
        new_sql = sql.replace('activity_id INTEGER NOT NULL', 'activity_id INTEGER', 1)
        new_sql = new_sql.replace('CREATE TABLE content_assets', 'CREATE TABLE content_assets_v031b', 1)
        c.execute(new_sql)
        c.execute('INSERT INTO content_assets_v031b(%s) SELECT %s FROM content_assets'
                  % (','.join(cols), ','.join(cols)))
        c.execute('DROP TABLE content_assets')
        c.execute('ALTER TABLE content_assets_v031b RENAME TO content_assets')
    finally:
        c.execute('PRAGMA foreign_keys=ON')
    c.commit()


def init_db():
    with conn() as c:
        c.executescript(SCHEMA.read_text(encoding='utf-8'))
        _run_compat_migrations(c)
        # 内容资产放宽 activity_id（独立回顾不绑定活动）—— 必须排在播种 / 写入之前
        _migrate_content_assets_activity_nullable(c)
        # 券表放宽 display_club_id（平台直发的券没有归属俱乐部）—— 必须排在播种之前，
        # 否则任何一条发券动作都会撞外键。
        _migrate_v031_coupon_club_nullable(c)
        if c.execute('SELECT COUNT(*) FROM clubs').fetchone()[0]:
            _seed_v08_defaults(c)
            if os.getenv('CLUBOS_SECURITY_MODE','demo')!='production': _seed_v15_payment_defaults(c)
            _backfill_v019_opening_inventory(c)
            _backfill_v020_warehouse(c)
            _backfill_v021_finance(c)
            _backfill_v023_ai_credits(c)
            _backfill_plan_gear_discount(c)
            _backfill_plan_gear_points_percent(c)
            _backfill_v027_variants(c)
            # 必须挂在这个 return 之前：init_db 只要发现库里已经有 clubs
            # 就会提前返回，挂在后面等于给存量库迁移不到新列。
            _backfill_v028_leader_avatar(c)
            # 商品目录迁移要读 products.category，必须在补列之后 ——
            # 补列走 _run_compat_migrations（上面第一个调用），顺序天然成立。
            _backfill_v030_catalog(c)
            # 2026-11-03：会员活动折扣默认开启。存量活动此前因默认 0（且后台无开关）一直不生效，
            # 一次性把已发布活动翻成 1（仅跑一次，运营后续手动置 0 不会被重启覆盖）。
            _backfill_v032_member_discount_default(c)
            # 2026-11-06：活动分类后台化 —— 给没有类别的俱乐部种默认类别 + 正则挂存量活动（仅一次）。
            _backfill_activity_categories(c)
            return
        if os.getenv('CLUBOS_SECURITY_MODE','demo')=='production':
            # Never provision sample clubs, consumers, catalog, payments or credits in production.
            from clubos_domain.ai_credits import AICreditEngine
            _ensure_column(c,'ai_credit_plans','gear_points_redeem_percent',
                           'gear_points_redeem_percent REAL NOT NULL DEFAULT 0')
            AICreditEngine().seed_defaults(c)
            return
        c.execute('INSERT INTO clubs(name,status,plan) VALUES(?,?,?)',('远拓户外','active','pro')); club_id=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('INSERT INTO ai_credit_accounts(club_id,balance,monthly_quota) VALUES(?,?,?)',(club_id,1600,1200))
        c.execute('INSERT INTO ai_credit_ledger(club_id,type,amount,source_type,note) VALUES(?,?,?,?,?)',(club_id,'subscription',1200,'monthly','月度订阅额度'))
        c.execute('INSERT INTO ai_credit_ledger(club_id,type,amount,source_type,note) VALUES(?,?,?,?,?)',(club_id,'mall_reward',400,'gear_gmv','装备商城增长奖励'))
        c.execute('INSERT INTO users(name,phone) VALUES(?,?)',('林野','13800000001')); user_id=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('INSERT INTO club_members(club_id,user_id,level,club_points_balance) VALUES(?,?,?,?)',(club_id,user_id,'银卡会员',860))
        c.execute('INSERT INTO gear_point_accounts(user_id,balance) VALUES(?,?)',(user_id,1280))
        products=[
            ('轻量防风软壳','GEAR-SHELL-001',699,48,'服装',0.08),
            ('碳纤维折叠登山杖','GEAR-POLE-001',299,86,'徒步装备',0.10),
            ('22L 日行背包','GEAR-PACK-001',459,35,'背包',0.09),
            ('美丽诺羊毛基础层','GEAR-WOOL-001',599,52,'服装',0.08),
        ]
        c.executemany('INSERT INTO products(name,sku,price,stock,category,commission_rate) VALUES(?,?,?,?,?,?)',products)
        # 种子商品也必须补默认规格：库存真源在规格层，不回填的话
        # 汇总重算会把商品库存抹成 0，双账本自检永远报不一致。
        _backfill_v027_variants(c)

        media=_demo_media()
        # 演示团期必须始终落在未来：原先把日期写死成 2026-10-24，当天 08:00 之后
        # 「报名」一律 409（该团期已出发），演示站看起来像坏了，回归里 6 个
        # checkout 用例（smoke / v08 / v09 / v10 / v15 / cost_leak）也会集体挂掉。
        from datetime import date as _date, timedelta as _td
        _d1=_date.today()+_td(days=1)
        _d2=_d1+_td(days=7)
        demo_master={
            'title':'MOVE TO NATURAL｜蓥华山徒步 × 户外瑜伽','date':_d1.isoformat(),'location':'什邡蓥华山','price':498,'capacity':30,
            'publicFacts':{'date':_d1.isoformat(),'location':'什邡蓥华山','distance':'约6公里','price':'新客498元','team':'约30人'},
            'itinerary':[
                {'time':'08:00–08:30','content':'万象城 icebreaker 门店集合签到'},
                {'time':'08:30–10:00','content':'统一乘车前往蓥华山'},
                {'time':'10:00–11:00','content':'草坪户外瑜伽'},
                {'time':'11:00–12:00','content':'户外牛肉汤锅'},
                {'time':'12:00–17:00','content':'蓥华山约6公里轻徒步'},
                {'time':'17:00–18:50','content':'统一乘车返回成都'}],
            'fees':{'newCustomer':'498元/人','member':'1500积分/人','note':'具体权益以最终报名规则为准'},
            'checklist':['防水防滑徒步鞋','速干衣裤','防晒外套','单日小背包','防晒帽','可选登山杖'],
            'services':['户外领队','瑜伽老师','摄影师','后勤保障'],
            'internalData':{},'uncertainties':[],'blocking_conflicts':[],'media':media,
        }
        def refs(a,b):return [m['ref'] for m in media[a:b]]
        demo_detail={
            'activityUnderstanding':'这不是“上午一个瑜伽 + 下午一个徒步”的拼盘，而是一整天从身体到自然的转换。',
            'coreSellingIdea':'先把身体打开，再走进森林。',
            'editorialIntent':{'opening':'从身体感受而不是景区介绍开场','visualWeight':'high','template':'NONE'},
            'blocks':[
                {'type':'hero','kicker':f'ICEBREAKER NATURAL CLUB · {_d1:%Y.%m.%d}','headline':'MOVE TO NATURAL','subtitle':'先把身体打开，再走进森林。','mediaRefs':refs(0,1)},
                {'type':'lead','text':'周末不一定需要安排得很满。离开城市，把上午交给呼吸和伸展，再把午后交给一条约6公里的森林步道。'},
                {'type':'facts','items':[{'label':'YOGA','value':'1 HOUR'},{'label':'HIKE','value':'6 KM'},{'label':'TEAM','value':'≈30 PEOPLE'}]},
                {'type':'gallery','mediaRefs':refs(1,4),'caption':'真正进入山里之前，先让身体慢下来。'},
                {'type':'narrative','eyebrow':'01 · OPEN THE BODY','headline':'上午，我们先不急着走','body':'抵达之后，先用呼吸和伸展把身体从城市节奏里松开。瑜伽不是附加环节，而是进入这一天的方式。','mediaRefs':refs(4,6)},
                {'type':'statement','text':'先用一小时把身体打开，再用六公里把自己交给森林。'},
                {'type':'gallery','mediaRefs':refs(6,10)},
                {'type':'narrative','eyebrow':'02 · WALK INTO THE FOREST','headline':'午饭之后，真正走进山里','body':'沿林间石阶、杉林与湖岸慢慢向前。页面不把目的地写成百科，只保留真正会影响你是否想去的那部分体验。','mediaRefs':refs(10,12)},
                {'type':'media','mediaRefs':refs(12,16),'layout':'mosaic'},
                {'type':'timeline','title':'把一天安排得刚刚好','items':demo_master['itinerary']},
                {'type':'info','title':'出发前知道这些就够了','items':['约6公里轻徒步','建议穿防滑徒步鞋','带单日背包与饮水','天气与最终装备提醒以出发前通知为准']},
                {'type':'cta','headline':'把这个周六留给身体，也留给自然','text':'选择团期后，可使用活动积分与平台装备积分按规则抵扣。'}]
        }
        c.execute('''INSERT INTO activities(
            club_id,title,status,event_date,location,price,capacity,
            points_enabled,earn_club_points,accept_club_points,club_points_max_discount_percent,
            accept_gear_points,gear_points_max_discount_amount,activity_master_json,detail_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
            club_id,demo_master['title'],'published',demo_master['date'],demo_master['location'],498,30,
            1,1,1,10,1,30,json.dumps(demo_master,ensure_ascii=False),json.dumps(demo_detail,ensure_ascii=False)))
        aid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        occs=[
            (aid,club_id,f'{_d1} 08:00',f'{_d1} 18:50',498,30,8,'open',f'{_d1.month}月{_d1.day}日 · 标准团'),
            (aid,club_id,f'{_d2} 08:00',f'{_d2} 18:50',528,24,3,'open',f'{_d2.month}月{_d2.day}日 · 小团'),
        ]
        c.executemany('INSERT INTO activity_occurrences(activity_id,club_id,start_at,end_at,price,capacity,sold,status,label) VALUES(?,?,?,?,?,?,?,?,?)',occs)
        settings={
            'club_points_rate':'1','club_points_redeem_rate':'100','gear_points_rate':'1','gear_points_redeem_rate':'100',
            'mall_ai_reward_per_1000_gmv':'20','gear_points_activity_redeem_enabled':'1','commission_after_sales_days':'7','ai_cost_detail':'30','ai_cost_wechat':'12','ai_cost_xhs':'8','ai_cost_poster':'6','ai_cost_recap':'16'}
        c.executemany('INSERT OR IGNORE INTO platform_settings(key,value) VALUES(?,?)',settings.items())
        _seed_v15_payment_defaults(c)
        _seed_v08_defaults(c)
        _backfill_v019_opening_inventory(c)
        _backfill_v020_warehouse(c)
        _backfill_v021_finance(c)
        _backfill_v023_ai_credits(c)
        # 新库演示种子也要给俱乐部种默认活动分类（上面「已有 clubs」分支只覆盖存量库）。
        _backfill_activity_categories(c)

def _backfill_v032_member_discount_default(c):
    # 2026-11-03：会员活动折扣默认开启。存量活动此前因 member_discount_enabled 默认 0、且后台无开关，
    # 导致购买会员卡后「会员 9 折报名」永远不生效。这里一次性把已发布活动翻成 1。
    # 用 _migrations 守护：仅执行一次，运营后续若手动把某场活动置 0（opt-out），重启不会被覆盖。
    c.execute("CREATE TABLE IF NOT EXISTS _migrations(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    if c.execute("SELECT 1 FROM _migrations WHERE name='member_discount_default_on'").fetchone():
        return
    c.execute("UPDATE activities SET member_discount_enabled=1 WHERE member_discount_enabled=0 AND status='published'")
    c.execute("INSERT INTO _migrations(name,applied_at) VALUES('member_discount_default_on', datetime('now'))")


def _backfill_activity_categories(c):
    # 2026-11-06：活动分类后台化。为「一个类别都没有」的俱乐部种入 4 个默认类别
    # （与旧前端 ACT_THEMES 同名同序），并按旧正则把存量**已发布**活动挂上类别，
    # 保证 C 端磁贴从自动分类切到后台分类的第一天就不是空的；之后运营在后台
    # 随意增删改名换图，重启不会被覆盖（_migrations 守护，仅执行一次）。
    # 未命中任何正则的活动不强行归类 —— 它仍出现在「全部」里，后台可手动挂。
    c.execute("CREATE TABLE IF NOT EXISTS _migrations(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    if c.execute("SELECT 1 FROM _migrations WHERE name='activity_categories_seed'").fetchone():
        return
    defaults=[
        ('徒步穿越', r'徒步|登山|穿越|爬山|古道|山径|速降|攀冰|越野'),
        ('风光摄影', r'摄影|金山|日照|风光|观景|星空|云海|日出|日落|雪山|航拍'),
        ('疗愈静心', r'疗愈|颂钵|冥想|禅修|森林|瑜伽|正念|温泉'),
        ('亲子研学', r'亲子|儿童|研学|夏令营|成长营|自然教育'),
    ]
    import re as _re
    for club in c.execute('SELECT id FROM clubs').fetchall():
        cid=club[0]
        if c.execute('SELECT 1 FROM activity_categories WHERE club_id=? LIMIT 1',(cid,)).fetchone():
            continue
        cat_ids=[]
        for i,(name,pat) in enumerate(defaults):
            cur=c.execute('INSERT INTO activity_categories(club_id,name,sort_order,active) VALUES(?,?,?,1)',(cid,name,i))
            cat_ids.append(cur.lastrowid)
        rex=[(_re.compile(pat),cid_i) for pat,cid_i in zip([d[1] for d in defaults],cat_ids)]
        for act in c.execute("SELECT id,title FROM activities WHERE club_id=? AND status='published'",(cid,)).fetchall():
            title=str(act[1] or '')
            for rx,catid in rex:
                if rx.search(title):
                    c.execute('INSERT OR IGNORE INTO activity_category_links(activity_id,category_id) VALUES(?,?)',(act[0],catid))
    c.execute("INSERT INTO _migrations(name,applied_at) VALUES('activity_categories_seed', datetime('now'))")


def setting(key,default=None):
    with conn() as c:
        r=c.execute('SELECT value FROM platform_settings WHERE key=?',(key,)).fetchone(); return r[0] if r else default

def jdump(v):return json.dumps(v,ensure_ascii=False)
def jload(v,default=None):
    try:return json.loads(v)
    except Exception:return default
