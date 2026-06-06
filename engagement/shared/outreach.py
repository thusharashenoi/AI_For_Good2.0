"""Donor outreach pipeline — Step Functions in production, inline fallback otherwise."""
from __future__ import annotations

import logging
from typing import Dict, Optional

from . import config, dynamodb_client as db, periskope_client

logger = logging.getLogger("raktsetu.outreach")


def _outreach_whatsapp_message(donor: dict, req: dict) -> str:
    """WhatsApp-only fallback when voice call cannot be placed."""
    from .branding import BOT_NAME

    name = (donor.get("name") or "Friend").split(" ")[0]
    hospital = req.get("hospital") or "a nearby hospital"
    area = req.get("city") or donor.get("area") or "Hyderabad"
    bg = req.get("bloodGroup") or "blood"
    return (
        f"🩸 *Urgent blood donation request — RaktSetu / Blood Warriors*\n\n"
        f"Hi {name},\n\n"
        f"A Thalassemia patient at *{hospital}, {area}* urgently needs "
        f"*{bg} blood*.\n\n"
        f"You registered as a donor — can you help save a life?\n\n"
        f"Reply:\n"
        f"✅ *YES* — I can donate\n"
        f"❌ *NO* — not right now\n"
        f"⏰ *LATER* — remind me tomorrow\n\n"
        f"— {BOT_NAME}"
    )


def _post_call_whatsapp_message(donor: dict, req: dict, appt: Optional[dict] = None) -> str:
    """Short confirmation after a voice outreach call — not the YES/NO template."""
    from .branding import BOT_NAME
    from .datetime_utils import format_display

    name = (donor.get("name") or "there").split(" ")[0]
    hospital = req.get("hospital") or "the hospital"
    bg = req.get("bloodGroup") or "blood"
    if appt:
        date_disp, time_disp = format_display(
            appt.get("appointmentDate", ""), appt.get("appointmentTime", "10:00 AM"))
        return (
            f"Hi {name}, thank you for taking our call. 🩸\n\n"
            f"Your donation for the urgent {bg} need is confirmed:\n"
            f"📍 {appt.get('hospital') or hospital}\n"
            f"📅 {date_disp} at {time_disp}\n\n"
            f"Please eat light, stay hydrated, and bring ID.\n\n"
            f"— {BOT_NAME}, Blood Warriors"
        )
    return (
        f"Hi {name}, thank you for speaking with us about the urgent {bg} need "
        f"at {hospital}.\n\n"
        f"We appreciate your time. If you agreed to donate, we will follow up with "
        f"next steps soon. If not, thank you for being a Blood Warrior. 🙏\n\n"
        f"— {BOT_NAME}, Blood Warriors"
    )


def _prime_donor_conversation(donor: dict, request_id: str) -> None:
    conv = db.get_conversation(donor["phone"]) or {
        "phone_number": donor["phone"], "conversationId": db.new_id(),
        "channel": "voice", "state": "REGISTRATION_COMPLETE",
        "language": donor.get("preferredLanguage", "en"), "userType": "donor",
        "contextData": {},
    }
    conv["awaitingOutreachReply"] = True
    conv["activeRequestId"] = request_id
    conv["donorId"] = donor["donorId"]
    db.save_conversation(conv)


def _donor_already_contacted(req: dict, donor_id: str) -> bool:
    for entry in req.get("assignedDonors") or []:
        if entry.get("donorId") != donor_id:
            continue
        if entry.get("status") in ("outreach_sent", "voice_outreach", "calling", "confirmed"):
            return True
    return False


def _voice_call_placed(voice_result: Optional[dict]) -> bool:
    if not voice_result or voice_result.get("status") == "error":
        return False
    if voice_result.get("ok") is False:
        return False
    return bool(
        voice_result.get("callSid")
        or voice_result.get("callId")
        or voice_result.get("voicePlaced")
    )


def _send_whatsapp_template(donor: dict, req: dict) -> bool:
    wa_provider = (config.get("WA_PROVIDER") or "twilio").lower()
    if wa_provider == "periskope":
        msg = _outreach_whatsapp_message(donor, req)
        res = periskope_client.send_message(donor["phone"], msg)
        if not res.get("ok"):
            logger.error("Periskope outreach failed: %s", res.get("error"))
        return bool(res.get("ok"))
    from lambdas.send_whatsapp import handler as send_whatsapp
    res = send_whatsapp.handler({"requestId": req["requestId"]})
    return res.get("status") == "outreach_sent"


def send_post_call_whatsapp(phone: str, call_id: Optional[str] = None) -> Dict:
    """Send a single post-call WhatsApp confirmation (not the outreach template)."""
    conv = db.get_conversation(phone) or {}
    if call_id and conv.get("postCallWhatsappForCallId") == call_id:
        return {"ok": True, "skipped": True, "reason": "already_sent"}

    appt_id = conv.get("activeAppointmentId")
    if appt_id and conv.get("appointmentWhatsappForId") == appt_id:
        return {"ok": True, "skipped": True, "reason": "appointment_already_sent"}

    is_voice_outreach = bool(
        conv.get("voiceOutreachPlaced")
        or conv.get("awaitingOutreachReply")
        or (conv.get("channel") == "voice" and appt_id)
    )
    if not is_voice_outreach:
        return {"ok": False, "reason": "not_voice_outreach"}

    request_id = conv.get("activeRequestId")
    req = db.get_request(request_id) if request_id else None
    donor = db.get_donor_by_phone(phone) or (
        db.get_donor(conv["donorId"]) if conv.get("donorId") else None
    )
    if not req or not donor:
        return {"ok": False, "reason": "missing_context"}

    appt = db.get_appointment(appt_id) if appt_id else None

    msg = _post_call_whatsapp_message(donor, req, appt)
    sent = _send_whatsapp_text(phone, msg)

    if sent:
        conv["postCallWhatsappForCallId"] = call_id
        conv["voiceOutreachPlaced"] = False
        if appt_id:
            conv["appointmentWhatsappForId"] = appt_id
        db.save_conversation(conv)
        logger.info("post-call WhatsApp sent phone=%s call=%s", phone, call_id)

    return {"ok": sent, "phone": phone, "callId": call_id}


