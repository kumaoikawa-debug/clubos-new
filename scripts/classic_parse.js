/* classic_parse.js —— 用 classic script（非 module）目标解析 static 目录下所有 js 文件
 *
 * 为什么不能只靠 `node --check`：
 *   Node 22 默认开启模块语法探测（--experimental-detect-module 已默认打开），
 *   `node --check` 会把 .js 当 ESM 解析 —— 于是「顶层 await」这种在浏览器里
 *   必然 SyntaxError 的代码会被静默判为通过（假绿）。实测：
 *     printf 'await 1;\n' > /tmp/t.js && node --check /tmp/t.js   →  退出码 0
 *     new Function('await 1;')                                     →  SyntaxError
 * 浏览器里 `<script src=...>` 是 classic script，所以真正的判据必须是 classic 目标。
 *
 * 用法：node scripts/classic_parse.js            （默认扫 static/ 下所有 .js）
 *      node scripts/classic_parse.js a.js b.js  （指定文件）
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

let files = process.argv.slice(2);
if (!files.length) {
  const walk = d => fs.readdirSync(d, { withFileTypes: true }).flatMap(e =>
    e.isDirectory() ? walk(path.join(d, e.name)) : (e.name.endsWith('.js') ? [path.join(d, e.name)] : []));
  files = walk(path.join(__dirname, '..', 'static')).sort();
}

let bad = 0;
for (const f of files) {
  const src = fs.readFileSync(f, 'utf8');
  try {
    new vm.Script(src, { filename: f });
    console.log('OK   ' + f);
  } catch (e) {
    bad++;
    // V8 报的 line 是整个文件行号，codeFrame 里的行尾插入位置容易误导，
    // 这里只取第一行并把出错那一行原样打出来。
    console.log('FAIL ' + f);
    console.log('     ' + e.message);
    const ln = Number((e.stack.match(/^.*:(\d+)$/m) || [])[1]);
    if (ln) {
      const line = src.split('\n')[ln - 1] || '';
      console.log('     line ' + ln + ': ' + line.slice(0, 220));
    }
  }
}
console.log(bad ? `\n${bad} 个文件在浏览器 classic 目标下解析失败` : `\n全部 ${files.length} 个文件 classic 解析通过`);
process.exit(bad ? 1 : 0);
