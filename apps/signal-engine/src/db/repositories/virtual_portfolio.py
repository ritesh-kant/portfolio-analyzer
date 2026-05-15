"""Repository for trading_virtual_portfolio collection (singleton document)."""

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from src.db.constants import COLLECTION_NAMES

from .base import BaseRepository

_PORTFOLIO_ID = "main"


class VirtualPortfolioRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase) -> None:  # type: ignore[type-arg]
        super().__init__(db, COLLECTION_NAMES["VIRTUAL_PORTFOLIO"])

    async def get(self) -> dict[str, Any] | None:
        return await self.find_one({"portfolio_id": _PORTFOLIO_ID})

    async def ensure_exists(self, initial_capital: float) -> None:
        existing = await self.get()
        if existing is None:
            now = datetime.now(timezone.utc)
            await self.insert_one(
                {
                    "portfolio_id": _PORTFOLIO_ID,
                    "cash": initial_capital,
                    "invested": 0.0,
                    "total_value": initial_capital,
                    "initial_capital": initial_capital,
                    "open_positions": 0,
                    "total_trades": 0,
                    "winning_trades": 0,
                    "total_pnl": 0.0,
                    "total_pnl_pct": 0.0,
                    "updatedAt": now,
                }
            )

    async def apply_order(self, position_value: float) -> None:
        await self._col.update_one(
            {"portfolio_id": _PORTFOLIO_ID},
            {
                "$inc": {
                    "invested": position_value,
                    "cash": -position_value,
                    "open_positions": 1,
                    "total_trades": 1,
                },
                "$set": {"updatedAt": datetime.now(timezone.utc)},
            },
        )

    async def close_position(self, exit_value: float, position_value: float, was_correct: bool) -> None:
        """Release cash and update win/loss counters when a position is closed."""
        pnl = exit_value - position_value
        winning_inc = 1 if was_correct else 0
        await self._col.update_one(
            {"portfolio_id": _PORTFOLIO_ID},
            {
                "$inc": {
                    "cash": exit_value,
                    "invested": -position_value,
                    "open_positions": -1,
                    "winning_trades": winning_inc,
                    "total_pnl": pnl,
                },
                "$set": {"updatedAt": datetime.now(timezone.utc)},
            },
        )
        # Recompute total_value and total_pnl_pct from current doc
        doc = await self.get()
        if doc:
            total_value = float(doc.get("cash", 0)) + float(doc.get("invested", 0))
            initial = float(doc.get("initial_capital", total_value) or total_value)
            pnl_pct = round(((total_value - initial) / initial) * 100, 4) if initial else 0.0
            await self._col.update_one(
                {"portfolio_id": _PORTFOLIO_ID},
                {"$set": {"total_value": total_value, "total_pnl_pct": pnl_pct}},
            )

    async def update_totals(self, updates: dict[str, Any]) -> None:
        updates["updatedAt"] = datetime.now(timezone.utc)
        await self.update_one({"portfolio_id": _PORTFOLIO_ID}, {"$set": updates})
