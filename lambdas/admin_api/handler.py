"""Admin/ops API (FastAPI + Mangum) over the live DynamoDB state.

Read-only endpoints powering the ops dashboard:
  GET /stats               headline counts
  GET /patients            all patients + bridge status
  GET /unbridged           patients with no bridge (planner targets)
  GET /bridges             bridge health summary (active/buffer fill)
  GET /bridges/{id}        one bridge with its slots
  GET /at-risk             patients with the thinnest eligible donor pools
  GET /candidates/{pid}    ranked donor candidates for a patient
  GET /graph/overview      unassigned-donor blood graph for needy patients
  GET /graph/bridge/{id}   bridge constellation around one patient
  GET /graph/patient/{id}  ranked donor pool for one patient (unbridged focus)
  POST /emergency/match     ad-hoc patient outside the registry
  GET /emergency/cities     Telangana cities for the emergency form
  GET /healthz             liveness

Runs locally with uvicorn (see scripts/run_admin_api.py) and in Lambda via the
Mangum adapter exported as ``handler``.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
import pandas as pd
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from pydantic import BaseModel, Field

from raktsetu import config, emergency, store, synth
from raktsetu.eligibility import annotate

from .._common import get_patient, load_edges, load_frames

app = FastAPI(title="Blood Warriors Bridge Intelligence API", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
def _warm_cache():
    """Pre-load donors/patients/edges in a background thread so the first real
    request isn't blocked on the (network-bound) DynamoDB scan."""
    import threading

    def _warm():
        try:
            load_edges(force=True)
        except Exception:  # pragma: no cover - best-effort warmer
            pass

    threading.Thread(target=_warm, daemon=True).start()


def _bridge_state() -> dict[str, dict]:
    state: dict[str, dict] = {}
    for it in store.scan_bridges():
        bid = it.get("bridgeId")
        if not bid:
            continue
        s = state.setdefault(bid, {"bridgeId": bid, "patientId": None,
                                   "status": None, "bloodGroup": None,
                                   "active": 0, "buffer": 0, "vacant": 0,
                                   "slots": []})
        if it.get("SK") == "META":
            s["patientId"] = it.get("patientId")
            s["status"] = it.get("status")
            s["bloodGroup"] = it.get("bloodGroup")
        elif str(it.get("SK", "")).startswith("SLOT#"):
            slot = {
                "slotId": str(it.get("SK")).split("#", 1)[-1],
                "slotType": it.get("slotType"),
                "status": it.get("status"),
                "donorId": it.get("donorId"),
                "score": float(it["score"]) if it.get("score") is not None else None,
                "backupFor": it.get("backupFor"),
                "reason": it.get("reason"),
            }
            s["slots"].append(slot)
            filled = it.get("status") in ("CONFIRMED", "PENDING")
            if it.get("slotType") == "buffer":
                s["buffer"] += 1 if filled else 0
            else:
                s["active"] += 1 if filled else 0
            if it.get("status") == "VACANT":
                s["vacant"] += 1
    for s in state.values():
        # Existing bridges may only persist filled slots; treat missing capacity as vacant.
        s["vacant"] = max(0, config.BRIDGE_SIZE - s["active"] - s["buffer"])
    return state


def _bridged_donor_ids(bridge_state: dict[str, dict], donors: pd.DataFrame) -> set[str]:
    ids: set[str] = set()
    for b in bridge_state.values():
        for s in b.get("slots", []):
            did = s.get("donorId")
            if did and s.get("status") in ("CONFIRMED", "PENDING"):
                ids.add(str(did))
    if "bridge_id" in donors.columns:
        ids.update(donors.loc[donors["bridge_id"].notna(), "user_id"].astype(str))
    return ids


def _patient_group(pat: pd.Series) -> str | None:
    g = pat.get("bridge_blood_group_norm") or pat.get("blood_group_norm")
    return None if pd.isna(g) else str(g)


