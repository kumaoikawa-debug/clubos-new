let CLUB=Number(new URLSearchParams(location.search).get('club_id')||1),USER=1,PAYER_NAME='林野',PAYER_PHONE='13800000001';let currentAct=null,currentOcc=null,activityVouchers=[],bookingParticipants=[];
function wv(id,b){$$('.wview').forEach(x=>x.style.display='none');$('#'+id).style.display='block';$$('.web-nav button').forEach(x=>x.classList.remove('active'));b?.classList.add('active');if(id==='wmall')loadMall();if(id==='wmember')loadMemberCenter();if(id==='worders')loadOrders()}
async function loadActivities(){let a=await api(`/api/public/clubs/${CLUB}/activities`);$('#publicActivities').innerHTML=a.length?`<div class="act-grid">`+a.map(x=>{const cov=x.id%6+1;const date=x.event_date?`<span>${esc(x.event_date)}</span>`:'';return `<div class="act-card" onclick="openAct(${x.id})"><div class="act-card__cover cov-${cov}"><div class="act-card__coverInner"><span class="act-card__loc">📍 ${esc(x.location||'户外')}</span><h3 class="act-card__title">${esc(x.title)}</h3></div></div><div class="act-card__body"><div class="act-card__meta">${date}<span class="badge badge--live badge--dot">报名中</span><span class="act-card__price">${money(x.price)}</span></div></div></div>`}).join('')+`</div>`:'<div class="web-card empty">俱乐部暂时没有已发布活动</div>';let q=new URLSearchParams(location.search).get('activity');if(q)openAct(Number(q))}
async function openAct(id){let a=await api(`/api/public/activities/${id}`);currentAct=a;currentOcc=a.occurrences?.[0]||null;bookingParticipants=[{name:PAYER_NAME,phone:PAYER_PHONE,relationToPayer:'本人',idType:'',idNumber:'',emergencyContactName:'',emergencyContactPhone:''}];$('#publicActivities').style.display='none';$('#publicDetail').innerHTML=`<button class="btn ghost" onclick="backList()">← 返回活动</button><div class="public-editorial" style="margin-top:10px">${renderPromo(a.detail,a.activityMaster,{hideButton:true})}</div>${renderInfoStack(a.activityMaster)}${bookingHtml(a)}`;window.scrollTo(0,0);renderParticipantForms();await loadActivityVouchers();await refreshQuote()}
function backList(){$('#publicActivities').style.display='block';$('#publicDetail').innerHTML='';history.replaceState({},'',location.pathname)}
function bookingHtml(a){
  const p=a.pointsPolicy||{}; const e=p.effective||{};
  const clubBox=e.acceptClubPoints?`<div class="point-box"><b>活动积分</b><div class="sub">本俱乐部资产 · 成本由俱乐部承担${p.clubPointsMaxDiscountPercent<100?` · 最多抵 ${p.clubPointsMaxDiscountPercent}%`:''}</div><input id="clubUse" type="number" min="0" value="0" oninput="refreshQuote()"></div>`:'';
  const gearCap=p.gearPointsMaxDiscountAmount==null?'':` · 最多补贴 ${money(p.gearPointsMaxDiscountAmount)}`;
  const gearBox=e.acceptGearPoints?`<div class="point-box"><b>装备积分</b><div class="sub">平台资产 · 抵扣由总平台补贴${gearCap}</div><input id="gearUse" type="number" min="0" value="0" oninput="refreshQuote()"></div>`:'';
  let pointArea='';
  if(p.enabled && (clubBox||gearBox)){pointArea=`<div class="point-row">${clubBox}${gearBox}</div>`}
  else if(!p.enabled){pointArea='<div class="notice" style="margin-top:14px">本活动不参与积分抵扣。</div>'}
  const earnNote=e.earnClubPoints?'<div class="notice" style="margin-top:10px">报名完成后，本次现金实付金额将按俱乐部规则累计活动积分。</div>':'';
  const benefitArea=`<div id="activityBenefitArea" style="margin-top:12px"></div>`;
  const pp=a.participantPolicy||{};
  const participantArea=`<div class="point-box" style="margin-top:14px"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center"><div><b>参加人</b><div class="sub">付款人和参加人可以不同；每位参加人占 1 个名额。</div></div><label class="sub">人数 <input id="participantCount" type="number" min="1" max="${pp.maxParticipantsPerOrder||8}" value="1" style="width:72px;margin-left:6px" onchange="setParticipantCount(this.value)"></label></div><div id="participantForms" style="margin-top:12px"></div><div class="sub" style="margin-top:8px">${pp.allowIncompleteAtCheckout===false?'本活动要求支付前完成全部报名资料。':'可先报名支付；身份证/紧急联系人等资料可在订单中心后补。'}${pp.insuranceRequired?' · 本活动需要保险资料。':''}</div></div>`;
  return `<div class="booking-card" id="bookingCard"><div class="eyebrow">BOOK THIS TRIP</div><h2 style="margin:6px 0 2px">选择团期</h2><div class="sub">同一活动可有不同日期、不同价格和不同名额。</div><div class="occ-list">${(a.occurrences||[]).map((o,i)=>`<div class="occ ${i===0?'active':''}" data-occ="${o.id}" onclick="selectOcc(${o.id},this)"><div><strong>${esc(o.label||o.start_at)}</strong><div class="sub">剩余 ${o.remaining} / ${o.capacity}</div></div><strong>${money(o.price)}</strong></div>`).join('')||'<div class="notice warn">暂无可报名团期</div>'}</div>${participantArea}${pointArea}${benefitArea}${earnNote}${refundPolicyBrief(a.refundPolicy)}<div class="quote-box" id="quoteBox"><div class="sub" style="color:#b8c8c2">正在计算...</div></div><button class="btn" style="width:100%;margin-top:12px" onclick="signupNow()">立即报名</button></div>`
}
function refundPolicyBrief(p){
  if(!p)return '';
  if(!p.enabled)return '<div class="notice warn" style="margin-top:10px"><strong>退款规则</strong><div class="sub">本活动不支持用户自主退款；如遇特殊情况请联系俱乐部。</div></div>';
  const rules=(p.rules||[]).map(r=>`${r.label||`出发前${r.minHoursBefore}小时`}：现金退 ${Number(r.cashRefundPercent)}%`).join(' · ');
  return `<div class="notice" style="margin-top:10px"><strong>退款规则</strong><div class="sub">${esc(rules||'以订单申请时系统计算为准')}${p.afterStartCashRefundPercent?` · 活动开始后退 ${Number(p.afterStartCashRefundPercent)}%`:' · 活动开始后不退'}</div></div>`
}
function setParticipantCount(v){
  const max=Number(currentAct?.participantPolicy?.maxParticipantsPerOrder||8),n=Math.max(1,Math.min(max,Number(v||1)));
  while(bookingParticipants.length<n)bookingParticipants.push({name:'',phone:'',relationToPayer:'同行人',idType:'',idNumber:'',emergencyContactName:'',emergencyContactPhone:''});
  bookingParticipants=bookingParticipants.slice(0,n); if($('#participantCount'))$('#participantCount').value=n; renderParticipantForms(); refreshQuote();
}
function participantField(i,key,val){bookingParticipants[i][key]=val}
function renderParticipantForms(){
  const box=$('#participantForms');if(!box)return;
  box.innerHTML=bookingParticipants.map((p,i)=>`<div class="notice" style="margin-bottom:10px"><strong>参加人 ${i+1}${i===0?' · 可与付款人相同':''}</strong><div class="grid g2" style="margin-top:8px"><input placeholder="姓名*" value="${esc(p.name||'')}" oninput="participantField(${i},'name',this.value)" style="padding:9px;border:1px solid var(--line);border-radius:9px"><input placeholder="手机号*" value="${esc(p.phone||'')}" oninput="participantField(${i},'phone',this.value)" style="padding:9px;border:1px solid var(--line);border-radius:9px"><input placeholder="证件类型，如身份证" value="${esc(p.idType||'')}" oninput="participantField(${i},'idType',this.value)" style="padding:9px;border:1px solid var(--line);border-radius:9px"><input placeholder="证件号码（可后补）" value="${esc(p.idNumber||'')}" oninput="participantField(${i},'idNumber',this.value)" style="padding:9px;border:1px solid var(--line);border-radius:9px"><input placeholder="紧急联系人（可后补）" value="${esc(p.emergencyContactName||'')}" oninput="participantField(${i},'emergencyContactName',this.value)" style="padding:9px;border:1px solid var(--line);border-radius:9px"><input placeholder="紧急联系人电话" value="${esc(p.emergencyContactPhone||'')}" oninput="participantField(${i},'emergencyContactPhone',this.value)" style="padding:9px;border:1px solid var(--line);border-radius:9px"></div></div>`).join('');
}
async function loadActivityVouchers(){
  if(!currentAct||!$('#activityBenefitArea'))return;
  try{activityVouchers=await api(`/api/public/clubs/${CLUB}/vouchers?user_id=${USER}&kind=activity`)}catch(e){activityVouchers=[]}
  const box=$('#activityBenefitArea');
  if(!activityVouchers.length){box.innerHTML='';return}
  box.innerHTML=`<div class="point-box"><b>会员福利券</b><div class="sub">积分兑换后的福利券可在交易中真正核销，成本归属保持不变。</div><select id="benefitUse" onchange="refreshQuote()" style="width:100%;margin-top:8px;padding:10px;border-radius:10px"><option value="">本单不使用福利券</option>${activityVouchers.map(v=>`<option value="${esc(v.voucher_code)}">${esc(v.title)} · 抵 ${money(v.cash_value)} · ${v.funding_owner==='CLUB'?'俱乐部承担':'平台承担'}</option>`).join('')}</select></div>`
}
function selectOcc(id,el){currentOcc=currentAct.occurrences.find(x=>x.id===id);$$('.occ').forEach(x=>x.classList.remove('active'));el.classList.add('active');refreshQuote()}
async function refreshQuote(){if(!currentAct||!currentOcc||!$('#quoteBox'))return;let cp=Number($('#clubUse')?.value||0),gp=Number($('#gearUse')?.value||0),voucher=$('#benefitUse')?.value||'';try{let q=await api(`/api/public/activities/${currentAct.id}/price-quote?occurrence_id=${currentOcc.id}&user_id=${USER}&club_points=${cp}&gear_points=${gp}&voucher_codes=${encodeURIComponent(voucher)}&participant_count=${bookingParticipants.length}`);if($('#clubUse'))$('#clubUse').max=q.wallet.clubPoints;if($('#gearUse'))$('#gearUse').max=q.wallet.gearPoints;let lines=`<div class="quote-line"><span>活动费用（${q.participantCount}人 × ${money(q.unitPrice)}）</span><span>${money(q.original)}</span></div>`;if(q.pointsPolicy?.effective?.acceptClubPoints)lines+=`<div class="quote-line"><span>活动积分抵扣（俱乐部承担）</span><span>- ${money(q.clubPointDiscount)}</span></div>`;if(q.pointsPolicy?.effective?.acceptGearPoints)lines+=`<div class="quote-line"><span>装备积分补贴（平台承担）</span><span>- ${money(q.platformPointSubsidy)}</span></div>`;(q.benefits?.applied||[]).forEach(v=>{lines+=`<div class="quote-line"><span>${esc(v.title)}（${v.fundingOwner==='CLUB'?'俱乐部承担':'平台承担'}）</span><span>- ${money(v.cashValue)}</span></div>`});lines+=`<div class="quote-line total"><span>需支付</span><span>${money(q.payable)}</span></div>`;let balances=[];if(q.pointsPolicy?.effective?.acceptClubPoints)balances.push(`活动积分 ${q.wallet.clubPoints}`);if(q.pointsPolicy?.effective?.acceptGearPoints)balances.push(`装备积分 ${q.wallet.gearPoints}`);if(balances.length)lines+=`<div class="sub" style="color:#a9bbb4;margin-top:8px">可用：${balances.join(' · ')}</div>`;$('#quoteBox').innerHTML=lines}catch(e){$('#quoteBox').textContent=e.message}}
async function waitForCheckoutPaid(checkoutId,attempts=45){for(let i=0;i<attempts;i++){await new Promise(r=>setTimeout(r,2000));let x=await api(`/api/public/checkouts/${checkoutId}`);if(x.status==='paid'||x.payment_status==='succeeded')return x.result||x;if(x.payment_status==='failed')throw new Error('支付失败，可重新发起支付')}throw new Error('支付状态仍在处理中，请稍后到“我的订单”查看')}
async function payCheckout(checkout){let p=await api(`/api/public/checkouts/${checkout.checkoutId}/pay`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({simulateSuccess:!Boolean(clubosCookie('clubos_csrf')),returnUrl:location.href})});if(p.paymentStatus==='succeeded')return p.result;let a=p.paymentAction||{};if(a.type==='redirect'&&a.url){location.href=a.url;return null}if(a.type==='jsapi'&&a.params){if(window.WeixinJSBridge){await new Promise((resolve,reject)=>WeixinJSBridge.invoke('getBrandWCPayRequest',a.params,r=>String(r.err_msg||'').includes(':ok')?resolve(r):reject(new Error(r.err_msg||'微信支付未完成'))));return await waitForCheckoutPaid(checkout.checkoutId)}alert('当前页面不在微信 JSAPI 环境，请在微信内打开');return null}if(a.type==='qrcode'&&a.url){prompt('微信 Native 支付 code_url（正式前端用二维码组件渲染）：',a.url);return await waitForCheckoutPaid(checkout.checkoutId,15)}return null}
async function signupNow(){if(!currentOcc)return alert('请选择团期');let cp=Number($('#clubUse')?.value||0),gp=Number($('#gearUse')?.value||0),voucher=$('#benefitUse')?.value||'';try{let d=await api(`/api/public/activities/${currentAct.id}/checkout`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:PAYER_NAME,phone:PAYER_PHONE,occurrenceId:currentOcc.id,clubPoints:cp,gearPoints:gp,voucherCodes:voucher?[voucher]:[],participants:bookingParticipants})});let paid=await payCheckout(d);if(!paid)return;alert(`报名成功 · ${paid.participantCount||bookingParticipants.length} 人\n实际支付 ${money(paid.cashPaid)}\n本次获得 ${paid.clubPointsEarned} 活动积分${paid.clubBenefitDiscount?`\n俱乐部福利抵扣 ${money(paid.clubBenefitDiscount)}`:''}${paid.platformBenefitSubsidy?`\n平台福利补贴 ${money(paid.platformBenefitSubsidy)}`:''}`);await loadWallet();openAct(currentAct.id)}catch(e){alert(e.message)}}
async function loadMall(){let p=await api(`/api/public/clubs/${CLUB}/mall/products`);$('#publicProducts').innerHTML=p.map(x=>`<div class="web-card"><div style="height:130px;border-radius:14px;background:#edf3f1;display:grid;place-items:center;font-size:42px">🎒</div><h3>${esc(x.name)}</h3><div class="price">${money(x.price)}</div><div class="sub">平台统一发货 · 库存 ${x.stock}</div><button class="btn" style="width:100%;margin-top:12px" onclick="buy(${x.id})">购买</button></div>`).join('')}
async function buy(pid){let w=await api(`/api/public/users/${USER}/wallet?club_id=${CLUB}`);let use=Number(prompt(`你有 ${w.gearPoints} 装备积分，本单想使用多少？`,0)||0);let vouchers=[];try{vouchers=await api(`/api/public/clubs/${CLUB}/vouchers?user_id=${USER}&kind=gear`)}catch(e){}let voucher='';if(vouchers.length){let msg='可用装备福利券：\n'+vouchers.map((v,i)=>`${i+1}. ${v.title} · 抵 ${money(v.cash_value)}`).join('\n')+'\n输入序号使用，0=不用';let pick=Number(prompt(msg,'0')||0);if(pick>0&&vouchers[pick-1])voucher=vouchers[pick-1].voucher_code}try{let d=await api(`/api/public/clubs/${CLUB}/gear-checkout`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:USER,items:[{productId:pid,quantity:1}],gearPoints:use,voucherCodes:voucher?[voucher]:[]})});let paid=await payCheckout(d);if(!paid)return;alert(`下单成功\n实际支付 ${money(paid.cashPaid)}\n获得 ${paid.gearPointsEarned} 装备积分${paid.platformBenefitSubsidy?`\n平台福利补贴 ${money(paid.platformBenefitSubsidy)}`:''}\n俱乐部获得 ${money(paid.clubCommission)} 佣金\n俱乐部获得 ${paid.clubAIReward} AI Credits奖励`);loadWallet();loadMemberCenter()}catch(e){alert(e.message)}}
async function loadMemberCenter(){
  let d=await api(`/api/public/clubs/${CLUB}/member-center?user_id=${USER}`),w=d.wallet||{};
  if($('#clubPts'))$('#clubPts').textContent=w.clubPoints??0;if($('#gearPts'))$('#gearPts').textContent=w.gearPoints??0;
  if($('#memberLevel'))$('#memberLevel').innerHTML=`<strong>${esc(w.memberLevel||'普通会员')}</strong> · 已参加 ${w.activityCount||0} 场活动 · 累计活动消费 ${money(w.lifetimeActivitySpend||0)}`;
  if($('#memberBenefits'))$('#memberBenefits').innerHTML=(d.benefits||[]).map(x=>{let pt=x.points_type==='club'?'活动积分':'装备积分',owner=x.owner_type==='CLUB'?'俱乐部承担':'ClubOS 平台承担';return `<div class="notice" style="margin-bottom:10px"><div class="panel-title"><div><strong>${esc(x.title)}</strong><div class="sub">${esc(x.description||'')}</div></div><span class="tag">${owner}</span></div><div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px"><span><strong>${x.points_cost}</strong> ${pt}${x.cash_value?` · 权益价值 ${money(x.cash_value)}`:''}</span><button class="btn secondary" onclick="redeemBenefit(${x.id})">兑换</button></div></div>`}).join('')||'<div class="empty">暂无可兑换福利</div>';
  if($('#memberRedemptions'))$('#memberRedemptions').innerHTML=(d.redemptions||[]).map(x=>`<div style="padding:10px 0;border-bottom:1px solid var(--line)"><strong>${esc(x.title)}</strong><div class="sub">${x.points_spent} ${x.point_type==='club'?'活动积分':'装备积分'} · ${x.funding_owner==='CLUB'?'俱乐部承担':'平台承担'} · 券码 ${esc(x.voucher_code||'')} · ${x.status==='issued'?'可使用':x.status==='held'?'结算中':x.status==='used'?'已使用':esc(x.status||'')}</div></div>`).join('')||'<div class="empty">还没有兑换记录</div>';
}
async function redeemBenefit(id){if(!confirm('确认兑换这项会员福利？'))return;try{let r=await api(`/api/public/clubs/${CLUB}/benefits/${id}/redeem`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:USER})});alert(`兑换成功\n券码：${r.voucherCode}\n使用 ${r.pointsSpent} ${r.pointsType==='club'?'活动积分':'装备积分'}`);loadMemberCenter()}catch(e){alert(e.message)}}
async function loadWallet(){try{let d=await api(`/api/public/users/${USER}/wallet?club_id=${CLUB}`);if($('#clubPts'))$('#clubPts').textContent=d.clubPoints;if($('#gearPts'))$('#gearPts').textContent=d.gearPoints}catch(e){}}

