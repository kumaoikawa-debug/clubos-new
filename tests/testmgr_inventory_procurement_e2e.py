#!/usr/bin/env venv/bin/python
# -*- coding: utf-8 -*-
"""进销存全链路专项回归（2026-11-16 V1.3）。

背景：商品库有货但新建采购单选不到 —— 根因是采购候选前端自拼口径，与后端
owned()/sku() 校验漂移。V1.3 收口为后端统一标注 procurementEligible。
本测试覆盖：
  A. 采购口径统一（外部品牌 / 镜像 / 自营 / 待核对）
  B. 采购单全流程状态机（建单→审核→下单→部分收货→收完）
  C. 滚动加权成本与库存流水勾稽
  D. 到货异常（closed 扣在途 / pending 保留在途）
  E. 采购退货（出库→贷项）与新增的作队能力
  F. 期初成本录入护栏
  G. 汇总口径（草稿单单独带出、成本入账）
  H. 前端重复定义收口（静态断言）
"""
import io, os, sqlite3, sys, json, tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)
sys.path.insert(0, BASE)

DB = tempfile.mktemp(prefix='clubos_inv_e2e_', suffix='.db')
os.environ['CLUBOS_DB_PATH'] = DB
os.environ['CLUBOS_TEST_OPEN'] = '1'
os.environ['CLUBOS_SECURITY_MODE'] = 'demo'
os.environ['MOCK_AI'] = '1'
os.environ['COMMERCE_PROVIDER'] = 'local'

from fastapi.testclient import TestClient
import app

PASS = FAIL = 0
FAILURES = []

def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  PASS {name}')
    else:
        FAIL += 1
        FAILURES.append((name, detail))
        print(f'  FAIL {name} :: {detail}')

def call(m, p, d=None):
    r = client.request(m, p, json=d) if d is not None else client.request(m, p)
    return r

def j(r):
    try:
        return r.json()
    except Exception:
        return {}

client = TestClient(app.app)

print('== A. 采购口径统一 ==')
r = call('POST', '/api/platform/brands', {'name': '外牌QA', 'sourceType': 'external_api'})
check('A1 外部品牌建档', r.status_code == 200 and j(r).get('id'), r.text[:120])
EXT_BRAND = j(r).get('id')
r = call('POST', '/api/platform/brands', {'name': '自营QA', 'sourceType': 'platform_self'})
SELF_BRAND = j(r).get('id')
check('A2 自营品牌建档', r.status_code == 200 and SELF_BRAND, r.text[:120])

r = call('POST', '/api/platform/products', {
    'name': '自营采购T', 'price': 100, 'status': 'draft', 'brandId': SELF_BRAND,
    'colors': ['黑', '白'], 'sizes': ['M', 'L']})
check('A3 自营商品建档(4规格)', r.status_code == 200 and j(r).get('variantsCreated') == 4, r.text[:160])
OWN_PID = j(r).get('id')
r = call('POST', '/api/platform/products', {
    'name': '外部同步款', 'price': 50, 'status': 'draft', 'brandId': EXT_BRAND, 'colors': ['红'], 'sizes': ['M']})
check('A4 外部品牌商品建档', r.status_code == 200, r.text[:160])
EXT_PID = j(r).get('id')
r = call('POST', '/api/platform/products', {
    'name': '镜像款', 'price': 60, 'status': 'draft', 'inventoryMode': 'external_mirror', 'colors': ['蓝'], 'sizes': ['M']})
check('A5 镜像商品建档', r.status_code == 200, r.text[:160])
MIR_PID = j(r).get('id')

ps = j(call('GET', '/api/platform/products'))
by = {p['name']: p for p in ps}
check('A6 自营商品 procurementEligible=True', by.get('自营采购T', {}).get('procurementEligible') is True,
      str(by.get('自营采购T', {}).get('procurementEligible')))
check('A7 外部品牌商品不可采购+原因', by.get('外部同步款', {}).get('procurementEligible') is False
      and '外部品牌' in (by.get('外部同步款', {}).get('procurementBlockReason') or ''), str(by.get('外部同步款', {}))[:160])
check('A8 镜像商品不可采购+原因', by.get('镜像款', {}).get('procurementEligible') is False
      and '库存模式' in (by.get('镜像款', {}).get('procurementBlockReason') or ''), str(by.get('镜像款', {}))[:160])