def _donor_node(drow: pd.Series, *, role: str, donor_id: str | None = None,
                  x: float | None = None, y: float | None = None) -> dict:
    uid = donor_id or drow.name or drow.get("user_id")
    node = {
        "id": f"D:{uid}",
        "donorId": uid,
        "label": drow.get("name") or str(uid)[:8],
        "kind": "donor",
        "group": drow.get("blood_group_norm"),
        "city": drow.get("city"),
        "role": role,
        "showRate": round(float(drow.get("show_rate", 0.8)), 3),
    }
    if x is not None:
        node["x"], node["y"] = x, y
    return node


def _patient_node(pat: pd.Series, *, reason: str, active: int | None = None,
                  bridge_id: str | None = None, x: float | None = None,
                  y: float | None = None) -> dict:
    node = {
        "id": f"P:{pat['user_id']}",
        "patientId": pat["user_id"],
        "label": pat.get("name") or str(pat["user_id"])[:8],
        "kind": "patient",
        "group": _patient_group(pat),
        "city": pat.get("city"),
        "reason": reason,
        "bridgeId": bridge_id,
        "activeCount": active,
    }
    if x is not None:
        node["x"], node["y"] = x, y
    return node


@app.get("/graph/overview")
def graph_overview(top_per_patient: int = 8):
    """Blood graph: unassigned donors linked to unbridged + under-strength patients."""
    donors, patients = load_frames()
    edges = load_edges()
    bridge_state = _bridge_state()
    in_bridge = _bridged_donor_ids(bridge_state, donors)
    dmeta = donors.set_index("user_id")

    targets: list[tuple[pd.Series, str, int | None, str | None]] = []
    for _, pat in patients.iterrows():
        bid = pat.get("bridge_id")
        if pd.isna(bid):
            targets.append((pat, "unbridged", None, None))
            continue
        bs = bridge_state.get(str(bid), {})
        active = int(bs.get("active", 0))
        fill = active + int(bs.get("buffer", 0))
        if fill < config.BRIDGE_SIZE:
            targets.append((pat, "under_strength", active, str(bid)))

    nodes: dict[str, dict] = {}
    edge_rows: list[dict] = []
    for pat, reason, active, bid in targets:
        pid = pat["user_id"]
        pnode = _patient_node(pat, reason=reason, active=active, bridge_id=bid)
        nodes[pnode["id"]] = pnode
        if edges.empty:
            continue
        sub = edges[(edges["patient_id"] == pid) & (~edges["donor_id"].isin(in_bridge))]
        sub = sub.sort_values("score", ascending=False).head(top_per_patient)
        for e in sub.itertuples(index=False):
            did = e.donor_id
            dnode_id = f"D:{did}"
            if dnode_id not in nodes and did in dmeta.index:
                nodes[dnode_id] = _donor_node(dmeta.loc[did], role="free_pool", donor_id=did)
            edge_rows.append({
                "from": pnode["id"], "to": dnode_id,
                "score": round(float(e.score), 4),
                "kind": "match",
            })

    return {
        "nodes": list(nodes.values()),
        "edges": edge_rows,
        "meta": {
            "targetPatients": len(targets),
            "unbridgedPatients": sum(1 for _, r, _, _ in targets if r == "unbridged"),
            "underStrengthPatients": sum(1 for _, r, _, _ in targets if r == "under_strength"),
            "freeDonorsInGraph": sum(1 for n in nodes.values() if n["kind"] == "donor"),
            "totalFreeDonors": int((~donors["user_id"].isin(in_bridge)).sum()),
        },
    }


