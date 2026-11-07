# ClubOS 电商全链路 + 装备积分冲正 测试用例（AAA / P0-P4）

> Campaign: `ecommerce-gear-points` · 执行日期 2026-11-07 · 执行人 Horizon（QA）
> 被测版本：`91185e5` + BUG-001 fixed + **OBS-02/03/04 fixed + 固定门禁**（本轮本地 main，待发布）
> 环境：隔离 DB（`CLUBOS_TEST_OPEN=1` / `CLUBOS_SECURITY_MODE=demo` / `COMMERCE_PROVIDER=local` / `MOCK_AI=1`）
> 方法：Google AAA（Arrange / Act / Assert），P0 阻塞 → P4 低。所有用例均可执行，逐条判定见 `TEST-RESULTS.json`。
> **唯一真实来源原则**：本文档是用例权威来源，`TEST-EXECUTION-TRACKING.csv` 只记录执行状态。

---

## 0. 被测业务规则（先读代码取证，再定断言）

| 规则 | 代码位置 | 期望 |
|---|---|---|
| 购买发积分 | `checkout._finalize_gear` → `points.earn_gear_points` | `earn = int(cash_paid × gear_points_rate)`（默认 rate=1），写 `gear_point_ledger(type=earn, source_type=gear_order)` |
| 支付前抵扣锁积分 | `points.hold_points` / `create_gear_intent` | 结算单创建即扣余额，写 `type=hold`；`gear_points_redeem_rate=100`（100 分=¥1） |
| 支付转正式抵扣 | `points.consume_holds` + `materialize_redemptions` | 写 `redeem_commit`(0) + `point_redemptions(order_kind=gear, point_type=gear)` |
| 全额退款冲正 | `refund_lifecycle.finalize` → `commerce_refund.refund_gear_order` → `points.reverse_gear_order` | 返还已用积分（**自动增加**）+ 冲回已赚积分（**自动扣减**）；余额不足落 `point_adjustment_debt`；返还按「总抵扣 − 已返还 ＝ 待返还差额」一次性入账（BUG-001 修复） |
| 售后金额口径 | `clubos_domain/order_amounts.py` → `after_sales.create` / `commerce_refund.refund_gear_after_sales` | `gross_all = SUM(unit_price×qty)`；`goods_factor = total/gross_all`、`cash_factor = cash_paid/gross_all`。申请退款现金 = `原价 × cash_factor`（夹在剩余可退现金内）；商品额 = `原价 × goods_factor`；累计已退商品额按已完成售后单同系数重算（OBS-02 修复） |
| 售后部分退款冲正 | `refund_lifecycle.finalize(refund_scope=after_sales)` → `commerce_refund.refund_gear_after_sales` | 按 `(已退现金+本次退款)/现金实付` 冲回已赚积分；按 `(已退商品额+本次商品额)/订单额` 返还已用积分；`after_sales_marker` 幂等；终态重复退款返回 `200 + idempotent:true`（OBS-03 修复） |
| 退款入口分工 | `app.py` | 装备退款**审核权归总平台** `/api/platform/refunds`；`/api/club/{id}/refunds` 返回活动退款（可审核）+ 装备退款（`managed_by=platform` / `can_review=false` / `readonly=true`，只读可见）；俱乐部端三个写路由拒绝装备退款 409（OBS-04 修复） |
| 固定发版门禁 | `scripts/regression_v025.sh` | 末尾执行本套件；`set -euo pipefail` 下任一用例失败即挡发版 |

### ★ 测试设计不变量（避免魔法数字 / seed 脏数据）
探针证实隔离库 user 1 初始带 **1280** 装备积分（seed），且 club1 为 Pro 套餐（9.5 折）、user1 为会员，故一律用以下不变量判定，而非绝对余额：

