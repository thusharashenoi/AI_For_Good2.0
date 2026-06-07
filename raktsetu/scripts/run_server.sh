#!/usr/bin/env bash
cd "$(dirname "$0")/.."
export LOCAL_MODE=1 AGENT_FORCE_BEDROCK=1 PERISKOPE_LIVE_SENDS=1 VAPI_LIVE_CALLS=1
export PORT="${PORT:-4000}"
export LOCAL_STATE_PATH="$(pwd)/local_state.json"
export LOCAL_SERVER_URL="http://127.0.0.1:${PORT}"
exec /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 scripts/local_server.py >> /tmp/raktsetu-server.log 2>&1
