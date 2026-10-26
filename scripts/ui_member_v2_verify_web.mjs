// 会员体系 V2 UI 真点验证 —— C 端段（③ 邀请好友页 ④ 自定义券面包）
// 用法：node scripts/ui_member_v2_verify_web.mjs http://127.0.0.1:8798
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = process.argv[2] || "http://127.0.0.1:8798";
let pass = 0, fail = 0;
function ok(cond, label, extra = "") {
  console.log((cond ? "  PASS  " : "  FAIL  ") + label + (extra && !cond ? `   | ${extra}` : ""));
  cond ? pass++ : fail++;
}
async function waitFn(page, fn, ms = 9000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { if (await page.evaluate(fn)) return true; } catch (e) { /* 跳转中 */ }
    await page.waitForTimeout(300);
  }
  return false;
}

const browser = await chromium.launch({ channel: "chrome" });
try {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const w = await ctx.newPage();
  const errs2 = [];
  w.on("pageerror", e => errs2.push(String(e).slice(0, 120)));
  await w.goto(BASE + "/web/", { waitUntil: "domcontentloaded", timeout: 30000 });
  // 等我的页数据加载完（meLoadAll 异步），入口按钮出现即算就绪
  await w.waitForTimeout(1500);
  await w.evaluate(() => pushMe("me"));
  ok(await waitFn(w, () => !!document.querySelector(".wme-li--invite")), "③ 我的页出现「邀请好友」入口");
  await w.evaluate(() => pushMe("invite"));
  // 等邀请码渲染（IV 开头）
  ok(await waitFn(w, () => /IV[A-Z0-9]{6,}/.test(document.body.innerText)), "③ 邀请页渲染邀请码");
  const invTxt = await w.evaluate(() => document.body.innerText.replace(/\s+/g, " "));
  ok(invTxt.includes("好友") && (invTxt.includes("注册") || invTxt.includes("报名")), "③ 邀请页有奖励说明文案");
  // ④ 券包：meLoadAll 由 loadMemberCenter()（「我的」tab 切换）触发，pushMe 不装数据。
  // 这里直接切 tab 等数据就绪，再进券包子页。
  await w.evaluate(() => window.wv ? wv("wme") : (window.loadMemberCenter && loadMemberCenter()));
  await waitFn(w, () => !!document.querySelector(".wme-li--invite"), 12000);
  await w.evaluate(() => pushMe("coupons"));
  const faceFound = await waitFn(w, () => !!document.querySelector(".ux-cpn"));
  ok(faceFound, "④ 券包渲染出券面");
  const face = await w.evaluate(() => {
    const el = [...document.querySelectorAll(".ux-cpn")].find(e => e.innerText.includes("金卡专享礼券")) || document.querySelector(".ux-cpn");
    if (!el) return { found: false };
    const st = getComputedStyle(el);
    return { found: true, bg: st.backgroundImage + "|" + st.backgroundColor, txt: el.innerText.replace(/\s+/g, " ").slice(0, 100) };
  });
  ok(face.found && face.bg.includes("184, 134, 11"), "④ 自定义券面色 #B8860B 应用", (face.bg || face.txt || "").slice(0, 140));
  ok(face.found && face.txt.includes("满"), "④ 券面含面额门槛文案", face.txt || "");
  const errs2Real = errs2.filter(e => !/favicon|401|Failed to load resource/.test(e));
  ok(errs2Real.length === 0, "③④ C 端无 JS 错误", errs2Real.slice(0, 2).join(" / "));
  await ctx.close();
} finally {
  await browser.close();
}
console.log(`\n${fail === 0 ? "ALL PASS" : "FAILED: " + fail + " 项"} （共 ${pass + fail}）`);
process.exit(fail ? 1 : 0);
