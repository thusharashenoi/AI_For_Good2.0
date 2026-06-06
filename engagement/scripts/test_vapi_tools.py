#!/usr/bin/env python3
"""Exercise the Vapi voice tool-calls webhook locally (no phone call needed).

Simulates the exact `tool-calls` payloads Vapi POSTs to /vapi/tools during a
call, bound to a caller's phone number, and prints the {"results": [...]} that
the assistant would receive. Use this to verify the guardrailed tools behave on
the voice channel before wiring a real Vapi number.

Examples:
    LOCAL_MODE=1 python scripts/test_vapi_tools.py --phone +919876500099
    LOCAL_MODE=1 python scripts/test_vapi_tools.py --phone +919876500099 \
        --call get_state \
        --call 'complete_donor_registration {"name":"Asha Rao","age":28,"weight":62,"blood_group":"A+","area":"Madhapur"}' \
        --call get_my_profile
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lambdas.vapi_tools import handler as vapi_tools  # noqa: E402


def _make_payload(phone: str, name: str, arguments: dict) -> dict:
    return {"message": {
        "type": "tool-calls",
        "toolCallList": [{"id": f"toolu_{uuid.uuid4().hex[:12]}",
                          "name": name, "arguments": arguments}],
        "call": {"id": "call-sim", "type": "inboundPhoneCall",
                 "customer": {"number": phone}}}}


def _parse_call(spec: str):
    spec = spec.strip()
    if " " in spec:
        name, raw = spec.split(" ", 1)
        try:
            args = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Bad JSON args for '{name}': {exc}")
    else:
        name, args = spec, {}
    return name, args


DEFAULT_SCRIPT = [
    ("get_state", {}),
    ("complete_donor_registration",
     {"name": "Voice Tester", "age": 30, "weight": 65, "blood_group": "O+", "area": "Gachibowli"}),
    ("get_my_profile", {}),
    ("get_matching_requests", {}),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", default="+919876500099")
    parser.add_argument("--call", action="append", default=[],
                        help="tool call as 'name {json-args}'. Repeatable. "
                             "If omitted, runs a default donor-registration script.")
    args = parser.parse_args()

    calls = [_parse_call(c) for c in args.call] if args.call else DEFAULT_SCRIPT

    print(f"Simulating Vapi tool-calls for caller {args.phone}\n" + "-" * 60)
    for name, tool_args in calls:
        payload = _make_payload(args.phone, name, tool_args)
        resp = vapi_tools.handler({"body": json.dumps(payload)})
        body = json.loads(resp["body"])
        result = body["results"][0]
        out = result.get("result", result.get("error"))
        try:
            out = json.dumps(json.loads(out), indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            pass
        print(f"\n▶ {name}({json.dumps(tool_args, ensure_ascii=False)})")
        print(f"  → {out}")
    print("\n" + "-" * 60 + "\nDone. HTTP status was 200 for every call (as Vapi requires).")


if __name__ == "__main__":
    main()
