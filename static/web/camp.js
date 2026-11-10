/* ===========================================================================
 * ClubOS 营地业态 · C 端（web H5，2026-11-10 UI 重做 v2）
 * 视觉参考「迎曦露营」：沉浸头图 / 悬浮信息卡 / 分区图标宫格 / 日期区间条 /
 * 两列营位卡片 / 红价 + 黑色胶囊预订 / 底部预订抽屉。样式见 camp.css（cp- 前缀）。
 *
 * 依赖（web.js / shared.js 已先加载）：CLUB / USER / PAYER_NAME / PAYER_PHONE /
 *       api / esc / $ / $$；showAlert 仅作最终兜底。
 * 后端契约（不改动）：
 *   GET  /api/public/clubs/{CLUB}                      → module_switches（tab 显隐唯一数据源）
 *   GET  /api/public/clubs/{CLUB}/camps               → 启用营地档案（camelCase）
 *   GET  /api/public/clubs/{CLUB}/pitches?camp_id     → 在售营位（snake_case）
 *   GET  /api/public/clubs/{CLUB}/camp-availability   → 营位×日期房态（含当日价）
 *   GET  /api/public/clubs/{CLUB}/rental-items|store-products|tickets
 *   POST /api/public/clubs/{CLUB}/camp-bookings       → 酒店式跨日锁，返回 pending
 *   POST /api/public/clubs/{CLUB}/camp-checkout       → 结算单骨架
 * 设计：活动/营地互斥同槽；营位=酒店房态，过夜 [check_in, check_out) 跨日锁。
 * ===========================================================================*/

let CAMP_SW={home:true,activity:true,camp:false,bizIntro:true};
let CAMP_LIST=[];          // 营地档案
let CAMP_PITCHES=[];       // 当前营地营位
let CAMP_GOODS={rental:[],store:[],tickets:[]};
let CAMP_AVAIL=null;       // 房态缓存 {start,end,pitches}
let CAMP_CAMP_ID=0;        // 当前营地
let CAMP_FILTER='all';     // 分区筛选
let CAMP_IN='',CAMP_OUT='';// 入住 / 离营（ISO）
let CAMP_BOOK_PID=0;       // 抽屉当前营位
let CAMP_BOOK_QTY=1,CAMP_BOOK_DEP=0;
let CAMP_HERO_IDX=0;
let CAMP_TOAST_T=null;

/* ── 小工具 ─────────────────────────────────────────────────────────────── */
function _iso(d){const z=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+z(d.getMonth()+1)+'-'+z(d.getDate());}
function _add(dstr,n){const d=new Date(dstr+'T00:00:00');d.setDate(d.getDate()+n);return _iso(d);}
function _cnDate(s){if(!s)return '—';const p=s.split('-');return Number(p[1])+'月'+Number(p[2])+'日';}
function _relDay(s){const t=_iso(new Date());if(s===t)return '今天';if(s===_add(t,1))return '明天';if(s===_add(t,2))return '后天';
  const w=['周日','周一','周二','周三','周四','周五','周六'];try{return w[new Date(s+'T00:00:00').getDay()]}catch(e){return ''}}
