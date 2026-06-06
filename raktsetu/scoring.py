"""Donor -> patient match scoring.

The pipeline is a strict cascade:
  1. Compatibility (ABO/Rh)         -- hard gate (compatibility.py)
  2. Eligibility (90-day cooldown)  -- hard gate (eligibility.py)
  3. Availability (patient-specific) -- hard gate (availability.py)
  4. Score = proximity + show-up + willingness  -- this module

After the three gates, three signals decide the ranking:
  - proximity   : closer donors are easier to mobilize (Telangana coords),
  - show_rate   : analytic probability the donor actually turns up,
  - willingness : ML retention score — donors who stay in the ecosystem rank
                  higher for blood-bridge and emergency matching.

Returns a score in [0, 1]; 0 means a gate blocked the pair.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .compatibility import is_compatible

# Post-gate ranking weights: proximity + show-up + ecosystem retention.
WEIGHTS = {
    "proximity": 0.45,
    "show_rate": 0.30,
    "willingness": 0.25,
}


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km between two lat/lon points."""
    if any(pd.isna(v) for v in (lat1, lon1, lat2, lon2)):
        return np.nan
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return float(2 * r * np.arcsin(np.sqrt(a)))


def proximity_score(dist_km: float, scale_km: float = 25.0) -> float:
    """Exponential decay; ~1 nearby, ~0.37 at scale_km, ~0 far away."""
    if pd.isna(dist_km):
        return 0.4  # unknown location -> mild neutral prior
    return float(np.exp(-dist_km / scale_km))


def match_score(donor: pd.Series, patient: pd.Series) -> float:
    """Combined donor->patient match score in [0, 1] (0 = a gate blocked it).

    Gates: ABO/Rh compatibility + cooldown eligibility. (Patient-specific
    availability is applied upstream in candidate generation.) Post-gate score is
    proximity + show-up rate only.
    """
    if not is_compatible(donor.get("blood_group_norm"), patient.get("bridge_blood_group_norm")
                         or patient.get("blood_group_norm")):
        return 0.0
    if donor.get("eligible_now") is False:
        return 0.0

    dist = haversine_km(donor.get("latitude"), donor.get("longitude"),
                        patient.get("latitude"), patient.get("longitude"))
    show = float(donor.get("show_rate", 0.8))
    will = float(donor.get("willingness", 0.5))
    score = (WEIGHTS["proximity"] * proximity_score(dist)
             + WEIGHTS["show_rate"] * show
             + WEIGHTS["willingness"] * will)
    return float(np.clip(score, 0.0, 1.0))
