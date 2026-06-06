"""Donor graph network (NetworkX) — "Blood Bridge 2.0".

Models donors + patients as a graph and computes, for a given patient/request,
the k-hop *reachable* eligible+available donor pool plus a coverage score. This
is the key differentiator over a rigid 8-donor bridge: each patient draws from a
self-healing reachable subgraph.

Edges are created on:
- ABO/Rh compatibility (donor -> patient)
- geo-proximity (within PROXIMITY_KM)
- shared organisation / college
- shared area (locality string)

Production path (documented, not built): Amazon Neptune + Neptune ML GraphSAGE
for inductive donor->patient willingness link-prediction.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from shared import eligibility_rules as rules
from shared import geocoding

PROXIMITY_KM = 15.0


def build_graph(donors: List[Dict], patients: List[Dict]):
    import networkx as nx

    g = nx.Graph()
    for d in donors:
        g.add_node(f"donor:{d['donorId']}", kind="donor", data=d)
    for p in patients:
        g.add_node(f"patient:{p['patientId']}", kind="patient", data=p)

    # Donor <-> patient compatibility edges.
    for p in patients:
        pid = f"patient:{p['patientId']}"
        for d in donors:
            did = f"donor:{d['donorId']}"
            if rules.is_compatible(d.get("bloodGroup", ""), p.get("bloodGroup", "")):
                w = _edge_weight(d, p)
                if w > 0:
                    g.add_edge(did, pid, weight=w, kind="compatible")

    # Donor <-> donor affinity edges (same area / org) for k-hop expansion.
    for i, a in enumerate(donors):
        for b in donors[i + 1:]:
            aff = _donor_affinity(a, b)
            if aff > 0:
                g.add_edge(f"donor:{a['donorId']}", f"donor:{b['donorId']}",
                           weight=aff, kind="affinity")
    return g


def _edge_weight(donor: Dict, patient: Dict) -> float:
    weight = 0.5
    dist = _distance(donor, patient)
    if dist is not None and dist <= PROXIMITY_KM:
        weight += 0.5 * (1 - dist / PROXIMITY_KM)
    if donor.get("area") and donor.get("area") == patient.get("area"):
        weight += 0.2
    return round(weight, 3)


def _donor_affinity(a: Dict, b: Dict) -> float:
    score = 0.0
    if a.get("area") and a.get("area") == b.get("area"):
        score += 0.4
    if a.get("organization") and a.get("organization") == b.get("organization"):
        score += 0.4
    return score


def _distance(donor: Dict, patient: Dict) -> Optional[float]:
    if all(donor.get(k) is not None for k in ("lat", "lng")) and \
       all(patient.get(k) is not None for k in ("lat", "lng")):
        return geocoding.haversine_km(
            (float(donor["lat"]), float(donor["lng"])),
            (float(patient["lat"]), float(patient["lng"])))
    return None


def reachable_pool(graph, patient_id: str, k: int = 2) -> List[str]:
    """Donor node ids reachable within k hops of the patient node."""
    import networkx as nx

    start = f"patient:{patient_id}"
    if start not in graph:
        return []
    lengths = nx.single_source_shortest_path_length(graph, start, cutoff=k)
    return [n for n, d in lengths.items() if n.startswith("donor:") and d <= k]


def coverage_score(graph, patient_id: str, eligible_donor_ids: set, k: int = 2) -> Dict:
    """Coverage = #reachable donors who are currently eligible+available."""
    pool = reachable_pool(graph, patient_id, k=k)
    pool_ids = {n.split(":", 1)[1] for n in pool}
    available = pool_ids & eligible_donor_ids
    return {
        "reachable": len(pool_ids),
        "available": len(available),
        "coverageRatio": round(len(available) / len(pool_ids), 3) if pool_ids else 0.0,
        "atRisk": len(available) < 3,  # threshold for alerting/expanding the graph
        "availableDonorIds": sorted(available),
    }


def fair_allocation(donors: List[Dict], requests: List[Dict]) -> Dict[str, str]:
    """Assign donors to requests minimising over-tapping using a cost matrix +
    linear_sum_assignment (Hungarian algorithm). Returns {requestId: donorId}.

    Falls back to greedy if scipy is unavailable.
    """
    if not donors or not requests:
        return {}
    from .scoring import score_donor

    # cost = negative score (we maximise score -> minimise -score).
    try:
        import numpy as np
        from scipy.optimize import linear_sum_assignment

        cost = np.zeros((len(requests), len(donors)))
        for i, req in enumerate(requests):
            for j, d in enumerate(donors):
                if rules.is_compatible(d.get("bloodGroup", ""), req.get("bloodGroup", "")):
                    cost[i, j] = -score_donor(d, req)["score"]
                else:
                    cost[i, j] = 1e3  # effectively unassignable
        rows, cols = linear_sum_assignment(cost)
        out = {}
        for r, c in zip(rows, cols):
            if cost[r, c] < 1e3:
                out[requests[r]["requestId"]] = donors[c]["donorId"]
        return out
    except Exception:
        # Greedy fallback (no scipy/numpy).
        assigned_donors = set()
        out = {}
        for req in requests:
            best = None
            best_score = -1.0
            for d in donors:
                if d["donorId"] in assigned_donors:
                    continue
                if not rules.is_compatible(d.get("bloodGroup", ""), req.get("bloodGroup", "")):
                    continue
                s = score_donor(d, req)["score"]
                if s > best_score:
                    best, best_score = d, s
            if best:
                out[req["requestId"]] = best["donorId"]
                assigned_donors.add(best["donorId"])
        return out
