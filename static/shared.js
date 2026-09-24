const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
function clubosCookie(name){return document.cookie.split('; ').find(x=>x.startsWith(name+'='))?.split('=').slice(1).join('=')||''}
async function api(url,opt={}){
  opt.headers=new Headers(opt.headers||{});
  if(!['GET','HEAD'].includes((opt.method||'GET').toUpperCase())){
    const csrf=clubosCookie('clubos_csrf');if(csrf)opt.headers.set('X-ClubOS-CSRF',decodeURIComponent(csrf));
  }
  const r=await fetch(url,{credentials:'same-origin',...opt});
  let d;try{d=await r.json()}catch{d={}}
  if(r.status===401 && location.pathname!='/login'){location.href='/login';throw new Error('请先登录')}
  if(!r.ok)throw new Error(d.detail||'请求失败');return d
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