@app.get("/graph/bridge/{bridge_id}")
def graph_bridge(bridge_id: str, top_candidates: int = 12):
    """Bridge constellation: patient center + members + ranked free candidates."""
    import math

    state = _bridge_state()
    b = state.get(bridge_id)
    if not b:
        return {"error": "not found", "bridgeId": bridge_id}
    donors, patients = load_frames()
    edges = load_edges()
    in_bridge = _bridged_donor_ids(state, donors)
    pat = get_patient(patients, b["patientId"])
    if pat is None:
        return {"error": "patient not found", "bridgeId": bridge_id}

    nodes: dict[str, dict] = {}
    edge_rows: list[dict] = []
    dmeta = donors.set_index("user_id")
    pnode = _patient_node(pat, reason="bridge_center", active=b["active"],
                          bridge_id=bridge_id, x=0, y=0)
    nodes[pnode["id"]] = pnode

    members = [s for s in b.get("slots", [])
               if s.get("donorId") and s.get("status") in ("CONFIRMED", "PENDING")]
    n_mem = max(len(members), 1)
    for i, slot in enumerate(members):
        did = slot["donorId"]
        if did not in dmeta.index:
            continue
        ang = 2 * math.pi * i / n_mem - math.pi / 2
        dnode = _donor_node(dmeta.loc[did], role=slot.get("slotType") or "bridge",
                            donor_id=did, x=140 * math.cos(ang), y=140 * math.sin(ang))
        dnode["slotId"] = slot.get("slotId")
        dnode["backupFor"] = slot.get("backupFor")
        nodes[dnode["id"]] = dnode
        edge_rows.append({
            "from": pnode["id"], "to": dnode["id"],
            "score": round(float(slot.get("score") or 0.85), 4),
            "kind": "bridge",
        })

    bridge_ids = {s["donorId"] for s in members}
    if not edges.empty:
        sub = edges[(edges["patient_id"] == pat["user_id"])
                    & (~edges["donor_id"].isin(in_bridge))
                    & (~edges["donor_id"].isin(bridge_ids))]
        sub = sub.sort_values("score", ascending=False).head(top_candidates)
        n_cand = max(len(sub), 1)
        for j, e in enumerate(sub.itertuples(index=False)):
            did = e.donor_id
            if did not in dmeta.index:
                continue
            ang = 2 * math.pi * j / n_cand - math.pi / 2
            dnode_id = f"D:{did}"
            nodes[dnode_id] = _donor_node(dmeta.loc[did], role="candidate", donor_id=did,
                                          x=260 * math.cos(ang), y=260 * math.sin(ang))
            edge_rows.append({
                "from": pnode["id"], "to": dnode_id,
                "score": round(float(e.score), 4),
                "kind": "candidate",
            })

    pname = patients.set_index("user_id")["name"].to_dict() if "name" in patients.columns else {}
    return {
        "bridgeId": bridge_id,
        "patientId": b["patientId"],
        "patientName": pname.get(b["patientId"]),
        "bloodGroup": b.get("bloodGroup"),
        "active": b["active"],
        "buffer": b["buffer"],
        "vacant": b["vacant"],
        "target": config.BRIDGE_SIZE,
        "nodes": list(nodes.values()),
        "edges": edge_rows,
    }


@app.get("/graph/patient/{patient_id}")
def graph_patient(patient_id: str, top_candidates: int = 14):
    """Patient focus view: center patient + ranked free-pool donors (for unbridged)."""
    import math

    donors, patients = load_frames()
    pat = get_patient(patients, patient_id)
    if pat is None:
        return {"error": "patient not found", "patientId": patient_id}
    edges = load_edges()
    bridge_state = _bridge_state()
    in_bridge = _bridged_donor_ids(bridge_state, donors)
    dmeta = donors.set_index("user_id")

    nodes: dict[str, dict] = {}
    edge_rows: list[dict] = []
    reason = "unbridged" if pd.isna(pat.get("bridge_id")) else "patient_focus"
    pnode = _patient_node(pat, reason=reason, x=0, y=0)
    nodes[pnode["id"]] = pnode

    if not edges.empty:
        sub = edges[(edges["patient_id"] == patient_id) & (~edges["donor_id"].isin(in_bridge))]
        sub = sub.sort_values("score", ascending=False).head(top_candidates)
        n_cand = max(len(sub), 1)
        for j, e in enumerate(sub.itertuples(index=False)):
            did = e.donor_id
            if did not in dmeta.index:
                continue
            ang = 2 * math.pi * j / n_cand - math.pi / 2
            dnode_id = f"D:{did}"
            slot_hint = "active" if j < config.BRIDGE_ACTIVE_TARGET else "buffer"
            dnode = _donor_node(dmeta.loc[did], role="candidate", donor_id=did,
                                x=200 * math.cos(ang), y=200 * math.sin(ang))
            dnode["slotTypeHint"] = slot_hint
            nodes[dnode_id] = dnode
            edge_rows.append({
                "from": pnode["id"], "to": dnode_id,
                "score": round(float(e.score), 4),
                "kind": "candidate",
            })

    return {
        "patientId": patient_id,
        "patientName": pat.get("name"),
        "bloodGroup": _patient_group(pat),
        "reason": reason,
        "candidateCount": sum(1 for n in nodes.values() if n["kind"] == "donor"),
        "target": config.BRIDGE_SIZE,
        "nodes": list(nodes.values()),
        "edges": edge_rows,
    }


