"""回归测试：出行清单两区 UI + 费用说明极性（2026-10-23 用户截图反馈三件事）。

背景（用户截图实锤）：
  ① 费用区「notIncluded」键裸奔成英文，且整行被打成 ✓ 绿 chip —— 负向键含 "includ"，
     极性判据先匹配了正向，顾客把「个人消费」读成「已包含」；
  ② 装备卡只有 emoji 没有商城真实产品图与介绍；总览「商城可配 6 项」与分区「4 件」
     两个数对不上；登山杖卡挂着「平替 · 防水防滑徒步鞋」像挂错标签；
  ③ 「这些请自己准备」是 emoji 方块平铺，观感差。

锁住的不变量：
  1. fees 负向键（notIncluded/excluded/不含…）→ 「不包含」+ is-no（✗），不被误判成 ✓；
  2. 装备卡必须上商城真实产品图（imageUrl）与商城介绍（description），
     无图退 emoji 方块、裂图兜底函数有 parentNode 守卫；
  3. 「N 件装备」与「商城可配 X 项」口径可对（件=去重商品数，项=覆盖的清单数）；
  4. 平替 chip 文本只留清单项名，「替」是小徽标不焊进文字；
  5. 自备区是 soft pills（span 胶囊），不再是 div 网格卡叠卡。

node 侧真实执行 shared.js 的 feeListHtml/gearRow/renderPacking（只读不写）；
python 侧用推荐引擎算真实匹配（不 import app、不建库、不碰业务库）。
"""
import io
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from clubos_domain.gear_recommend import GearRecommendService  # noqa: E402

NODE = os.environ.get('CLUBOS_NODE') or 'node'
SHARED_JS = os.path.join(ROOT, 'static/shared.js')
SHARED_CSS = os.path.join(ROOT, 'static/shared.css')

FAILED = []


def ck(label, cond, extra=''):
    print(('  PASS  ' if cond else '  FAIL  ') + label + (('  | ' + str(extra)) if extra else ''))
    if not cond:
        FAILED.append(label)


# ── 真实在售商品（与生产库同一批：4 件，含商城介绍）─────────────────────────
PRODUCTS = [
    {'id': 1, 'name': '轻量防风软壳', 'price': 699.0, 'stock': 27, 'category': '服装',
     'image_url': '/static/uploads/products/gear-1-1.jpg',
     'description': '轻薄型防风软壳，为春秋交替与海拔大风环境准备。面料经过防泼水处理，偶遇小雨雪不易渗透。'},
    {'id': 2, 'name': '碳纤维折叠登山杖', 'price': 299.0, 'stock': 65, 'category': '徒步装备',
     'image_url': '/static/uploads/products/gear-2-1.jpg',
     'description': '碳纤维杖身，三节折叠后约一掌长度，可直插包侧袋。杖尖配防泥裙，外锁扣一扣一调。'},
    {'id': 3, 'name': '22L 日行背包', 'price': 459.0, 'stock': 35, 'category': '背包',
     'image_url': '/static/uploads/products/gear-3-1.jpg',
     'description': '22 升单日容量，主仓开口到顶，取物不用翻底。加厚肩带配胸扣与腰翼。'},
    {'id': 4, 'name': '美丽诺羊毛基础层', 'price': 599.0, 'stock': 52, 'category': '服装',
     'image_url': '/static/uploads/products/gear-4-1.jpg',
     'description': '美丽诺羊毛长袖基础层，贴肤柔软不扎。羊毛天然抑菌，连续穿着不易闷味。'},
]
# 生产活动 55 的真实清单（13 项）
CHECKLIST = ['防水防滑徒步鞋', '透气排汗速干衣裤', '防晒外套', '单日徒步小背包', '防晒帽', '登山杖',
             '充电宝', '防晒霜', '护唇膏', '护手霜', '防风外套', '具有一定弹性的舒适服饰', '身份证']
MASTER = {'title': '卓西牧场徒步 × 高山疗愈瑜伽', 'location': '四川 甘孜', 'checklist': CHECKLIST}

# ── python 侧：引擎真实匹配（description 必须随 pick 下发）──────────────────
plan = GearRecommendService().plan(PRODUCTS, MASTER).as_dict()
ck('引擎 coverage 与生产一致（13 项 / 精确 6 / 平替 1）',
   plan['coverage'] == {'needs': 13, 'matched': 6, 'substituted': 1, 'ratio': round(6 / 13, 4)},
   json.dumps(plan['coverage'], ensure_ascii=False))
ck('pick 带出商城介绍 description',
   all('description' in p for it in plan['items'] for p in (it.get('matches') or []) + (it.get('substitutes') or [])),
   'matches/subs 均含 description 键')
