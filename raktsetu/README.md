# RaktSetu — Autonomous Donor & Patient Engagement System

A production-oriented, **multilingual WhatsApp + Voice bot** that automates blood
donor registration, patient blood requests, one-time-donor re-engagement, and
appointment management for **Blood Warriors** (a Thalassemia NGO in India).

This is not a scripted chatbot. A **strict Amazon Bedrock (Claude) agent** named
**Veeru** drives every conversation and calls a **guardrailed tool layer**
(`shared/agent_tools.py`) where *all* validation, eligibility, and cooldown logic
is enforced in Python — the model can never hallucinate the rules. Conversations
have **memory**, **multilingual support (English / Hindi / Telugu)**, and **real
backend writes to DynamoDB**. A deterministic FSM is kept as an automatic
fallback if Bedrock is ever unavailable.

**Channels:**
- **WhatsApp** on your own number via **Periskope** (a proxy — no Meta template
  approval needed for free-form chat).
- **Voice** via **Vapi** — an AI voice assistant that uses the *same* tool layer
  over a server-URL webhook, so chat and calls share identical guardrails.

> Bot persona: **Veeru** — warm, empathetic, brief, never pushy.

---

## What it does (the 7 flows)

| Flow | Description |
|------|-------------|
| **1. New donor registration** | Name → age (18–65) → weight (≥45kg) → blood group → area (geocoded) → last donation (90-day cooldown). Under-age/under-weight → waitlist. "Don't know" blood group → partial save + 48h follow-up. |
| **2. Incomplete registration follow-up** | 48h after a partial save, a nudge; resume mid-flow on `YES`. Max 2 attempts. |
| **3. Patient / guardian request** | Patient details → blood group → units (1–6) → hospital (geocoded) → required-by (NLU date parse) → request raised → outreach started. |
| **4. One-time donor emergency outreach** | Approved WhatsApp template (`donation_request_v1`). `YES`→FLOW 5, `NO`→next donor, `LATER`→snooze. No reply in 4h → voice call. |
| **5. Eligibility quick-check + booking** | 5 rapid health questions → deferral rules → appointment booking with calendar link → patient notified → reminders scheduled. |
| **6. Voice call escalation** | Outbound IVR (`Polly.Aditi`/`Polly.Raveena`, `en/hi/te-IN`). Press 1 = donate (→FLOW 5 over WhatsApp), Press 2 = decline. Inbound calls reuse the same FSM brain. |
| **7. Appointment reminders** | Day-before WhatsApp + confirm/cancel/call; 2h no-reply → call. 3-hour-before WhatsApp with maps link. Cancellation → instant replacement search. |

Plus **DPDP compliance**: reply `DELETE MY DATA` to erase your profile.

---

## Architecture

```
 WhatsApp (your number)            Voice
   via Periskope                  via Vapi
        │                            │
        ▼                            ▼
  /periskope webhook          /vapi/tools webhook
        │                            │
        ▼                            │
  router.respond() ──► shared/agent.py (Bedrock Converse, strict prompt)
        │  (FSM fallback)            │
        └──────────────┬─────────────┘
                       ▼
        shared/agent_tools.py  (ONE guardrailed tool layer,
        phone-bound; validation + eligibility enforced in Python)
                       │
                       ▼
   DynamoDB  (conversations, donors, patients, bloodRequests, appointments, …)
        │
        ▼
  Step Functions  "raktsetu-outreach"
   GetRankedDonors → SendOutreach → Wait → CheckReply
     → (confirmed) Book → NotifyPatient → ScheduleReminders
     → (no_response) EscalateToCall → … → TryNextDonor
        │
        ▼
  EventBridge Scheduler  (48h follow-ups, day-before / 3h reminders, cooldown nudges)
```

Matching engine = **eligibility filter** (ABO/Rh + cooldown + deferral) →
**explainable scoring** (compatibility, proximity, reliability, recency,
willingness) → **NetworkX donor graph** (k-hop reachable pool + coverage score,
"Blood Bridge 2.0"). Production scale path: Amazon Neptune + Neptune ML GraphSAGE.

---

## Repo layout

