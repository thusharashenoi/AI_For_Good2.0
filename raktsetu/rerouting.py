"""Freshness-aware unit re-routing (7-10 day window).

Thalassemia transfusions can't bank blood: a collected unit is only usable for a
short window (~7-10 days). When a unit is collected but its intended patient
doesn't need it (e.g. a backup donor gave, but the primary active donor showed
up), the unit must be routed to ANOTHER compatible patient whose forecasted need
falls inside the freshness window — otherwise it's wasted.

This is a small assignment problem: given perishable units (group + collection
date) and upcoming patient needs (from the transfusion forecast), maximize units
used before expiry, prioritising the most perishable units and most urgent
patients. We solve it greedily by expiry/urgency, which is optimal for the common
case of single-unit needs and is transparent for a demo.
"""
from __future__ import annotations

import pandas as pd

from . import config
from .compatibility import is_compatible

MAX_SHELF_DAYS = 10   # hard expiry after collection
USABLE_FROM_DAYS = 0  # a fresh unit can be used immediately


def reroute(units: pd.DataFrame, patients_fc: pd.DataFrame,
            shelf_days: int = MAX_SHELF_DAYS, ref_date: str | None = None) -> dict:
    """Assign perishable units to compatible patients within the freshness window.

    ``units`` columns: unit_id, blood_group (donor group), collection_date,
        [intended_patient_id].
    ``patients_fc`` columns: user_id, (bridge_)blood_group_norm,
        predicted_next_transfusion, [quantity_required].

    Returns {assignments, wasted_units, coverage} where assignments is a list of
    {unit_id, donor_group, patient_id, need_date, collection_date, days_to_expiry}.
    """
    ref = pd.Timestamp(ref_date or config.REFERENCE_DATE)
    if units.empty or patients_fc.empty:
        return {"assignments": [], "wasted_units": int(len(units)), "coverage": 0.0}

    # Remaining need per patient within the planning horizon.
    need = {}
    for _, p in patients_fc.iterrows():
        nd = p.get("predicted_next_transfusion")
        if pd.isna(nd):
            continue
        q = p.get("quantity_required")
        try:
            q = max(int(q), 1)
        except (TypeError, ValueError):
            q = 1
        need[p["user_id"]] = {
            "group": p.get("bridge_blood_group_norm") or p.get("blood_group_norm"),
            "need_date": pd.Timestamp(nd),
            "remaining": q,
        }

    u = units.copy()
    u["collection_date"] = pd.to_datetime(u["collection_date"], errors="coerce").fillna(ref)
    u["expiry"] = u["collection_date"] + pd.Timedelta(days=shelf_days)
    u = u.sort_values("expiry")  # most perishable first

    assignments, wasted = [], 0
    for unit in u.itertuples(index=False):
        dgroup = unit.blood_group
        coll = unit.collection_date
        expiry = unit.expiry
        # Candidate patients: compatible, unmet need, need lands inside the window.
        best_pid, best_date = None, None
        for pid, info in need.items():
            if info["remaining"] <= 0:
                continue
            if not is_compatible(dgroup, info["group"]):
                continue
            nd = info["need_date"]
            if nd < coll or nd > expiry:
                continue
            # Prefer the most urgent (soonest) need that still fits.
            if best_date is None or nd < best_date:
                best_pid, best_date = pid, nd
        if best_pid is None:
            wasted += 1
            continue
        need[best_pid]["remaining"] -= 1
        assignments.append({
            "unit_id": getattr(unit, "unit_id", None),
            "donor_group": dgroup,
            "patient_id": best_pid,
            "need_date": best_date.date().isoformat(),
            "collection_date": coll.date().isoformat(),
            "days_to_expiry": int((expiry - best_date).days),
            "rerouted": getattr(unit, "intended_patient_id", None) != best_pid,
        })

    total = len(u)
    return {
        "assignments": assignments,
        "wasted_units": wasted,
        "coverage": round(len(assignments) / total, 3) if total else 0.0,
    }
