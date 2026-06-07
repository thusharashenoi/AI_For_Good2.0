"""Voice outreach appointment flow — proposed slot, booking, WhatsApp, hangup."""
from __future__ import annotations

import logging
from typing import Dict, Optional

from . import dynamodb_client as db
from .datetime_utils import (
    format_ask_donation_time_spoken,
    format_availability_window,
    format_blood_due_relative,
    format_blood_due_spoken,
    format_spoken,
    normalize_appointment_time,
    normalize_due_date,
    outreach_slot_days_until,
)
from .voice_speech import sanitize_for_speech

logger = logging.getLogger("raktsetu.voice_booking")


def proposed_appointment_for_request(req: dict) -> dict:
    """Outreach context for a blood request — no date/time until the donor confirms."""
    required_by = req.get("requiredBy")
    due_iso = normalize_due_date(required_by)
    hospital = req.get("hospital") or "the hospital"
    blood_due = format_blood_due_spoken(required_by)
    due_relative = format_blood_due_relative(required_by)
    window = format_availability_window(required_by)
    days_until = outreach_slot_days_until(required_by)
    return {
        "hospital": hospital,
        "date": None,
        "time": None,
        "spokenWhen": None,
        "requestId": req.get("requestId"),
        "bloodDueBy": due_iso or required_by,
        "bloodDueSpoken": blood_due,
        "bloodDueRelative": due_relative,
        "availabilityWindowSpoken": window,
        "availabilityConfirmed": False,
        "daysUntilDue": days_until,
        "slotQuestionSpoken": format_ask_donation_time_spoken(required_by, hospital),
        "askTimeOnly": days_until is not None and days_until <= 1,
    }


def prime_proposed_appointment(phone: str) -> Optional[dict]:
    """Store outreach context on the donor conversation (idempotent refresh)."""
    conv = db.get_conversation(phone) or {}
    request_id = conv.get("activeRequestId")
    if not request_id:
        return None
    req = db.get_request(request_id)
    if not req:
        return None
    ctx = conv.setdefault("contextData", {})
    existing = ctx.get("proposedAppointment") or {}
    if (
        existing.get("availabilityConfirmed")
        and existing.get("date")
        and existing.get("time")
        and existing.get("requestId") == request_id
    ):
        return existing
    ctx = conv.setdefault("contextData", {})
    proposed = proposed_appointment_for_request(req)
    ctx["proposedAppointment"] = proposed
    db.save_conversation(conv)
    return proposed


def get_proposed_appointment(phone: str) -> Optional[dict]:
    conv = db.get_conversation(phone) or {}
    proposed = (conv.get("contextData") or {}).get("proposedAppointment")
    if proposed:
        return proposed
    return prime_proposed_appointment(phone)


