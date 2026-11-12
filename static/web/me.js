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
/* 活动报名不是快递：已付款的报名单过去复用装备的「待发货」，顾客会以为漏发了装备、
   反复找客服。活动用出行语义，装备才用电商五态（BUG-CUX-002 / 2026-11-08）。 */
const ACT_ST={
  pending:{t:'待付款',k:'wait'}, paid:{t:'已报名 · 待出行',k:'ok'}, shipped:{t:'待出行',k:'ok'},
  completed:{t:'已出行',k:'ok'}, refunded:{t:'已退款',k:'off'}, refunding:{t:'退款中',k:'warn'},
  cancelled:{t:'已取消',k:'off'}, processing:{t:'处理中',k:'warn'}
};
function meOrderSt(s,kind){const m=(kind==='activity'?ACT_ST:ME_ST)[s];return m?m.t:(s||'');}
function meOrderTone(s,kind){const m=(kind==='activity'?ACT_ST:ME_ST)[s];return m?m.k:'def';}
function meIsRefund(o){return /refund/i.test(o.status||'')||(o.afterSalesCases&&o.afterSalesCases.length)||(o.refund_status&&!['none','rejected'].includes(o.refund_status));}
/* 售后类型 / 状态的中文映射，键必须覆盖 clubos_domain/after_sales.py 的
   CASE_TYPES 与 ACTIVE/FINAL_STATUSES。键写错（如写成 refund/return 而非
   refund_only/return_refund）或漏写状态，会回退把英文枚举原样显示给顾客。 */
function meAsType(t){return ({refund_only:'仅退款',return_refund:'退货退款',exchange:'换货'}[t]||t||'退款');}
function meAsSt(s){return ({
  requested:'已提交', pending_review:'待审核', reviewing:'审核中',
  awaiting_return:'待退货', return_in_transit:'退回途中', returned:'已退货',
  approved_pending_refund:'待退款', refund_processing:'退款中', refund_pending:'待退款',
  refunded:'已退款', exchange_pending_shipment:'待换货发出', exchanged:'已换货',
  completed:'已完成', rejected:'已驳回', cancelled:'已取消', closed:'已关闭',
  open:'处理中', processing:'处理中', refunding:'退款中', done:'已完成',
  partial_requested:'部分退款申请中', partial_processing:'部分退款处理中',
  partial:'部分退款', succeeded:'退款完成'
}[s]||s||'处理中');}

/* ── 数据获取 ─────────────────────────────────────────────────────────── */
async function meLoadAll(force){
  if(!force && ME.ts && Date.now()-ME.ts<1500 && ME.mc) return;
  const [mc,oc,addr,vch,claimable,mine,prof,camp]=await Promise.all([
    api(`/api/public/clubs/${CLUB}/member-center?user_id=${USER}`),
    api(`/api/public/users/${USER}/order-center?club_id=${CLUB}`),
    api(`/api/public/users/${USER}/addresses`),
    api(`/api/public/clubs/${CLUB}/vouchers?user_id=${USER}&kind=gear`),
    // 领券中心与券包（2026-10-24 优惠券体系）：券是用户维度的钱包，跨俱乐部。
    api(`/api/public/coupons/claimable?user_id=${USER}&club_id=${CLUB}`).catch(()=>[]),
    api(`/api/public/coupons/mine?user_id=${USER}`).catch(()=>[]),
    api(`/api/public/users/${USER}/profile`).catch(()=>({})),
    // 营地订单（#799 S3）：与活动订单并列归入「我的订单」。
    api(`/api/public/clubs/${CLUB}/camp-orders?user_id=${USER}`).catch(()=>({orders:[]}))
  ]);
  ME.mc=mc||{}; ME.oc=oc||{};
  if(mc&&mc.member&&mc.member.name) ME.name=mc.member.name;
  ME.addr=Array.isArray(addr)?addr:(addr&&addr.items)||[];
  ME.vch =Array.isArray(vch)?vch:(vch&&vch.items)||[];
  ME.claimable=Array.isArray(claimable)?claimable:[];
  ME.mineCoupons=Array.isArray(mine)?mine:[];
  ME.city=(prof&&prof.city)||'';
  ME.avatar=(prof&&prof.avatarUrl)||''; ME.wechatAvatar=(prof&&prof.wechatAvatarUrl)||'';
  ME.ts=Date.now();
  ME.campOrders=(camp&&camp.orders)||[];
  ME.cardNo='YT'+String(CLUB).padStart(2,'0')+String(USER).padStart(4,'0');
  if(!ME.club){ try{ ME.club=await api(`/api/public/clubs/${CLUB}`); }catch(e){ ME.club=ME.club||{}; } }
  /* 角标要跟着数据走：顾客一进小程序就停在首页，不点「我的」也得看得见有新通知。 */
  try{ paintNoticeHint(); }catch(e){}
}
const meWallet   =()=> (ME.mc&&ME.mc.wallet)||{};
const meTiers    =()=> (ME.mc&&ME.mc.tiers)||[];
const meBenefits =()=> (ME.mc&&ME.mc.benefits)||[];
const meGearDiscount=()=> (ME.mc&&ME.mc.gearDiscount)||null;
const mePurchase=()=> (ME.mc&&ME.mc.membershipPurchase)||{};
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

/* ── 会员码：默认隐藏，点击会员卡上的「会员码 ›」弹出底部抽屉 ───────────────
   抽屉内含头像、用户名、会员码编号（条状装饰 + 文字）、二维码（真实会员码）、
   每 60 秒自动刷新提示。刷新为前端按 60s 时间桶重绘二维码 —— 无后端签名令牌时
   仅为展示刷新动作（二维码内容随桶变化）；关闭抽屉时清掉定时器。 */
let _meCodeTimer=null;
function closeMemberCodeSheet(){
  const el=document.getElementById('meCodeSheet'); if(el)el.remove();
  document.documentElement.classList.remove('w-sheet-open');
  if(_meCodeTimer){ clearInterval(_meCodeTimer); _meCodeTimer=null; }
}
function openMemberCodeSheet(){
  let el=document.getElementById('meCodeSheet');
  if(!el){
    el=document.createElement('div');el.id='meCodeSheet';el.className='w-sheet';
    el.innerHTML=`<div class="w-sheet__mask" data-close></div><div class="w-sheet__panel me-code" role="dialog" aria-modal="true" aria-label="会员码">`
      +`<button type="button" class="w-sheet__x" data-close aria-label="关闭">×</button>`
      +`<div class="me-code__body">
          <div class="me-code__av" id="meCodeAv"></div>
          <div class="me-code__name" id="meCodeName"></div>
          <div class="me-code__bar" id="meCodeBar" aria-hidden="true"></div>
          <div class="me-code__no" id="meCodeNo"></div>
          <div class="me-code__qrwrap"><canvas class="me-code__qr" id="meCodeQr" width="200" height="200"></canvas></div>
          <div class="me-code__tip">每 60 秒自动刷新 · 请向工作人员出示</div>
        </div></div>`;
    document.body.appendChild(el);
    document.documentElement.classList.add('w-sheet-open');
    el.addEventListener('click',e=>{ const t=e.target.closest('[data-close]'); if(t&&el.contains(t))closeMemberCodeSheet(); });
  }
  const name=ME.name||'会员';
  const no='NO. '+(ME.cardNo||'—');
  const nm=document.getElementById('meCodeName'); if(nm)nm.textContent=name;
  const nov=document.getElementById('meCodeNo'); if(nov)nov.textContent=no;
  const av=document.getElementById('meCodeAv'); if(av)av.innerHTML=meAvatarInner(name,(ME.avatar||''));
  /* 条状装饰：依会员编号生成确定性条纹（纯展示，不宣称可扫描）。 */
  const bar=document.getElementById('meCodeBar');
  if(bar){ const s=String(ME.cardNo||'CLUBOS'); let html=''; for(let i=0;i<s.length;i++){ const w=2+((s.charCodeAt(i)*7)%5); html+='<i style="width:'+w+'px"></i>'; } bar.innerHTML=html; }
  const draw=()=>{ const bucket=Math.floor(Date.now()/60000); drawMeQr('meCodeQr', location.origin+'/web?club_id='+CLUB+'&user='+USER+'&t='+bucket+'#member'); };
  draw();
  if(_meCodeTimer)clearInterval(_meCodeTimer);
  _meCodeTimer=setInterval(draw, 60000);
}
window.openMemberCodeSheet=openMemberCodeSheet;
window.closeMemberCodeSheet=closeMemberCodeSheet;
function mePaintHome(){
  const w=meWallet();
  setT('meClubPts', w.clubPoints??0);
  setT('meGearPts', w.gearPoints??0);
  setT('meName', ME.name);
  meApplyAvatar();
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
  paintNoticeHint();
}
function renderMeQuad(){
  const box=document.getElementById('meQuad'); if(!box)return;
  const cells=[
    ['我的订单', meActivity().length + (ME.campOrders||[]).length, 'myregs', 'regs'],
    ['会员权益', meBenefits().length, 'member', 'gift'],
    ['收货地址', ME.addr.length, 'address', 'pin'],
    ['优惠券', ME.vch.length, 'coupons', 'coupon']
  ];
  box.innerHTML=cells.map(([t,n,go,ic])=>
    `<button class="wme-quad__c" type="button" onclick="pushMe('${go}')">
       <span class="wme-quad__ic">${meIcon(ic)}${(go==='myregs'&&(meNoticeUnread().length||meCampUnread().length))?'<i class="wme-quad__ndot"></i>':''}${n?`<i class="wme-quad__n">${n>99?'99+':n}</i>`:''}</span>
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
const SUB_ME_TITLE={myregs:'我的订单',member:'会员权益',address:'收货地址',coupons:'优惠券',orders:'商城订单',aftersale:'售后 / 退款',service:'联系我们',about:'关于俱乐部',membercard:'会员信息',pointsmall:'积分商城',invite:'邀请好友',inquiries:'我的咨询'};
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
function pushMe(k,param){
  /* 打开「我的报名」即视为已读该用户的俱乐部通知：角标（铃铛 / 我的 tab / 我的报名红点）随之清除。 */
  if(k==='myregs'){ if(meMarkNoticesRead()){ try{ paintNoticeHint(); renderMeQuad(); }catch(e){} } }
  meEnsureStack(); meStack.push({k,param}); window.__meCurSub={k,param:param}; renderMeSub();
}
function popMe(){ if(meStack.length)meStack.pop(); const t=meStack[meStack.length-1]; window.__meCurSub=t||null; renderMeSub(); }
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


/* ── 我的咨询（2026-11-05）：C 端会员与领队/俱乐部的站内对话入口 ──────────
   会话归属靠建单时后端下发的 accessToken，存在 localStorage（web.js 的 myInquirySave）。
   列表先占位、再由 SUB_ME_AFTER 并行拉每条的最新状态与未读红点；点行打开对话抽屉。
   凭证只在自己这台设备上，换设备看不到历史咨询 —— 这是有意的简化，
   避免为「咨询」去做一整套登录身份改造。 */
SUB_ME.inquiries=function(){
  const list=(window.myInquiries&&window.myInquiries())||[];
  if(!list.length)return empty('还没有咨询过','在活动详情点「咨询这条线路」或「咨询领队」。提交后收到回复，页面会弹提醒，点开就能看；也能随时从「我的 → 我的咨询」进来。（咨询记录保存在本机，换设备看不到历史对话）');
  return `<div class="me-iq">`
    +`<div class="me-iq__hint">这里就是查看回复的入口。有回复时会标红点、页面也会弹提醒；点开任意一条可继续追问。</div>`
    +list.map(x=>`<button type="button" class="me-iq__row" data-kind="${esc(x.kind)}" data-id="${Number(x.id)}" `
      +`onclick="window.myInquiryOpen('${esc(x.kind)}',${Number(x.id)},'${esc(x.token||'')}')">`
      +`<span class="me-iq__ic"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5.2 5.8h13.6v10.4H9.4L5.2 19z"/><path d="M8.8 9.6h6.4M8.8 12.6h4"/></svg></span>`
      +`<span class="me-iq__tx"><b>${esc(x.title||'我的咨询')}</b><em>正在读取最新回复…</em></span>`
      +`<span class="me-iq__ar">›</span></button>`).join('')
    +`</div>`;
};
SUB_ME_AFTER.inquiries=async function(){
  const list=(window.myInquiries&&window.myInquiries())||[];
  const rows=[...document.querySelectorAll('.me-iq__row')];
  await Promise.all(list.map(async x=>{
    const el=rows.find(r=>r.dataset.kind===x.kind&&Number(r.dataset.id)===Number(x.id)); if(!el)return;
    try{
      const d=await api(`/api/public/inquiries/${x.kind}/${Number(x.id)}?token=${encodeURIComponent(x.token||'')}`,{cache:'no-store'});
      const msgs=d.messages||[];
      const last=msgs.length?msgs[msgs.length-1]:null;
      const prefix=last?(last.sender==='customer'?'我：':(last.sender==='leader'?'领队：':'俱乐部：')):'';
      const unread=!!((window.__iqUnreadMap||{})[String(x.kind)+':'+Number(x.id)]);
      el.innerHTML=`<span class="me-iq__ic"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5.2 5.8h13.6v10.4H9.4L5.2 19z"/><path d="M8.8 9.6h6.4M8.8 12.6h4"/></svg>${unread?'<i class="me-iq__dot"></i>':''}</span>`
        +`<span class="me-iq__tx"><b>${esc(x.title||'我的咨询')}</b>`
        +`<em>${esc(prefix+(last?String(last.body||'').slice(0,42):'等待回复'))}</em>`
        +`<small>${esc(String(d.lastMessageAt||d.createdAt||'').replace('T',' ').slice(0,16))}${d.replyCount?' · 已回复 '+Number(d.replyCount):''}</small></span>`
        +`<span class="me-iq__ar">›</span>`;
    }catch(_e){
      el.innerHTML=`<span class="me-iq__ic">!</span><span class="me-iq__tx"><b>${esc(x.title||'我的咨询')}</b>`
        +`<em>这条会话已失效，点开可重新发起咨询</em></span><span class="me-iq__ar">›</span>`;
    }
  }));
};
/* 关掉对话抽屉后，若「我的咨询」子页还开着就重绘一次，红点/最新一条立刻更新。 */
window.__meRenderInquiries=function(){
  try{if(window.__meCurSub&&window.__meCurSub.k==='inquiries')renderMeSub();}catch(_e){}
};

