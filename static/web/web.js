let CLUB=Number(new URLSearchParams(location.search).get('club_id')||1),USER=1,PAYER_NAME='林野',PAYER_PHONE='13800000001';let currentAct=null,currentOcc=null,activityVouchers=[],bookingParticipants=[];let ACT_ALL=[],ACT_GROUP='all',MALL_ALL=[],MALL_CAT='全部',MALL_SORT='rec';
/* ══════════════════════════════════════════════════════════════════════════
   C 端视觉层（配合 static/web/web.css）
   ══════════════════════════════════════════════════════════════════════════ */

/* 图标：内联 SVG，与 index.html 底部导航同一套笔画风格。不用 emoji 当图标 ——
   字形在不同系统上不一样，户外品牌的调性也压不住。 */
const WI={
  pin:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s6.2-5.5 6.2-10.4a6.2 6.2 0 1 0-12.4 0C5.8 15.5 12 21 12 21z"/><circle cx="12" cy="10.4" r="2.3"/></svg>',
  arrow:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13"/><path d="m12.4 6 6 6-6 6"/></svg>',
  back:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14.5 5-7 7 7 7"/></svg>',
  bag:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 8h14l-1.2 12H6.2z"/><path d="M8.6 8V6.4a3.4 3.4 0 0 1 6.8 0V8"/></svg>',
  empty:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 17.5 9 8.5l3.4 5.4L15 10l5.5 7.5z"/><circle cx="17.4" cy="6.6" r="1.6"/></svg>',
  ticket:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7.5h16v3a2 2 0 0 0 0 3v3H4v-3a2 2 0 0 0 0-3z"/><path d="M14.2 7.5v9"/></svg>',
  gift:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 11.5h16V20H4z"/><path d="M3 8h18v3.5H3z"/><path d="M12 8v12"/><path d="M12 8S10.4 4 8.2 4a2.1 2.1 0 0 0 0 4.2z"/><path d="M12 8s1.6-4 3.8-4a2.1 2.1 0 0 1 0 4.2z"/></svg>',
  diamond:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20 3.5 9.2 6.6 4h10.8l3.1 5.2z"/><path d="M3.5 9.2h17"/><path d="m9.4 4-1.6 5.2L12 20l4.2-10.8L14.6 4"/></svg>',
  receipt:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6"/></svg>',
  help:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.3 2.4c-.6.3-.9.8-.9 1.4v.4"/><path d="M12 17h.01"/></svg>',
  reward:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 0 1 13.6-5.7"/><path d="M20 12a8 8 0 0 1-13.6 5.7"/><path d="M17.2 3.2v3.4h-3.4"/><path d="M6.8 20.8v-3.4h3.4"/></svg>',
  /* 会员「卡」而不是二维码：这张卡是可出示的身份凭证，但没有可被扫码核销的签名码，
     画一个二维码出来等于伪造一个扫不动的东西。用卡片图标，点开是真实的会员信息。 */
  card:'<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2.8" y="5" width="18.4" height="14" rx="2.6"/><path d="M2.8 9.6h18.4"/><path d="M6.4 14.6h4"/></svg>',
  pax:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8.4" r="3.6"/><path d="M4.8 20.2a7.2 7.2 0 0 1 14.4 0"/></svg>'
};

/* 活动日期在库里是两种格式混着的：老数据 `2026-10-24`，生成器产出 `2026年11月2日`。
   筛选、排序、展示都要用，所以统一解析；解析不出来返回 null，让调用方降级成原样显示 ——
   宁可显示原始字符串，也不要猜一个日期出来。 */
function wDate(s){
  const t=String(s||'').trim();if(!t)return null;
  const m=t.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/)||t.match(/(\d{4})\D+(\d{1,2})\D+(\d{1,2})/);
  return m?{y:+m[1],m:+m[2],d:+m[3],key:(+m[1])*100+(+m[2])}:null;
}
function wDay(s){const t=wDate(s);return t?t.m+'月'+t.d+'日':String(s||'');}
function wJump(id){const el=document.getElementById(id);if(el)el.scrollIntoView({behavior:'smooth',block:'center'});}
function wCovCls(x){return 'cov-'+((Number(x&&x.id||0)%6)+1);}

/* ── 状态文案 ────────────────────────────────────────────────────────────────
   后端枚举一律映射成中文；**认不出来的值原样显示**（`||x.status`）。
   猜错枚举比露出 `awaiting_return` 更糟 —— 前者会让顾客看到一句断言错的话。 */
/* pending_payment 是真实的订单态（建单未付款），failed 来自 registrations/gear_orders 的
   payment_status。缺这两个键时 badge 会把英文枚举原样显示给用户。 */
const ORD_ST={paid:'已支付',pending:'待支付',unpaid:'待支付',pending_payment:'待支付',payment_failed:'支付未完成',refunded:'已退款',cancelled:'已取消',canceled:'已取消',closed:'已关闭',completed:'已完成',refunding:'退款中',partial_refunded:'部分退款',processing:'处理中'};
const RF_ST={none:'',rejected:'已驳回',pending:'审核中',approved:'已通过',processing:'处理中',refunded:'已退款'};
const INS_ST={pending:'待处理',processing:'办理中',done:'已投保',insured:'已投保',completed:'已投保',failed:'投保失败',not_required:'无需保险'};
const AS_TYPE={refund_only:'仅退款',return_refund:'退货退款',exchange:'换货'};
const AS_ST={pending:'待审核',reviewing:'审核中',approved:'已通过',rejected:'已驳回',awaiting_return:'待寄回',returned:'已寄回',refunded:'已退款',exchanging:'换货中',exchanged:'已换货',completed:'已完成',closed:'已关闭'};
const st=map=>v=>map[String(v||'')]||(v?String(v):'');
const ordSt=st(ORD_ST),rfSt=st(RF_ST),insSt=st(INS_ST),asType=st(AS_TYPE),asSt=st(AS_ST);
/* 状态色调：只分四档（成功 / 进行中 / 警示 / 中性），不按业务枚举逐个配色 ——
   枚举会随版本增加，色调档位不会。 */
function stTone(v){
  const k=String(v||'');
  if(['paid','completed','refunded','approved','done','insured','exchanged','issued','returned'].includes(k))return 'ok';
  if(['pending','processing','reviewing','refunding','awaiting_return','exchanging','unpaid','held'].includes(k))return 'wait';
  if(['rejected','cancelled','canceled','closed','failed'].includes(k))return 'off';
  if(['partial_refunded','return_refund','refund_only','exchange'].includes(k))return 'warn';
  return 'mute';
}
function badge(text,tone){return text?`<span class="w-bdg w-bdg--${tone||'mute'}">${esc(text)}</span>`:''}

/* ── 骨架屏 ───────────────────────────────────────────────────────────────── */
function wSkBars(n,w){let o='';for(let i=0;i<n;i++)o+=`<div class="w-sk__bar${w?' style="width:'+w+'"':''}"></div>`;return o}
function wSkMini(n){let o='';for(let i=0;i<n;i++)o+=`<div class="w-sk-card" style="aspect-ratio:4/3.1"></div>`;return o}
function wSkReel(n){let o='';for(let i=0;i<n;i++)o+=`<div class="w-sk-card" style="flex:0 0 76%;aspect-ratio:3/4.15"></div>`;return o}
function wSkRows(n){let o='';for(let i=0;i<n;i++)o+=`<div class="w-sk-row">${wSkBars(2,'62%')}</div>`;return o}

/* ── 首页 ─────────────────────────────────────────────────────────────────── */
async function loadHome(){
  const feat=$('#homeFeatured');
  if(feat&&!feat.children.length)feat.innerHTML=wSkMini(3);
  const acts=await api(`/api/public/clubs/${CLUB}/activities`);
  ACT_ALL=acts;
  const top=acts.slice(0,3);
  const hero=$('#homeHero');
  if(hero){
    /* 进度条与页码只在真有多于一屏时才画：只有一场活动时「01 / 01」+ 一根满格进度条
       是纯噪音，还会暗示「后面还有」（其实没有）。 */
    hero.innerHTML=top.length
      ? '<div class="w-hero__track">'+top.map((x,i)=>heroSlide(x,i)).join('')+'</div>'
        +(top.length>1?'<div class="w-hero__meter"><i></i></div><div class="w-hero__count"></div>':'')
      : '<div class="w-hero__slide is-fallback"><div class="w-hero__media"></div><div class="w-hero__scrim"></div><div class="w-hero__copy"><div class="w-hero__eyebrow">REMOTE OUTDOOR</div><h1>去户外，找到下一场。</h1><div class="w-hero__sub">俱乐部正在筹备新的线路，稍后再来看看。</div></div></div>';
    bindHero(hero);
  }
  /* 「我的」页的背景图沿用主推活动封面，两屏之间保持同一片山。 */
  const bg=$('#meBg');
  if(bg&&top[0]&&top[0].cover)bg.style.backgroundImage=`url('${esc(top[0].cover)}')`;
  if(feat)feat.innerHTML=acts.length?acts.slice(0,8).map(miniCard).join(''):'<div class="w-empty">'+WI.empty+'<div>俱乐部暂时没有已发布活动</div></div>';
  try{
    const g=$('#homeGear');
    if(g&&!g.children.length)g.innerHTML=wSkMini(3);
    const pr=await api(`/api/public/clubs/${CLUB}/mall/products`);
    MALL_ALL=pr;
    if(g)g.innerHTML=pr.length?pr.slice(0,8).map(miniGear).join(''):'<div class="w-empty">'+WI.empty+'<div>商城暂时没有在售装备</div></div>';
  }catch(e){}
}
/* hero 轮播指示：底部那根细线就是「第几场 / 共几场」，不是装饰。滚动停下 60ms 再量，
   否则 iOS 惯性滚动期间每帧都读一次 scrollLeft，指示器会乱跳。 */
function bindHero(hero){
  const track=hero.querySelector('.w-hero__track');if(!track)return;
  const n=track.children.length,bar=hero.querySelector('.w-hero__meter i'),cnt=hero.querySelector('.w-hero__count');
  const sync=()=>{
    const i=Math.max(0,Math.min(n-1,Math.round(track.scrollLeft/Math.max(1,track.clientWidth))));
    if(bar)bar.style.width=((i+1)/n*100)+'%';
    if(cnt)cnt.textContent=String(i+1).padStart(2,'0')+' / '+String(n).padStart(2,'0');
  };
  track.addEventListener('scroll',()=>{clearTimeout(track._wt);track._wt=setTimeout(sync,60)},{passive:true});
  sync();
}
function heroSlide(x,i){
  const cov=x.cover||'',d=wDate(x.event_date);
  return `<div class="w-hero__slide${cov?'':' is-fallback'}">
    ${cov?`<div class="w-hero__media" style="background-image:url('${esc(cov)}')"></div>`:'<div class="w-hero__media"></div>'}
    <div class="w-hero__scrim"></div>
    <div class="w-hero__copy">
      <div class="w-hero__eyebrow">${i===0?'FEATURED · 本期主推':'UPCOMING · 招募中'}</div>
      <h1>${esc(x.title)}</h1>
      <div class="w-hero__sub">${esc(x.location||'户外')}${x.capacity?` · 限 ${x.capacity} 人`:''}${d?` · ${wDay(x.event_date)}出发`:''}</div>
      <div class="w-hero__facts">
        <span class="w-hero__fact">${d?wDay(x.event_date):'团期待定'}</span>
        ${x.capacity?`<span class="w-hero__fact">限 ${x.capacity} 人</span>`:''}
        <span class="w-hero__fact">${money(x.price)} / 人</span>
      </div>
      <button class="w-hero__cta" type="button" onclick="openAct(${x.id})">即刻探索${WI.arrow}</button>
    </div>
  </div>`;
}
function miniCard(x){
  const cov=x.cover||'',d=wDate(x.event_date);
  return `<button class="w-mini" type="button" onclick="openAct(${x.id})">
    <div class="w-mini__media">
      ${cov?`<img src="${esc(cov)}" alt="" loading="lazy">`:`<div class="w-cov ${wCovCls(x)}"></div>`}
      ${d?`<span class="w-mini__date">${wDay(x.event_date)}</span>`:''}
    </div>
    <div class="w-mini__title">${esc(x.title)}</div>
    <div class="w-mini__meta"><span>${esc(x.location||'户外')}</span><span class="w-mini__price">${money(x.price)}</span></div>
  </button>`;
}
function miniGear(x){
  return `<button class="w-mini w-mini--gear" type="button" onclick="buy(${x.id})">
    <div class="w-mini__media">${x.image_url?`<img src="${esc(x.image_url)}" alt="" loading="lazy">`:WI.bag}</div>
    <div class="w-mini__title">${esc(x.name)}</div>
    <div class="w-mini__meta"><span class="w-mini__price">${money(x.price)}</span></div>
  </button>`;
}

