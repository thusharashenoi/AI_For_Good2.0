"""DynamoDB adapter: bridge between live tables and the in-memory pipeline.

Responsibilities:
  - read Donors/Patients items into the SAME DataFrame shape that
    ``data_processing.clean`` produces, so graph/scoring/buffer/eligibility run
    unchanged against live data;
  - write Donor/Patient profile items (used by the seed script);
  - read/write the Bridges table (META + SLOT items) that we own;
  - start Step Functions executions to mobilize donors for a vacant slot.

Numbers are stored as DynamoDB ``Decimal``; we convert to/from float here and
drop NaN/None so items stay clean. Dates are stored as ISO strings.
"""
from __future__ import annotations

import decimal
from typing import Any, Iterable

import boto3
import numpy as np
import pandas as pd
from boto3.dynamodb.conditions import Key

from . import config
from . import engagement_adapter as adapt

_session = boto3.session.Session(region_name=config.AWS_REGION)
_ddb = _session.resource("dynamodb")
_sfn = _session.client("stepfunctions")


# --------------------------------------------------------------------------
# Serialization helpers
# --------------------------------------------------------------------------
def _clean_value(v: Any) -> Any:
    """Convert a Python value to a DynamoDB-safe value, or None to drop it."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else decimal.Decimal(str(round(f, 6)))
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.date().isoformat()
    if isinstance(v, list):
        return [_clean_value(x) for x in v if _clean_value(x) is not None]
    if isinstance(v, str):
        return v
    # Scalar NaN (e.g. numpy nan that slipped through) -> drop.
    try:
        if np.isscalar(v) and pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _to_item(d: dict) -> dict:
    """Drop empty values and coerce types for a DynamoDB put_item."""
    out = {}
    for k, v in d.items():
        cv = _clean_value(v)
        if cv is None:
            continue
        if isinstance(cv, str) and cv == "":
            continue
        out[k] = cv
    return out


def _num(v, default=np.nan) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _date(v):
    return pd.to_datetime(v, errors="coerce") if v not in (None, "") else pd.NaT


# --------------------------------------------------------------------------
# Writers (seeding)
# --------------------------------------------------------------------------
def donor_item(row: pd.Series) -> dict:
    """Map a cleaned donor DataFrame row to a Donors profile item."""
    return _to_item({
        "donorId": row.get("user_id"),
        "SK": config.PROFILE_SK,
        "name": row.get("name"),
        "bloodGroup": row.get("blood_group_norm"),
        "role": row.get("role"),
        "donorType": row.get("donor_type"),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "eligibilityStatus": row.get("eligibility_status"),
        "nextEligibleDate": row.get("next_eligible_date"),
        "lastDonationDate": row.get("last_donation_date"),
        "lastContactedDate": row.get("last_contacted_date"),
        "frequencyInDays": row.get("frequency_in_days"),
        "donationsTillDate": row.get("donations_till_date"),
        "noShows": row.get("no_shows"),
        "showRate": row.get("show_rate"),
        "reliabilityScore": row.get("reliability_score"),
        "willingnessScore": row.get("willingness"),
        "userDonationActiveStatus": row.get("user_donation_active_status"),
        "currentBridgeId": row.get("bridge_id"),
        "city": row.get("city"),
    })


def patient_item(row: pd.Series) -> dict:
    """Map a cleaned patient DataFrame row to a Patients profile item."""
    return _to_item({
        "patientId": row.get("user_id"),
        "SK": config.PROFILE_SK,
        "name": row.get("name"),
        "bloodGroup": row.get("blood_group_norm"),
        "bridgeBloodGroup": row.get("bridge_blood_group_norm"),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "frequencyInDays": row.get("frequency_in_days"),
        "lastTransfusionDate": row.get("last_transfusion_date"),
        "expectedNextTransfusionDate": row.get("expected_next_transfusion_date"),
        "quantityRequired": row.get("quantity_required"),
        "city": row.get("city"),
        "bridgeId": row.get("bridge_id"),
    })


def _batch_put_chunk(table_name: str, chunk: list[dict]) -> int:
    table = _ddb.Table(table_name)
    n = 0
    with table.batch_writer() as bw:
        for it in chunk:
            if it and it.get(next(iter(it))):  # has a non-empty PK
                bw.put_item(Item=it)
                n += 1
    return n


def batch_put(table_name: str, items: Iterable[dict], workers: int = 8) -> int:
    """Parallel batch write (much faster over WAN than a single writer)."""
    from concurrent.futures import ThreadPoolExecutor

    items = list(items)
    if not items:
        return 0
    size = max(1, (len(items) + workers - 1) // workers)
    chunks = [items[i:i + size] for i in range(0, len(items), size)]
    with ThreadPoolExecutor(max_workers=len(chunks)) as ex:
        return sum(ex.map(lambda c: _batch_put_chunk(table_name, c), chunks))


# --------------------------------------------------------------------------
# Readers (DynamoDB -> pipeline DataFrame shape)
# --------------------------------------------------------------------------
def _scan_segment(table_name: str, segment: int, total: int, fexpr) -> list[dict]:
    table = _ddb.Table(table_name)
    items, kwargs = [], {"Segment": segment, "TotalSegments": total}
    if fexpr is not None:
        kwargs["FilterExpression"] = fexpr
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def _scan_profiles(table_name: str, total_segments: int = 8) -> list[dict]:
    """Parallel (segmented) scan for PROFILE items — much faster over WAN."""
    from concurrent.futures import ThreadPoolExecutor

    fexpr = Key("SK").eq(config.PROFILE_SK)
    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=total_segments) as ex:
        for chunk in ex.map(lambda s: _scan_segment(table_name, s, total_segments, fexpr),
                            range(total_segments)):
            items.extend(chunk)
    return items


def load_donors_df(ref_date: str | None = None) -> pd.DataFrame:
    """Read Donors profile items into the pipeline's donor DataFrame shape."""
    ref = pd.Timestamp(ref_date or config.REFERENCE_DATE)
    rows = []
    for it in _scan_profiles(config.TABLE_DONORS):
        row = adapt.donor_row(it, ref)
        if row is None:
            continue
        last_contact = row.get("last_contacted_date")
        if pd.notna(last_contact):
            row["days_since_last_contact"] = (ref - last_contact).days
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=["user_id"])
    return pd.DataFrame(rows)


