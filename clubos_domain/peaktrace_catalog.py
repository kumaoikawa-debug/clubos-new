"""PEAKTRACE public catalog import. This supplies product information, not inventory."""
import hashlib
import math
from urllib.parse import urljoin, urlsplit

import httpx

ORIGIN = 'https://peaktrace-h5.app.workbuddy.host'


def fetch_catalog():
    with httpx.Client(timeout=20) as client:
        products = client.get(ORIGIN + '/api/products')
        products.raise_for_status()
        categories = client.get(ORIGIN + '/api/categories')
        categories.raise_for_status()
    return products.json(), categories.json()


def _image(value):
    if not isinstance(value, str) or not value.strip():
        return None
    url = urljoin(ORIGIN + '/', value.strip())
    return url if urlsplit(url).scheme == 'https' and urlsplit(url).netloc else None


def sync_catalog(c, products, categories):
    if not isinstance(products, list) or not isinstance(categories, list):
        raise ValueError('PEAKTRACE 商品接口格式异常，本次未同步')
    category_names = {str(x['id']): str(x['name']) for x in categories
                      if isinstance(x, dict) and x.get('id') and x.get('name')}
    prepared = []
    seen = set()
    # Validate the entire response before touching the local catalog.
    for x in products:
        if not isinstance(x, dict):
            raise ValueError('PEAKTRACE 商品资料格式异常')
        external_id = str(x.get('id') or '').strip()
        name = str(x.get('name') or '').strip()
        try:
            price = float(x['price'])
        except (KeyError, ValueError, TypeError):
            raise ValueError('PEAKTRACE 商品价格无效')
        if not external_id or len(external_id) > 100 or external_id in seen or not name or not math.isfinite(price) or price < 0:
            raise ValueError('PEAKTRACE 商品编号、名称或价格无效')
        seen.add(external_id)
        if not isinstance(x.get('gallery') or [], list):
            raise ValueError('PEAKTRACE 图片资料格式异常')
        images = list(dict.fromkeys(u for u in [_image(x.get('image'))] +
                                   [_image(v) for v in (x.get('gallery') or [])] if u))
        sizes = x.get('sizes') or []
        if not isinstance(sizes, list) or any(not isinstance(s, str) or not s.strip() for s in sizes):
            raise ValueError('PEAKTRACE 尺码资料格式异常')
        sizes = list(dict.fromkeys(s.strip() for s in sizes)) or ['默认规格']
        description = '\n'.join(str(x[k]).strip() for k in ('sub', 'desc', 'spec') if x.get(k))
        prepared.append((external_id, name, price, images, sizes, description,
                         category_names.get(str(x.get('category')), str(x.get('category') or '装备')),
                         str(x.get('fabric') or ''), str(x.get('mountain') or '')))

    c.execute('BEGIN IMMEDIATE')
    brand = c.execute("SELECT * FROM product_brands WHERE lower(name)='peaktrace'").fetchone()
    if brand and brand['source_type'] != 'external_api':
        raise ValueError('已有 PEAKTRACE 品牌不是外部品牌，请先核对品牌来源，避免覆盖自营商品')
    if not brand:
        c.execute("""INSERT INTO product_brands(name,name_en,slug,source_type,official_website)
                     VALUES('PEAKTRACE','PEAKTRACE','PEAKTRACE','external_api',?)""", (ORIGIN,))
        brand_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    else:
        brand_id = brand['id']
    created = updated = hidden = 0
    ids = []
    for eid, name, price, images, sizes, description, category, material, series in prepared:
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
            c.execute("""INSERT INTO products(name,sku,price,status,brand_id,source,inventory_mode,fulfillment_mode)
                         VALUES(?,?,?,'pending_listing',?,'external_api','external_api','supplier_fulfillment')""",
                      (name, sku, price, brand_id))
            pid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            created += 1
        else:
            pid = p['id']
            updated += 1
        cover = images[0] if images else None
        c.execute("""UPDATE products SET name=?,price=?,description=?,category=?,category_id=?,
                     image_url=?,cover_url=?,material=?,series=? WHERE id=?""",
                  (name, price, description, category, category_id, cover, cover, material, series, pid))
        c.execute('DELETE FROM product_images WHERE product_id=?', (pid,))
        c.executemany('INSERT INTO product_images(product_id,url,sort) VALUES(?,?,?)',
                      [(pid, url, i) for i, url in enumerate(images)])
        # Sizes are display variants, not brand warehouse SKU identifiers.
        c.execute("UPDATE product_variants SET status='inactive' WHERE product_id=?", (pid,))
        for i, size in enumerate(sizes):
            local_sku = sku + '-' + hashlib.sha256(size.encode()).hexdigest()[:8].upper()
            variant = c.execute('SELECT id FROM product_variants WHERE product_id=? AND sku=?', (pid, local_sku)).fetchone()
            if not variant:
                c.execute("""INSERT INTO product_variants(product_id,name,sku,price,stock,sort,is_default,size,inventory_mode,fulfillment_mode)
                             VALUES(?,?,?,?,0,?,?,?,'external_api','supplier_fulfillment')""",
                          (pid, size, local_sku, price, i, int(i == 0), size if size != '默认规格' else None))
            else:
                c.execute("UPDATE product_variants SET name=?,price=?,sort=?,is_default=?,status='active' WHERE id=?",
                          (size, price, i, int(i == 0), variant['id']))
        c.execute("""INSERT INTO external_product_mappings(brand_id,external_product_id,external_sku_id,product_id,external_sync_status)
                     VALUES(?,?,'',?,'catalog_only') ON CONFLICT(brand_id,external_product_id,external_sku_id)
                     DO UPDATE SET product_id=excluded.product_id,external_sync_status='catalog_only'""", (brand_id, eid, pid))
        ids.append(pid)
    for r in c.execute("""SELECT m.external_product_id,p.id,p.status FROM external_product_mappings m
                          JOIN products p ON p.id=m.product_id WHERE m.brand_id=? AND m.external_sku_id=''
                          AND m.external_sync_status='catalog_only'""", (brand_id,)).fetchall():
        if r['external_product_id'] not in seen and r['status'] in ('active', 'pending_listing'):
            c.execute("UPDATE products SET status='inactive' WHERE id=?", (r['id'],))
            hidden += 1
        if r['external_product_id'] not in seen:
            c.execute("UPDATE external_product_mappings SET external_sync_status='catalog_unavailable' WHERE brand_id=? AND external_product_id=? AND external_sku_id=''", (brand_id, r['external_product_id']))
    return {'ok': True, 'brandId': brand_id, 'created': created, 'updated': updated,
            'hidden': hidden, 'productIds': ids, 'inventoryConnected': False}
