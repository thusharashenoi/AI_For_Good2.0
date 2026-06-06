"""Synthetic data realism for the demo.

The raw dataset's coordinates are India-wide and coarse (≈130 unique points from
Kashmir to Kerala), which makes "proximity" meaningless for a Telangana-based
operation, and it carries no no-show signal at all. This module deterministically
(per ``user_id``) re-grounds both:

  - ``telangana_coords``: places each person in Telangana, Hyderabad-metro heavy
    with a realistic spread to district towns;
  - ``synth_no_shows``: gives each donor a plausible no-show count correlated
    with their donation history and a per-donor "flakiness" prior.

Determinism (seed derived from the id) means a person lands in the same place
with the same reliability on every run, and the same across donor/patient tables.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

# (name, lat, lon, weight, jitter_deg). Hyderabad metro dominates; district
# towns share the remainder. jitter ~0.12deg ≈ 13km spread around each centre.
TELANGANA_CENTERS = [
    ("Hyderabad",   17.3850, 78.4867, 0.55, 0.18),
    ("Secunderabad",17.4399, 78.4983, 0.10, 0.10),
    ("Warangal",    17.9689, 79.5941, 0.07, 0.12),
    ("Nizamabad",   18.6725, 78.0941, 0.05, 0.12),
    ("Karimnagar",  18.4386, 79.1288, 0.05, 0.12),
    ("Khammam",     17.2473, 80.1514, 0.04, 0.12),
    ("Mahbubnagar", 16.7375, 78.0080, 0.04, 0.12),
    ("Nalgonda",    17.0575, 79.2684, 0.04, 0.12),
    ("Siddipet",    18.1018, 78.8520, 0.03, 0.10),
    ("Suryapet",    17.1305, 79.6230, 0.03, 0.10),
]

_NAMES = [c[0] for c in TELANGANA_CENTERS]
_LATS = np.array([c[1] for c in TELANGANA_CENTERS])
_LONS = np.array([c[2] for c in TELANGANA_CENTERS])
_WEIGHTS = np.array([c[3] for c in TELANGANA_CENTERS])
_WEIGHTS = _WEIGHTS / _WEIGHTS.sum()
_JITTER = np.array([c[4] for c in TELANGANA_CENTERS])

# Bayesian prior for the analytic show-rate: donors generally show up, so an
# unknown donor starts at alpha/(alpha+beta) = 0.8.
SHOW_PRIOR_ALPHA = 4.0
SHOW_PRIOR_BETA = 1.0


def _seed(uid: object) -> int:
    return int(hashlib.md5(str(uid).encode()).hexdigest()[:8], 16)


# ---- Synthetic Indian names (deterministic per id) ------------------------
_FIRST_M = [
    "Aarav", "Vivaan", "Aditya", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan",
    "Rohan", "Vihaan", "Karthik", "Rahul", "Akhil", "Tarun", "Naveen", "Surya",
    "Manoj", "Praveen", "Vamshi", "Charan", "Nikhil", "Rakesh", "Sandeep", "Kiran",
    "Harsha", "Teja", "Ravi", "Anil", "Suresh", "Mahesh",
]
_FIRST_F = [
    "Aanya", "Diya", "Saanvi", "Ananya", "Aadhya", "Pari", "Anika", "Navya",
    "Sri", "Keerthi", "Divya", "Sneha", "Pooja", "Lakshmi", "Swathi", "Harini",
    "Meghana", "Nandini", "Sushma", "Bhavana", "Tejaswi", "Akshara", "Ramya",
    "Sravani", "Deepika", "Madhuri", "Kavya", "Spandana", "Vaishnavi", "Anjali",
]
_LAST = [
    "Reddy", "Rao", "Naidu", "Sharma", "Kumar", "Goud", "Yadav", "Varma",
    "Chowdary", "Reddy", "Nair", "Iyer", "Pillai", "Patel", "Shetty", "Bhat",
    "Raju", "Prasad", "Mehta", "Gupta", "Desai", "Acharya", "Menon", "Babu",
]


def _name_for(uid: object, gender: str | None) -> str:
    rng = np.random.default_rng(_seed(uid) ^ 0x1234ABCD)
    g = str(gender or "").strip().lower()
    if g.startswith("f"):
        first = _FIRST_F[rng.integers(len(_FIRST_F))]
    elif g.startswith("m"):
        first = _FIRST_M[rng.integers(len(_FIRST_M))]
    else:
        pool = _FIRST_M + _FIRST_F
        first = pool[rng.integers(len(pool))]
    last = _LAST[rng.integers(len(_LAST))]
    return f"{first} {last}"


def names(ids: pd.Series, genders: pd.Series | None = None) -> pd.Series:
    """Deterministic synthetic Indian full name per id (gender-aware if given)."""
    if genders is None:
        genders = pd.Series([None] * len(ids), index=ids.index)
    return pd.Series(
        [_name_for(u, g) for u, g in zip(ids, genders)],
        index=ids.index, name="name",
    )


def telangana_coords(ids: pd.Series) -> pd.DataFrame:
    """Deterministic (lat, lon, city) for each id, Hyderabad-metro heavy."""
    lat, lon, city = [], [], []
    for uid in ids:
        rng = np.random.default_rng(_seed(uid))
        k = rng.choice(len(_NAMES), p=_WEIGHTS)
        lat.append(round(float(_LATS[k] + rng.normal(0, _JITTER[k])), 5))
        lon.append(round(float(_LONS[k] + rng.normal(0, _JITTER[k])), 5))
        city.append(_NAMES[k])
    return pd.DataFrame({"latitude": lat, "longitude": lon, "city": city}, index=ids.index)


def city_names() -> list[str]:
    return list(_NAMES)


def coords_for_city(city: str) -> tuple[float, float]:
    """Lat/lon for a Telangana city name; defaults to Hyderabad."""
    key = str(city or "").strip().lower()
    for name, lat, lon, *_ in TELANGANA_CENTERS:
        if name.lower() == key:
            return float(lat), float(lon)
    return 17.3850, 78.4867


def synth_no_shows(ids: pd.Series, donations: pd.Series) -> pd.Series:
    """Plausible no-show count per donor.

    A per-donor flakiness f ~ Beta(2, 12) (mean ≈ 0.14) is drawn deterministically,
    then no_shows ~ Binomial(donations, f). Reliable high-volume donors trend to
    ~0 no-shows; flakier ones accumulate misses. Donors with no history get 0
    (the backup policy treats them as "new" separately).
    """
    out = []
    don = pd.to_numeric(donations, errors="coerce").fillna(0).astype(int)
    for uid, d in zip(ids, don):
        if d <= 0:
            out.append(0)
            continue
        rng = np.random.default_rng(_seed(uid) ^ 0x5F3759DF)
        f = rng.beta(2, 12)
        out.append(int(rng.binomial(d, f)))
    return pd.Series(out, index=ids.index, name="no_shows")


def show_rate(donations: pd.Series, no_shows: pd.Series) -> pd.Series:
    """Analytic, smoothed show-up probability in (0, 1).

    show_rate = (shows + alpha) / (shows + misses + alpha + beta), where shows =
    donations_till_date and misses = no_shows. New donors fall back to the 0.8 prior.
    """
    d = pd.to_numeric(donations, errors="coerce").fillna(0)
    n = pd.to_numeric(no_shows, errors="coerce").fillna(0)
    return ((d + SHOW_PRIOR_ALPHA) /
            (d + n + SHOW_PRIOR_ALPHA + SHOW_PRIOR_BETA)).clip(0.01, 0.99)
