from pathlib import Path
import os, sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['MOCK_AI']='1'
os.environ['COMMERCE_PROVIDER']='medusa'
os.environ['CLUBOS_DB_PATH']=str(ROOT/'clubos_v16_test.db')
os.environ['MEDUSA_URL']='http://medusa.test'
os.environ['MEDUSA_ADMIN_TOKEN']='adm_test'
os.environ['MEDUSA_PUBLISHABLE_KEY']='pk_test'
os.environ['MEDUSA_SHIPPING_PROFILE_ID']='sp_1'
os.environ['MEDUSA_SALES_CHANNEL_ID']='sc_1'
os.environ['MEDUSA_STOCK_LOCATION_ID']='sloc_1'
os.environ['MEDUSA_CURRENCY_CODE']='cny'
os.environ['MEDUSA_DEFAULT_SHIPPING_OPTION_ID']='so_1'
os.environ['MEDUSA_INTERNAL_PAYMENT_PROVIDER_ID']='pp_system_default'
try: Path(os.environ['CLUBOS_DB_PATH']).unlink()
except FileNotFoundError: pass

import app
from fastapi.testclient import TestClient
c=TestClient(app.app)

calls=[]
refund_state={'done':False}
def fake_request(self,path,method='GET',payload=None,admin=False,timeout=8):
    calls.append((method,path,payload,admin))
    if path=='/health': return {'ok':True}
    if path=='/admin/products' and method=='POST':
        return {'product':{'id':'prod_1','variants':[{'id':'variant_1'}]}}
    if path.startswith('/admin/products/prod_1?'):
        return {'product':{'id':'prod_1','variants':[{'id':'variant_1','inventory_items':[{'inventory_item_id':'iitem_1'}]}]}}
    if path=='/admin/products/prod_1' and method=='POST': return {'product':{'id':'prod_1'}}
    if path=='/admin/products/prod_1/variants/variant_1' and method=='POST': return {'product':{'id':'prod_1'}}
    if path=='/admin/inventory-items/iitem_1/location-levels' and method=='POST': return {'inventory_item':{'id':'iitem_1'}}
    if path=='/admin/inventory-items/iitem_1/location-levels/sloc_1' and method=='POST': return {'inventory_item':{'id':'iitem_1'}}
    if path.startswith('/admin/orders/order_medusa_1?'):
        return {'order':{'id':'order_medusa_1','items':[{'id':'item_m1','metadata':{'clubos_product_id':'1'}}],
                         'fulfillments':[{'id':'ful_1'}]}}
    if path.startswith('/admin/orders/order_medusa_checkout_1?'):
        refunds=[{'id':'refund_medusa_1','amount':729}] if refund_state['done'] else []
        return {'order':{'id':'order_medusa_checkout_1','payment_collections':[{'id':'paycol_1','payments':[{'id':'pay_medusa_1','amount':729,'captured_amount':729,'refunded_amount':729 if refund_state['done'] else 0,'captures':[{'id':'cap_1','amount':729}],'refunds':refunds}]}]}}
    if path=='/admin/payments/pay_medusa_1/refund' and method=='POST':
        refund_state['done']=True
        return {'payment':{'id':'pay_medusa_1','amount':729,'captured_amount':729,'refunded_amount':729,'refunds':[{'id':'refund_medusa_1','amount':729}]}}
    if path=='/admin/orders/order_medusa_1/fulfillments' and method=='POST':
        return {'order':{'id':'order_medusa_1','fulfillments':[{'id':'ful_1'}]}}
    if path=='/admin/orders/order_medusa_1/fulfillments/ful_1/shipments' and method=='POST': return {'order':{'id':'order_medusa_1'}}
    if path=='/admin/orders/order_medusa_1/fulfillments/ful_1/mark-as-delivered' and method=='POST': return {'order':{'id':'order_medusa_1'}}
    if path=='/store/carts' and method=='POST': return {'cart':{'id':'cart_1'}}
    if path=='/admin/clubos-carts/cart_1/items' and method=='POST' and admin: return {'cart':{'id':'cart_1'}}
    if path=='/store/carts/cart_1/shipping-methods' and method=='POST': return {'cart':{'id':'cart_1'}}
    if path=='/admin/clubos-paid-carts/cart_1/complete' and method=='POST':
        return {'type':'order','order':{'id':'order_medusa_checkout_1'},'idempotent':False}
    raise AssertionError(f'unhandled Medusa call: {method} {path} {payload}')

