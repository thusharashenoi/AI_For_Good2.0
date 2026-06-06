"""Lambda: follow-up-incomplete-registration  (EventBridge target, FLOW 2)

Fires 48h after a partial registration. If the donor is still partial, sends a
follow-up nudge and primes the conversation to resume on YES. Caps attempts at 2
(48h nudge, then one final message ~3 days later) to avoid spamming.
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import config, dynamodb_client as db, i18n, scheduler, twilio_client  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.follow_up_registration")

MAX_ATTEMPTS = 2


def handler(event, context=None):
    phone = event.get("phone")
    conv = db.get_conversation(phone) if phone else None
    if not conv:
        return {"status": "skipped", "reason": "no_conversation"}

    donor = db.get_donor_by_phone(phone)
    # Completed in the meantime -> nothing to do.
    if donor and donor.get("registrationStatus") == "complete":
        return {"status": "skipped", "reason": "already_complete"}

    attempts = conv.get("followUpAttempts", 0)
    if attempts >= MAX_ATTEMPTS:
        return {"status": "skipped", "reason": "max_attempts"}

    lang = conv.get("language", "en")
    name = (conv.get("contextData", {}).get("name") or "there").split(" ")[0]

    if attempts == 0:
        msg = i18n.t("FOLLOW_UP_INCOMPLETE_REGISTRATION", lang, name=name)
        # Schedule the final message ~3 days out.
        scheduler.create_schedule(
            name=f"followup-final-{phone.strip('+')}",
            at=scheduler.in_days(3),
            target_arn=config.get("FOLLOW_UP_ARN", "local"),
            payload={"phone": phone})
    else:
        msg = i18n.t("FOLLOW_UP_FINAL", lang, name=name)

    # Prime resume-on-YES.
    conv["pendingResumeState"] = conv.get("resumeState") or conv.get("state")
    conv["followUpAttempts"] = attempts + 1
    db.save_conversation(conv)

    try:
        twilio_client.send_whatsapp(phone, msg)
    except Exception as exc:
        logger.warning("follow-up send failed: %s", exc)

    return {"status": "follow_up_sent", "attempt": attempts + 1, "phone": phone}
