// -*- coding: utf-8 -*-
// 把一段 HTML（长图 / 活动详情宣传区块）栅格化成真实 PNG。
//
// 用法：
//   node html_to_image.cjs --html <输入.html> --out <输出.png> [--width 750] [--scale 2]
//
// 设计要点（对照前端那条脆弱的 SVG foreignObject 导出链路，2026-11-05 重写）：
//   · 用 Chromium 真渲染整页，输出 750px 宽、deviceScaleFactor=2 的 2 倍图
//     （公众号长图标准宽度；2 倍图在朋友圈 / 公众号里才不糊）。
//   · 输入 HTML 里的图片一律是 file:// 绝对路径（由 Python 端 _resolve_media_to_file 预处理好），
//     所以这里直接 page.goto('file://…') 让图片同源加载，避免 foreignObject 内嵌外链光栅化失败。
//   · 不用 NODE_OPTIONS 注入的 shim：调用方会用 `env -u NODE_OPTIONS node …` 启动，
//     否则 WorkBuddy 的 fs/network shim 会干扰 chromium 启动。
const { chromium } = require('/Users/jckuma/.workbuddy/binaries/node/workspace/node_modules/playwright');
const fs = require('fs');
const path = require('path');

function parseArgs(argv) {
  const o = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (typeof a === 'string' && a.startsWith('--')) {
      const k = a.slice(2);
      const v = argv[i + 1];
      if (v === undefined || (typeof v === 'string' && v.startsWith('--'))) { o[k] = true; }
      else { o[k] = v; i++; }
    }
  }
  return o;
}

(async () => {
  const args = parseArgs(process.argv.slice(2));
  const htmlFile = args.html;
  const out = args.out;
  const width = parseInt(args.width || '750', 10);
  const scale = parseFloat(args.scale || '2');
  if (!htmlFile || !out) {
    console.error('usage: --html <file> --out <png> [--width 750] [--scale 2]');
    process.exit(2);
  }
  if (!fs.existsSync(htmlFile)) {
    console.error('FAIL input html not found: ' + htmlFile);
    process.exit(3);
  }
  let browser;
  try {
    browser = await chromium.launch({
      args: ['--no-sandbox', '--disable-dev-shm-usage', '--allow-file-access-from-files',
             '--disable-gpu', '--hide-scrollbars'],
    });
    const page = await browser.newPage({
      viewport: { width: width, height: 1200 },
      deviceScaleFactor: scale,
    });
    const abs = path.resolve(htmlFile);
    await page.goto('file://' + abs, { waitUntil: 'load', timeout: 60000 });
    // 等所有图片解码完成（本地 file:// 通常很快，但大图仍可能慢）；最多等 8s
    await page.evaluate(() => new Promise((resolve) => {
      const imgs = Array.prototype.slice.call(document.images || []);
      if (!imgs.length) return resolve(1);
      let left = imgs.filter((im) => !im.complete).length;
      if (!left) return resolve(1);
      const done = () => { if (--left <= 0) resolve(1); };
      const t = setTimeout(resolve, 8000);
      imgs.forEach((im) => {
        im.addEventListener('load', done, { once: true });
        im.addEventListener('error', done, { once: true });
      });
      Promise.race([new Promise((r) => setTimeout(r, 8000)), Promise.all(
        imgs.map((im) => (im.decode ? im.decode().catch(() => 0) : 0)),
      )]).then(() => { clearTimeout(t); resolve(1); });
    }));
    await new Promise((r) => setTimeout(r, 400));
    const outDir = path.dirname(path.resolve(out));
    fs.mkdirSync(outDir, { recursive: true });
    await page.screenshot({ path: out, fullPage: true, type: 'png' });
    await browser.close();
    console.log('OK ' + out);
  } catch (e) {
    if (browser) try { await browser.close(); } catch (_) {}
    console.error('FAIL ' + (e && e.message ? e.message : e));
    process.exit(1);
  }
})();
