"""Lambda: periskope-webhook

Receives Periskope `message.created` webhook events (inbound WhatsApp on our own
number via the Periskope proxy), runs the strict agent (FSM fallback), and replies
through Periskope. Idempotent on the Periskope message id.
"""
from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import dynamodb_client as db  # noqa: E402
from shared import periskope_client  # noqa: E402
from lambdas.whatsapp_webhook import router  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.periskope_webhook")


def _payload(event: dict) -> dict:
    body = event.get("body")
    if body is None:
        return event  # direct invoke with the raw payload
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode("utf-8")
    try:
        return json.loads(body)
    except Exception:
        return {}


def handler(event, context=None):
    try:
        payload = _payload(event)
        inbound = periskope_client.parse_inbound(payload)
        if not inbound or not inbound["text"]:
            return _ok()

        phone, text, msg_id = inbound["phone"], inbound["text"], inbound["message_id"]
        if msg_id and db.is_message_processed(msg_id):
            return _ok()

        logger.info("Periskope inbound %s: %s", phone, text)
        replies = router.respond(phone, text, channel="whatsapp")
        for reply in replies:
            periskope_client.send_message(phone, reply)

        if msg_id:
            db.mark_message_processed(msg_id)
        return _ok(replies)
    except Exception as exc:
        logger.exception("periskope webhook error: %s", exc)
        return _ok()


def _ok(replies=None):
    out = {"statusCode": 200, "headers": {"Content-Type": "application/json"},
           "body": json.dumps({"ok": True})}
    if replies is not None:
        out["replies"] = replies
    return out
