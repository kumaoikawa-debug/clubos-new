/* v0.25 UX: replace high frequency admin prompt chains with deliberate forms; APIs unchanged. */
(function(){
 if(!location.pathname.startsWith('/platform'))return;
 const post=(path,data)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
 const patch=(path,data)=>api(path,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
 const option=(items,makeLabel)=>items.map(x=>({value:x.id,label:makeLabel(x)}));
 window.addClub=async()=>uxTask(async()=>{const d=await uxForm({title:'新增俱乐部入驻',subtitle:'创建后进入待审核；未审核前不能调用模型。',fields:[{name:'name',label:'俱乐部名称',required:true,full:true,maxLength:90},{name:'contactName',label:'联系人',required:true},{name:'contactPhone',label:'联系电话',required:true},{name:'city',label:'所在城市'},{name:'applicationNote',label:'申请说明',type:'textarea',full:true,value:'平台新增入驻'}],submitText:'提交待审核'});if(!d)return;await post('/api/platform/clubs',d);toast('已创建待审核俱乐部');loadClubs()});
/* 2026-10-24 多品牌化：新建商品的真身是 platform.js 的 addProduct()
   （六维 + 颜色×尺码矩阵 + SKU 自动生成）。本文件加载顺序在其后，
   window.addProduct 会整体覆盖它 —— 所以这里只保留 uxTask 包裹，
   并直接调别名 platformAddProduct（不能调 addProduct：那已经是本行覆盖后的
   自己，会无限递归）。 */
 window.addProduct=async()=>uxTask(async()=>{await platformAddProduct()});
 window.addSupplier=async()=>uxTask(async()=>{const d=await uxForm({title:'新增供应商',subtitle:'供应商档案用于采购、到货和应付账款。品牌一并建档，入库弹窗「品牌按供应商选」才能选到它。',fields:[{name:'name',label:'供应商名称',required:true,full:true},{name:'brandName',label:'品牌名称',full:true,help:'该供应商对应的品牌。填了会一并创建品牌并自动与该供应商关联；留空则只建供应商。'},{name:'contactName',label:'联系人',required:true},{name:'phone',label:'联系电话',required:true},{name:'leadTimeDays',label:'默认交期（天）',type:'number',min:0,max:365,step:1,value:7,required:true},{name:'paymentTerms',label:'账期说明',placeholder:'例如：月结30天',full:true}],submitText:'保存供应商'});if(!d)return;const sup=await post('/api/platform/suppliers',d);toast('供应商已创建');const bn=String(d.brandName||'').trim();if(bn&&sup&&sup.id){try{const br=await post('/api/platform/brands',{name:bn});await post('/api/platform/brands/'+br.id+'/suppliers',{supplierId:sup.id,isPrimary:true});toast('品牌「'+bn+'」已关联该供应商')}catch(e){toast('品牌关联未完成：'+(e.message||e))}}loadSupplyChain()});
/* V1.2 修复（进销存任务 bug）：此前「采购单内联快速建商品」写在 platform.js 的 createPO 里，
   但本文件在 platform.js 之后加载、整体覆盖了它，导致线上采购单从 V1.1 起一直只能用已有商品、
   看不到「➕ 快速新建商品」。现把真身收口到此处（本文件才是真正生效的版本）。
   商品下拉列出全部商品（不限于在售）+ 置顶「➕ 快速新建商品…」；零商品时也能现场建。 */
/* V1.2 修复（进销存任务）：快速新建商品改为「规格矩阵」编辑器 —— 同一货号可逐行加多个
   颜色/尺码（含非对称：黑色 S/M/L、红色 M/L），每行可录条码供收货扫码；不填规格行则
   只建一个默认规格。替代原先只有单个颜色/尺码输入框、无法表达多规格的版本。 */
/* V1.4（2026-11-01 用户反馈三点）：
   ① 品牌不再从全量品牌池选，而是「按供应商选」——采购单已选定供应商，
     品牌下拉只列该供应商在品牌管理里关联的品牌（GET /api/platform/suppliers/{id}/brands，
     反查 brand_suppliers）。供应商没关联品牌时给出提示并可留空，不阻断入库。
   ② 类别（品类）可现场手动增加：「＋ 新增品类」小表单（名称 + 可选上级），
     POST /api/platform/product-categories 后立即刷新下拉并选中新品类。
   ③ 规格区改为「颜色分组」：一个颜色一组，组内可加任意多行「尺码 × 数量 × 条码」，
     组头实时小计（黑色 S-30 M-30 L-30 → 小计 90 件），底部汇总「颜色 N · 规格 M · 合计 X 件」
     并作为采购数量带回 —— 总数依旧自动汇总，不手填。
   ★ 规格行数量语义不变：只是「本次采购多少件」，绝不写成期初库存（stock=0）。 */
async function quickCreateProduct(unitCost,supplierId){
  // 供应商 + 品牌：弹窗内可直接选供应商，品牌按所选供应商从品牌库带出（反查 brand_suppliers）。
  // 供应商未关联品牌时给提示并可留空；未选供应商时品牌提示「请先选择供应商」。
  let brandOpts=[],brandEmpty=false,catOpts=[],supOpts=[];
  try{ if(typeof loadProductDicts==='function'){ await loadProductDicts(); } }catch(e){}
  try{ if(typeof prodCatOptions==='function') catOpts=prodCatOptions()||[]; }catch(e){catOpts=[]}
  try{
    const ss=await api('/api/platform/suppliers');
    supOpts=(ss||[]).map(s=>({value:String(s.id),label:s.name+(s.code?`（${s.code}）`:'')}));
  }catch(e){supOpts=[]}
  if(supplierId){
    try{
      const sb=await api('/api/platform/suppliers/'+Number(supplierId)+'/brands');
      brandOpts=(sb||[]).filter(b=>b.status!=='inactive')
        .map(b=>({value:b.brand_id!=null?b.brand_id:b.id,label:b.name+(b.is_primary?'（主供应商）':'')}));
    }catch(e){brandOpts=[]}
    brandEmpty=!brandOpts.length;
  }
  try{
    const whs=await api('/api/platform/warehouses');
    whOpts=(whs||[]).map(w=>({value:w.name,label:(w.name||'仓库')+(w.code?`（${w.code}）`:''),def:!!w.is_default}));
  }catch(e){whOpts=[]}
  return await new Promise(resolve=>{
    let result=null;
    const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const optsHtml=(arr,ph)=>'<option value="">'+(ph||'')+'</option>'+arr.map(o=>`<option value="${esc(o.value)}"${o.def?' selected':''}>${esc(o.label)}</option>`).join('');
    const ov=uxDialog({title:'新商品入库 · 完善基础信息',wide:true,
      desc:'品牌按所选供应商带出；品类可直接新增。规格按颜色分组：一个颜色下加多个尺码和数量，组头自动小计，底部汇总总件数并带回采购单 —— 总数不用手填。后续运营只负责在「从仓库上架」里补图片与营销信息。',
      body:`<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">
        <div style="grid-column:1/-1;display:flex;flex-direction:column;gap:4px;font-size:13px"><span>供应商 *</span>${uxChoiceHtml({id:'qcSupplier',value:supplierId?String(supplierId):'',options:[{value:'',label:'请选择供应商'},...supOpts]})}</div>
        <label style="display:flex;flex-direction:column;gap:4px;font-size:13px">商品名称 *<input id="qcName" style="padding:8px;border:1px solid #cdd5cf;border-radius:8px"></label>
        <label style="display:flex;flex-direction:column;gap:4px;font-size:13px">货号 / 款号<small style="color:#8a958c">留空自动生成 GEAR-…</small><input id="qcSku" placeholder="例如 SUP-2026-001" style="padding:8px;border:1px solid #cdd5cf;border-radius:8px"></label>
        <div style="display:flex;flex-direction:column;gap:4px;font-size:13px"><span>品牌（按所选供应商带出）</span><div id="qcBrandWrap">${uxChoiceHtml({id:'qcBrand',value:'',options:[{value:'',label:supplierId?(brandEmpty?'该供应商暂未关联品牌':'暂不设品牌'):'请先选择供应商'},...brandOpts]})}</div>${supplierId&&brandEmpty?'<small style="color:#a06a00">该供应商还没在「品牌管理」里关联品牌 —— 可先去关联，或先留空完成入库。</small>':(supplierId?'':'<small style="color:#8a958c">选好供应商后，品牌会自动从品牌库带出。</small>')}</div>
        <div style="display:flex;flex-direction:column;gap:4px;font-size:13px"><span>类别</span><div id="qcCatWrap">${uxChoiceHtml({id:'qcCat',value:'',options:[{value:'',label:'暂不设类别'},...catOpts]})}</div><div><button type="button" id="qcAddCat" title="新增品类" style="padding:6px 12px;border:1px solid #cfe3d4;background:#f2f7f3;border-radius:8px;white-space:nowrap">＋ 新增品类</button></div></div>
        <label style="display:flex;flex-direction:column;gap:4px;font-size:13px">零售价（可由运营上架时填写）<input id="qcPrice" type="number" min="0" step="0.01" style="padding:8px;border:1px solid #cdd5cf;border-radius:8px"></label>
        <label style="display:flex;flex-direction:column;gap:4px;font-size:13px">成本价 / 采购单价（¥）<small style="color:#8a958c">同时作为本次采购单价</small><input id="qcCost" type="number" min="0" step="0.01" value="${unitCost!=null?unitCost:''}" style="padding:8px;border:1px solid #cdd5cf;border-radius:8px"></label>
        <div style="grid-column:1/-1;display:flex;flex-direction:column;gap:4px;font-size:13px"><span>入的仓库${whOpts.length?'':'<small style="color:#8a958c">（未配置仓库，可留空）</small>'}</span>${uxChoiceHtml({id:'qcWh',value:(whOpts.find(o=>o.def)||{}).value||'',options:[{value:'',label:'暂不指定'},...whOpts]})}</div>
      </div>
      <div style="border-top:1px solid #eee;padding-top:10px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px"><b style="font-size:14px">颜色 × 尺码 × 数量</b><button type="button" class="btn ghost" id="qcAddGroup" style="padding:4px 10px">＋ 加一个颜色</button></div>
        <div id="qcRows" style="display:flex;flex-direction:column;gap:8px"></div>
        <div id="qcSum" style="margin-top:10px;padding:10px 12px;background:#f2f7f3;border:1px solid #cfe3d4;border-radius:8px;font-size:13px;line-height:1.6"></div>
      </div>`,
      foot:'<button class="btn ghost" type="button" data-cancel>取消</button><button class="btn" type="button" data-submit>创建并带入采购数量</button>',
      onClose:()=>resolve(result)});
    const rowsEl=ov.querySelector('#qcRows'), sumEl=ov.querySelector('#qcSum');
    /* ── 颜色分组模型：组=颜色，组内行=尺码×数量×条码。组小计、全局汇总实时算。 ── */
    const groups=()=>[...rowsEl.querySelectorAll('.qc-group')];
    const rowValid=r=>!!(r.querySelector('.qcSize').value.trim()||r.querySelector('.qcBar').value.trim());
    const groupData=g=>{
      const color=g.querySelector('.qcColor').value.trim();
      const rows=[...g.querySelectorAll('.qc-row')].map(r=>({
        color,
        size:r.querySelector('.qcSize').value.trim(),
        qty:Math.max(0,parseInt(r.querySelector('.qcQty').value,10)||0),
        barcode:r.querySelector('.qcBar').value.trim()
      })).filter(r=>r.size||r.barcode);
      return {color,rows,subtotal:rows.reduce((a,r)=>a+r.qty,0)};
    };
    const refresh=()=>{
      let colors=0,specs=0,total=0;
      groups().forEach(g=>{
        const d=groupData(g);
        g.querySelector('.qcGSub').textContent=d.rows.length?`小计 ${d.subtotal} 件 · ${d.rows.length} 个尺码`:'填尺码行后计入小计';
        if(d.rows.length){colors++;specs+=d.rows.length;total+=d.subtotal;}
      });
      if(!specs){
        sumEl.innerHTML='<span style="color:#8a958c">未填规格：将只创建 1 个默认规格。给「颜色 → 尺码」行填上数量后，这里会自动汇总并带入采购数量。</span>';
        return;
      }
      sumEl.innerHTML=`<div>颜色 <b>${colors}</b> · 规格 <b>${specs}</b> 个 · 合计 <b style="font-size:16px;color:#1a7f37">${total}</b> 件</div>`
        +`<div class="sub" style="font-size:12px;color:#6b7a70;margin-top:2px">已按颜色自动汇总，采购数量无需手填（「＋ 加一个颜色」「＋ 加尺码」可继续）。</div>`;
    };
    const addSizeRow=(g,init)=>{const row=document.createElement('div');row.className='qc-row';row.style.cssText='display:grid;grid-template-columns:1fr 88px 1fr auto;gap:6px;align-items:center';
      row.innerHTML=`<input class="qcSize" placeholder="尺码 如 S" value="${esc(init&&init.size||'')}" style="padding:7px;border:1px solid #cdd5cf;border-radius:7px">`
        +`<input class="qcQty" type="number" min="0" step="1" value="${init&&init.qty!=null?Number(init.qty):1}" style="padding:7px;border:1px solid #cdd5cf;border-radius:7px">`
        +`<input class="qcBar" placeholder="条码（可留空）" value="${esc(init&&init.barcode||'')}" style="padding:7px;border:1px solid #cdd5cf;border-radius:7px">`
        +`<button type="button" class="qcDel" title="删除此行" style="padding:7px 10px;border:1px solid #e0c4c4;background:#fff;color:#b23;border-radius:7px">✕</button>`;
      row.querySelector('.qcQty').addEventListener('input',refresh);
      row.querySelector('.qcDel').onclick=()=>{row.remove();refresh();};
      g.querySelector('.qc-srows').appendChild(row);refresh();};
    const addGroup=(init)=>{
      const g=document.createElement('div');g.className='qc-group';
      g.style.cssText='border:1px solid #e3ece5;border-radius:10px;padding:10px;background:#fbfdfb;display:flex;flex-direction:column;gap:6px';
      g.innerHTML=`<div style="display:grid;grid-template-columns:1fr auto auto;gap:6px;align-items:center">`
        +`<input class="qcColor" placeholder="颜色 如 黑色（留空 = 无颜色款）" value="${esc(init&&init.color||'')}" style="padding:7px;border:1px solid #cdd5cf;border-radius:7px">`
        +`<span class="qcGSub" style="font-size:12px;color:#6b7a70;white-space:nowrap"></span>`
        +`<button type="button" class="qcGDel" title="删除整个颜色组" style="padding:6px 10px;border:1px solid #e0c4c4;background:#fff;color:#b23;border-radius:7px;white-space:nowrap">删颜色</button></div>`
        +`<div style="display:grid;grid-template-columns:1fr 88px 1fr auto;gap:6px;font-size:12px;color:#6b7a70"><span>尺码</span><span>数量</span><span>条码（可留空）</span><span></span></div>`
        +`<div class="qc-srows" style="display:flex;flex-direction:column;gap:6px"></div>`
        +`<button type="button" class="qcAddSize" style="align-self:flex-start;padding:4px 10px;border:1px dashed #b9ccbd;border-radius:7px;background:#fff;color:#3c7a4e">＋ 加尺码</button>`;
      g.querySelector('.qcGDel').onclick=()=>{g.remove();if(!groups().length)addGroup();else refresh();};
      g.querySelector('.qcAddSize').onclick=()=>addSizeRow(g);
      rowsEl.appendChild(g);
      (init&&init.sizes&&init.sizes.length?init.sizes:[null]).forEach(s=>addSizeRow(g,s));
      refresh();};
    ov.querySelector('#qcAddGroup').onclick=()=>addGroup();
    addGroup();refresh();
    /* ① 供应商选择：切换时按所选供应商重新带出品牌（品牌按供应商库筛选）。
       点选控件重建容器即可 —— 重建后的 hidden input 仍带 #qcBrand，读取逻辑不变。
       change 走事件委托（点选 hidden input 与原生 select 回退分支都冒泡）。 */
    const reloadBrands=async sid=>{
      const wrap=ov.querySelector('#qcBrandWrap'); if(!wrap)return;
      const build=opts=>{wrap.innerHTML=uxChoiceHtml({id:'qcBrand',value:'',options:[{value:'',label:sid?(opts.length?'暂不设品牌':'该供应商暂未关联品牌'):'请先选择供应商'},...opts]});};
      if(!sid){build([]);return;}
      try{
        const sb=await api('/api/platform/suppliers/'+Number(sid)+'/brands');
        build((sb||[]).filter(b=>b.status!=='inactive').map(b=>({value:b.brand_id!=null?b.brand_id:b.id,label:b.name+(b.is_primary?'（主供应商）':'')})));
      }catch(e){ toast('加载品牌失败'); build([]); }
    };
    ov.addEventListener('change',e=>{ if(e.target&&e.target.id==='qcSupplier') reloadBrands(e.target.value); });
    /* ② 品类现场新增：名称 + 可选上级（两层品类），成功后刷新字典并选中新值 */
    ov.querySelector('#qcAddCat').onclick=async()=>{
      try{
        const d=await uxForm({title:'新增品类',subtitle:'品类为两层结构（例：服装 → 冲锋衣），创建后立即可选。',fields:[
          {name:'name',label:'品类名称',required:true},
          {name:'parentId',label:'上级品类（可选）',type:'select',full:true,options:[{value:'',label:'（作为顶层品类）'},...catOpts]}],submitText:'创建'});
        if(!d||!String(d.name||'').trim())return;
        const body={name:String(d.name).trim()};
        if(d.parentId)body.parentId=Number(d.parentId);
        const r=await post('/api/platform/product-categories',body);
        try{ if(typeof loadProductDicts==='function'){ await loadProductDicts(true); } }catch(e){}
        if(typeof prodCatOptions==='function'){ try{ catOpts=prodCatOptions()||[]; }catch(e){} }
        const wrap=ov.querySelector('#qcCatWrap');
        if(wrap)wrap.innerHTML=uxChoiceHtml({id:'qcCat',value:(r&&r.id)?String(r.id):'',options:[{value:'',label:'暂不设类别'},...catOpts]});
        toast('品类已创建并选中');
      }catch(e){toast('创建品类失败：'+e.message);}
    };
    ov.querySelector('[data-cancel]').onclick=()=>ov.uxClose();
    ov.querySelector('[data-submit]').onclick=async()=>{
      const name=ov.querySelector('#qcName').value.trim();
      const price=Number(ov.querySelector('#qcPrice').value||0);
      if(!name){toast('请填写商品名称');ov.querySelector('#qcName').focus();return;}
      const supplierIdSel=ov.querySelector('#qcSupplier').value;
      if(!supplierIdSel){toast('请选择供应商');ov.querySelector('#qcSupplier').focus();return;}
      const sku=ov.querySelector('#qcSku').value.trim();
      const brandId=ov.querySelector('#qcBrand').value;
      const categoryId=ov.querySelector('#qcCat').value;
      const warehouse=ov.querySelector('#qcWh').value;
      const costRaw=ov.querySelector('#qcCost').value;
      const cost=(costRaw!==undefined&&costRaw!==''&&costRaw!=null)?Number(costRaw):undefined;
      const specs=groups().flatMap(g=>groupData(g).rows);
      const total=specs.reduce((a,s)=>a+s.qty,0);
      if(specs.length&&total<=0){toast('请至少给一个尺码填上数量');return;}
      try{
        const body={name,price,status:'draft',description:''};
        if(sku)body.sku=sku;
        // 品牌 / 类别 / 仓库：后端 POST 已支持 brandId / categoryId / warehouse（app.py:7344-7346）
        if(brandId)body.brandId=Number(brandId);
        if(categoryId)body.categoryId=Number(categoryId);
        if(warehouse)body.warehouse=warehouse;
        const created=await api('/api/platform/products',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        const pid=Number(created.id);
        const skuItems=[];
        if(specs.length){
          for(const s of specs){
            const vname=[s.color,s.size].filter(Boolean).join(' / ')||'默认';
            const vb={name:vname,color:s.color||null,size:s.size||null,...(price>0?{price}:{}),stock:0};
            if(s.barcode)vb.barcode=s.barcode;
            const variant=await api('/api/platform/products/'+pid+'/variants',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(vb)});
            skuItems.push({variantId:variant.id,quantity:s.qty});
          }
          /* 后端在没收到 colors 时会自动补一个空壳「默认」规格来承接库存（app.py:7429-7434）。
             这里已经按颜色×尺码建好了真实规格，那个空壳会让收货和上架各多出一行无关项 ——
             停用（不删：留档更安全，且收货端本来就只取 active 规格）。清理失败不影响主流程。 */
          try{
            const vs=await api('/api/platform/products/'+pid+'/variants');
            const dummy=(vs||[]).find(v=>String(v.status||'active')==='active' && !v.color && !v.size);
            if(dummy) await api('/api/platform/products/'+pid+'/variants/'+dummy.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:'inactive'})});
          }catch(e){}
        }
        result={id:pid,cost,totalQty:total,supplierId:supplierIdSel,skuItems};
        ov.uxClose();
      }catch(e){toast('创建商品失败：'+e.message);}
    };
  });
}
 window.quickCreateInventoryProduct=quickCreateProduct;
 window.inventoryBarcodeCamera=poOpenBarcodeCamera;
 window.createPO=async()=>uxTask(async()=>{
  let [ss,ps]=await Promise.all([api('/api/platform/suppliers?status=active'),api('/api/platform/products')]);
  if(!ss.length){toast('请先创建供应商');return}
  const productOptions=[{value:'__new__',label:'➕ 快速新建商品…'},...option(ps,x=>x.name+' · '+x.sku+' · 可售 '+x.stock)];
  const d=await uxForm({title:'创建采购单草稿',subtitle:'选择供应商与商品 → 确认数量与采购价 → 生成草稿。',steps:'01 / 采购信息 → 02 / 审核 → 03 / 下单 → 04 / 入库',hint:'创建草稿、审核、下单都不会增加库存。只有确认实物到货，才会发生入库。',fields:[
    {name:'supplierId',label:'供应商',type:'select',required:true,options:option(ss,x=>x.name+' · 交期 '+(x.lead_time_days||x.leadTimeDays||'—')+'天'),full:true},
    {name:'productId',label:'商品 / SKU（没有可现场新建）',type:'select',required:true,options:productOptions,full:true},
    {name:'quantity',label:'采购数量',type:'number',min:1,step:1,value:1,help:'选「快速新建商品」时由「颜色 × 尺码」数量自动汇总带入，无需手填；选已有商品时才需要填写。'},
    {name:'unitCost',label:'采购单价（元，可留空）',type:'number',min:0,step:0.01,placeholder:'留空使用供货价',help:'系统将按后台供应商供货关系校验 MOQ 和采购价格。'},
    {name:'note',label:'采购备注',type:'textarea',full:true,value:'平台采购'}
  ],submitText:'生成采购草稿'});
  if(!d)return;
  let productId=Number(d.productId), unitCost=(d.unitCost!==undefined&&d.unitCost!==''&&d.unitCost!=null)?Number(d.unitCost):undefined;
  let qty=Number(d.quantity)||1;
  let qcSupplierId=Number(d.supplierId);
  if(d.productId==='__new__'){
    /* ① 品牌按供应商选：把采购单已选的供应商传进入库弹窗，品牌下拉只列该供应商关联的品牌。 */
    const qc=await quickCreateProduct(unitCost,Number(d.supplierId));
    if(!qc)return;
    productId=Number(qc.id);
    if(qc.cost!==undefined)unitCost=qc.cost;
    if(qc.supplierId)qcSupplierId=Number(qc.supplierId);
    /* 入库逻辑要求「总数 = 所有颜色尺码数量之和」，不再让人手填一个拍脑袋的数：
       规格矩阵里每行录多少件，汇总就是采购数量（用户原话：总数不是手动填写的）。 */
    if(qc.totalQty>0){ qty=qc.totalQty; }
    toast('商品已创建 · 采购数量按规格合计自动带入 '+qty+' 件');
  }
  const item={productId,quantity:qty};
  if(unitCost!==undefined)item.unitCost=unitCost;
  const r=await post('/api/platform/procurement/purchase-orders',{supplierId:qcSupplierId,items:[item],note:d.note});
  toast('采购草稿已创建'+(r?.id?' #'+r.id:''));loadSupplyChain();
 });
 window.receivePO=async id=>uxTask(async()=>{
  let po=await api('/api/platform/procurement/purchase-orders/'+id);
  const rows=(po.items||[]).filter(x=>Number(x.quantity_outstanding??(x.quantity_ordered-x.quantity_received))>0);
  if(!rows.length){toast('采购单已经全部到货');return}
  const varMap={};
  await Promise.all([...new Set(rows.map(r=>r.product_id))].map(async pid=>{try{varMap[pid]=await api('/api/platform/products/'+pid+'/variants')}catch(e){varMap[pid]=[]}}));
  // V1.2 修复（进销存任务）：收货须确认入哪个仓 —— 拉取仓库/可售库位列表，弹窗内提供「入仓」下拉。
  let whOpts=[];
  try{const whs=await api('/api/platform/warehouses');whOpts=(whs||[]).flatMap(w=>(w.locations||[]).filter(l=>String(l.zone_type)==='sellable').map(l=>({value:l.id,label:(w.name||'仓库')+' · '+(l.name||l.code||'库位')+'（'+(l.code||'')+'）',def:!!l.is_default})));}catch(e){whOpts=[]}
  // V1.2 收货扫码：按 规格SKU/条码(barcode)/货号(product_sku) 建索引，扫到即确认颜色尺码并定位到对应行。
  const scanIdx={};
  for(const r of rows){
    const vs=(varMap[r.product_id]||[]).filter(v=>String(v.status||'active')==='active');
    // 单规格商品的收货输入框 data-vid 为空（后端靠 product_id 解析），扫码要用空 vid 命中那一行。
    const useVid = vs.length===1 ? '' : null;
    for(const v of vs){
      const vid = useVid===null ? v.id : '';
      const key=(v.sku||'').trim().toLowerCase(); if(key) scanIdx[key]={iid:r.id,vid,sku:v.sku,name:r.product_name,color:v.color,size:v.size};
      const bc=(v.barcode||'').trim().toLowerCase(); if(bc) scanIdx[bc]={iid:r.id,vid,sku:v.sku,name:r.product_name,color:v.color,size:v.size};
    }
    // 单规格商品：货号（款号）也能直接扫 —— 多规格时货号不唯一，需扫具体规格条码。
    if(vs.length===1){
      const psk=(r.product_sku||'').trim().toLowerCase(); if(psk) scanIdx[psk]={iid:r.id,vid:'',sku:r.product_sku,name:r.product_name,color:vs[0].color||'',size:vs[0].size||''};
    }
  }
  let html='';
  for(const r of rows){
    const remain=Number(r.quantity_outstanding??(r.quantity_ordered-r.quantity_received));
    const vs=(varMap[r.product_id]||[]).filter(v=>String(v.status||'active')==='active');
    if(vs.length>1){
      html+=`<div class="sub" style="margin:12px 0 4px;font-weight:700">${esc(r.product_name)} · 未到货 ${remain}（多规格逐行填写，可只填其中几行）</div>`;
      for(const v of vs) html+=`<label style="display:flex;gap:10px;align-items:center;margin:4px 0"><span style="flex:1">${esc(v.name||v.sku||('规格 #'+v.id))}${v.color?` · ${esc(v.color)}`:''}${v.size?` / ${esc(v.size)}`:''}<span class="sub"> · 现存 ${v.stock||0}</span></span><input type="number" min="0" value="0" class="rx" data-iid="${r.id}" data-vid="${v.id}" data-remain="${remain}" style="width:90px"></label>`;
    }else{
      html+=`<label style="display:flex;gap:10px;align-items:center;margin:4px 0"><span style="flex:1">${esc(r.product_name)}<span class="sub"> · 未到货 ${remain}</span></span><input type="number" min="0" value="${remain}" class="rx" data-iid="${r.id}" data-vid="" data-remain="${remain}" style="width:90px"></label>`;
    }
  }
  const ov=uxDialog({title:'确认到货入库',desc:'先选择入仓，再扫条码 / 输入货号确认颜色尺码，然后填实收数量；数量合计不能超过未到货量。',
    body:`<div style="position:sticky;top:0;background:#fff;z-index:2;padding-bottom:6px;border-bottom:1px solid #eee;margin-bottom:8px">`
      + (whOpts.length?`<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px"><span style="font-size:13px;white-space:nowrap;color:#3a4651">入仓</span><div style="flex:1">${uxChoiceHtml({id:'poLoc',value:(whOpts.find(o=>o.def)||{}).value||'',options:whOpts})}</div></div>`:'')
      +`<div style="display:flex;gap:8px;align-items:center"><input id="poScanInput" placeholder="扫条码或输入货号/SKU 后回车，系统确认颜色尺码" style="flex:1;padding:9px;border:1px solid #cdd5cf;border-radius:8px" autocomplete="off">`
      +`<button class="btn ghost" type="button" id="poScanCam">📷 扫一扫</button></div>`
      +`<div id="poScanResult" style="font-size:13px;margin-top:6px;min-height:18px"></div></div>`
      +'<div>'+html+'</div>',initialFocus:'#poScanInput',
    foot:'<button class="btn ghost" type="button" data-cancel>取消</button><button class="btn" type="button" data-submit>确认入库</button>'});
  ov.querySelector('[data-cancel]').onclick=()=>ov.uxClose();
  const applyScan=(raw)=>{
    const val=String(raw||'').trim().toLowerCase(); if(!val)return;
    const res=ov.querySelector('#poScanResult');
    const m=scanIdx[val];
    if(!m){res.style.color='#b45309';res.textContent=`⚠ 货号/条码「${raw}」不在本采购单中`;return;}
    const inp=ov.querySelector(m.vid ? `input.rx[data-iid="${m.iid}"][data-vid="${m.vid}"]` : `input.rx[data-iid="${m.iid}"]`);
    if(inp){inp.value=String((Number(inp.value||0))+1);inp.style.background='#e6f4ea';inp.style.boxShadow='0 0 0 2px #34a853 inset';}
    res.style.color='#1a7f37';
    res.textContent=`✓ 已匹配：${esc(m.name)} · 货号 ${esc(m.sku||'—')}${m.color?` · ${esc(m.color)}`:''}${m.size?` / ${esc(m.size)}`:''}（实收 +1）`;
  };
  const sinp=ov.querySelector('#poScanInput');
  sinp.addEventListener('keydown',e=>{ if(e.key==='Enter'){e.preventDefault();applyScan(sinp.value);sinp.value='';sinp.focus();} });
  ov.querySelector('#poScanCam').onclick=()=>poOpenBarcodeCamera(applyScan);
  ov.querySelector('[data-submit]').onclick=async()=>{
    const items=[];let over=null;
    ov.querySelectorAll('input.rx').forEach(inp=>{
      const q=Number(inp.value||0);if(q<=0)return;
      const remain=Number(inp.dataset.remain||0);
      if(remain&&q>remain&&!over)over=inp;
      items.push({purchaseOrderItemId:Number(inp.dataset.iid),quantity:q,variantId:inp.dataset.vid?Number(inp.dataset.vid):undefined});
    });
    if(over){over.classList.add('ux-invalid');over.focus();return}
    if(!items.length){toast('请至少填写一个规格的实收数量');return}
    try{
      const locEl=ov.querySelector('#poLoc');
      const payload={items,note:'平台仓库确认到货'};
      if(locEl&&locEl.value)payload.locationId=Number(locEl.value);
      let r=await api('/api/platform/procurement/purchase-orders/'+id+'/receive',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      let badSync=(r.commerceSync||[]).filter(x=>x.status==='error');
      toast(badSync.length?'已入库；Medusa库存同步待重试':'到货已入库');
      ov.uxClose();loadSupplyChain();loadProducts();
    }catch(e){toast('入库失败：'+e.message)}
  };
 });
// V1.2 收货相机扫码：用浏览器原生 BarcodeDetector 解商品一维条码（货号/条码）；
// 不支持的浏览器（或桌面无摄像头）自动降级为「扫码枪/手动输入」，不阻塞入库。
async function poOpenBarcodeCamera(apply){
  if(typeof BarcodeDetector==='undefined'){showAlert({title:'相机扫码不可用',message:'当前浏览器不支持相机扫码。请用扫码枪扫描，或在上方输入框手动输入货号/SKU。'});return;}
  let fmts=[];try{fmts=await BarcodeDetector.getSupportedFormats();}catch(e){}
  const want=['ean_13','ean_8','upc_a','upc_e','code_128','code_39','codabar','itf'];
  const use=want.filter(f=>fmts.includes(f));
  const ov=uxDialog({title:'扫商品条码',body:'<video id="poCamVid" autoplay playsinline style="width:100%;background:#000;border-radius:8px"></video>'
    +'<div id="poCamMsg" class="sub" style="margin-top:8px">将商品条码对准取景框…</div>',
    foot:'<button class="btn ghost" type="button" data-cc>取消</button>'});
  const v=ov.querySelector('#poCamVid'),msg=ov.querySelector('#poCamMsg');
  let stream=null,det=null,raf=null,done=false;
  const stop=()=>{done=true;if(raf)cancelAnimationFrame(raf);if(stream)stream.getTracks().forEach(t=>t.stop());};
  try{
    stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});
    v.srcObject=stream;await v.play();
    det=new BarcodeDetector({formats:use.length?use:fmts});
    const loop=async()=>{
      if(done)return;
      if(v.readyState>=2&&v.videoWidth){
        try{const codes=await det.detect(v);if(codes.length){const t=codes[0].rawValue.trim();stop();apply(t);ov.uxClose();return;}}catch(e){}
      }
      raf=requestAnimationFrame(loop);
    };
    loop();
  }catch(e){msg.textContent='相机打开失败：'+(e&&e.name||e.message||'权限不足')+'，请改用扫码枪或手动输入';}
  ov.querySelector('[data-cc]').onclick=()=>{stop();ov.uxClose();};
 }

 window.manualAdjust=async()=>uxTask(async()=>{const ps=await api('/api/platform/products');if(!ps.length){toast('暂无商品');return}const d=await uxForm({title:'库存盘点调整',subtitle:'调整将产生可追溯流水，不会覆盖原记录。',hint:'盘盈输入正数，盘亏输入负数；必须填写原因。',fields:[{name:'productId',label:'商品',type:'select',options:option(ps,x=>x.name+' · 当前可售 '+x.stock),required:true,full:true},{name:'quantityDelta',label:'调整数量',type:'number',step:1,required:true,value:1,validate:v=>v===0?'调整数量不能为 0':''},{name:'reason',label:'调整原因',required:true,placeholder:'例如：季度盘点发现差异',full:true}],submitText:'确认记入库存流水'});if(!d)return;await post('/api/platform/inventory/adjustments',{productId:Number(d.productId),quantityDelta:d.quantityDelta,reason:d.reason});toast('库存调整已记入流水');loadSupplyChain();loadProducts()});
 window.setReplenishmentPolicy=async()=>uxTask(async()=>{const p=await api('/api/platform/replenishment/policy');const d=await uxForm({title:'补货计算策略',subtitle:'修改后重新计算建议，不会自动下单。',fields:[{name:'salesWindowDays',label:'销量观察窗口（天）',type:'number',min:7,max:180,required:true,value:p.salesWindowDays},{name:'targetCoverDays',label:'目标库存覆盖（天）',type:'number',min:7,max:120,required:true,value:p.targetCoverDays},{name:'safetyDays',label:'安全库存（天）',type:'number',min:0,max:60,required:true,value:p.safetyDays},{name:'slowMovingDays',label:'无销售视作慢动销（天）',type:'number',min:30,max:365,required:true,value:p.slowMovingDays}],hint:'使用近窗净销量、供应商交期、在途采购和 MOQ；低销量商品需要人工审核。',submitText:'保存策略'});if(!d)return;await patch('/api/platform/replenishment/policy',d);toast('补货策略已更新');loadAnalytics()});
 window.createReplenishmentPO=async(productId,qty)=>uxTask(async()=>{const d=await uxForm({title:'确认补货建议',subtitle:'只创建采购草稿，仍须人工审批、确认下单和到货入库。',fields:[{name:'quantity',label:'建议采购数量',type:'number',min:1,step:1,value:qty,required:true}],submitText:'创建采购草稿'});if(!d)return;const r=await post('/api/platform/replenishment/products/'+productId+'/create-po',{quantity:d.quantity});toast('采购草稿已创建 #'+r.purchaseOrder.id);loadAnalytics()});
 window.paySupplier=async(supplierId,outstanding)=>uxTask(async()=>{if(Number(outstanding)<=0){toast('当前无应付余额');return}const d=await uxForm({title:'登记供应商付款',subtitle:'请先核对银行/支付凭证。重复凭证将由服务端幂等保护。',hint:'本操作只记录已经实际完成的付款，不会在银行发起转账。',fields:[{name:'amount',label:'本次付款金额（元）',type:'number',min:.01,max:Number(outstanding),step:.01,value:Number(outstanding).toFixed(2),required:true},{name:'paymentRef',label:'付款凭证号',required:true,full:true},{name:'note',label:'备注',value:'平台供应商付款',full:true}],submitText:'确认已付款并记账'});if(!d)return;await post('/api/platform/finance/supplier-payments',{supplierId,amount:d.amount,paymentRef:d.paymentRef,paymentMethod:'bank',note:d.note});toast('付款已登记');loadFinance()});
 window.setOrderCosts=async(orderId,shipping,packaging)=>uxTask(async()=>{const d=await uxForm({title:'订单运营成本 · #'+orderId,fields:[{name:'shippingCost',label:'物流成本（元）',type:'number',min:0,step:.01,value:shipping||0,required:true},{name:'packagingCost',label:'包材成本（元）',type:'number',min:0,step:.01,value:packaging||0,required:true}],submitText:'保存成本'});if(!d)return;await patch('/api/platform/orders/'+orderId+'/operating-costs',d);toast('订单成本已更新');loadFinance()});
 window.refundOrder=async id=>uxTask(async()=>{if(!await uxConfirm({title:'确认全额退款',message:'订单 #'+id+' 将执行全额退款，并按规则冲回 Gear Points、佣金与积分。请先确认支付渠道退款资格。',confirmText:'确认执行全额退款',danger:true}))return;const r=await post('/api/platform/orders/'+id+'/refund',{reason:'平台确认全额退款'});toast('退款已处理 · 佣金冲回 '+money(r.commissionReversed||0));loadOrders();loadCommissions();dash()});
 window.refundAS=async id=>uxTask(async()=>{if(!await uxConfirm({title:'确认售后退款',message:'售后单 #'+id+' 将提交支付渠道退款。该操作会影响佣金、积分和订单状态，请确认审核与收货条件。',confirmText:'确认退款',danger:true}))return;await api('/api/platform/after-sales/'+id+'/refund',{method:'POST'});toast('退款状态已更新');loadAfterSales();loadOrders();loadCommissions()});
})();
