import os
import sys
import tempfile

# Run everything against the in-memory local store, no AWS/Twilio.
os.environ["LOCAL_MODE"] = "1"
os.environ.setdefault("LOCAL_STATE_PATH", os.path.join(tempfile.gettempdir(), "raktsetu_test_state.json"))

# Make `shared` and `lambdas` importable.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest  # noqa: E402

# Single demo phone for all tests — matches DEMO_OUTREACH_PHONE in .env.
DEMO_PHONE = "+919372875356"


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset the file-backed store + Twilio outbox before each test."""
    path = os.environ["LOCAL_STATE_PATH"]
    if os.path.exists(path):
        os.remove(path)
    from shared import dynamodb_client as db
    db._local_store = None  # force reload of empty store
    from shared import twilio_client, scheduler
    twilio_client.clear_outbox()
    scheduler.SCHEDULED.clear()
    scheduler.STARTED_EXECUTIONS.clear()
    yield
