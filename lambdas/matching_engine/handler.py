"""MatchingEngine Lambda: ranked, medically-valid donor candidates for a patient.

Consumed by the OutreachStateMachine. Compatibility (ABO/Rh) and cooldown
eligibility are hard gates inside the Blood Graph, so every returned candidate
is valid to contact right now. The first ``BRIDGE_ACTIVE_TARGET`` are hinted as
"active", the rest as "buffer".

Event:
    {
      "patientId": "<id>",
      "excludeDonorIds": ["..."],   # optional: already in bridge / declined
      "limit": 20                    # optional, default 20
    }
Returns:
    {
      "patientId": "...", "bloodGroup": "O+",
      "count": 12,
      "candidates": [
        {"donorId": "...", "score": 0.87, "group": "O+", "slotTypeHint": "active"},
        ...
      ]
    }
"""
from __future__ import annotations

from raktsetu import config
from raktsetu.bridge import _ranked_for_patient

from .._common import get_patient, load_frames


def handler(event, context=None):
    patient_id = event.get("patientId") or event.get("patient_id")
    if not patient_id:
        return {"error": "patientId required"}

    exclude = set(event.get("excludeDonorIds") or [])
    limit = int(event.get("limit") or 20)

    donors, patients = load_frames()
    patient = get_patient(patients, patient_id)
    if patient is None:
        return {"error": f"patient {patient_id} not found", "candidates": []}

    ranked = _ranked_for_patient(patient, donors)
    if not ranked.empty and exclude:
        ranked = ranked[~ranked["donor_id"].isin(exclude)]

    candidates = []
    for i, row in enumerate(ranked.head(limit).itertuples(index=False)):
        candidates.append({
            "donorId": row.donor_id,
            "score": round(float(row.score), 4),
            "group": getattr(row, "donor_group", None),
            "slotTypeHint": "active" if i < config.BRIDGE_ACTIVE_TARGET else "buffer",
        })

    return {
        "patientId": patient_id,
        "bloodGroup": patient.get("bridge_blood_group_norm") or patient.get("blood_group_norm"),
        "count": len(candidates),
        "candidates": candidates,
    }