def load_patients_df() -> pd.DataFrame:
    """Read Patients profile items into the pipeline's patient DataFrame shape."""
    request_items = _scan_table(config.TABLE_REQUESTS) if _table_exists(config.TABLE_REQUESTS) else []
    coords = adapt.request_coords_by_patient(request_items)
    rows = []
    for it in _scan_profiles(config.TABLE_PATIENTS):
        rows.append(adapt.patient_row(it, coords))
    if not rows:
        return pd.DataFrame(columns=["user_id"])
    return pd.DataFrame(rows)


def _table_exists(name: str) -> bool:
    try:
        _ddb.meta.client.describe_table(TableName=name)
        return True
    except Exception:
        return False


def _scan_table(table_name: str) -> list[dict]:
    table = _ddb.Table(table_name)
    items, kwargs = [], {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def load_open_requests(limit: int = 20) -> list[dict]:
    """Open blood requests from the automation portal (for admin visibility)."""
    if not _table_exists(config.TABLE_REQUESTS):
        return []
    open_status = {"open", "outreach_started"}
    rows = []
    for it in _scan_table(config.TABLE_REQUESTS):
        if it.get("SK") != "REQUEST":
            continue
        if str(it.get("status", "")).lower() not in open_status:
            continue
        rows.append({
            "requestId": it.get("requestId"),
            "patientId": it.get("patientId"),
            "patientName": it.get("patientName"),
            "bloodGroup": it.get("bloodGroup"),
            "hospital": it.get("hospital"),
            "city": it.get("city"),
            "requiredBy": it.get("requiredBy"),
            "urgencyLevel": it.get("urgencyLevel"),
            "status": it.get("status"),
            "unitsNeeded": it.get("unitsNeeded"),
        })
    rows.sort(key=lambda r: str(r.get("requiredBy") or ""))
    return rows[:limit]


def link_patient_bridge(patient_id: str, bridge_id: str) -> None:
    """Mark a patient as bridged (automation + intelligence schema)."""
    table = _ddb.Table(config.TABLE_PATIENTS)
    table.update_item(
        Key={"patientId": patient_id, "SK": config.PROFILE_SK},
        UpdateExpression="SET bridgeId = :b, updatedAt = :u",
        ExpressionAttributeValues={
            ":b": bridge_id,
            ":u": pd.Timestamp.utcnow().isoformat(),
        },
    )


def persist_bridge_plan(plan, status_for_meta: str = "FORMING") -> int:
    """Write bridge META + VACANT slots from a BridgePlan dataclass."""
    put_bridge_meta(plan.bridge_id, plan.patient_id,
                    status=status_for_meta, bloodGroup=plan.blood_group,
                    coverage=plan.coverage)
    vacant = 0
    for slot in plan.slots:
        put_slot(plan.bridge_id, slot.slot_id, slot.slot_type, "VACANT",
                 donor_id=slot.donor_id, score=slot.score,
                 reason=slot.reason, candidate_queue=plan.candidate_queue,
                 patient_id=plan.patient_id, backup_for=slot.backup_for)
        vacant += 1
    link_patient_bridge(plan.patient_id, plan.bridge_id)
    return vacant


# --------------------------------------------------------------------------
# Bridges table (META + SLOT items)
# --------------------------------------------------------------------------
def put_bridge_meta(bridge_id: str, patient_id: str, **attrs) -> None:
    item = _to_item({
        "bridgeId": bridge_id, "SK": "META", "patientId": patient_id,
        "activeTarget": config.BRIDGE_ACTIVE_TARGET,
        "bufferTarget": config.BRIDGE_BUFFER_TARGET,
        **attrs,
    })
    _ddb.Table(config.TABLE_BRIDGES).put_item(Item=item)


# Keep stored fallback queues small: get_next_donor re-ranks live, so the queue
# is only a hint. A long queue on every slot would bloat the table and slow scans.
QUEUE_CAP = 25


def put_slot(bridge_id: str, slot_id: str, slot_type: str, status: str,
             donor_id: str | None = None, score: float | None = None,
             reason: str | None = None, candidate_queue: list | None = None,
             patient_id: str | None = None, backup_for: str | None = None) -> None:
    item = _to_item({
        "bridgeId": bridge_id, "SK": f"SLOT#{slot_id}",
        "patientId": patient_id, "slotType": slot_type, "status": status,
        "donorId": donor_id, "score": score, "reason": reason,
        "backupFor": backup_for,
        "candidateQueue": (candidate_queue or [])[:QUEUE_CAP],
        "updatedAt": pd.Timestamp.utcnow().isoformat(),
    })
    _ddb.Table(config.TABLE_BRIDGES).put_item(Item=item)


def get_bridge(bridge_id: str) -> list[dict]:
    resp = _ddb.Table(config.TABLE_BRIDGES).query(
        KeyConditionExpression=Key("bridgeId").eq(bridge_id)
    )
    return resp.get("Items", [])


def confirm_bridge_donor(bridge_id: str, donor_id: str, status: str = "PENDING") -> bool:
    """Mark a bridge slot as agreed after WhatsApp outreach completes."""
    for it in get_bridge(bridge_id):
        sk = str(it.get("SK", ""))
        if not sk.startswith("SLOT#") or it.get("donorId") != donor_id:
            continue
        slot_id = sk.split("#", 1)[-1]
        put_slot(
            bridge_id, slot_id, it.get("slotType", "active"), status,
            donor_id=donor_id,
            score=float(it["score"]) if it.get("score") is not None else None,
            reason=it.get("reason"),
            patient_id=it.get("patientId"),
            backup_for=it.get("backupFor"),
        )
        return True
    return False


def list_appointments(limit: int = 30) -> list[dict]:
    if not _table_exists(config.TABLE_APPOINTMENTS):
        return []
    rows = [r for r in _scan_table(config.TABLE_APPOINTMENTS) if r.get("SK") == "APPT"]
    rows.sort(key=lambda r: r.get("createdAt") or "", reverse=True)
    return rows[:limit]


def scan_bridges() -> list[dict]:
    table = _ddb.Table(config.TABLE_BRIDGES)
    items, kwargs = [], {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def start_outreach(state_machine_arn: str, payload: dict) -> str:
    """Kick the teammate's OutreachStateMachine to mobilize a donor for a slot."""
    import json
    resp = _sfn.start_execution(
        stateMachineArn=state_machine_arn,
        input=json.dumps(payload, default=str),
    )
    return resp["executionArn"]
