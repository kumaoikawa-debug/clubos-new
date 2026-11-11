/* ===========================================================================
 * ClubOS 营地经营 · 俱乐部后台（2026-11-11 v3）
 * 借鉴酒店 PMS 的成熟交互（不引入任何第三方代码）：
 *   Dashboard KPI → tape chart 房态带（营位为行 / 日期为列 / 状态图例 / 今日高亮）
 *   → 弹层表单（不再把长表单塞在列表下面）→ 搜索筛选与空态 → 批量封锁/改价。
 * 样式见 camp-admin.css（ca- 前缀），不修改任何共用 CSS。
 *
 * v3 变更：
 *   - 移除「活动票」栏目（营地活动直接复用原活动模块；后端表与接口保留兼容）
 *   - 新增「营地订单」「会员权益」栏目
 *   - 营位 / 租赁装备 / 小站商品表单补齐 C 端高标准详情页所需的图文字段
 *   - 三类商品统一复用原活动会员体系（等级折扣 / 积分 / 券 / 消费送积分）
 *
 * 后端契约：
 *   GET/PATCH /api/club/{CLUB}/module-switches
 *   GET/PATCH /api/club/{CLUB}/camp-benefits        v3 会员权益开关
 *   GET       /api/club/{CLUB}/camp-orders          v3 统一营地订单
 *   GET/POST/PATCH/DELETE /api/club/{CLUB}/camps[/{id}]
 *   GET/POST/PATCH/DELETE /api/club/{CLUB}/pitches[/{id}]        ?camp_id= 必传真实营地 id
 *   GET/POST /api/club/{CLUB}/pitch-status                       封锁 / 解锁 / 临时改价
 *   GET /api/public/clubs/{CLUB}/camp-availability                ?camp_id&start&end
 *   GET/POST/PATCH/DELETE /api/club/{CLUB}/rental-items|store-products
 *
 * ★ v2 修复：旧版所有后台列表一律传 camp_id=0，而后端是 `WHERE camp_id=?`，
 *   导致关联了真实营地的营位/租赁/小站在后台「建完就消失」。统一按 CA_CAMP_ID 查询。
 * ===========================================================================*/

let MODSW=null;                 // 经营业态开关缓存
let CAMP_PROFILES=[];           // 营地档案
let CAMP_PITCHES=[];            // 当前营地营位
let CAMP_TAB='overview';        // 当前子页
let CAMP_RENTAL=[],CAMP_STORE=[],CAMP_ORDERS=[],CAMP_BENEFITS=null;
let CA_CAMP_ID=0;               // 工作台当前营地（列表与房态共用）
let CA_START='',CA_END='';      // tape chart 日期窗口
let CA_AVAIL=null,CA_BOOKINGS=[];
let CA_SEL=new Set(),CA_MULTI=false,CA_SEARCH='',CA_OV=null,CA_ROWS_FN=null;

/* ── 小工具 ─────────────────────────────────────────────────────────────── */
function _localDate(d){const z=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+z(d.getMonth()+1)+'-'+z(d.getDate());}
function _addDay(s,n){const d=new Date(s+'T00:00:00');d.setDate(d.getDate()+n);return _localDate(d);}
function _today(){return _localDate(new Date());}
function _j(s,d){try{return JSON.parse(s)}catch(e){return d||[];}}
function _money(n){return (Math.round((Number(n)||0)*100)/100).toFixed(2);}
function _campName(id){const c=(CAMP_PROFILES||[]).find(x=>String(x.id)===String(id));return c?c.name:'未关联营地';}
function _typeName(t){return {tent:'帐篷位',rv:'房车位',cabin:'木屋/舱房',day:'日间营位'}[t]||t||'营位';}
function _icon(p){return '<svg viewBox="0 0 24 24" aria-hidden="true">'+p+'</svg>';}
const CA_ICON={
  pitch:_icon('<path d="M12 4 3 20h18z"/><path d="M12 9 7.5 20h9z"/>'),
  check:_icon('<path d="M4 12h16"/><path d="M8 6v12M16 6v12"/><circle cx="12" cy="12" r="9"/>'),
  rate:_icon('<path d="M4 19V9M10 19V5M16 19v-7M22 19H2"/>'),
  clock:_icon('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>')
};
function _campOpts(sel){return (CAMP_PROFILES||[]).map(c=>`<option value="${c.id}" ${String(c.id)===String(sel)?'selected':''}>${esc(c.name||'')}</option>`).join('');}

/* ===================== 俱乐部设置 → 经营业态开关 ===================== */
async function loadModSw(){
  let s;
  try{ s=await api(`/api/club/${CLUB}/module-switches`,{cache:'no-store'}) }
  catch(e){ return; }
  MODSW=Object.assign({home:true,activity:true,camp:false,bizIntro:true},s||{});
  renderModSw();
}
function renderModSw(){
  const box=$('#modswForm'); if(!box)return;
  const c=MODSW||{home:true,activity:true,camp:false,bizIntro:true};
  const sw=(k,t,s)=>`<label class="ca-sw ${c[k]?'on':''}" data-sw="${k}">
    <input type="checkbox" data-ms="${k}" ${c[k]?'checked':''} onchange="modSwToggle('${k}',this.checked)">
    <span class="ca-sw__main"><span class="ca-sw__t">${t}</span><span class="ca-sw__s">${s}</span></span>
    <span class="ca-sw__track"></span></label>`;
  box.innerHTML=`<div class="ca"><div class="ca-sw-grid">
      ${sw('home','首页','关闭后 C 端不显示首页信息流')}
      ${sw('activity','活动经营','俱乐部发布活动 / 报名，与营地互斥')}
      ${sw('camp','营地经营','营位房态 / 场地预订 / 装备租赁，与活动互斥')}
      ${sw('bizIntro','业务介绍','C 端「户外能力」板块')}
    </div>
    <div class="ca-sw__mut">营地与活动共用 C 端同一个导航槽：开启其中一个会自动关闭另一个。切换后 C 端刷新即生效。</div>
    <div style="margin-top:14px;display:flex;gap:10px;align-items:center">
      <button class="ca-btn" type="button" onclick="saveModSw()">保存开关</button>
      <span style="font-size:11.5px;color:#8a9a90" id="modswTip"></span>
    </div></div>`;
}
window.modSwToggle=function(key,on){
  if(!MODSW)MODSW={home:true,activity:true,camp:false,bizIntro:true};
  MODSW[key]=!!on;
  if(key==='camp'&&on){MODSW.activity=false;refreshModSwChecks();toast('已开启营地，活动自动关闭（二者共用一个 tab）');}
  if(key==='activity'&&on){MODSW.camp=false;refreshModSwChecks();toast('已开启活动，营地自动关闭（二者共用一个 tab）');}
  document.querySelectorAll('#modswForm .ca-sw').forEach(l=>l.classList.toggle('on',!!MODSW[l.getAttribute('data-sw')]));
};
function refreshModSwChecks(){
  document.querySelectorAll('#modswForm input[data-ms]').forEach(i=>{i.checked=!!MODSW[i.getAttribute('data-ms')];
    const l=i.closest('.ca-sw');if(l)l.classList.toggle('on',i.checked);});
}
window.saveModSw=async function(){
  if(!MODSW)return;
  const tip=$('#modswTip');if(tip)tip.textContent='';
  try{
    const r=await api(`/api/club/${CLUB}/module-switches`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(MODSW)});
    MODSW=Object.assign({home:true,activity:true,camp:false,bizIntro:true},r||{});
    renderModSw();if(tip)tip.textContent='已保存 ✓';
    toast('经营业态开关已保存，C 端刷新即生效');
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
};

/* ===================== 主入口 ===================== */
async function _loadCamps(){
  try{ CAMP_PROFILES=await api(`/api/club/${CLUB}/camps`,{cache:'no-store'})||[]; }
  catch(e){ CAMP_PROFILES=[]; }
  if(!CAMP_PROFILES.some(c=>String(c.id)===String(CA_CAMP_ID)))CA_CAMP_ID=CAMP_PROFILES[0]?CAMP_PROFILES[0].id:0;
}
async function loadCamp(){
  const root=$('#campRoot');if(!root)return;
  root.innerHTML='<div class="ca"><div class="ca-state" style="padding:40px;text-align:center;color:#8a9a90">加载中…</div></div>';
  try{ MODSW=Object.assign({home:true,activity:true,camp:false,bizIntro:true},await api(`/api/club/${CLUB}/module-switches`,{cache:'no-store'})||{}); }
  catch(e){}
  await _loadCamps();
  if(!MODSW||!MODSW.camp){
    root.innerHTML=`<div class="ca"><div class="ca-card" style="border-left:3px solid #b67816">
      <div class="ca-card__t">营地经营 · 未开启</div>
      <div class="ca-card__s">本板块需在「俱乐部设置 → 经营业态开关」中开启「营地」后才能编辑。开启后可管理：营地档案、营位与按日期的房态（过夜跨日锁）、装备租赁、营地小站，以及统一营地订单与会员权益配置。</div>
      <div style="margin-top:14px"><button class="ca-btn" type="button" onclick="go('settings')">前往开启营地开关 →</button></div>
    </div></div>`;
    return;
  }
  if(!CA_START){CA_START=_today();CA_END=_addDay(CA_START,13);}
  const tabs=[
    ['overview','工作台 · 房态'],['pitches','营位管理'],['profiles','营地档案'],
    ['rental','装备租赁'],['store','营地小站'],
    ['orders','营地订单'],['benefits','会员权益']
  ];
  root.innerHTML=`<div class="ca">
    <div class="ca-bar">
      <div><div class="ca-bar__t">营地经营</div>
        <div class="ca-bar__s">营位是固定资源，按日期管理房态；过夜营位像酒店一样跨日锁定（如 11/10 → 11/11 一晚），日间营当天结束。</div></div>
      <div class="ca-bar__acts">
        <span style="font-size:11.5px;color:#8a9a90">当前营地</span>
        <select class="ca-select" id="caCampSel" onchange="caSetCamp(this.value)" ${CAMP_PROFILES.length?'':'disabled'}>
          ${CAMP_PROFILES.length?_campOpts(CA_CAMP_ID):'<option value="0">请先创建营地</option>'}
        </select>
        <button class="ca-btn ghost" type="button" onclick="caReload()">↻ 刷新</button>
      </div>
    </div>
    <div class="ca-tabs" id="campTabs">${tabs.map(([k,t])=>`<button type="button" class="ca-tab ${CAMP_TAB===k?'active':''}" data-t="${k}" onclick="campTab('${k}')">${t}</button>`).join('')}</div>
    <div class="ca-pane" id="campPane"></div>
  </div>`;
  await campTab(CAMP_TAB);
}
window.campTab=async function(t){
  CAMP_TAB=t;
  document.querySelectorAll('#campTabs .ca-tab').forEach(b=>b.classList.toggle('active',b.getAttribute('data-t')===t));
  CA_SEARCH='';CA_ROWS_FN=null;
  const fn={overview:loadOverview,pitches:loadCampPitches,profiles:loadCampProfiles,
            rental:loadCampRental,store:loadCampStore,
            orders:loadCampOrders,benefits:loadCampBenefits}[t];
  if(fn)await fn();
};
window.caSetCamp=async function(v){CA_CAMP_ID=Number(v)||0;CA_SEL.clear();await campTab(CAMP_TAB);};
window.caReload=async function(){await _loadCamps();await campTab(CAMP_TAB);toast('已刷新');};

