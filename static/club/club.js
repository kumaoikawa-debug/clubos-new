let CLUB=1;let currentActivity=null;
navInit();window.go=v=>{document.querySelector(`.nav button[data-view="${v}"]`)?.click()};
window.onView=async v=>{if(v==='activities')await loadActivities();if(v==='content')await loadContent();if(v==='regs')await loadRegs();if(v==='execution')await loadExecution();if(v==='members')await loadMembers();if(v==='mall')await loadMall();if(v==='analytics')await loadClubBI();if(v==='points')await loadPointsPolicy();if(v==='settings')await setTab(SETTINGS_TAB)}
async function loadDash(){skel('#recentActivities',4);let d=await api(`/api/club/${CLUB}/dashboard`);$('#creditPill').textContent=`AI Credits ${d.credits?.balance||0}`;$('#dashMetrics').innerHTML=[['活动',d.activityCount],['报名',d.registrationCount],['客户',d.memberCount],['商城GMV',money(d.gearGMV)]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div><div class="hint">独立经营数据</div></div>`).join('');$('#analyticsMetrics').innerHTML=[['活动数',d.activityCount],['报名数',d.registrationCount],['商城GMV',money(d.gearGMV)],['商城佣金',money(d.commission)]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div></div>`).join('');let a=await api(`/api/club/${CLUB}/activities`);$('#recentActivities').innerHTML=a.slice(0,5).map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.title)}</div><div class="list-row__sub">${dateText(x.event_date)} · ${esc(x.location||'')}</div></div></div>`).join('')||'<div class="empty">还没有活动</div>';loadClubAttention()}
/* 工作台「待处理事项」：C 端产生新报名 / 新订单后，俱乐部没有主动提醒，只能自己进
   报名管理 / 商城去翻。这里把各视图里「需要人跟进」的数字汇总到工作台一张卡上，
   每项可点击直接跳去处理 —— 等于给俱乐部端补上了测试中暴露的"通知"缺口，
   而不必为此新建一套消息系统。只复用已有的 registrations / mall-orders /
   visibility-requests 三个只读接口，按业务状态聚合，不做任何写操作。 */
async function loadClubAttention(){
  const box=$('#clubAttention'); if(!box)return;
  box.innerHTML='<div class="sub">正在汇总待处理事项…</div>';
  try{
    const [regs,orders,reqs]=await Promise.all([
      api(`/api/club/${CLUB}/registrations`),
      api(`/api/club/${CLUB}/mall/orders`),
      api(`/api/club/${CLUB}/mall/visibility-requests`)
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
    const items=[];
    if(paidRegs)items.push({n:paidRegs,t:'进行中报名',go:'regs',h:'已支付报名待跟进'});
    if(incomplete)items.push({n:incomplete,t:'资料待补',go:'regs',h:'参加人资料待补全'});
    if(ins)items.push({n:ins,t:'保险待处理',go:'regs',h:'保险待出 / 待确认'});
    if(rf)items.push({n:rf,t:'待审退款',go:'regs',h:'退款申请待审核'});
    if(ship)items.push({n:ship,t:'待发货订单',go:'mall',h:'商城订单待发货'});
    if(as)items.push({n:as,t:'商城售后',go:'mall',h:'售后进度跟进'});
    if(vp)items.push({n:vp,t:'待处理上架',go:'mall',h:'上下架申请待总平台'});
    box.innerHTML=items.length
      ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px">`+items.map(i=>`<div style="border:1px solid #d8e3dd;border-radius:14px;padding:14px;cursor:pointer;background:#fff" role="button" tabindex="0" onclick="go('${i.go}')"><div style="font-size:26px;font-weight:800;color:#1f7a4d">${i.n}</div><div style="font-weight:600;margin-top:2px">${esc(i.t)}</div><div style="font-size:12px;color:#6b7f76;margin-top:3px">${esc(i.h)}</div><div style="font-size:12px;color:#1f7a4d;margin-top:8px">去处理 ›</div></div>`).join('')+`</div>`
      : `<div class="notice">暂时没有需要处理的待办。C 端有新报名或新订单时，这里会第一时间提醒你。</div>`;
  }catch(e){box.innerHTML=`<div class="notice warn">待处理事项加载失败：${esc(e.message)}</div>`}
}
/* ===== 活动中心：可搜索的活动栏 + 右侧成品预览（2026-09-26）=====
   过去 #activityRows 里又套了一层 .act-grid：外层网格只给内层一个格子宽，
   于是卡片被压成一列、右边永远空一大片。现在去掉嵌套，改成「左栏活动 + 右栏预览」。 */
