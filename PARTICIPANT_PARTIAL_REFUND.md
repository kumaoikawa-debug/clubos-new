# ClubOS v0.14｜单参加人退出 / 部分退款

## 目标

一笔多人报名订单允许其中某一位参加人单独退出，而不破坏其他同行人的报名、名额、积分、会员、福利和执行数据。

## 核心模型

- `Registration`：付款订单。
- `RegistrationParticipant`：真正参加活动的人。
- `ParticipantFinancialAllocation`：该参加人在成交订单中的资金/积分分摊快照。
- `RefundRequest.refund_scope`：`full` 或 `participant`。

每位参加人在支付成功时固定保存：

- 原价分摊
- 现金实付分摊
- Club Points 使用量与折扣金额
- Gear Points 使用量与平台补贴金额
- 俱乐部福利金额
- 平台福利金额
- 本单产生的 Club Points 分摊

分摊在成交时冻结，后续退款不重新计算历史成交结构。

## 单参加人退款流程

```text
用户选择某位参加人退出
→ 按该参加人的现金分摊 + 活动退款规则计算退款
→ 创建 participant-scope RefundRequest
→ 俱乐部审核
→ 支付渠道部分退款
→ refund-succeeded
→ 该参加人状态改为 refunded
→ 返还该参加人分摊的 Club Points / Gear Points
→ 冲回该参加人分摊的活动消费积分
→ 释放 1 个团期名额
→ 移除该参加人的执行分组
→ 重算会员数据
```

其他参加人保持有效，Registration 仍为 `paid`，订单退款状态为 `partial`。

## 福利券规则

福利券属于订单级资产，不在部分退出时拆成“半张券”。

- 部分退出：只记录该参加人的福利资金分摊，不重新发券。
- 仍有参加人：原福利券保持 `used`。
- 最后一位参加人也退款成功：整单关闭，原福利券恢复为 `issued`。
- 原兑换积分不同时返还，避免双重补偿。

## 全单退款与部分退款互斥

如果订单已经发生任意参加人退款或存在单人退款处理中：

- 禁止再走旧的整单退款流程。
- 剩余参加人应逐一退出。

这样避免整单积分冲正与部分积分冲正重复执行。

## 名额与执行

- 每成功退款 1 位参加人，`ActivityOccurrence.sold - 1`。
- 退款成功前仍占名额。
- 退款成功后 Participant 从执行名单移除，并删除车辆/领队组等分组关系。
- 已退款参加人仍保留在订单历史和审计记录中。

## 会员口径

部分退款后，会员活动消费只统计仍然有效参加人的：

```text
现金实付分摊
+ Gear Points 平台补贴分摊
+ Platform Benefit 平台补贴分摊
```

俱乐部承担的 Club Points / Club Benefit 不计入会员消费。

## API

- `GET /api/public/registrations/{registration_id}/participants/{participant_id}/refund-quote`
- `POST /api/public/registrations/{registration_id}/participants/{participant_id}/refund-request`
- 审核继续复用：
  - `POST /api/club/{club_id}/refunds/{refund_id}/approve`
  - `POST /api/club/{club_id}/refunds/{refund_id}/reject`
- 支付渠道退款成功继续复用：
  - `POST /api/commerce/events/refund-succeeded`

## 当前边界

- 当前 ActivityOccurrence 每位参加人的基础单价相同，因此金额按人数确定性分摊。
- 若未来支持儿童价 / 成人价 / 会员价同时存在，应升级为 participant-level price item，在成交时直接保存每人的真实价格，而不是平均分摊。
- 订单级福利券部分退出暂不生成残值券；只有整单全部退出后恢复原券。
