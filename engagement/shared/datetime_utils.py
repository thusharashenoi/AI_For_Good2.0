"""IST-aware date/time helpers for appointments, voice, and reminders."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))


def now_local() -> datetime:
    return datetime.now(IST)


def appt_datetime(date_str: Optional[str], time_str: str = "10:00 AM") -> Optional[datetime]:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M%p"):
        try:
            raw = f"{date_str} {time_str.strip()}"
            return datetime.strptime(raw, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=10, minute=0, tzinfo=IST)
    except ValueError:
        return None


def format_display(date_iso: str, time_str: str = "10:00 AM") -> Tuple[str, str]:
    """Human-friendly date + time for WhatsApp."""
    dt = appt_datetime(date_iso, time_str)
    if not dt:
        return date_iso or "-", time_str or "-"
    return dt.strftime("%A, %d %B %Y"), time_str


def format_spoken(date_iso: str, time_str: str = "10:00 AM") -> str:
    """Natural phrase for TTS."""
    dt = appt_datetime(date_iso, time_str)
    if not dt:
        parts = [p for p in (date_iso, time_str) if p]
        return " at ".join(parts) if parts else "the scheduled time"
    day = dt.strftime("%A, %d %B")
    spoken_time = dt.strftime("%I:%M %p").lstrip("0")
    if dt.date() == now_local().date():
        return f"today at {spoken_time}"
    if dt.date() == (now_local() + timedelta(days=1)).date():
        return f"tomorrow at {spoken_time}"
    return f"{day} at {spoken_time}"


def default_appointment_slot(
    date_iso: Optional[str] = None,
    time_str: Optional[str] = None,
    *,
    required_by: Optional[str] = None,
) -> Tuple[str, str]:
    """Pick a sensible donation date/time from now (IST)."""
    from .bedrock_client import parse_date

    now = now_local()
    if not date_iso and required_by:
        date_iso = parse_date(required_by, reference=now)
    if not date_iso:
        date_iso = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    if not time_str:
        if date_iso == now.strftime("%Y-%m-%d"):
            slot = now + timedelta(hours=2)
            minute = 0 if slot.minute < 30 else 30
            slot = slot.replace(minute=minute, second=0, microsecond=0)
            if slot.hour >= 18:
                date_iso = (now + timedelta(days=1)).strftime("%Y-%m-%d")
                time_str = "10:00 AM"
            else:
                time_str = slot.strftime("%I:%M %p").lstrip("0")
        else:
            time_str = "10:00 AM"
    return date_iso, time_str


def schedule_appointment_reminders(appt: Dict) -> None:
    """Schedule day-before and 3-hour WhatsApp reminders (EventBridge or local log)."""
    from . import config, scheduler

    when = appt_datetime(appt.get("appointmentDate"), appt.get("appointmentTime", "10:00 AM"))
    if not when:
        return
    when_utc = when.astimezone(timezone.utc)
    now_utc = datetime.now(timezone.utc)
    day_before = when_utc - timedelta(hours=24)
    three_hours = when_utc - timedelta(hours=3)
    appt_id = appt["appointmentId"]

    if day_before > now_utc:
        scheduler.create_schedule(
            name=f"appt-daybefore-{appt_id[:8]}",
            at=day_before,
            target_arn=config.get("APPOINTMENT_REMINDER_ARN", "local"),
            payload={"appointmentId": appt_id, "type": "day_before"})
    if three_hours > now_utc:
        scheduler.create_schedule(
            name=f"appt-threehr-{appt_id[:8]}",
            at=three_hours,
            target_arn=config.get("THREE_HOUR_REMINDER_ARN", "local"),
            payload={"appointmentId": appt_id, "type": "three_hour"})
