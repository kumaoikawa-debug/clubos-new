const qs=new URLSearchParams(location.search),OCC=Number(qs.get('occurrence')||0);let CLUB=Number(qs.get('club_id')||1);
const labels={preparing:'准备中',departed:'已出发',in_progress:'进行中',completed:'已完成'};
async function loadLeader(){if(!OCC){return loadLeaderPicker()}let d=await api(`/api/leader/occurrences/${OCC}?club_id=${CLUB}`),o=d.occurrence||{},s=d.summary||{},st=d.settings||{};renderLeaderHead(o,d.leaders||[]);$('#lmeta').textContent=`${o.label||o.start_at||''} · ${o.activity_location||''}`;let seq=['preparing','departed','in_progress','completed'],cur=o.execution_status||'preparing';$('#leaderBody').innerHTML=`<div class="card"><div class="exec-step">${seq.map(x=>`<span class="${x===cur?'active':''}">${labels[x]}</span>`).join('')}</div><div class="grid g4 section">${[['参加',s.participantCount],['已签到',s.checkedInCount],['未到',s.noShowCount],['保险待办',s.insurancePendingCount]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]||0}</div></div>`).join('')}</div><div class="leader-actions"><button class="btn" onclick="advance('${cur}')">推进活动状态</button></div></div>
${leaderCardsHtml(d.leaders||[])}<div class="card section"><h3>集合 / 应急</h3><div><strong>${esc(st.meeting_time||'集合时间待设置')}</strong></div><div class="sub">${esc(st.meeting_location||'集合地点待设置')}</div><div class="sub" style="margin-top:8px">应急电话：${esc(st.emergency_phone||'待设置')}</div>${st.leader_note?`<div class="notice" style="margin-top:10px">${esc(st.leader_note)}</div>`:''}</div>
${d.issues?.length?`<div class="notice warn section"><strong>待处理</strong><div class="sub">${d.issues.map(esc).join('；')}</div></div>`:''}
<div class="card section"><h3>已发送通知</h3>${(d.notices||[]).map(n=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(n.title)}</div><div class="list-row__sub">${esc(n.content)}</div></div></div>`).join('')||'<div class="empty">暂无通知</div>'}</div>
<div class="section"><div class="panel-title"><h3>出发签到</h3><span class="tag">${s.checkedInCount||0}/${s.participantCount||0}</span></div><div class="leader-list">${(d.participants||[]).map(p=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(p.name)}</div><div class="list-row__sub">${esc(p.phone||'')} · ${esc(p.vehicle_group||'未分车')}</div><div class="list-row__sub">保险 ${({pending:'待投保',enrolling:'投保中',insured:'已投保',cancelling:'退保中',cancelled:'已退保',failed:'投保失败',cancel_failed:'退保失败',not_required:'无需保险',submitted:'已提交',processing:'办理中',done:'已投保',completed:'已投保'})[p.insurance_status]||esc(p.insurance_status||'待投保')}${p.insurance_policy_no?` · ${esc(p.insurance_policy_no)}`:''}${p.effective_at?` · 生效 ${esc(String(p.effective_at).slice(0,10))}`:''} · 资料 ${p.form_status==='complete'?'完整':'待补'}</div></div><div class="list-row__end"><div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px"><span class="tag ${p.checkin_status==='checked_in'?'':'orange'}">${esc(p.checkin_status||'pending')}</span><div class="leader-actions" style="margin:0"><button class="btn secondary" onclick="checkin(${p.id},'checked_in')">到场</button><button class="btn ghost" onclick="checkin(${p.id},'no_show')">未到</button><button class="btn ghost" onclick="checkin(${p.id},'cancelled')">临时取消</button></div></div></div></div>`).join('')||'<div class="empty">暂无参加人</div>'}</div></div>`}
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

async function startLeader(){
  if(clubosCookie('clubos_csrf')){const me=await api('/api/auth/me');if(me.role!=='leader')throw new Error('请使用领队账号登录');CLUB=Number(me.clubId);}
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
  $('#ltitle').outerHTML=`<div class="leader-headrow">${stack}<div>`
    +`<div class="eyebrow">LEADER EXECUTION</div>`
    +`<h2 style="margin:2px 0">${esc(o.activity_title||'活动执行')}</h2>`
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
