"""规格层库存的读写口（v0.27）。

库存真源从 products.stock 下沉到 product_variants.stock 之后，所有写库存的地方
都必须经过这里：它负责写规格、并把 products.stock 重算成各规格之和。

单独成模块而不是挂在 ProcurementEngine 上，是因为采购（procurement）和仓储
（warehouse）都要用，互相 import 会绕成循环依赖。
"""
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
