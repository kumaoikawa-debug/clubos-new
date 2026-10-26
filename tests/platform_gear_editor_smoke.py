# -*- coding: utf-8 -*-
"""平台端商品编辑器三件改造（2026-10-26 用户反馈）回归。

① 品牌管理 Logo 本地上传：POST /api/platform/uploads/image 先拿 URL，
   再随品牌 {name,logoUrl} 落库（新品牌没有 id，不能「建完再传」）。
② 商品「最高佣金率」：products.commission_rate 语义升级为俱乐部专属费率的
   上限 —— resolve() 一律 min(专属费率, 商品上限)，谈判价可以定得高，
   成交按商品上限封顶；仓库（warehouse）随商品创建/更新落库。
③ 期初库存自动汇总：skuStock {颜色:{尺码:数量}} 逐格落库，
   products.stock = 全部数量之和；没传 skuStock 保持旧口径（压第一个组合）。
   追加矩阵（variants/matrix）同样支持 skuStock，已存在组合跳过不受影响。

自带临时库隔离，不碰项目真库；TEST_OPEN 模式直连平台 API。
"""
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

TMP = Path(tempfile.mkdtemp(prefix='clubos-gear-editor-smoke-'))
os.environ['CLUBOS_DB_PATH'] = str(TMP / 'clubos.db')

import db  # noqa: E402
db.init_db()
import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

UPLOAD_TMP = Path(TMP.name) / 'uploads'
UPLOAD_TMP.mkdir(parents=True, exist_ok=True)
app.UPLOAD = UPLOAD_TMP

c = TestClient(app.app)
PASS = []


def check(name, cond, detail=''):
    assert cond, f'FAIL {name} {detail}'
    PASS.append(name)
    print('  ok -', name)


PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


