"""Lambda: vapi-tools  (Vapi assistant server URL)

Vapi POSTs tool-call requests here during a voice call. We execute the SAME
guardrailed tools used on WhatsApp (bound to the caller's phone number) and
return Vapi's required {"results": [{toolCallId, result}]} shape.

Also handles assistant-request for personalised greetings from DynamoDB.
"""
from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import config, vapi_client  # noqa: E402
from shared.agent import VOICE_SYSTEM_PROMPT  # noqa: E402
from shared.agent_tools import AgentTools, dispatch  # noqa: E402
from shared.branding import BOT_NAME  # noqa: E402
from shared import vapi_context  # noqa: E402
from shared.voice_speech import format_tool_result_for_voice, sanitize_for_speech  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.vapi_tools")


def _payload(event: dict) -> dict:
    body = event.get("body")
    if body is None:
        return event
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode("utf-8")
    try:
        return json.loads(body)
    except Exception:
        return {}


def _one_line(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _normalize_tool_call(call: dict) -> dict:
    """Normalize Vapi tool-call shapes (flat, Anthropic function.*, parameters)."""
    fn = call.get("function") or {}
    name = call.get("name") or call.get("toolName") or fn.get("name")
    args = call.get("arguments")
    if args is None:
        args = call.get("parameters")
    if args is None:
        args = fn.get("arguments") or fn.get("parameters")
    return {"id": call.get("id"), "name": name, "arguments": args}


def _tool_calls(message: dict) -> list:
    """Normalize Vapi/Gemini/Anthropic tool-call payloads."""
    by_id: dict = {}
    for item in message.get("toolWithToolCallList") or []:
        tc = item.get("toolCall") or {}
        cid = tc.get("id")
        if not cid:
            continue
        fn = tc.get("function") or {}
        by_id[cid] = _normalize_tool_call({
            "id": cid,
            "name": fn.get("name") or item.get("name"),
            "function": fn,
            "arguments": fn.get("arguments") or fn.get("parameters") or tc.get("parameters"),
        })

    out = []
    raw_calls = list(message.get("toolCallList") or [])
    if not raw_calls and by_id:
        raw_calls = list(by_id.values())

    for call in raw_calls:
        normalized = _normalize_tool_call(call)
        cid = normalized.get("id")
        if not normalized.get("name") and cid and cid in by_id:
            normalized = by_id[cid]
        if normalized.get("id") and normalized.get("name"):
            out.append(normalized)
        else:
            logger.warning("Skipping malformed tool call: %s", call)
    return out


def _registration_goodbye(result: dict, donor_name: str) -> str:
    name = (donor_name or "friend").split(" ")[0]
    if result.get("eligibilityStatus") == "cooldown":
        return sanitize_for_speech(
            f"Thank you so much {name}. You are registered with us. "
            "You are in a cooldown period right now, but we will contact you when you are eligible again. Namaste."
        )
    return sanitize_for_speech(
        f"Thank you so much {name}. You are now a Blood Warrior with us. "
        "We will reach out when a patient needs your blood. Namaste."
    )


def _finish_call(message: dict, goodbye: str, entry: dict) -> None:
    """Speak goodbye once via live control, then hang up before returning to Vapi."""
    entry["message"] = goodbye
    entry["result"] = "Do not speak. Goodbye was delivered and the call is ending."
    hangup = vapi_client.hangup_after_goodbye(message, goodbye)
    logger.info("hangup_after_goodbye result=%s call=%s", hangup, vapi_client.call_id(message))


def _call_variables(message: dict) -> dict:
    call = message.get("call") or {}
    overrides = call.get("assistantOverrides") or {}
    return dict(overrides.get("variableValues") or call.get("variableValues") or {})


def _handle_assistant_request(message: dict) -> dict:
    phone = vapi_client.caller_phone(message) or "+910000000000"
    variables = _call_variables(message)
    assistant_id = config.get("VAPI_ASSISTANT_ID")
    overrides = vapi_context.assistant_overrides(phone, VOICE_SYSTEM_PROMPT, variables or None)
    if assistant_id:
        return {"assistantId": assistant_id, "assistantOverrides": overrides}
    # Fallback: transient assistant (should not happen in prod).
    from shared.vapi_voice import build_assistant_payload
    from shared.agent_tools import TOOL_SCHEMAS
    server = (config.get("VAPI_SERVER_URL") or "").rstrip("/")
    base = build_assistant_payload(server, BOT_NAME, VOICE_SYSTEM_PROMPT, TOOL_SCHEMAS)
    base["firstMessage"] = overrides["firstMessage"]
    base["model"]["messages"] = overrides["model"]["messages"]
    return {"assistant": base}


def handler(event, context=None):
    payload = _payload(event)
    message = payload.get("message") or payload
    mtype = message.get("type")

    if mtype == "assistant-request":
        body = _handle_assistant_request(message)
        logger.info("assistant-request phone=%s", vapi_client.caller_phone(message))
        return _resp(body)

    if mtype == "end-of-call-report":
        phone = vapi_client.caller_phone(message)
        call_id = vapi_client.call_id(message)
        if phone:
            from shared import dynamodb_client as db
            from shared.outreach import send_appointment_confirmation_whatsapp, send_post_call_whatsapp
            conv = db.get_conversation(phone) or {}
            if conv.get("activeAppointmentId"):
                result = send_appointment_confirmation_whatsapp(phone)
            else:
                result = send_post_call_whatsapp(phone, call_id)
            logger.info("end-of-call-report phone=%s result=%s", phone, result)
        return _resp({})

    if mtype != "tool-calls":
        return _resp({"results": []})

    phone = vapi_client.caller_phone(message) or "+910000000000"
    tools = AgentTools(phone, channel="voice")
    # Ensure conversation is tagged as voice for this caller.
    conv = tools._conv()
    if conv.get("channel") != "voice":
        conv["channel"] = "voice"
        from shared import dynamodb_client as db
        db.save_conversation(conv)

    results = []

    for call in _tool_calls(message):
        call_id = call.get("id")
        name = call.get("name")
        args = _parse_args(call.get("arguments"))
        if not call_id or not name:
            logger.warning("Skipping malformed tool call: %s", call)
            continue
        try:
            result = dispatch(tools, name, args)
            entry = {"toolCallId": call_id, "result": format_tool_result_for_voice(name, result)}
            if name == "complete_donor_registration" and result.get("ok"):
                goodbye = _registration_goodbye(result, args.get("name") or "")
                _finish_call(message, goodbye, entry)
            elif name == "book_appointment" and result.get("ok"):
                from shared import dynamodb_client as db
                from shared.voice_booking import finalize_voice_booking
                donor = db.get_donor_by_phone(phone) or {}
                try:
                    finalize_voice_booking(
                        message, entry, phone, result, donor.get("name") or "")
                except Exception as exc:
                    logger.exception("finalize_voice_booking failed phone=%s: %s", phone, exc)
            elif name == "decline_outreach" and result.get("ok"):
                from shared import dynamodb_client as db
                donor = db.get_donor_by_phone(phone) or {}
                name_part = (donor.get("name") or "friend").split(" ")[0]
                goodbye = sanitize_for_speech(
                    f"Thank you {name_part}. That is completely fine. Take care. Namaste.")
                _finish_call(message, goodbye, entry)
            results.append(entry)
            if result.get("ok"):
                logger.info("vapi tool %s ok phone=%s", name, phone)
            else:
                logger.warning(
                    "vapi tool %s failed phone=%s reason=%s",
                    name, phone, result.get("reason") or result.get("error"),
                )
        except Exception as exc:
            logger.exception("vapi tool %s failed: %s", name, exc)
            results.append({"toolCallId": call_id, "result": "Something went wrong. Apologize and continue."})

    return _resp({"results": results})


def _resp(body: dict) -> dict:
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, ensure_ascii=False)}
