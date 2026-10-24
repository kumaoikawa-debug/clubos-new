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
/* 空态：只有一行灰字会让人以为页面没加载完。给一枚淡淡的图标 + 标题 +
   一句解释，说明「这里本该有什么」，空页面才不像坏页面。 */
function empty(t,s){
  return `<div class="me-empty"><span class="me-empty__ic">${meIcon('box')}</span>`
    +`<b>${esc(t||'')}</b>${s?`<span class="me-empty__sub">${esc(s)}</span>`:''}</div>`;
}

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
  const [mc,oc,addr,vch,claimable,mine,prof]=await Promise.all([
    api(`/api/public/clubs/${CLUB}/member-center?user_id=${USER}`),
    api(`/api/public/users/${USER}/order-center?club_id=${CLUB}`),
    api(`/api/public/users/${USER}/addresses`),
    api(`/api/public/clubs/${CLUB}/vouchers?user_id=${USER}&kind=gear`),
    // 领券中心与券包（2026-10-24 优惠券体系）：券是用户维度的钱包，跨俱乐部。
    api(`/api/public/coupons/claimable?user_id=${USER}&club_id=${CLUB}`).catch(()=>[]),
    api(`/api/public/coupons/mine?user_id=${USER}`).catch(()=>[]),
    api(`/api/public/users/${USER}/profile`).catch(()=>({}))
  ]);
  ME.mc=mc||{}; ME.oc=oc||{};
  if(mc&&mc.member&&mc.member.name) ME.name=mc.member.name;
  ME.addr=Array.isArray(addr)?addr:(addr&&addr.items)||[];
  ME.vch =Array.isArray(vch)?vch:(vch&&vch.items)||[];
  ME.claimable=Array.isArray(claimable)?claimable:[];
  ME.mineCoupons=Array.isArray(mine)?mine:[];
  ME.city=(prof&&prof.city)||'';
  ME.ts=Date.now();
  ME.cardNo='YT'+String(CLUB).padStart(2,'0')+String(USER).padStart(4,'0');
  if(!ME.club){ try{ ME.club=await api(`/api/public/clubs/${CLUB}`); }catch(e){ ME.club=ME.club||{}; } }
}
const meWallet   =()=> (ME.mc&&ME.mc.wallet)||{};
const meTiers    =()=> (ME.mc&&ME.mc.tiers)||[];
const meBenefits =()=> (ME.mc&&ME.mc.benefits)||[];
const meGearDiscount=()=> (ME.mc&&ME.mc.gearDiscount)||null;
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
/* 线稿图标（与底部导航同一套笔画：1.7px、圆角、无填充）。
   四宫格/订单五态原来只有文字，一行四个纯文字块在手机上像表格 —— 加图标才有"入口"的可读性。 */
