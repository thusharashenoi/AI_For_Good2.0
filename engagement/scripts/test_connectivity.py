#!/usr/bin/env python3
"""Verify live credentials: AWS identity, Bedrock model access, Twilio account.

Reads config from raktsetu/.env (auto-loaded by shared.config). Run with
LOCAL_MODE=0 to actually hit the services.

Usage:
    LOCAL_MODE=0 python scripts/test_connectivity.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import config  # noqa: E402  (auto-loads .env)

OK, FAIL, WARN = "\033[92m✓\033[0m", "\033[91m✗\033[0m", "\033[93m!\033[0m"


def check_aws_identity():
    import boto3
    sts = boto3.client("sts", region_name=config.region())
    ident = sts.get_caller_identity()
    print(f"{OK} AWS identity: account={ident['Account']} arn={ident['Arn']}")
    return True


def check_bedrock():
    from shared import bedrock_client
    for model_id in bedrock_client._model_chain():
        try:
            import boto3
            rt = boto3.client("bedrock-runtime", region_name=config.region())
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 32,
                "messages": [{"role": "user",
                              "content": [{"type": "text",
                                           "text": "Reply with exactly: RAKTSETU OK"}]}],
            }
            resp = rt.invoke_model(modelId=model_id, body=json.dumps(body))
            payload = json.loads(resp["body"].read())
            text = "".join(p.get("text", "") for p in payload.get("content", [])).strip()
            print(f"{OK} Bedrock model '{model_id}' responded: {text!r}")
            return True
        except Exception as exc:
            print(f"{WARN} Bedrock model '{model_id}' failed: {exc}")
    print(f"{FAIL} No Bedrock model was reachable (enable model access in "
          f"{config.region()} console).")
    return False


def check_twilio():
    from twilio.rest import Client
    sid = config.require("TWILIO_ACCOUNT_SID")
    token = config.require("TWILIO_AUTH_TOKEN")
    client = Client(sid, token)
    acct = client.api.accounts(sid).fetch()
    print(f"{OK} Twilio account: {acct.friendly_name} (status={acct.status})")
    wa = config.get("TWILIO_WHATSAPP_NUMBER")
    voice = config.get("TWILIO_VOICE_NUMBER")
    print(f"    WhatsApp sender: {wa or '(unset)'} | Voice number: {voice or '(unset — FLOW 6 disabled)'}")
    if not config.get("TWILIO_TEMPLATE_SID_DONATION_REQUEST"):
        print(f"{WARN} TWILIO_TEMPLATE_SID_DONATION_REQUEST unset — FLOW 4 proactive outreach disabled.")
    return True


def main():
    if config.LOCAL_MODE:
        print(f"{WARN} LOCAL_MODE=1 — set LOCAL_MODE=0 to test live services. Exiting.")
        return

    results = []
    for name, fn in (("AWS", check_aws_identity), ("Bedrock", check_bedrock), ("Twilio", check_twilio)):
        try:
            results.append(fn())
        except Exception as exc:
            print(f"{FAIL} {name} check errored: {exc}")
            results.append(False)

    print("\nSummary:", "all good 🩸" if all(results) else "some checks failed — see above.")


if __name__ == "__main__":
    main()
