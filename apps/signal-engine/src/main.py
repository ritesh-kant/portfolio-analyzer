"""FastAPI application entry point for the signal engine."""

import asyncio
import logging
import os
from datetime import date as _date

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

# Pull secrets from SSM before Pydantic-settings reads env vars (Lambda only).
from .secrets import bootstrap_secrets

bootstrap_secrets(stage=os.getenv("STAGE", "dev"))

from .config import Settings
from .db.client import close_client, get_db
from .db.repositories.pipeline_runs import PipelineRunsRepository
from .pipeline import run_pipeline

logger = logging.getLogger(__name__)
settings = Settings()

app = FastAPI(
    title="Signal Engine",
    description="Autonomous AI trading signal pipeline for Indian markets (NSE/BSE)",
    version="0.1.0",
)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await close_client()


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "signal-engine",
        "version": "0.1.0",
        "ai_provider": settings.ai_provider,
    }


class PipelineRunRequest(BaseModel):
    run_id: str
    date: str
    ai_provider: str = ""


@app.post("/pipeline/run", status_code=202)
async def trigger_pipeline(
    body: PipelineRunRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    if not body.run_id or not body.date:
        raise HTTPException(status_code=400, detail="run_id and date are required")

    ai_provider = body.ai_provider or settings.ai_provider

    repo = PipelineRunsRepository(get_db())
    await repo.create(body.run_id, body.date, ai_provider)

    background_tasks.add_task(_run_pipeline_task, body.run_id, body.date, ai_provider)

    return {"run_id": body.run_id, "date": body.date, "ai_provider": ai_provider, "status": "accepted"}


async def _run_pipeline_task(run_id: str, date: str, ai_provider: str) -> None:
    try:
        await run_pipeline(run_id, date, ai_provider)
    except Exception as exc:
        logger.exception("pipeline run %s failed: %s", run_id, exc)
        repo = PipelineRunsRepository(get_db())
        await repo.finalize(run_id, "failed", error_summary=str(exc))
