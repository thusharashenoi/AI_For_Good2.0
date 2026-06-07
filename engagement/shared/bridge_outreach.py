"""Bridge mobilization outreach — prime conversation + blood request from admin broadcast."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _normalize_phone(phone: str) -> str:
    p = phone.strip()
    if not p.startswith("+"):
        p = f"+{p.lstrip('+')}"
    return p


def _phone_digits(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


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


def ensure_demo_outreach_donor(db, demo_phone: str, lead_donor: dict) -> dict:
    """Donor profile for escalation — voice calls go to the demo phone."""
    demo_phone = _normalize_phone(demo_phone)
    for variant in (demo_phone, demo_phone.lstrip("+"), f"+{_phone_digits(demo_phone)}"):
        existing = db.get_donor_by_phone(variant)
        if existing:
            return existing

    digits = _phone_digits(demo_phone)
    donor_id = f"demo-{digits[-10:]}"
    donor = {
        "donorId": donor_id,
        "phone": demo_phone,
        "name": lead_donor.get("name") or "Demo Donor",
        "bloodGroup": lead_donor.get("bloodGroup"),
        "blood_group_norm": lead_donor.get("bloodGroup"),
        "area": lead_donor.get("city") or "Hyderabad",
        "city": lead_donor.get("city") or "Hyderabad",
        "eligibilityStatus": "eligible",
        "preferredLanguage": "en",
        "createdAt": db.now_iso(),
    }
    db.save_donor(donor)
    return donor


def record_broadcast_outreach(db, request: dict, demo_donor: dict) -> dict:
    """Mark demo donor as WhatsApp-contacted so voice escalation can fire."""
    request_id = request["requestId"]
    req = db.get_request(request_id) or request
    assigned = req.setdefault("assignedDonors", [])
    # One row per donor — drop stale confirmed/voice rows from prior demos.
    assigned[:] = [a for a in assigned if a.get("donorId") != demo_donor["donorId"]]
    assigned.append({
        "donorId": demo_donor["donorId"],
        "status": "outreach_sent",
        "outreachAt": db.now_iso(),
        "channel": "whatsapp",
    })
    req["currentDonorIndex"] = 0
    req["rankedDonorQueue"] = [demo_donor["donorId"]]
    db.save_request(req)
    return req


def prime_bridge_conversation(conv: dict, bridge: dict, lead_donor: dict,
                              request: dict, patient_name: str | None,
                              demo_donor: dict | None = None) -> dict:
    """Load ranked-donor context so we never re-ask name / group / city on bridge demo."""
    ctx = conv.setdefault("contextData", {})
    ctx.pop("medicalFlags", None)
    ctx["bridgeDonorName"] = lead_donor.get("name")
    ctx["bridgeDonorGroup"] = lead_donor.get("bloodGroup")
    ctx["bridgeDonorCity"] = lead_donor.get("city")
    ctx["bridgePatientName"] = patient_name or request.get("patientName")

    outreach_donor = demo_donor or lead_donor
    conv["bridgeOutreach"] = True
    conv["bridgeId"] = bridge.get("bridgeId")
    conv["bridgePatientId"] = bridge.get("patientId")
    conv["awaitingOutreachReply"] = True
    conv["state"] = "REGISTRATION_COMPLETE"
    conv["userType"] = "donor"
    conv["donorId"] = outreach_donor.get("donorId")
    conv["activeRequestId"] = request.get("requestId")
    # Fresh broadcast — clear stale voice flags so re-broadcast can escalate again.
    conv["voiceOutreachPlaced"] = False
    conv.pop("voiceOutreachRequestId", None)
    conv.pop("activeVoiceCallId", None)
    conv["escalationCallPlaced"] = False
    conv.pop("escalationCallResult", None)
    return conv
