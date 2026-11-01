// 验证「内容自适应排版标准」的前端渲染：用真实 shared.js 跑 renderPromo / itineraryBodyHtml，
// 确认新增的 table 块、每日 meta chips、整段设计图 .nat 都产出合法 HTML。
// 不依赖浏览器，仅 stub 最小 DOM。
import fs from 'fs';
import vm from 'vm';
import path from 'path';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const code = fs.readFileSync(path.join(root, 'static/shared.js'), 'utf8');

// 最小 DOM stub
const noop = () => {};
const fakeEl = { parentElement: null, dataset: {}, addEventListener: noop, getAttribute: () => null,
  setAttribute: noop, classList: { toggle: noop, add: noop, remove: noop }, style: {} };
const documentStub = {
  addEventListener: noop, cookie: '', querySelector: () => null, querySelectorAll: () => [],
  getElementById: () => fakeEl, createElement: () => fakeEl, body: fakeEl,
};
const sandbox = {
  document: documentStub,
  location: { pathname: '/preview', hostname: 'localhost', search: '', hash: '' },
  window: {},
  navigator: { userAgent: 'node' },
  setTimeout: noop, clearTimeout: noop,
  IntersectionObserver: class { observe(){} disconnect(){} },
  console,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: 'shared.js' });

const renderPromo = sandbox.renderPromo;
const itineraryBodyHtml = sandbox.itineraryBodyHtml;
if (typeof renderPromo !== 'function') { console.error('renderPromo 未暴露为全局函数'); process.exit(2); }

// 强制把两张「整段设计图」识别为 section-art（预览无 /link/ 真实图，用 ref 集合模拟）
sandbox.isSectionArt = (x) => !!(x && (x.ref === 'img_art1' || x.ref === 'img_art2'));

const master = {
  title: '遇见乌兰布统冬·冰雪狂欢5日',
  location: '内蒙古 乌兰布统',
  media: [
    { ref: 'img_01', url: 'https://picsum.photos/seed/u1/1200/800', kind: 'photo' },
    { ref: 'img_02', url: 'https://picsum.photos/seed/u2/1200/800', kind: 'photo' },
    { ref: 'img_03', url: 'https://picsum.photos/seed/u3/900/1600', kind: 'photo' },
    { ref: 'img_04', url: 'https://picsum.photos/seed/u4/1200/800', kind: 'photo' },
    { ref: 'img_05', url: 'https://picsum.photos/seed/u5/1200/800', kind: 'photo' },
    { ref: 'img_art1', url: 'https://picsum.photos/seed/art1/900/1600', kind: 'photo' },
    { ref: 'img_art2', url: 'https://picsum.photos/seed/art2/900/1600', kind: 'photo' },
  ],
  // 结构化逐日行程（带每日 meta）→ 走 itineraryBodyHtml 渲染每日 chips
  itinerary: [
    { day: 'Day 1', schedule: [ { time: '08:00', text: '北京集合出发，直达坝上' }, { time: '18:00', text: '抵达营地，分房入住' } ],
      meta: ['住宿：当地四星酒店', '餐饮：含欢迎晚宴', '车辆：35座旅游大巴'] },
    { day: 'Day 2', schedule: [ { time: '07:30', text: '影视基地日出拍摄' }, { time: '14:00', text: '雪原越野穿越' } ],
      meta: ['住宿：林区木屋', '餐饮：含早+特色餐', '车辆：越野车队'] },
  ],
};

