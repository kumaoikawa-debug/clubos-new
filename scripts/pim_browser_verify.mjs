/* 商品图片管理器 · 真浏览器验收（Playwright）
 *   NODE_PATH=<workspace>/node_modules node scripts/pim_browser_verify.mjs [baseUrl]
 * 覆盖用户原始诉求：头图 / 详情图集 / 本地批量上传 / 上传后裁剪（含手动裁剪弹层）。
 */
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const fs = require("fs");

const BASE = process.argv[2] || "http://127.0.0.1:8912";
const API = BASE;
const results = [];
function check(name, cond, extra="") { results.push({name, ok: !!cond, extra}); console.log((cond?"✓":"✗")+" "+name+(extra?"  — "+extra:"")); }

// 1) 通过 API 建一个没有头图的新商品（走空封面 → 上传的完整路径）
async function makeProduct() {
  const sku = "PIM-BRW-" + Math.floor(Math.random()*100000);
  const r = await fetch(API + "/api/platform/products", {
    method: "POST", headers: {"content-type": "application/json"},
    body: JSON.stringify({ name: "浏览器验收商品", sku, price: 299, stock: 15 })
  });
  const j = await r.json();
  return { id: j.id, sku };
}

(async () => {
  const prod = await makeProduct();
  console.log("created product", prod);

  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  const errors = [], bad = [];
  page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", e => errors.push("pageerror: " + e.message));
  page.on("requestfailed", r => bad.push("FAILED " + r.url()));
  page.on("response", r => { if (r.status() >= 400) bad.push(r.status() + " " + r.url()); });

  await page.goto(BASE + "/platform", { waitUntil: "networkidle" });

  // 2) 切到装备商品视图
  await page.click('.nav [data-view="products"]');
  await page.waitForSelector("#productRows tr", { timeout: 8000 });
  // 关掉可能的首屏弹窗（如无则忽略）
  // 3) 商品行应渲染头图缩略（img 或占位 span）
  const rowThumbCount = await page.locator("#productRows .plat-prod-cover").count();
  check("商品行渲染头图缩略容器", rowThumbCount > 0, rowThumbCount + " 行有缩略");

  // 4) 打开刚建商品的图片管理器
  const row = page.locator("#productRows tr", { hasText: prod.sku }).first();
  check("能找到新建商品行", await row.count() > 0);
  await row.locator("button", { hasText: "图片" }).click();
  await page.waitForSelector(".pim", { timeout: 8000 });
  check("图片管理器弹层打开", true);

  // 5) 空封面 → 上传头图（触发文件选择 + 手动裁剪弹层）
  await page.waitForSelector('.pim [data-act="coverbtn"]', { timeout: 5000 });
  const [chCover] = await Promise.all([
    page.waitForEvent("filechooser"),
    page.click('.pim [data-act="coverbtn"]')
  ]);
  await chCover.setFiles("/tmp/t_a.png");
  // 裁剪弹层出现 → 点确定（接受默认中心裁剪）。注意 data-ok 在弹层 footer，是 .pim-crop 的兄弟节点
  await page.waitForSelector(".pim-crop", { timeout: 5000 });
  check("头图上传后弹出裁剪框", true);
  await page.click("[data-ok]");
  await page.waitForSelector(".pim-cover img", { timeout: 8000 });
  check("头图上传成功并显示", true);

  // 6) 批量添加详情图（本地多选 → 自动裁 3:4 → 批量上传）
  const [chGal] = await Promise.all([
    page.waitForEvent("filechooser"),
    page.click('.pim [data-act="addgallery"]')
  ]);
  await chGal.setFiles(["/tmp/t_b.png", "/tmp/t_c.png"]);
  await page.waitForFunction(() => document.querySelectorAll(".pim-thumb").length >= 2, { timeout: 10000 });
  const thumbCount = await page.locator(".pim-thumb").count();
  check("详情图批量上传成功", thumbCount >= 2, thumbCount + " 张详情图");

  // 7) 验证第一张详情图被标为头图（无头图时首张顶上）
  const coverBadge = await page.locator(".pim-thumb__cover").count();
  // 注意：第5步已设头图（t_a 的裁剪），所以图集里可能没有“头图”标记——这里只验证图集数量与上传按钮态
  // 8) 把第二张详情图「设为主图」
  const secondThumb = page.locator(".pim-thumb").nth(1);
  await secondThumb.hover();
  await secondThumb.locator('[data-act="setcover"]').click();
  await page.waitForTimeout(1200);
  // 头图框应刷新，且被设为主图的那张带头图标记
  const coverImgSrc = await page.locator(".pim-cover img").getAttribute("src");
  const secondSrc = await secondThumb.locator("img").getAttribute("src");
  check("设为主图生效（头图框切到该图）", coverImgSrc === secondSrc, coverImgSrc + " == " + secondSrc);

  // 9) 截图留证
  await page.screenshot({ path: "/tmp/pim_manager.png", fullPage: false });

  // 10) 关闭弹层（完成按钮在 footer），回到列表，确认商品行头图缩略变成了真实图
  await page.click('[data-act="done"]');
  await page.waitForTimeout(800);
  const rowImg = row.locator(".plat-prod-cover");
  const isImg = await rowImg.evaluate(el => el.tagName === "IMG").catch(()=>false);
  check("商品行头图缩略刷新为真实图", isImg);

  // 汇总
  console.log("\n── 控制台错误 / 失败请求 ──");
  console.log("errors:", errors.length ? errors : "无");
  console.log("bad:", bad.length ? bad : "无");
  await browser.close();

  const failed = results.filter(r => !r.ok);
  console.log("\n==== " + (failed.length===0 && errors.length===0 ? "ALL PASS" : "HAS FAILURES") + " ====");
  process.exit(failed.length===0 && errors.length===0 ? 0 : 1);
})().catch(e => { console.error("SCRIPT ERROR:", e); process.exit(2); });
