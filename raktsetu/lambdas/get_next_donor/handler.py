"""Lambda: get-next-donor  (SFN task: TryNextDonor)

Advances the request's ranked-donor cursor and reports how many donors remain.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import dynamodb_client as db  # noqa: E402


def handler(event, context=None):
    request_id = event.get("requestId")
    req = db.get_request(request_id) if request_id else None
    if not req:
        return {"requestId": request_id, "donorsRemaining": 0}

    queue = req.get("rankedDonorQueue") or []
    req["currentDonorIndex"] = req.get("currentDonorIndex", 0) + 1
    db.save_request(req)
    remaining = max(0, len(queue) - req["currentDonorIndex"])
    return {"requestId": request_id, "donorsRemaining": remaining,
            "currentDonorIndex": req["currentDonorIndex"]}
