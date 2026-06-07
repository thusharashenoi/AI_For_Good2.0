"""Input validators + quick-reply parsing for the WhatsApp FSM."""
from __future__ import annotations

import re
from typing import Optional

from shared import eligibility_rules

NAME_RE = re.compile(r"^[A-Za-z\u0900-\u097F\u0C00-\u0C7F]+(?:\s+[A-Za-z\u0900-\u097F\u0C00-\u0C7F]+)+$")


def valid_name(text: str) -> bool:
    """At least two words, only letters/spaces (Latin/Devanagari/Telugu)."""
    return bool(text and NAME_RE.match(text.strip()))


def parse_age(text: str) -> Optional[int]:
    m = re.search(r"\d{1,3}", text or "")
    return int(m.group(0)) if m else None


def parse_weight(text: str) -> Optional[float]:
    m = re.search(r"\d{1,3}(?:\.\d+)?", text or "")
    return float(m.group(0)) if m else None


def parse_units(text: str) -> Optional[int]:
    m = re.search(r"\d", text or "")
    return int(m.group(0)) if m else None


def parse_blood_group(text: str) -> Optional[str]:
    return eligibility_rules.normalize_blood_group(text)


YES_WORDS = {"yes", "y", "ok", "okay", "sure", "haan", "haa", "ha", "✅",
             "confirm", "confirmed", "yes i'll be there", "yes ill be there",
             "అవును", "हाँ", "हां", "donate", "avunu"}
NO_WORDS = {"no", "n", "nope", "nahi", "nahin", "cancel", "❌", "kaadu", "లేదు", "नहीं"}
LATER_WORDS = {"later", "baad mein", "baad me", "tarvata", "తర్వాత", "बाद में"}


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def is_yes(text: str) -> bool:
    t = _norm(text)
    return t in YES_WORDS or t.startswith("yes") or t.startswith("✅") or "i'll be there" in t


def is_no(text: str) -> bool:
    t = _norm(text)
    return t in NO_WORDS or t.startswith("no ") or t == "no" or t.startswith("❌") or t in {"n", "0"}


def is_later(text: str) -> bool:
    return _norm(text) in LATER_WORDS or "later" in _norm(text)


def is_first_time(text: str) -> bool:
    t = _norm(text)
    return "first" in t or t in NO_WORDS or "pehli baar" in t or "modati" in t


def menu_choice(text: str) -> Optional[int]:
    """Map '1'/'2'/'donor'/'patient' to a menu index."""
    t = _norm(text)
    if t in {"1", "donor", "blood donor", "रक्तदाता", "రక్తదాత"} or "donor" in t or "donat" in t:
        return 1
    if t in {"2", "patient", "guardian"} or "patient" in t or "blood" in t and "need" in t:
        return 2
    if t == "1":
        return 1
    if t == "2":
        return 2
    return None
