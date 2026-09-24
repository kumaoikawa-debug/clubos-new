# ClubOS NEW · 开源底座使用边界

## 已选主底座：Medusa v2

用途：商品、SKU、价格、购物车、订单、库存、支付、履约、退换货等通用 Commerce primitives。

原则：不把 ClubOS 改造成 Medusa 的业务模型；通过 Adapter / Custom Module / Workflow 接入。

## 参考的官方示例

- Medusa Examples · Loyalty Points System
  - 只学习 custom module / service / workflow 的实现方式。
  - ClubOS 不采用单钱包设计，而是 Club Points + Gear Points 两套账户。
- Medusa Examples · Ticket Booking System
  - 只学习“业务模块 + Product/Variant + Order”的连接方式。
  - ClubOS 不采用 venue/seat 业务语义，改造成 Activity / Occurrence。

## 许可证注意

- Medusa core 的开源 commerce modules 使用 MIT；Enterprise Materials 有独立商业许可，ClubOS 当前不得依赖 Enterprise-only 代码。
- Medusa 官方 examples 为示例项目，使用时保留其原始许可证和版权要求。
- 第三方 booking 插件在接入前必须重新核对：仓库 LICENSE、npm metadata、当前 peerDependencies、维护活跃度与 Medusa 当前版本兼容性。不要因为“能 npm install”就直接成为核心依赖。

## 红线

任何开源项目与以下规则冲突时，以 ClubOS Domain Rules 为准，不允许修改 ClubOS 业务去迁就插件。
