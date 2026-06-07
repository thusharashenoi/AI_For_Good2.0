"""Admin/ops API (FastAPI + Mangum) over the live DynamoDB state.

Read-only endpoints powering the ops dashboard:
  GET /dashboard           stats + bridges + unbridged + at-risk (single round-trip)
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
  GET /calendar/patients     bridged patients list (search via ?q=)
  GET /calendar/patient/{id} transfusion calendar with donor coverage

Runs locally with uvicorn (see scripts/run_admin_api.py) and in Lambda via the
Mangum adapter exported as ``handler``.
"""
from __future__ import annotations

import copy

from fastapi import FastAPI, HTTPException
import pandas as pd
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from pydantic import BaseModel, Field

from raktsetu import broadcast, calendar_plan, config, emergency, store, synth
from raktsetu.bridge import formation_queue
from raktsetu.graph import candidate_edges

from .._common import (
    bridge_has_commitment,
    committed_bridges,
    edge_patient_stats,
    get_patient,
    is_patient_committed_bridged,
    is_patient_unbridged,
    load_bridge_state,
    load_edges,
    load_frames,
    patient_bridge_id,
)

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
            load_frames(force=True)
            load_bridge_state(force=True)
            load_edges(force=True)
        except Exception:  # pragma: no cover - best-effort warmer
            pass

    threading.Thread(target=_warm, daemon=True).start()


def _stats_payload(donors: pd.DataFrame, patients: pd.DataFrame,
                   bridges: dict[str, dict]) -> dict:
    committed = committed_bridges(bridges)
    unbridged_n = sum(
        1 for _, pat in patients.iterrows() if is_patient_unbridged(pat, bridges)
    )
    return {
        "donors": int(len(donors)),
        "eligibleDonors": int(donors["eligible_now"].sum()) if "eligible_now" in donors else None,
        "avgShowRate": round(float(donors["show_rate"].mean()), 3) if "show_rate" in donors else None,
        "patients": int(len(patients)),
        "unbridgedPatients": unbridged_n,
        "bridges": len(committed),
        "formingBridges": sum(
            1 for b in committed.values()
            if b.get("status") == "FORMING" and bridge_has_commitment(b)
        ),
        "vacantSlots": sum(b["vacant"] for b in committed.values()),
    }


def _bridges_payload(state: dict[str, dict], patients: pd.DataFrame) -> dict:
    pname = patients.set_index("user_id")["name"].to_dict() if "name" in patients.columns else {}
    out = []
    for b in committed_bridges(state).values():
        row = dict(b)
        row.pop("slots", None)
        row["patientName"] = pname.get(row.get("patientId"))
        row["fill"] = row["active"] + row["buffer"]
        row["target"] = config.BRIDGE_SIZE
        out.append(row)
    # One row per patient (drop duplicate FORMING bridges from repeated form actions).
    by_patient: dict[str, dict] = {}
    for row in out:
        pid = row.get("patientId") or row.get("bridgeId")
        prev = by_patient.get(pid)
        if prev is None or (row.get("fill", 0), row.get("bridgeId", "")) > (
            prev.get("fill", 0), prev.get("bridgeId", "")
        ):
            by_patient[pid] = row
    out = list(by_patient.values())
    out.sort(key=lambda x: (x["fill"], x["vacant"]))
    return {"count": len(out), "bridges": out}


def _unbridged_payload(patients: pd.DataFrame, edges: pd.DataFrame,
                       bridge_state: dict[str, dict]) -> dict:
    counts, best = edge_patient_stats(edges)
    rows = []
    for _, pat in patients.iterrows():
        if not is_patient_unbridged(pat, bridge_state):
            continue
        pid = pat["user_id"]
        row = {
            "patientId": pid,
            "name": pat.get("name"),
            "city": pat.get("city"),
            "bloodGroup": pat.get("bridge_blood_group_norm") or pat.get("blood_group_norm"),
            "candidatePool": int(counts.get(pid, 0)) if len(counts) else 0,
            "topScore": round(float(best.get(pid, 0.0)), 4) if len(best) else None,
        }
        bid = patient_bridge_id(pat)
        if bid and bid in bridge_state and not bridge_has_commitment(bridge_state[bid]):
            row["bridgeId"] = bid
            row["forming"] = True
        rows.append(row)
    return {"count": len(rows), "patients": rows}


def _at_risk_payload(patients: pd.DataFrame, edges: pd.DataFrame, limit: int,
                     bridge_state: dict[str, dict]) -> dict:
    counts, best = edge_patient_stats(edges)
    rows = []
    for _, pat in patients.iterrows():
        pid = pat["user_id"]
        bridged = is_patient_committed_bridged(pat, bridge_state)
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


