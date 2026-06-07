"""Bridge mobilization broadcast — WhatsApp to bridge donors, Vapi call on no reply."""
from __future__ import annotations

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("raktsetu.broadcast")

DEMO_PHONE = os.environ.get("DEMO_OUTREACH_PHONE", "+919372875356")

_engagement_path = Path(__file__).resolve().parents[1] / "engagement"
if _engagement_path.is_dir() and str(_engagement_path) not in sys.path:
    sys.path.insert(0, str(_engagement_path))


def _digits(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


def _escalation_delay_sec() -> float:
    raw = (
        os.environ.get("OUTREACH_VOICE_ESCALATION_SECONDS")
        or os.environ.get("DEMO_OUTREACH_CALL_DELAY_SEC")
        or "7"
    )
    return float(raw)


def _bootstrap_env() -> None:
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)
    delay = _escalation_delay_sec()
    for key, val in {
        "STAGE": "dev",
        "LOCAL_MODE": "0",
        "PERISKOPE_LIVE_SENDS": "1",
        "VAPI_LIVE_CALLS": "1",
        "DEMO_OUTREACH_PHONE": "+919372875356",
        "DEMO_OUTREACH_CALL_DELAY_SEC": str(int(delay)),
        "OUTREACH_VOICE_ESCALATION_SECONDS": str(int(delay)),
        "DYNAMODB_TABLE_CONVERSATIONS": "raktsetu-conversations-dev",
        "DYNAMODB_TABLE_DONORS": "raktsetu-donors-dev",
        "LOCAL_SERVER_URL": os.environ.get("LOCAL_SERVER_URL", "http://127.0.0.1:4000"),
    }.items():
        os.environ.setdefault(key, val)
    demo = _digits(os.environ.get("DEMO_OUTREACH_PHONE", "918372875356"))
    periskope = _digits(os.environ.get("PERISKOPE_PHONE", ""))
    bot = _digits(os.environ.get("TWILIO_WHATSAPP_NUMBER", "")) or "918433775356"
    if not periskope or periskope == demo:
        os.environ["PERISKOPE_PHONE"] = bot


def _engagement():
    _bootstrap_env()
    from shared import config, dynamodb_client as db, periskope_client, vapi_client  # noqa: WPS433
    from shared.outreach import _outreach_whatsapp_message  # noqa: WPS433

    # config.LOCAL_MODE is fixed at first import — refresh for live demo sends/calls.
    config.LOCAL_MODE = os.environ.get("LOCAL_MODE", "0") == "1"

    return config, db, periskope_client, vapi_client, _outreach_whatsapp_message


def _demo_phone() -> str:
    phone = DEMO_PHONE.strip()
    if not phone.startswith("+"):
        phone = f"+{phone.lstrip('+')}"
    return phone


def _send_mobilization_whatsapp(to: str, msg: str) -> dict[str, Any]:
    """Send FROM the Blood Warriors bot TO the demo/coordinator phone."""
    _, _, periskope_client, _, _ = _engagement()
    from shared import config, twilio_client  # noqa: WPS433

    bot = periskope_client.bot_phone_digits()
    to_digits = _digits(to)
    if to_digits == bot:
        return {
            "ok": False,
            "error": "DEMO_OUTREACH_PHONE must differ from the bot sender number",
            "from": bot,
            "to": to_digits,
        }

    # Prefer Periskope (Twilio WhatsApp channel often unset in dev — error 63007).
    res = periskope_client.send_message(to, msg)
    if res.get("ok"):
        return {
            **res,
            "provider": "periskope",
            "from": periskope_client.org_phone(),
            "to": to_digits,
        }

    if config.get("TWILIO_ACCOUNT_SID") and config.get("TWILIO_AUTH_TOKEN"):
        try:
            res = twilio_client.send_whatsapp_direct(to, msg)
            if res.get("ok"):
                return {
                    "ok": True,
                    "provider": "twilio",
                    "from": bot,
                    "to": to_digits,
                    "response": res,
                }
        except Exception as exc:
            logger.warning("Twilio mobilization send failed: %s", exc)

    return res


