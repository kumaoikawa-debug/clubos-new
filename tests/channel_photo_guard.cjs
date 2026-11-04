// 复现小红书媒体池混入 logo、回顾照片与受限原始地址；不访问浏览器或网络。
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/channel-render.js'), 'utf8');
const sandbox = { window: {}, location: { origin: 'https://review.invalid' }, CLUB: 1 };
vm.createContext(sandbox);
vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'window.review={xhsModel,setMediaActivityId};})();'), sandbox);
const ctx = { title: '真实活动', mm: {
  logo: { kind: 'logo', url: '/static/uploads/1/logo.png' },
  recap: { kind: 'photo', source: 'recap', url: '/static/uploads/1/recap.jpg' },
  photo: { kind: 'photo', url: '/static/uploads/1/photo.jpg' }
}};
sandbox.window.review.setMediaActivityId(9);
const model = sandbox.window.review.xhsModel({ imageSequence: ['logo', 'recap'], body: '真实内容' }, ctx);
assert.equal(model.heroSrc, 'https://review.invalid/api/club/1/activities/9/media/uploads/1/photo.jpg');
assert.equal(model.photos.length, 0);
console.log('PASS xhs real-photo whitelist and club-scoped media URL');
