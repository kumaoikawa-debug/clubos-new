# ClubOS NEW · v0.17 Gear Commission & Settlement

## 1. 目标

v0.17 在 v0.16 的 `sourceClubId + GearOrder + Refund` 基础上补齐 **俱乐部商城佣金结算闭环**。

原则不变：

- Gear 商品与履约属于总平台。
- 俱乐部不是卖家，只获得来源归因与约定佣金。
- Medusa 不决定佣金规则，也不保存 ClubOS 结算状态机。
- 历史结算不能被退款“回写修改”；退款通过新的负向账务流水冲抵。

## 2. 佣金状态机

```text
Gear paid
→ pending
→ order delivered
→ frozen
→ after-sales window elapsed
→ available
→ platform payout recorded
→ settled
```

退款分两种：

```text
退款发生在 settled 前
→ earn 标记 reversed
→ refund_reverse 负向流水标记 reversed
→ 不进入可结算金额

退款发生在 settled 后
→ 历史 earn 保持 settled
→ 新建 refund_reverse 负向 available 流水
→ 自动抵扣下一期佣金
```

## 3. 规则

平台设置：

- `commission_after_sales_days`，默认 7 天，可配置 0~90 天。

签收后才开始售后冻结期。处于退款申请 `requested / processing` 的订单，即使冻结期已到，也不会释放为 `available`。

## 4. 数据模型

### commission_ledger 新增

- `frozen_at`
- `available_at`
- `settlement_id`
- `settled_at`

### commission_settlements

记录平台实际完成的一次俱乐部打款：

- `id`
- `club_id`
- `gross_amount`
- `deduction_amount`
- `net_amount`
- `entry_count`
- `status`
- `payment_ref`（唯一，作为幂等键/实际付款凭证）
- `note`
- `created_by`
- `created_at`
- `paid_at`

## 5. API

### 俱乐部只读

- `GET /api/club/{club_id}/mall/commission-summary`
- `GET /api/club/{club_id}/mall/commissions`
- `GET /api/club/{club_id}/mall/settlements`

### 总平台

- `GET /api/platform/commission-policy`
- `PATCH /api/platform/commission-policy`
- `POST /api/platform/commissions/release`
- `GET /api/platform/commission-summary`
- `GET /api/platform/commissions`
- `GET /api/platform/settlements`
- `GET /api/platform/settlements/preview?club_id=...`
- `POST /api/platform/settlements`

`POST /api/platform/settlements` 不负责银行实际转账。它用于平台在真实付款完成后，以 `paymentRef` 登记结算事实，并原子地把当期 `available` 流水标记为 `settled`。

## 6. 关键财务约束

### 结算前退款

不应支付该笔佣金。

### 结算后退款

不能删除或修改历史 settlement；系统生成负向 `available` 冲抵项。

例如：

```text
上期已结算佣金      +¥80
随后该订单退款      -¥80 available
本期新佣金         +¥200 available
本期实际应结算      ¥120
```

如果负向余额大于新佣金，则：

- `payableNow = 0`
- `carryDebt = 未覆盖的负向余额`

平台不能创建净额 <= 0 的打款结算。

## 7. v0.17 回归

```bash
./scripts/regression_v017.sh
```

覆盖：

- pending → frozen → available → settled
- paymentRef 幂等
- 结算前退款 reversed
- 结算后退款 next-period offset
- 负向余额自动抵扣下一期佣金
- 俱乐部只读视图
- v0.8~v0.16 全历史回归

## 8. 与 v0.16 Runtime 的关系

v0.17 已开发并通过本地回归，不代表 v0.16 的 Medusa 真运行验收已经完成。

v0.16 仍需在可联网 PostgreSQL + Medusa 2.21.1 环境通过 `.github/workflows/v016-medusa-runtime.yml` 的真实 Product → Cart → Payment → Order → Fulfillment → Shipment → Delivered → Refund E2E。
