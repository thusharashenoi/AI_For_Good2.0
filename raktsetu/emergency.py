"""Ad-hoc emergency patient matching (outside the Blood Warriors registry).

Coordinators can register a one-off request for a patient not in the system.
We rank eligible compatible donors by the full gate + score pipeline (including
willingness), and for donors already committed to a blood bridge we propose a
replacement from the graph so their bridge slot can be backfilled.
"""
from __future__ import annotations

import hashlib
from typing import Any

import pandas as pd

from . import synth
from .bridge import _ranked_for_patient
from .compatibility import normalize_group


def _request_id(name: str, blood_group: str, city: str) -> str:
    raw = f"emergency|{name.strip().lower()}|{normalize_group(blood_group)}|{city.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _donor_bridge_map(bridge_state: dict[str, dict]) -> dict[str, dict]:
    """donor_id -> {bridgeId, patientId, slotType, slotId}."""
    out: dict[str, dict] = {}
    for bid, b in bridge_state.items():
        pid = b.get("patientId")
        for s in b.get("slots", []):
            did = s.get("donorId")
            if not did or s.get("status") not in ("CONFIRMED", "PENDING"):
                continue
            out[str(did)] = {
                "bridgeId": bid,
                "patientId": pid,
                "slotType": s.get("slotType"),
                "slotId": s.get("slotId"),
            }
    return out


def _replacement_for_bridge(
    pulled_donor_id: str,
    bridge_info: dict,
    donors: pd.DataFrame,
    patients: pd.DataFrame,
    bridge_member_ids: set[str],
) -> dict[str, Any] | None:
    pid = bridge_info.get("patientId")
    if not pid:
        return None
    pat = patients[patients["user_id"] == pid]
    if pat.empty:
        return None
    ranked = _ranked_for_patient(pat.iloc[0], donors)
    if ranked.empty:
        return None
    exclude = bridge_member_ids | {pulled_donor_id}
    sub = ranked[~ranked["donor_id"].isin(exclude)]
    if sub.empty:
        return None
    row = sub.iloc[0]
    dmeta = donors.set_index("user_id")
    drow = dmeta.loc[row.donor_id] if row.donor_id in dmeta.index else None
    pname = patients.set_index("user_id")["name"].to_dict() if "name" in patients.columns else {}
    return {
        "donorId": row.donor_id,
        "name": drow.get("name") if drow is not None else None,
        "group": row.donor_group,
        "score": round(float(row.score), 4),
        "distanceKm": round(float(row.distance_km), 1) if pd.notna(row.get("distance_km")) else None,
        "bridgePatientId": pid,
        "bridgePatientName": pname.get(pid),
        "fillsSlot": bridge_info.get("slotId"),
        "slotType": bridge_info.get("slotType"),
    }


def build_emergency_patient(name: str, blood_group: str, city: str) -> pd.Series:
    bg = normalize_group(blood_group)
    if not bg:
        raise ValueError(f"Unknown blood group: {blood_group}")
    lat, lon = synth.coords_for_city(city)
    rid = _request_id(name, bg, city)
    return pd.Series({
        "user_id": rid,
        "name": name.strip(),
        "blood_group_norm": bg,
        "bridge_blood_group_norm": bg,
        "city": city.strip(),
        "latitude": lat,
        "longitude": lon,
    })


def match_emergency(
    name: str,
    blood_group: str,
    city: str,
    donors: pd.DataFrame,
    patients: pd.DataFrame,
    bridge_state: dict[str, dict],
    limit: int = 15,
) -> dict[str, Any]:
    """Rank donors for an ad-hoc emergency patient; include bridge backfill hints."""
    patient = build_emergency_patient(name, blood_group, city)
    ranked = _ranked_for_patient(patient, donors)
    bridge_map = _donor_bridge_map(bridge_state)
    dmeta = donors.set_index("user_id")
    pname = patients.set_index("user_id")["name"].to_dict() if "name" in patients.columns else {}

    bridge_members_by_bridge: dict[str, set[str]] = {}
    for did, info in bridge_map.items():
        bid = info["bridgeId"]
        bridge_members_by_bridge.setdefault(bid, set()).add(did)

    rows: list[dict] = []
    for row in ranked.head(limit).itertuples(index=False):
        did = row.donor_id
        drow = dmeta.loc[did] if did in dmeta.index else None
        binfo = bridge_map.get(str(did))
        replacement = None
        if binfo:
            members = bridge_members_by_bridge.get(binfo["bridgeId"], set())
            replacement = _replacement_for_bridge(did, binfo, donors, patients, members)
        rows.append({
            "donorId": did,
            "name": drow.get("name") if drow is not None else None,
            "group": row.donor_group,
            "score": round(float(row.score), 4),
            "distanceKm": round(float(row.distance_km), 1) if pd.notna(getattr(row, "distance_km", float("nan"))) else None,
            "showRate": round(float(drow.get("show_rate", 0.8)), 3) if drow is not None else 0.8,
            "willingness": round(float(drow.get("willingness", 0.5)), 3) if drow is not None else 0.5,
            "city": drow.get("city") if drow is not None else None,
            "inBridge": binfo is not None,
            "bridgeId": binfo.get("bridgeId") if binfo else None,
            "bridgePatientName": pname.get(binfo.get("patientId")) if binfo else None,
            "bridgeSlotType": binfo.get("slotType") if binfo else None,
            "replacement": replacement,
        })

    return {
        "request": {
            "requestId": patient["user_id"],
            "patientName": patient["name"],
            "bloodGroup": patient["blood_group_norm"],
            "city": patient["city"],
        },
        "count": len(rows),
        "poolSize": len(ranked),
        "candidates": rows,
    }
