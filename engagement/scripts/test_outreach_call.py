#!/usr/bin/env python3
"""End-to-end test: patient needs blood → top donor gets a Vapi outreach call.

WhatsApp YES/NO template is skipped when the call connects; a short confirmation
is sent on WhatsApp after the call ends.

Usage:
    python scripts/test_outreach_call.py
    python scripts/test_outreach_call.py --donor-phone +919483399667 --donor-name Thushara
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import config  # noqa: E402
from shared.agent_tools import AgentTools  # noqa: E402
from shared.outreach import run_outreach  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Test donor outreach call flow")
    parser.add_argument("--donor-phone", default="+919483399667")
    parser.add_argument("--donor-name", default="Thushara")
    parser.add_argument("--blood-group", default="A+")
    parser.add_argument("--area", default="Bangalore")
    parser.add_argument("--hospital", default="NIAT Hospital")
    parser.add_argument("--patient-phone", default="+919876500099")
    parser.add_argument("--voice-only", action="store_true",
                        help="Skip WhatsApp; prime outreach state and call only")
    args = parser.parse_args()

    if config.LOCAL_MODE and config.get("VAPI_LIVE_CALLS") != "1":
        print("Set VAPI_LIVE_CALLS=1 in .env to place a real call.")
        sys.exit(1)

    server = (config.get("VAPI_SERVER_URL") or "").rstrip("/")
    if not server:
        print("Set VAPI_SERVER_URL (ngrok URL) in .env and run local_server.py + ngrok.")
        sys.exit(1)

    print("=== Step 1: Register donor ===")
    donor_tools = AgentTools(args.donor_phone, channel="voice")
    reg = donor_tools.complete_donor_registration(
        name=args.donor_name,
        age=28,
        weight=65,
        blood_group=args.blood_group,
        area=args.area,
        donated_before=False,
    )
    if not reg.get("ok"):
        print(f"Donor registration failed: {reg}")
        sys.exit(1)
    print(f"  Donor registered: {args.donor_name} ({args.blood_group}) at {args.area}")

    print("\n=== Step 2: Patient raises blood request ===")
    patient_tools = AgentTools(args.patient_phone, channel="whatsapp")
    req = patient_tools.raise_blood_request(
        patient_name="Ananya",
        patient_age=6,
        blood_group=args.blood_group,
        units=1,
        hospital=args.hospital,
        required_by="tomorrow",
    )
    if not req.get("ok"):
        print(f"Blood request failed: {req}")
        sys.exit(1)
    request_id = req["requestId"]
    print(f"  Request {request_id} raised for {args.blood_group} at {args.hospital}")

    print("\n=== Step 3: Outreach (voice call — no duplicate WhatsApp template) ===")
    if args.voice_only:
        from shared import dynamodb_client as db
        from lambdas.matching_engine import handler as matching_engine
        from lambdas.trigger_voice import handler as trigger_voice
        from shared.outreach import _prime_donor_conversation

        matching_engine.handler({"requestId": request_id})
        donor = db.get_donor_by_phone(args.donor_phone)
        _prime_donor_conversation(donor, request_id)
        result = trigger_voice.handler({"requestId": request_id})
    elif req.get("outreach"):
        result = req["outreach"]
        print("  (Using outreach already started in step 2 — not re-running)")
    else:
        result = run_outreach(request_id)

    print(json.dumps(result, indent=2, ensure_ascii=False))

    voice = result.get("voiceCall") or result
    voice_placed = result.get("voicePlaced") or voice.get("voicePlaced") or voice.get("callId")
    if voice_placed or voice.get("status") in ("calling", "voice_outreach") or voice.get("ok"):
        print("\n✅ Outreach call initiated (no WhatsApp template sent with the call).")
        print(f"   Donor phone: {args.donor_phone}")
        print(f"   Vapi server: {server}/vapi/tools")
        print("\nWhen your phone rings:")
        print("  • Twilio trial may ask you to press any digit first — press 1–9.")
        print("  • Tara should greet you about the urgent blood need.")
        print("  • After the call ends, you'll get a short WhatsApp confirmation only.")
        print("  • Keep local_server.py running so she can handle your yes/no.")
    else:
        err = voice.get("error") or result.get("status")
        print(f"\n⚠️  Call may not have been placed: {err}")
        if err and "international" in str(err).lower():
            print("   US Vapi numbers often cannot dial +91. Try WhatsApp outreach or Exotel BYO.")
        sys.exit(1)


if __name__ == "__main__":
    main()
