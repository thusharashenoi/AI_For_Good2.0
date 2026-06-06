"""Feature definitions for the willingness model.

IMPORTANT - label leakage note:
The dataset's ``user_donation_active_status`` is *rule-derived*: ~85% of "Inactive"
donors simply have ``days_since_last_donation > 365`` ("Not donated in last 1 year")
and the remainder have a high ``calls_to_donations_ratio`` ("Very limited activity
despite multiple calls"). Training on those exact columns reconstructs the label
perfectly (ROC-AUC ~1.0) and teaches the model nothing generalizable.

We therefore train on a LEAKAGE-CONTROLLED feature set that excludes the rule
drivers (days_since_last_donation, total_calls, calls_to_donations_ratio,
reliability_score, inactive_trigger_comment). The resulting model predicts churn
risk from *structural* signals (role, donor type, tenure, cadence, recency of
contact, donation count, geography) and so can flag a donor who is still "Active"
today but trending toward lapse - which is the actually useful product signal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Leakage-controlled feature set (default).
# We also drop recency *proxies* (tenure_days, days_since_last_contact): because
# most donors are one-time, their registration date ~= last activity, so those
# columns sneak the recency rule back in. What remains is genuinely structural
# (who the donor is + how engaged they are), giving an honest CV ROC-AUC ~0.92
# driven mainly by role (emergency donors churn ~3x more than bridge donors).
# Note: latitude/longitude were dropped — coordinates are now synthetic Telangana
# values (synth.py) and carry no churn signal. Willingness feeds the match score
# (with proximity + show_rate) so bridge and emergency picks favour donors who
# stay engaged in the ecosystem.
NUMERIC_FEATURES = [
    "frequency_in_days",
    "donations_till_date",
]

CATEGORICAL_FEATURES = [
    "role",
    "donor_type",
]

# Columns deliberately excluded because they encode (or proxy) the label rule.
LEAKING_FEATURES = [
    "days_since_last_donation",
    "total_calls",
    "calls_to_donations_ratio",
    "reliability_score",
    "inactive_trigger_comment",
    "tenure_days",
    "days_since_last_contact",
]

TARGET = "user_donation_active_status"  # Active / Inactive


def make_xy(donors: pd.DataFrame):
    """Return (X, y) where y is 1 for Active, 0 for Inactive."""
    df = donors.copy()
    y = (df[TARGET].astype(str).str.strip().str.lower() == "active").astype(int)
    x = df[NUMERIC_FEATURES + CATEGORICAL_FEATURES].copy()
    x[NUMERIC_FEATURES] = x[NUMERIC_FEATURES].apply(pd.to_numeric, errors="coerce")
    x[NUMERIC_FEATURES] = x[NUMERIC_FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0)
    for col in CATEGORICAL_FEATURES:
        x[col] = x[col].fillna("unknown").astype(str)
    return x, y
