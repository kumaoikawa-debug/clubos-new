navInit();window.onView=v=>{if(v==='clubs')loadClubs();if(v==='pcredits')loadCredits();if(v==='ppoints')loadPointPolicy();if(v==='pbenefits')loadPlatformBenefits();if(v==='products'){loadProducts();loadVisibilityRequests()}if(v==='supply')loadSupplyChain();if(v==='warehouse')loadWarehouse();if(v==='finance')loadFinance();if(v==='analytics')loadAnalytics();if(v==='orders')loadOrders();if(v==='aftersales')loadAfterSales();if(v==='commissions')loadCommissions()};
/* logo 裂图兜底用事件委托绑一次（俱乐部表整块重渲染，逐个绑 onerror 会漏）。 */
document.addEventListener('error',platLogoFallback,true);
async function dash(){let d=await api('/api/platform/dashboard'),ai=d.aiBilling||{};$('#pMetrics').innerHTML=[['入驻俱乐部',d.clubs],['活跃俱乐部',d.activeClubs],['AI本月收入',money(ai.cashRevenueCny||0)],['装备GMV',money(d.gearGMV)]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div></div>`).join('');loadPlatformAttention()}
/* 工作台「待处理事项」：俱乐部提交的入驻申请 / 上下架申请、待处理的售后工单、待发货的
   装备订单，在总平台没有统一入口时只能逐个进对应页面翻。这里把它们汇总到工作台一张卡上，
   每项点击直接跳到对应视图 —— 复用已有的只读列表接口，按业务状态聚合，不做任何写操作。 */
function pgo(v){document.querySelector('.nav button[data-view="'+v+'"]')?.click()}
async function loadPlatformAttention(){
  const box=$('#platformAttention'); if(!box)return;
  box.innerHTML='<div class="sub">正在汇总待处理事项…</div>';
  try{
    const [clubs,reqs,as,orders]=await Promise.all([
      api('/api/platform/clubs'),
      api('/api/platform/product-visibility-requests'),
      api('/api/platform/after-sales'),
      api('/api/platform/orders')
    ]);
    const pendClubs=(clubs||[]).filter(x=>x.status==='pending').length;
    const pendReq=(reqs||[]).filter(x=>x.status==='pending').length;
    const AS_ACTION=['pending_review','return_in_transit','approved_pending_refund','exchange_pending_shipment'];
    const pendAS=(as||[]).filter(x=>AS_ACTION.includes(String(x.status||''))).length;
    const ship=(orders||[]).filter(x=>x.status==='paid'&&!(x.tracking_no)).length;
    const items=[];
    if(pendClubs)items.push({n:pendClubs,t:'待审俱乐部',go:'clubs',h:'入驻申请待审核'});
    if(pendReq)items.push({n:pendReq,t:'待审上下架',go:'products',h:'俱乐部商品上架申请'});
    if(pendAS)items.push({n:pendAS,t:'待审售后',go:'aftersales',h:'售后工单待处理'});
    if(ship)items.push({n:ship,t:'待发货订单',go:'orders',h:'装备订单待发货'});
    box.innerHTML=items.length
      ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px">`+items.map(i=>`<div style="border:1px solid #d8e3dd;border-radius:14px;padding:14px;cursor:pointer;background:#fff" role="button" tabindex="0" onclick="pgo('${i.go}')"><div style="font-size:26px;font-weight:800;color:#1f7a4d">${i.n}</div><div style="font-weight:600;margin-top:2px">${esc(i.t)}</div><div style="font-size:12px;color:#6b7f76;margin-top:3px">${esc(i.h)}</div><div style="font-size:12px;color:#1f7a4d;margin-top:8px">去处理 ›</div></div>`).join('')+`</div>`
      : `<div class="notice">暂无待处理事项。</div>`;
  }catch(e){box.innerHTML=`<div class="notice warn">待处理事项加载失败：${esc(e.message)}</div>`}
}
async function loadClubs(){try{skel('#clubRows',4);let [a,cr]=await Promise.all([api('/api/platform/clubs'),api('/api/platform/commission-rates').catch(()=>null)]);
  const rateMap={};((cr&&cr.clubs)||[]).forEach(x=>rateMap[x.clubId]=x);
  const defPct=Number((cr&&cr.productDefaultRatePercent)||8);$('#clubRows').innerHTML=a.map(x=>`<tr><td><div class="plat-club-cell"><span class="plat-club-logo">${clubLogoHtml(x)}</span><span><strong>${esc(x.name)}</strong><div class="sub">${esc(x.city||'')} ${esc(x.contact_name||'')} ${esc(x.contact_phone||'')}</div></span></div></td><td>${esc(x.subscription_plan||x.plan||'—')}<div class="sub">${esc(enumCn(x.subscription_status,'未开通'))}</div></td><td><span class="tag ${x.status==='pending'||x.status==='disabled'?'orange':''}">${esc(enumCn(x.status))}</span></td><td>${(()=>{const r=rateMap[x.id];if(!r)return '<span class="sub">—</span>';
   const pct=Number(r.ratePercent||0);const own=!!r.hasClubOverride;
   const dis=!r.enabled;
   return `<div class="plat-rate-cell"><strong>${dis?'0':pct}%</strong>${own?'<span class="tag">已单独设定</span>':'<span class="sub">商品默认</span>'}${dis?'<span class="tag orange">已停发</span>':''}${own?`<div class="sub">默认 ${Number(r.productRatePercent||defPct)}%</div>`:''}</div>`})()}</td><td><div style="display:flex;gap:6px;flex-wrap:wrap"><button class="btn ghost sm" onclick="setClubCommissionRate(${x.id})">佣金率</button></div></td><td><div class="plat-credits-cell"><strong>${x.ai_credits}</strong>${Number(x.ai_credit_debt||0)>0?`<div class="sub">欠账 ${x.ai_credit_debt}</div>`:''}<div class="plat-credits-ops"><button class="btn ghost sm" title="扣减 100 Credits" onclick="quickCredits(${x.id},-100)">−100</button><button class="btn ghost sm" title="增加 100 Credits" onclick="quickCredits(${x.id},100)">+100</button><button class="btn ghost sm" onclick="adjust(${x.id})">调整</button></div></div></td><td><div style="display:flex;gap:6px;flex-wrap:wrap"><button class="btn secondary" onclick="clubStatus(${x.id},'${x.status==='active'?'disabled':'active'}')">${x.status==='active'?'停用':'启用'}</button><button class="btn ghost" onclick="assignPlan(${x.id})">套餐</button>${x.status==='pending'?`<button class="btn ghost" onclick="clubStatus(${x.id},'rejected')">拒绝</button>`:''}</div></td></tr>`).join('')}catch(e){loaderError('#clubRows',e,'loadClubs 加载失败')}}

/* 俱乐部 logo：平台端用 /api/platform/clubs/{id}/logo.png 代理（后端已把
   clubs.logo_url 的原始路径换算过来）。没设 logo 的俱乐部不能显示裂图，
   画一个「首字母 + 品牌色块」占位，视觉重量和真 logo 一致。
   裂图兜底走事件委托（platLogoFallback），不用内联 onerror 转义字符串 ——
   内联里要塞俱乐部名，容易被引号/中文引号搞挂。 */
function clubLogoHtml(x){
  const initial=String(x.name||'?').trim().slice(0,1)||'?';
  if(!x.logoUrl)return `<span class="plat-club-logo__ph">${esc(initial)}</span>`;
  return `<img class="plat-club-logo__img" src="${esc(x.logoUrl)}" alt="${esc(x.name||'')}" data-ph="${esc(initial)}" loading="lazy">`;
}
/* 事件委托：一次绑定管全表所有 logo（表格是整块重渲染的，绑到 tbody 上最稳）。 */
function platLogoFallback(e){
  const img=e.target;
  if(!img||img.tagName!=='IMG'||!img.classList.contains('plat-club-logo__img'))return;
  const ph=document.createElement('span');
  ph.className='plat-club-logo__ph';
  ph.textContent=img.getAttribute('data-ph')||'?';
  if(img.parentNode)img.parentNode.replaceChild(ph,img);
}

/* 快捷加减 Credits：平台运营最高频的动作就是「给某个俱乐部补/扣一点额度」，
   每次都开表单太慢。±100 一键出账，任意数额仍走 adjust() 表单。
   扣减超过余额时后端会记成欠账（debtCreated），提示里要写清楚。 */
async function quickCredits(id,amount){
  try{
    let r=await api('/api/platform/credits/adjust',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({clubId:id,amount:Number(amount),note:amount>0?'平台快捷补发':'平台快捷扣减'})});
    loadClubs();loadCredits();
    toast(r.debtCreated?`已${amount>0?'增加':'扣减'}，余额不足部分形成欠账 ${r.debtCreated}`:`Credits 已${amount>0?'增加':'扣减'} ${Math.abs(amount)}`);
  }catch(e){showAlert({title:'调整失败',message:e.message})}
}

async function addClub(){const v=await showForm({title:'新增入驻俱乐部',submitText:'创建',fields:[{name:'name',label:'俱乐部名称',required:true},{name:'contactName',label:'联系人',value:''},{name:'contactPhone',label:'联系电话',value:''},{name:'city',label:'所在城市',value:''}]});if(!v||!v.name)return;await api('/api/platform/clubs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:v.name,contactName:v.contactName||'',contactPhone:v.contactPhone||'',city:v.city||'',applicationNote:'平台新增入驻'})});loadClubs();toast('已创建待审核俱乐部')}
async function clubStatus(id,status){let reason='';if(['disabled','rejected'].includes(status)){const v=await showForm({title:status==='disabled'?'停用俱乐部':'拒绝入驻',desc:'操作会通知俱乐部，请填写原因。',submitText:'确认',danger:true,fields:[{name:'reason',label:status==='disabled'?'停用原因':'拒绝原因',type:'textarea',value:'',required:true}]});if(!v)return;reason=v.reason}await api(`/api/platform/clubs/${id}/status`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status,reason})});loadClubs();dash()}
async function loadCredits(){try{skel('#creditRows',4);let [a,plans,packs,sum,orders,pricing]=await Promise.all([api('/api/platform/credits'),api('/api/platform/ai-credit/plans'),api('/api/platform/ai-credit/topup-packages'),api('/api/platform/ai-credit/summary'),api('/api/platform/ai-credit/orders?limit=100'),api('/api/platform/ai-credit/task-pricing')]);$('#aiBillingMetrics').innerHTML=[['本月AI收入',money(sum.cashRevenueCny)],['Provider成本',money(sum.providerCostCny)],['Credits消耗',sum.creditsConsumed],['未偿欠账',sum.outstandingDebt]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div></div>`).join('');$('#creditRows').innerHTML=a.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(x.plan_code||'—')}</td><td><strong>${x.balance}</strong></td><td>${x.debt||0}</td><td>${x.monthly_quota}</td><td><button class="btn secondary" onclick="adjust(${x.club_id})">调整</button> <button class="btn ghost" onclick="assignPlan(${x.club_id})">套餐</button></td></tr>`).join('');$('#aiPlanList').innerHTML=plans.map(x=>`<div class="plat-plan${x.status==='active'?'':' is-off'}">
      <div class="plat-plan__head"><b>${esc(x.name)}</b><span class="tag">${esc(x.code)}</span>${x.status==='active'?'':'<span class="tag orange">已下架</span>'}</div>
      <div class="plat-plan__credits"><b>${Number(x.monthly_credits||0).toLocaleString()}</b> Credits / 月</div>
      <div class="plat-plan__meta">${Number(x.monthly_fee)>0?money(x.monthly_fee):'定制报价'} · 装备折扣 ${(Number(x.gear_discount||1)*10).toFixed(Number(x.gear_discount||1)*10%1?1:0)} 折</div>
      <div class="plat-plan__meta">${Number(x.gear_points_redeem_percent||0)>0?`装备积分抵用 上限 ${Number(x.gear_points_redeem_percent)}%`:'装备积分抵用 无权益'}</div>
      ${String(x.description||'').trim()?`<div class="plat-plan__desc">${esc(x.description)}</div>`:''}
      <div class="plat-plan__ops"><button class="btn ghost sm" onclick="editPlan('${esc(x.code)}')">编辑</button><button class="btn ghost sm" onclick="togglePlan('${esc(x.code)}','${x.status==='active'?'inactive':'active'}')">${x.status==='active'?'下架':'上架'}</button><button class="btn ghost sm plat-plan__del" onclick="deletePlan('${esc(x.code)}')">删除</button></div>
    </div>`).join('')||'<div class="empty">还没有套餐，点右上角「＋ 新增套餐」。</div>';$('#aiTopupList').innerHTML=packs.map(x=>`<div class="plat-plan plat-plan--pack${x.status==='active'?'':' is-off'}">
      <div class="plat-plan__head"><b>${esc(x.name)}</b>${x.status==='active'?'':'<span class="tag orange">已下架</span>'}</div>
      <div class="plat-plan__credits"><b>${Number(x.credits||0).toLocaleString()}</b> Credits 额度</div>
      <div class="plat-plan__meta">${money(x.amount)}</div>
    </div>`).join('')||'<div class="empty">还没有充值包，点右上角「＋/编辑充值包」。</div>';$('#aiBillingOrders').innerHTML=orders.slice(0,30).map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.club_name)}</div> · ${x.credits} Credits · ${money(x.amount)} <div class="list-row__sub">${x.order_type==='subscription'?`${esc(x.plan_code||'')} · ${esc(x.period_key||'')}`:`充值包 ${esc(x.package_code||'')}`} · ${esc(x.payment_ref||'待付款')}</div>${x.status==='pending'?`<button class="btn secondary" style="margin-top:6px" onclick="confirmAIBill('${x.id}')">确认付款</button>`:''}<div class="list-row__end"><span class="tag ${x.status==='pending'?'orange':''}">${esc(enumCn(x.status))}</span></div></div></div>`).join('')||'<div class="empty">暂无AI账单</div>';$('#aiTaskPricing').innerHTML=Object.entries(pricing).map(([k,v])=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(k)}</div> · ${v} Credits/次</div></div>`).join('')}catch(e){loaderError('#creditRows',e,'loadCredits 加载失败')}}
