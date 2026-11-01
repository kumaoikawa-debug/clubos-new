# ClubOS 后台设计系统 · Admin Design System v1（2026-10-30）

> 依据 `ui-ux-pro-max` 引擎推导：BM25 命中 **Dashboard / Admin** 类目（置信 8.33），
> 风格 = Data/Dashboard；调色板以 **#10 Muted（鼠尾草绿）** 为起点，
> 保留既有品牌深松绿为锚点 —— 是「现代清爽化」，不是推倒重来。
> 适用范围：`body.admin-workspace`（俱乐部端 `/club` + 总平台端 `/platform` 共用 `static/ux/admin-workspace.css`）。

## 1. 设计原则（对应引擎反模式）
- 反「information overload」：层级靠留白与字重，不靠加框加线。
- 反「low contrast data」：正文对底 ≥ 12:1，次要文字 ≥ 4.5:1。
- 反「no empty states」：所有空态保留现有 `.empty` 结构，只换视觉。
- 每屏一个主操作（primary CTA 视觉最重）。

## 2. 色彩 Token
| Token | 值 | 用途 | 旧值 |
|---|---|---|---|
| `--bg` | `#F3F6F2` | 页面底（冷鼠尾草灰） | `#f6f4ed` 暖卡其 |
| `--panel` | `#FFFFFF` | 卡片/面板 | `#fffef9` |
| `--text` | `#1B2A20` | 正文墨绿黑 | `#243d31` |
| `--muted` | `#5E6E62` | 次要文字 | `#5c685d` |
| `--line` | `#E2E8DF` | 常规边线 | `#d8dbcf` |
| `--line-strong` | `#C9D4C8` | 输入框/ghost 按钮边 | 散落多值 |
| `--brand` | `#2E6B4E` | 主操作/激活态（松绿 600） | `#344f3c`（保留作 `--brand-deep`） |
| `--brand-strong` | `#22543C` | 主按钮 hover | `#1d4a38` |
| `--brand-deep` | `#22352B` | 侧栏激活/重墨块 | `#344f3c` |
| `--brand-soft` | `#E8F2EB` | 次级按钮/绿色 tag 底 | `#e8ecde` |
| `--accent` | `#C8452D` | 警示/角标（赭红，沿用品牌） | 同 |
| `--warn` | `#B7791F` | 注意类 | 散落 |
| `--info` | `#3B6FB5` | 信息类 | `#3b4bc4` 散落 |
| `--focus` | `#2E6B4E` | 焦点环 | `#78632f` |

语义规则：**绿=可行动/成功，赭红=待处理/危险，琥珀=注意，蓝=中性信息**；同一页面 tag 用色不超过 4 种。

## 3. 形状 / 阴影 / 层次
| 项 | 值 | 旧值 |
|---|---|---|
| 卡片圆角 | `12px` | `3px` |
| 控件（按钮/输入/下拉）圆角 | `8px` | `2px` |
| tag/badge/角标 | `999px` 胶囊 | `2px` |
| 弹窗圆角 | `14px` | `3px` |
| 卡片静息阴影 | `0 1px 2px rgba(27,42,32,.05)` | none |
| 卡片/行 hover | `0 8px 24px rgba(27,42,32,.09)` + border 变深 | 仅变底色 |
| 焦点环 | `:focus-visible{outline:2px solid var(--brand);outline-offset:2px}`（a11y P0 UX-005） | 无 |
| 过渡 | `transition: background .15s, border-color .15s, box-shadow .15s` | 无 |

## 4. 字体 / 字阶
系统中文栈不变（`-apple-system, PingFang SC, Microsoft YaHei`），**不引外部字体**（境内加载慢且阻塞）。
数字沿用 `tabular-nums`。阶梯：`12 / 12.5 / 13(基准控件) / 14(正文) / 16(节标题) / 20(页题) / 26(顶栏) / 30(KPI)`；
字重 `450 常规 / 550 中 / 650-700 标题`。

## 5. 间距
4 的倍数：卡片内边距 `20-22px`、节间距 `22px`、控件内边距 `8px 14px`、表格单元 `12px 14px`、移动端卡片 `16px`。

## 6. 组件规范
- **侧栏**：底 `#FBFCFA`，右缘 1px `--line`；激活项 = 松绿 `--brand` 圆角 8px 胶囊 + 白字 + 轻投影；hover = `#EFF4EC`。
- **按钮**：primary = 松绿实底白字；secondary = `--brand-soft` 绿字；ghost = 透明 + `--line-strong` 边。高度 38px（表格内 32px，移动端 42px）。
- **表格**：表头底 `#F4F7F2` 12px 加重；行 hover `#F6F9F4`；数字列右对齐沿用 tabular-nums。
- **表单**：白底、`--line-strong` 边、8px 圆角；focus = 品牌边 + `0 0 0 3px rgba(46,107,78,.15)` 光环。
- **KPI 指标条**：由「连体带边条带」改为**独立白卡 × 4**（gap 12px，移动端 2×2），数值 30px/650。
- **弹窗**：14px 圆角 + 大投影 `0 16px 54px rgba(27,42,32,.18)`；foot 底 `#F7FAF5`。

## 7. 交付门禁（P0，来自引擎 QA 清单）
- [ ] 所有可交互元素有可访问名称（沿用现有文本/aria）
- [ ] 正文对比度 ≥ 4.5:1（新 token 实测 `#1B2A20` on `#FFFFFF` ≈ 14.9:1，`--muted` ≈ 5.2:1 ✔）
- [ ] 不只靠颜色传达含义（角标有数字、状态有文字）
- [ ] 键盘可达 + 可见焦点环（新增 `:focus-visible`）
- [ ] 移动端 320px 不横向滚动、点按目标 ≥ 42px（沿用现有响应式断点）

## 8. 落地文件
- `static/ux/admin-workspace.css` — 视觉层重写（选择器结构不变，只改视觉值）
- `static/club/index.html` / `static/platform/index.html` — `?v=` bump 破缓存
- 内联样式（`.seg/.info-row/.ex-lead/.nav-badge`）色值与新 token 兼容，暂不动
