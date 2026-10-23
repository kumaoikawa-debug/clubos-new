/* 商品域多品牌化 — 真浏览器验收（2026-10-24）
 * 覆盖三处本次改动：
 *  ① 平台端「装备商品」视图：筛选条 / 品牌品类列 / 新商品表单六维 / 规格矩阵弹层 / 字典管理弹窗 / CSV 导入导出
 *  ② C 端商城：品牌+人群筛选 chip / 商品卡品牌前缀
 *  ③ C 端商品详情：底部弹层两级选择（先颜色后尺码），换色后尺码行只留该色、价格随规格走
 * 用法：NODE_PATH=<workspace>/node_modules node scripts/ui_catalog_verify.mjs [baseUrl]
 */
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = process.argv[2] || "http://127.0.0.1:8010";
const PW = process.env.STAGING_PASSWORD || "Staging-2026-ClubOS-demo!";

const results = [];
const rec = (name, cond, extra = "") => {
  results.push({ name, ok: !!cond, extra });
  console.log((cond ? "  ✅ " : "  ❌ ") + name + (extra ? "   | " + extra : ""));
};

async function login(page, path, user) {
  // 与 ui_chain_walk 同一套：登录页本身没有表单（TEST_OPEN 下登录门槛已关），
  // 用 fetch 种同源 cookie，再进目标页。
  await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" }).catch(() => {});
  const st = await page.evaluate(async ([u, p]) => {
    const r = await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: u, password: p }),
    });
    return r.status;
  }, [user, PW]);
  await page.goto(BASE + path, { waitUntil: "domcontentloaded" }).catch(() => {});
  return st;
}

/* 关弹层：uxDialog 的 Esc 只关最上一层（_uxDlgStack 设计如此），
 * 规格弹层里再开「新增规格」表单时，关掉表单后外层规格弹层还在，
 * 遮罩会继续拦截页面点击 —— 必须逐层点 .ux-x 直到归零。 */
async function closeAllDialogs(page) {
  for (let i = 0; i < 8; i++) {
    const n = await page.locator(".ux-overlay").count();
    if (n === 0) return true;
    const x = page.locator(".ux-overlay").last().locator(".ux-x");
    if (await x.count()) {
      await x.click({ timeout: 3000 }).catch(() => {});
    } else {
      await page.keyboard.press("Escape").catch(() => {});
    }
    await page.waitForTimeout(250);
  }
  return (await page.locator(".ux-overlay").count()) === 0;
}

const browser = await chromium.launch({ channel: "chrome" });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });

