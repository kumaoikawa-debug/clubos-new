#!/usr/bin/env bash
set -euo pipefail
export MOCK_AI="${MOCK_AI:-1}"
python tests/smoke.py
for v in 08 09 10 11 12 13 14 15 16 17; do
  python "tests/smoke_v${v}.py"
done
echo "[v0.17] FULL REGRESSION PASSED"
