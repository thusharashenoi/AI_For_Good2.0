"""Run the Admin API locally with uvicorn.

Usage:  STAGE=dev .venv/bin/python -m scripts.run_admin_api
Then open http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run("lambdas.admin_api.handler:app", host="127.0.0.1", port=8000, reload=False)
