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
/* 把后端错误体归一成一句人能读的话。
   ★ 为什么必须在这里归一，而不是让每个catch 自己处理：
   FastAPI 的 `detail` **不一定是字符串** ——
     · `HTTPException(409,'xx')` → `'xx'`（正常）；
     · **请求体校验失败 422 → `detail` 是数组** `[{loc,msg,type},…]`；
     · 少数校验器/中间件会回 `detail:{msg:…}` 或 `detail:{error:…}`。
   旧代码直接 `new Error(d.detail)`，遇到数组/对象会被强制 String() →
   顾客看到 `[object Object][object Object]`（用户 2026-10-06 截图实证：
   C 端点「补资料」即撞此坑）。edPlain 虽能兜住不吐 [object Object]，
   但会把 loc/msg/type 三个键的值全拼出来，仍然不是能读的话，
   所以这里显式按形状取msg、并把字段名中文化。 */
function apiErrorText(detail,status){
  const fallback='请求失败'+(status?`（${status}）`:'');
  if(detail==null)return fallback;
  if(typeof detail==='string')return detail.trim()||fallback;
  // 422 校验错误数组：取每条的 msg，并带上中文字段名
  if(Array.isArray(detail)){
    const names={idType:'证件类型',idNumber:'证件号码',emergencyContactName:'紧急联系人',
      emergencyContactPhone:'紧急联系人电话',name:'姓名',phone:'手机号',password:'密码',
      email:'邮箱',amount:'金额',price:'价格',quantity:'数量',voucherCodes:'福利券码',reason:'原因'};
    const parts=detail.map(x=>{
      if(x==null)return'';
      if(typeof x==='string')return x;
      const loc=Array.isArray(x.loc)?x.loc.filter(k=>k!=='body'&&k!=='query'&&k!=='path'):[];
      const field=loc.length?names[String(loc[loc.length-1])]||String(loc[loc.length-1]):'';
      const msg=x.msg||x.message||x.detail||'';
      let text=typeof msg==='string'?msg:'';
      /* FastAPI/pydantic 的 msg 是英文（Field required / Input should be a valid integer…）。
         顾客看英文校验提示等于没提示，按 type + 常见 msg 前缀翻成中文；
         翻不出来就保留原文（总比 [object Object] 强）。 */
      if(text)text=apiMsgCn(text,x.type);
      if(!text)return '';
      return field?`${field}：${text}`:text;
    }).filter(Boolean);
    return parts.length?parts.join('；'):fallback;
  }
  if(typeof detail==='object'){
    const msg=detail.msg||detail.message||detail.error||detail.reason;
    if(typeof msg==='string'&&msg.trim())return apiMsgCn(msg.trim(),'');
  }
  return fallback;
}
/* 校验错误英文 → 中文。命中不了就原样返回，不做猜测式翻译。 */
function apiMsgCn(text,type){
  const t=String(text||'');
  const exact={'Field required':'不能为空','missing':'不能为空',
    'Input should be a valid integer':'请填整数','Input should be a valid number':'请填数字',
    'Input should be a valid string':'请填文本','Input should be a valid list':'格式不正确',
    'Input should be a valid dictionary':'格式不正确','value is not a valid email':'邮箱格式不正确',
    'Input should be a valid boolean':'请填是/否','string too short':'内容太短','string too long':'内容太长'};
  if(exact[t])return exact[t];
  let m=t;
  m=m.replace(/^Field required$/,'不能为空');
  m=m.replace(/^Input should be a valid (\w+).*$/,(s,g)=>exact['Input should be a valid '+g]||'格式不正确');
  m=m.replace(/^Value error,\s*/,'参数错误：');
  // 已被上面规则处理过的（仍是英文原文）保持不变
  return m;
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
      if(!r.ok)throw new Error(apiErrorText(d&&d.detail,r.status));return d
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
/* 活动价显示：成本为 0 / 缺失 / 被标记为待定时，一律显示「价格待定」，绝不把成本底价当售价露出。
   `pending` 由后端在「活动来自成本表」时打上（priceFrom='pending'）；即便没有该标记，
   只要价格不是正数也按待定处理 —— 兜底防住库里残留的成本价。
   unit 是价格单位（如 "/人"），普通商品/订单价请不要走这里（它们永远是真售价）。 */
function pricePending(p,opts){opts=opts||{};return opts.pending||!(Number(p)>0)}
function priceHtml(p,opts){opts=opts||{};
  if(pricePending(p,opts))return '<span class="price-pending">价格待定</span>';
  const unit=opts.unit||'';return money(p)+(unit?`<em>${unit}</em>`:'');}
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
/* toast 是全站最后一道文案出口（uxTask / 各 catch 都往这里塞 e.message）。
   任何非字符串进来都先归一 —— 用户截图实锤过 `[object Object][object Object]`：
   根因是 api() 把 FastAPI 的数组 detail 直接交给 new Error()。这里再兜一次，
   保证「点任何按钮都不会弹出看不懂的东西」。 */
function toast(msg){
  let s=(typeof msg==='string')?msg:(msg==null?'':apiErrorText(msg));
  if(!s.trim())s='操作未完成';
  /* 归一后仍可能过长（数组拼接时），截断避免撑破移动端toast */
  if(s.length>160)s=s.slice(0,160)+'…';
  let el=document.createElement('div');el.textContent=s;el.className='toast';document.body.appendChild(el);setTimeout(()=>el.remove(),2400)}
/* 数据加载失败的统一错误态：把残留骨架换成明确提示 + 重试入口。
   各端很多加载函数此前没有 try/catch，接口一旦报错就永远停在骨架，用户无从判断是网络还是系统问题。 */
function loaderError(sel,e,tip){
  const box=document.querySelector(sel); if(!box)return;
  box.innerHTML='<div class="notice warn">'+(tip||'加载失败')+'：'+esc((e&&e.message)||e||'未知错误')
    +'<div class="sub" style="margin-top:8px"><button class="btn ghost" type="button" onclick="location.reload()">刷新重试</button></div></div>';
}
/* ★ 全站文本渲染的总闸门。真模型给对象/数组时 String() 会得到 "[object Object]"，
   直接印到顾客眼前（2026-10-03 实锤）。所有文案最终都要过这里，所以**在这一层一次性收口**：
   对象/数组先经 edPlain() 归一成纯文本再转义。标量输入的行为完全不变。 */
function esc(v=''){
  if(v!=null&&typeof v==='object')v=edPlain(v);
  return String(v).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}
function mediaMap(master){let m={};for(const x of master?.media||[]){if(typeof x==='string'){if(x)m[x]=m[x]||{ref:x};continue}if(x?.ref)m[x.ref]=x}return m}
function resolvedRefs(refs,map){return (refs||[]).filter(r=>r&&map[r]&&map[r].url)}
function mediaHtml(ref,map,cls=''){const x=map[ref];if(!x?.url)return `<div class="editorial-media missing ${cls}"><span>${esc(ref||'image')}</span></div>`;return `<figure class="editorial-media ${cls}"><img src="${esc(x.url)}" alt="" loading="lazy"></figure>`}
/* 把任意形状的值归一成**纯文本**，且任何形状都不许产出 "[object Object]"。
   真模型（通义千问等）输出的字段形状不稳定：同一处可能给字符串、字符串数组，
   也可能给对象数组（[{text:'…'},{para:'…'}]）。直接 String() 就会把
   "[object Object]" 印到页面上 —— 与 fees 嵌套对象是同一类洞（2026-10-03）。
   取字段的优先级按模型实测高频键排，取不到就递归把所有字符串值拼出来。 */
const ED_TEXT_KEYS=['text','content','desc','detail','description','value','para','p','item','title','body','summary','copy'];
function edPlain(v){
  if(v==null)return '';
  if(typeof v==='string')return v;
  if(typeof v==='number'||typeof v==='boolean')return String(v);
  if(Array.isArray(v))return v.map(edPlain).filter(Boolean).join('\n');
  if(typeof v==='object'){
    for(const k of ED_TEXT_KEYS){const s=v[k];if(typeof s==='string'&&s.trim())return s}
    return Object.keys(v).map(k=>edPlain(v[k])).filter(Boolean).join('\n');
  }
  return '';
}
/* 文案段落归一：真模型会把 body 写成数组或多句一段（\n 分隔）。过去整段 esc 进一个 <p>，
   编辑排版的多段节奏全部丢失（用户截图实锤「文字平铺没有吸引力」）。
   数组 → 逐段；字符串 → 按 \n 拆；空段丢弃；渲染不出任何段落就不输出。
   走 edPlain 归一：对象数组也能拆成正常段落，而不是 [object Object]。 */
function edParas(v){
  const raw=Array.isArray(v)?v.map(edPlain).join('\n'):edPlain(v);
  // 宣传正文不许以钟点区间开头（「14:20至15:00，…」像排班表）：分钟级安排在「详细行程」折叠区
  // 已完整呈现（2026-10-08 用户反馈）。只剥段落开头，不动句中时间；旧数据也会在这里被兜住。
  const clockRe=/^\d{1,2}[:：]\d{2}\s*[至到~～\-—–]\s*\d{1,2}[:：]\d{2}\s*[,，、：:]?\s*/;
  const arr=String(raw||'').split(/\n+/).map(s=>s.trim().replace(clockRe,'')).filter(Boolean);
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
    /* facts 的 value 必须是标量。真模型可能给对象/数组（2026-10-03：esc(x.value||x)
       遇到对象会把它 String() 成 JSON 印在页面上）。这里只渲染标量，
       对象/数组用 feeValueHtml 展开，绝不把 JSON 文本漏给顾客。 */
    else if(b.type==='facts'){
      /* facts 现在也常被模型用来写「这场活动只做三件事」这类引导语（headline）+
         几条具体说明（items，label 常为空）。引导语以前会被丢掉，整块只剩几行孤立的字；
         label 为空时也不再渲染一个空的 <small>。 */
      const fit=(b.items||[]).map(x=>{
        let v=(x&&typeof x==='object'&&!Array.isArray(x))?x.value:x;
        let shown;
        if(v==null)v=(x&&typeof x==='object')?feeValueHtml(x,1):x;
        else if(typeof v==='object')shown=feeValueHtml(v,1);
        else shown=esc(String(v));
        const lab=(x&&x.label)?`<small>${esc(x.label)}</small>`:'';
        return `<div>${lab}<strong>${shown==null?'':shown}</strong></div>`;
      }).join('');
      // 空块不上屏：宁可不渲染，也不要页面中间出现一块空白
      if(fit)h+=`<section class="ed-facts">${(b.headline||b.title)?`<p class="ed-facts__lead">${esc(b.headline||b.title)}</p>`:''}${fit}</section>`;
    }
    else if(b.type==='narrative'){
      const ok=resolvedRefs(refs,mm);
      const media=ok.length?`<div class="ed-narrative-media">${ok.map(r=>mediaHtml(r,mm)).join('')}</div>`:'';
      /* 文案升级（2026-09-28 用户反馈「图片好看但文字没气势」）：
         body 支持多段（数组或 \n 分隔），pull 是独立金句行——编辑排版的节奏全靠这两样。
         layout 变体（2026-10-08）：image-left / image-right 让图文左右错落，full 让首图整版铺满 +
         文字叠在图上 —— 给模型更多"画报感"的拼法，避免每版都长一个样。 */
      const pull=b.pull?`<p class="ed-pull">${esc(b.pull)}</p>`:'';
      const copy=`<div class="ed-copy">${b.eyebrow?`<div class="ed-kicker">${esc(b.eyebrow)}</div>`:''}<h2>${esc(b.headline||'')}</h2>${edParas(b.body||b.text||'')}${pull}</div>`;
      const layout=b.layout||'text-top';
      if((layout==='image-left'||layout==='image-right')&&ok.length){
        h+=`<section class="ed-narrative ed-narrative--split ${layout==='image-right'?'img-right':'img-left'} has-media">${layout==='image-right'?copy+media:media+copy}</section>`;
      }else if(layout==='full'&&ok.length){
        const url=mm[ok[0]]?.url||'';
        h+=`<section class="ed-narrative ed-narrative--full${url?' has-photo':''}"${url?` style="background-image:linear-gradient(180deg,rgba(7,17,14,.06),rgba(7,17,14,.74)),url('${esc(url)}')"`:''}><div class="ed-narrative__fullcopy">${copy}</div></section>`;
      }else{
        h+=`<section class="ed-narrative ${ok.length?'has-media':''}${pull?' has-pull':''}">${copy}${media}</section>`;
      }
    }    else if(b.type==='media'){
      // 解析不到 url 的 ref 直接不排版：宁可少一张图，也不要满屏灰色占位块。
      const ok=resolvedRefs(refs,mm);
      if(ok.length||b.caption){
        if(b.layout==='full'&&ok.length){
          // 整版铺满大图：让一张照片占满整段，公众号式的跨页视觉冲击。
          h+=`<section class="ed-media ed-media--full">${ok.map(r=>{const u=mm[r]?.url||'';return `<figure class="editorial-media full-bleed"${u?` style="background-image:url('${esc(u)}')"`:''}></figure>`}).join('')}${b.caption?`<p class="ed-caption">${esc(b.caption)}</p>`:''}</section>`;
        }else{
          const layout=b.layout==='mosaic'?'mosaic':ok.length===2?'pair':ok.length>=3?'grid':'single';
          h+=`<section class="ed-media ${layout}">${ok.map(r=>mediaHtml(r,mm)).join('')}${b.caption?`<p class="ed-caption">${esc(b.caption)}</p>`:''}</section>`;
        }
      }
    }    /* ---- 2026-10-08 新增画报式组件：让第一段（引言宣传）拥有公众号长图级别的版式自由度 ---- */
    else if(b.type==='bigimage'){
      // 整版铺满大图 + 可选叠字标题/图注：画报式跨页图，给页面一个"呼吸的大瞬间"。
      const ok=resolvedRefs(refs,mm);
      if(ok.length){
        const url=mm[ok[0]]?.url||'';
        const cap=(b.title||b.text||b.caption)?`<div class="ed-bigimage__cap">${b.title?`<h3>${esc(b.title)}</h3>`:''}${b.text?`<p>${esc(b.text)}</p>`:''}${b.caption?`<span class="ed-caption">${esc(b.caption)}</span>`:''}</div>`:'';
        h+=`<section class="ed-bigimage${url?' has-photo':''}"${url?` style="background-image:linear-gradient(180deg,rgba(7,17,14,.12),rgba(7,17,14,.62)),url('${esc(url)}')"`:''}>${cap}</section>`;
      }
    }
    else if(b.type==='imagetext'){
      // 杂志式图文左右：narrative 的"图在侧边"版本，更适合做体验/产品特写。
      const ok=resolvedRefs(refs,mm);
      const right=(b.layout||'')==='right';
      const media=ok.length?`<div class="ed-imagetext__media${ok.length>1?' two':''}">${ok.map(r=>mediaHtml(r,mm)).join('')}</div>`:'';
      const copy=`<div class="ed-imagetext__copy">${b.eyebrow?`<div class="ed-kicker">${esc(b.eyebrow)}</div>`:''}<h2>${esc(b.headline||'')}</h2>${edParas(b.body||b.text||'')}</div>`;
      h+=media?`<section class="ed-imagetext ${right?'img-right':'img-left'}">${right?copy+media:media+copy}</section>`:`<section class="ed-narrative">${copy}</section>`;
    }
    else if(b.type==='cards'){
      // 图标 + 标题 + 要点 的卡片网格：把"为什么值得 / 包含什么"做成视觉块，比纯文字段落更有节奏。
      const items=(b.items||[]).filter(x=>x&&(x.title||x.text||x.icon));
      if(items.length)h+=`<section class="ed-cards"><div class="ed-cards__grid">${items.map(x=>`<div class="ed-card"><span class="ed-card__ic">${esc(x.icon||'✦')}</span><div><b>${esc(x.title||'')}</b>${x.text?`<p>${esc(x.text)}</p>`:''}</div></div>`).join('')}</div></section>`;
    }
    else if(b.type==='numbercards'){
      // 大数字统计卡：距离 / 海拔 / 天数 / 人数用大字号突出，比 facts 条更有冲击力。
      const items=(b.items||[]).filter(x=>x&&(x.value!=null&&x.value!==''||x.label));
      if(items.length)h+=`<section class="ed-numbercards">${items.map(x=>{const shown=esc(String(x.value==null?'':x.value));return `<div class="ed-numcard"><strong>${shown}</strong>${x.unit?`<i>${esc(x.unit)}</i>`:''}${x.label?`<span>${esc(x.label)}</span>`:''}</div>`}).join('')}</section>`;
    }
    else if(b.type==='highlight'){
      // 高亮提示框：强调一句关键承诺或须知，与 narrative 区隔。
      if(b.title||b.text)h+=`<section class="ed-highlight">${b.title?`<div class="ed-kicker">${esc(b.title)}</div>`:''}${edParas(b.text||'')}</section>`;
    }
    else if(b.type==='columns'){
      // 双栏长文：无图时的多段排版，避免长文字平铺。
      const t=edPlain(b.text||b.body||'');
      if(t.trim())h+=`<section class="ed-columns">${edParas(b.text||b.body||'')}</section>`;
    }
    else if(b.type==='gallery'){
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
  /* 点清单里的装备 = **先看这件商品**，不是直接下单（用户 2026-10-06 明确要求）。
     之前 C 端（canBuy 分支）绑的是 buy(id) —— 一碰就弹「确认装备订单」，
     顾客连图片、规格、库存都还没看就被要求付钱；这跟商城列表里那张卡
     写着「查看详情」、点了会进#productDetail 的行为也自相矛盾。
     现在两端统一进商品详情，下单动作交给详情页底部那颗「立即购买」。
     详情页入口按端挑，且优先「会先切视图」的那个：C 端的 #productDetail 挂在 wmall
     视图下，wmall 隐藏时 openProduct 会把内容写进一个看不见的容器 → 点了像没反应
     （与 openAct 当年同一个坑）。所以从活动页进来走 openProductFromPacking。*/
  const detailFn=(typeof window.openProductFromPacking==='function'&&!opts.manage)?'openProductFromPacking'
    :((typeof window.openProduct==='function'&&!opts.manage)?'openProduct'
    :((typeof window.openGearProduct==='function')?'openGearProduct':''));
  if(detailFn){
    return '<button type="button" class="gear-row buyable'+(sub?' gear-row--sub':'')+'" onclick="'+detailFn+'('+Number(p.id||0)+')" title="查看这件装备的详情">'+inner+'</button>';
  }
  /* 连详情页都没有的端（理论上不会发生）：退回只读行，绝不退化成「一点就下单」。 */
  return '<div class="gear-row'+(sub?' gear-row--sub':'')+'" title="查看装备详情">'+inner+'</div>';
}
/* 旧 gearRowDup（同一件装备第二次出现时收成「已在上方列出」的只读行）已在 2026-10-09
   随 renderPacking 重构删除：现在按装备聚合、一件装备全局只出现一次，不再需要补丁行。 */
function renderPacking(master,opts){
  opts=opts||{};
  const list=master.checklist||[];
  /* chips 不再是哑的纯文本：哪几项商城真能配到，就要在清单里直接标出来——
     顾客先扫一眼清单（✓ 的=能一键配齐），再往下看分区。 */
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
  /* ★ 2026-10-09 用户反馈重构（「一个装备只推荐出现一次，商城有的和没有的要分开」）：
     以前是「一个清单项一个槽位、槽位里挂商品」，同一件商品跨多个清单项时，第二处只能留一行
     「同一件装备·已在上方列出」——清单被拉长一倍，还看不出哪些是商城真能买到的、哪些得自己准备。
     现在按**装备**聚合，整份清单只分两个区：
       ① 商城可以配齐：每件商品全局只出现一次，卡上写清它满足了哪几项清单要求；
       ② 这些请自己准备：清单里商城配不到的项，只列文字，不挂商品卡。 */
  const goods=[],byKey={};
  const keyOf=p=>{const id=Number((p&&p.id)||0);return id?('id:'+id):('n:'+String((p&&p.name)||''))};
  const addGood=(p,need,isSub)=>{
    if(!p)return;
    const k=keyOf(p);let row=byKey[k];
    if(!row){row={p,needs:[],sub:!!isSub};byKey[k]=row;goods.push(row)}
    else if(row.sub&&!isSub)row.sub=false;   // 既是精确匹配又被当平替：按精确匹配呈现（实线卡）
    const t=String(need||'').trim();
    if(!t)return;
    // 每个清单要求单独记是不是「平替」：同一件商品可能既顶了甲的缺，又是乙的正牌货
    const hit=row.needs.find(n=>n.t===t);
    if(!hit)row.needs.push({t,sub:!!isSub});
    else if(hit.sub&&!isSub)hit.sub=false;
  };
  /* 顾客视角只认「真能买到的」；运营视角（manage）连无匹配的清单项一起过一遍，好定位补货缺口。 */
  const visible=items.filter(it=>(it.matches||[]).length||(it.substitutes||[]).length);
  (o.manage?items:visible).forEach(it=>{
    const need=String(it.text||'').trim();
    const ms=it.matches||[],subs=it.substitutes||[];
    if(ms.length||subs.length){ms.forEach(p=>addGood(p,need,false));subs.forEach(p=>addGood(p,need,true));}
  });
  // 「本次活动推荐装备」并进第一区：全局去重，已经出现过的商品不再列第二次
  extras.forEach(p=>addGood(p,'',false));
  /* 需要自备的 = 清单里有、商城配不到的项（以清单本身为准，而不是以引擎识别到的项为准，
     否则「商城认不出品类」的那些会被悄悄吞掉，顾客以为全都买得到）。 */
  const miss=[];
  (list||[]).forEach(x=>{
    const t=String(x||'').trim();
    if(t&&!okTexts.has(t)&&miss.indexOf(t)<0)miss.push(t);
  });
  /* 卡上写清它满足了哪几项清单要求；平替的那一项单独标出来——
     顾客得知道「这不是清单里那件，是商城目前最接近的同类」（引擎给的理由在卡片行里）。 */
  const needTags=r=>r.needs.length
    ? '<div class="pack-needs">'+r.needs.slice(0,4).map(n=>'<span'+(n.sub?' class="pack-needs__sub" title="商城没有清单里那件，这是最接近的同类"':'')+'>'+(n.sub?'平替 · ':'')+esc(n.t)+'</span>').join('')
      +(r.needs.length>4?'<span class="pack-needs__more">+'+(r.needs.length-4)+'</span>':'')+'</div>'
    : '';
  const ownHtml=goods.length
    ? '<div class="pack-sec pack-sec--own"><div class="pack-sec__head"><b>商城可以配齐</b>'
      +'<span class="pack-sec__count">'+goods.length+' 件</span>'
      +'<span class="pack-sec__hint">点开看规格与会员价</span></div>'
      +'<div class="pack-goods'+(goods.length===1?' pack-goods--1':'')+'">'
      +goods.map(r=>'<div class="pack-good">'+gearRow(r.p,o,r.sub)+needTags(r)+'</div>').join('')
      +'</div></div>'
    : '';
  const missHtml=miss.length
    ? '<div class="pack-sec pack-sec--miss"><div class="pack-sec__head"><b>这些请自己准备</b>'
      +'<span class="pack-sec__count">商城暂无</span>'
      +'<span class="pack-sec__hint">'+(o.manage?'可在商城上架补全':'按自己的习惯带去就好')+'</span></div>'
      +'<div class="pack-miss">'+miss.map(t=>'<div class="pack-miss__item">'+esc(t)+'</div>').join('')+'</div></div>'
    : '';
  const cov=g.coverage||{};
  const md=g.memberDiscount||null;
  const subCount=Number(cov.substituted||0);
  const head='<div class="gear-head"><span class="eyebrow">按清单搭配</span><span class="gear-summary">清单 '
    +Number(cov.needs||0)+' 项 · 商城可配 '+Number(cov.matched||0)+' 项'
    +(subCount?' · 平替 '+subCount+' 项':'')
    +(md?' · <b>'+esc(md.tierName)+' '+esc(String(md.discountZhe))+' 折</b>':'')
    +'</span></div>';
  /* 运营视角额外给一句「哪些品类商城没有」——这是给老板补货看的；
     顾客分区里只有「请自己准备」，不需要出现「缺货」这种字眼。 */
  let missLine='';
  if(o.manage){
    const cats=[];
    items.forEach(it=>{
      if((it.tags||[]).length&&!(it.matches||[]).length&&!(it.substitutes||[]).length){
        const l=(it.tagLabels||[])[0];
        if(l&&cats.indexOf(l)<0)cats.push(l);
      }
    });
    missLine=cats.length?'<div class="gear-missing">以下品类商城暂无，也未找到相近装备：'+esc(cats.join('、'))+'（可在商城上架补全）</div>':'';
  }
  return head+chips+ownHtml+missHtml+missLine;
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
  meal:'餐费',meals:'餐费',accommodation:'住宿费',ticket:'门票费',guide:'领队费',
  /* 真模型（通义千问）爱用的结构化键，2026-10-03 用户截图里裸露成 price/currency/condition/benefit。
     补这一批是因为模型输出形状是「自由对象」，键名集合比人工枚举宽得多 ——
     与 ENUM_CN 同一取舍：表里没有的键原样显示，宁可见到原始键名也不编错中文。 */
  price:'价格',amount:'金额',currency:'币种',unit:'单位',perPerson:'每人',per_person:'每人',
  discount:'优惠',discounts:'优惠',coupon:'优惠券',voucher:'代金券',
  condition:'条件',benefit:'权益',benefits:'权益',gift:'赠品',shoppingGift:'购物赠礼',
  shopping_gift:'购物赠礼',giftCondition:'赠礼条件',giftBenefit:'赠礼权益',
  deadline:'截止时间',validUntil:'有效期至',usage:'使用方式',limit:'限制',
  refundable:'是否可退',refund:'退款规则',depositNote:'定金说明',payment:'付款方式'
};
/* 费用说明里「值本身就是价格/币种」的键：直接并进父行的值，不单独占一行。
   否则会出现「价格 498」下面又一行「币种 积分」这种把读者当数据字段看的排版。 */
const FEE_INLINE_KEYS=new Set(['price','amount','currency','unit','perPerson','per_person']);
/* 费用说明：把 fees 对象渲染成可读的键值行，而不是一整块 JSON 代码。数组走 chips。
   真模型（通义千问等）输出的是**嵌套对象** —— fees.newCustomer = {price, includes:[...]}
   2026-10-03 用户截图实锤：只处理一层时 `JSON.stringify(v)` 把
   {"price":498,"includes":["icebreaker 200 Oasis T恤…"]} 原样印在页面上。
   规则：递归展开成子行，**任何一层都不许再出现 JSON 文本**。 */
function feeValueHtml(v,depth){
  depth=depth||0;
  if(v==null||v==='')return '';
  if(Array.isArray(v)){
    const items=v.map(x=>feeValueHtml(x,depth+1)).filter(Boolean);
    if(!items.length)return '';
    // 纯文本数组走 chips；元素本身是对象则已经渲染成子块，直接顺序铺开
    if(items.every(s=>s.indexOf('<div')!==0))return '<div class="info-chips">'+items.map(s=>'<div>'+s+'</div>').join('')+'</div>';
    return '<div class="fee-nest">'+items.join('')+'</div>';
  }
  if(typeof v==='object'){
    const keys=Object.keys(v).filter(k=>v[k]!=null&&v[k]!=='');
    if(!keys.length)return '';
    // price/currency/unit 这类「值本身就是价格/币种」的键合成一行「498 · 积分」，不逐个字段占行
    const inline=keys.filter(k=>FEE_INLINE_KEYS.has(String(k).replace(/[_\-\s]/g,'').toLowerCase())&&typeof v[k]!=='object');
    const inlineTxt=inline.map(k=>esc(String(v[k]))).filter(Boolean).join(' · ');
    const rest=keys.filter(k=>inline.indexOf(k)<0);
    const parts=[];
    if(inlineTxt)parts.push('<p class="fee-lead">'+inlineTxt+'</p>');
    if(rest.length)parts.push('<div class="fee-nest">'+rest.map(k=>{
      const label=FEE_CN[String(k)]||FEE_CN[String(k).replace(/[_\-\s]/g,'').toLowerCase()]||k;
      const raw=v[k];
      /* ★ 标量必须包一层 <p>：裸文本节点在 flex 容器里是**匿名 flex 项**，
         拿不到 `.fee-row>*:last-child{flex:1 1 auto;min-width:0}`，宽度会按内容参差
         —— 2026-10-03 实测「说明」被压成 22px 换行、同组「条件」294px。 */
      const inner=(raw!=null&&typeof raw!=='object')?('<p>'+esc(String(raw))+'</p>'):feeValueHtml(raw,depth+1);
      if(!inner)return '';
      return '<div class="fee-row'+(depth?' sub':'')+'"><b>'+esc(label)+'</b>'+inner+'</div>';
    }).join('')+'</div>');
    if(!parts.length)return '';
    /* ★ 多块（内联价格 + 子行）必须包成**一个**容器再返回。
       否则父行变成「标签 + 价格 + 子行」三个并列 flex 子项，全挤在同一行
       （2026-10-03 实测：新客价行里 498 与「包含」并排）。 */
    return parts.length===1?parts[0]:'<div class="fee-nest">'+parts.join('')+'</div>';
  }
  return esc(String(v));
}
/* 成本闸门（2026-10-06）：费项标签若命中成本专属词，绝不许渲染到顾客眼前（后端已剔除，
   这里再兜底一道，防住浏览器缓存/其它路径漏进来的老数据）。只拦成本专属词，绝不含泛词
   「费用」「价格」—— 否则会把公开的 `费用包含`、对外 `价格` 一并删掉。
   注：`对外报价` 含「报价」但不在下表，故不会被误删（对外报价是俱乐部想公开的价）。 */
const _COST_FEE_LABELS=['人均费用','人均单价','人均价','合计','总计','小计','单价','总价','总费用',
  '人均成本','成本','毛利','利润','净利','税金','税费','未含税','不含税','含税','税后','税前',
  '策划执行','策划费','管理费率','报价单','预算','结算','返点','提成','成本预估','费用结构',
  '费用条目','前期合计','前期计调','利润率'];
function isCostFeeLabel(label){const s=String(label||'');
  return _COST_FEE_LABELS.some(t=>s.indexOf(t)>=0);}
function feeListHtml(fees){
  fees=fees||{};
  // fees 本身可能是数组或单个对象（模型输出形状不固定），统一成键值对再渲染
  let pairs=[];
  if(Array.isArray(fees)){
    fees.forEach((x,i)=>{if(x!=null&&x!==''&&!isCostFeeLabel(x))pairs.push([String(i),x])});
  }else if(typeof fees==='object'){
    pairs=Object.keys(fees)
      .filter(k=>fees[k]!=null&&fees[k]!==''&&!isCostFeeLabel(k))
      // 值是成本措辞（如纯「¥3,806.55」）也一并丢弃
      .filter(k=>!isCostFeeLabel(fees[k]))
      .map(k=>[k,fees[k]]);
  }else if(fees!==''&&fees!=null){
    pairs=isCostFeeLabel(fees)?[]:[['',fees]];
  }
  /* 成本行被过滤光时返回空串（而不是「费用以活动通知为准」这句占位）——
     占位文案会让调用方以为还有内容，渲染出一个只有「费用说明 PRICE」标题的空壳栏目。
     真要提示顾客，交给调用方按「价格待定」统一处理。 */
  if(!pairs.length)return '';
  return '<div class="fee-list">'+pairs.map(([k,v])=>{
    const label=k?(FEE_CN[String(k)]||FEE_CN[String(k).replace(/[_\-\s]/g,'').toLowerCase()]||k):'';
    /* 「费用包含 / 费用不含」是资料里本来就分栏写给顾客的两组（✅ 含 / ❌ 不含）。
       在这里给整行打一个极性类，chips 的 ✓/✗ 由 CSS 按谱系统一下发 ——
       不往 feeValueHtml 里穿参数，嵌套多深的数组都能吃到同一个标记。 */
    const pol=/不含|不包含|未含|自理|自费|exclude/i.test(String(k||''))?'is-no'
             :(/包含|含|include/i.test(String(k||''))?'is-ok':'');
    // 顶层值传 depth=1：这样 feeValueHtml 展开出的子行才会带 `.sub`（缩进 + 76px 标签列）
    let inner=feeValueHtml(v,1);
    if(!inner)return '';
    if(inner.charAt(0)!=='<')inner='<p>'+inner+'</p>';   // 扁平标量也包 <p>，成为合法 flex 项
    /* 极性类（--ok/--no）只是叠加在基础类 fee-row 之上的修饰，必须保留 fee-row 这个基类，
       否则俱乐部端 .fee-row{display:flex} 的「标签 92px 列 + 内容」并排布局会失效（标签和正文堆叠）。 */
    const cls='fee-row'+(pol?' '+pol:'');
    if(!label)return '<div class="'+cls+'">'+inner+'</div>';
    return '<div class="'+cls+'"><b>'+esc(label)+'</b>'+inner+'</div>';
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
/* ★ 逐日行程折叠（2026-10-08 用户要求，用户截图实锤）：多日行程在页面上是一条条平铺，
   3 天就是二三十行，顾客要滑很久才能看到费用与报名；「哪几行属于哪一天」还得靠
   「Day 1 06:30」这种前缀去认。现在按天收成一条：DAY n ／ 时间范围 ／ 当天前几项预告 ／ N 项，
   点开才展开当天明细（与主流 OTA 的行程卡一致）。**单日活动保持原来的平铺**，不套多余一层。 */
const _DAY_CN={'一':'1','二':'2','三':'3','四':'4','五':'5','六':'6','七':'7','八':'8','九':'9','十':'10'};
function dayNumOf(s){
  const t=String(s==null?'':s).trim();
  let m=t.match(/^(?:day|d)\s*([0-9]{1,2})\b/i);
  if(m)return String(Number(m[1]));
  m=t.match(/^第\s*([0-9]{1,2}|[一二三四五六七八九十])\s*[天日]/);
  if(m){const v=m[1];return /^[0-9]+$/.test(v)?String(Number(v)):(_DAY_CN[v]||'');}
  return '';
}
function stripDayPrefix(s){
  return String(s==null?'':s)
    .replace(/^\s*(?:day|d)\s*[0-9]{1,2}\s*[·:：\-–—,，、.．]?\s*/i,'')
    .replace(/^\s*第\s*(?:[0-9]{1,2}|[一二三四五六七八九十])\s*[天日]\s*[·:：\-–—,，、.．]?\s*/,'')
    .trim();
}
/* 归一后的行按天分组。日期前缀既可能在 time（"Day 1 06:30"）也可能在 text（"Day 1：抵达营地"）；
   `{day,schedule:[…]}` 这类嵌套形状经 itineraryRows 会变成一条「Day N / 空正文」的标题行 ——
   所以纯标题行只用来开新分组，不产出数据行。 */
function itineraryGroups(list){
  const out=[],seen={};let cur=null;
  for(const r of itineraryRows(list)){
    const rawT=(r.time||'').trim(),rawX=(r.text||'').trim();
    const num=dayNumOf(rawT)||dayNumOf(rawX);
    let time=rawT,text=rawX;
    if(num){time=stripDayPrefix(rawT);if(!time)text=stripDayPrefix(rawX);}
    if(num){
      if(!seen[num]){cur={num:num,rows:[]};seen[num]=cur;out.push(cur);}
      else cur=seen[num];
      if(!text)continue;
    }else if(!cur){
      cur={num:'',rows:[]};out.push(cur);
    }
    cur.rows.push({time:time,text:text});
  }
  return out.filter(g=>g.rows.length);
}
function itinRowHtml(x){return `<div>${x.time?`<b>${esc(x.time)}</b>`:''}<p>${esc(x.text)}</p></div>`;}
function itinDigest(rows){
  const t=rows.map(r=>r.text).filter(Boolean).join(' · ');
  return t.length>38?t.slice(0,38)+'…':t;
}
function itineraryBodyHtml(master){
  const groups=itineraryGroups((master||{}).itinerary);
  let all=[];groups.forEach(g=>{all=all.concat(g.rows)});
  if(!all.length)return '<p class="sub">以最终活动通知为准</p>';
  // 只有一天、或资料里压根没有日期前缀 → 保持平铺，不给单日行程套一层折叠
  if(groups.length<2||!groups.some(g=>g.num))return `<div class="detail-list">${all.map(itinRowHtml).join('')}</div>`;
  return '<div class="itin-days">'+groups.map((g,i)=>{
    const times=g.rows.map(r=>r.time).filter(Boolean);
    const range=times.length?(times[0]+(times.length>1&&times[times.length-1]!==times[0]?' – '+times[times.length-1]:'')):'';
    return `<details class="itin-day"${i===0?' open':''}>`
      +`<summary><span class="itin-day__no"><i>DAY</i><b>${esc(g.num||String(i+1))}</b></span>`
      +`<span class="itin-day__main"><b class="itin-day__range">${esc(range||('第 '+(i+1)+' 天'))}</b>`
      +`<small class="itin-day__digest">${esc(itinDigest(g.rows))}</small></span>`
      +`<span class="itin-day__count">${g.rows.length} 项</span></summary>`
      +`<div class="detail-list">${g.rows.map(itinRowHtml).join('')}</div></details>`;
  }).join('')+'</div>';
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
  // 多日行程走按天折叠（itineraryBodyHtml），单日仍是平铺
  if(has('itinerary'))h+=`<details open class="info-sec"><summary>详细行程 <span>ITINERARY</span></summary>${itineraryBodyHtml(master)}</details>`;
  /* 成本闸门（2026-10-06）：费用说明整块按内容决定是否渲染。
     成本行被后端/本地过滤后如果一条都不剩，就不要留一个只有「费用说明 PRICE」标题的空壳 ——
     那既是个空栏目，又在提示顾客「这里原本有价格」。价格待定时由priceHtml 显示「价格待定」。
     ★ 2026-10-08 用户截图实锤「这里的费用说明为空」：内容其实在（fees 有 6 项），
     但这里写的是 `<details${has('itinerary')?'':' open'}>` —— 只要页面同时有「详细行程」，
     费用说明就**默认折叠**；而 .info-stack summary 是 display:flex，浏览器不再画那个三角，
     折叠态看起来就是一个只有标题的空壳。费用是顾客下决心前必看的一项，一律默认展开。 */
  if(has('fees')){
    const feeBody=feeListHtml(master.fees);
    if(feeBody&&feeBody.replace(/<[^>]*>/g,'').trim())h+=`<details open class="info-sec"><summary>费用说明 <span>PRICE</span></summary>${feeBody}</details>`;
  }
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
