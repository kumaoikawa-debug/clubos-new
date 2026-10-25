/* 规格色值「纯手填」验收（2026-10-25）
 * 用户原话：「这里的颜色选择,只需要手动填写即可,不需要色块选择。手动填写更准」
 * 覆盖：
 *   ① 商品编辑器 · 规格矩阵：颜色 chip 里是文本框（不是 <input type=color>），
 *      失焦补全 #RRGGBB、色块跟着变、非法值保存时被拦下
 *   ② 「管理已有规格 → 批量生成规格」弹层：同样无取色器，打字不丢焦点
 *   ③ 原生取色器在平台端彻底不存在（全局扫 input[type=color]）
 * 用法：NODE_PATH=<workspace>/node_modules node scripts/ui_sku_color_verify.mjs [baseUrl]
 */
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = process.argv[2] || "http://127.0.0.1:8132";
const PW = process.env.STAGING_PASSWORD || "Staging-2026-ClubOS-demo!";

const results = [];
const SEL_ALL = process.platform === "darwin" ? "Meta+A" : "Control+A";
const rec = (name, cond, extra = "") => {
  results.push({ name, ok: !!cond, extra });
  console.log((cond ? "  ✅ " : "  ❌ ") + name + (extra ? "   | " + extra : ""));
};

async function closeAllDialogs(page) {
  for (let i = 0; i < 8; i++) {
    const n = await page.locator(".ux-overlay").count();
    if (n === 0) return true;
    const x = page.locator(".ux-overlay").last().locator(".ux-x");
    if (await x.count()) await x.click({ timeout: 2000 }).catch(() => {});
    else await page.keyboard.press("Escape").catch(() => {});
    await page.waitForTimeout(200);
  }
  return (await page.locator(".ux-overlay").count()) === 0;
}

const browser = await chromium.launch({ channel: "chrome" });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });

/* ── ① 商品编辑器 · 规格矩阵 ───────────────────────────────────────────── */
{
  const page = await ctx.newPage();
  const errs = [];
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errs.push(m.text()); });
  page.on("pageerror", (e) => errs.push(String(e && e.message)));

  await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" }).catch(() => {});
  await page.evaluate(async ([u, p]) => {
    await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: u, password: p }),
    });
  }, ["platform", PW]);
  await page.goto(BASE + "/platform", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);

  rec("平台端无原生取色器（加载态）", (await page.locator('input[type="color"]').count()) === 0);

  /* 打开「添加商品」单页编辑器 */
  await page.evaluate(() => { window.platformAddProduct(); });
  await page.waitForSelector(".plat-pe__scroll", { timeout: 15000 });
  await page.waitForTimeout(600);

  rec("编辑器内无 input[type=color]", (await page.locator('.plat-pe input[type="color"]').count()) === 0);

  /* 新商品颜色行为空，先点「＋ 添加颜色」 */
  await page.locator('.plat-pe [data-act="addcolor"]').first().click();
  await page.waitForTimeout(300);
  rec("颜色 chip 用文本框填色值", (await page.locator(".plat-pe__chiphex").count()) === 1,
      String(await page.locator(".plat-pe__chiphex").count()));

  const ph = await page.locator(".plat-pe__chiphex").first().getAttribute("placeholder");
  rec("色值框提示 #RRGGBB", ph === "#RRGGBB", ph || "");

  /* 添加第二个颜色行，手填色值 */
  await page.locator('.plat-pe [data-act="addcolor"]').first().click();
  await page.waitForTimeout(300);
  const chips = page.locator(".plat-pe__chip");
  const n = await chips.count();
  rec("添加颜色后 chip 数 +1", n >= 2, String(n));

  const last = chips.nth(n - 1);
  await last.locator(".plat-pe__chipname").fill("炭灰");
  await last.locator(".plat-pe__chiphex").fill("2b2b2b");
  await last.locator(".plat-pe__chiphex").blur();
  await page.waitForTimeout(200);

  const normed = await last.locator(".plat-pe__chiphex").inputValue();
  rec("失焦补全为 #2B2B2B", normed === "#2B2B2B", normed);

  const swBg = await last.locator(".plat-mx__swatch").evaluate((el) => getComputedStyle(el).backgroundColor);
  rec("色块预览跟随色值（rgb(43,43,43)）", swBg === "rgb(43, 43, 43)", swBg);

  const emptyCls = await page.locator(".plat-pe__chip").first().locator(".plat-mx__swatch").getAttribute("class");
  rec("未填色值的 chip 色块是空样", /--empty/.test(emptyCls || ""), emptyCls || "");

  /* 3 位简写 → 6 位 */
  const third = chips.nth(0);
  await third.locator(".plat-pe__chiphex").fill("#abc");
  await third.locator(".plat-pe__chiphex").blur();
  await page.waitForTimeout(150);
  const short = await third.locator(".plat-pe__chiphex").inputValue();
  rec("#abc 展开为 #AABBCC", short === "#AABBCC", short);

  /* 非法值保存被拦（先补必填项，否则会先撞「请填写商品名称」） */
  await page.locator("#peName").fill("色值手填测试商品");
  await page.locator("#pePrice").fill("99");
  await third.locator(".plat-pe__chiphex").fill("zzz");
  await page.locator('.plat-pe__foot [data-act="save"]').click();
  await page.waitForTimeout(700);
  const toastTxt = await page.locator(".ux-toast, .toast").first().textContent().catch(() => "");
  const stillOpen = (await page.locator(".plat-pe__scroll").count()) === 1;
  rec("非法色值保存被拦（提示 + 不关页）", /RRGGBB/.test(toastTxt || "") && stillOpen, (toastTxt || "").trim());

  const marked = await third.locator(".plat-pe__chiphex").inputValue();
  rec("非法值仍留在输入框（不被悄悄清空）", marked === "zzz", marked);

  await page.screenshot({ path: "/tmp/sku_color_editor.png" });
  await closeAllDialogs(page);
  rec("编辑器控制台无报错", errs.length === 0, errs.slice(0, 2).join(" / "));
  await page.close();
}

