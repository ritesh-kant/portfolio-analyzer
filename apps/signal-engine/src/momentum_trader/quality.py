"""Selectivity filters — trade rarely, only the good ones.

Three gates applied on top of the existing universe and setup rules
(research/hypotheses/2026-09-06-quality-selectivity.md §1):

  F1 uptrend      don't buy a stock that is falling. EMA9 > EMA20, price above
                  the session VWAP, and the stock not below its 20-day average
                  on the daily chart.
  F2 chart shape  some names barely trade. Their charts are mostly flat bars and
                  gaps, so every indicator computed on them is reading padding.
                  Measured over the PRIOR 20 sessions, so it is knowable before
                  the day starts.
  F3 surge        don't enter on a limp break. A pole must already have formed
                  today, and at least one bar in it must have traded on heavy
                  volume.

F1 and F3 reuse constants frozen elsewhere (`exits.EMA_FAST/EMA_SLOW`,
`exits.CLIMAX_VOL_RATIO`, `setups.POLE_MIN_PCT/POLE_MAX_BARS` via
`pullback.first_pole`). Only F2's two thresholds are new, and they come from the
measured distribution of chart quality across 495 cached stock-years rather than
from anything about returns — see MIN_MINUTE_COVERAGE below.

Every function here reads only bars up to and including the trigger bar.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .exits import CLIMAX_VOL_RATIO, EMA_FAST, EMA_SLOW
from .indicators import ema, session_vwap, validate_bars, volume_ratio
from .pullback import first_pole

# ── F2 thresholds — the only new tunable numbers in this hypothesis ───────────
# Set from the 2022-2023 cache (495 stock-years, measured 2026-09-06):
#   minute coverage  median 0.992, 10th pct 0.840
#   flat-bar share   median 0.059, 90th pct 0.309
# so these exclude roughly the worst decile on each measure. Chosen from the
# data-quality distribution BEFORE any P&L was computed on the filtered set.
MIN_MINUTE_COVERAGE = 0.85
MAX_FLAT_BAR_SHARE = 0.30
NSE_SESSION_MINUTES = 375
QUALITY_LOOKBACK_DAYS = 20


@dataclass(frozen=True)
class ChartQuality:
    """How well-formed a symbol's chart is. `ok` is the F2 verdict."""

    minute_coverage: float
    flat_bar_share: float
    sessions: int

    @property
    def ok(self) -> bool:
        return (self.minute_coverage >= MIN_MINUTE_COVERAGE
                and self.flat_bar_share <= MAX_FLAT_BAR_SHARE)

    @property
    def reason(self) -> str:
        if self.minute_coverage < MIN_MINUTE_COVERAGE:
            return "thin_tape"
        if self.flat_bar_share > MAX_FLAT_BAR_SHARE:
            return "flat_bars"
        return "ok"


def chart_quality(history_1m: pd.DataFrame,
                  lookback_days: int = QUALITY_LOOKBACK_DAYS) -> ChartQuality | None:
    """Median minute-coverage and flat-bar share over the last `lookback_days`
    PRIOR sessions. None when there is not enough history to judge.

    `history_1m` must contain only sessions strictly before the day being traded;
    the caller owns that slice, so this cannot see the future.
    """
    if history_1m.empty:
        return None
    validate_bars(history_1m)
    day = history_1m.index.normalize()
    days = sorted(set(day))[-lookback_days:]
    h = history_1m[day.isin(days)]
    if h.empty:
        return None
    hday = h.index.normalize()
    traded = (h["volume"] > 0).groupby(hday).sum() / float(NSE_SESSION_MINUTES)
    flat = (h["high"] == h["low"]).groupby(hday).mean()
    return ChartQuality(minute_coverage=float(traded.median()),
                        flat_bar_share=float(flat.median()),
                        sessions=len(days))


def uptrend_ok(bars_tf: pd.DataFrame, prev_close: float,
               daily_sma20: float | None) -> tuple[bool, str]:
    """F1. (ok, reason) — the stock must not be falling on any of three horizons."""
    if bars_tf.empty:
        return False, "no_bars"
    validate_bars(bars_tf)
    close = float(bars_tf["close"].iloc[-1])

    fast = ema(bars_tf["close"], EMA_FAST)
    slow = ema(bars_tf["close"], EMA_SLOW)
    if pd.isna(fast.iloc[-1]) or pd.isna(slow.iloc[-1]):
        return False, "ema_warmup"
    if float(fast.iloc[-1]) <= float(slow.iloc[-1]):
        return False, "ema_down"

    vw = session_vwap(bars_tf)
    if len(vw) and not pd.isna(vw.iloc[-1]) and close < float(vw.iloc[-1]):
        return False, "below_vwap"

    if daily_sma20 is not None and prev_close < daily_sma20:
        return False, "daily_downtrend"
    return True, "ok"


def surge_ok(bars_tf: pd.DataFrame) -> tuple[bool, str]:
    """F3. A pole must have formed today, with heavy volume behind it.

    `first_pole` supplies the "big move" definition already frozen for
    `bull_flag` and reused by the pullback ordinal; the volume test reuses the
    climax threshold from the exit rules. No new constants.
    """
    if bars_tf.empty:
        return False, "no_bars"
    anchor = first_pole(bars_tf)
    if anchor is None:
        return False, "no_surge"
    vr = volume_ratio(bars_tf)
    pole = bars_tf.index <= anchor
    heavy = vr[pole].dropna()
    if heavy.empty or float(heavy.max()) < CLIMAX_VOL_RATIO:
        return False, "surge_no_volume"
    return True, "ok"
