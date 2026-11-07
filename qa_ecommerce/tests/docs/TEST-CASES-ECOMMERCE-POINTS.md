# ClubOS 电商全链路 + 装备积分冲正 测试用例（AAA / P0-P4）

> Campaign: `ecommerce-gear-points` · 执行日期 2026-11-07 · 执行人 Horizon（QA）
> 被测版本：`0873e01` · 环境：隔离 DB（`CLUBOS_TEST_OPEN=1` / `CLUBOS_SECURITY_MODE=demo` / `COMMERCE_PROVIDER=local` / `MOCK_AI=1`）
> 方法：Google AAA（Arrange / Act / Assert），P0 阻塞 → P4 低。所有用例均可执行，判定见 `TEST-RESULTS.md`。

---

## 0. 被测业务规则（先读代码取证，再定断言）

| 规则 | 代码位置 | 期望 |
|---|---|---|
| 购买发积分 | `checkout._finalize_gear` → `points.earn_gear_points` | `earn = int(cash_paid × gear_points_rate)`（默认 rate=1），写 `gear_point_ledger(type=earn, source_type=gear_order)` |
| 支付前抵扣锁积分 | `points.hold_points` / `create_gear_intent` | 结算单创建即扣余额，写 `type=hold`；`gear_points_redeem_rate=100`（100 分=¥1） |
| 支付转正式抵扣 | `points.consume_holds` + `materialize_redemptions` | 写 `redeem_commit`(0) + `point_redemptions(order_kind=gear, point_type=gear)` |
| 全额退款冲正 | `refund_lifecycle.finalize` → `commerce_refund.refund_gear_order` → `points.reverse_gear_order` | 返还已用积分（**自动增加**）+ 冲回已赚积分（**自动扣减**）；余额不足落 `point_adjustment_debt`；ledger marker 幂等 |
| 售后部分退款冲正 | `refund_lifecycle.finalize(refund_scope=after_sales)` → `commerce_refund.refund_gear_after_sales` | 按 `(已退货款+本次退款额)/现金实付` 比例冲回已赚积分；按 `(已退商品额+本次商品额)/订单额` 比例返还已用积分；`after_sales_marker` 幂等 |
| 退款入口分工 | `app.py` | 装备退款归**平台** `/api/platform/refunds|orders/{id}/refund`；`/api/club/{id}/refunds` 仅活动退款（by-design，非缺陷） |

### ★ 测试设计不变量（避免魔法数字 / seed 脏数据）
探针证实隔离库 user 1 初始带 **1280** 装备积分（seed），故一律用以下不变量判定，而非绝对余额：

- **I1 余额守恒**：任一用户 `gear_point_accounts.balance == 初始基线 + SUM(gear_point_ledger.amount)`（把模拟消费也写成负流水）。
- **I2 返回值 ↔ 流水一致**：退款返回的 `points.returnedRedeemedGearPoints` == `SUM(refund_redeem_return[source_id=订单])`；`earnedGearPointsToReverse` == `|SUM(refund_earn_reverse[source_id=订单])|`。
- **I3 幂等**：重复 finalize 后 balance 不变、ledger 条数不变、返回 `idempotent=true`。
- **I4 非负**：`balance >= 0` 恒成立。
- **I5 欠账**：应冲回 > 可用余额时，`point_adjustment_debt.amount == 差额`，且余额不为负。

---

## 1. 采购 / 入库 / 上架 / 库存（ECOM）

### TC-ECOM-001 · P0 · 采购收货幂等，不重复入库
- **Arrange**：建供应商 → 建 draft 商品（规格 红/M）→ 建采购单 50 件 → approve → order → supplier-shipment。
- **Act**：`POST /receive` 同一 `requestKey` 连发两次。
- **Assert**：商品 `stock == 50`（不是 100）；`supplier_payables` 中该 `receiptId` 恰好 1 条；`purchase_receipt_items` 保留规格维度。

### TC-ECOM-002 · P1 · 收货不自动发布商品
- **Act**：查 `GET /api/platform/products`。
- **Assert**：状态为 `pending_listing`，不是 `active`；C 端不可下单。

### TC-ECOM-003 · P1 · 上架前置校验
- **Act**：先只 `PATCH {status:active}`，再补全 `{price, image_url, description, status:active}`。
- **Assert**：第一次 400/409，第二次 200 且状态 `active`。

