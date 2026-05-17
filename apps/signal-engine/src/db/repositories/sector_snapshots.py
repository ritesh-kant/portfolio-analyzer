"""Repository for trading_sector_snapshots collection.

One document per trading date — upserted by sector_agent after each pipeline run.
Preserves the daily sector direction and confidence scores needed for replaying
signal scoring in future backtests.
"""

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from src.db.constants import COLLECTION_NAMES

from .base import BaseRepository


class SectorSnapshotsRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase) -> None:  # type: ignore[type-arg]
        super().__init__(db, COLLECTION_NAMES["SECTOR_SNAPSHOTS"])

    async def upsert(self, date: str, run_id: str, sectors: list[dict[str, Any]]) -> None:
        """Insert or replace the sector snapshot for a given trading date."""
        now = datetime.now(timezone.utc)
        doc = {
            "date": date,
            "run_id": run_id,
            "sectors": sectors,
            "updatedAt": now,
        }
        await self._col.update_one(
            {"date": date},
            {"$set": doc, "$setOnInsert": {"createdAt": now}},
            upsert=True,
        )
