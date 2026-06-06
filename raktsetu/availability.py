"""Patient-specific donor availability gate.

Real availability is not a global donor property: donor A may be in town and free
for patient Y this week but travelling and unreachable for patient X. We model a
deterministic, distance-weighted availability per ``(donor, patient)`` pair:
closer donors are more likely "available now", farther ones less so, giving an
overall unavailable fraction of roughly 20-30%.

Determinism comes from a stable hash of the (donor, patient) id pair, so the same
pair always resolves the same way across runs (reproducible demos), while still
varying per patient (A can be available for Y but not X).
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

# Availability-vs-distance curve. ~0.92 next door, ~0.37 at 100 km, floored 0.35.
_BASE = 0.92
_SLOPE = 0.55
_SCALE_KM = 100.0
_FLOOR = 0.35

_MIX_A = np.uint64(2654435761)
_MIX_B = np.uint64(40503)
_MIX_C = np.uint64(0x9E3779B97F4A7C15)
_TWO64 = float(2 ** 64)


def seed_array(ids) -> np.ndarray:
    """Stable uint64 seed for each id (vectorized via md5 prefix)."""
    return np.array(
        [int(hashlib.md5(str(u).encode()).hexdigest()[:16], 16) for u in ids],
        dtype=np.uint64,
    )


def patient_seed(pid: object) -> np.uint64:
    return np.uint64(int(hashlib.md5(str(pid).encode()).hexdigest()[:16], 16))


def _uniform(donor_seeds: np.ndarray, p_seed: np.uint64) -> np.ndarray:
    """Deterministic uniform [0,1) per (donor, patient) pair."""
    with np.errstate(over="ignore"):
        mixed = donor_seeds * _MIX_A + p_seed * _MIX_B + _MIX_C
    return (mixed.astype(np.float64)) / _TWO64


def p_available(dist_km) -> np.ndarray:
    d = np.asarray(dist_km, dtype=float)
    p = _BASE - _SLOPE * (d / _SCALE_KM)
    p = np.where(np.isnan(d), 0.6, p)  # unknown distance -> mild prior
    return np.clip(p, _FLOOR, _BASE)


def is_available(donor_seeds: np.ndarray, p_seed: np.uint64, dist_km) -> np.ndarray:
    """Boolean mask: is each donor available for this patient right now?"""
    return _uniform(donor_seeds, p_seed) < p_available(dist_km)
