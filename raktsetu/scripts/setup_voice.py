#!/usr/bin/env python3
"""One-shot voice setup: Vapi assistant + optional Exotel BYO trunk for India calls.

Why Exotel? Vapi's free US number cannot place outbound calls to +91 numbers
(error: vapi-number-international). Wire your Exotel trial ExoPhone as a BYO SIP
trunk so Veeru can call Indian donors.

Prerequisites:
  - VAPI_API_KEY, VAPI_ASSISTANT_ID in .env
  - local_server.py running + ngrok http 4000  (for /vapi/tools during calls)
  - Exotel trial: API key, token, Account SID, ExoPhone; whitelist test numbers

Usage:
    python scripts/setup_voice.py                    # update assistant server URL
    python scripts/setup_voice.py --setup-exotel     # BYO Exotel trunk + phone
    python scripts/setup_voice.py --setup-all        # both
    python scripts/setup_voice.py --status           # print current config

After --setup-exotel, update .env with the printed VAPI_PHONE_NUMBER_ID, then:
    python scripts/test_vapi_call.py --to +91XXXXXXXXXX
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import config  # noqa: E402

API_BASE = "https://api.vapi.ai"
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {config.require('VAPI_API_KEY')}",
            "Content-Type": "application/json"}


def _request(method: str, path: str, payload: Optional[dict] = None):
    import requests
    url = API_BASE + path
    if method == "GET":
        return requests.get(url, headers=_headers(), timeout=20)
    if method == "POST":
        return requests.post(url, headers=_headers(), json=payload, timeout=20)
    if method == "PATCH":
        return requests.patch(url, headers=_headers(), json=payload, timeout=20)
    raise ValueError(method)


def _detect_ngrok() -> Optional[str]:
    try:
        import requests
        resp = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
        for t in resp.json().get("tunnels", []):
            url = t.get("public_url", "")
            if url.startswith("https://"):
                return url.rstrip("/")
    except Exception:
        pass
    return None


def resolve_server_url() -> str:
    url = (config.get("VAPI_SERVER_URL") or "").rstrip("/")
    if url:
        return url
    url = _detect_ngrok()
    if url:
        print(f"→ Detected ngrok: {url}")
        return url
    raise RuntimeError("Set VAPI_SERVER_URL in .env or run `ngrok http 4000`")


def build_assistant_payload(server_url: str) -> dict:
    from shared.agent import VOICE_SYSTEM_PROMPT
    from shared.agent_tools import TOOL_SCHEMAS
    from shared.vapi_voice import build_assistant_payload as _build

    return _build(server_url, "RaktSetu Tara", VOICE_SYSTEM_PROMPT, TOOL_SCHEMAS)


def update_assistant(server_url: str) -> str:
    assistant_id = config.require("VAPI_ASSISTANT_ID")
    payload = build_assistant_payload(server_url)
    resp = _request("PATCH", f"/assistant/{assistant_id}", payload)
    if resp.status_code >= 300:
        raise RuntimeError(f"Vapi assistant update failed ({resp.status_code}): {resp.text[:400]}")
    print(f"✓ Assistant updated — tool server: {server_url.rstrip('/')}/vapi/tools")
    return assistant_id


def _exotel_cfg() -> Dict[str, str]:
    return {
        "key": config.get("EXOTEL_API_KEY") or config.get("EXO_AUTH_KEY") or "",
        "token": config.get("EXOTEL_API_TOKEN") or config.get("EXO_AUTH_TOKEN") or "",
        "sid": config.get("EXOTEL_ACCOUNT_SID") or config.get("EXO_ACCOUNT_SID") or "",
        "phone": config.get("EXOTEL_PHONE_NUMBER") or config.get("PHONE_NUMBER") or "",
        "gateway_ip": config.get("EXOTEL_GATEWAY_IP", "pstn.mum1.exotel.com"),
        "gateway_port": int(config.get("EXOTEL_GATEWAY_PORT", "5070") or "5070"),
    }


def setup_exotel_byo() -> Dict[str, Any]:
    """Create Vapi BYO SIP trunk pointing at Exotel gateway + link trial ExoPhone."""
    cfg = _exotel_cfg()
    missing = [k for k, v in {
        "EXOTEL_API_KEY": cfg["key"],
        "EXOTEL_ACCOUNT_SID": cfg["sid"],
        "EXOTEL_PHONE_NUMBER": cfg["phone"],
    }.items() if not v]
    if missing:
        raise RuntimeError(f"Missing in .env: {', '.join(missing)}")

    assistant_id = config.require("VAPI_ASSISTANT_ID")
    phone = cfg["phone"]
    if not phone.startswith("+"):
        phone = "+" + phone.lstrip("0")

    # Reuse existing BYO phone resource if already configured for this number.
    listed = _request("GET", "/phone-number")
    if listed.status_code == 200:
        for item in listed.json():
            if item.get("number") == phone and item.get("provider") == "byo-phone-number":
                pid = item.get("id")
                print(f"✓ Exotel BYO phone already exists: {pid} ({phone})")
                _patch_env("VAPI_PHONE_NUMBER_ID", pid)
                return {"phone_number_id": pid, "phone": phone, "reused": True}

    stamp = datetime.now().strftime("%m%d%H%M")
    # Vapi rejects hostnames when inbound is enabled; use numeric IP for Exotel Mumbai PoP.
    gateway_ip = cfg["gateway_ip"]
    if not re.match(r"^\d+\.\d+\.\d+\.\d+$", gateway_ip):
        gateway_ip = "129.154.231.198"
    cred_payload = {
        "provider": "byo-sip-trunk",
        "name": f"Exotel RaktSetu {stamp}",
        "gateways": [{
            "ip": gateway_ip,
            "port": cfg["gateway_port"],
            "inboundEnabled": False,
            "outboundEnabled": True,
            "outboundProtocol": "tcp",
            "optionsPingEnabled": True,
        }],
        "outboundLeadingPlusEnabled": True,
    }
    cred_resp = _request("POST", "/credential", cred_payload)
    if cred_resp.status_code >= 300:
        raise RuntimeError(f"BYO credential failed ({cred_resp.status_code}): {cred_resp.text[:500]}")
    credential_id = cred_resp.json().get("id")
    print(f"✓ BYO credential created: {credential_id}")

    phone_payload = {
        "provider": "byo-phone-number",
        "name": f"Exotel {phone} {stamp}",
        "number": phone,
        "numberE164CheckEnabled": False,
        "credentialId": credential_id,
        "assistantId": assistant_id,
    }
    phone_resp = _request("POST", "/phone-number", phone_payload)
    if phone_resp.status_code >= 300:
        raise RuntimeError(f"BYO phone failed ({phone_resp.status_code}): {phone_resp.text[:500]}")
    phone_id = phone_resp.json().get("id")
    print(f"✓ BYO phone linked: {phone_id} → {phone} → assistant {assistant_id}")
    _patch_env("VAPI_PHONE_NUMBER_ID", phone_id)
    print(f"→ .env updated: VAPI_PHONE_NUMBER_ID={phone_id}")
    return {"credential_id": credential_id, "phone_number_id": phone_id, "phone": phone}


def setup_vapi_sip_inbound(sip_user: str = "raktsetu-veeru") -> Dict[str, str]:
    """Create sip:USER@sip.vapi.ai endpoint Exotel Connect can dial (no BYO needed)."""
    assistant_id = config.require("VAPI_ASSISTANT_ID")
    sip_uri = f"sip:{sip_user}@sip.vapi.ai"
    listed = _request("GET", "/phone-number")
    if listed.status_code == 200:
        for item in listed.json():
            if item.get("sipUri") == sip_uri:
                print(f"✓ Vapi SIP inbound already exists: {sip_uri}")
                _patch_env("EXOTEL_VAPI_SIP_URI", sip_uri)
                return {"sip_uri": sip_uri, "phone_number_id": item.get("id")}
    resp = _request("POST", "/phone-number", {
        "provider": "vapi",
        "sipUri": sip_uri,
        "assistantId": assistant_id,
        "name": "RaktSetu Exotel SIP Inbound",
    })
    if resp.status_code >= 300:
        raise RuntimeError(f"Vapi SIP inbound failed ({resp.status_code}): {resp.text[:400]}")
    pid = resp.json().get("id")
    print(f"✓ Vapi SIP inbound created: {sip_uri} (id={pid})")
    _patch_env("EXOTEL_VAPI_SIP_URI", sip_uri)
    return {"sip_uri": sip_uri, "phone_number_id": pid}


def setup_exotel_trunk() -> Dict[str, Any]:
    """Whitelist Vapi IPs on Exotel vSIP trunk (needs API token)."""
    from shared import exotel_client
    if not _exotel_cfg()["token"]:
        raise RuntimeError("EXOTEL_API_TOKEN is required for trunk whitelist (Exotel dashboard → API Settings)")
    result = exotel_client.setup_trunk_for_vapi()
    print(f"✓ Exotel trunk ready: {result['trunk_sid']}")
    print("  Whitelisted Vapi IPs:", ", ".join(exotel_client.VAPI_SBC_IPS))
    return result


def _patch_env(key: str, value: str) -> None:
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, "r", encoding="utf-8") as fh:
        text = fh.read()
    pattern = rf"^{re.escape(key)}=.*$"
    line = f"{key}={value}"
    if re.search(pattern, text, flags=re.MULTILINE):
        text = re.sub(pattern, line, text, count=1, flags=re.MULTILINE)
    else:
        text = text.rstrip() + f"\n{line}\n"
    with open(ENV_PATH, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.environ[key] = value


def print_status() -> None:
    import requests
    server = config.get("VAPI_SERVER_URL") or _detect_ngrok() or "(unset)"
    print("Voice configuration")
    print("  VOICE_PROVIDER:", config.get("VOICE_PROVIDER", "vapi"))
    print("  VAPI_ASSISTANT_ID:", config.get("VAPI_ASSISTANT_ID"))
    print("  VAPI_PHONE_NUMBER_ID:", config.get("VAPI_PHONE_NUMBER_ID"))
    print("  VAPI_SERVER_URL:", server)
    print("  VAPI_LIVE_CALLS:", config.get("VAPI_LIVE_CALLS"))
    exo = _exotel_cfg()
    print("  EXOTEL_PHONE_NUMBER:", exo["phone"] or "(unset — needed for India outbound)")
    try:
        health = requests.get(f"{server}/health", timeout=3).text if server.startswith("http") else "n/a"
        print("  local_server health:", health)
    except Exception as exc:
        print("  local_server health: unreachable (%s)" % exc)
    try:
        aid = config.get("VAPI_ASSISTANT_ID")
        pid = config.get("VAPI_PHONE_NUMBER_ID")
        if aid:
            a = _request("GET", f"/assistant/{aid}").json()
            print("  assistant server:", (a.get("server") or {}).get("url"))
        if pid:
            p = _request("GET", f"/phone-number/{pid}").json()
            print("  call-from number:", p.get("number"), f"({p.get('provider')})")
    except Exception as exc:
        print("  Vapi API:", exc)
    try:
        r = requests.get("https://api.vapi.ai/call", headers=_headers(),
                         params={"limit": 3}, timeout=15)
        if r.status_code == 200:
            for c in r.json()[:3]:
                print("  recent call:", c.get("status"), c.get("endedReason"),
                      c.get("customer", {}).get("number"))
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="RaktSetu voice setup (Vapi + Exotel)")
    parser.add_argument("--setup-exotel", action="store_true", help="Create Exotel BYO trunk on Vapi")
    parser.add_argument("--setup-trunk", action="store_true", help="Whitelist Vapi IPs on Exotel vSIP trunk")
    parser.add_argument("--setup-all", action="store_true", help="Assistant + BYO + trunk whitelist")
    parser.add_argument("--status", action="store_true", help="Print current voice config")
    parser.add_argument("--server-url", help="Override VAPI_SERVER_URL (default: .env or ngrok)")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    if args.setup_trunk:
        setup_exotel_trunk()
        return

    if args.server_url and not args.setup_exotel and not args.setup_all:
        update_assistant(args.server_url.rstrip("/"))
        _patch_env("VAPI_SERVER_URL", args.server_url.rstrip("/"))
        print(f"→ Exotel Connect (use SIP directly): {config.get('EXOTEL_VAPI_SIP_URI', 'sip:raktsetu-veeru@sip.vapi.ai')}")
        return

    if not args.setup_exotel and not args.setup_all:
        parser.print_help()
        print("\nTip: run with --setup-all after filling Exotel vars in .env")
        return

    server_url = args.server_url or resolve_server_url()
    _patch_env("VAPI_SERVER_URL", server_url)

    if args.setup_all:
        update_assistant(server_url)
        setup_vapi_sip_inbound()
        print(f"→ Exotel Connect applet URL: {server_url.rstrip('/')}/exotel/connect")
        setup_exotel_byo()
        if _exotel_cfg()["token"]:
            try:
                setup_exotel_trunk()
            except Exception as exc:
                print(f"! Trunk whitelist skipped/failed: {exc}")
                print("  Add EXOTEL_API_TOKEN and run: python scripts/setup_voice.py --setup-trunk")
        else:
            print("! EXOTEL_API_TOKEN missing — run --setup-trunk after adding it (fixes SIP 403)")
        print("\nDone. Test with:")
        print("  python scripts/test_vapi_call.py --to +91XXXXXXXXXX")
        return

    if args.setup_exotel:
        setup_exotel_byo()
        print("\nDone. Test with:")
        print("  python scripts/test_vapi_call.py --to +91XXXXXXXXXX")


if __name__ == "__main__":
    main()
