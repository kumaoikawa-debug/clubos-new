"""ClubOS NEW v0.16 real Medusa runtime acceptance test.

This test MUST run against real ClubOS + Medusa + PostgreSQL. It intentionally does
not monkeypatch Medusa. Passing it is required before v0.16 can be marked completed.
"""
from __future__ import annotations

import os
import time
import uuid
import httpx

CLUBOS=os.getenv("CLUBOS_URL","http://127.0.0.1:8000").rstrip("/")
MEDUSA=os.getenv("MEDUSA_URL","http://127.0.0.1:9000").rstrip("/")
ADMIN_TOKEN=os.environ["MEDUSA_ADMIN_TOKEN"]
SECRET=os.getenv("CLUBOS_COMMERCE_WEBHOOK_SECRET","")

client=httpx.Client(timeout=30.0)
admin_headers={"Authorization":f"Bearer {ADMIN_TOKEN}"}
webhook_headers={"x-clubos-commerce-secret":SECRET} if SECRET else {}


def req(method:str,url:str,**kwargs):
    r=client.request(method,url,**kwargs)
    if r.status_code>=400:
        raise AssertionError(f"{method} {url} -> {r.status_code}: {r.text}")
    return r


def amount(v):
    if v is None:return 0.0
    if isinstance(v,(int,float)):return float(v)
    if isinstance(v,str):return float(v or 0)
    if isinstance(v,dict):
        if isinstance(v.get("numeric"),(int,float)):return float(v["numeric"])
        if "value" in v:return float(v.get("value") or 0)
        raw=v.get("raw")
        if isinstance(raw,dict):return float(raw.get("value") or 0)
    return 0.0


def medusa_order(order_id:str):
    fields="*items,*items.metadata,*fulfillments,*fulfillments.labels,*payment_collections,*payment_collections.payments,*payment_collections.payments.captures,*payment_collections.payments.refunds"
    return req("GET",f"{MEDUSA}/admin/orders/{order_id}",headers=admin_headers,params={"fields":fields}).json()["order"]


# 0. Both processes must be actually reachable.
h=req("GET",f"{CLUBOS}/api/health").json()
assert str(h.get("version","")).startswith(("0.16","0.17","0.18","0.19","0.20","0.21","0.22")),h
cs=req("GET",f"{CLUBOS}/api/platform/commerce/status").json()
assert cs.get("provider")=="medusa" and cs.get("reachable") is True,cs
req("GET",f"{MEDUSA}/health")

# 1. Real platform product -> Medusa Product / Variant / Inventory Item / Location Level.
suffix=uuid.uuid4().hex[:8]
sku=f"V016-CI-{suffix.upper()}"
p=req("POST",f"{CLUBOS}/api/platform/products",json={
    "name":f"ClubOS v0.16 Runtime Jacket {suffix}",
    "sku":sku,
    "price":729,
    "stock":7,
    "status":"active",
    "category":"runtime-acceptance",
}).json()
product_id=int(p["id"])
commerce=p.get("commerce") or {}
medusa_product_id=commerce.get("commerceProductId")
medusa_variant_id=commerce.get("commerceVariantId")
inv_id=commerce.get("commerceInventoryItemId")
assert medusa_product_id and medusa_variant_id and inv_id,p

mp=req("GET",f"{MEDUSA}/admin/products/{medusa_product_id}",headers=admin_headers,params={"fields":"*variants,*variants.inventory_items,*variants.prices"}).json()["product"]
variant=next((v for v in mp.get("variants",[]) if v.get("id")==medusa_variant_id),None)
assert variant and variant.get("sku")==sku,mp

# 2. Real update reaches Medusa price + inventory sync code path.
req("PATCH",f"{CLUBOS}/api/platform/products/{product_id}",json={"price":739,"stock":6,"status":"active"})

# 3. Real Gear Cart with physical shipping. ClubOS computes the cash price; Medusa stores commerce cart.
checkout=req("POST",f"{CLUBOS}/api/public/clubs/1/gear-checkout",json={
    "userId":1,
    "items":[{"productId":product_id,"quantity":1}],
    "gearPoints":0,
    "email":"clubos-runtime@example.test",
    "shippingAddress":{
        "first_name":"ClubOS",
        "last_name":"Runtime",
        "address_1":"No. 1 Runtime Road",
        "city":"Chengdu",
        "province":"Sichuan",
        "country_code":"cn",
        "postal_code":"610000"
    }
}).json()
checkout_id=checkout["checkoutId"]
cart_id=checkout.get("commerceCartId")
assert cart_id and str(cart_id).startswith("cart_"),checkout
assert not checkout.get("commerceNote"),checkout

