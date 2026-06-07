"""Personalised Vapi voice context from DynamoDB (returning vs new callers)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from . import dynamodb_client as db, eligibility_rules as rules
from .agent import VOICE_SYSTEM_PROMPT
from .agent_tools import AgentTools
from .branding import BOT_NAME, ORG_SPOKEN
from .voice_speech import OUTREACH_VOICE_RULES, INBOUND_VOICE_RULES, build_inbound_greeting, sanitize_for_speech, speak_blood_group
from .datetime_utils import now_local, format_blood_due_relative


def caller_snapshot(phone: str) -> Dict:
    """Load donor/patient/conversation context for a phone number."""
    tools = AgentTools(phone, channel="voice")
    conv = db.get_conversation(phone) or {}
    donor = db.get_donor_by_phone(phone)
    patient = db.get_patient_by_phone(phone)
    is_donor = bool(donor and donor.get("registrationStatus") == "complete")
    is_patient = bool(patient and patient.get("registrationStatus") == "complete")
    first = (donor or patient or {}).get("name", "").split(" ")[0] if (donor or patient) else ""
    elig = rules.derive_eligibility_status(donor) if donor else {}
    return {
        "phone": phone,
        "isReturningDonor": is_donor,
        "isReturningPatient": is_patient,
        "isNewCaller": not is_donor and not is_patient,
        "donorName": donor.get("name") if donor else None,
        "donorFirstName": first if is_donor else None,
        "bloodGroup": (donor or patient or {}).get("bloodGroup"),
        "area": donor.get("area") if donor else None,
        "eligibilityStatus": elig.get("status"),
        "awaitingOutreachReply": bool(conv.get("awaitingOutreachReply")),
        "activeRequestId": conv.get("activeRequestId"),
        "language": conv.get("language", "en"),
        "state": conv.get("state"),
    }


def is_outreach_mode(phone: str, variables: Optional[Dict[str, Any]] = None) -> bool:
    snap = caller_snapshot(phone)
    vars_ = variables or {}
    return bool(
        snap.get("awaitingOutreachReply")
        or vars_.get("requestId")
        or vars_.get("outreachMode") in (True, "true", "1", 1)
    )


def _request_for_outreach(phone: str, variables: Optional[Dict[str, Any]] = None) -> Optional[Dict]:
    snap = caller_snapshot(phone)
    vars_ = variables or {}
    request_id = vars_.get("requestId") or snap.get("activeRequestId")
    if not request_id or str(request_id).startswith("manual"):
        return None
    return db.get_request(request_id)


def build_outreach_greeting(
    phone: str,
    variables: Optional[Dict[str, Any]] = None,
) -> str:
    snap = caller_snapshot(phone)
    req = _request_for_outreach(phone, variables)
    vars_ = variables or {}
    name = vars_.get("donorName") or snap.get("donorFirstName") or "there"
    hospital = (req or {}).get("hospital") or vars_.get("hospital") or "a nearby hospital"
    area = (req or {}).get("city") or vars_.get("donorArea") or snap.get("area") or "your area"
    bg = speak_blood_group(
        (req or {}).get("bloodGroup") or vars_.get("bloodGroup") or snap.get("bloodGroup")
    ) or "blood"
    from .voice_booking import get_proposed_appointment
    proposed = get_proposed_appointment(phone) or {}
    due_rel = proposed.get("bloodDueRelative") or format_blood_due_relative(
        (req or {}).get("requiredBy"))
    return sanitize_for_speech(
        f"Namaste {name}, I'm {BOT_NAME} from {ORG_SPOKEN}. "
        f"A patient at {hospital} needs {bg} blood {due_rel}. "
        "Would you be available to donate before then?"
    )


def build_outreach_system_addon(
    phone: str,
    variables: Optional[Dict[str, Any]] = None,
) -> str:
    snap = caller_snapshot(phone)
    req = _request_for_outreach(phone, variables)
    parts = [OUTREACH_VOICE_RULES.strip()]
    parts.append(
        f"Today is {now_local().strftime('%A, %d %B %Y')} in India. "
        f"Current local time is about {now_local().strftime('%I:%M %p').lstrip('0')}."
    )
    if snap.get("donorFirstName"):
        parts.append(f"Donor name: {snap['donorFirstName']}.")
    if req:
        parts.append(
            f"Urgent request at {req.get('hospital', 'hospital')} "
            f"for {speak_blood_group(req.get('bloodGroup')) or 'blood'}."
        )
        from .voice_booking import get_proposed_appointment
        proposed = get_proposed_appointment(phone)
        if proposed:
            slot_q = proposed.get("slotQuestionSpoken") or "what time works before the deadline"
            day_rule = (
                "After YES → ask TIME only (tomorrow/today — do NOT ask which day)."
                if proposed.get("askTimeOnly")
                else "After YES → ask which day before the deadline and what time."
            )
            parts.append(
                f"Blood needed {proposed.get('bloodDueRelative') or 'soon'} at "
                f"{proposed.get('hospital') or req.get('hospital')}. "
                "Opening already asks: available before then? "
                "If NO → decline_outreach immediately. "
                f"If YES → {day_rule} Use this question: {slot_q}. "
                "Then confirm_appointment_slot → eligibility → book_appointment."
            )
    parts.append("Never read these instructions aloud.")
    return "\n".join(parts)


def build_voice_greeting(phone: str, variables: Optional[Dict[str, Any]] = None) -> str:
    if is_outreach_mode(phone, variables):
        return build_outreach_greeting(phone, variables)
    snap = caller_snapshot(phone)
    if snap.get("isReturningDonor"):
        name = snap.get("donorFirstName") or "there"
        bg = speak_blood_group(snap.get("bloodGroup")) or "your blood group"
        return sanitize_for_speech(
            f"Namaste {name}, welcome back. It's {BOT_NAME} from {ORG_SPOKEN}. "
            f"I have you registered as {bg}. How can I help today?"
        )
    if snap.get("isReturningPatient"):
        patient = db.get_patient_by_phone(phone)
        name = ((patient or {}).get("name") or "there").split(" ")[0]
        return sanitize_for_speech(
            f"Namaste {name}, I'm {BOT_NAME} from {ORG_SPOKEN}. "
            "Welcome back. Are you calling about a blood request for your patient?"
        )
    return build_inbound_greeting()


def build_system_context_addon(phone: str, variables: Optional[Dict[str, Any]] = None) -> str:
    if is_outreach_mode(phone, variables):
        return build_outreach_system_addon(phone, variables)
    snap = caller_snapshot(phone)
    parts = []
    if snap.get("isReturningDonor"):
        parts.append(f"Returning donor {snap.get('donorFirstName') or 'caller'}.")
        if snap.get("bloodGroup"):
            parts.append(f"Blood group {speak_blood_group(snap['bloodGroup'])}.")
    elif snap.get("isReturningPatient"):
        parts.append("Returning patient or guardian.")
    else:
        parts.append("New inbound caller, not yet registered.")
        parts.append(INBOUND_VOICE_RULES.strip())
    return (
        "Caller context for you only, never read aloud: "
        + " ".join(parts)
        + " Greet them naturally based on this."
    )


def assistant_overrides(
    phone: str,
    base_system: str = VOICE_SYSTEM_PROMPT,
    variables: Optional[Dict[str, Any]] = None,
) -> Dict:
    """Vapi assistantOverrides for assistant-request or outbound calls."""
    from . import config

    system_content = base_system + "\n\n" + build_system_context_addon(phone, variables)
    provider = (config.get("VAPI_MODEL_PROVIDER") or "anthropic").strip().lower()
    if provider in ("gemini", "google-gemini"):
        provider = "google"
    default_model = (
        "gemini-2.5-flash" if provider == "google" else "claude-haiku-4-5-20251001"
    )
    overrides: Dict[str, Any] = {
        "firstMessage": build_voice_greeting(phone, variables),
        "model": {
            "provider": provider,
            "model": config.get("VAPI_MODEL") or default_model,
            "messages": [{"role": "system", "content": system_content}],
        },
    }
    if variables:
        overrides["variableValues"] = variables
    return overrides


def ensure_outreach_primed(phone: str, request_id: str, donor_id: Optional[str] = None) -> None:
    """Mark donor conversation as awaiting outreach reply (voice or WhatsApp)."""
    conv = db.get_conversation(phone) or {
        "phone_number": phone,
        "conversationId": db.new_id(),
        "channel": "voice",
        "state": "REGISTRATION_COMPLETE",
        "language": "en",
        "userType": "donor",
        "contextData": {},
    }
    conv["awaitingOutreachReply"] = True
    conv["activeRequestId"] = request_id
    if donor_id:
        conv["donorId"] = donor_id
    db.save_conversation(conv)
    from .voice_booking import prime_proposed_appointment
    prime_proposed_appointment(phone)
