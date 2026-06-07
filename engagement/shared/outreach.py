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
    # Fresh WhatsApp outreach — clear stale voice flags from prior test calls.
    conv["voiceOutreachPlaced"] = False
    conv.pop("voiceOutreachRequestId", None)
    conv.pop("activeVoiceCallId", None)
    db.save_conversation(conv)
    from .voice_booking import prime_proposed_appointment
    prime_proposed_appointment(donor["phone"])


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


def _appointment_for_post_call(conv: dict, req: Optional[dict]) -> Optional[dict]:
    """Resolve appointment details for WhatsApp — booked appt or confirmed proposed slot."""
    appt_id = conv.get("activeAppointmentId")
    if appt_id:
        appt = db.get_appointment(appt_id)
        if appt:
            return appt
    proposed = (conv.get("contextData") or {}).get("proposedAppointment") or {}
    has_slot = bool(proposed.get("date") or proposed.get("time") or proposed.get("spokenWhen"))
    if has_slot and (
        proposed.get("availabilityConfirmed")
        or proposed.get("spokenWhen")
    ):
        return {
            "hospital": proposed.get("hospital") or (req or {}).get("hospital"),
            "appointmentDate": proposed.get("date"),
            "appointmentTime": proposed.get("time") or "10:00 AM",
        }
    return None


def send_post_call_whatsapp(phone: str, call_id: Optional[str] = None) -> Dict:
    """Send post-call WhatsApp — appointment confirmation when booked, generic only if not."""
    conv = db.get_conversation(phone) or {}
    if call_id and conv.get("postCallWhatsappForCallId") == call_id:
        return {"ok": True, "skipped": True, "reason": "already_sent"}

    appt_id = conv.get("activeAppointmentId")
    if appt_id and conv.get("appointmentWhatsappForId") == appt_id:
        return {"ok": True, "skipped": True, "reason": "appointment_already_sent"}

    request_id = conv.get("activeRequestId")
    req = db.get_request(request_id) if request_id else None
    donor = db.get_donor_by_phone(phone) or (
        db.get_donor(conv["donorId"]) if conv.get("donorId") else None
    )
    if not donor:
        return {"ok": False, "reason": "missing_donor"}

    appt = _appointment_for_post_call(conv, req)
    if appt_id and appt:
        return send_appointment_confirmation_whatsapp(phone, appt_id)

    is_voice_outreach = bool(
        conv.get("voiceOutreachPlaced")
        or conv.get("awaitingOutreachReply")
        or appt
        or (conv.get("channel") == "voice" and appt_id)
    )
    if not is_voice_outreach:
        return {"ok": False, "reason": "not_voice_outreach"}

    if not req:
        return {"ok": False, "reason": "missing_context"}

    msg = _post_call_whatsapp_message(donor, req, appt)
    sent = _send_whatsapp_text(phone, msg)

    if sent:
        conv["postCallWhatsappForCallId"] = call_id
        conv["voiceOutreachPlaced"] = False
        if appt_id:
            conv["appointmentWhatsappForId"] = appt_id
        db.save_conversation(conv)
        logger.info("post-call WhatsApp sent phone=%s call=%s appt=%s", phone, call_id, bool(appt))

    return {"ok": sent, "phone": phone, "callId": call_id, "hadAppointment": bool(appt)}


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


def _assigned_outreach_entry(req: dict, donor_id: str) -> Optional[dict]:
    """Return the assigned-donor row eligible for voice escalation."""
    matches = [a for a in req.get("assignedDonors", []) if a.get("donorId") == donor_id]
    if not matches:
        return None
    for status in ("outreach_sent", "voice_outreach", "calling"):
        for entry in matches:
            if entry.get("status") == status:
                return entry
    return matches[-1]


