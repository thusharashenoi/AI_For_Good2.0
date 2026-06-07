"""Donor scoring / ranking for a blood request.

score = w_compat * compatibility
      + w_prox   * proximity
      + w_reliab * reliability
      + w_recency* recency
      + w_will   * willingness

All sub-scores are normalised to [0, 1]. Weights are tunable and documented so
the ranking is explainable (a key "AI Component" talking point).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from shared import eligibility_rules as rules
from shared import geocoding

WEIGHTS = {
    "compatibility": 0.30,
    "proximity": 0.25,
    "reliability": 0.20,
    "recency": 0.10,
    "willingness": 0.15,
}

# Exact-match donor groups are preferred over universal donors so we conserve
# rare universal blood (O-) for emergencies.
_EXACT_BONUS = 1.0
_UNIVERSAL_PENALTY = 0.7


def _compatibility_score(donor: Dict, request: Dict) -> float:
    dg = rules.normalize_blood_group(donor.get("bloodGroup"))
    rg = rules.normalize_blood_group(request.get("bloodGroup"))
    if not dg or not rg or not rules.is_compatible(dg, rg):
        return 0.0
    if dg == rg:
        return _EXACT_BONUS
    if dg == "O-":  # universal donor, conserve
        return _UNIVERSAL_PENALTY
    return 0.85


def _proximity_score(donor: Dict, request: Dict) -> float:
    d = _donor_coords(donor)
    r = _request_coords(request)
    if not d or not r:
        return 0.5  # unknown distance -> neutral
    km = geocoding.haversine_km(d, r)
    # 0 km -> 1.0, decays to ~0 at 40km.
    return max(0.0, 1.0 - km / 40.0)


def _reliability_score(donor: Dict) -> float:
    """Historical response/turn-up reliability, defaults to a mild prior."""
    if "reliabilityScore" in donor and donor["reliabilityScore"] is not None:
        return _clamp(float(donor["reliabilityScore"]))
    asked = donor.get("outreachCount") or 0
    accepted = donor.get("acceptedCount") or 0
    if asked == 0:
        return 0.6  # benefit of the doubt for new donors
    return _clamp(accepted / asked)


def _recency_score(donor: Dict, now: Optional[datetime] = None) -> float:
    """Reward donors who are well past cooldown (more rested), penalise none."""
    last = donor.get("lastDonationDate")
    if not last:
        return 1.0  # never donated -> fully rested / first-timer
    days = max(0, rules.COOLDOWN_DAYS - rules.days_until_eligible(last, now=now))
    return _clamp(days / rules.COOLDOWN_DAYS)


def _willingness_score(donor: Dict) -> float:
    if donor.get("_willingnessScore") is not None:
        return _clamp(float(donor["_willingnessScore"]))
    if donor.get("willingnessScore") is not None:
        try:
            return _clamp(float(donor["willingnessScore"]))
        except (TypeError, ValueError):
            pass
    if donor.get("oneTimeDonor"):
        return 0.8  # re-engagement target — high value
    base = 0.5 + 0.05 * (donor.get("totalDonations") or 0)
    return _clamp(base)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _donor_coords(donor: Dict) -> Optional[Tuple[float, float]]:
    if donor.get("lat") is not None and donor.get("lng") is not None:
        return float(donor["lat"]), float(donor["lng"])
    if donor.get("latitude") is not None and donor.get("longitude") is not None:
        return float(donor["latitude"]), float(donor["longitude"])
    return None


def _request_coords(request: Dict) -> Optional[Tuple[float, float]]:
    if request.get("hospitalLat") is not None and request.get("hospitalLng") is not None:
        return float(request["hospitalLat"]), float(request["hospitalLng"])
    return None


def score_donor(donor: Dict, request: Dict, now: Optional[datetime] = None) -> Dict:
    components = {
        "compatibility": _compatibility_score(donor, request),
        "proximity": _proximity_score(donor, request),
        "reliability": _reliability_score(donor),
        "recency": _recency_score(donor, now=now),
        "willingness": _willingness_score(donor),
    }
    total = sum(WEIGHTS[k] * v for k, v in components.items())
    return {
        "donorId": donor.get("donorId"),
        "name": donor.get("name"),
        "phone": donor.get("phone"),
        "bloodGroup": donor.get("bloodGroup"),
        "area": donor.get("area"),
        "preferredLanguage": donor.get("preferredLanguage", "en"),
        "score": round(total, 4),
        "components": {k: round(v, 3) for k, v in components.items()},
        "oneTimeDonor": bool(donor.get("oneTimeDonor")),
    }


def rank_donors(donors: List[Dict], request: Dict,
                now: Optional[datetime] = None) -> List[Dict]:
    scored = [score_donor(d, request, now=now) for d in donors]
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored
