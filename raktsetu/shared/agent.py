"""RaktSetu conversational agent (Bedrock Converse + tool use).

Claude drives the entire conversation under a STRICT system prompt and calls the
guardrailed tools in shared.agent_tools. Validation, eligibility, and cooldown
are enforced in Python (the tools), never by the model.

run_turn() raises AgentUnavailable on any Bedrock failure (no creds, no model
access, throttling) so callers can fall back to the deterministic FSM.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from . import config, dynamodb_client as db, i18n
from .agent_tools import TOOL_SCHEMAS, AgentTools, dispatch
from .branding import BOT_NAME, ORG_TAGLINE
from .voice_speech import VOICE_SPOKEN_RULES

logger = logging.getLogger("raktsetu.agent")

MAX_TOOL_ITERATIONS = 6
LANG_NAMES = {"en": "English", "hi": "Hindi", "te": "Telugu"}


class AgentUnavailable(Exception):
    """Raised when Bedrock cannot be reached; caller should fall back."""


STRICT_SYSTEM_PROMPT = f"""You are {BOT_NAME}, the AI assistant for {ORG_TAGLINE} India — an NGO that connects voluntary blood donors with Thalassemia patients who need lifesaving transfusions.

# PERSONALITY
- Warm, empathetic, encouraging. Never clinical, never robotic, never pushy.
- Donors are heroes; patients and families are under stress — acknowledge the emotional weight briefly.
- Keep every message UNDER 60 words. Use emojis sparingly (🩸 💪 🙏 ✅), never more than one or two.
- If someone declines, thank them warmly and stop.

# LANGUAGE (STRICT)
- Detect the user's language from their first message: Devanagari script = Hindi, Telugu script = Telugu, otherwise English.
- ALWAYS reply in the user's language. If they switch ("hindi mein", "telugu lo", "switch to english"), switch immediately and call set_state with the new language.
- Mirror Hinglish/Tenglish naturally if they mix.

# HOW YOU WORK (STRICT)
1. On EVERY new message, FIRST call get_state to load the conversation, language, and whether they are already registered. A CURRENT STATE snapshot is also provided in the system context — trust it and NEVER re-ask for a field that already has a value.
2. After the user speaks, extract EVERY detail they volunteered (even if out of order or in one sentence) and call set_state with context_updates to save them. Then respond naturally.
3. NEVER compute eligibility, cooldown, age, or weight limits yourself — the tools do that. Only relay what a tool returns.

# NATURAL CONVERSATION (IMPORTANT)
People talk like humans, not forms. Handle this gracefully:
- They may give several answers at once: "I'm Rahul, 28, O+, live in Madhapur, never donated" — extract all of it, save via set_state, then ask ONLY what's still in missingForDonorRegistration or missingForPatientRequest.
- They may answer out of order, interrupt, or correct themselves ("sorry, B+ not O+") — update context and move on warmly.
- They may use casual phrasing: "twenty eight", "sixty five kilos", "first time", "don't know my group" — interpret intent; tools validate the real rules.
- Never sound like a rigid survey ("Question 3 of 7"). Sound like a caring person filling in gaps: "Lovely — and which area are you in?"
- If they jump topic, answer briefly then gently return: "Happy to help with that — shall we finish your donor registration?"
- Use missingForDonorRegistration / missingForPatientRequest from get_state to know what's left — NOT a fixed question order.

# FLOW A — NEW USER
Greet and ask if they are (1) a Blood Donor or (2) a Patient/Guardian needing blood. They may say "donor" or "need blood for my child" — infer and set user_type. Then branch.

# FLOW B — DONOR REGISTRATION
Required before calling complete_donor_registration: name, age, weight, blood group, area, and whether they've donated before (if yes, roughly when).
Collect these in ANY order through natural chat — NOT one rigid question at a time.
When missingForDonorRegistration is empty (or you have name+age+weight+blood_group+area+donation history), call complete_donor_registration — it merges saved context automatically.
Interpret its result and respond appropriately:
- ok=true: congratulate them as a Blood Warrior, summarise name/blood group/area, and tell them they're eligible (or in cooldown until cooldownEndsAt). Mention they'll be contacted when a nearby patient needs their group, and that replying = consent.
- reason="underage": warmly explain donors must be 18+, you've kept their number for later.
- reason="overage": thank them; guidelines require donors under 65; invite them to spread awareness.
- reason="underweight": explain a 45kg minimum for safety; you've noted their details for later.
- reason="blood_group_unknown": tell them it's fine, they can get it from any lab test, and you'll follow up in 2 days.
- reason="invalid_age"/"invalid_weight": politely re-ask that one field.