def _safe_num(v, default=0.0, ndigits: int | None = None):
    if v is None:
        return default
    try:
        f = float(v)
        if pd.isna(f):
            return default
        return round(f, ndigits) if ndigits is not None else f
    except (TypeError, ValueError):
        return default


def _patient_edges(donors: pd.DataFrame, pat: pd.Series) -> pd.DataFrame:
    """Edges for one patient only — avoids rebuilding the full population graph."""
    if donors.empty:
        return pd.DataFrame()
    return candidate_edges(donors, pat.to_frame().T)


def _ranked_candidates(patient_id: str, donors: pd.DataFrame, edges: pd.DataFrame,
                       limit: int = 20) -> list[dict]:
    """Ranked donor pool for a patient — JSON-safe (no NaN)."""
    if donors.empty or edges.empty:
        return []
    dmeta = donors.drop_duplicates(subset=["user_id"], keep="first").set_index("user_id")
    show_by_id = dmeta["show_rate"].to_dict() if "show_rate" in dmeta.columns else {}
    will_by_id = dmeta["willingness"].to_dict() if "willingness" in dmeta.columns else {}
    name_by_id = dmeta["name"].to_dict() if "name" in dmeta.columns else {}
    city_by_id = dmeta["city"].to_dict() if "city" in dmeta.columns else {}
    sub = edges[edges["patient_id"] == patient_id].sort_values("score", ascending=False).head(limit)
    cands = []
    for i, (_, r) in enumerate(sub.iterrows()):
        did = r["donor_id"]
        dist = r.get("distance_km")
        cands.append({
            "donorId": did,
            "name": name_by_id.get(did),
            "city": city_by_id.get(did),
            "score": _safe_num(r["score"], ndigits=4),
            "group": None if pd.isna(r.get("donor_group")) else r.get("donor_group"),
            "distanceKm": _safe_num(dist, ndigits=1) if pd.notna(dist) else None,
            "showRate": _safe_num(show_by_id.get(did, 0.8), ndigits=3),
            "willingness": _safe_num(will_by_id.get(did, 0.5), ndigits=3),
            "slotTypeHint": "active" if i < config.BRIDGE_ACTIVE_TARGET else "buffer",
        })
    return cands


def _candidates_from_slots(slots: list[dict], patient_id: str,
                           donors: pd.DataFrame, edges: pd.DataFrame,
                           limit: int = 20) -> list[dict]:
    """Enrich bridge slot assignments with donor metadata for the admin drawer."""
    ranked = {c["donorId"]: c for c in _ranked_candidates(patient_id, donors, edges, limit=max(limit, 50))}
    cands = []
    for i, s in enumerate(slots):
        did = s.get("donorId")
        if not did:
            continue
        base = ranked.get(did, {"donorId": did})
        slot_type = s.get("slotType") or ("active" if i < config.BRIDGE_ACTIVE_TARGET else "buffer")
        cands.append({
            "donorId": did,
            "name": base.get("name"),
            "city": base.get("city"),
            "score": _safe_num(s.get("score") if s.get("score") is not None else base.get("score"), ndigits=4),
            "group": base.get("group"),
            "distanceKm": base.get("distanceKm"),
            "showRate": base.get("showRate", 0.8),
            "willingness": base.get("willingness", 0.5),
            "slotTypeHint": slot_type,
            "reason": s.get("reason"),
        })
        if len(cands) >= limit:
            break
    return cands


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
    bridge_state = load_bridge_state()
    in_bridge = _bridged_donor_ids(bridge_state, donors)
    dmeta = donors.set_index("user_id")

    targets: list[tuple[pd.Series, str, int | None, str | None]] = []
    for _, pat in patients.iterrows():
        bid = pat.get("bridge_id")
        if pd.isna(bid) or is_patient_unbridged(pat, bridge_state):
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

    state = load_bridge_state()
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
    bridge_state = load_bridge_state()
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


@app.get("/requests")
def open_requests(limit: int = 20):
    """Open blood-donation requests raised via WhatsApp/voice (automation flow)."""
    rows = store.load_open_requests(limit=limit)
    return {"count": len(rows), "requests": rows}


@app.delete("/requests/{request_id}")
def delete_request(request_id: str):
    """Delete an open blood request (demo cleanup)."""
    if not store.delete_request(request_id):
        raise HTTPException(status_code=404, detail="request not found")
    return {"ok": True, "requestId": request_id}


