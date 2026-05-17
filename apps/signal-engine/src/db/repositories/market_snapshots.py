"""Repository for trading_market_snapshots collection.

One document per trading date — upserted by market_agent after each pipeline run.
Provides a clean daily macro record (Nifty, VIX, FII) for future backtest replay
without re-fetching from yfinance or NSE APIs.
"""

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from src.db.constants import COLLECTION_NAMES

from .base import BaseRepository


class MarketSnapshotsRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase) -> None:  # type: ignore[type-arg]
        super().__init__(db, COLLECTION_NAMES["MARKET_SNAPSHOTS"])

    async def upsert(self, date: str, market_data: dict[str, Any]) -> None:
        """Insert or replace the market snapshot for a given trading date."""
        now = datetime.now(timezone.utc)
        doc = {
            "date": date,
            "nifty_close": market_data.get("nifty_close"),
            "nifty_prev_close": market_data.get("nifty_prev_close"),
            "nifty_change_pct": market_data.get("nifty_change_pct"),
            "nifty_5d_return": market_data.get("nifty_5d_return"),
            "nifty_30d_return": market_data.get("nifty_30d_return"),
            "nifty_ema50": market_data.get("nifty_ema50"),
            "nifty_above_ema50": market_data.get("nifty_above_ema50"),
            "vix": market_data.get("vix"),
            "vix_caution": market_data.get("vix_caution", False),
            "fii_net_crore": market_data.get("fii_net_crore"),
            "dii_net_crore": market_data.get("dii_net_crore"),
            "updatedAt": now,
        }
        await self._col.update_one(
            {"date": date},
            {"$set": doc, "$setOnInsert": {"createdAt": now}},
            upsert=True,
        )
