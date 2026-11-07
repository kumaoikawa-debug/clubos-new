# ClubOS 电商全链路 + 装备积分冲正 · QA 与修复验证报告

| 项 | 值 |
|---|---|
| Campaign | `ecommerce-gear-points` |
| 执行人 | Horizon（QA 测试专家） |
| 执行日期 | 2026-11-07 |
| 被测版本 | `0873e01`（首轮 QA）→ `91185e5`（BUG-001 发布）→ 本轮 OBS-02/03/04 + 门禁挂载（本地 main，**待发布**） |
| 环境 | 隔离 DB：`CLUBOS_TEST_OPEN=1` / `CLUBOS_SECURITY_MODE=demo` / `COMMERCE_PROVIDER=local` / `MOCK_AI=1`；`fastapi.testclient.TestClient`，临时 SQLite，零共享状态 |
| 交付物 | `tests/ecommerce_gear_points_regression.py`、`TEST-CASES-ECOMMERCE-POINTS.md`、`TEST-RESULTS.json`、`TEST-EXECUTION-TRACKING.csv`、`BUG-TRACKING.csv`、`tests/e2e/reproduce-BUG-001.py`、`finalize.py` |
| 发版状态 | 主套件 **27/27 全绿**；全量 `scripts/regression_v025.sh` 已挂载本套件作固定门禁；**本轮用户未说「发布」，仅本地完成 + 待发布** |

---

## 一、结论速览

1. **电商购物全链路（采购 → 入库 → 上架 → 结算 → 支付 → 履约 → 盘点 → 采购退回）**：主套件 **27/27 全绿**，交叉跑通既有全链路回归 **29/29 全绿**。
2. **退款/退货之后的积分自动增加与自动扣减**：全部正确。全额退款返还已抵扣积分（自动增加）+ 冲回已赚积分（自动扣减）；售后部分退款按现金比例冲回、按商品占比返还；幂等、欠账不透支、累计不超额均验证通过。
3. **BUG-001（P2）已修复并发布**：多抵扣流水少返还积分 / 返回值虚高已根治，`TC-GP-019` 固化；`91185e5` 已上线核验。
4. **本轮 4 项待办全部落地**：固定门禁挂载、OBS-02 金额口径统一、OBS-03 幂等语义、OBS-04 俱乐部端只读可见装备退款。新增 `TC-GP-020 ~ 024` 五条自动用例，主套件从 22 条扩到 27 条。
5. **OBS-02 复核后升级为真实资金缺陷**：不是「口径略不一致」。隔离库 club1 = Pro 套餐（9.5 折）、user1 为会员，折扣真实生效；旧逻辑对 9.5 折订单退 1 件 ¥100 原价商品会申请退 **¥100**，而顾客实付仅 **¥95**，每单多退一个折扣差。已在缺陷表登记为 **BUG-002（P1，Closed）**。

---

## 二、质量门禁判定（本轮复验）

| 指标 | 门禁 | 实测 | 判定 |
|---|---|---|---|
| 用例执行率 | 100% | 27/27（+ 交叉回归 29/29） | ✅ |
| 通过率 | ≥ 80% | 主套件 100% | ✅ |
| P0 缺陷 | = 0 | 0 | ✅ |
| P1 缺陷 | ≤ 5 | 0（BUG-002 已 Closed） | ✅ |
| 遗留缺陷 | 0 | 0（BUG-001/002/003/004 全 Closed） | ✅ |
| 电商+积分域覆盖率 | ≥ 80% | 11 个子域全覆盖 | ✅ |
| 资金/积分安全不变量 | 100% | I1/I2/I3/I4/I5 全过 | ✅ |
| 固定发版门禁 | 必须挂载 | `regression_v025.sh` 已挂载，且 `TC-GP-023` 自检防摘除 | ✅ |
| **发版门禁** | — | **PASS（无遗留缺陷）** | ✅ |

覆盖子域：采购/收货、库存/上架、结算/锁库存、支付/冻结单价、积分发放、积分抵扣、全额退款冲正、售后/退货部分冲正、资金安全边界（含异常数据）、售后金额口径、退款归属与权限、发版门禁自检。

---

## 三、核心机制取证（真实数值，非推测）

以回归中一笔真实订单为例（会员 95 折，100 元/件，2 件）：

