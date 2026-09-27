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
