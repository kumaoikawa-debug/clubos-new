/* ===========================================================================
 * ClubOS 营地经营 · 俱乐部后台（2026-11-10）
 * 依赖（shared.js / club.js 已先加载）：CLUB / api / esc / toast / showAlert /
 *       showConfirm / uxDialog / $ / $$ / go
 * 后端契约见 app.py 营地模块 API：
 *   /api/club/{CLUB}/module-switches        GET/PATCH {home,activity,camp,bizIntro}
 *   /api/club/{CLUB}/camps                  GET/POST/PATCH/DELETE（返回 camelCase）
 *   /api/club/{CLUB}/pitches?camp_id=        GET/POST/PATCH/DELETE（返回 snake_case）
 *   /api/public/clubs/{CLUB}/camp-availability  GET ?camp_id&start&end
 *   /api/club/{CLUB}/pitch-status           GET/POST（封锁 / 解锁 / 改价）
 * 设计：营位=酒店房态（固定资源 + 日期房态网格，过夜跨日锁）；营地/活动互斥同槽。
 * ===========================================================================*/

let MODSW=null;            // 经营业态开关缓存
let CAMP_PROFILES=[];      // 营地档案列表
let CAMP_PITCHES=[];       // 营位列表
let CAMP_TAB='profiles';   // 营地经营当前子页
let CAMP_RENTAL=[],CAMP_STORE=[],CAMP_TICKETS=[]; // 营地经营：租赁/小站/活动票缓存

function _localDate(d){const z=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+z(d.getMonth()+1)+'-'+z(d.getDate());}
function _j(s,d){try{return JSON.parse(s)}catch(e){return d||[];}}
function campName(id){const c=(CAMP_PROFILES||[]).find(x=>x.id==id);return c?c.name:'未关联营地';}

/* ===================== 经营业态开关 ===================== */
async function loadModSw(){
  let s;
  try{ s=await api(`/api/club/${CLUB}/module-switches`,{cache:'no-store'}) }
  catch(e){ return; }
  MODSW=Object.assign({home:true,activity:true,camp:false,bizIntro:true},s||{});
  renderModSw();
}
function renderModSw(){
  const box=$('#modswForm'); if(!box) return;
  const c=MODSW||{home:true,activity:true,camp:false,bizIntro:true};
  const row=(key,label,sub)=>`<label class="list-row" style="gap:10px;align-items:center;cursor:pointer">
    <input type="checkbox" data-ms="${key}" ${c[key]?'checked':''} onchange="modSwToggle('${key}',this.checked)">
    <div class="list-row__main"><div class="list-row__title">${label}</div><div class="list-row__sub">${sub}</div></div></label>`;
  box.innerHTML=`
    <div class="grid g2 section">
      ${row('home','首页','关闭后 C 端不显示首页信息流')}
      ${row('activity','活动','俱乐部发活动 / 报名；与「营地」互斥')}
      ${row('camp','营地','场地预订 / 营位房态 / 装备租赁；与「活动」互斥')}
      ${row('bizIntro','业务介绍','C 端「户外能力」板块开关')}
    </div>
    <div style="margin-top:14px;display:flex;gap:10px;align-items:center">
      <button class="btn" type="button" onclick="saveModSw()">保存开关</button>
      <span class="sub" id="modswTip"></span>
    </div>`;
}
window.modSwToggle=function(key,on){
  if(!MODSW)MODSW={home:true,activity:true,camp:false,bizIntro:true};
  MODSW[key]=!!on;
  // 营地与活动共用一个 tab 槽：开启其一自动关闭另一个
  if(key==='camp'&&on){MODSW.activity=false;refreshModSwChecks();toast('已开启营地，活动自动关闭（二者共用一个 tab）');}
  if(key==='activity'&&on){MODSW.camp=false;refreshModSwChecks();toast('已开启活动，营地自动关闭（二者共用一个 tab）');}
};
function refreshModSwChecks(){
  document.querySelectorAll('#modswForm input[data-ms]').forEach(i=>{i.checked=!!MODSW[i.getAttribute('data-ms')];});
}
window.saveModSw=async function(){
  if(!MODSW)return;
  const tip=$('#modswTip'); if(tip)tip.textContent='';
  try{
    const r=await api(`/api/club/${CLUB}/module-switches`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(MODSW)});
    MODSW=Object.assign({home:true,activity:true,camp:false,bizIntro:true},r||{});
    renderModSw();
    if(tip)tip.textContent='已保存 ✓';
    toast('经营业态开关已保存，C 端刷新即生效');
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
};

