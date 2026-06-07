"""DB field completeness tests for the AgentTools path (Vapi voice + Bedrock WhatsApp)."""
from shared import dynamodb_client as db
from shared import db_schema as schema
from shared.agent_tools import AgentTools
from lambdas.whatsapp_webhook import flows


def _run_fsm(phone, *messages):
    for m in messages:
        flows.handle_message(phone, m)


def test_voice_donor_registration_fills_all_fields():
    phone = "+919876500001"
    tools = AgentTools(phone, channel="voice")
    tools.set_state(state="COLLECTING_NAME", user_type="donor", language="en")
    result = tools.complete_donor_registration(
        name="Voice Donor", age=30, weight=65, blood_group="O+",
        area="Madhapur", donated_before=False)
    assert result["ok"] is True

    donor = db.get_donor_by_phone(phone)
    schema.assert_donor_complete(donor)
    assert donor["registrationChannel"] == "voice"
    assert donor["preferredChannel"] == "voice"
    assert donor["bloodGroup"] == "O+"
    assert donor["area"] == "Madhapur"
    assert donor["city"] == "Hyderabad"
    assert donor["lat"] is not None
    assert donor["lng"] is not None

    conv = db.get_conversation(phone)
    schema.assert_conversation(conv)
    assert conv["donorId"] == donor["donorId"]
    assert conv["state"] == "REGISTRATION_COMPLETE"
    assert conv["userType"] == "donor"
    assert conv["channel"] == "voice"


def test_whatsapp_donor_registration_fills_all_fields():
    phone = "+919876500002"
    tools = AgentTools(phone, channel="whatsapp")
    tools.complete_donor_registration(
        name="WA Donor", age=28, weight=70, blood_group="A+",
        area="Gachibowli", donated_before=True, last_donation="March 2024")
    donor = db.get_donor_by_phone(phone)
    schema.assert_donor_complete(donor)
    assert donor["registrationChannel"] == "whatsapp"
    assert donor["lastDonationDate"] is not None


def test_partial_donor_fills_required_fields():
    phone = "+919876500003"
    tools = AgentTools(phone, channel="voice")
    tools.complete_donor_registration(
        name="Partial Donor", age=25, weight=40, blood_group="B+", area="Secunderabad")
    donor = db.get_donor_by_phone(phone)
    schema.assert_donor_partial(donor)
    assert donor["preferredChannel"] == "voice"
    assert donor["city"] == "Hyderabad"


def test_underage_waitlist_fills_fields():
    phone = "+919876500004"
    tools = AgentTools(phone, channel="whatsapp")
    tools.set_state(language="hi")
    result = tools.complete_donor_registration(name="Young One", age=15, weight=50)
    assert result["ok"] is False
    entry = db.get_item(db.config.table_names()["waitlist"], "phone", phone)
    schema.assert_waitlist(entry, reason="underage")
    assert entry["language"] == "hi"
    assert entry["age"] == 15


def test_patient_request_fills_all_fields():
    phone = "+919876500005"
    tools = AgentTools(phone, channel="whatsapp")
    tools.set_state(language="te")
    result = tools.raise_blood_request(
        patient_name="Baby Rao", patient_age=6, blood_group="B+",
        units=2, hospital="Rainbow Hospital", required_by="tomorrow")
    assert result["ok"] is True

    patient = db.get_patient_by_phone(phone)
    schema.assert_patient(patient)
    assert patient["registrationChannel"] == "whatsapp"
    assert patient["preferredLanguage"] == "te"
    assert patient["age"] == 6

    req = db.get_request(result["requestId"])
    schema.assert_request(req)
    assert req["bloodGroup"] == "B+"
    assert req["unitsNeeded"] == 2
    assert req["hospitalLat"] is not None
    assert req["hospitalLng"] is not None

    conv = db.get_conversation(phone)
    schema.assert_conversation(conv)
    assert conv["patientId"] == patient["patientId"]
    assert conv["activeRequestId"] == req["requestId"]


def test_book_appointment_fills_all_fields():
    donor_phone = "+919876500006"
    patient_phone = "+919876500007"
    dt = AgentTools(donor_phone, channel="voice")
    dt.complete_donor_registration(
        name="Appt Donor", age=32, weight=72, blood_group="O+", area="LB Nagar")
    pt = AgentTools(patient_phone, channel="whatsapp")
    req_result = pt.raise_blood_request(
        patient_name="Needy Patient", blood_group="O+", units=1,
        hospital="Apollo Hospital", required_by="in 2 days")
    req_id = req_result["requestId"]

    conv = db.get_conversation(donor_phone)
    conv["activeRequestId"] = req_id
    db.save_conversation(conv)

    dt.confirm_appointment_slot(date="2026-06-10", time="11:00 AM")
    book = dt.book_appointment(request_id=req_id, date="2026-06-10", time="11:00 AM")
    assert book["ok"] is True

    appt = db.get_appointment(book["appointmentId"])
    schema.assert_appointment(appt)
    assert appt["bloodGroup"] == "O+"
    assert appt["channel"] == "voice"
    assert appt["donorName"] == "Appt Donor"
    assert appt["patientName"] == "Needy Patient"


def test_fsm_donor_matches_schema():
    phone = "+919876500008"
    _run_fsm(phone, "Hi", "1", "FSM Donor", "30", "70", "AB+", "Begumpet", "No, First Time")
    donor = db.get_donor_by_phone(phone)
    schema.assert_donor_complete(donor)
    assert donor["medicalFlags"] == {}


def test_fsm_appointment_has_blood_group():
    donor_phone = "+919876500009"
    patient_phone = "+919876500010"
    _run_fsm(donor_phone, "Hi", "1", "Match Donor", "30", "70", "A+", "Madhapur", "No, First Time")
    _run_fsm(patient_phone, "Hi", "2", "Match Patient", "8", "A+", "1", "NIMS", "in 3 days")
    req = db.scan(db.config.table_names()["requests"])[0]
    conv = db.get_conversation(donor_phone)
    conv["awaitingOutreachReply"] = True
    conv["activeRequestId"] = req["requestId"]
    db.save_conversation(conv)
    _run_fsm(donor_phone, "YES", "No", "No", "No", "No", "No", "Confirm")
    appt = db.scan(db.config.table_names()["appointments"])[0]
    schema.assert_appointment(appt)
    assert appt["bloodGroup"] == "A+"
