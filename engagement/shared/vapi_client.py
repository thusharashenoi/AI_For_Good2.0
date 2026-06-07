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


def _verify_call_started(call_id: Optional[str], wait_sec: float = 15.0) -> Optional[str]:
    """Return an error string if the callee's phone never actually rang."""
    if not call_id or not _call_live():
        return None
    import time
    import requests

    deadline = time.time() + wait_sec
    while time.time() < deadline:
        try:
            resp = requests.get(f"{API_BASE}/call/{call_id}", headers=_headers(), timeout=10)
            if resp.status_code >= 300:
                time.sleep(0.5)
                continue
            st = resp.json()
            status = st.get("status")
            started_at = st.get("startedAt")
            if started_at:
                return None
            if status == "ended":
                reason = st.get("endedReason") or "ended"
                if reason.startswith("call.start."):
                    return reason
                if not started_at:
                    return f"call_never_rang:{reason}"
                return None
        except Exception as exc:
            logger.warning("Vapi call status poll failed call=%s: %s", call_id, exc)
        time.sleep(0.5)
    return "call_not_connecting:timeout"


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
        call_id = data.get("id")
        result = {"ok": True, "callId": call_id, **record}
        fail = _verify_call_started(call_id)
        if fail:
            result["connectWarning"] = fail
            logger.warning(
                "Vapi call %s created but not yet connected (%s) — still ringing",
                call_id, fail,
            )
        return result
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


def hangup_after_goodbye(message: Dict, goodbye: str,
                         delay_after_speak_seconds: Optional[float] = None) -> Dict:
    """Speak goodbye via live call control, wait briefly, then end the call (blocking)."""
    import time

    import requests

    cid = call_id(message)
    ctrl = resolve_control_url(message)
    if not cid and not ctrl:
        logger.warning("hangup_after_goodbye: no call id or control URL")
        return {"ok": False, "error": "no call id"}

    delay = float(
        delay_after_speak_seconds
        if delay_after_speak_seconds is not None
        else config.get("VAPI_HANGUP_DELAY_SECONDS") or 2.0
    )

    try:
        if ctrl:
            say = requests.post(
                ctrl, json={"type": "say", "content": goodbye}, timeout=15)
            logger.info("control say status=%s call=%s", say.status_code, cid)
            time.sleep(delay)
            end = requests.post(ctrl, json={"type": "end-call"}, timeout=15)
            logger.info(
                "control end-call status=%s call=%s body=%s",
                end.status_code, cid, (end.text or "")[:120],
            )
            if end.status_code < 300:
                return {"ok": True, "method": "controlUrl", "callId": cid}

        if cid and _call_live():
            if not ctrl:
                time.sleep(delay)
            resp = requests.delete(
                f"{API_BASE}/call/{cid}", headers=_headers(), timeout=15)
            logger.info("DELETE call status=%s call=%s", resp.status_code, cid)
            return {"ok": resp.status_code < 300, "method": "delete", "callId": cid}
    except Exception as exc:
        logger.exception("hangup_after_goodbye failed call=%s: %s", cid, exc)
    return {"ok": False, "callId": cid}


def end_call(message: Dict, *, delay_seconds: float = 4.0,
             goodbye: Optional[str] = None) -> Dict:
    """End call after optional goodbye (async fallback — prefer hangup_after_goodbye)."""
    if goodbye:
        import threading
        threading.Thread(
            target=hangup_after_goodbye,
            args=(message, goodbye),
            daemon=False,
        ).start()
        return {"ok": True, "callId": call_id(message), "scheduledEndSeconds": delay_seconds}

    import threading
    import time

    cid = call_id(message)
    ctrl = resolve_control_url(message)
    if not cid and not ctrl:
        return {"ok": False, "error": "no call id"}

    def _hangup():
        try:
            import requests

            if delay_seconds:
                time.sleep(delay_seconds)
            if ctrl:
                resp = requests.post(ctrl, json={"type": "end-call"}, timeout=10)
                if resp.status_code < 300:
                    return
            if cid and _call_live():
                requests.delete(f"{API_BASE}/call/{cid}", headers=_headers(), timeout=10)
        except Exception as exc:
            logger.exception("end_call failed: %s", exc)

    threading.Thread(target=_hangup, daemon=False).start()
    return {"ok": True, "callId": cid, "scheduledEndSeconds": delay_seconds}
