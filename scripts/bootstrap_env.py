"""Load repo .env and dev-demo overrides for local processes."""
from __future__ import annotations

import os
import re
from pathlib import Path


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _normalize_sender_phone() -> None:
    """PERISKOPE_PHONE is the bot sender; DEMO_OUTREACH_PHONE is the recipient."""
    demo = _digits(os.environ.get("DEMO_OUTREACH_PHONE", "918372875356"))
    periskope = _digits(os.environ.get("PERISKOPE_PHONE", ""))
    bot = _digits(os.environ.get("TWILIO_WHATSAPP_NUMBER", "")) or "919076150904"
    if not periskope or periskope == demo:
        os.environ["PERISKOPE_PHONE"] = bot


def bootstrap() -> None:
    root = Path(__file__).resolve().parents[1]
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
        "DEMO_OUTREACH_PHONE": "+919372875356",
        "DEMO_OUTREACH_CALL_DELAY_SEC": "30",
    }
    for key, val in overrides.items():
        os.environ[key] = val

    _normalize_sender_phone()

    os.environ.setdefault("AWS_DEFAULT_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    os.environ.setdefault("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
