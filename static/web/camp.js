/* ===========================================================================
 * ClubOS 营地业态 · C 端（web H5，2026-11-11 v3）
 * v3 六点迭代：
 *   1 连续日期区间日历（一次点选起止日 → 一次确认）
 *   2 确认订单 → 支付确认 → 支付成功 → 我的订单 全链路（不再停在 alert）
 *   3 营位/房间高标准详情页（图廊 / 规格 / 设施 / 政策 / 图文详情 / 底部预订栏）
 *   4 装备租赁 + 营地小站商城化（图片 / 日租价 / 押金 / 规格 / 数量 / 详情）
 *   5 移除「活动票」，营地活动直接复用原活动模块
 *   6 会员体系打通：营位 / 租赁 / 商品统一走 camp-quote + camp-orders，
 *     复用会员等级折扣、积分抵用、券、消费送积分
 * 后端契约见 /tmp/camp-api-contract.md；样式 camp.css（cp- 前缀）。
 * 依赖 web.js / shared.js：CLUB / USER / PAYER_NAME / PAYER_PHONE / api / esc / $。
 * ===========================================================================*/

let CAMP_SW={home:true,activity:true,camp:false,bizIntro:true};
let CAMP_LIST=[],CAMP_PITCHES=[],CAMP_GOODS={rental:[],store:[]};
let CAMP_AVAIL=null,CAMP_CAMP_ID=0,CAMP_FILTER='all';
let CAMP_IN='',CAMP_OUT='';
let CAMP_BENEFITS=null,CAMP_CART=[],CAMP_QUOTE=null,CAMP_ORDER=null;
let CAMP_USE_POINTS=false,CAMP_VOUCHER='',CAMP_GUEST={name:'',phone:''};
let CAMP_MY_VOUCHERS=null,CAMP_CONTACTS=null,CAMP_VOUCHER_AUTO=false; /* #811 联系人历史+券自动匹配 */
let CAMP_CAL={in:'',out:'',anchor:''};
let CAMP_DETAIL=null,CAMP_DETAIL_QTY=1;
let CAMP_HERO_IDX=0,CAMP_TOAST_T=null,CAMP_PAY_TIMER=null,CAMP_PAY_LEFT=0;
let CAMP_BUSY=false,CAMP_LV=null;

/* ── 小工具 ─────────────────────────────────────────────────────────────── */
function _iso(d){const z=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+z(d.getMonth()+1)+'-'+z(d.getDate());}
function _add(dstr,n){const d=new Date(dstr+'T00:00:00');d.setDate(d.getDate()+n);return _iso(d);}
function _cnDate(s){if(!s)return '—';const p=s.split('-');return Number(p[1])+'月'+Number(p[2])+'日';}
function _relDay(s){const t=_iso(new Date());if(s===t)return '今天';if(s===_add(t,1))return '明天';if(s===_add(t,2))return '后天';
  const w=['周日','周一','周二','周三','周四','周五','周六'];try{return w[new Date(s+'T00:00:00').getDay()]}catch(e){return ''}}
function _nights(){if(!CAMP_IN||!CAMP_OUT)return 0;return Math.max(0,Math.round((new Date(CAMP_OUT+'T00:00:00')-new Date(CAMP_IN+'T00:00:00'))/86400000));}
function _isDayUse(){return CAMP_IN===CAMP_OUT;}
function _pitchTypeLabel(t){return ({tent:'帐篷位',rv:'房车位',cabin:'木屋/舱房',day:'日间营位'}[t]||t||'营位');}
function _money(n){return (Math.round((Number(n)||0)*100)/100).toFixed(2);}
function _svg(p){return '<svg viewBox="0 0 24 24" aria-hidden="true">'+p+'</svg>';}
function _uid(){try{return (typeof USER!=='undefined'&&USER)||0}catch(e){return 0}}
function _guest(){return {name:CAMP_GUEST.name||((typeof PAYER_NAME!=='undefined'&&PAYER_NAME)||''),
                          phone:CAMP_GUEST.phone||((typeof PAYER_PHONE!=='undefined'&&PAYER_PHONE)||'')};}
function _safeUrl(u){const s=String(u||'');
  if(/^(https?:\/\/|\/static\/|\/uploads\/|data:image\/)/i.test(s))return s;
  /* #809 修复「后台传图前端不显示」：营地素材工作台的公开代理路径（服务端 _camp_safe_url 已放行同一形状） */
  if(s.startsWith('/api/public/clubs/')&&s.includes('/camp-media-file/'))return s;
  return '';}
