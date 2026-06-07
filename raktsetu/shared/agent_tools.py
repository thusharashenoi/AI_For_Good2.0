"""RaktSetu agent tool layer — the single, guardrailed source of truth.

Used by BOTH the Bedrock agent (WhatsApp) and the Vapi voice assistant. The LLM
never decides eligibility, validation, or cooldown — it calls these tools, and
all such logic is enforced here in Python. Tools are bound to a single caller's
phone number at construction, so the model cannot read or mutate another user's
data (defends "NEVER share one donor's details with another user").

Every tool returns a JSON-serialisable dict. The Vapi webhook wraps the dict as
a single-line string; the Bedrock loop passes it back as a toolResult.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import (config, dynamodb_client as db, eligibility_rules as rules,
               geocoding, i18n, scheduler, twilio_client)

logger = logging.getLogger("raktsetu.agent_tools")

# Fields we need for registration / requests (order does not matter to the caller).
_DONOR_FIELD_KEYS = ("name", "age", "weight", "blood_group", "area", "donated_before", "last_donation")
_PATIENT_FIELD_KEYS = ("patient_name", "patient_age", "blood_group", "units", "hospital", "required_by")


def _ctx_val(ctx: dict, *keys):
    for k in keys:
        v = ctx.get(k)
        if v is not None and v != "":
            return v
    return None


def _missing_donor_fields(ctx: dict) -> List[str]:
    missing = []
    if not _ctx_val(ctx, "name"):
        missing.append("name")
    if _ctx_val(ctx, "age") is None:
        missing.append("age")
    if _ctx_val(ctx, "weight") is None:
        missing.append("weight")
    if not _ctx_val(ctx, "blood_group", "bloodGroup"):
        missing.append("blood_group")
    if not _ctx_val(ctx, "area"):
        missing.append("area")
    return missing


def _missing_patient_fields(ctx: dict) -> List[str]:
    missing = []
    if not _ctx_val(ctx, "patient_name", "patientName"):
        missing.append("patient_name")
    if not _ctx_val(ctx, "blood_group", "bloodGroupNeeded", "bloodGroup"):
        missing.append("blood_group")
    if _ctx_val(ctx, "units") is None:
        missing.append("units")
    if not _ctx_val(ctx, "hospital"):
        missing.append("hospital")
    if not _ctx_val(ctx, "required_by", "requiredBy"):
        missing.append("required_by")
    return missing


def _clean_phone(raw: str) -> str:
    return (raw or "").replace("whatsapp:", "").replace("tel:", "").strip()


class AgentTools:
    """Phone-bound tool implementations shared across channels."""

    def __init__(self, phone: str, channel: str = "whatsapp"):
        self.phone = _clean_phone(phone)
        self.channel = channel

    # -- conversation / state -------------------------------------------
    def _conv(self) -> Dict:
        conv = db.get_conversation(self.phone)
        if conv is None:
            conv = {
                "phone_number": self.phone, "conversationId": db.new_id(),
                "channel": self.channel, "state": "UNKNOWN",
                "language": i18n.DEFAULT_LANG, "userType": "unknown",
                "contextData": {}, "messageHistory": [],
            }
            db.save_conversation(conv)
        return conv

    def get_state(self) -> Dict:
        conv = self._conv()
        donor = db.get_donor_by_phone(self.phone)
        patient = db.get_patient_by_phone(self.phone)
        is_donor = bool(donor and donor.get("registrationStatus") == "complete")
        is_patient = bool(patient and patient.get("registrationStatus") == "complete")
        first_name = ""
        if donor and donor.get("name"):
            first_name = donor["name"].split(" ")[0]
        elif patient and patient.get("name"):
            first_name = patient["name"].split(" ")[0]
        req = db.get_request(conv.get("activeRequestId")) if conv.get("activeRequestId") else None
        proposed = (conv.get("contextData") or {}).get("proposedAppointment")
        if req and conv.get("awaitingOutreachReply") and not proposed:
            from .voice_booking import prime_proposed_appointment
            proposed = prime_proposed_appointment(self.phone)
        active_request = None
        if req:
            active_request = {
                "requestId": req.get("requestId"),
                "hospital": req.get("hospital"),
                "bloodGroup": req.get("bloodGroup"),
                "patientName": req.get("patientName"),
                "requiredBy": req.get("requiredBy"),
            }
        return {
            "state": conv.get("state"),
            "userType": conv.get("userType", "unknown"),
            "language": conv.get("language", "en"),
            "contextData": conv.get("contextData", {}),
            "isRegisteredDonor": is_donor,
            "isRegisteredPatient": is_patient,
            "isReturningCaller": is_donor or is_patient,
            "callerFirstName": first_name or None,
            "donorName": (donor or {}).get("name"),
            "donorBloodGroup": (donor or {}).get("bloodGroup"),
            "donorArea": (donor or {}).get("area"),
            "donorId": (donor or {}).get("donorId"),
            "patientId": (patient or {}).get("patientId"),
            "activeRequestId": conv.get("activeRequestId"),
            "activeRequest": active_request,
            "proposedAppointment": proposed,
            "awaitingOutreachReply": bool(conv.get("awaitingOutreachReply")),
            "outreachMode": bool(conv.get("awaitingOutreachReply")),
            "eligibilityAnswers": (conv.get("contextData") or {}).get("eligibilityAnswers", {}),
            "eligibilityChecklist": rules.eligibility_checklist(
                (conv.get("contextData") or {}).get("eligibilityAnswers", {}),
                conv.get("language", "en"),
            ),
            "missingForDonorRegistration": _missing_donor_fields(conv.get("contextData", {})),
            "missingForPatientRequest": _missing_patient_fields(conv.get("contextData", {})),
        }

    def set_state(self, state: Optional[str] = None, user_type: Optional[str] = None,
                  language: Optional[str] = None,
                  context_updates: Optional[Dict] = None) -> Dict:
        conv = self._conv()
        if state:
            conv["state"] = state
        if user_type:
            conv["userType"] = user_type
        if language and language in i18n.SUPPORTED:
            conv["language"] = language
        if context_updates:
            conv.setdefault("contextData", {}).update(context_updates)
        db.save_conversation(conv)
        ctx = conv.get("contextData", {})
        return {
            "ok": True,
            "state": conv.get("state"),
            "language": conv.get("language"),
            "missingForDonorRegistration": _missing_donor_fields(ctx),
            "missingForPatientRequest": _missing_patient_fields(ctx),
        }

    def get_my_profile(self) -> Dict:
        donor = db.get_donor_by_phone(self.phone)
        patient = db.get_patient_by_phone(self.phone)
        if donor:
            elig = rules.derive_eligibility_status(donor)
            return {"type": "donor", "profile": {
                "name": donor.get("name"), "bloodGroup": donor.get("bloodGroup"),
                "area": donor.get("area"), "registrationStatus": donor.get("registrationStatus"),
                "eligibilityStatus": elig["status"], "cooldownEndsAt": elig["cooldownEndsAt"],
                "totalDonations": donor.get("totalDonations", 0)}}
        if patient:
            return {"type": "patient", "profile": {
                "name": patient.get("name"), "bloodGroup": patient.get("bloodGroup"),
                "hospital": patient.get("hospital")}}
        return {"type": "unknown", "profile": None}

    # -- donor registration (FLOW 1) ------------------------------------
    def complete_donor_registration(self, name: Optional[str] = None, age: Any = None, weight: Any = None,
                                     blood_group: Optional[str] = None,
                                     area: Optional[str] = None,
                                     donated_before: Optional[bool] = None,
                                     last_donation: Optional[str] = None) -> Dict:
        """Validate and persist a donor. Eligibility is computed here, not by the LLM.

        Merges any fields already saved in contextData (from earlier natural speech).
        Returns ok=False with a `reason` the agent must communicate:
          underage | overage | underweight | blood_group_unknown | missing_fields | invalid_age | invalid_weight.
        """
        ctx = self._conv().get("contextData", {})
        name = name or _ctx_val(ctx, "name")
        age = age if age is not None else _ctx_val(ctx, "age")
        weight = weight if weight is not None else _ctx_val(ctx, "weight")
        blood_group = blood_group or _ctx_val(ctx, "blood_group", "bloodGroup")
        area = area or _ctx_val(ctx, "area")
        if donated_before is None:
            donated_before = _ctx_val(ctx, "donated_before")
        if last_donation is None:
            last_donation = _ctx_val(ctx, "last_donation", "lastDonationDate")
        if donated_before is None and last_donation:
            donated_before = True
        if donated_before is None:
            donated_before = False

        # Validate age/weight early when provided (even if other fields still missing).
        if age is not None:
            ok_age, age_reason = rules.check_age(age)
            if not ok_age:
                if age_reason == "underage":
                    conv = self._conv()
                    db.add_to_waitlist({"phone": self.phone, "name": name or "Unknown", "age": age,
                                        "reason": "underage",
                                        "language": conv.get("language", "en")})
                    self.set_state(state="ENDED")
                    return {"ok": False, "reason": "underage"}
                if age_reason == "overage":
                    self.set_state(state="ENDED")
                    return {"ok": False, "reason": "overage"}
                return {"ok": False, "reason": "invalid_age"}

        if weight is not None:
            ok_w, w_reason = rules.check_weight(weight)
            if not ok_w:
                if w_reason == "underweight":
                    self._save_partial_donor(name, age, weight, blood_group, area)
                    conv = self._conv()
                    db.add_to_waitlist({"phone": self.phone, "name": name or "Unknown", "weight": weight,
                                        "reason": "underweight",
                                        "language": conv.get("language", "en")})
                    self.set_state(state="ENDED")
                    return {"ok": False, "reason": "underweight"}
                return {"ok": False, "reason": "invalid_weight"}

        missing = _missing_donor_fields({
            "name": name, "age": age, "weight": weight,
            "blood_group": blood_group, "area": area,
        })
        if missing:
            return {"ok": False, "reason": "missing_fields", "missing": missing,
                    "hint": "Ask only for what's missing — they may have already said it."}

        bg = rules.normalize_blood_group(blood_group) if blood_group else None
        # "Don't know" blood group -> partial save + 48h follow-up.
        if blood_group and not bg and blood_group.strip().lower() not in ("", "none"):
            if "know" in blood_group.lower() or "dont" in blood_group.lower():
                self._save_partial_donor(name, age, weight, None, area)
                self._schedule_followup()
                return {"ok": False, "reason": "blood_group_unknown"}

        donor = db.get_donor_by_phone(self.phone) or {"donorId": db.new_id()}
        coords = geocoding.geocode(area) if area else None
        last_iso = None
        if donated_before and last_donation:
            from .bedrock_client import parse_date
            last_iso = parse_date(last_donation)
        donor.update({
            "phone": self.phone, "name": name, "age": int(age), "weight": float(weight),
            "bloodGroup": bg, "area": area, "city": config.get("DEFAULT_CITY", "Hyderabad"),
            "lat": coords[0] if coords else None, "lng": coords[1] if coords else None,
            "lastDonationDate": last_iso,
            "registrationStatus": "complete", "registrationChannel": self.channel,
            "consentGiven": True, "preferredLanguage": self._conv().get("language", "en"),
            "preferredChannel": self.channel, "totalDonations": donor.get("totalDonations", 0),
            "medicalFlags": donor.get("medicalFlags", {}),
        })
        donor["oneTimeDonor"] = donor.get("totalDonations", 0) == 1
        elig = rules.derive_eligibility_status(donor)
        donor["eligibilityStatus"] = elig["status"]
        donor["cooldownEndsAt"] = elig["cooldownEndsAt"]
        db.save_donor(donor)
        self.set_state(state="REGISTRATION_COMPLETE", user_type="donor")
        conv = self._conv(); conv["donorId"] = donor["donorId"]; db.save_conversation(conv)

        matches = self._match_open_requests(donor)
        out = {"ok": True, "donorId": donor["donorId"],
                "eligibilityStatus": elig["status"],
                "cooldownEndsAt": elig["cooldownEndsAt"],
                "matchingOpenRequests": matches}
        if self.channel == "voice":
            out["endCall"] = True
            out["voiceInstruction"] = (
                "Thank the caller warmly in one or two sentences, then call endCall immediately."
            )
        return out

    def _save_partial_donor(self, name, age, weight, blood_group, area) -> None:
        donor = db.get_donor_by_phone(self.phone) or {"donorId": db.new_id()}
        coords = geocoding.geocode(area) if area else None
        donor.update({"phone": self.phone, "name": name, "age": age, "weight": weight,
                      "bloodGroup": rules.normalize_blood_group(blood_group) if blood_group else None,
                      "area": area, "city": config.get("DEFAULT_CITY", "Hyderabad"),
                      "lat": coords[0] if coords else None, "lng": coords[1] if coords else None,
                      "registrationStatus": "partial",
                      "registrationChannel": self.channel,
                      "preferredLanguage": self._conv().get("language", "en"),
                      "preferredChannel": self.channel})
        db.save_donor(donor)
        conv = self._conv(); conv["donorId"] = donor["donorId"]
        conv.setdefault("contextData", {}).update({"name": name, "age": age, "weight": weight})
        db.save_conversation(conv)

    def _schedule_followup(self) -> None:
        conv = self._conv()
        conv["followUpScheduled"] = True
        conv["resumeState"] = "COLLECTING_BLOOD_GROUP"
        db.save_conversation(conv)
        scheduler.create_schedule(
            name=f"followup-{self.phone.strip('+')}", at=scheduler.in_days(2),
            target_arn=config.get("FOLLOW_UP_ARN", "local"), payload={"phone": self.phone})

    # -- eligibility quick-check (FLOW 5) -------------------------------
    _ELIG_PARAMS = frozenset(rules._PARAM_TO_FLAG.keys())

    def get_eligibility_checklist(self) -> Dict:
        """Return medical quick-check questions, what's answered, and what's still missing."""
        conv = self._conv()
        lang = conv.get("language", "en")
        answers = (conv.get("contextData") or {}).get("eligibilityAnswers", {})
        checklist = rules.eligibility_checklist(answers, lang)
        donor = db.get_donor_by_phone(self.phone)
        base = rules.derive_eligibility_status(donor) if donor else {"status": "unknown"}
        checklist["donorStatus"] = base.get("status")
        checklist["donorStatusReason"] = base.get("reason")
        checklist["canDonateBeforeMedicalCheck"] = base.get("status") == "eligible"
        if base.get("status") == "cooldown":
            checklist["hint"] = (
                "Donor is in cooldown — explain warmly they cannot donate yet, "
                "then thank them and end the call."
            )
        elif checklist["readyToEvaluate"]:
            checklist["hint"] = "All health questions answered — call check_eligibility now."
        else:
            missing_topics = [
                c["spokenTopic"] for c in checklist["checks"] if not c["answered"]
            ]
            checklist["hint"] = (
                "Ask naturally about: "
                + ", ".join(missing_topics[:3])
                + ". Do not re-ask topics already answered."
            )
        return checklist

    def save_eligibility_answers(self, diabetes_insulin: Optional[bool] = None,
                                 tattoo_6mo: Optional[bool] = None,
                                 fever_or_antibiotics_2wk: Optional[bool] = None,
                                 pregnant_or_breastfeeding: Optional[bool] = None,
                                 malaria_travel_3mo: Optional[bool] = None) -> Dict:
        """Save yes/no answers from natural speech (partial updates OK)."""
        conv = self._conv()
        ctx = conv.setdefault("contextData", {})
        answers = ctx.setdefault("eligibilityAnswers", {})
        updates = {
            "diabetes_insulin": diabetes_insulin,
            "tattoo_6mo": tattoo_6mo,
            "fever_or_antibiotics_2wk": fever_or_antibiotics_2wk,
            "pregnant_or_breastfeeding": pregnant_or_breastfeeding,
            "malaria_travel_3mo": malaria_travel_3mo,
        }
        saved = []
        for key, val in updates.items():
            if val is not None and key in self._ELIG_PARAMS:
                answers[key] = bool(val)
                saved.append(key)
        db.save_conversation(conv)
        checklist = rules.eligibility_checklist(answers, conv.get("language", "en"))
        return {
            "ok": True,
            "saved": saved,
            "missingAnswers": checklist["missingAnswers"],
            "readyToEvaluate": checklist["readyToEvaluate"],
        }

    def check_eligibility(self, diabetes_insulin: Optional[bool] = None,
                          tattoo_6mo: Optional[bool] = None,
                          fever_or_antibiotics_2wk: Optional[bool] = None,
                          pregnant_or_breastfeeding: Optional[bool] = None,
                          malaria_travel_3mo: Optional[bool] = None) -> Dict:
        """Run deferral rules once all quick-check answers are collected."""
        if any(v is not None for v in (
            diabetes_insulin, tattoo_6mo, fever_or_antibiotics_2wk,
            pregnant_or_breastfeeding, malaria_travel_3mo,
        )):
            self.save_eligibility_answers(
                diabetes_insulin=diabetes_insulin, tattoo_6mo=tattoo_6mo,
                fever_or_antibiotics_2wk=fever_or_antibiotics_2wk,
                pregnant_or_breastfeeding=pregnant_or_breastfeeding,
                malaria_travel_3mo=malaria_travel_3mo,
            )
        conv = self._conv()
        answers = (conv.get("contextData") or {}).get("eligibilityAnswers", {})
        checklist = rules.eligibility_checklist(answers, conv.get("language", "en"))
        if not checklist["readyToEvaluate"]:
            return {
                "ok": False,
                "reason": "incomplete",
                "missing": checklist["missingAnswers"],
                "hint": "Call get_eligibility_checklist and save_eligibility_answers first.",
            }

        donor = db.get_donor_by_phone(self.phone)
        if not donor:
            return {"ok": False, "reason": "not_a_registered_donor"}

        base = rules.derive_eligibility_status(donor)
        if base.get("status") == "cooldown":
            return {
                "eligible": False,
                "reason": base.get("reason") or "In 90-day cooldown after last donation.",
                "eligibleDate": base.get("cooldownEndsAt"),
                "status": "cooldown",
            }

        flags = rules.answers_to_medical_flags(answers)
        deferral = rules.evaluate_deferral(flags)
        donor["medicalFlags"] = flags
        donor["eligibilityStatus"] = "eligible" if deferral["eligible"] else "deferred"
        if not deferral["eligible"]:
            donor["cooldownEndsAt"] = deferral.get("eligible_date")
        db.save_donor(donor)

        if deferral["eligible"]:
            conv.setdefault("contextData", {})["eligibilityComplete"] = True
            db.save_conversation(conv)
            from .voice_booking import prime_proposed_appointment
            proposed = prime_proposed_appointment(self.phone)
            out = {"eligible": True, "ok": True}
            if proposed:
                out["proposedAppointment"] = proposed
            return out
        return {
            "ok": True,
            "eligible": False,
            "permanent": deferral["permanent"],
            "reason": deferral["reason"],
            "eligibleDate": deferral.get("eligible_date"),
        }

    # -- patient request (FLOW 3) ---------------------------------------
    def raise_blood_request(self, patient_name: Optional[str] = None, blood_group: Optional[str] = None,
                            units: Any = None, hospital: Optional[str] = None,
                            required_by: Optional[str] = None,
                            patient_age: Optional[Any] = None) -> Dict:
        ctx = self._conv().get("contextData", {})
        patient_name = patient_name or _ctx_val(ctx, "patient_name", "patientName")
        patient_age = patient_age if patient_age is not None else _ctx_val(ctx, "patient_age", "patientAge")
        blood_group = blood_group or _ctx_val(ctx, "blood_group", "bloodGroupNeeded", "bloodGroup")
        units = units if units is not None else _ctx_val(ctx, "units")
        hospital = hospital or _ctx_val(ctx, "hospital")
        required_by = required_by or _ctx_val(ctx, "required_by", "requiredBy")

        missing = _missing_patient_fields({
            "patient_name": patient_name, "patient_age": patient_age,
            "blood_group": blood_group, "units": units,
            "hospital": hospital, "required_by": required_by,
        })
        if missing:
            return {"ok": False, "reason": "missing_fields", "missing": missing,
                    "hint": "Ask only for what's missing — they may have already said it."}

        bg = rules.normalize_blood_group(blood_group)
        if not bg:
            return {"ok": False, "reason": "invalid_blood_group"}
        try:
            units = int(units)
        except (TypeError, ValueError):
            units = 1
        units = max(1, min(units, 6))
        from .bedrock_client import parse_date
        req_by = parse_date(required_by) or required_by

        patient = db.get_patient_by_phone(self.phone) or {"patientId": db.new_id()}
        patient.update({"phone": self.phone, "name": patient_name, "age": patient_age,
                        "bloodGroup": bg, "hospital": hospital,
                        "city": config.get("DEFAULT_CITY", "Hyderabad"),
                        "diagnosisType": patient.get("diagnosisType", "thalassemia_major"),
                        "registrationStatus": "complete",
                        "registrationChannel": self.channel,
                        "preferredLanguage": self._conv().get("language", "en"),
                        "preferredChannel": self.channel})
        db.save_patient(patient)

        coords = geocoding.geocode(hospital) if hospital else None
        urgent = False
        from datetime import datetime as _dt
        parsed = parse_date(required_by)
        if parsed:
            try:
                due = _dt.strptime(parsed, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                urgent = (due - datetime.now(timezone.utc)).total_seconds() <= 24 * 3600
            except ValueError:
                pass
        req = {
            "requestId": db.new_id(), "patientId": patient["patientId"],
            "patientName": patient_name, "patientPhone": self.phone, "bloodGroup": bg,
            "unitsNeeded": units, "hospital": hospital,
            "hospitalLat": coords[0] if coords else None,
            "hospitalLng": coords[1] if coords else None,
            "city": config.get("DEFAULT_CITY", "Hyderabad"), "requiredBy": req_by,
            "urgencyLevel": "urgent" if urgent else "normal", "status": "open",
            "assignedDonors": [], "createdAt": db.now_iso(), "createdBy": "patient_bot",
        }
        db.save_request(req)
        conv = self._conv(); conv["patientId"] = patient["patientId"]
        conv["activeRequestId"] = req["requestId"]; conv["userType"] = "patient"
        db.save_conversation(conv)
        outreach = scheduler.start_outreach(req["requestId"])
        out = {"ok": True, "requestId": req["requestId"], "bloodGroup": bg,
                "unitsNeeded": units, "requiredBy": req_by,
                "urgent": urgent, "helpline": config.get("EMERGENCY_HELPLINE", "")}
        if outreach.get("inline"):
            out["outreach"] = outreach["inline"]
            inline = outreach["inline"]
            out["donorsContacted"] = inline.get("donorsContacted")
            out["rankedDonorCount"] = inline.get("rankedCount")
        return out

    # -- matching / appointments ----------------------------------------
    def get_matching_requests(self) -> Dict:
        donor = db.get_donor_by_phone(self.phone)
        if not donor:
            return {"requests": []}
        out = []
        for req in db.open_requests():
            if rules.is_compatible(donor.get("bloodGroup", ""), req.get("bloodGroup", "")):
                out.append({"requestId": req["requestId"], "bloodGroup": req["bloodGroup"],
                            "hospital": req.get("hospital"), "requiredBy": req.get("requiredBy"),
                            "patientName": req.get("patientName")})
        return {"requests": out}

    def book_appointment(self, request_id: Optional[str] = None,
                         date: Optional[str] = None, time: Optional[str] = None) -> Dict:
        donor = db.get_donor_by_phone(self.phone)
        if not donor:
            return {"ok": False, "reason": "not_a_registered_donor"}
        conv = self._conv()
        request_id = request_id or conv.get("activeRequestId")
        req = db.get_request(request_id) if request_id else None
        if not req:
            return {"ok": False, "reason": "no_active_request"}
        from .bedrock_client import parse_date
        from .datetime_utils import (
            default_appointment_slot,
            format_availability_window,
            format_blood_due_spoken,
            schedule_appointment_reminders,
        )
        from .voice_booking import get_proposed_appointment

        proposed = (conv.get("contextData") or {}).get("proposedAppointment") or {}
        if not proposed:
            proposed = get_proposed_appointment(self.phone) or {}
        if not date:
            date = proposed.get("date")
        if not time:
            time = proposed.get("time")

        if self.channel == "voice" and conv.get("activeRequestId"):
            if not proposed.get("availabilityConfirmed"):
                due = proposed.get("bloodDueSpoken") or format_blood_due_spoken(req.get("requiredBy"))
                window = proposed.get("availabilityWindowSpoken") or format_availability_window(
                    req.get("requiredBy"))
                return {
                    "ok": False,
                    "reason": "availability_not_confirmed",
                    "bloodDueSpoken": due,
                    "bloodDueRelative": proposed.get("bloodDueRelative") or due,
                    "availabilityWindowSpoken": window,
                    "hint": (
                        f"MANDATORY: tell them blood is needed by {due}. Ask if they can donate "
                        f"{window} and what time works. Then call confirm_appointment_slot, "
                        "then book_appointment."
                    ),
                }

        parsed_date = parse_date(date) if date else None
        appt_date, appt_time = default_appointment_slot(
            parsed_date, time, required_by=req.get("requiredBy"))
        appt = {
            "appointmentId": db.new_id(), "donorId": donor["donorId"],
            "patientId": req.get("patientId"), "requestId": req["requestId"],
            "donorPhone": self.phone, "patientPhone": req.get("patientPhone"),
            "donorName": donor.get("name"), "patientName": req.get("patientName"),
            "hospital": req.get("hospital"), "city": req.get("city"),
            "appointmentDate": appt_date, "appointmentTime": appt_time,
            "bloodGroup": donor.get("bloodGroup"), "status": "scheduled",
            "donorConfirmedDayBefore": False, "channel": self.channel,
            "createdAt": db.now_iso(),
        }
        db.save_appointment(appt)
        req.setdefault("assignedDonors", []).append(
            {"donorId": donor["donorId"], "appointmentId": appt["appointmentId"],
             "status": "confirmed"})
        req["status"] = "confirmed"
        db.save_request(req)
        conv["activeAppointmentId"] = appt["appointmentId"]
        conv["awaitingOutreachReply"] = False
        conv.setdefault("contextData", {}).pop("proposedAppointment", None)
        db.save_conversation(conv)
        self._notify_patient(appt, donor)
        schedule_appointment_reminders(appt)
        return {"ok": True, "appointmentId": appt["appointmentId"],
                "hospital": appt["hospital"], "date": appt_date, "time": appt_time,
                "calendarLink": self._calendar_link(appt)}

    def confirm_appointment_slot(self, date: Optional[str] = None,
                                 time: Optional[str] = None) -> Dict:
        """Save agreed date/time before book_appointment (after donor confirms verbally)."""
        from .voice_booking import update_proposed_slot
        return update_proposed_slot(self.phone, date=date, time=time)

    def decline_outreach(self, reason: str = "not_available") -> Dict:
        """Donor cannot donate for this urgent request — record and end the call."""
        conv = self._conv()
        request_id = conv.get("activeRequestId")
        donor = db.get_donor_by_phone(self.phone)
        if request_id and donor:
            req = db.get_request(request_id)
            if req:
                marked = False
                for entry in req.get("assignedDonors") or []:
                    if entry.get("donorId") == donor["donorId"]:
                        entry["status"] = "declined"
                        entry["declineReason"] = reason
                        entry["declinedAt"] = db.now_iso()
                        marked = True
                        break
                if not marked:
                    req.setdefault("assignedDonors", []).append({
                        "donorId": donor["donorId"],
                        "status": "declined",
                        "declineReason": reason,
                        "declinedAt": db.now_iso(),
                        "channel": self.channel,
                    })
                db.save_request(req)
        conv["awaitingOutreachReply"] = False
        conv.setdefault("contextData", {}).pop("proposedAppointment", None)
        db.save_conversation(conv)
        return {"ok": True, "declined": True, "reason": reason, "endCall": True}

    def get_my_appointments(self) -> Dict:
        donor = db.get_donor_by_phone(self.phone)
        if not donor:
            return {"appointments": []}
        appts = [a for a in db.appointments_for_donor(donor["donorId"])
                 if a.get("status") not in ("cancelled", "completed")]
        return {"appointments": [{"appointmentId": a["appointmentId"],
                                  "hospital": a.get("hospital"),
                                  "date": a.get("appointmentDate"),
                                  "time": a.get("appointmentTime"),
                                  "patientName": a.get("patientName")} for a in appts]}

    def cancel_appointment(self, appointment_id: Optional[str] = None,
                           reason: str = "other") -> Dict:
        conv = self._conv()
        appointment_id = appointment_id or conv.get("activeAppointmentId")
        appt = db.get_appointment(appointment_id) if appointment_id else None
        if not appt:
            return {"ok": False, "reason": "no_appointment"}
        appt["status"] = "cancelled"; appt["cancellationReason"] = reason
        db.save_appointment(appt)
        if appt.get("requestId"):
            scheduler.start_outreach(appt["requestId"], extra={"replacement": True})
        if appt.get("patientPhone"):
            pconv = db.get_conversation(appt["patientPhone"])
            plang = (pconv or {}).get("language", "en")
            try:
                twilio_client.send_whatsapp(appt["patientPhone"],
                                            i18n.t("CANCEL_REPLACEMENT_PATIENT", plang))
            except Exception:
                pass
        return {"ok": True, "replacementSearchStarted": True}

    def delete_my_data(self) -> Dict:
        tables = config.table_names()
        donor = db.get_donor_by_phone(self.phone)
        if donor:
            db.delete_item(tables["donors"], "donorId", donor["donorId"], "SK", "PROFILE")
        patient = db.get_patient_by_phone(self.phone)
        if patient:
            db.delete_item(tables["patients"], "patientId", patient["patientId"], "SK", "PROFILE")
        db.delete_item(tables["conversations"], "phone_number", self.phone)
        return {"ok": True}

    # -- internal helpers -----------------------------------------------
    def _match_open_requests(self, donor: Dict) -> List[Dict]:
        bg = donor.get("bloodGroup")
        if not bg or donor.get("eligibilityStatus") != "eligible":
            return []
        out = []
        for req in db.open_requests():
            if rules.is_compatible(bg, req.get("bloodGroup", "")):
                scheduler.start_outreach(req["requestId"], extra={"triggerDonorId": donor["donorId"]})
                out.append({"requestId": req["requestId"], "hospital": req.get("hospital")})
        return out

    def _notify_patient(self, appt: Dict, donor: Dict) -> None:
        phone = appt.get("patientPhone")
        if not phone:
            return
        pconv = db.get_conversation(phone)
        lang = (pconv or {}).get("language", "en")
        try:
            twilio_client.send_whatsapp(phone, i18n.t(
                "DONOR_CONFIRMED_TO_PATIENT", lang, patientName=appt.get("patientName"),
                donorName=donor.get("name", "A donor"),
                bloodGroup=donor.get("bloodGroup", "-"),
                appointmentDate=appt.get("appointmentDate"),
                appointmentTime=appt.get("appointmentTime"), hospital=appt.get("hospital")))
        except Exception as exc:
            logger.warning("notify patient failed: %s", exc)

    def _schedule_reminders(self, appt: Dict) -> None:
        from .datetime_utils import schedule_appointment_reminders
        schedule_appointment_reminders(appt)

    @staticmethod
    def _calendar_link(appt: Dict) -> str:
        title = "RaktSetu+Blood+Donation"
        loc = (appt.get("hospital") or "").replace(" ", "+")
        return (f"https://calendar.google.com/calendar/render?action=TEMPLATE"
                f"&text={title}&location={loc}")


# ---------------------------------------------------------------------------
# Tool schema (JSON Schema) shared by Bedrock (toolSpec) and Vapi (function).
# Keeping ONE schema list guarantees both channels expose identical tools.
# ---------------------------------------------------------------------------
TOOL_SCHEMAS: List[Dict] = [
    {"name": "get_state",
     "description": "Get caller state, saved contextData, missingForDonorRegistration, missingForPatientRequest, and whether they are already registered. Call at the start of a turn (or after a long pause) — use missing* lists to ask only what's left.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "set_state",
     "description": "Save conversation progress. ALWAYS pass context_updates with every field extracted from the user's message (name, age, weight, blood_group, area, patient_name, etc.) — even if they said several at once or out of order.",
     "parameters": {"type": "object", "properties": {
         "state": {"type": "string"},
         "user_type": {"type": "string", "enum": ["donor", "patient", "unknown"]},
         "language": {"type": "string", "enum": ["en", "hi", "te"]},
         "context_updates": {"type": "object"}}}},
    {"name": "get_my_profile",
     "description": "Fetch the caller's own donor or patient profile if it exists.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "complete_donor_registration",
     "description": "Validate and save a donor when all required fields are collected (from this call or saved context). Call when missingForDonorRegistration is empty. Merges contextData automatically — pass any new fields from the latest utterance.",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string"},
         "age": {"type": "integer"},
         "weight": {"type": "number"},
         "blood_group": {"type": "string"},
         "area": {"type": "string"},
         "donated_before": {"type": "boolean"},
         "last_donation": {"type": "string", "description": "free text date, e.g. 'March 2025'"}},
         "required": []}},
    {"name": "get_eligibility_checklist",
     "description": "Fetch the medical quick-check questions, which are already answered, and what is still missing. Call this BEFORE asking health questions on outreach calls.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "save_eligibility_answers",
     "description": "Save yes/no answers from the donor's speech. Pass only the fields they just answered (true=yes, false=no). Can be called multiple times with partial updates.",
     "parameters": {"type": "object", "properties": {
         "diabetes_insulin": {"type": "boolean"},
         "tattoo_6mo": {"type": "boolean"},
         "fever_or_antibiotics_2wk": {"type": "boolean"},
         "pregnant_or_breastfeeding": {"type": "boolean"},
         "malaria_travel_3mo": {"type": "boolean"}}}},
    {"name": "check_eligibility",
     "description": "Evaluate deferral rules after ALL quick-check answers are saved. Returns eligible=true/false with reason. Call get_eligibility_checklist first — do not call with guessed defaults.",
     "parameters": {"type": "object", "properties": {
         "diabetes_insulin": {"type": "boolean"},
         "tattoo_6mo": {"type": "boolean"},
         "fever_or_antibiotics_2wk": {"type": "boolean"},
         "pregnant_or_breastfeeding": {"type": "boolean"},
         "malaria_travel_3mo": {"type": "boolean"}}}},
    {"name": "raise_blood_request",
     "description": "Create a blood request when all required fields are collected. Call when missingForPatientRequest is empty. Merges contextData — pass any new fields from the latest utterance. units must be 1-6.",
     "parameters": {"type": "object", "properties": {
         "patient_name": {"type": "string"},
         "patient_age": {"type": "integer"},
         "blood_group": {"type": "string"},
         "units": {"type": "integer"},
         "hospital": {"type": "string"},
         "required_by": {"type": "string", "description": "free text, e.g. 'tomorrow', 'within 3 days'"}},
         "required": []}},
    {"name": "get_matching_requests",
     "description": "List open blood requests compatible with this donor's blood group.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "book_appointment",
     "description": "Book a donation appointment for this donor against a request (defaults to the active request and proposed slot). Notifies the patient and schedules reminders. On voice calls the system sends WhatsApp confirmation and ends the call — do not speak after this succeeds.",
     "parameters": {"type": "object", "properties": {
         "request_id": {"type": "string"},
         "date": {"type": "string", "description": "free text date — omit to use proposed slot"},
         "time": {"type": "string", "description": "e.g. 10:00 AM — omit to use proposed slot"}}}},
    {"name": "confirm_appointment_slot",
     "description": "After the donor said YES they are available before the blood deadline, ask which day and time works, then save it here. Only call AFTER they give a specific day/time. Then proceed to eligibility.",
     "parameters": {"type": "object", "properties": {
         "date": {"type": "string", "description": "donor's preferred date free text"},
         "time": {"type": "string", "description": "donor's preferred time e.g. 2:00 PM"}},
         "required": []}},
    {"name": "decline_outreach",
     "description": "Call immediately when the donor says they cannot donate before the deadline (NO / not available / not today). Ends the call politely.",
     "parameters": {"type": "object", "properties": {
         "reason": {"type": "string", "description": "brief reason e.g. not_available, busy, travelling"}},
         "required": []}},
    {"name": "get_my_appointments",
     "description": "List this donor's upcoming appointments.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "cancel_appointment",
     "description": "Cancel an appointment (defaults to the active one), record the reason, and trigger a replacement donor search.",
     "parameters": {"type": "object", "properties": {
         "appointment_id": {"type": "string"},
         "reason": {"type": "string"}}}},
    {"name": "delete_my_data",
     "description": "DPDP compliance: permanently delete this caller's profile and conversation when they ask to delete their data.",
     "parameters": {"type": "object", "properties": {}}},
]


def dispatch(tools: "AgentTools", name: str, args: Dict[str, Any]) -> Dict:
    """Execute a tool by name with kwargs. Returns the tool's dict result."""
    args = args or {}
    method = getattr(tools, name, None)
    if method is None or name.startswith("_") or name not in {t["name"] for t in TOOL_SCHEMAS}:
        return {"error": f"unknown tool '{name}'"}
    try:
        return method(**args)
    except TypeError as exc:
        logger.warning("tool %s bad args %s: %s", name, args, exc)
        return {"error": f"invalid arguments for {name}: {exc}"}
    except Exception as exc:
        logger.exception("tool %s failed: %s", name, exc)
        return {"error": f"{name} failed: {exc}"}
