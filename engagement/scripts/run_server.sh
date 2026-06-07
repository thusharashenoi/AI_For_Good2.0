#!/usr/bin/env bash
# Start engagement server on :4000 with live DynamoDB dev tables (matches Admin API).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/engagement"
eval "$("$ROOT/scripts/load_env.sh")"
export VAPI_SERVER_URL="${VAPI_SERVER_URL:-http://127.0.0.1:4000}"
exec "$ROOT/.venv/bin/python" scripts/local_server.py
