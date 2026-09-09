"""Re-entry tests (research/hypotheses/2026-09-06-multi-entry-same-stock.md).

`one_trade_per_day` already existed; these pin down that flipping it does what
the hypothesis says and nothing else — in particular that re-entry never
overlaps an open position, never happens on the bar a trade closed, and does not
disturb the trade the single-entry arm would have taken.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.momentum_trader import engine as eng

IST = "Asia/Kolkata"

# A stock that runs, pauses, breaks out again and gives some of it back, over
# and over — the shape that yields repeated setups. Values are chosen so the
# whole session stays inside the engine's 4–8% day-change band; drift outside it
# silently stops the scan and the fixture then produces no trades at all.
PREV_CLOSE = 100.0
START = 104.6
CYCLES = 14
BREAK_UP = 0.45
FADE_BARS, FADE_STEP = 4, 0.14


def _session() -> tuple[pd.DataFrame, float, pd.Series]:
    rows: list[tuple[float, float, float, float, float]] = []
    px = START

    def add(o: float, h: float, lo: float, c: float, v: float) -> None:
        rows.append((o, h, lo, c, v))

    for _ in range(6):                                    # quiet opening
        add(px, px + 0.05, px - 0.05, px + 0.01, 4000.0)
        px += 0.01
    for _ in range(CYCLES):
        p = px
        add(p, p + 0.15, p - 0.03, p + 0.13, 20000.0)                  # green
        add(p + 0.13, p + 0.30, p + 0.11, p + 0.28, 22000.0)           # green
        add(p + 0.28, p + 0.29, p - 0.09, p - 0.05, 5000.0)            # red pause
        add(p - 0.05, p + BREAK_UP, p - 0.08, p + BREAK_UP - 0.05, 30000.0)   # break
        px = p + BREAK_UP - 0.05
        for _ in range(FADE_BARS):                        # give some back
            add(px, px + 0.04, px - FADE_STEP - 0.02, px - FADE_STEP, 6000.0)
            px -= FADE_STEP

    idx = pd.DatetimeIndex(
        [pd.Timestamp("2024-06-03 09:15", tz=IST) + pd.Timedelta(minutes=i)
         for i in range(len(rows))]
    )
    bars = pd.DataFrame(
        [{"open": o, "high": h, "low": lo, "close": c, "volume": v} for o, h, lo, c, v in rows],
        index=idx,
    )
    profile = pd.Series(
        [bars["volume"].iloc[: i + 1].sum() / 4.0 for i in range(len(bars))],
        index=[ts.time() for ts in bars.index],
    )
    return bars, PREV_CLOSE, profile


def _run(one_per_day: bool) -> list[eng.ClosedTrade]:
    bars, prev_close, profile = _session()
    cfg = eng.EngineConfig(stress_slip=0.0, one_trade_per_day=one_per_day)
    st = eng.run_day("TEST", bars, prev_close, profile, cfg, lambda _s, _t: (0, ""))
    return sorted(st.closed, key=lambda t: t.entry_time)


def test_fixture_stays_inside_the_day_change_band() -> None:
    """Guards the fixture itself: outside 4–8% the scanner stops looking and
    every assertion below would pass vacuously on zero trades."""
    bars, prev_close, _ = _session()
    cfg = eng.EngineConfig()
    chg = (bars["close"] / prev_close - 1.0) * 100.0
    assert chg.max() <= cfg.day_chg_max
    assert (chg >= cfg.day_chg_min).any()


def test_default_is_still_one_trade_per_day() -> None:
    assert eng.EngineConfig().one_trade_per_day is True


def test_single_arm_takes_exactly_one_trade() -> None:
    assert len(_run(one_per_day=True)) == 1


def test_multi_arm_takes_several() -> None:
    assert len(_run(one_per_day=False)) > 1


def test_multi_arm_is_a_superset_starting_with_the_same_trade() -> None:
    """Re-entry must ADD trades, not change the one we already took."""
    single, multi = _run(one_per_day=True), _run(one_per_day=False)
    assert len(multi) > len(single)
    assert multi[0].entry_time == single[0].entry_time
    assert multi[0].entry == pytest.approx(single[0].entry)
    assert multi[0].exit_time == single[0].exit_time
    assert multi[0].exit_reason == single[0].exit_reason


def test_positions_never_overlap() -> None:
    closed = _run(one_per_day=False)
    for prev, nxt in zip(closed, closed[1:], strict=False):
        assert nxt.entry_time > prev.exit_time


def test_no_re_entry_on_the_bar_a_trade_closed() -> None:
    """step() returns after closing a position, so the earliest a re-entry can
    fill is a later bar."""
    closed = _run(one_per_day=False)
    assert all(n.entry_time != p.exit_time for p, n in zip(closed, closed[1:], strict=False))


def test_entry_cutoff_still_applies_to_re_entries() -> None:
    cutoff = eng.EngineConfig().entry_cutoff
    assert all(t.entry_time.time() < cutoff for t in _run(one_per_day=False))
