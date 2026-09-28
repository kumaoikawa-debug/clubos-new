const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
/* 当前打开的是哪一端。**唯一的权威判据** —— ux/*.js 过去各自写
   `location.pathname.startsWith('/club')`，但后端 `/` 与 `/club` 服务的是同一份
   club/index.html（见 app.py 的 root()），从根路径打开时那些守卫全部判假：
   整个 workflows-club.js 不执行，quickAddOccurrence 等业务动作函数全是 undefined，
   用户点「＋ 添加团期」只得到一个 ReferenceError（页面毫无反应）。
   所以把「根路径 = 俱乐部端」这条事实收在这里，各处一律引用它。 */
function uxPage(){
  const p=location.pathname;
  if(p.startsWith('/platform'))return 'platform';
  if(p.startsWith('/web'))return 'web';
  if(p.startsWith('/leader'))return 'leader';
  if(p.startsWith('/club')||p==='/'||p==='')return 'club';
  return 'other';
}
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
/* 页面内 .modal（如 club 端 AI 发活动）也接同一套会话：Esc 关闭、焦点进入并圈闭、
   关闭后归还焦点、锁背景滚动。之前 modal() 只是 classList.toggle，键盘用户打开后
   焦点仍留在页面上、Esc 也关不掉。 */
const _uxModalSessions=new Map();
function modal(id,on=true){
  const el=$('#'+id);if(!el)return;
  const show=on!==false;
  el.classList.toggle('show',show);
  const live=_uxModalSessions.get(id);
  if(!show){if(live){live.release();_uxModalSessions.delete(id)}return}
  if(live)return;
  const head=el.querySelector('h2');
  if(head&&!head.id){head.id='uxm-title-'+id;el.setAttribute('aria-labelledby',head.id)}
  if(!el.hasAttribute('tabindex'))el.tabIndex=-1;
  _uxModalSessions.set(id,uxDialogSession(el,{onEscape:()=>modal(id,false),initialFocus:'textarea,input:not([type=file]),select'}));
}
function toast(msg){let el=document.createElement('div');el.textContent=msg;el.className='toast';document.body.appendChild(el);setTimeout(()=>el.remove(),2400)}
function esc(v=''){return String(v).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function mediaMap(master){let m={};for(const x of master?.media||[]){if(typeof x==='string'){if(x)m[x]=m[x]||{ref:x};continue}if(x?.ref)m[x.ref]=x}return m}
function resolvedRefs(refs,map){return (refs||[]).filter(r=>r&&map[r]&&map[r].url)}
function mediaHtml(ref,map,cls=''){const x=map[ref];if(!x?.url)return `<div class="editorial-media missing ${cls}"><span>${esc(ref||'image')}</span></div>`;return `<figure class="editorial-media ${cls}"><img src="${esc(x.url)}" alt="" loading="lazy"></figure>`}
/* 文案段落归一：真模型会把 body 写成数组或多句一段（\n 分隔）。过去整段 esc 进一个 <p>，
   编辑排版的多段节奏全部丢失（用户截图实锤「文字平铺没有吸引力」）。
   数组 → 逐段；字符串 → 按 \n 拆；空段丢弃；渲染不出任何段落就不输出。 */
function edParas(v){
  const arr=(Array.isArray(v)?v:String(v||'').split(/\n+/))
    .map(s=>String(s).trim()).filter(Boolean);
  return arr.map(s=>`<p>${esc(s)}</p>`).join('');
}
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
    }    else if(b.type==='lead')h+=`<section class="ed-lead">${edParas(b.text||b.body||'')}</section>`;
    else if(b.type==='statement')h+=`<section class="ed-statement"><span>${esc(b.text||'')}</span></section>`;
    else if(b.type==='facts')h+=`<section class="ed-facts">${(b.items||[]).map(x=>`<div><small>${esc(x.label||'')}</small><strong>${esc(x.value||x)}</strong></div>`).join('')}</section>`;
    else if(b.type==='narrative'){
      const ok=resolvedRefs(refs,mm);
      const media=ok.length?`<div class="ed-narrative-media">${ok.map(r=>mediaHtml(r,mm)).join('')}</div>`:'';
      /* 文案升级（2026-09-28 用户反馈「图片好看但文字没气势」）：
         body 支持多段（数组或 \n 分隔），pull 是独立金句行——编辑排版的节奏全靠这两样。 */
      const pull=b.pull?`<p class="ed-pull">${esc(b.pull)}</p>`:'';
      h+=`<section class="ed-narrative ${ok.length?'has-media':''}${pull?' has-pull':''}"><div class="ed-copy">${b.eyebrow?`<div class="ed-kicker">${esc(b.eyebrow)}</div>`:''}<h2>${esc(b.headline||'')}</h2>${edParas(b.body||b.text||'')}${pull}</div>${media}</section>`;
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
    else if(b.type==='timeline'){
      /* 模型输出的行程项形状不稳定（字符串 / {time,text} / {day,schedule:[...]}），
         过去只认 {time,text}，其他形状整段渲染成空行（用户截图实锤「行程区只有图片没有字」）。
         统一走 itineraryRows 归一；归一后为空就整段不渲染，宁缺勿空。 */
      const rows=itineraryRows(b.items||b.body||[]);
      if(rows.length)h+=`<section class="ed-section ed-timeline"><div class="ed-section-head"><div class="ed-kicker">SCHEDULE</div><h2>${esc(b.title||b.headline||'行程')}</h2></div><div class="timeline-list">${rows.map(x=>`<div class="timeline-item"><time>${esc(x.time)}</time><p>${esc(x.text)}</p></div>`).join('')}</div></section>`;
    }
    else if(b.type==='info')h+=`<section class="ed-section ed-info"><div class="ed-section-head"><div class="ed-kicker">GOOD TO KNOW</div><h2>${esc(b.title||'出发前知道')}</h2></div><div class="info-chips">${(b.items||[]).map(x=>`<div>${esc(x)}</div>`).join('')}</div></section>`;
    else if(b.type==='quote')h+=`<section class="ed-quote">“${esc(b.text||'')}”</section>`;
    else if(b.type==='divider')h+='<div class="ed-divider"></div>';
    // cta 块不再渲染（2026-09-28 用户反馈）：详情页中部出现「立即报名」大块很突兀，
    // 页面底部本就有常驻报名入口。AI 若仍输出 cta 块，直接跳过不画。
    else if(b.type==='cta'){/* skipped */}
  }
  return h+'</article>';
}
function gearRow(p,opts,sub){
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
  // 平替徽标放在最前面：顾客第一眼就要知道「这不是清单里那件东西」，
  // 理由文案（为什么拿它顶上）由引擎写在 p.reason 里，一起放进第二行。
  const badge=sub?'<span class="gear-badge">平替</span>':'';
  const inner=badge+'<span class="gear-emoji">'+esc(p.emoji||'🧰')+'</span>'
    +'<span class="gear-main"><b>'+esc(p.name)+'</b>'
    +'<small>'+priceHtml+esc(p.reason||'')+(p.inStock?'':' · 暂时缺货')+memberNote+'</small></span>'
    +'<span class="gear-go">›</span>';
  // C 端 = 下单入口；俱乐部后台点进去是「本俱乐部商城里的这件商品」——
  // 推荐只能看不能买等于没落地，配上会员价才有意义。
  return opts.canBuy
    ? '<button type="button" class="gear-row buyable'+(sub?' gear-row--sub':'')+'" onclick="buy('+Number(p.id||0)+')" title="下单购买">'+inner+'</button>'
    : '<button type="button" class="gear-row buyable'+(sub?' gear-row--sub':'')+'" onclick="openGearProduct('+Number(p.id||0)+')" title="在装备商城里查看这件商品">'+inner+'</button>';
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
  /* chips 不再是哑的纯文本：哪几项商城真能配到，就要在清单里直接标出来——
     顾客先扫一眼清单（✓ 的=能一键配齐），再往下看装备行，视线动线才是通的。 */
  const g=opts.gear;
  const okTexts=new Set(((g&&g.items)||[])
    .filter(it=>(it.matches||[]).length||(it.substitutes||[]).length)
    .map(it=>String(it.text||'').trim()));
  const chips='<div class="info-chips pack-chips">'+(list.map(x=>{
    const ok=okTexts.has(String(x).trim());
    return '<div'+(ok?' class="ok" title="商城有可搭配装备"':'')+'>'+esc(x)+'</div>';
  }).join('')||'<div>出发前由俱乐部通知</div>')+'</div>';
  if(!g||!g.available)return chips;                       // 商城没有在售装备：不编造推荐，保持原样
  const items=g.items||[],extras=g.extras||[];
  if(!items.length&&!extras.length)return chips;
  const o={canBuy:!!opts.canBuy&&typeof window.buy==='function',manage:!!opts.manage};
  /* 原则（2026-09-28 用户定）：推荐装备必须匹配商城，有货才显示、没货就不显示。
     「商城暂无对应装备」占位与缺货品类提示是给老板看的运营信息（manage），
     顾客看到的应当只有「清单 + 能买到的装备」——满屏"暂无"只会显得商城很空。 */
  const visible=items.filter(it=>(it.matches||[]).length||(it.substitutes||[]).length);
  const seen={};                                          // 已完整展示过的商品 id
  const slot=(it)=>{
    const ms=(it.matches||[]).map(p=>{
      const id=Number(p.id||0);
      if(id&&seen[id])return gearRowDup(p);
      if(id)seen[id]=1;
      return gearRow(p,o);
    }).join('');
    // 平替：清单项没有精确匹配时引擎给的「最接近的同类」。徽标与理由由后端生成，
    // 这里只负责渲染；同样计入 seen，避免它又在「其他在售装备」里出现一次。
    const subs=(it.substitutes||[]).map(p=>{
      const id=Number(p.id||0);
      if(id&&seen[id])return gearRowDup(p);
      if(id)seen[id]=1;
      return gearRow(p,o,true);
    }).join('');
    // 清单项本身认不出装备品类时（如"身份证"）不该说"商城没有"，那是两回事
    const body=ms||subs
      ||((it.tags||[]).length?'<div class="gear-none">商城暂无对应装备，可看看下方其他在售装备</div>':'');
    return '<div class="pack-slot"><div class="pack-need">'+esc(it.text)+'</div>'
      +'<div class="pack-gear">'+body+'</div></div>';
  };
  const slots=(o.manage?items:visible).map(slot).join('');
  const cov=g.coverage||{};
  const md=g.memberDiscount||null;
  const subCount=Number(cov.substituted||0);
  let head='<div class="gear-head"><span class="eyebrow">按清单搭配</span><span class="gear-summary">清单 '
    +Number(cov.needs||0)+' 项 · 商城可配 '+Number(cov.matched||0)+' 项'
    +(subCount?' · 平替 '+subCount+' 项':'')
    +(md?' · <b>'+esc(md.tierName)+' '+esc(String(md.discountZhe))+' 折</b>':'')
    +'</span></div>';
  let missLine='';
  if(o.manage){
    const miss=[];
    items.forEach(it=>{
      // 已经给了平替的项不再算「缺」：整页都在说"暂无"会让顾客以为这家店什么都没有
      if((it.tags||[]).length&&!(it.matches||[]).length&&!(it.substitutes||[]).length){
        const l=(it.tagLabels||[])[0];
        if(l&&miss.indexOf(l)<0)miss.push(l);
      }
    });
    missLine=miss.length
      ? '<div class="gear-missing">以下品类商城暂无，也未找到相近装备：'+esc(miss.join('、'))+'（可在商城上架补全）</div>'
      : '';
  }
  // 「其他在售装备」是补充位：清单里已经出现过的商品不再重复列一次
  const extraRows=extras.filter(p=>!seen[Number(p.id||0)]).map(p=>gearRow(p,o)).join('');
  const extra=extraRows
    ? '<div class="gear-extras"><div class="gear-extras-title">本场活动其他在售装备</div><div class="pack-gear">'+extraRows+'</div></div>'
    : '';
  return head+chips+'<div class="pack-plan">'+slots+'</div>'+missLine+extra;
}
/* 费用说明的键名中文化。fees 是 AI 生成时落库的自由对象，键名常是 newCustomer /
   member / note 这类英文标识符 —— 直接渲染出来，顾客看到的是「newCustomer 498元/人」。
   与 ENUM_CN 同一套取舍：**表里没有的键原样显示**，宁可见到原始键名，也不要编一个
   不存在的中文名把信息改错。 */
