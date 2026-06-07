"""Bridge calendar — transfusion cycles with active + backup donor coverage."""
from __future__ import annotations

from typing import Any

import pandas as pd

from . import config
from .ml.forecast import forecast

BACKUP_RELIABILITY_THRESHOLD = 0.90


def _safe_num(v, default=0.0):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def donor_reliability(drow: pd.Series) -> float:
    if "reliability_score" in drow.index and pd.notna(drow.get("reliability_score")):
        return round(_safe_num(drow["reliability_score"], 0.5), 3)
    show = _safe_num(drow.get("show_rate"), 0.8)
    will = _safe_num(drow.get("willingness"), 0.5)
    return round(0.55 * show + 0.45 * will, 3)


def _donor_payload(did: str, dmeta: pd.DataFrame, slot: dict | None = None) -> dict[str, Any]:
    row = dmeta.loc[did] if did in dmeta.index else pd.Series(dtype=object)
    rel = donor_reliability(row) if did in dmeta.index else 0.5
    return {
        "donorId": did,
        "name": row.get("name") if did in dmeta.index else None,
        "city": row.get("city") if did in dmeta.index else None,
        "bloodGroup": row.get("blood_group_norm") if did in dmeta.index else None,
        "reliability": rel,
        "showRate": round(_safe_num(row.get("show_rate"), 0.8), 3) if did in dmeta.index else None,
        "willingness": round(_safe_num(row.get("willingness"), 0.5), 3) if did in dmeta.index else None,
        "slotId": (slot or {}).get("slotId"),
        "slotType": (slot or {}).get("slotType"),
        "status": (slot or {}).get("status"),
        "score": _safe_num((slot or {}).get("score"), None),
        "reason": (slot or {}).get("reason"),
    }


def _units_per_cycle(pat: pd.Series) -> int:
    try:
        q = int(pat.get("quantity_required") or 1)
    except (TypeError, ValueError):
        q = 1
    return max(q, 1)


def _fmt_date(ts) -> str | None:
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _bridge_for_patient(patient_id: str, bridge_state: dict[str, dict],
                        pat: pd.Series) -> tuple[str | None, dict | None]:
    bid = pat.get("bridge_id")
    if bid is not None and not pd.isna(bid) and str(bid) in bridge_state:
        return str(bid), bridge_state[str(bid)]
    for bid, b in bridge_state.items():
        if b.get("patientId") == patient_id:
            return bid, b
    return None, None


def _requires_backup(primary: dict[str, Any]) -> bool:
    return _safe_num(primary.get("reliability"), 0.5) < BACKUP_RELIABILITY_THRESHOLD


def _slot_coverage(bridge: dict, dmeta: pd.DataFrame) -> list[dict[str, Any]]:
    slots = [
        s for s in bridge.get("slots", [])
        if s.get("donorId") and s.get("status") in ("CONFIRMED", "PENDING")
    ]
    actives = [s for s in slots if (s.get("slotType") or "active") == "active"]
    actives.sort(key=lambda s: s.get("slotId") or "")
    buffer_slots = [s for s in slots if s.get("slotType") == "buffer"]
    backups_by_slot = {
        s.get("backupFor"): s
        for s in buffer_slots
        if s.get("backupFor")
    }
    general_buffers = [s for s in buffer_slots if not s.get("backupFor")]

    coverage: list[dict[str, Any]] = []
    used_backup_ids: set[str] = set()

    for slot in actives:
        did = slot["donorId"]
        primary = _donor_payload(did, dmeta, slot)
        backup_slot = backups_by_slot.get(slot.get("slotId"))
        backup_payload = None
        backup_reason: str | None = None
        missing_backup = False

        if backup_slot:
            backup_payload = _donor_payload(backup_slot["donorId"], dmeta, backup_slot)
            used_backup_ids.add(backup_slot["donorId"])
            needs_backup = True
        elif not _requires_backup(primary):
            needs_backup = False
            backup_reason = "trusted_high_reliability"
        else:
            needs_backup = True
            missing_backup = True

        coverage.append({
            "slotId": slot.get("slotId"),
            "primary": primary,
            "backup": backup_payload,
            "needsBackup": needs_backup,
            "backupReason": backup_reason,
            "missingBackup": missing_backup,
        })

    # Assign shared buffer donors to active slots that still need one (<90% reliability).
    needy = [row for row in coverage if row["needsBackup"] and row["backup"] is None]
    needy.sort(key=lambda row: _safe_num(row["primary"].get("reliability"), 0.5))
    spare_buffers = [
        s for s in general_buffers
        if s.get("donorId") and s["donorId"] not in used_backup_ids
    ]
    for row, buffer_slot in zip(needy, spare_buffers):
        backup_payload = _donor_payload(buffer_slot["donorId"], dmeta, buffer_slot)
        row["backup"] = backup_payload
        row["missingBackup"] = False
        row["backupReason"] = "reliability_below_threshold"
        used_backup_ids.add(buffer_slot["donorId"])

    return coverage