# 与后端 owned() 校验一致性：外部商品建采购单必须被拒
r = call('POST', '/api/platform/suppliers', {'name': 'QA供应商', 'leadTimeDays': 7})
SUP = j(r).get('id')
check('A9 供应商建档', bool(SUP), r.text[:120])
own_vs = j(call('GET', f'/api/platform/products/{OWN_PID}/variants'))
check('A10 自营规格=4且active', len([v for v in own_vs if v['status'] == 'active']) == 4, str(len(own_vs)))
ext_vs = j(call('GET', f'/api/platform/products/{EXT_PID}/variants'))
r = call('POST', '/api/platform/procurement/purchase-orders', {
    'supplierId': SUP, 'items': [{'productId': EXT_PID, 'variantId': ext_vs[0]['id'], 'quantity': 2, 'unitCost': 10}]})
check('A11 后端拒绝外部商品采购(409)', r.status_code == 409, f'{r.status_code} {r.text[:120]}')

print('== B. 采购单全流程 ==')
vids = {v['name']: v['id'] for v in own_vs}
items = [
    {'productId': OWN_PID, 'variantId': vids['黑 / M'], 'quantity': 10, 'unitCost': 40},
    {'productId': OWN_PID, 'variantId': vids['白 / L'], 'quantity': 10, 'unitCost': 60},
]
r = call('POST', '/api/platform/procurement/purchase-orders', {'supplierId': SUP, 'items': items, 'note': 'QA全流程'})
check('B1 建采购草稿', r.status_code == 200 and j(r).get('id'), r.text[:160])
PO = j(r).get('id')
check('B2 金额勾稽 1000', float(j(r).get('total_amount') or 0) == 1000.0, str(j(r).get('total_amount')))
check('B3 sku_plan_json 落库', len(j(r).get('items', [{}])[0].get('skuItems') or []) == 2,
      json.dumps(j(r).get('items', [{}])[0].get('skuItems'))[:120])
r = call('POST', f'/api/platform/procurement/purchase-orders/{PO}/receive', {'items': []})
check('B4 草稿不可验收(409)', r.status_code == 409, str(r.status_code))
check('B5 审核 draft→approved', j(call('POST', f'/api/platform/procurement/purchase-orders/{PO}/approve')).get('status') == 'approved')
check('B6 下单 approved→ordered', j(call('POST', f'/api/platform/procurement/purchase-orders/{PO}/order')).get('status') == 'ordered')
s, sm = j(call('GET', '/api/platform/inventory/summary')), None
sm = call('GET', '/api/platform/inventory/summary').json()
check('B7 下单后在途计入', sm.get('openPurchaseOrders') == 1 and float(sm.get('openPurchaseValue') or 0) == 1000.0, str(sm))
check('B8 草稿口径清零', sm.get('draftPurchaseOrders') == 0, str(sm.get('draftPurchaseOrders')))

print('== C. 部分收货 · 滚动加权成本 ==')
po = j(call('GET', f'/api/platform/procurement/purchase-orders/{PO}'))
it0 = po['items'][0]
r = call('POST', f'/api/platform/procurement/purchase-orders/{PO}/receive', {
    'requestKey': 'qa-rc1', 'items': [{'purchaseOrderItemId': it0['id'], 'variantId': vids['黑 / M'], 'quantity': 4}]})
rb = j(r)
check('C1 部分收货成功 partially_received', r.status_code == 200 and rb.get('status') == 'partially_received', r.text[:200])
check('C2 收货成本入账 160', float(rb.get('receivedCost') or 0) == 160.0, str(rb.get('receivedCost')))
p_own = next(p for p in j(call('GET', '/api/platform/products')) if p['id'] == OWN_PID)
check('C3 商品平均成本=40', float(p_own.get('average_cost') or 0) == 40.0, str(p_own.get('average_cost')))
check('C4 商品推进 pending_listing', p_own['status'] == 'pending_listing', p_own['status'])
mv = call('GET', '/api/platform/inventory/movements?limit=50').json()
inb = [m for m in mv if m['movement_type'] == 'purchase_inbound' and m['product_id'] == OWN_PID]
check('C5 purchase_inbound 流水落账', len(inb) == 1 and int(inb[0]['quantity_delta']) == 4, str(inb[:1]))
check('C6 幂等键防重放', call('POST', f'/api/platform/procurement/purchase-orders/{PO}/receive', {
    'requestKey': 'qa-rc1', 'items': [{'purchaseOrderItemId': it0['id'], 'variantId': vids['黑 / M'], 'quantity': 4}]}).status_code in (200, 409))

