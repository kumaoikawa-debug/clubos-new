let CLUB=1;let currentActivity=null;
navInit();window.go=v=>{document.querySelector(`.nav button[data-view="${v}"]`)?.click()};
window.onView=async v=>{if(v==='activities')await loadActivities();if(v==='content')await loadContent();if(v==='regs')await loadRegs();if(v==='execution')await loadExecution();if(v==='members')await loadMembers();if(v==='mall')await loadMall();if(v==='credits')await loadCredits();if(v==='analytics')await loadClubBI()};
async function loadDash(){skel('#recentActivities',4);let d=await api(`/api/club/${CLUB}/dashboard`);$('#creditPill').textContent=`AI Credits ${d.credits?.balance||0}`;$('#dashMetrics').innerHTML=[['活动',d.activityCount],['报名',d.registrationCount],['客户',d.memberCount],['商城GMV',money(d.gearGMV)]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div><div class="hint">独立经营数据</div></div>`).join('');$('#analyticsMetrics').innerHTML=[['活动数',d.activityCount],['报名数',d.registrationCount],['商城GMV',money(d.gearGMV)],['商城佣金',money(d.commission)]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div></div>`).join('');let a=await api(`/api/club/${CLUB}/activities`);$('#recentActivities').innerHTML=a.slice(0,5).map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.title)}</div><div class="list-row__sub">${dateText(x.event_date)} · ${esc(x.location||'')}</div></div></div>`).join('')||'<div class="empty">还没有活动</div>'}
/* ===== 活动中心：可搜索的活动栏 + 右侧成品预览（2026-09-26）=====
   过去 #activityRows 里又套了一层 .act-grid：外层网格只给内层一个格子宽，
   于是卡片被压成一列、右边永远空一大片。现在去掉嵌套，改成「左栏活动 + 右栏预览」。 */
let _acts=[],_actOpenId=null;
async function loadActivities(){
  skel('#activityRows',6);
  const s=$('#activitySearch'); if(s&&!s.__wired){s.__wired=true;s.oninput=()=>renderActivityList()}
  const so=$('#activitySort'); if(so&&!so.__wired){so.__wired=true;so.onchange=()=>renderActivityList()}
  _acts=await api(`/api/club/${CLUB}/activities`);
  renderActivityList();
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
async function openActivity(id){try{_actOpenId=Number(id);syncActRail();let a=await api(`/api/club/${CLUB}/activities/${id}`);currentActivity=a;let conflicts=a.activityMaster?.blocking_conflicts||[];const pane=$('#activityDetail');if(!pane)return;pane.innerHTML=`${detailNavHtml([['sec-cover','封面'],['sec-detail','AI 详情'],['sec-ops','行程与清单'],['sec-leaders','带队领队'],['sec-rules','报名与政策'],['sec-occ','团期价格']])}<div class="panel-title"><div><div class="eyebrow">AI EDITORIAL PREVIEW</div><h2 style="margin:4px 0">${esc(a.title)}</h2><div class="sub">${esc(a.event_date||'')} · ${esc(a.location||'')} · ${money(a.price)} · ${a.occurrences?.length||0} 个团期</div></div><div style="display:flex;gap:8px;flex-wrap:wrap">${a.status==='draft'?`<button class="btn" onclick="publishActivity(${id})">发布活动</button>`:'<span class="tag">已发布</span>'}<button class="btn ghost" onclick="goContentForActivity(${id})">去做宣发内容</button><a class="btn ghost" href="/web?club_id=${CLUB}&activity=${id}" target="_blank" style="text-decoration:none">打开C端</a>${a.detailVersion?.canRegenerate?`<button class="btn secondary" onclick="openRegenerateModal(${id})">重新生成 / 换一版</button>`:''}<button class="btn ghost" onclick="editActivity(${id})">编辑基本信息</button><button class="btn ghost" onclick="deleteActivity(${id})">删除活动</button></div></div>${conflicts.length?`<div class="notice warn">发现真实冲突：${conflicts.map(esc).join('；')}</div>`:''}<div class="notice" style="margin:10px 0 18px">AI 自己决定页面叙事、图片节奏和区块顺序；这里没有模板 A/B/C。</div><div class="card section" id="sec-cover"><div class="panel-title"><div><h3>活动封面</h3><div class="sub">用于 C 端活动列表卡片；建议横图 16:9，C 端仅在活动发布后展示。</div></div></div><div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap"><div style="width:160px;height:90px;border-radius:12px;background:#edf3f1;background-size:cover;background-position:center;display:flex;align-items:center;justify-content:center;color:#6b8a7b;font-size:12px;text-align:center;${a.cover?`background-image:url('/api/club/${CLUB}/activities/${a.id}/cover')`:''}">${a.cover?'':'未设封面'}</div><div style="display:flex;flex-direction:column;gap:8px"><input id="coverFile" type="file" accept="image/*"><button class="btn secondary" onclick="uploadCover(${a.id})">上传 / 替换封面</button></div></div></div><div id="sec-detail">${renderPromo(a.detail,a.activityMaster,{hideButton:true})}</div><div id="sec-ops">${renderInfoStack(a.activityMaster,{gear:a.gearRecommendations,manage:true,skip:infoStackSkip(a.detail)})}</div>${renderLeaderCard(a,id)}<div id="sec-rules">${pointsPolicyCard(a)}${refundPolicyCard(a)}${participantPolicyCard(a)}</div><div class="card section" id="sec-occ"><div class="panel-title"><h3>团期 / 价格 / 名额</h3><button class="btn secondary" onclick="quickAddOccurrence(${id})">＋ 添加团期</button></div>${(a.occurrences||[]).map(o=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(o.label||o.start_at)}</div><div class="list-row__sub">${money(o.price)} · 已售 ${o.sold}/${o.capacity}</div></div>`).join('')||'<div class="empty">暂无团期</div>'}</div>`;if(window.matchMedia&&matchMedia('(max-width:1180px)').matches)setTimeout(()=>pane.scrollIntoView({behavior:'smooth',block:'start'}),60);syncActRail()}catch(e){showAlert({title:'打开活动失败',message:e.message})}}

