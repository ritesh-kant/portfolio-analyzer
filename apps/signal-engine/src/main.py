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


@app.post("/trigger/pipeline")
async def trigger_pipeline() -> dict:
    """Run the full news-trader pipeline locally, bypassing SQS.

    Chains: ingester → classifier (per new article) → trade decision (per signal).
    The 15-min SQS delay is skipped so the full cycle completes in one call.
    """
    import asyncio
    from datetime import datetime, timezone

    from bson import ObjectId

    from handlers.news_ingester import _run as _ingest
    from handlers.news_classifier import _process_message
    from handlers.trade_decision import _process_signal
    from src.db.client import get_db
    from src.news_trader.db import ensure_indexes, signals as signals_coll

    logger.info("[PIPELINE] starting local full-chain run")
    run_start = datetime.now(tz=timezone.utc)

    # 1. Ingest
    ingest_result = await _ingest(settings)
    if ingest_result.get("skipped"):
        return {"skipped": ingest_result["skipped"]}

    n_new = ingest_result.get("new_articles", 0)
    logger.info("[PIPELINE] ingester done — %d new articles", n_new)

    if n_new == 0:
        return {"new_articles": 0, "signals": 0, "positions_opened": 0}

    # 2. Classify each new article (direct call, no SQS)
    db = get_db()
    await ensure_indexes(db)
    cursor = db["nt_news_raw"].find(
        {"ingested_at": {"$gte": run_start}, "classified": False},
        {"_id": 1},
    )
    new_ids = [str(doc["_id"]) async for doc in cursor]
    logger.info("[PIPELINE] classifying %d articles...", len(new_ids))

    acted_signals = 0
    for news_id in new_ids:
        try:
            if await _process_message({"news_id": news_id}, settings):
                acted_signals += 1
        except Exception as exc:
            logger.error("[PIPELINE] classifier error news_id=%s err=%s", news_id, exc)
        await asyncio.sleep(0.05)  # small gap between articles

    logger.info("[PIPELINE] classification done — %d actionable signals", acted_signals)

    if acted_signals == 0:
        return {"new_articles": n_new, "signals": 0, "positions_opened": 0}

    # 3. Trade decision for each new signal (skip the 15-min delay locally)
    sig_cursor = signals_coll(db).find(
        {"created_at": {"$gte": run_start}, "acted_on": False},
        sort=[("created_at", 1)],
    )
    total_entered = 0
    async for sig in sig_cursor:
        try:
            n = await _process_signal(sig, settings)
            total_entered += n
            await signals_coll(db).update_one(
                {"_id": sig["_id"]}, {"$set": {"acted_on": True}}
            )
        except Exception as exc:
            logger.error("[PIPELINE] trade error signal_id=%s err=%s", sig["_id"], exc)

    logger.info("[PIPELINE] done — articles=%d signals=%d positions=%d",
                n_new, acted_signals, total_entered)
    return {"new_articles": n_new, "signals": acted_signals, "positions_opened": total_entered}
