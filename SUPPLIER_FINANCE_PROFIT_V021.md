# ClubOS NEW · v0.21 Supplier Settlement & Merchandise Profit

v0.21 在 v0.20 WMS Lite 之上补齐两件事：

1. **供应商应付与结算**：采购单本身不产生应付，只有真实到货 GRN 才形成供应商应付。
2. **商品真实利润**：Gear Order 成交时冻结商品成本快照，后续采购价变化不能改写历史订单利润。

## 1. 供应商应付

流程：

```text
PO 下单
→ 实际到货 GRN
→ 使用实际到货单价计算入库成本
→ 生成 Supplier Payable
→ 按供应商账期得到 due_at
→ 平台部分/全部付款
→ FIFO 分摊至最早到期应付
```

核心表：

- `supplier_payables`
- `supplier_payments`
- `supplier_payment_allocations`
- `supplier_credits`
- `supplier_credit_allocations`

供应商付款使用 `payment_ref` 幂等，避免重复登记。

## 2. 采购价差

采购单锁定 `purchase_order_items.unit_cost`，到货时允许录入真实 `unitCost`。

```text
price_variance = (actual_unit_cost - ordered_unit_cost) × received_qty
```

正数表示实际采购成本高于下单价，负数表示节省。

字段：

- `purchase_receipt_items.ordered_unit_cost`
- `purchase_receipt_items.unit_cost`
- `purchase_receipt_items.price_variance`

## 3. 采购退货 / 供应商贷项

状态：

```text
draft → approved → shipped → credited
```

- `approved`：平台确认退货。
- `shipped`：真实库存与 WMS 实物库存减少。
- `credited`：供应商确认贷项，形成 `supplier_credits`。
- 贷项自动 FIFO 冲抵未结供应商应付；若超出当前应付，余额保留给未来 GRN。

## 4. 历史成本快照

新增：

```text
gear_order_items.unit_cost_snapshot
```

新订单成交时取当时的 `products.average_cost`。

原则：**历史订单利润不能随未来采购成本变化而变化。**

v0.20 → v0.21 升级时，老订单没有真实历史成本，因此以升级时的当前 `average_cost` 作为明确的 migration approximation。之后不再变化。

## 5. 利润口径

订单净销售：

```text
Net Sales = Gross Sales - Refunded Goods Value
```

订单 COGS：

```text
COGS = 未回库商品数量 × unit_cost_snapshot
```

订单贡献利润：

```text
Contribution Profit
= Net Sales
- COGS
- Platform Gear Points Subsidy（按退款商品比例冲减）
- Platform Benefit Subsidy（整单退款后归零）
- Net Club Commission（含退款反向冲抵）
- Shipping Cost
- Packaging Cost
```

退款但实物未回库时，COGS 仍保留；只有真实回库/取消订单才减少 COGS。

## 6. Platform API

### Supplier Finance

- `GET /api/platform/finance/supplier-summary`
- `GET /api/platform/finance/suppliers/{supplier_id}`
- `POST /api/platform/finance/supplier-payments`

### Purchase Return

- `GET /api/platform/procurement/purchase-returns`
- `POST /api/platform/procurement/purchase-returns`
- `GET /api/platform/procurement/purchase-returns/{return_id}`
- `POST /api/platform/procurement/purchase-returns/{return_id}/approve`
- `POST /api/platform/procurement/purchase-returns/{return_id}/ship`
- `POST /api/platform/procurement/purchase-returns/{return_id}/credit`

### Profit

- `GET /api/platform/profit/summary`
- `GET /api/platform/profit/orders`
- `GET /api/platform/profit/products`
- `PATCH /api/platform/orders/{order_id}/operating-costs`

## 7. 权限边界

以上供应商账款、采购成本、采购退货和平台利润仅属于 **Platform Admin**。

俱乐部仍只能看到：

- 自己来源的 Gear Order
- 物流 / 售后
- 自己的佣金 / 结算

俱乐部不能看到供应商采购价、平台应付或平台真实毛利。
