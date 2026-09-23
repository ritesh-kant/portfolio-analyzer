"""The Warrior transcript's entry checklist, rule by rule.

Each test names the line of the guide it is protecting, and every one of them
checks BOTH directions: that the rule refuses what the guide says to refuse,
and that the switch left off changes nothing. The second half matters as much
as the first — these gates ship default-off precisely so that the forward arms
recorded before 2026-09-15 still replay bit-for-bit.

See research/hypotheses/2026-09-15-warrior-guide-strict.md.
"""

from __future__ import annotations

from datetime import time

import pandas as pd
import pytest
from src.momentum_trader import exits
from src.momentum_trader.engine import (
    GUIDE_PULLBACK_ORDINALS,
    VOL_BASELINE_MIN_BARS,
    DayState,
    EngineConfig,
    GuideGates,
    _attention_confirmation,
    _attention_context,
    _macd_closing,
    _macd_open_state,
    _pullback_ordinal_gate,
    _trend_ema,
    resample_5m,
)
from src.momentum_trader.setups import micro_pullback

IST = "Asia/Kolkata"


def _bars(rows: list[tuple[float, float, float, float, float]],
          start: str = "2026-09-15 09:15", freq: str = "1min") -> pd.DataFrame:
    idx = pd.date_range(pd.Timestamp(start, tz=IST), periods=len(rows), freq=freq)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)


def _flat_day(n: int, price: float = 100.0, vol: float = 1_000.0,
              start: str = "2026-09-14 09:15", freq: str = "5min") -> pd.DataFrame:
    """A prior session of gently rising bars, for warming indicators."""
    rows = []
    for i in range(n):
        p = price + i * 0.01
        rows.append((p, p + 0.05, p - 0.05, p + 0.01, vol))
    return _bars(rows, start=start, freq=freq)


# ── 1. micro pullback: "wait for the pullback ... 1 to 2 candles" ────────────

def test_micro_pullback_accepts_a_two_bar_pause_only_when_allowed():
    # 2 green push bars, TWO red pause bars, then the break.
    rows = [
        (100.0, 101.0, 99.9, 100.9, 2_000.0),
        (100.9, 102.0, 100.8, 101.9, 2_000.0),
        (101.9, 101.95, 101.2, 101.3, 400.0),
        (101.3, 101.4, 101.0, 101.1, 400.0),
        (101.1, 102.5, 101.0, 102.4, 2_500.0),
    ]
    bars = _bars(rows)
    assert micro_pullback(bars) is None, "one-bar pause cannot describe this"
    found = micro_pullback(bars, max_pause_bars=2)
    assert found is not None
    assert found.trigger == pytest.approx(101.95)   # highest high of the pause
    assert found.stop == pytest.approx(101.0)       # lowest low of the pause
    assert found.meta["pause_bars"] == 2.0


def test_micro_pullback_default_is_unchanged_by_the_new_parameters():
    rows = [
        (100.0, 101.0, 99.9, 100.9, 2_000.0),
        (100.9, 102.0, 100.8, 101.9, 2_000.0),
        (101.9, 101.95, 101.2, 101.3, 400.0),
        (101.3, 102.5, 101.25, 102.4, 2_500.0),
    ]
    bars = _bars(rows)
    one = micro_pullback(bars)
    assert one is not None
    assert (one.trigger, one.stop) == (pytest.approx(101.95), pytest.approx(101.2))
    # Raising the cap can only ADD structures: the shorter pause is tried first.
    two = micro_pullback(bars, max_pause_bars=2)
    assert (two.trigger, two.stop) == (one.trigger, one.stop)


# ── 2. "light volume on pullbacks" ───────────────────────────────────────────

def test_light_volume_refuses_a_pullback_that_sellers_showed_up_in():
    heavy = [
        (100.0, 101.0, 99.9, 100.9, 2_000.0),
        (100.9, 102.0, 100.8, 101.9, 2_000.0),
        (101.9, 101.95, 101.2, 101.3, 5_000.0),   # heavier than the push
        (101.3, 102.5, 101.25, 102.4, 2_500.0),
    ]
    bars = _bars(heavy)
    assert micro_pullback(bars) is not None, "structure is present either way"
    assert micro_pullback(bars, require_light_volume=True) is None

    light = list(heavy)
    light[2] = (101.9, 101.95, 101.2, 101.3, 400.0)
    assert micro_pullback(_bars(light), require_light_volume=True) is not None


# ── 3. "MACD positive and open"; "don't trade if negative or flat" ───────────

def _macd_frame(closes: list[float]) -> pd.DataFrame:
    rows = [(c, c + 0.05, c - 0.05, c, 1_000.0) for c in closes]
    return _bars(rows)