- **I1 余额守恒**：任一用户 `gear_point_accounts.balance == 初始基线 + SUM(gear_point_ledger.amount)`（把模拟消费也写成负流水）。
- **I2 返回值 ↔ 流水一致**：退款返回的 `points.returnedRedeemedGearPoints` == `SUM(refund_redeem_return[source_id=订单])`；`earnedGearPointsToReverse` == `|SUM(refund_earn_reverse[source_id=订单])|`。
- **I3 幂等**：重复 finalize 后 balance 不变、ledger 条数不变、返回 `idempotent=true`（重复售后退款为 `200 + idempotent:true`，并以零新增流水硬断言守底）。
- **I4 非负**：`balance >= 0` 恒成立。
- **I5 欠账**：应冲回 > 可用余额时，`point_adjustment_debt.amount == 差额`，且余额不为负。

---

## 1. 采购 / 入库 / 上架 / 库存（ECOM）

### TC-ECOM-001 · P0 · 采购收货幂等，不重复入库
- **Arrange**：建供应商 → 建 draft 商品（规格 红/M）→ 建采购单 60 件 → approve → order → supplier-shipment。
- **Act**：`POST /receive` 同一 `requestKey` 连发两次。
- **Assert**：商品 `stock == 60`（不是 120）；`supplier_payables` 中该 `receiptId` 恰好 1 条；`purchase_receipt_items` 保留规格维度。

### TC-ECOM-002 · P1 · 收货不自动发布商品
- **Assert**：状态为 `pending_listing`，不是 `active`；C 端不可下单。

### TC-ECOM-003 · P1 · 上架前置校验
- **Assert**：只 `PATCH {status:active}` 返回 400；补全 `{price, image_url, description, status:active}` 后 200。

### TC-ECOM-004 · P0 · 结算锁库存
- **Assert**：可售库存减 2，且 `warehouse_sku_reservations` 有 reserved 记录。

### TC-ECOM-005 · P0 · 支付冻结成交单价
- **Act**：checkout 后把商品改价，再 `POST /pay {simulateSuccess:true}`。
- **Assert**：`gear_order_items.unit_price` 仍是下单时价格。

### TC-ECOM-006 · P1 · 取消未支付订单释放库存与锁定积分
- **Assert**：可售库存回补；`hold` 被 `hold_release` 抵消；I1 守恒。

### TC-ECOM-007 · P2 · 采购退货（B 端）不影响 C 端装备积分
- **Assert**：只扣该规格库存；`gear_point_ledger` 无任何新增；I1 守恒。

---

## 2. 装备积分发放与抵扣（GP-EARN）

### TC-GP-001 · P0 · 购买自动发放积分
- **Assert**：`pay.result.gearPointsEarned == int(cashPaid × gear_points_rate)`；ledger 有 `earn`；余额增加该值（I1）。实测 `earn=190 (cashPaid=190)`。

### TC-GP-002 · P0 · 积分抵扣即时扣减（hold）
- **Assert**：`quote.gearPointsUsed == min(请求数, 余额, 可抵上限)`；结算后余额减少；ledger 有 `hold(-N)`；未支付时 `point_redemptions` 无该订单记录。

### TC-GP-003 · P0 · 支付后 hold 转正式抵扣
- **Assert**：ledger 有 `redeem_commit(0)`；`point_redemptions` 有 `(gear, gear, points_used=N)`；`cash_value == N / gear_points_redeem_rate`。

---

## 3. 全额退款的积分自动增加 / 自动扣减（核心，GP-REFUND）

### TC-GP-004 · P0 · 全额退款「自动增加」：返还已抵扣积分
- **Assert**：余额 `+1470`；ledger `refund_redeem_return(+1470)`；返回 `points.returnedRedeemedGearPoints == 1470`（I2）。

### TC-GP-005 · P0 · 全额退款「自动扣减」：冲回已赚积分
- **Assert**：余额 `-175`；ledger `refund_earn_reverse(-175)`；返回 `earnedGearPointsToReverse == 175`（I2）。

### TC-GP-006 · P0 · 全额退款积分净额守恒
- **Assert**：退款后余额 == 该单下单前余额；I1 守恒；订单 `status=refunded`。