def _assigned_for_cycle(
    coverage: list[dict[str, Any]],
    cycle_offset: int,
    units: int,
) -> list[dict[str, Any]]:
    """Pick the active rotation slot(s) scheduled for one transfusion cycle."""
    if not coverage:
        return []
    n = len(coverage)
    units = max(1, min(int(units or 1), n))
    start = cycle_offset % n
    out: list[dict[str, Any]] = []
    for i in range(units):
        idx = (start + i) % n
        row = dict(coverage[idx])
        row["rotationIndex"] = idx
        row["unitNumber"] = i + 1
        out.append(row)
    return out


def _bridge_has_commitment(bridge: dict) -> bool:
    for s in bridge.get("slots", []):
        if s.get("donorId") and s.get("status") in ("CONFIRMED", "PENDING"):
            return True
    return False


def list_bridged_patients(
    patients: pd.DataFrame,
    bridge_state: dict[str, dict],
    *,
    q: str = "",
) -> dict[str, Any]:
    """Summaries for bridged patients (searchable list)."""
    patients_fc = forecast(patients)
    fc_by_id = patients_fc.set_index("user_id")
    q_lower = (q or "").strip().lower()
    rows = []
    seen: set[str] = set()

    for bid, bridge in bridge_state.items():
        if not _bridge_has_commitment(bridge):
            continue
        pid = bridge.get("patientId")
        if not pid or pid in seen:
            continue
        pat = patients[patients["user_id"] == pid]
        if pat.empty:
            continue
        pat = pat.iloc[0]
        pat_bid = pat.get("bridge_id")
        if pat_bid is not None and not pd.isna(pat_bid) and str(pat_bid) != bid:
            if str(pat_bid) in bridge_state and _bridge_has_commitment(bridge_state[str(pat_bid)]):
                continue
        seen.add(pid)

        fc = fc_by_id.loc[pid] if pid in fc_by_id.index else pat
        name = pat.get("name") or pid[:8]
        blood = pat.get("bridge_blood_group_norm") or pat.get("blood_group_norm")
        city = pat.get("city")
        hay = " ".join(str(x or "") for x in (name, blood, city, bid, pid)).lower()
        if q_lower and q_lower not in hay:
            continue

        active = int(bridge.get("active", 0))
        buffer_n = int(bridge.get("buffer", 0))
        rows.append({
            "patientId": pid,
            "patientName": name,
            "bloodGroup": None if pd.isna(blood) else str(blood),
            "city": None if pd.isna(city) else str(city),
            "bridgeId": bid,
            "bridgeStatus": bridge.get("status"),
            "activeDonors": active,
            "bufferDonors": buffer_n,
            "vacantSlots": int(bridge.get("vacant", 0)),
            "target": config.BRIDGE_SIZE,
            "unitsPerCycle": _units_per_cycle(pat),
            "cadenceDays": int(_safe_num(fc.get("cadence_days"), 21)),
            "nextTransfusionDate": _fmt_date(fc.get("predicted_next_transfusion")),
            "daysUntilTransfusion": int(_safe_num(fc.get("days_until_transfusion"), 0)),
        })

    rows.sort(key=lambda r: (r.get("daysUntilTransfusion") or 999, r.get("patientName") or ""))
    return {"count": len(rows), "patients": rows}


def _parse_date(s: str | None) -> pd.Timestamp | None:
    if not s:
        return None
    try:
        return pd.Timestamp(s)
    except Exception:
        return None


def _month_label(year: int, month: int) -> str:
    import calendar as cal
    return f"{cal.month_name[month]} {year}"