async function adjust(id){const v=await showForm({title:'调整 AI Credits',submitText:'确认调整',fields:[{name:'amount',label:'增加/扣减 Credits（可输入负数）',type:'number',value:100,required:true},{name:'note',label:'调整原因',value:'平台手工调整'}]});if(!v||!v.amount)return;let r=await api('/api/platform/credits/adjust',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({clubId:id,amount:Number(v.amount),note:v.note||'平台手工调整'})});loadCredits();toast(r.debtCreated?`已调整，余额不足部分形成欠账 ${r.debtCreated}`:'Credits已调整')}
async function confirmAIBill(id){const v=await showForm({title:'确认 AI 账单到账',submitText:'确认入账',fields:[{name:'ref',label:'付款凭证号',value:'AI-'+Date.now(),required:true}]});if(!v)return;let r=await api(`/api/platform/ai-credit/orders/${id}/confirm-paid`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paymentRef:v.ref})});toast(`已入账 ${r.grant?.granted||0} Credits${r.grant?.debtRecovered?`，其中 ${r.grant.debtRecovered} 用于偿还欠账`:''}`);loadCredits();dash()}
async function monthlyRoll(){let r=await api('/api/platform/ai-credit/monthly-roll',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});toast(`已生成/确认 ${r.orders.length} 个本月套餐账单`);loadCredits()}
async function editTopupPackage(){const v=await showForm({title:'编辑充值包',submitText:'保存充值包',fields:[{name:'code',label:'充值包 code（已有 code 会更新）',value:'T1000',required:true},{name:'name',label:'充值包名称',value:'1,000 Credits'},{name:'amount',label:'售价（元）',type:'number',value:99,min:0},{name:'credits',label:'Credits 数量',type:'number',value:1000,min:0}]});if(!v||!String(v.code).trim())return;await api('/api/platform/ai-credit/topup-packages',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:String(v.code).trim(),name:v.name||String(v.code).trim(),amount:Number(v.amount||0),credits:Number(v.credits||0),status:'active'})});toast('充值包已保存');loadCredits()}
async function editTaskPricing(){let p=await api('/api/platform/ai-credit/task-pricing');const keys=Object.keys(p);const v=await showForm({title:'AI 任务计费',desc:'设置每类任务每次消耗的 Credits。',submitText:'保存计费',fields:keys.map(k=>({name:k,label:k+' 每次消耗 Credits',type:'number',value:Number(p[k]),min:0}))});if(!v)return;const body={};for(const k of keys)body[k]=Number(v[k]||0);await api('/api/platform/ai-credit/task-pricing',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});toast('AI任务计费已更新');loadCredits()}
async function loadPointPolicy(){try{skel('#platformPointPolicy',2);skel('#gearPolicyTable',4);let d=await api('/api/platform/points-policy');
$('#platformPointPolicy').innerHTML=`<label style="display:flex;align-items:center;gap:10px"><input id="platformGearActivity" type="checkbox" ${d.gearPointsActivityRedeemEnabled?'checked':''}> <strong>允许 Gear Points 用于活动报名抵扣</strong></label><div class="notice" style="margin-top:12px">装备积分是<strong>总平台出钱</strong>，所以准入权与比例都在平台：俱乐部订阅的套餐决定「有没有这项权益」，下方按俱乐部设定「最多能抵订单金额的百分之几」。俱乐部在活动里不再能自行开关。</div><button class="btn secondary" style="margin-top:12px" onclick="savePointPolicy()">保存平台规则</button>`;
const rows=d.clubs||[];
/* 表格列：俱乐部 / 套餐 / 是否可抵 / 生效比例 / 生效状态 / 操作 ——
   「是否可抵」与「生效比例」分开，是为了让「不可抵」和「0%」在视觉上不混淆：
   前者是资格问题（套餐/平台没给），后者是比例问题（可以抵但比例是 0）。 */
$('#gearPolicyTable').innerHTML=rows.map(x=>{
  const planPct=Number(x.planPercent||0), pct=Number(x.redeemPercent||0);
  const capped=planPct>0&&pct>planPct;
  const canSet=planPct>0;
  return `<tr>
  <td><strong>${esc(x.clubName)}</strong>${x.clubStatus!=='active'?` <span class="tag orange">${esc(x.clubStatus)}</span>`:''}</td>
  <td>${esc(x.planName||x.planCode||'—')}<div class="sub">上限 ${planPct}%</div></td>
  <td>${x.allowed?'<span class="tag">可抵</span>':'<span class="tag orange">不可抵</span>'}</td>
  <td>${x.allowed?`<strong>${pct}%</strong>`:'—'}</td>
  <td>${x.hasClubOverride?'<span class="tag">已单独设定</span>':'<span class="sub">套餐默认</span>'}${capped?`<div class="sub">已达套餐上限 ${planPct}%</div>`:''}${x.reason?`<div class="sub">${esc(x.reason)}</div>`:''}</td>
  <td>${canSet?`<button class="btn ghost" onclick="setClubGearPercent(${x.clubId},${pct},${planPct})">设定比例</button>`:'<span class="sub">套餐无此权益</span>'}${x.hasClubOverride?` <button class="btn ghost" onclick="resetClubGearPercent(${x.clubId})">恢复套餐默认</button>`:''}</td>
  </tr>`;
}).join('')||'<tr><td colspan="6"><div class="empty">还没有俱乐部</div></td></tr>';
}catch(e){loaderError('#platformPointPolicy',e,'loadPointPolicy 加载失败');loaderError('#gearPolicyTable',e,'loadPointPolicy 加载失败')}}
async function savePointPolicy(){let enabled=$('#platformGearActivity').checked;await api('/api/platform/points-policy',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({gearPointsActivityRedeemEnabled:enabled})});toast('平台 Gear Points 活动补贴规则已保存');loadPointPolicy()}
/* 按俱乐部设定装备积分抵用比例。套餐自带比例是权益上限，
   这里只能在这个额度内分配 —— 超过会被自动夹到套餐上限（后端 min 处理）。 */
async function setClubGearPercent(clubId,current,planPercent){
  const cap=Number(planPercent||0);
  if(cap<=0){showAlert({title:'无法设定',message:'该俱乐部的套餐不包含装备积分抵用权益，平台不能凭空发放。请先给它配置具备该权益的套餐。'});return}
  const v=await showForm({title:'设定装备积分抵用比例',desc:`比例口径：单笔最多可抵订单金额的百分之几。该俱乐部套餐上限 ${cap}%（实际生效取两者较小值）。填 0 表示关闭该俱乐部的装备积分抵用。`,submitText:'保存',fields:[{name:'percent',label:'抵扣比例（%）',type:'number',value:Number(current||0),min:0,max:cap,required:true},{name:'note',label:'备注（仅平台内部可见）',value:''}]});
  if(!v||v.percent===undefined||v.percent===null)return;
  const r=await api(`/api/platform/points-policy/clubs/${clubId}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({redeemPercent:Number(v.percent),enabled:Number(v.percent)>0,note:v.note||''})});
  toast(Number(v.percent)>0?`已设定为 ${r.redeemPercent}%`:'已关闭该俱乐部的装备积分抵用');
  loadPointPolicy();
}
async function resetClubGearPercent(clubId){await api(`/api/platform/points-policy/clubs/${clubId}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({clearOverride:true})});toast('已恢复为套餐默认比例');loadPointPolicy()}

async function loadPlatformBenefits(){try{skel('#platformBenefits',3);let a=await api('/api/platform/benefits');$('#platformBenefits').innerHTML=a.map(x=>`<div class="card"><div class="panel-title"><strong>${esc(x.title)}</strong><span class="tag">Gear Points</span></div><div class="sub">${esc(x.description||'')}<br>${x.points_cost} 装备积分 · ${x.benefit_type==='activity_coupon'?'活动报名补贴':'装备商城抵扣'} · 平台承担成本${x.cash_value?` · 权益价值 ${money(x.cash_value)}`:''} · ${x.target_club_id?`仅俱乐部 #${x.target_club_id}`:'全部俱乐部'}</div><div style="margin-top:10px"><span class="tag ${x.status==='active'?'':'orange'}">${esc(enumCn(x.status))}</span></div></div>`).join('')||'<div class="empty">暂无平台福利</div>'}catch(e){loaderError('#platformBenefits',e,'loadPlatformBenefits 加载失败')}}
async function addPlatformBenefit(){const v=await showForm({title:'新增平台福利',submitText:'创建福利',fields:[{name:'kind',label:'福利用途',type:'select',value:'gear',options:[{value:'gear',label:'装备商城抵扣'},{value:'activity',label:'活动报名补贴'}]},{name:'title',label:'平台福利名称',value:'装备商城 ¥30 抵扣福利',required:true},{name:'cost',label:'需要多少装备积分',type:'number',value:3000,min:0},{name:'value',label:'权益价值（元）',type:'number',value:30,min:0}]});if(!v||!v.title)return;const kind=v.kind;await api('/api/platform/benefits',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:v.title,description:'ClubOS 平台会员福利',pointsType:'gear',pointsCost:Number(v.cost||0),cashValue:Number(v.value||0),benefitType:kind==='gear'?'gear_coupon':'activity_coupon',stock:200})});toast('平台福利已创建');loadPlatformBenefits()}
/* ── 装备商品中心（2026-10-24 多品牌化）───────────────────────────────────
   维度进字典表：品牌 / 品类是 product_brands / product_categories，商品存外键；
   颜色 / 尺码下沉到规格层（product_variants）。SKU 由后端按
   「品牌-品类-商品-颜色-尺码」自动生成，前端不手拼 —— 手填在多品牌下必然撞号，
   而撞号在采购入库和库存流水里是查不出的数据事故。 */
const PROD_GENDERS=[{value:'male',label:'男款'},{value:'female',label:'女款'},{value:'unisex',label:'中性'},{value:'kids',label:'儿童'}];
let PROD_DICTS={brands:[],categories:[],loaded:false};
async function loadProductDicts(force){
  if(PROD_DICTS.loaded&&!force)return PROD_DICTS;
  const [bs,cs]=await Promise.all([api('/api/platform/brands'),api('/api/platform/product-categories')]);
  PROD_DICTS={brands:bs||[],categories:cs||[],loaded:true};
  paintProductFilters();
  return PROD_DICTS;
}
/* 品类下拉压平显示（父 / 子）：showForm/uxForm 的 select 只吃扁平 options，
   层级用文字前缀表达，比为了一个下拉去改表单组件小得多。 */
function prodCatLabel(c){const p=(PROD_DICTS.categories||[]).find(x=>Number(x.id)===Number(c.parent_id||c.parentId));return p?p.name+' / '+c.name:c.name}
function prodCatOptions(sel){return (PROD_DICTS.categories||[]).filter(c=>c.status!=='inactive'||Number(c.id)===Number(sel)).map(c=>({value:c.id,label:prodCatLabel(c)}))}
function prodBrandOptions(sel){return (PROD_DICTS.brands||[]).filter(b=>b.status!=='inactive'||Number(b.id)===Number(sel)).map(b=>({value:b.id,label:b.name}))}
function paintProductFilters(){
  const bs=$('#prodFilterBrand');
  if(bs){const keep=bs.value;bs.innerHTML='<option value="">全部品牌</option>'+prodBrandOptions().map(b=>`<option value="${b.value}">${esc(b.label)}</option>`).join('');if(keep)bs.value=keep}
  const cs=$('#prodFilterCat');
  if(cs){const keep=cs.value;cs.innerHTML='<option value="">全部品类</option>'+prodCatOptions().map(c=>`<option value="${c.value}">${esc(c.label)}</option>`).join('');if(keep)cs.value=keep}
}
/* 筛选走后端 SQL：商品池是全局的，多品牌之后会有几百个 SKU，
   全量拉回来再筛等于每次进商城都把整个商品池拖到浏览器里。 */