### TC-ECOM-004 · P0 · 结算锁库存
- **Act**：`POST /api/public/clubs/1/gear-checkout` 下单 2 件。
- **Assert**：可售库存减 2，且 `warehouse_sku_reservations` 有 reserved 记录。

### TC-ECOM-005 · P0 · 支付冻结成交单价
- **Act**：checkout 后把商品改价，再 `POST /pay {simulateSuccess:true}`。
- **Assert**：`gear_order_items.unit_price` 仍是下单时价格，不受改价影响。

### TC-ECOM-006 · P1 · 取消未支付订单释放库存与锁定积分（双重释放防护）
- **Act**：带 `gearPoints` 下单 → `POST /cancel`。
- **Assert**：可售库存回补；`hold` 被 `hold_release` 抵消；I1 守恒。

### TC-ECOM-007 · P2 · 采购退货（B 端）不影响 C 端装备积分
- **Act**：`POST /api/platform/procurement/purchase-returns` → approve → ship。
- **Assert**：只扣该规格库存；`gear_point_ledger` 无任何新增；I1 守恒。

---

## 2. 装备积分发放与抵扣（GP-EARN）

### TC-GP-001 · P0 · 购买自动发放积分
- **Assert**：`pay.result.gearPointsEarned == int(cashPaid × gear_points_rate)`；ledger 有 `earn` 且 `source_id=订单`；余额增加该值（I1）。

### TC-GP-002 · P0 · 积分抵扣即时扣减（hold）
- **Act**：带 `gearPoints` 结算。
- **Assert**：`quote.gearPointsUsed == min(请求数, 余额, 可抵上限)`；checkout 后余额减少该值；ledger 有 `hold(-N)`；**尚未支付时** `point_redemptions` 无该订单记录。

### TC-GP-003 · P0 · 支付后 hold 转正式抵扣
- **Assert**：ledger 有 `redeem_commit(0)`；`point_redemptions` 有 `(gear, gear, points_used=N)`；`cash_value == N / gear_points_redeem_rate`。

---

## 3. 全额退款的积分自动增加 / 自动扣减（核心，GP-REFUND）

### TC-GP-004 · P0 · 全额退款「自动增加」：返还已抵扣积分
- **Arrange**：订单使用了积分抵扣（探针：1470 分抵 ¥14.7）。
- **Act**：`POST /api/platform/orders/{id}/refund`。
- **Assert**：余额 `+1470`；ledger 有 `refund_redeem_return(+1470)`；返回 `points.returnedRedeemedGearPoints == 1470`（I2）。

### TC-GP-005 · P0 · 全额退款「自动扣减」：冲回已赚积分
- **Assert**：余额 `-175`；ledger 有 `refund_earn_reverse(-175)`；返回 `earnedGearPointsToReverse == 175`（I2）。

### TC-GP-006 · P0 · 全额退款积分净额守恒（回到下单前）
- **Assert**：退款后余额 == 该单下单前余额；I1 全程守恒；订单 `status=refunded`。

### TC-GP-007 · P0 · 全额退款幂等（防重复加/扣分）
- **Act**：同一订单再退一次。
- **Assert**：返回 `idempotent:true`；balance 不变；ledger 条数不变（I3）。

### TC-GP-008 · P1 · 标准退款链路同样冲正积分
- **Act**：`POST /api/public/orders/{id}/refund-request` → `POST /api/platform/refunds/{rid}/approve`（local 回调 finalize）。
- **Assert**：纯现金单赚的 190 分被 `refund_earn_reverse(-190)` 冲回；`refund_requests.status=succeeded`。

### TC-GP-014 · P1 · 退款副作用联动：库存/佣金/AI Credits 一并冲回
- **Assert**：`refund_restock` 回补库存；`commission_ledger` 有负向 `refund_reverse`；`ai_credit_ledger` 有 `mall_reward_reverse`；返回字段与流水一致。

---

## 4. 售后（退款 / 退货）的部分冲正（GP-AS）

### TC-GP-009 · P1 · 售后部分退款按现金比例冲回已赚积分
- **Arrange**：3 件订单实付 ¥285 赚 285 分；只退 1 件（¥100）。
- **Act**：`POST /after-sales` → `POST /api/platform/after-sales/{cid}/approve {refundAmount:100}` → `POST /api/platform/after-sales/{cid}/refund`。
- **Assert**：`gearPointsEarnedReversed == 100`；ledger `after_sales_earn_reverse(-100)` + `after_sales_marker(0)`；余额 `-100`（I1）。

