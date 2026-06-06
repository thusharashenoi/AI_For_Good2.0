"""End-to-end RaktSetu intelligence pipeline.

Runs the full chain and writes artifacts to data/outputs/:
  1. clean + split the dataset
  2. score donor willingness (ML) + annotate eligibility
  3. forecast each patient's next transfusion
  4. build candidate match edges + the Blood Graph
  5. fair allocation (Hungarian) for the current cycle
  6. Louvain communities (auto-bridges)
  7. per-patient coverage + one-cycle buffer plan

Usage:  .venv/bin/python -m scripts.run_pipeline
"""
from __future__ import annotations

import json

import pandas as pd

from raktsetu import config
from raktsetu.data_processing import build
from raktsetu.eligibility import annotate
from raktsetu.ml import willingness as W
from raktsetu.ml.forecast import forecast, evaluate
from raktsetu import graph as G
from raktsetu.buffer import build_buffer


def main():
    print("[1/7] Cleaning dataset...")
    _, donors, patients, bridges = build(write=True)

    print("[2/7] Scoring willingness (ML) + eligibility...")
    model = W.load()
    donors = annotate(donors)
    donors["willingness"] = W.score(donors, model=model)

    print("[3/7] Forecasting transfusions...")
    patients_fc = forecast(patients)
    fc_metrics = evaluate(patients)

    print("[4/7] Building candidate edges + Blood Graph...")
    edges = G.candidate_edges(donors, patients_fc)
    g = G.build_graph(edges, donors, patients_fc)

    print("[5/7] Fair allocation (Hungarian)...")
    def _units(v):
        return max(int(v), 1) if pd.notna(v) else 1

    slots = {p["user_id"]: _units(p.get("quantity_required"))
             for _, p in patients_fc.iterrows()}
    allocation = G.fair_allocation(edges, slots_per_patient=slots)

    print("[6/7] Community detection (auto-bridges)...")
    communities = G.detect_communities(g)
    n_comm = len(set(communities.values())) if communities else 0

    print("[7/7] Coverage + buffer plan...")
    coverage = G.coverage_report(edges, patients_fc)
    buf_summary, buf_assign = build_buffer(patients_fc, edges, g)

    # ---- Persist outputs ----
    out = config.OUTPUTS_DIR
    edges.to_parquet(out / "match_edges.parquet", index=False)
    allocation.to_csv(out / "allocation.csv", index=False)
    coverage.to_csv(out / "coverage_report.csv", index=False)
    buf_summary.to_csv(out / "buffer_summary.csv", index=False)
    buf_assign.to_csv(out / "buffer_assignments.csv", index=False)
    patients_fc[["user_id", "blood_group_norm", "cadence_days",
                 "predicted_next_transfusion", "days_until_transfusion"]].to_csv(
        out / "transfusion_forecast.csv", index=False)

    stats = {
        "donors": int(len(donors)),
        "patients": int(len(patients)),
        "eligible_donors": int(donors["eligible_now"].sum()),
        "mean_willingness": round(float(donors["willingness"].mean()), 3),
        "match_edges": int(len(edges)),
        "graph_nodes": g.number_of_nodes(),
        "graph_edges": g.number_of_edges(),
        "allocated_patients": int(allocation["patient_id"].nunique()) if not allocation.empty else 0,
        "communities": n_comm,
        "forecast_metrics": fc_metrics,
        "buffer_status_counts": buf_summary["buffer_status"].value_counts().to_dict(),
        "patients_at_risk": int(coverage["at_risk"].sum()),
    }
    with open(out / "pipeline_stats.json", "w") as f:
        json.dump(stats, f, indent=2, default=str)

    # ---- Console summary ----
    print("\n" + "=" * 60)
    print("RAKTSETU PIPELINE SUMMARY")
    print("=" * 60)
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print("\nMost at-risk patients (thinnest eligible donor pools):")
    print(coverage.head(8).to_string(index=False))
    print("\nBuffer status breakdown:")
    print(buf_summary["buffer_status"].value_counts().to_string())
    print(f"\nArtifacts written to {out}")


if __name__ == "__main__":
    pd.set_option("display.width", 160)
    main()