/* ===================== 营地经营：入口与子页 ===================== */
async function loadCamp(){
  const root=$('#campRoot'); if(!root)return;
  root.innerHTML='<div class="sub">加载中…</div>';
  try{ MODSW=Object.assign({home:true,activity:true,camp:false,bizIntro:true}, await api(`/api/club/${CLUB}/module-switches`,{cache:'no-store'})||{}); }
  catch(e){}
  if(!MODSW||!MODSW.camp){
    root.innerHTML=`<div class="grid section"><div class="card" style="grid-column:1/-1">
      <div class="panel-title"><h3>营地经营</h3><span class="tag">未开启</span></div>
      <div class="notice warn">本板块需在 <b>俱乐部设置 → 经营业态开关</b> 中开启「营地」后才能编辑。开启后这里可用于：营地档案、营位与房态（按日期管理，过夜跨日锁）、营地装备租赁、营地小站、营地活动票。</div>
      <div style="margin-top:12px"><button class="btn" type="button" onclick="go('settings')">前往开启营地开关</button></div>
    </div></div>`;
    return;
  }
  root.innerHTML=`<div class="sub" style="margin:4px 0 10px">营地业态控制台：先建<b>营地档案</b>，再在「营位与房态」里维护营位与每日房态。营位按日期管房态，过夜营位像酒店一样跨日锁定。</div>
    <div class="set-tabs" id="campTabs">
      <button type="button" class="set-tab ${CAMP_TAB==='profiles'?'active':''}" onclick="campTab('profiles')">营地档案</button>
      <button type="button" class="set-tab ${CAMP_TAB==='pitches'?'active':''}" onclick="campTab('pitches')">营位与房态</button>
      <button type="button" class="set-tab ${CAMP_TAB==='rental'?'active':''}" onclick="campTab('rental')">装备租赁</button>
      <button type="button" class="set-tab ${CAMP_TAB==='store'?'active':''}" onclick="campTab('store')">营地小站</button>
      <button type="button" class="set-tab ${CAMP_TAB==='tickets'?'active':''}" onclick="campTab('tickets')">活动票</button>
    </div>
    <div id="campPane"></div>`;
  await campTab(CAMP_TAB);
}
window.campTab=async function(t){
  CAMP_TAB=t;
  document.querySelectorAll('#campTabs .set-tab').forEach(b=>b.classList.toggle('active',b.getAttribute('onclick')===`campTab('${t}')`));
  if(t==='profiles')await loadCampProfiles();
  else if(t==='pitches')await loadCampPitches();
  else if(t==='rental')await loadCampRental();
  else if(t==='store')await loadCampStore();
  else if(t==='tickets')await loadCampTickets();
};

