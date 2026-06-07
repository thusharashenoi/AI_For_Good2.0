"""Voice outreach appointment flow — proposed slot, booking, WhatsApp, hangup."""
from __future__ import annotations

import logging
from typing import Dict, Optional

from . import dynamodb_client as db
from .datetime_utils import (
    default_appointment_slot,
    format_availability_window,
    format_blood_due_relative,
    format_blood_due_spoken,
    format_spoken,
    normalize_due_date,
)
from .voice_speech import sanitize_for_speech

logger = logging.getLogger("raktsetu.voice_booking")


def proposed_appointment_for_request(req: dict) -> dict:
    """Default donation slot and blood-due window from an open blood request."""
    required_by = req.get("requiredBy")
    due_iso = normalize_due_date(required_by)
    date_iso, time_str = default_appointment_slot(required_by=required_by)
    hospital = req.get("hospital") or "the hospital"
    blood_due = format_blood_due_spoken(required_by)
    due_relative = format_blood_due_relative(required_by)
    window = format_availability_window(required_by)
    return {
        "hospital": hospital,
        "date": date_iso,
        "time": time_str,
        "spokenWhen": format_spoken(date_iso, time_str),
        "requestId": req.get("requestId"),
        "bloodDueBy": due_iso or required_by,
        "bloodDueSpoken": blood_due,
        "bloodDueRelative": due_relative,
        "availabilityWindowSpoken": window,
        "availabilityConfirmed": False,
    }


def prime_proposed_appointment(phone: str) -> Optional[dict]:
    """Store the default proposed slot on the donor conversation (idempotent refresh)."""
    conv = db.get_conversation(phone) or {}
    request_id = conv.get("activeRequestId")
    if not request_id:
        return None
    req = db.get_request(request_id)
    if not req:
        return None
    proposed = proposed_appointment_for_request(req)
    conv.setdefault("contextData", {})["proposedAppointment"] = proposed
    db.save_conversation(conv)
    return proposed


def get_proposed_appointment(phone: str) -> Optional[dict]:
    conv = db.get_conversation(phone) or {}
    proposed = (conv.get("contextData") or {}).get("proposedAppointment")
    if proposed:
        return proposed
    return prime_proposed_appointment(phone)


def update_proposed_slot(phone: str, date: Optional[str] = None,
                         time: Optional[str] = None) -> dict:
    """Update proposed slot after donor asks for a different date or time."""
    from .bedrock_client import parse_date

    conv = db.get_conversation(phone) or {}
    ctx = conv.setdefault("contextData", {})
    proposed = dict(ctx.get("proposedAppointment") or get_proposed_appointment(phone) or {})
    req = db.get_request(conv.get("activeRequestId")) if conv.get("activeRequestId") else None
    if not proposed and req:
        proposed = proposed_appointment_for_request(req)

    parsed_date = parse_date(date) if date else None
    if parsed_date:
        proposed["date"] = parsed_date
    if time:
        proposed["time"] = time.strip()

    if req and not proposed.get("hospital"):
        proposed["hospital"] = req.get("hospital") or "the hospital"

    date_iso = proposed.get("date")
    time_str = proposed.get("time") or "10:00 AM"
    if date_iso:
        date_iso, time_str = default_appointment_slot(date_iso, time_str)
        proposed["date"] = date_iso
        proposed["time"] = time_str
    proposed["spokenWhen"] = format_spoken(
        proposed.get("date") or "", proposed.get("time") or "10:00 AM")
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