function _nights(){if(!CAMP_IN||!CAMP_OUT)return 0;return Math.max(0,Math.round((new Date(CAMP_OUT+'T00:00:00')-new Date(CAMP_IN+'T00:00:00'))/86400000));}
function _pitchTypeLabel(t){return ({tent:'帐篷位',rv:'房车位',cabin:'木屋/舱房',day:'日间营位'}[t]||t||'营位');}
function _money(n){return (Math.round((Number(n)||0)*100)/100).toFixed(2);}
function _svg(p){return '<svg viewBox="0 0 24 24" aria-hidden="true">'+p+'</svg>';}
const CP_ICON={
  all:_svg('<path d="M3 10.5 12 3l9 7.5"/><path d="M5.5 9.5V21h13V9.5"/><path d="M9.5 21v-6h5v6"/>'),
  tent:_svg('<path d="M12 4 3 20h18z"/><path d="M12 9 7.5 20h9z"/>'),
  rv:_svg('<path d="M2 16V9h12l4 4h3v3"/><circle cx="7" cy="17.5" r="2"/><circle cx="17" cy="17.5" r="2"/><path d="M9 17.5h6"/>'),
  cabin:_svg('<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>'),
  day:_svg('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
  rental:_svg('<path d="M4 8h16l-1.2 12H5.2z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>'),
  store:_svg('<path d="M3 9h18l-1 11H4z"/><path d="M3 9l2-5h14l2 5"/><path d="M9 20v-7h6v7"/>'),
  ticket:_svg('<path d="M3 8a2 2 0 0 0 0 4v4a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-4a2 2 0 0 0 0-4V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2z"/><path d="M13 5v14" stroke-dasharray="2 2"/>'),
  service:_svg('<path d="M4 12a8 8 0 0 1 16 0v5a2 2 0 0 1-2 2h-2v-7h4"/><path d="M4 12v5a2 2 0 0 0 2 2h2v-7H4"/>'),
  nav:_svg('<path d="M3 11 21 3l-8 18-2-8z"/>'),
  phone:_svg('<path d="M6 3h3l2 5-2.5 1.5a12 12 0 0 0 6 6L16 13l5 2v3a2 2 0 0 1-2 2A16 16 0 0 1 4 5a2 2 0 0 1 2-2z"/>')
};
function _cpToast(msg){
  let t=document.querySelector('.cp-toast');
  if(!t){t=document.createElement('div');t.className='cp-toast';document.body.appendChild(t);}
  t.textContent=msg;t.classList.add('on');
  clearTimeout(CAMP_TOAST_T);CAMP_TOAST_T=setTimeout(()=>t.classList.remove('on'),2200);
}
function _curCamp(){return CAMP_LIST.find(c=>String(c.id)===String(CAMP_CAMP_ID))||CAMP_LIST[0]||{};}
function _safeUrl(u){const s=String(u||'');return /^(https?:\/\/|\/static\/|\/uploads\/|data:image\/)/i.test(s)?s:'';}

/* ── 经营业态开关：活动 / 营地互斥同槽（唯一数据源） ────────────────────── */
async function applyModuleSwitches(){
  try{
    const club=await api('/api/public/clubs/'+CLUB,{cache:'no-store'});
    if(club&&club.module_switches)CAMP_SW=Object.assign({home:true,activity:true,camp:false,bizIntro:true},club.module_switches);
  }catch(e){/* 默认兜底：活动开、营地关 */}
  const actBtn=document.querySelector('.web-nav button[data-wv="wactivities"]');
  const campBtn=document.querySelector('.web-nav button[data-wv="wcamp"]');
  const abBtn=document.querySelector('.web-nav button[data-wv="wability"]');
  if(actBtn)actBtn.style.display=(CAMP_SW.activity&&!CAMP_SW.camp)?'':'none';
  if(campBtn)campBtn.style.display=CAMP_SW.camp?'':'none';
  if(abBtn)abBtn.style.display=CAMP_SW.bizIntro?'':'none';
}
window.applyModuleSwitches=applyModuleSwitches;

/* ── 页入口（web.js 的 wv('wcamp') 调用） ────────────────────────────────── */
async function loadCampPage(){
  const root=$('#campRoot'); if(!root)return;
  root.innerHTML='<div class="cp-page"><div class="cp-state">正在加载营地…</div></div>';
  // 进页重新对齐开关，避免首屏早于 onLaunch/onLoad 的开关返回而误报「未开启」。
  try{
    const club=await api('/api/public/clubs/'+CLUB,{cache:'no-store'});
    if(club&&club.module_switches)CAMP_SW=Object.assign({home:true,activity:true,camp:false,bizIntro:true},club.module_switches);
  }catch(e){}
  if(!CAMP_SW.camp){
    root.innerHTML='<div class="cp-page"><div class="cp-state">本俱乐部暂未开启「营地」业态。</div></div>';
    return;
  }
  let camps;
  try{ camps=await api('/api/public/clubs/'+CLUB+'/camps',{cache:'no-store'}); }
  catch(e){ root.innerHTML='<div class="cp-page"><div class="cp-state">营地读取失败：'+esc(e.message||'')+'</div></div>'; return; }
  CAMP_LIST=camps||[];
  if(!CAMP_LIST.length){
    root.innerHTML='<div class="cp-page"><div class="cp-state">营地暂未开放预订，敬请期待。</div></div>';
    return;
  }
  if(!CAMP_LIST.some(c=>String(c.id)===String(CAMP_CAMP_ID))){CAMP_CAMP_ID=CAMP_LIST[0].id;CAMP_HERO_IDX=0;}
  if(!CAMP_IN){const t=_iso(new Date());CAMP_IN=t;CAMP_OUT=_add(t,1);}
  CAMP_FILTER='all';
  await campOnChange();
}
window.loadCampPage=loadCampPage;

/* 切换营地（保留旧函数名以兼容历史 inline 调用） */
async function campOnChange(){
  const sel=$('#campSel');
  if(sel&&Number(sel.value))CAMP_CAMP_ID=Number(sel.value);
  const root=$('#campRoot'); if(!root)return;
  CAMP_AVAIL=null;CAMP_FILTER='all';
  root.innerHTML='<div class="cp-page"><div class="cp-state">正在读取房态…</div></div>';
  let pitches,rental,store,tickets;
  try{
    [pitches,rental,store,tickets]=await Promise.all([
      api('/api/public/clubs/'+CLUB+'/pitches?camp_id='+CAMP_CAMP_ID,{cache:'no-store'}),
      api('/api/public/clubs/'+CLUB+'/rental-items?camp_id='+CAMP_CAMP_ID,{cache:'no-store'}),
      api('/api/public/clubs/'+CLUB+'/store-products?camp_id='+CAMP_CAMP_ID,{cache:'no-store'}),
      api('/api/public/clubs/'+CLUB+'/tickets?camp_id='+CAMP_CAMP_ID,{cache:'no-store'})
    ]);
  }catch(e){ root.innerHTML='<div class="cp-page"><div class="cp-state">读取失败：'+esc(e.message||'')+'</div></div>'; return; }
  CAMP_PITCHES=pitches||[];
  CAMP_GOODS={rental:rental||[],store:store||[],tickets:tickets||[]};
  await _loadAvail();
  _render();
}
window.campOnChange=campOnChange;

/* 房态一次拉 31 天缓存；日期超出窗口时自动拉到离营日 */
async function _loadAvail(){
  if(!CAMP_OUT)return;
  const end=_add(CAMP_IN,Math.max(30,_nights()+1));
  try{
    CAMP_AVAIL=await api('/api/public/clubs/'+CLUB+'/camp-availability?camp_id='+CAMP_CAMP_ID+
      '&start='+encodeURIComponent(CAMP_IN)+'&end='+encodeURIComponent(end),{cache:'no-store'});
  }catch(e){CAMP_AVAIL={pitches:[]};}
}

/* ── 价格 / 可订判定（与后端 [check_in, check_out) 口径一致） ────────────── */
function _quote(pid,qty,dep){
  const p=(CAMP_AVAIL&&CAMP_AVAIL.pitches||[]).find(x=>String(x.pitchId)===String(pid));
  if(!p)return {ok:false,reason:'暂无房态'};
  const day=p.days||[];
  const meta=CAMP_PITCHES.find(x=>String(x.id)===String(pid));
  const isDay=CAMP_IN===CAMP_OUT||(meta&&!meta.is_overnight);  // 日间营位永远只占当天
  const stay=isDay?day.filter(d=>d.day===CAMP_IN):day.filter(d=>d.day>=CAMP_IN&&d.day<CAMP_OUT);
  if(!stay.length)return {ok:false,reason:'所选区间无房态'};
  const blocked=stay.filter(d=>d.status!=='available');
  const payable=Math.round(stay.reduce((a,d)=>a+(Number(d.price)||0),0)*qty*100)/100;
  return {ok:blocked.length===0,blocked,stay,payable,
    total:Math.round((payable+dep)*100)/100,
    reason:blocked.length?('含不可订日期 '+blocked.map(b=>b.day.slice(5)).join('、')):''};
}
function _pitchPrice(pid){
  const q=_quote(pid,1,0);
  if(q.stay&&q.stay.length)return q.stay.reduce((a,d)=>a+(Number(d.price)||0),0)/q.stay.length;
  const p=CAMP_PITCHES.find(x=>String(x.id)===String(pid));
  return Number(p&&p.base_price)||0;
}

/* ── 主渲染 ─────────────────────────────────────────────────────────────── */
function _render(){
  const root=$('#campRoot'); if(!root)return;
  const c=_curCamp();
  const gallery=(c.gallery||[]).map(_safeUrl).filter(Boolean);
  const hero=gallery[CAMP_HERO_IDX]||gallery[0]||'';
  const selOpts=CAMP_LIST.map(x=>`<option value="${x.id}" ${String(x.id)===String(CAMP_CAMP_ID)?'selected':''}>${esc(x.name||'')}</option>`).join('');
  const chips=[];
  (c.facilities||[]).slice(0,5).forEach(f=>chips.push('<span class="cp-chip">'+esc(f)+'</span>'));
  if(chips.length)chips.unshift('<span class="cp-chip green">营地设施</span>');
  const mapUrl=(c.geoLat&&c.geoLng)
    ? 'https://uri.amap.com/marker?position='+encodeURIComponent(c.geoLng)+','+encodeURIComponent(c.geoLat)+'&name='+encodeURIComponent(c.name||'营地')
    : 'https://www.amap.com/search?query='+encodeURIComponent(c.address||c.name||'营地');

  root.innerHTML=`<div class="cp-page" id="cpPage">
    <div class="cp-hero">
      ${hero?`<img class="cp-hero__img" src="${esc(hero)}" alt="${esc(c.name||'营地')}">`:'<div class="cp-hero__fallback"></div>'}
      <div class="cp-hero__shade"></div>
      <div class="cp-hero__rail">
        <span class="cp-hero__tag">${CP_ICON.tent} CAMP · 营地预订</span>
        ${CAMP_LIST.length>1?`<select id="campSel" onchange="campOnChange()" style="border:0;background:rgba(255,255,255,.9);color:#1d3527;border-radius:99px;padding:6px 10px;font-size:11px;font-weight:700;max-width:150px">${selOpts}</select>`:`<span class="cp-hero__count">${gallery.length>1?(CAMP_HERO_IDX+1)+' / '+gallery.length:'营地实景'}</span>`}
      </div>
      <div class="cp-hero__cap">
        <h1 class="cp-hero__name">${esc(c.name||'山野营地')}</h1>
        <p class="cp-hero__slogan">${esc(c.introText||'选一处营位，把日子过成山野的样子')}</p>
      </div>
      ${gallery.length>1?`<div class="cp-hero__dots">${gallery.map((_,i)=>`<i class="cp-hero__dot ${i===CAMP_HERO_IDX?'on':''}" onclick="campHero(${i})"></i>`).join('')}</div>`:''}
    </div>

    <div class="cp-info">
      <div class="cp-info__row">
        <div class="cp-info__main">
          <div class="cp-info__name">${esc(c.name||'营地')} <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg></div>
          <div class="cp-info__addr">${esc(c.address||'地址待补充')}</div>
        </div>
        <div class="cp-info__acts">
          <a class="cp-ibtn" href="${esc(mapUrl)}" target="_blank" rel="noopener" aria-label="导航">${CP_ICON.nav}</a>
          ${c.contactPhone?`<a class="cp-ibtn" href="tel:${esc(c.contactPhone)}" aria-label="电话">${CP_ICON.phone}</a>`:''}
        </div>
      </div>
      ${chips.length?`<div class="cp-info__chips">${chips.join('')}</div>`:''}
    </div>

    <div class="cp-sec">
      <div class="cp-sec__head"><span class="cp-sec__title">营地分区</span><span class="cp-sec__hint">点图标快速筛选</span></div>
      <div class="cp-grid" id="cpGrid">${_gridHtml()}</div>
    </div>

    <div class="cp-dates" onclick="campOpenDates()" role="button" tabindex="0" aria-label="选择入住离营日期">
      <div class="cp-dates__side"><div class="cp-dates__big">${_cnDate(CAMP_IN)}</div><div class="cp-dates__small">入住 · ${_relDay(CAMP_IN)}</div></div>
      <div class="cp-dates__mid"><span class="cp-dates__nights">${_nights()?('共 '+_nights()+' 晚'):'当天日间'}</span><span class="cp-dates__edit">修改日期 ›</span></div>
      <div class="cp-dates__side"><div class="cp-dates__big">${_cnDate(CAMP_OUT)}</div><div class="cp-dates__small">离营 · ${_relDay(CAMP_OUT)}</div></div>
    </div>

    <div class="cp-zone"><i></i><span>${CAMP_FILTER==='all'?'精选营位':_pitchTypeLabel(CAMP_FILTER)}</span><i></i></div>
    <div class="cp-pitches" id="cpPitches">${_pitchesHtml()}</div>

    ${_goodsHtml()}
    <div style="margin:24px 14px 4px;text-align:center;color:#9aa79f;font-size:10.5px;line-height:1.7">
      营位按日期房态管理 · 过夜跨日锁定 · 下单后 30 分钟内完成支付
    </div>
  </div>`;
}

function _gridHtml(){
  const types=[...new Set(CAMP_PITCHES.map(p=>p.pitch_type||'tent'))];
  const items=[{k:'all',t:'全部营位',i:'all'}];
  const label={tent:'帐篷区',rv:'房车区',cabin:'木屋区',day:'日间营'};
  types.forEach(t=>items.push({k:t,t:label[t]||_pitchTypeLabel(t),i:(CP_ICON[t]?t:'all')}));
  items.push({k:'rental',t:'装备租赁',i:'rental'},{k:'store',t:'营地小站',i:'store'},
             {k:'tickets',t:'活动票',i:'ticket'},{k:'service',t:'联系客服',i:'service'});
  return items.slice(0,8).map(x=>{
    const act=CAMP_FILTER===x.k?' active':'';
    const click=['rental','store','tickets','service'].includes(x.k)?`campJump('${x.k}')`:`campFilter('${x.k}')`;
    return `<button class="cp-grid__item${act}" type="button" onclick="${click}">
      <span class="cp-grid__ico">${CP_ICON[x.i]||CP_ICON.all}</span><span>${x.t}</span></button>`;
  }).join('');
}

function _pitchesHtml(){
  const list=CAMP_FILTER==='all'?CAMP_PITCHES:CAMP_PITCHES.filter(p=>(p.pitch_type||'tent')===CAMP_FILTER);
  if(!list.length)return '<div class="cp-empty">这个分区暂时没有可订营位，看看其它分区吧。</div>';
  const campName=_curCamp().name||'营地';
  return list.map(p=>{
    const q=_quote(p.id,1,0);
    const off=!q.ok;
    const price=_pitchPrice(p.id);
    const badge=off?'<span class="cp-card__badge warn">已订满</span>':`<span class="cp-card__badge">${esc(campName)}</span>`;
    const spec=`${_pitchTypeLabel(p.pitch_type)} · 适用 ${p.capacity||1} 人 · ${p.is_overnight?'过夜':'日间当天'}`;
    const tags=[p.code,p.pitch_type==='day'?'不过夜':'轻户外'].filter(Boolean).map(t=>`<span class="cp-chip" style="padding:2px 6px;font-size:9.5px">${esc(t)}</span>`).join('');
    return `<div class="cp-card${off?' is-off':''}">
      <div class="cp-card__media"><div class="cp-card__ph"></div>${badge}</div>
      <div class="cp-card__body">
        <div class="cp-card__title">${esc(p.name||'营位')}</div>
        <div class="cp-card__spec">${esc(spec)}</div>
        <div style="margin-top:6px;display:flex;gap:4px;flex-wrap:wrap">${tags}</div>
        <div class="cp-card__foot">
          <div class="cp-price"><small>¥</small>${_money(price)}<em>${p.is_overnight?'/晚':'/天'}</em></div>
          <button class="cp-book" type="button" ${off?'disabled':''} onclick="campOpenBook(${p.id})">${off?'已满':'预订'}</button>
        </div>
      </div></div>`;
  }).join('');
}

function _goodsHtml(){
  const blocks=[
    {k:'rental',title:'装备租赁',list:CAMP_GOODS.rental,sub:x=>`日租 ¥${_money(x.daily_price)} · 押金 ¥${_money(x.deposit)}`,price:x=>'¥'+_money(x.daily_price)+' /天'},
    {k:'store',title:'营地小站',list:CAMP_GOODS.store,sub:x=>esc(x.category||'营地自营')+' · 库存 '+(x.stock||0),price:x=>'¥'+_money(x.price)},
    {k:'tickets',title:'活动票',list:CAMP_GOODS.tickets,sub:x=>(x.entry_type==='multi'?'多次入场':'单次入场')+(x.event_date?' · '+esc(x.event_date):''),price:x=>'¥'+_money(x.price)}
  ];
  return blocks.filter(b=>b.list&&b.list.length).map(b=>`<div class="cp-sec" id="cp-${b.k}">
    <div class="cp-sec__head"><span class="cp-sec__title">${b.title}</span><span class="cp-sec__hint">共 ${b.list.length} 项 · 现场取用</span></div>
    <div class="cp-hscroll" style="padding-left:0;padding-right:0">${b.list.map(x=>`
      <div class="cp-gcard"><div class="cp-gcard__media">${_safeUrl(x.cover_url)?`<img class="cp-gcard__img" src="${esc(_safeUrl(x.cover_url))}" alt="">`:'<div class="cp-gcard__ph"></div>'}</div>
        <div class="cp-gcard__body"><div class="cp-gcard__t">${esc(x.name||x.title||'')}</div>
          <div class="cp-gcard__s">${b.sub(x)}</div><div class="cp-gcard__p">${b.price(x)}</div></div></div>`).join('')}</div>
  </div>`).join('');
}

/* ── 交互（全部挂 window，保证 inline onclick 可用） ─────────────────────── */
window.campHero=function(i){CAMP_HERO_IDX=i;_render();};
window.campFilter=function(k){CAMP_FILTER=(CAMP_FILTER===k?'all':k);_render();
  const el=document.querySelector('.cp-zone');if(el)el.scrollIntoView({behavior:'smooth',block:'start'});};
window.campJump=function(k){
  if(k==='service'){const p=_curCamp().contactPhone;if(p)location.href='tel:'+p;else _cpToast('营地暂未登记联系电话');return;}
  const el=document.querySelector('#cp-'+k);if(el)el.scrollIntoView({behavior:'smooth',block:'start'});
  else _cpToast('该营地暂未上架'+({rental:'租赁装备',store:'小站商品',tickets:'活动票'}[k]||''));
};

/* 日期抽屉 */
window.campOpenDates=function(){
  const t=_iso(new Date());
  _showSheet('选择入住 / 离营日期',`
    <div class="cp-f__2">
      <div class="cp-f"><label for="dtIn">入住日期</label><input id="dtIn" type="date" min="${t}" value="${CAMP_IN}"></div>
      <div class="cp-f"><label for="dtOut">离营日期</label><input id="dtOut" type="date" min="${t}" value="${CAMP_OUT}"></div>
    </div>
    <div class="cp-quick">
      <button type="button" onclick="campQuick(1)">今晚 1 晚</button>
      <button type="button" onclick="campQuick(2)">两晚连住</button>
      <button type="button" onclick="campQuick(0)">当天日间营</button>
    </div>
    <div class="cp-sum"><div class="cp-sum__row"><span>营位按日期锁定</span><b>退房日不占用</b></div>
      <div class="cp-sum__note">过夜预订例如 11月10日 → 11月11日 只锁 11月10日一晚；日间营当天结束不跨日。</div></div>
    <button class="cp-submit" type="button" onclick="campApplyDates()">查看可订营位</button>`);
};
window.campQuick=function(n){
  const t=_iso(new Date());
  const i=$('#dtIn'),o=$('#dtOut');if(!i||!o)return;
  i.value=t;o.value=_add(t,n||0);
};
window.campApplyDates=async function(){
  const i=$('#dtIn')?.value,o=$('#dtOut')?.value;
  if(!i||!o){_cpToast('请选择日期');return;}
  if(o<i){_cpToast('离营日期不能早于入住日期');return;}
  CAMP_IN=i;CAMP_OUT=o;
  _closeSheet();
  const root=$('#campRoot');if(root)root.innerHTML='<div class="cp-page"><div class="cp-state">正在更新房态…</div></div>';
  await _loadAvail();_render();
};

/* 预订抽屉 */
window.campOpenBook=function(pid){
  CAMP_BOOK_PID=pid;CAMP_BOOK_QTY=1;CAMP_BOOK_DEP=0;
  const p=CAMP_PITCHES.find(x=>String(x.id)===String(pid))||{};
  const gname=(typeof PAYER_NAME!=='undefined'&&PAYER_NAME)||'';
  const gphone=(typeof PAYER_PHONE!=='undefined'&&PAYER_PHONE)||'';
  _showSheet('预订营位',`
    <div style="display:flex;gap:12px;align-items:center;margin-bottom:16px">
      <div style="width:62px;height:62px;border-radius:12px;overflow:hidden;position:relative;flex:0 0 62px;background:#eaf1e9">
        <div class="cp-card__ph" style="position:absolute;inset:0"></div></div>
      <div style="flex:1;min-width:0"><div style="font-size:15px;font-weight:800">${esc(p.name||'营位')}</div>
        <div style="font-size:11px;color:#8b9691;margin-top:4px">${esc(_pitchTypeLabel(p.pitch_type))} · 适用 ${p.capacity||1} 人</div>
        <div style="font-size:11px;color:#1d6b48;margin-top:3px">${_cnDate(CAMP_IN)} → ${_cnDate(p.is_overnight?CAMP_OUT:CAMP_IN)} · ${p.is_overnight?(_nights()?_nights()+' 晚':'当天'):'日间营 · 当天结束'}</div></div>
    </div>
    <div class="cp-f"><label>预定数量</label><div class="cp-stepper">
      <button type="button" onclick="campQty(-1)">−</button><span id="bkQty">1</span>
      <button type="button" onclick="campQty(1)">＋</button></div></div>
    <div class="cp-f"><label for="bkDep">押金（可选，元）</label><input id="bkDep" type="number" min="0" step="0.01" value="0" onchange="campRecalc()" oninput="campRecalc()"></div>
    <div class="cp-f__2">
      <div class="cp-f"><label for="bkName">入住人姓名</label><input id="bkName" value="${esc(gname)}" placeholder="请填写真实姓名"></div>
      <div class="cp-f"><label for="bkPhone">手机号</label><input id="bkPhone" type="tel" value="${esc(gphone)}" placeholder="便于营地联系"></div>
    </div>
    <div id="bkSummary" class="cp-sum"></div>
    <button class="cp-submit" id="bkSubmit" type="button" onclick="campBook(${pid})">确认预订</button>`);
  campRecalc();
};
window.campQty=function(d){CAMP_BOOK_QTY=Math.max(1,Math.min(20,CAMP_BOOK_QTY+d));
  const el=$('#bkQty');if(el)el.textContent=CAMP_BOOK_QTY;campRecalc();};

/* 价格摘要（保留旧函数名；从状态 + 抽屉 DOM 读取，不依赖页面表单） */
async function campRecalc(){
  const sum=$('#bkSummary');if(!sum)return;
  const dep=Number($('#bkDep')?.value||0);CAMP_BOOK_DEP=dep;
  const q=_quote(CAMP_BOOK_PID,CAMP_BOOK_QTY,dep);
  const meta=CAMP_PITCHES.find(x=>String(x.id)===String(CAMP_BOOK_PID));
  const isDay=CAMP_IN===CAMP_OUT||(meta&&!meta.is_overnight);
  if(!q.ok){sum.innerHTML=`<div class="cp-sum__row"><span style="color:#e0412a">${esc(q.reason||'该区间不可订')}</span></div>`;
    const b=$('#bkSubmit');if(b){b.disabled=true;b.textContent='该区间不可订';}return;}
  sum.innerHTML=`
    <div class="cp-sum__row"><span>${q.stay.length} ${isDay?'天':'晚'} × ${CAMP_BOOK_QTY} 个营位 · 营位费</span><b>¥${_money(q.payable)}</b></div>
    <div class="cp-sum__row"><span>押金（离营退还）</span><b>¥${_money(dep)}</b></div>
    <div class="cp-sum__total"><span>合计</span><b>¥${_money(q.total)}</b></div>
    <div class="cp-sum__note">提交后营位锁定 30 分钟，超时未支付自动释放；结算单生成后按页面提示完成支付。</div>`;
  const b=$('#bkSubmit');if(b){b.disabled=false;b.textContent='确认预订';}
}
window.campRecalc=campRecalc;

/* 提交预订（酒店式跨日锁 + 结算单骨架） */
window.campBook=async function(pid){
  pid=pid||CAMP_BOOK_PID;
  const gname=$('#bkName')?.value.trim()||'',gphone=$('#bkPhone')?.value.trim()||'';
  if(!gname||!gphone){_cpToast('请填写入住人姓名与手机号');return;}
  const q=_quote(pid,CAMP_BOOK_QTY,CAMP_BOOK_DEP);
  if(!q.ok){_cpToast(q.reason||'该区间不可订');return;}
  const meta=CAMP_PITCHES.find(x=>String(x.id)===String(pid));
  const isDay=CAMP_IN===CAMP_OUT||(meta&&!meta.is_overnight);
  const checkOut=isDay?CAMP_IN:CAMP_OUT;
  const btn=$('#bkSubmit');if(btn){btn.disabled=true;btn.textContent='提交中…';}
  try{
    const r=await api('/api/public/clubs/'+CLUB+'/camp-bookings',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({pitchId:pid,checkIn:CAMP_IN,checkOut,isDayUse:isDay,
        qty:CAMP_BOOK_QTY,deposit:CAMP_BOOK_DEP,userId:USER,guestName:gname,guestPhone:gphone})});
    let co=null;
    try{ co=await api('/api/public/clubs/'+CLUB+'/camp-checkout',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({bookingId:r.id})}); }catch(e){/* 结算单失败不阻断预订提示 */}
    _closeSheet();
    showAlert({title:'预订已提交',message:`预订号 #${r.id}\n营位费 ¥${r.payable} + 押金 ¥${r.deposit} = ¥${r.total}\n结算单：${co?(co.checkoutId||co.id||'已生成'):'生成中'}\n状态：待支付（30 分钟内完成，超时房态自动释放）`});
    await campOnChange();
  }catch(e){
    if(btn){btn.disabled=false;btn.textContent='确认预订';}
    showAlert({title:'预订失败',message:e.message||'请重试'});
  }
};