const FEE_CN={
  newCustomer:'新客价',new_customer:'新客价',firstTime:'首次参加',first_time:'首次参加',
  member:'会员价',memberPrice:'会员价',vip:'会员价',
  original:'原价',list:'原价',standard:'标准价',regular:'标准价',
  child:'儿童价',kids:'儿童价',student:'学生价',
  deposit:'定金',balance:'尾款',remaining:'尾款',
  note:'说明',notes:'说明',remark:'说明',description:'说明',
  includes:'包含',included:'包含',excludes:'不包含',excluded:'不包含',
  extra:'额外费用',extras:'额外费用',optional:'可选费用',
  gear:'装备租赁',rental:'装备租赁',insurance:'保险费',transport:'交通费',
  meal:'餐费',meals:'餐费',accommodation:'住宿费',ticket:'门票费',guide:'领队费'
};
/* 费用说明：把 fees 对象渲染成可读的键值行，而不是一整块 JSON 代码。数组走 chips。 */
function feeListHtml(fees){
  fees=fees||{};const keys=Object.keys(fees).filter(k=>fees[k]!=null&&fees[k]!=='');
  if(!keys.length)return '<p class="sub">费用以活动通知与最终确认为准</p>';
  return '<div class="fee-list">'+keys.map(k=>{const v=fees[k];
    const label=FEE_CN[String(k)]||FEE_CN[String(k).replace(/[_\-\s]/g,'').toLowerCase()]||k;
    if(Array.isArray(v))return '<div class="fee-row"><b>'+esc(label)+'</b><div class="info-chips">'+v.map(x=>'<div>'+esc(String(x))+'</div>').join('')+'</div></div>';
    if(typeof v==='object')return '<div class="fee-row"><b>'+esc(label)+'</b><p>'+esc(JSON.stringify(v))+'</p></div>';
    return '<div class="fee-row"><b>'+esc(label)+'</b><p>'+esc(String(v))+'</p></div>';
  }).join('')+'</div>';
}
/* 行程数据形状归一：模型（真模型尤其）会输出多种形状 ——
   字符串（"D1：抵达报国寺…"）、{time,text|content|desc}、{day,schedule|items|list:[...]}。
   过去渲染器只认 {time,text}，其他形状整段渲染成空行（2026-09-28 用户截图实锤：
   master.itinerary 明明有完整两天行程，「详细行程」却是空的）。统一归一成 {time,text} 行。 */
