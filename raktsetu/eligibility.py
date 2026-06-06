"""Donor eligibility engine: 90-day whole-blood cooldown + basic deferral rules.

The dataset already carries an ``eligibility_status`` and a ``next_eligible_date``;
we trust those when present but also recompute a cooldown-based view from
``last_donation_date`` so the logic is transparent and works for synthetic donors
the rest of the team seeds later.
"""
from __future__ import annotations

import numpy as np
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
    if row.get("_live_eligibility") or row.get("registration_channel"):
        from .engagement_adapter import live_reference_date
        ref = live_reference_date()

    # Respect an explicit status when the data provides one.
    status = str(row.get("eligibility_status") or "").strip().lower()
    cooldown_clear = days_until_eligible(row, ref) <= 0
    if status == "eligible":
        return cooldown_clear
    if status in ("not eligible", "cooldown", "deferred"):
        return False
    return cooldown_clear


def annotate(donors: pd.DataFrame, ref_date: str | None = None) -> pd.DataFrame:
    """Add ``days_to_eligible`` and ``eligible_now`` columns to a donor frame."""
    if donors.empty:
        out = donors.copy()
        out["days_to_eligible"] = pd.Series(dtype=float)
        out["eligible_now"] = pd.Series(dtype=bool)
        return out

    live_mask = donors.get("_live_eligibility")
    if live_mask is None:
        live_mask = pd.Series(False, index=donors.index)
    else:
        live_mask = live_mask.fillna(False).astype(bool)

    from .engagement_adapter import live_reference_date
    fixed_ref = pd.Timestamp(ref_date or config.REFERENCE_DATE)
    live_ref = live_reference_date()

    out = donors.copy()
    out["days_to_eligible"] = np.nan
    out["eligible_now"] = False

    if (~live_mask).any():
        sub = out.loc[~live_mask]
        out.loc[~live_mask, "days_to_eligible"] = sub.apply(
            lambda r: days_until_eligible(r, fixed_ref), axis=1)
        out.loc[~live_mask, "eligible_now"] = sub.apply(
            lambda r: is_eligible(r, fixed_ref), axis=1)

    if live_mask.any():
        sub = out.loc[live_mask]
        out.loc[live_mask, "days_to_eligible"] = sub.apply(
            lambda r: days_until_eligible(r, live_ref), axis=1)
        out.loc[live_mask, "eligible_now"] = sub.apply(
            lambda r: is_eligible(r, live_ref), axis=1)

    return out