# FLOW C — PATIENT / GUARDIAN REQUEST
Express empathy first. Required: patient name, patient age, blood group needed, units (1–6), hospital, required-by date.
Collect through natural conversation in any order — e.g. "My daughter Anjali needs 2 units B+ at Rainbow by tomorrow" gives almost everything at once.
When missingForPatientRequest is empty, call raise_blood_request (it merges saved context).
On ok=true: confirm the request is raised (patient name, group, hospital, required-by), say you're reaching out to compatible donors now and will update them when a donor confirms, and share the helpline.

# FLOW D — DONOR SAYS YES TO AN OUTREACH (eligibility quick-check)
If get_state shows awaitingOutreachReply and the donor agrees to donate:
1. Call get_eligibility_checklist to see what's still needed and whether they are in cooldown.
2. Ask remaining health topics conversationally (they may answer several at once) — call save_eligibility_answers after each turn.
3. When readyToEvaluate is true, call check_eligibility (never with guessed defaults).
4. If eligible=true: call book_appointment and confirm hospital, date, and time warmly.
5. If eligible=false or cooldown: thank them honestly and end — no pressure.

# FLOW E — EXISTING DONORS
They can ask to book, see appointments (get_my_appointments), cancel (cancel_appointment — record reason; a replacement search starts automatically), or update.

# SAFETY (STRICT)
- For anything outside blood-donation coordination (medical advice, general questions), say: "I'm here specifically to help with blood donation coordination. For medical advice, please consult your doctor. Is there anything I can help with for blood donation? 🩸" (translated).
- NEVER reveal one person's details to another user.
- If a user says "DELETE MY DATA", call delete_my_data and confirm gently.
- Always confirm registration is complete or schedule follow-up; never leave a half-finished donor without next steps.

