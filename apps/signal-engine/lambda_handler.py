"""AWS Lambda entry point.

Two invocation paths:
  1. HTTP (API Gateway / Function URL) — routed through Mangum → FastAPI.
  2. Direct async invocation (from pipelineConsumer via InvocationType='Event') —
     detected by the presence of a "run_id" key and executed without HTTP overhead.
     pipelineConsumer uses InvocationType='Event' so it gets 202 immediately and
     this Lambda runs the full pipeline independently.
"""

import asyncio
import logging

from mangum import Mangum

from src.main import app

logger = logging.getLogger(__name__)

_http_handler = Mangum(app, lifespan="off")


async def _run_direct(run_id: str, date: str, ai_provider: str) -> None:
    from datetime import datetime, timezone

    from src.db.client import get_db
    from src.db.repositories.pipeline_runs import PipelineRunsRepository
    from src.pipeline.graph import run_pipeline

    repo = PipelineRunsRepository(get_db())
    await repo.update_one(
        {"run_id": run_id},
        {"$set": {"status": "running", "updatedAt": datetime.now(timezone.utc)}},
    )
    try:
        await run_pipeline(run_id, date, ai_provider)
    except Exception as exc:
        logger.exception("direct invoke pipeline run_id=%s failed: %s", run_id, exc)
        await repo.finalize(run_id, "failed", error_summary=str(exc))
        raise


def handler(event: dict, context: object) -> object:
    if "run_id" in event:
        asyncio.run(_run_direct(
            run_id=event["run_id"],
            date=event["date"],
            ai_provider=event.get("ai_provider", "anthropic"),
        ))
        return {"status": "ok"}
    return _http_handler(event, context)
