"""Repository for trading_paper_orders collection."""

from datetime import date, datetime, timedelta, timezone
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
        return await cursor.to_list(length=None)

    async def has_open_position(self, symbol: str) -> bool:
        doc = await self._col.find_one({"symbol": symbol, "status": "OPEN"})
        return doc is not None

    async def get_open_sector_counts(self, sector_map: dict[str, str]) -> dict[str, int]:
        """Return {sector_name: open_position_count} for all currently OPEN orders.

        Uses in-memory grouping on get_open_orders() — safe because open positions
        are always bounded by max_positions (≤8), so no aggregation pipeline needed.
        """
        open_orders = await self.get_open_orders()
        counts: dict[str, int] = {}
        for order in open_orders:
            sector = sector_map.get(order.get("symbol", ""))
            if sector:
                counts[sector] = counts.get(sector, 0) + 1
        return counts

    async def close_order(
        self,
        order_id: Any,
        exit_price: float,
        actual_return_pct: float,
        was_correct: bool,
        exit_note: str = "",
        exit_reason: str = "",
    ) -> None:
        fields: dict[str, Any] = {
            "status": "CLOSED",
            "exit_price": exit_price,
            "exit_date": datetime.now(timezone.utc).date().isoformat(),
            "actual_return_pct": actual_return_pct,
            "was_correct": was_correct,
            "updatedAt": datetime.now(timezone.utc),
        }
        if exit_note:
            fields["exit_note"] = exit_note
        if exit_reason:
            fields["exit_reason"] = exit_reason
        await self.update_one({"_id": order_id}, {"$set": fields})

    async def update_trail(
        self,
        order_id: Any,
        *,
        highest_close: float,
        trailing_stop: float,
    ) -> None:
        """Persist the ratcheted high-water close and chandelier trailing stop.

        Both values move up only — the monitor computes max(prev, new) before
        calling this so the write is unconditional from the repo's perspective.
        """
        await self.update_one(
            {"_id": order_id},
            {"$set": {
                "highest_close": highest_close,
                "trailing_stop": trailing_stop,
                "updatedAt": datetime.now(timezone.utc),
            }},
        )

    async def partial_close(
        self,
        order_id: Any,
        *,
        shares_closed: int,
        remaining_shares: int,
        remaining_position_value: float,
        partial_exit: dict[str, Any],
        new_original_stop: float,
    ) -> None:
        """Record a partial exit (TP1) on an open order.

        The order remains OPEN with reduced shares. The closed slice is appended
        to a `partial_exits` array on the doc for audit. The order's
        `original_stop` is moved to breakeven (entry price), and `tp1_taken`
        is flipped so the trailing-stop branch activates.
        """
        await self.update_one(
            {"_id": order_id},
            {
                "$set": {
                    "shares": remaining_shares,
                    "position_value": remaining_position_value,
                    "tp1_taken": True,
                    "original_stop": new_original_stop,
                    "trailing_stop": new_original_stop,
                    "updatedAt": datetime.now(timezone.utc),
                },
                "$push": {"partial_exits": partial_exit},
            },
        )

    async def set_cooloff(self, order_id: Any, cooloff_until: str) -> None:
        """Set cooloff_until ISO date on a closed STOP order.

        stock_selector reads this to skip re-entry on recently stopped-out symbols.
        """
        await self.update_one(
            {"_id": order_id},
            {"$set": {"cooloff_until": cooloff_until}},
        )

    async def get_cooled_off_symbols(self) -> set[str]:
        """Return symbols whose cooloff_until date is today or in the future."""
        today = date.today().isoformat()
        cursor = self._col.find(
            {"cooloff_until": {"$gte": today}},
            projection={"symbol": 1},
        )
        return {doc["symbol"] async for doc in cursor}


def cooloff_until_date(business_days: int = 15) -> str:
    """Return the ISO date that is `business_days` trading days from today."""
    current = date.today()
    added = 0
    while added < business_days:
        current += timedelta(days=1)
        if current.weekday() < 5:   # Mon–Fri only
            added += 1
    return current.isoformat()
