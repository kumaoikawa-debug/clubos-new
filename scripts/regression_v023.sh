#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export MOCK_AI=1
export COMMERCE_PROVIDER=local
for t in tests/smoke.py tests/smoke_v08.py tests/smoke_v09.py tests/smoke_v10.py tests/smoke_v11.py tests/smoke_v12.py tests/smoke_v13.py tests/smoke_v14.py tests/smoke_v15.py tests/smoke_v16.py tests/smoke_v17.py tests/smoke_v18.py tests/smoke_v19.py tests/smoke_v20.py tests/smoke_v21.py tests/smoke_v22.py tests/smoke_v23.py; do
  echo "[v0.23 regression] $t"
  python "$t"
done
