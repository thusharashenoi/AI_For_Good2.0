#!/usr/bin/env bash
cd "$(dirname "$0")/.."
# Respect LOCAL_MODE / table names from .env (shared/config.py loads .env on startup).
export AGENT_FORCE_BEDROCK=1 PERISKOPE_LIVE_SENDS=1 VAPI_LIVE_CALLS=1
export PORT="${PORT:-4000}"
export LOCAL_SERVER_URL="http://127.0.0.1:${PORT}"
exec /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 scripts/local_server.py >> /tmp/raktsetu-server.log 2>&1
