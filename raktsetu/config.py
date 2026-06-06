"""Central paths and domain constants for the RaktSetu intelligence layer."""
from __future__ import annotations

import os
from pathlib import Path

# ---- Paths ----------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
RAW_CSV = ROOT / "resources" / "Dataset.csv"
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUTS_DIR = ROOT / "data" / "outputs"
MODELS_DIR = ROOT / "data" / "outputs" / "models"

# Local pipeline writes processed CSVs/models; skip on Lambda (read-only /var/task).
if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    for _d in (PROCESSED_DIR, OUTPUTS_DIR, MODELS_DIR):
        _d.mkdir(parents=True, exist_ok=True)

# ---- Domain constants -----------------------------------------------------
# Whole-blood donation cooldown (India / standard guideline).
WHOLE_BLOOD_COOLDOWN_DAYS = 90

# Reference "today" for the dataset. The data was exported around mid-2025 and
# the bulk of expected_next_transfusion_date values sit in Aug 2025, so we
# anchor relative-time features to a fixed date for reproducibility instead of
# datetime.now() (which would make every donor look wildly overdue).
REFERENCE_DATE = "2025-08-15"

# Roles in the raw data.
DONOR_ROLES = ("Emergency Donor", "Bridge Donor", "Volunteer")
PATIENT_ROLE = "Patient"

# Buffer policy: how many transfusion cycles of donors to pre-arrange per patient.
BUFFER_CYCLES = 1

# Coverage target: minimum number of distinct eligible+compatible donors a
# patient's reachable pool should contain to be considered "safe".
COVERAGE_TARGET_DONORS = 4

# ---- Blood Bridge composition (Blood Warriors model) ----------------------
# A bridge is 10 committed donors: 6 active (rotation) + 4 buffer (standby).
BRIDGE_ACTIVE_TARGET = 6
BRIDGE_BUFFER_TARGET = 4
BRIDGE_SIZE = BRIDGE_ACTIVE_TARGET + BRIDGE_BUFFER_TARGET

# ---- AWS / DynamoDB --------------------------------------------------------
# Region + table names are env-driven so the same code runs against a -dev
# stage locally and the teammate's -prod stage in Lambda. Names mirror the
# teammate's SAM Globals (DYNAMODB_TABLE_*), plus a new Bridges table we own.
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION_NAME") or "us-east-1"
STAGE = os.environ.get("STAGE", "dev")


def _table(env_key: str, suffix: str) -> str:
    return os.environ.get(env_key) or f"raktsetu-{suffix}-{STAGE}"


TABLE_DONORS = _table("DYNAMODB_TABLE_DONORS", "donors")
TABLE_PATIENTS = _table("DYNAMODB_TABLE_PATIENTS", "patients")
TABLE_BRIDGES = _table("DYNAMODB_TABLE_BRIDGES", "bridges")
TABLE_REQUESTS = _table("DYNAMODB_TABLE_REQUESTS", "bloodRequests")
TABLE_APPOINTMENTS = _table("DYNAMODB_TABLE_APPOINTMENTS", "appointments")

# Convention: one profile item per entity (matches their donorId/patientId + SK).
PROFILE_SK = "PROFILE"
