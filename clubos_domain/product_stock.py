"""SKU stock projection helpers. warehouse_sku_balances owns physical stock; product stocks are read-compatible projections."""
from __future__ import annotations


def variant_of(c, *, product_id:int, variant_id:int|None=None):
    """确定这次库存变动要落到哪个规格。

    没传 variant_id 时取该商品的默认规格（迁移给每个商品建的那个 is_default=1），
    这样单规格与存量商品的语义和原来完全一致。

    **只取在售规格**：商品一旦有了真实规格，迁移生成的那个占位规格会被停用，
    而汇总只累加 status='active' 的规格。若这里不排除停用的占位规格，采购入库
    补的货就会落到一个不计入汇总的规格上 —— 库存凭空消失，且查不出去了哪儿。
    （采购明细没有规格字段，所以补货一律落到第一个在售规格，随后由运营在各规格间分配。）

    多规格商品的调用方仍应显式传 —— 否则「买 S 码扣 M 码库存」这种串账查不出来。
    """
    if variant_id:
        r=c.execute('SELECT * FROM product_variants WHERE id=? AND product_id=?',(int(variant_id),int(product_id))).fetchone()
        if not r: raise LookupError('规格不存在或不属于该商品')
        return dict(r)
    r=c.execute("SELECT * FROM product_variants WHERE product_id=? AND status='active' ORDER BY is_default DESC,sort,id LIMIT 1",(int(product_id),)).fetchone()
    if not r:  # 全部规格都被停用（商品下架）时退回取第一个，让调用方拿到行而不是 None
        r=c.execute('SELECT * FROM product_variants WHERE product_id=? ORDER BY is_default DESC,sort,id LIMIT 1',(int(product_id),)).fetchone()
    return dict(r) if r else None


def write_variant_stock(c, *, product_id:int, variant_id:int, new_stock:int, sync_status:str|None=None) -> int:
    """写规格层库存，并把 products.stock 重算成各规格之和。

    products.stock 从真源降级为冗余字段（列表排序、采购预警读它），所以它必须
    每次规格变动后跟着变，否则后台看到的汇总和详情页看到的规格明细会对不上。
    返回写入前的规格库存，供调用方记流水。
    """
    v=c.execute('SELECT id,stock FROM product_variants WHERE id=?',(int(variant_id),)).fetchone()
    if not v: raise LookupError('规格不存在')
    before=int(v['stock'] or 0)
    c.execute('UPDATE product_variants SET stock=? WHERE id=?',(int(new_stock),int(variant_id)))
    total=c.execute("SELECT COALESCE(SUM(stock),0) s FROM product_variants WHERE product_id=? AND status='active'",(int(product_id),)).fetchone()['s']
    if sync_status:
        c.execute('UPDATE products SET stock=?,commerce_sync_status=? WHERE id=?',(int(total),sync_status,int(product_id)))
    else:
        c.execute('UPDATE products SET stock=? WHERE id=?',(int(total),int(product_id)))
    return before


def resync_product_stock(c, *, product_id:int) -> int:
    """把 products.stock 重算成各在售规格库存之和（防御性重算）。

    采购入库、销售出库等关键路径都走 write_variant_stock 自动重算，但历史代码里
    仍有「直接 UPDATE products.stock」的旁路（无规格商品入库、售后回库等），
    任何一条漏掉都会让列表汇总和规格明细对不上。新增产品在外部手工改过库存后，
    统一用本函数兜底一次，代价只是一次聚合查询，可反复调用。
    """
    total=c.execute("SELECT COALESCE(SUM(stock),0) s FROM product_variants WHERE product_id=? AND status='active'",(int(product_id),)).fetchone()['s']
    c.execute('UPDATE products SET stock=? WHERE id=?',(int(total),int(product_id)))
    return int(total)


# ── 库存真源反转（进销存 V1.2，2026-10-31）────────────────────────────────────
# 口径变更：warehouse_inventory.on_hand 是「实物真源」（收货/发货/盘点/调整只认它），
# product_variants.stock 降级为「可售派生」= WMS 可售总额（Σon_hand−Σreserved−Σunsellable）
# 按业务事件分摊到规格。派生重算的触发点：
#   采购收货 / 销售出库(锁库) / 退款回库 / 售后回库 / 手工调整 / 采购退货出库 /
#   盘点 / 订单发货 —— 全部走 resync_sellable_from_wms 收口。
def wms_sellable_total(c, *, product_id:int) -> int:
    """WMS 侧可售总额 = Σ(on_hand − reserved − unsellable)，跨全部库位。

    reserved（锁库）与 unsellable（不可售）都已从实物里扣掉，剩下的才是 C 端能卖的。"""
    r=c.execute('SELECT COALESCE(SUM(on_hand-reserved-unsellable),0) s FROM warehouse_inventory WHERE product_id=?',(int(product_id),)).fetchone()
    return max(0,int(r['s'] or 0))


def resync_sellable_from_wms(c, *, product_id:int) -> dict:
    from . import stock_control as sc
    p=dict(c.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone())
    if p.get('inventory_mode')!='platform_owned' or p.get('source')=='external_api':
        return {'resynced':False,'external':True}
    if c.execute('SELECT 1 FROM stock_reconciliation WHERE product_id=? AND status="pending"',(product_id,)).fetchone():
        return {'resynced':False,'pendingReconciliation':True}
    sc.sync(c,product_id)
    total=c.execute('SELECT stock FROM products WHERE id=?',(product_id,)).fetchone()[0]
    return {'wmsSellable':total,'variantSum':total,'drift':0,'resynced':True}
