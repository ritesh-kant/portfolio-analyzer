"""stock_selector — rank and select top stocks per bullish sector by relative strength.

Algorithm:
  1. Collect candidate symbols from all bullish sectors (score > 50)
  2. Batch-download 35-day OHLCV from yfinance (sync, run in thread)
  3. Calculate 30-day relative strength vs Nifty 50 (^NSEI)
  4. Filter by liquidity: avg daily volume ≥ MIN_VOLUME
  5. Rank by RS, take top MAX_PER_SECTOR per sector
  6. Global cap at MAX_POSITIONS total; deduplicate across sectors
"""

import asyncio
import logging
from typing import Any

import pandas as pd
import yfinance as yf

from .base import BaseAgent
from ..state import TradingState
from ...scrapers.sector_stocks import SECTOR_STOCKS

logger = logging.getLogger(__name__)

_RS_DAYS = 30
_DOWNLOAD_PERIOD = f"{_RS_DAYS + 7}d"
_MIN_VOLUME = 500_000       # avg daily shares traded
_MAX_PER_SECTOR = 2
_MAX_POSITIONS = 8
_NIFTY = "^NSEI"
_YFINANCE_TIMEOUT = 60      # seconds


def _compute_rs(closes: pd.DataFrame, nifty_col: str) -> dict[str, float]:
    """Return {symbol: RS_ratio} for each non-Nifty column."""
    if nifty_col not in closes.columns or closes[nifty_col].dropna().shape[0] < 5:
        return {}
    nifty_ret = (closes[nifty_col].iloc[-1] / closes[nifty_col].iloc[0]) - 1
    if nifty_ret == 0:
        return {}
    rs: dict[str, float] = {}
    for col in closes.columns:
        if col == nifty_col:
            continue
        series = closes[col].dropna()
        if series.shape[0] < 5:
            continue
        stock_ret = (series.iloc[-1] / series.iloc[0]) - 1
        rs[col] = stock_ret / nifty_ret
    return rs


def _compute_avg_volume(data: pd.DataFrame, symbol: str) -> float:
    """Return average daily volume for a symbol from a multi-ticker download."""
    try:
        vol_col = ("Volume", symbol) if isinstance(data.columns, pd.MultiIndex) else "Volume"
        return float(data[vol_col].dropna().mean())
    except Exception:
        return 0.0


def _download_batch(symbols: list[str]) -> dict[str, Any]:
    """Sync yfinance download — runs in thread pool."""
    all_symbols = symbols + [_NIFTY]
    data = yf.download(
        all_symbols,
        period=_DOWNLOAD_PERIOD,
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    if data.empty:
        return {"closes": pd.DataFrame(), "raw": data}

    if isinstance(data.columns, pd.MultiIndex):
        closes = data["Close"]
        volumes = data.get("Volume", pd.DataFrame())
    else:
        closes = data[["Close"]].rename(columns={"Close": symbols[0]})
        volumes = data[["Volume"]].rename(columns={"Volume": symbols[0]})

    return {"closes": closes, "volumes": volumes, "raw": data}


class StockSelector(BaseAgent):
    name = "stock_selector"

    async def _execute(self, state: TradingState) -> TradingState:
        bullish_sectors = [
            s for s in state.sectors
            if s.get("direction") == "bullish" and s.get("score", 0) > 50
        ]
        if not bullish_sectors:
            logger.info("stock_selector no bullish sectors — skipping")
            return state

        # Collect unique candidate symbols
        sector_candidates: dict[str, list[str]] = {}
        all_symbols: list[str] = []
        seen: set[str] = set()
        for sector in bullish_sectors:
            name = sector["name"]
            candidates = SECTOR_STOCKS.get(name, [])
            sector_candidates[name] = candidates
            for sym in candidates:
                if sym not in seen:
                    all_symbols.append(sym)
                    seen.add(sym)

        if not all_symbols:
            return state

        # Download price data in thread pool
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_download_batch, all_symbols),
                timeout=_YFINANCE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            state.errors.append("stock_selector: yfinance download timed out")
            return state

        closes: pd.DataFrame = result.get("closes", pd.DataFrame())
        volumes: pd.DataFrame = result.get("volumes", pd.DataFrame())
        if closes.empty or _NIFTY not in closes.columns:
            logger.warning("stock_selector: no price data or Nifty missing")
            return state

        rs_map = _compute_rs(closes, _NIFTY)

        # Per-sector selection
        selected: list[str] = []
        added: set[str] = set()

        for sector in bullish_sectors:
            name = sector["name"]
            candidates = sector_candidates.get(name, [])
            ranked = sorted(
                [sym for sym in candidates if sym in rs_map],
                key=lambda s: rs_map[s],
                reverse=True,
            )
            count = 0
            for sym in ranked:
                if sym in added:
                    continue
                # Liquidity check
                avg_vol = _compute_avg_volume(result["raw"], sym)
                if avg_vol < _MIN_VOLUME:
                    continue
                selected.append(sym)
                added.add(sym)
                count += 1
                if count >= _MAX_PER_SECTOR:
                    break

            if len(selected) >= _MAX_POSITIONS:
                break

        logger.info("stock_selector selected=%s", selected)
        return state.model_copy(update={"selected_stocks": selected[:_MAX_POSITIONS]})


stock_selector = StockSelector()
