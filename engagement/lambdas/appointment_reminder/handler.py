"""Lambda: appointment-reminder  (EventBridge target, day-before)

Sends the day-before WhatsApp reminder with confirm/cancel/call quick replies.
If the donor doesn't reply within 2 hours, a follow-up schedule triggers a call
(handled by re-invoking with {"escalate": true}).
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import config, dynamodb_client as db, i18n, scheduler, twilio_client  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.appointment_reminder")

VOICE_OUTBOUND_URL = os.environ.get("VOICE_OUTBOUND_URL", "https://example.com/voice/outbound")


def handler(event, context=None):
    appt_id = event.get("appointmentId")
    appt = db.get_appointment(appt_id) if appt_id else None
    if not appt:
        return {"status": "error", "reason": "appointment_not_found"}
    if appt.get("status") in ("cancelled", "completed"):
        return {"status": "skipped", "reason": appt.get("status")}

    donor_phone = appt.get("donorPhone")
    conv = db.get_conversation(donor_phone) if donor_phone else None
    lang = (conv or {}).get("language", "en")

    # Escalation path: donor didn't confirm -> place a reminder call.
    if event.get("escalate"):
        if appt.get("donorConfirmedDayBefore"):
            return {"status": "already_confirmed"}
        try:
            twilio_client.make_call(
                donor_phone,
                f"{VOICE_OUTBOUND_URL}?appointmentId={appt_id}&lang={lang}&mode=reminder")
        except Exception as exc:
            logger.warning("reminder call failed: %s", exc)
        return {"status": "reminder_call_placed", "appointmentId": appt_id}

    msg = i18n.t("APPOINTMENT_REMINDER", lang,
                 hospital=appt.get("hospital", "-"),
                 appointmentTime=appt.get("appointmentTime", "-"),
                 patientName=appt.get("patientName", "-"))
    try:
        twilio_client.send_whatsapp(donor_phone, msg)
    except Exception as exc:
        logger.warning("reminder send failed: %s", exc)

    appt["status"] = "reminder_sent"
    appt["reminderSentAt"] = db.now_iso()
    db.save_appointment(appt)

    # Schedule a no-reply escalation in 2 hours.
    scheduler.create_schedule(
        name=f"appt-remind-escalate-{appt_id[:8]}",
        at=scheduler.in_hours(2),
        target_arn=config.get("APPOINTMENT_REMINDER_ARN", "local"),
        payload={"appointmentId": appt_id, "escalate": True})

    return {"status": "reminder_sent", "appointmentId": appt_id}