async function applyProductFilter(){
  const qs=new URLSearchParams();
  const b=$('#prodFilterBrand')?.value||'',c=$('#prodFilterCat')?.value||'',g=$('#prodFilterGender')?.value||'',kw=($('#prodFilterQ')?.value||'').trim();
  if(b)qs.set('brand_id',b);
  if(c)qs.set('category_id',c);
  if(g)qs.set('gender',g);
  if(kw)qs.set('q',kw);
  const q=qs.toString();
  try{
    skel('#productRows',4);
    const a=await api('/api/platform/products'+(q?'?'+q:''));
    $('#productRows').innerHTML=a.map(prodRowHtml).join('')||'<tr><td colspan="10" class="empty">没有符合筛选条件的商品</td></tr>';
    const hit=$('#prodFilterHit');if(hit)hit.textContent=a.length?a.length+' 件商品':'';
  }catch(e){loaderError('#productRows',e,'商品筛选失败')}
}
function clearProductFilter(){['#prodFilterBrand','#prodFilterCat','#prodFilterGender','#prodFilterQ'].forEach(s=>{const el=$(s);if(el)el.value=''});applyProductFilter()}
function prodRowHtml(x){
  const brand=x.brand_name?esc(x.brand_name):'<span class="sub">未设品牌</span>';
  const cat=x.category_name?esc(x.category_name)+(x.parent_category_name?`<div class="sub">${esc(x.parent_category_name)}</div>`:''):'<span class="sub">未分类</span>';
  return `<tr><td><strong>${esc(x.name)}</strong><div class="sub">${esc(x.gender?PROD_GENDERS.find(g=>g.value===x.gender)?.label||x.gender:'')} · 规格 ${x.variants_count||0} · 图 ${x.images_count||0} · ${x.description?'有介绍':'无介绍'}</div></td><td>${brand}<div class="sub">${cat}</div></td><td>${esc(x.sku)}</td><td>${money(x.price)}</td><td>${money(x.average_cost||0)}</td><td>${x.stock}</td><td>${x.reorder_point||0}</td><td><span class="tag ${x.status==='active'?'':'orange'}">${esc(enumCn(x.status))}</span></td><td>${Math.round(x.commission_rate*100)}%</td><td><button class="btn secondary" onclick="toggleProduct(${x.id},'${x.status}')">${x.status==='active'?'下架':'上架'}</button> <button class="btn ghost" onclick="editProduct(${x.id})">编辑</button> <button class="btn ghost" onclick="linkSupplier(${x.id})">供货商</button> <button class="btn ghost" onclick="manageVariants(${x.id})">规格</button> <button class="btn ghost" onclick="manageImages(${x.id})">图片</button></td></tr>`;
}
async function loadProducts(){try{await loadProductDicts()}catch(e){/* 字典拉不到也要让列表自己报错，别静默 */}applyProductFilter()}
async function addProduct(){return platformAddProduct()}
/* 新商品上架：六维 + 颜色×尺码矩阵，分两步 —— 先填商品信息，再配规格矩阵。
   ★ 真身叫 platformAddProduct 而不是 addProduct：workflows-platform.js 后加载，
     它的 window.addProduct 会覆盖本函数；那个覆盖壳要能无损调到这里，
     两个名字必须分开，否则覆盖壳调 addProduct 就是调它自己（无限递归）。
   ★ 矩阵弹层用 uxDialog 不用 uxForm：uxForm 只认 text/number/select/textarea/datetime，
     没有「可增删的颜色行」这种卡片式 body（memory 雷区 ⑧）。
   ★ 款号留空时系统生成 GEAR-<时间戳>：它是 CSV 导入的归组键，运营认自己的款号。 */
async function platformAddProduct(){
  await loadProductDicts();
  const v=await uxForm({title:'新建装备商品',subtitle:'统一商品池由平台持有；品牌 / 品类 / 人群 / 季节 / 系列 / 面料在这里一次配好，SKU 由系统自动生成。',hint:'不配矩阵则只建一个「默认」规格承接期初库存，C 端照常可买。',submitText:'下一步：配规格',wide:true,fields:[
    {name:'name',label:'商品名称',full:true,required:true,placeholder:'例如：Beta LT 冲锋衣'},
    {name:'styleNo',label:'款号 / 货号',placeholder:'留空自动生成（GEAR-…）',help:'CSV 导入按货号归组，建议填品牌方给的款号'},
    {name:'brandId',label:'品牌',type:'select',options:[{value:'',label:'不设品牌'},...prodBrandOptions()]},
    {name:'categoryId',label:'品类',type:'select',options:[{value:'',label:'不分类'},...prodCatOptions()]},
    {name:'gender',label:'人群',type:'select',value:'unisex',options:PROD_GENDERS},
    {name:'season',label:'季节',placeholder:'例如 2026 秋冬',help:'留空表示常年款'},
    {name:'series',label:'系列',placeholder:'例如 Beta 系列'},
    {name:'material',label:'面料 / 材质',placeholder:'例如 GORE-TEX 3L'},
    {name:'price',label:'零售价（元）',type:'number',min:0,step:0.01,required:true},
    {name:'commissionRatePct',label:'来源俱乐部佣金率（%）',type:'number',min:0,max:100,step:0.1,value:8,required:true},
    {name:'stock',label:'期初可售库存',type:'number',min:0,step:1,value:0,required:true,help:'落在第一个颜色×尺码组合上；后续补货请走采购入库，不要直接改库存'},
    {name:'description',label:'商品介绍',type:'textarea',full:true,placeholder:'C 端详情页展示的图文介绍；留空则详情页不显示该区块'}
  ]});
  if(!v||!v.name)return;
  const mx=await prodMatrixDialog({});
  if(mx===null)return;
  const body={name:v.name,sku:(v.styleNo||'').trim()||('GEAR-'+Date.now()),price:Number(v.price||0),
    stock:Number(v.stock||0),commission_rate:Number(v.commissionRatePct||8)/100,
    gender:v.gender||'unisex',description:v.description||''};
  if(v.brandId)body.brandId=Number(v.brandId);
  if(v.categoryId)body.categoryId=Number(v.categoryId);
  if((v.season||'').trim())body.season=v.season.trim();
  if((v.series||'').trim())body.series=v.series.trim();
  if((v.material||'').trim())body.material=v.material.trim();
  if(mx&&(mx.colors.length||mx.sizes.length)){body.colors=mx.colors;body.sizes=mx.sizes}
  try{
    const r=await api('/api/platform/products',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    toast('商品已上架'+(r&&r.variantsCreated?` · ${r.variantsCreated} 个规格`:''));
    loadProducts();
  }catch(e){showAlert({title:'上架失败',message:e.message})}
}
/* ── 规格矩阵弹层 ─────────────────────────────────────────────────────────
   返回 {colors:[{name,hex}],sizes:[…]}（确定）/ {colors:[],sizes:[]}（跳过矩阵）/ null（取消）。
   颜色是动态行（名称 + 色值 + 删除），尺码是逗号分隔单行 —— 尺码本来就是
   S/M/L 这种短词，做成动态行的交互成本远高于收益。 */
async function prodMatrixDialog(initial){
  initial=initial||{};
  let colors=(initial.colors||[]).map(c=>({name:c.name||'',hex:c.hex||''}));
  /* resolve 提到外层：onClose（× / Esc / 点遮罩）在弹窗创建时就要能调到它。 */
  let _resolve=null;
  const dlg=uxDialog({title:initial.title||'规格矩阵',desc:initial.desc||'按 颜色 × 尺码 一次生成所有 SKU 组合；已存在的组合会跳过，不会重置已调好的库存与条码。',wide:true,
    onClose:()=>_resolve&&_resolve(null),
    body:'<div class="plat-mx">'
      +'<div><div class="plat-mx__label">颜色 <em>可留空；色值用于 C 端色块</em></div><div class="plat-mx__chips" id="mxColors"></div>'
      +'<div style="margin-top:8px"><button class="btn ghost plat-mx__add" type="button" id="mxAddColor">＋ 添加颜色</button></div></div>'
      +'<div><div class="plat-mx__label">尺码 <em>逗号分隔，例如 S,M,L,XL 或 38,39,40,41</em></div>'
      +'<input class="plat-mx__sizes" id="mxSizes" placeholder="S, M, L, XL" value="'+esc((initial.sizes||[]).join(', '))+'"></div>'
      +'<div class="plat-mx__preview" id="mxPreview"></div>'
      +'<div class="plat-mx__hint">库存真源在规格层：每个组合都是独立 SKU，期初库存落在第一个组合上。<br>不配任何颜色 / 尺码时，系统只建一个「默认」规格，C 端照常可买。</div>'
      +'</div>',
    foot:'<button class="btn ghost" type="button" data-skip>不配矩阵</button><button class="btn" type="button" data-ok>生成矩阵</button>'});
  const chip=i=>'<span class="plat-mx__chip" data-i="'+i+'"><span class="plat-mx__swatch" style="background:'+(colors[i].hex||'#dfe5e0')+'"></span>'
    +'<input class="plat-mx__chipname" type="text" value="'+esc(colors[i].name)+'" placeholder="颜色名" style="width:88px;border:0;background:none;font-size:12.5px;font-weight:700">'
    +'<input type="color" value="'+(colors[i].hex||'#dfe5e0')+'" aria-label="色值">'
    +'<button type="button" aria-label="删除该颜色">×</button></span>';
  const paint=()=>{
    dlg.querySelector('#mxColors').innerHTML=colors.map((_,i)=>chip(i)).join('');
    const sizes=parseSizes();
    const n=Math.max(1,colors.length)*Math.max(1,sizes.length);
    dlg.querySelector('#mxPreview').textContent=colors.length&&sizes.length
      ?('将生成 '+colors.length+' 色 × '+sizes.length+' 码 = '+n+' 个 SKU：'+colors.slice(0,3).map(c=>c.name||'未命名').join(' / ')+(colors.length>3?' 等':'')+' × '+sizes.slice(0,6).join(',')+(sizes.length>6?' 等':''))
      :(colors.length||sizes.length)?'将生成 '+n+' 个 SKU':'未配置矩阵：只建一个「默认」规格';
  };
  const parseSizes=()=>(dlg.querySelector('#mxSizes').value||'').split(/[,，]/).map(s=>s.trim()).filter(Boolean);
  dlg.querySelector('#mxColors').addEventListener('click',e=>{
    const del=e.target.closest('button[aria-label="删除该颜色"]');
    if(del){const i=Number(del.closest('.plat-mx__chip').dataset.i);colors.splice(i,1);paint();return}
  });
  dlg.querySelector('#mxColors').addEventListener('input',e=>{
    const chipEl=e.target.closest('.plat-mx__chip');if(!chipEl)return;
    const i=Number(chipEl.dataset.i);
    if(e.target.classList.contains('plat-mx__chipname'))colors[i].name=e.target.value.trim();
    if(e.target.type==='color')colors[i].hex=e.target.value;
    paint();
  });
  dlg.querySelector('#mxAddColor').onclick=()=>{colors.push({name:'',hex:''});paint();dlg.querySelector('#mxColors').lastElementChild?.querySelector('.plat-mx__chipname')?.focus()};
  dlg.querySelector('#mxSizes').addEventListener('input',paint);
  paint();
  return await new Promise(resolve=>{
    _resolve=resolve;
    let done=false;
    const fin=v=>{if(done)return;done=true;resolve(v)};
    dlg.querySelector('[data-skip]').onclick=()=>{fin({colors:[],sizes:[]});dlg.uxClose()};
    dlg.querySelector('[data-ok]').onclick=()=>{
      const sizes=parseSizes();
      const named=colors.filter(c=>c.name);
      if(!named.length&&!sizes.length){fin({colors:[],sizes:[]});dlg.uxClose();return}
      if(colors.length&&!named.length){toast('颜色还没有填名称');return}
      fin({colors:named.map(c=>({name:c.name,hex:c.hex||''})),sizes});
      dlg.uxClose();
    };
  });
}
/* 商品描述单独可改：C 端详情页的「商品介绍」读的就是 products.description，
   上架后补文案不需要动价格库存，也不该触发 commerce 重同步以外的噪音。
   六维（品牌 / 品类 / 人群 / 季节 / 系列 / 面料）在这里一起改：品类同时回写
   category 文本列，C 端副标题、Medusa 同步、老统计都还在读它。 */
async function editProduct(id){
  await loadProductDicts();
  const ps=await api('/api/platform/products');
  const p=(ps||[]).find(x=>Number(x.id)===Number(id));if(!p)return;
  const v=await uxForm({title:'编辑商品',subtitle:'商品由总平台统一上架、定价与库存；改描述不影响在售状态。颜色与尺码请在「规格」里维护。',submitText:'保存',wide:true,fields:[
    {name:'name',label:'商品名',value:p.name,required:true},
    {name:'price',label:'价格',type:'number',value:p.price,min:0,required:true},
    {name:'brandId',label:'品牌',type:'select',value:p.brand_id||'',options:[{value:'',label:'不设品牌'},...prodBrandOptions(p.brand_id)]},
    {name:'categoryId',label:'品类',type:'select',value:p.category_id||'',options:[{value:'',label:'不分类'},...prodCatOptions(p.category_id)]},
    {name:'gender',label:'人群',type:'select',value:p.gender||'unisex',options:PROD_GENDERS},
    {name:'season',label:'季节',value:p.season||'',placeholder:'例如 2026 秋冬',help:'留空表示常年款'},
    {name:'series',label:'系列',value:p.series||'',placeholder:'例如 Beta 系列'},
    {name:'material',label:'面料 / 材质',value:p.material||'',placeholder:'例如 GORE-TEX 3L'},
    {name:'description',label:'商品介绍',type:'textarea',full:true,value:p.description||'',placeholder:'留空则 C 端详情页不显示「商品介绍」区块'}
  ]});
  if(!v)return;
  /* category 文本列不从前端发：后端在收到 category_id 时会按所选品类自动回写，
     两边永远一致，不需要前端再算一份（也算不对 —— 前端只有压平标签）。 */
  const body={name:v.name,price:Number(v.price||0),description:v.description||'',gender:v.gender||'unisex',
    brand_id:v.brandId?Number(v.brandId):null,category_id:v.categoryId?Number(v.categoryId):null,
    season:(v.season||'').trim()||null,series:(v.series||'').trim()||null,material:(v.material||'').trim()||null};
  await api('/api/platform/products/'+id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  loadProducts();toast('商品已更新');
}
async function toggleProduct(id,status){await api(`/api/platform/products/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:status==='active'?'inactive':'active'})});loadProducts()}
/* 俱乐部提交的上下架申请。平台自己有 toggleProduct 直接改，但俱乐部没有这个权限，
   只能走申请；批准即写 products.status，C 端商城的可见性才跟着变。 */
async function loadVisibilityRequests(){try{skel('#visibilityRequestList',3);let list=await api('/api/platform/product-visibility-requests');$('#visibilityRequestList').innerHTML=list.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.club_name)} · ${esc(x.product_name)} <span class="tag ${x.action==='on'?'':'orange'}">${x.action==='on'?'申请上架':'申请下架'}</span></div><div class="list-row__sub">当前 ${esc(enumCn(x.product_status))} · ${esc(x.note||'')} · 提交于 ${esc(x.created_at||'')}${x.decided_note?' · 处理意见 '+esc(x.decided_note):''}</div></div><div class="list-row__end">${x.status==='pending'?`<button class="btn secondary" onclick="decideVisibility(${x.id},'approved')">批准</button> <button class="btn ghost" onclick="decideVisibility(${x.id},'rejected')">驳回</button>`:`<span class="tag">${x.status==='approved'?'已批准':'已驳回'}</span>`}</div></div>`).join('')||'<div class="empty">暂无上下架申请</div>'}catch(e){loaderError('#visibilityRequestList',e,'loadVisibilityRequests 加载失败')}}
async function decideVisibility(id,decision){if(!(await showConfirm({title:decision==='approved'?'批准上下架申请':'驳回上下架申请',message:decision==='approved'?'批准后会立即写入商品状态，C 端商城可见性随之变化。':'驳回后俱乐部会看到处理结果，商品状态不变。',confirmText:decision==='approved'?'确认批准':'确认驳回'})))return;try{await api(`/api/platform/product-visibility-requests/${id}/decide`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision})});toast(decision==='approved'?'已批准，商品状态已更新':'已驳回');await loadVisibilityRequests()}catch(e){showAlert({title:'处理失败',message:e.message})}}
async function loadOrders(){try{skel('#orderRows',4);let a=await api('/api/platform/orders');$('#orderRows').innerHTML=a.map(x=>`<tr><td>#${x.id}</td><td>${esc(x.source_club)}</td><td>${esc(x.buyer)}</td><td>${x.shipping_consignee?esc(x.shipping_consignee)+(x.shipping_phone?' '+esc(x.shipping_phone):'')+(x.shipping_address?'<div class="sub">'+esc(x.shipping_address)+'</div>':''):'<span class="sub">历史订单无地址</span>'}</td><td>${money(x.total)}</td><td><span class="tag ${x.status==='paid'?'orange':''}">${esc(enumCn(x.status))}</span></td><td>${esc(x.carrier||'')} ${esc(x.tracking_no||'待发货')}</td><td>${x.after_sales_status||'无'}</td><td><div style="display:flex;gap:6px;flex-wrap:wrap">${x.packed?'<span class="tag">已打包</span>':`<button class="btn ghost" onclick="packOrder(${x.id})">完成打包</button>`}<button class="btn secondary" onclick="ship(${x.id})">更新物流</button>${x.status==='shipped'?`<button class="btn ghost" onclick="markDelivered(${x.id})">标记签收</button>`:''}${x.status!=='refunded'?`<button class="btn ghost" onclick="refundOrder(${x.id})">全额退款</button>`:''}</div></td></tr>`).join('')||'<tr><td colspan="9" class="empty">暂无订单</td></tr>'}catch(e){loaderError('#orderRows',e,'loadOrders 加载失败')}}
async function ship(id){const v=await showForm({title:'填写物流信息',submitText:'确认发货',fields:[{name:'no',label:'物流单号',value:'SF'+Date.now(),required:true},{name:'carrier',label:'承运商',value:'顺丰'}]});if(!v)return;await api(`/api/platform/orders/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:'shipped',carrier:v.carrier||'顺丰',tracking_no:v.no})});loadOrders();toast('物流已更新')}
async function markDelivered(id){if(!(await showConfirm({title:'标记签收',message:'确认买家已签收？签收后进入售后保障期，佣金冻结。',confirmText:'确认签收'})))return;try{await api(`/api/platform/orders/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:'delivered'})});toast('已标记签收，佣金冻结');loadOrders()}catch(e){showAlert({title:'操作失败',message:e.message})}}
async function refundOrder(id){if(!(await showConfirm({title:'确认全额退款',message:'确认全额退款？系统会同步冲回 Gear Points、俱乐部佣金和商城奖励 AI Credits。',confirmText:'确认退款',danger:true})))return;try{let r=await api(`/api/platform/orders/${id}/refund`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason:'平台确认全额退款'})});toast(`订单已退款 · 佣金冲回 ${money(r.commissionReversed||0)}`);loadOrders();loadCommissions();dash()}catch(e){showAlert({title:'退款失败',message:e.message})}}
let platformCommissionRange={from:'',to:''};
function platFmtUtc8(ts){ /* DB 存 UTC；时间戳 +8h 后用 UTC 字段读（★别用 setHours(getHours()+8)，GMT+8 浏览器双重偏移） */
  if(!ts)return '';
  const d=new Date(String(ts).replace(' ','T')+'Z');
  if(isNaN(d))return String(ts).slice(0,16);
  const l=new Date(d.getTime()+8*3600e3);
  const p=n=>String(n).padStart(2,'0');
  return `${p(l.getUTCMonth()+1)}-${p(l.getUTCDate())} ${p(l.getUTCHours())}:${p(l.getUTCMinutes())}`;
}
function platLocalDay(off){const d=new Date(Date.now()+(off||0)*86400e3+8*3600e3);const p=n=>String(n).padStart(2,'0');return `${d.getUTCFullYear()}-${p(d.getUTCMonth()+1)}-${p(d.getUTCDate())}`;}
/* 快捷时间段的选中态：只有点了「自选日期」或手动改日期时才清空，
   否则用户看不出当前到底筛了哪一段（原生 date 输入空值时也会显示灰色 YYYY/MM/DD，很容易误读成"已筛选"）。 */