app.MedusaClient._request=fake_request

def ok(method,url,**kw):
    r=getattr(c,method)(url,**kw)
    assert r.status_code<400,(url,r.status_code,r.text)
    return r

h=ok('get','/api/health').json(); assert h['version'].startswith(('0.16','0.17','0.18','0.19','0.20','0.21','0.22','0.23','0.24','0.25'))
st=ok('get','/api/platform/commerce/status').json(); assert st['provider']=='medusa' and st['reachable'] is True

# Platform catalog becomes a Medusa-backed catalog: create product + variant + inventory level.
p=ok('post','/api/platform/products',json={'name':'v16测试冲锋衣','sku':'V16-JKT-001','price':699,'stock':12,'status':'active','category':'服装'}).json()
pid=p['id']; assert p['commerce']['commerceProductId']=='prod_1' and p['commerce']['commerceInventoryItemId']=='iitem_1'
with app.conn() as db:
    row=dict(db.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone())
assert row['commerce_product_id']=='prod_1' and row['commerce_variant_id']=='variant_1' and row['commerce_inventory_item_id']=='iitem_1'
assert row['commerce_sync_status']=='synced'
assert any(x[1]=='/admin/products' for x in calls)
assert any('/location-levels' in x[1] for x in calls)

# Update price/stock/status pushes to Medusa instead of only changing SQLite.
ok('patch',f'/api/platform/products/{pid}',json={'price':729,'stock':9,'status':'active'})
assert any(x[1]=='/admin/products/prod_1/variants/variant_1' and x[2].get('prices') for x in calls)
assert any(x[1]=='/admin/inventory-items/iitem_1/location-levels/sloc_1' and x[2].get('stocked_quantity')==9 for x in calls)


# Activity business must not touch Medusa even while COMMERCE_PROVIDER=medusa.
with app.conn() as db:
    act=dict(db.execute("SELECT * FROM activities WHERE status='published' ORDER BY id LIMIT 1").fetchone())
    occ=dict(db.execute("SELECT * FROM activity_occurrences WHERE activity_id=? AND status='open' ORDER BY id LIMIT 1",(act['id'],)).fetchone())
before_activity_calls=len(calls)
aco=ok('post',f"/api/public/activities/{act['id']}/checkout",json={
    'name':'v16活动用户','phone':'13916000001','occurrenceId':occ['id'],'clubPoints':0,'gearPoints':0
}).json()
assert aco['commerceProvider']=='clubos-domain' and aco['commerceCartId'] is None
assert len(calls)==before_activity_calls, 'activity checkout must never create Medusa calls'

# Gear checkout -> verified external payment -> real Medusa Order ID -> ClubOS projection.
gco=ok('post','/api/public/clubs/1/gear-checkout',json={
    'userId':1,'items':[{'productId':pid,'quantity':1}],'gearPoints':0,
    'email':'gear@example.test',
    'shippingAddress':{'first_name':'ClubOS','last_name':'User','address_1':'Test Road 1','city':'Chengdu','country_code':'cn','postal_code':'610000'},
}).json()
assert gco['commerceCartId']=='cart_1' and not gco['commerceNote']
pay=ok('post','/api/commerce/events/payment-succeeded',json={
    'eventKey':'v16-payment-success-1','provider':'local-test','checkoutId':gco['checkoutId'],'providerPaymentId':'pay_external_1'
}).json()
assert pay['result']['kind']=='gear'
assert pay['result']['commerceOrderId']=='order_medusa_checkout_1'
with app.conn() as db:
    checkout=dict(db.execute('SELECT * FROM checkout_intents WHERE id=?',(gco['checkoutId'],)).fetchone())
    gear=dict(db.execute('SELECT * FROM gear_orders WHERE id=?',(int(checkout['result_id']),)).fetchone())
assert checkout['commerce_order_id']=='order_medusa_checkout_1'
assert gear['commerce_order_id']=='order_medusa_checkout_1'
assert any(x[1]=='/admin/clubos-paid-carts/cart_1/complete' and x[3] is True for x in calls)