def escalate_to_voice_if_no_reply(request_id: str, donor_id: str,
                                  token: Optional[str] = None) -> Dict:
    """Place an outreach voice call only if the donor has not replied on WhatsApp."""
    from .engagement_imports import load_handler
    trigger_voice = load_handler("lambdas/trigger_voice/handler")

    req = db.get_request(request_id)
    donor = db.get_donor(donor_id) if donor_id else None
    if not req or not donor:
        return {"ok": False, "reason": "missing_context", "requestId": request_id}

    conv = db.get_conversation(donor["phone"]) or {}
    if not conv.get("awaitingOutreachReply") and not conv.get("bridgeOutreach"):
        for alt in (config.get("DEMO_OUTREACH_PHONE"), "+919372875356"):
            if not alt:
                continue
            alt_conv = db.get_conversation(alt)
            if alt_conv and (alt_conv.get("awaitingOutreachReply") or alt_conv.get("bridgeOutreach")):
                conv = alt_conv
                break
    if token and conv.get("outreachEscalationToken") != token:
        return {"ok": False, "reason": "superseded", "requestId": request_id}
    if not conv.get("awaitingOutreachReply"):
        return {"ok": False, "reason": "already_replied", "requestId": request_id}
    if conv.get("voiceOutreachPlaced") and conv.get("voiceOutreachRequestId") == request_id:
        return {"ok": False, "reason": "voice_already_placed", "requestId": request_id}

    entry = _assigned_outreach_entry(req, donor_id)
    if not entry or entry.get("status") != "outreach_sent":
        result = {
            "ok": False,
            "reason": "status_changed",
            "status": (entry or {}).get("status"),
            "requestId": request_id,
        }
        if conv:
            conv["escalationCallPlaced"] = False
            conv["escalationCallResult"] = {**result, "at": db.now_iso()}
            db.save_conversation(conv)
        return result

    voice_result = None
    voice_placed = False
    if config.get("VOICE_PROVIDER", "vapi").lower() == "vapi":
        try:
            voice_result = trigger_voice({
                "requestId": request_id,
                "donorId": donor_id,
            })
            voice_placed = _voice_call_placed(voice_result)
        except Exception as exc:
            logger.warning("outreach voice escalation failed donor=%s: %s", donor_id, exc)

    call_error = (voice_result or {}).get("error")
    if voice_placed:
        entry["status"] = "voice_outreach"
        entry["channel"] = "voice"
        entry["callSid"] = (voice_result or {}).get("callSid") or (voice_result or {}).get("callId")
        entry["voiceEscalatedAt"] = db.now_iso()
        db.save_request(req)
        conv["voiceOutreachPlaced"] = True
        conv["voiceOutreachRequestId"] = request_id
        if voice_result and (voice_result.get("callSid") or voice_result.get("callId")):
            conv["activeVoiceCallId"] = voice_result.get("callSid") or voice_result.get("callId")
        logger.info(
            "outreach: voice escalation placed for %s request=%s", donor["phone"], request_id)
    elif call_error:
        logger.warning(
            "outreach: voice call did not connect for %s request=%s: %s",
            donor["phone"], request_id, call_error,
        )

    if conv:
        conv["escalationCallPlaced"] = voice_placed
        conv["escalationCallResult"] = {
            "ok": voice_placed,
            "reason": call_error if not voice_placed else None,
            "status": "voice_outreach" if voice_placed else "call_failed",
            "callId": (voice_result or {}).get("callId") or (voice_result or {}).get("callSid"),
            "at": db.now_iso(),
        }
        db.save_conversation(conv)

    return {
        "ok": voice_placed,
        "requestId": request_id,
        "donorId": donor_id,
        "voicePlaced": voice_placed,
        "voiceCall": voice_result,
        "reason": call_error if not voice_placed else None,
        "status": "voice_outreach" if voice_placed else "call_failed",
    }