function platSyncRangeBtns(active){
  document.querySelectorAll('#platRangeBtns .plat-range__btn[data-range]').forEach(b=>{
    b.classList.toggle('is-on', b.getAttribute('data-range')===active);
  });
}
function toggleCommissionCustom(){
  const box=$('#platCommCustom'); if(!box)return;
  const open=box.hasAttribute('hidden');
  if(open)box.removeAttribute('hidden'); else box.setAttribute('hidden','');
  const tg=$('#platRangeToggle'); if(tg)tg.classList.toggle('is-on',open);
  if(open)$('#platCommFrom').focus();
}
function setCommissionRange(preset){
  const t=platLocalDay(0);
  if(preset==='today')platformCommissionRange={from:t,to:t};
  else if(preset==='7d')platformCommissionRange={from:platLocalDay(-6),to:t};
  else if(preset==='month')platformCommissionRange={from:t.slice(0,8)+'01',to:t};
  else platformCommissionRange={from:'',to:''};
  $('#platCommFrom').value=platformCommissionRange.from;$('#platCommTo').value=platformCommissionRange.to;
  platSyncRangeBtns(preset);
  const tg=$('#platRangeToggle'); if(tg)tg.classList.remove('is-on');
  loadCommissions();
}
function applyCommissionRange(){
  platformCommissionRange={from:$('#platCommFrom').value||'',to:$('#platCommTo').value||''};
  platSyncRangeBtns('');
  const tg=$('#platRangeToggle'); if(tg)tg.classList.add('is-on');
  loadCommissions();
}
function clearCommissionRange(){
  $('#platCommFrom').value='';$('#platCommTo').value='';
  const box=$('#platCommCustom'); if(box)box.setAttribute('hidden','');
  const tg=$('#platRangeToggle'); if(tg)tg.classList.remove('is-on');
  setCommissionRange('all');
}
/* 佣金状态：pending 在通用枚举里叫「待处理」，放到结算语境下太含糊 ——
   运营要一眼分清「还没到可结算」「冻结中」「现在能打款」「已经打过款」。 */
function platLedgerStatus(st){
  const m={pending:'待结算',frozen:'冻结中',available:'可结算',settled:'已结算'};
  return m[String(st||'').toLowerCase()]||enumCn(st);
}
function platLedgerRow(x){
  const rev=String(x.ledger_type||'')==='refund_reverse';
  const st=String(x.status||'').toLowerCase();
  const stCls=(st==='pending'||st==='frozen')?'orange':(st==='available'?'blue':'');
  const abs=Math.abs(Number(x.amount||0));
  return `<tr>`+
    `<td class="plat-ledger__time">${platFmtUtc8(x.created_at)||'—'}</td>`+
    `<td class="plat-ledger__club">${esc(x.club_name||('俱乐部 '+x.club_id))}</td>`+
    `<td class="plat-ledger__order">#${esc(String(x.order_id==null?'':x.order_id))}</td>`+
    `<td><span class="tag ${rev?'red':''}">${rev?'退款冲回':'销售佣金'}</span></td>`+
    `<td class="plat-ledger__amt ${rev?'is-rev':'is-earn'}">${rev?'−':'+'}${money(abs)}</td>`+
    `<td><span class="tag ${stCls}">${platLedgerStatus(st)}</span></td>`+
    `<td class="plat-ledger__time">${x.available_at?platFmtUtc8(x.available_at):'—'}${x.settlement_id?` · ${esc(x.settlement_id)}`:''}</td>`+
  `</tr>`;
}
let platCommRows=[];
/* 这张表自己管搜索与计数（表格带 data-ux-skip，退出 experience-core 的通用增强）：
   通用增强把筛选条插在表格容器外面，一换日期范围表格重渲染，筛选条就变成
   一条带着旧计数、点了没反应的死控件。 */
