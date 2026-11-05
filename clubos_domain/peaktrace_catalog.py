"""PEAKTRACE 商品全量镜像（2026-11-05 起）。

上游是唯一主数据：PEAKTRACE 上架什么，ClubOS 就上架并可卖什么；名称 / 价格 /
图集 / 规格 / 描述 / 面料 / 上架状态 / 每个 SKU 的可售库存全部以上游为准。

设计要点
--------
* 一次拿齐：优先走 PEAKTRACE 的 ``GET /api/b2b/catalog``（已上架商品 + 按 SKU 库存）。
  线上旧版还没这个接口时，自动回退到 ``GET /api/products`` + ``POST
  /api/admin/inventory/get``（令牌必须放 JSON body —— 托管网关会剥掉自定义头与查询串）。
* 镜像仓而不是「展示型商品」：上游库存写进独立的 ``PT-MIRROR`` 可售库位，
  因此 ``stock_control.reserve()`` / 结算 / 出库 / 退款这些已经验过的链路原样复用，
  不需要为外部品牌另写一套库存逻辑。
* 只改「可售量」不抹「已预留」：同步时按 ``上游 available - 当前 available`` 做增量，
  保留 reserved / unsellable，避免把顾客正在付款的预留冲掉。
* 整包校验、整包落库：上游任何一条数据不合法就整体抛错，本地商品一件都不改。
* 幂等：按 ``external_product_id`` 稳定映射，重复同步不产生新商品 / 新图 / 新 SKU。
* 上游下架 → 本地 inactivate；之后重新上架会自动复活。人工在平台端下架的商品，
  上游没变时不会被强行翻回 active。

边界（需要业务确认后再做）：ClubOS 的订单目前**不回写** PEAKTRACE，两边各自扣减
自己的库存。若要彻底避免超卖，下一步需要接 PEAKTRACE 的 B2B 下单 / 库存占用接口。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from urllib.parse import urljoin, urlsplit

import httpx

from . import stock_control as sc

ORIGIN = os.environ.get('PEAKTRACE_ORIGIN', 'https://peaktrace-h5.app.workbuddy.host').rstrip('/')

# 镜像商品的库存归属。inventory_mode 刻意不叫 platform_owned：它仍是外部品牌，
# 只是库存已经落到本地可售仓，stock_control.owned() 只对这一组合放行。
MIRROR_MODE = 'external_mirror'
MIRROR_FULFILLMENT = 'platform_warehouse'
MIRROR_WH_CODE = 'PT-MIRROR'
MIRROR_LOC_CODE = 'PT-SELLABLE'
SYNCED = 'stock_synced'
UNAVAILABLE = 'catalog_unavailable'


def fetch_catalog():
    """读取上游主数据，返回统一 payload dict（不碰本地数据库）。"""
    with httpx.Client(timeout=20) as client:
        # 首选：PEAKTRACE B2B 只读目录 —— 一次拿齐已上架商品 + 按 SKU 可售库存。
        try:
            r = client.get(ORIGIN + '/api/b2b/catalog')
            if r.status_code == 200:
                d = r.json()
                if (isinstance(d, dict) and isinstance(d.get('products'), list)
                        and isinstance(d.get('categories'), list)):
                    inv = {}
                    for p in d['products']:
                        if not isinstance(p, dict):
                            continue
                        for s in (p.get('skus') or []):
                            if isinstance(s, dict) and s.get('sku'):
                                inv[str(s['sku'])] = s.get('available')
                    return {'products': d['products'], 'categories': d['categories'],
                            'inventory': inv, 'source': 'b2b', 'origin': ORIGIN,
                            'generatedAt': d.get('generatedAt')}
        except Exception:
            pass  # 老版本线上实例没有 B2B 接口，走下面的兼容路径

        products = client.get(ORIGIN + '/api/products')
        products.raise_for_status()
        categories = client.get(ORIGIN + '/api/categories')
        categories.raise_for_status()
        token = os.environ.get('PEAKTRACE_API_TOKEN', 'peaktrace-admin')
        inv = {}
        try:
            # 网关剥离自定义头与查询串，管理令牌只能放 JSON body；库读取失败按「未开放库存」处理，
            # 不因此中断整次同步（商品资料仍然先接通）。
            ir = client.post(ORIGIN + '/api/admin/inventory/get', json={'token': token})
            if ir.status_code == 200 and isinstance(ir.json(), dict):
                inv = {str(k): (v.get('available') if isinstance(v, dict) else v)
                       for k, v in ir.json().items()}
        except Exception:
            inv = {}
        return {'products': products.json(), 'categories': categories.json(),
                'inventory': inv, 'source': 'legacy', 'origin': ORIGIN}


def _image(value, origin):
    if not isinstance(value, str) or not value.strip():
        return None
    url = urljoin(origin + '/', value.strip())
    sp = urlsplit(url)
    if sp.scheme == 'http' and sp.hostname in ('127.0.0.1', 'localhost'):
        return url if sp.netloc else None  # 本地联调
    return url if sp.scheme == 'https' and sp.netloc else None


def _catalog_view(value):
    """头图裁切区 [srcW,srcH,cropX,cropY,cropW,cropH]，原样存成 JSON 数组字符串。

    官网商品图是「多视图合成图」，前端按这组坐标用 SVG viewBox 只露头图那一张。
    格式不合法就返回 None（前端退回整图渲染），**不因它打断整次同步**。
    """
    if not isinstance(value, (list, tuple)) or len(value) != 6:
        return None
    try:
        nums = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in nums):
        return None
    src_w, src_h, cx, cy, cw, ch = nums
    if min(src_w, src_h, cw, ch) <= 0 or cx < 0 or cy < 0:
        return None
    if cx + cw > src_w + 0.5 or cy + ch > src_h + 0.5:
        return None
    return json.dumps(nums)


def _coerce_available(value, eid, size):
    """上游库存必须是非负整数；没配置视为 0（不开放售卖），格式非法则整包失败。"""
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('PEAKTRACE 库存格式无效：%s / %s' % (eid, size))
    if not math.isfinite(float(value)) or value < 0:
        return 0
    if float(value) != int(value):
        raise ValueError('PEAKTRACE 库存不是整数：%s / %s' % (eid, size))
    return int(value)


def _prepare(products, categories, inventory, origin):
    """全量校验上游响应，返回可落库的记录；任何一条异常都直接抛错。"""
    if not isinstance(products, list) or not isinstance(categories, list):
        raise ValueError('PEAKTRACE 商品接口格式异常，本次未同步')
    if not isinstance(inventory, dict):
        raise ValueError('PEAKTRACE 库存接口格式异常，本次未同步')
    category_names = {str(x['id']): str(x['name']) for x in categories
                      if isinstance(x, dict) and x.get('id') and x.get('name')}
    prepared = []
    seen = set()
    for x in products:
        if not isinstance(x, dict):
            raise ValueError('PEAKTRACE 商品资料格式异常')
        external_id = str(x.get('id') or '').strip()
        name = str(x.get('name') or '').strip()
        try:
            price = float(x['price'])
        except (KeyError, ValueError, TypeError):
            raise ValueError('PEAKTRACE 商品价格无效')
        if (not external_id or len(external_id) > 100 or external_id in seen or not name
                or not math.isfinite(price) or price <= 0):
            raise ValueError('PEAKTRACE 商品编号、名称或价格无效')
        seen.add(external_id)
        if not isinstance(x.get('gallery') or [], list):
            raise ValueError('PEAKTRACE 图片资料格式异常')
        images = list(dict.fromkeys(u for u in [_image(x.get('image'), origin)] +
                                   [_image(v, origin) for v in (x.get('gallery') or [])] if u))
        raw_sizes = x.get('sizes')
        if not raw_sizes and isinstance(x.get('skus'), list):
            raw_sizes = [s.get('size') for s in x['skus']
                         if isinstance(s, dict) and str(s.get('size') or '').strip()]
        if raw_sizes is None:
            raw_sizes = []
        if not isinstance(raw_sizes, list) or any(not isinstance(s, str) or not s.strip() for s in raw_sizes):
            raise ValueError('PEAKTRACE 尺码资料格式异常')
        # 无尺码商品用上游 spec（PEAKTRACE 的 skuKey 就是 id|spec 或 id|均码），
        # 这样库存 key 与上游 /api/admin/inventory/get 完全对齐。
        sizes = list(dict.fromkeys(s.strip() for s in raw_sizes)) or [str(x.get('spec') or '').strip() or '均码']
        sku_rows = []
        for size in sizes:
            key = '%s|%s' % (external_id, size)
            sku_rows.append((size, key, _coerce_available(inventory.get(key), external_id, size)))
        description = '\n'.join(str(x[k]).strip() for k in ('sub', 'desc', 'spec') if x.get(k))
        prepared.append((external_id, name, price, images, sku_rows, description,
                         category_names.get(str(x.get('category')), str(x.get('category') or '装备')),
                         str(x.get('fabric') or ''), str(x.get('mountain') or ''),
                         _catalog_view(x.get('catalog_view'))))
    return prepared, seen


def _ensure_mirror_location(c):
    """独立的 PEAKTRACE 镜像仓 / 可售库位，避免和平台自营实物库存混账。"""
    wh = c.execute('SELECT id FROM warehouses WHERE code=?', (MIRROR_WH_CODE,)).fetchone()
    if not wh:
        c.execute("""INSERT INTO warehouses(code,name,address,status,is_default)
                     VALUES(?,?,?,'active',0)""",
                  (MIRROR_WH_CODE, 'PEAKTRACE 镜像仓', '上游 PEAKTRACE 可售库存镜像（自动同步）'))
        wid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    else:
        wid = wh[0]
        c.execute("UPDATE warehouses SET status='active' WHERE id=?", (wid,))
    loc = c.execute('SELECT id FROM warehouse_locations WHERE warehouse_id=? AND code=?',
                    (wid, MIRROR_LOC_CODE)).fetchone()
    if not loc:
        c.execute("""INSERT INTO warehouse_locations(warehouse_id,code,name,zone_type,status,is_default)
                     VALUES(?,?,?,'sellable','active',0)""",
                  (wid, MIRROR_LOC_CODE, 'PEAKTRACE 可售镜像库位'))
        lid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    else:
        lid = loc[0]
        c.execute("UPDATE warehouse_locations SET status='active',zone_type='sellable' WHERE id=?", (lid,))
    return wid, lid


def _mirror_balance(c, pid, vid, lid, available):
    """只把「可售量」对齐上游，保留本地 reserved / unsellable。"""
    b = c.execute('SELECT * FROM warehouse_sku_balances WHERE variant_id=? AND location_id=?',
                  (vid, lid)).fetchone()
    if not b:
        c.execute("""INSERT INTO warehouse_sku_balances
                     (product_id,variant_id,location_id,on_hand,reserved,unsellable)
                     VALUES(?,?,?,?,0,0)""", (pid, vid, lid, available))
        return
    on_hand = int(b['on_hand'] or 0)
    reserved = int(b['reserved'] or 0)
    unsellable = int(b['unsellable'] or 0)
    current = max(0, on_hand - reserved - unsellable)
    new_on_hand = max(reserved + unsellable, on_hand + (available - current))
    c.execute('UPDATE warehouse_sku_balances SET on_hand=? WHERE id=?', (new_on_hand, b['id']))


def sync_catalog(c, payload, categories=None):
    """把上游主数据镜像进本地。payload 为 fetch_catalog() 的 dict；

    兼容旧调用/旧测试的两元组 ``(products, categories)`` —— 那种情况没有库存，
    按「上游未开放库存」全 0 处理，商品仍会接通但暂时售罄。
    """
    if isinstance(payload, (tuple, list)) and len(payload) == 2 and not isinstance(payload, dict):
        products, categories = payload
        payload = {'products': products, 'categories': categories or [], 'inventory': {},
                   'source': 'legacy', 'origin': ORIGIN}
    if not isinstance(payload, dict):
        raise ValueError('PEAKTRACE 同步数据格式异常')
    products = payload.get('products')
    categories = payload.get('categories') if categories is None else categories
    inventory = payload.get('inventory') or {}
    origin = str(payload.get('origin') or ORIGIN).rstrip('/')
    source = str(payload.get('source') or 'legacy')
    prepared, seen = _prepare(products, categories, inventory, origin)

    c.execute('BEGIN IMMEDIATE')
    brand = c.execute("SELECT * FROM product_brands WHERE lower(name)='peaktrace'").fetchone()
    if brand and brand['source_type'] != 'external_api':
        raise ValueError('已有 PEAKTRACE 品牌不是外部品牌，请先核对品牌来源，避免覆盖自营商品')
    if not brand:
        c.execute("""INSERT INTO product_brands(name,name_en,slug,source_type,official_website)
                     VALUES('PEAKTRACE','PEAKTRACE','PEAKTRACE','external_api',?)""", (origin,))
        brand_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    else:
        brand_id = brand['id']
    wid, lid = _ensure_mirror_location(c)
    created = updated = hidden = sku_count = 0
    ids = []
    for eid, name, price, images, sku_rows, description, category, material, series, catalog_view in prepared:
        mapping = c.execute("""SELECT * FROM external_product_mappings WHERE brand_id=?
                               AND external_product_id=? AND external_sku_id=''""", (brand_id, eid)).fetchone()
        p = c.execute('SELECT * FROM products WHERE id=?', (mapping['product_id'],)).fetchone() if mapping else None
        if p and (p['source'] != 'external_api' or p['brand_id'] != brand_id):
            raise ValueError('PEAKTRACE 商品映射与库存归属冲突，请先核对')
        cat = c.execute('SELECT id FROM product_categories WHERE name=? ORDER BY id LIMIT 1', (category,)).fetchone()
        if not cat:
            c.execute('INSERT INTO product_categories(name) VALUES(?)', (category,))
            category_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        else:
            category_id = cat['id']
        sku = 'PT-' + hashlib.sha256(eid.encode()).hexdigest()[:16].upper()
        if not p:
            c.execute("""INSERT INTO products
                         (name,sku,price,status,brand_id,source,inventory_mode,fulfillment_mode)
                         VALUES(?,?,?,'active',?,'external_api',?,?)""",
                      (name, sku, price, brand_id, MIRROR_MODE, MIRROR_FULFILLMENT))
            pid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            new_status = 'active'   # 上游已发布 → 直接可售，不再停在待上架
            created += 1
        else:
            pid = p['id']
            if p['status'] in ('active', 'pending_listing'):
                new_status = 'active'
            elif p['status'] == 'inactive' and mapping and mapping['external_sync_status'] == UNAVAILABLE:
                new_status = 'active'   # 上游下架后重新上架 → 自动复活
            else:
                new_status = p['status']   # 平台端人工下架，上游没变就不强行翻回来
            updated += 1
        c.execute("""UPDATE products SET name=?,price=?,description=?,category=?,category_id=?,
                     image_url=?,cover_url=?,material=?,series=?,catalog_view=?,
                     source='external_api',inventory_mode=?,fulfillment_mode=?,status=? WHERE id=?""",
                  (name, price, description, category, category_id,
                   images[0] if images else None, images[0] if images else None,
                   material, series, catalog_view, MIRROR_MODE, MIRROR_FULFILLMENT, new_status, pid))
        c.execute('DELETE FROM product_images WHERE product_id=?', (pid,))
        c.executemany('INSERT INTO product_images(product_id,url,sort) VALUES(?,?,?)',
                      [(pid, url, i) for i, url in enumerate(images)])
        c.execute("UPDATE product_variants SET status='inactive' WHERE product_id=?", (pid,))
        for i, (size, ext_sku, available) in enumerate(sku_rows):
            local_sku = sku + '-' + hashlib.sha256(size.encode()).hexdigest()[:8].upper()
            v = c.execute('SELECT id FROM product_variants WHERE product_id=? AND sku=?',
                          (pid, local_sku)).fetchone()
            if not v:
                c.execute("""INSERT INTO product_variants
                             (product_id,name,sku,price,stock,sort,is_default,size,
                              inventory_mode,fulfillment_mode,status)
                             VALUES(?,?,?,?,0,?,?,?,?,?,'active')""",
                          (pid, size, local_sku, price, i, int(i == 0), size,
                           MIRROR_MODE, MIRROR_FULFILLMENT))
                vid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            else:
                vid = v['id']
                c.execute("""UPDATE product_variants SET name=?,price=?,sort=?,is_default=?,
                             inventory_mode=?,fulfillment_mode=?,status='active' WHERE id=?""",
                          (size, price, i, int(i == 0), MIRROR_MODE, MIRROR_FULFILLMENT, vid))
            _mirror_balance(c, pid, vid, lid, available)
            sku_count += 1
            c.execute("""INSERT INTO external_product_mappings
                         (brand_id,external_product_id,external_sku_id,external_inventory_id,
                          product_id,variant_id,external_sync_status,last_inventory_sync_at)
                         VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                         ON CONFLICT(brand_id,external_product_id,external_sku_id) DO UPDATE SET
                           external_inventory_id=excluded.external_inventory_id,
                           product_id=excluded.product_id,variant_id=excluded.variant_id,
                           external_sync_status=excluded.external_sync_status,
                           last_inventory_sync_at=CURRENT_TIMESTAMP""",
                      (brand_id, eid, size, ext_sku, pid, vid, SYNCED))
        # 变体可售量从镜像库位反算，保证 C 端 stock 与结算 reserve 是同一本账
        sc.sync(c, pid)
        c.execute("""INSERT INTO external_product_mappings
                     (brand_id,external_product_id,external_sku_id,product_id,
                      external_sync_status,last_inventory_sync_at)
                     VALUES(?,?,'',?,?,CURRENT_TIMESTAMP)
                     ON CONFLICT(brand_id,external_product_id,external_sku_id) DO UPDATE SET
                       product_id=excluded.product_id,
                       external_sync_status=excluded.external_sync_status,
                       last_inventory_sync_at=CURRENT_TIMESTAMP""",
                  (brand_id, eid, pid, SYNCED))
        mid = c.execute("""SELECT id FROM external_product_mappings WHERE brand_id=?
                           AND external_product_id=? AND external_sku_id=''""",
                        (brand_id, eid)).fetchone()[0]
        c.execute("""INSERT INTO external_inventory_sync_logs
                     (mapping_id,result,snapshot_json) VALUES(?,'ok',?)""",
                  (mid, json.dumps({'source': source, 'skus': sku_rows}, ensure_ascii=False)))
        ids.append(pid)

    # 上游不再发布的商品：下架并记录，等它重新上架时再自动复活
    for r in c.execute("""SELECT m.external_product_id,p.id,p.status FROM external_product_mappings m
                          JOIN products p ON p.id=m.product_id WHERE m.brand_id=?
                          AND m.external_sku_id=''""", (brand_id,)).fetchall():
        if r['external_product_id'] in seen:
            continue
        if r['status'] in ('active', 'pending_listing'):
            c.execute("UPDATE products SET status='inactive' WHERE id=?", (r['id'],))
            hidden += 1
        c.execute("""UPDATE external_product_mappings SET external_sync_status=?,
                     last_inventory_sync_at=CURRENT_TIMESTAMP WHERE brand_id=?
                     AND external_product_id=? AND external_sku_id=''""",
                  (UNAVAILABLE, brand_id, r['external_product_id']))
    return {'ok': True, 'brandId': brand_id, 'created': created, 'updated': updated,
            'hidden': hidden, 'productIds': ids, 'skuCount': sku_count,
            'inventoryConnected': True, 'source': source, 'origin': origin,
            'warehouseId': wid, 'locationId': lid}