@app.post("/bridges/form/{patient_id}")
def form_bridge(patient_id: str):
    """Form a bridge from the Blood Graph for one unbridged patient (admin action)."""
    donors, patients = load_frames()
    bridge_state = load_bridge_state()
    pat = get_patient(patients, patient_id)
    if pat is None:
        raise HTTPException(status_code=404, detail="patient not found")
    if is_patient_committed_bridged(pat, bridge_state):
        raise HTTPException(status_code=400, detail="patient already has a bridge")
    pat_edges = _patient_edges(donors, pat)
    existing = patient_bridge_id(pat)
    if existing and existing in bridge_state and not bridge_has_commitment(bridge_state[existing]):
        b = bridge_state[existing]
        slots = b.get("slots") or []
        candidates = _candidates_from_slots(slots, patient_id, donors, pat_edges, limit=12)
        return {
            "ok": True,
            "patientId": patient_id,
            "bridgeId": existing,
            "coverage": len(candidates),
            "vacantSlots": b.get("vacant", 0),
            "candidates": candidates,
            "reused": True,
        }
    plan = formation_queue(pat, donors)
    if not plan.slots:
        return {
            "ok": False,
            "patientId": patient_id,
            "reason": "no_candidates",
            "coverage": plan.coverage,
        }
    vacant = store.persist_bridge_plan(plan, "FORMING")
    load_bridge_state(force=True)
    slots = [{
        "donorId": s.donor_id,
        "score": s.score,
        "slotType": s.slot_type,
        "reason": s.reason,
    } for s in plan.slots]
    candidates = _candidates_from_slots(slots, patient_id, donors, pat_edges, limit=12)
    return {
        "ok": True,
        "patientId": patient_id,
        "bridgeId": plan.bridge_id,
        "coverage": plan.coverage,
        "vacantSlots": vacant,
        "candidates": candidates,
    }


