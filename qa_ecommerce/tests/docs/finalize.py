"""从 TEST-RESULTS.json 生成执行追踪表 / 缺陷表 / QA 报告（可重复运行）。

唯一真实来源：TEST-CASES-ECOMMERCE-POINTS.md 是用例权威来源，
TEST-RESULTS.json 是执行结果，本脚本只把它们汇总成 CSV。
"""
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
    'TC-GP-011': '幂等/API 语义(OBS-03)',
    'TC-GP-012': '资金安全边界',
    'TC-GP-013': '退货全链路',
    'TC-GP-014': '退款联动',
    'TC-GP-015': '幂等',
    'TC-GP-016': '退款状态机',
    'TC-GP-017': '权限/归属(OBS-04)',
    'TC-GP-018': '资金安全边界',
    'TC-GP-019': '资金安全边界(异常数据)',
    'TC-GP-020': '售后金额口径(OBS-02)',
    'TC-GP-021': '售后状态机守卫(OBS-03)',
    'TC-GP-022': '权限/归属(OBS-04)',
    'TC-GP-023': '固定发版门禁',
    'TC-GP-024': '前端只读护栏(OBS-04)',
}

# 用例 → 关联缺陷（Gate 自检无缺陷关联）
CASE_BUG = {
    'TC-GP-019': 'BUG-001',
    'TC-GP-020': 'BUG-002',
    'TC-GP-011': 'BUG-003',
    'TC-GP-021': 'BUG-003',
    'TC-GP-017': 'BUG-004',
    'TC-GP-022': 'BUG-004',
    'TC-GP-024': 'BUG-004',
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
                    'Completed', c['result'], CASE_BUG.get(c['id'], ''), DATE, BY, c['detail'],
                    'TEST-RESULTS.json'])
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
ENV = 'macOS / Python 3.13 / 隔离 DB / CLUBOS_TEST_OPEN=1 / 91185e5 + OBS fixes'
rows = [
    ['BUG-001', '全额退款在多抵扣流水下少返还积分且返回值虚高', 'P2',
     'clubos_domain/points.py :: reverse_gear_order', 'TC-GP-019', 'Closed',
     DATE, BY, 'Engineering Lead',
     'reverse_gear_order() 的幂等判断 `if already: break` 写在 for r0 in reds 循环内部，'
     '且判断粒度是订单级（source_id=order_id）。第一条返还写入后即跳出循环，'
     '导致同订单其余 gear 抵扣流水不返还；返回值仍用 sum(所有 reds)，形成账实不符。',
     '1. 用积分抵扣下单一笔装备订单\n2. 注入第 2 条同订单 gear 流水(500分)\n'
     '3. POST /api/platform/orders/{id}/refund\n4. 对比返回值与 refund_redeem_return 流水之和',
     '返还全部抵扣分；返回值 == 流水之和（I2）；余额守恒（I1）',
     '修复前仅返还 3000/3500 分且返回值报 3500。修复后全额返还，MISMATCH=False',
     'macOS / Python 3.13 / 隔离 DB / 版本 0873e01',
     'qa_ecommerce/tests/e2e/reproduce-BUG-001.py',
     '改为「总抵扣额 - 已返还额 = 待返还差额」一次性入账，幂等基于流水 SUM，返回值取累计账实。'
     '已补 TC-GP-019；commit 91185e5 已发布上线核验。',
     DATE, 'Horizon (QA)', DATE],
    ['BUG-002', '会员折扣订单的售后退款按原价计算，多退现金且放大商品占比（OBS-02）', 'P1',
     'clubos_domain/after_sales.py :: create / clubos_domain/commerce_refund.py :: refund_gear_after_sales',
     'TC-GP-020', 'Closed', DATE, BY, 'Engineering Lead',
     'after_sales_items.requested_amount 与 gear_order_items.unit_price 是商品原价，'
     'gear_orders.total / cash_paid 却是会员装备折扣 + 积分抵扣后的成交金额。'
     '旧逻辑 requested_cash = gross*(cash_paid/total)，在 9.5 折且无积分抵扣时 '
     'cash_paid==total，于是原价 100 元的商品申请退 100 元（实付仅 95 元）；'
     'goods/total = 100/190 ≈ 0.526（应为 0.5），驱动积分/佣金/AI Credits 冲回比例与 '
     'orderFullyRefunded 判定，累计售后时可能把部分退款误判为全单退完。'
     '另使 merchandise_finance.order_profit() 低估净销售。',
     '1. club1=Pro(9.5折)、user1 为会员，下单 2 件 ×¥100（total=190）\n'
     '2. 对 1 件发起 refund_only 售后\n3. 查看 requested_refund_amount / goodsRefundAmount',
     '申请退款 ¥95；商品额 ¥95；goods/total = 1/2；orderFullyRefunded=False',
     '修复前申请退款 ¥100（多退 ¥5）、商品额 ¥100、占比 100/190',
     ENV, 'TEST-RESULTS.json / TC-GP-020',
     '新增 clubos_domain/order_amounts.py 作为唯一真实来源：goods_factor=total/gross_all、'
     'cash_factor=cash_paid/gross_all；建单按原价占比分摊实付现金并夹在剩余可退现金内；'
     '冲正把商品额折到 total 同源口径，累计已退商品额按已完成售后单（after_sales_marker）'
     '同系数重算；返回值新增 amountBasis。老库缺明细时安全回退旧口径。',
     DATE, 'Horizon (QA)', DATE],
    ['BUG-003', '重复售后退款返回 409 而非 idempotent:true 200（OBS-03）', 'P3',
     'app.py :: _start_after_sales_refund', 'TC-GP-011 / TC-GP-021', 'Closed',
     DATE, BY, 'Engineering Lead',
     '售后单成功退款后进入 completed/refunded，重复点击或网络重试被状态机直接 409，'
     '前端必须特判 409 才能识别为「已处理」，与其他幂等端点（全额退款、重复支付）语义不一致。',
     '1. 完成一次售后退款\n2. 再次 POST /api/platform/after-sales/{cid}/refund',
     '200 + {ok:true, idempotent:true, alreadyRefunded:true, refundRequestId, refundStatus}，零副作用',
     '修复前 409 "当前售后状态不能发起退款: refunded"',
     ENV, 'TEST-RESULTS.json / TC-GP-011',
     '仅当终态且确有 refund_request_id 或 after_sales_marker 时短路返回幂等 200；'
     'pending_review / awaiting_return / rejected / cancelled / exchange 仍 409。'
     'TC-GP-011 收紧为必须 200+idempotent，TC-GP-021 守住非法状态不被放宽。',
     DATE, 'Horizon (QA)', DATE],
    ['BUG-004', '俱乐部端完全看不到自己订单的装备退款进度（OBS-04）', 'P3',
     'app.py :: club_refunds / _club_activity_refund_or_404；static/club/club.js',
     'TC-GP-017 / TC-GP-022 / TC-GP-024', 'Closed',
     DATE, BY, 'Engineering Lead',
     '原 /api/club/{club_id}/refunds SQL 写死 rr.kind=activity 且 INNER JOIN 活动表，'
     '装备退款全部不可见。审核权归平台是正确分工，但「不可见」导致俱乐部客服无法回答顾客进度。',
     '1. 产生一笔 club1 的装备退款\n2. GET /api/club/1/refunds',
     '装备退款只读可见，带 managed_by=platform / can_review=false / readonly=true；'
     '活动退款仍可审核；俱乐部端写操作全部拒绝；平台端仍是唯一审核入口',
     '修复前列表完全没有装备退款行',
     ENV, 'TEST-RESULTS.json / TC-GP-017',
     'club_refunds 合并 activity + gear 两类查询并统一打归属标记、按时间倒序；'
     '新增 _club_activity_refund_or_404 闸门，approve/reject/retry-provider 遇装备退款 409；'
     'club.js 新增 gearRefundRow() 只读行（无审核按钮），index.html 缓存版本 bump 20261108a。',
     DATE, 'Horizon (QA)', DATE],
]
with bug.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f); w.writerow(bcols)
    for r in rows:
        w.writerow(r)

print('WROTE', track.name, bug.name)
print('cases=', len(cases), 'bugs=', len(rows), 'summary=', summary)
