from __future__ import annotations
import json, os, uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from clubos_domain.product_stock import variant_of, write_variant_stock
from clubos_domain.order_amounts import amount_basis

def _external_order(c, order_id: int) -> bool:
    """订单是否由 PEAKTRACE 代发：货在供应商仓，退回/重发都不进本地仓库。

    用户明确「ClubOS 与 PEAKTRACE 库存共享、不需要单独建仓」——外部订单的
    after_sales_restock / warehouse.outbound_available 会把货错误地记到本地
    镜像仓，必须整体跳过。表不存在（老库）时安全返回 False。
    """
    try:
        return c.execute('SELECT 1 FROM external_fulfillment_orders WHERE order_id=?',
                         (int(order_id),)).fetchone() is not None
    except Exception:
        return False


def _assert_within_return_window(c, order: dict) -> None:
    """签收/妥投后 7 天内可申请退换货（AFTER_SALES_WINDOW_DAYS，0 关闭）。

    时间基准优先「用户确认收货 delivered_at」；外部代发订单妥投后主状态仍是
    shipped、没有 delivered_at，改取履约表 fulfilled_at（物流妥投时间）。
    没有可信基准的存量订单一律放行，避免误伤。
    """
    try:
        days = int(os.environ.get('AFTER_SALES_WINDOW_DAYS', '7'))
    except Exception:
        days = 7
    if days <= 0:
        return
    base = str(order.get('delivered_at') or '').strip()
    if not base:
        try:
            r = c.execute("""SELECT fulfilled_at FROM external_fulfillment_orders
                             WHERE order_id=? AND fulfill_status='delivered'""",
                          (int(order['id']),)).fetchone()
            base = str((r['fulfilled_at'] if r else '') or '').strip()
        except Exception:
            base = ''
    if not base:
        return
    try:
        dt = datetime.fromisoformat(base.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - dt > timedelta(days=days):
            raise ValueError(f'已超过 {days} 天退换货申请期限')
    except ValueError:
        raise
    except Exception:
        return


ACTIVE_STATUSES={
    'requested','pending_review','awaiting_return','return_in_transit',
    'approved_pending_refund','refund_processing','exchange_pending_shipment'
}
FINAL_STATUSES={'completed','refunded','rejected','cancelled'}
CASE_TYPES={'refund_only','return_refund','exchange'}


def _json(v):
    return json.dumps(v,ensure_ascii=False,separators=(',',':'))

class AfterSalesEngine:
    """Platform-owned Gear after-sales state machine.

    ClubOS owns eligibility, evidence, return/exchange workflow and business-side
    reversals. Payment providers/Medusa remain the money/commerce primitives.
    """
    def __init__(self, refund_lifecycle=None, inventory_engine=None):
        self.refunds=refund_lifecycle
        self.inventory=inventory_engine

    def _id(self): return 'as_'+uuid.uuid4().hex[:20]

    def _case(self,c,case_id:str):
        r=c.execute('SELECT * FROM after_sales_cases WHERE id=?',(case_id,)).fetchone()
        if not r: raise LookupError('售后单不存在')
        out=dict(r)
        out['items']=[dict(x) for x in c.execute('''SELECT ai.*,goi.product_id,goi.variant_id,goi.unit_price,p.name product_name,p.sku
            FROM after_sales_items ai JOIN gear_order_items goi ON goi.id=ai.order_item_id
            JOIN products p ON p.id=goi.product_id WHERE ai.case_id=? ORDER BY ai.id''',(case_id,)).fetchall()]
        try: out['evidenceUrls']=json.loads(out.get('evidence_json') or '[]')
        except Exception: out['evidenceUrls']=[]
        return out

    def get(self,c,case_id:str): return self._case(c,case_id)

    def list_for_order(self,c,order_id:int):
        return [self._case(c,r['id']) for r in c.execute('SELECT id FROM after_sales_cases WHERE order_id=? ORDER BY created_at DESC',(order_id,)).fetchall()]

    def list_for_user(self,c,user_id:int):
        return [self._case(c,r['id']) for r in c.execute('SELECT id FROM after_sales_cases WHERE user_id=? ORDER BY created_at DESC',(user_id,)).fetchall()]

    def list_platform(self,c,status:str|None=None):
        sql='SELECT id FROM after_sales_cases'; args=[]
        if status: sql+=' WHERE status=?'; args.append(status)
        sql+=' ORDER BY created_at DESC'
        return [self._case(c,r['id']) for r in c.execute(sql,args).fetchall()]

    def create(self,c,*,order_id:int,user_id:int,case_type:str,items:list[dict[str,Any]],reason:str='',evidence_urls:list[str]|None=None,note:str=''):
        case_type=str(case_type or '').strip()
        if case_type not in CASE_TYPES: raise ValueError('售后类型仅支持 refund_only / return_refund / exchange')
        order0=c.execute('SELECT * FROM gear_orders WHERE id=?',(order_id,)).fetchone()
        if not order0: raise LookupError('装备订单不存在')
        order=dict(order0)
        if int(order['user_id'])!=int(user_id): raise ValueError('只能为自己的订单申请售后')
        if order['status'] not in ('paid','processing','shipped','delivered','completed','after_sales'):
            raise ValueError(f"当前订单状态不能申请售后: {order['status']}")
        _assert_within_return_window(c, order)
        if not items: raise ValueError('至少选择一个售后商品')
        normalized=[]; gross=0.0
        for it in items:
            item_id=int(it.get('orderItemId') or 0); qty=max(1,int(it.get('quantity') or 1))
            oi0=c.execute('SELECT * FROM gear_order_items WHERE id=? AND order_id=?',(item_id,order_id)).fetchone()
            if not oi0: raise ValueError(f'订单商品不存在: {item_id}')
            oi=dict(oi0)
            used=c.execute('''SELECT COALESCE(SUM(ai.quantity),0) FROM after_sales_items ai
                 JOIN after_sales_cases ac ON ac.id=ai.case_id
                 WHERE ai.order_item_id=? AND ac.status NOT IN ('rejected','cancelled')''',(item_id,)).fetchone()[0]
            remaining=int(oi['quantity'])-int(used or 0)
            if qty>remaining: raise ValueError(f'售后数量超过可申请数量: orderItemId={item_id}, remaining={remaining}')
            line=round(float(oi['unit_price'])*qty,2); gross+=line
            normalized.append((item_id,qty,line))
        gross=round(gross,2)
        # OBS-02：售后金额与订单成交口径同源。after_sales_items 存商品原价，
        # gear_orders.total/cash_paid 却是会员装备折扣 + 积分抵扣之后的金额；
        # 旧式 gross*(cash_paid/total) 在 9.5 折订单上会把退款现金多算一个折扣差。
        # 改为按原价占比分摊「实付现金」，并夹在剩余可退现金内。
        basis=amount_basis(c,int(order_id),total=order.get('total'),cash_paid=order.get('cash_paid'))
        cash_left=max(0.0,float(order.get('cash_paid') or 0)-float(order.get('refunded_cash_total') or 0))
        requested_cash=0.0 if case_type=='exchange' else round(min(gross*basis['cashFactor'],cash_left),2)
        cid=self._id()
        c.execute('''INSERT INTO after_sales_cases(id,order_id,user_id,source_club_id,case_type,status,reason,evidence_json,note,
                     requested_goods_amount,requested_refund_amount,approved_refund_amount)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (cid,order_id,user_id,order['source_club_id'],case_type,'pending_review',reason,_json(evidence_urls or []),note,gross,requested_cash,0))
        for item_id,qty,line in normalized:
            c.execute('INSERT INTO after_sales_items(case_id,order_item_id,quantity,requested_amount) VALUES(?,?,?,?)',(cid,item_id,qty,line))
        c.execute("UPDATE gear_orders SET after_sales_status='requested' WHERE id=?",(order_id,))
        return self._case(c,cid)

    def approve(self,c,*,case_id:str,reviewer:str='platform',approved_refund_amount:float|None=None,note:str=''):
        case=self._case(c,case_id)
        if case['status']!='pending_review':
            if case['status'] in ('awaiting_return','approved_pending_refund','exchange_pending_shipment','completed','refunded'):
                return {**case,'idempotent':True}
            raise ValueError(f"当前售后状态不能审核通过: {case['status']}")
        amount=float(case['requested_refund_amount'] or 0) if approved_refund_amount is None else max(0.0,float(approved_refund_amount))
        amount=min(amount,float(case['requested_refund_amount'] or 0)) if case['case_type']!='exchange' else 0.0
        status='approved_pending_refund' if case['case_type']=='refund_only' else 'awaiting_return'
        c.execute('''UPDATE after_sales_cases SET status=?,approved_refund_amount=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,
                     decision_note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',(status,round(amount,2),reviewer,note,case_id))
        c.execute("UPDATE gear_orders SET after_sales_status=? WHERE id=?",(status,case['order_id']))
        return self._case(c,case_id)

    def reject(self,c,*,case_id:str,reviewer:str='platform',note:str=''):
        case=self._case(c,case_id)
        if case['status']=='rejected': return {**case,'idempotent':True}
        if case['status']!='pending_review': raise ValueError(f"当前售后状态不能拒绝: {case['status']}")
        c.execute("UPDATE after_sales_cases SET status='rejected',reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,decision_note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(reviewer,note,case_id))
        self._refresh_order(c,int(case['order_id']))
        return self._case(c,case_id)

    def submit_return(self,c,*,case_id:str,carrier:str,tracking_no:str):
        case=self._case(c,case_id)
        if case['status']=='return_in_transit': return {**case,'idempotent':True}
        if case['status']!='awaiting_return': raise ValueError(f"当前状态不能填写退货物流: {case['status']}")
        if not str(tracking_no or '').strip(): raise ValueError('trackingNo required')
        c.execute('''UPDATE after_sales_cases SET status='return_in_transit',return_carrier=?,return_tracking_no=?,returned_at=CURRENT_TIMESTAMP,
                     updated_at=CURRENT_TIMESTAMP WHERE id=?''',(str(carrier or ''),str(tracking_no),case_id))
        c.execute("UPDATE gear_orders SET after_sales_status='return_in_transit' WHERE id=?",(case['order_id'],))
        return self._case(c,case_id)

    def receive_return(self,c,*,case_id:str,restock:bool=True,note:str=''):
        case=self._case(c,case_id)
        if case['status'] in ('approved_pending_refund','exchange_pending_shipment','completed','refunded'):
            return {**case,'idempotent':True}
        if case['status']!='return_in_transit': raise ValueError(f"当前状态不能确认退货收货: {case['status']}")
        next_status='approved_pending_refund' if case['case_type']=='return_refund' else 'exchange_pending_shipment'
        c.execute('''UPDATE after_sales_cases SET status=?,received_at=CURRENT_TIMESTAMP,decision_note=CASE WHEN ?!='' THEN ? ELSE decision_note END,
                     updated_at=CURRENT_TIMESTAMP WHERE id=?''',(next_status,note,note,case_id))
        external = _external_order(c, int(case['order_id']))
        restocked_products=[]
        # 外部代发：货退回 PEAKTRACE，本地镜像仓不入库（两边共享库存，不单独建仓）
        if restock and not external:
            for it in case['items']:
                if not int(it.get('restocked') or 0):
                    if self.inventory:
                        self.inventory.after_sales_restock(c,case_id=case_id,item_id=int(it['id']),product_id=int(it['product_id']),quantity=int(it['quantity']),actor_type='platform_after_sales',variant_id=it.get('variant_id'))
                    else:
                        _v=variant_of(c,product_id=int(it['product_id']),variant_id=it.get('variant_id'))
                        if _v: write_variant_stock(c,product_id=int(it['product_id']),variant_id=int(_v['id']),new_stock=int(_v['stock'] or 0)+int(it['quantity']))
                        else: c.execute('UPDATE products SET stock=stock+? WHERE id=?',(int(it['quantity']),int(it['product_id'])))
                    restocked_products.append(int(it['product_id']))
                    c.execute('UPDATE after_sales_items SET restocked=1 WHERE id=?',(it['id'],))
        if not restock and not external and self.inventory and self.inventory.warehouse:
            wh=self.inventory.warehouse;lid=wh.ensure_defaults(c)['returnLocation']['id']
            for it in case['items']:
                wh.inbound(c,product_id=int(it['product_id']),variant_id=it.get('variant_id'),quantity=int(it['quantity']),location_id=lid,reference_type='after_sales_quarantine',reference_id=f'{case_id}:{it["id"]}',note='退回待检，不计可售')
        c.execute("UPDATE gear_orders SET after_sales_status=? WHERE id=?",(next_status,case['order_id']))
        out=self._case(c,case_id)
        out['restockedProductIds']=sorted(set(restocked_products))
        out['externalFulfillment']=external
        return out

    def ship_exchange(self,c,*,case_id:str,carrier:str,tracking_no:str):
        case=self._case(c,case_id)
        if case['status']=='completed': return {**case,'idempotent':True}
        if case['case_type']!='exchange' or case['status']!='exchange_pending_shipment': raise ValueError('当前售后单不能执行换货发货')
        if not str(tracking_no or '').strip(): raise ValueError('trackingNo required')
        external = _external_order(c, int(case['order_id']))
        # 外部代发：换货由 PEAKTRACE 直接重发给顾客，不从本地仓出库
        for it in ([] if external else case['items']):
            if self.inventory and self.inventory.warehouse:
                self.inventory.warehouse.outbound_available(c,product_id=int(it['product_id']),variant_id=it.get('variant_id'),quantity=int(it['quantity']),reference_type='exchange',reference_id=case_id,idempotency_key=f'exchange:{case_id}:{it["id"]}',note='换货实物出库')
        c.execute('''UPDATE after_sales_cases SET status='completed',exchange_carrier=?,exchange_tracking_no=?,exchange_shipped_at=CURRENT_TIMESTAMP,
                     completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?''',(str(carrier or ''),str(tracking_no),case_id))
        self._refresh_order(c,int(case['order_id']))
        out=self._case(c,case_id)
        out['externalFulfillment']=external
        return out

    def attach_refund(self,c,*,case_id:str,refund_request_id:str):
        case=self._case(c,case_id)
        c.execute("UPDATE after_sales_cases SET refund_request_id=?,status='refund_processing',updated_at=CURRENT_TIMESTAMP WHERE id=?",(refund_request_id,case_id))
        c.execute("UPDATE gear_orders SET after_sales_status='refund_processing' WHERE id=?",(case['order_id'],))
        return self._case(c,case_id)

    def mark_refunded(self,c,*,case_id:str):
        case=self._case(c,case_id)
        if case['status']=='refunded': return {**case,'idempotent':True}
        c.execute("UPDATE after_sales_cases SET status='refunded',completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",(case_id,))
        self._refresh_order(c,int(case['order_id']))
        return self._case(c,case_id)

    def _refresh_order(self,c,order_id:int):
        order=c.execute('SELECT status FROM gear_orders WHERE id=?',(order_id,)).fetchone()
        if order and str(order['status'])=='refunded':
            c.execute("UPDATE gear_orders SET after_sales_status='refunded' WHERE id=?",(order_id,)); return
        active=int(c.execute("SELECT COUNT(*) FROM after_sales_cases WHERE order_id=? AND status IN ('pending_review','awaiting_return','return_in_transit','approved_pending_refund','refund_processing','exchange_pending_shipment')",(order_id,)).fetchone()[0])
        if active:
            c.execute("UPDATE gear_orders SET after_sales_status='processing' WHERE id=?",(order_id,)); return
        refunded=int(c.execute("SELECT COUNT(*) FROM after_sales_cases WHERE order_id=? AND status='refunded'",(order_id,)).fetchone()[0])
        completed=int(c.execute("SELECT COUNT(*) FROM after_sales_cases WHERE order_id=? AND status='completed'",(order_id,)).fetchone()[0])
        c.execute("UPDATE gear_orders SET after_sales_status=? WHERE id=?",('completed' if (refunded or completed) else None,order_id))