| 阶段 | 余额 | 关键流水 |
|---|---|---|
| 下单前 | 1470 | — |
| 结算用积分抵扣 1470 分（抵 ¥14.7） | 0 | `hold -1470` |
| 支付成功（实付 ¥175.3，赚 175 分） | 175 | `redeem_commit 0`、`point_redemptions(1470, ¥14.7)`、`earn +175` |
| **全额退款** | **1470** | **`refund_redeem_return +1470`（自动增加）**、`refund_earn_reverse -175`（自动扣减）、佣金 -16、AI Credits -3、库存回补 |
| 重复退款 | 1470 | 无新增流水，`idempotent=true` |

- 返回值 `points.{returnedRedeemedGearPoints:1470, earnedGearPointsToReverse:175}` 与流水逐条对得上（I2）；退款后余额精确回到下单前（I1）。
- 标准退款链路（`refund-request` → `approve` → local 回调 finalize）冲回 95 分，与快捷链路口径一致。
- **售后部分退款（本轮口径统一后）**：3 件实付 ¥285 只退 1 件 → 申请退款 **¥95**（不是 ¥100），冲回 `285×95/285 = 95` 分，写 `after_sales_earn_reverse` + `after_sales_marker`；用抵扣的订单按同源商品占比返还 `+553/1660` 分。
- 边界：余额清零后退款 → 余额保持 0 不透支，落 `point_adjustment_debt(gear, 95)`；同单两次部分售后累计返还 551/827、冲回 184/276，均不超额。

---

## 四、五大不变量（避免魔法数字与脏数据）

隔离库里 user 1 初始带 1280 装备积分（seed 数据），因此**没有写死任何绝对余额**：

- **I1 余额守恒**：`balance(after) == balance(before) + SUM(ledger.amount WHERE id > 基线)` — 全流程通过。
- **I2 返回值 ↔ 流水一致**：退款返回的积分字段必须等于对应流水金额 — 正常链路与异常数据链路（TC-GP-019）全部通过。
- **I3 幂等**：重复 finalize 后余额/流水条数不变 — 通过；本轮 OBS-03 起重复售后退款返回 `200 + idempotent:true`，仍由「零新增流水」硬断言守住。
- **I4 非负**：`balance >= 0` 恒成立 — 通过。
- **I5 欠账**：应冲回 > 可用余额时 `point_adjustment_debt.amount == 差额` — 通过。

---

## 五、BUG-001 修复记录（P2 / Closed / 已发布）

| 项 | 内容 |
|---|---|
| 组件 | `clubos_domain/points.py :: reverse_gear_order()` |
| 根因 | 幂等判断 `if already: break` 写在 `for r0 in reds` 循环**内部**，粒度却是订单级；第一条返还写入后跳出，其余流水不再处理，而返回值用 `sum(所有 reds)` |
| 修复 | 改为**「总抵扣额 − 已返还额 ＝ 待返还差额」一次性入账**，幂等基于 `gear_point_ledger` SUM，返回值取累计账实 |
| 验证 | `TC-GP-019`（含重入幂等）+ `reproduce-BUG-001.py` `MISMATCH=False` |
| 发布 | `91185e5` 已上线；四端静态 SHA-256 全 MATCH，`/api/platform/orders` 真实数据完好 |

---

## 六、本轮四项改动与取证

### 1. 电商积分回归挂进 `scripts/regression_v025.sh` 固定门禁

脚本末尾新增 `python tests/ecommerce_gear_points_regression.py`（自带临时库隔离，不需要 `CLUBOS_DB_PATH`）。脚本首行是 `set -euo pipefail`，因此该套件任一用例失败都会直接挡住发版。`TC-GP-023` 会自检该调用仍在且位于 `set -e` 之后，防止日后被悄悄摘除。

### 2. OBS-02 → BUG-002（P1 / Closed）：售后金额口径统一

**取证**：隔离库 `club1.plan=pro`、`ai_credit_plans.pro.gear_discount=0.95`、`user1 ∈ club_members`，`checkout._member_gear_discount(200.0)` 实测返回 `(0.95, 10.0)`。于是 `gear_orders.total=190`（折后），而 `gear_order_items.unit_price×qty=200`、`after_sales_items.requested_amount=100`（原价）。

**旧行为的两处真实偏差**：
- `requested_refund = gross × (cash_paid/total) = 100 × 1 = ¥100`，但顾客为该件实付 ¥95 → **多退 ¥5/件**。
- `goods/total = 100/190 ≈ 0.526`（正确应为 `95/190 = 0.5`），驱动积分/佣金/AI Credits 冲回比例与 `fully` 判定；累计多次售后时可能把「退部分」误判为「全单退完」，触发整单退款终态与福利券恢复。
- 附带影响：`merchandise_finance.order_profit()` 用 `min(total, refunded_goods_total)` 算净销售，旧口径会低估净销售。

