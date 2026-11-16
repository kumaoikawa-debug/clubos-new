# 采购单审核/确认决策专项 E2E：同意备注 / 不同意退回原因 / 确认下单备注 / 驳回原因 / 重新提交
# 前提：8262 QA 实例（TEST_OPEN + /tmp/clubos_full_ui.db）
import json, sys, urllib.request, urllib.parse
from playwright.sync_api import sync_playwright

BASE='http://127.0.0.1:8262'
results=[]; fails=[]
def rec(name,ok,detail=''):
    results.append((name,bool(ok)))
    if not ok: fails.append(f'{name} | {detail}')
    print(('PASS ' if ok else 'FAIL ')+name+((' | '+str(detail)[:140]) if detail else ''))

def call(path,method='GET',body=None):
    data=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(BASE+urllib.parse.quote(path,safe='/?:=&'),data=data,method=method)
    if data is not None: req.add_header('Content-Type','application/json')
    try:
        with urllib.request.urlopen(req,timeout=15) as r: return r.status,json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code,json.loads(e.read().decode() or '{}')

def new_po(suffix='A'):
    _,sup=call('/api/platform/suppliers'); sid=(sup[0]['id'] if isinstance(sup,list) and sup else 1)
    _,prods=call('/api/platform/products'); prod=(prods[0] if isinstance(prods,list) and prods else None)
    if not prod: raise RuntimeError('no product for PO fixture')
    pid=prod['id']
    _,vars_=call(f'/api/platform/products/{pid}/variants')
    vid=(vars_[0]['id'] if isinstance(vars_,list) and vars_ else None)
    items=[{'productId':pid,'quantity':2,'unitCost':10,'skuItems':[{'variantId':vid,'quantity':2,'unitCost':10}] if vid else []}]
    st,po=call('/api/platform/procurement/purchase-orders','POST',{'supplierId':sid,'items':items,'note':'E2E '+suffix})
    assert st==200, (st,po)
    return po['id']

# ── A. API 状态机 ──
po1=new_po('退回')
st,d=call(f'/api/platform/procurement/purchase-orders/{po1}/reject','POST',{'stage':'audit','reason':'预算超标，退回调整'})
rec('A1 审核退回 draft→rejected',st==200 and d.get('status')=='rejected' and d.get('reject_reason')=='预算超标，退回调整',str(d)[:120])
rec('A2 退回原因持久化',d.get('rejected_by') and d.get('rejected_at'),str(d)[:120])
st,e=call(f'/api/platform/procurement/purchase-orders/{po1}/reject','POST',{'stage':'audit','reason':'again'})
rec('A3 重复退回幂等',st==200 and d.get('status')=='rejected')
st,e=call(f'/api/platform/procurement/purchase-orders/{po1}/approve','POST',{})
rec('A4 rejected 不可审核',st==409,str(e)[:100])
st,e=call(f'/api/platform/procurement/purchase-orders/{po1}/reject','POST',{'stage':'audit','reason':'   '})
rec('A5 空原因被拒绝',st==400,str(e)[:100])
st,e=call(f'/api/platform/procurement/purchase-orders/{po1}/reject','POST',{'stage':'whatever','reason':'x'})
rec('A6 非法阶段被拒绝',st==400,str(e)[:100])
st,e=call(f'/api/platform/procurement/purchase-orders/{po1}/resubmit','POST',{'note':'已调整预算'})
rec('A7 重新提交 rejected→draft',st==200 and e.get('status')=='draft',str(e)[:120])
st,e=call(f'/api/platform/procurement/purchase-orders/{po1}/approve','POST',{'note':'预算已核对'})
rec('A8 同意可附备注',st==200 and e.get('approve_note')=='预算已核对',str(e)[:120])

po2=new_po('驳回')
st,d=call(f'/api/platform/procurement/purchase-orders/{po2}/approve','POST',{'note':'同意采购'})
rec('A9 先审核通过',st==200 and d.get('status')=='approved')
st,d=call(f'/api/platform/procurement/purchase-orders/{po2}/reject','POST',{'stage':'order','reason':'MOQ 不满足，供应商确认驳'})
rec('A10 下单驳回 approved→rejected',st==200 and d.get('status')=='rejected' and d.get('reject_reason','').startswith('MOQ'),str(d)[:120])
st,e=call(f'/api/platform/procurement/purchase-orders/{po2}/order','POST',{})
rec('A11 驳回后不可下单',st==409,str(e)[:100])
st,e=call(f'/api/platform/procurement/purchase-orders/{po2}/reject','POST',{'stage':'audit','reason':'x'})
rec('A12 已驳回单重复退回幂等',st==200 and e.get('status')=='rejected',str(e)[:100])
po2b=new_po('阶段')
call(f'/api/platform/procurement/purchase-orders/{po2b}/approve','POST',{})
call(f'/api/platform/procurement/purchase-orders/{po2b}/reject','POST',{'stage':'order','reason':'订单阶段驳回'})
st,e=call(f'/api/platform/procurement/purchase-orders/{po2b}/reject','POST',{'stage':'order','reason':'x'})
rec('A12b 重复驳回幂等',st==200 and e.get('status')=='rejected')