function itineraryRows(list){
  const rows=[];
  for(const x of (list||[])){
    if(typeof x==='string'){if(x.trim())rows.push({time:'',text:x.trim()});continue}
    if(!x||typeof x!=='object')continue;
    const day=x.day||x.date||x.title||'';
    const sched=x.schedule||x.items||x.list||x.details||x.arrange;
    if(Array.isArray(sched)){
      if(String(day).trim())rows.push({time:String(day).trim(),text:''});
      for(const s of sched){
        if(typeof s==='string'){if(s.trim())rows.push({time:'',text:s.trim()});continue}
        if(s&&typeof s==='object')rows.push({time:String(s.time||s.period||'').trim(),text:String(s.text||s.content||s.desc||s.detail||s.description||'').trim()});
      }
      continue;
    }
    rows.push({time:String(x.time||x.period||'').trim(),text:String(x.text||x.content||x.desc||x.detail||x.description||'').trim()});
  }
  return rows.filter(r=>r.time||r.text);
}
/* detail.blocks 里已经排过行程时，结构化区不再重复渲染同一份 master.itinerary——
   此前「把一天安排得刚刚好」(promo timeline) 与「详细行程 ITINERARY」是同一份数据渲染两遍，
   同一页出现两次行程，是用户看到的"详情重复出现"。 */