@app.get("/healthz")
def healthz():
    return {"ok": True, "stage": config.STAGE, "region": config.AWS_REGION}


@app.get("/stats")
def stats():
    donors, patients = load_frames()
    donors = annotate(donors) if "eligible_now" not in donors.columns else donors
    bridges = _bridge_state()
    bridged = sum(1 for b in bridges.values() if b["status"] != "FORMING")
    return {
        "donors": int(len(donors)),
        "eligibleDonors": int(donors["eligible_now"].sum()) if "eligible_now" in donors else None,
        "avgShowRate": round(float(donors["show_rate"].mean()), 3) if "show_rate" in donors else None,
        "patients": int(len(patients)),
        "unbridgedPatients": int(patients["bridge_id"].isna().sum()),
        "bridges": len(bridges),
        "formingBridges": sum(1 for b in bridges.values() if b["status"] == "FORMING"),
        "vacantSlots": sum(b["vacant"] for b in bridges.values()),
    }


@app.get("/patients")
def list_patients():
    _, patients = load_frames()
    cols = ["user_id", "name", "blood_group_norm", "bridge_blood_group_norm",
            "city", "frequency_in_days", "expected_next_transfusion_date", "bridge_id"]
    out = patients[[c for c in cols if c in patients.columns]].copy()
    if "expected_next_transfusion_date" in out.columns:
        out["expected_next_transfusion_date"] = out["expected_next_transfusion_date"].astype(str)
    out = out.rename(columns={"user_id": "patientId", "name": "name",
                              "blood_group_norm": "bloodGroup",
                              "bridge_blood_group_norm": "bridgeBloodGroup",
                              "frequency_in_days": "frequencyDays",
                              "expected_next_transfusion_date": "nextTransfusion",
                              "bridge_id": "bridgeId"})
    return {"count": len(out), "patients": out.to_dict(orient="records")}


@app.get("/unbridged")
def unbridged():
    _, patients = load_frames()
    edges = load_edges()
    counts = edges.groupby("patient_id")["donor_id"].nunique() if not edges.empty else {}
    best = edges.groupby("patient_id")["score"].max() if not edges.empty else {}
    unb = patients[patients["bridge_id"].isna()]
    rows = []
    for _, pat in unb.iterrows():
        pid = pat["user_id"]
        rows.append({
            "patientId": pid,
            "name": pat.get("name"),
            "city": pat.get("city"),
            "bloodGroup": pat.get("bridge_blood_group_norm") or pat.get("blood_group_norm"),
            "candidatePool": int(counts.get(pid, 0)) if len(counts) else 0,
            "topScore": round(float(best.get(pid, 0.0)), 4) if len(best) else None,
        })
    return {"count": len(rows), "patients": rows}


@app.get("/bridges")
def bridges():
    state = _bridge_state()
    _, patients = load_frames()
    pname = patients.set_index("user_id")["name"].to_dict() if "name" in patients.columns else {}
    out = []
    for b in state.values():
        b = dict(b)
        b.pop("slots", None)
        b["patientName"] = pname.get(b.get("patientId"))
        b["fill"] = b["active"] + b["buffer"]
        b["target"] = config.BRIDGE_SIZE
        out.append(b)
    out.sort(key=lambda x: (x["fill"], x["vacant"]))
    return {"count": len(out), "bridges": out}


