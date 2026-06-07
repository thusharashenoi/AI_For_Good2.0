"""Tests for server-side voice escalation scheduling."""
from unittest.mock import patch

from shared import voice_escalation_scheduler
from shared.outreach import escalate_to_voice_if_no_reply


def test_schedule_escalation_fires_callback():
    fired = []

    def fake_escalate(request_id, donor_id, token=None):
        fired.append({"requestId": request_id, "donorId": donor_id, "token": token})
        return {"ok": True, "voicePlaced": True}

    with patch("shared.outreach.escalate_to_voice_if_no_reply", side_effect=fake_escalate):
        voice_escalation_scheduler.schedule_escalation("req-1", "donor-1", "tok", 0.05)
        import time
        time.sleep(0.15)

    assert len(fired) == 1
    assert fired[0]["token"] == "tok"


def test_cancel_escalation_prevents_call():
    fired = []

    with patch("shared.outreach.escalate_to_voice_if_no_reply", side_effect=lambda *a, **k: fired.append(1)):
        voice_escalation_scheduler.schedule_escalation("req-2", "donor-2", "tok2", 0.2)
        voice_escalation_scheduler.cancel_escalation("req-2", "donor-2")
        import time
        time.sleep(0.35)

    assert fired == []
