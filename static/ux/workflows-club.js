/* Club-facing primary actions use structured forms; all writes still use v0.25 protected APIs. */
(function(){if(uxPage()!=='club')return;
const post=(p,d)=>api(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}),patch=(p,d)=>api(p,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
window.quickAddOccurrence=async id=>uxTask(async()=>{const d=await uxForm({title:'为活动新增团期',subtitle:'点选出发日期与时间，可一键套用常用出发时间；显示名称留空会按日期自动生成，之后可随时修改。',fields:[{name:'startAt',label:'出发日期与时间',type:'datetime',required:true,full:true,value:'',timeChips:['07:00','08:00','09:00','13:00','14:00','19:00']},{name:'endAt',label:'结束日期与时间（多日行程选填）',type:'datetime',full:true,value:'',timeChips:['12:00','17:00','18:00','19:00','20:00'],help:'两天以上的行程填返程日期，如 10月1日～10月5日；单日团留空即可。',validate:(v,dd)=>(!v||v>dd.startAt)?'':'结束时间必须晚于出发时间（多日行程请检查日期）'},{name:'price',label:'单人价格（元）',type:'number',min:0,step:.01,required:true,value:498},{name:'capacity',label:'人数名额',type:'number',min:1,step:1,required:true,value:30},{name:'label',label:'团期显示名称（可选）',full:true,placeholder:'留空自动生成，例如：11月7日 周六团'}],submitText:'新增团期'});if(!d)return;if(!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(d.startAt)){toast('请选择出发日期和时间');return}const endAt=(d.endAt||'').trim();if(endAt){if(!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(endAt)){toast('结束日期和时间没有选完整');return}if(endAt<=d.startAt){toast('结束时间必须晚于出发时间（多日行程请检查日期）');return}}let label=(d.label||'').trim();if(!label){const m=/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec(d.startAt);const dt=new Date(d.startAt.replace(' ','T'));const wd=['周日','周一','周二','周三','周四','周五','周六'][dt.getDay()];label=Number(m[2])+'月'+Number(m[3])+'日 '+wd+'团';if(endAt){const me=/^(\d{4})-(\d{2})-(\d{2})/.exec(endAt);label=Number(m[2])+'月'+Number(m[3])+'日 ～ '+Number(me[2])+'月'+Number(me[3])+'日 '+wd+'团'}}await post('/api/club/'+CLUB+'/activities/'+id+'/occurrences',Object.assign({startAt:d.startAt,price:d.price,capacity:d.capacity,label},endAt?{endAt}:{}));toast('团期已添加');openActivity(id)});
/* 改团期：此前只有「添加团期」，没有任何地方能改已生成团期的时间与价格
   （首发团期是创建活动时自动生成的，老板想调档期/调价/调名额只能删掉活动重做，
   用户 2026-10-07 截图实证）。这里补上编辑入口，并且坚持在**同一个表单里**
   同时暴露时间 / 价格 / 名额 / 名称 / 报名状态 —— 改价格从来不是孤立动作。 */
window.editOccurrence=async(aid,oid)=>uxTask(async()=>{const a=await api('/api/club/'+CLUB+'/activities/'+aid);const o=(a.occurrences||[]).find(x=>Number(x.id)===Number(oid));if(!o){toast('这个团期已经不在了，可能刚被删除');return}const sold=Number(o.sold||0);const st=String(o.status||'open');const d=await uxForm({title:'修改团期',subtitle:'团期是报名、报价与结算的依据，改完立刻同步到 C 端。已有报名时名额不能少于已售人数。',fields:[{name:'startAt',label:'出发日期与时间',type:'datetime',required:true,full:true,value:o.start_at||'',timeChips:['07:00','08:00','09:00','13:00','14:00','19:00']},{name:'endAt',label:'结束日期与时间（多日行程选填）',type:'datetime',full:true,value:o.end_at||'',timeChips:['12:00','17:00','18:00','19:00','20:00'],help:'两天以上的行程填返程日期，如 10月1日～10月5日；单日团留空即可。'},{name:'price',label:'单人价格（元）',type:'number',min:0,step:.01,required:true,value:Number(o.price||0),help:'填 0 表示暂不公开价格（C 端显示「价格待定」，暂不可报名）；填了正价即视为正式对外定价。'},{name:'capacity',label:'人数名额',type:'number',min:Math.max(1,sold),step:1,required:true,value:Number(o.capacity||0),help:'已售 '+sold+' 人，名额不能少于这个数。'},{name:'label',label:'团期显示名称（可选）',type:'text',full:true,value:o.label||'',placeholder:'留空按日期自动生成，例如：11月7日 周六团'},{name:'status',label:'报名状态',type:'select',required:true,full:true,value:(st==='closed'?'closed':st==='cancelled'?'cancelled':'open'),options:[{value:'open',label:'开放报名（C 端可选此团期）'},{value:'closed',label:'停止报名（C 端不再展示此团期）'},{value:'cancelled',label:'已取消（不再计入对外价格）'}],help:'已有报名的团期请用「停止报名」，不要删除。'}],submitText:'保存团期'});if(!d)return;if(!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(String(d.startAt||''))){toast('请选择完整的出发日期和时间');return}const endAt=String(d.endAt||'').trim();if(endAt){if(!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(endAt)){toast('结束日期和时间没有选完整');return}if(endAt<=d.startAt){toast('结束时间必须晚于出发时间（多日行程请检查日期）');return}}if(Number(d.capacity)<sold){toast('名额不能少于已售的 '+sold+' 人');return}const changed=[];if(String(d.startAt)!==String(o.start_at||''))changed.push('出发时间');if(String(endAt)!==String(o.end_at||''))changed.push('返程时间');if(Number(d.price)!==Number(o.price||0))changed.push('价格');if(Number(d.capacity)!==Number(o.capacity||0))changed.push('名额');if(String(d.label||'').trim()!==String(o.label||'').trim())changed.push('显示名称');if(String(d.status)!==st)changed.push('报名状态');await patch('/api/club/'+CLUB+'/occurrences/'+oid,{startAt:d.startAt,endAt,price:d.price,capacity:d.capacity,label:d.label,status:d.status});openActivity(aid);occurrenceSavedBanner(changed)});
/* 删团期：后端有资金护栏（还有未取消报名就 409），前端也要先说清后果再动手。 */
window.deleteOccurrence=async(aid,oid)=>uxTask(async()=>{const a=await api('/api/club/'+CLUB+'/activities/'+aid);const o=(a.occurrences||[]).find(x=>Number(x.id)===Number(oid));const nm=o?(String(o.label||'').trim()||o.start_at||'该团期'):'该团期';const sold=Number(o&&o.sold||0);if(!await uxConfirm({title:'删除团期',message:'将删除「'+nm+'」（已售 '+sold+' 人）及其执行数据。已有未取消报名的团期无法删除，请改用「停止报名」。',confirmText:'删除团期',danger:true}))return;await api('/api/club/'+CLUB+'/occurrences/'+oid,{method:'DELETE'});toast('团期已删除');openActivity(aid)});
window.editExecutionSettings=async oid=>uxTask(async()=>{const data=await api('/api/club/'+CLUB+'/occurrences/'+oid+'/execution'),st=data.settings||data.executionSettings||{};const d=await uxForm({title:'集合与应急信息',subtitle:'保存后领队和执行人员查看的是同一份资料。',fields:[{name:'meetingTime',label:'集合时间',value:st.meeting_time||'07:50',required:true},{name:'meetingLocation',label:'集合地点',value:st.meeting_location||'',full:true,required:true},{name:'emergencyPhone',label:'应急联系电话',value:st.emergency_phone||'',required:true},{name:'leaderNote',label:'给领队的执行备注',type:'textarea',value:st.leader_note||'',full:true}],submitText:'保存集合信息'});if(!d)return;await patch('/api/club/'+CLUB+'/occurrences/'+oid+'/execution/settings',d);toast('执行信息已更新');openExecution(oid)});
/* 执行页领队：与俱乐部领队资源库（club_leaders）同源。
   此前这里是「姓名+手机号」手打表单 —— 同一个人每场活动打一遍，occurrence_leaders
   里躺着一堆对不上名册的野记录：C 端「带队领队」卡片拿不到头像/擅长/常驻城市，
   累计带队次数永远对不上，自动推荐也无从谈起（用户 2026-10-22 截图反馈：
   「应该跟俱乐部领队资源库同步，直接在里面选即可，也可以增加」）。
   现在改成从名册挑人（后端按 leaderId 回填姓名/电话/角色），名册没有时当场建档再排。 */
window.addExecutionLeader=async oid=>uxTask(async()=>{
  const [rosterRes,exec]=await Promise.all([
    api('/api/club/'+CLUB+'/leaders'),
    api('/api/club/'+CLUB+'/occurrences/'+oid+'/execution')]);
  const taken=new Set((exec.leaders||[]).map(x=>Number(x.leader_id||0)).filter(Boolean));
  const avail=(rosterRes.leaders||[]).filter(r=>r.status==='active'&&!taken.has(Number(r.id)));
  const ROLE_OPTS=[{value:'领队',label:'领队'},{value:'副领队',label:'副领队'},{value:'教练',label:'教练'},{value:'向导',label:'向导'},{value:'随队医护',label:'随队医护'},{value:'后勤',label:'后勤'}];
  const opts=avail.map(r=>({value:String(r.id),
    label:r.name+'（'+(r.role||'领队')+((r.specialties||[]).length?' · 擅长'+r.specialties.join('/'):'')+(r.base_city?' · 常驻'+r.base_city:'')+'）'}));
  opts.push({value:'__new__',label:'＋ 资源库里没有，新增一位领队…'});
  const d=await uxForm({title:'安排本团期领队',
    subtitle:avail.length?'从俱乐部领队资源库里挑人；姓名、电话以名册为准，同一个人的带队历史才能累计。':'资源库里暂无可安排的领队（在岗的都排过了或还没建档），可直接新增一位入册。',
    fields:[
      {name:'leaderId',label:'选择领队',type:'select',required:true,full:true,options:opts},
      {name:'role',label:'本团期角色',type:'select',required:true,full:true,value:'领队',options:ROLE_OPTS}],
    submitText:'安排到本团期'});
  if(!d)return;
  if(String(d.leaderId)==='__new__'){
    const nd=await uxForm({title:'新增领队到资源库',subtitle:'先建档入册，再排进这个团期。之后每场活动都能直接挑他。',
      fields:[{name:'name',label:'姓名',required:true},{name:'phone',label:'手机号',required:true},
              {name:'role',label:'名册身份',type:'select',required:true,value:d.role,options:ROLE_OPTS},
              {name:'baseCity',label:'常驻城市（可选）',full:true}],submitText:'建档并安排'});
    if(!nd)return;
    const created=await post('/api/club/'+CLUB+'/leaders',{name:nd.name,phone:nd.phone,role:nd.role,baseCity:(nd.baseCity||'').trim()||null,specialties:[]});
    await post('/api/club/'+CLUB+'/occurrences/'+oid+'/leaders',{leaderId:created.id,role:d.role});
    toast('新领队已入册，并安排到本团期');openExecution(oid);return;
  }
  await post('/api/club/'+CLUB+'/occurrences/'+oid+'/leaders',{leaderId:Number(d.leaderId),role:d.role});
  toast('领队已安排');openExecution(oid);
});
window.removeExecutionLeader=async(oid,aid)=>uxTask(async()=>{
  if(!await uxConfirm({title:'撤下领队',message:'确定把这名领队从本团期撤下来吗？撤下后不再计入其带队记录。'}))return;
  await api(`/api/club/${CLUB}/occurrences/${oid}/leaders/${aid}`,{method:'DELETE'});
  toast('已撤下');openExecution(oid);
});
window.addExecutionGroup=async oid=>uxTask(async()=>{const d=await uxForm({title:'添加活动分组',subtitle:'分车结果会同步到 C 端顾客的订单里（「出行指引 → 分车」），备注会一起展示。',fields:[{name:'groupType',label:'分组类型',required:true,type:'select',options:[{label:'车辆分组',value:'vehicle'},{label:'领队分组',value:'leader'},{label:'房间分组',value:'room'},{label:'自定义分组',value:'custom'}]},{name:'name',label:'分组名称',required:true,value:'1号车'},{name:'capacity',label:'容量（0 = 不限）',type:'number',min:0,step:1,value:20,required:true},{name:'note',label:'备注（可选）',full:true,placeholder:'如：车上发水、准时发车'}],submitText:'添加分组'});if(!d)return;await post('/api/club/'+CLUB+'/occurrences/'+oid+'/groups',{...d,capacity:d.capacity||null});toast('分组已创建');openExecution(oid)});
window.assignVehicle=async(oid,pid)=>uxTask(async()=>{const r=await api('/api/club/'+CLUB+'/occurrences/'+oid+'/execution'),gs=(r.groups||[]).filter(g=>g.group_type==='vehicle');if(!gs.length){toast('请先创建车辆分组');return}const d=await uxForm({title:'为参加人分配车辆',fields:[{name:'groupId',label:'选择车辆',type:'select',required:true,full:true,options:gs.map(g=>({value:g.id,label:g.name+' · '+(g.assigned_count||0)+'/'+(g.capacity||'不限')}))}],submitText:'确认分车'});if(!d)return;await post('/api/club/'+CLUB+'/execution-groups/'+d.groupId+'/assign',{participantId:pid});toast('已分配车辆');openExecution(oid)});
window.quickCheckin=async(oid,pid,current)=>uxTask(async()=>{const d=await uxForm({title:'更新参加人签到',fields:[{name:'status',label:'签到状态',type:'select',required:true,full:true,value:current||'pending',options:[{value:'checked_in',label:'已签到'},{value:'no_show',label:'未到场'},{value:'cancelled',label:'取消参加'},{value:'pending',label:'等待签到'}]}],submitText:'保存签到'});if(!d)return;await patch('/api/club/'+CLUB+'/occurrences/'+oid+'/participants/'+pid+'/checkin',d);toast('签到已更新');openExecution(oid)});
window.addExecutionNotice=async oid=>uxTask(async()=>{const d=await uxForm({title:'活动通知',subtitle:'点「标记发送」后，报了名的顾客会立刻在自己的订单「出行指引」里看到这条通知；短信 / 微信渠道后续接入后才会真正发到手机上。',fields:[{name:'title',label:'通知标题',full:true,required:true,value:'出发前集合通知'},{name:'content',label:'通知内容',type:'textarea',full:true,required:true,value:'请按时到达集合点，并携带活动所需装备。'},{name:'channel',label:'通知渠道',type:'select',required:true,options:[{value:'manual',label:'站内同步（订单内展示）'},{value:'sms',label:'短信（需渠道接入）'},{value:'wechat',label:'微信（需渠道接入）'}]}],submitText:'创建通知'});if(!d)return;await post('/api/club/'+CLUB+'/occurrences/'+oid+'/notices',{...d,audience:'all'});toast('通知已创建');openExecution(oid)});
window.addTier=async()=>uxTask(async()=>{const d=await uxForm({title:'创建会员等级',subtitle:'开通方式二选一：条件开通（消费/次数达标自动升级）或购买开通（顾客付费购买会员卡）。等级归属于当前俱乐部，不影响其他俱乐部会员。',fields:[{name:'name',label:'等级名称',required:true,value:'钻石会员'},{name:'rank',label:'等级顺序（越大越高）',type:'number',min:1,step:1,value:30,required:true},{name:'accessMode',label:'开通方式',type:'select',full:true,value:'CONDITION',options:[{value:'CONDITION',label:'条件开通（消费 / 次数达标自动升级）'},{value:'PURCHASE',label:'购买开通（顾客付费购买会员卡）'}]},{name:'price',label:'会员卡售价（元，仅购买开通生效）',type:'number',min:0,step:.01,value:399},{name:'validMonths',label:'会员卡有效期（月，仅购买开通生效）',type:'number',min:1,step:1,value:12},{name:'minActivitySpend',label:'累计活动消费（元，仅条件开通生效）',type:'number',min:0,step:1,value:5000,required:true},{name:'minActivityCount',label:'累计活动次数（仅条件开通生效）',type:'number',min:0,step:1,value:10,required:true}],submitText:'保存会员等级'});if(!d)return;await post('/api/club/'+CLUB+'/membership/tiers',{...d,qualificationMode:'ANY',benefits:['专属会员权益'],accessMode:d.accessMode||'CONDITION',price:Number(d.price||0),validMonths:Number(d.validMonths||12)});toast('等级已新增');loadMembers()});
/* 俱乐部营销 · 优惠券（2026-10-26）：满减 / 折扣 / 直抵三种形态，只能用于活动报名。 */
window.addClubBenefit=window.addClubCoupon=async()=>uxTask(async()=>{
  const d=await uxForm({title:'创建俱乐部优惠券',subtitle:'券只能用于本俱乐部活动报名，不能涉及装备商城；成本由本俱乐部承担。顾客可用活动积分兑换，也可由「消费送券」自动赠送。',
    fields:[
      {name:'title',label:'券名称',required:true,full:true,value:'活动满 200 减 30 券'},
      {name:'mode',label:'券形态',type:'select',full:true,required:true,value:'threshold',options:[{value:'threshold',label:'满减券（满指定金额立减）'},{value:'discount',label:'折扣券（订单打 N 折）'},{value:'fixed',label:'直抵券（无门槛直接抵）'}]},
      {name:'thresholdAmount',label:'满减门槛（元，仅满减券填写）',type:'number',min:0,step:.01,value:200},
      {name:'cashValue',label:'抵扣金额（元，仅满减/直抵券填写）',type:'number',min:0,step:.01,value:30},
      {name:'discountPercent',label:'折扣（填折后百分比：90 = 9 折，仅折扣券填写）',type:'number',min:1,max:99,step:1,value:90},
      {name:'pointsCost',label:'兑换所需活动积分',type:'number',min:1,step:1,value:300,required:true},
      {name:'stock',label:'券库存（消费送券要求已有库存）',type:'number',min:0,step:1,value:50,required:true},
      {name:'description',label:'使用说明',full:true,value:'本俱乐部活动报名专用'}
    ],submitText:'创建优惠券'});
  if(!d)return;
  const mode=String(d.mode||'threshold');
  await post('/api/club/'+CLUB+'/benefits',{title:d.title,benefitType:'activity_coupon',mode,
    thresholdAmount:mode==='threshold'?Number(d.thresholdAmount||0):0,
    cashValue:(mode==='discount')?0:Number(d.cashValue||0),
    discountPercent:(mode==='discount')?Number(d.discountPercent||0):0,
    pointsCost:Number(d.pointsCost||0),stock:d.stock===''?null:Number(d.stock),
    description:d.description,pointsType:'club',claimMode:'grant'});
  toast('优惠券已创建');loadMembers()});
/* 消费送券规则（2026-10-26）：单笔活动消费满 X 元自动送一张券（券须已有库存）。 */
window.addGiftRule=async()=>uxTask(async()=>{
  let benefits=[];try{benefits=await api(`/api/club/${CLUB}/benefits`)}catch(e){benefits=[]}
  const pool=(benefits||[]).filter(b=>b.owner_type==='CLUB'&&b.benefit_type==='activity_coupon'&&b.status==='active');
  if(!pool.length){showAlert({title:'先创建优惠券',message:'消费送券只能赠送本俱乐部已创建的活动优惠券，请先在「俱乐部营销 · 优惠券」里建券。'});return}
  const d=await uxForm({title:'新增消费送券规则',
    subtitle:'顾客单笔活动实付达到门槛后，自动把选中的券送进TA的券包。只能送满减券 / 折扣券 / 直抵券，且券必须有库存。',
    fields:[
      {name:'benefitId',label:'赠送哪张券',type:'select',required:true,full:true,
       options:pool.map(b=>({value:b.id,label:`${b.title} · ${String(b.mode||'fixed')==='discount'?(Number(b.discount_percent||0)/10)+' 折':(String(b.mode||'fixed')==='threshold'?`满${money(b.threshold_amount)}减${money(b.cash_value)}`:`直抵${money(b.cash_value)}`)} · 库存 ${b.stock==null?'不限':b.stock}`}))},
      {name:'thresholdAmount',label:'单笔消费满（元）',type:'number',min:.01,step:.01,value:500,required:true},
      {name:'perUserLimit',label:'每人最多被赠送（次）',type:'number',min:1,step:1,value:1,required:true}
    ],submitText:'创建规则'});
  if(!d)return;
  await post('/api/club/'+CLUB+'/coupon-gift-rules',{benefitId:Number(d.benefitId),thresholdAmount:Number(d.thresholdAmount),perUserLimit:Number(d.perUserLimit)});
  toast('送券规则已创建');loadMembers()});
window.toggleGiftRule=async(id,enabled)=>uxTask(async()=>{await api(`/api/club/${CLUB}/coupon-gift-rules/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!!enabled})});toast(enabled?'规则已启用':'规则已停用');loadMembers()});
window.delGiftRule=async id=>uxTask(async()=>{if(!await uxConfirm({title:'删除送券规则',message:'删除后，已赠送出去的券不受影响，只是不再自动赠送。'}))return;await api(`/api/club/${CLUB}/coupon-gift-rules/${id}`,{method:'DELETE'});toast('规则已删除');loadMembers()});
window.togglePurchaseEnabled=async enabled=>uxTask(async()=>{await post('/api/club/'+CLUB+'/membership/purchase-enabled',{enabled:!!enabled});toast(enabled?'已开通「购买会员」：C 端会员中心会出现可购买的会员卡':'已关闭「购买会员」');loadMembers()});
/* 积分商城（2026-10-26）：顾客在 C 端用活动积分兑换优惠券/活动/服务/实物。 */
window.addMallItem=async()=>uxTask(async()=>{
  let benefits=[];try{benefits=await api(`/api/club/${CLUB}/benefits`)}catch(e){benefits=[]}
  const coupons=(benefits||[]).filter(b=>b.owner_type==='CLUB'&&b.benefit_type==='activity_coupon'&&b.status==='active');
  const d=await uxForm({title:'上架积分兑换商品',
    subtitle:'顾客在 C 端点「可用积分」进入积分商城，用活动积分兑换。优惠券需提前在「俱乐部营销 · 优惠券」中设计好；活动/服务/实物兑换后生成核销码，在本页核销。',
    fields:[
      {name:'itemType',label:'兑换类型',type:'select',full:true,required:true,value:'coupon',options:[{value:'coupon',label:'优惠券（兑换即发到顾客券包）'},{value:'activity',label:'活动（生成核销码）'},{value:'service',label:'一次服务（生成核销码）'},{value:'goods',label:'实物（生成核销码）'}]},
      {name:'refId',label:'关联优惠券（仅优惠券类型）',type:'select',full:true,options:coupons.length?coupons.map(b=>({value:b.id,label:b.title})):[]},
      {name:'title',label:'商品名称',required:true,full:true,value:'露营灯兑换'},
      {name:'pointsCost',label:'所需活动积分（100 分 = ¥1）',type:'number',min:1,step:1,value:500,required:true},
      {name:'stock',label:'库存（留空 = 不限）',type:'number',min:0,step:1,value:20},
      {name:'description',label:'兑换说明',full:true,value:'凭核销码到店领取'}
    ],submitText:'上架'});
  if(!d)return;
  const type=String(d.itemType||'goods');
  await post('/api/club/'+CLUB+'/points-mall',{itemType:type,title:d.title,
    refId:type==='coupon'?Number(d.refId||0):null,
    pointsCost:Number(d.pointsCost),stock:d.stock===''?null:Number(d.stock),description:d.description});
  toast('兑换商品已上架');loadMembers()});
window.editMallItem=async id=>{
  let mall=[];try{mall=await api(`/api/club/${CLUB}/points-mall`)}catch(e){mall=[]}
  const r=(mall||[]).find(x=>Number(x.id)===Number(id));
  if(!r){showAlert({title:'读取失败',message:'请重新打开「客户会员」再试'});return}
  const d=await uxForm({title:'编辑兑换商品 · '+(r.title||''),fields:[
    {name:'title',label:'商品名称',required:true,full:true,value:r.title||''},
    {name:'pointsCost',label:'所需活动积分',type:'number',min:1,step:1,value:r.points_cost||1,required:true},
    {name:'stock',label:'库存（留空 = 不限）',type:'number',min:0,step:1,value:r.stock==null?'':r.stock},
    {name:'status',label:'状态',type:'select',full:true,value:r.status||'active',options:[{value:'active',label:'上架中'},{value:'inactive',label:'已下架'}]},
    {name:'description',label:'兑换说明',full:true,value:r.description||''}
  ],submitText:'保存'});
  if(!d)return;
  await uxFlow('editMallItem',async()=>{
    await api(`/api/club/${CLUB}/points-mall/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({title:d.title,pointsCost:Number(d.pointsCost),stock:d.stock===''?null:Number(d.stock),status:d.status,description:d.description})});
    toast('兑换商品已更新');await loadMembers();
  });
};
window.delMallItem=async id=>uxTask(async()=>{if(!await uxConfirm({title:'删除兑换商品',message:'删除后 C 端积分商城立刻不可见；已产生的兑换记录保留。'}))return;await api(`/api/club/${CLUB}/points-mall/${id}`,{method:'DELETE'});toast('已删除');loadMembers()});
window.verifyMallCode=async()=>{
  const d=await uxForm({title:'核销积分商城兑换码',subtitle:'顾客出示 PM- 开头的核销码（活动/服务/实物类兑换），输入即可核销。优惠券类兑换直接进了顾客券包，无需核销。',
    fields:[{name:'code',label:'核销码',required:true,full:true,placeholder:'如 PM-1A2B3C4D'}],submitText:'核销'});
  if(!d)return;
  await uxFlow('verifyMallCode',async()=>{
    const r=await post('/api/club/'+CLUB+'/points-mall/verify',{code:d.code});
    showAlert({title:'核销成功',message:`「${r.redemption.item_title}」已核销（${r.redemption.points_spent} 积分 · ${r.redemption.item_type==='activity'?'活动':r.redemption.item_type==='service'?'服务':'实物'}）`});
    await loadMembers();
  });
};
/* 同意之后状态是 processing（等待支付渠道退回）。渠道侧失败/回调丢失时，这一条会一直挂在那儿，
   后台没有任何可操作的东西 —— 之前 processing 只显示「等待支付渠道退款」一句静态文字。
   补一个重试入口：后端 retry-provider 会重新把退款推给支付渠道，本地通道会回
   SIMULATE_PROVIDER_REFUND_CALLBACK，需要再补一个退款成功事件才能真正到账。 */
