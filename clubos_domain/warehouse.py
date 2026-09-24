from __future__ import annotations

import uuid
from typing import Any


class WarehouseEngine:
    """Lightweight WMS owned by ClubOS Platform.

    products.stock remains aggregate sellable stock. warehouse_inventory stores physical
    on-hand and reservations. A paid Gear order reserves physical stock; shipment turns
    the reservation into a physical outbound. Procurement / returns / manual counts are
    physical facts and are never rolled back because a commerce projection fails.
    """

    def ensure_defaults(self, c) -> dict[str, Any]:
        wh=c.execute("SELECT * FROM warehouses WHERE is_default=1 AND status='active' ORDER BY id LIMIT 1").fetchone()
        if not wh:
            c.execute("INSERT INTO warehouses(code,name,address,status,is_default) VALUES('MAIN','平台中心仓','', 'active',1)")
            wh=c.execute('SELECT * FROM warehouses WHERE id=last_insert_rowid()').fetchone()
        loc=c.execute("SELECT * FROM warehouse_locations WHERE warehouse_id=? AND is_default=1 AND status='active' ORDER BY id LIMIT 1",(wh['id'],)).fetchone()
        if not loc:
            c.execute("INSERT INTO warehouse_locations(warehouse_id,code,name,zone_type,status,is_default) VALUES(?,?,?,?,?,1)",(wh['id'],'A-DEFAULT','默认可售库位','sellable','active'))
            loc=c.execute('SELECT * FROM warehouse_locations WHERE id=last_insert_rowid()').fetchone()
        ret=c.execute("SELECT * FROM warehouse_locations WHERE warehouse_id=? AND zone_type='returns' AND status='active' ORDER BY id LIMIT 1",(wh['id'],)).fetchone()
        if not ret:
            c.execute("INSERT INTO warehouse_locations(warehouse_id,code,name,zone_type,status,is_default) VALUES(?,?,?,?,?,0)",(wh['id'],'R-01','退货待检区','returns','active'))
            ret=c.execute('SELECT * FROM warehouse_locations WHERE id=last_insert_rowid()').fetchone()
        return {'warehouse':dict(wh),'defaultLocation':dict(loc),'returnLocation':dict(ret)}

    def backfill_opening(self,c):
        d=self.ensure_defaults(c); loc_id=int(d['defaultLocation']['id'])
        for p in c.execute('SELECT id,stock FROM products ORDER BY id').fetchall():
            if not c.execute('SELECT 1 FROM warehouse_inventory WHERE product_id=?',(p['id'],)).fetchone():
                q=max(0,int(p['stock'] or 0))
                c.execute('INSERT INTO warehouse_inventory(product_id,location_id,on_hand,reserved) VALUES(?,?,?,0)',(p['id'],loc_id,q))
                self._move(c,product_id=int(p['id']),movement_type='opening_balance',quantity=q,to_location_id=loc_id,
                           reference_type='migration',reference_id='v0.20',note='v0.20 承接历史可售库存为中心仓期初实物库存',
                           idempotency_key=f'v020-opening:{p["id"]}')

    def _move(self,c,*,product_id:int,movement_type:str,quantity:int,from_location_id:int|None=None,to_location_id:int|None=None,
              reference_type:str='',reference_id:str='',note:str='',actor_type:str='system',idempotency_key:str|None=None):
        key=idempotency_key or f'{movement_type}:{uuid.uuid4().hex}'
        old=c.execute('SELECT * FROM warehouse_movements WHERE idempotency_key=?',(key,)).fetchone()
        if old:return dict(old)
        c.execute('''INSERT INTO warehouse_movements(product_id,movement_type,quantity,from_location_id,to_location_id,reference_type,reference_id,note,actor_type,idempotency_key)
                     VALUES(?,?,?,?,?,?,?,?,?,?)''',(product_id,movement_type,int(quantity),from_location_id,to_location_id,reference_type,reference_id,note,actor_type,key))
        return dict(c.execute('SELECT * FROM warehouse_movements WHERE id=last_insert_rowid()').fetchone())

    def _row(self,c,product_id:int,location_id:int):
        r=c.execute('SELECT * FROM warehouse_inventory WHERE product_id=? AND location_id=?',(product_id,location_id)).fetchone()
        if not r:
            c.execute('INSERT INTO warehouse_inventory(product_id,location_id,on_hand,reserved) VALUES(?,?,0,0)',(product_id,location_id))
            r=c.execute('SELECT * FROM warehouse_inventory WHERE product_id=? AND location_id=?',(product_id,location_id)).fetchone()
        return dict(r)

    def _update(self,c,product_id:int,location_id:int,on_hand:int,reserved:int):
        if on_hand<0 or reserved<0 or reserved>on_hand: raise ValueError('仓库库存状态非法')
        c.execute('UPDATE warehouse_inventory SET on_hand=?,reserved=?,updated_at=CURRENT_TIMESTAMP WHERE product_id=? AND location_id=?',(on_hand,reserved,product_id,location_id))

    def inbound(self,c,*,product_id:int,quantity:int,reference_type:str,reference_id:str,note:str='',location_id:int|None=None,actor_type:str='platform',idempotency_key:str|None=None):
        q=max(1,int(quantity)); d=self.ensure_defaults(c); lid=int(location_id or d['defaultLocation']['id'])
        key=idempotency_key or f'wms-in:{reference_type}:{reference_id}:{product_id}'
        old=c.execute('SELECT * FROM warehouse_movements WHERE idempotency_key=?',(key,)).fetchone()
        if old:return {'idempotent':True,'movementId':old['id']}
        r=self._row(c,product_id,lid); self._update(c,product_id,lid,int(r['on_hand'])+q,int(r['reserved']))
        m=self._move(c,product_id=product_id,movement_type='inbound',quantity=q,to_location_id=lid,reference_type=reference_type,reference_id=reference_id,note=note,actor_type=actor_type,idempotency_key=key)
        return {'movementId':m['id'],'locationId':lid,'quantity':q}

    def outbound_available(self,c,*,product_id:int,quantity:int,reference_type:str,reference_id:str,note:str='',actor_type:str='platform',idempotency_key:str|None=None):
        q=max(1,int(quantity));key=idempotency_key or f'wms-out:{reference_type}:{reference_id}:{product_id}'
        old=c.execute('SELECT * FROM warehouse_movements WHERE idempotency_key=?',(key,)).fetchone()
        if old:return {'idempotent':True,'movementId':old['id'],'quantity':q}
        rows=c.execute("""SELECT wi.*,wl.zone_type,(wi.on_hand-wi.reserved) available FROM warehouse_inventory wi
                          JOIN warehouse_locations wl ON wl.id=wi.location_id
                          WHERE wi.product_id=? AND wl.status='active' AND (wi.on_hand-wi.reserved)>0
                          ORDER BY CASE WHEN wl.zone_type='sellable' THEN 0 ELSE 1 END,wl.is_default DESC,wi.location_id""",(product_id,)).fetchall()
        if sum(int(x['available']) for x in rows)<q:raise ValueError('仓库可用实物库存不足')
        need=q;first=None
        for x in rows:
            take=min(need,int(x['available']))
            if take<=0:continue
            self._update(c,product_id,int(x['location_id']),int(x['on_hand'])-take,int(x['reserved']))
            m=self._move(c,product_id=product_id,movement_type='supplier_return_outbound',quantity=take,from_location_id=int(x['location_id']),reference_type=reference_type,reference_id=reference_id,note=note,actor_type=actor_type,idempotency_key=key if first is None and take==q else f'{key}:{x["location_id"]}')
            if first is None:first=m
            need-=take
            if need<=0:break
        return {'movementId':first['id'] if first else None,'quantity':q}

    def reserve_order(self,c,*,order_id:int,product_id:int,quantity:int,actor_type:str='system'):
        q=max(1,int(quantity)); key=f'wms-reserve:order:{order_id}:product:{product_id}'
        old=c.execute('SELECT * FROM warehouse_reservations WHERE order_id=? AND product_id=?',(order_id,product_id)).fetchone()
        if old:return dict(old)
        need=q; allocations=[]
        rows=c.execute('''SELECT wi.*,wl.warehouse_id,(wi.on_hand-wi.reserved) available FROM warehouse_inventory wi
                          JOIN warehouse_locations wl ON wl.id=wi.location_id
                          WHERE wi.product_id=? AND wl.status='active' AND wl.zone_type='sellable' AND (wi.on_hand-wi.reserved)>0
                          ORDER BY wl.is_default DESC,wi.location_id''',(product_id,)).fetchall()
        if sum(int(x['available']) for x in rows)<q: raise ValueError('仓库可用库存不足')
        for x in rows:
            take=min(need,int(x['available']))
            if take<=0: continue
            self._update(c,product_id,int(x['location_id']),int(x['on_hand']),int(x['reserved'])+take)
            allocations.append({'locationId':int(x['location_id']),'quantity':take}); need-=take
            if need==0:break
        c.execute('''INSERT INTO warehouse_reservations(order_id,product_id,quantity,status,allocations_json)
                     VALUES(?,?,?,?,json(?))''',(order_id,product_id,q,'reserved',__import__('json').dumps(allocations,ensure_ascii=False)))
        self._move(c,product_id=product_id,movement_type='reserve',quantity=q,reference_type='gear_order',reference_id=str(order_id),note=f'装备订单 #{order_id} 锁定仓库库存',actor_type=actor_type,idempotency_key=key)
        return dict(c.execute('SELECT * FROM warehouse_reservations WHERE order_id=? AND product_id=?',(order_id,product_id)).fetchone())

    def release_order(self,c,*,order_id:int,product_id:int,quantity:int|None=None,actor_type:str='system'):
        import json
        r=c.execute("SELECT * FROM warehouse_reservations WHERE order_id=? AND product_id=? AND status='reserved'",(order_id,product_id)).fetchone()
        if not r:return {'released':0,'idempotent':True}
        alloc=json.loads(r['allocations_json'] or '[]'); remain=int(quantity or r['quantity']); released=0
        for a in alloc:
            if remain<=0:break
            take=min(remain,int(a['quantity'])); wi=self._row(c,product_id,int(a['locationId']))
            self._update(c,product_id,int(a['locationId']),int(wi['on_hand']),int(wi['reserved'])-take)
            a['quantity']=int(a['quantity'])-take; remain-=take; released+=take
        left=int(r['quantity'])-released; status='released' if left<=0 else 'reserved'
        c.execute('UPDATE warehouse_reservations SET quantity=?,status=?,allocations_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(max(0,left),status,json.dumps([a for a in alloc if a['quantity']>0]),r['id']))
        self._move(c,product_id=product_id,movement_type='release',quantity=released,reference_type='gear_order',reference_id=str(order_id),note=f'装备订单 #{order_id} 释放锁定库存',actor_type=actor_type,idempotency_key=f'wms-release:{order_id}:{product_id}:{r["id"]}:{released}')
        return {'released':released,'remaining':max(0,left)}

    def validate_dispatch(self,c,order_id:int):
        items=c.execute('SELECT product_id,quantity FROM gear_order_items WHERE order_id=?',(order_id,)).fetchall()
        if not items: raise ValueError('订单没有商品明细')
        for it in items:
            r=c.execute("SELECT quantity FROM warehouse_reservations WHERE order_id=? AND product_id=? AND status='reserved'",(order_id,it['product_id'])).fetchone()
            if not r or int(r['quantity'])<int(it['quantity']): raise ValueError(f"商品 #{it['product_id']} 未完成仓库锁库")
        return True

    def dispatch_order(self,c,*,order_id:int,actor_type:str='platform'):
        import json
        old=c.execute("SELECT 1 FROM warehouse_movements WHERE reference_type='gear_order_dispatch' AND reference_id=? LIMIT 1",(str(order_id),)).fetchone()
        if old:return {'ok':True,'idempotent':True}
        self.validate_dispatch(c,order_id)
        for r0 in c.execute("SELECT * FROM warehouse_reservations WHERE order_id=? AND status='reserved'",(order_id,)).fetchall():
            r=dict(r0); alloc=json.loads(r['allocations_json'] or '[]')
            for a in alloc:
                q=int(a['quantity']); wi=self._row(c,int(r['product_id']),int(a['locationId']))
                self._update(c,int(r['product_id']),int(a['locationId']),int(wi['on_hand'])-q,int(wi['reserved'])-q)
                self._move(c,product_id=int(r['product_id']),movement_type='dispatch',quantity=q,from_location_id=int(a['locationId']),reference_type='gear_order_dispatch',reference_id=str(order_id),note=f'装备订单 #{order_id} 发货出库',actor_type=actor_type,idempotency_key=f'wms-dispatch:{order_id}:{r["product_id"]}:{a["locationId"]}')
            c.execute("UPDATE warehouse_reservations SET status='dispatched',updated_at=CURRENT_TIMESTAMP WHERE id=?",(r['id'],))
        c.execute("UPDATE warehouse_tasks SET status='shipped',updated_at=CURRENT_TIMESTAMP WHERE order_id=? AND task_type IN ('pick','pack')",(order_id,))
        return {'ok':True,'orderId':order_id}

    def create_pick_task(self,c,*,order_id:int,warehouse_id:int|None=None):
        d=self.ensure_defaults(c); wid=int(warehouse_id or d['warehouse']['id'])
        old=c.execute("SELECT * FROM warehouse_tasks WHERE order_id=? AND task_type='pick'",(order_id,)).fetchone()
        if old:return dict(old)
        self.validate_dispatch(c,order_id)
        tid='pick_'+uuid.uuid4().hex[:10]
        c.execute("INSERT INTO warehouse_tasks(id,order_id,warehouse_id,task_type,status) VALUES(?,?,?,?,?)",(tid,order_id,wid,'pick','pending'))
        return dict(c.execute('SELECT * FROM warehouse_tasks WHERE id=?',(tid,)).fetchone())

    def mark_picked(self,c,task_id:str):
        r=c.execute("SELECT * FROM warehouse_tasks WHERE id=? AND task_type='pick'",(task_id,)).fetchone()
        if not r:raise LookupError('拣货任务不存在')
        if r['status'] in ('picked','packed','shipped'):return dict(r)
        c.execute("UPDATE warehouse_tasks SET status='picked',picked_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",(task_id,))
        return dict(c.execute('SELECT * FROM warehouse_tasks WHERE id=?',(task_id,)).fetchone())

    def pack_order(self,c,*,order_id:int,note:str=''):
        pick=c.execute("SELECT * FROM warehouse_tasks WHERE order_id=? AND task_type='pick'",(order_id,)).fetchone()
        if not pick: pick=self.create_pick_task(c,order_id=order_id)
        if pick['status']=='pending': self.mark_picked(c,pick['id'])
        old=c.execute("SELECT * FROM warehouse_tasks WHERE order_id=? AND task_type='pack'",(order_id,)).fetchone()
        if old:return dict(old)
        tid='pack_'+uuid.uuid4().hex[:10]
        c.execute("INSERT INTO warehouse_tasks(id,order_id,warehouse_id,task_type,status,note,packed_at) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)",(tid,order_id,pick['warehouse_id'],'pack','packed',note))
        return dict(c.execute('SELECT * FROM warehouse_tasks WHERE id=?',(tid,)).fetchone())

    def move_stock(self,c,*,product_id:int,from_location_id:int,to_location_id:int,quantity:int,note:str=''):
        q=max(1,int(quantity)); src=self._row(c,product_id,from_location_id); dst=self._row(c,product_id,to_location_id)
        available=int(src['on_hand'])-int(src['reserved'])
        if available<q:raise ValueError('源库位可移动库存不足')
        self._update(c,product_id,from_location_id,int(src['on_hand'])-q,int(src['reserved']))
        self._update(c,product_id,to_location_id,int(dst['on_hand'])+q,int(dst['reserved']))
        m=self._move(c,product_id=product_id,movement_type='transfer',quantity=q,from_location_id=from_location_id,to_location_id=to_location_id,reference_type='location_transfer',reference_id=str(uuid.uuid4()),note=note,actor_type='platform')
        return {'ok':True,'movementId':m['id']}

    def count_location(self,c,*,product_id:int,location_id:int,counted_on_hand:int,note:str=''):
        counted=max(0,int(counted_on_hand)); r=self._row(c,product_id,location_id); old=int(r['on_hand']); reserved=int(r['reserved'])
        if counted<reserved:raise ValueError('盘点实物数不能小于已锁定库存')
        delta=counted-old
        self._update(c,product_id,location_id,counted,reserved)
        if delta:
            p=c.execute('SELECT stock FROM products WHERE id=?',(product_id,)).fetchone(); before=int(p['stock'] or 0); after=before+delta
            if after<0:raise ValueError('盘点调整后可售库存不能为负数')
            c.execute('UPDATE products SET stock=? WHERE id=?',(after,product_id))
        m=self._move(c,product_id=product_id,movement_type='cycle_count',quantity=abs(delta),to_location_id=location_id if delta>=0 else None,from_location_id=location_id if delta<0 else None,reference_type='cycle_count',reference_id=str(uuid.uuid4()),note=note or f'库位盘点差异 {delta:+d}',actor_type='platform') if delta else None
        return {'ok':True,'productId':product_id,'locationId':location_id,'previousOnHand':old,'countedOnHand':counted,'delta':delta,'movementId':m['id'] if m else None}

    def summary(self,c):
        self.ensure_defaults(c)
        r=c.execute('''SELECT COALESCE(SUM(on_hand),0) on_hand,COALESCE(SUM(reserved),0) reserved,
                       COALESCE(SUM(on_hand-reserved),0) available,COUNT(DISTINCT product_id) sku_count FROM warehouse_inventory''').fetchone()
        return {'onHand':int(r['on_hand'] or 0),'reserved':int(r['reserved'] or 0),'available':int(r['available'] or 0),'skuCount':int(r['sku_count'] or 0),
                'warehouses':int(c.execute("SELECT COUNT(*) FROM warehouses WHERE status='active'").fetchone()[0]),
                'pendingPickTasks':int(c.execute("SELECT COUNT(*) FROM warehouse_tasks WHERE task_type='pick' AND status='pending'").fetchone()[0])}


    def create_warehouse(self,c,payload:dict[str,Any]):
        code=str(payload.get('code') or '').strip().upper(); name=str(payload.get('name') or '').strip()
        if not code or not name: raise ValueError('仓库 code / name 不能为空')
        is_default=1 if payload.get('isDefault') else 0
        if is_default:c.execute('UPDATE warehouses SET is_default=0')
        c.execute('INSERT INTO warehouses(code,name,address,status,is_default) VALUES(?,?,?,?,?)',(code,name,str(payload.get('address') or ''),str(payload.get('status') or 'active'),is_default))
        wid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        return dict(c.execute('SELECT * FROM warehouses WHERE id=?',(wid,)).fetchone())

    def create_location(self,c,warehouse_id:int,payload:dict[str,Any]):
        if not c.execute('SELECT 1 FROM warehouses WHERE id=?',(warehouse_id,)).fetchone(): raise LookupError('仓库不存在')
        code=str(payload.get('code') or '').strip().upper(); name=str(payload.get('name') or '').strip()
        if not code or not name: raise ValueError('库位 code / name 不能为空')
        zone=str(payload.get('zoneType') or 'sellable'); is_default=1 if payload.get('isDefault') else 0
        if is_default:c.execute('UPDATE warehouse_locations SET is_default=0 WHERE warehouse_id=?',(warehouse_id,))
        c.execute('INSERT INTO warehouse_locations(warehouse_id,code,name,zone_type,status,is_default) VALUES(?,?,?,?,?,?)',(warehouse_id,code,name,zone,str(payload.get('status') or 'active'),is_default))
        lid=int(c.execute('SELECT last_insert_rowid()').fetchone()[0])
        return dict(c.execute('SELECT * FROM warehouse_locations WHERE id=?',(lid,)).fetchone())

    def list_warehouses(self,c):
        out=[]
        for w in c.execute('SELECT * FROM warehouses ORDER BY is_default DESC,id').fetchall():
            d=dict(w); d['locations']=[dict(x) for x in c.execute('''SELECT wl.*,COALESCE(SUM(wi.on_hand),0) on_hand,COALESCE(SUM(wi.reserved),0) reserved
                     FROM warehouse_locations wl LEFT JOIN warehouse_inventory wi ON wi.location_id=wl.id
                     WHERE wl.warehouse_id=? GROUP BY wl.id ORDER BY wl.is_default DESC,wl.id''',(w['id'],)).fetchall()];out.append(d)
        return out

    def inventory(self,c,*,warehouse_id:int|None=None,product_id:int|None=None):
        sql='''SELECT wi.*,p.name product_name,p.sku product_sku,wl.code location_code,wl.name location_name,wl.zone_type,w.id warehouse_id,w.name warehouse_name,
                      (wi.on_hand-wi.reserved) available
               FROM warehouse_inventory wi JOIN products p ON p.id=wi.product_id JOIN warehouse_locations wl ON wl.id=wi.location_id JOIN warehouses w ON w.id=wl.warehouse_id WHERE 1=1''';args=[]
        if warehouse_id is not None:sql+=' AND w.id=?';args.append(warehouse_id)
        if product_id is not None:sql+=' AND p.id=?';args.append(product_id)
        sql+=' ORDER BY p.name,w.is_default DESC,wl.id'
        return [dict(x) for x in c.execute(sql,args).fetchall()]

    def tasks(self,c,*,status:str|None=None):
        sql='''SELECT t.*,w.name warehouse_name,o.status order_status,o.tracking_no FROM warehouse_tasks t JOIN warehouses w ON w.id=t.warehouse_id JOIN gear_orders o ON o.id=t.order_id WHERE 1=1''';args=[]
        if status:sql+=' AND t.status=?';args.append(status)
        sql+=' ORDER BY t.created_at DESC'
        return [dict(x) for x in c.execute(sql,args).fetchall()]
