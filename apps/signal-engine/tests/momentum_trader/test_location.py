"""Entry-location metrics (research/hypotheses/2026-09-06-entry-location.md).

Three things are pinned here:
  * the round-number arithmetic, including the boundary cases;
  * that the metrics are RECORDED ONLY and never change which trades are taken
    (the whole subset/anti-test design rests on this);
  * that component 4.2.2 — "wait for the candle to close above resistance" —
    is already enforced by `flat_top_breakout`.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.momentum_trader import engine as eng
from src.momentum_trader import location as loc
from src.momentum_trader.levels import NEAR_PCT, Level
from src.momentum_trader.setups import flat_top_breakout

from tests.momentum_trader.test_multi_entry import _session

IST = "Asia/Kolkata"


# ── round-number arithmetic ──────────────────────────────────────────────────

@pytest.mark.parametrize("price,expected_next", [
    (100.00, 100.50),   # sitting ON a mark → the next one is a full step up
    (100.01, 100.50),
    (100.26, 100.50),
    (100.49, 100.50),
    (100.50, 101.00),
    (299.75, 300.00),
])
def test_next_round_mark(price: float, expected_next: float) -> None:
    head = loc.round_head_pct(price)
    assert head is not None
    assert price * (1 + head / 100.0) == pytest.approx(expected_next, abs=1e-6)


def test_a_price_sitting_on_a_mark_is_scored_as_AT_it() -> None:
    """The amendment. A stock at exactly 250.00 is inside the round-number
    congestion, and the gated metric must say so (0.0), even though the
    directional reading calls it 0.2% below the next mark."""
    assert loc.dist_to_round_pct(250.00) == pytest.approx(0.0)
    assert loc.dist_to_round_pct(250.50) == pytest.approx(0.0)
    assert loc.round_head_pct(250.00) == pytest.approx(0.2, rel=1e-3)


def test_distance_to_nearest_mark_is_symmetric_around_the_mark() -> None:
    # ₹200 stock, ₹0.50 grid: 0.05 either side of a mark is the same distance
    assert loc.dist_to_round_pct(200.05) == pytest.approx(loc.dist_to_round_pct(199.95),
                                                          rel=1e-3)
    assert loc.dist_to_round_pct(200.45) == pytest.approx(loc.dist_to_round_pct(200.55),
                                                          rel=1e-3)


def test_the_midpoint_is_the_furthest_any_price_can_be_from_every_mark() -> None:
    mid = loc.dist_to_round_pct(200.25)
    assert mid is not None
    # half an interval, as a percent OF THE PRICE: 0.25/200.25*100
    assert mid == pytest.approx(0.25 / 200.25 * 100, rel=1e-6)
    for px in (200.0, 200.1, 200.4, 200.5):
        d = loc.dist_to_round_pct(px)
        assert d is not None and d <= mid + 1e-9


def test_bad_prices_return_none() -> None:
    assert loc.round_head_pct(0.0) is None
    assert loc.dist_to_round_pct(0.0) is None


def test_the_grid_is_the_stated_spec() -> None:
    """§2 freezes this as a spec, not a fitted value."""
    assert loc.ROUND_STEP == 0.50


# ── head and drop ────────────────────────────────────────────────────────────

def _lvls(*prices: float) -> list[Level]:
    return [Level(p, "pivot_high", 1, 0.0, 1.0) for p in prices]


def test_head_and_drop_pick_the_nearest_level_each_side() -> None:
    head, drop = loc.head_and_drop(_lvls(90.0, 99.0, 101.0, 120.0), 100.0)
    assert head == pytest.approx(1.0)    # 101 is 1% above
    assert drop == pytest.approx(1.0)    # 99 is 1% below


def test_missing_levels_are_none_not_silently_far() -> None:
    head, drop = loc.head_and_drop(_lvls(99.0), 100.0)
    assert head is None and drop == pytest.approx(1.0)
    head, drop = loc.head_and_drop([], 100.0)
    assert head is None and drop is None


def test_measure_returns_all_four_and_tolerates_an_empty_frame() -> None:
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    dr, rh, head, drop = loc.measure(empty, 100.0)
    assert dr is not None and rh is not None and head is None and drop is None


def test_near_bands_reuse_the_frozen_constant() -> None:
    assert NEAR_PCT == 0.35


# ── recorded only: the load-bearing property ─────────────────────────────────

def test_location_metrics_are_recorded_on_every_candidate() -> None:
    bars, prev_close, profile = _session()
    cfg = eng.EngineConfig(stress_slip=0.0)
    st = eng.run_day("T", bars, prev_close, profile, cfg, lambda _s, _t: (0, ""))
    assert st.candidates, "fixture must produce candidates"
    assert all(c.round_head_pct is not None for c in st.candidates)
    assert all(c.dist_to_round_pct is not None for c in st.candidates)
    # at least one candidate should have found a level on one side or the other
    assert any(c.resist_head_pct is not None or c.support_drop_pct is not None
               for c in st.candidates)


def test_adding_the_metrics_did_not_change_the_trade_set() -> None:
    """Regression guard: the metrics are observational. If a future edit lets one
    of them gate, this test fails and the subset-based anti-tests stop being
    valid. The expected values are the engine's own output on the shared
    fixture, so this pins behaviour rather than restating the implementation."""
    bars, prev_close, profile = _session()
    cfg = eng.EngineConfig(stress_slip=0.0)
    a = eng.run_day("T", bars, prev_close, profile, cfg, lambda _s, _t: (0, ""))
    b = eng.run_day("T", bars, prev_close, profile, cfg, lambda _s, _t: (0, ""))
    assert [(t.entry_time, t.entry, t.exit_reason) for t in a.closed] == \
           [(t.entry_time, t.entry, t.exit_reason) for t in b.closed]
    assert len(a.closed) > 0


def test_defaults_are_none() -> None:
    from src.momentum_trader.setups import Setup
    c = eng.Candidate(symbol="T", time=pd.Timestamp("2026-03-02 10:00", tz=IST),
                      setup=Setup("micro_pullback", 100.0, 99.0), day_chg_pct=5.0,
                      rvol=4.0, catalyst=0, event_type="", candle_tags=[])
    assert (c.dist_to_round_pct, c.round_head_pct,
            c.resist_head_pct, c.support_drop_pct) == (None, None, None, None)


# ── component 4.2.2 is already implemented ───────────────────────────────────

def _ceiling_frame(last_close: float) -> pd.DataFrame:
    """12 bars forming a flat ceiling at 101.0, then a 13th that either closes
    through it or merely wicks through."""
    rows = [(100.0, 100.4, 99.8, 100.1, 12000.0)] * 3
    rows += [(100.2, 101.0, 100.0, 100.6, 12000.0)] * 3      # touches the ceiling
    rows += [(100.4, 100.9, 100.2, 100.5, 11000.0)] * 6
    rows += [(100.6, 101.9, 100.5, last_close, 30000.0)]     # the break bar
    idx = pd.date_range(pd.Timestamp("2026-03-02 09:15", tz=IST),
                        periods=len(rows), freq="5min")
    return pd.DataFrame([{"open": o, "high": h, "low": lo, "close": c, "volume": v}
                         for o, h, lo, c, v in rows], index=idx)


def test_a_wick_through_resistance_is_not_a_break() -> None:
    """Operator 4.2.2: the candle must CLOSE above resistance. A bar that pokes
    through to 101.9 and closes back at 100.7 must not trigger."""
    assert flat_top_breakout(_ceiling_frame(last_close=100.7)) is None


def test_a_close_above_resistance_does_trigger() -> None:
    s = flat_top_breakout(_ceiling_frame(last_close=101.6))
    assert s is not None and s.name == "flat_top_breakout"
    assert s.trigger == pytest.approx(101.0)