/* ===================== 工作台：KPI + tape chart ===================== */
async function _refreshChartData(){
  if(!CA_CAMP_ID){CAMP_PITCHES=[];CA_AVAIL={pitches:[]};CA_BOOKINGS=[];return;}
  const q=`camp_id=${CA_CAMP_ID}`;
  const [p,a,b]=await Promise.all([
    api(`/api/club/${CLUB}/pitches?${q}`,{cache:'no-store'}).catch(()=>[]),
    api(`/api/public/clubs/${CLUB}/camp-availability?${q}&start=${encodeURIComponent(CA_START)}&end=${encodeURIComponent(CA_END)}`,{cache:'no-store'}).catch(()=>({pitches:[]})),
    api(`/api/club/${CLUB}/camp-bookings?${q}`,{cache:'no-store'}).catch(()=>[])
  ]);
  CAMP_PITCHES=p||[];CA_AVAIL=a||{pitches:[]};CA_BOOKINGS=b||[];
}
function _kpiHtml(){
  const today=_today(),active=CAMP_PITCHES.filter(p=>p.status==='active').length;
  const live=b=>['paid','checked_in','pending'].includes(b.status)&&
    (b.is_day_use?b.check_in===today:(b.check_in<=today&&today<b.check_out));
  const arrivals=CA_BOOKINGS.filter(live).length;
  const pending=CA_BOOKINGS.filter(b=>b.status==='pending').length;
  const cells=(CA_AVAIL.pitches||[]).flatMap(p=>(p.days||[]).slice(0,7));
  const booked=cells.filter(d=>d.status==='booked').length;
  const rate=cells.length?Math.round(booked/cells.length*100):0;
  const kpi=(cls,label,val,unit,hint,ico)=>`<div class="ca-kpi ${cls}"><div>
      <div class="ca-kpi__l">${label}</div><div class="ca-kpi__v">${val}${unit?`<small>${unit}</small>`:''}</div>
      <div class="ca-kpi__h">${hint}</div></div><div class="ca-kpi__i">${ico}</div></div>`;
  return `<div class="ca-kpis">
    ${kpi('','在营营位',active,'个','当前营地启用的固定营位',CA_ICON.pitch)}
    ${kpi('cool','今日在营',arrivals,'单','已支付 / 待支付 / 已入住',CA_ICON.check)}
    ${kpi('plain','未来 7 日占用率',rate,'%',`${booked} / ${cells.length} 个营位日`,CA_ICON.rate)}
    ${kpi('warm','待支付',pending,'单','30 分钟后自动释放房态',CA_ICON.clock)}
  </div>`;
}
function _chartHtml(){
  const pitches=CA_AVAIL.pitches||[];
  if(!pitches.length)return `<div class="ca-empty"><b>还没有可展示的房态</b>请先在「营位管理」里为当前营地新增营位。</div>`;
  const days=(pitches[0].days||[]).map(d=>d.day);
  const wk=['日','一','二','三','四','五','六'];
  const today=_today();
  const head=days.map(d=>{
    const dt=new Date(d+'T00:00:00');
    return `<th class="${d===today?'today':''}">${d.slice(5).replace('-','/')}<small>周${wk[dt.getDay()]}</small></th>`;
  }).join('');
  const rows=pitches.map(p=>{
    const cells=(p.days||[]).map(dd=>{
      const st=dd.status==='booked'?'bk':dd.status==='blocked'?'bl':'av';
      const label=st==='bk'?'已订':st==='bl'?'封锁':'可订';
      const key=p.pitchId+'|'+dd.day;
      return `<td><button type="button" class="ca-cell ${st} ${dd.day===today?'today-col':''} ${CA_SEL.has(key)?'sel':''}"
        data-key="${key}" title="${esc(p.name)} · ${dd.day} · ${label} · ¥${dd.price}"
        onclick="caCell(${p.pitchId},'${dd.day}','${dd.status}')">¥${dd.price}<span>${label}</span></button></td>`;
    }).join('');
    return `<tr><td class="ca-rowh">${esc(p.name||'营位')}<small>${esc(p.code||'')} · ${_typeName(p.pitchType)}</small></td>${cells}</tr>`;
  }).join('');
  return `<div class="ca-legend">
      <span><i class="av"></i>可订</span><span><i class="bk"></i>已订（预订自动占用）</span>
      <span><i class="bl"></i>已封锁 / 维护</span><span><i class="tdy"></i>今日列</span>
      <span style="margin-left:auto;color:#8a9a90">点格子可封锁 / 解锁 / 临时改价；开启批量模式可一次改多天</span>
    </div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
      <button class="ca-btn ghost sm" type="button" onclick="caShiftDays(-7)">← 前一周</button>
      <input class="ca-input" type="date" id="caStart" value="${CA_START}" onchange="caSetDates()">
      <span style="color:#8a9a90;font-size:12px">至</span>
      <input class="ca-input" type="date" id="caEnd" value="${CA_END}" onchange="caSetDates()">
      <button class="ca-btn ghost sm" type="button" onclick="caShiftDays(7)">后一周 →</button>
      <button class="ca-btn ghost sm" type="button" onclick="caToday()">回到今天</button>
      <label style="display:flex;align-items:center;gap:6px;font-size:11.5px;color:#4a5b51;cursor:pointer;margin-left:6px">
        <input type="checkbox" id="caMulti" ${CA_MULTI?'checked':''} onchange="caToggleMulti(this.checked)"> 批量选择</label>
    </div>
    <div class="ca-chart-wrap"><table class="ca-chart"><thead><tr><th class="ca-rowh" style="position:sticky;left:0;z-index:4;min-width:132px;text-align:left">营位 / 日期</th>${head}</tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${CA_SEL.size?`<div class="ca-batchbar"><span>已选 ${CA_SEL.size} 个营位日</span><span class="sp"></span>
      <button class="ca-btn sm" type="button" onclick="caBatch('blocked')">批量封锁</button>
      <button class="ca-btn ghost sm" type="button" onclick="caBatch('clear')">批量解锁</button>
      <button class="ca-btn ghost sm" type="button" onclick="caClearSel()">清除选择</button></div>`:''}`;
}
async function loadOverview(){
  const pane=$('#campPane');if(!pane)return;
  pane.innerHTML='<div class="ca-card"><div class="ca-card__s">正在读取房态…</div></div>';
  if(!CAMP_PROFILES.length){
    pane.innerHTML=`<div class="ca-empty"><b>先建一个营地</b>营地是整个工作台的入口：建好后才能维护营位、看房态带、接预订。
      <div style="margin-top:14px"><button class="ca-btn" type="button" onclick="campTab('profiles')">去创建营地 →</button></div></div>`;
    return;
  }
  await _refreshChartData();
  const recent=CA_BOOKINGS.slice(0,5).map(b=>`<div style="display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid #f0f4f1;font-size:12px">
    <span><b>#${b.id}</b> ${esc(b.guest_name||'未填姓名')} · ${esc(b.check_in||'')}${b.check_out&&b.check_out!==b.check_in?' → '+esc(b.check_out):''}</span>
    <span><span class="ca-tag ${b.status==='pending'?'amber':b.status==='cancelled'?'grey':''}">${({pending:'待支付',paid:'已支付',checked_in:'已入住',cancelled:'已取消',completed:'已完成'}[b.status]||b.status)}</span> ¥${_money(b.total)}</span></div>`).join('');
  pane.innerHTML=`${_kpiHtml()}
    <div class="ca-card"><div class="ca-card__head"><div><div class="ca-card__t">房态日历 · tape chart</div>
      <div class="ca-card__s">营位为行、日期为列。预订产生「已订」，手工封锁产生「封锁」；两者都会阻止 C 端下单。价格是当日结算价（含周末价与临时改价）。</div></div></div>
      <div id="caChartBox">${_chartHtml()}</div></div>
    <div class="ca-card"><div class="ca-card__head"><div><div class="ca-card__t">最近预订</div>
      <div class="ca-card__s">当前营地最近 5 条营位预订</div></div>
      <button class="ca-btn ghost sm" type="button" onclick="caReload()">刷新</button></div>
      ${recent||'<div class="ca-card__s" style="padding:16px 0">还没有预订记录。C 端开启营地业态后，用户下单会实时反映到上面的房态带。</div>'}
    </div>`;
}
/* 兼容旧函数名：房态刷新 */
async function loadAvailability(){await loadOverview();}
window.loadAvailability=loadAvailability;

