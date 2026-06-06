"""Interactive (zoom/drag) HTML visualization of the RaktSetu Blood Graph.

Design goals: clean and legible, not a hairball.
  - Patients are stars (labeled by blood group); donors are circles (no label,
    details on hover only) so the canvas stays uncluttered.
  - Edges are thin, translucent, curved; they brighten when you hover a node.
  - Generous spacing via tuned physics; navigation buttons for zoom/pan.
Open data/outputs/blood_graph_interactive.html in any browser.
"""
from __future__ import annotations

import json

from pyvis.network import Network

from raktsetu import config
from raktsetu import viz

N_PATIENTS = 8       # spotlight patients
TOP_DONORS = 12      # top reachable donors drawn per patient

# Slightly translucent fills look softer than flat saturated dots.
HIDDEN_FONT = {"size": 0, "color": "rgba(0,0,0,0)"}


def _legend_html() -> str:
    """A floating legend overlay pinned to the top-left of the canvas."""
    swatches = "".join(
        f'<div style="display:flex;align-items:center;gap:7px;">'
        f'<span style="width:13px;height:13px;border-radius:50%;background:{c};'
        f'display:inline-block;border:1px solid #1c2530;"></span>'
        f'<span>{g}</span></div>'
        for g, c in viz.GROUP_COLORS.items() if g is not None
    )
    return f"""
    <div id="rs-legend" style="position:fixed;top:18px;left:18px;z-index:1000;
        background:rgba(18,23,30,0.88);border:1px solid #2a3441;border-radius:12px;
        padding:14px 16px;font-family:Helvetica,Arial,sans-serif;color:#eaeaea;
        font-size:13px;box-shadow:0 6px 22px rgba(0,0,0,0.45);backdrop-filter:blur(4px);">
      <div style="font-weight:700;font-size:15px;margin-bottom:2px;">RaktSetu Blood Graph</div>
      <div style="font-size:11px;color:#9aa6b4;margin-bottom:10px;">
        patients &amp; their reachable compatible donors</div>
      <div style="font-weight:600;font-size:11px;color:#9aa6b4;text-transform:uppercase;
        letter-spacing:.5px;margin-bottom:6px;">Blood group</div>
      <div style="display:grid;grid-template-columns:auto auto;gap:5px 18px;margin-bottom:10px;">
        {swatches}
      </div>
      <div style="border-top:1px solid #2a3441;padding-top:9px;display:flex;
        flex-direction:column;gap:5px;">
        <div style="display:flex;align-items:center;gap:8px;">
          <span style="color:#ffd27f;font-size:17px;line-height:1;">&#9733;</span>
          <span>Patient</span></div>
        <div style="display:flex;align-items:center;gap:8px;">
          <span style="color:#c8d0db;font-size:15px;line-height:1;">&#9679;</span>
          <span>Donor <span style="color:#9aa6b4;">(size = willingness)</span></span></div>
        <div style="display:flex;align-items:center;gap:8px;color:#9aa6b4;font-size:11px;">
          <span style="display:inline-block;width:18px;height:0;border-top:2px solid #ffcf6b;">
          </span><span>hover a node to trace its pool</span></div>
      </div>
    </div>
    """


def main():
    print("Loading data + scoring...")
    donors, patients_fc, edges, _ = viz.prepare()
    donor_lookup = donors.set_index("user_id")

    net = Network(height="840px", width="100%", bgcolor="#0f1419",
                  font_color="#eaeaea", directed=False, notebook=False)

    pats = (patients_fc.dropna(subset=["blood_group_norm"])
            .drop_duplicates("blood_group_norm").head(N_PATIENTS))
    if len(pats) < N_PATIENTS:
        pats = patients_fc.head(N_PATIENTS)

    added_donors: set[str] = set()
    for _, p in pats.iterrows():
        pid = p["user_id"]
        pgroup = viz.patient_group(p)
        pnode = f"P:{pid}"
        net.add_node(
            pnode, label=f"  {pgroup}  ", shape=viz.PATIENT_SHAPE,
            color={"background": viz.color_for(pgroup), "border": "#ffffff"},
            size=30, borderWidth=3, shadow=True,
            font={"size": 22, "color": "#ffffff", "face": "Helvetica", "strokeWidth": 4,
                  "strokeColor": "#0f1419", "vadjust": -38},
            title=(f"<b>Patient</b> &mdash; {pgroup}<br>"
                   f"Next transfusion: {p.get('predicted_next_transfusion')}<br>"
                   f"Days until: {p.get('days_until_transfusion')}"),
        )
        for e in viz.reachable_donors(pid, edges, top=TOP_DONORS).itertuples(index=False):
            dnode = f"D:{e.donor_id}"
            if dnode not in added_donors:
                drow = donor_lookup.loc[e.donor_id] if e.donor_id in donor_lookup.index else None
                role = drow["role"] if drow is not None else "Donor"
                will = float(drow["willingness"]) if drow is not None else 0.5
                rel = float(drow["reliability_score"]) if drow is not None else 0.5
                net.add_node(
                    dnode, label=" ", shape=viz.DONOR_SHAPE,
                    color={"background": viz.color_for(e.donor_group), "border": "#1c2530"},
                    size=8 + 8 * will, borderWidth=1, font=HIDDEN_FONT,
                    title=(f"<b>Donor</b> ({role}) &mdash; {e.donor_group}<br>"
                           f"Willingness: {will:.2f} &middot; Reliability: {rel:.2f}<br>"
                           f"Match score: {e.score:.2f}"),
                )
                added_donors.add(dnode)
            net.add_edge(pnode, dnode, value=float(e.score))

    options = {
        "nodes": {"shadow": {"enabled": True, "size": 8, "x": 0, "y": 0,
                              "color": "rgba(0,0,0,0.4)"}},
        "edges": {
            "color": {"color": "rgba(150,160,175,0.18)",
                      "highlight": "#ffcf6b", "hover": "#ffcf6b"},
            "smooth": {"enabled": True, "type": "continuous", "roundness": 0.5},
            "scaling": {"min": 0.2, "max": 2.2},
            "width": 0.4, "hoverWidth": 1.4, "selectionWidth": 1.8,
        },
        "interaction": {"hover": True, "tooltipDelay": 80, "hideEdgesOnDrag": True,
                        "navigationButtons": True, "keyboard": False},
        "physics": {
            "barnesHut": {"gravitationalConstant": -22000, "centralGravity": 0.2,
                          "springLength": 190, "springConstant": 0.012,
                          "damping": 0.5, "avoidOverlap": 0.7},
            "minVelocity": 0.75,
            "stabilization": {"enabled": True, "iterations": 350},
        },
    }
    net.set_options(json.dumps(options))

    # Generate the page, then inject the floating legend overlay.
    html = net.generate_html(notebook=False)
    html = html.replace("</body>", _legend_html() + "\n</body>")

    out = config.OUTPUTS_DIR / "blood_graph_interactive.html"
    out.write_text(html, encoding="utf-8")
    print(f"Spotlighted {len(pats)} patients, {len(added_donors)} donors")
    print(f"Saved -> {out}\nOpen it in a browser to explore.")


if __name__ == "__main__":
    main()