const ME_IC={
  regs:'<path d="M4.6 8.2h14.8v11.2H4.6z"/><path d="M8.2 4.6v4M15.8 4.6v4"/><path d="m9.2 13.6 2 2 3.6-3.8"/>',
  gift:'<path d="M4.4 9.4h15.2v9.8H4.4z"/><path d="M4.4 9.4h15.2M12 9.4v9.8"/><path d="M12 9.4S9.6 4.6 7.6 5.8c-1.7 1-.8 3.6 4.4 3.6Zm0 0s2.4-4.8 4.4-3.6c1.7 1 .8 3.6-4.4 3.6Z"/>',
  pin:'<path d="M12 20s6.2-5.6 6.2-10.1A6.2 6.2 0 0 0 5.8 9.9C5.8 14.4 12 20 12 20Z"/><circle cx="12" cy="9.8" r="2.2"/>',
  coupon:'<path d="M4 8.6h16v2.6a1.8 1.8 0 0 0 0 3.6v2.6H4v-2.6a1.8 1.8 0 0 0 0-3.6z"/><path d="M14.4 11.4h3.2M14.4 14h3.2"/>',
  wait:'<path d="M4.4 7.4h15.2v11H4.4z"/><path d="M4.4 10.6h15.2"/><path d="M7.4 14.6h3.2"/>',
  send:'<path d="M4.4 8h9.2v8H4.4z"/><path d="M13.6 10.4h3.6l2.4 2.6v3H13.6z"/><path d="M4.4 16h1.4M17.6 16h1.9"/>',
  truck:'<path d="M2.8 7.4h9.6v8.2H2.8z"/><path d="M12.4 10h4l3 3.1v2.5h-7z"/><circle cx="6" cy="17.6" r="1.5"/><circle cx="16.4" cy="17.6" r="1.5"/>',
  star:'<path d="m12 4.8 2.3 4.7 5.2.7-3.8 3.6.9 5.1-4.6-2.5-4.6 2.5.9-5.1L4.5 10.2l5.2-.7z"/>',
  refund:'<path d="M4.6 10.4a7.4 7.4 0 1 1 2 6"/><path d="M4.4 5.6v4.8h4.8"/>',
  box:'<path d="M4.4 8.6 12 4.8l7.6 3.8v6.8L12 19.2 4.4 15.4z"/><path d="M4.4 8.6 12 12.4l7.6-3.8M12 12.4v6.8"/>',
  phone:'<path d="M7.2 4.8h2.7l1.4 3.4-2 1.3a9.6 9.6 0 0 0 5 5l1.3-2 3.4 1.4v2.7a1.9 1.9 0 0 1-2.1 1.9A15.5 15.5 0 0 1 5.3 6.9a1.9 1.9 0 0 1 1.9-2.1Z"/>',
  chat:'<path d="M4.6 6.4h14.8v9.4H9.6l-4 3.2v-3.2H4.6z"/><path d="M8.4 10.2h7.2M8.4 12.8h4.4"/>'
};
function meIcon(k){return `<svg viewBox="0 0 24 24" aria-hidden="true">${ME_IC[k]||''}</svg>`;}
/* 升级进度（0-100）：当前档与下一档之间，按「消费额」或「场次」算比例。
   到最高档 = 100%，数据异常时给 0 而不是 NaN（width:NaN% 会让整条进度条消失）。 */
function meProgPct(){
  const w=meWallet(), tiers=meTiers();
  const cur=tiers.find(t=>String(t.name)===(w.memberLevel||''))||null;
  const nx=tiers.filter(t=>(Number(t.rank)||0)>(Number(cur&&cur.rank)||0))
                .sort((a,b)=>(Number(a.rank)||0)-(Number(b.rank)||0))[0]||null;
  if(!nx) return 100;
  const base=Number(cur&&cur.min_activity_spend)||0, top=Number(nx.min_activity_spend)||0;
  const v=Number(w.lifetimeActivitySpend)||0;
  if(top>base){
    const p=Math.round((v-base)/(top-base)*100);
    return isFinite(p)?Math.max(0,Math.min(100,p)):0;
  }
  const cb=Number(cur&&cur.min_activity_count)||0, cn=Number(nx.min_activity_count)||0;
  const n=Number(w.activityCount)||0;
  if(cn>cb){const p=Math.round((n-cb)/(cn-cb)*100);return isFinite(p)?Math.max(0,Math.min(100,p)):0;}
  return 0;
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
  setT('meGrow', (Math.round(Number(w.lifetimeActivitySpend)||0)).toLocaleString('zh-CN'));
  setT('meUp', meUpTip());
  /* 0% 的进度条只是页面上一条没有含义的灰线（新人一进来就是 0），
     直接藏掉；有进展才画 —— 那时它才真的在表达「还差多少」。 */
  const pg=document.getElementById('meProg');
  if(pg){
    const pct=meProgPct();
    pg.style.width=pct+'%';
    if(pg.parentElement) pg.parentElement.style.display=pct>0?'':'none';
  }
  drawMeQr('meQr', location.origin+'/web?club_id='+CLUB+'&user='+USER+'#member');
  renderMeQuad();
  renderMeOrders();
}
function renderMeQuad(){
  const box=document.getElementById('meQuad'); if(!box)return;
  const cells=[
    ['我的报名', meActivity().length, 'myregs', 'regs'],
    ['会员权益', meBenefits().length, 'member', 'gift'],
    ['收货地址', ME.addr.length, 'address', 'pin'],
    ['优惠券', ME.vch.length, 'coupons', 'coupon']
  ];
  box.innerHTML=cells.map(([t,n,go,ic])=>
    `<button class="wme-quad__c" type="button" onclick="pushMe('${go}')">
       <span class="wme-quad__ic">${meIcon(ic)}${n?`<i class="wme-quad__n">${n>99?'99+':n}</i>`:''}</span>
       <span class="wme-quad__t">${esc(t)}</span>
     </button>`).join('');
}
function renderMeOrders(){
  const box=document.getElementById('meOrders'); if(!box)return;
  const c=meOrderCounts();
  const order=[['待付款',c.pending,'pending','wait'],['待发货',c.paid,'paid','send'],
               ['待收货',c.shipped,'shipped','truck'],['待评价',c.completed,'completed','star'],
               ['退款',c.refund,'refund','refund']];
  box.innerHTML=order.map(([t,n,st,ic])=>
    `<button class="wme-order" type="button" onclick="pushMe('orders','${st}')">
       <span class="wme-order__ic">${meIcon(ic)}${n?`<i class="wme-order__n">${n>99?'99':n}</i>`:''}</span>
       <span class="wme-order__t">${esc(t)}</span>
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
const SUB_ME_TITLE={myregs:'我的报名',member:'会员权益',address:'收货地址',coupons:'优惠券',orders:'商城订单',aftersale:'售后 / 退款',service:'联系我们',about:'关于俱乐部',membercard:'会员信息'};
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
  const title=document.getElementById('meSubTitle'); if(title)title.textContent=top.k==='about'?('关于'+((ME.club&&ME.club.name)||'俱乐部')):(SUB_ME_TITLE[top.k]||'');
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

/* ── 出行指引（2026-10-22 用户反馈「分车、活动通知保存之后能同步到 C 端用户吗」）───
   集合 / 应急电话 / 带队人 / 本人分车 / 已发送通知。数据来自俱乐部端「活动执行」页的
   团期执行数据，后端只在真有内容时才下发 trip；一块都没有时整块不渲染 —— 空壳卡片
   只会让顾客以为页面缺了东西。活动详情页那句「出发前会在订单里给出集合与联络方式」
   从这里开始才是真话。只给已支付的订单：退款后还印集合信息只会添堵。 */
function meTrip(o){
  if(o.status!=='paid')return '';
  const t=o.trip||{}; const rows=[];
  if(t.meeting)rows.push(`<div class="me-trip__row"><span>集合</span><b>${esc(t.meeting.time||'时间待定')} · ${esc(t.meeting.location||'地点待定')}</b></div>`);
  if(t.emergencyPhone)rows.push(`<div class="me-trip__row"><span>应急电话</span><b>${esc(t.emergencyPhone)}</b></div>`);
  if(t.leaders&&t.leaders.length)rows.push(`<div class="me-trip__row"><span>带队</span><b>${t.leaders.map(l=>esc(l.name)+(l.role&&l.role!=='领队'?'（'+esc(l.role)+'）':'')).join('、')}</b></div>`);
  /* 分车按参加人逐人显示：同一天报名的一家人可能被分在不同车；已退出的不计。 */
  const veh=(o.participants||[]).filter(p=>p.status!=='refunded'&&t.vehicles&&t.vehicles[String(p.id)])
    .map(p=>esc(p.name)+' · '+esc(t.vehicles[String(p.id)]));
  if(veh.length)rows.push(`<div class="me-trip__row"><span>分车</span><b>${veh.join('；')}</b></div>`);
  const notices=(t.notices||[]).map(n=>`<div class="me-trip__notice"><b>${esc(n.title)}</b><p>${esc(n.content)}</p>${n.sentAt?`<i>${esc(String(n.sentAt).slice(5,16))}</i>`:''}</div>`).join('');
  if(!rows.length&&!notices)return '';
  return `<div class="me-trip"><div class="me-trip__hd">出行指引</div>${rows.join('')}${notices}</div>`;
}

/* ── 通用订单卡（装备 / 活动）─────────────────────────────────────────── */
function meOrderCard(o){
  const kind=o._kind||o.orderKind||(o.items?'gear':'activity');
  let inner='', acts='';
  if(kind==='gear'){
    /* 单件订单的商品名已经做了标题，明细里再列一遍就是同一行话说两次 ——
       单件只报「共 N 件」，多件才逐行列商品。 */
    const its=o.items||[];
    const totalQty=its.reduce((n,i)=>n+Number(i.quantity||0),0);
    inner=`<div class="me-card2__meta">订单号 #${esc(o.id)}${totalQty?' · 共 '+totalQty+' 件':''}</div>`+
      (its.length>1?its.map(i=>`<div class="me-item"><span>${esc(i.product_name)}</span><b>×${i.quantity}</b></div>`).join(''):'')+
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
      `<div class="me-card2__amt">实付 <b>${money(o.amount||0)}</b></div>`+
      meTrip(o);
    if(o.status==='paid' && ['none','rejected'].includes(o.refund_status||'none')){
      const rq=o.refundQuote;
      acts = rq&&rq.eligible?`<button class="me-btn me-btn--danger" onclick="requestActivityRefund(${o.id},${rq.cashRefundAmount||0},${rq.cashRefundPercent||0})">申请退款</button>`:`<span class="me-note">当前不可退款${rq&&rq.reason?' · '+esc(rq.reason):''}</span>`;
    }
  }
  /* 标题用**真实主体**（商品名 / 活动名），不是「装备订单」这种类别词 ——
     类别已经由状态徽标和下面的明细说明，标题再写一遍等于浪费一行最好的位置。 */
  const head=kind==='gear'
    ? ((o.items&&o.items[0]&&o.items[0].product_name)||'装备订单')
    : (o.activity_title||'活动报名');
  return `<div class="me-card2">
    <div class="me-card2__hd"><b>${esc(head)}</b>${meBadge(meStLabel(o.status),meStTone(o.status))}</div>
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
      ${meTrip(o)}
      ${pax?`<div class="me-paxs">${pax}</div>`:''}
      <div class="me-card2__ref">退款：${esc((typeof refundStateText==='function')?refundStateText(o):'')}</div>
      ${act?`<div class="me-card2__ops">${act}</div>`:''}
    </div>`;
  }).join('');
};

SUB_ME.member=function(){
  const tiers=meTiers(), ben=meBenefits(), gd=meGearDiscount();
  /* 装备折扣随俱乐部订阅套餐（平台控制，2026-10-23 起等级不参与）：
     作为一条横幅放在等级列表上方，逐等级的「装备 X 折」不再展示。 */
  const gdHtml=gd&&gd.rate<1
    ? `<div class="me-gd">装备商城 <b>${esc(gd.discountZhe)} 折</b><span>本俱乐部「${esc(gd.planName||'订阅套餐')}」套餐权益 · 购物车与结算自动生效</span></div>`
    : '';
  const tHtml=tiers.map(t=>{
    const cur=String((meWallet().memberLevel)||'');
    const isCur=cur&&String(t.name)===cur;
    return `<div class="me-tier-row${isCur?' on':''}">
      <div class="me-tier-row__hd"><span class="me-tier-row__name">${esc(t.name)}</span>${isCur?'<i class="me-tier-row__cur">当前等级</i>':''}</div>
      <div class="me-tier-row__cond">${esc(t.qualification_mode==='ANY'?'满足其一即可':'需同时满足')} · 消费满 ${money(t.min_activity_spend||0)} 或 参加 ${t.min_activity_count||0} 场</div>
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
  return `${gdHtml}<div class="me-sec-t">会员等级</div>${tHtml||empty('暂无等级')}<div class="me-sec-t">会员权益</div>${bHtml}`;
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
/* 券包（2026-10-24）：领券中心 + 我的券 + 城市设置。
   券面用通用券图模板渲染（coupon-faces.js），平台端发的券在这里也能领、也能分享。 */
function meCouponFace(v,claimable){
  const face=window.uxCouponFace?window.uxCouponFace({
    title:v.title,imageKey:v.image_key||v.imageKey,
    cashValue:(v.cash_value!=null?v.cash_value:v.cashValue),
    thresholdAmount:(v.threshold_amount!=null?v.threshold_amount:v.thresholdAmount),
    mode:v.mode,benefitType:v.benefit_type||v.benefitType,
    targetType:v.target_type||v.targetType,targetCity:v.target_city||v.targetCity,
    validTo:v.valid_to||v.validTo,voucherCode:v.voucher_code||v.voucherCode,
    status:v.expired?'expired':(v.status==='used'?'used':'')
  }):'';
  const code=v.voucher_code||v.voucherCode||'';
  let act='';
  if(claimable) act=`<button class="me-btn me-btn--sm" onclick="meClaimCoupon(${v.id})">立即领取</button>`;
  else if(v.status==='issued'&&!v.expired&&code) act=`<button class="me-btn me-btn--sm" onclick="meShareCoupon('${esc(code)}','${esc(v.title||'优惠券')}')">分享</button>`;
  return `<div class="me-cpnitem">${face}<div class="me-cpnitem__ops">${act}</div></div>`;
}
SUB_ME.coupons=function(){
  const claimable=ME.claimable||[];
  const mine=ME.mineCoupons||[];
  const usable=mine.filter(v=>v.status==='issued'&&!v.expired);
  const spent=mine.filter(v=>v.status!=='issued'||v.expired);
  const head=`<div class="me-cpn__city">所在城市：<b>${esc(ME.city||'未设置')}</b>
      <button class="me-btn me-btn--sm" onclick="meSetCity()">${ME.city?'修改':'去设置'}</button>
      <span class="me-cpn__hint">按城市定向的优惠券按这里圈人</span></div>`;
  const parts=[];
  if(claimable.length) parts.push(`<div class="me-cpn__grp"><h4>可领取</h4><div class="me-cpn__list">${claimable.map(v=>meCouponFace(v,true)).join('')}</div></div>`);
  if(usable.length) parts.push(`<div class="me-cpn__grp"><h4>我的优惠券（${usable.length}）</h4><div class="me-cpn__list">${usable.map(v=>meCouponFace(v,false)).join('')}</div></div>`);
  if(spent.length) parts.push(`<div class="me-cpn__grp"><h4>已使用 / 已过期</h4><div class="me-cpn__list">${spent.map(v=>meCouponFace(v,false)).join('')}</div></div>`);
  if(!parts.length) return head+empty('还没有优惠券，去活动页或商城领一张');
  return head+parts.join('');
};
async function meClaimCoupon(id){
  try{
    const r=await api(`/api/public/coupons/${id}/claim`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:USER,clubId:CLUB})});
    toast('已领取 '+(window.uxCouponMoney?window.uxCouponMoney(r.cashValue||0):('¥'+(r.cashValue||0)))+' 优惠券');
    ME.ts=0; await meLoadAll(true); renderMeSub();
  }catch(e){ toast((e&&e.message)||e); }
}
async function meShareCoupon(code,title){
  if(!code) return;
  const text=(title||'优惠券')+'｜券码 '+code+'｜报名或买装备时直接使用';
  try{ if(navigator.share){ await navigator.share({title:title||'优惠券',text}); return; } }catch(e){}
  if(typeof meCopy==='function'){ meCopy(text,'券码已复制'); return; }
  try{ await navigator.clipboard.writeText(text); toast('券码已复制：'+code); }
  catch(e){ showAlert({title:'券码',message:'请复制券码使用：'+code}); }
}
async function meSetCity(){
  const cities=(window.CLUBOS_CITIES||[]);
  const opts=[{value:'',label:'不设置'}].concat(cities.map(c=>({value:c,label:c})));
  const v=await showForm({title:'设置所在城市',subtitle:'城市定向的优惠券按注册城市发放（例：成都区域的用户可领专属券）。',fields:[{name:'city',label:'所在城市',type:'select',options:opts,value:ME.city||''}],submitText:'保存'});
  if(!v) return;
  try{
    await api(`/api/public/users/${USER}/city`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({city:v.city})});
    ME.city=v.city; ME.ts=0; toast('已保存');
    await meLoadAll(true); renderMeSub();
  }catch(e){ toast((e&&e.message)||e); }
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
/* ── 联系我们 / 关于：内容由俱乐部在后台自定义 ────────────────────────────
   这两页原来整页是前端写死的占位文案：服务时间「9:00 - 21:00」、官网
   topoutdoor.cn、「户外俱乐部一站式服务平台」。顾客照着假时段打过去没人接，
   还以为是自己的问题。现在后端只下发老板真的填了的字段（c_profile.public_view），
   所以这里一律「有值才渲染这一行」—— 空字段宁可不出现，也不回退成假号码。 */
function meProfile(){
  const p=(ME.club&&ME.club.profile)||{};
  return {c:p.contact||{},a:p.about||{}};
}
function meCopy(text,label){
  const t=String(text||'').trim();if(!t)return;
  const done=()=>{if(window.toast)toast((label||'内容')+'已复制：'+t)};
  const manual=()=>{if(window.toast)toast('复制失败，请长按手动复制：'+t)};
  const fallback=()=>{
    try{
      const ta=document.createElement('textarea');ta.value=t;ta.setAttribute('readonly','');
      ta.style.cssText='position:fixed;top:-999px;opacity:0';document.body.appendChild(ta);
      ta.select();document.execCommand('copy');ta.remove();done();
    }catch(e){manual()}
  };
  if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(t).then(done).catch(fallback);
  else fallback();
}
window.meCopy=meCopy;
/* 复制按钮统一走委托：把微信号/邮箱拼进 onclick 字符串会被引号打断（值里带 ' 就炸）。 */
document.addEventListener('click',function(e){
  const b=e.target&&e.target.closest?e.target.closest('[data-copy]'):null;
  if(b)meCopy(b.getAttribute('data-copy'),b.getAttribute('data-copy-label')||'内容');
});

SUB_ME.service=function(){
  const c=ME.club||{},p=meProfile().c;
  /* 客服电话没单独填时，用「设置 → 联系电话」兜底：同一个老板的号，不用填两遍。 */
  const phone=String(p.phone||c.contactPhone||'').trim();
  const tel=phone.replace(/[^0-9+]/g,'');
  const wechat=String(p.wechat||'').trim();
  const email=String(p.email||'').trim();
  const hours=String(p.hours||'').trim();
  const address=String(p.address||'').trim();
  const note=String(p.note||'').trim();
  const name=String(c.name||'俱乐部');
  const tag=String((meProfile().a.tagline)||c.slogan||'').trim();

  const acts=[];
  if(phone&&tel)acts.push(`<a class="me-svc__act" href="tel:${esc(tel)}">${meIcon('phone')}拨打电话</a>`);
  if(wechat)acts.push(`<button class="me-svc__act" type="button" data-copy="${esc(wechat)}" data-copy-label="微信号">${meIcon('chat')}复制微信号</button>`);

  const rows=[];
  if(phone)rows.push(`<div class="me-info__row"><span>客服电话</span>${tel?`<a href="tel:${esc(tel)}">${esc(phone)}</a>`:`<b>${esc(phone)}</b>`}</div>`);
  if(wechat)rows.push(`<div class="me-info__row"><span>微信</span><button class="me-copy" type="button" data-copy="${esc(wechat)}" data-copy-label="微信号">${esc(wechat)}<i>复制</i></button></div>`);
  if(email)rows.push(`<div class="me-info__row"><span>邮箱</span><a href="mailto:${esc(email)}">${esc(email)}</a></div>`);
  if(hours)rows.push(`<div class="me-info__row"><span>服务时间</span><b>${esc(hours)}</b></div>`);
  if(address)rows.push(`<div class="me-info__row"><span>地址</span><b class="me-info__addr">${esc(address)}</b></div>`);

  return `<div class="me-svc">
      <div class="me-svc__hd">
        <span class="me-svc__ava">${esc(name.slice(0,1))}</span>
        <div><b>${esc(name)}</b>${tag?`<em>${esc(tag)}</em>`:''}</div>
      </div>
      ${acts.length?`<div class="me-svc__acts">${acts.join('')}</div>`:''}
      ${rows.length?`<div class="me-info">${rows.join('')}</div>`
        :empty('暂未公布联系方式','报名后可在「我的报名」里查看集合地点与联络人')}
      ${note?`<p class="me-tip">${esc(note)}</p>`:''}
    </div>`;
};
SUB_ME.about=function(){
  const c=ME.club||{},a=meProfile().a;
  const name=String(c.name||'俱乐部');
  const tag=String(a.tagline||c.slogan||'').trim();
  const hl=(Array.isArray(a.highlights)?a.highlights:[]).slice(0,6).map(t=>String(t||'').trim()).filter(Boolean);
  const paras=String(a.intro||'').split(/\n{2,}/).map(t=>t.trim()).filter(Boolean);
  const website=String(a.website||'').trim();
  let since=String(a.since||'').trim();
  if(since&&!/年$/.test(since))since+=' 年';

  const rows=[];
  if(since)rows.push(`<div class="me-info__row"><span>成立</span><b>${esc(since)}</b></div>`);
  if(website)rows.push(`<div class="me-info__row"><span>官网</span><a href="${esc(website)}" target="_blank" rel="noopener">${esc(website.replace(/^https?:\/\//,''))}</a></div>`);
  rows.push(`<div class="me-info__row"><span>版本</span><b>v0.25</b></div>`);

  return `<div class="me-about">
      <div class="me-about__logo">${esc(name)}</div>
      ${tag?`<div class="me-about__tag">${esc(tag)}</div>`:''}
      ${hl.length?`<div class="me-about__chips">${hl.map(t=>`<span>${esc(t)}</span>`).join('')}</div>`:''}
      ${paras.length?`<div class="me-about__intro">${paras.map(t=>`<p>${esc(t)}</p>`).join('')}</div>`:''}
      <div class="me-info">${rows.join('')}</div>
    </div>`;
};
SUB_ME.membercard=function(){
  const w=meWallet();
  /* 进度条与统计数据必须放进白卡：原来进度条与「再消费…」直接落在浅灰页底上，
     而它们的颜色是按深色卡写的（浅绿 / 半透明白）→ 几乎看不见。
     卡片用与主页面同一套品牌渐变，二维码同样落在白色圆角方块里。 */
  const grow=Math.round(Number(w.lifetimeActivitySpend)||0).toLocaleString('zh-CN');
  return `<div class="me-mc">
      <div class="me-mc__card">
        <div class="me-mc__top">
          <div class="me-mc__ava">${esc((ME.name||'远')[0])}</div>
          <div class="me-mc__id"><div class="me-mc__tier">${esc(w.memberLevel||'普通会员')}</div><div class="me-mc__no">NO. ${esc(ME.cardNo||'')}</div></div>
          <span class="me-mc__qrtile"><canvas class="me-mc__qr" id="meMcQr" width="92" height="92"></canvas></span>
        </div>
        <div class="me-mc__grow"><b>${grow}</b><em>元</em><span>成长值</span></div>
      </div>
      <div class="me-mc__panel">
        <div class="me-mc__bar"><i style="width:${meProgPct()}%"></i></div>
        <div class="me-mc__up">${esc(meUpTip())}</div>
        <div class="me-mc__rows">
          <div class="me-info__row"><span>可用积分</span><b>${w.clubPoints??0}</b></div>
          <div class="me-info__row"><span>装备积分</span><b>${w.gearPoints??0}</b></div>
          <div class="me-info__row"><span>已参加活动</span><b>${w.activityCount||0} 场</b></div>
        </div>
      </div>
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
