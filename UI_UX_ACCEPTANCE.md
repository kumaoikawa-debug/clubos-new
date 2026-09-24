# ClubOS NEW UX 交付与验收清单

## 交付文件

- `static/ux/clubos-ux.css`：统一设计 token、经营端/移动端响应式布局、表单、状态反馈。
- `static/ux/clubos-ux.js`：导航增强、模块搜索、退出、结构化业务表单与二次确认。
- `static/ux/workflows-platform*.js`：平台经营业务交互。采购退货操作单独拆分。
- `static/ux/workflows-club.js`：俱乐部业务表单。
- `static/ux/workflows-web.js`：C 端表单。
- `static/platform/index.html`、`static/club/index.html`、`static/web/index.html`、`static/login.html`：前端接线。
- `ClubOS_NEW_UIUX_Interactive_Preview.html`：单文件离线设计评审，数据仅驻留当前页面。
- `tests/ux_smoke_v025.py`：结构及脚本语法检查。

## 可在离线预览执行

1. 切换总平台 / 俱乐部 / C端，验证不会把平台采购价展示给俱乐部/C端。
2. 总平台点击供应商/采购，创建采购草稿（带数量校验）。
3. 审核草稿：在途数量增加、可售库存不动。
4. 分批到货确认：可售和实物库存同步增加、在途减少。
5. 俱乐部端打开 AI 活动、执行信息表单；C 端浏览活动、会员及订单路径。
6. 390px 手机视口检查导航/卡片/底部安全区；键盘 Esc 可关闭弹窗。

## 上线前必须在真实 Staging 再验收

- 登录/权限/跨租户、文件上传与图片访问；真实活动多团期及所有退款边界。
- 真正的支付、退款、Medusa PostgreSQL Runtime 与 WMS 同步重试；不要把离线预览算真实 E2E。
- 完整空状态/失败/冲突/重复提交/无网络演练，多浏览器与无障碍检测。
- 原始业务回归 `bash scripts/regression_v025.sh` 保持通过。

## 第二轮补全交付（v0.25 UI/UX Complete Phase 2）

详见 `UI_UX_COMPLETE_PHASE2.md`。新增 `ClubOS_NEW_UIUX_Full_Interactive_Preview.html`，覆盖仓库/库位、移库盘点、按活动生成渠道内容、C 端装备搜索与订单、售后流程本地模拟。生产路径继续保留服务端权限/资金状态为唯一事实。

专项测试：`python tests/ux_completion_smoke.py`；组合复测：`bash scripts/ux_acceptance_v025.sh`。离线模拟不代表支付/Medusa/部署验收通过。
