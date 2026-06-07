"""Bridge mobilization outreach — prime conversation + blood request from admin broadcast."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def ensure_bridge_request(db, bridge: dict, lead_donor: dict, patient_name: str | None,
                          patient_meta: dict | None = None) -> dict:
    """Persist (or return) a blood request tied to this bridge mobilization."""
    bridge_id = bridge.get("bridgeId") or "unknown"
    request_id = f"bridge-{bridge_id}"
    existing = db.get_request(request_id)
    if existing:
        return existing

    meta = patient_meta or {}
    required_by = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")
    req = {
        "requestId": request_id,
        "patientId": bridge.get("patientId"),
        "patientName": patient_name or meta.get("name") or "Patient",
        "patientPhone": meta.get("phone"),
        "bloodGroup": bridge.get("bloodGroup") or meta.get("blood_group_norm"),
        "unitsNeeded": 1,
        "hospital": meta.get("hospital") or "Blood Warriors partner hospital",
        "city": meta.get("city") or "Hyderabad",
        "requiredBy": required_by,
        "urgencyLevel": "high",
        "status": "open",
        "assignedDonors": [],
        "bridgeId": bridge_id,
        "createdAt": db.now_iso(),
        "createdBy": "bridge_broadcast",
    }
    db.save_request(req)
    return req


def prime_bridge_conversation(conv: dict, bridge: dict, lead_donor: dict,
                              request: dict, patient_name: str | None) -> dict:
    """Load ranked-donor context so we never re-ask name / group / city on bridge demo."""
    ctx = conv.setdefault("contextData", {})
    ctx.pop("medicalFlags", None)
    ctx["bridgeDonorName"] = lead_donor.get("name")
    ctx["bridgeDonorGroup"] = lead_donor.get("bloodGroup")
    ctx["bridgeDonorCity"] = lead_donor.get("city")
    ctx["bridgePatientName"] = patient_name or request.get("patientName")

    conv["bridgeOutreach"] = True
    conv["bridgeId"] = bridge.get("bridgeId")
    conv["bridgePatientId"] = bridge.get("patientId")
    conv["awaitingOutreachReply"] = True
    conv["state"] = "REGISTRATION_COMPLETE"
    conv["userType"] = "donor"
    conv["donorId"] = lead_donor.get("donorId")
    conv["activeRequestId"] = request.get("requestId")
    return conv
