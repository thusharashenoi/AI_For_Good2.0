"""Tests for seamless voice appointment booking pipeline."""
from unittest.mock import patch

from shared import dynamodb_client as db
from shared.agent_tools import AgentTools
from shared.voice_booking import finalize_voice_booking, prime_proposed_appointment
from lambdas.vapi_tools import handler as vapi_tools


def _seed_outreach(donor_phone: str, patient_phone: str) -> str:
    AgentTools(donor_phone, channel="voice").complete_donor_registration(
        name="Pipeline Donor", age=30, weight=70, blood_group="A+", area="Madhapur")
    req = AgentTools(patient_phone, channel="whatsapp").raise_blood_request(
        patient_name="Baby", blood_group="A+", units=1,
        hospital="Apollo", required_by="tomorrow")
    conv = db.get_conversation(donor_phone)
    conv["activeRequestId"] = req["requestId"]
    conv["awaitingOutreachReply"] = True
    db.save_conversation(conv)
    return req["requestId"]


def test_prime_proposed_appointment_on_outreach():
    donor_phone = "+919483399667"
    request_id = _seed_outreach(donor_phone, "+919483399667")
    proposed = prime_proposed_appointment(donor_phone)
    assert proposed
    assert proposed["hospital"] == "Apollo"
    assert not proposed.get("date")
    assert not proposed.get("time")
    assert not proposed.get("spokenWhen")
    assert proposed["bloodDueSpoken"]
    assert proposed["availabilityWindowSpoken"]
    assert proposed["availabilityConfirmed"] is False
    assert proposed.get("askTimeOnly") is True
    assert "tomorrow" in (proposed.get("slotQuestionSpoken") or "")
    assert proposed["requestId"] == request_id


def test_check_eligibility_returns_proposed_slot():
    donor_phone = "+919483399667"
    _seed_outreach(donor_phone, "+919483399667")
    tools = AgentTools(donor_phone, channel="voice")
    tools.save_eligibility_answers(
        diabetes_insulin=False, tattoo_6mo=False, fever_or_antibiotics_2wk=False,
        pregnant_or_breastfeeding=False, malaria_travel_3mo=False)
    result = tools.check_eligibility()
    assert result.get("eligible") is True
    assert result.get("proposedAppointment", {}).get("hospital") == "Apollo"


def test_confirm_slot_requires_time():
    donor_phone = "+919876502060"
    request_id = _seed_outreach(donor_phone, "+919876502061")
    tools = AgentTools(donor_phone, channel="voice")
    slot = tools.confirm_appointment_slot(date="tomorrow")
    assert slot.get("ok") is False
    assert slot.get("reason") == "missing_time"
    book = tools.book_appointment(request_id=request_id)
    assert book.get("ok") is False
    assert book.get("reason") == "availability_not_confirmed"


def test_book_requires_availability_confirmation():
    donor_phone = "+919483399667"
    request_id = _seed_outreach(donor_phone, "+919483399667")
    tools = AgentTools(donor_phone, channel="voice")
    book = tools.book_appointment(request_id=request_id)
    assert book.get("ok") is False
    assert book.get("reason") == "availability_not_confirmed"
    assert book.get("bloodDueSpoken")


def test_full_voice_booking_pipeline():
    donor_phone = "+919483399667"
    request_id = _seed_outreach(donor_phone, "+919483399667")
    tools = AgentTools(donor_phone, channel="voice")
    tools.save_eligibility_answers(
        diabetes_insulin=False, tattoo_6mo=False, fever_or_antibiotics_2wk=False,
        pregnant_or_breastfeeding=False, malaria_travel_3mo=False)
    tools.check_eligibility()
    tools.confirm_appointment_slot(time="10:00 AM")

    with patch("shared.periskope_client.send_message", return_value={"ok": True}) as send_mock, \
         patch("shared.vapi_client.hangup_after_goodbye", return_value={"ok": True}) as hangup_mock:
        event = {
            "body": __import__("json").dumps({
                "message": {
                    "type": "tool-calls",
                    "call": {
                        "id": "call-pipeline",
                        "customer": {"number": donor_phone},
                        "monitor": {"controlUrl": "https://example.com/control"},
                    },
                    "toolCallList": [{
                        "id": "tc-book",
                        "name": "book_appointment",
                        "arguments": {"request_id": request_id},
                    }],
                },
            }),
        }
        resp = vapi_tools.handler(event)

    results = __import__("json").loads(resp["body"])["results"]
    assert "do not speak" in results[0]["result"].lower()
    assert "lifesaver" in results[0]["message"].lower()
    assert "Apollo" in results[0]["message"]
    donor_calls = [c for c in send_mock.call_args_list if c[0][0] == donor_phone]
    assert donor_calls
    body = donor_calls[-1][0][1]
    assert "confirmed" in body.lower()
    assert "Apollo" in body
    hangup_mock.assert_called_once()
    assert hangup_mock.call_args.kwargs.get("delay_after_speak_seconds") == 2.0
    conv = db.get_conversation(donor_phone)
    assert conv.get("appointmentWhatsappForId")
    assert conv.get("activeAppointmentId")


def test_finalize_voice_booking_idempotent_whatsapp():
    donor_phone = "+919483399667"
    request_id = _seed_outreach(donor_phone, "+919483399667")
    tools = AgentTools(donor_phone, channel="voice")
    tools.confirm_appointment_slot(time="10:00 AM")
    book = tools.book_appointment(request_id=request_id)
    entry = {"toolCallId": "x", "result": "ok"}
    message = {"call": {"id": "c1", "monitor": {"controlUrl": "https://example.com/control"}}}

    with patch("shared.periskope_client.send_message", return_value={"ok": True}) as send_mock, \
         patch("shared.vapi_client.hangup_after_goodbye", return_value={"ok": True}):
        finalize_voice_booking(message, entry, donor_phone, book, "Pipeline Donor")
        again = finalize_voice_booking(message, entry, donor_phone, book, "Pipeline Donor")

    assert send_mock.call_count == 1
    assert again["whatsapp"].get("skipped") is True
