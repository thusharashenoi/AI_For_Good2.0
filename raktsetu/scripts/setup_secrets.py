#!/usr/bin/env python3
"""Create or update the raktsetu/config secret in AWS Secrets Manager from .env.

Usage:
    python scripts/setup_secrets.py
    python scripts/setup_secrets.py --region us-east-1
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import config  # noqa: E402 — loads .env

# Keys Lambdas read via shared.config (env or Secrets Manager).
SECRET_KEYS = [
    "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_NUMBER",
    "TWILIO_VOICE_NUMBER", "TWILIO_TEMPLATE_SID_DONATION_REQUEST",
    "BEDROCK_MODEL_ID", "BEDROCK_FALLBACK_MODEL_ID", "BEDROCK_CHEAP_MODEL_ID",
    "BEDROCK_AGENT_ID", "BEDROCK_AGENT_ALIAS_ID",
    "WA_PROVIDER", "PERISKOPE_PHONE", "PERISKOPE_API_KEY", "PERISKOPE_LIVE_SENDS",
    "VOICE_PROVIDER", "VAPI_API_KEY", "VAPI_ASSISTANT_ID", "VAPI_PHONE_NUMBER_ID",
    "VAPI_LIVE_CALLS", "VAPI_VOICE_PROVIDER", "VAPI_VOICE_ID", "VAPI_VOICE_VERSION",
    "VAPI_MODEL_PROVIDER", "VAPI_MODEL", "VAPI_MODEL_MAX_TOKENS",
    "VAPI_WAIT_SECONDS", "VAPI_ON_PUNCTUATION_SECONDS", "VAPI_ON_NO_PUNCTUATION_SECONDS",
    "VAPI_ON_NUMBER_SECONDS", "VAPI_TRANSCRIBER_PROVIDER", "VAPI_TRANSCRIBER_MODEL",
    "VAPI_TRANSCRIBER_LANGUAGE",
    "NOMINATIM_USER_AGENT", "DEFAULT_CITY", "EMERGENCY_HELPLINE",
    "AGENT_FORCE_BEDROCK",
]


def _payload() -> dict:
    out = {}
    for key in SECRET_KEYS:
        val = config.get(key)
        if val:
            out[key] = val
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default=config.region())
    parser.add_argument("--secret-id", default=config.get("SECRETS_MANAGER_SECRET_ID", "raktsetu/config"))
    args = parser.parse_args()

    import boto3

    payload = _payload()
    if not payload.get("TWILIO_ACCOUNT_SID"):
        print("ERROR: TWILIO_ACCOUNT_SID missing — check raktsetu/.env")
        sys.exit(1)

    client = boto3.client("secretsmanager", region_name=args.region)
    body = json.dumps(payload)
    try:
        client.describe_secret(SecretId=args.secret_id)
        client.put_secret_value(SecretId=args.secret_id, SecretString=body)
        print(f"Updated secret {args.secret_id!r} ({len(payload)} keys) in {args.region}")
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(Name=args.secret_id, SecretString=body)
        print(f"Created secret {args.secret_id!r} ({len(payload)} keys) in {args.region}")


if __name__ == "__main__":
    main()
