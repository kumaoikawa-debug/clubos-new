/**
 * UI 链路巡检（Playwright）— 真点每个导航，看视图有没有切、有没有报错、面板有没有内容。
 *
 *   NODE_PATH=<workspace>/node_modules node scripts/ui_chain_walk.mjs [baseUrl]
 *
 * 只读为主：只做「点击导航」这一件事，不提交任何表单、不删任何数据。
 */
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = process.argv[2] || "http://127.0.0.1:8321";
const PW = process.env.STAGING_PASSWORD || "Staging-2026-ClubOS-demo!";

const ENDS = [
  { name: "C端 /web", path: "/web", user: "member1", navSel: ".web-nav [data-wv]", attr: "data-wv" },
  { name: "后台 /club", path: "/club", user: "club1_admin", navSel: ".nav [data-view]", attr: "data-view" },
  { name: "总平台 /platform", path: "/platform", user: "platform_admin", navSel: ".nav [data-view]", attr: "data-view" },
  { name: "领队端 /leader", path: "/leader", user: "leader1", navSel: "", attr: "" },
];

const PROBE = `
  window.__err = [];
  window.addEventListener('error', e => window.__err.push('onerror: ' + (e.message || '')));
  window.addEventListener('unhandledrejection', e => window.__err.push('reject: ' + (e.reason && e.reason.message || e.reason)));
  const _f = window.fetch;
  window.__bad = [];
  window.fetch = function (...a) {
    const u = typeof a[0] === 'string' ? a[0] : (a[0] && a[0].url) || '';
    return _f.apply(this, a).then(r => {
      if (r.status >= 400) window.__bad.push(r.status + ' ' + (a[1] && a[1].method || 'GET') + ' ' + u);
      return r;
    }, e => { window.__bad.push('NET ' + u + ' ' + e.message); throw e; });
  };
`;

const SIGN = `
  (() => {
    const vis = [...document.querySelectorAll('[id]')].filter(el => {
      const r = el.getBoundingClientRect();
      return el.offsetParent !== null && r.height > 120 && r.width > 200;
    });
    return {
      ids: vis.slice(-3).map(e => e.id),
      text: document.body.innerText.replace(/\\s+/g, ' ').trim().slice(0, 400),
      len: document.body.innerText.trim().length,
    };
  })()
`;

const out = [];
const browser = await chromium.launch({ channel: "chrome" });

for (const end of ENDS) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const consoleErrs = [];
  const badResp = [];
  page.on("console", m => { if (m.type() === "error") consoleErrs.push(m.text().slice(0, 160)); });
  page.on("pageerror", e => consoleErrs.push("pageerror: " + e.message.slice(0, 160)));
  page.on("response", r => { if (r.status() >= 400) badResp.push(r.status() + " " + r.url().replace(BASE, "")); });

  // 登录（同源种 cookie）
  // ★ /login 页面自身会跳转，趁它跳转时 evaluate 会撞上
  //   「Execution context was destroyed, most likely because of a navigation」。
  //   先等页面稳定，登录失败也允许继续（TEST_OPEN 下本来就不需要登录态）。
  await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" }).catch(() => {});
  await page.waitForTimeout(1200);
  const login = await page.evaluate(async ([u, p]) => {
    const r = await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: u, password: p }),
    });
    return r.status;
  }, [end.user, PW]).catch(() => 0);

  await page.goto(BASE + end.path, { waitUntil: "networkidle" }).catch(() => {});
  await page.waitForTimeout(2500);
  await page.evaluate(PROBE);

  const rec = { end: end.name, login, navs: [], consoleErrs: [], badResp: [] };

  if (end.navSel) {
    const items = await page.$$eval(end.navSel, (els, attr) =>
      els.map(e => ({ t: (e.textContent || "").trim().slice(0, 14), v: e.getAttribute(attr) })),
      end.attr);
    for (const it of items) {
      const before = await page.evaluate(SIGN);
      const errBefore = consoleErrs.length, badBefore = badResp.length;
      await page.evaluate(PROBE);
      let clicked = false;
      try {
        await page.click(`${end.navSel}[${end.attr}="${it.v}"]`, { timeout: 3000 });
        clicked = true;
      } catch (e) { /* 不可见/被遮挡 */ }
      await page.waitForTimeout(1800);
      const after = await page.evaluate(SIGN);
      const probe = await page.evaluate(() => ({ err: window.__err || [], bad: window.__bad || [] }));
      rec.navs.push({
        tab: it.t || it.v,
        target: it.v,
        clicked,
        changed: after.text !== before.text || String(after.ids) !== String(before.ids),
        activeIds: after.ids,
        textLen: after.len,
        thin: after.len < 120,
        err: [...probe.err, ...consoleErrs.slice(errBefore)].slice(0, 4),
        bad: [...probe.bad, ...badResp.slice(badBefore)].slice(0, 4),
      });
      // 关掉可能弹出的遮罩，避免污染下一次点击
      await page.evaluate(() => document.querySelectorAll(".ux-overlay,.modal.show").forEach(e => e.remove()));
    }
  }

  rec.consoleErrs = consoleErrs.slice(0, 8);
  rec.badResp = [...new Set(badResp)].slice(0, 12);
  out.push(rec);
  await ctx.close();
}
await browser.close();

// ---------- 报告 ----------
let hard = 0, soft = 0;
for (const r of out) {
  console.log("\n" + "=".repeat(74));
  console.log(`${r.end}   login=${r.login}`);
  console.log("=".repeat(74));
  if (!r.navs.length) {
    console.log(`  （无导航项，单页）控制台错误 ${r.consoleErrs.length} 条`);
    r.consoleErrs.forEach(e => console.log("    ! " + e));
  }
  for (const n of r.navs) {
    const flags = [];
    if (!n.clicked) flags.push("点不到");
    if (n.clicked && !n.changed) flags.push("点了没反应");
    if (n.err.length) flags.push("JS错误");
    if (n.bad.length) flags.push(n.bad[0]);
    if (n.thin) flags.push("内容偏少");
    const ok = flags.length === 0;
    if (!ok && !n.thin) hard++;
    if (!ok && n.thin && flags.length === 1) soft++;
    console.log(`  ${ok ? "✅" : "⚠️ "} ${n.tab.padEnd(8)} -> ${String(n.target).padEnd(12)} 文本${String(n.textLen).padStart(5)}  ${flags.join(" | ")}`);
    n.err.forEach(e => console.log("        JS: " + e));
    n.bad.forEach(b => console.log("        HTTP: " + b));
  }
  if (r.badResp.length) {
    console.log("  页面级失败请求:");
    r.badResp.forEach(b => console.log("    " + b));
  }
}
console.log("\n" + "=".repeat(74));
console.log(`硬失败 ${hard} · 需人工确认 ${soft}`);
console.log("=".repeat(74));
process.exit(hard > 0 ? 1 : 0);
