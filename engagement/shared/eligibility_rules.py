"""Blood-donation domain rules for RaktSetu.

Encodes:
- ABO/Rh compatibility matrix (donor -> recipient)
- whole-blood 90-day cooldown
- age (18-65) and weight (>=45kg) thresholds
- medical deferral periods (FLOW 5)

These are pure functions with no AWS dependency so they're trivially unit
testable (tests/test_eligibility.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

# Whole-blood donation interval (India / NBTC guideline: 90 days).
COOLDOWN_DAYS = 90
MIN_AGE = 18
MAX_AGE = 65
MIN_WEIGHT_KG = 45

ALL_BLOOD_GROUPS = ["O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"]

# donor blood group -> set of recipient groups it can give whole blood / red cells to.
# (Standard RBC compatibility.)
_CAN_DONATE_TO: Dict[str, List[str]] = {
    "O-": ["O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"],
    "O+": ["O+", "A+", "B+", "AB+"],
    "A-": ["A-", "A+", "AB-", "AB+"],
    "A+": ["A+", "AB+"],
    "B-": ["B-", "B+", "AB-", "AB+"],
    "B+": ["B+", "AB+"],
    "AB-": ["AB-", "AB+"],
    "AB+": ["AB+"],
}


def normalize_blood_group(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value.strip().upper().replace(" ", "")
    v = v.replace("POSITIVE", "+").replace("POS", "+")
    v = v.replace("NEGATIVE", "-").replace("NEG", "-")
    return v if v in ALL_BLOOD_GROUPS else None


def is_compatible(donor_group: str, recipient_group: str) -> bool:
    """True if a donor of donor_group can give red cells to recipient_group."""
    dg = normalize_blood_group(donor_group)
    rg = normalize_blood_group(recipient_group)
    if not dg or not rg:
        return False
    return rg in _CAN_DONATE_TO.get(dg, [])


def compatible_donor_groups(recipient_group: str) -> List[str]:
    """All donor blood groups that can give to this recipient."""
    rg = normalize_blood_group(recipient_group)
    if not rg:
        return []
    return [dg for dg in ALL_BLOOD_GROUPS if rg in _CAN_DONATE_TO[dg]]


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
def _parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d %B %Y", "%B %Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def cooldown_end(last_donation_date) -> Optional[str]:
    """Return ISO timestamp 90 days after the last donation, or None."""
    dt = _parse_dt(last_donation_date)
    if dt is None:
        return None
    return (dt + timedelta(days=COOLDOWN_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")


def in_cooldown(last_donation_date, now: Optional[datetime] = None) -> bool:
    dt = _parse_dt(last_donation_date)
    if dt is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - dt).days < COOLDOWN_DAYS


def days_until_eligible(last_donation_date, now: Optional[datetime] = None) -> int:
    dt = _parse_dt(last_donation_date)
    if dt is None:
        return 0
    now = now or datetime.now(timezone.utc)
    remaining = COOLDOWN_DAYS - (now - dt).days
    return max(remaining, 0)


# ---------------------------------------------------------------------------
# Basic eligibility (registration gates)
# ---------------------------------------------------------------------------
def check_age(age) -> Tuple[bool, Optional[str]]:
    try:
        a = int(age)
    except (TypeError, ValueError):
        return False, "invalid"
    if a < MIN_AGE:
        return False, "underage"
    if a > MAX_AGE:
        return False, "overage"
    return True, None


def check_weight(weight) -> Tuple[bool, Optional[str]]:
    try:
        w = float(weight)
    except (TypeError, ValueError):
        return False, "invalid"
    if w < MIN_WEIGHT_KG:
        return False, "underweight"
    return True, None


# ---------------------------------------------------------------------------
# Medical deferral rules (FLOW 5)
# ---------------------------------------------------------------------------
# flag -> (deferral_days, permanent, human-reason-key)
DEFERRAL_RULES: Dict[str, Dict] = {
    "recentTattoo": {"days": 182, "permanent": False,
                     "reason": "A tattoo or piercing requires a 6-month wait before donating."},
    "recentFever": {"days": 14, "permanent": False,
                    "reason": "Recent fever or antibiotics requires a 2-week wait."},
    "recentAntibiotics": {"days": 14, "permanent": False,
                          "reason": "Recent antibiotics require a 2-week wait."},
    "pregnant": {"days": 270, "permanent": False,
                 "reason": "Pregnancy/breastfeeding requires a wait of about 6 months post-delivery."},
    "malariaTravel": {"days": 90, "permanent": False,
                      "reason": "Travel to a malaria-affected area requires a 3-month wait."},
    "recentVaccination": {"days": 14, "permanent": False,
                          "reason": "A recent vaccination requires a short 2-week wait."},
    "diabetes": {"days": 0, "permanent": True,
                 "reason": "Insulin-dependent diabetes is a permanent deferral per donation guidelines."},
}


def evaluate_deferral(medical_flags: Dict[str, bool],
                      now: Optional[datetime] = None) -> Dict:
    """Given a map of medical flags, return the most restrictive deferral.

    Returns dict: {eligible, permanent, deferral_days, eligible_date, reason}.
    """
    now = now or datetime.now(timezone.utc)
    worst: Optional[Dict] = None
    for flag, is_set in (medical_flags or {}).items():
        if not is_set:
            continue
        rule = DEFERRAL_RULES.get(flag)
        if not rule:
            continue
        if rule["permanent"]:
            return {
                "eligible": False,
                "permanent": True,
                "deferral_days": None,
                "eligible_date": None,
                "reason": rule["reason"],
            }
        if worst is None or rule["days"] > worst["days"]:
            worst = rule

    if worst is None:
        return {"eligible": True, "permanent": False, "deferral_days": 0,
                "eligible_date": None, "reason": None}

    eligible_date = (now + timedelta(days=worst["days"])).strftime("%Y-%m-%d")
    return {
        "eligible": False,
        "permanent": False,
        "deferral_days": worst["days"],
        "eligible_date": eligible_date,
        "reason": worst["reason"],
    }


def derive_eligibility_status(donor: Dict, now: Optional[datetime] = None) -> Dict:
    """Compute a donor's current eligibility status for matching.

    Returns {status: eligible|cooldown|deferred, cooldownEndsAt, reason}.
    """
    flags = donor.get("medicalFlags") or {}
    deferral = evaluate_deferral(flags, now=now)
    if not deferral["eligible"]:
        return {
            "status": "deferred",
            "cooldownEndsAt": None if deferral["permanent"] else deferral["eligible_date"],
            "reason": deferral["reason"],
        }
    last = donor.get("lastDonationDate")
    if in_cooldown(last, now=now):
        return {
            "status": "cooldown",
            "cooldownEndsAt": cooldown_end(last),
            "reason": f"In 90-day cooldown after last donation on {last}.",
        }
    return {"status": "eligible", "cooldownEndsAt": None, "reason": None}


# Medical quick-check items (FLOW 5 / voice outreach). Single source of truth.
ELIGIBILITY_CHECKS: List[Dict] = [
    {
        "key": "diabetes_insulin",
        "flag": "diabetes",
        "i18n_key": "ASK_DIABETES",
        "spoken": "diabetes that requires insulin",
    },
    {
        "key": "tattoo_6mo",
        "flag": "recentTattoo",
        "i18n_key": "ASK_RECENT_TATTOO",
        "spoken": "a tattoo or piercing in the last six months",
    },
    {
        "key": "fever_or_antibiotics_2wk",
        "flag": "recentFever",
        "i18n_key": "ASK_RECENT_FEVER",
        "spoken": "fever or antibiotics in the last two weeks",
    },
    {
        "key": "pregnant_or_breastfeeding",
        "flag": "pregnant",
        "i18n_key": "ASK_PREGNANT",
        "spoken": "pregnancy or breastfeeding",
    },
    {
        "key": "malaria_travel_3mo",
        "flag": "malariaTravel",
        "i18n_key": "ASK_MALARIA_TRAVEL",
        "spoken": "travel to a malaria area in the last three months",
    },
]

_PARAM_TO_FLAG = {c["key"]: c["flag"] for c in ELIGIBILITY_CHECKS}


def eligibility_checklist(answers: Optional[Dict[str, bool]] = None,
                          lang: str = "en") -> Dict:
    """Progress through the medical quick-check — used by voice and WhatsApp agents."""
    from . import i18n

    answers = answers or {}
    checks = []
    missing = []
    for item in ELIGIBILITY_CHECKS:
        key = item["key"]
        answered = key in answers
        if not answered:
            missing.append(key)
        checks.append({
            "key": key,
            "question": i18n.t(item["i18n_key"], lang),
            "spokenTopic": item["spoken"],
            "answered": answered,
            "answerYes": answers[key] if answered else None,
        })
    return {
        "checks": checks,
        "missingAnswers": missing,
        "readyToEvaluate": len(missing) == 0,
        "answeredCount": len(answers),
        "totalCount": len(ELIGIBILITY_CHECKS),
    }


def answers_to_medical_flags(answers: Dict[str, bool]) -> Dict[str, bool]:
    flags: Dict[str, bool] = {}
    for key, val in answers.items():
        flag = _PARAM_TO_FLAG.get(key)
        if flag is not None:
            flags[flag] = bool(val)
    return flags
