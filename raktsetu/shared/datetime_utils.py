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
    """Human-friendly date + time for WhatsApp (written form)."""
    dt = appt_datetime(date_iso, time_str)
    if not dt:
        return date_iso or "-", time_str or "-"
    return dt.strftime("%A, %d %B %Y"), dt.strftime("%I:%M %p").lstrip("0")


def speak_time(time_str: str = "10:00 AM") -> str:
    """TTS-safe time without AM/PM letter sequences (avoids 'ay em' / garbled acronyms)."""
    raw = (time_str or "10:00 AM").strip()
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            t = datetime.strptime(raw.upper(), fmt.upper() if "%p" in fmt else fmt)
            break
        except ValueError:
            try:
                t = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    else:
        return raw.replace(":", " ").replace("AM", "in the morning").replace("PM", "in the afternoon")

    hour24 = t.hour
    minute = t.minute
    h12 = hour24 % 12 or 12
    if hour24 < 12:
        period = "in the morning"
    elif hour24 < 17:
        period = "in the afternoon"
    else:
        period = "in the evening"
    if minute == 0:
        return f"{h12} {period}"
    if minute == 30:
        return f"{h12} thirty {period}"
    if minute == 15:
        return f"{h12} fifteen {period}"
    if minute == 45:
        return f"{h12} forty five {period}"
    return f"{h12} {minute} {period}"


def normalize_due_date(required_by: Optional[str],
                       reference: Optional[datetime] = None) -> Optional[str]:
    """Parse required-by into YYYY-MM-DD when possible."""
    import re

    if not required_by:
        return None
    raw = str(required_by).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    from .bedrock_client import parse_date
    return parse_date(raw, reference=reference or now_local())


def format_date_spoken(date_iso: Optional[str]) -> str:
    """Spoken calendar day for TTS."""
    if not date_iso:
        return "soon"
    dt = appt_datetime(date_iso, "12:00 PM")
    if not dt:
        return date_iso
    now = now_local()
    if dt.date() == now.date():
        return "today"
    if dt.date() == (now + timedelta(days=1)).date():
        return "tomorrow"
    return dt.strftime("%A, %d %B")


def format_blood_due_spoken(required_by: Optional[str]) -> str:
    """When the patient needs blood — for voice."""
    due_iso = normalize_due_date(required_by)
    if due_iso:
        return format_date_spoken(due_iso)
    raw = (required_by or "soon").strip()
    return raw.replace("_", " ")


def outreach_slot_days_until(required_by: Optional[str]) -> Optional[int]:
    """Calendar days from today until blood is due (None if unknown)."""
    due_iso = normalize_due_date(required_by)
    if not due_iso:
        return None
    due_dt = appt_datetime(due_iso, "12:00 PM")
    if not due_dt:
        return None
    return (due_dt.date() - now_local().date()).days


def format_ask_donation_time_spoken(
    required_by: Optional[str],
    hospital: Optional[str] = None,
) -> str:
    """Context-aware question after donor says YES — no redundant 'which day' when due is tomorrow."""
    hospital = (hospital or "the hospital").strip()
    days = outreach_slot_days_until(required_by)
    if days is None:
        return f"what day and time work for you at {hospital} before blood is needed"
    if days <= 0:
        return f"what time today you can come to {hospital}"
    if days == 1:
        return f"what time tomorrow works for you at {hospital}"
    if days == 2:
        return f"what time the day after tomorrow works at {hospital}"
    due_spoken = format_date_spoken(normalize_due_date(required_by))
    return f"which day before {due_spoken} works and what time at {hospital}"


def format_blood_due_relative(required_by: Optional[str]) -> str:
    """Spoken relative deadline e.g. 'tomorrow', 'in 3 days'."""
    due_iso = normalize_due_date(required_by)
    if not due_iso:
        spoken = format_blood_due_spoken(required_by)
        if spoken == "tomorrow":
            return "tomorrow"
        if spoken == "today":
            return "today"
        return spoken
    now = now_local()
    due_dt = appt_datetime(due_iso, "12:00 PM")
    if not due_dt:
        return format_blood_due_spoken(required_by)
    days = (due_dt.date() - now.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days == 2:
        return "in two days"
    return f"in {days} days"


def format_availability_window(required_by: Optional[str]) -> str:
    """Spoken window: anytime from now until the blood is due."""
    now = now_local()
    due_iso = normalize_due_date(required_by, now)
    if not due_iso:
        return "any time before the patient needs it"
    due_spoken = format_date_spoken(due_iso)
    if due_iso == now.strftime("%Y-%m-%d"):
        return "any time today"
    return f"any time between now and {due_spoken}"


def format_spoken(date_iso: str, time_str: str = "10:00 AM") -> str:
    """Natural phrase for TTS."""
    dt = appt_datetime(date_iso, time_str)
    spoken_time = speak_time(time_str)
    if not dt:
        parts = [p for p in (date_iso, spoken_time) if p]
        return " at ".join(parts) if parts else "the scheduled time"
    day = dt.strftime("%A, %d %B")
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
