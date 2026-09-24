# ClubOS NEW v0.23 · Platform Club & AI Credits

## 1. 目标

v0.23 不扩商城交易底层，而是补齐 ClubOS 总平台另一条主线：**俱乐部准入 + AI Credits 商业化**。

总平台仍然不参与俱乐部活动、客户、会员和日常经营。总平台只负责：

- 俱乐部入驻申请、审核、启用、停用；
- AI Credits 套餐、充值包、账单、付款确认；
- 商城增长奖励、平台调整、退款欠账；
- AI 调用用量和 Provider 成本观测；
- AI Credits 任务计费规则。

## 2. Credits 原则

> AI Credits 只负责计费，绝不参与模型降级。

余额、套餐和欠账不得用于：

- 降低模型级别；
- 减少上传图片；
- 截断上下文；
- 强制套模板；
- 降低输出质量。

余额只决定“本次成功调用是否允许扣费”。失败调用不正常扣 Credits。

## 3. 俱乐部状态

```text
pending → active → disabled
   └────────→ rejected
```

- `pending`：已提交入驻，不能调用 AI。
- `active`：可使用 ClubOS AI 与充值能力。
- `disabled`：暂停新的 AI 调用和充值订单；历史内容、余额、账单、使用记录保留。
- `rejected`：申请未通过。

公开入驻：

- `POST /api/public/club-applications`
- `GET /api/public/club-applications/{id}`

## 4. 套餐与充值

新增：

- `ai_credit_plans`
- `ai_credit_topup_packages`
- `club_ai_subscriptions`
- `ai_credit_orders`

v0.23 内置的是**可编辑 Demo 默认值**，不是写死的商业政策：

- Starter：¥299 / 3,000 Credits
- Pro：¥699 / 10,000 Credits
- Enterprise：30,000 Credits / 价格定制

充值包同样由 Platform Admin 可编辑。

分配套餐时只生成 `pending` 账单，**不会直接增加余额**。只有平台记录真实付款凭证后：

```text
pending AI Credit Order
→ confirm-paid(paymentRef)
→ paid
→ Credits ledger
→ wallet balance
```

`paymentRef` 唯一，重复确认幂等。

## 5. 月度续费

`POST /api/platform/ai-credit/monthly-roll`

为 `active + auto_renew` 的订阅生成当月套餐账单。`club_id + subscription + period_key` 唯一，因此重复运行不会生成双份月账单。

本版只负责账单与付款确认闭环；真实微信/支付宝自动扣款可以继续复用平台 Payment Provider，在正式支付接入时对接 `confirm-paid`。

## 6. 欠账回收

商城奖励退款时，如果俱乐部已经花掉奖励 Credits，系统已有 `ai_credit_adjustment_debt`。

v0.23 新规则：以后任何正向 Credits（套餐、充值、商城奖励、平台正向调整）都会先偿还未结欠账。

例如：

```text
欠账 530
充值 +1,000
→ Ledger +1,000 topup
→ Ledger -530 debt_recovery
→ 可用余额实际 +470
→ 欠账归零
```

钱包不会静默进入负数。

## 7. AI 成本与平台经营

`ai_usage_records` 继续保存：

- Provider / Model
- input/output tokens
- success / failed
- Provider Cost
- Credits Charged

平台新增 AI Credits Summary：

- 本期套餐/充值收入（CNY）
- Provider Cost（USD）
- 配置汇率后的成本参考（CNY）
- Accounting Gross Profit
- Credits 发放 / 消耗
- 未偿欠账
- 待付款 AI Credits 账单
- Active Subscriptions

`ai_usd_cny_rate` 是平台内部**可配置核算汇率**，不是实时 FX 数据。

俱乐部自己的 Statement 不暴露 Provider 成本，只展示自己的套餐、账单、Credits、调用次数与用量。

## 8. 核心 API

### Platform

- `GET /api/platform/clubs`
- `POST /api/platform/clubs`
- `PATCH /api/platform/clubs/{id}/status`
- `PATCH /api/platform/clubs/{id}/plan`
- `GET/POST /api/platform/ai-credit/plans`
- `GET/POST /api/platform/ai-credit/topup-packages`
- `GET /api/platform/ai-credit/orders`
- `POST /api/platform/ai-credit/orders/{id}/confirm-paid`
- `POST /api/platform/ai-credit/monthly-roll`
- `GET /api/platform/ai-credit/summary`
- `GET/PATCH /api/platform/ai-credit/task-pricing`
- `GET /api/platform/ai-credit/clubs/{id}/statement`

### Club

- `GET /api/club/{id}/credits`
- `GET /api/club/{id}/credits/statement`
- `POST /api/club/{id}/credits/topups`

## 9. 迁移规则

v0.22 → v0.23：

- 不修改历史 AI Credits `balance`；
- 不修改 Gear Order、库存、WMS、活动和佣金交易事实；
- 为旧俱乐部建立订阅映射，但不伪造历史现金收入；
- 旧 `plan` 字段映射到可编辑套餐；
- 旧 AI Credits 流水保留；
- 只补充新的商业账务结构和欠账结算字段。

## 10. 回归

```bash
./scripts/regression_v023.sh
```

覆盖 `smoke.py` 与 v0.8 ~ v0.23。