/* ===================== 营地档案 ===================== */
async function loadCampProfiles(){
  const box=$('#campPane'); if(!box)return;
  box.innerHTML='<div class="sub">加载中…</div>';
  let list;
  try{ list=await api(`/api/club/${CLUB}/camps`,{cache:'no-store'}) }
  catch(e){ box.innerHTML='<div class="sub">读取失败：'+(e.message||'')+'</div>'; return; }
  CAMP_PROFILES=list||[];
  const cards=(CAMP_PROFILES.length?CAMP_PROFILES:[]).map(c=>`
    <div class="list-row" style="align-items:flex-start">
      <div class="list-row__main">
        <div class="list-row__title">${esc(c.name||'')} ${c.status==='active'?'':'<span class="tag">已停用</span>'}</div>
        <div class="list-row__sub">${esc(c.address||'未填地址')} · ${((c.facilities||[]).length)?((c.facilities||[]).length+' 项设施'):'暂无设施'} · ${((c.gallery||[])||[]).length?'有图':'无图'}</div>
      </div>
      <div class="list-row__end">
        <button class="btn ghost" type="button" onclick="editCamp(${c.id})">编辑</button>
        <button class="btn ghost" type="button" onclick="deleteCamp(${c.id})">删除</button>
      </div>
    </div>`).join('');
  box.innerHTML=`<div class="card section"><div class="panel-title"><div><h3>营地档案</h3><div class="sub">一个俱乐部可建多个营地（如山顶营、湖边营）。每个营地独立维护营位与房态。</div></div><button class="btn" type="button" onclick="editCamp(0)">＋ 新建营地</button></div>
    <div id="campProfilesList">${cards||'<div class="empty">还没有营地，点「新建营地」开始。</div>'}</div>
    <div id="campProfileForm"></div></div>`;
}
window.editCamp=function(id){
  const c=(id&&CAMP_PROFILES.find(x=>x.id===id))||{name:'',introText:'',gallery:[],facilities:[],rules:[],contactPhone:'',address:'',geoLat:'',geoLng:'',status:'active'};
  const form=$('#campProfileForm'); if(!form)return;
  form.innerHTML=`<div class="card section" style="margin-top:14px"><div class="panel-title"><h3>${id?'编辑营地':'新建营地'}</h3></div>
    <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 18px">
      <label style="display:block"><span class="sub">营地名称 *</span><input id="cpfName" maxlength="40" style="width:100%;margin-top:4px" value="${esc(c.name||'')}" placeholder="例如：云顶星空营地"></label>
      <label style="display:block"><span class="sub">联系电话</span><input id="cpfPhone" maxlength="20" style="width:100%;margin-top:4px" value="${esc(c.contactPhone||'')}" placeholder="138 0000 0000"></label>
      <label style="display:block"><span class="sub">地址</span><input id="cpfAddr" maxlength="120" style="width:100%;margin-top:4px" value="${esc(c.address||'')}" placeholder="成都市××区××路"></label>
      <label style="display:block"><span class="sub">状态</span><select id="cpfStatus" style="width:100%;margin-top:4px"><option value="active" ${c.status!=='inactive'?'selected':''}>启用</option><option value="inactive" ${c.status==='inactive'?'selected':''}>停用</option></select></label>
      <label style="display:block"><span class="sub">纬度 geoLat（可空）</span><input id="cpfLat" style="width:100%;margin-top:4px" value="${esc(c.geoLat==null?'':c.geoLat)}" placeholder="如 30.66"></label>
      <label style="display:block"><span class="sub">经度 geoLng（可空）</span><input id="cpfLng" style="width:100%;margin-top:4px" value="${esc(c.geoLng==null?'':c.geoLng)}" placeholder="如 103.95"></label>
    </div>
    <div style="margin-top:12px"><div class="sub">一句话简介</div><textarea id="cpfIntro" rows="2" maxlength="300" style="width:100%;margin-top:6px" placeholder="营地的特色、适合人群">${esc(c.introText||'')}</textarea></div>
    <div style="margin-top:12px"><div class="sub">设施（每行一项，最多 20 项）</div><textarea id="cpfFac" rows="3" style="width:100%;margin-top:6px" placeholder="水电桩&#10;公共卫生间&#10;篝火区">${((c.facilities||[]).join('\n'))}</textarea></div>
    <div style="margin-top:12px"><div class="sub">规则须知（每行一条，最多 20 条）</div><textarea id="cpfRules" rows="3" style="width:100%;margin-top:6px" placeholder="22:00 后请安静&#10;宠物需牵绳">${((c.rules||[]).join('\n'))}</textarea></div>
    <div style="margin-top:14px;display:flex;gap:10px">
      <button class="btn" type="button" onclick="saveCamp(${id||0})">${id?'保存修改':'创建营地'}</button>
      <button class="btn ghost" type="button" onclick="loadCampProfiles()">取消</button>
      <span class="sub" id="cpfTip"></span>
    </div></div>`;
};
window.saveCamp=async function(id){
  const name=$('#cpfName')?.value.trim(); if(!name){showAlert({title:'请填写营地名称'});return;}
  const payload={
    name, introText:$('#cpfIntro')?.value.trim()||'',
    facilities:($('#cpfFac')?.value||'').split('\n').map(t=>t.trim()).filter(Boolean).slice(0,20),
    rules:($('#cpfRules')?.value||'').split('\n').map(t=>t.trim()).filter(Boolean).slice(0,20),
    contactPhone:$('#cpfPhone')?.value.trim()||'',
    address:$('#cpfAddr')?.value.trim()||'',
    geoLat:($('#cpfLat')?.value.trim()||'')||null,
    geoLng:($('#cpfLng')?.value.trim()||'')||null,
    status:$('#cpfStatus')?.value||'active'
  };
  const tip=$('#cpfTip'); if(tip)tip.textContent='';
  try{
    if(id){ await api(`/api/club/${CLUB}/camps/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); toast('营地已更新'); }
    else { await api(`/api/club/${CLUB}/camps`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); toast('营地已创建'); }
    await loadCampProfiles();
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
};
window.deleteCamp=async function(id){
  const ok=await showConfirm({title:'删除营地',message:'确定删除这个营地？其下的营位、房态覆盖与历史预订会一并清除，且不可恢复。',danger:true});
  if(!ok)return;
  try{ await api(`/api/club/${CLUB}/camps/${id}`,{method:'DELETE'}); toast('已删除'); await loadCampProfiles(); }
  catch(e){showAlert({title:'删除失败',message:e.message||'请重试'})}
};

/* ===================== 营位与房态 ===================== */
async function loadCampPitches(){
  const box=$('#campPane'); if(!box)return;
  box.innerHTML='<div class="sub">加载中…</div>';
  let camps,pitches;
  try{
    camps=await api(`/api/club/${CLUB}/camps`,{cache:'no-store'});
    pitches=await api(`/api/club/${CLUB}/pitches?camp_id=0`,{cache:'no-store'});
  }catch(e){ box.innerHTML='<div class="sub">读取失败：'+(e.message||'')+'</div>'; return; }
  CAMP_PROFILES=camps||[]; CAMP_PITCHES=pitches||[];
  if(!CAMP_PROFILES.length){
    box.innerHTML='<div class="notice warn">请先在「营地档案」里创建一个营地，再回来维护营位。</div>';
    return;
  }
  const rows=(CAMP_PITCHES.length?CAMP_PITCHES:[]).map(p=>{
    const typeName={tent:'帐篷位',rv:'房车位',cabin:'木屋/舱房',day:'日间营位'}[p.pitch_type]||p.pitch_type;
    return `<div class="list-row">
      <div class="list-row__main"><div class="list-row__title">${esc(p.name||'')} <span class="tag">${typeName}</span> ${p.is_overnight?'<span class="tag">过夜</span>':''}</div>
      <div class="list-row__sub">编码 ${esc(p.code||'-')} · 容量 ${p.capacity||1} · 平日 ¥${p.base_price||0}${p.weekend_price!=null?' / 周末 ¥'+p.weekend_price:''} · ${p.status==='active'?'启用':'停用'}</div></div>
      <div class="list-row__end"><button class="btn ghost" type="button" onclick="editPitch(${p.id})">编辑</button><button class="btn ghost" type="button" onclick="deletePitch(${p.id})">删除</button></div>
    </div>`;
  }).join('');
  const campOpts=CAMP_PROFILES.map(c=>`<option value="${c.id}">${esc(c.name||'')}</option>`).join('');
  box.innerHTML=`<div class="card section"><div class="panel-title"><div><h3>营位</h3><div class="sub">营位是<b>固定资源</b>，按日期管房态。日间营位当天结束，过夜营位像酒店一样跨日锁定（如 11/10–11/11 一晚）。</div></div><button class="btn" type="button" onclick="editPitch(0)">＋ 新增营位</button></div>
    <div id="pitchRows">${rows||'<div class="empty">还没有营位，点「新增营位」开始。</div>'}</div>
    <div id="pitchForm"></div></div>
    <div class="card section" style="margin-top:14px"><div class="panel-title"><div><h3>房态日历</h3><div class="sub">选营地 + 日期范围，查看每个营位每天的房态（可订 / 已订 / 已封锁）与当日价；点格子可封锁或改价。</div></div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
        <select id="avCamp" onchange="loadAvailability()">${campOpts}</select>
        <input type="date" id="avStart" onchange="loadAvailability()">
        <span class="sub">至</span>
        <input type="date" id="avEnd" onchange="loadAvailability()">
        <button class="btn secondary" type="button" onclick="loadAvailability()">刷新房态</button>
      </div></div>
    <div id="avBody"><div class="empty">选择日期范围后查看房态。</div></div></div>`;
  const today=new Date();
  const sEl=$('#avStart'),eEl=$('#avEnd');
  if(sEl&&!sEl.value)sEl.value=_localDate(today);
  if(eEl&&!eEl.value){const e=new Date(today);e.setDate(e.getDate()+13);eEl.value=_localDate(e);}
  await loadAvailability();
}
window.editPitch=function(id){
  const p=(id&&CAMP_PITCHES.find(x=>x.id===id))||{camp_id:0,code:'',name:'',pitch_type:'tent',is_overnight:1,capacity:1,base_price:0,weekend_price:null,holiday_price:null,status:'active'};
  const form=$('#pitchForm'); if(!form)return;
  const typeOpt=(v,t)=>`<option value="${v}" ${p.pitch_type===v?'selected':''}>${t}</option>`;
  const campOpt=CAMP_PROFILES.map(c=>`<option value="${c.id}" ${(p.camp_id||0)===c.id?'selected':''}>${esc(c.name||'')}</option>`).join('');
  form.innerHTML=`<div class="card section" style="margin-top:14px"><div class="panel-title"><h3>${id?'编辑营位':'新增营位'}</h3></div>
    <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 18px">
      <label style="display:block"><span class="sub">所属营地 *</span><select id="pfCamp" style="width:100%;margin-top:4px">${campOpt}</select></label>
      <label style="display:block"><span class="sub">营位编码</span><input id="pfCode" maxlength="20" style="width:100%;margin-top:4px" value="${esc(p.code||'')}" placeholder="如 A01"></label>
      <label style="display:block"><span class="sub">营位名称 *</span><input id="pfName" maxlength="40" style="width:100%;margin-top:4px" value="${esc(p.name||'')}" placeholder="如 湖景帐篷位 A01"></label>
      <label style="display:block"><span class="sub">营位类型</span><select id="pfType" style="width:100%;margin-top:4px" onchange="pfTypeChange(this.value)">${typeOpt('tent','帐篷位')}${typeOpt('rv','房车位')}${typeOpt('cabin','木屋/舱房')}${typeOpt('day','日间营位')}</select></label>
      <label style="display:block"><span class="sub">是否过夜</span><select id="pfOvn" style="width:100%;margin-top:4px"><option value="1" ${p.is_overnight?'selected':''}>过夜（跨日锁）</option><option value="0" ${!p.is_overnight?'selected':''}>日间（当天结束）</option></select></label>
      <label style="display:block"><span class="sub">容量（可住人数/帐）</span><input id="pfCap" type="number" min="1" style="width:100%;margin-top:4px" value="${p.capacity||1}"></label>
      <label style="display:block"><span class="sub">平日价（元/天）</span><input id="pfBase" type="number" min="0" step="0.01" style="width:100%;margin-top:4px" value="${p.base_price||0}"></label>
      <label style="display:block"><span class="sub">周末价（可空）</span><input id="pfWeek" type="number" min="0" step="0.01" style="width:100%;margin-top:4px" value="${p.weekend_price==null?'':p.weekend_price}"></label>
      <label style="display:block"><span class="sub">节假日价（可空）</span><input id="pfHol" type="number" min="0" step="0.01" style="width:100%;margin-top:4px" value="${p.holiday_price==null?'':p.holiday_price}"></label>
      <label style="display:block"><span class="sub">状态</span><select id="pfStatus" style="width:100%;margin-top:4px"><option value="active" ${p.status!=='inactive'?'selected':''}>启用</option><option value="inactive" ${p.status==='inactive'?'selected':''}>停用</option></select></label>
    </div>
    <div style="margin-top:14px;display:flex;gap:10px">
      <button class="btn" type="button" onclick="savePitch(${id||0})">${id?'保存修改':'创建营位'}</button>
      <button class="btn ghost" type="button" onclick="loadCampPitches()">取消</button>
      <span class="sub" id="pfTip"></span></div></div>`;
};
window.pfTypeChange=function(v){
  const ovn=$('#pfOvn'); if(!ovn)return;
  ovn.value=(v==='day')?'0':'1';
};
window.savePitch=async function(id){
  const name=$('#pfName')?.value.trim(); if(!name){showAlert({title:'请填写营位名称'});return;}
  const payload={
    campId:Number($('#pfCamp')?.value||0),
    code:$('#pfCode')?.value.trim()||'',
    name,
    pitchType:$('#pfType')?.value||'tent',
    isOvernight:Number($('#pfOvn')?.value||0),
    capacity:Math.max(1,Number($('#pfCap')?.value||1)),
    basePrice:Number($('#pfBase')?.value||0),
    weekendPrice:$('#pfWeek')?.value===''?null:Number($('#pfWeek')?.value||0),
    holidayPrice:$('#pfHol')?.value===''?null:Number($('#pfHol')?.value||0),
    status:$('#pfStatus')?.value||'active'
  };
  const tip=$('#pfTip'); if(tip)tip.textContent='';
  try{
    if(id){ await api(`/api/club/${CLUB}/pitches/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); toast('营位已更新'); }
    else { await api(`/api/club/${CLUB}/pitches`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); toast('营位已创建'); }
    await loadCampPitches();
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
};
window.deletePitch=async function(id){
  const ok=await showConfirm({title:'删除营位',message:'确定删除这个营位？该营位的历史预订与房态覆盖会一并清除。',danger:true});
  if(!ok)return;
  try{ await api(`/api/club/${CLUB}/pitches/${id}`,{method:'DELETE'}); toast('已删除'); await loadCampPitches(); }
  catch(e){showAlert({title:'删除失败',message:e.message||'请重试'})}
};
async function loadAvailability(){
  const box=$('#avBody'); if(!box)return;
  const campId=Number($('#avCamp')?.value||0);
  const start=$('#avStart')?.value, end=$('#avEnd')?.value;
  if(!start||!end){box.innerHTML='<div class="empty">请选择起止日期。</div>';return;}
  box.innerHTML='<div class="sub">加载房态…</div>';
  let data;
  try{ data=await api(`/api/public/clubs/${CLUB}/camp-availability?camp_id=${campId}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,{cache:'no-store'}) }
  catch(e){ box.innerHTML='<div class="sub">读取失败：'+(e.message||'')+'</div>'; return; }
  const pitches=data.pitches||[];
  if(!pitches.length){box.innerHTML='<div class="empty">该营地还没有营位，请先新增营位。</div>';return;}
  const days=(pitches[0].days||[]).map(d=>d.day);
  const cellStyle=st=>st==='booked'?'background:#fbeae6;color:#c8452d' : st==='blocked'?'background:#eceeec;color:#88968f':'background:#eaf3ef;color:#1f7a52';
  const dayHead=days.map(d=>`<th style="padding:4px 6px;font-weight:600;white-space:nowrap">${esc(d.slice(5))}</th>`).join('');
  const body=pitches.map(p=>{
    const cells=(p.days||[]).map(dd=>{
      const st=dd.status;
      return `<td style="text-align:center;cursor:pointer;padding:4px 6px;font-size:12px;${cellStyle(st)}" title="${esc(p.name)} · ${esc(dd.day)} · ${st==='booked'?'已订':st==='blocked'?'已封锁':'可订'} · ¥${dd.price}" onclick="openPitchDay(${p.pitchId},'${dd.day}','${st}')">¥${dd.price}<br>${st==='booked'?'已订':st==='blocked'?'锁':'空'}</td>`;
    }).join('');
    return `<tr><td style="white-space:nowrap;padding:4px 8px;font-weight:600">${esc(p.name||'')}</td>${cells}</tr>`;
  }).join('');
  box.innerHTML=`<div class="sub" style="margin-bottom:8px">绿=可订 · 红=已订 · 灰=已封锁。点任意格子可<b>封锁/解锁</b>或<b>临时改价</b>。</div>
    <div style="overflow:auto"><table class="table" style="border-collapse:collapse;min-width:600px">
      <thead><tr><th style="padding:4px 8px;text-align:left">营位</th>${dayHead}</tr></thead>
      <tbody>${body}</tbody></table></div>`;
}
window.openPitchDay=function(pitchId,day,status){
  let ov=null;
  ov=uxDialog({
    title:'房态操作',
    desc:`营位 #${pitchId} · ${day}（当前：${status==='booked'?'已订（预订产生，不可直接改）':status==='blocked'?'已封锁':'可订'}）`,
    body:`<div class="ux-field"><label for="pdAct">操作</label>
      <select id="pdAct" style="width:100%">
        <option value="blocked">封锁此日（维护 / 满员）</option>
        <option value="available">解锁（恢复可订）</option>
        <option value="price">仅临时改价</option>
      </select></div>
      <div class="ux-field"><label for="pdPrice">临时价（仅「临时改价」时填写，留空=恢复默认）</label><input id="pdPrice" type="number" min="0" step="0.01" style="width:100%" placeholder="覆盖当日价"></div>`,
    foot:`<button class="btn ghost" type="button" data-cancel>取消</button><button class="btn" type="button" data-ok>确定</button>`
  });
  ov.querySelector('[data-cancel]').onclick=()=>ov.uxClose();
  ov.querySelector('[data-ok]').onclick=async()=>{
    const act=ov.querySelector('#pdAct').value;
    const price=ov.querySelector('#pdPrice').value;
    try{
      if(act==='price'){
        await api(`/api/club/${CLUB}/pitch-status`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({campId:Number($('#avCamp')?.value||0),pitchId,day,status:'available',priceOverride:price===''?null:Number(price)})});
      }else{
        await api(`/api/club/${CLUB}/pitch-status`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({campId:Number($('#avCamp')?.value||0),pitchId,day,status:act==='blocked'?'blocked':'clear'})});
      }
      toast('房态已更新'); ov.uxClose(); await loadAvailability();
    }catch(e){showAlert({title:'操作失败',message:e.message||'请重试'})}
  };
};

