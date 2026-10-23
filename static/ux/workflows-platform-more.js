/* Credits, approvals and after-sales forms. */
(function(){if(!location.pathname.startsWith('/platform'))return;
 const post=(p,d)=>api(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}),patch=(p,d)=>api(p,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
 window.clubStatus=async(id,status)=>uxTask(async()=>{let reason='';if(status==='disabled'||status==='rejected'){const d=await uxForm({title:status==='disabled'?'停用俱乐部':'拒绝入驻',subtitle:'此操作影响俱乐部新 AI 调用；历史经营记录不删除。',fields:[{name:'reason',label:'原因说明',type:'textarea',required:true,full:true}],submitText:'确认'+(status==='disabled'?'停用':'拒绝')});if(!d)return;reason=d.reason}else if(!await uxConfirm({title:'启用俱乐部',message:'请确认已经完成准入审核。启用后俱乐部可以按照额度和权限调用 AI。',confirmText:'确认启用'}))return;await patch('/api/platform/clubs/'+id+'/status',{status,reason});toast('俱乐部状态已更新');loadClubs();dash()});
 /* 分配套餐（2026-10-23 用户反馈「下拉选择要改成平铺三个套餐再选，交互更丝滑」）：
    旧版是一个 <select> —— 三个档位挤在一行文字里，看不出额度/价格/折扣差别，
    还得点开才比得出 Pro 和 Starter 差多少。
    现在用 uxDialog 自定义 body 做卡片平铺：每张卡摊开「名称 / 月额度 / 月费 /
    装备折扣 / 定位说明」，点一下即选中（描边+底色高亮），底部统一提交。
    当前订阅的套餐标「当前套餐」，运营一眼知道是维持还是真换。 */
 window.assignPlan=async id=>uxTask(async()=>{
   const ps=(await api('/api/platform/ai-credit/plans')).filter(x=>x.status==='active');
   if(!ps.length){toast('请先创建套餐');return}
   let currentCode='';
   try{const cs=await api('/api/platform/credits');const c=(cs||[]).find(x=>Number(x.club_id)===Number(id));currentCode=c&&c.plan_code?String(c.plan_code):''}catch(e){}
   const card=p=>{const rate=Number(p.gear_discount||1);const disc=(rate*10).toFixed(rate*10%1?1:0)+' 折';
     const fee=Number(p.monthly_fee)>0?money(p.monthly_fee):'定制报价';const desc=String(p.description||'').trim();
     return '<button type="button" class="plat-pick'+(p.code===currentCode?' is-current':'')+'" data-code="'+esc(p.code)+'">'
       +'<span class="plat-pick__head"><b>'+esc(p.name)+'</b>'+(p.code===currentCode?'<span class="tag">当前套餐</span>':'')+'</span>'
       +'<span class="plat-pick__credits">'+Number(p.monthly_credits||0).toLocaleString()+'<em>Credits / 月</em></span>'
       +'<span class="plat-pick__fee">'+fee+'<i>· 装备折扣 '+disc+'</i></span>'
       +(desc?'<span class="plat-pick__desc">'+esc(desc)+'</span>':'')+'</button>'};
   const dlg=uxDialog({title:'为俱乐部分配套餐',desc:'分配只生成待付款账单，付款确认后才发放 Credits。',
     body:'<div class="plat-pick-grid">'+ps.map(card).join('')+'</div>',wide:true,
     foot:'<button class="btn ghost" data-act="cancel">取消</button><button class="btn" data-act="ok" disabled>分配并生成账单</button>'});
   let picked='';
   const okBtn=dlg.querySelector('[data-act="ok"]');
   const grid=dlg.querySelector('.plat-pick-grid');
   const sync=()=>{grid.querySelectorAll('.plat-pick').forEach(b=>b.classList.toggle('is-on',b.dataset.code===picked));okBtn.disabled=!picked};
   grid.onclick=e=>{const b=e.target.closest('.plat-pick');if(!b)return;picked=b.dataset.code;sync()};
   dlg.querySelector('[data-act="cancel"]').onclick=()=>dlg.uxClose();
   okBtn.onclick=async()=>{if(!picked)return;okBtn.disabled=true;
     try{const r=await patch('/api/platform/clubs/'+id+'/plan',{planCode:picked,autoRenew:true});
       dlg.uxClose();toast('已分配 '+(r.subscription.plan_name||r.subscription.plan_code)+'，等待付款');loadClubs();loadCredits()}
     catch(e){okBtn.disabled=false;toast((e&&e.message)||'分配失败')}};
 });
 window.adjust=async id=>uxTask(async()=>{const d=await uxForm({title:'调整 AI Credits',subtitle:'正数发放，负数扣回；不足部分形成欠账，后续自动偿还。',fields:[{name:'amount',label:'调整 Credits（可负数）',type:'number',step:1,value:100,required:true,validate:v=>v===0?'调整数量不能为 0':''},{name:'note',label:'调整原因',type:'textarea',full:true,required:true,value:'平台手工调整'}],submitText:'记录调整流水'});if(!d)return;const r=await post('/api/platform/credits/adjust',{clubId:id,...d});toast(r.debtCreated?'已调整，新增欠账 '+r.debtCreated:'Credits已调整');loadCredits()});
 window.confirmAIBill=async id=>uxTask(async()=>{const d=await uxForm({title:'确认 Credits 账单已付款',subtitle:'仅当外部款项实际到账后执行；支付凭证可防重复入账。',fields:[{name:'paymentRef',label:'付款流水 / 凭证号',required:true,full:true,placeholder:'银行、微信或支付宝实际凭证号'}],submitText:'确认实际到账'});if(!d)return;const r=await post('/api/platform/ai-credit/orders/'+id+'/confirm-paid',d);toast('已发放 '+(r.grant?.granted||0)+' Credits'+(r.grant?.debtRecovered?'；偿还欠账 '+r.grant.debtRecovered:''));loadCredits();dash()});
 /* 套餐增删改（2026-10-23 用户反馈「套餐不能增删，操作很不方便」）：
    旧版只有一个「＋/编辑套餐」按钮，点开永远是 starter 那套默认值 ——
    想加新档位得先手抄 code 想名称，想删一个根本做不到。
    现在：
    · editPlan()      —— 新增套餐（code 自填，已存在则更新）
    · editPlan(code)  —— 编辑指定套餐（表单预填真实值，不是 starter 默认值）
    · togglePlan()    —— 上架 / 下架（下架后不能再分配，历史账单与订阅保留）
    · deletePlan()    —— 真删除；仍被俱乐部订阅的套餐后端会拒绝并说明原因 */
 window.editPlan=async code=>uxTask(async()=>{
   let editing=null;
   if(code){const all=await api('/api/platform/ai-credit/plans');editing=(all||[]).find(x=>x.code===code)||null}
   const d=await uxForm({
     title:editing?('编辑套餐 · '+editing.name):'新增 AI Credits 套餐',
     subtitle:editing?'改这个档位的额度/价格/折扣，会立刻影响所有订阅它的俱乐部。':'新档位：Code 是内部标识（英文小写），填好后俱乐部就能在「分配套餐」里选到它。',
     fields:[
       {name:'code',label:'套餐 Code（英文小写）',required:true,full:true,value:editing?editing.code:'',help:editing?'Code 创建后不可修改。':'例如 starter / pro / enterprise'},
       {name:'name',label:'套餐名称',required:true,value:editing?editing.name:'',help:'展示给运营和俱乐部的名字，例如 Pro'},
       {name:'monthlyFee',label:'月费（元，0 = 定制）',type:'number',min:0,step:.01,value:editing?Number(editing.monthly_fee||0):299},
       {name:'monthlyCredits',label:'月度 Credits',type:'number',min:0,step:1,required:true,value:editing?Number(editing.monthly_credits||0):3000},
       {name:'description',label:'说明',type:'textarea',full:true,value:editing?(editing.description||''):'',help:'一句话说明这个档位适合谁，会显示在分配套餐的卡片上'},
       {name:'gearDiscount',label:'装备商城折扣（该套餐下俱乐部会员共用）',type:'select',full:true,value:String(editing?Number(editing.gear_discount||1):0.95),help:'装备折扣是平台资产：会员等级不参与，由俱乐部订阅的套餐决定。1=无折扣，0.95=九五折。',options:[{value:'1',label:'无折扣（按原价）'},{value:'0.95',label:'95 折'},{value:'0.9',label:'9 折'},{value:'0.85',label:'85 折'},{value:'0.8',label:'8 折'}]}
     ],submitText:editing?'保存套餐':'创建套餐'});
   if(!d||!String(d.code||'').trim())return;
   const cd=String(d.code).trim().toLowerCase();
   if(editing&&cd!==editing.code){toast('已有套餐的 Code 不可修改，请新建一个套餐');return}
   await post('/api/platform/ai-credit/plans',{code:cd,name:d.name||cd,monthlyFee:Number(d.monthlyFee||0),
     monthlyCredits:Number(d.monthlyCredits||0),gearDiscount:Number(d.gearDiscount||1),description:String(d.description||''),
     status:'active',sortOrder:editing?Number(editing.sort_order||100):100});
   toast(editing?'套餐已保存':'套餐已创建');loadCredits();
 });
 /* 上架 / 下架：下架后不能被新分配，历史订阅与账单完全保留（可随时上架回来）。 */
 window.togglePlan=async(code,status)=>uxTask(async()=>{
   const all=await api('/api/platform/ai-credit/plans');const p=(all||[]).find(x=>x.code===code);if(!p)return;
   await post('/api/platform/ai-credit/plans',{code:p.code,name:p.name,monthlyFee:Number(p.monthly_fee||0),
     monthlyCredits:Number(p.monthly_credits||0),gearDiscount:Number(p.gear_discount||1),description:p.description||'',
     status:status,sortOrder:Number(p.sort_order||100)});
   toast(status==='active'?'套餐已上架':'套餐已下架，俱乐部不能再分配它');loadCredits();
 });
 /* 删除套餐：仍被俱乐部订阅的套餐后端会拒绝（返回说明），这里如实转给运营。 */
 window.deletePlan=async code=>uxTask(async()=>{
   if(!await uxConfirm({title:'删除这个套餐？',message:'删除后不能恢复，也不能再给俱乐部分配它。若还有俱乐部在用这个套餐，系统会拦下来并告诉你。',confirmText:'删除套餐',danger:true}))return;
   try{await api('/api/platform/ai-credit/plans/'+encodeURIComponent(code),{method:'DELETE'});toast('套餐已删除');loadCredits()}
   catch(e){toast((e&&e.message)||'删除失败')}
 });
 window.editTopupPackage=async()=>uxTask(async()=>{const d=await uxForm({title:'编辑 Credits 充值包',fields:[{name:'code',label:'充值包 Code',required:true,value:'T1000'},{name:'name',label:'显示名称',required:true,value:'1,000 Credits'},{name:'amount',label:'售价（元）',type:'number',min:0,step:.01,required:true,value:99},{name:'credits',label:'Credits 数量',type:'number',min:1,step:1,required:true,value:1000}],submitText:'保存充值包'});if(!d)return;await post('/api/platform/ai-credit/topup-packages',{...d,status:'active'});toast('充值包已保存');loadCredits()});
 window.approveAS=async id=>uxTask(async()=>{const d=await uxForm({title:'审核通过售后 · #'+id,subtitle:'仅退款/退货退款可设置批准金额；换货无需填写金额。',fields:[{name:'refundAmount',label:'批准退款金额（元，可留空）',type:'number',min:0,step:.01,placeholder:'换货可留空'}],submitText:'审核同意'});if(!d)return;const body={};if(d.refundAmount!=='')body.refundAmount=d.refundAmount;await post('/api/platform/after-sales/'+id+'/approve',body);toast('售后已审核');loadAfterSales()});
 window.rejectAS=async id=>uxTask(async()=>{const d=await uxForm({title:'拒绝售后 · #'+id,fields:[{name:'note',label:'拒绝原因',type:'textarea',full:true,required:true,value:'不符合售后条件'}],submitText:'确认拒绝'});if(!d)return;await post('/api/platform/after-sales/'+id+'/reject',d);toast('已拒绝');loadAfterSales()});
})();
