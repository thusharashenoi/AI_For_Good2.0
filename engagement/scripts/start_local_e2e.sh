#!/usr/bin/env bash
# Run the engagement webhook server locally (WhatsApp + Vapi).
# Keep this terminal open while testing.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/engagement"
eval "$( "$ROOT/scripts/load_env.sh" )"
PY="${ROOT}/.venv/bin/python"
if ! "$PY" -c "import twilio" 2>/dev/null; then
  "${ROOT}/.venv/bin/pip" install -q -r requirements.txt
fi
echo "Mode: LOCAL_MODE=$LOCAL_MODE STAGE=$STAGE WA_PROVIDER=${WA_PROVIDER:-twilio}"
echo "Listening on http://localhost:${PORT:-4000}"
echo "In another terminal: ngrok http ${PORT:-4000}"
exec "$PY" scripts/local_server.py
