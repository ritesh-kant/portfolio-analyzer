"""Repository for trading_agent_logs collection (TTL: 30 days)."""

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from src.db.constants import COLLECTION_NAMES

from .base import BaseRepository


class AgentLogsRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase) -> None:  # type: ignore[type-arg]
        super().__init__(db, COLLECTION_NAMES["AGENT_LOGS"])

    async def log(
        self,
        run_id: str,
        agent: str,
        level: str,
        message: str,
        duration_ms: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        doc: dict[str, Any] = {
            "run_id": run_id,
            "agent": agent,
            "level": level,
            "message": message,
            "createdAt": datetime.now(timezone.utc),
        }
        if duration_ms is not None:
            doc["duration_ms"] = duration_ms
        if context:
            doc["context"] = context
        await self.insert_one(doc)

    async def ensure_ttl_index(self) -> None:
        await self._col.create_index(
            "createdAt", expireAfterSeconds=2_592_000, background=True
        )