### TC-GP-007 · P0 · 全额退款幂等
- **Assert**：返回 `idempotent:true`；balance 不变；ledger 条数不变（I3）。

### TC-GP-008 · P1 · 标准退款链路同样冲正积分
- **Act**：`refund-request` → `platform/refunds/{rid}/approve`（local 回调 finalize）。
- **Assert**：冲回 95 分；`refund_requests.status=succeeded`。

### TC-GP-014 · P1 · 退款副作用联动：库存/佣金/AI Credits 一并冲回
- **Assert**：`refund_restock` 回补库存；`commission_ledger` 负向 `refund_reverse`；`ai_credit_ledger` 有 `mall_reward_reverse`。

---

## 4. 售后（退款 / 退货）的部分冲正（GP-AS）

### TC-GP-009 · P1 · 售后部分退款按现金比例冲回已赚积分
- **Arrange**：3 件订单实付 ¥285 赚 285 分；只退 1 件。
- **Assert**：口径统一后申请退款 ¥95（旧口径 ¥100），冲回 `285×95/285 = 95` 分；ledger `after_sales_earn_reverse(-95)` + `after_sales_marker(0)`；I1 守恒。

### TC-GP-010 · P1 · 售后部分退款按商品占比返还已抵扣积分（自动增加）
- **Assert**：`gearPointsReturned > 0` 且与同源 `goods/total` 比例一致；ledger `after_sales_redeem_return(>0)`；累计不超过原抵扣。实测 `+553/1660`。

### TC-GP-011 · P1 · 售后退款幂等（OBS-03）
- **Act**：重复 `POST /after-sales/{cid}/refund`。
- **Assert**：**200 + `idempotent:true` + `alreadyRefunded:true` + 回带原退款单号**；无重复 `after_sales_marker`；余额与冲正流水零变化（I3）。

### TC-GP-013 · P2 · 退货（return_refund）与仅退款口径一致
- **Assert**：最终积分冲正结果与等额 `refund_only` 一致；实物回库恰好一次。

---

## 5. 边界 / 资金安全（GP-EDGE）

### TC-GP-012 · P1 · 余额不足形成积分欠账，且余额不为负（I4/I5）
- **Assert**：`point_adjustment_debt` 有 `(point_type=gear, amount=95)`；余额保持 0。

### TC-GP-015 · P2 · 重复支付不重复发积分
- **Assert**：返回 `idempotent:true`；`earn` 流水不新增。

### TC-GP-016 · P2 · 拒绝退款不冲正积分
- **Assert**：订单 `refund_status=rejected`；积分余额/流水不变。

### TC-GP-017 · P3 · 装备退款对俱乐部只读可见、平台仍统管（OBS-04）
- **Assert**：`GET /api/club/1/refunds` **包含**装备退款行，且带 `managed_by='platform'` / `can_review=false` / `readonly=true` / 「装备订单 #N」标题；活动退款仍 `managed_by='club'` 且可审核；平台列表仍可查该单。

### TC-GP-018 · P1 · 同单两次部分售后累计冲正不超额
- **Assert**：累计返还/冲回均 ≤ 原抵扣/原赚取；marker 恰好 2 个。实测 `551/827`、`184/276`。

### TC-GP-019 · P1 · 同订单多条抵扣流水全额返还且返回值=账实（BUG-001）
- **Act**：正常支付后注入第 2 条同订单 `point_redemptions`，再全额退款；并对已冲正订单重入冲正。
- **Assert**：返还全部抵扣分；返回值 == 流水之和 == 抵扣总额；余额守恒；重入不二次返还。

---

## 6. 本轮新增：口径 / 幂等 / 权限 / 门禁（OBS 固化用例）