let _acts=[],_actOpenId=null;
async function loadActivities(){
  skel('#activityRows',6);
  try{
  const s=$('#activitySearch'); if(s&&!s.__wired){s.__wired=true;s.oninput=()=>renderActivityList()}
  const so=$('#activitySort'); if(so&&!so.__wired){so.__wired=true;so.onchange=()=>renderActivityList()}
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
function activityMini(a,k){
  const draft=a.status==='draft';
  const thumb=a.cover?` style="background-image:url('/api/club/${CLUB}/activities/${a.id}/cover')"`:` cov-${a.id%6+1}`;
  return `<div class="act-mini${_actOpenId===a.id?' active':''}" data-id="${a.id}" role="button" tabindex="0" onclick="openActivity(${a.id})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openActivity(${a.id})}">`
    +`<span class="act-mini__thumb"${thumb}>${a.cover?'':(draft?'✎':'⛰')}<span class="cover-flag ${a.cover?'cover-flag--ok':'cover-flag--missing'}">${a.cover?'封面':'未设封面'}</span></span>`
    +`<span class="act-mini__body"><span class="act-mini__title">${esc(a.title)}</span>`
    +`<span class="act-mini__meta"><span>${esc(dateText(k||a.event_date))}</span><span class="badge ${draft?'badge--soon':'badge--published'} badge--dot">${draft?'草稿':'已发布'}</span><span class="act-mini__price">${money(a.price)}</span></span></span>`
    +`<span class="act-mini__ops"><button type="button" class="act-op" onclick="event.stopPropagation();editActivity(${a.id})">编辑</button><button type="button" class="act-op act-op--danger" onclick="event.stopPropagation();deleteActivity(${a.id})">删除</button></span>`+`</div>`;
}
function renderActivityList(){
  const box=$('#activityRows'); if(!box)return;
  const q=($('#activitySearch')?.value||'').trim().toLowerCase();
  const sort=$('#activitySort')?.value||'date-asc';
  const rows=_acts.map(a=>({a,k:actDateKey(a)})).filter(({a})=>{
    if(!q)return true;
    return [a.title,a.location,a.event_date,a.status,String(a.price)].filter(Boolean).join(' ').toLowerCase().includes(q);
  });
  rows.sort(actSorter(sort));
  const cnt=$('#actRailCount'); if(cnt)cnt.textContent=(q?`${rows.length} / ${_acts.length}`:`${rows.length}`)+' 场活动';
  box.innerHTML=rows.map(({a,k})=>activityMini(a,k)).join('')
    ||`<div class="reg-none">${q?'没有匹配的活动，换个关键词或清空搜索。':'还没有活动，点右上角「＋ AI 发活动」。'}</div>`;
  syncActRail();
}
function goActivity(id){_actOpenId=Number(id);go('activities');openActivity(Number(id))}
function goContentForActivity(id){window.__contentActId=Number(id);go('content')}
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
  const bits=[];
  bits.push(plans.length?('活动方案《'+plans.map(esc).join('》《')+'》'
      +(si.hasPlanText?('（读到 '+Number(si.textLength||0)+' 字）'):'（未读到文字）')):'没有上传方案文件');
  if(photos)bits.push('可用照片 '+photos+' 张');
  if(extras)bits.push('其中单独上传 '+extras+' 张');
  return '<div class="sub" style="margin-top:6px">本场按上传资料生成：'+bits.join(' · ')+'</div>';
}
async function openActivity(id){try{LEADER_CTX='activity';if(LEADER_FORM){dropLeaderDraft();LEADER_FORM=null}_actOpenId=Number(id);syncActRail();let a=await api(`/api/club/${CLUB}/activities/${id}`);currentActivity=a;let conflicts=a.activityMaster?.blocking_conflicts||[];const pane=$('#activityDetail');if(!pane)return;pane.innerHTML=`${detailNavHtml([['sec-cover','封面'],['sec-detail','AI 详情'],['sec-ops','行程与清单'],['sec-leaders','带队领队'],['sec-rules','报名与政策'],['sec-occ','团期价格']])}<div class="panel-title"><div><div class="eyebrow">AI EDITORIAL PREVIEW</div><h2 style="margin:4px 0">${esc(a.title)}</h2><div class="sub">${esc(a.event_date||'')} · ${esc(a.location||'')} · ${money(a.price)} · ${a.occurrences?.length||0} 个团期</div></div><div style="display:flex;gap:8px;flex-wrap:wrap">${a.status==='draft'?`<button class="btn" onclick="publishActivity(${id})">发布活动</button>`:'<span class="tag">已发布</span>'}<button class="btn ghost" onclick="goContentForActivity(${id})">去做宣发内容</button><a class="btn ghost" href="/web?club_id=${CLUB}&activity=${id}" target="_blank" style="text-decoration:none">打开C端</a>${a.status==='published'?`<button class="btn secondary" onclick="shareActivity(${id})">分享活动</button>`:''}${a.detailVersion?.canRegenerate?`<button class="btn secondary" onclick="openRegenerateModal(${id})">重新生成 / 换一版</button>`:''}<button class="btn ghost" onclick="editActivity(${id})">编辑基本信息</button><button class="btn ghost" onclick="deleteActivity(${id})">删除活动</button></div></div>${conflicts.length?`<div class="notice warn">发现真实冲突：${conflicts.map(esc).join('；')}</div>`:''}<div class="notice" style="margin:10px 0 18px">AI 自己决定页面叙事、图片节奏和区块顺序；这里没有模板 A/B/C。${activitySourceLine(a)}</div><div class="card section" id="sec-cover"><div class="panel-title"><div><h3>活动封面</h3><div class="sub">用于 C 端活动列表卡片；建议横图 16:9，C 端仅在活动发布后展示。</div></div></div><div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap"><div style="width:160px;height:90px;border-radius:12px;background:#edf3f1;background-size:cover;background-position:center;display:flex;align-items:center;justify-content:center;color:#6b8a7b;font-size:12px;text-align:center;${a.cover?`background-image:url('/api/club/${CLUB}/activities/${a.id}/cover')`:''}">${a.cover?'':'未设封面'}</div><div style="display:flex;flex-direction:column;gap:8px"><input id="coverFile" type="file" accept="image/*"><button class="btn secondary" onclick="uploadCover(${a.id})">上传 / 替换封面</button></div></div></div><div id="sec-detail">${renderPromo(a.detail,a.activityMaster,{hideButton:true})}</div><div id="sec-ops">${renderInfoStack(a.activityMaster,{gear:a.gearRecommendations,manage:true,skip:infoStackSkip(a.detail,a.activityMaster)})}</div>${renderLeaderCard(a,id)}<div id="sec-rules">${pointsPolicyCard(a)}${refundPolicyCard(a)}${participantPolicyCard(a)}</div><div class="card section" id="sec-occ"><div class="panel-title"><h3>团期 / 价格 / 名额</h3><button class="btn secondary" onclick="quickAddOccurrence(${id})">＋ 添加团期</button></div>${(a.occurrences||[]).map(o=>{const oc=_occRange(o),lbl=String(o.label||'').trim();const multi=!!(oc&&oc.multi);const title=lbl||(oc?(multi?oc.st.md+' ～ '+oc.en.md:oc.st.md):'团期');const sub=lbl?(oc?oc.full+' · ':'')+(multi?'多日行程 · ':'')+money(o.price)+' · 已售 '+o.sold+'/'+o.capacity:(oc?(multi?oc.st.wd+' '+oc.st.t+' 出发 · '+oc.en.wd+' '+oc.en.t+' 返程 · ':oc.st.wd+' '+oc.st.t+' · '):'')+money(o.price)+' · 已售 '+o.sold+'/'+o.capacity;const tag=multi&&!/～|~/.test(lbl)?' <span class="tag">多日</span>':'';/* 团期行此前只有文字、没有任何操作入口：老板改不了时间/价格/名额（用户截图实证）。
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
      ?'《'+_plans.map(esc).join('》《')+'》的文字可能全部做成了图片，AI 读不到这些字。'
      :'系统只识别到照片，没有读到任何文字资料。')
    +'当前的标题 / 日期 / 地点 / 行程因此是通用兜底内容，不是你的方案。'
    +'补一句活动说明（名称 / 日期 / 地点 / 人数）或换一份带文字的方案，点「重新生成 / 换一版」即可；'
    +'也可以直接「编辑基本信息」手工补全。</div>');
  pane.prepend(w);
}
if(window.matchMedia&&matchMedia('(max-width:1180px)').matches)setTimeout(()=>pane.scrollIntoView({behavior:'smooth',block:'start'}),60);syncActRail()}catch(e){showAlert({title:'打开活动失败',message:e.message})}}

function pointsPolicyCard(a){
  const p=a.pointsPolicy||{}; const e=p.effective||{};
  return `<div class="card section" id="pointsPolicyCard">
    <div class="panel-title"><div><h3>活动积分规则</h3><div class="sub">由俱乐部决定这场活动是否参与积分；团期默认继承整场活动规则。</div></div><span class="tag ${p.enabled?'':'orange'}">${p.enabled?'已开启':'不参与积分'}</span></div>
    <label style="display:flex;gap:10px;align-items:center;padding:10px 0"><input id="ppEnabled" type="checkbox" ${p.enabled?'checked':''}> <strong>这场活动参与积分体系</strong></label>
    <div class="grid g2" style="margin-top:4px">
      <div class="notice"><label><input id="ppEarn" type="checkbox" ${p.earnClubPoints?'checked':''}> 报名后产生活动积分</label><div class="sub" style="margin-top:6px">成本由本俱乐部承担；默认按俱乐部积分规则累计。</div></div>
      <div class="notice"><label><input id="ppClub" type="checkbox" ${p.acceptClubPoints?'checked':''}> 允许活动积分抵现金</label><div style="margin-top:8px"><span class="sub">本单最多抵活动金额</span> <input id="ppClubMax" type="number" min="0" max="100" step="1" value="${Number(p.clubPointsMaxDiscountPercent??100)}" style="width:80px"> %</div></div>
      <div class="notice"><strong>C端实际生效</strong><div class="sub" style="margin-top:6px">累计活动积分：${e.earnClubPoints?'是':'否'} · 活动积分抵扣：${e.acceptClubPoints?'是':'否'}</div></div>
    </div>
    <button class="btn secondary" style="margin-top:12px" onclick="savePointsPolicy(${a.id})">保存积分规则</button>
  </div>`
}
async function savePointsPolicy(id){
  /* 装备积分（总平台体系）不在俱乐部后台配置：相关字段既不出现在界面上，
     也不进 payload —— 后端 activity_points_policy.normalize_update 对缺失键
     回退到当前值，平台侧设置不会被这里顺手改掉。 */
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
  return `<div class="card section"><div class="panel-title"><div><h3>报名人 / 参加人规则</h3><div class="sub">付款人和真正参加活动的人分开管理；多人报名按参加人数占名额和计价。</div></div><span class="tag">最多 ${p.maxParticipantsPerOrder||8} 人/单</span></div><div class="grid g2"><div class="notice"><label><input id="pfIncomplete" type="checkbox" ${p.allowIncompleteAtCheckout!==false?'checked':''}> 允许先付款、后补紧急联系人资料</label><div class="sub" style="margin-top:6px">证件类型与证件号码是平台级必填（投保与实名出行要用），付款前顾客必须填好；勾选后仅紧急联系人可到订单中心补充。</div></div><div class="notice"><label><input id="pfInsurance" type="checkbox" ${p.insuranceRequired?'checked':''}> 本活动需要保险资料</label><div class="sub" style="margin-top:6px">俱乐部可在报名名单中维护投保状态与保单号。</div></div><div class="notice"><label><input id="pfReplace" type="checkbox" ${p.allowParticipantReplacement?'checked':''}> 允许用户自助转名额</label><div style="margin-top:8px"><span class="sub">出发前至少</span> <input id="pfCutoff" type="number" min="0" value="${Number(p.replacementCutoffHours||0)}" style="width:80px"> 小时</div></div><div class="notice"><span class="sub">单笔最多报名</span> <input id="pfMax" type="number" min="1" max="50" value="${Number(p.maxParticipantsPerOrder||8)}" style="width:80px"> 人</div></div><button class="btn secondary" style="margin-top:12px" onclick="saveParticipantPolicy(${a.id})">保存参加人规则</button></div>`
}
async function saveParticipantPolicy(id){
  const payload={allowIncompleteAtCheckout:$('#pfIncomplete').checked,insuranceRequired:$('#pfInsurance').checked,allowParticipantReplacement:$('#pfReplace').checked,replacementCutoffHours:Number($('#pfCutoff').value||0),maxParticipantsPerOrder:Number($('#pfMax').value||1)};
  try{await api(`/api/club/${CLUB}/activities/${id}/participant-policy`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});toast('参加人规则已保存');openActivity(id)}catch(e){showAlert({title:'操作失败',message:e.message})}
}

function refundPolicyCard(a){
  const p=a.refundPolicy||{enabled:true,rules:[{minHoursBefore:0,cashRefundPercent:100,label:'活动开始前可退款'}],afterStartCashRefundPercent:0,note:''};
  const rules=(p.rules||[]).slice(0,3); while(rules.length<3) rules.push({minHoursBefore:rules.length===0?72:rules.length===1?24:0,cashRefundPercent:rules.length===0?100:rules.length===1?80:0,label:''});
  return `<div class="card section" id="refundPolicyCard">
    <div class="panel-title"><div><h3>活动退款规则</h3><div class="sub">退款政策属于俱乐部经营规则；系统按用户申请时距离团期开始时间自动计算现金退款比例。</div></div><span class="tag ${p.enabled?'':'orange'}">${p.enabled?'可申请退款':'不可自主退款'}</span></div>
    <label style="display:flex;gap:10px;align-items:center;padding:10px 0"><input id="rpEnabled" type="checkbox" ${p.enabled?'checked':''}> <strong>允许用户按本活动规则申请退款</strong></label>
    <div class="sub" style="margin-bottom:10px">建议按时间从远到近设置。取消费只作用于现金实付；活动积分、装备积分和福利券在退款成功后按 v0.11 规则完整恢复。</div>
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
/* ===== 活动详情：重新生成 / 换一版 ===== */
let _regenDetailBusy=false;
async function openRegenerateModal(id){
  try{
    const r=await showForm({title:'重新生成 / 换一版',desc:'对当前这一版不满意？给个方向，AI 重新做叙事和排版。默认只换叙事/排版、保留全部事实（价格、日期、地点、人数、团期、政策都不会变）。',wide:true,fields:[
      {name:'direction',label:'换版方向 / 要求（可选）',type:'textarea',placeholder:'例如：更年轻化一点、多放实拍图、换个更有冲击力的开场……不填则 AI 自己换一个角度。'},
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
    toast(`已生成第 ${r.versionNo} 版`);
    await openActivity(id);
  }catch(e){ showAlert({title:'换版失败',message:e.message}); }
  finally{ _regenDetailBusy=false; }
}
async function uploadCover(id){let f=$('#coverFile')?.files?.[0];if(!f){toast('请先选择一张图片');return}let fd=new FormData();fd.append('file',f);try{await api(`/api/club/${CLUB}/activities/${id}/cover`,{method:'POST',body:fd});toast('封面已更新');window.channelRenderResetCtx&&channelRenderResetCtx(Number(id));await openActivity(id);await loadActivities()}catch(e){showAlert({title:'操作失败',message:e.message})}}
$('#createForm').onsubmit=async e=>{
  e.preventDefault();let b=$('#genBtn');b.disabled=true;b.textContent='AI正在读资料、选图并排版…';
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
        b.textContent=`AI 正在生成（已等 ${sec} 秒，大资料约 1~5 分钟）…`;
        if(j.status==='done'){done=true;r={activityId:j.activityId,source:j.source||r.source};}
        else if(j.status==='failed'){throw new Error(j.error||'后台生成失败，已退还本次 AI Credits（如未扣）。');}
        else if(Date.now()-started>10*60*1000){throw new Error('等待超时（10 分钟）。任务可能仍在后台运行，稍后刷新活动列表即可看到结果。');}
      }
    }
    modal('createModal',false);
    /* 反馈必须说清「系统读到了什么」。原来只报图片数：上传的 PPT 一个字都没读到时，
       图片数照样是 5，toast 还是绿油油的「已完成」，用户完全无法察觉方案没被读到。
       现在把「几份资料 / 多少字方案 / 几张图」一起报，字数为 0 时再补一条明确提示。 */
    let src=r.source||{},docs=(src.docFiles||[]).length,chars=Number(src.textLength||0),imgs=Number(src.imageCount||0);
    toast(`活动详情已完成 · ${docs} 份资料 · ${chars} 字方案 · ${imgs} 张图片`);
    /* 被跳过的文件必须当场说清：以前不支持的格式是静默丢弃的，老板传了几张 HEIC 手机照、
       系统只字不提，他只会以为「系统读错了文件」。 */
    const _skip=src.skippedFiles||[],_rerr=(src.readErrors||[]).map(x=>(x&&x.name)||'文件');
    if(_skip.length||_rerr.length){
      showAlert({title:'有文件没能读进去',message:
        (_rerr.length?`打不开：${_rerr.join('、')}（可能已损坏或加密）；`:'')
        +(_skip.length?`类型不支持，已跳过：${_skip.join('、')}（支持 PPT / Word / PDF / 文字文件，图片支持 JPG / PNG / WEBP）。`:'')
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
    showAlert({title:'AI 生成失败',message:msg});
  }
  finally{b.disabled=false;b.textContent='AI直接生成详情'}
};
/* 手工新建活动：不走 AI、不消耗 Credits 的第二条创建路径。只落活动事实，生成一份最小可渲染的
   master+detail（facts 区块），让 C 端与俱乐部端立刻能编辑、能加团期、能发布；发布前可随时改。
   走 uxForm 而非新建一个 HTML 弹窗：少一套弹窗层、复用同一套校验/焦点/保持逻辑。 */
async function createActivityManual(){
  const d=await uxForm({title:'手工新建活动',
    subtitle:'不调用 AI、不消耗 Credits。先落活动事实，发布前可随时编辑；之后还能用 AI 重新生成文案。',
    fields:[
      {name:'title',label:'活动名称',type:'text',required:true,full:true,placeholder:'例如：10月24日 蓥华山轻徒步'},
      {name:'eventDate',label:'出发时间',type:'datetime',required:true,defaultTime:'08:00',help:'用于生成首个团期；只填日期时按 08:00 出发。'},
      {name:'location',label:'集合地 / 目的地',type:'text',placeholder:'如：成都 邛崃 兴福寺'},
      {name:'price',label:'活动价格（元）',type:'number',min:0,step:.01,value:0},
      {name:'capacity',label:'总名额（人）',type:'number',min:0,step:1,value:20},
      {name:'summary',label:'一句话介绍（可选）',type:'textarea',full:true,placeholder:'例如：轻装穿越竹林与寺观，适合入门徒步。'}
    ],submitText:'创建草稿'});
  if(!d)return;
  try{
    const r=await api(`/api/club/${CLUB}/activities`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({title:d.title,eventDate:d.eventDate,location:d.location,price:d.price,capacity:d.capacity,summary:d.summary})});
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
/* ===== AI 内容中心：生成 → 直接出成品（公众号图文 / 小红书卡片 / 海报）=====
   过去 genChannel 把接口返回的 JSON 塞进 <pre>，老板拿到的是一堆代码。
   现在交给 static/channel-render.js 渲染成能直接用的成品，并可复制 / 下载。 */
/* ===== 活动选择器（AI 内容中心）=====
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
    $('#channelArea').innerHTML='<div class="empty">先创建一场活动，再让 AI 做内容。</div>';
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
    ['longpic','AI 宣传长图','★ 推荐：让大模型自己排版，直接出 750px 公众号长图，可下载 2 倍图。结构、配色、留白都由模型决定，不再走固定模板'],
    ['wechat','微信公众号图文','AI 重排公众号阅读节奏；生成后可直接预览，并一键复制带格式图文到公众号编辑器'],
    ['xhs','小红书图文','文案为主：独立标题、Hook、正文与话题标签可直接复制走；另出一张 1080×1440 首页海报，并把其余现场照片裁成 3:4 九宫格配图'],
    ['poster','活动招募海报','用活动真实封面 + AI 文案在本地合成 1080×1440 海报，可直接下载'],
    ['recap','活动回顾','只在有真实现场素材时才写；素材不足会明确告诉你缺什么，不编造']
  ];
  $('#channelArea').innerHTML=cards.map(x=>`<div class="card channel-card"><div><strong>${x[1]}</strong><p>${x[2]}</p><div class="sub">当前活动：${esc(a.title)}</div></div><button class="btn secondary" onclick="genChannel('${x[0]}',${a.id})">AI 生成${x[1]}</button></div>`).join('');
  // 已生成内容里补上「这是哪场活动的」——以前只显示 活动 #12，活动一多就认不出来。
  const byId={}; acts.forEach(x=>{byId[x.id]=x});
  $('#contentList').innerHTML=(content||[]).map(x=>{const t=byId[x.activity_id];const cur=Number(x.activity_id)===Number(window.__contentActId);return `<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.title||channelLabel(x.channel))}</div><div class="list-row__sub">${t?`活动：${esc(t.title)}`:`活动 #${x.activity_id}`} · ${esc(x.created_at||'')}</div></div><div class="list-row__end"><span class="tag">${esc(channelLabel(x.channel))}</span>${t&&!cur?`<button class="btn ghost" onclick="actPickChoose(${x.activity_id})">切到这场</button>`:''}<button class="btn secondary" onclick="openContentAsset(${x.id})">查看成品</button><button class="btn ghost" style="color:var(--danger,#b54644)" title="删除这条已生成内容" onclick="deleteContentAsset(${x.id})">删除</button></div></div>`}).join('')
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
async function genChannel(ch,id){
  try{
    // 生成会真花 AI Credits，动手前必须讲清楚。本地渲染成品（复制 / 下载）不再另计费。
    // 长图会多跑一次「图片识别」：每张照片先让视觉模型写一句画面描述、判掉地图截图和
    // 别的活动的照片，否则写作模型是盲选图（实测会把路线地图选成首屏大图）。费用按
    // 公众号同一档计，所以这里把这件事对老板讲明白，而不是事后解释。
    const msg = ch==='longpic'
      ? '系统会读取这场活动的真实资料并调用 AI：先识别每张照片的内容（剔除地图截图与不属于本活动的照片），再由模型自行排版成一张 750px 长图。过程中产生 AI Credits 计费，生成后可直接预览、下载 2 倍图、复制图文。'
      : '系统会读取这场活动的真实资料并调用一次 AI，产生一次 AI Credits 计费。生成后直接给成品，可以复制 / 下载。';
    if(window.uxConfirm&&!await uxConfirm({title:'生成'+channelLabel(ch)+'内容',
      message:msg,confirmText:'开始生成'}))return;
    const d=await api(`/api/club/${CLUB}/activities/${id}/channel/${ch}`,{method:'POST'});
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
  }).join('')||`<div class="empty">${q?'没有匹配的活动':'还没有活动，先让 AI 发一场。'}</div>`;
}
function renderRefundReview(refunds){
  const box=$('#refundReviewList'); if(!box)return;
  box.innerHTML=refunds.map(r=>`<div class="notice" style="margin-bottom:10px"><div class="panel-title"><div><strong>${esc(r.activity_title)}</strong> · ${r.refund_scope==='participant'?`参加人 ${esc(r.participant_name||'')}`:`付款人 ${esc(r.user_name)}`}<div class="sub">${esc(r.occurrence_label||r.start_at||'')} · ${r.refund_scope==='participant'?'单人退款':'整单退款'} · 原现金 ${money(r.original_cash_amount||r.paid_cash)} · 本次退 ${money(r.cash_amount)}${r.refund_percent!=null?`（${Number(r.refund_percent)}%）`:''}${Number(r.retained_cash_amount||0)>0?` · 取消费 ${money(r.retained_cash_amount)}`:''}</div></div><span class="tag ${r.status==='requested'?'orange':''}">${esc(enumCn(r.status,'待处理'))}</span></div><div class="sub">规则：${esc(r.policy_label||'—')} · 原因：${esc(r.reason||'')}${r.refund_scope==='participant'?` · 分摊活动积分 ${r.allocated_club_points||0} / 装备积分 ${r.allocated_gear_points||0}`:''}</div>${r.status==='requested'?`<div style="display:flex;gap:8px;margin-top:10px"><button class="btn secondary" onclick="approveRefund('${r.id}')">同意退款</button><button class="btn ghost" onclick="rejectRefund('${r.id}')">拒绝</button></div>`:''}${r.status==='processing'?`<div class="sub" style="margin-top:8px">已审核通过，等待支付渠道退款。</div><div style="display:flex;gap:8px;margin-top:8px"><button class="btn secondary" onclick="retryRefundProvider('${r.id}')">重试退款到账</button></div>`:''}${r.status==='rejected'&&r.decision_note?`<div class="sub" style="margin-top:8px">审核说明：${esc(r.decision_note)}</div>`:''}</div>`).join('')||'<div class="empty">暂无退款申请</div>'
}
async function showParticipants(regId){
  try{let d=await api(`/api/club/${CLUB}/registrations/${regId}/participants`);let el=document.createElement('div');el.className='modal show';el.innerHTML=`<div class="modal-box"><div class="modal-head"><div><div class="eyebrow">PARTICIPANTS</div><h2 style="margin:4px 0">参加人名单</h2></div><button class="x">×</button></div>${(d.participants||[]).map(p=>`<div class="notice" style="margin-bottom:10px;${p.status==='refunded'?'opacity:.6':''}"><strong>${esc(p.name)}</strong> · ${esc(p.phone||'')} <span class="tag">${p.status==='refunded'?'已退出':`资料${p.form_status==='complete'?'完整':'待补'}`}</span><div class="sub">证件 ${esc(p.id_type||'—')} ${esc(p.id_number||'—')} · 紧急联系人 ${esc(p.emergency_contact_name||'—')} ${esc(p.emergency_contact_phone||'')}</div><div class="sub">保险：${esc(p.insurance_status||'pending')} ${esc(p.insurance_provider||'')} ${esc(p.insurance_policy_no||'')} · 退款 ${esc(p.refund_status||'none')}</div>${p.status!=='refunded'?`<button class="btn ghost" style="margin-top:7px" onclick="clubInsurance(${p.id},${regId})">更新保险</button>`:''}</div>`).join('')||'<div class="empty">暂无参加人</div>'}</div>`;el.querySelector('.x').onclick=()=>el.remove();el.onclick=e=>{if(e.target===el)el.remove()};document.body.appendChild(el)}catch(e){showAlert({title:'操作失败',message:e.message})}
}


async function loadMembers(){skel('#tierList',3);
  try{
  let [a,tiers,benefits,reds]=await Promise.all([
    api(`/api/club/${CLUB}/members`),api(`/api/club/${CLUB}/membership/tiers`),
    api(`/api/club/${CLUB}/benefits`),api(`/api/club/${CLUB}/benefits/redemptions`)
  ]);
  $('#memberRows').innerHTML=a.map(x=>`<tr><td>${esc(x.name)}</td><td><strong>${esc(x.level)}</strong></td><td>${x.activity_count||0}</td><td>${money(x.lifetime_activity_spend||0)}</td><td>${x.club_points_balance}</td><td>${x.gear_points}</td><td>${esc(x.phone||'')}</td></tr>`).join('')||'<tr><td colspan="7" class="empty">暂无会员</td></tr>';
  _tiers=tiers;
  $('#tierList').innerHTML=tiers.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.name)} <span class="tag">Rank ${x.rank}</span>${tierGearChip(x)}</div><div class="list-row__sub">活动消费 ≥ ${money(x.min_activity_spend)} · 活动次数 ≥ ${x.min_activity_count} · ${x.qualification_mode==='ALL'?'同时满足':'任一满足'}</div><div class="list-row__sub">${tierGearNote(x)}</div></div><div class="list-row__end"><button class="btn ghost" onclick="editTier(${x.id})">编辑</button></div></div>`).join('');
  $('#clubBenefitList').innerHTML=benefits.filter(x=>x.owner_type==='CLUB').map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.title)}</div><div class="list-row__sub">${x.points_cost} 活动积分 · ${x.benefit_type==='activity_coupon'?`活动抵扣 ${money(x.cash_value)}`:esc(x.benefit_type)} · 成本由俱乐部承担 · 库存 ${x.stock==null?'不限':x.stock}</div></div></div>`).join('')||'<div class="empty">还没有俱乐部福利</div>';
  $('#clubBenefitRedemptions').innerHTML=reds.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.user_name)}</div> · ${esc(x.title)}<div class="list-row__sub">${x.points_spent} ${x.point_type==='club'?'活动积分':'装备积分'} · ${x.funding_owner==='CLUB'?'俱乐部承担':'平台承担'} · ${esc(x.voucher_code||'')}</div></div></div>`).join('')||'<div class="empty">暂无兑换记录</div>';
  }catch(e){loaderError('#tierList',e,'客户与会员加载失败')}
}
async function recalcMembership(){await api(`/api/club/${CLUB}/membership/recalculate`,{method:'POST'});toast('会员等级已重新计算');loadMembers()}

async function loadMall(){ try{skel('#clubOrders',4);let [p,o,cs,sett,aftersales,tiers]=await Promise.all([api(`/api/club/${CLUB}/mall/products`),api(`/api/club/${CLUB}/mall/orders`),api(`/api/club/${CLUB}/mall/commission-summary`),api(`/api/club/${CLUB}/mall/settlements`),api(`/api/club/${CLUB}/mall/after-sales`),api(`/api/club/${CLUB}/membership/tiers`)]);const md=bestGearDiscount(tiers);$('#clubCommissionSummary').innerHTML=[['待签收',money(cs.pending)],['售后冻结',money(cs.frozen)],['可结算',money(cs.payableNow)],['已结算',money(cs.settled)]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div><div class="hint">${cs.carryDebt&&x[0]==='可结算'?`退款待冲抵 ${money(cs.carryDebt)}`:`售后期 ${cs.policy.afterSalesDays} 天`}</div></div>`).join('');$('#clubProducts').innerHTML=p.map(x=>`<div class="product" data-pid="${x.id}" role="button" tabindex="0" onclick="openClubProduct(${x.id})"><div class="ph">🎒</div><h4>${esc(x.name)}</h4><div class="sub">${esc(x.category||'户外装备')}</div>${memberPriceHtml(x.price,md)}<div class="sub">库存 ${x.stock} · 平台统一履约</div></div>`).join('');$('#clubOrders').innerHTML=o.map(x=>`<div class="list-row" role="button" tabindex="0" onclick="openClubOrder(${x.id})"><div class="list-row__main"><div class="list-row__title">订单 #${x.id}</div> · ${money(x.total)} · 佣金 ${money(x.club_commission)}<div class="list-row__sub">${x.status} · ${x.carrier||'待发货'} ${x.tracking_no||''} · 售后 ${x.after_sales_status||'无'}</div></div></div>`).join('')||'<div class="empty">暂无商城订单</div>';$('#clubAfterSales').innerHTML=aftersales.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.case_type)}</div> · 订单 #${x.order_id} <span class="tag">${esc(x.status)}</span><div class="list-row__sub">平台售后处理 · ${esc(x.reason||'')} · 申请退款 ${money(x.requested_refund_amount||0)}</div></div></div>`).join('')||'<div class="empty">暂无商城售后</div>';$('#clubSettlements').innerHTML=sett.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${money(x.net_amount)}</div> · ${esc(x.payment_ref)} <span class="tag">${x.status}</span><div class="list-row__sub">佣金 ${money(x.gross_amount)} · 退款冲抵 ${money(x.deduction_amount)} · ${esc(x.paid_at||x.created_at)}</div></div></div>`).join('')||'<div class="empty">暂无结算记录</div>';await loadInventory()}
catch(e){loaderError('#clubOrders',e,'商城数据加载失败')}}
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
/* 「积分策略」二级页：把每场活动的「活动积分规则」摊平在一张表里，点一行就地改。
   规则就落在 activities 的列上（见 app.py club_activities），所以列表接口带出来即可，
   不用为每一行再打一次 points-policy。
   ★ 本页只配「俱乐部自己的活动积分」。总平台装备积分（Gear Points）是另一套体系、
   另一个资金池，不在俱乐部后台出现（用户 2026-09-29 明确：这里只配俱乐部活动积分）。
   ★ 点行必须就地弹窗。早先写的是 `onclick="openActivity(id)"`，而详情容器
   `#activityDetail` 在 `#activities` 视图里 —— 站在本页时 `.view.active` 是 `#points`，
   等于把内容写进一个 display:none 的 section，用户点「修改」页面毫无反应
   （原话：「这里还是不能改呀」）。 */
let pointsActivities=[];
async function loadPointsPolicy(){
  skel('#pointsList',6);
  try{
  const list=await api(`/api/club/${CLUB}/activities`);
  pointsActivities=list||[];
  $('#pointsList').innerHTML=pointsActivities.map(a=>{
    const tags=[];
    const enabled=Number(a.points_enabled??1)>0;
    if(enabled&&Number(a.earn_club_points??1)>0)tags.push('报名可获得');
    if(enabled&&Number(a.accept_club_points??1)>0)tags.push('可抵 '+Number(a.club_points_max_discount_percent??100)+'%');
    const tagHtml=tags.length?tags.map(t=>'<span class="tag info">'+esc(t)+'</span>').join(' '):'<span class="tag orange">不参与积分</span>';
    return '<div class="list-row" role="button" tabindex="0" data-points-act="'+a.id+'" onclick="editActivityPoints('+a.id+')" aria-label="修改 '+esc(a.title)+' 的积分规则">'
      +'<div class="list-row__main"><div class="list-row__title">'+esc(a.title)+'</div>'
      +'<div class="list-row__sub">'+esc(a.event_date||'')+' · '+esc(a.location||'')+' · 报名 '+money(a.price||0)+'</div></div>'
      +'<div class="list-row__end">'+tagHtml+'<span class="sub">修改 ›</span></div></div>';
  }).join('')||'<div class="empty">还没有活动</div>';
  }catch(e){loaderError('#pointsList',e,'积分策略加载失败')}
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
  const act=(pointsActivities||[]).find(x=>Number(x.id)===Number(id))||{};
  const f=await uxForm({
    title:'本场活动的积分规则',
    subtitle:act.title?act.title:('活动 #'+id),
    hint:'只配俱乐部自己的活动积分：这场活动参不参与、报名送不送累计、能不能抵现金、最多抵多少。团期默认继承整场活动规则。',
    fields:[
      {name:'enabled',label:'这场活动参与积分体系',type:'select',full:true,value:p.enabled?'1':'0',options:[{value:'1',label:'参与'},{value:'0',label:'不参与'}]},
      {name:'earnClubPoints',label:'报名后累计活动积分',type:'select',value:p.earnClubPoints?'1':'0',options:[{value:'1',label:'累计'},{value:'0',label:'不累计'}],help:'积分成本由本俱乐部承担。'},
      {name:'acceptClubPoints',label:'允许活动积分抵现金',type:'select',value:p.acceptClubPoints?'1':'0',options:[{value:'1',label:'允许抵扣'},{value:'0',label:'不允许抵扣'}]},
      {name:'clubPointsMaxDiscountPercent',label:'最多抵扣活动金额（%）',type:'number',full:true,min:0,max:100,step:1,value:String(Number(p.clubPointsMaxDiscountPercent??100)),help:'本单活动积分最多能抵掉活动金额的百分之多少，0 表示不能抵。'}
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
    toast('活动积分规则已保存');
    await loadPointsPolicy();
  }catch(e){showAlert({title:'保存积分规则失败',message:e.message})}
}
async function loadCredits(){ try{skel('#creditLedger',5);let d=await api(`/api/club/${CLUB}/credits`),sub=d.subscription||{};$('#creditAccount').innerHTML=`<div class="grid g4"><div class="stat-tile"><div class="k">当前可用</div><div class="v">${d.account?.balance||0}</div><div class="hint">AI Credits</div></div><div class="stat-tile"><div class="k">当前套餐</div><div class="v" style="font-size:22px">${esc(sub.plan_name||sub.plan_code||'未开通')}</div><div class="hint">月额度 ${d.account?.monthly_quota||0}</div></div><div class="stat-tile"><div class="k">本月已用</div><div class="v">${d.creditsConsumed||0}</div><div class="hint">成功调用 ${d.successfulCalls||0} 次</div></div><div class="stat-tile"><div class="k">待偿欠账</div><div class="v">${d.unresolvedDebt||0}</div><div class="hint">后续获得 Credits 自动优先抵扣</div></div></div><div class="notice section">大模型由<b>总平台统一接入并结算</b>，俱乐部不需要也无法配置模型或密钥；Credits 只决定计费，不决定模型质量——平台不会因为余额或套餐降低模型、减少图片或截断资料。</div>`;$('#creditTopups').innerHTML=(d.topupPackages||[]).map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.name)}</div><div class="list-row__sub">${money(x.amount)} · 共 ${x.credits} Credits · 单价 ${unitCreditPrice(x.amount,x.credits)}</div><button class="btn secondary" style="margin-top:6px" onclick="buyCredits('${x.code}')">创建充值订单</button></div></div>`).join('')||'<div class="empty">暂无充值包</div>';$('#creditPendingOrders').innerHTML=(d.pendingOrders||[]).map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${x.order_type==='subscription'?'套餐':'充值'} ${x.credits} Credits</div><div class="list-row__sub">${money(x.amount)} · 待付款确认 · ${esc(x.period_key||x.package_code||'')}</div></div></div>`).join('')||'<div class="empty">暂无待付款账单</div>';$('#creditLedger').innerHTML=d.ledger.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${x.amount>0?'+':''}${x.amount}</div> · ${esc(x.note||x.type)}<div class="list-row__sub">${esc(x.type)} · ${x.created_at}</div></div></div>`).join('')}
catch(e){loaderError('#creditLedger',e,'Credits 数据加载失败')}}
async function buyCredits(code){let r=await api(`/api/club/${CLUB}/credits/topups`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({packageCode:code})});toast(`充值订单已创建：${r.credits} Credits / ${money(r.amount)}，等待平台/支付确认`);loadCredits()}
async function startClub(){
  if(clubosCookie('clubos_csrf')){const me=await api('/api/auth/me');if(me.role!=='club')throw new Error('请使用俱乐部账号登录');CLUB=Number(me.clubId);}
  try{const club=await api('/api/public/clubs/'+CLUB);if(club?.name){document.querySelector('.topbar .title').textContent=club.name;document.title=club.name+' · ClubOS 俱乐部经营';}}catch(e){}
  await loadDash();
  loadCreateEngine();
}
/* 创建弹窗明示当前 AI 引擎：真实大模型 or 演示模式。避免老板把「演示模板内容」误判成 AI 没看资料。 */
async function loadCreateEngine(){
  const box=document.getElementById('createEngine');if(!box)return;
  try{
    const m=await api('/api/club/'+CLUB+'/ai-mode');const live=m.mode==='live';
    box.className='ux-create-engine '+(live?'live':'mock');
    box.innerHTML=live
      ?'<b>AI 引擎：真实大模型</b> · 会真实理解你上传的方案与照片，并按内容生成活动详情与宣发素材。'
      :'<b>AI 引擎：演示模式</b> · 当前未接通大模型：系统仅按上传资料<b>抽取真实事实</b>（行程、费用、人数、照片）并套用通用文案，内容可用但表达为示例；由总平台接通模型后即为真实创作。';
  }catch(e){box.style.display='none'}
}
startClub().catch(err=>{showAlert({title:'无法进入俱乐部后台',message:err.message});setTimeout(()=>location.href='/login',1800)});


// v0.13 Activity Execution Center
let currentExecutionOccurrence=null;
const execStateLabel={preparing:'准备中',departed:'已出发',in_progress:'进行中',completed:'已完成'};
async function loadExecution(){skel('#executionOccurrenceList',4);
  try{
  let list=await api(`/api/club/${CLUB}/execution/occurrences`);
  $('#executionOccurrenceList').innerHTML=list.map(o=>`<div class="notice" style="margin-bottom:10px;cursor:pointer" onclick="openExecution(${o.id})"><div class="panel-title"><div><strong>${esc(o.activity_title)}</strong><div class="sub">${esc(o.label||o.start_at)} · ${money(o.price)} · 售出 ${o.sold}/${o.capacity}</div></div><span class="tag ${o.execution_status==='completed'?'':'orange'}">${execStateLabel[o.execution_status||'preparing']||esc(o.execution_status)}</span></div><div class="sub">实名 ${o.named_participants||0} · 资料待补 ${o.incomplete_participants||0} · 保险待处理 ${o.insurance_pending||0} · 已签到 ${o.checked_in||0}</div></div>`).join('')||'<div class="empty">暂无团期</div>';
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
    <div class="card"><div class="panel-title"><h3>领队</h3><button class="btn secondary" onclick="addExecutionLeader(${oid})">＋ 领队</button></div>${leaders.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.name)}</div> · ${esc(x.role)}<div class="list-row__sub">${esc(x.phone||'')}</div></div></div>`).join('')||'<div class="empty">尚未添加领队</div>'}</div>
  </div>
  <div class="grid g2 section"><div class="card"><div class="panel-title"><h3>车辆 / 分组</h3><button class="btn secondary" onclick="addExecutionGroup(${oid})">＋ 分组</button></div>${groups.map(g=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(g.name)}</div> <span class="tag">${esc(g.group_type)}</span><div class="list-row__sub">${g.assigned_count||0}/${g.capacity||'不限'} · ${esc(g.leader_name||'')}</div></div></div>`).join('')||'<div class="empty">尚未分组</div>'}</div>
  <div class="card"><div class="panel-title"><h3>活动通知</h3><button class="btn secondary" onclick="addExecutionNotice(${oid})">＋ 通知</button></div>${notices.map(n=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(n.title)}</div> <span class="tag ${n.status==='sent'?'':'orange'}">${esc(n.status)}</span><div class="list-row__sub">${esc(n.content)}</div>${n.status!=='sent'?`<button class="btn ghost" style="margin-top:6px" onclick="sendExecutionNotice(${oid},${n.id})">标记发送</button>`:''}</div></div>`).join('')||'<div class="empty">尚未创建通知</div>'}</div></div>
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
   biCard('本期 AI 消耗',`${ai.creditsConsumedInWindow} Credits`,`成功 ${ai.successfulCalls} 次 · 失败 ${ai.failedCalls} 次`),
   biCard('当前 AI 余额',`${ai.currentBalance} Credits`,`待偿欠账 ${ai.unresolvedDebt}`)
  ].join('');
  $('#clubBITrend').innerHTML=biTrend(d);
  $('#clubBIActivityFinance').innerHTML=biLine('本期报名实收',money(a.paidCashInWindow))+biLine('本期实际现金退款',money(a.confirmedRefundCashInWindow))+biLine('本期现金净流入',money(a.netCashFlowInWindow))+biLine('本期订单截至当前净实收',money(a.bookingCohortNetCash),'含本期订单后来发生的退款')+biLine('俱乐部承担折扣',money(a.clubFundedDiscount))+biLine('平台承担补贴',money(a.platformFundedSubsidy))+biLine('全额退款订单',a.fullRefundOrders+' 笔')+biLine('部分退款订单',a.partialRefundOrders+' 笔');
  $('#clubBIMembers').innerHTML=biLine('本期独立付费用户',a.payingUsers)+biLine('重复付费用户',a.repeatPayingUsers)+biLine('新会员',m.newMembersInWindow)+biLine('会员活动积分余额',m.clubPointsBalance)+biLine('本期积分发放',m.clubPointsIssuedInWindow)+biLine('本期积分减少',m.clubPointsDebitedInWindow)+'<div class="sub" style="margin-top:14px">会员等级结构</div>'+Object.entries(m.tierCounts).map(([k,v])=>biLine(k,v+' 人')).join('');
  $('#clubBICommerceAI').innerHTML=biLine('商城归因销售额',money(g.attributedGMV))+biLine('商城订单已付现金',money(g.attributedCashPaid))+biLine('本期商城现金退款',money(g.cashRefundedInWindow))+biLine('本期新增佣金',money(g.commissionEarnedInWindow))+biLine('本期佣金退款冲回',money(g.commissionReversedInWindow))+biLine('本期实际结算',money(g.settlementPaidInWindow))+biLine('本期 AI 奖励',g.aiRewardCreditsInWindow+' Credits')+biLine('本期 AI 调用成功',ai.successfulCalls+' 次');
  $('#clubBIActivities').innerHTML=d.activities.length?`<div style="overflow-x:auto"><table class="table"><thead><tr><th>活动</th><th>报名订单</th><th>付费用户</th><th>报名实收</th><th>已确认退款</th><th>订单净实收</th><th>俱乐部折扣</th><th>平台补贴</th></tr></thead><tbody>${d.activities.map(x=>`<tr><td>${esc(x.title)}</td><td>${x.orders}</td><td>${x.paidBuyers}</td><td>${money(x.paidCash)}</td><td>${money(x.confirmedRefundCash)}</td><td><strong>${money(x.netCash)}</strong></td><td>${money(x.clubDiscount)}</td><td>${money(x.platformSubsidy)}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">所选周期暂无成功付费报名。</div>';
  $('#clubBIDefinitions').innerHTML=Object.entries(d.definitions).map(([k,v])=>`<div style="padding:4px 0">${esc(v)}</div>`).join('');
 }catch(e){ $('#analyticsMetrics').innerHTML=`<div class="notice warn">经营数据读取失败：${esc(e.message)}</div>`; }
}

/* ---- 报名与执行：按活动 → 按档期钻取（9 号团与 10 号团必须分开看）---- */
function statTile(k,v,warn){return `<div class="reg-occ__stat${warn?' warn':''}"><div class="k">${esc(k)}</div><div class="v">${esc(String(v))}</div></div>`}
function regRow(x){
  const n=regPcount(x);
  const rf=x.refund_status&&x.refund_status!=='none';
  return `<div class="list-row"><div class="list-row__main"><div class="list-row__title">付款人 ${esc(x.name)}${x.phone?` · ${esc(x.phone)}`:''}</div><div class="list-row__sub">${n} 位参加人 · 资料完整 ${Number(x.complete_participants||0)}/${n} · 待处理保险 ${Number(x.insurance_pending||0)} · 活动积分抵 ${money(x.club_point_discount)} / 平台补贴 ${money(x.platform_point_subsidy)}</div><div class="list-row__sub">报名时间 ${esc(x.created_at||'')}${rf?` · 退款状态 ${esc(x.refund_status)}`:''}</div></div><div class="list-row__end"><span class="act-card__price">${money(x.amount)}</span><span class="tag ${x.status==='paid'?'':'orange'}">${esc(x.status)}</span><button class="btn ghost" onclick="event.stopPropagation();showParticipants(${x.id})">参加人名单</button></div></div>`;
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
   字段，并写明 AI 详情文案不会跟着改 —— 否则用户会以为改了价格详情页就该跟着变。 */
function activitySavedBanner(changed){
  const host=document.getElementById('activities');if(!host)return;
  document.getElementById('actSavedBanner')?.remove();
  const when=new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
  const bar=document.createElement('div');
  bar.id='actSavedBanner';bar.className='act-saved';bar.setAttribute('role','status');
  bar.innerHTML='<span class="act-saved__ok" aria-hidden="true">✓</span>'
    +'<div class="act-saved__body"><b>活动信息已保存</b>'
    +'<span>'+(changed.length?'本次改动：'+esc(changed.join('、'))+' · ':'本次未改动任何字段 · ')+'已同步到 C 端<i>'+esc(when)+'</i></span>'
    +'<span class="act-saved__note">AI 详情文案不会自动跟着改；需要重写文案或排版请点「重新生成 / 换一版」。</span></div>'
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
    +'<span class="act-saved__note">填了正价即视为正式对外定价，C 端不再显示「价格待定」；已生成的 AI 详情文案不会自动跟着改。</span></div>'
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
                price:Number(a.price||0),capacity:Number(a.capacity||0)};
  const d=await uxForm({title:'编辑活动基本信息',
    subtitle:'改的是活动事实（名称 / 日期 / 地点 / 价格 / 名额），会同步到 C 端与页面上的事实字段；AI 详情文案如需重写，请用「重新生成 / 换一版」。',
    fields:[
      {name:'title',label:'活动名称',type:'text',required:true,full:true,value:a.title||''},
      {name:'eventDate',label:'活动日期',type:'text',value:a.event_date||'',placeholder:'如：2026-10-24 或 2026年10月24日'},
      {name:'location',label:'集合地 / 目的地',type:'text',value:a.location||''},
      {name:'price',label:'活动价格（元）',type:'number',min:0,step:.01,value:a.price||0},
      {name:'capacity',label:'总名额（人）',type:'number',min:0,step:1,value:a.capacity||0},
      {name:'checklist',label:'出行清单（每行一项）',type:'textarea',full:true,
       value:(a.activityMaster?.checklist||[]).join('\n'),
       placeholder:'冲锋衣\n登山杖\n头灯\n身份证件',
       help:'AI 已按活动地点 / 天数 / 难度给出初稿；这里可增删改。保存后 C 端出行清单同步更新，商城可配的装备自动挂到对应条目上。'}
    ],submitText:'保存并更新活动'});
  if(!d)return;
  const checklistLines=String(d.checklist||'').split('\n').map(x=>x.trim()).filter(Boolean);
  await uxFlow('editActivity',async()=>{
    await api(`/api/club/${CLUB}/activities/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({title:d.title,eventDate:d.eventDate,location:d.location,price:d.price,capacity:d.capacity,checklist:checklistLines})});
    const beforeList=(a.activityMaster?.checklist||[]).join('|');
    const changed=[];
    if(String(d.title||'').trim()!==before.title.trim())changed.push('名称');
    if(String(d.eventDate||'').trim()!==before.eventDate.trim())changed.push('日期');
    if(String(d.location||'').trim()!==before.location.trim())changed.push('地点');
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
      +`<div class="list-row__sub">擅长：${(r.specialties||[]).length?esc(r.specialties.join('、')):'未填写'}</div></div>`
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
   原先点「＋ 新增领队」会在「AI 详情预览」正中间盖出一张弹窗，
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
let POOL_ROSTER=[],POOL_SPECS=[];

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
      <label>角色<select name="role">${LEADER_ROLE_OPTS.map(o=>`<option value="${o.value}"${o.value===(r.role||'领队')?' selected':''}>${o.label}</option>`).join('')}</select></label>
      <label>常驻城市<input name="baseCity" value="${esc(r.base_city||'')}" placeholder="如 成都"></label>
      <label>状态<select name="status">${['active','inactive'].map(v=>`<option value="${v}"${v===(r.status||'active')?' selected':''}>${v==='active'?'在岗':'停用'}</option>`).join('')}</select></label>
      <label class="full">擅长方向<input name="specialties" value="${esc((r.specialties||[]).join(','))}" placeholder="从这些里选：${esc(specs.join('、'))}"></label>
      <label class="full">备注<textarea name="note" rows="2" placeholder="领队资质、带线经历等">${esc(r.note||'')}</textarea></label>
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
                 specialties:g('specialties'),note:g('note')};
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
    renderPoolPane();
  }catch(e){POOL_ROSTER=[];POOL_SPECS=[];renderPoolPane();loaderError('#lpRoster',e,'领队资源库加载失败')}
}

function poolRosterRows(roster){
  return roster.map(r=>`<div class="list-row leader-row"><div class="list-row__main">`
      +`<div class="list-row__title">${leaderAvatar(r.name,r.avatar_url,'sm')}${esc(r.name)} <span class="tag ${r.status==='active'?'':'orange'}">${r.status==='active'?'在岗':'已停用'}</span></div>`
      +`<div class="list-row__sub">${esc(r.role||'领队')}${r.phone?' · '+esc(r.phone):''}${r.base_city?' · 常驻 '+esc(r.base_city):''} · 累计带队 ${Number(r.assignedCount||0)} 次</div>`
      +`<div class="list-row__sub">擅长：${(r.specialties||[]).length?esc(r.specialties.join('、')):'未填写'}</div></div>`
      +`<div class="list-row__end"><button class="btn ghost" onclick="lpEditLeader(${r.id})">编辑</button>`
      +`<button class="btn ghost" onclick="lpRemoveLeader(${r.id})">${r.status==='active'?'停用':'删除'}</button></div></div>`).join('')
    ||'<div class="empty">资源库还没有领队：点右上角「＋ 新增领队」建好名册，之后每场活动直接挑人，系统也能开始按历史记录推荐。</div>';
}

function renderPoolPane(){
  const roster=POOL_ROSTER||[];
  const sum=$('#lpSummary');
  if(sum)sum.innerHTML=`领队资源库 <b>${roster.length}</b> 人 · 在岗 <b>${roster.filter(x=>x.status==='active').length}</b> 人 · 累计带队 <b>${roster.reduce((s,x)=>s+Number(x.assignedCount||0),0)}</b> 次`;
  const rowsEl=$('#lpRoster');
  if(rowsEl)rowsEl.innerHTML=poolRosterRows(roster);
  const fEl=$('#lpForm');
  if(fEl)fEl.innerHTML=(LEADER_FORM&&LEADER_FORM.mode)
    ?leaderFormHtml(0,LEADER_FORM.mode==='edit'?(roster.find(x=>Number(x.id)===Number(LEADER_FORM.leaderId))||{}):{})
    :'';
  mountLeaderCrop();
}

function lpToggleForm(){toggleLeaderForm('add',0)}
function lpEditLeader(id){toggleLeaderForm('edit',0,id)}
function lpRemoveLeader(id){removeLeader(id,0)}


/* ---------------------------------------------------------------------------
   会员装备折扣 / 领队推荐空态 / 充值单价
   这几块都是「新能力必须在界面上看得见、说得清」：会员价不能只存在于接口里，
   领队推荐为空时也不能什么都不显示（用户会以为是坏了）。
--------------------------------------------------------------------------- */
let _tiers=[];
function gearRate(r){const v=Number((r||{}).gear_discount);return v>0&&v<1?v:0}
function gearZhe(rate){const z=rate*10;return (Math.round(z*10)/10)+' 折'}
function bestGearDiscount(tiers){
  const use=(tiers||[]).filter(x=>gearRate(x)>0);
  if(!use.length)return null;
  use.sort((a,b)=>gearRate(a)-gearRate(b));      // 折扣越低（数字越小）越优惠
  const t=use[0],rate=gearRate(t);
  return {rate,tierName:t.name||'会员',discountZhe:Math.round(rate*1000)/100};
}
function tierGearChip(r){
  const rate=gearRate(r);
  return rate?`<span class="tier-gear">装备 ${esc(gearZhe(rate))}</span>`:'<span class="tier-gear tier-gear--off">装备无折扣</span>';
}
function tierGearNote(r){
  const rate=gearRate(r);
  if(!rate)return '装备商城按原价：会员在出行清单 / 装备商城里看不到会员价。点「编辑」可以设置折扣。';
  const t=(_tiers||[]).find(x=>gearRate(x)&&gearRate(x)<rate);
  return `装备商城 ${gearZhe(rate)}：这件等级在出行清单与装备商城里直接显示会员价${t?`（低于「${esc(t.name)}」的 ${esc(gearZhe(gearRate(t)))}）`:''}。`;
}
async function editTier(tierId){
  const r=(_tiers||[]).find(x=>Number(x.id)===Number(tierId));
  if(!r){showAlert({title:'读取等级失败',message:'请重新打开「客户会员」再试'});return}
  const rate=gearRate(r);
  const d=await uxForm({title:'编辑会员等级 · '+(r.name||''),
    subtitle:'装备商城折扣决定这个等级的会员在出行清单与商城里看到的会员价——「加入会员更便宜」要看得见才成立。',
    fields:[
      {name:'name',label:'等级名称',type:'text',required:true,value:r.name||''},
      {name:'rank',label:'等级顺序（数字越大等级越高）',type:'number',min:0,step:1,value:r.rank||0},
      {name:'minActivitySpend',label:'累计活动消费（元）',type:'number',min:0,step:1,value:r.min_activity_spend||0},
      {name:'minActivityCount',label:'累计活动次数',type:'number',min:0,step:1,value:r.min_activity_count||0},
      {name:'gearDiscount',label:'装备商城折扣',type:'select',full:true,value:String(rate||1),
        options:[{value:'1',label:'无折扣（按原价）'},{value:'0.95',label:'95 折'},{value:'0.9',label:'9 折'},
                 {value:'0.85',label:'85 折'},{value:'0.8',label:'8 折'}]},
      {name:'qualificationMode',label:'升级条件',type:'select',full:true,value:r.qualification_mode||'ANY',
        options:[{value:'ANY',label:'任一满足'},{value:'ALL',label:'同时满足'}]}
    ],submitText:'保存'});
  if(!d)return;
  await uxFlow('editTier',async()=>{
    let benefits=[];try{benefits=JSON.parse(r.benefits_json||'[]')||[]}catch(e){benefits=[]}
    // 必须把 benefits 原样带回：接口是全量覆盖，漏传会把这条等级已有的权益清空。
    await api(`/api/club/${CLUB}/membership/tiers/${tierId}`,{method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:d.name,rank:d.rank,minActivitySpend:d.minActivitySpend,
        minActivityCount:d.minActivityCount,gearDiscount:d.gearDiscount,
        qualificationMode:d.qualificationMode,benefits})});
    toast('会员等级已更新');
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
  const body=`<div style="margin-top:6px">${line('订单号','#'+o.id)}${line('买家',o.buyer)}${line('下单时间',o.created_at)}`
    +`${line('订单状态',enumCn(o.status))}${line('订单金额',money(items))}${line('本单佣金',money(o.club_commission))}`
    +`${line('物流',[o.carrier,o.tracking_no].filter(Boolean).join(' ')||'待发货')}${line('售后',o.after_sales_status||'无')}</div>`
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

/* AI 用量明细：Credits 余额看得出「花了多少」，但看不出「花在哪一步」。
   这张表把每次调用的类型、模型、token 与扣费摊开，超额时才知道该调哪类用法的额度。 */
async function loadAIUsage(){
  const box=$('#aiUsage'); if(!box)return;
  skel('#aiUsage',6);
  try{
    const list=await api('/api/club/'+CLUB+'/ai/usage')||[];
    /* 任务名与 channel-render.js 的 LABEL 对齐：渠道生成时 task_type 直接就是渠道 id，
   表里有 wechat/xhs/poster/recap（见 static/channel-render.js）。之前只认了 detail 一类，
   其余直接把英文 id 显示给用户。 */
    const task={detail:'活动详情生成',wechat:'微信公众号图文',xhs:'小红书图文',poster:'活动招募海报',recap:'活动回顾'};
    box.innerHTML=list.slice(0,60).map(u=>`<div class="list-row"><div class="list-row__main">`
      +`<div class="list-row__title">${esc(task[u.task_type]||u.task_type||'AI 调用')} <span class="tag">${esc(u.status||'—')}</span></div>`
      +`<div class="list-row__sub">${esc(u.model||'—')} · ${esc(u.provider||'—')} · ${u.input_tokens||0} in / ${u.output_tokens||0} out · ${esc(u.created_at||'')}</div>`
      +`<div class="list-row__end"><strong>${u.credits_charged!=null?('-'+u.credits_charged+' Credits'):(u.provider_cost!=null?money(u.provider_cost):'—')}</strong></div>`
      +`</div></div>`).join('')||'<div class="empty">还没有 AI 调用记录</div>';
  }catch(e){ box.innerHTML='<div class="empty">读取失败：'+esc(e.message)+'</div>' }
}


/* ===== 俱乐部设置（品牌 DIY · 2026-10-09）=================================
   订阅套件的俱乐部在这里自助包装自己的前端：名称 / logo / 口号 / 城市 / 联系人。
   保存后 C 端顾客看到的门面（顶栏名字、logo、首页品牌条）跟着变。
   logo 上传走独立端点（POST …/settings/logo），表单文本走 PUT …/settings；
   保存成功后同步刷新后台侧栏的品牌名，前后台看到的永远同一个名字。 */
let clubLogoUrl='';
let SETTINGS_TAB='brand';
/* 设置中心 tab：领队资源库/业务介绍/AI Credits/收款账户都收进了设置页，
   每个 tab 面板沿用原 load 函数（DOM id 不变），首次切入时才拉数据。 */
const SET_TAB_LOAD={brand:()=>loadSettings(),
  leaders:async()=>{await loadLeaders()},
  biz:async()=>{await loadBizSection()},
  credits:async()=>{await loadCredits();await loadAIUsage()},
  pay:async()=>{await loadPayAccount()}};
window.setTab=async name=>{
  if(!SET_TAB_LOAD[name])name='brand';
  SETTINGS_TAB=name;
  document.querySelectorAll('#setTabs .set-tab').forEach(b=>b.classList.toggle('active',b.getAttribute('onclick')===`setTab('${name}')`));
  ['brand','leaders','biz','credits','pay'].forEach(p=>{
    const el=document.getElementById('pane-'+p);
    if(el)el.style.display=p===name?'':'none';
  });
  try{await SET_TAB_LOAD[name]()}catch(e){}
};
async function loadSettings(){
  const box=$('#settingsBody');if(!box)return;
  box.innerHTML='<div class="empty">正在加载设置…</div>';
  let s;
  try{s=await api(`/api/club/${CLUB}/settings`)}
  catch(e){loaderError('#settingsBody',e,'设置读取失败');return}
  clubLogoUrl=s.logoUrl||'';
  box.innerHTML=`
  <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 18px">
    <label style="display:block"><span class="sub">俱乐部名称 *</span><input id="setName" maxlength="40" style="width:100%;margin-top:4px" value="${esc(s.name||'')}" placeholder="例如：远拓户外"></label>
    <label style="display:block"><span class="sub">品牌口号（C 端首页品牌条）</span><input id="setSlogan" maxlength="60" style="width:100%;margin-top:4px" value="${esc(s.slogan||'')}" placeholder="例如：把周末还给山野"></label>
    <label style="display:block"><span class="sub">所在城市</span><input id="setCity" maxlength="30" style="width:100%;margin-top:4px" value="${esc(s.city||'')}" placeholder="例如：成都"></label>
    <label style="display:block"><span class="sub">联系人</span><input id="setContact" maxlength="20" style="width:100%;margin-top:4px" value="${esc(s.contactName||'')}" placeholder="姓名"></label>
    <label style="display:block"><span class="sub">联系电话</span><input id="setPhone" maxlength="20" style="width:100%;margin-top:4px" value="${esc(s.contactPhone||'')}" placeholder="手机号"></label>
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
  try{
    await api(`/api/club/${CLUB}/settings`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(tip)tip.textContent='已保存 ✓';
    toast('俱乐部设置已保存，C 端门面已同步');
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
    +'<div class="list-row__end"><button class="btn ghost" type="button" onclick="editBizItem('+i+')">编辑</button> '
    +'<button class="btn ghost" type="button" onclick="deleteBizItem('+i+')">删除</button></div></div>').join('')
    ||'<div class="empty">还没有条目。加一条「企业团建」试试。</div>';
}
function bizToggle(on){if(!bizCfg)return;bizCfg.enabled=!!on;renderBiz();persistBiz()}
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
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
}
async function saveBizSection(){await persistBiz()}
async function addBizItem(){
  if((bizCfg?.items||[]).length>=8)return showAlert({title:'最多 8 条',message:'条目太多会让 C 端这一屏变成广告墙，建议合并同类业务。'});
  const f=await uxForm({title:'添加业务',subtitle:'比如企业团建、亲子研学、装备租赁',fields:[
    {name:'title',label:'业务名称',required:true,placeholder:'企业团建'},
    {name:'desc',label:'一句话说明',type:'textarea',placeholder:'10–50 人的团队定制，含场地、教练与装备'},
    {name:'image',label:'配图地址（可空）',placeholder:'/static/uploads/xxx.jpg 或 https://…'}],submitText:'添加'});
  if(!f)return;
  bizCfg.items=bizCfg.items||[];bizCfg.items.push({title:f.title||'',desc:f.desc||'',image:f.image||''});
  await persistBiz();
}
async function editBizItem(i){
  const it=(bizCfg?.items||[])[Number(i)];if(!it)return;
  const f=await uxForm({title:'编辑业务',fields:[
    {name:'title',label:'业务名称',required:true,value:it.title||''},
    {name:'desc',label:'一句话说明',type:'textarea',value:it.desc||''},
    {name:'image',label:'配图地址（可空）',value:it.image||''}],submitText:'保存'});
  if(!f)return;
  bizCfg.items[Number(i)]={title:f.title||'',desc:f.desc||'',image:f.image||''};
  await persistBiz();
}
async function deleteBizItem(i){
  const it=(bizCfg?.items||[])[Number(i)];if(!it)return;
  const ok=await uxConfirm({title:'删除业务条目',message:'确定删除「'+(it.title||'')+'」？删除后 C 端不再显示。',confirmText:'删除',danger:true});
  if(!ok)return;
  bizCfg.items.splice(Number(i),1);
  await persistBiz();
}
