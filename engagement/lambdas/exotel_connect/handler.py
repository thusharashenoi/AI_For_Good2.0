"""Exotel programmable Connect webhook — return Vapi SIP URI for Veeru."""
from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import config  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raktsetu.exotel_connect")

# Exotel Connect accepts SIP URI; dot-form also works on some accounts.
DEFAULT_SIP = "sip:raktsetu-veeru@sip.vapi.ai"
ALT_SIP = "sip:raktsetu-veeru.sip.vapi.ai:5060;transport=tcp"


def handler(event, context=None):
    sip = config.get("EXOTEL_VAPI_SIP_URI", DEFAULT_SIP)
    body = {
        "fetch_after_attempt": False,
        "destination": [{"contact_uri": sip}, {"contact_uri": ALT_SIP}],
        "max_ringing_duration": 45,
        "max_conversation_duration": 1800,
        "music_on_hold": {"type": "operator_tone"},
        "recording": {"record": False},
    }
    logger.info("Exotel connect → %s", sip)
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