function refundStateText(x){
  const p=x.refundProgress||{}; return `${p.label||'无退款申请'}${p.detail?` · ${p.detail}`:''}`
}
async function loadOrders(){
  let d=await api(`/api/public/users/${USER}/order-center?club_id=${CLUB}`);
  $('#activityOrders').innerHTML=(d.activityOrders||[]).map(x=>{
    const rq=x.refundQuote;
    let action='';
    if(x.status==='paid' && ['none','rejected'].includes(x.refund_status||'none')){
      action=rq?.eligible?`<button class="btn ghost" style="margin-top:8px" onclick="requestActivityRefund(${x.id},${rq.cashRefundAmount||0},${rq.cashRefundPercent||0})">申请退款</button>`:`<div class="sub" style="margin-top:8px">当前不可退款${rq?.reason?` · ${esc(rq.reason)}`:''}</div>`;
    }
    const ps=(x.participants||[]).map(p=>{const refunded=p.status==='refunded';const rq=p.refundQuote;const refundBtn=(!refunded&&rq?.eligible&&['none','rejected'].includes(p.refund_status||'none'))?` <button class="btn ghost" style="padding:3px 8px" onclick="requestParticipantRefund(${x.id},${p.id},${rq.cashRefundAmount||0},${rq.cashRefundPercent||0},'${esc(p.name)}')">退出/退款</button>`:'';return `<div class="sub" style="margin-top:5px;${refunded?'opacity:.6':''}">👤 ${esc(p.name)} · ${esc(p.phone||'')} · ${refunded?'已退出':`资料${p.form_status==='complete'?'完整':'待补'} · 保险 ${esc(p.insurance_status||'pending')}`} ${!refunded?`<button class="btn ghost" style="padding:3px 8px" onclick="editParticipant(${x.id},${p.id})">补资料</button> <button class="btn ghost" style="padding:3px 8px" onclick="replaceParticipant(${x.id},${p.id})">转名额</button>`:''}${refundBtn}<div class="sub">${p.refundProgress?.label||''}${p.participant_refund_cash?` · 已退 ${money(p.participant_refund_cash)}`:''}</div></div>`}).join('');
    return `<div style="padding:12px 0;border-bottom:1px solid var(--line)"><strong>${esc(x.activity_title)}</strong><div class="sub">${esc(x.occurrence_label||x.start_at||'')} · ${x.participantCount||1}人 · 实付 ${money(x.amount)} · 状态 ${esc(x.status)}</div>${ps}<div class="sub" style="margin-top:6px">退款：${esc(refundStateText(x))}${x.request_refund_percent!=null?` · 现金退 ${Number(x.request_refund_percent)}% / ${money(x.requested_refund_cash||0)}`:''}</div>${action}</div>`
  }).join('')||'<div class="empty">暂无活动订单</div>';
  $('#gearOrders').innerHTML=(d.gearOrders||[]).map(x=>{
    const items=(x.items||[]).map(i=>`${esc(i.product_name)} ×${i.quantity}`).join('、');
    let action='';
    if(x.status!=='refunded') action=`<button class="btn ghost" style="margin-top:8px" onclick='requestGearAfterSales(${JSON.stringify(x).replace(/'/g,"&#39;")})'>申请售后</button>`;
    const cases=(x.afterSalesCases||[]).map(a=>`<div class="sub" style="margin-top:5px">售后 ${esc(a.case_type)} · ${esc(a.status)}${a.return_tracking_no?` · 退货 ${esc(a.return_tracking_no)}`:''}${a.exchange_tracking_no?` · 换货 ${esc(a.exchange_tracking_no)}`:''}${a.status==='awaiting_return'?` <button class="btn ghost" style="padding:3px 8px" onclick="submitReturn('${a.id}')">填写退货物流</button>`:''}</div>`).join('');
    return `<div style="padding:12px 0;border-bottom:1px solid var(--line)"><strong>装备订单 #${x.id}</strong> · ${money(x.total)}<div class="sub">${items||'商品'} · ${x.carrier||'待发货'} ${x.tracking_no||''}</div><div class="sub">退款：${esc(refundStateText(x))}</div>${cases}${action}</div>`
  }).join('')||'<div class="empty">暂无装备订单</div>';
}
async function editParticipant(regId,pid){
  const d=await api(`/api/public/registrations/${regId}/participants`),p=(d.participants||[]).find(x=>x.id===pid);if(!p)return;
  let idType=prompt('证件类型',p.id_type||'身份证');if(idType===null)return;let idNumber=prompt('证件号码',p.id_number||'')||'';let ec=prompt('紧急联系人',p.emergency_contact_name||'')||'';let ep=prompt('紧急联系人电话',p.emergency_contact_phone||'')||'';
  try{await api(`/api/public/registrations/${regId}/participants/${pid}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({idType,idNumber,emergencyContactName:ec,emergencyContactPhone:ep})});toast('报名资料已更新');loadOrders()}catch(e){alert(e.message)}
}
async function replaceParticipant(regId,pid){
  let name=prompt('新参加人姓名');if(!name)return;let phone=prompt('新参加人手机号');if(!phone)return;
  try{await api(`/api/public/registrations/${regId}/participants/${pid}/replace`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,phone,relationToPayer:'同行人',reason:'用户自助转名额'})});toast('参加人已更换；保险信息需重新处理');loadOrders()}catch(e){alert(e.message)}
}
async function requestParticipantRefund(regId,pid,amount,pct,name){
  if(!confirm(`参加人 ${name} 退出后，预计现金退款 ${money(amount)}（${Number(pct)}%）。\n该参加人分摊的活动积分/装备积分会在退款成功后返还；订单级福利券只有全部参加人都退出时才恢复。\n确认提交？`))return;
  let reason=prompt('退出原因','临时有事无法参加')||'参加人退出';
  try{let r=await api(`/api/public/registrations/${regId}/participants/${pid}/refund-request`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason})});alert(`申请已提交\n${name} 预计现金退款 ${money(r.cashAmount)}\n活动积分返还 ${r.pointsToRestore?.club||0}\n装备积分返还 ${r.pointsToRestore?.gear||0}`);loadOrders()}catch(e){alert(e.message)}
}
async function requestActivityRefund(id,amount,pct){
  if(!confirm(`按当前活动规则，本次预计现金退款 ${money(amount)}（${Number(pct)}%）。\n退款成功后，本单使用的积分/福利券按规则恢复。\n确认提交？`))return;
  let reason=prompt('退款原因','临时有事无法参加')||'用户申请退款';
  try{let r=await api(`/api/public/registrations/${id}/refund-request`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason})});alert(`退款申请已提交\n预计现金退款 ${money(r.cashAmount)}\n退款比例 ${Number(r.refundPercent||0)}%${Number(r.retainedCashAmount||0)>0?`\n取消费 ${money(r.retainedCashAmount)}`:''}`);loadOrders()}catch(e){alert(e.message)}
}
async function requestGearAfterSales(order){
  const type=prompt('售后类型：refund_only=仅退款；return_refund=退货退款；exchange=换货','return_refund');if(!type)return;
  const opts=(order.items||[]).map(i=>`${i.id}:${i.product_name}×${i.quantity}`).join('\n');
  const itemId=Number(prompt(`选择订单商品ID：\n${opts}`,(order.items||[])[0]?.id||''));if(!itemId)return;
  const oi=(order.items||[]).find(i=>Number(i.id)===itemId);if(!oi)return alert('商品ID不正确');
  const qty=Number(prompt('售后数量',String(oi.quantity||1))||1);let reason=prompt('售后原因','尺码或商品问题')||'用户申请装备售后';
  try{await api(`/api/public/orders/${order.id}/after-sales`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:USER,type,reason,items:[{orderItemId:itemId,quantity:qty}]})});toast('售后申请已提交，由 ClubOS 总平台处理');loadOrders()}catch(e){alert(e.message)}
}
async function submitReturn(id){let carrier=prompt('退货承运商','顺丰')||'顺丰';let no=prompt('退货物流单号');if(!no)return;try{await api(`/api/public/after-sales/${id}/return-shipment`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({carrier,trackingNo:no})});toast('退货物流已提交');loadOrders()}catch(e){alert(e.message)}}
async function requestGearRefund(id){alert('v0.18 已升级为商品级售后，请使用“申请售后”。')}
async function afterSales(id){return requestGearRefund(id)}
async function startWeb(){
  if(clubosCookie('clubos_csrf')){
    try{const me=await api('/api/auth/me');if(me.role!=='member')throw new Error('请使用会员账号登录');USER=Number(me.userId);PAYER_NAME=me.name||'';PAYER_PHONE=me.phone||'';}catch(err){alert(err.message);location.href='/login';return}
  }
  try{const club=await api('/api/public/clubs/'+CLUB);if(club?.name){document.querySelector('.web-top strong').textContent=club.name;document.title=club.name+' · 活动与装备';}}catch(e){}
  await loadActivities();await loadWallet();
}
startWeb();
