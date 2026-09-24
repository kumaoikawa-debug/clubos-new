#!/usr/bin/env bash
# 重启 ClubOS Staging(demo) 实例 —— 三端联调用的可写工作区副本。
# 用法：bash scripts/run_demo.sh [端口，默认 8000]
set -euo pipefail
PORT="${1:-8000}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${CLUBOS_PYTHON:-/Users/jckuma/.workbuddy/binaries/python/envs/default/bin/python}"
DB="${CLUBOS_DB_PATH:-/tmp/clubos_staging/clubos.db}"
mkdir -p "$(dirname "$DB")"

cd "$ROOT"
# 注意：SQLite 与 macOS TCC 限制下，库必须放在可写目录，不要用项目内的 clubos.db。
CLUBOS_SECURITY_MODE=demo \
MOCK_AI="${MOCK_AI:-1}" \
COMMERCE_PROVIDER=local \
CLUBOS_DB_PATH="$DB" \
CLUBOS_AUTH_SIGNING_KEY="Staging-ClubOS-demo-signing-key-please-rotate-000000000000" \
PYTHONUNBUFFERED=1 \
exec "$PY" -m uvicorn app:app --host 127.0.0.1 --port "$PORT" --log-level info
