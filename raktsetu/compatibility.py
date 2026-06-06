"""ABO/Rh blood-group compatibility for whole-blood / red-cell donation.

Direction matters: we model DONOR -> RECIPIENT (patient) compatibility, which is
the relevant direction for transfusion. O- is the universal donor, AB+ the
universal recipient.
"""
from __future__ import annotations

from typing import Optional

# Canonical 8 groups.
GROUPS = ["O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"]

# Map the dataset's verbose labels to canonical short codes.
_LABEL_MAP = {
    "o positive": "O+", "o negative": "O-",
    "a positive": "A+", "a negative": "A-",
    "b positive": "B+", "b negative": "B-",
    "ab positive": "AB+", "ab negative": "AB-",
    # A1 / A1B subtypes behave as A / AB for ABO transfusion purposes.
    "a1 positive": "A+", "a1 negative": "A-",
    "a1b positive": "AB+", "a1b negative": "AB-",
}

# Recipient group -> set of donor groups that can give to them (red cells).
_CAN_RECEIVE_FROM = {
    "O-": {"O-"},
    "O+": {"O-", "O+"},
    "A-": {"O-", "A-"},
    "A+": {"O-", "O+", "A-", "A+"},
    "B-": {"O-", "B-"},
    "B+": {"O-", "O+", "B-", "B+"},
    "AB-": {"O-", "A-", "B-", "AB-"},
    "AB+": set(GROUPS),  # universal recipient
}


def normalize_group(raw: Optional[str]) -> Optional[str]:
    """Normalize a raw blood-group label to a canonical code, or None if unknown."""
    if raw is None:
        return None
    s = str(raw).strip()
    # Idempotent: already-canonical codes (e.g. "O+", "AB-") pass straight through.
    if s.upper() in GROUPS:
        return s.upper()
    key = s.lower()
    if not key or key in {"do not know", "dont know", "unknown", "nan", "none"}:
        return None
    return _LABEL_MAP.get(key)


def is_compatible(donor_group: Optional[str], recipient_group: Optional[str]) -> bool:
    """True if a donor of donor_group can donate red cells to recipient_group."""
    d = normalize_group(donor_group)
    r = normalize_group(recipient_group)
    if d is None or r is None:
        return False
    return d in _CAN_RECEIVE_FROM[r]


def compatible_donor_groups(recipient_group: Optional[str]) -> set[str]:
    """Set of donor groups that can give to this recipient."""
    r = normalize_group(recipient_group)
    return set(_CAN_RECEIVE_FROM.get(r, set())) if r else set()