**修复**：新增 `clubos_domain/order_amounts.py` 作为唯一真实来源。
```
gross_all     = SUM(gear_order_items.unit_price × quantity)
goods_factor  = total     / gross_all   （原价 → 折后商品口径，与 total 同源）
cash_factor   = cash_paid / gross_all   （原价 → 实付现金口径，与 cash_paid 同源）
```
`after_sales.create` 用 `gross × cash_factor` 生成申请退款额（并夹在剩余可退现金内）；`refund_gear_after_sales` 用 `goods_raw × goods_factor` 生成商品额，累计已退商品额改从「已写 `after_sales_marker` 的售后单」按同一系数重算，避免历史库里按原价写入的 `refunded_goods_total` 与新口径混用。返回值新增 `amountBasis` 供对账。老库缺订单明细时安全回退旧口径。

**验证**：`TC-GP-020` 实测 `gross=200 → total=190`，退 1 件现金 ¥95（旧口径 ¥100）、商品额 ¥95、占比恰为 1/2、`orderFullyRefunded=False`、累计 `refunded_goods_total` 落在折后口径。原有 21 条全部复跑通过。

### 3. OBS-03 → BUG-003（P3 / Closed）：重复售后退款改幂等 200

`_start_after_sales_refund` 原来对终态 `completed/refunded` 一律 409。现改为：仅当该售后单确有 `refund_request_id` 或 `after_sales_marker`（即确实完成过资产冲正）时，返回
`200 + {ok:true, idempotent:true, alreadyRefunded:true, refundRequestId, refundStatus, cashAmount}`，不产生任何副作用；`pending_review` / `awaiting_return` / `rejected` / `cancelled` / `exchange` 等非法状态仍 409。

**验证**：`TC-GP-011` 断言 200 + `idempotent:true` + 余额/冲正流水零变化；`TC-GP-021` 断言 `pending_review` 与 `exchange` 仍被 409 拦截（没有为了幂等放宽状态机）。

### 4. OBS-04 → BUG-004（P3 / Closed）：俱乐部端只读可见装备退款

原分工「装备退款审核权归总平台」保持不变，但 `/api/club/{club_id}/refunds` 之前 SQL 写死 `kind='activity'`，俱乐部完全查不到自己订单的装备退款进度。现该接口合并返回两类退款：

- 活动退款：`managed_by='club'`、`can_review=true`（保持原审核能力）。
- 装备退款：`managed_by='platform'`、`can_review=false`、`readonly=true`，带 `gear_order_id` / `gear_titles` / `order_total` / `gear_order_status` / `refund_scope` / `after_sales_case_id`，标题为「装备订单 #N」。
- 新增 `_club_activity_refund_or_404` 写操作闸门：俱乐部端 `approve` / `reject` / `retry-provider` 遇到装备退款一律 **409「装备退款由总平台审核，俱乐部端仅可查看」**，平台端 `/api/platform/refunds` 仍是唯一审核入口。
- 前端 `static/club/club.js` 新增 `gearRefundRow()`：装备退款行显示真实状态与金额，明确「本端仅可查看（只读）」，**不渲染任何审核/拒绝/重试按钮**；`index.html` 缓存版本 `20261107d → 20261107e`、退款审核页副标题同步改为「活动退款由俱乐部审核；装备退款由总平台统管，本端仅可查看进度」，`node --check` 通过。
- **真浏览器复核**（隔离实例 + 真实装备退款数据）：确认加载的是新代码（`gearRefundRow` / `?v=20261107e` 均在），`regs-refund` 视图渲染 11 条装备退款行、11 条均带只读提示、`approve/reject/retry` 按钮数均为 0、控制台无错误、版式无重叠；真实接口重复售后退款返回 `200 + idempotent:true`，俱乐部越权写返回 409。

**验证**：`TC-GP-017`（只读可见 + 归属标记 + 活动退款未被误标）、`TC-GP-022`（三个写路由全 409）、`TC-GP-024`（前端只读行 + 版本 bump）。

---

## 七、观察项闭环表

