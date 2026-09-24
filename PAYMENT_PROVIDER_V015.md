# ClubOS Payment Provider · v0.15

## 1. 收款归属

```text
活动报名
→ ClubOS Checkout
→ resolve club_id
→ 俱乐部自己的 PaymentAccount
→ 微信 / 支付宝

Gear 商城
→ ClubOS Checkout
→ Platform PaymentAccount
→ 微信 / 支付宝
```

活动收入与装备收入从支付入口就分开，不做“平台统一代收后再分账”的默认模式。

## 2. PaymentAccount

数据库只保存：

- scope_type: club / platform
- scope_id
- provider
- channel
- merchant_id
- app_id
- credential_ref
- enabled
- metadata

数据库禁止保存：

- 商户私钥
- APIv3 Key
- 支付宝应用私钥
- 支付宝公钥原文

真正密钥通过 `credential_ref` 指向部署环境 Secret。

## 3. 微信支付 v3

已实现：

- Native
- JSAPI
- H5
- RSA2048 请求签名
- 回调 RSA 验签
- APIv3 AES-GCM resource 解密
- 支付金额核对
- out_trade_no → PaymentAttempt 对账
- 微信退款创建
- 退款结果回调

## 4. 支付宝

已实现：

- WAP pay
- Page pay
- RSA2 请求签名
- RSA2 notify 验签
- app_id 校验
- total_amount 校验
- alipay.trade.refund 调用

## 5. 成交规则

浏览器跳回 ClubOS 不代表支付成功。

只有：

```text
Provider signed callback
+ 正确 PaymentAccount
+ 正确 merchant order no
+ 正确金额
+ 幂等事件未处理
```

全部成立后才能：

```text
PaymentAttempt → succeeded
CheckoutIntent → paid
→ Registration / GearOrder
→ 积分、福利、佣金等后续业务
```

## 6. 退款规则

Activity：

```text
用户申请
→ 俱乐部审核
→ 使用原活动收款账户发起退款
→ Provider 成功
→ ClubOS finalize
```

Gear：

```text
用户申请
→ 总平台审核
→ 使用 Platform 收款账户发起退款
→ Provider 成功
→ ClubOS finalize
```

这样资金责任与前面 ClubOS 商业边界保持一致。

## 7. 生产上线前必须补齐

- HTTPS 公网回调域名
- 微信 / 支付宝正式商户配置
- Secret Manager / KMS
- 证书 / 公钥轮换机制
- 微信 openid / JSAPI 授权链路
- 对账任务
- 支付异常补单任务
- 退款超时重试队列
- 财务审计导出

## 8. v0.16 与 Medusa 的衔接

v0.15 Payment Provider 的验签职责不下沉 Medusa。Gear 支付验签成功后，ClubOS 才允许 Medusa 将对应 Cart 完成成 Order；Activity 始终不进入 Medusa。

因此 WeChat / Alipay `transaction_id / trade_no` 是支付流水号，不是 Gear `commerce_order_id`。Gear `commerce_order_id` 必须保存真实 Medusa Order ID。
