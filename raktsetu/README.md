# RaktSetu - Intelligence Layer (ML, Matching, Blood Graph, Buffer)

This package is the data-science / ML half of RaktSetu. It turns the raw Blood
Warriors donor-patient dataset into:

1. clean entity tables,
2. an ABO/Rh compatibility + 90-day-cooldown eligibility engine,
3. a leakage-controlled donor **willingness** model and a transfusion-date **forecaster**,
4. a population-scale **Blood Graph** (the differentiator vs rigid 8-10 donor bridges),
5. a per-patient **one-cycle buffer** plan so no patient is ever left searching for blood.

The frontend/leaderboard/WhatsApp(Twilio) pieces are owned by the other teammate;
this package exposes plain functions + CSV/Parquet artifacts they can consume.

## Layout
| File | Responsibility |
|------|----------------|
| `config.py` | Paths + domain constants (cooldown, reference date, buffer policy) |
| `compatibility.py` | ABO/Rh donor->recipient rules, blood-group normalization |
| `data_processing.py` | Clean CSV, coerce types, derive features, split donors/patients/bridges |
| `eligibility.py` | 90-day cooldown / `next_eligible_date` -> `eligible_now`, `days_to_eligible` |
| `ml/features.py` | Feature set (with leakage controls documented) |
| `ml/willingness.py` | Gradient-boosted donor willingness/retention model + inactivity rule |
| `ml/forecast.py` | Per-patient next-transfusion-date forecaster (validated vs dataset) |
| `scoring.py` | Donor->patient match score (compatibility, proximity, reliability, willingness, recency) |
| `graph.py` | Blood Graph: candidate edges, k-hop pools, fair allocation, communities, coverage |
| `buffer.py` | One-cycle buffer plan + at-risk classification |

## Run
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# train the willingness model (writes data/outputs/models/willingness_model.joblib)
.venv/bin/python -m raktsetu.ml.willingness
# run the full pipeline (writes artifacts to data/outputs/)
.venv/bin/python -m scripts.run_pipeline
```
Note: on this machine `python` is aliased to system Python 3.9; always invoke
`.venv/bin/python` so you use the 3.10 venv where deps are installed.

## Outputs (`data/outputs/`)
- `transfusion_forecast.csv` - next transfusion date + days-until per patient
- `match_edges.parquet` - every (patient, eligible compatible donor) pair with score
- `allocation.csv` - Hungarian fair assignment of distinct donors for the current cycle
- `coverage_report.csv` - eligible-compatible donor count + at-risk flag per patient
- `buffer_summary.csv` / `buffer_assignments.csv` - one-cycle buffer plan
- `pipeline_stats.json` - run summary

## Key results / honesty notes
- **Willingness model**: the dataset label `user_donation_active_status` is
  *rule-derived* (Inactive == "not donated in 1 year" OR very low call->donation
  conversion). Training on those columns gives a meaningless ROC-AUC ~1.0
  (leakage). We exclude the rule drivers **and** their recency proxies and train
  on structural signals (role, donor type, cadence, donation depth, geography),
  giving an honest CV ROC-AUC ~0.92, driven mainly by `role` (emergency donors
  churn ~3x more than bridge donors). The exact inactivity rule is kept as a
  transparent early-warning trigger (`days_to_inactive_by_rule`).
- **Forecaster**: reproduces the platform's own `expected_next_transfusion_date`
  exactly (MAE 0) where cadence is known; its real value is blood-group back-off
  for patients with missing/irregular cadence.
- **Blood Graph**: vs the fragile 8-10 donor bridge, every patient gains a deep
  reachable pool of compatible eligible donors (O+ patients reach 1,600+; rare
  groups like O-/A- correctly have the thinnest pools). Result on this data: all
  patients buffered for the next cycle, 0 at risk.
