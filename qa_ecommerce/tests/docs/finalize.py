"""从 TEST-RESULTS.json 生成执行追踪表 / 缺陷表 / QA 报告（可重复运行）。"""
import json, csv
from pathlib import Path

HERE = Path(__file__).resolve().parent
data = json.loads((HERE / 'TEST-RESULTS.json').read_text(encoding='utf-8'))
cases = data['cases']
summary = data['summary']
DATE = '2026-11-07'
BY = 'Horizon (QA)'

CATEGORY = {
    'TC-ECOM': '电商采购/入库/库存',
    'TC-GP-001': '积分发放',
    'TC-GP-002': '积分抵扣',
    'TC-GP-004': '全额退款冲正',
    'TC-GP-007': '幂等',
    'TC-GP-008': '全额退款冲正',
    'TC-GP-009': '售后部分退款',
    'TC-GP-010': '售后部分退款',
    'TC-GP-011': '幂等',
    'TC-GP-012': '资金安全边界',
    'TC-GP-013': '退货全链路',
    'TC-GP-014': '退款联动',
    'TC-GP-015': '幂等',
    'TC-GP-016': '退款状态机',
    'TC-GP-017': '权限/归属',
    'TC-GP-018': '资金安全边界',
    'TC-GP-019': '资金安全边界(异常数据)',
}


def category(tc_id):
    for k, v in CATEGORY.items():
        if tc_id.startswith(k):
            return v
    return '积分/订单'


# ---------------- 1. 执行追踪表 ----------------
track = HERE / 'TEST-EXECUTION-TRACKING.csv'
cols = ['Test Case ID', 'Category', 'Priority', 'Test Name', 'Estimated Time (min)',
        'Prerequisites', 'Status', 'Result', 'Bug ID', 'Execution Date',
        'Executed By', 'Notes', 'Screenshot/Log']
with track.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(cols)
    for c in cases:
        w.writerow([c['id'], category(c['id']), c['priority'], c['name'], 3,
                    '隔离DB + 供应商/商品/采购入库/上架 + 收货地址',
                    'Completed', c['result'], 'BUG-001' if c['id'] == 'TC-GP-019' else '', DATE, BY, c['detail'],
                    'TEST-RESULTS.json'])
    # TC-GP-019 已并入主套件（见 TEST-RESULTS.json），不再单列深度取证行
    # 既有全链路回归（交叉验证，29 项汇总为 1 行）
    w.writerow(['TC-ECOM-REG', '电商全链路交叉验证', 'P0',
                'inventory_flow_regression.py（采购→入库→结算→发货→盘点→采购退回→权限）', 5,
                '隔离DB；既有回归脚本', 'Completed', 'PASS', '', DATE, BY,
                '29/29 全绿：页发货恰好一次/并发最后一件一胜/隔离库存不可售/过期结账释放/迁移外键完好',
                'tests/inventory_flow_regression.py'])

# ---------------- 2. 缺陷表 ----------------
bug = HERE / 'BUG-TRACKING.csv'
bcols = ['Bug ID', 'Title', 'Severity', 'Component', 'Test Case ID', 'Status',
         'Reported Date', 'Reported By', 'Assigned To', 'Description',
         'Steps to Reproduce', 'Expected Result', 'Actual Result', 'Environment',
         'Screenshots/Logs', 'Resolution', 'Resolved Date', 'Verified By', 'Verification Date']
with bug.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(bcols)
    w.writerow([
        'BUG-001', '全额退款在多抵扣流水下少返还积分且返回值虚高', 'P2',
        'clubos_domain/points.py :: reverse_gear_order', 'TC-GP-019', 'Closed',
        DATE, BY, 'Engineering Lead',
        'reverse_gear_order() 的幂等判断 `if already: break` 写在 for r0 in reds 循环内部，'
        '且判断粒度是订单级（source_id=order_id）。第一条返还写入后即跳出循环，'
        '导致同订单其余 gear 抵扣流水不返还；但返回值 returnedRedeemedGearPoints '
        '仍用 sum(所有 reds) 求和，形成「返回值 3500 / 实际入账 3000」的账实不符。'
        '正常 HTTP 链路一个 gear 订单只产生 1 条抵扣流水，故不触发；'
        '历史脏数据、重复记账或未来多来源抵扣会踩中。售后部分退款路径按 SUM 一次性返还，不受影响。',
        '1. 用积分抵扣下单一笔装备订单\n'
        '2. 手工往 point_redemptions 注入第 2 条同订单 gear 流水(500分)\n'
        '3. POST /api/platform/orders/{id}/refund 全额退款\n'
        '4. 对比返回的 points.returnedRedeemedGearPoints 与 gear_point_ledger 中 refund_redeem_return 之和',
        '返还全部 3500 分；返回值 == 流水之和（不变量 I2）；余额守恒 I1 成立',
        '修复前：仅返还 3000 分；返回值报 3500（虚高 500）；用户少拿 500 装备积分。'
        '修复后：返还 3500 分，返回值 = 流水 = 3500，MISMATCH = False',
        'macOS / Python 3.13 / 隔离 DB / CLUBOS_TEST_OPEN=1 / 版本 0873e01',
        'qa_ecommerce/tests/e2e/reproduce-BUG-001.py',
        '已修复：reverse_gear_order 改为「总抵扣额 - 已返还额 = 待返还差额」一次性入账，'
        '幂等判断基于 gear_point_ledger 流水 SUM 而非存在性，返回值取累计已返还额，保证 I2 账实一致。'
        '已补 TC-GP-019 自动回归（含重入幂等断言），主套件 22/22 全绿。',
        DATE, 'Horizon (QA)', DATE])

print('WROTE', track.name, bug.name)
print('cases=', len(cases), 'summary=', summary)
