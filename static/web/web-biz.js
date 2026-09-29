/* ═══════════════════════════════════════════════════════════════════════════
   C 端改版覆盖层（web.js 之后加载，同名 window.X 赢 —— 项目「后加载覆盖优先」
   约定；函数声明在同一文件内赢，跨文件后加载的 window.X 赋值也赢，均不报错。）
   四块：① 首页满屏 hero（一屏一张大图 + 底部文案 + 描边按钮），下滑是活动瀑布流
        ② 活动页主题卡 peek 轮播（中间一张竖版大卡、左右露出相邻卡片边）+ 瀑布流
        ③ 业务介绍区（后台开关）
        ④ 我的页会员中心化（活动积分/装备积分/优惠券 + 三类订单 + 积分商城）
   ═══════════════════════════════════════════════════════════════════════════ */
'use strict';
(function(){
const $=s=>document.querySelector(s),api=window.api,esc=window.esc||(x=>String(x??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])));
const CLUB=window.CLUB||1,money=window.money||(v=>'¥'+(Number(v||0)%1?Number(v).toFixed(2):Number(v||0)));
/* 旧加载器引用必须在任何 window.X 覆盖赋值之前捕获 —— 覆盖后再取 window.loadMemberCenter
   拿到的就是本文件自己的新实现，递归套娃会 stack overflow。 */
const _oldMemberCenter=window.loadMemberCenter,_oldWallet=window.loadWallet;
const day=x=>window.wDay?wDay(x.event_date):'';

/* ── 瀑布流卡片（首页 / 活动页共用）：双列，封面 + 日期角标 + 标题 + 地点·价格。
   没封面走渐变兜底，不造灰假图块（顾客会把灰块当成「图挂了」）。 */
function feedCard(x){
  const cov=x.cover||'',d=day(x);
  return `<button class="w-fd" type="button" onclick="openAct(${Number(x.id)})" aria-label="${esc(x.title)} 详情">
    <div class="w-fd__media${cov?'':' is-fallback'}">${cov?`<img src="${esc(cov)}" alt="" loading="lazy">`:''}</div>
    ${d?`<span class="w-fd__date">${esc(d)}</span>`:''}
    <div class="w-fd__copy">
      <h3>${esc(x.title)}</h3>
      <div class="w-fd__meta"><span>${esc(x.location||'户外')}</span><b>${money(x.price)}<em>/人</em></b></div>
    </div>
  </button>`;
}

/* ── ① 首页满屏 hero ─────────────────────────────────────────────────────
   参考图（始祖鸟首页）：一张照片占满首屏，文案压在底部 —— 小字标签 / 大标题 /
   时间地点 / 描边方框按钮，四行止。高度由 CSS 的 .w-hero--full 控制（100svh 减
   底部导航），这里不写像素，免得和导航高度各算一套、两处对不上。
   不在 hero 上放价格/名额：首页要的是「想去看一眼」，不是「先比一次价」；
   价格留给下面的瀑布流卡片，那里才是选购的场景。
   标题用 h1 而不是 h2 —— 旧实现写的 <h2> 不匹配 CSS 的 .w-hero h1，
   30px 大标题从来没生效过（首页标题一直是被浏览器默认字号的 h2 顶着）。 */
function homeSlide(x,i){
  const cov=x.cover||'',sub=[day(x),x.location||''].filter(Boolean).join(' · ');
  return `<div class="w-hero__slide${cov?'':' is-fallback'}">
    ${cov?`<div class="w-hero__media" style="background-image:url('${esc(cov)}')"></div>`:'<div class="w-hero__media"></div>'}
    <div class="w-hero__scrim"></div>
    <div class="w-hero__copy">
      <div class="w-hero__eyebrow">${i===0?'本期主推':'即将出发'}</div>
      <h1>${esc(x.title)}</h1>
      ${sub?`<p class="w-hero__sub">${esc(sub)}</p>`:''}
      <button class="w-hero__cta" type="button" onclick="openAct(${Number(x.id)})">即刻探索</button>
    </div>
  </div>`;
}

/* ── ② 活动页主题卡：peek 轮播 ───────────────────────────────────────────
   参考图（松赞主题卡）：中间一张竖版大卡，左右各露出相邻卡片的边 —— 一眼看出
   「可以左右滑」，而不是「只有这一张」。文案压在卡片中下部**居中**，与首页 hero
   的左对齐刻意区分：两屏的层次要能一眼分开，否则滑过来像"又回到首页"。 */
function themeCard(x,i){
  const cov=x.cover||'',meta=[day(x),x.location||''].filter(Boolean).join(' · ');
  return `<button class="w-theme__card${cov?'':' is-fallback'}" type="button" onclick="openAct(${Number(x.id)})" aria-label="${esc(x.title)} 详情">
    ${cov?`<div class="w-theme__media" style="background-image:url('${esc(cov)}')"></div>`:'<div class="w-theme__media"></div>'}
    <div class="w-theme__scrim"></div>
    <div class="w-theme__copy">
      <div class="w-theme__eyebrow">${i===0?'本期主推':'精选线路'}</div>
      <h2>${esc(x.title)}</h2>
      ${meta?`<div class="w-theme__meta">${esc(meta)}</div>`:''}
      <div class="w-theme__price">${money(x.price)}<em> / 人</em></div>
    </div>
    <span class="w-theme__cue" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m6 9.8 6 5.4 6-5.4"/></svg></span>
  </button>`;
}

