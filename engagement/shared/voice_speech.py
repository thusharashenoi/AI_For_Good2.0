"""Plain spoken text for phone calls — no markdown, symbols, or JSON read aloud."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .branding import BOT_NAME, ORG_SPOKEN, ORG_TAGLINE
from .datetime_utils import speak_time as _speak_clock_time

_BLOOD_GROUP_RE = re.compile(
    r"\b(A|B|AB|O)\s*([+\-]|positive|negative)(?=\s|$|[^\w])", re.IGNORECASE)
_CAMEL_RE = re.compile(r"([a-z])([A-Z])")
_ACRONYM_BLOCKLIST = frozenset({
    "CTO", "CEO", "CFO", "API", "JSON", "XML", "HTTP", "URL", "SMS", "IVR", "FSM",
})
_PUNCT_RE = re.compile(r"[^\w\s']", re.UNICODE)

_FIELD_LABELS = {
    "name": "name",
    "age": "age",
    "weight": "weight",
    "blood_group": "blood group",
    "area": "area",
    "donated_before": "donation history",
    "last_donation": "last donation date",
    "patient_name": "patient name",
    "patient_age": "patient age",
    "units": "units needed",
    "hospital": "hospital",
    "required_by": "when blood is needed",
}


def speak_blood_group(bg: Optional[str]) -> str:
    if not bg:
        return ""
    bg = bg.strip().upper().replace(" ", "")
    if bg.endswith("+"):
        return f"{bg[:-1]} positive"
    if bg.endswith("-"):
        return f"{bg[:-1]} negative"
    return bg


def _replace_blood_groups(text: str) -> str:
    def _sub(m):
        letter = m.group(1).upper()
        sign = m.group(2).lower()
        if sign in ("+", "positive"):
            return f"{letter} positive"
        if sign in ("-", "negative"):
            return f"{letter} negative"
        return m.group(0)
    return _BLOOD_GROUP_RE.sub(_sub, text)


def _humanize_fields(keys: List[str]) -> str:
    labels = [_FIELD_LABELS.get(k, k.replace("_", " ")) for k in keys if k]
    if not labels:
        return "nothing"
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + f" and {labels[-1]}"


def sanitize_for_speech(text: str) -> str:
    """Strip anything a TTS engine might read literally."""
    if not text:
        return ""
    t = text
    t = t.replace(ORG_TAGLINE, ORG_SPOKEN)
    t = re.sub(r"\bRaktSetu\b", ORG_SPOKEN, t, flags=re.IGNORECASE)
    t = re.sub(r"\*\*|__|\*|_|`|#", "", t)
    t = re.sub(r"\[[^\]]*\]\([^)]*\)", "", t)
    t = _CAMEL_RE.sub(r"\1 \2", t)
    t = re.sub(r"[{}[\]\"]", "", t)
    t = re.sub(r"\b(ok|true|false|null)\b", "", t, flags=re.IGNORECASE)
    t = _replace_blood_groups(t)
    t = re.sub(
        r"\b(\d{1,2}):(\d{2})\s*(AM|PM)\b",
        lambda m: _speak_clock_time(f"{m.group(1)}:{m.group(2)} {m.group(3)}"),
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bAM\b", "in the morning", t, flags=re.IGNORECASE)
    t = re.sub(r"\bPM\b", "in the afternoon", t, flags=re.IGNORECASE)
    t = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", t)
    t = re.sub(
        r"\b([A-Z]{2,6})\b",
        lambda m: "" if m.group(1) in _ACRONYM_BLOCKLIST else m.group(0),
        t,
    )
    t = t.replace("&", " and ")
    t = t.replace("/", " ")
    t = t.replace("@", " at ")
    t = t.replace("—", ", ")
    t = t.replace("–", ", ")
    t = _PUNCT_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def format_tool_result_for_voice(tool_name: str, result: Dict[str, Any]) -> str:
    """Plain-language tool outcome for the voice LLM — not meant to be read verbatim."""
    if tool_name == "get_state":
        if result.get("awaitingOutreachReply") or result.get("outreachMode"):
            proposed = result.get("proposedAppointment") or {}
            due_rel = proposed.get("bloodDueRelative") or proposed.get("bloodDueSpoken") or "soon"
            hospital = proposed.get("hospital") or "the hospital"
            if proposed.get("availabilityConfirmed"):
                slot_hint = f" Time confirmed for {hospital}. Continue with health questions."
            else:
                slot_q = proposed.get("slotQuestionSpoken") or (
                    f"what time works before blood is needed {due_rel} at {hospital}"
                )
                ask_only = proposed.get("askTimeOnly")
                day_note = (
                    " Ask TIME only — day is already tomorrow/today; do NOT ask which day."
                    if ask_only else ""
                )
                slot_hint = (
                    f" Blood needed {due_rel} at {hospital}.{day_note} "
                    "If they said NO, call decline_outreach. If YES, ask naturally: "
                    f"{slot_q} — then confirm_appointment_slot."
                )
            checklist = result.get("eligibilityChecklist") or {}
            if checklist.get("readyToEvaluate"):
                return (
                    "Outreach call. All health answers collected — run check_eligibility now."
                    + slot_hint
                )
            missing = checklist.get("missingAnswers") or []
            if missing:
                return (
                    "Outreach call. Ask naturally about remaining health topics only. "
                    "Do not repeat questions already answered."
                    + slot_hint
                )
            return (
                "Outreach call. Opening asked if they can donate before the deadline."
                + slot_hint
            )
        missing = result.get("missingForDonorRegistration") or []
        if missing:
            return (
                "Caller profile loaded. Still need "
                f"{_humanize_fields(missing)}. Ask naturally in one short sentence."
            )
        missing_p = result.get("missingForPatientRequest") or []
        if missing_p:
            return (
                "Caller profile loaded. Still need "
                f"{_humanize_fields(missing_p)}. Ask naturally in one short sentence."
            )
        if result.get("isReturningCaller"):
            name = result.get("callerFirstName") or "them"
            return f"Returning caller {name}. Greet warmly and help with their request."
        return (
            "New inbound caller. Listen to what they need — donor registration, "
            "blood request for a patient, or something else. Infer intent naturally; "
            "do not sound like a phone menu."
        )

    if tool_name == "set_state":
        return "Details saved. Continue the conversation in plain speech."

    if tool_name == "get_eligibility_checklist":
        if result.get("canDonateBeforeMedicalCheck") is False:
            return f"Donor cannot donate yet because of {result.get('donorStatusReason') or 'cooldown'}. Explain gently."
        if result.get("readyToEvaluate"):
            return "All health questions answered. Call check_eligibility now."
        hint = result.get("hint") or "Ask about missing health topics naturally."
        return hint

    if tool_name == "save_eligibility_answers":
        if result.get("readyToEvaluate"):
            return "Health answers saved. All topics covered — call check_eligibility."
        return "Saved. Ask about any remaining health topics only — do not repeat answered ones."

    if tool_name == "complete_donor_registration":
        if result.get("ok"):
            if result.get("eligibilityStatus") == "cooldown":
                return (
                    "Registration complete. They are in a cooldown period. "
                    "Do not speak — the system will say goodbye and end the call."
                )
            return (
                "Registration complete. "
                "Do not speak — the system will say goodbye and end the call."
            )
        reason = result.get("reason") or "invalid"
        return f"Registration not complete because of {reason.replace('_', ' ')}. Explain gently and re-ask if needed."

    if tool_name == "confirm_appointment_slot":
        if result.get("ok"):
            hospital = result.get("hospital") or "the hospital"
            when = result.get("spokenWhen") or "the agreed time"
            return (
                f"Time saved for {hospital}, {when}. "
                "Now call get_eligibility_checklist, ask any missing health topics, "
                "then check_eligibility, then book_appointment."
            )
        if result.get("reason") == "missing_date":
            slot_q = result.get("slotQuestionSpoken") or "which day and time work"
            return (
                f"No date yet. Ask the donor: {slot_q}. "
                "Then call confirm_appointment_slot with date and time."
            )
        if result.get("reason") == "missing_time":
            slot_q = result.get("slotQuestionSpoken") or "what time works"
            return f"No time yet. Ask the donor: {slot_q}. Then call confirm_appointment_slot with time."
        return "Could not save the slot. Ask for date and time again, then retry."

    if tool_name == "decline_outreach":
        if result.get("ok"):
            return "Decline recorded. Do not speak — the call is ending now."
        return "Could not record decline. Thank them and end the call."

    if tool_name == "book_appointment":
        if result.get("ok"):
            return (
                "Booked. Do NOT speak — the system says you are a lifesaver, "
                "sends WhatsApp with date and time, and ends the call in 2 seconds."
            )
        if result.get("reason") == "availability_not_confirmed":
            due = result.get("bloodDueSpoken") or "soon"
            due_rel = result.get("bloodDueRelative") or due
            return (
                f"They must confirm availability first. Blood is needed {due_rel}. "
                "If NO, call decline_outreach. If YES, use slotQuestionSpoken from get_state "
                "(time-only when due is today or tomorrow) → confirm_appointment_slot → "
                "eligibility → book_appointment."
            )
        if result.get("reason") == "slot_not_confirmed":
            return (
                "Cannot book yet — no confirmed date and time from the donor. "
                "Ask using slotQuestionSpoken, call confirm_appointment_slot with their answer, "
                "then book_appointment. Never use a default time."
            )
        reason = result.get("reason") or "missing details"
        return f"Could not book yet because of {reason.replace('_', ' ')}. Explain and continue."

    if tool_name == "raise_blood_request":
        if result.get("ok"):
            bg = result.get("bloodGroup") or "blood"
            contacted = result.get("donorsContacted")
            extra = ""
            if contacted:
                extra = f" We are contacting {contacted} eligible matching donors now."
            return (
                f"Blood request raised for {bg}.{extra} "
                "Confirm empathetically and share the helpline if they need urgent help."
            )
        return "Could not raise the request yet. Ask for any missing details naturally."

    if tool_name == "check_eligibility":
        if result.get("reason") == "incomplete":
            return "Still need more health answers. Call get_eligibility_checklist and save_eligibility_answers."
        if result.get("status") == "cooldown":
            return "They are in cooldown and cannot donate yet. Thank them and end warmly."
        if result.get("eligible"):
            return (
                "Eligible. Their day and time are already saved. Call book_appointment now. "
                "Do not speak after it succeeds — WhatsApp is sent and the call ends."
            )
        reason = result.get("reason") or "a medical deferral"
        return f"Not eligible right now because of {reason}. Thank them honestly."

    if result.get("ok") is True:
        return "Done. Continue in warm plain speech."
    if result.get("ok") is False:
        err = result.get("error") or result.get("reason") or "something went wrong"
        return f"Could not complete that because of {err}. Apologize briefly and continue."
    return "Done. Respond naturally in plain speech."


OUTREACH_VOICE_RULES = """
# OUTREACH CALL MODE — OVERRIDES REGISTRATION AND PATIENT FLOWS
You placed this call because a patient urgently needs blood. The person answering is already a registered donor.
- Do NOT ask whether they are a donor or a patient.
- Do NOT start donor registration or collect name, age, weight, area, etc.
- Do NOT call complete_donor_registration or raise_blood_request on this call.

