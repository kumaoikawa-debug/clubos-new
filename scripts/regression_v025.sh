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
