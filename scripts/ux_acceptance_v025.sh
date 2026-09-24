#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python tests/ux_smoke_v025.py
python tests/ux_completion_smoke.py
bash scripts/regression_v025.sh
