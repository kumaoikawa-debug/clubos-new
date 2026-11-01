from __future__ import annotations

import uuid
import json
import hashlib
from . import stock_control as sc
from datetime import datetime
from typing import Any
from clubos_domain.product_stock import variant_of, write_variant_stock, resync_product_stock, resync_sellable_from_wms


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

    def delete_supplier(self, c, supplier_id: int) -> dict[str, Any]:
        """硬删供应商。带关联保护的删除：若供应商已挂商品供货关系或存在未作废采购单，
        直接硬删会让 product_suppliers / purchase_orders 外键悬空（列表一片空白、筛不到），
        所以先拦截并给出可执行提示；仅在无依赖时才删除，并顺手清掉 brand_suppliers 关联。"""
        self.get_supplier(c,supplier_id)  # 不存在则抛 LookupError
        linked=int(c.execute('SELECT COUNT(*) FROM product_suppliers WHERE supplier_id=?',(supplier_id,)).fetchone()[0])
        open_po=int(c.execute("SELECT COUNT(*) FROM purchase_orders WHERE supplier_id=? AND status!='cancelled'",(supplier_id,)).fetchone()[0])
        if linked or open_po:
            raise ValueError('该供应商已关联商品或存在采购单，无法删除。请先解除商品关联或作废采购单后再删。')
        c.execute('DELETE FROM brand_suppliers WHERE supplier_id=?',(supplier_id,))
        c.execute('DELETE FROM suppliers WHERE id=?',(supplier_id,))
        return {'ok':True,'id':supplier_id}

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
        groups={}
        for raw in items:
            pid=int(raw.get("productId") or 0);sc.owned(c,pid)
            vid=raw.get("variantId")
            vs=c.execute("SELECT id FROM product_variants WHERE product_id=? AND status='active'",(pid,)).fetchall()
            if not vid and len(vs)==1:vid=vs[0]["id"]
            if not vid:raise ValueError("多规格采购必须逐 SKU 填写计划数量")
            v=sc.sku(c,pid,int(vid));q=int(raw.get("quantity") or 0)
            if q<=0:raise ValueError("采购数量必须大于 0")
            plan=groups.setdefault(pid,[])
            if any(x["variantId"]==v["id"] for x in plan):raise ValueError("采购 SKU 重复")
            cost=self._resolve_unit_cost(c,supplier_id=supplier_id,product_id=pid,explicit=raw.get("unitCost"))
            plan.append({"variantId":v["id"],"quantity":q,"unitCost":cost})
        items=[{"productId":pid,"quantity":sum(x["quantity"] for x in plan),"unitCost":sum(x["quantity"]*x["unitCost"] for x in plan)/sum(x["quantity"] for x in plan)} for pid,plan in groups.items()]
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
        for pid,plan in groups.items():
            c.execute('UPDATE purchase_order_items SET sku_plan_json=?,variant_id=? WHERE purchase_order_id=? AND product_id=?',(json.dumps(plan),plan[0]['variantId'] if len(plan)==1 else None,po_id,pid))
        return self.get_purchase_order(c,po_id)

    def get_purchase_order(self, c, po_id: str) -> dict[str, Any]:
        r=c.execute('''SELECT po.*,s.name supplier_name,s.code supplier_code FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id WHERE po.id=?''',(po_id,)).fetchone()
        if not r: raise LookupError('采购单不存在')
        out=dict(r)
        out['supplierShipments']=[dict(x) for x in c.execute('SELECT * FROM purchase_shipments WHERE purchase_order_id=? ORDER BY id',(po_id,))]
        # V1.1：未到货量 = ordered - received - rejected（任务书 4.2③）。
        # 拒收/短少的量永远不会到，不能挂在「未到货」里卡住采购单收完。
        rejected={int(x['purchase_order_item_id']):int(x['qty'] or 0) for x in
                  c.execute("SELECT purchase_order_item_id,SUM(quantity) qty FROM purchase_order_exceptions WHERE purchase_order_id=? AND resolution='closed' GROUP BY purchase_order_item_id",(po_id,)).fetchall()}
        out['items']=[dict(x) for x in c.execute('''SELECT poi.*,p.name product_name,p.sku product_sku,p.stock,p.average_cost
              FROM purchase_order_items poi JOIN products p ON p.id=poi.product_id
              WHERE poi.purchase_order_id=? ORDER BY poi.id''',(po_id,)).fetchall()]
        for x in out['items']:
            x['skuItems']=json.loads(x.get('sku_plan_json') or '[]')
            for z in x['skuItems']:
                z['closed']=int(c.execute("SELECT COALESCE(SUM(quantity),0) FROM purchase_order_exceptions WHERE purchase_order_item_id=? AND variant_id=? AND resolution='closed'",(x['id'],z['variantId'])).fetchone()[0])
                z['received']=int(c.execute('SELECT COALESCE(SUM(quantity),0) FROM purchase_receipt_items WHERE purchase_order_item_id=? AND variant_id=?',(x['id'],z['variantId'])).fetchone()[0])
            x['quantity_rejected']=rejected.get(int(x['id']),0)
            x['quantity_outstanding']=int(x['quantity_ordered'] or 0)-int(x['quantity_received'] or 0)-int(x['quantity_rejected'])
        out['orderedQty']=sum(int(x['quantity_ordered'] or 0) for x in out['items'])
        out['receivedQty']=sum(int(x['quantity_received'] or 0) for x in out['items'])
        out['rejectedQty']=sum(rejected.values())
        out['outstandingQty']=out['orderedQty']-out['receivedQty']-out['rejectedQty']
        out['exceptions']=[dict(x) for x in c.execute('''SELECT e.*,p.name product_name FROM purchase_order_exceptions e
              JOIN products p ON p.id=e.product_id WHERE e.purchase_order_id=? ORDER BY e.id DESC''',(po_id,)).fetchall()]
        return out

    def list_purchase_orders(self, c, *, status: str | None = None, supplier_id: int | None = None) -> list[dict[str, Any]]:
        sql='''SELECT po.*,s.name supplier_name,s.code supplier_code,
              (SELECT COALESCE(SUM(quantity_ordered),0) FROM purchase_order_items i WHERE i.purchase_order_id=po.id) ordered_qty,
              (SELECT COALESCE(SUM(quantity_received),0) FROM purchase_order_items i WHERE i.purchase_order_id=po.id) received_qty,
              (SELECT COALESCE(SUM(quantity),0) FROM purchase_order_exceptions e WHERE e.purchase_order_id=po.id AND e.resolution='closed') rejected_qty
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
        # V1.1 在途联动（任务书 5.1）：确认下单 → 全部订购量进入 WMS「在途」。
        # on_order 只是实物库存之外的期望量（可售=on_hand-reserved，不包含在途），下单幂等性
        # 由状态机保证：只有 approved→ordered 这一次会走这里，重复调用在上面就返回了。
        for x in po['items']:
            if int(x['quantity_ordered'] or 0)>0:
                self._on_order_delta(c,product_id=int(x['product_id']),delta=int(x['quantity_ordered']))
        return self.get_purchase_order(c,po_id)

    def cancel_purchase_order(self, c, *, po_id: str, note: str='') -> dict[str, Any]:
        po=self.get_purchase_order(c,po_id)
        if po['status']=='cancelled': return {**po,'idempotent':True}
        if po['status'] not in ('draft','approved','ordered'): raise ValueError('已经发生入库的采购单不能直接取消')
        if int(po['receivedQty'] or 0)>0: raise ValueError('已经发生入库的采购单不能直接取消')
        c.execute("UPDATE purchase_orders SET status='cancelled',cancelled_at=CURRENT_TIMESTAMP,note=CASE WHEN ?!='' THEN ? ELSE note END,updated_at=CURRENT_TIMESTAMP WHERE id=?",(note,note,po_id))
        # V1.1：已下单的采购单取消 → 撤销在途（下单时加过 on_order，取消必须减回来）。
        if po['status']=='ordered':
            for x in po['items']:
                if int(x['quantity_ordered'] or 0)>0:
                    self._on_order_delta(c,product_id=int(x['product_id']),delta=-int(x['quantity_outstanding']))
        return self.get_purchase_order(c,po_id)

    def _on_order_delta(self,c,*,product_id:int,delta:int):
        """V1.1 在途数量联动。warehouse_inventory 行不存在时先建（on_hand/reserved 为 0）。"""
        if not self.warehouse or delta==0: return
        d=self.warehouse.ensure_defaults(c); lid=int(d['defaultLocation']['id'])
        r=self.warehouse._row(c,product_id,lid)
        new_oo=max(0,int(r['on_order'] or 0)+int(delta))
        c.execute('UPDATE warehouse_inventory SET on_order=?,updated_at=CURRENT_TIMESTAMP WHERE product_id=? AND location_id=?',(new_oo,product_id,lid))

    def _variant_of(self,c,*,product_id:int,variant_id:int|None=None):
        return variant_of(c,product_id=product_id,variant_id=variant_id)
    def _write_variant_stock(self,c,*,product_id:int,variant_id:int,new_stock:int,sync_status:str|None=None) -> int:
        return write_variant_stock(c,product_id=product_id,variant_id=variant_id,new_stock=new_stock,sync_status=sync_status)

    def _record_movement(self,c,*,product_id:int,movement_type:str,quantity_delta:int,stock_before:int,stock_after:int,
                         unit_cost:float|None=0,total_cost:float|None=None,reference_type:str='',reference_id:str='',note:str='',actor_type:str='platform',idempotency_key:str|None=None,variant_id:int|None=None):
        key=idempotency_key or self._movement_key(movement_type)
        existing=c.execute('SELECT * FROM inventory_movements WHERE idempotency_key=?',(key,)).fetchone()
        if existing: return dict(existing)
        total=float(total_cost if total_cost is not None else (float(unit_cost or 0)*abs(int(quantity_delta))))
        c.execute('''INSERT INTO inventory_movements(product_id,variant_id,movement_type,quantity_delta,stock_before,stock_after,unit_cost,total_cost,reference_type,reference_id,note,actor_type,idempotency_key)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(product_id,variant_id,movement_type,int(quantity_delta),int(stock_before),int(stock_after),float(unit_cost or 0),round(total,2),reference_type,reference_id,note,actor_type,key))
        return dict(c.execute('SELECT * FROM inventory_movements WHERE id=?',(c.execute('SELECT last_insert_rowid()').fetchone()[0],)).fetchone())

    def adjust_stock(self,c,*,product_id:int,quantity_delta:int,reason:str,actor_type:str='platform',reference_type:str='manual_adjustment',reference_id:str='',variant_id:int|None=None):
        v=sc.sku(c,product_id,variant_id);delta=int(quantity_delta)
        if not delta:raise ValueError('调整数量不能为 0')
        before=int(v['stock']);ref=reference_id or uuid.uuid4().hex
        if delta>0:self.warehouse.inbound(c,product_id=product_id,variant_id=v['id'],quantity=delta,reference_type=reference_type,reference_id=ref,note=reason,actor_type=actor_type)
        else:self.warehouse.outbound_available(c,product_id=product_id,variant_id=v['id'],quantity=-delta,reference_type=reference_type,reference_id=ref,note=reason,actor_type=actor_type)
        after=c.execute('SELECT stock FROM product_variants WHERE id=?',(v['id'],)).fetchone()[0]
        m=self._record_movement(c,product_id=product_id,variant_id=v['id'],movement_type='manual_in' if delta>0 else 'manual_out',quantity_delta=delta,stock_before=before,stock_after=after,reference_type=reference_type,reference_id=ref,note=reason,actor_type=actor_type)
        return {'ok':True,'productId':product_id,'variantId':v['id'],'stockBefore':before,'stockAfter':after,'quantityDelta':delta,'movementId':m['id'],'productIds':[product_id]}

    def set_absolute_stock(self,c,*,product_id:int,new_stock:int,reason:str='平台手工修改库存',actor_type:str='platform',variant_id:int|None=None) -> dict[str,Any]:
        """把库存设成绝对值。库存真源在规格层，所以「设成多少」也必须落在具体规格上。

        多规格商品没有「总库存」可设：delta 若按 products.stock（各规格之和）算、却落到
        单个规格上，数字必然对不上。所以未指定规格且存在多个启用规格时直接报错（app.py 转成
        409），让运营去规格管理里逐个改，而不是静默写歪。
        """
        if variant_id is None:
            n=c.execute("SELECT COUNT(*) n FROM product_variants WHERE product_id=? AND status='active'",(product_id,)).fetchone()['n']
            if int(n or 0)>1: raise ValueError('该商品有多个规格，库存按规格分别管理，请在「规格」里逐个调整')
        p=c.execute('SELECT stock FROM products WHERE id=?',(product_id,)).fetchone()
        if not p: raise LookupError('商品不存在')
        v=self._variant_of(c,product_id=product_id,variant_id=variant_id)
        vid=int(v['id']) if v else None
        before=int(v['stock'] or 0) if v else int(p['stock'] or 0)
        delta=int(new_stock)-before
        if delta==0: return {'ok':True,'productId':product_id,'variantId':vid,'stockBefore':before,'stockAfter':before,'quantityDelta':0,'productIds':[],'idempotent':True}
        return self.adjust_stock(c,product_id=product_id,quantity_delta=delta,reason=reason,actor_type=actor_type,variant_id=vid)

    def sale_outbound(self,c,*,product_id:int,quantity:int,order_id:int,actor_type:str='system',variant_id:int|None=None):
        v=sc.sku(c,product_id,variant_id);before=int(v['stock']);q=int(quantity)
        self.warehouse.reserve_order(c,order_id=order_id,product_id=product_id,variant_id=v['id'],quantity=q,actor_type=actor_type)
        after=c.execute('SELECT stock FROM product_variants WHERE id=?',(v['id'],)).fetchone()[0]
        m=self._record_movement(c,product_id=product_id,variant_id=v['id'],movement_type='sale_outbound',quantity_delta=-q,stock_before=after+q,stock_after=after,reference_type='gear_order',reference_id=str(order_id),actor_type=actor_type,idempotency_key=f'sale:{order_id}:{v["id"]}')
        return {'productId':product_id,'variantId':v['id'],'stockBefore':before,'stockAfter':after,'movementId':m['id']}

    def refund_restock(self,c,*,product_id:int,quantity:int,order_id:int,actor_type:str='system',variant_id:int|None=None):
        v=sc.sku(c,product_id,variant_id);before=int(v['stock'])
        out=self.warehouse.release_order(c,order_id=order_id,product_id=product_id,variant_id=v['id'],quantity=quantity,actor_type=actor_type)
        q=int(out['released']);after=before+q
        if q:self._record_movement(c,product_id=product_id,variant_id=v['id'],movement_type='refund_restock',quantity_delta=q,stock_before=before,stock_after=after,reference_type='gear_refund',reference_id=str(order_id),actor_type=actor_type,idempotency_key=f'refund:{order_id}:{v["id"]}')
        return {'productId':product_id,'variantId':v['id'],'stockBefore':before,'stockAfter':after}

    def after_sales_restock(self,c,*,case_id:str,item_id:int,product_id:int,quantity:int,actor_type:str='platform',variant_id:int|None=None):
        v=sc.sku(c,product_id,variant_id);key=f'after_sales:{case_id}:item:{item_id}'
        old=c.execute('SELECT id FROM inventory_movements WHERE idempotency_key=?',(key,)).fetchone()
        if old:return {'productId':product_id,'idempotent':True}
        before=v['stock']
        self.warehouse.inbound(c,product_id=product_id,variant_id=v['id'],quantity=quantity,reference_type='after_sales',reference_id=f'{case_id}:{item_id}',actor_type=actor_type)
        self._record_movement(c,product_id=product_id,variant_id=v['id'],movement_type='after_sales_return',quantity_delta=quantity,stock_before=before,stock_after=before+quantity,reference_type='after_sales',reference_id=case_id,actor_type=actor_type,idempotency_key=key)
        return {'productId':product_id,'stockBefore':before,'stockAfter':before+quantity}

    def receive_purchase_order(self,c,*,po_id:str,items:list[dict[str,Any]],received_by:str='platform',note:str='',location_id:int|None=None,request_key:str|None=None) -> dict[str,Any]:
        request_hash=hashlib.sha256(json.dumps({'po':po_id,'items':items,'location':location_id},sort_keys=True).encode()).hexdigest()
        if request_key:
            old=c.execute('SELECT * FROM purchase_receipts WHERE request_key=?',(request_key,)).fetchone()
            if old:
                if old['purchase_order_id']!=po_id:raise ValueError('收货请求键已用于另一采购单')
                if old['request_hash'] and old['request_hash']!=request_hash:raise ValueError('本次请求键已经入账；修改收货数量需新建真实收货批次')
                return {'ok':True,'idempotent':True,'receiptId':old['id'],'productIds':[],'purchaseOrder':self.get_purchase_order(c,po_id)}
        po=self.get_purchase_order(c,po_id)
        if po['status'] not in ('ordered','partially_received'): raise ValueError(f"当前采购单状态不能入库: {po['status']}")
        if not items: raise ValueError('本次到货至少包含一个商品')
        requested={}
        for raw in items:
            iid=int(raw.get('purchaseOrderItemId') or 0); qty=int(raw.get('quantity') or 0)
            if iid<=0 or qty<=0: raise ValueError('purchaseOrderItemId / quantity 必须大于 0')
            vid=raw.get('variantId') or None
            vid=int(vid) if vid not in (None,'') else None
            # V1.1：键从 iid 改成 (iid, variantId) —— 同一采购明细允许一次收进多个规格
            # （30 件冲锋衣拆成 红-M 10 / 红-L 20），但同一规格只能出现一行。
            key=(iid,vid)
            if key in requested: raise ValueError('同一采购明细的同一规格本次入库只能出现一次')
            requested[key]={'quantity':qty,'unitCost':raw.get('unitCost'),'variantId':vid}
        po_items={int(x['id']):x for x in po['items']}
        # 未到货校验按明细聚合：同一明细多个规格行合计不得超过 ordered-received-rejected。
        agg={}
        for (iid,_vid),req in requested.items(): agg[iid]=agg.get(iid,0)+int(req['quantity'])
        for iid,qty in agg.items():
            if iid not in po_items: raise ValueError(f'采购单明细不存在: {iid}')
            line=po_items[iid]; outstanding=int(line['quantity_outstanding'])
            if qty>outstanding: raise ValueError(f"{line['product_name']} 本次入库 {qty} 超过未到货数量 {outstanding}")
        receipt_id=self._receipt_id(); total_cost=0.0; changed=[]
        for (iid,vid),req in requested.items():
            line=po_items[iid];v=sc.sku(c,int(line['product_id']),vid);req['variantId']=v['id']
            plan=json.loads(line.get('sku_plan_json') or '[]')
            if plan:
                planned=next((x for x in plan if x['variantId']==v['id']),None)
                if not planned:raise ValueError('该 SKU 不在采购计划内')
                received=int(c.execute('SELECT COALESCE(SUM(quantity),0) FROM purchase_receipt_items WHERE purchase_order_item_id=? AND variant_id=?',(iid,v['id'])).fetchone()[0])
                if received+req['quantity']+int(planned.get('closed') or 0)>planned['quantity']:raise ValueError('该 SKU 实收超过订购余量')
                if req.get('unitCost') in (None,''):req['unitCost']=planned['unitCost']
        c.execute('''INSERT INTO purchase_receipts(id,purchase_order_id,supplier_id,status,received_by,note,total_cost)
                     VALUES(?,?,?,?,?,?,0)''',(receipt_id,po_id,po['supplier_id'],'received',received_by,note))
        c.execute('UPDATE purchase_receipts SET request_key=?,request_hash=? WHERE id=?',(request_key,request_hash,receipt_id))
        for key,req in requested.items():
            iid,vid=key  # ★ 幂等键要用本行的规格 id；不能用解析循环残留的 vid（会让第二行被幂等吞掉）
            qty=int(req['quantity']); line=po_items[iid]; pid=int(line['product_id']); ordered_cost=float(line['unit_cost'] or 0)
            unit_cost=ordered_cost if req.get('unitCost') in (None,'') else float(req.get('unitCost'))
            if unit_cost<0: raise ValueError('实际到货单价不能小于 0')
            p=dict(c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone())
            # 采购明细没有规格概念，入库一律落到默认规格；库存真源在规格层，
            # 只写 products.stock 的话，下一次规格变动触发汇总重算就会把这次补货抹掉。
            # V1：允许验收时显式指定规格（variantId），多规格商品按颜色/尺码分别收货；
            # 不传则退回默认规格（与存量单规格商品语义一致）。
            v=variant_of(c,product_id=pid, variant_id=req.get('variantId'))
            before=int(v['stock'] or 0) if v else int(p['stock'] or 0); after=before+qty
            avg_before=float(p.get('average_cost') or 0)
            physical=int(c.execute('SELECT COALESCE(SUM(on_hand),0) FROM warehouse_sku_balances WHERE product_id=?',(pid,)).fetchone()[0])
            sku_physical=int(c.execute('SELECT COALESCE(SUM(on_hand),0) FROM warehouse_sku_balances WHERE variant_id=?',(v['id'],)).fetchone()[0])
            avg_after=unit_cost if avg_before<=0 else round((physical*avg_before+qty*unit_cost)/(physical+qty),4)
            c.execute('''UPDATE products SET average_cost=?,last_purchase_cost=?,last_inbound_at=CURRENT_TIMESTAMP,
                         commerce_sync_status=CASE WHEN commerce_inventory_item_id IS NOT NULL THEN 'pending' ELSE commerce_sync_status END WHERE id=?''',
                      (avg_after,unit_cost,pid))
            if v:
                write_variant_stock(c,product_id=pid,variant_id=int(v['id']),new_stock=after)
                # 规格层采购成本同步滚动加权：毛利/结算按规格成本算才准，
                # 只更 products.average_cost 会让多规格商品的成本全糊在一起。
                v_avg=unit_cost if float(v['purchase_cost'] or 0)<=0 else round((sku_physical*float(v['purchase_cost'])+qty*unit_cost)/(sku_physical+qty),4)
                c.execute('UPDATE product_variants SET purchase_cost=? WHERE id=?',(v_avg,int(v['id'])))
            else: c.execute('UPDATE products SET stock=? WHERE id=?',(after,pid))
            c.execute('UPDATE purchase_order_items SET quantity_received=quantity_received+? WHERE id=?',(qty,iid))
            line_total=round(qty*unit_cost,2); total_cost+=line_total
            variance=round((unit_cost-ordered_cost)*qty,2)
            c.execute('''INSERT INTO purchase_receipt_items(receipt_id,purchase_order_item_id,product_id,quantity,unit_cost,line_total,stock_before,stock_after,ordered_unit_cost,price_variance,variant_id)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(receipt_id,iid,pid,qty,unit_cost,line_total,before,after,ordered_cost,variance,v['id']))
            self._record_movement(c,product_id=pid,movement_type='purchase_inbound',quantity_delta=qty,stock_before=before,stock_after=after,
                                  unit_cost=unit_cost,total_cost=line_total,reference_type='purchase_receipt',reference_id=receipt_id,
                                  note=f'采购单 {po_id} 到货入库',actor_type=received_by,idempotency_key=f'purchase_receipt:{receipt_id}:item:{iid}:v{vid or 0}',variant_id=int(v['id']) if v else None)
            if self.warehouse:
                self.warehouse.inbound(c,product_id=pid,variant_id=v['id'],quantity=qty,reference_type='purchase_receipt',reference_id=receipt_id,location_id=location_id,note=f'采购单 {po_id} 到货上架',actor_type=received_by,idempotency_key=f'wms-purchase:{receipt_id}:item:{iid}:v{vid or 0}')
            # V1.1 在途联动（任务书 5.1）：到货入库 → 对应数量从「在途」退出。
            self._on_order_delta(c,product_id=pid,delta=-qty)
            changed.append(pid)
        # V1 进销存：收后防御性重算汇总库存（双账本一致性兜底，防止旁路直写 products.stock 漏算），
        # 并把仍处于「入库前」状态的商品自动推进到「待上架」——运营在商城经营里配好售价后即可上架。
        # 已上架(active)的商品补货不会被降级，避免影响在售。
        # V1.2 真源反转：收货入的是 WMS 实物台账 → 可售库存以 resync_sellable_from_wms 派生收口。
        for pid in set(changed):
            if self.warehouse: resync_sellable_from_wms(c, product_id=pid)
            else: resync_product_stock(c, product_id=pid)
            cur=c.execute('SELECT status FROM products WHERE id=?',(pid,)).fetchone()['status']
            if cur in ('draft','pending_inbound','partially_received','pending_listing'):
                c.execute("UPDATE products SET status='pending_listing' WHERE id=?",(pid,))
        c.execute('UPDATE purchase_receipts SET total_cost=? WHERE id=?',(round(total_cost,2),receipt_id))
        payable=self.finance.register_receipt_payable(c,receipt_id=receipt_id) if self.finance else None
        refreshed=self.get_purchase_order(c,po_id)
        new_status='received' if int(refreshed['outstandingQty'])==0 else 'partially_received'
        if new_status=='received':c.execute("UPDATE purchase_order_exceptions SET resolution='resolved' WHERE purchase_order_id=? AND resolution='pending'",(po_id,))
        c.execute("UPDATE purchase_orders SET status=?,received_at=CASE WHEN ?='received' THEN CURRENT_TIMESTAMP ELSE received_at END,updated_at=CURRENT_TIMESTAMP WHERE id=?",(new_status,new_status,po_id))
        return {'ok':True,'receiptId':receipt_id,'purchaseOrderId':po_id,'status':new_status,'receivedCost':round(total_cost,2),'supplierPayable':payable,'productIds':sorted(set(changed)),'purchaseOrder':self.get_purchase_order(c,po_id)}

    # ── V1.1 拒收/异常登记（任务书 4.2③）─────────────────────────────────────
    # 登记即扣减未到货：outstanding = ordered - received - rejected。
    # 到货时发现短少/破损/错发，登记后采购单才能在「永远不会到的量」上正常收完，
    # 而不是永远卡在 partially_received。已收货的数量不能再登记异常（那要走采购退货）。
    EXCEPTION_TYPES={'shortage':'短少','damaged':'破损','wrong_item':'错发','rejected':'拒收','other':'其他'}

    def register_po_exception(self,c,*,po_id:str,item_id:int,exception_type:str,quantity:int,
                              note:str='',registered_by:str='platform',variant_id:int|None=None,resolution:str='closed') -> dict[str,Any]:
        po=self.get_purchase_order(c,po_id)
        if po['status'] not in ('ordered','partially_received','received'):
            raise ValueError(f"当前采购单状态不能登记异常: {po['status']}")
        et=str(exception_type or '').strip()
        if et not in self.EXCEPTION_TYPES: raise ValueError(f"异常类型无效: {et or '(空)'}")
        line=next((x for x in po['items'] if int(x['id'])==int(item_id)),None)
        if not line: raise LookupError(f'采购单明细不存在: {item_id}')
        v=sc.sku(c,int(line['product_id']),variant_id or line.get('variant_id'))
        if resolution not in ('pending','closed'):raise ValueError('异常需明确待补发或确认关闭余量')
        plan=next((z for z in line.get('skuItems',[]) if z['variantId']==v['id']),None)
        qty=int(quantity)
        if plan and qty>plan['quantity']-plan['received']-plan['closed']:raise ValueError('异常数量超过该 SKU 未到货量')
        if qty<=0: raise ValueError('异常数量必须大于 0')
        if qty>int(line['quantity_outstanding']):
            raise ValueError(f"异常数量 {qty} 超过未到货数量 {int(line['quantity_outstanding'])}（已到货部分请走采购退货）")
        c.execute('''INSERT INTO purchase_order_exceptions(purchase_order_id,purchase_order_item_id,product_id,exception_type,quantity,note,registered_by,variant_id,resolution)
                     VALUES(?,?,?,?,?,?,?,?,?)''',(po_id,int(item_id),int(line['product_id']),et,qty,str(note or ''),registered_by,v['id'],resolution))
        # 在途联动：登记异常的那部分货永远不会到，必须同时从「在途」里退出，
        # 否则拒收之后 on_order 会永远残留（下单 10 → 拒收 2 → 收 8，在途还挂着 2）。
        if resolution=='closed':self._on_order_delta(c,product_id=int(line['product_id']),delta=-qty)
        if self.get_purchase_order(c,po_id)['outstandingQty']==0:
            c.execute("UPDATE purchase_orders SET status='received',received_at=CURRENT_TIMESTAMP WHERE id=?",(po_id,))
            c.execute("UPDATE purchase_order_exceptions SET resolution='resolved' WHERE purchase_order_id=? AND resolution='pending'",(po_id,))
        return self.get_purchase_order(c,po_id)

    def list_po_exceptions(self,c,*,po_id:str) -> list[dict[str,Any]]:
        return [dict(x) for x in c.execute('''SELECT e.*,p.name product_name,p.sku product_sku FROM purchase_order_exceptions e
                 JOIN products p ON p.id=e.product_id WHERE e.purchase_order_id=? ORDER BY e.id DESC''',(po_id,)).fetchall()]

    def _return_id(self)->str:
        return 'prt_'+datetime.utcnow().strftime('%Y%m%d')+'_'+uuid.uuid4().hex[:8]

    def create_purchase_return(self,c,*,po_id:str,items:list[dict[str,Any]],note:str='',created_by:str='platform')->dict[str,Any]:
        po=self.get_purchase_order(c,po_id)
        if int(po.get('receivedQty') or 0)<=0: raise ValueError('采购单尚未到货，不能创建采购退货')
        if not items: raise ValueError('采购退货至少包含一个商品')
        po_items={int(x['id']):x for x in po['items']}; normalized=[]; total=0.0; seen=set()
        for raw in items:
            iid=int(raw.get('purchaseOrderItemId') or 0); qty=int(raw.get('quantity') or 0)
            if iid<=0 or qty<=0: raise ValueError('采购退货明细无效')
            if iid not in po_items: raise ValueError(f'采购单明细不存在: {iid}')
            line=po_items[iid]
            plan=json.loads(line.get('sku_plan_json') or '[]')
            vid=raw.get('variantId') or (plan[0]['variantId'] if len(plan)==1 else line.get('variant_id'))
            v=sc.sku(c,int(line['product_id']),vid)
            if (iid,v['id']) in seen:raise ValueError('同一 SKU 退货行重复')
            seen.add((iid,v['id']))
            already=int(c.execute("""SELECT COALESCE(SUM(pri.quantity),0) FROM purchase_return_items pri JOIN purchase_returns pr ON pr.id=pri.purchase_return_id
                WHERE pri.purchase_order_item_id=? AND (pri.variant_id=? OR pri.variant_id IS NULL) AND pr.status IN ('approved','shipped','credited')""",(iid,v['id'])).fetchone()[0] or 0)
            sku_received=int(c.execute('SELECT COALESCE(SUM(quantity),0) FROM purchase_receipt_items WHERE purchase_order_item_id=? AND (variant_id=? OR variant_id IS NULL)',(iid,v['id'])).fetchone()[0])
            maxq=sku_received-already
            if qty>maxq: raise ValueError(f"{line['product_name']} 最多可退 {maxq}")
            ordered_cost=next((z['unitCost'] for z in plan if z['variantId']==v['id']),line['unit_cost'])
            cost=float(raw.get('unitCost') if raw.get('unitCost') not in (None,'') else ordered_cost or 0); line_total=round(cost*qty,2); total+=line_total
            normalized.append((iid,int(line['product_id']),qty,cost,line_total,v['id']))
        rid=self._return_id()
        c.execute("INSERT INTO purchase_returns(id,supplier_id,purchase_order_id,status,total_amount,note,created_by) VALUES(?,?,?,'draft',?,?,?)",(rid,po['supplier_id'],po_id,round(total,2),note,created_by))
        for iid,pid,qty,cost,line_total,vid in normalized:
            c.execute('INSERT INTO purchase_return_items(purchase_return_id,purchase_order_item_id,product_id,quantity,unit_cost,line_total,variant_id) VALUES(?,?,?,?,?,?,?)',(rid,iid,pid,qty,cost,line_total,vid))
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
        for it in r['items']:
            vid=it.get('variant_id');iid=it['purchase_order_item_id']
            received=int(c.execute('SELECT COALESCE(SUM(quantity),0) FROM purchase_receipt_items WHERE purchase_order_item_id=? AND (variant_id=? OR variant_id IS NULL)',(iid,vid)).fetchone()[0])
            approved=int(c.execute("SELECT COALESCE(SUM(i.quantity),0) FROM purchase_return_items i JOIN purchase_returns r ON r.id=i.purchase_return_id WHERE i.purchase_order_item_id=? AND (i.variant_id=? OR i.variant_id IS NULL) AND r.id!=? AND r.status IN ('approved','shipped','credited')",(iid,vid,return_id)).fetchone()[0])
            if approved+int(it['quantity'])>received:raise ValueError('其他采购退货已占用该 SKU 可退数量，请重新核对')
        c.execute("UPDATE purchase_returns SET status='approved',approved_by=?,approved_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",(approved_by,return_id)); return self.get_purchase_return(c,return_id)

    def ship_purchase_return(self,c,*,return_id:str,actor_type:str='platform')->dict[str,Any]:
        r=self.get_purchase_return(c,return_id)
        if r['status'] in ('shipped','credited'): return {**r,'idempotent':True,'productIds':[]}
        if r['status']!='approved': raise ValueError(f"当前采购退货状态不能出库: {r['status']}")
        changed=[]
        for it in r['items']:
            pid=int(it['product_id']); q=int(it['quantity']); p=dict(c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone())
            # before 必须和 after 落在同一级：都取默认规格的库存。读 products.stock（各规格之和）
            # 却写回单个规格，多规格商品会凭空多出/少掉库存，且汇总重算后对不上。
            v=sc.sku(c,pid,it.get('variant_id'))
            before=int(v['stock'] or 0) if v else int(p['stock'] or 0)
            if before<q: raise ValueError(f"{p['name']} 可售库存不足，不能退回供应商")
            if self.warehouse: self.warehouse.outbound_available(c,product_id=pid,variant_id=v['id'],quantity=q,reference_type='purchase_return',reference_id=return_id,note=f'采购退货 {return_id} 出库',actor_type=actor_type,idempotency_key=f'wms-purchase-return:{return_id}:{pid}')
            after=before-q
            if v: write_variant_stock(c,product_id=pid,variant_id=int(v['id']),new_stock=after,
                                      sync_status='pending' if c.execute('SELECT commerce_inventory_item_id FROM products WHERE id=?',(pid,)).fetchone()['commerce_inventory_item_id'] else None)
            else: c.execute("UPDATE products SET stock=?,commerce_sync_status=CASE WHEN commerce_inventory_item_id IS NOT NULL THEN 'pending' ELSE commerce_sync_status END WHERE id=?",(after,pid))
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
                       FROM products WHERE inventory_mode='platform_owned' AND source!='external_api'
                       ''').fetchone()
        open_po=c.execute("SELECT COUNT(*),COALESCE(SUM(total_amount),0) FROM purchase_orders WHERE status IN ('approved','ordered','partially_received')").fetchone()
        return {'skuCount':int(r['sku_count'] or 0),'units':int(r['units'] or 0),'inventoryValue':round(float(r['inventory_value'] or 0),2),
                'lowStockCount':int(r['low_stock_count'] or 0),'openPurchaseOrders':int(open_po[0] or 0),'openPurchaseValue':round(float(open_po[1] or 0),2)}
