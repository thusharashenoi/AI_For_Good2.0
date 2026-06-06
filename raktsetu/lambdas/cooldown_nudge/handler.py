"""Lambda: cooldown-eligible-nudge  (EventBridge target, FLOW 4-B)

Fires when a donor crosses their 90-day cooldown. Re-evaluates eligibility, flips
the donor to "eligible", and sends the "you can donate again" nudge. If invoked
without a donorId it sweeps all donors whose cooldown has elapsed (daily cron).
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import dynamodb_client as db, eligibility_rules as rules  # noqa: E402
from shared import i18n, twilio_client  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.cooldown_nudge")


def _nudge_one(donor: dict) -> bool:
    status = rules.derive_eligibility_status(donor)
    if status["status"] != "eligible":
        return False
    if donor.get("eligibilityStatus") == "eligible" and donor.get("cooldownNudgeSent"):
        return False  # already nudged this cycle
    donor["eligibilityStatus"] = "eligible"
    donor["cooldownEndsAt"] = None
    donor["cooldownNudgeSent"] = True
    db.save_donor(donor)

    lang = donor.get("preferredLanguage", "en")
    name = (donor.get("name") or "Warrior").split(" ")[0]
    try:
        twilio_client.send_whatsapp(donor["phone"], i18n.t("COOLDOWN_ELIGIBLE_AGAIN", lang, name=name))
    except Exception as exc:
        logger.warning("cooldown nudge send failed: %s", exc)
    return True


def handler(event, context=None):
    donor_id = event.get("donorId")
    if donor_id:
        donor = db.get_donor(donor_id)
        if not donor:
            return {"status": "skipped", "reason": "donor_not_found"}
        return {"status": "nudged" if _nudge_one(donor) else "not_eligible_yet",
                "donorId": donor_id}

    # Sweep mode: nudge everyone who just became eligible.
    nudged = 0
    for donor in db.all_donors():
        # Reset the sent flag if they donated again (back in cooldown).
        if rules.in_cooldown(donor.get("lastDonationDate")):
            if donor.get("cooldownNudgeSent"):
                donor["cooldownNudgeSent"] = False
                db.save_donor(donor)
            continue
        if _nudge_one(donor):
            nudged += 1
    return {"status": "swept", "nudged": nudged}
