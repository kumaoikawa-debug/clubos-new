/* 平台端紧凑页头 — 多栏目巡检（2026-10-24）
 * 遍历平台端主要栏目，量页头高度 / 首个工作区块位置，检查横向溢出与元素重叠，
 * 每个栏目出一张截图到 /tmp/plat_views/<view>.png。
 * 用法：NODE_PATH=<workspace>/node_modules node scripts/ui_header_walk.mjs [baseUrl]
 */
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
import { mkdirSync } from "fs";

const BASE = process.argv[2] || "http://127.0.0.1:8010";
const PW = process.env.STAGING_PASSWORD || "Staging-2026-ClubOS-demo!";
const VIEWS = ["pdash", "clubs", "pcredits", "ppoints", "pbenefits", "products", "supply", "warehouse", "finance", "analytics", "orders", "aftersales", "commissions"];

mkdirSync("/tmp/plat_views", { recursive: true });
const browser = await chromium.launch({ channel: "chrome" });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

const errs = [];
page.on("pageerror", (e) => errs.push("pageerror: " + e.message));
page.on("console", (m) => { if (m.type() === "error") errs.push("console: " + m.text()); });

await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" }).catch(() => {});
await page.evaluate(async ([u, p]) => {
  await fetch("/api/auth/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: u, password: p }),
  });
}, ["admin", PW]);
await page.goto(BASE + "/platform/#pdash", { waitUntil: "networkidle" });
await page.waitForTimeout(1000);

console.log("view         topbar  viewHead  firstBlock  headBottom  overflow  overlap");
let bad = 0;
for (const v of VIEWS) {
  await page.evaluate((vv) => {
    const b = document.querySelector(`.nav button[data-view="${vv}"]`);
    if (b) b.click();
  }, v);
  await page.waitForTimeout(700);

  const m = await page.evaluate(() => {
    const box = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { t: r.top, b: r.bottom, h: r.height, x: r.right };
    };
    const act = document.querySelector(".view.active");
    const firstBlock = act ? (act.querySelector(".panel-title") || act.querySelector(".grid,.table,.panel,.section,.stat,.card")) : null;
    const fb = firstBlock ? act.querySelector(".panel-title") || act.querySelector(".grid,.table,.panel,.section,.stat,.card") : null;
    const fbRect = fb ? fb.getBoundingClientRect() : null;
    const tb = document.querySelector(".main>.topbar");
    const vh = document.querySelector(".view.active>.panel-title") || document.querySelector(".view.active>h2");
    const vhRect = vh ? vh.getBoundingClientRect() : null;
    // 重叠检测：pill 与搜索框、pill 与标题
    const rr = (s) => { const e = document.querySelector(s); return e ? e.getBoundingClientRect() : null; };
    const hit = (a, b) => !!a && !!b && !(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top);
    return {
      topbar: tb ? +tb.getBoundingClientRect().height.toFixed(1) : null,
      viewHead: vhRect ? +vhRect.height.toFixed(1) : null,
      firstBlock: fbRect ? +fbRect.top.toFixed(1) : null,
      headBottom: vhRect ? +vhRect.bottom.toFixed(1) : null,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      overlap: hit(rr(".main>.topbar .pill"), rr(".main>.workspace-bar .workspace-search")) || hit(rr(".main>.topbar .pill"), rr(".main>.topbar .title")),
    };
  });
  const ok = m.overflow <= 0 && !m.overlap;
  if (!ok) bad++;
  console.log(
    v.padEnd(12) + String(m.topbar).padEnd(8) + String(m.viewHead).padEnd(10) +
    String(m.firstBlock).padEnd(12) + String(m.headBottom).padEnd(12) +
    (m.overflow > 0 ? ("⚠+" + m.overflow).padEnd(10) : "0".padEnd(10)) +
    (m.overlap ? "⚠ 重叠" : "无") + (ok ? "" : "  ❌")
  );
  await page.screenshot({ path: `/tmp/plat_views/${v}.png` });
}

// 窄屏复检（1024 / 768）
for (const w of [1024, 768]) {
  await page.setViewportSize({ width: w, height: 800 });
  await page.evaluate(() => document.querySelector('.nav button[data-view="products"]')?.click());
  await page.waitForTimeout(600);
  const m = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    topbar: +document.querySelector(".main>.topbar").getBoundingClientRect().height.toFixed(1),
  }));
  console.log(`\n${w}px 宽：topbar=${m.topbar}px 横向溢出=${m.overflow}`);
  await page.screenshot({ path: `/tmp/plat_views/products_${w}.png` });
}

console.log("\nJS 错误:", errs.length ? errs.slice(0, 5) : "无");
console.log("异常栏目数:", bad);
await browser.close();