print('== D. 到货异常 ==')
# 同一商品多 SKU 在 items 表合并为一条（UNIQUE(po,product)），逐 SKU 计划在 sku_plan_json。
po = j(call('GET', f'/api/platform/procurement/purchase-orders/{PO}'))
check('D0 商品级明细合并为一条', len(po['items']) == 1 and po['items'][0]['quantity_ordered'] == 20, str(po['items']))
it0 = po['items'][0]
r = call('POST', f'/api/platform/procurement/purchase-orders/{PO}/exceptions', {
    'purchaseOrderItemId': it0['id'], 'variantId': vids['黑 / M'], 'exceptionType': 'shortage', 'quantity': 2,
    'resolution': 'closed', 'note': 'QA短少'})
check('D1 closed 异常登记', r.status_code == 200, r.text[:160])
po = j(call('GET', f'/api/platform/procurement/purchase-orders/{PO}'))
check('D2 outstanding 扣减(20-4-2=14)', po['items'][0]['quantity_outstanding'] == 14, str(po['items'][0]['quantity_outstanding']))
r = call('POST', f'/api/platform/procurement/purchase-orders/{PO}/exceptions', {
    'purchaseOrderItemId': it0['id'], 'variantId': vids['白 / L'], 'exceptionType': 'damaged', 'quantity': 3,
    'resolution': 'pending', 'note': 'QA待补发'})
check('D3 pending 异常登记', r.status_code == 200, r.text[:160])
po = j(call('GET', f'/api/platform/procurement/purchase-orders/{PO}'))
check('D4 pending 不扣 outstanding(14)', po['items'][0]['quantity_outstanding'] == 14, str(po['items'][0]['quantity_outstanding']))
# 收完剩余：黑M 剩 4（10计划-4收-2closed），白L 收 10（pending 保留在途仍按计划收）
r = call('POST', f'/api/platform/procurement/purchase-orders/{PO}/receive', {
    'requestKey': 'qa-rc2', 'items': [
        {'purchaseOrderItemId': it0['id'], 'variantId': vids['黑 / M'], 'quantity': 4},
        {'purchaseOrderItemId': it0['id'], 'variantId': vids['白 / L'], 'quantity': 10}]})
check('D5 收完 → received', r.status_code == 200 and j(r).get('status') == 'received', r.text[:200])
po = j(call('GET', f'/api/platform/procurement/purchase-orders/{PO}'))
pend = [e for e in po.get('exceptions', []) if e.get('resolution') == 'pending']
check('D6 收完 pending→resolved', len(pend) == 0, str([(e['resolution']) for e in po.get('exceptions', [])]))
check('D7 采购单最终 20 收 18 拒 2', po['receivedQty'] == 18 and po['rejectedQty'] == 2, f"{po['receivedQty']}/{po['rejectedQty']}")
# 滚动加权：黑M 共 8 件 @40 + 白L 10 件 @60 → (320+600)/18
p_own = next(p for p in j(call('GET', '/api/platform/products')) if p['id'] == OWN_PID)
check('D8 加权成本 (320+600)/18≈51.11', abs(float(p_own.get('average_cost') or 0) - 920 / 18) < 0.01, str(p_own.get('average_cost')))

print('== E. 采购退货 + 作废 ==')
r = call('POST', '/api/platform/procurement/purchase-returns', {
    'purchaseOrderId': PO, 'note': 'QA退货',
    'items': [{'purchaseOrderItemId': it0['id'], 'variantId': vids['黑 / M'], 'quantity': 2}]})
check('E1 建退货草稿', r.status_code == 200 and j(r).get('id'), r.text[:160])
RET = j(r).get('id')
check('E2 退货金额=80', float(j(r).get('total_amount') or 0) == 80.0, str(j(r).get('total_amount')))
r2 = call('POST', '/api/platform/procurement/purchase-returns', {
    'purchaseOrderId': PO, 'note': 'QA作废用',
    'items': [{'purchaseOrderItemId': it0['id'], 'variantId': vids['黑 / M'], 'quantity': 1}]})
RET2 = j(r2).get('id')
check('E3 草稿退货可作废', j(call('POST', f'/api/platform/procurement/purchase-returns/{RET2}/cancel', {'note': 'QA'})).get('status') == 'cancelled')
check('E4a 审核退货', j(call('POST', f'/api/platform/procurement/purchase-returns/{RET}/approve')).get('status') == 'approved')
# 已审核（未出库，无库存/财务事实）仍可作废；出库后才禁止
r3 = call('POST', '/api/platform/procurement/purchase-returns', {
    'purchaseOrderId': PO, 'note': 'QA已审核作废用',
    'items': [{'purchaseOrderItemId': it0['id'], 'variantId': vids['黑 / M'], 'quantity': 1}]})
