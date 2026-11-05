"""SKU physical stock. Legacy product-level WMS rows remain read-compatible projections."""
import json
from .product_stock import variant_of


def mirrored(c, p):
    """外部品牌「库存已镜像」：PEAKTRACE 等上游的可售库存已落进本地镜像仓。

    只对这一组合放行，普通 external_api 商品仍然被 owned() 拦住（它们没有真实库存）。
    必须同时满足：镜像库存模式 + 平台履约 + 存在一次成功的库存同步映射。
    """
    p=dict(p) if not isinstance(p,dict) else p
    if p.get('inventory_mode')!='external_mirror' or p.get('fulfillment_mode')!='platform_warehouse':
        return False
    return bool(c.execute("""SELECT 1 FROM external_product_mappings
                            WHERE product_id=? AND external_sku_id=''
                            AND external_sync_status='stock_synced'""",(p.get('id'),)).fetchone())


def owned(c, product_id):
    p=c.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone()
    if not p: raise LookupError('商品不存在')
    p=dict(p)
    # 2026-11-05 PEAKTRACE 全量直连：镜像库存已落到本地可售库位，走的是同一套
    # reserve / 结算 / 出库链路，因此允许进入平台仓履约；外部品牌仍由上游主数据驱动。
    if mirrored(c,p): return p
    brand=c.execute('SELECT source_type FROM product_brands WHERE id=?',(p.get('brand_id'),)).fetchone()
    if (brand and brand[0]=='external_api') or p.get('source')=='external_api' or p.get('inventory_mode')!='platform_owned' or p.get('fulfillment_mode')!='platform_warehouse':
        raise ValueError('外部品牌由品牌独立库存和履约，不能进入平台仓；真实接口尚未接通')
    return p


def sku(c, product_id, variant_id=None):
    owned(c,product_id)
    if variant_id is None:
        vs=c.execute("SELECT id FROM product_variants WHERE product_id=? AND status='active'",(product_id,)).fetchall()
        if len(vs)!=1: raise ValueError('请明确选择颜色/尺码 SKU，不能按商品总量操作')
        variant_id=vs[0]['id']
    v=variant_of(c,product_id=product_id,variant_id=variant_id)
    if not v: raise ValueError('请先建立商品 SKU')
    if c.execute('SELECT 1 FROM stock_reconciliation WHERE product_id=? AND status=?',(product_id,'pending')).fetchone():
        raise ValueError('历史库存规格/库位待核对，请先完成实盘核对，原账已保留')
    return v


def migrate_product(c,pid):
    # Only one-SKU legacy stock can be assigned without inventing a size/location distribution.
    if c.execute('SELECT 1 FROM warehouse_sku_balances WHERE product_id=?',(pid,)).fetchone(): return
    if c.execute('SELECT 1 FROM stock_reconciliation WHERE product_id=?',(pid,)).fetchone(): return
    p=dict(c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone())
    if p.get('inventory_mode')!='platform_owned' or p.get('source')=='external_api': return
    rows=[dict(x) for x in c.execute('SELECT * FROM warehouse_inventory WHERE product_id=?',(pid,))]
    if not rows:return
    vs=c.execute("SELECT id FROM product_variants WHERE product_id=? AND status='active'",(pid,)).fetchall()
    if len(vs)!=1:
        if any(x['on_hand'] or x['reserved'] for x in rows):
            c.execute('INSERT INTO stock_reconciliation(product_id,snapshot_json) VALUES(?,?)',(pid,json.dumps(rows)))
        return
    vid=vs[0]['id']
    for r in rows:
        c.execute('INSERT INTO warehouse_sku_balances(product_id,variant_id,location_id,on_hand,reserved,unsellable) VALUES(?,?,?,?,?,?)',
                  (pid,vid,r['location_id'],r['on_hand'],r['reserved'],r.get('unsellable',0)))
    for r in c.execute('SELECT * FROM warehouse_reservations WHERE product_id=?',(pid,)).fetchall():
        c.execute('INSERT OR IGNORE INTO warehouse_sku_reservations(order_id,product_id,variant_id,quantity,status,allocations_json) VALUES(?,?,?,?,?,?)',
                  (r['order_id'],pid,vid,r['quantity'],r['status'],r['allocations_json']))
    sync(c,pid)


def balance(c,pid,vid,lid):
    owned(c,pid)
    loc=c.execute("SELECT l.* FROM warehouse_locations l JOIN warehouses w ON w.id=l.warehouse_id WHERE l.id=? AND l.status='active' AND w.status='active'",(lid,)).fetchone()
    if not loc:raise ValueError('库位不存在或仓库/库位已停用')
    c.execute('INSERT OR IGNORE INTO warehouse_sku_balances(product_id,variant_id,location_id) VALUES(?,?,?)',(pid,vid,lid))
    return dict(c.execute('SELECT * FROM warehouse_sku_balances WHERE variant_id=? AND location_id=?',(vid,lid)).fetchone())


def update(c,r,on_hand,reserved,unsellable=None):
    u=r['unsellable'] if unsellable is None else unsellable
    if min(on_hand,reserved,u)<0 or reserved+u>on_hand:raise ValueError('实物数量不能小于锁定与不可售数量之和')
    c.execute('UPDATE warehouse_sku_balances SET on_hand=?,reserved=?,unsellable=? WHERE id=?',(on_hand,reserved,u,r['id']))


