#!/usr/bin/env python3
"""Seed RaktSetu demo data: 20 donors + 5 patients + 10 blood requests.

Works against the local in-memory store (LOCAL_MODE=1, default) or real
DynamoDB (LOCAL_MODE=0 with AWS creds + table env vars).

Usage:
    LOCAL_MODE=1 python scripts/seed_demo_data.py
    python scripts/seed_demo_data.py --reset
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LOCAL_MODE", "1")

from shared import dynamodb_client as db  # noqa: E402
from shared import eligibility_rules as rules  # noqa: E402

random.seed(42)

AREAS = [
    ("Banjara Hills", 17.4156, 78.4347), ("Madhapur", 17.4483, 78.3915),
    ("Secunderabad", 17.4399, 78.4983), ("Gachibowli", 17.4401, 78.3489),
    ("Kukatpally", 17.4849, 78.4138), ("Ameerpet", 17.4374, 78.4487),
    ("LB Nagar", 17.3473, 78.5520), ("Begumpet", 17.4441, 78.4677),
]
HOSPITALS = [
    ("Apollo Hospital Jubilee Hills", 17.4239, 78.4099),
    ("Rainbow Children's Hospital", 17.4156, 78.4347),
    ("NIMS Hyderabad", 17.4239, 78.4525),
    ("Yashoda Hospital Somajiguda", 17.4256, 78.4566),
    ("KIMS Hospital Secunderabad", 17.4399, 78.4983),
]
BLOOD_GROUPS = ["O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"]
NAMES = ["Rahul Kumar", "Priya Sharma", "Arjun Reddy", "Sneha Rao", "Vikram Singh",
         "Anjali Nair", "Karthik Iyer", "Divya Menon", "Rohan Gupta", "Meera Pillai",
         "Suresh Babu", "Lakshmi Devi", "Imran Khan", "Fatima Begum", "Aakash Verma",
         "Pooja Patel", "Sandeep Yadav", "Kavya Krishnan", "Manoj Tiwari", "Nisha Joshi"]
LANGS = ["en", "hi", "te"]


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def seed_donors(n=20):
    donors = []
    for i in range(n):
        area, lat, lng = random.choice(AREAS)
        total = random.choice([0, 0, 1, 1, 2, 3, 5])
        # Mix of eligible / cooldown.
        last = None if total == 0 else _iso_days_ago(random.choice([20, 45, 95, 120, 200]))
        donor = {
            "donorId": db.new_id(),
            "phone": f"+9190000{10000 + i}",
            "name": NAMES[i % len(NAMES)],
            "age": random.randint(19, 55),
            "weight": random.randint(50, 90),
            "bloodGroup": random.choice(BLOOD_GROUPS),
            "area": area, "city": "Hyderabad", "lat": lat, "lng": lng,
            "registrationStatus": "complete",
            "registrationChannel": random.choice(["whatsapp", "voice"]),
            "lastDonationDate": last,
            "totalDonations": total,
            "oneTimeDonor": total == 1,
            "medicalFlags": {},
            "consentGiven": True,
            "preferredLanguage": random.choice(LANGS),
            "preferredChannel": "whatsapp",
            "outreachCount": random.randint(0, 6),
        }
        donor["acceptedCount"] = random.randint(0, donor["outreachCount"])
        status = rules.derive_eligibility_status(donor)
        donor["eligibilityStatus"] = status["status"]
        donor["cooldownEndsAt"] = status["cooldownEndsAt"]
        db.save_donor(donor)
        donors.append(donor)
    return donors


def seed_patients(n=5):
    patients = []
    for i in range(n):
        hosp, lat, lng = random.choice(HOSPITALS)
        p = {
            "patientId": db.new_id(),
            "phone": f"+9190001{20000 + i}",
            "name": f"Patient {NAMES[i].split()[0]}",
            "age": random.randint(2, 18),
            "guardianName": NAMES[(i + 5) % len(NAMES)],
            "guardianPhone": f"+9190002{30000 + i}",
            "bloodGroup": random.choice(BLOOD_GROUPS),
            "hospital": hosp, "city": "Hyderabad",
            "diagnosisType": "thalassemia_major",
            "lastTransfusionDate": _iso_days_ago(random.randint(5, 25)),
            "averageCadenceDays": random.choice([21, 28, 30]),
            "registrationStatus": "complete",
            "preferredLanguage": random.choice(LANGS),
            "preferredChannel": "whatsapp",
        }
        cadence = p["averageCadenceDays"]
        last = datetime.strptime(p["lastTransfusionDate"], "%Y-%m-%d")
        p["nextTransfusionDue"] = (last + timedelta(days=cadence)).strftime("%Y-%m-%d")
        db.save_patient(p)
        patients.append(p)
    return patients


def seed_requests(patients, n=10):
    requests = []
    for i in range(n):
        p = random.choice(patients)
        hosp, lat, lng = random.choice(HOSPITALS)
        req = {
            "requestId": db.new_id(),
            "patientId": p["patientId"],
            "patientName": p["name"],
            "patientPhone": p["phone"],
            "bloodGroup": p["bloodGroup"],
            "unitsNeeded": random.randint(1, 3),
            "hospital": hosp, "hospitalLat": lat, "hospitalLng": lng,
            "city": "Hyderabad",
            "requiredBy": (datetime.now(timezone.utc) +
                           timedelta(days=random.randint(0, 5))).strftime("%Y-%m-%d"),
            "urgencyLevel": random.choice(["normal", "normal", "urgent"]),
            "status": random.choice(["open", "open", "outreach_started", "fulfilled"]),
            "assignedDonors": [],
            "createdAt": db.now_iso(),
            "createdBy": "admin",
        }
        db.save_request(req)
        requests.append(req)
    return requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true",
                        help="Delete the local_state.json before seeding (LOCAL_MODE only).")
    args = parser.parse_args()
    if args.reset and os.environ.get("LOCAL_MODE") == "1":
        path = os.environ.get("LOCAL_STATE_PATH", "local_state.json")
        if os.path.exists(path):
            os.remove(path)
            db._local_store = None

    donors = seed_donors(20)
    patients = seed_patients(5)
    requests = seed_requests(patients, 10)
    print(f"Seeded {len(donors)} donors, {len(patients)} patients, {len(requests)} requests.")
    eligible = [d for d in donors if d["eligibilityStatus"] == "eligible"]
    one_time = [d for d in donors if d["oneTimeDonor"]]
    print(f"  eligible donors: {len(eligible)} | one-time donors: {len(one_time)}")
    print(f"  open requests:   {len([r for r in requests if r['status'] in ('open', 'outreach_started')])}")


if __name__ == "__main__":
    main()
