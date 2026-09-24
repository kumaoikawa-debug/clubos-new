/* 平台独占的大模型接入：总平台配置 Provider / 模型 / 密钥，折算 AI Credits 后供各端俱乐部按任务消耗。 */
(function(){'use strict';if(!location.pathname.startsWith('/platform'))return;
 const escHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const patch=(p,d)=>api(p,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
 const post=(p,d)=>api(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d||{})});
 const badge=v=>v?'<span class="tag">已配置</span>':'<span class="tag orange">未配置</span>';

 async function renderProviders(){
   const box=document.getElementById('aiProviderPanel');if(!box)return;
   let d;try{d=await api('/api/platform/ai/providers');}catch(e){box.innerHTML='<div class="empty">无法读取模型接入配置：'+escHtml(e.message)+'</div>';return;}
   const st=d.effective||{},r=st.routing||{},pol=st.policy||{};
   const mode=st.mode==='mock'?'<span class="tag orange">mock 演示模式 · 未真实调用模型</span>':'<span class="tag">live · 真实调用</span>';
   const src=st.configSource==='platform'?'总平台后台配置':'环境变量配置';
   const vision=r.vision?escHtml(r.vision):'<span class="tag orange">未启用视觉模型</span>';
   const rows=(st.providers||[]).map(p=>`<div style="padding:10px 0;border-bottom:1px solid var(--line)"><strong>${escHtml(p.label||p.preset)}</strong> <span class="tag">${p.role==='primary'?'主用':'备用'}</span> ${badge(p.configured)}<div class="sub">文本模型 ${escHtml(p.defaultModel||'—')} · 视觉模型 ${escHtml(p.visionModel||'—')} · 密钥来源 ${escHtml(p.keySource)}</div><div class="sub">${escHtml(p.baseUrl||'')}</div></div>`).join('')||'<div class="empty">尚未配置任何模型 Provider</div>';
   box.innerHTML=`<div class="notice" style="margin-bottom:12px"><b>接入策略：</b>大模型只由总平台接入并持有密钥 → 平台把调用成本折算为 AI Credits 发放给俱乐部 → 各端俱乐部按任务消耗 Credits 使用。俱乐部${pol.clubCanConfigureModel===false?'<b>没有</b>':'有'}模型配置入口，也看不到任何密钥。</div>`
     +`<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px">${mode}<span class="tag">${escHtml(src)}</span><span class="tag">容灾切换 ${st.failoverEnabled?'开启':'关闭'}</span></div>`
     +`<div class="sub" style="margin-bottom:12px">文本任务 → <b>${escHtml(r.text||'未配置')}</b> · 图片理解任务 → <b>${vision}</b></div>`
     +rows
     +`<div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap"><button class="btn" type="button" onclick="editAiProviders()">配置模型接入</button><button class="btn secondary" type="button" onclick="testAiProvider()">连通性自检</button><button class="btn ghost" type="button" onclick="loadAiProviders()">刷新</button></div>`;
 }
 window.loadAiProviders=renderProviders;

 window.editAiProviders=()=>uxTask(async()=>{
   const d=await api('/api/platform/ai/providers');
   const opts=(d.presets||[]).map(x=>({value:x.code,label:x.label+(x.supportsVision?'（支持视觉）':'（仅文本）')}));
   const sp=(d.saved&&d.saved.primary)||{},ss=(d.saved&&d.saved.secondary)||{};
   const f=await uxForm({title:'AI 模型接入（总平台独占）',subtitle:'只有总平台能配置模型；俱乐部只按任务消耗 AI Credits。',hint:'API Key 留空表示沿用已保存的密钥或服务器环境变量；接口不会把完整密钥返回给浏览器。',wide:true,fields:[
     {name:'primaryPreset',label:'主用 Provider',type:'select',required:true,value:sp.preset||'deepseek',options:opts},
     {name:'primaryModel',label:'主用文本模型',required:true,value:sp.model||'deepseek-chat',placeholder:'如 deepseek-chat'},
     {name:'primaryVisionModel',label:'主用视觉模型（可留空）',value:sp.visionModel||'',placeholder:'留空 = 主用不处理图片任务'},
     {name:'primaryApiKey',label:'主用 API Key（留空 = 不修改）',full:true,value:'',placeholder:sp.apiKeyMasked?('已保存：'+sp.apiKeyMasked):'未配置，请填入'},
     {name:'secondaryPreset',label:'备用 Provider',type:'select',required:true,value:ss.preset||'qwen',options:opts},
     {name:'secondaryModel',label:'备用文本模型',required:true,value:ss.model||'qwen-plus',placeholder:'如 qwen-plus'},
     {name:'secondaryVisionModel',label:'备用视觉模型（推荐 qwen-vl-max）',value:ss.visionModel||'qwen-vl-max',placeholder:'如 qwen-vl-max'},
     {name:'secondaryApiKey',label:'备用 API Key（留空 = 不修改）',full:true,value:'',placeholder:ss.apiKeyMasked?('已保存：'+ss.apiKeyMasked):'未配置，请填入'},
     {name:'allowFailover',label:'主用失败时是否切换备用',type:'select',required:true,full:true,value:(d.saved&&d.saved.allowFailover===false)?'no':'yes',options:[{value:'yes',label:'允许（推荐）：主用异常时用备用模型重试'},{value:'no',label:'不允许：只使用主用 Provider'}]}
   ],submitText:'保存接入配置'});
   if(!f)return;
   const payload={primary:{preset:f.primaryPreset,model:f.primaryModel,visionModel:f.primaryVisionModel,enabled:true},secondary:{preset:f.secondaryPreset,model:f.secondaryModel,visionModel:f.secondaryVisionModel,enabled:true},allowFailover:f.allowFailover==='yes'};
   if(f.primaryApiKey&&f.primaryApiKey.trim())payload.primary.apiKey=f.primaryApiKey.trim();
   if(f.secondaryApiKey&&f.secondaryApiKey.trim())payload.secondary.apiKey=f.secondaryApiKey.trim();
   await patch('/api/platform/ai/providers',payload);
   toast('模型接入配置已保存');renderProviders();
 });

 window.testAiProvider=()=>uxTask(async()=>{
   const f=await uxForm({title:'连通性自检',subtitle:'向所选 Provider 真实发送一次最小请求，验证密钥与端点是否可用。',fields:[{name:'role',label:'要自检的 Provider',type:'select',required:true,full:true,value:'primary',options:[{value:'primary',label:'主用 Provider'},{value:'secondary',label:'备用 Provider'}]}],submitText:'开始自检'});
   if(!f)return;
   const r=await post('/api/platform/ai/providers/test',{role:f.role});
   if(r.ok)toast('连通正常 · '+r.preset+' / '+r.model);else toast('自检未通过：'+(r.error||'未知错误'));
   renderProviders();
 });

 function injectProviderCard(){const host=document.getElementById('pcredits');if(!host||document.getElementById('aiProviderPanel'))return;const card=document.createElement('div');card.className='card section';card.innerHTML='<div class="panel-title"><div><h3>AI 模型接入（总平台独占）</h3><div class="sub">总平台统一接入 DeepSeek / 通义千问，折算为 AI Credits 后供各俱乐部按任务消耗。</div></div></div><div id="aiProviderPanel"></div>';host.appendChild(card)}
 document.addEventListener('DOMContentLoaded',injectProviderCard);

 const originalLoadCredits=window.loadCredits;
 if(typeof originalLoadCredits==='function')window.loadCredits=async function(){await originalLoadCredits();renderProviders()};
})();
