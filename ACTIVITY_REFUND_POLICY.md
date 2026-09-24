# ClubOS NEW v0.11｜活动退款规则

## 1. 归属原则

活动退款规则属于俱乐部经营域，总平台不制定也不审核俱乐部活动退款。

每场 Activity 独立配置：

- 是否允许用户自主申请退款
- 距离团期开始时间的规则档位
- 每个档位对应的现金退款比例
- 活动开始后的现金退款比例
- 对 C 端展示的退款说明

团期默认继承 Activity 规则。第一版不做 Occurrence 单独覆盖。

## 2. 示例

```json
{
  "enabled": true,
  "rules": [
    {"minHoursBefore": 72, "cashRefundPercent": 100, "label": "出发前72小时及以上"},
    {"minHoursBefore": 24, "cashRefundPercent": 80, "label": "出发前24-72小时"},
    {"minHoursBefore": 0, "cashRefundPercent": 50, "label": "出发前24小时以内"}
  ],
  "afterStartCashRefundPercent": 0
}
```

特殊活动不可退：`enabled=false`。

## 3. v0.11 资金处理

退款比例只作用于用户现金实付：

```text
现金实付 ¥400
退款比例 80%
→ 支付渠道退款 ¥320
→ 俱乐部保留取消费 ¥80
```

非现金资产在退款成功后完整恢复/冲正：

- 本单使用的 Club Points：全部返还
- 本单使用的 Gear Points：全部返还
- 本单已核销福利券：恢复可使用
- 本单产生的活动积分：全部冲回
- 团期名额：释放
- 会员累计活动消费/次数：重算

这样可以避免在第一版引入“半张福利券、部分积分返还”等难以解释的资产状态。

## 4. 规则快照

用户提交退款申请时，系统立即保存：

- 原现金实付
- 退款比例
- 应退款现金
- 取消费
- 命中的时间规则
- 完整退款政策快照

之后俱乐部修改活动退款规则，不会改写已经提交的退款申请。

## 5. API

- `GET /api/club/{club_id}/activities/{activity_id}/refund-policy`
- `PATCH /api/club/{club_id}/activities/{activity_id}/refund-policy`
- `GET /api/public/registrations/{registration_id}/refund-quote`
- `POST /api/public/registrations/{registration_id}/refund-request`
