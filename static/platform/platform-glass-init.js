/* ─────────────────────────────────────────────────────────────────────────
   ClubOS 总平台端 · 全局皮肤初始化（2026-11-01）
   注入固定流体背景层（blob + 等高线 / 山脊 / 小径点 户外母题）。
   与俱乐部端同一套视觉语言，仅作用范围不同。
   不改 platform.js：只读取 DOM 注入背景层，失败静默。
   ───────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  function injectBg() {
    if (document.querySelector(".plat-bg")) return;
    var bg = document.createElement("div");
    bg.className = "plat-bg";
    bg.setAttribute("aria-hidden", "true");
    bg.innerHTML =
      '<span class="blob b1"></span><span class="blob b2"></span><span class="blob b3"></span>' +
      '<span class="blob b4"></span><span class="blob b5"></span><span class="blob b6"></span>' +
      // 等高线（徒步地图感）
      '<svg class="topo" viewBox="0 0 600 600" fill="none" xmlns="http://www.w3.org/2000/svg">' +
        '<path d="M60 430 C160 360 240 400 320 330 C400 260 470 300 540 250"/>' +
        '<path d="M40 480 C150 410 250 450 330 380 C420 305 500 345 560 300"/>' +
        '<path d="M80 380 C180 310 270 350 350 280 C440 215 500 250 560 200"/>' +
        '<path d="M120 520 C210 460 300 495 380 430 C470 360 520 395 580 350"/>' +
        '<path d="M150 560 C230 510 320 540 400 480 C490 420 540 455 590 410"/>' +
        '<circle cx="430" cy="200" r="4"/>' +
      '</svg>' +
      // 山脊剪影（底部）
      '<svg class="ridge" viewBox="0 0 1440 220" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">' +
        '<path d="M0 220 L0 150 C120 90 220 130 340 100 C460 70 560 120 700 90 C840 60 940 130 1080 100 C1220 72 1320 120 1440 95 L1440 220 Z" opacity="0.5"/>' +
        '<path d="M0 220 L0 175 C140 140 240 165 360 140 C480 115 600 160 760 135 C920 110 1040 165 1180 140 C1300 120 1380 150 1440 140 L1440 220 Z" opacity="0.35"/>' +
      '</svg>' +
      // 飘浮小径点（虚线轨迹）
      '<svg class="trail" viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">' +
        '<path d="M10 100 C40 70 60 80 80 50 C95 30 105 40 112 20" stroke="#2e6b4e" stroke-width="2.4" stroke-dasharray="1 9" stroke-linecap="round"/>' +
      '</svg>';
    document.body.insertBefore(bg, document.body.firstChild);
  }

  function init() {
    injectBg();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