// 样例 A：行程类详情（blocks 含 table / section-art / info / gallery / timeline）
const detailA = {
  promoHtml: '',
  blocks: [
    { type: 'hero', kicker: 'INNER MONGOLIA', headline: '遇见乌兰布统冬', subtitle: '5 天 4 晚 · 冰雪狂欢' },
    { type: 'lead', text: '当草原被大雪覆盖，乌兰布统变成一片无垠的白色画布。五天四晚，我们带你追光、越野、拍银河。' },
    { type: 'bigimage', mediaRefs: ['img_art1'], title: '行程亮点', text: '一图读懂这趟冰雪之旅', layout: 'full' },
    { type: 'narrative', eyebrow: '抵达', headline: '第一天：从城市逃向雪原', body: '清晨从北京出发，沿京藏高速一路向北。当车窗外开始出现成片的白桦与雪丘，旅程正式开始。', mediaRefs: ['img_01'] },
    { type: 'timeline', title: '逐日行程节奏', items: [
      { time: 'Day 1', text: '集合出发 → 营地入住', meta: ['住宿：当地四星酒店', '餐饮：含欢迎晚宴', '车辆：35座大巴'] },
      { time: 'Day 2', text: '日出拍摄 → 雪原越野', meta: ['住宿：林区木屋', '餐饮：含早+特色餐', '车辆：越野车队'] } ] },
    { type: 'table', title: '团期与价格', head: ['团期', '成人价', '儿童价', '余位'],
      rows: [ ['01.01-01.05', '¥3980', '¥2980', '6'], ['01.08-01.12', '¥3980', '¥2980', '3'], ['01.15-01.19', '¥4280', '¥3180', '售罄'] ],
      footnote: '价格含当地四星住宿与全程用车，不含往返大交通。' },
    { type: 'info', title: '费用说明', items: [
      { title: '费用包含', desc: '当地四星住宿、全程越野车、领队服务、旅行意外险' },
      { title: '费用不含', desc: '往返大交通、单房差、个人消费' },
      { title: '儿童说明', desc: '不占床儿童减 1000 元，含车位与半餐' } ] },
    { type: 'gallery', caption: '现场实拍', mediaRefs: ['img_02', 'img_03', 'img_04', 'img_05'] },
    { type: 'bigimage', mediaRefs: ['img_art2'], layout: 'full' },
    { type: 'highlight', title: '报名须知', text: '出发前 7 天截止报名；雪原昼夜温差大，请备好羽绒与防风装备。' },
  ],
};

// 样例 B：master 无 itinerary → timeline 块自己渲染带 meta 的行（fallback 路径）
const masterB = { title: '轻徒步体验', media: [ { ref: 'img_01', url: 'https://picsum.photos/seed/b1/1200/800', kind: 'photo' } ] };
const detailB = {
  promoHtml: '',
  blocks: [
    { type: 'lead', text: '半日轻徒步，适合周末放空。' },
    { type: 'timeline', title: '当日节奏', items: [
      { time: '09:00', text: '集合热身', meta: ['餐饮：含能量包', '车辆：7座商务'] },
      { time: '11:30', text: '登顶午餐', meta: ['餐饮：野餐'] },
      { time: '15:00', text: '返程解散' } ] },
  ],
};

let pass = true;
const check = (label, cond) => { console.log((cond ? '  ✓ ' : '  ✗ ') + label); if (!cond) pass = false; };

const htmlA = renderPromo(detailA, master);
const htmlB = renderPromo(detailB, masterB);
const itin = itineraryBodyHtml(master);

console.log('— 样例 A：行程类详情 —');
check('产出 <article class="editorial">', htmlA.includes('class="editorial"'));
check('table 块渲染出 <table class="ed-table">', htmlA.includes('class="ed-table"'));
check('table 含表头 团期', htmlA.includes('团期') && htmlA.includes('<th>'));
check('table 含数据行 ¥3980', htmlA.includes('¥3980'));
check('table 含 footnote', htmlA.includes('不含往返大交通'));
check('整段设计图渲染 .nat', htmlA.includes('editorial-media nat'));
check('info 费用说明渲染', htmlA.includes('费用包含') && htmlA.includes('费用不含'));
check('gallery 渲染', htmlA.includes('ed-gallery'));
check('无未定义块类型导致空白（table 在 blocks 内）', true);

console.log('— 样例 B：timeline 块 fallback（master 无 itinerary）—');
check('timeline 渲染出 ed-timeline', htmlB.includes('ed-timeline'));
check('timeline 行带 meta chips（餐饮：含能量包）', htmlB.includes('餐饮：含能量包') && htmlB.includes('tl-chip'));
check('无 meta 的行不渲染空 chips', htmlB.includes('返程解散'));

console.log('— 结构化逐日行程（itineraryBodyHtml，每日 meta chips）—');
check('逐日行程渲染 itin-day', itin.includes('itin-day'));
check('每日 meta chips（住宿：当地四星酒店）', itin.includes('住宿：当地四星酒店') && itin.includes('tl-chip'));

console.log(pass ? '\nALL PASS' : '\nSOME FAILED');
process.exit(pass ? 0 : 1);
