#!/usr/bin/env bash
set -euo pipefail
export MOCK_AI="${MOCK_AI:-1}"
export COMMERCE_PROVIDER="${COMMERCE_PROVIDER:-local}"
python tests/smoke.py
for v in 08 09 10 11 12 13 14 15 16 17 18 19; do
  echo "=== tests/smoke_v${v}.py ==="
  python "tests/smoke_v${v}.py"
done
echo "[v0.19] FULL REGRESSION PASSED"
