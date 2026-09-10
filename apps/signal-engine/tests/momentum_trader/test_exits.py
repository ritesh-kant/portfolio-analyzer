"""Tests for the indicator-driven exits, the derived support/resistance levels,
and the new indicators (MACD, ATR, bar-level volume ratio).

The central behaviours under test:
  * a fixed-target position is unaffected by any trend rule (baseline preserved);
  * a trend position is NOT closed by trend rules until it is in profit (armed);
  * a winner keeps running while it makes higher lows, and gives back only to
    the ratcheted stop rather than to a price chosen at entry.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.momentum_trader import exits, levels
from src.momentum_trader.indicators import atr, macd, volume_ratio

IST = "Asia/Kolkata"
Row = tuple[float, float, float, float, float]
COLS = ["open", "high", "low", "close", "volume"]


def _bars(rows: list[Row], start: str = "2026-09-07 09:15", freq: str = "5min") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz=IST)
    return pd.DataFrame(rows, index=idx, columns=COLS)


def _ramp(n: int, start: float, step: float, vol: float = 1000.0) -> list[Row]:
    out: list[Row] = []
    px = start
    for _ in range(n):
        out.append((px, px + step, px - step * 0.2, px + step * 0.9, vol))
        px += step
    return out


# ── new indicators ────────────────────────────────────────────────────────────

def test_macd_attribute_access_and_sign() -> None:
    up = pd.Series(np.linspace(100, 130, 80))
    m = macd(up)
    assert isinstance(m, exits.macd(up).__class__)
    assert m.hist.iloc[-1] > 0                      # rising series → positive histogram
    down = pd.Series(np.linspace(130, 100, 80))
    assert macd(down).hist.iloc[-1] < 0
    assert len(m.line) == len(up) and pd.isna(m.line.iloc[0])


def test_macd_histogram_crosses_when_trend_turns() -> None:
    s = pd.Series(list(np.linspace(100, 120, 60)) + list(np.linspace(120, 108, 40)))
    h = macd(s).hist.dropna()
    assert (h > 0).any() and (h < 0).any()
    crossings = ((h.shift(1) > 0) & (h <= 0)).sum()
    assert crossings >= 1


def test_atr_and_volume_ratio() -> None:
    b = _bars([(100, 102, 98, 101, 1000)] * 30)
    assert atr(b).iloc[-1] == pytest.approx(4.0, rel=0.05)
    spike = _bars([(100, 101, 99, 100, 1000)] * 25 + [(100, 101, 99, 100, 5000)])
    assert volume_ratio(spike).iloc[-1] == pytest.approx(5.0, rel=0.01)


# ── levels ────────────────────────────────────────────────────────────────────

def test_swing_pivots_finds_peaks_and_troughs() -> None:
    rows = [
        (100, 101, 99, 100, 100), (100, 102, 100, 101, 100), (101, 103, 100, 102, 100),
        (102, 108, 101, 107, 500),                                    # pivot high 108
        (107, 107, 105, 106, 100), (106, 106, 104, 105, 100), (105, 105, 103, 104, 100),
        (104, 104, 100, 101, 400),                                    # pivot low 100
        (101, 105, 101, 104, 100), (104, 106, 103, 105, 100), (105, 107, 104, 106, 100),
    ]
    highs, lows = levels.swing_pivots(_bars(rows), k=3)
    assert any(p == pytest.approx(108.0) for p, _ in highs)
    assert any(p == pytest.approx(100.0) for p, _ in lows)


def test_swing_pivots_never_uses_unconfirmed_recent_bars() -> None:
    """The last k bars cannot form a pivot, so no look-ahead."""
    rows = _ramp(20, 100, 0.5) + [(110, 120, 109, 119, 900)]   # huge final bar
    highs, _ = levels.swing_pivots(_bars(rows), k=3)
    assert all(p < 119 for p, _ in highs)


def test_cluster_merges_nearby_prices_and_weights_by_volume() -> None:
    pts = [(100.0, 1000.0), (100.2, 3000.0), (105.0, 500.0)]
    out = levels.cluster(pts, "pivot_high", tol_pct=0.5)
    assert len(out) == 2
    strong = out[0]
    assert strong.touches == 2
    assert strong.price == pytest.approx((100.0 * 1000 + 100.2 * 3000) / 4000)
    assert strong.strength > out[1].strength


def test_derive_levels_includes_anchors_and_finds_nearest() -> None:
    rows = _ramp(25, 100, 0.4)
    lv = levels.derive_levels(_bars(rows), prev_day={"high": 118.0, "low": 96.0, "close": 99.0},
                              orb={"high": 104.0, "low": 99.5})
    kinds = {x.kind for x in lv}
    assert {"prev_day", "orb", "round"} <= kinds
    price = 105.0
    res = levels.nearest_resistance(lv, price)
    sup = levels.nearest_support(lv, price)
    assert res is not None and res.price > price
    assert sup is not None and sup.price < price
    assert levels.nearest_resistance([], price) is None


def test_level_is_near() -> None:
    lv = levels.Level(200.0, "pivot_high", 3, 0.0, 3.0)
    assert lv.is_near(200.5, tol_pct=0.35)
    assert not lv.is_near(203.0, tol_pct=0.35)


def test_structural_resistance_requires_an_anchor_or_repeated_pivot() -> None:
    one_touch = levels.Level(105.0, "pivot_high", 1, 500.0, 1.0)
    repeated = levels.Level(106.0, "pivot_high", 2, 1000.0, 2.0)
    prior_high = levels.Level(107.0, "prev_day", 1, 0.0, 1.5)
    assert not levels.is_structural(one_touch)
    assert levels.is_structural(repeated) and levels.is_structural(prior_high)
    assert levels.nearest_structural_resistance([one_touch, repeated, prior_high], 100.0) == repeated


# ── exit config ───────────────────────────────────────────────────────────────

def test_modes_enable_the_right_rules() -> None:
    fixed = exits.ExitConfig.for_mode(exits.MODE_FIXED)
    assert fixed.has_target and not fixed.use_ema_fast_break and not fixed.use_swing_trail
    tmin = exits.ExitConfig.for_mode(exits.MODE_TREND_MIN)
    assert not tmin.has_target and tmin.use_ema_fast_break and tmin.use_swing_trail
    assert not tmin.use_macd_fade
    tfull = exits.ExitConfig.for_mode(exits.MODE_TREND_FULL)
    assert tfull.use_macd_fade and tfull.use_resistance_reject and tfull.use_volume_climax
    resistance_state = exits.ExitConfig.for_mode(exits.MODE_TREND_RESISTANCE_STATE)
    assert resistance_state.structural_resistance_only
    assert resistance_state.resistance_requires_failed_break
    with pytest.raises(ValueError):
        exits.ExitConfig.for_mode("nope")


# ── arming, breakeven, trailing ───────────────────────────────────────────────

def _state(entry: float = 100.0, stop: float = 99.0) -> exits.ExitState:
    return exits.initial_state(entry=entry, hard_stop=stop)


def test_trend_rules_are_disarmed_until_in_profit() -> None:
    cfg = exits.ExitConfig.for_mode(exits.MODE_TREND_MIN)
    st = _state()
    falling = _bars(_ramp(30, 100, -0.3))
    assert exits.check_trend(st, falling, None, cfg) is None      # not armed → no exit
    exits.update_high(st, 100.6, cfg)                             # +0.6R, arm threshold 0.5R
    assert st.armed
    assert exits.check_trend(st, falling, None, cfg) is not None


def test_breakeven_lock_at_1r() -> None:
    cfg = exits.ExitConfig.for_mode(exits.MODE_TREND_MIN)
    st = _state(entry=100.0, stop=99.0)
    exits.update_high(st, 100.5, cfg)
    assert st.trail == pytest.approx(99.0)      # 0.5R: armed but stop not yet moved
    exits.update_high(st, 101.0, cfg)
    assert st.trail == pytest.approx(100.0)     # 1R: stop lifted to entry
    exits.update_high(st, 100.2, cfg)
    assert st.trail == pytest.approx(100.0)     # never falls back


def test_swing_trail_ratchets_up_only() -> None:
    cfg = exits.ExitConfig.for_mode(exits.MODE_TREND_MIN)
    st = _state(entry=100.0, stop=99.0)
    # pivot low of 101.0 at index 4: it is the lowest of the 3 bars either side
    rows: list[Row] = [
        (103.0, 104.0, 102.0, 103.5, 100),
        (103.5, 104.5, 102.5, 104.0, 100),
        (104.0, 105.0, 103.0, 104.5, 100),
        (104.5, 105.0, 102.5, 103.0, 100),
        (103.0, 103.5, 101.0, 101.5, 100),      # ← pivot low 101.0
        (101.5, 103.5, 102.0, 103.2, 100),
        (103.2, 104.5, 103.0, 104.2, 100),
        (104.2, 105.5, 104.0, 105.2, 100),
    ]
    exits.update_trail(st, _bars(rows), cfg)
    expected = 101.0 * (1 - cfg.swing_buffer_pct / 100)
    assert st.trail == pytest.approx(expected, rel=1e-6)

    # a later, LOWER pivot low must not drag the stop back down
    rows2 = rows + [
        (105.2, 106.0, 104.5, 105.0, 100),
        (105.0, 105.5, 104.0, 104.5, 100),
        (104.5, 105.0, 100.2, 100.5, 100),      # ← lower pivot low 100.2
        (100.5, 102.0, 100.4, 101.8, 100),
        (101.8, 103.0, 101.0, 102.5, 100),
        (102.5, 104.0, 102.0, 103.5, 100),
    ]
    _, lows = levels.swing_pivots(_bars(rows2))
    assert lows[-1][0] == pytest.approx(100.2)   # the lower pivot is the most recent
    exits.update_trail(st, _bars(rows2), cfg)
    assert st.trail == pytest.approx(expected, rel=1e-6)   # ratchet held


def test_trail_never_placed_above_current_price() -> None:
    cfg = exits.ExitConfig.for_mode(exits.MODE_TREND_MIN)
    st = _state(entry=100.0, stop=99.0)
    rows = _ramp(8, 100, 1.0) + [(108, 108.2, 100.0, 100.2, 500)]   # collapse on the last bar
    exits.update_trail(st, _bars(rows), cfg)
    assert st.trail < 100.2


# ── stop / target / trend signals ─────────────────────────────────────────────

def test_check_stop_reports_hard_vs_trailed_and_gap_fill() -> None:
    st = _state(entry=100.0, stop=99.0)
    hit = pd.Series({"open": 99.5, "high": 99.6, "low": 98.9, "close": 99.0})
    sig = exits.check_stop(st, hit)
    assert sig is not None and sig.reason == "stop" and sig.price == pytest.approx(99.0)
    gap = pd.Series({"open": 98.0, "high": 98.2, "low": 97.5, "close": 97.8})
    assert exits.check_stop(st, gap).price == pytest.approx(98.0)   # gap fills worse
    st.trail = 100.5
    sig2 = exits.check_stop(st, pd.Series({"open": 101, "high": 101, "low": 100.4, "close": 100.6}))
    assert sig2 is not None and sig2.reason == "trail_stop"


def test_target_only_in_fixed_mode() -> None:
    bar = pd.Series({"open": 101.0, "high": 103.0, "low": 100.5, "close": 102.5})
    fixed = exits.ExitConfig.for_mode(exits.MODE_FIXED)
    trend = exits.ExitConfig.for_mode(exits.MODE_TREND_MIN)
    assert exits.check_target(bar, 102.0, fixed).reason == "target"
    assert exits.check_target(bar, 102.0, trend) is None
    assert exits.check_target(bar, None, fixed) is None


def test_ema_break_fires_on_close_below_fast_average() -> None:
    cfg = exits.ExitConfig.for_mode(exits.MODE_TREND_MIN)
    st = _state(entry=100.0, stop=99.0)
    st.armed = True
    rising = _bars(_ramp(30, 100, 0.5))
    assert exits.check_trend(st, rising, None, cfg) is None
    broken = rising.copy()
    last = broken.index[-1]
    broken.loc[last, ["open", "high", "low", "close"]] = [114.0, 114.2, 108.0, 108.5]
    sig = exits.check_trend(st, broken, None, cfg)
    assert sig is not None and sig.reason in ("ema9_break", "ema20_break")
    assert sig.price == pytest.approx(108.5)


def test_macd_fade_only_in_full_mode() -> None:
    st = _state()
    st.armed = True
    rows = _ramp(60, 100, 0.5) + _ramp(12, 130, -0.9)
    bars = _bars(rows)
    tmin = exits.ExitConfig(mode=exits.MODE_TREND_MIN, use_ema_fast_break=False,
                            use_ema_slow_break=False, use_swing_trail=False)
    tfull = exits.ExitConfig(mode=exits.MODE_TREND_FULL, use_ema_fast_break=False,
                             use_ema_slow_break=False, use_swing_trail=False,
                             use_macd_fade=True)
    hist = macd(bars["close"]).hist
    cross = [i for i in range(1, len(hist)) if hist.iloc[i - 1] > 0 >= hist.iloc[i]]
    assert cross, "test data must contain a histogram crossing"
    at = bars.iloc[: cross[0] + 1]
    assert exits.check_trend(st, at, None, tmin) is None
    sig = exits.check_trend(st, at, None, tfull)
    assert sig is not None and sig.reason == "macd_fade"


def test_resistance_rejection_needs_a_level_and_a_weak_candle() -> None:
    st = _state(entry=100.0, stop=99.0)
    st.armed = True
    st.levels = [levels.Level(105.0, "pivot_high", 3, 5000.0, 4.0)]
    cfg = exits.ExitConfig(mode=exits.MODE_TREND_FULL, use_ema_fast_break=False,
                           use_ema_slow_break=False, use_swing_trail=False,
                           use_resistance_reject=True)
    rejected = _bars(_ramp(10, 100, 0.4) + [(104.9, 105.1, 104.0, 104.2, 900)])
    sig = exits.check_trend(st, rejected, None, cfg)
    assert sig is not None and sig.reason == "resistance_reject"
    strong = _bars(_ramp(10, 100, 0.4) + [(104.9, 105.1, 104.8, 105.05, 900)])
    assert exits.check_trend(st, strong, None, cfg) is None      # closed strong at the level
    st.levels = []
    assert exits.check_trend(st, rejected, None, cfg) is None    # no levels → no rule


def test_resistance_state_needs_structural_level_and_failed_break() -> None:
    st = _state(entry=100.0, stop=99.0)
    st.armed = True
    cfg = exits.ExitConfig(
        mode=exits.MODE_TREND_RESISTANCE_STATE,
        use_ema_fast_break=False, use_ema_slow_break=False, use_swing_trail=False,
        use_resistance_reject=True, structural_resistance_only=True,
        resistance_requires_failed_break=True,
    )
    # A one-touch pivot must not force an exit, even when price turns red nearby.
    st.levels = [levels.Level(105.0, "pivot_high", 1, 500.0, 1.0)]
    failed = _bars(_ramp(10, 100, 0.4) + [(104.9, 105.1, 104.0, 104.2, 900)])
    assert exits.check_trend(st, failed, None, cfg) is None

    # A structural ceiling exits only after an actual test closes back below it.
    st.levels = [levels.Level(105.0, "pivot_high", 2, 5000.0, 2.0)]
    sig = exits.check_trend(st, failed, None, cfg)
    assert sig is not None and sig.reason == "resistance_reject"

    # A red pullback that remains above an accepted level is not a rejection.
    held_above = _bars(_ramp(10, 100, 0.4) + [(105.6, 105.8, 105.1, 105.2, 900)])
    assert exits.check_trend(st, held_above, None, cfg) is None


def test_volume_climax_needs_a_heavy_down_bar() -> None:
    st = _state()
    st.armed = True
    cfg = exits.ExitConfig(mode=exits.MODE_TREND_FULL, use_ema_fast_break=False,
                           use_ema_slow_break=False, use_swing_trail=False,
                           use_volume_climax=True)
    quiet = _bars([(100, 101, 99, 100.5, 1000)] * 25)
    heavy_down = _bars([(100, 101, 99, 100.5, 1000)] * 25 + [(101, 101.2, 98, 98.5, 4000)])
    heavy_up = _bars([(100, 101, 99, 100.5, 1000)] * 25 + [(98.5, 102, 98.4, 101.8, 4000)])
    assert exits.check_trend(st, quiet, None, cfg) is None
    sig = exits.check_trend(st, heavy_down, None, cfg)
    assert sig is not None and sig.reason == "volume_climax"
    assert exits.check_trend(st, heavy_up, None, cfg) is None    # heavy but UP → not selling


def test_warmup_makes_indicators_available_immediately() -> None:
    """Without warm-up a 4-bar session has no MACD; with it, the value exists."""
    today = _bars(_ramp(4, 120, 0.3), start="2026-09-08 09:15")
    warm = _bars(_ramp(60, 100, 0.3), start="2026-09-07 09:15")
    cold = exits.indicator_frame(today, None)
    hot = exits.indicator_frame(today, warm)
    assert pd.isna(cold["macd_hist"].iloc[-1]) and pd.isna(cold["ema_slow"].iloc[-1])
    assert not pd.isna(hot["macd_hist"].iloc[-1]) and not pd.isna(hot["ema_slow"].iloc[-1])
    assert len(hot["ema_fast"]) == len(today)     # sliced back to today only
