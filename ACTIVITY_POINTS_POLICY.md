# ClubOS Activity Points Policy · v0.7

## 目标

每场活动由俱乐部老板决定是否参与积分体系。积分规则属于 ClubOS Domain Rules，不由 Medusa / Loyalty Plugin 决定。

## 一场活动的积分开关

```text
points_enabled
├─ earn_club_points
├─ accept_club_points
│  └─ club_points_max_discount_percent
└─ accept_gear_points
   └─ gear_points_max_discount_amount
```

### 规则

- `points_enabled = false`：本活动不产生 Club Points，也不允许 Club Points / Gear Points 抵扣。
- `earn_club_points = true`：支付成功后，按本次现金实付金额产生 Club Points；成本由俱乐部承担。
- `accept_club_points = true`：允许同一俱乐部的 Club Points 抵扣，可设置最高抵扣比例。
- `accept_gear_points = true`：仅表示俱乐部愿意接受；还必须满足总平台 Gear Points 活动补贴总开关已开启。
- Gear Points 抵活动的成本始终由总平台承担。

## 两级 Gear Points 权限

```text
总平台允许活动场景 Gear Points 补贴
              +
俱乐部本活动 accept_gear_points = true
              =
C端用户可以使用 Gear Points 抵扣
```

总平台关闭后，俱乐部活动里的 Gear Points 选项自动失效，但不会改掉俱乐部原始配置；平台重新开启后即可恢复生效。

## Checkout Snapshot

创建 `CheckoutIntent` 时必须保存 `points_policy_snapshot_json`。

原因：

1. 用户创建支付单时看到的是当时的价格和积分规则；
2. 老板之后修改活动积分规则，不能反向修改已经创建的支付单；
3. 支付成功后 Club Points 是否产生，也按 Checkout 当时的快照执行；
4. `Registration` 同样保存该快照，便于退款、审计和后续结算。

## 多团期

v0.7 第一版：积分规则属于 Activity，所有 `ActivityOccurrence` 默认继承。

暂不开放团期级覆盖，避免老板后台过度复杂。以后只有出现明确业务需求时再增加 `Occurrence override`。
