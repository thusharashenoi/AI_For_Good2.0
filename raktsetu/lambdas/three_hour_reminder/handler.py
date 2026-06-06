"""Lambda: three-hour-reminder  (EventBridge target, 3h before)

WhatsApp-only final nudge with a maps link. No call (too close to appointment).
"""
from __future__ import annotations

import logging
import os
import sys
from urllib.parse import quote_plus

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import dynamodb_client as db, i18n, twilio_client  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.three_hour_reminder")


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
    maps = f"https://www.google.com/maps/search/?api=1&query={quote_plus(appt.get('hospital', ''))}"

    msg = i18n.t("THREE_HOUR_REMINDER", lang,
                 hospital=appt.get("hospital", "-"),
                 googleMapsLink=maps,
                 appointmentTime=appt.get("appointmentTime", "-"))
    try:
        twilio_client.send_whatsapp(donor_phone, msg)
    except Exception as exc:
        logger.warning("3h reminder send failed: %s", exc)
    return {"status": "three_hour_sent", "appointmentId": appt_id}
