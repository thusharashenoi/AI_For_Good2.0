"""DynamoDB access layer for RaktSetu.

Exposes a thin repository API on top of the five core tables. In LOCAL_MODE a
file-backed in-memory store is used instead of boto3 so the conversation test
harness (scripts/test_whatsapp_flow.py) runs with zero AWS dependencies while
exercising the exact same code paths as production.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

from . import config
from .legacy_schema import normalize_donor, normalize_patient, normalize_request

# ---------------------------------------------------------------------------
# Local file-backed store (LOCAL_MODE=1)
# ---------------------------------------------------------------------------
_LOCAL_PATH = os.environ.get("LOCAL_STATE_PATH", "local_state.json")
_lock = threading.Lock()


class _LocalStore:
    """Minimal DynamoDB stand-in. Keyed as {table: {pk|pk#sk: item}}."""

    def __init__(self, path: str):
        self.path = path
        self._data: Dict[str, Dict[str, dict]] = {}
        self._mtime: float = 0.0
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    self._data = json.load(fh)
                self._mtime = os.path.getmtime(self.path)
            except Exception:
                self._data = {}
        else:
            self._data = {}

    def _maybe_reload(self) -> None:
        """Reload when another process (e.g. local_server.py) updated the file."""
        if not os.path.exists(self.path):
            return
        mtime = os.path.getmtime(self.path)
        if mtime > self._mtime:
            self._load()

    def _flush(self) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, default=str)
        self._mtime = os.path.getmtime(self.path)

    @staticmethod
    def _key(pk: str, sk: Optional[str]) -> str:
        return f"{pk}#{sk}" if sk else pk

    def put(self, table: str, item: dict, pk_attr: str, sk_attr: Optional[str]) -> None:
        with _lock:
            self._maybe_reload()
            self._data.setdefault(table, {})
            key = self._key(item[pk_attr], item.get(sk_attr) if sk_attr else None)
            self._data[table][key] = item
            self._flush()

    def get(self, table: str, pk: str, sk: Optional[str]) -> Optional[dict]:
        with _lock:
            self._maybe_reload()
            return self._data.get(table, {}).get(self._key(pk, sk))

    def scan(self, table: str) -> List[dict]:
        with _lock:
            self._maybe_reload()
            return list(self._data.get(table, {}).values())

    def delete(self, table: str, pk: str, sk: Optional[str]) -> None:
        with _lock:
            self._maybe_reload()
            self._data.get(table, {}).pop(self._key(pk, sk), None)
            self._flush()


_local_store: Optional[_LocalStore] = None


def _store() -> _LocalStore:
    global _local_store
    if _local_store is None:
        _local_store = _LocalStore(_LOCAL_PATH)
    return _local_store


# ---------------------------------------------------------------------------
# boto3 resource (production)
# ---------------------------------------------------------------------------
_resource = None


def _ddb():
    global _resource
    if _resource is None:
        import boto3

        _resource = boto3.resource("dynamodb", region_name=config.region())
    return _resource


def _table(name: str):
    return _ddb().Table(name)


def _to_ddb(obj: Any) -> Any:
    """Recursively convert floats -> Decimal (DynamoDB requirement)."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, list):
        return [_to_ddb(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_ddb(v) for k, v in obj.items()}
    return obj


def _from_ddb(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, list):
        return [_from_ddb(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _from_ddb(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id() -> str:
    return str(uuid.uuid4())


def put_item(table: str, item: dict, pk_attr: str, sk_attr: Optional[str] = None) -> dict:
    if config.LOCAL_MODE:
        _store().put(table, item, pk_attr, sk_attr)
    else:
        _table(table).put_item(Item=_to_ddb(item))
    return item


def get_item(table: str, pk_attr: str, pk_val: str,
             sk_attr: Optional[str] = None, sk_val: Optional[str] = None) -> Optional[dict]:
    if config.LOCAL_MODE:
        return _store().get(table, pk_val, sk_val)
    key = {pk_attr: pk_val}
    if sk_attr and sk_val is not None:
        key[sk_attr] = sk_val
    resp = _table(table).get_item(Key=key)
    item = resp.get("Item")
    return _from_ddb(item) if item else None


def scan(table: str) -> List[dict]:
    if config.LOCAL_MODE:
        return _store().scan(table)
    items: List[dict] = []
    resp = _table(table).scan()
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = _table(table).scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return [_from_ddb(i) for i in items]


def delete_item(table: str, pk_attr: str, pk_val: str,
                sk_attr: Optional[str] = None, sk_val: Optional[str] = None) -> None:
    if config.LOCAL_MODE:
        _store().delete(table, pk_val, sk_val)
        return
    key = {pk_attr: pk_val}
    if sk_attr and sk_val is not None:
        key[sk_attr] = sk_val
    _table(table).delete_item(Key=key)


# ---------------------------------------------------------------------------
# Domain repositories
# ---------------------------------------------------------------------------
T = config.table_names


# --- Conversations ---
def get_conversation(phone: str) -> Optional[dict]:
    return get_item(T()["conversations"], "phone_number", phone)


def save_conversation(conv: dict) -> dict:
    conv["lastMessageAt"] = now_iso()
    return put_item(T()["conversations"], conv, "phone_number")


# --- Donors ---
def get_donor(donor_id: str) -> Optional[dict]:
    item = get_item(T()["donors"], "donorId", donor_id, "SK", "PROFILE")
    return normalize_donor(item) if item else None


def get_donor_by_phone(phone: str) -> Optional[dict]:
    conv = get_conversation(phone)
    if conv and conv.get("donorId"):
        donor = get_donor(conv["donorId"])
        if donor and donor.get("phone") == phone:
            return donor
    for d in scan(T()["donors"]):
        if d.get("phone") == phone:
            return normalize_donor(d)
    return None


def save_donor(donor: dict) -> dict:
    donor.setdefault("SK", "PROFILE")
    donor["updatedAt"] = now_iso()
    donor.setdefault("createdAt", donor["updatedAt"])
    return put_item(T()["donors"], donor, "donorId", "SK")


def all_donors() -> List[dict]:
    return [
        normalize_donor(d)
        for d in scan(T()["donors"])
        if d.get("SK") == "PROFILE"
    ]


# --- Patients ---
def get_patient(patient_id: str) -> Optional[dict]:
    item = get_item(T()["patients"], "patientId", patient_id, "SK", "PROFILE")
    return normalize_patient(item) if item else None


def get_patient_by_phone(phone: str) -> Optional[dict]:
    for p in scan(T()["patients"]):
        if p.get("phone") == phone:
            return normalize_patient(p)
    return None


def save_patient(patient: dict) -> dict:
    patient.setdefault("SK", "PROFILE")
    patient["updatedAt"] = now_iso()
    patient.setdefault("createdAt", patient["updatedAt"])
    return put_item(T()["patients"], patient, "patientId", "SK")


# --- Blood requests ---
def get_request(request_id: str) -> Optional[dict]:
    item = get_item(T()["requests"], "requestId", request_id, "SK", "REQUEST")
    return normalize_request(item) if item else None


def save_request(req: dict) -> dict:
    req.setdefault("SK", "REQUEST")
    return put_item(T()["requests"], req, "requestId", "SK")


def open_requests() -> List[dict]:
    return [r for r in scan(T()["requests"])
            if r.get("SK") == "REQUEST" and r.get("status") in ("open", "outreach_started")]


# --- Appointments ---
def get_appointment(appointment_id: str) -> Optional[dict]:
    return get_item(T()["appointments"], "appointmentId", appointment_id, "SK", "APPT")


def save_appointment(appt: dict) -> dict:
    appt.setdefault("SK", "APPT")
    return put_item(T()["appointments"], appt, "appointmentId", "SK")


def appointments_for_donor(donor_id: str) -> List[dict]:
    return [a for a in scan(T()["appointments"])
            if a.get("SK") == "APPT" and a.get("donorId") == donor_id]


# --- Waitlist (under-age / under-weight prospects) ---
def add_to_waitlist(entry: dict) -> dict:
    entry.setdefault("createdAt", now_iso())
    return put_item(T()["waitlist"], entry, "phone")


# --- Idempotency (processed Twilio message ids) ---
def is_message_processed(message_id: str) -> bool:
    if not message_id:
        return False
    return get_item(T()["processed_messages"], "messageId", message_id) is not None


def mark_message_processed(message_id: str) -> None:
    if not message_id:
        return
    put_item(
        T()["processed_messages"],
        {"messageId": message_id, "processedAt": now_iso(),
         # 7-day TTL so the dedupe table self-cleans
         "ttl": int(time.time()) + 7 * 24 * 3600},
        "messageId",
    )
