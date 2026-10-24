/* 平台端紧凑页头 — 前后高度实测（2026-10-24）
 * 用法：NODE_PATH=<workspace>/node_modules node scripts/ui_header_measure.mjs [baseUrl]
 * 做法：进 /platform/#products，先用新 CSS 量一次，再从 CSSOM 删掉本轮的紧凑规则
 *       量一次「改前」，同时各出一张截图对比。 */
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = process.argv[2] || "http://127.0.0.1:8010";
const PW = process.env.STAGING_PASSWORD || "Staging-2026-ClubOS-demo!";

const browser = await chromium.launch({ channel: "chrome" });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" }).catch(() => {});
const st = await page.evaluate(async ([u, p]) => {
  const r = await fetch("/api/auth/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: u, password: p }),
  });
  return r.status;
}, ["admin", PW]);

await page.goto(BASE + "/platform/#products", { waitUntil: "networkidle" });
await page.waitForTimeout(1200);

const measure = () =>
  page.evaluate(() => {
    const r = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const b = el.getBoundingClientRect();
      return { top: +b.top.toFixed(1), h: +b.height.toFixed(1), bottom: +b.bottom.toFixed(1) };
    };
    const main = document.querySelector(".main");
    const cs = getComputedStyle(main);
    const active = document.querySelector(".view.active");
    return {
      main: { padTop: cs.paddingTop, ...r(".main") },
      workspace: r(".main>.workspace-bar"),
      topbar: r(".main>.topbar"),
      viewHead: r(".view.active>.panel-title") || r(".view.active>h2"),
      firstRow: r(".view.active .table thead") || r(".view.active .plat-prodfilter") || r(".view.active .panel"),
      activeTop: active ? +active.getBoundingClientRect().top.toFixed(1) : null,
    };
  });

const after = await measure();
await page.screenshot({ path: "/tmp/plat_header_after.png", fullPage: false });

// 从 CSSOM 删掉本轮的紧凑规则（第一条 .main{padding-top:16px} 到末尾），恢复改动前
const removed = await page.evaluate(() => {
  const sh = [...document.styleSheets].find((s) => s.href && s.href.includes("platform.css"));
  if (!sh) return -1;
  const rules = sh.cssRules;
  let start = -1;
  for (let i = 0; i < rules.length; i++) {
    const r = rules[i];
    if (r.selectorText === ".main" && r.style && r.style.paddingTop === "16px") { start = i; break; }
  }
  if (start < 0) return -1;
  const n = rules.length - start;
  for (let i = rules.length - 1; i >= start; i--) sh.deleteRule(i);
  return n;
});
await page.waitForTimeout(300);
const before = await measure();
await page.screenshot({ path: "/tmp/plat_header_before.png", fullPage: false });

const fmt = (o) => (o ? `top=${o.top} h=${o.h}` : "—");
console.log("登录状态:", st, "| 删掉的紧凑规则数:", removed);
console.log("\n           改前                    改后");
for (const k of ["workspace", "topbar", "viewHead", "firstRow"]) {
  console.log(String(k).padEnd(11) + fmt(before[k]).padEnd(24) + fmt(after[k]));
}
console.log("\n首个工作区内容距视口顶部：改前 " + (before.firstRow?.top ?? "-") + "px → 改后 " + (after.firstRow?.top ?? "-") + "px");
console.log("页头总占用（main 顶到栏目标题底）：改前 " + ((before.viewHead?.bottom ?? 0) - (before.main?.top ?? 0)).toFixed(0) + "px → 改后 " + ((after.viewHead?.bottom ?? 0) - (after.main?.top ?? 0)).toFixed(0) + "px");
console.log("首屏可见表格行数（表格可视高度估算）：改前 " + Math.max(0, 900 - (before.firstRow?.top ?? 900)) + "px → 改后 " + Math.max(0, 900 - (after.firstRow?.top ?? 900)) + "px");

await browser.close();