/* ── 抽屉基础设施 ───────────────────────────────────────────────────────── */
function _showSheet(title,body){
  _closeSheet(true);
  const mask=document.createElement('div');mask.className='cp-mask';mask.id='cpMask';
  mask.onclick=_closeSheet;
  const sh=document.createElement('div');sh.className='cp-sheet';sh.id='cpSheet';
  sh.innerHTML=`<div class="cp-sheet__bar"></div>
    <div class="cp-sheet__head"><span class="cp-sheet__title">${esc(title)}</span>
      <button class="cp-sheet__x" type="button" onclick="campCloseSheet()" aria-label="关闭">×</button></div>${body}`;
  document.body.appendChild(mask);document.body.appendChild(sh);
  requestAnimationFrame(()=>{mask.classList.add('open');sh.classList.add('open');});
}
function _closeSheet(now){
  const m=document.querySelector('#cpMask'),s=document.querySelector('#cpSheet');
  if(!m&&!s)return;
  if(m)m.classList.remove('open');if(s)s.classList.remove('open');
  setTimeout(()=>{m&&m.remove();s&&s.remove();},now?0:300);
}
window.campCloseSheet=function(){_closeSheet();};
document.addEventListener('keydown',e=>{if(e.key==='Escape')_closeSheet();});

applyModuleSwitches();
