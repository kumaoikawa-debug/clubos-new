"""PEAKTRACE 主数据全量镜像回归（隔离库；不发起真实商户/支付/物流请求）。

2026-11-05 起行为变更：不再只同步展示资料，而是把「上游已上架商品 + 每个 SKU 的
可售库存」镜像进本地可售仓，让 C 端真正可购买。本文件按新口径重写断言。
"""
import copy
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ.update(CLUBOS_TEST_OPEN='0', CLUBOS_SECURITY_MODE='demo', MOCK_AI='1',
                  COMMERCE_PROVIDER='local', CLUBOS_DB_PATH=str(Path(tempfile.mkdtemp()) / 'catalog.db'))
import app
from fastapi.testclient import TestClient
from security_v025 import _hash

client = TestClient(app.app, raise_server_exceptions=False)
headers = {}
with app.conn() as db:
    for department in ('operations', 'warehouse', 'all'):
        db.execute("INSERT INTO auth_accounts(username,password_hash,role,platform_department) VALUES(?,'test','platform',?)", (department, department))
        aid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        token = secrets.token_urlsafe(32)
        db.execute('INSERT INTO auth_sessions(token_hash,account_id,csrf_hash,expires_at) VALUES(?,?,?,?)', (_hash(token), aid, _hash('test'), int(time.time()) + 3600))
        headers[department] = {'Authorization': 'Bearer ' + token}
    db.execute("INSERT INTO user_addresses(user_id,consignee,phone,province,city,district,detail) VALUES(1,'测试','13800000001','四川','成都','高新','测试地址')")
    address = db.execute('SELECT last_insert_rowid()').fetchone()[0]

feed = [{'id': 'tee-1', 'name': '品牌测试T恤', 'price': 199, 'category': 'tee',
         'image': '/assets/tee.jpg', 'gallery': ['/assets/tee.jpg', '/assets/detail.jpg'],
         'sizes': ['S', 'M'], 'desc': '品牌商品介绍', 'fabric': '棉',
         'skus': [{'sku': 'tee-1|S', 'size': 'S', 'available': 6},
                  {'sku': 'tee-1|M', 'size': 'M', 'available': 4}]}]
categories = [{'id': 'tee', 'name': '山系T恤'}]
inventory = {'tee-1|S': 6, 'tee-1|M': 4}


def payload():
    return {'products': copy.deepcopy(feed), 'categories': categories,
            'inventory': dict(inventory), 'source': 'b2b', 'origin': app.peaktrace_catalog.ORIGIN}


app.peaktrace_catalog.fetch_catalog = payload
checks = []


def check(name, condition):
    assert condition, name
    checks.append(name)
    print('PASS', name, flush=True)


def call(method, url, body=None, status=200, department='operations'):
    r = client.request(method, url, json=body, headers=headers[department])
    assert r.status_code == status, (url, r.status_code, r.text[:500])
    return r.json()


url = '/api/platform/brands/peaktrace/sync'
check('anonymous sync rejected', client.post(url).status_code == 401)
call('POST', url, status=403, department='warehouse')
check('warehouse cannot sync catalog', True)

sync = call('POST', url)
check('inventory is connected', sync['inventoryConnected'] and sync['source'] == 'b2b' and sync['skuCount'] == 2)
check('one product auto-published as sellable', sync['created'] == 1)
pid = sync['productIds'][0]
check('new upstream product is active immediately',
      next(p for p in call('GET', '/api/platform/products') if p['id'] == pid)['status'] == 'active')

p = call('GET', f'/api/public/clubs/1/mall/products/{pid}')
check('published product is purchasable', (not p['displayOnly']) and p['purchaseAvailable'] and p['externalMirrored'])
check('stock mirrors upstream by SKU', p['stock'] == 10 and [v['stock'] for v in p['variants']] == [6, 4])
check('absolute images and actual sizes exposed',
      p['images'][1]['url'].startswith(app.peaktrace_catalog.ORIGIN) and p['sizeOptions'] == ['S', 'M'])

st = call('GET', '/api/platform/brands/peaktrace/status')
check('status endpoint reports connected mirror', st['inventoryConnected'] and st['syncedProducts'] == 1 and st['lastSyncAt'])

