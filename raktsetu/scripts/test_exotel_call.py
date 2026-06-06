#!/usr/bin/env python3
"""Place a test outbound call via Exotel Connect API (trial-friendly).

Requires EXOTEL_API_KEY, EXOTEL_API_TOKEN, EXOTEL_ACCOUNT_SID, EXOTEL_FLOW_URL.
The donor number must be whitelisted in Exotel trial settings.

Usage:
    python scripts/test_exotel_call.py --to +919876543210
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import exotel_client  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", required=True, help="Whitelisted donor E.164, e.g. +919876543210")
    args = parser.parse_args()
    print(f"Placing Exotel call to {args.to} …")
    result = exotel_client.connect_to_flow(args.to)
    if result.get("ok"):
        print(f"Call placed: sid={result.get('sid')} status={result.get('status')}")
    else:
        print(f"Call failed: {result.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
