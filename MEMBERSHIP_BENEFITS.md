# ClubOS 会员等级与福利中心规则 · v0.9

## 1. 会员等级属于俱乐部

用户在 Club A 的会员等级，不影响 Club B。

每个俱乐部独立配置：
- 等级名称
- Rank
- 累计活动消费门槛
- 累计活动次数门槛
- `ANY`：任一门槛满足即可
- `ALL`：所有门槛同时满足
- 等级权益说明

系统选择用户满足的最高 Rank。

## 2. 会员消费统计口径

会员累计活动消费使用：

```text
用户现金实付
+
ClubOS 平台承担的 Gear Points 活动补贴
```

不计入：

```text
俱乐部自己承担的 Club Points 折扣
```

原因：平台补贴仍会形成俱乐部实际收入；Club Points 折扣是俱乐部自己的会员营销成本。

## 3. 两类福利

### Club Benefit
- `owner_type = CLUB`
- `points_type = club`
- 只能由对应俱乐部创建
- 只能消耗该俱乐部 Club Points
- `funding_owner = CLUB`

### Platform Benefit
- `owner_type = PLATFORM`
- `points_type = gear`
- 只能由总平台创建
- 可开放给全部俱乐部，或指定俱乐部展示
- 消耗用户平台 Gear Points
- `funding_owner = PLATFORM`

## 4. C端体验

C 端只看到一个“会员中心”：

```text
会员等级
活动积分余额
装备积分余额
可兑换福利
我的兑换
```

但每项福利必须明确标识“俱乐部承担 / 平台承担”。

## 5. 兑换

兑换成功后：
- 原子扣减对应积分
- 扣减库存（如果有限量）
- 创建 `benefit_redemptions`
- 生成唯一 `voucher_code`
- 保留 funding owner 和 cash value 供未来结算 / 核销

## 6. 红线

- 俱乐部不得创建 Gear Points 福利。
- 平台不得用 Gear Points 把成本转嫁给俱乐部。
- Club Points 不跨俱乐部。
- Gear Points 跟用户走，不归某个俱乐部。
- 福利中心不能把两类积分合并为一个余额。

## 7. v0.9：福利真正进入交易

`activity_coupon` 和 `gear_coupon` 不再只是兑换记录。

- Activity coupon 可进入活动 Checkout。
- Club activity coupon 成本归俱乐部。
- Platform activity coupon 成本归总平台。
- Gear coupon 只能由总平台提供，并进入 Gear Checkout。
- Checkout 创建后券先进入 `held`，支付成功才 `used`。
- 取消支付释放；整单退款重新变为 `issued`。

详见 `BENEFIT_CHECKOUT_RULES.md`。