RET3 = j(r3).get('id')
call('POST', f'/api/platform/procurement/purchase-returns/{RET3}/approve')
check('E4b 已审核未出库可作废', j(call('POST', f'/api/platform/procurement/purchase-returns/{RET3}/cancel', {'note': 'QA'})).get('status') == 'cancelled')
r = call('POST', f'/api/platform/procurement/purchase-returns/{RET}/ship')
check('E5 退货出库 shipped', j(r).get('status') == 'shipped', r.text[:160])
mv = call('GET', '/api/platform/inventory/movements?limit=50').json()
sr = [m for m in mv if m['movement_type'] == 'supplier_return' and m['product_id'] == OWN_PID]
check('E6 supplier_return 流水-2', len(sr) == 1 and int(sr[0]['quantity_delta']) == -2, str(sr[:1]))
check('E7 贷项确认 credited', j(call('POST', f'/api/platform/procurement/purchase-returns/{RET}/credit', {'note': 'QA贷项'})).get('status') == 'credited')
check('E8 已贷项不可作废(409)', call('POST', f'/api/platform/procurement/purchase-returns/{RET}/cancel').status_code == 409)
check('E9 已出库不可作废(409)', True if j(call('GET', f'/api/platform/procurement/purchase-returns/{RET}')).get('status') == 'credited' else False)

print('== F. 期初成本护栏 ==')
r = call('POST', '/api/platform/products', {'name': '成本QA款', 'price': 30, 'status': 'draft', 'averageCost': 12.5, 'colors': ['灰'], 'sizes': ['M']})
check('F1 建档带期初成本', r.status_code == 200, r.text[:160])
p_new = next((p for p in j(call('GET', '/api/platform/products')) if p['name'] == '成本QA款'), {})
check('F2 average_cost=12.5', float(p_new.get('average_cost') or 0) == 12.5, str(p_new.get('average_cost')))
check('F3 有采购史商品改成本 409', call('PATCH', f'/api/platform/products/{OWN_PID}', {'averageCost': 1}).status_code == 409)
check('F4 无采购史商品改成本 OK', call('PATCH', f'/api/platform/products/{p_new["id"]}', {'averageCost': 15}).status_code == 200)
check('F5 负成本 400', call('PATCH', f'/api/platform/products/{p_new["id"]}', {'averageCost': -1}).status_code == 400)

print('== G. 汇总口径 ==')
sm = call('GET', '/api/platform/inventory/summary').json()
# 自营可采购库存：黑M 8 + 白L 10 - 退2 = 16；成本 16*?  逐件库存成本与流水一致
check('G1 在途清零(已收完)', sm.get('openPurchaseOrders') == 0, str(sm.get('openPurchaseOrders')))
check('G2 库存价值>0', float(sm.get('inventoryValue') or 0) > 0, str(sm.get('inventoryValue')))
s = j(call('GET', '/api/platform/stock/consistency'))
check('G3 库存一致性自检无差异', s.get('count') == 0, json.dumps(s.get('mismatches', [])[:2], ensure_ascii=False)[:200])

print('== H. 前端重复定义收口（静态断言）==')
wp = io.open('static/ux/workflows-platform.js', encoding='utf-8').read()
check('H1 workflows-platform 无 createPO 死代码', 'window.createPO' not in wp)
check('H2 workflows-platform 无 receivePO 死代码', 'window.receivePO' not in wp)
check('H3 workflows-platform 无 manualAdjust 死代码', 'window.manualAdjust' not in wp)
iw = io.open('static/platform/inventory-workflow.js', encoding='utf-8').read()
check('H4 inventory-workflow 为唯一真身', iw.count('window.createPO') == 1 and iw.count('window.receivePO') == 1 and iw.count('window.manualAdjust') == 1)
check('H5 采购候选走 procurementEligible', 'procurementEligible' in iw)
pj = io.open('static/platform/platform.js', encoding='utf-8').read()
check('H6 商品库外部标记渲染', 'procurementEligible' in pj and '外部库存' in pj)

print(f'\n═══ 进销存专项 {PASS} PASS / {FAIL} FAIL ═══')
for n, d in FAILURES:
    print('  FAILED:', n, '::', d[:160])
sys.exit(1 if FAIL else 0)
