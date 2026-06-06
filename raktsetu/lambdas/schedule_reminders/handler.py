"""Lambda: schedule-appointment-reminders  (SFN task: ScheduleReminders)

Creates two EventBridge one-time schedules per appointment:
  - day-before reminder (~24h before appointmentDate)  -> appointment-reminder
  - 3-hours-before reminder                            -> three-hour-reminder
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import config, dynamodb_client as db, scheduler  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.schedule_reminders")


def _appt_datetime(appt: dict) -> datetime | None:
    date_str = appt.get("appointmentDate")
    time_str = appt.get("appointmentTime", "10:00 AM")
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date_str} {time_str}", fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=10, tzinfo=timezone.utc)
    except ValueError:
        return None


def handler(event, context=None):
    appt_id = event.get("appointmentId")
    if not appt_id and event.get("requestId"):
        req = db.get_request(event["requestId"]) or {}
        for a in req.get("assignedDonors", []):
            if a.get("appointmentId"):
                appt_id = a["appointmentId"]
                break
    appt = db.get_appointment(appt_id) if appt_id else None
    if not appt:
        return {"status": "error", "reason": "appointment_not_found"}

    from shared.datetime_utils import schedule_appointment_reminders
    schedule_appointment_reminders(appt)

    from shared.datetime_utils import appt_datetime
    when = appt_datetime(appt.get("appointmentDate"), appt.get("appointmentTime", "10:00 AM"))
    if not when:
        return {"status": "error", "reason": "bad_appointment_date"}
    from datetime import timedelta, timezone
    when_utc = when.astimezone(timezone.utc)
    day_before = when_utc - timedelta(hours=24)
    three_hours = when_utc - timedelta(hours=3)

    return {"status": "scheduled", "appointmentId": appt_id,
            "dayBeforeAt": day_before.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "threeHourAt": three_hours.strftime("%Y-%m-%dT%H:%M:%SZ")}