```
raktsetu/
  lambdas/
    whatsapp_webhook/   handler.py · router.py (agent→FSM) · flows.py (FSM) · validators.py
    periskope_webhook/  handler.py   (inbound WhatsApp via Periskope proxy)
    vapi_tools/         handler.py   (Vapi voice tool-calls webhook)
    voice_inbound/      handler.py
    voice_outbound/     handler.py
    voice_keypress/     handler.py
    matching_engine/    handler.py · eligibility.py · scoring.py · graph.py
    send_whatsapp/ check_outreach/ trigger_voice/ check_call/ get_next_donor/
    book_appointment/ notify_patient/ schedule_reminders/
    appointment_reminder/ three_hour_reminder/ follow_up_registration/
    cooldown_nudge/ delete_donor/
  shared/
    config.py · dynamodb_client.py · twilio_client.py · bedrock_client.py
    agent.py (Bedrock Converse loop) · agent_tools.py (guardrailed tools)
    periskope_client.py · vapi_client.py
    i18n.py · eligibility_rules.py · geocoding.py · scheduler.py · twiml.py
  infra/template.yaml            AWS SAM (DynamoDB + Lambda + Step Functions + EventBridge)
  step_functions/outreach_machine.json
  tests/                          test_eligibility · test_scoring · test_flows
  scripts/                        seed_demo_data.py · test_whatsapp_flow.py
  events/whatsapp.json            sample SAM local event
  .env.example
```

---

## Quick start (local, no AWS / no Twilio)

Everything runs against an in-memory file-backed store when `LOCAL_MODE=1`.

```bash
cd raktsetu
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Seed demo data (20 donors + 5 patients + 10 requests)
LOCAL_MODE=1 python scripts/seed_demo_data.py --reset

# Walk a conversation end-to-end
LOCAL_MODE=1 python scripts/test_whatsapp_flow.py --phone +919876543210 --message "Hi"
LOCAL_MODE=1 python scripts/test_whatsapp_flow.py --phone +919876543210 --message "1"
LOCAL_MODE=1 python scripts/test_whatsapp_flow.py --phone +919876543210 --message "Rahul Kumar"
# ... or replay a whole script in one go:
LOCAL_MODE=1 python scripts/test_whatsapp_flow.py --phone +919876543210 --reset \
  --script "Hi|1|Rahul Kumar|30|70|O+|Madhapur|No, First Time"

# Run the test suite
LOCAL_MODE=1 python -m pytest tests/ -q
```

Conversation state persists in `local_state.json` between calls, so you can step
through any flow one message at a time exactly like a real chat.

---

## Realistic live demo — your WhatsApp number (Periskope) + AI calls (Vapi)

This runs the **real Bedrock agent** locally (no full AWS deploy) and serves
WhatsApp + voice webhooks through `ngrok`. Local DynamoDB is fine for the demo;
set `LOCAL_MODE=1` with `AGENT_FORCE_BEDROCK=1` (already in `.env`).

**1. Start the local webhook server + ngrok**

```bash
cd raktsetu
source .venv/bin/activate
python scripts/local_server.py            # serves /whatsapp /periskope /vapi/tools
# in another terminal:
ngrok http 8000                           # copy the https URL, e.g. https://abc.ngrok.app
```

**2. WhatsApp via Periskope (your number +919076150904)**

- In the Periskope dashboard, set the **webhook URL** to
  `https://abc.ngrok.app/periskope` and enable the `message.created` event.
- `.env` already has `WA_PROVIDER=periskope` + `PERISKOPE_API_KEY` +
  `PERISKOPE_PHONE`. Message your connected WhatsApp number — Veeru replies in the
  sender's language, free-form, with full memory.

**3. Voice via Vapi**

```bash
# Create the assistant from the SAME prompt + tools, pointed at your ngrok URL:
export VAPI_API_KEY=...                    # from the Vapi dashboard
python scripts/setup_vapi.py --server-url https://abc.ngrok.app
# → prints VAPI_ASSISTANT_ID; paste it into .env
```

- In the Vapi dashboard, **create/import a phone number** and set its inbound
  assistant to the one just created; put its id in `.env` as `VAPI_PHONE_NUMBER_ID`.
- Inbound: call the Vapi number → Veeru answers and uses `/vapi/tools`.
- Outbound escalation (FLOW 6) is placed automatically via Vapi when a donor
  doesn't reply (`VOICE_PROVIDER=vapi`).

