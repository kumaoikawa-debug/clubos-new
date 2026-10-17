/* ===========================================================================
   ClubOS · C 端「我的」整页（参考 PEAKTRACE 小程序「我的」版式）
   ---------------------------------------------------------------------------
   全部 2 级子页面与交互动逻辑对齐 PEAKTRACE 手机壳复刻版：
   - 主页面：深色会员卡（等级 / 卡号 / 二维码 / 成长值 / 升级提示）+ 可用·装备积分
            + 四宫格（我的报名 / 会员权益 / 收货地址 / 优惠券）
            + 商城订单五态（待付款 / 待发货 / 待收货 / 待评价 / 退款）
            + 服务列表（售后退款 / 联系我们 / 关于 / 退出登录）
   - 子页面栈：pushMe(k,param) / popMe()，覆盖整屏；所有 2 级页由 SUB_ME[k] 渲染。

   数据全部来自已实测的公开 API：
     member-center  → wallet / tiers / benefits / redemptions / activityHistory / gearOwned
     order-center   → activityOrders / gearOrders / allOrders / pendingOrders
     addresses      → GET/POST/PATCH/DELETE
     vouchers       → 优惠券列表

   购物车 / 旅行记录 PEAKTRACE 有但 ClubOS 无后端支撑，按「把没用的去掉」删除。

   覆盖纪律（同 web-biz.js）：先捕获旧加载器再覆盖 window.loadMemberCenter / loadOrders，
   wv('wme') 调的就是这两个裸名，覆盖后自动生效。
   =========================================================================== */
