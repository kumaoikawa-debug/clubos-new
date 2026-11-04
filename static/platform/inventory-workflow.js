/* SKU procurement, warehouse tasks and reconciliation reuse the existing admin UI. */
(function(){
'use strict';
if(!location.pathname.startsWith('/platform'))return;
const post=(url,data={})=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
const refresh=()=>Promise.all([loadSupplyChain(),loadProducts()]);
const requestKey=()=>crypto.randomUUID();
const active=v=>String(v.status||'active')==='active';
const label=(p,v)=>`${p.name} · ${v.color||''} ${v.size||v.name||''} · ${v.sku||''}`;
async function skuOptions(){
 const ps=(await api('/api/platform/products')).filter(p=>p.inventory_mode==='platform_owned'&&p.source!=='external_api');
 return (await Promise.all(ps.map(async p=>(await api(`/api/platform/products/${p.id}/variants`)).filter(active).map(v=>({p,v}))))).flat();
}
window.createPO=async()=>uxTask(async()=>{
 const suppliers=await api('/api/platform/suppliers?status=active');if(!suppliers.length)return toast('请先建立供应商档案');
 let options=await skuOptions();
 const dlg=uxDialog({title:'采购下单',desc:'按 SKU 填写计划数量和成本；验收沿用这份计划，零售价和图片由运营上架时完善。',wide:true,body:`<label>供应商<select id="poSupplier">${suppliers.map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select></label><div style="overflow-x:auto"><table class="table"><thead><tr><th>商品 / 颜色 / 尺码 / SKU</th><th>计划数量</th><th>采购单价</th><th></th></tr></thead><tbody id="poSkuRows"></tbody></table></div><div class="actions"><button class="btn secondary" id="poAddRow">添加 SKU</button><button class="btn secondary" id="poQuickProduct">快速建档并带入规格</button></div><label>期望到货时间<input id="poExpectedAt" type="datetime-local" placeholder="预计供应商送达日期"></label><label>采购备注<input id="poNote" value="平台采购"></label><div id="poError" role="alert"></div>`,foot:'<button class="btn ghost" data-cancel>取消</button><button class="btn" data-submit>保存采购草稿</button>'});
 dlg.classList.add('inventory-workflow');
 function add(vid,q=1,cost=''){
  const tr=document.createElement('tr');tr.innerHTML=`<td><select aria-label="采购 SKU">${options.map(x=>`<option value="${x.v.id}"${Number(vid)===x.v.id?' selected':''}>${esc(label(x.p,x.v))}</option>`).join('')}</select></td><td data-label="计划数量"><input aria-label="计划数量" type="number" min="1" step="1" value="${q}" style="width:90px"></td><td data-label="采购单价"><input aria-label="采购单价" type="number" min="0" step="0.01" value="${cost}" placeholder="使用供货价" style="width:110px"></td><td><button class="btn ghost" aria-label="移除此采购行">移除</button></td>`;tr.querySelector('button').onclick=()=>tr.remove();dlg.querySelector('#poSkuRows').appendChild(tr);
 }
 if(options.length)add();dlg.querySelector('#poAddRow').onclick=()=>options.length?add():toast('请先建商品规格');
 dlg.querySelector('#poQuickProduct').onclick=async()=>{const q=await quickCreateInventoryProduct(undefined,Number(dlg.querySelector('#poSupplier').value));if(!q)return;options=await skuOptions();for(const r of q.skuItems||[])add(r.variantId,r.quantity,q.cost??'');};
 dlg.querySelector('[data-cancel]').onclick=()=>dlg.uxClose();
 dlg.querySelector('[data-submit]').onclick=async e=>{
  const btn=e.currentTarget;btn.disabled=true;
  try{
   const items=[...dlg.querySelectorAll('#poSkuRows tr')].map(tr=>{const [qty,cost]=tr.querySelectorAll('input'),vid=Number(tr.querySelector('select').value),x=options.find(o=>o.v.id===vid);return {productId:x.p.id,variantId:vid,quantity:Number(qty.value),...(cost.value!==''?{unitCost:Number(cost.value)}:{})};});
   if(!items.length)throw Error('请至少添加一个 SKU');
   const exp=dlg.querySelector('#poExpectedAt').value;
   await post('/api/platform/procurement/purchase-orders',{supplierId:Number(dlg.querySelector('#poSupplier').value),items,note:dlg.querySelector('#poNote').value,expectedAt:exp||null});dlg.uxClose();toast('采购草稿已保存，待审核下单');refresh();
  }catch(err){dlg.querySelector('#poError').textContent=err.message;btn.disabled=false;}
 };
});
window.receivePO=async id=>uxTask(async()=>{
 const po=await api('/api/platform/procurement/purchase-orders/'+id),ws=await api('/api/platform/warehouses'),specs=[];
 for(const it of po.items||[]){
  const vs=(await api(`/api/platform/products/${it.product_id}/variants`)).filter(active),plan=it.skuItems?.length?it.skuItems:(vs.length===1?[{variantId:vs[0].id,quantity:it.quantity_ordered,received:it.quantity_received}]:[]);
  for(const z of plan){const v=vs.find(v=>v.id===z.variantId),remain=Math.min(z.quantity-z.received-Number(z.closed||0),it.quantity_outstanding);if(v&&remain>0)specs.push({it,v,remain});}
 }
 if(!specs.length)return toast('没有可直接验收的 SKU；历史无规格计划请先核对采购明细');
 const locations=ws.flatMap(w=>(w.locations||[]).filter(l=>l.status==='active').map(l=>({...l,warehouse:w.name}))),key=requestKey();
 const dlg=uxDialog({title:'仓库验收入库',desc:'核对供应商送货与 SKU 后填写实收数量；扫描条码每次计 1 件。待检库位不计可售。',wide:true,body:`<p>供应商发货凭证 ${(po.supplierShipments||[]).map(x=>esc(x.tracking_no)).join('、')||'尚未登记，可先核实送货单后验收'}</p><label>入库库位<select id="receiveLocation">${locations.map(l=>`<option value="${l.id}">${esc(l.warehouse+' / '+l.name)}</option>`).join('')}</select></label><div class="actions"><input id="receiveScan" aria-label="扫描条码或 SKU" placeholder="扫描条码或输入 SKU 后回车"><button class="btn secondary" id="receiveCamera">相机扫码</button></div><div style="overflow-x:auto"><table class="table"><thead><tr><th>SKU</th><th>待收</th><th>本次实收</th></tr></thead><tbody>${specs.map((x,i)=>`<tr><td>${esc(label({name:x.it.product_name},x.v))}</td><td data-label="待收数量">${x.remain}</td><td data-label="本次实收"><input data-index="${i}" aria-label="${esc(x.v.name)} 本次实收" type="number" min="0" max="${x.remain}" step="1" value="${x.remain}" style="width:90px"></td></tr>`).join('')}</tbody></table></div><div id="receiveError" role="alert"></div>`,foot:'<button class="btn ghost" data-cancel>取消</button><button class="btn" data-submit>确认实际入库</button>'});
 dlg.classList.add('inventory-workflow');
 const scan=raw=>{const i=specs.findIndex(x=>[x.v.sku,x.v.barcode].some(v=>v&&v.toLowerCase()===String(raw).trim().toLowerCase()));if(i<0)return toast('条码不属于本次待验收 SKU');const input=dlg.querySelector(`[data-index="${i}"]`);if(Number(input.value)>=specs[i].remain)return toast('该 SKU 已达到采购余量');input.value=Number(input.value)+1;input.focus();};
 dlg.querySelector('#receiveScan').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();scan(e.target.value);e.target.value='';}};
 dlg.querySelector('#receiveCamera').onclick=()=>inventoryBarcodeCamera(scan);
 dlg.querySelector('[data-cancel]').onclick=()=>dlg.uxClose();
 dlg.querySelector('[data-submit]').onclick=async e=>{const btn=e.currentTarget;btn.disabled=true;try{
  const items=[...dlg.querySelectorAll('[data-index]')].map(input=>{const x=specs[Number(input.dataset.index)];return {purchaseOrderItemId:x.it.id,variantId:x.v.id,quantity:Number(input.value)};}).filter(x=>x.quantity>0);
  if(!items.length)throw Error('请填写实际收到的数量');
  await post(`/api/platform/procurement/purchase-orders/${id}/receive`,{requestKey:key,locationId:Number(dlg.querySelector('#receiveLocation').value),items});dlg.uxClose();toast('验收已入账，运营可在待上架完善商品');refresh();
 }catch(err){dlg.querySelector('#receiveError').textContent=err.message;toast(err.message,'error');btn.disabled=false;}};
});
window.createPurchaseReturn=async poId=>uxTask(async()=>{
 const po=await api(`/api/platform/procurement/purchase-orders/${poId}`),options=[];
 for(const it of po.items){const vs=await api(`/api/platform/products/${it.product_id}/variants`);for(const z of it.skuItems?.length?it.skuItems:(vs.length===1?[{variantId:vs[0].id,received:it.quantity_received}]:[])){const v=vs.find(v=>v.id===z.variantId);if(v&&z.received>0)options.push({it,z,v});}}
 if(!options.length)return toast('没有可明确退回的已验收 SKU');
 const f=await uxForm({title:'采购退货草稿',subtitle:'按实际退回 SKU 填数量；后续仍独立审核、实物出库和确认供应商贷项。',fields:[...options.map((x,i)=>({name:'q'+i,label:label({name:x.it.product_name},x.v)+' · 已收 '+x.z.received,type:'number',min:0,step:1,value:0,required:true})),{name:'note',label:'退货原因',required:true,full:true}],submitText:'只建立退货草稿'});if(!f)return;
 const items=options.map((x,i)=>({purchaseOrderItemId:x.it.id,variantId:x.v.id,quantity:Number(f['q'+i])})).filter(x=>x.quantity>0);if(!items.length)return toast('请填写实际拟退数量');
 await post('/api/platform/procurement/purchase-returns',{purchaseOrderId:poId,items,note:f.note});toast('草稿已保存，尚未出库或冲账');loadSupplyChain();
});
window.registerPOException=async id=>uxTask(async()=>{
 const po=await api(`/api/platform/procurement/purchase-orders/${id}`),options=[];
 for(const it of po.items){const vs=await api(`/api/platform/products/${it.product_id}/variants`);for(const z of it.skuItems||[]){const v=vs.find(v=>v.id===z.variantId);if(v&&z.quantity-z.received-Number(z.closed||0)>0)options.push({it,z,v});}}
 if(!options.length)return toast('没有未到货 SKU；已入库异常请走采购退货');
 const f=await uxForm({title:'登记验收异常',subtitle:'待补发保留在途；仅在确认不再补发时关闭余量，不虚增实收。',fields:[{name:'sku',label:'异常 SKU',type:'select',required:true,full:true,options:options.map((x,i)=>({value:i,label:label({name:x.it.product_name},x.v)}))},{name:'exceptionType',label:'情况',type:'select',options:[{value:'shortage',label:'短少'},{value:'damaged',label:'破损'},{value:'wrong_item',label:'错发'},{value:'rejected',label:'拒收'}],required:true},{name:'quantity',label:'异常数量',type:'number',min:1,step:1,value:1,required:true},{name:'resolution',label:'余量处理',type:'select',required:true,options:[{value:'pending',label:'等待供应商补发，保留在途'},{value:'closed',label:'已确认不补发，关闭余量'}]},{name:'note',label:'验收依据与供应商处理意见',required:true,full:true}],submitText:'记录实际异常'});if(!f)return;const x=options[Number(f.sku)];await post(`/api/platform/procurement/purchase-orders/${id}/exceptions`,{...f,purchaseOrderItemId:x.it.id,variantId:x.v.id,quantity:Number(f.quantity)});toast('异常已登记');loadSupplyChain();
});
window.registerSupplierShipment=async id=>uxTask(async()=>{
 const po=await api(`/api/platform/procurement/purchase-orders/${id}`),plan=po.items.flatMap(it=>(it.skuItems||[]).map(z=>({...z,name:it.product_name})));
 if(!plan.length)return toast('历史采购缺少 SKU 计划，请先核对');
 const form=await uxForm({title:'登记供应商发货',subtitle:'记录供应商实际发出的货物，不增加平台仓实物。',fields:[{name:'carrier',label:'承运商 / 送货方',required:true},{name:'trackingNo',label:'实际运单 / 送货单号',required:true},...plan.map((z,i)=>({name:'q'+i,label:z.name+' · SKU #'+z.variantId+' 本次发出',type:'number',min:0,step:1,value:0}))],submitText:'保存实际发货凭证'});if(!form)return;
 await post(`/api/platform/procurement/purchase-orders/${id}/supplier-shipment`,{...form,requestKey:requestKey(),items:plan.map((z,i)=>({variantId:z.variantId,quantity:Number(form['q'+i])})).filter(z=>z.quantity>0)});toast('供应商发货已登记');loadSupplyChain();
});
const oldSupply=window.loadSupplyChain;
window.loadSupplyChain=async function(){await oldSupply();const ps=await api('/api/platform/procurement/purchase-orders');for(const p of ps.filter(x=>['ordered','partially_received'].includes(x.status))){const receive=[...document.querySelectorAll('#purchaseOrderList button')].find(b=>(b.getAttribute('onclick')||'').includes(`receivePO('${p.id}')`));if(receive&&!receive.parentNode.querySelector('[data-supplier-ship]')){const b=document.createElement('button');b.className='btn ghost';b.dataset.supplierShip='1';b.textContent='供应商发货';b.onclick=()=>registerSupplierShipment(p.id);receive.before(b);}}};
window.manualAdjust=async()=>uxTask(async()=>{const options=await skuOptions();if(!options.length)return toast('没有自营 SKU');const f=await uxForm({title:'自营 SKU 库存调整',subtitle:'仅用于有凭证的实盘差异；采购到货请走验收。',fields:[{name:'vid',label:'商品 / 颜色 / 尺码',type:'select',options:options.map(x=>({value:x.v.id,label:label(x.p,x.v)})),required:true,full:true},{name:'quantityDelta',label:'差异数量（调减填负数）',type:'number',step:1,value:0,required:true},{name:'reason',label:'实盘依据 / 原因',required:true,full:true}],submitText:'记录库存调整'});if(!f)return;const x=options.find(x=>x.v.id===Number(f.vid));await post('/api/platform/inventory/adjustments',{productId:x.p.id,variantId:x.v.id,quantityDelta:Number(f.quantityDelta),reason:f.reason});refresh();});
// Replace the brittle table-column hook with explicit row data from the server renderer.
window.retryOrderCommerce=async id=>uxTask(async()=>{const r=await post(`/api/platform/orders/${id}/shipment/sync-commerce`);toast(r.status==='synced'?'交易引擎已同步':r.message||'仍待同步');loadOrders();});
const oldOrders=window.loadOrders;
window.loadOrders=async function(){await oldOrders();document.querySelectorAll('#orderRows tr[data-status]').forEach(tr=>{if(!['paid','processing','confirmed'].includes(tr.dataset.status)||tr.querySelector('.ux-pick-action'))return;const b=document.createElement('button');b.className='btn ghost ux-pick-action';b.textContent='创建拣货';b.onclick=()=>createPick(Number(tr.dataset.orderId));tr.lastElementChild.querySelector('div')?.prepend(b);});};
})();
(function(){
if(!location.pathname.startsWith('/platform'))return;
const original=window.loadWarehouse;
window.loadWarehouse=async function(){
 await original();
 const pending=await api('/api/platform/stock/reconciliation');let panel=document.querySelector('#stockReconciliation');
 if(!panel){panel=document.createElement('div');panel.id='stockReconciliation';panel.className='card section';document.querySelector('#warehouse').appendChild(panel);}
 panel.replaceChildren();if(!pending.length){panel.hidden=true;return;}panel.hidden=false;
 const heading=document.createElement('h3');heading.textContent='历史 SKU 库存待核对';panel.appendChild(heading);
 pending.forEach(p=>{const row=document.createElement('div');row.className='list-row';const text=document.createElement('span');text.textContent=p.name+' · 原账已保留，实盘拆分完成前暂停库存操作';const button=document.createElement('button');button.className='btn secondary';button.textContent='实盘核对';button.onclick=()=>reconcileInventory(p);row.append(text,button);panel.appendChild(row);});
};
window.reconcileInventory=async p=>uxTask(async()=>{
 const old=JSON.parse(p.snapshot_json),vs=(await api(`/api/platform/products/${p.product_id}/variants`)).filter(v=>v.status==='active');
 if(old.some(x=>x.reserved>0))return showAlert({title:'需逐单核对已付款订单',message:'该商品还有历史订单预留，无法仅凭商品总量确认颜色尺码。原库存和订单完整保留，请先核对订单的具体 SKU 与拣货位置。'});
 const fields=old.flatMap((r,i)=>vs.flatMap(v=>[{name:`q${i}_${v.id}`,label:`库位 #${r.location_id} · ${v.name} 实物数`,type:'number',min:0,step:1,value:0,required:true},{name:`u${i}_${v.id}`,label:`库位 #${r.location_id} · ${v.name} 不可售数`,type:'number',min:0,step:1,value:0,required:true}]));
 const f=await uxForm({title:'按实盘拆分历史 SKU',subtitle:'每个库位的实物/不可售合计须保持原账一致。不同于盘盈盘亏，此操作只确认颜色尺码归属。',fields:[...fields,{name:'note',label:'实盘凭证 / 核对说明',required:true,full:true}],submitText:'确认 SKU 归属'});if(!f)return;
 const balances=old.flatMap((r,i)=>vs.map(v=>({variantId:v.id,locationId:r.location_id,onHand:Number(f[`q${i}_${v.id}`]),unsellable:Number(f[`u${i}_${v.id}`])})));
 await api(`/api/platform/warehouse/reconcile/${p.product_id}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({balances,note:f.note})});toast('历史 SKU 已核对，原实物总量保持一致');loadWarehouse();loadProducts();
});
})();
