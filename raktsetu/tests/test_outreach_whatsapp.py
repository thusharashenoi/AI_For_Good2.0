"""Tests for WhatsApp-first outreach and voice escalation after no reply."""
from unittest.mock import patch

from shared import dynamodb_client as db
from shared.agent_tools import AgentTools
from shared.outreach import (
    _send_to_top_donor,
    escalate_to_voice_if_no_reply,
    send_appointment_confirmation_whatsapp,
    send_post_call_whatsapp,
)


def _seed_request_with_donor(donor_phone: str, patient_phone: str) -> str:
    AgentTools(donor_phone, channel="voice").complete_donor_registration(
        name="Voice Donor", age=30, weight=70, blood_group="A+", area="Madhapur")
    with patch("shared.scheduler.start_outreach", return_value={}):
        result = AgentTools(patient_phone, channel="whatsapp").raise_blood_request(
            patient_name="Baby", blood_group="A+", units=1,
            hospital="Apollo", required_by="tomorrow")
    assert result["ok"] is True
    return result["requestId"]


def test_whatsapp_outreach_schedules_voice_not_immediate():
    donor_phone = "+919876501030"
    patient_phone = "+919876501031"
    request_id = _seed_request_with_donor(donor_phone, patient_phone)

    with patch("shared.outreach._send_whatsapp_template", return_value=True) as wa_mock, \
         patch("shared.scheduler.schedule_outreach_voice_escalation", return_value={"delaySeconds": 15}) as sched_mock, \
         patch("lambdas.trigger_voice.handler.handler") as voice_mock:
        result = _send_to_top_donor(request_id)

    assert result.get("status") == "outreach_sent"
    assert result.get("whatsappSent") is True
    assert result.get("voicePlaced") is False
    assert result.get("voiceEscalationScheduled") is True
    wa_mock.assert_called_once()
    sched_mock.assert_called_once()
    voice_mock.assert_not_called()
    conv = db.get_conversation(donor_phone)
    assert conv.get("outreachEscalationToken")
    assert conv.get("awaitingOutreachReply") is True


def test_whatsapp_first_on_fresh_request():
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
    with patch("shared.outreach._send_whatsapp_template", return_value=True) as wa_mock, \
         patch("shared.scheduler.schedule_outreach_voice_escalation", return_value={"delaySeconds": 15}), \
         patch("lambdas.trigger_voice.handler.handler", return_value=fake_voice) as voice_mock:
        outreach = _send_to_top_donor(request_id)

    assert outreach.get("whatsappSent") is True
    assert outreach.get("voicePlaced") is False
    wa_mock.assert_called_once()
    voice_mock.assert_not_called()


def test_escalate_to_voice_if_no_reply():
    donor_phone = "+919876501036"
    patient_phone = "+919876501037"
    request_id = _seed_request_with_donor(donor_phone, patient_phone)
    donor = db.get_donor_by_phone(donor_phone)
    conv = db.get_conversation(donor_phone)
    conv["outreachEscalationToken"] = "tok-123"
    conv["awaitingOutreachReply"] = True
    db.save_conversation(conv)
    req = db.get_request(request_id)
    req["assignedDonors"] = [{
        "donorId": donor["donorId"],
        "status": "outreach_sent",
        "channel": "whatsapp",
    }]
    db.save_request(req)

    fake_voice = {"ok": True, "callSid": "call-esc", "voicePlaced": True, "status": "calling"}
    with patch("lambdas.trigger_voice.handler.handler", return_value=fake_voice) as voice_mock:
        result = escalate_to_voice_if_no_reply(request_id, donor["donorId"], token="tok-123")

    assert result.get("voicePlaced") is True
    voice_mock.assert_called_once()
    conv = db.get_conversation(donor_phone)
    assert conv.get("voiceOutreachPlaced") is True
    req = db.get_request(request_id)
    assert req["assignedDonors"][0]["status"] == "voice_outreach"


def test_escalate_after_prime_clears_stale_voice_flag():
    donor_phone = "+919876501039"
    request_id = _seed_request_with_donor(donor_phone, "+919876501049")
    donor = db.get_donor_by_phone(donor_phone)
    conv = db.get_conversation(donor_phone)
    conv["voiceOutreachPlaced"] = True
    conv["voiceOutreachRequestId"] = "old-request"
    conv["activeVoiceCallId"] = "old-call-id"
    conv["outreachEscalationToken"] = "tok-stale"
    db.save_conversation(conv)
    req = db.get_request(request_id)
    req["assignedDonors"] = [{
        "donorId": donor["donorId"],
        "status": "outreach_sent",
        "channel": "whatsapp",
    }]
    db.save_request(req)

    from shared.outreach import _prime_donor_conversation
    _prime_donor_conversation(donor, request_id)
    conv = db.get_conversation(donor_phone)
    assert conv.get("voiceOutreachPlaced") is False
    token = conv.get("outreachEscalationToken") or "tok-stale"
    conv["outreachEscalationToken"] = token
    db.save_conversation(conv)

    fake_voice = {"ok": True, "callSid": "call-new", "callId": "call-new", "status": "calling"}
    with patch("lambdas.trigger_voice.handler.handler", return_value=fake_voice) as voice_mock:
        result = escalate_to_voice_if_no_reply(request_id, donor["donorId"], token=token)

    assert result.get("voicePlaced") is True
    voice_mock.assert_called_once()


