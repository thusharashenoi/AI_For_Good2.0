"""Per-patient transfusion buffer system.

Goal: a patient must never have to go looking for blood. For every patient we
pre-arrange a **buffer of one full transfusion cycle** ahead of the predicted
need, on top of the current cycle's donors.

Cooldown-aware reservation: a donor who gives now is on a 90-day cooldown, and
the transfusion cadence is only ~3 weeks, so the same donor cannot cover both
the current and the next cycle. The buffer therefore needs *distinct* donors:
    buffer_target = units_per_cycle * (1 + BUFFER_CYCLES)
We draw them from the patient's reachable graph pool, ranked by match score, and
classify each patient as:
    - "buffered"      : enough eligible compatible donors in the direct pool,
    - "expand_graph"  : direct pool short, but k-hop reachable pool covers it,
    - "recruit"       : even the k-hop pool is short -> needs donor recruitment.
"""
from __future__ import annotations

import pandas as pd

from . import config
from .graph import reachable_pool


def _units_per_cycle(patient: pd.Series) -> int:
    q = patient.get("quantity_required")
    try:
        q = int(q)
    except (TypeError, ValueError):
        q = 1
    return max(q, 1)


def build_buffer(patients_fc: pd.DataFrame, edges: pd.DataFrame, g,
                 k_hops: int = 2) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (buffer_summary, buffer_assignments).

    ``patients_fc`` is the patient frame after forecast() (has cadence/next date).
    ``edges`` is the candidate-edge frame from graph.candidate_edges().
    ``g`` is the built Blood Graph (for k-hop reachable expansion).
    """
    summary_rows = []
    assign_rows = []

    edges_by_patient = {pid: df.sort_values("score", ascending=False)
                        for pid, df in edges.groupby("patient_id")} if not edges.empty else {}

    for _, pat in patients_fc.iterrows():
        pid = pat["user_id"]
        units = _units_per_cycle(pat)
        buffer_target = units * (1 + config.BUFFER_CYCLES)

        direct = edges_by_patient.get(pid, pd.DataFrame(columns=edges.columns))
        direct_pool = direct["donor_id"].tolist()

        # k-hop reachable backup pool (donors not necessarily directly compatible-scored).
        reachable = reachable_pool(g, pid, k_hops=k_hops)
        reachable_ids = {n[2:] for n in reachable}  # strip "D:" prefix
        reachable_count = len(reachable_ids)

        if len(direct_pool) >= buffer_target:
            status = "buffered"
        elif reachable_count >= buffer_target:
            status = "expand_graph"
        else:
            status = "recruit"

        # Assign the top donors: first `units` to the current cycle, next `units`
        # (distinct, cooldown-safe) reserved for the buffer cycle.
        chosen = direct.head(buffer_target)
        for rank, row in enumerate(chosen.itertuples(index=False)):
            cycle = "current" if rank < units else "buffer"
            assign_rows.append({
                "patient_id": pid,
                "donor_id": row.donor_id,
                "cycle": cycle,
                "score": float(row.score),
            })

        summary_rows.append({
            "patient_id": pid,
            "blood_group": pat.get("bridge_blood_group_norm") or pat.get("blood_group_norm"),
            "units_per_cycle": units,
            "buffer_target_donors": buffer_target,
            "direct_pool": len(direct_pool),
            "reachable_pool": reachable_count,
            "assigned": int(min(len(direct_pool), buffer_target)),
            "predicted_next_transfusion": pat.get("predicted_next_transfusion"),
            "days_until_transfusion": pat.get("days_until_transfusion"),
            "buffer_status": status,
        })

    summary = pd.DataFrame(summary_rows).sort_values(
        ["buffer_status", "direct_pool"], ascending=[True, True]
    )
    assignments = pd.DataFrame(assign_rows)
    return summary, assignments