/* 轮播指示同步。bar / cnt 由调用方传入：首页与活动页的指示元素不在同一处，
   写死一个选择器必然有一边永远不动。步长按「第一张卡宽 + 卡间距」算，不能用
   box.clientWidth —— peek 轮播的相邻卡是露出来的，clientWidth 里含着它们的宽度，
   用它当步长页码会一直算错。滚动停下 60ms 再量，否则 iOS 惯性滚动期间每帧读一次
   scrollLeft，指示器会乱跳。 */
function bindCaro(box,bar,cnt){
  const n=box.children.length;if(!n)return;
  const step=()=>{
    const a=box.children[0],b=box.children[1];if(!a)return box.clientWidth||1;
    const gap=b?Math.max(0,b.getBoundingClientRect().left-a.getBoundingClientRect().right):0;
    return Math.max(1,a.getBoundingClientRect().width+gap);
  };
  const sync=()=>{
    const s=step(),i=Math.max(0,Math.min(n-1,Math.round(box.scrollLeft/s)));
    if(bar)bar.style.width=((i+1)/n*100)+'%';
    if(cnt)cnt.textContent=`${String(i+1).padStart(2,'0')} / ${String(n).padStart(2,'0')}`;
  };
  box.addEventListener('scroll',()=>{clearTimeout(box._wt);box._wt=setTimeout(sync,60)},{passive:true});
  sync();
}

/* ── ① 首页：满屏 hero + 全部活动瀑布流（最新上架倒序；id 自增 = 上架顺序，最稳）── */
window.loadHome=async function(){
  const hero=$('#homeHero'),feed=$('#homeFeed');
  if(hero&&!hero.children.length)hero.innerHTML='<div class="w-sk" style="padding:0;height:100%"><div class="w-sk__bar" style="height:100%;border-radius:0"></div></div>';
  if(feed&&!feed.children.length)feed.innerHTML='<div class="w-sk-row"></div><div class="w-sk-row"></div>';
  try{
    const acts=await api(`/api/public/clubs/${CLUB}/activities`);
    window.ACT_ALL=acts;
    const list=(acts||[]).slice().sort((a,b)=>Number(b.id||0)-Number(a.id||0));
    if(hero)hero.innerHTML=list.length
      ?'<div class="w-hero__track">'+list.slice(0,5).map(homeSlide).join('')+'</div>'
        +(list.length>1?'<div class="w-hero__meter"><i></i></div><div class="w-hero__count"></div>':'')
      :'<div class="w-hero__slide is-fallback"><div class="w-hero__media"></div><div class="w-hero__scrim"></div><div class="w-hero__copy"><div class="w-hero__eyebrow">远拓户外</div><h1>去户外，找到下一场。</h1><p class="w-hero__sub">俱乐部正在筹备新的线路，稍后再来看看。</p></div></div>';
    const t=hero&&hero.querySelector('.w-hero__track');
    if(t&&list.length>1)bindCaro(t,hero.querySelector('.w-hero__meter i'),hero.querySelector('.w-hero__count'));
    if(feed)feed.innerHTML=list.length?list.map(feedCard).join(''):'<div class="w-empty">俱乐部正在筹备新的活动，稍后再来看看。</div>';
  }catch(e){if(feed)feed.innerHTML='<div class="w-empty">活动加载失败，请稍后重试</div>'}
};

/* ── ② 活动页：主题卡 peek 轮播 + 全部活动瀑布流 ─────────────────────────
   只把有封面的活动做成主题卡：没封面时那张卡是一块深色渐变，放在 C 位等于告诉
   顾客「这家的活动连张图都没有」。宁可少两张卡，也不把兜底图摆到台面上。 */
