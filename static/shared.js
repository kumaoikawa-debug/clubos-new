const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
function clubosCookie(name){return document.cookie.split('; ').find(x=>x.startsWith(name+'='))?.split('=').slice(1).join('=')||''}
/* ===== 写操作防重复提交（在 api() 层统一兜底，覆盖全部调用点，无需改调用方）=====
   - 在途去重：同一写请求（方法+URL+body）在途时复用同一 Promise，不会向服务端发第二次。
   - 成功重放：成功后 WRITE_REPLAY_MS 内再次发起相同请求，直接返回上次结果 → 拦「快速连击」。
   - 失败不缓存：请求失败后允许立即重试（不会把失败结果重放给用户）。
   - 文件上传（FormData）不参与去重：其 body 无法稳定序列化，去重可能把 A 文件的结果串给 B 文件。
   注意：唯一键不含 headers；如需绕过（同一请求确实要连着发两次）可在 opt 上传 allowRepeat:true。 */
const WRITE_METHODS=['POST','PUT','PATCH','DELETE'];
const WRITE_REPLAY_MS=700;
const _writeInflight=new Map();   // key -> Promise
const _writeRecent=new Map();     // key -> {at,value}
function _writeKey(url,opt){
  const m=String(opt.method||'GET').toUpperCase();
  if(!WRITE_METHODS.includes(m)||opt.allowRepeat)return null;
  const b=opt.body;
  if(b instanceof FormData)return null;
  let bs='';
  if(b!=null){if(typeof b==='string')bs=b;else{try{bs=JSON.stringify(b)}catch{return null}}}
  return m+' '+url+' '+bs;
}
/* ===== 写操作按钮可见反馈（busy / disabled）=====
   api() 已能保证"不重复提交"，但用户点了写按钮仍看不出是否已响应。这里把「点击来源」与
   「真正发出的写请求」关联起来：只有当写请求确实发出时，才给该控件加 .is-busy + disabled，
   并记下原 disabled（业务上本就禁用的情况），请求结束（成功或失败）后原样恢复。
   因为只在写请求发出时才附加，纯读操作（打开详情、切 tab）点下去不会闪一下 disabled。
   覆盖全部 onclick 调用点，无需改调用方。 */
let _clickTrigger=null,_clickTriggerAt=0;
document.addEventListener('click',e=>{
  const t=e.target&&e.target.closest?e.target.closest('button,.btn,[onclick]'):null;
  if(!t||t.disabled){_clickTrigger=null;return}
  _clickTrigger=t;_clickTriggerAt=Date.now();
},true);   // 捕获阶段：先于元素自身的 onclick 记录点击来源
function _claimWriteTrigger(){
  const el=_clickTrigger;_clickTrigger=null;
  if(!el)return null;
  if(Date.now()-_clickTriggerAt>8000)return null;   // 旧点击（如填表单填太久）不再认领
  return el;
}
function uxBusyOn(el){
  if(!el||!el.isConnected)return null;
  const st={el,disabled:el.disabled};
  el.classList.add('is-busy');el.setAttribute('aria-busy','true');
  if('disabled' in el)el.disabled=true;
  return st;
}
function uxBusyOff(st){
  if(!st)return;const el=st.el;
  el.classList.remove('is-busy');el.removeAttribute('aria-busy');
  if(!el.isConnected)return;                       // 已从 DOM 移除（如成功关弹窗）则不动
  // 只回滚「由我们禁用」的情况。若按钮本来就 disabled（业务按自己的条件禁用的，比如余额不足时
  // 置灰的下单按钮），它何时恢复由业务代码决定——我们一律不碰，避免把业务刚恢复的按钮又按回禁用。
  if('disabled' in el&&!st.disabled)el.disabled=false;
}
/* ===== 写请求活跃计数：让「提交/确认」类弹窗把反馈保持到请求落定 =====
   提交类弹窗（shared.js 的 showForm/showConfirm、ux/clubos-ux.js 的 uxForm/uxConfirm）过去在
   被点击的同一帧就关掉弹窗，用户看不到任何"已提交"反馈；而且弹窗一关，紧随其后的写请求就再没有
   可以挂 busy 的控件。现在改为：点提交/确认后保留弹窗、把该按钮置 busy 并禁用取消，等写请求
   全部落定（或超时 cap）再撤。 */
