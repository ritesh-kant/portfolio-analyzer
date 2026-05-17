"""Repository for trading_intraday_bars collection.

One document per (symbol, date, interval) — upserted by monitor_agent on each
price fetch. yfinance only provides 1m bars for the last ~7 days, so bars must
be archived daily to enable future intraday backtesting.

Last write wins: the final monitor run of the trading day holds the most complete
set of bars for that date.
"""

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from src.db.constants import COLLECTION_NAMES

from .base import BaseRepository

_MIN_BARS_EXPECTED = 200  # full NSE session ≈ 375 bars; warn if significantly fewer


class IntradayBarsRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase) -> None:  # type: ignore[type-arg]
        super().__init__(db, COLLECTION_NAMES["INTRADAY_BARS"])

    async def upsert(
        self,
        symbol: str,
        date: str,
        interval: str,
        bars: list[dict[str, Any]],
    ) -> None:
        """Insert or replace the intraday bar archive for a given symbol/date/interval."""
        now = datetime.now(timezone.utc)
        doc = {
            "symbol": symbol,
            "date": date,
            "interval": interval,
            "bars": bars,
            "bar_count": len(bars),
            "updatedAt": now,
        }
        await self._col.update_one(
            {"symbol": symbol, "date": date, "interval": interval},
            {"$set": doc, "$setOnInsert": {"createdAt": now}},
            upsert=True,
        )
