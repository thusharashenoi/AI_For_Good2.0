#!/usr/bin/env python3
"""Point the Twilio WhatsApp sender inbound webhook at our local/public server.

Broadcast mobilization sends via Twilio (bot → demo phone). Replies must hit
POST {base_url}/whatsapp or YES/NO will never be processed.

Usage:
    eval "$(../scripts/load_env.sh)"
    python scripts/setup_twilio_webhook.py --base-url https://xxxx.ngrok-free.app
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import config  # noqa: E402

SENDERS_URL = "https://messaging.twilio.com/v2/Channels/Senders"


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _bot_digits() -> str:
    raw = config.get("TWILIO_WHATSAPP_NUMBER") or config.get("PERISKOPE_PHONE") or "919076150904"
    return _digits(raw)


def configure(base_url: str) -> dict:
    base = base_url.rstrip("/")
    webhook = f"{base}/whatsapp"
    sid = config.require("TWILIO_ACCOUNT_SID")
    token = config.require("TWILIO_AUTH_TOKEN")
    bot = _bot_digits()

    import requests

    auth = (sid, token)
    resp = requests.get(SENDERS_URL, params={"Channel": "whatsapp"}, auth=auth, timeout=15)
    resp.raise_for_status()
    senders = resp.json().get("senders") or []

    updated = []
    for sender in senders:
        sender_id = sender.get("sender_id") or ""
        digits = _digits(sender_id)
        if not (digits.endswith(bot) or bot.endswith(digits)):
            continue
        sender_sid = sender.get("sid")
        if not sender_sid:
            continue
        patch = requests.post(
            f"{SENDERS_URL}/{sender_sid}",
            auth=auth,
            json={"webhook": {"callback_method": "POST", "callback_url": webhook}},
            timeout=15,
        )
        patch.raise_for_status()
        updated.append(sender_id)
        print(f"Updated {sender_id} inbound webhook → {webhook}")

    if not updated:
        print(f"WARNING: no WhatsApp sender matched bot {bot}.")
        print("Set manually in Twilio Console → Messaging → WhatsApp Senders:")
        print(f"  When a message comes in → {webhook}")

    return {"ok": bool(updated), "webhook": webhook, "senders": updated}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=os.environ.get("VAPI_SERVER_URL") or os.environ.get("NGROK_URL"),
        help="Public base URL (ngrok), without trailing path",
    )
    args = parser.parse_args()
    if not args.base_url:
        print("ERROR: pass --base-url or set VAPI_SERVER_URL / NGROK_URL", file=sys.stderr)
        sys.exit(1)
    configure(args.base_url)


if __name__ == "__main__":
    main()
