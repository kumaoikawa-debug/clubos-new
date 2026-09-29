/* ═══════════════════════════════════════════════════════════════════════════
   C 端四块改版覆盖层（web.js 之后加载，同名 window.X 赢 —— 项目「后加载覆盖优先」
   约定；函数声明在同一文件内赢，跨文件后加载的 window.X 赋值也赢，均不报错。）
   四块：① 首页纯活动瀑布流 ② 活动页主推轮播+瀑布流 ③ 业务介绍区（后台开关）
        ④ 我的页会员中心化（活动积分/装备积分/优惠券 + 三类订单 + 积分商城）
   ═══════════════════════════════════════════════════════════════════════════ */
'use strict';
(function(){
const $=s=>document.querySelector(s),api=window.api,esc=window.esc||(x=>String(x??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])));
const CLUB=window.CLUB||1,money=window.money||(v=>'¥'+(Number(v||0)%1?Number(v).toFixed(2):Number(v||0)));
/* 旧加载器引用必须在任何 window.X 覆盖赋值之前捕获 —— 覆盖后再取 window.loadMemberCenter
   拿到的就是本文件自己的新实现，递归套娃会 stack overflow。 */
const _oldMemberCenter=window.loadMemberCenter,_oldWallet=window.loadWallet;
/* 瀑布流卡片：封面 + 日期徽章压图 + 标题 + 地点·名额·价格。没封面走渐变兜底，
   不造灰假图块（顾客会把灰块当成「图挂了」）。 */
function feedCard(x){
  const cov=x.cover||'',d=window.wDay?wDay(x.event_date):'';
  return `<button class="w-fd" type="button" onclick="openAct(${Number(x.id)})" aria-label="${esc(x.title)} 详情">
    <div class="w-fd__media${cov?'':' is-fallback'}">${cov?`<img src="${esc(cov)}" alt="" loading="lazy">`:''}</div>
    ${d?`<span class="w-fd__date">${esc(d)}</span>`:''}
    <div class="w-fd__copy">
      <h3>${esc(x.title)}</h3>
      <div class="w-fd__meta"><span>${esc(x.location||'户外')}</span><b>${money(x.price)}<em>/人</em></b></div>
    </div>
  </button>`;
}
/* 主推轮播：取有封面的前 5 场；底部细线指示器只在真有多于一场时画 ——
   只有一场时「01 / 01」+ 一根满格条是纯噪音，还暗示「后面还有」（其实没有）。 */
