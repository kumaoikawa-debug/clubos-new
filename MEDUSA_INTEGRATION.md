# ClubOS NEW · Medusa Integration v0.16

## 1. 核心原则

**ClubOS Domain 决定业务规则，Medusa 只做 Gear Commerce Engine。**

### ClubOS 拥有

- Activity / Occurrence / Registration / Execution
- Club Points / Gear Points
- Benefit / funding owner
- `sourceClubId`
- commission
- AI Credits
- Payment Provider account ownership、验签、支付状态
- RefundRequest 与业务冲正

### Medusa 拥有

- Gear Product / Variant / SKU / Price
- Inventory Item / Stock Location Level
- Cart / Line Item
- Order
- Fulfillment / Shipment / Delivered

## 2. Activity 明确不进入 Medusa

v0.16 删除了早期 sidecar 中的 `clubos-booking` / `clubos-points` 模块，也删除 Activity 创建 Medusa Cart 的逻辑。

```text
Activity Checkout
→ ClubOS Quote
→ ClubOS Points / Benefits HOLD
→ Club PaymentAccount
→ Provider verified callback
→ ClubOS Registration
```

Activity 的日期、名额、积分规则、报名、参加人、现场执行都只属于 ClubOS Domain。

## 3. Gear Checkout

ClubOS 先计算：

```text
商品原价
- Gear Points 平台补贴
- Platform Benefit 补贴
= 最终现金价
```

然后创建 Medusa Cart。Medusa line-item metadata 至少保留：

- `clubos_checkout_intent_id`
- `clubos_product_id`

Cart metadata 保留：

- `clubos_checkout_intent_id`
- `clubos_kind=gear`
- `source_club_id`
- `user_id`

`sourceClubId` 只是归因信息；佣金算法仍在 ClubOS。

## 4. Payment → Order 的正确顺序

Medusa 不负责验证 ClubOS 的微信 / 支付宝商户回调。

```text
Gear CheckoutIntent
→ Medusa Cart
→ ClubOS Platform PaymentAccount
→ WeChat Pay / Alipay
→ ClubOS 验签 + merchant/account/amount 校验
→ POST /admin/clubos-paid-carts/{cart_id}/complete
→ Medusa Manual/System payment session
→ completeCartWorkflow
→ real Medusa Order ID
→ capture Medusa System Payment mirror
→ ClubOS confirm GearOrder
→ Gear Points / commission / AI Credits
```

所以：

- `provider_payment_id` 是支付渠道交易号；
- `commerce_order_id` 对 Gear 必须是 Medusa Order ID；
- 两者不得混用。

## 5. `order.placed` 只用于 reconciliation

Medusa subscriber 回调：

```text
POST /api/commerce/events/order-placed
```

这个事件：

- 可以核对 `checkoutId ↔ Medusa Order ID`；
- **不能**把未验证支付的 Checkout 标记 paid；
- **不能**独立发放积分、佣金或 AI Credits。

支付真相仍来自 ClubOS Payment Provider 的可信回调链。

## 6. Fulfillment

平台履约 API：

- `POST /api/platform/orders/{order_id}/fulfill`
- `POST /api/platform/orders/{order_id}/ship`
- `POST /api/platform/orders/{order_id}/delivered`

ClubOS 保存 `commerce_fulfillment_id`、物流单号、承运商以及同步状态，Medusa 保存 Commerce Fulfillment 真相。

## 7. 权限

Gear 商品属于总平台。

Club：

- 可查看；
- 可推荐；
- 可产生 `sourceClubId` 归因；
- 不可编辑 SKU、价格、库存、供应商、发货。

Medusa 自定义 paid-cart Admin route 已配置 Admin authentication；它不是公开 Store route。

## 8. Refund 镜像

退款业务规则仍由 ClubOS 决定，真实资金状态仍由微信/支付宝等 Payment Provider 决定。

```text
Provider refund succeeded
→ ClubOS finalize（Points / Benefits / commission / AI Credits / local projection）
→ Medusa captured Payment refund mirror
```

Medusa refund 是 Commerce 账务镜像，不反向成为支付真相。若镜像暂时失败，`refund_requests.commerce_refund_status/error` 记录同步失败；重复 provider callback 可以安全重试，已存在的 Medusa refund 金额会先被计算，避免重复退款。


## v0.19：Supplier / Procurement / Inventory Ledger 与 Medusa

供应商、采购单、采购价、MOQ、到货批次和采购成本全部属于 ClubOS Platform Domain，不进入 Medusa。

采购真实到货时：

```text
ClubOS Purchase Order
→ Goods Receipt
→ inventory_movements(purchase_inbound)
→ products.stock / average_cost
→ Medusa Inventory Level 最终库存镜像
```

Medusa 库存同步失败只标记 `commerce_sync_status=error`，不能回滚已发生的仓库到货事实。售后实物退回同理：ClubOS 先确认收货并写入库存流水，再同步 Medusa。
