"""Lambda: book-appointment  (SFN task: BookAppointment)

Creates an appointment for the confirmed donor on a request and updates the
request's assignedDonors + status. Also callable as a tool by the Bedrock agent.

Input: {"requestId", optional "donorId", "date", "time"}
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import dynamodb_client as db, scheduler  # noqa: E402


def _confirmed_donor_id(req: dict, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    for a in req.get("assignedDonors", []):
        if a.get("status") == "confirmed":
            return a.get("donorId")
    queue = req.get("rankedDonorQueue") or []
    idx = req.get("currentDonorIndex", 0)
    return queue[idx] if idx < len(queue) else None


def handler(event, context=None):
    request_id = event.get("requestId")
    req = db.get_request(request_id) if request_id else None
    if not req:
        return {"status": "error", "reason": "request_not_found"}

    donor_id = _confirmed_donor_id(req, event.get("donorId"))
    donor = db.get_donor(donor_id) if donor_id else {}
    donor = donor or {}

    appt_date = event.get("date") or req.get("requiredBy") or \
        scheduler.in_days(1).strftime("%Y-%m-%d")
    appt_time = event.get("time") or "10:00 AM"

    appt = {
        "appointmentId": db.new_id(),
        "donorId": donor_id,
        "patientId": req.get("patientId"),
        "requestId": request_id,
        "donorPhone": donor.get("phone"),
        "patientPhone": req.get("patientPhone"),
        "donorName": donor.get("name"),
        "patientName": req.get("patientName"),
        "hospital": req.get("hospital"),
        "city": req.get("city"),
        "appointmentDate": appt_date,
        "appointmentTime": appt_time,
        "bloodGroup": donor.get("bloodGroup"),
        "status": "scheduled",
        "donorConfirmedDayBefore": False,
        "channel": "whatsapp",
        "createdAt": db.now_iso(),
    }
    db.save_appointment(appt)

    for a in req.get("assignedDonors", []):
        if a.get("donorId") == donor_id:
            a["status"] = "appointment_booked"
            a["appointmentId"] = appt["appointmentId"]
            break
    else:
        req.setdefault("assignedDonors", []).append(
            {"donorId": donor_id, "appointmentId": appt["appointmentId"],
             "status": "appointment_booked"})
    req["status"] = "confirmed"
    db.save_request(req)

    return {"status": "confirmed", "appointmentId": appt["appointmentId"],
            "requestId": request_id, "donorId": donor_id, "appointment": appt}
