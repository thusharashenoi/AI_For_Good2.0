#!/usr/bin/env python3
"""Local webhook server for chatting with RaktSetu on real WhatsApp.

Wraps the same conversational FSM the Lambdas use and exposes the Twilio
webhook endpoints over plain HTTP (stdlib only — no Flask). Replies are returned
as TwiML <Message> elements, so Twilio delivers them to the user with NO outbound
API call needed — meaning you can chat on real WhatsApp even in LOCAL_MODE
(in-memory store, no AWS deploy, no Bedrock access required).

Run:
    python scripts/local_server.py                  # uses PORT from .env (4000)
Then expose it (in another terminal):
    ngrok http 4000
Then set your Twilio WhatsApp Sandbox "When a message comes in" webhook to:
    https://<your-ngrok-subdomain>.ngrok.app/whatsapp     (HTTP POST)

Endpoints:
    POST /whatsapp        inbound WhatsApp messages
    POST /voice/inbound   inbound voice (TwiML)
    POST /voice/outbound  escalation TwiML
    POST /voice/keypress  IVR digit handling
    GET  /health          liveness check
"""
from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import config, dynamodb_client as db  # noqa: E402
from shared.agent_tools import AgentTools  # noqa: E402
from shared.outreach import run_outreach  # noqa: E402
from shared import voice_escalation_scheduler  # noqa: E402
from lambdas.whatsapp_webhook import router  # noqa: E402
from lambdas.voice_inbound import handler as voice_inbound  # noqa: E402
from lambdas.voice_outbound import handler as voice_outbound  # noqa: E402
from lambdas.voice_keypress import handler as voice_keypress  # noqa: E402
from lambdas.periskope_webhook import handler as periskope_webhook  # noqa: E402
from lambdas.vapi_tools import handler as vapi_tools  # noqa: E402
from lambdas.exotel_connect import handler as exotel_connect  # noqa: E402

PORT = int(config.get("PORT", "4000") or "4000")


def _json_response(handler, body: dict, code: int = 200) -> None:
    import json
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _parse_json(handler) -> dict:
    import json
    raw = handler._raw_body()
    if not raw:
        return {}
    return json.loads(raw)


def _handle_internal_test_outreach(body: dict) -> dict:
    """Run donor registration + patient request + outreach inside the server process."""
    donor_phone = body.get("donorPhone") or "+919483399667"
    patient_phone = body.get("patientPhone") or "+919876500099"
    voice_only = bool(body.get("voiceOnly"))

    reg = AgentTools(donor_phone, channel="voice").complete_donor_registration(
        name=body.get("donorName") or "Donor",
        age=int(body.get("age") or 28),
        weight=int(body.get("weight") or 65),
        blood_group=body.get("bloodGroup") or "A+",
        area=body.get("area") or "Bangalore",
        donated_before=False,
    )
    if not reg.get("ok"):
        return {"ok": False, "step": "register_donor", "error": reg}

    req = AgentTools(patient_phone, channel="whatsapp").raise_blood_request(
        patient_name=body.get("patientName") or "Ananya",
        patient_age=int(body.get("patientAge") or 6),
        blood_group=body.get("bloodGroup") or "A+",
        units=1,
        hospital=body.get("hospital") or "NIAT Hospital",
        required_by=body.get("requiredBy") or "tomorrow",
    )
    if not req.get("ok"):
        return {"ok": False, "step": "raise_request", "error": req}

    request_id = req["requestId"]
    if voice_only:
        from lambdas.matching_engine import handler as matching_engine
        from lambdas.trigger_voice import handler as trigger_voice
        from shared.outreach import _prime_donor_conversation

        matching_engine.handler({"requestId": request_id})
        donor = db.get_donor_by_phone(donor_phone)
        _prime_donor_conversation(donor, request_id)
        result = trigger_voice.handler({"requestId": request_id, "donorId": donor["donorId"]})
    elif req.get("outreach"):
        result = req["outreach"]
    else:
        result = run_outreach(request_id, {"topDonorOnly": True})

    return {"ok": True, "requestId": request_id, "result": result}


def _handle_schedule_voice(body: dict) -> dict:
    request_id = body.get("requestId")
    donor_id = body.get("donorId")
    token = body.get("token")
    delay = float(body.get("delaySeconds") or config.get("OUTREACH_VOICE_ESCALATION_SECONDS") or 15)
    if not request_id or not donor_id or not token:
        return {"ok": False, "error": "requestId, donorId, and token required"}
    return voice_escalation_scheduler.schedule_escalation(
        request_id, donor_id, token, delay)


