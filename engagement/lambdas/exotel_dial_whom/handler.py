"""Legacy Exotel Connect dial-whom URL — returns plain-text E.164/SIP for trial accounts."""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import config  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.exotel_dial_whom")


def handler(event, context=None):
    # Plain-text mode (older Connect URL). SIP may work on some accounts.
    sip = config.get("EXOTEL_VAPI_SIP_URI", "sip:raktsetu-veeru@sip.vapi.ai")
    logger.info("Exotel dial-whom → %s", sip)
    return {"statusCode": 200, "headers": {"Content-Type": "text/plain"}, "body": sip}
