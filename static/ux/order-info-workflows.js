/* ClubOS NEW · 订单信息台 / 资料补全（2026-11-16）
   需求来源：活动执行端一直提示「资料待补」，但后台/C 端都没有明确补全入口；
   会员详情里的历史订单点不开；经营数据下希望有活动/营地两类订单的统一列表。
   这里只放俱乐部端交互；C 端入口在 static/web/web.js。 */
(function(){
  const STATUS_ACTIVITY=[['paid','已支付'],['pending','待支付'],['refunded','已退款'],['cancelled','已取消'],['processing','处理中'],['requested','退款中']];
  const STATUS_CAMP=[['paid','已支付'],['pending_payment','待支付'],['checked_in','已入住'],['completed','已完成'],['cancelled','已取消'],['expired','已过期'],['deleted','已删除']];
  const stCn=(list,v)=>{const x=(list||[]).find(a=>a[0]===v);return x?x[1]:(v||'—')};
  const escHtml=v=>String(v==null?'':v);
  const moneyText=v=>'¥'+Number(v||0).toFixed(2);
  const line=(k,v)=>`<div style="display:flex;justify-content:space-between;gap:14px;border-bottom:1px dashed #d8e3dd;padding:8px 0"><span class="sub">${escHtml(k)}</span><strong>${escHtml(v==null||v===''?'—':v)}</strong></div>`;
  const opts=list=>list.map(([v,l])=>({value:v,label:l}));

  window.orderInfoState={kind:'activity'};
  window._orderInfoOv=null;

  window.orderInfoSetKind=function(kind){
    window.orderInfoState.kind=kind;
    document.querySelectorAll('#orderInfoKindSeg .seg-btn').forEach(b=>b.classList.toggle('active',b.dataset.kind===kind));
    const sel=document.getElementById('orderInfoStatus');
    if(sel){const list=kind==='activity'?STATUS_ACTIVITY:STATUS_CAMP;
      sel.innerHTML='<option value="">全部状态</option>'+list.map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}
    window.orderInfoLoad();
  };
  window.orderInfoReset=function(){
    ['orderInfoQuery','orderInfoFrom','orderInfoTo'].forEach(id=>{const el=document.getElementById(id);if(el)el.value=''});
    window.orderInfoSetKind('activity');
  };
  window.orderInfoLoad=async function(){
    const kind=window.orderInfoState.kind;
    const q=document.getElementById('orderInfoQuery')?.value||'';
    const from=document.getElementById('orderInfoFrom')?.value||'';
    const to=document.getElementById('orderInfoTo')?.value||'';
    const status=document.getElementById('orderInfoStatus')?.value||'';
    const rowsEl=document.getElementById('orderInfoRows');
    if(rowsEl)rowsEl.innerHTML='<tr><td colspan="7" class="empty">正在读取真实订单…</td></tr>';
    try{
      const qs=new URLSearchParams({kind,query:q,dateFrom:from,dateTo:to,status});
      const d=await api(`/api/club/${CLUB}/operation-orders?${qs}`);
      const s=d.summary||{};
      const sum=document.getElementById('orderInfoSummary');
      if(sum)sum.innerHTML=[
        ['订单数',s.count||0,`当前${kind==='activity'?'活动':'营地'}筛选结果`],
        ['订单金额',moneyText(s.amount),'含未支付/已退款订单原金额'],
        ['已支付金额',moneyText(s.paidAmount),'按当前账面金额统计'],
        ['待补资料',s.incompleteCount||0,'活动订单参加人维度'],
        ['退款处理中',s.refundPendingCount||0,'需到退款审核处理']
      ].map(([k,v,h])=>`<div class="stat-tile"><div class="k">${escHtml(k)}</div><div class="v">${escHtml(v)}</div><div class="hint">${escHtml(h)}</div></div>`).join('');
      if(rowsEl)rowsEl.innerHTML=(d.items||[]).map(x=>{
        const list=kind==='activity'?STATUS_ACTIVITY:STATUS_CAMP;
        const warn=x.needsAttention?' <span class="tag orange">待处理</span>':'';
        return `<tr>
          <td><strong>${escHtml(x.orderNo)}</strong></td>
          <td>${escHtml(x.title)}${x.subtitle?`<div class="sub">${escHtml(x.subtitle)}</div>`:''}${warn}</td>
          <td>${escHtml(x.buyer||'—')}${x.buyerPhone?`<div class="sub">${escHtml(x.buyerPhone)}</div>`:''}</td>
          <td><strong>${moneyText(x.amount)}</strong></td>
          <td><span class="tag ${['paid','completed','checked_in'].includes(x.status)?'':'orange'}">${escHtml(stCn(list,x.status))}</span>${x.refundStatus&&x.refundStatus!=='none'?`<div class="sub">退款 ${escHtml(x.refundStatus)}</div>`:''}</td>
          <td>${escHtml(String(x.createdAt||'').slice(0,16).replace('T',' '))}</td>
          <td class="row-ops"><button class="btn ghost" onclick="openOrderInfo('${kind}',${x.orderId})">详情</button> <button class="btn ghost" onclick="orderInfoAdjustPrice('${kind}',${x.orderId},${Number(x.amount)||0})">改价</button> <button class="btn ghost" onclick="orderInfoRefund('${kind}',${x.orderId},${Number(x.amount)||0})">退款</button></td>
        </tr>`}).join('')||`<tr><td colspan="7" class="empty">当前筛选没有订单。试试清空搜索词或切换状态。</td></tr>`;
    }catch(e){if(rowsEl)rowsEl.innerHTML=`<tr><td colspan="7" class="empty">读取失败：${escHtml(e.message||'未知错误')}</td></tr>`}
  };

  function detailActions(kind,id){
    return `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
      <button class="btn secondary" onclick="orderInfoAdjustPrice('${kind}',${id},null)">账面改价</button>
      <button class="btn ghost" onclick="orderInfoRefund('${kind}',${id},null)">${kind==='activity'?'发起退款':'线下退款登记'}</button>
      ${kind==='camp'?`<button class="btn ghost" onclick="orderInfoCampGuest(${id})">修改预订人</button>`:''}
    </div><div class="sub" style="margin-top:8px">改价 / 退款登记都会写入「操作留痕」。活动退款仍需到「退款审核」通过后才会走支付渠道。</div>`;
  }
  function logHtml(logs){
    return (logs||[]).map(l=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${escHtml(l.action)} <span class="tag">${escHtml(l.actor||'club')}</span></div><div class="list-row__sub">${escHtml(String(l.created_at||'').slice(0,16).replace('T',' '))} · ${escHtml(l.reason||'未填原因')}</div>${l.before||l.after?`<div class="list-row__sub">前 ${escHtml(JSON.stringify(l.before||{}))} → 后 ${escHtml(JSON.stringify(l.after||{}))}</div>`:''}</div></div>`).join('')||'<div class="empty">暂无人工干预记录</div>';
  }

  window.openOrderInfo=async function(kind,id){
    try{
      const d=await api(`/api/club/${CLUB}/operation-orders/${kind}/${id}`);
      let body='';
      if(kind==='activity'){
        const r=d.registration||{},b=d.buyer||{};
        const pax=(d.participants||[]).map(p=>`<div class="notice" style="margin-bottom:10px;${p.status==='refunded'?'opacity:.6':''}">
          <div class="panel-title"><div><strong>${escHtml(p.name||'未填写')}</strong> · ${escHtml(p.phone||'未填写')} <span class="tag ${p.form_status==='complete'?'':'orange'}">${p.form_status==='complete'?'资料完整':'资料待补'}</span>${p.status==='refunded'?' <span class="tag">已退出</span>':''}</div><span class="sub">#${p.id}</span></div>
          <div class="sub">证件：${escHtml(p.id_type||'—')} ${escHtml(p.id_number||'—')} · 紧急联系人：${escHtml(p.emergency_contact_name||'—')} ${escHtml(p.emergency_contact_phone||'')}</div>
          <div class="sub">用户备注 / 特别要求：${escHtml(p.notes||'—')}</div>
          <div class="sub">付款信息：实付 ${moneyText(p.allocation?.cash_paid||0)} · 俱乐部积分 ${p.allocation?.club_points_used||0} · 装备积分 ${p.allocation?.gear_points_used||0} · 已退 ${moneyText(p.participant_refund_cash||0)}</div>
          <div class="sub">保险：${escHtml(p.insurance_status||'pending')} ${escHtml(p.insurance_policy_no||'')} · 签到：${escHtml(p.checkin_status||'pending')}</div>
          ${p.status==='refunded'?'':`<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap"><button class="btn secondary" onclick="adminEditParticipant(${id},${p.id})">补全/修正资料</button><button class="btn ghost" onclick="adminReplaceParticipant(${id},${p.id})">变更参加人</button></div>`}
        </div>`).join('')||'<div class="empty">暂无参加人资料</div>';
        body=`<div class="grid g2" style="align-items:start"><div class="card">
          <div class="panel-title"><h3>订单 / 付款信息</h3><span class="tag">${escHtml(r.status||'')}</span></div>
          ${line('订单号','ACT-'+id)}${line('活动',d.activityTitle)}${line('档期',d.occurrenceLabel)}${line('地点',d.location)}
          ${line('付款人',b.name)}${line('付款人手机',b.phone)}${line('现金实付',moneyText(r.amount))}${line('原价',moneyText(r.original_amount))}
          ${line('俱乐部积分抵扣',moneyText(r.club_point_discount))}${line('平台补贴',moneyText(r.platform_point_subsidy))}
          ${line('支付状态',r.payment_status)}${line('退款状态',r.refund_status)}${line('已退款',moneyText(r.refund_cash_amount))}
          ${line('下单时间',String(r.created_at||'').slice(0,16).replace('T',' '))}${line('支付单号',r.commerce_order_id||r.booking_ref)}
          ${detailActions('activity',id)}</div>
          <div class="card"><div class="panel-title"><h3>操作留痕</h3><span class="sub">改价 / 补资料 / 变更 / 退款</span></div>${logHtml(d.adminLogs)}</div></div>
          <div class="card section"><div class="panel-title"><h3>报名人详情</h3><span class="tag">${(d.participants||[]).length} 人</span></div>${pax}</div>`;
      }else{
        const items=(d.items||[]).map(i=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${escHtml(i.title||'营地商品')} <span class="tag">${escHtml(i.kind||'')}</span></div><div class="list-row__sub">${escHtml(i.spec||'')} · ${i.qty||1} × ${moneyText(i.unitPrice)} · ${moneyText(i.subtotal)}${i.checkIn?` · ${escHtml(i.checkIn)} ~ ${escHtml(i.checkOut||'')}`:''}</div></div></div>`).join('')||'<div class="empty">暂无商品明细</div>';
        body=`<div class="grid g2" style="align-items:start"><div class="card">
          <div class="panel-title"><h3>订单 / 付款信息</h3><span class="tag">${escHtml(d.status||'')}</span></div>
          ${line('订单号',d.orderNo)}${line('预订人',d.guestName||d.buyer?.name)}${line('预订人手机',d.guestPhone||d.buyer?.phone)}
          ${line('商品金额',moneyText(d.goodsAmount))}${line('会员立减',moneyText(d.memberDiscount))}${line('积分抵扣',moneyText(d.clubPointDiscount)+' / '+moneyText(d.gearPointDiscount))}
          ${line('应付',moneyText(d.payable))}${line('押金',moneyText(d.depositAmount))}${line('订单总额',moneyText(d.total))}
          ${line('支付时间',String(d.paidAt||'').slice(0,16).replace('T',' '))}${line('核销码',d.redeemCode)}
          ${line('履约状态',[d.checkedInAt?'已入住':'未入住',d.checkedOutAt?'已退房':'未退房',d.settledAt?'已结算':'未结算'].join(' · '))}
          ${detailActions('camp',id)}</div>
          <div class="card"><div class="panel-title"><h3>操作留痕</h3><span class="sub">改价 / 预订人 / 线下退款</span></div>${logHtml(d.adminLogs)}</div></div>
          <div class="card section"><div class="panel-title"><h3>商品明细</h3><span class="tag">${(d.items||[]).length} 项</span></div>${items}</div>`;
      }
      const ov=uxDialog({title:kind==='activity'?'活动订单详情':'营地订单详情',wide:true,nested:true,
        desc:kind==='activity'?'姓名、手机号、证件、用户备注与付款分摊都在这里；可代客补资料或变更参加人。':'顾客信息、商品明细、押金与履约状态；营地订单退款为线下登记。',
        body,foot:'<button class="btn" type="button" data-ok>关闭</button>'});
      ov.querySelector('[data-ok]').onclick=()=>ov.uxClose();
      window._orderInfoOv=ov;
    }catch(e){showAlert({title:'读取详情失败',message:e.message})}
  };

  window.orderInfoAdjustPrice=async function(kind,id,current){
    const v=await uxForm({title:'账面改价',subtitle:`订单 #${id} · 当前 ${moneyText(current)}`,nested:true,submitText:'保存改价',
      hint:'这里只修正 ClubOS 账面金额，不调用支付渠道、不生成支付流水。改价原因必填，便于对账追溯。',
      fields:[{name:'amount',label:'调整后金额（元）',type:'number',step:'0.01',min:0,required:true,value:current==null?'':current},
              {name:'reason',label:'改价原因',type:'textarea',required:true,full:true,placeholder:'例如：线下补收 / 线下已退现金 / 活动规则修正'}]});
    if(!v)return;
    try{await api(`/api/club/${CLUB}/operation-orders/${kind}/${id}/price`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(v)});
      toast('账面金额已调整并留痕');window._orderInfoOv?.uxClose();window.orderInfoLoad();if(document.querySelector('.ux-overlay[data-ux-nested]'))openOrderInfo(kind,id)}
    catch(e){showAlert({title:'改价失败',message:e.message})}
  };

  window.orderInfoRefund=async function(kind,id,current){
    if(kind==='camp'){
      const v=await uxForm({title:'营地订单 · 线下退款登记',nested:true,submitText:'登记退款',
        hint:'营地订单尚未接入支付渠道原路退款。这里只登记线下退款金额与凭证说明，不会调用支付渠道，也不会自动改订单状态。',
        fields:[{name:'amount',label:'退款金额（元）',type:'number',step:'0.01',min:0.01,required:true,value:current==null?'':current},
                {name:'reason',label:'退款说明 / 凭证',type:'textarea',required:true,full:true,placeholder:'例如：已通过微信转账退回，交易单号 4200…'}]});
      if(!v)return;
      try{await api(`/api/club/${CLUB}/operation-orders/camp/${id}/refund-record`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(v)});
        toast('线下退款已登记');window._orderInfoOv?.uxClose();window.orderInfoLoad();openOrderInfo('camp',id)}
      catch(e){showAlert({title:'登记失败',message:e.message})}
      return;
    }
    const v=await uxForm({title:'发起活动退款申请',nested:true,submitText:'提交申请',
      hint:'提交后仍需到「退款审核」通过，才会走支付渠道退款。请按实际退给顾客的规则填写原因。',
      fields:[{name:'reason',label:'退款原因',type:'textarea',required:true,full:true,value:'后台代客发起退款'}]});
    if(!v)return;
    try{const r=await api(`/api/club/${CLUB}/operation-orders/activity/${id}/refund-request`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(v)});
      await showAlert({title:'退款申请已提交',message:`预计现金退款 ${moneyText(r.cashAmount)}\n退款比例 ${Number(r.refundPercent||0)}%\n请到「退款审核」完成审核。`});
      window._orderInfoOv?.uxClose();window.orderInfoLoad();openOrderInfo('activity',id)}
    catch(e){showAlert({title:'提交失败',message:e.message})}
  };

  window.orderInfoCampGuest=async function(id){
    const v=await uxForm({title:'修改营地预订人',nested:true,submitText:'保存',
      fields:[{name:'name',label:'预订人姓名',required:true},{name:'phone',label:'预订人手机号'},{name:'reason',label:'修改原因',type:'textarea',full:true,required:true}]});
    if(!v)return;
    try{await api(`/api/club/${CLUB}/operation-orders/camp/${id}/guest`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(v)});
      toast('预订人已更新并留痕');window._orderInfoOv?.uxClose();window.orderInfoLoad();openOrderInfo('camp',id)}
    catch(e){showAlert({title:'保存失败',message:e.message})}
  };

  window.adminEditParticipant=async function(regId,pid,occurrenceId){
    try{
      const d=await api(`/api/club/${CLUB}/registrations/${regId}/participants`);
      const p=(d.participants||[]).find(x=>Number(x.id)===Number(pid));if(!p)return;
      const v=await uxForm({title:'补全 / 修正报名资料',nested:true,submitText:'保存资料',
        subtitle:`${p.name||'未填写姓名'} · ${p.phone||'未填写手机号'}`,
        hint:'证件用于投保，证件号码按证件类型实时校验。修改会同时写入参加人变更日志与后台操作留痕。',
        fields:[
          {name:'name',label:'姓名',required:true,value:p.name||''},
          {name:'phone',label:'手机号',value:p.phone||''},
          {name:'relationToPayer',label:'与付款人关系',value:p.relation_to_payer||''},
          {name:'idType',label:'证件类型',type:'select',required:true,value:p.id_type||'身份证',options:(window.ID_TYPES||[]).map(t=>({value:t.v,label:t.label||t.v}))},
          {name:'idNumber',label:'证件号码',required:true,value:p.id_number||'',validate:(val,all)=>window.idNumberError?(window.idNumberError(all.idType,val)||''):''},
          {name:'emergencyContactName',label:'紧急联系人',value:p.emergency_contact_name||''},
          {name:'emergencyContactPhone',label:'紧急联系人电话',value:p.emergency_contact_phone||''},
          {name:'notes',label:'用户备注 / 特别要求',type:'textarea',full:true,value:p.notes||''},
          {name:'reason',label:'修改原因',type:'textarea',full:true,value:'后台代客补全/修正报名资料'}
        ]});
      if(!v)return;
      const reason=v.reason;delete v.reason;
      await api(`/api/club/${CLUB}/registrations/${regId}/participants/${pid}/profile`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({...v,reason})});
      toast('报名资料已保存，出发提醒已同步刷新');
      if(occurrenceId&&window.openExecution)await openExecution(occurrenceId);
      if(occurrenceId&&window.loadExecution)await loadExecution();
      if(window.orderInfoState&&document.getElementById('order-info')?.classList.contains('active'))window.orderInfoLoad();
    }catch(e){showAlert({title:'保存失败',message:e.message})}
  };

  window.adminReplaceParticipant=async function(regId,pid){
    const v=await uxForm({title:'后台变更参加人',nested:true,submitText:'确认变更',
      hint:'后台操作不受出发前 24 小时自助闸门限制，但新参加人同样要通过证件校验；旧参加人资料与操作人全部留痕。',
      fields:[
        {name:'name',label:'新参加人姓名',required:true},
        {name:'phone',label:'新参加人手机号',required:true},
        {name:'relationToPayer',label:'与付款人关系',value:'同行人'},
        {name:'idType',label:'证件类型',type:'select',required:true,value:'身份证',options:(window.ID_TYPES||[]).map(t=>({value:t.v,label:t.label||t.v}))},
        {name:'idNumber',label:'证件号码',required:true,validate:(val,all)=>window.idNumberError?(window.idNumberError(all.idType,val)||''):''},
        {name:'emergencyContactName',label:'紧急联系人'},
        {name:'emergencyContactPhone',label:'紧急联系人电话'},
        {name:'notes',label:'备注',type:'textarea',full:true},
        {name:'reason',label:'变更原因',type:'textarea',full:true,required:true,value:'后台变更参加人'}
      ]});
    if(!v)return;
    try{await api(`/api/club/${CLUB}/registrations/${regId}/participants/${pid}/replace`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(v)});
      toast('参加人已变更，保险信息需重新处理');
      if(window.orderInfoState&&document.getElementById('order-info')?.classList.contains('active'))window.orderInfoLoad();
    }catch(e){showAlert({title:'变更失败',message:e.message})}
  };
})();
