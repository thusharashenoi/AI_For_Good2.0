"""Exotel voice helpers — Connect API + vSIP trunk management for Vapi BYO."""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from . import config

logger = logging.getLogger("raktsetu.exotel")

VAPI_SBC_IPS = ("44.229.228.186", "44.238.177.138")
DEFAULT_DOMAIN = "api.exotel.com"


def _cfg() -> Dict[str, str]:
    return {
        "key": config.get("EXOTEL_API_KEY") or config.get("EXO_AUTH_KEY") or "",
        "token": config.get("EXOTEL_API_TOKEN") or config.get("EXO_AUTH_TOKEN") or "",
        "sid": config.get("EXOTEL_ACCOUNT_SID") or config.get("EXO_ACCOUNT_SID") or "",
        "domain": config.get("EXOTEL_API_DOMAIN", DEFAULT_DOMAIN),
        "phone": config.get("EXOTEL_PHONE_NUMBER") or "",
        "flow_url": config.get("EXOTEL_FLOW_URL") or "",
    }


def _auth_header() -> str:
    cfg = _cfg()
    if not cfg["key"] or not cfg["token"]:
        raise RuntimeError("EXOTEL_API_KEY and EXOTEL_API_TOKEN are required")
    raw = f"{cfg['key']}:{cfg['token']}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _request(method: str, url: str, data: Optional[dict] = None, json_body: Optional[dict] = None):
    import requests
    headers = {"Authorization": _auth_header()}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
        return requests.request(method, url, headers=headers, json=json_body, timeout=20)
    if data is not None:
        return requests.request(method, url, headers=headers, data=data, timeout=20)
    return requests.request(method, url, headers=headers, timeout=20)


def _e164(number: str) -> str:
    n = number.strip().replace(" ", "").replace("-", "")
    if n.startswith("+"):
        return n
    if n.startswith("0"):
        return "+91" + n[1:]
    if len(n) == 10:
        return "+91" + n
    return "+" + n


def _parse_call_xml(text: str) -> Dict[str, str]:
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
        call = root.find("Call")
        if call is None:
            return {}
        return {child.tag: (child.text or "") for child in call}
    except Exception:
        return {}


def connect_to_flow(to: str, flow_url: Optional[str] = None,
                    caller_id: Optional[str] = None) -> Dict[str, Any]:
    """Outbound: dial `to`, run Exotel flow when they answer (trial-friendly path)."""
    cfg = _cfg()
    url = flow_url or cfg["flow_url"]
    if not url:
        raise RuntimeError("Set EXOTEL_FLOW_URL (Exotel applet that bridges to Vapi SIP)")
    caller = _e164(caller_id or cfg["phone"])
    to_num = _e164(to)
    # Exotel expects CallerId without +; From is the callee (donor) for connect-to-flow.
    caller_display = caller.lstrip("+")
    if caller_display.startswith("91") and len(caller_display) > 10:
        caller_display = "0" + caller_display[2:]  # 08047284351 style for landline
    endpoint = f"https://{cfg['domain']}/v1/Accounts/{cfg['sid']}/Calls/connect"
    payload = {"From": to_num, "CallerId": caller_display, "Url": url, "CallType": "trans"}
    resp = _request("POST", endpoint, data=payload)
    if resp.status_code >= 300:
        return {"ok": False, "error": resp.text, "status": resp.status_code}
    data = _parse_call_xml(resp.text)
    if not data:
        return {"ok": False, "error": resp.text[:500], "status": resp.status_code}
    return {"ok": True, "sid": data.get("Sid"), "status": data.get("Status"), **data}


def _v2_base() -> str:
    cfg = _cfg()
    return f"https://{cfg['domain']}/v2/accounts/{cfg['sid']}"


def list_trunks() -> List[dict]:
    resp = _request("GET", _v2_base() + "/trunks")
    if resp.status_code >= 300:
        raise RuntimeError(f"Exotel trunks list failed ({resp.status_code}): {resp.text[:400]}")
    out = []
    for item in resp.json().get("response") or []:
        if item.get("status") == "success" and item.get("data"):
            out.append(item["data"])
    return out


def get_or_create_trunk(name: str = "RaktSetu Vapi") -> str:
    for trunk in list_trunks():
        if (trunk.get("status") or "").lower() == "active":
            return trunk["trunk_sid"]
    resp = _request("POST", _v2_base() + "/trunks", json_body={"trunk_name": name})
    if resp.status_code >= 300:
        raise RuntimeError(f"Exotel trunk create failed ({resp.status_code}): {resp.text[:400]}")
    data = (resp.json().get("response") or [{}])[0].get("data") or {}
    return data["trunk_sid"]


def whitelist_vapi_ips(trunk_sid: str) -> None:
    for ip in VAPI_SBC_IPS:
        resp = _request("POST", f"{_v2_base()}/trunks/{trunk_sid}/whitelisted-ips",
                        json_body={"ip": ip, "mask": 32})
        if resp.status_code >= 300 and "Duplicate" not in resp.text:
            logger.warning("Whitelist %s: %s", ip, resp.text[:200])


def map_phone_to_trunk(trunk_sid: str, phone: Optional[str] = None) -> None:
    num = _e164(phone or _cfg()["phone"])
    resp = _request("POST", f"{_v2_base()}/trunks/{trunk_sid}/phone-numbers",
                    json_body={"phone_number": num})
    if resp.status_code >= 300 and "Duplicate" not in resp.text:
        raise RuntimeError(f"Map phone failed ({resp.status_code}): {resp.text[:400]}")


def setup_trunk_for_vapi() -> Dict[str, str]:
    """Whitelist Vapi SBC IPs + map ExoPhone — fixes SIP 403 on BYO outbound."""
    trunk_sid = get_or_create_trunk()
    whitelist_vapi_ips(trunk_sid)
    map_phone_to_trunk(trunk_sid)
    return {"trunk_sid": trunk_sid}
