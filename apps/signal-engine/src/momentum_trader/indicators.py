"""Indicators used by the Warrior-style setups. Pure pandas, no I/O.

Bar frame contract (used by every function in this package):
    index  : tz-aware DatetimeIndex, one row per bar, ascending
    columns: open, high, low, close, volume (float)
Bars are *closed* bars. The last row is the most recently completed bar.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import pandas as pd

REQUIRED_COLS = ("open", "high", "low", "close", "volume")

# The short side (short_side.py) runs this package on a REFLECTED tape,
# p' = 2K - p. Every indicator here is linear in price, so the reflection is
# exact for them. The round-number grid is not: it only means something on real
# prices. ₹300 reflected about K = 312.40 is ₹324.80, a price nobody has an
# order resting at. While a reflection is active `round_levels_above` answers in
# real prices - the next marks BELOW the real price, where a short's buyers
# rest - and maps them back. Unset, which every long path is, nothing changes.
_REFLECT_K: ContextVar[float | None] = ContextVar("momentum_reflect_k", default=None)


@contextmanager
def reflected(k: float) -> Iterator[None]:
    """Mark the enclosed engine calls as running on the tape p' = 2k - p."""
    token = _REFLECT_K.set(k)
    try:
        yield
    finally:
        _REFLECT_K.reset(token)


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

    Inside `reflected(k)` the grid is the REAL one: the marks strictly below
    the real price 2k - price, reflected back so the caller still receives
    "the next levels above" in its own frame.
    """
    k = _REFLECT_K.get()
    if k is not None:
        real = round(2.0 * k - price, 6)
        minor, major = _round_steps(real)

        def _below(step: float) -> float:
            return 2.0 * k - float((math.ceil(real / step) - 1) * step)

        return _below(minor), _below(major)

    minor, major = _round_steps(price)

    def _next(step: float) -> float:
        n = (price // step + 1) * step
        return float(n)

    return _next(minor), _next(major)


def _round_steps(price: float) -> tuple[float, float]:
    if price < 100:
        return 5.0, 10.0
    if price < 1000:
        return 10.0, 50.0
    return 50.0, 100.0


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


def volume_ratio(
    bars: pd.DataFrame, lookback: int = 20, min_periods: int | None = None
) -> pd.Series:
    """Each bar's volume divided by the average of the `lookback` bars before it.

    This is bar-level relative volume, distinct from the day-level RVOL used to
    pick candidates. A value above ~2.5 on a red bar is distribution: sellers
    are hitting the bid in size.

    `min_periods` is how many prior bars must exist before a ratio is produced;
    it defaults to `max(3, lookback // 2)`, which on the 1-minute frame means
    the first reading of a session appears at 09:25. That blindness is why the
    volume test could never pass inside the guide's peak-volatility window, so
    a caller that trades the open may lower it — to 3, the floor this very
    expression already contains, not to a new number. It cannot be raised
    above `lookback`.
    """
    validate_bars(bars)
    floor = max(3, lookback // 2) if min_periods is None else min(int(min_periods), lookback)
    avg = bars["volume"].rolling(lookback, min_periods=max(1, floor)).mean().shift(1)
    return bars["volume"] / avg.replace(0.0, float("nan"))


def price_volume_slopes(
    bars: pd.DataFrame, lookback: int = 4
) -> tuple[float, float] | None:
    """Normalized least-squares price and volume slopes over completed bars.

    This is the literal four-bar interpretation of the common price/volume
    quadrant graphic: positive/positive confirms an advance, while a positive
    price slope with a negative volume slope is weakening participation.  The
    result is descriptive, not signed buy/sell volume; OHLCV has no aggressor
    side.  Both values are expressed per bar relative to the window mean so the
    signs are comparable across symbols with different prices and liquidity.
    """
    validate_bars(bars)
    if lookback < 2 or bars.empty:
        return None
    # A short intraday slope must never bridge the overnight/session boundary.
    # Callers such as review replays may carry prior-session warm-up bars even
    # though the live bar builder normally supplies only today's session.
    session = bars[bars.index.normalize() == bars.index[-1].normalize()]
    if len(session) < lookback:
        return None
    window = session.iloc[-lookback:]
    price_mean = float(window["close"].mean())
    volume_mean = float(window["volume"].mean())
    if price_mean <= 0.0 or volume_mean <= 0.0:
        return None
    x = pd.Series(range(lookback), dtype=float)
    x -= float(x.mean())
    denom = float((x * x).sum())
    price = window["close"].astype(float).reset_index(drop=True) / price_mean
    volume = window["volume"].astype(float).reset_index(drop=True) / volume_mean
    return float((x * price).sum() / denom), float((x * volume).sum() / denom)
