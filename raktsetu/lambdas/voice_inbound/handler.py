"""Lambda: voice-inbound

Handles inbound calls to the RaktSetu Twilio voice number. Greets the caller,
gathers speech, and routes the transcribed text through the SAME conversational
FSM used for WhatsApp (so voice and chat share one brain). The donor's phone is
confirmed via IVR; on completion we promise a WhatsApp confirmation.
"""
from __future__ import annotations

import logging
import os
import sys
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import dynamodb_client as db  # noqa: E402
from shared import i18n, twiml  # noqa: E402
from lambdas.whatsapp_webhook import flows  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.voice_inbound")

GATHER_ACTION = os.environ.get("VOICE_GATHER_ACTION", "/voice/inbound/turn")


def _form(event: dict) -> dict:
    body = event.get("body")
    if body is None:
        return {k: event.get(k) for k in ("From", "To", "SpeechResult", "CallSid", "Digits")}
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode("utf-8")
    return {k: v[0] for k, v in parse_qs(body).items()}


def handler(event, context=None):
    try:
        form = _form(event)
        phone = (form.get("From") or "").strip()
        speech = (form.get("SpeechResult") or "").strip()
        conv = db.get_conversation(phone)
        lang = (conv or {}).get("language", i18n.DEFAULT_LANG)

        # First leg of the call (no speech yet): greet + gather.
        if not speech:
            if conv is None:
                lang = i18n.DEFAULT_LANG
            return _xml(twiml.inbound_welcome(lang, GATHER_ACTION))

        # Detect language from speech for brand-new callers.
        if conv is None:
            lang = i18n.detect_language(speech)

        replies = flows.handle_message(phone, speech, channel="voice")
        spoken = " ".join(replies) if replies else i18n.t("GENERIC_FALLBACK", lang)

        conv = db.get_conversation(phone) or {}
        lang = conv.get("language", lang)

        # If the flow reached a terminal state, say goodbye + hang up.
        if conv.get("state") in ("REGISTRATION_COMPLETE", "ENDED", "REQUEST_RAISED"):
            closing = {
                "en": "We will send your confirmation on WhatsApp to this number. Thank you!",
                "hi": "हम इस नंबर पर WhatsApp पर आपकी पुष्टि भेजेंगे। धन्यवाद!",
                "te": "ఈ నంబర్‌కు WhatsApp లో మీ నిర్ధారణ పంపుతాం. ధన్యవాదాలు!",
            }.get(lang, "We will send your confirmation on WhatsApp. Thank you!")
            return _xml(twiml.say_and_hangup(f"{spoken} {closing}", lang))

        return _xml(twiml.say_and_gather(spoken, lang, GATHER_ACTION, input_type="speech"))
    except Exception as exc:
        logger.exception("voice_inbound error: %s", exc)
        return _xml(twiml.say_and_hangup(
            "Sorry, we hit a problem. Please message us on WhatsApp. Goodbye.", "en"))


def _xml(body: str) -> dict:
    return {"statusCode": 200, "headers": {"Content-Type": "application/xml"}, "body": body}
