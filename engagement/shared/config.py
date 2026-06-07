"""Central configuration loader for RaktSetu.

Order of precedence:
1. AWS Secrets Manager (secret id from SECRETS_MANAGER_SECRET_ID) — production
2. Process environment variables (.env loaded by the runtime / test harness)

The Secrets Manager fetch is cached for the lifetime of the warm Lambda
container, so we pay the lookup once per cold start, not once per invocation.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any, Dict

logger = logging.getLogger("raktsetu.config")


def _load_dotenv_file(path: str) -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                os.environ.setdefault(key, val)
    except Exception as exc:  # never let env loading crash anything
        logger.warning("Could not load .env from %s: %s", path, exc)


def _load_dotenv() -> None:
    """Lightweight .env loader (no python-dotenv dependency).

    Loads repo-root ``.env`` then ``engagement/.env``. Existing process env
    vars win, so SAM / Lambda injected values are never overwritten.
    """
    engagement_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_root = os.path.dirname(engagement_root)
    for path in (
        os.path.join(repo_root, ".env"),
        os.path.join(engagement_root, ".env"),
    ):
        _load_dotenv_file(path)


_load_dotenv()

LOCAL_MODE = os.environ.get("LOCAL_MODE", "0") == "1"


@lru_cache(maxsize=1)
def _secrets() -> Dict[str, Any]:
    """Fetch the merged secret blob from Secrets Manager (cached)."""
    if LOCAL_MODE:
        return {}
    secret_id = os.environ.get("SECRETS_MANAGER_SECRET_ID")
    if not secret_id:
        return {}
    try:
        import boto3  # imported lazily so LOCAL_MODE needs no AWS sdk creds

        client = boto3.client("secretsmanager", region_name=region())
        resp = client.get_secret_value(SecretId=secret_id)
        raw = resp.get("SecretString") or "{}"
        return json.loads(raw)
    except Exception as exc:  # never let config crash a webhook
        logger.warning("Could not load Secrets Manager secret %s: %s", secret_id, exc)
        return {}


def get(key: str, default: str | None = None) -> str | None:
    """Resolve a config value: env var wins, then Secrets Manager, then default."""
    if key in os.environ and os.environ[key] != "":
        return os.environ[key]
    val = _secrets().get(key)
    if val is not None:
        return str(val)
    return default


def require(key: str) -> str:
    val = get(key)
    if val is None:
        raise RuntimeError(f"Required config '{key}' is not set")
    return val


def region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"


# --- Convenience accessors used across lambdas -------------------------------

def table_names() -> Dict[str, str]:
    return {
        "conversations": get("DYNAMODB_TABLE_CONVERSATIONS", "raktsetu-conversations"),
        "donors": get("DYNAMODB_TABLE_DONORS", "raktsetu-donors"),
        "patients": get("DYNAMODB_TABLE_PATIENTS", "raktsetu-patients"),
        "requests": get("DYNAMODB_TABLE_REQUESTS", "raktsetu-bloodRequests"),
        "appointments": get("DYNAMODB_TABLE_APPOINTMENTS", "raktsetu-appointments"),
        "waitlist": get("DYNAMODB_TABLE_WAITLIST", "raktsetu-waitlist"),
        "processed_messages": get("DYNAMODB_TABLE_PROCESSED_MESSAGES", "raktsetu-processedMessages"),
    }