> The agent's `/vapi/tools` calls and `/periskope` replies all flow through the
> identical `agent_tools.py`, so WhatsApp and phone behave the same and obey the
> same eligibility/validation rules.

---

## Deploy to AWS

RaktSetu goes live on **WhatsApp** (+91 number via Periskope) and **voice** (Vapi phone
number). There is no public website — users message or call those numbers.

### Prerequisites

1. **AWS credentials** with DynamoDB, Secrets Manager, Bedrock, and (for full deploy) CloudFormation SAM transform access.
2. **Bedrock model access** enabled for Claude Haiku in your region (`us-east-1` in `.env`).
3. **Secrets** — run once: `python scripts/setup_secrets.py` (uploads `raktsetu/.env` → Secrets Manager).
4. **DynamoDB tables** — run once: `python scripts/create_dynamodb_tables.py`.

### Option A — Full stack (Lambda + Step Functions) — recommended

Requires permission to use the CloudFormation transform `Serverless-2016-10-09`
(hackathon accounts sometimes block this).

```bash
pip install aws-sam-cli
./scripts/deploy_aws.sh
```

This deploys all Lambdas, Step Functions outreach, EventBridge reminders, and API Gateway.
Wire webhooks using the stack outputs (`WhatsappWebhookUrl`, `PeriskopeWebhookUrl`, `VapiToolsUrl`).

### Option B — App Runner (webhooks + Bedrock + DynamoDB) — workaround

Use when SAM deploy is blocked. Runs `scripts/local_server.py` in a container with
`LOCAL_MODE=0`. **Donor outreach Step Functions are not included** — registration,
WhatsApp chat, and Vapi voice work; patient-request outreach needs Option A or manual triggers.

```bash
# Start Docker Desktop first, then:
./scripts/deploy_apprunner.sh
```

The script prints live URLs. It also runs `setup_vapi.py` to point Tara at the new server.

### After deploy — make it reachable

| Channel | What to configure |
|---------|-------------------|
| **WhatsApp** | Periskope dashboard → Webhooks → `message.created` → `https://…/periskope` |
| **Voice** | Automatic via `setup_vapi.py` → `https://…/vapi/tools` |
| **Twilio WA** (optional) | Twilio sender webhook → `https://…/whatsapp` |

Test: message **+918433775356** on WhatsApp, or call your **Vapi** number.

### Original SAM steps (manual)

1. **Enable Bedrock model access** for Claude in your region.
2. **Store secrets** in Secrets Manager (`python scripts/setup_secrets.py`).
3. **Build & deploy**:
   ```bash
   sam build -t infra/template.yaml
   sam deploy --config-file samconfig.toml
   ```
4. **Wire Twilio** to the stack outputs (if using Twilio instead of Periskope).

Test the deployed webhook locally with SAM:

```bash
sam local invoke WhatsappWebhookFunction -e events/whatsapp.json
```

---

## Languages

`shared/i18n.py` holds every bot message in **en / hi / te**. Language is:
1. read from the conversation's stored `preferredLanguage`, else
2. detected from the script of the first message (Devanagari→hi, Telugu→te), and
3. switchable any time ("hindi mein", "switch to english", "తెలుగులో", …).

Voice uses `<Say language="hi-IN" voice="Polly.Aditi">` etc.

---

## Key implementation notes

- **Idempotency** — every inbound message is deduped on Twilio `MessageSid`
  (`processedMessages` table, 7-day TTL).
- **Low latency** — YES/NO/quick-replies are handled in the webhook with zero
  Bedrock inference; only free-text needing NLU hits Claude.
- **24-hour window** — proactive outreach uses approved templates
  (`send_whatsapp_template`); replies open the free-form window.
- **Error handling** — the webhook never returns 5xx to Twilio (that would
  trigger retries / duplicate messages); errors are logged to CloudWatch and an
  empty `<Response/>` is returned.
- **Consent** — `consentGiven=true` is recorded at registration before any
  proactive contact.
- **DPDP** — `DELETE MY DATA` triggers `delete_donor` to erase profile +
  conversation.

---

## Environment variables

See `.env.example`. In production all values resolve from Secrets Manager
(`SECRETS_MANAGER_SECRET_ID`), with environment variables taking precedence for
table names / ARNs injected by SAM.