function pointsPolicyCard(a){
  const p=a.pointsPolicy||{}; const e=p.effective||{};
  const gearCap=p.gearPointsMaxDiscountAmount==null?'':p.gearPointsMaxDiscountAmount;
  return `<div class="card section" id="pointsPolicyCard">
    <div class="panel-title"><div><h3>活动积分规则</h3><div class="sub">由俱乐部决定这场活动是否参与积分。团期默认继承整场活动规则。</div></div><span class="tag ${p.enabled?'':'orange'}">${p.enabled?'已开启':'不参与积分'}</span></div>
    <label style="display:flex;gap:10px;align-items:center;padding:10px 0"><input id="ppEnabled" type="checkbox" ${p.enabled?'checked':''}> <strong>这场活动参与积分体系</strong></label>
    <div class="grid g2" style="margin-top:4px">
      <div class="notice"><label><input id="ppEarn" type="checkbox" ${p.earnClubPoints?'checked':''}> 报名后产生活动积分</label><div class="sub" style="margin-top:6px">成本由本俱乐部承担；默认按俱乐部积分规则累计。</div></div>
      <div class="notice"><label><input id="ppClub" type="checkbox" ${p.acceptClubPoints?'checked':''}> 允许活动积分抵现金</label><div style="margin-top:8px"><span class="sub">本单最多抵活动金额</span> <input id="ppClubMax" type="number" min="0" max="100" step="1" value="${Number(p.clubPointsMaxDiscountPercent??100)}" style="width:80px"> %</div></div>
      <div class="notice"><label><input id="ppGear" type="checkbox" ${p.acceptGearPoints?'checked':''} ${p.platformGearPointsAllowed?'':'disabled'}> 允许装备积分抵现金</label><div style="margin-top:8px"><span class="sub">本单最多补贴 ¥</span> <input id="ppGearMax" type="number" min="0" step="1" placeholder="不限制" value="${gearCap}" style="width:100px" ${p.platformGearPointsAllowed?'':'disabled'}></div><div class="sub" style="margin-top:6px">${p.platformGearPointsAllowed?'成本由 ClubOS 总平台承担。':'总平台当前已关闭活动场景的 Gear Points 补贴。'}</div></div>
      <div class="notice"><strong>C端实际生效</strong><div class="sub" style="margin-top:6px">累计活动积分：${e.earnClubPoints?'是':'否'} · 活动积分抵扣：${e.acceptClubPoints?'是':'否'} · 装备积分抵扣：${e.acceptGearPoints?'是':'否'}</div></div>
    </div>
    <button class="btn secondary" style="margin-top:12px" onclick="savePointsPolicy(${a.id})">保存积分规则</button>
  </div>`
}
async function savePointsPolicy(id){
  const payload={
    enabled:$('#ppEnabled').checked,
    earnClubPoints:$('#ppEarn').checked,
    acceptClubPoints:$('#ppClub').checked,
    clubPointsMaxDiscountPercent:Number($('#ppClubMax').value||0),
    acceptGearPoints:$('#ppGear').checked,
    gearPointsMaxDiscountAmount:$('#ppGearMax').value===''?null:Number($('#ppGearMax').value)
  };
  try{await api(`/api/club/${CLUB}/activities/${id}/points-policy`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});toast('活动积分规则已保存');await openActivity(id)}catch(e){showAlert({title:'操作失败',message:e.message})}
}

