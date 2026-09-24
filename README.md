# ClubOS NEW · v0.25 — Production Hardening (代码验收版)

在 v0.24 基线上增加服务端身份/RBAC、俱乐部与会员对象所有权检查、领队团期授权、CSRF、会话撤销、生产配置 fail-closed、生产禁用 Demo 支付捷径与未经验签资金事件、公开图片白名单、审计记录、限速、上传边界、在线 SQLite 备份。

- 本地回归：`./scripts/regression_v025.sh`（v0.8～v0.25）。
- 生产配置示例：`.env.production.example`；账户工具：`scripts/auth_account_v025.py`；上线门槛：`PRODUCTION_HARDENING_V025.md`。
- **并不代表可直接正式上线**：真实 Medusa PostgreSQL E2E、微信/支付宝支付与退款、TLS/监控/备份恢复/压测均仍需现场验证。
- 生产不得使用随包附带的演示 `clubos.db`；`CLUBOS_SECURITY_MODE=production` 必须显式配置。

---

# ClubOS NEW · v0.24 — Club Business Intelligence

新增俱乐部经营驾驶舱：活动收入与已确认退款、报名参加人、会员与重复付费、Club Points、商城归因 GMV/佣金/真实打款、AI Credits 用量与时间趋势。

- 页面：`/club → 经营数据`（7 / 30 / 90 / 365 天）
- 只读 API：`GET /api/club/{club_id}/analytics?windowDays=30`
- 口径/隔离说明：`CLUB_BUSINESS_INTELLIGENCE_V024.md`
- 回归：`./scripts/regression_v024.sh`
- 现有平台商品、采购、WMS、售后、佣金、AI Credits Domain 逻辑不变。
- v0.16 Medusa 实际 npm/PostgreSQL 运行验收仍独立待执行；现有端点生产 RBAC 仍待正式验收。

---

# ClubOS NEW · v0.23

当前代码基线：**ClubOS NEW v0.23 · Platform Club & AI Credits**。

v0.23 补齐总平台“俱乐部准入 + AI Credits 商业化”主线：公开入驻申请、审核/启停、可编辑套餐与充值包、月度账单、付款确认、Credits 发放与欠账自动回收、AI Provider 成本/用量与平台经营汇总。Credits 只负责计费，不参与模型降级。详见 `PLATFORM_CLUB_AI_CREDITS_V023.md`。

## v0.23 新增

1. 俱乐部 `pending / active / disabled / rejected` 准入状态。
2. 公开入驻申请 API，Platform Admin 审核后才启用。
3. AI Credits 套餐、充值包、订阅、账单与付款凭证。
4. 未付款账单绝不发 Credits；付款确认按 `paymentRef` 幂等。
5. AI Credits 欠账不会让钱包变负；未来正向 Credits 自动优先偿还欠账。
6. AI 成功后扣费；失败调用不正常扣费；余额绝不改变模型能力。
7. Platform 可查看 AI 收入、Provider 成本、Credits 用量、待付款账单与欠账。
8. Club 只查看自己的套餐、充值、账单和用量，不暴露平台 Provider 成本。
9. v0.22 → v0.23 保留历史余额、库存、订单、活动和 WMS 事实。

## v0.23 回归测试

```bash
./scripts/regression_v023.sh
```

覆盖 `smoke.py` 与 v0.8 ~ v0.23。

# ClubOS NEW · Commerce v0.22

当前代码基线：**ClubOS NEW Commerce v0.22 · Commerce Analytics & Replenishment**。

v0.22 在 v0.21 的供应商财务与真实利润基础上，把订单、WMS、采购、售后、成本和 `sourceClubId` 汇总成平台商城经营驾驶舱，并提供不会越过采购审批的智能补货建议。详见 `COMMERCE_ANALYTICS_REPLENISHMENT_V022.md`。

## v0.22 新增

1. Commerce Dashboard：窗口 Gear GMV、订单、售后率、库存金额、库存覆盖、缺货风险、慢动销、贡献利润。
2. SKU Analytics：销量速度、GMV、毛利、WMS `on_hand / reserved / available`、在途、Days Cover、预计缺货天数。
3. Replenishment：结合销量窗口、Supplier Lead Time、安全天数、目标覆盖、在途采购与 MOQ 计算建议补货量。
4. 没有近期销量时不机械自动补货；低库存只提示人工复核，避免慢动销越补越多。
5. 一键补货只创建 `draft` Purchase Order，仍必须走 v0.19 的 `approve → ordered → receipt`。
6. Club Analytics：来源订单、商城 GMV、净销售、客单价、退款率、佣金、平台贡献利润。
7. Supplier Analytics：到货率、实际交期、按时率、采购退货率、采购价差、未付/逾期应付。
8. Platform Admin 新增“商城经营 / 补货”页面；供应商成本与平台利润仍不开放给俱乐部/C端。
9. v0.21 → v0.22 不新增第二套交易事实表，不改历史库存/订单；分析实时读取现有 Domain 数据。

## v0.22 回归测试

```bash
./scripts/regression_v022.sh
```

覆盖 `smoke.py` 与 v0.8 ~ v0.22。

# ClubOS NEW · Commerce v0.21

当前代码基线：**ClubOS NEW Commerce v0.21 · Supplier Settlement & Merchandise Profit**。

v0.21 在 v0.20 WMS Lite 上补齐供应商应付、账期、付款分摊、采购退货贷项、采购价差，以及 Gear Order 成交成本快照与真实贡献利润。详见 `SUPPLIER_FINANCE_PROFIT_V021.md`。

## v0.21 新增

