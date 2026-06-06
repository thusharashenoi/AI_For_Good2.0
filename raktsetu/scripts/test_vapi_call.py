#!/usr/bin/env python3
"""Place a test outbound Vapi call (FLOW 6 escalation path).

Requires VAPI_API_KEY, VAPI_ASSISTANT_ID, VAPI_PHONE_NUMBER_ID in .env.
Set VAPI_LIVE_CALLS=1 (or LOCAL_MODE=0) so the call is actually placed.

Usage:
    python scripts/test_vapi_call.py --to +91XXXXXXXXXX
    python scripts/test_vapi_call.py --to +91XXXXXXXXXX --name Rahul --blood-group O+
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import config, vapi_client  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Place a test Vapi outbound call")
    parser.add_argument("--to", required=True, help="Recipient E.164 number, e.g. +919876543210")
    parser.add_argument("--name", default="there", help="Donor first name for the greeting")
    parser.add_argument("--blood-group", default="O+", help="Blood group context var")
    parser.add_argument("--area", default="Hyderabad", help="Donor area context var")
    args = parser.parse_args()

    if config.LOCAL_MODE and config.get("VAPI_LIVE_CALLS") != "1":
        print("Set VAPI_LIVE_CALLS=1 in .env (or LOCAL_MODE=0) to place a real call.")
        sys.exit(1)

    variables = {
        "donorName": args.name,
        "bloodGroup": args.blood_group,
        "donorArea": args.area,
        "requestId": "test-request",
        "donorId": "test-donor",
    }
    print(f"Placing Vapi call to {args.to} …")
    print(
        "NOTE: If the caller ID is a Twilio trial number (+1325…), Twilio plays:\n"
        '  "You have a trial account… Press any key to execute your code."\n'
        "  Press any digit (1–9) on your phone — then Veeru will connect.\n"
        "  Upgrade Twilio or use Exotel BYO to remove this permanently."
    )
    result = vapi_client.create_outbound_call(args.to, variables=variables)
    if result.get("ok"):
        print(f"Call placed: callId={result.get('callId')}")
    else:
        print(f"Call failed: {result.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