function platRenderComm(){
  const box=$('#commissionList'); if(!box)return;
  const input=$('#platCommSearch');
  const q=(input&&input.value||'').trim().toLowerCase();
  const all=platCommRows;
  const list=q?all.filter(x=>[x.club_name,'#'+x.order_id,'销售佣金','退款冲回',platLedgerStatus(x.status),String(x.amount),platFmtUtc8(x.created_at)].join(' ').toLowerCase().includes(q)):all;
  const hit=$('#platCommHit');
  if(hit)hit.textContent=q?`匹配 ${list.length} / ${all.length} 笔`:`共 ${all.length} 笔`;
  if(!list.length){
    const ranged=!!(platformCommissionRange.from||platformCommissionRange.to);
    box.innerHTML=`<div class="plat-ledger__empty"><b>${q?'没有匹配的记录':(ranged?'这个时间段没有佣金流水':'还没有佣金流水')}</b><span>${q?'换个关键词试试，或清空搜索框。':(ranged?'换个时间段试试，或直接看全部记录。':'成交后系统自动入账，不需要手工录入。')}</span>${q?'<button type="button" class="btn ghost sm" onclick="clearCommissionSearch()">清空搜索</button>':(ranged?'<button type="button" class="btn ghost sm" onclick="setCommissionRange(\'all\')">查看全部</button>':'')}</div>`;
    return;
  }
  box.innerHTML=`<table class="table plat-ledger" data-ux-skip="1"><thead><tr><th>时间</th><th>俱乐部</th><th>订单</th><th>类型</th><th class="plat-ledger__thnum">佣金金额</th><th>状态</th><th>可结算 / 结算单</th></tr></thead><tbody>${list.map(platLedgerRow).join('')}</tbody></table>`;
}
function filterCommissionRows(){platRenderComm()}
function clearCommissionSearch(){const el=$('#platCommSearch');if(el){el.value='';platRenderComm();el.focus()}}
async function loadCommissions(){try{skel('#commissionList',4);
  const q=new URLSearchParams();
  if(platformCommissionRange.from)q.set('date_from',platformCommissionRange.from);
  if(platformCommissionRange.to)q.set('date_to',platformCommissionRange.to);
  let [a,sum,sett,policy]=await Promise.all([api('/api/platform/commissions'+(q.toString()?'?'+q.toString():'')),api('/api/platform/commission-summary'),api('/api/platform/settlements'),api('/api/platform/commission-policy')]);
  const ranged=platformCommissionRange.from||platformCommissionRange.to;$('#commissionSummary').innerHTML=sum.map(x=>`<div class="stat-tile"><div class="k">${esc(x.clubName||('俱乐部 '+x.clubId))}</div><div class="v">${money(x.payableNow)}</div><div class="hint">可打款 · 冻结 ${money(x.frozen)}${x.carryDebt?` · 待冲抵 ${money(x.carryDebt)}`:''}${x.hasAccount?'':' · <b style="color:var(--orange)">未提供结算账户</b>'}</div><div style="margin-top:10px"><button class="btn secondary" onclick="settleClub(${x.clubId})">结算</button></div></div>`).join('')+`<div class="stat-tile"><div class="k">当前售后冻结期</div><div class="v">${policy.afterSalesDays} 天</div><div class="hint">签收后开始计算</div></div>`;const commSum=a.reduce((s,x)=>s+Number(x.amount||0),0);
  const earnSum=a.filter(x=>String(x.ledger_type||'')!=='refund_reverse').reduce((s2,x)=>s2+Number(x.amount||0),0);
  const revSum=a.filter(x=>String(x.ledger_type||'')==='refund_reverse').reduce((s2,x)=>s2+Math.abs(Number(x.amount||0)),0);
  const pendCnt=a.filter(x=>String(x.status||'').toLowerCase()==='pending').length;
  $('#commissionStat').innerHTML=`<span>销售佣金 <b>${money(earnSum)}</b></span>${revSum?`<span>退款冲回 <b class="is-rev">−${money(revSum)}</b></span>`:''}<span>净额 <b>${money(commSum)}</b></span><span class="is-mut">${ranged?`${platformCommissionRange.from||'最早'} 至 ${platformCommissionRange.to||'最新'}`:'全部时间'}${pendCnt?` · ${pendCnt} 笔待结算`:''}</span>`;
  platCommRows=a;platRenderComm();$('#settlementList').innerHTML=sett.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.club_name)}</div> · 实付 ${money(x.net_amount)} <span class="tag ${x.status==='requested'?'orange':''}">${x.status==='requested'?'俱乐部已申请 · 待打款':esc(enumCn(x.status))}</span><div class="list-row__sub">销售佣金 ${money(x.gross_amount)} · 退款冲抵 ${money(x.deduction_amount)} · ${x.entry_count} 笔 · ${x.payment_ref&&!String(x.payment_ref).startsWith('pending:')?`凭证 ${esc(x.payment_ref)}`:'待打款'}${x.payee?` · 打款至 ${esc(x.payee.holder)} ${esc(x.payee.accountTypeCn)}（${esc(x.payee.cardNoMasked)}）`:' · <b style="color:var(--orange)">俱乐部未登记结算账户</b>'}${x.status==='requested'?` · 申请于 ${esc(String(x.created_at||'').slice(0,16).replace('T',' '))}`:''}</div></div><div class="list-row__end">${x.status==='requested'?`<button class="btn secondary sm" onclick="payoutSettlement('${x.id}')">确认打款</button>`:''}</div></div>`).join('')||'<div class="empty">暂无结算</div>'}catch(e){loaderError('#commissionList',e,'loadCommissions 加载失败')}}async function payoutSettlement(sid){
  const r=await showForm({title:'确认佣金打款',submitText:'确认已打款',desc:'请先按结算单申请金额向俱乐部登记的结算账户完成打款，再填写银行/支付凭证号。确认后该结算单转为已打款，预留佣金转为已结算。',fields:[{name:'ref',label:'银行/支付凭证号',required:true}]});
  if(!r||!String(r.ref).trim())return;
  try{await api(`/api/platform/settlements/${sid}/payout`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paymentRef:String(r.ref).trim()})});toast('打款已确认，结算完成');loadCommissions()}
  catch(e){showAlert({title:'确认失败',message:e.message})}
}async function releaseCommissions(){await api('/api/platform/commissions/release',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});toast('已刷新可结算佣金');loadCommissions()}async function settleClub(clubId){
  let p;
  try{p=await api(`/api/platform/settlements/preview?club_id=${clubId}`)}catch(e){showAlert({title:'加载失败',message:e.message});return}
  /* 2026-10-22 用户反馈：俱乐部结算佣金需要先提供法人卡或对公账户——没账户一律不准结算。 */
  if(!p.account||!p.account.exists){showAlert({title:'俱乐部尚未提供结算账户',message:'结算佣金前，俱乐部必须先在后台「商城 · 结算账户」登记法人卡或对公账户。请通知俱乐部补登后再结算。'});return}
  if(Number(p.netAmount)<=0){showAlert({title:'无正向可结算净额',message:`当前没有正向可结算净额${p.carryDebt?`，待冲抵 ${money(p.carryDebt)}`:''}`});return}
  const acct=p.account;
  const _sv=await showForm({title:'登记佣金结算',submitText:'确认结算',
    desc:`打款账户：${acct.holder} · ${acct.accountTypeCn} · ${acct.bankName}${acct.bankBranch?' · '+acct.bankBranch:''} · 卡号 ${acct.cardNoMasked}。请按此账户打款后填写凭证号。`,
    fields:[{name:'ref',label:'本次应打款 '+money(p.netAmount)+'，银行/支付凭证号',required:true}]});
  if(!_sv)return;let ref=_sv.ref;
  try{await api('/api/platform/settlements',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({clubId,paymentRef:ref,note:'平台佣金结算'})});toast('结算已登记');loadCommissions()}
  catch(e){showAlert({title:'结算失败',message:e.message})}
}async function setCommissionPolicy(){let p=await api('/api/platform/commission-policy');const _cv=await showForm({title:'佣金结算规则',submitText:'保存',fields:[{name:'d',label:'签收后冻结多少天再进入可结算',type:'number',value:p.afterSalesDays,min:0,required:true}]});if(!_cv)return;let d=_cv.d;await api('/api/platform/commission-policy',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({afterSalesDays:Number(d)})});toast('佣金规则已更新');loadCommissions()}
dash();

/* 售后工单详情：列表卡片只给「类型 + 订单号 + 金额」，审核动作又要按状态分别摆在前头，
   单看列表根本判断不出「这张单卡在哪一步、卡了多久、退给谁」。
   后端 GET /api/platform/after-sales/{case_id} 早已就绪，只是前端从没调过 ——
   于是每一张卡都只能原地做操作，看不到单据全貌。这里补一个只读详情弹窗。
   字段一律用 `?? '—'` 兜：不同阶段返回的键不完全一致，缺键不该让整块渲染失败。 */
async function openASDetail(id){
  let d;
  try{ d=await api('/api/platform/after-sales/'+id) }
  catch(e){ showAlert({title:'读取售后单失败',message:e.message}); return }
  const line=(k,v)=>`<div class="row" style="display:flex;justify-content:space-between;border-bottom:1px dashed #d8e3dd;padding:8px 0"><span style="color:#6b7f76">${esc(k)}</span><strong>${esc(v==null||v===''?'—':String(v))}</strong></div>`;
  const items=(d.items||[]).map(i=>`<div class="sub">${esc(i.product_name||i.product_id||'商品')} ×${i.quantity}${i.unit_refund_amount?` · 退 ${money(i.unit_refund_amount)}`:''}</div>`).join('')||'<div class="sub">无商品明细</div>';
  const log=(d.log||d.timeline||[]).map(l=>`<div class="sub">${esc(l.at||l.created_at||'')} · ${esc(l.event||l.action||'')}${l.note?` · ${esc(l.note)}`:''}</div>`).join('')||'';
  const body=`<div style="margin-top:6px">${line('售后单号',d.id)}${line('订单号',d.order_id)}${line('状态',enumCn(d.status))}${line('售后类型',({refund_only:'仅退款',return_refund:'退货退款',exchange:'换货'})[d.case_type]||d.case_type)}${line('俱乐部',d.club_name||d.club_id)}${line('买家',d.user_name||d.user_id)}${line('申请退款',money(d.requested_refund_amount))}${line('批准退款',(['approved','refunded','completed'].includes(String(d.status||'')))?money(d.approved_refund_amount??d.refund_amount):'— 待审批')}${line('创建时间',d.created_at)}${items}</div>${log?`<div style="margin-top:14px"><b class="sub">处理记录</b><div style="margin-top:6px">${log}</div></div>`:''}`;
  uxDialog({title:'售后单详情',desc:'由总平台处理的装备售后单据。',body,initialFocus:'[data-ok]',
    foot:`<button class="btn" type="button" data-ok>关闭</button>`});
}
async function loadAfterSales(){
  skel('#afterSalesList',4);try{let a=await api('/api/platform/after-sales');
  const label={refund_only:'仅退款',return_refund:'退货退款',exchange:'换货'};
  const AS_ACT=['pending_review','return_in_transit','approved_pending_refund','exchange_pending_shipment'];
  $('#afterSalesList').innerHTML=a.map(x=>{
    let actions='';
    if(x.status==='pending_review') actions=`<button class="btn secondary" onclick="approveAS('${x.id}')">同意</button> <button class="btn ghost" onclick="rejectAS('${x.id}')">拒绝</button>`;
    if(x.status==='return_in_transit') actions=`<button class="btn secondary" onclick="receiveAS('${x.id}')">确认收货</button>`;
    if(x.status==='approved_pending_refund') actions=`<button class="btn secondary" onclick="refundAS('${x.id}')">执行退款</button>`;
    if(x.status==='exchange_pending_shipment') actions=`<button class="btn secondary" onclick="exchangeShipAS('${x.id}')">换货发货</button>`;
    return `<div class="card" style="margin-bottom:10px"><div class="panel-title"><div><strong>${label[x.case_type]||x.case_type} · 订单 #${x.order_id}</strong><div class="sub">售后单 ${esc(x.id)} · ${esc(x.reason||'')} · 申请退款 ${money(x.requested_refund_amount||0)}</div></div><span class="tag ${AS_ACT.includes(String(x.status))?'orange':''}">${esc(enumCn(x.status))}</span></div><div class="sub">${(x.items||[]).map(i=>`${esc(i.product_name)} ×${i.quantity}`).join('、')}</div>${x.return_tracking_no?`<div class="sub">退货：${esc(x.return_carrier||'')} ${esc(x.return_tracking_no)}</div>`:''}${x.exchange_tracking_no?`<div class="sub">换货发出：${esc(x.exchange_carrier||'')} ${esc(x.exchange_tracking_no)}</div>`:''}<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">${actions}<button class="btn ghost" type="button" data-as-detail onclick="openASDetail('${x.id}')">详情</button></div></div>`
  }).join('')||'<div class="empty">暂无售后单</div>';
}catch(e){loaderError('#afterSalesList',e,'loadAfterSales 加载失败')}}
async function approveAS(id){const v=await showForm({title:'批准售后',desc:'换货场景可留空退款金额。',submitText:'批准',fields:[{name:'amt',label:'批准退款金额（换货可留空）',type:'number',value:'',min:0}]});if(!v)return;let body={};if(v.amt!==undefined&&v.amt!==null&&v.amt!=='')body.refundAmount=Number(v.amt);await api(`/api/platform/after-sales/${id}/approve`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});toast('售后已审核');loadAfterSales()}
async function rejectAS(id){const v=await showForm({title:'拒绝售后',submitText:'确认拒绝',fields:[{name:'note',label:'拒绝原因',type:'textarea',value:'不符合售后条件',required:true}]});if(!v)return;await api(`/api/platform/after-sales/${id}/reject`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({note:v.note})});toast('已拒绝');loadAfterSales()}
async function receiveAS(id){await api(`/api/platform/after-sales/${id}/receive-return`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({restock:true})});toast('退货已收货入库');loadAfterSales()}
async function refundAS(id){await api(`/api/platform/after-sales/${id}/refund`,{method:'POST'});toast('退款已进入支付渠道/已完成');loadAfterSales();loadOrders();loadCommissions()}
async function exchangeShipAS(id){const v=await showForm({title:'填写换货物流',submitText:'确认发出',fields:[{name:'no',label:'换货新物流单号',value:'SF'+Date.now(),required:true},{name:'carrier',label:'承运商',value:'顺丰'}]});if(!v)return;await api(`/api/platform/after-sales/${id}/exchange-ship`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({carrier:v.carrier||'顺丰',trackingNo:v.no})});toast('换货已发出');loadAfterSales()}


