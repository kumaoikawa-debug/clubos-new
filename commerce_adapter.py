from __future__ import annotations
import json, os, urllib.request, urllib.parse
from dataclasses import dataclass
from typing import Any

@dataclass
class CommerceStatus:
    provider:str
    configured:bool
    reachable:bool
    url:str|None=None
    note:str|None=None

class MedusaClient:
    """Thin Medusa v2 HTTP adapter.

    ClubOS owns attribution / points / commission / AI-credit business rules.
    Medusa owns gear catalog primitives, inventory reservation, commerce order and fulfillment.
    """
    def __init__(self):
        self.base=os.getenv('MEDUSA_URL','http://localhost:9000').rstrip('/')
        self.publishable_key=os.getenv('MEDUSA_PUBLISHABLE_KEY','')
        self.admin_token=os.getenv('MEDUSA_ADMIN_TOKEN','')
        self.region_id=os.getenv('MEDUSA_REGION_ID','')
        self.shipping_profile_id=os.getenv('MEDUSA_SHIPPING_PROFILE_ID','')
        self.sales_channel_id=os.getenv('MEDUSA_SALES_CHANNEL_ID','')
        self.stock_location_id=os.getenv('MEDUSA_STOCK_LOCATION_ID','')
        self.currency_code=os.getenv('MEDUSA_CURRENCY_CODE','cny').lower()
        self.default_shipping_option_id=os.getenv('MEDUSA_DEFAULT_SHIPPING_OPTION_ID','')
        self.internal_payment_provider_id=os.getenv('MEDUSA_INTERNAL_PAYMENT_PROVIDER_ID','pp_system_default')

    def _request(self,path,method='GET',payload=None,admin=False,timeout=8):
        headers={'Content-Type':'application/json'}
        if admin and self.admin_token:
            auth_type=os.getenv('MEDUSA_ADMIN_AUTH_TYPE','bearer').strip().lower()
            scheme='Basic' if auth_type in ('api-key','api_key','secret-api-key','basic') else 'Bearer'
            headers['Authorization']=f'{scheme} {self.admin_token}'
        if not admin and self.publishable_key: headers['x-publishable-api-key']=self.publishable_key
        data=json.dumps(payload).encode('utf-8') if payload is not None else None
        req=urllib.request.Request(self.base+path,data=data,headers=headers,method=method)
        with urllib.request.urlopen(req,timeout=timeout) as r:
            raw=r.read().decode('utf-8')
            return json.loads(raw) if raw else {}

    def status(self):
        configured=bool(os.getenv('MEDUSA_URL'))
        try:
            self._request('/health')
            return CommerceStatus('medusa',configured,True,self.base,'Medusa reachable')
        except Exception as e:
            return CommerceStatus('medusa',configured,False,self.base,str(e))

    # ---------- Storefront ----------
    def list_products(self,limit=100):
        q=urllib.parse.urlencode({'limit':limit,'fields':'*variants,*variants.calculated_price'})
        return self._request('/store/products?'+q).get('products',[])

    def create_cart(self,metadata=None,*,email:str|None=None,shipping_address:dict|None=None):
        payload={'metadata':metadata or {}}
        if self.region_id: payload['region_id']=self.region_id
        if self.sales_channel_id: payload['sales_channel_id']=self.sales_channel_id
        if email: payload['email']=email
        if shipping_address: payload['shipping_address']=shipping_address
        return self._request('/store/carts',method='POST',payload=payload).get('cart')

    def add_line_item(self,cart_id,variant_id,quantity=1):
        return self._request(f'/store/carts/{cart_id}/line-items',method='POST',payload={'variant_id':variant_id,'quantity':quantity}).get('cart')

    def add_custom_items(self,cart_id,items:list[dict]):
        """ClubOS route backed by Medusa addToCartWorkflow with explicit unit_price."""
        return self._request(f'/admin/clubos-carts/{cart_id}/items',method='POST',payload={'items':items},admin=True).get('cart')

    def set_shipping_method(self,cart_id:str,option_id:str):
        if not option_id: return None
        return self._request(f'/store/carts/{cart_id}/shipping-methods',method='POST',payload={'option_id':option_id}).get('cart')

    def prepare_gear_checkout(self,*,intent_id:str,items:list[dict],platform_subsidy:float=0,metadata:dict|None=None,
                              email:str|None=None,shipping_address:dict|None=None,shipping_option_id:str|None=None):
        cart=self.create_cart(metadata={'clubos_checkout_intent_id':intent_id,'clubos_kind':'gear',**(metadata or {})},
                              email=email,shipping_address=shipping_address)
        if not cart: raise RuntimeError('Medusa cart creation failed')
        subtotal=sum(float(x['unit_price'])*int(x.get('quantity',1)) for x in items)
        subsidy=max(0.0,min(float(platform_subsidy or 0),subtotal))
        prepared=[]; remaining=subsidy
        for i,x in enumerate(items):
            qty=max(1,int(x.get('quantity',1))); base=float(x['unit_price']); line=base*qty
            line_sub=remaining if i==len(items)-1 else (round(subsidy*(line/subtotal),2) if subtotal else 0)
            remaining=max(0.0,remaining-line_sub); effective=max(0.0,(line-line_sub)/qty)
            prepared.append({'variant_id':x['variant_id'],'quantity':qty,'unit_price':effective,
                'metadata':{'clubos_checkout_intent_id':intent_id,'clubos_product_id':str(x.get('product_id',''))}})
        cart=self.add_custom_items(cart['id'],prepared) or cart
        option_id=shipping_option_id or self.default_shipping_option_id
        if option_id:
            cart=self.set_shipping_method(cart['id'],option_id) or cart
        return cart

    def complete_paid_cart(self,cart_id:str,*,provider_payment_id:str|None=None,provider:str='external'):
        """Complete a Gear cart only after ClubOS has verified the external payment.

        The Medusa-side route mirrors the verified payment using Medusa's internal/manual
        provider and returns the actual Medusa Order. Activity checkouts never call this.
        """
        result=self._request(f'/admin/clubos-paid-carts/{cart_id}/complete',method='POST',admin=True,payload={
            'provider_payment_id':provider_payment_id,
            'provider':provider,
        })
        if result.get('type')!='order' or not (result.get('order') or {}).get('id'):
            raise RuntimeError(f'Medusa cart completion did not return an order: {result}')
        return result

    def get_order(self,order_id):
        return self._request(f'/store/orders/{order_id}').get('order')

    # ---------- Platform admin / catalog ----------
    def list_admin_orders(self,limit=100):
        q=urllib.parse.urlencode({'limit':limit})
        return self._request('/admin/orders?'+q,admin=True).get('orders',[])

    def get_admin_order(self,order_id:str,*,include_payments:bool=False):
        fields='*items,*items.metadata,*fulfillments,*fulfillments.labels'
        if include_payments:
            fields+=',*payment_collections,*payment_collections.payments,*payment_collections.payments.captures,*payment_collections.payments.refunds'
        q=urllib.parse.urlencode({'fields':fields})
        return self._request(f'/admin/orders/{order_id}?{q}',admin=True).get('order')

    @staticmethod
    def _amount(value):
        """Normalize Medusa BigNumber-ish HTTP values to float."""
        if value is None: return 0.0
        if isinstance(value,(int,float)): return float(value)
        if isinstance(value,str):
            try:return float(value)
            except ValueError:return 0.0
        if isinstance(value,dict):
            for key in ('numeric','value'):
                if key in value:
                    try:return float(value[key] or 0)
                    except (TypeError,ValueError):pass
            raw=value.get('raw')
            if isinstance(raw,dict):
                try:return float(raw.get('value') or 0)
                except (TypeError,ValueError):pass
        return 0.0

    def mirror_order_refund(self,order_id:str,amount:float,*,note:str='ClubOS provider refund mirror'):
        """Mirror a provider-confirmed cash refund into Medusa's captured payment.

        ClubOS/payment provider remains money truth. This method is intentionally
        idempotent: existing Medusa refunds count toward the requested total, so a
        replay only creates the missing remainder.
        """
        target=round(max(0.0,float(amount or 0)),2)
        if target<=0:
            return {'ok':True,'order_id':order_id,'target_amount':0.0,'refunded_amount':0.0,'idempotent':True,'refund_ids':[]}
        order=self.get_admin_order(order_id,include_payments=True)
        if not order: raise RuntimeError(f'Medusa order not found: {order_id}')
        payments=[]
        for pc in order.get('payment_collections') or []:
            payments.extend(pc.get('payments') or [])
        if not payments: raise RuntimeError(f'Medusa order has no payments: {order_id}')

        existing=sum(self._amount(r.get('amount')) for p in payments for r in (p.get('refunds') or []))
        remaining=round(max(0.0,target-existing),2)
        if remaining<=0.000001:
            ids=[str(r.get('id')) for p in payments for r in (p.get('refunds') or []) if r.get('id')]
            return {'ok':True,'order_id':order_id,'target_amount':target,'refunded_amount':round(existing,2),'idempotent':True,'refund_ids':ids}

        created=[]
        for payment in payments:
            captured=self._amount(payment.get('captured_amount'))
            refunded=self._amount(payment.get('refunded_amount'))
            if refunded<=0:
                refunded=sum(self._amount(r.get('amount')) for r in (payment.get('refunds') or []))
            available=max(0.0,captured-refunded)
            if available<=0.000001: continue
            part=round(min(remaining,available),2)
            response=self._request(f"/admin/payments/{payment['id']}/refund",method='POST',payload={'amount':part,'note':note},admin=True)
            pay=response.get('payment') or {}
            refunds=pay.get('refunds') or []
            if refunds and refunds[-1].get('id'): created.append(str(refunds[-1]['id']))
            remaining=round(max(0.0,remaining-part),2)
            if remaining<=0.000001: break
        if remaining>0.000001:
            raise RuntimeError(f'Medusa captured payment is insufficient for refund: target={target}, existing={existing}, missing={remaining}')
        refreshed=self.get_admin_order(order_id,include_payments=True) or order
        all_refunds=[r for pc in (refreshed.get('payment_collections') or []) for p in (pc.get('payments') or []) for r in (p.get('refunds') or [])]
        total=sum(self._amount(r.get('amount')) for r in all_refunds)
        return {'ok':True,'order_id':order_id,'target_amount':target,'refunded_amount':round(total,2),'idempotent':False,'refund_ids':created}

    def get_admin_product(self,product_id:str):
        q=urllib.parse.urlencode({'fields':'*variants,*variants.inventory_items,*variants.prices'})
        return self._request(f'/admin/products/{product_id}?{q}',admin=True).get('product')

    @staticmethod
    def _inventory_item_id(product:dict|None,variant_id:str|None=None):
        for v in (product or {}).get('variants') or []:
            if variant_id and str(v.get('id'))!=str(variant_id): continue
            for inv in v.get('inventory_items') or []:
                iid=inv.get('inventory_item_id') or ((inv.get('inventory') or {}).get('id') if isinstance(inv.get('inventory'),dict) else None) or inv.get('id')
                if iid: return str(iid)
        return None

    def create_platform_product(self,*,name:str,sku:str,price:float,stock:int,status:str='active',image_url:str|None=None,category:str|None=None):
        if not self.shipping_profile_id:
            raise RuntimeError('MEDUSA_SHIPPING_PROFILE_ID is required for catalog sync')
        payload={
            'title':name,'status':'published' if status=='active' else 'draft',
            'options':[{'title':'Default','values':['Default']}],
            'variants':[{'title':'Default','sku':sku,'manage_inventory':True,'options':{'Default':'Default'},
                         'prices':[{'amount':float(price),'currency_code':self.currency_code}]}],
            'shipping_profile_id':self.shipping_profile_id,
            'metadata':{'clubos_catalog':'gear','clubos_sku':sku},
        }
        if image_url: payload['thumbnail']=image_url
        if category: payload['metadata']['clubos_category']=category
        if self.sales_channel_id: payload['sales_channels']=[{'id':self.sales_channel_id}]
        product=self._request('/admin/products',method='POST',payload=payload,admin=True).get('product')
        if not product: raise RuntimeError('Medusa product creation failed')
        variant=(product.get('variants') or [None])[0] or {}
        variant_id=variant.get('id')
        full=self.get_admin_product(product['id']) or product
        inventory_item_id=self._inventory_item_id(full,variant_id)
        if inventory_item_id and self.stock_location_id:
            self._request(f'/admin/inventory-items/{inventory_item_id}/location-levels',method='POST',admin=True,
                          payload={'location_id':self.stock_location_id,'stocked_quantity':int(stock)})
        return {'product':full,'product_id':product.get('id'),'variant_id':variant_id,'inventory_item_id':inventory_item_id}

    def update_platform_product(self,*,product_id:str,variant_id:str,name:str|None=None,price:float|None=None,status:str|None=None,image_url:str|None=None):
        pp={}
        if name is not None: pp['title']=name
        if status is not None: pp['status']='published' if status=='active' else 'draft'
        if image_url is not None: pp['thumbnail']=image_url
        if pp: self._request(f'/admin/products/{product_id}',method='POST',payload=pp,admin=True)
        vp={}
        if price is not None: vp['prices']=[{'amount':float(price),'currency_code':self.currency_code}]
        if vp: self._request(f'/admin/products/{product_id}/variants/{variant_id}',method='POST',payload=vp,admin=True)
        return self.get_admin_product(product_id)

    def set_inventory(self,*,inventory_item_id:str,stock:int):
        if not self.stock_location_id: raise RuntimeError('MEDUSA_STOCK_LOCATION_ID is required for inventory sync')
        # Medusa location-level update route addresses the level by inventory-item + location.
        return self._request(f'/admin/inventory-items/{inventory_item_id}/location-levels/{self.stock_location_id}',method='POST',admin=True,
                             payload={'stocked_quantity':int(stock)})

    # ---------- Fulfillment ----------
    def create_fulfillment(self,order_id:str,items:list[dict]):
        payload={'items':items}
        if self.stock_location_id: payload['location_id']=self.stock_location_id
        order=self._request(f'/admin/orders/{order_id}/fulfillments',method='POST',payload=payload,admin=True).get('order')
        fs=(order or {}).get('fulfillments') or []
        return {'order':order,'fulfillment':fs[-1] if fs else None}

    def create_shipment(self,order_id:str,fulfillment_id:str,items:list[dict],*,tracking_number:str,tracking_url:str|None=None,label_url:str|None=None):
        label={'tracking_number':tracking_number,'tracking_url':tracking_url or f'https://tracking.local/{urllib.parse.quote(tracking_number)}','label_url':label_url or 'https://tracking.local/label'}
        payload={'items':items,'labels':[label]}
        return self._request(f'/admin/orders/{order_id}/fulfillments/{fulfillment_id}/shipments',method='POST',payload=payload,admin=True).get('order')

    def mark_delivered(self,order_id:str,fulfillment_id:str):
        return self._request(f'/admin/orders/{order_id}/fulfillments/{fulfillment_id}/mark-as-delivered',method='POST',payload={'no_notification':True},admin=True).get('order')


def commerce_status():
    provider=os.getenv('COMMERCE_PROVIDER','local').lower().strip()
    if provider!='medusa':
        return CommerceStatus('local',True,True,None,'Demo/local adapter; set COMMERCE_PROVIDER=medusa to use Medusa')
    return MedusaClient().status()

def commerce_provider():
    return os.getenv('COMMERCE_PROVIDER','local').lower().strip()
