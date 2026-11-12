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
let CAMP_ZONES=[];             // #826：后台自定义的 C 端分区与图标配置
let CAMP_DETAIL_PLAN=0,CAMP_DETAIL_UNITS=0;   // #822：租赁计费项选中项与数量
let CAMP_IN='',CAMP_OUT='';
let CAMP_BENEFITS=null,CAMP_CART=[],CAMP_QUOTE=null,CAMP_ORDER=null;
let CAMP_USE_POINTS=false,CAMP_VOUCHER='',CAMP_GUEST={name:'',phone:''};
let CAMP_MY_VOUCHERS=null,CAMP_CONTACTS=null,CAMP_VOUCHER_AUTO=false; /* #811 联系人历史+券自动匹配 */
let CAMP_CAL={in:'',out:'',anchor:''};
let CAMP_DETAIL=null,CAMP_DETAIL_QTY=1;
let CAMP_HERO_IDX=0,CAMP_TOAST_T=null,CAMP_PAY_TIMER=null,CAMP_PAY_LEFT=0,CAMP_HERO_T=null;
let CAMP_BUSY=false,CAMP_LV=null,CAMP_INTRO=null;
let CAMP_ME={name:'',avatar:''};   /* #827 会员头像与「我的」同源 */

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
  if(s.startsWith('/api/public/clubs/')&&(s.includes('/camp-media-file/')||s.includes('/camp-src-file/')))return s;
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
/* #827 会员头像：与「我的」页用同一个来源（公开资料的 avatarUrl）。
   营地页不依赖 me.js 是否已经加载过 —— 自己取一次，取不到再回落 ME.avatar。 */
function _meAvatarUrl(){
  if(CAMP_ME&&CAMP_ME.avatar)return CAMP_ME.avatar;
  try{if(typeof ME!=='undefined'&&ME&&ME.avatar)return ME.avatar;}catch(e){}
  return '';
}
function _meName(){
  if(CAMP_ME&&CAMP_ME.name)return CAMP_ME.name;
  try{if(typeof ME!=='undefined'&&ME&&ME.name)return ME.name;}catch(e){}
  return '远拓会员';
}

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
  /* #827：取一次公开资料拿头像，让会员条与「我的」显示同一个头像。 */
  try{const prof=await api('/api/public/users/'+_uid()+'/profile',{cache:'no-store'});
      if(prof)CAMP_ME={name:(prof.name||''),avatar:(prof.avatarUrl||'')};}catch(e){}
  /* #826：分区与图标由后台自定义（老板改名字/图标/隐藏/排序）。读不到就退回内置默认。 */
  try{const z=await api('/api/public/clubs/'+CLUB+'/camp-zones',{cache:'no-store'});
      CAMP_ZONES=(z&&z.zones)||[];}catch(e){CAMP_ZONES=[];}
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
  /* #832 单元化：按「区间内最少剩余单元数」判定可订 qty 个；满员/不足都算不可订。 */
  const minLeft=stay.length?Math.min.apply(null,stay.map(d=>(d.unitsLeft==null?1:Number(d.unitsLeft)))):0;
  const payable=Math.round(stay.reduce((a,d)=>a+(Number(d.price)||0),0)*qty*100)/100;
  const ok=blocked.length===0&&minLeft>=qty;
  let reason='';
  if(blocked.length)reason='含不可订日期 '+blocked.map(b=>b.day.slice(5)).join('、');
  else if(minLeft<qty)reason='所选日期仅剩 '+minLeft+' 个可订';
  return {ok,blocked,stay,payable,minLeft,unitQty:p.unitQty||1,total:Math.round((payable+dep)*100)/100,reason};
}
/* #832 某营位在当前选中区间的最少剩余单元（用于卡片「剩N/10」） */
function _unitsLeftOf(pid){
  const q=_quote(pid,1,0);
  return (q&&q.stay&&q.stay.length)?q.minLeft:0;
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
  /* #827 会员条头像改用本人头像（与「我的」一致），没有头像时用姓名首字母。 */
  const _ava=_meAvatarUrl(),_nm=_meName();
  const memberIco=_ava
    ? '<span class="cp-member__ico cp-member__ico--ava"><img src="'+esc(_ava)+'" alt=""></span>'
    : '<span class="cp-member__ico cp-member__ico--letter">'+esc(_nm.slice(0,1))+'</span>';
  const memberBar=`<div class="cp-member" onclick="campOpenOrders()" role="button" tabindex="0">
      ${memberIco}
      <span class="cp-member__txt"><b>${esc(b.memberLevel||'营地会员')}</b>
        <em>${Number(b.memberDiscountPercent||0)>0?'营地商品立减 '+_money(b.memberDiscountPercent)+'% · ':''}${Number(b.clubPoints||0)} 积分可用 · ${b.pointsRedeemRate||100} 分抵 ¥1</em></span>
      <span class="cp-member__more">我的订单 ›</span></div>`;

  root.innerHTML='<div class="cp-page" id="cpPage">'
    + '<div class="cp-hero">'
      + (hero?'<img class="cp-hero__img" src="'+esc(hero)+'" alt="'+esc(c.name||'营地')+'"'+_focalStyle((c.focalPoints||[])[CAMP_HERO_IDX]||c.coverFocal)+'>':'<div class="cp-hero__fallback"></div>')
      + '<div class="cp-hero__shade"></div>'
      + '<div class="cp-hero__rail"><span class="cp-hero__tag">'+CP_ICON.tent+' CAMP · 营地预订</span>'
      + (CAMP_LIST.length>1?'<select id="campSel" onchange="campOnChange()" class="cp-hero__sel">'+selOpts+'</select>':'')
      + '</div>'
      /* #835 hero 纯视觉：不再压营地名/slogan 文字（营地名称保留在下方信息卡），保留轮播圆点 */
      + (gallery.length>1?'<div class="cp-hero__dots">'+gallery.map((_,i)=>'<i class="cp-hero__dot '+(i===CAMP_HERO_IDX?'on':'')+'" onclick="campHero('+i+')"></i>').join('')+'</div>':'')
    + '</div>'
    + '<div class="cp-info"><div class="cp-info__row"><div class="cp-info__main">'
      /* #825 删除重复提示：头图上的「营地介绍 ›」按钮已撤掉，「详细介绍」入口
         只保留信息卡这一处；名字后面那个内嵌箭头也和文案里的 › 重复，一并去掉。 */
      + '<div class="cp-info__name" role="button" tabindex="0" onclick="campOpenIntro()">'+esc(c.name||'营地')
        +'<em>查看详细介绍 ›</em></div>'
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
  /* #835 多图自动轮播：5s 一张循环；重渲染（筛选/日期变化）会重建 DOM，
     这里统一重置定时器，保证只有一个在跑。手动点圆点后从下一张继续。 */
  if(CAMP_HERO_T){clearInterval(CAMP_HERO_T);CAMP_HERO_T=null;}
  if(gallery.length>1){
    CAMP_HERO_T=setInterval(()=>{
      /* 页面不可见或打开了详情浮层时不轮播，回来后自动续 */
      if(document.hidden||document.querySelector('#cpDetailHost')||!document.querySelector('.cp-hero__img'))return;
      CAMP_HERO_IDX=(CAMP_HERO_IDX+1)%gallery.length;
      _render();
    },5000);
  }
}

