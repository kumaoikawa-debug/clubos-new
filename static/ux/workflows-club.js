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
/* 2026-10-27（用户要求）：条件开通与购买开通彻底分开 —— 入口不同、字段不同，绝不混在一个弹窗。
   折扣统一按「折」输入（8 = 8 折），保存时 ×10 存成 activity_discount_percent（90 制）。 */
window.addTier=async mode=>{
  const buy=String(mode||'CONDITION')==='PURCHASE';
  const zheField={name:'discountZhe',label:'活动报名折扣（折）',type:'number',min:0,max:9.9,step:.1,value:0,full:true,
    help:'填 8 = 打 8 折，填 0 = 该等级不打折。会员报名「开启了会员优惠」的活动时按此价立减，活动上还要打开开关才生效。'};
  const rateField={name:'pointsRedeemRate',label:'积分换算（X 积分 = ¥1）',type:'number',min:0,max:10000,step:1,value:0,full:true,
    help:'填 0 = 跟随俱乐部默认；填正数 = 本等级专属（例 100 / 50 / 20）。只影响该等级会员报名时用活动积分抵扣，装备积分不受影响。'};
  return uxTask(async()=>{
    const d=await uxForm({
      title: buy?'新建付费会员卡':'新建等级会员',
      subtitle: buy?'购买开通：顾客在 C 端付费购买这张卡，到期自动失效。卡价与有效期只在这里填。'
                  :'条件开通：会员累计活动消费 / 活动次数达标后自动升级，不需要付费。',
      fields: buy?[
        {name:'name',label:'会员卡名称',required:true,value:'年卡会员'},
        {name:'rank',label:'卡等级顺序（越大越高）',type:'number',min:1,step:1,value:30,required:true},
        {name:'price',label:'售价（元）',type:'number',min:0,step:.01,value:399},
        {name:'validMonths',label:'有效期（月）',type:'number',min:1,step:1,value:12},
        zheField,
        rateField
      ]:[
        {name:'name',label:'等级名称',required:true,value:'新等级'},
        {name:'rank',label:'等级顺序（越大越高）',type:'number',min:1,step:1,value:30,required:true},
        {name:'minActivitySpend',label:'累计活动消费 ≥（元）',type:'number',min:0,step:1,value:5000,required:true},
        {name:'minActivityCount',label:'累计活动次数 ≥',type:'number',min:0,step:1,value:10,required:true},
        zheField,
        rateField
      ],
      submitText: buy?'保存会员卡':'保存等级'});
    if(!d)return;
    await post('/api/club/'+CLUB+'/membership/tiers',{...d,
      qualificationMode:'ANY',benefits:['专属会员权益'],
      accessMode: buy?'PURCHASE':'CONDITION',
      price: buy?Number(d.price||0):0,
      validMonths:Number(d.validMonths||12),
      activityDiscountPercent:Math.round(Number(d.discountZhe||0)*10),
      pointsRedeemRate:Number(d.pointsRedeemRate||0)});
    toast(buy?'会员卡已新增':'等级已新增');
    loadMembers();
  });
};
/* 券中心（#860 重构 → #869 经营主体隔离，2026-11-12）：券就是券，与积分完全解耦。
   · 券不消耗积分；要用积分换券去「积分商城」上架，积分价由老板自定义（addMallItem）。
   · #869 用户明确要求：俱乐部 / 营地后台只涉及活动与营地产品，总平台只管装备产品。
     本端候选项由 /api/club/{club}/coupon-scope-options 严格下发，服务端已把
     装备商城的 products / product_categories 整表挡在外面，两端数据绝不混用。
   · 建券流程：① 券形态（满减 / 折扣）→ ② 适用范围（仅活动 / 仅营地 / 通用，
     自定义且**非必选**）→ ③ 具体产品（**非必选**，不选 = 适用于该范围内全部产品）。
   · 有效期用原生日历点选，不再手填日期字符串。
   · 领用限制：可领取人群 / 每人每日限领 / 仅新用户。 */
let _cpnScopeCache=null;
async function couponScopeOptions(){
  if(_cpnScopeCache)return _cpnScopeCache;
  try{_cpnScopeCache=await api(`/api/club/${CLUB}/coupon-scope-options`);}
  catch(e){_cpnScopeCache={activities:[],camps:[],pitches:[],products:[],categories:[]};}
  return _cpnScopeCache;
}
/* 适用范围只决定「活动 / 营地」两大类，由经营者自定义，系统不设必选项。 */
function couponAppliesOpts(){
  return [{value:'all',label:'活动 + 营地通用'},
          {value:'activity',label:'仅限活动报名'},
          {value:'camp',label:'仅限营地消费'}];
}
/* P2-4：一级「适用范围」+ 二级「指定对象」级联。
   旧版两组 radio 概念重叠（「仅限活动报名」旁边又出现「指定营地」等 6 项），
   用户判断不出两者关系，还容易选中冲突组合触发校验错误。
   现在二级只列一级范围内真实存在的对象；一级切换时选项即时重绘，
   旧值若已不在范围内自动回落到「不指定」。第一项恒为「不指定」，
   用户明确要求「不选某一个产品就适用于全部产品」，这里没有 required、没有兜底选中。 */
