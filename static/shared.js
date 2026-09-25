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
async function api(url,opt={}){
  const key=_writeKey(url,opt);
  if(key){
    const inflight=_writeInflight.get(key);
    if(inflight)return inflight;                                   // 在途 → 复用，不再发第二次
    const recent=_writeRecent.get(key);
    if(recent&&Date.now()-recent.at<WRITE_REPLAY_MS)return recent.value;  // 刚成功过 → 拦连击
  }
  const run=(async()=>{
    opt.headers=new Headers(opt.headers||{});
    if(!['GET','HEAD'].includes((opt.method||'GET').toUpperCase())){
      const csrf=clubosCookie('clubos_csrf');if(csrf)opt.headers.set('X-ClubOS-CSRF',decodeURIComponent(csrf));
    }
    const r=await fetch(url,{credentials:'same-origin',...opt});
    let d;try{d=await r.json()}catch{d={}}
    if(r.status===401 && location.pathname!='/login'){location.href='/login';throw new Error('请先登录')}
    if(!r.ok)throw new Error(d.detail||'请求失败');return d
  })();
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
function mediaMap(master){let m={};for(const x of master?.media||[])if(x?.ref)m[x.ref]=x;return m}
function mediaHtml(ref,map,cls=''){const x=map[ref];if(!x?.url)return `<div class="editorial-media missing ${cls}"><span>${esc(ref||'image')}</span></div>`;return `<figure class="editorial-media ${cls}"><img src="${esc(x.url)}" alt="" loading="lazy"></figure>`}
function renderPromo(detail,master={},opts={}){
  const mm=mediaMap(master);let h='<article class="editorial">';
  for(const b of detail?.blocks||[]){const refs=b.mediaRefs||[];
    if(b.type==='hero'){
      const bg=refs[0]&&mm[refs[0]]?.url?` style="background-image:linear-gradient(180deg,rgba(7,17,14,.10),rgba(7,17,14,.72)),url('${esc(mm[refs[0]].url)}')"`:'';
      h+=`<section class="ed-hero"${bg}><div class="ed-hero-copy"><div class="ed-kicker">${esc(b.kicker||master.location||'OUTDOOR EXPERIENCE')}</div><h1>${esc(b.headline||master.title||'活动')}</h1><p>${esc(b.subtitle||'')}</p></div></section>`;
    }else if(b.type==='lead')h+=`<section class="ed-lead"><p>${esc(b.text||b.body||'')}</p></section>`;
    else if(b.type==='statement')h+=`<section class="ed-statement"><span>${esc(b.text||'')}</span></section>`;
    else if(b.type==='facts')h+=`<section class="ed-facts">${(b.items||[]).map(x=>`<div><small>${esc(x.label||'')}</small><strong>${esc(x.value||x)}</strong></div>`).join('')}</section>`;
    else if(b.type==='narrative'){
      const media=refs.length?`<div class="ed-narrative-media">${refs.map(r=>mediaHtml(r,mm)).join('')}</div>`:'';
      h+=`<section class="ed-narrative ${refs.length?'has-media':''}"><div class="ed-copy">${b.eyebrow?`<div class="ed-kicker">${esc(b.eyebrow)}</div>`:''}<h2>${esc(b.headline||'')}</h2><p>${esc(b.body||b.text||'')}</p></div>${media}</section>`;
    }else if(b.type==='media'){
      const layout=b.layout==='mosaic'?'mosaic':refs.length===2?'pair':refs.length>=3?'grid':'single';
      h+=`<section class="ed-media ${layout}">${refs.map(r=>mediaHtml(r,mm)).join('')}${b.caption?`<p class="ed-caption">${esc(b.caption)}</p>`:''}</section>`;
    }else if(b.type==='gallery')h+=`<section class="ed-gallery count-${Math.min(refs.length,4)}">${refs.map(r=>mediaHtml(r,mm)).join('')}${b.caption?`<p class="ed-caption">${esc(b.caption)}</p>`:''}</section>`;
    else if(b.type==='timeline')h+=`<section class="ed-section ed-timeline"><div class="ed-section-head"><div class="ed-kicker">SCHEDULE</div><h2>${esc(b.title||'行程')}</h2></div><div class="timeline-list">${(b.items||[]).map(x=>`<div class="timeline-item"><time>${esc(x.time||'')}</time><p>${esc(x.text||x.content||'')}</p></div>`).join('')}</div></section>`;
    else if(b.type==='info')h+=`<section class="ed-section ed-info"><div class="ed-section-head"><div class="ed-kicker">GOOD TO KNOW</div><h2>${esc(b.title||'出发前知道')}</h2></div><div class="info-chips">${(b.items||[]).map(x=>`<div>${esc(x)}</div>`).join('')}</div></section>`;
    else if(b.type==='quote')h+=`<section class="ed-quote">“${esc(b.text||'')}”</section>`;
    else if(b.type==='divider')h+='<div class="ed-divider"></div>';
    else if(b.type==='cta')h+=`<section class="ed-cta"><div><div class="ed-kicker">READY TO GO</div><h2>${esc(b.headline||'立即报名')}</h2><p>${esc(b.text||'')}</p></div>${opts.hideButton?'':`<button class="btn light">选择团期并报名</button>`}</section>`;
  }
  return h+'</article>';
}
function renderInfoStack(master){return `<div class="info-stack polished"><details open><summary>详细行程 <span>ITINERARY</span></summary><div class="detail-list">${(master.itinerary||[]).map(x=>`<div><b>${esc(x.time||'')}</b><p>${esc(x.content||x.text||'')}</p></div>`).join('')||'<p class="sub">以最终活动通知为准</p>'}</div></details><details><summary>费用说明 <span>PRICE</span></summary><div class="json-pretty">${esc(JSON.stringify(master.fees||{},null,2))}</div></details><details><summary>出行清单 <span>PACKING</span></summary><div class="info-chips">${(master.checklist||[]).map(x=>`<div>${esc(x)}</div>`).join('')||'<div>出发前由俱乐部通知</div>'}</div></details></div>`}

// Logout must revoke the session on the server, not just hide the current UI.
document.addEventListener('DOMContentLoaded',()=>{
  if(!clubosCookie('clubos_csrf')||location.pathname==='/login')return;
  const button=document.createElement('button');button.type='button';button.textContent='退出登录';
  button.className='btn ghost';button.style.cssText='position:fixed;right:10px;bottom:10px;z-index:9000;font-size:12px';
  button.onclick=async()=>{try{await api('/api/auth/logout',{method:'POST'})}finally{location.href='/login'}};
  document.body.appendChild(button);
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
    ov.querySelector('[data-ok]').onclick=()=>{ov.remove();fin(true)};
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
      ov.remove();fin(vals);
    };
  });
}
