/* 后台页头说明「一行省略 + 完整信息不丢」（2026-11-08）
   配套 admin-workspace.css 末尾的紧凑工作区规则。
   CSS 把页头 .sub 压成一行，这里做两件事：
     1) 把完整文案写进 title —— 浏览器原生 tooltip 不受 overflow:hidden 影响，
        比 ::after 浮层可靠，也不撑开布局；
     2) 点击就地展开/收起，给触屏（没有 hover）一条等价通路。
   只在 club / platform 两个后台端的 index.html 加载，C 端与领队端不受影响。
   __adminCompact 同时作为「浏览器加载的是哪一版代码」的自检标记。 */
(function () {
  'use strict';
  window.__adminCompact = '20261108b';
  // 有的视图把 .panel-title 放在 .card 里（如 ai-config），所以用后代而非直接子选择器
  var SEL = '.view .panel-title > div:first-child > .sub, .hero-action p';
  var HIT = '.panel-title .sub, .hero-action p';

  function annotate() {
    var nodes;
    try { nodes = document.querySelectorAll(SEL); } catch (e) { return; }
    Array.prototype.forEach.call(nodes, function (n) {
      var t = (n.textContent || '').trim();
      if (!t) return;
      if (!n.getAttribute('title')) n.setAttribute('title', t);
      if (n.classList.contains('is-expanded')) { n.classList.remove('is-clamped'); return; }
      if (n.scrollHeight > n.clientHeight + 2) n.classList.add('is-clamped');
      else n.classList.remove('is-clamped');
    });
  }

  function onClick(e) {
    if (!e.target || !e.target.closest) return;
    var n = e.target.closest(HIT);
    if (!n || !n.getAttribute('title')) return;
    if (n.classList.contains('is-expanded')) n.classList.remove('is-expanded');
    else n.classList.add('is-expanded');
    setTimeout(annotate, 0);
  }

  function init() {
    annotate();
    document.addEventListener('click', onClick);
    // 切视图 / JS 注入页头（如 ai-config）后补注；只听 childList，
    // annotate 幂等且带 180ms 防抖，不会与自身 setAttribute 形成循环。
    if (window.MutationObserver) {
      var timer = null;
      new MutationObserver(function () {
        if (timer) clearTimeout(timer);
        timer = setTimeout(annotate, 60);
      }).observe(document.body, { childList: true, subtree: true });
    }
    // 有些板块（ai-config / costbom）的页头是切视图后异步注入的，
    // 单靠 MutationObserver 防抖会赶不上首次渲染；点击导航时补几次。
    document.addEventListener('click', function (e) {
      if (e.target && e.target.closest && e.target.closest('[data-view]')) {
        setTimeout(annotate, 0); setTimeout(annotate, 220); setTimeout(annotate, 700);
      }
    }, true);
    window.__adminCompactAnnotate = annotate;
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
