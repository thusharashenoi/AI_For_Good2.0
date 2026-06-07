"""In-process voice escalation timers (must run in the long-lived local server)."""
from __future__ import annotations

import logging
import threading
from typing import Dict, Optional

logger = logging.getLogger("raktsetu.voice_escalation")

_timers: Dict[str, threading.Timer] = {}
_lock = threading.Lock()


def schedule_escalation(request_id: str, donor_id: str, token: str,
                        delay_seconds: float) -> Dict:
    """Fire escalate_to_voice_if_no_reply after delay unless cancelled."""
    key = f"{request_id}:{donor_id}"
    delay = max(0.0, float(delay_seconds))

    with _lock:
        existing = _timers.pop(key, None)
        if existing:
            existing.cancel()

        def _fire() -> None:
            with _lock:
                _timers.pop(key, None)
            try:
                from .outreach import escalate_to_voice_if_no_reply
                result = escalate_to_voice_if_no_reply(request_id, donor_id, token=token)
                logger.info(
                    "voice escalation fired request=%s donor=%s result=%s",
                    request_id, donor_id, result,
                )
            except Exception as exc:
                logger.exception("voice escalation failed request=%s: %s", request_id, exc)

        timer = threading.Timer(delay, _fire)
        timer.daemon = True
        timer.start()
        _timers[key] = timer

    logger.info(
        "scheduled voice escalation in %.1fs request=%s donor=%s",
        delay, request_id, donor_id,
    )
    return {"ok": True, "scheduled": True, "delaySeconds": delay, "key": key}


def cancel_escalation(request_id: str, donor_id: str) -> None:
    key = f"{request_id}:{donor_id}"
    with _lock:
        existing = _timers.pop(key, None)
        if existing:
            existing.cancel()
