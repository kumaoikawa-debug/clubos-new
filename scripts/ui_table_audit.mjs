/* 平台端表格列宽巡检（2026-10-24 UI 走查）
 * 目的：抓「中文逐字竖排」——表格列被压到 1 字宽时中文会逐字换行。
 * 指标：每张表的列数 / 最窄列宽 / 竖排嫌疑单元格数 / 容器是否可横向滚动 / 行高。
 * 做法：先用新 CSS 量一次，再从 CSSOM 删掉本轮的表格规则量一次「改前」。
 * 用法：NODE_PATH=<workspace>/node_modules node scripts/ui_table_audit.mjs [baseUrl]
 */
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
import { mkdirSync } from "fs";

const BASE = process.argv[2] || "http://127.0.0.1:8010";
const PW = process.env.STAGING_PASSWORD || "Staging-2026-ClubOS-demo!";
const VIEWS = ["pdash", "clubs", "pcredits", "ppoints", "pbenefits", "products", "supply", "warehouse", "finance", "analytics", "orders", "aftersales", "commissions"];
const WIDTHS = [1440, 1024, 768];

mkdirSync("/tmp/plat_tables", { recursive: true });

const browser = await chromium.launch({ channel: "chrome" });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" }).catch(() => {});
/* /login 页面自身会跳转，趁它跳转时 evaluate 会撞上「Execution context destroyed」。
   先等页面稳定，登录请求也允许失败（TEST_OPEN 下本来就不需要登录态）。 */
await page.waitForTimeout(900);
await page.evaluate(async ([u, p]) => {
  await fetch("/api/auth/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: u, password: p }),
  });
}, ["admin", PW]).catch(() => {});
await page.goto(BASE + "/platform/#pdash", { waitUntil: "networkidle" });
await page.waitForTimeout(1000);

/* 竖排嫌疑：单元格高度明显超过两行、但文字很短 → 中文被逐字竖排 */
const probe = () =>
  page.evaluate(() => {
    const out = [];
    document.querySelectorAll(".view.active table.table").forEach((t, ti) => {
      const ths = [...t.querySelectorAll("thead th")];
      const cols = ths.length;
      if (!cols) return;
      const widths = ths.map((th) => +th.getBoundingClientRect().width.toFixed(0));
      /* 竖排判据：中文被压成单字时，文本的「行盒数」≈「字符数」。
         用 Range.getClientRects() 取真实行盒，比用高度粗判准得多。 */
      let vertical = 0;
      const range = document.createRange();
      t.querySelectorAll("tbody td").forEach((td) => {
        const txt = (td.textContent || "").trim();
        if (!txt) return;
        range.selectNodeContents(td);
        const rects = [...range.getClientRects()].filter((r) => r.height > 2 && r.width > 0.5);
        /* Range 的 rect 是「行内盒子」：一个 td 里有 5 个按钮就会返回 5 个盒子，
           但它们可能在同一视觉行上。必须按 top 聚类成真正的行数。 */
        const tops = [];
        rects.forEach((r) => {
          /* 容差 8px：inline-block 标签的「文本行盒」与「元素盒」top 会差 4~6px，
             那是基线差不是换行；真换行的行距是 17~20px，区分得开。 */
          if (!tops.some((t) => Math.abs(t - r.top) < 8)) tops.push(r.top);
        });
        const lines = tops.length;
        if (lines >= 2 && txt.length / lines < 2.2) vertical++;
      });
      const rowH = t.querySelector("tbody tr") ? +t.querySelector("tbody tr").getBoundingClientRect().height.toFixed(0) : 0;
      const box = t.parentElement;
      const scrollable = box.scrollWidth > box.clientWidth + 1;
      out.push({
        ti, cols,
        minCol: Math.min(...widths),
        vertical,
        rowH,
        tableW: +t.getBoundingClientRect().width.toFixed(0),
        scrollable,
        overflowX: getComputedStyle(box).overflowX,
      });
    });
    return out;
  });

const strip = () =>
  page.evaluate(() => {
    const sh = [...document.styleSheets].find((s) => s.href && s.href.includes("platform.css"));
    if (!sh) return -1;
    const rules = sh.cssRules;
    let start = -1;
    for (let i = 0; i < rules.length; i++) {
      const r = rules[i];
      /* Chrome 会规范化 selectorText（:has(> .table) → :has(>table)），
         所以按 cssText 特征串找，别按手写选择器串匹配。 */
      /* ★ CSSOM 序列化会写成 `overflow-x: auto`（冒号后带空格），
         用字面串匹配会永远匹配不上 —— 必须用正则容忍空格。 */
      if (r.cssText && /has\(\.table\)/.test(r.cssText) && /overflow-x:\s*auto/.test(r.cssText)) { start = i; break; }
    }
    if (start < 0) return -1;
    const n = rules.length - start;
    for (let i = rules.length - 1; i >= start; i--) sh.deleteRule(i);
    return n;
  });

let totalAfter = 0, totalBefore = 0;
for (const w of WIDTHS) {
  await page.setViewportSize({ width: w, height: 900 });
  for (const v of VIEWS) {
    await page.evaluate((vv) => document.querySelector(`.nav button[data-view="${vv}"]`)?.click(), v);
    await page.waitForTimeout(600);
    const after = await probe();
    const n = await strip();
    await page.waitForTimeout(150);
    const before = await probe();
    await page.reload({ waitUntil: "networkidle" });
    await page.waitForTimeout(700);
    await page.evaluate((vv) => document.querySelector(`.nav button[data-view="${vv}"]`)?.click(), v);
    await page.waitForTimeout(600);

    const sum = (arr) => arr.reduce((a, x) => a + x.vertical, 0);
    const minCol = (arr) => (arr.length ? Math.min(...arr.map((x) => x.minCol)) : 0);
    totalAfter += sum(after);
    totalBefore += sum(before);
    const line = `${String(w).padEnd(5)}${v.padEnd(11)}表 ${after.length} 张  竖排嫌疑 改前 ${String(sum(before)).padEnd(3)}→ 改后 ${String(sum(after)).padEnd(3)} 最窄列 ${String(minCol(before)).padEnd(5)}→ ${String(minCol(after)).padEnd(5)}`;
    console.log(line);
    if (after.length) {
      const t = after.find((x) => x.vertical) || after[0];
      console.log(`      最宽表 ${t.cols} 列 / 宽 ${t.tableW}px / 行高 ${t.rowH}px / 容器 overflow-x=${t.overflowX} 可滚动=${t.scrollable}`);
    }
    if (w === 1440 || w === 1024) await page.screenshot({ path: `/tmp/plat_tables/${v}_${w}.png` });
  }
  console.log("");
}
console.log(`竖排嫌疑单元格合计：改前 ${totalBefore} → 改后 ${totalAfter}`);
await browser.close();
