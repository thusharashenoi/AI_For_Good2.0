"""End-to-end conversation flow tests (LOCAL_MODE, in-memory store)."""
from shared import dynamodb_client as db
from shared import db_schema as schema
from shared import scheduler
from lambdas.whatsapp_webhook import flows
from conftest import DEMO_PHONE


def _run(phone, *messages):
    out = []
    for m in messages:
        out.append(flows.handle_message(phone, m))
    return out


def test_donor_registration_complete():
    phone = DEMO_PHONE
    _run(phone, "Hi", "1", "Rahul Kumar", "30", "70", "O+", "Madhapur", "No, First Time")
    donor = db.get_donor_by_phone(phone)
    assert donor is not None
    schema.assert_donor_complete(donor)
    assert donor["bloodGroup"] == "O+"
    assert donor["consentGiven"] is True
    assert donor["eligibilityStatus"] == "eligible"


def test_underage_goes_to_waitlist():
    phone = DEMO_PHONE
    replies = _run(phone, "Hi", "1", "Tiny Tim", "15")
    last = replies[-1][0]
    assert "18" in last
    conv = db.get_conversation(phone)
    assert conv["state"] == "ENDED"
    assert db.get_item(db.config.table_names()["waitlist"], "phone", phone) is not None


def test_underweight_partial_save():
    phone = DEMO_PHONE
    _run(phone, "Hi", "1", "Light Weight", "25", "40")
    donor = db.get_donor_by_phone(phone)
    schema.assert_donor_partial(donor)


def test_blood_group_unknown_schedules_followup():
    phone = DEMO_PHONE
    _run(phone, "Hi", "1", "Dont Know", "28", "60", "don't know")
    conv = db.get_conversation(phone)
    assert conv["followUpScheduled"] is True
    assert any(s["target"] for s in scheduler.SCHEDULED)


def test_patient_request_starts_outreach():
    phone = DEMO_PHONE
    _run(phone, "Hi", "2", "Baby Anjali", "6", "B+", "2", "Rainbow Hospital", "tomorrow")
    reqs = db.scan(db.config.table_names()["requests"])
    assert len(reqs) == 1
    schema.assert_request(reqs[0])
    assert reqs[0]["bloodGroup"] == "B+"
    assert reqs[0]["unitsNeeded"] == 2
    assert len(scheduler.STARTED_EXECUTIONS) == 1


def test_language_switch_to_hindi():
    phone = DEMO_PHONE
    flows.handle_message(phone, "Hi")
    replies = flows.handle_message(phone, "hindi mein")
    conv = db.get_conversation(phone)
    assert conv["language"] == "hi"
    # The welcome re-prompt should be in Hindi (Devanagari present).
    assert any("\u0900" <= ch <= "\u097f" for ch in "".join(replies))


def test_full_match_to_appointment():
    # Register an eligible A+ donor.
    donor_phone = DEMO_PHONE
    _run(donor_phone, "Hi", "1", "Donor One", "30", "70", "A+", "Madhapur", "No, First Time")
    # Patient raises an A+ request.
    patient_phone = DEMO_PHONE
    _run(patient_phone, "Hi", "2", "Patient One", "10", "A+", "1", "Apollo", "in 3 days")
    req = db.scan(db.config.table_names()["requests"])[0]

    # Simulate outreach: prime donor conversation as send_whatsapp would.
    conv = db.get_conversation(donor_phone)
    conv["awaitingOutreachReply"] = True
    conv["activeRequestId"] = req["requestId"]
    db.save_conversation(conv)

    # Donor says YES -> eligibility quick-check -> all NO -> booking -> confirm.
    flows.handle_message(donor_phone, "YES")     # start elig check
    flows.handle_message(donor_phone, "No")      # diabetes
    flows.handle_message(donor_phone, "No")      # tattoo
    flows.handle_message(donor_phone, "No")      # fever
    flows.handle_message(donor_phone, "No")      # pregnant
    flows.handle_message(donor_phone, "No")      # malaria -> booking prompt
    flows.handle_message(donor_phone, "Confirm")  # confirm appointment

    appts = db.scan(db.config.table_names()["appointments"])
    assert len(appts) == 1
    schema.assert_appointment(appts[0])
    assert appts[0]["status"] == "scheduled"
    updated_req = db.get_request(req["requestId"])
    assert updated_req["status"] == "confirmed"


def test_ineligible_donor_deferred():
    donor_phone = DEMO_PHONE
    _run(donor_phone, "Hi", "1", "Defer Donor", "30", "70", "O+", "Madhapur", "No, First Time")
    conv = db.get_conversation(donor_phone)
    conv["awaitingOutreachReply"] = True
    db.save_conversation(conv)
    flows.handle_message(donor_phone, "YES")   # start check
    flows.handle_message(donor_phone, "Yes")   # diabetes = YES -> permanent defer
    flows.handle_message(donor_phone, "No")
    flows.handle_message(donor_phone, "No")
    flows.handle_message(donor_phone, "No")
    replies = flows.handle_message(donor_phone, "No")
    donor = db.get_donor_by_phone(donor_phone)
    assert donor["eligibilityStatus"] == "deferred"


def test_delete_my_data():
    phone = DEMO_PHONE
    _run(phone, "Hi", "1", "Delete Me", "30", "70", "O+", "Madhapur", "No, First Time")
    assert db.get_donor_by_phone(phone) is not None
    flows.handle_message(phone, "DELETE MY DATA")
    assert db.get_donor_by_phone(phone) is None
    assert db.get_conversation(phone) is None