function _curCamp(){return CAMP_LIST.find(c=>String(c.id)===String(CAMP_CAMP_ID))||CAMP_LIST[0]||{};}
function _cpToast(msg){
  let t=document.querySelector('.cp-toast');
  if(!t){t=document.createElement('div');t.className='cp-toast';document.body.appendChild(t);}
  t.textContent=msg;t.classList.add('on');
  clearTimeout(CAMP_TOAST_T);CAMP_TOAST_T=setTimeout(()=>t.classList.remove('on'),2200);
}
function _post(path,body){return api('/api/public/clubs/'+CLUB+path,{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store'});}
function _get(path){return api('/api/public/clubs/'+CLUB+path,{cache:'no-store'});}
function _err(e){return (e&&(e.message||e.detail))||'网络异常，请重试';}

const CP_ICON={
  all:_svg('<path d="M3 10.5 12 3l9 7.5"/><path d="M5.5 9.5V21h13V9.5"/><path d="M9.5 21v-6h5v6"/>'),
  tent:_svg('<path d="M12 4 3 20h18z"/><path d="M12 9 7.5 20h9z"/>'),
  rv:_svg('<path d="M2 16V9h12l4 4h3v3"/><circle cx="7" cy="17.5" r="2"/><circle cx="17" cy="17.5" r="2"/><path d="M9 17.5h6"/>'),
  cabin:_svg('<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>'),
  day:_svg('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
  rental:_svg('<path d="M4 8h16l-1.2 12H5.2z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>'),
  store:_svg('<path d="M3 9h18l-1 11H4z"/><path d="M3 9l2-5h14l2 5"/><path d="M9 20v-7h6v7"/>'),
  service:_svg('<path d="M4 12a8 8 0 0 1 16 0v5a2 2 0 0 1-2 2h-2v-7h4"/><path d="M4 12v5a2 2 0 0 0 2 2h2v-7H4"/>'),
  order:_svg('<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6"/>'),
  member:_svg('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'),
  cart:_svg('<path d="M2 4h2l2.4 11h11l2-8H6"/><circle cx="9" cy="20" r="1.4"/><circle cx="18" cy="20" r="1.4"/>'),
  check:_svg('<path d="m4 12 5 5L20 6"/>'),
  clock:_svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  back:_svg('<path d="m15 5-7 7 7 7"/>'),
  nav:_svg('<path d="M3 11 21 3l-8 18-2-8z"/>'),
  phone:_svg('<path d="M6 3h3l2 5-2.5 1.5a12 12 0 0 0 6 6L16 13l5 2v3a2 2 0 0 1-2 2A16 16 0 0 1 4 5a2 2 0 0 1 2-2z"/>')
};

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

/* ── 页入口 ─────────────────────────────────────────────────────────────── */
async function loadCampPage(){
  const root=$('#campRoot'); if(!root)return;
  root.innerHTML='<div class="cp-page"><div class="cp-state">正在加载营地…</div></div>';
  try{
    const club=await api('/api/public/clubs/'+CLUB,{cache:'no-store'});
    if(club&&club.module_switches)CAMP_SW=Object.assign({home:true,activity:true,camp:false,bizIntro:true},club.module_switches);
  }catch(e){}
  if(!CAMP_SW.camp){
    root.innerHTML='<div class="cp-page"><div class="cp-state">本俱乐部暂未开启「营地」业态。</div></div>';return;
  }
  let camps;
  try{camps=await api('/api/public/clubs/'+CLUB+'/camps',{cache:'no-store'});}
  catch(e){root.innerHTML='<div class="cp-page"><div class="cp-state">营地读取失败：'+esc(_err(e))+'</div></div>';return;}
  CAMP_LIST=camps||[];
  if(!CAMP_LIST.length){
    root.innerHTML='<div class="cp-page"><div class="cp-state">营地暂未开放预订，敬请期待。</div></div>';return;
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
  CAMP_AVAIL=null;CAMP_FILTER='all';CAMP_CART=[];
  root.innerHTML='<div class="cp-page"><div class="cp-state">正在读取房态…</div></div>';
  let pitches,rental,store,benefits;
  try{
    [pitches,rental,store,benefits]=await Promise.all([
      _get('/pitches?camp_id='+CAMP_CAMP_ID),
      _get('/rental-items?camp_id='+CAMP_CAMP_ID),
      _get('/store-products?camp_id='+CAMP_CAMP_ID),
      _get('/camp-benefits?user_id='+_uid()).catch(()=>null)
    ]);
  }catch(e){root.innerHTML='<div class="cp-page"><div class="cp-state">读取失败：'+esc(_err(e))+'</div></div>';return;}
  CAMP_PITCHES=pitches||[];
  CAMP_GOODS={rental:rental||[],store:store||[]};   // v3：活动票不再请求、不再展示
  CAMP_BENEFITS=benefits||null;
  await _loadAvail();
  _render();
}
window.campOnChange=campOnChange;

/* 房态拉 62 天，覆盖连续区间日历的两个月 */
async function _loadAvail(){
  if(!CAMP_IN)return;
  const end=_add(CAMP_IN,Math.max(62,_nights()+2));
  try{CAMP_AVAIL=await _get('/camp-availability?camp_id='+CAMP_CAMP_ID+
    '&start='+encodeURIComponent(CAMP_IN)+'&end='+encodeURIComponent(end));}
  catch(e){CAMP_AVAIL={pitches:[]};}
}

/* ── 价格 / 可订判定（与后端 [check_in, check_out) 口径一致） ────────────── */
function _quote(pid,qty,dep){
  const p=(CAMP_AVAIL&&CAMP_AVAIL.pitches||[]).find(x=>String(x.pitchId)===String(pid));
  if(!p)return {ok:false,reason:'暂无房态'};
  const day=p.days||[];
  const meta=CAMP_PITCHES.find(x=>String(x.id)===String(pid));
  const isDay=_isDayUse()||(meta&&!meta.is_overnight);
  const stay=isDay?day.filter(d=>d.day===CAMP_IN):day.filter(d=>d.day>=CAMP_IN&&d.day<CAMP_OUT);
  if(!stay.length)return {ok:false,reason:'所选区间无房态'};
  const blocked=stay.filter(d=>d.status!=='available');
  const payable=Math.round(stay.reduce((a,d)=>a+(Number(d.price)||0),0)*qty*100)/100;
  return {ok:blocked.length===0,blocked,stay,payable,total:Math.round((payable+dep)*100)/100,
    reason:blocked.length?('含不可订日期 '+blocked.map(b=>b.day.slice(5)).join('、')):''};
}
function _pitchPrice(pid){
  const q=_quote(pid,1,0);
  if(q.stay&&q.stay.length)return q.stay.reduce((a,d)=>a+(Number(d.price)||0),0)/q.stay.length;
  const p=CAMP_PITCHES.find(x=>String(x.id)===String(pid));
  return Number(p&&p.base_price)||0;
}
function _dayFull(ds){
  const ps=(CAMP_AVAIL&&CAMP_AVAIL.pitches)||[];
  if(!ps.length)return false;
  let any=false;
  for(const p of ps){const d=(p.days||[]).find(x=>x.day===ds);if(!d)return false;if(d.status==='available')any=true;}
  return !any;
}

/* ── 主渲染 ─────────────────────────────────────────────────────────────── */
function _render(){
  const root=$('#campRoot'); if(!root)return;
  const c=_curCamp();
  const gallery=(c.gallery||[]).map(_safeUrl).filter(Boolean);
  const hero=gallery[CAMP_HERO_IDX]||gallery[0]||'';
  const selOpts=CAMP_LIST.map(x=>'<option value="'+x.id+'" '+(String(x.id)===String(CAMP_CAMP_ID)?'selected':'')+'>'+esc(x.name||'')+'</option>').join('');
  const chips=[];
  (c.facilities||[]).slice(0,5).forEach(f=>chips.push('<span class="cp-chip">'+esc(f)+'</span>'));
  if(chips.length)chips.unshift('<span class="cp-chip green">营地设施</span>');
  const mapUrl=(c.geoLat&&c.geoLng)
    ? 'https://uri.amap.com/marker?position='+encodeURIComponent(c.geoLng)+','+encodeURIComponent(c.geoLat)+'&name='+encodeURIComponent(c.name||'营地')
    : 'https://www.amap.com/search?query='+encodeURIComponent(c.address||c.name||'营地');
  const b=CAMP_BENEFITS||{};
  const memberBar=`<div class="cp-member" onclick="campOpenOrders()" role="button" tabindex="0">
      <span class="cp-member__ico">${CP_ICON.member}</span>
      <span class="cp-member__txt"><b>${esc(b.memberLevel||'营地会员')}</b>
        <em>${Number(b.memberDiscountPercent||0)>0?'营地商品立减 '+_money(b.memberDiscountPercent)+'% · ':''}${Number(b.clubPoints||0)} 积分可用 · ${b.pointsRedeemRate||100} 分抵 ¥1</em></span>
      <span class="cp-member__more">我的订单 ›</span></div>`;

  root.innerHTML='<div class="cp-page" id="cpPage">'
    + '<div class="cp-hero">'
      + (hero?'<img class="cp-hero__img" src="'+esc(hero)+'" alt="'+esc(c.name||'营地')+'">':'<div class="cp-hero__fallback"></div>')
      + '<div class="cp-hero__shade"></div>'
      + '<div class="cp-hero__rail"><span class="cp-hero__tag">'+CP_ICON.tent+' CAMP · 营地预订</span>'
      + (CAMP_LIST.length>1?'<select id="campSel" onchange="campOnChange()" class="cp-hero__sel">'+selOpts+'</select>'
          :'<span class="cp-hero__count">'+(gallery.length>1?(CAMP_HERO_IDX+1)+' / '+gallery.length:'营地实景')+'</span>')
      + '</div>'
      + '<div class="cp-hero__cap"><h1 class="cp-hero__name">'+esc(c.name||'山野营地')+'</h1>'
      + '<p class="cp-hero__slogan">'+esc(c.introText||'选一处营位，把日子过成山野的样子')+'</p></div>'
      + (gallery.length>1?'<div class="cp-hero__dots">'+gallery.map((_,i)=>'<i class="cp-hero__dot '+(i===CAMP_HERO_IDX?'on':'')+'" onclick="campHero('+i+')"></i>').join('')+'</div>':'')
    + '</div>'
    + '<div class="cp-info"><div class="cp-info__row"><div class="cp-info__main">'
      + '<div class="cp-info__name">'+esc(c.name||'营地')+' <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg></div>'
      + '<div class="cp-info__addr">'+esc(c.address||'地址待补充')+'</div></div>'
      + '<div class="cp-info__acts"><a class="cp-ibtn" href="'+esc(mapUrl)+'" target="_blank" rel="noopener" aria-label="导航">'+CP_ICON.nav+'</a>'
      + (c.contactPhone?'<a class="cp-ibtn" href="tel:'+esc(c.contactPhone)+'" aria-label="电话">'+CP_ICON.phone+'</a>':'')
      + '</div></div>'+(chips.length?'<div class="cp-info__chips">'+chips.join('')+'</div>':'')+'</div>'
    + memberBar
    + '<div class="cp-sec"><div class="cp-sec__head"><span class="cp-sec__title">营地分区</span><span class="cp-sec__hint">点图标快速筛选</span></div>'
      + '<div class="cp-grid" id="cpGrid">'+_gridHtml()+'</div></div>'
    + '<div class="cp-dates" onclick="campOpenDates()" role="button" tabindex="0" aria-label="选择入住离营日期">'
      + '<div class="cp-dates__side"><div class="cp-dates__big">'+_cnDate(CAMP_IN)+'</div><div class="cp-dates__small">入住 · '+_relDay(CAMP_IN)+'</div></div>'
      + '<div class="cp-dates__mid"><span class="cp-dates__nights">'+(_nights()?('共 '+_nights()+' 晚'):'当天日间')+'</span><span class="cp-dates__edit">修改日期 ›</span></div>'
      + '<div class="cp-dates__side"><div class="cp-dates__big">'+_cnDate(CAMP_OUT)+'</div><div class="cp-dates__small">离营 · '+_relDay(CAMP_OUT)+'</div></div></div>'
    + '<div class="cp-zone"><i></i><span>'+(CAMP_FILTER==='all'?'精选营位':_pitchTypeLabel(CAMP_FILTER))+'</span><i></i></div>'
    + '<div class="cp-pitches" id="cpPitches">'+_pitchesHtml()+'</div>'
    + _shopHtml('rental')
    + _shopHtml('store')
    + '<div class="cp-footnote">营位按日期房态管理 · 过夜跨日锁定 · 下单后 30 分钟内完成支付<br>营地活动请前往「活动」栏目查看与报名</div>'
    + _cartBarHtml()
    + '</div>';
}

function _gridHtml(){
  const types=[...new Set(CAMP_PITCHES.map(p=>p.pitch_type||'tent'))];
  const items=[{k:'all',t:'全部营位',i:'all'}];
  const label={tent:'帐篷区',rv:'房车区',cabin:'木屋区',day:'日间营'};
  types.forEach(t=>items.push({k:t,t:label[t]||_pitchTypeLabel(t),i:(CP_ICON[t]?t:'all')}));
  items.push({k:'rental',t:'装备租赁',i:'rental'},{k:'store',t:'营地小站',i:'store'},
             {k:'service',t:'联系客服',i:'service'});
  return items.slice(0,8).map(x=>{
    const act=CAMP_FILTER===x.k?' active':'';
    const click=['rental','store','service'].includes(x.k)?"campJump('"+x.k+"')":"campFilter('"+x.k+"')";
    return '<button class="cp-grid__item'+act+'" type="button" onclick="'+click+'">'
      + '<span class="cp-grid__ico">'+(CP_ICON[x.i]||CP_ICON.all)+'</span><span>'+x.t+'</span></button>';
  }).join('');
}

function _mediaHtml(url,cls){
  const u=_safeUrl(url);
  return u?'<img class="'+cls+'" src="'+esc(u)+'" alt="">':'<div class="cp-card__ph"></div>';
}

function _pitchesHtml(){
  const list=CAMP_FILTER==='all'?CAMP_PITCHES:CAMP_PITCHES.filter(p=>(p.pitch_type||'tent')===CAMP_FILTER);
  if(!list.length)return '<div class="cp-empty">这个分区暂时没有可订营位，看看其它分区吧。</div>';
  const campName=_curCamp().name||'营地';
  return list.map(p=>{
    const q=_quote(p.id,1,0),off=!q.ok,price=_pitchPrice(p.id);
    const badge=off?'<span class="cp-card__badge warn">已订满</span>':'<span class="cp-card__badge">'+esc(campName)+'</span>';
    const spec=_pitchTypeLabel(p.pitch_type)+' · 适用 '+(p.capacity||1)+' 人 · '+(p.is_overnight?'过夜':'日间当天');
    const tags=[p.code,p.pitch_type==='day'?'不过夜':'轻户外'].filter(Boolean)
      .map(t=>'<span class="cp-chip" style="padding:2px 6px;font-size:9.5px">'+esc(t)+'</span>').join('');
    return '<div class="cp-card'+(off?' is-off':'')+'">'
      + '<div class="cp-card__media" onclick="campOpenDetail(\'pitch\','+p.id+')">'+_mediaHtml(p.coverUrl||p.cover_url,
          'cp-card__img')+badge+'</div>'
      + '<div class="cp-card__body"><div class="cp-card__title" onclick="campOpenDetail(\'pitch\','+p.id+')">'+esc(p.name||'营位')+'</div>'
      + '<div class="cp-card__spec">'+esc(spec)+'</div>'
      + '<div class="cp-card__tags">'+tags+'</div>'
      + '<div class="cp-card__foot"><div class="cp-price"><small>¥</small>'+_money(price)+'<em>'+(p.is_overnight?'/晚':'/天')+'</em></div>'
      + '<button class="cp-book" type="button" '+(off?'disabled':'')+' onclick="campBookNow(\'pitch\','+p.id+')">'+(off?'已满':'预订')+'</button></div>'
      + '</div></div>';
  }).join('');
}

/* 装备租赁 / 营地小站：商品化两列网格（图片、规格、价格、押金、库存、加购） */
function _shopHtml(kind){
  const list=CAMP_GOODS[kind]||[];
  if(!list.length)return '';
  const isRental=kind==='rental';
  const title=isRental?'装备租赁':'营地小站';
  const hint=isRental?'按天计价 · 押金离营退还':'营地自营 · 现场取货';
  const cards=list.map(x=>{
    const price=isRental?Number(x.daily_price||0):Number(x.price||0);
    const unit=isRental?'/天':'/件';
    const sub=isRental
      ? ('押金 ¥'+_money(x.deposit)+' · 可租 '+(x.available_qty||0)+' 件')
      : (esc(x.category||'营地自营')+' · 库存 '+(x.stock||0));
    const spec=(x.specs&&x.specs[0])?(x.specs[0].label+' '+x.specs[0].value):(x.description||x.subtitle||'');
    const off=isRental?!(Number(x.available_qty||0)>0):!(Number(x.stock||0)>0);
    return '<div class="cp-prod" onclick="campOpenDetail(\''+kind+'\','+x.id+')">'
      + '<div class="cp-prod__media">'+_mediaHtml(x.coverUrl||x.cover_url,'cp-prod__img')
      + (off?'<span class="cp-prod__off">已租罄</span>':'')+'</div>'
      + '<div class="cp-prod__body"><div class="cp-prod__t">'+esc(x.name||'商品')+'</div>'
      + '<div class="cp-prod__spec">'+esc(String(spec).slice(0,28))+'</div>'
      + '<div class="cp-prod__sub">'+sub+'</div>'
      + '<div class="cp-prod__foot"><div class="cp-price"><small>¥</small>'+_money(price)+'<em>'+unit+'</em></div>'
      + '<button class="cp-prod__add" type="button" '+(off?'disabled':'')
      + ' onclick="event.stopPropagation();campAddCart(\''+kind+'\','+x.id+')">＋</button></div></div></div>';
  }).join('');
  return '<div class="cp-sec" id="cp-'+kind+'"><div class="cp-sec__head cp-sec__head--link" onclick="_openShopList(\''+kind+'\')">'
    + '<span class="cp-sec__title">'+title+'</span>'
    + '<span class="cp-sec__hint">共 '+list.length+' 项 · 查看全部 ›</span></div>'
    + '<div class="cp-shop">'+cards+'</div></div>';
}

function _cartBarHtml(){
  if(!CAMP_CART.length)return '';
  const n=CAMP_CART.reduce((a,x)=>a+Number(x.qty||1),0);
  const est=CAMP_CART.reduce((a,x)=>a+Number(x.unitPrice||0)*Number(x.qty||1)*
    (x.kind==='store'?1:Math.max(1,Number(x.days||1)))+Number(x.deposit||0),0);
  return '<div class="cp-cartbar"><div class="cp-cartbar__ico">'+CP_ICON.cart+'<i>'+n+'</i></div>'
    + '<div class="cp-cartbar__txt"><b>已选 '+n+' 件</b><em>预估 ¥'+_money(est)+' · 结算时自动计算会员价</em></div>'
    + '<button class="cp-cartbar__go" type="button" onclick="campOpenCheckout()">去确认</button></div>';
}

/* ── 交互（全部挂 window，保证 inline onclick 可用） ─────────────────────── */
window.campHero=function(i){CAMP_HERO_IDX=i;_render();};
window.campFilter=function(k){CAMP_FILTER=(CAMP_FILTER===k?'all':k);_render();
  const el=document.querySelector('.cp-zone');if(el)el.scrollIntoView({behavior:'smooth',block:'start'});};
window.campJump=function(k){
  if(k==='service'){const p=_curCamp().contactPhone;if(p)location.href='tel:'+p;else _cpToast('营地暂未登记联系电话');return;}
  if(k==='rental'||k==='store'){_openShopList(k);return;}
  const el=document.querySelector('#cp-'+k);if(el)el.scrollIntoView({behavior:'smooth',block:'start'});
  else _cpToast('该营地暂未上架'+(k==='rental'?'租赁装备':'小站商品'));
};

/* ── 双商城二级页（独立列表页：从分区宫格 / 主页分区头进入） ──────────────── */
function _jsStr(s){return "'"+String(s==null?'':s).replace(/\\/g,'\\\\').replace(/'/g,"\\'")+"'";}
function _openShopList(kind){
  const list=CAMP_GOODS[kind]||[];
  if(!list.length){_cpToast('该营地暂未上架'+(kind==='rental'?'租赁装备':'小站商品'));return;}
  _paintShopList(kind,'');
}
function _shopCats(kind){
  if(kind!=='store')return [];
  return [...new Set((CAMP_GOODS.store||[]).map(x=>x.category).filter(Boolean))];
}
function _shopCardHtml(x,kind){
  const isR=kind==='rental';
  const price=isR?Number(x.daily_price||0):Number(x.price||0);
  const unit=isR?'/天':'/件';
  const sub=isR?('押金 ¥'+_money(x.deposit)+' · 可租 '+(x.available_qty||0)+' 件')
               :(esc(x.category||'营地自营')+' · 库存 '+(x.stock||0));
  const spec=(x.specs&&x.specs[0])?(x.specs[0].label+' '+x.specs[0].value):(x.description||x.subtitle||'');
  const off=isR?!(Number(x.available_qty||0)>0):!(Number(x.stock||0)>0);
  return '<div class="cp-prod cp-prod--lv" onclick="campOpenDetail(\''+kind+'\','+x.id+')">'
    + '<div class="cp-prod__media">'+_mediaHtml(x.coverUrl||x.cover_url,'cp-prod__img')
    + (off?'<span class="cp-prod__off">已租罄</span>':'')+'</div>'
    + '<div class="cp-prod__body"><div class="cp-prod__t">'+esc(x.name||'商品')+'</div>'
    + '<div class="cp-prod__spec">'+esc(String(spec).slice(0,28))+'</div>'
    + '<div class="cp-prod__sub">'+sub+'</div>'
    + '<div class="cp-prod__foot"><div class="cp-price"><small>¥</small>'+_money(price)+'<em>'+unit+'</em></div>'
    + '<button class="cp-prod__add" type="button" '+(off?'disabled':'')
    + ' onclick="event.stopPropagation();campAddCart(\''+kind+'\','+x.id+')">＋</button></div></div></div>';
}
function _paintShopList(kind,cat){
  const isR=kind==='rental';
  const title=isR?'装备租赁':'营地小站';
  const cats=_shopCats(kind);
  const list=(CAMP_GOODS[kind]||[]).filter(x=>!cat||x.category===cat);
  const hero=(CAMP_GOODS[kind]||[]).find(x=>_safeUrl(x.coverUrl||x.cover_url))||list[0]||{};
  const heroUrl=_safeUrl(hero.coverUrl||hero.cover_url);
  const catChips=cats.length
    ? ('<div class="cp-lv__chips">'
       + '<button class="cp-lv__chip'+(!cat?' on':'')+'" type="button" onclick="_shopPickCat(\''+kind+'\',\'\')">全部</button>'
       + cats.map(c=>'<button class="cp-lv__chip'+(cat===c?' on':'')+'" type="button" onclick="_shopPickCat(\''+kind+'\','+_jsStr(c)+')">'+esc(c)+'</button>').join('')
       + '</div>')
    : '';
  const cnt=CAMP_CART.reduce((a,x)=>a+Number(x.qty||1),0);
  const cards=list.length?list.map(x=>_shopCardHtml(x,kind)).join('')
    : '<div class="cp-empty">该分类下暂无商品，换个分类看看。</div>';
  const html='<div class="cp-lv" id="cpShop">'
    + '<div class="cp-lv__bar"><button class="cp-lv__back" type="button" onclick="_closeShopList()">'+CP_ICON.back+'</button>'
    + '<span class="cp-lv__title">'+title+'</span>'
    + '<button class="cp-lv__cart'+(cnt?' has':'')+'" type="button" onclick="campOpenCheckout()">'+CP_ICON.cart+(cnt?'<i>'+cnt+'</i>':'')+'</button></div>'
    + (heroUrl?'<div class="cp-lv__hero"><img src="'+esc(heroUrl)+'" alt=""></div>':'')
    + catChips
    + '<div class="cp-lv__meta">共 '+list.length+' 项 · '+(isR?'按天计价 · 押金离营退还':'营地自营 · 现场取货')+'</div>'
    + '<div class="cp-shop cp-lv__grid">'+cards+'</div>'
    + '<div class="cp-footnote">营地活动请前往「活动」栏目查看与报名</div>'
    + '</div>';
  let node=document.querySelector('#cpShopHost');
  if(!node){node=document.createElement('div');node.id='cpShopHost';document.body.appendChild(node);}
  node.innerHTML=html;
  document.body.classList.add('cp-lock');
}
window._shopPickCat=function(kind,cat){_paintShopList(kind,cat);};
window._closeShopList=function(){const n=document.querySelector('#cpShopHost');if(n)n.remove();
  document.body.classList.remove('cp-lock');CAMP_LV=null;};

/* ── 连续日期区间日历（一次点选起止、一次确认） ──────────────────────────── */
function _monthAdd(ym,n){const p=ym.split('-');const d=new Date(Number(p[0]),Number(p[1])-1+n,1);
  return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0');}
function _calMonthHtml(ym){
  const p=ym.split('-'),y=Number(p[0]),m=Number(p[1]);
  const first=new Date(y,m-1,1),lead=first.getDay(),count=new Date(y,m,0).getDate();
  const wk=['日','一','二','三','四','五','六'].map(x=>'<span>'+x+'</span>').join('');
  let cells='';
  for(let i=0;i<lead;i++)cells+='<span class="cp-cal__cell empty"></span>';
  for(let d=1;d<=count;d++){
    const ds=ym+'-'+String(d).padStart(2,'0');
    const dis=ds<_iso(new Date()),a=CAMP_CAL.in,b=CAMP_CAL.out;
    const isIn=ds===a,isOut=ds===b,between=!!(a&&b&&ds>a&&ds<b),full=_dayFull(ds);
    const cls=['cp-cal__cell',dis?'disabled':'',isIn||isOut?'edge':'',between?'between':'',full?'full':''].filter(Boolean).join(' ');
    const lab=isIn?'入住':(isOut?'离营':(full?'满':''));
    cells+='<button type="button" class="'+cls+'" '+(dis?'disabled':'')+' onclick="campPickDay(\''+ds+'\')">'
      + '<span class="cp-cal__d">'+d+'</span>'+(lab?'<span class="cp-cal__lab">'+lab+'</span>':'')+'</button>';
  }
  return '<div class="cp-cal__month"><div class="cp-cal__mh">'+y+' 年 '+m+' 月</div>'
    + '<div class="cp-cal__wk">'+wk+'</div><div class="cp-cal__grid">'+cells+'</div></div>';
}
function _calHtml(){
  const a=CAMP_CAL.in||CAMP_IN,b=CAMP_CAL.out||'';
  const nights=(a&&b)?Math.max(0,Math.round((new Date(b+'T00:00:00')-new Date(a+'T00:00:00'))/86400000)):0;
  const tip=!a?'请选择入住日期':(!b?'已选入住 '+_cnDate(a)+' · 再点一天选离营，或直接确认为当天日间营'
    :'入住 '+_cnDate(a)+' → 离营 '+_cnDate(b)+' · 共 '+nights+' 晚');
  return '<div class="cp-cal__tip">'+tip+'</div>'
    + '<div class="cp-cal__nav"><button type="button" onclick="campCalShift(-1)">‹ 上个月</button>'
    + '<span>可连续选择起止日期</span><button type="button" onclick="campCalShift(1)">下个月 ›</button></div>'
    + '<div class="cp-cal__months">'+_calMonthHtml(CAMP_CAL.anchor)+_calMonthHtml(_monthAdd(CAMP_CAL.anchor,1))+'</div>'
    + '<div class="cp-quick"><button type="button" onclick="campQuick(1)">今晚 1 晚</button>'
    + '<button type="button" onclick="campQuick(2)">两晚连住</button>'
    + '<button type="button" onclick="campQuick(0)">当天日间营</button></div>'
    + '<div class="cp-sum"><div class="cp-sum__note">过夜预订按 [入住, 离营) 锁定，退房日不占用；例如 11月10日 → 11月11日 只锁 10 日一晚。日间营当天结束。</div></div>'
    + '<button class="cp-submit" type="button" onclick="campApplyDates()">确认日期 · 查看可订营位</button>';
}
function _paintCal(){const el=$('#cpCalBody');if(el)el.innerHTML=_calHtml();}
window.campOpenDates=function(){
  const t=_iso(new Date());
  CAMP_CAL={in:CAMP_IN,out:_isDayUse()?'':CAMP_OUT,anchor:t.slice(0,7)};
  _showSheet('选择入住 / 离营日期','<div id="cpCalBody">'+_calHtml()+'</div>');
};
window.campCalShift=function(n){CAMP_CAL.anchor=_monthAdd(CAMP_CAL.anchor,n);_paintCal();};
window.campPickDay=function(ds){
  if(ds<_iso(new Date()))return;
  if(!CAMP_CAL.in||CAMP_CAL.out||ds<=CAMP_CAL.in){CAMP_CAL.in=ds;CAMP_CAL.out='';}
  else CAMP_CAL.out=ds;
  _paintCal();
};
window.campQuick=function(n){
  const t=_iso(new Date());CAMP_CAL={in:t,out:n?_add(t,n):'',anchor:t.slice(0,7)};_paintCal();
};
window.campApplyDates=async function(){
  const i=CAMP_CAL.in||CAMP_IN,o=CAMP_CAL.out||i;
  CAMP_IN=i;CAMP_OUT=o;
  // 日期变化后同步刷新清单里的时间型商品，避免按旧日期报价/锁房态
  CAMP_CART.forEach(x=>{
    if(x.kind==='store')return;
    x.checkIn=i;
    x.checkOut=x.kind==='pitch'?(i===o?i:o):(i===o?_add(i,1):o);
    x.isDayUse=(i===o)?1:0;
    x.days=x.kind==='pitch'?Math.max(1,_nights()):Math.max(1,Math.round((new Date(x.checkOut+'T00:00:00')-new Date(i+'T00:00:00'))/86400000));
  });
  _closeSheet();
  const root=$('#campRoot');if(root)root.innerHTML='<div class="cp-page"><div class="cp-state">正在更新房态…</div></div>';
  await _loadAvail();_render();
  _cpToast('已选 '+_cnDate(i)+' → '+_cnDate(o)+(_nights()?' · '+_nights()+' 晚':' · 当天日间营'));
};

/* ── 商品详情（高标准详情页） ───────────────────────────────────────────── */
function _listFallback(kind,id){
  const arr=(kind==='pitch'?CAMP_PITCHES:CAMP_GOODS[kind])||[];
  const x=arr.find(v=>String(v.id)===String(id))||{};
  const isP=kind==='pitch',isR=kind==='rental';
  return {ok:true,kind:kind,id:id,name:x.name||'',subtitle:x.subtitle||'',
    coverUrl:_safeUrl(x.coverUrl||x.cover_url),gallery:(x.gallery||[]).map(_safeUrl).filter(Boolean),
    price:isP?Number(x.base_price||0):(isR?Number(x.daily_price||0):Number(x.price||0)),
    originalPrice:Number(x.originalPrice||0),unitLabel:x.unitLabel||(isP?'/晚':(isR?'/天':'/件')),
    specs:x.specs||[],facilities:x.facilities||[],policies:x.policies||[],
    description:x.description||'',detailText:x.detailText||'',
    stock:isR?Number(x.available_qty||0):(isP?0:Number(x.stock||0)),
    dailyPrice:isR?Number(x.daily_price||0):0,deposit:isR?Number(x.deposit||0):0,
    isOvernight:isP?!!x.is_overnight:false,capacity:isP?Number(x.capacity||1):0,pitchType:isP?(x.pitch_type||''):''};
}
window.campOpenDetail=async function(kind,id){
  let d=null;
  try{d=await _get('/camp-products/'+kind+'/'+id);}catch(e){}
  CAMP_DETAIL=d||_listFallback(kind,id);
  CAMP_DETAIL_QTY=1;
  _paintDetail();
};
function _detailSpecs(d){
  const rows=[];
  if(d.kind==='pitch'){
    rows.push(['营位类型',_pitchTypeLabel(d.pitchType)],['可住人数',(d.capacity||1)+' 人'],
      ['营期',d.isOvernight?'可过夜（跨日锁定）':'日间营 · 当天结束'],['计价单位',d.unitLabel||'/晚']);
  }else if(d.kind==='rental'){
    rows.push(['日租金','¥'+_money(d.dailyPrice)+' / 天'],['押金','¥'+_money(d.deposit)+' · 离营退还'],
      ['可租数量',(d.stock||0)+' 件'],['租期',_isDayUse()?'当天':(_nights()+' 晚')]);
  }else{
    rows.push(['规格',(d.specs&&d.specs[0])?String(d.specs[0].value):'标准规格'],
      ['库存',(d.stock||0)+' 件'],['取货','营地小站 · 现场取货'],['计价单位',d.unitLabel||'/件']);
  }
  (d.specs||[]).forEach(s=>{if(s&&s.label)rows.push([String(s.label),String(s.value||'')]);});
  return rows.map(r=>'<div class="cp-dt__spec"><span>'+esc(r[0])+'</span><b>'+esc(r[1])+'</b></div>').join('');
}
function _detailPolicies(d){
  const ps=(d.policies||[]).filter(Boolean);
  const base=[{title:'预订说明',text:'下单后营位 / 库存锁定 30 分钟，超时未支付自动释放。'},
              {title:'退改规则',text:'未支付订单可直接取消；已支付订单请联系营地处理。'}];
  return base.concat(ps).map(p=>{
    const t=(typeof p==='string')?p:(p.title||''),x=(typeof p==='string')?'':(p.text||'');
    return '<div class="cp-dt__pol"><b>'+esc(t)+'</b>'+(x?'<p>'+esc(x)+'</p>':'')+'</div>';
  }).join('');
}
/* AI 图文详情（营地 v4）：sections=[{headline,body,mediaRefs:[idx]}]，mediaRefs 指向图廊 */
function _paintHighlights(d){
  const ai=d.aiDetail;
  if(!ai||!Array.isArray(ai.highlights)||!ai.highlights.length)return '';
  return '<div class="cp-dt__hl">'+ai.highlights.map(h=>'<span class="cp-chip">'+esc(String(h))+'</span>').join('')+'</div>';
}
function _paintAiSections(d){
  const ai=d.aiDetail;
  if(!ai||!Array.isArray(ai.sections)||!ai.sections.length)return '';
  const gal=[d.coverUrl].concat(d.gallery||[]).map(_safeUrl).filter(Boolean);
  const blocks=(ai.sections||[]).map(s=>{
    const refs=Array.isArray(s.mediaRefs)?s.mediaRefs:[];
    const imgs=refs.map(i=>gal[Number(i)]).filter(Boolean)
      .map(u=>'<img src="'+esc(u)+'" alt="" loading="lazy">').join('');
    return '<div class="cp-dt__sec">'
      + (s.headline?'<h4>'+esc(String(s.headline))+'</h4>':'')
      + (s.body?'<p>'+esc(String(s.body))+'</p>':'')
      + (imgs?'<div class="cp-dt__secimgs">'+imgs+'</div>':'')
      + '</div>';
  }).join('');
  return '<div class="cp-dt__block"><h3>图文详情</h3><div class="cp-dt__ai">'+blocks+'</div></div>';
}
function _paintDetail(){
  const d=CAMP_DETAIL;if(!d)return;
  const gal=[d.coverUrl].concat(d.gallery||[]).map(_safeUrl).filter(Boolean);
  const imgs=gal.length?gal.map(u=>'<img src="'+esc(u)+'" alt="">').join('')
    :'<div class="cp-dt__ph"></div>';
  const fac=(d.facilities||[]).map(f=>'<span class="cp-chip">'+esc(String(f))+'</span>').join('');
  const hasAi=!!(d.aiDetail&&Array.isArray(d.aiDetail.sections)&&d.aiDetail.sections.length);
  const para=hasAi?'':String(d.detailText||d.description||'').split(/\n+/).filter(Boolean)
    .map(t=>'<p>'+esc(t)+'</p>').join('');
  const isPitch=d.kind==='pitch';
  const price=d.price||0;
  const canAdd=isPitch?_quote(d.id,1,0).ok:true;
  const html='<div class="cp-detail" id="cpDetail">'
    + '<div class="cp-dt__top"><button type="button" onclick="campCloseDetail()">'+CP_ICON.back+'</button>'
      + '<span>商品详情</span><button type="button" onclick="campOpenCheckout()" aria-label="清单">'+CP_ICON.cart+'</button></div>'
    + '<div class="cp-dt__scroll">'
      + '<div class="cp-dt__gallery">'+imgs+'</div>'
      + '<div class="cp-dt__head"><div class="cp-dt__kicker">'+esc(isPitch?'PITCH / 营位':(d.kind==='rental'?'RENTAL / 装备租赁':'STORE / 营地小站'))+'</div>'
        + '<h2>'+esc(d.name||'营地商品')+'</h2>'
        + (d.subtitle?'<p class="cp-dt__sub">'+esc(d.subtitle)+'</p>':'')
        + _paintHighlights(d)
        + '<div class="cp-dt__price"><span>¥</span>'+_money(price)+'<em>'+esc(d.unitLabel||'')+'</em>'
        + (Number(d.originalPrice)>price?'<del>¥'+_money(d.originalPrice)+'</del>':'')+'</div>'
        + (CAMP_BENEFITS&&Number(CAMP_BENEFITS.memberDiscountPercent)>0
            ?'<div class="cp-dt__vip">'+esc(CAMP_BENEFITS.memberLevel||'会员')+' 立减 '+_money(CAMP_BENEFITS.memberDiscountPercent)+'% · 可用积分 '+Number(CAMP_BENEFITS.clubPoints||0)+' 分</div>':'')
        + '</div>'
      + '<div class="cp-dt__block"><h3>规格参数</h3><div class="cp-dt__specs">'+_detailSpecs(d)+'</div></div>'
      + (fac?'<div class="cp-dt__block"><h3>设施与服务</h3><div class="cp-dt__chips">'+fac+'</div></div>':'')
      + (para?'<div class="cp-dt__block"><h3>商品详情</h3><div class="cp-dt__rich">'+para+'</div></div>':'')
      + _paintAiSections(d)
      + '<div class="cp-dt__block"><h3>预订须知</h3>'+_detailPolicies(d)+'</div>'
      + '<div class="cp-dt__safe">ClubOS 营地 · 价格与会员权益由系统实时计算</div>'
    + '</div>'
    + '<div class="cp-dt__bar">'
      + (isPitch?'<div class="cp-dt__barinfo"><b>1 个营位</b><em>'+_cnDate(CAMP_IN)+' → '+_cnDate(d.isOvernight&&!_isDayUse()?CAMP_OUT:CAMP_IN)+'</em></div>'
        :'<div class="cp-stepper"><button type="button" onclick="campDetailQty(-1)">−</button><span id="dtQty">'+CAMP_DETAIL_QTY+'</span><button type="button" onclick="campDetailQty(1)">＋</button></div>')
      + '<button class="cp-dt__add" type="button" '+(canAdd?'':'disabled')+' onclick="campDetailAdd(0)">'+(canAdd?'加入清单':'已订满')+'</button>'
      + '<button class="cp-dt__buy" type="button" '+(canAdd?'':'disabled')+' onclick="campDetailAdd(1)">立即预订</button>'
    + '</div></div>';
  let node=document.querySelector('#cpDetail');
  if(!node){node=document.createElement('div');node.id='cpDetailHost';document.body.appendChild(node);}
  node.innerHTML=html;
}
window.campCloseDetail=function(){const n=document.querySelector('#cpDetailHost');if(n)n.remove();CAMP_DETAIL=null;};
window.campDetailQty=function(delta){
  CAMP_DETAIL_QTY=Math.max(1,Math.min(20,CAMP_DETAIL_QTY+delta));
  const el=$('#dtQty');if(el)el.textContent=CAMP_DETAIL_QTY;
};
window.campDetailAdd=function(goCheckout){
  const d=CAMP_DETAIL;if(!d)return;
  const ok=_addToCart(d.kind,d.id,goCheckout?CAMP_DETAIL_QTY:CAMP_DETAIL_QTY);
  if(!ok)return;
  campCloseDetail();
  if(goCheckout)campOpenCheckout();
};

/* ── 购物车：营位 / 装备 / 商品统一进一张订单 ───────────────────────────── */
function _addToCart(kind,id,qty){
  qty=Math.max(1,Math.min(20,Number(qty)||1));
  if(kind==='pitch'){
    const q=_quote(id,1,0);
    if(!q.ok){_cpToast(q.reason||'该日期区间不可订');return false;}
    const exist=CAMP_CART.find(x=>x.kind==='pitch'&&String(x.id)===String(id));
    if(exist){_cpToast('该营位已在清单中');return true;}
    const p=CAMP_PITCHES.find(x=>String(x.id)===String(id))||{};
    const day=_isDayUse()||!p.is_overnight;
    CAMP_CART.push({kind:'pitch',id:id,title:p.name||'营位',coverUrl:_safeUrl(p.coverUrl||p.cover_url),
      spec:_pitchTypeLabel(p.pitch_type),unitLabel:p.is_overnight?'/晚':'/天',unitPrice:q.payable,
      qty:1,days:day?1:Math.max(1,_nights()),checkIn:CAMP_IN,checkOut:day?CAMP_IN:CAMP_OUT,
      isDayUse:day?1:0,deposit:0});
  }else{
    const arr=CAMP_GOODS[kind]||[];
    const x=arr.find(v=>String(v.id)===String(id))||{};
    const isR=kind==='rental';
    const unit=isR?Number(x.daily_price||0):Number(x.price||0);
    const cap=isR?Number(x.available_qty||0):Number(x.stock||0);
    const exist=CAMP_CART.find(v=>v.kind===kind&&String(v.id)===String(id));
    const newQty=(exist?Number(exist.qty):0)+qty;
    if(newQty>cap){_cpToast('库存仅剩 '+cap+' 件');return false;}
    if(exist){exist.qty=newQty;exist.deposit=isR?Number(x.deposit||0)*newQty:0;}
    else CAMP_CART.push({kind:kind,id:id,title:x.name||'商品',coverUrl:_safeUrl(x.coverUrl||x.cover_url),
      spec:x.description||x.subtitle||'',unitLabel:isR?'/天':'/件',unitPrice:unit,qty:qty,
      days:Math.max(1,_nights()),checkIn:isR?CAMP_IN:'',checkOut:isR?(_isDayUse()?_add(CAMP_IN,1):CAMP_OUT):'',
      isDayUse:0,deposit:isR?Number(x.deposit||0)*qty:0});
  }
  _render();
  _cpToast('已加入清单 · 共 '+CAMP_CART.length+' 项');
  return true;
}
window.campAddCart=function(kind,id){_addToCart(kind,id,1);};
window.campBookNow=function(kind,id){
  if(!_addToCart(kind,id,1))return;
  campOpenCheckout();
};

/* ── 统一会员报价（唯一价格真源） ───────────────────────────────────────── */
function _quoteItems(){
  return CAMP_CART.map(x=>({kind:x.kind,id:Number(x.id),qty:Number(x.qty)||1,
    checkIn:x.checkIn||'',checkOut:x.checkOut||'',isDayUse:!!x.isDayUse,
    deposit:Number(x.deposit||0),days:Number(x.days||1)}));
}
function _quoteBody(){
  return {userId:_uid(),items:_quoteItems(),
    clubPoints:CAMP_USE_POINTS?99999999:0,gearPoints:0,
    voucherCodes:CAMP_VOUCHER?[CAMP_VOUCHER]:[]};
}
async function _refreshQuote(){
  if(!CAMP_CART.length){CAMP_QUOTE=null;return;}
  try{CAMP_QUOTE=await _post('/camp-quote',_quoteBody());return;}
  catch(e){
    /* #811：券不满足门槛/不可用时，不把结算页卡死在「计算中」——自动降级回无券价 */
    if(CAMP_VOUCHER){
      CAMP_VOUCHER='';CAMP_VOUCHER_AUTO=false;
      try{CAMP_QUOTE=await _post('/camp-quote',_quoteBody());_cpToast('所选优惠券不可用，已按无券计价');return;}
      catch(_e){CAMP_QUOTE=null;_cpToast(_err(_e));return;}
    }
    CAMP_QUOTE=null;_cpToast(_err(e));
  }
}

/* ── 确认订单页 ─────────────────────────────────────────────────────────── */
function _amountRows(q){
  const rows=[['商品金额','¥'+_money(q.goodsAmount)]];
  if(Number(q.memberDiscount)>0)rows.push(['会员折扣'+(q.memberLevel?'（'+esc(q.memberLevel)+'）':''),'− ¥'+_money(q.memberDiscount)]);
  if(Number(q.clubPointDiscount)>0)rows.push(['活动积分抵扣','− ¥'+_money(q.clubPointDiscount)]);
  if(Number(q.gearPointDiscount)>0)rows.push(['装备积分抵扣','− ¥'+_money(q.gearPointDiscount)]);
  if(Number(q.voucherDiscount)>0)rows.push(['优惠券','− ¥'+_money(q.voucherDiscount)]);
  if(Number(q.depositAmount)>0)rows.push(['押金（离营退还，不参与折扣）','+ ¥'+_money(q.depositAmount)]);
  return rows.map(r=>'<div class="cp-sum__row"><span>'+r[0]+'</span><b>'+r[1]+'</b></div>').join('');
}
function _checkoutHtml(){
  if(!CAMP_CART.length)return '<div class="cp-state">清单是空的，先挑一个营位或商品吧。</div>';
  const g=_guest();
  const items=CAMP_CART.map((x,i)=>'<div class="cp-ck__item">'
    + '<div class="cp-ck__img">'+_mediaHtml(x.coverUrl,'cp-ck__imgfile')+'</div>'
    + '<div class="cp-ck__meta"><div class="cp-ck__t">'+esc(x.title)+'</div>'
    + '<div class="cp-ck__s">'+esc(x.spec||'')+' · ¥'+_money(x.unitPrice)+esc(x.unitLabel||'')+'</div>'
    + '<div class="cp-ck__s">'+(x.checkIn?(_cnDate(x.checkIn)+' → '+_cnDate(x.checkOut||x.checkIn)+' · '+(x.days||1)+(x.kind==='pitch'?' 晚':' 天')):'现场取货')+'</div></div>'
    + '<div class="cp-ck__ops"><div class="cp-stepper small">'
    + '<button type="button" onclick="campCartQty('+i+',-1)">−</button><span>'+x.qty+'</span>'
    + '<button type="button" onclick="campCartQty('+i+',1)">＋</button></div>'
    + '<button class="cp-ck__del" type="button" onclick="campCartDel('+i+')">移除</button></div></div>').join('');
  const q=CAMP_QUOTE;
  const b=CAMP_BENEFITS||{};
  const maxCash=(q&&q.maxRedeemable&&q.maxRedeemable.club)?Number(q.maxRedeemable.club.cash||0):0;
  const benefits='<div class="cp-ck__vip">'+CP_ICON.member
    + '<div><b>'+esc(b.memberLevel||'营地会员')+'</b><em>'
    + (Number(b.memberDiscountPercent)>0?'营地商品立减 '+_money(b.memberDiscountPercent)+'% · ':'')
    + '可用 '+Number(b.clubPoints||0)+' 积分 · '+(b.pointsRedeemRate||100)+' 分抵 ¥1</em></div></div>'
    + '<label class="cp-ck__switch"><span>使用活动积分抵扣<i>本单最多可抵 ¥'+_money(maxCash)+'</i></span>'
    + '<input type="checkbox" '+(CAMP_USE_POINTS?'checked':'')+' onchange="campTogglePoints(this.checked)"></label>'
    + _voucherChipsHtml();
  const amount=q?('<div class="cp-sum">'+_amountRows(q)
    + '<div class="cp-sum__total"><span>应付合计</span><b>¥'+_money(q.total)+'</b></div>'
    + '<div class="cp-sum__note">支付完成后预计获得 '+Number(q.pointsEarned||0)+' 活动积分；押金不计入积分与折扣。</div></div>')
    :'<div class="cp-sum"><div class="cp-sum__row"><span>'+esc('正在按会员权益重新计算…')+'</span></div></div>';
  return '<div class="cp-ck">'+items+benefits
    + '<div class="cp-f__2"><div class="cp-f"><label for="cpGuestName">联系人姓名</label>'
    + '<input id="cpGuestName" value="'+esc(g.name)+'" placeholder="请填写真实姓名" oninput="campGuestInput()"></div>'
    + '<div class="cp-f"><label for="cpGuestPhone">手机号</label>'
    + '<input id="cpGuestPhone" type="tel" value="'+esc(g.phone)+'" placeholder="便于营地联系" oninput="campGuestInput()"></div></div>'
    + _contactChipsHtml()
    + amount
    + '<button class="cp-submit" id="cpOrderSubmit" type="button" '+(q?'':'disabled')+' onclick="campSubmitOrder()">'
    + (q?'提交订单 · ¥'+_money(q.total):'计算中…')+'</button>'
    + '<button class="cp-link" type="button" onclick="campOpenOrders()">查看我的营地订单 ›</button></div>';
}
function _paintCheckout(){const el=$('#cpCheckoutBody');if(el)el.innerHTML=_checkoutHtml();}
/* ── #811 联系人历史 + 优惠券自动匹配 ───────────────────────────────────── */
function _contactChipsHtml(){
  const cs=CAMP_CONTACTS||[];
  if(!cs.length)return '';
  return '<div class="cp-contacts">'+cs.slice(0,4).map((c,i)=>
    '<button type="button" class="cp-cchip" onclick="campUseContact('+i+')">'+esc(c.name)+' '+esc(c.phone)+'</button>').join('')+'</div>';
}
function _voucherChipsHtml(){
  const vs=CAMP_MY_VOUCHERS||[];
  if(!vs.length)return '<div class="cp-ck__coupon"><span class="cp-vempty">暂无可用优惠券</span></div>';
  const chips=vs.slice(0,6).map(v=>{
    const sel=!!CAMP_VOUCHER&&String(v.voucher_code)===String(CAMP_VOUCHER);
    const val=String(v.mode||'fixed')==='discount'?((Number(v.discount_percent)||0)/10)+'折':'¥'+_money(v.cash_value||0);
    return '<button type="button" class="cp-vchip'+(sel?' on':'')+'" onclick="campPickVoucher(\''+esc(v.voucher_code)+'\')">'
      +'<b>'+esc(v.title||'福利券')+'</b><em>'+val+(Number(v.threshold_amount)>0?' · 满¥'+_money(v.threshold_amount):'')+'</em></button>';
  }).join('');
  return '<div class="cp-ck__coupon cp-vchips">'+chips
    +(CAMP_VOUCHER?'<button type="button" class="cp-vchip off" onclick="campPickVoucher(\'\')">不使用</button>':'')+'</div>';
}
/* 按「当前报价的可券金额」估算每张券能抵多少，取最大者为自动匹配结果 */
function _voucherEst(v){
  const q=CAMP_QUOTE;let avail;
  if(q&&Number(q.goodsAmount)>0){
    const el=q.eligibility||{};
    avail=(Number(q.goodsAmount)-Number(q.clubPointDiscount||0)-Number(q.memberDiscount||0))*(Number(el.promoBase)||0)/Number(q.goodsAmount);
  }else{
    avail=CAMP_CART.reduce((s,x)=>s+Number(x.unitPrice||0)*Number(x.qty||1)*(x.kind==='pitch'?Number(x.days||1):1),0);
  }
  avail=Math.max(0,avail);
  const thr=Number(v.threshold_amount||0);
  if(avail<thr)return 0;
  if(String(v.mode||'fixed')==='discount'){
    const dp=Math.max(1,Math.min(99,Number(v.discount_percent)||0));
    return Math.round(avail*(100-dp))/100;
  }
  return Math.min(avail,Math.max(0,Number(v.cash_value||0)));
}
function _autoPickVoucher(){
  if(CAMP_VOUCHER)return; /* 用户已选券则不覆盖 */
  const vs=CAMP_MY_VOUCHERS||[];let best=null,bestVal=0;
  for(const v of vs){const val=_voucherEst(v);if(val>bestVal){best=v;bestVal=val;}}
  if(best&&bestVal>0){CAMP_VOUCHER=best.voucher_code;CAMP_VOUCHER_AUTO=true;}
}
async function _loadBookingAssist(){
  const uid=_uid();
  if(uid){try{CAMP_CONTACTS=await api('/api/public/users/'+uid+'/camp-contacts?club_id='+CLUB,{cache:'no-store'});}catch(e){CAMP_CONTACTS=null;}}
  try{CAMP_MY_VOUCHERS=await api('/api/public/clubs/'+CLUB+'/vouchers?user_id='+uid+'&kind=camp',{cache:'no-store'});}catch(e){CAMP_MY_VOUCHERS=null;}
  if(CAMP_CONTACTS&&CAMP_CONTACTS.length&&!CAMP_GUEST.name&&!CAMP_GUEST.phone)
    CAMP_GUEST={name:String(CAMP_CONTACTS[0].name||''),phone:String(CAMP_CONTACTS[0].phone||'')};
  _autoPickVoucher();
  _paintCheckout();
  if(CAMP_VOUCHER&&CAMP_VOUCHER_AUTO){CAMP_QUOTE=null;await _refreshQuote();_paintCheckout();}
}
window.campOpenCheckout=async function(){
  if(!CAMP_CART.length){_cpToast('清单是空的，先挑一个营位或商品');return;}
  CAMP_GUEST=_guest();
  _showSheet('确认订单','<div id="cpCheckoutBody">'+_checkoutHtml()+'</div>');
  await _refreshQuote();_paintCheckout();
  _loadBookingAssist(); /* 异步补联系人历史与券包，回来后自动匹配最优券并重绘 */
};
window.campGuestInput=function(){
  CAMP_GUEST={name:($('#cpGuestName')||{}).value||'',phone:($('#cpGuestPhone')||{}).value||''};
};
window.campUseContact=function(i){
  const c=(CAMP_CONTACTS||[])[i];if(!c)return;
  CAMP_GUEST={name:String(c.name||''),phone:String(c.phone||'')};_paintCheckout();
};
window.campTogglePoints=async function(on){campGuestInput();CAMP_USE_POINTS=!!on;
  CAMP_QUOTE=null;_paintCheckout();await _refreshQuote();_paintCheckout();};
window.campPickVoucher=async function(code){
  campGuestInput();CAMP_VOUCHER=String(code||'').trim();CAMP_VOUCHER_AUTO=false;
  CAMP_QUOTE=null;_paintCheckout();await _refreshQuote();_paintCheckout();
  if(CAMP_VOUCHER&&CAMP_QUOTE&&!Number(CAMP_QUOTE.voucherDiscount))_cpToast('该券暂不可用或不符合门槛');};
window.campCartQty=async function(i,d){
  const x=CAMP_CART[i];if(!x)return;
  campGuestInput();x.qty=Math.max(1,Math.min(20,Number(x.qty||1)+d));
  if(Number(x.deposit)&&x.kind==='rental')x.deposit=Number(x.deposit)/Math.max(1,(x.qty-d))*x.qty;
  CAMP_QUOTE=null;_paintCheckout();await _refreshQuote();_paintCheckout();_render();};
window.campCartDel=async function(i){campGuestInput();CAMP_CART.splice(i,1);
  if(!CAMP_CART.length){_closeSheet();_render();return;}
  CAMP_QUOTE=null;_paintCheckout();await _refreshQuote();_paintCheckout();_render();};
window.campSubmitOrder=async function(){
  if(CAMP_BUSY)return;
  campGuestInput();
  const g=_guest();
  if(!g.name.trim()||!g.phone.trim()){_cpToast('请填写联系人姓名与手机号');return;}
  if(!CAMP_QUOTE){_cpToast('价格计算中，请稍候');return;}
  CAMP_BUSY=true;const btn=$('#cpOrderSubmit');if(btn){btn.disabled=true;btn.textContent='提交中…';}
  try{
    const body=Object.assign(_quoteBody(),{guestName:g.name.trim(),guestPhone:g.phone.trim()});
    const r=await _post('/camp-orders',body);
    CAMP_ORDER=r;CAMP_CART=[];CAMP_QUOTE=null;CAMP_USE_POINTS=false;CAMP_VOUCHER='';CAMP_VOUCHER_AUTO=false;
    _render();
    _openPaySheet();
  }catch(e){_cpToast(_err(e));if(btn){btn.disabled=false;btn.textContent='重新提交订单';}}
  finally{CAMP_BUSY=false;}
};

/* ── 支付确认页（明确的「下一步」） ─────────────────────────────────────── */
function _payHtml(){
  const o=CAMP_ORDER||{};
  const mm=String(Math.floor(CAMP_PAY_LEFT/60)).padStart(2,'0'),ss=String(CAMP_PAY_LEFT%60).padStart(2,'0');
  return '<div class="cp-pay">'
    + '<div class="cp-pay__badge">'+CP_ICON.clock+'</div>'
    + '<div class="cp-pay__h">订单已提交，待支付</div>'
    + '<div class="cp-pay__amt"><span>¥</span>'+_money(o.total)+'</div>'
    + '<div class="cp-pay__no">订单号 '+esc(o.orderNo||'')+'</div>'
    + '<div class="cp-pay__timer">请在 <b>'+mm+':'+ss+'</b> 内完成支付，超时营位与库存自动释放</div>'
    + '<div class="cp-pay__method"><span class="cp-pay__mico">微</span><div><b>微信支付 / 营地收银</b>'
    + '<em>由支付渠道确认到账后订单自动生效</em></div><i class="cp-pay__radio on"></i></div>'
    + '<div class="cp-pay__note">确认支付后：会员折扣与积分抵扣即时生效，支付成功自动发放消费积分。</div>'
    + '<div id="cpPayStatus" class="cp-pay__status"></div>'
    + '<button class="cp-submit" id="cpPayBtn" type="button" onclick="campPayNow()">确认支付 ¥'+_money(o.total)+'</button>'
    + '<div class="cp-pay__links"><button type="button" onclick="campOpenOrders()">稍后支付 · 我的订单</button>'
    + '<button type="button" onclick="campCancelCurrent()">取消订单</button></div></div>';
}
function _paintPay(){const el=$('#cpPayBody');if(el)el.innerHTML=_payHtml();}
function _openPaySheet(){
  clearInterval(CAMP_PAY_TIMER);
  CAMP_PAY_LEFT=Number((CAMP_ORDER||{}).expiresInSeconds)||1800;
  _showSheet('支付确认','<div id="cpPayBody">'+_payHtml()+'</div>');
  CAMP_PAY_TIMER=setInterval(()=>{
    CAMP_PAY_LEFT=Math.max(0,CAMP_PAY_LEFT-1);
    const t=document.querySelector('.cp-pay__timer b');
    if(t)t.textContent=String(Math.floor(CAMP_PAY_LEFT/60)).padStart(2,'0')+':'+String(CAMP_PAY_LEFT%60).padStart(2,'0');
    if(!CAMP_PAY_LEFT)clearInterval(CAMP_PAY_TIMER);
  },1000);
}
function _payStatus(msg){const el=$('#cpPayStatus');if(el)el.innerHTML=msg;}
window.campPayNow=async function(){
  if(CAMP_BUSY||!CAMP_ORDER)return;
  CAMP_BUSY=true;const btn=$('#cpPayBtn');if(btn){btn.disabled=true;btn.textContent='确认中…';}
  try{
    const r=await api('/api/public/camp-orders/'+CAMP_ORDER.orderId+'/pay',
      {method:'POST',headers:{'Content-Type':'application/json'},body:'{}',cache:'no-store'});
    clearInterval(CAMP_PAY_TIMER);_showSuccess(r);
  }catch(e){
    const msg=_err(e);
    if(/生产环境|渠道/.test(msg)){
      _payStatus('<div class="cp-pay__warn">当前为正式环境，需由支付渠道确认到账。完成付款后点下方按钮查询状态，系统不会把未到账订单标记为已支付。</div>'
        + '<button class="cp-pay__poll" type="button" onclick="campPollOrder()">我已付款，查询状态</button>');
    }else _payStatus('<div class="cp-pay__warn">'+esc(msg)+'</div>');
    if(btn){btn.disabled=false;btn.textContent='重试确认支付';}
  }finally{CAMP_BUSY=false;}
};
window.campPollOrder=async function(){
  if(!CAMP_ORDER)return;
  try{
    const d=await api('/api/public/camp-orders/'+CAMP_ORDER.orderId+'?user_id='+_uid(),{cache:'no-store'});
    if(d.order&&d.order.status==='paid'){clearInterval(CAMP_PAY_TIMER);_showSuccess({orderId:CAMP_ORDER.orderId,
      total:d.order.total,pointsEarned:d.order.pointsEarned});}
    else _payStatus('<div class="cp-pay__warn">尚未查询到到账记录，请勿重复付款。</div>'
      +'<button class="cp-pay__poll" type="button" onclick="campPollOrder()">再次查询</button>');
  }catch(e){_payStatus('<div class="cp-pay__warn">'+esc(_err(e))+'</div>');}
};
window.campCancelCurrent=async function(){
  if(!CAMP_ORDER)return;
  await campCancelOrder(CAMP_ORDER.orderId,true);
};
function _showSuccess(r){
  const earned=Number(r.pointsEarned||0);
  _showSheet('支付成功','<div class="cp-ok"><div class="cp-ok__ico">'+CP_ICON.check+'</div>'
    + '<h2>预订成功</h2><p class="cp-ok__amt">实付 ¥'+_money(r.total||(CAMP_ORDER||{}).total)+'</p>'
    + '<div class="cp-ok__card"><div><span>订单号</span><b>'+esc((CAMP_ORDER||{}).orderNo||'')+'</b></div>'
    + '<div><span>订单状态</span><b>已支付</b></div>'
    + '<div><span>获得活动积分</span><b>+'+earned+' 分</b></div>'
    + '<div><span>营位 / 库存</span><b>已锁定</b></div></div>'
    + '<p class="cp-ok__tip">出行信息可在「我的订单」查看；到营地报订单号或手机号即可。</p>'
    + '<button class="cp-submit" type="button" onclick="campOpenOrders()">查看我的订单</button>'
    + '<button class="cp-link" type="button" onclick="campCloseSheet();loadCampPage()">继续逛营地 ›</button></div>');
}

/* ── 我的营地订单 ───────────────────────────────────────────────────────── */
const CAMP_ST={pending_payment:'待支付',paid:'已支付',cancelled:'已取消',expired:'已过期'};
const CAMP_ST_CN={pending_payment:'待支付',paid:'已支付 · 待出行',cancelled:'已取消',expired:'已超时'};
function _ordersHtml(orders){
  if(!orders.length)return '<div class="cp-state">还没有营地订单，去挑个营位吧。</div>';
  return '<div class="cp-orders">'+orders.map(o=>{
    const st=o.status||'';
    const items=(o.items||[]).map(it=>'<div class="cp-order__it"><span>'+esc(it.title||'')+' × '+it.qty+'</span>'
      +'<b>¥'+_money(it.subtotal)+'</b></div>').join('');
    return '<div class="cp-order"><div class="cp-order__hd"><b>'+esc(o.orderNo||'')+'</b>'
      +'<span class="cp-order__st '+esc(st)+'">'+esc(CAMP_ST_CN[st]||CAMP_ST[st]||st)+'</span></div>'
      +'<div class="cp-order__date">'+esc(o.createdAt||'')+' · 联系人 '+esc(o.guestName||'')+'</div>'
      + items
      +'<div class="cp-order__sum">商品 ¥'+_money(o.goodsAmount)
      +(Number(o.memberDiscount)?' − 会员 ¥'+_money(o.memberDiscount):'')
      +(Number(o.clubPointDiscount)?' − 积分 ¥'+_money(o.clubPointDiscount):'')
      +(Number(o.depositAmount)?' + 押金 ¥'+_money(o.depositAmount):'')
      +' · <b>合计 ¥'+_money(o.total)+'</b></div>'
      +(st==='pending_payment'?'<div class="cp-order__ops">'
        +'<button type="button" onclick="campResumePay('+o.id+')">去支付</button>'
        +'<button type="button" onclick="campCancelOrder('+o.id+')">取消订单</button></div>':'')
      +(st==='paid'?'<div class="cp-order__earn">已获得 '+Number(o.pointsEarned||0)+' 活动积分</div>':'')
      +'</div>';
  }).join('')+'</div>';
}
window.campOpenOrders=async function(){
  clearInterval(CAMP_PAY_TIMER);
  _showSheet('我的营地订单','<div id="cpOrdersBody"><div class="cp-state">正在读取订单…</div></div>');
  try{
    const d=await _get('/camp-orders?user_id='+_uid());
    const el=$('#cpOrdersBody');if(el)el.innerHTML=_ordersHtml(d.orders||[]);
  }catch(e){const el=$('#cpOrdersBody');if(el)el.innerHTML='<div class="cp-state">'+esc(_err(e))+'</div>';}
};
window.campResumePay=async function(oid){
  try{
    const d=await api('/api/public/camp-orders/'+oid+'?user_id='+_uid(),{cache:'no-store'});
    if(d.order.status!=='pending_payment'){_cpToast('该订单已无需支付');campOpenOrders();return;}
    CAMP_ORDER={orderId:oid,orderNo:d.order.orderNo,total:d.order.total,expiresInSeconds:1800};
    _openPaySheet();
  }catch(e){_cpToast(_err(e));}
};
window.campCancelOrder=async function(oid,fromPay){
  try{
    await api('/api/public/camp-orders/'+oid+'/cancel',
      {method:'POST',headers:{'Content-Type':'application/json'},body:'{}',cache:'no-store'});
    clearInterval(CAMP_PAY_TIMER);_cpToast('订单已取消，库存与积分已释放');
    if(fromPay)CAMP_ORDER=null;
    campOpenOrders();
  }catch(e){_cpToast(_err(e));}
};

/* ── 兼容旧 inline 名（历史页面/脚本可能引用） ─────────────────────────── */
window.campOpenBook=function(pid){campOpenDetail('pitch',pid);};
window.campQty=function(){campDetailQty(1);};
window.campRecalc=function(){};
window.campBook=function(pid){campBookNow('pitch',pid);};

/* ── 抽屉 / 全屏基础设施 ───────────────────────────────────────────────── */
function _showSheet(title,body){
  _closeSheet(true);
  const mask=document.createElement('div');mask.className='cp-mask';mask.id='cpMask';mask.onclick=_closeSheet;
  const sh=document.createElement('div');sh.className='cp-sheet';sh.id='cpSheet';
  sh.innerHTML='<div class="cp-sheet__bar"></div><div class="cp-sheet__head">'
    + '<span class="cp-sheet__title">'+esc(title)+'</span>'
    + '<button class="cp-sheet__x" type="button" onclick="campCloseSheet()" aria-label="关闭">×</button></div>'+body;
  document.body.appendChild(mask);document.body.appendChild(sh);
  requestAnimationFrame(()=>{mask.classList.add('open');sh.classList.add('open');});
}
function _closeSheet(now){
  const m=document.querySelector('#cpMask'),s=document.querySelector('#cpSheet');
  if(!m&&!s)return;
  if(m)m.classList.remove('open');if(s)s.classList.remove('open');
  setTimeout(()=>{m&&m.remove();s&&s.remove();},now?0:300);
}
window.campCloseSheet=function(){_closeSheet();clearInterval(CAMP_PAY_TIMER);};
document.addEventListener('keydown',e=>{
  if(e.key!=='Escape')return;
  if(document.querySelector('#cpDetail')){campCloseDetail();return;}
  if(document.querySelector('#cpShopHost')){_closeShopList();return;}
  _closeSheet();
});

applyModuleSwitches();