def test_macd_open_state_reports_both_readings():
    rising = _macd_frame([100.0 + i * 0.5 for i in range(80)])
    state = _macd_open_state(rising, None)
    assert state is not None
    hist, prev = state
    assert hist > 0.0, "a steady advance leaves the histogram positive"
    assert hist != prev


def test_macd_warmup_returns_none_rather_than_a_number():
    assert _macd_open_state(_macd_frame([100.0, 100.1]), None) is None


def test_confirmation_refuses_a_falling_macd_and_accepts_a_rising_one():
    cfg = EngineConfig(require_macd_positive_open=True, vol_baseline_min_bars=3)
    # A long advance that rolls over into a fade, then one strong candle.
    closes = [100.0 + i * 0.4 for i in range(60)] + [124.0 - i * 0.5 for i in range(20)]
    rows = [(c, c + 0.05, c - 0.05, c, 1_000.0) for c in closes]
    rows.append((114.0, 115.0, 113.9, 114.95, 9_000.0))   # green, strong close, heavy
    faded = _bars(rows)
    setup, reason = _attention_confirmation(faded, 2.5, GuideGates(cfg=cfg))
    assert setup is None
    assert reason in {"attention_macd_not_positive", "attention_macd_not_open"}

    # Same candle without the MACD requirement is accepted, so the refusal
    # above is attributable to the MACD rule alone.
    off = EngineConfig(vol_baseline_min_bars=3)
    setup_off, reason_off = _attention_confirmation(faded, 2.5, GuideGates(cfg=off))
    assert setup_off is not None and reason_off == "confirmed"


# ── 4. "first and second pullbacks only" ─────────────────────────────────────

@pytest.mark.parametrize(
    ("ordinal", "expected"),
    [(1, "ok"), (2, "ok"), (3, "pullback_not_allowed"),
     (None, "pullback_no_anchor")],
)
def test_pullback_ordinal_gate(ordinal, expected):
    cfg = EngineConfig(allowed_pullback_ordinals=GUIDE_PULLBACK_ORDINALS)
    ok, reason = _pullback_ordinal_gate(ordinal, cfg)
    assert reason == expected
    assert ok == (expected == "ok")


def test_pullback_ordinal_gate_is_inert_when_unset():
    for ordinal in (None, 1, 7):
        assert _pullback_ordinal_gate(ordinal, EngineConfig()) == (True, "ok")


# ── 5. peak hours only / avoid midday ────────────────────────────────────────

def test_peak_hours_tightens_the_cutoff_and_never_extends_it():
    assert EngineConfig().entry_deadline == time(14, 30)
    assert EngineConfig(peak_hours_only=True).entry_deadline == time(11, 0)
    # A peak_hours_end LATER than the standing cutoff must not buy extra hours.
    late = EngineConfig(peak_hours_only=True, peak_hours_end=time(15, 0))
    assert late.entry_deadline == time(14, 30)


# ── 6. the warm-up that makes peak hours reachable ───────────────────────────

def test_context_needs_twenty_bars_without_warmup_and_none_with_it():
    today = _flat_day(4, price=110.0, start="2026-09-15 09:15")
    ok, reason = _attention_context(today)
    assert (ok, reason) == (False, "ema_warmup"), "this is the 10:55 floor"

    warm = _flat_day(240, price=100.0, start="2026-09-08 09:15")
    ok_warm, reason_warm = _attention_context(today, warm)
    assert ok_warm, f"warmed context should resolve, got {reason_warm}"


def test_warm_context_does_not_warm_vwap():
    """Session VWAP resets daily; warming it would compare today to last week."""
    warm = _flat_day(240, price=100.0, start="2026-09-08 09:15")
    # Today's close sits far above last week's prices, but the VWAP test is run
    # on today's bars alone, so it is the session VWAP that decides.
    rows = [(110.0, 110.2, 109.0, 109.1, 5_000.0)] * 4
    falling = _bars(rows, start="2026-09-15 09:15", freq="5min")
    ok, reason = _attention_context(falling, warm)
    assert not ok and reason in {"below_vwap", "ema_down"}


# ── 7. 2:1 target kept alongside the trend exits ─────────────────────────────

def test_fixed_target_can_be_added_to_a_trend_mode():
    plain = exits.ExitConfig.for_mode(exits.MODE_TREND_RESISTANCE_STATE)
    assert not plain.has_target
    both = exits.ExitConfig.for_mode(
        exits.MODE_TREND_RESISTANCE_STATE, use_fixed_target=True
    )
    assert both.has_target
    # The trend rules survive: this is "target OR signal", not "target instead".
    assert both.use_macd_fade and both.use_resistance_reject and both.use_swing_trail


def test_fixed_mode_target_is_untouched():
    assert exits.ExitConfig.for_mode(exits.MODE_FIXED).has_target


# ── 8. the 200 EMA is recorded, never gated ──────────────────────────────────