/* ── 俱乐部通知数据（2026-10-29 用户反馈「后台发布的通知，C 端客户没有提示」）─────────
   只汇总**已支付**活动订单里后端下发的 status='sent' 通知（草稿与内部备注到不了前端）。
   这些通知不再在「我的」首页插一条卡片，而是统一通过顶栏「信息」铃铛（#msgDot）、
   底部导航「我的」红点、以及「我的报名」宫格红点提示；点开铃铛弹出 wNoticeSheet 看详情。 */
/* ── 通知删除：C 端用户可把已读通知从本地视图清除（仅本地，不影响后台）────── */
function meNoticeDeletedKey(){ return 'clubos_notice_del_'+CLUB+'_'+USER; }
function meNoticeDeletedSet(){
  try{ return new Set(JSON.parse(localStorage.getItem(meNoticeDeletedKey())||'[]')); }catch(e){ return new Set(); }
}
function meNoticeDelete(id){
  const d=meNoticeDeletedSet(); d.add(Number(id));
  try{ localStorage.setItem(meNoticeDeletedKey(), JSON.stringify([...d])); }catch(e){}
}
function meNoticeItems(){
  const out=[];
  const del=meNoticeDeletedSet();
  meActivity().forEach(o=>{
    if(o.status!=='paid')return;
    const t=o.trip||{};
    (t.notices||[]).forEach(n=>{
      const id=Number(n.id||0);
      if(id && del.has(id))return;
      out.push({
        id,title:String(n.title||'出行通知'),content:String(n.content||''),sentAt:n.sentAt,
        act:String(o.activity_title||'活动报名'),occ:String(o.occurrence_label||o.start_at||'')
      });
    });
  });
  return out;
}
/* ── 通知已读：用户「看过」之后角标自动清除 ────────────────────────────────
   用 localStorage 记录已读通知 id（按 club+user 命名空间，避免多账号串台）。
   角标只统计「未读」；打开「信息」铃铛或「我的报名」即视为已读。 */
function meNoticeReadKey(){ return 'clubos_notice_read_'+CLUB+'_'+USER; }
function meNoticeReadSet(){
  try{ return new Set(JSON.parse(localStorage.getItem(meNoticeReadKey())||'[]')); }catch(e){ return new Set(); }
}
function meNoticeUnread(){
  const read=meNoticeReadSet();
  return meNoticeItems().filter(n=>n.id && !read.has(n.id));
}
function meMarkNoticesRead(){
  const all=meNoticeItems(); if(!all.length) return false;
  const read=meNoticeReadSet(); let changed=false;
  all.forEach(n=>{ if(n.id && !read.has(n.id)){ read.add(n.id); changed=true; } });
  if(changed){ try{ localStorage.setItem(meNoticeReadKey(), JSON.stringify([...read])); }catch(e){} }
  return changed;
}

/* ── 带队领队（2026-10-29 用户反馈「领队功能没实现：要显示头像，还要能咨询领队」）───
   出行指引里原先只印一串名字，顾客认不出是谁、更没法咨询。这里改成带头像的 chip，
   点开直接进领队主页（可咨询 / 评分），与 C 端活动详情页的领队卡是同一套交互。
   只有 leaderId（在册领队）才给点击：执行端手打的临时增援没有档案，点了只有空壳。 */
function meLeaderChip(l){
  const lid=Number((l&&l.leaderId)||0);
  const role=String((l&&l.role)||'').trim()||'领队';
  const name=String((l&&l.name)||'').trim()||'领队';
  const av=leaderAvatarHtml(name,(l&&l.avatarUrl)||null);
  const inner=`<span class="me-trip__lav">${av}</span><span class="me-trip__lname">${esc(name)}</span>`
    +(role!=='领队'?`<i class="me-trip__lrole">${esc(role)}</i>`:'');
  return lid
    ? `<button type="button" class="me-trip__leader" title="查看领队资料并咨询" onclick="openLeaderSheet(${lid})">${inner}</button>`
    : `<span class="me-trip__leader is-static">${inner}</span>`;
}

/* ── 通知角标：顶栏「信息」+ 底部导航「我的」───────────────────────────────
   通知条活在「我的」页里，顾客停在首页 / 活动页时完全察觉不到后台发了通知 ——
   所以提示要做到**页面之外**：顶栏一个「信息」标签带条数，底部「我的」挂一个红点。
   两处都只在真有通知时亮；没有通知就整块不留痕迹（空角标比没角标更让人困惑）。 */
function paintNoticeHint(){
  let n=0;
  try{ n=meNoticeUnread().length; }catch(e){ n=0; }
  /* 2026-11-05：顶栏「信息」是顾客找「有人回复我」的第一直觉入口，角标把咨询
     未读一起算；面板里也加了「咨询回复」区块（见 openNoticeSheet）。 */
  const total=n+(Number(window.__iqUnreadCount)||0);
  const dot=document.getElementById('msgDot');
  if(dot){ dot.hidden=!total; dot.textContent=total>99?'99+':String(total); }
  const nd=document.getElementById('meNavDot');
  if(nd) nd.hidden=!total;
  return n;
}
window.paintNoticeHint=paintNoticeHint;

/* ── 顶栏「信息」面板 ──────────────────────────────────────────────────────
   与领队主页同一套底部抽屉（appendChild 到 body，不吃 .web-shell 的作用域），
   z-index 同样低于 .ux-overlay(1100)。列出全部已发送通知，底部给一个直达入口。 */
function closeNoticeSheet(){
  const el=document.getElementById('wNoticeSheet');
  if(el)el.remove();
  document.documentElement.classList.remove('w-sheet-open');
}
/* 顶栏「信息」面板里的「咨询回复」区块：铃铛是顾客找消息的第一直觉入口，
   以前只列出行通知，后台回了咨询点进来反而更困惑。未读来自 web.js 的全局轮询。 */
function iqNoticeHtml(){
  const map=window.__iqUnreadMap||{};
  const list=(window.myInquiries&&window.myInquiries())||[];
  const rows=list.filter(x=>map[String(x.kind)+':'+Number(x.id)]);
  if(!rows.length)return '';
  return `<div class="w-sh__iq"><div class="w-sh__iqhd"><b>咨询回复</b><span>${rows.length} 条新消息</span></div>`
    +rows.map(x=>{
      const d=map[String(x.kind)+':'+Number(x.id)]||{};
      const who=d.lastSender==='leader'?(d.leaderName?('领队 '+d.leaderName):'领队'):'俱乐部';
      return `<button type="button" class="w-sh__iqrow" onclick="closeNoticeSheet();window.myInquiryOpen('${esc(x.kind)}',${Number(x.id)},'${esc(x.token||'')}')">`
        +`<span class="w-sh__iqic"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5.2 5.8h13.6v10.4H9.4L5.2 19z"/><path d="M8.8 9.6h6.4M8.8 12.6h4"/></svg></span>`
        +`<span class="w-sh__iqtx"><b>${esc(x.title||'我的咨询')}</b><em>${esc(who+'：'+String(d.lastBody||'').slice(0,40))}</em></span>`
        +`<span class="w-sh__iqgo">›</span></button>`;
    }).join('')
    +`<button type="button" class="w-sh__iqall" onclick="closeNoticeSheet();wv('wme');pushMe('inquiries')">查看全部咨询 ›</button></div>`;
}
function openNoticeSheet(){
  const items=(typeof meNoticeItems==='function')?meNoticeItems():[];
  let el=document.getElementById('wNoticeSheet');
  if(!el){
    el=document.createElement('div');el.id='wNoticeSheet';el.className='w-sheet';
    el.innerHTML=`<div class="w-sheet__mask" data-close></div><div class="w-sheet__panel" role="dialog" aria-modal="true" aria-label="俱乐部通知">`
      +`<button type="button" class="w-sheet__x" data-close aria-label="关闭">×</button>`
      +`<div class="w-sheet__body" id="wNoticeBody"></div></div>`;
    document.body.appendChild(el);
    document.documentElement.classList.add('w-sheet-open');
    /* 打开「信息」面板＝用户已阅读：标记全部通知为已读，角标立即清除（铃铛 / 我的 tab / 我的报名红点）。 */
    if(meMarkNoticesRead()){ try{ paintNoticeHint(); renderMeQuad(); }catch(e){} }
    /* 关闭走事件委托、且挂在壳上：别用 {once:true} —— 抽屉里点任何东西都会把那个
       一次性监听吃掉，之后错误态里的「关闭」就点不动了（领队抽屉踩过同一个坑）。 */
    el.addEventListener('click',e=>{
      const t=e.target.closest('[data-close]');
      if(t&&el.contains(t))closeNoticeSheet();
    });
  }
  const body=el.querySelector('#wNoticeBody');
  const read=meNoticeReadSet();
  body.innerHTML=iqNoticeHtml()
    +`<div class="w-sh__noticehd"><b>俱乐部通知</b><span>${items.length} 条</span></div>`
    +`<div class="w-sh__noticelist" id="wNoticeList">`
    +(items.length
      ? items.map(n=>{
          const isRead=read.has(n.id);
          return `<div class="w-sh__noticeitem${isRead?' is-read':''}" data-nid="${n.id}">`
            +`<button type="button" class="w-sh__noticedel" onclick="meNoticeDeleteFromSheet(${n.id})">删除</button>`
            +`<div class="w-sh__noticefg">`
              +`<div class="w-sh__noticetop"><b>${esc(n.title)}</b>${n.sentAt?`<i>${esc(String(n.sentAt).slice(5,16))}</i>`:''}${isRead?'':`<em class="w-sh__unread"></em>`}</div>`
              +`<div class="w-sh__noticemeta">${esc(n.act)}${n.occ?' · '+esc(n.occ):''}</div>`
              + (n.content?`<p>${esc(n.content)}</p>`:'')
            +`</div>`
          +`</div>`;
        }).join('')
      : '<div class="w-sh__none">暂时没有俱乐部通知。出发前的集合与装备提醒会发到这里。</div>')
    +`</div>`
    +`<div class="w-sh__noticego"><button type="button" class="w-sh__btn is-primary" onclick="closeNoticeSheet();wv('wme');pushMe('myregs')">查看我的订单</button></div>`;
  meInitNoticeSwipe(document.getElementById('wNoticeList'));
}
window.closeNoticeSheet=closeNoticeSheet;
window.openNoticeSheet=openNoticeSheet;

/* ── 通知：左滑删除（仅已读项；打开面板时已全部标记为已读，故默认可删）──────────
   手势只认横向滑动：竖向滑动立即放弃，交给列表原生滚动，绝不抢手势。
   fg 左移超过阈值则吸附到 -76px 露出红色删除按钮；否则回弹归位。 */