# Provider-confirmed Gear refund must reverse ClubOS state and mirror the captured cash refund into Medusa.
gear_id=int(checkout['result_id'])
rr=ok('post',f'/api/public/orders/{gear_id}/refund-request',json={'reason':'v0.16 refund mirror test'}).json()
rid=rr['refundRequestId']
approved=ok('post',f'/api/platform/refunds/{rid}/approve').json()
assert approved['status']=='processing' and approved['providerStatus']=='local_waiting'
ref=ok('post','/api/commerce/events/refund-succeeded',headers={'x-clubos-commerce-secret':os.environ.get('CLUBOS_COMMERCE_WEBHOOK_SECRET','')},json={
    'eventKey':'v16-refund-success-1','refundRequestId':rid,'provider':'local','providerRefundId':'local_refund_1'
}).json()
assert ref['result']['commerceMirror']['status']=='synced'
assert any(x[1]=='/admin/payments/pay_medusa_1/refund' and x[3] is True and x[2]['amount']==729 for x in calls)
with app.conn() as db:
    rrdb=dict(db.execute('SELECT * FROM refund_requests WHERE id=?',(rid,)).fetchone())
    godb=dict(db.execute('SELECT * FROM gear_orders WHERE id=?',(gear_id,)).fetchone())
assert rrdb['status']=='succeeded' and rrdb['commerce_refund_status']=='synced' and rrdb['commerce_refund_id']=='refund_medusa_1'
assert godb['status']=='refunded'
# Duplicate provider callback must be idempotent and must not create another Medusa refund.
before_refund_calls=sum(1 for x in calls if x[1]=='/admin/payments/pay_medusa_1/refund')
ref2=ok('post','/api/commerce/events/refund-succeeded',json={
    'eventKey':'v16-refund-success-1','refundRequestId':rid,'provider':'local','providerRefundId':'local_refund_1'
}).json()
assert ref2['idempotent'] is True
assert sum(1 for x in calls if x[1]=='/admin/payments/pay_medusa_1/refund')==before_refund_calls

# order.placed is reconciliation-only and may not turn an unpaid checkout into paid.
gco2=ok('post','/api/public/clubs/1/gear-checkout',json={'userId':1,'items':[{'productId':pid,'quantity':1}],'gearPoints':0}).json()
rec=ok('post','/api/commerce/events/order-placed',json={
    'eventKey':'medusa:order.placed:unpaid-v16','checkoutId':gco2['checkoutId'],'orderId':'order_unverified_1'
}).json()
assert rec['reconciled'] is False and rec['reason']=='payment_not_verified'
with app.conn() as db:
    unpaid=dict(db.execute('SELECT * FROM checkout_intents WHERE id=?',(gco2['checkoutId'],)).fetchone())
assert unpaid['status']=='pending_payment' and unpaid['payment_status']!='succeeded'

# Create a local projection of a paid gear order linked to a Medusa Order, then fulfill -> ship -> delivered.
with app.conn() as db:
    # fixture user/club are seeded by db init
    db.execute("INSERT INTO gear_orders(user_id,source_club_id,total,cash_paid,status,commerce_order_id,commerce_sync_status) VALUES(1,1,729,729,'paid','order_medusa_1','paid')")
    oid=db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.execute("INSERT INTO gear_order_items(order_id,product_id,quantity,unit_price) VALUES(?,?,1,729)",(oid,1))

ful=ok('post',f'/api/platform/orders/{oid}/fulfill').json(); assert ful['fulfillmentId']=='ful_1'
ship=ok('post',f'/api/platform/orders/{oid}/ship',json={'trackingNo':'SF123456789','carrier':'顺丰'}).json(); assert ship['status']=='shipped'
deliv=ok('post',f'/api/platform/orders/{oid}/delivered').json(); assert deliv['status']=='delivered'
with app.conn() as db: o=dict(db.execute('SELECT * FROM gear_orders WHERE id=?',(oid,)).fetchone())
assert o['commerce_fulfillment_id']=='ful_1' and o['status']=='delivered' and o['tracking_no']=='SF123456789'
assert o['shipped_at'] and o['delivered_at']

print('SMOKE V0.16 OK · MEDUSA GEAR-ONLY CATALOG / CART / VERIFIED PAYMENT -> ORDER / FULFILLMENT / SHIPMENT / DELIVERY / CAPTURED PAYMENT / REFUND MIRROR')
