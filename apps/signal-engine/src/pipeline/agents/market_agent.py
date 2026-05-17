"""market_agent — fetch Nifty 50 change%, India VIX, FII/DII net equity flows.

Data sources:
  - Nifty 50 (^NSEI) and India VIX (^INDIAVIX): yfinance
  - FII/DII net flows: NSE fiidiiTradeReact API

Populates state.market_data with all values needed by guard_agent.
"""

import asyncio
import logging
from typing import Any

from .base import BaseAgent
from ..state import TradingState
from ...scrapers.nse_market import fetch_nifty_vix_sync, fetch_fii_dii
from ...db.client import get_db
from ...db.repositories.market_snapshots import MarketSnapshotsRepository

logger = logging.getLogger(__name__)

_YFINANCE_TIMEOUT = 30


class MarketAgent(BaseAgent):
    name = "market_agent"

    async def _execute(self, state: TradingState) -> TradingState:
        # Run yfinance sync call in thread pool; FII/DII async concurrently
        nifty_vix_task = asyncio.wait_for(
            asyncio.to_thread(fetch_nifty_vix_sync),
            timeout=_YFINANCE_TIMEOUT,
        )
        fii_dii_task = fetch_fii_dii()

        nifty_vix, fii_dii = await asyncio.gather(
            nifty_vix_task,
            fii_dii_task,
            return_exceptions=True,
        )

        market_data: dict[str, Any] = {}

        if isinstance(nifty_vix, dict):
            market_data.update(nifty_vix)
        else:
            logger.warning("market_agent nifty_vix_failed error=%s", nifty_vix)

        if isinstance(fii_dii, dict):
            market_data.update(fii_dii)
        else:
            logger.warning("market_agent fii_dii_failed error=%s", fii_dii)

        logger.info(
            "market_agent nifty_change=%s vix=%s fii=%s dii=%s",
            market_data.get("nifty_change_pct"),
            market_data.get("vix"),
            market_data.get("fii_net_crore"),
            market_data.get("dii_net_crore"),
        )

        # Persist daily snapshot for future backtest replay
        if market_data and state.date:
            try:
                db = get_db()
                await MarketSnapshotsRepository(db).upsert(state.date, market_data)
            except Exception as exc:
                logger.warning("market_agent snapshot_save_failed error=%s", exc)

        return state.model_copy(update={"market_data": market_data})


market_agent = MarketAgent()
