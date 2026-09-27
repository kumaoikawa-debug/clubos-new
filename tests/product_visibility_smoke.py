"""回归测试：俱乐部提交的「装备上下架」申请必须真的改到 C 端商城的可见性。

背景：products 表没有 club 归属列，商品归总平台所有。所以俱乐部的「申请下架」不能
直接写 products.status —— 那等于让俱乐部绕过总平台的商品管理。真实链路是：
俱乐部提交申请 → 总平台批准 → 只有这一步才写 products.status → C 端商城可见性随之变化。
此测试用 FastAPI TestClient 锁住这条链条，任何一个环节写漏都会在这里红。
"""
import os, sys, tempfile
sys.path.insert(0, '.')

# 自我隔离：app.py 在模块级调 init_db()，任何 import app 的测试都会把迁移落到目标库。
# 必须在 import 之前把库路径指向临时文件，绝不碰项目真库 clubos.db。
os.environ['CLUBOS_DB_PATH'] = os.path.join(tempfile.mkdtemp(prefix='clubos-visibility-smoke-'), 'clubos.db')

from fastapi.testclient import TestClient  # noqa: E402

import db  # noqa: E402
db.init_db()
import app  # noqa: E402

CLUB = 1
FAILS = []


def ok(cond, label, extra=''):
    print(('  PASS  ' if cond else '  FAIL  ') + label + (f'   | {extra}' if extra else ''))
    if not cond:
        FAILS.append(label)


def main():
    c = TestClient(app.app)
    products = c.get(f'/api/club/{CLUB}/mall/products').json()
    target = next((p for p in products if p.get('status') == 'active'), None)
    if not target:
        print('  跳过：临时库里没有 active 商品'); return
    pid, name = target['id'], target['name']

    # 1) C 端商城里现在有它
    before = [p['name'] for p in c.get(f'/api/club/{CLUB}/mall/products').json()]
    ok(name in before, '初始状态：C 端商城能看到这件商品')

    # 2) 俱乐部提交下架申请（只落申请，不改 status）
    r = c.post(f'/api/club/{CLUB}/mall/products/{pid}/visibility',
               json={'action': 'off', 'note': '回归测试：春季调整'})
    ok(r.status_code == 200 and r.json().get('ok'), '俱乐部提交下架申请成功', r.text[:120])
    rid = r.json().get('requestId')
    mid = c.get(f'/api/club/{CLUB}/mall/products/{pid}/visibility').status_code
    still_active = next(p for p in c.get(f'/api/club/{CLUB}/mall/products').json() if p['id'] == pid)
    ok(still_active.get('status') == 'active', '申请落库后商品状态未被俱乐部改动（仍是 active）')

    # 3) 同一件商品不能重复挂待处理申请（否则总平台会处理两次）
    dup = c.post(f'/api/club/{CLUB}/mall/products/{pid}/visibility', json={'action': 'off', 'note': '再来一条'})
    ok(dup.status_code == 409, '重复申请被拦（409）', str(dup.status_code))

    # 4) 俱乐部侧的申请列表能看到
    mine = c.get(f'/api/club/{CLUB}/mall/visibility-requests').json()
    ok(any(x['id'] == rid for x in mine), '俱乐部端申请列表包含这条申请')

    # 5) 总平台待办能看到，且带出商品当前状态
    todo = c.get('/api/platform/product-visibility-requests', params={'status': 'pending'}).json()
    row = next((x for x in todo if x['id'] == rid), None)
    ok(bool(row) and row.get('product_status') == 'active', '总平台待办能看到这条申请', f'product_status={row and row.get("product_status")}')

    # 6) 批准：这一步才真正改 products.status
    d = c.post(f'/api/platform/product-visibility-requests/{rid}/decide', json={'decision': 'approved', 'note': '同意下架'}).json()
    ok(d.get('productStatus') == 'inactive', '批准后商品变为 inactive', str(d.get('productStatus')))

    # 7) 关键：C 端商城真的看不见它了
    after = [p['name'] for p in c.get(f'/api/club/{CLUB}/mall/products').json()]
    ok(name not in after, '批准后 C 端商城已无这件商品（下架真实生效）')

    # 8) 俱乐部端库存接口同步反映（按钮要变成「申请上架」）
    inv = {p['id']: p for p in c.get(f'/api/club/{CLUB}/mall/inventory').json()}
    ok(inv[pid].get('status') == 'inactive' and not inv[pid].get('pendingRequest'),
       '俱乐部端库存同步为已下架且无待处理申请')

    # 9) 重复批准幂等：第二次只能回「已处理过」，不能再次写商品
    again = c.post(f'/api/platform/product-visibility-requests/{rid}/decide', json={'decision': 'approved'}).json()
    ok(again.get('productStatus') is None and '已处理过' in str(again.get('note', '')), '重复批准是幂等的')

    # 10) 反向：重新上架也走同样的申请链路
    r2 = c.post(f'/api/club/{CLUB}/mall/products/{pid}/visibility', json={'action': 'on', 'note': '回归测试：重新上架'})
    ok(r2.status_code == 200, '下架后重新申请上架成功')
    rid2 = r2.json().get('requestId')
    d2 = c.post(f'/api/platform/product-visibility-requests/{rid2}/decide', json={'decision': 'approved'}).json()
    ok(d2.get('productStatus') == 'active', '批准后商品回到 active', str(d2.get('productStatus')))
    back = [p['name'] for p in c.get(f'/api/club/{CLUB}/mall/products').json()]
    ok(name in back, '重新上架后 C 端商城又能看到')

    # 11) 非法申请类型要被拒
    bad = c.post(f'/api/club/{CLUB}/mall/products/{pid}/visibility', json={'action': 'delete'})
    ok(bad.status_code == 400, '非法 action 被拒（400）', str(bad.status_code))


main()
print()
if FAILS:
    print(f'RESULT: FAILED ({len(FAILS)})')
    for f in FAILS:
        print('  - ' + f)
    sys.exit(1)
print('RESULT: ALL PASS · 俱乐部上下架申请 ↔ 总平台批准 ↔ C 端可见性 链路回归通过')
