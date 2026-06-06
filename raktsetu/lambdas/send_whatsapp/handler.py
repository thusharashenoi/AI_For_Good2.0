"""Lambda: send-whatsapp-template  (SFN task: SendOutreachToTopDonor)

Sends the approved WhatsApp template "donation_request_v1" to the current top
donor on a request's ranked queue, and primes that donor's conversation so a
WhatsApp/voice YES reply routes into the eligibility quick-check (FLOW 5).

Template vars: 1=first name, 2=blood group, 3=donor area.
"""
from __future__ import annotations

import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import config, dynamodb_client as db, twilio_client  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.send_whatsapp")


def _current_donor(req: dict):
    queue = req.get("rankedDonorQueue") or []
    idx = req.get("currentDonorIndex", 0)
    if idx >= len(queue):
        return None
    return db.get_donor(queue[idx])


def handler(event, context=None):
    request_id = event.get("requestId")
    req = db.get_request(request_id) if request_id else None
    if not req:
        return {"requestId": request_id, "status": "error", "reason": "request_not_found"}

    donor = _current_donor(req)
    if not donor:
        return {"requestId": request_id, "status": "no_donors", "donorsRemaining": 0}

    first_name = (donor.get("name") or "Friend").split(" ")[0]
    variables = {"1": first_name, "2": req.get("bloodGroup", ""), "3": donor.get("area", "")}
    template_sid = config.get("TWILIO_TEMPLATE_SID_DONATION_REQUEST", "HX_LOCAL_TEMPLATE")

    # Optional rate-limit spacing for bulk sends.
    delay = float(event.get("rateLimitDelaySeconds", 0) or 0)
    if delay and not config.LOCAL_MODE:
        time.sleep(min(delay, 5))

    try:
        result = twilio_client.send_whatsapp_template(donor["phone"], template_sid, variables)
    except Exception as exc:
        logger.exception("template send failed: %s", exc)
        result = {"sid": None, "error": str(exc)}

    # Record outreach attempt + prime the donor conversation.
    assigned = req.setdefault("assignedDonors", [])
    if not any(a.get("donorId") == donor["donorId"] for a in assigned):
        assigned.append({"donorId": donor["donorId"], "status": "outreach_sent",
                         "outreachAt": db.now_iso(), "channel": "whatsapp"})
    db.save_request(req)

    conv = db.get_conversation(donor["phone"]) or {
        "phone_number": donor["phone"], "conversationId": db.new_id(),
        "channel": "whatsapp", "state": "REGISTRATION_COMPLETE",
        "language": donor.get("preferredLanguage", "en"), "userType": "donor",
        "contextData": {}}
    conv["awaitingOutreachReply"] = True
    conv["activeRequestId"] = request_id
    conv["donorId"] = donor["donorId"]
    db.save_conversation(conv)

    return {"requestId": request_id, "status": "outreach_sent",
            "donorId": donor["donorId"], "messageSid": result.get("sid")}
