"""Lambda: whatsapp-webhook

Receives the Twilio inbound WhatsApp webhook (application/x-www-form-urlencoded),
extracts the sender + message, runs the conversational FSM, and replies via the
Twilio REST API.

Resilience:
- Idempotent on Twilio MessageSid (the webhook can fire twice).
- Always returns 200 with empty TwiML so Twilio does not retry on our errors
  (a retry would duplicate messages); errors are logged to CloudWatch.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from urllib.parse import parse_qs

# Allow `from shared ...` and `from . import` both in Lambda (layer) and local.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import dynamodb_client as db  # noqa: E402
from shared import twilio_client  # noqa: E402

try:
    from . import router  # packaged import
except ImportError:  # local / direct invoke
    import router  # type: ignore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.whatsapp_webhook")

_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _parse_body(event: dict) -> dict:
    """Support API Gateway proxy, function URL, and direct test payloads."""
    body = event.get("body")
    if body is None:
        # Direct invocation with already-parsed fields.
        return {k: event.get(k) for k in ("From", "Body", "MessageSid", "ProfileName")}
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode("utf-8")
    parsed = parse_qs(body)
    return {k: v[0] for k, v in parsed.items()}


def _clean_phone(raw: str) -> str:
    return (raw or "").replace("whatsapp:", "").strip()


def handler(event, context=None):
    try:
        fields = _parse_body(event)
        phone = _clean_phone(fields.get("From", ""))
        message = (fields.get("Body") or "").strip()
        message_id = fields.get("MessageSid") or fields.get("SmsMessageSid") or ""

        if not phone:
            logger.warning("No sender in webhook payload: %s", fields)
            return _response(_EMPTY_TWIML)

        # Idempotency guard.
        if message_id and db.is_message_processed(message_id):
            logger.info("Duplicate message %s ignored", message_id)
            return _response(_EMPTY_TWIML)

        replies = router.respond(phone, message, channel="whatsapp")

        for reply in replies:
            try:
                twilio_client.send_whatsapp(phone, reply)
            except Exception as exc:
                logger.exception("Failed to send WhatsApp reply: %s", exc)

        if message_id:
            db.mark_message_processed(message_id)

        return _response(_EMPTY_TWIML, replies=replies)
    except Exception as exc:  # never 500 to Twilio
        logger.exception("Unhandled error in whatsapp webhook: %s", exc)
        return _response(_EMPTY_TWIML)


def _response(twiml: str, replies=None) -> dict:
    out = {
        "statusCode": 200,
        "headers": {"Content-Type": "application/xml"},
        "body": twiml,
    }
    # Surface replies for local/direct test harness assertions.
    if replies is not None:
        out["replies"] = replies
    return out


# Local quick test:  LOCAL_MODE=1 python handler.py "+91..." "Hi"
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("phone")
    parser.add_argument("message")
    args = parser.parse_args()
    res = handler({"From": f"whatsapp:{args.phone}", "Body": args.message,
                   "MessageSid": db.new_id()})
    print(json.dumps(res.get("replies", []), ensure_ascii=False, indent=2))
