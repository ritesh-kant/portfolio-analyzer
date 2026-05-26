"""FastAPI application entry point for the signal engine.

The previous LangGraph pipeline and its FastAPI surface (/pipeline/run,
/pipeline/monitor) were removed during the demolition phase of the
signal-engine rebuild (see ~/.claude/plans/based-on-the-full-harmonic-gosling.md).

This stub keeps only the /health endpoint so existing deploys do not 502.
New endpoints will be added in Month 3 once quant/ pipeline lands.
"""

import logging
import os

from fastapi import FastAPI

# Ensure INFO-level logs from application code are visible even under uvicorn,
# which does not set the root logger level (leaves it at WARNING by default).
logging.getLogger().setLevel(logging.INFO)

from .secrets import bootstrap_secrets

bootstrap_secrets(stage=os.getenv("STAGE", "dev"))

from .config import Settings
from .db.client import close_client

logger = logging.getLogger(__name__)
settings = Settings()

app = FastAPI(
    title="Signal Engine (rebuild in progress)",
    description="Quant signal platform — under reconstruction.",
    version="0.2.0-rebuild",
)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await close_client()


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "signal-engine",
        "version": "0.2.0-rebuild",
        "phase": "demolition-complete-month1",
    }


@app.post("/trigger/ingester")
async def trigger_ingester() -> dict:
    """Manually invoke the news ingester — local dev only."""
    from handlers.news_ingester import _run
    result = await _run(settings)
    return result