po3=new_po('确认')
call(f'/api/platform/procurement/purchase-orders/{po3}/approve','POST',{})
st,d=call(f'/api/platform/procurement/purchase-orders/{po3}/order','POST',{'note':'供应商确认交期 11/25'})
rec('A13 确认下单附备注',st==200 and d.get('status')=='ordered' and d.get('order_note')=='供应商确认交期 11/25',str(d)[:120])
st,e=call(f'/api/platform/procurement/purchase-orders/{po3}/reject','POST',{'stage':'order','reason':'已下单不可驳回'})
rec('A14 已下单不可驳回',st==409,str(e)[:100])

# ── B. UI 弹窗交互（真实按钮 + 原因校验 + 列表回显） ──
with sync_playwright() as p:
    b=p.chromium.launch(channel='chrome', headless=True); pg=b.new_page(viewport={'width':1440,'height':900})
    errors=[]; pg.on('pageerror',lambda e: errors.append(str(e)))
    pg.goto(BASE+'/platform?club_id=1',wait_until='domcontentloaded',timeout=30000)
    pg.wait_for_timeout(1400)
    pg.evaluate("()=>document.querySelector('.nav button[data-view=\"supp-po\"]').click()")
    pg.wait_for_timeout(1300)

    # B1 审核弹窗双按钮
    po4=new_po('UI审核')
    pg.click(f'button[onclick="approvePO(\'{po4}\')"]') if pg.locator(f'button[onclick="approvePO(\'{po4}\')"]').count() else pg.evaluate(f"()=>approvePO('{po4}')")
    pg.wait_for_timeout(900)
    rec('B1 审核弹窗含同意/退回双按钮',pg.locator('.ux-dialog [data-submit]').count()==1 and pg.locator('.ux-dialog [data-reject]').count()==1)
    rec('B2 审核弹窗含备注/原因输入',pg.locator('.ux-dialog #poNote').count()==1 and pg.locator('.ux-dialog #poReason').count()==1)
    # 空原因点退回 → 不提交
    pg.click('.ux-dialog [data-reject]'); pg.wait_for_timeout(400)
    st,d=call(f'/api/platform/procurement/purchase-orders/{po4}')
    rec('B3 空原因点击退回不生效',d.get('status')=='draft')
    pg.fill('.ux-dialog #poReason','UI 测试：价格过高退回')
    pg.click('.ux-dialog [data-reject]'); pg.wait_for_timeout(1100)
    st,d=call(f'/api/platform/procurement/purchase-orders/{po4}')
    rec('B4 退回原因落库',d.get('status')=='rejected' and d.get('reject_reason')=='UI 测试：价格过高退回',str(d)[:120])
    body=pg.evaluate("()=>document.querySelector('#purchaseOrderList').innerText")
    rec('B5 列表显示已退回+原因',po4 in body and ('已退回' in body or '驳回' in body) and '价格过高' in body,body[:140])

    # B6 重新提交按钮 → 草稿 → 同意备注
    pg.click(f'button[onclick="resubmitPO(\'{po4}\')"]')
    pg.wait_for_timeout(700)
    if pg.locator('.ux-dialog textarea').count():
        pg.fill('.ux-dialog textarea','已重新议价')
        pg.click('.ux-dialog button[type="submit"]')
        pg.wait_for_timeout(900)
    st,d=call(f'/api/platform/procurement/purchase-orders/{po4}')
    rec('B6 UI 重新提交回到草稿',d.get('status')=='draft',str(d)[:100])

    pg.evaluate(f"()=>approvePO('{po4}')"); pg.wait_for_timeout(800)
    pg.fill('.ux-dialog #poNote','UI 同意备注')
    pg.click('.ux-dialog [data-submit]'); pg.wait_for_timeout(1100)
    st,d=call(f'/api/platform/procurement/purchase-orders/{po4}')
    rec('B7 UI 同意附备注',d.get('status')=='approved' and d.get('approve_note')=='UI 同意备注',str(d)[:120])

    # B8 下单确认弹窗：确认附备注
    pg.evaluate(f"()=>orderPO('{po4}')"); pg.wait_for_timeout(900)
    rec('B8 下单弹窗含确认/驳回双按钮',pg.locator('.ux-dialog [data-submit]').count()==1 and pg.locator('.ux-dialog [data-reject]').count()==1)
    pg.fill('.ux-dialog #poNote','UI 确认交期')
    pg.click('.ux-dialog [data-submit]'); pg.wait_for_timeout(1100)
    st,d=call(f'/api/platform/procurement/purchase-orders/{po4}')
    rec('B9 UI 确认下单附备注',d.get('status')=='ordered' and d.get('order_note')=='UI 确认交期',str(d)[:120])

    # B10 下单驳回 UI（另建一单）
    po5=new_po('UI驳回')
    call(f'/api/platform/procurement/purchase-orders/{po5}/approve','POST',{})
    pg.evaluate("()=>loadSupplyChain()"); pg.wait_for_timeout(800)
    pg.evaluate(f"()=>orderPO('{po5}')"); pg.wait_for_timeout(900)
    pg.fill('.ux-dialog #poReason','UI 测试：交期不满足驳回')
    pg.click('.ux-dialog [data-reject]'); pg.wait_for_timeout(1100)
    st,d=call(f'/api/platform/procurement/purchase-orders/{po5}')
    rec('B10 UI 下单驳回原因落库',d.get('status')=='rejected' and d.get('reject_reason')=='UI 测试：交期不满足驳回',str(d)[:120])

    rec('Z 无 JS 错误',not errors,'; '.join(errors[:3]))
    b.close()

print('\n==== %d 项 | 失败 %d ===='%(len(results),len(fails)))
for f in fails: print('FAILED:',f)
sys.exit(1 if fails else 0)
