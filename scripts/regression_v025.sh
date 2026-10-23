#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# v0.8-v0.24 demo fixtures remain isolated from the independent production-mode security test.
export CLUBOS_SECURITY_MODE=demo MOCK_AI=1 COMMERCE_PROVIDER=local
# Portable expansion (works on macOS bash 3.2 and Linux bash 4+; brace {08..24}
# zero-pads only on bash 4+, so use explicit globs to target smoke_v08..v24).
for t in tests/smoke.py tests/smoke_v0[89].py tests/smoke_v1[0-9].py tests/smoke_v2[0-4].py; do
  echo "[v0.25 regression] $t"
  python "$t"
done
# Uses a fresh temp SQLite, production mode and synthetic roles; no third-party provider is faked as deployed.
python tests/smoke_v25.py
# 积分抵扣的「报价上限」与「下单实扣」必须同源（C 端报名页由顾客填写改为系统算好上限 + 顾客确认）。
# 该测试自带临时库隔离，不需要 CLUBOS_DB_PATH。
python tests/points_max_redeemable_smoke.py
# 删除活动的报名护栏：只有「未取消且未退款」的报名才拦得住（refunded 已全额退款，不该再拦）。
# 自带临时库隔离，不需要 CLUBOS_DB_PATH。
python tests/registration_delete_smoke.py
# 俱乐部提交的「装备上下架」申请必须真的改到 C 端商城的可见性：
# products 表没有 club 归属列，俱乐部无权直接改 products.status，只能申请，
# 由总平台批准时改。这条链断在哪一环，这个测试都会红。
# 自带临时库隔离，不需要 CLUBOS_DB_PATH。
python tests/product_visibility_smoke.py
# C 端活动详情的「带队领队」：只下发已指派的人、绝不下发手机号与推荐名单、
# 头像必须走 C 端可访问的公开代理、生产白名单必须放行 leaders。
# 自带临时库隔离，不需要 CLUBOS_DB_PATH。
python tests/public_leaders_smoke.py
# C 端从首页点进活动详情必须真的看得见：openAct 要先把视图切到 #wactivities，
# 否则详情被写进一个 display:none 的 section，用户看到的是「点了没反应」。
# 纯静态断言（只读 static/web/*），不建库不碰真库。
python tests/web_act_view_guard.py
# AI 生成的活动必须自带封面：cover 字段此前只有「上传封面」接口会写，AI 生成从不碰它，
# 于是素材里几十张照片、列表却挂「未设封面」。落版补空 + 手动接口 + 绝不覆盖人工封面。
# 自带临时库隔离，不需要 CLUBOS_DB_PATH。
python tests/auto_cover_smoke.py
# AI 宣传长图/详情的「极简画报」版式（2026-10-21 对照样张定稿）：主题存在性、零圆角零卡片、
# 满幅照片、用图密度下限 min(N, max(8, round(0.6N)))、提示词占位符接线。
# 纯函数 + 本地渲染，不联网不调模型。
python tests/longpic_minimal_smoke.py
# 详情「风格家族」守门（2026-10-22 用户反馈「所有详情长得一样」后引入）：五个家族到底色不同、
# 题材→家族映射确定、同一场稳定复现、promoHtml 图片封顶 + 用图密度闸门。不联网不调模型。
python tests/detail_style_smoke.py
# 执行页领队资源库指派 + C 端「出行指引」同步（2026-10-22 用户反馈两件事）：
# ①「活动执行里的领队应该跟资源库同步、直接选、也可以增加」——按 leaderId 指派回填名册、
#    重复/不存在指派被拒、撤下后 C 端带队人清空；
# ②「分车、活动通知保存之后能同步到 C 端用户吗」——order-center 的 trip 下发
#    （集合/应急/带队/sent 通知/分车），草稿通知与领队手机号、内部备注不下发。
# 需要 CLUBOS_TEST_OPEN=1（俱乐部写接口在 demo 模式仍要求登录，由调用方环境提供）。
python tests/execution_trip_smoke.py
# 会员深度管理（2026-10-22 用户截图反馈「点进会员要看全量信息 + 调积分 + 赠券」）：
# 详情聚合（个人信息/订单/券/流水）、积分手工增减（负数/越界/非法入参/流水落账）、
# 赠券（零积分、俱乐部承担、库存扣减、售罄/平台券/非会员被拒）、赠券立刻进 C 端可用券。
# 需要 CLUBOS_TEST_OPEN=1（俱乐部写接口在 demo 模式仍要求登录，由调用方环境提供）。
python tests/member_manage_smoke.py
# 商城佣金日期搜索 + 俱乐部结算账户（2026-10-22 用户截图反馈两件事）：
# 订单/佣金流水 date_from/date_to 过滤（UTC+8 本地日期对齐、from>to 与非法格式 400）；
# 结算账户法人卡/对公账户建档（脱敏、非法入参 400、upsert），平台无账户结算 409、
# 有账户才放行并回带打款账户摘要，结算幂等，记录可核对打到了哪张卡。
# 需要 CLUBOS_TEST_OPEN=1（俱乐部写接口在 demo 模式仍要求登录，由调用方环境提供）。
python tests/mall_commission_search_settlement_smoke.py
# 城市维度（2026-10-23 用户反馈：SaaS 面向全国，后台新建活动选城市、C 端按城市筛选 + 定位默认当前城市）：
# 手工新建/编辑写入 city、俱乐部与公开活动列表带 city、老活动（无 city）兜底为空；
# 另含 node 校验 clubos-cities.js 的 location 兜底推导与定位最近城市。自带临时库隔离。
python tests/city_dimension_smoke.py
# AI Credits 账本 / 用量明细的日期筛选 + 区间汇总 + 分页（2026-10-23 用户反馈）：
# /credits/ledger 与 /ai/usage 支持 date_from/date_to/limit/offset，
# /ai/usage/summary 按 task_type 拆分；旧 /credits 端点与 /ai/usage 旧 list 契约保留。
# 自带临时库隔离（CLUBOS_DB_PATH 指到 clubos_ledger_test.db），不需要外部传入。
python tests/ledger_date_filter_smoke.py
# 装备折扣归属迁移 + 积分权限边界（2026-10-23 用户反馈两件事）：
# ① 装备折扣从俱乐部会员等级迁到平台订阅套餐（ai_credit_plans.gear_discount），
#    结算/会员中心/只读端点全部按套餐口径，等级接口收到 gearDiscount 一律忽略；
# ② 俱乐部调积分只允许活动积分，kind=gear 一律 403（装备积分只能由总平台调整）。
# 自带临时库隔离（CLUBOS_DB_PATH 指到 clubos_plan_gear_test.db），不需要外部传入。
python tests/plan_gear_discount_smoke.py
python tests/commission_request_smoke.py
# C 端「我的」页文案（联系我们 / 关于俱乐部）由俱乐部自定义（2026-10-23）：
# 没填的字段不下发（不编造营业时间）、局部更新不清空另一块、脏输入清洗、内部列不泄漏。
# 自带临时库隔离（clubos_cprofile_test.db），不需要外部传入。
python tests/cprofile_smoke.py

