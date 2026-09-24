# ClubOS NEW v0.24 · Club Business Intelligence

## 范围与边界

这是俱乐部自己的经营驾驶舱，不是总平台介入俱乐部运营的新后台。BI 全部按 `club_id` 读取 ClubOS Domain 中已经存在的报名、退款、会员、积分、商城归因、佣金与 AI Credits 账本，不复制订单，也不生成虚构的分析交易。

新增只读接口：`GET /api/club/{club_id}/analytics?windowDays=7|30|90|365`。响应包含 `period / activity / membership / commerce / ai / activities / trend / definitions`。前端为 `/club → 经营数据`；周期筛选与趋势、活动表现、会员和商城/AI 拆解均可直接查看。

### 核算定义

- 统计周期用 UTC、左闭右开；结束边界覆盖请求的当前秒。
- 本期活动实收：本期创建且支付成功的报名 `amount`，仅计入 `paid/refunded` 订单，不计待付款。
- 本期退款：`refund_requests(kind=activity,status=succeeded)` 按 `refunded_at` 落入本期计算，含往期报名在本期退款；未成功或仅申请退款不计入。
- 本期现金净流入 = 本期报名实收 - 本期成功现金退款。这是期间流水口径，不是活动利润。
- 本期新报名订单截至当前净实收 = 本期新报名实收 - 这些订单的全部已成功退款；另列这一 *cohort* 口径，避免跨期退款混淆。
- 人数按当前 active 参加人记录计，不把报名订单数当人数。
- 重复付费用户是同一俱乐部截至期末累计两次及以上成功付费报名、且在本期付费的独立用户；不是完整留存率或页面转化率。
- 俱乐部自费优惠 = Club Points 抵扣 + Club Benefit 折扣；平台补贴 = Gear Points 抵扣 + Platform Benefit 补贴，分别展示，不错误减到俱乐部现金实收两次。
- 商城 GMV 只按 `gear_orders.source_club_id` 做销售归因。商城履约、库存与利润仍属总平台；本期佣金账本变动、本期真实打款分开统计。
- AI 用量只返回俱乐部自己的调用次数、Credits 消耗、任务分布、余额/欠账，不返回 Provider 采购成本。
- 不计算活动净利润：当前还没有完整的活动资源 BOM/执行成本/人工费用账本。没有曝光日志时也不计算转化率。

### 安全与验收说明

所有查询包含俱乐部范围约束，跨俱乐部测试会插入两个租户与同一用户，验证活动、佣金、商城、AI 数据不会混入另一俱乐部。**这只是查询隔离，不等于已完成生产身份鉴权**：现有项目整体仍需正式认证、RBAC、API session 绑定、审计与联邦租户访问控制（原规划 v0.25 的上线前验收项）。不能把可猜测路径上的 `club_id` 当身份认证。

### 验收

`python tests/smoke_v24.py` 验证期间/订单 cohort、跨期实际退款、未成功退款不计入、部分退款、重复付费、佣金反向流水、AI 信息隔离、跨租户查询隔离、周期校验与 404。

`./scripts/regression_v024.sh` 运行原始 smoke 与 v0.8～v0.24 回归。

原 v0.16 Medusa PostgreSQL 真运行门槛依然独立存在，不能因这里的离线回归而宣称已通过。