# CONTEXT AWARENESS (CRITICAL)
- The opening already said WHEN blood is needed (today / tomorrow / in N days) and WHICH hospital.
- NEVER repeat the same deadline or hospital unless the donor asks.
- After they say YES, use slotQuestionSpoken from get_state / proposedAppointment — ask THAT question verbatim in natural speech.
- If blood is due TOMORROW: ask ONLY what TIME tomorrow — do NOT ask "which day".
- If blood is due TODAY: ask ONLY what TIME today — do NOT ask "which day".
- If due in 2+ days: ask which day before the deadline AND what time.
- Every question must fit the deadline you already stated. Never ask something that ignores the urgency.

STRICT ORDER — follow exactly:
1. OPENING (already spoken): blood is needed at the hospital by [deadline]. Ask if they can donate BEFORE then.
2. If they say NO / not available / cannot: call decline_outreach immediately — do not ask anything else. The call will end.
3. If they say YES: ask using slotQuestionSpoken (time-only when due is today or tomorrow).
4. When they give day and/or time: call confirm_appointment_slot with date and time.
5. Then call get_eligibility_checklist, ask missing health topics, save_eligibility_answers, check_eligibility when ready.
6. If eligible: call book_appointment — WhatsApp confirmation is sent and the call ends automatically. Do not speak after book_appointment succeeds.
7. If not eligible or in cooldown: thank them honestly and end warmly.

