"""Poll Twilio for inbound WhatsApp when webhooks don't reach local ngrok.

Twilio reliably stores inbound messages even if the Senders API webhook POST
fails (common with ngrok free tier). This background poller picks them up,
runs the same router as /whatsapp, and replies via send_whatsapp_direct.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config, dynamodb_client as db, twilio_client

logger = logging.getLogger("raktsetu.twilio_poll")


def _bot_wa() -> str:
    num = config.get("TWILIO_WHATSAPP_NUMBER") or "whatsapp:+919076150904"
    if num.startswith("whatsapp:"):
        return num
    if num.startswith("+"):
        return f"whatsapp:{num}"
    return f"whatsapp:+{num}"


def _clean_phone(raw: str) -> str:
    return (raw or "").replace("whatsapp:", "").strip()


def poll_once() -> int:
    if not config.get("TWILIO_ACCOUNT_SID") or not config.get("TWILIO_AUTH_TOKEN"):
        return 0
    if config.LOCAL_MODE and config.get("TWILIO_INBOUND_POLL", "1") != "1":
        return 0

    from lambdas.whatsapp_webhook import router  # noqa: WPS433

    client = twilio_client._twilio()
    bot = _bot_wa()
    handled = 0
    for msg in client.messages.list(to=bot, limit=20):
        if msg.direction != "inbound":
            continue
        sid = msg.sid
        if db.is_message_processed(sid):
            continue
        phone = _clean_phone(msg.from_)
        body = (msg.body or "").strip()
        if not phone or not body:
            db.mark_message_processed(sid)
            continue
        conv = db.get_conversation(phone) or {}
        created = msg.date_created
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        recent = created >= datetime.now(timezone.utc) - timedelta(minutes=15)
        if not recent and not conv.get("awaitingOutreachReply"):
            db.mark_message_processed(sid)
            continue
        logger.info("Polled inbound WhatsApp %s: %s", phone, body[:80])
        replies = router.respond(phone, body, channel="whatsapp")
        for reply in replies:
            twilio_client.send_whatsapp_direct(phone, reply)
        db.mark_message_processed(sid)
        handled += 1
    return handled


def start_background(interval_sec: float = 4.0) -> None:
    """Start daemon thread — safe to call once at server startup."""

    def _loop() -> None:
        while True:
            try:
                n = poll_once()
                if n:
                    logger.info("Twilio poll handled %s inbound message(s)", n)
            except Exception:
                logger.exception("Twilio inbound poll failed")
            time.sleep(interval_sec)

    threading.Thread(target=_loop, daemon=True, name="twilio-inbound-poll").start()
    logger.info("Twilio inbound poller started (every %.0fs)", interval_sec)
