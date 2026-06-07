"""Tests for broadcasting outreach to all eligible matching donors."""
from unittest.mock import patch

from shared import dynamodb_client as db
from shared.agent_tools import AgentTools
from shared.outreach import run_outreach


def _seed_two_donors_and_request():
    d1 = "+919876503001"
    d2 = "+919876503002"
    patient = "+919876503003"
    AgentTools(d1, channel="voice").complete_donor_registration(
        name="Donor One", age=30, weight=70, blood_group="A+", area="Madhapur")
    AgentTools(d2, channel="voice").complete_donor_registration(
        name="Donor Two", age=32, weight=72, blood_group="A+", area="Gachibowli")
    with patch("shared.scheduler.start_outreach", return_value={}):
        req = AgentTools(patient, channel="whatsapp").raise_blood_request(
            patient_name="Baby", blood_group="A+", units=1,
            hospital="Apollo", required_by="tomorrow")
    return req["requestId"], d1, d2


def test_broadcast_contacts_all_eligible_donors():
    request_id, d1, d2 = _seed_two_donors_and_request()

    with patch("shared.outreach._send_whatsapp_template", return_value=True) as wa_mock, \
         patch("shared.scheduler.schedule_outreach_voice_escalation", return_value={"delaySeconds": 15}), \
         patch("lambdas.trigger_voice.handler.handler") as voice_mock:
        result = run_outreach(request_id)

    assert result.get("donorsContacted", 0) >= 2
    assert result.get("rankedCount", 0) >= 2
    assert wa_mock.call_count >= 2
    voice_mock.assert_not_called()
    req = db.get_request(request_id)
    assert len(req.get("assignedDonors") or []) >= 2


def test_top_donor_only_mode():
    request_id, _, _ = _seed_two_donors_and_request()

    with patch("shared.outreach._send_whatsapp_template", return_value=True), \
         patch("shared.scheduler.schedule_outreach_voice_escalation", return_value={"delaySeconds": 15}), \
         patch("lambdas.trigger_voice.handler.handler") as voice_mock:
        result = run_outreach(request_id, {"topDonorOnly": True})

    assert result.get("status") in ("outreach_sent", "already_contacted")
    assert result.get("donorsContacted") is None
    voice_mock.assert_not_called()