### TC-GP-020 · P1 · 售后金额口径同源：折扣订单不多退现金、不放大占比（OBS-02）
- **Arrange**：9.5 折会员订单，`2 件 × ¥100 原价` → `gross=200`、`total=190`、`cash_paid=190`。
- **Act**：只退 1 件 → approve → refund。
- **Assert**：
  - `requested_refund_amount == approved_refund_amount == ¥95`（旧口径 ¥100，**多退 ¥5**），且 `< 原价 ¥100`；
  - `amountBasis.goodsFactor == 190/200`、`cashFactor == 190/200`；
  - `goodsRefundAmount == ¥95`，`goods/total == 1/2`（旧口径 `100/190 ≈ 0.526`）；
  - `orderFullyRefunded == False`；累计 `refunded_goods_total` 落在折后口径；累计退款现金 ≤ 实付。

### TC-GP-021 · P2 · 非法状态发起售后退款仍 409（OBS-03 守卫）
- **Assert**：`pending_review` 状态与 `exchange` 类型售后单发起退款均返回 409；幂等 200 只覆盖已完成冲正的终态。

### TC-GP-022 · P2 · 俱乐部端不得审核装备退款（OBS-04 只读闸门）
- **Act**：对一条装备退款分别调用 `/api/club/1/refunds/{rid}/{approve,reject,retry-provider}`。
- **Assert**：三者全部 409；`/api/platform/refunds` 仍能查到该单（平台是唯一审核入口）。

### TC-GP-023 · P1 · 电商积分回归已挂进 `regression_v025.sh` 固定门禁
- **Assert**：脚本含 `set -euo pipefail`；含 `python tests/ecommerce_gear_points_regression.py` 且位于 `set -e` 之后。

### TC-GP-024 · P2 · 俱乐部端装备退款只读行与缓存版本护栏
- **Assert**：`club.js` 有 `gearRefundRow()`、按 `managed_by==='platform'` 分流、含「本端仅可查看」、只读分支内不出现 `approveRefund(`；`club/index.html` 引用 `club.js?v=20261107e`。

---

## 7. 观察项 / 缺陷闭环

| ID | 记录 | 结论 |
|---|---|---|
| OBS-01 | 多 redemption 少返还猜想 | **BUG-001（P2）→ 已修复、已发布**；TC-GP-019 固化 |
| OBS-02 | 售后商品占比分子用原价、分母用会员折后金额 | 复核发现「多退现金」，**升级 BUG-002（P1）→ 已修复关闭**；TC-GP-020 固化 |
| OBS-03 | 重复售后退款返 409 而非 `idempotent:true` | **BUG-003（P3）→ 已修复关闭**；TC-GP-011 收紧 / TC-GP-021 守卫 |
| OBS-04 | 装备退款归平台、俱乐部端仅活动退款（原判 by-design） | 按产品要求改为「俱乐部只读可见、平台保留审核权」→ **BUG-004（P3）关闭**；TC-GP-017/022/024 固化 |
| — | 电商积分回归未挂发版门禁 | **已挂载**；TC-GP-023 自检 |

> 全量交付：`tests/ecommerce_gear_points_regression.py`（27 条自动用例，自带临时库隔离）。

---

## 8. 质量门禁（Google QA 标准）

| 指标 | 门禁 | 判定（本轮复验） |
|---|---|---|
| 用例执行率 | 100% | ✅ 27/27 + 交叉回归 29/29 |
| 通过率 | ≥ 80% | ✅ 100%（主套件 27/27） |
| P0 缺陷 | = 0 | ✅ 0 |
| P1 缺陷 | ≤ 5 | ✅ 0（BUG-002 已 Closed） |
| 遗留缺陷 | — | ✅ 0（BUG-001/002/003/004 全 Closed） |
| 覆盖率（电商+积分域） | ≥ 80% | ✅ 11 个子域全覆盖 |
| 安全（资金/积分不变量） | 100% | ✅ I1~I5 全过 |
| 固定发版门禁 | 必须挂载 | ✅ 已挂载 + TC-GP-023 防摘除 |

**门禁总判定：PASS（无遗留缺陷，可发版）**。完整结论见 `QA-REPORT.md`。本轮按用户要求仅本地完成，**待用户明确说「发布」再上线**。
