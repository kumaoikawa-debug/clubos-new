#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export MOCK_AI=1
export COMMERCE_PROVIDER=local
for t in tests/smoke.py tests/smoke_v{08..24}.py; do
  echo "[v0.24 regression] $t"
  python "$t"
done
