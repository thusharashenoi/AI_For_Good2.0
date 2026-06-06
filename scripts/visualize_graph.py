"""Static overview of the RaktSetu Blood Graph (a readable sample).

Spotlights a few patients (drawn as stars) and their top reachable compatible
eligible donors (drawn as circles), colored by blood group; edge width = match
score. Saves data/outputs/blood_graph.png.
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

N_PATIENTS = 6
TOP_DONORS = 12


def main():
    print("Loading data + scoring...")
    donors, patients_fc, edges, _ = viz.prepare()

    pats = (patients_fc.dropna(subset=["blood_group_norm"])
            .drop_duplicates("blood_group_norm")
            .head(N_PATIENTS))

    g = nx.Graph()
    for _, p in pats.iterrows():
        pnode = f"P:{p['user_id'][:6]}"
        g.add_node(pnode, kind="patient", group=viz.patient_group(p))
        for e in viz.reachable_donors(p["user_id"], edges, top=TOP_DONORS).itertuples(index=False):
            dnode = f"D:{e.donor_id[:6]}"
            g.add_node(dnode, kind="donor", group=e.donor_group)
            g.add_edge(pnode, dnode, weight=float(e.score))

    print(f"Sample subgraph: {g.number_of_nodes()} nodes, {g.number_of_edges()} edges")
    pos = nx.spring_layout(g, k=0.6, seed=42, weight="weight")

    fig, ax = plt.subplots(figsize=(16, 11))
    weights = np.array([g[u][v]["weight"] for u, v in g.edges()])
    nx.draw_networkx_edges(g, pos, width=1 + 3 * weights, edge_color="#bbbbbb", alpha=0.5, ax=ax)

    # Donors (circles) and patients (stars) drawn separately so shapes differ.
    donor_nodes = [n for n, d in g.nodes(data=True) if d["kind"] == "donor"]
    pat_nodes = [n for n, d in g.nodes(data=True) if d["kind"] == "patient"]
    nx.draw_networkx_nodes(g, pos, nodelist=donor_nodes,
                           node_color=[viz.color_for(g.nodes[n]["group"]) for n in donor_nodes],
                           node_shape=viz.DONOR_MARKER, node_size=220,
                           edgecolors="white", linewidths=0.5, ax=ax)
    nx.draw_networkx_nodes(g, pos, nodelist=pat_nodes,
                           node_color=[viz.color_for(g.nodes[n]["group"]) for n in pat_nodes],
                           node_shape=viz.PATIENT_MARKER, node_size=1400,
                           edgecolors="black", linewidths=2.0, ax=ax)

    legend = [Line2D([0], [0], marker="o", color="w", markerfacecolor=c, markersize=10, label=grp)
              for grp, c in viz.GROUP_COLORS.items() if grp is not None]
    legend += [
        Line2D([0], [0], marker="*", color="w", markerfacecolor="#999",
               markeredgecolor="black", markersize=18, label="Patient (star)"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#999",
               markeredgecolor="white", markersize=10, label="Donor (circle)"),
    ]
    ax.legend(handles=legend, title="Legend", loc="upper left", fontsize=9)
    ax.set_title("RaktSetu Blood Graph (sample): patients (stars) and their top "
                 "reachable compatible eligible donors (circles)\nedge width = match score",
                 fontsize=13)
    ax.axis("off")
    fig.tight_layout()
    out = config.OUTPUTS_DIR / "blood_graph.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    print(f"Saved -> {out}")


if __name__ == "__main__":
    main()
