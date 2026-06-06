"""Before/after render: rigid Blood Bridge vs the RaktSetu Blood Graph pool.

LEFT  ('before'): one patient hard-bound to a fixed ~8-donor bridge. If a few of
                  those donors lapse or hit cooldown, the patient is stranded.
RIGHT ('after') : the same patient drawing from their full reachable subgraph of
                  compatible, currently-eligible donors - a deep, self-healing pool.

Patients are stars, donors are circles, colored by blood group.
Saves data/outputs/blood_graph_before_after.png.
"""
from __future__ import annotations

import os

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplcache")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
from matplotlib.lines import Line2D

from raktsetu import config
from raktsetu import viz

AFTER_TOP = 40  # cap the 'after' pool so the panel stays readable


def _draw_panel(ax, patient, donors_df, title, pgroup):
    g = nx.Graph()
    pnode = "PATIENT"
    g.add_node(pnode, kind="patient", group=pgroup)
    for d in donors_df.itertuples(index=False):
        # donors_df may come from edges (has donor_group) or donor rows (blood_group_norm)
        grp = getattr(d, "donor_group", None) or getattr(d, "blood_group_norm", None)
        score = float(getattr(d, "score", 0.7))
        did = getattr(d, "donor_id", None) or getattr(d, "user_id")
        dnode = f"D:{str(did)[:6]}"
        g.add_node(dnode, kind="donor", group=grp)
        g.add_edge(pnode, dnode, weight=score)

    n_donors = g.number_of_nodes() - 1
    pos = nx.spring_layout(g, k=0.9, seed=7, weight="weight")
    weights = np.array([g[u][v]["weight"] for u, v in g.edges()]) if g.number_of_edges() else np.array([])
    if len(weights):
        nx.draw_networkx_edges(g, pos, width=1 + 3 * weights, edge_color="#cccccc", alpha=0.6, ax=ax)

    donor_nodes = [n for n, d in g.nodes(data=True) if d["kind"] == "donor"]
    nx.draw_networkx_nodes(g, pos, nodelist=donor_nodes,
                           node_color=[viz.color_for(g.nodes[n]["group"]) for n in donor_nodes],
                           node_shape=viz.DONOR_MARKER, node_size=240,
                           edgecolors="white", linewidths=0.6, ax=ax)
    nx.draw_networkx_nodes(g, pos, nodelist=[pnode],
                           node_color=[viz.color_for(pgroup)],
                           node_shape=viz.PATIENT_MARKER, node_size=2000,
                           edgecolors="black", linewidths=2.5, ax=ax)
    ax.set_title(f"{title}\n{pgroup} patient | {n_donors} donors in pool", fontsize=12)
    ax.axis("off")
    return n_donors


def main():
    print("Loading data + scoring...")
    donors, patients_fc, edges, bridges = viz.prepare()

    # Pick the patient whose rigid bridge has the most donors (best contrast),
    # and who also has a healthy reachable pool.
    best_pid, best_n = None, -1
    for _, p in patients_fc.iterrows():
        rb = viz.rigid_bridge_donors(p, donors)
        if len(rb) > best_n:
            best_n, best_pid = len(rb), p["user_id"]
    patient = patients_fc[patients_fc["user_id"] == best_pid].iloc[0]
    pgroup = viz.patient_group(patient)

    rigid = viz.rigid_bridge_donors(patient, donors)
    reach = viz.reachable_donors(best_pid, edges, top=AFTER_TOP)
    print(f"Patient {best_pid[:8]} ({pgroup}): rigid bridge={len(rigid)}, reachable pool={len(reach)}")

    fig, (axl, axr) = plt.subplots(1, 2, figsize=(20, 10))
    n_before = _draw_panel(axl, patient, rigid,
                           "BEFORE - Rigid Blood Bridge (hard-bound donors)", pgroup)
    n_after = _draw_panel(axr, patient, reach,
                          "AFTER - RaktSetu Blood Graph (reachable eligible pool)", pgroup)

    legend = [Line2D([0], [0], marker="o", color="w", markerfacecolor=c, markersize=10, label=grp)
              for grp, c in viz.GROUP_COLORS.items() if grp is not None]
    legend += [
        Line2D([0], [0], marker="*", color="w", markerfacecolor="#999",
               markeredgecolor="black", markersize=18, label="Patient (star)"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#999",
               markeredgecolor="white", markersize=10, label="Donor (circle)"),
    ]
    fig.legend(handles=legend, title="Blood group / shape", loc="lower center",
               ncol=6, fontsize=9)
    fig.suptitle(f"Self-healing matching: {n_before} fixed donors  -->  "
                 f"{n_after}+ reachable eligible donors for the same patient",
                 fontsize=15, fontweight="bold")
    fig.tight_layout(rect=[0, 0.06, 1, 0.95])
    out = config.OUTPUTS_DIR / "blood_graph_before_after.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    print(f"Saved -> {out}")


if __name__ == "__main__":
    main()
