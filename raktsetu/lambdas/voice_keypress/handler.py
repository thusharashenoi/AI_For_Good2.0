"""Lambda: voice-keypress

Handles IVR digit input from the escalation call (FLOW 6).
  Digit 1 (YES): record reply, kick off FLOW 5 over WhatsApp, hang up.
  Digit 2 (NO) : record decline, move to next donor.
  No input     : log no_response (handled by Twilio timeout TwiML), no callback.
"""
from __future__ import annotations

import logging
import os
import sys
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import dynamodb_client as db  # noqa: E402
from shared import i18n, scheduler, twilio_client, twiml  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.voice_keypress")


def _params(event: dict) -> dict:
    params = dict(event.get("queryStringParameters") or {})
    body = event.get("body")
    if body:
        if event.get("isBase64Encoded"):
            import base64
            body = base64.b64decode(body).decode("utf-8")
        params.update({k: v[0] for k, v in parse_qs(body).items()})
    return params


def _record_call_status(request_id: str, donor_id: str, status: str) -> None:
    if not request_id:
        return
    req = db.get_request(request_id)
    if not req:
        return
    for d in req.get("assignedDonors", []):
        if d.get("donorId") == donor_id:
            d["callStatus"] = status
            d["status"] = "confirmed" if status == "replied_yes_via_call" else "declined"
            break
    else:
        req.setdefault("assignedDonors", []).append(
            {"donorId": donor_id, "callStatus": status,
             "status": "confirmed" if status == "replied_yes_via_call" else "declined"})
    db.save_request(req)


def handler(event, context=None):
    try:
        params = _params(event)
        digit = (params.get("Digits") or "").strip()
        request_id = params.get("requestId", "")
        donor_id = params.get("donorId", "")
        lang = params.get("lang") or "en"
        donor = db.get_donor(donor_id) if donor_id else None
        donor_phone = (donor or {}).get("phone")
        lang = (donor or {}).get("preferredLanguage", lang)

        if digit == "1":
            _record_call_status(request_id, donor_id, "replied_yes_via_call")
            # Prime the conversation so the WhatsApp reply path starts FLOW 5.
            if donor_phone:
                conv = db.get_conversation(donor_phone) or {
                    "phone_number": donor_phone, "conversationId": db.new_id(),
                    "channel": "whatsapp", "state": "REGISTRATION_COMPLETE",
                    "language": lang, "userType": "donor", "contextData": {}}
                conv["awaitingOutreachReply"] = True
                conv["activeRequestId"] = request_id
                conv["donorId"] = donor_id
                conv["state"] = "REGISTRATION_COMPLETE"
                db.save_conversation(conv)
                # Immediately advance to eligibility quick-check.
                from lambdas.whatsapp_webhook import flows
                for reply in flows.handle_message(donor_phone, "YES", channel="whatsapp"):
                    twilio_client.send_whatsapp(donor_phone, reply)
            return _xml(twiml.keypress_yes(lang))

        if digit == "2":
            _record_call_status(request_id, donor_id, "declined_via_call")
            if request_id:
                scheduler.start_outreach(request_id, extra={"advance": True})
            return _xml(twiml.keypress_no(lang))

        # No / unexpected digit.
        _record_call_status(request_id, donor_id, "no_response")
        return _xml(twiml.say_and_hangup(i18n.t("OUTREACH_DECLINED", lang), lang))
    except Exception as exc:
        logger.exception("voice_keypress error: %s", exc)
        return _xml(twiml.say_and_hangup("Thank you.", "en"))


def _xml(body: str) -> dict:
    return {"statusCode": 200, "headers": {"Content-Type": "application/xml"}, "body": body}
