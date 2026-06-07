"""RaktSetu conversational FSM (FLOWS 1-5).

`handle_message(phone, text, channel)` is the single entry point. It loads the
conversation from DynamoDB, advances the finite-state machine based on the
inbound text, performs backend writes, and returns a list of reply strings to
send back to the user. Side-effects to *other* parties (e.g. notifying a
patient) are sent directly via the Twilio client.

Design notes:
- Quick-reply / YES-NO answers are handled here with zero Bedrock inference
  (low latency). Free-text that needs NLU (dates, names) uses bedrock_client.
- Every state transition persists contextData so the conversation survives
  cold starts and follow-ups can resume mid-flow.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from shared import (bedrock_client, config, dynamodb_client as db,
                    eligibility_rules as rules, geocoding, i18n, scheduler,
                    twilio_client, whatsapp_ui)

from . import validators as v

logger = logging.getLogger("raktsetu.flows")

# --- FSM states -------------------------------------------------------------
UNKNOWN = "UNKNOWN"
# Donor (FLOW 1)
COLLECTING_NAME = "COLLECTING_NAME"
COLLECTING_AGE = "COLLECTING_AGE"
COLLECTING_WEIGHT = "COLLECTING_WEIGHT"
COLLECTING_BLOOD_GROUP = "COLLECTING_BLOOD_GROUP"
COLLECTING_AREA = "COLLECTING_AREA"
COLLECTING_LAST_DONATION = "COLLECTING_LAST_DONATION"
COLLECTING_LAST_DONATION_DATE = "COLLECTING_LAST_DONATION_DATE"
REGISTRATION_COMPLETE = "REGISTRATION_COMPLETE"
ENDED = "ENDED"
# Patient (FLOW 3)
COLLECTING_PATIENT_NAME = "COLLECTING_PATIENT_NAME"
COLLECTING_PATIENT_AGE = "COLLECTING_PATIENT_AGE"
COLLECTING_BLOOD_GROUP_NEEDED = "COLLECTING_BLOOD_GROUP_NEEDED"
COLLECTING_UNITS = "COLLECTING_UNITS"
COLLECTING_HOSPITAL = "COLLECTING_HOSPITAL"
COLLECTING_REQUIRED_BY = "COLLECTING_REQUIRED_BY"
REQUEST_RAISED = "REQUEST_RAISED"
# Eligibility quick-check + booking (FLOW 5)
ELIG_DIABETES = "ELIG_DIABETES"
ELIG_TATTOO = "ELIG_TATTOO"
ELIG_FEVER = "ELIG_FEVER"
ELIG_PREGNANT = "ELIG_PREGNANT"
ELIG_MALARIA = "ELIG_MALARIA"
BOOKING_APPOINTMENT = "BOOKING_APPOINTMENT"
DIFFERENT_DATE = "DIFFERENT_DATE"
# Cancellation (FLOW 7)
CANCEL_REASON = "CANCEL_REASON"

ELIG_SEQUENCE = [
    (ELIG_DIABETES, "diabetes", "ASK_RECENT_TATTOO", ELIG_TATTOO),
    (ELIG_TATTOO, "recentTattoo", "ASK_RECENT_FEVER", ELIG_FEVER),
    (ELIG_FEVER, "recentFever", "ASK_PREGNANT", ELIG_PREGNANT),
    (ELIG_PREGNANT, "pregnant", "ASK_MALARIA_TRAVEL", ELIG_MALARIA),
    (ELIG_MALARIA, "malariaTravel", None, BOOKING_APPOINTMENT),
]


# ---------------------------------------------------------------------------
# Conversation helpers
# ---------------------------------------------------------------------------
def _load_or_create(phone: str, channel: str) -> Dict:
    conv = db.get_conversation(phone)
    if conv is None:
        conv = {
            "phone_number": phone,
            "conversationId": db.new_id(),
            "channel": channel,
            "state": UNKNOWN,
            "language": i18n.DEFAULT_LANG,
            "userType": "unknown",
            "contextData": {},
            "messageHistory": [],
            "followUpScheduled": False,
            "followUpAttempts": 0,
        }
    return conv


def _ctx(conv: Dict) -> Dict:
    return conv.setdefault("contextData", {})


def _push_history(conv: Dict, role: str, text: str) -> None:
    hist = conv.setdefault("messageHistory", [])
    hist.append({"role": role, "text": text, "at": db.now_iso()})
    conv["messageHistory"] = hist[-20:]


def _reply(conv: Dict, *messages: str) -> List[str]:
    for m in messages:
        _push_history(conv, "assistant", m)
    db.save_conversation(conv)
    return list(messages)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def handle_message(phone: str, text: str, channel: str = "whatsapp") -> List[str]:
    text = (text or "").strip()
    conv = _load_or_create(phone, channel)
    conv["lastUserReplyAt"] = db.now_iso()
    _push_history(conv, "user", text)
    lang = conv.get("language", i18n.DEFAULT_LANG)

    # Bridge mobilization YES/NO — always wins over in-progress eligibility flows.
    if conv.get("awaitingOutreachReply"):
        return _handle_outreach_reply(conv, text)

    # Global commands ------------------------------------------------------
    if text.upper().replace(" ", "") in {"DELETEMYDATA", "DELETE"}:
        return _handle_delete(conv)

    switch = i18n.detect_switch_command(text)
    if switch and switch != lang:
        conv["language"] = switch
        lang = switch
        # Acknowledge then re-prompt current state.
        ack = i18n.t("LANGUAGE_SWITCHED", lang)
        return _reply(conv, ack, *_reprompt(conv))

    # New users: detect language from script of first message.
    if conv["state"] == UNKNOWN and conv.get("language") == i18n.DEFAULT_LANG:
        detected = i18n.detect_language(text)
        conv["language"] = detected
        lang = detected

    state = conv["state"]

    # Out-of-band re-engagement triggers (FLOW 2 / 4) ---------------------
    if state in (REGISTRATION_COMPLETE, ENDED, UNKNOWN):
        early = _maybe_reengage(conv, text)
        if early is not None:
            return early

    # FSM dispatch ---------------------------------------------------------
    handler = _DISPATCH.get(state, _state_unknown)
    return handler(conv, text)


# ---------------------------------------------------------------------------
# Re-engagement (resume partial registration, outreach replies)
# ---------------------------------------------------------------------------
def _maybe_reengage(conv: Dict, text: str) -> Optional[List[str]]:
    lang = conv["language"]
    # Resume an incomplete donor registration on YES (FLOW 2).
    if conv.get("pendingResumeState") and v.is_yes(text):
        resume = conv.pop("pendingResumeState")
        conv["state"] = resume
        return _reply(conv, *_reprompt(conv))
    # One-time donor outreach reply (FLOW 4).
    if conv.get("awaitingOutreachReply"):
        return _handle_outreach_reply(conv, text)
    return None


def _handle_outreach_reply(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    conv["awaitingOutreachReply"] = False
    request_id = conv.get("activeRequestId")
    if v.is_yes(text):
        conv["state"] = ELIG_DIABETES
        _ctx(conv)["medicalFlags"] = {}
        if conv.get("bridgeOutreach"):
            return _reply(conv, whatsapp_ui.eligibility_question(lang, "ASK_DIABETES", 1, len(ELIG_SEQUENCE)))
        return _reply(conv,
                      i18n.t("ELIGIBILITY_CHECK_START", lang),
                      whatsapp_ui.eligibility_question(lang, "ASK_DIABETES", 1, len(ELIG_SEQUENCE)))
    if v.is_later(text):
        conv["awaitingSnoozeDate"] = True
        conv["state"] = UNKNOWN
        return _reply(conv, i18n.t("OUTREACH_LATER_ASK", lang))
    # treat as NO / decline
    _set_outreach_status(conv, request_id, "declined")
    return _reply(conv, i18n.t("OUTREACH_DECLINED", lang))


def _set_outreach_status(conv: Dict, request_id: Optional[str], status: str) -> None:
    if not request_id:
        return
    req = db.get_request(request_id)
    if not req:
        return
    donor_id = conv.get("donorId")
    for d in req.get("assignedDonors", []):
        if d.get("donorId") == donor_id:
            d["status"] = status
    db.save_request(req)


# ---------------------------------------------------------------------------
# State: UNKNOWN -> menu / identify
# ---------------------------------------------------------------------------
def _state_unknown(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]

    # Awaiting a snooze date from a LATER outreach reply.
    if conv.get("awaitingSnoozeDate"):
        conv["awaitingSnoozeDate"] = False
        date = bedrock_client.parse_date(text) or "next week"
        if not config.LOCAL_MODE or True:
            scheduler.create_schedule(
                name=f"snooze-{conv['phone_number'].strip('+')}-{db.now_iso()[:10]}",
                at=scheduler.in_days(7),
                target_arn=config.get("OUTREACH_STATE_MACHINE_ARN", "local"),
                payload={"requestId": conv.get("activeRequestId"),
                         "phone": conv["phone_number"], "resume": True},
            )
        _set_outreach_status(conv, conv.get("activeRequestId"), "snoozed")
        return _reply(conv, i18n.t("OUTREACH_SNOOZED", lang, date=date))

    choice = v.menu_choice(text)
    if choice == 1:
        # Existing donor? jump straight to a friendly ack; else register.
        existing = db.get_donor_by_phone(conv["phone_number"])
        if existing and existing.get("registrationStatus") == "complete":
            conv["userType"] = "donor"
            conv["donorId"] = existing["donorId"]
            conv["state"] = REGISTRATION_COMPLETE
            return _reply(conv, i18n.t("DAY_BEFORE_CONFIRMED", lang))
        conv["userType"] = "donor"
        conv["state"] = COLLECTING_NAME
        return _reply(conv,
                      i18n.t("DONOR_REGISTRATION_START", lang),
                      i18n.t("ASK_NAME", lang))
    if choice == 2:
        conv["userType"] = "patient"
        conv["state"] = COLLECTING_PATIENT_NAME
        return _reply(conv,
                      i18n.t("PATIENT_REGISTRATION_START", lang),
                      i18n.t("ASK_PATIENT_NAME", lang))

    # No clear choice -> welcome menu.
    return _reply(conv, i18n.t("WELCOME_NEW", lang))


# ---------------------------------------------------------------------------
# FLOW 1: Donor registration
# ---------------------------------------------------------------------------
def _state_name(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    if not v.valid_name(text):
        return _reply(conv, i18n.t("ASK_NAME", lang))
    _ctx(conv)["name"] = text.strip()
    conv["state"] = COLLECTING_AGE
    return _reply(conv, i18n.t("ASK_AGE", lang))


def _state_age(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    age = v.parse_age(text)
    ok, reason = rules.check_age(age)
    if not ok:
        if reason == "underage":
            _save_waitlist(conv, "underage")
            conv["state"] = ENDED
            return _reply(conv, i18n.t("UNDERAGE", lang))
        if reason == "overage":
            conv["state"] = ENDED
            return _reply(conv, i18n.t("OVERAGE", lang))
        return _reply(conv, i18n.t("ASK_AGE", lang))
    _ctx(conv)["age"] = age
    conv["state"] = COLLECTING_WEIGHT
    return _reply(conv, i18n.t("ASK_WEIGHT", lang))


def _state_weight(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    weight = v.parse_weight(text)
    ok, reason = rules.check_weight(weight)
    if not ok:
        if reason == "underweight":
            _save_partial_donor(conv)
            _save_waitlist(conv, "underweight")
            conv["state"] = ENDED
            return _reply(conv, i18n.t("UNDERWEIGHT", lang))
        return _reply(conv, i18n.t("ASK_WEIGHT", lang))
    _ctx(conv)["weight"] = weight
    conv["state"] = COLLECTING_BLOOD_GROUP
    return _reply(conv, i18n.t("ASK_BLOOD_GROUP", lang))


def _state_blood_group(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    low = text.strip().lower()
    if "don't" in low or "dont" in low or "know" in low or "नहीं पता" in text or "తెలియదు" in text:
        _ctx(conv)["bloodGroup"] = None
        _save_partial_donor(conv)
        _schedule_followup(conv)
        conv["state"] = ENDED
        return _reply(conv, i18n.t("BLOOD_GROUP_UNKNOWN", lang))
    bg = v.parse_blood_group(text)
    if not bg:
        return _reply(conv, i18n.t("ASK_BLOOD_GROUP", lang))
    _ctx(conv)["bloodGroup"] = bg
    conv["state"] = COLLECTING_AREA
    return _reply(conv, i18n.t("ASK_AREA", lang))


def _state_area(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    ctx = _ctx(conv)
    ctx["area"] = text.strip()
    coords = geocoding.geocode(text.strip())
    if coords:
        ctx["lat"], ctx["lng"] = coords
    conv["state"] = COLLECTING_LAST_DONATION
    return _reply(conv, i18n.t("ASK_LAST_DONATION", lang))


def _state_last_donation(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    if v.is_first_time(text):
        _ctx(conv)["lastDonationDate"] = None
        return _complete_donor(conv)
    if v.is_yes(text):
        conv["state"] = COLLECTING_LAST_DONATION_DATE
        return _reply(conv, i18n.t("ASK_LAST_DONATION_DATE", lang))
    # Maybe they typed a date directly.
    parsed = bedrock_client.parse_date(text)
    if parsed:
        _ctx(conv)["lastDonationDate"] = parsed
        return _complete_donor(conv)
    return _reply(conv, i18n.t("ASK_LAST_DONATION", lang))


def _state_last_donation_date(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    parsed = bedrock_client.parse_date(text)
    _ctx(conv)["lastDonationDate"] = parsed
    return _complete_donor(conv)


def _complete_donor(conv: Dict) -> List[str]:
    """Finalize donor record (REGISTRATION_COMPLETE) and check for matches."""
    lang = conv["language"]
    ctx = _ctx(conv)
    donor_id = conv.get("donorId") or db.new_id()
    donor = db.get_donor(donor_id) or {"donorId": donor_id}
    donor.update({
        "phone": conv["phone_number"],
        "name": ctx.get("name"),
        "age": ctx.get("age"),
        "weight": ctx.get("weight"),
        "bloodGroup": ctx.get("bloodGroup"),
        "area": ctx.get("area"),
        "city": config.get("DEFAULT_CITY", "Hyderabad"),
        "lat": ctx.get("lat"),
        "lng": ctx.get("lng"),
        "lastDonationDate": ctx.get("lastDonationDate"),
        "registrationStatus": "complete",
        "registrationChannel": conv.get("channel", "whatsapp"),
        "consentGiven": True,
        "preferredLanguage": lang,
        "preferredChannel": conv.get("channel", "whatsapp"),
        "totalDonations": donor.get("totalDonations", 0),
        "medicalFlags": donor.get("medicalFlags", {}),
    })
    donor["oneTimeDonor"] = donor.get("totalDonations", 0) == 1
    elig = rules.derive_eligibility_status(donor)
    donor["eligibilityStatus"] = elig["status"]
    donor["cooldownEndsAt"] = elig["cooldownEndsAt"]
    db.save_donor(donor)

    conv["donorId"] = donor_id
    conv["userType"] = "donor"
    conv["state"] = REGISTRATION_COMPLETE
    conv["followUpScheduled"] = False

    status_label = (i18n.t("STATUS_COOLDOWN", lang) if elig["status"] == "cooldown"
                    else i18n.t("STATUS_ELIGIBLE", lang))
    messages = [i18n.t("REGISTRATION_COMPLETE", lang,
                       name=donor["name"], bloodGroup=donor.get("bloodGroup") or "-",
                       area=donor.get("area") or "-", statusLabel=status_label)]
    if elig["status"] == "cooldown" and elig["cooldownEndsAt"]:
        messages.append(i18n.t("COOLDOWN_AFTER_LAST_DONATION", lang,
                               cooldownEndsAt=elig["cooldownEndsAt"][:10]))
    # Trigger matching against open requests if eligible.
    if elig["status"] == "eligible":
        _check_open_requests_for_donor(conv, donor)
    return _reply(conv, *messages)


def _check_open_requests_for_donor(conv: Dict, donor: Dict) -> None:
    """If a freshly-registered donor matches an open request, start outreach."""
    bg = donor.get("bloodGroup")
    if not bg:
        return
    for req in db.open_requests():
        if rules.is_compatible(bg, req.get("bloodGroup", "")):
            scheduler.start_outreach(req["requestId"], extra={"triggerDonorId": donor["donorId"]})
            break


# ---------------------------------------------------------------------------
# FLOW 3: Patient registration + blood request
# ---------------------------------------------------------------------------
def _state_patient_name(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    if not v.valid_name(text):
        return _reply(conv, i18n.t("ASK_PATIENT_NAME", lang))
    _ctx(conv)["patientName"] = text.strip()
    conv["state"] = COLLECTING_PATIENT_AGE
    return _reply(conv, i18n.t("ASK_PATIENT_AGE", lang))


def _state_patient_age(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    age = v.parse_age(text)
    if age is None:
        return _reply(conv, i18n.t("ASK_PATIENT_AGE", lang))
    _ctx(conv)["patientAge"] = age
    conv["state"] = COLLECTING_BLOOD_GROUP_NEEDED
    return _reply(conv, i18n.t("ASK_BLOOD_GROUP_NEEDED", lang))


def _state_blood_group_needed(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    bg = v.parse_blood_group(text)
    if not bg:
        return _reply(conv, i18n.t("ASK_BLOOD_GROUP_NEEDED", lang))
    _ctx(conv)["bloodGroupNeeded"] = bg
    conv["state"] = COLLECTING_UNITS
    return _reply(conv, i18n.t("ASK_UNITS", lang))


def _state_units(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    units = v.parse_units(text)
    if units is None or not (1 <= units <= 6):
        return _reply(conv, i18n.t("ASK_UNITS", lang))
    _ctx(conv)["units"] = units
    conv["state"] = COLLECTING_HOSPITAL
    return _reply(conv, i18n.t("ASK_HOSPITAL", lang))


def _state_hospital(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    ctx = _ctx(conv)
    ctx["hospital"] = text.strip()
    coords = geocoding.geocode(text.strip())
    if coords:
        ctx["hospitalLat"], ctx["hospitalLng"] = coords
    conv["state"] = COLLECTING_REQUIRED_BY
    return _reply(conv, i18n.t("ASK_REQUIRED_BY", lang))


def _state_required_by(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    ctx = _ctx(conv)
    required_by = bedrock_client.parse_date(text) or text.strip()
    ctx["requiredBy"] = required_by
    # Urgency: within 24h => urgent.
    urgent = False
    parsed = bedrock_client.parse_date(text)
    if parsed:
        try:
            due = datetime.strptime(parsed, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            urgent = (due - datetime.now(timezone.utc)).total_seconds() <= 24 * 3600
        except ValueError:
            pass
    ctx["urgencyLevel"] = "urgent" if urgent else "normal"
    return _raise_request(conv)


def _raise_request(conv: Dict) -> List[str]:
    lang = conv["language"]
    ctx = _ctx(conv)
    # Upsert patient profile.
    patient_id = conv.get("patientId") or db.new_id()
    patient = db.get_patient(patient_id) or {"patientId": patient_id}
    patient.update({
        "phone": conv["phone_number"],
        "name": ctx.get("patientName"),
        "age": ctx.get("patientAge"),
        "bloodGroup": ctx.get("bloodGroupNeeded"),
        "hospital": ctx.get("hospital"),
        "city": config.get("DEFAULT_CITY", "Hyderabad"),
        "diagnosisType": patient.get("diagnosisType", "thalassemia_major"),
        "registrationStatus": "complete",
        "registrationChannel": conv.get("channel", "whatsapp"),
        "preferredLanguage": lang,
        "preferredChannel": conv.get("channel", "whatsapp"),
    })
    db.save_patient(patient)
    conv["patientId"] = patient_id

    request_id = db.new_id()
    req = {
        "requestId": request_id,
        "patientId": patient_id,
        "patientName": ctx.get("patientName"),
        "patientPhone": conv["phone_number"],
        "bloodGroup": ctx.get("bloodGroupNeeded"),
        "unitsNeeded": ctx.get("units"),
        "hospital": ctx.get("hospital"),
        "hospitalLat": ctx.get("hospitalLat"),
        "hospitalLng": ctx.get("hospitalLng"),
        "city": config.get("DEFAULT_CITY", "Hyderabad"),
        "requiredBy": ctx.get("requiredBy"),
        "urgencyLevel": ctx.get("urgencyLevel", "normal"),
        "status": "open",
        "assignedDonors": [],
        "createdAt": db.now_iso(),
        "createdBy": "patient_bot",
    }
    db.save_request(req)
    conv["activeRequestId"] = request_id
    conv["state"] = REQUEST_RAISED

    scheduler.start_outreach(request_id)

    return _reply(conv, i18n.t(
        "REQUEST_RAISED", lang,
        patientName=req["patientName"], bloodGroup=req["bloodGroup"],
        hospital=req["hospital"], requiredBy=req["requiredBy"],
        helpline=config.get("EMERGENCY_HELPLINE", "+91 62814 77836")))


# ---------------------------------------------------------------------------
# FLOW 5: Eligibility quick-check + appointment booking
# ---------------------------------------------------------------------------
def _state_eligibility(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    state = conv["state"]
    spec = next((s for s in ELIG_SEQUENCE if s[0] == state), None)
    if spec is None:
        return _reply(conv, i18n.t("GENERIC_FALLBACK", lang))
    _, flag, next_question_key, next_state = spec
    flags = _ctx(conv).setdefault("medicalFlags", {})
    flags[flag] = v.is_yes(text)

    if next_state == BOOKING_APPOINTMENT:
        return _evaluate_and_book(conv)
    conv["state"] = next_state
    step = next(i for i, s in enumerate(ELIG_SEQUENCE, start=1) if s[0] == next_state)
    return _reply(conv, whatsapp_ui.eligibility_question(lang, next_question_key, step, len(ELIG_SEQUENCE)))


def _evaluate_and_book(conv: Dict) -> List[str]:
    lang = conv["language"]
    flags = _ctx(conv).get("medicalFlags", {})
    deferral = rules.evaluate_deferral(flags)
    donor_id = conv.get("donorId")
    donor = db.get_donor(donor_id) if donor_id else None

    if not deferral["eligible"]:
        if donor:
            donor["medicalFlags"] = flags
            donor["eligibilityStatus"] = "deferred"
            donor["cooldownEndsAt"] = deferral.get("eligible_date")
            db.save_donor(donor)
        _set_outreach_status(conv, conv.get("activeRequestId"), "declined")
        conv["state"] = REGISTRATION_COMPLETE
        eligible_date = deferral.get("eligible_date") or "a future date"
        return _reply(conv, i18n.t("INELIGIBLE_MESSAGE", lang,
                                   reason=deferral["reason"], eligibleDate=eligible_date))

    if donor:
        donor["medicalFlags"] = flags
        donor["eligibilityStatus"] = "eligible"
        db.save_donor(donor)

    if conv.get("bridgeOutreach"):
        req = db.get_request(conv.get("activeRequestId")) if conv.get("activeRequestId") else None
        req = req or {}
        from shared.datetime_utils import default_appointment_slot
        appt_date, appt_time = default_appointment_slot(
            None, None, required_by=req.get("requiredBy"))
        _ctx(conv)["proposedAppointment"] = {
            "hospital": req.get("hospital", "Blood Warriors partner hospital"),
            "date": appt_date,
            "time": appt_time,
        }
        ctx = _ctx(conv)
        pre = i18n.t(
            "BRIDGE_ELIGIBLE_BOOKED", lang,
            donorName=ctx.get("bridgeDonorName") or (donor or {}).get("name") or "Donor",
            bloodGroup=ctx.get("bridgeDonorGroup") or (donor or {}).get("bloodGroup") or "-",
            city=ctx.get("bridgeDonorCity") or (donor or {}).get("city") or "Hyderabad",
        )
        appt_msgs = _confirm_appointment(conv)
        conv["bridgeOutreach"] = False
        return _reply(conv, pre, *appt_msgs)

    # Build proposed appointment from the active request.
    req = db.get_request(conv.get("activeRequestId")) if conv.get("activeRequestId") else None
    ctx = _ctx(conv)
    hospital = (req or {}).get("hospital", "the blood bank")
    appt_date = (req or {}).get("requiredBy") or scheduler.in_days(1).strftime("%Y-%m-%d")
    appt_time = "10:00 AM"
    ctx["proposedAppointment"] = {
        "hospital": hospital, "date": appt_date, "time": appt_time}
    conv["state"] = BOOKING_APPOINTMENT
    return _reply(conv, i18n.t("ELIGIBLE_CONFIRMATION", lang,
                               hospital=hospital, appointmentDate=appt_date,
                               appointmentTime=appt_time))


def _state_booking(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    low = text.strip().lower()
    if "different" in low or "another" in low or "📅" in text or "reschedule" in low:
        conv["state"] = DIFFERENT_DATE
        req = db.get_request(conv.get("activeRequestId")) or {}
        return _reply(conv, i18n.t("DIFFERENT_DATE_ASK", lang,
                                   requiredBy=req.get("requiredBy", "soon")))
    if "help" in low or "❓" in text:
        return _reply(conv, i18n.t("REQUEST_RAISED", lang,
                                   patientName="-", bloodGroup="-", hospital="-",
                                   requiredBy="-",
                                   helpline=config.get("EMERGENCY_HELPLINE", "+91 62814 77836")))
    if v.is_yes(text):
        return _confirm_appointment(conv)
    return _reply(conv, i18n.t("GENERIC_FALLBACK", lang))


def _state_different_date(conv: Dict, text: str) -> List[str]:
    parsed = bedrock_client.parse_date(text)
    proposed = _ctx(conv).get("proposedAppointment", {})
    if parsed:
        proposed["date"] = parsed
    _ctx(conv)["proposedAppointment"] = proposed
    return _confirm_appointment(conv)


def _mark_bridge_slot_confirmed(bridge_id: str | None, donor_id: str | None) -> None:
    if not bridge_id or not donor_id:
        return
    try:
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parents[3]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from raktsetu import store
        store.confirm_bridge_donor(bridge_id, donor_id, "PENDING")
    except Exception as exc:
        logger.warning("Bridge slot confirm failed: %s", exc)


def _confirm_appointment(conv: Dict) -> List[str]:
    lang = conv["language"]
    proposed = _ctx(conv).get("proposedAppointment", {})
    donor_id = conv.get("donorId")
    donor = db.get_donor(donor_id) if donor_id else {}
    req = db.get_request(conv.get("activeRequestId")) if conv.get("activeRequestId") else {}
    req = req or {}

    appt_id = db.new_id()
    appt = {
        "appointmentId": appt_id,
        "donorId": donor_id,
        "patientId": req.get("patientId") or conv.get("bridgePatientId"),
        "requestId": req.get("requestId") or conv.get("activeRequestId"),
        "donorPhone": conv["phone_number"],
        "patientPhone": req.get("patientPhone"),
        "donorName": (donor or {}).get("name") or _ctx(conv).get("bridgeDonorName"),
        "patientName": req.get("patientName") or _ctx(conv).get("bridgePatientName"),
        "hospital": proposed.get("hospital", req.get("hospital")),
        "city": req.get("city") or _ctx(conv).get("bridgeDonorCity"),
        "appointmentDate": proposed.get("date"),
        "appointmentTime": proposed.get("time", "10:00 AM"),
        "bloodGroup": (donor or {}).get("bloodGroup") or _ctx(conv).get("bridgeDonorGroup"),
        "status": "scheduled",
        "donorConfirmedDayBefore": False,
        "channel": conv.get("channel", "whatsapp"),
        "bridgeId": conv.get("bridgeId"),
        "createdAt": db.now_iso(),
    }
    db.save_appointment(appt)
    conv["activeAppointmentId"] = appt_id
    _mark_bridge_slot_confirmed(conv.get("bridgeId"), donor_id)

    # Update request.
    if req:
        req.setdefault("assignedDonors", []).append(
            {"donorId": donor_id, "appointmentId": appt_id, "status": "confirmed"})
        req["status"] = "confirmed"
        db.save_request(req)

    # Schedule reminders (day-before + 3h-before).
    _schedule_appointment_reminders(appt)

    # Notify patient (FLOW 3 DONOR_CONFIRMED).
    _notify_patient(appt, donor or {})

    cal = _calendar_link(appt)
    return _reply(conv, i18n.t(
        "APPOINTMENT_DETAILS", lang,
        patientName=appt["patientName"], hospital=appt["hospital"],
        hospitalAddress=appt.get("hospital", ""),
        appointmentDate=appt["appointmentDate"], appointmentTime=appt["appointmentTime"],
        calendarLink=cal))


def _notify_patient(appt: Dict, donor: Dict) -> None:
    patient_phone = appt.get("patientPhone")
    if not patient_phone:
        return
    patient_conv = db.get_conversation(patient_phone)
    lang = (patient_conv or {}).get("language", i18n.DEFAULT_LANG)
    msg = i18n.t("DONOR_CONFIRMED_TO_PATIENT", lang,
                 patientName=appt.get("patientName"), donorName=donor.get("name", "A donor"),
                 bloodGroup=donor.get("bloodGroup", appt.get("bloodGroup", "-")),
                 appointmentDate=appt.get("appointmentDate"),
                 appointmentTime=appt.get("appointmentTime"), hospital=appt.get("hospital"))
    try:
        twilio_client.send_whatsapp(patient_phone, msg)
    except Exception as exc:
        logger.warning("Failed to notify patient: %s", exc)


def _schedule_appointment_reminders(appt: Dict) -> None:
    from shared.datetime_utils import schedule_appointment_reminders
    schedule_appointment_reminders(appt)


def _calendar_link(appt: Dict) -> str:
    title = "RaktSetu Blood Donation".replace(" ", "+")
    details = f"Donation+for+{(appt.get('patientName') or '').replace(' ', '+')}"
    loc = (appt.get("hospital") or "").replace(" ", "+")
    return (f"https://calendar.google.com/calendar/render?action=TEMPLATE"
            f"&text={title}&details={details}&location={loc}")


# ---------------------------------------------------------------------------
# FLOW 7: cancellation reason capture
# ---------------------------------------------------------------------------
def _state_cancel_reason(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    appt_id = conv.get("activeAppointmentId")
    reasons = {"1": "feeling_unwell", "2": "work_emergency",
               "3": "changed_mind", "4": "other"}
    reason = reasons.get(text.strip(), "other")
    appt = db.get_appointment(appt_id) if appt_id else None
    if appt:
        appt["status"] = "cancelled"
        appt["cancellationReason"] = reason
        db.save_appointment(appt)
        # Trigger replacement search + notify patient.
        if appt.get("requestId"):
            scheduler.start_outreach(appt["requestId"], extra={"replacement": True})
        if appt.get("patientPhone"):
            pconv = db.get_conversation(appt["patientPhone"])
            plang = (pconv or {}).get("language", i18n.DEFAULT_LANG)
            try:
                twilio_client.send_whatsapp(
                    appt["patientPhone"], i18n.t("CANCEL_REPLACEMENT_PATIENT", plang))
            except Exception:
                pass
    conv["state"] = REGISTRATION_COMPLETE
    return _reply(conv, i18n.t("OUTREACH_DECLINED", lang))


# ---------------------------------------------------------------------------
# Terminal / fallback states
# ---------------------------------------------------------------------------
def _state_registration_complete(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    low = text.strip().lower()
    if "reschedule" in low or "cancel" in low or "❌" in text:
        conv["state"] = CANCEL_REASON
        return _reply(conv, i18n.t("CANCEL_REASON_ASK", lang))
    if v.is_yes(text) and conv.get("activeAppointmentId"):
        appt = db.get_appointment(conv["activeAppointmentId"])
        if appt:
            appt["donorConfirmedDayBefore"] = True
            appt["status"] = "confirmed_day_before"
            db.save_appointment(appt)
        return _reply(conv, i18n.t("DAY_BEFORE_CONFIRMED", lang))
    # Otherwise hand to Bedrock for conversational reply.
    reply = bedrock_client.invoke_agent(conv["phone_number"], text, lang)
    return _reply(conv, reply or i18n.t("OUT_OF_SCOPE", lang))


def _state_ended(conv: Dict, text: str) -> List[str]:
    lang = conv["language"]
    # A previously-incomplete user re-engaging.
    if v.is_yes(text) and conv.get("contextData"):
        conv["state"] = _resume_state_for(conv)
        return _reply(conv, *_reprompt(conv))
    return _state_unknown(conv, text)


# ---------------------------------------------------------------------------
# Helpers: partial save, follow-up scheduling, waitlist, re-prompt
# ---------------------------------------------------------------------------
def _save_partial_donor(conv: Dict) -> None:
    ctx = _ctx(conv)
    donor_id = conv.get("donorId") or db.new_id()
    donor = db.get_donor(donor_id) or {"donorId": donor_id}
    donor.update({
        "phone": conv["phone_number"],
        "name": ctx.get("name"),
        "age": ctx.get("age"),
        "weight": ctx.get("weight"),
        "bloodGroup": ctx.get("bloodGroup"),
        "area": ctx.get("area"),
        "city": config.get("DEFAULT_CITY", "Hyderabad"),
        "lat": ctx.get("lat"),
        "lng": ctx.get("lng"),
        "registrationStatus": "partial",
        "registrationChannel": conv.get("channel", "whatsapp"),
        "preferredLanguage": conv["language"],
        "preferredChannel": conv.get("channel", "whatsapp"),
    })
    db.save_donor(donor)
    conv["donorId"] = donor_id


def _schedule_followup(conv: Dict) -> None:
    conv["followUpScheduled"] = True
    at = scheduler.in_days(2)
    conv["followUpAt"] = at.strftime("%Y-%m-%dT%H:%M:%SZ")
    conv["resumeState"] = conv["state"]
    scheduler.create_schedule(
        name=f"followup-{conv['phone_number'].strip('+')}",
        at=at,
        target_arn=config.get("FOLLOW_UP_ARN", "local"),
        payload={"phone": conv["phone_number"]})


def _save_waitlist(conv: Dict, reason: str) -> None:
    ctx = _ctx(conv)
    db.add_to_waitlist({
        "phone": conv["phone_number"],
        "name": ctx.get("name"),
        "age": ctx.get("age"),
        "weight": ctx.get("weight"),
        "reason": reason,
        "language": conv["language"],
    })


def _resume_state_for(conv: Dict) -> str:
    return conv.get("resumeState") or COLLECTING_BLOOD_GROUP


def _reprompt(conv: Dict) -> List[str]:
    """Return the question for the current state (used after language switch/resume)."""
    lang = conv["language"]
    prompts = {
        COLLECTING_NAME: ["ASK_NAME"],
        COLLECTING_AGE: ["ASK_AGE"],
        COLLECTING_WEIGHT: ["ASK_WEIGHT"],
        COLLECTING_BLOOD_GROUP: ["ASK_BLOOD_GROUP"],
        COLLECTING_AREA: ["ASK_AREA"],
        COLLECTING_LAST_DONATION: ["ASK_LAST_DONATION"],
        COLLECTING_LAST_DONATION_DATE: ["ASK_LAST_DONATION_DATE"],
        COLLECTING_PATIENT_NAME: ["ASK_PATIENT_NAME"],
        COLLECTING_PATIENT_AGE: ["ASK_PATIENT_AGE"],
        COLLECTING_BLOOD_GROUP_NEEDED: ["ASK_BLOOD_GROUP_NEEDED"],
        COLLECTING_UNITS: ["ASK_UNITS"],
        COLLECTING_HOSPITAL: ["ASK_HOSPITAL"],
        COLLECTING_REQUIRED_BY: ["ASK_REQUIRED_BY"],
        ELIG_DIABETES: ["ASK_DIABETES"],
        ELIG_TATTOO: ["ASK_RECENT_TATTOO"],
        ELIG_FEVER: ["ASK_RECENT_FEVER"],
        ELIG_PREGNANT: ["ASK_PREGNANT"],
        ELIG_MALARIA: ["ASK_MALARIA_TRAVEL"],
    }
    keys = prompts.get(conv["state"], ["WELCOME_NEW"])
    return [i18n.t(k, lang) for k in keys]


def _handle_delete(conv: Dict) -> List[str]:
    """DPDP 'DELETE MY DATA' handler."""
    lang = conv["language"]
    phone = conv["phone_number"]
    donor = db.get_donor_by_phone(phone)
    if donor:
        db.delete_item(config.table_names()["donors"], "donorId", donor["donorId"], "SK", "PROFILE")
    patient = db.get_patient_by_phone(phone)
    if patient:
        db.delete_item(config.table_names()["patients"], "patientId", patient["patientId"], "SK", "PROFILE")
    db.delete_item(config.table_names()["conversations"], "phone_number", phone)
    return [i18n.t("DATA_DELETED", lang)]


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------
_DISPATCH = {
    UNKNOWN: _state_unknown,
    COLLECTING_NAME: _state_name,
    COLLECTING_AGE: _state_age,
    COLLECTING_WEIGHT: _state_weight,
    COLLECTING_BLOOD_GROUP: _state_blood_group,
    COLLECTING_AREA: _state_area,
    COLLECTING_LAST_DONATION: _state_last_donation,
    COLLECTING_LAST_DONATION_DATE: _state_last_donation_date,
    REGISTRATION_COMPLETE: _state_registration_complete,
    ENDED: _state_ended,
    COLLECTING_PATIENT_NAME: _state_patient_name,
    COLLECTING_PATIENT_AGE: _state_patient_age,
    COLLECTING_BLOOD_GROUP_NEEDED: _state_blood_group_needed,
    COLLECTING_UNITS: _state_units,
    COLLECTING_HOSPITAL: _state_hospital,
    COLLECTING_REQUIRED_BY: _state_required_by,
    REQUEST_RAISED: _state_registration_complete,
    ELIG_DIABETES: _state_eligibility,
    ELIG_TATTOO: _state_eligibility,
    ELIG_FEVER: _state_eligibility,
    ELIG_PREGNANT: _state_eligibility,
    ELIG_MALARIA: _state_eligibility,
    BOOKING_APPOINTMENT: _state_booking,
    DIFFERENT_DATE: _state_different_date,
    CANCEL_REASON: _state_cancel_reason,
}
