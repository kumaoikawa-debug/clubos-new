#!/usr/bin/env bash
# ClubOS NEW · 发布前统一门禁（2026-11-14，#927~#930）
#
# 用法：bash scripts/predeploy_gate.sh
# 前置：按发布纪律起好隔离实例（默认 http://127.0.0.1:8124，带 seed 的临时库 + 真 Chrome）。
#       CLUBOS_GATE_BASE / CLUBOS_GATE_OUT / STAGING_PASSWORD 可覆盖默认值。
#
# 三段任一失败即整体非零退出，禁止发布：
#   [1/3] 静态安全门禁   —— web root 敏感文件 / .gitignore / 四端版本 / 守卫 / 密钥 / 门禁自检
#   [2/3] 设置中枢回归门禁 —— 4 份真浏览器脚本 + 4 条独立硬锁（见 predeploy_hub_gate.py）
#   [3/3] 全量回归       —— scripts/regression_v025.sh（全新临时库，不碰真库）
#
# 注意：回归会改写 qa_ecommerce/tests/docs/TEST-RESULTS.json，发布前按既有纪律
#       git restore 丢弃该漂移；本脚本不自动 restore，避免误丢人工改动。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/venv/bin/python"
if [ ! -x "$PY" ]; then PY="$(command -v python3)"; fi

echo "===== [1/3] 静态安全门禁 ====="
"$PY" scripts/predeploy_security_check.py

echo "===== [2/3] 设置中枢回归门禁（4 脚本 + 4 硬锁）====="
"$PY" scripts/predeploy_hub_gate.py

echo "===== [3/3] 全量回归 ====="
DB="/tmp/clubos_predeploy_gate_$(date +%s).db"
PATH="$ROOT/venv/bin:$PATH" CLUBOS_DB_PATH="$DB" CLUBOS_TEST_OPEN=1 \
  bash scripts/regression_v025.sh

echo "===== PREDEPLOY GATE: ALL PASS ====="
