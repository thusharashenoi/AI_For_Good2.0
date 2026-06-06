"""Donor eligibility filter for a blood request.

Wraps shared.eligibility_rules to decide whether a donor can be *contacted* for
a specific request right now: complete registration, consent given, compatible
blood group, and not in cooldown / deferral.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from shared import eligibility_rules as rules


def is_eligible_for_request(donor: Dict, request: Dict,
                            now: datetime | None = None) -> Dict:
    """Return {eligible: bool, reasons: [...], status: ...} for a donor/request."""
    now = now or datetime.now(timezone.utc)
    reasons: List[str] = []

    if donor.get("registrationStatus") != "complete":
        reasons.append("registration_incomplete")
    if not donor.get("consentGiven"):
        reasons.append("no_consent")

    donor_bg = donor.get("bloodGroup")
    need_bg = request.get("bloodGroup")
    if not donor_bg or not rules.is_compatible(donor_bg, need_bg):
        reasons.append("blood_group_incompatible")

    status = rules.derive_eligibility_status(donor, now=now)
    if status["status"] != "eligible":
        reasons.append(status["status"])  # "cooldown" | "deferred"

    return {
        "eligible": len(reasons) == 0,
        "reasons": reasons,
        "status": status["status"],
        "cooldownEndsAt": status.get("cooldownEndsAt"),
    }


def filter_eligible(donors: List[Dict], request: Dict,
                    now: datetime | None = None) -> List[Dict]:
    return [d for d in donors if is_eligible_for_request(d, request, now=now)["eligible"]]