function meInitNoticeSwipe(list){
  if(!list)return;
  let cur=null,sx=0,sy=0,curX=0,dragging=false,locked=false;
  list.addEventListener('touchstart',e=>{
    const fg=e.target.closest('.w-sh__noticefg'); if(!fg)return;
    const item=fg.closest('.w-sh__noticeitem'); if(!item||!item.classList.contains('is-read'))return;
    cur=fg; curX=parseFloat(fg.dataset.x||'0')||0; sx=e.touches[0].clientX; sy=e.touches[0].clientY; dragging=true; locked=false;
    fg.style.transition='';
  },{passive:true});
  list.addEventListener('touchmove',e=>{
    if(!dragging||!cur)return;
    const dx=e.touches[0].clientX-sx, dy=e.touches[0].clientY-sy;
    if(!locked){ if(Math.abs(dy)>Math.abs(dx)){ dragging=false; return; } locked=true; }
    let nx=curX+dx; if(nx>0)nx=0; if(nx<-76)nx=-76;
    cur.style.transform='translateX('+nx+'px)';
  },{passive:true});
  list.addEventListener('touchend',e=>{
    if(!dragging||!cur)return; dragging=false;
    const m=cur.style.transform.match(/translateX\(([-\d.]+)px\)/); const nx=m?parseFloat(m[1]):0;
    cur.style.transition='transform .2s ease';
    if(nx<-40){ cur.style.transform='translateX(-76px)'; cur.dataset.x='-76'; }
    else { cur.style.transform='translateX(0)'; cur.dataset.x='0'; }
    cur=null;
  });
}
/* 删除：从本地删除记录移除该通知 id，并就地重绘列表与计数（不重新拉接口）。 */
function meNoticeDeleteFromSheet(id){
  meNoticeDelete(id);
  const item=document.querySelector('.w-sh__noticeitem[data-nid="'+id+'"]');
  if(item)item.remove();
  const hd=document.querySelector('#wNoticeBody .w-sh__noticehd span');
  const rem=document.querySelectorAll('#wNoticeBody .w-sh__noticeitem').length;
  if(hd)hd.textContent=rem+' 条';
  if(rem===0){ const list=document.getElementById('wNoticeList'); if(list)list.innerHTML='<div class="w-sh__none">暂时没有俱乐部通知。出发前的集合与装备提醒会发到这里。</div>'; }
}
window.meNoticeDeleteFromSheet=meNoticeDeleteFromSheet;
window.meInitNoticeSwipe=meInitNoticeSwipe;

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
  if(t.leaders&&t.leaders.length)rows.push(`<div class="me-trip__row"><span>带队</span><b class="me-trip__leaders">${t.leaders.map(meLeaderChip).join('')}</b></div>`);
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
    <div class="me-card2__hd"><b>${esc(head)}</b>${meBadge(meOrderSt(o.status,kind),meOrderTone(o.status,kind))}</div>
    ${inner}
    <div class="me-card2__ref">退款：${esc((typeof refundStateText==='function')?refundStateText(o):'')}</div>
    ${acts?`<div class="me-card2__ops">${acts}</div>`:''}
  </div>`;
}

/* ── 自助签到码（2026-10-30）：C 端「我的报名」每个参加人一个二维码 ──────── */
function meCheckinBlock(o){
  // 仅已支付、未退款的报名，且参加人处于 active 时才出码（与后端 checkin-qr 口径一致）。
  if(!(o.status==='paid' && ['none','rejected'].includes(o.refund_status||'none'))) return '';
  const ps=(o.participants||[]).filter(p=>p.status==='active');
  if(!ps.length) return '';
  const rows=ps.map(p=>{
    const signed = p.checkinStatus==='checked_in';
    return `<div class="me-ck" data-pid="${p.id}">
      <img class="me-ck__qr" src="/api/public/participants/${p.id}/checkin-qr?user_id=${USER}&club_id=${CLUB}" alt="签到码"
           onclick="meZoomQr(${p.id},'${esc(p.name||'')}')"/>
      <div class="me-ck__info">
        <div class="me-ck__name">${esc(p.name||'')}</div>
        <div class="me-ck__st ${signed?'on':''}">${signed?'已签到':'未签到'}</div>
      </div>
    </div>`;
  }).join('');
  return `<div class="me-ck-wrap">
    <div class="me-ck-hd">签到码 <span>活动当日向领队出示 · 扫码签到</span></div>
    <div class="me-ck-list">${rows}</div>
  </div>`;
}
function meZoomQr(pid,name){
  const url=`/api/public/participants/${pid}/checkin-qr?user_id=${USER}&club_id=${CLUB}`;
  let layer=document.getElementById('meQrZoom');
  if(!layer){
    layer=document.createElement('div');
    layer.id='meQrZoom'; layer.className='me-qr-zoom';
    layer.onclick=function(){ this.style.display='none'; };
    document.body.appendChild(layer);
  }
  layer.innerHTML=`<div class="me-qr-zoom__box" onclick="event.stopPropagation()">
      <div class="me-qr-zoom__name">${esc(name||'')}</div>
      <img src="${url}" alt="签到码"/>
      <div class="me-qr-zoom__tip">请向领队出示此码，由领队扫码完成现场签到</div>
      <button class="me-btn me-btn--block" onclick="document.getElementById('meQrZoom').style.display='none'">关闭</button>
    </div>`;
  layer.style.display='flex';
}

/* ── 子页面实现 ───────────────────────────────────────────────────────── */
/* 「我的订单」= 活动订单（原「我的报名」）+ 营地订单（住宿 / 租赁 / 营地小站）。
   两个 tab 共用同一个 meStack 入口 myregs；param 用 'activity'/'camp' 区分，
   切换走 meSetTab，renderMeSub 会重跑 SUB_ME.myregs 并触发 SUB_ME_AFTER.myregs 拉取营地侧。 */
function meActivityOrdersHtml(){
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
    /* #877 与营地订单卡同口径：包左滑容器 + 常驻删除按钮（用户反馈「左滑没有删除选项」的残留部分） */
    return `<div class="me-camp-wrap">
      <div class="me-camp-del" onclick="event.stopPropagation();meActivityOrderDelete(${Number(o.id)})">删除</div>
      <div class="me-card2 me-camp">
      <div class="me-card2__hd"><b>${esc(o.activity_title||'活动')}</b>${meBadge(meOrderSt(o.status,'activity'),meOrderTone(o.status,'activity'))}</div>
      <div class="me-card2__meta">${esc(o.occurrence_label||o.start_at||'团期待定')} · ${o.participantCount||ps.length} 人</div>
      <div class="me-card2__amt">实付 <b>${money(o.amount||0)}</b></div>
      ${meTrip(o)}
      ${meCheckinBlock(o)}
      ${pax?`<div class="me-paxs">${pax}</div>`:''}
      <div class="me-card2__ref">退款：${esc((typeof refundStateText==='function')?refundStateText(o):'')}</div>
      ${act?`<div class="me-card2__ops">${act}</div>`:''}
      <div class="me-camp__more"><span></span><button type="button" class="me-camp__delbtn" aria-label="删除订单" onclick="event.stopPropagation();meActivityOrderDelete(${Number(o.id)})">删除</button></div>
    </div></div>`;
  }).join('');
}
/* ── #829 营地订单「已读」与红点 ───────────────────────────────────────────
   顾客下了营地订单却完全不知道有人处理过 —— 列表里躺着一张待支付的单子，
   没人提醒。这里用 localStorage 记**已读订单 id**（按 club+user 命名空间，
   多账号不串台）：红点只在真有没看过的订单时亮，点开订单详情即算已读。 */
function meCampSeenKey(){ return 'clubos_camp_seen_'+CLUB+'_'+USER; }
function meCampSeenSet(){
  try{ return new Set(JSON.parse(localStorage.getItem(meCampSeenKey())||'[]').map(String)); }catch(e){ return new Set(); }
}
function meCampSeenSave(s){ try{ localStorage.setItem(meCampSeenKey(), JSON.stringify([...s])); }catch(e){} }
function meCampUnread(){
  const seen=meCampSeenSet();
  return (ME.campOrders||[]).filter(o=>o&&o.id!=null&&!seen.has(String(o.id)));
}
function meCampMarkSeen(id){
  if(id==null) return false;
  const s=meCampSeenSet(); const k=String(id);
  if(s.has(k)) return false;
  s.add(k); meCampSeenSave(s); return true;
}
function meOrderTabs(cur){
  const unread=meCampUnread().length;
  return `<div class="me-tabs">
    <button class="me-tab ${cur==='activity'?'on':''}" type="button" onclick="meSetTab('activity')">活动订单</button>
    <button class="me-tab ${cur==='camp'?'on':''}" type="button" onclick="meSetTab('camp')">营地订单${unread?'<i class="me-tab__dot" aria-label="有未查看的新订单"></i>':''}</button>
  </div>`;
}
function meCampKindLabel(k){return ({stay:'住宿',rental:'租赁',store:'营地小站'})[k]||k||'商品';}
function meCampOrderSt(s){return ({pending_payment:'待支付',paid:'已支付',checked_in:'已入住',completed:'已完成',cancelled:'已取消',expired:'已过期'})[s]||s||'处理中';}
function meCampOrderTone(s){return ({pending_payment:'warn',paid:'ok',checked_in:'ok',completed:'info',cancelled:'off',expired:'off'})[s]||'info';}
function meCampOrdersHtml(list){
  if(!list||!list.length) return empty('暂无营地订单','预订营位、租赁装备或购买营地小站商品后，订单会出现在这里');
  const seen=meCampSeenSet();
  /* #837 用户已删除的订单不再显示 */
  const visible=(list||[]).filter(o=>o&&o.status!=='deleted');
  if(!visible.length) return empty('暂无营地订单','预订营位、租赁装备或购买营地小站商品后，订单会出现在这里');
  return visible.map(o=>{
    const its=o.items||[];
    const itemsHtml=its.map(i=>{
      const parts=[];
      if(i.spec) parts.push(i.spec);
      if(Number(i.qty)>1) parts.push('×'+i.qty);
      if(Number(i.days)>1) parts.push('· '+i.days+'天');
      const specTxt=parts.filter(Boolean).join(' ');
      const cover=i.coverUrl?`<div class="me-camp__cover"><img src="${esc(i.coverUrl)}" alt="" loading="lazy"></div>`:'';
      return `<div class="me-camp__item ${cover?'has-cover':''}">
        ${cover}
        <div class="me-camp__info">
          <div class="me-camp__tags">${meBadge(meCampKindLabel(i.kind),'info')}</div>
          <b class="me-camp__title">${esc(i.title||'营地商品')}</b>
          ${specTxt?`<div class="me-camp__spec">${esc(specTxt)}</div>`:''}
          ${Number(i.deposit)>0?`<div class="me-camp__dep">押金 ${money(i.deposit)}</div>`:''}
          ${(i.checkIn||i.checkOut)?`<div class="me-camp__dates">${esc(i.checkIn||'')}${i.checkOut?(' → '+esc(i.checkOut)):''}</div>`:''}
          <div class="me-camp__sub">小计 <b>${money(Number(i.subtotal||0))}</b></div>
        </div>
      </div>`;
    }).join('');
    const deposit=Number(o.depositAmount||0), payable=Number(o.payable||0), tot=Number(o.total||0);
    const disc=[];
    if(Number(o.memberDiscount)>0) disc.push('会员折扣 '+money(o.memberDiscount));
    if(Number(o.clubPointDiscount)>0) disc.push('积分 '+money(o.clubPointDiscount));
    if(Number(o.voucherDiscount)>0) disc.push('优惠券 '+money(o.voucherDiscount));
    const discHtml=disc.length?`<div class="me-camp__disc">${disc.map(d=>`<span>${esc(d)}</span>`).join('')}</div>`:'';
    const guest=(o.guestName||o.guestPhone)?`<div class="me-camp__guest">${esc(o.guestName||'')} ${esc(o.guestPhone||'')}</div>`:'';
    const head=(its[0]&&its[0].title)||'营地订单';
    const isNew=(o.id!=null&&!seen.has(String(o.id)));
    /* #821 删除按钮默认隐藏，所有订单都能左滑露出 */
    const st=String(o.status||'');
    return `<div class="me-camp-wrap">
      <div class="me-camp-del" onclick="event.stopPropagation();meCampOrderDelete(${Number(o.id)},'${st}')">删除</div>
      <div class="me-card2 me-camp${isNew?' is-new':''}" role="button" tabindex="0" data-oid="${Number(o.id)}" onclick="meCampOrderDetail(${Number(o.id)})">
      <div class="me-card2__hd"><b>${esc(head)}</b><span class="me-card2__hdR">${meBadge(meCampOrderSt(o.status),meCampOrderTone(o.status))}${isNew?'<i class="me-camp__dot" aria-label="新订单"></i>':''}</span></div>
      ${guest}
      ${itemsHtml?`<div class="me-camp__items">${itemsHtml}</div>`:''}
      ${discHtml}
      <div class="me-camp__tot">
        <span>应付 <b>${money(payable)}</b></span>
        ${deposit?`<span>押金 <b>${money(deposit)}</b></span>`:''}
        <span class="me-camp__grand">合计 <b>${money(tot)}</b></span>
      </div>
      <div class="me-card2__meta">订单号 #${esc(o.orderNo||o.id)} · ${esc(String(o.createdAt||'').slice(0,16))}</div>
      <div class="me-camp__more"><span>查看订单详情${o.redeemCode?' · 出示核销码':''} ›</span><button type="button" class="me-camp__delbtn" aria-label="删除订单" onclick="event.stopPropagation();meCampOrderDelete(${Number(o.id)},'${st}')">删除</button></div>
    </div></div>`;
  }).join('');
}
/* #821 左滑删除改为 document 级事件委托：列表多次重渲染也不用手势重绑。
   删除按钮默认由 CSS 藏在右侧外面，只有按住左滑越过 36px 才吸附展开。 */
let _meSw={x:0,dx:0,wrap:null};
function _meSwCard(w){return w?w.querySelector('.me-card2'):null;}
document.addEventListener('touchstart',function(e){
  const w=e.target.closest&&e.target.closest('.me-camp-wrap');
  if(!w)return;
  _meSw={x:e.touches[0].clientX,dx:0,wrap:w};
},{passive:true});
document.addEventListener('touchmove',function(e){
  if(!_meSw.wrap)return;
  _meSw.dx=e.touches[0].clientX-_meSw.x;
  const card=_meSwCard(_meSw.wrap);
  if(card){card.style.transition='none';card.style.transform='translateX('+Math.max(-72,Math.min(0,_meSw.dx))+'px)';}
},{passive:true});
document.addEventListener('touchend',function(){
  const w=_meSw.wrap;if(!w)return;
  const open=_meSw.dx<-36,card=_meSwCard(w);
  if(card){card.style.transition='transform .18s ease';card.style.transform=open?'translateX(-72px)':'translateX(0)';}
  w.classList.toggle('is-open',open);
  _meSw={x:0,dx:0,wrap:null};
},{passive:true});
/* 展开态再点卡片任意处先收起，并且不能穿透进订单详情 */
document.addEventListener('click',function(e){
  const w=e.target.closest&&e.target.closest('.me-camp-wrap.is-open');
  if(!w)return;
  const card=_meSwCard(w);
  w.classList.remove('is-open');
  if(card)card.style.transform='translateX(0)';
  /* #868：捕获层过去对「展开态里的任意点击」一律 stopPropagation/preventDefault，
     把新加的常驻「删除」小按钮也拦死了（点了没反应）。删除类按钮必须放行，
     让它自己的 onclick 正常执行，这里只负责把展开态收起来。 */
  if(e.target.closest('.me-camp-del,.me-camp__delbtn'))return;
  e.stopPropagation();e.preventDefault();
},true);
/* 兼容旧调用点：委托已常驻，无需逐卡片重绑 */
function meBindSwipe(){}

async function meCampOrderDelete(oid,st){
  if(st&&st!=='cancelled'&&st!=='expired'){toast('该订单尚在进行中，请先取消订单再删除');return;}
  const ok=await uxConfirm({title:'删除订单',message:'删除后该订单不再显示，且无法恢复。确定删除？',confirmText:'删除',danger:true});
  if(!ok)return;
  try{
    const uid=(typeof USER!=='undefined'&&USER)||0;
    await api('/api/public/camp-orders/'+oid+'?user_id='+uid,{method:'DELETE',cache:'no-store'});
    const s=meCampSeenSet();s.delete(String(oid));meCampSeenSave(s);
    toast('订单已删除');
    try{renderMeSub();}catch(e){}
    try{await meLoadCampOrders&&meLoadCampOrders();}catch(e){}
  }catch(e){showAlert({title:'删除失败',message:e.message||'请重试'});}
}
window.meCampOrderDelete=meCampOrderDelete;

/* #877 活动订单删除＝对本用户「软隐藏」。后端只写 user_hidden_orders，
   registrations 一行不动（后台报名汇总 / 积分 / 退款 / 核销记录完整保留），
   刷新后由 order-center 继续过滤；DELETE 同端点可恢复。 */
async function meActivityOrderDelete(oid){
  const ok=await uxConfirm({title:'删除订单',message:'删除后这条记录不再显示，报名与支付记录仍完整保留。确定删除？',confirmText:'删除',danger:true});
  if(!ok)return;
  try{
    const uid=(typeof USER!=='undefined'&&USER)||0;
    await api('/api/public/users/'+uid+'/hidden-orders',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({orderKind:'activity',orderId:Number(oid)}),cache:'no-store'});
    if(ME.oc&&Array.isArray(ME.oc.activityOrders))
      ME.oc.activityOrders=ME.oc.activityOrders.filter(x=>Number(x.id)!==Number(oid));
    toast('订单已删除');
    try{renderMeSub();}catch(e){}
  }catch(e){showAlert({title:'删除失败',message:e.message||'请重试'});}
}
window.meActivityOrderDelete=meActivityOrderDelete;
/* ── #829 营地订单详情（订单信息 + 便于核销的二维码）─────────────────────
   原来只有一张列表卡：顾客看到「已支付 · 待出行」却不知道订单号、住了几晚、
   押金多少、码长什么样，到营地只能靠报手机号。这里点开订单 → 独立详情层，
   核销码本地生成二维码（ClubOSQR.draw，离线可用），到营地直接出示。 */
function meCampOrderDetailHtml(o){
  const st=o.status||'';
  const kv=[];
  const push=(k,v)=>{ if(v!==''&&v!=null&&v!==undefined) kv.push('<div class="me-campd__kv"><span>'+esc(k)+'</span><b>'+esc(v)+'</b></div>'); };
  push('订单号',o.orderNo||o.id);
  push('下单时间',String(o.createdAt||'').replace('T',' ').slice(0,16));
  if(o.paidAt) push('支付时间',String(o.paidAt).replace('T',' ').slice(0,16));
  if(o.checkedInAt) push('入住时间',String(o.checkedInAt).replace('T',' ').slice(0,16));
  if(o.checkedOutAt) push('退房时间',String(o.checkedOutAt).replace('T',' ').slice(0,16));
  if(o.cancelledAt) push('取消时间',String(o.cancelledAt).replace('T',' ').slice(0,16));
  const guest=[o.guestName,o.guestPhone].filter(Boolean).join(' ');
  push('联系人',guest);

  const its=(o.items||[]).map(i=>{
    const bits=[];
    if(i.spec) bits.push(i.spec);
    if(i.unitLabel) bits.push(i.unitLabel);
    if(Number(i.qty)>1) bits.push('×'+i.qty);
    if(Number(i.days)>1) bits.push(i.days+' 天');
    const cover=i.coverUrl?'<div class="me-campd__cover"><img src="'+esc(i.coverUrl)+'" alt="" loading="lazy"></div>':'';
    return '<div class="me-campd__it">'+cover+'<div class="me-campd__itbody">'
      +'<b>'+esc(i.title||'营地商品')+'</b>'
      +(bits.length?'<span>'+esc(bits.join(' · '))+'</span>':'')
      +((i.checkIn||i.checkOut)?'<span>'+esc(i.checkIn||'')+(i.checkOut?(' → '+esc(i.checkOut)):'')+'</span>':'')
      +'<em>¥'+money(i.subtotal||0)+'</em></div></div>';
  }).join('');

  const amts=[];
  const ar=(k,v,cls)=>amts.push('<div class="me-campd__amt'+(cls?' '+cls:'')+'"><span>'+esc(k)+'</span><b>'+money(v)+'</b></div>');
  ar('商品金额',o.goodsAmount||0);
  if(Number(o.memberDiscount)>0) ar('会员立减',-Number(o.memberDiscount),'is-cut');
  if(Number(o.clubPointDiscount)>0) ar('积分抵扣',-Number(o.clubPointDiscount),'is-cut');
  if(Number(o.gearPointDiscount)>0) ar('装备积分抵扣',-Number(o.gearPointDiscount),'is-cut');
  if(Number(o.voucherDiscount)>0) ar('优惠券',-Number(o.voucherDiscount),'is-cut');
  if(Number(o.depositAmount)>0) ar('押金',o.depositAmount);
  amts.push('<div class="me-campd__amt is-total"><span>合计</span><b>'+money(o.total||0)+'</b></div>');

  const rc=String(o.redeemCode||'');
  const qr=rc
    ? (o.redeemedAt
        ? '<div class="me-campd__qrbox is-done"><b>已核销</b><span>'+esc(o.redeemedAt)+'</span></div>'
        : '<div class="me-campd__qrbox"><canvas class="me-campd__qr" data-code="'+esc(rc)+'" width="200" height="200"></canvas>'
          +'<div class="me-campd__rctxt">核销码 <b>'+esc(rc)+'</b><span>到营地出示此码，工作人员扫码核销</span></div></div>')
    : '';
  const earn=Number(o.pointsEarned||0)>0
    /* 未支付时积分只是「预计」，说成「已获得」是骗人。 */
    ? '<div class="me-campd__earn">'+(st==='pending_payment'?'支付后可获得 ':'本单获得 ')+Number(o.pointsEarned)+' 活动积分</div>'
    : '';
  let ops='';
  if(st==='pending_payment'){
    ops='<div class="me-campd__ops">'
      +'<button class="me-campd__btn is-primary" type="button" onclick="meCampResumePay('+Number(o.id)+')">去支付</button>'
      +'<button class="me-campd__btn" type="button" onclick="meCampCancelOrder('+Number(o.id)+')">取消订单</button></div>';
  }else if(rc&&!o.redeemedAt){
    ops='<div class="me-campd__ops"><button class="me-campd__btn is-primary" type="button" onclick="meCampZoomQr()">放大核销码</button></div>';
  }
  return '<div class="me-campd__st">'+meBadge(meCampOrderSt(st),meCampOrderTone(st))+'</div>'
    +'<div class="me-campd__kvs">'+kv.join('')+'</div>'
    +(its?'<div class="me-campd__sec"><h4>商品明细</h4>'+its+'</div>':'')
    +'<div class="me-campd__sec"><h4>费用明细</h4>'+amts.join('')+'</div>'
    +qr+earn+ops;
}
window.meCampOrderDetail=async function(oid){
  let o=(ME.campOrders||[]).find(x=>String(x.id)===String(oid));
  try{
    const d=await api('/api/public/camp-orders/'+oid+'?user_id='+USER,{cache:'no-store'});
    if(d&&d.order) o=Object.assign({},o||{},d.order);
  }catch(e){}
  if(!o){ if(window.toast) toast('订单不存在'); return; }
  /* 看过即已读：红点当场熄灭。 */
  if(meCampMarkSeen(o.id)){ try{ renderMeSub(); }catch(e){} }
  const old=document.getElementById('meCampDetail'); if(old) old.remove();
  const el=document.createElement('div');
  el.id='meCampDetail'; el.className='me-campd';
  el.innerHTML='<div class="me-campd__mask" onclick="meCampCloseDetail()"></div>'
    +'<div class="me-campd__panel"><header class="me-campd__hd"><b>订单详情</b>'
    +'<button class="me-campd__x" type="button" onclick="meCampCloseDetail()" aria-label="关闭">×</button></header>'
    +'<div class="me-campd__body">'+meCampOrderDetailHtml(o)+'</div></div>';
  (document.querySelector('.web-shell')||document.body).appendChild(el);
  const cv=el.querySelector('canvas.me-campd__qr');
  if(cv&&window.ClubOSQR){ try{ window.ClubOSQR.draw(cv,cv.dataset.code); }catch(e){} }
};
window.meCampCloseDetail=function(){
  const el=document.getElementById('meCampDetail'); if(el) el.remove();
};
window.meCampZoomQr=function(){
  const cv=document.querySelector('#meCampDetail canvas.me-campd__qr');
  const code=cv?cv.dataset.code:'';
  if(!code) return;
  const old=document.getElementById('meCampQrZoom'); if(old) old.remove();
  const layer=document.createElement('div');
  layer.id='meCampQrZoom'; layer.className='me-campd-zoom';
  layer.innerHTML='<div class="me-campd-zoom__box" onclick="event.stopPropagation()">'
    +'<canvas class="me-campd-zoom__qr" data-code="'+esc(code)+'" width="280" height="280"></canvas>'
    +'<div class="me-campd-zoom__tip">请向营地工作人员出示此码，由其扫码核销</div>'
    +'<button class="me-campd__btn" type="button" onclick="document.getElementById(\'meCampQrZoom\').remove()">关闭</button></div>';
  layer.onclick=function(){ this.remove(); };
  (document.querySelector('.web-shell')||document.body).appendChild(layer);
  const z=layer.querySelector('canvas.me-campd-zoom__qr');
  if(z&&window.ClubOSQR){ try{ window.ClubOSQR.draw(z,z.dataset.code); }catch(e){} }
};
window.meCampResumePay=function(oid){
  window.meCampCloseDetail();
  if(window.campResumePay) window.campResumePay(oid);
  else if(window.toast) toast('请到营地页完成支付');
};
window.meCampCancelOrder=async function(oid){
  try{
    await api('/api/public/camp-orders/'+oid+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',cache:'no-store'});
    if(window.toast) toast('订单已取消，库存与积分已释放');
    window.meCampCloseDetail();
    ME.campOrders=(ME.campOrders||[]).filter(x=>String(x.id)!==String(oid));
    try{ renderMeSub(); }catch(e){}
  }catch(e){ if(window.toast) toast((e&&e.message)||e); }
};
SUB_ME.myregs=function(param){
  const tab = param==='camp' ? 'camp' : 'activity';
  const body = tab==='activity'
    ? meActivityOrdersHtml()
    : (ME.campOrders&&ME.campOrders.length ? meCampOrdersHtml(ME.campOrders) : '<div class="me-loading">加载营地订单中…</div>');
  return meOrderTabs(tab) + `<div id="meOrdersTab" class="me-orderstab">${body}</div>`;
};
SUB_ME_AFTER.myregs=async function(param){
  if(param!=='camp') return;
  const box=document.getElementById('meOrdersTab'); if(!box) return;
  try{
    const data=await api(`/api/public/clubs/${CLUB}/camp-orders?user_id=${USER}`);
    const list=(data&&data.orders)||[];
    ME.campOrders=list;
    box.innerHTML = list.length ? meCampOrdersHtml(list) : empty('暂无营地订单','预订营位、租赁装备或购买营地小站商品后，订单会出现在这里');
    meBindSwipe(); /* #837 左滑删除 */
  }catch(e){
    if(box) box.innerHTML='<div class="me-empty">营地订单加载失败，请稍后重试</div>';
  }
};

SUB_ME.member=function(){
  const tiers=meTiers(), ben=meBenefits(), gd=meGearDiscount();
  /* 装备折扣随俱乐部订阅套餐（平台控制，2026-10-23 起等级不参与）：
     作为一条横幅放在等级列表上方，逐等级的「装备 X 折」不再展示。 */
  const gdHtml=gd&&gd.rate<1
    ? `<div class="me-gd">装备商城 <b>${esc(gd.discountZhe)} 折</b><span>本俱乐部会员专享 · 购物车与结算自动生效</span></div>`
    : '';
  const tHtml=tiers.map(t=>{
    const cur=String((meWallet().memberLevel)||'');
    const isCur=cur&&String(t.name)===cur;
    return `<div class="me-tier-row${isCur?' on':''}">
      <div class="me-tier-row__hd"><span class="me-tier-row__name">${esc(t.name)}</span>${isCur?'<i class="me-tier-row__cur">当前等级</i>':''}</div>
      <div class="me-tier-row__cond">${esc(t.qualification_mode==='ANY'?'满足其一即可':'需同时满足')} · 消费满 ${money(t.min_activity_spend||0)} 或 参加 ${t.min_activity_count||0} 场</div>
    </div>`;
  }).join('');
  /* #860：券不带积分价，这里只展示权益本身；要用积分兑换请进积分商城。 */
  const bHtml=ben.length?ben.map(b=>`<div class="me-bnf">
      <div class="me-bnf__hd"><b>${esc(b.title)}</b></div>
      ${b.description?`<p>${esc(b.description)}</p>`:''}
      <div class="me-bnf__ft">
        <div class="me-bnf__cost">${b.cash_value?`价值 ${money(b.cash_value)}`:'会员权益'}</div>
        <button class="me-btn" onclick="pushMe('pointsmall')">去积分商城</button>
      </div>
    </div>`).join(''):empty('暂无会员权益','');
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
    face:v.face_json||v.face,
    cashValue:(v.cash_value!=null?v.cash_value:v.cashValue),
    thresholdAmount:(v.threshold_amount!=null?v.threshold_amount:v.thresholdAmount),
    mode:v.mode,discountPercent:(v.discount_percent!=null?v.discount_percent:v.discountPercent),
    benefitType:v.benefit_type||v.benefitType,
    targetType:v.target_type||v.targetType,targetCity:v.target_city||v.targetCity,
    validTo:v.valid_to||v.validTo,voucherCode:v.voucher_code||v.voucherCode,
    status:v.expired?'expired':(v.status==='used'?'used':'')
  }):'';
  const code=v.voucher_code||v.voucherCode||'';
  let act='';
  if(claimable) act=`<button class="me-btn me-btn--sm" onclick="meClaimCoupon(${v.id})">立即领取</button>`;
  else if(v.status==='issued'&&!v.expired&&code) act=`<button class="me-btn me-btn--sm" onclick="meShareCoupon('${esc(code)}','${esc(v.title||'优惠券')}')">送好友</button>`;
  return `<div class="me-cpnitem">${face}<div class="me-cpnitem__ops">${act}</div></div>`;
}
SUB_ME.coupons=function(){
  const claimable=ME.claimable||[];
  const mine=ME.mineCoupons||[];
  const usable=mine.filter(v=>v.status==='issued'&&!v.expired);
  const spent=mine.filter(v=>v.status!=='issued'||v.expired);
  const manual=meCitySource()==='manual';
  const head=`<div class="me-cpn__city">
      <span>所在城市</span><b>${esc(ME.city||'未设置')}</b>
      ${ME.city?`<i class="me-cpn__src${manual?' me-cpn__src--manual':''}">${manual?'手动设置':'系统定位'}</i>`:''}
      <span class="me-cpn__ops">
        <button type="button" id="meCpnLocate" onclick="meLocateCity(true)">重新定位</button>
        <button type="button" onclick="meSetCity()">手动调整</button>
      </span>
      <span class="me-cpn__tip">优先按系统定位的本地城市发放城市定向优惠券；手动调整后会固定使用所选城市，不再被自动定位覆盖。</span>
    </div>`;
  const parts=[];
  if(claimable.length) parts.push(`<div class="me-cpn__grp"><h4>可领取</h4><div class="me-cpn__list">${claimable.map(v=>meCouponFace(v,true)).join('')}</div></div>`);
  if(usable.length) parts.push(`<div class="me-cpn__grp"><h4>我的优惠券（${usable.length}）</h4><div class="me-cpn__list">${usable.map(v=>meCouponFace(v,false)).join('')}</div></div>`);
  if(spent.length) parts.push(`<div class="me-cpn__grp"><h4>已使用 / 已过期</h4><div class="me-cpn__list">${spent.map(v=>meCouponFace(v,false)).join('')}</div></div>`);
  if(!parts.length) return head+empty('还没有优惠券，去活动页或商城领一张');
  return head+parts.join('');
};
/* 进入优惠券子页即尝试一次系统定位（renderMeSub 会跑 SUB_ME_AFTER）。
   _meGeoTried 保证重渲染不会再触发，手动设过城市也会被 meCitySource 拦下。 */
SUB_ME_AFTER.coupons=function(){ meLocateCity(false); };
async function meClaimCoupon(id){
  try{
    const r=await api(`/api/public/coupons/${id}/claim`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:USER,clubId:CLUB})});
    toast('已领取 '+(window.uxCouponMoney?window.uxCouponMoney(r.cashValue||0):('¥'+(r.cashValue||0)))+' 优惠券');
    ME.ts=0; await meLoadAll(true); renderMeSub();
  }catch(e){ toast((e&&e.message)||e); }
}
async function meShareCoupon(code,title){
  if(!code) return;
  const text=(title||'优惠券')+'｜券码 '+code+'｜把券码发给好友，好友在「邀请好友」页输入券码即可领取，报名活动直接抵用';
  try{ if(navigator.share){ await navigator.share({title:title||'送你一张券',text}); return; } }catch(e){}
  if(typeof meCopy==='function'){ meCopy(text,'券码已复制，发给好友吧'); return; }
  try{ await navigator.clipboard.writeText(text); toast('券码已复制：'+code); }
  catch(e){ showAlert({title:'券码',message:'请把券码发给好友：'+code}); }
}
/* 好友送券：输入券码领取（券会从好友券包转移到你的券包） */
async function meClaimByCode(){
  const v=await showForm({title:'输入券码领券',subtitle:'好友把「券码 + 领取码」发给你后，在这里输入即可把券领到自己的券包，报名活动时直接抵用。',
    fields:[{name:'code',label:'券码',required:true,full:true,placeholder:'如 CPN-1A2B3C4D',validate:x=>/^CPN-/i.test(x||'')?'':'券码以 CPN- 开头'}],submitText:'领取到券包'});
  if(!v) return;
  try{
    const r=await api(`/api/public/clubs/${CLUB}/coupons/transfer`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:v.code.trim().toUpperCase(),toUserId:USER})});
    toast(`已领取「${r.title}」到券包`);
    ME.ts=0; await meLoadAll(true); renderMeSub();
  }catch(e){ showAlert({title:'领取失败',message:(e&&e.message)||e}); }
}
/* 填邀请码入会（拉新）：注册后补填也有效，双方各得一张邀请券 */
async function meFillInviteCode(){
  const v=await showForm({title:'填写好友邀请码',subtitle:'填好友的邀请码（IV 开头）完成绑定，你和好友都会获得一张邀请优惠券，报名活动时直接抵用。',
    fields:[{name:'code',label:'好友邀请码',required:true,full:true,placeholder:'如 IV3A2B1C',validate:x=>/^IV/i.test(x||'')?'':'邀请码以 IV 开头'}],submitText:'绑定并领券'});
  if(!v) return;
  try{
    const r=await api(`/api/public/clubs/${CLUB}/membership/join`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({inviteCode:v.code.trim().toUpperCase(),userId:USER})});
    if(r.alreadyBound) toast('你已绑定过邀请人，无需重复填写');
    else{ toast(`绑定成功！你和好友各得一张邀请优惠券`); }
    ME.ts=0; await meLoadAll(true); renderMeSub();
  }catch(e){ showAlert({title:'绑定失败',message:(e&&e.message)||e}); }
}
/* 邀请好友页（2026-10-26 拉新）：邀请码 / 已邀请 / 奖励说明 / 输码领券 */
SUB_ME.invite=function(){
  return `<div id="meInviteBody"><div class="me-empty">正在加载邀请卡…</div></div>`;
};
SUB_ME_AFTER.invite=async function(){
  const box=document.getElementById('meInviteBody'); if(!box) return;
  try{
    const d=await api(`/api/public/clubs/${CLUB}/membership/invite-info?user_id=${USER}`);
    const rw=d.reward;
    const rwText=rw?`好友注册成功后，双方各得一张「${esc(rw.title)}」`+(String(rw.mode)==='discount'?`（${Number(rw.discountPercent||0)/10} 折）`:`（抵 ${window.uxCouponMoney?window.uxCouponMoney(rw.cashValue):'¥'+(rw.cashValue||0)}）`)+`，报名活动直接抵用`:'俱乐部暂未开启邀请奖励，仍可填写邀请码绑定好友';
    const invited=(d.invited||[]).map(x=>`<div class="me-invite__rec"><b>${esc(x.name||'好友')}</b><span>${esc(x.level||'会员')} · ${esc(String(x.created_at||'').slice(0,10))}</span></div>`).join('');
    box.innerHTML=`
      <div class="me-invite__hero">
        <div class="me-invite__label">我的邀请码</div>
        <div class="me-invite__code" onclick="meCopy('${esc(d.inviteCode)}','邀请码已复制')">${esc(d.inviteCode)}</div>
        <div class="me-invite__hint">把邀请码发给好朋友，TA 注册会员后填入即可绑定</div>
        <div class="me-invite__ops">
          <button class="me-btn" onclick="meCopy('${esc(d.inviteCode)}','邀请码已复制，发给好友吧')">复制邀请码</button>
          <button class="me-btn me-btn--ghost" onclick="meFillInviteCode()">我有邀请码</button>
        </div>
      </div>
      <div class="me-invite__reward">${esc(rwText)}</div>
      <div class="me-sec-t">好友送我一张券？</div>
      <div class="me-invite__gift">
        <span class="sub">在下面输入券码（CPN- 开头），券会立刻转到你的券包，报名活动时直接抵用。</span>
        <button class="me-btn me-btn--sm" onclick="meClaimByCode()">输入券码领取</button>
      </div>
      <div class="me-sec-t">已邀请好友（${d.invitedCount||0}）</div>
      ${invited||'<div class="empty">还没有好友用你的邀请码注册，快去分享吧</div>'}`;
  }catch(e){ box.innerHTML=empty('邀请卡加载失败',(e&&e.message)||'请稍后再试'); }
};
/* ── 所在城市：默认系统定位，其次才手动调整（2026-11-09） ─────────────────
   city 只有一份真源（users.city / public_set_user_city）。「是不是用户手动选的」
   后端没有字段，用 localStorage 记来源：manual 的城市不再被自动定位覆盖；
   点「重新定位」是显式的 force 操作，允许把来源改回 geo。 */
function meCitySourceKey(){return 'clubos_city_src_'+String(USER||0);}
function meCitySource(){try{return localStorage.getItem(meCitySourceKey())||''}catch(e){return ''}}
function meCitySourceSet(v){try{if(v)localStorage.setItem(meCitySourceKey(),v);else localStorage.removeItem(meCitySourceKey())}catch(e){}}
let _meGeoTried=false;
function meLocateCity(force){
  if(!force&&_meGeoTried)return;
  if(!force&&ME.city&&meCitySource()==='manual')return;
  _meGeoTried=true;
  if(!navigator.geolocation||!window.clubosNearestCity){
    if(force)toast('当前浏览器不支持定位，请手动选择城市');
    return;
  }
  const btn=document.getElementById('meCpnLocate');
  if(btn){btn.disabled=true;btn.textContent='定位中…';}
  const restore=()=>{const b=document.getElementById('meCpnLocate');if(b){b.disabled=false;b.textContent='重新定位'}};
  try{
    navigator.geolocation.getCurrentPosition(async pos=>{
      const c=window.clubosNearestCity(pos.coords.latitude,pos.coords.longitude);
      restore();
      if(!c){if(force)toast('附近暂无可选城市，请手动选择');return;}
      if(c===ME.city){if(force)toast('已在 '+c);return;}
      try{
        await api(`/api/public/users/${USER}/city`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({city:c})});
        ME.city=c;meCitySourceSet('geo');ME.ts=0;
        await meLoadAll(true);renderMeSub();
        if(force)toast('已定位到 '+c);
      }catch(e){if(force)toast((e&&e.message)||'定位城市保存失败')}
    },()=>{restore();if(force)toast('定位未开启或已拒绝，可手动选择城市')},
      {timeout:8000,maximumAge:600000});
  }catch(e){restore();if(force)toast('定位失败，请手动选择城市')}
}
async function meSetCity(){
  const cities=(window.CLUBOS_CITIES||[]);
  const opts=[{value:'',label:'不设置'}].concat(cities.map(c=>({value:c,label:c})));
  const v=await showForm({title:'手动调整所在城市',desc:'默认是系统定位的本地城市；手动选择后会固定使用这个城市，不再被自动定位覆盖。',fields:[{name:'city',label:'所在城市',type:'select',options:opts,value:ME.city||''}],submitText:'保存'});
  if(!v) return;
  try{
    await api(`/api/public/users/${USER}/city`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({city:v.city})});
    ME.city=v.city||'';meCitySourceSet(v.city?'manual':'');_meGeoTried=true;ME.ts=0;
    toast('已保存');
    await meLoadAll(true); renderMeSub();
  }catch(e){ toast((e&&e.message)||e); }
}
SUB_ME.orders=function(st){
  const tab=st||'all';
  const tabs=[['all','全部'],['activity','活动报名'],['pending','待付款'],['paid','待发货'],['shipped','待收货'],['completed','待评价'],['refund','退款']];
  const tabHtml=tabs.map(([k,t])=>`<button class="me-tab${k===tab?' on':''}" onclick="meSetTab('${k}')">${esc(t)}</button>`).join('');
  let items=[];
  if(tab==='all') items=meAll().map(x=>(Object.assign({},x.order,{_kind:x.kind})));
  else if(tab==='activity') items=meActivity().map(o=>Object.assign({},o,{_kind:'activity'}));
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
          <div class="me-mc__ava" id="meMcAva">${meAvatarInner(ME.name||'远',ME.avatar||'')}</div>
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
          ${(()=>{const p=mePurchase();return (p.activePurchase)?`<div class="me-info__row"><span>购买会员有效期至</span><b>${esc(String(p.activePurchase.valid_to||'').slice(0,10))}</b></div>`:''})()}
        </div>
      </div>
      ${(()=>{const p=mePurchase();
        if(!p.enabled||!(p.tiers||[]).length) return '';
        return `<div class="me-sec-t" style="margin-top:14px">可购买的会员卡</div>`
          +p.tiers.map(t=>`<div class="me-bnf">
              <div class="me-bnf__hd"><b>${esc(t.name)} 会员卡</b></div>
              <div class="me-bnf__cost"><b>${money(t.price||0)}</b> / ${t.valid_months||12} 个月</div>
              <div class="me-bnf__ft"><span class="me-note">${p.paymentAvailable===false?'会员购买暂未开放，当前权益不受影响':'购买后立即生效，有效期内享受该等级权益'}</span>
                <button class="me-btn" ${p.paymentAvailable===false?'disabled':''} onclick="meBuyTier(${t.id},'${esc(t.name)}',${Number(t.price||0)})">${p.paymentAvailable===false?'暂未开放购买':'立即购买'}</button></div>
            </div>`).join('');
      })()}
    </div>`;
};
async function meBuyTier(tierId,name,price){
  if(!(await showConfirm({title:`购买「${name}」会员卡`,message:`确认支付 ${money(price)} 购买？购买后立即生效。`})))return;
  try{
    const r=await api(`/api/public/clubs/${CLUB}/membership/purchase`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:USER,tierId})});
    toast(`购买成功，已升级为「${(r.tier&&r.tier.name)||name}」`);
    ME.ts=0; await meLoadAll(true); renderMeSub();
  }catch(e){ toast((e&&e.message)||e); }
}
SUB_ME_AFTER.membercard=function(){ drawMeQr('meMcQr', location.origin+'/web?club_id='+CLUB+'&user='+USER+'#member'); };

