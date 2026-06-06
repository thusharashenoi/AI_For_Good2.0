"""Shared helpers for Blood Graph visualizations (static + interactive).

Centralizes the blood-group palette, node-shape conventions, and the data prep
(scoring + edges + bridge membership) so the plotting scripts stay thin.
"""
from __future__ import annotations

import pandas as pd

from .data_processing import build
from .eligibility import annotate
from .ml import willingness as W
from .ml.forecast import forecast
from . import graph as G

# Blood-group color palette (shared by every visualization).
GROUP_COLORS = {
    "O+": "#e6194B", "O-": "#911eb4", "A+": "#3cb44b", "A-": "#469990",
    "B+": "#4363d8", "B-": "#000075", "AB+": "#f58231", "AB-": "#9A6324",
    None: "#999999",
}

# Node-shape conventions.
#   matplotlib markers: patient '*' (star), donor 'o' (circle)
#   vis.js shapes:      patient 'star',      donor 'dot'
PATIENT_MARKER = "*"
DONOR_MARKER = "o"
PATIENT_SHAPE = "star"
DONOR_SHAPE = "dot"


def color_for(group) -> str:
    return GROUP_COLORS.get(group, "#999999")


def prepare():
    """Load + score everything needed for visualization.

    Returns (donors_scored, patients_fc, edges, bridges).
    """
    _, donors, patients, bridges = build(write=False)
    donors = annotate(donors)
    donors["willingness"] = W.score(donors)
    patients_fc = forecast(patients)
    edges = G.candidate_edges(donors, patients_fc)
    return donors, patients_fc, edges, bridges


def patient_group(patient: pd.Series):
    return patient.get("bridge_blood_group_norm") or patient.get("blood_group_norm")


def rigid_bridge_donors(patient: pd.Series, donors: pd.DataFrame) -> pd.DataFrame:
    """The 'before' world: donors hard-bound to this patient's bridge_id."""
    bid = patient.get("bridge_id")
    if pd.isna(bid):
        return donors.iloc[0:0]
    return donors[donors["bridge_id"] == bid]


def reachable_donors(patient_id: str, edges: pd.DataFrame, top: int = 25) -> pd.DataFrame:
    """The 'after' world: top-scoring reachable compatible eligible donors."""
    sub = edges[edges["patient_id"] == patient_id]
    return sub.sort_values("score", ascending=False).head(top)