### TC-GP-010 · P1 · 售后部分退款按商品占比返还已抵扣积分（自动增加）
- **Arrange**：订单既用了积分抵扣，又只退部分商品。
- **Assert**：`gearPointsReturned > 0` 且与 `goods/total` 比例一致；ledger `after_sales_redeem_return(>0)`；I1 守恒。

### TC-GP-011 · P1 · 售后退款幂等
- **Act**：重复 `POST /after-sales/{cid}/refund`。
- **Assert**：无重复 `after_sales_marker`；余额不再变化（I3）。

### TC-GP-013 · P2 · 退货（return_refund）与仅退款（refund_only）积分口径一致
- **Act**：走 `submit_return` → `receive_return` → approve → refund。
- **Assert**：最终积分冲正结果与等额 `refund_only` 一致；实物回库恰好一次。

---

## 5. 边界 / 资金安全（GP-EDGE）

### TC-GP-012 · P1 · 余额不足形成积分欠账，且余额不为负（I4/I5）
- **Arrange**：订单赚 N 分，模拟用户已把积分消费掉（写负流水把余额归零，保持 I1）。
- **Act**：全额退款。
- **Assert**：`point_adjustment_debt` 有 `(point_type=gear, amount=N)`；余额保持 0 不为负；不出现「扣成负数」。

### TC-GP-015 · P2 · 重复支付不重复发积分
- **Act**：同一 checkout 再 `POST /pay`。
- **Assert**：返回 `idempotent:true`；`earn` 流水不新增。

### TC-GP-016 · P2 · 拒绝退款不冲正积分
- **Act**：`POST /api/platform/refunds/{rid}/reject`。
- **Assert**：订单回到可退款态；积分余额/流水不变。

### TC-GP-017 · P3 · 装备退款不被俱乐部端活动退款接口误处理
- **Assert**：`GET /api/club/1/refunds` 不含装备退款单（by-design 分工）。

---

## 6. 潜在缺陷观察项（代码走查发现）

| ID | 观察 | 影响 | 验证结论 |
|---|---|---|---|
| OBS-01 | `points.reverse_gear_order` 的返还循环里 `if already: break` 在**同一订单存在多条 gear redemption** 时，第二条之后会被跳过 | 多笔抵扣场景少返还积分 | **已取证为 BUG-001（P2）→ 已修复并关闭**：改为「总抵扣额 − 已返还额 ＝ 待返还差额」一次性入账；TC-GP-019（含重入幂等）纳入主套件，22/22 全绿。见 `tests/e2e/reproduce-BUG-001.py` |
| OBS-02 | `refund_gear_after_sales` 的商品占比基数是 `after_sales_items.requested_amount`（原价），而 `order.total` 是会员折扣后金额，口径可能不一致 | 部分退款返还比例偏高（有 `min(1.0,…)` 兜底） | TC-GP-010 / 018 实测累计不超额，当前不判缺陷，建议产品确认口径 |
| OBS-03 | 装备积分初始余额来自 seed（1280），所有断言须相对化 | 绝对值断言必然误报 | 已用 I1（余额守恒）规避 |

> 执行阶段追加 **TC-GP-018**（同单两次部分售后累计冲正不超额）与 **TC-GP-019**（多抵扣流水异常数据健壮性）。BUG-001 修复后 TC-GP-019 已升级为主套件自动用例（`tests/ecommerce_gear_points_regression.py`），不再是独立取证项。

---

## 7. 质量门禁（Google QA 标准）

| 指标 | 门禁 | 判定（BUG-001 修复后复验） |
|---|---|---|
| 用例执行率 | 100% | ✅ 22/22 + 交叉回归 29/29 |
| 通过率 | ≥ 80% | ✅ 100%（主套件 22/22） |
| P0 缺陷 | = 0 | ✅ 0 |
| P1 缺陷 | ≤ 5 | ✅ 0 |
| 遗留缺陷 | — | ✅ 0（BUG-001 已 Closed） |
| 覆盖率（电商+积分域） | ≥ 80% | ✅ 9 个子域全覆盖 |
| 安全（资金/积分不变量） | 100% | ✅ I1/I2/I3/I4/I5 全过，异常数据场景由 TC-GP-019 覆盖 |

**门禁总判定：PASS（无遗留缺陷，可发版）**。完整结论见 `QA-REPORT.md`。
