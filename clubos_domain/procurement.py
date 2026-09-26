from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any


class ProcurementEngine:
    """Platform-owned supplier, procurement and inventory ledger for Gear.

    ClubOS owns supplier relations, purchase orders, physical receipt facts and the
    auditable stock ledger. Medusa receives the resulting on-hand quantity as a
    commerce/inventory projection; Medusa availability must not decide whether a
    physical warehouse receipt happened.
    """

    PO_STATUSES={"draft","approved","ordered","partially_received","received","cancelled"}

    def __init__(self, warehouse_engine=None, finance_engine=None):
        self.warehouse=warehouse_engine
        self.finance=finance_engine

    def _po_id(self) -> str:
        return "po_" + datetime.utcnow().strftime("%Y%m%d") + "_" + uuid.uuid4().hex[:8]

    def _receipt_id(self) -> str:
        return "grn_" + datetime.utcnow().strftime("%Y%m%d") + "_" + uuid.uuid4().hex[:8]

    def _movement_key(self, prefix: str) -> str:
        return prefix + ":" + uuid.uuid4().hex

    def list_suppliers(self, c, *, status: str | None = None) -> list[dict[str, Any]]:
        sql="""SELECT s.*,
                 (SELECT COUNT(*) FROM product_suppliers ps WHERE ps.supplier_id=s.id AND ps.status='active') product_count,
                 (SELECT COUNT(*) FROM purchase_orders po WHERE po.supplier_id=s.id AND po.status IN ('approved','ordered','partially_received')) open_po_count,
                 (SELECT COALESCE(SUM(po.total_amount),0) FROM purchase_orders po WHERE po.supplier_id=s.id AND po.status IN ('approved','ordered','partially_received')) open_po_value
               FROM suppliers s"""
        args=[]
        if status:
            sql += " WHERE s.status=?"; args.append(status)
        sql += " ORDER BY s.status='active' DESC,s.created_at DESC,s.id DESC"
        return [dict(r) for r in c.execute(sql,args).fetchall()]

    def get_supplier(self, c, supplier_id: int) -> dict[str, Any]:
        r=c.execute('SELECT * FROM suppliers WHERE id=?',(supplier_id,)).fetchone()
        if not r: raise LookupError('供应商不存在')
        out=dict(r)
        out['products']=[dict(x) for x in c.execute('''SELECT ps.*,p.name product_name,p.sku product_sku,p.stock,p.average_cost
              FROM product_suppliers ps JOIN products p ON p.id=ps.product_id
              WHERE ps.supplier_id=? ORDER BY ps.is_primary DESC,p.name''',(supplier_id,)).fetchall()]
        return out

    def create_supplier(self, c, payload: dict[str, Any]) -> dict[str, Any]:
        name=str(payload.get('name') or '').strip()
        if not name: raise ValueError('供应商名称不能为空')
        code=str(payload.get('code') or '').strip().upper() or ('SUP-'+uuid.uuid4().hex[:8].upper())
        if c.execute('SELECT 1 FROM suppliers WHERE code=?',(code,)).fetchone(): raise ValueError('供应商编码已存在')
        c.execute('''INSERT INTO suppliers(code,name,contact_name,phone,email,address,payment_terms,lead_time_days,status,note)
                     VALUES(?,?,?,?,?,?,?,?,?,?)''',(
            code,name,str(payload.get('contactName') or ''),str(payload.get('phone') or ''),str(payload.get('email') or ''),
            str(payload.get('address') or ''),str(payload.get('paymentTerms') or ''),max(0,int(payload.get('leadTimeDays') or 0)),
            str(payload.get('status') or 'active'),str(payload.get('note') or '')
        ))
        sid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        if payload.get('paymentTermDays') is not None:
            c.execute('UPDATE suppliers SET payment_term_days=? WHERE id=?',(max(0,int(payload.get('paymentTermDays') or 0)),sid))
        return self.get_supplier(c,sid)

    def update_supplier(self, c, supplier_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.get_supplier(c,supplier_id)
        mapping={'name':'name','contactName':'contact_name','phone':'phone','email':'email','address':'address','paymentTerms':'payment_terms','leadTimeDays':'lead_time_days','paymentTermDays':'payment_term_days','status':'status','note':'note'}
        sets=[]; vals=[]
        for src,dst in mapping.items():
            if src in payload:
                v=payload[src]
                if src in ('leadTimeDays','paymentTermDays'): v=max(0,int(v or 0))
                if src=='name' and not str(v or '').strip(): raise ValueError('供应商名称不能为空')
                sets.append(f'{dst}=?');vals.append(v)
        if sets:
            vals.append(supplier_id)
            c.execute(f"UPDATE suppliers SET {', '.join(sets)},updated_at=CURRENT_TIMESTAMP WHERE id=?",vals)
        return self.get_supplier(c,supplier_id)

    def upsert_product_supplier(self, c, *, product_id: int, supplier_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        if not c.execute('SELECT 1 FROM products WHERE id=?',(product_id,)).fetchone(): raise LookupError('商品不存在')
        supplier=self.get_supplier(c,supplier_id)
        if supplier['status']!='active': raise ValueError('供应商已停用，不能新增供货关系')
        price=max(0.0,float(payload.get('purchasePrice') or 0))
        moq=max(1,int(payload.get('minOrderQty') or 1))
        lead=max(0,int(payload.get('leadTimeDays') if payload.get('leadTimeDays') is not None else supplier.get('lead_time_days') or 0))
        primary=1 if bool(payload.get('isPrimary',False)) else 0
        if primary:
            c.execute('UPDATE product_suppliers SET is_primary=0,updated_at=CURRENT_TIMESTAMP WHERE product_id=?',(product_id,))
        existing=c.execute('SELECT id FROM product_suppliers WHERE product_id=? AND supplier_id=?',(product_id,supplier_id)).fetchone()
        if existing:
            c.execute('''UPDATE product_suppliers SET supplier_sku=?,purchase_price=?,min_order_qty=?,lead_time_days=?,is_primary=?,status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',(
                str(payload.get('supplierSku') or ''),price,moq,lead,primary,str(payload.get('status') or 'active'),existing['id']))
            link_id=int(existing['id'])
        else:
            c.execute('''INSERT INTO product_suppliers(product_id,supplier_id,supplier_sku,purchase_price,min_order_qty,lead_time_days,is_primary,status)
                         VALUES(?,?,?,?,?,?,?,?)''',(product_id,supplier_id,str(payload.get('supplierSku') or ''),price,moq,lead,primary,str(payload.get('status') or 'active')))
            link_id=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        return dict(c.execute('''SELECT ps.*,s.name supplier_name,p.name product_name,p.sku product_sku
            FROM product_suppliers ps JOIN suppliers s ON s.id=ps.supplier_id JOIN products p ON p.id=ps.product_id WHERE ps.id=?''',(link_id,)).fetchone())

    def product_suppliers(self, c, product_id: int) -> list[dict[str, Any]]:
        if not c.execute('SELECT 1 FROM products WHERE id=?',(product_id,)).fetchone(): raise LookupError('商品不存在')
        return [dict(r) for r in c.execute('''SELECT ps.*,s.name supplier_name,s.code supplier_code,s.status supplier_status
              FROM product_suppliers ps JOIN suppliers s ON s.id=ps.supplier_id
              WHERE ps.product_id=? ORDER BY ps.is_primary DESC,ps.id DESC''',(product_id,)).fetchall()]

    def _resolve_unit_cost(self, c, *, supplier_id: int, product_id: int, explicit: Any) -> float:
        if explicit not in (None,''):
            cost=float(explicit)
            if cost < 0: raise ValueError('采购单价不能小于 0')
            return cost
        r=c.execute('''SELECT purchase_price FROM product_suppliers WHERE supplier_id=? AND product_id=? AND status='active' ORDER BY is_primary DESC,id DESC LIMIT 1''',(supplier_id,product_id)).fetchone()
        if not r: raise ValueError(f'商品 #{product_id} 未配置该供应商的采购价，请先建立供货关系或在采购单中填写 unitCost')
        return max(0.0,float(r['purchase_price'] or 0))

    def create_purchase_order(self, c, *, supplier_id: int, items: list[dict[str, Any]], expected_at: str | None = None, note: str = '', created_by: str = 'platform') -> dict[str, Any]:
        supplier=self.get_supplier(c,supplier_id)
        if supplier['status']!='active': raise ValueError('供应商已停用')
        if not items: raise ValueError('采购单至少包含一个商品')
        normalized=[]; seen=set(); subtotal=0.0
        for raw in items:
            pid=int(raw.get('productId') or 0)
            if not pid or pid in seen: raise ValueError('采购单商品不能为空且同一商品只能出现一次')
            seen.add(pid)
            p=c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
            if not p: raise LookupError(f'商品不存在: {pid}')
            qty=int(raw.get('quantity') or 0)
            if qty<=0: raise ValueError('采购数量必须大于 0')
            link=c.execute('SELECT * FROM product_suppliers WHERE supplier_id=? AND product_id=?',(supplier_id,pid)).fetchone()
            min_qty=max(1,int(link['min_order_qty'] or 1)) if link else 1
            if qty<min_qty: raise ValueError(f"{p['name']} 最小采购量为 {min_qty}")
            cost=self._resolve_unit_cost(c,supplier_id=supplier_id,product_id=pid,explicit=raw.get('unitCost'))
            line=round(cost*qty,2); subtotal+=line
            supplier_sku=str(raw.get('supplierSku') or (link['supplier_sku'] if link else '') or '')
            normalized.append((pid,qty,cost,line,supplier_sku))
        po_id=self._po_id(); subtotal=round(subtotal,2)
        c.execute('''INSERT INTO purchase_orders(id,supplier_id,status,currency_code,subtotal,total_amount,expected_at,note,created_by)
                     VALUES(?,?,?,?,?,?,?,?,?)''',(po_id,supplier_id,'draft','cny',subtotal,subtotal,expected_at,note,created_by))
        for pid,qty,cost,line,ssku in normalized:
            c.execute('''INSERT INTO purchase_order_items(purchase_order_id,product_id,supplier_sku,quantity_ordered,quantity_received,unit_cost,line_total)
                         VALUES(?,?,?,?,?,?,?)''',(po_id,pid,ssku,qty,0,cost,line))
        return self.get_purchase_order(c,po_id)

    def get_purchase_order(self, c, po_id: str) -> dict[str, Any]:
        r=c.execute('''SELECT po.*,s.name supplier_name,s.code supplier_code FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id WHERE po.id=?''',(po_id,)).fetchone()
        if not r: raise LookupError('采购单不存在')
        out=dict(r)
        out['items']=[dict(x) for x in c.execute('''SELECT poi.*,p.name product_name,p.sku product_sku,p.stock,p.average_cost
              FROM purchase_order_items poi JOIN products p ON p.id=poi.product_id
              WHERE poi.purchase_order_id=? ORDER BY poi.id''',(po_id,)).fetchall()]
        out['orderedQty']=sum(int(x['quantity_ordered'] or 0) for x in out['items'])
        out['receivedQty']=sum(int(x['quantity_received'] or 0) for x in out['items'])
        out['outstandingQty']=out['orderedQty']-out['receivedQty']
        return out

    def list_purchase_orders(self, c, *, status: str | None = None, supplier_id: int | None = None) -> list[dict[str, Any]]:
        sql='''SELECT po.*,s.name supplier_name,s.code supplier_code,
              (SELECT COALESCE(SUM(quantity_ordered),0) FROM purchase_order_items i WHERE i.purchase_order_id=po.id) ordered_qty,
              (SELECT COALESCE(SUM(quantity_received),0) FROM purchase_order_items i WHERE i.purchase_order_id=po.id) received_qty
              FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id WHERE 1=1'''
        args=[]
        if status: sql+=' AND po.status=?';args.append(status)
        if supplier_id is not None: sql+=' AND po.supplier_id=?';args.append(supplier_id)
        sql+=' ORDER BY po.created_at DESC,po.id DESC'
        return [dict(r) for r in c.execute(sql,args).fetchall()]

    def approve_purchase_order(self, c, *, po_id: str, approved_by: str='platform') -> dict[str, Any]:
        po=self.get_purchase_order(c,po_id)
        if po['status']=='approved': return {**po,'idempotent':True}
        if po['status']!='draft': raise ValueError(f"当前采购单状态不能审核: {po['status']}")
        c.execute("UPDATE purchase_orders SET status='approved',approved_by=?,approved_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",(approved_by,po_id))
        return self.get_purchase_order(c,po_id)

    def place_purchase_order(self, c, *, po_id: str) -> dict[str, Any]:
        po=self.get_purchase_order(c,po_id)
        if po['status'] in ('ordered','partially_received','received'): return {**po,'idempotent':True}
        if po['status']!='approved': raise ValueError(f"当前采购单状态不能下单: {po['status']}")
        c.execute("UPDATE purchase_orders SET status='ordered',ordered_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",(po_id,))
        return self.get_purchase_order(c,po_id)

    def cancel_purchase_order(self, c, *, po_id: str, note: str='') -> dict[str, Any]:
        po=self.get_purchase_order(c,po_id)
        if po['status']=='cancelled': return {**po,'idempotent':True}
        if po['status'] not in ('draft','approved','ordered'): raise ValueError('已经发生入库的采购单不能直接取消')
        if int(po['receivedQty'] or 0)>0: raise ValueError('已经发生入库的采购单不能直接取消')
        c.execute("UPDATE purchase_orders SET status='cancelled',cancelled_at=CURRENT_TIMESTAMP,note=CASE WHEN ?!='' THEN ? ELSE note END,updated_at=CURRENT_TIMESTAMP WHERE id=?",(note,note,po_id))
        return self.get_purchase_order(c,po_id)

    def _variant_of(self,c,*,product_id:int,variant_id:int|None=None):
        """确定这次库存变动要落到哪个规格。

        库存真源在 product_variants。没传 variant_id 时取该商品的默认规格（迁移给每个
        商品建的那个 is_default=1），这样单规格与存量商品的语义和原来完全一致。
        多规格商品的调用方必须显式传 —— 否则「买 S 码扣 M 码库存」这种串账查不出来。
        """
        if variant_id:
            r=c.execute('SELECT * FROM product_variants WHERE id=? AND product_id=?',(int(variant_id),int(product_id))).fetchone()
            if not r: raise LookupError('规格不存在或不属于该商品')
            return dict(r)
        r=c.execute('SELECT * FROM product_variants WHERE product_id=? ORDER BY is_default DESC,sort,id LIMIT 1',(int(product_id),)).fetchone()
        return dict(r) if r else None

    def _write_variant_stock(self,c,*,product_id:int,variant_id:int,new_stock:int,sync_status:str|None=None) -> int:
        """写规格层库存，并把 products.stock 重算成各规格之和。

        products.stock 从真源降级为冗余字段（列表排序、采购预警读它），所以它必须
        每次规格变动后跟着变，否则后台看到的汇总和详情页看到的规格明细会对不上。
        """
        v=c.execute('SELECT id,stock FROM product_variants WHERE id=?',(int(variant_id),)).fetchone()
        if not v: raise LookupError('规格不存在')
        before=int(v['stock'] or 0)
        c.execute('UPDATE product_variants SET stock=? WHERE id=?',(int(new_stock),int(variant_id)))
        total=c.execute('SELECT COALESCE(SUM(stock),0) s FROM product_variants WHERE product_id=? AND status=\'active\'',(int(product_id),)).fetchone()['s']
        if sync_status: c.execute('UPDATE products SET stock=?,commerce_sync_status=? WHERE id=?',(int(total),sync_status,int(product_id)))
        else: c.execute('UPDATE products SET stock=? WHERE id=?',(int(total),int(product_id)))
        return before

    def _record_movement(self,c,*,product_id:int,movement_type:str,quantity_delta:int,stock_before:int,stock_after:int,
                         unit_cost:float|None=0,total_cost:float|None=None,reference_type:str='',reference_id:str='',note:str='',actor_type:str='platform',idempotency_key:str|None=None,variant_id:int|None=None):
        key=idempotency_key or self._movement_key(movement_type)
        existing=c.execute('SELECT * FROM inventory_movements WHERE idempotency_key=?',(key,)).fetchone()
        if existing: return dict(existing)
        total=float(total_cost if total_cost is not None else (float(unit_cost or 0)*abs(int(quantity_delta))))
        c.execute('''INSERT INTO inventory_movements(product_id,variant_id,movement_type,quantity_delta,stock_before,stock_after,unit_cost,total_cost,reference_type,reference_id,note,actor_type,idempotency_key)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(product_id,variant_id,movement_type,int(quantity_delta),int(stock_before),int(stock_after),float(unit_cost or 0),round(total,2),reference_type,reference_id,note,actor_type,key))
        return dict(c.execute('SELECT * FROM inventory_movements WHERE id=?',(c.execute('SELECT last_insert_rowid()').fetchone()[0],)).fetchone())

    def adjust_stock(self,c,*,product_id:int,quantity_delta:int,reason:str,actor_type:str='platform',reference_type:str='manual_adjustment',reference_id:str='',variant_id:int|None=None) -> dict[str,Any]:
        delta=int(quantity_delta)
        if delta==0: raise ValueError('库存调整数量不能为 0')
        p0=c.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone()
        if not p0: raise LookupError('商品不存在')
        p=dict(p0); v=self._variant_of(c,product_id=product_id,variant_id=variant_id)
        before=int(v['stock'] or 0) if v else int(p['stock'] or 0); after=before+delta
        if after<0: raise ValueError('库存不足，调整后库存不能小于 0')
        vid=int(v['id']) if v else None
        sync='pending' if p.get('commerce_product_id') or p.get('commerce_inventory_item_id') else p.get('commerce_sync_status','local')
        if v: self._write_variant_stock(c,product_id=product_id,variant_id=vid,new_stock=after,sync_status=sync)
        else: c.execute('UPDATE products SET stock=?,commerce_sync_status=? WHERE id=?',(after,sync,product_id))
        m=self._record_movement(c,product_id=product_id,movement_type='manual_in' if delta>0 else 'manual_out',quantity_delta=delta,
                                stock_before=before,stock_after=after,unit_cost=float(p.get('average_cost') or 0),reference_type=reference_type,
                                reference_id=reference_id,note=reason,actor_type=actor_type,variant_id=vid)
        if self.warehouse:
            d=self.warehouse.ensure_defaults(c); lid=int(d['defaultLocation']['id']); wi=self.warehouse._row(c,product_id,lid)
            if delta<0 and int(wi['on_hand'])-int(wi['reserved']) < abs(delta): raise ValueError('仓库可售实物库存不足')
            self.warehouse._update(c,product_id,lid,int(wi['on_hand'])+delta,int(wi['reserved']))
            self.warehouse._move(c,product_id=product_id,movement_type='manual_adjustment',quantity=abs(delta),from_location_id=lid if delta<0 else None,to_location_id=lid if delta>0 else None,reference_type=reference_type,reference_id=reference_id or str(m['id']),note=reason,actor_type=actor_type,idempotency_key=f'wms-manual:{m["id"]}')
        return {'ok':True,'productId':product_id,'variantId':vid,'stockBefore':before,'stockAfter':after,'quantityDelta':delta,'movementId':m['id'],'productIds':[product_id]}

    def set_absolute_stock(self,c,*,product_id:int,new_stock:int,reason:str='平台手工修改库存',actor_type:str='platform') -> dict[str,Any]:
        p=c.execute('SELECT stock FROM products WHERE id=?',(product_id,)).fetchone()
        if not p: raise LookupError('商品不存在')
        delta=int(new_stock)-int(p['stock'] or 0)
        if delta==0: return {'ok':True,'productId':product_id,'stockBefore':int(new_stock),'stockAfter':int(new_stock),'quantityDelta':0,'productIds':[],'idempotent':True}
        return self.adjust_stock(c,product_id=product_id,quantity_delta=delta,reason=reason,actor_type=actor_type)

    def sale_outbound(self,c,*,product_id:int,quantity:int,order_id:int,actor_type:str='system',variant_id:int|None=None) -> dict[str,Any]:
        q=max(1,int(quantity)); p0=c.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone()
        if not p0: raise LookupError('商品不存在')
        p=dict(p0); v=self._variant_of(c,product_id=product_id,variant_id=variant_id)
        before=int(v['stock'] or 0) if v else int(p['stock'] or 0)
        if before<q: raise ValueError('库存不足')
        after=before-q; vid=int(v['id']) if v else None
        if v: self._write_variant_stock(c,product_id=product_id,variant_id=vid,new_stock=after)
        else: c.execute('UPDATE products SET stock=? WHERE id=?',(after,product_id))
        m=self._record_movement(c,product_id=product_id,movement_type='sale_outbound',quantity_delta=-q,stock_before=before,stock_after=after,
                                unit_cost=float(p.get('average_cost') or 0),reference_type='gear_order',reference_id=str(order_id),
                                note=f'装备订单 #{order_id} 占用可售库存',actor_type=actor_type,variant_id=vid)
        if self.warehouse: self.warehouse.reserve_order(c,order_id=order_id,product_id=product_id,quantity=q,actor_type=actor_type)
        return {'productId':product_id,'variantId':vid,'stockBefore':before,'stockAfter':after,'movementId':m['id']}

    def refund_restock(self,c,*,product_id:int,quantity:int,order_id:int,actor_type:str='system',variant_id:int|None=None) -> dict[str,Any]:
        q=max(1,int(quantity)); p0=c.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone()
        if not p0: raise LookupError('商品不存在')
        p=dict(p0); v=self._variant_of(c,product_id=product_id,variant_id=variant_id)
        before=int(v['stock'] or 0) if v else int(p['stock'] or 0); after=before+q; vid=int(v['id']) if v else None
        if v: self._write_variant_stock(c,product_id=product_id,variant_id=vid,new_stock=after)
        else: c.execute('UPDATE products SET stock=? WHERE id=?',(after,product_id))
        m=self._record_movement(c,product_id=product_id,movement_type='refund_restock',quantity_delta=q,stock_before=before,stock_after=after,
                                unit_cost=float(p.get('average_cost') or 0),reference_type='gear_refund',reference_id=str(order_id),
                                note=f'装备订单 #{order_id} 全额退款恢复可售库存',actor_type=actor_type,variant_id=vid)
        if self.warehouse:
            released=self.warehouse.release_order(c,order_id=order_id,product_id=product_id,quantity=q,actor_type=actor_type)
            if int(released.get('released') or 0)<q:
                self.warehouse.inbound(c,product_id=product_id,quantity=q-int(released.get('released') or 0),reference_type='gear_refund',reference_id=str(order_id),note=f'装备订单 #{order_id} 退款实物回库',actor_type=actor_type,idempotency_key=f'wms-refund-in:{order_id}:{product_id}')
        return {'productId':product_id,'variantId':vid,'stockBefore':before,'stockAfter':after,'movementId':m['id']}

    def after_sales_restock(self,c,*,case_id:str,item_id:int,product_id:int,quantity:int,actor_type:str='platform',variant_id:int|None=None) -> dict[str,Any]:
        key=f'after_sales:{case_id}:item:{item_id}'
        existing=c.execute('SELECT * FROM inventory_movements WHERE idempotency_key=?',(key,)).fetchone()
        if existing:return {'productId':product_id,'stockBefore':existing['stock_before'],'stockAfter':existing['stock_after'],'movementId':existing['id'],'idempotent':True}
        q=max(1,int(quantity)); p0=c.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone()
        if not p0: raise LookupError('商品不存在')
        p=dict(p0); v=self._variant_of(c,product_id=product_id,variant_id=variant_id)
        before=int(v['stock'] or 0) if v else int(p['stock'] or 0); after=before+q; vid=int(v['id']) if v else None
        if v: self._write_variant_stock(c,product_id=product_id,variant_id=vid,new_stock=after,sync_status='pending' if p.get('commerce_inventory_item_id') else None)
        else: c.execute('UPDATE products SET stock=?,commerce_sync_status=CASE WHEN commerce_inventory_item_id IS NOT NULL THEN \'pending\' ELSE commerce_sync_status END WHERE id=?',(after,product_id))
        m=self._record_movement(c,product_id=product_id,movement_type='after_sales_return',quantity_delta=q,stock_before=before,stock_after=after,
                                unit_cost=float(p.get('average_cost') or 0),reference_type='after_sales',reference_id=case_id,
                                note=f'售后单 {case_id} 退货入库',actor_type=actor_type,idempotency_key=key,variant_id=vid)
        if self.warehouse:
            d=self.warehouse.ensure_defaults(c)
            self.warehouse.inbound(c,product_id=product_id,quantity=q,reference_type='after_sales',reference_id=case_id,note=f'售后单 {case_id} 退货回库',location_id=int(d['returnLocation']['id']),actor_type=actor_type,idempotency_key=f'wms-after-sales:{case_id}:item:{item_id}')
        return {'productId':product_id,'variantId':vid,'stockBefore':before,'stockAfter':after,'movementId':m['id']}

    def receive_purchase_order(self,c,*,po_id:str,items:list[dict[str,Any]],received_by:str='platform',note:str='') -> dict[str,Any]:
        po=self.get_purchase_order(c,po_id)
        if po['status'] not in ('ordered','partially_received'): raise ValueError(f"当前采购单状态不能入库: {po['status']}")
        if not items: raise ValueError('本次到货至少包含一个商品')
        requested={}
        for raw in items:
            iid=int(raw.get('purchaseOrderItemId') or 0); qty=int(raw.get('quantity') or 0)
            if iid<=0 or qty<=0: raise ValueError('purchaseOrderItemId / quantity 必须大于 0')
            if iid in requested: raise ValueError('同一采购明细本次入库只能出现一次')
            requested[iid]={'quantity':qty,'unitCost':raw.get('unitCost')}
        po_items={int(x['id']):x for x in po['items']}
        for iid,req in requested.items():
            qty=int(req['quantity'])
            if iid not in po_items: raise ValueError(f'采购单明细不存在: {iid}')
            line=po_items[iid]; outstanding=int(line['quantity_ordered'])-int(line['quantity_received'])
            if qty>outstanding: raise ValueError(f"{line['product_name']} 本次入库 {qty} 超过未到货数量 {outstanding}")
        receipt_id=self._receipt_id(); total_cost=0.0; changed=[]
        c.execute('''INSERT INTO purchase_receipts(id,purchase_order_id,supplier_id,status,received_by,note,total_cost)
                     VALUES(?,?,?,?,?,?,0)''',(receipt_id,po_id,po['supplier_id'],'received',received_by,note))
        for iid,req in requested.items():
            qty=int(req['quantity']); line=po_items[iid]; pid=int(line['product_id']); ordered_cost=float(line['unit_cost'] or 0)
            unit_cost=ordered_cost if req.get('unitCost') in (None,'') else float(req.get('unitCost'))
            if unit_cost<0: raise ValueError('实际到货单价不能小于 0')
            p=dict(c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone())
            before=int(p['stock'] or 0); after=before+qty; avg_before=float(p.get('average_cost') or 0)
            avg_after=unit_cost if avg_before<=0 else round(((before*avg_before)+(qty*unit_cost))/after,4) if after else unit_cost
            c.execute('''UPDATE products SET stock=?,average_cost=?,last_purchase_cost=?,last_inbound_at=CURRENT_TIMESTAMP,
                         commerce_sync_status=CASE WHEN commerce_inventory_item_id IS NOT NULL THEN 'pending' ELSE commerce_sync_status END WHERE id=?''',
                      (after,avg_after,unit_cost,pid))
            c.execute('UPDATE purchase_order_items SET quantity_received=quantity_received+? WHERE id=?',(qty,iid))
            line_total=round(qty*unit_cost,2); total_cost+=line_total
            variance=round((unit_cost-ordered_cost)*qty,2)
            c.execute('''INSERT INTO purchase_receipt_items(receipt_id,purchase_order_item_id,product_id,quantity,unit_cost,line_total,stock_before,stock_after,ordered_unit_cost,price_variance)
                         VALUES(?,?,?,?,?,?,?,?,?,?)''',(receipt_id,iid,pid,qty,unit_cost,line_total,before,after,ordered_cost,variance))
            self._record_movement(c,product_id=pid,movement_type='purchase_inbound',quantity_delta=qty,stock_before=before,stock_after=after,
                                  unit_cost=unit_cost,total_cost=line_total,reference_type='purchase_receipt',reference_id=receipt_id,
                                  note=f'采购单 {po_id} 到货入库',actor_type=received_by,idempotency_key=f'purchase_receipt:{receipt_id}:item:{iid}')
            if self.warehouse:
                self.warehouse.inbound(c,product_id=pid,quantity=qty,reference_type='purchase_receipt',reference_id=receipt_id,note=f'采购单 {po_id} 到货上架',actor_type=received_by,idempotency_key=f'wms-purchase:{receipt_id}:item:{iid}')
            changed.append(pid)
        c.execute('UPDATE purchase_receipts SET total_cost=? WHERE id=?',(round(total_cost,2),receipt_id))
        payable=self.finance.register_receipt_payable(c,receipt_id=receipt_id) if self.finance else None
        refreshed=self.get_purchase_order(c,po_id)
        new_status='received' if int(refreshed['outstandingQty'])==0 else 'partially_received'
        c.execute("UPDATE purchase_orders SET status=?,received_at=CASE WHEN ?='received' THEN CURRENT_TIMESTAMP ELSE received_at END,updated_at=CURRENT_TIMESTAMP WHERE id=?",(new_status,new_status,po_id))
        return {'ok':True,'receiptId':receipt_id,'purchaseOrderId':po_id,'status':new_status,'receivedCost':round(total_cost,2),'supplierPayable':payable,'productIds':sorted(set(changed)),'purchaseOrder':self.get_purchase_order(c,po_id)}

    def _return_id(self)->str:
        return 'prt_'+datetime.utcnow().strftime('%Y%m%d')+'_'+uuid.uuid4().hex[:8]

    def create_purchase_return(self,c,*,po_id:str,items:list[dict[str,Any]],note:str='',created_by:str='platform')->dict[str,Any]:
        po=self.get_purchase_order(c,po_id)
        if int(po.get('receivedQty') or 0)<=0: raise ValueError('采购单尚未到货，不能创建采购退货')
        if not items: raise ValueError('采购退货至少包含一个商品')
        po_items={int(x['id']):x for x in po['items']}; normalized=[]; total=0.0; seen=set()
        for raw in items:
            iid=int(raw.get('purchaseOrderItemId') or 0); qty=int(raw.get('quantity') or 0)
            if iid<=0 or qty<=0 or iid in seen: raise ValueError('采购退货明细无效或重复')
            seen.add(iid)
            if iid not in po_items: raise ValueError(f'采购单明细不存在: {iid}')
            line=po_items[iid]
            already=int(c.execute("""SELECT COALESCE(SUM(pri.quantity),0) FROM purchase_return_items pri JOIN purchase_returns pr ON pr.id=pri.purchase_return_id
                WHERE pri.purchase_order_item_id=? AND pr.status IN ('approved','shipped','credited')""",(iid,)).fetchone()[0] or 0)
            maxq=int(line['quantity_received'])-already
            if qty>maxq: raise ValueError(f"{line['product_name']} 最多可退 {maxq}")
            cost=float(raw.get('unitCost') if raw.get('unitCost') not in (None,'') else line['unit_cost'] or 0); line_total=round(cost*qty,2); total+=line_total
            normalized.append((iid,int(line['product_id']),qty,cost,line_total))
        rid=self._return_id()
        c.execute("INSERT INTO purchase_returns(id,supplier_id,purchase_order_id,status,total_amount,note,created_by) VALUES(?,?,?,'draft',?,?,?)",(rid,po['supplier_id'],po_id,round(total,2),note,created_by))
        for iid,pid,qty,cost,line_total in normalized:
            c.execute('INSERT INTO purchase_return_items(purchase_return_id,purchase_order_item_id,product_id,quantity,unit_cost,line_total) VALUES(?,?,?,?,?,?)',(rid,iid,pid,qty,cost,line_total))
        return self.get_purchase_return(c,rid)

    def get_purchase_return(self,c,return_id:str)->dict[str,Any]:
        r=c.execute('SELECT pr.*,s.name supplier_name FROM purchase_returns pr JOIN suppliers s ON s.id=pr.supplier_id WHERE pr.id=?',(return_id,)).fetchone()
        if not r: raise LookupError('采购退货单不存在')
        out=dict(r); out['items']=[dict(x) for x in c.execute('SELECT pri.*,p.name product_name,p.sku product_sku FROM purchase_return_items pri JOIN products p ON p.id=pri.product_id WHERE pri.purchase_return_id=? ORDER BY pri.id',(return_id,)).fetchall()]; return out

    def list_purchase_returns(self,c,*,supplier_id:int|None=None,status:str|None=None)->list[dict[str,Any]]:
        sql='SELECT pr.*,s.name supplier_name FROM purchase_returns pr JOIN suppliers s ON s.id=pr.supplier_id WHERE 1=1'; args=[]
        if supplier_id is not None: sql+=' AND pr.supplier_id=?'; args.append(supplier_id)
        if status: sql+=' AND pr.status=?'; args.append(status)
        sql+=' ORDER BY pr.created_at DESC,pr.id DESC'; return [dict(x) for x in c.execute(sql,args).fetchall()]

    def approve_purchase_return(self,c,*,return_id:str,approved_by:str='platform')->dict[str,Any]:
        r=self.get_purchase_return(c,return_id)
        if r['status']=='approved': return {**r,'idempotent':True}
        if r['status']!='draft': raise ValueError(f"当前采购退货状态不能审核: {r['status']}")
        c.execute("UPDATE purchase_returns SET status='approved',approved_by=?,approved_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",(approved_by,return_id)); return self.get_purchase_return(c,return_id)

    def ship_purchase_return(self,c,*,return_id:str,actor_type:str='platform')->dict[str,Any]:
        r=self.get_purchase_return(c,return_id)
        if r['status'] in ('shipped','credited'): return {**r,'idempotent':True,'productIds':[]}
        if r['status']!='approved': raise ValueError(f"当前采购退货状态不能出库: {r['status']}")
        changed=[]
        for it in r['items']:
            pid=int(it['product_id']); q=int(it['quantity']); p=dict(c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()); before=int(p['stock'] or 0)
            if before<q: raise ValueError(f"{p['name']} 可售库存不足，不能退回供应商")
            if self.warehouse: self.warehouse.outbound_available(c,product_id=pid,quantity=q,reference_type='purchase_return',reference_id=return_id,note=f'采购退货 {return_id} 出库',actor_type=actor_type,idempotency_key=f'wms-purchase-return:{return_id}:{pid}')
            after=before-q; c.execute("UPDATE products SET stock=?,commerce_sync_status=CASE WHEN commerce_inventory_item_id IS NOT NULL THEN 'pending' ELSE commerce_sync_status END WHERE id=?",(after,pid))
            self._record_movement(c,product_id=pid,movement_type='supplier_return',quantity_delta=-q,stock_before=before,stock_after=after,unit_cost=float(it['unit_cost'] or 0),total_cost=float(it['line_total'] or 0),reference_type='purchase_return',reference_id=return_id,note=f'采购退货 {return_id} 退回供应商',actor_type=actor_type,idempotency_key=f'purchase-return:{return_id}:{it["id"]}')
            changed.append(pid)
        c.execute("UPDATE purchase_returns SET status='shipped',shipped_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",(return_id,)); return {**self.get_purchase_return(c,return_id),'productIds':sorted(set(changed))}

    def credit_purchase_return(self,c,*,return_id:str,credit_amount:float|None=None,note:str='')->dict[str,Any]:
        r=self.get_purchase_return(c,return_id)
        if r['status']=='credited': return {**r,'idempotent':True}
        if r['status']!='shipped': raise ValueError(f"当前采购退货状态不能确认贷项: {r['status']}")
        amount=round(float(r['total_amount'] if credit_amount is None else credit_amount),2)
        if amount<0 or amount>float(r['total_amount'])+0.004: raise ValueError('贷项金额必须在 0 与退货金额之间')
        credit=self.finance.create_credit(c,supplier_id=int(r['supplier_id']),source_type='purchase_return',source_id=return_id,amount=amount,note=note or f'采购退货 {return_id} 供应商贷项') if self.finance and amount>0 else None
        c.execute("UPDATE purchase_returns SET status='credited',credit_amount=?,credited_at=CURRENT_TIMESTAMP,note=CASE WHEN ?!='' THEN ? ELSE note END,updated_at=CURRENT_TIMESTAMP WHERE id=?",(amount,note,note,return_id)); return {**self.get_purchase_return(c,return_id),'supplierCredit':credit}

    def list_receipts(self,c,*,po_id:str|None=None,supplier_id:int|None=None) -> list[dict[str,Any]]:
        sql='''SELECT r.*,s.name supplier_name FROM purchase_receipts r JOIN suppliers s ON s.id=r.supplier_id WHERE 1=1''';args=[]
        if po_id: sql+=' AND r.purchase_order_id=?';args.append(po_id)
        if supplier_id is not None: sql+=' AND r.supplier_id=?';args.append(supplier_id)
        sql+=' ORDER BY r.received_at DESC,r.id DESC'
        out=[]
        for r in c.execute(sql,args).fetchall():
            d=dict(r);d['items']=[dict(x) for x in c.execute('''SELECT ri.*,p.name product_name,p.sku product_sku FROM purchase_receipt_items ri JOIN products p ON p.id=ri.product_id WHERE ri.receipt_id=? ORDER BY ri.id''',(d['id'],)).fetchall()];out.append(d)
        return out

    def list_movements(self,c,*,product_id:int|None=None,movement_type:str|None=None,limit:int=200) -> list[dict[str,Any]]:
        sql='''SELECT m.*,p.name product_name,p.sku product_sku FROM inventory_movements m JOIN products p ON p.id=m.product_id WHERE 1=1''';args=[]
        if product_id is not None: sql+=' AND m.product_id=?';args.append(product_id)
        if movement_type: sql+=' AND m.movement_type=?';args.append(movement_type)
        sql+=' ORDER BY m.id DESC LIMIT ?';args.append(max(1,min(1000,int(limit))))
        return [dict(r) for r in c.execute(sql,args).fetchall()]

    def inventory_summary(self,c) -> dict[str,Any]:
        r=c.execute('''SELECT COUNT(*) sku_count,COALESCE(SUM(stock),0) units,
                       COALESCE(SUM(stock*average_cost),0) inventory_value,
                       COALESCE(SUM(CASE WHEN stock<=reorder_point THEN 1 ELSE 0 END),0) low_stock_count
                       FROM products''').fetchone()
        open_po=c.execute("SELECT COUNT(*),COALESCE(SUM(total_amount),0) FROM purchase_orders WHERE status IN ('approved','ordered','partially_received')").fetchone()
        return {'skuCount':int(r['sku_count'] or 0),'units':int(r['units'] or 0),'inventoryValue':round(float(r['inventory_value'] or 0),2),
                'lowStockCount':int(r['low_stock_count'] or 0),'openPurchaseOrders':int(open_po[0] or 0),'openPurchaseValue':round(float(open_po[1] or 0),2)}
