#!/usr/bin/env bash
# Keep the local webhook server running for Vapi + WhatsApp testing.
set -euo pipefail
cd "$(dirname "$0")/.."
export LOCAL_MODE=1
export AGENT_FORCE_BEDROCK=1
export PERISKOPE_LIVE_SENDS=1
export VAPI_LIVE_CALLS=1
PORT="${PORT:-4000}"
if lsof -t -i ":$PORT" >/dev/null 2>&1; then
  echo "Port $PORT in use — stop the old server first (lsof -t -i :$PORT | xargs kill)"
  exit 1
fi
echo "Starting RaktSetu local server on http://localhost:$PORT"
echo "Keep this terminal open during voice/WhatsApp tests."
exec python3 scripts/local_server.py
