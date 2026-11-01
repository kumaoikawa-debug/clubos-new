/* ─────────────────────────────────────────────────────────────────────────
   ClubOS 俱乐部端 · 全局皮肤初始化（2026-11-01）
   1) 注入固定流体背景层（blob + 等高线 / 山脊 / 小径点 户外母题）
   2) 拉取真实 BI 数据，覆盖 #dash 的「示意」图表：
        · 折线：trend[].activityNetCashFlow（活动净现金流）
        · 环图：membership.repeatPayingUserRatioPct（会员复购率）
   不改 club.js：只读取已存在的 DOM 与 CLUB 变量，失败静默保留示意。
   ───────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  function injectBg() {
    if (document.querySelector(".club-bg")) return;
    var bg = document.createElement("div");
    bg.className = "club-bg";
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

  function paintCharts() {
    var club = (typeof CLUB !== "undefined" && CLUB) ? CLUB : 1;
    fetch("/api/club/" + club + "/analytics?windowDays=7", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (d) {
        paintTrend(d.trend || []);
        paintRing(d.membership || {});
        markReal();
      })
      .catch(function () { /* 拉取失败：保留示意，不打断页面 */ });
  }

  function paintTrend(trend) {
    var svg = document.querySelector("#dash .dash-trend");
    if (!svg || !trend.length) return;
    var W = 480, H = 160, pad = 12;
    var vals = trend.map(function (t) { return +(t.activityNetCashFlow || 0); });
    var max = Math.max.apply(null, vals.concat([1]));
    var min = Math.min.apply(null, vals.concat([0]));
    var span = (max - min) || 1;
    var n = vals.length;
    var pts = vals.map(function (v, i) {
      var x = n > 1 ? (i / (n - 1)) * W : W / 2;
      var y = H - pad - ((v - min) / span) * (H - pad * 2);
      return [x, y];
    });
    var line = "M" + pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" L");
    var area = line + " L" + W + "," + H + " L0," + H + " Z";
    var paths = svg.querySelectorAll("path");
    if (paths[0]) paths[0].setAttribute("d", area);
    if (paths[1]) paths[1].setAttribute("d", line);
    var card = svg.closest(".card");
    if (card) {
      var legend = card.querySelector(".dash-legend");
      if (legend) legend.innerHTML = '<span><i style="background:#1D9E75"></i>活动净现金流（¥/天）</span>';
    }
  }

  function paintRing(mem) {
    var ratio = +(mem.repeatPayingUserRatioPct || 0);
    if (ratio > 1) ratio = ratio / 100;        // 容错：后端可能返回 0–100 或 0–1
    if (ratio < 0) ratio = 0;
    if (ratio > 1) ratio = 1;
    var svg = document.querySelector("#dash .ring");
    if (!svg) return;
    var circ = 2 * Math.PI * 50; // ≈ 314
    var c = svg.querySelectorAll("circle");
    if (c[1]) c[1].setAttribute("stroke-dashoffset", (circ * (1 - ratio)).toFixed(1));
    var card = svg.closest(".card");
    if (card) {
      var info = card.querySelector(".ring-info");
      if (info) {
        var b = info.querySelector("b");
        if (b) b.textContent = (ratio * 100).toFixed(0) + "%";
        var p = info.querySelector("p");
        if (p) p.innerHTML = "会员复购率<br><span>本期重复付费会员占比</span>";
      }
    }
  }

  function markReal() {
    // 去掉「示意」字样 → 实时
    var tags = document.querySelectorAll("#dashCharts .tag");
    Array.prototype.forEach.call(tags, function (t) {
      t.textContent = t.textContent.replace("示意", "实时");
    });
    // 标题校正为真实指标
    var h3s = document.querySelectorAll("#dashCharts .panel-title h3");
    Array.prototype.forEach.call(h3s, function (h) {
      if (h.textContent.indexOf("报名趋势") >= 0) h.textContent = "活动净现金流趋势";
      if (h.textContent.indexOf("本月目标完成度") >= 0) h.textContent = "会员复购率";
    });
  }

  function init() {
    injectBg();
    paintCharts();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
