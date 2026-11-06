"""成本 BOM 冒烟测试（#641 / #647）。

不依赖线上库、不跑 init_db 的播种/回填逻辑：直接用 schema.sql + 兼容迁移建表，
再用裸 sqlite 连接插入最小订单数据（关闭外键约束，避免要补 users/clubs），
然后用 MerchandiseFinanceEngine 验证：
  - get_bom 空脚手架
  - save_bom 启用后，order_profit 自动按 BOM 分摊 物流/包材/其他
  - 订单手动覆盖（cost_bom_override=1）优先于 BOM
  - 重置覆盖后回到 BOM
  - profit_summary 聚合 otherCost
运行：python3 tests/bom_cost_smoke.py
"""
import os, sys, sqlite3, tempfile

DB = '/tmp/bom_smoke.db'
if os.path.exists(DB):
    os.remove(DB)
os.environ['CLUBOS_DB_PATH'] = DB

ROOT = os.path.dirname(os.path.abspath(__file__)) + '/..'
sys.path.insert(0, ROOT)

import db
from clubos_domain.merchandise_finance import MerchandiseFinanceEngine

# 1) 纯建表 + 迁移（不播种）
with db.conn() as c:
    c.executescript(db.SCHEMA.read_text(encoding='utf-8'))
    db._run_compat_migrations(c)

# 2) 裸连接插入最小数据（默认不启用外键，规避 users/clubs 依赖）
raw = sqlite3.connect(DB)
raw.execute("INSERT INTO products(name,sku,price) VALUES('测试帐篷','SKU-TENT-001',500)")
pid = raw.execute("SELECT last_insert_rowid()").fetchone()[0]
raw.execute(
    "INSERT INTO gear_orders(user_id,source_club_id,total,status) VALUES(1,1,1000.00,'paid')")
oid = raw.execute("SELECT last_insert_rowid()").fetchone()[0]
raw.execute(
    "INSERT INTO gear_order_items(order_id,product_id,quantity,unit_price,unit_cost_snapshot) "
    "VALUES(?,?,?,?,?)", (oid, pid, 2, 500.00, 300.00))
raw.execute("INSERT INTO commission_ledger(club_id,order_id,amount) VALUES(1,?,?)", (oid, 50.00))
raw.commit()
raw.close()

eng = MerchandiseFinanceEngine()
FAILED = False

def check(name, cond, extra=''):
    global FAILED
    ok = bool(cond)
    if not ok:
        FAILED = True
    print(('PASS' if ok else 'FAIL') + ': ' + name + (('   ' + str(extra)) if extra != '' else ''))

# 3) 空脚手架
with db.conn() as c:
    b = eng.get_bom(c)
    check('get_bom 初始为空脚手架', b['id'] is None and b['lines'] == [])

# 4) 保存并启用 BOM（fixed / per_item / percent_net 三种计算方式）
with db.conn() as c:
    saved = eng.save_bom(c, name='默认成本清单', note='测试', enabled=True, lines=[
        {'label': '顺丰陆运', 'category': 'logistics', 'calcType': 'fixed', 'baseValue': 12.0, 'rateValue': 0},
        {'label': '纸箱', 'category': 'packaging', 'calcType': 'per_item', 'baseValue': 3.0, 'rateValue': 0},
        {'label': '打包人工', 'category': 'other', 'calcType': 'percent_net', 'baseValue': 0, 'rateValue': 0.02},
    ])
    check('save_bom isActive=1', saved['isActive'] == 1)
    check('save_bom 返回 3 条成本项', len(saved['lines']) == 3)

# 5) order_profit 自动套 BOM
# net_sales=1000, cogs=2*300=600, commission=50, 积分/福利=0
# shipping=12(fixed), packaging=3*2=6(per_item), other=1000*0.02=20(percent_net)
# contribution = 1000-600-0-0-50-12-6-20 = 312
with db.conn() as c:
    p = eng.order_profit(c, oid)
    check('costSource=BOM', p['costSource'] == 'BOM', p['costSource'])
    check('shippingCost=12', p['shippingCost'] == 12.0, p['shippingCost'])
    check('packagingCost=6', p['packagingCost'] == 6.0, p['packagingCost'])
    check('otherCost=20', p['otherCost'] == 20.0, p['otherCost'])
    check('contributionProfit=312', p['contributionProfit'] == 312.0, p['contributionProfit'])
    check('bomLines=3', len(p['bomLines']) == 3, len(p['bomLines']))

# 6) 手动覆盖优先
with db.conn() as c:
    c.execute("UPDATE gear_orders SET shipping_cost=?,packaging_cost=?,cost_bom_override=1 WHERE id=?",
              (20.0, 10.0, oid))
    p = eng.order_profit(c, oid)
    check('覆盖后 costSource=manual', p['costSource'] == 'manual', p['costSource'])
    check('覆盖后 shippingCost=20', p['shippingCost'] == 20.0, p['shippingCost'])
    check('覆盖后 packagingCost=10', p['packagingCost'] == 10.0, p['packagingCost'])
    check('覆盖后 otherCost=0', p['otherCost'] == 0.0, p['otherCost'])
    # 1000-600-0-0-50-20-10-0 = 320
    check('覆盖后 contributionProfit=320', p['contributionProfit'] == 320.0, p['contributionProfit'])

# 7) 重置覆盖 -> 回到 BOM
with db.conn() as c:
    c.execute("UPDATE gear_orders SET cost_bom_override=0 WHERE id=?", (oid,))
    p = eng.order_profit(c, oid)
    check('重置后 costSource=BOM', p['costSource'] == 'BOM', p['costSource'])

# 8) profit_summary 聚合 otherCost
with db.conn() as c:
    s = eng.profit_summary(c)
    check('summary otherCost=20', s['otherCost'] == 20.0, s['otherCost'])
    check('summary orderCount=1', s['orderCount'] == 1, s['orderCount'])

print('\nRESULT:', 'ALL PASS' if not FAILED else 'SOME FAILED')
sys.exit(1 if FAILED else 0)
