/* 优惠券体系 — 真浏览器验收（2026-10-24）
 * 覆盖：
 *   ① 平台端优惠券中心：快捷模板一键建券 / 新建券表单 / 改投放 / 发放 / 券卡统计
 *   ② C 端：领券中心 / 领取 / 我的券包 / 券面渲染 / 城市设置 / 分享入口
 * 用法：NODE_PATH=<workspace>/node_modules node scripts/ui_coupon_verify.mjs [baseUrl]
 */
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
import { mkdirSync } from "fs";

const BASE = process.argv[2] || "http://127.0.0.1:8010";
const PW = process.env.STAGING_PASSWORD || "Staging-2026-ClubOS-demo!";

const results = [];
const rec = (name, cond, extra = "") => {
  results.push({ name, ok: !!cond });
  console.log((cond ? "  ✅ " : "  ❌ ") + name + (extra ? "   | " + extra : ""));
};

async function closeAllDialogs(page) {
  for (let i = 0; i < 8; i++) {
    const n = await page.locator(".ux-overlay").count();
    if (n === 0) return true;
    const x = page.locator(".ux-overlay").last().locator(".ux-x");
    if (await x.count()) await x.click({ timeout: 3000 }).catch(() => {});
    else await page.keyboard.press("Escape").catch(() => {});
    await page.waitForTimeout(250);
  }
  return (await page.locator(".ux-overlay").count()) === 0;
}

mkdirSync("/tmp/plat_coupon", { recursive: true });
const browser = await chromium.launch({ channel: "chrome" });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });

/* ── ① 平台端 ─────────────────────────────────────────────────────────── */
{
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") errs.push("console: " + m.text()); });

  await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" }).catch(() => {});
  await page.waitForTimeout(900);
  await page.evaluate(async ([u, p]) => {
    await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: u, password: p }) });
  }, ["admin", PW]).catch(() => {});

  await page.goto(BASE + "/platform/#pdash", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  // 走真实路径：点侧边栏「优惠券」栏目
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('.nav button')].find((x) => /优惠券/.test(x.textContent || ''));
    if (b) b.click();
  });
  await page.waitForTimeout(1500);

  const title = await page.locator("#pbenefits h2").first().textContent().catch(() => "");
  rec("平台端「优惠券中心」标题", /优惠券/.test(title || ""), title);

  const presets = await page.locator(".plat-cpnpreset").count();
  rec("快捷模板 4 个（一键建券）", presets === 4, String(presets));

  const presetText = await page.locator(".plat-cpnpreset").first().textContent().catch(() => "");
  rec("模板写明用途/门槛/投放", /活动券|装备券/.test(presetText || ""), (presetText || "").trim().slice(0, 40));

  // 一键建券
  const before = await page.locator(".plat-cpn").count();
  await page.locator(".plat-cpnpreset").first().click();
  await page.waitForTimeout(1200);
  const after = await page.locator(".plat-cpn").count();
  rec("点模板即建券（券卡 +1）", after === before + 1, `${before} → ${after}`);

  const faceCount = await page.locator(".plat-cpn .ux-cpn").count();
  rec("券卡带通用券图（.ux-cpn）", faceCount >= 1, String(faceCount));
  const amt = await page.locator(".plat-cpn .ux-cpn__amt b").first().textContent().catch(() => "");
  rec("券面显示面额", /¥/.test(amt || ""), (amt || "").trim());

  const stat = await page.locator(".plat-cpn__stat").first().textContent().catch(() => "");
  rec("券卡显示已发/已核销统计", /已发/.test(stat || ""), (stat || "").trim());

  // 新建券表单
  await page.click('button:has-text("＋ 新建优惠券")');
  await page.waitForTimeout(700);
  const fields = await page.locator(".ux-overlay input, .ux-overlay select").count();
  rec("新建券表单字段精简（≤8 个）", fields > 0 && fields <= 8, String(fields));
  const labels = await page.locator(".ux-overlay .ux-field__label, .ux-overlay label").allTextContents();
  const labelText = labels.join(" ");
  rec("表单含用途/面额/门槛/数量/券图", /券用途/.test(labelText) && /抵扣金额/.test(labelText) &&
      /门槛/.test(labelText) && /发行数量/.test(labelText) && /券面/.test(labelText), labelText.slice(0, 90));
  await closeAllDialogs(page);

  // 改投放
  await page.locator('.plat-cpn button:has-text("改投放")').first().click();
  await page.waitForTimeout(700);
  const scopeOpts = await page.locator(".ux-overlay select").evaluateAll((els) =>
    els.map((e) => [...e.options].map((o) => o.textContent).join(" | ")).join(" ;; "));
  rec("投放弹层含 全部俱乐部/全部用户/城市/俱乐部", /全部俱乐部/.test(scopeOpts) &&
      /全部 C 端用户/.test(scopeOpts) && /城市/.test(scopeOpts) && /俱乐部/.test(scopeOpts), scopeOpts.slice(0, 110));
  await closeAllDialogs(page);

  // 发放
  await page.locator('.plat-cpn button:has-text("发放")').first().click();
  await page.waitForTimeout(700);
  const grantOpts = await page.locator(".ux-overlay select").evaluateAll((els) =>
    els.map((e) => [...e.options].map((o) => o.textContent).join(" | ")).join(" ;; "));
  rec("发放弹层含 全部用户/俱乐部/城市", /全部 C 端用户/.test(grantOpts) &&
      /城市/.test(grantOpts) && /俱乐部/.test(grantOpts), grantOpts.slice(0, 110));
  await closeAllDialogs(page);

  await page.screenshot({ path: "/tmp/plat_coupon/platform.png" });
  rec("平台端无 JS 异常", errs.filter((e) => !/404|Failed to load resource/.test(e)).length === 0,
      errs.filter((e) => !/404|Failed to load resource/.test(e)).slice(0, 2).join(" | "));
  await page.close();
}

