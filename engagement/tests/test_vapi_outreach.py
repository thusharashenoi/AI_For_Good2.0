"""Tests for Vapi context, call end, and inline outreach."""
from shared import dynamodb_client as db, scheduler
from shared.agent_tools import AgentTools
from shared import vapi_context
from lambdas.vapi_tools import handler as vapi_tools


def test_returning_donor_greeting():
    phone = "+919876501001"
    tools = AgentTools(phone, channel="voice")
    tools.complete_donor_registration(
        name="Rahul Kumar", age=30, weight=70, blood_group="O+", area="Madhapur")
    greeting = vapi_context.build_voice_greeting(phone)
    assert "Rahul" in greeting
    assert "welcome back" in greeting.lower() or "Good to hear" in greeting


def test_new_caller_greeting():
    phone = "+919876501002"
    greeting = vapi_context.build_voice_greeting(phone)
    assert "blood donor" in greeting.lower()


def test_get_state_returning_caller():
    phone = "+919876501003"
    AgentTools(phone, channel="voice").complete_donor_registration(
        name="Priya Sharma", age=28, weight=65, blood_group="A+", area="Gachibowli")
    state = AgentTools(phone, channel="voice").get_state()
    assert state["isReturningCaller"] is True
    assert state["callerFirstName"] == "Priya"
    assert state["isRegisteredDonor"] is True


def test_assistant_request_returns_overrides():
    phone = "+919876501004"
    AgentTools(phone, channel="voice").complete_donor_registration(
        name="Arjun Reddy", age=32, weight=72, blood_group="B+", area="Secunderabad")
    event = {"body": '{"message":{"type":"assistant-request","call":{"customer":{"number":"'
            + phone + '"}}}}'}
    resp = vapi_tools.handler(event)
    body = __import__("json").loads(resp["body"])
    assert "assistantId" in body or "assistant" in body
    overrides = body.get("assistantOverrides") or body.get("assistant") or {}
    fm = overrides.get("firstMessage") or body.get("assistant", {}).get("firstMessage", "")
    assert "Arjun" in fm


def test_registration_tool_schedules_end_call(monkeypatch):
    phone = "+919876501005"
    ended = []

    def fake_end(message, **kw):
        ended.append({"message": message, **kw})
        return {"ok": True}

    monkeypatch.setattr("shared.vapi_client.end_call", fake_end)
    event = {
        "body": __import__("json").dumps({
            "message": {
                "type": "tool-calls",
                "call": {
                    "id": "call-123",
                    "customer": {"number": phone},
                    "monitor": {"controlUrl": "https://example.com/control"},
                },
                "toolCallList": [{
                    "id": "tc1",
                    "name": "complete_donor_registration",
                    "arguments": {
                        "name": "End Call Test", "age": 30, "weight": 70,
                        "blood_group": "O+", "area": "Madhapur",
                        "donated_before": False,
                    },
                }],
            },
        }),
    }
    resp = vapi_tools.handler(event)
    results = __import__("json").loads(resp["body"])["results"]
    assert results[0].get("message")
    assert "Thank you" in results[0]["message"]
    assert len(ended) == 1
    assert ended[0].get("goodbye")
    donor = db.get_donor_by_phone(phone)
    assert donor["registrationStatus"] == "complete"


def test_book_appointment_schedules_end_call(monkeypatch):
    phone = "+919876501006"
    ended = []

    def fake_end(message, **kw):
        ended.append(kw)
        return {"ok": True}

    monkeypatch.setattr("shared.vapi_client.end_call", fake_end)
    tools = AgentTools(phone, channel="voice")
    tools.complete_donor_registration(
        name="Appt End Test", age=30, weight=70, blood_group="A+", area="Madhapur")
    req_id = db.new_id()
    db.save_request({
        "requestId": req_id, "patientId": db.new_id(), "patientPhone": "+919800000001",
        "patientName": "Baby K", "bloodGroup": "A+", "hospital": "NIAT Hospital",
        "city": "Bangalore", "status": "open", "requiredBy": "2026-06-10",
    })
    conv = db.get_conversation(phone)
    conv["activeRequestId"] = req_id
    db.save_conversation(conv)

    event = {
        "body": __import__("json").dumps({
            "message": {
                "type": "tool-calls",
                "call": {
                    "id": "call-appt-1",
                    "customer": {"number": phone},
                    "monitor": {"controlUrl": "https://example.com/control"},
                },
                "toolCallList": [{
                    "id": "tc-appt",
                    "name": "book_appointment",
                    "arguments": {"date": "2026-06-10", "time": "10:00 AM"},
                }],
            },
        }),
    }
    resp = vapi_tools.handler(event)
    results = __import__("json").loads(resp["body"])["results"]
    assert "Thank you" in results[0]["message"]
    assert "NIAT Hospital" in results[0]["message"]
    assert len(ended) == 1
    assert "Namaste" in ended[0]["goodbye"]


def test_outreach_greeting_when_awaiting_reply():
    phone = "+919876501020"
    AgentTools(phone, channel="voice").complete_donor_registration(
        name="Outreach Test", age=30, weight=70, blood_group="A+", area="Madhapur")
    vapi_context.ensure_outreach_primed(phone, "req-test-123")
    greeting = vapi_context.build_voice_greeting(phone)
    assert "urgently needs" in greeting.lower() or "can you donate" in greeting.lower()
    assert "blood donor" not in greeting.lower() or "patient or guardian" not in greeting.lower()


def test_outreach_overrides_from_variables():
    phone = "+919876501021"
    overrides = vapi_context.assistant_overrides(
        phone,
        variables={
            "requestId": "manual-outreach",
            "donorName": "Rahul",
            "bloodGroup": "O+",
            "hospital": "Apollo",
            "donorArea": "Hyderabad",
            "outreachMode": "true",
        },
    )
    fm = overrides["firstMessage"]
    assert "Rahul" in fm
    assert "Apollo" in fm
    system = overrides["model"]["messages"][0]["content"]
    assert "OUTREACH CALL MODE" in system
    assert "complete_donor_registration" in system


def test_get_state_outreach_voice_summary():
    from shared.voice_speech import format_tool_result_for_voice

    summary = format_tool_result_for_voice("get_state", {"awaitingOutreachReply": True, "outreachMode": True})
    assert "Outreach call" in summary
    assert "register" in summary.lower()


def test_inline_outreach_after_patient_request():
    donor_phone = "+919876501010"
    patient_phone = "+919876501011"
    AgentTools(donor_phone, channel="voice").complete_donor_registration(
        name="Outreach Donor", age=30, weight=70, blood_group="A+", area="Madhapur")
    pt = AgentTools(patient_phone, channel="whatsapp")
    result = pt.raise_blood_request(
        patient_name="Needy Baby", blood_group="A+", units=1,
        hospital="Apollo", required_by="tomorrow")
    assert result["ok"] is True
    req = db.get_request(result["requestId"])
    assert req.get("rankedDonorQueue")
    assert len(req["rankedDonorQueue"]) >= 1
    assert any(e.get("inline") for e in scheduler.STARTED_EXECUTIONS)
