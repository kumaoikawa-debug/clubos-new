// 会员体系 V2 UI 真点验证 —— club 端段（① 会员页区块 ② 券面设计器两步）
// 用法：node scripts/ui_member_v2_verify_club.mjs http://127.0.0.1:8798
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = process.argv[2] || "http://127.0.0.1:8798";
import { appendFileSync } from "fs";
const _log = m => { const line = `[${new Date().toISOString().slice(11,19)}] ${m}\n`; process.stdout.write(line); try { appendFileSync("/tmp/v2club.log", line); } catch (e) {} };
let pass = 0, fail = 0;
function ok(cond, label, extra = "") {
  console.log((cond ? "  PASS  " : "  FAIL  ") + label + (extra && !cond ? `   | ${extra}` : ""));
  cond ? pass++ : fail++;
}
async function waitText(page, fn, ms = 8000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { if (await page.evaluate(fn)) return true; } catch (e) { /* 页面跳转中 */ }
    await page.waitForTimeout(300);
  }
  return false;
}

const browser = await chromium.launch({ channel: "chrome" });
try {
  const pc = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await pc.newPage();
  const errs = [];
  page.on("pageerror", e => errs.push(String(e).slice(0, 120)));
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForTimeout(1500);
  await page.evaluate(() => {
    const btn = [...document.querySelectorAll("button,.nav a,[data-view]")].find(b => (b.textContent || "").includes("会员"));
    if (btn) btn.click();
  });
  ok(await waitText(page, () => document.body.innerText.includes("黑金会员")), "① 会员页渲染黑金会员（等级存在）");
  const bodyTxt = await page.evaluate(() => document.body.innerText.replace(/\s+/g, " "));
  ok(bodyTxt.includes("8 折") || bodyTxt.includes("8折"), "① 等级行显示活动 8 折 tag");
  ok(bodyTxt.includes("升级赠券") || bodyTxt.includes("升级自动赠券"), "① 升级赠券卡存在");
  ok(bodyTxt.includes("升卡好礼券") || bodyTxt.includes("升卡好礼"), "① 升级赠券规则显示绑定的券");
  ok(bodyTxt.includes("拉新"), "① 拉新邀请卡存在");
  ok(bodyTxt.includes("金卡专享礼券"), "① 拉新卡显示已设定邀请券");
  ok(bodyTxt.includes("购买会员"), "① 购买会员开关存在");
  ok(await page.evaluate(() => typeof window.editActivityPoints === "function"), "① editActivityPoints 已挂载");

  /* ── ② 券面设计器两步流程 ── */
  await page.evaluate(() => { window.addClubCoupon && addClubCoupon(); });
  await page.waitForTimeout(1000);
  const step1 = await page.evaluate(() => {
    const ovs = [...document.querySelectorAll(".ux-overlay")].filter(e => e.offsetParent !== null || getComputedStyle(e).position === "fixed");
    const ov = ovs[ovs.length - 1];
    if (!ov) return { r: "NO_OVERLAY" };
    const title = ov.querySelector("input");
    if (title) { title.value = "UI真点设计券"; title.dispatchEvent(new Event("input", { bubbles: true })); }
    const btn = [...ov.querySelectorAll("button")].find(b => /设计券面|下一步/.test(b.textContent || ""));
    if (btn) { btn.click(); return { r: "SUBMITTED", btn: btn.textContent.trim() }; }
    return { r: "NO_BTN", txt: ov.innerText.slice(0, 100) };
  });
  ok(step1.r === "SUBMITTED", "② 第一步业务表单已提交", JSON.stringify(step1).slice(0, 120));
  // 等设计器弹层出现（ux-cpn-designer）
  ok(await waitText(page, () => !!document.querySelector(".ux-cpn-designer")), "② 进入券面设计器");
  const prevOk = await page.evaluate(() => {
    const p = document.querySelector("#cpnPrev");
    return !!(p && p.querySelector(".ux-cpn"));
  });
  ok(prevOk, "② 设计器实时预览渲染出券面");
  const tplCount = await page.evaluate(() => document.querySelectorAll(".ux-cpn-designer__tpl").length);
  ok(tplCount >= 8, "② 内置模板齐备（≥8 款）", String(tplCount));
  // 改自定义色 → 预览跟随
  await page.evaluate(() => {
    const c1 = document.querySelector("#cpnC1");
    c1.value = "#123456";
    c1.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.waitForTimeout(400);
  const faceCustom = await page.evaluate(() => {
    const el = document.querySelector("#cpnPrev .ux-cpn");
    return el ? getComputedStyle(el).backgroundImage : "";
  });
  ok(faceCustom.includes("18, 52, 86"), "② 自定义色 #123456 应用到预览", faceCustom.slice(0, 120));
  // 保存创建
  await page.evaluate(() => {
    const ovs = [...document.querySelectorAll(".ux-overlay")].filter(e => e.offsetParent !== null || getComputedStyle(e).position === "fixed");
    const ov = ovs[ovs.length - 1];
    const btn = ov && ov.querySelector("[data-cpn-create]");
    if (btn) btn.click();
  });
  ok(await waitText(page, () => document.body.innerText.includes("UI真点设计券"), 6000), "② 设计器保存 → 优惠券列表出现新券");
  const errsReal = errs.filter(e => !/favicon|401|Failed to load resource/.test(e));
  ok(errsReal.length === 0, "①② club 端无 JS 错误", errsReal.slice(0, 2).join(" / "));
  await page.evaluate(() => document.querySelectorAll(".ux-overlay").forEach(e => e.remove()));
  await pc.close();
} finally {
  await browser.close();
}
console.log(`\n${fail === 0 ? "ALL PASS" : "FAILED: " + fail + " 项"} （共 ${pass + fail}）`);
process.exit(fail ? 1 : 0);
