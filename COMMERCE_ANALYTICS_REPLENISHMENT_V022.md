# ClubOS NEW · Commerce Analytics & Replenishment v0.22

v0.22 把 v0.19~v0.21 已经形成的采购、WMS、订单、售后、成本与利润事实汇总为平台商城经营驾驶舱，并给出可执行但不自动下单的补货建议。

## 1. 数据源

全部来自 ClubOS 已有真实 Domain 数据：

- Gear Order / Gear Order Item
- `sourceClubId`
- WMS `on_hand / reserved / available`
- Purchase Order / Goods Receipt / 在途采购
- Product Supplier / MOQ / Lead Time
- After-sales / 实际退货回库
- v0.21 `unit_cost_snapshot` / COGS / Contribution Profit
- Supplier Payable / Purchase Price Variance

不建立第二套商品、库存、订单或利润事实表。

## 2. 平台经营指标

新增：

- 指定窗口 Gear GMV / 订单数 / 净销量
- 库存金额
- 实物 / 锁定 / 可售库存
- 汇总库存覆盖天数
- 缺货风险 SKU 数
- 慢动销 SKU / 慢动销库存金额
- 售后率
- 平台贡献利润
- `每 100 活动报名 → Gear GMV`

## 3. 商品库存健康

每个 SKU 返回：

- 窗口销量、销售速度、GMV、毛利率
- `onHand / reserved / available`
- 在途采购量
- 当前平均成本与库存金额
- 预计库存覆盖天数 / 预计缺货天数
- 主供应商、MOQ、有效交期
- 目标库存与建议补货量
- 健康状态：`stockout / at_risk / replenish / needs_supplier / low_stock / slow_moving / healthy`

## 4. 补货算法

默认策略：

```text
销量窗口 = 30 天
目标覆盖 = 30 天
安全库存 = 7 天
慢动销阈值 = 60 天
```

核心计算：

```text
日均净销量 = 窗口净销量 / 销量窗口
目标库存 = 日均净销量 × (供应商交期 + 安全天数 + 目标覆盖天数)
计划库存 = 当前可售库存 + 已下单未到货数量
原始补货量 = max(0, 目标库存 - 计划库存)
最终建议 = 尊重 MOQ 后的补货数量
```

没有近期销量时，不因为人工 `reorder_point` 自动采购；只提示低库存/人工复核，避免给慢动销商品机械补货。

## 5. 执行边界

建议不会自动采购。Platform Admin 点击“生成采购单”后，只创建：

```text
Purchase Order status = draft
created_by = replenishment
```

仍必须经过原 v0.19 流程：

```text
draft → approved → ordered → receipt
```

所以 AI/算法不能越过平台采购审批，也不能直接增加库存。

## 6. 经营维度

### Club
- 来源订单数
- Gear GMV / 净销售
- 客单价
- 退款率
- 俱乐部佣金
- 平台贡献利润

### Supplier
- 供货 SKU 数
- 采购下单/到货量
- 到货率
- 平均实际交期
- 按时到货率
- 采购退货率
- 采购价差
- 未付 / 逾期应付

## 7. API

- `GET /api/platform/analytics/commerce`
- `GET /api/platform/analytics/products`
- `GET /api/platform/analytics/clubs`
- `GET /api/platform/analytics/suppliers`
- `GET /api/platform/replenishment/policy`
- `PATCH /api/platform/replenishment/policy`
- `GET /api/platform/replenishment/recommendations`
- `POST /api/platform/replenishment/products/{product_id}/create-po`

## 8. 权限边界

本版经营分析属于 Platform Mall。不会把 Club A/B 的私有活动经营数据开放给其他俱乐部；供应商采购价、库存成本、平台利润仍仅 Platform Admin 可见。