ck('pick 带出商城产品图 imageUrl',
   all(p.get('imageUrl', '').startswith('/static/uploads/products/')
       for it in plan['items'] for p in (it.get('matches') or []) + (it.get('substitutes') or [])))

# 会员折扣按 app.py _gear_plan_for 同款口径补（Enterprise 9 折），让渲染走真实价格分支
RATE, PLAN_NAME = 0.9, 'Enterprise'
plan['memberDiscount'] = {'rate': RATE, 'discountZhe': 9.0, 'planName': PLAN_NAME,
                          'tierName': PLAN_NAME, 'label': 'Enterprise 套餐 9 折'}
for it in plan['items']:
    for p in (it.get('matches') or []) + (it.get('substitutes') or []):
        base = round(float(p.get('price') or 0), 2)
        p['memberPrice'] = round(base * RATE, 2)
        p['memberSavings'] = round(base - base * RATE, 2)
        p['memberTierName'] = PLAN_NAME

payload = {'master': MASTER, 'plan': plan}

# ── node 侧：真实执行 shared.js 渲染函数 ────────────────────────────────────
NODE_SRC = r'''
const fs=require('fs');
global.window={};
global.document={cookie:'',addEventListener:function(){},querySelector:function(){return null},querySelectorAll:function(){return []}};
global.location={pathname:'/web'};
eval(fs.readFileSync(process.argv[1],'utf8'));
const P=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
window.openProductFromPacking=function(){};   // 让 gearRow 走「可点进详情」分支
let fails=0;
function t(name,cond,extra){ if(cond){console.log('  PASS  '+name)}else{fails++;console.log('  FAIL  '+name+(extra===undefined?'':' | '+extra))} }
function has(h,s){return String(h).indexOf(s)>=0}

/* ① 费用极性：负向键 → 「不包含」+ is-no，绝不打成 ✓ */
let fee=feeListHtml({included:['桌餐午餐'],notIncluded:['个人消费及其他未提及的自理项目']});
t('notIncluded 标签中文化', has(fee,'<b>不包含</b>'), fee.slice(0,120));
t('notIncluded 不再裸奔', !has(fee,'notIncluded'));
t('notIncluded 行是 is-no（✗）', /<div class="fee-row is-no"><b>不包含<\/b>/.test(fee), fee.match(/fee-row[^"]*"><b>[^<]*<\/b>/g));
t('同组 included 仍是 is-ok（✓）', /<div class="fee-row is-ok"><b>包含<\/b>/.test(fee));
t('excluded 也是 is-no', /is-no/.test(feeListHtml({excluded:['x']})));
t('中文「费用不含」也是 is-no', /is-no/.test(feeListHtml({'费用不含':['x']})));
t('中文「包含」仍是 is-ok', /is-ok/.test(feeListHtml({'包含':['x']})));

/* ② 装备卡：真实产品图 + 商城介绍 + 价格/会员价/平替/转义 */
const pick={id:2,name:'碳纤维折叠登山杖',price:299,stock:65,inStock:true,
  imageUrl:'/static/uploads/products/gear-2-1.jpg',emoji:'🥾',
  reason:'商城暂无「徒步鞋袜」，推荐相近的「徒步支撑」类装备',
  description:'碳纤维杖身，三节折叠后约一掌长度，可直插包侧杖尖配防泥裙。',
  tags:['trekking'],memberPrice:269.1,memberSavings:29.9,memberTierName:'Enterprise'};
let row=gearRow(pick,{canBuy:true},false);
t('装备卡上商城真实产品图', has(row,'<img src="/static/uploads/products/gear-2-1.jpg"'));
t('产品图带裂图兜底 onerror', has(row,'onerror="gearImgFallback(this)"'));
t('装备卡带商城介绍（description）', has(row,'gear-desc')&&has(row,'三节折叠后约一掌长度'));
t('介绍两行截断（line-clamp）', true); // 由 CSS 守，node 不判样式
t('会员价替代原价 + 原价划线', has(row,'¥269.10')&&has(row,'gear-was')&&has(row,'¥299'));
t('会员节省说明', has(row,'Enterprise价 省 ¥29.9'));
t('平替卡：徽标 + 虚线类', has(gearRow(pick,{},true),'gear-badge')&&has(gearRow(pick,{},true),'gear-row--sub'));
t('无图商品退 emoji 方块', has(gearRow(Object.assign({},pick,{imageUrl:''}),{},false),'gear-img--none')
  &&!has(gearRow(Object.assign({},pick,{imageUrl:''}),{},false),'<img'));
t('商品名转义（XSS）', !has(gearRow(Object.assign({},pick,{name:'<script>x</script>'}),{},false),'<script>'));
t('缺货标注', has(gearRow(Object.assign({},pick,{inStock:false}),{},false),'暂时缺货'));

/* 裂图兜底函数：存在、能换 emoji、无 parentNode 不炸 */
t('gearImgFallback 已挂到 window', typeof window.gearImgFallback==='function');
let box={classList:{_c:' gear-img',add:function(c){this._c+=' '+c},contains:function(c){return this._c.indexOf(c)>=0}},innerHTML:''};
window.gearImgFallback({parentNode:box,getAttribute:function(){return '🥾'}});
t('裂图后换成品类 emoji 方块', has(box.innerHTML,'gear-emoji')&&has(box.innerHTML,'🥾'),box.innerHTML);
let threw=false;try{window.gearImgFallback({parentNode:null,getAttribute:function(){return '🥾'}})}catch(e){threw=true}
t('图片已移出 DOM 时不抛错', !threw);

/* ③④ renderPacking 全量：口径对齐 + 平替徽标 + 自备 pills */
let html=renderPacking(P.master,{gear:P.plan,canBuy:true,manage:false});
t('总览：清单 13 项 · 商城可配 7 项（6 精确 + 1 平替）', has(html,'清单 13 项 · 商城可配 7 项'));
t('总览带平替数与会员折扣', has(html,'平替 1 项')&&has(html,'Enterprise 9 折'));
t('分区头：4 件装备', has(html,'商城可以配齐')&&has(html,'>4 件<'));
t('分区头注明覆盖清单 7 项（两个数对上）', has(html,'覆盖清单 7 项'));
t('四件商品全部上真实产品图', (html.match(/<img src="\/static\/uploads\/products\//g)||[]).length===4,
   (html.match(/<img src="\/static\/uploads\/products\//g)||[]).length+' 张');
t('登山杖卡：既是精确匹配就不打平替徽标', !has(html,'gear-badge')&&!has(html,'gear-row--sub'));
t('平替 chip：小徽标「替」不焊文字', has(html,'<i>替</i>')&&!has(html,'平替 · '));
t('平替 chip 挂 tooltip 说明', has(html,'商城没有清单里那件，这是最接近的同类'));
t('精确匹配 chip 保留清单项名', has(html,'>登山杖</span>'));
t('自备区是 span 胶囊 pills', has(html,'<span class="pack-own__item">')&&!has(html,'<div class="pack-own__item">'));
t('自备 6 项（防晒帽/充电宝/防晒霜/护唇膏/护手霜/身份证）',
   (html.match(/pack-own__item/g)||[]).length===6,(html.match(/pack-own__item/g)||[]).length+' 项');
t('自备项带品类图标', has(html,'pack-own__ico'));

console.log('NODE_RESULT '+(fails?'FAIL':'OK'));
process.exit(fails?1:0);
'''

