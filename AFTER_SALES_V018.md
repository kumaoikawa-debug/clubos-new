# ClubOS NEW Commerce v0.18 · Gear After-Sales & Return Management

v0.18 补齐平台统一装备商城的售后闭环，不改变 v0.16/v0.17 已确定的 Commerce / 佣金边界。

## 责任边界

### C 端用户
- 发起仅退款 / 退货退款 / 换货
- 选择订单商品与数量
- 填写原因与凭证
- 退货类售后填写退货物流
- 查看售后进度

### Club Admin
- 只查看自己 `sourceClubId` 归因订单的售后进度
- 不审核、不退款、不改退货地址、不执行换货

### Platform Admin
- 统一审核装备售后
- 确认退货收货与是否入库
- 执行退款
- 执行换货发货
- 负责售后与佣金 / Gear Points / AI Credits 的联动

## 售后类型

- `refund_only`：仅退款，不要求商品退回，默认不恢复库存
- `return_refund`：退货退款，收到退货后才进入退款
- `exchange`：换货，收到退货后重新发货，不产生退款

## 状态机

```text
pending_review
  ├─ rejected
  ├─ refund_only -> approved_pending_refund -> refund_processing -> refunded
  ├─ return_refund -> awaiting_return -> return_in_transit -> approved_pending_refund -> refund_processing -> refunded
  └─ exchange -> awaiting_return -> return_in_transit -> exchange_pending_shipment -> completed
```

## 部分退款规则

v0.18 支持订单商品级、数量级部分退款：

- 退款现金按售后商品金额占订单总额比例，映射到该订单原现金实付
- 已使用 Gear Points 按累计退款商品比例返还
- 已赚 Gear Points 按累计现金退款比例冲回；余额不足形成积分欠账
- 俱乐部佣金按退款商品对应佣金金额冲回
- 已结算佣金形成下一期负向冲抵
- 商城奖励 AI Credits 按累计退款商品 GMV 比例冲回；不足形成 AI Credits 欠账
- 仅当整单商品均已退款，订单才进入 `refunded`，并恢复订单级福利券
- 部分退款保持订单主体状态不变，`refund_status=partial`

## 库存规则

- `refund_only` 默认不入库
- `return_refund` / `exchange` 由平台确认收货时决定是否 `restock`
- 已入库售后明细通过 `restocked=1` 保证幂等，不重复加库存

## Medusa / Payment Provider

支付渠道仍是现金退款事实来源。ClubOS 审核通过后：

```text
AfterSalesCase
-> RefundRequest(refund_scope=after_sales)
-> 原 Payment Provider 退款
-> provider refund succeeded
-> ClubOS 部分资产冲正
-> Medusa Payment refund mirror
```

Medusa mirror 失败只记录 reconciliation error，不回滚已经成功的渠道退款。

## 新接口

### C 端
- `POST /api/public/orders/{order_id}/after-sales`
- `GET /api/public/users/{user_id}/after-sales`
- `GET /api/public/after-sales/{case_id}`
- `POST /api/public/after-sales/{case_id}/return-shipment`

### Club Admin（只读）
- `GET /api/club/{club_id}/mall/after-sales`

### Platform Admin
- `GET /api/platform/after-sales`
- `GET /api/platform/after-sales/{case_id}`
- `POST /api/platform/after-sales/{case_id}/approve`
- `POST /api/platform/after-sales/{case_id}/reject`
- `POST /api/platform/after-sales/{case_id}/receive-return`
- `POST /api/platform/after-sales/{case_id}/refund`
- `POST /api/platform/after-sales/{case_id}/exchange-ship`

## 验收

```bash
python tests/smoke_v18.py
```

覆盖：仅退款部分退款、退货退款、退货物流、入库、整单退款、换货、俱乐部只读、平台统一售后。
