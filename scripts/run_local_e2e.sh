#!/usr/bin/env bash
# Start the full local demo stack: engagement webhooks + Admin API + ngrok + Vapi.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
eval "$("$ROOT/scripts/load_env.sh")"

PY="${ROOT}/.venv/bin/python"
PIP="${ROOT}/.venv/bin/pip"
PORT="${PORT:-4000}"
LOG_DIR="${TMPDIR:-/tmp}/raktsetu-local"
mkdir -p "$LOG_DIR"

cmd="${1:-start}"

need_deps() {
  if ! "$PY" -c "import twilio, requests" 2>/dev/null; then
    echo "Installing engagement dependencies (twilio, requests)..."
    "$PIP" install -q -r "$ROOT/engagement/requirements.txt"
  fi
}

ngrok_url() {
  curl -sf http://127.0.0.1:4040/api/tunnels | "$PY" -c "
import sys, json
d = json.load(sys.stdin)
for t in d.get('tunnels', []):
    u = t.get('public_url', '')
    if u.startswith('https://'):
        print(u)
        break
" 2>/dev/null || true
}

pid_on_port() {
  lsof -t -i ":$1" 2>/dev/null | head -1 || true
}

do_setup() {
  need_deps
  echo "Creating / verifying DynamoDB tables (STAGE=$STAGE, region=$AWS_DEFAULT_REGION)..."
  "$PY" -m scripts.create_tables
  echo ""
  echo "Running connectivity checks (AWS + Bedrock + Twilio)..."
  (cd "$ROOT/engagement" && LOCAL_MODE=0 "$PY" scripts/test_connectivity.py) || true
}