with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as f:
    json.dump(payload, f, ensure_ascii=False)
    payload_path = f.name
try:
    proc = subprocess.run([NODE, '-e', NODE_SRC, SHARED_JS, payload_path],
                          capture_output=True, text=True)
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr)
        FAILED.append('node 渲染断言（见上）')
finally:
    os.unlink(payload_path)

# ── CSS / HTML 静态守门 ─────────────────────────────────────────────────────
css = io.open(SHARED_CSS, encoding='utf-8').read()
ck('CSS：产品图方块 .gear-img', '.gear-img{' in css and '.gear-img img{' in css)
ck('CSS：介绍两行截断', '-webkit-line-clamp:2' in css)
ck('CSS：平替小徽标 .pack-needs__sub i', '.pack-needs__sub i{' in css)
ck('CSS：自备区 flex 胶囊（不再是 grid 卡）',
   '.pack-own{display:flex' in css and '.pack-own__item{display:inline-flex' in css)

htmls = {}
for end in ('web', 'club', 'leader', 'platform'):
    p = os.path.join(ROOT, 'static', end, 'index.html')
    htmls[end] = io.open(p, encoding='utf-8').read()
vers = set()
for end, h in htmls.items():
    import re
    m = re.search(r'shared\.css\?v=([0-9a-z]+)', h)
    if m:
        vers.add(m.group(1))
ck('四端 shared.css 缓存版本一致（改版必升版本，防浏览器吃旧缓存）', len(vers) == 1 and vers != {''}, vers)
app_src = io.open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
ck('app.py：_gear_plan_for 查出 description', 'image_url,description FROM products' in app_src)

print('\nRESULT:', 'ALL PASS' if not FAILED else 'HAS FAILURE')
sys.exit(0 if not FAILED else 1)
