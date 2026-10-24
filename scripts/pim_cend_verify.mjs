/* C 端封面图验收（Playwright）：商城卡片封面 + 详情页首图都用 coverUrl 头图。
 *   NODE_PATH=<workspace>/node_modules node scripts/pim_cend_verify.mjs [baseUrl]
 */
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = process.argv[2] || "http://127.0.0.1:8912";
const results = [];
function check(name, cond, extra="") { results.push({name, ok: !!cond}); console.log((cond?"✓":"✗")+" "+name+(extra?"  — "+extra:"")); }

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await (await browser.newContext({ viewport: { width: 420, height: 900 } })).newPage();
  const errors = [];
  page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", e => errors.push("pageerror: " + e.message));

  await page.goto(BASE + "/web", { waitUntil: "networkidle" });
  // 进商城视图
  await page.click('[data-wv="wmall"]');
  await page.waitForSelector("#publicProducts .web-card", { timeout: 10000 });

  // 找一张带头图的卡（封面 src 指向 platform 上传目录）
  const cardInfo = await page.evaluate(() => {
    const cards = [...document.querySelectorAll("#publicProducts .web-card")];
    for (const c of cards) {
      const img = c.querySelector(".w-product__media img");
      const name = c.querySelector(".w-product__name")?.textContent || "";
      if (img && /\/static\/uploads\/platform\//.test(img.getAttribute("src") || "")) {
        return { src: img.getAttribute("src"), name, id: (c.getAttribute("onclick") || "").match(/\d+/)?.[0] };
      }
    }
    return null;
  });
  check("商城卡片渲染头图封面", !!cardInfo, cardInfo ? cardInfo.name + " → " + cardInfo.src : "未找到带头图卡片");
  // 用 page.request 探图（不要 page.goto，那会把 /web 文档导航走，后面 openProduct 就没了）
  const imgStatus = cardInfo ? (await page.request.get(BASE + cardInfo.src)).status() : 0;
  check("封面图资源可访问(200)", imgStatus === 200, cardInfo?.src + " → HTTP " + imgStatus);

  // 点卡片进详情（走卡片自身 wired 的 onclick），验证详情页首图 = 头图
  if (cardInfo?.id) {
    const card = page.locator("#publicProducts .web-card", { has: page.locator('.w-product__media img[src*="/static/uploads/platform/"]') }).first();
    await card.click();
    await page.waitForSelector("#pdGal .w-pd__slide img", { timeout: 8000 });
    const firstSlide = await page.locator("#pdGal .w-pd__slide img").first().getAttribute("src");
    check("详情页首图 = 头图", firstSlide === cardInfo.src, firstSlide);
    const slideCount = await page.locator("#pdGal .w-pd__slide").count();
    check("详情页图集含多张(头图+详情图)", slideCount >= 2, slideCount + " 张");
    await page.screenshot({ path: "/tmp/pim_cend_detail.png" });
  }

  await browser.close();
  console.log("\nerrors:", errors.length ? errors : "无");
  const failed = results.filter(r => !r.ok);
  console.log("\n==== " + (failed.length===0 && errors.length===0 ? "ALL PASS" : "HAS FAILURES") + " ====");
  process.exit(failed.length===0 && errors.length===0 ? 0 : 1);
})().catch(e => { console.error("SCRIPT ERROR:", e); process.exit(2); });
