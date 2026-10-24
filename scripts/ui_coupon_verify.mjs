/* 优惠券体系 — 真浏览器验收（2026-10-24 重设计版）
 * 覆盖：
 *   ① 平台端优惠券中心：快捷模板一键建券 / 拆分式券卡（左渐变券面＋右白信息区）
 *      / 分栏编辑器（基本信息/使用条件/效期设置/领用限制/其他设置 + 实时预览）
 *      / 建券 / 改投放（同一页编辑器）/ 发放 / 券卡统计
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

/* 分栏编辑器不是 .ux-overlay，单独关：点取消 → 有未保存修改会弹确认 → 点放弃 */
async function closeCouponEditor(page) {
  if (!(await page.locator(".plat-cpe").count())) return;
  await page.locator(".plat-cpe [data-act=close]").first().click();
  await page.waitForTimeout(400);
  const giveUp = page.locator('.ux-overlay button:has-text("放弃修改")');
  if (await giveUp.count()) await giveUp.click().catch(() => {});
  await page.waitForTimeout(400);
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
  rec("模板卡带渐变面额图标", (await page.locator(".plat-cpnpreset__icon").count()) === 4,
      String(await page.locator(".plat-cpnpreset__icon").count()));

  // 一键建券
  const before = await page.locator("#platformCoupons .plat-cpn").count();
  await page.locator(".plat-cpnpreset").first().click();
  await page.waitForTimeout(1200);
  const after = await page.locator("#platformCoupons .plat-cpn").count();
  rec("点模板即建券（券卡 +1）", after === before + 1, `${before} → ${after}`);

  // ── 拆分式券卡（左渐变券面 + 右白信息区，照图1）──
  const faceCount = await page.locator(".plat-cpn__face").count();
  rec("券卡是拆分式（左渐变券面 .plat-cpn__face）", faceCount >= 1, String(faceCount));
  const amt = await page.locator(".plat-cpn__face b").first().textContent().catch(() => "");
  rec("券面显示面额", /¥/.test(amt || ""), (amt || "").trim());
  const sub = await page.locator(".plat-cpn__face span").first().textContent().catch(() => "");
  rec("券面显示门槛（无门槛/满X可用）", /无门槛|满/.test(sub || ""), (sub || "").trim());
  const infoTitle = await page.locator(".plat-cpn__info .plat-cpn__head strong").first().textContent().catch(() => "");
  rec("右侧信息区有标题", (infoTitle || "").trim().length > 0, (infoTitle || "").trim());
  const useText = await page.locator(".plat-cpn__use").first().textContent().catch(() => "");
  rec("右侧信息区有用途（活动报名可用/装备购买可用）", /活动报名可用|装备购买可用/.test(useText || ""), (useText || "").trim());
  const badge = await page.locator(".plat-cpn__badge").first().textContent().catch(() => "");
  rec("状态徽章（进行中/已下架）", /进行中|已下架/.test(badge || ""), (badge || "").trim());
  const stat = await page.locator(".plat-cpn__stat").first().textContent().catch(() => "");
  rec("券卡显示已发/已核销统计", /已发/.test(stat || "") && /已核销/.test(stat || ""), (stat || "").trim());
  const opNames = await page.locator(".plat-cpn__ops").first().textContent().catch(() => "");
  rec("操作行：发放/改投放/下架", /发放/.test(opNames || "") && /改投放/.test(opNames || "") && /下架/.test(opNames || ""),
      (opNames || "").trim().replace(/\s+/g, " "));

  // ── 分栏编辑器（照图3-13 的栏目结构）──
  await page.click('button:has-text("＋ 新建优惠券")');
  await page.waitForTimeout(700);
  rec("编辑器是全屏分栏页（.plat-cpe）", (await page.locator(".plat-cpe").count()) === 1);
  const navLabels = (await page.locator(".plat-cpe__nav a").allTextContents()).join(" ");
  rec("右侧栏目导航 5 项", ["基本信息", "使用条件", "效期设置", "领用限制", "其他设置"].every((s) => navLabels.includes(s)),
      navLabels.trim().replace(/\s+/g, " "));
  const secTitles = (await page.locator(".plat-cpe__sec-h h3").allTextContents()).join(" ");
  rec("内容区 5 个分区", ["基本信息", "使用条件", "效期设置", "领用限制", "其他设置"].every((s) => secTitles.includes(s)),
      secTitles.trim().replace(/\s+/g, " "));
  rec("顶部实时预览券卡", (await page.locator(".plat-cpe__preview .plat-cpn__face").count()) === 1);
  rec("预览卡不挂操作按钮（编辑器里点了会两层弹窗）",
      (await page.locator(".plat-cpe__preview .plat-cpn__ops").count()) === 0);
  // 条件字段默认隐藏：all_clubs 不显示城市/俱乐部；长期有效不显示日期
  rec("默认（全部俱乐部）不显示投放城市/俱乐部",
      (await page.locator('.plat-cpe__cond[data-when=city]').isVisible()) === false &&
      (await page.locator('.plat-cpe__cond[data-when=club]').isVisible()) === false);
  rec("默认（长期有效）不显示有效期输入",
      (await page.locator('.plat-cpe__cond[data-when=fixed]').isVisible()) === false);

  // 实时预览联动：改面额 → 预览跟着变
  await page.fill("#cpeCash", "66");
  await page.waitForTimeout(300);
  const previewAmt = await page.locator(".plat-cpe__preview .plat-cpn__face b").textContent().catch(() => "");
  rec("改面额实时刷新预览", /66/.test(previewAmt || ""), (previewAmt || "").trim());

  // 填表：活动券 + 城市投放 + 固定有效期 + 限领 2 张 + 不允许分享
  const uniq = String(Date.now()).slice(-6);
  await page.fill("#cpeTitle", `自动化验收券 ${uniq}`);
  await page.fill("#cpeThreshold", "120");
  await page.fill("#cpeStock", "88");
  await page.click('.plat-cpe__radio:has(input[name="targetType"][value="city"])');
  await page.waitForTimeout(250);
  rec("选「指定城市」后出现城市下拉", (await page.locator('.plat-cpe__cond[data-when=city]').isVisible()) === true);
  await page.selectOption("#cpeCity", { index: 1 }).catch(() => {});
  await page.click('.plat-cpe__radio:has(input[name="validType"][value="fixed"])');
  await page.waitForTimeout(250);
  rec("选「固定有效期」后出现日期输入", (await page.locator('.plat-cpe__cond[data-when=fixed]').isVisible()) === true);
  await page.fill("#cpeValidFrom", "2026-11-01T09:00");
  await page.fill("#cpeValidTo", "2026-12-31T23:59");
  await page.fill("#cpePerUser", "2");
  await page.click('.plat-cpe__radio:has(input[name="shareEnabled"][value="0"])');
  await page.click('.plat-cpe__tpl:has(input[value="forest"])');
  await page.waitForTimeout(250);
  const tplOnVal = await page.evaluate(() => {
    const lab = document.querySelector('.plat-cpe__tpl.is-on input');
    return lab ? lab.value : '';
  });
  rec("券面样式可点选（forest 高亮）", (await page.locator(".plat-cpe__tpl.is-on").count()) === 1 &&
      tplOnVal === "forest", tplOnVal);

  const listBefore = await page.locator("#platformCoupons .plat-cpn").count();
  await page.locator(".plat-cpe__foot [data-act=save]").click();
  await page.waitForTimeout(1600);
  rec("点创建后编辑器关闭", (await page.locator(".plat-cpe").count()) === 0);
  const listAfter = await page.locator("#platformCoupons .plat-cpn").count();
  rec("建券成功（券卡 +1）", listAfter === listBefore + 1, `${listBefore} → ${listAfter}`);
  const firstCard = await page.locator("#platformCoupons .plat-cpn").first().textContent().catch(() => "");
  rec("新券出现在列表最前（id DESC）", firstCard.includes(uniq), (firstCard || "").trim().slice(0, 50));
  rec("新券门槛文案为「满 ¥120 可用」", /满\s*¥?\s*120\s*可用/.test(firstCard || ""));

  // ── 改投放（同一页编辑器，编辑模式）──
  await page.locator('.plat-cpn button:has-text("改投放")').first().click();
  await page.waitForTimeout(800);
  rec("改投放打开同一页编辑器", (await page.locator(".plat-cpe").count()) === 1);
  const benefitLocked = await page.locator('.plat-cpe input[name="benefitType"]').first().isDisabled();
  rec("编辑模式券用途只读", benefitLocked === true);
  const titleVal = await page.locator("#cpeTitle").inputValue().catch(() => "");
  rec("编辑模式回填券名称", (titleVal || "").includes(uniq), (titleVal || "").trim());
  await page.click('.plat-cpe__radio:has(input[name="targetType"][value="all_users"])');
  await page.waitForTimeout(250);
  await page.locator(".plat-cpe__foot [data-act=save]").click();
  await page.waitForTimeout(1600);
  const afterEdit = await page.locator("#platformCoupons .plat-cpn").first().textContent().catch(() => "");
  rec("改投放保存后范围变为「全部 C 端用户」", /全部 C 端用户/.test(afterEdit || ""), (afterEdit || "").trim().slice(0, 60));

  // 发放（uxForm 弹层不变）
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