def send_appointment_confirmation_whatsapp(phone: str,
                                           appointment_id: Optional[str] = None) -> Dict:
    """Send booking confirmation immediately after voice appointment is booked."""
    conv = db.get_conversation(phone) or {}
    appt_id = appointment_id or conv.get("activeAppointmentId")
    if not appt_id:
        return {"ok": False, "reason": "no_appointment"}
    if conv.get("appointmentWhatsappForId") == appt_id:
        return {"ok": True, "skipped": True, "reason": "already_sent"}

    appt = db.get_appointment(appt_id)
    if not appt:
        return {"ok": False, "reason": "appointment_not_found"}

    donor = db.get_donor_by_phone(phone) or (
        db.get_donor(conv["donorId"]) if conv.get("donorId") else None
    )
    req = db.get_request(appt.get("requestId")) if appt.get("requestId") else None
    if not donor:
        return {"ok": False, "reason": "missing_donor"}

    msg = _post_call_whatsapp_message(donor, req or {}, appt)
    sent = _send_whatsapp_text(phone, msg)
    if sent:
        conv["appointmentWhatsappForId"] = appt_id
        db.save_conversation(conv)
        logger.info("appointment WhatsApp sent phone=%s appt=%s", phone, appt_id)
    return {"ok": sent, "phone": phone, "appointmentId": appt_id}


def _send_whatsapp_text(phone: str, msg: str) -> bool:
    wa_provider = (config.get("WA_PROVIDER") or "twilio").lower()
    if wa_provider == "periskope":
        res = periskope_client.send_message(phone, msg)
        return bool(res.get("ok"))
    from shared import twilio_client
    try:
        twilio_client.send_whatsapp(phone, msg)
        return True
    except Exception as exc:
        logger.warning("WhatsApp send failed: %s", exc)
        return False


def _send_to_top_donor(request_id: str) -> Dict:
    """Rank donors and contact the top match (voice first, WhatsApp template fallback)."""
    from lambdas.matching_engine import handler as matching_engine
    from lambdas.trigger_voice import handler as trigger_voice

    ranking = matching_engine.handler({"requestId": request_id})
    remaining = ranking.get("donorsRemaining", 0)
    if remaining <= 0:
        logger.warning("outreach: no eligible donors for request %s", request_id)
        return {"requestId": request_id, "status": "no_donors", "donorsRemaining": 0}

    req = db.get_request(request_id)
    if not req:
        return {"requestId": request_id, "status": "error", "reason": "request_not_found"}

    queue = req.get("rankedDonorQueue") or []
    idx = req.get("currentDonorIndex", 0)
    donor = db.get_donor(queue[idx]) if idx < len(queue) else None
    if not donor:
        return {"requestId": request_id, "status": "no_donors"}

    if _donor_already_contacted(req, donor["donorId"]):
        logger.info("outreach: donor %s already contacted for %s", donor["donorId"], request_id)
        return {
            "requestId": request_id,
            "status": "already_contacted",
            "donorId": donor["donorId"],
            "donorsRemaining": remaining,
        }

    _prime_donor_conversation(donor, request_id)

    voice_result = None
    voice_placed = False
    if config.get("VOICE_PROVIDER", "vapi").lower() == "vapi":
        try:
            voice_result = trigger_voice.handler({"requestId": request_id})
            voice_placed = _voice_call_placed(voice_result)
        except Exception as exc:
            logger.warning("Vapi outreach call failed: %s", exc)

    whatsapp_sent = False
    channel = "voice" if voice_placed else "whatsapp"
    status = "voice_outreach" if voice_placed else "outreach_sent"

    if voice_placed:
        conv = db.get_conversation(donor["phone"]) or {}
        conv["voiceOutreachPlaced"] = True
        if voice_result and voice_result.get("callSid"):
            conv["activeVoiceCallId"] = voice_result["callSid"]
        db.save_conversation(conv)
        logger.info("outreach: voice call placed for %s — skipping WhatsApp template", donor["phone"])
    else:
        whatsapp_sent = _send_whatsapp_template(donor, req)
        if not whatsapp_sent:
            return {
                "requestId": request_id,
                "status": "outreach_failed",
                "donorId": donor["donorId"],
                "donorsRemaining": remaining,
                "voiceCall": voice_result,
            }

    assigned = req.setdefault("assignedDonors", [])
    if not any(a.get("donorId") == donor["donorId"] for a in assigned):
        assigned.append({
            "donorId": donor["donorId"],
            "status": status,
            "outreachAt": db.now_iso(),
            "channel": channel,
            "callSid": (voice_result or {}).get("callSid"),
        })
    db.save_request(req)

    return {
        "requestId": request_id,
        "status": status,
        "donorId": donor["donorId"],
        "donorsRemaining": remaining,
        "whatsappSent": whatsapp_sent,
        "voicePlaced": voice_placed,
        "voiceCall": voice_result,
    }


def run_outreach(request_id: str, extra: Optional[Dict] = None) -> Dict:
    """Start donor outreach for a blood request."""
    extra = extra or {}
    logger.info("Starting outreach for request %s extra=%s", request_id, extra)
    return _send_to_top_donor(request_id)
