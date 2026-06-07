#!/usr/bin/env python3
"""End-to-end test: patient needs blood → WhatsApp → voice call after 15s no reply.

In LOCAL_MODE this runs through local_server.py so timers and DB state stay in one process.

Usage:
    python scripts/test_outreach_call.py
    python scripts/test_outreach_call.py --donor-phone +919483399667 --donor-name Thushara
    python scripts/test_outreach_call.py --voice-only   # skip WhatsApp, call immediately
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import config  # noqa: E402


def _local_server_base() -> str:
    port = config.get("PORT", "4000") or "4000"
    return (config.get("LOCAL_SERVER_URL") or f"http://127.0.0.1:{port}").rstrip("/")


def _run_via_server(args) -> dict:
    import requests
    base = _local_server_base()
    try:
        health = requests.get(f"{base}/health", timeout=3)
        health.raise_for_status()
    except Exception as exc:
        print(f"Local server not reachable at {base}: {exc}")
        print("Start it: screen -dmS raktsetu scripts/run_server.sh")
        sys.exit(1)

    payload = {
        "donorPhone": args.donor_phone,
        "donorName": args.donor_name,
        "bloodGroup": args.blood_group,
        "area": args.area,
        "hospital": args.hospital,
        "patientPhone": args.patient_phone,
        "voiceOnly": args.voice_only,
    }
    resp = requests.post(f"{base}/internal/test-outreach", json=payload, timeout=60)
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        print(json.dumps(body, indent=2, ensure_ascii=False))
        sys.exit(1)
    return body


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

    if config.LOCAL_MODE:
        os.environ.setdefault(
            "LOCAL_STATE_PATH",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "local_state.json"),
        )
        print("=== Running outreach via local server (shared state + timers) ===")
        body = _run_via_server(args)
        request_id = body["requestId"]
        result = body.get("result") or {}
        print(f"  Request {request_id}")
    else:
        from shared.agent_tools import AgentTools
        from shared.outreach import run_outreach

        print("=== Step 1: Register donor ===")
        reg = AgentTools(args.donor_phone, channel="voice").complete_donor_registration(
            name=args.donor_name, age=28, weight=65,
            blood_group=args.blood_group, area=args.area, donated_before=False)
        if not reg.get("ok"):
            sys.exit(1)
        req = AgentTools(args.patient_phone, channel="whatsapp").raise_blood_request(
            patient_name="Ananya", patient_age=6, blood_group=args.blood_group,
            units=1, hospital=args.hospital, required_by="tomorrow")
        if not req.get("ok"):
            sys.exit(1)
        request_id = req["requestId"]
        result = req.get("outreach") or run_outreach(request_id, {"topDonorOnly": True})

    print(json.dumps(result, indent=2, ensure_ascii=False))

    voice = result.get("voiceCall") or result
    voice_placed = result.get("voicePlaced") or voice.get("voicePlaced") or voice.get("callId")
    contacts = result.get("contacts") or []
    donor_contact = next(
        (c for c in contacts if c.get("donorPhone") == args.donor_phone),
        result if result.get("donorPhone") == args.donor_phone else {},
    )
    escalation_scheduled = (
        result.get("voiceEscalationScheduled")
        or donor_contact.get("voiceEscalationScheduled")
    )
    delay = float(
        result.get("voiceEscalationDelaySeconds")
        or donor_contact.get("voiceEscalationDelaySeconds")
        or config.get("OUTREACH_VOICE_ESCALATION_SECONDS")
        or 15
    )

    if escalation_scheduled and not voice_placed and not args.voice_only:
        print(f"\n⏳ Waiting {delay:.0f}s on server for voice call (do NOT reply on WhatsApp)...")
        time.sleep(delay + 2)
        from shared import dynamodb_client as db
        conv = db.get_conversation(args.donor_phone) or {}
        call_id = conv.get("activeVoiceCallId")
        placed_for_request = conv.get("voiceOutreachRequestId") == request_id
        if conv.get("voiceOutreachPlaced") and placed_for_request and call_id:
            voice_placed = True
            print(f"   Voice call placed: {call_id}")
        else:
            print("   Voice call was NOT placed — check /tmp/raktsetu-server.log")
            print(f"   (voiceOutreachPlaced={conv.get('voiceOutreachPlaced')}, "
                  f"request match={placed_for_request})")

    if voice_placed or voice.get("status") in ("calling", "voice_outreach") or voice.get("ok"):
        print("\n✅ Voice outreach call placed.")
        print(f"   Donor phone: {args.donor_phone}")
        print(f"   Vapi server: {server}/vapi/tools")
        print("\nWhen your phone rings:")
        print("  • Twilio trial may ask you to press any digit first — press 1–9.")
        print("  • Tara asks if you can donate before the blood deadline.")
    elif escalation_scheduled or result.get("whatsappSent") or donor_contact.get("whatsappSent"):
        print("\n✅ WhatsApp sent — voice call should ring after ~15s if you don't reply.")
        print(f"   Donor phone: {args.donor_phone}")
        print("   Keep the server running: screen -r raktsetu")
    else:
        err = voice.get("error") or result.get("status")
        print(f"\n⚠️  Outreach issue: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