# 4. ClubOS verifies external payment truth, then Medusa completes cart and CAPTURES its system payment mirror.
payment_event=f"runtime-payment-{suffix}"
payment=req("POST",f"{CLUBOS}/api/commerce/events/payment-succeeded",headers=webhook_headers,json={
    "eventKey":payment_event,
    "provider":"local-runtime",
    "checkoutId":checkout_id,
    "providerPaymentId":f"provider_tx_{suffix}"
}).json()
order_id=(payment.get("result") or {}).get("commerceOrderId")
assert order_id and str(order_id).startswith("order_"),payment
assert order_id!=f"provider_tx_{suffix}",payment

co=req("GET",f"{CLUBOS}/api/public/checkouts/{checkout_id}").json()
gear_order_id=int(co["result_id"])
assert co.get("payment_status")=="succeeded" and co.get("commerce_order_id")==order_id,co

mo=medusa_order(order_id)
payments=[pay for pc in mo.get("payment_collections",[]) for pay in (pc.get("payments") or [])]
assert payments,mo
captured=sum(amount(x.get("captured_amount")) for x in payments)
assert captured>=739-0.001,(captured,payments)

# 5. Real fulfillment -> shipment -> delivered through ClubOS platform routes.
ful=req("POST",f"{CLUBOS}/api/platform/orders/{gear_order_id}/fulfill").json()
fulfillment_id=ful.get("fulfillmentId")
assert fulfillment_id and str(fulfillment_id).startswith("ful_"),ful
tracking=f"CI{suffix.upper()}"
sh=req("POST",f"{CLUBOS}/api/platform/orders/{gear_order_id}/ship",json={"trackingNo":tracking,"carrier":"CI-CARRIER"}).json()
assert sh.get("status")=="shipped",sh
dl=req("POST",f"{CLUBOS}/api/platform/orders/{gear_order_id}/delivered").json()
assert dl.get("status")=="delivered",dl
mo=medusa_order(order_id)
assert any(f.get("id")==fulfillment_id for f in (mo.get("fulfillments") or [])),mo

# 6. Provider refund truth -> ClubOS reversals -> real Medusa captured-payment refund mirror.
rr=req("POST",f"{CLUBOS}/api/public/orders/{gear_order_id}/refund-request",json={"reason":"v0.16 runtime refund acceptance"}).json()
refund_id=rr["refundRequestId"]
ap=req("POST",f"{CLUBOS}/api/platform/refunds/{refund_id}/approve").json()
assert ap.get("status")=="processing",ap

refund_event=f"runtime-refund-{suffix}"
rf=req("POST",f"{CLUBOS}/api/commerce/events/refund-succeeded",headers=webhook_headers,json={
    "eventKey":refund_event,
    "refundRequestId":refund_id,
    "provider":"local-runtime",
    "providerRefundId":f"provider_refund_{suffix}"
}).json()
mirror=((rf.get("result") or {}).get("commerceMirror") or {})
assert mirror.get("status")=="synced",rf

refunds=req("GET",f"{CLUBOS}/api/platform/refunds").json()
local_refund=next((x for x in refunds if x.get("id")==refund_id),None)
assert local_refund and local_refund.get("status")=="succeeded",local_refund
assert local_refund.get("commerce_refund_status")=="synced",local_refund

mo=medusa_order(order_id)
payments=[pay for pc in mo.get("payment_collections",[]) for pay in (pc.get("payments") or [])]
refunded=sum(amount(r.get("amount")) for pay in payments for r in (pay.get("refunds") or []))
assert refunded>=739-0.001,(refunded,payments)

# 7. Duplicate provider callback is idempotent: Medusa refund total must not increase.
rf2=req("POST",f"{CLUBOS}/api/commerce/events/refund-succeeded",headers=webhook_headers,json={
    "eventKey":refund_event,
    "refundRequestId":refund_id,
    "provider":"local-runtime",
    "providerRefundId":f"provider_refund_{suffix}"
}).json()
assert rf2.get("idempotent") is True,rf2
mo2=medusa_order(order_id)
payments2=[pay for pc in mo2.get("payment_collections",[]) for pay in (pc.get("payments") or [])]
refunded2=sum(amount(r.get("amount")) for pay in payments2 for r in (pay.get("refunds") or []))
assert abs(refunded2-refunded)<0.001,(refunded,refunded2)

print("RUNTIME V0.16 MEDUSA E2E OK")
print({
    "product_id":medusa_product_id,
    "variant_id":medusa_variant_id,
    "inventory_item_id":inv_id,
    "cart_id":cart_id,
    "order_id":order_id,
    "fulfillment_id":fulfillment_id,
    "captured":captured,
    "refunded":refunded,
})
