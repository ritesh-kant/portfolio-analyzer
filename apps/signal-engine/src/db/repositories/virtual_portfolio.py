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
                    "unrealized_pnl": 0.0,
                    "total_value": initial_capital,
                    "initial_capital": initial_capital,
                    "open_positions": 0,
                    "total_trades": 0,
                    "winning_trades": 0,
                    "total_pnl": 0.0,
                    "total_pnl_pct": 0.0,
                    "daily_pnl": 0.0,
                    "daily_pnl_date": "",
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

    async def get_daily_loss_pct(self) -> float:
        """Return today's realized PnL as % of initial capital. Negative means loss.

        Returns 0.0 when no positions have been closed today or on the first
        run of a new day (before the daily reset in order_agent fires).
        """
        doc = await self.get()
        if doc is None:
            return 0.0
        today = datetime.now(timezone.utc).date().isoformat()
        if doc.get("daily_pnl_date", "") != today:
            return 0.0
        initial = float(doc.get("initial_capital", 1.0) or 1.0)
        return round(float(doc.get("daily_pnl", 0.0)) / initial * 100.0, 4)

    async def record_close_pnl(self, pnl: float) -> None:
        """Accumulate realized PnL for today's circuit-breaker counter.

        Called by monitor_agent after every position close. Automatically
        resets the daily counter when called on a new calendar date.
        """
        today = datetime.now(timezone.utc).date().isoformat()
        doc = await self.get()
        if doc is None:
            return
        now = datetime.now(timezone.utc)
        if doc.get("daily_pnl_date", "") != today:
            await self._col.update_one(
                {"portfolio_id": _PORTFOLIO_ID},
                {"$set": {"daily_pnl": pnl, "daily_pnl_date": today, "updatedAt": now}},
            )
        else:
            await self._col.update_one(
                {"portfolio_id": _PORTFOLIO_ID},
                {"$inc": {"daily_pnl": pnl}, "$set": {"updatedAt": now}},
            )

    async def update_mark_to_market(self, current_positions_market_value: float) -> None:
        """Recompute total_value and unrealized_pnl using live market prices.

        Called by run_monitor() at the END of each monitoring cycle, after all
        close decisions have been made. Closed positions must already be excluded
        from current_positions_market_value by the caller.

        Does NOT touch: cash, invested, daily_pnl, open_positions, or trade counters.
        """
        doc = await self.get()
        if doc is None:
            return
        cash     = float(doc.get("cash", 0.0))
        invested = float(doc.get("invested", 0.0))
        initial  = float(doc.get("initial_capital", 1.0) or 1.0)

        total_value    = round(cash + current_positions_market_value, 2)
        unrealized_pnl = round(current_positions_market_value - invested, 2)
        total_pnl_pct  = round(((total_value - initial) / initial) * 100, 4) if initial else 0.0

        await self._col.update_one(
            {"portfolio_id": _PORTFOLIO_ID},
            {"$set": {
                "total_value":    total_value,
                "unrealized_pnl": unrealized_pnl,
                "total_pnl_pct":  total_pnl_pct,
                "updatedAt":      datetime.now(timezone.utc),
            }},
        )
