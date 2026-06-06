"""Map WhatsApp/automation DynamoDB items into the intelligence pipeline shape.

Automation writes donors/patients with ``lat``/``lng``, ``cooldownEndsAt``, etc.
The Blood Graph expects ``latitude``/``longitude``, ``next_eligible_date``, ML
scores, and roles. This module normalises both schemas on read so the admin UI
and matching engine see newly registered portal users immediately.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import synth


def live_reference_date() -> pd.Timestamp:
    """Use real time for engagement-sourced rows (demo + production bot flow)."""
    if os.environ.get("LIVE_ELIGIBILITY", "1") == "1":
        return pd.Timestamp.now(tz=timezone.utc).tz_localize(None)
    from . import config
    return pd.Timestamp(config.REFERENCE_DATE)


def _is_engagement_item(item: dict) -> bool:
    return bool(item.get("registrationChannel") or item.get("registrationStatus"))


def default_willingness(item: dict) -> float:
    """Lightweight score when ML model was not run at registration time."""
    score = 0.62
    donations = int(item.get("totalDonations") or item.get("donationsTillDate") or 0)
    if donations >= 5:
        score += 0.12
    elif donations >= 2:
        score += 0.06
    if item.get("oneTimeDonor"):
        score -= 0.05
    if str(item.get("registrationChannel", "")).lower() in ("whatsapp", "voice"):
        score += 0.08
    if str(item.get("eligibilityStatus", "")).lower() == "eligible":
        score += 0.05
    return float(np.clip(score, 0.35, 0.95))


def donor_row(item: dict, ref: pd.Timestamp) -> dict | None:
    """Convert one donor PROFILE item to a pipeline row, or None if skip."""
    status = str(item.get("registrationStatus") or "complete").lower()
    if status not in ("complete", ""):
        return None

    lat = item.get("latitude", item.get("lat"))
    lng = item.get("longitude", item.get("lng"))
    if lat is None and item.get("area"):
        try:
            lat, lng = synth.coords_for_city(str(item.get("city") or "Hyderabad"))
        except Exception:
            lat, lng = np.nan, np.nan

    last_donation = item.get("lastDonationDate") or item.get("last_donation_date")
    next_eligible = item.get("nextEligibleDate") or item.get("cooldownEndsAt")
    elig = item.get("eligibilityStatus") or item.get("eligibility_status")
    if not elig and next_eligible:
        try:
            ned = pd.Timestamp(next_eligible)
            elig = "eligible" if ned <= ref else "not eligible"
        except Exception:
            elig = "eligible"

    donations = item.get("donationsTillDate", item.get("totalDonations", 0))
    one_time = item.get("oneTimeDonor")
    donor_type = item.get("donorType")
    if donor_type is None:
        donor_type = "One-Time Donor" if one_time else "Regular Donor"

    role = item.get("role") or "Volunteer"
    if str(item.get("registrationChannel", "")).lower() == "whatsapp":
        role = item.get("role") or "Emergency Donor"

    return {
        "user_id": item.get("donorId"),
        "name": item.get("name"),
        "phone": item.get("phone"),
        "blood_group_norm": item.get("bloodGroup") or item.get("blood_group_norm"),
        "role": role,
        "donor_type": donor_type,
        "latitude": _num(lat),
        "longitude": _num(lng),
        "eligibility_status": elig,
        "next_eligible_date": _date(next_eligible),
        "last_donation_date": _date(last_donation),
        "last_contacted_date": _date(item.get("lastContactedDate")),
        "days_since_last_contact": np.nan,
        "frequency_in_days": _num(item.get("frequencyInDays"), 0.0),
        "donations_till_date": _num(donations, 0.0),
        "no_shows": _num(item.get("noShows"), 0.0),
        "show_rate": _num(item.get("showRate"), 0.8),
        "reliability_score": _num(item.get("reliabilityScore"), 0.5),
        "willingness": _num(item.get("willingnessScore"), default_willingness(item)),
        "user_donation_active_status": item.get("userDonationActiveStatus"),
        "city": item.get("city") or item.get("area"),
        "bridge_id": item.get("currentBridgeId") or item.get("bridgeId"),
        "registration_channel": item.get("registrationChannel"),
        "_live_eligibility": _is_engagement_item(item),
    }


def patient_row(item: dict, request_coords: dict[str, tuple[float, float]]) -> dict:
    pid = item.get("patientId")
    lat = item.get("latitude", item.get("lat"))
    lng = item.get("longitude", item.get("lng"))
    if (lat is None or lng is None) and pid in request_coords:
        lat, lng = request_coords[pid]
    if lat is None or lng is None:
        city = item.get("city") or "Hyderabad"
        try:
            lat, lng = synth.coords_for_city(str(city))
        except Exception:
            lat, lng = np.nan, np.nan

    bg = item.get("bloodGroup") or item.get("blood_group_norm")
    return {
        "user_id": pid,
        "name": item.get("name"),
        "phone": item.get("phone"),
        "blood_group_norm": bg,
        "bridge_blood_group_norm": item.get("bridgeBloodGroup") or bg,
        "latitude": _num(lat),
        "longitude": _num(lng),
        "frequency_in_days": _num(item.get("frequencyInDays"), np.nan),
        "last_transfusion_date": _date(item.get("lastTransfusionDate")),
        "expected_next_transfusion_date": _date(item.get("expectedNextTransfusionDate")
                                                or item.get("nextTransfusionDue")),
        "quantity_required": _num(item.get("quantityRequired"), 1.0),
        "city": item.get("city") or "Hyderabad",
        "bridge_id": item.get("bridgeId"),
        "hospital": item.get("hospital"),
        "registration_channel": item.get("registrationChannel"),
    }


def request_coords_by_patient(request_items: list[dict]) -> dict[str, tuple[float, float]]:
    """Latest blood-request hospital coordinates per patient."""
    out: dict[str, tuple[float, float]] = {}
    for it in sorted(request_items, key=lambda x: str(x.get("createdAt") or "")):
        if it.get("SK") != "REQUEST":
            continue
        pid = it.get("patientId")
        lat, lng = it.get("hospitalLat"), it.get("hospitalLng")
        if pid and lat is not None and lng is not None:
            out[str(pid)] = (float(lat), float(lng))
    return out


def _num(v, default=np.nan) -> float:
    if v is None:
        return default
    try:
        f = float(v)
        return default if np.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _date(v):
    return pd.to_datetime(v, errors="coerce") if v not in (None, "") else pd.NaT
