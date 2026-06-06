"""Lambda: matching-engine  (Step Functions task: GetRankedDonors)

Input : {"requestId": "..."}
Output: {"requestId", "bloodGroup", "rankedDonors": [...], "donorsRemaining",
         "coverage": {...}}

Filters all donors for eligibility against the request, ranks them by score,
computes a graph coverage score, and persists the ranked queue on the request so
TryNextDonor can pop from it.
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import dynamodb_client as db  # noqa: E402

try:
    from . import eligibility, graph, scoring
except ImportError:  # local / direct invoke
    import eligibility, graph, scoring  # type: ignore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.matching_engine")


def handler(event, context=None):
    request_id = event.get("requestId")
    req = db.get_request(request_id) if request_id else None
    if not req:
        logger.warning("matching-engine: request %s not found", request_id)
        return {"requestId": request_id, "rankedDonors": [], "donorsRemaining": 0}

    all_donors = db.all_donors()
    eligible = eligibility.filter_eligible(all_donors, req)
    ranked = scoring.rank_donors(eligible, req)

    # Graph coverage (explainable, demo-friendly).
    coverage = {}
    try:
        patients = [{"patientId": req.get("patientId"), "bloodGroup": req.get("bloodGroup"),
                     "lat": req.get("hospitalLat"), "lng": req.get("hospitalLng"),
                     "area": req.get("area")}]
        g = graph.build_graph(all_donors, patients)
        coverage = graph.coverage_score(
            g, req.get("patientId"), {d["donorId"] for d in eligible})
    except Exception as exc:
        logger.warning("graph coverage failed: %s", exc)

    # Persist the ranked queue on the request.
    req["rankedDonorQueue"] = [d["donorId"] for d in ranked]
    req["currentDonorIndex"] = 0
    req["coverage"] = coverage
    if ranked and req.get("status") == "open":
        req["status"] = "outreach_started"
    db.save_request(req)

    return {
        "requestId": request_id,
        "bloodGroup": req.get("bloodGroup"),
        "rankedDonors": ranked,
        "donorsRemaining": len(ranked),
        "coverage": coverage,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(handler({"requestId": sys.argv[1] if len(sys.argv) > 1 else ""}),
                     indent=2, ensure_ascii=False))
