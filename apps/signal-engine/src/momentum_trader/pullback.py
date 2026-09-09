"""How deep into today's trend an entry sits — the "1st / 2nd / 3rd pullback".

Warrior's stated preference is the first and second pullback after a stock's
first sharp advance; by the fourth, the buyers who wanted in are in. To test
that we need a number on every entry saying which pullback it is.

The count is anchored to the day's **first big move up**, not to the opening
bell and not to the moment the stock joined our watchlist. That distinction is
the whole point: the scanner only starts looking once a stock is up 4% on 3×
volume, so a stock that rallied and pulled back four times before crossing that
line is on its fifth pullback while being the first one we ever saw.

Both definitions here are **reused unchanged** from code that was already frozen
before this module existed, so counting introduces no new tunable parameter:

  * the anchor is `setups.bull_flag`'s pole (POLE_MIN_PCT, POLE_MAX_BARS);
  * a pullback is `levels.swing_pivots`'s confirmed pivot high (PIVOT_K).

A pivot needs PIVOT_K bars to print after it before it is confirmed, so an
ordinal computed from bars up to now never looks ahead.

See research/hypotheses/2026-09-06-pullback-ordinal.md §0.
"""

from __future__ import annotations

import pandas as pd

from .indicators import validate_bars
from .levels import swing_pivot_positions
from .setups import POLE_MAX_BARS, POLE_MIN_PCT

# The pole's last bar must close in the top (1 - POLE_CLOSE_TOP) of its range.
# Same 0.6 constant bull_flag uses inline; named here, value unchanged.
POLE_CLOSE_TOP = 0.6


def first_pole(bars: pd.DataFrame) -> pd.Timestamp | None:
    """Timestamp of the last bar of the day's first sharp advance, or None.

    Scans forward for the earliest window of ≤ POLE_MAX_BARS bars whose
    high-to-low range is ≥ POLE_MIN_PCT and whose final bar closes in the top
    40% of that range — `bull_flag`'s pole test, applied to find the anchor
    rather than to trigger an entry.
    """
    validate_bars(bars)
    n = len(bars)
    if n < 2:
        return None
    high = bars["high"].to_numpy()
    low = bars["low"].to_numpy()
    close = bars["close"].to_numpy()
    # earliest END bar wins, so iterate the end forward and the start backward
    for end in range(1, n):
        start_min = max(0, end - POLE_MAX_BARS + 1)
        for start in range(start_min, end):
            w_high = float(high[start : end + 1].max())
            w_low = float(low[start : end + 1].min())
            if w_low <= 0:
                continue
            if (w_high / w_low - 1.0) * 100.0 < POLE_MIN_PCT:
                continue
            if float(close[end]) < w_low + POLE_CLOSE_TOP * (w_high - w_low):
                continue
            ts = bars.index[end]
            return pd.Timestamp(ts)
    return None


def pullback_ordinal(bars: pd.DataFrame, anchor: pd.Timestamp | None = None) -> int | None:
    """Which pullback of the day's move this bar sits on. None if no anchor yet.

    1 = the first pullback after the day's first sharp advance. Each confirmed
    swing pivot high after the anchor advances the count by one.
    """
    validate_bars(bars)
    if bars.empty:
        return None
    if anchor is None:
        anchor = first_pole(bars)
    if anchor is None:
        return None
    # Pivots are found on the WHOLE frame and then filtered by timestamp. Slicing
    # to post-anchor bars first would make a peak immediately after the anchor
    # uncountable, since it would have no bars before it inside the slice.
    highs, _ = swing_pivot_positions(bars)
    idx = bars.index
    return 1 + sum(1 for i in highs if idx[i] > anchor)
