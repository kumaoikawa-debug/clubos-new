#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# v0.8-v0.24 demo fixtures remain isolated from the independent production-mode security test.
export CLUBOS_SECURITY_MODE=demo MOCK_AI=1 COMMERCE_PROVIDER=local
for t in tests/smoke.py tests/smoke_v{08..24}.py; do
  echo "[v0.25 regression] $t"
  python "$t"
done
# Uses a fresh temp SQLite, production mode and synthetic roles; no third-party provider is faked as deployed.
python tests/smoke_v25.py