def _phone_keys(phone: str) -> list[str]:
    """Lookup keys used in the conversations table."""
    p = _demo_phone() if phone == DEMO_PHONE else (phone if phone.startswith("+") else f"+{phone.lstrip('+')}")
    digits = p.lstrip("+")
    keys = [p, f"+{digits}", digits]
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _get_conversation(db, phone: str) -> tuple[dict, str]:
    for key in _phone_keys(phone):
        conv = db.get_conversation(key)
        if conv:
            return conv, key
    return {}, _phone_keys(phone)[0]


def _save_conversation(db, conv: dict, phone_key: str) -> None:
    conv["phone_number"] = phone_key
    db.save_conversation(conv)


def _donor_records(bridge: dict, donors: pd.DataFrame) -> list[dict]:
    dmeta = donors.drop_duplicates(subset=["user_id"], keep="first")
    if "user_id" in dmeta.columns:
        dmeta = dmeta.set_index("user_id")
    rows = []
    for slot in bridge.get("slots") or []:
        did = slot.get("donorId")
        if not did:
            continue
        if did in dmeta.index:
            drow = dmeta.loc[did]
            rows.append({
                "donorId": did,
                "name": drow.get("name") or did,
                "phone": drow.get("phone") or _demo_phone(),
                "bloodGroup": drow.get("blood_group_norm"),
                "city": drow.get("city"),
            })
        else:
            rows.append({"donorId": did, "name": did, "phone": _demo_phone()})
    return rows


def _build_request_stub(bridge: dict, patient_name: str | None) -> dict:
    return {
        "requestId": f"bridge-{bridge.get('bridgeId')}",
        "patientName": patient_name or "patient",
        "bloodGroup": bridge.get("bloodGroup") or "blood",
        "hospital": "Blood Warriors partner hospital",
        "city": "Hyderabad",
    }


def _single_broadcast_message(req: dict, donor_rows: list[dict], patient_name: str | None) -> str:
    """One consolidated WhatsApp for demo (all bridge donors, one recipient)."""
    from shared.outreach import _outreach_whatsapp_message  # noqa: WPS433

    lead = donor_rows[0]
    base = _outreach_whatsapp_message(lead, req)
    patient = patient_name or req.get("patientName") or "a patient"
    names = ", ".join(d.get("name", "donor") for d in donor_rows[:4])
    if len(donor_rows) > 4:
        names += f" + {len(donor_rows) - 4} more"
    header = (
        f"🩸 *Bridge mobilization — {patient} ({req.get('bloodGroup', 'blood')})*\n"
        f"{len(donor_rows)} donors ranked on the Blood Graph: {names}.\n\n"
    )
    return header + base


def _schedule_voice_escalation(request_id: str, donor_id: str, token: str) -> dict[str, Any]:
    """Schedule Vapi call after threshold if WhatsApp unanswered (automation branch flow)."""
    delay = _escalation_delay_sec()
    payload = {
        "requestId": request_id,
        "donorId": donor_id,
        "token": token,
        "delaySeconds": delay,
    }
    base = os.environ.get("LOCAL_SERVER_URL", "http://127.0.0.1:4000").rstrip("/")

    # Local demo: always delegate timers to the long-lived engagement server (:4000)
    # so escalation runs with engagement/lambdas on sys.path (Admin API lacks that).
    try:
        import requests

        resp = requests.post(
            f"{base}/internal/outreach/schedule-voice",
            json=payload,
            timeout=5,
        )
        if resp.status_code < 300:
            body = resp.json() if resp.content else {}
            logger.info(
                "Voice escalation scheduled on engagement server in %.0fs request=%s",
                delay, request_id,
            )
            return {"ok": True, "scheduledOnServer": True, "delaySeconds": delay, **body}
        logger.warning(
            "Engagement server schedule-voice returned %s: %s",
            resp.status_code, resp.text[:200],
        )
    except Exception as exc:
        logger.warning(
            "Could not reach engagement server at %s — using in-process timer (%s)",
            base, exc,
        )

    from shared import voice_escalation_scheduler  # noqa: WPS433

    voice_escalation_scheduler.schedule_escalation(request_id, donor_id, token, delay)
    logger.info(
        "Voice escalation scheduled in-process in %.0fs request=%s",
        delay, request_id,
    )
    return {"ok": True, "localTimer": True, "delaySeconds": delay}


