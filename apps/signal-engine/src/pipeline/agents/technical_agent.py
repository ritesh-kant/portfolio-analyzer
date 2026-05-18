"""technical_agent — compute RSI(14), MACD(12,26,9), EMA(20/50), Bollinger Bands,
volume ratio, and 52-week high/low proximity.

Uses yfinance for 75-day OHLCV data and pandas-ta for indicator calculation.
Each symbol is processed in a thread pool, all symbols run in parallel.
"""

import asyncio
import logging
import math
from datetime import date as _date
from typing import Any

import pandas as pd
import pandas_ta as ta  # noqa: F401 — registers df.ta accessor
import yfinance as yf

from .base import BaseAgent
from ..state import TradingState
from ...scrapers.nse_options import fetch_pcr_batch

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


def _is_stale(last_bar_date: _date, today: _date) -> bool:
    """Return True only if data is older than the most recent valid trading session.

    Accounts for weekends and long-weekend gaps so Monday/post-holiday runs
    don't incorrectly flag Friday's data as stale.
    """
    gap = (today - last_bar_date).days
    if gap <= 1:
        return False
    # Monday: Friday close is 3 calendar days ago — still valid
    if gap <= 3 and today.weekday() == 0:
        return False
    # Tuesday: allow 4-day gap to cover Mon public holidays
    if gap <= 4 and today.weekday() == 1:
        return False
    return True


def _compute_indicators(symbol: str) -> dict[str, Any]:
    """Sync — runs inside asyncio.to_thread."""
    df = yf.download(symbol, period=_PERIOD, auto_adjust=True, progress=False, threads=False)

    # yfinance single-ticker sometimes wraps in MultiIndex
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    if df.empty or len(df) < _MIN_BARS:
        logger.warning("technical_agent insufficient_data symbol=%s bars=%d", symbol, len(df))
        return {}

    # Staleness guard: last bar must be today or the most recent valid trading day.
    last_bar_date = df.index[-1].date() if hasattr(df.index[-1], "date") else df.index[-1]
    today = _date.today()
    if _is_stale(last_bar_date, today):
        logger.warning(
            "technical_agent stale_data symbol=%s last_bar=%s today=%s — skipping",
            symbol, last_bar_date, today,
        )
        return {"stale": True, "last_data_date": str(last_bar_date)}

    # pandas-ta expects DataFrame with Open/High/Low/Close/Volume columns (case-insensitive)
    df.ta.rsi(length=14, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.ema(length=20, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.bbands(length=20, std=2, append=True)
    df.ta.atr(length=14, append=True)   # ATRr_14 — absolute rupee ATR for adaptive stop sizing

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = _safe_float(last.get("Close"), 0.0)
    prev_close = _safe_float(prev.get("Close"), close)
    change_pct = (close - prev_close) / prev_close * 100 if prev_close else 0.0

    close_5d_ago = _safe_float(df["Close"].iloc[-5], close) if len(df) >= 5 else close
    five_day_ret = (close - close_5d_ago) / close_5d_ago * 100 if close_5d_ago else 0.0

    vol_series = df["Volume"].tail(20)
    vol_avg = float(vol_series.mean()) if not vol_series.empty else 0.0
    vol_today = _safe_float(last.get("Volume"), 0.0)
    vol_ratio = vol_today / vol_avg if vol_avg > 0 else 1.0

    ema20 = _safe_float(last.get("EMA_20"), close)
    ema50 = _safe_float(last.get("EMA_50"), close)

    # Bollinger Bands (20-period, 2 std dev)
    bb_upper = _safe_float(last.get("BBU_20_2.0"), close * 1.02)
    bb_lower = _safe_float(last.get("BBL_20_2.0"), close * 0.98)
    bb_mid   = _safe_float(last.get("BBM_20_2.0"), close)
    bb_range = bb_upper - bb_lower
    bb_pct   = (close - bb_lower) / bb_range if bb_range > 0 else 0.5
    bb_width = bb_range / bb_mid if bb_mid > 0 else 0.0

    # 52-week high/low using the full 75-day window as a proxy
    high_75d = float(df["High"].max()) if "High" in df.columns else close
    low_75d  = float(df["Low"].min())  if "Low"  in df.columns else close
    pct_from_high = (close - high_75d) / high_75d * 100 if high_75d > 0 else 0.0
    pct_from_low  = (close - low_75d)  / low_75d  * 100 if low_75d  > 0 else 0.0

    return {
        "symbol": symbol,
        "close": round(close, 2),
        "change_pct_today": round(change_pct, 3),
        "five_day_ret": round(five_day_ret, 3),
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
        # Bollinger Bands
        "bb_upper": round(bb_upper, 2),
        "bb_lower": round(bb_lower, 2),
        "bb_pct": round(bb_pct, 3),    # 0 = at lower band, 1 = at upper band
        "bb_width": round(bb_width, 4),
        # 52-week proxy (75-day window)
        "high_75d": round(high_75d, 2),
        "low_75d": round(low_75d, 2),
        "pct_from_high": round(pct_from_high, 2),
        "pct_from_low": round(pct_from_low, 2),
        # ATR(14) — absolute rupee value; used by order_agent for adaptive stop/target sizing.
        # pandas_ta names column ATRr_14 (older) or ATR_14 (newer) — try both.
        "atr14": round(_safe_float(last.get("ATRr_14") or last.get("ATR_14"), 0.0), 2),
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

        # Fetch PCR for all non-stale symbols concurrently (fails gracefully per symbol)
        valid_symbols = [sym for sym, data in technical_data.items() if not data.get("stale")]
        if valid_symbols:
            pcr_data = await fetch_pcr_batch(valid_symbols)
            for sym, pcr_info in pcr_data.items():
                if sym in technical_data and pcr_info:
                    technical_data[sym].update(pcr_info)

        logger.info(
            "technical_agent computed symbols=%d failed=%d",
            len(technical_data),
            len(state.selected_stocks) - len(technical_data),
        )
        return state.model_copy(update={"technical_data": technical_data})


technical_agent = TechnicalAgent()
