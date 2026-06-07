"""Bridge mobilization broadcast — WhatsApp to bridge donors, Vapi call on no reply."""
from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("raktsetu.broadcast")

DEMO_PHONE = os.environ.get("DEMO_OUTREACH_PHONE", "+919372875356")
CALL_DELAY_SEC = int(os.environ.get("DEMO_OUTREACH_CALL_DELAY_SEC", "30"))

_engagement_path = Path(__file__).resolve().parents[1] / "engagement"
if _engagement_path.is_dir() and str(_engagement_path) not in sys.path:
    sys.path.insert(0, str(_engagement_path))


def _digits(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


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
    for key, val in {
        "STAGE": "dev",
        "LOCAL_MODE": "0",
        "PERISKOPE_LIVE_SENDS": "1",
        "VAPI_LIVE_CALLS": "1",
        "DEMO_OUTREACH_PHONE": "+919372875356",
        "DEMO_OUTREACH_CALL_DELAY_SEC": "30",
        "DYNAMODB_TABLE_CONVERSATIONS": "raktsetu-conversations-dev",
        "DYNAMODB_TABLE_DONORS": "raktsetu-donors-dev",
    }.items():
        os.environ[key] = val
    demo = _digits(os.environ.get("DEMO_OUTREACH_PHONE", "918372875356"))
    periskope = _digits(os.environ.get("PERISKOPE_PHONE", ""))
    bot = _digits(os.environ.get("TWILIO_WHATSAPP_NUMBER", "")) or "919076150904"
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

    # Prefer Twilio so from/to are explicit (bot → demo), like Vapi uses its own caller ID.
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
            logger.warning("Twilio mobilization send failed, trying Periskope: %s", exc)

    res = periskope_client.send_message(to, msg)
    if res.get("ok"):
        return {
            **res,
            "provider": "periskope",
            "from": periskope_client.org_phone(),
            "to": to_digits,
        }
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


def start_bridge_broadcast(
    bridge: dict,
    donors: pd.DataFrame,
    *,
    patient_name: str | None = None,
    patient_meta: dict | None = None,
) -> dict[str, Any]:
    """Send one WhatsApp to DEMO_OUTREACH_PHONE; schedule call escalation separately."""
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
        conv, bridge, lead, bridge_req, patient_name or req_stub.get("patientName"))
    conv["bridgeBroadcastId"] = broadcast_id
    conv["broadcastSentAt"] = db.now_iso()
    conv["escalationCallPlaced"] = False
    _save_conversation(db, conv, phone_key)

    sender = res.get("from")
    return {
        "ok": True,
        "broadcastId": broadcast_id,
        "demoPhone": demo,
        "senderPhone": f"+{sender}" if sender else None,
        "provider": res.get("provider"),
        "donorsTargeted": len(donor_rows),
        "messagesSent": 1,
        "callDelaySec": CALL_DELAY_SEC,
        "callScheduled": True,
    }


def _reply_received(db, demo: str, broadcast_id: str) -> bool:
    conv, _ = _get_conversation(db, demo)
    if conv.get("bridgeBroadcastId") != broadcast_id:
        logger.warning(
            "Broadcast %s: conversation id mismatch (stored=%s) — treating as no reply",
            broadcast_id,
            conv.get("bridgeBroadcastId"),
        )
        return False
    if conv.get("escalationCallPlaced"):
        return True
    return not bool(conv.get("awaitingOutreachReply"))


def run_call_escalation(broadcast_id: str, bridge: dict, patient_name: str | None) -> dict[str, Any]:
    """Wait DEMO_OUTREACH_CALL_DELAY_SEC then place Vapi call if WhatsApp unanswered."""
    demo = _demo_phone()
    logger.info("Broadcast %s: waiting %ss before call escalation to %s", broadcast_id, CALL_DELAY_SEC, demo)
    time.sleep(CALL_DELAY_SEC)

    _, db, _, vapi_client, _ = _engagement()
    if _reply_received(db, demo, broadcast_id):
        logger.info("Broadcast %s: reply received — skipping call", broadcast_id)
        return {"ok": True, "skipped": True, "reason": "reply_received", "broadcastId": broadcast_id}

    donor = db.get_donor_by_phone(demo) or {}
    if not donor:
        for key in _phone_keys(demo):
            donor = db.get_donor_by_phone(key) or {}
            if donor:
                break

    name = (donor.get("name") or patient_name or "there").split()[0]
    variables = {
        "donorName": name,
        "bloodGroup": bridge.get("bloodGroup") or "O+",
        "donorArea": donor.get("area") or donor.get("city") or "Hyderabad",
        "requestId": f"bridge-{bridge.get('bridgeId')}",
        "donorId": donor.get("donorId") or donor_rows_fallback(bridge, db),
        "outreachMode": True,
    }
    result = vapi_client.create_outbound_call(demo, variables=variables)
    conv, phone_key = _get_conversation(db, demo)
    conv["voiceOutreachPlaced"] = bool(result.get("ok"))
    conv["escalationCallPlaced"] = bool(result.get("ok"))
    conv["escalationCallAt"] = db.now_iso()
    conv["escalationCallResult"] = {
        "ok": result.get("ok"),
        "callId": result.get("callId"),
        "error": result.get("error"),
        "local": result.get("local"),
    }
    if result.get("callId"):
        conv["activeVoiceCallId"] = result["callId"]
    _save_conversation(db, conv, phone_key)

    if result.get("ok"):
        logger.info("Broadcast %s: escalation call placed callId=%s", broadcast_id, result.get("callId"))
    else:
        logger.error("Broadcast %s: escalation call failed: %s", broadcast_id, result.get("error"))

    return {"ok": bool(result.get("ok")), "broadcastId": broadcast_id, "call": result}


def donor_rows_fallback(bridge: dict, db) -> str:
    for slot in bridge.get("slots") or []:
        if slot.get("donorId"):
            return str(slot["donorId"])
    return "demo-donor"


def start_bridge_broadcast_async(
    bridge: dict,
    donors: pd.DataFrame,
    *,
    patient_name: str | None = None,
) -> dict[str, Any]:
    """Legacy wrapper — prefer start_bridge_broadcast + run_call_escalation via BackgroundTasks."""
    return start_bridge_broadcast(bridge, donors, patient_name=patient_name)
