# ClubOS NEW v0.19 · Supplier / Procurement / Inbound Inventory

## 1. 目标

v0.19 把装备商城库存从一个可编辑的 `products.stock` 数字，升级为可审计的供应链闭环：

```text
供应商
→ 商品供货关系 / 采购价 / MOQ / 交期
→ 采购单 Draft
→ 平台审核 Approved
→ 确认下单 Ordered
→ 部分到货 Partially Received
→ 全部到货 Received
→ 采购入库 GRN
→ Inventory Movement
→ products.stock / average_cost
→ Medusa Inventory Level projection
```

## 2. 责任边界

### ClubOS Platform Domain owns

- Supplier 主数据
- 商品 ↔ 供应商关系
- Supplier SKU
- 采购价 / MOQ / 采购交期
- Purchase Order 状态机
- 实际到货事实
- Goods Receipt / 入库批次
- 本地库存流水
- 平均采购成本 / 最近采购成本
- 盘点调整
- 采购和库存审计

### Medusa owns only the Gear commerce inventory projection

Medusa 继续负责 Product / Variant / Inventory Item / Stock Location 等 Commerce Primitive。

**重要：仓库已经实际签收的采购入库不能因为 Medusa 暂时不可用而回滚。**

ClubOS 先持久化真实入库事实，再将最终 on-hand stock 同步到 Medusa；同步失败记录在商品 `commerce_sync_status / commerce_sync_error`，后续可以重试。

## 3. Purchase Order 状态机

```text
draft
  ↓ approve
approved
  ↓ order
ordered
  ↓ receive partial
partially_received
  ↓ receive remaining
received
```

无入库记录的 `draft / approved / ordered` 可以取消为 `cancelled`。

已经发生部分入库的采购单不允许直接取消，避免抹掉真实库存历史。

## 4. 库存流水

`inventory_movements` 记录：

- `opening_balance`：v0.19 升级承接旧库存
- `purchase_inbound`：采购到货入库
- `sale_outbound`：装备订单销售出库
- `refund_restock`：兼容全额退款回库
- `after_sales_return`：售后实物退回入库
- `manual_in`：盘盈 / 人工调增
- `manual_out`：盘亏 / 人工调减

每笔流水保存：

```text
product_id
movement_type
quantity_delta
stock_before
stock_after
unit_cost
total_cost
reference_type
reference_id
note
actor_type
idempotency_key
created_at
```

## 5. 采购成本

商品新增：

- `average_cost`
- `last_purchase_cost`
- `last_inbound_at`
- `reorder_point`

如果升级前商品没有历史成本，第一次真实采购入库将采购单价作为第一笔已知平均成本；之后按库存数量进行加权平均。

## 6. v0.19 API

### Supplier

- `GET /api/platform/suppliers`
- `POST /api/platform/suppliers`
- `GET /api/platform/suppliers/{supplier_id}`
- `PATCH /api/platform/suppliers/{supplier_id}`

### Product sourcing

- `GET /api/platform/products/{product_id}/suppliers`
- `POST /api/platform/products/{product_id}/suppliers`

### Procurement

- `GET /api/platform/procurement/purchase-orders`
- `POST /api/platform/procurement/purchase-orders`
- `GET /api/platform/procurement/purchase-orders/{po_id}`
- `POST /api/platform/procurement/purchase-orders/{po_id}/approve`
- `POST /api/platform/procurement/purchase-orders/{po_id}/order`
- `POST /api/platform/procurement/purchase-orders/{po_id}/receive`
- `POST /api/platform/procurement/purchase-orders/{po_id}/cancel`
- `GET /api/platform/procurement/receipts`

### Inventory

- `GET /api/platform/inventory/summary`
- `GET /api/platform/inventory/movements`
- `POST /api/platform/inventory/adjustments`

旧的 `PATCH /api/platform/products/{product_id}` 仍兼容 `stock` 字段，但 v0.19 会把差额自动记录成库存调整流水，不再静默修改库存。

## 7. 权限边界

供应商、采购单、采购价、库存成本、入库和盘点属于 **Platform Admin**。

Club Admin 与 C 端：

- 不能创建供应商
- 不能看采购价 / 库存成本
- 不能建采购单
- 不能执行入库
- 不能调整库存

俱乐部仍只可查看/推荐平台在售商品，并查看自己来源的销售订单与佣金。

## 8. 验收

```bash
python tests/smoke_v19.py
./scripts/regression_v019.sh
```

专项验收覆盖：

```text
legacy opening balance
→ supplier
→ product sourcing / MOQ
→ PO draft
→ approve
→ ordered
→ partial receipt
→ final receipt
→ average cost
→ inventory movement
→ manual adjustment
→ sale outbound
→ refund restock
→ after-sales return restock
```