window.caShiftDays=async function(n){CA_START=_addDay(CA_START,n);CA_END=_addDay(CA_END,n);await loadOverview();};
window.caToday=async function(){CA_START=_today();CA_END=_addDay(CA_START,13);await loadOverview();};
window.caSetDates=async function(){
  const s=$('#caStart')?.value,e=$('#caEnd')?.value;
  if(!s||!e||e<s){toast('日期范围不合法');return;}
  CA_START=s;CA_END=e;await loadOverview();
};
window.caToggleMulti=function(on){CA_MULTI=!!on;if(!on)CA_SEL.clear();
  const box=$('#caChartBox');if(box)box.innerHTML=_chartHtml();};
window.caClearSel=function(){CA_SEL.clear();const box=$('#caChartBox');if(box)box.innerHTML=_chartHtml();};
window.caCell=function(pitchId,day,status){
  const key=pitchId+'|'+day;
  if(CA_MULTI){
    if(CA_SEL.has(key))CA_SEL.delete(key);else CA_SEL.add(key);
    const box=$('#caChartBox');if(box)box.innerHTML=_chartHtml();
    return;
  }
  openPitchDay(pitchId,day,status);
};
window.caBatch=async function(act){
  if(!CA_SEL.size){toast('请先选择格子');return;}
  const ok=await showConfirm({title:act==='blocked'?'批量封锁':'批量解锁',
    message:`将对 ${CA_SEL.size} 个「营位 × 日期」执行${act==='blocked'?'封锁':'解锁'}，C 端立即生效。`,danger:act==='blocked'});
  if(!ok)return;
  const items=[...CA_SEL].map(k=>{const [pid,d]=k.split('|');return {pitchId:Number(pid),day:d};});
  let done=0;
  for(let i=0;i<items.length;i+=6){
    await Promise.all(items.slice(i,i+6).map(it=>api(`/api/club/${CLUB}/pitch-status`,{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({campId:CA_CAMP_ID,pitchId:it.pitchId,day:it.day,
        status:act==='blocked'?'blocked':'clear'})}).then(()=>done++).catch(()=>{})));
  }
  CA_SEL.clear();toast(`已更新 ${done} 个格子`);await loadOverview();
};

/* 单格操作弹层（保留原全局名 openPitchDay） */
window.openPitchDay=async function(pitchId,day,status){
  if(status==='booked'){ return _openPitchBooking(pitchId,day); }
  const ov=uxDialog({title:'房态操作',desc:`营位 #${pitchId} · ${day}（当前：${status==='booked'?'已订':status==='blocked'?'已封锁':'可订'}）`,
    body:`<div class="ca"><div class="ca-form">
      <label class="ca-field"><span>操作</span><select id="f_act">
        <option value="blocked">封锁此日（维护 / 满员）</option>
        <option value="available">解锁（恢复可订）</option>
        <option value="price">仅临时改价</option>
      </select></label>
      <label class="ca-field"><span>临时价（元，留空恢复默认）</span><input id="f_price" type="number" min="0" step="0.01" placeholder="覆盖当日价"></label>
      <div class="ca-form__hint">已订格子来自真实预订，不能直接改成可订；如需释放请取消对应订单。临时改价只影响这一天，周末价 / 节假日价规则仍由营位配置决定。</div>
    </div></div>`,
    foot:`<button class="btn ghost" type="button" data-cancel>取消</button><button class="btn" type="button" data-ok>确定</button>`});
  ov.querySelector('[data-cancel]').onclick=()=>ov.uxClose();
  ov.querySelector('[data-ok]').onclick=async()=>{
    const act=ov.querySelector('#f_act').value,price=ov.querySelector('#f_price').value;
    try{
      if(act==='price'){
        if(price===''){toast('请填写临时价，或选择解锁');return;}
        await api(`/api/club/${CLUB}/pitch-status`,{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({campId:CA_CAMP_ID,pitchId,day,status:'available',priceOverride:Number(price)})});
      }else{
        await api(`/api/club/${CLUB}/pitch-status`,{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({campId:CA_CAMP_ID,pitchId,day,status:act==='blocked'?'blocked':'clear'})});
      }
      toast('房态已更新');ov.uxClose();await loadOverview();
    }catch(e){showAlert({title:'操作失败',message:e.message||'请重试'})}
  };
};
/* #800：已订格子点击 → 查看预订详情并可直接走两步履约
   #808 修复「查无此日预订」：不再依赖单独日期边界接口（服务端 check_in<=date<check_out 过滤与房态带口径不一致会漏单），
   改为在已加载的 CA_BOOKINGS（当前营地全量预订）里客户端匹配。 */
function _findPitchBooking(pitchId,day){
  const pid=Number(pitchId),dead=['cancelled','expired'];
  return (CA_BOOKINGS||[]).find(x=>Number(x.pitch_id||x.pitchId)===pid
    && !dead.includes(String(x.status||''))
    && String(x.check_in||'')<=String(day) && String(day)<String(x.check_out||''))||null;
}
async function _openPitchBooking(pitchId,day){
  let b=_findPitchBooking(pitchId,day);
  if(!b){ await _refreshChartData().catch(()=>{}); b=_findPitchBooking(pitchId,day); }
  if(!b){ const ov=uxDialog({title:'房态预订',desc:`营位 #${pitchId} · ${day}`,
      body:`<div class="ca-empty"><b>查无此日预订</b>可能已被取消或数据未同步。</div>`,
      foot:`<button class="btn" type="button" data-ok>知道了</button>`});
    ov.querySelector('[data-ok]').onclick=()=>ov.uxClose(); return; }
  const oid=Number(b.camp_order_id||b.orderId||0);
  if(oid) return caOpenOrderDetail(oid);
  // 无关联订单的历史数据兜底：保留轻量信息窗
  const ov=uxDialog({title:'房态预订详情',desc:`营位 #${pitchId} · ${day}`,
    body:`<div class="ca"><div class="ca-form">
      <div class="ca-bk"><div class="ca-bk__row"><span>预订人</span><b>${esc(b.guest_name||'—')}</b></div>
        <div class="ca-bk__row"><span>联系电话</span><b>${esc(b.guest_phone||'—')}</b></div>
        <div class="ca-bk__row"><span>订单号</span><b>${esc(b.order_no||'—')}</b></div>
        <div class="ca-bk__row"><span>状态</span><b>${esc(CA_ORDER_ST[b.order_status||b.status]||b.order_status||b.status||'')}</b></div>
        <div class="ca-bk__row"><span>合计</span><b>¥${_money(b.total||0)}${Number(b.deposit_amount||b.deposit||0)?'（押金 ¥'+_money(b.deposit_amount||b.deposit||0)+'）':''}</b></div>
        <div class="ca-bk__row"><span>入住 → 离营</span><b>${esc(b.check_in||'')} → ${esc(b.check_out||'')}</b></div></div>
    </div></div>`,
    foot:`<button class="btn ghost" type="button" data-cancel>关闭</button>`});
  ov.querySelector('[data-cancel]').onclick=()=>ov.uxClose();
};