# 上游调价 + 改库存 → 下次同步全量对齐
feed[0]['price'] = 219
feed[0]['skus'][0]['available'] = 3
inventory['tee-1|S'] = 3
sync = call('POST', url)
check('repeat sync keeps stable IDs', sync['created'] == 0 and sync['updated'] == 1 and sync['productIds'] == [pid])
p = call('GET', f'/api/public/clubs/1/mall/products/{pid}')
check('price and stock refreshed from upstream', p['price'] == 219 and p['stock'] == 7)
with app.conn() as db:
    check('no duplicate images or variants',
          db.execute('SELECT COUNT(*) FROM product_images WHERE product_id=?', (pid,)).fetchone()[0] == 2
          and db.execute('SELECT COUNT(*) FROM product_variants WHERE product_id=?', (pid,)).fetchone()[0] == 2)
    check('mirror warehouse balance exists',
          db.execute("""SELECT COUNT(*) FROM warehouse_sku_balances b JOIN warehouse_locations l ON l.id=b.location_id
                        WHERE b.product_id=? AND l.code='PT-SELLABLE'""", (pid,)).fetchone()[0] == 2)
    check('inventory sync log written',
          db.execute('SELECT COUNT(*) FROM external_inventory_sync_logs').fetchone()[0] >= 1)
    check('sync timestamp recorded',
          db.execute("""SELECT last_inventory_sync_at FROM external_product_mappings
                        WHERE product_id=? AND external_sku_id=''""", (pid,)).fetchone()[0] is not None)
    check('per-SKU upstream mapping exists',
          db.execute("""SELECT COUNT(*) FROM external_product_mappings
                        WHERE product_id=? AND external_sku_id!='' AND external_sync_status='stock_synced'""",
                     (pid,)).fetchone()[0] == 2)

# 真正下单：镜像商品走的是平台仓同一套 reserve 链路
variants = p['variants']
co = call('POST', '/api/public/clubs/1/gear-checkout',
          {'userId': 1, 'addressId': address,
           'items': [{'productId': pid, 'variantId': variants[0]['id'], 'quantity': 1}]})
check('mirrored product can be checked out', bool(co.get('checkoutId')))
with app.conn() as db:
    check('checkout reserves mirror stock',
          db.execute('SELECT COUNT(*) FROM warehouse_sku_reservations WHERE product_id=?', (pid,)).fetchone()[0] == 1)
call('POST', f'/api/public/checkouts/{co["checkoutId"]}/cancel')
call('PATCH', f'/api/platform/products/{pid}', {'stock': 99}, status=409, department='all')
check('platform cannot overwrite upstream stock', True)

# 人工下架要被尊重；上游重新上架后自动复活
call('PATCH', f'/api/platform/products/{pid}', {'status': 'inactive'})
call('POST', url)
check('manual hide survives a sync',
      next(p for p in call('GET', '/api/platform/products') if p['id'] == pid)['status'] == 'inactive')
call('PATCH', f'/api/platform/products/{pid}', {'status': 'active'})

# 上游下架 → 本地下架且不允许手工复活
app.peaktrace_catalog.fetch_catalog = lambda: {'products': [], 'categories': categories,
                                               'inventory': {}, 'source': 'b2b',
                                               'origin': app.peaktrace_catalog.ORIGIN}
sync = call('POST', url)
check('upstream removal hides without deleting local data',
      sync['hidden'] == 1 and any(p['id'] == pid for p in call('GET', '/api/platform/products')))
call('PATCH', f'/api/platform/products/{pid}', {'status': 'active'}, status=409)
check('upstream removed product cannot be republished', True)
app.peaktrace_catalog.fetch_catalog = payload
call('POST', url)
check('upstream relisting auto-republishes',
      next(p for p in call('GET', '/api/platform/products') if p['id'] == pid)['status'] == 'active')

# 脏数据整包拒绝，本地数据一件不改
bad = copy.deepcopy(feed[0]); bad['id'] = 'invalid'; bad['price'] = 'broken'; feed.append(bad)
call('POST', url, status=409)
check('invalid upstream data changes nothing',
      call('GET', f'/api/public/clubs/1/mall/products/{pid}')['price'] == 219)
feed.pop()
bad_stock = dict(inventory); bad_stock['tee-1|S'] = 'oops'
app.peaktrace_catalog.fetch_catalog = lambda: {'products': copy.deepcopy(feed), 'categories': categories,
                                               'inventory': bad_stock, 'source': 'b2b',
                                               'origin': app.peaktrace_catalog.ORIGIN}
call('POST', url, status=409)
check('invalid upstream stock changes nothing',
      call('GET', f'/api/public/clubs/1/mall/products/{pid}')['stock'] == 7)

app.peaktrace_catalog.fetch_catalog = lambda: (_ for _ in ()).throw(RuntimeError('network failure'))
call('POST', url, status=502)
check('network failure preserves last valid catalog',
      call('GET', f'/api/public/clubs/1/mall/products/{pid}')['price'] == 219)
app.peaktrace_catalog.fetch_catalog = payload
app.commerce_provider = lambda: 'medusa'
check('mirrored catalog never pushed into Medusa inventory', app._sync_product_to_medusa(pid)['skipped'])
print('PEAKTRACE CATALOG OK', len(checks), 'checks')
