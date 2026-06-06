#!/usr/bin/env python3
"""Create / update the RaktSetu Vapi voice assistant.

Builds the assistant from the SAME guardrailed tools as WhatsApp, pointing every
tool at your /vapi/tools server URL. After it runs, put the printed assistantId
into .env as VAPI_ASSISTANT_ID.

Usage:
    export VAPI_API_KEY=...
    python scripts/setup_vapi.py --server-url https://<your-ngrok>.ngrok.app

Then (one-time) create/import a phone number in the Vapi dashboard and set
VAPI_PHONE_NUMBER_ID in .env so outbound escalation calls can be placed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import config  # noqa: E402
from shared.agent import VOICE_SYSTEM_PROMPT  # noqa: E402
from shared.agent_tools import TOOL_SCHEMAS  # noqa: E402
from shared.vapi_voice import build_assistant_payload  # noqa: E402

API_BASE = "https://api.vapi.ai"


def build_assistant(server_url: str, name: str) -> dict:
    return build_assistant_payload(server_url, name, VOICE_SYSTEM_PROMPT, TOOL_SCHEMAS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", required=True,
                        help="Public base URL of your server, e.g. https://abc.ngrok.app")
    parser.add_argument("--name", default="RaktSetu Tara")
    parser.add_argument("--print-only", action="store_true",
                        help="Print the assistant JSON without creating it.")
    args = parser.parse_args()

    assistant = build_assistant(args.server_url, args.name)
    if args.print_only:
        print(json.dumps(assistant, indent=2, ensure_ascii=False))
        return

    import requests
    key = config.require("VAPI_API_KEY")
    existing = config.get("VAPI_ASSISTANT_ID")
    if existing:
        resp = requests.patch(f"{API_BASE}/assistant/{existing}",
                              headers={"Authorization": f"Bearer {key}"}, json=assistant, timeout=20)
        action = "updated"
    else:
        resp = requests.post(f"{API_BASE}/assistant",
                             headers={"Authorization": f"Bearer {key}"}, json=assistant, timeout=20)
        action = "created"
    if resp.status_code >= 300:
        print(f"Vapi error {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    assistant_id = data.get("id")
    print(f"Assistant {action}: id={assistant_id}")
    print("→ Put this in .env:  VAPI_ASSISTANT_ID=%s" % assistant_id)
    print(f"→ Tool server URL configured: {args.server_url.rstrip('/')}/vapi/tools")
    print(f"→ Model: {config.get('VAPI_MODEL_PROVIDER', 'google')} / "
          f"{config.get('VAPI_MODEL', 'gemini-2.5-flash')} (voice latency preset)")

    phone_id = config.get("VAPI_PHONE_NUMBER_ID")
    if phone_id and assistant_id:
        # Inbound: leave assistantId unset on the *number* so Vapi sends
        # assistant-request to our server for a personalised greeting. Outbound
        # calls still pass assistantId explicitly in create_outbound_call().
        tools_url = args.server_url.rstrip("/") + "/vapi/tools"
        pr = requests.patch(
            f"{API_BASE}/phone-number/{phone_id}",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "assistantId": None,
                "server": {"url": tools_url, "headers": {"ngrok-skip-browser-warning": "true"}},
            },
            timeout=20,
        )
        if pr.status_code < 300:
            num = pr.json().get("number", "?")
            print(f"→ Phone {num} uses server URL for inbound (personalised greeting)")
            print(f"→ Outbound calls use VAPI_ASSISTANT_ID={assistant_id}")
        else:
            print(f"→ Warning: could not configure phone ({pr.status_code}): {pr.text[:200]}")
            # Fallback: link assistant directly (generic greeting).
            pr2 = requests.patch(
                f"{API_BASE}/phone-number/{phone_id}",
                headers={"Authorization": f"Bearer {key}"},
                json={"assistantId": assistant_id},
                timeout=20,
            )
            if pr2.status_code < 300:
                print(f"→ Phone linked to assistant (generic firstMessage)")
    else:
        print("→ Next: create/import a phone number in the Vapi dashboard, set VAPI_PHONE_NUMBER_ID,")
        print("        then re-run this script to link it to the assistant.")


if __name__ == "__main__":
    main()
