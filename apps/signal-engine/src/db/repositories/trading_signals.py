"""Repository for trading_signals collection."""

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from src.db.constants import COLLECTION_NAMES

from .base import BaseRepository


class TradingSignalsRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase) -> None:  # type: ignore[type-arg]
        super().__init__(db, COLLECTION_NAMES["TRADING_SIGNALS"])

    async def insert_signal(self, signal: dict[str, Any]) -> str:
        now = datetime.now(timezone.utc)
        signal.setdefault("createdAt", now)
        signal.setdefault("updatedAt", now)
        signal.setdefault("order_placed", False)
        return await self.insert_one(signal)

    async def mark_order_placed(self, run_id: str, symbol: str) -> None:
        await self.update_one(
            {"run_id": run_id, "symbol": symbol},
            {
                "$set": {
                    "order_placed": True,
                    "updatedAt": datetime.now(timezone.utc),
                }
            },
        )

    async def update_outcome(
        self,
        run_id: str,
        symbol: str,
        actual_return_pct: float,
        was_correct: bool,
        outcome_date: str,
    ) -> None:
        """Back-fill outcome data once a paper position is closed.

        Called by monitor_agent after stop-loss or target is hit. Linking outcome
        back to trading_signals (not just paper_orders) enables per-signal accuracy
        queries without a join across two collections.
        """
        await self.update_one(
            {"run_id": run_id, "symbol": symbol},
            {
                "$set": {
                    "actual_return_pct": round(actual_return_pct, 4),
                    "was_correct": was_correct,
                    "outcome_date": outcome_date,
                    "updatedAt": datetime.now(timezone.utc),
                }
            },
        )

    async def get_by_run(self, run_id: str) -> list[dict[str, Any]]:
        cursor = self._col.find({"run_id": run_id})
        return await cursor.to_list(length=None)  # type: ignore[arg-type]
