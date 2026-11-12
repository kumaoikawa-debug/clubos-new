/* ===========================================================================
   ClubOS · C 端手势导航（2026-10-30 用户需求：左滑退回上一个界面、右滑前进）
   ---------------------------------------------------------------------------
   设计要点：
   - 只认「屏幕边缘」起手的手势（左缘≤28px / 右缘≥innerWidth-28px），与内容区的
     横向轮播（主视觉、商城头图、团期卡片）天然不冲突 —— 那些滚动条在内容中部，
     不会从屏幕最边缘起手。
   - 左缘横滑 → 返回当前上下文的「上一个界面」；右缘横滑 → 前进到刚退回的界面。
   - 返回映射复刻原有「返回」按钮的语义：me 子页→popMe、活动详情→backList、
     商城商品/购物车→showMallList、底部 tab→上一个 tab（支持多步）。
   - 抽屉 / 弹层 / 对话框打开时、或焦点在输入框、或竖向滚动、或在横向滚动容器内
     起手时，不抢手势，交给原生行为。
   - 用户在「前进」入口（点活动 / 点商品 / 点购物车 / 开子页 / 切 tab）正常导航时，
     清空前进栈，避免右滑回到陈旧界面。
   =========================================================================== */
(function(){
'use strict';
var MIN_SWIPE=56;
var sx=0, sy=0, edge=null;
var forwardStack=[];          // 前进栈：退回时压入「刚离开界面」的还原闭包
var tabHist=[];               // 底部 tab 历史（支持多步返回）

function d(id){ return document.getElementById(id); }
function activeTab(){ var b=document.querySelector('.web-nav button.active'); return (b&&b.dataset)?b.dataset.wv:null; }
function clearFwd(){ forwardStack.length=0; }

/* 抽屉 / 弹层 / 对话框 / 输入态 —— 这些手势交给原生，不抢。 */
function isSheetOpen(){
  if(document.documentElement.classList.contains('w-sheet-open')) return true;
  if(d('wNoticeSheet')||d('wLeaderSheet')) return true;
  /* #821 营地商品详情是挂在 body 上的全屏层，手势要让它自己处理返回 */
  if(d('cpDetailHost')) return true;
  if(document.querySelector('.ux-overlay:not([hidden])')) return true;
  var dg=document.querySelector('[role="dialog"]');
  if(dg && dg.offsetParent!==null) return true;
  return false;
}
/* 起手点落在横向滚动容器内（轮播 / 可横滑列表）→ 让原生滚动，不触发返回。 */
function inHScroller(el){
  while(el && el!==document.body && el!==document.documentElement){
    var s=getComputedStyle(el);
    if(/(auto|scroll)/.test(s.overflowX) && el.scrollWidth>el.clientWidth+4) return true;
    el=el.parentElement;
  }
  return false;
}

/* ── 返回：复刻各上下文的「返回」按钮 ───────────────────────────────────── */
function appBack(){
  /* 0) #821 营地商品详情全屏层，优先于所有其它返回通路 */
  if(d('cpDetailHost')){ if(window.campCloseDetail)window.campCloseDetail(); return true; }
  /* 1) me 子页（覆盖层，最上层） */
  var ms=d('meStack');
  if(ms && !ms.hidden){
    var cur=window.__meCurSub;
    if(cur && cur.k) forwardStack.push(function(){ window.pushMe(cur.k, cur.param); });
    window.popMe(); return true;
  }
  /* 2) 活动详情（#publicDetail 有内容 且 列表已藏 且 可见） */
  var det=d('publicDetail');
  if(det && det.innerHTML && det.offsetParent!==null && d('activityList') && d('activityList').style.display==='none'){
    var aid=(typeof window.__curActId!=='undefined')?window.__curActId:null;
    if(aid!=null) forwardStack.push(function(){ if(window.openAct) window.openAct(aid); });
    if(window.backList) window.backList(); return true;
  }
  /* 3) 商城商品详情 */
  var pd=d('productDetail');
  if(pd && pd.offsetParent!==null && pd.style.display!=='none'){
    var pid=(typeof window.__curProductId!=='undefined')?window.__curProductId:null;
    if(pid!=null) forwardStack.push(function(){ if(window.openProduct) window.openProduct(pid); });
    if(window.showMallList) window.showMallList(); return true;
  }
  /* 4) 商城购物车 */
  var cv=d('cartView');
  if(cv && cv.offsetParent!==null && cv.style.display!=='none'){
    forwardStack.push(function(){ if(window.openCart) window.openCart(); });
    if(window.showMallList) window.showMallList(); return true;
  }
  /* 5) 底部 tab：退到上一个 tab（多步） */
  if(tabHist.length){
    var p=tabHist.pop();
    forwardStack.push(function(){ if(window.wv) window.wv(p); });
    if(window.wv) window.wv(p); return true;
  }
  return false;
}
/* ── 前进：退回到刚离开的界面 ─────────────────────────────────────────── */
function appForward(){
  var f=forwardStack.pop();
  if(!f) return false;
  f(); return true;
}

/* ── 钩住导航入口：记录 tab 历史 + 清空前进栈 ──────────────────────────── */
var _wvOri=window.wv;
window.wv=function(id,b){
  var cur=activeTab();
  if(cur && cur!==id && tabHist[tabHist.length-1]!==cur) tabHist.push(cur);
  return _wvOri?_wvOri(id,b):undefined;
};
['openAct','openProduct','openCart','pushMe'].forEach(function(fn){
  var ori=window[fn];
  if(typeof ori==='function'){
    window[fn]=function(){
      clearFwd();
      if(fn==='openAct') window.__curActId=arguments[0];
      else if(fn==='openProduct') window.__curProductId=arguments[0];
      return ori.apply(this,arguments);
    };
  }
});

/* ── 手势监听（passive，不拦截原生滚动）────────────────────────────────
   2026-10-31 修正：上一版只在屏幕最边缘 28px 内起手才触发，顾客在页面中部左滑
   根本进不了手势分支 —— 所以「左滑返回」形同虚设。改为：页面任意位置起手的
   横向滑动都能触发（左滑返回 / 右滑前进），只要不是下列例外：
   · 抽屉 / 弹层 / 对话框打开时（交给原生 / 内部手势，如通知左滑删除）；
   · 焦点在输入框；
   · 起手点在横向滚动容器里（主视觉 / 团期卡 / 商城头图等轮播）；
   · 竖向滚动（dy 占主导）直接放行，不影响正常上下浏览。 */
document.addEventListener('touchstart', function(e){
  if(e.touches.length!==1){ edge=null; return; }
  if(isSheetOpen()){ edge=null; return; }
  var ae=document.activeElement;
  if(ae && /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName)){ edge=null; return; }
  if(inHScroller(e.target)){ edge=null; return; }
  var t=e.touches[0];
  sx=t.clientX; sy=t.clientY; edge='any';   // 任意位置起手都进入手势判定
}, {passive:true});

document.addEventListener('touchend', function(e){
  if(!edge) return;
  var t=e.changedTouches[0];
  var dx=t.clientX-sx, dy=t.clientY-sy;
  edge=null;
  if(Math.abs(dx)<MIN_SWIPE) return;        // 滑动距离太短，不当作手势
  if(Math.abs(dy)>Math.abs(dx)) return;      // 竖向滚动，放行给原生
  if(dx<0) appBack();                        // 左滑 → 返回上一个界面
  else appForward();                          // 右滑 → 前进到刚退回的界面
}, {passive:true});

window.NavBack=appBack;
window.NavForward=appForward;
})();
