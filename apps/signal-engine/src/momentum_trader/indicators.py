"""Indicators used by the Warrior-style setups. Pure pandas, no I/O.

Bar frame contract (used by every function in this package):
    index  : tz-aware DatetimeIndex, one row per bar, ascending
    columns: open, high, low, close, volume (float)
Bars are *closed* bars. The last row is the most recently completed bar.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

REQUIRED_COLS = ("open", "high", "low", "close", "volume")


def validate_bars(bars: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLS if c not in bars.columns]
    if missing:
        raise ValueError(f"bars missing columns: {missing}")
    if not bars.index.is_monotonic_increasing:
        raise ValueError("bars index must be ascending")


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average. Warrior uses 9 and 20 on 5-min, 200 for trend."""
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def session_vwap(bars: pd.DataFrame) -> pd.Series:
    """Volume-weighted average price, reset at each session (calendar day).

    VWAP = cumulative(price × volume) / cumulative(volume) since the open.
    Institutions benchmark fills to it, which is why price tends to react there.
    """
    validate_bars(bars)
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    pv = typical * bars["volume"]
    day = bars.index.normalize()
    cum_pv = pv.groupby(day).cumsum()
    cum_v = bars["volume"].groupby(day).cumsum()
    return (cum_pv / cum_v.replace(0.0, float("nan"))).rename("vwap")


def cumulative_session_volume(bars: pd.DataFrame) -> pd.Series:
    """Running volume since the session open, per bar."""
    return bars["volume"].groupby(bars.index.normalize()).cumsum()


def relative_volume_by_time(
    today: pd.DataFrame, history: pd.DataFrame, lookback_days: int = 20
) -> float | None:
    """Time-of-day relative volume (RVOL).

    RVOL = today's cumulative volume so far ÷ the average cumulative volume at the
    same clock time over the last `lookback_days` sessions. This is the honest
    intraday version of Warrior's "5× relative volume": comparing today's 10:30
    volume against a full-day average would understate it ~4×.

    `today` = today's closed bars. `history` = prior sessions' bars (same
    interval). Returns None if there is no comparable history.
    """
    if today.empty or history.empty:
        return None
    validate_bars(today)
    validate_bars(history)
    now_t = today.index[-1].time()
    today_cum = float(today["volume"].sum())
    hist = history[history.index.time <= now_t]
    if hist.empty:
        return None
    per_day = hist["volume"].groupby(hist.index.normalize()).sum()
    per_day = per_day.iloc[-lookback_days:]
    if per_day.empty or per_day.mean() <= 0:
        return None
    return today_cum / float(per_day.mean())


def day_change_pct(bars: pd.DataFrame, prev_close: float) -> float:
    """% change of the latest close vs the prior session close."""
    if prev_close <= 0:
        raise ValueError("prev_close must be > 0")
    return (float(bars["close"].iloc[-1]) / prev_close - 1.0) * 100.0


def round_levels_above(price: float) -> tuple[float, float]:
    """NSE analogue of Warrior's "whole dollar / half dollar" levels.

    Traders anchor on round rupee figures; the grid scales with price:
        < ₹100   : ₹5 / ₹10
        < ₹1000  : ₹10 / ₹50
        else     : ₹50 / ₹100
    Returns (next_minor, next_major) strictly above `price`.
    """
    if price < 100:
        minor, major = 5.0, 10.0
    elif price < 1000:
        minor, major = 10.0, 50.0
    else:
        minor, major = 50.0, 100.0

    def _next(step: float) -> float:
        n = (price // step + 1) * step
        return float(n)

    return _next(minor), _next(major)


@dataclass(frozen=True)
class Macd:
    """MACD series. A dataclass rather than a DataFrame because `DataFrame.hist`
    is pandas' histogram-plot method, so `frame.hist` would silently return a
    function instead of the histogram column."""

    line: pd.Series
    signal: pd.Series
    hist: pd.Series


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> Macd:
    """MACD (Moving Average Convergence Divergence), conventional 12/26/9.

    Plain meaning: `line` is a fast average minus a slow average, so it is
    positive while short-term price is pulling ahead of the longer trend.
    `signal` is a smoothing of that line, and `hist` is line − signal. The
    histogram crossing from positive to negative is the standard "momentum is
    rolling over" reading — it turns before price does, which is why it is the
    natural trigger for an exit that does not wait for a fixed target.
    """
    f = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    s = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    line = f - s
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return Macd(line=line, signal=sig, hist=line - sig)


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range: the typical size of a bar, including gaps.

    Used to size tolerances in price terms rather than fixed percentages, so a
    quiet ₹80 stock and a volatile ₹1,500 stock get comparable treatment.
    """
    validate_bars(bars)
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - prev_close).abs(),
        (bars["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def volume_ratio(bars: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """Each bar's volume divided by the average of the `lookback` bars before it.

    This is bar-level relative volume, distinct from the day-level RVOL used to
    pick candidates. A value above ~2.5 on a red bar is distribution: sellers
    are hitting the bid in size.
    """
    validate_bars(bars)
    avg = bars["volume"].rolling(lookback, min_periods=max(3, lookback // 2)).mean().shift(1)
    return bars["volume"] / avg.replace(0.0, float("nan"))
