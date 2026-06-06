"""Expected entity fields — used by tests to catch incomplete DB writes."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set


def _missing_keys(item: dict, required: Iterable[str]) -> List[str]:
    return sorted(k for k in required if k not in item)


def _assert_keys(item: dict, required: Iterable[str], label: str) -> None:
    missing = _missing_keys(item, required)
    if missing:
        raise AssertionError(f"{label} missing fields: {', '.join(missing)}")


# Keys that must exist (value may be None).
DONOR_COMPLETE_KEYS = {
    "donorId", "SK", "phone", "name", "age", "weight", "bloodGroup",
    "area", "city", "lat", "lng", "lastDonationDate",
    "registrationStatus", "registrationChannel", "consentGiven",
    "preferredLanguage", "preferredChannel", "totalDonations",
    "medicalFlags", "oneTimeDonor", "eligibilityStatus", "cooldownEndsAt",
    "createdAt", "updatedAt",
}

DONOR_PARTIAL_KEYS = {
    "donorId", "SK", "phone", "name", "registrationStatus",
    "registrationChannel", "preferredLanguage", "preferredChannel",
    "createdAt", "updatedAt",
}

PATIENT_KEYS = {
    "patientId", "SK", "phone", "name", "bloodGroup", "hospital", "city",
    "diagnosisType", "registrationStatus", "registrationChannel",
    "preferredLanguage", "preferredChannel", "createdAt", "updatedAt",
}

REQUEST_KEYS = {
    "requestId", "SK", "patientId", "patientName", "patientPhone",
    "bloodGroup", "unitsNeeded", "hospital", "hospitalLat", "hospitalLng",
    "city", "requiredBy", "urgencyLevel", "status", "assignedDonors",
    "createdAt", "createdBy",
}

APPOINTMENT_KEYS = {
    "appointmentId", "SK", "donorId", "patientId", "requestId",
    "donorPhone", "patientPhone", "donorName", "patientName",
    "hospital", "city", "appointmentDate", "appointmentTime", "bloodGroup",
    "status", "donorConfirmedDayBefore", "channel", "createdAt",
}

WAITLIST_KEYS = {"phone", "name", "reason", "createdAt"}

CONVERSATION_KEYS = {
    "phone_number", "conversationId", "channel", "state", "language",
    "userType", "contextData", "lastMessageAt",
}


def assert_donor_complete(donor: dict) -> None:
    _assert_keys(donor, DONOR_COMPLETE_KEYS, "donor (complete)")
    assert donor["registrationStatus"] == "complete"
    assert donor["consentGiven"] is True
    assert donor["SK"] == "PROFILE"


def assert_donor_partial(donor: dict) -> None:
    _assert_keys(donor, DONOR_PARTIAL_KEYS, "donor (partial)")
    assert donor["registrationStatus"] == "partial"


def assert_patient(patient: dict) -> None:
    _assert_keys(patient, PATIENT_KEYS, "patient")
    assert patient["registrationStatus"] == "complete"
    assert patient["SK"] == "PROFILE"


def assert_request(req: dict) -> None:
    _assert_keys(req, REQUEST_KEYS, "request")
    assert req["SK"] == "REQUEST"


def assert_appointment(appt: dict) -> None:
    _assert_keys(appt, APPOINTMENT_KEYS, "appointment")
    assert appt["SK"] == "APPT"


def assert_waitlist(entry: dict, *, reason: Optional[str] = None) -> None:
    _assert_keys(entry, WAITLIST_KEYS, "waitlist")
    if reason:
        assert entry["reason"] == reason


def assert_conversation(conv: dict) -> None:
    _assert_keys(conv, CONVERSATION_KEYS, "conversation")
