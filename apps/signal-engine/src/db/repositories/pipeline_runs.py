"""Repository for trading_pipeline_runs collection."""

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from src.db.constants import COLLECTION_NAMES

from .base import BaseRepository

_AGENTS = [
    "news_agent",
    "sector_agent",
    "stock_selector",
    "technical_agent",
    "market_agent",
    "guard_agent",
    "signal_agent",
    "order_agent",
    "audit_agent",
]


class PipelineRunsRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase) -> None:  # type: ignore[type-arg]
        super().__init__(db, COLLECTION_NAMES["PIPELINE_RUNS"])

    async def create(self, run_id: str, date: str, ai_provider: str) -> None:
        now = datetime.now(timezone.utc)
        await self.insert_one(
            {
                "run_id": run_id,
                "date": date,
                "status": "running",
                "ai_provider": ai_provider,
                "started_at": now,
                "agent_statuses": {a: "pending" for a in _AGENTS},
                "agent_timings": {},
                "createdAt": now,
                "updatedAt": now,
            }
        )

    async def update_agent_status(
        self, run_id: str, agent: str, status: str
    ) -> None:
        await self.update_one(
            {"run_id": run_id},
            {
                "$set": {
                    f"agent_statuses.{agent}": status,
                    "updatedAt": datetime.now(timezone.utc),
                }
            },
        )

    async def record_timing(
        self, run_id: str, agent: str, duration_ms: float
    ) -> None:
        await self.update_one(
            {"run_id": run_id},
            {
                "$set": {
                    f"agent_timings.{agent}": duration_ms,
                    "updatedAt": datetime.now(timezone.utc),
                }
            },
        )

    async def finalize(
        self,
        run_id: str,
        status: str,
        stats: dict[str, Any] | None = None,
        error_summary: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        update: dict[str, Any] = {
            "$set": {
                "status": status,
                "completed_at": now,
                "updatedAt": now,
            }
        }
        if stats:
            update["$set"]["stats"] = stats
        if error_summary:
            update["$set"]["error_summary"] = error_summary
        await self.update_one({"run_id": run_id}, update)
