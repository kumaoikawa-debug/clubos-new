# ClubOS Transaction Rules v0.9

## 不可变规则

1. Club Points 与 Gear Points 永远是两本账。
2. Club Points 只能属于 `user + club`，活动/俱乐部福利消耗，成本由该俱乐部承担。
3. Gear Points 属于平台用户，装备商城消费产生；装备抵扣或平台授权福利由总平台承担。
4. AI Credits 属于 B 端俱乐部，与 C 端积分完全隔离。
5. 任何积分抵扣必须经过 `quote -> hold -> paid/restore` 生命周期，不能在支付前永久扣减。
6. 活动报名只有支付成功后才占用最终名额并产生 Club Points。
7. 装备订单只有支付成功后才产生 Gear Points、俱乐部佣金和商城 AI Credits 奖励。
8. Gear Points 的平台补贴不应降低俱乐部佣金计算基数；佣金按商品原始合格 GMV 计算。
9. 新积分按现金实付计算，防止“用积分买积分”的循环放大。
10. Medusa 是 Commerce Primitive，不是 ClubOS 业务规则来源。
11. 每场 Activity 由俱乐部自主决定是否参与积分体系。
12. “产生 Club Points”与“允许 Club Points 抵扣”必须分开控制。
13. Gear Points 用于活动必须同时满足：总平台允许补贴 + 俱乐部本活动开启。
14. Club Points 抵扣可以设置单场活动最高抵扣比例；Gear Points 可以设置单场活动最高平台补贴金额。
15. Checkout 创建时保存活动积分规则快照；后续活动规则修改不得改变已创建的支付单。
16. Registration 保存同一份积分规则快照，退款和审计以成交时规则为准。
17. 第一版积分规则属于 Activity，所有团期默认继承，不做 Occurrence 级覆盖。

## v0.8 补充：会员与福利

- 会员等级属于 `club_id`，不能跨俱乐部共享。
- 会员消费统计采用 `cash paid + platform Gear Points subsidy`，不包含 Club Points 俱乐部自费折扣。
- Club Benefit 只能消耗 Club Points，Funding Owner = CLUB。
- Platform Benefit 只能消耗 Gear Points，Funding Owner = PLATFORM。
- 福利兑换必须保留独立 Redemption Ledger / Voucher，不得只改积分余额。

## v0.8 补充：Gear 全额退款

- 真实退款由 Commerce / Payment 层先完成。
- ClubOS 在 confirmed refund 后反向处理 Gear Points、佣金、AI Credits、库存镜像。
- 已被消费的待冲回积分 / AI Credits 不静默丢失，形成 adjustment debt。
- 冲正通过负向 Ledger 保留历史，不删除原收入 / 奖励流水。
- refund callback 必须幂等。

## v0.9 补充：福利券进入交易

18. 福利兑换与订单核销是两个阶段：积分在兑换福利时扣除，券在交易时核销。
19. 福利券也必须遵循 `issued -> held -> used / release`，不能在支付前永久核销。
20. Club activity coupon 的成本只能归 CLUB；Platform activity coupon / gear coupon 的成本归 PLATFORM。
21. Gear 订单不接受 Club-funded voucher。
22. Activity Checkout 必须分别保存：Club Points 折扣、Gear Points 平台补贴、Club Benefit 折扣、Platform Benefit 补贴。
23. 会员累计活动消费计入平台补贴，不计入俱乐部自费折扣。
24. 整单退款恢复已使用 voucher；原兑换积分不同时返还，防止双重退款。
25. Medusa 只接收 ClubOS 已计算完成的最终现金应付金额，不决定 Funding Owner。

## v0.10 补充：支付与退款状态机