def test_trend_ema_needs_prior_sessions_and_gates_nothing():
    today = _flat_day(10, price=110.0, start="2026-09-15 09:15")
    assert _trend_ema(today, None) is None, "200 bars cannot exist in 50 minutes"
    warm = _flat_day(240, price=100.0, start="2026-09-08 09:15")
    value = _trend_ema(today, warm)
    assert value is not None and value > 0.0
    # No EngineConfig switch exists for it — that is the point.
    assert not any("ema200" in f or "trend_ema" in f for f in EngineConfig.__dataclass_fields__)


# ── 9. the volume baseline that lets the open be traded at all ───────────────

def test_volume_baseline_default_is_blind_to_the_open():
    from src.momentum_trader.indicators import volume_ratio

    early = _bars([(100.0, 100.5, 99.8, 100.4, 1_000.0)] * 5)
    assert pd.isna(volume_ratio(early).iloc[-1]), "default needs 10 prior bars"
    lowered = volume_ratio(early, min_periods=VOL_BASELINE_MIN_BARS)
    assert not pd.isna(lowered.iloc[-1])


def test_volume_baseline_cannot_be_raised_above_the_lookback():
    from src.momentum_trader.indicators import volume_ratio

    bars = _bars([(100.0, 100.5, 99.8, 100.4, 1_000.0)] * 40)
    capped = volume_ratio(bars, lookback=20, min_periods=999)
    assert not pd.isna(capped.iloc[-1])


# ── 10. config validation ────────────────────────────────────────────────────

def test_light_volume_without_a_pullback_requirement_is_rejected_loudly():
    with pytest.raises(ValueError, match="require_micro_pullback"):
        EngineConfig(require_light_pullback_volume=True)


def test_guide_switches_are_all_off_by_default():
    cfg = EngineConfig()
    assert not cfg.require_micro_pullback
    assert not cfg.require_light_pullback_volume
    assert not cfg.require_macd_positive_open
    assert not cfg.peak_hours_only
    assert not cfg.warm_context
    assert not cfg.use_fixed_target
    assert cfg.vol_baseline_min_bars is None
    assert cfg.allowed_pullback_ordinals == ()


# ── 11. the checklist is applied, and recorded, on a real confirmation ───────

def _promoted_state(warm_1m: pd.DataFrame) -> DayState:
    return DayState(
        symbol="TEST", prev_close=100.0, cum_vol_profile=None,
        warmup_1m=warm_1m, warmup_5m=resample_5m(warm_1m),
    )


def test_confirmation_moves_the_buy_stop_down_to_the_pullback_high():
    """The guide buys the break of the PULLBACK candle, not of the push candle."""
    rows = [(99.0, 99.2, 98.9, 99.1, 1_000.0)] * 6 + [
        (100.0, 101.0, 99.9, 100.9, 3_000.0),
        (100.9, 102.0, 100.8, 101.9, 3_000.0),
        (101.9, 101.95, 101.2, 101.3, 400.0),
        (101.3, 102.5, 101.25, 102.45, 9_000.0),
    ]
    bars = _bars(rows)
    off = EngineConfig(vol_baseline_min_bars=3)
    plain, _ = _attention_confirmation(bars, 2.5, GuideGates(cfg=off))
    assert plain is not None and plain.trigger == pytest.approx(102.5)

    on = EngineConfig(require_micro_pullback=True, require_light_pullback_volume=True,
                      vol_baseline_min_bars=3)
    guided, reason = _attention_confirmation(bars, 2.5, GuideGates(cfg=on))
    assert reason == "confirmed"
    assert guided.trigger == pytest.approx(101.95), "pullback high, not the push high"
    assert guided.stop == pytest.approx(101.2), "the low of the pullback"
    assert guided.trigger < plain.trigger, "a strictly better entry price"


def test_confirmation_without_a_pullback_is_refused_by_its_own_reason():
    # Straight green bars — a squeeze with no pause anywhere in it. Only the
    # last candle is heavy, so the volume test passes and the refusal below is
    # attributable to the missing pullback rather than to thin volume.
    rows = [(99.0, 99.2, 98.9, 99.1, 1_000.0)] * 6 + [
        (100.0 + i, 101.0 + i, 99.9 + i, 100.9 + i, 1_500.0) for i in range(3)
    ] + [(103.0, 104.0, 102.9, 103.9, 9_000.0)]
    bars = _bars(rows)
    cfg = EngineConfig(require_micro_pullback=True, vol_baseline_min_bars=3)
    setup, reason = _attention_confirmation(bars, 2.5, GuideGates(cfg=cfg))
    assert setup is None and reason == "attention_no_micro_pullback"


# ── 12. headroom must not count a level the breakout already cleared ─────────

