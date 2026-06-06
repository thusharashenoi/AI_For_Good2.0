"""The Blood Graph: a population-scale donor-patient network.

This replaces the rigid "Blood Bridge" (8-10 donors hard-bound to one patient,
which collapses when a few drop out or hit cooldown) with a graph where every
patient draws from their *reachable* subgraph of eligible, compatible donors -
their primary bridge plus 2nd/3rd-degree donors reachable through shared
geography. The result is a self-healing "Blood Bridge 2.0" with built-in backups.

Provides:
  - candidate edges (patient <- eligible compatible donor) weighted by match score,
  - a NetworkX graph (bipartite patient-donor + sparse donor-donor affinity),
  - k-hop reachable donor pools per patient,
  - fair allocation (Hungarian assignment) so no donor is over-tapped,
  - Louvain community detection to auto-discover/optimize bridges,
  - per-patient coverage scoring to flag patients whose pool is too thin.
"""
from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd

from . import availability as avail
from . import config
from .compatibility import compatible_donor_groups
from .scoring import WEIGHTS, proximity_score


def _vec_distance_km(donors: pd.DataFrame, plat, plon) -> np.ndarray:
    """Great-circle distance (km) from each donor to the patient; NaN if unknown."""
    if pd.isna(plat) or pd.isna(plon):
        return np.full(len(donors), np.nan)
    r = 6371.0
    lat1 = np.radians(donors["latitude"].to_numpy(dtype=float))
    p2 = np.radians(float(plat))
    dphi = np.radians(float(plat) - donors["latitude"].to_numpy(dtype=float))
    dlmb = np.radians(float(plon) - donors["longitude"].to_numpy(dtype=float))
    a = np.sin(dphi / 2) ** 2 + np.cos(lat1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def candidate_edges(donors_scored: pd.DataFrame, patients: pd.DataFrame,
                    apply_availability: bool = True) -> pd.DataFrame:
    """All (patient, donor) pairs that pass the gates, with a match score.

    Cascade per patient:
      1. compatible blood groups (ABO/Rh),
      2. eligible_now (cooldown),
      3. patient-specific availability gate (distance-weighted, deterministic),
      4. score = proximity + show_rate + willingness.

    ``donors_scored`` must already have ``eligible_now``. ``show_rate`` is used if
    present (else a 0.8 prior). Set ``apply_availability=False`` to inspect the
    pre-gate pool (e.g. for coverage analysis).
    """
    rows = []
    elig = donors_scored[donors_scored["eligible_now"] != False].copy()  # noqa: E712
    if elig.empty:
        return pd.DataFrame(columns=["patient_id", "donor_id", "donor_group", "patient_group", "score"])

    donor_seeds_all = avail.seed_array(elig["user_id"])
    elig = elig.reset_index(drop=True)
    show_all = (elig["show_rate"].to_numpy(dtype=float)
                if "show_rate" in elig.columns else np.full(len(elig), 0.8))
    will_all = (elig["willingness"].to_numpy(dtype=float)
                if "willingness" in elig.columns else np.full(len(elig), 0.5))

    for _, pat in patients.iterrows():
        pgroup = pat.get("bridge_blood_group_norm") or pat.get("blood_group_norm")
        ok_groups = compatible_donor_groups(pgroup)
        if not ok_groups:
            continue
        gmask = elig["blood_group_norm"].isin(ok_groups).to_numpy()
        if not gmask.any():
            continue

        dist = _vec_distance_km(elig, pat.get("latitude"), pat.get("longitude"))
        mask = gmask
        if apply_availability:
            pseed = avail.patient_seed(pat["user_id"])
            mask = gmask & avail.is_available(donor_seeds_all, pseed, dist)
            if not mask.any():
                continue

        prox = np.exp(-np.nan_to_num(dist[mask], nan=40.0) / 25.0)
        show = show_all[mask]
        will = will_all[mask]
        score = (WEIGHTS["proximity"] * prox + WEIGHTS["show_rate"] * show
                 + WEIGHTS["willingness"] * will)
        sub = pd.DataFrame({
            "patient_id": pat["user_id"],
            "donor_id": elig.loc[mask, "user_id"].to_numpy(),
            "donor_group": elig.loc[mask, "blood_group_norm"].to_numpy(),
            "patient_group": pgroup,
            "distance_km": np.round(dist[mask], 1),
            "score": np.clip(score, 0, 1),
        })
        rows.append(sub)
    if not rows:
        return pd.DataFrame(columns=["patient_id", "donor_id", "donor_group", "patient_group", "score"])
    return pd.concat(rows, ignore_index=True)


def build_graph(edges: pd.DataFrame, donors_scored: pd.DataFrame, patients: pd.DataFrame,
                geo_round: int = 2) -> nx.Graph:
    """Bipartite patient-donor graph plus sparse donor-donor geo-affinity edges."""
    g = nx.Graph()
    for _, p in patients.iterrows():
        g.add_node(f"P:{p['user_id']}", kind="patient",
                   blood_group=p.get("bridge_blood_group_norm") or p.get("blood_group_norm"))
    for _, d in donors_scored.iterrows():
        g.add_node(f"D:{d['user_id']}", kind="donor", blood_group=d.get("blood_group_norm"),
                   willingness=float(d.get("willingness", 0.5)),
                   eligible=bool(d.get("eligible_now", True)))

    for e in edges.itertuples(index=False):
        g.add_edge(f"P:{e.patient_id}", f"D:{e.donor_id}", weight=float(e.score), kind="can_donate")

    # Donor-donor affinity: same blood group + same rounded geo bucket -> they
    # can back each other up. Sparse by construction (bucketed), so it scales.
    elig = donors_scored.copy()
    elig["geo"] = (elig["latitude"].round(geo_round).astype(str) + "," +
                   elig["longitude"].round(geo_round).astype(str))
    for (grp, geo), members in elig.groupby(["blood_group_norm", "geo"]):
        ids = members["user_id"].tolist()
        if len(ids) < 2 or len(ids) > 60:  # skip giant buckets to stay sparse
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                g.add_edge(f"D:{ids[i]}", f"D:{ids[j]}", weight=0.5, kind="affinity")
    return g


def reachable_pool(g: nx.Graph, patient_id: str, k_hops: int = 2) -> list[str]:
    """Donor node ids reachable from a patient within k hops (the backup pool)."""
    src = f"P:{patient_id}"
    if src not in g:
        return []
    seen = nx.single_source_shortest_path_length(g, src, cutoff=k_hops)
    return [n for n in seen if g.nodes[n].get("kind") == "donor"]


def fair_allocation(edges: pd.DataFrame, slots_per_patient: dict | None = None) -> pd.DataFrame:
    """Assign distinct donors to patients maximizing total score (no donor reused).

    Greedy by score: each donor is used at most once; each patient slot is filled
    at most once. Avoids scipy on Lambda (deployment size).
    """
    if edges.empty:
        return pd.DataFrame(columns=["patient_id", "donor_id", "score"])

    slots_per_patient = slots_per_patient or {}
    candidates: list[tuple[str, int, str, float]] = []
    for e in edges.itertuples(index=False):
        n_slots = int(slots_per_patient.get(e.patient_id, 1))
        score = float(e.score)
        for slot in range(n_slots):
            candidates.append((e.patient_id, slot, e.donor_id, score))

    candidates.sort(key=lambda row: -row[3])
    used_donors: set[str] = set()
    used_slots: set[tuple[str, int]] = set()
    out: list[dict] = []
    for pid, slot, donor_id, score in candidates:
        if donor_id in used_donors or (pid, slot) in used_slots:
            continue
        used_donors.add(donor_id)
        used_slots.add((pid, slot))
        out.append({"patient_id": pid, "donor_id": donor_id, "score": score})
    return pd.DataFrame(out)


def detect_communities(g: nx.Graph) -> dict:
    """Louvain communities over the donor-donor affinity subgraph (auto-bridges)."""
    import community as community_louvain  # python-louvain (optional; not needed in Lambda)
    donor_sub = g.edge_subgraph(
        [(u, v) for u, v, d in g.edges(data=True) if d.get("kind") == "affinity"]
    ).copy()
    if donor_sub.number_of_edges() == 0:
        return {}
    return community_louvain.best_partition(donor_sub, weight="weight", random_state=42)


def coverage_report(edges: pd.DataFrame, patients: pd.DataFrame,
                    target: int = config.COVERAGE_TARGET_DONORS) -> pd.DataFrame:
    """Per-patient count of eligible compatible donors and an at-risk flag."""
    counts = edges.groupby("patient_id")["donor_id"].nunique() if not edges.empty else pd.Series(dtype=int)
    best = edges.groupby("patient_id")["score"].max() if not edges.empty else pd.Series(dtype=float)
    rows = []
    for _, p in patients.iterrows():
        pid = p["user_id"]
        n = int(counts.get(pid, 0))
        rows.append({
            "patient_id": pid,
            "blood_group": p.get("bridge_blood_group_norm") or p.get("blood_group_norm"),
            "eligible_compatible_donors": n,
            "best_match_score": float(best.get(pid, 0.0)),
            "at_risk": n < target,
        })
    return pd.DataFrame(rows).sort_values("eligible_compatible_donors")