/* ── ② 批量生成规格弹层（prodMatrixDialog） ────────────────────────────── */
{
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e && e.message)));
  await page.goto(BASE + "/platform", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);

  await page.evaluate(() => {
    window.__mxP = window.prodMatrixDialog({ title: "批量生成规格", colors: [{ name: "炭灰", hex: "#2B2B2B" }], sizes: ["M"] });
  });
  await page.waitForTimeout(800);

  rec("矩阵弹层无 input[type=color]", (await page.locator('.ux-overlay input[type="color"]').count()) === 0);
  rec("矩阵弹层色值框存在", (await page.locator(".ux-overlay .plat-mx__chiphex").count()) === 1);

  /* 打字不丢焦点：连打 4 个字符后输入框仍是焦点、内容完整 */
  const hexEl = page.locator(".ux-overlay .plat-mx__chiphex").first();
  await hexEl.click();
  await page.keyboard.press(SEL_ALL);
  await page.keyboard.type("1a2b3c", { delay: 60 });
  await page.waitForTimeout(200);
  const typed = await hexEl.inputValue();
  const focused = await page.evaluate(() => document.activeElement && document.activeElement.className);
  rec("连打不被重建打断（值完整）", typed === "1a2b3c", typed);
  rec("输入时焦点留在原输入框", /plat-mx__chiphex/.test(focused || ""), focused || "");

  /* 颜色名连打，同样不该丢焦点 */
  const nameEl = page.locator(".ux-overlay .plat-mx__chipname").first();
  await nameEl.click();
  await page.keyboard.press(SEL_ALL);
  await page.keyboard.type("雾灰蓝", { delay: 60 });
  await page.waitForTimeout(200);
  const typedName = await nameEl.inputValue();
  rec("颜色名连打不丢字", typedName === "雾灰蓝", typedName);

  await hexEl.blur();
  await page.waitForTimeout(150);
  rec("失焦补全 #1A2B3C", (await hexEl.inputValue()) === "#1A2B3C", await hexEl.inputValue());
  const sw = await page.locator(".ux-overlay .plat-mx__swatch").first().evaluate((el) => getComputedStyle(el).backgroundColor);
  rec("弹层色块跟随（rgb(26,43,60)）", sw === "rgb(26, 43, 60)", sw);

  /* 非法值点了「生成矩阵」要被拦 */
  await hexEl.fill("oops");
  await page.locator('.ux-overlay [data-ok]').click();
  await page.waitForTimeout(500);
  const t2 = await page.locator(".ux-toast, .toast").first().textContent().catch(() => "");
  const stillThere = (await page.locator(".ux-overlay .plat-mx__chiphex").count()) === 1;
  rec("非法色值「生成矩阵」被拦", /RRGGBB/.test(t2 || "") && stillThere, (t2 || "").trim());

  await page.screenshot({ path: "/tmp/sku_color_matrix.png" });
  await closeAllDialogs(page);
  rec("矩阵弹层无 JS 报错", errs.length === 0, errs.slice(0, 2).join(" / "));
  await page.close();
}

/* ── ③ 已有商品的规格编辑表单（showForm 色值） ─────────────────────────── */
{
  const page = await ctx.newPage();
  await page.goto(BASE + "/platform", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);
  const probe = await page.evaluate(async () => {
    const list = await (await fetch("/api/platform/products")).json();
    const p = (list || [])[0];
    if (!p) return null;
    const vs = await (await fetch("/api/platform/products/" + p.id + "/variants")).json();
    return { id: p.id, variants: (vs || []).length };
  });
  if (!probe) {
    rec("有商品可用于规格编辑用例", false, "列表为空，跳过");
  } else {
    await page.evaluate((pid) => { window.manageVariants(pid); }, probe.id);
    await page.waitForTimeout(1200);
    const hasAdd = await page.locator('.ux-overlay [data-act="add"]').count();
    rec("规格管理弹层可打开", hasAdd === 1, "variants=" + probe.variants);
    if (hasAdd) {
      await page.locator('.ux-overlay [data-act="add"]').first().click();
      await page.waitForTimeout(700);
      rec("新增规格表单无取色器", (await page.locator('.ux-overlay input[type="color"]').count()) === 0);
      const hexInputs = await page.locator('.ux-overlay input[name="colorHex"]').count();
      const hexType = hexInputs ? await page.locator('.ux-overlay input[name="colorHex"]').first().getAttribute("type") : "";
      rec("色值字段是文本框", hexInputs === 1 && hexType === "text", "type=" + hexType);

      /* 填非法值 → 提交被拦，表单不关 */
      await page.locator('.ux-overlay input[name="colorHex"]').first().fill("nope");
      await page.locator('.ux-overlay input[name="stock"]').first().fill("1");
      await page.locator('.ux-overlay [data-submit]').last().click();
      await page.waitForTimeout(600);
      const t3 = await page.locator(".ux-toast, .toast").first().textContent().catch(() => "");
      const formAlive = (await page.locator('.ux-overlay input[name="colorHex"]').count()) === 1;
      rec("规格表单非法色值被拦", /RRGGBB/.test(t3 || "") && formAlive, (t3 || "").trim());
    }
    await closeAllDialogs(page);
  }
  await page.close();
}

await browser.close();

const bad = results.filter((r) => !r.ok);
console.log("\n" + (bad.length ? "FAILED " + bad.length + "/" + results.length : "ALL PASS (" + results.length + ")"));
process.exit(bad.length ? 1 : 0);
