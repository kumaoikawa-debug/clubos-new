# -*- coding: utf-8 -*-
"""商品域多品牌化回归（2026-10-24 用户需求：后台商品管理要考虑多品牌，
   逻辑上展示 品牌 / 品类 / 尺码 / 颜色 / 男/女款 等服饰电商标准模式）。

锁住的东西，按「改坏了会怎样」排序：

① 维度进字典表：product_brands / product_categories 是真表，商品存外键；
   老数据迁移把 products.category 文本建成一级品类并回填 category_id，
   且**不删文本列**（C 端副标题、Medusa 同步、老统计都还在读它）。
② SKU 按「品牌-品类-商品-颜色-尺码」自动生成：手填在多品牌下必然撞号，
   撞号在采购入库和库存流水里是查不出的数据事故。
③ 库存真源在规格层：products.stock 只是汇总，任何规格增删改都要重算，
   否则后台列表和 C 端详情会对不上。
④ 「默认」占位规格：迁移/无矩阵建的商品靠它承接库存；一旦有了真实规格
   它必须退场（否则 C 端把「默认」和「炭灰/M」并排显示），且库存要转移、
   不能凭空消失；删到 0 个规格时又要补回来（否则商品在 C 端直接缺货）。
⑤ 筛选走后端 SQL：多品牌之后商品池会有几百个 SKU，全量拉回前端再筛
   等于每次进商城都把整个池子拖到浏览器里。
⑥ CSV 一行 = 一个 SKU，按货号归组，单行出错只跳过该行 ——
   一张几百行的表，全有或全无是没法用的。
⑦ 前端形态：平台端筛选条 / 字典管理 / 矩阵弹层 / C 端两级选择器必须真在，
   且同一批发版的脚本版本号一致（不一致就会出现一个旧缓存、一个新代码）。

自带临时库隔离，不碰项目真库。
"""
import csv
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault('CLUBOS_TEST_OPEN', '1')
os.environ.setdefault('MOCK_AI', '1')
os.environ.setdefault('COMMERCE_PROVIDER', 'local')

TMP = Path(tempfile.mkdtemp(prefix='clubos-catalog-smoke-'))
os.environ['CLUBOS_DB_PATH'] = str(TMP / 'clubos.db')

import db  # noqa: E402
db.init_db()
import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(app.app)
FAILS = []


def ok(cond, label, extra=''):
    print(('  PASS  ' if cond else '  FAIL  ') + label + (f'   | {extra}' if extra else ''))
    if not cond:
        FAILS.append(label)


