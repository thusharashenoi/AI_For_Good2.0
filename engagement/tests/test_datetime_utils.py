from datetime import datetime, timedelta
from unittest.mock import patch

from shared.datetime_utils import (
    appt_datetime,
    default_appointment_slot,
    format_spoken,
    normalize_appointment_time,
    now_local,
    schedule_appointment_reminders,
)
from shared import scheduler
from conftest import DEMO_PHONE


def test_format_spoken_tomorrow():
    tomorrow = (now_local() + timedelta(days=1)).strftime("%Y-%m-%d")
    spoken = format_spoken(tomorrow, "10:00 AM")
    assert "tomorrow" in spoken.lower()
    assert "10" in spoken


def test_default_appointment_slot_from_required_by():
    from shared.datetime_utils import IST
    fixed = datetime(2026, 6, 6, 14, 0, tzinfo=IST)
    with patch("shared.datetime_utils.now_local", return_value=fixed):
        date_iso, time_str = default_appointment_slot(required_by="tomorrow")
    assert date_iso == "2026-06-07"
    assert time_str == "10:00 AM"


def test_schedule_reminders_24h_and_3h_before():
    scheduler.SCHEDULED.clear()
    appt = {
        "appointmentId": "appt-test-123",
        "appointmentDate": (now_local() + timedelta(days=2)).strftime("%Y-%m-%d"),
        "appointmentTime": "10:00 AM",
    }
    schedule_appointment_reminders(appt)
    assert len(scheduler.SCHEDULED) == 2
    names = {s["name"] for s in scheduler.SCHEDULED}
    assert "appt-daybefore-appt-tes" in names
    assert "appt-threehr-appt-tes" in names


def test_normalize_appointment_time():
    assert normalize_appointment_time("2:00 PM") == "2:00 PM"
    assert normalize_appointment_time("2 PM") == "2:00 PM"
    assert normalize_appointment_time("2pm") == "2:00 PM"
    assert normalize_appointment_time("3pm") == "3:00 PM"
    assert normalize_appointment_time("14:00") == "2:00 PM"


def test_format_display_uses_agreed_time_not_default():
    from shared.datetime_utils import format_display

    date_disp, time_disp = format_display("2026-06-07", "2 PM")
    assert time_disp == "2:00 PM"
    dt = appt_datetime("2026-06-07", "2pm")
    assert dt and dt.hour == 14


def test_confirm_slot_preserves_spoken_time():
    from shared.agent_tools import AgentTools
    from shared import dynamodb_client as db

    donor_phone = DEMO_PHONE
    patient_phone = DEMO_PHONE
    dt = AgentTools(donor_phone, channel="voice")
    dt.complete_donor_registration(
        name="Time Donor", age=30, weight=70, blood_group="A+", area="Madhapur")
    req = AgentTools(patient_phone, channel="whatsapp").raise_blood_request(
        patient_name="Baby", blood_group="A+", units=1,
        hospital="Apollo", required_by="tomorrow")
    conv = db.get_conversation(donor_phone)
    conv["activeRequestId"] = req["requestId"]
    db.save_conversation(conv)

    slot = dt.confirm_appointment_slot(time="2 PM")
    assert slot.get("ok") is True
    assert slot.get("time") == "2:00 PM"
    assert slot.get("availabilityConfirmed") is True

    book = dt.book_appointment(request_id=req["requestId"])
    assert book.get("ok") is True
    assert book.get("time") == "2:00 PM"
    appt = db.get_appointment(book["appointmentId"])
    assert appt["appointmentTime"] == "2:00 PM"