| ID | 原观察 | 本轮结论 |
|---|---|---|
| OBS-01 | 多 redemption 少返还猜想 | **BUG-001（P2）→ 已修复、已发布** |
| OBS-02 | 售后商品占比分子用原价、分母用会员折后金额 | **升级为 BUG-002（P1，多退现金）→ 已修复并关闭**（TC-GP-020） |
| OBS-03 | 重复售后退款返 409 而非 `idempotent:true` | **BUG-003（P3，API 语义）→ 已修复并关闭**（TC-GP-011/021） |
| OBS-04 | 装备退款归平台、俱乐部端仅活动退款（原判 by-design） | **BUG-004（P3，可见性）→ 已改为「俱乐部只读可见、平台保留审核权」并关闭**（TC-GP-017/022/024） |
| — | 电商积分回归未挂发版门禁 | **已挂载 + TC-GP-023 自检** |

> 本轮按用户要求把这 4 项全部改完；**没有放宽任何断言掩盖问题**，OBS-02 反而从「观察项」升级为真实资金缺陷。

---

## 八、过程中两条 FAIL 的甄别（历史记录，用例问题非产品缺陷）

首轮 18/20，两条 FAIL 经取证确认均为测试用例自身设计问题，已修正并复跑：

1. **TC-GP-011**：售后退款成功后 case 进入 `completed`，重复请求被 409 拒绝；用例却写死 `want 200`。当时改为「409 或幂等 200 均可，但零重复副作用」；本轮 OBS-03 落地后**收紧**为必须 `200 + idempotent:true`。
2. **TC-GP-016**：基线 `base()` 取在 `new_order()` 之前，把「购买发积分」算进「拒绝退款」差值。把基线移到支付完成后。

---

## 九、复现命令

```bash
cd clubos-new
./venv/bin/python tests/ecommerce_gear_points_regression.py      # 主套件 27/27
PATH="$PWD/venv/bin:$PATH" CLUBOS_DB_PATH=/tmp/x.db CLUBOS_TEST_OPEN=1 bash scripts/regression_v025.sh  # 全量发版门禁（已含本套件）
./venv/bin/python tests/inventory_flow_regression.py             # 交叉回归 29/29
./venv/bin/python qa_ecommerce/tests/e2e/reproduce-BUG-001.py   # BUG-001 修复验证，MISMATCH=False
./venv/bin/python qa_ecommerce/tests/docs/finalize.py           # 重建追踪表/缺陷表
env -u NODE_OPTIONS node --check static/club/club.js            # 前端语法
```

输出：`qa_ecommerce/tests/docs/TEST-RESULTS.json`（结构化逐条结果）。

---

## 十、本轮变更清单（2026-11-07 第二波）

| 文件 | 变更 |
|---|---|
| `scripts/regression_v025.sh` | 末尾挂载 `tests/ecommerce_gear_points_regression.py` 作固定门禁（`set -euo pipefail` 下失败即挡发版） |
| `clubos_domain/order_amounts.py` | **新增**：订单级金额口径唯一真实来源（`order_gross` / `amount_basis`） |
| `clubos_domain/after_sales.py` | 申请退款现金改为「原价占比 × 实付现金」，夹在剩余可退现金内（OBS-02） |
| `clubos_domain/commerce_refund.py` | 商品额折到 `total` 同源口径、累计已退商品额按已完成售后单重算、返回 `amountBasis`（OBS-02） |
| `app.py` | `_start_after_sales_refund` 终态幂等 200（OBS-03）；`club_refunds` 合并只读装备退款 + `_club_activity_refund_or_404` 写闸门（OBS-04） |
| `static/club/club.js` | `renderRefundReview` 分流 + 新增 `gearRefundRow()` 只读行（无审核按钮） |
| `static/club/index.html` | `club.js?v=20261107d → 20261107e` |
| `tests/ecommerce_gear_points_regression.py` | 收紧 TC-GP-011 / 改写 TC-GP-017；新增 TC-GP-020~024；版本标记更新；主套件 22 → 27 条 |
| `TEST-CASES-ECOMMERCE-POINTS.md` / `QA-REPORT.md` / `finalize.py` / 两张 CSV / `TEST-RESULTS.json` | 同步 27/27 结论、BUG-002/003/004 登记与观察项闭环 |
| `versions/*-OBS*前-v1.*` | 10 份改动前备份 |

**待办**：本轮用户未说「发布」，以上均为本地改动 + 本地验证；按发布纪律需用户明确说「发布」才走 hold → 部署 → 线上 SHA/接口取证 → 还原 → commit/push。
