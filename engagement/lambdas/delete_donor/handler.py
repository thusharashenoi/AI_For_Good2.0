"""Lambda: delete-donor  (DPDP "DELETE MY DATA")

Deletes a user's donor/patient profile and conversation on request. Invoked by
the webhook flow or directly with {"phone": "+91..."}.
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import config, dynamodb_client as db, i18n  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.delete_donor")


def handler(event, context=None):
    phone = event.get("phone")
    if not phone:
        return {"status": "error", "reason": "phone_required"}
    tables = config.table_names()
    deleted = []

    donor = db.get_donor_by_phone(phone)
    if donor:
        db.delete_item(tables["donors"], "donorId", donor["donorId"], "SK", "PROFILE")
        deleted.append("donor")
    patient = db.get_patient_by_phone(phone)
    if patient:
        db.delete_item(tables["patients"], "patientId", patient["patientId"], "SK", "PROFILE")
        deleted.append("patient")
    if db.get_conversation(phone):
        db.delete_item(tables["conversations"], "phone_number", phone)
        deleted.append("conversation")

    logger.info("DPDP delete for %s -> %s", phone, deleted)
    return {"status": "deleted", "phone": phone, "deleted": deleted,
            "message": i18n.t("DATA_DELETED", (donor or patient or {}).get("preferredLanguage", "en"))}