def main():
    with db.conn() as cc:
        # 老数据形态：一个只有 category 文本、没有任何维度的商品。
        # 迁移标记在 import 时的 init_db() 里就已经置位，所以这里手工再跑一次回填，
        # 模拟「老库升级」这一刻 —— 否则这条断言永远测不到迁移本身。
        cc.execute("INSERT INTO products(name,sku,price,stock,status,category,commission_rate) "
                   "VALUES('老软壳','GEAR-OLD-1',699,27,'active','服装',0.08)")
        cc.execute("DELETE FROM platform_settings WHERE key='catalog_backfilled_v030'")
        db._backfill_v030_catalog(cc)

    print('\n── ① 迁移：字典表 + 回填 ──')
    with db.conn() as cc:
        cats = [dict(r) for r in cc.execute('SELECT id,name,parent_id FROM product_categories')]
        p = cc.execute("SELECT category,category_id,gender,brand_id FROM products WHERE sku='GEAR-OLD-1'").fetchone()
        vcols = [d[1] for d in cc.execute('PRAGMA table_info(product_variants)')]
    ok(any(x['name'] == '服装' for x in cats), '老 category 文本回填成一级品类')
    ok(p['category_id'] == next(x['id'] for x in cats if x['name'] == '服装'), 'category_id 已回填')
    ok(p['category'] == '服装' and p['brand_id'] is None, '文本列保留、品牌留空待认领')
    ok(p['gender'] == 'unisex', '缺省人群落 unisex（不能让运营为了必填随便选）')
    ok(all(col in vcols for col in ('color', 'color_hex', 'size', 'barcode')), '规格表新增四列')

    print('\n── ② 品牌 / 品类字典 CRUD ──')
    r = c.post('/api/platform/brands', json={'name': 'Kailas'})
    ok(r.status_code == 200 and r.json().get('id'), '新建品牌')
    bid = r.json()['id']
    ok(c.post('/api/platform/brands', json={'name': 'Kailas'}).status_code == 400, '重名品牌 400')
    ok(c.post('/api/platform/brands', json={'name': '  '}).status_code == 400, '空品牌名 400')
    c.patch(f'/api/platform/brands/{bid}', json={'name': 'Kailas 凯乐石'})
    brands = c.get('/api/platform/brands').json()
    ok(any(b['name'] == 'Kailas 凯乐石' for b in brands), '改品牌名')

    r = c.post('/api/platform/product-categories', json={'name': '冲锋衣', 'parentId': next(x['id'] for x in cats if x['name'] == '服装')})
    cid_del = r.json()['id']
    ok(c.delete(f'/api/platform/product-categories/{next(x["id"] for x in cats if x["name"] == "服装")}').status_code == 400,
       '有子品类的父品类不许删')
    ok(c.delete(f'/api/platform/product-categories/{cid_del}').status_code == 200, '子品类可删')
    # 商品要挂一个**活着**的品类：上面那个已经删了，这里另建一个（顺带验层级）
    cid = c.post('/api/platform/product-categories',
                 json={'name': '软壳', 'parentId': next(x['id'] for x in cats if x['name'] == '服装')}).json()['id']

    print('\n── ③ 新建商品：六维 + 颜色×尺码矩阵 ──')
    r = c.post('/api/platform/products', json={
        'name': 'Beta LT 冲锋衣', 'sku': 'AR-BETA-LT', 'price': 4299, 'stock': 40,
        'commission_rate': 0.1, 'brandId': bid, 'categoryId': cid, 'gender': '男款',
        'season': '2026 秋冬', 'series': 'Beta', 'material': 'GORE-TEX 3L',
        'description': '测试', 'colors': [{'name': '炭灰', 'hex': '4a4a4a'}, {'name': '深蓝', 'hex': '#1b2a4a'}],
        'sizes': ['S', 'M', 'L', 'XL']})
    ok(r.status_code == 200, '带矩阵建商品 200', r.text)
    pid = r.json()['id']
    ok(r.json()['variantsCreated'] == 8, '2 色 × 4 码 = 8 个规格')
    vs = c.get(f'/api/platform/products/{pid}/variants').json()
    ok(len(vs) == 8 and all(v['status'] == 'active' for v in vs), '8 个规格全部在售')
    ok(all(v['sku'].startswith('KAILAS') and 'P%04d' % pid in v['sku'] for v in vs), 'SKU 带品牌与商品段')
    ok(all(v['color_hex'] in ('#4A4A4A', '#1B2A4A') for v in vs), '色值规范化（补 #、大写）')
    ok(sum(int(v['stock']) for v in vs) == 40, '规格库存合计 = 期初 40')
    ok(vs[0]['stock'] == 40 and all(int(v['stock']) == 0 for v in vs[1:]), '期初库存只落第一个组合')
    plist = c.get('/api/platform/products', params={'q': 'AR-BETA-LT'}).json()
    p0 = plist[0]
    ok(p0['stock'] == 40, '商品汇总库存已重算')
    ok(p0['brand_name'] == 'Kailas 凯乐石' and p0['category_name'] == '软壳', '品牌/品名列可读')
    ok(p0['parent_category_name'] == '服装', '父品类名一起下发')
    ok(p0['gender'] == 'male', '中文人群别名归一（男款→male）')

    print('\n── ④ 无矩阵：建「默认」占位规格承接库存 ──')
    r = c.post('/api/platform/products', json={'name': '登山杖', 'sku': 'AR-POLE-1', 'price': 299, 'stock': 12})
    ok(r.status_code == 200 and r.json()['variantsCreated'] == 1, '不配矩阵也建 1 个规格')
    pv = c.get(f"/api/platform/products/{r.json()['id']}/variants").json()
    ok(len(pv) == 1 and pv[0]['is_default'] == 1 and pv[0]['stock'] == 12, '占位规格带全量库存')

    print('\n── ⑤ 矩阵端点：跳过已存在 + 占位退场 + 库存转移 ──')
    r = c.post(f'/api/platform/products/{pid}/variants/matrix',
               json={'colors': [{'name': '炭灰', 'hex': '#4a4a4a'}, {'name': '米白', 'hex': '#e8dcc8'}], 'sizes': ['S', 'M'], 'stock': 5})
    ok(r.json()['created'] == 2 and r.json()['skipped'] == 2, '已存在的组合跳过不覆盖', r.text)
    vs = c.get(f'/api/platform/products/{pid}/variants').json()
    ok(len(vs) == 10, '矩阵后共 10 个规格')
    ok(c.post(f'/api/platform/products/{pid}/variants/matrix', json={'colors': [], 'sizes': []}).status_code == 400,
       '空矩阵 400')

    print('\n── ⑥ 规格增改：颜色/尺码/条码 + 名称自动拼 ──')
    r = c.post(f'/api/platform/products/{pid}/variants',
               json={'color': '米白', 'size': 'L', 'stock': 3})
    ok(r.status_code == 200, '只给颜色尺码也能建规格')
    nv = [v for v in c.get(f'/api/platform/products/{pid}/variants').json() if v['size'] == 'L' and v['color'] == '米白']
    ok(nv and nv[0]['name'] == '米白 / L', '名称自动拼成「颜色 / 尺码」')
    vid = nv[0]['id']
    c.patch(f'/api/platform/products/{pid}/variants/{vid}',
            json={'color': '米白', 'size': 'L', 'barcode': '6901234567890', 'stock': 9, 'price': 4599})
    nv = [v for v in c.get(f'/api/platform/products/{pid}/variants').json() if v['id'] == vid][0]
    ok(nv['barcode'] == '6901234567890' and nv['stock'] == 9 and nv['price'] == 4599, '条码/库存/规格价可改')
    total = c.get('/api/platform/products', params={'q': 'AR-BETA-LT'}).json()[0]['stock']
    ok(total == 40 + 5 + 5 + 9 - 0 and total > 0, '改规格后商品汇总跟着重算', str(total))

    print('\n── ⑦ 删到 0 个规格：补回「默认」 ──')
    pid2 = c.post('/api/platform/products', json={'name': '临时商品', 'sku': 'AR-TMP-1', 'price': 99, 'stock': 7}).json()['id']
    v2 = c.get(f'/api/platform/products/{pid2}/variants').json()
    for v in v2:
        rr = c.delete(f'/api/platform/products/{pid2}/variants/{v["id"]}')
    last = c.get(f'/api/platform/products/{pid2}/variants').json()
    ok(len(last) == 1 and last[0]['is_default'] == 1 and last[0]['stock'] == 7,
       '删空后补回占位规格且库存不丢')

    print('\n── ⑧ 筛选走后端 SQL ──')
    ok(len(c.get('/api/platform/products', params={'brand_id': bid}).json()) >= 1, '平台端按品牌筛')
    ok(len(c.get('/api/platform/products', params={'gender': 'male'}).json()) >= 1, '平台端按人群筛')
    ok(c.get('/api/platform/products', params={'brand_id': bid, 'gender': 'female'}).json() == [], '组合筛选取交集')
    mall = c.get('/api/public/clubs/1/mall/products').json()
    ok(all('brand_name' in x and 'gender' in x for x in mall), 'C 端列表带品牌与人群字段')
    ok(len(c.get('/api/public/clubs/1/mall/products', params={'brand_id': bid}).json()) ==
       len([x for x in mall if x.get('brand_id') == bid]), 'C 端按品牌筛与全量比对一致')
    ok(len(c.get('/api/public/clubs/1/mall/products', params={'gender': 'male'}).json()) ==
       len([x for x in mall if x.get('gender') == 'male']), 'C 端按人群筛与全量比对一致')
    ok(len(c.get('/api/public/clubs/1/mall/products', params={'category_id': cid}).json()) ==
       len([x for x in mall if x.get('category_id') == cid]), 'C 端按品类筛与全量比对一致')

    print('\n── ⑨ C 端详情：两级选择器的数据源 ──')
    d = c.get(f'/api/public/clubs/1/mall/products/{pid}?user_id=1').json()
    ok([o['name'] for o in d['colorOptions']] == ['炭灰', '深蓝', '米白'], 'colorOptions 去重且有序')
    ok({o['name']: o['hex'] for o in d['colorOptions']}['炭灰'] == '#4A4A4A', 'colorOptions 带色值')
    ok(set(d['sizeOptions']) == {'S', 'M', 'L', 'XL'}, 'sizeOptions 去重')
    ok(all(v.get('color') and v.get('size') for v in d['variants']), '每个规格都带两个维度')

    print('\n── ⑩ CSV 导出 / 导入 ──')
    exp = c.get('/api/platform/products/export')
    ok(exp.status_code == 200 and '商品名,货号,品牌,品类' in exp.text, '导出带 18 列表头')
    rows = list(csv.DictReader(io.StringIO(exp.text)))
    ok(any(r['货号'] == 'AR-BETA-LT' and r['颜色'] == '炭灰' and r['尺码'] == 'S' for r in rows), '导出一行一个 SKU')

    csv_text = '\n'.join([
        '商品名,货号,品牌,品类,人群,季节,系列,面料,零售价,佣金率%,补货点,颜色,色值,尺码,条码,库存,规格价,介绍',
        'Mont Bell 羽绒服,AR-MB-DOWN,Mont Bell,羽绒服,男款,2026 秋冬,MB 系列,800蓬鹅绒,2899,12,8,火焰红,#ff3300,M,6901234567890,15,,轻薄',
        'Mont Bell 羽绒服,AR-MB-DOWN,Mont Bell,羽绒服,男款,2026 秋冬,MB 系列,800蓬鹅绒,2899,12,8,火焰红,#ff3300,L,6901234567891,10,,轻薄',
        'Mont Bell 羽绒服,AR-MB-DOWN,Mont Bell,羽绒服,男款,2026 秋冬,MB 系列,800蓬鹅绒,2899,12,8,深海蓝,001133,XL,,5,,轻薄',
        '坏行缺价格,AR-BAD-1,Mont Bell,羽绒服,男款,,,,,,,,,M,,3,,',
    ])
    r = c.post('/api/platform/products/import', json={'csv': csv_text, 'createMissing': True}).json()
    ok(r['createdProducts'] == 1 and r['createdVariants'] == 3, '按货号归组：1 商品 3 规格', str(r))
    ok(r['errorCount'] == 1 and r['errors'][0]['row'] == 5, '第 5 行出错只跳过该行', str(r['errors']))
    ok('Mont Bell' in r['newBrands'] and '羽绒服' in r['newCategories'], '表里没有的品牌/品类自动创建并回传')
    imp = c.get('/api/platform/products', params={'q': 'AR-MB-DOWN'}).json()[0]
    ok(imp['stock'] == 30 and imp['commission_rate'] == 0.12, '导入库存合计与佣金率正确')
    iv = c.get(f"/api/platform/products/{imp['id']}/variants").json()
    ok(all(v['color_hex'] in ('#FF3300', '#001133') for v in iv), '导入色值规范化')
    ok([v['barcode'] for v in iv if v['barcode']] == ['6901234567890', '6901234567891'], '导入条码落库')
    ok(not any(v['is_default'] for v in iv), '导入的真实规格顶掉占位规格')
    ok(c.post('/api/platform/products/import', json={'csv': '只有一列\n值'}).status_code == 400, '缺商品名列 400')
    ok(c.post('/api/platform/products/import', json={'csv': ''}).status_code == 400, '空 CSV 400')

    print('\n── ⑪ 品牌删除只摘挂、不删商品 ──')
    r = c.delete(f'/api/platform/brands/{bid}')
    ok(r.status_code == 200 and r.json().get('detachedProducts', 0) >= 1, '删品牌回报摘挂数量')
    left = c.get('/api/platform/products', params={'q': 'AR-BETA-LT'}).json()
    ok(left and left[0]['brand_id'] is None, '商品还在，只是没了品牌')

    print('\n── ⑫ 前端形态（文件级断言）──')
    idx = (ROOT / 'static/platform/index.html').read_text(encoding='utf-8')
    pjs = (ROOT / 'static/platform/platform.js').read_text(encoding='utf-8')
    wjs = (ROOT / 'static/web/web.js').read_text(encoding='utf-8')
    wcss = (ROOT / 'static/web/web.css').read_text(encoding='utf-8')
    wfjs = (ROOT / 'static/ux/workflows-platform.js').read_text(encoding='utf-8')
    ok('id="prodFilterBrand"' in idx and 'id="prodFilterCat"' in idx and 'id="prodFilterGender"' in idx,
       '平台端商品视图有三维筛选条')
    ok('onclick="manageBrands()"' in idx and 'onclick="manageCategories()"' in idx, '品牌/品类管理入口')
    ok('onclick="exportProducts()"' in idx and 'onclick="importProducts()"' in idx, 'CSV 导入导出入口')
    ok('function applyProductFilter' in pjs and 'function loadProductDicts' in pjs, '筛选与字典加载函数')
    # 踩过的坑：prodBrandOptions/prodCatOptions 返回的是 {value,label}（uxForm 的契约），
    #  paintProductFilters 却按 b.id 取 value → 选项 value 全变成字符串 "undefined"，
    #  筛选请求变成 brand_id=undefined → 422，表格空但计数还是旧的。这里钉住形状。
    ok('prodBrandOptions().map(b=>`<option value="${b.value}"' in pjs.replace('\n', ''),
       '品牌筛选项取 b.value（不是 b.id）')
    ok('prodCatOptions().map(c=>`<option value="${c.value}"' in pjs.replace('\n', ''),
       '品类筛选项取 c.value（不是 c.id）')
    ok('function prodMatrixDialog' in pjs and 'function prodDictDialog' in pjs, '矩阵弹层与字典管理弹层')
    ok('async function platformAddProduct' in pjs, '上架真身改名，避免被覆盖壳递归')
    ok('window.addProduct' in wfjs and 'platformAddProduct()' in wfjs, '覆盖壳委托到真身')
    ok("colors:[{name:c.name,hex:c.hex||''}]" in pjs or 'mx.colors' in pjs, '矩阵收集颜色色值')
    ok('function pdColorList' in wjs and 'function pdPickColorByIdx' in wjs, 'C 端两级选择器函数')
    # 2026-10-24 验收抓到的真 bug：尺码按钮渲染时按当前色过滤（pdSizeList），
    #  换色后另一色的按钮从没进过 DOM，pdPaintSheet 只能把当前色的藏起来 →
    #  尺码行整行消失、无可点项。钉住「全量渲染 + 显隐切换」的形态。
    ok('const sizeRow=vs.length?(hasSize?vs:[])' in wjs.replace('\n', ''),
       '尺码按钮全量渲染（换色后另一色按钮仍在 DOM，由 pdPaintSheet 显隐）')
    # 同一个坑的续集：hasSize 声明在 sizeRow 之后会进 TDZ → 详情页直接白屏。
    ok(wjs.index('const hasSize=vs.some(v=>v.size)') < wjs.index('const sizeRow=vs.length?(hasSize?vs:[])'),
       'hasSize 先声明后使用（TDZ 防回归）')
    ok('id="pdSheetColors"' in wjs and 'w-pd__color' in wcss, 'C 端颜色行与样式')
    ok('id="mallFilters"' in (ROOT / 'static/web/index.html').read_text(encoding='utf-8'), 'C 端商城筛选行容器')
    ok('function renderMallFilters' in wjs, 'C 端筛选渲染函数')

    import re
    def ver(asset, html):
        m = re.search(re.escape(asset) + r'\?v=([0-9A-Za-z]+)', html)
        return m.group(1) if m else None
    ok(ver('/static/platform/platform.js', idx) == ver('/static/ux/workflows-platform-more.js', idx),
       '同一批平台脚本版本号一致')
    ok(ver('/static/platform/platform.js', idx) == ver('/static/platform/platform.css', idx), 'platform.css 同批')
    ok(ver('/static/ux/workflows-platform.js', idx) == ver('/static/platform/platform.js', idx),
       'workflows-platform.js 同批（本次改过它）')
    widx = (ROOT / 'static/web/index.html').read_text(encoding='utf-8')
    ok(ver('/static/web/web.js', widx) == ver('/static/web/web-biz.js', widx), 'C 端两脚本同批')

    print('\n' + ('ALL PASS' if not FAILS else 'FAILED: %s' % FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
