"""Map legacy Blood Warriors Bridge DynamoDB rows to Tara field names on read."""
from __future__ import annotations

from typing import Dict, Optional


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_legacy_bridge_donor(donor: dict) -> bool:
    if not donor:
        return False
    if donor.get("registrationChannel") == "bridge":
        return True
    return bool(
        donor.get("willingnessScore") is not None
        or donor.get("donationsTillDate") is not None
        or (donor.get("latitude") is not None and donor.get("lat") is None)
    )


def normalize_donor(donor: Optional[dict]) -> Optional[dict]:
    if not donor:
        return donor
    d = dict(donor)

    lat = _as_float(d.get("lat"))
    if lat is None and d.get("latitude") is not None:
        lat = _as_float(d["latitude"])
        if lat is not None:
            d["lat"] = lat

    lng = _as_float(d.get("lng"))
    if lng is None and d.get("longitude") is not None:
        lng = _as_float(d["longitude"])
        if lng is not None:
            d["lng"] = lng

    if d.get("totalDonations") is None and d.get("donationsTillDate") is not None:
        d["totalDonations"] = _as_int(d["donationsTillDate"])

    if d.get("oneTimeDonor") is None and d.get("donorType"):
        d["oneTimeDonor"] = "one-time" in str(d["donorType"]).lower()

    if d.get("willingnessScore") is not None and d.get("willingnessScore") not in (None, ""):
        try:
            d.setdefault("_willingnessScore", float(d["willingnessScore"]))
        except (TypeError, ValueError):
            pass

    if is_legacy_bridge_donor(d):
        d.setdefault("registrationStatus", "complete")
        d.setdefault("consentGiven", True)
        d.setdefault("registrationChannel", "bridge")
        d.setdefault("preferredLanguage", "en")
        d.setdefault("preferredChannel", "whatsapp")
        d.setdefault("medicalFlags", d.get("medicalFlags") or {})
        d.setdefault("area", d.get("area") or d.get("city"))
        if d.get("nextEligibleDate") and not d.get("cooldownEndsAt"):
            d["cooldownEndsAt"] = d["nextEligibleDate"]

    return d


def normalize_patient(patient: Optional[dict]) -> Optional[dict]:
    if not patient:
        return patient
    p = dict(patient)
    if p.get("bridgeId") and not p.get("diagnosisType"):
        p.setdefault("diagnosisType", "thalassemia_major")
    if p.get("registrationStatus") is None:
        p.setdefault("registrationStatus", "complete")
        p.setdefault("registrationChannel", "bridge")
        p.setdefault("preferredLanguage", "en")
        p.setdefault("preferredChannel", "whatsapp")
    lat = _as_float(p.get("lat"))
    if lat is None and p.get("latitude") is not None:
        lat = _as_float(p["latitude"])
        if lat is not None:
            p["lat"] = lat
    lng = _as_float(p.get("lng"))
    if lng is None and p.get("longitude") is not None:
        lng = _as_float(p["longitude"])
        if lng is not None:
            p["lng"] = lng
    return p


def normalize_request(req: Optional[dict]) -> Optional[dict]:
    if not req:
        return req
    r = dict(req)
    r.setdefault("assignedDonors", r.get("assignedDonors") or [])
    r.setdefault("rankedDonorQueue", r.get("rankedDonorQueue") or [])
    return r