const COUPON_APPLIES_HELP={
  all:'通用：活动报名与营地消费都能用；下面可选任意活动 / 营地对象。',
  activity:'仅活动报名：营位、营地商品不能用；下面只列活动与活动分类。',
  camp:'仅营地消费：活动报名不能用；下面只列营地、营位与营地商品。'
};
function couponProductOpts(o,applies){
  const mk=(arr,prefix,label)=>(arr||[]).map(x=>({value:prefix+':'+x.id,label:label+' · '+(x.name||('#'+x.id))}));
  const all=[{value:'',label:'不指定（一级范围内全部产品）'}];
  const a=String(applies||'all');
  if(a!=='camp')all.push(...mk(o.activities,'activity','指定活动'),...mk(o.categories,'activity_category','指定活动分类'));
  if(a!=='activity')all.push(...mk(o.camps,'camp','指定营地'),...mk(o.pitches,'pitch','指定营位'),
    ...mk(o.products,'camp_product','指定营地商品'));
  return all;
}
function couponScopeValue(b){
  if(!b)return '';
  if(b.scope_ref_kind&&b.scope_ref_id){
    /* #877 存量券的旧 kind 映射到带主体的新 kind，编辑时仍能正确回显 */
    const k=({product:'camp_product',category:'activity_category'})[b.scope_ref_kind]||b.scope_ref_kind;
    return k+':'+b.scope_ref_id;
  }
  return '';
}
function couponScopeParse(v){
  const s=String(v||'');
  if(!s||s.indexOf(':')<0)return {scopeRefKind:'',scopeRefId:null};
  const p=s.split(':'),k=p[0],id=Number(p[1])||null;
  const allowed={activity:'activity',camp:'camp',pitch:'pitch',
    camp_product:'camp_product',activity_category:'activity_category'};
  if(!id||!allowed[k])return {scopeRefKind:'',scopeRefId:null};
  return {scopeRefKind:allowed[k],scopeRefId:id};
}
/* 多营地适用：只有老板名下确实有多家营地时才给「仅指定营地」选项（用户明确要求）。 */
function couponCampFields(o,d={}){
  const camps=o.camps||[],multi=camps.length>1;
  let cur=[];try{cur=JSON.parse(d.camp_scope_ids||'[]')}catch(e){cur=[]}
  const out=[{name:'campScope',label:'多营地适用',type:'select',full:true,value:d.camp_scope||'all',
    options:[{value:'all',label:'全部营地适用'},...(multi?[{value:'specified',label:'仅指定营地适用'}]:[])],
    help:multi?'你名下有多家营地，可以限定这张券只能在其中一家使用。':'当前只有一家营地，默认全部营地适用。'}];
  if(multi)out.push({name:'campScopeId',label:'选择适用营地',type:'select',full:true,
    value:String(cur[0]||camps[0].id||''),options:camps.map(x=>({value:String(x.id),label:x.name||('#'+x.id)}))});
  return out;
}
function couponValidityFields(d={}){
  return [
    {name:'validityType',label:'有效期类型',type:'select',full:true,required:true,value:d.validity_type||'fixed',
      options:[{value:'fixed',label:'固定有效期内可用'},{value:'relative',label:'领券后有效期内可用'}]},
    {name:'validDays',label:'领券后有效天数（选「领券后有效期内可用」时填）',type:'number',min:1,step:1,value:d.valid_days||7,
      help:'例：填 7 表示顾客领券当日起 7 天内可用，每张券各自计算。'},
    /* #869 用户要求「有效期不要用手填，需要直接日历式直接选」：原生 date 控件
       在手机端唤起系统日历点选，桌面端也是下拉日历，不再接受手打 YYYY-MM-DD。 */
    {name:'validFrom',label:'固定有效期 · 开始日期（点日历选，留空 = 立即生效）',type:'date',value:String(d.valid_from||'').slice(0,10)},
    {name:'validTo',label:'固定有效期 · 结束日期（点日历选，留空 = 长期有效）',type:'date',value:String(d.valid_to||'').slice(0,10)}
  ];
}
function couponClaimFields(d={}){
  return [
    {name:'claimAudience',label:'可领取人群',type:'select',full:true,required:true,value:d.claim_audience||'all',
      options:[{value:'all',label:'所有人（会员 + 非会员）'},{value:'member',label:'仅会员'},
               {value:'non_member',label:'仅非会员'},{value:'new_user',label:'仅新用户'}]},
    {name:'perUserDailyLimit',label:'每人每日限领（张，0 = 不限）',type:'number',min:0,step:1,value:d.per_user_daily_limit||0},
    {name:'newUserOnly',label:'仅限新用户领取',type:'select',value:d.new_user_only?'是':'否',
      options:[{value:'否',label:'否'},{value:'是',label:'是'}],help:'新用户 = 从未下过任何活动 / 装备 / 营地订单。'},
    {name:'perUserLimit',label:'每人累计限领（张）',type:'number',min:1,step:1,value:d.per_user_limit||1}
  ];
}
function couponBuildBase(d,mode,extra){
  extra=extra||{};
  const sp=couponScopeParse(d.scopeRef);
  const specified=d.campScope==='specified';
  /* #869 适用范围与具体产品彻底解耦：appliesTo 只由经营者自己选的大类决定，
     不再被「指定对象」反推覆盖；不选具体产品时 scopeRef 为空、大类照常生效。 */
  const applies=(['activity','camp','all'].indexOf(d.appliesTo)>=0)?d.appliesTo:'all';
  return Object.assign({
    title:d.title,benefitType:'activity_coupon',appliesTo:applies,mode,
    scopeRefKind:sp.scopeRefKind,scopeRefId:sp.scopeRefId,
    campScope:specified?'specified':'all',
    campScopeIds:specified&&d.campScopeId?[Number(d.campScopeId)]:[],
    validityType:d.validityType||'fixed',
    validDays:Number(d.validDays||0),
    validFrom:d.validFrom||null,validTo:d.validTo||null,
    claimAudience:d.claimAudience||'all',
    perUserDailyLimit:Number(d.perUserDailyLimit||0),
    newUserOnly:d.newUserOnly==='是',
    perUserLimit:Math.max(1,Number(d.perUserLimit||1)),
    thresholdAmount:Number(d.thresholdAmount||0)||0,
    cashValue:(mode==='discount')?0:Number(d.cashValue||0),
    discountPercent:(mode==='discount')?Number(d.discountPercent||0):0,
    maxDiscountAmount:Number(d.maxDiscountAmount||0)||0,
    discountCouponType:d.discountCouponType||'single',
    /* #860：券本身不带任何积分门槛。pointsCost 固定 0，仅为兼容历史列。 */
    pointsCost:0,
    stock:(d.stock===''||d.stock==null)?null:Number(d.stock),
    description:d.description||'',pointsType:'club',claimMode:'grant'
  },extra);
}
async function couponCreateFlow(title,mode,buildFields){
  const o=await couponScopeOptions();
  const d=await uxForm({title,
    subtitle:'俱乐部优惠券只用于本俱乐部的活动报名与营地消费；装备商品由总平台管理，不在这里配置。先定适用范围，再按需指定具体产品（可以不选）。券不消耗积分，如需积分兑换请到「积分商城」上架。',
    fields:buildFields(o),submitText:'下一步：设计券面'});
  if(!d)return;
  const base=couponBuildBase(d,mode);
  const ok=await cpnFaceDialog(base);
  if(ok){toast('优惠券已创建');loadMembers()}
}
function couponCommonFields(o,d={},extra=[]){
  return [
    {name:'title',label:'优惠券名称',required:true,full:true,value:d.title||'',maxLength:30},
    /* #869 第一步：定「活动 / 营地」大类。不是必填，默认通用，完全由经营者自定义。 */
    {name:'appliesTo',label:'① 适用范围（一级）',type:'select',full:true,value:d.applies_to||'all',
      options:couponAppliesOpts(),
      helpFn:all=>COUPON_APPLIES_HELP[String(all.appliesTo||'all')]||COUPON_APPLIES_HELP.all},
    /* #869 第二步：选定范围后才选具体产品；不选＝适用于该范围内全部产品。
       P2-4：二级候选随一级级联，冲突组合从源头消失，下面的 validate 只作兜底。 */
    {name:'scopeRef',label:'② 指定对象（二级 · 可选）',type:'select',full:true,value:couponScopeValue(d),
      options:couponProductOpts(o,d.applies_to||'all'),
      optionsFn:all=>couponProductOpts(o,all.appliesTo||'all'),
      helpFn:all=>'只列「'+({all:'活动 + 营地',activity:'活动',camp:'营地'}[String(all.appliesTo||'all')]||'活动 + 营地')
        +'」范围内本俱乐部的真实业务数据；不指定 = 一级范围内全部产品，不是必填项。',
      validate:(v,data)=>{
        if(!v)return '';
        const k=String(v).split(':')[0], ap=data.appliesTo||'all';
        if(ap==='activity'&&['camp','pitch','camp_product'].indexOf(k)>=0)
          return '「仅限活动报名」的券不能指定营地 / 营位 / 营地商品，请改选活动或清空具体产品';
        if(ap==='camp'&&['activity','activity_category'].indexOf(k)>=0)
          return '「仅限营地消费」的券不能指定活动 / 活动分类，请改选营地对象或清空具体产品';
        return '';
      }},
    ...extra,
    ...couponCampFields(o,d),
    ...couponValidityFields(d),
    ...couponClaimFields(d),
    {name:'stock',label:'券库存',type:'number',min:0,step:1,value:d.stock==null?50:d.stock,required:true},
    {name:'description',label:'使用说明',full:true,value:d.description||''}
  ];
}
window.addThresholdCoupon=()=>uxTask(()=>couponCreateFlow('添加满减优惠券','threshold',o=>couponCommonFields(o,{},[
  {name:'thresholdAmount',label:'满减金额 · 满（元）',type:'number',min:0,step:.01,value:200,required:true},
  {name:'cashValue',label:'满减金额 · 减（元）',type:'number',min:0,step:.01,value:30,required:true}
])));
window.addDiscountCoupon=()=>uxTask(()=>couponCreateFlow('添加折扣券','discount',o=>couponCommonFields(o,{},[
  {name:'discountCouponType',label:'折扣券类型',type:'select',full:true,required:true,value:'single',
    options:[{value:'single',label:'单次折扣券'},{value:'stored',label:'储值折扣券'}]},
  {name:'discountPercent',label:'折扣（填折后百分比：90 = 9 折）',type:'number',min:1,max:99,step:1,value:90,required:true},
  {name:'thresholdAmount',label:'使用门槛（元，满此金额可用；0 = 无门槛）',type:'number',min:0,step:.01,value:0},
  {name:'maxDiscountAmount',label:'最大抵扣金额（元，0 = 不限制）',type:'number',min:0,step:.01,value:0}
])));
window.addRedeemCoupon=()=>uxTask(()=>couponCreateFlow('添加商品兑换券','fixed',o=>couponCommonFields(o,{},[
  {name:'cashValue',label:'券价值（元，兑换时抵扣的金额）',type:'number',min:0,step:.01,value:68,required:true},
  {name:'thresholdAmount',label:'使用门槛（元，满此金额可用；0 = 无门槛）',type:'number',min:0,step:.01,value:0}
])));
/* 编辑（#860）：三型券共用一套字段，提交走 PATCH（cpnFaceDialog 透传全部新字段）。 */
window.editCoupon=async id=>uxTask(async()=>{
  const b=(window._clubBenefits||[]).find(x=>Number(x.id)===Number(id));
  if(!b){showAlert({title:'读取失败',message:'请重新打开「俱乐部营销」再试'});return;}
  const mode=String(b.mode||'fixed'),o=await couponScopeOptions();
  let extra;
  if(mode==='threshold')extra=[
    {name:'thresholdAmount',label:'满减金额 · 满（元）',type:'number',min:0,step:.01,value:b.threshold_amount||0,required:true},
    {name:'cashValue',label:'满减金额 · 减（元）',type:'number',min:0,step:.01,value:b.cash_value||0,required:true}];
  else if(mode==='discount')extra=[
    {name:'discountCouponType',label:'折扣券类型',type:'select',full:true,required:true,value:b.discount_coupon_type||'single',
      options:[{value:'single',label:'单次折扣券'},{value:'stored',label:'储值折扣券'}]},
    {name:'discountPercent',label:'折扣（填折后百分比：90 = 9 折）',type:'number',min:1,max:99,step:1,value:b.discount_percent||0,required:true},
    {name:'thresholdAmount',label:'使用门槛（元，0 = 无门槛）',type:'number',min:0,step:.01,value:b.threshold_amount||0},
    {name:'maxDiscountAmount',label:'最大抵扣金额（元，0 = 不限制）',type:'number',min:0,step:.01,value:b.max_discount_amount||0}];
  else extra=[
    {name:'cashValue',label:'券价值（元，兑换时抵扣的金额）',type:'number',min:0,step:.01,value:b.cash_value||0,required:true},
    {name:'thresholdAmount',label:'使用门槛（元，0 = 无门槛）',type:'number',min:0,step:.01,value:b.threshold_amount||0}];
  const d=await uxForm({title:'编辑优惠券 · '+(b.title||''),fields:couponCommonFields(o,b,extra),submitText:'保存修改'});
  if(!d)return;
  const base=couponBuildBase(d,mode);
  base._editId=b.id;
  const ok=await cpnFaceDialog(base);
  if(ok){toast('优惠券已更新');loadMembers()}
});
window.delCoupon=async id=>uxTask(async()=>{
  const b=(window._clubBenefits||[]).find(x=>Number(x.id)===Number(id));
  const name=b?b.title:'该优惠券';
  if(!await uxConfirm({title:'下架优惠券',message:'确定下架「'+name+'」？下架后顾客无法再领取，但已领取 / 已核销的记录不受影响。',confirmText:'下架'}))return;
  try{await api(`/api/club/${CLUB}/benefits/${id}`,{method:'DELETE'});toast('已下架');loadMembers()}
  catch(e){showAlert({title:'下架失败',message:e.message})}
});
window.couponDetail=async id=>{
  const b=(window._clubBenefits||[]).find(x=>Number(x.id)===Number(id));
  if(!b){showAlert({title:'读取失败',message:'请重新打开「俱乐部营销」再试'});return;}
  const scopeCn=({all:'活动 + 营地通用',activity:'仅限活动报名',camp:'仅限营地消费',product:'指定商品',category:'指定分类'})[b.applies_to]||b.applies_to;
  const kindCn=({activity:'活动',camp:'营地',pitch:'营位',product:'商品',category:'商品分类',
    camp_product:'营地商品',activity_category:'活动分类'})[b.scope_ref_kind]||'';
  const audienceCn=({all:'所有人（会员 + 非会员）',member:'仅会员',non_member:'仅非会员',new_user:'仅新用户'})[b.claim_audience]||'所有人';
  const validCn=b.validity_type==='relative'?('领券后 '+(b.valid_days||0)+' 天内可用'):couponValidCn(b);
  const rows=[
    ['ID',b.id],['名称',b.title],
    ['券形态',({threshold:'满减优惠券',discount:'折扣券',fixed:'商品兑换券'})[b.mode]||b.mode],
    ['适用范围',scopeCn+(kindCn?(' · 指定' + kindCn + ' #' + b.scope_ref_id):'')],
    ['多营地适用',b.camp_scope==='specified'?('仅指定营地 · '+(b.camp_scope_ids||'[]')):'全部营地适用'],
    ['权益',b.mode==='threshold'?`满 ${money(b.threshold_amount)} 减 ${money(b.cash_value)}`:(b.mode==='discount'?`${Number(b.discount_percent||0)}%（最高抵 ${b.max_discount_amount?money(b.max_discount_amount):'不限'}）`:`价值 ${money(b.cash_value)}`)],
    ['有效期',validCn],
    ['可领取人群',audienceCn+(b.new_user_only?' · 仅新用户':'')],
    ['每人每日限领',b.per_user_daily_limit?b.per_user_daily_limit+' 张':'不限'],
    ['每人累计限领',(b.per_user_limit||1)+' 张'],
    ['库存',b.stock==null?'不限':b.stock],
    ['状态',b.status==='active'?'上架中':'已下架']
  ];
  const body='<div class="kv-list">'+rows.map(r=>`<div class="kv"><span class="k">${esc(r[0])}</span><span class="v">${esc(String(r[1]))}</span></div>`).join('')+'</div>';
  const ov=uxDialog({title:'优惠券详情 · '+(b.title||''),body,foot:'<button class="btn ghost" type="button" data-close>关闭</button>'});
  ov.querySelector('[data-close]').onclick=()=>ov.uxClose();
};