/* ── ① 平台端 ─────────────────────────────────────────────────────────── */
{
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e.message || e)));
  await login(page, "/platform", "platform_admin");
  await page.click('.nav [data-view="products"]');
  await page.waitForTimeout(1200);

  rec("商品视图：筛选条三个下拉在位",
    await page.locator("#prodFilterBrand").count() && await page.locator("#prodFilterCat").count() && await page.locator("#prodFilterGender").count());
  const brandOpts = await page.locator("#prodFilterBrand option").allTextContents();
  rec("品牌下拉有「全部品牌」+ 真品牌", brandOpts.length >= 2 && brandOpts[0] === "全部品牌", brandOpts.join("/"));
  const catOpts = await page.locator("#prodFilterCat option").allTextContents();
  rec("品类下拉带层级前缀（父 / 子）", catOpts.some((t) => t.includes("/")), catOpts.slice(0, 4).join("|"));

  // 表格出现品牌/品名列（表头第 2 列）
  const ths = await page.locator("#products .table thead th").allTextContents();
  rec("表头有「品牌 / 品类」列", ths.some((t) => t.includes("品牌")), ths.join("|"));

  // 按品牌筛选：选第一个真品牌，行数应变少且每行都带品牌名
  const firstBrand = await page.locator("#prodFilterBrand option").nth(1).getAttribute("value");
  await page.selectOption("#prodFilterBrand", firstBrand);
  await page.waitForTimeout(900);
  const hit = await page.locator("#prodFilterHit").textContent();
  const rowCount = await page.locator("#productRows tr").count();
  rec("按品牌筛选后有命中计数", /\d+ 件商品/.test(hit || ""), `${hit} / 行数 ${rowCount}`);
  await page.click('button:has-text("清除")');
  await page.waitForTimeout(900);
  rec("清除筛选后回到全量", (await page.locator("#prodFilterHit").textContent()) !== hit);

  // 关键词搜索
  await page.fill("#prodFilterQ", "UI 验收");
  await page.press("#prodFilterQ", "Enter");
  await page.waitForTimeout(900);
  const qRows = await page.locator("#productRows tr").count();
  rec("关键词搜索命中验收商品", qRows >= 1, `行数 ${qRows}`);
  await page.click('button:has-text("清除")');
  await page.waitForTimeout(600);

  // 新商品表单：六维字段
  await page.click('button:has-text("＋ 新商品")');
  await page.waitForTimeout(600);
  const formLabels = await page.locator(".ux-overlay .ux-field label").allTextContents();
  for (const f of ["品牌", "品类", "人群", "季节", "系列", "面料 / 材质"]) {
    rec(`上架表单有「${f}」字段`, formLabels.some((t) => t.includes(f)), formLabels.join("|").slice(0, 160));
  }
  await page.fill('.ux-overlay input[name="name"]', "矩阵验收商品");
  await page.fill('.ux-overlay input[name="price"]', "899");
  await page.click('.ux-overlay button:has-text("下一步：配规格")');
  await page.waitForTimeout(700);

  // 矩阵弹层：加两个颜色、填尺码
  const mxVisible = await page.locator("#mxColors").count();
  rec("矩阵弹层打开", mxVisible === 1);
  await page.click("#mxAddColor");
  await page.click("#mxAddColor");
  await page.locator(".plat-mx__chip").nth(0).locator(".plat-mx__chipname").fill("雪白");
  await page.locator(".plat-mx__chip").nth(1).locator(".plat-mx__chipname").fill("松绿");
  await page.fill("#mxSizes", "S, M, L");
  await page.waitForTimeout(300);
  const preview = await page.locator("#mxPreview").textContent();
  rec("矩阵预览算出 2 色 × 3 码 = 6 个 SKU", /2 色 × 3 码 = 6/.test(preview || ""), preview);
  await page.click('.ux-dialog-foot button:has-text("生成矩阵")');
  await page.waitForTimeout(1500);
  const toast = await page.locator(".toast, .ux-toast").allTextContents().catch(() => []);
  rec("上架成功并提示规格数", toast.some((t) => /6 个规格/.test(t)), toast.join("|"));

  // 规格管理：矩阵按钮 + 颜色/尺码字段
  const newRow = page.locator("#productRows tr", { hasText: "矩阵验收商品" }).first();
  await newRow.locator('button:has-text("规格")').click();
  await page.waitForTimeout(900);
  const vRows = await page.locator(".pv-list .list-row").count();
  rec("规格弹层列出 6 个规格", vRows === 6, `行数 ${vRows}`);
  const vNames = await page.locator(".pv-list .list-row__title").allTextContents();
  rec("规格名是「颜色 / 尺码」", vNames.some((t) => t.includes("雪白 / S")), vNames.slice(0, 3).join("|"));
  rec("规格弹层有批量矩阵按钮", await page.locator('[data-act="matrix"]').count() === 1);
  await page.locator('[data-act="add"]').click();
  await page.waitForTimeout(500);
  const vfLabels = await page.locator(".ux-overlay .ux-field label").allTextContents();
  rec("新增规格表单有颜色/色值/尺码/条码", ["颜色", "色值", "尺码", "条码"].every((f) => vfLabels.some((t) => t.includes(f))), vfLabels.join("|"));
  await closeAllDialogs(page);

  // 品牌管理弹窗
  await page.click('button:has-text("品牌")');
  await page.waitForTimeout(800);
  const bRows = await page.locator(".pd-list .list-row").count();
  rec("品牌管理弹窗列出品牌", bRows >= 1, `行数 ${bRows}`);
  await closeAllDialogs(page);
  await page.click('button:has-text("品类")');
  await page.waitForTimeout(800);
  const cRows = await page.locator(".pd-list .list-row").count();
  rec("品类管理弹窗列出品类（含层级副标）", cRows >= 1, `行数 ${cRows}`);
  await closeAllDialogs(page);

  // CSV 导入弹层
  await page.click('button:has-text("导入 CSV")');
  await page.waitForTimeout(700);
  rec("CSV 导入弹层有粘贴框与表头提示", (await page.locator("#csvText").count()) === 1);
  await closeAllDialogs(page);
  rec("平台端无 JS 异常", errs.length === 0, errs.join("|"));
  await page.close();
}

