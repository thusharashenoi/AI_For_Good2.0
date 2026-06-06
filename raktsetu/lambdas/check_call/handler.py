"""Lambda: check-call-status  (SFN task: CheckCallStatus)

Reads the current donor's call response (set by voice-keypress) and maps it to
the Step Functions Choice vocabulary.
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
        return {"requestId": request_id, "status": "declined"}
    donor_id = _current_donor_id(req)
    entry = next((a for a in req.get("assignedDonors", []) if a.get("donorId") == donor_id), None)
    call_status = (entry or {}).get("callStatus", "no_response")
    mapping = {
        "replied_yes_via_call": "confirmed",
        "declined_via_call": "declined",
        "no_response": "declined",
        "calling": "declined",
    }
    return {"requestId": request_id, "donorId": donor_id,
            "status": mapping.get(call_status, "declined")}
