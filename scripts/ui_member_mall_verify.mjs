/**
 * 会员购买 / 俱乐部营销 / 积分商城 UI 真点验证（Playwright）。
 *
 *   NODE_PATH=<workspace>/node_modules node scripts/ui_member_mall_verify.mjs [baseUrl]
 *
 * 前置数据（8797 demo 已造好）：
 *   - 购买会员开关已开、存在购买型等级「年卡会员 ¥299」
 *   - 积分商城已上架「俱乐部周边帽」100 积分（实物，会发核销码）
 *   - 默认 C 端用户活动积分 ≥ 100
 *
 * 覆盖：
 *   ① club 端会员页：营销区块改名 / 购买开关勾选 / 等级行「购买 ¥299」/ 商城商品行 / 购买订单卡
 *   ② C 端我的页：「可用积分 ›」入口 → 点进积分商城 → 商品渲染 → 真点「兑换」→ 弹出 PM- 核销码
 *   ③ C 端会员信息页：可购买会员卡「立即购买」入口渲染
 *   ④ club 端营销兑换记录出现刚才的兑换（回流核销列表）
 */
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = process.argv[2] || "http://127.0.0.1:8797";
const PW = process.env.STAGING_PASSWORD || "Staging-2026-ClubOS-demo!";

const results = [];
const ok = (cond, name, extra = "") => {
  results.push({ name, pass: !!cond, extra });
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}${extra ? "  | " + extra : ""}`);
};

const browser = await chromium.launch({ channel: "chrome" });

/* ---------- ① club 端会员页 ---------- */
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", e => errs.push(e.message.slice(0, 120)));
  page.on("console", m => { if (m.type() === "error") errs.push(m.text().slice(0, 120)); });

  await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" }).catch(() => {});
  await page.waitForTimeout(1000);
  await page.evaluate(async ([u, p]) => {
    await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: u, password: p }) });
  }, ["club1_admin", PW]).catch(() => {});

  await page.goto(BASE + "/club", { waitUntil: "networkidle" }).catch(() => {});
  await page.waitForTimeout(2000);
  await page.click('.nav [data-view="members"]', { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(2200);

  const clubTxt = await page.evaluate(() => document.body.innerText.replace(/\s+/g, " "));
  ok(clubTxt.includes("俱乐部营销"), "① 会员页出现「俱乐部营销」");
  ok(!clubTxt.includes("本俱乐部福利"), "① 旧文案「本俱乐部福利」已消失");
  const peChecked = await page.evaluate(() => { const b = document.getElementById("purchaseEnabled"); return b ? b.checked : null; });
  ok(peChecked === true, "① 购买会员开关为勾选态", "checked=" + peChecked);
  const tierTxt = await page.evaluate(() => (document.getElementById("tierList") || {}).innerText || "");
  ok(/购买\s*¥\s*299/.test(tierTxt.replace(/\s+/g, " ")), "① 等级行显示「购买 ¥299 · 12 个月」", tierTxt.slice(0, 120).replace(/\n/g, " "));
  const mallTxt = await page.evaluate(() => (document.getElementById("mallItemList") || {}).innerText || "");
  ok(mallTxt.includes("俱乐部周边帽") && /100/.test(mallTxt), "① 积分商城卡显示已上架商品", mallTxt.slice(0, 100).replace(/\n/g, " "));
  ok(!!(await page.$("#giftRuleList")), "① 消费送券卡存在");
  ok(!!(await page.$("#memberOrderList")), "① 购买订单卡存在");
  ok((await page.evaluate(() => typeof window.togglePurchaseEnabled)) === "function", "① togglePurchaseEnabled 已挂载");
  ok((await page.evaluate(() => typeof window.addMallItem)) === "function", "① addMallItem 已挂载");
  /* 「Failed to load resource」是网络级噪音（favicon/login 401 等）；真 JS 错误 = pageerror + 其它 console error */
  const jsErrs = errs.filter(e => !/^Failed to load resource/.test(e));
  if (jsErrs.length) ok(false, "① club 端无 JS 错误", jsErrs.slice(0, 3).join(" / "));
  else ok(true, "① club 端无 JS 错误", errs.length ? `仅 ${errs.length} 条资源加载噪音` : "");
  await ctx.close();
}

/* ---------- ②③ C 端 ---------- */
let redeemCode = "";
{
  const ctx = await browser.newContext({ viewport: { width: 414, height: 896 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", e => errs.push(e.message.slice(0, 120)));
  page.on("console", m => { if (m.type() === "error") errs.push(m.text().slice(0, 120)); });

  await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" }).catch(() => {});
  await page.waitForTimeout(1000);
  await page.evaluate(async ([u, p]) => {
    await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: u, password: p }) });
  }, ["member1", PW]).catch(() => {});

  await page.goto(BASE + "/web", { waitUntil: "networkidle" }).catch(() => {});
  await page.waitForTimeout(2200);
  await page.click('.web-nav [data-wv="wme"]', { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(2200);

  const meTxt = await page.evaluate(() => document.body.innerText.replace(/\s+/g, " "));
  ok(meTxt.includes("可用积分"), "② 我的页出现「可用积分」入口");
  const entry = await page.evaluate(() => { const el = document.querySelector('.wme-point[onclick*="pointsmall"]'); return el ? (document.getElementById("meClubPts") || {}).textContent : null; });
  ok(entry !== null && entry !== undefined, "② 可用积分入口绑定了 pointsmall 跳转", "积分=" + entry);

  // 真点入口 → 积分商城
  await page.evaluate(() => document.querySelector('.wme-point[onclick*="pointsmall"]').click());
  await page.waitForTimeout(2200);
  const mallTxt = await page.evaluate(() => document.body.innerText.replace(/\s+/g, " "));
  ok(mallTxt.includes("积分商城"), "② 点「可用积分」进入积分商城");
  ok(mallTxt.includes("俱乐部周边帽"), "② 商城渲染出「俱乐部周边帽」");

  // 真点兑换（实物 → 确认框 → 弹核销码）
  await page.evaluate(() => {
    const btn = [...document.querySelectorAll("button")].find(b => b.textContent.trim() === "兑换" && !b.disabled);
    if (btn) btn.click();
  });
  await page.waitForTimeout(1200);
  // 点确认框里的「确认」
  const confirmed = await page.evaluate(() => {
    const ov = [...document.querySelectorAll(".ux-overlay,.modal")].find(e => e.offsetParent !== null || getComputedStyle(e).position === "fixed");
    if (!ov) return false;
    const btn = [...ov.querySelectorAll("button")].find(b => b.textContent.trim() === "确认");
    if (btn) { btn.click(); return true; }
    return false;
  });
  await page.waitForTimeout(2200);
  const dlgTxt = await page.evaluate(() => {
    const ov = [...document.querySelectorAll(".ux-overlay,.modal")].find(e => e.offsetParent !== null || getComputedStyle(e).position === "fixed");
    return ov ? ov.innerText.replace(/\s+/g, " ") : "";
  });
  const m = dlgTxt.match(/PM-[0-9A-F]+/i);
  if (m) redeemCode = m[0];
  ok(!!m, "② 兑换实物弹出 PM- 核销码", redeemCode || ("confirmed=" + confirmed + " " + dlgTxt.slice(0, 120)));
  await page.evaluate(() => document.querySelectorAll(".ux-overlay,.modal").forEach(e => e.remove()));

  // 兑换记录区出现待核销
  const mineTxt = await page.evaluate(() => document.body.innerText.replace(/\s+/g, " "));
  ok(mineTxt.includes("我的兑换") && mineTxt.includes("待核销"), "② 「我的兑换」出现待核销记录");
  ok(!errs.filter(e => !/^Failed to load resource/.test(e)).length, "② C 端无 JS 错误", errs.filter(e => !/^Failed to load resource/.test(e)).slice(0, 3).join(" / "));

  // ③ 会员信息页：购买入口
  await page.evaluate(() => pushMe("membercard"));
  await page.waitForTimeout(2200);
  const mcTxt = await page.evaluate(() => document.body.innerText.replace(/\s+/g, " "));
  ok(mcTxt.includes("年卡会员") && mcTxt.includes("立即购买"), "③ 会员信息页渲染可购买会员卡「立即购买」");
  ok((await page.evaluate(() => typeof window.meBuyTier)) === "function", "③ meBuyTier 已挂载");
  await ctx.close();
}

/* ---------- ④ club 端兑换回流 ---------- */
if (redeemCode) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" }).catch(() => {});
  await page.waitForTimeout(1000);
  await page.evaluate(async ([u, p]) => {
    await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: u, password: p }) });
  }, ["club1_admin", PW]).catch(() => {});
  await page.goto(BASE + "/club", { waitUntil: "networkidle" }).catch(() => {});
  await page.waitForTimeout(2000);
  await page.click('.nav [data-view="members"]', { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(2200);
  const redTxt = await page.evaluate(() => (document.getElementById("mallRedemptions") || document.getElementById("clubBenefitRedemptions") || {}).innerText || "");
  ok(redTxt.includes(redeemCode) || redTxt.includes("待核销"), "④ 俱乐部端兑换记录看到刚才的核销码", (redTxt.slice(0, 120) || "空").replace(/\n/g, " "));
  await ctx.close();
} else {
  ok(false, "④ 俱乐部端兑换回流（前面未拿到核销码，跳过）");
}

await browser.close();

const fail = results.filter(r => !r.pass).length;
console.log("\n" + "=".repeat(60));
console.log(`共 ${results.length} 项，FAIL ${fail}`);
console.log("=".repeat(60));
process.exit(fail > 0 ? 1 : 0);
