"""FastAPI application entry point for the signal engine.

The previous LangGraph pipeline and its FastAPI surface (/pipeline/run,
/pipeline/monitor) were removed during the demolition phase of the
signal-engine rebuild (see ~/.claude/plans/based-on-the-full-harmonic-gosling.md).

This stub keeps only the /health endpoint so existing deploys do not 502.
New endpoints will be added in Month 3 once quant/ pipeline lands.
"""

import logging
import os
from typing import Any

from fastapi import FastAPI, Query

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
async def trigger_ingester() -> dict[str, Any]:
    """Manually invoke the news ingester — local dev only."""
    from handlers.news_ingester import _run
    result = await _run(settings)
    return result


@app.post("/trigger/pipeline")
async def trigger_pipeline(run_id: str | None = Query(None)) -> dict[str, Any]:
    """Run the full news-trader pipeline locally, bypassing SQS.

    Chains: ingester → classifier (per new article) → trade decision (per signal).
    The 15-min SQS delay is skipped so the full cycle completes in one call.

    run_id: if provided, updates nt_pipeline_runs on completion or failure.
    """
    import asyncio
    from datetime import datetime, timezone

    from handlers.news_ingester import _run as _ingest
    from handlers.news_classifier import _process_message, _LLM_MAX_CONCURRENCY
    from handlers.trade_decision import _process_signal
    from src.db.client import get_db
    from src.news_trader.db import ensure_indexes, signals as signals_coll
    from src.news_trader.pipeline_lifecycle import delete_run, fail_run, finalise_run

    logger.info("[PIPELINE] starting local full-chain run run_id=%s", run_id)
    run_start = datetime.now(tz=timezone.utc)

    try:
        # 1. Ingest
        ingest_result = await _ingest(settings)
        if ingest_result.get("skipped"):
            if run_id:
                await delete_run(run_id)
            return {"skipped": ingest_result["skipped"]}

        n_new = ingest_result.get("new_articles", 0)
        logger.info("[PIPELINE] ingester done — %d new articles", n_new)

        if n_new == 0:
            result: dict[str, Any] = {"new_articles": 0, "signals": 0, "positions_opened": 0}
            if run_id:
                await finalise_run(run_id, result)
            return result

        # 2. Classify each new article (direct call, no SQS)
        db = get_db()
        await ensure_indexes(db)
        cursor = db["nt_news_raw"].find(
            {"ingested_at": {"$gte": run_start}, "classified": False},
            {"_id": 1},
        )
        new_ids = [str(doc["_id"]) async for doc in cursor]
        logger.info("[PIPELINE] classifying %d articles...", len(new_ids))

        actionable_signals = 0
        llm_semaphore = asyncio.Semaphore(_LLM_MAX_CONCURRENCY)
        for news_id in new_ids:
            try:
                if await _process_message({"news_id": news_id}, settings, llm_semaphore):
                    actionable_signals += 1
            except Exception as exc:
                logger.error("[PIPELINE] classifier error news_id=%s err=%s", news_id, exc)
            await asyncio.sleep(0.05)  # small gap between articles

        # Count all signals created (matches what nt-pipeline-status live view counts)
        signals_created = await signals_coll(db).count_documents({"created_at": {"$gte": run_start}})
        logger.info("[PIPELINE] classification done — %d total signals (%d actionable)",
                    signals_created, actionable_signals)

        if actionable_signals == 0:
            result = {"new_articles": n_new, "signals": signals_created, "positions_opened": 0}
            if run_id:
                await finalise_run(run_id, result)
            return result

        # 3. Trade decision for each new signal (skip the 15-min delay locally)
        sig_cursor = signals_coll(db).find(
            {"created_at": {"$gte": run_start}, "acted_on": False},
            sort=[("created_at", 1)],
        )
        total_entered = 0
        async for sig in sig_cursor:
            try:
                n, gate_result = await _process_signal(sig, settings, pipeline_run_id=run_id)
                total_entered += n
                await signals_coll(db).update_one(
                    {"_id": sig["_id"]}, {"$set": {"acted_on": True, "gate_result": gate_result}}
                )
            except Exception as exc:
                logger.error("[PIPELINE] trade error signal_id=%s err=%s", sig["_id"], exc)

        logger.info("[PIPELINE] done — articles=%d signals=%d positions=%d",
                    n_new, signals_created, total_entered)
        result = {"new_articles": n_new, "signals": signals_created, "positions_opened": total_entered}
        if run_id:
            await finalise_run(run_id, result)
        return result

    except Exception as exc:
        logger.error("[PIPELINE] unhandled error run_id=%s err=%s", run_id, exc)
        if run_id:
            await fail_run(run_id, str(exc))
        raise