let _writesPending=0;const _idleWaiters=[];
function _writeBegin(){_writesPending++}
function _writeEnd(){
  _writesPending=Math.max(0,_writesPending-1);
  if(!_writesPending)_idleWaiters.splice(0).forEach(f=>f());
}
function _whenWritesIdle({grace=150,cap=8000}={}){
  return new Promise(res=>{
    let settled=false;const finish=()=>{if(settled)return;settled=true;res()};
    const capT=setTimeout(finish,cap);const t0=Date.now();
    const check=()=>{
      if(Date.now()-t0<grace){setTimeout(check,Math.max(grace-(Date.now()-t0),20));return}  // 给调用方发起写请求留出时间
      if(!_writesPending){clearTimeout(capT);return finish()}
      _idleWaiters.push(()=>{clearTimeout(capT);finish()});
    };
    setTimeout(check,0);
  });
}
/* 保留弹窗直到写请求全部落定。after 用于「延迟拆除」的弹窗（如 uxForm 需要先恢复 body 滚动、
   归还焦点），默认直接 remove。取消按钮的类名各弹窗实现不统一（data-cancel / .ux-cancel / .x），
   这里一起禁用，避免请求在途时被关掉。 */
function _holdDialogUntilIdle(ov,btn,after){
  uxBusyOn(btn);
  for(const c of ov.querySelectorAll('[data-cancel],.ux-cancel,.x'))c.disabled=true;
  _whenWritesIdle().then(()=>{if(!ov.isConnected)return;(after||(()=>ov.remove()))()});
}
/* ===== 流程级锁：一个业务流程里连着发多条写请求时用 =====
   api() 的去重只保证「单条请求」不重复；报名与装备下单是「建单 → 支付 → 查单 → 收据 → 刷新」
   串起来的多请求流程。用户连点时，即便每条请求都被去重，流程本身仍会整跑两遍：弹出两个收款码、
   两张收据、跳两次页面。uxFlow 兜住这一段，调用方不必再自建 boolean 锁：
     · 同名流程在执行期间再次触发 → 立刻返回 null，不进入 fn；
     · 复用写操作那套按钮反馈（.is-busy + disabled），从流程开始一直保持到流程结束，
       而不是只在其中某一条请求在途时闪一下；
     · 结束时按「只回滚自己禁用的」规则还原，不夺业务代码对按钮的控制权。
   注意：它不计入 _writesPending —— 计数是给「弹窗保持到请求落定」用的，若把整个流程（含最长
   90 秒的收款码轮询）算成未落定，会把上层弹窗拖到 8 秒上限才关。 */