/* ── 活动列表 ─────────────────────────────────────────────────────────────── */
async function loadActivities(){
  const box=$('#publicActivities');
  if(box&&!box.children.length)box.innerHTML=wSkReel(2);
  ACT_ALL=await api(`/api/public/clubs/${CLUB}/activities`);
  renderActivityChips();renderActivityList();
}
/* 筛选项按「本月 / 下月 / 更远」分：活动是有关键时间窗的，顾客真正在问的是「最近哪场能去」，
   不是按品类浏览。没有活动的档位直接禁用 —— 不做点进去空一片的入口。 */
function activityGroups(){
  const now=new Date(),cy=now.getFullYear(),cm=now.getMonth()+1;
  const nx=cm===12?{y:cy+1,m:1}:{y:cy,m:cm+1};
  const thisKey=cy*100+cm,nextKey=nx.y*100+nx.m;
  return [
    {k:'all',label:'全部',test:()=>true},
    {k:'now',label:'本月',test:x=>{const d=wDate(x.event_date);return d&&d.key===thisKey}},
    {k:'next',label:'下月',test:x=>{const d=wDate(x.event_date);return d&&d.key===nextKey}},
    {k:'later',label:'更远',test:x=>{const d=wDate(x.event_date);return d&&d.key>nextKey}}
  ];
}
function renderActivityChips(){
  const box=$('#activityChips');if(!box)return;
  box.innerHTML=activityGroups().map(g=>{
    const n=ACT_ALL.filter(g.test).length,off=g.k!=='all'&&n===0;
    return `<button class="w-chip${g.k===ACT_GROUP?' on':''}" type="button" data-g="${g.k}"${off?' disabled':''}>${g.label}${!off&&g.k!=='all'?' '+n:''}</button>`;
  }).join('');
}
function renderActivityList(){
  const box=$('#publicActivities');if(!box)return;
  /* 档位 chip 必须在这里渲染：从首页进活动页时 ACT_ALL 已经由 loadHome 填好，
     wv() 会走「已有数据 → 直接渲染」的快路径、根本不会经过 loadActivities()，
     挂在 loadActivities 里的 renderActivityChips 就被整个跳过，chip 永远是空的。 */
  renderActivityChips();
  const q=(($('#actSearch')&&$('#actSearch').value)||'').trim().toLowerCase();
  const g=activityGroups().find(x=>x.k===ACT_GROUP);
  let list=g?ACT_ALL.filter(g.test):ACT_ALL.slice();
  if(q)list=list.filter(x=>((x.title||'')+' '+(x.location||'')).toLowerCase().includes(q));
  box.innerHTML=list.map(reelCard).join('');
  const cnt=$('#actCount');
  if(cnt)cnt.textContent=ACT_ALL.length?`共 ${list.length} 场可报名${q?` · 关键词「${q}」`:''}`:'暂时没有已发布的活动';
  const emp=$('#activityEmpty');
  if(emp)emp.innerHTML=list.length?'':`<div class="w-empty">${WI.empty}<div>没有符合条件的活动</div><div style="margin-top:6px">换个时间档位，或清空搜索词再看看。</div></div>`;
}
function reelCard(x){
  const cov=x.cover||'',d=wDate(x.event_date);
  return `<article class="w-reel__card${cov?'':' is-fallback'}" onclick="openAct(${x.id})" tabindex="0" role="button" aria-label="${esc(x.title)} 详情">
    ${cov?`<div class="w-reel__media" style="background-image:url('${esc(cov)}')"></div>`:''}
    <div class="w-reel__scrim"></div>
    <span class="w-reel__price">${money(x.price)}</span>
    ${x.capacity?`<span class="w-reel__seat">限 ${x.capacity} 人</span>`:''}
    <div class="w-reel__copy">
      <h3>${esc(x.title)}</h3>
      <div class="w-reel__meta">${esc(x.location||'户外')}${d?' · '+wDay(x.event_date):''}</div>
      <span class="w-reel__cta">查看详情</span>
    </div>
    <div class="w-reel__brand">REMOTE OUTDOOR CLUB</div>
  </article>`;
}

/* ── 装备商城 ─────────────────────────────────────────────────────────────── */
async function loadMall(){
  const box=$('#publicProducts');
  if(box&&!box.children.length)box.innerHTML=wSkMini(4);
  MALL_ALL=await api(`/api/public/clubs/${CLUB}/mall/products`);
  renderMall();
}
/* 左侧竖栏是分类，右侧 tab 是排序 —— 参考图那种「男士/女士/鞋履 + 软壳夹克/三合一」是两级品类，
   我们库里没有第二层，硬造一层只会让老板在后台多维护一堆没意义的字段。这里把它变成顾客真会用的排序。 */
const MALL_SORTS=[
  {k:'rec',label:'推荐'},
  {k:'low',label:'价格从低到高',cmp:(a,b)=>Number(a.price||0)-Number(b.price||0)},
  {k:'high',label:'价格从高到低',cmp:(a,b)=>Number(b.price||0)-Number(a.price||0)}
];
function mallCats(){return ['全部'].concat(Array.from(new Set(MALL_ALL.map(x=>x.category).filter(Boolean))))}
function renderMallCats(){
  const box=$('#mallCats');if(!box)return;
  const cats=mallCats();
  if(!cats.includes(MALL_CAT))MALL_CAT='全部';
  const cnt=c=>c==='全部'?MALL_ALL.length:MALL_ALL.filter(x=>x.category===c).length;
  box.innerHTML=cats.map(c=>`<button class="${c===MALL_CAT?'on':''}" type="button" data-c="${esc(c)}">${esc(c)}<i>${cnt(c)}</i></button>`).join('');
}
function renderMallTabs(){
  const box=$('#mallTabs');if(!box)return;
  box.innerHTML=MALL_SORTS.map(s=>`<button class="${s.k===MALL_SORT?'on':''}" type="button" data-s="${s.k}">${s.label}</button>`).join('');
}
function renderMall(){
  const box=$('#publicProducts');if(!box)return;
  /* 和 renderActivityList 同一个坑：MALL_ALL 可能已由首页 loadHome 填好，
     wv() 走快路径直接进这里，左侧分类栏 / 排序 tab 就不会渲染。
     分类与 tab 只依赖 MALL_ALL，放在这里等价于「每次重渲染前先校正一次」。 */
  renderMallCats();renderMallTabs();
  /* 收掉 experience-consumer.js 注入的 #uxGearFilter：它自带一个装备搜索框，
     而本页已有自己的搜索框，两个框同时出现是明显的功能重复。
     它给卡片加的 ux-gear-card 类名不受影响。 */
  document.getElementById('uxGearFilter')?.remove();
  let list=MALL_ALL.slice();
  if(MALL_CAT!=='全部')list=list.filter(x=>x.category===MALL_CAT);
  const q=(($('#gearSearch')&&$('#gearSearch').value)||'').trim().toLowerCase();
  if(q)list=list.filter(x=>((x.name||'')+' '+(x.category||'')).toLowerCase().includes(q));
  const s=MALL_SORTS.find(x=>x.k===MALL_SORT);
  if(s&&s.cmp)list=list.sort(s.cmp);
  box.innerHTML=list.length?list.map(productCard).join('')
    :`<div class="w-empty" style="grid-column:1/-1">${WI.empty}<div>${q?'没有匹配的装备':'这个分类暂时没有在售装备'}</div>${q?'<div style="margin-top:6px">换个关键词，或清空搜索。</div>':''}</div>`;
  /* 结果计数写回页脚说明位：搜索/切分类后顾客需要知道「筛完还剩几件」。 */
  const note=$('#mallNote');
  if(note)note.textContent=list.length!==MALL_ALL.length
    ? `当前筛选出 ${list.length} 件（共 ${MALL_ALL.length} 件在售）。平台统一商品、库存、发货与售后。`
    : `共 ${MALL_ALL.length} 件在售。平台统一商品、库存、发货与售后；会员等级折扣在结算时自动生效。`;
}
/* 商品卡必须带 .web-card 类：experience-consumer.js 是按 `#publicProducts .web-card` 数商品张数的，
   卡片换类名它就会判定「0 件在售」并把整个容器换成空态文案。外观由 .w-product 覆盖。 */
function productCard(x){
  const out=Number(x.stock||0)<=0;
  return `<div class="web-card w-product${out?' is-out':''}" role="button" tabindex="0" onclick="openProduct(${x.id})">
    <div class="w-product__media">${x.image_url?`<img src="${esc(x.image_url)}" alt="" loading="lazy">`:WI.bag}</div>
    <div class="w-product__inner">
      <div class="w-product__name">${esc(x.name)}</div>
      <div class="w-product__price">${money(x.price)}</div>
      <div class="w-product__meta">${esc(x.category||'装备')} · ${out?'暂时缺货':`库存 ${x.stock}`}</div>
      <button class="w-product__buy" type="button" onclick="openProduct(${x.id})">${out?'暂时缺货':'查看详情'}</button>
    </div>
  </div>`;
}

/* ── 装备详情页 ───────────────────────────────────────────────────────────────
   和 openAct 一个套路（列表区留在 DOM 里靠 display 切，返回不用重拉），但多一层状态：
   图集滚到第几张、选了哪个规格、买几件。所以拆成「整块渲染一次 + 局部更新」。 */
let P_CUR=null,P_VID=null,P_QTY=1;

/* 图集只有一张时不画指示点：一排只有一个点是噪音。
   商品没有图就返回空数组，由调用方画占位 —— 不拿占位图冒充商品照。 */
function productImages(x){
  const list=(x.images||[]).map(i=>typeof i==='string'?i:(i&&i.url)).filter(Boolean);
  return list.length?list:(x.image_url?[x.image_url]:[]);
}
/* 只有一个规格时不显示规格选择区。迁移给无规格商品建的那个「默认」规格会混在
   variants 里，画一排只有一个按钮的「选择规格」，顾客会以为还有别的没加载出来。 */
