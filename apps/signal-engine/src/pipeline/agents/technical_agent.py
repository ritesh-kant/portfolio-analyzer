"""technical_agent — compute RSI(14), MACD(12,26,9), EMA(20/50), volume ratio.

Uses yfinance for 75-day OHLCV data and pandas-ta for indicator calculation.
Each symbol is processed in a thread pool, all symbols run in parallel.
"""

import asyncio
import logging
import math
from typing import Any

import pandas as pd
import pandas_ta as ta  # noqa: F401 — registers df.ta accessor
import yfinance as yf

from .base import BaseAgent
from ..state import TradingState

logger = logging.getLogger(__name__)

_PERIOD = "75d"       # enough for EMA50 to stabilise (needs ~50 periods of warmup)
_MIN_BARS = 55        # skip symbol if fewer bars available
_TIMEOUT_S = 30


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return default


def _compute_indicators(symbol: str) -> dict[str, Any]:
    """Sync — runs inside asyncio.to_thread."""
    df = yf.download(symbol, period=_PERIOD, auto_adjust=True, progress=False, threads=False)

    # yfinance single-ticker sometimes wraps in MultiIndex
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    if df.empty or len(df) < _MIN_BARS:
        logger.warning("technical_agent insufficient_data symbol=%s bars=%d", symbol, len(df))
        return {}

    # pandas-ta expects DataFrame with Open/High/Low/Close/Volume columns (case-insensitive)
    df.ta.rsi(length=14, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.ema(length=20, append=True)
    df.ta.ema(length=50, append=True)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = _safe_float(last.get("Close"), 0.0)
    prev_close = _safe_float(prev.get("Close"), close)
    change_pct = (close - prev_close) / prev_close * 100 if prev_close else 0.0

    vol_series = df["Volume"].tail(20)
    vol_avg = float(vol_series.mean()) if not vol_series.empty else 0.0
    vol_today = _safe_float(last.get("Volume"), 0.0)
    vol_ratio = vol_today / vol_avg if vol_avg > 0 else 1.0

    ema20 = _safe_float(last.get("EMA_20"), close)
    ema50 = _safe_float(last.get("EMA_50"), close)

    return {
        "symbol": symbol,
        "close": round(close, 2),
        "change_pct_today": round(change_pct, 3),
        "rsi": round(_safe_float(last.get("RSI_14"), 50.0), 2),
        "macd": round(_safe_float(last.get("MACD_12_26_9"), 0.0), 4),
        "macd_signal": round(_safe_float(last.get("MACDs_12_26_9"), 0.0), 4),
        "macd_hist": round(_safe_float(last.get("MACDh_12_26_9"), 0.0), 4),
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "volume": vol_today,
        "volume_ratio": round(vol_ratio, 3),
        "above_ema20": close > ema20,
        "above_ema50": close > ema50,
    }


async def _fetch_one(symbol: str) -> tuple[str, dict[str, Any]]:
    try:
        data = await asyncio.wait_for(
            asyncio.to_thread(_compute_indicators, symbol),
            timeout=_TIMEOUT_S,
        )
        return symbol, data
    except asyncio.TimeoutError:
        logger.warning("technical_agent timeout symbol=%s", symbol)
        return symbol, {}
    except Exception as exc:
        logger.warning("technical_agent error symbol=%s error=%s", symbol, exc)
        return symbol, {}


class TechnicalAgent(BaseAgent):
    name = "technical_agent"

    async def _execute(self, state: TradingState) -> TradingState:
        if not state.selected_stocks:
            logger.info("technical_agent no selected_stocks — skipping")
            return state

        results = await asyncio.gather(*[_fetch_one(s) for s in state.selected_stocks])
        technical_data = {sym: data for sym, data in results if data}

        logger.info(
            "technical_agent computed symbols=%d failed=%d",
            len(technical_data),
            len(state.selected_stocks) - len(technical_data),
        )
        return state.model_copy(update={"technical_data": technical_data})


technical_agent = TechnicalAgent()