const _flowLocks=new Set();
function uxFlow(name,fn){
  if(_flowLocks.has(name))return Promise.resolve(null);
  _flowLocks.add(name);
  const busy=uxBusyOn(_claimWriteTrigger());
  return (async()=>{try{return await fn()}finally{_flowLocks.delete(name);uxBusyOff(busy)}})();
}
async function api(url,opt={}){
  const key=_writeKey(url,opt);
  if(key){
    const inflight=_writeInflight.get(key);
    if(inflight)return inflight;                                   // 在途 → 复用，不再发第二次
    const recent=_writeRecent.get(key);
    if(recent&&Date.now()-recent.at<WRITE_REPLAY_MS)return recent.value;  // 刚成功过 → 拦连击
  }
  const isWrite=WRITE_METHODS.includes(String(opt.method||'GET').toUpperCase());
  const busy=isWrite?uxBusyOn(_claimWriteTrigger()):null;
  const run=(async()=>{
    if(isWrite)_writeBegin();
    try{
      opt.headers=new Headers(opt.headers||{});
      if(!['GET','HEAD'].includes((opt.method||'GET').toUpperCase())){
        const csrf=clubosCookie('clubos_csrf');if(csrf)opt.headers.set('X-ClubOS-CSRF',decodeURIComponent(csrf));
      }
      const r=await fetch(url,{credentials:'same-origin',...opt});
      let d;try{d=await r.json()}catch{d={}}
      if(r.status===401 && location.pathname!='/login'){location.href='/login';throw new Error('请先登录')}
      if(!r.ok)throw new Error(d.detail||'请求失败');return d
    }finally{if(isWrite)_writeEnd()}
  })();
  if(busy)run.then(()=>uxBusyOff(busy),()=>uxBusyOff(busy));
  if(key){
    _writeInflight.set(key,run);
    run.then(v=>{_writeRecent.set(key,{at:Date.now(),value:v})},()=>{})   // 只缓存成功结果
       .then(()=>{if(_writeInflight.get(key)===run)_writeInflight.delete(key)});
  }
  return run;
}
function money(n){return '¥'+Number(n||0).toLocaleString('zh-CN',{maximumFractionDigits:2})}
function dateText(s){return s||'待定'}
function navInit(){$$('.nav button[data-view]').forEach(b=>b.onclick=()=>{$$('.nav button').forEach(x=>x.classList.remove('active'));b.classList.add('active');$$('.view').forEach(v=>v.classList.remove('active'));$('#'+b.dataset.view)?.classList.add('active');if(window.onView)window.onView(b.dataset.view)});}
function modal(id,on=true){$('#'+id)?.classList.toggle('show',on)}
function toast(msg){let el=document.createElement('div');el.textContent=msg;el.className='toast';document.body.appendChild(el);setTimeout(()=>el.remove(),2400)}
function esc(v=''){return String(v).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function mediaMap(master){let m={};for(const x of master?.media||[]){if(typeof x==='string'){if(x)m[x]=m[x]||{ref:x};continue}if(x?.ref)m[x.ref]=x}return m}
function resolvedRefs(refs,map){return (refs||[]).filter(r=>r&&map[r]&&map[r].url)}
function mediaHtml(ref,map,cls=''){const x=map[ref];if(!x?.url)return `<div class="editorial-media missing ${cls}"><span>${esc(ref||'image')}</span></div>`;return `<figure class="editorial-media ${cls}"><img src="${esc(x.url)}" alt="" loading="lazy"></figure>`}
function renderPromo(detail,master={},opts={}){
  const mm=mediaMap(master);let h='<article class="editorial">';
  // 头图兜底：block 自己没写 ref（或 ref 解析不到 url）时，用媒体清单里第一张真实存在的照片。
  // 头图必须是照片打底，而不是一块纯色——live 模式下模型常常只给 hero 文案、不给 mediaRefs。
  const fallbackPhoto=Object.keys(mm).find(r=>mm[r]&&mm[r].url)||'';
  for(const b of detail?.blocks||[]){const refs=b.mediaRefs||[];
    if(b.type==='hero'){
      const heroRef=resolvedRefs(refs,mm)[0]||fallbackPhoto;
      const url=heroRef&&mm[heroRef]?mm[heroRef].url:'';
      const bg=url?` style="background-image:linear-gradient(180deg,rgba(7,17,14,.12),rgba(7,17,14,.74)),url('${esc(url)}')"`:'';
      h+=`<section class="ed-hero${url?' has-photo':''}"${bg}><div class="ed-hero-copy"><div class="ed-kicker">${esc(b.kicker||master.location||'OUTDOOR EXPERIENCE')}</div><h1>${esc(b.headline||master.title||'活动')}</h1><p>${esc(b.subtitle||'')}</p></div></section>`;
    }else if(b.type==='lead')h+=`<section class="ed-lead"><p>${esc(b.text||b.body||'')}</p></section>`;
    else if(b.type==='statement')h+=`<section class="ed-statement"><span>${esc(b.text||'')}</span></section>`;
    else if(b.type==='facts')h+=`<section class="ed-facts">${(b.items||[]).map(x=>`<div><small>${esc(x.label||'')}</small><strong>${esc(x.value||x)}</strong></div>`).join('')}</section>`;
    else if(b.type==='narrative'){
      const ok=resolvedRefs(refs,mm);
      const media=ok.length?`<div class="ed-narrative-media">${ok.map(r=>mediaHtml(r,mm)).join('')}</div>`:'';
      h+=`<section class="ed-narrative ${ok.length?'has-media':''}"><div class="ed-copy">${b.eyebrow?`<div class="ed-kicker">${esc(b.eyebrow)}</div>`:''}<h2>${esc(b.headline||'')}</h2><p>${esc(b.body||b.text||'')}</p></div>${media}</section>`;
    }else if(b.type==='media'){
      // 解析不到 url 的 ref 直接不排版：宁可少一张图，也不要满屏灰色占位块。
      const ok=resolvedRefs(refs,mm);
      if(ok.length||b.caption){
        const layout=b.layout==='mosaic'?'mosaic':ok.length===2?'pair':ok.length>=3?'grid':'single';
        h+=`<section class="ed-media ${layout}">${ok.map(r=>mediaHtml(r,mm)).join('')}${b.caption?`<p class="ed-caption">${esc(b.caption)}</p>`:''}</section>`;
      }
    }else if(b.type==='gallery'){
      const ok=resolvedRefs(refs,mm);
      if(ok.length)h+=`<section class="ed-gallery count-${Math.min(ok.length,4)}">${ok.map(r=>mediaHtml(r,mm)).join('')}${b.caption?`<p class="ed-caption">${esc(b.caption)}</p>`:''}</section>`;
    }
    else if(b.type==='timeline')h+=`<section class="ed-section ed-timeline"><div class="ed-section-head"><div class="ed-kicker">SCHEDULE</div><h2>${esc(b.title||'行程')}</h2></div><div class="timeline-list">${(b.items||[]).map(x=>`<div class="timeline-item"><time>${esc(x.time||'')}</time><p>${esc(x.text||x.content||'')}</p></div>`).join('')}</div></section>`;
    else if(b.type==='info')h+=`<section class="ed-section ed-info"><div class="ed-section-head"><div class="ed-kicker">GOOD TO KNOW</div><h2>${esc(b.title||'出发前知道')}</h2></div><div class="info-chips">${(b.items||[]).map(x=>`<div>${esc(x)}</div>`).join('')}</div></section>`;
    else if(b.type==='quote')h+=`<section class="ed-quote">“${esc(b.text||'')}”</section>`;
    else if(b.type==='divider')h+='<div class="ed-divider"></div>';
    else if(b.type==='cta')h+=`<section class="ed-cta"><div><div class="ed-kicker">READY TO GO</div><h2>${esc(b.headline||'立即报名')}</h2><p>${esc(b.text||'')}</p></div>${opts.hideButton?'':`<button class="btn light">选择团期并报名</button>`}</section>`;
  }
  return h+'</article>';
}
function gearRow(p,opts){
  // 价格走第二行：窄栏（C 端 560px 容器）下右挂价格会把商品名挤成两三行
  const base=Number(p.price||0),mp=Number(p.memberPrice||0);
  const hasMember=mp>0&&mp<base;
  const fmt=n=>'¥'+Number(n||0).toFixed(2).replace(/\.00$/,'');
  // 会员价直接替代原价显示、原价划线保留：让"加入会员更便宜"一眼可见
  const priceHtml=hasMember
    ? '<i class="gear-price">'+esc(fmt(mp))+'</i><span class="gear-was">'+esc(fmt(base))+'</span>'
    : '<i class="gear-price">'+esc(fmt(base))+'</i>';
  const memberNote=hasMember
    ? ' · '+esc(p.memberTierName||'会员')+'价 省 '+esc(fmt(p.memberSavings||0))
    : '';
  const inner='<span class="gear-emoji">'+esc(p.emoji||'🧰')+'</span>'
    +'<span class="gear-main"><b>'+esc(p.name)+'</b>'
    +'<small>'+priceHtml+esc(p.reason||'')+(p.inStock?'':' · 暂时缺货')+memberNote+'</small></span>'
    +'<span class="gear-go">›</span>';
  // C 端 = 下单入口；俱乐部后台点进去是「本俱乐部商城里的这件商品」——
  // 推荐只能看不能买等于没落地，配上会员价才有意义。
  return opts.canBuy
    ? '<button type="button" class="gear-row buyable" onclick="buy('+Number(p.id||0)+')" title="下单购买">'+inner+'</button>'
    : '<button type="button" class="gear-row buyable" onclick="openGearProduct('+Number(p.id||0)+')" title="在装备商城里查看这件商品">'+inner+'</button>';
}
/* 同一件装备会同时满足多项清单要求（「速干衣裤」与「防晒外套」都命中服装面料），
   整行重复出现会把清单拉长一倍，看起来像推荐错了。第二次出现收成一行只读引用，
   购买入口保留在首次出现处。 */
function gearRowDup(p){
  return '<div class="gear-row gear-row--dup" title="同一件装备，已在上方列出">'
    +'<span class="gear-emoji">'+esc(p.emoji||'🧰')+'</span>'
    +'<span class="gear-main"><b>'+esc(p.name)+'</b><small>同一件装备 · 已在上方列出</small></span></div>';
}
function renderPacking(master,opts){
  opts=opts||{};
  const list=master.checklist||[];
  const chips='<div class="info-chips">'+(list.map(x=>'<div>'+esc(x)+'</div>').join('')||'<div>出发前由俱乐部通知</div>')+'</div>';
  const g=opts.gear;
  if(!g||!g.available)return chips;                       // 商城没有在售装备：不编造推荐，保持原样
  const items=g.items||[],extras=g.extras||[];
  if(!items.length&&!extras.length)return chips;
  const o={canBuy:!!opts.canBuy&&typeof window.buy==='function',manage:!!opts.manage};
  const seen={};                                          // 已完整展示过的商品 id
  const slots=items.map(it=>{
    const ms=(it.matches||[]).map(p=>{
      const id=Number(p.id||0);
      if(id&&seen[id])return gearRowDup(p);
      if(id)seen[id]=1;
      return gearRow(p,o);
    }).join('');
    // 清单项本身认不出装备品类时（如"身份证"）不该说"商城没有"，那是两回事
    const body=ms||((it.tags||[]).length?'<div class="gear-none">商城暂无对应装备</div>':'');
    return '<div class="pack-slot"><div class="pack-need">'+esc(it.text)+'</div>'
      +'<div class="pack-gear">'+body+'</div></div>';
  }).join('');
  const cov=g.coverage||{};
  const miss=[];
  items.forEach(it=>{
    if((it.tags||[]).length&&!(it.matches||[]).length){
      const l=(it.tagLabels||[])[0];
      if(l&&miss.indexOf(l)<0)miss.push(l);
    }
  });
  const md=g.memberDiscount||null;
  let head='<div class="gear-head"><span class="eyebrow">按清单搭配</span><span class="gear-summary">清单 '
    +Number(cov.needs||0)+' 项 · 商城可配 '+Number(cov.matched||0)+' 项'
    +(md?' · <b>'+esc(md.tierName)+' '+esc(String(md.discountZhe))+' 折</b>':'')
    +'</span></div>';
  const missLine=miss.length
    ? '<div class="gear-missing">商城暂无对应装备：'+esc(miss.join('、'))+(o.manage?'（可在商城上架补全）':'')+'</div>'
    : '';
  // 「其他在售装备」是补充位：清单里已经出现过的商品不再重复列一次
  const extraRows=extras.filter(p=>!seen[Number(p.id||0)]).map(p=>gearRow(p,o)).join('');
  const extra=extraRows
    ? '<div class="gear-extras"><div class="gear-extras-title">本场活动其他在售装备</div><div class="pack-gear">'+extraRows+'</div></div>'
    : '';
  return head+'<div class="pack-plan">'+slots+'</div>'+missLine+extra;
}
/* 费用说明：把 fees 对象渲染成可读的键值行，而不是一整块 JSON 代码。数组走 chips。 */
function feeListHtml(fees){
  fees=fees||{};const keys=Object.keys(fees).filter(k=>fees[k]!=null&&fees[k]!=='');
  if(!keys.length)return '<p class="sub">费用以活动通知与最终确认为准</p>';
  return '<div class="fee-list">'+keys.map(k=>{const v=fees[k];
    if(Array.isArray(v))return '<div class="fee-row"><b>'+esc(k)+'</b><div class="info-chips">'+v.map(x=>'<div>'+esc(String(x))+'</div>').join('')+'</div></div>';
    if(typeof v==='object')return '<div class="fee-row"><b>'+esc(k)+'</b><p>'+esc(JSON.stringify(v))+'</p></div>';
    return '<div class="fee-row"><b>'+esc(k)+'</b><p>'+esc(String(v))+'</p></div>';
  }).join('')+'</div>';
}
/* detail.blocks 里已经排过行程时，结构化区不再重复渲染同一份 master.itinerary——
   此前「把一天安排得刚刚好」(promo timeline) 与「详细行程 ITINERARY」是同一份数据渲染两遍，
   同一页出现两次行程，是用户看到的"详情重复出现"。 */
function promoHasItinerary(detail){return ((detail||{}).blocks||[]).some(b=>b&&b.type==='timeline'&&(b.items||[]).length)}
function infoStackSkip(detail){return promoHasItinerary(detail)?['itinerary']:[]}
/* 详情页很长，给一条页内跳转，避免"不知道下面还有什么"。用 scrollIntoView 而不是 <a href="#…">，
   避免和可能存在的 hash 路由打架。 */
function jumpTo(id){const el=document.getElementById(id);if(el)el.scrollIntoView({behavior:'smooth',block:'start'})}
function detailNavHtml(items){
  if(!items||!items.length)return '';
  return '<div class="detail-nav">'+items.map(([id,label])=>`<button type="button" class="detail-nav__item" onclick="jumpTo('${id}')">${esc(label)}</button>`).join('')+'</div>';
}
function renderInfoStack(master,opts={}){
  const skip=opts.skip||[],has=k=>skip.indexOf(k)<0;
  let h='<div class="info-stack polished">';
  if(has('itinerary'))h+=`<details open><summary>详细行程 <span>ITINERARY</span></summary><div class="detail-list">${(master.itinerary||[]).map(x=>`<div><b>${esc(x.time||'')}</b><p>${esc(x.content||x.text||'')}</p></div>`).join('')||'<p class="sub">以最终活动通知为准</p>'}</div></details>`;
  if(has('fees'))h+=`<details${has('itinerary')?'':' open'}><summary>费用说明 <span>PRICE</span></summary>${feeListHtml(master.fees)}</details>`;
  if(has('packing'))h+=`<details open><summary>出行清单 <span>PACKING</span></summary>${renderPacking(master,opts)}</details>`;
  return h+'</div>';
}

/* 枚举值中文化：同一个 status 在整个后台要长一样。此前「报名管理」把 draft / published、
   平台端把 pro / active 这类原始枚举直接摆在页面上，中文界面里突然蹦出英文单词。
   未知值原样返回——宁可显示原始值，也不要编一个不存在的中文名。 */
const ENUM_CN={
  active:'已启用',inactive:'已停用',disabled:'已停用',pending:'待处理',rejected:'已拒绝',
  draft:'草稿',published:'已发布',archived:'已归档',cancelled:'已取消',canceled:'已取消',
  paid:'已付款',unpaid:'待付款',open:'开放中',closed:'已关闭',ready:'就绪',failed:'失败',
  processing:'处理中',completed:'已完成',shipped:'已发货',delivered:'已签收',refunded:'已退款',
  requested:'待处理',approved:'已通过',settled:'已结算',preparing:'准备中',arrived:'已到达',
  in_progress:'进行中',expired:'已过期'
};
function enumCn(v,fallback){
  const k=String(v==null?'':v).trim();
  if(!k)return fallback==null?'—':fallback;
  return ENUM_CN[k]||ENUM_CN[k.toLowerCase()]||fallback||k;
}

// Logout must revoke the session on the server, not just hide the current UI.
document.addEventListener('DOMContentLoaded',()=>{
  if(!clubosCookie('clubos_csrf')||location.pathname==='/login')return;
  const button=document.createElement('button');button.type='button';button.textContent='退出登录';
  /* 定位交给 CSS（.logout-fab）：C 端底部有一条固定 tab bar，按钮浮在右下角会把
     第 4 个 tab「订单」整块盖住、那个入口点不到。有 tab bar 时按钮上移，并给页面留出底部空间。 */
  button.className='btn ghost logout-fab';
  button.onclick=async()=>{try{await api('/api/auth/logout',{method:'POST'})}finally{location.href='/login'}};
  document.body.appendChild(button);
  if(document.querySelector('.web-nav'))document.body.classList.add('has-webbar');
});

/* ---- 骨架屏 skeleton（组件层 .skeleton 已定义在 ux/clubos-ux.css）---- */
function skelRows(n=4,h=14){let o='';for(let i=0;i<n;i++)o+=`<div class="skeleton" style="height:${h}px;margin:9px 0"></div>`;return o}
function skel(sel,n=4,h=14){const el=$(sel);if(!el)return;el.innerHTML=skelRows(n,h);clearTimeout(el.__skelT);
  el.__skelT=setTimeout(()=>{const c=[...el.children];
    if(c.length&&c.every(x=>x.classList&&x.classList.contains('skeleton')))
      el.innerHTML='<div class="empty">加载超时，请刷新重试</div>'},10000)}

/* ===== 原生弹窗替代层：结构化表单 / 确认弹窗 / 信息弹窗（替换 prompt/confirm/alert）===== */
function uxDialog({title='',desc='',body='',foot='',wide=false,onClose=null}={}){
  const ov=document.createElement('div');ov.className='ux-overlay';
  ov.innerHTML=`<div class="ux-dialog${wide?' wide':''}" role="dialog" aria-modal="true">
    <div class="ux-dialog-head"><div><h2>${esc(title)}</h2>${desc?`<p>${esc(desc)}</p>`:''}</div><button class="ux-x" type="button" aria-label="关闭">×</button></div>
    ${body?`<div class="ux-dialog-body">${body}</div>`:''}${foot?`<div class="ux-dialog-foot">${foot}</div>`:''}</div>`;
  const close=()=>{ov.remove();if(onClose)onClose()};
  ov.querySelector('.ux-x').onclick=close;
  ov.onclick=e=>{if(e.target===ov)close()};
  document.body.appendChild(ov);
  return ov;
}
function showConfirm({title='请确认',message='',confirmText='确认',cancelText='取消',danger=false}={}){
  return new Promise(res=>{
    let done=false;const fin=v=>{if(done)return;done=true;res(v)};
    const ov=uxDialog({title,desc:message,onClose:()=>fin(false),foot:`<button class="btn ghost" type="button" data-cancel>${esc(cancelText)}</button><button class="btn ${danger?'danger':''}" type="button" data-ok>${esc(confirmText)}</button>`});
    ov.querySelector('[data-cancel]').onclick=()=>{ov.remove();fin(false)};
    ov.querySelector('[data-ok]').onclick=()=>{_holdDialogUntilIdle(ov,ov.querySelector('[data-ok]'));fin(true)};
  });
}
function showAlert({title='提示',message='',okText='知道了',wide=false}={}){
  return new Promise(res=>{
    let done=false;const fin=()=>{if(done)return;done=true;res()};
    const ov=uxDialog({title,desc:message,wide,onClose:fin,foot:`<button class="btn" type="button" data-ok>${esc(okText)}</button>`});
    ov.querySelector('[data-ok]').onclick=()=>{ov.remove();fin()};
  });
}
function showForm({title='',desc='',fields=[],submitText='提交',validate=null,wide=false}={}){
  const body=fields.map(f=>{
    const id='uxf_'+f.name,val=esc(f.value??'');
    let ctrl;
    if(f.type==='textarea')ctrl=`<textarea id="${id}" name="${f.name}" placeholder="${esc(f.placeholder||'')}">${val}</textarea>`;
    else if(f.type==='select')ctrl=`<select id="${id}" name="${f.name}">${(f.options||[]).map(o=>`<option value="${esc(o.value)}" ${String(o.value)===String(f.value)?'selected':''}>${esc(o.label??o.value)}</option>`).join('')}</select>`;
    else ctrl=`<input id="${id}" name="${f.name}" type="${f.type||'text'}" value="${val}" placeholder="${esc(f.placeholder||'')}" ${f.required?'data-req=1':''} ${f.step!==undefined?`step="${f.step}"`:''} ${f.min!==undefined?`min="${f.min}"`:''}>`;
    return `<div class="ux-field"><label for="${id}">${esc(f.label)}${f.required?' <em>*</em>':''}</label>${ctrl}${f.help?`<small>${esc(f.help)}</small>`:''}</div>`;
  }).join('');
  return new Promise(resolve=>{
    let done=false;const fin=v=>{if(done)return;done=true;resolve(v)};
    const ov=uxDialog({title,desc,wide,body,onClose:()=>fin(null),foot:`<button class="btn ghost" type="button" data-cancel>取消</button><button class="btn" type="button" data-submit>${esc(submitText)}</button>`});
    const bodyEl=ov.querySelector('.ux-dialog-body')||ov.querySelector('.ux-dialog');
    ov.querySelector('[data-cancel]').onclick=()=>{ov.remove();fin(null)};
    ov.querySelector('[data-submit]').onclick=()=>{
      let ok=true;const vals={};
      for(const f of fields){
        const el=ov.querySelector('#uxf_'+f.name);if(!el){vals[f.name]=undefined;continue}
        let v=el.value;
        if(f.type==='number'){v=el.value===''?undefined:Number(el.value);
          if(f.required&&(v===undefined||isNaN(v))){el.classList.add('ux-invalid');ok=false;continue}
          if(v!==undefined&&!isNaN(v)&&f.min!==undefined&&v<f.min){el.classList.add('ux-invalid');ok=false;continue}}
        if(f.required&&(v===undefined||v==='')){el.classList.add('ux-invalid');ok=false}else el.classList.remove('ux-invalid');
        vals[f.name]=v;
      }
      if(!ok){let e=bodyEl.querySelector('.ux-inline-error');if(!e){e=document.createElement('div');e.className='ux-inline-error';e.textContent='请填写带 * 的必填项';bodyEl.insertBefore(e,bodyEl.firstChild)}return}
      if(validate&&!validate(vals,ov))return;
      _holdDialogUntilIdle(ov,ov.querySelector('[data-submit]'));fin(vals);
    };
  });
}
