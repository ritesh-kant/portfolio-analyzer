"""indicators — replicate technical_agent.py indicator computation for the backtest.

This module is the backtest's parity-critical layer. Every formula here must
produce the same result as the production technical_agent._compute_indicators()
for identical input data.

Parity guarantee:
    The unit test tests/test_backtest_parity.py feeds the same OHLCV DataFrame
    to both this module and the production technical_agent and asserts equal output.
    If production logic changes, this file MUST be updated to match.

Key design differences from technical_agent:
    - Operates on a pre-fetched DataFrame (no yfinance download inside this module).
    - The staleness guard is replaced by a backtest-mode date parameter — the caller
      controls which date is "today" for a historical simulation.
    - PCR is not available historically; it is returned as None and callers default to
      neutral (0 pts contribution) when None.
    - Returns a dict with exactly the same keys as technical_agent._compute_indicators().
"""

import logging
import math
from typing import Any

import pandas as pd
import pandas_ta as ta  # noqa: F401 — registers df.ta accessor

logger = logging.getLogger(__name__)

# ── Constants (must match technical_agent.py) ─────────────────────────────────
_MIN_BARS = 55   # minimum bars needed for EMA50 to stabilise


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Identical to technical_agent._safe_float."""
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return default


def compute_indicators(
    symbol: str,
    ohlcv: pd.DataFrame,
    as_of_date: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Compute all indicators for a single symbol on a specific backtest date.

    Args:
        symbol:      NSE symbol (e.g. "RELIANCE.NS"). Used only for logging.
        ohlcv:       DataFrame with lowercase columns: open, high, low, close, volume.
                     Index must be a DatetimeIndex sorted ascending.
                     Pass the trailing 75-day window (matching production's _PERIOD="75d").
        as_of_date:  The simulation date. If None, uses the last row (latest date).
                     The function selects the row for this date and uses it as "last".

    Returns:
        A dict with the same keys as technical_agent._compute_indicators():
            symbol, close, change_pct_today, five_day_ret,
            rsi, macd, macd_signal, macd_hist,
            ema20, ema50, volume, volume_ratio,
            above_ema20, above_ema50,
            bb_upper, bb_lower, bb_pct, bb_width,
            high_75d, low_75d, pct_from_high, pct_from_low.
        Returns {} if insufficient data.
        Returns {"stale": True, ...} to signal the caller to skip (backtest equivalent
        of the production staleness guard — used when the as_of_date has no data bar).

    Note:
        PCR is not included — it is not historically available for free.
        Callers should treat None PCR as neutral (0 pts contribution).
    """
    df = ohlcv.copy()

    # Ensure lowercase column names (normalise upstream variations)
    df.columns = [c.lower() for c in df.columns]

    if len(df) < _MIN_BARS:
        logger.debug(
            "indicators insufficient_data symbol=%s bars=%d (min %d)",
            symbol, len(df), _MIN_BARS,
        )
        return {}

    # ── Select the row to treat as "today" ────────────────────────────────────
    if as_of_date is not None:
        # Find the most recent bar on or before as_of_date
        available = df.index[df.index <= as_of_date]
        if available.empty:
            logger.debug("indicators no_bar symbol=%s as_of=%s", symbol, as_of_date)
            return {"stale": True, "last_data_date": str(df.index[0].date())}
        last_idx = available[-1]
        # We work on the slice up to and including last_idx
        df = df.loc[:last_idx]
        if len(df) < _MIN_BARS:
            return {}
    # else: use the full DataFrame as-is (last row = "today")

    # ── Compute pandas-ta indicators on the window ────────────────────────────
    # Must match technical_agent exactly:
    #   rsi(14), macd(12,26,9), ema(20), ema(50), bbands(20,2)
    # ATR(14) added for ATR-based stop/target sizing (backtest only).
    df.ta.rsi(length=14, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.ema(length=20, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.bbands(length=20, std=2, append=True)
    df.ta.atr(length=14, append=True)   # column: ATRr_14

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = _safe_float(last.get("close"), 0.0)
    prev_close = _safe_float(prev.get("close"), close)
    change_pct = (close - prev_close) / prev_close * 100 if prev_close else 0.0

    close_5d_ago = _safe_float(df["close"].iloc[-5], close) if len(df) >= 5 else close
    five_day_ret = (close - close_5d_ago) / close_5d_ago * 100 if close_5d_ago else 0.0

    vol_series = df["volume"].tail(20)
    vol_avg = float(vol_series.mean()) if not vol_series.empty else 0.0
    vol_today = _safe_float(last.get("volume"), 0.0)
    vol_ratio = vol_today / vol_avg if vol_avg > 0 else 1.0

    ema20 = _safe_float(last.get("EMA_20"), close)
    ema50 = _safe_float(last.get("EMA_50"), close)

    # Bollinger Bands — pandas-ta column names: BBU_20_2.0, BBL_20_2.0, BBM_20_2.0
    bb_upper = _safe_float(last.get("BBU_20_2.0"), close * 1.02)
    bb_lower = _safe_float(last.get("BBL_20_2.0"), close * 0.98)
    bb_mid   = _safe_float(last.get("BBM_20_2.0"), close)
    bb_range = bb_upper - bb_lower
    bb_pct   = (close - bb_lower) / bb_range if bb_range > 0 else 0.5
    bb_width = bb_range / bb_mid if bb_mid > 0 else 0.0

    # 75-day high/low proxy (same as technical_agent — uses full window passed in)
    high_75d = float(df["high"].max()) if "high" in df.columns else close
    low_75d  = float(df["low"].min())  if "low"  in df.columns else close
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
        "above_ema20": bool(close > ema20),
        "above_ema50": bool(close > ema50),
        # Bollinger Bands
        "bb_upper": round(bb_upper, 2),
        "bb_lower": round(bb_lower, 2),
        "bb_pct": round(bb_pct, 3),
        "bb_width": round(bb_width, 4),
        # 52-week proxy (75-day window)
        "high_75d": round(high_75d, 2),
        "low_75d": round(low_75d, 2),
        "pct_from_high": round(pct_from_high, 2),
        "pct_from_low": round(pct_from_low, 2),
        # PCR — not available historically; caller treats None as neutral
        "pcr": None,
        # ATR(14) — absolute ATR in rupees; used for adaptive stop/target sizing
        # pandas_ta names the column ATRr_14 (older) or ATR_14 (newer) — try both
        "atr14": round(_safe_float(last.get("ATRr_14") or last.get("ATR_14"), 0.0), 2),
    }


def derive_sector_scores(
    indicators: dict[str, dict[str, Any]],
    sector_stocks: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Derive sector bullish/bearish scores from pre-computed indicators.

    Historically computable proxy for the production sector_agent. Counts
    what fraction of a sector's stocks are currently above their EMA50 and
    returns sector dicts in the exact format expected by
    signal_replay.compute_signal_score().

    Why this works:
        The live sector_agent scores sectors using the same EMA-based
        technical analysis. % stocks above EMA50 is a reliable, lag-free
        proxy that requires only OHLCV data already in cache.

    Threshold: ≥ 60% above EMA50 → "bullish" (score ≥ 60) → +12 pts.
               ≤ 40% above EMA50 → "bearish".
               Between 40%–60%    → "neutral" (0 pts, no penalty either).

    Args:
        indicators:    Output of compute_indicators_batch() for the current day.
                       Must contain "above_ema50" key per symbol.
        sector_stocks: SECTOR_STOCKS dict {sector_name: [symbol, ...]}

    Returns:
        [{"name": sector, "direction": "bullish"|"bearish"|"neutral", "score": 0–100}]
        Only sectors with ≥ 3 available symbols are included (below that the
        sample is too small to be meaningful).
    """
    results: list[dict[str, Any]] = []

    for sector, symbols in sector_stocks.items():
        available = [sym for sym in symbols if sym in indicators]
        if len(available) < 3:
            # Too few data points — skip rather than produce noisy signal
            continue

        above = sum(1 for sym in available if indicators[sym].get("above_ema50", False))
        pct = above / len(available)
        score = round(pct * 100)

        if pct >= 0.6:
            direction = "bullish"
        elif pct <= 0.4:
            direction = "bearish"
        else:
            direction = "neutral"

        results.append({"name": sector, "direction": direction, "score": score})

    return results


def compute_indicators_batch(
    symbols_ohlcv: dict[str, pd.DataFrame],
    as_of_date: pd.Timestamp,
    lookback_days: int = 90,
) -> dict[str, dict[str, Any]]:
    """Compute indicators for all symbols as of a single backtest date.

    Args:
        symbols_ohlcv:  {symbol: full_history_df} — the full cached OHLCV per symbol.
        as_of_date:     The simulation date ("today" in the backtest).
        lookback_days:  How many calendar days of history to pass to compute_indicators.
                        Default 90 matches production's 75d fetch with some buffer.

    Returns:
        {symbol: indicator_dict}  — symbols with insufficient data are omitted.
    """
    cutoff = as_of_date - pd.Timedelta(days=lookback_days)
    results: dict[str, dict[str, Any]] = {}

    for symbol, full_df in symbols_ohlcv.items():
        # Slice the trailing window that production would have fetched
        window = full_df.loc[
            (full_df.index >= cutoff) & (full_df.index <= as_of_date)
        ].copy()

        ind = compute_indicators(symbol, window, as_of_date=as_of_date)
        if ind and not ind.get("stale"):
            results[symbol] = ind

    return results