window.retryRefundProvider=async id=>uxTask(async()=>{const r=await api('/api/club/'+CLUB+'/refunds/'+id+'/retry-provider',{method:'POST'});toast((r&&r.status==='succeeded')?'退款已到账':'已重新提交支付渠道，等待到账');loadRegs()});
window.rejectRefund=async id=>uxTask(async()=>{const d=await uxForm({title:'拒绝活动退款申请',subtitle:'拒绝原因会留在本俱乐部退款记录中。',fields:[{name:'note',label:'处理原因',type:'textarea',required:true,full:true,value:'不符合本活动退款规则'}],submitText:'确认拒绝'});if(!d)return;await post('/api/club/'+CLUB+'/refunds/'+id+'/reject',d);toast('已拒绝退款');loadRegs()});
window.approveRefund=async id=>uxTask(async()=>{if(!await uxConfirm({title:'同意活动退款',message:'确认本俱乐部的退款规则和申请金额后继续。实际退款取决于支付渠道处理状态。',confirmText:'同意并提交退款'}))return;const r=await api('/api/club/'+CLUB+'/refunds/'+id+'/approve',{method:'POST'});toast(r.status==='succeeded'?'退款已完成':'已同意，等待支付渠道退款');loadRegs()});
window.clubInsurance=async(pid,regId)=>uxTask(async()=>{const d=await uxForm({title:'参加人保险资料',fields:[{name:'status',label:'保险状态',type:'select',required:true,options:[{value:'pending',label:'待处理'},{value:'submitted',label:'已提交'},{value:'insured',label:'已承保'},{value:'failed',label:'提交失败'},{value:'not_required',label:'无需保险'}]},{name:'provider',label:'保险公司 / 渠道'},{name:'policyNo',label:'保单号',full:true}],submitText:'保存保险状态'});if(!d)return;await patch('/api/club/'+CLUB+'/participants/'+pid+'/insurance',d);toast('保险状态已更新');document.querySelector('.modal.show')?.remove();showParticipants(regId);loadRegs()});