function promoHasItinerary(detail){return ((detail||{}).blocks||[]).some(b=>b&&b.type==='timeline'&&itineraryRows(b.items||b.body||[]).length)}
/* 跳过结构化「详细行程」的前提是 timeline 确实覆盖了同一份行程。
   mock 时代两者是同一份数据，跳过没问题；真模型会各写各的——实测出现过
   timeline 放编辑精选、master.itinerary 装着完整逐日行程却被跳过，
   顾客整页看不到完整行程。所以只有 itinerary 文本确实被 timeline 覆盖时才跳过。 */
function infoStackSkip(detail,master){
  const tls=((detail||{}).blocks||[]).filter(b=>b&&b.type==='timeline');
  if(!tls.length)return [];
  const tlText=tls.map(b=>itineraryRows(b.items||b.body||[]).map(r=>(r.time+' '+r.text).trim()).join(' ')).join(' ');
  const itText=itineraryRows((master||{}).itinerary).map(r=>(r.time+' '+r.text).trim()).join(' ');
  return (itText&&tlText&&tlText.includes(itText.slice(0,120)))?['itinerary']:[];
}
/* 详情页很长，给一条页内跳转，避免"不知道下面还有什么"。用 scrollIntoView 而不是 <a href="#…">，
   避免和可能存在的 hash 路由打架。 */
