#!/usr/bin/env python3
"""Local WhatsApp conversation harness — walk every flow without Twilio.

Directly invokes the whatsapp-webhook Lambda handler in LOCAL_MODE and prints
the bot's reply. Conversation state persists between calls via the file-backed
local store (local_state.json), so you can step through a flow one message at a
time exactly like a real chat.

Usage:
    python scripts/test_whatsapp_flow.py --phone +919876543210 --message "Hi"
    python scripts/test_whatsapp_flow.py --phone +919876543210 --message "1"
    python scripts/test_whatsapp_flow.py --phone +919876543210 --message "Rahul Kumar"

Interactive mode (REPL):
    python scripts/test_whatsapp_flow.py --phone +919876543210 --interactive

Replay a scripted conversation:
    python scripts/test_whatsapp_flow.py --phone +91... --script "Hi|1|Rahul Kumar|30|70|O+|Madhapur|No"

Production note: to drive the *deployed* Lambda instead, use:
    sam local invoke WhatsappWebhookFunction -e events/whatsapp.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LOCAL_MODE", "1")

from shared import dynamodb_client as db  # noqa: E402
from lambdas.whatsapp_webhook import handler as webhook  # noqa: E402


def send(phone: str, message: str):
    event = {"From": f"whatsapp:{phone}", "Body": message, "MessageSid": db.new_id()}
    res = webhook.handler(event)
    return res.get("replies", [])


def _print_turn(phone: str, message: str):
    print(f"\n\033[94m[{phone}] You:\033[0m {message}")
    for reply in send(phone, message):
        print(f"\033[92mVeeru:\033[0m {reply}")
    conv = db.get_conversation(phone)
    if conv:
        print(f"\033[90m  (state={conv.get('state')} lang={conv.get('language')} "
              f"type={conv.get('userType')})\033[0m")


def main():
    parser = argparse.ArgumentParser(description="RaktSetu WhatsApp flow harness")
    parser.add_argument("--phone", required=True)
    parser.add_argument("--message")
    parser.add_argument("--script", help="Pipe-separated messages to replay in order.")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--reset", action="store_true", help="Wipe this phone's conversation first.")
    args = parser.parse_args()

    if args.reset:
        conv = db.get_conversation(args.phone)
        if conv:
            db.delete_item(db.config.table_names()["conversations"], "phone_number", args.phone)

    if args.script:
        for msg in args.script.split("|"):
            _print_turn(args.phone, msg.strip())
        return

    if args.interactive:
        print("Interactive mode — type messages (Ctrl-C to exit).")
        try:
            while True:
                msg = input(f"[{args.phone}] You: ").strip()
                if not msg:
                    continue
                for reply in send(args.phone, msg):
                    print(f"Veeru: {reply}")
        except (KeyboardInterrupt, EOFError):
            print("\nbye 🩸")
        return

    if not args.message:
        parser.error("Provide --message, --script, or --interactive")
    _print_turn(args.phone, args.message)


if __name__ == "__main__":
    main()