/* ── ②③ C 端商城 + 两级选择器 ────────────────────────────────────────── */
{
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e.message || e)));
  await login(page, "/web", "member1");
  await page.click('.web-nav [data-wv="wmall"]');
  await page.waitForTimeout(2500);

  // 验收商品带品牌，筛选行应出现（>=2 品牌或有人群差异）
  const filterCount = await page.locator("#mallFilters button").count();
  rec("商城筛选行出现（品牌/人群 chip）", filterCount >= 2, `${filterCount} 个 chip`);
  if (filterCount >= 2) {
    const label = await page.locator("#mallFilters .w-shop__flabel").first().textContent();
    rec("筛选项带分组标签", /品牌|人群/.test(label || ""), label);
  }

  // 商品卡带品牌前缀
  const cardMeta = await page.locator("#publicProducts .w-product__meta").allTextContents();
  rec("商品卡 meta 带品牌名", cardMeta.some((t) => /凯乐石/.test(t)), cardMeta.slice(0, 3).join("|"));

  // 进验收商品详情
  await page.locator('#publicProducts .web-card', { hasText: "UI 验收软壳" }).first().click();
  await page.waitForTimeout(2000);
  rec("详情页打开（标题在）", (await page.locator(".w-pd__title").textContent() || "").includes("UI 验收软壳"));

  // 规格表：有颜色列
  await page.locator("#pdSecSize .w-pd__sechd").click();
  await page.waitForTimeout(400);
  const sizeThs = await page.locator(".w-pd__sizetab thead th").allTextContents();
  rec("规格表有「颜色」列", sizeThs[0] === "颜色", sizeThs.join("|"));
  const firstRow = await page.locator(".w-pd__sizetab tbody tr").first().locator("td").allTextContents();
  rec("规格表首行是 颜色+尺码+价格", firstRow.length === 3 && /炭灰/.test(firstRow[0]), firstRow.join("|"));

  // 底部弹层：两级选择
  await page.locator(".w-pd__fbuy").click();
  await page.waitForTimeout(700);
  rec("弹层打开", await page.locator("#pdSheet.on").count() === 1);
  const colorCount = await page.locator("#pdSheetColors .w-pd__color").count();
  rec("弹层有颜色行（2 色）", colorCount === 2, `${colorCount} 个色块`);
  const swatchBg = await page.locator("#pdSheetColors .w-pd__color .w-pd__swatch").first().evaluate((el) => getComputedStyle(el).backgroundColor);
  rec("色块用真实色值（炭灰 #4A4A4A）", swatchBg === "rgb(74, 74, 74)", swatchBg);

  // 换色：可见尺码仍 3 个（另一色的藏起来），烈日橙 0 库存 → 三个全禁用
  await page.locator("#pdSheetColors .w-pd__color").nth(1).click();
  await page.waitForTimeout(400);
  const sizeBtns = await page.locator("#pdSheetSizes .w-pd__size:not([hidden])").count();
  rec("换色后可见尺码仍是 3 个", sizeBtns === 3, `可见 ${sizeBtns}`);
  const lrAllOut = await page.locator("#pdSheetSizes .w-pd__size:not([hidden])")
    .evaluateAll((els) => els.every((e) => e.disabled));
  rec("烈日橙 0 库存 → 三个尺码全禁用（售罄态，期初库存只落第一格）", lrAllOut === true);
  const label2 = await page.locator("#pdSheetColorLabel").textContent();
  rec("颜色标签显示已选颜色", /烈日橙/.test(label2 || ""), label2);
  const onColor = await page.locator("#pdSheetColors .w-pd__color.on").count();
  rec("换色后选中态跟着走", onColor === 1);

  // 换回有货的炭灰 → 选尺码 → CTA 与数量上限
  await page.locator("#pdSheetColors .w-pd__color").nth(0).click();
  await page.waitForTimeout(400);
  await page.locator("#pdSheetSizes .w-pd__size:not([disabled]):not([hidden])").first().click();
  await page.waitForTimeout(300);
  const cta = await page.locator("#pdSheetCta").textContent();
  rec("选完规格 CTA 是「立即购买」", /立即购买/.test(cta || ""), cta);
  const maxQty = await page.locator("#pdSheetMax").textContent();
  rec("数量上限随规格库存（期初 30 落第一格）", Number(maxQty) >= 1, `上限 ${maxQty}`);
  await page.locator("#pdSheet .w-pd__sheetclose").click();
  await page.waitForTimeout(400);

  // 加购物车（确认两级选择真能落单）
  await page.locator(".w-pd__fadd").click();
  await page.waitForTimeout(500);
  await page.locator("#pdSheetCta").click();
  await page.waitForTimeout(900);
  const badge = await page.locator("#pdFootBadge").textContent().catch(() => "");
  rec("加入购物车后角标计数", Number(badge) >= 1, `角标 ${badge}`);

  rec("C 端无 JS 异常", errs.length === 0, errs.join("|"));
  await page.close();
}

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n验收项 ${results.length} · 通过 ${results.length - failed.length} · 失败 ${failed.length}`);
if (failed.length) { console.log("失败项：", failed.map((f) => f.name).join(" / ")); process.exit(1); }
console.log("ALL_GREEN");
