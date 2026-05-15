"""Repository for trading_paper_orders collection."""

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from src.db.constants import COLLECTION_NAMES

from .base import BaseRepository


class PaperOrdersRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase) -> None:  # type: ignore[type-arg]
        super().__init__(db, COLLECTION_NAMES["PAPER_ORDERS"])

    async def insert_order(self, order: dict[str, Any]) -> str:
        now = datetime.now(timezone.utc)
        order.setdefault("createdAt", now)
        order.setdefault("updatedAt", now)
        order.setdefault("status", "OPEN")
        return await self.insert_one(order)

    async def get_open_orders(self) -> list[dict[str, Any]]:
        cursor = self._col.find({"status": "OPEN"})
        return await cursor.to_list(length=None)  # type: ignore[arg-type]

    async def close_order(
        self, order_id: Any, exit_price: float, actual_return_pct: float, was_correct: bool
    ) -> None:
        await self.update_one(
            {"_id": order_id},
            {
                "$set": {
                    "status": "CLOSED",
                    "exit_price": exit_price,
                    "exit_date": datetime.now(timezone.utc).date().isoformat(),
                    "actual_return_pct": actual_return_pct,
                    "was_correct": was_correct,
                    "updatedAt": datetime.now(timezone.utc),
                }
            },
        )