@app.post("/bridges/{bridge_id}/broadcast")
def broadcast_bridge(bridge_id: str):
    """One WhatsApp to demo phone; Vapi call after threshold if no reply."""
    state = load_bridge_state()
    bridge = state.get(bridge_id)
    if not bridge:
        raise HTTPException(status_code=404, detail="bridge not found")
    donors, patients = load_frames()
    pname = patients.set_index("user_id")["name"].to_dict() if "name" in patients.columns else {}
    patient_name = pname.get(bridge.get("patientId"))
    pat = get_patient(patients, bridge.get("patientId"))
    patient_meta = None
    if pat is not None:
        patient_meta = {
            k: (None if pd.isna(v) else v)
            for k, v in pat.items()
        }
    detail = copy.deepcopy(bridge)
    detail["slots"] = bridge.get("slots") or []
    try:
        result = broadcast.start_bridge_broadcast(
            detail,
            donors,
            patient_name=patient_name,
            patient_meta=patient_meta,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not result.get("ok"):
        detail = result.get("reason") or "broadcast failed"
        if result.get("error"):
            detail = f"{detail}: {result['error']}"
        raise HTTPException(status_code=400, detail=detail)
    return result


@app.get("/bridges/{bridge_id}/outreach-status")
def bridge_outreach_status(bridge_id: str):
    """Poll voice escalation outcome after a broadcast (demo phone conversation)."""
    import os
    import sys
    from pathlib import Path

    eng = Path(__file__).resolve().parents[2] / "engagement"
    if str(eng) not in sys.path:
        sys.path.insert(0, str(eng))
    from shared import dynamodb_client as db  # noqa: WPS433

    demo = os.environ.get("DEMO_OUTREACH_PHONE", "+919372875356")
    if not demo.startswith("+"):
        demo = f"+{demo.lstrip('+')}"
    conv = db.get_conversation(demo) or {}
    for alt in (demo, demo.lstrip("+"), f"+{demo.lstrip('+')}"):
        c = db.get_conversation(alt)
        if c:
            conv = c
            break
    result = conv.get("escalationCallResult") or {}
    return {
        "bridgeId": bridge_id,
        "requestId": conv.get("activeRequestId"),
        "voiceOutreachPlaced": bool(conv.get("voiceOutreachPlaced")),
        "voiceEscalationScheduled": bool(conv.get("outreachEscalationToken")),
        "escalationCallPlaced": bool(conv.get("escalationCallPlaced")),
        "escalationCallResult": result,
        "voiceCallId": conv.get("activeVoiceCallId"),
        "whatsappSentAt": conv.get("outreachWhatsappSentAt"),
    }


@app.get("/appointments")
def list_appointments(limit: int = 20):
    """Recent donation appointments booked via WhatsApp / voice outreach."""
    rows = store.list_appointments(limit=limit)
    out = []
    for a in rows:
        out.append({
            "appointmentId": a.get("appointmentId"),
            "bridgeId": a.get("bridgeId"),
            "requestId": a.get("requestId"),
            "donorName": a.get("donorName"),
            "donorPhone": a.get("donorPhone"),
            "patientName": a.get("patientName"),
            "bloodGroup": a.get("bloodGroup"),
            "hospital": a.get("hospital"),
            "city": a.get("city"),
            "appointmentDate": a.get("appointmentDate"),
            "appointmentTime": a.get("appointmentTime"),
            "status": a.get("status"),
            "channel": a.get("channel"),
            "createdAt": a.get("createdAt"),
        })
    return {"count": len(out), "appointments": out}


@app.delete("/appointments/{appointment_id}")
def delete_appointment(appointment_id: str):
    """Delete a donation appointment (demo cleanup)."""
    if not store.delete_appointment(appointment_id):
        raise HTTPException(status_code=404, detail="appointment not found")
    return {"ok": True, "appointmentId": appointment_id}


@app.get("/dashboard")
def dashboard(at_risk_limit: int = 12):
    """Single round-trip payload for the Operations tab (avoids 4 parallel Lambdas)."""
    try:
        donors, patients = load_frames()
        edges = load_edges()
        bridge_state = load_bridge_state()
        try:
            appts = store.list_appointments(limit=20)
        except Exception:
            appts = []
        try:
            req_rows = store.load_open_requests(limit=10)
        except Exception:
            req_rows = []
        return {
            "stats": _stats_payload(donors, patients, bridge_state),
            "bridges": _bridges_payload(bridge_state, patients),
            "unbridged": _unbridged_payload(patients, edges, bridge_state),
            "atRisk": _at_risk_payload(patients, edges, at_risk_limit, bridge_state),
            "requests": {"count": len(req_rows), "requests": req_rows},
            "appointments": {"appointments": [
                {
                    "appointmentId": a.get("appointmentId"),
                    "bridgeId": a.get("bridgeId"),
                    "donorName": a.get("donorName"),
                    "donorPhone": a.get("donorPhone"),
                    "patientName": a.get("patientName"),
                    "bloodGroup": a.get("bloodGroup"),
                    "hospital": a.get("hospital"),
                    "city": a.get("city"),
                    "appointmentDate": a.get("appointmentDate"),
                    "appointmentTime": a.get("appointmentTime"),
                    "status": a.get("status"),
                    "channel": a.get("channel"),
                    "createdAt": a.get("createdAt"),
                }
                for a in appts
            ]},
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/healthz")
def healthz():
    return {"ok": True, "stage": config.STAGE, "region": config.AWS_REGION}


@app.get("/stats")
def stats():
    donors, patients = load_frames()
    return _stats_payload(donors, patients, load_bridge_state())


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
    return _unbridged_payload(patients, load_edges(), load_bridge_state())


@app.get("/bridges")
def bridges():
    _, patients = load_frames()
    return _bridges_payload(load_bridge_state(), patients)


@app.get("/bridges/{bridge_id}")
def bridge_detail(bridge_id: str):
    state = load_bridge_state()
    b = copy.deepcopy(state.get(bridge_id))
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


@app.get("/calendar/patients")
def calendar_patients(q: str = ""):
    """Bridged patients for the calendar tab — optional name/group/city search."""
    _, patients = load_frames()
    state = committed_bridges(load_bridge_state())
    return calendar_plan.list_bridged_patients(patients, state, q=q)


@app.get("/calendar/patient/{patient_id}")
def calendar_patient(patient_id: str):
    """Transfusion cycle calendar with active + backup donor coverage."""
    donors, patients = load_frames()
    state = load_bridge_state()
    result = calendar_plan.build_patient_calendar(patient_id, patients, donors, state)
    if result.get("error") == "patient not found":
        raise HTTPException(status_code=404, detail="patient not found")
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.get("/at-risk")
def at_risk(limit: int = 15):
    _, patients = load_frames()
    return _at_risk_payload(patients, load_edges(), limit, load_bridge_state())


@app.get("/candidates/{patient_id}")
def candidates(patient_id: str, limit: int = 20):
    donors, patients = load_frames()
    pat = get_patient(patients, patient_id)
    if pat is None:
        raise HTTPException(status_code=404, detail="patient not found")
    try:
        cands = _ranked_candidates(patient_id, donors, load_edges(), limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"candidate ranking failed: {exc}") from exc
    return {
        "patientId": patient_id,
        "patientName": pat.get("name"),
        "count": len(cands),
        "candidates": cands,
    }


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
            load_bridge_state(),
            limit=body.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


handler = Mangum(app, api_gateway_base_path=f"/{config.STAGE}")
