/* 平台独占的大模型接入：总平台配置 Provider / 模型 / 密钥 / 运行模式，折算积分后供各端俱乐部按任务消耗。 */
(function(){'use strict';if(!location.pathname.startsWith('/platform'))return;
 const escHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const patch=(p,d)=>api(p,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
 const post=(p,d)=>api(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d||{})});
 const badge=v=>v?'<span class="tag">已配置</span>':'<span class="tag orange">未配置</span>';
 const sourceLabel=s=>s==='platform'?'总平台后台配置':'服务器环境变量';
 const CLUB_CREDITS_PATH='/club#credits';
 let lastProviderTest=null;

 const presetMap=d=>{const m={};(d.presets||[]).forEach(p=>{m[p.code]=p});return m};
 const isForeignModel=(presets,code,model)=>{const m=String(model||'').trim().toLowerCase();
   return !!(m && code && Object.keys(presets).some(k=>k!==code && String(presets[k].defaultModel||'').trim().toLowerCase()===m));};
 const isForeignEndpoint=(presets,code,url)=>{const u=String(url||'').trim().replace(/\/$/,'');
   return !!(u && code && Object.keys(presets).some(k=>k!==code && String(presets[k].baseUrl||'').trim().replace(/\/$/,'')===u));};
 const testResultHtml=t=>{
   if(!t)return '';
   const ok=!!t.ok;
   return `<div class="plat-ai-test ${ok?'is-ok':'is-bad'}"><div class="plat-ai-test__title">${ok?'✓ 连通正常':'✕ 自检未通过'}</div>`
     +`<div class="plat-ai-test__meta">服务商 ${escHtml(t.preset||'—')} · 模型 ${escHtml(t.model||'—')} · 端点 ${escHtml(t.baseUrl||'—')}</div>`
     +`<div class="plat-ai-test__msg">${escHtml(ok?('样例返回：'+(t.sample||'（空）')):(t.error||'未知错误'))}</div></div>`;
 };

 async function renderProviders(){
   const box=document.getElementById('aiProviderPanel');if(!box)return;
   let d;try{d=await api('/api/platform/ai/providers');}catch(e){box.innerHTML='<div class="empty">无法读取模型接入配置：'+escHtml(e.message)+'</div>';return;}
   const st=d.effective||{},r=st.routing||{},pol=st.policy||{};
   const live=st.mode!=='mock';
   const mode=live?'<span class="tag">live · 真实调用模型</span>':'<span class="tag orange">mock · 演示模式，不会真实调用</span>';
   const vision=r.vision?escHtml(r.vision):'<span class="tag orange">未启用视觉模型</span>';
   const savedMode=(d.saved&&d.saved.mode)||'auto';
   const rows=(st.providers||[]).map(p=>`<div style="padding:10px 0;border-bottom:1px solid var(--line)"><strong>${escHtml(p.label||p.preset)}</strong> <span class="tag">${p.role==='primary'?'主用':'备用'}</span> ${badge(p.configured)}<div class="sub">文本模型 ${escHtml(p.defaultModel||'—')} · 视觉模型 ${escHtml(p.visionModel||'—')} · 密钥来源 ${escHtml(p.keySource)}</div><div class="sub">${escHtml(p.baseUrl||'')}</div></div>`).join('')||'<div class="empty">尚未配置任何模型服务商</div>';
   const hint=live
     ? '<span class="tag">点击「启用真实调用」后，俱乐部生成会直接调用上方模型并计入积分</span>'
     : '填好密钥后点 <b>启用真实调用</b> 才会真正请求模型；在此之前保持 mock 演示不影响任何功能。';
   box.innerHTML=`<div class="notice" style="margin-bottom:12px"><b>接入策略：</b>大模型只由总平台接入并持有密钥 → 平台把调用成本折算为积分发放给俱乐部 → 各端俱乐部按任务消耗积分使用。俱乐部${pol.clubCanConfigureModel===false?'<b>没有</b>':'有'}模型配置入口，也看不到任何密钥。</div>`
     +`<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px">${mode}<span class="tag">模式来源：${escHtml(sourceLabel(st.modeSource))}</span><span class="tag">已保存：${escHtml(savedMode)}</span><span class="tag">容灾切换 ${st.failoverEnabled?'开启':'关闭'}</span>${st.modeLockedByProduction?'<span class="tag orange">生产模式：禁止切回 mock</span>':''}</div>`
     +`<div class="sub" style="margin-bottom:8px">文本任务 → <b>${escHtml(r.text||'未配置')}</b> · 图片理解任务 → <b>${vision}</b></div>`
     +`<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">`
     + (live?'':'<button class="btn" type="button" onclick="setAiMode(\'live\')">启用真实调用</button>')
     + (live&&!st.modeLockedByProduction?'<button class="btn ghost" type="button" onclick="setAiMode(\'mock\')">回到 mock 演示</button>':'')
     + (savedMode!=='auto'?'<button class="btn ghost" type="button" onclick="setAiMode(\'auto\')">跟随服务器环境变量</button>':'')
     +`</div><div class="sub" style="margin-bottom:6px">${hint}</div>`
     +rows
     +`<div id="aiProviderTestResult">${testResultHtml(lastProviderTest)}</div>`
     +`<div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap"><button class="btn" type="button" onclick="editAiProviders()">配置模型接入</button><button class="btn secondary" type="button" onclick="testAiProvider()">连通性自检</button><button class="btn ghost" type="button" onclick="loadAiProviders()">刷新</button></div>`;
   renderHandoff(d,st);
 }

 /* 给俱乐部的取用地址 + 一键发放：平台把地址发给俱乐部即可自助充值，也可平台直接发放。 */
 function renderHandoff(d,st){
   const host=document.getElementById('aiCreditHandoff');if(!host)return;
   const url=location.origin+CLUB_CREDITS_PATH;
   host.innerHTML=`<div class="notice" style="margin-bottom:10px"><b>俱乐部获取积分的地址（发给俱乐部即可）：</b><div class="sub" style="margin-top:4px"><code>${escHtml(url)}</code> <button class="btn ghost" type="button" onclick="copyClubCreditsUrl()">复制</button></div><div class="sub">俱乐部登录后进入「积分中心」，可查看余额 / 充值包 / 流水，自助下单后由总平台确认到账。</div></div>`
     +`<div class="sub">给俱乐部发放/扣回用「调整积分」按钮（选俱乐部 + 数量，可正可负）· 俱乐部自助下单后在本页账单上点「确认到账」即可。</div>`
     +`<div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap"><button class="btn secondary" type="button" onclick="dispatchAiCredits()">给俱乐部分配积分</button><button class="btn ghost" type="button" onclick="monthlyRoll()">生成本月套餐账单</button></div>`;
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
   const presets=presetMap(d);
   const opts=(d.presets||[]).map(x=>({value:x.code,label:x.label+(x.supportsVision?'（支持视觉）':'（仅文本）')}));
   const sp=(d.saved&&d.saved.primary)||{},ss=(d.saved&&d.saved.secondary)||{};
   const savedMode=(d.saved&&d.saved.mode)||'auto';
   const initial=(spec,fallback)=>{
     const code=spec.preset||fallback,meta=presets[code]||{},fixed=[];
     let model=spec.model||meta.defaultModel||'';
     let vision=spec.visionModel||meta.visionModel||'';
     let url=spec.baseUrl||meta.baseUrl||'';
     if(isForeignModel(presets,code,model)){model=meta.defaultModel||'';fixed.push('文本模型')}
     if(isForeignModel(presets,code,vision)){vision=meta.visionModel||'';fixed.push('视觉模型')}
     if(isForeignEndpoint(presets,code,url)){url=meta.baseUrl||'';fixed.push('接口地址')}
     return {code,model,vision,url,fixed};
   };
   const P=initial(sp,'deepseek'),S=initial(ss,'qwen');
   const fixedText=[...P.fixed.map(x=>'主用'+x),...S.fixed.map(x=>'备用'+x)];
   const block=(role,title,x,mask)=>`<section class="plat-ai-block"><h4>${escHtml(title)}</h4>`
     +`<div class="plat-ai-grid">`
     +`<label class="plat-ai-field"><span>服务商 <b>*</b></span><select id="ai_${role}_preset" class="plat-ai-input">`
       +opts.map(o=>`<option value="${escHtml(o.value)}"${o.value===x.code?' selected':''}>${escHtml(o.label)}</option>`).join('')
     +`</select><small>切换后会自动填入该服务商推荐模型与接口地址。</small></label>`
     +`<label class="plat-ai-field"><span>文本模型 <b>*</b></span><input id="ai_${role}_model" class="plat-ai-input" value="${escHtml(x.model)}" placeholder="按服务商自动填入，可修改"><small>必须是所选服务商支持的模型名。</small></label>`
     +`<label class="plat-ai-field"><span>视觉模型（图片任务用）</span><input id="ai_${role}_vision" class="plat-ai-input" value="${escHtml(x.vision)}" placeholder="不支持视觉可留空"><small>带图生成会走这个模型；可留空。</small></label>`
     +`<label class="plat-ai-field"><span>接口地址（OpenAI 兼容完整 URL） <b>*</b></span><input id="ai_${role}_url" class="plat-ai-input" value="${escHtml(x.url)}" placeholder="https://.../v1/chat/completions"><small>切服务商会自动填；自定义端点必须自己填完整地址。</small></label>`
     +`<label class="plat-ai-field plat-ai-field--full"><span>API Key（留空 = 不修改）</span><input id="ai_${role}_key" class="plat-ai-input" value="" placeholder="${escHtml(mask?('已保存：'+mask):'未配置，请填入')}" autocomplete="off"><small>页面永远只显示掩码，保存后不再回显完整密钥。</small></label>`
     +`</div><div id="ai_${role}_hint" class="plat-ai-hint"></div></section>`;
   const body=`<div class="plat-ai-form">`
     +`<div class="plat-ai-tip"><b>怎么填：</b>先选服务商，系统会自动填好配套模型与接口地址；只需要在需要时改 API Key。`
       +(fixedText.length?` <span class="plat-ai-warn">检测到${escHtml(fixedText.join(' / '))}与服务商不匹配，已自动替换为推荐值，确认后保存即可。</span>`:'')
     +`</div>`
     +`<div class="plat-ai-grid">`
       +`<label class="plat-ai-field"><span>运行模式 <b>*</b></span><select id="ai_mode" class="plat-ai-input">`
         +`<option value="live"${savedMode==='live'?' selected':''}>真实调用（已填密钥就用这个）</option>`
         +`<option value="auto"${savedMode==='auto'?' selected':''}>跟随服务器环境变量</option>`
         +`<option value="mock"${savedMode==='mock'?' selected':''}>mock 演示（不请求模型，仅本地演示）</option>`
       +`</select><small>填了 Key 后选「真实调用」立刻生效，不需要重启。</small></label>`
       +`<label class="plat-ai-field"><span>主用失败时是否切换备用 <b>*</b></span><select id="ai_failover" class="plat-ai-input">`
         +`<option value="yes"${d.saved&&d.saved.allowFailover===false?'':' selected'}>允许（推荐）</option>`
         +`<option value="no"${d.saved&&d.saved.allowFailover===false?' selected':''}>不允许，只用主用</option>`
       +`</select><small>备用需要单独填 Key，否则不会生效。</small></label>`
     +`</div>`
     +block('primary','主用服务商',P,sp.apiKeyMasked)
     +block('secondary','备用服务商',S,ss.apiKeyMasked)
     +`<div id="aiFormError" class="plat-ai-error" hidden></div></div>`;
   const ov=uxDialog({title:'模型接入（总平台独占）',desc:'只有总平台配置模型；俱乐部只按任务消耗积分。',wide:true,body,
     foot:'<button class="btn ghost" type="button" data-cancel>取消</button><button class="btn" type="button" data-save>保存接入配置</button>',initialFocus:'#ai_mode'});
   const f=id=>ov.querySelector('#'+id);
   const applyPreset=(role,code,force)=>{
     const meta=presets[code];if(!meta)return;
     const model=f('ai_'+role+'_model'),vision=f('ai_'+role+'_vision'),url=f('ai_'+role+'_url');
     if(force||isForeignModel(presets,code,model.value)||!model.value.trim())model.value=meta.defaultModel||'';
     if(force||isForeignModel(presets,code,vision.value)||!vision.value.trim())vision.value=meta.visionModel||'';
     if(force||isForeignEndpoint(presets,code,url.value)||!url.value.trim())url.value=meta.baseUrl||'';
     const hint=f('ai_'+role+'_hint');
     if(hint)hint.textContent='已按「'+meta.label+'」填入推荐值：文本 '+meta.defaultModel+(meta.visionModel?' · 视觉 '+meta.visionModel:'')+' · 端点 '+(meta.baseUrl||'自定义');
   };
   ['primary','secondary'].forEach(role=>f('ai_'+role+'_preset').addEventListener('change',e=>applyPreset(role,e.target.value,true)));
   ov.querySelector('[data-cancel]').onclick=()=>ov.uxClose();
   ov.querySelector('[data-save]').onclick=async btn=>{
     const errs=[],payload={mode:f('ai_mode').value,allowFailover:f('ai_failover').value==='yes',primary:{enabled:true},secondary:{enabled:true}};
     ['primary','secondary'].forEach(role=>{
       const code=f('ai_'+role+'_preset').value,model=f('ai_'+role+'_model').value.trim();
       const vision=f('ai_'+role+'_vision').value.trim(),url=f('ai_'+role+'_url').value.trim();
       const key=f('ai_'+role+'_key').value.trim(),label=role==='primary'?'主用':'备用';
       if(!model)errs.push(label+'文本模型不能为空');
       if(isForeignModel(presets,code,model))errs.push(label+'文本模型与服务商不匹配，请重新选择服务商或改成推荐模型');
       if(!/^https?:\/\//i.test(url))errs.push(label+'接口地址必须以 http(s):// 开头');
       if(code==='custom'&&!url)errs.push('自定义服务商必须填写接口地址');
       Object.assign(payload[role],{preset:code,model,visionModel:vision,baseUrl:url});
       if(key)payload[role].apiKey=key;
     });
     const errBox=f('aiFormError');
     if(errs.length){errBox.hidden=false;errBox.innerHTML=errs.map(x=>'• '+escHtml(x)).join('<br>');return}
     errBox.hidden=true;
     await patch('/api/platform/ai/providers',payload);
     ov.uxClose();
     toast(payload.mode==='live'?'模型接入配置已保存，真实调用已开启':'模型接入配置已保存');
     renderProviders();
   };
 });

 window.testAiProvider=()=>uxTask(async()=>{
   const f=await uxForm({title:'连通性自检',subtitle:'向所选服务商真实发送一次最小请求，验证密钥、模型与端点是否配套。',hint:'自检结果会保留在「模型接入」卡片里；失败时请按提示回到配置弹窗检查服务商与模型。',fields:[{name:'role',label:'要自检的服务商',type:'select',required:true,full:true,value:(lastProviderTest&&lastProviderTest.role)||'primary',options:[{value:'primary',label:'主用服务商'},{value:'secondary',label:'备用服务商'}]}],submitText:'开始自检'});
   if(!f)return;
   const r=await post('/api/platform/ai/providers/test',{role:f.role});
   lastProviderTest=r;renderProviders();
   if(r.ok)toast('连通正常 · '+r.preset+' / '+r.model);
   else await showAlert({title:'自检未通过',message:(r.error||'未知错误')+'\n\n建议：点「配置模型接入」重新选择服务商，让系统自动填入配套模型与端点。'});
 });

 window.dispatchAiCredits=()=>uxTask(async()=>{
   const clubs=await api('/api/platform/clubs');
   if(!clubs.length){toast('还没有俱乐部，请先在「俱乐部入驻」里新增');return}
   const f=await uxForm({title:'给俱乐部分配积分',subtitle:'正数发放，负数扣回；余额不足的部分自动形成欠账，后续充值会优先偿还。',fields:[
     {name:'clubId',label:'俱乐部',type:'select',required:true,full:true,value:clubs[0].id,options:clubs.map(c=>({value:c.id,label:c.name+'（当前 '+c.ai_credits+' Credits'+(Number(c.ai_credit_debt||0)>0?'，欠账 '+c.ai_credit_debt:'')+'）'}))},
     {name:'amount',label:'分配数量（积分）',type:'number',step:1,required:true,value:1000},
     {name:'note',label:'说明',full:true,value:'平台发放积分'}
   ],submitText:'确认分配'});
   if(!f)return;
   const r=await post('/api/platform/credits/adjust',{clubId:Number(f.clubId),amount:Number(f.amount),note:f.note});
   toast('已发放 '+(r.granted||0)+' 积分'+(r.debtRecovered?'，其中 '+r.debtRecovered+' 用于偿还欠账':'')+(r.netToBalance?'，实际到账 '+r.netToBalance:''));
   renderProviders();loadCredits();loadClubs();dash();
 });

 function injectProviderCard(){
   const host=document.getElementById('ai-config');if(!host||document.getElementById('aiProviderPanel'))return;
   const card=document.createElement('div');card.className='card section';
   card.innerHTML='<div class="panel-title"><div><h3>模型接入（总平台独占）</h3><div class="sub">总平台统一接入 DeepSeek / 通义千问并控制运行模式，折算为积分后供各俱乐部按任务消耗。</div></div></div><div id="aiProviderPanel"></div>';
   const handoff=document.createElement('div');handoff.className='card section';
   handoff.innerHTML='<div class="panel-title"><div><h3>俱乐部积分发放与获取</h3><div class="sub">把下方地址发给俱乐部即可自助充值；平台也可以直接发放。</div></div></div><div id="aiCreditHandoff"></div>';
   const anchor=host.children[1]||null;
   host.insertBefore(card,anchor);host.insertBefore(handoff,anchor);
 }
 document.addEventListener('DOMContentLoaded',injectProviderCard);

 const originalLoadCredits=window.loadCredits;
 if(typeof originalLoadCredits==='function')window.loadCredits=async function(){await originalLoadCredits();injectProviderCard();renderProviders()};
})();
