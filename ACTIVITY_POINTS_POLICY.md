# ClubOS Activity Points Policy · v0.8

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

## v0.8：积分现金换算比例 `X 积分 = ¥1`（2026-11-05）

活动积分抵扣是**两步**，顺序不可颠倒：

```text
① 活动规则先算现金上限：  现金抵扣上限 = 订单金额 × club_points_max_discount_percent
② 积分按换算比例折成现金：实际抵扣现金 = 使用积分 ÷ X
                          所需积分上限 = 现金抵扣上限 × X
```

因此 **X 只决定「多少积分能抵满」，不改变「最多能抵多少钱」**。
例：¥498 的活动、最多抵 10% → 现金闸门恒为 ¥49.80；
`100:1` 需 4980 分，`50:1` 需 2490 分，`20:1` 只需 996 分，三者都只抵 ¥49.80。
调比例是「让高等级会员的积分更值钱」，不是「放开抵扣上限」，俱乐部成本仍由百分比锁死。

### 生效优先级

```text
会员当前等级专属 (club_member_tiers.points_redeem_rate)
        ↓ 未设(0)
俱乐部默认       (clubs.points_redeem_rate)
        ↓ 未设(0)
平台全局默认     (platform_settings.club_points_redeem_rate)
        ↓ 缺失/非法
100 积分 = ¥1
```

- `0` 是「跟随上一级」，不是「0 积分 = ¥1」；合法显式值为 `1~10000`。
- 存量库升级后两个新列全部落 `0`，**等价于升级前所有等级共用平台全局值，行为零变化**。
  之所以不给 `clubs` 写死默认 100：那会把平台曾经改过的全局比例（如 50）悄悄顶成 100，属于无声资损。
- 装备积分（Gear Points）由总平台出钱、准入与规则都归总平台，继续走
  `gear_points_redeem_rate`（平台全局 100:1），**不参与本优先级**。
- 积分商城 `points_cost` 是固定积分标价，不按此比例折现金；比例只用于活动报名抵扣。

### 落点与同源

| 位置 | 职责 |
| --- | --- |
| `clubos_domain/points_redeem_rate.py` | 唯一解析口径 `resolve()` / 后台说明 `describe()` / 入参 `validate_rate()` |
| `ClubOSPointsEngine.quote()` / `max_redeemable()` | 接收可选 `club_points_redeem_rate`，两步算法在一处实现 |
| `BookingEngine._quote_with_policy()` | 报价时解析比例，同时传给 `quote()` 与 `max_redeemable()`，并写进 `pointsPolicy.pointsRedeemRate` |
| `CheckoutEngine.create_activity_intent()` | **同一个 resolver、同一组参数**，并把比例写进 `points_policy_snapshot_json` |
| `GET/PATCH /api/club/{id}/points-redeem-rate` | 俱乐部默认读写 + 各等级生效一览 |
| `PATCH /api/club/{id}/membership/tiers/{tierId}` | `pointsRedeemRate` 写等级专属值；**不带该键的 PATCH 不覆盖已有值** |

`price-quote` 与 `create_activity_intent` 必须始终用同一个解析结果，否则会出现
「页面显示可抵 ¥30、下单按 0 抵扣」。成交快照记录 `pointsRedeemRate`，
日后运营改比例不会改写历史订单的口径。

回归：`tests/points_redeem_rate_smoke.py`（已纳入 `scripts/regression_v025.sh`）。