do_start() {
  need_deps
  do_setup

  if [ -z "$(pid_on_port "$PORT")" ]; then
    echo "Starting engagement webhook server on :$PORT ..."
    (cd "$ROOT/engagement" && nohup env STAGE="$STAGE" LOCAL_MODE="$LOCAL_MODE" \
      AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
      AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" AWS_REGION="$AWS_REGION" \
      DYNAMODB_TABLE_DONORS="$DYNAMODB_TABLE_DONORS" DYNAMODB_TABLE_PATIENTS="$DYNAMODB_TABLE_PATIENTS" \
      DYNAMODB_TABLE_REQUESTS="$DYNAMODB_TABLE_REQUESTS" DYNAMODB_TABLE_APPOINTMENTS="$DYNAMODB_TABLE_APPOINTMENTS" \
      DYNAMODB_TABLE_CONVERSATIONS="$DYNAMODB_TABLE_CONVERSATIONS" DYNAMODB_TABLE_WAITLIST="$DYNAMODB_TABLE_WAITLIST" \
      DYNAMODB_TABLE_PROCESSED_MESSAGES="$DYNAMODB_TABLE_PROCESSED_MESSAGES" \
      BEDROCK_MODEL_ID="$BEDROCK_MODEL_ID" AGENT_FORCE_BEDROCK="$AGENT_FORCE_BEDROCK" \
      WA_PROVIDER="$WA_PROVIDER" PERISKOPE_API_KEY="$PERISKOPE_API_KEY" PERISKOPE_PHONE="$PERISKOPE_PHONE" \
      PERISKOPE_LIVE_SENDS="$PERISKOPE_LIVE_SENDS" VAPI_LIVE_CALLS="$VAPI_LIVE_CALLS" \
      TWILIO_ACCOUNT_SID="$TWILIO_ACCOUNT_SID" TWILIO_AUTH_TOKEN="$TWILIO_AUTH_TOKEN" \
      PORT="$PORT" \
      "$PY" scripts/local_server.py >"$LOG_DIR/local_server.log" 2>&1 &)
    sleep 2
  else
    echo "Engagement server already on :$PORT (pid $(pid_on_port "$PORT"))"
  fi

  if ! curl -sf "http://127.0.0.1:4040/api/tunnels" >/dev/null 2>&1; then
    echo "Starting ngrok http $PORT ..."
    nohup ngrok http "$PORT" >"$LOG_DIR/ngrok.log" 2>&1 &
    sleep 4
  else
    echo "ngrok already running"
  fi

  NGROK="$(ngrok_url)"
  if [ -z "$NGROK" ]; then
    echo "ERROR: ngrok URL not ready — check $LOG_DIR/ngrok.log"
    exit 1
  fi
  export VAPI_SERVER_URL="$NGROK"
  echo "ngrok public URL: $NGROK"

  echo "Updating Vapi assistant tool server URL..."
  (cd "$ROOT/engagement" && "$PY" scripts/setup_vapi.py --server-url "$NGROK") || true

  echo "Pointing Twilio WhatsApp inbound webhook at $NGROK/whatsapp ..."
  (cd "$ROOT/engagement" && "$PY" scripts/setup_twilio_webhook.py --base-url "$NGROK") || true

  if [ -z "$(pid_on_port 8000)" ]; then
    echo "Starting Admin API on :8000 ..."
    nohup env STAGE="$STAGE" LIVE_ELIGIBILITY="$LIVE_ELIGIBILITY" \
      DYNAMODB_TABLE_DONORS="$DYNAMODB_TABLE_DONORS" \
      DYNAMODB_TABLE_PATIENTS="$DYNAMODB_TABLE_PATIENTS" \
      DYNAMODB_TABLE_BRIDGES="$DYNAMODB_TABLE_BRIDGES" \
      DYNAMODB_TABLE_REQUESTS="$DYNAMODB_TABLE_REQUESTS" \
      AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
      AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
      AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
      "$PY" -m scripts.run_admin_api >"$LOG_DIR/admin_api.log" 2>&1 &
    sleep 2
  else
    echo "Admin API already on :8000 — restart it with 'scripts/run_local_e2e.sh restart-admin' if env changed"
  fi

  if [ -z "$(pid_on_port 5173)" ]; then
    echo "Starting Admin UI on :5173 ..."
    (cd "$ROOT/admin" && nohup npm run dev >"$LOG_DIR/admin_ui.log" 2>&1 &)
    sleep 3
  else
    echo "Admin UI already on :5173"
  fi

  do_status
}

do_restart_admin() {
  pid="$(pid_on_port 8000)"
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    sleep 2
    if [ -n "$(pid_on_port 8000)" ]; then
      kill -9 "$(pid_on_port 8000)" 2>/dev/null || true
      sleep 1
    fi
  fi
  nohup env STAGE="$STAGE" LIVE_ELIGIBILITY="$LIVE_ELIGIBILITY" \
    DYNAMODB_TABLE_DONORS="$DYNAMODB_TABLE_DONORS" \
    DYNAMODB_TABLE_PATIENTS="$DYNAMODB_TABLE_PATIENTS" \
    DYNAMODB_TABLE_BRIDGES="$DYNAMODB_TABLE_BRIDGES" \
    DYNAMODB_TABLE_REQUESTS="$DYNAMODB_TABLE_REQUESTS" \
    AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
    AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
    AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
    "$PY" -m scripts.run_admin_api >"$LOG_DIR/admin_api.log" 2>&1 &
  echo "Admin API restarted with STAGE=$STAGE dev tables."
}

do_status() {
  NGROK="$(ngrok_url)"
  echo ""
  echo "════════════════════════════════════════════════════════════"
  echo " RaktSetu local E2E stack"
  echo "════════════════════════════════════════════════════════════"
  echo " Engagement server : http://localhost:$PORT/health"
  echo " ngrok (public)    : ${NGROK:-not running}"
  echo " Admin API         : http://127.0.0.1:8000/docs"
  echo " Admin UI          : http://127.0.0.1:5173"
  echo " STAGE             : $STAGE | LOCAL_MODE=$LOCAL_MODE"
  echo " WhatsApp provider : ${WA_PROVIDER:-twilio}"
  echo ""
  echo " Twilio WhatsApp (required for broadcast replies) → POST ${NGROK}/whatsapp"
  if [ "${WA_PROVIDER:-}" = "periskope" ]; then
    echo " Periskope webhook (donor bot on own number) → POST ${NGROK}/periskope"
  fi
  echo " Vapi tools        → POST ${NGROK}/vapi/tools"
  echo " Voice inbound     → POST ${NGROK}/voice/inbound"
  echo ""
  echo " Test WhatsApp (CLI): cd engagement && $PY scripts/test_whatsapp_flow.py --phone +91... --interactive"
  echo " Test Vapi call     : cd engagement && $PY scripts/test_vapi_call.py --to +91..."
  echo " Logs               : $LOG_DIR/"
  echo "════════════════════════════════════════════════════════════"
}

do_stop() {
  for p in "$PORT" 8000; do
    pid="$(pid_on_port "$p")"
    if [ -n "$pid" ]; then
      kill "$pid" 2>/dev/null || true
      echo "Stopped process on :$p (pid $pid)"
    fi
  done
  pids="$(pgrep -f 'ngrok http' 2>/dev/null || true)"
  if [ -n "$pids" ]; then kill $pids 2>/dev/null || true; echo "Stopped ngrok"; fi
}

case "$cmd" in
  setup) do_setup ;;
  start) do_start ;;
  status) do_status ;;
  stop) do_stop ;;
  restart-admin) do_restart_admin ;;
  *)
    echo "Usage: $0 {setup|start|status|stop|restart-admin}"
    exit 1
    ;;
esac
