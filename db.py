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
    for p in c.execute('SELECT id,stock,average_cost FROM products').fetchall():
        key=f"v019-opening:{p['id']}"
        if not c.execute('SELECT 1 FROM inventory_movements WHERE idempotency_key=?',(key,)).fetchone():
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
                 WHERE unit_cost_snapshot IS NULL OR unit_cost_snapshot=0""")
    for r in c.execute('SELECT id FROM purchase_receipts ORDER BY received_at,id').fetchall():
        finance.register_receipt_payable(c,receipt_id=str(r['id']))

def _backfill_gear_discount(c):
    """给存量会员等级补一个「装备商城会员折扣」兜底值：等级越高折扣越低。

    按俱乐部分组、**按 rank 排序取档位**（最低档无折扣、第二档 95 折、第三档及以后 9 折），
    而不是拿 rank 的绝对值当档位——本项目 rank 用的是 0/10/20 这种量级，
    用绝对值判断会把银卡也算成 9 折（实测踩过这个坑）。

    只补 NULL（= 从未配置过）的行，所以重复执行幂等，也绝不会覆盖俱乐部自己调过的比例。
    """
    ladder=(1.0,0.95,0.90)
    for row in c.execute('SELECT DISTINCT club_id FROM club_member_tiers').fetchall():
        cid=row[0]
        tiers=c.execute('SELECT id,gear_discount FROM club_member_tiers WHERE club_id=? ORDER BY rank,id',(cid,)).fetchall()
        for pos,t in enumerate(tiers):
            if t['gear_discount'] is not None:
                continue
            rate=ladder[pos] if pos<len(ladder) else ladder[-1]
            c.execute('UPDATE club_member_tiers SET gear_discount=? WHERE id=?',(rate,t['id']))


def _backfill_v023_ai_credits(c):
    from clubos_domain.ai_credits import AICreditEngine
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

def _run_compat_migrations(c):
    _ensure_column(c,'clubs','contact_name','contact_name TEXT')
    _ensure_column(c,'clubs','contact_phone','contact_phone TEXT')
    _ensure_column(c,'clubs','city','city TEXT')
    _ensure_column(c,'clubs','application_note','application_note TEXT')
    _ensure_column(c,'clubs','business_license_ref','business_license_ref TEXT')
    _ensure_column(c,'clubs','reviewed_at','reviewed_at TEXT')
    _ensure_column(c,'clubs','reviewed_by','reviewed_by TEXT')
    _ensure_column(c,'clubs','disabled_reason','disabled_reason TEXT')
    _ensure_column(c,'ai_credit_adjustment_debt','resolved_amount','resolved_amount INTEGER NOT NULL DEFAULT 0')
    _ensure_column(c,'ai_credit_adjustment_debt','resolved_at','resolved_at TEXT')
    _ensure_column(c,'registrations','commerce_order_id','commerce_order_id TEXT')
    _ensure_column(c,'registrations','booking_ref','booking_ref TEXT')
    _ensure_column(c,'registrations','points_policy_snapshot_json','points_policy_snapshot_json TEXT')
    _ensure_column(c,'registrations','club_benefit_discount','club_benefit_discount REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'registrations','platform_benefit_subsidy','platform_benefit_subsidy REAL NOT NULL DEFAULT 0')
    _ensure_column(c,'registrations','benefit_redemption_ids_json',"benefit_redemption_ids_json TEXT NOT NULL DEFAULT '[]'")
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
    # v0.27 库存下沉到规格：流水与订单明细要能指到具体规格。
    # 都可空 —— 无规格商品（以及 v0.27 之前的历史流水）variant_id 为 NULL 仍然合法。
    _ensure_column(c,'inventory_movements','variant_id','variant_id INTEGER')
    _ensure_column(c,'gear_order_items','variant_id','variant_id INTEGER')
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
    # v0.27：装备商城的会员折扣率、领队资源库与团期领队的关联列
    _ensure_column(c,'club_member_tiers','gear_discount','gear_discount REAL')
    _ensure_column(c,'occurrence_leaders','leader_id','leader_id INTEGER')
    _backfill_gear_discount(c)
    _backfill_detail_versions(c)
    _backfill_v019_opening_inventory(c)
    _backfill_v020_warehouse(c)
    _backfill_v021_finance(c)
    _backfill_v023_ai_credits(c)
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

def init_db():
    with conn() as c:
        c.executescript(SCHEMA.read_text(encoding='utf-8'))
        _run_compat_migrations(c)
        if c.execute('SELECT COUNT(*) FROM clubs').fetchone()[0]:
            _seed_v08_defaults(c)
            if os.getenv('CLUBOS_SECURITY_MODE','demo')!='production': _seed_v15_payment_defaults(c)
            _backfill_v019_opening_inventory(c)
            _backfill_v020_warehouse(c)
            _backfill_v021_finance(c)
            _backfill_v023_ai_credits(c)
            _backfill_v027_variants(c)
            # 必须挂在这个 return 之前：init_db 只要发现库里已经有 clubs
            # 就会提前返回，挂在后面等于给存量库迁移不到新列。
            _backfill_v028_leader_avatar(c)
            return
        if os.getenv('CLUBOS_SECURITY_MODE','demo')=='production':
            # Never provision sample clubs, consumers, catalog, payments or credits in production.
            from clubos_domain.ai_credits import AICreditEngine
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

        media=_demo_media()
        demo_master={
            'title':'MOVE TO NATURAL｜蓥华山徒步 × 户外瑜伽','date':'2026-10-24','location':'什邡蓥华山','price':498,'capacity':30,
            'publicFacts':{'date':'2026-10-24','location':'什邡蓥华山','distance':'约6公里','price':'新客498元','team':'约30人'},
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
                {'type':'hero','kicker':'ICEBREAKER NATURAL CLUB · 2026.10.24','headline':'MOVE TO NATURAL','subtitle':'先把身体打开，再走进森林。','mediaRefs':refs(0,1)},
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
            (aid,club_id,'2026-10-24 08:00','2026-10-24 18:50',498,30,8,'open','10月24日 · 标准团'),
            (aid,club_id,'2026-10-31 08:00','2026-10-31 18:50',528,24,3,'open','10月31日 · 小团'),
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

def setting(key,default=None):
    with conn() as c:
        r=c.execute('SELECT value FROM platform_settings WHERE key=?',(key,)).fetchone(); return r[0] if r else default

def jdump(v):return json.dumps(v,ensure_ascii=False)
def jload(v,default=None):
    try:return json.loads(v)
    except Exception:return default