def _build_month_view(cycles: list[dict[str, Any]],
                      *,
                      anchor: pd.Timestamp | None = None) -> list[dict[str, Any]]:
    """Group cycles into month grids with per-day coverage windows."""
    anchor = anchor or pd.Timestamp(config.REFERENCE_DATE)
    month_keys: set[str] = set()
    day_events: dict[str, list[dict[str, Any]]] = {}

    for cycle in cycles:
        start = _parse_date(cycle.get("windowStart"))
        end = _parse_date(cycle.get("transfusionDate")) or _parse_date(cycle.get("windowEnd"))
        if start is None or end is None:
            continue
        cur = start.normalize()
        end_d = end.normalize()
        while cur <= end_d:
            key = cur.strftime("%Y-%m-%d")
            month_keys.add(cur.strftime("%Y-%m"))
            day_events.setdefault(key, []).append({
                "cycleOffset": cycle.get("offset"),
                "label": cycle.get("label"),
                "isCurrent": cycle.get("isCurrent"),
                "isPast": cycle.get("isPast"),
                "isTransfusionDay": cur == end_d,
                "windowStart": cycle.get("windowStart"),
                "windowEnd": cycle.get("windowEnd"),
                "transfusionDate": cycle.get("transfusionDate"),
                "unitsRequired": cycle.get("unitsRequired"),
                "assigned": cycle.get("assigned") or [],
            })
            cur += pd.Timedelta(days=1)

    # Always include anchor month even if empty.
    month_keys.add(anchor.strftime("%Y-%m"))
    # Extend one month before earliest and two after latest for context.
    if month_keys:
        sorted_keys = sorted(month_keys)
        first = pd.Timestamp(sorted_keys[0] + "-01")
        last = pd.Timestamp(sorted_keys[-1] + "-01")
        month_keys.add((first - pd.offsets.MonthBegin(1)).strftime("%Y-%m"))
        month_keys.add((last + pd.offsets.MonthBegin(1)).strftime("%Y-%m"))
        month_keys.add((last + pd.offsets.MonthBegin(2)).strftime("%Y-%m"))

    months_out = []
    for mk in sorted(month_keys):
        year, month = int(mk[:4]), int(mk[5:7])
        first_of_month = pd.Timestamp(year=year, month=month, day=1)
        start_weekday = first_of_month.weekday()  # Mon=0
        # Calendar grid starting Monday.
        grid_start = first_of_month - pd.Timedelta(days=start_weekday)
        weeks = []
        cursor = grid_start
        for _ in range(6):
            week = []
            for _d in range(7):
                date_str = cursor.strftime("%Y-%m-%d")
                in_month = cursor.month == month
                events = day_events.get(date_str, []) if in_month else []
                week.append({
                    "date": date_str,
                    "day": int(cursor.day),
                    "inMonth": in_month,
                    "isToday": cursor.normalize() == anchor.normalize(),
                    "isTransfusionDay": any(e.get("isTransfusionDay") for e in events),
                    "hasCoverage": bool(events),
                    "events": events,
                })
                cursor += pd.Timedelta(days=1)
            weeks.append(week)
        months_out.append({
            "monthKey": mk,
            "label": _month_label(year, month),
            "year": year,
            "month": month,
            "isCurrentMonth": mk == anchor.strftime("%Y-%m"),
            "weeks": weeks,
        })
    return months_out


def build_patient_calendar(
    patient_id: str,
    patients: pd.DataFrame,
    donors: pd.DataFrame,
    bridge_state: dict[str, dict],
    *,
    past_cycles: int = 6,
    future_cycles: int = 12,
) -> dict[str, Any]:
    """Transfusion timeline with donor coverage per cycle."""
    pat_row = patients[patients["user_id"] == patient_id]
    if pat_row.empty:
        return {"error": "patient not found", "patientId": patient_id}

    pat = pat_row.iloc[0]
    bid, bridge = _bridge_for_patient(patient_id, bridge_state, pat)
    if not bridge:
        return {"error": "no committed bridge", "patientId": patient_id}

    patients_fc = forecast(patients)
    fc = patients_fc[patients_fc["user_id"] == patient_id].iloc[0]
    cadence = max(int(_safe_num(fc.get("cadence_days"), 21)), 7)
    next_dt = pd.Timestamp(fc.get("predicted_next_transfusion"))
    if pd.isna(next_dt):
        next_dt = pd.Timestamp(config.REFERENCE_DATE)

    dmeta = donors.drop_duplicates(subset=["user_id"], keep="first").set_index("user_id")
    coverage = _slot_coverage(bridge, dmeta)
    units = _units_per_cycle(pat)

    cycles = []
    for offset in range(-past_cycles, future_cycles + 1):
        cycle_date = next_dt + pd.Timedelta(days=offset * cadence)
        window_start = cycle_date - pd.Timedelta(days=max(cadence // 2, 3))
        label = "Current cycle" if offset == 0 else (
            f"Cycle +{offset}" if offset > 0 else f"Cycle {offset}"
        )
        assigned = _assigned_for_cycle(coverage, offset, units)
        cycles.append({
            "offset": offset,
            "label": label,
            "transfusionDate": _fmt_date(cycle_date),
            "windowStart": _fmt_date(window_start),
            "windowEnd": _fmt_date(cycle_date),
            "isCurrent": offset == 0,
            "isPast": offset < 0,
            "unitsRequired": units,
            "assigned": assigned,
        })

    blood = pat.get("bridge_blood_group_norm") or pat.get("blood_group_norm")
    anchor = pd.Timestamp(config.REFERENCE_DATE)
    months = _build_month_view(cycles, anchor=anchor)
    return {
        "patientId": patient_id,
        "patientName": pat.get("name"),
        "bloodGroup": None if pd.isna(blood) else str(blood),
        "city": None if pd.isna(pat.get("city")) else str(pat.get("city")),
        "bridgeId": bid,
        "bridgeStatus": bridge.get("status"),
        "activeDonors": int(bridge.get("active", 0)),
        "bufferDonors": int(bridge.get("buffer", 0)),
        "unitsPerCycle": _units_per_cycle(pat),
        "cadenceDays": cadence,
        "nextTransfusionDate": _fmt_date(next_dt),
        "daysUntilTransfusion": int(_safe_num(fc.get("days_until_transfusion"), 0)),
        "referenceDate": _fmt_date(anchor),
        "backupReliabilityThreshold": BACKUP_RELIABILITY_THRESHOLD,
        "cycles": cycles,
        "months": months,
    }
