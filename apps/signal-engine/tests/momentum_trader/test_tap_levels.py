"""Repeated-rejection ("tap") resistance: the ceiling a pivot cannot see.

LATENTVIEW 2024-03-28: 5-minute highs 486.95 / 487.20 / 487.20 at 11:05-11:25,
all under a 494 spike at 11:00. A pivot needs to beat 3 bars either side, so
none qualified and the engine picked the round 490. See
research/hypotheses/2026-10-08-tap-high-levels.md.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.momentum_trader import levels

IST = "Asia/Kolkata"
COLS = ["open", "high", "low", "close", "volume"]


def _bars(rows) -> pd.DataFrame:
    idx = pd.date_range("2024-03-28 10:50", periods=len(rows), freq="5min", tz=IST)
    return pd.DataFrame(rows, index=idx, columns=COLS)


def _filler(n: int, top: float) -> list[tuple[float, float, float, float, float]]:
    """Quiet bars whose highs are all different, so none is a tap of another."""
    return [(top - 3 - i, top - i, top - 4 - i, top - 3 - i, 100.0) for i in range(n)]


def _triple_top_under_spike() -> pd.DataFrame:
    rows = [
        (470, 471.0, 468, 470, 1000),    # 10:50
        (471, 494.0, 470, 486, 9000),    # 10:55 spike
        (486, 486.95, 483, 484, 5000),   # tap 1
        (484, 483.5, 481, 482, 3000),
        (482, 487.20, 481, 485, 4000),   # tap 2
        (485, 483.6, 481, 482, 3000),
        (482, 487.20, 481, 484, 4000),   # tap 3
        (484, 485.7, 482, 483, 3000),
    ]
    return _bars(rows)


def test_pivot_test_cannot_see_the_triple_top_but_taps_can() -> None:
    bars = _triple_top_under_spike()
    pivots = [x for x in levels.derive_levels(bars, add_round=False)
              if x.kind == "pivot_high"]
    assert not any(abs(x.price - 487.2) < 0.5 for x in pivots)
    taps = levels.tap_high_levels(bars, min_touches=3)
    assert [(round(x.price, 2), x.touches, x.kind) for x in taps] == [
        (487.2, 3, "tap_high")]
    assert levels.is_structural(taps[0])


def test_derive_levels_is_unchanged_with_taps_off() -> None:
    bars = _triple_top_under_spike()
    assert (levels.derive_levels(bars, tap_min_touches=0)
            == levels.derive_levels(bars))
    on = levels.derive_levels(bars, tap_min_touches=3)
    assert [x for x in on if x.kind != "tap_high"] == levels.derive_levels(bars)
    assert sum(x.kind == "tap_high" for x in on) == 1


def test_adjacent_bars_are_one_push_not_two_taps() -> None:
    rows = _filler(3, 90.0)
    rows += [(100, 101.0, 99.5, 100.0, 100), (100, 101.0, 99.5, 100.2, 100)]
    rows += _filler(3, 80.0)
    assert levels.tap_high_levels(_bars(rows), min_touches=2) == []


def test_a_bar_that_closes_through_is_not_a_rejection() -> None:
    rows = _filler(3, 90.0)
    rows += [(100, 101.0, 99.5, 101.0, 100), (100, 100.5, 99.5, 100.0, 100),
             (100, 101.0, 99.5, 101.0, 100)]       # closes ON its high twice
    rows += _filler(3, 80.0)
    assert levels.tap_high_levels(_bars(rows), min_touches=2) == []


def test_two_touches_threshold_and_validation() -> None:
    bars = _triple_top_under_spike().iloc[:5]            # taps at 486.95 / 487.20
    two = levels.tap_high_levels(bars, min_touches=2)
    assert [(round(x.price, 2), x.touches) for x in two] == [(487.2, 2)]
    assert levels.tap_high_levels(bars, min_touches=3) == []
    assert levels.tap_high_levels(bars, min_touches=1) == []


def test_engine_config_validates_tap_setting() -> None:
    from src.momentum_trader.engine import EngineConfig
    assert EngineConfig().tap_levels_min_touches == 0
    EngineConfig(tap_levels_min_touches=3)
    with pytest.raises(ValueError):
        EngineConfig(tap_levels_min_touches=1)
