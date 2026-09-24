/* 平台独占的大模型接入：总平台配置 Provider / 模型 / 密钥 / 运行模式，折算 AI Credits 后供各端俱乐部按任务消耗。 */
(function(){'use strict';if(!location.pathname.startsWith('/platform'))return;
 const escHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const patch=(p,d)=>api(p,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
 const post=(p,d)=>api(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d||{})});
 const badge=v=>v?'<span class="tag">已配置</span>':'<span class="tag orange">未配置</span>';
 const sourceLabel=s=>s==='platform'?'总平台后台配置':'服务器环境变量';
 const CLUB_CREDITS_PATH='/club#credits';

 async function renderProviders(){
   const box=document.getElementById('aiProviderPanel');if(!box)return;
   let d;try{d=await api('/api/platform/ai/providers');}catch(e){box.innerHTML='<div class="empty">无法读取模型接入配置：'+escHtml(e.message)+'</div>';return;}
   const st=d.effective||{},r=st.routing||{},pol=st.policy||{};
   const live=st.mode!=='mock';
   const mode=live?'<span class="tag">live · 真实调用模型</span>':'<span class="tag orange">mock · 演示模式，不会真实调用</span>';
   const vision=r.vision?escHtml(r.vision):'<span class="tag orange">未启用视觉模型</span>';
   const savedMode=(d.saved&&d.saved.mode)||'auto';
   const rows=(st.providers||[]).map(p=>`<div style="padding:10px 0;border-bottom:1px solid var(--line)"><strong>${escHtml(p.label||p.preset)}</strong> <span class="tag">${p.role==='primary'?'主用':'备用'}</span> ${badge(p.configured)}<div class="sub">文本模型 ${escHtml(p.defaultModel||'—')} · 视觉模型 ${escHtml(p.visionModel||'—')} · 密钥来源 ${escHtml(p.keySource)}</div><div class="sub">${escHtml(p.baseUrl||'')}</div></div>`).join('')||'<div class="empty">尚未配置任何模型 Provider</div>';
   const hint=live
     ? '<span class="tag">点击「启用真实调用」后，俱乐部生成会直接调用上方模型并计入 Credits</span>'
     : '填好密钥后点 <b>启用真实调用</b> 才会真正请求模型；在此之前保持 mock 演示不影响任何功能。';
   box.innerHTML=`<div class="notice" style="margin-bottom:12px"><b>接入策略：</b>大模型只由总平台接入并持有密钥 → 平台把调用成本折算为 AI Credits 发放给俱乐部 → 各端俱乐部按任务消耗 Credits 使用。俱乐部${pol.clubCanConfigureModel===false?'<b>没有</b>':'有'}模型配置入口，也看不到任何密钥。</div>`
     +`<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px">${mode}<span class="tag">模式来源：${escHtml(sourceLabel(st.modeSource))}</span><span class="tag">已保存：${escHtml(savedMode)}</span><span class="tag">容灾切换 ${st.failoverEnabled?'开启':'关闭'}</span>${st.modeLockedByProduction?'<span class="tag orange">生产模式：禁止切回 mock</span>':''}</div>`
     +`<div class="sub" style="margin-bottom:8px">文本任务 → <b>${escHtml(r.text||'未配置')}</b> · 图片理解任务 → <b>${vision}</b></div>`
     +`<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">`
     + (live?'':'<button class="btn" type="button" onclick="setAiMode(\'live\')">启用真实调用</button>')
     + (live&&!st.modeLockedByProduction?'<button class="btn ghost" type="button" onclick="setAiMode(\'mock\')">回到 mock 演示</button>':'')
     + (savedMode!=='auto'?'<button class="btn ghost" type="button" onclick="setAiMode(\'auto\')">跟随服务器环境变量</button>':'')
     +`</div><div class="sub" style="margin-bottom:6px">${hint}</div>`
     +rows
     +`<div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap"><button class="btn" type="button" onclick="editAiProviders()">配置模型接入</button><button class="btn secondary" type="button" onclick="testAiProvider()">连通性自检</button><button class="btn ghost" type="button" onclick="loadAiProviders()">刷新</button></div>`;
   renderHandoff(d,st);
 }

 /* 给俱乐部的取用地址 + 一键发放：平台把地址发给俱乐部即可自助充值，也可平台直接发放。 */
 function renderHandoff(d,st){
   const host=document.getElementById('aiCreditHandoff');if(!host)return;
   const url=location.origin+CLUB_CREDITS_PATH;
   host.innerHTML=`<div class="notice" style="margin-bottom:10px"><b>俱乐部获取 AI 积分的地址（发给俱乐部即可）：</b><div class="sub" style="margin-top:4px"><code>${escHtml(url)}</code> <button class="btn ghost" type="button" onclick="copyClubCreditsUrl()">复制</button></div><div class="sub">俱乐部登录后进入「AI Credits」，可查看余额 / 充值包 / 流水，自助下单后由总平台确认到账。</div></div>`
     +`<div class="sub">平台发放接口 <code>POST /api/platform/credits/adjust</code>（选俱乐部 + 数量，可正可负）· 到账确认 <code>POST /api/platform/ai-credit/orders/{orderId}/confirm-paid</code></div>`
     +`<div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap"><button class="btn secondary" type="button" onclick="dispatchAiCredits()">给俱乐部分配 AI 积分</button><button class="btn ghost" type="button" onclick="monthlyRoll()">生成本月套餐账单</button></div>`;
 }
 window.copyClubCreditsUrl=()=>uxTask(async()=>{const url=location.origin+CLUB_CREDITS_PATH;try{await navigator.clipboard.writeText(url);toast('已复制：'+url)}catch(e){toast(url)}});
 window.loadAiProviders=renderProviders;

 window.setAiMode=mode=>uxTask(async()=>{
   const labels={live:'启用真实调用',mock:'回到 mock 演示',auto:'跟随服务器环境变量'};
   const warn=mode==='mock'?'切换后俱乐部生成将回到本地演示模板，不再请求真实模型。':'';
   if(!await uxConfirm({title:labels[mode]||'切换运行模式',message:(warn||'切换后立即对三端生效，无需重启服务。')+' 生产环境下该开关不接受 mock。',confirmText:labels[mode]||'确认'}))return;
   const r=await patch('/api/platform/ai/providers',{mode});
   const eff=(r.effective||{});
   toast('运行模式已切换：'+(eff.mode==='live'?'真实调用模型':'mock 演示')+'（来源 '+(eff.modeSource==='platform'?'总平台后台':'服务器环境变量')+'）');
   renderProviders();
 });

 window.editAiProviders=()=>uxTask(async()=>{
   const d=await api('/api/platform/ai/providers');
   const opts=(d.presets||[]).map(x=>({value:x.code,label:x.label+(x.supportsVision?'（支持视觉）':'（仅文本）')}));
   const sp=(d.saved&&d.saved.primary)||{},ss=(d.saved&&d.saved.secondary)||{};
   const savedMode=(d.saved&&d.saved.mode)||'auto';
   const f=await uxForm({title:'AI 模型接入（总平台独占）',subtitle:'只有总平台能配置模型；俱乐部只按任务消耗 AI Credits。',hint:'API Key 留空表示沿用已保存的密钥或服务器环境变量；接口不会把完整密钥返回给浏览器。填入密钥后请把运行模式选为「真实调用」，保存即生效，无需重启。',wide:true,fields:[
     {name:'mode',label:'运行模式',type:'select',required:true,full:true,value:savedMode,options:[{value:'live',label:'真实调用（已填密钥就用这个）'},{value:'auto',label:'跟随服务器环境变量'},{value:'mock',label:'mock 演示（不请求模型，仅本地演示）'}]},
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
   const payload={mode:f.mode,primary:{preset:f.primaryPreset,model:f.primaryModel,visionModel:f.primaryVisionModel,enabled:true},secondary:{preset:f.secondaryPreset,model:f.secondaryModel,visionModel:f.secondaryVisionModel,enabled:true},allowFailover:f.allowFailover==='yes'};
   if(f.primaryApiKey&&f.primaryApiKey.trim())payload.primary.apiKey=f.primaryApiKey.trim();
   if(f.secondaryApiKey&&f.secondaryApiKey.trim())payload.secondary.apiKey=f.secondaryApiKey.trim();
   await patch('/api/platform/ai/providers',payload);
   toast(f.mode==='live'?'模型接入配置已保存，真实调用已开启':'模型接入配置已保存');renderProviders();
 });

 window.testAiProvider=()=>uxTask(async()=>{
   const f=await uxForm({title:'连通性自检',subtitle:'向所选 Provider 真实发送一次最小请求，验证密钥与端点是否可用。',fields:[{name:'role',label:'要自检的 Provider',type:'select',required:true,full:true,value:'primary',options:[{value:'primary',label:'主用 Provider'},{value:'secondary',label:'备用 Provider'}]}],submitText:'开始自检'});
   if(!f)return;
   const r=await post('/api/platform/ai/providers/test',{role:f.role});
   if(r.ok)toast('连通正常 · '+r.preset+' / '+r.model);else toast('自检未通过：'+(r.error||'未知错误'));
   renderProviders();
 });

 window.dispatchAiCredits=()=>uxTask(async()=>{
   const clubs=await api('/api/platform/clubs');
   if(!clubs.length){toast('还没有俱乐部，请先在「俱乐部入驻」里新增');return}
   const f=await uxForm({title:'给俱乐部分配 AI 积分',subtitle:'正数发放，负数扣回；余额不足的部分自动形成欠账，后续充值会优先偿还。',fields:[
     {name:'clubId',label:'俱乐部',type:'select',required:true,full:true,value:clubs[0].id,options:clubs.map(c=>({value:c.id,label:c.name+'（当前 '+c.ai_credits+' Credits'+(Number(c.ai_credit_debt||0)>0?'，欠账 '+c.ai_credit_debt:'')+'）'}))},
     {name:'amount',label:'分配数量（Credits）',type:'number',step:1,required:true,value:1000},
     {name:'note',label:'说明',full:true,value:'平台发放 AI 积分'}
   ],submitText:'确认分配'});
   if(!f)return;
   const r=await post('/api/platform/credits/adjust',{clubId:Number(f.clubId),amount:Number(f.amount),note:f.note});
   toast('已发放 '+(r.granted||0)+' Credits'+(r.debtRecovered?'，其中 '+r.debtRecovered+' 用于偿还欠账':'')+(r.netToBalance?'，实际到账 '+r.netToBalance:''));
   renderProviders();loadCredits();loadClubs();dash();
 });

 function injectProviderCard(){
   const host=document.getElementById('pcredits');if(!host||document.getElementById('aiProviderPanel'))return;
   const card=document.createElement('div');card.className='card section';
   card.innerHTML='<div class="panel-title"><div><h3>AI 模型接入（总平台独占）</h3><div class="sub">总平台统一接入 DeepSeek / 通义千问并控制运行模式，折算为 AI Credits 后供各俱乐部按任务消耗。</div></div></div><div id="aiProviderPanel"></div>';
   const handoff=document.createElement('div');handoff.className='card section';
   handoff.innerHTML='<div class="panel-title"><div><h3>俱乐部 AI 积分发放与获取</h3><div class="sub">把下方地址发给俱乐部即可自助充值；平台也可以直接发放。</div></div></div><div id="aiCreditHandoff"></div>';
   const anchor=host.children[1]||null;
   host.insertBefore(card,anchor);host.insertBefore(handoff,anchor);
 }
 document.addEventListener('DOMContentLoaded',injectProviderCard);

 const originalLoadCredits=window.loadCredits;
 if(typeof originalLoadCredits==='function')window.loadCredits=async function(){await originalLoadCredits();injectProviderCard();renderProviders()};
})();
