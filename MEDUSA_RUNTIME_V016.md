# ClubOS NEW · Medusa Runtime v0.16

## 状态

**v0.16 仍处于开发 / 真实运行验收阶段。**

代码已经按 Medusa v2 Gear Commerce 流程收口，但“真实 Medusa 已部署成功”必须以 `npm install + PostgreSQL migration + build + start + E2E` 的实际结果为准。

## 1. 运行结构

```text
ClubOS FastAPI / Domain
        │
        ├─ Activity / Member / Points / Benefits / Payment rules
        │
        └─ Gear Commerce Adapter
                 │
                 ▼
            Medusa v2
     Product / Inventory / Cart
     Order / Fulfillment / Shipment
                 │
                 ▼
             PostgreSQL
```

Medusa sidecar 不再注册 ClubOS Booking / Points Domain 模块。

## 2. Gear 下单 E2E

```text
C端选 Gear
→ ClubOS Quote + HOLD
→ Medusa Cart
→ address + shipping method
→ ClubOS Platform PaymentAccount
→ 微信 / 支付宝真实支付
→ ClubOS 验签成功
→ ClubOS 调 /admin/clubos-paid-carts/{cart_id}/complete
→ Medusa System Payment Provider
→ Payment Collection / Session
→ completeCartWorkflow
→ Medusa Order
→ capturePaymentWorkflow（System Payment 账务镜像）
→ ClubOS GearOrder projection
→ Points / commission / AI Credits
→ order.placed reconciliation
```

Activity 不创建 Medusa Cart / Order。

## 3. 环境变量

ClubOS：

```env
COMMERCE_PROVIDER=medusa
MEDUSA_URL=http://localhost:9000
MEDUSA_ADMIN_TOKEN=...
MEDUSA_ADMIN_AUTH_TYPE=bearer   # 或 api-key
MEDUSA_PUBLISHABLE_KEY=...
MEDUSA_REGION_ID=reg_...
MEDUSA_SHIPPING_PROFILE_ID=sp_...
MEDUSA_SALES_CHANNEL_ID=sc_...
MEDUSA_STOCK_LOCATION_ID=sloc_...
MEDUSA_DEFAULT_SHIPPING_OPTION_ID=so_...
MEDUSA_INTERNAL_PAYMENT_PROVIDER_ID=pp_system_default
MEDUSA_CURRENCY_CODE=cny
CLUBOS_CALLBACK_URL=http://host.docker.internal:8000
CLUBOS_COMMERCE_WEBHOOK_SECRET=...
```

Medusa：

```env
DATABASE_URL=postgres://...
REDIS_URL=redis://...
STORE_CORS=...
ADMIN_CORS=...
AUTH_CORS=...
JWT_SECRET=...
COOKIE_SECRET=...
CLUBOS_CALLBACK_URL=...
CLUBOS_COMMERCE_WEBHOOK_SECRET=...
MEDUSA_INTERNAL_PAYMENT_PROVIDER_ID=pp_system_default
```

Manual/System Payment Provider 必须在 Gear 使用的 Region 中启用。该 Provider 只用于 Medusa 内部镜像“ClubOS 已验证成功”的外部支付，不取代微信/支付宝验签。

## 4. 必须准备的 Medusa 基础数据

- Region（CNY）
- Sales Channel
- Shipping Profile
- Stock Location
- Shipping Option
- Manual/System Payment Provider enabled in Region
- Publishable API Key 绑定 Sales Channel
- Admin API Token / authenticated admin credential

## 5. 真实验收命令

在 `services/medusa`：

```bash
npm install
npm run db:migrate
npm run build
npm run start
```

然后必须真实验证：

1. `/health`；
2. ClubOS 创建 Gear Product → Medusa Product / Variant；
3. Inventory Level 可读写；
4. Gear Cart 创建；
5. shipping address + shipping method；
6. 已验证外部支付映射为 System payment session；
7. Cart 完成得到真实 Medusa Order，并确认 System Payment 已 captured；
8. `order.placed` 回调只做 reconciliation；
9. Fulfillment；
10. Shipment + tracking；
11. Delivered；
12. Delivered 后仍可按 ClubOS 规则申请售后；
13. Provider refund succeeded 后 ClubOS 冲正 + Medusa captured Payment refund；
14. 重复 refund callback 不重复退款。

## 6. 当前代码级回归

`tests/smoke_v16.py` 使用 Mock Medusa HTTP 检查接口路径、payload 与 ClubOS 状态机，覆盖：

- Product / Variant / Inventory；
- Activity 不进入 Medusa；
- Gear Cart + shipping method；
- verified payment → Medusa Order；
- provider transaction id / Medusa Order ID 分离；
- 未验证 `order.placed` 不得确认支付；
- Fulfillment / Shipment / Delivered；
- System Payment capture；
- Medusa Payment refund mirror 与幂等重试状态。

这属于 **ClubOS 接入代码回归**，不等同于 Medusa 进程真实运行验收。


## 7. GitHub Actions 真实验收

新增 `.github/workflows/v016-medusa-runtime.yml`，使用真实 PostgreSQL 16 + Redis 7，并执行：

```text
npm install
→ medusa db:migrate
→ seed ClubOS CNY Region / Sales Channel / Shipping / Stock Location / Publishable Key
→ medusa user
→ medusa build
→ built server npm install
→ NODE_ENV=production medusa start
→ ClubOS start
→ tests/runtime_v16_medusa_e2e.py
→ v0.8 ~ v0.16 全回归
```

`runtime_v16_medusa_e2e.py` 不 monkeypatch Medusa；只有真实 Product / Variant / Inventory / Cart / captured Payment / Order / Fulfillment / Shipment / Delivered / Refund 全部产生真实 Medusa 对象才通过。

## 8. 完成定义

只有真实环境全部通过后，README 才能把 v0.16 从“开发中”改成“completed”。在此之前不得进入“v0.17 已开始”的口径。

## 9. 本次执行环境真实尝试（2026-09-23）

已实际检查：

- Node `v22.16.0`；
- npm `10.9.2`；
- 当前执行容器无 `psql` / `postgres`；
- 当前执行容器无 Docker；
- `registry.npmjs.org` DNS 无解析结果；
- `npm install --no-audit --no-fund` 实际执行 90 秒后超时；
- 本轮再次执行 `npm view @medusajs/medusa@2.21.1 version`，30 秒仍超时，确认当前容器 npm registry 仍不可达；
- 已新增 GitHub Actions PostgreSQL/Redis 真实验收工作流，但仓库尚未写入/执行，因此不能声称 `medusa db:migrate / build / start` 已通过。

因此当前交付状态是：**ClubOS v0.16 接入代码 + 完整 Python 回归已通过；真实 Medusa runtime 验收被执行环境阻断，v0.16 仍未标记 completed。**
