"""Tests for voice outreach appointment booking."""
from unittest.mock import patch

from shared import dynamodb_client as db
from shared.agent_tools import AgentTools
from shared.voice_booking import prime_proposed_appointment
from lambdas.vapi_tools import handler as vapi_tools
from conftest import DEMO_PHONE


def _seed_outreach(donor_phone: str, patient_phone: str) -> str:
    AgentTools(donor_phone, channel="voice").complete_donor_registration(
        name="Outreach Donor", age=30, weight=70, blood_group="A+", area="Madhapur")
    result = AgentTools(patient_phone, channel="whatsapp").raise_blood_request(
        patient_name="Baby", blood_group="A+", units=1,
        hospital="Apollo", required_by="tomorrow")
    assert result["ok"] is True
    request_id = result["requestId"]
    conv = db.get_conversation(donor_phone)
    conv["activeRequestId"] = request_id
    conv["awaitingOutreachReply"] = True
    db.save_conversation(conv)
    return request_id


def test_prime_proposed_appointment_on_outreach():
    donor_phone = DEMO_PHONE
    patient_phone = DEMO_PHONE
    _seed_outreach(donor_phone, patient_phone)
    proposed = prime_proposed_appointment(donor_phone)
    assert proposed
    assert proposed["hospital"] == "Apollo"
    assert not proposed.get("date")
    assert not proposed.get("time")
    assert not proposed.get("spokenWhen")
    assert proposed["bloodDueSpoken"]
    assert proposed["availabilityWindowSpoken"]
    assert proposed["availabilityConfirmed"] is False


def test_check_eligibility_returns_proposed_slot():
    donor_phone = DEMO_PHONE
    _seed_outreach(donor_phone, DEMO_PHONE)
    tools = AgentTools(donor_phone, channel="voice")
    tools.save_eligibility_answers(
        diabetes_insulin=False, tattoo_6mo=False,
        fever_or_antibiotics_2wk=False, pregnant_or_breastfeeding=False,
        malaria_travel_3mo=False)
    result = tools.check_eligibility()
    assert result.get("eligible") is True
    assert result.get("proposedAppointment", {}).get("hospital") == "Apollo"


def test_confirm_slot_requires_time():
    donor_phone = DEMO_PHONE
    request_id = _seed_outreach(donor_phone, DEMO_PHONE)
    tools = AgentTools(donor_phone, channel="voice")
    slot = tools.confirm_appointment_slot(date="tomorrow")
    assert slot.get("ok") is False
    assert slot.get("reason") == "missing_time"
    book = tools.book_appointment(request_id=request_id)
    assert book.get("ok") is False
    assert book.get("reason") == "availability_not_confirmed"


def test_confirm_combined_date_time_phrase():
    donor_phone = DEMO_PHONE
    request_id = _seed_outreach(donor_phone, DEMO_PHONE)
    tools = AgentTools(donor_phone, channel="voice")
    slot = tools.confirm_appointment_slot(date="Monday 9 AM, 9th June")
    assert slot.get("ok") is True
    assert slot.get("time") == "9:00 AM"
    assert slot.get("date") == "2026-06-09"
    tools.save_eligibility_answers(
        diabetes_insulin=False, tattoo_6mo=False,
        fever_or_antibiotics_2wk=False, pregnant_or_breastfeeding=False,
        malaria_travel_3mo=False)
    tools.check_eligibility()
    book = tools.book_appointment(request_id=request_id)
    assert book.get("ok") is True
    assert book.get("time") == "9:00 AM"


def test_book_requires_availability_confirmation():
    donor_phone = DEMO_PHONE
    request_id = _seed_outreach(donor_phone, DEMO_PHONE)
    tools = AgentTools(donor_phone, channel="voice")
    book = tools.book_appointment(request_id=request_id)
    assert book.get("ok") is False
    assert book.get("reason") == "availability_not_confirmed"


def test_full_voice_booking_pipeline(monkeypatch):
    donor_phone = DEMO_PHONE
    request_id = _seed_outreach(donor_phone, DEMO_PHONE)
    tools = AgentTools(donor_phone, channel="voice")
    tools.confirm_appointment_slot(time="2:00 PM")
    tools.save_eligibility_answers(
        diabetes_insulin=False, tattoo_6mo=False,
        fever_or_antibiotics_2wk=False, pregnant_or_breastfeeding=False,
        malaria_travel_3mo=False)
    tools.check_eligibility()

    hangup_mock = patch("shared.vapi_client.hangup_after_goodbye", return_value={"ok": True})
    send_mock = patch("shared.periskope_client.send_message", return_value={"ok": True})
    with hangup_mock as hangup, send_mock as send:
        event = {
            "body": __import__("json").dumps({
                "message": {
                    "type": "tool-calls",
                    "call": {
                        "id": "call-full-1",
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
        book_entry = results[-1]
        assert "lifesaver" in book_entry["message"].lower()
        assert "Apollo" in book_entry["message"]
        donor_calls = [c for c in send.call_args_list if c[0][0] == donor_phone]
        assert donor_calls
        body = donor_calls[-1][0][1]
        assert "confirmed" in body.lower()
        assert "Apollo" in body
        hangup.assert_called_once()
