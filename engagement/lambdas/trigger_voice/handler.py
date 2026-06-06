"""Lambda: trigger-voice-call  (SFN task: EscalateToCall)

Initiates an outbound Twilio voice call to the current donor. Twilio fetches the
escalation TwiML from the voice-outbound endpoint (URL passed at call creation).
"""
from __future__ import annotations

import logging
import os
import sys
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import config, dynamodb_client as db, exotel_client, twilio_client, vapi_client  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.trigger_voice")

VOICE_OUTBOUND_URL = os.environ.get("VOICE_OUTBOUND_URL", "https://example.com/voice/outbound")


def _current_donor(req: dict):
    queue = req.get("rankedDonorQueue") or []
    idx = req.get("currentDonorIndex", 0)
    return db.get_donor(queue[idx]) if idx < len(queue) else None


def handler(event, context=None):
    request_id = event.get("requestId")
    req = db.get_request(request_id) if request_id else None
    if not req:
        return {"requestId": request_id, "status": "error"}
    donor = _current_donor(req)
    if not donor:
        return {"requestId": request_id, "status": "no_donors"}

    provider = (config.get("VOICE_PROVIDER") or "vapi").lower()
    try:
        if provider == "exotel":
            # Trial-friendly: Exotel dials donor, flow bridges to Vapi SIP (Veeru).
            result = exotel_client.connect_to_flow(donor["phone"])
            call = {"sid": result.get("sid"), **result}
        elif provider == "vapi":
            # Vapi assistant handles the conversation + tools; pass outreach context.
            req_vars = {
                "requestId": request_id,
                "donorId": donor["donorId"],
                "bloodGroup": req.get("bloodGroup", ""),
                "donorArea": donor.get("area", ""),
                "donorName": (donor.get("name") or "").split(" ")[0],
                "hospital": req.get("hospital", ""),
                "outreachMode": "true",
            }
            from shared import vapi_context
            vapi_context.ensure_outreach_primed(donor["phone"], request_id, donor["donorId"])
            result = vapi_client.create_outbound_call(donor["phone"], variables=req_vars)
            call = {"sid": result.get("callId") or result.get("sid"), **result}
        else:
            qs = urlencode({
                "requestId": request_id, "donorId": donor["donorId"],
                "lang": donor.get("preferredLanguage", "en"),
                "bloodGroup": req.get("bloodGroup", ""), "donorArea": donor.get("area", "")})
            call = twilio_client.make_call(donor["phone"], f"{VOICE_OUTBOUND_URL}?{qs}")
    except Exception as exc:
        logger.exception("trigger_voice failed: %s", exc)
        call = {"sid": None, "error": str(exc)}

    for a in req.get("assignedDonors", []):
        if a.get("donorId") == donor["donorId"]:
            a["callStatus"] = "calling"
            a["callSid"] = call.get("sid")
    db.save_request(req)

    voice_placed = bool(call.get("ok")) and bool(call.get("sid") or call.get("callId"))
    return {
        "requestId": request_id,
        "donorId": donor["donorId"],
        "status": "calling" if voice_placed else "call_failed",
        "callSid": call.get("sid"),
        "callId": call.get("callId") or call.get("sid"),
        "ok": call.get("ok", voice_placed),
        "voicePlaced": voice_placed,
        "error": call.get("error"),
    }
