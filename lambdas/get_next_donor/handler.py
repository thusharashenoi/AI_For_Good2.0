"""GetNextDonor Lambda: the single best donor to mobilize next for a slot.

Used by the OutreachStateMachine when a contacted donor declines or times out:
it returns the next-best replacement, skipping anyone already contacted,
declined, or confirmed in the bridge. Returns ``exhausted`` when the pool is dry
(the planner should then widen the graph / trigger recruitment).

Event:
    {
      "patientId": "<id>",            # or "bridgeId" to resolve patient via Bridges
      "contactedDonorIds": ["..."],   # declined / pending
      "excludeDonorIds": ["..."]      # confirmed members
    }
Returns:
    {"donorId": "...", "score": 0.81, "group": "O+"}  |  {"donorId": null, "exhausted": true}
"""
from __future__ import annotations

from raktsetu import store
from raktsetu.bridge import _ranked_for_patient, next_candidate

from .._common import get_patient, load_frames


def _patient_from_bridge(bridge_id: str) -> str | None:
    for it in store.get_bridge(bridge_id):
        if it.get("SK") == "META":
            return it.get("patientId")
    return None


def handler(event, context=None):
    patient_id = event.get("patientId") or event.get("patient_id")
    bridge_id = event.get("bridgeId") or event.get("bridge_id")
    if not patient_id and bridge_id:
        patient_id = _patient_from_bridge(bridge_id)
    if not patient_id:
        return {"error": "patientId or resolvable bridgeId required"}

    contacted = set(event.get("contactedDonorIds") or [])
    exclude = set(event.get("excludeDonorIds") or [])
    skip = contacted | exclude

    donors, patients = load_frames()
    patient = get_patient(patients, patient_id)
    if patient is None:
        return {"error": f"patient {patient_id} not found", "donorId": None, "exhausted": True}

    ranked = _ranked_for_patient(patient, donors)
    if ranked.empty:
        return {"donorId": None, "exhausted": True, "patientId": patient_id}

    queue = ranked["donor_id"].tolist()
    nxt = next_candidate(queue, skip)
    if nxt is None:
        return {"donorId": None, "exhausted": True, "patientId": patient_id}

    row = ranked[ranked["donor_id"] == nxt].iloc[0]
    return {
        "patientId": patient_id,
        "bridgeId": bridge_id,
        "donorId": nxt,
        "score": round(float(row["score"]), 4),
        "group": row.get("donor_group"),
        "exhausted": False,
    }
