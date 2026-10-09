# ClubOS NEW · v0.12 报名人 / 参加人体系

## 核心原则

`付款人 != 参加人`。

一笔活动订单（Registration）属于付款人，但一笔订单可以包含多个真正参加活动的人（RegistrationParticipant）。每位参加人独立占用 1 个活动名额，并拥有自己的报名资料、紧急联系人、保险状态和变更记录。

## v0.12 数据关系

```text
User / Payer
  ↓
Registration（订单/付款）
  ↓ 1:N
RegistrationParticipant（实际参加人）
```

### Registration 保存

- payer user_id
- activity / occurrence
- participant_count
- 总价 / 实付 / 积分 / 福利
- 支付与退款状态
- Participant Policy Snapshot

### RegistrationParticipant 保存

- 姓名 / 手机
- 与付款人关系
- 证件类型 / 证件号码
- 紧急联系人 / 电话
- 资料完整状态
- 保险状态 / 保险公司 / 保单号
- replacement_count
- linked_user_id（如手机号已对应现有用户）

> 正式生产环境应对证件号码等身份字段做加密存储、权限控制、脱敏展示和审计。当前 Demo SQLite 为了开发验证使用明文字段。

## 多人报名计价

```text
团期单价 × 实际参加人数 = 活动原价
```

例如：

- 单价 ¥200
- 一次报名 3 人
- 原价 = ¥600

Club Points / Gear Points / 福利券仍按照整个订单进行结算，Funding Owner 规则不改变。

## 名额

Occurrence 的 `sold` 从“已支付订单数”改为“已支付参加人数”。

3 人订单支付成功：

```text
sold += 3
```

整单退款完成：

```text
sold -= 3
```

## 资料补充

默认支持“先付款，后补资料”：

支付前必须填写（平台级必填，2026-10-09 起不受「允许后补」开关影响）：

- 姓名
- 手机号
- 证件类型（选单：身份证 / 护照 / 台胞证 / 回乡证 / 往来港澳台通行证 / 居住证 / 外国人永居证 / 军官证 / 士兵证 / 出生医学证明 / 其他，清单与号码校验见 `clubos_domain/participants.py` 的 `ID_TYPES`，前端同规则实现在 `static/shared.js`）
- 证件号码（按类型校验格式；身份证 / 居住证校验 GB 11643 MOD 11-2 校验位）

俱乐部可配置「允许先付款、后补紧急联系人资料」；证件两项不在可后补范围内。

出发前完整资料默认包括：

- 姓名
- 手机号
- 证件类型
- 证件号码
- 紧急联系人
- 紧急联系人电话

## 转名额 / 更换参加人

更换参加人必须走独立 `replace` 路径，不能通过普通“补资料”接口直接修改姓名/手机号绕过审计。

替换后：

- 付款人不变
- Registration 不变
- Participant 资料更新
- replacement_count + 1
- 原保险状态重置，需要重新处理
- ParticipantChangeLog 保存前后快照

俱乐部可设置：

- 是否允许自助转名额
- 出发前多少小时停止自助转名额

## 保险

ClubOS 仅建立参加人级保险状态模型：

- pending
- submitted
- insured
- failed
- not_required

俱乐部后台可更新保险公司、保单号、状态。后续可再对接真实保险 API。

## API

### Activity Participant Policy

- `GET /api/club/{club_id}/activities/{activity_id}/participant-policy`
- `PATCH /api/club/{club_id}/activities/{activity_id}/participant-policy`

### C端参加人

- `GET /api/public/registrations/{registration_id}/participants`
- `PATCH /api/public/registrations/{registration_id}/participants/{participant_id}`
- `POST /api/public/registrations/{registration_id}/participants/{participant_id}/replace`

### 俱乐部执行端

- `GET /api/club/{club_id}/registrations/{registration_id}/participants`
- `GET /api/club/{club_id}/occurrences/{occurrence_id}/participants`
- `PATCH /api/club/{club_id}/participants/{participant_id}/insurance`

## 本版暂不做

- 单个同行人独立退款 / 部分退名额
- 单个同行人独立价格
- 支付前名额临时锁定（Seat Hold）
- 身份证 OCR / 实名认证
- 真实保险 API 投保

这些应在后续版本继续做，而不是用整单逻辑硬模拟。

## v0.13：参加人进入执行中心

支付成功后的 Participant 继续成为现场执行的唯一人员实体。保险、车辆/领队分组、签到全部引用 `participant_id`，不会重新复制一份“执行名单”。这样转名额后的新参加人会自然进入同一执行链，避免报名名单和现场名单分叉。

## v0.14：单参加人退出

v0.14 为每位参加人增加独立退款状态和成交资金分摊。多人订单中的某一位可以单独退出，退款成功后释放 1 个名额并退出执行名单，其他参加人的报名继续有效。详细规则见 `PARTICIPANT_PARTIAL_REFUND.md`。
