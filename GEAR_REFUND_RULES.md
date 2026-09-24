# Gear 订单退款冲正规则 · v0.9

## 正式顺序

```text
C端申请售后
↓
总平台 / Medusa 审核
↓
支付服务完成真实退款
↓
ClubOS 收到 confirmed refund event
↓
执行本地业务冲正
```

ClubOS 不应在支付服务未确认退款前，提前把订单当作退款完成。

## 全额退款需要冲正

1. 原订单使用的 Gear Points → 返还用户。
2. 原订单产生的 Gear Points → 从用户余额冲回。
3. 用户已花掉这部分 Gear Points → 记录 `point_adjustment_debt`。
4. 原订单俱乐部佣金 → 新增负向 commission ledger，不删除原流水。
5. 原订单奖励的 AI Credits → 冲回。
6. AI Credits 已经使用 → 记录 `ai_credit_adjustment_debt`。
7. 恢复本地库存镜像。
8. `gear_orders.status = refunded`。
9. 重复 refund event 必须幂等。

## 为什么不用“直接改余额”

所有冲正都必须留下 Ledger，后续才能审计：
- 用户积分为什么减少 / 增加
- 俱乐部佣金为什么冲回
- AI Credits 为什么减少
- 是否形成欠账

## v0.8 范围

当前实现全额退款。部分退款需要下一阶段增加：
- refund items
- refund quantity
- refund amount
- 对应 Gear Points earning 按比例冲回
- 佣金按退款商品行冲回
- AI Credits 奖励按退款后的有效 GMV 重算

## v0.9：Gear 福利券退款恢复

若 Gear 订单使用了 Platform gear coupon，整单退款后：

- 该 coupon 从 `used` 恢复为 `issued`；
- 原先兑换 coupon 所花的 Gear Points 不再次返还；
- 用户因此只恢复“券的使用权”，不会同时得到“券 + 原积分”的双重返还。