/* ===================== 装备租赁（camp_rental_items，club 域，与平台商城隔离）===================== */
async function loadCampRental(){
  const box=$('#campPane'); if(!box)return;
  box.innerHTML='<div class="sub">加载中…</div>';
  let list;
  try{ list=await api(`/api/club/${CLUB}/rental-items?camp_id=0`,{cache:'no-store'}) }
  catch(e){ box.innerHTML='<div class="sub">读取失败：'+(e.message||'')+'</div>'; return; }
  CAMP_RENTAL=list||[];
  const cards=(CAMP_RENTAL.length?CAMP_RENTAL:[]).map(r=>{
    const attrs=_j(r.attributes_json,[]);
    return `<div class="list-row">
      <div class="list-row__main"><div class="list-row__title">${esc(r.name||'')} <span class="tag">${campName(r.camp_id)}</span> ${r.status==='active'?'':'<span class="tag">停用</span>'}</div>
      <div class="list-row__sub">日租 ¥${r.daily_price||0} · 押金 ¥${r.deposit||0} · 库存 ${r.available_qty||0}${attrs&&attrs.length?' · '+(attrs.length)+' 项规格':''}</div></div>
      <div class="list-row__end"><button class="btn ghost" type="button" onclick="editRental(${r.id})">编辑</button><button class="btn ghost" type="button" onclick="deleteRental(${r.id})">删除</button></div>
    </div>`;
  }).join('');
  box.innerHTML=`<div class="card section"><div class="panel-title"><div><h3>营地装备租赁</h3><div class="sub">营地自营装备按<b>日租</b>、带<b>押金</b>。与平台商城（公司统一售卖）严格隔离，属于俱乐部/营地域。</div></div><button class="btn" type="button" onclick="editRental(0)">＋ 新增租赁装备</button></div>
    <div id="rentalRows">${cards||'<div class="empty">还没有租赁装备，点「新增」开始。</div>'}</div>
    <div id="rentalForm"></div></div>`;
}
window.editRental=function(id){
  const r=(id&&CAMP_RENTAL.find(x=>x.id===id))||{camp_id:0,name:'',cover_url:'',description:'',daily_price:0,deposit:0,available_qty:0,attributes_json:'',status:'active'};
  const form=$('#rentalForm'); if(!form)return;
  const attrs=_j(r.attributes_json,[]);
  const campOpt=CAMP_PROFILES.map(c=>`<option value="${c.id}" ${(r.camp_id||0)===c.id?'selected':''}>${esc(c.name||'')}</option>`).join('');
  form.innerHTML=`<div class="card section" style="margin-top:14px"><div class="panel-title"><h3>${id?'编辑租赁装备':'新增租赁装备'}</h3></div>
    <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 18px">
      <label style="display:block"><span class="sub">所属营地 *</span><select id="rfCamp" style="width:100%;margin-top:4px">${campOpt}</select></label>
      <label style="display:block"><span class="sub">装备名称 *</span><input id="rfName" maxlength="40" style="width:100%;margin-top:4px" value="${esc(r.name||'')}"></label>
      <label style="display:block"><span class="sub">日租金（元/天）</span><input id="rfDaily" type="number" min="0" step="0.01" style="width:100%;margin-top:4px" value="${r.daily_price||0}"></label>
      <label style="display:block"><span class="sub">押金（元）</span><input id="rfDep" type="number" min="0" step="0.01" style="width:100%;margin-top:4px" value="${r.deposit||0}"></label>
      <label style="display:block"><span class="sub">可租数量</span><input id="rfQty" type="number" min="0" style="width:100%;margin-top:4px" value="${r.available_qty||0}"></label>
      <label style="display:block"><span class="sub">封面图 URL（可空）</span><input id="rfCover" maxlength="300" style="width:100%;margin-top:4px" value="${esc(r.cover_url||'')}"></label>
      <label style="display:block"><span class="sub">状态</span><select id="rfStatus" style="width:100%;margin-top:4px"><option value="active" ${r.status!=='inactive'?'selected':''}>启用</option><option value="inactive" ${r.status==='inactive'?'selected':''}>停用</option></select></label>
    </div>
    <div style="margin-top:12px"><div class="sub">描述</div><textarea id="rfDesc" rows="2" maxlength="300" style="width:100%;margin-top:6px">${esc(r.description||'')}</textarea></div>
    <div style="margin-top:12px"><div class="sub">规格属性（每行一项，可选）</div><textarea id="rfAttr" rows="2" style="width:100%;margin-top:6px" placeholder="双人帐&#10;含防潮垫">${attrs.join('\n')}</textarea></div>
    <div style="margin-top:14px;display:flex;gap:10px">
      <button class="btn" type="button" onclick="saveRental(${id||0})">${id?'保存修改':'创建'}</button>
      <button class="btn ghost" type="button" onclick="loadCampRental()">取消</button>
      <span class="sub" id="rfTip"></span></div></div>`;
};
window.saveRental=async function(id){
  const name=$('#rfName')?.value.trim(); if(!name){showAlert({title:'请填写装备名称'});return;}
  const payload={
    campId:Number($('#rfCamp')?.value||0), name,
    coverUrl:$('#rfCover')?.value.trim()||'',
    description:$('#rfDesc')?.value.trim()||'',
    dailyPrice:Number($('#rfDaily')?.value||0),
    deposit:Number($('#rfDep')?.value||0),
    availableQty:Math.max(0,Number($('#rfQty')?.value||0)),
    attributes:($('#rfAttr')?.value||'').split('\n').map(t=>t.trim()).filter(Boolean).slice(0,20),
    status:$('#rfStatus')?.value||'active'
  };
  const tip=$('#rfTip'); if(tip)tip.textContent='';
  try{
    if(id){ await api(`/api/club/${CLUB}/rental-items/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); toast('租赁装备已更新'); }
    else { await api(`/api/club/${CLUB}/rental-items`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); toast('租赁装备已创建'); }
    await loadCampRental();
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
};
window.deleteRental=async function(id){
  const ok=await showConfirm({title:'删除租赁装备',message:'确定删除该租赁装备？',danger:true});
  if(!ok)return;
  try{ await api(`/api/club/${CLUB}/rental-items/${id}`,{method:'DELETE'}); toast('已删除'); await loadCampRental(); }
  catch(e){showAlert({title:'删除失败',message:e.message||'请重试'})}
};

/* ===================== 营地小站（camp_store_products，club 自营零售，与平台商城隔离）===================== */
async function loadCampStore(){
  const box=$('#campPane'); if(!box)return;
  box.innerHTML='<div class="sub">加载中…</div>';
  let list;
  try{ list=await api(`/api/club/${CLUB}/store-products?camp_id=0`,{cache:'no-store'}) }
  catch(e){ box.innerHTML='<div class="sub">读取失败：'+(e.message||'')+'</div>'; return; }
  CAMP_STORE=list||[];
  const cards=(CAMP_STORE.length?CAMP_STORE:[]).map(s=>`
    <div class="list-row">
      <div class="list-row__main"><div class="list-row__title">${esc(s.name||'')} <span class="tag">${campName(s.camp_id)}</span> ${s.status==='active'?'':'<span class="tag">停用</span>'}</div>
      <div class="list-row__sub">¥${s.price||0} · 库存 ${s.stock||0} · ${esc(s.category||'未分类')}</div></div>
      <div class="list-row__end"><button class="btn ghost" type="button" onclick="editStore(${s.id})">编辑</button><button class="btn ghost" type="button" onclick="deleteStore(${s.id})">删除</button></div>
    </div>`).join('');
  box.innerHTML=`<div class="card section"><div class="panel-title"><div><h3>营地小站</h3><div class="sub">营地自营零售（饮料、食材、配件等），club 域，与平台商城隔离。</div></div><button class="btn" type="button" onclick="editStore(0)">＋ 新增商品</button></div>
    <div id="storeRows">${cards||'<div class="empty">还没有小站商品，点「新增」开始。</div>'}</div>
    <div id="storeForm"></div></div>`;
}
window.editStore=function(id){
  const s=(id&&CAMP_STORE.find(x=>x.id===id))||{camp_id:0,name:'',cover_url:'',price:0,stock:0,category:'',status:'active'};
  const form=$('#storeForm'); if(!form)return;
  const campOpt=CAMP_PROFILES.map(c=>`<option value="${c.id}" ${(s.camp_id||0)===c.id?'selected':''}>${esc(c.name||'')}</option>`).join('');
  form.innerHTML=`<div class="card section" style="margin-top:14px"><div class="panel-title"><h3>${id?'编辑商品':'新增商品'}</h3></div>
    <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 18px">
      <label style="display:block"><span class="sub">所属营地 *</span><select id="sfCamp" style="width:100%;margin-top:4px">${campOpt}</select></label>
      <label style="display:block"><span class="sub">商品名称 *</span><input id="sfName" maxlength="40" style="width:100%;margin-top:4px" value="${esc(s.name||'')}"></label>
      <label style="display:block"><span class="sub">售价（元）</span><input id="sfPrice" type="number" min="0" step="0.01" style="width:100%;margin-top:4px" value="${s.price||0}"></label>
      <label style="display:block"><span class="sub">库存</span><input id="sfStock" type="number" min="0" style="width:100%;margin-top:4px" value="${s.stock||0}"></label>
      <label style="display:block"><span class="sub">分类</span><input id="sfCat" maxlength="20" style="width:100%;margin-top:4px" value="${esc(s.category||'')}" placeholder="饮料 / 食材 / 配件"></label>
      <label style="display:block"><span class="sub">封面图 URL（可空）</span><input id="sfCover" maxlength="300" style="width:100%;margin-top:4px" value="${esc(s.cover_url||'')}"></label>
      <label style="display:block"><span class="sub">状态</span><select id="sfStatus" style="width:100%;margin-top:4px"><option value="active" ${s.status!=='inactive'?'selected':''}>启用</option><option value="inactive" ${s.status==='inactive'?'selected':''}>停用</option></select></label>
    </div>
    <div style="margin-top:14px;display:flex;gap:10px">
      <button class="btn" type="button" onclick="saveStore(${id||0})">${id?'保存修改':'创建'}</button>
      <button class="btn ghost" type="button" onclick="loadCampStore()">取消</button>
      <span class="sub" id="sfTip"></span></div></div>`;
};
window.saveStore=async function(id){
  const name=$('#sfName')?.value.trim(); if(!name){showAlert({title:'请填写商品名称'});return;}
  const payload={
    campId:Number($('#sfCamp')?.value||0), name,
    coverUrl:$('#sfCover')?.value.trim()||'',
    price:Number($('#sfPrice')?.value||0),
    stock:Math.max(0,Number($('#sfStock')?.value||0)),
    category:$('#sfCat')?.value.trim()||'',
    status:$('#sfStatus')?.value||'active'
  };
  const tip=$('#sfTip'); if(tip)tip.textContent='';
  try{
    if(id){ await api(`/api/club/${CLUB}/store-products/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); toast('商品已更新'); }
    else { await api(`/api/club/${CLUB}/store-products`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); toast('商品已创建'); }
    await loadCampStore();
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
};
window.deleteStore=async function(id){
  const ok=await showConfirm({title:'删除商品',message:'确定删除该小站商品？',danger:true});
  if(!ok)return;
  try{ await api(`/api/club/${CLUB}/store-products/${id}`,{method:'DELETE'}); toast('已删除'); await loadCampStore(); }
  catch(e){showAlert({title:'删除失败',message:e.message||'请重试'})}
};

/* ===================== 营地活动票（camp_activity_tickets，电子票+核销）===================== */
async function loadCampTickets(){
  const box=$('#campPane'); if(!box)return;
  box.innerHTML='<div class="sub">加载中…</div>';
  let list;
  try{ list=await api(`/api/club/${CLUB}/tickets?camp_id=0`,{cache:'no-store'}) }
  catch(e){ box.innerHTML='<div class="sub">读取失败：'+(e.message||'')+'</div>'; return; }
  CAMP_TICKETS=list||[];
  const cards=(CAMP_TICKETS.length?CAMP_TICKETS:[]).map(t=>{
    const et={single:'单次入场',multi:'多次入场'}[t.entry_type]||t.entry_type;
    return `<div class="list-row">
      <div class="list-row__main"><div class="list-row__title">${esc(t.title||'')} <span class="tag">${campName(t.camp_id)}</span> <span class="tag">${et}</span> ${t.status==='open'?'':'<span class="tag">已关闭</span>'}</div>
      <div class="list-row__sub">¥${t.price||0} · 活动日 ${esc(t.event_date||'-')} · 已售 ${t.sold||0}/${t.capacity||0}${t.valid_from?' · 有效 '+esc(t.valid_from)+'~'+esc(t.valid_to||''):''}</div></div>
      <div class="list-row__end"><button class="btn ghost" type="button" onclick="editTicket(${t.id})">编辑</button><button class="btn ghost" type="button" onclick="deleteTicket(${t.id})">删除</button></div>
    </div>`;
  }).join('');
  box.innerHTML=`<div class="card section"><div class="panel-title"><div><h3>营地活动票</h3><div class="sub">营地自营活动电子票，支持单次/多次入场，可设活动日与有效期限，售出门票可线下核销。与俱乐部普通活动（场次报名）分离。</div></div><button class="btn" type="button" onclick="editTicket(0)">＋ 新增活动票</button></div>
    <div id="ticketRows">${cards||'<div class="empty">还没有活动票，点「新增」开始。</div>'}</div>
    <div id="ticketForm"></div></div>`;
}
window.editTicket=function(id){
  const t=(id&&CAMP_TICKETS.find(x=>x.id===id))||{camp_id:0,title:'',event_date:'',price:0,capacity:0,entry_type:'single',valid_from:'',valid_to:'',status:'open'};
  const form=$('#ticketForm'); if(!form)return;
  const campOpt=CAMP_PROFILES.map(c=>`<option value="${c.id}" ${(t.camp_id||0)===c.id?'selected':''}>${esc(c.name||'')}</option>`).join('');
  form.innerHTML=`<div class="card section" style="margin-top:14px"><div class="panel-title"><h3>${id?'编辑活动票':'新增活动票'}</h3></div>
    <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 18px">
      <label style="display:block"><span class="sub">所属营地 *</span><select id="tfCamp" style="width:100%;margin-top:4px">${campOpt}</select></label>
      <label style="display:block"><span class="sub">票名 *</span><input id="tfTitle" maxlength="40" style="width:100%;margin-top:4px" value="${esc(t.title||'')}"></label>
      <label style="display:block"><span class="sub">活动日</span><input id="tfDate" type="date" style="width:100%;margin-top:4px" value="${esc(t.event_date||'')}"></label>
      <label style="display:block"><span class="sub">票价（元）</span><input id="tfPrice" type="number" min="0" step="0.01" style="width:100%;margin-top:4px" value="${t.price||0}"></label>
      <label style="display:block"><span class="sub">总票数（0=不限）</span><input id="tfCap" type="number" min="0" style="width:100%;margin-top:4px" value="${t.capacity||0}"></label>
      <label style="display:block"><span class="sub">入场类型</span><select id="tfType" style="width:100%;margin-top:4px"><option value="single" ${t.entry_type!=='multi'?'selected':''}>单次入场</option><option value="multi" ${t.entry_type==='multi'?'selected':''}>多次入场</option></select></label>
      <label style="display:block"><span class="sub">有效起（可空）</span><input id="tfFrom" type="date" style="width:100%;margin-top:4px" value="${esc(t.valid_from||'')}"></label>
      <label style="display:block"><span class="sub">有效止（可空）</span><input id="tfTo" type="date" style="width:100%;margin-top:4px" value="${esc(t.valid_to||'')}"></label>
      <label style="display:block"><span class="sub">状态</span><select id="tfStatus" style="width:100%;margin-top:4px"><option value="open" ${t.status!=='closed'?'selected':''}>售票中</option><option value="closed" ${t.status==='closed'?'selected':''}>已关闭</option></select></label>
    </div>
    <div style="margin-top:14px;display:flex;gap:10px">
      <button class="btn" type="button" onclick="saveTicket(${id||0})">${id?'保存修改':'创建'}</button>
      <button class="btn ghost" type="button" onclick="loadCampTickets()">取消</button>
      <span class="sub" id="tfTip"></span></div></div>`;
};
window.saveTicket=async function(id){
  const title=$('#tfTitle')?.value.trim(); if(!title){showAlert({title:'请填写票名'});return;}
  const payload={
    campId:Number($('#tfCamp')?.value||0), title,
    eventDate:$('#tfDate')?.value||'',
    price:Number($('#tfPrice')?.value||0),
    capacity:Math.max(0,Number($('#tfCap')?.value||0)),
    entryType:$('#tfType')?.value||'single',
    validFrom:$('#tfFrom')?.value||'',
    validTo:$('#tfTo')?.value||'',
    status:$('#tfStatus')?.value||'open'
  };
  const tip=$('#tfTip'); if(tip)tip.textContent='';
  try{
    if(id){ await api(`/api/club/${CLUB}/tickets/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); toast('活动票已更新'); }
    else { await api(`/api/club/${CLUB}/tickets`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); toast('活动票已创建'); }
    await loadCampTickets();
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
};
window.deleteTicket=async function(id){
  const ok=await showConfirm({title:'删除活动票',message:'确定删除该活动票？已售出的记录不受影响。',danger:true});
  if(!ok)return;
  try{ await api(`/api/club/${CLUB}/tickets/${id}`,{method:'DELETE'}); toast('已删除'); await loadCampTickets(); }
  catch(e){showAlert({title:'删除失败',message:e.message||'请重试'})}
};