async function loadSupplyChain(){
  skel('#inventorySummary',4);try{let [sum,sups,pos,moves]=await Promise.all([api('/api/platform/inventory/summary'),api('/api/platform/suppliers'),api('/api/platform/procurement/purchase-orders'),api('/api/platform/inventory/movements?limit=80')]);
  $('#inventorySummary').innerHTML=[['库存 SKU',sum.skuCount],['现货件数',sum.units],['库存成本',money(sum.inventoryValue)],['在途采购',`${sum.openPurchaseOrders} 单 · ${money(sum.openPurchaseValue)}`]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div></div>`).join('');
  $('#supplierList').innerHTML=sups.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.name)}</div> <div class="list-row__sub">${esc(x.code)} · 供货商品 ${x.product_count} · 交期 ${x.lead_time_days||0} 天 · 在途 ${x.open_po_count||0} 单</div><button class="btn ghost" style="margin-top:7px" onclick="linkSupplier(null,${x.id})">关联商品</button><div class="list-row__end"><span class="tag ${x.status==='active'?'':'orange'}">${esc(enumCn(x.status))}</span></div></div>`).join('')||'<div class="empty">暂无供应商</div>';
  const st={draft:'草稿',approved:'已审核',ordered:'已下单',partially_received:'部分到货',received:'已完成',cancelled:'已取消'};
  $('#purchaseOrderList').innerHTML=pos.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.id)}</div> · ${esc(x.supplier_name)} <div class="list-row__sub">${money(x.total_amount)} · 到货 ${x.received_qty}/${x.ordered_qty}</div><div style="margin-top:7px">${x.status==='draft'?`<button class="btn ghost" onclick="approvePO('${x.id}')">审核</button> <button class="btn ghost" onclick="cancelPO('${x.id}')">取消</button>`:''}${x.status==='approved'?`<button class="btn ghost" onclick="orderPO('${x.id}')">确认下单</button>`:''}${['ordered','partially_received'].includes(x.status)?`<button class="btn secondary" onclick="receivePO('${x.id}')">到货入库</button>`:''}${Number(x.received_qty||0)>0?`<button class="btn ghost" onclick="createPurchaseReturn('${x.id}')">采购退货</button>`:''}</div><div class="list-row__end"><span class="tag ${['draft','approved','ordered','partially_received'].includes(x.status)?'orange':''}">${st[x.status]||esc(enumCn(x.status))}</span></div></div>`).join('')||'<div class="empty">暂无采购单</div>';
  const mt={opening_balance:'期初库存',purchase_inbound:'采购入库',sale_outbound:'销售出库',refund_restock:'退款回库',after_sales_return:'售后退货',manual_in:'盘盈/调增',manual_out:'盘亏/调减'};
  $('#inventoryMovementList').innerHTML=moves.map(x=>`<div style="display:grid;grid-template-columns:1.3fr .8fr .6fr .8fr 1.5fr;gap:10px;padding:10px 0;border-bottom:1px solid var(--line);align-items:center"><div><strong>${esc(x.product_name)}</strong><div class="sub">${esc(x.product_sku)}</div></div><div>${mt[x.movement_type]||esc(x.movement_type)}</div><div><strong>${x.quantity_delta>0?'+':''}${x.quantity_delta}</strong></div><div>${x.stock_before} → ${x.stock_after}</div><div class="sub">${esc(x.note||'')} · ${esc(x.created_at||'')}</div></div>`).join('')||'<div class="empty">暂无库存流水</div>';
  try{const rets=await api('/api/platform/procurement/purchase-returns');const rst={draft:'草稿',approved:'已审核',shipped:'已出库',credited:'已贷项'};$('#purchaseReturnList').innerHTML=rets.length?rets.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">退货单 ${esc(x.id)} · ${esc(x.supplier_name)}</div> · ${money(x.total_amount||0)}<div class="list-row__sub">${rst[x.status]||esc(enumCn(x.status))}${x.note?` · ${esc(x.note)}`:''} · ${esc(x.created_at||'')}</div></div><div class="list-row__end"><span class="tag ${x.status==='draft'?'orange':''}">${rst[x.status]||esc(enumCn(x.status))}</span></div></div>`).join(''):'<div class="empty">暂无采购退货</div>'}catch(e){$('#purchaseReturnList').innerHTML=`<div class="notice warn">采购退货加载失败：${esc(e.message)}</div>`}
}catch(e){loaderError('#inventorySummary',e,'loadSupplyChain 加载失败')}}
async function addSupplier(){const v=await showForm({title:'新增供应商',submitText:'创建',fields:[{name:'name',label:'供应商名称',required:true},{name:'contact',label:'联系人',value:''},{name:'phone',label:'联系电话',value:''},{name:'lead',label:'默认交期（天）',type:'number',value:7,min:0}]});if(!v||!v.name)return;await api('/api/platform/suppliers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:v.name,contactName:v.contact||'',phone:v.phone||'',leadTimeDays:Number(v.lead||0)})});toast('供应商已创建');loadSupplyChain()}
async function linkSupplier(productId=null,supplierId=null){let ps=await api('/api/platform/products');let ss=await api('/api/platform/suppliers?status=active');if(!ps.length||!ss.length){showAlert({title:'缺少数据',message:'需要先有商品和供应商。'});return}const v=await showForm({title:'建立供货关系',submitText:'保存',fields:[{name:'pid',label:'商品',type:'select',value:String(productId||ps[0].id),options:ps.map(x=>({value:String(x.id),label:x.name}))},{name:'sid',label:'供应商',type:'select',value:String(supplierId||ss[0].id),options:ss.map(x=>({value:String(x.id),label:x.name}))},{name:'price',label:'采购单价',type:'number',value:100,min:0},{name:'moq',label:'最小采购量 MOQ',type:'number',value:1,min:1}]});if(!v)return;const pid=Number(v.pid),sid=Number(v.sid);await api(`/api/platform/products/${pid}/suppliers`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({supplierId:sid,purchasePrice:Number(v.price||0),minOrderQty:Number(v.moq||1),isPrimary:true})});toast('供货关系已保存');loadSupplyChain();if($('#products').classList.contains('active'))loadProducts()}
async function createPO(){let ss=await api('/api/platform/suppliers?status=active'),ps=await api('/api/platform/products');if(!ss.length){showAlert({title:'缺少供应商',message:'请先创建供应商。'});return}const v=await showForm({title:'新建采购单',submitText:'创建采购草稿',fields:[{name:'sid',label:'供应商',type:'select',value:String(ss[0].id),options:ss.map(x=>({value:String(x.id),label:x.name}))},{name:'pid',label:'采购商品',type:'select',value:String(ps[0]?.id||''),options:ps.map(x=>({value:String(x.id),label:`${x.name} · 库存${x.stock}`}))},{name:'qty',label:'采购数量',type:'number',value:10,min:1,required:true},{name:'unit',label:'采购单价（已配置供货价可留空）',type:'number',value:'',min:0}]});if(!v)return;let item={productId:Number(v.pid),quantity:Number(v.qty||0)};if(v.unit!==undefined&&v.unit!==null&&v.unit!=='')item.unitCost=Number(v.unit);await api('/api/platform/procurement/purchase-orders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({supplierId:Number(v.sid),items:[item],note:'平台采购'})});toast('采购单草稿已创建');loadSupplyChain()}
async function approvePO(id){await api(`/api/platform/procurement/purchase-orders/${id}/approve`,{method:'POST'});toast('采购单已审核');loadSupplyChain()}
async function cancelPO(id){const v=await showForm({title:'取消采购单',desc:'取消后该草稿采购单不可再审核或下单。',submitText:'确认取消',danger:true,fields:[{name:'note',label:'取消原因',type:'textarea',value:'采购计划调整',required:true}]});if(!v)return;await api(`/api/platform/procurement/purchase-orders/${id}/cancel`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({note:v.note})});toast('采购单已取消');loadSupplyChain()}
async function orderPO(id){await api(`/api/platform/procurement/purchase-orders/${id}/order`,{method:'POST'});toast('采购单已确认下单');loadSupplyChain()}
async function receivePO(id){let po=await api(`/api/platform/procurement/purchase-orders/${id}`);const rows=(po.items||[]).map(x=>({x,remain:x.quantity_ordered-x.quantity_received})).filter(r=>r.remain>0);if(!rows.length){showAlert({title:'无需入库',message:'该采购单没有待入库数量。'});return}const v=await showForm({title:'确认到货入库',submitText:'确认入库',fields:rows.map(r=>({name:'q_'+r.x.id,label:`${r.x.product_name}（未到货 ${r.remain}）`,type:'number',value:r.remain,min:0}))});if(!v)return;const items=rows.map(r=>({purchaseOrderItemId:r.x.id,quantity:Number(v['q_'+r.x.id]||0)})).filter(i=>i.quantity>0);if(!items.length)return;let r=await api(`/api/platform/procurement/purchase-orders/${id}/receive`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items,note:'平台仓库确认到货'})});let badSync=(r.commerceSync||[]).filter(x=>x.status==='error');toast(badSync.length?'已入库；Medusa库存同步待重试':'到货已入库');loadSupplyChain();loadProducts()}
async function manualAdjust(){let ps=await api('/api/platform/products');const v=await showForm({title:'库存调整',submitText:'提交调整',fields:[{name:'pid',label:'商品',type:'select',value:String(ps[0]?.id||''),options:ps.map(x=>({value:String(x.id),label:`${x.name} · 库存${x.stock}`}))},{name:'delta',label:'库存调整数量（盘亏输入负数）',type:'number',value:1,required:true},{name:'reason',label:'调整原因',value:'仓库盘点'}]});if(!v||!v.delta)return;await api('/api/platform/inventory/adjustments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({productId:Number(v.pid),quantityDelta:Number(v.delta||0),reason:v.reason||'仓库盘点'})});toast('库存调整已记入流水');loadSupplyChain();loadProducts()}

async function loadWarehouse(){
 skel('#warehouseSummary',4);try{let [sum,ws,inv,tasks]=await Promise.all([api('/api/platform/warehouse/summary'),api('/api/platform/warehouses'),api('/api/platform/warehouse/inventory'),api('/api/platform/warehouse/tasks')]);
 $('#warehouseSummary').innerHTML=[['实物库存',sum.onHand],['锁定库存',sum.reserved],['可用库存',sum.available],['待拣货',sum.pendingPickTasks]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div></div>`).join('');
 $('#warehouseList').innerHTML=ws.map(w=>`<div class="notice" style="margin-bottom:8px"><strong>${esc(w.name)}</strong> · ${esc(w.code)}${w.is_default?' · 默认仓':''}<div class="sub">${(w.locations||[]).map(l=>`${esc(l.code)} ${esc(l.name)}｜实物 ${l.on_hand||0}｜锁定 ${l.reserved||0}`).join('<br>')}</div></div>`).join('')||'<div class="empty">暂无仓库</div>';
 $('#warehouseInventory').innerHTML=`<table class="table"><thead><tr><th>商品</th><th>仓库</th><th>库位</th><th>区域</th><th>实物</th><th>锁定</th><th>可用</th></tr></thead><tbody>${inv.map(x=>`<tr><td>${esc(x.product_name)}</td><td>${esc(x.warehouse_name)}</td><td>${esc(x.location_code)}</td><td>${esc(x.zone_type)}</td><td>${x.on_hand}</td><td>${x.reserved}</td><td><strong>${x.available}</strong></td></tr>`).join('')}</tbody></table>`;
 $('#warehouseTasks').innerHTML=tasks.map(t=>`<div class="notice" style="margin-bottom:8px"><strong>#${t.order_id} · ${t.task_type==='pick'?'拣货':'打包'}</strong><span class="tag" style="margin-left:8px">${esc(enumCn(t.status))}</span>${t.task_type==='pick'&&t.status==='pending'?` <button class="btn secondary" onclick="markPicked('${t.id}')">完成拣货</button>`:''}</div>`).join('')||'<div class="empty">暂无仓库任务</div>';
}catch(e){loaderError('#warehouseSummary',e,'loadWarehouse 加载失败')}}
async function markPicked(id){await api(`/api/platform/warehouse/tasks/${id}/picked`,{method:'POST'});toast('拣货完成');loadWarehouse()}

async function loadFinance(){try{skel('#supplierFinanceList',4);let [ss,sum,orders,products]=await Promise.all([api('/api/platform/finance/supplier-summary'),api('/api/platform/profit/summary'),api('/api/platform/profit/orders?limit=50'),api('/api/platform/profit/products')]);let payable=ss.reduce((a,x)=>a+Number(x.outstanding||0),0),overdue=ss.reduce((a,x)=>a+Number(x.overdue||0),0);$('#financeMetrics').innerHTML=[['供应商应付',money(payable)],['逾期应付',money(overdue)],['贡献利润',money(sum.contributionProfit)],['贡献毛利率',`${Number(sum.contributionMarginPct||0).toFixed(1)}%`]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div></div>`).join('');$('#supplierFinanceList').innerHTML=ss.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.supplierName)}</div> · 应付 ${money(x.outstanding)}${x.overdue>0?` <span class="tag orange">逾期 ${money(x.overdue)}</span>`:''}<div class="list-row__sub">已付 ${money(x.paidAmount)} · 已冲贷项 ${money(x.creditsApplied)} · 未用贷项 ${money(x.availableCredit)} · 采购价差 ${money(x.purchasePriceVariance)}</div><button class="btn ghost" style="margin-top:7px" onclick="paySupplier(${x.supplierId},${x.outstanding})">登记付款</button></div></div>`).join('')||'<div class="empty">暂无供应商应付</div>';$('#orderProfitList').innerHTML=orders.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">订单 #${x.orderId}</div> · 净销售 ${money(x.netSales)} · <b>${money(x.contributionProfit)}</b> <div class="list-row__sub">COGS ${money(x.cogs)} · 佣金 ${money(x.clubCommission)} · 积分/福利 ${money(Number(x.platformPointSubsidy)+Number(x.platformBenefitSubsidy))} · 物流包装 ${money(Number(x.shippingCost)+Number(x.packagingCost))}</div><button class="btn ghost" style="margin-top:6px" onclick="setOrderCosts(${x.orderId},${x.shippingCost||0},${x.packagingCost||0})">录入物流成本</button><div class="list-row__end"><span class="tag ${x.contributionProfit<0?'orange':''}">${Number(x.contributionMarginPct||0).toFixed(1)}%</span></div></div></div>`).join('')||'<div class="empty">暂无商城订单</div>';$('#productProfitList').innerHTML=`<table class="table"><thead><tr><th>商品</th><th>净销量</th><th>净销售</th><th>COGS</th><th>毛利</th><th>毛利率</th></tr></thead><tbody>${products.map(x=>`<tr><td>${esc(x.name)}<div class="sub">${esc(x.sku)}</div></td><td>${x.netUnits}</td><td>${money(x.netSales)}</td><td>${money(x.cogs)}</td><td><strong>${money(x.grossProfit)}</strong></td><td>${Number(x.grossMarginPct||0).toFixed(1)}%</td></tr>`).join('')}</tbody></table>`}catch(e){loaderError('#supplierFinanceList',e,'loadFinance 加载失败')}}
async function paySupplier(supplierId,outstanding){if(Number(outstanding)<=0){showAlert({title:'没有应付余额',message:'当前没有应付余额。'});return}const v=await showForm({title:'登记供应商付款',submitText:'登记付款',fields:[{name:'amt',label:'本次付款金额',type:'number',value:Number(Number(outstanding).toFixed(2)),min:0.01,required:true},{name:'ref',label:'银行/支付凭证号',required:true}]});if(!v)return;await api('/api/platform/finance/supplier-payments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({supplierId,amount:Number(v.amt||0),paymentRef:v.ref,paymentMethod:'bank',note:'平台供应商付款'})});toast('供应商付款已登记');loadFinance()}
async function setOrderCosts(orderId,shipping,packaging){const v=await showForm({title:'设置订单运营成本',submitText:'保存',fields:[{name:'s',label:'物流成本',type:'number',value:Number(shipping||0),min:0},{name:'p',label:'包材成本',type:'number',value:Number(packaging||0),min:0}]});if(!v)return;await api(`/api/platform/orders/${orderId}/operating-costs`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({shippingCost:Number(v.s||0),packagingCost:Number(v.p||0)})});toast('订单运营成本已更新');loadFinance()}
async function createPurchaseReturn(poId){let po=await api(`/api/platform/procurement/purchase-orders/${poId}`);const rows=(po.items||[]).filter(x=>x.quantity_received>0);if(!rows.length){showAlert({title:'无可退货商品',message:'该采购单没有已到货数量。'});return}const v=await showForm({title:'新建采购退货',submitText:'提交退货',fields:rows.map(x=>({name:'q_'+x.id,label:`${x.product_name}（已到货 ${x.quantity_received}）本次退回数量`,type:'number',value:0,min:0}))});if(!v)return;const items=rows.map(x=>({purchaseOrderItemId:x.id,quantity:Number(v['q_'+x.id]||0)})).filter(i=>i.quantity>0);if(!items.length)return;let r=await api('/api/platform/procurement/purchase-returns',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({purchaseOrderId:poId,items,note:'平台采购退货'})});await api(`/api/platform/procurement/purchase-returns/${r.id}/approve`,{method:'POST'});await api(`/api/platform/procurement/purchase-returns/${r.id}/ship`,{method:'POST'});await api(`/api/platform/procurement/purchase-returns/${r.id}/credit`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({note:'供应商确认退货贷项'})});toast('采购退货已出库并形成供应商贷项');loadSupplyChain();loadFinance()}


async function loadAnalytics(){
 skel('#analyticsMetrics',4);try{let [d,products,clubs,suppliers,recs]=await Promise.all([
   api('/api/platform/analytics/commerce'),api('/api/platform/analytics/products'),api('/api/platform/analytics/clubs'),api('/api/platform/analytics/suppliers'),api('/api/platform/replenishment/recommendations')
 ]);
 const cover=d.inventoryDaysCover===null?'—':`${Number(d.inventoryDaysCover).toFixed(1)}天`;
 $('#analyticsMetrics').innerHTML=[['近'+d.windowDays+'天 Gear GMV',money(d.grossSales)],['库存金额',money(d.inventoryValue)],['库存覆盖',cover],['缺货风险 SKU',d.stockRiskSkuCount],['慢动销金额',money(d.slowMovingInventoryValue)],['售后率',`${Number(d.afterSalesRatePct||0).toFixed(1)}%`],['近'+d.windowDays+'天贡献利润',money(d.windowContributionProfit)],['每100报名 Gear GMV',d.gearGmvPer100Registrations===null?'—':money(d.gearGmvPer100Registrations)]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div></div>`).join('');
 const health={stockout:'缺货',at_risk:'高风险',replenish:'建议补货',needs_supplier:'缺供应商',low_stock:'低库存',slow_moving:'慢动销',healthy:'健康'};
 $('#replenishmentList').innerHTML=recs.map(x=>`<div class="list-row"><div class="list-row__main"><div class="panel-title"><div><div class="list-row__title">${esc(x.name)}</div> <div class="list-row__sub">可售 ${x.available} · 在途 ${x.inboundOpenQty} · ${x.daysCover===null?'暂无销量':`覆盖 ${x.daysCover} 天`} · ${esc(x.primarySupplier||'未配置供应商')}</div></div>${x.recommendedReorderQty>0?`<button class="btn secondary" onclick="createReplenishmentPO(${x.productId},${x.recommendedReorderQty})">补 ${x.recommendedReorderQty}</button>`:''}</div><div class="list-row__sub">${esc(x.healthReason)}${x.salesVelocityPerDay?` · 日均 ${Number(x.salesVelocityPerDay).toFixed(2)} 件`:''}</div><div class="list-row__end"><span class="tag ${['stockout','at_risk','needs_supplier'].includes(x.health)?'orange':''}">${health[x.health]||x.health}</span></div></div>`).join('')||'<div class="empty">当前没有库存预警</div>';
 $('#clubAnalyticsList').innerHTML=clubs.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.clubName)}</div> · ${money(x.netSales)}<div class="list-row__sub">${x.orderCount} 单 · 客单 ${money(x.aov)} · 退款率 ${Number(x.refundRatePct||0).toFixed(1)}% · 佣金 ${money(x.commission)}</div></div></div>`).join('')||'<div class="empty">暂无俱乐部商城数据</div>';
 $('#productAnalyticsList').innerHTML=`<table class="table"><thead><tr><th>商品</th><th>${d.windowDays}天销量</th><th>销售额</th><th>毛利率</th><th>可售</th><th>覆盖</th></tr></thead><tbody>${products.map(x=>`<tr><td>${esc(x.name)}<div class="sub">${esc(x.sku)}</div></td><td>${x.soldUnits}</td><td>${money(x.windowSales)}</td><td>${Number(x.windowGrossMarginPct||0).toFixed(1)}%</td><td>${x.available}</td><td>${x.daysCover===null?'—':Number(x.daysCover).toFixed(1)+'天'}</td></tr>`).join('')}</tbody></table>`;
 $('#supplierAnalyticsList').innerHTML=suppliers.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.supplierName)}</div><div class="list-row__sub">到货率 ${Number(x.fillRatePct||0).toFixed(1)}% · 平均交期 ${x.averageLeadDays===null?'—':x.averageLeadDays+'天'} · 采购退货率 ${Number(x.returnRatePct||0).toFixed(1)}% · 应付 ${money(x.outstandingPayable)}</div></div></div>`).join('')||'<div class="empty">暂无供应商数据</div>';
}catch(e){loaderError('#analyticsMetrics',e,'loadAnalytics 加载失败')}}
async function setReplenishmentPolicy(){let p=await api('/api/platform/replenishment/policy');const v=await showForm({title:'补货策略',submitText:'保存策略',fields:[{name:'windowDays',label:'销量计算窗口（7~180天）',type:'number',value:p.salesWindowDays,min:7,max:180,required:true},{name:'targetCoverDays',label:'到货后目标库存覆盖天数（7~120天）',type:'number',value:p.targetCoverDays,min:7,max:120,required:true},{name:'safetyDays',label:'安全库存天数（0~60天）',type:'number',value:p.safetyDays,min:0,max:60,required:true},{name:'slowMovingDays',label:'多少天无销售视为慢动销（30~365天）',type:'number',value:p.slowMovingDays,min:30,max:365,required:true}]});if(!v)return;await api('/api/platform/replenishment/policy',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({salesWindowDays:Number(v.windowDays),targetCoverDays:Number(v.targetCoverDays),safetyDays:Number(v.safetyDays),slowMovingDays:Number(v.slowMovingDays)})});toast('补货策略已更新');loadAnalytics()}
async function createReplenishmentPO(productId,qty){const v=await showForm({title:'创建补货采购单',submitText:'创建采购草稿',fields:[{name:'q',label:'确认补货数量',type:'number',value:Number(qty||0),min:1,required:true}]});if(!v)return;let r=await api(`/api/platform/replenishment/products/${productId}/create-po`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({quantity:Number(v.q||0)})});toast(`已创建采购草稿 ${r.purchaseOrder.id}`);loadAnalytics();if($('#supply').classList.contains('active'))loadSupplyChain()}

/* ── 商品规格与图集管理 ────────────────────────────────────────────────────
   库存真源已经下沉到规格（product_variants.stock），所以后台必须能维护规格，
   否则多规格商品永远补不了货 —— 采购入库是按商品走的，规格库存只能在这里调。
   图集同理：C 端详情页只读 product_images，不再读 products.image_url 单图。 */

/* ── 品牌 / 品类字典管理（2026-10-24 多品牌化）─────────────────────────────
   两个弹窗结构完全同构，只是端点不同，所以合成一个工厂函数。
   ★ 删除品牌会把商品的 brand_id 置空（不删商品）；删品类同理，但有子品类的
     父品类不让删（后端 400）—— 悬空外键比「删不掉」危险得多。 */
function prodDictDialog(kind){
  const isBrand=kind==='brand';
  const base=isBrand?'/api/platform/brands':'/api/platform/product-categories';
  const label=isBrand?'品牌':'品类';
  const paint=async dlg=>{
    const list=await api(base);
    dlg.querySelector('.pd-list').innerHTML=list.length?list.map(x=>{
      const sub=isBrand?(x.logo_url?'<div class="list-row__sub" style="word-break:break-all">'+esc(x.logo_url)+'</div>':'')
        :(x.parent_id?'<div class="list-row__sub">上级：'+esc((list.find(p=>Number(p.id)===Number(x.parent_id))||{}).name||'—')+'</div>':'<div class="list-row__sub">一级品类</div>');
      return `<div class="list-row" data-id="${x.id}"><div class="list-row__main"><div class="list-row__title">${esc(x.name)}${x.status!=='active'?' <span class="tag orange">已停用</span>':''}</div>${sub}</div>
        <div class="list-row__end"><button class="btn ghost" data-act="edit">编辑</button> <button class="btn ghost" data-act="del" style="color:#a44435">删除</button></div></div>`;
    }).join(''):'<div class="empty">还没有'+label+'。新建商品时可以直接在表单里选，或导入 CSV 时自动创建。</div>';
  };
  const dlg=uxDialog({title:label+'管理',desc:isBrand?'商品可归属品牌；删除品牌只会把商品的品牌置空，不会删除商品。':'支持两级品类；有下级品类的父品类不能删除。',wide:true,
    body:'<div class="pd-list"></div>',
    foot:'<button class="btn" data-act="add">＋ 新增'+label+'</button>'});
  paint(dlg);
  dlg.querySelector('[data-act="add"]').onclick=async()=>{
    const fields=isBrand?[{name:'name',label:'品牌名',required:true,placeholder:'例如 Arc\'teryx'},{name:'logoUrl',label:'Logo 地址',placeholder:'/static/uploads/… 或 https://…',help:'可留空'}]
      :[{name:'name',label:'品类名',required:true,placeholder:'例如 冲锋衣 / 软壳'},{name:'parentId',label:'上级品类',type:'select',value:'',options:[{value:'',label:'无（一级品类）'},...(await api(base)).filter(x=>!x.parent_id).map(x=>({value:x.id,label:x.name}))]}];
    const v=await uxForm({title:'新增'+label,submitText:'创建',fields});
    if(!v||!v.name)return;
    try{
      const body=isBrand?{name:v.name,logoUrl:v.logoUrl||''}:{name:v.name,parentId:v.parentId?Number(v.parentId):null};
      await api(base,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      await loadProductDicts(true);await paint(dlg);toast(label+'已创建');
    }catch(e){showAlert({title:'创建失败',message:e.message})}
  };
  dlg.querySelector('.pd-list').onclick=async e=>{
    const row=e.target.closest('.list-row[data-id]');if(!row)return;
    const id=Number(row.dataset.id),act=e.target.dataset.act;
    if(act==='del'){
      const ok=await showConfirm({title:'删除这个'+label+'？',message:isBrand?'引用该品牌的商品会变成「未设品牌」，商品本身不受影响。':'引用该品类的商品会变成「未分类」，商品本身不受影响。',confirmText:'删除',danger:true});
      if(!ok)return;
      try{await api(base+'/'+id,{method:'DELETE'});await loadProductDicts(true);await paint(dlg);loadProducts();toast(label+'已删除')}
      catch(err){showAlert({title:'删除失败',message:err.message})}
    }
    if(act==='edit'){
      const list=await api(base),cur=list.find(x=>Number(x.id)===id);if(!cur)return;
      const fields=isBrand?[{name:'name',label:'品牌名',value:cur.name,required:true},{name:'logoUrl',label:'Logo 地址',value:cur.logo_url||'',placeholder:'/static/uploads/… 或 https://…'}]
        :[{name:'name',label:'品类名',value:cur.name,required:true},{name:'parentId',label:'上级品类',type:'select',value:cur.parent_id||'',options:[{value:'',label:'无（一级品类）'},...list.filter(x=>!x.parent_id&&Number(x.id)!==id).map(x=>({value:x.id,label:x.name}))]}];
      const v=await uxForm({title:'编辑'+label,submitText:'保存',fields});
      if(!v||!v.name)return;
      try{
        const body=isBrand?{name:v.name,logoUrl:v.logoUrl||''}:{name:v.name,parentId:v.parentId?Number(v.parentId):null};
        await api(base+'/'+id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        await loadProductDicts(true);await paint(dlg);loadProducts();toast('已保存');
      }catch(e){showAlert({title:'保存失败',message:e.message})}
    }
  };
}
function manageBrands(){prodDictDialog('brand')}
function manageCategories(){prodDictDialog('category')}

/* ── CSV 批量导入 / 导出（P3）─────────────────────────────────────────────
   一行 = 一个 SKU：运营手里的表本来就是「采购明细」式的（一色一码一行），
   让他先 pivot 成商品维度再导入，等于把工具的成本转嫁给人。 */
function exportProducts(){
  const qs=new URLSearchParams();
  const b=$('#prodFilterBrand')?.value||'',c=$('#prodFilterCat')?.value||'';
  if(b)qs.set('brand_id',b);
  if(c)qs.set('category_id',c);
  const q=qs.toString();
  /* 直接走浏览器下载：导出可能几百行，经 api() 再拼 Blob 只是多绕一圈。 */
  const a=document.createElement('a');
  a.href='/api/platform/products/export'+(q?'?'+q:'');
  a.download='products.csv';
  document.body.appendChild(a);a.click();a.remove();
  toast('已开始导出'+(q?'（含当前筛选）':'全部商品'));
}
async function importProducts(){
  const dlg=uxDialog({title:'CSV 批量导入',desc:'一行 = 一个 SKU，按「货号 + 商品名」归组；单行出错只跳过该行，不影响其它行。表里写了库里没有的品牌 / 品类会自动创建。',wide:true,
    body:'<div class="plat-csvimp"><textarea id="csvText" class="plat-csvimp__ta" placeholder="从 Excel 直接复制粘贴到这里（含表头行）&#10;商品名,货号,品牌,品类,人群,季节,系列,面料,零售价,佣金率%,补货点,颜色,色值,尺码,条码,库存,规格价,介绍" spellcheck="false"></textarea>'
      +'<div class="plat-csvimp__hint">列名：'+['商品名','货号','品牌','品类','人群','季节','系列','面料','零售价','佣金率%','补货点','颜色','色值','尺码','条码','库存','规格价','介绍'].join(' / ')+'。人群可填 男款 / 女款 / 中性 / 儿童。</div>'
      +'<div class="plat-csvimp__result" id="csvResult" hidden></div></div>',
    foot:'<button class="btn ghost" type="button" data-ok>取消</button><button class="btn" type="button" id="csvGo">开始导入</button>'});
  dlg.querySelector('#csvGo').onclick=async()=>{
    const text=(dlg.querySelector('#csvText').value||'').trim();
    if(!text){toast('请先粘贴 CSV 内容');return}
    const btn=dlg.querySelector('#csvGo');btn.disabled=true;btn.textContent='导入中…';
    try{
      const r=await api('/api/platform/products/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csv:text,createMissing:true})});
      const box=dlg.querySelector('#csvResult');box.hidden=false;
      const errs=(r.errors||[]);
      box.innerHTML='<b>导入完成</b>：新建商品 '+r.createdProducts+' · 更新商品 '+r.updatedProducts+' · 新建规格 '+r.createdVariants
        +((r.newBrands||[]).length?'<br>自动创建品牌：'+r.newBrands.map(esc).join('、'):'')
        +((r.newCategories||[]).length?'<br>自动创建品类：'+r.newCategories.map(esc).join('、'):'')
        +(errs.length?'<br><span style="color:#b4482f">'+r.errorCount+' 行出错（已跳过）：</span><ul style="margin:6px 0 0;padding-left:18px;font-size:12px;line-height:1.7">'+errs.slice(0,12).map(e=>'<li>第 '+e.row+' 行：'+esc(e.msg||'')+'</li>').join('')+(errs.length>12?'<li>… 其余 '+(errs.length-12)+' 行略</li>':'')+'</ul>':'');
      await loadProductDicts(true);loadProducts();
    }catch(e){
      const box=dlg.querySelector('#csvResult');box.hidden=false;
      box.innerHTML='<span style="color:#b4482f">导入失败：'+esc(e.message||'')+'</span>';
    }
    btn.disabled=false;btn.textContent='重新导入';
  };
}
async function manageVariants(pid){
  const list=async()=>await api(`/api/platform/products/${pid}/variants`);
  const paint=async(dlg)=>{
    const vs=await list();
    dlg.querySelector('.pv-list').innerHTML=vs.length?vs.map(v=>`<div class="list-row" data-id="${v.id}">
        <div class="list-row__main"><div class="list-row__title">${v.color_hex?`<span class="plat-mx__swatch" style="background:${esc(v.color_hex)};display:inline-block;vertical-align:-4px;margin-right:6px"></span>`:''}${esc(v.name)}${v.is_default?' <span class="tag">占位</span>':''}</div>
          <div class="list-row__sub">${esc(v.sku||'')}${v.barcode?' · 条码 '+esc(v.barcode):''}<br>${v.price!=null?money(v.price):'跟随商品价'} · 库存 ${v.stock}${v.status!=='active'?' · 已停用':''}</div></div>
        <div class="list-row__end"><button class="btn ghost" data-act="edit">编辑</button> <button class="btn ghost" data-act="del">删除</button></div>
      </div>`).join(''):'<div class="empty">还没有规格。没有规格的商品在 C 端不显示规格选择。</div>';
  };
  const dlg=uxDialog({title:'商品规格',
    desc:'库存已下沉到规格：这里的数量就是 C 端能买到的真实数量，改完商品汇总会自动重算。颜色 / 尺码 / 条码是 SKU 的两个维度，C 端按它们画选择器。',
    body:'<div class="pv-list"></div>',wide:true,
    foot:'<button class="btn ghost" data-act="matrix">按颜色×尺码批量生成</button><button class="btn" data-act="add">新增规格</button>'});
  await paint(dlg);
  /* 批量矩阵：2 色 × 4 码逐个加要填 8 遍，运营一定会漏。
     期初库存给一个明确落点（第一个组合），均分会让每个 SKU 都只有几件。 */
  dlg.querySelector('[data-act="matrix"]').onclick=async()=>{
    const vs=await list();
    const colors=[],seen=new Set();
    vs.forEach(v=>{if(v.color&&!seen.has(v.color)){seen.add(v.color);colors.push({name:v.color,hex:v.color_hex||''})}});
    const mx=await prodMatrixDialog({title:'批量生成规格',desc:'按 颜色 × 尺码 一次生成所有组合；已存在的组合会跳过，不会重置已调好的库存与条码。',colors,sizes:vs.map(v=>v.size).filter(Boolean)});
    if(mx===null)return;
    if(!mx.colors.length&&!mx.sizes.length){toast('没有配置颜色或尺码');return}
    try{
      const r=await api(`/api/platform/products/${pid}/variants/matrix`,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({colors:mx.colors,sizes:mx.sizes})});
      toast(`已生成 ${r.created} 个规格`+(r.skipped?`，跳过已存在 ${r.skipped} 个`:'')+(r.movedStock?`（占位规格 ${r.movedStock} 件库存已转入）`:''));
      await paint(dlg);loadProducts();
    }catch(e){showAlert({title:'生成失败',message:e.message})}
  };
  dlg.querySelector('[data-act="add"]').onclick=async()=>{
    const v=await showForm({title:'新增规格',submitText:'保存',fields:[
      {name:'name',label:'规格名',help:'颜色 / 尺码都填了可留空，自动拼成「炭灰 / M」'},
      {name:'color',label:'颜色',placeholder:'例如 炭灰'},
      {name:'colorHex',label:'色值',type:'color',help:'C 端色块用；不填用默认灰'},
      {name:'size',label:'尺码',placeholder:'例如 M'},
      {name:'barcode',label:'条码',placeholder:'可留空'},
      {name:'price',label:'规格价',type:'number',value:'',min:0,help:'留空则跟随商品价格'},
      {name:'stock',label:'该规格库存',type:'number',value:0,min:0,required:true}]});
    if(!v)return;
    if(!v.name&&!v.color&&!v.size){showAlert({title:'保存失败',message:'规格名、颜色、尺码至少要填一个'});return}
    try{
      const r=await api(`/api/platform/products/${pid}/variants`,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:v.name||'',color:v.color||'',colorHex:v.colorHex||'',size:v.size||'',barcode:v.barcode||'',
          price:(v.price===''||v.price==null)?null:Number(v.price),stock:Number(v.stock||0)})});
      toast(r.movedStock?`规格已新增（占位规格的 ${r.movedStock} 件库存已转入）`:'规格已新增');
      await paint(dlg);loadProducts();
    }catch(e){showAlert({title:'新增失败',message:e.message})}
  };
  dlg.querySelector('.pv-list').onclick=async e=>{
    const row=e.target.closest('.list-row[data-id]');if(!row)return;
    const id=Number(row.dataset.id),act=e.target.dataset.act;
    if(act==='del'){
      const ok=await showConfirm({title:'删除这个规格？',message:'该规格的库存会从商品汇总里扣掉，已下单的订单不受影响。删到只剩 0 个规格时，系统会补回一个「默认」规格，避免商品在 C 端直接变缺货。',confirmText:'删除',danger:true});
      if(!ok)return;
      try{await api(`/api/platform/products/${pid}/variants/${id}`,{method:'DELETE'});await paint(dlg);loadProducts();toast('规格已删除')}
      catch(err){showAlert({title:'删除失败',message:err.message})}
    }
    if(act==='edit'){
      const cur=(await list()).find(x=>x.id===id);if(!cur)return;
      const v=await showForm({title:'编辑规格',submitText:'保存',fields:[
        {name:'name',label:'规格名',value:cur.name,required:true},
        {name:'color',label:'颜色',value:cur.color||'',placeholder:'例如 炭灰'},
        {name:'colorHex',label:'色值',type:'color',value:cur.color_hex||'#dfe5e0',help:'C 端色块用'},
        {name:'size',label:'尺码',value:cur.size||'',placeholder:'例如 M'},
        {name:'barcode',label:'条码',value:cur.barcode||'',placeholder:'可留空'},
        {name:'price',label:'规格价',type:'number',value:cur.price==null?'':cur.price,min:0,help:'留空则跟随商品价格'},
        {name:'stock',label:'库存',type:'number',value:cur.stock,min:0,required:true}]});
      if(!v)return;
      try{await api(`/api/platform/products/${pid}/variants/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:v.name,color:v.color||'',colorHex:v.colorHex||'',size:v.size||'',barcode:v.barcode||'',
          price:(v.price===''||v.price==null)?null:Number(v.price),stock:Number(v.stock||0)})});
        await paint(dlg);loadProducts();toast('已保存')}
      catch(err){showAlert({title:'保存失败',message:err.message})}
    }
  };
}
async function manageImages(pid){
  const list=async()=>await api(`/api/platform/products/${pid}/images`);
  const paint=async(dlg)=>{
    const imgs=await list();
    dlg.querySelector('.pi-list').innerHTML=imgs.length?imgs.map(i=>`<div class="list-row" data-id="${i.id}">
        <div class="list-row__main"><div class="list-row__title" style="word-break:break-all;font-weight:400">${esc(i.url)}</div>
          <div class="list-row__sub">${i.sort===0?'主图（同时作为列表封面）':'第 '+(i.sort+1)+' 张'}</div></div>
        <div class="list-row__end"><button class="btn ghost" data-act="del">删除</button></div>
      </div>`).join(''):'<div class="empty">还没有图片。没有图片的商品在 C 端详情页显示占位，不会显示轮播。</div>';
  };
  const dlg=uxDialog({title:'商品图集',
    desc:'C 端详情页按这里的顺序轮播；第一张同时作为列表卡片封面。',
    body:'<div class="pi-list"></div>',wide:true,
    foot:'<button class="btn" data-act="add">添加图片</button>'});
  await paint(dlg);
  dlg.querySelector('[data-act="add"]').onclick=async()=>{
    const v=await showForm({title:'添加图片',submitText:'保存',fields:[
      {name:'url',label:'图片地址',required:true,help:'以 /static/uploads/… 或 https:// 开头的图片地址'}]});
    if(!v||!v.url)return;
    try{await api(`/api/platform/products/${pid}/images`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url:String(v.url).trim()})});
      await paint(dlg);loadProducts();toast('图片已添加')}
    catch(e){showAlert({title:'添加失败',message:e.message})}
  };
  dlg.querySelector('.pi-list').onclick=async e=>{
    const row=e.target.closest('.list-row[data-id]');if(!row)return;
    if(e.target.dataset.act!=='del')return;
    const ok=await showConfirm({title:'删除这张图片？',message:'删除后 C 端详情页就不再显示它。',confirmText:'删除',danger:true});
    if(!ok)return;
    try{await api(`/api/platform/products/${pid}/images/${row.dataset.id}`,{method:'DELETE'});await paint(dlg);loadProducts();toast('已删除')}
    catch(err){showAlert({title:'删除失败',message:err.message})}
  };
}