# 出行清单两区 UI + 费用极性（2026-10-23 用户截图反馈三件事）：
# ① notIncluded 负向键不再裸奔/不再被打成 ✓（含 "includ" 的否定式先判负向）；
# ② 装备卡上商城真实产品图（imageUrl）与商城介绍（description），裂图退 emoji 有 parentNode 守卫；
# ③ 「N 件装备」与「商城可配 X 项」口径可对、平替 chip 只挂小徽标、自备区是 soft pills。
# node 真实执行 shared.js 渲染函数（只读），python 侧用推荐引擎算真实匹配，不碰业务库。
python tests/pack_ui_smoke.py
# 总平台端三件事（2026-10-23 用户截图反馈）：
# ①「俱乐部入驻」显示俱乐部 logo（平台专属代理，只有平台角色能取）+ 手动增减 AI Credits；
# ②「分配套餐」由下拉改卡片平铺（前端形态断言，后端契约不变）；
# ③ AI 套餐可增删（被订阅占用的套餐必须拒绝硬删）。
# 附带守住「套餐/账单列表 DOM 少一个 </div> 导致文字竖排」的渲染 bug。
# 自带临时库与临时 uploads 隔离（app.UPLOAD 改写），不碰项目真库与真 uploads。
python tests/platform_plans_smoke.py

# 装备积分抵用资格与比例（2026-10-24 用户设计纠正）：准入权归总平台 —— 套餐决定有没有权益
# （Starter 无 / Pro / Enterprise 有），比例由总平台按俱乐部单独设定（套餐自带比例是权益上限，
# 平台不能凭空放大）；活动级 accept_gear_points 与金额上限彻底退出判定；
# C 端 / 俱乐部端 / 平台端三处下发的比例必须同源。
# 自带临时库隔离，不需要 CLUBOS_DB_PATH。
python tests/gear_points_plan_policy_smoke.py
