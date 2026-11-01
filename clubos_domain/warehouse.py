from __future__ import annotations

import uuid
import json
from . import stock_control as sc
from typing import Any
from clubos_domain.product_stock import variant_of, write_variant_stock, resync_sellable_from_wms


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
        d=self.ensure_defaults(c);lid=int(d['defaultLocation']['id'])
        for p0 in c.execute('SELECT * FROM products ORDER BY id').fetchall():
            p=dict(p0)
            try:sc.owned(c,p['id'])
            except ValueError:continue
            if not c.execute('SELECT 1 FROM warehouse_inventory WHERE product_id=?',(p['id'],)).fetchone():
                c.execute('INSERT INTO warehouse_inventory(product_id,location_id,on_hand,reserved) VALUES(?,?,?,0)',(p['id'],lid,max(0,int(p['stock'] or 0))))
            sc.migrate_product(c,p['id'])

    def _move(self,c,*,product_id:int,movement_type:str,quantity:int,from_location_id:int|None=None,to_location_id:int|None=None,
              reference_type:str='',reference_id:str='',note:str='',actor_type:str='system',idempotency_key:str|None=None,variant_id:int|None=None):
        key=idempotency_key or f'{movement_type}:{uuid.uuid4().hex}'
        old=c.execute('SELECT * FROM warehouse_movements WHERE idempotency_key=?',(key,)).fetchone()
        if old:return dict(old)
        c.execute('''INSERT INTO warehouse_movements(product_id,movement_type,quantity,from_location_id,to_location_id,reference_type,reference_id,note,actor_type,idempotency_key,variant_id)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(product_id,movement_type,int(quantity),from_location_id,to_location_id,reference_type,reference_id,note,actor_type,key,variant_id))
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

    def inbound(self,c,*,product_id:int,quantity:int,reference_type:str,reference_id:str,note:str='',location_id:int|None=None,actor_type:str='platform',idempotency_key:str|None=None,variant_id:int|None=None):
        v=sc.sku(c,product_id,variant_id);q=int(quantity)
        if q<=0:raise ValueError('入库数量必须大于 0')
        lid=int(location_id or self.ensure_defaults(c)['defaultLocation']['id'])
        key=idempotency_key or f'wms-in:{reference_type}:{reference_id}:{v["id"]}'
        old=c.execute('SELECT id FROM warehouse_movements WHERE idempotency_key=?',(key,)).fetchone()
        if old:return {'idempotent':True,'movementId':old['id']}
        r=sc.balance(c,product_id,v['id'],lid);sc.update(c,r,r['on_hand']+q,r['reserved'])
        m=self._move(c,product_id=product_id,movement_type='inbound',quantity=q,to_location_id=lid,reference_type=reference_type,reference_id=reference_id,note=note,actor_type=actor_type,idempotency_key=key,variant_id=v['id'])
        sc.sync(c,product_id)
        return {'movementId':m['id'],'locationId':lid,'quantity':q}

    def outbound_available(self,c,*,product_id:int,quantity:int,reference_type:str,reference_id:str,note:str='',actor_type:str='platform',idempotency_key:str|None=None,variant_id:int|None=None):
        v=sc.sku(c,product_id,variant_id);q=int(quantity);key=idempotency_key or f'out:{reference_type}:{reference_id}:{v["id"]}'
        old=c.execute('SELECT id FROM warehouse_movements WHERE idempotency_key=?',(key,)).fetchone()
        if old:return {'idempotent':True,'movementId':old['id']}
        rows=[dict(x) for x in c.execute('SELECT * FROM warehouse_sku_balances WHERE variant_id=? ORDER BY location_id',(v['id'],))]
        if q<=0 or sum(x['on_hand']-x['reserved']-x['unsellable'] for x in rows)<q:raise ValueError('SKU 可出库实物不足')
        need=q;first=None
        for r in rows:
            take=min(need,r['on_hand']-r['reserved']-r['unsellable'])
            if take:
                sc.update(c,r,r['on_hand']-take,r['reserved']);need-=take
                moved=self._move(c,product_id=product_id,variant_id=v['id'],movement_type='supplier_return_outbound',quantity=take,from_location_id=r['location_id'],reference_type=reference_type,reference_id=reference_id,note=note,actor_type=actor_type,idempotency_key=key if first is None else f'{key}:{r["location_id"]}')
                if first is None:first=moved
            if not need:break
        m=first
        sc.sync(c,product_id);return {'quantity':q,'movementId':m['id']}

    def reserve_order(self,c,*,order_id:int,product_id:int,quantity:int,actor_type:str='system',variant_id:int|None=None):
        v=sc.sku(c,product_id,variant_id)
        r=sc.reserve(c,product_id,v['id'],int(quantity),order_id=order_id)
        self._move(c,product_id=product_id,variant_id=v['id'],movement_type='reserve',quantity=quantity,reference_type='gear_order',reference_id=str(order_id),actor_type=actor_type,idempotency_key=f'reserve:{order_id}:{v["id"]}')
        return r

    def release_order(self,c,*,order_id:int,product_id:int,quantity:int|None=None,actor_type:str='system',variant_id:int|None=None):
        v=sc.sku(c,product_id,variant_id)
        r=c.execute("SELECT * FROM warehouse_sku_reservations WHERE order_id=? AND variant_id=? AND status='reserved'",(order_id,v['id'])).fetchone()
        return {'released':sc.release(c,dict(r),quantity) if r else 0}

    def validate_dispatch(self,c,order_id:int):
        order=c.execute('SELECT * FROM gear_orders WHERE id=?',(order_id,)).fetchone()
        if not order or order['status'] not in ('paid','processing','confirmed'):raise ValueError('只有已付款待发货订单可出库')
        items=c.execute('SELECT product_id,variant_id,SUM(quantity) q FROM gear_order_items WHERE order_id=? GROUP BY product_id,variant_id',(order_id,)).fetchall()
        if not items:raise ValueError('订单没有商品明细')
        for it in items:
            v=sc.sku(c,it['product_id'],it['variant_id'])
            r=c.execute("SELECT quantity FROM warehouse_sku_reservations WHERE order_id=? AND variant_id=? AND status='reserved'",(order_id,v['id'])).fetchone()
            if not r or r['quantity']!=it['q']:raise ValueError('订单 SKU 未完整锁库，请核对历史订单')
        return True

    def dispatch_order(self,c,*,order_id:int,actor_type:str='platform'):
        old=c.execute("SELECT 1 FROM warehouse_movements WHERE reference_type='gear_order_dispatch' AND reference_id=?",(str(order_id),)).fetchone()
        if old:return {'ok':True,'idempotent':True}
        self.validate_dispatch(c,order_id)
        for r0 in c.execute("SELECT * FROM warehouse_sku_reservations WHERE order_id=? AND status='reserved'",(order_id,)).fetchall():
            r=dict(r0)
            for a in json.loads(r['allocations_json']):
                b=sc.balance(c,r['product_id'],r['variant_id'],a['locationId']);q=a['quantity']
                sc.update(c,b,b['on_hand']-q,b['reserved']-q)
                self._move(c,product_id=r['product_id'],variant_id=r['variant_id'],movement_type='dispatch',quantity=q,from_location_id=a['locationId'],reference_type='gear_order_dispatch',reference_id=str(order_id),actor_type=actor_type,idempotency_key=f'dispatch:{order_id}:{r["variant_id"]}:{a["locationId"]}')
            c.execute("UPDATE warehouse_sku_reservations SET status='dispatched' WHERE id=?",(r['id'],));sc.sync(c,r['product_id'])
        sc.mirror_reservations(c,order_id)
        c.execute("UPDATE warehouse_tasks SET status='shipped' WHERE order_id=?",(order_id,))
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
        if pick['status']=='pending': raise ValueError('请先完成拣货核对，再确认打包')
        old=c.execute("SELECT * FROM warehouse_tasks WHERE order_id=? AND task_type='pack'",(order_id,)).fetchone()
        if old:return dict(old)
        tid='pack_'+uuid.uuid4().hex[:10]
        c.execute("INSERT INTO warehouse_tasks(id,order_id,warehouse_id,task_type,status,note,packed_at) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)",(tid,order_id,pick['warehouse_id'],'pack','packed',note))
        return dict(c.execute('SELECT * FROM warehouse_tasks WHERE id=?',(tid,)).fetchone())

    def move_stock(self,c,*,product_id:int,from_location_id:int,to_location_id:int,quantity:int,note:str='',variant_id:int|None=None):
        v=sc.sku(c,product_id,variant_id);q=int(quantity)
        a=sc.balance(c,product_id,v['id'],from_location_id);b=sc.balance(c,product_id,v['id'],to_location_id)
        if from_location_id==to_location_id or q<=0 or a['on_hand']-a['reserved']-a['unsellable']<q:raise ValueError('移库数量/库位无效或可移动库存不足')
        sc.update(c,a,a['on_hand']-q,a['reserved']);sc.update(c,b,b['on_hand']+q,b['reserved']);sc.sync(c,product_id)
        m=self._move(c,product_id=product_id,variant_id=v['id'],movement_type='transfer',quantity=q,from_location_id=from_location_id,to_location_id=to_location_id,note=note)
        return {'ok':True,'movementId':m['id']}

    def count_location(self,c,*,product_id:int,location_id:int,counted_on_hand:int,note:str='',variant_id:int|None=None):
        v=sc.sku(c,product_id,variant_id);r=sc.balance(c,product_id,v['id'],location_id);n=int(counted_on_hand)
        sc.update(c,r,n,r['reserved']);sc.sync(c,product_id)
        m=self._move(c,product_id=product_id,variant_id=v['id'],movement_type='cycle_count',quantity=abs(n-r['on_hand']),to_location_id=location_id,note=note)
        return {'ok':True,'delta':n-r['on_hand'],'movementId':m['id']}

    def summary(self,c):
        self.ensure_defaults(c)
        r=c.execute('''SELECT COALESCE(SUM(on_hand),0) on_hand,COALESCE(SUM(reserved),0) reserved,
                       COALESCE(SUM(CASE WHEN location_id IN (SELECT l.id FROM warehouse_locations l JOIN warehouses w ON w.id=l.warehouse_id WHERE l.zone_type='sellable' AND l.status='active' AND w.status='active') THEN on_hand-reserved-unsellable ELSE 0 END),0) available,COUNT(DISTINCT product_id) sku_count FROM warehouse_inventory''').fetchone()
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
        sql="""SELECT b.*,p.name product_name,p.sku product_code,v.sku product_sku,v.name variant_name,v.color,v.size,
          l.code location_code,l.name location_name,l.zone_type,w.id warehouse_id,w.name warehouse_name,
          CASE WHEN l.zone_type='sellable' AND l.status='active' AND w.status='active' THEN b.on_hand-b.reserved-b.unsellable ELSE 0 END available
          FROM warehouse_sku_balances b JOIN products p ON p.id=b.product_id JOIN product_variants v ON v.id=b.variant_id
          JOIN warehouse_locations l ON l.id=b.location_id JOIN warehouses w ON w.id=l.warehouse_id WHERE 1=1""";args=[]
        if warehouse_id is not None:sql+=' AND w.id=?';args.append(warehouse_id)
        if product_id is not None:sql+=' AND p.id=?';args.append(product_id)
        return [dict(x) for x in c.execute(sql+' ORDER BY p.name,v.id,l.id',args)]

    def tasks(self,c,*,status:str|None=None):
        sql='''SELECT t.*,w.name warehouse_name,o.status order_status,o.tracking_no FROM warehouse_tasks t JOIN warehouses w ON w.id=t.warehouse_id JOIN gear_orders o ON o.id=t.order_id WHERE 1=1''';args=[]
        if status:sql+=' AND t.status=?';args.append(status)
        sql+=' ORDER BY t.created_at DESC'
        return [dict(x) for x in c.execute(sql,args).fetchall()]
