const qs=new URLSearchParams(location.search),OCC=Number(qs.get('occurrence')||0);let CLUB=Number(qs.get('club_id')||1);
const labels={preparing:'准备中',departed:'已出发',in_progress:'进行中',completed:'已完成'};
let _scanStream=null,_scanRAF=null,_scanDone=false;
async function loadLeader(){if(!OCC){return loadLeaderPicker()}let d=await api(`/api/leader/occurrences/${OCC}?club_id=${CLUB}`),o=d.occurrence||{},s=d.summary||{},st=d.settings||{};renderLeaderHead(o,d.leaders||[]);$('#lmeta').textContent=`${o.label||o.start_at||''} · ${o.activity_location||''}`;let seq=['preparing','departed','in_progress','completed'],cur=o.execution_status||'preparing';$('#leaderBody').innerHTML=`<div class="card"><div class="exec-step">${seq.map(x=>`<span class="${x===cur?'active':''}">${labels[x]}</span>`).join('')}</div><div class="grid g4 section">${[['参加',s.participantCount],['已签到',s.checkedInCount],['未到',s.noShowCount],['保险待办',s.insurancePendingCount]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]||0}</div></div>`).join('')}</div><div class="leader-actions"><button class="btn" onclick="advance('${cur}')">推进活动状态</button></div></div>
${leaderCardsHtml(d.leaders||[])}<div class="card section"><h3>集合 / 应急</h3><div><strong>${esc(st.meeting_time||'集合时间待设置')}</strong></div><div class="sub">${esc(st.meeting_location||'集合地点待设置')}</div><div class="sub" style="margin-top:8px">应急电话：${esc(st.emergency_phone||'待设置')}</div>${st.leader_note?`<div class="notice" style="margin-top:10px">${esc(st.leader_note)}</div>`:''}</div>
${d.issues?.length?`<div class="notice warn section"><strong>待处理</strong><div class="sub">${d.issues.map(esc).join('；')}</div></div>`:''}
<div class="card section"><h3>已发送通知</h3>${(d.notices||[]).map(n=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(n.title)}</div><div class="list-row__sub">${esc(n.content)}</div></div></div>`).join('')||'<div class="empty">暂无通知</div>'}</div>
<div class="section"><div class="panel-title"><h3>出发签到</h3><span class="tag">${s.checkedInCount||0}/${s.participantCount||0}</span><button class="btn primary" style="margin-left:auto" onclick="openScan()">扫码签到</button></div><div class="leader-list">${(d.participants||[]).map(p=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(p.name)}</div><div class="list-row__sub">${esc(p.phone||'')} · ${esc(p.vehicle_group||'未分车')}</div><div class="list-row__sub">保险 ${({pending:'待投保',enrolling:'投保中',insured:'已投保',cancelling:'退保中',cancelled:'已退保',failed:'投保失败',cancel_failed:'退保失败',not_required:'无需保险',submitted:'已提交',processing:'办理中',done:'已投保',completed:'已投保'})[p.insurance_status]||esc(p.insurance_status||'待投保')}${p.insurance_policy_no?` · ${esc(p.insurance_policy_no)}`:''}${p.effective_at?` · 生效 ${esc(String(p.effective_at).slice(0,10))}`:''} · 资料 ${p.form_status==='complete'?'完整':'待补'}</div></div><div class="list-row__end"><div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px"><span class="tag ${p.checkin_status==='checked_in'?'':'orange'}">${esc(p.checkin_status||'pending')}</span><div class="leader-actions" style="margin:0"><button class="btn secondary" onclick="checkin(${p.id},'checked_in')">到场</button><button class="btn ghost" onclick="checkin(${p.id},'no_show')">未到</button><button class="btn ghost" onclick="checkin(${p.id},'cancelled')">临时取消</button></div></div></div></div>`).join('')||'<div class="empty">暂无参加人</div>'}</div></div>`}
async function checkin(pid,status){try{await api(`/api/leader/occurrences/${OCC}/participants/${pid}/checkin?club_id=${CLUB}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});toast('签到已更新');loadLeader()}catch(e){showAlert({title:'签到失败',message:e.message})}}
async function advance(cur){let seq=['preparing','departed','in_progress','completed'],next=seq[Math.min(seq.indexOf(cur)+1,seq.length-1)];if(cur==='completed'){toast('活动已完成');return}if(!(await showConfirm({title:'推进活动状态',message:`${labels[cur]} → ${labels[next]}？`,confirmText:'确认推进'})))return;try{await api(`/api/leader/occurrences/${OCC}/status?club_id=${CLUB}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:next})});toast('状态已更新');loadLeader()}catch(e){showAlert({title:'操作失败',message:e.message})}}
/* 领队落地页：裸开 /leader（URL 里没有 occurrence）时，列出现有场次让领队自己点进去。
   此前这里只回一句「缺少 occurrence 参数」—— 领队在集合点、在车上单手翻手机，
   第一件事是「今天这场在哪、我带哪场、打给谁」，一个参数报错页等于把人挡在门外。
   数据走 /api/leader/occurrences（后端走白名单，不含价格/售出等经营字段）。 */
async function loadLeaderPicker(){
  const lt=$('#ltitle'),lm=$('#lmeta');
  if(lt)lt.textContent='我要执行的场次';
  if(lm)lm.textContent='选一场进入执行页';
  skel('#leaderBody',4);
  let list;
  try{list=await api(`/api/leader/occurrences?club_id=${CLUB}`)}
  catch(e){return loaderError('#leaderBody',e,'执行场次加载失败')}
  if(!list.length){
    return $('#leaderBody').innerHTML='<div class="card"><div class="empty">暂无可执行场次<div class="sub" style="margin-top:6px">俱乐部在「活动执行」里建好团期并指派领队后，这里就会出现。</div></div></div>';
  }
  const active=list.filter(o=>o.execution_status!=='completed');
  const finished=list.filter(o=>o.execution_status==='completed');
  const rowHtml=o=>{
    const ls=o.leaders||[],done=o.execution_status==='completed';
    const who=ls.length
      ?`<span class="leader-mini">${ls.map(l=>leaderAvatar(l.name||'',l.avatar_url,'sm')).join('')}</span>${ls.map(l=>esc(l.name||'')).join('、')}`
      :'未指派领队';
    return `<a class="list-row list-row--tap" href="/leader?occurrence=${o.id}&club_id=${CLUB}">`
      +`<div class="list-row__main"><div class="list-row__title">${esc(o.activity_title||'未命名活动')}</div>`
      +`<div class="list-row__sub">${esc(o.label||o.start_at||'')}${o.activity_location?` · ${esc(o.activity_location)}`:''}</div>`
      +`<div class="list-row__sub">${who} · 实名 ${o.named_participants||0} · 已签到 ${o.checked_in||0}${o.insurance_pending?` · 保险待办 ${o.insurance_pending}`:''}</div></div>`
      +`<div class="list-row__end"><span class="tag ${done?'':'orange'}">${labels[o.execution_status]||esc(o.execution_status||'preparing')}</span><span class="list-row__chev" aria-hidden="true">›</span></div></a>`;
  };
  $('#leaderBody').innerHTML=
    `<div class="card section">${active.map(rowHtml).join('')||'<div class="empty">暂无进行中的场次</div>'}</div>`
    +(finished.length?`<div class="card section"><div class="panel-title"><h3>已完成</h3><span class="tag">${finished.length}</span></div>${finished.map(rowHtml).join('')}</div>`:'');
}

/* 顾客咨询（2026-10-28 用户要求「让领队也能看到、有提示」）：
   C 端点「咨询领队」留下的问题，以前只有俱乐部「设置 → 领队资源库」深处的折叠块能看到，
   被问到的那位领队本人在 /leader 上完全不知情。这里按领队账号身份（users.phone ↔ 领队名册
   phone，与 /api/leader/occurrences 同一口径）拉「问我的」那几条，顶在页面最上方并带顾客电话：
   在山里、在车上接到活的领队，第一件事就是回这个电话。 */
async function loadMyInquiries(){
  const box=document.getElementById('leaderInbox');if(!box)return;
  // 内测（没有真领队账号）时允许 URL 带 leader_id 指定本人；生产由后端只认登录身份。
  const lid=qs.get('leader_id');
  let d;try{d=await api(`/api/leader/inquiries?club_id=${CLUB}${lid?`&leader_id=${encodeURIComponent(lid)}`:''}`,{cache:'no-store'})}catch(_e){box.innerHTML='';return}
  const list=(d&&d.inquiries)||[],n=Number((d&&d.newCount)||0);
  const base=document.title.replace(/^\(\d+\)\s*/,'');
  document.title=n?`(${n}) ${base}`:base;
  if(!list.length){box.innerHTML='';return}
  box.innerHTML=`<div class="card section" style="border-color:${n?'#e6c98d':'var(--line)'}">
    <h3 style="margin:0 0 4px">顾客咨询${n?` · <span style="color:#b8722d">待回复 ${n}</span>`:' · 已全部回复'}</h3>
    <div class="sub" style="margin-bottom:8px">顾客在 C 端点你的名字问的问题，回完电话点「标记已回复」就收口。</div>
    ${list.map(i=>`<div class="list-row"><div class="list-row__main">
      <div class="list-row__title">${esc(i.name||'山友')} <span class="tag ${i.status==='new'?'orange':''}">${i.status==='new'?'待回复':'已回复'}</span></div>
      <div class="list-row__sub">${esc(i.message)}</div>
      <div class="list-row__sub">${esc(i.phone||'')}${i.activityTitle?` · 来自《${esc(i.activityTitle)}》`:''} · ${esc(String(i.createdAt||'').slice(0,16))}</div>
    </div><div class="list-row__end"><div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end">
      ${i.phone?`<a class="btn ghost" href="tel:${esc(i.phone)}">打电话</a>`:''}
      <button class="btn ghost" onclick="markInquiry(${Number(i.id)},${i.status==='new'?1:0})">${i.status==='new'?'标记已回复':'改回待回复'}</button>
    </div></div></div>`).join('')}
  </div>`;
}
async function markInquiry(id,handled){
  try{
    await api(`/api/leader/inquiries/${Number(id)}/handled?club_id=${CLUB}`,{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({handled:!!Number(handled)})});
    toast(Number(handled)?'已标记为已回复':'已改回待回复');
    loadMyInquiries();
  }catch(e){showAlert({title:'操作失败',message:e.message})}
}
window.markInquiry=markInquiry;

/* ── 扫码签到（2026-10-30）：领队相机扫码核销 C 端签到码 ─────────────────── */
/* 二维码内容 = 后端 HMAC 签名 token（"club.occurrence.participant.sig"）；
   解出后 POST /api/leader/occurrences/{OCC}/checkin-by-token 核销（actor=leader_scan）。
   相机不可用时（无权限/无设备）提供「手动输入」兜底，绝不阻断签到。 */
function openScan(){
  let ov=document.getElementById('scanOverlay');
  if(!ov){
    ov=document.createElement('div'); ov.id='scanOverlay'; ov.className='scan-ov';
    ov.innerHTML=`<div class="scan-box">
      <div class="scan-box__hd">扫码签到</div>
      <div class="scan-stage"><video id="scanVideo" class="scan-video" playsinline muted></video><div class="scan-reticle"></div></div>
      <div id="scanMsg" class="scan-msg">将顾客签到码对准取景框</div>
      <div class="scan-actions">
        <button class="btn primary" onclick="closeScan()">关闭</button>
        <button class="btn ghost" onclick="scanManual()">手动输入</button>
      </div>
    </div>`;
    document.body.appendChild(ov);
    ov.addEventListener('click',e=>{ if(e.target===ov) closeScan(); });
  }
  ov.style.display='flex'; _scanDone=false;
  startCamera();
}
async function startCamera(){
  const v=document.getElementById('scanVideo'),msg=document.getElementById('scanMsg');
  if(!v) return;
  if(typeof jsQR==='undefined'){ if(msg)msg.textContent='扫码组件未加载，请用「手动输入」'; return; }
  try{
    _scanStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});
    v.srcObject=_scanStream; await v.play();
    scanLoop();
  }catch(e){
    if(msg)msg.textContent='无法打开相机（'+(e&&e.name||'权限不足')+'），请点「手动输入」';
  }
}
function scanLoop(){
  const v=document.getElementById('scanVideo');
  if(!v||!_scanStream) return;
  if(v.readyState>=2 && v.videoWidth){
    const cv=document.createElement('canvas'); cv.width=v.videoWidth; cv.height=v.videoHeight;
    const ctx=cv.getContext('2d'); ctx.drawImage(v,0,0,cv.width,cv.height);
    let code=null;
    try{ const imgd=ctx.getImageData(0,0,cv.width,cv.height); code=jsQR(imgd.data,cv.width,cv.height); }catch(e){}
    if(code&&code.data){ onScanToken(code.data.trim()); return; }
  }
  _scanRAF=requestAnimationFrame(scanLoop);
}
function onScanToken(token){
  if(_scanDone) return; _scanDone=true;
  const msg=document.getElementById('scanMsg'); if(msg)msg.textContent='识别成功，正在签到…';
  checkinByToken(token).catch(e=>{ if(msg)msg.textContent='签到失败：'+e.message; _scanDone=false; });
}
async function checkinByToken(token){
  await api(`/api/leader/occurrences/${OCC}/checkin-by-token?club_id=${CLUB}`,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
  toast('签到成功');
  closeScan();
  await loadLeader();
}
function closeScan(){
  if(_scanStream){ _scanStream.getTracks().forEach(t=>t.stop()); _scanStream=null; }
  if(_scanRAF) cancelAnimationFrame(_scanRAF); _scanRAF=null; _scanDone=false;
  const ov=document.getElementById('scanOverlay'); if(ov) ov.style.display='none';
}
function scanManual(){
  const t=(window.prompt('请输入签到码（签到二维码下方的字符串）：')||'').trim();
  if(!t) return;
  checkinByToken(t);
}
window.openScan=openScan; window.closeScan=closeScan; window.scanManual=scanManual;

async function startLeader(){
  if(clubosCookie('clubos_csrf')){const me=await api('/api/auth/me');if(me.role!=='leader')throw new Error('请使用领队账号登录');CLUB=Number(me.clubId);}
  loadMyInquiries();          // 不 await：咨询卡片先出，别让执行页等它
  await loadLeader();
}
/* 头部领队头像组。领队端基本是在山里、在车上、单手举手机打开的页面，
   认人必须靠「脸」而不是「字」：多领队时叠成一排，没有领队时给个空位占位。
   小头像（24px）时叠排会看不清，所以这里统一用 lg，靠负边距做重叠。 */
function renderLeaderHead(o,leaders){
  // index.html 的静态页头里已经写好一行 eyebrow 和 #lmeta；下面会整体换成 headrow 版本。
  // 不清理就会同时出现两行「LEADER EXECUTION」，以及两个同 id 的 #lmeta（截图中实测）。
  // 只清 .leader-head 的直接子节点：headrow 里的那份是新注入的，不在这个选择器范围内。
  document.querySelectorAll('.leader-head > .eyebrow').forEach(e=>e.remove());
  document.querySelectorAll('.leader-head > #lmeta').forEach(e=>e.remove());
  const ls=leaders||[];
  const stack=ls.length
    ? `<div class="leader-stack">${ls.map(l=>`<span class="leader-stack__it" title="${esc(l.name||'')}">${leaderAvatar(l.name||'',l.avatar_url,'lg')}</span>`).join('')}</div>`
    : `<span class="leader-av leader-av--lg" style="background:#ccd8d0" aria-hidden="true"></span>`;
  const title=$('#ltitle');
  (title.closest('.leader-headrow')||title).outerHTML=`<div class="leader-headrow">${stack}<div>`
    +`<div class="eyebrow">LEADER EXECUTION</div>`
    +`<h2 id="ltitle" style="margin:2px 0">${esc(o.activity_title||'活动执行')}</h2>`
    +`<div class="sub" id="lmeta"></div></div></div>`;
  return ls;
}

/* 「本次带队」卡片。执行页此前完全不显示谁带这场，
   集合点到了才发现名单里没有领队的电话。 */
function leaderCardsHtml(leaders){
  const ls=leaders||[];
  if(!ls.length)return `<div class="card section"><h3>本次带队</h3><div class="empty">这场活动还没有指派领队</div></div>`;
  return `<div class="card section"><h3>本次带队</h3><div class="leader-cards">${ls.map(l=>`
    <div class="leader-card">
      <div class="leader-card__top">${leaderAvatar(l.name||'',l.avatar_url,'lg')}
        <div class="leader-card__id"><b>${esc(l.name||'未命名')}</b><i>${esc(l.role||'领队')}</i>
        ${l.base_city?`<u>${esc(l.base_city)}</u>`:''}</div></div>
      ${l.phone?`<a class="leader-card__tel" href="tel:${esc(l.phone)}">${esc(l.phone)}</a>`:''}
      ${l.note?`<div class="sub" style="margin-top:6px">${esc(l.note)}</div>`:''}
    </div>`).join('')}</div></div>`;
}

startLeader().catch(err=>{showAlert({title:'无法进入领队页',message:err.message});setTimeout(()=>location.href='/login',1800)});
