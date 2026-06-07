"""In-process voice escalation timers (must run in the long-lived local server)."""
from __future__ import annotations

import logging
import threading
from typing import Dict, Optional

logger = logging.getLogger("raktsetu.voice_escalation")

_timers: Dict[str, threading.Timer] = {}
_lock = threading.Lock()


def _save_escalation_result(request_id: str, result: Dict) -> None:
    from . import config, dynamodb_client as db

    for phone in (config.get("DEMO_OUTREACH_PHONE"), "+919372875356"):
        if not phone:
            continue
        conv = db.get_conversation(phone)
        if not conv:
            continue
        if conv.get("activeRequestId") != request_id and conv.get("voiceOutreachRequestId") != request_id:
            if not conv.get("bridgeOutreach"):
                continue
        conv["escalationCallPlaced"] = bool(result.get("voicePlaced") or result.get("ok"))
        conv["escalationCallResult"] = {**result, "at": db.now_iso()}
        db.save_conversation(conv)
        return


def schedule_escalation(request_id: str, donor_id: str, token: str,
                        delay_seconds: float) -> Dict:
    """Fire escalate_to_voice_if_no_reply after delay unless cancelled."""
    key = f"{request_id}:{donor_id}"
    delay = max(0.0, float(delay_seconds))

    with _lock:
        existing = _timers.pop(key, None)
        if existing:
            existing.cancel()

        def _run_escalation(retry: int = 0) -> None:
            try:
                from .outreach import escalate_to_voice_if_no_reply
                result = escalate_to_voice_if_no_reply(request_id, donor_id, token=token)
                _save_escalation_result(request_id, result)
                logger.info(
                    "voice escalation fired request=%s donor=%s result=%s",
                    request_id, donor_id, result,
                )
                if (
                    retry < 1
                    and not result.get("voicePlaced")
                    and result.get("reason") not in (
                        "already_replied", "superseded", "voice_already_placed",
                    )
                ):
                    logger.warning(
                        "voice escalation retry in 20s request=%s donor=%s reason=%s",
                        request_id, donor_id, result.get("reason") or result.get("status"),
                    )
                    retry_timer = threading.Timer(
                        20.0,
                        lambda: _run_escalation(retry + 1),
                    )
                    retry_timer.daemon = True
                    retry_timer.start()
            except Exception as exc:
                logger.exception("voice escalation failed request=%s: %s", request_id, exc)

        def _fire() -> None:
            with _lock:
                _timers.pop(key, None)
            logger.info(
                "voice escalation timer fired request=%s donor=%s — placing call",
                request_id, donor_id,
            )
            threading.Thread(
                target=_run_escalation,
                daemon=True,
                name=f"voice-escalation-{request_id[:8]}",
            ).start()

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
