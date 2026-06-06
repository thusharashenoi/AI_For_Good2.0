"""Dual-write ML/intelligence fields when automation saves donors or patients.

Keeps the admin Blood Graph and matching engine in sync with WhatsApp/voice
registrations without requiring a separate seed job.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("raktsetu.intelligence_bridge")


def _default_willingness(donor: dict) -> float:
    score = 0.62
    donations = int(donor.get("totalDonations") or 0)
    if donations >= 5:
        score += 0.12
    elif donations >= 2:
        score += 0.06
    if donor.get("oneTimeDonor"):
        score -= 0.05
    if str(donor.get("registrationChannel", "")).lower() in ("whatsapp", "voice"):
        score += 0.08
    if str(donor.get("eligibilityStatus", "")).lower() == "eligible":
        score += 0.05
    return min(0.95, max(0.35, score))


def enrich_donor(donor: dict) -> dict:
    """Add fields the intelligence layer reads alongside automation-native ones."""
    if donor.get("lat") is not None and donor.get("latitude") is None:
        donor["latitude"] = donor["lat"]
    if donor.get("lng") is not None and donor.get("longitude") is None:
        donor["longitude"] = donor["lng"]
    if donor.get("cooldownEndsAt") and not donor.get("nextEligibleDate"):
        donor["nextEligibleDate"] = donor["cooldownEndsAt"]
    if donor.get("totalDonations") is not None and donor.get("donationsTillDate") is None:
        donor["donationsTillDate"] = donor["totalDonations"]
    donor.setdefault("donorType", "One-Time Donor" if donor.get("oneTimeDonor") else "Regular Donor")
    donor.setdefault("role", "Emergency Donor" if donor.get("registrationChannel") == "whatsapp" else "Volunteer")
    donor.setdefault("showRate", 0.8)
    donor.setdefault("reliabilityScore", 0.5)
    donor.setdefault("noShows", 0)
    donor.setdefault("willingnessScore", _default_willingness(donor))
    return donor


def enrich_patient(patient: dict, *, lat: Any = None, lng: Any = None) -> dict:
    patient.setdefault("bridgeBloodGroup", patient.get("bloodGroup"))
    if lat is not None and patient.get("latitude") is None:
        patient["latitude"] = lat
        patient["lat"] = lat
    if lng is not None and patient.get("longitude") is None:
        patient["longitude"] = lng
        patient["lng"] = lng
    return patient
