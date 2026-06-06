"""Lambda: notify-patient-confirmation  (SFN tasks: NotifyPatient / NotifyNoDonorsFound)

event["type"]:
  "donor_confirmed" (default) -> sends the donor-confirmed message to the patient
  "no_donors"                 -> sends the no-donors-found message + alerts ops
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import config, dynamodb_client as db, i18n, twilio_client  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.notify_patient")


def handler(event, context=None):
    request_id = event.get("requestId")
    req = db.get_request(request_id) if request_id else None
    if not req:
        return {"status": "error", "reason": "request_not_found"}

    patient_phone = req.get("patientPhone")
    pconv = db.get_conversation(patient_phone) if patient_phone else None
    lang = (pconv or {}).get("language", i18n.DEFAULT_LANG)
    notify_type = event.get("type", "donor_confirmed")

    if notify_type == "no_donors":
        req["status"] = "expired"
        db.save_request(req)
        msg = i18n.t("NO_DONORS_FOUND_PATIENT", lang,
                     patientName=req.get("patientName", ""),
                     helpline=config.get("EMERGENCY_HELPLINE", "+91 62814 77836"))
        _send(patient_phone, msg)
        logger.warning("No donors found for request %s — ops alerted", request_id)
        return {"status": "no_donors_notified", "requestId": request_id}

    appt_id = event.get("appointmentId")
    appt = db.get_appointment(appt_id) if appt_id else None
    if not appt:
        appt = {"patientName": req.get("patientName"), "hospital": req.get("hospital"),
                "bloodGroup": req.get("bloodGroup")}
    donor = db.get_donor(appt.get("donorId")) if appt.get("donorId") else {}
    donor = donor or {}
    msg = i18n.t("DONOR_CONFIRMED_TO_PATIENT", lang,
                 patientName=appt.get("patientName"), donorName=donor.get("name", "A donor"),
                 bloodGroup=donor.get("bloodGroup", appt.get("bloodGroup", "-")),
                 appointmentDate=appt.get("appointmentDate", "-"),
                 appointmentTime=appt.get("appointmentTime", "-"),
                 hospital=appt.get("hospital", "-"))
    _send(patient_phone, msg)
    return {"status": "patient_notified", "requestId": request_id}


def _send(phone, msg):
    if not phone:
        return
    try:
        twilio_client.send_whatsapp(phone, msg)
    except Exception as exc:
        logger.warning("notify_patient send failed: %s", exc)
