"""Repository for trading_stock_analyses collection."""

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from src.db.constants import COLLECTION_NAMES

from .base import BaseRepository


class StockAnalysesRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase) -> None:  # type: ignore[type-arg]
        super().__init__(db, COLLECTION_NAMES["STOCK_ANALYSES"])

    async def upsert(self, run_id: str, symbol: str, data: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        data.setdefault("createdAt", now)
        data["updatedAt"] = now
        await self.update_one(
            {"run_id": run_id, "symbol": symbol},
            {"$set": data},
            upsert=True,
        )

    async def get_by_run(self, run_id: str) -> list[dict[str, Any]]:
        cursor = self._col.find({"run_id": run_id})
        return await cursor.to_list(length=None)  # type: ignore[arg-type]
