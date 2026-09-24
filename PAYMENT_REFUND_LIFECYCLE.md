# Payment + Refund Lifecycle · v0.15

## Payment

```text
CheckoutIntent
→ Points / Benefits HOLD
→ PaymentAttempt(processing)
→ Payment Provider create payment
→ 用户完成支付
→ Provider signed callback
→ verify signature + account + order no + amount
→ PaymentAttempt(succeeded)
→ CheckoutIntent(paid)
→ Registration or GearOrder
→ Points / Benefits CONSUME
```

支付失败时 Checkout 保留，可重新创建 PaymentAttempt；取消/过期时释放积分与福利 HOLD。

### 收款账户

- Activity Checkout：俱乐部自己的 PaymentAccount。
- Gear Checkout：总平台 PaymentAccount。
- Zero-cash Checkout：不调用外部 Provider，直接走 ClubOS Zero Payment。

## Activity refund

```text
用户申请
→ 冻结 Refund Policy Snapshot
→ 俱乐部审核
→ 原俱乐部 PaymentAccount 发起退款
→ Provider refund succeeded
→ ClubOS finalize
→ 返积分 / 恢复福利 / 释放名额 / 重算会员
```

多人订单单参加人退款仍按 v0.14 分摊金额执行部分现金退款。

## Gear refund

```text
用户申请
→ 总平台审核
→ Platform PaymentAccount 发起退款
→ Provider succeeded
→ ClubOS finalize
→ Gear Points / 福利券 / 佣金 / AI Credits / 库存冲正
→ Medusa captured Payment refund mirror（仅 Commerce 账务同步）
```

## Callback Safety

生产现金订单只能由经过验签的 Provider 回调确认。

Browser return URL、前端 JS 状态、query string 都不能直接修改支付结果。

## v0.16 Gear + Medusa 补充

Gear 的现金支付仍由 v0.15 Payment Provider 验证。验证成功后，在 ClubOS 业务落单之前必须取得真实 Medusa Order ID：

```text
Provider signed callback
→ ClubOS verify
→ Medusa Gear Cart complete
→ Medusa Order ID
→ Medusa System Payment capture
→ CheckoutIntent paid
→ ClubOS GearOrder
→ Points / Benefits consume
```

支付渠道交易号保存在 PaymentAttempt；Gear `commerce_order_id` 保存 Medusa Order ID。`order.placed` 只是对账事件，不是支付凭证。


### v0.16 Refund reconciliation

Gear 的 Medusa refund 不负责决定“是否应该退款”。只有 Payment Provider 已确认退款成功后才执行。若 Medusa 暂时不可用，ClubOS 仍以 provider 成功为资金事实完成业务冲正，并记录 commerce refund sync error 供后续幂等重试。


## v0.18 Gear After-Sales

装备部分售后退款通过 `after_sales_cases` 建立商品级/数量级范围，生成 `refund_scope=after_sales` 的 RefundRequest。现金退款仍必须以原 Payment Provider 成功为事实依据；成功后 ClubOS 仅按售后范围进行 Gear Points / commission / AI Credits 冲正，并将同金额镜像到 Medusa Payment。
