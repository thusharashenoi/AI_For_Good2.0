from datetime import datetime, timedelta
from unittest.mock import patch

from shared.datetime_utils import (
    default_appointment_slot,
    format_spoken,
    now_local,
    schedule_appointment_reminders,
)
from shared import scheduler


def test_format_spoken_tomorrow():
    tomorrow = (now_local() + timedelta(days=1)).strftime("%Y-%m-%d")
    spoken = format_spoken(tomorrow, "10:00 AM")
    assert "tomorrow" in spoken.lower()
    assert "morning" in spoken.lower()
    assert "10:00 AM" not in spoken


def test_default_appointment_slot_from_required_by():
    from shared.datetime_utils import IST
    fixed = datetime(2026, 6, 6, 14, 0, tzinfo=IST)
    with patch("shared.datetime_utils.now_local", return_value=fixed):
        date_iso, time_str = default_appointment_slot(required_by="tomorrow")
    assert date_iso == "2026-06-07"
    assert time_str == "10:00 AM"


def test_format_blood_due_and_availability_window():
    from shared.datetime_utils import format_availability_window, format_blood_due_spoken
    from unittest.mock import patch
    from datetime import datetime, timezone, timedelta

    IST = timezone(timedelta(hours=5, minutes=30))
    fixed = datetime(2026, 6, 6, 14, 0, tzinfo=IST)
    with patch("shared.datetime_utils.now_local", return_value=fixed):
        assert "tomorrow" in format_blood_due_spoken("tomorrow").lower()
        window = format_availability_window("tomorrow")
        assert "between now and tomorrow" in window.lower()


def test_format_blood_due_relative():
    from datetime import datetime, timezone, timedelta
    from unittest.mock import patch
    from shared.datetime_utils import format_blood_due_relative, IST

    fixed = datetime(2026, 6, 6, 14, 0, tzinfo=IST)
    with patch("shared.datetime_utils.now_local", return_value=fixed):
        assert format_blood_due_relative("tomorrow") == "tomorrow"
        assert "days" in format_blood_due_relative("in 3 days") or \
            format_blood_due_relative("2026-06-09") == "in 3 days"


def test_format_ask_donation_time_tomorrow():
    from unittest.mock import patch
    from shared.datetime_utils import format_ask_donation_time_spoken, IST
    from datetime import datetime

    fixed = datetime(2026, 6, 6, 14, 0, tzinfo=IST)
    with patch("shared.datetime_utils.now_local", return_value=fixed):
        q = format_ask_donation_time_spoken("tomorrow", "NIAT Hospital")
    assert "tomorrow" in q
    assert "NIAT" in q
    assert "which day" not in q


def test_format_ask_donation_time_today():
    from unittest.mock import patch
    from shared.datetime_utils import format_ask_donation_time_spoken, IST
    from datetime import datetime

    fixed = datetime(2026, 6, 6, 14, 0, tzinfo=IST)
    with patch("shared.datetime_utils.now_local", return_value=fixed):
        q = format_ask_donation_time_spoken("today", "Apollo")
    assert "today" in q
    assert "which day" not in q


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
