"""Blood Bridge formation and buffer maintenance on top of the Blood Graph.

Reframes the graph output into the Blood Warriors operating model:
  - a bridge is 10 committed donors = 6 active + 4 buffer;
  - for patients WITHOUT a bridge, build a tentative bridge from the ranked pool;
  - for EXISTING bridges below target, propose buffer backfill, drawing
    preferentially from the one-time / emergency donor pool;
  - every proposed slot starts VACANT and is only CONFIRMED after the donor
    agrees (the outreach state machine owns that conversation).

These functions are pure (DataFrame in, plan out); persistence + mobilization
live in store.py and the Lambda handlers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import config
from .graph import candidate_edges


# Donor pools considered "flexible" supply for buffering existing bridges.
ONE_TIME_POOL_ROLES = {"Emergency Donor", "Volunteer"}
ONE_TIME_POOL_TYPES = {"One-Time Donor", "Other"}

# Backup policy thresholds.
HIGH_DONATIONS = 10   # "high donations" => proven reliable if no no-shows
NEW_DONOR_DONATIONS = 2  # < this (or no history) => treat as new, back up by default


def needs_backup(donor) -> tuple[bool, str]:
    """Decide whether an assigned donor needs a standby backup.

    Rules (per the product spec):
      - new donor (few/no donations or no donation history) -> backup by default
      - high donations + zero no-shows                      -> NO backup (trusted)
      - high donations + some no-shows                      -> backup
      - everyone in between                                 -> backup (cautious)
    Returns (needs_backup, reason).
    """
    don = donor.get("donations_till_date")
    try:
        don = float(don)
    except (TypeError, ValueError):
        don = 0.0
    no_shows = donor.get("no_shows")
    try:
        no_shows = float(no_shows) if no_shows is not None and not pd.isna(no_shows) else 0.0
    except (TypeError, ValueError):
        no_shows = 0.0
    has_history = pd.notna(donor.get("last_donation_date")) or don >= 1

    if don < NEW_DONOR_DONATIONS or not has_history:
        return True, "new donor (default backup)"
    if don >= HIGH_DONATIONS and no_shows == 0:
        return False, "trusted (high donations, no no-shows)"
    if don >= HIGH_DONATIONS and no_shows > 0:
        return True, f"reliable but {int(no_shows)} no-show(s)"
    return True, "establishing reliability"


@dataclass
class SlotPlan:
    slot_id: str
    slot_type: str          # "active" | "buffer"
    donor_id: str | None
    score: float
    reason: str
    status: str = "VACANT"
    backup_for: str | None = None  # for buffer slots: which active slot they cover


@dataclass
class BridgePlan:
    patient_id: str
    bridge_id: str
    blood_group: str | None
    slots: list[SlotPlan] = field(default_factory=list)
    candidate_queue: list[str] = field(default_factory=list)  # ranked fallbacks
    coverage: int = 0       # eligible compatible donors available

    def as_dict(self) -> dict:
        return {
            "patient_id": self.patient_id,
            "bridge_id": self.bridge_id,
            "blood_group": self.blood_group,
            "coverage": self.coverage,
            "candidate_queue": self.candidate_queue,
            "slots": [s.__dict__ for s in self.slots],
        }


def _reason(row) -> str:
    bits = []
    if row.get("donor_group"):
        bits.append(str(row["donor_group"]))
    if pd.notna(row.get("score")):
        bits.append(f"match {row['score']:.2f}")
    return " | ".join(bits)


def _ranked_for_patient(patient: pd.Series, donors: pd.DataFrame) -> pd.DataFrame:
    """All eligible compatible donors for one patient, best first.

    Hard gates (ABO/Rh compatibility + cooldown eligibility) are enforced inside
    candidate_edges, so anything returned here is medically valid + available.
    """
    pat_df = patient.to_frame().T
    edges = candidate_edges(donors, pat_df)
    if edges.empty:
        return edges
    return edges.sort_values("score", ascending=False).reset_index(drop=True)


def formation_queue(patient: pd.Series, donors: pd.DataFrame,
                    bridge_id: str | None = None) -> BridgePlan:
    """Build a tentative bridge for an unbridged patient with *dynamic* backups.

    Picks the top ``BRIDGE_ACTIVE_TARGET`` donors as the active rotation, then
    adds a backup (buffer) slot ONLY for active donors flagged by ``needs_backup``
    (new donors, or proven donors with a no-show history). Trusted high-volume
    donors with a clean record get no backup, so bridge size adapts to reliability
    instead of always reserving a flat 4.
    """
    bid = bridge_id or f"BR-{patient['user_id'][:10]}"
    pgroup = patient.get("bridge_blood_group_norm") or patient.get("blood_group_norm")
    ranked = _ranked_for_patient(patient, donors)

    plan = BridgePlan(patient_id=patient["user_id"], bridge_id=bid,
                      blood_group=pgroup, coverage=len(ranked))
    if ranked.empty:
        return plan

    meta = donors.set_index("user_id")
    active = ranked.head(config.BRIDGE_ACTIVE_TARGET)
    used = set(active["donor_id"])
    backup_pool = ranked.iloc[config.BRIDGE_ACTIVE_TARGET:].reset_index(drop=True)
    bp_idx = 0
    buffer_n = 0

    for i, row in enumerate(active.itertuples(index=False)):
        slot_id = f"{i + 1:02d}"
        donor = meta.loc[row.donor_id] if row.donor_id in meta.index else pd.Series(dtype=object)
        plan.slots.append(SlotPlan(
            slot_id=slot_id, slot_type="active", donor_id=row.donor_id,
            score=float(row.score), reason=_reason(row._asdict()),
        ))
        need, why = needs_backup(donor)
        if not need:
            continue
        # Attach the next-best distinct donor as this slot's backup.
        while bp_idx < len(backup_pool) and backup_pool.loc[bp_idx, "donor_id"] in used:
            bp_idx += 1
        if bp_idx < len(backup_pool):
            brow = backup_pool.loc[bp_idx]
            used.add(brow["donor_id"])
            buffer_n += 1
            plan.slots.append(SlotPlan(
                slot_id=f"B{buffer_n:02d}", slot_type="buffer", donor_id=brow["donor_id"],
                score=float(brow["score"]), reason=f"backup for {slot_id}: {why}",
                backup_for=slot_id,
            ))
            bp_idx += 1

    # Remaining ranked donors are the fallback queue used on decline/timeout.
    plan.candidate_queue = [d for d in ranked["donor_id"].tolist() if d not in used]
    return plan


def buffer_backfill(patient: pd.Series, donors: pd.DataFrame, bridge_id: str,
                    current_donor_ids: set[str], vacant_buffer: int) -> BridgePlan:
    """Propose buffer replacements for an existing bridge below target.

    Pulls preferentially from the one-time / emergency donor pool so the
    committed active core is preserved while standby depth is restored.
    """
    pgroup = patient.get("bridge_blood_group_norm") or patient.get("blood_group_norm")
    ranked = _ranked_for_patient(patient, donors)
    plan = BridgePlan(patient_id=patient["user_id"], bridge_id=bridge_id,
                      blood_group=pgroup, coverage=len(ranked))
    if ranked.empty or vacant_buffer <= 0:
        return plan

    # Exclude donors already in the bridge.
    avail = ranked[~ranked["donor_id"].isin(current_donor_ids)].copy()
    if avail.empty:
        return plan

    # Prefer the flexible pool for buffer slots; fall back to anyone eligible.
    donor_meta = donors.set_index("user_id")
    def _is_flexible(did):
        if did not in donor_meta.index:
            return False
        r = donor_meta.loc[did]
        return (r.get("role") in ONE_TIME_POOL_ROLES) or (r.get("donor_type") in ONE_TIME_POOL_TYPES)

    avail["flexible"] = avail["donor_id"].map(_is_flexible)
    avail = avail.sort_values(["flexible", "score"], ascending=[False, False])

    picks = avail.head(vacant_buffer)
    for i, row in enumerate(picks.itertuples(index=False)):
        plan.slots.append(SlotPlan(
            slot_id=f"B{i + 1:02d}", slot_type="buffer", donor_id=row.donor_id,
            score=float(row.score),
            reason=_reason(row._asdict()) + (" | flexible pool" if getattr(row, "flexible", False) else ""),
        ))
    plan.candidate_queue = avail["donor_id"].iloc[vacant_buffer:].tolist()
    return plan


def next_candidate(candidate_queue: list[str], contacted: set[str]) -> str | None:
    """Return the next donor to mobilize, skipping already-contacted/declined."""
    for did in candidate_queue:
        if did not in contacted:
            return did
    return None