/* #826：分区宫格读后台配置。配置读不到时退回内置默认（保证旧数据/接口异常也能正常显示）。 */
function _defaultZones(){
  const types=[...new Set(CAMP_PITCHES.map(p=>p.pitch_type||'tent'))];
  const label={tent:'帐篷区',rv:'房车区',cabin:'木屋区',day:'日间营'};
  const zs=[{key:'all',name:'全部营位',target:'all',icon:'all',emoji:''}];
  types.forEach(t=>zs.push({key:'t_'+t,name:label[t]||_pitchTypeLabel(t),target:t,icon:(CP_ICON[t]?t:'all'),emoji:''}));
  zs.push({key:'rental',name:'装备租赁',target:'rental',icon:'rental',emoji:''},
          {key:'store',name:'营地小站',target:'store',icon:'store',emoji:''},
          {key:'service',name:'联系客服',target:'service',icon:'service',emoji:''});
  return zs;
}
function _zoneIconHtml(z){
  const lib=window.CAMP_ICON_LIB||{};
  if(z.emoji)return '<span class="cp-grid__ico cp-grid__ico--emoji">'+esc(z.emoji)+'</span>';
  const svg=lib[z.icon]||lib.all||CP_ICON.all;
  return '<span class="cp-grid__ico">'+svg+'</span>';
}
function _gridHtml(){
  const zones=(CAMP_ZONES&&CAMP_ZONES.length)?CAMP_ZONES:_defaultZones();
  const have={}; CAMP_PITCHES.forEach(p=>{have[p.pitch_type||'tent']=1;});
  const items=[];
  zones.forEach(z=>{
    if(z.visible===false)return;
    const tgt=String(z.target||z.key||'all');
    /* 指向某种营位类型、但当前营地没有该类型营位 → 不显示（点了也是空的）。 */
    if(tgt!=='all'&&tgt!=='rental'&&tgt!=='store'&&tgt!=='service'&&!have[tgt])return;
    items.push({t:tgt,n:String(z.name||z.key||''),z:z});
  });
  return items.slice(0,8).map(x=>{
    const act=CAMP_FILTER===x.t?' active':'';
    const jump=['rental','store','service'].includes(x.t);
    const click=jump?"campJump('"+x.t+"')":"campFilter('"+x.t+"')";
    return '<button class="cp-grid__item'+act+'" type="button" onclick="'+click+'">'
      + _zoneIconHtml(x.z) +'<span>'+esc(x.n)+'</span></button>';
  }).join('');
}

/* #813 焦点对齐：后台裁剪算出的主体位置，C 端用它设置 object-position，
   避免不同容器比例二次 cover 裁切后「天空占满 / 主体被切掉」。 */
function _focalStyle(f){
  if(!f)return '';
  const x=Math.max(0,Math.min(100,(Number(f.x!=null?f.x:.5)||0)*100));
  const y=Math.max(0,Math.min(100,(Number(f.y!=null?f.y:.5)||0)*100));
  return ' style="object-position:'+x.toFixed(1)+'% '+y.toFixed(1)+'%"';
}
window._focalStyle=_focalStyle;   /* #813 暴露给其它视图/调试复用 */
function _focalPairs(d){
  const urls=[d&&d.coverUrl].concat((d&&d.gallery)||[]);
  const foc=[d&&d.coverFocal].concat((d&&d.focalPoints)||[]);
  return urls.map((u,i)=>({u:_safeUrl(u),f:foc[i]||null})).filter(p=>p.u);
}
function _mediaHtml(url,cls,focal){
  const u=_safeUrl(url);
  return u?'<img class="'+cls+'" src="'+esc(u)+'" alt=""'+_focalStyle(focal)+'>':'<div class="cp-card__ph"></div>';
}

function _pitchesHtml(){
  const list=CAMP_FILTER==='all'?CAMP_PITCHES:CAMP_PITCHES.filter(p=>(p.pitch_type||'tent')===CAMP_FILTER);
  if(!list.length)return '<div class="cp-empty">这个分区暂时没有可订营位，看看其它分区吧。</div>';
  const campName=_curCamp().name||'营地';
  return list.map(p=>{
    const q=_quote(p.id,1,0),off=!q.ok,price=_pitchPrice(p.id);
    /* #832 单元化：unitQty>1 显示「剩 N/M」徽章；=1 保持旧徽章 */
    const uq=Number(p.unitQty||1);
    let badge;
    if(uq>1){
      const left=q.stay&&q.stay.length?q.minLeft:null;
      badge=(left!=null&&left<=0)?'<span class="cp-card__badge warn">已订满</span>'
        :'<span class="cp-card__badge">剩 '+(left==null?'-':left)+'/'+uq+'</span>';
    }else badge=off?'<span class="cp-card__badge warn">已订满</span>':'<span class="cp-card__badge">'+esc(campName)+'</span>';
    const spec=_pitchTypeLabel(p.pitch_type)+' · 适用 '+(p.capacity||1)+' 人 · '+(p.is_overnight?'过夜':'日间当天')+(uq>1?(' · '+uq+' 个'):'');
    const tags=[p.code,p.pitch_type==='day'?'不过夜':'轻户外'].filter(Boolean)
      .map(t=>'<span class="cp-chip" style="padding:2px 6px;font-size:9.5px">'+esc(t)+'</span>').join('');
    return '<div class="cp-card'+(off?' is-off':'')+'">'
      + '<div class="cp-card__media" onclick="campOpenDetail(\'pitch\','+p.id+')">'+_mediaHtml(p.coverUrl||p.cover_url,
          'cp-card__img',p.coverFocal)+badge+'</div>'
      + '<div class="cp-card__body"><div class="cp-card__title" onclick="campOpenDetail(\'pitch\','+p.id+')">'+esc(p.name||'营位')+'</div>'
      + '<div class="cp-card__spec">'+esc(spec)+'</div>'
      + '<div class="cp-card__tags">'+tags+'</div>'
      + '<div class="cp-card__foot"><div class="cp-price"><small>¥</small>'+_money(price)+'<em>'+(p.is_overnight?'/晚':'/天')+'</em></div>'
      + '<button class="cp-book" type="button" '+(off?'disabled':'')+' onclick="campBookNow(\'pitch\','+p.id+')">'+(off?'已满':'预订')+'</button></div>'
      + '</div></div>';
  }).join('');
}

/* 装备租赁 / 营地小站：商品化两列网格（图片、规格、价格、押金、库存、加购）
   #824 小站商品不显示库存 / 计价单位 / 场地尺寸等仓储或营位字段 —— 与营位商品明确区分。 */
