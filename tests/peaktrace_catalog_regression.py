"""Catalog sync with isolated storage; no merchant, payment or logistics requests."""
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

client = TestClient(app.app)
headers = {}
with app.conn() as db:
    for department in ('operations', 'warehouse', 'all'):
        db.execute("INSERT INTO auth_accounts(username,password_hash,role,platform_department) VALUES(?,'test','platform',?)", (department, department))
        aid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        token = secrets.token_urlsafe(32)
        db.execute('INSERT INTO auth_sessions(token_hash,account_id,csrf_hash,expires_at) VALUES(?,?,?,?)', (_hash(token), aid, _hash('test'), int(time.time()) + 3600))
        headers[department] = {'Authorization': 'Bearer ' + token}

feed = [{'id': 'tee-1', 'name': '品牌测试T恤', 'price': 199, 'category': 'tee',
         'image': '/assets/tee.jpg', 'gallery': ['/assets/tee.jpg', '/assets/detail.jpg'],
         'sizes': ['S', 'M'], 'desc': '品牌商品介绍', 'fabric': '棉'}]
categories = [{'id': 'tee', 'name': '山系T恤'}]
app.peaktrace_catalog.fetch_catalog = lambda: (copy.deepcopy(feed), categories)
checks = []


def check(name, condition):
    assert condition, name
    checks.append(name)
    print('PASS', name)


def call(method, url, body=None, status=200, department='operations'):
    r = client.request(method, url, json=body, headers=headers[department])
    assert r.status_code == status, (url, r.status_code, r.text[:500])
    return r.json()


url = '/api/platform/brands/peaktrace/sync'
check('anonymous sync rejected', client.post(url).status_code == 401)
call('POST', url, status=403, department='warehouse')
check('warehouse cannot sync catalog', True)
sync = call('POST', url)
pid = sync['productIds'][0]
check('new catalog awaits operations publishing', next(p for p in call('GET', '/api/platform/products') if p['id'] == pid)['status'] == 'pending_listing')
call('PATCH', f'/api/platform/products/{pid}', {'status': 'active'})
p = call('GET', f'/api/public/clubs/1/mall/products/{pid}')
check('published product visible with explicit display status', p['displayOnly'] and not p['purchaseAvailable'] and p['stock'] is None)
check('absolute images and actual sizes exposed', p['images'][1]['url'].startswith(app.peaktrace_catalog.ORIGIN) and p['sizeOptions'] == ['S', 'M'])
feed[0]['price'] = 219
sync = call('POST', url)
check('repeat sync keeps stable IDs', sync['created'] == 0 and sync['updated'] == 1 and sync['productIds'] == [pid])
p = call('GET', f'/api/public/clubs/1/mall/products/{pid}')
check('price refreshed and publication preserved', p['price'] == 219)
check('display price does not promise member discount', 'memberPrice' not in call('GET', f'/api/public/clubs/1/mall/products/{pid}?user_id=1'))
with app.conn() as db:
    check('no duplicate images or variants', db.execute('SELECT COUNT(*) FROM product_images WHERE product_id=?', (pid,)).fetchone()[0] == 2 and db.execute('SELECT COUNT(*) FROM product_variants WHERE product_id=?', (pid,)).fetchone()[0] == 2)
    check('no WMS or inventory sync facts', not db.execute('SELECT 1 FROM warehouse_inventory WHERE product_id=?', (pid,)).fetchone() and not db.execute('SELECT 1 FROM warehouse_sku_balances WHERE product_id=?', (pid,)).fetchone() and not db.execute('SELECT 1 FROM external_inventory_sync_logs').fetchone())
    check('inventory sync timestamp remains unknown', db.execute('SELECT last_inventory_sync_at FROM external_product_mappings WHERE product_id=?', (pid,)).fetchone()[0] is None)
call('POST', '/api/public/clubs/1/gear-checkout', {'userId': 1, 'items': [{'productId': pid, 'quantity': 1}]}, status=409)
check('checkout rejects display-only product', True)
call('PATCH', f'/api/platform/products/{pid}', {'stock': 1}, status=409, department='all')
check('display product cannot enter platform stock', True)
call('PATCH', f'/api/platform/products/{pid}', {'status': 'inactive'})
call('POST', url)
check('repeat sync does not republish manually hidden product', all(p['id'] != pid for p in call('GET', '/api/public/clubs/1/mall/products')))
call('PATCH', f'/api/platform/products/{pid}', {'status': 'active'})
bad = copy.deepcopy(feed[0]); bad['id'] = 'invalid'; bad['price'] = 'broken'; feed.append(bad)
call('POST', url, status=409)
check('invalid upstream data changes nothing', call('GET', f'/api/public/clubs/1/mall/products/{pid}')['price'] == 219)
feed.pop()
app.peaktrace_catalog.fetch_catalog = lambda: (_ for _ in ()).throw(RuntimeError('network failure'))
call('POST', url, status=502)
check('network failure preserves last valid catalog', call('GET', f'/api/public/clubs/1/mall/products/{pid}')['price'] == 219)
app.peaktrace_catalog.fetch_catalog = lambda: ([], categories)
sync = call('POST', url)
check('upstream removal hides without deleting local data', sync['hidden'] == 1 and any(p['id'] == pid for p in call('GET', '/api/platform/products')))
call('PATCH', f'/api/platform/products/{pid}', {'status': 'active'}, status=409)
check('upstream removed product cannot be republished', True)
app.commerce_provider = lambda: 'medusa'
check('external catalog never mirrored into Medusa inventory', app._sync_product_to_medusa(pid)['skipped'])
print('PEAKTRACE CATALOG OK', len(checks), 'checks')