window.loadActivities=async function(){
  const car=$('#actFeatured'),box=$('#publicActivities'),emp=$('#activityEmpty');
  if(car&&!car.children.length)car.innerHTML='<div class="w-sk"><div class="w-sk__bar" style="height:380px"></div></div>';
  if(box&&!box.children.length)box.innerHTML='<div class="w-sk-row"></div><div class="w-sk-row"></div>';
  try{
    const acts=await api(`/api/public/clubs/${CLUB}/activities`);
    window.ACT_ALL=acts;
    const list=(acts||[]).slice().sort((a,b)=>Number(b.id||0)-Number(a.id||0));
    if(car){
      /* 有封面的优先做主题卡；**一张都没封面时也要把板块摆出来**（走兜底视觉）。
         早先这里是「filter(x=>x.cover)，空了就 display:none」——线上两条活动都没配
         封面，于是活动页只剩瀑布流，老板打开一看：「这个板块没有改呀」。整块消失
         比兜底图糟得多：顾客还会以为这页坏了。
         有真图时仍只显示真图的 —— 一屏里真照片和色块混着，比全兜底更掉价。 */
      const withCover=list.filter(x=>x.cover);
      const f=(withCover.length?withCover:list).slice(0,5);
      car.classList.toggle('is-fallback',!withCover.length);
      car.style.display=f.length?'':'none';
      car.innerHTML=f.length
        ?'<div class="w-theme__track">'+f.map(themeCard).join('')+'</div>'
          +(f.length>1?'<div class="w-theme__meter"><i></i></div>':'')
        :'';
      const t=car.querySelector('.w-theme__track');
      if(t&&f.length>1)bindCaro(t,car.querySelector('.w-theme__meter i'));
    }
    if(box)box.innerHTML=list.length?list.map(feedCard).join(''):'';
    if(emp)emp.innerHTML=list.length?'':'<div class="w-empty">暂时没有可报名的活动<br>换个时间再来看看</div>';
  }catch(e){if(box)box.innerHTML=''}
};

/* ── ③ 业务介绍区：clubs.biz_section_json（后台开关 + 自定义内容）──────────
   注意取值对象：这一屏的容器就是 #wability 本身（<section id="wability"> 里只有
   一个 <div id="bizIntro">），没有 #wbiz 这层壳。早先按 #wbiz 取不到节点 →
   整个函数提前 return，业务区永远不渲染、开关点了也没反应。
   老板没配时连导航入口一起收起来：点进去看一块空白，比没有入口还糟。 */
function bizCard(x){
  return `<div class="w-biz__item">
    ${x.image?`<img src="${esc(x.image)}" alt="" loading="lazy" onerror="this.remove()">`:''}
    <div class="w-biz__copy"><b>${esc(x.title||'')}</b>${x.desc?`<span>${esc(x.desc)}</span>`:''}</div>
  </div>`;
}
window.loadWability=async function(){
  const sec=$('#wability'),box=$('#bizIntro');
  if(!sec||!box)return;
  const navBtn=document.querySelector('.web-nav button[data-wv="wability"]');
  let b=null;
  try{
    const c=await api(`/api/public/clubs/${CLUB}`);
    try{b=c.biz_section?JSON.parse(c.biz_section):null}catch(e){b=null}
  }catch(e){b=null}
  const on=!!(b&&b.enabled&&(b.items||[]).length);
  if(navBtn)navBtn.style.display=on?'':'none';
  box.innerHTML=on
    ?`<div class="w-biz__head"><h2>${esc(b.title||'业务介绍')}</h2>${b.intro?`<p>${esc(b.intro)}</p>`:''}</div>`
      +`<div class="w-biz__list">${b.items.map(bizCard).join('')}</div>`
    :'';
  /* 正停在这一屏且内容被关掉（后台刚关 / 直接落到这一屏）→ 退回活动页，
     不让顾客对着空白页。 */
  if(!on&&sec.style.display==='block')wv('wactivities');
};

/* ── ④ 我的页：会员中心化（参考 JPG：积分三格 + 宫格入口 + 订单/福利）────── */
window.loadMemberCenter=async function(){
  const A=$('#meAvatar');
  if(A&&!A.dataset.done)A.textContent=(window.USER_NAME||'户').slice(0,1);
  /* 积分/等级/订单全部走旧加载器（web.js 原生 loadMemberCenter/loadWallet 写的就是
     #clubPts/#gearPts/#memberLevel 这批 id，端点真实存在）。之前这里调的
     /api/public/users/me/summary 后端根本没有 —— 两个 404 就是它打出来的。 */
  try{await _oldMemberCenter?.()}catch(e){}
  try{await _oldWallet?.()}catch(e){}
  try{if(window.loadOrders)await loadOrders()}catch(e){}
};
window.loadMemberInfo=window.loadMemberCenter;
window.openMemberCard=window.openMemberCard||function(){wv('wme')};

/* ── 视图切换挂载：切到哪块就加载哪块（幂等，重复切不重复拉）──────────────── */
const _wv=window.wv;
window.wv=function(id,btn){
  const r=_wv?_wv(id,btn):true;
  try{
    if(id==='whome')loadHome();
    else if(id==='wactivities')loadActivities();
    else if(id==='wability')loadWability();
    else if(id==='wme')loadMemberCenter();
  }catch(e){}
  return r;
};
/* 首次进入也挂一次（wv 首调发生在 web.js 的 start 里，本文件后加载已接管）。
   loadWability 也在这里跑一次：导航入口的显示与否要在**页面加载时**就定下来，
   否则老板没配业务介绍时，「户外能力」入口会一直挂在那儿，点进去才发现是空的。 */
if(document.readyState!=='loading'){loadHome();loadWability()}
else document.addEventListener('DOMContentLoaded',()=>{loadHome();loadWability()});
})();
