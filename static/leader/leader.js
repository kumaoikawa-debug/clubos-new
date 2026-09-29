const qs=new URLSearchParams(location.search),OCC=Number(qs.get('occurrence')||0);let CLUB=Number(qs.get('club_id')||1);
const labels={preparing:'准备中',departed:'已出发',in_progress:'进行中',completed:'已完成'};
async function loadLeader(){if(!OCC){$('#leaderBody').innerHTML='<div class="notice warn">缺少 occurrence 参数</div>';return}let d=await api(`/api/leader/occurrences/${OCC}?club_id=${CLUB}`),o=d.occurrence||{},s=d.summary||{},st=d.settings||{};renderLeaderHead(o,d.leaders||[]);$('#lmeta').textContent=`${o.label||o.start_at||''} · ${o.activity_location||''}`;let seq=['preparing','departed','in_progress','completed'],cur=o.execution_status||'preparing';$('#leaderBody').innerHTML=`<div class="card"><div class="exec-step">${seq.map(x=>`<span class="${x===cur?'active':''}">${labels[x]}</span>`).join('')}</div><div class="grid g4 section">${[['参加',s.participantCount],['已签到',s.checkedInCount],['未到',s.noShowCount],['保险待办',s.insurancePendingCount]].map(x=>`<div class="stat-tile"><div class="k">${x[0]}</div><div class="v">${x[1]||0}</div></div>`).join('')}</div><div class="leader-actions"><button class="btn" onclick="advance('${cur}')">推进活动状态</button></div></div>
${leaderCardsHtml(d.leaders||[])}<div class="card section"><h3>集合 / 应急</h3><div><strong>${esc(st.meeting_time||'集合时间待设置')}</strong></div><div class="sub">${esc(st.meeting_location||'集合地点待设置')}</div><div class="sub" style="margin-top:8px">应急电话：${esc(st.emergency_phone||'待设置')}</div>${st.leader_note?`<div class="notice" style="margin-top:10px">${esc(st.leader_note)}</div>`:''}</div>
${d.issues?.length?`<div class="notice warn section"><strong>待处理</strong><div class="sub">${d.issues.map(esc).join('；')}</div></div>`:''}
<div class="card section"><h3>已发送通知</h3>${(d.notices||[]).map(n=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(n.title)}</div><div class="list-row__sub">${esc(n.content)}</div></div></div>`).join('')||'<div class="empty">暂无通知</div>'}</div>
<div class="section"><div class="panel-title"><h3>出发签到</h3><span class="tag">${s.checkedInCount||0}/${s.participantCount||0}</span></div><div class="leader-list">${(d.participants||[]).map(p=>`<div class="list-row"><div class="list-row__main"><div class="list-row__title">${esc(p.name)}</div><div class="list-row__sub">${esc(p.phone||'')} · ${esc(p.vehicle_group||'未分车')}</div><div class="list-row__sub">保险 ${({pending:'待投保',enrolling:'投保中',insured:'已投保',cancelling:'退保中',cancelled:'已退保',failed:'投保失败',cancel_failed:'退保失败',not_required:'无需保险',submitted:'已提交',processing:'办理中',done:'已投保',completed:'已投保'})[p.insurance_status]||esc(p.insurance_status||'待投保')}${p.insurance_policy_no?` · ${esc(p.insurance_policy_no)}`:''}${p.effective_at?` · 生效 ${esc(String(p.effective_at).slice(0,10))}`:''} · 资料 ${p.form_status==='complete'?'完整':'待补'}</div></div><div class="list-row__end"><div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px"><span class="tag ${p.checkin_status==='checked_in'?'':'orange'}">${esc(p.checkin_status||'pending')}</span><div class="leader-actions" style="margin:0"><button class="btn secondary" onclick="checkin(${p.id},'checked_in')">到场</button><button class="btn ghost" onclick="checkin(${p.id},'no_show')">未到</button><button class="btn ghost" onclick="checkin(${p.id},'cancelled')">临时取消</button></div></div></div></div>`).join('')||'<div class="empty">暂无参加人</div>'}</div></div>`}
async function checkin(pid,status){try{await api(`/api/leader/occurrences/${OCC}/participants/${pid}/checkin?club_id=${CLUB}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});toast('签到已更新');loadLeader()}catch(e){showAlert({title:'签到失败',message:e.message})}}
async function advance(cur){let seq=['preparing','departed','in_progress','completed'],next=seq[Math.min(seq.indexOf(cur)+1,seq.length-1)];if(cur==='completed'){toast('活动已完成');return}if(!(await showConfirm({title:'推进活动状态',message:`${labels[cur]} → ${labels[next]}？`,confirmText:'确认推进'})))return;try{await api(`/api/leader/occurrences/${OCC}/status?club_id=${CLUB}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:next})});toast('状态已更新');loadLeader()}catch(e){showAlert({title:'操作失败',message:e.message})}}
async function startLeader(){
  if(clubosCookie('clubos_csrf')){const me=await api('/api/auth/me');if(me.role!=='leader')throw new Error('请使用领队账号登录');CLUB=Number(me.clubId);}
  await loadLeader();
}
/* 头部领队头像组。领队端基本是在山里、在车上、单手举手机打开的页面，
   认人必须靠「脸」而不是「字」：多领队时叠成一排，没有领队时给个空位占位。
   小头像（24px）时叠排会看不清，所以这里统一用 lg，靠负边距做重叠。 */
function renderLeaderHead(o,leaders){
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
