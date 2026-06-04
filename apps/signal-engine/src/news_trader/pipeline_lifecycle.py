"""Shared helpers for updating nt_pipeline_runs from within signal-engine.

Both the local FastAPI endpoint (main.py) and the production Lambda handlers
import from here so run finalization is always consistent.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId

from src.db.client import get_db

logger = logging.getLogger(__name__)


async def insert_run(source: str) -> str:
    db = get_db()
    result = await db["nt_pipeline_runs"].insert_one({
        "triggered_at": datetime.now(timezone.utc),
        "source": source,
        "status": "running",
    })
    run_id = str(result.inserted_id)
    logger.info("[LIFECYCLE] run_id=%s created source=%s", run_id, source)
    return run_id


async def finalise_run(run_id: str, result: dict[str, Any]) -> None:
    db = get_db()
    await db["nt_pipeline_runs"].update_one(
        {"_id": ObjectId(run_id)},
        {
            "$set": {
                "status": "completed",
                "completed_at": datetime.now(timezone.utc),
                "new_articles": result.get("new_articles", 0),
                "signals_created": result.get("signals", 0),
                "positions_opened": result.get("positions_opened", 0),
            }
        },
    )
    logger.info("[LIFECYCLE] run_id=%s marked completed articles=%d signals=%d positions=%d",
                run_id, result.get("new_articles", 0), result.get("signals", 0),
                result.get("positions_opened", 0))


async def fail_run(run_id: str, error: str) -> None:
    db = get_db()
    await db["nt_pipeline_runs"].update_one(
        {"_id": ObjectId(run_id)},
        {
            "$set": {
                "status": "failed",
                "completed_at": datetime.now(timezone.utc),
                "error": error,
            }
        },
    )
    logger.info("[LIFECYCLE] run_id=%s marked failed: %s", run_id, error)


async def delete_run(run_id: str) -> None:
    db = get_db()
    await db["nt_pipeline_runs"].delete_one({"_id": ObjectId(run_id)})
    logger.info("[LIFECYCLE] run_id=%s deleted (skipped — outside market hours)", run_id)


async def sweep_stale_runs(delay_seconds: int) -> int:
    """Backstop: finalize runs left in 'running' past the point where they could
    still be legitimately in-flight.

    On AWS the only thing that writes status=completed is tradeDecision's
    _maybe_finalise_run. But tradeDecision is SQS-triggered by the news-signals
    queue, so it never fires for a run whose articles all classified as
    non-actionable (neutral / low-conf / no-stocks) — nothing gets enqueued.
    Those runs would otherwise stay 'running' forever.

    A run with actionable signals self-finalizes via tradeDecision within the
    15-min SQS delay + processing. We use delay + 7-min buffer (matching the
    nt-pipeline-status TTL) as the cutoff: any run still 'running' past that is
    genuinely stuck, so we finalize it with the current counts. Returns the
    number of runs swept.
    """
    db = get_db()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=delay_seconds + 7 * 60)

    stale = await db["nt_pipeline_runs"].find(
        {"status": "running", "triggered_at": {"$lte": cutoff}},
        {"triggered_at": 1},
    ).to_list(length=50)

    for run in stale:
        run_id = str(run["_id"])
        triggered_at = run["triggered_at"]
        new_articles = await db["nt_news_raw"].count_documents(
            {"ingested_at": {"$gte": triggered_at}}
        )
        signals_created = await db["nt_signals"].count_documents(
            {"created_at": {"$gte": triggered_at}}
        )
        positions_opened = await db["nt_positions"].count_documents(
            {"entry_at": {"$gte": triggered_at}}
        )
        logger.warning(
            "[LIFECYCLE] sweeping stale run_id=%s triggered_at=%s — tradeDecision never finalised it",
            run_id, triggered_at,
        )
        await finalise_run(run_id, {
            "new_articles": new_articles,
            "signals": signals_created,
            "positions_opened": positions_opened,
        })

    return len(stale)
