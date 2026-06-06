"""Plain spoken text for phone calls — no markdown, symbols, or JSON read aloud."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .branding import BOT_NAME, ORG_SPOKEN, ORG_TAGLINE

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
    t = re.sub(
        r"\b([A-Z]{2,6})\b",
        lambda m: "" if m.group(1) in _ACRONYM_BLOCKLIST else m.group(0),
        t,
    )
    t = _replace_blood_groups(t)
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
            checklist = result.get("eligibilityChecklist") or {}
            if checklist.get("readyToEvaluate"):
                return "Outreach call. All health answers collected — run check_eligibility now."
            missing = checklist.get("missingAnswers") or []
            if missing:
                return (
                    "Outreach call. Ask naturally about remaining health topics only. "
                    "Do not repeat questions already answered."
                )
            return (
                "Outreach call to a registered donor. Ask if they can donate today. "
                "Then call get_eligibility_checklist for health questions."
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
        return "New caller. Ask if they are a donor or need blood for a patient."

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
                return "Registration complete. They are in a cooldown period. Thank them warmly."
            return "Registration complete. Thank them as a Blood Warrior."
        reason = result.get("reason") or "invalid"
        return f"Registration not complete because of {reason.replace('_', ' ')}. Explain gently and re-ask if needed."

    if tool_name == "book_appointment":
        if result.get("ok"):
            hospital = result.get("hospital") or "the hospital"
            when = result.get("date") or result.get("time") or "the scheduled time"
            return (
                f"Appointment booked at {hospital} on {when}. "
                "Thank them warmly and end the call — do not ask anything else."
            )
        reason = result.get("reason") or "missing details"
        return f"Could not book yet because of {reason.replace('_', ' ')}. Explain and continue."

    if tool_name == "raise_blood_request":
        if result.get("ok"):
            return "Blood request raised. Confirm details and say donors are being contacted."
        return "Could not raise the request yet. Ask for any missing details naturally."

    if tool_name == "check_eligibility":
        if result.get("reason") == "incomplete":
            return "Still need more health answers. Call get_eligibility_checklist and save_eligibility_answers."
        if result.get("status") == "cooldown":
            return "They are in cooldown and cannot donate yet. Thank them and end warmly."
        if result.get("eligible"):
            return "They are eligible. Book the appointment and confirm details aloud."
        reason = result.get("reason") or "a medical deferral"
        return f"Not eligible right now because of {reason}. Thank them honestly."

    if result.get("ok") is True:
        return "Done. Continue in warm plain speech."
    if result.get("ok") is False:
        err = result.get("error") or result.get("reason") or "something went wrong"
        return f"Could not complete that because of {err}. Apologize briefly and continue."
    return "Done. Respond naturally in plain speech."


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

OUTREACH_VOICE_RULES = """
# OUTREACH CALL MODE — OVERRIDES REGISTRATION AND PATIENT FLOWS
You placed this call because a patient urgently needs blood. The person answering is already a registered donor.
- Do NOT ask whether they are a donor or a patient.
- Do NOT start donor registration or collect name, age, weight, area, etc.
- Do NOT call complete_donor_registration or raise_blood_request on this call.
- Your job: ask if they can donate today for this urgent request.
- If they say yes: call get_eligibility_checklist, ask any missing health topics naturally, save_eligibility_answers, then check_eligibility when ready, then book_appointment if eligible.
- If they say no or not today: thank them warmly and end the call. Do not pressure them.
- Never loop on the same health question — check the checklist for what is still missing.
"""
