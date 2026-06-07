"""EventBridge Scheduler + Step Functions helpers.

Creates one-time schedules for follow-ups / reminders and starts the outreach
state machine. LOCAL_MODE records intents in-memory so flows are testable.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from . import config

logger = logging.getLogger("raktsetu.scheduler")

# In-memory log for LOCAL_MODE / tests.
SCHEDULED: List[Dict] = []
STARTED_EXECUTIONS: List[Dict] = []

_scheduler_client = None
_sfn_client = None


def _scheduler():
    global _scheduler_client
    if _scheduler_client is None:
        import boto3

        _scheduler_client = boto3.client("scheduler", region_name=config.region())
    return _scheduler_client


def _sfn():
    global _sfn_client
    if _sfn_client is None:
        import boto3

        _sfn_client = boto3.client("stepfunctions", region_name=config.region())
    return _sfn_client


def create_schedule(name: str, at: datetime, target_arn: str, payload: Dict,
                    role_arn: Optional[str] = None) -> Dict:
    """Production EventBridge one-time schedule."""
    at = at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
    record = {"name": name, "at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "target": target_arn, "payload": payload}
    if config.LOCAL_MODE:
        SCHEDULED.append(record)
        logger.info("[LOCAL SCHEDULE] %s at %s -> %s", name, record["at"], target_arn)
        return record
    role_arn = role_arn or config.get("SCHEDULER_ROLE_ARN")
    group = config.get("SCHEDULER_GROUP_NAME", "raktsetu")
    try:
        _scheduler().create_schedule(
            Name=name,
            GroupName=group,
            FlexibleTimeWindow={"Mode": "OFF"},
            ScheduleExpression=f"at({at.strftime('%Y-%m-%dT%H:%M:%S')})",
            Target={
                "Arn": target_arn,
                "RoleArn": role_arn,
                "Input": json.dumps(payload),
            },
            ActionAfterCompletion="DELETE",
        )
    except Exception as exc:
        logger.exception("create_schedule failed for %s: %s", name, exc)
    return record


def start_outreach(request_id: str, extra: Optional[Dict] = None) -> Dict:
    """Kick off the donor outreach Step Functions state machine for a request."""
    payload = {"requestId": request_id, **(extra or {})}
    record = {"requestId": request_id, "payload": payload}
    if config.LOCAL_MODE:
        STARTED_EXECUTIONS.append(record)
        logger.info("[LOCAL SFN START] outreach for request %s", request_id)
        # Run inline so local/App Runner tests actually contact donors.
        try:
            from .outreach import run_outreach
            record["inline"] = run_outreach(request_id, extra)
        except Exception as exc:
            logger.exception("inline outreach failed: %s", exc)
        return record
    arn = config.get("OUTREACH_STATE_MACHINE_ARN")
    if not arn or arn == "local":
        logger.warning("No Step Functions ARN — running inline outreach for %s", request_id)
        try:
            from .outreach import run_outreach
            record["inline"] = run_outreach(request_id, extra)
        except Exception as exc:
            logger.exception("inline outreach failed: %s", exc)
        return record
    try:
        _sfn().start_execution(stateMachineArn=arn, input=json.dumps(payload))
    except Exception as exc:
        logger.exception("start_outreach SFN failed, falling back inline: %s", exc)
        try:
            from .outreach import run_outreach
            record["inline"] = run_outreach(request_id, extra)
        except Exception as exc2:
            logger.exception("inline outreach fallback failed: %s", exc2)
    return record


def in_hours(hours: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def in_days(days: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


def in_seconds(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def schedule_outreach_voice_escalation(request_id: str, donor_id: str,
                                       token: str, delay_seconds: Optional[float] = None) -> Dict:
    """After WhatsApp outreach, place a voice call if the donor has not replied."""
    delay = float(
        delay_seconds
        if delay_seconds is not None
        else config.get("OUTREACH_VOICE_ESCALATION_SECONDS") or 15
    )
    payload = {
        "requestId": request_id,
        "donorId": donor_id,
        "token": token,
        "action": "outreach_voice_escalation",
        "onlyIfNoReply": True,
    }
    name = f"outreach-voice-{request_id[:8]}-{donor_id[:8]}-{token[:6]}"
    record = {"name": name, "delaySeconds": delay, "payload": payload}

    if config.LOCAL_MODE:
        if os.environ.get("RAKTSETU_SERVER_PROCESS") == "1":
            from . import voice_escalation_scheduler
            voice_escalation_scheduler.schedule_escalation(
                request_id, donor_id, token, delay)
            record["scheduledOnServer"] = True
            SCHEDULED.append(record)
            logger.info(
                "[SERVER TIMER] outreach voice escalation in %.1fs request=%s donor=%s",
                delay, request_id, donor_id,
            )
            return record

        base = (
            config.get("LOCAL_SERVER_URL")
            or f"http://127.0.0.1:{config.get('PORT', '4000')}"
        ).rstrip("/")
        try:
            import requests
            resp = requests.post(
                f"{base}/internal/outreach/schedule-voice",
                json={
                    "requestId": request_id,
                    "donorId": donor_id,
                    "token": token,
                    "delaySeconds": delay,
                },
                timeout=5,
            )
            if resp.status_code < 300:
                body = resp.json() if resp.content else {}
                record["scheduledOnServer"] = True
                record.update(body)
                logger.info(
                    "[SERVER TIMER] outreach voice escalation in %.1fs request=%s donor=%s",
                    delay, request_id, donor_id,
                )
                return record
            logger.warning(
                "server schedule-voice returned %s: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("server schedule-voice failed (%s) — using in-process timer", exc)

        from . import voice_escalation_scheduler
        voice_escalation_scheduler.schedule_escalation(
            request_id, donor_id, token, delay)
        record["localTimer"] = True
        SCHEDULED.append(record)
        return record

    target = (
        config.get("TRIGGER_VOICE_LAMBDA_ARN")
        or config.get("TRIGGER_VOICE_ARN")
        or "local"
    )
    if target == "local":
        logger.warning("No TRIGGER_VOICE lambda ARN — escalation not scheduled for %s", request_id)
        return record
    return create_schedule(name, in_seconds(delay), target, payload)
