"""Pullback-ordinal tests (research/hypotheses/2026-09-06-pullback-ordinal.md).

Two things must hold: the anchor is the day's FIRST sharp advance (not the
opening bell, not when the stock joined the watchlist), and the count never
looks ahead — a pivot high is only counted once the bars that confirm it have
printed.

Fixtures deliberately avoid perfectly flat filler. Identical highs tie, and
`swing_pivots` resolves a tie to the first bar of the plateau, so flat filler
manufactures pivots that no real chart would produce.
"""

from __future__ import annotations

import pandas as pd
from src.momentum_trader.levels import PIVOT_K
from src.momentum_trader.pullback import first_pole, pullback_ordinal
from src.momentum_trader.setups import POLE_MAX_BARS

IST = "Asia/Kolkata"

Row = tuple[float, float, float, float]


def _bars(rows: list[Row]) -> pd.DataFrame:
    """rows = (open, high, low, close); 5-min bars from 09:15."""
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2024-06-03 09:15", tz=IST) + pd.Timedelta(minutes=5 * i)
         for i in range(len(rows))]
    )
    return pd.DataFrame(
        [{"open": o, "high": h, "low": lo, "close": c, "volume": 1000.0} for o, h, lo, c in rows],
        index=idx,
    )


def _quiet(px: float, n: int) -> list[Row]:
    """Filler that drifts down a paisa a bar, so no two highs tie."""
    return [(px - i * 0.01, px + 0.05 - i * 0.01, px - 0.05 - i * 0.01, px - i * 0.01)
            for i in range(n)]


def _pole() -> list[Row]:
    """A run that only clears POLE_MIN_PCT on its THIRD bar and closes at its
    high, so the anchor is unambiguously that third bar."""
    return [(100.0, 100.5, 99.95, 100.45),    # +0.55% — not yet a pole
            (100.45, 101.0, 100.4, 100.95),   # +1.05% — still not
            (100.95, 102.1, 100.9, 102.0)]    # +2.15% from the low, closes at the top


def _swing_high(px: float) -> list[Row]:
    """A peak with PIVOT_K lower bars either side — one confirmed pivot high."""
    low = [(px - 1.0, px - 0.8, px - 1.2, px - 0.9)] * PIVOT_K
    peak = [(px - 0.5, px, px - 0.6, px - 0.1)]
    return low + peak + low


# ── the anchor ───────────────────────────────────────────────────────────────

def test_no_pole_means_no_anchor() -> None:
    assert first_pole(_bars(_quiet(100.0, 20))) is None


def test_pole_is_found_and_anchored_at_its_last_bar() -> None:
    bars = _bars(_quiet(100.0, 4) + _pole() + _quiet(101.5, 6))
    assert first_pole(bars) == bars.index[6]        # 4 quiet + 3 pole bars → index 6


def test_the_FIRST_pole_wins_not_a_later_one() -> None:
    """The operator's requirement: count from the day's first big move up."""
    bars = _bars(_quiet(100.0, 2) + _pole() + _quiet(101.5, 8) + _pole() + _quiet(101.5, 4))
    assert first_pole(bars) == bars.index[4]        # 2 quiet + 3 pole bars → index 4


def test_a_drift_that_never_runs_2pct_in_6_bars_is_not_a_pole() -> None:
    slow = [(100.0 + i * 0.2, 100.0 + i * 0.2 + 0.05, 100.0 + i * 0.2 - 0.05, 100.0 + i * 0.2)
            for i in range(20)]
    assert first_pole(_bars(slow)) is None


def test_a_run_that_closes_off_its_low_end_is_not_a_pole() -> None:
    """Range is big enough but the last bar gives it all back — a spike and fade,
    not an advance."""
    faded = [(100.0, 101.2, 99.9, 101.1), (101.1, 103.5, 101.0, 101.2),
             (101.2, 101.4, 100.0, 100.1)]
    assert first_pole(_bars(_quiet(100.0, 2) + faded)) is None


# ── the count ────────────────────────────────────────────────────────────────

def test_ordinal_is_none_without_an_anchor() -> None:
    assert pullback_ordinal(_bars(_quiet(100.0, 20))) is None


def test_first_pullback_is_ordinal_1() -> None:
    bars = _bars(_quiet(100.0, 2) + _pole() + _quiet(101.5, 4))
    assert pullback_ordinal(bars) == 1


def test_each_confirmed_swing_high_advances_the_count() -> None:
    base = _quiet(100.0, 2) + _pole()
    assert pullback_ordinal(_bars(base + _swing_high(104.0))) == 2
    assert pullback_ordinal(_bars(base + _swing_high(104.0) + _swing_high(105.0))) == 3


def test_an_unconfirmed_peak_is_not_counted_yet() -> None:
    """A peak with fewer than PIVOT_K bars after it cannot be a pivot, so a live
    scan cannot count a high that has not yet been confirmed."""
    base = _quiet(100.0, 2) + _pole()
    peak = [(103.5, 104.0, 103.4, 103.6)]
    assert pullback_ordinal(_bars(base + peak + _quiet(103.0, PIVOT_K - 1))) == 1
    # once the confirming bars print, the same peak counts
    assert pullback_ordinal(_bars(base + peak + _quiet(103.0, PIVOT_K + 1))) == 2


def test_pivots_before_the_anchor_are_not_counted() -> None:
    """A stock that chopped up and down before its first sharp advance is still
    on pullback 1 when the advance happens.

    The quiet run between the chop and the pole is POLE_MAX_BARS long so that no
    ≤6-bar window can span the chop's lows and the pole's high, which would
    anchor the pole earlier than intended.
    """
    noisy = _swing_high(99.0) + _swing_high(99.5)
    bars = _bars(noisy + _quiet(100.0, POLE_MAX_BARS) + _pole() + _quiet(101.5, 4))
    assert first_pole(bars) == bars.index[len(noisy) + POLE_MAX_BARS + 2]
    assert pullback_ordinal(bars) == 1


def test_explicit_anchor_overrides_the_search() -> None:
    bars = _bars(_quiet(100.0, 2) + _pole() + _swing_high(104.0))
    assert pullback_ordinal(bars, anchor=bars.index[-1]) == 1
