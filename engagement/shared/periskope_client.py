"""Periskope WhatsApp gateway (proxy on your own number, no Meta templates).

Lets RaktSetu send/receive free-form WhatsApp from +918433775356 via Periskope's
unofficial API — ideal for the hackathon demo where business-initiated template
approval would otherwise be required.

Send:    POST https://api.periskope.app/v1/message/send
Headers: Authorization: Bearer <PERISKOPE_API_KEY>, x-phone: <org phone digits>
Body:    {"chat_id": "<recipient digits>@c.us", "message": "..."}

In LOCAL_MODE sends are recorded to the Twilio outbox (so the local server and
tests don't hit the network); inbound parsing always works offline.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Optional

from . import config
from . import twilio_client  # reuse OUTBOX for local recording

logger = logging.getLogger("raktsetu.periskope")

SEND_URL = "https://api.periskope.app/v1/message/send"


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


DEFAULT_BOT_PHONE = "918433775356"


def bot_phone_digits() -> str:
    """Blood Warriors WhatsApp bot (sender for outbound mobilization messages)."""
    twilio = config.get("TWILIO_WHATSAPP_NUMBER") or ""
    if twilio:
        return _digits(twilio) or DEFAULT_BOT_PHONE
    return _digits(config.get("PERISKOPE_PHONE") or DEFAULT_BOT_PHONE) or DEFAULT_BOT_PHONE


def org_phone() -> str:
    """Periskope x-phone header — must be the bot number, not the demo recipient."""
    p = config.get("PERISKOPE_PHONE") or bot_phone_digits()
    digits = _digits(p)
    demo = _digits(config.get("DEMO_OUTREACH_PHONE") or "")
    bot = bot_phone_digits()
    if demo and digits == demo:
        logger.warning(
            "PERISKOPE_PHONE (%s) matches DEMO_OUTREACH_PHONE; using bot sender %s",
            digits,
            bot,
        )
        return bot
    return digits or bot


def chat_id_for(phone: str) -> str:
    d = _digits(phone)
    return d if d.endswith("@c.us") else f"{d}@c.us"


def _send_live() -> bool:
    """LOCAL_MODE stubs network calls for tests; PERISKOPE_LIVE_SENDS=1 keeps real WhatsApp replies."""
    return not config.LOCAL_MODE or config.get("PERISKOPE_LIVE_SENDS") == "1"


def send_message(to_phone: str, text: str) -> Dict:
    record = {"channel": "periskope", "to": _digits(to_phone), "body": text}
    if not _send_live():
        twilio_client.OUTBOX.append(record)
        logger.info("[LOCAL PERISKOPE] -> %s: %s", to_phone, text)
        return {"ok": True, "local": True, **record}
    api_key = config.require("PERISKOPE_API_KEY")
    try:
        import requests

        resp = requests.post(
            SEND_URL,
            headers={"Authorization": f"Bearer {api_key}", "x-phone": org_phone(),
                     "Content-Type": "application/json"},
            json={"chat_id": chat_id_for(to_phone), "message": text},
            timeout=10)
        resp.raise_for_status()
        return {"ok": True, **record, "response": _safe_json(resp)}
    except Exception as exc:
        logger.exception("Periskope send failed: %s", exc)
        return {"ok": False, "error": str(exc), **record}


def _safe_json(resp) -> Dict:
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code}


def parse_inbound(payload: Dict) -> Optional[Dict]:
    """Normalise a Periskope webhook payload into {phone, text, message_id, from_me}.

    Tolerant to schema variations across Periskope webhook versions. Returns None
    for events we should ignore (non-message events, our own outbound echoes,
    empty bodies).
    """
    if not isinstance(payload, dict):
        return None

    event = (payload.get("event") or payload.get("type") or "").lower()
    if event and "message" not in event:
        return None

    # The message object can be at the top level or under "message"/"data".
    msg = payload.get("message") or payload.get("data") or payload
    if not isinstance(msg, dict):
        return None

    from_me = bool(msg.get("from_me") or msg.get("fromMe") or
                   (msg.get("key") or {}).get("fromMe"))
    if from_me:
        return None  # ignore echoes of our own sends

    body = (msg.get("body") or msg.get("message") or msg.get("text") or "")
    if isinstance(body, dict):  # some payloads nest {text: {body: "..."}}
        body = body.get("body") or body.get("text") or ""
    body = (body or "").strip()

    chat = msg.get("chat") or {}
    chat_id = (msg.get("chat_id") or chat.get("id") or msg.get("from")
               or (msg.get("key") or {}).get("remoteJid") or "")
    phone = _digits(str(chat_id).split("@")[0])
    if not phone:
        return None

    message_id = (msg.get("message_id") or msg.get("id")
                  or (msg.get("key") or {}).get("id") or "")
    return {"phone": f"+{phone}", "text": body, "message_id": message_id, "from_me": False}
