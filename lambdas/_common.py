"""Shared helpers for the intelligence Lambdas.

Loads donors/patients from DynamoDB into the pipeline DataFrame shape, annotates
eligibility, and caches them at module scope so warm invocations are cheap. The
willingness already lives on the donor item (seeded), so we don't need the model
at request time; we only fall back to it if a donor is missing a score.
"""
from __future__ import annotations

import os
import time

import pandas as pd

from raktsetu import store
from raktsetu.eligibility import annotate
from raktsetu.graph import candidate_edges

# Simple warm-container cache. Bridge planning tolerates a few minutes of
# staleness; set CACHE_TTL=0 to disable.
_CACHE_TTL = int(os.environ.get("CACHE_TTL", "120"))
_cache: dict = {"ts": 0.0, "donors": None, "patients": None, "edges": None}


def load_frames(force: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    now = time.time()
    fresh = (now - _cache["ts"]) < _CACHE_TTL and _cache["donors"] is not None
    if fresh and not force:
        return _cache["donors"], _cache["patients"]

    donors = annotate(store.load_donors_df())
    patients = store.load_patients_df()
    if "willingness" in donors.columns:
        donors["willingness"] = donors["willingness"].fillna(0.5)
    _cache.update(ts=now, donors=donors, patients=patients, edges=None)
    return donors, patients


def load_edges(force: bool = False) -> pd.DataFrame:
    """Full (patient, eligible-compatible donor) edge set, computed once.

    Lets the dashboard derive coverage / at-risk / candidates for ALL patients
    from a single pass instead of re-scanning the donor pool per patient.
    """
    donors, patients = load_frames(force=force)
    if _cache.get("edges") is None or force:
        _cache["edges"] = candidate_edges(donors, patients)
    return _cache["edges"]


def get_patient(patients: pd.DataFrame, patient_id: str) -> pd.Series | None:
    hit = patients[patients["user_id"] == patient_id]
    return None if hit.empty else hit.iloc[0]


def state_machine_arn() -> str | None:
    return os.environ.get("OUTREACH_STATE_MACHINE_ARN")