function heroSlide(x,i){
  const cov=x.cover||'';
  return `<div class="w-hero__slide">
    ${cov?`<div class="w-hero__media" style="background-image:url('${esc(cov)}')"></div>`:'<div class="w-hero__media"></div>'}
    <div class="w-hero__scrim"></div>
    <div class="w-hero__copy">
      <div class="w-hero__eyebrow">${i===0?'本期主推 · FEATURED':'UPCOMING · 招募中'}</div>
      <h2>${esc(x.title)}</h2>
      <div class="w-hero__facts"><span>${esc(x.location||'户外')}</span><span>${money(x.price)} / 人</span></div>
      <button class="w-hero__cta" type="button" onclick="openAct(${Number(x.id)})">查看详情</button>
    </div></div>`;
}
function bindCaro(box){
  const n=box.children.length,bar=box.parentElement.querySelector('.w-caro__meter i'),cnt=box.parentElement.querySelector('.w-caro__count');
  const sync=()=>{const i=Math.max(0,Math.min(n-1,Math.round(box.scrollLeft/Math.max(1,box.clientWidth))));
    if(bar)bar.style.width=((i+1)/n*100)+'%';if(cnt)cnt.textContent=`${String(i+1).padStart(2,'0')} / ${String(n).padStart(2,'0')}`};
  box.addEventListener('scroll',()=>{clearTimeout(box._wt);box._wt=setTimeout(sync,60)},{passive:true});sync();
}
/* ── ① 首页：hero + 全部活动瀑布流（最新上架倒序；id 自增 = 上架顺序，最稳）── */
window.loadHome=async function(){
  const hero=$('#homeHero'),feed=$('#homeFeed');
  if(hero&&!hero.children.length)hero.innerHTML='<div class="w-sk"><div class="w-sk__bar" style="height:min(58vh,440px);border-radius:0"></div></div>';
  if(feed&&!feed.children.length)feed.innerHTML='<div class="w-sk-row"></div><div class="w-sk-row"></div>';
  try{
    const acts=await api(`/api/public/clubs/${CLUB}/activities`);
    window.ACT_ALL=acts;
    const list=(acts||[]).slice().sort((a,b)=>Number(b.id||0)-Number(a.id||0));
    if(hero)hero.innerHTML=list.length
      ?'<div class="w-hero__track">'+list.slice(0,5).map((x,i)=>heroSlide(x,i)).join('')+'</div>'
        +(list.length>1?'<div class="w-caro__meter"><i></i></div><div class="w-caro__count"></div>':'')
      :'<div class="w-hero__slide is-fallback"><div class="w-hero__media"></div><div class="w-hero__scrim"></div><div class="w-hero__copy"><div class="w-hero__eyebrow">远拓户外</div><h2>去户外，找到下一场。</h2><div class="w-hero__sub">俱乐部正在筹备新的线路，稍后再来看看。</div></div></div>';
    if(hero&&hero.querySelector('.w-hero__track'))bindCaro(hero.querySelector('.w-hero__track'));
    if(feed)feed.innerHTML=list.length?list.map(feedCard).join(''):'<div class="w-empty">俱乐部正在筹备新的活动，稍后再来看看。</div>';
    /* 旧首页的 #homeGear / #homeFeatured 节点已删；旧 loadHome 对它们都有 if 守卫，
       这里不再引用。旧函数在同文件是函数声明赢，但 loadHome 由 wv() 在运行时按
       window.loadHome 解析 —— 本文件后加载，赋值赢。 */
  }catch(e){if(feed)feed.innerHTML='<div class="w-empty">活动加载失败，请稍后重试</div>'}
};
/* ── ② 活动页：主推横滑轮播 + 全部活动瀑布流 ─────────────────────────────── */
window.loadActivities=async function(){
  const car=$('#actFeatured'),box=$('#publicActivities'),emp=$('#activityEmpty');
  if(car&&!car.children.length)car.innerHTML='<div class="w-sk"><div class="w-sk__bar" style="height:200px"></div></div>';
  if(box&&!box.children.length)box.innerHTML='<div class="w-sk-row"></div><div class="w-sk-row"></div>';
  try{
    const acts=await api(`/api/public/clubs/${CLUB}/activities`);
    window.ACT_ALL=acts;
    const list=(acts||[]).slice().sort((a,b)=>Number(b.id||0)-Number(a.id||0));
    if(car){const f=list.filter(x=>x.cover).slice(0,5);
      car.innerHTML=f.length
        ?'<div class="w-caro__track">'+f.map((x,i)=>heroSlide(x,i)).join('')+'</div>'+(f.length>1?'<div class="w-caro__meter"><i></i></div><div class="w-caro__count"></div>':'')
        :'';
      car.style.display=f.length?'':'none';
      const t=car.querySelector('.w-caro__track');if(t&&f.length>1)bindCaro(t);
    }
    if(box)box.innerHTML=list.length?list.map(feedCard).join(''):'';
    if(emp)emp.innerHTML=list.length?'':'<div class="w-empty">暂时没有可报名的活动<br>换个时间再来看看</div>';
  }catch(e){if(box)box.innerHTML=''}
};
/* ── ③ 业务介绍区：clubs.biz_section_json（后台开关 + 自定义内容）──────────
   注意取值对象：这一屏的容器就是 #wability 本身（<section id="wability"> 里只有
   一个 <div id="bizIntro">），没有 #wbiz 这层壳。早先按 #wbiz 取不到节点 →
   整个函数提前 return，业务区永远不渲染、开关点了也没反应。
   老板没配时连导航入口一起收起来：点进去看一块空白，比如口还在更糟。 */
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
const ORD_MAP=window.ORD_ST||{};
window.loadMemberCenter=async function(){
  const A=$('#meAvatar'),N=$('#meName'),T=$('#meTier'),L=$('#memberLevel');
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
