# ClubOS NEW Commerce v0.20 · WMS Lite

## 库存口径
- `products.stock`：平台商城可售库存。
- `warehouse_inventory.on_hand`：仓库实物库存。
- `warehouse_inventory.reserved`：已被订单占用但尚未发货的实物库存。
- `available = on_hand - reserved`，正常情况下汇总可用库存与 `products.stock` 一致。

## 主链路
采购到货 → 中心仓入库 → 上架/移库 → 用户下单锁库 → 拣货 → 打包 → 发货出库 → 售后退货回库。

## 原则
ClubOS 保存仓储事实；Medusa 保存 Commerce/Inventory 投影。现实仓库动作不得因为 Medusa 暂时失败而被伪造回滚。
