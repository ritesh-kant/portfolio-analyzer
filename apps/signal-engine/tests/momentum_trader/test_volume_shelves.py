"""Volume-by-price shelves: the level type pivots are blind to.

The case these exist for is a vertical run. Every bar of the run makes a higher
high, so none of them is a local maximum, and the single impulse bar then
blinds the pivot detector for PIVOT_K bars either side - which is exactly where
the supply that stopped the run sits. See
research/hypotheses/2026-09-16-volume-shelf-levels.md.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.momentum_trader import levels

IST = "Asia/Kolkata"
COLS = ["open", "high", "low", "close", "volume"]


def _bars(rows, start: str = "2026-09-16 09:15", freq: str = "1min") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz=IST)
    return pd.DataFrame(rows, index=idx, columns=COLS)


def _spike_then_shelf() -> pd.DataFrame:
    """A quiet base, a vertical run topping at 105, then repeated trade at 101.

    This is the EIHOTEL 2026-09-16 shape. The run's top IS a pivot high - one
    bar, one print, correctly weak evidence. What no pivot can see is the 101
    band: the bars that traded through it on the way up and back down are
    inside PIVOT_K of the 105 bar, so the spike wins their windows, and the
    bars that revisit it later share one flat high. A human draws resistance
    at 101. The pivot detector draws nothing there.
    """
    rows = [(100.0, 100.2, 99.8, 100.0, 500.0) for _ in range(20)]
    rows += [(100.2, 101.2, 100.2, 101.1, 8_000.0),       # up through the band
             (101.1, 102.6, 101.0, 102.5, 9_000.0),
             (102.5, 105.0, 102.4, 104.6, 40_000.0),      # the impulse top
             (104.6, 104.7, 102.6, 102.7, 12_000.0),      # and back down
             (102.7, 102.8, 101.0, 101.1, 9_000.0)]
    rows += [(101.0, 101.2, 100.8, 101.0, 6_000.0) for _ in range(12)]
    return _bars(rows)


def test_shelf_finds_supply_a_pivot_cannot_see() -> None:
    bars = _spike_then_shelf()
    highs, _ = levels.swing_pivots(bars)
    assert any(price > 104.0 for price, _ in highs), (
        "the 105 top is a pivot - one print, correctly weak evidence"
    )
    assert not any(100.5 <= price <= 101.5 for price, _ in highs), (
        "no pivot can form in the 101 band: the impulse bar wins the windows "
        "of the bars that traded through it"
    )
    shelves = levels.volume_shelf_levels(bars)
    assert any(100.5 <= x.price <= 101.5 for x in shelves), (
        "the shelf detector must see the band the pivots are blind to"
    )


def test_shelf_ignores_a_single_print_wick() -> None:
    """The 105 tip carries one bar's volume but almost none of it traded there.

    This is the EIHOTEL 293.50 case: crediting a bar's whole volume to its wick
    tip is what makes a single candle look like a level.
    """
    shelves = levels.volume_shelf_levels(_spike_then_shelf())
    assert not any(x.price > 104.0 for x in shelves)


def test_volume_by_price_spreads_a_bar_over_its_range() -> None:
    bars = _bars([(100.0, 104.0, 100.0, 103.0, 1_000.0)])
    profile = levels.volume_by_price(bars, bucket=1.0)
    assert profile.sum() == pytest.approx(1_000.0)
    # a 4-wide bar over 1-wide buckets puts a quarter of its volume in each
    assert profile.max() == pytest.approx(250.0)


def test_volume_by_price_handles_a_zero_range_bar() -> None:
    bars = _bars([(100.0, 100.0, 100.0, 100.0, 700.0)])
    profile = levels.volume_by_price(bars, bucket=0.5)
    assert profile.sum() == pytest.approx(700.0)


def test_shelf_bucket_scales_with_atr_and_has_a_floor() -> None:
    bars = _spike_then_shelf()
    assert levels._shelf_bucket(bars, atr=2.0) == pytest.approx(2.0)
    assert levels._shelf_bucket(bars, atr=0.001) == levels.SHELF_BUCKET_MIN
    assert levels._shelf_bucket(bars, atr=None) == pytest.approx(
        round(float(bars["close"].iloc[-1]) * levels.SHELF_BUCKET_FALLBACK_PCT / 100.0, 2)
    )


def test_shelves_are_structural_so_they_can_block_an_entry() -> None:
    shelves = levels.volume_shelf_levels(_spike_then_shelf())
    assert shelves and all(levels.is_structural(x) for x in shelves)


def test_derive_levels_leaves_the_level_set_unchanged_by_default() -> None:
    bars = _spike_then_shelf()
    assert levels.derive_levels(bars) == levels.derive_levels(bars, add_shelves=False)
    assert not any(x.kind == "shelf" for x in levels.derive_levels(bars))


def test_derive_levels_only_adds_when_asked() -> None:
    bars = _spike_then_shelf()
    base = levels.derive_levels(bars)
    with_shelves = levels.derive_levels(bars, add_shelves=True)
    assert len(with_shelves) > len(base)
    assert base == [x for x in with_shelves if x.kind != "shelf"]


def test_shelf_is_look_ahead_safe() -> None:
    """What the detector reports at bar N cannot depend on bars after N."""
    bars = _spike_then_shelf()
    cut = len(bars) - 5
    seen_then = levels.volume_shelf_levels(bars.iloc[:cut])
    assert seen_then, "the fixture must produce a shelf before the cut"
    future = bars.iloc[:cut].copy()
    tail = _bars([(130.0, 140.0, 129.0, 139.0, 500_000.0)] * 5,
                 start="2026-09-16 14:00")
    assert levels.volume_shelf_levels(pd.concat([future, tail]).iloc[:cut]) == seen_then


def test_empty_frame_returns_no_shelves() -> None:
    empty = pd.DataFrame(columns=COLS).astype(float)
    empty.index = pd.DatetimeIndex([], tz=IST)
    assert levels.volume_shelf_levels(empty) == []
    assert levels.derive_levels(empty, add_shelves=True) == []


# --- engine wiring ----------------------------------------------------------

from src.momentum_trader import engine as eng  # noqa: E402


def test_volume_shelf_levels_is_off_by_default() -> None:
    """Shelves change which trades exist, so every recorded run is untouched."""
    assert eng.EngineConfig().volume_shelf_levels is False


def test_engine_asks_for_shelves_only_when_the_flag_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[bool] = []

    def spy(*_args: object, **kwargs: object) -> list:
        seen.append(bool(kwargs.get("add_shelves")))
        return [levels.Level(102.0, "pivot_high", 2, 0.0, 1.0)]

    monkeypatch.setattr(eng, "derive_levels", spy)
    monkeypatch.setattr(eng, "resample_5m", lambda bars: bars.iloc[:5])
    bars = _engine_confirmation_bars()

    eng._resistance_aware_attention_confirmation(bars, 2.5, None, v2=True)
    eng._resistance_aware_attention_confirmation(bars, 2.5, None, v2=True, shelves=True)

    assert seen == [False, True]


def _engine_confirmation_bars() -> pd.DataFrame:
    """Prelude plus a confirming minute that closes through 102.0."""
    rows = [(100.0, 100.4, 99.9, 100.3, 400.0) for _ in range(20)]
    bars = _bars(rows)
    confirmation = pd.DataFrame(
        [(101.70, 102.30, 101.60, 102.20, 1200.0)],
        index=pd.DatetimeIndex([bars.index[-1] + pd.Timedelta(minutes=1)]),
        columns=COLS,
    )
    return pd.concat([bars, confirmation])
