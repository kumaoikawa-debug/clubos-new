/* ClubOS NEW UX · progressive enhancement for the current three frontends. */
(function(){
 const page=uxPage();
 const platformGroups=[['工作空间',['pdash']],['组织与资源',['clubs','pcredits','ppoints','pbenefits']],['商品与供应链',['products','supply','warehouse','analytics']],['交易与服务',['orders','aftersales','commissions']],['财务',['finance']]];
const clubGroups=[['工作空间',['dash','analytics']],['活动经营',['activities','content','regs','execution','points']],['客户与增长',['members','mall','biz']],['账户',['credits','payaccount']]];
const glyphs={pdash:'◫',clubs:'♧',pcredits:'◎',ppoints:'◈',pbenefits:'◇',products:'▤',supply:'⇄',warehouse:'▦',finance:'¥',analytics:'▥',orders:'▤',aftersales:'↺',commissions:'⇥',dash:'◫',activities:'⌁',content:'✦',regs:'▤',execution:'⤴',members:'♧',mall:'◇',credits:'◎',payaccount:'¥',points:'◈',biz:'▧'};
 const leafLabel={pdash:'平台概览',dash:'工作台',analytics:page==='platform'?'商城经营 / 补货':'经营驾驶舱'};
 const nav=document.querySelector('.nav'),bar=document.querySelector('.topbar');
 function roleName(){return page==='platform'?'总平台管理':page==='club'?'俱乐部经营':'用户端'}
 function enableNavigation(){
  if(!nav||!bar)return;
  const buttons=[...nav.querySelectorAll('button[data-view]')];const byId=new Map(buttons.map(b=>[b.dataset.view,b]));const groups=page==='platform'?platformGroups:clubGroups;
  nav.innerHTML='';const used=new Set();for(const [name,ids] of groups){const section=document.createElement('div');section.className='nav-group';const heading=document.createElement('div');heading.className='nav-group-label';heading.textContent=name;nav.appendChild(heading);for(const id of ids){const b=byId.get(id);if(!b)continue;used.add(id);const label=b.textContent.trim();b.innerHTML='<span class="nav-glyph" aria-hidden="true">'+(glyphs[id]||'·')+'</span><span class="nav-label"></span>';b.querySelector('.nav-label').textContent=label;b.setAttribute('aria-label',label);section.appendChild(b)}nav.appendChild(section)}
  /* 没登记进 group 白名单的入口过去会被静默丢掉 —— 页面加出来了，侧边栏里却找不到按钮。
     现在统一收到末尾的「其他入口」，漏登记从「页面不存在」变成「页面在但位置怪」。 */
  const rest=buttons.filter(b=>!used.has(b.dataset.view));
  if(rest.length){const more=document.createElement('div');more.className='nav-group';const heading=document.createElement('div');heading.className='nav-group-label';heading.textContent='其他入口';more.appendChild(heading);for(const b of rest){const label=b.textContent.trim();b.innerHTML='<span class="nav-glyph" aria-hidden="true">'+(glyphs[b.dataset.view]||'·')+'</span><span class="nav-label"></span>';b.querySelector('.nav-label').textContent=label;b.setAttribute('aria-label',label);more.appendChild(b)}nav.appendChild(more)}
  const sidebar=document.querySelector('.sidebar');if(sidebar){const brand=sidebar.querySelector('.brand');if(brand){brand.innerHTML='<span class="brand-symbol" aria-hidden="true">↗</span><span class="brand-copy">ClubOS NEW<small>'+roleName()+' · 独立工作空间</small></span>'}const foot=document.createElement('div');foot.className='sidebar-foot';foot.innerHTML='<strong>CLUBOS / WORKSPACE</strong>'+ (page==='platform'?'基础设施 · 不参与俱乐部日常经营':'您的活动、客户和会员独立经营');sidebar.appendChild(foot)}
  const workspace=document.createElement('div');workspace.className='workspace-bar';workspace.innerHTML='<div class="workspace-crumb"><button type="button" class="ux-mobile-toggle" aria-label="打开导航" aria-expanded="false">☰</button><span>'+roleName()+'</span><span aria-hidden="true">/</span><strong id="uxCurrentView">'+(buttons.find(b=>b.classList.contains('active'))?.textContent||'概览')+'</strong></div><div class="workspace-tools"><label class="workspace-search" title="搜索左侧模块，快捷键 /">⌕ <input id="uxNavSearch" type="search" placeholder="搜索功能入口" aria-label="搜索功能入口"><kbd>/</kbd></label><button type="button" class="btn ghost ux-logout" id="uxLogout" hidden>退出登录</button></div>';
  bar.parentNode.insertBefore(workspace,bar);
  const scrim=document.createElement('div');scrim.className='ux-drawer-scrim';scrim.setAttribute('aria-hidden','true');document.body.appendChild(scrim);
  const toggle=workspace.querySelector('.ux-mobile-toggle');const close=()=>{sidebar.classList.remove('ux-open');scrim.classList.remove('ux-open');toggle.setAttribute('aria-expanded','false')};toggle.onclick=()=>{const isOpen=sidebar.classList.toggle('ux-open');scrim.classList.toggle('ux-open',isOpen);toggle.setAttribute('aria-expanded',String(isOpen))};scrim.onclick=close;
  const search=workspace.querySelector('#uxNavSearch');search.addEventListener('input',()=>{let found=0;const needle=search.value.trim().toLowerCase();for(const b of buttons){const match=b.textContent.toLowerCase().includes(needle);b.hidden=!match;found+=match?1:0}for(const g of nav.querySelectorAll('.nav-group')){g.hidden=![...g.querySelectorAll('button')].some(b=>!b.hidden);g.previousElementSibling.hidden=g.hidden}nav.querySelector('.ux-nav-empty')?.remove();if(!found){const empty=document.createElement('div');empty.className='ux-nav-empty';empty.textContent='没有匹配的功能入口';nav.appendChild(empty)}});
  /* 有弹窗打开时 Esc 归弹窗层处理（shared.js 的 uxDialogSession 会在自己那一层
     stopPropagation）。这里只负责关闭移动端导航抽屉，别抢 Esc。 */
  const overlayOpen=()=>!!document.querySelector('.ux-overlay,.modal.show');
  document.addEventListener('keydown',e=>{const t=e.target;if(e.key==='Escape'){if(!overlayOpen()){close();search.blur()}}if(e.key==='/'&&!e.metaKey&&!e.ctrlKey&&!e.altKey&&!['INPUT','TEXTAREA','SELECT'].includes(t.tagName)&&!document.querySelector('.ux-overlay')){e.preventDefault();if(innerWidth<=650){toggle.click()}search.focus()}});
  for(const b of buttons){b.addEventListener('click',()=>{const label=b.querySelector('.nav-label')?.textContent||b.textContent;workspace.querySelector('#uxCurrentView').textContent=leafLabel[b.dataset.view]||label;buttons.forEach(x=>x.removeAttribute('aria-current'));b.setAttribute('aria-current','page');history.replaceState(null,'',location.pathname+location.search+'#'+b.dataset.view);close()});}
  const hash=location.hash.slice(1);if(hash&&byId.has(hash)){byId.get(hash).click()}else{const active=buttons.find(b=>b.classList.contains('active'));active?.setAttribute('aria-current','page')}
  const logout=document.getElementById('uxLogout');if(logout&&document.cookie.includes('clubos_csrf=')){logout.hidden=false;logout.onclick=async()=>{try{await api('/api/auth/logout',{method:'POST'})}finally{location.href='/login'}}}
  // v0.25 attached a floating logout. Replace it with the workspace action.
  document.querySelectorAll('body > button').forEach(b=>{if(b.textContent.trim()==='退出登录')b.remove()});
 }
 function enhanceWeb(){if(page!=='web')return;document.querySelectorAll('.web-nav button').forEach(b=>b.setAttribute('aria-label',b.textContent.trim()));const heading=document.querySelector('#wactivities .web-card h2');if(heading)heading.textContent='下一场冒险，从这里开始。';const webTop=document.querySelector('.web-top');webTop?.setAttribute('role','banner');document.querySelector('.web-nav')?.setAttribute('aria-label','用户主要导航');}
 document.addEventListener('DOMContentLoaded',()=>{enableNavigation();enhanceWeb()});
 // Validated, accessible business form. Data is sent by existing authenticated api() only.
 window.uxForm=function({title,subtitle='',fields=[],hint='',submitText='确认',wide=false,steps='',danger=false}){return new Promise(resolve=>{
  let settled=false;const teardown=()=>{el.remove();document.body.style.overflow=before;priorFocus?.focus?.()};
  /* hold=true 用于「提交」路径：弹窗不再同帧关闭，而是保留到紧随其后的写请求落定（与 shared.js 的
     showForm/showConfirm 同一套 _holdDialogUntilIdle 语义），用户才看得到"已提交"的反馈。 */
  const finish=(v,hold)=>{if(settled)return;settled=true;document.removeEventListener('keydown',key);if(hold){_holdDialogUntilIdle(el,el.querySelector('.ux-submit'),teardown)}else{teardown()}resolve(v)};
  const priorFocus=document.activeElement;const before=document.body.style.overflow;document.body.style.overflow='hidden';const el=document.createElement('div');el.className='ux-overlay';el.innerHTML='<div class="ux-dialog'+(wide?' wide':'')+'" role="dialog" aria-modal="true" aria-labelledby="uxDialogTitle"><div class="ux-dialog-head"><div>'+(steps?'<div class="ux-wizard-steps"><b>'+esc(steps)+'</b></div>':'<div class="eyebrow">CLUBOS / BUSINESS ACTION</div>')+'<h2 id="uxDialogTitle">'+esc(title)+'</h2>'+(subtitle?'<p>'+esc(subtitle)+'</p>':'')+'</div><button class="x" type="button" aria-label="关闭表单">×</button></div><form novalidate><div class="ux-dialog-body">'+(hint?'<div class="ux-help">'+esc(hint)+'</div>':'')+'<div class="ux-fields-2" id="uxFields"></div><div class="ux-inline-error" role="alert" hidden></div></div><div class="ux-dialog-foot"><button type="button" class="btn ghost ux-cancel">取消</button><button type="submit" class="btn ux-submit'+(danger?' danger':'')+'">'+esc(submitText)+'</button></div></form></div>';
  const grid=el.querySelector('#uxFields');fields.forEach((f,i)=>{const box=document.createElement('div');box.className='ux-field';if(f.full)box.style.gridColumn='1/-1';const id='uxf'+i;const label=document.createElement('label');label.htmlFor=id;label.textContent=f.label||f.name;if(f.required){const em=document.createElement('em');em.textContent=' *';label.appendChild(em)}box.appendChild(label);let inp;
  if(f.type==='select'){inp=document.createElement('select');for(const opt of f.options||[]){const o=document.createElement('option');o.value=String(opt.value);o.textContent=opt.label;inp.appendChild(o)}}
  else if(f.type==='textarea'){inp=document.createElement('textarea')}
  else if(f.type==='datetime'){
    /* 日期/时间点选（原生 date+time，可点击选择）＋ 常用时间胶囊 ＋ 实时自然语言预览。
       取值返回「YYYY-MM-DD HH:mm」，与后端团期 start_at 接受的文本格式一致。 */
    inp=document.createElement('div');inp.className='ux-datetime';
    const chips=(Array.isArray(f.timeChips)&&f.timeChips.length)?f.timeChips:['07:00','08:00','09:00','14:00','18:00','19:00'];
    inp.innerHTML='<div class="ux-dt-row"><input type="date" class="ux-dt-date"><input type="time" class="ux-dt-time"></div><div class="ux-dt-chips" role="group" aria-label="常用出发时间"></div><div class="ux-dt-preview" aria-live="polite"></div>';
    const dEl=inp.querySelector('.ux-dt-date'),tEl=inp.querySelector('.ux-dt-time'),pEl=inp.querySelector('.ux-dt-preview'),cBox=inp.querySelector('.ux-dt-chips');
    const fmt=()=>{const d=dEl.value,t=tEl.value;pEl.textContent='';if(!d||!t)return;const dt=new Date(d+'T'+t);if(isNaN(dt.getTime()))return;const wd=['周日','周一','周二','周三','周四','周五','周六'][dt.getDay()];pEl.textContent=dt.getFullYear()+'年'+(dt.getMonth()+1)+'月'+dt.getDate()+'日 '+wd+' '+t};
    for(const tt of chips){const b=document.createElement('button');b.type='button';b.className='ux-chip';b.textContent=tt;b.addEventListener('click',()=>{tEl.value=tt;cBox.querySelectorAll('.ux-chip').forEach(x=>x.classList.toggle('active',x===b));fmt()});cBox.appendChild(b)}
    dEl.addEventListener('input',fmt);tEl.addEventListener('input',fmt);
    const m=/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/.exec(f.value||'');
    if(m){dEl.value=m[1];tEl.value=m[2]}else{const n=new Date();dEl.value=n.getFullYear()+'-'+String(n.getMonth()+1).padStart(2,'0')+'-'+String(n.getDate()).padStart(2,'0');tEl.value=f.defaultTime||'08:00'}
    dEl.id=id;dEl.setAttribute('aria-label',(f.label||'日期')+' 日期');tEl.setAttribute('aria-label',(f.label||'时间')+' 时间');if(f.required){dEl.required=true;tEl.required=true}
    fmt();inp._collect=()=>{const d=dEl.value,t=tEl.value;return (d&&t)?(d+' '+t):''};inp._focus=dEl;f._el=inp;box.appendChild(inp);
  }
  else{inp=document.createElement('input');inp.type=f.type||'text';if(f.min!==undefined)inp.min=f.min;if(f.max!==undefined)inp.max=f.max;if(f.step!==undefined)inp.step=f.step}
  if(f.type!=='datetime'){
    inp.id=id;inp.name=f.name;if(f.value!==undefined&&f.value!==null)inp.value=f.value;if(f.placeholder)inp.placeholder=f.placeholder;if(f.required)inp.required=true;if(f.maxLength)inp.maxLength=f.maxLength;if(f.readonly)inp.readOnly=true;box.appendChild(inp);
  }
  if(f.help){const small=document.createElement('small');small.textContent=f.help;box.appendChild(small)}grid.appendChild(box)});
  const form=el.querySelector('form'),error=el.querySelector('.ux-inline-error');
  const invalid=(node,msg)=>{error.textContent=msg;error.hidden=false;node.setAttribute('aria-invalid','true');node.focus()};
  form.addEventListener('input',e=>{if(e.target instanceof HTMLElement)e.target.removeAttribute('aria-invalid');error.hidden=true});
  form.onsubmit=e=>{e.preventDefault();error.hidden=true;const data={};for(const f of fields){
   if(f.type==='datetime'){const v=f._el?f._el._collect():'';if(f.required&&!v){invalid((f._el&&f._el._focus)||f._el,'请选择「'+(f.label||f.name)+'」的日期和时间');return}data[f.name]=v;continue}
   const node=form.elements.namedItem(f.name);const raw=node.value.trim();if(f.required&&!raw){invalid(node,'请填写「'+f.label+'」');return}if(f.type==='number'&&raw){const n=Number(raw);if(!Number.isFinite(n)||(f.min!==undefined&&n<Number(f.min))||(f.max!==undefined&&n>Number(f.max))||node.validity.stepMismatch){invalid(node,'「'+f.label+'」不在允许范围内或不符合数量精度');return}data[f.name]=n}else data[f.name]=raw;}
   for(const f of fields){if(f.validate){const msg=f.validate(data[f.name],data);if(msg){invalid(form.elements.namedItem(f.name)||(f._el&&f._el._focus),msg);return}}}finish(data,true)};
  el.querySelector('.x').onclick=()=>finish(null);el.querySelector('.ux-cancel').onclick=()=>finish(null);el.onclick=e=>{if(e.target===el)finish(null)};const key=e=>{if(e.key==='Escape'){e.stopPropagation();finish(null)}if(e.key==='Tab'){const focusables=[...el.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled])')];if(!focusables.length)return;const first=focusables[0],last=focusables.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}};document.addEventListener('keydown',key);document.body.appendChild(el);el.querySelector('input,select,textarea,.x')?.focus();
 })};
 window.uxConfirm=async function({title='确认操作',message,confirmText='确认',danger=false}){const result=await uxForm({title,hint:message,fields:[],submitText:confirmText,danger});return result!==null};
 /* e.message 可能是对象（后端 detail 是数组/对象时），原样塞给 toast 会印出
    [object Object]（用户 2026-10-06 截图实证）。统一交给 toast 归一。 */
 window.uxTask=async function(fn){try{return await fn()}catch(e){toast((e&&e.message)||e||'操作未完成');return null}};
})();