function productVariants(x){
  const vs=(x.variants||[]).filter(v=>v&&v.id!=null);
  return vs.length>1?vs:[];
}
function productPrice(x,v){return v&&v.price!=null?Number(v.price):Number(x.price||0)}
function productStock(x,v){return v?Number(v.stock||0):Number(x.stock||0)}
function curVariant(){
  if(!P_CUR)return null;
  return productVariants(P_CUR).find(v=>String(v.id)===String(P_VID))||null;
}
function showProductPane(){
  const shop=document.querySelector('#wmall .w-shop');if(shop)shop.style.display='none';
  const d=$('#productDetail');if(d)d.style.display='block';
}
function showMallList(){
  P_CUR=null;P_VID=null;P_QTY=1;
  const d=$('#productDetail');if(d){d.innerHTML='';d.style.display='none'}
  const shop=document.querySelector('#wmall .w-shop');if(shop)shop.style.display='';
  if(MALL_ALL.length)renderMall();else loadMall();
}
async function openProduct(id){
  const box=$('#productDetail');if(!box)return;
  showProductPane();
  box.innerHTML='<div class="w-pd__sk">'+wSkMini(1)+'</div>';
  let p=MALL_ALL.find(x=>String(x.id)===String(id));
  /* 详情页可能被直接打开或刷新（分享出去的链接），列表缓存里不一定有，
     这时候回源拉一次；拉不到才降级。 */
  if(!p){try{p=await api(`/api/public/clubs/${CLUB}/mall/products/${id}`)}catch(e){p=null}}
  if(!p){
    box.innerHTML=`<div class="w-pad"><div class="w-empty" style="padding:64px 0">${WI.empty}<div>这件装备暂时看不了</div><div style="margin-top:6px">可能已下架，或者链接失效。</div><button class="w-act" type="button" style="margin-top:14px" onclick="showMallList()">返回装备列表</button></div></div>`;
    return;
  }
  P_CUR=p;
  const vs=productVariants(p);
  /* 默认选中第一个还有货的规格：和团期同理，默认落在售罄规格上，顾客点「立即购买」
     会被后端拒，还看不出为什么。全缺货时留第一个，由按钮的 disabled 说明。 */
  const first=vs.find(v=>Number(v.stock||0)>0)||vs[0]||null;
  P_VID=first?first.id:null;P_QTY=1;
  box.innerHTML=productDetailHtml();
  window.scrollTo({top:0,behavior:'instant'});
  bindProductGallery();
}
function productDetailHtml(){
  const p=P_CUR;if(!p)return '';
  const imgs=productImages(p),vs=productVariants(p),v=curVariant();
  const price=productPrice(p,v),stock=productStock(p,v),out=stock<=0;
  const sku=p.sku?String(p.sku):'';
  return `<button class="w-back" type="button" onclick="showMallList()">${WI.back}返回装备</button>
  <div class="w-pd__gal" id="pdGal">
    ${imgs.length?`<div class="w-pd__track" id="pdTrack">${imgs.map((u,i)=>'<div class="w-pd__slide"><img src="'+esc(u)+'" alt="'+esc(p.name)+'"'+(i?' loading="lazy"':'')+'></div>').join('')}</div>`
      :'<div class="w-pd__noimg">'+WI.bag+'<span>暂无商品图片</span></div>'}
    ${imgs.length>1?`<div class="w-pd__dots" id="pdDots">${imgs.map((_,i)=>'<i'+(i===0?' class="on"':'')+'></i>').join('')}</div><div class="w-pd__count" id="pdCount">1/${imgs.length}</div>`:''}
  </div>
  <div class="w-pd__head">
    <div class="w-pd__price">${money(price)}</div>
    <h2 class="w-pd__name">${esc(p.name)}</h2>
    <div class="w-pd__meta">${esc(p.category||'装备')}${sku?' · 货号 '+esc(sku):''}</div>
  </div>
  ${vs.length?`<div class="w-pd__block">
    <div class="w-pd__label">选择规格${v?' <em>'+esc(v.name)+'</em>':''}</div>
    <div class="w-pd__opts">${vs.map(x=>{
      const o=Number(x.stock||0)<=0;
      return '<button class="w-pd__opt'+(String(x.id)===String(P_VID)?' on':'')+(o?' is-out':'')+'" type="button"'+(o?' disabled':'')+' onclick="pickVariant('+x.id+')">'+esc(x.name)+(o?'<i>缺货</i>':(x.price!=null?'<i>'+money(x.price)+'</i>':''))+'</button>';
    }).join('')}</div>
  </div>`:''}
  <div class="w-pd__block">
    <div class="w-pd__label">数量<em>${out?'暂时缺货':'库存 '+stock+' 件'}</em></div>
    <div class="w-pd__qty">
      <button class="w-pd__qbtn" type="button" data-pstep="-1"${P_QTY<=1?' disabled':''} aria-label="减少数量">−</button>
      <output id="pdQty">${P_QTY}</output>
      <button class="w-pd__qbtn" type="button" data-pstep="1"${(out||P_QTY>=stock)?' disabled':''} aria-label="增加数量">+</button>
    </div>
  </div>
  <div class="w-pd__block">
    <div class="w-pd__label">服务</div>
    <div class="w-pd__svc">
      <div><b>平台统一发货</b><span>下单后由平台仓库统一出库，物流单号可在订单里查看。</span></div>
      <div><b>会员折扣自动生效</b><span>结算时按你的会员等级自动计算，不需要先领券。</span></div>
      <div><b>退换与售后</b><span>在「我的 · 装备订单与售后」里申请，审核进度可追踪。</span></div>
    </div>
  </div>
  <div class="w-pd__cta">
    <div class="w-pd__sum"><span>合计</span><b id="pdTotal">${money(price*P_QTY)}</b></div>
    <button class="w-pd__buy" type="button"${out?' disabled':''} onclick="buyProduct()">${out?'暂时缺货':'立即购买'}</button>
  </div>`;
}
/* 换规格只重画整块详情页：规格会同时影响价格、库存、数量上限和合计，
   局部改三四处比整体重渲染更容易漏。重渲染会滚回顶部，所以先把滚动位置存回来。 */
function pickVariant(vid){
  if(!P_CUR)return;
  const v=productVariants(P_CUR).find(x=>String(x.id)===String(vid));
  if(!v||Number(v.stock||0)<=0)return;
  P_VID=vid;P_QTY=1;
  const y=window.scrollY;
  const box=$('#productDetail');if(box)box.innerHTML=productDetailHtml();
  bindProductGallery();window.scrollTo({top:y,behavior:'instant'});
}
function setProductQty(n){
  if(!P_CUR)return;
  const stock=productStock(P_CUR,curVariant());
  P_QTY=Math.min(Math.max(1,Number(n)||1),Math.max(1,stock));
  const v=curVariant(),price=productPrice(P_CUR,v);
  const q=$('#pdQty');if(q)q.textContent=P_QTY;
  const t=$('#pdTotal');if(t)t.textContent=money(price*P_QTY);
  const box=$('#productDetail');
  if(box){
    const dec=box.querySelector('[data-pstep="-1"]'),inc=box.querySelector('[data-pstep="1"]');
    if(dec)dec.disabled=P_QTY<=1;
    if(inc)inc.disabled=P_QTY>=stock;
  }
}
/* 数量步进器的两个按钮是 productDetailHtml() 现场生成的，bindWebEvents() 跑的时候
   它们还不存在 —— 直接 addEventListener 会被静默跳过，按钮看着在、点了没反应
   （报名区的人数步进器踩过同一个坑）。所以挂到 document 上做事件委托。 */
document.addEventListener('click',e=>{
  const b=e.target.closest&&e.target.closest('#productDetail [data-pstep]');
  if(b)setProductQty(P_QTY+Number(b.dataset.pstep));
});
/* 图集用原生 scroll-snap 横滑，监听 scroll 更新指示点 —— 不自己写惯性滚动，
   手机上的手感交给系统。 */
function bindProductGallery(){
  const track=$('#pdTrack');if(!track)return;
  const dots=$('#pdDots'),count=$('#pdCount');
  const upd=()=>{
    const n=track.children.length;if(!n)return;
    const i=Math.round(track.scrollLeft/(track.clientWidth||1));
    if(dots)$$('i',dots).forEach((d,k)=>d.classList.toggle('on',k===i));
    if(count)count.textContent=(i+1)+'/'+n;
  };
  track.addEventListener('scroll',upd,{passive:true});upd();
}
async function buyProduct(){
  const p=P_CUR;if(!p)return;
  const v=curVariant(),stock=productStock(p,v);
  if(stock<=0){showAlert({title:'暂时缺货',message:'这件装备当前没有可售库存。'});return}
  if(P_QTY>stock){showAlert({title:'库存不足',message:'当前规格只剩 '+stock+' 件。'});return}
  const item={productId:p.id,quantity:P_QTY};
  if(v)item.variantId=v.id;
  let w;try{w=await api(`/api/public/users/${USER}/wallet?club_id=${CLUB}`)}catch(e){w={gearPoints:0}}
  let vouchers=[];try{vouchers=await api(`/api/public/clubs/${CLUB}/vouchers?user_id=${USER}&kind=gear`)}catch(e){}
  const label=v?'已选 '+v.name+' × '+P_QTY:'已选 '+P_QTY+' 件';
  let f=await showForm({title:'确认下单',desc:label+'，合计 '+money(productPrice(p,v)*P_QTY)+'。选择本单使用的装备积分与福利券。',submitText:'提交订单',
    fields:[{name:'use',label:'使用装备积分',type:'number',value:0,min:0,help:`当前可用装备积分 ${Number(w.gearPoints||0)}`},
            {name:'voucher',label:'装备福利券',type:'select',value:'',options:[{value:'',label:'不使用福利券'}].concat((vouchers||[]).map(x=>({value:x.voucher_code,label:`${x.title} · 抵 ${money(x.cash_value)}`})))}]});
  if(!f)return;
  try{
    let d=await api(`/api/public/clubs/${CLUB}/gear-checkout`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({userId:USER,items:[item],gearPoints:Number(f.use||0),voucherCodes:f.voucher?[f.voucher]:[]})});
    let paid=await payCheckout(d);if(!paid)return;
    await showAlert({title:'下单成功',message:`实际支付 ${money(paid.cashPaid)}
获得 ${paid.gearPointsEarned} 装备积分${paid.platformBenefitSubsidy?`
平台福利补贴 ${money(paid.platformBenefitSubsidy)}`:''}
俱乐部获得 ${money(paid.clubCommission)} 佣金
俱乐部获得 ${paid.clubAIReward} AI Credits奖励`});
    loadWallet();loadMemberCenter();
    /* 库存变了，回列表要重新拉一次，否则卡片上还是旧库存。 */
    MALL_ALL=[];loadMall();showMallList();
  }catch(e){showAlert({title:'下单失败',message:e.message})}
}

/* ── 我的：毛玻璃会员卡上的身份与升级进度 ─────────────────────────────────── */
/* 升级进度是真算出来的：tiers 里带 min_activity_spend / min_activity_count / qualification_mode。
   qualification_mode=ANY 表示「消费额或场次满足其一即可」，所以进度取两条路径里更近的那条；
   ALL 才取更远的那条。没有下一等级就直接满格，不编一个假的百分比。 */
function renderMemberCard(w,d){
  const tiers=(d.tiers||[]).slice().sort((a,b)=>(Number(a.rank)||0)-(Number(b.rank)||0));
  const cur=tiers.find(t=>String(t.name)===String(w.memberLevel))||tiers[0]||null;
  const up=tiers.find(t=>(Number(t.rank)||0)>(Number(cur&&cur.rank)||0))||null;
  const spend=Number(w.lifetimeActivitySpend||0),cnt=Number(w.activityCount||0);
  const nameEl=$('#meName'),tierEl=$('#meTier'),avEl=$('#meAvatar');
  if(nameEl)nameEl.textContent=PAYER_NAME||'户外会员';
  if(avEl)avEl.textContent=String(PAYER_NAME||'远').trim().slice(0,1)||'远';
  if(tierEl){
    const nm=String(w.memberLevel||'普通会员');tierEl.textContent=nm;
    const gold=/金/.test(nm),silver=/银/.test(nm);
    tierEl.style.background=gold?'linear-gradient(96deg,#e8c46a,#d8a63e)':(silver?'linear-gradient(96deg,#e2e7e9,#b6c0c5)':'rgba(255,255,255,.22)');
    tierEl.style.color=gold?'#4a3406':(silver?'#2c3639':'#fff');
  }
  const tip=$('#meUpTip'),bar=$('#meUpBar');
  if(tip&&bar){
    if(!up){tip.textContent='已是最高等级';bar.style.width='100%'}
    else{
      const need=Number(up.min_activity_spend||0),needN=Number(up.min_activity_count||0);
      const any=String(up.qualification_mode||'ANY').toUpperCase()!=='ALL';
      const ps=need>0?Math.min(1,spend/need):1,pc=needN>0?Math.min(1,cnt/needN):1;
      const prog=any?Math.max(ps,pc):Math.min(ps,pc);
      const parts=[];
      if(need>0)parts.push('再消费 '+money(Math.max(0,need-spend)));
      if(needN>0)parts.push('再参加 '+Math.max(0,needN-cnt)+' 场');
      tip.textContent=(parts.length?parts.join(any?' 或 ':' 且 ')+'，':'')+'可升至「'+up.name+'」';
      /* 差 ¥2 时 498/500 四舍五入成 100%，条是满的、下面却写着「还差 ¥2」——
         满格会被读成「我已经升到银卡了」。没真正达成就压到 99%，视觉与文案一致。 */
      bar.style.width=(prog>=1?100:Math.min(99,Math.round(prog*100)))+'%';
    }
  }
}
/* 会员卡上的图标入口。每个入口都必须落在**它字面说的那块内容**上 ——
   早前「帮助中心」滚到的是装备订单、「积分商城」和「我的卡券」滚到同一个位置，
   点了之后人会以为页面坏了。锚点目标现在逐个对得上。 */
