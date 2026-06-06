"""Vapi voice client + helpers.

Used to place outbound AI voice calls (e.g. FLOW 6 escalation when a donor
doesn't reply on WhatsApp). The assistant itself is created via
scripts/setup_vapi.py and converses using the SAME guardrailed tools as
WhatsApp (served by the vapi-tools webhook).
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from . import config

logger = logging.getLogger("raktsetu.vapi")

API_BASE = "https://api.vapi.ai"


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {config.require('VAPI_API_KEY')}",
            "Content-Type": "application/json"}


def _call_live() -> bool:
    """LOCAL_MODE stubs network calls for tests; VAPI_LIVE_CALLS=1 places real calls."""
    return not config.LOCAL_MODE or config.get("VAPI_LIVE_CALLS") == "1"


def create_outbound_call(to_number: str, variables: Optional[Dict] = None,
                         assistant_id: Optional[str] = None) -> Dict:
    """Place an outbound call. `variables` populate outreach context and overrides."""
    variables = dict(variables or {})
    record = {"channel": "vapi", "to": to_number, "variables": variables}
    if not _call_live():
        from . import twilio_client
        twilio_client.OUTBOX.append(record)
        logger.info("[LOCAL VAPI CALL] -> %s vars=%s", to_number, variables)
        return {"ok": True, "local": True, **record}
    assistant_id = assistant_id or config.require("VAPI_ASSISTANT_ID")
    phone_number_id = config.require("VAPI_PHONE_NUMBER_ID")
    from .agent import VOICE_SYSTEM_PROMPT
    from . import vapi_context

    body = {
        "assistantId": assistant_id,
        "phoneNumberId": phone_number_id,
        "customer": {"number": to_number},
    }
    if variables.get("requestId") or variables.get("outreachMode"):
        vapi_context.ensure_outreach_primed(
            to_number,
            str(variables.get("requestId") or "manual-outreach"),
            variables.get("donorId"),
        )
    body["assistantOverrides"] = vapi_context.assistant_overrides(
        to_number, VOICE_SYSTEM_PROMPT, variables)
    try:
        import requests

        resp = requests.post(f"{API_BASE}/call", headers=_headers(), json=body, timeout=15)
        if resp.status_code >= 300:
            try:
                err_body = resp.json()
                err = err_body.get("message") or resp.text
                if isinstance(err, list):
                    err = "; ".join(str(e) for e in err)
            except Exception:
                err = resp.text
            if "international" in str(err).lower():
                err += (" — wire Exotel BYO: fill EXOTEL_* in .env, run "
                        "python scripts/setup_voice.py --setup-exotel")
            return {"ok": False, "error": err, **record}
        data = resp.json()
        return {"ok": True, "callId": data.get("id"), **record}
    except Exception as exc:
        logger.exception("Vapi outbound call failed: %s", exc)
        return {"ok": False, "error": str(exc), **record}


def caller_phone(message: Dict) -> Optional[str]:
    """Extract the human caller's number from a Vapi webhook message."""
    call = message.get("call") or {}
    cust = call.get("customer") or message.get("customer") or {}
    number = cust.get("number")
    return number


def call_id(message: Dict) -> Optional[str]:
    call = message.get("call") or {}
    return call.get("id")


def control_url(message: Dict) -> Optional[str]:
    call = message.get("call") or {}
    monitor = call.get("monitor") or {}
    return monitor.get("controlUrl")


def resolve_control_url(message: Dict) -> Optional[str]:
    """Control URL from webhook payload or Vapi call API."""
    url = control_url(message)
    if url:
        return url
    cid = call_id(message)
    if not cid or not _call_live():
        return None
    try:
        import requests

        resp = requests.get(f"{API_BASE}/call/{cid}", headers=_headers(), timeout=10)
        if resp.status_code < 300:
            return (resp.json().get("monitor") or {}).get("controlUrl")
    except Exception as exc:
        logger.warning("resolve_control_url failed call=%s: %s", cid, exc)
    return None


def end_call(message: Dict, *, delay_seconds: float = 4.0,
             goodbye: Optional[str] = None) -> Dict:
    """End an active Vapi call after optional goodbye (server-side, reliable).

    Uses Live Call Control when controlUrl is present; otherwise DELETE /call/:id.
    """
    import threading
    import time

    cid = call_id(message)
    ctrl = resolve_control_url(message)
    if not cid and not ctrl:
        return {"ok": False, "error": "no call id"}

    def _hangup():
        try:
            import requests

            if ctrl:
                if goodbye:
                    requests.post(ctrl, json={"type": "say", "content": goodbye}, timeout=10)
                    pause = max(5.0, min(delay_seconds, 14.0))
                    time.sleep(pause)
                elif delay_seconds:
                    time.sleep(delay_seconds)
                resp = requests.post(ctrl, json={"type": "end-call"}, timeout=10)
                if resp.status_code < 300:
                    logger.info("Vapi end-call via controlUrl ok call=%s", cid)
                    return
            if cid and _call_live():
                if goodbye and not ctrl:
                    time.sleep(max(delay_seconds, 8.0))
                resp = requests.delete(f"{API_BASE}/call/{cid}", headers=_headers(), timeout=10)
                if resp.status_code < 300:
                    logger.info("Vapi DELETE call ok call=%s", cid)
                else:
                    logger.warning("Vapi DELETE call %s: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.exception("end_call failed: %s", exc)

    threading.Thread(target=_hangup, daemon=False).start()
    return {"ok": True, "callId": cid, "scheduledEndSeconds": delay_seconds}
