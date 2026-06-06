#!/usr/bin/env python3
"""FLOW 4/6: reach a donor about an urgent blood request (voice, WhatsApp fallback).

Vapi free US numbers cannot call India. This script tries Vapi first; on that
failure it sends the same outreach message on WhatsApp via Periskope.

Usage:
    python scripts/trigger_donation_outreach.py --to +919483399667 \\
        --name Thushara --blood-group "A+" --area Bangalore --hospital NIAT
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import config, periskope_client, vapi_client  # noqa: E402


def _whatsapp_message(name: str, blood_group: str, area: str, hospital: str) -> str:
    return (
        f"🩸 *Urgent blood donation request — RaktSetu / Blood Warriors*\n\n"
        f"Hi {name},\n\n"
        f"A Thalassemia patient at *{hospital}, {area}* urgently needs "
        f"*{blood_group} blood*.\n\n"
        f"You registered as a donor — can you help save a life?\n\n"
        f"Reply:\n"
        f"✅ *YES* — I can donate\n"
        f"❌ *NO* — not right now\n"
        f"⏰ *LATER* — remind me tomorrow\n\n"
        f"— {BOT_NAME} 🤖"
    )


def _voice_first_message(name: str, blood_group: str, area: str, hospital: str) -> str:
    from shared.branding import BOT_NAME, ORG_SPOKEN  # noqa: E402
    return (
        f"Namaste {name}, I'm {BOT_NAME} from {ORG_SPOKEN}. "
        f"A patient at {hospital} in {area} urgently needs {blood_group} blood. "
        f"You signed up as a donor — can you help save a life today?"
    )


def main():
    parser = argparse.ArgumentParser(description="Donor outreach: Vapi call → WhatsApp fallback")
    parser.add_argument("--to", required=True, help="Donor E.164 number")
    parser.add_argument("--name", default="there")
    parser.add_argument("--blood-group", default="O+")
    parser.add_argument("--area", default="Hyderabad")
    parser.add_argument("--hospital", default="a nearby hospital")
    parser.add_argument("--whatsapp-only", action="store_true",
                        help="Skip Vapi; send WhatsApp only")
    args = parser.parse_args()

    variables = {
        "donorName": args.name.split()[0],
        "bloodGroup": args.blood_group,
        "donorArea": args.area,
    }

    if not args.whatsapp_only and (config.get("VAPI_LIVE_CALLS") == "1" or not config.LOCAL_MODE):
        import requests

        body = {
            "assistantId": config.require("VAPI_ASSISTANT_ID"),
            "phoneNumberId": config.require("VAPI_PHONE_NUMBER_ID"),
            "customer": {"number": args.to, "name": args.name.split()[0]},
            "assistantOverrides": {
                "variableValues": variables,
                "firstMessage": _voice_first_message(
                    args.name.split()[0], args.blood_group, args.area, args.hospital),
            },
        }
        try:
            resp = requests.post(
                "https://api.vapi.ai/call",
                headers=vapi_client._headers(),
                json=body,
                timeout=20,
            )
            if resp.status_code < 300:
                print(f"✅ Vapi call placed to {args.to}: callId={resp.json().get('id')}")
                return
            err = resp.json().get("message") or resp.text
            print(f"⚠️  Vapi call failed ({resp.status_code}): {err}")
            if "international" not in err.lower():
                sys.exit(1)
            print("→ Falling back to WhatsApp (free Vapi US numbers cannot call India).")
        except Exception as exc:
            print(f"⚠️  Vapi error: {exc}")
            print("→ Falling back to WhatsApp.")

    msg = _whatsapp_message(args.name.split()[0], args.blood_group, args.area, args.hospital)
    res = periskope_client.send_message(args.to, msg)
    if res.get("ok"):
        print(f"✅ WhatsApp outreach sent to {args.to}")
    else:
        print(f"❌ WhatsApp send failed: {res.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
