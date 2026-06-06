"""Load and clean the raw Blood Warriors dataset into tidy entity tables.

The raw CSV is a flat export where each row is a (user, role) record. We:
  - normalize the Postgres ``\\x...`` byte-string ids into plain hex strings,
  - coerce dates and numerics, strip impossible values,
  - normalize blood groups to canonical codes,
  - derive relative-time + reliability features against a fixed REFERENCE_DATE,
  - split into three tidy tables: donors, patients, and bridge membership.

Run as a script to materialize parquet/csv under ``data/processed/``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from . import synth
from .compatibility import normalize_group

DATE_COLS = [
    "last_transfusion_date",
    "expected_next_transfusion_date",
    "registration_date",
    "last_contacted_date",
    "last_donation_date",
    "next_eligible_date",
    "last_bridge_donation_date",
]

NUMERIC_COLS = [
    "quantity_required",
    "donations_till_date",
    "cycle_of_donations",
    "total_calls",
    "frequency_in_days",
    "calls_to_donations_ratio",
    "latitude",
    "longitude",
]

BOOL_COLS = [
    "role_status",
    "bridge_status",
    "status_of_bridge",
    "donated_earlier",
]


def _clean_id(val: object) -> object:
    """Strip the Postgres bytea ``\\x`` prefix from id strings."""
    if pd.isna(val):
        return pd.NA
    s = str(val).strip()
    if s.startswith("\\x"):
        s = s[2:]
    return s or pd.NA


def load_raw(path=None) -> pd.DataFrame:
    """Read the raw CSV with all columns as strings (we coerce explicitly)."""
    path = path or config.RAW_CSV
    return pd.read_csv(path, dtype=str, keep_default_na=True, na_values=["", "NULL", "null"])


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Type-coerce and feature-engineer the flat table."""
    df = df.copy()

    # Ids
    for col in ("user_id", "bridge_id"):
        if col in df.columns:
            df[col] = df[col].map(_clean_id)

    # Dates
    ref = pd.Timestamp(config.REFERENCE_DATE)
    for col in DATE_COLS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # Numerics
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Booleans
    for col in BOOL_COLS:
        if col in df.columns:
            df[col] = df[col].map({"true": True, "false": False, "True": True, "False": False})

    # cycle_of_donations has impossible negatives and a few absurd outliers.
    if "cycle_of_donations" in df.columns:
        df.loc[df["cycle_of_donations"] < 0, "cycle_of_donations"] = pd.NA
        df.loc[df["cycle_of_donations"] > 365, "cycle_of_donations"] = pd.NA

    # Canonical blood groups
    df["blood_group_norm"] = df["blood_group"].map(normalize_group)
    df["bridge_blood_group_norm"] = df["bridge_blood_group"].map(normalize_group)

    # ---- Telangana geo (replaces the India-wide coarse coords) ----
    # Re-ground everyone in Telangana so proximity is meaningful for a
    # Hyderabad-based operation; deterministic per user_id.
    geo = synth.telangana_coords(df["user_id"])
    df["latitude"] = geo["latitude"]
    df["longitude"] = geo["longitude"]
    df["city"] = geo["city"]

    # ---- No-show signal + analytic show-up rate ----
    df["no_shows"] = synth.synth_no_shows(df["user_id"], df["donations_till_date"])
    df["show_rate"] = synth.show_rate(df["donations_till_date"], df["no_shows"])

    # ---- Synthetic display names (dataset has no names) ----
    gender = df["gender"] if "gender" in df.columns else None
    df["name"] = synth.names(df["user_id"], gender)

    # ---- Derived relative-time features (anchored to REFERENCE_DATE) ----
    df["days_since_last_donation"] = (ref - df["last_donation_date"]).dt.days
    df["days_since_last_contact"] = (ref - df["last_contacted_date"]).dt.days
    df["days_until_eligible"] = (df["next_eligible_date"] - ref).dt.days
    df["tenure_days"] = (ref - df["registration_date"]).dt.days

    # Reliability: of the calls placed, how many converted to donations.
    # Lower calls_to_donations_ratio == more reliable (fewer calls per donation).
    calls = df["total_calls"].fillna(0)
    dons = df["donations_till_date"].fillna(0)
    df["conversion_rate"] = (dons / calls.replace(0, np.nan)).clip(upper=1.0)
    # A bounded reliability score in [0, 1]; donors never called yet get a neutral prior.
    df["reliability_score"] = df["conversion_rate"].fillna(0.5)

    return df


def split_entities(df: pd.DataFrame):
    """Split the cleaned flat frame into donors, patients, and bridge membership."""
    donors = df[df["role"].isin(config.DONOR_ROLES)].copy()
    patients = df[df["role"] == config.PATIENT_ROLE].copy()

    # Bridge membership: bridge donors currently attached to a patient's bridge.
    bridges = (
        df[df["bridge_id"].notna() & df["role"].isin(config.DONOR_ROLES)]
        [["user_id", "bridge_id", "blood_group_norm", "bridge_blood_group_norm",
          "reliability_score", "eligibility_status"]]
        .copy()
    )
    return donors, patients, bridges


def build(path=None, write: bool = True):
    """Full pipeline: load -> clean -> split. Optionally persist to data/processed."""
    raw = load_raw(path)
    clean_df = clean(raw)
    donors, patients, bridges = split_entities(clean_df)

    if write:
        clean_df.to_parquet(config.PROCESSED_DIR / "clean_all.parquet", index=False)
        donors.to_parquet(config.PROCESSED_DIR / "donors.parquet", index=False)
        patients.to_parquet(config.PROCESSED_DIR / "patients.parquet", index=False)
        bridges.to_parquet(config.PROCESSED_DIR / "bridges.parquet", index=False)

    return clean_df, donors, patients, bridges


if __name__ == "__main__":
    clean_df, donors, patients, bridges = build()
    print(f"Cleaned rows : {len(clean_df)}")
    print(f"Donors       : {len(donors)}")
    print(f"Patients     : {len(patients)}")
    print(f"Bridge links : {len(bridges)}  (unique bridges: {bridges['bridge_id'].nunique()})")
    print(f"Wrote processed tables to {config.PROCESSED_DIR}")
