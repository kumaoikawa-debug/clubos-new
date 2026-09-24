# ClubOS Benefit Checkout Rules · v0.9

v0.9 把“积分兑换福利”真正接入活动报名与装备订单。福利不再只是一个券码展示，而进入 `quote -> hold -> paid -> refund restore` 交易生命周期。

## 1. 两类资金来源保持不变

### Club Benefit
- 使用 Club Points 兑换
- `funding_owner = CLUB`
- 只能由对应俱乐部创建
- 可以做 `activity_coupon / service / gift`
- 不能创建 `gear_coupon`，因为装备商城由总平台统一经营

### Platform Benefit
- 使用 Gear Points 兑换
- `funding_owner = PLATFORM`
- 只能由总平台创建
- 可以做 `activity_coupon / gear_coupon / gift`
- 可面向全部俱乐部或指定俱乐部

## 2. 抵扣券生命周期

```text
积分兑换福利
  -> issued
选择到结算单
  -> held
支付成功
  -> used
支付取消/过期
  -> issued
整单退款
  -> issued
```

原积分在“兑换福利”时已经消耗。订单退款时不再退回原积分，而是把原福利券恢复为可使用状态，避免用户同时拿回积分和券形成双重返还。

## 3. 活动报名结算

活动报名允许同时存在：

```text
活动原价
- Club Points 直接抵扣（俱乐部承担）
- Gear Points 直接补贴（平台承担）
- Club activity_coupon（俱乐部承担）
- Platform activity_coupon（平台承担）
= 用户现金实付
```

四类金额必须分开保存，不能合并成一个 discount。

Registration 保存：
- `club_point_discount`
- `platform_point_subsidy`
- `club_benefit_discount`
- `platform_benefit_subsidy`
- `benefit_redemption_ids_json`

会员累计活动消费口径：

```text
现金实付
+ Gear Points 平台补贴
+ Platform activity_coupon 平台补贴
```

不计俱乐部承担的 Club Points / Club activity_coupon。

## 4. 装备订单结算

装备订单只能使用平台承担的 `gear_coupon`。Club Benefit 不能进入平台 Gear 订单。

```text
商品原价
- Gear Points 直接抵扣（平台承担）
- Platform gear_coupon（平台承担）
= 用户现金实付
```

俱乐部佣金仍按合格商品原始 GMV 计算，不因平台补贴减少。

## 5. 并发与占用

福利券在 Checkout 创建时必须原子从 `issued -> held` 并写入 `held_checkout_id`。

同一张券不能同时进入两个支付单。取消/过期必须释放；支付成功必须与订单/报名绑定。

## 6. 退款

活动全额退款：
- 原活动积分/装备积分按既有规则冲正
- 已用福利券恢复 `issued`
- 会员等级重新计算

Gear 全额退款：
- 退款现金由 Medusa/支付服务负责
- Gear Points、佣金、AI Credits、库存按 v0.8 规则冲正
- 已用 `gear_coupon` 恢复 `issued`

## 7. v0.9 暂不做

- 单券部分核销
- 一张券跨多个订单拆用
- 部分退款按比例恢复福利券
- 有效期/过期时间
- 多券叠加优先级配置

当前支持多券一起传入 Checkout，但每张券都是一次性全额权益，实际抵扣金额最多不超过剩余应付金额。