function jumpTo(id){const el=document.getElementById(id);if(!el)return;el.scrollIntoView({behavior:'smooth',block:'start'});
  // 点击立即高亮，不等 observer 追上来（长页面滚动中途 observer 会连跳几档）。
  document.querySelectorAll('.detail-nav__item').forEach(b=>b.classList.toggle('active',b.dataset.dnav===id));}
function detailNavHtml(items){
  if(!items||!items.length)return '';
  // 调用方拿到返回值后立刻 innerHTML；延迟初始化 scrollspy 落在赋值之后。
  setTimeout(initDetailNav,80);
  return '<div class="detail-nav">'+items.map(([id,label])=>`<button type="button" class="detail-nav__item" data-dnav="${id}" onclick="jumpTo('${id}')">${esc(label)}</button>`).join('')+'</div>';
}
/* scrollspy：滚动到哪个区块，导航就高亮到哪一项；激活项自动横向滚进可视区。 */
function initDetailNav(){
  const nav=document.querySelector('.detail-nav');if(!nav)return;
  const btns=[...nav.querySelectorAll('.detail-nav__item')];if(!btns.length)return;
  if(window.__dnavObs)window.__dnavObs.disconnect();
  const setActive=id=>{btns.forEach(b=>{const on=b.dataset.dnav===id;b.classList.toggle('active',on);
    if(on&&nav.scrollWidth>nav.clientWidth){const x=b.offsetLeft-(nav.clientWidth-b.offsetWidth)/2;nav.scrollTo({left:Math.max(0,x),behavior:'smooth'});}});};
  setActive(btns[0].dataset.dnav);
  const secs=btns.map(b=>document.getElementById(b.dataset.dnav)).filter(Boolean);
  const obs=new IntersectionObserver(es=>{
    const vis=es.filter(e=>e.isIntersecting).sort((a,b)=>a.boundingClientRect.top-b.boundingClientRect.top)[0];
    if(vis)setActive(vis.target.id);
  },{rootMargin:'-12% 0px -68% 0px'});
  secs.forEach(s=>obs.observe(s));
  window.__dnavObs=obs;
}
function renderInfoStack(master,opts={}){
  const skip=opts.skip||[],has=k=>skip.indexOf(k)<0;
  let h='<div class="info-stack polished">';
  if(has('itinerary'))h+=`<details open><summary>详细行程 <span>ITINERARY</span></summary><div class="detail-list">${itineraryRows(master.itinerary).map(x=>`<div>${x.time?`<b>${esc(x.time)}</b>`:''}<p>${esc(x.text)}</p></div>`).join('')||'<p class="sub">以最终活动通知为准</p>'}</div></details>`;
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
  in_progress:'进行中',expired:'已过期',
  /* 售后流程的六个态此前没进来，中文界面会直接漏出 pending_review / awaiting_return 这类原始枚举。
     放在 shared 里而不是某一端，是为了让四端显示同一个名字。 */
  pending_review:'待审核',reviewing:'审核中',awaiting_return:'待寄回',returned:'已寄回',
  exchanging:'换货中',exchanged:'已换货'
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

/* ===== 弹窗可访问性会话：焦点进入 / Tab 圈闭 / Esc 逐层关闭 / 背景滚动锁 =====
   全站有三套 overlay（本文件的 uxDialog、clubos-ux.js 的 uxForm、payment-experience.js 的
   支付 sheet）。改之前只有 uxForm 这一套自带键盘与焦点行为，uxDialog（= showConfirm /
   showAlert / showForm，全站 70+ 个调用点，删除确认、报名确认、提示都走它）与 club 端的
   .modal 什么都没有 —— 实测：打开后焦点仍在 <body>、背景能滚、Tab 直接跑到弹窗背后的页面上、
   Esc 关不掉、也没有 aria-labelledby。这里把同一套能力抽成一个可复用的「会话」，
   三套 overlay 共用一份实现，行为不再分叉。

   实现要点：
     · 弹窗可以叠（uxForm 里再开一个确认框）：用栈，Esc 只关最上层那一层；
     · 背景滚动锁用引用计数，叠了多层时只在最后一层关闭后解锁；
     · 关闭后把焦点还给「打开它的那个元素」，键盘用户不会掉到页面顶部；
     · 聚焦键用捕获阶段并在消费掉 Esc/Tab 时 stopPropagation，避免同时关掉下层的弹窗
       或移动端导航抽屉。 */
const _uxDlgStack=[];let _uxScrollLocks=0,_uxDlgSeq=0;
const _UX_FOCUSABLE='a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
function _uxFocusables(root){return [...root.querySelectorAll(_UX_FOCUSABLE)].filter(el=>el.getClientRects().length>0)}
function uxDialogSession(root,{onEscape=null,initialFocus=null}={}){
  const prior=document.activeElement;
  const session={root};
  const onKey=e=>{
    if(_uxDlgStack[_uxDlgStack.length-1]!==session)return;      /* 只由最上层响应 */
    if(e.key==='Escape'){e.stopPropagation();if(onEscape)onEscape();return}
    if(e.key!=='Tab')return;
    const f=_uxFocusables(root);
    if(!f.length){e.preventDefault();root.focus?.();return}
    const first=f[0],last=f[f.length-1];
    const here=root.contains(document.activeElement)?document.activeElement:null;
    if(e.shiftKey&&(!here||here===first)){e.preventDefault();last.focus()}
    else if(!e.shiftKey&&(!here||here===last)){e.preventDefault();first.focus()}
  };
  session.release=()=>{
    const i=_uxDlgStack.indexOf(session);if(i<0)return;
    _uxDlgStack.splice(i,1);document.removeEventListener('keydown',onKey,true);
    /* 解锁前再看一眼「层里还有没有别的 overlay」：uxForm、渠道预览等各自管理 body 滚动
       （不参与这里的引用计数），只按计数解锁会把它们的锁一起解掉；同时必须把「正在释放的
       这一层自己」排除掉，否则关掉最后一层弹窗后滚动会永久锁住。 */
    if(--_uxScrollLocks<=0){
      _uxScrollLocks=0;
      const others=[...document.querySelectorAll('.ux-overlay,.modal.show')].filter(el=>el!==root);
      if(!others.length)document.body.style.overflow='';
    }
    if(prior&&prior.isConnected&&typeof prior.focus==='function'){try{prior.focus({preventScroll:true})}catch(_){prior.focus()}}
  };
  _uxDlgStack.push(session);
  if(_uxScrollLocks++===0)document.body.style.overflow='hidden';
  document.addEventListener('keydown',onKey,true);
  const pick=typeof initialFocus==='string'?root.querySelector(initialFocus):initialFocus;
  const target=pick||_uxFocusables(root)[0]||root;
  try{target.focus({preventScroll:true})}catch(_){target?.focus?.()}
  return session;
}
window.uxDialogSession=uxDialogSession;

/* ===== 原生弹窗替代层：结构化表单 / 确认弹窗 / 信息弹窗（替换 prompt/confirm/alert）===== */
function uxDialog({title='',desc='',body='',foot='',wide=false,onClose=null,initialFocus=null}={}){
  const ov=document.createElement('div');ov.className='ux-overlay';
  const titleId='uxdlg-title-'+(++_uxDlgSeq);
  ov.innerHTML=`<div class="ux-dialog${wide?' wide':''}" role="dialog" aria-modal="true" aria-labelledby="${titleId}" tabindex="-1">
    <div class="ux-dialog-head"><div><h2 id="${titleId}">${esc(title)}</h2>${desc?`<p>${esc(desc)}</p>`:''}</div><button class="ux-x" type="button" aria-label="关闭">×</button></div>
    ${body?`<div class="ux-dialog-body">${body}</div>`:''}${foot?`<div class="ux-dialog-foot">${foot}</div>`:''}</div>`;
  document.body.appendChild(ov);
  let closed=false,session=null;
  /* uxClose 幂等：取消/×/点遮罩/写请求落定后的持有回调都走它，只会真正释放一次。 */
  const close=()=>{if(closed)return;closed=true;session&&session.release();ov.remove();if(onClose)onClose()};
  ov.uxClose=close;
  ov.querySelector('.ux-x').onclick=close;
  ov.onclick=e=>{if(e.target===ov)close()};
  /* foot 里的 [data-ok]（各端「关闭 / 知道了」按钮）原本没有任何行为，点了没反应 ——
     弹窗只能靠 × / 点遮罩 / Esc 关。这里补上默认关闭；showConfirm 随后会在自己那一层重新覆盖。 */
  const footOk=ov.querySelector('.ux-dialog-foot [data-ok]');
  if(footOk&&!footOk.onclick)footOk.onclick=()=>close();
  session=uxDialogSession(ov,{onEscape:close,initialFocus:initialFocus||ov.querySelector('.ux-dialog')});
  return ov;
}
function showConfirm({title='请确认',message='',confirmText='确认',cancelText='取消',danger=false}={}){
  return new Promise(res=>{
    let done=false;const fin=v=>{if(done)return;done=true;res(v)};
    /* 破坏性确认默认聚焦「取消」，回车/空格不会误触发删除；普通确认才聚焦主按钮。 */
    const ov=uxDialog({title,desc:message,onClose:()=>fin(false),initialFocus:danger?'[data-cancel]':'[data-ok]',foot:`<button class="btn ghost" type="button" data-cancel>${esc(cancelText)}</button><button class="btn ${danger?'danger':''}" type="button" data-ok>${esc(confirmText)}</button>`});
    ov.querySelector('[data-cancel]').onclick=()=>{ov.uxClose();fin(false)};
    ov.querySelector('[data-ok]').onclick=()=>{_holdDialogUntilIdle(ov,ov.querySelector('[data-ok]'),()=>ov.uxClose());fin(true)};
  });
}
function showAlert({title='提示',message='',okText='知道了',wide=false}={}){
  return new Promise(res=>{
    let done=false;const fin=()=>{if(done)return;done=true;res()};
    const ov=uxDialog({title,desc:message,wide,onClose:fin,initialFocus:'[data-ok]',foot:`<button class="btn" type="button" data-ok>${esc(okText)}</button>`});
    ov.querySelector('[data-ok]').onclick=()=>{ov.uxClose();fin()};
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
    const ov=uxDialog({title,desc,wide,body,onClose:()=>fin(null),initialFocus:'input:not([type=file]),select,textarea',foot:`<button class="btn ghost" type="button" data-cancel>取消</button><button class="btn" type="button" data-submit>${esc(submitText)}</button>`});
    const bodyEl=ov.querySelector('.ux-dialog-body')||ov.querySelector('.ux-dialog');
    ov.querySelector('[data-cancel]').onclick=()=>{ov.uxClose();fin(null)};
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
      _holdDialogUntilIdle(ov,ov.querySelector('[data-submit]'),()=>ov.uxClose());fin(vals);
    };
  });
}

