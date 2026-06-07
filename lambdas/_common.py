"""Shared helpers for the intelligence Lambdas.

Loads donors/patients from DynamoDB into the pipeline DataFrame shape, annotates
eligibility, and caches them at module scope so warm invocations are cheap. The
willingness already lives on the donor item (seeded), so we don't need the model
at request time; we only fall back to it if a donor is missing a score.
"""
from __future__ import annotations

import os
import time
from typing import Any

import pandas as pd

from raktsetu import config, store
from raktsetu.eligibility import annotate
from raktsetu.graph import candidate_edges

# Simple warm-container cache. Bridge planning tolerates a few minutes of
# staleness; set CACHE_TTL=0 to disable.
_CACHE_TTL = int(os.environ.get("CACHE_TTL", "120"))
_cache: dict[str, Any] = {
    "ts": 0.0,
    "donors": None,
    "patients": None,
    "edges": None,
    "bridge_state": None,
}


def _cache_fresh() -> bool:
    return (time.time() - _cache["ts"]) < _CACHE_TTL and _cache["donors"] is not None


def load_frames(force: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    if _cache_fresh() and not force:
        return _cache["donors"], _cache["patients"]

    donors = annotate(store.load_donors_df())
    patients = store.load_patients_df()
    if "willingness" in donors.columns:
        donors["willingness"] = donors["willingness"].fillna(0.5)
    _cache.update(
        ts=time.time(),
        donors=donors,
        patients=patients,
        edges=None,
        bridge_state=None,
    )
    return donors, patients


def _build_bridge_state(items: list[dict]) -> dict[str, dict]:
    state: dict[str, dict] = {}
    for it in items:
        bid = it.get("bridgeId")
        if not bid:
            continue
        s = state.setdefault(bid, {"bridgeId": bid, "patientId": None,
                                   "status": None, "bloodGroup": None,
                                   "active": 0, "buffer": 0, "vacant": 0,
                                   "slots": []})
        if it.get("SK") == "META":
            s["patientId"] = it.get("patientId")
            s["status"] = it.get("status")
            s["bloodGroup"] = it.get("bloodGroup")
        elif str(it.get("SK", "")).startswith("SLOT#"):
            slot = {
                "slotId": str(it.get("SK")).split("#", 1)[-1],
                "slotType": it.get("slotType"),
                "status": it.get("status"),
                "donorId": it.get("donorId"),
                "score": float(it["score"]) if it.get("score") is not None else None,
                "backupFor": it.get("backupFor"),
                "reason": it.get("reason"),
            }
            s["slots"].append(slot)
            filled = it.get("status") in ("CONFIRMED", "PENDING")
            if it.get("slotType") == "buffer":
                s["buffer"] += 1 if filled else 0
            else:
                s["active"] += 1 if filled else 0
            if it.get("status") == "VACANT":
                s["vacant"] += 1
    for s in state.values():
        s["vacant"] = max(0, config.BRIDGE_SIZE - s["active"] - s["buffer"])
    return state


def load_bridge_state(force: bool = False) -> dict[str, dict]:
    """Cached bridge META + SLOT summary (avoids a full table scan per request)."""
    if _cache.get("bridge_state") is not None and _cache_fresh() and not force:
        return _cache["bridge_state"]
    state = _build_bridge_state(store.scan_bridges())
    _cache["bridge_state"] = state
    if not _cache.get("ts"):
        _cache["ts"] = time.time()
    return state


def load_edges(force: bool = False) -> pd.DataFrame:
    """Full (patient, eligible-compatible donor) edge set, computed once.

    Lets the dashboard derive coverage / at-risk / candidates for ALL patients
    from a single pass instead of re-scanning the donor pool per patient.
    """
    donors, patients = load_frames(force=force)
    if _cache.get("edges") is None or force:
        _cache["edges"] = candidate_edges(donors, patients)
    return _cache["edges"]


def edge_patient_stats(edges: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Per-patient eligible-compatible donor counts and best scores."""
    if edges.empty:
        return pd.Series(dtype=int), pd.Series(dtype=float)
    return (
        edges.groupby("patient_id")["donor_id"].nunique(),
        edges.groupby("patient_id")["score"].max(),
    )


def get_patient(patients: pd.DataFrame, patient_id: str) -> pd.Series | None:
    hit = patients[patients["user_id"] == patient_id]
    return None if hit.empty else hit.iloc[0]


def bridge_commitment_count(bridge: dict) -> int:
    """Donors who agreed (CONFIRMED/PENDING slots) — active + buffer fill."""
    return int(bridge.get("active", 0)) + int(bridge.get("buffer", 0))


def bridge_has_commitment(bridge: dict) -> bool:
    return bridge_commitment_count(bridge) >= 1


def patient_bridge_id(pat: pd.Series) -> str | None:
    bid = pat.get("bridge_id")
    if bid is None or pd.isna(bid) or str(bid) in ("nan", "None", ""):
        return None
    return str(bid)


def is_patient_committed_bridged(pat: pd.Series, bridge_state: dict[str, dict]) -> bool:
    """True once at least one donor has agreed to the patient's bridge."""
    bid = patient_bridge_id(pat)
    if not bid:
        return False
    b = bridge_state.get(bid)
    return b is not None and bridge_has_commitment(b)


def is_patient_unbridged(pat: pd.Series, bridge_state: dict[str, dict]) -> bool:
    return not is_patient_committed_bridged(pat, bridge_state)


def committed_bridges(state: dict[str, dict]) -> dict[str, dict]:
    """Bridges with at least one agreed donor — eligible for Bridge health."""
    return {bid: b for bid, b in state.items() if bridge_has_commitment(b)}


def state_machine_arn() -> str | None:
    return os.environ.get("OUTREACH_STATE_MACHINE_ARN")