def _contact_donor(req: dict, donor: dict, queue_index: int) -> Dict:
    """Contact one ranked donor — WhatsApp first, voice call after no reply."""
    from . import scheduler

    request_id = req["requestId"]
    req["currentDonorIndex"] = queue_index
    db.save_request(req)

    _prime_donor_conversation(donor, request_id)

    whatsapp_sent = _send_whatsapp_template(donor, req)
    if not whatsapp_sent:
        return {
            "requestId": request_id,
            "status": "outreach_failed",
            "donorId": donor["donorId"],
            "donorPhone": donor.get("phone"),
        }

    token = db.new_id()[:12]
    conv = db.get_conversation(donor["phone"]) or {}
    conv["outreachWhatsappSentAt"] = db.now_iso()
    conv["outreachEscalationToken"] = token
    db.save_conversation(conv)

    escalation = scheduler.schedule_outreach_voice_escalation(
        request_id, donor["donorId"], token)

    assigned = req.setdefault("assignedDonors", [])
    if not any(a.get("donorId") == donor["donorId"] for a in assigned):
        assigned.append({
            "donorId": donor["donorId"],
            "status": "outreach_sent",
            "outreachAt": db.now_iso(),
            "channel": "whatsapp",
        })
    db.save_request(req)

    delay = escalation.get("delaySeconds") or float(
        config.get("OUTREACH_VOICE_ESCALATION_SECONDS") or 7)

    logger.info(
        "outreach: WhatsApp sent to %s request=%s — voice in %.0fs if no reply",
        donor["phone"], request_id, delay,
    )

    return {
        "requestId": request_id,
        "status": "outreach_sent",
        "donorId": donor["donorId"],
        "donorPhone": donor.get("phone"),
        "whatsappSent": True,
        "voicePlaced": False,
        "voiceEscalationScheduled": True,
        "voiceEscalationDelaySeconds": delay,
        "voiceCall": None,
    }


def _broadcast_to_eligible_pool(request_id: str, extra: Optional[Dict] = None) -> Dict:
    """Notify all eligible matching donors from the ranked pool (not a fixed bridge list)."""
    from lambdas.matching_engine import handler as matching_engine

    extra = extra or {}
    if extra.get("topDonorOnly"):
        return _send_to_top_donor(request_id)

    ranking = matching_engine.handler({"requestId": request_id})
    req = db.get_request(request_id)
    if not req:
        return {"requestId": request_id, "status": "error", "reason": "request_not_found"}

    queue = req.get("rankedDonorQueue") or []
    if not queue:
        logger.warning("outreach: no eligible donors for request %s", request_id)
        return {"requestId": request_id, "status": "no_donors", "donorsRemaining": 0}

    max_contact = int(extra.get("maxDonors") or config.get("OUTREACH_BROADCAST_MAX") or "20")
    contacts = []
    for idx, donor_id in enumerate(queue[:max_contact]):
        if _donor_already_contacted(req, donor_id):
            continue
        donor = db.get_donor(donor_id)
        if not donor:
            continue
        req = db.get_request(request_id) or req
        result = _contact_donor(req, donor, idx)
        if result.get("status") != "outreach_failed":
            contacts.append(result)

    return {
        "requestId": request_id,
        "status": "broadcast_started" if contacts else "no_new_contacts",
        "bloodGroup": req.get("bloodGroup"),
        "rankedCount": len(queue),
        "donorsContacted": len(contacts),
        "donorsRemaining": max(0, len(queue) - len(contacts)),
        "contacts": contacts,
        "ranking": ranking,
    }


def _send_to_top_donor(request_id: str) -> Dict:
    """Rank donors and contact the current top match (WhatsApp, then voice if no reply)."""
    from lambdas.matching_engine import handler as matching_engine

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

    result = _contact_donor(req, donor, idx)
    result["donorsRemaining"] = remaining
    return result


def run_outreach(request_id: str, extra: Optional[Dict] = None) -> Dict:
    """Start donor outreach — notify eligible matching donors from the ranked pool."""
    extra = extra or {}
    logger.info("Starting outreach for request %s extra=%s", request_id, extra)
    if extra.get("topDonorOnly") or extra.get("replacement"):
        return _send_to_top_donor(request_id)
    return _broadcast_to_eligible_pool(request_id, extra)
