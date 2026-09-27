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