1. 真实 GRN 到货才形成供应商应付，采购单草稿/审核/下单不形成应付。
2. Supplier Payable 支持账期、逾期、部分付款、FIFO 付款分摊与 `paymentRef` 幂等。
3. 到货可录入实际单价，记录 `ordered_unit_cost / unit_cost / price_variance`。
4. 采购退货：`draft → approved → shipped → credited`。
5. 采购退货真实出库后减少 WMS / 可售库存；供应商确认贷项后自动冲抵未结应付。
6. `gear_order_items.unit_cost_snapshot` 冻结成交时平均成本，未来采购价变化不改历史利润。
7. 订单利润纳入 COGS、积分补贴、福利补贴、净佣金、物流成本、包材成本和退款。
8. Platform Admin 新增“供应商结算 / 利润”页面；Club/C端看不到采购成本与平台利润。
9. v0.20 → v0.21 兼容迁移保留原库存、WMS、订单，补齐历史成本快照与供应商财务表。

## 回归测试

```bash
./scripts/regression_v021.sh
```

覆盖 `smoke.py` 与 v0.8 ~ v0.21。

# ClubOS NEW · Commerce v0.20

本版新增 WMS Lite：仓库 / 库位 / 实物库存 / 锁定库存 / 移库 / 盘点 / 拣货 / 打包 / 发货出库 / 退货回库。详见 `WMS_V020.md`。

# ClubOS NEW · Commerce v0.19

当前代码基线：**ClubOS NEW Commerce v0.19 · Supplier / Procurement / Inbound Inventory**。

v0.19 承接 v0.18 装备售后闭环，把统一装备商城继续向平台供应链端推进：供应商、商品供货关系、采购单、部分/全部到货、采购入库、库存流水与采购成本正式进入 ClubOS Domain。

## v0.19 新增

1. Supplier 供应商主数据：编码、联系人、付款条件、默认交期、启停状态。
2. Product ↔ Supplier 供货关系：Supplier SKU、采购价、MOQ、交期、主供应商。
3. Purchase Order：`draft → approved → ordered → partially_received → received`。
4. Goods Receipt：只有真实到货确认才增加库存；支持分批到货。
5. Inventory Movement：期初、采购入库、销售出库、退款回库、售后退货、盘盈盘亏全部有流水。
6. 商品采购成本：`average_cost / last_purchase_cost / last_inbound_at`。
7. 补货点：`reorder_point`。
8. v0.18 以前的现有库存自动形成 `opening_balance`，不会伪造历史采购单。
9. 原有商品直接修改 `stock` 的兼容入口保留，但实际改为库存调整流水。
10. 采购入库、售后退货入库会同步最终库存到 Medusa；Medusa 同步失败不回滚现实入库事实。
11. Platform Admin 新增“供应链”界面；俱乐部和 C 端没有供应商、采购价、采购单、入库权限。

详见 `SUPPLY_CHAIN_V019.md`。

## 既有核心版本

- **v0.18**：仅退款 / 退货退款 / 换货 / 部分退款 / 退货物流 / 平台售后。
- **v0.17**：商城佣金 `pending → frozen → available → settled / reversed` 与跨期退款冲抵。
- **v0.16**：Medusa Gear Commerce Product / Inventory / Cart / Order / Fulfillment / Shipment / Delivered 接入结构。
- **v0.15**：微信支付 / 支付宝 Payment Provider 架构。
- **v0.8~v0.14**：会员、积分、福利券、支付退款、多参加人、活动执行、单参加人部分退款。

## 不变的架构边界

### ClubOS Domain

- Activity / Occurrence / Registration / Execution
- Club Points / Gear Points
- 福利券及 funding owner
- `sourceClubId`
- Gear 佣金与结算
- Gear 售后规则
- Supplier / Procurement / Goods Receipt / Inventory Ledger
- AI Credits
- Payment Provider 验签与退款业务状态

### Medusa = Gear Commerce Engine only

- Gear Product / Variant / SKU / Price
- Inventory Item / Stock Location projection
- Cart / Line Item
- Commerce Order
- Fulfillment / Shipment / Delivered

**Activity 不进入 Medusa；Supplier / Purchase Order / 采购成本也不下沉 Medusa。**

## 回归测试

```bash
./scripts/regression_v019.sh
```

覆盖 `smoke.py` 与 v0.8 ~ v0.19。

## Medusa Runtime 状态

v0.19 不改变此前的验收口径：Medusa 真实 Runtime 只有在可联网环境实际完成 npm install、PostgreSQL migration、build/start 和真实 Product → Cart → Payment → Order → Fulfillment → Shipment → Delivered → Refund E2E 后，才可标记 completed。

`.github/workflows/v016-medusa-runtime.yml` 继续承担真实 Medusa Runtime gate；`.github/workflows/v019-regression.yml` 负责 v0.19 ClubOS Domain 全回归。

## UI/UX Experience Upgrade（v0.25 后端不变）

本版前端已接入 `static/ux/` 的分组导航、响应式设计系统与高频业务结构化表单；详见 `UI_UX_DESIGN_SYSTEM.md` 和 `UI_UX_ACCEPTANCE.md`。可单独打开根目录 `ClubOS_NEW_UIUX_Interactive_Preview.html` 审核设计（离线演示、非真实后端）。执行 `python tests/ux_smoke_v025.py` 验证静态接线与 JS 语法。正式账号、支付、Medusa、备份/恢复等仍需真实 Staging 验收。

### UI/UX 第二轮补全

本项目当前包含第一轮和第二轮前端交互升级。打开根目录 `ClubOS_NEW_UIUX_Full_Interactive_Preview.html` 可以在离线浏览器里切换总平台、俱乐部与 C 端，体验采购、移库、盘点、内容生成和用户下单的**本地模拟流程**。真实项目三端及领队页已接入 `static/ux/` 的增强模块。详细边界、操作目录和验收方式见 `UI_UX_COMPLETE_PHASE2.md`。