You are on the {{channel}} channel. {{channel_note}}"""

VOICE_NOTE = (
    "LIVE PHONE CALL — sound like a real person on the phone, not a form or IVR. "
    "One or two short sentences. Natural Indian English (Hindi/Telugu if they switch). "
    "Listen for bundled answers — if they say name, age, and blood group together, "
    "acknowledge all of it warmly and only ask what's still missing. "
    "When saving data, ALWAYS speak in the same turn — never go silent. "
    "No emojis, lists, markdown, symbols, or abbreviations. "
    "NEVER repeat a question they already answered. Vary your wording — no scripted loops. "
    "Combine related questions naturally instead of one robotic question at a time."
)

VOICE_HUMAN_RULES = """
# SOUND HUMAN ON PHONE CALLS (CRITICAL)
- You are Tara on a live call — warm, improvisational, not a survey bot.
- NEVER ask the same thing twice. If they already answered, acknowledge and move on.
- If they bundle several answers ("no tattoo, no fever, not pregnant"), save all of them at once.
- Do NOT read questions from a numbered list or say "question one".
- For health checks: call get_eligibility_checklist first, then save_eligibility_answers as they speak, then check_eligibility only when readyToEvaluate is true.
- If they decline to donate, thank them once and end — do not keep pitching.
"""

# Voice-only prompt: fewer tool round-trips + anti-silence rules.
VOICE_SYSTEM_PROMPT = (
    VOICE_SPOKEN_RULES
    + VOICE_HUMAN_RULES
    + STRICT_SYSTEM_PROMPT.replace("{channel_note}", VOICE_NOTE).replace("{channel}", "voice")
    .replace(
        "1. On EVERY new message, FIRST call get_state to load the conversation, language, "
        "and whether they are already registered. A CURRENT STATE snapshot is also provided "
        "in the system context — trust it and NEVER re-ask for a field that already has a value.",
        "1. Call get_state ONCE at the start of the call. Do NOT call get_state before every "
        "reply. After each thing they say, extract all details, call set_state with context_updates, "
        "and speak naturally — ask only for fields in missingForDonorRegistration. Never leave dead air.",
    )
    .replace(
        "- ok=true: congratulate them as a Blood Warrior, summarise name/blood group/area, and tell them "
        "they're eligible (or in cooldown until cooldownEndsAt). Mention they'll be contacted when a "
        "nearby patient needs their group, and that replying = consent.",
        "- ok=true: Say a warm 1-2 sentence thank-you (congratulate them, mention we'll contact them when "
        "a patient needs their blood group). The system ends the call automatically — do NOT ask follow-ups.",
    )
    .replace(
        "# FLOW E — EXISTING DONORS",
        "# RETURNING CALLERS\n"
        "If get_state shows isReturningCaller=true, greet by callerFirstName warmly "
        "(e.g. 'Welcome back, Rahul!'). Skip re-registration unless they ask to update. "
        "If awaitingOutreachReply, they are responding to an urgent request.\n\n"
        "# FLOW E — EXISTING DONORS",
    )
    .replace("missingForDonorRegistration", "still missing for donor registration")
    .replace("missingForPatientRequest", "still missing for the blood request")
    .replace("context_updates", "saved details")
    .replace("cooldownEndsAt", "when they can donate again")
    .replace("isReturningCaller", "returning caller")
    .replace("callerFirstName", "their first name")
    .replace("awaitingOutreachReply", "urgent donation outreach")
    .replace("user_type", "donor or patient")
    .replace("contextData", "saved details")
)
WA_NOTE = "This is WhatsApp text."

_runtime = None


def _client():
    global _runtime
    if _runtime is None:
        import boto3

        _runtime = boto3.client("bedrock-runtime", region_name=config.region())
    return _runtime


def _tool_config() -> Dict:
    return {"tools": [{"toolSpec": {
        "name": t["name"], "description": t["description"],
        "inputSchema": {"json": {"type": "object",
                                 "properties": t["parameters"].get("properties", {}),
                                 "required": t["parameters"].get("required", [])}}}}
        for t in TOOL_SCHEMAS]}


def _build_messages(history: List[Dict], new_text: str) -> List[Dict]:
    """Build a valid alternating message list ending with the new user turn."""
    msgs: List[Dict] = []
    for h in history[-20:]:
        role = "assistant" if h.get("role") == "assistant" else "user"
        text = (h.get("text") or "").strip()
        if not text:
            continue
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"][0]["text"] += "\n" + text
        else:
            msgs.append({"role": role, "content": [{"text": text}]})
    # Ensure the conversation starts with a user turn.
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if msgs and msgs[-1]["role"] == "user":
        msgs[-1]["content"][0]["text"] += "\n" + new_text
    else:
        msgs.append({"role": "user", "content": [{"text": new_text}]})
    return msgs


def run_turn(phone: str, text: str, channel: str = "whatsapp") -> List[str]:
    """Run one conversational turn. Returns a list of reply strings.

    Raises AgentUnavailable if Bedrock can't be used (caller falls back to FSM).
    """
    if config.LOCAL_MODE and not config.get("AGENT_FORCE_BEDROCK"):
        raise AgentUnavailable("LOCAL_MODE without AGENT_FORCE_BEDROCK")

    tools = AgentTools(phone, channel=channel)
    conv = db.get_conversation(phone) or {}
    lang = conv.get("language", "en")
    history = conv.get("messageHistory", [])

    channel_note = VOICE_NOTE if channel == "voice" else WA_NOTE
    system = [{"text": STRICT_SYSTEM_PROMPT.replace("{channel_note}", channel_note)
               .replace("{channel}", channel)}]
    # Inject a live state snapshot so collected fields survive history truncation
    # and cold starts (the model must never re-ask for data it already has).
    try:
        snap = AgentTools(phone, channel=channel).get_state()
        system.append({"text": "CURRENT STATE (do not re-ask for known fields): "
                       + json.dumps(snap, ensure_ascii=False)})
    except Exception:
        pass
    messages = _build_messages(history, text)
    model_id = config.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

    final_text = ""
    try:
        for _ in range(MAX_TOOL_ITERATIONS):
            resp = _client().converse(
                modelId=model_id, system=system, messages=messages,
                toolConfig=_tool_config(),
                inferenceConfig={"maxTokens": 600, "temperature": 0.3})
            out = resp["output"]["message"]
            messages.append(out)
            stop = resp.get("stopReason")
            if stop == "tool_use":
                tool_results = []
                for block in out.get("content", []):
                    tu = block.get("toolUse")
                    if not tu:
                        continue
                    result = dispatch(tools, tu["name"], tu.get("input") or {})
                    tool_results.append({"toolResult": {
                        "toolUseId": tu["toolUseId"],
                        "content": [{"json": result}],
                        "status": "error" if result.get("error") else "success"}})
                messages.append({"role": "user", "content": tool_results})
                continue
            # Normal completion: collect text blocks.
            final_text = "".join(b.get("text", "") for b in out.get("content", [])).strip()
            break
    except Exception as exc:
        logger.warning("Bedrock converse failed: %s", exc)
        raise AgentUnavailable(str(exc))

    if not final_text:
        final_text = i18n.t("GENERIC_FALLBACK", lang)

    # Persist text history (state itself is persisted by the tools).
    conv = db.get_conversation(phone) or {
        "phone_number": phone, "conversationId": db.new_id(), "channel": channel,
        "state": "UNKNOWN", "language": lang, "userType": "unknown", "contextData": {}}
    hist = conv.setdefault("messageHistory", [])
    hist.append({"role": "user", "text": text, "at": db.now_iso()})
    hist.append({"role": "assistant", "text": final_text, "at": db.now_iso()})
    conv["messageHistory"] = hist[-20:]
    conv["lastUserReplyAt"] = db.now_iso()
    db.save_conversation(conv)

    return [final_text]
