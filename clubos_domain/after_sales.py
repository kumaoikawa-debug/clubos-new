from __future__ import annotations
import json, uuid
from typing import Any

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
        out['items']=[dict(x) for x in c.execute('''SELECT ai.*,goi.product_id,goi.unit_price,p.name product_name,p.sku
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
        cash_ratio=(float(order.get('cash_paid') or 0)/float(order.get('total') or 1)) if float(order.get('total') or 0)>0 else 0
        requested_cash=0.0 if case_type=='exchange' else round(gross*cash_ratio,2)
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
        restocked_products=[]
        if restock:
            for it in case['items']:
                if not int(it.get('restocked') or 0):
                    if self.inventory:
                        self.inventory.after_sales_restock(c,case_id=case_id,item_id=int(it['id']),product_id=int(it['product_id']),quantity=int(it['quantity']),actor_type='platform_after_sales')
                    else:
                        c.execute('UPDATE products SET stock=stock+? WHERE id=?',(int(it['quantity']),int(it['product_id'])))
                    restocked_products.append(int(it['product_id']))
                    c.execute('UPDATE after_sales_items SET restocked=1 WHERE id=?',(it['id'],))
        c.execute("UPDATE gear_orders SET after_sales_status=? WHERE id=?",(next_status,case['order_id']))
        out=self._case(c,case_id)
        out['restockedProductIds']=sorted(set(restocked_products))
        return out

    def ship_exchange(self,c,*,case_id:str,carrier:str,tracking_no:str):
        case=self._case(c,case_id)
        if case['status']=='completed': return {**case,'idempotent':True}
        if case['case_type']!='exchange' or case['status']!='exchange_pending_shipment': raise ValueError('当前售后单不能执行换货发货')
        if not str(tracking_no or '').strip(): raise ValueError('trackingNo required')
        c.execute('''UPDATE after_sales_cases SET status='completed',exchange_carrier=?,exchange_tracking_no=?,exchange_shipped_at=CURRENT_TIMESTAMP,
                     completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?''',(str(carrier or ''),str(tracking_no),case_id))
        self._refresh_order(c,int(case['order_id']))
        return self._case(c,case_id)

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