/* ===================== 通用：卡片列表 + 搜索 + 空态 + 弹层表单 ===================== */
function _listCard({title,desc,newFn,newLabel,rowsFn,emptyTitle,emptySub}){
  CA_ROWS_FN=rowsFn;
  return `<div class="ca-card">
    <div class="ca-card__head"><div><div class="ca-card__t">${title}</div><div class="ca-card__s">${desc}</div></div>
      <div class="ca-tools"><div class="ca-search"><input class="ca-input" type="search" placeholder="搜索名称 / 编码" oninput="caSearch(this.value)"></div>
        ${newFn?`<button class="ca-btn" type="button" onclick="${newFn}">＋ ${newLabel||'新建'}</button>`:''}</div></div>
    <div class="ca-list" id="caList">${rowsFn()||`<div class="ca-empty"><b>${emptyTitle||'还没有内容'}</b>${emptySub||''}</div>`}</div>
  </div>`;
}
window.caSearch=function(v){CA_SEARCH=String(v||'').trim().toLowerCase();const b=$('#caList');if(b&&CA_ROWS_FN)b.innerHTML=CA_ROWS_FN()||'<div class="ca-empty"><b>没有匹配项</b>换个关键词试试</div>';};
function _match(txt){return !CA_SEARCH||String(txt||'').toLowerCase().includes(CA_SEARCH);}
function _item(name,tags,meta,editFn,delFn){
  return `<div class="ca-item"><div class="ca-item__top"><div class="ca-item__n">${esc(name)}</div></div>
    <div class="ca-item__tags">${tags}</div><div class="ca-item__meta">${meta}</div>
    <div class="ca-item__foot"><button class="ca-btn ghost sm" type="button" onclick="${editFn}">编辑</button>
      <button class="ca-btn ghost sm" type="button" onclick="${delFn}">删除</button></div></div>`;
}
function _openForm(title,desc,fields,saveFn,okLabel){
  const html=fields.map(f=>{
    const val=f.value==null?'':String(f.value);
    if(f.type==='html'){return '<div class="ca-field full">'+f.html+'</div>';}
    let ctrl;
    if(f.type==='textarea')ctrl=`<textarea id="f_${f.name}" rows="${f.rows||3}" placeholder="${esc(f.placeholder||'')}">${esc(val)}</textarea>`;
    else if(f.type==='select')ctrl=`<select id="f_${f.name}" ${f.onchange?`onchange="${f.onchange}"`:''}>${(f.options||[]).map(o=>`<option value="${esc(o.value)}" ${String(o.value)===val?'selected':''}>${esc(o.label)}</option>`).join('')}</select>`;
    else ctrl=`<input id="f_${f.name}" type="${f.type||'text'}" value="${esc(val)}" placeholder="${esc(f.placeholder||'')}" ${f.min!=null?`min="${f.min}"`:''} ${f.step!=null?`step="${f.step}"`:''}>`;
    return `<label class="ca-field ${f.full?'full':''}"><span>${esc(f.label)}</span>${ctrl}</label>`;
  }).join('');
  const ov=uxDialog({title,desc,wide:true,
    body:`<div class="ca"><div class="ca-form">${html}</div></div>`,
    foot:`<button class="btn ghost" type="button" data-cancel>取消</button><button class="btn" type="button" id="campFormOk" data-ok>${okLabel||'保存'}</button>`,
    onClose:()=>{CA_OV=null;}});
  CA_OV=ov;
  ov.querySelector('[data-cancel]').onclick=()=>ov.uxClose();
  ov.querySelector('[data-ok]').onclick=()=>saveFn(ov);
  return ov;
}
function _fv(ov,n){const e=ov.querySelector('#f_'+n);return e?String(e.value).trim():'';}
function _fn(ov,n){const v=_fv(ov,n);return v===''?null:Number(v);}
function _lines(ov,n){return _fv(ov,n).split('\n').map(t=>t.trim()).filter(Boolean).slice(0,20);}
function _campField(label='所属营地'){return {name:'campId',label,type:'select',value:CA_CAMP_ID,options:CAMP_PROFILES.map(c=>({value:c.id,label:c.name}))};}
/* v3：C 端高标准详情页所需字段，三类商品共用一套录入口径（后台列表返回 snake_case + json 字符串） */
function _detailFields(x){
  const arr=v=>Array.isArray(v)?v:_j(v,[]);
  return [
    {name:'coverUrl',label:'封面图 URL（留空显示营地山景兜底）',value:x.cover_url||'',full:true},
    {name:'gallery',label:'图集 URL（每行一张，详情页可左右滑）',type:'textarea',rows:2,
     value:arr(x.gallery_json).join('\n'),full:true},
    {name:'subtitle',label:'副标题 / 一句话卖点',value:x.subtitle||''},
    {name:'originalPrice',label:'划线原价（元，0=不显示）',type:'number',min:0,step:0.01,value:x.original_price||0},
    {name:'unitLabel',label:'计价单位文案',value:x.unit_label||'',placeholder:'/晚 · /天 · /件'},
    {name:'specs',label:'规格参数（每行 标签=值）',type:'textarea',rows:3,
     value:arr(x.specs_json).map(v=>(v&&typeof v==='object')?((v.label||'')+'='+(v.value||'')):String(v)).join('\n'),full:true},
    {name:'facilities',label:'设施与服务（每行一项）',type:'textarea',rows:2,
     value:arr(x.facilities_json).map(v=>(v&&typeof v==='object')?(v.name||v.title||''):String(v)).join('\n'),full:true},
    {name:'policies',label:'预订须知（每行 标题|说明）',type:'textarea',rows:3,
     value:arr(x.policies_json).map(v=>(v&&typeof v==='object')?((v.title||'')+'|'+(v.text||'')):String(v)).join('\n'),full:true},
    {name:'description',label:'简介 / 列表副文案',type:'textarea',rows:2,value:x.description||'',full:true},
    {name:'detailText',label:'商品图文详情（可分段，每行一段）',type:'textarea',rows:4,value:x.detail_text||'',full:true}
  ];
}
function _detailPayload(ov){
  const specs=_lines(ov,'specs').map(t=>{const i=t.indexOf('=');return i>0?{label:t.slice(0,i).trim(),value:t.slice(i+1).trim()}:{label:'规格',value:t};});
  const pol=_lines(ov,'policies').map(t=>{const i=t.indexOf('|');return i>0?{title:t.slice(0,i).trim(),text:t.slice(i+1).trim()}:{title:t,text:''};});
  return {coverUrl:_fv(ov,'coverUrl'),gallery:_lines(ov,'gallery'),specs:specs,
    facilities:_lines(ov,'facilities'),policies:pol,subtitle:_fv(ov,'subtitle'),
    description:_fv(ov,'description'),detailText:_fv(ov,'detailText'),
    originalPrice:Number(_fv(ov,'originalPrice')||0),unitLabel:_fv(ov,'unitLabel')};
}

