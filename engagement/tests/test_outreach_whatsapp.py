"""Tests for outreach voice-first and post-call WhatsApp."""
from unittest.mock import patch

from shared import dynamodb_client as db
from shared.agent_tools import AgentTools
from shared.outreach import _send_to_top_donor, send_appointment_confirmation_whatsapp, send_post_call_whatsapp


def _seed_request_with_donor(donor_phone: str, patient_phone: str) -> str:
    AgentTools(donor_phone, channel="voice").complete_donor_registration(
        name="Voice Donor", age=30, weight=70, blood_group="A+", area="Madhapur")
    result = AgentTools(patient_phone, channel="whatsapp").raise_blood_request(
        patient_name="Baby", blood_group="A+", units=1,
        hospital="Apollo", required_by="tomorrow")
    assert result["ok"] is True
    return result["requestId"]


def test_voice_outreach_skips_whatsapp_template():
    donor_phone = "+919876501030"
    patient_phone = "+919876501031"
    request_id = _seed_request_with_donor(donor_phone, patient_phone)

    fake_voice = {"ok": True, "callSid": "call-abc", "voicePlaced": True, "status": "calling"}

    with patch("lambdas.trigger_voice.handler.handler", return_value=fake_voice), \
         patch("shared.outreach._send_whatsapp_template") as wa_mock:
        result = _send_to_top_donor(request_id)

    assert result.get("status") == "already_contacted" or (
        result.get("voicePlaced") is True and result.get("whatsappSent") is False
    )
    if result.get("voicePlaced"):
        wa_mock.assert_not_called()
        conv = db.get_conversation(donor_phone)
        assert conv.get("voiceOutreachPlaced") is True


def test_voice_first_on_fresh_request():
    donor_phone = "+919876501034"
    patient_phone = "+919876501035"
    AgentTools(donor_phone, channel="voice").complete_donor_registration(
        name="Fresh Donor", age=30, weight=70, blood_group="B+", area="Secunderabad")

    with patch("shared.scheduler.start_outreach", return_value={}):
        result = AgentTools(patient_phone, channel="whatsapp").raise_blood_request(
            patient_name="Baby", blood_group="B+", units=1,
            hospital="Apollo", required_by="tomorrow")
    request_id = result["requestId"]

    fake_voice = {"ok": True, "callSid": "call-fresh", "voicePlaced": True, "status": "calling"}
    with patch("lambdas.trigger_voice.handler.handler", return_value=fake_voice), \
         patch("shared.outreach._send_whatsapp_template") as wa_mock:
        outreach = _send_to_top_donor(request_id)

    assert outreach.get("voicePlaced") is True
    assert outreach.get("whatsappSent") is False
    wa_mock.assert_not_called()


def test_appointment_confirmation_whatsapp_on_booking():
    donor_phone = "+919876501040"
    tools = AgentTools(donor_phone, channel="voice")
    tools.complete_donor_registration(
        name="Book WA Donor", age=30, weight=70, blood_group="A+", area="Madhapur")
    req = AgentTools("+919876501041", channel="whatsapp").raise_blood_request(
        patient_name="Patient", blood_group="A+", units=1,
        hospital="NIAT", required_by="tomorrow")
    conv = db.get_conversation(donor_phone)
    conv["activeRequestId"] = req["requestId"]
    db.save_conversation(conv)

    with patch("shared.periskope_client.send_message", return_value={"ok": True}) as send_mock:
        book = tools.book_appointment(request_id=req["requestId"])
    assert book.get("ok") is True
    donor_calls = [c for c in send_mock.call_args_list if c[0][0] == donor_phone]
    assert len(donor_calls) == 1
    body = donor_calls[0][0][1]
    assert "confirmed" in body.lower()
    assert "NIAT" in body

    with patch("shared.periskope_client.send_message", return_value={"ok": True}) as send_mock2:
        again = send_appointment_confirmation_whatsapp(donor_phone, book["appointmentId"])
    assert again.get("skipped") is True
    send_mock2.assert_not_called()


def test_post_call_whatsapp_after_voice_outreach():
    donor_phone = "+919876501032"
    AgentTools(donor_phone, channel="voice").complete_donor_registration(
        name="Post Call Donor", age=30, weight=70, blood_group="O+", area="Madhapur")
    patient_phone = "+919876501033"
    req = AgentTools(patient_phone, channel="whatsapp").raise_blood_request(
        patient_name="Patient", blood_group="O+", units=1,
        hospital="Rainbow", required_by="tomorrow")
    request_id = req["requestId"]

    conv = db.get_conversation(donor_phone)
    conv["voiceOutreachPlaced"] = True
    conv["awaitingOutreachReply"] = True
    conv["activeRequestId"] = request_id
    db.save_conversation(conv)

    with patch("shared.periskope_client.send_message", return_value={"ok": True}) as send_mock:
        result = send_post_call_whatsapp(donor_phone, "call-xyz")

    assert result["ok"] is True
    send_mock.assert_called_once()
    body = send_mock.call_args[0][1]
    assert "thank you" in body.lower()
    assert "YES — I can donate" not in body

    with patch("shared.periskope_client.send_message", return_value={"ok": True}) as send_mock2:
        again = send_post_call_whatsapp(donor_phone, "call-xyz")
    assert again.get("skipped") is True
    send_mock2.assert_not_called()