/* #822：租赁商品价格按老板自定义的计费项走 —— 卡片上显示第一项（最低价优先）与它的单位。 */
function _rentalPlansOf(x){return (x&&Array.isArray(x.pricing)&&x.pricing.length)?x.pricing:null;}
function _shopPriceUnit(kind,x){
  if(kind!=='rental')return '';
  const pl=_rentalPlansOf(x);
  return pl?'/'+esc(pl[0].unitLabel||'天'):'/天';
}
function _shopPriceOf(x,kind){
  if(kind!=='rental')return Number(x.price||0);
  const pl=_rentalPlansOf(x);
  return pl?Number(pl[0].price||0):Number(x.daily_price||0);
}
function _shopSub(x,kind){
  if(kind==='rental'){
    const pl=_rentalPlansOf(x);
    const multi=pl&&pl.length>1?(' · '+pl.length+' 种计费'):'';
    return '押金 ¥'+_money(x.deposit)+' · 可租 '+(x.available_qty||0)+' 件'+multi;
  }
  return esc(x.category||'营地小站');
}
function _shopSpec(x){
  /* 与详情页同一套过滤：营位 / 仓储字段（库存、计价单位、场地尺寸…）不当商品卖点。 */
  const vis=(x.specs||[]).filter(s=>s&&s.label&&!CAMP_HIDE_SPEC.test(String(s.label).trim()));
  if(vis.length)return vis[0].label+' '+vis[0].value;
  return x.description||x.subtitle||'';
}
function _shopHtml(kind){
  const list=CAMP_GOODS[kind]||[];
  if(!list.length)return '';
  const isRental=kind==='rental';
  const title=isRental?'装备租赁':'营地小站';
  const hint=isRental?'计费方式自选（按小时 / 按天 / 按次…）· 押金离营退还':'营地自营 · 现场取货';
  const cards=list.map(x=>{
    const price=_shopPriceOf(x,kind);
    const spec=_shopSpec(x);
    const off=isRental?!(Number(x.available_qty||0)>0):!(Number(x.stock||0)>0);
    return '<div class="cp-prod" onclick="campOpenDetail(\''+kind+'\','+x.id+')">'
      + '<div class="cp-prod__media">'+_mediaHtml(x.coverUrl||x.cover_url,'cp-prod__img',x.coverFocal)
      + (off?'<span class="cp-prod__off">已租罄</span>':'')+'</div>'
      + '<div class="cp-prod__body"><div class="cp-prod__t">'+esc(x.name||'商品')+'</div>'
      + (spec?'<div class="cp-prod__spec">'+esc(String(spec).slice(0,28))+'</div>':'')
      + '<div class="cp-prod__sub">'+_shopSub(x,kind)+'</div>'
      + '<div class="cp-prod__foot"><div class="cp-price"><small>¥</small>'+_money(price)+'<em>'+_shopPriceUnit(kind,x)+'</em></div>'
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
/* #823 二级页商品卡：改成「左图右文」的横版列表 —— 竖版双列在只有一两件商品时
   会空出整整一列，卡片还被网格拉伸到容器高度，卡内一大片空白。横版一行一件，
   铺满整屏宽度，也更像商城列表页。 */
function _shopCardHtml(x,kind){
  const isR=kind==='rental';
  const price=_shopPriceOf(x,kind);
  const spec=_shopSpec(x);
  const off=isR?!(Number(x.available_qty||0)>0):!(Number(x.stock||0)>0);
  return '<div class="cp-pl" onclick="campOpenDetail(\''+kind+'\','+x.id+')">'
    + '<div class="cp-pl__media">'+_mediaHtml(x.coverUrl||x.cover_url,'cp-pl__img',x.coverFocal)
    + (off?'<span class="cp-pl__off">已租罄</span>':'')+'</div>'
    + '<div class="cp-pl__body"><div class="cp-pl__t">'+esc(x.name||'商品')+'</div>'
    + (spec?'<div class="cp-pl__spec">'+esc(String(spec).slice(0,40))+'</div>':'')
    + '<div class="cp-pl__sub">'+_shopSub(x,kind)+'</div>'
    + '<div class="cp-pl__foot"><div class="cp-price"><small>¥</small>'+_money(price)+'<em>'+_shopPriceUnit(kind,x)+'</em></div>'
    + '<button class="cp-pl__add" type="button" '+(off?'disabled':'')
    + ' onclick="event.stopPropagation();campAddCart(\''+kind+'\','+x.id+')">＋</button></div></div></div>';
}
function _paintShopList(kind,cat){
  const isR=kind==='rental';
  const title=isR?'装备租赁':'营地小站';
  const cats=_shopCats(kind);
  const list=(CAMP_GOODS[kind]||[]).filter(x=>!cat||x.category===cat);
  const hero=(CAMP_GOODS[kind]||[]).find(x=>_safeUrl(x.coverUrl||x.cover_url))||list[0]||{};
  const heroUrl=_safeUrl(hero.coverUrl||hero.cover_url);
  /* #825 去掉重复大图：头图往往就是第一件商品的图，再单独铺一条横幅等于同一张图
     连着出现两遍。现在只在头图与列表首图不同时才显示横幅。 */
  const firstCover=_safeUrl(((list[0]||{}).coverUrl)||((list[0]||{}).cover_url));
  const showHero=!!heroUrl&&heroUrl!==firstCover;
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
    + (showHero?'<div class="cp-lv__hero"><img src="'+esc(heroUrl)+'" alt=""'+_focalStyle(hero.coverFocal)+'></div>':'')
    + catChips
    + '<div class="cp-lv__meta">共 '+list.length+' 项 · '+(isR?'计费方式自选（按小时 / 按天 / 按次…）· 押金离营退还':'营地自营 · 现场取货')+'</div>'
    + '<div class="cp-lv__list">'+cards+'</div>'
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
    focalPoints:x.focalPoints||[],coverFocal:x.coverFocal||null,
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
  /* #821 请求在途时用户可能已切走 tab，别把全屏详情层挂到别的视图上 */
  const v=document.getElementById('wcamp');
  if(!v||v.style.display==='none')return;
  CAMP_DETAIL=d||_listFallback(kind,id);
  CAMP_DETAIL_QTY=1;
  CAMP_DETAIL_PLAN=0;CAMP_DETAIL_UNITS=0;
  _paintDetail();
};
/* #824 规格区过滤：库存 / 计价单位 / 场地尺寸 / 接待人数 这类是营位或仓储字段，
   不该出现在装备租赁与小站商品上（后台历史数据里经常混着这些），一律不显示。 */
const CAMP_HIDE_SPEC=/^(库存|余量|计价单位|单位|场地尺寸|场地面积|接待人数|适用人数|可住人数|售价|价格)$/;
function _detailSpecs(d){
  const rows=[];
  const vis=(d.specs||[]).filter(s=>s&&s.label&&!CAMP_HIDE_SPEC.test(String(s.label).trim()));
  if(d.kind==='pitch'){
    rows.push(['营位类型',_pitchTypeLabel(d.pitchType)],['可住人数',(d.capacity||1)+' 人'],
      ['营期',d.isOvernight?'可过夜（跨日锁定）':'日间营 · 当天结束'],['计价单位',d.unitLabel||'/晚']);
  }else if(d.kind==='rental'){
    /* #822：老板自定义了计费项就逐条列出来，别再只写一个「日租金」。 */
    const pl=_rentalPlansOf(d);
    if(pl) pl.forEach(x=>rows.push([x.name||'租金','¥'+_money(x.price)+' / '+(x.unitLabel||'天')]));
    else rows.push(['日租金','¥'+_money(d.dailyPrice)+' / 天']);
    rows.push(['押金','¥'+_money(d.deposit)+' · 离营退还'],
      ['可租数量',(d.stock||0)+' 件'],['租期',_isDayUse()?'当天':(_nights()+' 晚')]);
  }else{
    /* #824 小站商品：规格逐条列，不额外塞「规格」摘要行（那行会把第一条规格
       的标签吃掉，只剩一个值，看着像「规格 100 件」这种怪东西）。 */
    if(vis.length)vis.forEach(s=>rows.push([String(s.label),String(s.value||'')]));
    else rows.push(['规格','标准规格']);
    rows.push(['取货','营地小站 · 现场取货']);
  }
  if(d.kind!=='store')vis.forEach(s=>rows.push([String(s.label),String(s.value||'')]));
  return rows.map(r=>'<div class="cp-dt__spec"><span>'+esc(r[0])+'</span><b>'+esc(r[1])+'</b></div>').join('');
}
/* #822 租赁计费项：老板自定义「项目 + 价格 + 时间单位」，顾客直接点选 + 填数量。 */
function _planMaxUnits(p){
  const u=String((p||{}).unit||'day');
  if(u==='hour')return 24;                       // 一天最多 24 小时
  if(u==='times'||u==='item')return 20;          // 按次 / 按件
  return 30;                                      // 按天 / 晚 / 半天
}
function _planDefaultUnits(p){
  if(!p)return 1;
  const u=String(p.unit||'day');
  return (u==='day'||u==='night'||u==='halfday')?Math.max(1,_nights()):1;
}
function _rentalPlanBlock(plans){
  const i=Math.max(0,Math.min(CAMP_DETAIL_PLAN,plans.length-1));
  const p=plans[i]||{};
  return '<div class="cp-dt__block"><h3>计费方式</h3>'
    +'<div class="cp-plans">'+plans.map((x,j)=>'<button type="button" class="cp-plan'+(j===i?' on':'')+'" onclick="campPickPlan('+j+')">'
      +'<b>'+esc(x.name||'租金')+'</b><span>¥'+_money(x.price)+' / '+esc(x.unitLabel||'天')+'</span></button>').join('')+'</div>'
    +'<div class="cp-planqty"><span>租赁数量</span>'
      +'<button type="button" onclick="campPlanUnits(-1)">−</button><b id="dtPlanQty">'+CAMP_DETAIL_UNITS+'</b>'
      +'<button type="button" onclick="campPlanUnits(1)">＋</button><em id="dtPlanUnit">'+esc(p.unitLabel||'天')+'</em></div>'
    +'<div class="cp-planhint" id="dtPlanHint">小计 ¥'+_money((Number(p.price)||0)*CAMP_DETAIL_UNITS*CAMP_DETAIL_QTY)+'</div>'
    +'</div>';
}
function _syncRentalPlan(){
  const plans=(CAMP_DETAIL&&CAMP_DETAIL.pricing)||[];
  const p=plans[CAMP_DETAIL_PLAN]||{price:0,unitLabel:'天'};
  const q=document.getElementById('dtPlanQty'); if(q)q.textContent=CAMP_DETAIL_UNITS;
  const u=document.getElementById('dtPlanUnit'); if(u)u.textContent=p.unitLabel||'天';
  const h=document.getElementById('dtPlanHint');
  if(h)h.textContent='小计 ¥'+_money((Number(p.price)||0)*CAMP_DETAIL_UNITS*CAMP_DETAIL_QTY);
  const pe=document.getElementById('dtPrice');
  if(pe&&p)pe.innerHTML='<span>¥</span>'+_money(p.price)+'<em>/'+esc(p.unitLabel||'天')+'</em>';
}
window.campPickPlan=function(i){
  const plans=(CAMP_DETAIL&&CAMP_DETAIL.pricing)||[];
  if(!plans[i])return;
  CAMP_DETAIL_PLAN=i;
  CAMP_DETAIL_UNITS=_planDefaultUnits(plans[i]);
  const host=document.getElementById('cpDetailHost');
  if(host)host.querySelectorAll('.cp-plan').forEach((b,j)=>b.classList.toggle('on',j===i));
  _syncRentalPlan();
};
window.campPlanUnits=function(delta){
  const plans=(CAMP_DETAIL&&CAMP_DETAIL.pricing)||[];
  const p=plans[CAMP_DETAIL_PLAN]||{};
  CAMP_DETAIL_UNITS=Math.max(1,Math.min(_planMaxUnits(p),CAMP_DETAIL_UNITS+delta));
  _syncRentalPlan();
};
function _detailPolicies(d){
  const ps=(d.policies||[]).filter(Boolean);
  const base=[{title:'预订说明',text:'下单后营位 / 库存锁定 30 分钟，超时未支付自动释放。'},
              {title:'退改规则',text:'未支付订单可直接取消；已支付订单请联系营地处理。'}];
  return base.concat(ps).map(p=>{
    const t=(typeof p==='string')?p:(p.title||''),x=(typeof p==='string')?'':(p.text||'');
    return '<div class="cp-dt__pol"><b>'+esc(t)+'</b>'+(x?'<p>'+esc(x)+'</p>':'')+'</div>';
  }).join('');
}
/* ── #814 图文详情：资料图统一媒体池 + 可复用版式模板 ──────────────────────── */
function _shortSlogan(c){
  // #814 修正：头图只显示真正的短 tagline（slogan/subtitle），
  // 绝不再兜底长 introText——长介绍统一在 camp-info 独立页。
  const s=String((c&&(c.slogan||c.subtitle))||'选一处营位，把日子过成山野的样子')
    .replace(/\s+/g,' ').trim();
  return s.length>30?s.slice(0,30)+'…':s;
}
function _tplName(id){return ({classic:'经典图文',magazine:'杂志大图',spec:'参数优先'}[id]||'经典图文');}
function _aiMedia(d){
  /* 优先用 AI 生成时登记的媒体池（含上传资料图）；老数据回退到商品图廊 */
  const ai=d&&d.aiDetail;
  if(ai&&Array.isArray(ai.media)&&ai.media.length){
    return ai.media.map(m=>({u:_safeUrl(m&&m.url),f:(m&&m.focal)||null,
      cap:String((m&&m.caption)||''),source:(m&&m.source)||'reference'})).filter(p=>p.u);
  }
  return _focalPairs(d);
}
function _paintHighlights(d){
  const ai=d.aiDetail;
  if(!ai||!Array.isArray(ai.highlights)||!ai.highlights.length)return '';
  return '<div class="cp-dt__hl">'+ai.highlights.map(h=>'<span class="cp-chip">'+esc(String(h))+'</span>').join('')+'</div>';
}
function _paintAiSections(d){
  const ai=d.aiDetail;
  if(!ai||!Array.isArray(ai.sections)||!ai.sections.length)return '';
  const tpl=['classic','magazine','spec'].indexOf(String(ai.templateId||'classic'))>=0?String(ai.templateId):'classic';
  const media=_aiMedia(d);
  const blocks=(ai.sections||[]).map(sec=>{
    const refs=Array.isArray(sec.mediaRefs)?sec.mediaRefs:[];
    const imgs=refs.map(i=>media[Number(i)]).filter(Boolean)
      .map(x=>'<figure class="cp-dt__fig"><img src="'+esc(x.u)+'" alt="" loading="lazy"'+_focalStyle(x.f)+'></figure>').join('');
    const layout=['full','split','grid','text'].indexOf(sec.layout)>=0?sec.layout:'grid';
    return '<section class="cp-dt__sec cp-dt__sec--'+layout+'">'
      + '<div class="cp-dt__sectxt">'
      + (sec.headline?'<h4>'+esc(String(sec.headline))+'</h4>':'')
      + (sec.body?'<p>'+esc(String(sec.body))+'</p>':'')
      + '</div>'
      + (imgs?'<div class="cp-dt__secimgs">'+imgs+'</div>':'')
      + '</section>';
  }).join('');
  const meta='<div class="cp-dt__meta">版式 · '+esc(_tplName(tpl))
    +(ai.visualSummary?'　'+esc(String(ai.visualSummary).slice(0,58)):'')+'</div>';
  return '<div class="cp-dt__block"><h3>图文详情</h3>'+meta
    + '<div class="cp-dt__ai cp-dt__ai--'+tpl+'">'+blocks+'</div></div>';
}

/* ── #814 独立「营地详细介绍」页（body 级浮层，参照双商城二级页） ──────────── */
window.campOpenIntro=async function(){
  // 重新拉一次 /camps，保证后台刚生成的 AI 详情立刻可见
  try{const fresh=await _get('/camps');if(Array.isArray(fresh)&&fresh.length)CAMP_LIST=fresh;}catch(e){}
  const c=_curCamp();
  if(!c||!c.id){_cpToast('营地信息尚未加载');return;}
  CAMP_INTRO=c;
  _paintIntro();
};
function _introRules(c,ai){
  const arr=(c.rules&&c.rules.length)?c.rules:(ai.policies||[]);
  return (arr||[]).map(r=>{
    if(typeof r==='string')return '<div class="cp-intro__rule"><p>'+esc(r)+'</p></div>';
    return '<div class="cp-intro__rule"><b>'+esc(r.label||r.title||'')+'</b><p>'+esc(r.text||'')+'</p></div>';
  }).join('');
}
function _paintIntro(){
  const c=CAMP_INTRO||_curCamp();
  const ai=c.aiDetail||{};
  const pairs=_aiMedia(c);
  const gallery=(c.gallery||[]).map(_safeUrl).filter(Boolean);
  const pool=pairs.length?pairs:gallery.map(u=>({u,f:null,cap:''}));
  const hero=(c.coverUrl&&_safeUrl(c.coverUrl))||pool[0]&&pool[0].u||'';
  const figs=pool.slice(0,12).map(x=>'<figure class="cp-intro__fig"><img src="'+esc(x.u)+'" alt="" loading="lazy"'+_focalStyle(x.f)+'></figure>').join('');
  const hl=(ai.highlights||[]).map(h=>'<span class="cp-chip">'+esc(String(h))+'</span>').join('');
  const fac=(c.facilities||[]).map(f=>'<span class="cp-chip">'+esc(String(f))+'</span>').join('');
  const rules=_introRules(c,ai);
  const hasAi=!!(ai.sections&&ai.sections.length);
  const html='<div class="cp-intro" id="cpIntro">'
    + '<div class="cp-lv__bar"><button class="cp-lv__back" type="button" onclick="campCloseIntro()">'+CP_ICON.back+'</button>'
      + '<span class="cp-lv__title">营地介绍</span><span class="cp-intro__barspace"></span></div>'
    + '<div class="cp-intro__scroll">'
      + (hero?'<div class="cp-intro__hero"><img src="'+esc(hero)+'" alt=""'+_focalStyle(c.coverFocal||(pool[0]&&pool[0].f))+'>'
        + '<div class="cp-intro__herocap"><h1>'+esc(c.name||'营地')+'</h1><p>'+esc(_shortSlogan(c))+'</p></div></div>':'')
      + '<div class="cp-intro__body">'
        + (hl?'<div class="cp-intro__chips">'+hl+'</div>':'')
        + (ai.description?'<p class="cp-intro__lead">'+esc(String(ai.description))+'</p>':'')
        + (figs?'<div class="cp-intro__block"><h3>营地实景 · '+pool.length+' 张</h3><div class="cp-intro__gallery">'+figs+'</div></div>':'')
        + _paintAiSections(c)
        + (fac?'<div class="cp-intro__block"><h3>设施与服务</h3><div class="cp-dt__chips">'+fac+'</div></div>':'')
        + (rules?'<div class="cp-intro__block"><h3>营地须知</h3>'+rules+'</div>':'')
        + (!hasAi?'<div class="cp-intro__block"><p class="cp-intro__empty">营地方还没有生成详细介绍。上传照片与资料后，可在后台用「AI 生成图文详情」一键补齐。</p></div>':'')
        + (c.address?'<div class="cp-intro__block"><h3>到店信息</h3><p class="cp-intro__addr">'+esc(c.address)+'</p></div>':'')
      + '</div>'
    + '</div>'
    + '<div class="cp-intro__bar"><button type="button" onclick="campCloseIntro()">返回挑营位</button></div>'
    + '</div>';
  let node=document.querySelector('#cpIntroHost');
  if(!node){node=document.createElement('div');node.id='cpIntroHost';document.body.appendChild(node);}
  node.innerHTML=html;
  document.body.classList.add('cp-lock');
}
window.campCloseIntro=function(){
  const n=document.querySelector('#cpIntroHost');if(n)n.remove();
  document.body.classList.remove('cp-lock');CAMP_INTRO=null;
};

function _paintDetail(){
  const d=CAMP_DETAIL;if(!d)return;
  const pairs=_focalPairs(d);
  const imgs=pairs.length?pairs.map(x=>'<img src="'+esc(x.u)+'" alt=""'+_focalStyle(x.f)+'>').join('')
    :'<div class="cp-dt__ph"></div>';
  const fac=(d.facilities||[]).map(f=>'<span class="cp-chip">'+esc(String(f))+'</span>').join('');
  const hasAi=!!(d.aiDetail&&Array.isArray(d.aiDetail.sections)&&d.aiDetail.sections.length);
  const para=hasAi?'':String(d.detailText||d.description||'').split(/\n+/).filter(Boolean)
    .map(t=>'<p>'+esc(t)+'</p>').join('');
  const isPitch=d.kind==='pitch';
  const plans=(d.kind==='rental')?_rentalPlansOf(d):null;
  if(plans){
    if(CAMP_DETAIL_PLAN>=plans.length)CAMP_DETAIL_PLAN=0;
    if(!CAMP_DETAIL_UNITS)CAMP_DETAIL_UNITS=_planDefaultUnits(plans[CAMP_DETAIL_PLAN]);
  }
  const price=(plans&&plans[CAMP_DETAIL_PLAN])?Number(plans[CAMP_DETAIL_PLAN].price||0):(d.price||0);
  /* #832 单元化：营位详情按「区间最少剩余」钳制数量上限，canAdd 也看余量 */
  const unitQty=isPitch?Number(d.unitQty||1):0;
  const q=isPitch?_quote(d.id,1,0):null;
  const maxUnits=isPitch?Math.max(1,Math.min(unitQty||1,(q&&q.minLeft!=null)?q.minLeft:unitQty||1)):20;
  if(isPitch&&CAMP_DETAIL_QTY>maxUnits)CAMP_DETAIL_QTY=maxUnits;
  const canAdd=isPitch?!!(q&&q.ok):true;
  const html='<div class="cp-detail" id="cpDetailView">'
    + '<div class="cp-dt__top"><button type="button" onclick="campCloseDetail()">'+CP_ICON.back+'</button>'
      + '<span>商品详情</span><button type="button" onclick="campOpenCheckout()" aria-label="清单">'+CP_ICON.cart+'</button></div>'
    + '<div class="cp-dt__scroll">'
      + '<div class="cp-dt__gallery">'+imgs+'</div>'
      + '<div class="cp-dt__head"><div class="cp-dt__kicker">'+esc(isPitch?'PITCH / 营位':(d.kind==='rental'?'RENTAL / 装备租赁':'STORE / 营地小站'))+'</div>'
        + '<h2>'+esc(d.name||'营地商品')+'</h2>'
        + (d.subtitle?'<p class="cp-dt__sub">'+esc(d.subtitle)+'</p>':'')
        + _paintHighlights(d)
        + '<div class="cp-dt__price" id="dtPrice"><span>¥</span>'+_money(price)+'<em>'+esc((plans&&plans[CAMP_DETAIL_PLAN])?('/'+(plans[CAMP_DETAIL_PLAN].unitLabel||'天')):(d.unitLabel||''))+'</em>'
        + (Number(d.originalPrice)>price?'<del>¥'+_money(d.originalPrice)+'</del>':'')+'</div>'
        + (CAMP_BENEFITS&&Number(CAMP_BENEFITS.memberDiscountPercent)>0
            ?'<div class="cp-dt__vip">'+esc(CAMP_BENEFITS.memberLevel||'会员')+' 立减 '+_money(CAMP_BENEFITS.memberDiscountPercent)+'% · 可用积分 '+Number(CAMP_BENEFITS.clubPoints||0)+' 分</div>':'')
        + '</div>'
      + '<div class="cp-dt__block"><h3>规格参数</h3><div class="cp-dt__specs">'+_detailSpecs(d)+'</div></div>'
      + (plans?_rentalPlanBlock(plans):'')
      + (fac?'<div class="cp-dt__block"><h3>设施与服务</h3><div class="cp-dt__chips">'+fac+'</div></div>':'')
      + (para?'<div class="cp-dt__block"><h3>商品详情</h3><div class="cp-dt__rich">'+para+'</div></div>':'')
      + _paintAiSections(d)
      + '<div class="cp-dt__block"><h3>预订须知</h3>'+_detailPolicies(d)+'</div>'
      + '<div class="cp-dt__safe">ClubOS 营地 · 价格与会员权益由系统实时计算</div>'
    + '</div>'
    + '<div class="cp-dt__bar">'
      + (isPitch
          ? '<div class="cp-dt__barinfo"><b id="dtQtyNote"></b><em>'+_cnDate(CAMP_IN)+' → '+_cnDate(d.isOvernight&&!_isDayUse()?CAMP_OUT:CAMP_IN)+'</em></div>'
            + (unitQty>1?'<div class="cp-stepper"><button type="button" onclick="campDetailQty(-1)">−</button><span id="dtQty">'+CAMP_DETAIL_QTY+'</span><button type="button" onclick="campDetailQty(1)">＋</button><em class="cp-stepper__u">个</em></div>':'')
          :'<div class="cp-stepper"><button type="button" onclick="campDetailQty(-1)">−</button><span id="dtQty">'+CAMP_DETAIL_QTY+'</span><button type="button" onclick="campDetailQty(1)">＋</button></div>')
      + '<button class="cp-dt__add" type="button" '+(canAdd?'':'disabled')+' onclick="campDetailAdd(0)">'+(canAdd?'加入清单':'已订满')+'</button>'
      + '<button class="cp-dt__buy" type="button" '+(canAdd?'':'disabled')+' onclick="campDetailAdd(1)">立即预订</button>'
    + '</div></div>';
  /* #828 修正：这里过去查的是 '#cpDetail'（详情页内部节点），却把新建的宿主节点
     命名成 '#cpDetailHost' —— 于是第二次打开商品详情时，querySelector 命中的是
     上一次残留的内层节点，详情页被套进自己上一层的 DOM 里（fixed 层层层嵌套，
     底部主页面透出来，画面错乱）。统一只用 #cpDetailHost 作为宿主即可。
     同时打开详情时锁住 body 滚动，避免底层页面跟着一起动。 */
  let node=document.querySelector('#cpDetailHost');
  if(!node){node=document.createElement('div');node.id='cpDetailHost';document.body.appendChild(node);}
  node.innerHTML=html;
  document.body.classList.add('cp-lock');
  /* #832 初始化营位数量提示与 stepper 上限 */
  if(isPitch){
    const n=document.getElementById('dtQtyNote');
    if(n)n.textContent=CAMP_DETAIL_QTY+' 个营位 · '+(unitQty>1?('剩 '+(q&&q.minLeft!=null?q.minLeft:unitQty)+'/'+unitQty):'1 个营位');
  }
}
window.campCloseDetail=function(){
  const n=document.querySelector('#cpDetailHost');if(n)n.remove();
  document.body.classList.remove('cp-lock');CAMP_DETAIL=null;
  /* #821 清干净详情态，避免下次打开带着上一次的规格/数量；顺带收起可能叠加的清单抽屉 */
  CAMP_DETAIL_QTY=1;CAMP_DETAIL_PLAN=0;CAMP_DETAIL_UNITS=0;
  if(window.campCloseSheet)window.campCloseSheet();
};
window.campDetailQty=function(delta){
  const d=CAMP_DETAIL||{};
  const max=(d.kind==='pitch'&&d.unitQty>1)?Math.max(1,Math.min(d.unitQty,(_quote(d.id,1,0)||{}).minLeft!=null?_quote(d.id,1,0).minLeft:d.unitQty)):20;
  CAMP_DETAIL_QTY=Math.max(1,Math.min(max,CAMP_DETAIL_QTY+delta));
  const el=$('#dtQty');if(el)el.textContent=CAMP_DETAIL_QTY;
  const n=$('#dtQtyNote');
  if(n&&d.kind==='pitch')n.textContent=CAMP_DETAIL_QTY+' 个营位 · '+(d.unitQty>1?('剩 '+((_quote(d.id,1,0)||{}).minLeft!=null?_quote(d.id,1,0).minLeft:d.unitQty)+'/'+d.unitQty):'1 个营位');
  try{_syncRentalPlan();}catch(e){}
};
window.campDetailAdd=function(goCheckout){
  const d=CAMP_DETAIL;if(!d)return;
  const ok=_addToCart(d.kind,d.id,CAMP_DETAIL_QTY,{plan:CAMP_DETAIL_PLAN,units:CAMP_DETAIL_UNITS});
  if(!ok)return;
  campCloseDetail();
  if(goCheckout)campOpenCheckout();
};

/* ── 购物车：营位 / 装备 / 商品统一进一张订单 ───────────────────────────── */
function _addToCart(kind,id,qty,opts){
  qty=Math.max(1,Math.min(20,Number(qty)||1));
  if(kind==='pitch'){
    const q=_quote(id,1,0);
    if(!q.ok){_cpToast(q.reason||'该日期区间不可订');return false;}
    const exist=CAMP_CART.find(x=>x.kind==='pitch'&&String(x.id)===String(id));
    if(exist){_cpToast('该营位已在清单中');return true;}
    const p=CAMP_PITCHES.find(x=>String(x.id)===String(id))||{};
    const day=_isDayUse()||!p.is_overnight;
    CAMP_CART.push({kind:'pitch',id:id,title:p.name||'营位',coverUrl:_safeUrl(p.coverUrl||p.cover_url),coverFocal:p.coverFocal||null,
      spec:_pitchTypeLabel(p.pitch_type),unitLabel:p.is_overnight?'/晚':'/天',unitPrice:q.payable,
      qty:1,days:day?1:Math.max(1,_nights()),checkIn:CAMP_IN,checkOut:day?CAMP_IN:CAMP_OUT,
      isDayUse:day?1:0,deposit:0});
  }else{
    const arr=CAMP_GOODS[kind]||[];
    const x=arr.find(v=>String(v.id)===String(id))||{};
    const isR=kind==='rental';
    /* #822：租赁按老板自定义的计费项计价（按小时 / 按天 / 按次…）。 */
    const plans=isR?_rentalPlansOf(x):null;
    let plan=null,planIdx=-1,units=0;
    if(plans&&plans.length){
      planIdx=(opts&&opts.plan!=null)?Math.max(0,Math.min(plans.length-1,opts.plan)):0;
      plan=plans[planIdx];
      units=(opts&&opts.units)?Math.max(1,Math.min(_planMaxUnits(plan),opts.units)):_planDefaultUnits(plan);
    }
    const unit=isR?(plan?Number(plan.price||0):Number(x.daily_price||0)):Number(x.price||0);
    const cap=isR?Number(x.available_qty||0):Number(x.stock||0);
    const exist=CAMP_CART.find(v=>v.kind===kind&&String(v.id)===String(id));
    const newQty=(exist?Number(exist.qty):0)+qty;
    if(newQty>cap){_cpToast('库存仅剩 '+cap+' 件');return false;}
    if(exist){exist.qty=newQty;exist.deposit=isR?Number(x.deposit||0)*newQty:0;}
    else CAMP_CART.push({kind:kind,id:id,title:x.name||'商品',coverUrl:_safeUrl(x.coverUrl||x.cover_url),coverFocal:x.coverFocal||null,
      spec:(isR&&plan)?((plan.name||'租金')+' · '+units+(plan.unitLabel||'天')):(x.description||x.subtitle||''),
      unitLabel:isR?(plan?('/'+plan.unitLabel):'/天'):'/件',unitPrice:unit,qty:qty,
      planIdx:planIdx,units:units,
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
    deposit:Number(x.deposit||0),days:Number(x.days||1),
    // #822：租赁带计费项序号与数量，后端据此按老板自定义的单位重算
    plan:(x.planIdx!=null&&x.planIdx>=0)?x.planIdx:undefined,
    units:(Number(x.units||0)>0)?Number(x.units):undefined}));
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
    + '<div class="cp-ck__img">'+_mediaHtml(x.coverUrl,'cp-ck__imgfile',x.coverFocal)+'</div>'
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
  /* #860：把当前清单里的真实营地/营位/商品 ID 传给券列表，让「可用券」与
     下单 hold 用同一份适用范围判据，避免列表可抵、选中后却被拒。 */
  const _sw=new URLSearchParams();
  if(Number(CAMP_CAMP_ID))_sw.set('campIds',String(Number(CAMP_CAMP_ID)));
  const _pids=[],_gids=[];
  (CAMP_CART||[]).forEach(x=>{
    const id=Number(x.id);if(!id)return;
    if(x.kind==='pitch')_pids.push(id);else _gids.push(id);
  });
  if(_pids.length)_sw.set('pitchIds',[...new Set(_pids)].join(','));
  if(_gids.length)_sw.set('productIds',[...new Set(_gids)].join(','));
  const _qs=_sw.toString()?('&'+_sw.toString()):'';
  try{CAMP_MY_VOUCHERS=await api('/api/public/clubs/'+CLUB+'/vouchers?user_id='+uid+'&kind=camp'+_qs,{cache:'no-store'});}catch(e){CAMP_MY_VOUCHERS=null;}
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
  const rc=String((CAMP_ORDER||{}).redeemCode||'');
  _showSheet('支付成功','<div class="cp-ok"><div class="cp-ok__ico">'+CP_ICON.check+'</div>'
    + '<h2>预订成功</h2><p class="cp-ok__amt">实付 ¥'+_money(r.total||(CAMP_ORDER||{}).total)+'</p>'
    + '<div class="cp-ok__card"><div><span>订单号</span><b>'+esc((CAMP_ORDER||{}).orderNo||'')+'</b></div>'
    + '<div><span>订单状态</span><b>已支付</b></div>'
    + '<div><span>获得活动积分</span><b>+'+earned+' 分</b></div>'
    + '<div><span>营位 / 库存</span><b>已锁定</b></div></div>'
    + (rc?'<div class="cp-ok__redeem"><canvas class="cp-qr" data-code="'+esc(rc)+'"></canvas>'
        +'<div class="cp-order__rc">核销码 <b>'+esc(rc)+'</b><em>到营地出示此码，工作人员扫码核销</em></div></div>':'')
    + '<p class="cp-ok__tip">出行信息可在「我的订单」查看'+(rc?'，也可直接出示核销码':'到营地报订单号或手机号即可')+'。</p>'
    + '<button class="cp-submit" type="button" onclick="campOpenOrders()">查看我的订单</button>'
    + '<button class="cp-link" type="button" onclick="campCloseSheet();loadCampPage()">继续逛营地 ›</button></div>');
  _drawRedeemQrs();
}

/* ── 我的营地订单 ───────────────────────────────────────────────────────── */
const CAMP_ST={pending_payment:'待支付',paid:'已支付',cancelled:'已取消',expired:'已过期'};
const CAMP_ST_CN={pending_payment:'待支付',paid:'已支付 · 待出行',cancelled:'已取消',expired:'已超时'};
/* #829 与「我的」（me.js）共用同一份「已读订单」记录：同一个 key，两端不会各记一套。
   营地页的订单抽屉本来就直接摊开订单明细 + 核销码，所以「打开抽屉 = 已看」。 */
function _campSeenKey(){ try{ return 'clubos_camp_seen_'+(typeof CLUB!=='undefined'?CLUB:0)+'_'+_uid(); }catch(e){ return ''; } }
function _campSeenSet(){ try{ return new Set(JSON.parse(localStorage.getItem(_campSeenKey())||'[]').map(String)); }catch(e){ return new Set(); } }
function _campMarkAllSeen(orders){
  const seen=_campSeenSet(); let ch=false;
  (orders||[]).forEach(o=>{ if(o&&o.id!=null&&!seen.has(String(o.id))){ seen.add(String(o.id)); ch=true; } });
  if(ch){ try{ localStorage.setItem(_campSeenKey(), JSON.stringify([...seen])); }catch(e){} }
}
function _ordersHtml(orders){
  if(!orders.length)return '<div class="cp-state">还没有营地订单，去挑个营位吧。</div>';
  const seen=_campSeenSet();
  return '<div class="cp-orders">'+orders.map(o=>{
    const st=o.status||'';
    const isNew=(o.id!=null&&!seen.has(String(o.id)));
    const items=(o.items||[]).map(it=>'<div class="cp-order__it"><span>'+esc(it.title||'')+' × '+it.qty+'</span>'
      +'<b>¥'+_money(it.subtotal)+'</b></div>').join('');
    /* #821 删除按钮默认隐藏，所有订单都能左滑露出 */
    return '<div class="cp-order-wrap">'
      +'<div class="cp-order-del" onclick="campDeleteOrder('+o.id+',\''+st+'\')">删除</div>'
      +'<div class="cp-order" data-oid="'+o.id+'"><div class="cp-order__hd"><b>'+esc(o.orderNo||'')+'</b>'
      +'<span class="cp-order__st '+esc(st)+'">'+esc(CAMP_ST_CN[st]||CAMP_ST[st]||st)+'</span>'
      +(isNew?'<i class="cp-order__dot" aria-label="新订单"></i>':'')+'</div>'
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
      +(st==='paid'&&o.redeemCode?'<div class="cp-order__redeem'+(o.redeemedAt?' done':'')+'">'
        +(o.redeemedAt?'<b>已核销</b><em>'+esc(o.redeemedAt)+'</em>'
          :'<canvas class="cp-qr" data-code="'+esc(o.redeemCode)+'"></canvas>'
           +'<div class="cp-order__rc">核销码 <b>'+esc(o.redeemCode)+'</b><em>到营地出示此码，工作人员扫码核销</em></div>')
        +'</div>':'')
      +(st==='paid'?'<div class="cp-order__earn">已获得 '+Number(o.pointsEarned||0)+' 活动积分</div>':'')
      +'</div></div>';
  }).join('')+'</div>';
}
/* #821 左滑删除委托版：抽屉每次 innerHTML 重建也不会丢手势 */
let _cpSw={x:0,dx:0,wrap:null};
function _cpSwCard(w){return w?w.querySelector('.cp-order'):null;}
document.addEventListener('touchstart',function(e){
  const w=e.target.closest&&e.target.closest('.cp-order-wrap');
  if(!w)return;
  _cpSw={x:e.touches[0].clientX,dx:0,wrap:w};
},{passive:true});
document.addEventListener('touchmove',function(e){
  if(!_cpSw.wrap)return;
  _cpSw.dx=e.touches[0].clientX-_cpSw.x;
  const card=_cpSwCard(_cpSw.wrap);
  if(card){card.style.transition='none';card.style.transform='translateX('+Math.max(-72,Math.min(0,_cpSw.dx))+'px)';}
},{passive:true});
document.addEventListener('touchend',function(){
  const w=_cpSw.wrap;if(!w)return;
  const open=_cpSw.dx<-36,card=_cpSwCard(w);
  if(card){card.style.transition='transform .18s ease';card.style.transform=open?'translateX(-72px)':'translateX(0)';}
  w.classList.toggle('is-open',open);
  _cpSw={x:0,dx:0,wrap:null};
},{passive:true});
document.addEventListener('click',function(e){
  const w=e.target.closest&&e.target.closest('.cp-order-wrap.is-open');
  if(!w||e.target.closest('.cp-order-del'))return;
  w.classList.remove('is-open');
  const card=_cpSwCard(w);if(card)card.style.transform='translateX(0)';
  e.stopPropagation();e.preventDefault();
},true);
/* 兼容旧调用点 */
function _bindOrderSwipe(root){}

window.campDeleteOrder=async function(oid,st){
  if(st&&st!=='cancelled'&&st!=='expired'){_cpToast('该订单尚在进行中，请先取消订单再删除');return;}
  const ok=await (window.uxConfirm?uxConfirm({title:'删除订单',message:'删除后该订单不再显示，且无法恢复。确定删除？',confirmText:'删除',danger:true}):confirm('删除后不可恢复，确定？'));
  if(!ok)return;
  try{
    await api('/api/public/camp-orders/'+oid+'?user_id='+_uid(),{method:'DELETE',cache:'no-store'});
    /* 同步清理已读集合（两端共用 key） */
    const seen=_campSeenSet();seen.delete(String(oid));
    try{localStorage.setItem(_campSeenKey(),JSON.stringify([...seen]));}catch(e){}
    try{const k='clubos_camp_seen_'+(typeof CLUB!=='undefined'?CLUB:0)+'_'+_uid();
      const s=new Set(JSON.parse(localStorage.getItem(k)||'[]').map(String));s.delete(String(oid));
      localStorage.setItem(k,JSON.stringify([...s]));}catch(e){}
    _cpToast('订单已删除');
    campOpenOrders();
  }catch(e){_cpToast(_err(e));}
};
window.campOpenOrders=async function(){
  clearInterval(CAMP_PAY_TIMER);
  _showSheet('我的营地订单','<div id="cpOrdersBody"><div class="cp-state">正在读取订单…</div></div>');
  try{
    const d=await _get('/camp-orders?user_id='+_uid());
    const list=d.orders||[];
    const el=$('#cpOrdersBody');if(el)el.innerHTML=_ordersHtml(list);
    _drawRedeemQrs();
    _bindOrderSwipe(el);
    /* 抽屉里订单明细与核销码全摊开了，算「已看」；红点在打开抽屉后自动消失（#837） */
    _campMarkAllSeen(list);
  }catch(e){const el=$('#cpOrdersBody');if(el)el.innerHTML='<div class="cp-state">'+esc(_err(e))+'</div>';}
};
/* #812：把订单列表里的核销码 canvas 画成二维码（本地生成，无外网） */
function _drawRedeemQrs(root){
  if(!window.ClubOSQR)return;
  (root||document).querySelectorAll('canvas.cp-qr[data-code]').forEach(cv=>{
    if(cv.dataset.drawn)return;cv.dataset.drawn='1';
    try{window.ClubOSQR.draw(cv,cv.dataset.code);}catch(e){}
  });
}
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
  if(document.querySelector('#cpDetailHost')){campCloseDetail();return;}
  if(document.querySelector('#cpShopHost')){_closeShopList();return;}
  _closeSheet();
});

applyModuleSwitches();