/* ===== 领队头像（俱乐部端后台 + 领队执行端共用，四端都加载本文件）=====
   领队在排班、名册、执行页要认人。没有头像时用姓名首字生成占位圆，
   配色由姓名哈希稳定得出 —— 同一个人在任何页面都是同一个颜色，
   不会出现上一页蓝的下一页绿的情况。 */
const LEADER_AV_PALETTE=['#2f6b48','#3c5d78','#7a5a2e','#6d3a52','#2f5f5a','#5a4a7a'];
function leaderAvatarColor(name){
  const s=String(name||'');let n=0;
  for(let i=0;i<s.length;i++)n=(n*31+s.charCodeAt(i))>>>0;
  return LEADER_AV_PALETTE[n%LEADER_AV_PALETTE.length];
}
function leaderAvatar(name,url,size){
  const nm=String(name||'').trim();
  const cls=['leader-av',size?`leader-av--${size}`:''].filter(Boolean).join(' ');
  // 刻意不加 loading="lazy"：这是几十像素的小图，懒加载省不下什么，
  // 却会让头像在部分滚动位置一直空白 —— 「图不显示」比多请求一张图贵得多。
  if(url)return `<span class="${cls}"><img src="${esc(url)}" alt="${esc(nm)}"></span>`;
  if(!nm)return `<span class="${cls}" style="background:#9db5a6"></span>`;
  return `<span class="${cls}" style="background:${leaderAvatarColor(nm)}" title="${esc(nm)}" aria-hidden="true">${esc(nm.slice(0,1))}</span>`;
}