/* 会员深度管理（2026-10-22 用户截图反馈）：点进会员 → 看全量信息 + 手工调积分 + 赠券。
   两个写流程都走 /api/club/{c}/members/{uid}/...；成功后原位刷新详情弹窗
   （renderMemberDetail 在 club.js，全局可用），并刷新列表余额。 */
const _refreshMemberDetail=userId=>{
  loadMembers();
  const ov=window._memberDetailOv;
  if(ov&&ov.isConnected)renderMemberDetail(ov,userId);
};
window.adjustMemberPoints=async(userId,kind)=>uxTask(async()=>{
  /* 2026-10-23 起俱乐部只能调「活动积分」：装备积分是总平台体系积分，
     界面上不再提供入口，后端对 kind=gear 也一律 403。 */
  if(kind&&kind!=='club'){showAlert({title:'无法调整装备积分',message:'装备积分由 ClubOS 总平台统一调整，俱乐部只能调整活动积分。'});return}
  const d=await uxForm({title:'调整活动积分',
    subtitle:'正数＝增加，负数＝扣减。每次调整都会写进该会员的积分流水；扣减不能超过当前余额。',
    fields:[{name:'amount',label:'调整数量（正数增加 / 负数扣减）',type:'number',step:1,required:true,value:100,help:'例：补偿 500 分填 500；纠错扣 200 分填 -200'},
           {name:'note',label:'调整原因',required:true,full:true,value:'投诉补偿',placeholder:'会显示在积分流水里，请写清楚原因'}],
    submitText:'确认调整'});
  if(!d||!d.amount)return;
  const r=await post(`/api/club/${CLUB}/members/${userId}/points`,{kind:'club',amount:Number(d.amount),note:d.note});
  toast(`活动积分已调整，当前余额 ${r.balance}`);
  _refreshMemberDetail(userId);
});
window.giftMemberVoucher=async userId=>uxTask(async()=>{
  let benefits=[];try{benefits=await api(`/api/club/${CLUB}/benefits`)}catch(e){benefits=[]}
  const giftof=(benefits||[]).filter(b=>b.owner_type==='CLUB'&&b.status==='active'&&(b.stock==null||Number(b.stock)>0));
  if(!giftof.length){toast('还没有可赠送的券，请先在「俱乐部营销 · 优惠券」创建');return}
  const d=await uxForm({title:'赠送优惠券',
    subtitle:'顾客立刻在自己账户里收到这张券，报名付款时可直接抵扣；券库存会相应扣减。',
    fields:[{name:'benefitId',label:'选择优惠券',type:'select',required:true,full:true,
             options:giftof.map(b=>({value:b.id,label:`${b.title}${b.benefit_type==='activity_coupon'?`（抵扣 ${money(b.cash_value||0)}）`:'（非现金福利）'} · 库存 ${b.stock==null?'不限':b.stock}`}))},
           {name:'note',label:'赠送说明（可选）',full:true,placeholder:'如：老会员回馈'}],
    submitText:'确认赠送'});
  if(!d)return;
  const r=await post(`/api/club/${CLUB}/members/${userId}/vouchers`,{benefitId:Number(d.benefitId),note:d.note});
  toast(`已赠送「${r.title}」· 券码 ${r.voucherCode}`);
  _refreshMemberDetail(userId);
});
})();
