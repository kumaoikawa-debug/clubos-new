# ClubOS NEW v0.25 · UI/UX Experience Upgrade — 第二轮补全

本文件是代码交付记录，不宣称系统已通过生产上线验收。本轮不改变 v0.25 的业务实体和安全权限，页面中的业务提交继续通过原有 authenticated API；独立 HTML 预览完全是离线模拟。

## 三端 + 领队页范围

| 终端 | 第二轮新增/优化 | 数据边界 |
|---|---|---|
| Platform Admin | 表格搜索、加载/失败重试、佣金冻结/结算表单、产品上下架确认、月度 AI Credits 账单确认、售后退货检测、仓库与库位、移库/盘点、拣货/打包 | 仅平台；库存和财务变化由 ClubOS 服务端判定 |
| Club Admin | AI 创建素材拖放/清单、生成状态、按活动选择的渠道内容工作室、可读内容/复制、活动发布校验、保险批量提交、通知、执行状态确认 | 每个请求均携带当前已验证 clubId；平台不能代操作俱乐部日常经营 |
| Club Web/C 端 | 活动详情报名直达、装备搜索、报名资料校验、装备下单表单、付款 QR/跳转/微信桥接、服务器状态查询、支付确认后收据/订单、售后入口保持 | 用户身份和资金结果由后端认定；Club/Gear 积分分账 |
| 领队端 | 搜索签到名单、签到二次确认、执行状态确认 | 领队授权必须由服务端校验 |

## 真实接线与演示版边界

- `static/ux/experience-core.js`：导航/表格/加载与错误重试、AI 素材输入增强。
- `static/ux/workflows-completion-platform.js`：剩余高频平台操作，结构化字段和预提交确认。
- `static/ux/workflows-completion-club.js`：渠道生成、活动发布与执行的确认/结果展示。
- `static/ux/payment-experience.js` + `static/ux/qr-local.js`：二维码本地绘制，真实支付是否成功只读 ClubOS 结算接口，不使用外部 QR 网站，不因客户端点击而结算订单。第三方 QR 源码保留 MIT 许可文件。
- `static/ux/experience-consumer.js` 和 `static/ux/leader-experience.js`：用户及领队路径。
- `ClubOS_NEW_UIUX_Full_Interactive_Preview.html`：可切换三端，采购、入库、移库、盘点、渠道选择、装备搜索/确认、售后模拟。所有演示数据只存浏览器当前页面，**不连接真实后端或付款渠道**。

## 可复测验收

```bash
python tests/ux_smoke_v025.py
python tests/ux_completion_smoke.py
bash scripts/regression_v025.sh
```

另附独立浏览器检查，验证离线预览中的移库、盘点、渠道切换、商品搜索、装备订单模拟对话框，以及真实共享组件的二维码绘制、表单数字范围校验与页面 JS 错误。

## 不能被代码回归替代的正式验收

1. 用实际运行的 ClubOS Staging 登录四个角色，对完整菜单做一遍交互遍历（本容器无权直接提供公网 Staging）。
2. 使用真实 Webhook 验签的微信/支付宝订单/退款、支付中断/返回/重复通知；不可把离线预览当支付 E2E。
3. Medusa v2 + PostgreSQL + Redis 真正联通；多仓锁库与出库镜像、消息重试。
4. C 端为响应式 H5，微信原生小程序不是本轮交付。
5. 所有旧有低频原生弹窗、极端数据表格、无障碍读屏、多设备性能仍需按上线设备矩阵复查；本版优先补完高频路径，不宣称每一处已经人工验收。