26. `CheckoutIntent` 创建不等于支付成功；只有 Payment Provider 成功事件才能推进正式成交。
27. 每次支付尝试必须独立记录为 `PaymentAttempt`，失败后允许重试，不能覆盖历史失败记录。
28. 单次支付失败不自动释放积分与券；Checkout 仍可重试。只有取消/过期才释放 hold。
29. Payment webhook 必须幂等，同一个 provider event 不能重复生成 Registration / Gear Order。
30. 活动退款与 Gear 退款统一进入 `RefundRequest` 状态机，不允许点击退款按钮后直接静默改余额。
31. 活动退款审核权属于俱乐部；总平台不参与俱乐部经营决策。
32. Gear 商品退款审核/售后权属于总平台；俱乐部只查看进度。
33. 审核通过只代表进入 `processing`，真实资金退款成功前不得执行 ClubOS 业务冲正。
34. 只有 `refund-succeeded` 事件后才返还积分、恢复券、释放活动名额、冲回佣金/AI Credits等奖励。
35. v0.10 仅支持整单退款，部分退款不得用整单冲正逻辑模拟。

## v0.11 补充：活动退款政策

- 活动退款政策由俱乐部设置，总平台不参与。
- 同一 Activity 的团期默认继承同一规则。
- 退款规则按退款申请创建时的时间判断，不按审核时间重新计算。
- 创建退款申请即保存退款政策快照。
- 取消费仅从现金实付中扣除。
- 退款成功后，本单使用的 Club Points / Gear Points / 福利券完整恢复。
- 本单产生的活动积分完整冲回。
- 特殊活动可设置为不可自主退款。
- 活动与 Gear 在 C 端统一展示订单进度，但审核权限不合并。

## v0.12 补充：报名人 / 参加人

36. Registration 表示付款/交易订单；真正参加活动的人必须拆为 RegistrationParticipant。
37. 一名付款人可为多人报名，订单原价 = 团期单价 × 实际参加人数。
38. ActivityOccurrence 的 sold 表示已成交参加人数，不再表示订单数。
39. 一位参加人占一个名额；整单退款按 participant_count 一次性释放对应名额。
40. 活动积分与会员消费仍归付款人账户，不因同行人数量拆成多本积分账。
41. 普通补资料接口不得直接更换参加人姓名/手机号；身份更换必须走 Replace Participant 并保留审计记录。
42. Participant Policy 在 Checkout / Registration 中保存快照，后续活动规则修改不得改变已成交订单的报名资料规则。
43. 参加人保险状态属于俱乐部活动执行域，总平台不参与。
44. 身份证件等敏感字段正式上线必须加密、脱敏、鉴权和审计。
45. v0.12 不用整单退款逻辑模拟“部分同行人退出”；单人退名额必须在后续版本设计独立部分退款和积分分摊规则。

## v0.13 补充：活动执行域

46. 活动成交后进入 Execution Domain；商城 / Medusa 不负责活动现场执行。
47. 执行数据必须按 `ActivityOccurrence` 隔离，同一个 Activity 的不同日期可以有不同领队、车辆、集合信息和执行状态。
48. ActivityOccurrence 的销售状态与执行状态分开：`status` 管可售，`execution_status` 管现场生命周期。
49. Participant 是签到、保险、分车和现场执行的最小单位，不能用付款订单替代参加人。
50. 执行状态只允许 `preparing -> departed -> in_progress -> completed` 向前推进，已完成不能回退。
51. 保险属于俱乐部活动执行域；总平台不介入投保决策。
52. 同一参加人在同一种分组类型下只能属于一个组，防止同时分配两辆车/两个领队组。
53. 通知状态必须留痕；没有真实 Provider 成功回执时，不得冒充短信/微信已发送。
54. 出发准备 readiness 是运营提醒，不作为支付、退款或俱乐部现场决策的强制阻塞。
55. 领队执行端只暴露本团期所需最小信息，不暴露会员消费、Gear 商城、AI Credits 或平台数据。

## v0.14 补充：单参加人退出 / 部分退款