/* ===================== 营地 v4：素材工作台（本地上传 ≤5 + 比例 + AI 图文详情） ===================== */
function _campMediaSectionHtml(kind,id){return `<div class="ca-mwb" id="caMwb" data-kind="${kind}" data-id="${id}"></div>`;}
async function _campRenderMwb(kind,id){
  const box=document.getElementById('caMwb'); if(!box)return;
  box.setAttribute('data-kind',kind); box.setAttribute('data-id',id);
  if(!id){ box.innerHTML='<div class="ca-mwb__lock">保存基础信息后即可上传本地照片、用 AI 生成图文详情。</div>'; return; }
  let res={max:5,ratios:['4:3','1:1','16:9'],items:[]};
  try{ res=await api(`/api/club/${CLUB}/camp-media-list?target_kind=${encodeURIComponent(kind)}&target_id=${id}`,{cache:'no-store'})||res; }catch(e){}
  const items=(res.items||[]).map(a=>`<div class="ca-mwb__img ${a.isCover?'on':''}">
      <img src="${esc(a.preview)}" alt="">
      <div class="ca-mwb__ov">${a.isCover?'<span class="ca-tag">封面</span>':`<button type="button" class="ca-btn ghost xs" onclick="caMwbCover(${a.id})">设封面</button>`}
      <button type="button" class="ca-btn ghost xs" onclick="caMwbDel(${a.id})">删除</button></div></div>`).join('');
  const full=(res.items||[]).length>=res.max;
  box.innerHTML=`<div class="ca-mwb__hint">本地照片（最多 ${res.max} 张，服务端按所选比例统一裁剪，保证 C 端视觉一致；首张默认封面，可改设）</div>
    <div class="ca-mwb__bar">
      <label class="ca-mwb__file ${full?'disabled':''}">${full?'已达上限':`<input type="file" id="caMwbFile" accept="image/*">`}</label>
      <select id="caMwbRatio"><option value="4:3">4:3</option><option value="1:1">1:1</option><option value="16:9">16:9</option></select>
      <button type="button" class="ca-btn sm" onclick="caMwbUpload()" ${full?'disabled':''}>上传照片</button>
      <span class="ca-mwb__cnt">${(res.items||[]).length}/${res.max}</span>
    </div>
    <div class="ca-mwb__grid">${items||'<div class="ca-mwb__empty">还没有照片。先上传本地图片（最多 5 张、可调比例），AI 会按图片自动排版。</div>'}</div>
    <div class="ca-mwb__ai">
      <div class="ca-mwb__ail">一个文本框写清照片说明、设施要点、退改与经营规则（也可不上传文件）。点「AI 生成图文详情」，模型自动整理成规格 / 设施 / 须知 / 图文段落，无需手填。</div>
      <textarea id="caMwbPrompt" rows="3" placeholder="例如：湖景木屋，两张床，可住 2 大 1 小；含独立卫浴、空调、充电桩；18:00 后安静；退订需提前 2 天。"></textarea>
      <div class="ca-mwb__aibar"><button type="button" class="ca-btn" id="caMwbGen" onclick="caMwbGenerate()">AI 生成图文详情</button><span id="caMwbGenTip" class="ca-mwb__tip"></span></div>
      <div id="caMwbResult" class="ca-mwb__result"></div>
    </div>`;
}
window.caMwbUpload=async function(){
  const box=document.getElementById('caMwb'); if(!box)return;
  const kind=box.getAttribute('data-kind'), id=Number(box.getAttribute('data-id'));
  const f=document.getElementById('caMwbFile'); if(!f||!f.files||!f.files.length){toast('请先选择本地图片');return;}
  const ratio=document.getElementById('caMwbRatio')?document.getElementById('caMwbRatio').value:'4:3';
  const fd=new FormData(); fd.append('target_kind',kind); fd.append('target_id',id); fd.append('ratio',ratio); fd.append('file',f.files[0]);
  try{
    const r=await fetch(`/api/club/${CLUB}/camp-media`,{method:'POST',body:fd});
    const j=await r.json().catch(()=>({}));
    if(!r.ok){ toast(j.detail||'上传失败'); return; }
    await _campRenderMwb(kind,id); toast('已上传');
  }catch(e){toast('上传失败：'+(e.message||''));}
};
window.caMwbDel=async function(assetId){
  try{ await api(`/api/club/${CLUB}/camp-media/${assetId}`,{method:'DELETE'}); const box=document.getElementById('caMwb'); await _campRenderMwb(box.getAttribute('data-kind'),Number(box.getAttribute('data-id'))); toast('已删除'); }
  catch(e){showAlert({title:'删除失败',message:e.message||''});}
};
window.caMwbCover=async function(assetId){
  const box=document.getElementById('caMwb'); const kind=box.getAttribute('data-kind'), id=Number(box.getAttribute('data-id'));
  try{ await api(`/api/club/${CLUB}/camp-media/${assetId}/cover`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({targetKind:kind,targetId:id})}); await _campRenderMwb(kind,id); toast('已设为封面'); }
  catch(e){showAlert({title:'操作失败',message:e.message||''});}
};
window.caMwbGenerate=async function(){
  const box=document.getElementById('caMwb'); if(!box)return;
  const kind=box.getAttribute('data-kind'), id=Number(box.getAttribute('data-id'));
  if(!id){toast('请先保存基础信息再生成详情');return;}
  const prompt=document.getElementById('caMwbPrompt')?document.getElementById('caMwbPrompt').value:'';
  const gen=document.getElementById('caMwbGen'); if(gen)gen.disabled=true;
  const tip=document.getElementById('caMwbGenTip'); if(tip)tip.textContent='生成中…';
  try{
    const fd=new FormData(); fd.append('target_kind',kind); fd.append('target_id',id); fd.append('prompt',prompt);
    const r=await fetch(`/api/club/${CLUB}/camp-ai/generate`,{method:'POST',body:fd});
    const j=await r.json().catch(()=>({}));
    if(!r.ok){ toast(j.detail||'生成失败'); return; }
    const job=j.jobId; let st=null;
    for(let i=0;i<80;i++){
      await new Promise(r=>setTimeout(r,1500));
      const s=await api(`/api/club/${CLUB}/camp-ai/jobs/${job}`,{cache:'no-store'});
      if(s.status==='done'||s.status==='failed'){st=s;break;}
      if(tip)tip.textContent='生成中…（'+(i+1)+'）';
    }
    if(!st||st.status!=='done'){ toast('生成未完成，请稍后重试'); return; }
    await api(`/api/club/${CLUB}/camp-ai/apply`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({targetKind:kind,targetId:id,jobId:job})});
    const d=st.result||{};
    const res=document.getElementById('caMwbResult');
    if(res)res.innerHTML=`<div class="ca-mwb__ok">已生成图文详情（规格 ${d.specs?d.specs.length:0} · 设施 ${d.facilities?d.facilities.length:0} · 须知 ${d.policies?d.policies.length:0} 条），已写入并可在 C 端查看。</div>
      <div class="ca-mwb__preview"><b>${esc(d.subtitle||'')}</b><p>${esc(d.description||'')}</p>${(d.sections||[]).map(s=>`<div class="ca-mwb__sec"><b>${esc(s.headline||'')}</b><p>${esc(s.body||'')}</p></div>`).join('')}</div>`;
    if(tip)tip.textContent='完成 ✓';
    toast('AI 图文详情已生成并写入');
  }catch(e){toast('生成失败：'+(e.message||''));}
  finally{ if(gen)gen.disabled=false; if(tip&&tip.textContent!=='完成 ✓')tip.textContent=''; }
};
async function _campSaveBasic({kind,idRef,ov,apiPath,build,after}){
  ov=ov||CA_OV; if(!ov)return;
  const name=_fv(ov,'name'); if(!name){toast('请填写名称');return;}
  const btn=document.getElementById('campFormOk'); if(btn){btn.disabled=true;btn.textContent='保存中…';}
  try{
    const body=build(ov);
    if(idRef.v){ await api(apiPath(idRef.v),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); }
    else{ const created=await api(apiPath(0),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); idRef.v=(created&&created.id)?created.id:idRef.v; }
    toast(idRef.v?'已保存':'已创建');
    if(after) await after(idRef.v);
    if(btn)btn.textContent='保存修改';
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'});}
  finally{ if(btn)btn.disabled=false; }
}

/* ===================== 营地档案 ===================== */
async function loadCampProfiles(){
  const box=$('#campPane');if(!box)return;
  await _loadCamps();
  box.innerHTML=_listCard({title:'营地档案',desc:'一个俱乐部可建多个营地（如山顶营、湖边营），顶部下拉切换工作台营地；每个营地独立维护营位与房态。',
    newFn:'editCamp(0)',newLabel:'新建营地',
    rowsFn:()=>CAMP_PROFILES.filter(c=>_match(c.name)||_match(c.address)).map(c=>_item(c.name,
      [c.status==='active'?'<span class="ca-tag">启用中</span>':'<span class="ca-tag grey">已停用</span>',
       (c.facilities||[]).length?`<span class="ca-tag grey">${(c.facilities||[]).length} 项设施</span>`:'',
       (c.gallery||[]).length?'<span class="ca-tag grey">有头图</span>':''].join(''),
      `地址：<b>${esc(c.address||'未填写')}</b><br>电话：${esc(c.contactPhone||'未填写')} · 简介：${esc((c.introText||'').slice(0,40)||'无')}`,
      `editCamp(${c.id})`,`deleteCamp(${c.id})`)).join(''),
    emptyTitle:'还没有营地',emptySub:'点右上角「新建营地」开始，建好后回到工作台就能看到房态带。'});
}
window.editCamp=function(id){
  const c=(id&&CAMP_PROFILES.find(x=>x.id===id))||{name:'',contactPhone:'',address:'',geoLat:'',geoLng:'',status:'active'};
  const idRef={v:id||0};
  _openForm(id?'编辑营地':'新建营地','营地头图与图文详情：先保存基础信息，即可上传本地照片（最多 5 张、可调比例），在一个框里写规则，点「AI 生成图文详情」自动整理。',[
    {name:'name',label:'营地名称 *',value:c.name,placeholder:'例如：云顶星空营地'},
    {name:'contactPhone',label:'联系电话',value:c.contactPhone,placeholder:'138 0000 0000'},
    {name:'address',label:'地址',value:c.address,placeholder:'成都市××区××路',full:true},
    {name:'geoLat',label:'纬度（可空）',value:c.geoLat==null?'':c.geoLat,placeholder:'30.66'},
    {name:'geoLng',label:'经度（可空）',value:c.geoLng==null?'':c.geoLng,placeholder:'103.95'},
    {name:'status',label:'状态',type:'select',value:c.status||'active',options:[{value:'active',label:'启用'},{value:'inactive',label:'停用'}]},
    {name:'__mwb',label:'',type:'html',html:_campMediaSectionHtml('profile',id||0)}
  ],ov=>_campSaveBasic({kind:'profile',idRef,ov,
    apiPath:cur=>cur?`/api/club/${CLUB}/camps/${cur}`:`/api/club/${CLUB}/camps`,
    build:ov=>({name:_fv(ov,'name'),contactPhone:_fv(ov,'contactPhone'),address:_fv(ov,'address'),
      geoLat:_fn(ov,'geoLat'),geoLng:_fn(ov,'geoLng'),status:_fv(ov,'status')||'active'}),
    after:cur=>_campRenderMwb('profile',cur)
  }),'保存基础信息');
  _campRenderMwb('profile',id||0);
};
window.saveCamp=function(){};
window.deleteCamp=async function(id){
  const ok=await showConfirm({title:'删除营地',message:'确定删除这个营地？其下的营位、房态覆盖与历史预订会一并清除，且不可恢复。',danger:true});
  if(!ok)return;
  try{await api(`/api/club/${CLUB}/camps/${id}`,{method:'DELETE'});toast('已删除');await _loadCamps();await campTab('profiles');}
  catch(e){showAlert({title:'删除失败',message:e.message||'请重试'})}
};

/* ===================== 营位管理 ===================== */
async function loadCampPitches(){
  const box=$('#campPane');if(!box)return;
  if(!CA_CAMP_ID){box.innerHTML='<div class="ca-empty"><b>请先创建营地</b>营位必须归属某个营地。<div style="margin-top:12px"><button class="ca-btn" type="button" onclick="campTab(\'profiles\')">去创建营地 →</button></div></div>';return;}
  try{CAMP_PITCHES=await api(`/api/club/${CLUB}/pitches?camp_id=${CA_CAMP_ID}`,{cache:'no-store'})||[];}
  catch(e){box.innerHTML=`<div class="ca-empty">读取失败：${esc(e.message||'')}</div>`;return;}
  box.innerHTML=_listCard({title:`营位管理 · ${esc(_campName(CA_CAMP_ID))}`,
    desc:'营位是固定资源，按日期管房态。日间营当天结束；过夜营位像酒店一样跨日锁定。切换顶部「当前营地」查看其它营地。',
    newFn:'editPitch(0)',newLabel:'新增营位',
    rowsFn:()=>CAMP_PITCHES.filter(p=>_match(p.name)||_match(p.code)).map(p=>_item(p.name,
      [`<span class="ca-tag">${_typeName(p.pitch_type)}</span>`,p.is_overnight?'<span class="ca-tag grey">过夜 · 跨日锁</span>':'<span class="ca-tag grey">日间当天</span>',
       p.status==='active'?'':'<span class="ca-tag red">已停用</span>'].join(''),
      `编码 <b>${esc(p.code||'-')}</b> · 适用 ${p.capacity||1} 人<br>平日 ¥${_money(p.base_price)} / 天${p.weekend_price!=null?' · 周末 ¥'+_money(p.weekend_price):''}${p.holiday_price!=null?' · 节假日 ¥'+_money(p.holiday_price):''}`,
      `editPitch(${p.id})`,`deletePitch(${p.id})`)).join(''),
    emptyTitle:'这个营地还没有营位',emptySub:'点「新增营位」建第一个营位，之后去「工作台 · 房态」就能看到 tape chart。'});
}
window.editPitch=function(id){
  const p=(id&&CAMP_PITCHES.find(x=>x.id===id))||{camp_id:CA_CAMP_ID,code:'',name:'',pitch_type:'tent',is_overnight:1,capacity:1,base_price:0,weekend_price:null,holiday_price:null,status:'active'};
  const idRef={v:id||0};
  _openForm(id?'编辑营位':'新增营位','先保存基础信息，即可上传本地照片（最多 5 张、按所选比例统一裁剪）并在一个框里写规则，点「AI 生成图文详情」自动整理规格 / 设施 / 须知 / 图文，无需手填。',[
    _campField(),{name:'code',label:'营位编码',value:p.code,placeholder:'如 A01'},
    {name:'name',label:'营位名称 *',value:p.name,placeholder:'如 湖景帐篷位 A01'},
    {name:'pitchType',label:'营位类型',type:'select',value:p.pitch_type||'tent',onchange:"pfTypeChange(this.value)",
      options:[{value:'tent',label:'帐篷位'},{value:'rv',label:'房车位'},{value:'cabin',label:'木屋/舱房'},{value:'day',label:'日间营位'}]},
    {name:'isOvernight',label:'住宿语义',type:'select',value:String(p.is_overnight?1:0),
      options:[{value:'1',label:'过夜（跨日锁）'},{value:'0',label:'日间（当天结束）'}]},
    {name:'capacity',label:'适用人数',type:'number',min:1,value:p.capacity||1},
    {name:'basePrice',label:'平日价（元/天）',type:'number',min:0,step:0.01,value:p.base_price||0},
    {name:'weekendPrice',label:'周末价（留空=同平日）',type:'number',min:0,step:0.01,value:p.weekend_price==null?'':p.weekend_price},
    {name:'holidayPrice',label:'节假日价（留空=同平日）',type:'number',min:0,step:0.01,value:p.holiday_price==null?'':p.holiday_price},
    {name:'status',label:'状态',type:'select',value:p.status||'active',options:[{value:'active',label:'启用'},{value:'inactive',label:'停用'}]},
    {name:'__mwb',label:'',type:'html',html:_campMediaSectionHtml('pitch',id||0)}
  ],ov=>_campSaveBasic({kind:'pitch',idRef,ov,
    apiPath:cur=>cur?`/api/club/${CLUB}/pitches/${cur}`:`/api/club/${CLUB}/pitches`,
    build:ov=>({campId:Number(_fv(ov,'campId'))||CA_CAMP_ID,code:_fv(ov,'code'),name:_fv(ov,'name'),
      pitchType:_fv(ov,'pitchType')||'tent',isOvernight:Number(_fv(ov,'isOvernight')||0),
      capacity:Math.max(1,Number(_fv(ov,'capacity')||1)),basePrice:Number(_fv(ov,'basePrice')||0),
      weekendPrice:_fn(ov,'weekendPrice'),holidayPrice:_fn(ov,'holidayPrice'),status:_fv(ov,'status')||'active'}),
    after:cur=>_campRenderMwb('pitch',cur)
  }),'保存基础信息');
  _campRenderMwb('pitch',id||0);
};
window.pfTypeChange=function(v){const el=CA_OV&&CA_OV.querySelector('#f_isOvernight');if(el)el.value=(v==='day')?'0':'1';};
window.savePitch=function(){};
window.deletePitch=async function(id){
  const ok=await showConfirm({title:'删除营位',message:'确定删除这个营位？该营位的历史预订与房态覆盖会一并清除。',danger:true});
  if(!ok)return;
  try{await api(`/api/club/${CLUB}/pitches/${id}`,{method:'DELETE'});toast('已删除');await campTab('pitches');}
  catch(e){showAlert({title:'删除失败',message:e.message||'请重试'})}
};

/* ===================== 装备租赁（club 域，与平台商城隔离） ===================== */
async function loadCampRental(){
  const box=$('#campPane');if(!box)return;
  if(!CA_CAMP_ID){box.innerHTML='<div class="ca-empty"><b>请先创建营地</b>租赁装备需归属具体营地。</div>';return;}
  try{CAMP_RENTAL=await api(`/api/club/${CLUB}/rental-items?camp_id=${CA_CAMP_ID}`,{cache:'no-store'})||[];}
  catch(e){box.innerHTML=`<div class="ca-empty">读取失败：${esc(e.message||'')}</div>`;return;}
  box.innerHTML=_listCard({title:`装备租赁 · ${esc(_campName(CA_CAMP_ID))}`,
    desc:'营地自营装备按日租、带押金，属于俱乐部/营地域；与总平台公司的装备售卖（products / gear_orders）物理隔离。',
    newFn:'editRental(0)',newLabel:'新增装备',
    rowsFn:()=>CAMP_RENTAL.filter(r=>_match(r.name)).map(r=>_item(r.name,
      [`<span class="ca-tag">${esc(_campName(r.camp_id))}</span>`,r.status==='active'?'':'<span class="ca-tag grey">已停用</span>'].join(''),
      `日租 <b>¥${_money(r.daily_price)}</b> · 押金 ¥${_money(r.deposit)} · 可租 ${r.available_qty||0} 件<br>${esc((r.description||'').slice(0,48)||'暂无描述')}`,
      `editRental(${r.id})`,`deleteRental(${r.id})`)).join(''),
    emptyTitle:'还没有租赁装备',emptySub:'帐篷、天幕、防潮垫等按日出租的装备都放这里。'});
}
window.editRental=function(id){
  const r=(id&&CAMP_RENTAL.find(x=>x.id===id))||{camp_id:CA_CAMP_ID,name:'',daily_price:0,deposit:0,available_qty:0,status:'active'};
  const idRef={v:id||0};
  _openForm(id?'编辑租赁装备':'新增租赁装备','押金离营时退还；先保存基础信息，即可上传照片、用 AI 生成图文详情。',[
    _campField(),{name:'name',label:'装备名称 *',value:r.name},
    {name:'dailyPrice',label:'日租金（元/天）',type:'number',min:0,step:0.01,value:r.daily_price||0},
    {name:'deposit',label:'押金（元）',type:'number',min:0,step:0.01,value:r.deposit||0},
    {name:'availableQty',label:'可租数量',type:'number',min:0,value:r.available_qty||0},
    {name:'status',label:'状态',type:'select',value:r.status||'active',options:[{value:'active',label:'启用'},{value:'inactive',label:'停用'}]},
    {name:'__mwb',label:'',type:'html',html:_campMediaSectionHtml('rental',id||0)}
  ],ov=>_campSaveBasic({kind:'rental',idRef,ov,
    apiPath:cur=>cur?`/api/club/${CLUB}/rental-items/${cur}`:`/api/club/${CLUB}/rental-items`,
    build:ov=>({campId:Number(_fv(ov,'campId'))||CA_CAMP_ID,name:_fv(ov,'name'),
      dailyPrice:Number(_fv(ov,'dailyPrice')||0),deposit:Number(_fv(ov,'deposit')||0),
      availableQty:Math.max(0,Number(_fv(ov,'availableQty')||0)),status:_fv(ov,'status')||'active'}),
    after:cur=>_campRenderMwb('rental',cur)
  }),'保存基础信息');
  _campRenderMwb('rental',id||0);
};
window.saveRental=function(){};
window.deleteRental=async function(id){
  const ok=await showConfirm({title:'删除租赁装备',message:'确定删除该租赁装备？',danger:true});if(!ok)return;
  try{await api(`/api/club/${CLUB}/rental-items/${id}`,{method:'DELETE'});toast('已删除');await campTab('rental');}
  catch(e){showAlert({title:'删除失败',message:e.message||'请重试'})}
};

/* ===================== 营地小站（club 自营零售） ===================== */
async function loadCampStore(){
  const box=$('#campPane');if(!box)return;
  if(!CA_CAMP_ID){box.innerHTML='<div class="ca-empty"><b>请先创建营地</b>小站商品需归属具体营地。</div>';return;}
  try{CAMP_STORE=await api(`/api/club/${CLUB}/store-products?camp_id=${CA_CAMP_ID}`,{cache:'no-store'})||[];}
  catch(e){box.innerHTML=`<div class="ca-empty">读取失败：${esc(e.message||'')}</div>`;return;}
  box.innerHTML=_listCard({title:`营地小站 · ${esc(_campName(CA_CAMP_ID))}`,
    desc:'营地自营零售（饮料、食材、配件等），属于 club 域，与平台商城的装备售卖隔离。',
    newFn:'editStore(0)',newLabel:'新增商品',
    rowsFn:()=>CAMP_STORE.filter(s=>_match(s.name)||_match(s.category)).map(s=>_item(s.name,
      [`<span class="ca-tag">${esc(_campName(s.camp_id))}</span>`,`<span class="ca-tag grey">${esc(s.category||'未分类')}</span>`,
       s.status==='active'?'':'<span class="ca-tag red">已停用</span>'].join(''),
      `售价 <b>¥${_money(s.price)}</b> · 库存 ${s.stock||0} 件`,
      `editStore(${s.id})`,`deleteStore(${s.id})`)).join(''),
    emptyTitle:'还没有小站商品',emptySub:'饮料、烧烤食材、一次性用品等现场零售商品放这里。'});
}
window.editStore=function(id){
  const s=(id&&CAMP_STORE.find(x=>x.id===id))||{camp_id:CA_CAMP_ID,name:'',price:0,stock:0,category:'',status:'active'};
  const idRef={v:id||0};
  _openForm(id?'编辑小站商品':'新增小站商品','小站商品按件售卖，不计入房态。先保存基础信息，即可上传照片、用 AI 生成图文详情。',[
    _campField(),{name:'name',label:'商品名称 *',value:s.name},
    {name:'price',label:'售价（元）',type:'number',min:0,step:0.01,value:s.price||0},
    {name:'stock',label:'库存（件）',type:'number',min:0,value:s.stock||0},
    {name:'category',label:'分类',value:s.category,placeholder:'饮料 / 食材 / 配件'},
    {name:'status',label:'状态',type:'select',value:s.status||'active',options:[{value:'active',label:'启用'},{value:'inactive',label:'停用'}]},
    {name:'__mwb',label:'',type:'html',html:_campMediaSectionHtml('store',id||0)}
  ],ov=>_campSaveBasic({kind:'store',idRef,ov,
    apiPath:cur=>cur?`/api/club/${CLUB}/store-products/${cur}`:`/api/club/${CLUB}/store-products`,
    build:ov=>({campId:Number(_fv(ov,'campId'))||CA_CAMP_ID,name:_fv(ov,'name'),
      price:Number(_fv(ov,'price')||0),stock:Math.max(0,Number(_fv(ov,'stock')||0)),
      category:_fv(ov,'category'),status:_fv(ov,'status')||'active'}),
    after:cur=>_campRenderMwb('store',cur)
  }),'保存基础信息');
  _campRenderMwb('store',id||0);
};
window.saveStore=function(){};
window.deleteStore=async function(id){
  const ok=await showConfirm({title:'删除商品',message:'确定删除该小站商品？',danger:true});if(!ok)return;
  try{await api(`/api/club/${CLUB}/store-products/${id}`,{method:'DELETE'});toast('已删除');await campTab('store');}
  catch(e){showAlert({title:'删除失败',message:e.message||'请重试'})}
};

/* ===================== 营地订单（v3 统一订单；#800 增加分类筛选 + 两步履约） ===================== */
const CA_ORDER_ST={pending_payment:'待支付',paid:'已支付',checked_in:'已入住',completed:'已完成',cancelled:'已取消',expired:'已超时'};
const CA_ORDER_CAT=[['all','全部'],['stay','营位'],['rental','租赁'],['store','其它销售']];
let CA_ORDER_CAT_CUR='all';
function _orderCats(o){
  const ks=new Set((o.items||[]).map(i=>i.kind));
  const out=[];if(ks.has('stay'))out.push('营位');if(ks.has('rental'))out.push('租赁');if(ks.has('store'))out.push('其它销售');
  return out;
}
function _orderMatch(o){
  if(CA_ORDER_CAT_CUR!=='all'){const map={stay:'营位',rental:'租赁',store:'其它销售'};if(!_orderCats(o).includes(map[CA_ORDER_CAT_CUR]))return false;}
  if(CA_SEARCH){const s=CA_SEARCH,t=String(o.orderNo||'').toLowerCase()+' '+String(o.guestName||'').toLowerCase()+' '+String(o.guestPhone||'').toLowerCase();if(!t.includes(s))return false;}
  return true;
}
function _orderActions(o){
  const st=o.status||'';
  const det=`<button class="ca-btn ghost sm" type="button" onclick="caOpenOrderDetail(${o.id})">详情</button>`;
  if(st==='paid')return det+`<button class="ca-btn primary sm" type="button" onclick="caCampCheckin(${o.id})">确认入住 · 登记押金</button>`;
  if(st==='checked_in')return det+`<button class="ca-btn primary sm" type="button" onclick="caCampCheckout(${o.id})">办理退房 · 退押金结算</button>`+
    (Number(o.depositHeld||o.deposit_held||0)?`<span class="ca-item__dep">押金 ¥${_money(o.depositHeld||o.deposit_held)} 已登记</span>`:'');
  if(st==='completed')return det+`<span class="ca-item__dep">押金已退 ¥${_money(o.depositRefunded||o.deposit_refunded||0)}${Number(o.depositForfeit||o.deposit_forfeit||0)?'（扣减 ¥'+_money(o.depositForfeit||o.deposit_forfeit)+'）':''}</span>`;
  return det;
}
function caOrderRows(){
  const rows=CAMP_ORDERS.filter(_orderMatch).map(o=>{
    const st=o.status||'',items=(o.items||[]).map(x=>`${esc(x.title||'')} ×${x.qty}`).join('、');
    const cats=_orderCats(o).map(c=>`<span class="ca-tag soft">${c}</span>`).join('');
    return `<div class="ca-item"><div class="ca-item__top"><div class="ca-item__n">${esc(o.orderNo||'')}</div>
      <span class="ca-tag ${st==='paid'?'':st==='pending_payment'?'amber':(st==='checked_in'||st==='completed')?'green':'grey'}">${esc(CA_ORDER_ST[st]||st)}</span></div>
      ${cats?`<div class="ca-item__cats">${cats}</div>`:''}
      <div class="ca-item__meta" style="margin-top:8px">
        ${esc(o.createdAt||'')} · 联系人 ${esc(o.guestName||'')} ${esc(o.guestPhone||'')}<br>
        ${items}<br>
        商品 ¥${_money(o.goodsAmount)}${Number(o.memberDiscount)?' − 会员 ¥'+_money(o.memberDiscount):''}${Number(o.clubPointDiscount)?' − 积分 ¥'+_money(o.clubPointDiscount):''}${Number(o.voucherDiscount)?' − 券 ¥'+_money(o.voucherDiscount):''}${Number(o.depositAmount)?' + 押金 ¥'+_money(o.depositAmount):''}
        · <b>合计 ¥${_money(o.total)}</b> · 送 ${Number(o.pointsEarned||0)} 积分
      </div>
      <div class="ca-item__foot">${_orderActions(o)}</div></div>`;
  }).join('');
  return rows||'<div class="ca-empty"><b>没有匹配的营地订单</b>调整筛选条件，或 C 端下单后会显示在这里。</div>';
}
async function loadCampOrders(){
  const box=$('#campPane');if(!box)return;
  try{const d=await api(`/api/club/${CLUB}/camp-orders`,{cache:'no-store'})||{};CAMP_ORDERS=d.orders||[];}
  catch(e){box.innerHTML=`<div class="ca-empty">读取失败：${esc(e.message||'')}</div>`;return;}
  const chips=CA_ORDER_CAT.map(([k,t])=>`<button class="ca-chip ${CA_ORDER_CAT_CUR===k?'on':''}" type="button" onclick="caSetOrderCat('${k}')">${t}</button>`).join('');
  box.innerHTML=`<div class="ca-card"><div class="ca-card__head"><div>
      <div class="ca-card__t">营地订单 · 共 ${CAMP_ORDERS.length} 笔</div>
      <div class="ca-card__s">营位 / 租赁装备 / 小站商品统一进一张订单，金额与会员权益由系统重算。待支付订单 30 分钟未付款会自动释放房态与库存。按「营位 / 租赁 / 其它销售」筛选，已支付订单可确认入住（登记押金），已入住可办理退房（退押金并结算）。</div></div>
      <div class="ca-tools"><div class="ca-search"><input class="ca-input" type="search" placeholder="搜索订单号 / 联系人" oninput="caSearch(this.value)"></div>
      <button class="ca-btn ghost sm" type="button" onclick="caReload()">↻ 刷新</button></div></div>
    <div class="ca-chips">${chips}</div>
    <div class="ca-list" id="caList">${caOrderRows()}</div></div>`;
  CA_ROWS_FN=caOrderRows;
}
window.caSetOrderCat=function(k){CA_ORDER_CAT_CUR=k;const b=$('#caList');if(b&&CA_ROWS_FN)b.innerHTML=CA_ROWS_FN();
  document.querySelectorAll('.ca-chips .ca-chip').forEach(c=>c.classList.toggle('on',c.textContent===({all:'全部',stay:'营位',rental:'租赁',store:'其它销售'})[k]));};
/* #808：酒店化订单详情抽屉 + 入住（押金可调）/ 退房（损坏扣减）对话式履约 */
async function _campOrderFetch(id){
  try{const d=await api(`/api/club/${CLUB}/camp-orders/${id}`,{cache:'no-store'});return (d&&d.order)||null;}
  catch(e){showAlert({title:'读取订单失败',message:e.message||'请重试'});return null;}
}
async function _campAfterFulfil(){
  try{await campTab(CAMP_TAB);}catch(e){}
}
window.caOpenOrderDetail=async function(id){
  const o=await _campOrderFetch(id);if(!o)return;
  const st=o.status||'';
  const sec=(t,inner)=>`<div style="margin-top:14px"><div style="font-size:12px;color:#8a9a90;margin-bottom:6px">${t}</div>${inner}</div>`;
  const row=(k,v)=>`<div class="ca-bk__row"><span>${k}</span><b>${v}</b></div>`;
  const KIND_CN={stay:'营位',rental:'租赁',store:'小站'};
  const itemsHtml=(o.items||[]).map(it=>`<div class="ca-bk__row"><span>${esc((KIND_CN[it.kind]||it.kind)+' · '+(it.title||''))}
      <small style="display:block;color:#8a9a90">${esc(it.spec||'')}${it.checkIn?` ${esc(it.checkIn)} → ${esc(it.checkOut||it.checkIn)}${it.isDayUse?'（日间）':''}`:''}</small></span>
      <b>¥${_money(it.subtotal)}</b></div>`).join('');
  const payHtml=row('商品合计',`¥${_money(o.goodsAmount)}`)
    +(Number(o.memberDiscount)?row('会员优惠',`− ¥${_money(o.memberDiscount)}`):'')
    +(Number(o.clubPointDiscount)?row('积分抵扣',`− ¥${_money(o.clubPointDiscount)}`):'')
    +(Number(o.gearPointDiscount)?row('装备积分',`− ¥${_money(o.gearPointDiscount)}`):'')
    +(Number(o.voucherDiscount)?row('优惠券',`− ¥${_money(o.voucherDiscount)}`):'')
    +row('实付合计',`<b style="color:#1d7a4f">¥${_money(o.total)}</b>`)
    +(Number(o.pointsEarned)?row('送积分',`${Number(o.pointsEarned)} 分`):'');
  let depHtml;
  if(st==='paid')depHtml=row('押金（入住时登记，可现场调整）',`¥${_money(o.depositAmount)}`);
  else if(st==='checked_in')depHtml=row('押金已登记',`¥${_money(o.depositHeld)}`);
  else if(st==='completed')depHtml=row('押金结算',`扣减 ¥${_money(o.depositForfeit)} · 退还 ¥${_money(o.depositRefunded)}`);
  else depHtml=row('押金',`¥${_money(o.depositAmount)}`);
  const logHtml=row('下单',esc(o.createdAt||'—'))
    +(o.paidAt?row('支付',esc(o.paidAt)):'')
    +(o.checkedInAt?row('入住',esc(o.checkedInAt)):'')
    +(o.checkedOutAt?row('退房',esc(o.checkedOutAt)):'')
    +(o.checkinNote?row('入住备注',esc(o.checkinNote)):'')
    +(o.checkoutNote?row('退房备注',esc(o.checkoutNote)):'');
  const act= st==='paid'
    ? `<button class="ca-btn primary sm" type="button" onclick="this.closest('.ux-overlay').uxClose();caCampCheckin(${o.id})">确认入住 · 登记押金</button>`
    : st==='checked_in'
    ? `<button class="ca-btn primary sm" type="button" onclick="this.closest('.ux-overlay').uxClose();caCampCheckout(${o.id})">办理退房 · 退押金结算</button>`:'';
  const ov=uxDialog({title:'营地订单详情',desc:`${esc(o.orderNo||'')} · ${esc(CA_ORDER_ST[st]||st)}`,
    body:`<div class="ca">
      ${sec('顾客信息',row('姓名',esc(o.guestName||'—'))+row('电话',esc(o.guestPhone||'—'))+row('订单号',esc(o.orderNo||'—')))}
      ${sec('预订明细',(o.items||[]).length?itemsHtml:'<div class="ca-card__s">无明细</div>')}
      ${sec('费用',payHtml)}
      ${sec('押金',depHtml)}
      ${sec('履约记录',logHtml)}
    </div>`,
    foot:`<button class="btn ghost" type="button" data-cancel>关闭</button>${act}`});
  ov.querySelector('[data-cancel]').onclick=()=>ov.uxClose();
};
window.caCampCheckin=async function(id){
  const o=await _campOrderFetch(id);if(!o)return;
  if(o.status!=='paid'){showAlert({title:'无法入住',message:'仅「已支付」订单可办理入住'});return;}
  const ov=uxDialog({title:'确认入住 · 登记押金',desc:`${esc(o.orderNo||'')} · ${esc(o.guestName||'')}`,
    body:`<div class="ca"><div class="ca-form">
      <div class="ca-bk"><div class="ca-bk__row"><span>订单押金</span><b>¥${_money(o.depositAmount)}</b></div></div>
      <label class="ca-field"><span>实收押金（元，可按现场情况调整）</span>
        <input id="f_dep" type="number" min="0" step="0.01" value="${Number(o.depositAmount||0)}" placeholder="0 表示无押金"></label>
      <label class="ca-field"><span>备注（可选）</span><input id="f_note" type="text" placeholder="房号 / 特殊事项"></label>
      <div class="ca-form__hint">押金登记后，退房时以该金额为基数退还；如有营位损坏，可在退房时扣减。</div>
    </div></div>`,
    foot:`<button class="btn ghost" type="button" data-cancel>取消</button><button class="btn" type="button" data-ok>确认入住</button>`});
  ov.querySelector('[data-cancel]').onclick=()=>ov.uxClose();
  ov.querySelector('[data-ok]').onclick=async()=>{
    const dep=Math.max(0,Number(ov.querySelector('#f_dep').value||0));
    const note=String(ov.querySelector('#f_note').value||'').trim();
    try{
      const r=await api(`/api/club/${CLUB}/camp-orders/${id}/checkin`,{method:'PATCH',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({depositHeld:dep,note})});
      ov.uxClose();toast(`已确认入住 · 押金 ¥${_money(r.depositHeld||0)} 已登记`);
      await _campAfterFulfil();
    }catch(e){showAlert({title:'操作失败',message:e.message||'请重试'})}
  };
};
window.caDepPreview=function(dep,v){
  const f=Math.max(0,Math.min(dep,Number(v||0)));
  const el=$('#f_refv');if(el)el.textContent='¥'+_money(dep-f);
};
window.caCampCheckout=async function(id){
  const o=await _campOrderFetch(id);if(!o)return;
  if(o.status!=='checked_in'){showAlert({title:'无法退房',message:'仅「已入住」订单可办理退房'});return;}
  const dep=Number(o.depositHeld||o.depositAmount||0);
  const ov=uxDialog({title:'办理退房 · 退押金结算',desc:`${esc(o.orderNo||'')} · ${esc(o.guestName||'')}`,
    body:`<div class="ca"><div class="ca-form">
      <div class="ca-bk"><div class="ca-bk__row"><span>实收押金</span><b>¥${_money(dep)}</b></div></div>
      <label class="ca-field" style="margin-top:8px"><span>损坏 / 违规扣减（元，无损坏填 0）</span>
        <input id="f_forf" type="number" min="0" max="${dep}" step="0.01" value="0" oninput="caDepPreview(${dep},this.value)"></label>
      <div class="ca-bk" style="margin-top:8px">
        <div class="ca-bk__row"><span>预计退还押金</span><b id="f_refv">¥${_money(dep)}</b></div>
        <div class="ca-bk__row"><span>订单实付（不随押金变化）</span><b>¥${_money(o.total)}</b></div>
      </div>
      <label class="ca-field" style="margin-top:8px"><span>退房备注（可选，如损坏说明）</span><input id="f_note" type="text" placeholder="损坏情况 / 处理说明"></label>
      <div class="ca-form__hint">例：预交押金 ¥400，损坏扣减 ¥200，则退还顾客 ¥200。订单金额不参与押金抵扣。</div>
    </div></div>`,
    foot:`<button class="btn ghost" type="button" data-cancel>取消</button><button class="btn" type="button" data-ok>办理退房</button>`});
  ov.querySelector('[data-cancel]').onclick=()=>ov.uxClose();
  ov.querySelector('[data-ok]').onclick=async()=>{
    const forf=Math.max(0,Math.min(dep,Number(ov.querySelector('#f_forf').value||0)));
    const note=String(ov.querySelector('#f_note').value||'').trim();
    const okc=await showConfirm({title:'办理退房',message:`押金 ¥${_money(dep)}：扣减 ¥${_money(forf)}，退还 ¥${_money(dep-forf)}。确定完成退房？`,danger:forf>0});
    if(!okc)return;
    try{
      const r=await api(`/api/club/${CLUB}/camp-orders/${id}/checkout`,{method:'PATCH',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({depositForfeit:forf,note})});
      ov.uxClose();toast(`已退房 · 扣减 ¥${_money(r.depositForfeit||0)} · 退还 ¥${_money(r.depositRefunded||0)}`);
      await _campAfterFulfil();
    }catch(e){showAlert({title:'操作失败',message:e.message||'请重试'})}
  };
};
window.caOrderSearch=v=>caSearch(v);

/* ===================== 营地会员权益（v3，复用原活动会员体系） ===================== */
const CA_BENEFIT_ITEMS=[
  ['memberDiscountEnabled','会员等级折扣','营位 / 租赁 / 商品按会员等级的活动折扣立减'],
  ['pointsEnabled','积分抵用','允许使用活动积分按「X 分 = ¥1」抵扣营地商品'],
  ['voucherEnabled','优惠券','营地商品可使用活动券池中的可用券'],
  ['earnPointsEnabled','消费送积分','营地订单支付成功后按活动口径发放积分']
];
async function loadCampBenefits(){
  const box=$('#campPane');if(!box)return;
  try{CAMP_BENEFITS=await api(`/api/club/${CLUB}/camp-benefits`,{cache:'no-store'})||{};}
  catch(e){box.innerHTML=`<div class="ca-empty">读取失败：${esc(e.message||'')}</div>`;return;}
  const sw=CA_BENEFIT_ITEMS.map(([k,t,d])=>`<label class="ca-sw ${CAMP_BENEFITS[k]?'on':''}" data-cb="${k}">
      <input type="checkbox" data-cb="${k}" ${CAMP_BENEFITS[k]?'checked':''} onchange="caBenefitToggle('${k}',this.checked)">
      <span class="ca-sw__main"><span class="ca-sw__t">${t}</span><span class="ca-sw__s">${d}</span></span>
      <span class="ca-sw__track"></span></label>`).join('');
  box.innerHTML=`<div class="ca-card"><div class="ca-card__head"><div>
      <div class="ca-card__t">营地会员权益</div>
      <div class="ca-card__s">营地里的「房间 / 营位、租用装备、小站商品」全部适用原活动会员体系：会员等级折扣、积分抵用、优惠券与消费送积分共用同一套规则与账本，无需重复配置。</div></div></div>
    <div class="ca-sw-grid" id="caBenefitGrid">${sw}</div>
    <label class="ca-field" style="max-width:320px;margin-top:16px"><span>活动积分最多可抵扣订单比例（%）</span>
      <input id="caBenefitPercent" type="number" min="0" max="100" step="1" value="${Number(CAMP_BENEFITS.clubPointsMaxDiscountPercent||100)}"></label>
    <div class="ca-form__hint" style="margin-top:8px">设为 100 表示不额外限制；押金始终不参与折扣与积分抵扣。关闭某项后，C 端确认订单页会实时隐藏对应权益。</div>
    <div style="margin-top:16px;display:flex;gap:10px;align-items:center">
      <button class="ca-btn" type="button" onclick="saveCampBenefits()">保存会员权益</button>
      <span id="caBenefitTip" style="font-size:11.5px;color:#8a9a90"></span></div></div>`;
}
window.caBenefitToggle=function(k,on){
  if(!CAMP_BENEFITS)CAMP_BENEFITS={};
  CAMP_BENEFITS[k]=!!on;
  document.querySelectorAll('#caBenefitGrid .ca-sw').forEach(l=>{
    const key=l.getAttribute('data-cb');l.classList.toggle('on',!!CAMP_BENEFITS[key]);
    const i=l.querySelector('input');if(i&&key!==k)i.checked=!!CAMP_BENEFITS[key];
  });
};
window.saveCampBenefits=async function(){
  if(!CAMP_BENEFITS)return;
  const tip=$('#caBenefitTip');if(tip)tip.textContent='';
  const body={};
  CA_BENEFIT_ITEMS.forEach(([k])=>body[k]=!!CAMP_BENEFITS[k]);
  body.clubPointsMaxDiscountPercent=Math.max(0,Math.min(100,Number(($('#caBenefitPercent')||{}).value||100)));
  try{
    CAMP_BENEFITS=await api(`/api/club/${CLUB}/camp-benefits`,{method:'PATCH',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(tip)tip.textContent='已保存 ✓';toast('营地会员权益已保存，C 端报价即时生效');
    await loadCampBenefits();
  }catch(e){showAlert({title:'保存失败',message:e.message||'请重试'})}
};
