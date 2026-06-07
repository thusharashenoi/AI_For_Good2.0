#!/usr/bin/env bash
# Load repo .env safely (handles spaces in values) and apply dev-demo overrides.
# Usage: eval "$(scripts/load_env.sh)"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
PY="${ROOT}/.venv/bin/python"

"$PY" - "$ROOT" <<'PY'
import os
import shlex
import sys
from pathlib import Path

root = Path(sys.argv[1])
env_path = root / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ.setdefault(key, val)

overrides = {
    "STAGE": "dev",
    "LIVE_ELIGIBILITY": "1",
    "LOCAL_MODE": "0",
    "DYNAMODB_TABLE_DONORS": "raktsetu-donors-dev",
    "DYNAMODB_TABLE_PATIENTS": "raktsetu-patients-dev",
    "DYNAMODB_TABLE_BRIDGES": "raktsetu-bridges-dev",
    "DYNAMODB_TABLE_REQUESTS": "raktsetu-bloodRequests-dev",
    "DYNAMODB_TABLE_APPOINTMENTS": "raktsetu-appointments-dev",
    "DYNAMODB_TABLE_CONVERSATIONS": "raktsetu-conversations-dev",
    "DYNAMODB_TABLE_WAITLIST": "raktsetu-waitlist-dev",
    "DYNAMODB_TABLE_PROCESSED_MESSAGES": "raktsetu-processedMessages-dev",
    "AGENT_FORCE_BEDROCK": "1",
    "PERISKOPE_LIVE_SENDS": "1",
    "VAPI_LIVE_CALLS": "1",
    "TWILIO_INBOUND_POLL": "1",
    "TWILIO_WHATSAPP_NUMBER": "whatsapp:+918433775356",
    "PERISKOPE_PHONE": "918433775356",
    "DEMO_OUTREACH_PHONE": "+919372875356",
    "DEMO_OUTREACH_CALL_DELAY_SEC": "7",
    "OUTREACH_VOICE_ESCALATION_SECONDS": "7",
    "LOCAL_SERVER_URL": "http://127.0.0.1:4000",
}
for k, v in overrides.items():
    os.environ[k] = v

def _digits(phone: str) -> str:
    import re
    return re.sub(r"\D", "", phone or "")

demo = _digits(os.environ.get("DEMO_OUTREACH_PHONE", "918372875356"))
periskope = _digits(os.environ.get("PERISKOPE_PHONE", ""))
bot = _digits(os.environ.get("TWILIO_WHATSAPP_NUMBER", "")) or "918433775356"
if not periskope or periskope == demo:
    os.environ["PERISKOPE_PHONE"] = bot

os.environ.setdefault("PORT", "4000")

for key, val in sorted(os.environ.items()):
    if key.startswith("_"):
        continue
    print(f"export {key}={shlex.quote(val)}")
PY
