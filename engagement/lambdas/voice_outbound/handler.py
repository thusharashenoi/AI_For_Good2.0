"""Lambda: voice-outbound

Serves the TwiML for an outbound *escalation* call (FLOW 6). Twilio fetches this
URL when the call connects. The blood group / donor area / language are passed as
query string params by the trigger-voice Lambda.
"""
from __future__ import annotations

import logging
import os
import sys
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import dynamodb_client as db  # noqa: E402
from shared import twiml  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.voice_outbound")

KEYPRESS_ACTION = os.environ.get("VOICE_KEYPRESS_ACTION", "/voice/keypress")


def _params(event: dict) -> dict:
    params = dict(event.get("queryStringParameters") or {})
    body = event.get("body")
    if body:
        if event.get("isBase64Encoded"):
            import base64
            body = base64.b64decode(body).decode("utf-8")
        params.update({k: v[0] for k, v in parse_qs(body).items()})
    return params


def handler(event, context=None):
    try:
        params = _params(event)
        request_id = params.get("requestId", "")
        donor_id = params.get("donorId", "")
        lang = params.get("lang") or "en"
        blood_group = params.get("bloodGroup")
        donor_area = params.get("donorArea")

        if (not blood_group or not donor_area) and request_id:
            req = db.get_request(request_id) or {}
            blood_group = blood_group or req.get("bloodGroup", "the required")
            donor = db.get_donor(donor_id) if donor_id else None
            donor_area = donor_area or (donor or {}).get("area", "your area")

        action = f"{KEYPRESS_ACTION}?requestId={request_id}&donorId={donor_id}&lang={lang}"
        body = twiml.outreach_call(blood_group or "the required", donor_area or "your area",
                                   lang, action)
        return _xml(body)
    except Exception as exc:
        logger.exception("voice_outbound error: %s", exc)
        return _xml(twiml.say_and_hangup("Thank you for being a Blood Warrior.", "en"))


def _xml(body: str) -> dict:
    return {"statusCode": 200, "headers": {"Content-Type": "application/xml"}, "body": body}
