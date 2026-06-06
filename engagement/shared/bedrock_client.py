"""Amazon Bedrock (Claude) client for RaktSetu.

Two invocation paths:
1. invoke_agent(...)  — Bedrock Agent (with action groups / tools) for full
   conversational turns. Used by the WhatsApp webhook for free-text NLU.
2. invoke_model(...)  — direct Claude messages API, used for narrow NLU tasks
   like natural-language date parsing.

In LOCAL_MODE both paths fall back to lightweight heuristics so the test
harness runs offline with deterministic output.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from . import config
from .branding import BOT_NAME, ORG_TAGLINE

logger = logging.getLogger("raktsetu.bedrock")

SYSTEM_PROMPT = f"""You are {BOT_NAME}, the compassionate AI assistant for {ORG_TAGLINE} India.
You help connect voluntary blood donors with Thalassemia patients who need lifesaving transfusions.

Your personality:
- Warm, empathetic, and encouraging (not clinical or robotic)
- Always acknowledge the emotional weight of the situation (donors are heroes, patients and families are under stress)
- Brief and clear — WhatsApp messages should be under 150 words
- Use relevant emojis sparingly (🩸 💪 🙏 ✅) but don't overdo it
- Never be pushy — if someone declines, thank them and move on gracefully

Language rules:
- Always respond in {{language}} (set dynamically per conversation)
- If the user switches language mid-conversation, switch immediately
- For mixed language (Hinglish, Tenglish), respond in the same mix naturally

You MUST call get_conversation_state at the start of every message to resume the correct flow.
Never ask for information you already have in context.

If someone asks something outside your scope (medical advice, general questions), say:
"I'm here specifically to help with blood donation coordination. For medical advice, please consult your doctor. Is there anything I can help with for blood donation? 🩸"

NEVER share one donor's personal details with another user.
NEVER make medical eligibility decisions beyond the standard deferral rules.
ALWAYS end conversations where a user has provided their number but not completed registration by scheduling a follow-up."""

LANG_NAMES = {"en": "English", "hi": "Hindi", "te": "Telugu"}

_agent_client = None
_runtime_client = None


def _agent():
    global _agent_client
    if _agent_client is None:
        import boto3

        _agent_client = boto3.client("bedrock-agent-runtime", region_name=config.region())
    return _agent_client


def _runtime():
    global _runtime_client
    if _runtime_client is None:
        import boto3

        _runtime_client = boto3.client("bedrock-runtime", region_name=config.region())
    return _runtime_client


def invoke_agent(phone: str, text: str, language: str = "en",
                 session_attributes: Optional[Dict] = None) -> str:
    """Invoke the Bedrock Agent for a conversational turn and return its reply."""
    if config.LOCAL_MODE:
        return _local_reply(text, language)
    agent_id = config.get("BEDROCK_AGENT_ID")
    alias_id = config.get("BEDROCK_AGENT_ALIAS_ID")
    if not agent_id or not alias_id:
        # No agent provisioned — fall back to a direct model call.
        return invoke_model(_compose_prompt(text, language), language)
    try:
        resp = _agent().invoke_agent(
            agentId=agent_id,
            agentAliasId=alias_id,
            sessionId=re.sub(r"[^A-Za-z0-9_]", "", phone) or "anon",
            inputText=text,
            sessionState={"sessionAttributes": {
                "language": language, **(session_attributes or {})}},
        )
        chunks = []
        for event in resp.get("completion", []):
            if "chunk" in event and "bytes" in event["chunk"]:
                chunks.append(event["chunk"]["bytes"].decode("utf-8"))
        return "".join(chunks).strip() or _local_reply(text, language)
    except Exception as exc:
        logger.exception("invoke_agent failed: %s", exc)
        return invoke_model(_compose_prompt(text, language), language)


def invoke_model(prompt: str, language: str = "en",
                 max_tokens: int = 512, system: Optional[str] = None) -> str:
    """Direct Claude messages API call. Returns the assistant text."""
    if config.LOCAL_MODE:
        return _local_reply(prompt, language)
    sys_prompt = system or SYSTEM_PROMPT.format(language=LANG_NAMES.get(language, "English"))
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": sys_prompt,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }
    # Try the primary model, then the fallback (Claude requires cross-region
    # inference-profile IDs, e.g. us.anthropic.claude-haiku-4-5-...).
    for model_id in _model_chain():
        try:
            resp = _runtime().invoke_model(modelId=model_id, body=json.dumps(body))
            payload = json.loads(resp["body"].read())
            parts = payload.get("content", [])
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                return text
        except Exception as exc:
            logger.warning("invoke_model failed on %s: %s", model_id, exc)
            continue
    return ""


def _model_chain() -> list:
    """Ordered list of model ids to try: primary then fallback."""
    primary = config.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    fallback = config.get("BEDROCK_FALLBACK_MODEL_ID")
    chain = [primary]
    if fallback and fallback != primary:
        chain.append(fallback)
    return chain


def _compose_prompt(text: str, language: str) -> str:
    return f"User (language={LANG_NAMES.get(language, 'English')}) says: {text}"


# ---------------------------------------------------------------------------
# Natural-language date parsing (used by patient required-by, last donation,
# LATER snooze). Uses Claude in prod, dateutil heuristics in LOCAL_MODE.
# ---------------------------------------------------------------------------
def parse_date(text: str, reference: Optional[datetime] = None) -> Optional[str]:
    """Parse a fuzzy human date into an ISO date (YYYY-MM-DD), or None."""
    if reference is None:
        from .datetime_utils import now_local
        reference = now_local()
    elif reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    if not text:
        return None

    heuristic = _heuristic_date(text, reference)
    if config.LOCAL_MODE:
        return heuristic

    prompt = (
        f"Today is {reference.strftime('%Y-%m-%d')}. Convert the following phrase to a single "
        f"calendar date. Reply with ONLY the date in YYYY-MM-DD format, nothing else. "
        f"If no date is implied, reply NONE.\nPhrase: \"{text}\""
    )
    out = invoke_model(prompt, system="You are a precise date parser. Output only YYYY-MM-DD or NONE.")
    m = re.search(r"\d{4}-\d{2}-\d{2}", out or "")
    if m:
        return m.group(0)
    return heuristic


def _heuristic_date(text: str, reference: datetime) -> Optional[str]:
    low = text.lower().strip()
    if any(w in low for w in ["today", "now", "asap", "urgent", "immediately"]):
        return reference.strftime("%Y-%m-%d")
    if "tomorrow" in low or "kal" in low:
        return (reference + timedelta(days=1)).strftime("%Y-%m-%d")
    if "day after" in low:
        return (reference + timedelta(days=2)).strftime("%Y-%m-%d")
    if "weekend" in low:
        days_ahead = (5 - reference.weekday()) % 7 or 7  # next Saturday
        return (reference + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    if "next week" in low:
        return (reference + timedelta(days=7)).strftime("%Y-%m-%d")
    m = re.search(r"within\s+(\d+)\s+day", low)
    if m:
        return (reference + timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.search(r"(\d+)\s+day", low)
    if m:
        return (reference + timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    try:
        from dateutil import parser as dparser

        dt = dparser.parse(text, default=reference, fuzzy=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None
