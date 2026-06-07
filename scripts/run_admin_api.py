"""Run the Admin API locally with uvicorn.

Usage:  .venv/bin/python -m scripts.run_admin_api
Then open http://127.0.0.1:8000/docs  (Admin UI proxies /api -> :8000)
"""
from __future__ import annotations

from scripts.bootstrap_env import bootstrap

bootstrap()

import uvicorn

if __name__ == "__main__":
    uvicorn.run("lambdas.admin_api.handler:app", host="127.0.0.1", port=8000, reload=False)
