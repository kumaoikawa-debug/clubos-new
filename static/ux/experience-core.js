/* ClubOS NEW / complete experience pass: UI-only, no authority or financial rules. */
(function(){
 'use strict';
 // 一侧判据统一走 uxPage()：根路径 `/` 服务的也是俱乐部端，用正则匹配 pathname
 // 会把「从根域名打开」判成非管理端，表格增强/视图反馈那一整层在那边全部静默不生效。
 const isAdmin=uxPage()==='platform'||uxPage()==='club';
 const isWeb=uxPage()==='web';
 let currentView='', seq=0;
 const text=(s)=>String(s??'');
 function empty(message='没有符合条件的记录') {const el=document.createElement('div');el.className='ux-state ux-empty';el.innerHTML='<div aria-hidden="true" class="ux-state-icon">⌁</div><strong></strong><p>可调整筛选条件或稍后刷新。</p>';el.querySelector('strong').textContent=message;return el;}
 function enrichTable(table){
  if(table.dataset.uxEnhanced)return;
  const body=table.tBodies[0];if(!body)return;
  table.dataset.uxEnhanced='1';
  table.setAttribute('role','table');
  // table 外面可能还套着一层容器（如 club.js 的 <div style="overflow:auto">）：此时 table 不是
  // .card 的直接子节点，shell.insertBefore(filter,table) 会抛 NotFoundError，筛选条与计数条会
  // 一起丢失（且因为已置 uxEnhanced，之后不再重试）。改为插到「直接包含 table 的那层容器」之前，
  // 该容器必然在 shell 之内，位置也仍是卡片标题与表格之间。
  const host=table.parentNode,shell=table.closest('.card')||host;
  const filter=document.createElement('div');filter.className='ux-table-tools';
  filter.innerHTML='<label class="ux-table-search"><span aria-hidden="true">⌕</span><input type="search" placeholder="筛选当前列表" aria-label="筛选当前列表"></label><span class="ux-table-count" aria-live="polite"></span>';
  shell.insertBefore(filter,shell===host?table:host);
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
   const header=target.querySelector('.panel-title');
   /* 状态行要在整条视图里找，不能用 `:scope > .ux-view-feedback`：绝大多数视图的
      panel-title 在 .card 内部，插入的状态行因此是「卡片里」而非「视图直接子节点」，
      带 :scope 的查询永远找不到上一次那条 → 每进一次视图就多插一条「数据已更新」
      （实测切三次就并排三条）。改成全局查找 + 每次把节点移回标题之后（after 会移动节点）。 */
   let status=target.querySelector('.ux-view-feedback');
   if(!status){status=document.createElement('div');status.className='ux-view-feedback';status.setAttribute('role','status');status.setAttribute('aria-live','polite')}
   if(header)header.after(status);else target.prepend(status);
   status.classList.remove('error');status.innerHTML='<span class="ux-spinner" aria-hidden="true"></span> 正在读取最新数据';target.setAttribute('aria-busy','true');
   try{await onView(view);if(token!==seq)return;status.textContent='数据已更新 · '+new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});target.removeAttribute('aria-busy');scheduleTables()}
   catch(err){if(token!==seq)return;target.removeAttribute('aria-busy');status.classList.add('error');status.replaceChildren();const msg=document.createElement('span');msg.textContent='读取失败：'+(err?.message||'网络异常');const retry=document.createElement('button');retry.type='button';retry.className='btn ghost';retry.textContent='重试';retry.onclick=()=>window.onView(view);status.append(msg,retry)}
  };
  const active=document.querySelector('.view.active');if(active){currentView=active.id;}
 }
 function initWeb(){
  if(!isWeb)return;
  const nav=document.querySelector('.web-nav');if(!nav)return;
  /* aria-label 原来按 nav 下标记硬编码一个 4 元数组 —— 导航一旦不是 4 格，
     多出来的格子会把字面量 "undefined" 写进无障碍标签（读屏念出「未定义」）。
     改成按 data-wv 映射，与按钮数量解耦；没有 data-wv 时退回用可见文字。 */
  const NAV_LABELS={whome:'首页',wactivities:'活动',wmall:'装备',wability:'户外能力',wme:'我的'};
  nav.querySelectorAll('button').forEach(b=>{
    b.type='button';
    const key=NAV_LABELS[b.dataset.wv];
    b.setAttribute('aria-label',key||(b.textContent||'').trim()||'导航');
    b.setAttribute('aria-current',b.classList.contains('active')?'page':'false');
  });
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
