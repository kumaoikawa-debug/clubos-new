let CLUB=1;let currentActivity=null;
navInit();window.go=v=>{document.querySelector(`.nav button[data-view="${v}"]`)?.click()};
window.onView=async v=>{if(v==='activities')await loadActivities();if(v==='actcats')await loadActCats();if(v==='content'||v==='content-channel'||v==='content-recap'||v==='content-list')await loadContent();if(v==='regs'||v==='regs-list'||v==='regs-refund')await loadRegs();if(v==='execution')await loadExecution();if(v==='members'||v==='members-list'||v==='members-tiers'||v==='members-benefits'||v==='members-mall')await loadMembers();if(v==='mall'||v==='mall-overview'||v==='mall-products'||v==='mall-orders'||v==='mall-aftersales'||v==='mall-settle')await loadMall();if(v==='analytics')await loadClubBI();if(v==='points')await loadPointsPolicy();if(v==='info')await loadInfoCenter();if(v==='camp')await loadCamp();if(v==='settings')await setTab(SETTINGS_TAB)}
/* 侧栏「设置中枢」入口：跳到独立的能力总览页 settings-hub.html，
   自动带上当前 club_id 与 sharecode（沿用当前页 query，避免二次鉴权失败）。
   注意用绝对路径 /static/club/settings-hub.html，SPA 入口在根路径，相对路径会 404。 */
window.openSettingsHub=function(){
  const p=new URLSearchParams(location.search);
  if(!p.has('club_id'))p.set('club_id',String(CLUB||1));
  location.href='/static/club/settings-hub.html?'+p.toString();
};
async function loadDash(){skel('#recentActivities',4);let d=await api(`/api/club/${CLUB}/dashboard`);$('#creditPill').textContent=`智能 Credits ${d.credits?.balance||0}`;$('#dashMetrics').innerHTML=[['活动',d.activityCount],['报名',d.registrationCount],['客户',d.memberCount],['商城GMV',money(d.gearGMV)]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div><div class="hint">独立经营数据</div></div>`).join('');$('#analyticsMetrics').innerHTML=[['活动数',d.activityCount],['报名数',d.registrationCount],['商城GMV',money(d.gearGMV)],['商城佣金',money(d.commission)]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div></div>`).join('');let a=await api(`/api/club/${CLUB}/activities`);$('#recentActivities').innerHTML=a.slice(0,5).map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.title)}</div><div class="list-row__sub">${dateText(x.event_date)} · ${esc(x.location||'')}</div></div></div>`).join('')||'<div class="empty">还没有活动</div>';loadClubAttention()}
/* 工作台「待处理事项」：C 端产生新报名 / 新订单后，俱乐部没有主动提醒，只能自己进
   报名管理 / 商城去翻。这里把各视图里「需要人跟进」的数字汇总到工作台一张卡上，
   每项可点击直接跳去处理 —— 等于给俱乐部端补上了测试中暴露的"通知"缺口，
   而不必为此新建一套消息系统。只复用已有的 registrations / mall-orders /
   visibility-requests 三个只读接口，按业务状态聚合，不做任何写操作。 */
async function loadClubAttention(){
  const box=$('#clubAttention'); if(!box)return;
  box.innerHTML='<div class="sub">正在汇总待处理事项…</div>';
  try{
    const [regs,orders,reqs,notice]=await Promise.all([
      api(`/api/club/${CLUB}/registrations`),
      api(`/api/club/${CLUB}/mall/orders`),
      api(`/api/club/${CLUB}/mall/visibility-requests`),
      api(`/api/club/${CLUB}/inquiries?status=new`,{cache:'no-store'}).catch(()=>({}))
    ]);
    const r=(regs||[]);
    const incomplete=r.reduce((s,x)=>s+Math.max(0,(Number(x.active_participants||x.participant_count||1))-Number(x.complete_participants||0)),0);
    const ins=r.reduce((s,x)=>s+Number(x.insurance_pending||0),0);
    const rf=r.filter(x=>String(x.refund_status||'')==='requested').length;
    const paidRegs=r.filter(x=>x.status==='paid'||x.status==='completed').length;
    const o=(orders||[]);
    const ship=o.filter(x=>x.status==='paid'&&!x.tracking_no).length;
    const as=o.filter(x=>x.after_sales_status&&x.after_sales_status!=='无'&&x.after_sales_status!=='none').length;
    const vp=(reqs||[]).filter(x=>String(x.status||'')==='pending').length;
    const inq=Number((notice||{}).newCount||0);
    const items=[];
    // 顾客咨询（线路 + 领队）排最前：C 端顾客把手机号留在那里等回电，比任何「待发货」都拖不起。
    if(inq)items.push({n:inq,t:'顾客咨询待回复',go:'info',h:'C 端顾客在问线路 / 领队，等你回电'});
    if(paidRegs)items.push({n:paidRegs,t:'进行中报名',go:'regs',h:'已支付报名待跟进'});
    if(incomplete)items.push({n:incomplete,t:'资料待补',go:'regs',h:'参加人资料待补全'});
    if(ins)items.push({n:ins,t:'保险待处理',go:'regs',h:'保险待出 / 待确认'});
    if(rf)items.push({n:rf,t:'待审退款',go:'regs',h:'退款申请待审核'});
    if(ship)items.push({n:ship,t:'待发货订单',go:'mall',h:'商城订单待发货'});
    if(as)items.push({n:as,t:'商城售后',go:'mall',h:'售后进度跟进'});
    if(vp)items.push({n:vp,t:'待处理上架',go:'mall',h:'上下架申请待总平台'});
    box.innerHTML=items.length
      ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px">`+items.map(i=>`<div style="border:1px solid #d8e3dd;border-radius:14px;padding:14px;cursor:pointer;background:#fff" role="button" tabindex="0" onclick="attentionGo('${i.go}')"><div style="font-size:26px;font-weight:800;color:#1f7a4d">${i.n}</div><div style="font-weight:600;margin-top:2px">${esc(i.t)}</div><div style="font-size:12px;color:#6b7f76;margin-top:3px">${esc(i.h)}</div><div style="font-size:12px;color:#1f7a4d;margin-top:8px">去处理 ›</div></div>`).join('')+`</div>`
      : `<div class="notice">暂时没有需要处理的待办。C 端有新报名或新订单时，这里会第一时间提醒你。</div>`;
  }catch(e){box.innerHTML=`<div class="notice warn">待处理事项加载失败：${esc(e.message)}</div>`}
}
/* 领队咨询的「一键到达」：工作台点待办，直接落到 设置 → 领队资源库 的咨询箱，
   顺手把折叠块展开、滚到视线中央 —— 以前要找它得先猜它藏在哪个标签页，翻三层。 */
async function gotoLeaderInbox(){
  window.go('settings');
  for(let i=0;i<40&&!document.querySelector('#setTabs .set-tab');i++)await new Promise(r=>setTimeout(r,100));
  try{await window.setTab('leaders')}catch(e){}
  const d=document.querySelector('#lpInbox details');if(d)d.open=true;
  const box=document.getElementById('lpInbox');
  if(box)box.scrollIntoView({behavior:'smooth',block:'center'});
}
window.gotoLeaderInbox=gotoLeaderInbox;
window.attentionGo=target=>{
  if(target==='__leaders_inbox'){gotoLeaderInbox();return}
  window.go(target);
};

/* 顾客咨询「有新的」提示（2026-10-28 起，2026-10-29 升级为统一收件箱）：60 秒轮询一次只做
   COUNT 的轻量接口，数字涨了就 toast + 侧栏角标 + 标签页标题前缀。统一收件箱合并线路咨询 +
   领队咨询，所以角标挂在「信息」栏目上 —— 「有活等着你」不再靠人自觉去翻。 */
let _inqSeen=null;
/* 角标由 JS 挂载：侧栏有个脚本会把按钮重绘成「图标 + 文字」，写死在 HTML 里的子节点会被吃掉。 */
function _inqBadge(){
  const btn=document.querySelector('.nav button[data-view="info"]');
  if(!btn)return null;
  let b=btn.querySelector('.nav-badge');
  if(!b){b=document.createElement('i');b.className='nav-badge';b.style.display='none';btn.appendChild(b)}
  return b;
}
async function pollLeaderInquiries(){
  let d;try{d=await api(`/api/club/${CLUB}/inquiries?status=new`,{cache:'no-store'})}catch(_e){return}
  const n=Number(d.newCount||0);
  const badge=_inqBadge();
  if(badge){badge.textContent=n>99?'99+':String(n);badge.style.display=n?'':'none'}
  const base=document.title.replace(/^\(\d+\)\s*/,'');
  document.title=n?`(${n}) ${base}`:base;
  if(_inqSeen!==null&&n>_inqSeen&&typeof toast==='function')toast(`有 ${n-_inqSeen} 条新的顾客咨询待回复`);
  _inqSeen=n;
}
window.pollLeaderInquiries=pollLeaderInquiries;

/* ===== 信息 · 顾客咨询统一收件箱（2026-10-29）=====
   合并线路咨询 + 领队咨询；可选企微群机器人通知（轻量，非硬依赖）。 */
let infoKind='all', infoStatus='new', infoQ='', infoItems=[], infoSearchT=null;

async function loadInfoCenter(){
  try{
    const q=encodeURIComponent(infoQ);
    const d=await api(`/api/club/${CLUB}/inquiries?kind=${infoKind}&status=${infoStatus}&q=${q}&limit=200`,{cache:'no-store'});
    infoItems=d.items||[];
    const cnt=$('#infoCount'); if(cnt)cnt.textContent=`${infoItems.length} 条${d.total!==undefined?`（共 ${d.total}）`:''}`;
    renderInfoList();
  }catch(e){loaderError('#infoList',e,'咨询加载失败')}
  loadInfoWecom();
}

function renderInfoList(){
  const box=$('#infoList'); if(!box)return;
  if(!infoItems.length){box.innerHTML='<div class="empty">暂无咨询。C 端顾客在活动详情点「咨询这条线路」或「咨询领队」后，会在这里出现。</div>';return;}
  box.innerHTML=infoItems.map(it=>{
    const isNew=it.status==='new';
    const kindLabel=it.kind==='route'?'线路咨询':'领队咨询';
    const who=it.name?esc(it.name):'匿名山友';
    const sub=(it.activityTitle?('线路：'+esc(it.activityTitle)):'(未关联活动)')+(it.kind==='leader'&&it.leaderName?(' · 领队：'+esc(it.leaderName)):'');
    return `<div class="info-row${isNew?' info-row--new':''}" role="button" tabindex="0" onclick="infoOpen('${it.kind}',${it.id})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();infoOpen('${it.kind}',${it.id})}">`
      +`<div class="info-row__dot"></div>`
      +`<div class="list-row__main">`
      +`<div class="list-row__title">${who} <span class="tag ${it.kind==='route'?'tag--route':'tag--leader'}">${kindLabel}</span>${isNew?'<span class="tag orange">待处理</span>':'<span class="tag">已处理</span>'}</div>`
      +`<div class="list-row__sub">${sub}</div>`
      +`<div class="info-row__msg">${esc(it.message)}</div>`
      +`<div class="info-row__meta">📞 ${esc(it.phone||'未留')} · ${esc(String(it.createdAt||'').replace('T',' '))}`
      +(Number(it.replyCount||0)?` · <b style="color:#2f6b48">已回复 ${Number(it.replyCount)} 条</b>`:'')+`</div>`
      +`</div></div>`;
  }).join('');
}

function infoSetKind(k){
  infoKind=k;
  $$('#infoKindSeg .seg-btn').forEach(b=>b.classList.toggle('active',b.dataset.kind===k));
  loadInfoCenter();
}
function infoSetStatus(s){
  infoStatus=s;
  $$('#infoStatusSeg .seg-btn').forEach(b=>b.classList.toggle('active',b.dataset.status===s));
  loadInfoCenter();
}
function infoSearchInput(){
  clearTimeout(infoSearchT);
  infoSearchT=setTimeout(()=>{infoQ=($('#infoSearch')?.value||'').trim();loadInfoCenter()},300);
}

/* 关闭最上层的 uxDialog（uxClose 只是 overlay 实例方法，不是全局函数，故封装一层）。 */
function infoClose(){const ov=document.querySelector('.ux-overlay');if(ov&&typeof ov.uxClose==='function')ov.uxClose();}

/* 2026-11-05：咨询从「只留痕 + 打电话」升级为站内对话。
   弹窗里直接读同一条 thread，顾客在 C 端「我的咨询」、领队在 /leader 看到的
   都是这份消息；回复框发出去的消息会同步给三方。老咨询（没有消息表记录）
   用原始 message 兜一条虚拟首条，后台不会突然看不到历史问题。 */
function infoThreadHtml(msgs){
  if(!msgs||!msgs.length)return '<div class="empty" style="padding:14px">暂无往来消息</div>';
  return `<div class="info-thread">`+msgs.map(m=>{
    const who=m.sender==='customer'?'顾客':(m.sender==='leader'?'领队':'俱乐部');
    const mine=m.sender!=='customer';
    return `<div class="info-thread__m ${mine?'is-staff':'is-cust'}">`
      +`<span class="info-thread__who">${esc(who)} · ${esc(String(m.createdAt||'').replace('T',' ').slice(0,16))}</span>`
      +`<div class="info-thread__b">${esc(m.body||'')}</div></div>`;
  }).join('')+`</div>`;
}
async function infoOpen(kind,id){
  const it=infoItems.find(x=>x.kind===kind&&x.id===Number(id)); if(!it)return;
  const isNew=it.status==='new';
  const kindLabel=it.kind==='route'?'线路咨询':'领队咨询';
  let msgs=[];
  try{
    const d=await api(`/api/club/${CLUB}/inquiries/${kind}/${Number(id)}/thread`,{cache:'no-store'});
    msgs=d.messages||[];
  }catch(_e){}
  if(!msgs.length)msgs=[{sender:'customer',body:it.message,createdAt:it.createdAt}];
  const html=`<div class="info-detail">`
    +`<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px"><span class="tag ${it.kind==='route'?'tag--route':'tag--leader'}">${kindLabel}</span>${isNew?'<span class="tag orange">待处理</span>':'<span class="tag">已处理</span>'}</div>`
    +`<div class="info-detail__kv"><b>顾客</b><span>${esc(it.name||'匿名山友')}</span></div>`
    +`<div class="info-detail__kv"><b>手机号</b><span>${it.phone?`<a href="tel:${esc(it.phone)}">${esc(it.phone)}</a>`:'未留'}</span></div>`
    +`<div class="info-detail__kv"><b>线路</b><span>${esc(it.activityTitle||'(未关联活动)')}</span></div>`
    +(it.kind==='leader'?`<div class="info-detail__kv"><b>领队</b><span>${esc(it.leaderName||'')}</span></div>`:'')
    +`<div class="info-detail__kv"><b>提交时间</b><span>${esc(String(it.createdAt||'').replace('T',' '))}</span></div>`
    +`<div class="info-detail__sec">往来消息</div>${infoThreadHtml(msgs)}`
    +`<div class="info-detail__sec">回复顾客</div>`
    +`<textarea id="infoReplyInput" class="info-reply__ta" maxlength="800" placeholder="直接在这里回复顾客，TA 会在「我的咨询」里看到，也可以继续追问…"></textarea>`
    +``
    +`<div style="display:flex;gap:10px;margin-top:14px;justify-content:flex-end;flex-wrap:wrap">`
    +`<button class="btn ghost" onclick="infoClose()">关闭</button>`
    +`<button class="btn ghost" onclick="infoToggleHandled('${it.kind}',${it.id},${isNew})">${isNew?'标记为已处理':'改回待处理'}</button>`
    +`<button class="btn" onclick="infoReply('${it.kind}',${it.id})">发送回复</button></div>`
    +`</div>`;
  uxDialog({title:'顾客咨询 · 对话',body:html});
}
async function infoReply(kind,id){
  const ta=document.getElementById('infoReplyInput');
  const body=String(ta&&ta.value||'').trim();
  if(body.length<2){showAlert({title:'再写具体一点',message:'回复内容至少 2 个字。'});return;}
  try{
    await api(`/api/club/${CLUB}/inquiries/${kind}/${Number(id)}/messages`,
      {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:body})});
    infoClose();await loadInfoCenter();pollLeaderInquiries();
    toast('已回复，顾客在「我的咨询」里能看到');
  }catch(e){showAlert({title:'回复失败',message:e.message})}
}
window.infoReply=infoReply;
window.infoThreadHtml=infoThreadHtml;

async function infoToggleHandled(kind,id,markHandled){
  try{
    await api(`/api/club/${CLUB}/inquiries/${kind}/${id}/handled`,{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({handled:!!markHandled})});
    infoClose();
    await loadInfoCenter();
    pollLeaderInquiries();
    toast(!!markHandled?'已标记为已处理':'已改回待处理');
  }catch(e){showAlert({title:'操作失败',message:e.message})}
}

async function loadInfoWecom(){
  const box=$('#infoWecom'); if(!box)return;
  try{
    const d=await api(`/api/club/${CLUB}/integrations`,{cache:'no-store'});
    const on=!!d.wecomEnabled;
    box.innerHTML=`<div class="ux-field"><label>企业微信群机器人 Webhook</label>`
      +`<input id="wecomWebhook" type="url" placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..." value="${esc(d.wecomWebhook||'')}">`
      +`</div>`
      +`<label class="sub" style="display:flex;align-items:center;gap:8px;margin:10px 0;cursor:pointer"><input type="checkbox" id="wecomEnabled" ${on?'checked':''}> 启用企业微信通知</label>`
      +`<div style="display:flex;gap:10px"><button class="btn" onclick="saveWecom()">保存配置</button><button class="btn ghost" onclick="testWecom()">发送测试</button></div>`;
  }catch(e){box.innerHTML='<div class="empty">读取企微配置失败</div>'}
}
async function saveWecom(){
  const wh=$('#wecomWebhook')?.value||'';
  const en=$('#wecomEnabled')?.checked||false;
  try{
    await api(`/api/club/${CLUB}/integrations`,{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({wecomWebhook:wh,wecomEnabled:en})});
    toast('企微配置已保存');
  }catch(e){showAlert({title:'保存失败',message:e.message})}
}
async function testWecom(){
  try{
    await api(`/api/club/${CLUB}/integrations/test`,{method:'POST'});
    toast('测试消息已发出（请到企微群查看）');
  }catch(e){showAlert({title:'发送失败',message:e.message})}
}

/* ===== 活动中心：可搜索的活动栏 + 右侧成品预览（2026-09-26）=====
   过去 #activityRows 里又套了一层 .act-grid：外层网格只给内层一个格子宽，
   于是卡片被压成一列、右边永远空一大片。现在去掉嵌套，改成「左栏活动 + 右栏预览」。 */
let _acts=[],_actOpenId=null;
async function loadActivities(){
  skel('#activityRows',6);
  try{
  const s=$('#activitySearch'); if(s&&!s.__wired){s.__wired=true;s.oninput=()=>renderActivityList()}
  const soW=$('#activitySortWrap'); /* #830 排序改直接点选（占位容器在 index.html，这里填充） */
  if(soW&&!soW.__wired){soW.__wired=true;soW.innerHTML=uxChoiceHtml({id:'activitySort',name:'activitySort',value:'date-asc',options:[{value:'date-asc',label:'团期最近优先'},{value:'date-desc',label:'团期最远优先'},{value:'new',label:'最近创建优先'},{value:'title',label:'按名称'},{value:'status',label:'已发布优先'}],row:true});}
  const so=$('#activitySort'); if(so&&!so.__wired){so.__wired=true;so.onchange=()=>renderActivityList()}
  const cf=$('#activityCity'); if(cf&&!cf.__wired){cf.__wired=true;cf.onchange=()=>renderActivityList()}
  _acts=await api(`/api/club/${CLUB}/activities`);
  renderActivityList();
  }catch(e){loaderError('#activityRows',e,'活动列表加载失败')}
}
function actDateKey(a){const m=String(a.event_date||'').match(/(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})/);if(!m)return '';return m[1]+'-'+String(m[2]).padStart(2,'0')+'-'+String(m[3]).padStart(2,'0')}
function actSorter(sort){
  return {'date-asc':(x,y)=>(x.k||'9999').localeCompare(y.k||'9999')||(x.a.id-y.a.id),
          'date-desc':(x,y)=>(y.k||'0000').localeCompare(x.k||'0000')||(y.a.id-x.a.id),
          'new':(x,y)=>y.a.id-x.a.id,
          'title':(x,y)=>String(x.a.title||'').localeCompare(String(y.a.title||''),'zh'),
          'status':(x,y)=>(x.a.status==='published'?0:1)-(y.a.status==='published'?0:1)||(y.a.id-x.a.id)}[sort]
    ||((x,y)=>(x.k||'9999').localeCompare(y.k||'9999')||(x.a.id-y.a.id));
}
function syncActRail(){$$('.act-mini').forEach(el=>el.classList.toggle('active',Number(el.dataset.id)===_actOpenId))}
/* 城市标签（2026-10-23 用户反馈）：优先用活动显式 city，老数据按 location 兜底（clubos-cities.js）。 */
function actCityTag(a){try{return window.clubosCityOf?window.clubosCityOf(a):(a.city||'')}catch(e){return a.city||''}}
function activityMini(a,k){
  const draft=a.status==='draft';
  const cityTag=actCityTag(a);
  const thumb=a.cover?` style="background-image:url('/api/club/${CLUB}/activities/${a.id}/cover')"`:` cov-${a.id%6+1}`;
  return `<div class="act-mini${_actOpenId===a.id?' active':''}" data-id="${a.id}" role="button" tabindex="0" onclick="openActivity(${a.id})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openActivity(${a.id})}">`
    +`<span class="act-mini__thumb"${thumb}>${a.cover?'':(draft?'✎':'⛰')}<span class="cover-flag ${a.cover?'cover-flag--ok':'cover-flag--missing'}">${a.cover?'封面':'未设封面'}</span></span>`
    +`<span class="act-mini__body"><span class="act-mini__title">${esc(a.title)}</span>`
    +`<span class="act-mini__meta"><span>${esc(dateText(k||a.event_date))}</span><span class="badge ${draft?'badge--soon':'badge--published'} badge--dot">${draft?'草稿':'已发布'}</span><span class="act-mini__price">${money(a.price)}</span>${cityTag?'<span class="act-mini__city">📍 '+esc(cityTag)+'</span>':''}</span></span>`
    +`<span class="act-mini__ops"><button type="button" class="act-op" onclick="event.stopPropagation();editActivity(${a.id})">编辑</button><button type="button" class="act-op act-op--danger" onclick="event.stopPropagation();deleteActivity(${a.id})">删除</button></span>`+`</div>`;
}
function renderActivityList(){
  const box=$('#activityRows'); if(!box)return;
  const q=($('#activitySearch')?.value||'').trim().toLowerCase();
  const sort=$('#activitySort')?.value||'date-asc';
  /* 城市筛选（2026-10-23）：把活动按城市归组填充下拉（保留当前选择），再过滤。 */
  const cf=$('#activityCity'); const curCity=cf?cf.value:'all';
  const cityCounts={};
  _acts.forEach(a=>{const c=actCityTag(a);if(c)cityCounts[c]=(cityCounts[c]||0)+1});
  const cities=Object.keys(cityCounts).sort((a,b)=>(cityCounts[b]||0)-(cityCounts[a]||0));
  if(cf){let opts='<option value="all">全部城市</option>';for(const c of cities)opts+=`<option value="${esc(c)}">${esc(c)}（${cityCounts[c]}）</option>`;cf.innerHTML=opts;if(curCity)cf.value=curCity;}
  const cityF=(cf&&cf.value&&cf.value!=='all')?cf.value:'all';
  let rows=_acts.map(a=>({a,k:actDateKey(a)})).filter(({a})=>{
    if(cityF!=='all'&&actCityTag(a)!==cityF)return false;
    if(!q)return true;
    return [a.title,a.location,a.event_date,a.status,String(a.price)].filter(Boolean).join(' ').toLowerCase().includes(q);
  });
  rows.sort(actSorter(sort));
  const cnt=$('#actRailCount'); if(cnt)cnt.textContent=(q||cityF!=='all'?`${rows.length} / ${_acts.length}`:`${rows.length}`)+' 场活动';
  box.innerHTML=rows.map(({a,k})=>activityMini(a,k)).join('')
    ||`<div class="reg-none">${q?'没有匹配的活动，换个关键词或清空搜索。':'还没有活动，点右上角「＋ 智能发活动」。'}</div>`;
  syncActRail();
}
function goActivity(id){_actOpenId=Number(id);go('activities');openActivity(Number(id))}
function goContentForActivity(id){window.__contentActId=Number(id);go('content-channel')}
/* 团期日期人性化：'2026-10-01 08:00' → {md:'10月1日',wd:'周四',t:'08:00'}；
   end_at 存在且跨天时 multi=true（「多日」徽标与范围标题用它判断）。 */
function _occRange(o){
  const p=s=>{const m=/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2})/.exec(String(s||''));if(!m)return null;const d=new Date(m[1]+'-'+m[2]+'-'+m[3]+'T'+m[4]);return {md:Number(m[2])+'月'+Number(m[3])+'日',wd:['周日','周一','周二','周三','周四','周五','周六'][d.getDay()],t:m[4]}};
  const st=p(o.start_at);if(!st)return null;
  const en=p(o.end_at);
  const multi=!!(en&&en.md!==st.md);
  return {st,en,multi,full:en?`${st.md}（${st.wd}）${st.t} ～ ${en.md}（${en.wd}）${en.t}`:`${st.md}（${st.wd}）${st.t}`};
}
/* 「这份详情是按哪些资料做的」必须在页面上说清：方案 + 额外照片一起上传时，光看成品
   根本判断不出 AI 读了哪份文件（2026-10-09 用户反馈）。数据来自后端 sourceInfo，
   它只回文件名 / 字数 / 张数，不回资料正文（正文属俱乐部内部资料，任何前端都不下发）。 */
function activitySourceLine(a){
  const si=(a&&a.sourceInfo)||{};
  if(!si.available||si.reconstructed)return '';
  const plans=(si.planFiles||[]).filter(Boolean),photos=Number(si.photoCount||0),extras=(si.photoFiles||[]).length;
  const lk=(si.link&&si.link.url)?si.link:null;
  /* 链接抓回来的图在 source 里就是 link_01.jpg 这种文件，混在「单独上传」里说会误导。 */
  const linkPhotos=(si.photoFiles||[]).filter(n=>/^link_\d+\./i.test(String(n||''))).length;
  const bits=[];
  if(lk){
    /* 有链接时不再说「没有上传方案文件」—— 那就是误导，老板明明给了资料。 */
    bits.push('依据链接《'+esc(lk.title||lk.url)+'》（读到 '+Number(si.textLength||0)+' 字）');
  }else{
    bits.push(plans.length?('活动方案《'+plans.map(esc).join('》《')+'》'
      +(si.hasPlanText?('（读到 '+Number(si.textLength||0)+' 字）'):'（未读到文字）')):'没有上传方案文件');
  }
  if(photos)bits.push(lk&&linkPhotos?`可用照片 ${photos} 张（链接配图 ${linkPhotos} 张）`:`可用照片 ${photos} 张`);
  if(extras-linkPhotos>0)bits.push('其中单独上传 '+(extras-linkPhotos)+' 张');
  return '<div class="sub" style="margin-top:6px">'+(lk?'本场按链接生成：':'本场按上传资料生成：')+bits.join(' · ')+'</div>';
}
async function openActivity(id){try{LEADER_CTX='activity';if(LEADER_FORM){dropLeaderDraft();LEADER_FORM=null}_actOpenId=Number(id);syncActRail();let a=await api(`/api/club/${CLUB}/activities/${id}`);currentActivity=a;let conflicts=a.activityMaster?.blocking_conflicts||[];const pane=$('#activityDetail');if(!pane)return;pane.innerHTML=`${detailNavHtml([['sec-cover','封面'],['sec-detail','智能详情'],['sec-ops','行程与清单'],['sec-leaders','带队领队'],['sec-rules','报名与政策'],['sec-occ','团期价格']])}<div class="act-head"><div class="act-head__id"><h2>${esc(a.title)}</h2><div class="sub">${esc(a.event_date||'')} · ${esc(a.location||'')} · ${money(a.price)} · ${a.occurrences?.length||0} 个团期 · ${a.status==='draft'?'<span class="tag orange">草稿</span>':'<span class="tag">已发布</span>'}</div></div><div class="act-head__ops">${a.status==='draft'?`<button class="btn" onclick="publishActivity(${id})">发布活动</button>`:''}<button class="btn ghost" onclick="goContentForActivity(${id})">去做宣发内容</button><a class="btn ghost" href="/web?club_id=${CLUB}&activity=${id}&v=${_cverId(a)}" target="_blank" style="text-decoration:none">打开C端</a>${a.status==='published'?`<button class="btn secondary" onclick="shareActivity(${id})">分享活动</button><button class="btn ghost" onclick="republishActivity(${id})" title="把当前这一版重新发布一次：C 端拿到的永远是最新版，这一步同时刷新 C 端版本戳，已打开的顾客页面会出现「有新版」提示">重新发布 / 同步 C 端</button>`:''}${a.detailVersion?.canRegenerate?`<button class="btn secondary" onclick="openRegenerateModal(${id})">重新生成 / 换一版</button>`:''}<button class="btn ghost" onclick="editActivity(${id})">编辑基本信息</button><button class="btn ghost" onclick="deleteActivity(${id})">删除活动</button></div></div>${conflicts.length?`<div class="notice warn">发现真实冲突：${conflicts.map(esc).join('；')}</div>`:''}<div class="act-meta">${activitySourceLine(a)}${_syncNoticeHtml(a)}</div><div class="card section" id="sec-cover"><div class="panel-title"><div><h3>活动封面</h3></div></div><div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap"><div style="width:160px;height:90px;border-radius:12px;background:#edf3f1;background-size:cover;background-position:center;display:flex;align-items:center;justify-content:center;color:#6b8a7b;font-size:12px;text-align:center;${a.cover?`background-image:url('/api/club/${CLUB}/activities/${a.id}/cover')`:''}">${a.cover?'':'未设封面<br><span style="font-size:11px;opacity:.75">可点右侧自动选</span>'}</div><div style="display:flex;flex-direction:column;gap:8px"><input id="coverFile" type="file" accept="image/*"><button class="btn secondary" onclick="uploadCover(${a.id})">上传 / 替换封面</button><button class="btn ghost" onclick="autoCover(${a.id})" title="从这份资料抽出的照片里挑一张做封面（优先沿用详情首图）">智能从素材选一张</button></div></div></div><div id="sec-detail">${renderPromo(a.detail,a.activityMaster,{hideButton:true})}</div><div id="sec-ops">${renderInfoStack(a.activityMaster,{gear:a.gearRecommendations,manage:true,skip:infoStackSkip(a.detail,a.activityMaster)})}</div>${renderLeaderCard(a,id)}<div id="sec-rules">${pointsPolicyCard(a)}${refundPolicyCard(a)}${participantPolicyCard(a)}</div><div class="card section" id="sec-occ"><div class="panel-title"><h3>团期 / 价格 / 名额</h3><button class="btn secondary" onclick="quickAddOccurrence(${id})">＋ 添加团期</button></div>${(a.occurrences||[]).map(o=>{const oc=_occRange(o),lbl=String(o.label||'').trim();const multi=!!(oc&&oc.multi);const title=lbl||(oc?(multi?oc.st.md+' ～ '+oc.en.md:oc.st.md):'团期');const sub=lbl?(oc?oc.full+' · ':'')+(multi?'多日行程 · ':'')+money(o.price)+' · 已售 '+o.sold+'/'+o.capacity:(oc?(multi?oc.st.wd+' '+oc.st.t+' 出发 · '+oc.en.wd+' '+oc.en.t+' 返程 · ':oc.st.wd+' '+oc.st.t+' · '):'')+money(o.price)+' · 已售 '+o.sold+'/'+o.capacity;const tag=multi&&!/～|~/.test(lbl)?' <span class="tag">多日</span>':'';/* 团期行此前只有文字、没有任何操作入口：老板改不了时间/价格/名额（用户截图实证）。
   现在每行都给出「改时间 / 价格」与「删除」；并把两种容易被当成 bug 的状态直接标出来：
   ¥0 不是出错而是「价格待定」（成本表来源的活动在俱乐部定价前会归零），
   已停止报名的团期也要一眼可见，否则老板会以为 C 端还能报。 */
const closed=String(o.status||'open')!=='open'?' <span class="tag orange">已停止报名</span>':'';const pend=!(Number(o.price)>0)?' <span class="tag orange">价格待定</span>':'';return `<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(title)}${tag}${closed}${pend}</div><div class="list-row__sub">${esc(sub)}</div></div><div class="list-row__end"><button type="button" class="act-op" onclick="editOccurrence(${id},${o.id})">改时间 / 价格</button><button type="button" class="act-op act-op--danger" onclick="deleteOccurrence(${id},${o.id})">删除</button></div></div>`}).join('')||'<div class="empty">暂无团期</div>'}</div>`;/* 「这份详情到底是不是按资料生成的」必须一眼看得见。资料里一个字都没读到过
   （master.sourceSummary 为空）时，页面上全是通用兜底文案，用户只会觉得系统坏了；
   这里如实标注来源，并直接给出两个补救入口，而不是让人自己猜。 */
/* ★ 只有拿到「确实没读到方案文字」的实证才告警（2026-10-09 修正）。
   旧判定看 master.sourceSummary，而那个字段只有离线引擎会写：真模型生成的活动
   它永远是空的，于是**每一场**都被红框告知「内容是通用兜底」，明明是按方案生成的。
   用户因此以为「方案和照片一起传，系统读错了文件」。现在以后端的 sourceInfo
   （取自落盘的原始资料）为准：拿不到证据就不说话，宁可不说也不误报。 */
const _si=a.sourceInfo||{};
const _noPlan=_si.available&&!_si.reconstructed&&!_si.hasPlanText
  &&((( _si.planFiles||[]).length>0)||Number(_si.photoCount||0)>0||Number(_si.imageCount||0)>0);
if(a.activityMaster&&a.activityMaster.createdVia!=='manual'&&_noPlan){
  const _plans=(_si.planFiles||[]).filter(Boolean);
  const w=document.createElement('div');w.className='notice warn';w.style.margin='0 0 16px';
  w.innerHTML=('<strong>'+(_plans.length?'这份方案没有读到文字内容':'这次只上传了照片，没有方案文字')+'</strong>'
    +'<div class="sub" style="margin-top:6px">'
    +(_plans.length
      ?'《'+_plans.map(esc).join('》《')+'》的文字可能全部做成了图片，智能读不到这些字。'
      :'系统只识别到照片，没有读到任何文字资料。')
    +'当前的标题 / 日期 / 地点 / 行程因此是通用兜底内容，不是你的方案。'
    +'补一句活动说明（名称 / 日期 / 地点 / 人数）或换一份带文字的方案，点「重新生成 / 换一版」即可；'
    +'也可以直接「编辑基本信息」手工补全。</div>');
  pane.prepend(w);
}
if(window.matchMedia&&matchMedia('(max-width:1180px)').matches)setTimeout(()=>pane.scrollIntoView({behavior:'smooth',block:'start'}),60);syncActRail()}catch(e){showAlert({title:'打开活动失败',message:e.message})}}

function pointsPolicyCard(a){
  const p=a.pointsPolicy||{}; const e=p.effective||{};
  /* 装备积分（Gear Points）是总平台出资的补贴：能不能用、用多少，都由总平台按
     「俱乐部买的套餐」+「平台给这个俱乐部单独设的比例」决定，活动层面改不动。
     所以这里只做只读展示 —— 不给开关，也不给输入框，
     免得俱乐部以为改了自己就能生效（实际下发给 C 端的比例仍以总平台为准）。 */
  const gp=p.gearPointsRule||{};
  const gpPct=Number(e.gearPointsRedeemPercent||0);
  const gpTag=gp.allowed
    ? `<span class="tag">可抵 ${gpPct}%</span>`
    : `<span class="tag orange">不可抵</span>`;
  const gpPlan=gp.planName?`当前套餐「${gp.planName}」`:'当前套餐';
  const gpHow=gp.hasClubOverride
    ? '总平台已为本网俱乐部单独设定比例'
    : (gp.allowed?'沿用套餐自带比例':'套餐未包含该权益');
  const gpBlock=`<div class="notice" style="margin-top:12px">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <strong>装备积分抵用</strong>${gpTag}
        <span class="sub">由总平台统一控制，活动不可调整</span>
      </div>
      <div class="sub" style="margin-top:8px">
        ${gpPlan}（上限 ${Number(gp.planPercent||0)}%）· ${gpHow}${gp.allowed?`，本俱乐部当前生效 ${gpPct}%`:''}。
      </div>
      ${gp.reason?`<div class="sub" style="margin-top:4px">${esc(gp.reason)}</div>`:''}
    </div>`;
  return `<div class="card section" id="pointsPolicyCard">
    <div class="panel-title"><div><h3>活动积分规则</h3></div><span class="tag ${p.enabled?'':'orange'}">${p.enabled?'已开启':'不参与积分'}</span></div>
    <label style="display:flex;gap:10px;align-items:center;padding:10px 0"><input id="ppEnabled" type="checkbox" ${p.enabled?'checked':''}> <strong>这场活动参与积分体系</strong></label>
    <div class="grid g2" style="margin-top:4px">
      <div class="notice"><label><input id="ppEarn" type="checkbox" ${p.earnClubPoints?'checked':''}> 报名后产生活动积分</label></div>
      <div class="notice"><label><input id="ppClub" type="checkbox" ${p.acceptClubPoints?'checked':''}> 允许活动积分抵现金</label><div style="margin-top:8px"><span class="sub">本单最多抵活动金额</span> <input id="ppClubMax" type="number" min="0" max="100" step="1" value="${Number(p.clubPointsMaxDiscountPercent??100)}" style="width:80px"> %</div></div>
    </div>
    ${gpBlock}
    <div class="notice" style="margin-top:12px"><strong>C端实际生效</strong><div class="sub" style="margin-top:6px">累计活动积分：${e.earnClubPoints?'是':'否'} · 活动积分抵扣：${e.acceptClubPoints?'是':'否'} · 装备积分抵扣：${e.acceptGearPoints?'是（最多 '+gpPct+'%）':'否'}</div></div>
    <button class="btn secondary" style="margin-top:12px" onclick="savePointsPolicy(${a.id})">保存积分规则</button>
  </div>`
}
async function savePointsPolicy(id){
  /* 装备积分（总平台体系）不在俱乐部后台配置：相关字段既不出现在界面上，
     也不进 payload —— 后端 activity_points_policy.normalize_update 对缺失键
     回退到当前值，平台侧设置不会被这里顺手改掉。
     （总平台按「套餐资格 + 按俱乐部设的比例」决定装备积分准入与封顶，
       活动级 accept_gear_points / gear_points_max_discount_amount 都不再参与判定。） */
  const payload={
    enabled:$('#ppEnabled').checked,
    earnClubPoints:$('#ppEarn').checked,
    acceptClubPoints:$('#ppClub').checked,
    clubPointsMaxDiscountPercent:Number($('#ppClubMax').value||0)
  };
  try{await api(`/api/club/${CLUB}/activities/${id}/points-policy`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});toast('活动积分规则已保存');await openActivity(id)}catch(e){showAlert({title:'操作失败',message:e.message})}
}

function participantPolicyCard(a){
  const p=a.participantPolicy||{};
  return `<div class="card section"><div class="panel-title"><div><h3>报名人 / 参加人规则</h3></div><span class="tag">最多 ${p.maxParticipantsPerOrder||8} 人/单</span></div><div class="grid g2"><div class="notice"><label><input id="pfIncomplete" type="checkbox" ${p.allowIncompleteAtCheckout!==false?'checked':''}> 允许先付款、后补紧急联系人资料</label></div><div class="notice"><label><input id="pfInsurance" type="checkbox" ${p.insuranceRequired?'checked':''}> 本活动需要保险资料</label></div><div class="notice"><label><input id="pfReplace" type="checkbox" ${p.allowParticipantReplacement?'checked':''}> 允许用户自助转名额</label><div style="margin-top:8px"><span class="sub">出发前至少</span> <input id="pfCutoff" type="number" min="0" value="${Number(p.replacementCutoffHours||0)}" style="width:80px"> 小时</div></div><div class="notice"><span class="sub">单笔最多报名</span> <input id="pfMax" type="number" min="1" max="50" value="${Number(p.maxParticipantsPerOrder||8)}" style="width:80px"> 人</div></div><button class="btn secondary" style="margin-top:12px" onclick="saveParticipantPolicy(${a.id})">保存参加人规则</button></div>`
}
async function saveParticipantPolicy(id){
  const payload={allowIncompleteAtCheckout:$('#pfIncomplete').checked,insuranceRequired:$('#pfInsurance').checked,allowParticipantReplacement:$('#pfReplace').checked,replacementCutoffHours:Number($('#pfCutoff').value||0),maxParticipantsPerOrder:Number($('#pfMax').value||1)};
  try{await api(`/api/club/${CLUB}/activities/${id}/participant-policy`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});toast('参加人规则已保存');openActivity(id)}catch(e){showAlert({title:'操作失败',message:e.message})}
}

function refundPolicyCard(a){
  const p=a.refundPolicy||{enabled:true,rules:[{minHoursBefore:0,cashRefundPercent:100,label:'活动开始前可退款'}],afterStartCashRefundPercent:0,note:''};
  const rules=(p.rules||[]).slice(0,3); while(rules.length<3) rules.push({minHoursBefore:rules.length===0?72:rules.length===1?24:0,cashRefundPercent:rules.length===0?100:rules.length===1?80:0,label:''});
  return `<div class="card section" id="refundPolicyCard">
    <div class="panel-title"><div><h3>活动退款规则</h3></div><span class="tag ${p.enabled?'':'orange'}">${p.enabled?'可申请退款':'不可自主退款'}</span></div>
    <label style="display:flex;gap:10px;align-items:center;padding:10px 0"><input id="rpEnabled" type="checkbox" ${p.enabled?'checked':''}> <strong>允许用户按本活动规则申请退款</strong></label>
    
    <div class="grid g3">
      ${rules.map((r,i)=>`<div class="notice"><strong>规则 ${i+1}</strong><div style="margin-top:8px"><span class="sub">出发前至少</span> <input id="rpH${i}" type="number" min="0" step="1" value="${Number(r.minHoursBefore||0)}" style="width:76px"> 小时</div><div style="margin-top:8px"><span class="sub">现金退款</span> <input id="rpP${i}" type="number" min="0" max="100" step="1" value="${Number(r.cashRefundPercent||0)}" style="width:76px"> %</div><input id="rpL${i}" value="${esc(r.label||'')}" placeholder="例如：出发前72小时以上" style="width:100%;margin-top:8px;padding:8px;border-radius:8px;border:1px solid var(--line)"></div>`).join('')}
    </div>
    <div class="notice" style="margin-top:10px"><span class="sub">活动开始后现金退款比例</span> <input id="rpAfter" type="number" min="0" max="100" step="1" value="${Number(p.afterStartCashRefundPercent||0)}" style="width:76px"> %</div>
    <textarea id="rpNote" rows="2" placeholder="退款说明" style="width:100%;margin-top:10px;padding:10px;border:1px solid var(--line);border-radius:10px">${esc(p.note||'')}</textarea>
    <button class="btn secondary" style="margin-top:12px" onclick="saveRefundPolicy(${a.id})">保存退款规则</button>
  </div>`
}
async function saveRefundPolicy(id){
  const rules=[0,1,2].map(i=>({minHoursBefore:Number($(`#rpH${i}`).value||0),cashRefundPercent:Number($(`#rpP${i}`).value||0),label:$(`#rpL${i}`).value.trim()}));
  const payload={enabled:$('#rpEnabled').checked,rules,afterStartCashRefundPercent:Number($('#rpAfter').value||0),note:$('#rpNote').value.trim()};
  try{await api(`/api/club/${CLUB}/activities/${id}/refund-policy`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});toast('活动退款规则已保存');await openActivity(id)}catch(e){showAlert({title:'操作失败',message:e.message})}
}
/* ===== 活动详情：重新生成 / 换一版 + 换版后的「同步到 C 端」 ===== */
/* 换版之后必须把「C 端现在是什么状态」讲清楚（2026-10-10 用户反馈「重新生成一版之后 C 端还是
   旧版本，后台要有个再次发布的功能」）。事实是：C 端详情是实时读库的，换完版它拿到的就是新版；
   但**已经打开的页面不会自己变** —— 老板在自己的手机上、顾客在报名页上看到的都还是旧的一屏。
   所以这里做两件事：换版后给一条明确提示 + 一颗「同步到 C 端」按钮（走 /republish，
   只刷新发布状态与版本戳，不改内容），C 端据此提示「有新版，点我刷新」。 */
window.__clubSyncPending=null;   // {id, versionNo}：刚换完版、还没点过「同步到 C 端」的那场活动
function _cverId(a){return Number((a&&a.detailVersion&&a.detailVersion.currentId)||0)}
function _syncNoticeHtml(a){
  if(!a)return '';
  const ver=(a.detailVersion&&a.detailVersion.versionNo)||'—';
  if(window.__clubSyncPending&&Number(window.__clubSyncPending.id)===Number(a.id)){
    return `<div class="notice warn act-meta__warn">已换成第 <b>${esc(String(ver))}</b> 版 · 已打开的顾客页面不会自动刷新`
      +`<button class="btn secondary act-meta__btn" type="button" onclick="republishActivity(${Number(a.id)})">同步到 C 端</button></div>`;
  }
  if(a.status==='published'){
    return `<div class="act-meta__hint">第 ${esc(String(ver))} 版 · C 端实时读库，旧页面刷新即可见新版</div>`;
  }
  return '';
}
let _regenDetailBusy=false;
async function openRegenerateModal(id){
  try{
    const r=await showForm({title:'重新生成 / 换一版',desc:'对当前这一版不满意？给个方向，智能重新做叙事和排版。默认只换叙事/排版、保留全部事实（价格、日期、地点、人数、团期、政策都不会变）。',wide:true,fields:[
      {name:'direction',label:'换版方向 / 要求（可选）',type:'textarea',placeholder:'例如：更年轻化一点、多放实拍图、换个更有冲击力的开场……不填则 智能自己换一个角度。'},
      {name:'mode',label:'换版方式',type:'select',value:'freeze',options:[
        {value:'freeze',label:'只换叙事/排版（保留所有事实，推荐）'},
        {value:'refresh',label:'连事实一起复核（仅当首版把日期/价格等读错了才用）'}
      ]}
    ],submitText:'生成新的一版'});
    if(!r) return;
    await regenerateDetail(id,r.direction||'',r.mode==='refresh');
  }catch(e){ showAlert({title:'换版未开始',message:e.message}) }
}
async function regenerateDetail(id,direction,refreshFacts){
  if(_regenDetailBusy){ toast('正在生成新的一版，请稍候…'); return; }
  _regenDetailBusy=true;
  try{
    const r=await api(`/api/club/${CLUB}/activities/${id}/detail-regenerate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({direction:direction||'',refreshFacts:!!refreshFacts})});
    /* live 模式改为异步任务：提交秒回 {jobId,versionNo}，真模型换版要跑几分钟，同步等待
       会被网关空闲超时掐断（2026-10-15 线上实测：95s 处 504 且任务随断连被取消）。
       mock 模式仍直接返回完整结果。两条路在此汇合。 */
    if(r.jobId){
      const started=Date.now();let done=false,misses=0;
      while(!done){
        await new Promise(res=>setTimeout(res,3000));
        let j=null;
        try{ j=await api(`/api/club/${CLUB}/activities/ai-generate/${r.jobId}`); misses=0; }
        catch(pe){ if(++misses>6)throw new Error('进度查询连续失败，请稍后刷新页面在活动详情查看结果（任务仍在后台运行）。'); continue; }
        const sec=Math.round((Date.now()-started)/1000);
        toast(`智能正在换版（已等 ${sec} 秒，约 1~5 分钟）…`);
        if(j.status==='done'){done=true;}
        else if(j.status==='failed'){throw new Error(j.error||'后台换版失败，已保留上一版（未扣 Credits）。');}
        else if(Date.now()-started>10*60*1000){throw new Error('等待超时（10 分钟）。任务可能仍在后台运行，稍后刷新活动详情即可看到结果。');}
      }
    }
    /* 标记「刚换版」：openActivity 据此在页头挂一条提示 + 「同步到 C 端」按钮。
       换版本身就写了库，所以这不是「数据没同步」，而是「把发布这件事显式做一次」。 */
    window.__clubSyncPending={id:Number(id),versionNo:r.versionNo};
    toast(`已生成第 ${r.versionNo} 版`);
    await openActivity(id);
  }catch(e){ showAlert({title:'换版失败',message:e.message}); }
  finally{ _regenDetailBusy=false; }
}
async function autoCover(id){try{const r=await api(`/api/club/${CLUB}/activities/${id}/cover/auto`,{method:'POST'});toast(r&&r.ref?`已选用素材 ${r.ref} 作封面`:'封面已自动设置');window.channelRenderResetCtx&&channelRenderResetCtx(Number(id));await openActivity(id);await loadActivities()}catch(e){showAlert({title:'没能自动选封面',message:e.message})}}
async function uploadCover(id){let f=$('#coverFile')?.files?.[0];if(!f){toast('请先选择一张图片');return}let fd=new FormData();fd.append('file',f);try{await api(`/api/club/${CLUB}/activities/${id}/cover`,{method:'POST',body:fd});toast('封面已更新');window.channelRenderResetCtx&&channelRenderResetCtx(Number(id));await openActivity(id);await loadActivities()}catch(e){showAlert({title:'操作失败',message:e.message})}}
$('#createForm').onsubmit=async e=>{
  e.preventDefault();let b=$('#genBtn');b.disabled=true;b.textContent='智能正在读资料、选图并排版…';
  try{
    let fd=new FormData(e.target);
    let r=await api(`/api/club/${CLUB}/activities/ai-generate`,{method:'POST',body:fd});
    /* live 模式改为异步任务：提交秒回 {jobId}，真模型生成要跑几分钟，同步等待会被
       网关空闲超时掐断（2026-09-28 实测：14MB 上传 11s 就过网关，死的是等响应）。
       mock 模式仍直接返回 activityId。两条路在此汇合。 */
    if(r.jobId){
      const started=Date.now();let done=false,misses=0;
      while(!done){
        await new Promise(res=>setTimeout(res,3000));
        let j=null;
        try{ j=await api(`/api/club/${CLUB}/activities/ai-generate/${r.jobId}`); misses=0; }
        catch(pe){ if(++misses>6)throw new Error('进度查询连续失败，请刷新页面稍后在活动列表查看结果（任务仍在后台运行）。'); continue; }
        const sec=Math.round((Date.now()-started)/1000);
        b.textContent=`智能正在生成（已等 ${sec} 秒，大资料约 1~5 分钟）…`;
        if(j.status==='done'){done=true;r={activityId:j.activityId,source:j.source||r.source};}
        else if(j.status==='failed'){throw new Error(j.error||'后台生成失败，已退还本次 智能 Credits（如未扣）。');}
        else if(Date.now()-started>10*60*1000){throw new Error('等待超时（10 分钟）。任务可能仍在后台运行，稍后刷新活动列表即可看到结果。');}
      }
    }
    modal('createModal',false);
    /* 反馈必须说清「系统读到了什么」。原来只报图片数：上传的 PPT 一个字都没读到时，
       图片数照样是 5，toast 还是绿油油的「已完成」，用户完全无法察觉方案没被读到。
       现在把「几份资料 / 多少字方案 / 几张图」一起报，字数为 0 时再补一条明确提示。 */
    let src=r.source||{},docs=(src.docFiles||[]).length,chars=Number(src.textLength||0),imgs=Number(src.imageCount||0);
    const _lnk=src.link;
    /* 贴了链接就必须说清「链接被读到了多少」，否则老板会怀疑系统根本没打开那条推文。 */
    toast(_lnk
      ? `活动详情已完成 · 链接《${(_lnk.title||_lnk.url||'').slice(0,16)}》读到 ${Number(_lnk.textLength||0)} 字 · ${Number(_lnk.imageCount||0)} 张配图`
      : `活动详情已完成 · ${docs} 份资料 · ${chars} 字方案 · ${imgs} 张图片`);
    /* 被跳过的文件必须当场说清：以前不支持的格式是静默丢弃的，老板传了几张 HEIC 手机照、
       系统只字不提，他只会以为「系统读错了文件」。 */
    const _skip=src.skippedFiles||[],_rerr=(src.readErrors||[]).map(x=>(x&&x.name)||'文件');
    if(_skip.length||_rerr.length){
      showAlert({title:'有文件没能读进去',message:
        (_rerr.length?`打不开：${_rerr.join('、')}（可能已损坏或加密）；`:'')
        +(_skip.length?`类型不支持，已跳过：${_skip.join('、')}（支持 PPT / Word / PDF / 文字文件；图片支持 JPG / PNG / WEBP / HEIC 手机照 / BMP / GIF。老版 .doc / .ppt / .wps 若总被跳过，请用 Office 或 WPS 另存为 .docx / .pptx 或 PDF）。`:'')
        +'其余资料已正常读取并用于生成。如果被跳过的是活动方案，请另存为 PDF 或 PPT 后重新上传。'});
    }
    await loadDash();go('activities');await openActivity(r.activityId);
    if(!chars)showAlert({title:'注意：这次没有读到方案文字',
      message:'系统只识别到照片，没有读到任何文字资料，所以活动名称 / 日期 / 地点 / 行程用的是通用兜底内容，不是你的方案。'
             +'建议重新上传带文字的方案（PPT 文字若做成图片则读不到），或直接在活动页点「编辑基本信息」补全。'});
  }catch(err){
    /* 裸「请求失败」= 响应不是我们的 JSON：请求半路被网关掐断（体积太大/超时/断网）。
       2026-09-28 实测：网关上传吞吐只有 ~20-60KB/s，十几张手机原图根本传不完，就死在这。
       必须翻译成人话，不然用户只会对着「请求失败」四个字干瞪眼。 */
    let msg=err.message||'';
    if(msg==='请求失败'||msg==='Failed to fetch')msg='资料没能完整传到服务器——通常是资料总体积太大、上传时间过长被中断。'
      +'大图片系统已会自动压缩，请检查是否有过大的文档（如几十 MB 的 PPT/PDF），删掉不必要的文件后重试；'
      +'也可以只保留方案文档先试一次，确认能生成后再补照片。';
    showAlert({title:'智能生成失败',message:msg});
  }
  finally{b.disabled=false;b.textContent='智能直接生成详情'}
};
/* 手工新建活动：不走 AI、不消耗 Credits 的第二条创建路径。只落活动事实，生成一份最小可渲染的
   master+detail（facts 区块），让 C 端与俱乐部端立刻能编辑、能加团期、能发布；发布前可随时改。
   走 uxForm 而非新建一个 HTML 弹窗：少一套弹窗层、复用同一套校验/焦点/保持逻辑。 */
async function createActivityManual(){
  const d=await uxForm({title:'手工新建活动',
    subtitle:'不调用智能、不消耗 Credits。先落活动事实，发布前可随时编辑；之后还能用智能重新生成文案。',
    fields:[
      {name:'title',label:'活动名称',type:'text',required:true,full:true,placeholder:'例如：10月24日 蓥华山轻徒步'},
      {name:'eventDate',label:'出发时间',type:'datetime',required:true,defaultTime:'08:00',help:'用于生成首个团期；只填日期时按 08:00 出发。'},
      {name:'location',label:'集合地 / 目的地',type:'text',placeholder:'如：成都 邛崃 兴福寺'},
      {name:'city',label:'活动城市（可选）',type:'select',options:[{value:'',label:'（暂不选 / 由地点推断）'}].concat((window.CLUBOS_CITIES||[]).map(c=>({value:c,label:c}))),value:''},
      {name:'price',label:'活动价格（元）',type:'number',min:0,step:.01,value:0},
      {name:'capacity',label:'总名额（人）',type:'number',min:0,step:1,value:20},
      {name:'summary',label:'一句话介绍（可选）',type:'textarea',full:true,placeholder:'例如：轻装穿越竹林与寺观，适合入门徒步。'}
    ],submitText:'创建草稿'});
  if(!d)return;
  try{
    const r=await api(`/api/club/${CLUB}/activities`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({title:d.title,eventDate:d.eventDate,location:d.location,city:d.city,price:d.price,capacity:d.capacity,summary:d.summary})});
    toast('已创建草稿，可继续编辑后发布');
    await loadDash();go('activities');await openActivity(r.activityId);await loadActivities();
  }catch(e){showAlert({title:'创建失败',message:e.message})}
}
/* 分享活动：给老板一条能直接发出去的链接 + 一张二维码。顾客扫码/点开就走 C 端活动详情。
   二维码用本地 QR 库（/static/ux/qr-local.js 的 ClubOSQR.draw 画到 canvas），不依赖外部网络。 */
async function shareActivity(id){
  let a=currentActivity;
  if(!a||Number(a.id)!==Number(id)){try{a=await api(`/api/club/${CLUB}/activities/${id}`)}catch(e){showAlert({title:'读取活动失败',message:e.message});return}}
  if(a.status!=='published'){showAlert({title:'活动尚未发布',message:'发布后顾客才能打开这条链接。先点「发布活动」再分享。'});return}
  const url=location.origin+`/web?club_id=${CLUB}&activity=${id}`;
  const cap=`【${a.title}】${a.event_date||''} ${a.location||''}${a.price?(' '+money(a.price)):''}\n报名看详情：${url}`;
  const ov=uxDialog({title:'分享活动',desc:'扫码或复制链接，发到微信 / 朋友圈。',body:`
    <div class="ux-share">
      <canvas class="ux-share-qr" aria-label="活动二维码"></canvas>
      <code class="ux-share-link">${esc(url)}</code>
      <div class="ux-share-cap" id="shareCap">${esc(cap)}</div>
    </div>`,foot:'<button class="btn" type="button" id="shareCopyLink">复制链接</button><button class="btn secondary" type="button" id="shareCopyCap">复制文案</button><button class="btn ghost" type="button" id="shareDl">下载二维码</button><button class="btn ghost" type="button" data-ok>关闭</button>'});
  const canvas=ov.querySelector('.ux-share-qr');
  try{window.ClubOSQR.draw(canvas,url);canvas.setAttribute('aria-label','活动二维码')}
  catch(e){canvas.hidden=true}
  const doCopy=async(text,label)=>{
    try{await navigator.clipboard.writeText(text);toast(label+'已复制到剪贴板')}
    catch(e){
      const ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';
      document.body.appendChild(ta);ta.select();let ok=false;try{ok=document.execCommand('copy')}catch(_){}
      ta.remove();toast(ok?label+'已复制到剪贴板':label+'复制失败，请手动选中文本复制');
    }
  };
  ov.querySelector('#shareCopyLink').onclick=()=>doCopy(url,'链接');
  ov.querySelector('#shareCopyCap').onclick=()=>doCopy(cap,'文案');
  ov.querySelector('#shareDl').onclick=()=>{
    try{const a2=document.createElement('a');a2.href=canvas.toDataURL('image/png');
      a2.download=(String(a.title||'活动').slice(0,20))+'.png';a2.click();}
    catch(e){toast('下载失败，可长按/截图保存二维码')}
  };
  return ov;
}
/* ===== 智能内容中心：生成 → 直接出成品（公众号图文 / 小红书卡片 / 海报）=====
   过去 genChannel 把接口返回的 JSON 塞进 <pre>，老板拿到的是一堆代码。
   现在交给 static/channel-render.js 渲染成能直接用的成品，并可复制 / 下载。 */
/* ===== 活动选择器（智能内容中心）=====
   老板原话：「如果以后活动多了，也一个一个选太难选了。」
   原生 <select> 在几十场活动时只能一行行翻、还搜不了。这里换成 combobox：
   按钮显示当前活动 → 弹出「搜索 + 状态筛选 + 可滚动列表」，
   ↑↓ 移动 / Enter 确认 / Esc 关闭，鼠标点击同样生效，并标出哪些活动已生成过内容。 */
const ActPick={acts:[],counts:{},host:null,q:'',filter:'all',idx:0,open:false};
const ACT_PICK_ORDER=['published','draft','archived','cancelled'];
/* 浏览器可能还缓存着旧版 HTML（那里是 <select id="contentActivity">）。
   取不到新容器时就地换成 div，避免出现「后台改好了、用户看到的还是旧的」。 */
function actPickHost(){
  const host=$('#contentActivityPick');
  if(host)return host;
  const legacy=$('#contentActivity');
  if(!legacy)return null;
  if(legacy.tagName==='SELECT'){
    const d=document.createElement('div');d.className='act-pick';d.id='contentActivityPick';
    legacy.replaceWith(d);return d;
  }
  return legacy;
}
function actPickStatuses(acts){
  const seen=[];
  acts.forEach(a=>{const s=String(a.status||'');if(s&&!seen.includes(s))seen.push(s)});
  return seen.sort((x,y)=>{const ix=ACT_PICK_ORDER.indexOf(x),iy=ACT_PICK_ORDER.indexOf(y);return (ix<0?99:ix)-(iy<0?99:iy)});
}
function actPickFiltered(){
  const q=ActPick.q.trim().toLowerCase();
  return ActPick.acts.filter(a=>{
    if(ActPick.filter!=='all'&&String(a.status||'')!==ActPick.filter)return false;
    if(!q)return true;
    return [a.title,a.location,a.event_date,a.status,enumCn(a.status),'#'+a.id]
      .filter(Boolean).join(' ').toLowerCase().includes(q);
  });
}
/* 高亮关键词：先转义再拼 <mark>，用户输入不会被当成标签执行。 */
function actPickHi(text,q){
  const t=String(text==null?'':text);
  if(!q)return esc(t);
  const i=t.toLowerCase().indexOf(q);
  if(i<0)return esc(t);
  return esc(t.slice(0,i))+'<mark class="act-pick__hit">'+esc(t.slice(i,i+q.length))+'</mark>'+esc(t.slice(i+q.length));
}
function actPickMeta(a){
  return [a.event_date,a.location,a.price?money(a.price):''].filter(Boolean).join(' · ');
}
function actPickItemHtml(a,selected){
  const q=ActPick.q.trim().toLowerCase();
  const n=Number(ActPick.counts[a.id]||0);
  return `<div class="act-pick__item${a.id===ActPick.idx?' active':''}" id="actPickItem${a.id}" role="option" aria-selected="${a.id===selected?'true':'false'}" data-id="${a.id}">`
    +`<span class="act-pick__item__main"><span class="act-pick__itemTitle">${actPickHi(a.title,q)}</span>`
    +`<span class="act-pick__itemSub">${actPickHi(actPickMeta(a)||'未填日期 / 地点',q)}</span></span>`
    +`<span class="act-pick__itemTags"><span class="tag${a.status==='draft'?' orange':''}">${esc(enumCn(a.status,'')||'未设置')}</span>${n?`<span class="tag">已有 ${n} 条内容</span>`:''}</span></div>`;
}
function actPickRenderList(){
  const host=ActPick.host; if(!host)return;
  const list=actPickFiltered(), selected=Number(window.__contentActId||0);
  const box=host.querySelector('#actPickList');
  if(!box)return;
  const q=ActPick.q.trim();
  if(!list.length){
    box.innerHTML=`<div class="act-pick__empty">没有匹配的活动${q?`（关键词「${esc(q)}」）`:''}<br><span class="sub">换个关键词，或点上面的「全部」。</span></div>`;
  }else{
    if(!list.some(a=>a.id===ActPick.idx))ActPick.idx=list[0].id;
    box.innerHTML=list.map(a=>actPickItemHtml(a,selected)).join('');
  }
  host.querySelectorAll('.act-pick__filters button').forEach(b=>b.classList.toggle('on',b.dataset.f===ActPick.filter));
  const count=host.querySelector('#actPickCount');
  if(count)count.textContent=`共 ${ActPick.acts.length} 场 · 显示 ${list.length}`;
  const input=host.querySelector('#actPickSearch');
  if(input)input.setAttribute('aria-activedescendant',list.length?('actPickItem'+ActPick.idx):'');
  const active=box.querySelector('.act-pick__item.active');
  if(active&&active.scrollIntoView)active.scrollIntoView({block:'nearest'});
}
function actPickMount(host,acts,counts){
  actPickClose();
  ActPick.host=host; ActPick.acts=acts||[]; ActPick.counts=counts||{};
  if(!ActPick.acts.length){host.innerHTML='<div class="act-pick__btn" style="cursor:default;color:var(--muted)">还没有活动</div>';return}
  const cur=ActPick.acts.find(a=>a.id===Number(window.__contentActId||0))||ActPick.acts[0];
  const sts=actPickStatuses(ActPick.acts), cnt={};
  ActPick.acts.forEach(a=>{const s=String(a.status||'');cnt[s]=(cnt[s]||0)+1});
  host.innerHTML=`<button type="button" class="act-pick__btn" id="actPickBtn" aria-haspopup="listbox" aria-expanded="false">`
    +`<span class="act-pick__value" id="actPickBtnLabel">${esc(cur.title)}</span>`
    +`<span class="act-pick__meta">${esc(actPickMeta(cur))}</span>`
    +`<span class="tag${cur.status==='draft'?' orange':''}">${esc(enumCn(cur.status,'')||'未设置')}</span>`
    +`<span class="act-pick__caret" aria-hidden="true">▼</span></button>`
    +`<div class="act-pick__pop" id="actPickPop" hidden>`
      +`<div class="act-pick__search"><span aria-hidden="true">🔍</span>`
      +`<input id="actPickSearch" type="search" role="combobox" aria-expanded="false" aria-controls="actPickList" aria-autocomplete="list" placeholder="搜索活动名称 / 地点 / 日期" autocomplete="off" aria-label="搜索活动">`
      +`<span class="act-pick__count" id="actPickCount"></span></div>`
      +`<div class="act-pick__filters" id="actPickFilters" role="group" aria-label="按状态筛选">`
      +`<button type="button" data-f="all" class="on">全部 ${ActPick.acts.length}</button>`
      +sts.map(s=>`<button type="button" data-f="${esc(s)}">${esc(enumCn(s))} ${cnt[s]}</button>`).join('')
      +`</div>`
      +`<div class="act-pick__list" id="actPickList" role="listbox" aria-label="活动列表"></div>`
      +`<div class="act-pick__hint"><kbd>↑</kbd><kbd>↓</kbd> 移动 · <kbd>Enter</kbd> 确认 · <kbd>Esc</kbd> 关闭 · 直接打字即搜索</div>`
    +`</div>`;
  const btn=host.querySelector('#actPickBtn');
  const input=host.querySelector('#actPickSearch');
  btn.onclick=()=>{ActPick.open?actPickClose():actPickOpen()};
  input.oninput=()=>{ActPick.q=input.value;ActPick.idx=0;actPickRenderList()};
  host.querySelector('#actPickFilters').onclick=e=>{
    const b=e.target.closest('button[data-f]'); if(!b)return;
    ActPick.filter=b.dataset.f;ActPick.idx=0;actPickRenderList();
  };
  host.querySelector('#actPickList').onclick=e=>{
    const it=e.target.closest('.act-pick__item'); if(!it)return;
    actPickChoose(Number(it.dataset.id));
  };
  host.onkeydown=e=>{
    if(!ActPick.open){
      if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();actPickOpen()}
      return;
    }
    if(e.key==='Escape'){e.preventDefault();e.stopPropagation();actPickClose();btn.focus();return}
    if(e.key==='ArrowDown'){e.preventDefault();actPickMove(1);return}
    if(e.key==='ArrowUp'){e.preventDefault();actPickMove(-1);return}
    if(e.key==='Enter'){e.preventDefault();if(ActPick.idx)actPickChoose(ActPick.idx)}
  };
  actPickRenderList();
}
function actPickOpen(){
  const host=ActPick.host; if(!host||ActPick.open)return;
  const pop=host.querySelector('#actPickPop'), btn=host.querySelector('#actPickBtn'), input=host.querySelector('#actPickSearch');
  if(!pop||!input)return;
  ActPick.open=true; ActPick.q=''; input.value='';
  const list=actPickFiltered();
  const sel=list.find(a=>a.id===Number(window.__contentActId||0));
  ActPick.idx=sel?sel.id:(list.length?list[0].id:0);
  pop.hidden=false; btn.setAttribute('aria-expanded','true'); input.setAttribute('aria-expanded','true');
  actPickRenderList();
  input.focus();
  document.addEventListener('click',actPickDocClick,true);
}
function actPickClose(){
  const host=ActPick.host; if(!host||!ActPick.open)return;
  ActPick.open=false;
  const pop=host.querySelector('#actPickPop'), btn=host.querySelector('#actPickBtn'), input=host.querySelector('#actPickSearch');
  if(pop)pop.hidden=true;
  if(btn)btn.setAttribute('aria-expanded','false');
  if(input)input.setAttribute('aria-expanded','false');
  document.removeEventListener('click',actPickDocClick,true);
}
function actPickDocClick(e){
  if(!ActPick.host||!ActPick.open)return;
  if(!ActPick.host.contains(e.target))actPickClose();
}
function actPickMove(step){
  const list=actPickFiltered(); if(!list.length)return;
  let i=list.findIndex(a=>a.id===ActPick.idx);
  if(i<0)i=step>0?-1:0;
  ActPick.idx=list[(i+step+list.length)%list.length].id;
  actPickRenderList();
}
function actPickChoose(id){
  id=Number(id)||0; if(!id)return;
  actPickClose();
  window.__contentActId=id;
  loadContent();
}
async function loadContent(){
  skel('#channelArea',2);
  try{
  const [acts,content]=await Promise.all([
    api(`/api/club/${CLUB}/activities`),
    api(`/api/club/${CLUB}/content`).catch(()=>[])
  ]);
  const pickHost=actPickHost();
  if(!acts.length){
    if(pickHost)pickHost.innerHTML='<div class="act-pick__btn" style="cursor:default;color:var(--muted)">还没有活动</div>';
    $('#channelArea').innerHTML='<div class="empty">先创建一场活动，再让智能做公众号 / 小红书 / 海报 / 长图等宣发内容。</div>';
    // 活动回顾不依赖系统里的活动：即使一场活动都还没建，也能直接填信息生成回顾
    renderRecapPanel();
    $('#contentList').innerHTML='<div class="empty">还没有渠道内容</div>';
    return;
  }
  // 每场活动已生成过多少条内容：选择器里标出来，老板一眼能找到上次做到哪。
  const counts={};
  (content||[]).forEach(x=>{counts[x.activity_id]=(counts[x.activity_id]||0)+1});
  const want=Number(window.__contentActId||0);
  const keep=acts.some(a=>a.id===want)?want:acts[0].id;
  window.__contentActId=keep;
  if(pickHost)actPickMount(pickHost,acts,counts);
  const a=acts.find(x=>x.id===keep)||acts[0];
  currentActivity=a;
  const cards=[
    ['longpic','智能宣传长图','按活动主题与照片自由编排长图，每场版式不同，可预览、复制和下载高清图片'],
    ['wechat','微信公众号图文','智能重排公众号阅读节奏；生成后可直接预览，并一键复制带格式图文到公众号编辑器'],
    ['xhs','小红书图文','文案为主：独立标题、Hook、正文与话题标签可直接复制走；另出一张 1080×1440 首页海报，并把其余现场照片裁成 3:4 九宫格配图'],
    ['poster','活动招募海报','用活动真实封面 + 智能文案在本地合成 1080×1440 海报，可直接下载'],
    ['hero','智能主视觉海报','零上传也能生成一张符合活动调性的户外主视觉，自动设为活动封面，详情页与长图直接采用']
  ];
  $('#channelArea').innerHTML=cards.map(x=>`<div class="card channel-card"><div><strong>${x[1]}</strong><p>${x[2]}</p><div class="sub">当前活动：${esc(a.title)}</div></div><button class="btn secondary" onclick="genOrHero('${x[0]}',${a.id})">智能生成</button></div>`).join('');
  renderRecapPanel();
  // 已生成内容里补上「这是哪场活动的」——以前只显示 活动 #12，活动一多就认不出来。
  // activity_id<=0 的是「独立回顾」（不绑定系统活动，见 2026-10-30 解耦），单独标注。
  const byId={}; acts.forEach(x=>{byId[x.id]=x});
  $('#contentList').innerHTML=(content||[]).map(x=>{const t=byId[x.activity_id];const cur=Number(x.activity_id)===Number(window.__contentActId);
    const isStandalone=Number(x.activity_id)<=0;
    const actLabel=isStandalone?'独立回顾':(t?`活动：${esc(t.title)}`:`活动 #${x.activity_id}`);
    return `<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.title||channelLabel(x.channel))}</div><div class="list-row__sub">${actLabel} · ${esc(x.created_at||'')}</div></div><div class="list-row__end"><span class="tag">${esc(channelLabel(x.channel))}</span>${!isStandalone&&t&&!cur?`<button class="btn ghost" onclick="actPickChoose(${x.activity_id})">切到这场</button>`:''}<button class="btn secondary" onclick="openContentAsset(${x.id})">查看成品</button><button class="btn ghost" style="color:var(--danger,#b54644)" title="删除这条已生成内容" onclick="deleteContentAsset(${x.id})">删除</button></div></div>`}).join('')
    ||'<div class="empty">还没有渠道内容</div>';
  }catch(e){loaderError('#channelArea',e,'内容中心加载失败')}
}
/* 删除一条已生成的宣发内容（过期物料清理，2026-10-09 用户提出）。
   前端 uxConfirm 危险确认 + 后端按 club 归属硬闸；只删内容记录本身，
   活动、报名、积分账本一概不动。 */
async function deleteContentAsset(assetId){
  if(!await uxConfirm({title:'删除这条内容',danger:true,confirmText:'确认删除',
    message:'该条已生成的宣传物料将删除，不可撤销；只删这条内容记录，活动与报名数据不受影响。'}))return;
  await uxFlow('deleteContentAsset',async()=>{
    try{await api(`/api/club/${CLUB}/content/${assetId}`,{method:'DELETE'})}
    catch(e){showAlert({title:'删除失败',message:e.message});return}
    toast('内容已删除');
    await loadContent();
  });
}
function channelLabel(c){return (window.ChannelRender&&ChannelRender.label(c))||c}
async function genOrHero(ch,id){ if(ch==='hero'){await aiGenerateHero(id);return;} return genChannel(ch,id); }
/* AI 一键生成活动主视觉（通义万相，复用总平台已配的 DashScope Key）。
   零上传也能出图：解决「没照片就撑不起图文」；生成后自动设为活动封面，
   详情页封面大图 / 长图 / promoHtml 都直接采用。 */
async function aiGenerateHero(id){
  if(window.uxConfirm&&!await uxConfirm({title:'智能生成主视觉海报',confirmText:'开始生成',
    message:'系统会读取这场活动的资料（标题 / 地点 / 调性），调用通义万相生成一张户外主视觉，约需 10-30 秒，并自动设为活动封面。需总平台已在「智能接入」里配置 DashScope / 通义万相 的 Key。'}))return;
  try{
    await api(`/api/club/${CLUB}/activities/${id}/generate-hero`,{method:'POST'});
    toast('主视觉生成中，约 10-30 秒');
    const started=Date.now();
    while(Date.now()-started<4*60*1000){
      await new Promise(r=>setTimeout(r,3000));
      let st; try{st=await api(`/api/club/${CLUB}/activities/${id}/generate-hero/status`)}catch(_e){continue}
      if(st.status==='done'){
        toast('主视觉已生成并设为封面');
        _showHeroModal(st.imageUrl);
        window.channelRenderResetCtx&&channelRenderResetCtx(Number(id));
        try{await openActivity(id)}catch(_e){}
        await loadActivities();
        return;
      }
      if(st.status==='error'){ throw new Error(st.error||'生成失败'); }
    }
    throw new Error('生成超时，请稍后在内容中心重试');
  }catch(e){ showAlert({title:'生成主视觉失败',message:e.message}) }
}
/* 轻量模态：展示生成好的主视觉，并提示已设为封面。 */
function _showHeroModal(url){
  const id='heroModal'; document.getElementById(id)&&document.getElementById(id).remove();
  const m=document.createElement('div'); m.id=id;
  m.style.cssText='position:fixed;inset:0;background:rgba(20,24,22,.72);display:flex;align-items:center;justify-content:center;z-index:9999;padding:24px';
  m.innerHTML='<div style="background:#fff;border-radius:12px;max-width:420px;width:100%;overflow:hidden;box-shadow:0 12px 40px rgba(0,0,0,.3)">'
    +'<img src="'+url+'?t='+Date.now()+'" style="width:100%;display:block" onerror="this.parentNode.innerHTML=\'<div style=&quot;padding:24px;color:#b54644&quot;>图片加载失败，但已设为封面</div>\'">'
    +'<div style="padding:14px 16px;font-size:14px;color:#28342D">已自动设为活动封面，详情页、长图与宣传区都会采用这张主视觉。</div></div>';
  m.onclick=(e)=>{ if(e.target===m) m.remove(); };
  document.body.appendChild(m);
}
async function genChannel(ch,id){
  try{
    // 生成会真花 智能 Credits，动手前必须讲清楚。本地渲染成品（复制 / 下载）不再另计费。
    // 长图会多跑一次「图片识别」：每张照片先让视觉模型写一句画面描述、判掉地图截图和
    // 别的活动的照片，否则写作模型是盲选图（实测会把路线地图选成首屏大图）。费用按
    // 公众号同一档计，所以这里把这件事对老板讲明白，而不是事后解释。
    const msg = ch==='longpic'
      ? '系统会读取这场活动的真实资料并调用智能：先识别每张照片的内容（剔除地图截图与不属于本活动的照片），再由模型自行排版成一张 750px 长图。过程中产生 智能 Credits 计费，生成后可直接预览、下载 2 倍图、复制图文。'
      : '系统会读取这场活动的真实资料并调用一次智能，产生一次 智能 Credits 计费。生成后直接给成品，可以复制 / 下载。';
    if(window.uxConfirm&&!await uxConfirm({title:'生成'+channelLabel(ch)+'内容',
      message:msg,confirmText:'开始生成'}))return;
    const d=await api(`/api/club/${CLUB}/activities/${id}/channel/${ch}`,{method:'POST'});
    if(ch==='longpic'&&d&&d.pending){
      // 长图异步化（2026-10-11）：生成要 1~4 分钟，同步等必被网关 504。立即返回，
      // 前端轮询 status，完成后自动打开成品；期间页面可正常使用。
      toast('长图已开始生成，约需 1-3 分钟，完成后自动打开，可先做别的');
      const started=Date.now();
      while(Date.now()-started<8*60*1000){
        await new Promise(r=>setTimeout(r,8000));
        let st;
        try{st=await api(`/api/club/${CLUB}/activities/${id}/channel/longpic/status`)}
        catch(_e){continue}
        if(st.status==='done'){
          toast('长图生成完成');
          const full=await api(`/api/club/${CLUB}/content/${st.assetId}`);
          await openChannelOutput('longpic',full.content||{},{activityId:Number(id)});
          await loadContent();
          if(window.loadCredits)await loadCredits();
          return;
        }
        if(st.status==='error'){showAlert({title:'生成失败',message:st.error||'请重试'});return}
      }
      showAlert({title:'仍在生成中',message:'这次生成时间较长，后台不会中断；稍后到「智能内容中心 → 已生成内容」里查看成品。'});
      await loadContent();
      return;
    }
    toast('内容已生成，正在渲染成品…');
    await openChannelOutput(ch,d,{activityId:Number(id)});
    await loadContent();
    if(window.loadCredits)await loadCredits();
  }catch(e){showAlert({title:'生成失败',message:e.message})}
}
async function openContentAsset(assetId){
  try{
    const d=await api(`/api/club/${CLUB}/content/${assetId}`);
    await openChannelOutput(d.channel,d.content||{},{activityId:Number(d.activity_id)});
  }catch(e){showAlert({title:'打开失败',message:e.message})}
}
/* ===== 活动回顾（2026-10-30 解耦：不绑定系统里的活动）=====
   老板的新逻辑：俱乐部管理人员直接输入关键活动信息（名称/日期/地点/规模）+ 现场照片 + 基础文案，
   系统就自动生成回顾图文，不必先在系统里建一场「已结束的活动」。
   素材门槛不变（至少 1 张现场照片或 1 段现场记录）——回顾只写真事。 */
function renderRecapPanel(){
  const host=$('#recapBody');if(!host)return;
  host.innerHTML=`
    
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;max-width:760px">
      <div><div class="sub" style="margin-bottom:4px">活动名称 *</div><input id="recapTitle" style="width:100%;padding:8px 10px" placeholder="例如：神农架三天两夜徒步"></div>
      <div><div class="sub" style="margin-bottom:4px">活动日期</div><input id="recapDate" style="width:100%;padding:8px 10px" placeholder="例如：2026年10月2日—4日"></div>
      <div><div class="sub" style="margin-bottom:4px">活动地点</div><input id="recapLoc" style="width:100%;padding:8px 10px" placeholder="例如：湖北神农架"></div>
      <div><div class="sub" style="margin-bottom:4px">活动规模</div><input id="recapScale" style="width:100%;padding:8px 10px" placeholder="例如：12 人小队"></div>
    </div>
    <div style="margin-top:12px"><div class="sub" style="margin-bottom:4px">现场照片</div>
    <input type="file" id="recapFiles" multiple accept="image/*,.heic,.heif,.bmp,.gif,.tif,.tiff,.avif"></div>
    <div style="margin-top:12px"><div class="sub" style="margin-bottom:4px">基础文案 / 领队记录</div>
    <textarea id="recapNote" rows="4" style="width:100%;max-width:680px;padding:8px 10px" placeholder="例如：12 个人全员登顶，山顶风大但出了太阳；下山路上有人扭了脚踝已处理；晚饭火锅全员到齐……"></textarea></div>
    <div style="margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <button class="btn secondary" id="recapGo">智能生成活动回顾</button>
      <button class="btn secondary" id="recapRetypeset" style="display:none">换一版排版（用同一批照片）</button>
      <span class="sub" id="recapHint"></span>
    </div>`;
  const fi=$('#recapFiles');
  fi.onchange=()=>{$('#recapHint').textContent=fi.files.length?`已选 ${fi.files.length} 张照片`:''};
  $('#recapGo').onclick=genRecap;
  $('#recapRetypeset').onclick=()=>window.clubRetypesetRecap&&window.clubRetypesetRecap({});
  if(window.__lastRecapAssetId)$('#recapRetypeset').style.display='';
}
async function genRecap(){
  const title=($('#recapTitle')?.value||'').trim();
  if(!title){toast('请先填写活动名称');$('#recapTitle')?.focus();return}
  const event_date=($('#recapDate')?.value||'').trim();
  const location=($('#recapLoc')?.value||'').trim();
  const scale=($('#recapScale')?.value||'').trim();
  const note=($('#recapNote')?.value||'').trim();
  const files=[...($('#recapFiles')?.files||[])];
  if(!files.length&&!note){
    showAlert({title:'还缺素材',message:'回顾只能写真正发生过的事：请至少上传 1 张现场照片，或填写几句现场记录。'});
    return;
  }
  const mat=[files.length?`${files.length} 张现场照片`:'',note?'你填写的现场记录':''].filter(Boolean).join(' 和 ');
  if(window.uxConfirm&&!await uxConfirm({title:'生成活动回顾',
    message:`系统会结合你填写的活动信息${mat?'、'+mat:''}调用智能生成回顾图文，产生一次 智能 Credits 计费。生成后可直接预览、复制、下载。`,
    confirmText:'开始生成'}))return;
  const fd=new FormData();
  fd.append('title',title);
  if(event_date)fd.append('event_date',event_date);
  if(location)fd.append('location',location);
  if(scale)fd.append('scale',scale);
  fd.append('note',note);
  files.forEach(f=>fd.append('files',f));
  try{
    // 解耦版端点：不传活动 id，后端用自由输入的最小 master 直接生成
    const d=await api(`/api/club/${CLUB}/recap`,{method:'POST',body:fd});
    // 生成异步化（与长图同理）：live 真模型带完整写作契约常超 55s 网关上限，
    // 立即返回轮询 status，完成后自动打开成品；期间页面可正常使用。
    if(d&&d.pending){toast('回顾已开始生成，约需 1-2 分钟，完成后自动打开，可先做别的');await waitRecapAndOpen();return}
    // 兼容：mock/旧实现直接同步返回成品
    if(d&&d.skippedFiles&&d.skippedFiles.length)toast(`有 ${d.skippedFiles.length} 个文件不是可识别的图片，已跳过`);
    toast('活动回顾已生成');
    window.channelRenderResetCtx&&channelRenderResetCtx(0);
    await openChannelOutput('recap',d,{activityId:0});
    await loadContent();
    if(window.loadCredits)await loadCredits();
  }catch(e){showAlert({title:'生成失败',message:e.message})}
}
/* 轮询独立回顾作业 → 自动打开成品。首次生成与「换一版排版」共用，避免两套轮询各自漂移。 */
async function waitRecapAndOpen(){
  const started=Date.now();
  while(Date.now()-started<8*60*1000){
    await new Promise(r=>setTimeout(r,6000));
    let st;
    try{st=await api(`/api/club/${CLUB}/recap/status`)}catch(_e){continue}
    if(st.status==='done'){
      toast('活动回顾生成完成');
      if(st.skipped&&st.skipped.length)toast(`有 ${st.skipped.length} 个文件不是可识别的图片，已跳过`);
      window.__lastRecapAssetId=st.assetId;
      const rs=$('#recapRetypeset');if(rs)rs.style.display='';
      const full=await api(`/api/club/${CLUB}/content/${st.assetId}`);
      window.channelRenderResetCtx&&channelRenderResetCtx(0);
      await openChannelOutput('recap',full.content||{},{activityId:0});
      await loadContent();
      if(window.loadCredits)await loadCredits();
      return true;
    }
    if(st.status==='error'){showAlert({title:'生成失败',message:st.error||'请重试'});return false}
  }
  showAlert({title:'仍在生成中',message:'这次生成时间较长，后台不会中断；稍后到「已生成内容」里查看成品。'});
  await loadContent();
  return false;
}
/* 成品弹层 / 回顾面板里的「换一版排版」：用同一批素材重跑，只换版式、不要求重新上传照片。 */
window.clubRetypesetRecap=async function(opts){
  const assetId=window.__lastRecapAssetId||0;
  if(!assetId){showAlert({title:'还不能换版',message:'请先生成一份活动回顾，再点「换一版排版」。'});return}
  if(window.uxConfirm&&!await uxConfirm({title:'换一版排版',
    message:'用同一批现场照片和记录重新生成，只换版式、不换素材，会产生一次 智能 Credits 计费。',
    confirmText:'重新排版'}))return;
  try{
    const fd=new FormData();fd.append('asset_id',String(assetId));
    const d=await api(`/api/club/${CLUB}/recap/retypeset`,{method:'POST',body:fd});
    if(!d||!d.pending){showAlert({title:'换版失败',message:'服务没有接受这次请求，请稍后重试。'});return}
    if(opts&&typeof opts.close==='function')opts.close();   // 关掉旧成品，别叠两层弹窗
    toast('正在重新排版，约 1-2 分钟，完成后自动打开新版本');
    await waitRecapAndOpen();
  }catch(e){showAlert({title:'换版失败',message:e.message})}
};
/* ===== 报名与执行：以单个活动为基础，再按档期（团期）拆开 =====
   老板的原话：把所有档期汇总到一起，后台就分不清哪些人是 9 号的、哪些人是 10 号的。
   所以第一层是活动，点进去后每个档期一块：这块只列它自己的报名人 + 它自己的执行准备。 */
let _regData=null;
async function loadRegs(){
  skel('#regRows',6);
  try{
  let [acts,regs,occs,refunds]=await Promise.all([
    api(`/api/club/${CLUB}/activities`),
    api(`/api/club/${CLUB}/registrations`),
    api(`/api/club/${CLUB}/execution/occurrences`),
    api(`/api/club/${CLUB}/refunds`)
  ]);
  _regData={acts,regs,occs,refunds};
  window.__regsActs=acts; window.__regsRegs=regs; window.__regsOccs=occs;
  const s=$('#regSearch'); if(s&&!s.__wired){s.__wired=true;s.oninput=()=>renderRegRows()}
  renderRegRows();
  renderRefundReview(refunds);
  }catch(e){loaderError('#regRows',e,'报名与执行加载失败')}
}
function regPcount(x){return Number(x.active_participants||x.participant_count||1)}
function regCash(rl){return rl.filter(x=>x.status==='paid'||x.status==='completed').reduce((s,x)=>s+Number(x.amount||0),0)}
function renderRegRows(){
  const box=$('#regRows'); if(!box||!_regData)return;
  const {acts,regs,occs}=_regData;
  const q=($('#regSearch')?.value||'').trim().toLowerCase();
  const regByAct={},occByAct={};
  regs.forEach(x=>{(regByAct[x.activity_id]=regByAct[x.activity_id]||[]).push(x)});
  occs.forEach(o=>{(occByAct[o.activity_id]=occByAct[o.activity_id]||[]).push(o)});
  const list=acts.filter(a=>!q||[a.title,a.location,a.event_date].filter(Boolean).join(' ').toLowerCase().includes(q));
  const cnt=$('#regCount'); if(cnt)cnt.textContent=`${list.length} 场活动 · ${regs.length} 笔报名`;
  box.innerHTML=list.map(a=>{
    const rs=regByAct[a.id]||[], os=occByAct[a.id]||[];
    const parts=rs.reduce((s,x)=>s+regPcount(x),0);
    const pInfo=rs.reduce((s,x)=>s+Math.max(0,regPcount(x)-Number(x.complete_participants||0)),0);
    const ins=rs.reduce((s,x)=>s+Number(x.insurance_pending||0),0);
    const rf=rs.filter(x=>x.refund_status==='requested').length;
    const exec=os.length?os.map(o=>execStateLabel[o.execution_status||'preparing']||o.execution_status).join(' / '):'未排期';
    const execChip=os.length?`<span class="tag ${os.every(o=>o.execution_status==='completed')?'':'orange'}">执行：${esc(exec)}</span>`:`<span class="tag">未排期</span>`;
    return `<div class="list-row" style="cursor:pointer" onclick="openActivityRegs(${a.id})"><div class="list-row__main"><div class="list-row__title">${esc(a.title)}</div><div class="list-row__sub">${esc(a.event_date||'')} · ${esc(a.location||'')} · ${money(a.price)}</div><div class="list-row__sub"><b>${os.length} 个档期</b> · 报名 ${rs.length} 笔 / ${parts} 人参加 · 实收 ${money(regCash(rs))}${pInfo?` · 资料待补 ${pInfo}`:''}${ins?` · 保险 ${ins}`:''}${rf?` · 待审退款 ${rf}`:''}</div></div><div class="list-row__end"><span class="tag ${a.status==='draft'?'orange':''}">${esc(enumCn(a.status))}</span>${execChip}<span class="sub">按档期 ›</span></div></div>`;
  }).join('')||`<div class="empty">${q?'没有匹配的活动':'还没有活动，智能发一场。'}</div>`;
}
function renderRefundReview(refunds){
  const box=$('#refundReviewList'); if(!box)return;
  const list=Array.isArray(refunds)?refunds:[];
  box.innerHTML=list.map(r=>(r.kind==='gear'||r.managed_by==='platform')?gearRefundRow(r):`<div class="notice" style="margin-bottom:10px"><div class="panel-title"><div><strong>${esc(r.activity_title)}</strong> · ${r.refund_scope==='participant'?`参加人 ${esc(r.participant_name||'')}`:`付款人 ${esc(r.user_name)}`}<div class="sub">${esc(r.occurrence_label||r.start_at||'')} · ${r.refund_scope==='participant'?'单人退款':'整单退款'} · 原现金 ${money(r.original_cash_amount||r.paid_cash)} · 本次退 ${money(r.cash_amount)}${r.refund_percent!=null?`（${Number(r.refund_percent)}%）`:''}${Number(r.retained_cash_amount||0)>0?` · 取消费 ${money(r.retained_cash_amount)}`:''}</div></div><span class="tag ${r.status==='requested'?'orange':''}">${esc(enumCn(r.status,'待处理'))}</span></div><div class="sub">规则：${esc(r.policy_label||'—')} · 原因：${esc(r.reason||'')}${r.refund_scope==='participant'?` · 分摊活动积分 ${r.allocated_club_points||0} / 装备积分 ${r.allocated_gear_points||0}`:''}</div>${r.status==='requested'?`<div style="display:flex;gap:8px;margin-top:10px"><button class="btn secondary" onclick="approveRefund('${r.id}')">同意退款</button><button class="btn ghost" onclick="rejectRefund('${r.id}')">拒绝</button></div>`:''}${r.status==='processing'?`<div class="sub" style="margin-top:8px">已审核通过，等待支付渠道退款。</div><div style="display:flex;gap:8px;margin-top:8px"><button class="btn secondary" onclick="retryRefundProvider('${r.id}')">重试退款到账</button></div>`:''}${r.status==='rejected'&&r.decision_note?`<div class="sub" style="margin-top:8px">审核说明：${esc(r.decision_note)}</div>`:''}</div>`).join('')||'<div class="empty">暂无退款申请</div>'
}

function gearRefundRow(r){
  const scope=r.refund_scope==='after_sales'?'售后部分退款':'整单退款';
  const goods=r.gear_titles?` · 商品 ${esc(r.gear_titles)}`:'';
  return `<div class="notice" style="margin-bottom:10px"><div class="panel-title"><div><strong>${esc(r.activity_title||'装备订单')}</strong> · 付款人 ${esc(r.user_name||'')}<div class="sub">${esc(r.created_at||'')} · ${scope} · 原现金 ${money(r.paid_cash||r.original_cash_amount||0)} · 本次退 ${money(r.cash_amount||0)}${goods}</div></div><span class="tag ${r.status==='requested'?'orange':''}">${esc(enumCn(r.status,'待处理'))}</span></div><div class="sub">装备退款由总平台审核处理，本端仅可查看（只读） · 原因：${esc(r.reason||'')}${r.after_sales_case_id?` · 售后单 ${esc(r.after_sales_case_id)}`:''}</div></div>`;
}
async function showParticipants(regId){
  try{let d=await api(`/api/club/${CLUB}/registrations/${regId}/participants`);let el=document.createElement('div');el.className='modal show';el.innerHTML=`<div class="modal-box"><div class="modal-head"><div><div class="eyebrow">PARTICIPANTS</div><h2 style="margin:4px 0">参加人名单</h2></div><button class="x">×</button></div>${(d.participants||[]).map(p=>`<div class="notice" style="margin-bottom:10px;${p.status==='refunded'?'opacity:.6':''}"><strong>${esc(p.name)}</strong> · ${esc(p.phone||'')} <span class="tag">${p.status==='refunded'?'已退出':`资料${p.form_status==='complete'?'完整':'待补'}`}</span><div class="sub">证件 ${esc(p.id_type||'—')} ${esc(p.id_number||'—')} · 紧急联系人 ${esc(p.emergency_contact_name||'—')} ${esc(p.emergency_contact_phone||'')}</div><div class="sub">保险：${esc(p.insurance_status||'pending')} ${esc(p.insurance_provider||'')} ${esc(p.insurance_policy_no||'')} · 退款 ${esc(p.refund_status||'none')}</div>${p.status!=='refunded'?`<button class="btn ghost" style="margin-top:7px" onclick="clubInsurance(${p.id},${regId})">更新保险</button>`:''}</div>`).join('')||'<div class="empty">暂无参加人</div>'}</div>`;el.querySelector('.x').onclick=()=>el.remove();el.onclick=e=>{if(e.target===el)el.remove()};document.body.appendChild(el)}catch(e){showAlert({title:'操作失败',message:e.message})}
}


async function loadMembers(){skel('#tierList',3);
  try{
  let [a,tiers,benefits,reds,gd,pe,orders,rules,mall,mallReds,tierGifts,inviteReward,buyGifts,grantLog]=await Promise.all([
    api(`/api/club/${CLUB}/members`),api(`/api/club/${CLUB}/membership/tiers`),
    api(`/api/club/${CLUB}/benefits`),api(`/api/club/${CLUB}/benefits/redemptions`),
    api(`/api/club/${CLUB}/gear-discount`),
    api(`/api/club/${CLUB}/membership/purchase-enabled`).catch(()=>({enabled:false})),
    api(`/api/club/${CLUB}/member-orders`).catch(()=>[]),
    api(`/api/club/${CLUB}/coupon-gift-rules`).catch(()=>[]),
    api(`/api/club/${CLUB}/points-mall`).catch(()=>[]),
    api(`/api/club/${CLUB}/points-mall/redemptions`).catch(()=>[]),
    api(`/api/club/${CLUB}/tier-reward-rules`).catch(()=>[]),
    api(`/api/club/${CLUB}/invite-reward`).catch(()=>({settings:{enabled:0},pool:[],records:[],stats:{}})),
    api(`/api/club/${CLUB}/campaigns`).catch(()=>[]),
    api(`/api/club/${CLUB}/campaign-grant-log`).catch(()=>[])
  ]);
  _inviteReward=inviteReward||{settings:{enabled:0},pool:[],records:[],stats:{}};
  $('#memberRows').innerHTML=a.map(x=>`<tr class="member-row" onclick="openMember(${x.user_id})"><td>${esc(x.name)}</td><td><strong>${esc(x.level)}</strong></td><td>${x.activity_count||0}</td><td>${money(x.lifetime_activity_spend||0)}</td><td>${x.club_points_balance}</td><td>${x.gear_points}</td><td>${esc(x.phone||'')}</td><td><span class="sub">查看详情 ›</span></td></tr>`).join('')||'<tr><td colspan="8" class="empty">暂无会员</td></tr>';
  _tiers=tiers;_gearDiscount=gd||null;
  _tierRewards=tierGifts||[];
  _clubBenefits=(benefits||[]).filter(x=>x.owner_type==='CLUB'&&x.benefit_type==='activity_coupon'&&String(x.status||'active')==='active');
  const peBox=document.getElementById('purchaseEnabled'); if(peBox)peBox.checked=!!(pe&&pe.enabled);
  /* 2026-10-23：等级不再含装备折扣（由总平台按订阅套餐统一控制）。
     2026-10-27（用户要求）：条件开通与购买开通彻底分开 —— 「等级会员」卡只列条件型，
     「付费会员卡」卡只列购买型，两边各有自己的＋按钮与编辑弹窗，不再混排。 */
  const isBuy=x=>String(x.access_mode||'CONDITION')==='PURCHASE';
  const zheTag=adp=>adp>0?` <span class="tag info">活动 ${(adp/10).toFixed(1).replace(/\.0$/,'')} 折</span>`:' <span class="sub" style="margin-left:6px">活动不打折</span>';
  const rateTag=v=>Number(v||0)>0?` <span class="tag info">积分 ${Number(v)}:1</span>`:'';
  const rewardSub=x=>{
    const r=tierRewardOf(x.id); const txt=tierRewardText(r);
    return txt?`<div class="list-row__sub">升级奖励：${esc(txt)}</div>`:'';
  };
  const condRows=tiers.filter(x=>!isBuy(x));
  const cardRows=tiers.filter(isBuy);
  $('#tierList').innerHTML=condRows.map(x=>{
    const adp=Number(x.activity_discount_percent||0);
    return `<div class="list-row"><div class="list-row__main">
      <div class="list-row__title">${esc(x.name)} <span class="tag">Rank ${x.rank}</span>${zheTag(adp)}${rateTag(x.points_redeem_rate)}${x.status!=='active'?' <span class="tag orange">已停用</span>':''}</div>
      <div class="list-row__sub">累计活动消费 ≥ ${money(x.min_activity_spend)} · 活动次数 ≥ ${x.min_activity_count} · ${x.qualification_mode==='ALL'?'同时满足':'任一满足'}</div>
      ${rewardSub(x)}</div>
      <div class="list-row__end"><button class="btn ghost" onclick="editTier(${x.id})">编辑</button></div></div>`;
  }).join('')||'<div class="empty">还没有等级会员，点右上「＋ 等级」创建</div>';
  $('#cardList').innerHTML=cardRows.map(x=>{
    const adp=Number(x.activity_discount_percent||0);
    return `<div class="list-row"><div class="list-row__main">
      <div class="list-row__title">${esc(x.name)} <span class="tag">¥${Number(x.price||0).toFixed(2).replace(/\.00$/,'')} · ${x.valid_months||12} 个月</span>${zheTag(adp)}${rateTag(x.points_redeem_rate)}${x.status!=='active'?' <span class="tag orange">已停用</span>':''}</div>
      <div class="list-row__sub">购买开通 · 到期自动失效</div>
      ${rewardSub(x)}</div>
      <div class="list-row__end"><button class="btn ghost" onclick="editTierCard(${x.id})">编辑</button></div></div>`;
  }).join('')||'<div class="empty">还没有会员卡，勾选右上「开通」后点「＋ 会员卡」创建</div>';
  /* 升级奖励：按等级列一遍，方便集中核对（配置入口在等级编辑里，避免两处入口打架） */
  $('#tierRewardList').innerHTML=(tiers||[]).slice().sort((a,b)=>Number(a.rank)-Number(b.rank)).map(x=>{
    const r=tierRewardOf(x.id); const txt=tierRewardText(r);
    const buy=isBuy(x);
    return `<div class="list-row"><div class="list-row__main">
      <div class="list-row__title">${esc(x.name)} <span class="tag">${buy?'付费会员卡':'等级会员'}</span>${r?`<span class="tag ${r.enabled?'info':'orange'}">${r.enabled?'已启用':'已停用'}</span>`:''}</div>
      <div class="list-row__sub">${txt?esc(txt):'<span class="sub">未设置奖励</span>'}</div></div>
      <div class="list-row__end"><button class="btn ghost" onclick="${buy?'editTierCard':'editTier'}(${x.id})">配置</button></div></div>`;
  }).join('')||'<div class="empty">还没有会员等级，先在左边创建等级，再回来配奖励</div>';
  const couponText=x=>{
    if(x.benefit_type!=='activity_coupon')return esc(x.benefit_type==='service'?'非现金服务福利':(x.benefit_type||'福利'));
    const m=String(x.mode||'fixed');
    if(m==='discount')return `${Number(x.discount_percent||0)/10} 折活动券${x.threshold_amount>0?`（满 ${money(x.threshold_amount)} 可用）`:''}`;
    if(m==='threshold')return `满 ${money(x.threshold_amount)} 减 ${money(x.cash_value)}`;
    return `活动直抵 ${money(x.cash_value)}`;
  };
  renderCouponCenter(benefits);
  $('#giftRuleList').innerHTML=(rules||[]).map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">单笔消费满 <b>${money(x.threshold_amount)}</b> 送「${esc(x.benefit_title||'')}」${x.enabled?'':' <span class="tag orange">已停用</span>'}</div><div class="list-row__sub">每人限送 ${x.per_user_limit} 次 · 券库存 ${x.benefit_stock==null?'不限':x.benefit_stock}${x.enabled&&x.benefit_stock!=null&&Number(x.benefit_stock)<=0?' · <b>库存为 0，暂不会触发赠送</b>':''}</div></div><div class="list-row__end"><button class="btn ghost" onclick="toggleGiftRule(${x.id},${x.enabled?0:1})">${x.enabled?'停用':'启用'}</button><button class="btn ghost" onclick="delGiftRule(${x.id})">删除</button></div></div>`).join('')||'<div class="empty">还没有送券规则</div>';
  /* 升级奖励已并入等级编辑（2026-10-27），上面的 tierRewardList 只是汇总视图。 */
  /* #858 优惠邀请 · 邀请奖励：三 Tab 渲染（基本设置 / 分享设置 / 邀请记录） */
  renderInviteReward();
  renderBuyGiftCampaigns(buyGifts);
  renderCampaignGrantLog(grantLog);
  $('#mallItemList').innerHTML=(mall||[]).map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.title)} <span class="tag">${esc(x.typeName||x.item_type)}</span>${x.status!=='active'?' <span class="tag orange">已下架</span>':''}</div><div class="list-row__sub">${x.points_cost} 活动积分 · 库存 ${x.stock==null?'不限':x.stock}${x.item_type==='coupon'&&x.refTitle?` · 关联券「${esc(x.refTitle)}」`:''}${x.description?` · ${esc(x.description)}`:''}</div></div><div class="list-row__end"><button class="btn ghost" onclick="editMallItem(${x.id})">编辑</button><button class="btn ghost" onclick="delMallItem(${x.id})">删除</button></div></div>`).join('')||'<div class="empty">还没有兑换商品，点「＋ 兑换商品」上架</div>';
  $('#memberOrderList').innerHTML=(orders||[]).map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.user_name||'用户')} · 「${esc(x.tier_name)}」会员卡 <span class="tag">${money(x.amount)}</span></div><div class="list-row__sub">有效期 ${esc((x.valid_from||'').slice(0,10))} 至 ${esc((x.valid_to||'').slice(0,10))} · ${x.months} 个月 · 订单 #${x.id}</div></div></div>`).join('')||'<div class="empty">还没有购买记录（开通「购买会员」并为等级设置购买开通后，顾客即可在 C 端购买）</div>';
  $('#clubBenefitRedemptions').innerHTML=reds.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.user_name)} · ${esc(x.title)}</div><div class="list-row__sub">${x.points_spent?`${x.points_spent} 活动积分 · `:''}${x.funding_owner==='CLUB'?'俱乐部承担':'平台承担'} · ${esc(x.voucher_code||'')}${x.gift_note?` · ${esc(x.gift_note)}`:''}</div></div></div>`).join('')
    +(mallReds||[]).map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.user_name||'顾客')} · 积分商城「${esc(x.item_title)}」<span class="tag">${x.status==='verified'?'已核销':'待核销'}</span></div><div class="list-row__sub">${x.points_spent} 活动积分 · ${esc(String(x.created_at||'').slice(0,10))} · 核销码 <b>${esc(x.code||'')}</b>${x.verified_at?` · 核销于 ${esc(String(x.verified_at).slice(0,16))}`:''}</div></div></div>`).join('')
    ||'<div class="empty">暂无兑换记录</div>';
  }catch(e){loaderError('#tierList',e,'客户与会员加载失败')}
}
/* ── 营销活动（P5/P6 营销引擎：立减 / 限时 / 买赠 / 邀请，成交自动生效与发放，幂等留痕）── */
function discountRuleText(r){
  r=r||{};
  const mode=String(r.mode||'fixed');
  if(mode==='percent')return `${(Number(r.percent||0)/10).toString().replace(/\.0$/,'')} 折`;
  return `立减 ${money(r.amount)}`;
}
function buyGiftRuleText(r){
  r=r||{};
  const gt=String(r.giftType||'points');
  const v=Number(r.giftValue||0);
  const unit=gt==='points'?`${v} 活动积分`:(gt==='gift_card'?`${money(v)} 礼品卡额度`:`${money(v)} 优惠券（自动生成）`);
  const mg=Number(r.maxGifts||0)>0?` · 单笔最多 ${r.maxGifts} 份`:'';
  return `满 ${money(r.threshold)} 送 ${unit}${mg}`;
}
/* V6：把 schedule 翻译成人话。空 schedule 明确写「长期生效」，不再让
   「限时折扣」名不副实；所有时段按北京时间 UTC+8 判定，与后端引擎同源。 */
function campaignScheduleText(s){
  const o=s||{},bits=[];
  const fmt=v=>String(v||'').replace('T',' ').slice(0,16);
  const mm=n=>{const v=Number(n||0);return String(Math.floor(v/60)).padStart(2,'0')+':'+String(v%60).padStart(2,'0')};
  if(o.startAt)bits.push('从 '+fmt(o.startAt));
  if(o.endAt)bits.push('至 '+fmt(o.endAt));
  if(Array.isArray(o.weekdays)&&o.weekdays.length)bits.push('每周 '+o.weekdays.map(d=>'一二三四五六日'[Number(d)]||d).join('/'));
  if(o.timeFrom||o.timeTo)bits.push('每日 '+(o.timeFrom!==undefined&&o.timeFrom!==null?mm(o.timeFrom):'00:00')+'–'+(o.timeTo!==undefined&&o.timeTo!==null?mm(o.timeTo):'23:59'));
  return bits.length?bits.join(' · ')+'（北京时间）':'未设时段 · 长期生效';
}
function campaignTypeMeta(t){
  return ({
    NEWCOMER:['首单立减','',x=>`首单 ${discountRuleText(x.rules)}（仅本俱乐部无已支付订单的新顾客）`],
    TIME_LIMIT:['限时折扣','info',x=>`${discountRuleText(x.rules)}（启用期间全部顾客可用）`],
    BUY_GIFT:['满额买赠','',x=>`${buyGiftRuleText(x.rules)}（成交自动发放）`],
    INVITE:['邀请有礼','info',x=>`${buyGiftRuleText(x.rules)}（仅被邀请好友的首单）`]
  })[t];
}
function renderBuyGiftCampaigns(list){
  const box=$('#buyGiftList'); if(!box)return;
  const rows=(Array.isArray(list)?list:[]).filter(x=>campaignTypeMeta(x.type));
  box.innerHTML=rows.map(x=>{
    const [tag,cls,ruleFn]=campaignTypeMeta(x.type);
    const sched=x.type==='TIME_LIMIT'?' · 时段：'+esc(campaignScheduleText(x.schedule)):'';
    return `<div class="list-row"><div class="list-row__main"><div class="list-row__title"><span class="tag ${cls}">${tag}</span> ${esc(x.title)} <span class="tag">${esc(ruleFn(x)||'')}</span>${x.status==='ACTIVE'?'':' <span class="tag orange">已停用</span>'}</div><div class="list-row__sub">适用：俱乐部活动 + 营地（装备订单不参与）· 发放与立减幂等留痕${sched}</div></div><div class="list-row__end"><button class="btn ghost" onclick="toggleCampaign(${x.id},'${x.status==='ACTIVE'?'PAUSED':'ACTIVE'}')">${x.status==='ACTIVE'?'停用':'启用'}</button><button class="btn ghost" onclick="delCampaign(${x.id})">删除</button></div></div>`;
  }).join('')||'<div class="empty">还没有营销活动，点「＋ 立减 / 限时」或「＋ 买赠 / 邀请」创建</div>';
}
function grantTypeCn(t){return ({points:'活动积分',gift_card:'礼品卡额度',coupon:'优惠券'})[t]||t;}
function renderCampaignGrantLog(log){
  const box=$('#campaignGrantLog'); if(!box)return;
  const rows=Array.isArray(log)?log:[];
  const cnt=$('#grantLogCount'); if(cnt)cnt.textContent=`${rows.length} 条`;
  box.innerHTML=rows.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.user_name||('用户 #'+x.user_id))} · ${esc(x.campaign_title||('活动 #'+x.campaign_id))} <span class="tag">${esc(grantTypeCn(x.gift_type))} × ${x.copies||1}</span></div><div class="list-row__sub">${esc(String(x.created_at||'').slice(0,16).replace('T',' '))} · 订单 ${esc(x.order_id)} · 成交时已自动入账</div></div></div>`).join('')||'<div class="empty">暂无发放记录 —— 规则创建后，顾客成交即自动发放并在这里留痕。</div>';
}
/* ── 券中心（#856 对齐 Word 三型券：满减 / 折扣 / 商品兑换）── */
window.cpnSwitch=tab=>{
  document.querySelectorAll('#cpnTabs .cpn-tab').forEach(b=>b.classList.toggle('active',b.dataset.cpn===tab));
  ['threshold','discount','redeem'].forEach(m=>{const p=document.getElementById('cpnPanel-'+m);if(p)p.style.display=m===tab?'':'none';});
};
/* ── 券中心（#860）：券与积分解耦，列表列同步去掉「门店 / 就餐 / 微信投放」等餐饮语义 ── */
function couponScopeCn(x){
  const base=({all:'活动 + 营地通用',activity:'仅限活动报名',camp:'仅限营地消费',product:'指定商品',category:'指定分类'})[x.applies_to]||'活动 + 营地通用';
  const k=({activity:'活动',camp:'营地',pitch:'营位',product:'商品',category:'分类',
    camp_product:'营地商品',activity_category:'活动分类'})[x.scope_ref_kind]||'';
  return k&&x.scope_ref_id?(base+' · #'+x.scope_ref_id):base;
}
function couponStoreCn(x){return couponScopeCn(x||{});}   /* 兼容历史调用点，语义已换成适用范围 */
function couponDcTypeCn(t){return ({single:'单次折扣券',stored:'储值折扣券'})[t]||'单次折扣券';}
function couponClaimCn(m){return ({grant:'手动赠送',auto:'自动领取',code:'兑换码领取'})[m]||'手动赠送';}
function couponValidCn(x){
  if(x.validity_type==='relative')return `领券后 ${Number(x.valid_days||0)} 天内可用`;
  const a=(x.valid_from||'').slice(0,10),b=(x.valid_to||'').slice(0,10);
  if(a&&b)return `${a} 至 ${b}`;
  if(a)return `自 ${a} 起`;
  if(b)return `至 ${b}`;
  return '长期有效';
}
function couponCampCn(x){return x.camp_scope==='specified'?'仅指定营地':'全部营地';}
function couponAudienceCn(x){
  const a=({all:'所有人',member:'仅会员',non_member:'仅非会员',new_user:'仅新用户'})[x.claim_audience]||'所有人';
  return a+(x.new_user_only?' · 仅新用户':'')+(x.per_user_daily_limit?(' · 每日 '+x.per_user_daily_limit+' 张'):'');
}
function couponStock(x){return x.stock==null?'不限':x.stock;}
function couponOps(x){return `<button class="btn ghost" onclick="editCoupon(${x.id})">编辑</button><button class="btn ghost" onclick="couponDetail(${x.id})">详情</button><button class="btn ghost danger" onclick="delCoupon(${x.id})">删除</button>`;}

/* ── #858 优惠邀请 · 邀请奖励：三 Tab（基本设置 / 分享设置 / 邀请记录）── */
let _inviteReward={settings:{enabled:0},pool:[],records:[],stats:{}};
const INVITE_IMG_KEYS=['sunrise','forest','alpine','dusk','sand','mono'];
function inviteTab(tab){
  document.querySelectorAll('#inviteTabs .seg-btn').forEach(b=>b.classList.toggle('active',b.dataset.itab===tab));
  ['basic','share','records'].forEach(t=>{const p=document.getElementById('invitePanel-'+t);if(p)p.style.display=t===tab?'':'none';});
}
function inviteCouponLabel(b){
  if(!b)return '未设置';
  const m=String(b.mode||'fixed');
  if(m==='discount')return `${b.title}（${Number(b.discount_percent||0)/10} 折）`;
  if(m==='threshold')return `${b.title}（满 ${money(b.threshold_amount)} 减 ${money(b.cash_value)}）`;
  return `${b.title}（直抵 ${money(b.cash_value)}）`;
}
function renderInviteReward(){
  const s=_inviteReward.settings||{},pool=_inviteReward.pool||[],recs=_inviteReward.records||[],st=_inviteReward.stats||{};
  const tag=document.getElementById('inviteRewardTag');
  if(tag)tag.textContent=Number(s.enabled)?'已开启':'未开启';
  const eb=pool.find(x=>Number(x.id)===Number(s.invitee_benefit_id));
  const ib=pool.find(x=>Number(x.id)===Number(s.inviter_benefit_id));
  // 基本设置
  $('#invitePanel-basic').innerHTML=`
    <div class="list-row"><div class="list-row__main"><div class="list-row__title">模块状态</div><div class="list-row__sub">${Number(s.enabled)?'已开启：邀请链路按下方配置双向发券':'未开启：不自动发券（降级沿用旧「拉新」设定）'}</div></div><div class="list-row__end"><b>${Number(s.enabled)?'开启中':'已关闭'}</b></div></div>
    <div class="list-row"><div class="list-row__main"><div class="list-row__title">被邀请人券</div><div class="list-row__sub">好友凭邀请码注册后，自动领到这张券</div></div><div class="list-row__end">${esc(inviteCouponLabel(eb))}</div></div>
    <div class="list-row"><div class="list-row__main"><div class="list-row__title">邀请人奖励券</div><div class="list-row__sub">${ib?'邀请人得这张（可与被邀请人不同）':'未单独设置 → 与被邀请人同券'}</div></div><div class="list-row__end">${esc(ib?inviteCouponLabel(ib):(eb?'同被邀请人券':'未设置'))}</div></div>
    <div class="list-row"><div class="list-row__main"><div class="list-row__title">邀请人额外活动积分</div><div class="list-row__sub">每成功邀请 1 人，邀请人再得的活动积分（100 分 = ¥1）</div></div><div class="list-row__end"><b>${Number(s.inviter_points||0)}</b> 分</div></div>
    <div class="sub" style="margin-top:10px">累计邀请 ${Number(st.total||0)} 人 · 发出被邀请人券 ${Number(st.invitee_coupons||0)} 张 · 邀请人券 ${Number(st.inviter_coupons||0)} 张 · 奖励积分 ${Number(st.points_awarded||0)} 分</div>`;
  // 分享设置
  $('#invitePanel-share').innerHTML=`
    <div class="list-row"><div class="list-row__main"><div class="list-row__title">分享卡片标题</div></div><div class="list-row__end">${esc(s.share_title||'（使用默认标题）')}</div></div>
    <div class="list-row"><div class="list-row__main"><div class="list-row__title">分享卡片副文案</div></div><div class="list-row__end">${esc(s.share_desc||'（未设置）')}</div></div>
    <div class="list-row"><div class="list-row__main"><div class="list-row__title">分享卡配图</div></div><div class="list-row__end"><b>${(s.share_image_key||'sunrise')}</b></div></div>
    `;
  // 邀请记录
  $('#invitePanel-records').innerHTML=recs.length?`<table class="table"><thead><tr><th>邀请人</th><th>被邀请人</th><th>邀请码</th><th>被邀请人券</th><th>邀请人券</th><th>积分</th><th>时间</th></tr></thead><tbody>${recs.map(r=>`<tr><td>${esc(r.inviter_name||'')}</td><td>${esc(r.invitee_name||'')}</td><td><b>${esc(r.invite_code||'')}</b></td><td>${r.invitee_redemption_id?'已发':'—'}</td><td>${r.inviter_redemption_id?'已发':'—'}</td><td>${Number(r.inviter_points_awarded||0)}</td><td class="sub">${esc(String(r.created_at||'').slice(0,16))}</td></tr>`).join('')}</tbody></table>`
    :'<div class="empty">还没有邀请记录。开启模块并让会员分享邀请码后即可看到。</div>';
}
window.inviteRewardEdit=async which=>{
  const s=_inviteReward.settings||{},pool=_inviteReward.pool||[];
  if(!pool.length){showAlert({title:'先创建优惠券',message:'被邀请人券 / 邀请人奖励券都必须是本俱乐部创建的活动优惠券，请先在「俱乐部营销 · 优惠券」里建券并备好库存。'});return}
  const opt0=[{value:0,label:'不送券'}];
  const opts=opt0.concat(pool.map(b=>({value:b.id,label:`${b.title} · 库存 ${b.stock==null?'不限':b.stock}`})));
  if(which==='share'){
    const d=await uxForm({title:'邀请奖励 · 分享设置',subtitle:'配置 C 端「邀请好友」页的分享卡样式。',fields:[
      {name:'shareTitle',label:'分享卡片标题',full:true,value:s.share_title||'',placeholder:'例如：邀好友，一起出发'},
      {name:'shareDesc',label:'分享卡片副文案',full:true,value:s.share_desc||'',placeholder:'例如：注册即得券，报名立减'},
      {name:'shareImageKey',label:'分享卡配图',type:'select',full:true,value:s.share_image_key||'sunrise',options:INVITE_IMG_KEYS.map(k=>({value:k,label:k}))}
    ],submitText:'保存分享设置'});
    if(!d)return;
    await uxFlow('inviteRewardShare',async()=>{
      await api(`/api/club/${CLUB}/invite-reward`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!!Number(s.enabled),inviteeBenefitId:Number(s.invitee_benefit_id||0),inviterBenefitId:Number(s.inviter_benefit_id||0),inviterPoints:Number(s.inviter_points||0),shareTitle:d.shareTitle,shareDesc:d.shareDesc,shareImageKey:d.shareImageKey})});
      toast('分享设置已保存');await loadMembers();
    });
    return;
  }
  const d=await uxForm({title:'邀请奖励 · 基本设置',subtitle:'被邀请人注册领券；邀请人得奖励券（可不同）+ 额外活动积分。',fields:[
    {name:'enabled',label:'开启邀请奖励',type:'select',full:true,value:Number(s.enabled)?1:0,options:[{value:1,label:'开启'},{value:0,label:'关闭（不自动发券）'}]},
    {name:'inviteeBenefitId',label:'被邀请人券',type:'select',required:true,full:true,options:opts.map(o=>({value:o.value,label:o.label})),value:Number(s.invitee_benefit_id||0)},
    {name:'inviterBenefitId',label:'邀请人奖励券（留 0 = 与被邀请人同券）',type:'select',full:true,options:opts.map(o=>({value:o.value,label:o.label})),value:Number(s.inviter_benefit_id||0)},
    {name:'inviterPoints',label:'邀请人额外活动积分',type:'number',min:0,step:1,value:Number(s.inviter_points||0),help:'每成功邀请 1 人，邀请人额外获得的活动积分（100 分 = ¥1）'}
  ],submitText:'保存基本设置'});
  if(!d)return;
  await uxFlow('inviteRewardBasic',async()=>{
    await api(`/api/club/${CLUB}/invite-reward`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!!Number(d.enabled),inviteeBenefitId:Number(d.inviteeBenefitId||0),inviterBenefitId:Number(d.inviterBenefitId||0),inviterPoints:Number(d.inviterPoints||0),shareTitle:s.share_title||'',shareDesc:s.share_desc||'',shareImageKey:s.share_image_key||'sunrise'})});
    toast(Number(d.enabled)?'邀请奖励已开启':'邀请奖励已关闭');await loadMembers();
  });
};
function couponEmpty(txt){return `<div class="empty">${txt}</div>`;}
function renderCouponCenter(benefits){
  const cp=(benefits||[]).filter(x=>x.owner_type==='CLUB'&&x.benefit_type==='activity_coupon');
  const byMode=m=>cp.filter(x=>String(x.mode||'fixed')===m);
  const t=$('#couponList-threshold'),d=$('#couponList-discount'),r=$('#couponList-redeem');
  if(t)t.innerHTML=renderThresholdList(byMode('threshold'));
  if(d)d.innerHTML=renderDiscountList(byMode('discount'));
  if(r)r.innerHTML=renderRedeemList(byMode('fixed'));
}
function renderThresholdList(rows){
  if(!rows.length)return couponEmpty('还没有满减优惠券，点上方「＋ 添加满减优惠券」创建');
  return `<table class="table"><thead><tr><th>ID</th><th>名称</th><th>使用门槛</th><th>适用范围</th><th>多营地适用</th><th>有效期</th><th>可领取人群</th><th>领取数</th><th>操作</th></tr></thead><tbody>`
    +rows.map(x=>`<tr><td>${x.id}</td><td>${esc(x.title)}${x.status!=='active'?' <span class="tag orange">已下架</span>':''}</td>`
      +`<td>满 ${money(x.threshold_amount)} 减 ${money(x.cash_value)}</td>`
      +`<td>${esc(couponScopeCn(x))}</td>`
      +`<td>${couponCampCn(x)}</td>`
      +`<td>${esc(couponValidCn(x))}</td>`
      +`<td>${esc(couponAudienceCn(x))}</td>`
      +`<td>${x.claimed_count!=null?x.claimed_count:'—'}</td>`
      +`<td class="row-ops">${couponOps(x)}</td></tr>`).join('')+`</tbody></table>`;
}
function renderDiscountList(rows){
  if(!rows.length)return couponEmpty('还没有折扣券，点上方「＋ 添加折扣券」创建');
  return `<table class="table"><thead><tr><th>ID</th><th>名称</th><th>券类型</th><th>使用门槛</th><th>折扣率(%)</th><th>适用范围</th><th>有效期</th><th>可领取人群</th><th>剩余/库</th><th>操作</th></tr></thead><tbody>`
    +rows.map(x=>`<tr><td>${x.id}</td><td>${esc(x.title)}${x.status!=='active'?' <span class="tag orange">已下架</span>':''}</td>`
      +`<td>${couponDcTypeCn(x.discount_coupon_type)}</td>`
      +`<td>${Number(x.threshold_amount)>0?`满 ${money(x.threshold_amount)} 可用`:'无门槛'}</td>`
      +`<td>${Number(x.discount_percent||0)}% <span class="cpn-sub">（${(Number(x.discount_percent||0)/10).toFixed(1)} 折${x.max_discount_amount?` · 最高抵 ${money(x.max_discount_amount)}`:''}）</span></td>`
      +`<td>${esc(couponScopeCn(x))}</td>`
      +`<td>${esc(couponValidCn(x))}</td>`
      +`<td>${esc(couponAudienceCn(x))}</td>`
      +`<td>${couponStock(x)}</td>`
      +`<td class="row-ops">${couponOps(x)}</td></tr>`).join('')+`</tbody></table>`;
}
function renderRedeemList(rows){
  if(!rows.length)return couponEmpty('还没有商品兑换券，点上方「＋ 添加商品兑换券」创建');
  return `<table class="table"><thead><tr><th>ID</th><th>名称</th><th>券价值</th><th>适用范围</th><th>有效期</th><th>可领取人群</th><th>领取数</th><th>剩余/库存</th><th>操作</th></tr></thead><tbody>`
    +rows.map(x=>`<tr><td>${x.id}</td><td>${esc(x.title)}${x.status!=='active'?' <span class="tag orange">已下架</span>':''}</td>`
      +`<td>${money(x.cash_value)}</td>`
      +`<td>${esc(couponScopeCn(x))}</td>`
      +`<td>${esc(couponValidCn(x))}</td>`
      +`<td>${esc(couponAudienceCn(x))}</td>`
      +`<td>${x.claimed_count!=null?x.claimed_count:'—'}</td>`
      +`<td>${couponStock(x)}</td>`
      +`<td class="row-ops">${couponOps(x)}</td></tr>`).join('')+`</tbody></table>`;
}
async function recalcMembership(){await api(`/api/club/${CLUB}/membership/recalculate`,{method:'POST'});toast('会员等级已重新计算');loadMembers()}

/* 会员深度管理（2026-10-22 用户截图反馈：「点进去之后，应该查看到所有相关该用户的信息」）。
   详情弹窗用 uxDialog 自定义 body：个人信息 / 积分 / 历史订单 / 优惠券（可赠）/ 积分流水。
   2026-10-23 起积分面板只允许调「活动积分」：装备积分是总平台体系积分（平台承担成本），
   俱乐部无权调整 —— 界面上干脆不给入口，只读展示并标注「平台管理」。
   调积分与赠券两个写流程在 workflows-club.js（adjustMemberPoints / giftMemberVoucher），
   成功后回调 renderMemberDetail 原位刷新，不叠弹窗。 */
window._memberDetailOv=null;
async function openMember(userId){
  const ov=uxDialog({title:'会员详情',wide:true,body:'<div class="empty">正在加载会员资料…</div>',
    foot:'<button class="btn ghost" type="button" data-close>关闭</button>'});
  ov.querySelector('[data-close]').onclick=()=>ov.uxClose();
  window._memberDetailOv=ov;
  await renderMemberDetail(ov,userId);
}
async function renderMemberDetail(ov,userId){
  const body=ov.querySelector('.ux-dialog-body'); if(!body)return;
  body.innerHTML='<div class="empty">正在加载会员资料…</div>';
  let d;
  try{ d=await api(`/api/club/${CLUB}/members/${userId}`); }
  catch(e){ body.innerHTML=`<div class="empty">${esc(e.message||'加载失败')}</div>`; return; }
  const m=d.member||{};
  const regStatusCn={paid:'已支付',refunded:'已退款',cancelled:'已取消',pending:'待支付',shipped:'已发货',completed:'已完成',refunding:'退款中',processing:'处理中'};
  const refundCn={requested:'退款审核中',processing:'退款中',succeeded:'已退款',rejected:'退款被拒',partial_requested:'部分退款申请中',partial_processing:'部分退款处理中',partial:'部分退款'};
  const ledTypeCn={earn:'获得',redeem:'抵扣',hold:'锁定',restore:'返还',benefit_redeem:'福利兑换',manual_adjust:'手工调整'};
  const voucherStatusCn={issued:'可用',held:'占用中',used:'已使用',expired:'已过期',redeemed:'已核销'};
  const actOrders=d.activityOrders||[],gearOrders=d.gearOrders||[],vouchers=d.vouchers||[];
  const clubLedger=d.clubLedger||[],gearLedger=d.gearLedger||[];
  const ledRow=(l,label)=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${label} ${l.amount>0?'+':''}${l.amount}</div><div class="list-row__sub">${ledTypeCn[l.type]||esc(l.type)} · ${esc(l.note||'')} · ${esc((l.created_at||'').slice(0,16))}</div></div></div>`;
  body.innerHTML=`
    <div class="grid g2" style="align-items:start">
      <div class="card">
        <h3>${esc(m.name||'')} <span class="tag">${esc(m.level||'非会员')}</span></h3>
        <div class="sub">${esc(m.phone||'未留手机')} · 注册于 ${esc((m.user_created_at||'').slice(0,10))} · 用户 #${m.user_id}</div>
        <div class="sub" style="margin-top:6px">${d.tier?`当前等级「${esc(d.tier.name||'')}」`:'暂未达到任何等级规则'} · 活动 ${m.activity_count||0} 次 · 累计消费 ${money(m.lifetime_activity_spend||0)}</div>
      </div>
      <div class="card">
        <div class="panel-title"><h3>积分</h3><span class="sub">调整会写入积分流水</span></div>
        <div class="pts-panel">
          <div class="pts-main">
            <div class="pts-main__meta">
              <div class="pts-label">活动积分</div>
              <div class="pts-hint">本俱乐部体系内使用 · 报名付款可直接抵扣</div>
            </div>
            <div class="pts-main__num">${m.club_points_balance||0}</div>
            <button class="btn secondary pts-btn" onclick="adjustMemberPoints(${m.user_id},'club')">调整</button>
          </div>
          <div class="pts-sub">
            <div class="pts-sub__meta">
              <div class="pts-label">装备积分 <span class="pts-lock">🔒 平台管理</span></div>
              <div class="pts-hint">ClubOS 平台体系 · 由总平台按订单发放与调整，俱乐部无权修改</div>
            </div>
            <div class="pts-sub__num">${m.gear_points||0}</div>
          </div>
        </div>
      </div>
    </div>
    <div class="card section">
      <div class="panel-title"><h3>历史订单 · 活动</h3><span class="tag">${actOrders.length} 笔</span></div>
      ${actOrders.map(o=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(o.title||'')}</div><div class="list-row__sub">${esc(o.event_date||'')} · ${esc(o.location||'')}</div><div class="list-row__sub">实付 ${money(o.amount||0)} · 参加人 ${(o.participants||[]).length} 人${(o.participants||[]).length?`（${o.participants.map(p=>esc(p.name)).join('、')}）`:''}</div></div><div class="list-row__end"><span class="tag ${o.status==='paid'?'':'orange'}">${regStatusCn[o.status]||esc(o.status)}</span>${o.refund_status&&o.refund_status!=='none'?`<span class="tag orange">${refundCn[o.refund_status]||esc(o.refund_status)}</span>`:''}</div></div>`).join('')||'<div class="empty">暂无活动订单</div>'}
    </div>
    <div class="card section">
      <div class="panel-title"><h3>历史订单 · 装备</h3><span class="tag">${gearOrders.length} 笔</span></div>
      ${gearOrders.map(o=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">订单 #${o.id} · ${money(o.total||0)}</div><div class="list-row__sub">${esc(o.items_text||'商品已下架')}</div></div><div class="list-row__end"><span class="tag ${o.status==='paid'?'':'orange'}">${enumCn(o.status)}</span>${o.after_sales_status&&o.after_sales_status!=='none'?`<span class="tag orange">售后 ${enumCn(o.after_sales_status)}</span>`:''}</div></div>`).join('')||'<div class="empty">暂无装备订单</div>'}
    </div>
    <div class="grid g2 section">
      <div class="card">
        <div class="panel-title"><h3>优惠券</h3><button class="btn secondary" onclick="giftMemberVoucher(${m.user_id})">＋ 赠送优惠券</button></div>
        
        ${vouchers.map(v=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(v.title||'')} <span class="tag">${v.benefit_type==='activity_coupon'?`抵扣 ${money(v.cash_value||0)}`:'非现金福利'}</span>${v.points_spent===0?' <span class="tag orange">获赠</span>':''}</div><div class="list-row__sub">${esc(v.voucher_code||'')}${v.gift_note?` · ${esc(v.gift_note)}`:''} · 领取于 ${esc((v.created_at||'').slice(0,10))}</div></div><div class="list-row__end"><span class="tag ${v.status==='issued'?'':'orange'}">${voucherStatusCn[v.status]||esc(v.status)}</span></div></div>`).join('')||'<div class="empty">暂无优惠券</div>'}
      </div>
      <div class="card">
        <div class="panel-title"><h3>积分流水</h3><span class="sub">最近 30 条</span></div>
        ${[...clubLedger.map(l=>ledRow(l,'活动积分')),...gearLedger.map(l=>ledRow(l,'装备积分'))].join('')||'<div class="empty">暂无积分变动</div>'}
      </div>
    </div>`;
}

async function loadMall(){ try{skel('#clubOrders',4);let [p,cs,aftersales,gd]=await Promise.all([api(`/api/club/${CLUB}/mall/products`),api(`/api/club/${CLUB}/mall/commission-summary`),api(`/api/club/${CLUB}/mall/after-sales`),api(`/api/club/${CLUB}/gear-discount`)]);const md=(gd&&gd.rate<1)?{rate:gd.rate,tierName:gd.planName||'会员',discountZhe:gd.discountZhe}:null;$('#clubCommissionSummary').innerHTML=[['待签收',money(cs.pending)],['售后冻结',money(cs.frozen)],['可结算',money(cs.payableNow)],['已结算',money(cs.settled)]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div><div class="hint">${cs.carryDebt&&x[0]==='可结算'?`退款待冲抵 ${money(cs.carryDebt)}`:`售后期 ${cs.policy.afterSalesDays} 天`}</div></div>`).join('');$('#clubProducts').innerHTML=p.map(x=>{const cv=x.coverUrl||(x.images&&x.images[0]&&(x.images[0].url||x.images[0]))||x.image_url||'';const ph=cv?`<span class="ph" style="position:relative;display:block;height:96px;background:#eef2ef;overflow:hidden"><span style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:40px">🎒</span><img src="${esc(cv)}" alt="${esc(x.name)}" loading="lazy" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover" onerror="this.remove()"></span>`:`<span class="ph" style="display:block;height:96px;background:#eef2ef;display:flex;align-items:center;justify-content:center;font-size:40px">🎒</span>`;const stockLine=(x.displayOnly||x.stock==null)?'平台统一履约':'库存 '+x.stock;return `<div class="product" data-pid="${x.id}" role="button" tabindex="0" onclick="openClubProduct(${x.id})">${ph}<h4>${esc(x.name)}</h4><div class="sub">${esc(x.category||'户外装备')}</div>${memberPriceHtml(x.price,md)}<div class="sub">${stockLine}</div></div>`}).join('');await loadMallOrders();await loadSettlementAccount();$('#clubAfterSales').innerHTML=aftersales.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${({refund_only:'仅退款',return_refund:'退货退款',exchange:'换货'})[x.case_type]||x.case_type}</div> · 订单 #${x.order_id} <span class="tag">${esc(x.status)}</span><div class="list-row__sub">平台售后处理 · ${esc(x.reason||'')} · 申请退款 ${money(x.requested_refund_amount||0)}</div></div></div>`).join('')||'<div class="empty">暂无商城售后</div>';$('#clubSettlements').innerHTML=(()=>{window._cs=cs;const st=x=>x==='requested'?'待平台打款':x==='paid'?'已打款':esc(x);
  const can=cs.canRequest;
  const hero=`<div class="settle-hero"><div class="settle-hero__main"><div class="settle-hero__k">可申请佣金</div><div class="settle-hero__amt">${money(cs.payableNow)}</div><div class="settle-hero__hint ${can?'':'bad'}">${can?'已达到起结门槛，可发起申请':esc(cs.blockReason||'')}</div></div><div class="settle-hero__side"><div>佣金累计 <b>${money(cs.lifetimeEarned)}</b></div><div>退款冲抵 <b>${money(cs.lifetimeReversed)}</b></div><div>打款中 <b>${money(cs.reserved||0)}</b></div><button class="btn" ${can?'':'disabled'} onclick="requestClubSettlement()">${can?'申请结算':'未达门槛'}</button></div></div>`
  +`<div class="notice" style="margin:12px 0 2px">佣金结算规则：订单签收后 ${cs.policy.afterSalesDays} 天售后期内冻结，期满自动转入可申请；退款订单的佣金自动冲抵；累计可申请满 <b>${money(cs.threshold)}</b> 才能发起结算；发起后由总平台核对结算账户并打款（一般 1-3 个工作日）。打款前请先在上方「结算账户」登记法人卡或对公账户。</div>`
  // 当前佣金率由总平台按俱乐部设定，俱乐部只读（2026-10-24）。
  // 费率在成交时快照 —— 平台改费率不追溯已成交的订单。
  +`<div class="notice" style="margin:8px 0 2px">当前佣金率 <b>${Number(cs.commissionRatePercent||0)}%</b>（${esc(cs.commissionRateSource==='club_override'?'总平台已为本俱乐部单独设定':'沿用商品默认费率')}）。费率在订单成交时锁定，后续调整不影响已成交订单。</div>`;
  const months=(cs.months||[]).map(m=>{const unsettled=Math.round((m.net-m.settled)*100)/100;
    return `<tr><td>${esc(m.ym)}</td><td>${money(m.earned)}</td><td>${money(m.reversed)}</td><td>${money(m.settled)}</td><td><b>${money(Math.max(0,unsettled))}</b></td><td><span class="tag ${m.settled>0?'':'orange'}">${m.settled>0?'部分/已结算':'未结算'}</span></td></tr>`}).join('');
  const table=months?`<table class="table" style="margin-top:8px"><thead><tr><th>年月</th><th>产生佣金</th><th>退款冲抵</th><th>已结算</th><th>未结余</th><th>状态</th></tr></thead><tbody>${months}</tbody></table>`:'<div class="empty" style="padding:14px 0">暂无按月佣金数据</div>';
  const rows=cs.recentSettlements.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">结算单 ${esc(String(x.id).slice(0,13))}… <span class="tag ${x.status==='paid'?'':'orange'}">${st(x.status)}</span></div><div class="list-row__sub">实打 ${money(x.net_amount)} · 佣金 ${money(x.gross_amount)} · 冲抵 ${money(x.deduction_amount)} · ${x.entry_count} 笔 · ${esc(String(x.paid_at||x.created_at||'').slice(0,16).replace('T',' '))}${x.status==='paid'&&x.payment_ref?` · 凭证 ${esc(x.payment_ref)}`:''}${x.status==='requested'?' · 等待总平台核对账户并打款':''}</div></div></div>`).join('')||'<div class="empty">暂无结算记录；可申请佣金达到门槛后，点上方「申请结算」发起</div>';
  return hero+'<div class="settle-sec-t">按月明细</div>'+table+'<div class="settle-sec-t">结算记录</div>'+rows;})();await loadInventory()}
catch(e){loaderError('#clubOrders',e,'商城数据加载失败')}}
async function requestClubSettlement(){
  const cs=window._cs||{};
  const okd=await showConfirm({title:'申请佣金结算',message:`将把当前可申请佣金 ${money(cs.payableNow||0)} 申请打入已登记的结算账户；总平台核对后一般 1-3 个工作日内打款。申请后这笔佣金即锁定，不能重复申请。`,confirmText:'确认申请'});
  if(!okd)return;
  try{await api(`/api/club/${CLUB}/mall/settlement-requests`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});toast('结算申请已提交，等待总平台打款');await loadMall()}
  catch(e){showAlert({title:'申请失败',message:e.message})}
}
/* ── 商城订单按日期筛选（2026-10-22 用户反馈「佣金明细太多，要按日期搜索」）────
   DB 时间戳是 UTC（SQLite CURRENT_TIMESTAMP 恒 UTC，线上服务器还是 GMT），
   用户按本地日期筛：后端 date(created_at,'+8 hours') 比较，前端显示也 +8。 */
let mallOrderRange={from:'',to:''};
function fmtUtc8(ts){ /* DB 存 UTC；时间戳 +8h 后用 UTC 字段读，才是本地(+8)时间。
   ★不能用 setHours(getHours()+8)：GMT+8 浏览器会先把 UTC 转本地再加 8，双重偏移。 */
  if(!ts)return '';
  const d=new Date(String(ts).replace(' ','T')+'Z');
  if(isNaN(d))return String(ts).slice(0,16);
  const l=new Date(d.getTime()+8*3600e3);
  const p=n=>String(n).padStart(2,'0');
  return `${p(l.getUTCMonth()+1)}-${p(l.getUTCDate())} ${p(l.getUTCHours())}:${p(l.getUTCMinutes())}`;
}
function localDay(offsetDays){
  const d=new Date(Date.now()+(offsetDays||0)*86400e3+8*3600e3);
  const p=n=>String(n).padStart(2,'0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth()+1)}-${p(d.getUTCDate())}`;
}
function setOrderRange(preset){
  const t=localDay(0);
  if(preset==='today')mallOrderRange={from:t,to:t};
  else if(preset==='yesterday'){const y=localDay(-1);mallOrderRange={from:y,to:y};}
  else if(preset==='7d')mallOrderRange={from:localDay(-6),to:t};
  else if(preset==='month')mallOrderRange={from:t.slice(0,8)+'01',to:t};
  else if(preset==='lastMonth'){
    // 本地(+8)视角：时间戳 +8h 后用 UTC 字段回退（setUTCDate(0)=上月最后一天，再来一次=上月更前一天）
    const base=new Date(Date.now()+8*3600e3);
    base.setUTCDate(0);
    const last=`${base.getUTCFullYear()}-${String(base.getUTCMonth()+1).padStart(2,'0')}-${String(base.getUTCDate()).padStart(2,'0')}`;
    base.setUTCDate(0);
    mallOrderRange={from:`${base.getUTCFullYear()}-${String(base.getUTCMonth()+1).padStart(2,'0')}-01`,to:last};
  }
  else mallOrderRange={from:'',to:''};
  $('#clubOrderFrom').value=mallOrderRange.from;
  $('#clubOrderTo').value=mallOrderRange.to;
  loadMallOrders();
}
function applyOrderRange(){ mallOrderRange={from:$('#clubOrderFrom').value||'',to:$('#clubOrderTo').value||''}; loadMallOrders(); }
async function loadMallOrders(){
  try{
    const q=new URLSearchParams();
    if(mallOrderRange.from)q.set('date_from',mallOrderRange.from);
    if(mallOrderRange.to)q.set('date_to',mallOrderRange.to);
    const o=await api(`/api/club/${CLUB}/mall/orders${q.toString()?'?'+q.toString():''}`);
    const gross=o.reduce((s,x)=>s+Number(x.total||0),0), comm=o.reduce((s,x)=>s+Number(x.club_commission||0),0);
    const ranged=mallOrderRange.from||mallOrderRange.to;
    $('#clubOrderStat').innerHTML=`共 <b>${o.length}</b> 单 · 实付合计 <b>${money(gross)}</b> · 佣金合计 <b>${money(comm)}</b> · ${ranged?`${mallOrderRange.from||'最早'} 至 ${mallOrderRange.to||'最新'}`:'全部时间'}`;
    $('#clubOrders').innerHTML=o.map(x=>`<div class="list-row" role="button" tabindex="0" onclick="openClubOrder(${x.id})"><div class="list-row__main"><div class="list-row__title">订单 #${x.id}</div> · ${money(x.total)} · 佣金 ${money(x.club_commission)}<div class="list-row__sub">${fmtUtc8(x.created_at)} 下单 · ${x.status} · ${x.carrier||'待发货'} ${x.tracking_no||''} · 售后 ${x.after_sales_status||'无'}</div></div></div>`).join('')||`<div class="empty">${ranged?'该日期范围内暂无订单':'暂无商城订单'}</div>`;
  }catch(e){loaderError('#clubOrders',e,'订单加载失败')}
}
/* ── 结算账户（法人卡/对公账户，2026-10-22 用户反馈「结算佣金需要提供法人卡或对公账户」）── */
async function loadSettlementAccount(){
  try{
    const a=await api(`/api/club/${CLUB}/settlement-account`);
    const box=$('#clubSettlementAccount');
    if(!a||!a.exists){
      box.innerHTML=`<div class="notice-bar warn" style="margin-bottom:10px">尚未登记结算账户。平台结算佣金前必须提供<b>法人卡</b>或<b>对公账户</b>，否则无法打款。</div><button class="btn" onclick="saveSettlementAccount()">添加结算账户</button>`;
      return;
    }
    box.innerHTML=`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(a.holder)} <span class="tag info">${esc(a.accountTypeCn)}</span></div><div class="list-row__sub">${esc(a.bankName)}${a.bankBranch?' · '+esc(a.bankBranch):''} · 卡号 ${esc(a.cardNoMasked)} · 法人 ${esc(a.legalName)}（${esc(a.legalIdNoMasked)}）${a.note?' · '+esc(a.note):''}</div></div><div class="list-row__end"><button class="btn ghost" onclick="saveSettlementAccount()">修改</button></div></div><div class="sub" style="margin-top:8px">${a.updatedAt?'更新于 '+esc(a.updatedAt.slice(0,10)):''}</div>`;
  }catch(e){loaderError('#clubSettlementAccount',e,'结算账户加载失败')}
}
async function saveSettlementAccount(){
  let cur={exists:false};
  try{cur=await api(`/api/club/${CLUB}/settlement-account`)}catch(e){showAlert({title:'加载失败',message:e.message});return}
  /* 明文字段（户名/法人/开户行）预填方便改；★卡号与身份证绝不出明文——只提示当前脱敏值，
     留空即保留原值（后端对空值走保留分支），防止用户把 ********9012 当明文存回库。 */
  const f=await uxForm({title:cur.exists?'修改结算账户':'添加结算账户',desc:'平台将按此账户打款，请确保信息准确；卡号仅平台核对打款之用，不会对外展示。',submitText:'保存',fields:[
    {name:'accountType',label:'账户类型',type:'select',required:true,value:cur.exists?cur.accountType:'legal',options:[{value:'legal',label:'法人银行卡'},{value:'corporate',label:'对公账户'}]},
    {name:'holder',label:'户名',required:true,value:cur.exists?cur.holder:'',placeholder:cur.exists?'':'法人银行卡填法人姓名；对公账户填单位名称'},
    {name:'legalName',label:'法人姓名',required:true,value:cur.exists?cur.legalName:''},
    {name:'legalIdNo',label:'法人身份证号',required:!cur.exists,value:'',placeholder:cur.exists?`不修改可留空 · 当前 ${cur.legalIdNoMasked}`:'18 位（末位可为 X）'},
    {name:'bankName',label:'开户行',required:true,value:cur.exists?cur.bankName:'',placeholder:'如：中国工商银行'},
    {name:'bankBranch',label:'开户支行（选填）',value:cur.exists?(cur.bankBranch||''):''},
    {name:'cardNo',label:'卡号 / 账号',required:!cur.exists,value:'',placeholder:cur.exists?`不修改可留空 · 当前 ${cur.cardNoMasked}`:'10-25 位数字'},
    {name:'note',label:'备注（选填）',full:true,value:cur.exists?(cur.note||''):''}
  ]});
  if(!f)return;
  try{
    await api(`/api/club/${CLUB}/settlement-account`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(f)});
    toast('结算账户已保存');await loadSettlementAccount();
  }catch(e){showAlert({title:'保存失败',message:e.message})}
}
/* 装备上下架：俱乐部只能申请，不能自己改 status —— products 表根本没有 club 归属列，
   让俱乐部直接改等于绕过总平台的商品管理。提交申请后由总平台一键处理，
   处理完 products.status 才变，C 端商城的可见性跟着变。 */
async function loadInventory(){
  skel('#clubInventory',4);
  try{
  const [items,reqs]=await Promise.all([api(`/api/club/${CLUB}/mall/inventory`),api(`/api/club/${CLUB}/mall/visibility-requests`)]);
  $('#clubInventory').innerHTML=items.map(x=>{
    const up=String(x.status||'active')==='active';
    const pend=x.pendingRequest;
    const pendTag=pend?'<span class="tag orange">'+(pend.action==='on'?'待总平台上架':'待总平台下架')+'</span>':'';
    return '<div class="list-row"><div class="list-row__main"><div class="list-row__title">'+esc(x.name)+'</div>'
      +'<div class="list-row__sub">'+esc(x.sku||'')+' · '+esc(x.category||'户外装备')+' · 库存 '+x.stock+' · 售价 '+money(x.price||0)+'</div></div>'
      +'<div class="list-row__end"><span class="tag '+(up?'':'orange')+'">'+(up?'在架':'已下架')+'</span>'+pendTag
      +'<button class="btn ghost" onclick="requestVisibility('+x.id+',\''+(up?'off':'on')+'\')">'+(up?'申请下架':'申请上架')+'</button></div></div>';
  }).join('')||'<div class="empty">暂无商品</div>';
  const stCn={pending:'待总平台处理',approved:'已批准',rejected:'已驳回'};
  $('#clubVisibilityRequests').innerHTML=reqs.map(x=>'<div class="list-row"><div class="list-row__main"><div class="list-row__title">'+esc(x.product_name)
    +' <span class="tag '+(x.action==='on'?'':'orange')+'">'+(x.action==='on'?'申请上架':'申请下架')+'</span></div>'
    +'<div class="list-row__sub">'+esc(x.note||'')+' · 提交于 '+esc(x.created_at||'')+(x.decided_note?' · 处理意见 '+esc(x.decided_note):'')+'</div></div>'
    +'<div class="list-row__end"><span class="tag">'+stCn[x.status]+'</span></div></div>').join('')||'<div class="empty">还没有提交过上下架申请</div>';
  }catch(e){loaderError('#clubInventory',e,'装备上架状态加载失败')}
}
async function requestVisibility(pid,action){
  const f=await showForm({title:action==='on'?'申请上架':'申请下架',desc:'提交后由总平台在「商品与供应链」里处理。',
    fields:[{name:'note',label:'说明（可选）',type:'textarea',placeholder:'例如：春季上新，需要先在商城露出'}]});
  if(!f)return;
  try{await api(`/api/club/${CLUB}/mall/products/${pid}/visibility`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,note:f.note||''})});
    toast('申请已提交，等待总平台处理');await loadInventory()}catch(e){showAlert({title:'提交失败',message:e.message})}
}
/* 「积分策略」二级页：上面是「新活动默认规则」（俱乐部级，新建活动继承），
   下面是各活动的实际规则（摊平的表，可勾选批量改 / 点一行单独改）。
   2026-10-31：以前每场活动都撞 activities 表写死的默认值（全开 + 可抵 100%），
   想全场收紧得逐场点一遍。现在默认值提到俱乐部级，设一次即对新活动生效；
   存量活动用下面的「批量设置」一次性刷齐。
   ★ 点行必须就地弹窗（不可 openActivity）—— 详情容器 #activityDetail 在 #activities
   视图里，站在本页时它是 display:none，写进去用户看不到任何反应。 */
let pointsActivities=[];
let pointsDefault=null;
let pointsRateInfo=null;
async function loadPointsPolicy(){
  skel('#pointsList',6);
  try{
    /* 默认值读不到（接口异常 / 老库）就退回「全开 100%」，与 activities 表历史默认一致，
       绝不因为加功能让整页白屏。 */
    try{pointsDefault=await api(`/api/club/${CLUB}/points-default`)}catch(e){pointsDefault=null}
    try{pointsRateInfo=await api(`/api/club/${CLUB}/points-redeem-rate`)}catch(e){pointsRateInfo=null}
    paintPointsDefault();
    paintPointsRate();
    const list=await api(`/api/club/${CLUB}/activities`);
    pointsActivities=list||[];
    const d=pointsDefault||{enabled:true,earnClubPoints:true,acceptClubPoints:true,clubPointsMaxDiscountPercent:100};
    $('#pointsList').innerHTML=pointsActivities.map(a=>{
      const tags=[];
      const enabled=Number(a.points_enabled??1)>0;
      if(enabled&&Number(a.earn_club_points??1)>0)tags.push('报名可获得');
      if(enabled&&Number(a.accept_club_points??1)>0)tags.push('可抵 '+Number(a.club_points_max_discount_percent??100)+'%');
      if(Number(a.member_discount_enabled??0)>0)tags.push('会员优惠');
      if(isCustomPoints(a,d))tags.push('已单独设置');
      const tagHtml=tags.length?tags.map(t=>'<span class="tag info">'+esc(t)+'</span>').join(' '):'<span class="tag orange">不参与积分</span>';
      /* 复选框要 stopPropagation：否则点勾选会冒泡到行的 onclick，顺手把编辑弹窗也打开了。 */
      return '<div class="list-row" role="button" tabindex="0" data-points-act="'+a.id+'" onclick="editActivityPoints('+a.id+')" aria-label="修改 '+esc(a.title)+' 的积分规则">'
        +'<input type="checkbox" class="points-pick" data-points-pick="'+a.id+'" onclick="event.stopPropagation()" aria-label="选中 '+esc(a.title)+'">'
        +'<div class="list-row__main"><div class="list-row__title">'+esc(a.title)+'</div>'
        +'<div class="list-row__sub">'+esc(a.event_date||'')+' · '+esc(a.location||'')+' · 报名 '+money(a.price||0)+'</div></div>'
        +'<div class="list-row__end">'+tagHtml+'<span class="sub">修改 ›</span></div></div>';
    }).join('')||'<div class="empty">还没有活动</div>';
  }catch(e){loaderError('#pointsList',e,'积分策略加载失败')}
}
/* 与俱乐部默认值不一致 = 运营单独调过，标出来方便一眼看出例外。 */
function isCustomPoints(a,d){
  const on=(v,dv)=>Number(v??dv)>0;
  return on(a.points_enabled,1)!==!!d.enabled
    || on(a.earn_club_points,1)!==!!d.earnClubPoints
    || on(a.accept_club_points,1)!==!!d.acceptClubPoints
    || Number(a.club_points_max_discount_percent??100)!==Number(d.clubPointsMaxDiscountPercent??100);
}
/* 顶部「新活动默认规则」卡片：只读展示 + 一个修改入口。 */
function paintPointsDefault(){
  const d=pointsDefault||{enabled:true,earnClubPoints:true,acceptClubPoints:true,clubPointsMaxDiscountPercent:100};
  const lines=[d.enabled?'新活动默认参与积分体系':'新活动默认不参与积分体系'];
  if(d.enabled){
    lines.push(d.earnClubPoints?'报名后累计活动积分':'报名不累计活动积分');
    lines.push(d.acceptClubPoints?('允许活动积分抵现金，最多抵 '+Number(d.clubPointsMaxDiscountPercent??100)+'%'):'不允许活动积分抵现金');
  }
  $('#pointsDefault').innerHTML='<div class="notice">'+lines.map(t=>'<div>'+esc(t)+'</div>').join('')
    +'<div class="sub" style="margin-top:6px">只影响<b>之后新建</b>的活动；已有活动不会被这里改动 —— 要改存量请用下面的「批量设置」。</div></div>'
    +'<div style="margin-top:10px"><button class="btn secondary" onclick="editPointsDefault()">修改默认规则</button></div>';
}
/* 「积分现金换算比例」卡片（2026-11-05）：X 积分 = ¥1。
   生效优先级：会员当前等级专属 > 俱乐部默认 > 平台全局默认(100)。
   各等级的专属值在「客户会员 → 等级会员 / 付费会员卡」里配，这里只读一览 + 改俱乐部默认。
   刻意不把比例塞进「新活动默认规则」：那四个字段是活动级开关，会被新活动继承；
   换算比例是俱乐部 / 会员级的资金口径，不属于任何一场活动。 */
function paintPointsRate(){
  const box=$('#pointsRateBox'); if(!box)return;
  const d=pointsRateInfo;
  if(!d){box.innerHTML='<div class="empty">换算比例加载失败，点右上「刷新」重试</div>';return}
  const tierRows=(d.tiers||[]).map(t=>{
    const own=Number(t.pointsRedeemRate||0)>0;
    const src=own?'等级专属':(t.source==='club'?'跟随俱乐部默认':'跟随平台默认');
    return `<div class="list-row"><div class="list-row__main">
      <div class="list-row__title">${esc(t.name)} <span class="tag ${own?'info':''}">${t.effectiveRate} 积分 = ¥1</span>${t.status!=='active'?' <span class="tag orange">已停用</span>':''}</div>
      <div class="list-row__sub">${esc(src)} · ${t.accessMode==='PURCHASE'?'付费会员卡':'等级会员'}</div>
    </div></div>`;
  }).join('')||'<div class="empty">还没有会员等级</div>';
  const clubLine=Number(d.clubRate||0)>0
    ? `本俱乐部单独设定 <b>${d.clubRate} 积分 = ¥1</b>（平台默认 ${d.platformRate} 积分 = ¥1）`
    : `本俱乐部<b>跟随平台默认</b> ${d.platformRate} 积分 = ¥1`;
  box.innerHTML='<div class="notice">'
    +'<div>顾客报名时：先把活动积分按这个比例折成现金，再受活动规则「最多抵扣 N%」的现金上限约束。数字越小，同样的积分越值钱；但「最多抵 N%」这道成本闸门不变。</div>'
    +`<div style="margin-top:6px">${clubLine}；未设等级专属比例时，本俱乐部默认生效 <b>${d.clubEffectiveRate} 积分 = ¥1</b>。</div>`
    +'<div class="sub" style="margin-top:6px">只影响<b>俱乐部活动积分</b>在活动报名时的抵扣；装备积分由总平台出钱，仍按平台规则，不随这里变化。积分商城的兑换标价是固定积分，不按此比例折现金。</div>'
    +'</div>'
    +'<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center"><button class="btn secondary" onclick="editPointsRedeemRate()">设置俱乐部默认比例</button></div>'
    +'<div style="margin-top:12px"><div class="list-row__sub" style="margin-bottom:6px"><b>各会员等级实际生效</b></div>'+tierRows+'</div>';
}
async function editPointsRedeemRate(){
  const d=pointsRateInfo||{};
  const f=await uxForm({
    title:'积分现金换算比例',
    hint:'X 积分 = ¥1。填 0 或留空 = 跟随平台默认；填正数 = 本俱乐部单独设定。会员等级还可以再单独覆盖。',
    fields:[
      {name:'pointsRedeemRate',label:'本俱乐部默认（X 积分 = ¥1）',type:'number',min:0,max:Number(d.maxRate||10000),step:1,
        full:true,value:String(Number(d.clubRate||0)),
        help:`填 0 = 跟随平台默认（当前 ${Number(d.platformRate||100)} 积分 = ¥1）；例如填 50 表示 50 积分抵 ¥1。范围 1~${Number(d.maxRate||10000)}。`}
    ],
    submitText:'保存换算比例'
  });
  if(!f)return;
  try{
    await api(`/api/club/${CLUB}/points-redeem-rate`,{method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({pointsRedeemRate:Number(f.pointsRedeemRate||0)})});
    toast('积分换算比例已保存');
    await loadPointsPolicy();
  }catch(e){showAlert({title:'保存换算比例失败',message:e.message})}
}
async function editPointsDefault(){
  const d=pointsDefault||{};
  const f=await uxForm({
    title:'新活动默认规则',
    hint:'设一次即对所有「之后新建」的活动生效。已有活动不受影响，可用下面的「批量设置」一次性刷齐。',
    fields:[
      {name:'enabled',label:'新活动默认参与积分体系',type:'select',full:true,value:d.enabled?'1':'0',options:[{value:'1',label:'参与'},{value:'0',label:'不参与'}]},
      {name:'earnClubPoints',label:'报名后累计活动积分',type:'select',value:d.earnClubPoints?'1':'0',options:[{value:'1',label:'累计'},{value:'0',label:'不累计'}],help:'积分成本由本俱乐部承担。'},
      {name:'acceptClubPoints',label:'允许活动积分抵现金',type:'select',value:d.acceptClubPoints?'1':'0',options:[{value:'1',label:'允许抵扣'},{value:'0',label:'不允许抵扣'}]},
      {name:'clubPointsMaxDiscountPercent',label:'最多抵扣活动金额（%）',type:'number',full:true,min:0,max:100,step:1,value:String(Number(d.clubPointsMaxDiscountPercent??100)),help:'0 表示不能抵。'}
    ],
    submitText:'保存默认规则'
  });
  if(!f)return;
  try{
    await api(`/api/club/${CLUB}/points-default`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      enabled:f.enabled==='1',earnClubPoints:f.earnClubPoints==='1',acceptClubPoints:f.acceptClubPoints==='1',
      clubPointsMaxDiscountPercent:Number(f.clubPointsMaxDiscountPercent||0)
    })});
    toast('默认积分规则已保存');
    await loadPointsPolicy();
  }catch(e){showAlert({title:'保存默认规则失败',message:e.message})}
}
/* 批量改勾选活动的积分规则：一个请求一个事务，不留「改了一半」的中间态。 */
async function bulkEditPoints(){
  const ids=[...document.querySelectorAll('.points-pick:checked')].map(x=>Number(x.dataset.pointsPick)).filter(Boolean);
  if(!ids.length)return showAlert({title:'还没勾选活动',message:'先勾选要修改的活动，再点「批量设置」。'});
  const d=pointsDefault||{};
  const f=await uxForm({
    title:'批量设置 '+ids.length+' 场活动',
    hint:'勾选的活动会被统一改成下面这套规则，未勾选的不受影响。装备积分仍由总平台控制，不受这里影响。',
    fields:[
      {name:'enabled',label:'参与积分体系',type:'select',full:true,value:d.enabled?'1':'0',options:[{value:'1',label:'参与'},{value:'0',label:'不参与'}]},
      {name:'earnClubPoints',label:'报名后累计活动积分',type:'select',value:d.earnClubPoints?'1':'0',options:[{value:'1',label:'累计'},{value:'0',label:'不累计'}]},
      {name:'acceptClubPoints',label:'允许活动积分抵现金',type:'select',value:d.acceptClubPoints?'1':'0',options:[{value:'1',label:'允许抵扣'},{value:'0',label:'不允许抵扣'}]},
      {name:'clubPointsMaxDiscountPercent',label:'最多抵扣活动金额（%）',type:'number',full:true,min:0,max:100,step:1,value:String(Number(d.clubPointsMaxDiscountPercent??100))}
    ],
    submitText:'应用到 '+ids.length+' 场活动'
  });
  if(!f)return;
  try{
    const r=await api(`/api/club/${CLUB}/activities/points-policy/bulk`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      activityIds:ids,enabled:f.enabled==='1',earnClubPoints:f.earnClubPoints==='1',acceptClubPoints:f.acceptClubPoints==='1',
      clubPointsMaxDiscountPercent:Number(f.clubPointsMaxDiscountPercent||0)
    })});
    toast('已更新 '+r.updated+' 场活动的积分规则');
    await loadPointsPolicy();
  }catch(e){showAlert({title:'批量设置失败',message:e.message})}
}
/* 行是 div[role=button]，onclick 只绑 click、不绑键盘，Enter/Space 不会触发 —— 自己补一条通路。
   事件委托绑在 document 上，列表重渲染后不用重绑。 */
document.addEventListener('keydown',e=>{
  if(e.key!=='Enter'&&e.key!==' ')return;
  const row=e.target&&e.target.closest?e.target.closest('#pointsList [data-points-act]'):null;
  if(!row)return;e.preventDefault();row.click();
});
/* 就地编辑一场活动的积分规则：只提交俱乐部活动积分的四个字段。
   装备积分相关字段一律不进 payload —— 后端 activity_points_policy.normalize_update
   对缺失键回退到当前值，所以平台侧设置不会被这里顺手改掉。 */
async function editActivityPoints(id){
  let p;
  try{p=await api(`/api/club/${CLUB}/activities/${id}/points-policy`)}
  catch(e){return showAlert({title:'读取积分规则失败',message:e.message})}
  let md;
  try{md=await api(`/api/club/${CLUB}/activities/${id}/member-discount`)}catch(e){md={enabled:false}}
  const act=(pointsActivities||[]).find(x=>Number(x.id)===Number(id))||{};
  const f=await uxForm({
    title:'本场活动的积分规则',
    subtitle:act.title?act.title:('活动 #'+id),
    hint:'只配俱乐部自己的活动积分：这场活动参不参与、报名送不送累计、能不能抵现金、最多抵多少。团期默认继承整场活动规则。',
    fields:[
      {name:'enabled',label:'这场活动参与积分体系',type:'select',full:true,value:p.enabled?'1':'0',options:[{value:'1',label:'参与'},{value:'0',label:'不参与'}]},
      {name:'earnClubPoints',label:'报名后累计活动积分',type:'select',value:p.earnClubPoints?'1':'0',options:[{value:'1',label:'累计'},{value:'0',label:'不累计'}],help:'积分成本由本俱乐部承担。'},
      {name:'acceptClubPoints',label:'允许活动积分抵现金',type:'select',value:p.acceptClubPoints?'1':'0',options:[{value:'1',label:'允许抵扣'},{value:'0',label:'不允许抵扣'}]},
      {name:'clubPointsMaxDiscountPercent',label:'最多抵扣活动金额（%）',type:'number',full:true,min:0,max:100,step:1,value:String(Number(p.clubPointsMaxDiscountPercent??100)),help:'本单活动积分最多能抵掉活动金额的百分之多少，0 表示不能抵。'},
      {name:'memberDiscount',label:'参与会员优惠（会员等级折扣）',type:'select',full:true,value:(md&&md.enabled)?'1':'0',options:[{value:'1',label:'参与 —— 达标会员按其等级折扣立减'},{value:'0',label:'不参与 —— 会员也按原价报名'}],help:'这是会员活动折扣的前提条件；各等级的折扣率在「客户 / 会员 → 等级会员 · 条件开通」里配置。'}
    ],
    submitText:'保存积分规则'
  });
  if(!f)return;
  const payload={
    enabled:f.enabled==='1',
    earnClubPoints:f.earnClubPoints==='1',
    acceptClubPoints:f.acceptClubPoints==='1',
    clubPointsMaxDiscountPercent:Number(f.clubPointsMaxDiscountPercent||0)
  };
  try{
    await api(`/api/club/${CLUB}/activities/${id}/points-policy`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    await api(`/api/club/${CLUB}/activities/${id}/member-discount`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:f.memberDiscount==='1'})});
    toast('活动积分规则已保存');
    await loadPointsPolicy();
  }catch(e){showAlert({title:'保存积分规则失败',message:e.message})}
}
async function loadCredits(){ try{skel('#creditLedger',5);let d=await api(`/api/club/${CLUB}/credits`),sub=d.subscription||{};$('#creditAccount').innerHTML=`<div class="grid g4"><div class="stat-tile"><div class="k">当前可用</div><div class="v">${d.account?.balance||0}</div><div class="hint">智能 Credits</div></div><div class="stat-tile"><div class="k">当前套餐</div><div class="v" style="font-size:22px">${esc(sub.plan_name||sub.plan_code||'未开通')}</div><div class="hint">月额度 ${d.account?.monthly_quota||0}</div></div><div class="stat-tile"><div class="k">本月已用</div><div class="v">${d.creditsConsumed||0}</div><div class="hint">成功调用 ${d.successfulCalls||0} 次</div></div><div class="stat-tile"><div class="k">待偿欠账</div><div class="v">${d.unresolvedDebt||0}</div><div class="hint">后续获得 Credits 自动优先抵扣</div></div></div><div class="notice section">大模型由<b>总平台统一接入并结算</b>，俱乐部不需要也无法配置模型或密钥；Credits 只决定计费，不决定模型质量——平台不会因为余额或套餐降低模型、减少图片或截断资料。</div>`;$('#creditTopups').innerHTML=(d.topupPackages||[]).map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.name)}</div><div class="list-row__sub">${money(x.amount)} · 共 ${x.credits} Credits · 单价 ${unitCreditPrice(x.amount,x.credits)}</div><button class="btn secondary" style="margin-top:6px" onclick="buyCredits('${x.code}')">创建充值订单</button></div></div>`).join('')||'<div class="empty">暂无充值包</div>';$('#creditPendingOrders').innerHTML=(d.pendingOrders||[]).map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${x.order_type==='subscription'?'套餐':'充值'} ${x.credits} Credits</div><div class="list-row__sub">${money(x.amount)} · 待付款确认 · ${esc(x.period_key||x.package_code||'')}</div></div></div>`).join('')||'<div class="empty">暂无待付款账单</div>';window._creditPlans=d.plans||[];$('#creditPlans').innerHTML=(d.plans||[]).map(p=>{const cur=sub.status==='active'&&sub.plan_code===p.code;const gd=Number(p.gear_discount||1);return `<div class="plan-card${cur?' on':''}"><div class="plan-card__hd"><strong>${esc(p.name)}</strong>${cur?'<span class="plan-card__cur">当前</span>':''}</div><div class="plan-card__fee">${Number(p.monthly_fee)>0?money(p.monthly_fee)+'<i>/月</i>':'定制价'}</div><div class="plan-card__meta">每月 ${p.monthly_credits} Credits${gd<1?` · 装备商城 ${Math.round(gd*100)/10} 折`:''}</div><div class="plan-card__desc">${esc(p.description||'')}</div><div style="margin-top:10px">${cur?'<button class="btn ghost" disabled>使用中</button>':`<button class="btn secondary" onclick="subscribePlan('${p.code}')">${sub.status==='active'?'切换到此套餐':'订阅此套餐'}</button>`}</div></div>`}).join('')||'<div class="empty">暂无可订阅套餐，请联系总平台开通</div>';await loadCreditLedger('reset')}
catch(e){loaderError('#creditLedger',e,'Credits 数据加载失败')}}
async function buyCredits(code){let r=await api(`/api/club/${CLUB}/credits/topups`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({packageCode:code})});toast(`充值订单已创建：${r.credits} Credits / ${money(r.amount)}，等待平台/支付确认`);loadCredits()}
async function subscribePlan(code){
  const p=(window._creditPlans||[]).find(x=>x.code===code);if(!p)return;
  const gd=Number(p.gear_discount||1);
  const parts=[`月费 ${Number(p.monthly_fee)>0?money(p.monthly_fee)+' / 月':'定制价（平台另行约定）'}`,`每月 ${p.monthly_credits} Credits`];
  if(gd<1)parts.push(`装备商城 ${Math.round(gd*100)/10} 折（由总平台统一控制）`);
  const okd=await showConfirm({title:`订阅「${p.name}」套餐`,message:parts.join('；')+'。订阅即刻生效，系统自动生成当月待付款账单；由总平台确认收款后 Credits 入账。',confirmText:'确认订阅'});
  if(!okd)return;
  try{
    await api(`/api/club/${CLUB}/subscription/plan`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({planCode:code})});
    toast(`已订阅「${p.name}」，待付款账单已生成`);
    await loadCredits();
  }catch(e){showAlert({title:'订阅失败',message:e.message})}
}
/* 顶栏品牌名：唯一真源是 clubs.name（平台端与俱乐部设置改的是同一列）。
   历史实现把名字写死在 index.html 里、只在启动时覆盖一次 —— 改完名字当场看不到变化，
   用户会判定「后台没同步」（2026-10-23 用户反馈）。现在：保存即刷新 + 本地缓存兜底。 */
function applyClubName(name,opt){
  const n=String(name||'').trim();if(!n)return;
  const el=document.getElementById('clubTopName')||document.querySelector('.topbar .title');
  if(el)el.textContent=n;
  document.title=n+' · ClubOS 俱乐部经营';
  if(!opt||opt.cache!==false){try{localStorage.setItem('clubos_club_name_'+CLUB,n)}catch(e){}}
}
window.applyClubName=applyClubName;
async function startClub(){
  if(clubosCookie('clubos_csrf')){const me=await api('/api/auth/me');if(me.role!=='club')throw new Error('请使用俱乐部账号登录');CLUB=Number(me.clubId);}
  // 先拿上次缓存的品牌名渲染，刷新时不会先闪一眼占位名
  try{const cached=localStorage.getItem('clubos_club_name_'+CLUB);if(cached)applyClubName(cached,{cache:false})}catch(e){}
  try{const club=await api('/api/public/clubs/'+CLUB,{cache:'no-store'});if(club?.name)applyClubName(club.name)}catch(e){}
  /* 领队咨询提示（2026-10-28）：进后台先拉一次，侧栏角标立刻可见；之后 60 秒一次，
     页面从后台切回前台时再补一次 —— 手机上切走再回来不会漏掉新问题的提醒。 */
  pollLeaderInquiries();
  setInterval(pollLeaderInquiries,60000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollLeaderInquiries()});
  await loadDash();
  loadCreateEngine();
}
/* 创建弹窗明示当前 智能引擎：真实大模型 or 演示模式。避免老板把「演示模板内容」误判成 AI 没看资料。 */
async function loadCreateEngine(){
  const box=document.getElementById('createEngine');if(!box)return;
  try{
    const m=await api('/api/club/'+CLUB+'/ai-mode');const live=m.mode==='live';
    box.className='ux-create-engine '+(live?'live':'mock');
    box.innerHTML=live
      ?'<b>智能引擎：真实大模型</b> · 会真实理解你上传的方案与照片，并按内容生成活动详情与宣发素材。'
      :'<b>智能引擎：演示模式</b> · 当前未接通大模型：系统仅按上传资料<b>抽取真实事实</b>（行程、费用、人数、照片）并套用通用文案，内容可用但表达为示例；由总平台接通模型后即为真实创作。';
  }catch(e){box.style.display='none'}
}
startClub().catch(err=>{showAlert({title:'无法进入俱乐部后台',message:err.message});setTimeout(()=>location.href='/login',1800)});


// v0.13 Activity Execution Center（2026-10-24 重做：出发日期/执行状态筛选 + 区间汇总 +
//   按出发日分组——复用 智能 Credits 明细的 .led-* 组件体系，长列表不再是无位置感的平铺。
//   start_at 是自由文本（有 '2026-10-xx' 这类占位），取前 10 位解析日期；
//   解析不出的归入「未定档期」组永远展示，任何筛选下都不会把团期悄悄藏没。）
let _exeAll=null,currentExecutionOccurrence=null;
const execStateLabel={preparing:'准备中',departed:'已出发',in_progress:'进行中',completed:'已完成'};
const EXE_QUICK=[{k:'upcoming',label:'即将到来'},{k:'7d',label:'近 7 天'},{k:'30d',label:'近 30 天'},{k:'month',label:'本月'},{k:'past',label:'历史团期'},{k:'all',label:'全部'}];
const EXE_STATES=[{k:'',label:'全部状态'},{k:'preparing',label:'准备中'},{k:'departed',label:'已出发'},{k:'in_progress',label:'进行中'},{k:'completed',label:'已完成'}];
let EXE_ST=null;
function exeSt(){if(!EXE_ST)EXE_ST={k:'upcoming',from:'',to:'',status:''};return EXE_ST}
function exeDayOf(o){const m=/^(\d{4}-\d{2}-\d{2})/.exec(String(o.start_at||'').trim());return m?m[1]:''}
function exeRangeOf(k){
  const t=localDay(0);
  if(k==='upcoming')return{from:t,to:''};
  if(k==='7d')return{from:t,to:localDay(7)};
  if(k==='30d')return{from:t,to:localDay(30)};
  if(k==='month'){const first=t.slice(0,8)+'01',y=+t.slice(0,4),m=+t.slice(5,7);
    const last=new Date(Date.UTC(y,m,0)).getUTCDate();
    return{from:first,to:t.slice(0,8)+String(last).padStart(2,'0')}}
  if(k==='past')return{from:'',to:localDay(-1)};
  return{from:'',to:''};
}
function exeDayLabel(d){
  const t=localDay(0);
  if(d===t)return'今天出发';
  if(d===localDay(1))return'明天出发';
  if(d===localDay(2))return'后天出发';
  const wk='日一二三四五六'[new Date(d+'T00:00:00Z').getUTCDay()];
  const md=(+d.slice(5,7))+'月'+(+d.slice(8,10))+'日';
  return (d.slice(0,4)===t.slice(0,4)?'':d.slice(0,4)+'年')+md+' 周'+wk+' 出发';
}
function exeFiltered(){
  const st=exeSt(),rg=st.k==='custom'?{from:st.from||'',to:st.to||''}:exeRangeOf(st.k);
  const list=[],unknown=[];
  for(const o of (_exeAll||[])){
    if(st.status&&(o.execution_status||'preparing')!==st.status)continue;
    const d=exeDayOf(o);
    if(!d){unknown.push(o);continue}
    if((rg.from&&d<rg.from)||(rg.to&&d>rg.to))continue;
    list.push(o);
  }
  list.sort((a,b)=>exeDayOf(a).localeCompare(exeDayOf(b))||(a.id-b.id));
  unknown.sort((a,b)=>a.id-b.id);
  return{list,unknown};
}
function exeSumHtml(f){
  const s=f.list.concat(f.unknown).reduce((a,o)=>{a.sold+=o.sold||0;a.named+=o.named_participants||0;a.inc+=o.incomplete_participants||0;a.ins+=o.insurance_pending||0;a.ck+=o.checked_in||0;return a},{sold:0,named:0,inc:0,ins:0,ck:0});
  const n=f.list.length+f.unknown.length;
  return `<div class="led-sum"><span>团期 <b>${n}</b></span><span>售出 <b>${s.sold}</b></span><span>实名 <b>${s.named}</b></span><span class="${s.inc?'bad':''}">资料待补 <b>${s.inc}</b></span><span class="${s.ins?'bad':''}">保险待处理 <b>${s.ins}</b></span><span>已签到 <b>${s.ck}</b></span></div>`;
}
function exeBarHtml(){
  const st=exeSt();
  return `<div class="led-bar"><div class="led-chips">${EXE_QUICK.map(q=>`<button class="led-chip ${st.k===q.k?'on':''}" onclick="exeQuick('${q.k}')">${q.label}</button>`).join('')}</div><div class="led-dates">${uxChoiceHtml({value:st.status,options:EXE_STATES.map(s=>({value:s.k,label:s.label})),row:true,onchange:'exeStatus(this.value)'})}<input type="date" value="${st.from}" onchange="exeCustom()"><span class="led-dates__sep">至</span><input type="date" value="${st.to}" onchange="exeCustom()"></div></div>`;
}
function exeRow(o){
  const stKey=o.execution_status||'preparing';
  const exl=execStateLabel[stKey]||stKey;
  const raw=String(o.start_at||'').trim();
  const day=exeDayOf(o);
  const tpart=day&&raw.length>10?raw.slice(10).replace(/^[\s至]+/,'').trim():'';
  const when=day?(tpart?tpart+' · ':''):(raw?esc(raw)+' · ':'');
  return `<div class="exe-row" onclick="openExecution(${o.id})"><div class="exe-row__hd"><strong>${esc(o.activity_title)}</strong><span class="tag ${stKey==='completed'?'':'orange'}">${esc(exl)}</span></div><div class="exe-row__meta">${o.label?esc(o.label)+' · ':''}${when}${money(o.price)} · 售出 ${o.sold}/${o.capacity}${o.activity_location?` · ${esc(o.activity_location)}`:''}</div><div class="exe-row__ct">实名 ${o.named_participants||0} · 资料待补 ${o.incomplete_participants||0} · 保险待处理 ${o.insurance_pending||0} · 已签到 ${o.checked_in||0}</div></div>`;
}
function renderExe(){
  const f=exeFiltered();
  $('#exeBar').innerHTML=exeBarHtml();
  $('#exeSum').innerHTML=(f.list.length+f.unknown.length)?exeSumHtml(f):'';
  const parts=[];
  let cur=null,rows=[];
  const pushGroup=()=>{if(cur!==null&&rows.length){parts.push(`<div class="led-day"><span class="led-day__label">${esc(exeDayLabel(cur))}</span><span class="led-day__sum">${rows.length} 个团期 · 售出 ${rows.reduce((a,o)=>a+(o.sold||0),0)}</span></div>`+rows.map(exeRow).join(''))}};
  for(const o of f.list){const d=exeDayOf(o);if(d!==cur){pushGroup();cur=d;rows=[]}rows.push(o)}
  pushGroup();
  if(f.unknown.length)parts.push(`<div class="led-day"><span class="led-day__label">未定档期</span><span class="led-day__sum">${f.unknown.length} 个团期</span></div>`+f.unknown.map(exeRow).join(''));
  $('#executionOccurrenceList').innerHTML=parts.join('')||'<div class="empty">所选范围内暂无团期；切「全部」可查看历史与未定档期团期。</div>';
}
function exeQuick(k){const st=exeSt();st.k=k;renderExe()}
function exeStatus(v){const st=exeSt();st.status=v;renderExe()}
function exeCustom(){const st=exeSt();const ds=document.querySelectorAll('#exeBar input[type=date]');st.from=ds[0]?ds[0].value:'';st.to=ds[1]?ds[1].value:'';st.k='custom';renderExe()}
async function loadExecution(){skel('#executionOccurrenceList',4);
  try{
  _exeAll=await api(`/api/club/${CLUB}/execution/occurrences`);
  renderExe();
  }catch(e){loaderError('#executionOccurrenceList',e,'活动执行加载失败')}
}
async function openExecution(oid){
  currentExecutionOccurrence=oid; let d=await api(`/api/club/${CLUB}/occurrences/${oid}/execution`); const s=d.summary||{},o=d.occurrence||{},st=d.settings||{};
  const groups=d.groups||[],leaders=d.leaders||[],notices=d.notices||[],parts=d.participants||[];
  $('#executionDetail').innerHTML=`<div class="card"><div class="panel-title"><div><div class="eyebrow">OCCURRENCE EXECUTION</div><h2 style="margin:4px 0">${esc(o.activity_title)}</h2><div class="sub">${esc(o.label||o.start_at)} · ${esc(o.activity_location||'')} · 执行状态 ${execStateLabel[o.execution_status||'preparing']||esc(o.execution_status)}</div></div><div style="display:flex;gap:8px;flex-wrap:wrap"><a class="btn ghost" target="_blank" href="/leader?occurrence=${oid}&club_id=${CLUB}" style="text-decoration:none">打开领队执行页</a><button class="btn secondary" onclick="advanceExecution(${oid})">推进状态</button></div></div>
  <div class="grid g4 section">${[['实名参加人',s.participantCount],['资料待补',s.incompleteCount],['保险待处理',s.insurancePendingCount],['已签到',s.checkedInCount]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]||0}</div></div>`).join('')}</div>
  ${d.issues?.length?`<div class="notice warn"><strong>出发前需要处理</strong><div class="sub" style="margin-top:6px">${d.issues.map(esc).join('；')}</div></div>`:`<div class="notice"><strong>准备状态良好</strong><div class="sub">当前未发现资料、保险或退款阻塞项。</div></div>`}
  </div>
  <div class="grid g2 section">
    <div class="card"><div class="panel-title"><h3>集合与应急信息</h3><button class="btn secondary" onclick="editExecutionSettings(${oid})">编辑</button></div><div><strong>集合时间</strong><div class="sub">${esc(st.meeting_time||'待设置')}</div></div><div style="margin-top:10px"><strong>集合地点</strong><div class="sub">${esc(st.meeting_location||'待设置')}</div></div><div style="margin-top:10px"><strong>应急电话</strong><div class="sub">${esc(st.emergency_phone||'待设置')}</div></div><div style="margin-top:10px"><strong>领队备注</strong><div class="sub">${esc(st.leader_note||'—')}</div></div></div>
    <div class="card"><div class="panel-title"><h3>领队</h3><button class="btn secondary" onclick="addExecutionLeader(${oid})">＋ 领队</button></div><div class="sub" style="margin-bottom:8px">与俱乐部领队资源库同步：这里派了谁，C 端活动页「带队领队」就显示谁（带头像与擅长）。</div>${leaders.map(x=>`<div class="list-row ex-lead"><span class="ex-lead__av">${leaderAvatar(x.name||'',x.avatar_url,'lg')}</span><div class="ex-lead__main"><div class="ex-lead__top"><b>${esc(x.name)}</b>${x.leader_id?'':'<span class="tag orange" title="历史手打记录，对不上资源库">未入册</span>'}</div><div class="ex-lead__meta"><i>${esc(x.role||'领队')}</i>${x.phone?`<u>${esc(x.phone)}</u>`:''}</div>${x.note?`<div class="ex-lead__note">${esc(x.note)}</div>`:''}</div><button class="btn ghost" title="撤下这位领队" onclick="removeExecutionLeader(${oid},${x.id})">撤下</button></div>`).join('')||'<div class="empty">尚未安排领队</div>'}</div>
  </div>
  <div class="grid g2 section"><div class="card"><div class="panel-title"><h3>车辆 / 分组</h3><button class="btn secondary" onclick="addExecutionGroup(${oid})">＋ 分组</button></div>${groups.map(g=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(g.name)}</div> <span class="tag">${esc(g.group_type)}</span><div class="list-row__sub">${g.assigned_count||0}/${g.capacity||'不限'} · ${esc(g.leader_name||'')}</div></div></div>`).join('')||'<div class="empty">尚未分组</div>'}</div>
  <div class="card"><div class="panel-title"><h3>活动通知</h3><button class="btn secondary" onclick="addExecutionNotice(${oid})">＋ 通知</button></div><div class="sub" style="margin-bottom:8px">标记发送后同步到 C 端顾客订单的「出行指引」；草稿仅内部可见。</div>${notices.map(n=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(n.title)}</div> <span class="tag ${n.status==='sent'?'':'orange'}">${n.status==='sent'?'已发送':'草稿'}</span> <span class="tag">${esc(n.channel||'manual')}</span><div class="list-row__sub">${esc(n.content)}${n.sent_at?' · 发送于 '+esc(String(n.sent_at).slice(0,16)):''}</div>${n.status!=='sent'?`<button class="btn ghost" style="margin-top:6px" onclick="sendExecutionNotice(${oid},${n.id})">标记发送</button>`:''}</div></div>`).join('')||'<div class="empty">尚未创建通知</div>'}</div></div>
  <div class="card section"><div class="panel-title"><div><h3>出发名单</h3><div class="sub">资料、保险、车辆、签到统一在这里看。</div></div><div style="display:flex;gap:8px;flex-wrap:wrap"><a class="btn ghost" href="/api/club/${CLUB}/occurrences/${oid}/insurance/export.csv">导出保险名单</a><button class="btn secondary" onclick="batchInsurance(${oid})">批量标记已提交保险</button></div></div>
  <div style="overflow:auto"><table class="table"><thead><tr><th>参加人</th><th>资料</th><th>保险</th><th>车辆</th><th>签到</th><th>操作</th></tr></thead><tbody>${parts.map(p=>`<tr><td><strong>${esc(p.name)}</strong><div class="sub">${esc(p.phone||'')} · 付款人 ${esc(p.payer_name||'')}</div></td><td><span class="tag ${p.form_status==='complete'?'':'orange'}">${p.form_status==='complete'?'完整':'待补'}</span></td><td>${({pending:'待投保',enrolling:'投保中',insured:'已投保',cancelling:'退保中',cancelled:'已退保',failed:'投保失败',cancel_failed:'退保失败',not_required:'无需保险',submitted:'已提交',processing:'办理中',done:'已投保',completed:'已投保'})[p.insurance_status]||esc(p.insurance_status||'待投保')}<div class="sub">${esc(p.insurance_policy_no||p.insurance_provider||'')}${p.effective_at?` · 生效 ${esc(String(p.effective_at).slice(0,10))}`:''}</div></td><td>${esc(p.vehicle_group||'未分配')}</td><td><span class="tag ${p.checkin_status==='checked_in'?'':'orange'}">${esc(p.checkin_status||'pending')}</span></td><td><button class="btn ghost" onclick="assignVehicle(${oid},${p.id})">分车</button> <button class="btn ghost" onclick="quickCheckin(${oid},${p.id},'${p.checkin_status||'pending'}')">签到</button></td></tr>`).join('')||'<tr><td colspan="6" class="empty">暂无实名参加人</td></tr>'}</tbody></table></div></div>`;
  $('#executionDetail').scrollIntoView({behavior:'smooth',block:'start'});
}


// v0.24: strictly club-scoped read-only intelligence; no platform-cost data.
function biLine(k,v,detail='') {return `<div style="display:flex;align-items:baseline;justify-content:space-between;gap:10px;padding:9px 0;border-bottom:1px solid var(--line)"><span class="sub">${esc(k)}</span><div style="text-align:right"><strong>${esc(String(v))}</strong>${detail?`<div class="sub">${esc(detail)}</div>`:''}</div></div>`}
function biCard(title,value,hint){return `<div class="stat-tile"><div class="k">${esc(title)}</div><div class="v">${esc(String(value))}</div><div class="hint">${esc(hint||'')}</div></div>`}
function biTrend(d){
  const days=d.trend||[];let groups=[];const step=days.length>100?30:days.length>40?7:1;
  for(let i=0;i<days.length;i+=step){let block=days.slice(i,i+step), sum=k=>block.reduce((a,x)=>a+Number(x[k]||0),0);groups.push({label:block[0].date.slice(5),activity:sum('activityPaidCash'),refund:sum('activityRefundCash'),gear:sum('gearAttributedGMV')})}
  const max=Math.max(1,...groups.flatMap(x=>[x.activity,x.refund,x.gear]));
  const legend=`<div class="sub" style="margin-bottom:12px">每组 ${step===1?'1天':step===7?'7天':'约30天'} · 深条=报名实收 · 中条=退款 · 浅条=商城GMV</div>`;
  // 本期一笔现金流都没有：画一排高度为 0 的柱子只会留下一大片空白，看起来像图表加载失败。
  // 空态要说清「是没数据」而不是「坏了」。
  const total=groups.reduce((s,x)=>s+x.activity+x.refund+x.gear,0);
  if(!total)return legend+`<div class="bi-trend-empty"><b>本期还没有可归因的现金流</b><span>报名实收 / 退款 / 商城 GMV 都是 0。有真实报名之后，这里会自动出现趋势。</span></div>`;
  return legend+`<div style="display:flex;align-items:end;gap:4px;height:150px;overflow:hidden;border-bottom:1px solid var(--line)">${groups.map(x=>`<div title="${esc(x.label)} 实收 ${money(x.activity)} / 退款 ${money(x.refund)} / 商城 ${money(x.gear)}" style="min-width:2px;flex:1;height:100%;display:flex;align-items:end;gap:1px"><div style="flex:1;height:${Math.max(0,x.activity/max*100)}%;background:#315a48;border-radius:3px 3px 0 0"></div><div style="flex:1;height:${Math.max(0,x.refund/max*100)}%;background:#bf7b42;border-radius:3px 3px 0 0"></div><div style="flex:1;height:${Math.max(0,x.gear/max*100)}%;background:#8cc2a8;border-radius:3px 3px 0 0"></div></div>`).join('')}</div><div class="sub" style="margin-top:6px;display:flex;justify-content:space-between"><span>${esc(groups[0]?.label||'')}</span><span>${esc(groups[groups.length-1]?.label||'')}</span></div>`;
}
async function loadClubBI(){
 /* #830 统计周期改直接点选（占位容器在 index.html） */
 const bw=$('#clubBIWindowWrap');
 if(bw&&!bw.__wired){bw.__wired=true;bw.innerHTML=uxChoiceHtml({id:'clubBIWindow',name:'clubBIWindow',value:'30',options:[{value:'7',label:'近7天'},{value:'30',label:'近30天'},{value:'90',label:'近90天'},{value:'365',label:'近365天'}],onchange:'loadClubBI()'});}
 const w=Number($('#clubBIWindow')?.value||30);
 $('#analyticsMetrics').innerHTML='<div class="card">正在读取真实经营数据…</div>';
 try{
  const d=await api(`/api/club/${CLUB}/analytics?windowDays=${w}`),a=d.activity,m=d.membership,g=d.commerce,ai=d.ai;
  $('#analyticsMetrics').innerHTML=[
   biCard('活动现金净流入',money(a.netCashFlowInWindow),`本期报名实收 ${money(a.paidCashInWindow)} · 本期退款 ${money(a.confirmedRefundCashInWindow)}`),
   biCard('本期报名订单',a.bookingOrders,`活跃参加人 ${a.activeParticipants} · 独立付费用户 ${a.payingUsers}`),
   biCard('商城归因 GMV',money(g.attributedGMV),`${g.attributedOrderCount} 笔订单 · 平台负责履约`),
   biCard('佣金净流水',money(g.commissionNetMovement),`实际打款 ${money(g.settlementPaidInWindow)}（单独统计）`),
   biCard('会员总数',m.totalMembers,`本期新增 ${m.newMembersInWindow}`),
   biCard('重复付费用户',a.repeatPayingUsers,`占本期付费用户 ${a.repeatPayingUserRatioPct}% · 非完整留存率`),
   biCard('本期 智能消耗',`${ai.creditsConsumedInWindow} Credits`,`成功 ${ai.successfulCalls} 次 · 失败 ${ai.failedCalls} 次`),
   biCard('当前智能余额',`${ai.currentBalance} Credits`,`待偿欠账 ${ai.unresolvedDebt}`)
  ].join('');
  $('#clubBITrend').innerHTML=biTrend(d);
  $('#clubBIActivityFinance').innerHTML=biLine('本期报名实收',money(a.paidCashInWindow))+biLine('本期实际现金退款',money(a.confirmedRefundCashInWindow))+biLine('本期现金净流入',money(a.netCashFlowInWindow))+biLine('本期订单截至当前净实收',money(a.bookingCohortNetCash),'含本期订单后来发生的退款')+biLine('俱乐部承担折扣',money(a.clubFundedDiscount))+biLine('平台承担补贴',money(a.platformFundedSubsidy))+biLine('全额退款订单',a.fullRefundOrders+' 笔')+biLine('部分退款订单',a.partialRefundOrders+' 笔');
  $('#clubBIMembers').innerHTML=biLine('本期独立付费用户',a.payingUsers)+biLine('重复付费用户',a.repeatPayingUsers)+biLine('新会员',m.newMembersInWindow)+biLine('会员活动积分余额',m.clubPointsBalance)+biLine('本期积分发放',m.clubPointsIssuedInWindow)+biLine('本期积分减少',m.clubPointsDebitedInWindow)+'<div class="sub" style="margin-top:14px">会员等级结构</div>'+Object.entries(m.tierCounts).map(([k,v])=>biLine(k,v+' 人')).join('');
  $('#clubBICommerceAI').innerHTML=biLine('商城归因销售额',money(g.attributedGMV))+biLine('商城订单已付现金',money(g.attributedCashPaid))+biLine('本期商城现金退款',money(g.cashRefundedInWindow))+biLine('本期新增佣金',money(g.commissionEarnedInWindow))+biLine('本期佣金退款冲回',money(g.commissionReversedInWindow))+biLine('本期实际结算',money(g.settlementPaidInWindow))+biLine('本期智能奖励',g.aiRewardCreditsInWindow+' Credits')+biLine('本期 智能调用成功',ai.successfulCalls+' 次');
  $('#clubBIActivities').innerHTML=d.activities.length?`<div style="overflow-x:auto"><table class="table"><thead><tr><th>活动</th><th>报名订单</th><th>付费用户</th><th>报名实收</th><th>已确认退款</th><th>订单净实收</th><th>俱乐部折扣</th><th>平台补贴</th></tr></thead><tbody>${d.activities.map(x=>`<tr><td>${esc(x.title)}</td><td>${x.orders}</td><td>${x.paidBuyers}</td><td>${money(x.paidCash)}</td><td>${money(x.confirmedRefundCash)}</td><td><strong>${money(x.netCash)}</strong></td><td>${money(x.clubDiscount)}</td><td>${money(x.platformSubsidy)}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">所选周期暂无成功付费报名。</div>';
  $('#clubBIDefinitions').innerHTML=Object.entries(d.definitions).map(([k,v])=>`<div style="padding:4px 0">${esc(v)}</div>`).join('');
 }catch(e){ $('#analyticsMetrics').innerHTML=`<div class="notice warn">经营数据读取失败：${esc(e.message)}</div>`; }
}

/* ---- 报名与执行：按活动 → 按档期钻取（9 号团与 10 号团必须分开看）---- */
function statTile(k,v,warn){return `<div class="reg-occ__stat${warn?' warn':''}"><div class="k">${esc(k)}</div><div class="v">${esc(String(v))}</div></div>`}
function regRow(x){
  const n=regPcount(x);
  const rf=x.refund_status&&x.refund_status!=='none';
  return `<div class="list-row"><div class="list-row__main"><div class="list-row__title">付款人 ${esc(x.name)}${x.phone?` · ${esc(x.phone)}`:''}</div><div class="list-row__sub">${n} 位参加人 · 资料完整 ${Number(x.complete_participants||0)}/${n} · 待处理保险 ${Number(x.insurance_pending||0)} · 活动积分抵 ${money(x.club_point_discount)} / 平台补贴 ${money(x.platform_point_subsidy)}</div><div class="list-row__sub">报名时间 ${esc(x.created_at||'')}${rf?` · 退款状态 ${enumCn(x.refund_status,'')}`:''}</div></div><div class="list-row__end"><span class="act-card__price">${money(x.amount)}</span><span class="tag ${x.status==='paid'?'':'orange'}">${enumCn(x.status)}</span><button class="btn ghost" onclick="event.stopPropagation();showParticipants(${x.id})">参加人名单</button></div></div>`;
}
function occPrepBlock(o){
  const ex=execStateLabel[o.execution_status||'preparing']||o.execution_status||'准备中';
  const full=Number(o.capacity||0)>0&&Number(o.sold||0)>=Number(o.capacity||0);
  const todo=Number(o.incomplete_participants||0)+Number(o.insurance_pending||0);
  return `<div class="reg-occ__block"><div class="reg-occ__label">这个档期的执行准备</div>
    <div class="reg-occ__stats">
      ${statTile('实名参加人',o.named_participants||0)}
      ${statTile('资料待补',o.incomplete_participants||0,Number(o.incomplete_participants||0)>0)}
      ${statTile('保险待处理',o.insurance_pending||0,Number(o.insurance_pending||0)>0)}
      ${statTile('已签到',o.checked_in||0)}
    </div>
    <div class="sub" style="margin-top:8px">执行状态 ${esc(ex)} · 名额 ${Number(o.sold||0)}/${Number(o.capacity||0)}${full?'（已满）':''}${todo?' · 出发前还有 '+todo+' 项要处理':''}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px"><button class="btn secondary" onclick="go('execution');openExecution(${o.id})">打开执行详情（分车 / 签到 / 通知）</button><a class="btn ghost" href="/api/club/${CLUB}/occurrences/${o.id}/insurance/export.csv">导出保险名单</a></div></div>`;
}
function occBlock(g){
  const o=g.occ, rl=g.regs;
  const parts=rl.reduce((s,x)=>s+regPcount(x),0);
  const req=rl.filter(x=>x.refund_status==='requested').length;
  const refd=rl.filter(x=>x.status==='refunded').length;
  const ex=execStateLabel[o.execution_status||'preparing']||o.execution_status||'准备中';
  return `<section class="reg-occ">
    <div class="reg-occ__head">
      <div><h4 class="reg-occ__title">${esc(o.label||o.start_at||'团期')}</h4>
      <div class="reg-occ__sub">${esc(o.start_at||'')}${o.end_at?' ～ '+esc(o.end_at):''}${o.activity_location?` · ${esc(o.activity_location)}`:''} · ${money(o.price)} · 名额 ${Number(o.sold||0)}/${Number(o.capacity||0)}</div></div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="tag ${(o.execution_status||'preparing')==='completed'?'':'orange'}">执行：${esc(ex)}</span></div>
    </div>
    <div class="reg-occ__block"><div class="reg-occ__label">这个档期的报名</div>
      <div class="reg-occ__stats">
        ${statTile('报名笔数',rl.length+' 笔')}
        ${statTile('参加人数',parts+' 人')}
        ${statTile('实收现金',money(regCash(rl)))}
        ${statTile('待审退款',req,req>0)}
        ${refd?statTile('已退款',refd+' 笔'):''}
      </div>
      <div style="margin-top:4px">${rl.map(regRow).join('')||'<div class="reg-none">这个档期还没有报名。</div>'}</div>
    </div>
    ${occPrepBlock(o)}
  </section>`;
}
function openActivityRegs(id){
  const a=(window.__regsActs||[]).find(x=>x.id===id); if(!a)return;
  const box=$('#activityRegDetail'); if(!box)return;
  const rs=(window.__regsRegs||[]).filter(x=>x.activity_id===id);
  const os=(window.__regsOccs||[]).filter(x=>x.activity_id===id).slice().sort((p,q)=>String(p.start_at||'').localeCompare(String(q.start_at||'')));
  const ids=os.map(o=>o.id);
  const groups=os.map(o=>({occ:o,regs:rs.filter(r=>r.occurrence_id===o.id)}));
  const orphan=rs.filter(r=>!r.occurrence_id||ids.indexOf(Number(r.occurrence_id))<0);
  const parts=rs.reduce((s,x)=>s+regPcount(x),0);
  box.innerHTML=`<div class="card">
    <div class="reg-activity-head">
      <div><div class="eyebrow">ACTIVITY · 报名与执行</div><h2 style="margin:4px 0">${esc(a.title)}</h2>
      <div class="sub">${esc(a.event_date||'')} · ${esc(a.location||'')} · ${money(a.price)} · <b>${os.length} 个档期</b> · 合计 ${rs.length} 笔报名 / ${parts} 人</div></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn ghost" onclick="goActivity(${a.id})">看活动成品</button><a class="btn ghost" href="/web?club_id=${CLUB}&activity=${a.id}" target="_blank" style="text-decoration:none">打开C端</a></div>
    </div>
    <div class="notice" style="margin:12px 0 16px">下面按档期分开列：每个档期只出现它自己的报名人和准备情况，9 号团和 10 号团不会混在一起。</div>
    ${groups.length?groups.map(occBlock).join(''):'<div class="reg-none">这场活动还没有团期。先到「活动中心」的活动详情里添加团期，报名和执行才能按档期分开统计。</div>'}
    ${orphan.length?`<section class="reg-occ"><div class="reg-occ__head"><div><h4 class="reg-occ__title">未绑定档期的报名</h4><div class="reg-occ__sub">这些报名没有关联团期，无法参与按档期的准备统计。</div></div></div><div class="reg-occ__block"><div class="reg-occ__label">报名（${orphan.length} 笔）</div>${orphan.map(regRow).join('')}</div></section>`:''}
  </div>`;
  box.scrollIntoView({behavior:'smooth',block:'start'});
}

/* ---------------------------------------------------------------------------
   活动基本信息的编辑 / 删除
   此前活动一旦生成就没有修改或删除入口：改不了名称/日期/价格，误建的活动也删不掉。
   注意：node 端的 post/patch 是 workflows-club.js 里 IIFE 的局部 const，不是全局，
   因此这里直接调全局 api()；api() 不会自己序列化 body，必须显式传 JSON 字符串。
--------------------------------------------------------------------------- */
/* 保存成功之后必须留下看得见的痕迹。原来只有一条 2.4 秒就消失的 toast，弹窗一关
   用户既不知道改了哪几项、也判断不了到底成没成功（用户明确反馈过「编辑修改活动之后
   不知道是否修改成功」）。这里在活动中心顶部留一条可关闭的横幅，只列**真正变化**的
   字段，并写明 智能详情文案不会跟着改 —— 否则用户会以为改了价格详情页就该跟着变。 */
function activitySavedBanner(changed){
  const host=document.getElementById('activities');if(!host)return;
  document.getElementById('actSavedBanner')?.remove();
  const when=new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
  const bar=document.createElement('div');
  bar.id='actSavedBanner';bar.className='act-saved';bar.setAttribute('role','status');
  bar.innerHTML='<span class="act-saved__ok" aria-hidden="true">✓</span>'
    +'<div class="act-saved__body"><b>活动信息已保存</b>'
    +'<span>'+(changed.length?'本次改动：'+esc(changed.join('、'))+' · ':'本次未改动任何字段 · ')+'已同步到 C 端<i>'+esc(when)+'</i></span>'
    +'<span class="act-saved__note">智能详情文案不会自动跟着改；需要重写文案或排版请点「重新生成 / 换一版」。</span></div>'
    +'<button type="button" class="act-saved__x" aria-label="关闭这条提示">×</button>';
  bar.querySelector('.act-saved__x').onclick=()=>bar.remove();
  host.prepend(bar);
  /* 30 秒够读完，不再是 toast 那种 2.4 秒；也可手动关掉。 */
  setTimeout(()=>{if(bar.isConnected)bar.remove()},30000);
}
/* 团期改了之后同样要留下看得见的痕迹，理由与上面 activitySavedBanner 完全一致：
   团期价格/时间直接决定 C 端能不能报名，老板必须确认「改的到底是哪几项、有没有生效」。
   与活动横幅互斥（同时出现两条会互相稀释），所以这里先把活动那条摘掉。 */
function occurrenceSavedBanner(changed){
  const host=document.getElementById('activities');if(!host)return;
  document.getElementById('actSavedBanner')?.remove();
  document.getElementById('occSavedBanner')?.remove();
  const when=new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
  const bar=document.createElement('div');
  bar.id='occSavedBanner';bar.className='act-saved';bar.setAttribute('role','status');
  bar.innerHTML='<span class="act-saved__ok" aria-hidden="true">✓</span>'
    +'<div class="act-saved__body"><b>团期已更新</b>'
    +'<span>'+(changed.length?'本次改动：'+esc(changed.join('、'))+' · ':'本次未改动任何字段 · ')+'C 端会按新团期展示<i>'+esc(when)+'</i></span>'
    +'<span class="act-saved__note">填了正价即视为正式对外定价，C 端不再显示「价格待定」；已生成的 智能详情文案不会自动跟着改。</span></div>'
    +'<button type="button" class="act-saved__x" aria-label="关闭这条提示">×</button>';
  bar.querySelector('.act-saved__x').onclick=()=>bar.remove();
  host.prepend(bar);
  setTimeout(()=>{if(bar.isConnected)bar.remove()},30000);
}
async function editActivity(id){
  let a=currentActivity;
  if(!a||Number(a.id)!==Number(id)){
    try{a=await api(`/api/club/${CLUB}/activities/${id}`)}catch(e){showAlert({title:'读取活动失败',message:e.message});return}
  }
  const before={title:String(a.title||''),eventDate:String(a.event_date||''),location:String(a.location||''),
                price:Number(a.price||0),capacity:Number(a.capacity||0),city:String(a.city||'')};
  const d=await uxForm({title:'编辑活动基本信息',
    subtitle:'改的是活动事实（名称 / 日期 / 地点 / 价格 / 名额），会同步到 C 端与页面上的事实字段；智能详情文案如需重写，请用「重新生成 / 换一版」。',
    fields:[
      {name:'title',label:'活动名称',type:'text',required:true,full:true,value:a.title||''},
      {name:'eventDate',label:'活动日期',type:'text',value:a.event_date||'',placeholder:'如：2026-10-24 或 2026年10月24日'},
      {name:'location',label:'集合地 / 目的地',type:'text',value:a.location||''},
      {name:'city',label:'活动城市（可选）',type:'select',options:[{value:'',label:'（暂不选 / 由地点推断）'}].concat((window.CLUBOS_CITIES||[]).map(c=>({value:c,label:c}))),value:a.city||''},
      {name:'price',label:'活动价格（元）',type:'number',min:0,step:.01,value:a.price||0},
      {name:'capacity',label:'总名额（人）',type:'number',min:0,step:1,value:a.capacity||0},
      {name:'checklist',label:'出行清单（每行一项）',type:'textarea',full:true,
       value:(a.activityMaster?.checklist||[]).join('\n'),
       placeholder:'冲锋衣\n登山杖\n头灯\n身份证件',
       help:'智能已按活动地点 / 天数 / 难度给出初稿；这里可增删改。保存后 C 端出行清单同步更新，商城可配的装备自动挂到对应条目上。'}
    ],submitText:'保存并更新活动'});
  if(!d)return;
  const checklistLines=String(d.checklist||'').split('\n').map(x=>x.trim()).filter(Boolean);
  await uxFlow('editActivity',async()=>{
    await api(`/api/club/${CLUB}/activities/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({title:d.title,eventDate:d.eventDate,location:d.location,city:d.city,price:d.price,capacity:d.capacity,checklist:checklistLines})});
    const beforeList=(a.activityMaster?.checklist||[]).join('|');
    const changed=[];
    if(String(d.title||'').trim()!==before.title.trim())changed.push('名称');
    if(String(d.eventDate||'').trim()!==before.eventDate.trim())changed.push('日期');
    if(String(d.location||'').trim()!==before.location.trim())changed.push('地点');
    if(String(d.city||'').trim()!==before.city.trim())changed.push('城市');
    if(Number(d.price)!==before.price)changed.push('价格');
    if(Number(d.capacity)!==before.capacity)changed.push('总名额');
    if(checklistLines.join('|')!==beforeList)changed.push('出行清单');
    toast(changed.length?'已保存：'+changed.join('、'):'已保存（本次没有字段变化）');
    await loadActivities();
    await openActivity(id);
    activitySavedBanner(changed);
  });
}

async function deleteActivity(id){
  let a=currentActivity;
  if(!a||Number(a.id)!==Number(id)){
    try{a=await api(`/api/club/${CLUB}/activities/${id}`)}catch(e){showAlert({title:'读取活动失败',message:e.message});return}
  }
  const occ=(a.occurrences||[]).length;
  if(!await uxConfirm({title:'删除活动',danger:true,confirmText:'确认删除',
    message:`将删除「${a.title}」及其 ${occ} 个团期、已生成的详情版本与宣发内容；此操作不可撤销。若活动已有报名，系统会拒绝删除并提示你先处理报名。`}))return;
  await uxFlow('deleteActivity',async()=>{
    try{await api(`/api/club/${CLUB}/activities/${id}`,{method:'DELETE'})}
    catch(e){showAlert({title:'暂时不能删除',message:e.message});return}   // 409：还有未取消的报名，透出后端原因
    toast('活动已删除');
    if(Number(_actOpenId)===Number(id)){_actOpenId=null;const pane=$('#activityDetail');if(pane)pane.innerHTML='<div class="empty">左侧选择一个活动查看详情</div>'}
    await loadActivities();
  });
}

/* ---------------------------------------------------------------------------
   在「我的装备商城」里定位并高亮一件商品。
   活动详情 → 出行清单里的推荐要能一键跳到本俱乐部商城中的这件商品，
   否则用户只看到"推荐了什么、多少钱"，却不知道去哪里下单。
--------------------------------------------------------------------------- */
async function openGearProduct(pid){
  const btn=document.querySelector('.nav button[data-view="mall"]');
  if(btn)btn.click();
  let el=null;
  for(let i=0;i<20&&!el;i++){
    el=document.querySelector('#clubProducts .product[data-pid="'+Number(pid||0)+'"]');
    if(!el)await new Promise(r=>setTimeout(r,120));
  }
  if(!el){toast('这件商品已下架，或不在本俱乐部商城里');return}
  el.scrollIntoView({behavior:'smooth',block:'center'});
  el.classList.add('flash');setTimeout(()=>el.classList.remove('flash'),1800);
}

/* ---------------------------------------------------------------------------
   带队领队 / 领队资源库
   过去每场活动只能手打「领队姓名 + 电话」，没有名册：既无法复用同一个人，
   也回答不了"这条线路以前是谁带的"。现在从资源库挑人，系统按「带过同线路 /
   同类型活动」给出推荐，并把理由写在按钮上——推荐必须是能讲清楚的，不做黑盒。
--------------------------------------------------------------------------- */
function renderLeaderCard(a,id){
  const lp=a?.leaderPlan||{};
  const occ=lp.occurrences||[],roster=lp.roster||[];
  // 团期上的领队是 occurrence_leaders 里的历史手打记录，头像要从名册按 leader_id 反查；
  // 查不到（早期没排到名册的人）就退回姓名首字占位，不显示空白。
  const avOf=lid=>{const r=(lp.roster||[]).find(x=>Number(x.id)===Number(lid));return r?r.avatar_url:''};
  const occRows=occ.map(o=>{
    const has=(o.leaders||[]).length;
    const chips=has
      ? o.leaders.map(x=>`<span class="leader-chip">${leaderAvatar(x.name||'',avOf(x.leaderId))}<b>${esc(x.name)}</b><i>${esc(x.role||'领队')}</i>${x.phone?`<u>${esc(x.phone)}</u>`:''}<button type="button" class="leader-chip__x" title="撤下这位领队" onclick="unassignLeader(${o.occurrenceId},${x.assignmentId},${id})">×</button></span>`).join('')
      : '<span class="leader-empty">本团期还没有安排领队</span>';
    const recs=(o.recommendations||[]).filter(r=>!r.alreadyAssigned).map(r=>
      `<button type="button" class="leader-rec" onclick="assignLeader(${o.occurrenceId},${r.leaderId},${id})">`
      +`${leaderAvatar(r.name,avOf(r.leaderId))}<b>${esc(r.name)}</b><i>${esc(r.reason)}</i><span>指派</span></button>`).join('');
    return `<div class="leader-occ"><div class="leader-occ__head"><b>${esc(o.label||'')}</b><span>${esc(o.startAt||'')}</span>`
      +`<button type="button" class="act-op" onclick="openLeaderPick(${o.occurrenceId},${id})">指派领队</button></div>`
      +`<div class="leader-chips">${chips}</div>`
      +(recs?`<div class="leader-recs"><span class="leader-recs__t">系统推荐</span>${recs}</div>`:leaderRecNote(roster.filter(r=>r.status==='active').length))
      +`</div>`;
  }).join('')||'<div class="empty">先在「团期 / 价格 / 名额」里添加团期，再安排带队领队</div>';
  const rosterRows=roster.map(r=>`<div class="list-row leader-row"><div class="list-row__main">`
      +`<div class="list-row__title">${leaderAvatar(r.name,r.avatar_url,'sm')}${esc(r.name)} <span class="tag ${r.status==='active'?'':'orange'}">${r.status==='active'?'在岗':'已停用'}</span></div>`
      +`<div class="list-row__sub">${esc(r.role||'领队')}${r.phone?' · '+esc(r.phone):''}${r.base_city?' · 常驻 '+esc(r.base_city):''} · 累计带队 ${Number(r.assignedCount||0)} 次</div>`
      +`<div class="list-row__sub">擅长：${(r.specialties||[]).length?esc(r.specialties.join('、')):'未填写'}`
      +`${r.ratingCount?` · ★${Number(r.ratingAvg||0).toFixed(2)}（${Number(r.ratingCount)} 条评价）`:' · 暂无评价'}`
      +`${Number(r.newInquiries||0)?` · <b style="color:#b8722d">咨询待回复 ${Number(r.newInquiries)}</b>`:''}</div></div>`
      +`<div class="list-row__end"><button class="btn ghost" onclick="editLeader(${r.id},${id})">编辑</button>`
      +`<button class="btn ghost" onclick="removeLeader(${r.id},${id})">${r.status==='active'?'停用':'删除'}</button></div></div>`).join('')
    ||'<div class="empty">资源库还没有领队：可到左侧菜单「领队资源库」统一建档，也可以直接在这里新增</div>';
  const active=LEADER_FORM&&LEADER_FORM.mode==='edit'?(roster.find(x=>Number(x.id)===Number(LEADER_FORM.leaderId))||{}):{};
  return `<div class="card section" id="sec-leaders"><div class="panel-title">`
    +`<div><h3>带队领队</h3><div class="sub">从俱乐部领队资源库里挑人；系统按「带过同线路 / 同类型活动」自动推荐，推荐理由直接写在按钮上。</div></div>`
    +`<button class="btn secondary" onclick="toggleLeaderForm('add',${id})">${LEADER_FORM&&LEADER_FORM.mode==='add'?'收起表单':'＋ 新增领队'}</button></div>`
    +`<div class="leader-summary">领队资源库 <b>${Number(lp.rosterCount||0)}</b> 人 · 在岗 <b>${Number(lp.activeCount||0)}</b> 人</div>`
    +`<div class="leader-occs">${occRows}</div>`
    +`<details class="leader-roster"${LEADER_FORM?' open':''}><summary>领队资源库（${Number(lp.rosterCount||0)} 人）</summary>${rosterRows}</details>`
    +`${LEADER_FORM?leaderFormHtml(id,active):''}</div>`;
}

async function assignLeader(occId,leaderId,actId){
  await uxFlow('assignLeader',async()=>{
    await api(`/api/club/${CLUB}/occurrences/${occId}/leaders`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({leaderId})});
    toast('已安排带队领队');
    await openActivity(actId);
  });
}

async function unassignLeader(occId,assignmentId,actId){
  if(!await uxConfirm({title:'撤下领队',message:'确定把这名领队从本团期撤下来吗？撤下后不再计入其带队记录。'}))return;
  await uxFlow('unassignLeader',async()=>{
    await api(`/api/club/${CLUB}/occurrences/${occId}/leaders/${assignmentId}`,{method:'DELETE'});
    toast('已撤下');
    await openActivity(actId);
  });
}

async function openLeaderPick(occId,actId){
  const lp=(currentActivity&&Number(currentActivity.id)===Number(actId))?(currentActivity.leaderPlan||{}):{};
  const occ=(lp.occurrences||[]).find(o=>Number(o.occurrenceId)===Number(occId))||{};
  const taken=new Set((occ.leaders||[]).map(x=>Number(x.leaderId||0)));
  const roster=(lp.roster||[]).filter(r=>r.status==='active'&&!taken.has(Number(r.id)));
  if(!roster.length){showAlert({title:'没有可选的领队',message:'资源库里没有「在岗且尚未安排」的领队。请到左侧菜单「领队资源库」新增或恢复一位，再回来指派。'});return}
  const recIds=new Set((occ.recommendations||[]).map(r=>Number(r.leaderId)));
  const opts=roster.slice().sort((x,y)=>(recIds.has(Number(y.id))?1:0)-(recIds.has(Number(x.id))?1:0)).map(r=>({value:String(r.id),
    label:`${r.name}（${r.role||'领队'}${(r.specialties||[]).length?' · 擅长'+r.specialties.join('/'):''}${recIds.has(Number(r.id))?' · 系统推荐':''}）`}));
  const d=await uxForm({title:'指派带队领队',subtitle:'带「系统推荐」标记的是按历史带队记录算出来的更合适人选。',
    fields:[{name:'leaderId',label:'选择领队',type:'select',required:true,full:true,options:opts}],submitText:'指派'});
  if(!d)return;
  await assignLeader(occId,Number(d.leaderId),actId);
}

const LEADER_ROLE_OPTS=[{value:'领队',label:'领队'},{value:'副领队',label:'副领队'},{value:'教练',label:'教练'},{value:'向导',label:'向导'},{value:'随队医护',label:'随队医护'}];

/* 新增/编辑领队改成卡片内联表单，不再弹 uxForm 覆盖层。
   原先点「＋ 新增领队」会在「智能详情预览」正中间盖出一张弹窗，
   填完还得先关掉才能继续看正文 —— 这是很强的打断，也解释了
   「怎么在活动详情预览里又跳出一个东西」。
   内联展开后表单就长在领队卡片里，与下面的名册同屏，改完直接提交。 */
let LEADER_FORM=null;

/* 领队表单机器（内联表单 + 头像裁剪）同时服务两个宿主：
   活动详情卡片（activity）与俱乐部级「领队资源库」页面（pool）。
   LEADER_CTX 由宿主的加载函数设置；refreshLeaderPane / submitLeaderForm /
   removeLeader 按它决定「提交/删完后重绘谁」。两套宿主切入时都要先收掉
   对方打开的表单 —— #leaderInlineForm / #leaderCrop 是全局唯一 id，
   两个宿主同时挂着表单会出现重复 id，裁剪面板会挂到第一个命中的那个上。 */
let LEADER_CTX='activity';
let POOL_ROSTER=[],POOL_SPECS=[],POOL_INBOX=null;

function refreshLeaderPane(){
  if(LEADER_CTX==='pool'){renderPoolPane();return}
  if(!currentActivity||!$('#sec-leaders'))return;
  $('#sec-leaders').outerHTML=renderLeaderCard(currentActivity,currentActivity.id);
  // 整块重绘会把裁剪面板一起冲掉，但它背后是一个内存里的裁剪会话 ——
  // 重新挂载即可，别让用户选好的图因为一次无关重绘就白选了。
  mountLeaderCrop();
}

function toggleLeaderForm(mode,actId,leaderId){
  const same=LEADER_FORM&&LEADER_FORM.mode===mode&&Number(LEADER_FORM.actId)===Number(actId)
    &&(mode!=='edit'||Number(LEADER_FORM.leaderId)===Number(leaderId));
  dropLeaderDraft();   // 关掉旧表单：回收预览 URL、丢弃未提交的裁图与在途裁剪会话
  LEADER_FORM=same?null:{mode,actId:Number(actId),
    ...(mode==='edit'?{leaderId:Number(leaderId)}:{}),pendingFile:null,preview:null};
  refreshLeaderPane();
}

function leaderFormHtml(actId,r){
  const editing=LEADER_FORM.mode==='edit';
  const av=r.avatar_url||'';
  // 本地预览优先：刚裁好的图还没上传（新增态更是连 id 都还没有），
  // 不显示预览的话「选完图什么都没变」= 用户以为上传没响应。
  const shown=(LEADER_FORM.preview||av);
  const specs=(LEADER_CTX==='pool')?POOL_SPECS:((currentActivity&&currentActivity.leaderPlan&&currentActivity.leaderPlan.specialties)||[]);
  return `<div class="leader-inline" id="leaderInlineForm">
    <div class="leader-inline__head">
      <div class="leader-av-cell">
        <button type="button" class="leader-av-pick" data-ava-pick title="上传头像">
          <span class="leader-av-wrap" id="leaderAvWrap">${leaderAvatar(r.name||'',shown,'xl')}<span class="leader-av-cam" aria-hidden="true">${AVA_CAM_SVG}</span></span>
        </button>
        <div class="leader-av-meta">
          <b>${editing?`编辑领队 · ${esc(r.name||'')}`:'新增领队'}</b>
          <button type="button" class="leader-av-link" data-ava-pick data-ava-tip>${shown?'更换头像':'上传头像 · 可拖动缩放'}</button>
        </div>
        <input type="file" accept="image/*" data-ava-input hidden>
      </div>
    </div>
    <div class="leader-crop" id="leaderCrop" hidden></div>
    <div class="leader-inline__grid">
      <label>姓名 *<input name="name" value="${esc(r.name||'')}" required placeholder="如 王野"></label>
      <label>联系电话<input name="phone" value="${esc(r.phone||'')}" placeholder="手机号"></label>
      <div class="lf-field"><span class="lf-t">角色</span>${uxChoiceHtml({name:'role',value:r.role||'领队',options:LEADER_ROLE_OPTS})}</div>
      <label>常驻城市<input name="baseCity" value="${esc(r.base_city||'')}" placeholder="如 成都"></label>
      <label>带队年限<input name="yearsExp" type="number" min="0" max="60" step="1" value="${Number(r.years_exp||0)||''}" placeholder="如 9"></label>
      <div class="lf-field"><span class="lf-t">资质核验</span>${uxChoiceHtml({name:'certVerified',value:String(Number(r.cert_verified||0)),options:[{value:'1',label:'已核验（C 端显示「资质已核验」）'},{value:'0',label:'未核验'}]})}</div>
      <div class="lf-field"><span class="lf-t">状态</span>${uxChoiceHtml({name:'status',value:r.status||'active',options:[{value:'active',label:'在岗'},{value:'inactive',label:'停用'}]})}</div>
      <label class="full">擅长方向<input name="specialties" value="${esc((r.specialties||[]).join(','))}" placeholder="从这些里选：${esc(specs.join('、'))}"></label>
      <label class="full">领队简介（C 端显示）<textarea name="bio" rows="3" placeholder="一两句说清 TA 是什么样的人、擅长带什么线、带团风格。例：8 年川西带队经验，走过 40 多次高原线路，节奏偏稳，擅长照顾第一次上高原的队员。">${esc(r.bio||'')}</textarea></label>
      <label class="full">备注（仅内部可见）<textarea name="note" rows="2" placeholder="证件编号、紧急联系人、内部约定等，不会出现在 C 端">${esc(r.note||'')}</textarea></label>
    </div>
    <input type="hidden" name="avatarUrl" value="${esc(av)}">
    <div class="leader-inline__foot">
      <button class="btn" type="button" data-lf-submit>${editing?'保存':'加入资源库'}</button>
      <button class="btn ghost" type="button" data-lf-cancel>取消</button>
      ${editing?'<span class="leader-inline__hint">停用后不再参与推荐，但历史带队记录会保留。</span>':''}
    </div>
  </div>`;
}

/* ===== 头像上传 + 裁剪（v0.28.1）=====
   用户反馈两点：① 点头像没反应 ② 希望图片能调节、自动适配到最佳。

   ① 的根因是入口本身是「隐藏交互」：✎ 只在 hover 才浮出（触屏永远看不到），
      而可点的只有那枚蒙层、圆本身不是按钮；再加上选完图后圆里没有任何变化，
      于是一次正常的选图在页面上完全无迹可寻 —— 看起来就是「上传没响应」。
      现在：圆自己是 <button>、相机角标常显、旁边再给一行文字按钮，
      三处入口指向同一个 file input；选完立刻在圆里显示裁好的预览。

   ② 的「自动适配」= 进入即按 cover 居中铺满，横图再往上偏一点点
      （人像主体通常偏上 —— 这是启发式，不是人脸检测，不要吹成智能裁脸）；
      之后可拖动 / 滚轮 / 滑块微调，输出固定 512×512 方形 JPEG。 */
const AVA_CAM_SVG='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5a2 2 0 0 1 2-2h1.6l1.2-1.8h8.4L17.4 6.5H19a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><circle cx="12" cy="12" r="3.4"/></svg>';
const AVA_CROP_SIZE=228;   // 取景框逻辑边长，与 CSS .leader-crop__stage 的 228px 一致
const AVA_OUT=512;         // 输出边长：60px 显示下 4x 也清晰，体积仍在几十 KB
let LEADER_CROP=null;      // {img,url,w,h,scale,ox,oy}，与 DOM 解耦，重绘后能重新挂载

/* 「自动适配」的默认落点：cover 居中，横图再上移一点。
   竖图 / 方图本来就撑满取景框，额外上移只会裁掉下巴，所以只在明显横图时生效。 */
function autoCropOffsetY(w,h){
  const base=Math.max(AVA_CROP_SIZE/w,AVA_CROP_SIZE/h);
  return w>h*1.15?-h*base*0.08:0;
}

function openLeaderCrop(file){
  if(!file)return;
  if(!/^image\//.test(file.type||'')){showAlert({title:'这不是图片',message:'请选择 png / jpg / webp 之类的图片文件。'});return}
  if(file.size>5*1024*1024){showAlert({title:'图片太大',message:'单张上限 5MB，先在相册里压缩一下再传。'});return}
  const url=URL.createObjectURL(file);
  const img=new Image();
  img.onload=()=>{
    if(!img.naturalWidth||!img.naturalHeight){URL.revokeObjectURL(url);showAlert({title:'这张图读不出来',message:'浏览器无法解码这个文件，换一张试试。'});return}
    if(LEADER_CROP)URL.revokeObjectURL(LEADER_CROP.url);
    LEADER_CROP={img,url,w:img.naturalWidth,h:img.naturalHeight,
      scale:1,ox:0,oy:autoCropOffsetY(img.naturalWidth,img.naturalHeight)};
    mountLeaderCrop();
    const st=$('#leaderCrop');if(st&&st.scrollIntoView)st.scrollIntoView({block:'nearest',behavior:'smooth'});
  };
  img.onerror=()=>{URL.revokeObjectURL(url);showAlert({title:'这张图读不出来',message:'浏览器无法解码这个文件，换一张试试。'})};
  img.src=url;
}

function cropGeom(){
  const c=LEADER_CROP;
  const base=Math.max(AVA_CROP_SIZE/c.w,AVA_CROP_SIZE/c.h)*c.scale;
  return {base,x:AVA_CROP_SIZE/2+c.ox-c.w*base/2,y:AVA_CROP_SIZE/2+c.oy-c.h*base/2};
}

/* 图像必须始终盖满取景框，否则拖动会露出黑边（输出的方图会带一块空白） */
function clampCrop(){
  const c=LEADER_CROP;
  const base=Math.max(AVA_CROP_SIZE/c.w,AVA_CROP_SIZE/c.h)*c.scale;
  const mx=Math.max(0,(c.w*base-AVA_CROP_SIZE)/2),my=Math.max(0,(c.h*base-AVA_CROP_SIZE)/2);
  c.ox=Math.max(-mx,Math.min(mx,c.ox));
  c.oy=Math.max(-my,Math.min(my,c.oy));
}

function paintLeaderCrop(){
  const cv=$('#leaderCropCv');
  if(!cv||!LEADER_CROP)return;
  const dpr=Math.min(window.devicePixelRatio||1,2),px=Math.round(AVA_CROP_SIZE*dpr);
  // 画布像素固定、CSS 尺寸自适应：小屏取景框会被拉宽，1:1 的比例保证不变形
  if(cv.width!==px){cv.width=px;cv.height=px}
  const ctx=cv.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.fillStyle='#0f1f19';ctx.fillRect(0,0,AVA_CROP_SIZE,AVA_CROP_SIZE);
  const g=cropGeom();
  // 缩照片时必须开高质量插值，否则细密画面会出摩尔纹
  ctx.imageSmoothingEnabled=true;ctx.imageSmoothingQuality='high';
  ctx.drawImage(LEADER_CROP.img,g.x,g.y,LEADER_CROP.w*g.base,LEADER_CROP.h*g.base);
}

function setCropScale(v){
  if(!LEADER_CROP)return;
  LEADER_CROP.scale=Math.max(1,Math.min(3.2,v));
  clampCrop();paintLeaderCrop();
  const z=$('#leaderCropZoom');if(z)z.value=Math.round(LEADER_CROP.scale*100);
}

function mountLeaderCrop(){
  const box=$('#leaderCrop');if(!box)return;
  if(!LEADER_CROP){box.hidden=true;box.innerHTML='';return}
  box.hidden=false;
  box.innerHTML=`
    <div class="leader-crop__stage" id="leaderCropStage">
      <canvas id="leaderCropCv"></canvas>
      <div class="leader-crop__ring"></div>
    </div>
    <div class="leader-crop__side">
      <div class="leader-crop__t">调整头像</div>
      <div class="leader-crop__d">已自动适配并居中。按住拖动可以挪位置，滚轮或下面的滑块缩放 —— 圆圈范围内的就是最终头像。</div>
      <label class="leader-crop__zoom"><i>缩放</i><input type="range" id="leaderCropZoom" min="100" max="320" step="1" value="${Math.round(LEADER_CROP.scale*100)}"></label>
      <div class="leader-crop__row">
        <button class="btn" type="button" data-crop-ok>使用这张</button>
        <button class="btn ghost" type="button" data-crop-reset>自动适配</button>
        <button class="btn ghost" type="button" data-crop-cancel>取消</button>
      </div>
    </div>`;
  paintLeaderCrop();
  const stage=$('#leaderCropStage'),zoom=$('#leaderCropZoom');
  if(!stage)return;
  let drag=null;
  stage.addEventListener('pointerdown',e=>{
    if(!LEADER_CROP)return;
    stage.setPointerCapture(e.pointerId);stage.classList.add('is-drag');
    drag={x:e.clientX,y:e.clientY};
  });
  stage.addEventListener('pointermove',e=>{
    if(!drag||!LEADER_CROP)return;
    // 小屏上取景框被拉伸过，按实际渲染宽度换算，手指移动 1px 图才跟得上
    const r=stage.getBoundingClientRect();
    const k=r.width>0?AVA_CROP_SIZE/r.width:1;
    LEADER_CROP.ox+=(e.clientX-drag.x)*k;
    LEADER_CROP.oy+=(e.clientY-drag.y)*k;
    drag={x:e.clientX,y:e.clientY};
    clampCrop();paintLeaderCrop();
  });
  const stop=()=>{drag=null;stage.classList.remove('is-drag')};
  stage.addEventListener('pointerup',stop);
  stage.addEventListener('pointercancel',stop);
  stage.addEventListener('wheel',e=>{
    if(!LEADER_CROP)return;
    e.preventDefault();
    setCropScale(LEADER_CROP.scale*(e.deltaY<0?1.08:1/1.08));
  },{passive:false});
  if(zoom)zoom.addEventListener('input',()=>setCropScale(Number(zoom.value)/100));
}

/* 只更新头像圆与那行提示，不整块重绘 —— 重绘会吃掉用户已经填好的字段，
   也会把正在进行的裁剪会话的 DOM 一起掀掉。 */
function paintLeaderAvatar(){
  const box=$('#leaderInlineForm');if(!box)return;
  const name=(box.querySelector('[name=name]')||{}).value||'';
  const av=(LEADER_FORM&&LEADER_FORM.preview)||(box.querySelector('[name=avatarUrl]')||{}).value||'';
  const wrap=box.querySelector('#leaderAvWrap');
  if(wrap)wrap.innerHTML=leaderAvatar(name,av,'xl')+`<span class="leader-av-cam" aria-hidden="true">${AVA_CAM_SVG}</span>`;
  const tip=box.querySelector('[data-ava-tip]');
  if(tip)tip.textContent=av?'更换头像':'上传头像 · 可拖动缩放';
}

function leaderCropBlob(){
  const out=document.createElement('canvas');
  out.width=AVA_OUT;out.height=AVA_OUT;
  const ctx=out.getContext('2d');
  // 底色用白不用黑：透明 PNG 裁成 JPEG 时黑底会很难看，白底退化成普通白边更安全
  ctx.fillStyle='#fff';ctx.fillRect(0,0,AVA_OUT,AVA_OUT);
  ctx.imageSmoothingEnabled=true;ctx.imageSmoothingQuality='high';
  const f=AVA_OUT/AVA_CROP_SIZE,g=cropGeom();
  ctx.drawImage(LEADER_CROP.img,g.x*f,g.y*f,LEADER_CROP.w*g.base*f,LEADER_CROP.h*g.base*f);
  return new Promise(res=>out.toBlob(b=>res(b),'image/jpeg',0.9));
}

function resetLeaderCrop(){
  if(!LEADER_CROP)return;
  LEADER_CROP.scale=1;LEADER_CROP.ox=0;
  LEADER_CROP.oy=autoCropOffsetY(LEADER_CROP.w,LEADER_CROP.h);
  const z=$('#leaderCropZoom');if(z)z.value='100';
  paintLeaderCrop();
}

function cancelLeaderCrop(){
  if(LEADER_CROP)URL.revokeObjectURL(LEADER_CROP.url);
  LEADER_CROP=null;
  const box=$('#leaderCrop');if(box){box.hidden=true;box.innerHTML=''}
  // 取消 = 放弃这一次的选择，回到上传前的样子（编辑态还有原头像兜底）
  if(LEADER_FORM){
    if(LEADER_FORM.preview)URL.revokeObjectURL(LEADER_FORM.preview);
    LEADER_FORM.preview=null;LEADER_FORM.pendingFile=null;
  }
  paintLeaderAvatar();
}

async function confirmLeaderCrop(){
  if(!LEADER_CROP)return;
  const blob=await leaderCropBlob();
  URL.revokeObjectURL(LEADER_CROP.url);
  LEADER_CROP=null;
  const box=$('#leaderCrop');if(box){box.hidden=true;box.innerHTML=''}
  if(!blob){toast('图片处理失败，换一张试试');return}
  const file=new File([blob],'avatar.jpg',{type:'image/jpeg'});
  if(LEADER_FORM){
    if(LEADER_FORM.preview)URL.revokeObjectURL(LEADER_FORM.preview);
    LEADER_FORM.preview=URL.createObjectURL(blob);
    LEADER_FORM.pendingFile=file;
  }
  paintLeaderAvatar();
  // 编辑态已经有 leaderId，可以直接传；新增态要等档案建好才有 id（见 submitLeaderForm）
  if(LEADER_FORM&&LEADER_FORM.mode==='edit'&&Number(LEADER_FORM.leaderId)){
    try{
      const url=await uploadLeaderAvatar(Number(LEADER_FORM.leaderId),file);
      if(url){
        const b=$('#leaderInlineForm');
        if(b){const h=b.querySelector('[name=avatarUrl]');if(h)h.value=url}
        if(LEADER_FORM.preview)URL.revokeObjectURL(LEADER_FORM.preview);
        LEADER_FORM.preview=null;LEADER_FORM.pendingFile=null;
        paintLeaderAvatar();
      }
    }catch(err){showAlert({title:'头像上传失败',message:err.message})}
  }else{
    toast('头像已选好，保存后生效');
  }
}

/* 关表单时把没提交的东西一起清掉，别把 objectURL 漏在内存里 */
function dropLeaderDraft(){
  if(LEADER_CROP){URL.revokeObjectURL(LEADER_CROP.url);LEADER_CROP=null}
  if(LEADER_FORM&&LEADER_FORM.preview){URL.revokeObjectURL(LEADER_FORM.preview);LEADER_FORM.preview=null}
  const box=$('#leaderCrop');if(box){box.hidden=true;box.innerHTML=''}
}

async function uploadLeaderAvatar(lid,file){
  const fd=new FormData();fd.append('file',file);
  const r=await api(`/api/club/${CLUB}/leaders/${lid}/avatar`,{method:'POST',body:fd});
  const url=((r||{}).avatarUrl||'');
  toast(url?'头像已更新':'头像上传成功');
  return url;
}

async function submitLeaderForm(){
  if(!LEADER_FORM)return;
  const box=$('#leaderInlineForm');if(!box)return;
  const g=n=>String((box.querySelector(`[name="${n}"]`)||{}).value||'').trim();
  const name=g('name');
  if(!name){toast('姓名不能为空');box.querySelector('[name=name]').focus();return}
  const editing=LEADER_FORM.mode==='edit';
  const payload={name,phone:g('phone'),role:g('role')||'领队',baseCity:g('baseCity'),
                 specialties:g('specialties'),note:g('note'),
                 bio:g('bio'),yearsExp:g('yearsExp')===''?0:Number(g('yearsExp')),
                 certVerified:g('certVerified')==='1'};
  const av=g('avatarUrl');
  if(av)payload.avatarUrl=av;
  const st=g('status');
  if(st&&editing)payload.status=st;   // 只有编辑态才有状态字段，新增不该带 status
  const actId=LEADER_FORM.actId,lid=LEADER_FORM.leaderId,file=LEADER_FORM.pendingFile||null;
  const btn=box.querySelector('[data-lf-submit]');
  if(btn){btn.disabled=true;btn.textContent='保存中…'}
  try{
    await uxFlow('leaderInline',async()=>{
      let targetId=lid;
      if(editing){
        await api(`/api/club/${CLUB}/leaders/${lid}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        toast('领队信息已更新');
      }else{
        const res=await api(`/api/club/${CLUB}/leaders`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        targetId=Number((res||{}).id||0);
        toast('领队已加入资源库');
      }
      // 新增时选了头像：档案建好才有 id 可传，所以放在创建之后补传。
      if(file&&targetId){
        const url=await uploadLeaderAvatar(targetId,file);
        if(url)payload.avatarUrl=url;
      }
    });
    dropLeaderDraft();   // 连同没提交的预览一起回收，别把 objectURL 留在内存里
    LEADER_FORM=null;
    if(LEADER_CTX==='pool')await loadLeaders();else await openActivity(actId);
  }catch(e){
    if(btn){btn.disabled=false;btn.textContent=editing?'保存':'加入资源库'}
    showAlert({title:editing?'保存领队失败':'新增领队失败',message:e.message});
  }
}

document.addEventListener('click',e=>{
  const t=e.target;if(!t||!t.closest)return;
  if(t.closest('[data-lf-submit]')){e.preventDefault();submitLeaderForm();return}
  if(t.closest('[data-lf-cancel]')){e.preventDefault();dropLeaderDraft();LEADER_FORM=null;refreshLeaderPane();return}
  if(t.closest('[data-crop-ok]')){e.preventDefault();confirmLeaderCrop();return}
  if(t.closest('[data-crop-reset]')){e.preventDefault();resetLeaderCrop();return}
  if(t.closest('[data-crop-cancel]')){e.preventDefault();cancelLeaderCrop();return}
  const pk=t.closest('[data-ava-pick]');
  if(pk){
    // 两个入口（头像圆、文字按钮）共用同一个 cell 下的 file input
    const cell=pk.closest('.leader-av-cell')||pk.parentElement;
    const inp=cell&&cell.querySelector('[data-ava-input]');
    if(inp)inp.click();
  }
});

document.addEventListener('change',e=>{
  const t=e.target;
  if(!t||!t.matches||!t.matches('[data-ava-input]'))return;
  const f=t.files&&t.files[0];
  t.value='';   // 不清空的话，第二次选同一个文件不会再触发 change
  if(!f)return;
  if(!$('#leaderInlineForm')||!LEADER_FORM){toast('请先在领队卡片里打开新增 / 编辑表单');return}
  // 不再整块重绘：这里只把文件交给裁剪面板，后面的预览与上传都由它接手。
  // 原先「重绘 + 快照还原」是为了躲开输入被冲掉，但那套写法解决不了预览问题，
  // 反而每次都要重搭一遍 DOM —— 现在改成局部更新，那两个函数也就不需要了。
  openLeaderCrop(f);
});

// 没头像时头像圆里显示的是姓名首字，改名字要跟着变（有头像时跳过，免得白白重拉一次图）
document.addEventListener('input',e=>{
  const t=e.target;
  if(!t||!t.matches||!t.matches('#leaderInlineForm [name=name]'))return;
  const box=$('#leaderInlineForm');if(!box)return;
  const hasAv=(LEADER_FORM&&LEADER_FORM.preview)||(box.querySelector('[name=avatarUrl]')||{}).value;
  if(hasAv)return;
  const wrap=box.querySelector('#leaderAvWrap');
  if(wrap)wrap.innerHTML=leaderAvatar(t.value,'','xl')+`<span class="leader-av-cam" aria-hidden="true">${AVA_CAM_SVG}</span>`;
});

function editLeader(leaderId,actId){toggleLeaderForm('edit',actId,leaderId)}

async function removeLeader(leaderId,actId){
  const lp=(LEADER_CTX==='pool')?{roster:POOL_ROSTER}
    :((currentActivity&&Number(currentActivity.id)===Number(actId))?(currentActivity.leaderPlan||{}):{});
  const r=(lp.roster||[]).find(x=>Number(x.id)===Number(leaderId))||{};
  const active=r.status==='active';
  if(!await uxConfirm({title:active?'停用领队':'删除领队',danger:true,confirmText:active?'停用':'删除',
    message:active?`「${r.name||''}」将不再出现在推荐与排班里，已有带队记录保留。若他正被安排在某团期上，安排不会自动撤销。`
                  :`「${r.name||''}」没有带队记录，将被彻底删除。`}))return;
  await uxFlow('removeLeader',async()=>{
    const res=await api(`/api/club/${CLUB}/leaders/${leaderId}`,{method:'DELETE'});
    toast(res.mode==='deactivated'?'已停用（带队记录保留）':'已删除');
    if(LEADER_CTX==='pool')await loadLeaders();else await openActivity(actId);
  });
}


/* ---------------------------------------------------------------------------
   俱乐部级「领队资源库」页面
   名册管理过去只能一场一场钻进活动详情做——还没有活动时就建不了名册，
   第一场活动只能手打。现在左侧导航有固定入口：在这里统一建档，
   每场活动的「带队领队」直接挑人。表单与头像裁剪机器和活动详情共用
   （见 LEADER_CTX），不复制第二套，避免两处行为漂移。
--------------------------------------------------------------------------- */
async function loadLeaders(){
  LEADER_CTX='pool';
  if(LEADER_FORM){dropLeaderDraft();LEADER_FORM=null}
  skel('#lpRoster',3);
  try{
    const r=await api(`/api/club/${CLUB}/leaders`);
    POOL_ROSTER=(r&&r.leaders)||[];
    POOL_SPECS=(r&&r.specialties)||[];
    await loadLeaderInbox();
    renderPoolPane();
  }catch(e){POOL_ROSTER=[];POOL_SPECS=[];POOL_INBOX=null;renderPoolPane();loaderError('#lpRoster',e,'领队资源库加载失败')}
}

function poolRosterRows(roster){
  return roster.map(r=>`<div class="list-row leader-row"><div class="list-row__main">`
      +`<div class="list-row__title">${leaderAvatar(r.name,r.avatar_url,'sm')}${esc(r.name)} <span class="tag ${r.status==='active'?'':'orange'}">${r.status==='active'?'在岗':'已停用'}</span></div>`
      +`<div class="list-row__sub">${esc(r.role||'领队')}${r.phone?' · '+esc(r.phone):''}${r.base_city?' · 常驻 '+esc(r.base_city):''} · 累计带队 ${Number(r.assignedCount||0)} 次</div>`
      +`<div class="list-row__sub">擅长：${(r.specialties||[]).length?esc(r.specialties.join('、')):'未填写'}`
      +`${r.ratingCount?` · ★${Number(r.ratingAvg||0).toFixed(2)}（${Number(r.ratingCount)} 条评价）`:' · 暂无评价'}`
      +`${Number(r.newInquiries||0)?` · <b style="color:#b8722d">咨询待回复 ${Number(r.newInquiries)}</b>`:''}</div></div>`
      +`<div class="list-row__end"><button class="btn ghost" onclick="lpEditLeader(${r.id})">编辑</button>`
      +`<button class="btn ghost" onclick="lpRemoveLeader(${r.id})">${r.status==='active'?'停用':'删除'}</button></div></div>`).join('')
    ||'<div class="empty">资源库还没有领队：点右上角「＋ 新增领队」建好名册，之后每场活动直接挑人，系统也能开始按历史记录推荐。</div>';
}

function renderPoolPane(){
  const roster=POOL_ROSTER||[];
  const sum=$('#lpSummary');
  if(sum)sum.innerHTML=`领队资源库 <b>${roster.length}</b> 人 · 在岗 <b>${roster.filter(x=>x.status==='active').length}</b> 人 · 累计带队 <b>${roster.reduce((s,x)=>s+Number(x.assignedCount||0),0)}</b> 次`;
  // 右上角入口按钮的文字跟着表单状态走：表单展开时变「收起表单」，与活动详情卡里的行为一致。
  const addBtn=$('#lpAddBtn');
  if(addBtn)addBtn.textContent=(LEADER_FORM&&LEADER_FORM.mode==='add')?'收起表单':'＋ 新增领队';
  const rowsEl=$('#lpRoster');
  if(rowsEl)rowsEl.innerHTML=poolRosterRows(roster);
  const fEl=$('#lpForm');
  if(fEl)fEl.innerHTML=(LEADER_FORM&&LEADER_FORM.mode)
    ?leaderFormHtml(0,LEADER_FORM.mode==='edit'?(roster.find(x=>Number(x.id)===Number(LEADER_FORM.leaderId))||{}):{})
    :'';
  renderLeaderInbox();
  mountLeaderCrop();
}

/* 领队咨询箱（2026-10-27）：C 端「咨询领队」留的问题落在这里。
   没有这一块，C 端那个按钮就是往黑洞里发消息 —— 顾客以为问了，实际没人看见。 */
async function loadLeaderInbox(){
  try{POOL_INBOX=await api(`/api/club/${CLUB}/leader-inbox`)}catch(_e){POOL_INBOX=null}
}
function renderLeaderInbox(){
  const el=$('#lpInbox');if(!el)return;
  if(!POOL_INBOX){el.innerHTML='';return}
  const iq=POOL_INBOX.inquiries||[],rv=POOL_INBOX.reviews||[];
  const newN=iq.filter(x=>x.status==='new').length;
  el.innerHTML=`<details class="leader-inbox"${newN?' open':''}><summary>领队咨询与评价${newN?` · <b>待回复 ${newN}</b>`:''}</summary>`
    +`<div class="leader-inbox__h">顾客咨询（${iq.length}）${newN?` · 待回复 ${newN}`:' · 全部已回'}</div>`
    +(iq.length?iq.map(i=>`<div class="list-row"><div class="list-row__main">`
        +`<div class="list-row__title">${esc(i.name||'山友')} 咨询 ${esc(i.leaderName||'领队')} <span class="tag ${i.status==='new'?'orange':''}">${i.status==='new'?'待回复':'已回复'}</span></div>`
        +`<div class="list-row__sub">${esc(i.message)}</div>`
        +`<div class="list-row__sub">${esc(i.phone)}${i.activityTitle?' · 来自《'+esc(i.activityTitle)+'》':''} · ${esc(String(i.createdAt||'').slice(0,16))}</div></div>`
        +`<div class="list-row__end"><a class="btn ghost" href="tel:${esc(i.phone)}">打电话</a>`
        +`<button class="btn ghost" onclick="toggleInquiry(${Number(i.id)},${i.status==='new'?1:0})">${i.status==='new'?'标记已回复':'改回待回复'}</button></div></div>`).join('')
      :'<div class="empty">还没有顾客咨询。顾客在 C 端点开领队卡点「咨询领队」，问题就会出现在这里。</div>')
    +`<div class="leader-inbox__h">顾客评价（${rv.length}）</div>`
    +(rv.length?rv.map(r=>{const st=Math.max(0,Math.min(5,Number(r.rating)||0));return `<div class="list-row"><div class="list-row__main">`
        +`<div class="list-row__title">${esc(r.leaderName||'领队')} <span class="tag">${'★'.repeat(st)}${'☆'.repeat(5-st)}</span> <span style="font-size:12px;color:#6b7a70">${esc(r.author||'匿名山友')}</span></div>`
        +`<div class="list-row__sub">${esc(r.comment||'（只打了分，没写内容）')}</div>`
        +`<div class="list-row__sub">${esc(String(r.createdAt||'').slice(0,16))}</div></div></div>`}).join('')
      :'<div class="empty">还没有顾客评价。C 端领队主页里的「给 TA 评分」提交后会出现在这里。</div>')
    +`</div></details>`;
}
async function toggleInquiry(id,handled){
  await uxFlow('inquiryHandled',async()=>{
    await api(`/api/club/${CLUB}/leader-inquiries/${Number(id)}/handled`,{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({handled:!!Number(handled)})});
    toast(Number(handled)?'已标记为已回复':'已改回待回复');
    await loadLeaders();
    pollLeaderInquiries();   // 处理完立刻把角标 / 标题数字降下来，别等下一轮轮询
  });
}
function lpToggleForm(){toggleLeaderForm('add',0);
  // 设置页里表单长在名册下方：展开后滚到位，避免名册长时用户以为点了没反应。
  if(LEADER_FORM&&LEADER_FORM.mode==='add'){const f=$('#lpForm');if(f&&f.offsetParent!==null)f.scrollIntoView({behavior:'smooth',block:'nearest'})}}
function lpEditLeader(id){toggleLeaderForm('edit',0,id)}
function lpRemoveLeader(id){removeLeader(id,0)}


/* ---------------------------------------------------------------------------
   会员装备折扣 / 领队推荐空态 / 充值单价
   这几块都是「新能力必须在界面上看得见、说得清」：会员价不能只存在于接口里，
   领队推荐为空时也不能什么都不显示（用户会以为是坏了）。
--------------------------------------------------------------------------- */
let _tiers=[];
/* 2026-10-27 升级奖励 v2：奖励挂在等级上（可送券 + 送积分），随等级一起渲染。 */
let _tierRewards=[];
let _clubBenefits=[];
/* 2026-10-23 起装备折扣不配在会员等级上：由总平台按俱乐部订阅套餐统一控制，
   俱乐部端只读（loadMembers 已拉 /gear-discount 存在 _gearDiscount 里）。 */
let _gearDiscount=null;
/* 2026-10-27（用户要求）：条件开通与购买开通彻底分开 ——
   editTier   只管条件型（消费/次数达标自动升级），弹窗里不出现任何卡价/有效期；
   editTierCard 只管购买型（售价/有效期），弹窗里不出现消费/次数门槛。
   两边都能配「活动报名折扣」与「升级奖励（送券 / 送积分）」。 */
function tierRewardOf(tierId){
  return (_tierRewards||[]).find(x=>Number(x.tier_id)===Number(tierId))||null;
}
function tierRewardText(r){
  if(!r)return '';
  const bits=[];
  if(r.benefit_id&&r.benefit_title)bits.push('送「'+r.benefit_title+'」');
  if(Number(r.points_award||0)>0)bits.push('送 '+r.points_award+' 活动积分');
  return bits.join(' + ');
}
function rewardCouponOptions(){
  const opts=[{value:'0',label:'不送券'}];
  (_clubBenefits||[]).forEach(b=>{
    const face=b.mode==='discount'?`${Number(b.discount_percent||0)/10} 折券`
      :b.mode==='threshold'?`满 ${money(b.threshold_amount)} 减 ${money(b.cash_value)}`
      :`直抵 ${money(b.cash_value)}`;
    opts.push({value:String(b.id),label:`${b.title}（${face}）`});
  });
  return opts;
}
function rewardFields(tierId){
  const r=tierRewardOf(tierId);
  const hasCoupon=(_clubBenefits||[]).length>0;
  return [
    {name:'rewardBenefit',label:'升级奖励 · 送一张优惠券',type:'select',full:true,
     value:String((r&&r.benefit_id)||0),options:rewardCouponOptions(),
     help:hasCoupon?'只能送本俱乐部已建好且有库存的活动券；升到该等级时自动进会员券包。'
       :'本俱乐部还没有「活动券」可选 —— 先去下面「俱乐部营销 · 优惠券」点「＋ 优惠券」建一张（要有库存），或者只送积分。'},
    {name:'rewardPoints',label:'升级奖励 · 送活动积分（0 = 不送）',type:'number',min:0,step:1,
     value:Number((r&&r.points_award)||0),full:true,
     help:'活动积分由俱乐部承担，计入会员「活动积分」余额。券和积分可只配一项，也可两项都给。'}
  ];
}
function zheValue(adp){return Number(adp||0)>0?Number((Number(adp)/10).toFixed(1)):0}
async function saveTierReward(tierId,d){
  const bid=Number(d.rewardBenefit||0),pts=Number(d.rewardPoints||0);
  const cur=tierRewardOf(tierId);
  if(!bid&&pts<=0){
    if(cur&&cur.id)await api(`/api/club/${CLUB}/tier-reward-rules/${cur.id}`,{method:'DELETE'});
    return;
  }
  await api(`/api/club/${CLUB}/tier-reward-rules`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tierId:Number(tierId),benefitId:bid,pointsAward:pts})});
}
function discountField(adp){
  return {name:'discountZhe',label:'活动报名折扣（折）',type:'number',min:0,max:9.9,step:.1,
    value:zheValue(adp),full:true,
    help:'填 8 = 打 8 折，填 0 = 该等级不打折。会员报名「开启了会员优惠」的活动时按此价立减，活动上还要打开开关才生效。'};
}
/* 等级专属积分现金比（2026-11-05）：0 = 跟随俱乐部生效值；正数 = 本等级专属。
   例：普通 100 / 高级 50 / 顶级 20 积分各抵 ¥1。只影响活动积分报名抵扣。 */
function pointsRateField(rate){
  return {name:'pointsRedeemRate',label:'本等级积分换算（X 积分 = ¥1）',type:'number',min:0,max:10000,step:1,
    value:Number(rate||0),full:true,
    help:'填 0 = 跟随俱乐部默认；填正数 = 本等级专属（例：普通 100 / 高级 50 / 顶级 20）。只影响该等级会员报名时用活动积分抵扣，装备积分不受影响。'};
}
async function editTier(tierId){
  const r=(_tiers||[]).find(x=>Number(x.id)===Number(tierId));
  if(!r){showAlert({title:'读取等级失败',message:'请重新打开「客户会员」再试'});return}
  if(String(r.access_mode||'CONDITION')==='PURCHASE')return editTierCard(tierId);
  const d=await uxForm({title:'编辑等级会员 · '+(r.name||''),
    subtitle:'条件开通：会员累计活动消费 / 活动次数达标后自动升级，不需要付费。',
    fields:[
      {name:'name',label:'等级名称',type:'text',required:true,value:r.name||''},
      {name:'rank',label:'等级顺序（数字越大等级越高）',type:'number',min:0,step:1,value:r.rank||0},
      {name:'minActivitySpend',label:'累计活动消费 ≥（元）',type:'number',min:0,step:1,value:r.min_activity_spend||0},
      {name:'minActivityCount',label:'累计活动次数 ≥',type:'number',min:0,step:1,value:r.min_activity_count||0},
      {name:'qualificationMode',label:'升级条件',type:'select',full:true,value:r.qualification_mode||'ANY',
        options:[{value:'ANY',label:'任一满足'},{value:'ALL',label:'同时满足'}]},
      discountField(r.activity_discount_percent),
      pointsRateField(r.points_redeem_rate),
      ...rewardFields(tierId)
    ],submitText:'保存'});
  if(!d)return;
  await uxFlow('editTier',async()=>{
    let benefits=[];try{benefits=JSON.parse(r.benefits_json||'[]')||[]}catch(e){benefits=[]}
    // 必须把 benefits 原样带回：接口是全量覆盖，漏传会把这条等级已有的权益清空。
    await api(`/api/club/${CLUB}/membership/tiers/${tierId}`,{method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:d.name,rank:d.rank,minActivitySpend:d.minActivitySpend,
        minActivityCount:d.minActivityCount,qualificationMode:d.qualificationMode,benefits,
        accessMode:'CONDITION',price:0,validMonths:Number(r.valid_months||12),
        activityDiscountPercent:Math.round(Number(d.discountZhe||0)*10),pointsRedeemRate:Number(d.pointsRedeemRate||0)})});
    await saveTierReward(tierId,d);
    toast('等级会员已更新');
    await loadMembers();
  });
}
async function editTierCard(tierId){
  const r=(_tiers||[]).find(x=>Number(x.id)===Number(tierId));
  if(!r){showAlert({title:'读取会员卡失败',message:'请重新打开「客户会员」再试'});return}
  const d=await uxForm({title:'编辑付费会员卡 · '+(r.name||''),
    subtitle:'购买开通：顾客在 C 端付费购买这张卡，到期自动失效。卡价与有效期只在这里设置。',
    fields:[
      {name:'name',label:'会员卡名称',type:'text',required:true,value:r.name||''},
      {name:'rank',label:'卡等级顺序（数字越大权益越高）',type:'number',min:0,step:1,value:r.rank||0},
      {name:'price',label:'售价（元）',type:'number',min:0,step:.01,value:Number(r.price||399)},
      {name:'validMonths',label:'有效期（月）',type:'number',min:1,step:1,value:r.valid_months||12},
      discountField(r.activity_discount_percent),
      pointsRateField(r.points_redeem_rate),
      ...rewardFields(tierId)
    ],submitText:'保存'});
  if(!d)return;
  await uxFlow('editTierCard',async()=>{
    let benefits=[];try{benefits=JSON.parse(r.benefits_json||'[]')||[]}catch(e){benefits=[]}
    await api(`/api/club/${CLUB}/membership/tiers/${tierId}`,{method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:d.name,rank:d.rank,minActivitySpend:0,minActivityCount:0,
        qualificationMode:'ANY',benefits,
        accessMode:'PURCHASE',price:Number(d.price||0),validMonths:Number(d.validMonths||12),
        activityDiscountPercent:Math.round(Number(d.discountZhe||0)*10),pointsRedeemRate:Number(d.pointsRedeemRate||0)})});
    await saveTierReward(tierId,d);
    toast('会员卡已更新');
    await loadMembers();
  });
}
/* 商品价签：有会员折扣就同时给出会员价与划线原价，没有则维持原样。 */
function memberPriceHtml(price,md){
  const base=Number(price||0);
  if(!md||!(md.rate>0&&md.rate<1))return `<div class="price">${money(base)}</div>`;
  // 折后价固定两位小数：通用 money() 会把 539.10 显示成 ¥539.1，看起来像被截断。
  const fmt2=n=>'¥'+Number(n||0).toFixed(2);
  const mp=Math.round(base*md.rate*100)/100;
  return `<div class="price">${fmt2(mp)}<span class="price-was">${money(base)}</span></div>`
    +`<div class="price-member">${esc(md.tierName)} ${esc(md.discountZhe)} 折 · 省 ${fmt2(base-mp)}</div>`;
}
function unitCreditPrice(amount,credits){
  const a=Number(amount||0),c=Number(credits||0);
  if(!(c>0))return '—';
  const v=a/c, s=v>=1?v.toFixed(2):v.toFixed(3).replace(/0+$/,'');
  return '¥'+s.replace(/\.$/,'')+' / Credit';
}
function leaderRecNote(activeCount){
  return activeCount
    ? '<div class="leader-recs leader-recs--none"><span class="leader-note">暂时没有可推荐的人：系统只推「带过这条线路或同类活动」的领队，讲不出理由的不硬推。可直接点上面的「指派领队」从资源库里挑。</span></div>'
    : '<div class="leader-recs leader-recs--none"><span class="leader-note">资源库里还没有在岗领队。先新增一位，之后系统才能按历史带队记录给出推荐。</span></div>';
}

/* ---------------------------------------------------------------------------
   装备商城的两个只读二级页。
   商品归总平台所有：俱乐部既改不了价也改不了库存，能做的是「看清楚」。
   过去商品卡和订单行都是死的，点下去没有任何反应，用户只能靠猜。
   详情统一走 uxDialog —— 俱乐部端的弹窗层已经收口到这一套，不再自造 modal。
--------------------------------------------------------------------------- */
async function openClubProduct(pid){
  let list;
  try{ list=await api('/api/club/'+CLUB+'/mall/products') }
  catch(e){ showAlert({title:'读取商品失败',message:e.message}); return }
  const p=(list||[]).find(x=>Number(x.id)===Number(pid));
  if(!p){ showAlert({title:'这件商品不在本俱乐部商城',message:'商品由总平台统一上架，俱乐部端只能查看已上架商品。'}); return }
  const imgs=(p.images||[]).map(i=>`<img src="${esc(i.url)}" alt="${esc(p.name)}" style="width:100%;border-radius:10px;display:block;margin-bottom:8px">`).join('');
  const variants=(p.variants||[]).map(v=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(v.name||'默认规格')}</div>`
    +`<div class="list-row__sub">${esc(v.sku||'—')} · 库存 ${v.stock}${v.isDefault?' · 默认规格':''}</div>`
    +`<div class="list-row__end"><strong>${money(v.price)}</strong></div></div></div>`).join('')
    ||'<div class="sub">这件商品没有启用中的规格，按整件计。</div>';
  uxDialog({title:p.name,desc:`${esc(p.category||'户外装备')} · ${esc(p.sku||'')} · 售价 ${money(p.price)} · 库存 ${p.stock}`,
    body:`<div>${imgs}</div><div class="sub" style="margin:2px 0 10px">商品由总平台统一上架、定价与库存，俱乐部只读；销售归因见下面的订单与佣金。</div>${variants}`,
    initialFocus:'[data-ok]',foot:`<button class="btn" type="button" data-ok>关闭</button>`});
}
async function openClubOrder(id){
  let o=null;
  try{ const list=await api('/api/club/'+CLUB+'/mall/orders'); o=(list||[]).find(x=>Number(x.id)===Number(id)) }
  catch(e){ showAlert({title:'读取订单失败',message:e.message}); return }
  if(!o){ showAlert({title:'订单不存在',message:'这笔订单不属于本俱乐部。'}); return }
  const line=(k,v)=>`<div style="display:flex;justify-content:space-between;border-bottom:1px dashed #d8e3dd;padding:8px 0"><span style="color:#6b7f76">${esc(k)}</span><strong>${esc(v==null||v===''?'—':String(v))}</strong></div>`;
  const items=Number(o.total||0);
  const parts=(o.items||[]).map(i=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(i.product_name||('商品 #'+i.product_id))}${i.variant_name?` · ${esc(i.variant_name)}`:''}</div>`
    +`<div class="list-row__sub">单价 ${money(i.unit_price)} · 数量 ${i.quantity}${i.unit_cost_snapshot?` · 成本快照 ${money(i.unit_cost_snapshot)}`:''}</div>`
    +`<div class="list-row__end"><strong>${money(Number(i.unit_price||0)*Number(i.quantity||0))}</strong></div></div></div>`).join('')
    ||'<div class="sub">这笔订单没有商品明细。</div>';
  const body=`<div style="margin-top:6px">${line('订单号','#'+o.id)}${line('买家',o.buyer)}${line('收货人',[o.shipping_consignee,o.shipping_phone].filter(Boolean).join(' ')||'历史订单无地址')}${line('收货地址',o.shipping_address||'历史订单无地址')}${line('下单时间',fmtUtc8(o.created_at)+'（本地时间）')}`
    +`${line('订单状态',enumCn(o.status))}${line('订单金额',money(items))}${line('本单佣金',money(o.club_commission))}`
    +`${line('物流',[o.carrier,o.tracking_no].filter(Boolean).join(' ')||'待发货')}${line('售后',enumCn(o.after_sales_status,'无'))}</div>`
    +`<div style="margin-top:14px"><b class="sub">商品明细</b><div style="margin-top:6px">${parts}</div></div>`;
  uxDialog({title:'商城订单详情',desc:'俱乐部只读这笔订单的成交明细；发货与售后仍由总平台处理。',
    body,initialFocus:'[data-ok]',foot:`<button class="btn" type="button" data-ok>关闭</button>`});
}

/* ---------------------------------------------------------------------------
   收款账户：后端 get / patch 早就就绪，前端从没调过 —— 于是这页在界面上完全不存在。
   俱乐部只登记渠道信息，不动密钥；生产环境后端会拒绝 provider=local，直接把原因透出。
--------------------------------------------------------------------------- */
const PAY_PROV_CN={local:'本地模拟',wechatpay_v3:'微信支付 v3',alipay:'支付宝'};
async function loadPayAccount(){
  const box=$('#payAccount'); if(!box)return;
  skel('#payAccount',3);
  try{
    const a=await api('/api/club/'+CLUB+'/payment-account')||{};
    const prov=PAY_PROV_CN[a.provider]||a.provider||'未配置';
    const pair=[a.merchant_id,a.app_id].filter(Boolean).join(' / ')||'—';
    box.innerHTML=`<div class="grid g4"><div class="stat-tile"><div class="k">当前收款渠道</div><div class="v" style="font-size:22px">${esc(prov)}</div>`
      +`<div class="hint">${esc(a.channel||'未配置接入方式')}</div></div>`
      +`<div class="stat-tile"><div class="k">状态</div><div class="v" style="font-size:22px">${a.enabled?'已启用':'已停用'}</div><div class="hint">${esc(a.updated_at||'尚未保存过')}</div></div>`
      +`<div class="stat-tile"><div class="k">商户号 / AppID</div><div class="v" style="font-size:22px">${esc(pair)}</div><div class="hint">来自支付渠道配置</div></div></div>`
      +`<div class="section" style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn secondary" type="button" onclick="editPayAccount()">编辑收款账户</button></div>`;
  }catch(e){ box.innerHTML='<div class="empty">读取失败：'+esc(e.message)+'</div>' }
}
async function editPayAccount(){
  let cur={};
  try{ cur=await api('/api/club/'+CLUB+'/payment-account')||{} }
  catch(e){ showAlert({title:'读取收款账户失败',message:e.message}); return }
  const v=await uxForm({title:'编辑收款账户',subtitle:'这里只登记渠道信息；密钥仍由 ClubOS 支付层统一管理，不写进俱乐部账户。',
    fields:[
      {name:'provider',label:'收款渠道',type:'select',required:true,options:[{value:'local',label:'本地模拟（仅演示与内部环境）'},{value:'wechatpay_v3',label:'微信支付 v3'},{value:'alipay',label:'支付宝'}]},
      {name:'channel',label:'接入方式',required:true,value:(cur.channel||'mock')},
      {name:'merchantId',label:'商户号',full:true,value:(cur.merchant_id||'')},
      {name:'appId',label:'AppID',full:true,value:(cur.app_id||'')},
      {name:'enabled',label:'启用该账户',type:'select',options:[{value:'1',label:'启用'},{value:'0',label:'停用'}],value:(cur.enabled?'1':'0')}
    ],submitText:'保存'});
  if(!v)return;
  try{
    await api('/api/club/'+CLUB+'/payment-account',{method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({provider:v.provider,channel:v.channel,merchantId:v.merchantId,appId:v.appId,enabled:String(v.enabled)!=='0'})});
    toast('收款账户已保存');
    await loadPayAccount();
  }catch(e){ showAlert({title:'保存失败',message:e.message}) }
}

/* ===== Credits 流水 / AI 用量明细（2026-10-23 重做）=========================
   原来两块明细都是「一拉到底」的平铺：流水被 /credits 截成当月前 60 条，
   用量固定最近 100 条 —— 既翻不到更早的账，也看不出某一天/某一段时间到底花了多少。
   现在统一成「日期区间 + 区间汇总 + 按日分组 + 加载更多」：
     · 区间走服务端过滤（date_from/date_to=YYYY-MM-DD，按 +8 本地日历日，和商城订单同口径）
     · 汇总由服务端聚合（用量还按任务类型拆开，回答「花在哪一步」）
     · 默认近 7 天、每页 20 条，点「加载更多」续翻，不再一屏几百行
   两块共用 .led-* 组件（样式在 clubos-ux.css），筛选状态各自独立：
   切 tab、刷新余额都不会把用户已经选好的区间冲掉。 */
const LED_PAGE=20;
const LED_QUICK=[{k:'today',label:'今天'},{k:'7d',label:'近 7 天'},{k:'30d',label:'近 30 天'},{k:'month',label:'本月'},{k:'all',label:'全部'}];
/* 任务名与 channel-render.js 的 LABEL 对齐：渠道生成时 task_type 直接就是渠道 id
   （wechat/xhs/poster/recap），只认 detail 会把英文 id 直接甩给用户。 */
const LED_TASK={detail:'活动详情生成',wechat:'微信公众号图文',xhs:'小红书图文',poster:'活动招募海报',recap:'活动回顾'};
const LED_TYPE={subscription:'套餐额度',topup:'充值',consume:'智能消耗',mall_reward:'商城奖励',
  mall_reward_reverse:'奖励冲回',debt_recovery:'欠账抵扣',adjustment:'平台调整',refund:'退款'};
const LED_WD=['周日','周一','周二','周三','周四','周五','周六'];
const LED_STATE={ledger:{key:'ledger',quick:'7d',from:'',to:'',offset:0,items:[],total:0,sum:null,ready:false},
                 usage:{key:'usage',quick:'7d',from:'',to:'',offset:0,items:[],total:0,sum:null,ready:false}};
function ledRangeOf(k){
  const t=localDay(0);                       // localDay 已按 +8 取本地日历日
  if(k==='today')return{from:t,to:t};
  if(k==='7d')return{from:localDay(-6),to:t};
  if(k==='30d')return{from:localDay(-29),to:t};
  if(k==='month')return{from:t.slice(0,8)+'01',to:t};
  return{from:'',to:''};
}
/* 懒初始化默认区间：第一次进 tab 就有界（近 7 天），不会一进页就拉全量。 */
function ledSt(key){
  const st=LED_STATE[key];
  if(!st.ready){const r=ledRangeOf(st.quick);st.from=r.from;st.to=r.to;st.ready=true}
  return st;
}
/* created_at 是 UTC 'YYYY-MM-DD HH:MM:SS'；+8h 后按 UTC 字段读才是本地日期。 */
function ledStamp(ts){
  const d=new Date(String(ts||'').replace(' ','T')+'Z');
  if(isNaN(d))return{key:String(ts||'').slice(0,10),label:'未知日期',time:'—'};
  const l=new Date(d.getTime()+8*3600e3),p=n=>String(n).padStart(2,'0');
  const key=`${l.getUTCFullYear()}-${p(l.getUTCMonth()+1)}-${p(l.getUTCDate())}`;
  const label=key===localDay(0)?'今天':key===localDay(-1)?'昨天':`${l.getUTCMonth()+1}月${l.getUTCDate()}日 · ${LED_WD[l.getUTCDay()]}`;
  return{key,label,time:`${p(l.getUTCHours())}:${p(l.getUTCMinutes())}`};
}
function ledGroups(items,amtOf){
  const out=[],byKey=new Map();
  (items||[]).forEach(x=>{
    const s=ledStamp(x.created_at);
    let g=byKey.get(s.key);
    if(!g){g={key:s.key,label:s.label,rows:[],sum:0};byKey.set(s.key,g);out.push(g)}
    g.rows.push({x,s});g.sum+=Number(amtOf(x)||0);
  });
  return out;
}
function ledRangeText(st){
  if(!st.from&&!st.to)return '全部时间';
  return (st.from||'最早')+' 至 '+(st.to||'至今');
}
function ledBarHtml(key){
  const st=ledSt(key);
  const chips=LED_QUICK.map(q=>`<button type="button" class="led-chip${st.quick===q.k?' on':''}" onclick="ledQuick('${key}','${q.k}')">${q.label}</button>`).join('');
  /* 只有用量页需要清理入口：账本（Credits 流水）是钱，绝不给批量删除按钮。 */
  const clearBtn=key==='usage'
    ?`<button type="button" class="btn ghost sm" onclick="clearUnbilledUsageFailures()">清理未计费失败记录</button>`
    :'';
  return `<div class="led-chips">${chips}</div>`
    +`<div class="led-dates"><input type="date" data-led-from="${key}" value="${st.from}" aria-label="开始日期">`
    +`<span class="led-dates__sep">至</span><input type="date" data-led-to="${key}" value="${st.to}" aria-label="结束日期">`
    +`<button type="button" class="btn ghost sm" onclick="ledApply('${key}')">应用</button>${clearBtn}</div>`;
}
function ledgerSumHtml(st){
  const s=st.sum||{income:0,expense:0,net:0,count:0};
  const netCls=s.net>0?'in':(s.net<0?'out':'');
  return `<span>${ledRangeText(st)}</span><span>共 <b>${s.count}</b> 笔</span>`
    +`<span class="in">入账 +${Number(s.income).toLocaleString('zh-CN')}</span>`
    +`<span class="out">消耗 −${Number(s.expense).toLocaleString('zh-CN')}</span>`
    +`<span>净变化 <b class="${netCls}">${s.net>0?'+':''}${Number(s.net).toLocaleString('zh-CN')}</b></span>`;
}
function usageSumHtml(st){
  const s=st.sum||{calls:0,success:0,failed:0,inputTokens:0,outputTokens:0,credits:0,byTask:[]};
  const rate=s.calls?Math.round(s.success/s.calls*100):0;
  const top=(s.byTask||[]).slice(0,3).map(b=>`<span class="led-pill">${esc(LED_TASK[b.task]||b.task)} <b>×${b.calls}</b>${b.credits?'<i>−'+b.credits+'</i>':''}</span>`).join('');
  return `<span>${ledRangeText(st)}</span>`
    +`<span>调用 <b>${s.calls}</b> 次 · 成功 <b>${s.success}</b>${s.failed?' / 失败 <b class="bad">'+s.failed+'</b>':''} · 成功率 ${rate}%</span>`
    +`<span>tokens ${Number(s.inputTokens+s.outputTokens).toLocaleString('zh-CN')}</span>`
    +`<span class="out">扣费 −${Number(s.credits).toLocaleString('zh-CN')}</span>`
    +(top?`<span class="led-pills">${top}</span>`:'');
}
function ledRowLedger(x,s){
  const amt=Number(x.amount||0),pos=amt>0;
  const typeCn=LED_TYPE[x.type]||x.type||'账务';
  const note=(x.note&&String(x.note)!==typeCn)?String(x.note):'';
  return `<div class="led-row"><span class="led-dot ${pos?'in':'out'}" aria-hidden="true"></span>`
    +`<div class="led-row__main"><div class="led-row__title">${esc(typeCn)}${note?`<span class="led-row__note">${esc(note)}</span>`:''}</div>`
    +`<div class="led-row__sub"><span class="led-row__time">${s.time}</span>${x.source_type?' · '+esc(x.source_type):''}</div></div>`
    +`<div class="led-row__amt ${pos?'in':'out'}">${pos?'+':'−'}${Math.abs(amt).toLocaleString('zh-CN')}</div></div>`;
}
function ledRowUsage(x,s){
  const ok=String(x.status||'')==='success',cred=Number(x.credits_charged||0);
  const tok=Number(x.input_tokens||0).toLocaleString('zh-CN')+' in / '+Number(x.output_tokens||0).toLocaleString('zh-CN')+' out';
  return `<div class="led-row"><span class="led-dot ${ok?'in':'out'}" aria-hidden="true"></span>`
    +`<div class="led-row__main"><div class="led-row__title">${esc(LED_TASK[x.task_type]||x.task_type||'智能调用')}`
    +`<span class="led-badge ${ok?'ok':'bad'}">${ok?'成功':(cred===0?'未计费失败':esc(x.status||'—'))}</span></div>`
    +`<div class="led-row__sub"><span class="led-mono">${esc(x.model||'—')}</span>${x.provider?' · '+esc(x.provider):''} · <span class="led-mono">${tok}</span> · <span class="led-row__time">${s.time}</span></div></div>`
    +`<div class="led-row__amt ${cred>0?'out':'flat'}">${cred>0?'−'+cred:'0'}<i>Credits</i></div></div>`;
}
function ledListHtml(key,st,rowFn,amtOf,emptyText,spent){
  /* spent=true 表示这一列的正数代表「消耗」（AI 用量），配色与符号要和账本相反，
     否则一天的扣费会被画成绿色 +30，看起来像收入。 */
  if(!st.items.length){
    return `<div class="led-empty"><div class="led-empty__ico">◷</div><div>${emptyText}</div>`
      +`<div style="margin-top:10px"><button type="button" class="btn ghost sm" onclick="ledQuick('${key}','all')">看全部时间</button></div></div>`;
  }
  const html=ledGroups(st.items,amtOf).map(g=>{
    const cls=spent?(g.sum>0?'out':'in'):(g.sum>0?'in':'out');
    const sign=spent?'−':(g.sum>0?'+':'−');
    const sum=g.sum?`<span class="led-day__sum ${cls}">${sign}${Math.abs(g.sum).toLocaleString('zh-CN')}</span>`:'';
    return `<div class="led-day"><span class="led-day__label">${g.label}</span>${sum}</div>`
      +g.rows.map(r=>rowFn(r.x,r.s)).join('');
  }).join('');
  const rest=Math.max(0,Number(st.total||0)-st.items.length);
  const foot=rest
    ? `<div class="led-foot"><button type="button" class="led-more" onclick="ledMore('${key}')">加载更多 <i>还有 ${rest} 条</i></button></div>`
    : `<div class="led-foot led-foot--end">已显示全部 ${st.total} 条</div>`;
  return html+foot;
}
function renderLedgerPanel(){
  const st=ledSt('ledger');
  const bar=$('#ledgerBar');if(bar)bar.innerHTML=ledBarHtml('ledger');
  const sum=$('#ledgerSum');if(sum)sum.innerHTML=ledgerSumHtml(st);
  const box=$('#creditLedger');if(!box)return;
  box.innerHTML=ledListHtml('ledger',st,ledRowLedger,x=>x.amount,
    (st.from||st.to)?'这个时间范围内没有 Credits 变动':'还没有 Credits 变动',false);
}
function renderUsagePanel(){
  const st=ledSt('usage');
  const bar=$('#usageBar');if(bar)bar.innerHTML=ledBarHtml('usage');
  const sum=$('#usageSum');if(sum)sum.innerHTML=usageSumHtml(st);
  const box=$('#aiUsage');if(!box)return;
  box.innerHTML=ledListHtml('usage',st,ledRowUsage,x=>x.credits_charged,
    (st.from||st.to)?'这个时间范围内没有 智能调用记录':'还没有 智能调用记录',true);
}
async function loadCreditLedger(mode){
  const st=ledSt('ledger'),box=$('#creditLedger');if(!box)return;
  if(mode!=='more'){st.offset=0;skel('#creditLedger',5)}
  const q=new URLSearchParams();
  if(st.from)q.set('date_from',st.from);
  if(st.to)q.set('date_to',st.to);
  q.set('limit',LED_PAGE);q.set('offset',mode==='more'?st.items.length:0);
  try{
    const r=await api(`/api/club/${CLUB}/credits/ledger?${q.toString()}`)||{};
    st.items=mode==='more'?st.items.concat(r.items||[]):(r.items||[]);
    st.total=Number(r.total||0);st.sum=r.summary||null;st.offset=st.items.length;
    renderLedgerPanel();
  }catch(e){
    if(mode==='more')toast('加载更多失败：'+(e.message||'请重试'));
    else box.innerHTML='<div class="empty">读取失败：'+esc(e.message||'')+'</div>';
  }
}
async function loadUsage(mode){
  const st=ledSt('usage'),box=$('#aiUsage');if(!box)return;
  if(mode!=='more'){st.offset=0;skel('#aiUsage',5)}
  const base=new URLSearchParams();
  if(st.from)base.set('date_from',st.from);
  if(st.to)base.set('date_to',st.to);
  const page=new URLSearchParams(base);
  page.set('limit',LED_PAGE);page.set('offset',mode==='more'?st.items.length:0);
  try{
    /* 明细与汇总各一个请求，都按同一区间；并行拿，别串行等两次往返。 */
    const [list,sum]=await Promise.all([
      api(`/api/club/${CLUB}/ai/usage?${page.toString()}`),
      api(`/api/club/${CLUB}/ai/usage/summary?${base.toString()}`)
    ]);
    st.items=mode==='more'?st.items.concat(list||[]):(list||[]);
    st.sum=sum||null;st.total=Number((sum&&sum.calls)||0);st.offset=st.items.length;
    renderUsagePanel();
  }catch(e){
    if(mode==='more')toast('加载更多失败：'+(e.message||'请重试'));
    else box.innerHTML='<div class="empty">读取失败：'+esc(e.message||'')+'</div>';
  }
}
/* 旧名字保留：设置页 tab 与外部调用都还按 loadAIUsage 走。 */
async function loadAIUsage(){return loadUsage('reset')}

/* 清理「生成失败且未扣费」的用量噪声。这类记录没扣任何 Credits，也不是账单，
   但混在明细里会把成功率算低、让运营误以为扣了费。只清这一类，成功记录、
   pending 记录、已扣费的失败记录一律保留（后端同样按此口径硬闸门）。
   club.js 里的函数要挂到 window，inline onclick 才找得到。 */
window.clearUnbilledUsageFailures=async function(){
  const yes=window.uxConfirm&&await uxConfirm({
    title:'清理未计费失败记录',
    message:'将删除本俱乐部「生成失败且未扣费」的智能调用记录。成功记录、已扣费记录不会受影响，Credits 余额与账单不变。如该失败仍需留作故障排查，请点取消。',
    confirmText:'确认清理',danger:true});
  if(!yes)return;
  try{
    const r=await api(`/api/club/${CLUB}/ai/usage/unbilled-failures`,{method:'DELETE'});
    toast(r&&r.deleted?`已清理 ${r.deleted} 条未计费失败记录`:'没有需要清理的记录');
    loadUsage('reset');
  }catch(e){toast('清理失败：'+(e.message||'请重试'))}
};
function ledRefresh(key){return key==='ledger'?loadCreditLedger('reset'):loadUsage('reset')}
function ledQuick(key,k){
  const st=ledSt(key),r=ledRangeOf(k);
  st.quick=k;st.from=r.from;st.to=r.to;st.offset=0;
  ledRefresh(key);
}
function ledApply(key){
  const st=ledSt(key);
  const f=document.querySelector(`[data-led-from="${key}"]`),t=document.querySelector(`[data-led-to="${key}"]`);
  st.from=(f&&f.value)||'';st.to=(t&&t.value)||'';
  if(st.from&&st.to&&st.from>st.to){toast('开始日期不能晚于结束日期');return}
  st.quick='custom';st.offset=0;
  ledRefresh(key);
}
function ledMore(key){
  if(key==='ledger')loadCreditLedger('more');else loadUsage('more');
}


/* ===== 俱乐部设置（品牌 DIY · 2026-10-09）=================================
   订阅套件的俱乐部在这里自助包装自己的前端：名称 / logo / 口号 / 城市 / 联系人。
   保存后 C 端顾客看到的门面（顶栏名字、logo、首页品牌条）跟着变。
   logo 上传走独立端点（POST …/settings/logo），表单文本走 PUT …/settings；
   保存成功后同步刷新后台侧栏的品牌名，前后台看到的永远同一个名字。 */
let clubLogoUrl='';
/* ── 小程序信息 / 管理助手 / 标准设置（2026-10-26）────────────────────────
   俱乐部租用 ClubOS 会有自己的微信小程序：认证信息、授权状态、体验者、
   管理助手（员工+角色权限）与标准设置（基础/转发/支付）在此集中管理。
   与微信开放平台的真实对接是后续版本，当前是台账模型（字段与微信公众平台
   一一对应）。后端：/api/club/{CLUB}/miniprogram* 与 /api/club/{CLUB}/mp/*。 */
const MP_PERMS={activities:'活动管理',registrations:'报名管理',content:'内容生成',
  members:'会员管理',mall:'商城查看',finance:'数据与结算',settings:'俱乐部设置',miniprogram:'小程序管理'};
let MP_STAFF_TAB='staff';
const MP_STORE_SCOPE_LABEL={this_club:'本俱乐部',all_clubs:'全平台',specified:'指定门店'};
const MP_ACCOUNT_SOURCE_LABEL={manual:'手动创建',import:'批量导入'};
/* 页面权限粒度控件：8 模块 × 无/查看/编辑（无=不单独设置，继承角色）。 */
function mpPermGrid(cur){
  cur=cur||{};
  return '<div class="mp-pg-grid" style="margin-top:6px">'+Object.entries(MP_PERMS).map(([k,label])=>{
    const v=cur[k]||'';
    const seg=(val,txt,active)=>`<label style="padding:6px 12px;cursor:pointer;font-size:13px;${active?'background:var(--brand);color:#fff':'background:var(--bg-soft,#f2f0ea)'}"><input type="radio" class="mp-pg" data-mod="${k}" name="mp-pg-${k}" value="${val}" ${v===val?'checked':''} style="display:none">${txt}</label>`;
    return `<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px dashed var(--line)"><span>${esc(label)}</span><div style="display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden">${seg('','无',v==='')}${seg('view','查看',v==='view')}${seg('edit','编辑',v==='edit')}</div></div>`;
  }).join('')+'</div>';
}
function mpCollectPagePerms(scope){
  const out={};
  document.querySelectorAll(`${scope||''} .mp-pg:checked`).forEach(el=>{ if(el.value) out[el.dataset.mod]=el.value; });
  return out;
}

/* ── 小程序信息 ─────────────────────────────────────────────────────────── */
/* 设计原则（2026-10-26 用户反馈）：老板只填「名字、简介、AppID、主体、类目、
   支付品牌」这类自己的信息；认证状态、版本号、备案、体验者这些是微信侧
   自动同步的，不该让老板手填；头像自动继承俱乐部 Logo；小程序码系统自动
   生成（扫码直达本俱乐部 C 端）。 */
let MP_LOGO='';
async function loadMpInfo(){
  const box=$('#mpInfoBody');if(!box)return;
  box.innerHTML='<div class="empty">正在加载小程序信息…</div>';
  let info;
  try{info=await api(`/api/club/${CLUB}/miniprogram`,{cache:'no-store'})}
  catch(e){loaderError('#mpInfoBody',e,'小程序信息读取失败');return}
  try{const s=await api(`/api/club/${CLUB}/settings`,{cache:'no-store'});MP_LOGO=s.logoUrl||''}catch(e){MP_LOGO=MP_LOGO||''}
  const avatarSrc=info.avatarUrl?info.avatarUrl+(info.avatarUrl.indexOf('?')<0?'?t='+Date.now():''):MP_LOGO;
  const avatar=avatarSrc?`<img src="${esc(avatarSrc)}" alt="头像" style="width:88px;height:88px;border-radius:50%;object-fit:cover">`:`<span style="width:88px;height:88px;border-radius:50%;background:var(--bg-soft,#f2f0ea);display:inline-flex;align-items:center;justify-content:center;font-size:26px">⛰</span>`;
  /* 上线流程 7 步骤卡：编号圆点（有状态=品牌色）+ 说明 + 自动同步 tag */
  const step=(n,title,desc,st,extra)=>`<div style="display:flex;gap:12px;padding:10px 0;border-bottom:1px dashed var(--line)">
    <span style="flex:0 0 26px;height:26px;border-radius:50%;background:${st?'var(--brand)':'var(--bg-soft,#f2f0ea)'};color:${st?'#fff':'inherit'};display:inline-flex;align-items:center;justify-content:center;font-size:13px;font-weight:600">${n}</span>
    <div style="flex:1;min-width:0"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b>${title}</b><span class="tag ${st?'':'orange'}">${st?esc(st):'待微信授权后自动同步'}</span></div><div class="sub" style="margin-top:2px">${desc}${extra?`<br>${esc(extra)}`:''}</div></div></div>`;
  const fillRow=(k,v)=>`<div style="display:flex;gap:14px;padding:9px 0;border-bottom:1px dashed var(--line)"><span class="sub" style="flex:0 0 110px">${k}</span><div style="flex:1;min-width:0">${v||'<span class="sub">未填写</span>'}</div></div>`;
  const cats=(info.serviceCategories||[]).map(c=>`<span class="tag">${esc(c)}</span>`).join(' ');
  const testers=(info.testers||[]);
  const payBrand=info.payBrandName?`${esc(info.payBrandName)}${info.payBrandId?`（ID:${esc(info.payBrandId)}）`:''}`:'';
  box.innerHTML=`
  <div class="card section">
    <div class="panel-title"><h3>认证信息</h3><button class="btn secondary" type="button" onclick="mpInfoEdit()">编辑信息</button></div>
    <div style="display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap;padding:6px 0 14px">
      <div style="text-align:center">${avatar}<div class="sub" style="margin-top:6px">${info.avatarUrl?'小程序头像（已自定义）':'小程序头像 = 俱乐部 Logo'}</div><div class="sub" style="max-width:120px">${info.avatarUrl?'<a href="javascript:void(0)" onclick="mpClearAvatar()">恢复自动</a>':'在「品牌与展示」里更换 Logo 自动同步'}</div>${info.avatarUrl?`<label class="btn ghost sm" style="cursor:pointer;margin-top:4px;display:inline-block">换图<input type="file" accept="image/*" style="display:none" onchange="mpUploadImage('avatar',this)"></label>`:''}</div>
      <div style="text-align:center"><canvas id="mpQrCanvas" style="width:84px;height:84px;border-radius:8px;border:1px solid var(--line)"></canvas><div class="sub" style="margin-top:6px">小程序码 · 系统自动生成</div><div class="sub" style="max-width:120px">扫码直达本俱乐部</div></div>
      <div style="flex:1;min-width:280px">
        ${fillRow('小程序名称',esc(info.appName))}
        ${fillRow('AppID',info.appid?`<code>${esc(info.appid)}</code>`:'')}
        ${fillRow('小程序简介',esc(info.appIntro))}
        ${fillRow('主体类型 / 名称',`${esc(info.subjectType)} ${info.subjectName&&info.subjectType?' / ':''}${esc(info.subjectName)}`)}
        ${fillRow('服务类目',cats)}
        ${fillRow('微信支付品牌',payBrand)}
      </div>
    </div>
  </div>
  <div class="card section">
    <div class="panel-title"><div><h3>上线流程与合规</h3><div class="sub">按微信小程序上线的合规顺序梳理；每步状态由微信授权后自动同步，不需要也不支持手动填写。</div></div><span class="tag ${info.authorized?'':'orange'}">${info.authorized?'已授权':'待微信授权'}</span></div>
    ${step('1','注册与主体认证','完成小程序注册，并通过微信认证（企业主体认证服务费 300 元/年）。',info.realnameStatus)}
    ${step('2','服务类目与行业资质','开通与业务一致的服务类目：旅游服务>旅行社 需上传《旅行社业务经营许可证》。',info.qualificationStatus)}
    ${step('3','小程序备案（ICP）','2023-09 起新注册小程序须完成 ICP 备案，未备案无法提交发布。',info.recordStatus)}
    ${step('4','用户隐私保护指引','在公众平台声明收集的用户信息；须与「标准设置→隐私授权」的开关一致，否则审核驳回。',info.privacyStatus)}
    ${step('5','微信支付绑定','绑定与小程序主体一致的微信支付商户号。',info.payBrandName?'已绑定（'+info.payBrandName+'）':'')}
    ${step('6','版本管理与发布','开发版上传 → 提交审核 → 发布；当前版本号由微信侧自动同步。',info.releaseStatus,info.currentVersion?'当前版本：'+info.currentVersion:'')}
    ${step('7','体验者与灰度','正式发布前可绑定最多 15 名体验者，扫码体验开发版/体验版。',testers.length?testers.length+' 人':'')}
    <div style="padding:10px 0 2px"><div class="sub" style="margin-bottom:6px">体验者（${testers.length}）—— 微信授权后在微信公众平台绑定，自动同步</div>
    ${testers.length?testers.map(t=>`<span class="tag" style="margin:0 6px 6px 0">${esc(t.nickname)}${t.wxid?` · ${esc(t.wxid)}`:''}</span>`).join(''):'<span class="sub">暂无</span>'}</div>
  </div>
  <div id="mpInfoFormBox"></div>`;
  const cv=document.getElementById('mpQrCanvas');
  if(cv&&window.ClubOSQR){try{ClubOSQR.draw(cv,location.origin+'/web?club_id='+CLUB)}catch(e){}}
}
async function mpClearAvatar(){
  try{await api(`/api/club/${CLUB}/miniprogram`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({avatarClear:true})});toast('已恢复为俱乐部 Logo 自动同步');loadMpInfo()}
  catch(e){showAlert({title:'操作失败',message:e.message||'请重试'})}
}
function mpInfoEdit(){
  const box=$('#mpInfoFormBox');if(!box)return;
  Promise.all([
    api(`/api/club/${CLUB}/miniprogram`,{cache:'no-store'}),
    api(`/api/club/${CLUB}/settings`,{cache:'no-store'}).catch(()=>({name:''}))
  ]).then(([info,s])=>{
    const f=(id,label,ph,max)=>`<label style="display:block"><span class="sub">${label}</span><input id="${id}" maxlength="${max||60}" style="width:100%;margin-top:4px" value="${esc(info[id]||'')}" placeholder="${ph||''}"></label>`;
    box.innerHTML=`<div class="card section">
    <div class="panel-title"><div><h3>编辑小程序信息</h3><div class="sub">只需填你自己的信息；认证状态与版本号等由微信授权后自动同步</div></div></div>
    <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 18px">
      ${f('appid','AppID','wxc014caaaa15c0fb2（微信公众平台「设置-基本设置」里复制）',64)}
      ${f('appName','小程序名称',esc(s.name||'远拓户外')+'（默认用俱乐部名，可改）',40)}
      ${f('subjectType','主体类型','企业 / 个人 / 政府媒体或其他',20)}
      ${f('subjectName','主体名称','营业执照上的公司全称',80)}
      ${f('payBrandName','微信支付品牌','远拓旅游',60)}
      ${f('payBrandId','微信支付品牌 ID','18226',32)}
    </div>
    <label style="display:block;margin-top:12px"><span class="sub">小程序简介</span><textarea id="appIntro" maxlength="300" rows="3" style="width:100%;margin-top:4px" placeholder="${esc(s.name||'')} 的活动与装备，都在这里。">${esc(info.appIntro||'')}</textarea></label>
    <label style="display:block;margin-top:12px"><span class="sub">服务类目（每行一个，最多 10 个）</span><textarea id="mpCats" rows="3" style="width:100%;margin-top:4px" placeholder="旅游服务>旅行社">${esc((info.serviceCategories||[]).join('\n'))}</textarea></label>
    <div style="margin-top:14px;display:flex;gap:10px"><button class="btn" type="button" onclick="saveMpInfo()">保存信息</button><button class="btn ghost" type="button" onclick="$('#mpInfoFormBox').innerHTML=''">取消</button></div>
    </div>`;
    // 2026-10-26 用户反馈「编辑信息点不动」：表单渲染在授权状态卡之后、视口之外，
    // 点击后必须滚动到表单，否则看起来毫无反应。
    box.scrollIntoView({behavior:'smooth',block:'start'});
  }).catch(e=>showAlert({title:'读取失败',message:e.message||'请重试'}));
}
async function saveMpInfo(){
  const cats=String($('#mpCats')?.value||'').split('\n').map(x=>x.trim()).filter(Boolean).slice(0,10);
  const payload={appid:$('#appid')?.value||'',appName:$('#appName')?.value||'',appIntro:$('#appIntro')?.value||'',
    subjectType:$('#subjectType')?.value||'',subjectName:$('#subjectName')?.value||'',
    payBrandName:$('#payBrandName')?.value||'',payBrandId:$('#payBrandId')?.value||'',
    serviceCategories:cats};
  try{
    await api(`/api/club/${CLUB}/miniprogram`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    toast('小程序信息已保存');$('#mpInfoFormBox').innerHTML='';loadMpInfo();
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
}
async function mpUploadImage(kind,input){
  const f=input.files&&input.files[0];if(!f)return;
  const fd=new FormData();fd.append('file',f);
  try{
    await api(`/api/club/${CLUB}/miniprogram/image/${kind}`,{method:'POST',body:fd});
    toast(kind==='avatar'?'小程序头像已更新':'小程序码已更新');loadMpInfo();
  }catch(e){showAlert({title:'上传失败',message:e.message||'请重试'})}
}

/* ── 小程序助手：员工管理 + 角色管理 ───────────────────────────────────── */
function mpStaffTab(t){
  MP_STAFF_TAB=t;
  document.querySelectorAll('#mpStaffTabs .set-tab').forEach(b=>b.classList.toggle('active',b.getAttribute('data-mptab')===t));
  const s=$('#mpStaffBody'),r=$('#mpRolesBody');
  if(s)s.style.display=t==='staff'?'':'none';
  if(r)r.style.display=t==='roles'?'':'none';
  t==='staff'?loadMpStaff():loadMpRoles();
}
async function loadMpStaffTab(){mpStaffTab(MP_STAFF_TAB)}
async function loadMpStaff(){
  const box=$('#mpStaffBody');if(!box)return;
  box.innerHTML='<div class="empty">正在加载员工…</div>';
  let d;
  try{d=await api(`/api/club/${CLUB}/mp/staff`,{cache:'no-store'})}
  catch(e){loaderError('#mpStaffBody',e,'员工读取失败');return}
  const storeTxt=x=>{const s=x.storeScope||'this_club';if(s==='specified')return '指定门店（'+(x.storeDetail||[]).length+'）';return MP_STORE_SCOPE_LABEL[s]||s;};
  const pgTxt=x=>{const n=Object.keys(x.pagePerms||{}).length;return n?`<span class="tag">自定义 ${n} 项</span>`:`<span class="sub">继承角色</span>`;};
  const rows=d.staff.map((x,i)=>`<tr><td>${i+1}</td><td><strong>${esc(x.name)}</strong><div class="sub">${[x.wxid,x.phone].filter(Boolean).map(esc).join(' · ')||'—'}</div></td><td>${esc(x.loginAccount||'—')}</td><td>${esc(MP_ACCOUNT_SOURCE_LABEL[x.accountSource]||x.accountSource||'—')}</td><td>${storeTxt(x)}</td><td>${pgTxt(x)}</td><td><div style="display:flex;gap:6px;flex-wrap:wrap"><button class="btn ghost sm" onclick="mpStaffForm(${x.id})">编辑</button><button class="btn ghost sm" onclick="mpStaffDel(${x.id})" style="color:var(--orange)">删除</button></div></td></tr>`).join('');
  box.innerHTML=`
  <div style="margin:4px 0 10px"><button class="btn" type="button" onclick="mpStaffForm()">＋ 添加员工</button><span class="sub" style="margin-left:10px">按角色分配权限；同一员工只绑一个角色</span></div>
  <div class="card"><table class="table"><thead><tr><th>序号</th><th>名称</th><th>登录账号</th><th>账号来源</th><th>门店权限</th><th>页面权限</th><th>操作</th></tr></thead><tbody>${rows||'<tr><td colspan="7" class="empty">还没有员工，点上方「添加员工」</td></tr>'}</tbody></table></div>
  <div id="mpStaffFormBox"></div>`;
}
function mpStaffForm(id){
  const box=$('#mpStaffFormBox');if(!box)return;
  api(`/api/club/${CLUB}/mp/staff`,{cache:'no-store'}).then(d=>{
    const st=(d.staff||[]).find(x=>x.id===id)||{};
    const roleOpts=[{value:'',label:'（暂不分配）'},...(d.roles||[]).map(r=>({value:String(r.id),label:r.name}))];
    const storeOpts=[{value:'',label:'继承角色'},{value:'this_club',label:'本俱乐部'},{value:'all_clubs',label:'全平台'},{value:'specified',label:'指定门店'}];
    const srcOpts=[{value:'manual',label:'手动创建'},{value:'import',label:'批量导入'}];
    const isSpec=st.storeScope==='specified';
    const detailTxt=(st.storeDetail||[]).join('\n');
    box.innerHTML=`<div class="card section"><div class="panel-title"><h3>${id?'编辑员工':'添加员工'}</h3></div>
    <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 18px">
      <label style="display:block"><span class="sub">姓名 *</span><input id="mpStName" maxlength="20" style="width:100%;margin-top:4px" value="${esc(st.name||'')}" placeholder="员工姓名"></label>
      <label style="display:block"><span class="sub">登录账号 *</span><input id="mpStLogin" maxlength="64" style="width:100%;margin-top:4px" value="${esc(st.loginAccount||'')}" placeholder="4-64 位字母/数字/._@-"></label>
      <div style="display:block"><span class="sub">登录密码 ${id?'' :''}</span><div style="display:flex;gap:6px;margin-top:4px"><input id="mpStPwd" type="password" style="flex:1;min-width:0" placeholder="${id?'留空则保持原密码':'设置登录密码'}"><button class="btn ghost sm" type="button" onclick="mpTogglePwd()">显示</button></div></div>
      <div style="display:block"><span class="sub">账号来源</span>${uxChoiceHtml({id:'mpStSrc',value:st.accountSource||'manual',options:srcOpts})}</div>
      <label style="display:block"><span class="sub">手机号</span><input id="mpStPhone" maxlength="11" inputmode="numeric" style="width:100%;margin-top:4px" value="${esc(st.phone||'')}" placeholder="11 位手机号"></label>
      <label style="display:block"><span class="sub">微信号</span><input id="mpStWx" maxlength="64" style="width:100%;margin-top:4px" value="${esc(st.wxid||'')}" placeholder="微信号（用于后续微信侧绑定）"></label>
      <div style="display:block"><span class="sub">用户角色</span>${uxChoiceHtml({id:'mpStRole',value:st.roleId!=null?String(st.roleId):'',options:roleOpts})}</div>
      <div style="display:block"><span class="sub">门店权限</span>${uxChoiceHtml({id:'mpStStore',value:st.storeScope||'',options:storeOpts,attrs:'onchange="mpStaffStoreChange()"',})}</div>
    </div>
    <label style="display:block;margin-top:10px${isSpec?'':';display:none'}" id="mpStDetailWrap"><span class="sub">指定门店（每行一个门店名/ID）</span><textarea id="mpStDetail" rows="3" style="width:100%;margin-top:4px" placeholder="杭州西湖营地&#10;千岛湖营地">${esc(detailTxt)}</textarea></label>
    <div style="margin-top:14px"><div style="display:flex;align-items:center;justify-content:space-between"><b>页面权限</b><span class="sub">留空=继承所绑角色的权限粒度</span></div>${mpPermGrid(st.pagePerms)}</div>
    <div style="margin-top:14px;display:flex;gap:10px"><button class="btn" type="button" onclick="saveMpStaff(${id||0})">${id?'保存':'添加'}</button><button class="btn ghost" type="button" onclick="$('#mpStaffFormBox').innerHTML=''">取消</button></div></div>`;
  }).catch(e=>showAlert({title:'读取失败',message:e.message||'请重试'}));
}
function mpStaffStoreChange(){
  const w=$('#mpStDetailWrap');if(!w)return;
  w.style.display=($('#mpStStore')?.value==='specified')?'':'none';
}
function mpTogglePwd(){
  const e=$('#mpStPwd');if(!e)return;const show=e.type==='password';
  e.type=show?'text':'password';const b=event&&event.target;if(b)b.textContent=show?'隐藏':'显示';
}
async function saveMpStaff(id){
  const roleId=$('#mpStRole')?.value||null;
  const storeScope=$('#mpStStore')?.value||'';
  const storeDetail=(storeScope==='specified')?(String($('#mpStDetail')?.value||'').split('\n').map(x=>x.trim()).filter(Boolean)):[];
  const payload={name:$('#mpStName')?.value||'',wxid:$('#mpStWx')?.value||'',phone:$('#mpStPhone')?.value||'',
    loginAccount:$('#mpStLogin')?.value||'',accountSource:$('#mpStSrc')?.value||'manual',
    storeScope,storeDetail,pagePerms:mpCollectPagePerms('#mpStaffFormBox')};
  const pwd=String($('#mpStPwd')?.value||'');
  if(pwd)payload.password=pwd;
  if(roleId!==null)payload.roleId=roleId?Number(roleId):null;
  try{
    if(id){await api(`/api/club/${CLUB}/mp/staff/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});toast('员工已更新')}
    else{await api(`/api/club/${CLUB}/mp/staff`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});toast('员工已添加')}
    loadMpStaff();
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
}
async function mpStaffDel(id){
  showAlert({title:'删除员工',message:'确定删除该员工的绑定？删除后其权限立即失效。',danger:true,
    actions:[{label:'取消',ghost:true},{label:'删除',danger:true,fn:async close=>{close();
      try{await api(`/api/club/${CLUB}/mp/staff/${id}`,{method:'DELETE'});toast('已删除');loadMpStaff()}
      catch(e){showAlert({title:'删除失败',message:e.message||'请重试'})}}}]}).catch(()=>{});
}
async function loadMpRoles(){
  const box=$('#mpRolesBody');if(!box)return;
  box.innerHTML='<div class="empty">正在加载角色…</div>';
  let d;
  try{d=await api(`/api/club/${CLUB}/mp/roles`,{cache:'no-store'})}
  catch(e){loaderError('#mpRolesBody',e,'角色读取失败');return}
  const cards=d.roles.map(r=>`<div class="card" style="margin-bottom:10px"><div class="panel-title"><div><h3>${esc(r.name)}${r.system?' <span class="tag">系统预置</span>':''}</h3><div class="sub">${esc(r.remark||'')} · ${r.perms.length} 项权限</div></div><div style="display:flex;gap:6px;flex:0 0 auto"><button class="btn ghost sm" onclick="mpRoleForm(${r.id})">编辑权限</button>${r.system?'':`<button class="btn ghost sm" onclick="mpRoleDel(${r.id})" style="color:var(--orange)">删除</button>`}</div></div><div>${r.perms.length?r.perms.map(p=>`<span class="tag" style="margin:0 6px 6px 0">${esc(MP_PERMS[p]||p)}</span>`).join(''):'<span class="sub">未分配任何权限</span>'}</div></div>`).join('');
  box.innerHTML=`<div style="margin:4px 0 10px"><button class="btn" type="button" onclick="mpRoleForm()">＋ 新建角色</button></div>${cards}<div id="mpRoleFormBox"></div>`;
}
function mpRoleForm(id){
  const box=$('#mpRoleFormBox');if(!box)return;
  api(`/api/club/${CLUB}/mp/roles`,{cache:'no-store'}).then(d=>{
    const r=(d.roles||[]).find(x=>x.id===id)||{};
    const storeOpts=[{value:'this_club',label:'本俱乐部'},{value:'all_clubs',label:'全平台'},{value:'specified',label:'指定门店'}];
    box.innerHTML=`<div class="card section"><div class="panel-title"><h3>${id?'编辑角色':'新建角色'}</h3></div>
    <label style="display:block;max-width:320px"><span class="sub">角色名称 *</span><input id="mpRoleName" maxlength="20" style="width:100%;margin-top:4px" value="${esc(r.name||'')}" placeholder="例如：客服 / 财务"></label>
    <div style="margin-top:12px"><div class="sub">权限点（无 / 查看 / 编辑）</div>${mpPermGrid(r.pagePerms)}</div>
    <div style="display:block;margin-top:12px;max-width:320px"><span class="sub">门店权限（该角色模板默认适用门店）</span>${uxChoiceHtml({id:'mpRoleStore',value:r.storeScope||'this_club',options:storeOpts})}</div>
    <label style="display:block;margin-top:10px;max-width:520px"><span class="sub">备注</span><input id="mpRoleRemark" maxlength="100" style="width:100%;margin-top:4px" value="${esc(r.remark||'')}" placeholder="这个角色负责什么"></label>
    <div style="margin-top:14px;display:flex;gap:10px"><button class="btn" type="button" onclick="saveMpRole(${id||0})">${id?'保存':'创建'}</button><button class="btn ghost" type="button" onclick="$('#mpRoleFormBox').innerHTML=''">取消</button></div></div>`;
  }).catch(e=>showAlert({title:'读取失败',message:e.message||'请重试'}));
}
async function saveMpRole(id){
  const pagePerms=mpCollectPagePerms('#mpRoleFormBox');
  const perms=Object.keys(pagePerms);
  const payload={name:$('#mpRoleName')?.value||'',perms,pagePerms,remark:$('#mpRoleRemark')?.value||'',
    storeScope:$('#mpRoleStore')?.value||'this_club'};
  try{
    if(id){await api(`/api/club/${CLUB}/mp/roles/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});toast('角色已更新')}
    else{await api(`/api/club/${CLUB}/mp/roles`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});toast('角色已创建')}
    loadMpRoles();
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
}
async function mpRoleDel(id){
  showAlert({title:'删除角色',message:'确定删除该角色？仍有员工使用时会被拒绝。',danger:true,
    actions:[{label:'取消',ghost:true},{label:'删除',danger:true,fn:async close=>{close();
      try{await api(`/api/club/${CLUB}/mp/roles/${id}`,{method:'DELETE'});toast('已删除');loadMpRoles()}
      catch(e){showAlert({title:'删除失败',message:e.message||'请重试'})}}}]}).catch(()=>{});
}

/* ── 小程序标准设置：基础 / 转发 / 支付 ─────────────────────────────────── */
let MP_SET=null;
const MP_PAY_BIZS={signup:'活动报名',mall:'装备商城',topup:'余额充值',stored:'会员储值'};
const MP_PAY_WAYS={balance:'余额支付',wx:'微信支付'};
async function loadMpSettings(){
  const box=$('#mpSetBody');if(!box)return;
  box.innerHTML='<div class="empty">正在加载设置…</div>';
  let d;
  try{d=await api(`/api/club/${CLUB}/miniprogram/settings`,{cache:'no-store'})}
  catch(e){loaderError('#mpSetBody',e,'设置读取失败');return}
  MP_SET=d.settings||{};
  const b=MP_SET.base||{},sh=MP_SET.share||{},p=MP_SET.pay||{};
  const matrix=p.matrix||{};
  const grpOf=g=>g==='base'?b:g==='share'?sh:p;
  const sw=(g,k,label,hint)=>{const on=!!grpOf(g)[k];return `<label style="display:flex;align-items:center;gap:10px;padding:7px 0;cursor:pointer"><input type="checkbox" data-mpset="${g}.${k}" ${on?'checked':''}><span>${label}${hint?` <span class="sub">（${hint}）</span>`:''}</span></label>`};
  const txt=(g,k,label,ph)=>`<label style="display:block"><span class="sub">${label}</span><input data-mpset="${g}.${k}" style="width:100%;margin-top:4px" maxlength="60" value="${esc(grpOf(g)[k]||'')}" placeholder="${ph||''}"></label>`;
  const matrixRow=biz=>`<tr><td><strong>${MP_PAY_BIZS[biz]}</strong></td>${Object.entries(MP_PAY_WAYS).map(([wk,wn])=>`<td><label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" data-mppay="${biz}.${wk}" ${((matrix[biz]||{})[wk])?'checked':''}> ${wn}</label></td>`).join('')}</tr>`;
  const customs=(p.customWays||[]).map((w,i)=>`<span class="tag" style="margin:0 6px 6px 0">${esc(w)} <a href="javascript:void(0)" onclick="mpDelCustomWay(${i})" style="color:var(--orange);text-decoration:none">×</a></span>`).join('');
  const imgField=(kind,label,hint,w,h,cur)=>`<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:6px 0">
      ${cur?`<img src="${esc(cur)}${cur.indexOf('?')<0?'?t='+Date.now():''}" alt="${label}" style="width:${w}px;height:${h}px;object-fit:cover;border-radius:8px;border:1px solid var(--line)">`:`<span style="width:${w}px;height:${h}px;border-radius:8px;border:1px dashed var(--line);display:inline-flex;align-items:center;justify-content:center" class="sub">未上传</span>`}
      <div><div style="font-weight:600">${label}</div><div class="sub">${hint}</div>
      <div style="display:flex;gap:8px;margin-top:6px"><label class="btn secondary sm" style="cursor:pointer;display:inline-block">${cur?'换图':'上传图片'}<input type="file" accept="image/*" style="display:none" onchange="mpUploadSetImage('${kind}',this)"></label>${cur?`<button class="btn ghost sm" type="button" onclick="mpClearSetImage('${kind}')">移除</button>`:''}</div></div>
    </div>`;
  box.innerHTML=`
  <div class="card section"><div class="panel-title"><div><h3>基础设置</h3><div class="sub">小程序的全局展示与授权行为</div></div></div>
    ${imgField('copyrightIcon','版权图标','建议尺寸 100×48px；显示在小程序底部版权栏',100,48,b.copyrightIcon)}
    <div style="margin-top:10px"><span class="sub">隐私授权样式</span>
      <label style="display:inline-flex;align-items:center;gap:6px;margin-left:12px;cursor:pointer"><input type="radio" name="mpPrivacy" data-mpset="base.privacyStyle" value="wx" ${b.privacyStyle!=='component'?'checked':''}> 微信自带</label>
      <label style="display:inline-flex;align-items:center;gap:6px;margin-left:12px;cursor:pointer"><input type="radio" name="mpPrivacy" data-mpset="base.privacyStyle" value="component" ${b.privacyStyle==='component'?'checked':''}> 小程序组件</label>
      <span class="sub" style="margin-left:10px">⚠ 需与微信公众平台的「用户隐私保护指引」配置一致，否则审核不通过</span>
    </div>
    <div style="margin-top:6px">
      ${sw('base','firstAuthNickname','首次授权头像昵称','新用户在首次使用时弹出授权')}
      ${sw('base','homeFavoriteTip','首页提示收藏小程序')}
      ${sw('base','showClubFavorite','显示俱乐部收藏')}
      ${sw('base','forceGPS','强制获取 GPS','需在微信公众平台开通地理位置接口权限')}
      ${sw('base','showMonthlySales','显示月销量')}
      ${sw('base','recentPurchase','开启最近购买')}
      ${sw('base','phoneRequired','预留电话必填','收集手机号须在隐私保护指引中声明')}
    </div>
    <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 18px;margin-top:8px">
      <div style="display:block"><span class="sub">门店排列模式</span>${uxChoiceHtml({value:b.listMode==='city'?'city':'default',options:[{value:'default',label:'传统排列'},{value:'city',label:'按城市排列'}],attrs:'data-mpset="base.listMode"'})}</div>
      <div style="display:block"><span class="sub">手机号码前缀</span>${uxChoiceHtml({value:b.phonePrefix||'中国大陆',options:['中国大陆','中国香港','中国澳门','中国台湾'].map(x=>({value:x,label:x})),attrs:'data-mpset="base.phonePrefix"'})}</div>
    </div>
    <div style="margin-top:12px"><button class="btn" type="button" onclick="saveMpSetGroup('base')">保存基础设置</button></div>
  </div>
  <div class="card section"><div class="panel-title"><div><h3>转发设置</h3><div class="sub">用户把小程序/活动转发给朋友与朋友圈时的标题与封面</div></div></div>
    ${imgField('friendCover','发给朋友 · 转发封面','建议 500×400（5:4）',100,80,b.friendCover)}
    ${imgField('momentCover','分享到朋友圈 · 封面','建议 500×400',100,80,b.momentCover)}
    <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 18px;margin-top:8px">
      ${txt('share','friendTitle','发给朋友 · 转发标题','让山野·走进日常生活!')}
      ${txt('share','momentTitle','分享到朋友圈 · 标题','')}
    </div>
    <div style="margin-top:8px">${sw('share','groupShare','开启分享拼单')}</div>
    <div style="margin-top:12px"><button class="btn" type="button" onclick="saveMpSetGroup('share')">保存转发设置</button></div>
  </div>
  <div class="card section"><div class="panel-title"><div><h3>支付设置</h3><div class="sub">按业务线勾选可用的支付方式；实际收款仍由 ClubOS 支付层统一处理</div></div></div>
    <div class="card"><table class="table"><thead><tr><th>业务</th>${Object.values(MP_PAY_WAYS).map(w=>`<th>${w}</th>`).join('')}</tr></thead><tbody>${Object.keys(MP_PAY_BIZS).map(matrixRow).join('')}</tbody></table></div>
    <div style="margin-top:8px">${sw('pay','mixedPay','允许混合支付','余额+微信组合支付')}</div>
    <div style="margin-top:10px"><span class="sub">自定义支付方式（仅代下单/统计用）</span>
      <div style="margin-top:6px">${customs||'<span class="sub">暂无</span>'}
      <span style="display:inline-flex;gap:6px;margin-left:6px"><input id="mpCustomWay" maxlength="20" placeholder="支付名称" style="width:140px"><button class="btn ghost sm" type="button" onclick="mpAddCustomWay()">添加方式</button></span></div>
    </div>
    ${txt('pay','wxMchId','微信支付商户 ID','1630023229')}
    <div style="margin-top:12px"><button class="btn" type="button" onclick="saveMpSetGroup('pay')">保存支付设置</button></div>
  </div>`;
}
async function mpUploadSetImage(kind,input){
  const f=input.files&&input.files[0];if(!f)return;
  const fd=new FormData();fd.append('file',f);
  try{await api(`/api/club/${CLUB}/miniprogram/image/${kind}`,{method:'POST',body:fd});toast('图片已上传');loadMpSettings()}
  catch(e){showAlert({title:'上传失败',message:e.message||'请重试'})}
}
async function mpClearSetImage(kind){
  const target={copyrightIcon:['base','copyrightIcon'],friendCover:['share','friendCover'],momentCover:['share','momentCover']}[kind];
  if(!target)return;
  try{await api(`/api/club/${CLUB}/miniprogram/settings`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:{[target[0]]:{[target[1]]:''}}})});toast('已移除，恢复默认');loadMpSettings()}
  catch(e){showAlert({title:'操作失败',message:e.message||'请重试'})}
}
function _mpCollect(group){
  const out={};
  document.querySelectorAll(`#mpSetBody [data-mpset]`).forEach(el=>{
    const [g,k]=el.getAttribute('data-mpset').split('.');
    if(g!==group)return;
    if(el.type==='checkbox')out[k]=el.checked;
    else if(el.type==='radio'){if(el.checked)out[k]=el.value}
    else out[k]=el.value;
  });
  if(group==='pay'){
    const matrix={};MP_SET.pay=MP_SET.pay||{};
    Object.keys(MP_PAY_BIZS).forEach(biz=>{matrix[biz]={};
      Object.keys(MP_PAY_WAYS).forEach(wk=>{
        const el=document.querySelector(`#mpSetBody [data-mppay="${biz}.${wk}"]`);
        const prev=(MP_SET.pay.matrix||{})[biz]||{};
        matrix[biz][wk]=el?el.checked:!!prev[wk];});});
    out.matrix=matrix;
    out.customWays=MP_SET.pay.customWays||[];
  }
  return out;
}
async function saveMpSetGroup(group){
  try{
    const payload={settings:{}};
    payload.settings[group]=_mpCollect(group);
    const r=await api(`/api/club/${CLUB}/miniprogram/settings`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    MP_SET=r.settings;toast('设置已保存');
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
}
function mpAddCustomWay(){
  const v=String($('#mpCustomWay')?.value||'').trim();if(!v)return;
  MP_SET.pay=MP_SET.pay||{};MP_SET.pay.customWays=MP_SET.pay.customWays||[];
  if(MP_SET.pay.customWays.length>=10){showAlert({title:'最多 10 个自定义支付方式'});return}
  MP_SET.pay.customWays.push(v.slice(0,20));saveMpSetGroup('pay');
}
function mpDelCustomWay(i){
  MP_SET.pay=MP_SET.pay||{};MP_SET.pay.customWays=MP_SET.pay.customWays||[];
  MP_SET.pay.customWays.splice(i,1);saveMpSetGroup('pay');
}

let SETTINGS_TAB='brand';
/* 设置中心 tab：领队资源库/业务介绍/智能 Credits/收款账户都收进了设置页，
   每个 tab 面板沿用原 load 函数（DOM id 不变），首次切入时才拉数据。 */
const SET_TAB_LOAD={brand:()=>loadSettings(),
  leaders:async()=>{await loadLeaders()},
  biz:async()=>{await loadBizSection()},
  home:async()=>{await loadHomeBanners()},
  cprofile:async()=>{await loadCProfile()},
  credits:async()=>{await loadCredits();await loadAIUsage()},
  pay:async()=>{await loadPayAccount()},
  mpinfo:async()=>{await loadMpInfo()},
  mpstaff:async()=>{await loadMpStaffTab()},
  mpset:async()=>{await loadMpSettings()},modsw:async()=>{await loadModSw()}};
window.setTab=async name=>{
  if(!SET_TAB_LOAD[name])name='brand';
  SETTINGS_TAB=name;
  document.querySelectorAll('#setTabs .set-tab').forEach(b=>b.classList.toggle('active',b.getAttribute('onclick')===`setTab('${name}')`));
  ['brand','leaders','biz','modsw','home','cprofile','credits','pay','mpinfo','mpstaff','mpset'].forEach(p=>{
    const el=document.getElementById('pane-'+p);
    if(el)el.style.display=p===name?'':'none';
  });
  try{await SET_TAB_LOAD[name]()}catch(e){}
};
async function loadSettings(){
  const box=$('#settingsBody');if(!box)return;
  box.innerHTML='<div class="empty">正在加载设置…</div>';
  let s;
  try{s=await api(`/api/club/${CLUB}/settings`,{cache:'no-store'})}
  catch(e){loaderError('#settingsBody',e,'设置读取失败');return}
  clubLogoUrl=s.logoUrl||'';
  applyClubName(s.name);   // 进设置页也校一次：顶栏永远等于后端当前名字
  box.innerHTML=`
  <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 18px">
    <label style="display:block"><span class="sub">俱乐部名称 *</span><input id="setName" maxlength="40" style="width:100%;margin-top:4px" value="${esc(s.name||'')}" placeholder="例如：远拓户外"></label>
    <label style="display:block"><span class="sub">品牌口号（C 端首页品牌条）</span><input id="setSlogan" maxlength="60" style="width:100%;margin-top:4px" value="${esc(s.slogan||'')}" placeholder="例如：把周末还给山野"></label>
    <label style="display:block"><span class="sub">所在城市</span><input id="setCity" maxlength="30" style="width:100%;margin-top:4px" value="${esc(s.city||'')}" placeholder="例如：成都"></label>
    <label style="display:block"><span class="sub">联系人</span><input id="setContact" maxlength="20" style="width:100%;margin-top:4px" value="${esc(s.contactName||'')}" placeholder="姓名"></label>
    <label style="display:block"><span class="sub">联系电话</span><input id="setPhone" maxlength="20" style="width:100%;margin-top:4px" value="${esc(s.contactPhone||'')}" placeholder="手机号"></label>
    <label style="display:block"><span class="sub">户外运动保险保额（万）· C 端活动详情「保障」行展示，留空=不显示</span><input id="setInsuranceWan" inputmode="decimal" style="width:100%;margin-top:4px" value="${s.insuranceCoverageWan?Number(s.insuranceCoverageWan):''}" placeholder="例如：60"></label>
    <label style="display:block"><span class="sub">腾讯地图 Key · 营地「地图选点」用（lbs.qq.com 免费申请，勾选 WebServiceAPI）</span><input id="setMapKey" maxlength="100" style="width:100%;margin-top:4px" value="${esc(s.mapKey||'')}" placeholder="如：TK5BZ-XXXXXXXX-…"></label>
  </div>
  <div style="margin-top:14px;display:flex;align-items:center;gap:14px;flex-wrap:wrap">
    <span id="setLogoPrev" style="width:56px;height:56px;border-radius:12px;overflow:hidden;background:var(--bg-soft,#f2f0ea);display:inline-flex;align-items:center;justify-content:center;flex:none">${clubLogoImgHtml()}</span>
    <div>
      <div style="font-weight:600">俱乐部 Logo</div>
      <div class="sub">显示在 C 端顶栏与首页品牌条；方图最佳，5MB 内 png/jpg/webp</div>
    </div>
    <label class="btn secondary" style="cursor:pointer;margin-left:auto">选择图片<input type="file" id="setLogoFile" accept="image/*" style="display:none" onchange="uploadClubLogo(this)"></label>
    ${clubLogoUrl?`<button class="btn ghost" type="button" onclick="removeClubLogo()">移除</button>`:''}
  </div>
  <div style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap">
    <button class="btn" type="button" onclick="saveClubSettings()">保存设置</button>
    <span class="sub" id="setSaveTip" style="align-self:center"></span>
  </div>`;
}
function clubLogoImgHtml(){
  return clubLogoUrl?`<img src="${esc(clubLogoUrl)}" alt="logo" style="width:100%;height:100%;object-fit:cover" onerror="this.parentNode.textContent='图'">`:'⚙';
}
async function saveClubSettings(){
  const tip=$('#setSaveTip');if(tip)tip.textContent='';
  const payload={name:$('#setName')?.value||'',slogan:$('#setSlogan')?.value||'',
    city:$('#setCity')?.value||'',contact_name:$('#setContact')?.value||'',contact_phone:$('#setPhone')?.value||''};
  const wan=($('#setInsuranceWan')?.value||'').trim();
  if(wan)payload.insurance_coverage_wan=wan;
  const mk=($('#setMapKey')?.value||'').trim();
  if(mk||s.mapKey)payload.mapKey=mk;
  try{
    await api(`/api/club/${CLUB}/settings`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    // 顶栏 / 浏览器标题当场跟着改：不等刷新，也不留「这里改了后台没同步」的错觉
    applyClubName(payload.name);
    if(tip)tip.textContent='已保存 ✓';
    toast('俱乐部设置已保存，顶栏与 C 端门面已同步');
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
}
async function uploadClubLogo(input){
  const f=input.files&&input.files[0];if(!f)return;
  const fd=new FormData();fd.append('file',f);
  try{
    const r=await api(`/api/club/${CLUB}/settings/logo`,{method:'POST',body:fd});
    clubLogoUrl=r.logoUrl||'';
    // 服务端是固定文件名覆盖写，URL 不变 —— 加时间戳强制绕过浏览器缓存，
    // 否则「上传成功了但预览/海报还是旧图」（2026-10-09 用户反馈）。
    if (clubLogoUrl) clubLogoUrl+=(clubLogoUrl.indexOf('?')<0?'?':'&')+'t='+Date.now();
    const prev=$('#setLogoPrev');if(prev)prev.innerHTML=clubLogoImgHtml();
    toast('Logo 已更新');
    const wrap=input.closest('div[style*="margin-top"]')?.parentElement; // 移除按钮按需出现
    if(wrap&&!wrap.querySelector('[onclick="removeClubLogo()"]')){
      const b=document.createElement('button');b.className='btn ghost';b.type='button';b.textContent='移除';
      b.setAttribute('onclick','removeClubLogo()');wrap.appendChild(b);
    }
  }catch(e){showAlert({title:'Logo 上传失败',message:e.message||'请重试'})}
  input.value='';
}
async function removeClubLogo(){
  try{
    await api(`/api/club/${CLUB}/settings`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({logo_url:''})});
  }catch(e){}
  clubLogoUrl='';
  const prev=$('#setLogoPrev');if(prev)prev.innerHTML=clubLogoImgHtml();
  const btn=document.querySelector('#settingsBody [onclick="removeClubLogo()"]');if(btn)btn.remove();
  toast('已移除 Logo');
}


/* ===== C 端「我的」页文案：联系我们 / 关于俱乐部（2026-10-23）==================
   C 端「我的」里那两页原来把占位文案写死在前端：服务时间 9:00-21:00、官网
   topoutdoor.cn、简介「户外俱乐部一站式服务平台」。顾客看到的是别人的文案。
   现在由老板自己填，右边**实时预览**顾客会看到的样子 —— 改一个字立刻能看到，
   不用保存后再跑去 C 端对一遍。空字段在预览里画成虚线占位，因为后端只下发
   有值的键：空着就是真的不显示，绝不拿默认值编造一个打不通的营业时间。 */
let CP={contact:{},about:{}};
function cpvVal(id){const el=document.getElementById(id);return el?String(el.value||'').trim():''}
function cpvClubName(){return (document.getElementById('clubTopName')?.textContent||'俱乐部').trim()||'俱乐部'}

async function loadCProfile(){
  const box=$('#cProfileBody');if(!box)return;
  box.innerHTML='<div class="empty">正在加载文案…</div>';
  let r;
  try{r=await api(`/api/club/${CLUB}/c-profile`,{cache:'no-store'})}
  catch(e){loaderError('#cProfileBody',e,'文案读取失败');return}
  CP=r.profile||{contact:{},about:{}};
  const c=CP.contact||{},a=CP.about||{},hl=(a.highlights||[]).join('\n');
  const name=cpvClubName(),slogan=(r.slogan||'').trim();
  box.innerHTML=`<div class="cpv">
  <div class="cpv__form">
    <div class="cpv-card">
      <div class="cpv-card__hd"><b>联系我们</b><span class="sub">C 端「我的 → 联系我们」</span></div>
      <div class="cpv-grid">
        <label class="cpv-f"><span>客服电话</span><input id="cpPhone" maxlength="30" value="${esc(c.phone||'')}" placeholder="138 0000 0000"><em>顾客点一下直接拨号</em></label>
        <label class="cpv-f"><span>微信</span><input id="cpWechat" maxlength="40" value="${esc(c.wechat||'')}" placeholder="微信号 / 加群说明"><em>顾客可一键复制</em></label>
        <label class="cpv-f"><span>邮箱</span><input id="cpEmail" maxlength="80" value="${esc(c.email||'')}" placeholder="hello@example.com"><em>格式不对保存会被拦下</em></label>
        <label class="cpv-f"><span>服务时间</span><input id="cpHours" maxlength="60" value="${esc(c.hours||'')}" placeholder="每天 09:00 - 21:00"><em>不填 → C 端不显示这一行</em></label>
        <label class="cpv-f cpv-f--wide"><span>门店 / 办公地址</span><input id="cpAddress" maxlength="80" value="${esc(c.address||'')}" placeholder="成都市武侯区××路 8 号 3 楼"></label>
        <label class="cpv-f cpv-f--wide"><span>底部提示语</span><textarea id="cpNote" maxlength="300" rows="3" placeholder="例如：退改请在订单页发起，我们会在 24 小时内回复。">${esc(c.note||'')}</textarea><em>退改规则、响应时效这类话说在这里</em></label>
      </div>
    </div>
    <div class="cpv-card">
      <div class="cpv-card__hd"><b>关于俱乐部</b><span class="sub">C 端「我的 → 关于${esc(name)}」</span></div>
      <div class="cpv-grid">
        <label class="cpv-f cpv-f--wide"><span>一句话简介</span><input id="caTagline" maxlength="40" value="${esc(a.tagline||'')}" placeholder="${esc(slogan?('留空则用品牌口号：'+slogan):'例如：把周末还给山野')}"></label>
        <label class="cpv-f"><span>成立年份</span><input id="caSince" maxlength="12" value="${esc(a.since||'')}" placeholder="2019"></label>
        <label class="cpv-f"><span>官网</span><input id="caWebsite" maxlength="120" value="${esc(a.website||'')}" placeholder="topoutdoor.cn"><em>自动补全 https://</em></label>
        <label class="cpv-f cpv-f--wide"><span>亮点标签</span><textarea id="caHighlights" maxlength="200" rows="3" placeholder="一行一条，最多 6 条&#10;本地领队 1:8 配比&#10;三年零事故">${esc(hl)}</textarea><em>一行一条，最多 6 条，每条 24 字内</em></label>
        <label class="cpv-f cpv-f--wide"><span>详细介绍</span><textarea id="caIntro" maxlength="1500" rows="5" placeholder="俱乐部怎么来的、谁在带队、擅长什么线路……空一行分段">${esc(a.intro||'')}</textarea><em>空一行 = 新段落</em></label>
      </div>
    </div>
    <div class="cpv-actions">
      <button class="btn" type="button" onclick="saveCProfile()">保存文案</button>
      <button class="btn secondary" type="button" onclick="loadCProfile()">放弃修改</button>
      <span class="sub" id="cpSaveTip"></span>
    </div>
  </div>
  <aside class="cpv__side">
    <div class="cpv-phone">
      <div class="cpv-phone__bar"><i></i><span>顾客看到的样子</span></div>
      <div class="cpv-scr" id="cpvScr"></div>
    </div>
    <div class="sub cpv-hint">虚线 = 还没填，C 端不会显示这一行。</div>
  </aside>
</div>`;
  box.querySelectorAll('input,textarea').forEach(el=>el.addEventListener('input',cpvRender));
  cpvRender();
}

function cpvRender(){
  const scr=document.getElementById('cpvScr');if(!scr)return;
  const name=cpvClubName();
  const phone=cpvVal('cpPhone'),wechat=cpvVal('cpWechat'),email=cpvVal('cpEmail'),
        hours=cpvVal('cpHours'),address=cpvVal('cpAddress'),note=cpvVal('cpNote');
  const tagline=cpvVal('caTagline'),since=cpvVal('caSince'),website=cpvVal('caWebsite'),
        intro=cpvVal('caIntro'),hlText=cpvVal('caHighlights');
  const hl=hlText.split('\n').map(t=>t.trim()).filter(Boolean).slice(0,6);
  const row=(label,v)=>`<div class="cpv-scr__row"><span>${label}</span>${v?`<b>${esc(v)}</b>`:'<b class="cpv-scr__none">未填写</b>'}</div>`;
  const introHtml=intro?intro.split(/\n{2,}/).map(t=>`<p>${esc(t)}</p>`).join(''):'<p class="cpv-scr__none">未填写</p>';
  const acts=[phone?'📞 拨打电话':'',wechat?'💬 复制微信':''].filter(Boolean);
  scr.innerHTML=`
    <div class="cpv-scr__nav"><span>‹ 返回</span><b>联系我们</b><span></span></div>
    <div class="cpv-scr__sec">
      <div class="cpv-scr__hd"><span class="cpv-scr__ava">${esc(name.slice(0,1))}</span><div><b>${esc(name)}</b><em>${esc(tagline||'')}</em></div></div>
      ${acts.length?`<div class="cpv-scr__acts">${acts.map(t=>`<span>${t}</span>`).join('')}</div>`:''}
      <div class="cpv-scr__rows">
        ${row('客服电话',phone)}${row('微信',wechat)}${row('邮箱',email)}${row('服务时间',hours)}${row('地址',address)}
      </div>
      ${note?`<div class="cpv-scr__note">${esc(note)}</div>`:'<div class="cpv-scr__note cpv-scr__none">底部提示语：未填写</div>'}
    </div>
    <div class="cpv-scr__nav cpv-scr__nav--plain"><span></span><b>关于${esc(name)}</b><span></span></div>
    <div class="cpv-scr__sec">
      <div class="cpv-scr__logo">${esc(name)}</div>
      <div class="cpv-scr__tag">${tagline?esc(tagline):'<span class="cpv-scr__none">未填写（留空时用品牌口号）</span>'}</div>
      ${hl.length?`<div class="cpv-scr__chips">${hl.map(t=>`<span>${esc(t)}</span>`).join('')}</div>`:''}
      <div class="cpv-scr__intro">${introHtml}</div>
      <div class="cpv-scr__rows">
        ${row('成立',since?since+' 年':'')}${row('官网',website)}${row('版本','v0.25')}
      </div>
    </div>`;
}

function cpvApply(){
  const c=CP.contact||{},a=CP.about||{};
  const set=(id,v)=>{const el=document.getElementById(id);if(el)el.value=v||''};
  set('cpPhone',c.phone);set('cpWechat',c.wechat);set('cpEmail',c.email);
  set('cpHours',c.hours);set('cpAddress',c.address);set('cpNote',c.note);
  set('caTagline',a.tagline);set('caSince',a.since);set('caWebsite',a.website);
  set('caIntro',a.intro);set('caHighlights',(a.highlights||[]).join('\n'));
  cpvRender();
}

async function saveCProfile(){
  const tip=$('#cpSaveTip');if(tip)tip.textContent='';
  const email=cpvVal('cpEmail');
  if(email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)){
    showAlert({title:'邮箱格式不正确',message:'检查一下邮箱，或者直接留空 —— 留空 C 端就不显示这一行。'});return;
  }
  const digits=cpvVal('cpPhone').replace(/\D/g,'');
  if(cpvVal('cpPhone') && digits.length<5){
    showAlert({title:'客服电话太短',message:'至少 5 位数字，否则顾客照着拨不通。'});return;
  }
  const payload={contact:{phone:cpvVal('cpPhone'),wechat:cpvVal('cpWechat'),email:email,
      hours:cpvVal('cpHours'),address:cpvVal('cpAddress'),note:cpvVal('cpNote')},
    about:{tagline:cpvVal('caTagline'),intro:cpvVal('caIntro'),website:cpvVal('caWebsite'),
      since:cpvVal('caSince'),highlights:cpvVal('caHighlights').split('\n').map(t=>t.trim()).filter(Boolean)}};
  try{
    const r=await api(`/api/club/${CLUB}/c-profile`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    CP=r.profile||payload;
    cpvApply();            // 回填服务端归一化后的值（域名补 https、电话清洗）—— 存进去什么就显示什么
    if(tip)tip.textContent='已保存 ✓';
    toast('我的页文案已保存，C 端刷新即生效');
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
}

/* ===== 业务介绍（C 端「户外能力」页的可配置区 · 2026-09-29）=================
   很多俱乐部除了常规线路，还有团建、研学、企业团、装备租赁等业务。老板在这里开关并
   自定义；打开后出现在 C 端，关闭时 C 端整块不渲染 —— 空壳板块比没有板块更伤信任。
   条目增删改一律「写穿」（改完立刻 PATCH 并在响应上重渲染），不留「忘了点保存」的状态。 */
let bizCfg=null;
async function loadBizSection(){
  const form=$('#bizForm');if(!form)return;
  if(!bizCfg)skel('#bizItems',2);
  let cfg;
  try{cfg=await api(`/api/club/${CLUB}/biz-section`)}
  catch(e){form.innerHTML='<div class="sub">读取失败：'+esc(e.message||'请重试')+'</div>';return}
  bizCfg=Object.assign({enabled:false,title:'业务介绍',intro:'',items:[]},cfg||{});
  if(!Array.isArray(bizCfg.items))bizCfg.items=[];
  renderBiz();
}
function renderBiz(){
  const form=$('#bizForm'),list=$('#bizItems'),tag=$('#bizStateTag');
  const c=bizCfg||{enabled:false,title:'业务介绍',intro:'',items:[]};
  if(tag){tag.className='tag'+(c.enabled?' info':'');tag.textContent=c.enabled?'已开启 · C 端可见':'已关闭 · C 端不显示'}
  if(form)form.innerHTML=
    '<label class="list-row" style="gap:10px;align-items:center;cursor:pointer">'
      +'<input type="checkbox" id="bizEnabled"'+(c.enabled?' checked':'')+' onchange="bizToggle(this.checked)">'
      +'<div class="list-row__main"><div class="list-row__title">在 C 端展示业务介绍</div>'
      +'<div class="list-row__sub">关闭时 C 端整块不渲染，不留空白。</div></div></label>'
    +'<div style="margin-top:12px"><div class="sub">板块标题</div>'
      +'<input id="bizTitle" maxlength="40" style="width:100%;margin-top:6px" value="'+esc(c.title||'')+'" placeholder="业务介绍"></div>'
    +'<div style="margin-top:12px"><div class="sub">一句话简介（可空）</div>'
      +'<textarea id="bizIntro" rows="2" maxlength="200" style="width:100%;margin-top:6px" placeholder="例如：除周末线路外，我们也承接企业团建与亲子研学。">'+esc(c.intro||'')+'</textarea></div>'
    +'<div style="margin-top:12px"><button class="btn" type="button" onclick="saveBizSection()">保存开关与文案</button></div>';
  if(!list)return;
  list.innerHTML=(c.items||[]).map((it,i)=>'<div class="list-row">'
    +'<div class="list-row__main"><div class="list-row__title">'+esc(it.title||'')+'</div>'
    +'<div class="list-row__sub">'+esc(it.desc||'（无说明）')+(it.image?' · 有图':'')+'</div></div>'
    +'<div class="list-row__end">'
    +(it.image?'<img src="'+esc(bizPreviewUrl(it.image))+'" alt="" style="width:44px;height:44px;object-fit:cover;border-radius:8px;flex:0 0 auto;background:#eef0ea" onerror="this.remove()">':'')
    +'<button class="btn ghost" type="button" onclick="editBizItem('+i+')">编辑</button> '
    +'<button class="btn ghost" type="button" onclick="deleteBizItem('+i+')">删除</button></div></div>').join('')
    ||'<div class="empty">还没有条目。加一条「企业团建」试试。</div>';
}
function bizToggle(on){if(!bizCfg)return;bizCfg.enabled=!!on;renderBiz();persistBiz().catch(()=>{})}
/* 只把这一屏的四个字段发上去：后端 normalize 对缺失键回退当前值，
   所以这里逐字段提交不会顺手覆盖掉别的东西。 */
async function persistBiz(){
  if(!bizCfg)return;
  const t=$('#bizTitle'),i=$('#bizIntro');
  if(t)bizCfg.title=t.value;
  if(i)bizCfg.intro=i.value;
  try{
    const cfg=await api(`/api/club/${CLUB}/biz-section`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:bizCfg.enabled,title:bizCfg.title,intro:bizCfg.intro,items:bizCfg.items})});
    bizCfg=Object.assign({enabled:false,title:'业务介绍',intro:'',items:[]},cfg||{});
    if(!Array.isArray(bizCfg.items))bizCfg.items=[];
    renderBiz();
    toast(bizCfg.enabled?'已保存 · C 端可见':'已保存 · C 端不显示');
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'});throw e}
}
async function saveBizSection(){try{await persistBiz()}catch(e){}}
/* 配图预览地址：落库的是 /static/uploads/...（club_biz._image 只认这个形状），
   而后台预览必须走俱乐部域代理 —— 生产环境 /static/uploads/* 被 security_v025
   直接 404，编辑器和列表里拿裸链等于一张破图（与领队头像代理同一个理由）。 */
function bizPreviewUrl(u){
  const s=String(u||'');
  const m=/^\/static\/uploads\/(\d+)\/biz\//.exec(s);
  if(!m)return s;
  return `/api/club/${m[1]}/biz-media${s.slice('/static'.length)}`;
}
/* 业务条目编辑器。uxForm 只有 textarea/select/input 三种字段、没有文件上传，
   所以配图这一栏用 uxDialog 自定义 body：上传按钮 + 即时预览 + 链接输入兜底。
   上传是独立动作（不随提交发）；提交时「链接输入有值就用链接，否则用上传结果」。
   长图不重编码、不设尺寸上限（10MB 封顶），C 端按原图比例完整展示。 */
let BIZ_DLG=null;
function bizItemDialog(mode,i){
  const it=(mode==='edit'&&bizCfg)?((bizCfg.items||[])[Number(i)]||{}):{};
  const state={image:String(it.image||''),uploading:false};
  const ov=uxDialog({
    title:mode==='edit'?'编辑业务':'添加业务',
    desc:'名称与说明展示在 C 端「户外能力」页；配图支持长图，会按原图比例完整展示、不裁不压。',
    body:'<div class="ux-field"><label for="bizDlgTitle">业务名称 <em>*</em></label>'
      +'<input id="bizDlgTitle" type="text" maxlength="40" value="'+esc(it.title||'')+'" placeholder="企业团建"></div>'
      +'<div class="ux-field"><label for="bizDlgDesc">一句话说明</label>'
      +'<textarea id="bizDlgDesc" rows="2" maxlength="200" placeholder="10–50 人的团队定制，含场地、教练与装备">'+esc(it.desc||'')+'</textarea></div>'
      +'<div class="ux-field"><label>配图（可空）</label>'
      +'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">'
      +'<button class="btn ghost" type="button" id="bizDlgPick">上传图片</button>'
      +'<input type="file" id="bizDlgFile" accept="image/png,image/jpeg,image/webp,image/gif" hidden>'
      +'<span class="sub" id="bizDlgState">'+(it.image?'已有一张配图，可更换':'还没有配图 · 支持 png / jpg / webp / gif，长图原样展示')+'</span></div>'
      +'<div id="bizDlgPrev" style="margin-top:8px">'
      +(it.image?'<img src="'+esc(bizPreviewUrl(it.image))+'" alt="" style="max-width:100%;max-height:160px;border-radius:8px;display:block;background:#eef0ea" onerror="this.remove()">':'')
      +'</div>'
      +'<input id="bizDlgUrl" type="text" style="margin-top:8px;width:100%" value="'+(/^https?:/.test(String(it.image||''))?esc(it.image):'')+'" placeholder="或粘贴图片链接（https://…）">'
      +'</div>',
    foot:'<button class="btn ghost" type="button" data-cancel>取消</button><button class="btn" type="button" data-ok>'+(mode==='edit'?'保存':'添加')+'</button>',
    wide:true,
    onClose:()=>{BIZ_DLG=null}
  });
  BIZ_DLG={ov,state,mode,i};
  const q=s=>ov.querySelector(s);
  const paint=url=>{const box=q('#bizDlgPrev');if(box)box.innerHTML=url?'<img src="'+esc(url)+'" alt="" style="max-width:100%;max-height:160px;border-radius:8px;display:block;background:#eef0ea" onerror="this.remove()">':''};
  q('#bizDlgPick').onclick=()=>q('#bizDlgFile').click();
  q('#bizDlgFile').onchange=async e=>{
    const f=e.target.files&&e.target.files[0];e.target.value='';
    if(!f)return;
    if(!/^image\//.test(f.type)){showAlert({title:'只能选图片',message:'请选择 png / jpg / webp / gif 格式的图片。'});return}
    if(f.size>10*1024*1024){showAlert({title:'图片太大',message:'单张上限 10MB。长图可以先压缩一下再传。'});return}
    const st=q('#bizDlgState');
    // 先用本地 blob 顶着，上传完成再换成代理地址；失败停回上传前那张
    const local=URL.createObjectURL(f);
    paint(local);
    if(st)st.textContent='上传中…';
    state.uploading=true;
    try{
      const fd=new FormData();fd.append('file',f);
      const r=await api(`/api/club/${CLUB}/biz-section/image`,{method:'POST',body:fd})||{};
      URL.revokeObjectURL(local);
      state.image=String(r.url||'');
      q('#bizDlgUrl').value='';   // 上传成功就清掉链接输入，别让旧链接把上传结果盖掉
      paint(bizPreviewUrl(state.image));
      if(st)st.textContent='上传成功';
    }catch(err){
      URL.revokeObjectURL(local);
      paint(bizPreviewUrl(state.image));
      if(st)st.textContent='上传失败，可重试';
      showAlert({title:'上传失败',message:err.message||'请重试'});
    }finally{state.uploading=false}
  };
  q('[data-ok]').onclick=async()=>{
    if(state.uploading){toast('图片还在上传，等它传完再保存');return}
    const title=(q('#bizDlgTitle').value||'').trim();
    if(!title){toast('业务名称不能为空');q('#bizDlgTitle').focus();return}
    const desc=(q('#bizDlgDesc').value||'').trim();
    const url=(q('#bizDlgUrl').value||'').trim();
    const item={title:title,desc:desc,image:url||state.image};
    bizCfg.items=bizCfg.items||[];
    if(mode==='edit')bizCfg.items[Number(i)]=item;else bizCfg.items.push(item);
    const btn=q('[data-ok]');btn.disabled=true;btn.textContent='保存中…';
    try{await persistBiz()}catch(e){btn.disabled=false;btn.textContent=mode==='edit'?'保存':'添加';return}
    ov.uxClose();
  };
  q('[data-cancel]').onclick=()=>ov.uxClose();
}
async function addBizItem(){
  if(!bizCfg)return;
  if((bizCfg.items||[]).length>=8)return showAlert({title:'最多 8 条',message:'条目太多会让 C 端这一屏变成广告墙，建议合并同类业务。'});
  bizItemDialog('add');
}
async function editBizItem(i){
  if(!bizCfg)return;
  bizItemDialog('edit',i);
}
async function deleteBizItem(i){
  const it=(bizCfg?.items||[])[Number(i)];if(!it)return;
  const ok=await uxConfirm({title:'删除业务条目',message:'确定删除「'+(it.title||'')+'」？删除后 C 端不再显示。',confirmText:'删除',danger:true});
  if(!ok)return;
  bizCfg.items.splice(Number(i),1);
  await persistBiz();
}

/* ===== 首页运营轮播（C 端首页顶部 banner 自定义 · 2026-10-26）=================
   替换写死的「最近 5 场活动」hero。老板可自定义图片、轮播间隔、跳转目标（活动/商品）。
   未开启或没条目时 C 端整块回落默认活动轮播（不破坏旧行为）。写穿式保存，沿用 biz 范式。 */
let homeCfg=null;
async function loadHomeBanners(){
  const form=$('#homeForm');if(!form)return;
  if(!homeCfg)skel('#homeItems',2);
  let cfg;
  try{cfg=await api(`/api/club/${CLUB}/home-banners`)}
  catch(e){form.innerHTML='<div class="sub">读取失败：'+esc(e.message||'请重试')+'</div>';return}
  homeCfg=Object.assign({enabled:false,interval:5,items:[]},cfg||{});
  if(!Array.isArray(homeCfg.items))homeCfg.items=[];
  renderHomeBanners();
}
function targetLabel(it){
  const t=String(it.targetType||'');
  if(t==='activity')return '跳活动 #'+(it.targetId||'?');
  if(t==='product')return '跳商品 #'+(it.targetId||'?');
  return '不跳转';
}
function renderHomeBanners(){
  const form=$('#homeForm'),list=$('#homeItems'),tag=$('#homeStateTag');
  const c=homeCfg||{enabled:false,interval:5,items:[]};
  if(tag){tag.className='tag'+(c.enabled?' info':'');tag.textContent=c.enabled?'已开启 · C 端首页生效':'已关闭 · C 端回落活动轮播'}
  if(form)form.innerHTML=
    '<label class="list-row" style="gap:10px;align-items:center;cursor:pointer">'
      +'<input type="checkbox" id="homeEnabled"'+(c.enabled?' checked':'')+' onchange="homeToggle(this.checked)">'
      +'<div class="list-row__main"><div class="list-row__title">在 C 端启用自定义首页轮播</div>'
      +'<div class="list-row__sub">关闭时 C 端整块回落默认「最近活动」轮播，不会留白。</div></div></label>'
    +'<div style="margin-top:12px"><div class="sub">自动轮播间隔（秒，2–30）</div>'
      +'<input id="homeInterval" type="number" min="2" max="30" step="1" style="width:120px;margin-top:6px" value="'+esc(String(c.interval||5))+'" placeholder="5"></div>'
    +'<div style="margin-top:12px"><button class="btn" type="button" onclick="saveHomeBanners()">保存开关与间隔</button></div>';
  if(!list)return;
  list.innerHTML=(c.items||[]).map((it,i)=>'<div class="list-row">'
    +'<div class="list-row__main"><div class="list-row__title">'+esc(it.title||'（纯图片）')+'</div>'
    +'<div class="list-row__sub">'
      +(it.badge?'<b>'+esc(it.badge)+'</b> · ':'')
      +targetLabel(it)
      +(it.subtitle?' · '+esc(it.subtitle):'')
      +(it.image?' · 有图':'')
    +'</div></div>'
    +'<div class="list-row__end">'
    +(it.image?'<img src="'+esc(homePreviewUrl(it.image))+'" alt="" style="width:44px;height:44px;object-fit:cover;border-radius:8px;flex:0 0 auto;background:#eef0ea" onerror="this.remove()">':'')
    +'<button class="btn ghost" type="button" onclick="moveHomeItem('+i+',-1)"'+(i===0?' disabled':'')+'>↑</button> '
    +'<button class="btn ghost" type="button" onclick="moveHomeItem('+i+',1)"'+(i===(c.items.length-1)?' disabled':'')+'>↓</button> '
    +'<button class="btn ghost" type="button" onclick="editHomeItem('+i+')">编辑</button> '
    +'<button class="btn ghost" type="button" onclick="deleteHomeItem('+i+')">删除</button></div></div>').join('')
    ||'<div class="empty">还没有轮播图。加一张想推的活动或商品图。</div>';
}
function homeToggle(on){if(!homeCfg)return;homeCfg.enabled=!!on;renderHomeBanners();persistHomeBanners().catch(()=>{})}
async function persistHomeBanners(){
  if(!homeCfg)return;
  const iv=$('#homeInterval');
  if(iv){let v=parseInt(iv.value,10);if(isNaN(v))v=5;v=Math.max(2,Math.min(30,v));homeCfg.interval=v;}
  try{
    const cfg=await api(`/api/club/${CLUB}/home-banners`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:homeCfg.enabled,interval:homeCfg.interval,items:homeCfg.items})});
    homeCfg=Object.assign({enabled:false,interval:5,items:[]},cfg||{});
    if(!Array.isArray(homeCfg.items))homeCfg.items=[];
    renderHomeBanners();
    toast(homeCfg.enabled?'已保存 · C 端首页生效':'已保存 · C 端回落活动轮播');
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'});throw e}
}
async function saveHomeBanners(){try{await persistHomeBanners()}catch(e){}}
function homePreviewUrl(u){
  const s=String(u||'');
  const m=/^\/static\/uploads\/(\d+)\/home\//.exec(s);
  if(!m)return s;
  return `/api/club/${m[1]}/home-media${s.slice('/static'.length)}`;
}
let HOME_DLG=null;
function homeItemDialog(mode,i){
  const it=(mode==='edit'&&homeCfg)?((homeCfg.items||[])[Number(i)]||{}):{};
  const state={image:String(it.image||''),uploading:false};
  const ov=uxDialog({
    title:mode==='edit'?'编辑轮播图':'添加轮播图',
    desc:'上传或粘贴一张图；可填标题/副标题/角标/按钮文案作为图片上的浮层。点图会按你选的「跳转目标」打开指定活动或商品。',
    body:'<div class="ux-field"><label>配图 <em>*</em></label>'
      +'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">'
      +'<button class="btn ghost" type="button" id="homeDlgPick">上传图片</button>'
      +'<input type="file" id="homeDlgFile" accept="image/png,image/jpeg,image/webp,image/gif" hidden>'
      +'<span class="sub" id="homeDlgState">'+(it.image?'已有一张配图，可更换':'还没有配图 · 支持 png / jpg / webp / gif')+'</span></div>'
      +'<div id="homeDlgPrev" style="margin-top:8px">'
      +(it.image?'<img src="'+esc(homePreviewUrl(it.image))+'" alt="" style="max-width:100%;max-height:160px;border-radius:8px;display:block;background:#eef0ea" onerror="this.remove()">':'')
      +'</div>'
      +'<input id="homeDlgUrl" type="text" style="margin-top:8px;width:100%" value="'+(/^https?:/.test(String(it.image||''))?esc(it.image):'')+'" placeholder="或粘贴图片链接（https://…）">'
      +'</div>'
      +'<div class="ux-field"><label for="homeDlgTitle">标题（可空）</label>'
      +'<input id="homeDlgTitle" type="text" maxlength="40" value="'+esc(it.title||'')+'" placeholder="秋日徒步招募"></div>'
      +'<div class="ux-field"><label for="homeDlgSub">副标题（可空）</label>'
      +'<input id="homeDlgSub" type="text" maxlength="80" value="'+esc(it.subtitle||'')+'" placeholder="10月24日 · 蓥华山"></div>'
      +'<div class="ux-field"><label for="homeDlgBadge">角标（可空）</label>'
      +'<input id="homeDlgBadge" type="text" maxlength="20" value="'+esc(it.badge||'')+'" placeholder="HOT / 限时"></div>'
      +'<div class="ux-field"><label for="homeDlgBtn">按钮文案（可空）</label>'
      +'<input id="homeDlgBtn" type="text" maxlength="20" value="'+esc(it.buttonText||'')+'" placeholder="即刻探索"></div>'
      +'<div class="ux-field"><div class="ux-field__t">跳转目标</div>'
      +uxChoiceHtml({id:'homeDlgTarget',value:String(it.targetType||''),options:[{value:'',label:'不跳转（纯展示）'},{value:'activity',label:'跳转到活动'},{value:'product',label:'跳转到商品'}]})
      +'</div>'
      +'<div class="ux-field" id="homeDlgTargetIdWrap" style="'+(String(it.targetType||'')? '':'display:none')+'"><label for="homeDlgTargetId">目标 ID（活动或商品的数字 id）</label>'
      +'<input id="homeDlgTargetId" type="number" min="1" style="width:160px" value="'+(it.targetId?esc(String(it.targetId)):'')+'" placeholder="例如 42"></div>',
    foot:'<button class="btn ghost" type="button" data-cancel>取消</button><button class="btn" type="button" data-ok>'+(mode==='edit'?'保存':'添加')+'</button>',
    wide:true,
    onClose:()=>{HOME_DLG=null}
  });
  HOME_DLG={ov,state,mode,i};
  const q=s=>ov.querySelector(s);
  const paint=url=>{const box=q('#homeDlgPrev');if(box)box.innerHTML=url?'<img src="'+esc(url)+'" alt="" style="max-width:100%;max-height:160px;border-radius:8px;display:block;background:#eef0ea" onerror="this.remove()">':''};
  q('#homeDlgTarget').onchange=()=>{const wrap=q('#homeDlgTargetIdWrap');if(wrap)wrap.style.display=q('#homeDlgTarget').value?'':'none';};
  q('#homeDlgPick').onclick=()=>q('#homeDlgFile').click();
  q('#homeDlgFile').onchange=async e=>{
    const f=e.target.files&&e.target.files[0];e.target.value='';
    if(!f)return;
    if(!/^image\//.test(f.type)){showAlert({title:'只能选图片',message:'请选择 png / jpg / webp / gif 格式的图片。'});return}
    if(f.size>10*1024*1024){showAlert({title:'图片太大',message:'单张上限 10MB。'});return}
    const st=q('#homeDlgState');
    const local=URL.createObjectURL(f);
    paint(local);
    if(st)st.textContent='上传中…';
    state.uploading=true;
    try{
      const fd=new FormData();fd.append('file',f);
      const r=await api(`/api/club/${CLUB}/home-banners/image`,{method:'POST',body:fd})||{};
      URL.revokeObjectURL(local);
      state.image=String(r.url||'');
      q('#homeDlgUrl').value='';
      paint(homePreviewUrl(state.image));
      if(st)st.textContent='上传成功';
    }catch(err){
      URL.revokeObjectURL(local);
      paint(homePreviewUrl(state.image));
      if(st)st.textContent='上传失败，可重试';
      showAlert({title:'上传失败',message:err.message||'请重试'});
    }finally{state.uploading=false}
  };
  q('[data-ok]').onclick=async()=>{
    if(state.uploading){toast('图片还在上传，等它传完再保存');return}
    const title=(q('#homeDlgTitle').value||'').trim();
    const subtitle=(q('#homeDlgSub').value||'').trim();
    const badge=(q('#homeDlgBadge').value||'').trim();
    const buttonText=(q('#homeDlgBtn').value||'').trim();
    const targetType=q('#homeDlgTarget').value||'';
    let targetId=null;
    if(targetType){
      targetId=parseInt((q('#homeDlgTargetId').value||'').trim(),10);
      if(isNaN(targetId)||targetId<=0){toast('请填写有效的目标 ID（正整数）');q('#homeDlgTargetId').focus();return}
    }
    const url=(q('#homeDlgUrl').value||'').trim();
    const image=url||state.image;
    if(!image){toast('请上传或粘贴一张轮播图');q('#homeDlgPick').focus();return}
    const item={image:image,title:title,subtitle:subtitle,badge:badge,buttonText:buttonText,targetType:targetType,targetId:targetId};
    homeCfg.items=homeCfg.items||[];
    if(mode==='edit')homeCfg.items[Number(i)]=item;else homeCfg.items.push(item);
    const btn=q('[data-ok]');btn.disabled=true;btn.textContent='保存中…';
    try{await persistHomeBanners()}catch(e){btn.disabled=false;btn.textContent=mode==='edit'?'保存':'添加';return}
    ov.uxClose();
  };
  q('[data-cancel]').onclick=()=>ov.uxClose();
}
async function addHomeItem(){
  if(!homeCfg)return;
  if((homeCfg.items||[]).length>=8)return showAlert({title:'最多 8 张',message:'轮播图太多会拖慢首屏，建议聚焦主推内容。'});
  homeItemDialog('add');
}
async function editHomeItem(i){
  if(!homeCfg)return;
  homeItemDialog('edit',i);
}
async function deleteHomeItem(i){
  const it=(homeCfg?.items||[])[Number(i)];if(!it)return;
  const ok=await uxConfirm({title:'删除轮播图',message:'确定删除「'+(it.title||'这张轮播图')+'」？删除后 C 端不再显示。',confirmText:'删除',danger:true});
  if(!ok)return;
  homeCfg.items.splice(Number(i),1);
  await persistHomeBanners();
}
async function moveHomeItem(i,dir){
  if(!homeCfg)return;
  const arr=homeCfg.items||[];
  const j=Number(i)+Number(dir);
  if(j<0||j>=arr.length)return;
  const tmp=arr[i];arr[i]=arr[j];arr[j]=tmp;
  await persistHomeBanners();
}
/* 后台 AI 发活动弹窗（createModal #createForm）补「活动城市」下拉：
   与手工新建 / 编辑共用同一份 CLUBOS_CITIES，AI 生成路径（ai-generate 已接收 city）也能落 city。 */
function populateCreateCity(){
  const sel=document.getElementById('createCity');if(!sel)return;
  if(sel.dataset.filled)return;
  const cities=window.CLUBOS_CITIES||[];
  sel.insertAdjacentHTML('beforeend',cities.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join(''));
  sel.dataset.filled='1';
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',populateCreateCity);else populateCreateCity();

/* ═══ 活动分类（2026-11-06）：后台自定义分类 + 活动归属多选 ═══════════════════
   C 端活动页顶部磁贴 = 本端「活动分类」维护的数据（替代旧的标题正则自动分类）：
   增删 / 改名 / 换图 / 排序 / 停用在这里；每场活动归属哪些分类，
   在活动详情的「活动分类」卡里勾选（PATCH .../categories 全量替换）。 */
let ACT_CATS_MGR=[];
async function loadActCats(){
  try{ACT_CATS_MGR=await api(`/api/club/${CLUB}/activity-categories`)||[]}
  catch(e){ACT_CATS_MGR=[];showAlert({title:'加载失败',message:e.message})}
  renderActCats();
}
function renderActCats(){
  const box=document.getElementById('actCatRows');if(!box)return;
  if(!ACT_CATS_MGR.length){box.innerHTML='<div class="empty">还没有分类。点右上「＋ 新增分类」创建第一个，C 端活动页马上就能看到。</div>';return}
  box.innerHTML=ACT_CATS_MGR.map((c,i)=>`
    <div style="display:flex;gap:12px;align-items:center;padding:10px 0;${i?'border-top:1px solid var(--line)':''}">
      <div style="width:64px;height:64px;border-radius:12px;flex:0 0 64px;background:#edf3f1 center/cover no-repeat;${c.imageUrl?`background-image:url('${esc(c.imageUrl)}')`:''};display:flex;align-items:center;justify-content:center;color:#6b8a7b;font-size:11px;text-align:center">${c.imageUrl?'':'无图'}</div>
      <div style="flex:1;min-width:0">
        <div style="font-weight:700">${esc(c.name)} ${c.active?'':'<span class="tag">已停用</span>'}</div>
        <div class="sub">${c.count||0} 场已发布活动挂在此分类 · 排序 ${c.sortOrder}</div>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
        <button class="btn ghost sm" onclick="actCatMove(${c.id},${c.sortOrder-1})" title="上移一位">↑</button>
        <button class="btn ghost sm" onclick="actCatMove(${c.id},${c.sortOrder+1})" title="下移一位">↓</button>
        <button class="btn ghost sm" onclick="actCatRename(${c.id})">改名</button>
        <label class="btn ghost sm" style="margin:0;cursor:pointer">换图<input type="file" accept="image/*" hidden onchange="actCatImage(${c.id},this)"></label>
        <button class="btn ghost sm" onclick="actCatToggle(${c.id})">${c.active?'停用':'启用'}</button>
        <button class="btn ghost sm" style="color:#b73a3a" onclick="actCatDelete(${c.id})">删除</button>
      </div>
    </div>`).join('');
}
async function addActCat(){
  const d=await uxForm({title:'新增活动分类',hint:'分类显示在 C 端活动页顶部的磁贴行；图片可创建后在列表里上传替换。',fields:[{name:'name',label:'分类名称（最长 20 字）',type:'text',required:true,full:true,placeholder:'如：徒步穿越'}],submitText:'创建'});
  if(!d)return;
  try{await api(`/api/club/${CLUB}/activity-categories`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:d.name})});toast('分类已创建');await loadActCats()}
  catch(e){showAlert({title:'创建失败',message:e.message})}
}
async function actCatRename(id){
  const c=ACT_CATS_MGR.find(x=>x.id===id);if(!c)return;
  const d=await uxForm({title:'修改分类名称',fields:[{name:'name',label:'分类名称',type:'text',required:true,full:true,value:c.name}],submitText:'保存'});
  if(!d)return;
  try{await api(`/api/club/${CLUB}/activity-categories/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:d.name})});toast('已改名');await loadActCats()}
  catch(e){showAlert({title:'保存失败',message:e.message})}
}
async function actCatMove(id,sortOrder){
  try{await api(`/api/club/${CLUB}/activity-categories/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({sortOrder})});await loadActCats()}
  catch(e){showAlert({title:'操作失败',message:e.message})}
}
async function actCatToggle(id){
  const c=ACT_CATS_MGR.find(x=>x.id===id);if(!c)return;
  try{await api(`/api/club/${CLUB}/activity-categories/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({active:!c.active})});toast(c.active?'已停用（C 端磁贴不再显示）':'已启用');await loadActCats()}
  catch(e){showAlert({title:'操作失败',message:e.message})}
}
async function actCatDelete(id){
  const c=ACT_CATS_MGR.find(x=>x.id===id);if(!c)return;
  if(!await uxConfirm({title:'删除分类',message:`删除「${c.name}」后，挂在该分类上的活动只是解除归属，活动本身不受影响。确认删除？`,confirmText:'删除',danger:true}))return;
  try{await api(`/api/club/${CLUB}/activity-categories/${id}`,{method:'DELETE'});toast('已删除');await loadActCats()}
  catch(e){showAlert({title:'删除失败',message:e.message})}
}
async function actCatImage(id,input){
  const f=input&&input.files&&input.files[0];if(!f)return;
  try{
    const fd=new FormData();fd.append('file',f);
    await api(`/api/club/${CLUB}/activity-categories/${id}/image`,{method:'POST',body:fd});
    toast('分类图已更新');await loadActCats();
  }catch(e){showAlert({title:'上传失败',message:e.message})}
  input.value='';
}
/* 活动详情注入「活动分类」卡：不改动 openActivity 的巨型模板，包一层在渲染完成后插入。 */
function injectActivityCats(a){
  const pane=document.getElementById('activityDetail');if(!pane)return;
  const old=pane.querySelector('#sec-cats');if(old)old.remove();
  a=a||currentActivity; if(!a)return;
  const sel=new Set((a.categoryIds||[]).map(Number));
  const card=document.createElement('div');
  card.className='card section act-cats';card.id='sec-cats';
  card.innerHTML=`<div class="act-cats__row"><strong class="act-cats__label" title="勾选这场活动归属的分类（可多选）；C 端活动页点分类磁贴即按此过滤。分类在左侧「活动分类」里维护。">活动分类</strong><div class="act-cats__chips">${ACT_CATS_MGR.length?ACT_CATS_MGR.map(c=>`<label class="actcat-pick${sel.has(c.id)?' on':''}"><input type="checkbox" value="${c.id}" ${sel.has(c.id)?'checked':''} onchange="this.closest('label').classList.toggle('on',this.checked)"> ${esc(c.name)}${c.active?'':'<span class="sub">（已停用）</span>'}</label>`).join(''):'<span class="sub">还没有分类，先去左侧「活动分类」创建</span>'}</div><button class="btn secondary act-cats__save" onclick="saveActivityCats(${a.id})">保存分类</button></div>`;
  /* 稳健插入：放在详情里第一个 .card.section 之前（没有则追加末尾），不再依赖可能不存在的 #sec-occ 锚点。 */
  const firstSec=pane.querySelector('.card.section');
  if(firstSec&&firstSec.parentNode) firstSec.parentNode.insertBefore(card,firstSec);
  else pane.appendChild(card);
}
async function saveActivityCats(id){
  const pane=document.getElementById('activityDetail');if(!pane)return;
  const ids=[...pane.querySelectorAll('#sec-cats input[type=checkbox]:checked')].map(x=>Number(x.value));
  try{
    await api(`/api/club/${CLUB}/activities/${id}/categories`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({categoryIds:ids})});
    toast('活动分类已保存');
    currentActivity=await api(`/api/club/${CLUB}/activities/${id}`);
  }catch(e){showAlert({title:'保存失败',message:e.message})}
}
/* 包一层 openActivity：详情渲染完成后把「活动分类」卡插到「团期价格」之前。
   首次进入时若分类还没拉过，顺手拉一次（失败不阻塞详情）。 */
const _openActivityOri=openActivity;
openActivity=async function(id){
  const r=await _openActivityOri(id);
  try{
    let a=currentActivity;
    if(!a||Number(a.id)!==Number(id)){try{a=await api(`/api/club/${CLUB}/activities/${id}`)}catch(e){a=null}}
    if(!ACT_CATS_MGR.length){try{ACT_CATS_MGR=await api(`/api/club/${CLUB}/activity-categories`)||[]}catch(e){ACT_CATS_MGR=[]}}
    injectActivityCats(a);
  }catch(e){}
  return r;
};
/* ===== P2-6（2026-11-14）club 端 inline handler 显式导出护栏 =====
   index.html 里有 30+ 处 onclick="xxx(...)"，其中 19 个此前只靠「顶层 function 隐式全局」
   生效（11 个营销函数是 workflows-club.js 里显式 window.xxx= 的，不受影响）。
   一旦 club.js 为了模块化被包进 IIFE，这些入口会整片静默失效 —— 点了没反应、控制台
   才看得到 ReferenceError。这里统一显式挂 window，并由 tests/club_static_guard.py
   静态巡检：凡被 inline 引用的函数都必须能找到 window.xxx=。 */
Object.assign(window,{
  setOrderRange,inviteTab,infoSetKind,infoSetStatus,mpStaffTab,createActivityManual,
  applyOrderRange,lpToggleForm,addBizItem,addHomeItem,loadCredits,loadPayAccount,
  loadClubAttention,addActCat,recalcMembership,loadPointsPolicy,bulkEditPoints,
  loadInfoCenter,infoSearchInput
});

/* ===== P2-7（2026-11-14）俱乐部营销页信息架构 =====
   一页塞了「券中心三 tab + 消费送券 + 营销活动 + 发放记录 + 邀请奖励」，
   390px 要长滚动才找得到创建入口。这里不改任何后端契约与 DOM id，
   只在运行时把现有 4 个卡收进二级 tab，并给一条 sticky 创建条：
     · 所有列表容器 id 原样保留，loadMembers() 照旧按 id 写入；
     · 创建条按钮直接调已有的 window.* 函数，不新增 inline onclick；
     · 能力地图把 CAMPAIGN_TYPES 里 5 个仅枚举占位的类型灰显标「未接入」。 */
const MKT_CAP=[
  ['首单立减','NEWCOMER',true,'新顾客首单专享'],['限时折扣','TIME_LIMIT',true,'北京时间生效时段'],
  ['满额买赠','BUY_GIFT',true,'成交自动发赠品'],['邀请有礼','INVITE',true,'被邀请好友首单'],
  ['拼团','GROUP',false,'未接入'],['每日签到','CHECKIN',false,'未接入'],['幸运抽奖','LOTTERY',false,'未接入'],
  ['集章兑换','STAMP',false,'未接入'],['礼品卡','GIFT_CARD',false,'未接入']
];
const MKT_GROUPS=[
  {key:'coupon',label:'券中心',create:[['addThresholdCoupon','＋ 满减券',[]],['addDiscountCoupon','＋ 折扣券',[]],['addRedeemCoupon','＋ 兑换券',[]]]},
  {key:'gift',label:'消费送券',create:[['addGiftRule','＋ 送券规则',[]]]},
  {key:'campaign',label:'营销活动',create:[['addPriceCampaign','＋ 立减 / 限时',[]],['addBuyGift','＋ 买赠 / 邀请',[]],['mktCreateSample','一键示例活动',[]]]},
  {key:'invite',label:'邀请奖励',create:[['inviteRewardEdit','编辑邀请设置',['basic']]]}
];
function initMarketingTabs(){
  const view=document.getElementById('members-benefits');
  if(!view||view.dataset.mktReady==='1')return;
  const head=view.querySelector('.panel-title');
  const kids=[...view.children].filter(x=>x!==head);
  if(!head||kids.length<4)return;               /* 结构变了就整体跳过，不误伤页面 */
  view.dataset.mktReady='1';
  const tabs=document.createElement('div');
  tabs.className='mkt-subtabs';tabs.id='mktSubTabs';tabs.setAttribute('role','tablist');
  tabs.setAttribute('aria-label','俱乐部营销二级导航');
  tabs.innerHTML=MKT_GROUPS.map((g,i)=>'<button type="button" role="tab" class="mkt-subtab'+(i?'':' on')
    +'" data-mkt="'+g.key+'" aria-selected="'+(i?'false':'true')+'">'+g.label+'</button>').join('');
  head.after(tabs);
  tabs.querySelectorAll('.mkt-subtab').forEach(b=>b.addEventListener('click',()=>window.mktTab(b.dataset.mkt)));
  MKT_GROUPS.forEach((g,i)=>{
    const pane=document.createElement('div');
    pane.className='mkt-pane';pane.id='mktPane-'+g.key;pane.dataset.mkt=g.key;
    if(i)pane.style.display='none';
    pane.appendChild(kids[i]);
    view.appendChild(pane);
  });
  /* 发放记录是宽表：让它在自己内部横滑，别把整页撑出 390px 的横向溢出。 */
  const gl=view.querySelector('#campaignGrantLog');
  if(gl){gl.classList.add('mkt-scroll');
    const hint=document.createElement('div');hint.className='mkt-scroll-hint';
    hint.textContent='发放记录表格较宽，可左右滑动查看 →';gl.before(hint)}
  const bar=document.createElement('div');
  bar.className='mkt-createbar';bar.id='mktCreateBar';bar.setAttribute('aria-label','创建营销配置');
  view.appendChild(bar);
  const map=document.createElement('div');
  map.className='mkt-capmap';map.id='mktCapMap';
  map.innerHTML='<div class="mkt-capmap__t"><b>活动能力地图</b><span>统一营销引擎 CAMPAIGN_TYPES · 灰显项仅枚举占位，尚未接入报价 / 发放链路</span></div>'
    +'<div class="mkt-capmap__grid">'+MKT_CAP.map(([label,code,on,note])=>
      '<div class="mkt-cap'+(on?'':' is-off')+'"'+(on?'':' aria-disabled="true"')+'><b>'+label+'</b><small>'+code+'</small><span>'+(on?note:'未接入')+'</span></div>').join('')
    +'</div><div class="mkt-capmap__hint">还没配置过？用底部「一键示例活动」先建一个「新客首单立减 30 元」，随时可停用或删除。</div>';
  view.appendChild(map);
  const paintBar=key=>{
    const g=MKT_GROUPS.find(x=>x.key===key)||MKT_GROUPS[0];
    bar.innerHTML='<span class="mkt-createbar__label">'+g.label+'</span><div class="mkt-createbar__acts">'
      +g.create.map(([fn,label],i)=>'<button type="button" class="btn secondary" data-mkt-create="'+i+'">'+label+'</button>').join('')+'</div>';
    bar.querySelectorAll('[data-mkt-create]').forEach(b=>b.addEventListener('click',()=>{
      const item=g.create[Number(b.dataset.mktCreate)];
      const fn=window[item[0]];if(typeof fn==='function')fn(...(item[2]||[]));
    }));
  };
  window.mktTab=function(key){
    const g=MKT_GROUPS.find(x=>x.key===key)||MKT_GROUPS[0];
    view.querySelectorAll('.mkt-pane').forEach(p=>{p.style.display=p.dataset.mkt===g.key?'':'none'});
    tabs.querySelectorAll('.mkt-subtab').forEach(b=>{
      const on=b.dataset.mkt===g.key;b.classList.toggle('on',on);b.setAttribute('aria-selected',on?'true':'false');
    });
    paintBar(g.key);view.dataset.mktTab=g.key;
  };
  window.mktTab(MKT_GROUPS[0].key);
}
/* 空态引导（UX 建议）：第一次进营销页不用先研究表单，一键落一个可停用/可删的示例活动。 */
window.mktCreateSample=async()=>uxTask(async()=>{
  if(!await uxConfirm({title:'创建示例活动',message:'将创建一个「新客首单立减 30 元」的启用活动，仅对本俱乐部新顾客首单生效，C 端结算明细会显示这笔优惠。创建后可随时停用或删除。',confirmText:'创建示例'}))return;
  await api('/api/club/'+CLUB+'/campaigns',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type:'NEWCOMER',title:'示例 · 新客首单立减 30 元',status:'ACTIVE',schedule:{},scope:{},rules:{mode:'fixed',amount:30}})});
  toast('示例活动已创建');if(typeof loadMembers==='function')loadMembers();
});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initMarketingTabs);
else initMarketingTabs();

/* BUG-HUB-011：设置中枢「前往配置」深链 ?view=xxx&tab=yyy。
   SPA 首屏固定落在工作台，过去 6 个跳 index.html 的入口进来后还要管理员自己在
   侧栏找目标模块。DOM 就绪后按 URL 直接激活对应视图；settings 的 tab 交给
   SETTINGS_TAB，避免与 window.onView 里的默认 tab 发生异步竞态。 */
(function(){
  const p=new URLSearchParams(location.search);
  const v=p.get('view'); if(!v)return;
  const apply=()=>{
    const btn=[...document.querySelectorAll('.nav button[data-view]')].find(b=>b.dataset.view===v);
    if(!btn)return;
    const t=p.get('tab');
    if(t&&v==='settings'){try{SETTINGS_TAB=t}catch(e){}}
    btn.click();
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',apply);
  else apply();
})();