function renderMeGrids(){
  const items=[[WI.ticket,'我的卡券',"wJump('memberRedemptions')"],[WI.gift,'积分商城',"wJump('memberBenefits')"],
    [WI.diamond,'会员权益',"wJump('memberTierPerks')"],[WI.receipt,'我的订单',"wJump('worders')"],
    [WI.help,'常见问题',"openHelp()"]];
  const g=$('#meGrid');
  if(g&&!g.children.length)g.innerHTML=items.map(([ic,lb,act])=>`<button class="w-me__cell" type="button" onclick="${act}">${ic}<span>${lb}</span></button>`).join('');
  /* 「我的服务」不再做成第二排图标格子：上一版它列的「我的订单 / 会员权益」与上面那排
     图标是同一个落点，同一个卡片里出现两遍（顾客会以为其中一个是坏的）。
     改成带去重的深层链接行：每行落到**上面那排到不了的具体位置**，并写清那是什么。 */
  const svc=[
    [WI.receipt,'活动订单','报名资料、参加人与退款进度',"wJump('activityOrders')"],
    [WI.reward,'装备订单与售后','发货、退换货与平台审核进度',"wJump('gearOrders')"],
    [WI.card,'会员信息','姓名、等级与双轨积分明细',"openMemberCard()"]
  ];
  const s=$('#meService');
  if(s&&!s.children.length)s.innerHTML=svc.map(([ic,lb,desc,act])=>`<button class="w-me__row" type="button" onclick="${act}">
    <span class="w-me__rowic">${ic}</span>
    <span class="w-me__rowmain"><b>${lb}</b><em>${desc}</em></span>
    <span class="w-me__rowgo" aria-hidden="true">›</span></button>`).join('');
}
/* 会员卡：展示账户里真实存在的信息。没有可核销的签名码，所以不出示二维码 ——
   竖一个扫不动的码比不放码更伤信任。线下核对用姓名 + 等级。 */