def main():
    # ── ① 品牌 Logo 上传 ────────────────────────────────────────────────────
    r = c.post('/api/platform/uploads/image', files={'file': ('logo.png', io.BytesIO(PNG), 'image/png')})
    check('通用图片上传返回 URL', r.status_code == 200 and r.json()['url'].startswith('/static/uploads/platform/'), r.text[:120])
    logo_url = r.json()['url']
    # 测试里 UPLOAD 被指到临时目录，落盘结果直接按相对路径读文件验证
    rel = logo_url.replace('/static/uploads/', '')
    f = Path(app.UPLOAD) / rel
    check('上传图已落盘可读', f.exists() and f.read_bytes() == PNG, str(f))
    r = c.post('/api/platform/uploads/image', files={'file': ('bad.txt', io.BytesIO(b'x'), 'text/plain')})
    check('非图片被拒', r.status_code == 400)
    r = c.post('/api/platform/brands', json={'name': 'Beta 测试牌', 'logoUrl': logo_url})
    check('新品牌带 Logo URL 创建', r.status_code == 200, r.text[:150])
    bid = r.json()['id']
    brands = c.get('/api/platform/brands').json()
    check('品牌列表 Logo 落库', any(x['id'] == bid and x['logo_url'] == logo_url for x in brands))
    r = c.patch(f'/api/platform/brands/{bid}', json={'name': 'Beta 测试牌', 'logoUrl': ''})
    check('编辑品牌可移除 Logo', r.status_code == 200
          and all(x['id'] != bid or not x['logo_url'] for x in c.get('/api/platform/brands').json()))

    # ── ② skuStock 逐格期初库存 + 仓库 ─────────────────────────────────────
    r = c.post('/api/platform/products', json={
        'name': 'Beta LT 冲锋衣', 'price': 1999, 'sku': 'GEAR-SMK-01',
        'warehouse': '成都主仓', 'commission_rate': 0.08,
        'colors': [{'name': '黑色', 'hex': '#2B2B2B'}, {'name': '黄色', 'hex': '#FFCC00'}],
        'sizes': ['S', 'M', 'XL'],
        'skuStock': {'黑色': {'S': 5, 'M': 5, 'XL': 0}, '黄色': {'XL': 5}}})
    check('带 skuStock 建商品成功', r.status_code == 200, r.text[:200])
    pid = r.json()['id']
    vs = c.get(f'/api/platform/products/{pid}/variants').json()
    stock_map = {(v['color'] or '', v['size'] or ''): v['stock'] for v in vs}
    check('黑色 S=5 M=5 XL=0 逐格落库', stock_map[('黑色', 'S')] == 5 and stock_map[('黑色', 'M')] == 5 and stock_map[('黑色', 'XL')] == 0,
          str(stock_map))
    check('黄色 XL=5（黄色 S/M 为 0）', stock_map[('黄色', 'XL')] == 5 and stock_map[('黄色', 'S')] == 0)
    prods = c.get('/api/platform/products').json()
    p1 = [x for x in prods if x['id'] == pid][0]
    check('products.stock=15 自动汇总（5+5+0+5）', p1['stock'] == 15, str(p1['stock']))
    check('仓库落库', p1.get('warehouse') == '成都主仓')
    r = c.patch(f'/api/platform/products/{pid}', json={'warehouse': '双流三号仓'})
    p1 = [x for x in c.get('/api/platform/products').json() if x['id'] == pid][0]
    check('PATCH 可改仓库', r.status_code == 200 and p1['warehouse'] == '双流三号仓')

    # 无 skuStock 的旧客户端：库存仍压第一个组合（兼容 CSV 导入路径）
    r = c.post('/api/platform/products', json={
        'name': '旧口径商品', 'price': 99, 'sku': 'GEAR-SMK-02',
        'colors': [{'name': '灰'}], 'sizes': ['M'], 'stock': 7})
    pid2 = r.json()['id']
    vs2 = c.get(f'/api/platform/products/{pid2}/variants').json()
    check('无 skuStock 保持旧口径（第一个组合承 7）', len(vs2) == 1 and vs2[0]['stock'] == 7, str([(v['name'], v['stock']) for v in vs2]))

    # ── ③ 追加矩阵支持 skuStock，已存在组合跳过 ────────────────────────────
    r = c.post(f'/api/platform/products/{pid}/variants/matrix',
               json={'colors': [{'name': '黑色'}], 'sizes': ['L'], 'skuStock': {'黑色': {'L': 6}}})
    check('追加矩阵 L=6 成功', r.status_code == 200 and r.json()['created'] == 1, r.text[:150])
    vs = c.get(f'/api/platform/products/{pid}/variants').json()
    l6 = [v for v in vs if v['color'] == '黑色' and v['size'] == 'L']
    check('新组合 L 库存=6', len(l6) == 1 and l6[0]['stock'] == 6)
    p1 = [x for x in c.get('/api/platform/products').json() if x['id'] == pid][0]
    check('汇总同步为 21（15+6）', p1['stock'] == 21, str(p1['stock']))
    r = c.post(f'/api/platform/products/{pid}/variants/matrix',
               json={'colors': [{'name': '黑色'}], 'sizes': ['L'], 'skuStock': {'黑色': {'L': 99}}})
    vs = c.get(f'/api/platform/products/{pid}/variants').json()
    l6 = [v for v in vs if v['color'] == '黑色' and v['size'] == 'L'][0]
    check('已存在组合跳过不被重置', r.json()['skipped'] == 1 and l6['stock'] == 6)

    # ── ④ 佣金率封顶：商品最高佣金率是俱乐部专属费率的上限 ────────────────────
    with db.conn() as cc:
        cc.execute("INSERT INTO clubs(name,status,plan) VALUES('封顶测试俱乐部','active','pro')")
        cc.execute("UPDATE products SET commission_rate=0.08 WHERE id=?", (pid,))
    with db.conn() as cc:
        club_id = cc.execute("SELECT id FROM clubs WHERE name='封顶测试俱乐部'").fetchone()[0]
    # 谈判价 20% > 商品上限 8% → 生效一律 8%
    r = c.patch(f'/api/platform/commission-rates/{club_id}', json={'ratePercent': 20, 'enabled': True})
    check('设 20% 被接受（谈判结果可高）', r.status_code == 200, r.text[:150])
    d = r.json()
    check('生效比例封顶到商品上限 8%', abs(d['rate'] - 0.08) < 1e-9 and d['source'] == 'club_override', str(d))
    check('封顶原因写明', '封顶' in (d.get('reason') or ''))
    check('单独设定值保留 20% 便于追溯', abs(d.get('clubRate', 0) - 0.20) < 1e-9)
    # 谈判价 5% < 商品上限 → 不封顶
    r = c.patch(f'/api/platform/commission-rates/{club_id}', json={'ratePercent': 5, 'enabled': True})
    d = r.json()
    check('低于上限按谈判价 5% 生效', abs(d['rate'] - 0.05) < 1e-9 and '封顶' not in (d.get('reason') or ''), str(d))
    c.patch(f'/api/platform/commission-rates/{club_id}', json={'ratePercent': 20, 'enabled': True})
    with db.conn() as cc:
        eff = app.commission_rate_service.rate_for_product(cc, club_id, 0.08)
    check('rate_for_product 下单口径同样封顶', abs(eff - 0.08) < 1e-9, str(eff))

    # ── ⑤ 前端护栏 ─────────────────────────────────────────────────────────
    html = (ROOT / 'static/platform/index.html').read_text(encoding='utf-8')
    import re
    vers = set(re.findall(r'v=(20261027a)', html))
    check('平台端脚本同批 20261027a', len(vers) == 1 and 'platform.js?v=20261027a' in html
          and 'product-editor.js?v=20261027a' in html and 'workflows-platform-more.js?v=20261027a' in html)
    pe = (ROOT / 'static/platform/product-editor.js').read_text(encoding='utf-8')
    check('编辑器：最高佣金率语义', '最高佣金率' in pe and '不能超过此值' in pe)
    check('编辑器：仓库字段', 'peWarehouse' in pe and '存在哪个仓' in pe)
    check('编辑器：期初库存只读自动汇总', "id=\"peStock\" type=\"number\" min=\"0\" step=\"1\" value=\"${isEdit ? (prod.stock || 0) : 0}\" readonly" in pe
          and 'paintQtyGrid' in pe and 'updateStockTotal' in pe)
    check('编辑器：skuStock 随保存提交', 'skuStock' in pe and 'data-cid' in pe)
    check('编辑器：库存数量网格输入框', 'plat-pe__qtyin' in pe and 'plat-pe__qtygrid' in pe)
    pf = (ROOT / 'static/platform/platform.js').read_text(encoding='utf-8')
    check('品牌弹窗 Logo 走上传', 'brandFormDialog' in pf and '/api/platform/uploads/image' in pf
          and pf.count("'Logo 地址'") == 0)
    css = (ROOT / 'static/platform/platform.css').read_text(encoding='utf-8')
    check('数量网格样式 plat- 前缀在 platform.css', '.plat-pe__qtygrid' in css and '.plat-pe__qtyrow' in css)
    wf = (ROOT / 'static/ux/workflows-platform-more.js').read_text(encoding='utf-8')
    check('佣金率弹窗提示封顶规则', '最高佣金率' in wf and '封顶' in wf)

    print(f'\nALL PASS ({len(PASS)} checks)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