@app.get("/bridges/{bridge_id}")
def bridge_detail(bridge_id: str):
    state = _bridge_state()
    b = state.get(bridge_id)
    if not b:
        return {"error": "not found", "bridgeId": bridge_id}
    donors, patients = load_frames()
    dname = donors.set_index("user_id")["name"].to_dict() if "name" in donors.columns else {}
    pname = patients.set_index("user_id")["name"].to_dict() if "name" in patients.columns else {}
    b["patientName"] = pname.get(b.get("patientId"))
    for s in b["slots"]:
        s["donorName"] = dname.get(s.get("donorId"))
    b["slots"].sort(key=lambda s: s["slotId"])
    return b


@app.get("/at-risk")
def at_risk(limit: int = 15):
    _, patients = load_frames()
    edges = load_edges()
    counts = edges.groupby("patient_id")["donor_id"].nunique() if not edges.empty else {}
    best = edges.groupby("patient_id")["score"].max() if not edges.empty else {}
    rows = []
    for _, pat in patients.iterrows():
        pid = pat["user_id"]
        bridged = pat.get("bridge_id") is not None and str(pat.get("bridge_id")) not in ("nan", "None", "")
        rows.append({
            "patientId": pid,
            "name": pat.get("name"),
            "bloodGroup": pat.get("bridge_blood_group_norm") or pat.get("blood_group_norm"),
            "eligibleCompatibleDonors": int(counts.get(pid, 0)) if len(counts) else 0,
            "bestScore": round(float(best.get(pid, 0.0)), 4) if len(best) else None,
            "bridged": bool(bridged),
        })
    rows.sort(key=lambda r: r["eligibleCompatibleDonors"])
    return {"count": min(limit, len(rows)), "patients": rows[:limit]}


@app.get("/candidates/{patient_id}")
def candidates(patient_id: str, limit: int = 20):
    _, patients = load_frames()
    if get_patient(patients, patient_id) is None:
        return {"error": "patient not found", "patientId": patient_id}
    edges = load_edges()
    donors, patients = load_frames()
    dmeta = donors.set_index("user_id")
    show_by_id = dmeta["show_rate"].to_dict() if "show_rate" in donors.columns else {}
    will_by_id = dmeta["willingness"].to_dict() if "willingness" in donors.columns else {}
    name_by_id = dmeta["name"].to_dict() if "name" in donors.columns else {}
    city_by_id = dmeta["city"].to_dict() if "city" in donors.columns else {}
    pat = get_patient(patients, patient_id)
    sub = edges[edges["patient_id"] == patient_id].sort_values("score", ascending=False).head(limit)
    cands = [{
        "donorId": r.donor_id,
        "name": name_by_id.get(r.donor_id),
        "city": city_by_id.get(r.donor_id),
        "score": round(float(r.score), 4),
        "group": getattr(r, "donor_group", None),
        "distanceKm": round(float(getattr(r, "distance_km", float("nan"))), 1)
                      if pd.notna(getattr(r, "distance_km", float("nan"))) else None,
        "showRate": round(float(show_by_id.get(r.donor_id, 0.8)), 3),
        "willingness": round(float(will_by_id.get(r.donor_id, 0.5)), 3),
        "slotTypeHint": "active" if i < config.BRIDGE_ACTIVE_TARGET else "buffer",
    } for i, r in enumerate(sub.itertuples(index=False))]
    return {"patientId": patient_id, "patientName": pat.get("name") if pat is not None else None,
            "count": len(cands), "candidates": cands}


class EmergencyMatchRequest(BaseModel):
    patient_name: str = Field(..., min_length=1, max_length=120)
    blood_group: str = Field(..., min_length=2, max_length=8)
    city: str = Field(default="Hyderabad", max_length=64)
    limit: int = Field(default=15, ge=1, le=50)


@app.get("/emergency/cities")
def emergency_cities():
    return {"cities": synth.city_names()}


@app.post("/emergency/match")
def emergency_match(body: EmergencyMatchRequest):
    donors, patients = load_frames()
    try:
        return emergency.match_emergency(
            body.patient_name,
            body.blood_group,
            body.city,
            donors,
            patients,
            _bridge_state(),
            limit=body.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


handler = Mangum(app, api_gateway_base_path=f"/{config.STAGE}")
