/* ClubOS NEW / complete experience pass: UI-only, no authority or financial rules. */
(function(){
 'use strict';
 const isAdmin=/^\/(platform|club)(\/|$)/.test(location.pathname);
 const isWeb=/^\/web(\/|$)/.test(location.pathname);
 let currentView='', seq=0;
 const text=(s)=>String(s??'');
 function empty(message='没有符合条件的记录') {const el=document.createElement('div');el.className='ux-state ux-empty';el.innerHTML='<div aria-hidden="true" class="ux-state-icon">⌁</div><strong></strong><p>可调整筛选条件或稍后刷新。</p>';el.querySelector('strong').textContent=message;return el;}
 function enrichTable(table){
  if(table.dataset.uxEnhanced)return;
  const body=table.tBodies[0];if(!body)return;
  table.dataset.uxEnhanced='1';
  table.setAttribute('role','table');
  const shell=table.closest('.card')||table.parentNode;
  const filter=document.createElement('div');filter.className='ux-table-tools';
  filter.innerHTML='<label class="ux-table-search"><span aria-hidden="true">⌕</span><input type="search" placeholder="筛选当前列表" aria-label="筛选当前列表"></label><span class="ux-table-count" aria-live="polite"></span>';
  shell.insertBefore(filter,table);
  const input=filter.querySelector('input'),counter=filter.querySelector('.ux-table-count');
  let noResults=null;
  const update=()=>{const rows=[...body.rows].filter(x=>!x.classList.contains('ux-filter-empty'));let shown=0,actual=0;const q=input.value.trim().toLocaleLowerCase();for(const row of rows){if(row.querySelector('.empty')&&row.cells.length===1){row.hidden=!!q;continue}actual++;const visible=row.textContent.toLocaleLowerCase().includes(q);row.hidden=!visible;if(visible)shown++}if(noResults&&(!q||shown)){noResults.remove();noResults=null}if(actual&&q&&!shown&&!noResults){noResults=document.createElement('tr');noResults.className='ux-filter-empty';const cell=document.createElement('td');cell.colSpan=Math.max(1,table.tHead?.rows?.[0]?.cells?.length||1);cell.appendChild(empty('未找到匹配结果'));noResults.appendChild(cell);body.appendChild(noResults)}counter.textContent=q?`显示 ${shown} / ${actual} 条`:`共 ${actual} 条`};
  input.addEventListener('input',update);
  const obs=new MutationObserver(()=>update());obs.observe(body,{childList:true});update();
 }
 function enrichTables(){if(!isAdmin)return;document.querySelectorAll('.view table.table').forEach(enrichTable)}
 let queued=false;
 function scheduleTables(){if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;enrichTables()})}
 function initViewFeedback(){
  if(!isAdmin)return;
  const onView=window.onView;
  if(typeof onView!=='function')return;
  window.onView=async function(view){
   currentView=view;const token=++seq;const target=document.getElementById(view);if(!target)return onView(view);
   const header=target.querySelector('.panel-title')||target;
   let status=target.querySelector(':scope > .ux-view-feedback');
   if(!status){status=document.createElement('div');status.className='ux-view-feedback';status.setAttribute('role','status');status.setAttribute('aria-live','polite');header.after(status)}
   status.classList.remove('error');status.innerHTML='<span class="ux-spinner" aria-hidden="true"></span> 正在读取最新数据';target.setAttribute('aria-busy','true');
   try{await onView(view);if(token!==seq)return;status.textContent='数据已更新 · '+new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});target.removeAttribute('aria-busy');scheduleTables()}
   catch(err){if(token!==seq)return;target.removeAttribute('aria-busy');status.classList.add('error');status.replaceChildren();const msg=document.createElement('span');msg.textContent='读取失败：'+(err?.message||'网络异常');const retry=document.createElement('button');retry.type='button';retry.className='btn ghost';retry.textContent='重试';retry.onclick=()=>window.onView(view);status.append(msg,retry)}
  };
  const active=document.querySelector('.view.active');if(active){currentView=active.id;}
 }
 function initWeb(){
  if(!isWeb)return;
  const nav=document.querySelector('.web-nav');if(!nav)return;
  nav.querySelectorAll('button').forEach((b,i)=>{b.type='button';b.setAttribute('aria-label',['查看活动','浏览装备','查看会员','查看订单'][i]);b.setAttribute('aria-current',b.classList.contains('active')?'page':'false')});
  const native=window.wv;
  if(typeof native==='function')window.wv=function(id,b){native(id,b);nav.querySelectorAll('button').forEach(x=>x.setAttribute('aria-current',x===b?'page':'false'));window.scrollTo({top:0,behavior:'instant'})};
  const body=document.querySelector('.web-content');body?.addEventListener('click',e=>{const target=e.target.closest('.web-card[onclick]');if(target&&e.target.closest('button,a,input'))e.stopPropagation()});
 }
 function initCreate(){
  if(!isAdmin||!location.pathname.startsWith('/club'))return;
  const modal=document.getElementById('createModal'),form=document.getElementById('createForm');if(!modal||!form)return;
  const field=form.elements.namedItem('files'),textarea=form.elements.namedItem('prompt');
  if(!field||!textarea)return;
  const drop=field.closest('.drop');drop.classList.add('ux-file-drop');drop.setAttribute('role','group');drop.setAttribute('aria-label','活动素材');
  const help=document.createElement('div');help.className='ux-upload-help';help.innerHTML='<strong>支持的资料</strong><p>PPT / Word / PDF / 图片；可以上传多份资料，AI 会结合原始事实与图片组织动态内容。</p><div class="ux-upload-list" aria-live="polite">尚未选择文件</div>';
  drop.appendChild(help);
  const render=()=>{const files=[...field.files];help.querySelector('.ux-upload-list').replaceChildren();if(!files.length){help.querySelector('.ux-upload-list').textContent='尚未选择文件';return}for(const f of files){const el=document.createElement('div');el.className='ux-file-chip';el.textContent=f.name+' · '+(f.size/1024/1024).toFixed(1)+' MB';help.querySelector('.ux-upload-list').append(el)} };
  field.addEventListener('change',render);drop.addEventListener('dragover',e=>{e.preventDefault();drop.classList.add('dragover')});drop.addEventListener('dragleave',()=>drop.classList.remove('dragover'));drop.addEventListener('drop',e=>{e.preventDefault();drop.classList.remove('dragover');if(e.dataTransfer?.files?.length){field.files=e.dataTransfer.files;render()}});
  const notice=document.createElement('div');notice.className='ux-create-status';notice.setAttribute('aria-live','polite');textarea.parentElement.after(notice);
  const submit=document.getElementById('genBtn');const refresh=()=>{const ready=!!textarea.value.trim()||field.files.length>0;notice.textContent=ready?'资料已就绪。生成后可检查事实、编辑团期及发布。':'填写一句目标或上传一份资料，即可开始。';if(submit.disabled===ready)submit.disabled=!ready};textarea.addEventListener('input',refresh);field.addEventListener('change',refresh);refresh();
  form.addEventListener('submit',()=>{notice.innerHTML='<span class="ux-spinner" aria-hidden="true"></span>正在读取资料并生成活动内容。完成前请勿重复提交。';submit.disabled=true}, {capture:true});
  const obs=new MutationObserver(()=>{if(!submit.disabled)refresh()});obs.observe(submit,{attributes:true,attributeFilter:['disabled']});
  modal.setAttribute('role','dialog');modal.setAttribute('aria-modal','true');modal.setAttribute('aria-label','AI 创建活动');
 }
 document.addEventListener('DOMContentLoaded',()=>{initViewFeedback();enrichTables();initWeb();initCreate();if(isAdmin){const watcher=new MutationObserver(scheduleTables);document.querySelectorAll('.view').forEach(v=>watcher.observe(v,{childList:true,subtree:true}))}});
})();
