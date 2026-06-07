"""Tests for legacy Bridge → Tara schema normalization."""
from shared.legacy_schema import is_legacy_bridge_donor, normalize_donor, normalize_patient
from conftest import DEMO_PHONE


def test_normalize_legacy_donor_coords_and_registration():
    raw = {
        "donorId": "abc",
        "SK": "PROFILE",
        "name": "Sai Nair",
        "bloodGroup": "O+",
        "city": "Secunderabad",
        "latitude": "17.3709",
        "longitude": "78.63919",
        "donationsTillDate": "1",
        "donorType": "One-Time Donor",
        "willingnessScore": "0.53",
        "eligibilityStatus": "eligible",
    }
    assert is_legacy_bridge_donor(raw)
    d = normalize_donor(raw)
    assert d["lat"] == 17.3709
    assert d["lng"] == 78.63919
    assert d["totalDonations"] == 1
    assert d["oneTimeDonor"] is True
    assert d["registrationStatus"] == "complete"
    assert d["consentGiven"] is True
    assert d["area"] == "Secunderabad"


def test_normalize_tara_donor_unchanged():
    raw = {
        "donorId": "x",
        "SK": "PROFILE",
        "phone": DEMO_PHONE,
        "registrationStatus": "complete",
        "consentGiven": True,
        "lat": 17.4,
        "lng": 78.5,
        "totalDonations": 2,
    }
    d = normalize_donor(raw)
    assert d["phone"] == DEMO_PHONE
    assert not is_legacy_bridge_donor(raw)


def test_normalize_bridge_patient():
    raw = {
        "patientId": "p1",
        "SK": "PROFILE",
        "name": "Akshara",
        "bridgeId": "BR-1",
        "bloodGroup": "O+",
        "latitude": "17.3",
        "longitude": "78.6",
    }
    p = normalize_patient(raw)
    assert p["registrationStatus"] == "complete"
    assert p["lat"] == 17.3