/* ── 积分商城（2026-10-26）：点首页「可用积分」进入的二级页 ──────────────
   顾客用活动积分兑换：优惠券（兑换即进券包）/ 活动 / 服务 / 实物（生成核销码）。
   下单可获得积分、下单可用积分抵扣 —— 兑换是积分的第三个出口。 */
window._mallFilter='all';
SUB_ME.pointsmall=function(){
  return `<div id="meMallBody"><div class="me-empty">正在加载积分商城…</div></div>`;
};
SUB_ME_AFTER.pointsmall=async function(){
  const box=document.getElementById('meMallBody'); if(!box)return;
  try{
    const d=await api(`/api/public/clubs/${CLUB}/points-mall?user_id=${USER}`);
    window._mallData=d;
    box.innerHTML=meMallPaint(d);
  }catch(e){ box.innerHTML=empty('积分商城加载失败',(e&&e.message)||'请稍后再试'); }
};
function meMallType(t){
  return ({coupon:'优惠券',activity:'活动',service:'服务',goods:'实物'}[t]||t);
}
function meMallPaint(d){
  const items=(d.items||[]); const mine=(d.redemptions||[]);
  const w=d.wallet||{};
  const filters=[['all','全部'],['coupon','优惠券'],['activity','活动'],['service','服务'],['goods','实物']];
  const shown=items.filter(x=>window._mallFilter==='all'||x.item_type===window._mallFilter);
  /* 2026-11-05：换算比例按当前会员等级由后端下发，不再写死 100 积分 = ¥1。
     老缓存 / 接口异常时退回 100（升级前的历史口径），页面不至于显示 undefined。 */
  const rate=Number(d.pointsRedeemRate||100)||100;
  const head=`<div class="me-gd"><b>${w.clubPoints??0}</b> 可用积分<span>报名付款可获得积分，报名时也可用积分抵扣；当前 ${rate} 积分 = ¥1（按你的会员等级）</span></div>`;
  const chips=`<div class="me-mall__tabs">${filters.map(([k,t])=>`<button class="me-mall__tab${window._mallFilter===k?' on':''}" onclick="meMallFilter('${k}')">${t}</button>`).join('')}</div>`;
  const list=shown.length?shown.map(x=>{
    const off=x.stock!=null&&Number(x.stock)<=0;
    const can=(w.clubPoints??0)>=Number(x.points_cost||0)&&!off;
    return `<div class="me-bnf">
      <div class="me-bnf__hd"><b>${esc(x.title)}</b>${meBadge(meMallType(x.item_type),'info')}</div>
      ${x.description?`<p>${esc(x.description)}</p>`:''}
      ${x.item_type==='coupon'&&x.refTitle?`<p>兑换后发放券：${esc(x.refTitle)}</p>`:''}
      <div class="me-bnf__ft">
        <div class="me-bnf__cost"><b>${x.points_cost}</b> 活动积分${x.stock!=null?` · 剩 ${x.stock}`:''}</div>
        ${off?meBadge('已兑完','off'):`<button class="me-btn" ${can?'':'disabled'} onclick="meRedeemMall(${x.id},'${esc(x.title)}',${Number(x.points_cost||0)})">兑换</button>`}
      </div>
    </div>`;
  }).join(''):empty('该分类暂无可兑换商品','换一个分类看看');
  const mineHtml=mine.length?`<div class="me-sec-t">我的兑换</div>`+mine.map(r=>`<div class="me-mall__rec">
      <div class="me-mall__rec__hd"><b>${esc(r.item_title)}</b>${meBadge(r.status==='verified'?'已核销':'待核销',r.status==='verified'?'ok':'warn')}</div>
      <div class="me-mall__rec__sub">${meMallType(r.item_type)} · ${r.points_spent} 积分 · ${esc(String(r.created_at||'').slice(0,10))}${r.code?` · 核销码 <b>${esc(r.code)}</b>`:' · 券已发到「优惠券」'}</div>
    </div>`).join(''):'';
  return head+chips+`<div class="me-mall__list">${list}</div>`+mineHtml;
}
function meMallFilter(k){ window._mallFilter=k; const box=document.getElementById('meMallBody'); if(box&&window._mallData)box.innerHTML=meMallPaint(window._mallData); }
async function meRedeemMall(id,title,points){
  if(!(await showConfirm({title:'确认兑换',message:`用 ${points} 活动积分兑换「${title}」？积分一旦兑换不可退回。`})))return;
  try{
    const r=await api(`/api/public/clubs/${CLUB}/points-mall/${id}/redeem`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:USER})});
    if(r.coupon) toast(`兑换成功，「${title}」已发到你的优惠券`);
    else if(r.code) showAlert({title:'兑换成功',message:`核销码：${r.code}\n请凭核销码到店使用，也可在「我的兑换」中随时查看。`});
    else toast('兑换成功');
    ME.ts=0; await meLoadAll(true);
    const box=document.getElementById('meMallBody');
    if(box){ const d=await api(`/api/public/clubs/${CLUB}/points-mall?user_id=${USER}`); window._mallData=d; box.innerHTML=meMallPaint(d); }
    mePaintHome();
  }catch(e){ toast((e&&e.message)||e); }
}