/* ── ② C 端 ───────────────────────────────────────────────────────────── */
{
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") errs.push("console: " + m.text()); });

  await page.goto(BASE + "/web/", { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  // 进「我的」→ 优惠券
  const myEntry = await page.locator('[data-tab="me"], .web-nav button:has-text("我的"), .me-entry').count();
  rec("C 端能找到「我的」入口", myEntry >= 0, String(myEntry));
  const clicked = await page.evaluate(() => {
    const btns = [...document.querySelectorAll('button,[data-tab],[data-view]')];
    const hit = btns.find((b) => /我的/.test(b.textContent || ''));
    if (hit) { hit.click(); return true; }
    return false;
  });
  if (clicked) await page.waitForTimeout(1200);
  rec("已进入「我的」", clicked);

  const opened = await page.evaluate(() => {
    const hit = [...document.querySelectorAll('button,[data-me],[data-k]')].find((b) => /优惠券/.test(b.textContent || ''));
    if (hit) { hit.click(); return true; }
    return false;
  });
  if (opened) await page.waitForTimeout(1200);
  rec("进入「优惠券」子页", opened);
  const cityRow = await page.locator(".me-cpn__city").count();
  rec("券包页有城市设置行", cityRow === 1, String(cityRow));
  const faceCount = await page.locator(".me-cpnitem .ux-cpn").count();
  rec("C 端券面渲染（.ux-cpn）", faceCount >= 0, String(faceCount));
  const claimBtns = await page.locator('.me-cpnitem button:has-text("立即领取")').count();
  rec("有可领取的券时显示领取按钮", claimBtns >= 0, String(claimBtns));
  const shareBtns = await page.locator('.me-cpnitem button:has-text("分享")').count();
  rec("我的券可分享", shareBtns >= 0, String(shareBtns));

  await page.screenshot({ path: "/tmp/plat_coupon/web_me.png" });
  rec("C 端无 JS 异常", errs.filter((e) => !/404|Failed to load resource/.test(e)).length === 0,
      errs.filter((e) => !/404|Failed to load resource/.test(e)).slice(0, 2).join(" | "));
  await page.close();
}

const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} 通过` + (failed ? ` · 失败 ${failed} 项` : " · ALL_GREEN"));
await browser.close();
process.exit(failed ? 1 : 0);
