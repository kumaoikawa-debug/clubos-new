#!/usr/bin/env bash
set -euo pipefail
python tests/smoke.py
for f in tests/smoke_v08.py tests/smoke_v09.py tests/smoke_v10.py tests/smoke_v11.py tests/smoke_v12.py tests/smoke_v13.py tests/smoke_v14.py tests/smoke_v15.py tests/smoke_v16.py tests/smoke_v17.py tests/smoke_v18.py; do
  echo "=== $f ==="
  python "$f"
done