/* ── 地址 CRUD（2026-11-09：与确认订单页共用 #addrSheet 抽屉） ─────────────
   之前这里用 showForm 的通用弹窗、结算页用内联 3×2 表单，两套字段两套校验。
   现在统一调 window.openAddressSheet()，后端接口与字段一字未改。 */
function meOpenAddrSheet(a){
  if(!window.openAddressSheet){toast('地址功能加载失败，请刷新页面重试');return;}
  window.openAddressSheet({userId:USER,addr:a||null,onSaved:async function(){
    ME.ts=0; await meLoadAll(true); renderMeSub();
  }});
}
function meAddAddr(){ meOpenAddrSheet(null); }
function meEditAddr(id){
  const a=(ME.addr||[]).find(x=>String(x.id)===String(id)); if(!a)return;
  meOpenAddrSheet(a);
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
/* 积分商城 / 购买会员 / 拉新邀请（inline onclick 引用，必须挂 window —— me.js 整体是 IIFE，裸 function 不上 window） */
window.meBuyTier=meBuyTier; window.meRedeemMall=meRedeemMall; window.meMallFilter=meMallFilter;
window.meClaimByCode=meClaimByCode; window.meFillInviteCode=meFillInviteCode;
/* BUG-CUX-001（2026-11-08）：下面 4 个函数被模板字符串的 inline onclick 调用，
   me.js 整体是 IIFE，裸 function 不会挂到 window —— 漏导出会让「签到码放大 /
   城市去设置 / 立即领取 / 送好友」点了静默抛 ReferenceError。新增任何 inline
   入口必须同时补一行导出，tests/web_inline_handlers_guard.py 会在发版门禁里卡住。 */
window.meZoomQr=meZoomQr; window.meSetCity=meSetCity; window.meLocateCity=meLocateCity;
window.meClaimCoupon=meClaimCoupon; window.meShareCoupon=meShareCoupon;

/* ── 头像（2026-10-31）：两种方案 —— 自定义上传 / 自动同步微信头像 ──────────────
   显示优先级：自定义 avatar_url > 微信 wechat_avatar_url > 首字母。
   微信登录尚未接入时 wechat_avatar_url 为空，「使用微信头像」按钮置灰提示；
   一旦接入并授权，顾客点一下即可把微信头像同步过来（后端把 wechat_avatar_url 复制到 avatar_url）。 */
function meAvatarInner(name,url){
  const letter=esc((name||'远')[0]||'远');
  if(url) return `<img class="me-ava__img" src="${esc(url)}" alt="${esc(name||'')}" onerror="this.parentNode.textContent='${letter}'">`;
  return letter;
}
function meApplyAvatar(){
  const name=ME.name||'远', url=ME.avatar||'';
  ['meAva','meCodeAv','meMcAva'].forEach(function(id){
    const el=document.getElementById(id);
    if(el) el.innerHTML=meAvatarInner(name,url);
  });
}
function closeAvatarSheet(){
  const el=document.getElementById('meAvatarSheet'); if(el)el.remove();
  document.documentElement.classList.remove('w-sheet-open');
}
function openAvatarSheet(){
  let el=document.getElementById('meAvatarSheet');
  if(!el){
    el=document.createElement('div');el.id='meAvatarSheet';el.className='w-sheet';
    el.innerHTML=`<div class="w-sheet__mask" data-close></div>
      <div class="w-sheet__panel me-av" role="dialog" aria-modal="true" aria-label="设置头像">
        <button type="button" class="w-sheet__x" data-close aria-label="关闭">×</button>
        <div class="me-av__body">
          <div class="me-av__preview" id="meAvPrev"></div>
          <div class="me-av__name">${esc(ME.name||'会员')}</div>
          <p class="me-av__hint">换一张清晰的照片，让大家更容易认出你</p>
          <input type="file" id="meAvFile" accept="image/*" hidden>
          <input type="file" id="meAvFileCam" accept="image/*" capture="environment" hidden>
          <div class="me-av__src">
            <button class="me-btn me-btn--block" type="button" onclick="mePickAvatar('cam')"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h3l1.6-2h6.8L17 8h3v11H4z"/><circle cx="12" cy="13" r="3.3"/></svg>拍照</button>
            <button class="me-btn me-btn--block" type="button" onclick="mePickAvatar('album')"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="12" cy="11" r="3"/><path d="M8 17l2.2-2.4 1.8 2 1.6-1.8L17 17"/></svg>从相册选择</button>
          </div>
          ${ME.wechatAvatar?`<button class="me-btn me-btn--block" type="button" onclick="meUseWechatAvatar()">使用微信头像</button>`
                            :`<button class="me-btn me-btn--block" type="button" disabled title="需在微信中授权后自动同步">使用微信头像（未授权）</button>`}
          ${ME.avatar?`<button class="me-btn me-btn--block me-btn--danger" type="button" onclick="meClearAvatar()">恢复默认头像</button>`:''}
        </div>
      </div>`;
    document.body.appendChild(el);
    document.documentElement.classList.add('w-sheet-open');
    el.addEventListener('click',function(e){ const t=e.target.closest('[data-close]'); if(t&&el.contains(t))closeAvatarSheet(); });
    const wire=function(id){ const fi=document.getElementById(id); if(fi&&!fi._wired){ fi._wired=true; fi.addEventListener('change',meOnAvatarFile); } };
    wire('meAvFile'); wire('meAvFileCam');
  }
  const prev=document.getElementById('meAvPrev'); if(prev)prev.innerHTML=meAvatarInner(ME.name||'远',ME.avatar||'');
}
function mePickAvatar(kind){
  const fi=document.getElementById(kind==='cam'?'meAvFileCam':'meAvFile');
  if(fi)fi.click();
}
function meOnAvatarFile(e){
  const f=e.target.files&&e.target.files[0]; if(!f) return;
  if(f.size>3*1024*1024){ if(window.toast)toast('图片不能超过 3MB'); return; }
  const rd=new FileReader();
  rd.onload=function(){
    const prev=document.getElementById('meAvPrev'); if(prev)prev.innerHTML='<img class="me-ava__img" src="'+rd.result+'">';
    meUploadAvatar(rd.result);
  };
  rd.readAsDataURL(f);
}
async function meUploadAvatar(dataUrl){
  try{
    const r=await api(`/api/public/users/${USER}/avatar`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({avatarData:dataUrl})});
    ME.avatar=r.avatarUrl||ME.avatar; meApplyAvatar();
    if(window.toast)toast('头像已更新');
    if(document.getElementById('meAvatarSheet')){ closeAvatarSheet(); openAvatarSheet(); }
  }catch(e){ if(window.toast)toast((e&&e.message)||e); }
}
async function meUseWechatAvatar(){
  try{
    const r=await api(`/api/public/users/${USER}/avatar`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({useWechat:true})});
    ME.avatar=r.avatarUrl||''; meApplyAvatar();
    if(window.toast)toast('已使用微信头像');
    if(document.getElementById('meAvatarSheet')){ closeAvatarSheet(); openAvatarSheet(); }
  }catch(e){ if(window.toast)toast((e&&e.message)||e); }
}
async function meClearAvatar(){
  try{
    await api(`/api/public/users/${USER}/avatar`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({clear:true})});
    ME.avatar=''; meApplyAvatar();
    if(window.toast)toast('已恢复默认头像');
    if(document.getElementById('meAvatarSheet')){ closeAvatarSheet(); openAvatarSheet(); }
  }catch(e){ if(window.toast)toast((e&&e.message)||e); }
}
window.openAvatarSheet=openAvatarSheet; window.closeAvatarSheet=closeAvatarSheet;
window.mePickAvatar=mePickAvatar; window.meUseWechatAvatar=meUseWechatAvatar; window.meClearAvatar=meClearAvatar;

/* 若脚本在 我的 已激活后注入，立即刷新一次 */
if(document.getElementById('wme') && !document.getElementById('wme').hasAttribute('hidden')===false){
  /* 由 wv 触发，无需主动刷 */
}
})();