def _coalesce_slot_fields(
    date: Optional[str],
    time: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Split combined donor utterances like 'Monday 9 AM, 9th June' into date + time."""
    import re

    def _has_time(text: str) -> bool:
        return bool(re.search(
            r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.))\b", text, re.I))

    if time and not date and re.search(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|monday|tuesday|"
        r"wednesday|thursday|friday|saturday|sunday|tomorrow|today|\d{1,2}(?:st|nd|rd|th))\b",
        time, re.I,
    ):
        date, time = time, None

    if date and not time and _has_time(date):
        m = re.search(
            r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.))\b", date, re.I)
        if m:
            time = m.group(1)
            date = (date[:m.start()] + date[m.end():]).strip(" ,")
            date = re.sub(r"\b(on|at)\b", "", date, flags=re.I).strip(" ,") or None

    if date:
        date = re.sub(
            r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            "", date, flags=re.I,
        ).strip(" ,") or date

    return date or None, time or None


def resolve_donor_slot(
    proposed: dict,
    req: Optional[dict],
    *,
    date: Optional[str] = None,
    time: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve date/time from donor input only — never invent a default slot."""
    from .bedrock_client import parse_date

    date, time = _coalesce_slot_fields(date, time)
    time_str = normalize_appointment_time(time) if time else None
    if not time_str:
        return None, None, "missing_time"

    parsed_date = parse_date(date) if date else None
    if not parsed_date and proposed.get("askTimeOnly") and req:
        parsed_date = normalize_due_date(req.get("requiredBy"))
    if not parsed_date:
        return None, None, "missing_date"

    return parsed_date, time_str, None


def update_proposed_slot(phone: str, date: Optional[str] = None,
                         time: Optional[str] = None) -> dict:
    """Save donor-agreed date/time — rejects missing date/time (no auto-booking defaults)."""
    conv = db.get_conversation(phone) or {}
    ctx = conv.setdefault("contextData", {})
    proposed = dict(ctx.get("proposedAppointment") or get_proposed_appointment(phone) or {})
    req = db.get_request(conv.get("activeRequestId")) if conv.get("activeRequestId") else None
    if not proposed and req:
        proposed = proposed_appointment_for_request(req)

    if req and not proposed.get("hospital"):
        proposed["hospital"] = req.get("hospital") or "the hospital"

    if not date and not time:
        proposed["availabilityConfirmed"] = False
        ctx["proposedAppointment"] = proposed
        db.save_conversation(conv)
        return {
            "ok": False,
            "reason": "missing_slot",
            "slotQuestionSpoken": proposed.get("slotQuestionSpoken"),
            "askTimeOnly": proposed.get("askTimeOnly"),
            "hint": (
                "Ask the donor for their preferred date and time using slotQuestionSpoken, "
                "then call confirm_appointment_slot with what they said."
            ),
        }

    date_iso, time_str, err = resolve_donor_slot(proposed, req, date=date, time=time)
    if err:
        proposed["availabilityConfirmed"] = False
        ctx["proposedAppointment"] = proposed
        db.save_conversation(conv)
        hint = (
            "Ask what TIME works (day is already today/tomorrow)."
            if err == "missing_time" and proposed.get("askTimeOnly")
            else "Ask which DAY before the deadline and what TIME, then retry."
            if err == "missing_date"
            else "Ask what time works, then retry."
        )
        return {
            "ok": False,
            "reason": err,
            "slotQuestionSpoken": proposed.get("slotQuestionSpoken"),
            "askTimeOnly": proposed.get("askTimeOnly"),
            "hint": hint,
        }

    proposed["date"] = date_iso
    proposed["time"] = time_str
    proposed["spokenWhen"] = format_spoken(date_iso, time_str)
    proposed["availabilityConfirmed"] = True

    ctx["proposedAppointment"] = proposed
    db.save_conversation(conv)
    return {"ok": True, **proposed}


def appointment_goodbye(book_result: dict, donor_name: str) -> str:
    name = (donor_name or "friend").split(" ")[0]
    hospital = book_result.get("hospital") or "the hospital"
    when = format_spoken(book_result.get("date") or "", book_result.get("time") or "10:00 AM")
    return sanitize_for_speech(
        f"Perfect {name}, you are a lifesaver, literally. "
        f"Your donation is confirmed at {hospital}, {when}. "
        "We will send the details on WhatsApp. Namaste."
    )


def finalize_voice_booking(message: dict, entry: dict, phone: str,
                           book_result: dict, donor_name: str) -> dict:
    """Send WhatsApp confirmation, speak goodbye, and end the call (blocking)."""
    from . import vapi_client
    from .outreach import send_appointment_confirmation_whatsapp

    wa = send_appointment_confirmation_whatsapp(phone, book_result.get("appointmentId"))
    logger.info("voice booking WhatsApp phone=%s result=%s", phone, wa)

    conv = db.get_conversation(phone) or {}
    call_id = vapi_client.call_id(message)
    if call_id:
        conv["postCallWhatsappForCallId"] = call_id
    db.save_conversation(conv)

    goodbye = appointment_goodbye(book_result, donor_name)
    entry["message"] = goodbye
    entry["result"] = "Do not speak. Goodbye was delivered and the call is ending."
    hangup = vapi_client.hangup_after_goodbye(
        message, goodbye, delay_after_speak_seconds=2.0)
    logger.info("voice booking hangup phone=%s result=%s", phone, hangup)
    return {"whatsapp": wa, "hangup": hangup}