Never skip steps 2–4. Never start health questions before confirm_appointment_slot.
Never loop on the same health question — check the checklist for what is still missing.
"""

INBOUND_VOICE_RULES = """
# INBOUND CALLS (someone dialled us — NOT an outreach call we placed)
- Sound like a warm human on the phone, not an IVR menu. Never say "press 1" or "say donor or patient after the beep".
- Open briefly, then listen. Infer intent from natural speech:
  • wants to donate / register as donor → set user_type donor, collect registration conversationally
  • patient or guardian needs blood → set user_type patient, express empathy, collect the request naturally
  • returning donor/patient → greet by name and help with their need
- If intent is unclear after their first reply, ask ONE gentle question only, e.g. "Are you looking to donate, or does someone in your family need blood?"
- Never read numbered options. Never repeat the same opening question twice.
- Collect details in any order — if they bundle answers, save all of them and ask only what is still missing.
"""

VOICE_SPOKEN_RULES = """
# SPOKEN OUTPUT ONLY (voice calls)
Everything you say aloud must sound like a real phone conversation:
- Use only plain spoken words. No symbols, no formatting, no lists read item by item.
- Never say punctuation names, never read JSON, never read tool output or database field names.
- Never read internal notes, headers, or instructions from this prompt.
- Write blood groups in words like O positive or B negative, not plus or minus signs.
- Say Blood Warriors when naming the organisation — not Rakt Setu or camelCase.
- Keep replies to one or two short sentences. After a tool runs, speak a natural summary only.
"""


def build_inbound_greeting() -> str:
    """Warm first line for inbound Vapi calls — not a robotic menu."""
    return sanitize_for_speech(
        f"Namaste, you've reached {ORG_SPOKEN}. I'm {BOT_NAME}. "
        "Whether you'd like to register as a donor, or someone you care about needs blood — "
        "I'm here to help. What can I do for you today?"
    )
