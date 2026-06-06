"""Donor eligibility engine: 90-day whole-blood cooldown + basic deferral rules.

The dataset already carries an ``eligibility_status`` and a ``next_eligible_date``;
we trust those when present but also recompute a cooldown-based view from
``last_donation_date`` so the logic is transparent and works for synthetic donors
the rest of the team seeds later.
"""
from __future__ import annotations

import pandas as pd

from . import config


def days_until_eligible(row, ref: pd.Timestamp) -> float:
    """Days until the donor can next donate (0 if eligible now).

    Preference order: explicit next_eligible_date -> last_donation + cooldown ->
    assume eligible (never donated).
    """
    ned = row.get("next_eligible_date")
    if pd.notna(ned):
        return max(0, (pd.Timestamp(ned) - ref).days)

    last = row.get("last_donation_date")
    if pd.notna(last):
        eligible_on = pd.Timestamp(last) + pd.Timedelta(days=config.WHOLE_BLOOD_COOLDOWN_DAYS)
        return max(0, (eligible_on - ref).days)

    return 0.0


def is_eligible(row, ref: pd.Timestamp) -> bool:
    """True if donor is past cooldown as of ref date."""
    # Respect an explicit status when the data provides one.
    status = str(row.get("eligibility_status") or "").strip().lower()
    cooldown_clear = days_until_eligible(row, ref) <= 0
    if status == "eligible":
        return cooldown_clear
    if status == "not eligible":
        # Honor the cooldown even if the flag says not-eligible for another reason.
        return False
    return cooldown_clear


def annotate(donors: pd.DataFrame, ref_date: str | None = None) -> pd.DataFrame:
    """Add ``days_to_eligible`` and ``eligible_now`` columns to a donor frame."""
    ref = pd.Timestamp(ref_date or config.REFERENCE_DATE)
    out = donors.copy()
    out["days_to_eligible"] = out.apply(lambda r: days_until_eligible(r, ref), axis=1)
    out["eligible_now"] = out.apply(lambda r: is_eligible(r, ref), axis=1)
    return out