def test_escalate_skipped_if_donor_replied_on_whatsapp():
    donor_phone = "+919876501038"
    request_id = _seed_request_with_donor(donor_phone, "+919876501039")
    donor = db.get_donor_by_phone(donor_phone)
    conv = db.get_conversation(donor_phone)
    conv["outreachEscalationToken"] = "tok-456"
    conv["awaitingOutreachReply"] = False
    db.save_conversation(conv)
    req = db.get_request(request_id)
    req["assignedDonors"] = [{"donorId": donor["donorId"], "status": "declined"}]
    db.save_request(req)

    with patch("lambdas.trigger_voice.handler.handler") as voice_mock:
        result = escalate_to_voice_if_no_reply(request_id, donor["donorId"], token="tok-456")

    assert result.get("reason") == "already_replied"
    voice_mock.assert_not_called()


def test_appointment_confirmation_whatsapp_on_booking():
    donor_phone = "+919876501040"
    request_id = _seed_request_with_donor(donor_phone, "+919876501041")
    conv = db.get_conversation(donor_phone)
    conv["activeRequestId"] = request_id
    conv["awaitingOutreachReply"] = True
    db.save_conversation(conv)
    AgentTools(donor_phone, channel="voice").confirm_appointment_slot(time="10:00 AM")

    with patch("shared.periskope_client.send_message", return_value={"ok": True}) as send_mock, \
         patch("shared.vapi_client.hangup_after_goodbye", return_value={"ok": True}):
        event = {
            "body": __import__("json").dumps({
                "message": {
                    "type": "tool-calls",
                    "call": {
                        "id": "call-wa-book",
                        "customer": {"number": donor_phone},
                        "monitor": {"controlUrl": "https://example.com/control"},
                    },
                    "toolCallList": [{
                        "id": "tc-wa",
                        "name": "book_appointment",
                        "arguments": {"request_id": request_id},
                    }],
                },
            }),
        }
        from lambdas.vapi_tools import handler as vapi_tools
        vapi_tools.handler(event)

    donor_calls = [c for c in send_mock.call_args_list if c[0][0] == donor_phone]
    assert len(donor_calls) == 1
    body = donor_calls[0][0][1]
    assert "confirmed" in body.lower()
    assert "Apollo" in body

    with patch("shared.periskope_client.send_message", return_value={"ok": True}) as send_mock2:
        appt_id = db.get_conversation(donor_phone).get("activeAppointmentId")
        again = send_appointment_confirmation_whatsapp(donor_phone, appt_id)
    assert again.get("skipped") is True
    send_mock2.assert_not_called()


def test_post_call_whatsapp_uses_spoken_when_slot():
    donor_phone = "+919876501062"
    request_id = _seed_request_with_donor(donor_phone, "+919876501063")
    conv = db.get_conversation(donor_phone)
    conv["activeRequestId"] = request_id
    conv["voiceOutreachPlaced"] = True
    ctx = conv.setdefault("contextData", {})
    ctx["proposedAppointment"] = {
        "hospital": "NIAT Hospital",
        "date": "2026-06-08",
        "time": "3:00 PM",
        "spokenWhen": "tomorrow at 3 in the afternoon",
        "availabilityConfirmed": False,
    }
    db.save_conversation(conv)

    with patch("shared.periskope_client.send_message", return_value={"ok": True}) as send_mock:
        result = send_post_call_whatsapp(donor_phone, "call-spoken")

    assert result["ok"] is True
    assert result.get("hadAppointment") is True
    body = send_mock.call_args[0][1]
    assert "confirmed" in body.lower()
    assert "NIAT" in body


def test_post_call_whatsapp_uses_proposed_slot_when_no_appt_record():
    donor_phone = "+919876501060"
    request_id = _seed_request_with_donor(donor_phone, "+919876501061")
    conv = db.get_conversation(donor_phone)
    conv["activeRequestId"] = request_id
    conv["voiceOutreachPlaced"] = True
    ctx = conv.setdefault("contextData", {})
    ctx["proposedAppointment"] = {
        "hospital": "Apollo",
        "date": "2026-06-08",
        "time": "2:00 PM",
        "availabilityConfirmed": True,
    }
    db.save_conversation(conv)

    with patch("shared.periskope_client.send_message", return_value={"ok": True}) as send_mock:
        result = send_post_call_whatsapp(donor_phone, "call-proposed")

    assert result["ok"] is True
    assert result.get("hadAppointment") is True
    body = send_mock.call_args[0][1]
    assert "confirmed" in body.lower()
    assert "Apollo" in body
    assert "follow up with next steps soon" not in body


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
