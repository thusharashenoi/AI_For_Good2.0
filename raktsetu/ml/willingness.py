"""Donor willingness / retention model.

We train a gradient-boosted classifier to predict whether a donor is currently an
*active* donor (the dataset's ``user_donation_active_status``). The predicted
probability of being active is used as a per-donor **willingness score** that
feeds the matching ranker and the buffer system: a donor who is compatible and
eligible but very likely to lapse should be ranked below an equally-eligible
reliable donor, and patients backed mostly by low-willingness donors are flagged.
"""
from __future__ import annotations

from dataclasses import dataclass

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .. import config
from . import features as F

MODEL_PATH = config.MODELS_DIR / "willingness_model.joblib"


def _build_pipeline() -> Pipeline:
    pre = ColumnTransformer(
        transformers=[
            ("num", StandardScaler(), F.NUMERIC_FEATURES),
            ("cat", OneHotEncoder(handle_unknown="ignore"), F.CATEGORICAL_FEATURES),
        ]
    )
    clf = GradientBoostingClassifier(random_state=42)
    return Pipeline([("pre", pre), ("clf", clf)])


@dataclass
class TrainResult:
    auc: float
    report: str
    n_train: int
    n_test: int


def train(donors: pd.DataFrame, save: bool = True) -> TrainResult:
    """Train the willingness model and (optionally) persist it."""
    x, y = F.make_xy(donors)
    x_tr, x_te, y_tr, y_te = train_test_split(
        x, y, test_size=0.2, random_state=42, stratify=y
    )
    pipe = _build_pipeline()
    pipe.fit(x_tr, y_tr)

    proba = pipe.predict_proba(x_te)[:, 1]
    auc = roc_auc_score(y_te, proba)
    report = classification_report(y_te, (proba >= 0.5).astype(int), digits=3)

    if save:
        joblib.dump(pipe, MODEL_PATH)
    return TrainResult(auc=auc, report=report, n_train=len(x_tr), n_test=len(x_te))


def load():
    """Load the trained pipeline (raises if not yet trained)."""
    return joblib.load(MODEL_PATH)


# ---- Transparent inactivity rule (early-warning trigger) ------------------
# The dataset's label is reproducible as: inactive if not donated in the last
# year OR very low call->donation conversion. We expose it explicitly so the
# orchestration layer can fire a proactive nudge *before* a donor crosses the
# threshold, rather than relying on the (leaky) classifier.
INACTIVITY_DAYS = 365
LOW_CONVERSION_CALLS = 3  # "multiple calls"
LOW_CONVERSION_RATIO = 3.0  # calls-per-donation considered very limited activity


def days_to_inactive_by_rule(row) -> float:
    """Days until this donor would be auto-flagged 'Not donated in last 1 year'.

    Negative means already past the threshold. Used as a proactive re-engagement
    trigger for the outreach scheduler.
    """
    dsld = row.get("days_since_last_donation")
    if pd.isna(dsld):
        return float("inf")
    return INACTIVITY_DAYS - float(dsld)


def score(donors: pd.DataFrame, model=None) -> pd.Series:
    """Return willingness probability in [0, 1] for each donor row."""
    model = model or load()
    x, _ = F.make_xy(donors)
    return pd.Series(model.predict_proba(x)[:, 1], index=donors.index, name="willingness")


if __name__ == "__main__":
    from ..data_processing import build

    _, donors, _, _ = build(write=False)
    res = train(donors)
    print(f"Willingness model trained on {res.n_train} donors (test {res.n_test})")
    print(f"ROC-AUC: {res.auc:.3f}")
    print(res.report)
    print(f"Saved -> {MODEL_PATH}")
