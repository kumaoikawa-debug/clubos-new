# ClubOS NEW v0.13 · 活动执行中心

v0.13 把 ClubOS 从“报名完成”推进到“活动真正出发并完成”。执行域按 **Occurrence / 团期** 管理，因为同一个 Activity 可以有多个日期、价格和不同的实际执行情况。

## 1. 执行主链

```text
报名支付成功
→ 参加人资料
→ 出发准备
→ 保险处理
→ 车辆 / 领队分组
→ 集合通知
→ 出发签到
→ 已出发
→ 进行中
→ 已完成
```

执行状态：`preparing -> departed -> in_progress -> completed`。状态只允许向前推进，已完成不能回退。

## 2. 出发准备看板

每个团期汇总：

- 已售名额
- 实名参加人数
- 报名资料待补人数
- 保险待处理人数
- 退款处理中数量
- 已签到 / 未到人数
- 未分配车辆人数
- 当前 readiness issues

`isReady` 只是运营提示，不是强制阻塞。真实活动中俱乐部可根据现场情况继续执行。

## 3. 集合与应急信息

`occurrence_execution_settings` 保存：

- meeting_time
- meeting_location
- emergency_phone
- leader_note

这些数据属于团期执行，不写回活动销售详情，也不属于 Medusa。

## 4. 领队与分组

支持：

- occurrence leaders
- vehicle group
- leader group
- room group
- custom group

参加人通过 `participant_group_assignments` 分配到分组。一个参加人在同一种 group_type 下只能属于一个组，防止同时被分到两辆车。

## 5. 保险

v0.13 支持：

- 每位参加人独立保险状态
- 批量标记 submitted
- 按团期导出保险 CSV
- 继续支持逐人更新 insured / failed / not_required

真实保险 API 暂不冒充已接入；后续 provider adapter 可替换“导出 + 回填”流程。

## 6. 通知

`activity_notices` 记录：

- title
- content
- audience
- channel
- draft / sent
- sent_at

v0.13 只完成“通知内容与发送状态记录”。SMS / 微信服务号 / 小程序订阅消息真实发送后接，不在本版伪造成功。

## 7. 签到

`participant_checkins` 目前支持 departure check-in：

- pending
- checked_in
- no_show
- cancelled

签到按真正 Participant 管理，不按付款订单管理。

## 8. 领队移动执行页

入口：

```text
/leader?occurrence={occurrenceId}&club_id={clubId}
```

领队只看到当前团期执行所需：

- 集合 / 应急信息
- 已发送通知
- 参加人
- 车辆分组
- 报名资料完整度
- 保险状态
- 签到
- 执行状态

不会显示 ClubOS 商城、会员消费、AI Credits、俱乐部经营数据。

> v0.13 Demo 用 query string 标识 club_id；生产环境必须改为领队登录 + occurrence assignment 鉴权。

## 9. 与其他系统边界

- Medusa：支付 / 商城 /订单等 Commerce Primitive
- ClubOS Registration：付款人与成交报名
- Participant：真正参加的人
- Execution Center：成交后的现场执行
- Platform Admin：不进入俱乐部活动执行流程

总平台仍然不参与俱乐部活动经营。

## 10. v0.13 暂不包含

- 微信 / 短信真实通知 Provider
- 保险 API 自动投保
- 实时 GPS / 轨迹
- 复杂车辆调度优化
- 房间自动排布算法
- 单参加人部分退款（计划独立版本处理）
- 生产级领队鉴权
