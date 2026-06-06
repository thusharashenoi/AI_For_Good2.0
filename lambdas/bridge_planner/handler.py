"""BridgePlanner Lambda (EventBridge-scheduled): keep every patient covered.

On each run it:
  1. finds patients with NO bridge and drafts a 10-donor bridge (6 active + 4
     buffer) from the Blood Graph;
  2. finds EXISTING bridges below buffer target and drafts replacements,
     preferring the flexible one-time/emergency pool;
  3. persists META + VACANT slots (with a ranked fallback queue) to the Bridges
     table;
  4. optionally kicks the OutreachStateMachine once per vacant slot to mobilize
     the proposed donor (only if OUTREACH_STATE_MACHINE_ARN is set).

Event (all optional):
    {"dryRun": true, "maxPatients": 50, "mobilize": true}
"""
from __future__ import annotations

import os

from raktsetu import config, store
from raktsetu.bridge import buffer_backfill, formation_queue

from .._common import load_frames, state_machine_arn


def _existing_bridge_state() -> dict[str, dict]:
    """bridgeId -> {patientId, donor_ids:set, buffer_count, active_count}."""
    state: dict[str, dict] = {}
    for it in store.scan_bridges():
        bid = it.get("bridgeId")
        if not bid:
            continue
        s = state.setdefault(bid, {"patientId": None, "donor_ids": set(),
                                   "active": 0, "buffer": 0})
        if it.get("SK") == "META":
            s["patientId"] = it.get("patientId")
        elif str(it.get("SK", "")).startswith("SLOT#"):
            if it.get("donorId"):
                s["donor_ids"].add(it["donorId"])
            if it.get("status") in ("CONFIRMED", "PENDING"):
                if it.get("slotType") == "buffer":
                    s["buffer"] += 1
                else:
                    s["active"] += 1
    return state


def _persist_plan(plan, status_for_meta: str) -> int:
    store.put_bridge_meta(plan.bridge_id, plan.patient_id,
                          status=status_for_meta, bloodGroup=plan.blood_group,
                          coverage=plan.coverage)
    vacant = 0
    for slot in plan.slots:
        store.put_slot(plan.bridge_id, slot.slot_id, slot.slot_type, "VACANT",
                       donor_id=slot.donor_id, score=slot.score,
                       reason=slot.reason, candidate_queue=plan.candidate_queue,
                       patient_id=plan.patient_id, backup_for=slot.backup_for)
        vacant += 1
    return vacant


def _mobilize(plan, sm_arn: str) -> int:
    started = 0
    for slot in plan.slots:
        if not slot.donor_id:
            continue
        store.start_outreach(sm_arn, {
            "patientId": plan.patient_id, "bridgeId": plan.bridge_id,
            "slotId": slot.slot_id, "slotType": slot.slot_type,
            "donorId": slot.donor_id, "candidateQueue": plan.candidate_queue,
        })
        started += 1
    return started


def handler(event=None, context=None):
    event = event or {}
    dry_run = bool(event.get("dryRun", False))
    mobilize = bool(event.get("mobilize", os.environ.get("PLANNER_MOBILIZE") == "1"))
    max_patients = int(event.get("maxPatients") or 100)

    donors, patients = load_frames(force=True)
    sm_arn = state_machine_arn()
    existing = _existing_bridge_state()

    summary = {"new_bridges": 0, "backfilled_bridges": 0, "vacant_slots": 0,
               "executions_started": 0, "dryRun": dry_run, "details": []}

    # 1) Unbridged patients -> new bridges.
    unbridged = patients[patients["bridge_id"].isna()].head(max_patients)
    for _, pat in unbridged.iterrows():
        plan = formation_queue(pat, donors)
        if not plan.slots:
            summary["details"].append({"patientId": pat["user_id"], "action": "no_candidates"})
            continue
        summary["new_bridges"] += 1
        summary["details"].append({
            "patientId": pat["user_id"], "bridgeId": plan.bridge_id,
            "action": "form_bridge", "slots": len(plan.slots),
            "coverage": plan.coverage, "queue": len(plan.candidate_queue),
        })
        if not dry_run:
            summary["vacant_slots"] += _persist_plan(plan, "FORMING")
            if mobilize and sm_arn:
                summary["executions_started"] += _mobilize(plan, sm_arn)

    # 2) Existing bridges below buffer target -> backfill.
    for bid, s in existing.items():
        if s["patientId"] is None:
            continue
        vacant_buffer = config.BRIDGE_BUFFER_TARGET - s["buffer"]
        if vacant_buffer <= 0:
            continue
        pat = patients[patients["user_id"] == s["patientId"]]
        if pat.empty:
            continue
        plan = buffer_backfill(pat.iloc[0], donors, bid, s["donor_ids"], vacant_buffer)
        if not plan.slots:
            continue
        summary["backfilled_bridges"] += 1
        summary["details"].append({
            "bridgeId": bid, "patientId": s["patientId"],
            "action": "backfill_buffer", "added": len(plan.slots),
            "vacant_buffer": vacant_buffer,
        })
        if not dry_run:
            for slot in plan.slots:
                store.put_slot(bid, slot.slot_id, slot.slot_type, "VACANT",
                               donor_id=slot.donor_id, score=slot.score,
                               reason=slot.reason, candidate_queue=plan.candidate_queue,
                               patient_id=s["patientId"])
                summary["vacant_slots"] += 1
            if mobilize and sm_arn:
                summary["executions_started"] += _mobilize(plan, sm_arn)

    return summary
