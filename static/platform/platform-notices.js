/* ==========================================================================
   ClubOS 总平台后台「新增未读」提醒（2026-11-06 / #621）

   为什么单独一个文件、且必须是 index.html 里最后一个脚本：
     platform.js 第 1 行定义 window.onView，experience-core.js 和
     workflows-platform-coupon.js 又各包了一层。脚本加载顺序 = 覆盖优先级，
     谁后加载谁是最外层。这里放最后，包一层不会吃掉前面任何一层的视图加载。

   语义区分（这是需求里最容易做错的一点）：
     「存量待办数」= 待发货 3 单这种一直挂着的数；「新增未读」= 自上次点开
     这个栏目以来又发生了什么。后端用事件账本 + 每栏目水位算后者，
     这里只负责把它显示出来：侧栏红点数字、标签页 (N)、新事件 toast。

   竞态防护（沿用 C 端全局提醒那套血泪经验）：
     关/开视图、切前台、60 秒轮询会连发请求；稍早发出的「未读=1」旧响应
     可能晚到，把刚刚清零的角标盖回来 → 串行队列 + 请求序号只认最后一次
     + 点开瞬间本地先清零 + GET 后作废旧响应。
   ========================================================================== */
(function () {
  'use strict';

  var CHANNELS = { orders: '订单履约', aftersales: '售后管理', pksettle: 'PEAKTRACE 结算' };
  var KEYS = Object.keys(CHANNELS);
  var counts = { orders: 0, aftersales: 0, pksettle: 0 };
  var baseTitle = document.title.replace(/^\(\d+\)\s*/, '');
  var seq = 0;
  var chain = Promise.resolve();
  var first = true;

  function total() {
    return KEYS.reduce(function (s, k) { return s + (Number(counts[k]) || 0); }, 0);
  }

  /* 侧栏按钮可能被别的脚本重绘 innerHTML，写死的子节点会被吃掉 ——
     每次 paint 都重新确认角标还在；MutationObserver 负责被重绘时立刻补回。 */
  function ensureBadge(view) {
    var btn = document.querySelector('.nav button[data-view="' + view + '"]');
    if (!btn) return null;
    var b = btn.querySelector('.plat-notice-badge');
    if (!b) {
      b = document.createElement('i');
      b.className = 'plat-notice-badge';
      b.setAttribute('aria-label', '新增未读');
      btn.appendChild(b);
      btn.classList.add('has-notice-badge');
    }
    return b;
  }

  function paint() {
    KEYS.forEach(function (k) {
      var b = ensureBadge(k);
      if (!b) return;
      var n = Number(counts[k]) || 0;
      var txt = n > 99 ? '99+' : String(n);
      /* textContent 赋值即便内容相同也会替换文本节点 → MutationObserver 会
         再次回调 → 无限循环。必须先比对。 */
      if (b.textContent !== txt) b.textContent = txt;
      b.classList.toggle('is-on', n > 0);
    });
    var t = total();
    document.title = t ? '(' + (t > 99 ? '99+' : t) + ') ' + baseTitle : baseTitle;
  }

  function enqueue(fn) {
    chain = chain.then(fn, fn);
    return chain;
  }

  async function poll() {
    var my = ++seq;
    try {
      var d = await api('/api/platform/notices/summary', { cache: 'no-store' });
      if (my !== seq) return;            // 期间已经点开视图/发了新请求：丢弃旧响应
      var c = (d && d.channels) || {};
      var grown = [];
      KEYS.forEach(function (k) {
        var n = Number(c[k]) || 0;
        var old = Number(counts[k]) || 0;
        if (!first && n > old) grown.push(CHANNELS[k] + ' +' + (n - old));
        counts[k] = n;
      });
      paint();
      if (grown.length && typeof toast === 'function') {
        toast('新增提醒：' + grown.join('、'));
      }
      first = false;
    } catch (e) {
      /* 轮询是后台增强，失败静默；下一轮（60s / 切前台）会再来 */
    }
  }

  function pollQueued() { enqueue(poll); }

  function openChannel(view) {
    if (!CHANNELS[view]) return;
    counts[view] = 0;                   // 点开瞬间本地先清零，不等网络
    paint();
    seq++;                              // 作废所有早于本次点开的 GET 响应
    enqueue(async function () {
      try {
        var d = await api('/api/platform/notices/read', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ channel: view }),
          allowRepeat: true             // 绕过 shared.js 的 700ms 写重放去重
        });
        if (d && d.channels) {
          KEYS.forEach(function (k) { counts[k] = Number(d.channels[k]) || 0; });
          paint();
        }
      } catch (e) { /* 标记失败不影响浏览，下轮轮询会重试 */ }
    });
  }

  /* 包在最外层：先让既有 onView 把 orders/aftersales 等视图数据加载出来，
     再把该栏目标记为已读。navInit() 的点击处理器在点击时才读 window.onView，
     因此在这里替换是有效的。 */
  var prevOnView = window.onView;
  window.onView = function (view) {
    if (typeof prevOnView === 'function') {
      try { prevOnView(view); } catch (e) { /* 不阻断既有视图加载 */ }
    }
    openChannel(view);
  };

  /* 侧栏被重绘（其它脚本改 .nav innerHTML）时立刻补回角标 */
  var nav = document.querySelector('.nav');
  if (nav && window.MutationObserver) {
    new MutationObserver(function () { paint(); }).observe(nav, { childList: true, subtree: true });
  }

  paint();
  pollQueued();
  setInterval(pollQueued, 60000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) pollQueued();
  });
  window.addEventListener('focus', pollQueued);
})();