function participantPolicyCard(a){
  const p=a.participantPolicy||{};
  return `<div class="card section"><div class="panel-title"><div><h3>报名人 / 参加人规则</h3><div class="sub">付款人和真正参加活动的人分开管理；多人报名按参加人数占名额和计价。</div></div><span class="tag">最多 ${p.maxParticipantsPerOrder||8} 人/单</span></div><div class="grid g2"><div class="notice"><label><input id="pfIncomplete" type="checkbox" ${p.allowIncompleteAtCheckout!==false?'checked':''}> 允许先付款、后补资料</label><div class="sub" style="margin-top:6px">支付时至少填写姓名和手机号；证件/紧急联系人可在订单中心补充。</div></div><div class="notice"><label><input id="pfInsurance" type="checkbox" ${p.insuranceRequired?'checked':''}> 本活动需要保险资料</label><div class="sub" style="margin-top:6px">俱乐部可在报名名单中维护投保状态与保单号。</div></div><div class="notice"><label><input id="pfReplace" type="checkbox" ${p.allowParticipantReplacement?'checked':''}> 允许用户自助转名额</label><div style="margin-top:8px"><span class="sub">出发前至少</span> <input id="pfCutoff" type="number" min="0" value="${Number(p.replacementCutoffHours||0)}" style="width:80px"> 小时</div></div><div class="notice"><span class="sub">单笔最多报名</span> <input id="pfMax" type="number" min="1" max="50" value="${Number(p.maxParticipantsPerOrder||8)}" style="width:80px"> 人</div></div><button class="btn secondary" style="margin-top:12px" onclick="saveParticipantPolicy(${a.id})">保存参加人规则</button></div>`
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
$('#createForm').onsubmit=async e=>{e.preventDefault();let b=$('#genBtn');b.disabled=true;b.textContent='AI正在读资料、选图并排版…';try{let fd=new FormData(e.target);let r=await api(`/api/club/${CLUB}/activities/ai-generate`,{method:'POST',body:fd});modal('createModal',false);toast(`活动详情已完成 · 识别 ${r.source.imageCount} 张图片`);await loadDash();go('activities');await openActivity(r.activityId)}catch(err){showAlert({title:'AI 生成失败',message:err.message})}finally{b.disabled=false;b.textContent='AI直接生成详情'}};
/* ===== AI 内容中心：生成 → 直接出成品（公众号图文 / 小红书卡片 / 海报）=====
   过去 genChannel 把接口返回的 JSON 塞进 <pre>，老板拿到的是一堆代码。
   现在交给 static/channel-render.js 渲染成能直接用的成品，并可复制 / 下载。 */
async function loadContent(){
  skel('#channelArea',2);
  const acts=await api(`/api/club/${CLUB}/activities`);
  const sel=$('#contentActivity');
  if(!acts.length){
    if(sel)sel.innerHTML='<option value="">还没有活动</option>';
    $('#channelArea').innerHTML='<div class="empty">先创建一场活动，再让 AI 做内容。</div>';
    $('#contentList').innerHTML='<div class="empty">还没有渠道内容</div>';
    return;
  }
  if(sel){
    const want=Number(window.__contentActId||0);
    const keep=acts.some(a=>a.id===want)?want:acts[0].id;
    sel.innerHTML=acts.map(a=>`<option value="${a.id}"${a.id===keep?' selected':''}>${esc(a.title)}${a.status==='draft'?'（草稿）':''}</option>`).join('');
    sel.onchange=()=>{window.__contentActId=Number(sel.value)||null;loadContent()};
    window.__contentActId=keep;
  }
  const a=acts.find(x=>x.id===Number(window.__contentActId))||acts[0];
  currentActivity=a;
  const cards=[
    ['wechat','微信公众号图文','AI 重排公众号阅读节奏；生成后可直接预览，并一键复制带格式图文到公众号编辑器'],
    ['xhs','小红书图文','文案为主：独立标题、Hook、正文与话题标签可直接复制走；另出一张 1080×1440 首页海报，并把其余现场照片裁成 3:4 九宫格配图'],
    ['poster','活动招募海报','用活动真实封面 + AI 文案在本地合成 1080×1440 海报，可直接下载'],
    ['recap','活动回顾','只在有真实现场素材时才写；素材不足会明确告诉你缺什么，不编造']
  ];
  $('#channelArea').innerHTML=cards.map(x=>`<div class="card channel-card"><div><strong>${x[1]}</strong><p>${x[2]}</p><div class="sub">当前活动：${esc(a.title)}</div></div><button class="btn secondary" onclick="genChannel('${x[0]}',${a.id})">AI 生成${x[1]}</button></div>`).join('');
  const list=await api(`/api/club/${CLUB}/content`);
  $('#contentList').innerHTML=list.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.title||channelLabel(x.channel))}</div><div class="list-row__sub">活动 #${x.activity_id} · ${esc(x.created_at||'')}</div></div><div class="list-row__end"><span class="tag">${esc(channelLabel(x.channel))}</span><button class="btn secondary" onclick="openContentAsset(${x.id})">查看成品</button></div></div>`).join('')
    ||'<div class="empty">还没有渠道内容</div>';
}
function channelLabel(c){return (window.ChannelRender&&ChannelRender.label(c))||c}
async function genChannel(ch,id){
  try{
    // 生成会真花 AI Credits，动手前必须讲清楚。本地渲染成品（复制 / 下载）不再另计费。
    if(window.uxConfirm&&!await uxConfirm({title:'生成'+channelLabel(ch)+'内容',
      message:'系统会读取这场活动的真实资料并调用一次 AI，产生一次 AI Credits 计费。生成后直接给成品，可以复制 / 下载。',
      confirmText:'开始生成'}))return;
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
  let [a,tiers,benefits,reds]=await Promise.all([
    api(`/api/club/${CLUB}/members`),api(`/api/club/${CLUB}/membership/tiers`),
    api(`/api/club/${CLUB}/benefits`),api(`/api/club/${CLUB}/benefits/redemptions`)
  ]);
  $('#memberRows').innerHTML=a.map(x=>`<tr><td>${esc(x.name)}</td><td><strong>${esc(x.level)}</strong></td><td>${x.activity_count||0}</td><td>${money(x.lifetime_activity_spend||0)}</td><td>${x.club_points_balance}</td><td>${x.gear_points}</td><td>${esc(x.phone||'')}</td></tr>`).join('')||'<tr><td colspan="7" class="empty">暂无会员</td></tr>';
  _tiers=tiers;
  $('#tierList').innerHTML=tiers.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.name)} <span class="tag">Rank ${x.rank}</span>${tierGearChip(x)}</div><div class="list-row__sub">活动消费 ≥ ${money(x.min_activity_spend)} · 活动次数 ≥ ${x.min_activity_count} · ${x.qualification_mode==='ALL'?'同时满足':'任一满足'}</div><div class="list-row__sub">${tierGearNote(x)}</div></div><div class="list-row__end"><button class="btn ghost" onclick="editTier(${x.id})">编辑</button></div></div>`).join('');
  $('#clubBenefitList').innerHTML=benefits.filter(x=>x.owner_type==='CLUB').map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.title)}</div><div class="list-row__sub">${x.points_cost} 活动积分 · ${x.benefit_type==='activity_coupon'?`活动抵扣 ${money(x.cash_value)}`:esc(x.benefit_type)} · 成本由俱乐部承担 · 库存 ${x.stock==null?'不限':x.stock}</div></div></div>`).join('')||'<div class="empty">还没有俱乐部福利</div>';
  $('#clubBenefitRedemptions').innerHTML=reds.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.user_name)}</div> · ${esc(x.title)}<div class="list-row__sub">${x.points_spent} ${x.point_type==='club'?'活动积分':'装备积分'} · ${x.funding_owner==='CLUB'?'俱乐部承担':'平台承担'} · ${esc(x.voucher_code||'')}</div></div></div>`).join('')||'<div class="empty">暂无兑换记录</div>';
}
async function recalcMembership(){await api(`/api/club/${CLUB}/membership/recalculate`,{method:'POST'});toast('会员等级已重新计算');loadMembers()}

async function loadMall(){skel('#clubOrders',4);let [p,o,cs,sett,aftersales,tiers]=await Promise.all([api(`/api/club/${CLUB}/mall/products`),api(`/api/club/${CLUB}/mall/orders`),api(`/api/club/${CLUB}/mall/commission-summary`),api(`/api/club/${CLUB}/mall/settlements`),api(`/api/club/${CLUB}/mall/after-sales`),api(`/api/club/${CLUB}/membership/tiers`)]);const md=bestGearDiscount(tiers);$('#clubCommissionSummary').innerHTML=[['待签收',money(cs.pending)],['售后冻结',money(cs.frozen)],['可结算',money(cs.payableNow)],['已结算',money(cs.settled)]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]}</div><div class="hint">${cs.carryDebt&&x[0]==='可结算'?`退款待冲抵 ${money(cs.carryDebt)}`:`售后期 ${cs.policy.afterSalesDays} 天`}</div></div>`).join('');$('#clubProducts').innerHTML=p.map(x=>`<div class="product" data-pid="${x.id}"><div class="ph">🎒</div><h4>${esc(x.name)}</h4><div class="sub">${esc(x.category||'户外装备')}</div>${memberPriceHtml(x.price,md)}<div class="sub">库存 ${x.stock} · 平台统一履约</div></div>`).join('');$('#clubOrders').innerHTML=o.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">订单 #${x.id}</div> · ${money(x.total)} · 佣金 ${money(x.club_commission)}<div class="list-row__sub">${x.status} · ${x.carrier||'待发货'} ${x.tracking_no||''} · 售后 ${x.after_sales_status||'无'}</div></div></div>`).join('')||'<div class="empty">暂无商城订单</div>';$('#clubAfterSales').innerHTML=aftersales.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.case_type)}</div> · 订单 #${x.order_id} <span class="tag">${esc(x.status)}</span><div class="list-row__sub">平台售后处理 · ${esc(x.reason||'')} · 申请退款 ${money(x.requested_refund_amount||0)}</div></div></div>`).join('')||'<div class="empty">暂无商城售后</div>';$('#clubSettlements').innerHTML=sett.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${money(x.net_amount)}</div> · ${esc(x.payment_ref)} <span class="tag">${x.status}</span><div class="list-row__sub">佣金 ${money(x.gross_amount)} · 退款冲抵 ${money(x.deduction_amount)} · ${esc(x.paid_at||x.created_at)}</div></div></div>`).join('')||'<div class="empty">暂无结算记录</div>'}
async function loadCredits(){skel('#creditLedger',5);let d=await api(`/api/club/${CLUB}/credits`),sub=d.subscription||{};$('#creditAccount').innerHTML=`<div class="grid g4"><div class="stat-tile"><div class="k">当前可用</div><div class="v">${d.account?.balance||0}</div><div class="hint">AI Credits</div></div><div class="stat-tile"><div class="k">当前套餐</div><div class="v" style="font-size:22px">${esc(sub.plan_name||sub.plan_code||'未开通')}</div><div class="hint">月额度 ${d.account?.monthly_quota||0}</div></div><div class="stat-tile"><div class="k">本月已用</div><div class="v">${d.creditsConsumed||0}</div><div class="hint">成功调用 ${d.successfulCalls||0} 次</div></div><div class="stat-tile"><div class="k">待偿欠账</div><div class="v">${d.unresolvedDebt||0}</div><div class="hint">后续获得 Credits 自动优先抵扣</div></div></div><div class="notice section">大模型由<b>总平台统一接入并结算</b>，俱乐部不需要也无法配置模型或密钥；Credits 只决定计费，不决定模型质量——平台不会因为余额或套餐降低模型、减少图片或截断资料。</div>`;$('#creditTopups').innerHTML=(d.topupPackages||[]).map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(x.name)}</div><div class="list-row__sub">${money(x.amount)} · 共 ${x.credits} Credits · 单价 ${unitCreditPrice(x.amount,x.credits)}</div><button class="btn secondary" style="margin-top:6px" onclick="buyCredits('${x.code}')">创建充值订单</button></div></div>`).join('')||'<div class="empty">暂无充值包</div>';$('#creditPendingOrders').innerHTML=(d.pendingOrders||[]).map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${x.order_type==='subscription'?'套餐':'充值'} ${x.credits} Credits</div><div class="list-row__sub">${money(x.amount)} · 待付款确认 · ${esc(x.period_key||x.package_code||'')}</div></div></div>`).join('')||'<div class="empty">暂无待付款账单</div>';$('#creditLedger').innerHTML=d.ledger.map(x=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${x.amount>0?'+':''}${x.amount}</div> · ${esc(x.note||x.type)}<div class="list-row__sub">${esc(x.type)} · ${x.created_at}</div></div></div>`).join('')}
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
  let list=await api(`/api/club/${CLUB}/execution/occurrences`);
  $('#executionOccurrenceList').innerHTML=list.map(o=>`<div class="notice" style="margin-bottom:10px;cursor:pointer" onclick="openExecution(${o.id})"><div class="panel-title"><div><strong>${esc(o.activity_title)}</strong><div class="sub">${esc(o.label||o.start_at)} · ${money(o.price)} · 售出 ${o.sold}/${o.capacity}</div></div><span class="tag ${o.execution_status==='completed'?'':'orange'}">${execStateLabel[o.execution_status||'preparing']||esc(o.execution_status)}</span></div><div class="sub">实名 ${o.named_participants||0} · 资料待补 ${o.incomplete_participants||0} · 保险待处理 ${o.insurance_pending||0} · 已签到 ${o.checked_in||0}</div></div>`).join('')||'<div class="empty">暂无团期</div>';
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
  <div style="overflow:auto"><table class="table"><thead><tr><th>参加人</th><th>资料</th><th>保险</th><th>车辆</th><th>签到</th><th>操作</th></tr></thead><tbody>${parts.map(p=>`<tr><td><strong>${esc(p.name)}</strong><div class="sub">${esc(p.phone||'')} · 付款人 ${esc(p.payer_name||'')}</div></td><td><span class="tag ${p.form_status==='complete'?'':'orange'}">${p.form_status==='complete'?'完整':'待补'}</span></td><td>${esc(p.insurance_status||'pending')}<div class="sub">${esc(p.insurance_provider||'')} ${esc(p.insurance_policy_no||'')}</div></td><td>${esc(p.vehicle_group||'未分配')}</td><td><span class="tag ${p.checkin_status==='checked_in'?'':'orange'}">${esc(p.checkin_status||'pending')}</span></td><td><button class="btn ghost" onclick="assignVehicle(${oid},${p.id})">分车</button> <button class="btn ghost" onclick="quickCheckin(${oid},${p.id},'${p.checkin_status||'pending'}')">签到</button></td></tr>`).join('')||'<tr><td colspan="6" class="empty">暂无实名参加人</td></tr>'}</tbody></table></div></div>`;
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
      <div class="reg-occ__sub">${esc(o.start_at||'')}${o.activity_location?` · ${esc(o.activity_location)}`:''} · ${money(o.price)} · 名额 ${Number(o.sold||0)}/${Number(o.capacity||0)}</div></div>
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
async function editActivity(id){
  let a=currentActivity;
  if(!a||Number(a.id)!==Number(id)){
    try{a=await api(`/api/club/${CLUB}/activities/${id}`)}catch(e){showAlert({title:'读取活动失败',message:e.message});return}
  }
  const d=await uxForm({title:'编辑活动基本信息',
    subtitle:'改的是活动事实（名称 / 日期 / 地点 / 价格 / 名额），会同步到 C 端与页面上的事实字段；AI 详情文案如需重写，请用「重新生成 / 换一版」。',
    fields:[
      {name:'title',label:'活动名称',type:'text',required:true,full:true,value:a.title||''},
      {name:'eventDate',label:'活动日期',type:'text',value:a.event_date||'',placeholder:'如：2026-10-24 或 2026年10月24日'},
      {name:'location',label:'集合地 / 目的地',type:'text',value:a.location||''},
      {name:'price',label:'活动价格（元）',type:'number',min:0,step:.01,value:a.price||0},
      {name:'capacity',label:'总名额（人）',type:'number',min:0,step:1,value:a.capacity||0}
    ],submitText:'保存修改'});
  if(!d)return;
  await uxFlow('editActivity',async()=>{
    await api(`/api/club/${CLUB}/activities/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({title:d.title,eventDate:d.eventDate,location:d.location,price:d.price,capacity:d.capacity})});
    toast('活动信息已更新');
    await loadActivities();
    await openActivity(id);
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
    ||'<div class="empty">资源库还没有领队，先新增一位</div>';
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
  if(!roster.length){showAlert({title:'没有可选的领队',message:'资源库里没有「在岗且尚未安排」的领队。请先在下方「领队资源库」里新增或恢复一位。'});return}
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

function refreshLeaderPane(){
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
  const specs=(currentActivity&&currentActivity.leaderPlan&&currentActivity.leaderPlan.specialties)||[];
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
    await openActivity(actId);
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
  const lp=(currentActivity&&Number(currentActivity.id)===Number(actId))?(currentActivity.leaderPlan||{}):{};
  const r=(lp.roster||[]).find(x=>Number(x.id)===Number(leaderId))||{};
  const active=r.status==='active';
  if(!await uxConfirm({title:active?'停用领队':'删除领队',danger:true,confirmText:active?'停用':'删除',
    message:active?`「${r.name||''}」将不再出现在推荐与排班里，已有带队记录保留。若他正被安排在某团期上，安排不会自动撤销。`
                  :`「${r.name||''}」没有带队记录，将被彻底删除。`}))return;
  await uxFlow('removeLeader',async()=>{
    const res=await api(`/api/club/${CLUB}/leaders/${leaderId}`,{method:'DELETE'});
    toast(res.mode==='deactivated'?'已停用（带队记录保留）':'已删除');
    await openActivity(actId);
  });
}


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
