"""Lambda: check-outreach-status  (SFN task: CheckReplyStatus)

Reads the current donor's reply status from the request after the Wait state.
Returns {"status": "confirmed" | "declined" | "no_response"} for the Choice.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import dynamodb_client as db  # noqa: E402


def _current_donor_id(req: dict):
    queue = req.get("rankedDonorQueue") or []
    idx = req.get("currentDonorIndex", 0)
    return queue[idx] if idx < len(queue) else None


def handler(event, context=None):
    request_id = event.get("requestId")
    req = db.get_request(request_id) if request_id else None
    if not req:
        return {"requestId": request_id, "status": "no_response"}

    donor_id = _current_donor_id(req)
    entry = next((a for a in req.get("assignedDonors", []) if a.get("donorId") == donor_id), None)
    raw = (entry or {}).get("status", "outreach_sent")

    mapping = {
        "confirmed": "confirmed",
        "appointment_booked": "confirmed",
        "declined": "declined",
        "snoozed": "declined",       # snoozed donors are scheduled separately
        "outreach_sent": "no_response",
        "no_response": "no_response",
    }
    status = mapping.get(raw, "no_response")
    return {"requestId": request_id, "donorId": donor_id, "status": status}