def sync(c,pid):
    # Product WMS is a projection only. Never use it to allocate stock back to a SKU.
    for r in c.execute('SELECT location_id,SUM(on_hand) h,SUM(reserved) r,SUM(unsellable) u FROM warehouse_sku_balances WHERE product_id=? GROUP BY location_id',(pid,)).fetchall():
        c.execute('INSERT OR IGNORE INTO warehouse_inventory(product_id,location_id) VALUES(?,?)',(pid,r['location_id']))
        c.execute('UPDATE warehouse_inventory SET on_hand=?,reserved=?,unsellable=?,updated_at=CURRENT_TIMESTAMP WHERE product_id=? AND location_id=?',(r['h'],r['r'],r['u'],pid,r['location_id']))
    for v in c.execute('SELECT id FROM product_variants WHERE product_id=?',(pid,)).fetchall():
        q=c.execute("""SELECT COALESCE(SUM(b.on_hand-b.reserved-b.unsellable),0) FROM warehouse_sku_balances b
          JOIN warehouse_locations l ON l.id=b.location_id JOIN warehouses w ON w.id=l.warehouse_id
          WHERE b.variant_id=? AND l.zone_type='sellable' AND l.status='active' AND w.status='active'""",(v['id'],)).fetchone()[0]
        c.execute('UPDATE product_variants SET stock=? WHERE id=?',(q,v['id']))
    c.execute("UPDATE products SET stock=(SELECT COALESCE(SUM(stock),0) FROM product_variants WHERE product_id=? AND status='active') WHERE id=?",(pid,pid))


def reserve(c,pid,vid,q,*,order_id=None,checkout_id=None):
    sku(c,pid,vid)
    old=c.execute('SELECT * FROM warehouse_sku_reservations WHERE variant_id=? AND (order_id=? OR checkout_id=?)',(vid,order_id,checkout_id)).fetchone()
    if old:
        if old['status']!='reserved' or old['quantity']!=q:raise ValueError('库存预留已失效或数量不一致')
        return dict(old)
    rows=c.execute("""SELECT b.*,(b.on_hand-b.reserved-b.unsellable) available FROM warehouse_sku_balances b
      JOIN warehouse_locations l ON l.id=b.location_id JOIN warehouses w ON w.id=l.warehouse_id
      WHERE b.variant_id=? AND l.zone_type='sellable' AND l.status='active' AND w.status='active'
      AND b.on_hand-b.reserved-b.unsellable>0 ORDER BY l.is_default DESC,l.id""",(vid,)).fetchall()
    if q<=0 or sum(r['available'] for r in rows)<q:raise ValueError('该 SKU 仓库可售库存不足')
    alloc=[];need=q
    for r0 in rows:
        r=dict(r0);take=min(need,r['available']);update(c,r,r['on_hand'],r['reserved']+take)
        alloc.append({'locationId':r['location_id'],'quantity':take});need-=take
        if not need:break
    c.execute('INSERT INTO warehouse_sku_reservations(checkout_id,order_id,product_id,variant_id,quantity,allocations_json) VALUES(?,?,?,?,?,?)',(checkout_id,order_id,pid,vid,q,json.dumps(alloc)))
    rid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    sync(c,pid);mirror_reservations(c,order_id)
    return dict(c.execute('SELECT * FROM warehouse_sku_reservations WHERE id=?',(rid,)).fetchone())


def mirror_reservations(c,oid):
    if not oid:return
    for r in c.execute('SELECT product_id,status,SUM(quantity) q FROM warehouse_sku_reservations WHERE order_id=? GROUP BY product_id,status',(oid,)).fetchall():
        alloc=[]
        for x in c.execute('SELECT allocations_json FROM warehouse_sku_reservations WHERE order_id=? AND product_id=? AND status=?',(oid,r['product_id'],r['status'])):alloc+=json.loads(x[0])
        c.execute('INSERT INTO warehouse_reservations(order_id,product_id,quantity,status,allocations_json) VALUES(?,?,?,?,?) ON CONFLICT(order_id,product_id) DO UPDATE SET quantity=excluded.quantity,status=excluded.status,allocations_json=excluded.allocations_json,updated_at=CURRENT_TIMESTAMP',
                  (oid,r['product_id'],r['q'],r['status'],json.dumps(alloc)))


def release(c,r,quantity=None):
    if r['status']!='reserved':return 0
    need=r['quantity'] if quantity is None else int(quantity);alloc=json.loads(r['allocations_json']);released=0
    for a in alloc:
        take=min(need,a['quantity']);b=balance(c,r['product_id'],r['variant_id'],a['locationId'])
        update(c,b,b['on_hand'],b['reserved']-take);a['quantity']-=take;released+=take;need-=take
    left=r['quantity']-released
    c.execute('UPDATE warehouse_sku_reservations SET quantity=?,status=?,allocations_json=? WHERE id=?',(left,'reserved' if left else 'released',json.dumps([a for a in alloc if a['quantity']]),r['id']))
    sync(c,r['product_id']);mirror_reservations(c,r['order_id']);return released