56. 多人订单支付成功时必须为每位 Participant 保存不可变的成交资金分摊快照。
57. 单参加人退款只处理该参加人的现金、Club Points、Gear Points、活动积分与资金承担分摊，不影响其他同行人。
58. 单人现金退款按照该参加人的 `allocated_cash_paid` 套用活动退款政策，不按整单现金重新计算。
59. Club Points / Gear Points 可以按参加人分摊并在单人退款成功后部分返还。
60. 本单产生的 Club Points 也必须按参加人分摊；单人退出只冲回其分摊部分，余额不足时继续形成积分欠账。
61. 每成功退款一位参加人，只释放 1 个 ActivityOccurrence 名额。
62. Participant 只有在 Provider 退款成功后才从 `active` 改为 `refunded`；审核中仍占名额。
63. 订单级福利券不在部分退出时拆分或重发；只有所有参加人都退款成功时才恢复原券。
64. 福利的 Club / Platform Funding Allocation 必须保留到 participant allocation，用于后续结算审计。
65. 一旦订单发生参加人级退款，禁止再使用整单退款逻辑，以免积分、券和名额重复冲正。
66. 最后一位有效参加人退款成功后，Registration 才整体进入 `refunded`，Checkout 才进入完整退款状态。
67. 已退款 Participant 保留历史，不做物理删除；订单中心必须可查看其退出与退款记录。
68. 会员累计活动消费按仍有效 Participant 的资金分摊计算，避免部分退款后仍按原整单消费累计等级。

---

## v0.15 · 支付账户与真实 Provider 红线

1. 活动报名现金默认进入对应俱乐部自己的 PaymentAccount。
2. Gear 商城现金默认进入 ClubOS Platform PaymentAccount。
3. 总平台不因为技术接入统一而默认代收俱乐部活动款。
4. PaymentAccount 数据库只保存 credential_ref，不保存私钥/APIv3 Key。
5. 生产现金订单只有“验签成功的 Provider 回调”才能确认 paid。
6. Browser return URL 不具备资金状态修改权。
7. 微信/支付宝回调必须同时校验 merchant order / account / amount。
8. Provider event 必须幂等。
9. 活动退款使用原活动收款账户；Gear 退款使用平台收款账户。
10. 100% 积分/福利抵扣的零现金订单不调用外部支付 Provider。

---

## v0.16 Gear Commerce Authority

When `COMMERCE_PROVIDER=medusa`:

- Medusa is authoritative for gear Product / Variant / Inventory / Cart / Order / Fulfillment.
- ClubOS is authoritative for source-club attribution, Gear Points, platform subsidy, club commission, AI Credit reward and reversal rules.
- ClubOS local stock/order fields are projections for UI and business ledger use; they must not become a second independent commerce truth.
- A Medusa order must retain `clubos_checkout_intent_id` and `clubos_product_id` metadata so callbacks and fulfillment mappings remain deterministic.

### v0.16 payment/order separation

- Activity Checkout must never create a Medusa Cart or Medusa Order.
- Gear payment authority remains ClubOS Payment Provider verification; Medusa `order.placed` is reconciliation-only.
- For Gear, provider transaction IDs belong to PaymentAttempt; `commerce_order_id` must be the actual Medusa Order ID.
- Gear Points, commission and AI Credits can materialize only through ClubOS Domain after payment verification and successful Medusa order creation.
- Medusa must not contain ClubOS Booking or Points domain modules.

## v0.17 · Gear Commission Settlement

Gear 佣金属于 ClubOS Domain，不属于 Medusa。

```text
paid order -> pending
order delivered -> frozen
after-sales window elapsed -> available
platform payout confirmed -> settled
```

结算前退款：原 earn 转 `reversed`，不形成应付款。

结算后退款：历史 settlement 保持不变，新建负向 `refund_reverse / available` 流水，在后续结算中抵扣。

平台不得在 `netAmount <= 0` 时创建打款结算；负向余额作为 `carryDebt` 延续到后续周期。
