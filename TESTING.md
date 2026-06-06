# Testing branch — unified demo flow

Combines **Bridge Intelligence** (`development/bridge-intelligence`) with **WhatsApp/voice automation** (`automation`).

## Architecture

```
WhatsApp / Voice bot  →  DynamoDB (donors, patients, requests, appointments)
                              ↓
                    engagement_adapter (schema normalisation)
                              ↓
              Blood Graph ML + Admin UI (matching, bridges, emergency)
```

| Path | Purpose |
|------|---------|
| `raktsetu/` | ML matching, Blood Graph, bridge planner, DynamoDB store |
| `engagement/` | WhatsApp/voice Lambdas, Step Functions outreach, agent tools |
| `lambdas/` | Admin API + intelligence Lambdas (deployed via `infra/`) |
| `admin/` | Operations dashboard |

## Demo scenario

1. **Donor registers** via WhatsApp bot → `complete_donor_registration` writes to `raktsetu-donors-{stage}` with `lat`/`lng`, `eligibilityStatus`, and dual-written ML fields (`willingnessScore`, `showRate`).
2. **Patient raises request** via WhatsApp → patient + `raktsetu-bloodRequests-{stage}` item with hospital coordinates.
3. **Admin UI** (refresh) shows the open request and updated donor pool.
4. **Form bridge** on an unbridged patient → `POST /bridges/form/{patientId}` ranks donors including the new WhatsApp registrant (compatibility + live 90-day eligibility + proximity).
5. **Emergency tab** → enter patient name/blood group/city → same donor pool, including bot-registered donors in range.

## Setup (local / dev)

```bash
export STAGE=dev
cp .env.example .env   # AWS creds

# Tables (donors, patients, bridges + automation tables)
.venv/bin/python -m scripts.create_tables

# Optional: seed CSV baseline for demo comparison
.venv/bin/python -m scripts.seed_dynamodb

# Intelligence API + admin UI
.venv/bin/python -m scripts.run_admin_api   # :8000
cd admin && npm run dev                       # :5173

# Automation stack (separate deploy — see engagement/scripts/deploy_aws.sh)
# Set DYNAMODB_TABLE_* and STAGE=dev so both stacks share the same tables.
```

## Key integration points

- **`raktsetu/engagement_adapter.py`** — maps automation DynamoDB fields → pipeline DataFrames; uses **live eligibility** (`LIVE_ELIGIBILITY=1`) for bot-registered donors.
- **`engagement/shared/intelligence_bridge.py`** — enriches donor/patient items on write with ML-readable fields.
- **`POST /bridges/form/{patient_id}`** — admin “Form bridge” action.
- **`GET /requests`** — open blood requests from the portal.

## Environment

Both stacks should use the same **`STAGE`** (e.g. `dev`) and table names:

| Variable | Example |
|----------|---------|
| `STAGE` | `dev` |
| `DYNAMODB_TABLE_DONORS` | `raktsetu-donors-dev` |
| `DYNAMODB_TABLE_PATIENTS` | `raktsetu-patients-dev` |
| `DYNAMODB_TABLE_REQUESTS` | `raktsetu-bloodRequests-dev` |
| `LIVE_ELIGIBILITY` | `1` (real-time 90-day cooldown for demo) |
