"""Conversation router: prefer the strict Bedrock agent, fall back to the FSM.

Both the Twilio and Periskope WhatsApp webhooks (and inbound voice) go through
respond() so behaviour is identical regardless of transport. If Bedrock is
unavailable (LOCAL_MODE without AGENT_FORCE_BEDROCK, no creds/model access, or a
runtime error), we transparently fall back to the deterministic FSM so the bot
never goes silent.
"""
from __future__ import annotations

import logging
import re
from typing import List

from shared import agent, dynamodb_client as db

try:
    from . import flows
except ImportError:  # pragma: no cover - direct invoke
    import flows  # type: ignore

logger = logging.getLogger("raktsetu.router")


_FSM_ELIG_STATES = frozenset({
    "ELIG_DIABETES", "ELIG_TATTOO", "ELIG_FEVER", "ELIG_PREGNANT", "ELIG_MALARIA",
    "BOOKING_APPOINTMENT", "DIFFERENT_DATE",
})


def _use_fsm(phone: str) -> bool:
    conv = db.get_conversation(phone) or {}
    if conv.get("awaitingOutreachReply") or conv.get("bridgeOutreach"):
        return True
    return conv.get("state") in _FSM_ELIG_STATES


def respond(phone: str, text: str, channel: str = "whatsapp") -> List[str]:
    if _use_fsm(phone):
        logger.info("FSM path for %s (outreach/eligibility)", phone)
        return flows.handle_message(phone, text, channel)
    try:
        return agent.run_turn(phone, text, channel)
    except agent.AgentUnavailable as exc:
        logger.info("Agent unavailable (%s) — using FSM fallback", exc)
        return flows.handle_message(phone, text, channel)
    except Exception as exc:  # safety net: never crash the webhook
        logger.exception("Agent error, falling back to FSM: %s", exc)
        return flows.handle_message(phone, text, channel)