(function(){
'use strict';

/* ── 状态缓存 ─────────────────────────────────────────────────────────── */
const ME={ mc:null, oc:null, addr:[], vch:[], club:null, ts:0, cardNo:'', name:'远拓会员' };

/* ── 小工具 ───────────────────────────────────────────────────────────── */
function setT(id,t){const e=document.getElementById(id); if(e)e.textContent=(t==null?'':t);}
function meBadge(t,tone){return `<span class="me-badge me-badge--${tone||'def'}">${esc(t==null?'':t)}</span>`;}
function empty(t,s){return `<div class="me-empty">${esc(t||'')}</div>`+(s?`<div class="me-empty__sub">${esc(s)}</div>`:'');}

const ME_ST={
  pending:{t:'待付款',k:'wait'}, paid:{t:'待发货',k:'info'}, shipped:{t:'待收货',k:'info'},
  completed:{t:'已完成',k:'ok'}, refunded:{t:'已退款',k:'off'}, refunding:{t:'退款中',k:'warn'},
  cancelled:{t:'已取消',k:'off'}, processing:{t:'处理中',k:'warn'}
};
function meStLabel(s){return (ME_ST[s]&&ME_ST[s].t)||s;}
function meStTone(s){return (ME_ST[s]&&ME_ST[s].k)||'def';}
function meIsRefund(o){return /refund/i.test(o.status||'')||(o.afterSalesCases&&o.afterSalesCases.length)||(o.refund_status&&!['none','rejected'].includes(o.refund_status));}
function meAsType(t){return ({refund:'仅退款',return:'退货退款',exchange:'换货'}[t]||t||'退款');}
function meAsSt(s){return ({open:'处理中',awaiting_return:'待退货',returned:'已退货',refunding:'退款中',refunded:'已退款',done:'已完成',rejected:'已驳回',processing:'处理中'}[s]||s||'处理中');}

/* ── 数据获取 ─────────────────────────────────────────────────────────── */
async function meLoadAll(force){
  if(!force && ME.ts && Date.now()-ME.ts<1500 && ME.mc) return;
  const [mc,oc,addr,vch]=await Promise.all([
    api(`/api/public/clubs/${CLUB}/member-center?user_id=${USER}`),
    api(`/api/public/users/${USER}/order-center?club_id=${CLUB}`),
    api(`/api/public/users/${USER}/addresses`),
    api(`/api/public/clubs/${CLUB}/vouchers?user_id=${USER}&kind=gear`)
  ]);
  ME.mc=mc||{}; ME.oc=oc||{};
  if(mc&&mc.member&&mc.member.name) ME.name=mc.member.name;
  ME.addr=Array.isArray(addr)?addr:(addr&&addr.items)||[];
  ME.vch =Array.isArray(vch)?vch:(vch&&vch.items)||[];
  ME.ts=Date.now();
  ME.cardNo='YT'+String(CLUB).padStart(2,'0')+String(USER).padStart(4,'0');
  if(!ME.club){ try{ ME.club=await api(`/api/public/clubs/${CLUB}`); }catch(e){ ME.club=ME.club||{}; } }
}
const meWallet   =()=> (ME.mc&&ME.mc.wallet)||{};
const meTiers    =()=> (ME.mc&&ME.mc.tiers)||[];
const meBenefits =()=> (ME.mc&&ME.mc.benefits)||[];
const meActivity =()=> (ME.oc&&ME.oc.activityOrders)||[];
const meGear     =()=> (ME.oc&&ME.oc.gearOrders)||[];
const mePending  =()=> (ME.oc&&ME.oc.pendingOrders)||[];
const meAll      =()=> (ME.oc&&ME.oc.allOrders)||[];

function meOrderCounts(){
  const g=meGear(),a=meActivity(),p=mePending();
  const c={pending:p.length,paid:0,shipped:0,completed:0,refund:0};
  g.forEach(o=>{ if(o.status==='paid')c.paid++; else if(o.status==='shipped')c.shipped++; else if(o.status==='completed')c.completed++; if(meIsRefund(o))c.refund++; });
  a.forEach(o=>{ if(meIsRefund(o))c.refund++; });
  return c;
}

/* ── 主页面绘制 ───────────────────────────────────────────────────────── */
function meUpTip(){
  const w=meWallet(); const tiers=meTiers();
  const cur=tiers.find(t=>String(t.name)===(w.memberLevel||''))||null;
  const nx=tiers.filter(t=>(Number(t.rank)||0)>(Number(cur&&cur.rank)||0)).sort((a,b)=>(Number(a.rank)||0)-(Number(b.rank)||0))[0]||null;
  if(!nx) return '已达最高等级';
  const needSpend=Math.max(0,(Number(nx.min_activity_spend)||0)-(Number(w.lifetimeActivitySpend)||0));
  if(needSpend>0) return `再消费 ${money(needSpend)} 升级${nx.name}`;
  const needCnt=Math.max(0,(Number(nx.min_activity_count)||0)-(Number(w.activityCount)||0));
  return needCnt>0?`再参加 ${needCnt} 场活动升级${nx.name}`:`已满足${nx.name}条件`;
}
function drawMeQr(canvasId,text){
  const c=document.getElementById(canvasId); if(!c||!window.ClubOSQR)return;
  try{ window.ClubOSQR.draw(c, text); }catch(e){}
}
function mePaintHome(){
  const w=meWallet();
  setT('meClubPts', w.clubPoints??0);
  setT('meGearPts', w.gearPoints??0);
  setT('meName', ME.name);
  setT('meTier', w.memberLevel||'普通会员');
  setT('meCardNo', 'NO. '+(ME.cardNo||'—'));
  setT('meGrow', Math.round(Number(w.lifetimeActivitySpend)||0));
  setT('meUp', meUpTip());
  drawMeQr('meQr', location.origin+'/web?club_id='+CLUB+'&user='+USER+'#member');
  renderMeQuad();
  renderMeOrders();
}
function renderMeQuad(){
  const box=document.getElementById('meQuad'); if(!box)return;
  const cells=[
    ['我的报名', meActivity().length, 'myregs'],
    ['会员权益', meBenefits().length, 'member'],
    ['收货地址', ME.addr.length, 'address'],
    ['优惠券', ME.vch.length, 'coupons']
  ];
  box.innerHTML=cells.map(([t,n,go])=>
    `<button class="wme-quad__c" type="button" onclick="pushMe('${go}')">
       <span class="wme-quad__t">${esc(t)}</span>${n?`<i class="wme-quad__n">${n}</i>`:''}
     </button>`).join('');
}
function renderMeOrders(){
  const box=document.getElementById('meOrders'); if(!box)return;
  const c=meOrderCounts();
  const order=[['待付款',c.pending,'pending'],['待发货',c.paid,'paid'],['待收货',c.shipped,'shipped'],['待评价',c.completed,'completed'],['退款',c.refund,'refund']];
  box.innerHTML=order.map(([t,n,st])=>
    `<button class="wme-order" type="button" onclick="pushMe('orders','${st}')">
       <span class="wme-order__t">${esc(t)}</span>${n?`<i class="wme-order__n">${n}</i>`:''}
     </button>`).join('');
}

/* ── 覆盖 wv('wme') 触发的加载器 ──────────────────────────────────────── */
const _prevMC=window.loadMemberCenter, _prevLO=window.loadOrders;
async function loadMemberCenter(){
  try{ await meLoadAll(false); }catch(e){}
  mePaintHome();
  if(meStackOpen()) renderMeSub();
}
async function loadOrders(){
  try{ await meLoadAll(true); }catch(e){}
  mePaintHome();
  if(meStackOpen()) renderMeSub();
}
window.loadMemberCenter=loadMemberCenter;
window.loadOrders=loadOrders;

/* ── 二级页栈 ─────────────────────────────────────────────────────────── */
const meStack=[];
const SUB_ME={};
const SUB_ME_TITLE={myregs:'我的报名',member:'会员权益',address:'收货地址',coupons:'优惠券',orders:'商城订单',aftersale:'售后 / 退款',service:'联系我们',about:'关于远拓户外',membercard:'会员信息'};
const SUB_ME_AFTER={};
function meStackOpen(){return meStack.length>0;}
function meEnsureStack(){
  let s=document.getElementById('meStack');
  if(!s){
    s=document.createElement('div'); s.id='meStack'; s.className='me-stack'; s.hidden=true;
    s.innerHTML=`<header class="me-subnav"><button class="me-back" type="button" onclick="popMe()">‹ 返回</button><strong class="me-subtitle" id="meSubTitle"></strong></header><div class="me-subbody" id="meSubBody"></div>`;
    const shell=document.querySelector('.web-shell'); (shell||document.body).appendChild(s);
  }
  return s;
}
function pushMe(k,param){ meEnsureStack(); meStack.push({k,param}); renderMeSub(); }
function popMe(){ if(meStack.length)meStack.pop(); renderMeSub(); }
function meSetTab(st){ if(meStack.length){ meStack[meStack.length-1].param=st; renderMeSub(); } }
function renderMeSub(){
  const s=meEnsureStack();
  if(!meStack.length){ s.hidden=true; const sh=document.querySelector('.web-shell'); if(sh)sh.classList.remove('me-sub-open'); return; }
  s.hidden=false;
  const sh=document.querySelector('.web-shell'); if(sh)sh.classList.add('me-sub-open');
  const top=meStack[meStack.length-1];
  const title=document.getElementById('meSubTitle'); if(title)title.textContent=SUB_ME_TITLE[top.k]||'';
  const body=document.getElementById('meSubBody');
  if(body){
    body.innerHTML=(typeof SUB_ME[top.k]==='function')?SUB_ME[top.k](top.param):'<div class="me-empty">页面不存在</div>';
    body.scrollTop=0;
    const af=SUB_ME_AFTER[top.k]; if(typeof af==='function'){ try{af(top.param);}catch(e){} }
  }
}
/* wv 切走任意 tab 时，若子页栈开着就清空（#meStack 挂在 .web-shell 下，不随 #wme 隐藏） */
const _wv=window.wv;
window.wv=function(id,b){
  if(meStackOpen()){ meStack.length=0; renderMeSub(); }
  return _wv?_wv(id,b):undefined;
};

/* ── 通用订单卡（装备 / 活动）─────────────────────────────────────────── */
function meOrderCard(o){
  const kind=o._kind||o.orderKind||(o.items?'gear':'activity');
  let inner='', acts='';
  if(kind==='gear'){
    inner=`<div class="me-card2__meta">装备订单 #${esc(o.id)}</div>`+
      (o.items||[]).map(i=>`<div class="me-item"><span>${esc(i.product_name)}</span><b>×${i.quantity}</b></div>`).join('')+
      (o.shipping_consignee?`<div class="me-card2__meta">${esc(o.shipping_consignee)} ${esc(o.shipping_phone||'')} · ${esc(o.shipping_address||'')}</div>`:'')+
      `<div class="me-card2__amt">实付 <b>${money(o.total||0)}</b></div>`+
      (o.carrier||o.tracking_no?`<div class="me-card2__meta">${esc(o.carrier||'')} ${esc(o.tracking_no||'')}</div>`:'');
    if(o.status==='shipped') acts+=`<button class="me-btn me-btn--primary" onclick="confirmGearReceipt(${o.id})">确认收货</button>`;
    if(o.status!=='refunded') acts+=`<button class="me-btn" onclick='requestGearAfterSales(${JSON.stringify(o).replace(/'/g,"&#39;")})'>申请售后</button>`;
    const cases=(o.afterSalesCases||[]).map(a=>`<div class="me-case">${meBadge(meAsType(a.case_type),'info')}${meBadge(meAsSt(a.status),meStTone(a.status))}${a.return_tracking_no?`<span class="me-case__t">退货 ${esc(a.return_tracking_no)}</span>`:''}${a.status==='awaiting_return'?`<button class="me-btn me-btn--sm" onclick="submitReturn('${esc(a.id)}')">填退货物流</button>`:''}</div>`).join('');
    inner+=cases;
  } else {
    inner=`<div class="me-card2__meta">${esc(o.activity_title||'活动报名')}</div>`+
      `<div class="me-card2__meta">${esc(o.occurrence_label||o.start_at||'团期待定')} · ${o.participantCount||(o.participants||[]).length} 人</div>`+
      `<div class="me-card2__amt">实付 <b>${money(o.amount||0)}</b></div>`;
    if(o.status==='paid' && ['none','rejected'].includes(o.refund_status||'none')){
      const rq=o.refundQuote;
      acts = rq&&rq.eligible?`<button class="me-btn me-btn--danger" onclick="requestActivityRefund(${o.id},${rq.cashRefundAmount||0},${rq.cashRefundPercent||0})">申请退款</button>`:`<span class="me-note">当前不可退款${rq&&rq.reason?' · '+esc(rq.reason):''}</span>`;
    }
  }
  return `<div class="me-card2">
    <div class="me-card2__hd"><b>${kind==='gear'?'装备订单':'活动报名'}</b>${meBadge(meStLabel(o.status),meStTone(o.status))}</div>
    ${inner}
    <div class="me-card2__ref">退款：${esc((typeof refundStateText==='function')?refundStateText(o):'')}</div>
    ${acts?`<div class="me-card2__ops">${acts}</div>`:''}
  </div>`;
}

/* ── 子页面实现 ───────────────────────────────────────────────────────── */
SUB_ME.myregs=function(){
  const list=meActivity();
  if(!list.length) return empty('暂无活动订单','报名成功后订单会出现在这里');
  return list.map(o=>{
    const ps=o.participants||[];
    let act='';
    if(o.status==='paid' && ['none','rejected'].includes(o.refund_status||'none')){
      const rq=o.refundQuote;
      act = rq&&rq.eligible?`<button class="me-btn me-btn--danger" onclick="requestActivityRefund(${o.id},${rq.cashRefundAmount||0},${rq.cashRefundPercent||0})">申请退款</button>`:`<span class="me-note">当前不可退款${rq&&rq.reason?' · '+esc(rq.reason):''}</span>`;
    }
    const pax=ps.map(p=>`<div class="me-pax"><b>${esc(p.name)}</b>${p.status==='refunded'?meBadge('已退出','off'):''}<span>${esc(p.phone||'')}</span></div>`).join('');
    return `<div class="me-card2">
      <div class="me-card2__hd"><b>${esc(o.activity_title||'活动')}</b>${meBadge(meStLabel(o.status),meStTone(o.status))}</div>
      <div class="me-card2__meta">${esc(o.occurrence_label||o.start_at||'团期待定')} · ${o.participantCount||ps.length} 人</div>
      <div class="me-card2__amt">实付 <b>${money(o.amount||0)}</b></div>
      ${pax?`<div class="me-paxs">${pax}</div>`:''}
      <div class="me-card2__ref">退款：${esc((typeof refundStateText==='function')?refundStateText(o):'')}</div>
      ${act?`<div class="me-card2__ops">${act}</div>`:''}
    </div>`;
  }).join('');
};

SUB_ME.member=function(){
  const tiers=meTiers(), ben=meBenefits();
  const tHtml=tiers.map(t=>{
    const disc=(t.gear_discount&&t.gear_discount<1)?(Math.round(t.gear_discount*1000)/10)+' 折':'原价';
    return `<div class="me-tier-row">
      <div class="me-tier-row__name">${esc(t.name)}</div>
      <div class="me-tier-row__cond">${esc(t.qualification_mode==='ANY'?'满足其一即可':'需同时满足')} · 消费满 ${money(t.min_activity_spend||0)} 或 参加 ${t.min_activity_count||0} 场</div>
      <div class="me-tier-row__disc">装备折扣 ${disc}</div>
    </div>`;
  }).join('');
  const bHtml=ben.length?ben.map(b=>`<div class="me-bnf">
      <div class="me-bnf__hd"><b>${esc(b.title)}</b></div>
      ${b.description?`<p>${esc(b.description)}</p>`:''}
      <div class="me-bnf__ft">
        <div class="me-bnf__cost"><b>${b.points_cost}</b> ${b.points_type==='club'?'活动积分':'装备积分'}${b.cash_value?` · 价值 ${money(b.cash_value)}`:''}</div>
        <button class="me-btn" onclick="redeemBenefit(${b.id})">兑换</button>
      </div>
    </div>`).join(''):empty('暂无可兑换福利','');
  return `<div class="me-sec-t">会员等级</div>${tHtml||empty('暂无等级')}<div class="me-sec-t">会员权益</div>${bHtml}`;
};

SUB_ME.address=function(){
  const list=ME.addr;
  if(!list.length) return `<button class="me-btn me-btn--block" onclick="meAddAddr()">+ 新增收货地址</button>`+empty('还没有收货地址','下单装备时需要一个收货地址');
  const full=a=>(a.full||((a.province||'')+(a.city||'')+(a.district||'')+(a.detail||'')));
  return `<button class="me-btn me-btn--block" onclick="meAddAddr()">+ 新增收货地址</button>`+list.map(a=>`<div class="me-addr">
      <div class="me-addr__top"><b>${esc(a.consignee)}</b><span>${esc(a.phone||'')}</span>${a.is_default?`<i class="me-badge me-badge--ok">默认</i>`:''}</div>
      <div class="me-addr__line">${esc(full(a))}</div>
      <div class="me-addr__ops">
        ${a.is_default?'':`<button class="me-btn me-btn--sm" onclick="meDefaultAddr(${a.id})">设为默认</button>`}
        <button class="me-btn me-btn--sm" onclick="meEditAddr(${a.id})">编辑</button>
        <button class="me-btn me-btn--sm me-btn--danger" onclick="meDelAddr(${a.id})">删除</button>
      </div>
    </div>`).join('');
};
SUB_ME.coupons=function(){
  const list=ME.vch;
  if(!list.length) return empty('暂无优惠券','');
  return list.map(v=>`<div class="me-cpn">
      <div class="me-cpn__val">¥${money(v.cash_value||0)}</div>
      <div class="me-cpn__info"><b>${esc(v.title||'优惠券')}</b>${v.desc?`<span>${esc(v.desc)}</span>`:''}${v.voucher_code?`<span class="me-cpn__code">券码 ${esc(v.voucher_code)}</span>`:''}</div>
    </div>`).join('');
};
SUB_ME.orders=function(st){
  const tab=st||'all';
  const tabs=[['all','全部'],['pending','待付款'],['paid','待发货'],['shipped','待收货'],['completed','待评价'],['refund','退款']];
  const tabHtml=tabs.map(([k,t])=>`<button class="me-tab${k===tab?' on':''}" onclick="meSetTab('${k}')">${esc(t)}</button>`).join('');
  let items=[];
  if(tab==='all') items=meAll().map(x=>(Object.assign({},x.order,{_kind:x.kind})));
  else if(tab==='pending') items=mePending();
  else if(tab==='refund') items=meGear().filter(meIsRefund).concat(meActivity().filter(meIsRefund));
  else items=meGear().filter(o=>o.status===tab);
  const list=items.length?items.map(meOrderCard).join(''):empty('该分类下暂无订单');
  return `<div class="me-tabs">${tabHtml}</div><div class="me-list2">${list}</div>`;
};
SUB_ME.aftersale=function(){
  const cases=[];
  meGear().forEach(o=>(o.afterSalesCases||[]).forEach(a=>cases.push({o,a})));
  meActivity().forEach(o=>{ if(o.refund_status&&!['none','rejected'].includes(o.refund_status)) cases.push({o,a:{status:o.refund_status}}); });
  if(!cases.length) return empty('暂无售后 / 退款','');
  return cases.map(({o,a})=>`<div class="me-card2">
      <div class="me-card2__hd"><b>${esc(o.activity_title||('装备订单 #'+o.id))}</b>${meBadge(meAsType(a.case_type)||'退款','info')}</div>
      <div class="me-card2__meta">状态：${meBadge(meAsSt(a.status),meStTone(a.status))}</div>
      ${a.return_tracking_no?`<div class="me-card2__meta">退货物流 ${esc(a.return_tracking_no)}</div>`:''}
    </div>`).join('');
};
SUB_ME.service=function(){
  const c=ME.club||{};
  return `<div class="me-info">
      <div class="me-info__row"><span>俱乐部</span><b>${esc(c.name||'远拓户外')}</b></div>
      ${c.contact_phone?`<div class="me-info__row"><span>客服电话</span><a href="tel:${esc(c.contact_phone)}">${esc(c.contact_phone)}</a></div>`:''}
      ${c.wechat?`<div class="me-info__row"><span>微信</span><b>${esc(c.wechat)}</b></div>`:''}
      ${c.email?`<div class="me-info__row"><span>邮箱</span><b>${esc(c.email)}</b></div>`:''}
      <div class="me-info__row"><span>服务时间</span><b>9:00 - 21:00</b></div>
    </div>
    <p class="me-tip">出行前请确认装备与身体状况；如需退改，请在订单页发起申请，由平台统一处理。</p>`;
};
SUB_ME.about=function(){
  return `<div class="me-about">
      <div class="me-about__logo">远拓户外</div>
      <p>户外俱乐部一站式服务平台</p>
      <div class="me-info__row"><span>版本</span><b>v0.25</b></div>
      <div class="me-info__row"><span>官网</span><b>topoutdoor.cn</b></div>
    </div>`;
};
SUB_ME.membercard=function(){
  const w=meWallet(), tiers=meTiers();
  const cur=tiers.find(t=>String(t.name)===(w.memberLevel||''))||null;
  const nx=tiers.filter(t=>(Number(t.rank)||0)>(Number(cur&&cur.rank)||0)).sort((a,b)=>(Number(a.rank)||0)-(Number(b.rank)||0))[0]||null;
  const prog=(cur&&nx)?Math.max(0,Math.min(100,Math.round((Number(w.lifetimeActivitySpend||0)/(Number(nx.min_activity_spend)||1))*100))):(nx?0:100);
  return `<div class="me-mc">
      <div class="me-mc__card">
        <div class="me-mc__top">
          <div class="me-mc__ava">${esc((ME.name||'远')[0])}</div>
          <div class="me-mc__id"><div class="me-mc__tier">${esc(w.memberLevel||'普通会员')}</div><div class="me-mc__no">${esc(ME.cardNo||'')}</div></div>
          <canvas class="me-mc__qr" id="meMcQr" width="92" height="92"></canvas>
        </div>
        <div class="me-mc__grow"><b>${money(w.lifetimeActivitySpend||0)}</b><span>成长值（元）</span></div>
      </div>
      <div class="me-mc__bar"><i style="width:${prog}%"></i></div>
      <div class="me-mc__up">${esc(meUpTip())}</div>
      <div class="me-info__row"><span>可用积分</span><b>${w.clubPoints??0}</b></div>
      <div class="me-info__row"><span>装备积分</span><b>${w.gearPoints??0}</b></div>
      <div class="me-info__row"><span>已参加活动</span><b>${w.activityCount||0} 场</b></div>
    </div>`;
};
SUB_ME_AFTER.membercard=function(){ drawMeQr('meMcQr', location.origin+'/web?club_id='+CLUB+'&user='+USER+'#member'); };

/* ── 地址 CRUD ─────────────────────────────────────────────────────────── */
async function meAddAddr(){
  const v=await addrForm(); if(!v)return;
  try{ await api(`/api/public/users/${USER}/addresses`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(v)}); toast('地址已保存'); await meLoadAll(true); renderMeSub(); }
  catch(e){ showAlert({title:'保存失败',message:e.message}); }
}
async function meEditAddr(id){
  const a=(ME.addr||[]).find(x=>String(x.id)===String(id)); if(!a)return;
  const v=await addrForm(a); if(!v)return;
  try{ await api(`/api/public/users/${USER}/addresses/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(v)}); toast('已更新'); await meLoadAll(true); renderMeSub(); }
  catch(e){ showAlert({title:'更新失败',message:e.message}); }
}
async function meDelAddr(id){
  if(!(await showConfirm({title:'删除地址',message:'确认删除这个收货地址？'})))return;
  try{ await api(`/api/public/users/${USER}/addresses/${id}`,{method:'DELETE'}); toast('已删除'); await meLoadAll(true); renderMeSub(); }
  catch(e){ showAlert({title:'删除失败',message:e.message}); }
}
async function meDefaultAddr(id){
  try{ await api(`/api/public/users/${USER}/addresses/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({is_default:true})}); toast('已设为默认'); await meLoadAll(true); renderMeSub(); }
  catch(e){ showAlert({title:'操作失败',message:e.message}); }
}
function addrForm(a){
  a=a||{};
  return showForm({title:a.id?'编辑地址':'新增地址',fields:[
    {name:'consignee',label:'收货人',required:true,value:a.consignee||''},
    {name:'phone',label:'手机号',required:true,value:a.phone||''},
    {name:'province',label:'省',required:true,value:a.province||''},
    {name:'city',label:'市',required:true,value:a.city||''},
    {name:'district',label:'区/县',value:a.district||''},
    {name:'detail',label:'详细地址',required:true,value:a.detail||''}
  ],submitText:'保存'});
}

/* ── 退出登录 ─────────────────────────────────────────────────────────── */
async function meLogout(){
  if(!(await showConfirm({title:'退出登录',message:'确认退出当前账号？'})))return;
  if(window.toast)toast('已退出登录');
  if(window.wv)wv('whome');
}

/* 暴露给 inline onclick */
window.pushMe=pushMe; window.popMe=popMe; window.meSetTab=meSetTab;
window.meAddAddr=meAddAddr; window.meEditAddr=meEditAddr; window.meDelAddr=meDelAddr; window.meDefaultAddr=meDefaultAddr;
window.meLogout=meLogout;

/* 若脚本在 我的 已激活后注入，立即刷新一次 */
if(document.getElementById('wme') && !document.getElementById('wme').hasAttribute('hidden')===false){
  /* 由 wv 触发，无需主动刷 */
}
})();