def _breakout_bars() -> pd.DataFrame:
    """A pullback break whose candle closes THROUGH yesterday's high."""
    return _bars([(99.0, 99.2, 98.9, 99.1, 1_000.0)] * 6 + [
        (100.0, 101.0, 99.9, 100.9, 3_000.0),
        (100.9, 102.0, 100.8, 101.9, 3_000.0),
        (101.9, 101.95, 101.2, 101.3, 400.0),
        (101.3, 102.5, 101.25, 102.45, 9_000.0),
    ])


def test_headroom_ignores_resistance_the_confirmation_candle_traded_through():
    """Regression: the strict arm took 0 trades on 2024 because of this.

    With `require_micro_pullback` the buy-stop moves DOWN to the pullback high,
    below the confirmation candle's close. The headroom search then found the
    prior-day high the candle had just broken, called it a ceiling, and refused
    every entry. A level price has already cleared is not supply.
    """
    from src.momentum_trader.engine import _resistance_aware_attention_confirmation

    bars = _breakout_bars()
    prev_day = {"high": 102.2, "low": 98.0, "close": 99.0}   # broken by the close
    cfg = EngineConfig(require_micro_pullback=True, require_light_pullback_volume=True,
                       vol_baseline_min_bars=3)
    setup, reason = _resistance_aware_attention_confirmation(
        bars, 2.5, prev_day, False, GuideGates(cfg=cfg)
    )
    assert setup is not None, f"refused its own breakout: {reason}"
    assert reason == "confirmed_resistance_breakout"
    assert setup.trigger == pytest.approx(101.95), "still the guide's entry price"


def test_headroom_still_refuses_a_ceiling_that_is_genuinely_above():
    """The rule must keep working — the fix is not "never refuse"."""
    from src.momentum_trader.engine import _resistance_aware_attention_confirmation

    bars = _breakout_bars()
    # Yesterday's high sits just above the close, unbroken and within 1R.
    prev_day = {"high": 102.6, "low": 98.0, "close": 99.0}
    cfg = EngineConfig(require_micro_pullback=True, require_light_pullback_volume=True,
                       vol_baseline_min_bars=3)
    setup, reason = _resistance_aware_attention_confirmation(
        bars, 2.5, prev_day, False, GuideGates(cfg=cfg)
    )
    assert setup is None
    assert reason in {"attention_wait_resistance_break",
                      "attention_wait_next_resistance_break"}


def test_the_headroom_reference_is_unchanged_without_a_pullback_trigger():
    """Every pre-2026-09-15 arm had trigger == the bar's high >= its close, so
    the reference price is the trigger and nothing moves."""
    from src.momentum_trader.engine import _headroom_from

    bars = _breakout_bars()
    high_trigger = _attention_confirmation(
        bars, 2.5, GuideGates(cfg=EngineConfig(vol_baseline_min_bars=3))
    )[0]
    assert _headroom_from(bars, high_trigger) == pytest.approx(high_trigger.trigger)


def test_no_guide_means_the_original_code_path():
    """A `None` guide must reproduce the pre-checklist confirmation exactly."""
    rows = [(99.0, 99.2, 98.9, 99.1, 1_000.0)] * 20 + [
        (100.0, 101.0, 99.9, 100.9, 9_000.0),
    ]
    bars = _bars(rows)
    assert _attention_confirmation(bars, 2.5) == _attention_confirmation(bars, 2.5, None)


# ── MACD "open" tolerance (operator decision 2026-09-23) ─────────────────────
# IKS 2026-09-23 09:30 on official candles: histogram 1.985 → 1.849, a 6.8%
# shrink on the breakout candle after a red pause.

def test_zero_tolerance_is_the_frozen_rule():
    assert _macd_closing(1.849, 1.985, 0.0)
    assert _macd_closing(1.985, 1.985, 0.0), "flat is 'not open' under the frozen rule"
    assert not _macd_closing(1.986, 1.985, 0.0)


def test_ten_percent_admits_the_iks_0930_dip():
    assert not _macd_closing(1.849, 1.985, 0.10)


def test_ten_percent_still_refuses_a_real_fade():
    # 12% shrink: beyond the allowance
    assert _macd_closing(1.985 * 0.88, 1.985, 0.10)


def test_the_boundary_itself_passes():
    assert not _macd_closing(1.985 * 0.90, 1.985, 0.10)


def test_a_histogram_coming_up_through_zero_is_open():
    assert not _macd_closing(0.2, -0.5, 0.10)


def test_tolerance_is_validated():
    with pytest.raises(ValueError):
        EngineConfig(macd_open_tolerance=-0.01)
    with pytest.raises(ValueError):
        EngineConfig(macd_open_tolerance=1.0)


def test_tolerance_defaults_to_the_frozen_rule():
    assert EngineConfig().macd_open_tolerance == 0.0