function openMemberCard(){
  const box=$('#meCardBody');const src=$('#wme');
  const CARD=[
    ['俱乐部',document.querySelector('.web-top strong')?.textContent||'远拓户外'],
    ['姓名',PAYER_NAME||'户外会员'],
    ['会员等级',$('#meTier')?.textContent||'普通会员'],
    ['活动积分',($('#clubPts')?.textContent||'—')+' 分'],
    ['装备积分',($('#gearPts')?.textContent||'—')+' 分']
  ];
  uxDialog({title:'会员信息',desc:'线下集合与核销时出示本页，工作人员核对姓名与等级。',
    body:`<div class="w-mcard">${CARD.map(([k,v])=>`<div class="w-mcard__row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('')}</div>
    <p class="w-mcard__note">会员等级与积分实时取自账户；装备折扣在结算时自动生效。</p>`,
    foot:'<button type="button" class="btn ux-x-close" onclick="this.closest(\'.ux-overlay\').uxClose()">知道了</button>'});
  void box;void src;
}
/* 常见问题：写清楚报名、积分、退款、售后四条规则。都是系统里真实存在的规则，
   不是占位文案 —— 点进去有东西可读，比「建设中」有意义。 */
function openHelp(){
  const rows=[
    ['报名与名额','每位参加人占 1 个名额。付款人和参加人可以不同；身份证、紧急联系人等资料可支付后于「我的订单」补填，活动要求支付前补齐时会在提交时提示。'],
    ['积分抵扣','活动积分与装备积分可在报名时勾选抵扣。上限由系统按活动规则和你的余额算好，你只需要选择用或不用，不需要自己填数字。'],
    ['退款规则','每个活动有自己的退款政策，详情页会列出出发前不同时段对应的退款比例。提交申请后按政策计算，积分与福利券按规则恢复。'],
    ['装备售后','装备订单由平台统一发货，售后（仅退款 / 退货退款 / 换货）由平台审核处理，可在「我的订单 → 装备订单」发起并填写退货物流。']
  ];
  uxDialog({title:'常见问题',desc:'报名、积分、退款与售后',
    body:`<div class="w-help">${rows.map(([q,a])=>`<div class="w-help__item"><b>${esc(q)}</b><p>${esc(a)}</p></div>`).join('')}</div>`,
    foot:'<button type="button" class="btn ux-x-close" onclick="this.closest(\'.ux-overlay\').uxClose()">知道了</button>'});
}
/* 列表页的筛选/搜索接线。容器的委托只绑一次（这些容器是静态 HTML，不会被 innerHTML 换掉；
   被换掉的是它们的内容），所以不会像逐次渲染那样反复叠加监听。 */
function bindWebEvents(){
  $('#actSearch')?.addEventListener('input',()=>renderActivityList());
  $('#activityChips')?.addEventListener('click',e=>{const b=e.target.closest('[data-g]');if(!b||b.disabled)return;ACT_GROUP=b.dataset.g;renderActivityChips();renderActivityList()});
  $('#mallCats')?.addEventListener('click',e=>{const b=e.target.closest('[data-c]');if(!b)return;MALL_CAT=b.dataset.c;renderMallCats();renderMall()});
  $('#mallTabs')?.addEventListener('click',e=>{const b=e.target.closest('[data-s]');if(!b)return;MALL_SORT=b.dataset.s;renderMallTabs();renderMall()});
  $('#gearSearch')?.addEventListener('input',()=>renderMall());
  /* 活动卡是 `<div onclick tabindex="0">` —— on 属性只绑 click，键盘按 Enter/空格不会触发，
     少了这段读屏和键盘用户就完全点不进详情页。用委托绑在容器上，重渲染也不用重新绑。 */
  $('#publicActivities')?.addEventListener('keydown',e=>{
    if(e.key!=='Enter'&&e.key!==' ')return;
    const c=e.target.closest('.w-reel__card');if(!c)return;
    e.preventDefault();c.click();
  });
  /* 人数步进器与「移除参加人」。
     这两个节点是 openAct() 里的 bookingHtml() 现场生成的，bindWebEvents() 跑的时候
     它们还不存在 —— 直接 $('#paxStep')?.addEventListener 会被 `?.` 静默吞掉，
     按钮看起来在、点了没反应。所以挂到 document 上做事件委托，用 #bookingCard 限定范围。 */
  document.addEventListener('click',e=>{
    const step=e.target.closest('#bookingCard [data-step]');
    if(step){
      const cur=Number($('#participantCount')?.value||1);
      setParticipantCount(cur+Number(step.dataset.step));
      return;
    }
    const del=e.target.closest('#bookingCard [data-del]');
    if(del)removeParticipant(Number(del.dataset.del));
  });
  /* 首页顶栏在 hero 上是透明的（沉浸）；滚过 hero 之后必须换成实底，
     否则白字会压在正文上糊成一片，且透明底在浅色内容上看不清。 */
  let raf=0;
  addEventListener('scroll',()=>{
    if(raf)return;
    raf=requestAnimationFrame(()=>{
      raf=0;
      document.querySelector('.web-shell')?.classList.toggle('is-scrolled',scrollY>72);
    });
  },{passive:true});
}
/* 底部导航 5 视图：首页 / 活动 / 装备 / 户外能力(建设中) / 我的。
   首页是品牌全屏大图页、活动是列表页 —— 这两个刻意分开：把「品牌印象」和「挑活动的任务」
   塞进同一屏，结果通常是两件事都没做好。
   「我的」= 毛玻璃会员卡 + 全部订单（活动 + 装备）+ 会员福利，所以两个 loader 一起跑。
   b 允许省略：页面里的「全部活动 →」这类入口不必自己去找底部按钮，由这里按 data-wv 反查，
   否则 active 态与 aria-current 会漏更新。 */
/* 只做「切视图 + 高亮对应 tab」这两件事，不碰任何业务状态。
   和 wv 拆开是因为 openAct 也需要它：从首页（hero 的「即刻探索」、本月精选卡）
   点进详情时当前视图还是 whome，而 #publicDetail 属于 #wactivities —— 那个 section
   还是 display:none，详情渲染得再完整也整段看不见，用户看到的就是「点了没反应」。
   openAct 不能直接调 wv：wv 见到 #publicDetail 里已有内容会按「还在详情页」调
   showActivityList() 把列表复位，和紧接着的渲染互相清；而且 wv 还会顺带重绘列表 /
   回源拉数据，这些都不该在打开详情时发生。顺序依赖太脆，所以只共用这一段。 */
function wviewShow(id,tab){
  const view=$('#'+id);if(!view)return false;
  $$('.wview').forEach(x=>x.style.display='none');view.style.display='block';
  tab=tab||document.querySelector('.web-nav button[data-wv="'+id+'"]');
  $$('.web-nav button').forEach(x=>x.classList.remove('active'));tab?.classList.add('active');
  /* 首页顶栏要浮在 hero 照片上，靠 .is-home 切；其余视图顶栏是白底吸顶。 */
  const shell=document.querySelector('.web-shell');
  shell?.classList.toggle('is-home',id==='whome');
  if(id!=='whome')shell?.classList.remove('is-scrolled');
  return true;
}
function wv(id,b){
  if(!wviewShow(id,b))return;
  window.scrollTo({top:0,behavior:'instant'});
  if(id==='whome')loadHome();
  /* 点「活动」tab 的语义是「我要看活动列表」。openAct 把列表区设成了 display:none，
     不复位的话用户点 tab 后看到的还是刚才那张详情页，而 tab 已经高亮在「活动」上 ——
     高亮和内容对不上，会被读成「点了没反应」。 */
  if(id==='wactivities'){
    if($('#publicDetail')&&$('#publicDetail').innerHTML)showActivityList();
    else if($('#activityList'))$('#activityList').style.display='block';
    if(ACT_ALL.length)renderActivityList();else loadActivities();
  }
  /* 和「活动」tab 同一个道理：点「装备」是要看列表。详情页开着时不复位，
     tab 已经高亮在装备上、内容却还是刚才那件商品，会被读成「点了没反应」。 */
  if(id==='wmall'){
    if($('#productDetail')&&$('#productDetail').innerHTML)showMallList();
    else if(MALL_ALL.length)renderMall();else loadMall();
  }
  if(id==='wme'){loadMemberCenter();loadOrders()}
  if(id==='wability')loadWability();
}
async function openAct(id){/* 不先切视图的话，详情会被渲染进一个 display:none 的 section ——
   从首页 hero / 本月精选点进来时，页面看起来毫无反应。 */wviewShow('wactivities');
let a=await api(`/api/public/activities/${id}`);currentAct=a;
/* 默认团期必须是**第一个还有余位**的：早前固定取 occurrences[0]，售罄的第一个团期会被默认选中，
   顾客直接点报名就被后端拒，还看不出为什么。全满时留 null，由 signupNow 给出明确提示。 */
currentOcc=(a.occurrences||[]).find(o=>Number(o.remaining||0)>0)||null;
bookingParticipants=[{name:PAYER_NAME,phone:PAYER_PHONE,relationToPayer:'本人',idType:'',idNumber:'',emergencyContactName:'',emergencyContactPhone:''}];const listEl=$('#activityList');if(listEl)listEl.style.display='none';if($('#publicActivities'))$('#publicActivities').style.display='';$('#publicDetail').innerHTML=`<button class="w-back" type="button" onclick="backList()">${WI.back}返回活动</button><div class="w-detailhero${a.cover?'':' is-fallback'}">${a.cover?`<img src="${esc(a.cover)}" alt="">`:''}<div class="w-detailhero__cap"><h2>${esc(a.title)}</h2><div class="w-detailhero__meta"><span>${esc(a.location||'户外')}</span>${a.event_date?`<span>${esc(a.event_date)}</span>`:''}<span>${money(a.price)} / 人</span></div></div></div><div class="public-editorial">${renderPromo(a.detail,a.activityMaster,{hideButton:true})}</div>${renderInfoStack(a.activityMaster,{gear:a.gearRecommendations,canBuy:true,skip:infoStackSkip(a.detail)})}${bookingHtml(a)}`;window.scrollTo(0,0);renderParticipantForms();await loadActivityVouchers();await refreshQuote()}
/* 回到活动列表。两个入口共用：详情页的「返回活动」、底部「活动」tab。 */
function showActivityList(){
  const l=$('#activityList');if(l)l.style.display='block';
  if($('#publicActivities'))$('#publicActivities').style.display='';
  const d=$('#publicDetail');if(d)d.innerHTML='';
  /* 必须一起清掉报名态：改人数/勾积分/换团期都会发出在途的 refreshQuote，
     只清 DOM 不清状态的话，请求回来时会往已被销毁的 #quoteBox 里写，抛错且看不见。 */
  currentAct=null;currentOcc=null;
  history.replaceState({},'',location.pathname);
}
function backList(){showActivityList()}
function bookingHtml(a){
  const p=a.pointsPolicy||{}; const e=p.effective||{};
  /* 抵扣不该是顾客的算术题：额度由系统算好（后端 maxRedeemable），顾客只勾一下「用 / 不用」。
     早前这里是两个空的 number 输入框，等于把「我该抵多少」甩给顾客；更糟的是顾客乱填也看不出
     错在哪。数值一律来自同一套封顶规则，前端不自己算百分比 —— 否则会出现「页面说能抵 30、
     结账只抵 0」这种对不上的账。 */
  const clubBox=e.acceptClubPoints?pointBlock('club','活动积分','本俱乐部资产 · 成本由俱乐部承担',p.clubPointsMaxDiscountPercent<100?`本活动最多抵活动金额的 ${p.clubPointsMaxDiscountPercent}%`:''):'';
  const gearBox=e.acceptGearPoints?pointBlock('gear','装备积分','平台资产 · 抵扣由总平台补贴',p.gearPointsMaxDiscountAmount==null?'':`本活动最多补贴 ${money(p.gearPointsMaxDiscountAmount)}`):'';
  let pointArea='';
  if(p.enabled && (clubBox||gearBox)){pointArea=`<div class="point-row">${clubBox}${gearBox}</div>`}
  else if(!p.enabled){pointArea='<div class="notice" style="margin-top:14px">本活动不参与积分抵扣。</div>'}
  const earnNote=e.earnClubPoints?'<div class="notice" style="margin-top:10px">报名完成后，本次现金实付金额将按俱乐部规则累计活动积分。</div>':'';
  const benefitArea=`<div id="activityBenefitArea" style="margin-top:12px"></div>`;
  const pp=a.participantPolicy||{};
  const maxPax=Number(pp.maxParticipantsPerOrder||8);
  const cur=bookingParticipants.length;
  /* 人数改成 −／＋ 步进器：手机上的 number 输入框那两个 4px 的小箭头几乎点不中，
     而「几人」是报名时唯一必改的数字。 */
  const participantArea=`<div class="w-block"><div class="w-block__head">
      <div><b>参加人</b><div class="w-block__hint">付款人和参加人可以不同；每位参加人占 1 个名额。</div></div>
      <div class="w-stepper" id="paxStep" role="group" aria-label="参加人数">
        <button type="button" data-step="-1" aria-label="减少一位参加人"${cur<=1?' disabled':''}>−</button>
        <output aria-live="polite">${cur}</output>
        <button type="button" data-step="1" aria-label="增加一位参加人"${cur>=maxPax?' disabled':''}>+</button>
      </div>
    </div>
    <div id="paxForms"></div>
    <div class="w-block__foot">${pp.allowIncompleteAtCheckout===false?'本活动要求支付前完成全部报名资料。':'可先报名支付；身份证 / 紧急联系人等资料可在「我的订单」后补。'}${pp.insuranceRequired?' · 本活动需要保险资料。':''}${cur>=maxPax?` · 单笔最多 ${maxPax} 人`:''}</div>
    <input type="hidden" id="participantCount" value="${cur}"></div>`;
  return `<div class="booking-card" id="bookingCard"><div class="eyebrow">BOOK THIS TRIP</div><h2 style="margin:6px 0 2px">选择团期</h2><div class="sub">同一活动可有不同日期、不同价格和不同名额。</div><div class="occ-list" role="radiogroup" aria-label="选择团期">${(a.occurrences||[]).map((o,i)=>occCard(o,i)).join('')||'<div class="notice warn">暂无可报名团期</div>'}</div>${(a.occurrences||[]).length>2?'<div class="occ-hint">← 左右滑动查看全部团期 →</div>':''}${participantArea}${pointArea}${benefitArea}${earnNote}${refundPolicyBrief(a.refundPolicy)}<div class="quote-box" id="quoteBox"><div class="sub" style="color:#b8c8c2">正在计算…</div></div><button class="w-submit" type="button" onclick="signupNow()"${currentOcc?'':' disabled'}><span>${currentOcc?'立即报名':'暂无可报名团期'}</span><b id="payHint">—</b></button></div>`
}
/* 团期卡：横向滑动的竖版卡片。竖排长列表在团期一多时把报名页拉得很长，
   而且「哪天 / 多少钱」要上下扫着比；卡片固定宽、一次并排露出两张左右，
   日期、类型、名额、价格各占一行，左右滑动 + scroll-snap 吸附即可比较。
   满员的团期**不可选**（而不是可选然后被后端拒），并且明确标出「已满」。 */
function occCard(o,i){
  const full=Number(o.remaining||0)<=0;
  const on=currentOcc&&Number(currentOcc.id)===Number(o.id);
  // label 常是「10月24日 · 标准团」的写法：拆成日期 + 团型徽标两段；
  // 拆不开就整段当日期，不硬造结构。
  const raw=String(o.label||o.start_at||'待定');
  const parts=raw.split('·').map(s=>s.trim()).filter(Boolean);
  const day=parts[0]||raw, kind=parts.slice(1).join(' · ');
  return `<div class="occ${on?' active':''}${full?' is-full':''}" data-occ="${o.id}"${full?'':' onclick="selectOcc('+o.id+',this)"'} tabindex="${full?'-1':'0'}" role="radio" aria-checked="${on?'true':'false'}" aria-disabled="${full?'true':'false'}" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();if(!${full})this.click()}">
    <div class="occ__top"><strong>${esc(day)}</strong>${kind?`<span class="occ__kind">${esc(kind)}</span>`:''}</div>
    <div class="occ__sub">${full?'<em>已满</em>':`剩余 ${o.remaining} / ${o.capacity} 个名额`}</div>
    <div class="occ__price">${money(o.price)}<span> / 人</span></div>
    <span class="occ__check" aria-hidden="true">✓</span>
  </div>`;
}
function refundPolicyBrief(p){
  if(!p)return '';
  if(!p.enabled)return '<div class="notice warn" style="margin-top:10px"><strong>退款规则</strong><div class="sub">本活动不支持用户自主退款；如遇特殊情况请联系俱乐部。</div></div>';
  const rules=(p.rules||[]).map(r=>`${r.label||`出发前${r.minHoursBefore}小时`}：现金退 ${Number(r.cashRefundPercent)}%`).join(' · ');
  return `<div class="notice" style="margin-top:10px"><strong>退款规则</strong><div class="sub">${esc(rules||'以订单申请时系统计算为准')}${p.afterStartCashRefundPercent?` · 活动开始后退 ${Number(p.afterStartCashRefundPercent)}%`:' · 活动开始后不退'}</div></div>`
}
function setParticipantCount(v){
  const max=Number(currentAct?.participantPolicy?.maxParticipantsPerOrder||8),n=Math.max(1,Math.min(max,Number(v||1)));
  while(bookingParticipants.length<n)bookingParticipants.push({name:'',phone:'',relationToPayer:'同行人',idType:'',idNumber:'',emergencyContactName:'',emergencyContactPhone:''});
  bookingParticipants=bookingParticipants.slice(0,n);
  if($('#participantCount'))$('#participantCount').value=n;
  /* 步进器的数字与两端的禁用态一并更新：只改隐藏 input 的话，加号到了上限还能点。 */
  const step=$('#paxStep');
  if(step){
    const out=step.querySelector('output');if(out)out.textContent=n;
    const minus=step.querySelector('[data-step="-1"]'),plus=step.querySelector('[data-step="1"]');
    if(minus)minus.disabled=n<=1;
    if(plus)plus.disabled=n>=max;
    const foot=document.querySelector('#bookingCard .w-block__foot');
    const ins=currentAct?.participantPolicy?.insuranceRequired?' · 本活动需要保险资料。':'';
    if(foot)foot.textContent=`${currentAct?.participantPolicy?.allowIncompleteAtCheckout===false?'本活动要求支付前完成全部报名资料。':'可先报名支付；身份证 / 紧急联系人等资料可在「我的订单」后补。'}${ins}${n>=max?` · 单笔最多 ${max} 人`:''}`;
  }
  renderParticipantForms(); refreshQuote();
}
function removeParticipant(i){
  if(bookingParticipants.length<=1){toast('至少保留 1 位参加人');return}
  bookingParticipants.splice(i,1);
  setParticipantCount(bookingParticipants.length);
}
function participantField(i,key,val){bookingParticipants[i][key]=val}
/* 参加人资料：每位参加人一张卡、字段各自带标签。
   早前是 6 个裸 input 挤在一条 .notice 里、只有 placeholder 没有标签 —— 填到第 6 个框
   就记不清这行到底是「证件号码」还是「紧急联系人电话」。 */
function renderParticipantForms(){
  const box=$('#paxForms');if(!box)return;
  box.innerHTML=bookingParticipants.map((p,i)=>`<div class="w-pax">
    <div class="w-pax__hd"><span class="w-pax__idx">${WI.pax}</span><b>参加人 ${i+1}</b>${i===0?'<span class="w-pax__tag">可与付款人相同</span>':''}
      ${bookingParticipants.length>1?`<button class="w-pax__del" type="button" data-del="${i}" aria-label="移除参加人 ${i+1}">移除</button>`:''}</div>
    <div class="w-pax__grid">
      ${paxField(i,'name','姓名',p.name,{req:1,ph:'与证件一致'})}
      ${paxField(i,'phone','手机号',p.phone,{req:1,type:'tel',ph:'11 位手机号'})}
      ${paxField(i,'idType','证件类型',p.idType,{ph:'如 身份证'})}
      ${paxField(i,'idNumber','证件号码',p.idNumber,{ph:'可支付后补'})}
      ${paxField(i,'emergencyContactName','紧急联系人',p.emergencyContactName,{ph:'可支付后补'})}
      ${paxField(i,'emergencyContactPhone','紧急联系人电话',p.emergencyContactPhone,{ph:'可支付后补'})}
    </div></div>`).join('');
}
function paxField(i,key,label,val,o){
  o=o||{};
  return `<label class="w-fld"><span>${label}${o.req?'<i aria-hidden="true">*</i>':''}</span>
    <input type="${o.type||'text'}" value="${esc(val||'')}" placeholder="${o.ph||''}" autocomplete="off" oninput="participantField(${i},'${key}',this.value)"></label>`;
}
async function loadActivityVouchers(){
  if(!currentAct||!$('#activityBenefitArea'))return;
  try{activityVouchers=await api(`/api/public/clubs/${CLUB}/vouchers?user_id=${USER}&kind=activity`)}catch(e){activityVouchers=[]}
  const box=$('#activityBenefitArea');
  if(!activityVouchers.length){box.innerHTML='';return}
  box.innerHTML=`<div class="point-box"><b>会员福利券</b><div class="sub">积分兑换后的福利券可在交易中真正核销，成本归属保持不变。</div><select id="benefitUse" onchange="refreshQuote()" style="width:100%;margin-top:8px;padding:10px;border-radius:10px"><option value="">本单不使用福利券</option>${activityVouchers.map(v=>`<option value="${esc(v.voucher_code)}">${esc(v.title)} · 抵 ${money(v.cash_value)} · ${v.funding_owner==='CLUB'?'俱乐部承担':'平台承担'}</option>`).join('')}</select></div>`
}
function selectOcc(id,el){
  currentOcc=currentAct.occurrences.find(x=>x.id===id);
  if(!currentOcc||Number(currentOcc.remaining||0)<=0){toast('该团期已满，请选择其他团期');return}
  $$('.occ').forEach(x=>{x.classList.remove('active');x.setAttribute('aria-checked','false')});
  el.classList.add('active');el.setAttribute('aria-checked','true');
  /* 横向卡片：点到的卡可能只露出一半，选中后把它滚到可视区中央
     （block:'nearest' 保证不带动页面竖向滚动）。 */
  if(el.scrollIntoView)try{el.scrollIntoView({behavior:'smooth',block:'nearest',inline:'center'})}catch(e){}
  refreshQuote()
}
function pointBlock(kind,title,owner,capNote){
  const club=kind==='club';
  return `<div class="point-box"><div class="point-box__hd"><b>${title}</b><label class="point-toggle"><input type="checkbox" id="${club?'clubUseToggle':'gearUseToggle'}" onchange="togglePoints('${kind}',this.checked)">用积分抵扣</label></div><div class="sub">${owner}${capNote?` · ${capNote}`:''}</div><div class="point-cap" id="${club?'clubUseCap':'gearUseCap'}">正在计算本单最多可抵多少…</div><input type="hidden" id="${club?'clubUse':'gearUse'}" value="0"></div>`
}
/* 勾选 = 用系统算出的上限；取消 = 归零。顾客不需要、也没机会填任何数字。 */
function togglePoints(kind,on){
  const box=$(kind==='club'?'#clubUse':'#gearUse');if(!box)return;
  box.value=on?String(Number(box.dataset.max||0)):0;
  refreshQuote();
}
/* 把 maxRedeemable 落到界面上，并回答一个「本单能不能收敛」的问题：
   返回 true 表示当前提交值与勾选状态不一致（已就地修正），调用方需要重算一次报价。 */
function syncPointConfirm(kind,mx){
  const club=kind==='club',cb=$(club?'#clubUseToggle':'#gearUseToggle'),capEl=$(club?'#clubUseCap':'#gearUseCap'),box=$(club?'#clubUse':'#gearUse');
  if(!cb||!capEl||!box)return false;
  const max=Number(mx?.points||0),cash=Number(mx?.cash||0),balance=Number(mx?.balance||0);
  box.dataset.max=String(max);
  cb.disabled=max<=0;
  if(max<=0){
    capEl.innerHTML=mx&&mx.allowed===false?'本活动不支持用这类积分抵扣。':(balance<=0?'当前没有可用积分。':'本单暂无可抵扣额度。');
  }else{
    /* 说清上限卡在哪：两种成因句式保持一致，都写成「（…上限）」。
       早前 balance 侧写「你的可用积分已全部用上」，在顾客还没勾选时就出现「已用上」，
       读起来像已经扣了；括号里只陈述上限来源，不描述已发生的事。 */
    const why=mx.limiter==='policy'?'（本活动抵扣上限）':(mx.limiter==='balance'?'（你的积分余额上限）':'');
    capEl.innerHTML=`最多可抵 <b>${money(cash)}</b> · 使用 ${max} 积分${why}<div class="sub">可用 ${balance} 积分</div>`;
  }
  const want=cb.checked?String(max):'0';
  if(String(box.value||'0')!==want){box.value=want;return true}
  return false;
}
async function refreshQuote(depth=0){if(!currentAct||!currentOcc||!$('#quoteBox'))return;let cp=Number($('#clubUse')?.value||0),gp=Number($('#gearUse')?.value||0),voucher=$('#benefitUse')?.value||'';try{let q=await api(`/api/public/activities/${currentAct.id}/price-quote?occurrence_id=${currentOcc.id}&user_id=${USER}&club_points=${cp}&gear_points=${gp}&voucher_codes=${encodeURIComponent(voucher)}&participant_count=${bookingParticipants.length}`);/* 换团期/改人数/刚勾上都会让上限变化，这里把提交值收敛回系统算出的上限，
   否则会出现「界面写着最多抵 ¥30、实际却按 0 抵扣下单」。depth 只是防呆上限，正常一到两次就稳定。 */let drifted=false;if(q.maxRedeemable){const a=syncPointConfirm('club',q.maxRedeemable.club);const b=syncPointConfirm('gear',q.maxRedeemable.gear);drifted=a||b}/* 抵扣行只在真的减了钱时才出现：没勾积分也画一行「活动积分抵扣 - ¥0」，
   顾客会以为系统扣了什么、或者以为抵扣坏了 —— 零减项不是「零」这件事值得看的信息。 */let lines=`<div class="quote-box__hd">订单摘要</div><div class="quote-line"><span>活动费用（${q.participantCount} 人 × ${money(q.unitPrice)}）</span><span>${money(q.original)}</span></div>`;if(q.pointsPolicy?.effective?.acceptClubPoints&&Number(q.clubPointDiscount||0)>0)lines+=`<div class="quote-line"><span>活动积分抵扣（俱乐部承担）</span><span class="is-cut">- ${money(q.clubPointDiscount)}</span></div>`;if(q.pointsPolicy?.effective?.acceptGearPoints&&Number(q.platformPointSubsidy||0)>0)lines+=`<div class="quote-line"><span>装备积分补贴（平台承担）</span><span class="is-cut">- ${money(q.platformPointSubsidy)}</span></div>`;(q.benefits?.applied||[]).forEach(v=>{lines+=`<div class="quote-line"><span>${esc(v.title)}（${v.fundingOwner==='CLUB'?'俱乐部承担':'平台承担'}）</span><span class="is-cut">- ${money(v.cashValue)}</span></div>`});lines+=`<div class="quote-line total"><span>需支付</span><span>${money(q.payable)}</span></div>`;let balances=[];if(q.pointsPolicy?.effective?.acceptClubPoints)balances.push(`活动积分 ${q.wallet.clubPoints}`);if(q.pointsPolicy?.effective?.acceptGearPoints)balances.push(`装备积分 ${q.wallet.gearPoints}`);if(balances.length)lines+=`<div class="sub" style="color:#a9bbb4;margin-top:8px">可用：${balances.join(' · ')}</div>`;/* await 期间详情页可能已被「返回活动」清空（backList 会置空 currentAct/currentOcc），
   容器没了就不能再往里写 —— 否则这里抛错，还会被下面的 catch 再抛一次。 */
const qb=$('#quoteBox');if(!qb)return;qb.innerHTML=lines;
/* 按钮上直接写清要付多少：顾客在点「立即报名」之前唯一真正想知道的就是这个数字，
   把它放在按钮上，省掉一次「点了才知道多少钱」的往返。 */
const hint=$('#payHint');if(hint)hint.textContent=money(q.payable);
if(drifted&&depth<4)return refreshQuote(depth+1)}catch(e){const qb2=$('#quoteBox');if(qb2)qb2.textContent=e.message}}
async function waitForCheckoutPaid(checkoutId,attempts=45){for(let i=0;i<attempts;i++){await new Promise(r=>setTimeout(r,2000));let x=await api(`/api/public/checkouts/${checkoutId}`);if(x.status==='paid'||x.payment_status==='succeeded')return x.result||x;if(x.payment_status==='failed')throw new Error('支付失败，可重新发起支付')}throw new Error('支付状态仍在处理中，请稍后到“我的订单”查看')}
async function payCheckout(checkout){let p=await api(`/api/public/checkouts/${checkout.checkoutId}/pay`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({simulateSuccess:!Boolean(clubosCookie('clubos_csrf')),returnUrl:location.href})});if(p.paymentStatus==='succeeded')return p.result;let a=p.paymentAction||{};if(a.type==='redirect'&&a.url){location.href=a.url;return null}if(a.type==='jsapi'&&a.params){if(window.WeixinJSBridge){await new Promise((resolve,reject)=>WeixinJSBridge.invoke('getBrandWCPayRequest',a.params,r=>String(r.err_msg||'').includes(':ok')?resolve(r):reject(new Error(r.err_msg||'微信支付未完成'))));return await waitForCheckoutPaid(checkout.checkoutId)}showAlert({title:'无法唤起微信支付',message:'当前页面不在微信 JSAPI 环境，请在微信内打开'});return null}if(a.type==='qrcode'&&a.url){await showAlert({title:'请扫码完成支付',message:'微信 Native 支付 code_url：'+a.url,wide:true});return await waitForCheckoutPaid(checkout.checkoutId,15)}return null}
async function signupNow(){if(!currentAct||!currentOcc){showAlert({title:'请选择团期',message:'当前没有可报名的团期，请稍后再试或联系俱乐部。'});return}let cp=Number($('#clubUse')?.value||0),gp=Number($('#gearUse')?.value||0),voucher=$('#benefitUse')?.value||'';try{let d=await api(`/api/public/activities/${currentAct.id}/checkout`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:PAYER_NAME,phone:PAYER_PHONE,occurrenceId:currentOcc.id,clubPoints:cp,gearPoints:gp,voucherCodes:voucher?[voucher]:[],participants:bookingParticipants})});let paid=await payCheckout(d);if(!paid)return;await showAlert({title:'报名成功',message:`${paid.participantCount||bookingParticipants.length} 人\n实际支付 ${money(paid.cashPaid)}\n本次获得 ${paid.clubPointsEarned} 活动积分${paid.clubBenefitDiscount?`\n俱乐部福利抵扣 ${money(paid.clubBenefitDiscount)}`:''}${paid.platformBenefitSubsidy?`\n平台福利补贴 ${money(paid.platformBenefitSubsidy)}`:''}`});await loadWallet();openAct(currentAct.id)}catch(e){showAlert({title:'操作失败',message:e.message})}}
async function buy(pid){let w=await api(`/api/public/users/${USER}/wallet?club_id=${CLUB}`);let vouchers=[];try{vouchers=await api(`/api/public/clubs/${CLUB}/vouchers?user_id=${USER}&kind=gear`)}catch(e){}let v=await showForm({title:'确认下单',desc:'选择本单使用的装备积分与福利券。',submitText:'提交订单',fields:[{name:'use',label:'使用装备积分',type:'number',value:0,min:0,help:`当前可用装备积分 ${w.gearPoints}`},{name:'voucher',label:'装备福利券',type:'select',value:'',options:[{value:'',label:'不使用福利券'}].concat(vouchers.map(x=>({value:x.voucher_code,label:`${x.title} · 抵 ${money(x.cash_value)}`})))}]});if(!v)return;try{let d=await api(`/api/public/clubs/${CLUB}/gear-checkout`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:USER,items:[{productId:pid,quantity:1}],gearPoints:Number(v.use||0),voucherCodes:v.voucher?[v.voucher]:[]})});let paid=await payCheckout(d);if(!paid)return;await showAlert({title:'下单成功',message:`实际支付 ${money(paid.cashPaid)}\n获得 ${paid.gearPointsEarned} 装备积分${paid.platformBenefitSubsidy?`\n平台福利补贴 ${money(paid.platformBenefitSubsidy)}`:''}\n俱乐部获得 ${money(paid.clubCommission)} 佣金\n俱乐部获得 ${paid.clubAIReward} AI Credits奖励`});loadWallet();loadMemberCenter()}catch(e){showAlert({title:'下单失败',message:e.message})}}
/* 「户外能力」页。后端没有任何 skill / ability / rating 表，所以这里不编造能力值，
   只用该用户在俱乐部的真实记录：报名过的活动 + 买过的装备。
   项目类型是从活动标题归纳出来的，页面上就按「参加过的项目类型」来写，
   不冒充技能等级或官方认证。没有的那一项直接说没有，不拿相似内容顶替。 */
const WABILITY_KEYS=[['徒步',/徒步|越野|walk|trail/i],['登山',/登山|雪山|雪线|攀登|岩/i],['溯溪',/溯溪|溪|瀑/i],
  ['露营',/露营|营地|帐篷|宿营/i],['滑雪',/滑雪|雪场|单板|双板/i],['皮划艇',/皮划艇|皮艇|舟/i],
  ['桨板',/桨板|SUP|冲浪/i],['骑行',/骑行|自行车|单车/i],['跑步',/跑|马拉松|越野跑/i],['航海',/帆|船/i]];
async function loadWability(){
  const box=$('#wabilityRoutes');
  if(box&&!box.children.length)box.innerHTML='<div class="w-sk"><div style="height:52px;border-radius:10px;background:#eef2ef"></div>'.repeat(2)+'</div>';
  let d=await api(`/api/public/clubs/${CLUB}/member-center?user_id=${USER}`);
  const h=d.activityHistory||[],g=d.gearOwned||[];
  /* 展示给会员的文案不能漏英文：status 是库里的原始值， refunded / cancelled 都要翻成中文，
     否则「线路履历」里会出现一个客人看不懂的 refunded。 */
  const tier=(x)=>x.refund_status&&x.refund_status!=='none'?'已退款':x.status==='paid'?'已参加':x.status==='refunded'?'已退款':x.status==='cancelled'?'已取消':x.status==='pending'?'待支付':String(x.status||'');
  /* WABILITY_KEYS 是 [名称, 正则] 的平铺对，解构要取第二个才是正则：
     写成 ([re]) 会把中文名当正则拿去 test()，整个函数当场抛错、页面永远停在骨架屏。 */
  const kinds=WABILITY_KEYS.filter(([,re])=>h.some(a=>re.test(String((a.title||'')+(a.location||''))))).map(x=>x[0]);
  $('#wabilitySummary').innerHTML=[['参加过',h.length+' 场活动'],['去过的地点',new Set(h.map(a=>a.location||'')).size+' 个'],
    ['用过的装备',g.length+' 件'],['装备累计',money(g.reduce((s,x)=>s+Number(x.unit_price||0)*Number(x.quantity||1),0))]]
    .map(x=>'<div style="background:#fff;border:1px solid var(--w-line);border-radius:14px;padding:14px 16px"><div style="font-size:11.5px;color:var(--w-muted)">'+(x[0]||'—')+'</div><div style="font-size:21px;font-weight:700;margin-top:5px">'+x[1]+'</div></div>').join('');
  $('#wabilityRoutes').innerHTML=h.length?h.map(a=>'<div style="display:flex;gap:12px;align-items:center;background:#fff;border:1px solid var(--w-line);border-radius:14px;padding:13px 15px;margin-bottom:9px">'
    +(a.cover?'<img src="'+esc(a.cover)+'" alt="" style="width:48px;height:48px;border-radius:10px;object-fit:cover">':'<span style="width:48px;height:48px;border-radius:10px;background:#eef2ef;display:block"></span>')
    +'<div style="min-width:0"><div style="font-size:13px;font-weight:600">'+esc(a.title)+'</div><div style="font-size:11.5px;color:var(--w-muted);margin-top:3px">'+esc(a.event_date||'')+' · '+esc(a.location||'')+' · '+money(a.amount||0)+'</div></div>'
    +'<span style="margin-left:auto;font-size:11px;color:#5c6b62;white-space:nowrap">'+esc(tier(a))+'</span></div>').join(''):'<div class="w-empty">还没有报名记录。报名参加活动后，这里会变成你的线路履历。</div>';
  $('#wabilitySkills').innerHTML='<h3 style="margin:0 0 6px;font-size:15px">参加过的项目类型</h3>'
    +'<div class="sub" style="margin-bottom:12px">按你实际报名过的活动标题归纳，不是技能认证。</div>'
    +(kinds.length?kinds.map(k=>'<span style="display:inline-block;margin:0 8px 8px 0;padding:6px 12px;border-radius:99px;background:#eaf3ec;color:#2c5c42;font-size:12px">'+esc(k)+'</span>').join('')
      :'<div class="w-empty" style="padding:26px 12px">暂无。参加活动后，这里会按活动类型归纳。</div>');
  /* 同一件商品会在不同订单里重复出现，26 行明细里其实可能只有 6 件不同的东西。
     先按商品聚合成「买了几次 / 共几件 / 花了多少」，列表才有信息量。 */
  /* 同一件商品会跨订单重复出现，而且 variant_name 在不同订单里可能一个是 '默认'、
     一个是 null —— 按 id+变体分组会把「轻量防风软壳」拆成前后两行，看起来像买了两样东西。
     所以按商品本身聚合，变体名收进一个集合，只有真有多种变体时才在副标题里列出来。 */
  const agg={};
  for(const x of g){const k=x.product_id;const a=agg[k]||(agg[k]={id:x.product_id,name:x.product_name,category:x.category,qty:0,times:0,spend:0,variants:[]});
    a.qty+=Number(x.quantity||1);a.times+=1;a.spend+=Number(x.unit_price||0)*Number(x.quantity||1);
    const vn=String(x.variant_name||'').trim();if(vn&&a.variants.indexOf(vn)<0)a.variants.push(vn);}
  const gl=Object.values(agg);
  const gearSpend=gl.reduce((s,x)=>s+x.spend,0);
  $('#wabilityGear').innerHTML='<h3 style="margin:0 0 6px;font-size:15px">买过的装备</h3>'
    +'<div class="sub" style="margin-bottom:12px">来自你的真实商城订单，不是推荐也不是库存。'+gl.length+' 种 / 累计 '+money(gearSpend)+'</div>'
    +(gl.length?gl.map(x=>'<div style="display:flex;gap:10px;align-items:center;padding:11px 0;border-top:1px solid var(--w-line)">'
      +'<div style="min-width:0;flex:1"><div style="font-size:12.5px;font-weight:600">'+esc(x.name||'?')+'</div>'
      +'<div style="font-size:11px;color:var(--w-muted);margin-top:3px">'+esc(x.category||'户外装备')+' · 共 '+x.qty+' 件'
      +(x.times>1?' · 买了 '+x.times+' 次':'')
      +(x.variants.length>1?' · '+esc(x.variants.join(' / ')):'')+'</div></div>'
      +'<div style="font-size:12px;font-weight:600;white-space:nowrap">'+money(x.spend)+'</div></div>').join('')
      :'<div class="w-empty" style="padding:26px 12px">暂无。在装备商城买过东西后，这里会列出你真实用过的装备。</div>');
}
async function loadMemberCenter(){
  const mbox=$('#memberBenefits'),rbox=$('#memberRedemptions');
  if(mbox&&!mbox.children.length)mbox.innerHTML=wSkRows(2);
  if(rbox&&!rbox.children.length)rbox.innerHTML=wSkRows(1);
  let d=await api(`/api/public/clubs/${CLUB}/member-center?user_id=${USER}`),w=d.wallet||{};
  if($('#clubPts'))$('#clubPts').textContent=w.clubPoints??0;if($('#gearPts'))$('#gearPts').textContent=w.gearPoints??0;
  if($('#memberLevel'))$('#memberLevel').innerHTML=`<strong>${esc(w.memberLevel||'普通会员')}</strong> · 已参加 ${w.activityCount||0} 场活动 · 累计活动消费 ${money(w.lifetimeActivitySpend||0)}`;
  renderMemberCard(w,d);
  /* 会员等级权益：装备折扣是「加入会员更便宜」的唯一凭证，只在后台配置、C 端看不到
     就等于没做。这里把当前折扣与「再消费能降到几折」讲清楚。 */
  if($('#memberTierPerks')){
    const tiers=d.tiers||[],rate=t=>{const v=Number(t&&t.gear_discount);return v>0&&v<1?v:0};
    const cur=tiers.find(t=>String(t.name||'')===String(w.memberLevel||''))||null;
    const up=tiers.filter(t=>(Number(t.rank)||0)>(Number(cur&&cur.rank)||0)).sort((a,b)=>(Number(a.rank)||0)-(Number(b.rank)||0))[0]||null;
    const on=rate(cur),lines=[];
    lines.push(on
      ? `会员权益：装备商城 <b>${(Math.round(on*1000)/100)} 折</b> —— 在活动出行清单与装备商城里直接看到会员价。`
      : '当前等级的装备商城按原价结算。');
    const nu=rate(up);
    /* 升级目标名已经在上面「升级条件」那行写过了，这里不重复念一遍等级名，
       只补一条它给不出的信息：升上去以后折扣变成几折。没有折扣变化就不加这一句。 */
    if(up&&nu&&(!on||nu<on))lines.push(`升级后装备商城折扣可降到 <b>${(Math.round(nu*1000)/100)} 折</b>。`);
    $('#memberTierPerks').innerHTML=lines.join(' ');
  }
  /* 福利条目做成「成本归属 + 积分成本 + 权益价值 + 兑换」四段，
     而不是一句话糊在一起 —— 顾客要判断的是「这个值不值这么多积分」。 */
  if($('#memberBenefits'))$('#memberBenefits').innerHTML=(d.benefits||[]).map(x=>{
    const pt=x.points_type==='club'?'活动积分':'装备积分';
    const owner=x.owner_type==='CLUB'?'俱乐部承担':'ClubOS 平台承担';
    return `<div class="w-bnf">
      <div class="w-bnf__hd"><b>${esc(x.title)}</b>${badge(owner,x.owner_type==='CLUB'?'mute':'info')}</div>
      ${x.description?`<p class="w-bnf__desc">${esc(x.description)}</p>`:''}
      <div class="w-bnf__ft">
        <div class="w-bnf__cost"><b>${x.points_cost}</b><span>${pt}</span>${x.cash_value?`<em>权益价值 ${money(x.cash_value)}</em>`:''}</div>
        <button class="w-bnf__btn" type="button" onclick="redeemBenefit(${x.id})">兑换</button>
      </div></div>`}).join('')||'<div class="w-empty">'+WI.gift+'<div>暂无可兑换福利</div></div>';
  if($('#memberRedemptions'))$('#memberRedemptions').innerHTML=(d.redemptions||[]).map(x=>`<div class="w-rdm">
      <div class="w-rdm__hd"><b>${esc(x.title)}</b>${badge(x.status==='issued'?'可使用':x.status==='held'?'结算中':x.status==='used'?'已使用':x.status,stTone(x.status))}</div>
      <div class="w-rdm__meta">${x.points_spent} ${x.point_type==='club'?'活动积分':'装备积分'} · ${x.funding_owner==='CLUB'?'俱乐部承担':'平台承担'}</div>
      ${x.voucher_code?`<div class="w-rdm__code">券码 <code>${esc(x.voucher_code)}</code></div>`:''}
    </div>`).join('')||'<div class="w-empty">'+WI.ticket+'<div>还没有兑换记录</div></div>';
}
async function redeemBenefit(id){if(!(await showConfirm({title:'兑换会员福利',message:'确认兑换这项会员福利？',confirmText:'确认兑换'})))return;try{let r=await api(`/api/public/clubs/${CLUB}/benefits/${id}/redeem`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:USER})});await showAlert({title:'兑换成功',message:`券码：${r.voucherCode}\n使用 ${r.pointsSpent} ${r.pointsType==='club'?'活动积分':'装备积分'}`});loadMemberCenter()}catch(e){showAlert({title:'兑换失败',message:e.message})}}
async function loadWallet(){try{let d=await api(`/api/public/users/${USER}/wallet?club_id=${CLUB}`);if($('#clubPts'))$('#clubPts').textContent=d.clubPoints;if($('#gearPts'))$('#gearPts').textContent=d.gearPoints}catch(e){}}

function refundStateText(x){
  const p=x.refundProgress||{}; return `${p.label||'无退款申请'}${p.detail?` · ${p.detail}`:''}`
}
/* 订单区：一条订单 = 标题 + 状态徽章 + 三行关键信息 + 参加人明细 + 操作。
   早前每条订单是一段用 border-bottom 拼的裸 div，参加人那行还带一个 👤 emoji 和
   「保险 pending」这种后端枚举 —— 顾客没法从里面读出「我这单现在要做什么」。 */
async function loadOrders(){
  const abox=$('#activityOrders'),gbox=$('#gearOrders');
  if(abox&&!abox.children.length)abox.innerHTML=wSkRows(1);
  if(gbox&&!gbox.children.length)gbox.innerHTML=wSkRows(1);
  let d=await api(`/api/public/users/${USER}/order-center?club_id=${CLUB}`);
  $('#activityOrders').innerHTML=(d.activityOrders||[]).map(x=>{
    const rq=x.refundQuote;
    let action='';
    if(x.status==='paid' && ['none','rejected'].includes(x.refund_status||'none')){
      action=rq?.eligible?`<button class="w-act w-act--danger" type="button" onclick="requestActivityRefund(${x.id},${rq.cashRefundAmount||0},${rq.cashRefundPercent||0})">申请退款</button>`:`<span class="w-act__note">当前不可退款${rq?.reason?` · ${esc(rq.reason)}`:''}</span>`;
    }
    const ps=(x.participants||[]).map(p=>{
      const refunded=p.status==='refunded';const prq=p.refundQuote;
      const refundBtn=(!refunded&&prq?.eligible&&['none','rejected'].includes(p.refund_status||'none'))?`<button class="w-act w-act--sm" type="button" onclick="requestParticipantRefund(${x.id},${p.id},${prq.cashRefundAmount||0},${prq.cashRefundPercent||0},'${esc(p.name)}')">退出退款</button>`:`<div class="w-pax-row__prog">${esc(p.refundProgress?.label||'')}${p.participant_refund_cash?` · 已退 ${money(p.participant_refund_cash)}`:''}</div>`;
      return `<div class="w-pax-row${refunded?' is-off':''}">
        <span class="w-pax-row__ic">${WI.pax}</span>
        <div class="w-pax-row__main">
          <div class="w-pax-row__nm"><b>${esc(p.name)}</b>${refunded?badge('已退出','off'):''}</div>
          <div class="w-pax-row__meta">${esc(p.phone||'')}${refunded?'':` · 资料${p.form_status==='complete'?'完整':'待补'} · 保险 ${insSt(p.insurance_status||'pending')}`}</div>
          ${refunded?'':`<div class="w-pax-row__ops">
            <button class="w-act w-act--sm" type="button" onclick="editParticipant(${x.id},${p.id})">补资料</button>
            <button class="w-act w-act--sm" type="button" onclick="replaceParticipant(${x.id},${p.id})">转名额</button>
            ${refundBtn}</div>`}
        </div></div>`}).join('');
    return `<div class="w-ord">
      <div class="w-ord__hd"><b>${esc(x.activity_title)}</b>${badge(ordSt(x.status),stTone(x.status))}</div>
      <div class="w-ord__meta">
        <span>${esc(x.occurrence_label||x.start_at||'团期待定')}</span><span>${x.participantCount||1} 人</span>
      </div>
      <div class="w-ord__amt"><span>实际支付</span><b>${money(x.amount)}</b></div>
      ${ps?`<div class="w-ord__pax">${ps}</div>`:''}
      <div class="w-ord__refund">退款：${esc(refundStateText(x))}${x.request_refund_percent!=null?` · 现金退 ${Number(x.request_refund_percent)}% / ${money(x.requested_refund_cash||0)}`:''}</div>
      ${action?`<div class="w-ord__ops">${action}</div>`:''}
    </div>`
  }).join('')||'<div class="w-empty">'+WI.receipt+'<div>暂无活动订单</div><div style="margin-top:4px">报名成功后订单会出现在这里</div></div>';
  $('#gearOrders').innerHTML=(d.gearOrders||[]).map(x=>{
    const items=(x.items||[]).map(i=>`<div class="w-item"><span>${esc(i.product_name)}</span><b>×${i.quantity}</b></div>`).join('');
    let action='';
    if(x.status!=='refunded') action=`<button class="w-act" type="button" onclick='requestGearAfterSales(${JSON.stringify(x).replace(/'/g,"&#39;")})'>申请售后</button>`;
    const cases=(x.afterSalesCases||[]).map(a=>`<div class="w-case">
      <div class="w-case__hd">${badge(asType(a.case_type),'info')}${badge(asSt(a.status),stTone(a.status))}</div>
      ${a.return_tracking_no?`<div class="w-case__meta">退货物流 ${esc(a.return_tracking_no)}</div>`:''}
      ${a.exchange_tracking_no?`<div class="w-case__meta">换货物流 ${esc(a.exchange_tracking_no)}</div>`:''}
      ${a.status==='awaiting_return'?`<button class="w-act w-act--sm" type="button" onclick="submitReturn('${a.id}')">填写退货物流</button>`:''}
    </div>`).join('');
    return `<div class="w-ord">
      <div class="w-ord__hd"><b>装备订单 #${x.id}</b>${badge(ordSt(x.status),stTone(x.status))}</div>
      <div class="w-ord__items">${items||'<div class="w-item"><span>商品</span></div>'}</div>
      <div class="w-ord__amt"><span>实付</span><b>${money(x.total)}</b></div>
      <div class="w-ord__meta"><span>${esc(x.carrier||'待发货')}${x.tracking_no?' '+esc(x.tracking_no):''}</span></div>
      <div class="w-ord__refund">退款：${esc(refundStateText(x))}</div>
      ${cases}${action?`<div class="w-ord__ops">${action}</div>`:''}
    </div>`
  }).join('')||'<div class="w-empty">'+WI.bag+'<div>暂无装备订单</div><div style="margin-top:4px">在装备商城下单后订单会出现在这里</div></div>';
  /* 待支付单独立成一块：它还没落 registrations / gear_orders，所以不会出现在上面两个列表里。
     以前这块是空的，用户关掉支付面板后这笔钱单就没有任何出口。 */
  const pbox=$('#pendingOrders');
  if(pbox){
    const pends=d.pendingOrders||[];
    pbox.innerHTML=pends.map(x=>`<div class="w-ord">
      <div class="w-ord__hd"><b>${x.orderKind==='gear'?`装备订单 #${esc(x.orderId)}`:esc(x.subject||'活动报名')}</b>${badge('待支付','wait')}</div>
      <div class="w-ord__amt"><span>待支付</span><b>${money(x.cashAmount)}</b></div>
      ${x.paymentStatus==='failed'?`<div class="w-act__note">上一次支付未完成，可继续支付或取消。</div>`:''}
      <div class="w-ord__ops"><button class="w-act" type="button" onclick="resumeCheckout('${esc(x.checkoutId)}','${esc(x.orderKind==='gear'?'gear':'activity')}')">继续支付</button><button class="w-act w-act--danger" type="button" onclick="cancelPendingCheckout('${esc(x.checkoutId)}')">取消订单</button></div>
    </div>`).join('')||'<div class="w-empty">'+WI.receipt+'<div>没有待支付订单</div><div style="margin-top:4px">未完成付款的订单会出现在这里</div></div>';
  }
}
async function editParticipant(regId,pid){
  const d=await api(`/api/public/registrations/${regId}/participants`),p=(d.participants||[]).find(x=>x.id===pid);if(!p)return;
  const v=await showForm({title:'编辑报名资料',submitText:'保存',fields:[{name:'idType',label:'证件类型',value:p.id_type||'身份证'},{name:'idNumber',label:'证件号码',value:p.id_number||''},{name:'ec',label:'紧急联系人',value:p.emergency_contact_name||''},{name:'ep',label:'紧急联系人电话',value:p.emergency_contact_phone||''}]});if(!v)return;
  try{await api(`/api/public/registrations/${regId}/participants/${pid}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({idType:v.idType,idNumber:v.idNumber,emergencyContactName:v.ec,emergencyContactPhone:v.ep})});toast('报名资料已更新');loadOrders()}catch(e){showAlert({title:'保存失败',message:e.message})}
}
async function replaceParticipant(regId,pid){
  const v=await showForm({title:'更换参加人',desc:'新参加人的保险信息需重新处理。',submitText:'确认更换',fields:[{name:'name',label:'新参加人姓名',required:true},{name:'phone',label:'新参加人手机号',required:true}]});if(!v)return;
  try{await api(`/api/public/registrations/${regId}/participants/${pid}/replace`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:v.name,phone:v.phone,relationToPayer:'同行人',reason:'用户自助转名额'})});toast('参加人已更换；保险信息需重新处理');loadOrders()}catch(e){showAlert({title:'更换失败',message:e.message})}
}
async function requestParticipantRefund(regId,pid,amount,pct,name){
  if(!(await showConfirm({title:'确认退出参加',message:`参加人 ${name} 退出后，预计现金退款 ${money(amount)}（${Number(pct)}%）。该参加人分摊的活动积分/装备积分会在退款成功后返还；订单级福利券只有全部参加人都退出时才恢复。`,confirmText:'确认提交',danger:true})))return;
  const v=await showForm({title:'填写退出原因',submitText:'提交',fields:[{name:'reason',label:'退出原因',type:'textarea',value:'临时有事无法参加',required:true}]});if(!v)return;
  try{let r=await api(`/api/public/registrations/${regId}/participants/${pid}/refund-request`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason:v.reason})});await showAlert({title:'申请已提交',message:`${name} 预计现金退款 ${money(r.cashAmount)}\n活动积分返还 ${r.pointsToRestore?.club||0}\n装备积分返还 ${r.pointsToRestore?.gear||0}`});loadOrders()}catch(e){showAlert({title:'提交失败',message:e.message})}
}
async function requestActivityRefund(id,amount,pct){
  if(!(await showConfirm({title:'确认申请退款',message:`按当前活动规则，本次预计现金退款 ${money(amount)}（${Number(pct)}%）。退款成功后，本单使用的积分/福利券按规则恢复。`,confirmText:'确认提交',danger:true})))return;
  const v=await showForm({title:'填写退款原因',submitText:'提交',fields:[{name:'reason',label:'退款原因',type:'textarea',value:'临时有事无法参加',required:true}]});if(!v)return;
  try{let r=await api(`/api/public/registrations/${id}/refund-request`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason:v.reason})});await showAlert({title:'退款申请已提交',message:`预计现金退款 ${money(r.cashAmount)}\n退款比例 ${Number(r.refundPercent||0)}%${Number(r.retainedCashAmount||0)>0?`\n取消费 ${money(r.retainedCashAmount)}`:''}`});loadOrders()}catch(e){showAlert({title:'提交失败',message:e.message})}
}
async function requestGearAfterSales(order){
  const items=order.items||[];
  const v=await showForm({title:'申请装备售后',desc:'售后由 ClubOS 总平台处理。',submitText:'提交售后',fields:[{name:'type',label:'售后类型',type:'select',value:'return_refund',options:[{value:'refund_only',label:'仅退款'},{value:'return_refund',label:'退货退款'},{value:'exchange',label:'换货'}]},{name:'itemId',label:'商品',type:'select',value:String(items[0]?.id||''),options:items.map(i=>({value:String(i.id),label:`${i.product_name} × ${i.quantity}`}))},{name:'qty',label:'售后数量',type:'number',value:items[0]?.quantity||1,min:1,required:true},{name:'reason',label:'售后原因',type:'textarea',value:'尺码或商品问题',required:true}]});if(!v)return;
  const itemId=Number(v.itemId),oi=items.find(i=>Number(i.id)===itemId);if(!oi){showAlert({title:'商品不正确',message:'请选择本订单内的商品。'});return}
  try{await api(`/api/public/orders/${order.id}/after-sales`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:USER,type:v.type,reason:v.reason,items:[{orderItemId:itemId,quantity:Number(v.qty||1)}]})});toast('售后申请已提交，由 ClubOS 总平台处理');loadOrders()}catch(e){showAlert({title:'提交失败',message:e.message})}
}
async function submitReturn(id){const v=await showForm({title:'填写退货物流',submitText:'提交',fields:[{name:'carrier',label:'退货承运商',value:'顺丰'},{name:'no',label:'退货物流单号',required:true}]});if(!v)return;try{await api(`/api/public/after-sales/${id}/return-shipment`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({carrier:v.carrier||'顺丰',trackingNo:v.no})});toast('退货物流已提交');loadOrders()}catch(e){showAlert({title:'提交失败',message:e.message})}}
async function startWeb(){
  if(clubosCookie('clubos_csrf')){
    try{const me=await api('/api/auth/me');if(me.role!=='member')throw new Error('请使用会员账号登录');USER=Number(me.userId);PAYER_NAME=me.name||'';PAYER_PHONE=me.phone||'';}catch(err){showAlert({title:'无法进入',message:err.message});setTimeout(()=>location.href='/login',1800);return}
  }
  try{const club=await api('/api/public/clubs/'+CLUB);if(club?.name){document.querySelector('.web-top strong').textContent=club.name;document.title=club.name+' · 活动与装备';}}catch(e){}
  renderMeGrids();bindWebEvents();
  /* 首屏是品牌页不是列表页：先把 hero 与精选拉起来，活动列表等切到那个 tab 再加载。
     带 ?activity=<id> 进来（后台分享的直达链接）时，才把列表也拉起来，好让「返回活动」有落点。 */
  const wanted=new URLSearchParams(location.search).get('activity');
  await loadHome();await loadWallet();
  if(wanted){await loadActivities();await openAct(Number(wanted))}
}
startWeb();
