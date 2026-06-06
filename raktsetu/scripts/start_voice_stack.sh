#!/usr/bin/env bash
# Start local_server + ngrok for Exotel/Vapi voice calls.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "${ROOT}/../.venv/bin/activate" 2>/dev/null || source "${ROOT}/.venv/bin/activate" 2>/dev/null || true

if lsof -t -i :4000 >/dev/null 2>&1; then
  echo "local_server already on :4000 (pid $(lsof -t -i :4000))"
else
  nohup python scripts/local_server.py >>/tmp/raktsetu-server.log 2>&1 &
  echo "local_server pid=$!"
fi

if curl -sf http://127.0.0.1:4040/api/tunnels >/dev/null 2>&1; then
  echo "ngrok already running"
else
  nohup ngrok http 4000 >>/tmp/ngrok.log 2>&1 &
  echo "ngrok pid=$!"
fi

sleep 4
echo "health: $(curl -sf http://localhost:4000/health || echo FAIL)"
NGROK=$(curl -sf http://127.0.0.1:4040/api/tunnels | python3 -c "
import sys,json
d=json.load(sys.stdin)
for t in d.get('tunnels',[]):
    u=t.get('public_url','')
    if u.startswith('https://'): print(u); break
" 2>/dev/null || true)
if [ -n "$NGROK" ]; then
  echo "ngrok: $NGROK"
  echo "exotel connect URL: ${NGROK}/exotel/connect"
  echo "vapi tools URL:     ${NGROK}/vapi/tools"
  LOCAL_MODE=1 python scripts/setup_voice.py --server-url "$NGROK" 2>/dev/null | head -5 || true
else
  echo "ngrok URL not ready — check /tmp/ngrok.log"
fi