/* 券面设计器（2026-10-26）：内置模板一键选色，或手填 #RRGGBB 自定义渐变双色 + 文字色，
   实时预览最终券面。手填色失焦自动补 # 前缀，写错标红保留原值 —— 与规格色值同一套交互。
   返回 true=已创建；false/undefined=取消。 */
window.cpnFaceDialog=base=>new Promise(resolve=>{
  const tpls=window.uxCouponTemplates||[];
  const $=s=>document.querySelector(s);
  const body=`<div class="ux-cpn-designer">
    <div class="ux-field"><label>内置模板</label>
      <div class="ux-cpn-designer__tpls">${tpls.map((t,i)=>`<button type="button" class="ux-cpn-designer__tpl${i===0?' on':''}" data-key="${t.key}"><i style="--t-from:${t.from};--t-to:${t.to}"></i>${t.name}</button>`).join('')}</div>
      <small>选一款模板，或在下面自定义渐变双色与文字色（留空 = 跟随所选模板）。</small></div>
    <div class="ux-cpn-designer__custom">
      <label>渐变起 <input id="cpnC1" placeholder="#FF9A5A" maxlength="7" autocomplete="off"></label>
      <label>渐变止 <input id="cpnC2" placeholder="#EF6236" maxlength="7" autocomplete="off"></label>
      <label>文字色 <input id="cpnInk" placeholder="#FFFFFF" maxlength="7" autocomplete="off"></label>
    </div>
    <div class="ux-field"><label>实时预览（顾客券包 / 结算 / 分享卡同款）</label>
      <div class="ux-cpn-designer__preview" id="cpnPrev"></div></div>
  </div>`;
  const ov=uxDialog({title:'设计券面',desc:'这张券会以你设计的样式出现在顾客的券包、报名结算与分享卡上。',wide:true,body,
    onClose:()=>resolve(false),
    foot:`<button class="btn ghost" type="button" data-cancel>取消</button><button class="btn" type="button" data-cpn-create>创建优惠券</button>`});
  let tplKey=(tpls[0]||{}).key||'sunrise';
  const norm=v=>{v=String(v||'').trim();if(!v)return null;if(/^[0-9a-fA-F]{6}$/.test(v))v='#'+v;return /^#[0-9a-fA-F]{6}$/.test(v)?v.toUpperCase():'BAD'};
  const readFace=()=>{
    const ids=['cpnC1','cpnC2','cpnInk'],out={},face={};
    ids.forEach((id,i)=>{const el=ov.querySelector('#'+id);const v=norm(el.value);
      el.classList.toggle('is-bad',v==='BAD');
      if(v&&v!=='BAD')face[['c1','c2','ink'][i]]=v;});
    return Object.keys(face).length?face:null;
  };
  const paint=()=>{
    const prev=ov.querySelector('#cpnPrev');if(!prev)return;
    prev.innerHTML=window.uxCouponFace(Object.assign({},base,{benefitType:'activity_coupon',imageKey:tplKey,face:readFace()}));
  };
  ov.querySelectorAll('.ux-cpn-designer__tpl').forEach(btn=>{btn.onclick=()=>{
    ov.querySelectorAll('.ux-cpn-designer__tpl').forEach(b=>b.classList.remove('on'));
    btn.classList.add('on');tplKey=btn.dataset.key;paint();}});
  ['cpnC1','cpnC2','cpnInk'].forEach(id=>{const el=ov.querySelector('#'+id);
    el.addEventListener('input',paint);
    el.addEventListener('blur',()=>{const v=norm(el.value);
      if(v&&v!=='BAD')el.value=v;           /* 失焦补全 # 与大写 */
      el.classList.toggle('is-bad',v==='BAD');paint();});});
  paint();
  ov.querySelector('[data-cancel]').onclick=()=>{ov.uxClose();resolve(false)};
  ov.querySelector('[data-cpn-create]').onclick=async()=>{
    const bad=ov.querySelector('.is-bad');
    if(bad){toast('自定义色值写错了，请填 6 位十六进制（如 #FF9A5A）');bad.focus();return}
    const submit=ov.querySelector('[data-cpn-create]');submit.disabled=true;
    try{
      const face=readFace();
      const payload=face?Object.assign({},base,{face}):base;
      if(base._editId){
        await api('/api/club/'+CLUB+'/benefits/'+base._editId,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      }else{
        await post('/api/club/'+CLUB+'/benefits',payload);
      }
      ov.uxClose();resolve(true);
    }catch(e){toast((e&&e.message)||e);submit.disabled=false}
  };
});
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
/* 满额买赠 / 邀请有礼（P5/P6 营销引擎）：成交回调自动发放积分 / 礼品卡额度 / 优惠券，幂等留痕。 */
window.addBuyGift=async()=>uxTask(async()=>{
  const d=await uxForm({title:'新增买赠 / 邀请有礼规则',
    subtitle:'顾客单笔活动 / 营地订单实付满门槛后，成交时自动发放赠品（不改订单应付金额，成本由俱乐部承担）。「邀请有礼」只对被邀请好友在本俱乐部的首单生效；装备商城订单不参与。同一订单同一活动只发一次。',
    fields:[
      {name:'ctype',label:'规则类型',type:'select',full:true,required:true,value:'BUY_GIFT',
       options:[{value:'BUY_GIFT',label:'满额买赠（对所有顾客生效）'},{value:'INVITE',label:'邀请有礼（仅被邀请好友的本俱乐部首单）'}]},
      {name:'title',label:'规则名称（仅后台识别用）',type:'text',required:true,full:true,placeholder:'如：满 500 送 30 元券'},
      {name:'threshold',label:'单笔实付满（元）',type:'number',min:.01,step:.01,value:500,required:true},
      {name:'giftType',label:'赠品类型',type:'select',full:true,required:true,value:'coupon',
       options:[{value:'coupon',label:'优惠券（自动生成活动券，按面额入顾客券包）'},{value:'points',label:'活动积分（入顾客俱乐部积分）'},{value:'gift_card',label:'礼品卡额度（入顾客礼品卡余额）'}]},
      {name:'giftValue',label:'赠品面额（积分数量 / 元）',type:'number',min:.01,step:.01,value:30,required:true},
      {name:'maxGifts',label:'单笔订单最多发放份数（留空 = 不限）',type:'number',min:1,step:1}
    ],submitText:'创建规则'});
  if(!d)return;
  const rules={threshold:Number(d.threshold),giftType:String(d.giftType),giftValue:Number(d.giftValue)};
  if(Number(d.maxGifts||0)>0)rules.maxGifts=Number(d.maxGifts);
  await post('/api/club/'+CLUB+'/campaigns',{type:String(d.ctype),title:String(d.title||'').trim(),status:'ACTIVE',schedule:{},scope:{},rules});
  showAlert({title:'规则已创建 · 启用中',message:'「'+String(d.title||'').trim()+'」'
    +(String(d.ctype)==='INVITE'?'仅对被邀请好友在本俱乐部的首单生效':'对所有顾客的活动 / 营地订单生效')
    +'。成交后自动发放赠品，发放记录可在「赠品发放记录」核对；C 端订单详情展示实发赠品，装备商城不参与。'});
  loadMembers()});
window.toggleCampaign=async(id,status)=>uxTask(async()=>{await api(`/api/club/${CLUB}/campaigns/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});toast(status==='ACTIVE'?'规则已启用':'规则已停用');loadMembers()});
window.delCampaign=async id=>uxTask(async()=>{if(!await uxConfirm({title:'删除买赠规则',message:'删除后，已发放出去的赠品不受影响，只是不再自动发放。'}))return;await api(`/api/club/${CLUB}/campaigns/${id}`,{method:'DELETE'});toast('规则已删除');loadMembers()});
/* 首单立减 / 限时折扣（P0-1 营销引擎定价型）：报价链自动生效，只改实付不改规则。 */
window.addPriceCampaign=async()=>uxTask(async()=>{
  const d=await uxForm({title:'新增立减 / 限时活动',
    subtitle:'「首单立减」只对在本俱乐部没有已支付订单的新顾客生效；「限时折扣」必须设置生效时段（北京时间 UTC+8），窗口外自动不生效。优惠叠加在会员折扣 / 券之后，只改实付金额。',
    fields:[
      /* P2-2：互斥字段按 mode / ctype 联动显隐，不再靠 label 文字提示「选折扣时填」。
         隐藏字段由 uxForm 自动跳过必填与校验，也不会把旧值带进提交数据。 */
      {name:'ctype',label:'活动类型',type:'select',full:true,required:true,value:'NEWCOMER',
       options:[{value:'NEWCOMER',label:'首单立减（新顾客首单专享）'},{value:'TIME_LIMIT',label:'限时折扣（全部顾客 · 需设时段）'}],
       helpFn:all=>all.ctype==='TIME_LIMIT'
         ?'限时折扣：对所有顾客生效，但只在下面设定的时段内生效；窗口外报价自动不打折。'
         :'首单立减：仅对在本俱乐部没有已支付订单的新顾客生效，长期有效。',
       validate:(v,all)=>(v==='TIME_LIMIT'&&!(all.startAt||all.endAt||all.timeFrom||all.timeTo||(all.weekdays&&all.weekdays!=='all')))
         ?'限时折扣请至少填一项生效时段；不设时段请选「首单立减」':''},
      {name:'title',label:'活动名称（仅后台识别用）',type:'text',required:true,full:true,placeholder:'如：新客立减 30 元'},
      {name:'mode',label:'优惠方式',type:'select',full:true,required:true,value:'fixed',
       options:[{value:'fixed',label:'立减固定金额（元）'},{value:'percent',label:'按百分比折扣'}],
       helpFn:all=>all.mode==='percent'
         ?'折扣模式：按折后百分比减价，可另设单笔封顶金额。'
         :'立减模式：每单直接减固定金额，不设封顶。'},
      {name:'amount',label:'立减金额（元）',type:'number',min:0,step:.01,value:30,required:true,
       when:{field:'mode',in:['fixed']},help:'每单直接减这么多钱；填 0 表示不打折。'},
      {name:'percent',label:'折扣率（90 = 9 折）',type:'number',min:1,max:100,step:1,value:90,required:true,
       when:{field:'mode',in:['percent']},help:'填 90 表示按应付金额的 90% 收取（即 9 折）。'},
      {name:'maxDiscount',label:'单笔最多优惠金额（元，可空）',type:'number',min:0,step:.01,
       when:{field:'mode',in:['percent']},help:'仅折扣模式有效；留空或 0 = 不封顶。'},
      {name:'startAt',label:'生效开始（北京时间，可空）',type:'text',full:true,placeholder:'2026-11-20 00:00',
       when:{field:'ctype',in:['TIME_LIMIT']},
       help:'仅「限时折扣」有效。格式 YYYY-MM-DD HH:mm；留空 = 立即生效。',
       validate:(v,all)=>{if(!v||all.ctype!=='TIME_LIMIT')return '';return /^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$/.test(String(v).trim())?'':'开始时间请填 YYYY-MM-DD HH:mm 或 YYYY-MM-DD';}},
      {name:'endAt',label:'生效结束（北京时间，可空）',type:'text',full:true,placeholder:'2026-11-30 23:59',
       when:{field:'ctype',in:['TIME_LIMIT']},
       help:'留空 = 不设结束时间；只填日期时按当天 23:59:59 计。',
       validate:(v,all)=>{if(!v||all.ctype!=='TIME_LIMIT')return '';return /^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$/.test(String(v).trim())?'':'结束时间请填 YYYY-MM-DD HH:mm 或 YYYY-MM-DD';}},
      {name:'timeFrom',label:'每日生效时段 · 从',type:'time',when:{field:'ctype',in:['TIME_LIMIT']},help:'如 20:00；留空 = 不限制开始时刻'},
      {name:'timeTo',label:'每日生效时段 · 到',type:'time',when:{field:'ctype',in:['TIME_LIMIT']},help:'如 22:00；留空 = 不限制结束时刻'},
      {name:'weekdays',label:'每周生效日',type:'select',value:'all',full:true,when:{field:'ctype',in:['TIME_LIMIT']},
       options:[{value:'all',label:'每天'},{value:'0,1,2,3,4',label:'工作日（周一至周五）'},
                {value:'5,6',label:'周末（周六 / 周日）'},{value:'0',label:'仅周一'},{value:'4',label:'仅周五'}]}
    ],submitText:'创建活动'});
  if(!d)return;
  const rules={mode:String(d.mode||'fixed')};
  if(rules.mode==='percent'){
    const p=Number(d.percent);
    if(!(p>0&&p<=100)){toast('折扣率请填 1–100（90 = 9 折）');return}
    rules.percent=p;
  }else{
    const a=Number(d.amount);
    if(!(a>=0)||!d.amount){toast('请填写立减金额');return}
    rules.amount=a;
  }
  if(Number(d.maxDiscount||0)>0)rules.maxDiscount=Number(d.maxDiscount);
  // P1-3：真正把时段写进 schedule，不再写死 {} 让「限时折扣」永久生效。
  const schedule={};
  if(String(d.ctype)==='TIME_LIMIT'){
    if(String(d.startAt||'').trim())schedule.startAt=String(d.startAt).trim();
    if(String(d.endAt||'').trim())schedule.endAt=String(d.endAt).trim();
    const toMin=s=>{const m=/^(\d{1,2}):(\d{2})$/.exec(String(s||''));return m?(Number(m[1])*60+Number(m[2])):null};
    const f=toMin(d.timeFrom),t=toMin(d.timeTo);
    if(f!==null)schedule.timeFrom=f;
    if(t!==null)schedule.timeTo=t;
    if(d.weekdays&&d.weekdays!=='all')schedule.weekdays=String(d.weekdays).split(',').map(Number);
    if(!Object.keys(schedule).length){toast('限时折扣必须设置至少一项生效时段');return}
    schedule.timezone='UTC+8';
  }
  await post('/api/club/'+CLUB+'/campaigns',{type:String(d.ctype),title:String(d.title||'').trim(),status:'ACTIVE',schedule,scope:{},rules});
  /* 操作反馈（UX 建议）：创建后不只给一句 toast，把「对谁生效 / 什么时段 / C 端能否看见」说清。 */
  const who=String(d.ctype)==='NEWCOMER'?'仅本俱乐部新顾客首单':'全部顾客';
  const when=String(d.ctype)==='TIME_LIMIT'?'按北京时间在设定时段生效，窗口外不打折':'长期生效';
  showAlert({title:'活动已创建 · 启用中',message:'「'+String(d.title||'').trim()+'」'+who+' · '+when
    +'。C 端结算明细会显示活动优惠名称与金额，订单详情可见活动优惠；装备商城订单不参与。'});
  loadMembers()});
/* 升级赠券（2026-10-26）：会员达到设定等级时自动送一张券；每个等级绑一张券，可换绑。 */
window.addTierGift=async()=>{
  let tiers=[],benefits=[];
  try{[tiers,benefits]=await Promise.all([api(`/api/club/${CLUB}/membership/tiers`),api(`/api/club/${CLUB}/benefits`)])}catch(e){}
  const coupons=(benefits||[]).filter(b=>b.owner_type==='CLUB'&&b.benefit_type==='activity_coupon'&&b.status==='active');
  if(!(tiers||[]).length){showAlert({title:'先创建会员等级',message:'请先在「等级会员 · 条件开通」里创建至少一个等级，再设置升级奖励。'});return}
  if(!coupons.length){showAlert({title:'先创建优惠券',message:'升级赠券只能赠送本俱乐部已创建且启用中的活动优惠券，请先在「俱乐部营销 · 优惠券」里建券并备好库存。'});return}
  const d=await uxForm({title:'设置升级赠券',
    subtitle:'会员升级到选定等级时，系统自动把券送进TA的券包（每个等级绑一张券，每位会员同一等级只送一次）。券必须有库存。',
    fields:[
      {name:'tierId',label:'达到哪个等级时赠送',type:'select',required:true,full:true,
       options:tiers.filter(t=>t.status==='active').map(t=>({value:t.id,label:`${t.name}（Rank ${t.rank}）`})).filter(x=>x.label)},
      {name:'benefitId',label:'赠送哪张券',type:'select',required:true,full:true,
       options:coupons.map(b=>({value:b.id,label:`${b.title} · 库存 ${b.stock==null?'不限':b.stock}`}))}
    ],submitText:'保存升级赠券'});
  if(!d)return;
  await post('/api/club/'+CLUB+'/tier-gift-rules',{tierId:Number(d.tierId),benefitId:Number(d.benefitId)});
  toast('升级赠券已设置');loadMembers();
};
window.toggleTierGift=async(id,enabled)=>uxTask(async()=>{await api(`/api/club/${CLUB}/tier-gift-rules/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!!enabled})});toast(enabled?'规则已启用':'规则已停用');loadMembers()});
window.delTierGift=async id=>uxTask(async()=>{if(!await uxConfirm({title:'删除升级赠券',message:'删除后，已赠送出去的券不受影响，升级不再自动赠券。'}))return;await api(`/api/club/${CLUB}/tier-gift-rules/${id}`,{method:'DELETE'});toast('已删除');loadMembers()});
/* 拉新设定（2026-10-26）：注册会员自动发一张「邀请优惠券」；邀请好友注册双方都得券。 */
window.setReferralCoupon=async()=>{
  let cfg=null;try{cfg=await api(`/api/club/${CLUB}/referral-coupon`)}catch(e){}
  const pool=(cfg&&cfg.pool)||[];
  if(!pool.length){showAlert({title:'先创建优惠券',message:'邀请券必须是本俱乐部创建的活动优惠券，请先在「俱乐部营销 · 优惠券」里建券并备好库存。'});return}
  const d=await uxForm({title:'设定邀请优惠券',
    subtitle:'新会员注册后自动获得一张该券；好友凭会员的邀请码注册，双方各得一张。报名活动时按券面金额抵扣。',
    fields:[{name:'benefitId',label:'邀请优惠券',type:'select',required:true,full:true,
      options:[{value:0,label:'不开启（不自动发邀请券）'}].concat(pool.map(b=>({value:b.id,label:`${b.title} · 库存 ${b.stock==null?'不限':b.stock}`}))),value:(cfg&&cfg.benefitId)||0}],
    submitText:'保存设定'});
  if(!d)return;
  await api(`/api/club/${CLUB}/referral-coupon`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({benefitId:Number(d.benefitId||0)})});
  toast(Number(d.benefitId)?'邀请优惠券已设定':'已关闭邀请券奖励');loadMembers();
};
window.togglePurchaseEnabled=async enabled=>uxTask(async()=>{await post('/api/club/'+CLUB+'/membership/purchase-enabled',{enabled:!!enabled});toast(enabled?'已开通「购买会员」：C 端会员中心会出现可购买的会员卡':'已关闭「购买会员」');loadMembers()});
/* 积分商城（#860 注释更新）：券与积分解耦后，**这里是唯一「用积分换券」的入口**。
   券本身没有积分价；老板先建券，再把已生成的券上架到这里，自己定所需积分。
   例：一张 50 元券可以定 1000 积分兑换，也可以改成 800 积分，与券面无关。 */