def start_bridge_broadcast(
    bridge: dict,
    donors: pd.DataFrame,
    *,
    patient_name: str | None = None,
    patient_meta: dict | None = None,
) -> dict[str, Any]:
    """Send one WhatsApp to DEMO_OUTREACH_PHONE; schedule voice call if no reply."""
    from shared import bridge_outreach  # noqa: WPS433

    _, db, _, _, _outreach_msg_fn = _engagement()
    donor_rows = _donor_records(bridge, donors)
    if not donor_rows:
        return {"ok": False, "reason": "no_donors_in_bridge"}

    demo = _demo_phone()
    lead = donor_rows[0]
    bridge_req = bridge_outreach.ensure_bridge_request(
        db, bridge, lead, patient_name, patient_meta)
    req_stub = {
        "requestId": bridge_req["requestId"],
        "patientName": bridge_req.get("patientName") or patient_name or "patient",
        "bloodGroup": bridge_req.get("bloodGroup") or bridge.get("bloodGroup") or "blood",
        "hospital": bridge_req.get("hospital"),
        "city": bridge_req.get("city"),
    }
    broadcast_id = str(uuid.uuid4())
    msg = _single_broadcast_message(req_stub, donor_rows, patient_name)
    res = _send_mobilization_whatsapp(demo, msg)
    if not res.get("ok"):
        return {
            "ok": False,
            "reason": "whatsapp_send_failed",
            "error": res.get("error"),
            "demoPhone": demo,
            "senderPhone": f"+{res.get('from', '')}" if res.get("from") else None,
        }

    demo_donor = bridge_outreach.ensure_demo_outreach_donor(db, demo, lead)
    bridge_outreach.record_broadcast_outreach(db, bridge_req, demo_donor)
    token = db.new_id()[:12]

    conv, phone_key = _get_conversation(db, demo)
    if not conv:
        conv = {
            "phone_number": phone_key,
            "conversationId": db.new_id(),
            "channel": "whatsapp",
            "state": "REGISTRATION_COMPLETE",
            "language": "en",
            "userType": "donor",
            "contextData": {},
        }
    bridge_outreach.prime_bridge_conversation(
        conv, bridge, lead, bridge_req, patient_name or req_stub.get("patientName"),
        demo_donor=demo_donor,
    )
    conv["voiceOutreachPlaced"] = False
    conv.pop("voiceOutreachRequestId", None)
    conv.pop("activeVoiceCallId", None)
    conv["escalationCallPlaced"] = False
    conv.pop("escalationCallResult", None)
    conv["bridgeBroadcastId"] = broadcast_id
    conv["broadcastSentAt"] = db.now_iso()
    conv["outreachWhatsappSentAt"] = db.now_iso()
    conv["outreachEscalationToken"] = token
    conv["escalationCallPlaced"] = False
    _save_conversation(db, conv, phone_key)

    escalation = _schedule_voice_escalation(bridge_req["requestId"], demo_donor["donorId"], token)
    delay = escalation.get("delaySeconds", _escalation_delay_sec())
    scheduled = bool(
        escalation.get("ok")
        or escalation.get("scheduled")
        or escalation.get("scheduledOnServer")
        or escalation.get("localTimer")
    )
    sender = res.get("from")
    return {
        "ok": True,
        "broadcastId": broadcast_id,
        "demoPhone": demo,
        "senderPhone": f"+{sender}" if sender else None,
        "provider": res.get("provider"),
        "donorsTargeted": len(donor_rows),
        "messagesSent": 1,
        "callDelaySec": delay,
        "voiceEscalationScheduled": scheduled,
        "voiceEscalationDelaySeconds": delay,
        "voiceEscalationError": None if scheduled else escalation.get("reason"),
        "callScheduled": scheduled,
    }


def start_bridge_broadcast_async(
    bridge: dict,
    donors: pd.DataFrame,
    *,
    patient_name: str | None = None,
) -> dict[str, Any]:
    """Legacy wrapper — prefer start_bridge_broadcast."""
    return start_bridge_broadcast(bridge, donors, patient_name=patient_name)
