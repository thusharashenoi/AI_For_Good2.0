"""Twilio messaging + voice helpers.

All credentials come from shared.config (Secrets Manager in prod). In LOCAL_MODE
sends are logged and recorded to an in-memory outbox instead of hitting Twilio,
so the test harness can assert on outbound messages.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from . import config

logger = logging.getLogger("raktsetu.twilio")

# In-memory outbox for LOCAL_MODE / tests.
OUTBOX: List[Dict] = []

_client = None


def _twilio():
    global _client
    if _client is None:
        from twilio.rest import Client

        _client = Client(config.require("TWILIO_ACCOUNT_SID"),
                         config.require("TWILIO_AUTH_TOKEN"))
    return _client


def _wa(number: str) -> str:
    """Ensure a whatsapp: prefix on the address."""
    if number.startswith("whatsapp:"):
        return number
    return f"whatsapp:{number}"


def send_whatsapp_direct(to: str, body: str, media_url: Optional[str] = None) -> Dict:
    """Send via Twilio WhatsApp API (ignores WA_PROVIDER=periskope).

    Use for mobilization broadcasts where the sender must be TWILIO_WHATSAPP_NUMBER
    (e.g. +919076150904) and the recipient is a separate demo/coordinator phone.
    """
    from_number = config.get("TWILIO_WHATSAPP_NUMBER", "whatsapp:+919076150904")
    record = {"channel": "whatsapp", "to": _wa(to), "from": _wa(from_number),
              "body": body, "mediaUrl": media_url, "at": time.time()}
    if config.LOCAL_MODE:
        OUTBOX.append(record)
        logger.info("[LOCAL WA direct] -> %s: %s", to, body)
        return {"sid": f"LOCAL-{len(OUTBOX)}", "ok": True, **record}
    kwargs = {"from_": _wa(from_number), "to": _wa(to), "body": body}
    if media_url:
        kwargs["media_url"] = [media_url]
    msg = _twilio().messages.create(**kwargs)
    return {"sid": msg.sid, "ok": True, **record}


def send_whatsapp(to: str, body: str, media_url: Optional[str] = None) -> Dict:
    """Send a free-form WhatsApp message (only valid inside the 24h window).

    Routes through Periskope when WA_PROVIDER=periskope (proxy on our own number,
    no Meta template needed); otherwise uses the Twilio WhatsApp API.
    """
    if (config.get("WA_PROVIDER") or "").lower() == "periskope":
        from . import periskope_client
        res = periskope_client.send_message(to, body)
        return {"sid": res.get("response", {}).get("message_id") if isinstance(res.get("response"), dict) else None,
                "provider": "periskope", "ok": res.get("ok"), "to": to, "body": body}
    from_number = config.get("TWILIO_WHATSAPP_NUMBER", "whatsapp:+919076150904")
    record = {"channel": "whatsapp", "to": _wa(to), "from": _wa(from_number),
              "body": body, "mediaUrl": media_url, "at": time.time()}
    if config.LOCAL_MODE:
        OUTBOX.append(record)
        logger.info("[LOCAL WA] -> %s: %s", to, body)
        return {"sid": f"LOCAL-{len(OUTBOX)}", **record}
    kwargs = {"from_": _wa(from_number), "to": _wa(to), "body": body}
    if media_url:
        kwargs["media_url"] = [media_url]
    msg = _twilio().messages.create(**kwargs)
    return {"sid": msg.sid, **record}


def send_whatsapp_template(to: str, content_sid: str, variables: Dict[str, str]) -> Dict:
    """Send an approved WhatsApp template (business-initiated, outside 24h window).

    Uses Twilio Content API content_sid + content_variables.
    """
    from_number = config.get("TWILIO_WHATSAPP_NUMBER", "whatsapp:+919076150904")
    record = {"channel": "whatsapp_template", "to": _wa(to), "contentSid": content_sid,
              "variables": variables, "at": time.time()}
    if config.LOCAL_MODE:
        OUTBOX.append(record)
        logger.info("[LOCAL WA TEMPLATE] -> %s sid=%s vars=%s", to, content_sid, variables)
        return {"sid": f"LOCAL-{len(OUTBOX)}", **record}
    import json

    msg = _twilio().messages.create(
        from_=_wa(from_number),
        to=_wa(to),
        content_sid=content_sid,
        content_variables=json.dumps(variables),
    )
    return {"sid": msg.sid, **record}


def make_call(to: str, twiml_url: str) -> Dict:
    """Initiate an outbound voice call that fetches TwiML from twiml_url."""
    voice_number = config.require("TWILIO_VOICE_NUMBER")
    record = {"channel": "voice", "to": to, "from": voice_number, "url": twiml_url,
              "at": time.time()}
    if config.LOCAL_MODE:
        OUTBOX.append(record)
        logger.info("[LOCAL CALL] -> %s url=%s", to, twiml_url)
        return {"sid": f"LOCAL-CALL-{len(OUTBOX)}", **record}
    call = _twilio().calls.create(to=to, from_=voice_number, url=twiml_url)
    return {"sid": call.sid, **record}


def make_call_with_twiml(to: str, twiml: str) -> Dict:
    """Initiate an outbound call passing inline TwiML (no public URL needed)."""
    voice_number = config.require("TWILIO_VOICE_NUMBER")
    record = {"channel": "voice", "to": to, "from": voice_number, "twiml": twiml, "at": time.time()}
    if config.LOCAL_MODE:
        OUTBOX.append(record)
        logger.info("[LOCAL CALL inline twiml] -> %s", to)
        return {"sid": f"LOCAL-CALL-{len(OUTBOX)}", **record}
    call = _twilio().calls.create(to=to, from_=voice_number, twiml=twiml)
    return {"sid": call.sid, **record}


def clear_outbox() -> None:
    OUTBOX.clear()
