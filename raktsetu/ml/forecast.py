"""Transfusion-demand forecasting for Thalassemia patients.

Each patient transfuses on a roughly fixed cadence. We forecast the next
transfusion date so the buffer system can pre-arrange donors *before* the need
arises. Cadence is taken from the patient's own ``frequency_in_days`` when
present, otherwise backed off to the population median cadence for their blood
group, then the global median.

This is intentionally a transparent, well-calibrated estimator rather than a
black box: with only ~80 patients a heavy model would overfit, and we validate
it against the dataset's own ``expected_next_transfusion_date``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config

GLOBAL_FALLBACK_CADENCE = 21  # days; typical Thalassemia transfusion interval


def _valid_cadence(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    # Plausible transfusion cadence window; 0/blank means unknown.
    return s.where((s >= 7) & (s <= 60))


def fit_cadence_table(patients: pd.DataFrame) -> dict:
    """Median cadence per blood group, plus a global median, for back-off."""
    p = patients.copy()
    p["cad"] = _valid_cadence(p["frequency_in_days"])
    by_group = p.groupby("bridge_blood_group_norm")["cad"].median().dropna().to_dict()
    global_med = p["cad"].median()
    if pd.isna(global_med):
        global_med = GLOBAL_FALLBACK_CADENCE
    return {"by_group": by_group, "global": float(global_med)}


def predict_cadence(row, table: dict) -> float:
    """Best cadence estimate for a patient with graceful back-off."""
    own = _valid_cadence(pd.Series([row.get("frequency_in_days")])).iloc[0]
    if pd.notna(own):
        return float(own)
    grp = row.get("bridge_blood_group_norm") or row.get("blood_group_norm")
    if grp in table["by_group"]:
        return float(table["by_group"][grp])
    return float(table["global"])


def forecast(patients: pd.DataFrame, ref_date: str | None = None) -> pd.DataFrame:
    """Return per-patient next-transfusion forecast and days-until."""
    ref = pd.Timestamp(ref_date or config.REFERENCE_DATE)
    table = fit_cadence_table(patients)
    out = patients.copy()

    out["cadence_days"] = out.apply(lambda r: predict_cadence(r, table), axis=1)

    # Anchor on the last transfusion; if missing, anchor on reference date.
    anchor = out["last_transfusion_date"].fillna(ref)
    out["predicted_next_transfusion"] = anchor + pd.to_timedelta(out["cadence_days"], unit="D")

    # If the predicted date is already in the past relative to ref, roll forward
    # by whole cadence cycles so we always forecast the *next* upcoming need.
    def _roll(row):
        nxt = row["predicted_next_transfusion"]
        cad = max(row["cadence_days"], 1)
        if pd.isna(nxt):
            return nxt
        while nxt < ref:
            nxt = nxt + pd.Timedelta(days=cad)
        return nxt

    out["predicted_next_transfusion"] = out.apply(_roll, axis=1)
    out["days_until_transfusion"] = (out["predicted_next_transfusion"] - ref).dt.days
    return out


def evaluate(patients: pd.DataFrame) -> dict:
    """Validate the forecast against the dataset's expected_next_transfusion_date."""
    p = patients.copy()
    table = fit_cadence_table(p)
    p["cad"] = p.apply(lambda r: predict_cadence(r, table), axis=1)
    anchor = p["last_transfusion_date"]
    pred = anchor + pd.to_timedelta(p["cad"], unit="D")
    actual = p["expected_next_transfusion_date"]
    mask = pred.notna() & actual.notna()
    err = (pred[mask] - actual[mask]).dt.days.abs()
    return {
        "n": int(mask.sum()),
        "mae_days": float(err.mean()) if len(err) else float("nan"),
        "median_abs_err_days": float(err.median()) if len(err) else float("nan"),
        "within_3_days_pct": float((err <= 3).mean() * 100) if len(err) else float("nan"),
    }


if __name__ == "__main__":
    from ..data_processing import build

    _, _, patients, _ = build(write=False)
    metrics = evaluate(patients)
    print("Transfusion forecast validation vs expected_next_transfusion_date:")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    fc = forecast(patients)
    print("\nSample forecasts:")
    print(
        fc[["user_id", "blood_group_norm", "cadence_days",
            "predicted_next_transfusion", "days_until_transfusion"]].head(8).to_string(index=False)
    )
