"""Shared helpers for updating nt_pipeline_runs from within signal-engine.

Both the local FastAPI endpoint (main.py) and the production Lambda handlers
import from here so run finalization is always consistent.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from src.db.client import get_db

logger = logging.getLogger(__name__)


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
