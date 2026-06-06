"""Conversation router: prefer the strict Bedrock agent, fall back to the FSM.

Both the Twilio and Periskope WhatsApp webhooks (and inbound voice) go through
respond() so behaviour is identical regardless of transport. If Bedrock is
unavailable (LOCAL_MODE without AGENT_FORCE_BEDROCK, no creds/model access, or a
runtime error), we transparently fall back to the deterministic FSM so the bot
never goes silent.
"""
from __future__ import annotations

import logging
from typing import List

from shared import agent

try:
    from . import flows
except ImportError:  # pragma: no cover - direct invoke
    import flows  # type: ignore

logger = logging.getLogger("raktsetu.router")


def respond(phone: str, text: str, channel: str = "whatsapp") -> List[str]:
    try:
        return agent.run_turn(phone, text, channel)
    except agent.AgentUnavailable as exc:
        logger.info("Agent unavailable (%s) — using FSM fallback", exc)
        return flows.handle_message(phone, text, channel)
    except Exception as exc:  # safety net: never crash the webhook
        logger.exception("Agent error, falling back to FSM: %s", exc)
        return flows.handle_message(phone, text, channel)