def _twiml_messages(replies):
    msgs = "".join(f"<Message>{escape(r)}</Message>" for r in replies)
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{msgs}</Response>'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("  " + (fmt % args) + "\n")

    def _read_raw(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length).decode("utf-8") if length else ""

    def _raw_body(self):
        return getattr(self, "_raw", "")

    def _send(self, body: str, content_type="application/xml", code=200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send("ok", "text/plain")
        elif self.path.startswith("/exotel/connect"):
            res = exotel_connect.handler({"queryStringParameters": self._query()})
            return self._send(res.get("body", "{}"), "application/json")
        elif self.path.startswith("/voice/outbound"):
            res = voice_outbound.handler({"queryStringParameters": self._query()})
            self._send(res["body"])
        else:
            self._send("RaktSetu local server is running 🩸", "text/plain")

    def _query(self):
        from urllib.parse import urlparse
        q = urlparse(self.path).query
        return {k: v[0] for k, v in parse_qs(q).items()}

    def do_POST(self):
        self._raw = self._read_raw()
        form = {k: v[0] for k, v in parse_qs(self._raw).items()}
        path = self.path.split("?")[0]

        if path == "/whatsapp":
            phone = (form.get("From") or "").replace("whatsapp:", "").strip()
            message = (form.get("Body") or "").strip()
            msg_id = form.get("MessageSid") or ""
            print(f"\n📥 {phone}: {message}")
            if msg_id and db.is_message_processed(msg_id):
                return self._send(_twiml_messages([]))
            replies = router.respond(phone, message, channel="whatsapp")
            if msg_id:
                db.mark_message_processed(msg_id)
            for r in replies:
                print(f"🤖 {r}")
            return self._send(_twiml_messages(replies))

        if path == "/periskope":
            raw = self._raw_body()
            res = periskope_webhook.handler({"body": raw})
            for r in res.get("replies", []) or []:
                print(f"🤖 {r}")
            return self._send(res.get("body", '{"ok":true}'), "application/json")

        if path == "/exotel/connect":
            res = exotel_connect.handler({"queryStringParameters": self._query()})
            return self._send(res.get("body", "{}"), "application/json")

        if path == "/vapi/tools":
            raw = self._raw_body()
            print("📞 Vapi webhook POST /vapi/tools")
            res = vapi_tools.handler({"body": raw})
            return self._send(res.get("body", '{"results":[]}'), "application/json")

        if path == "/internal/outreach/schedule-voice":
            try:
                body = _parse_json(self)
                result = _handle_schedule_voice(body)
                print(f"⏱️  schedule-voice request={body.get('requestId')} delay={body.get('delaySeconds')}s")
                return _json_response(self, result)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                return _json_response(self, {"ok": False, "error": str(exc)}, code=500)

        if path == "/internal/test-outreach":
            try:
                body = _parse_json(self)
                result = _handle_internal_test_outreach(body)
                print(f"🧪 test-outreach request={result.get('requestId')} ok={result.get('ok')}")
                return _json_response(self, result)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                return _json_response(self, {"ok": False, "error": str(exc)}, code=500)

        if path == "/voice/inbound" or path == "/voice/inbound/turn":
            res = voice_inbound.handler({"body": self._urlencode(form)})
            return self._send(res["body"])
        if path == "/voice/outbound":
            res = voice_outbound.handler({"queryStringParameters": self._query(),
                                          "body": self._urlencode(form)})
            return self._send(res["body"])
        if path == "/voice/keypress":
            res = voice_keypress.handler({"queryStringParameters": self._query(),
                                          "body": self._urlencode(form)})
            return self._send(res["body"])

        self._send("not found", "text/plain", code=404)

    @staticmethod
    def _urlencode(form: dict) -> str:
        from urllib.parse import urlencode
        return urlencode(form)


def main():
    os.environ["RAKTSETU_SERVER_PROCESS"] = "1"
    mode = "LOCAL (in-memory store, replies via TwiML)" if config.LOCAL_MODE \
        else "LIVE AWS/Bedrock"
    print("=" * 60)
    print(f" RaktSetu local server  →  http://localhost:{PORT}")
    print(f" Mode: {mode}")
    print(f" WhatsApp webhook path: POST /whatsapp")
    print("=" * 60)
    print(" Next: run `ngrok http %d` and set that URL + /whatsapp" % PORT)
    print("       as your Twilio WhatsApp Sandbox inbound webhook.\n")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