window.addMallItem=async()=>uxTask(async()=>{
  let benefits=[];try{benefits=await api(`/api/club/${CLUB}/benefits`)}catch(e){benefits=[]}
  const coupons=(benefits||[]).filter(b=>b.owner_type==='CLUB'&&b.benefit_type==='activity_coupon'&&b.status==='active');
  const d=await uxForm({title:'上架积分兑换商品',
    subtitle:'把已生成的优惠券上架到这里，由你自定义兑换所需积分（例：50 元券 = 1000 积分）。券本身与积分无关，积分价只属于这条商城商品。活动/服务/实物兑换后生成核销码，在本页核销。',
    fields:[
      {name:'itemType',label:'兑换类型',type:'select',full:true,required:true,value:'coupon',options:[{value:'coupon',label:'优惠券（兑换即发到顾客券包）'},{value:'activity',label:'活动（生成核销码）'},{value:'service',label:'一次服务（生成核销码）'},{value:'goods',label:'实物（生成核销码）'}]},
      {name:'refId',label:'关联优惠券（仅优惠券类型）',type:'select',full:true,options:coupons.length?coupons.map(b=>({value:b.id,label:b.title})):[]},
      {name:'title',label:'商品名称',required:true,full:true,value:'露营灯兑换'},
      {name:'pointsCost',label:'兑换所需积分（由老板自定义）',type:'number',min:1,step:1,value:1000,required:true,help:'例：50 元优惠券可定 1000 积分。这个积分价只作用于本条商城商品，不会写回券本身；随时可改。'},
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
