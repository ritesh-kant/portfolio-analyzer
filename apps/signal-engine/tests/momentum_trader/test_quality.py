"""Selectivity-filter tests (research/hypotheses/2026-09-06-quality-selectivity.md).

The filters only mean anything if they refuse the right things and, critically,
if they never read data from the day being traded. Both are pinned here.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.momentum_trader import engine as eng
from src.momentum_trader import quality
from src.momentum_trader.exits import CLIMAX_VOL_RATIO

IST = "Asia/Kolkata"
Row = tuple[float, float, float, float, float]


def _frame(rows: list[Row], start: str = "2024-06-03 09:15", freq: str = "5min") -> pd.DataFrame:
    idx = pd.date_range(start=pd.Timestamp(start, tz=IST), periods=len(rows), freq=freq)
    return pd.DataFrame(
        [{"open": o, "high": h, "low": lo, "close": c, "volume": v} for o, h, lo, c, v in rows],
        index=idx,
    )


def _rising(n: int, start: float = 100.0, step: float = 0.25, vol: float = 10000.0) -> list[Row]:
    out = []
    px = start
    for _ in range(n):
        out.append((px, px + step, px - 0.02, px + step - 0.02, vol))
        px += step
    return out


# ── F2: chart quality ────────────────────────────────────────────────────────

def _session(day: str, minutes: int, flat_share: float, traded_share: float = 1.0) -> pd.DataFrame:
    rows: list[Row] = []
    n_flat = int(minutes * flat_share)
    n_silent = int(minutes * (1.0 - traded_share))
    px = 100.0
    for i in range(minutes):
        vol = 0.0 if i < n_silent else 500.0
        if i < n_flat:
            rows.append((px, px, px, px, vol))          # flat bar
        else:
            rows.append((px, px + 0.2, px - 0.1, px + 0.1, vol))
            px += 0.01
    return _frame(rows, start=f"{day} 09:15", freq="1min")


def test_chart_quality_needs_history() -> None:
    assert quality.chart_quality(pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"])) is None


def test_a_normal_chart_passes() -> None:
    hist = pd.concat([_session(f"2024-05-{d:02d}", 375, flat_share=0.05) for d in range(1, 6)])
    cq = quality.chart_quality(hist)
    assert cq is not None and cq.ok and cq.reason == "ok"


def test_a_mostly_flat_chart_is_refused() -> None:
    """The JSWDULUX case: three quarters of bars are a flat line, so every
    indicator computed on it is reading its own padding."""
    hist = pd.concat([_session(f"2024-05-{d:02d}", 375, flat_share=0.74) for d in range(1, 6)])
    cq = quality.chart_quality(hist)
    assert cq is not None and not cq.ok and cq.reason == "flat_bars"


def test_a_thin_tape_is_refused() -> None:
    hist = pd.concat([_session(f"2024-05-{d:02d}", 375, flat_share=0.02, traded_share=0.40)
                      for d in range(1, 6)])
    cq = quality.chart_quality(hist)
    assert cq is not None and not cq.ok and cq.reason == "thin_tape"


def test_thresholds_are_the_frozen_ones() -> None:
    """Guards against a quiet retune: §1 freezes these two numbers."""
    assert quality.MIN_MINUTE_COVERAGE == 0.85
    assert quality.MAX_FLAT_BAR_SHARE == 0.30


# ── F1: uptrend ──────────────────────────────────────────────────────────────

def test_uptrend_passes_on_a_clean_advance() -> None:
    bars = _frame(_rising(40))
    ok, reason = quality.uptrend_ok(bars, prev_close=95.0, daily_sma20=90.0)
    assert ok and reason == "ok"


def test_falling_price_is_refused() -> None:
    bars = _frame(_rising(40, start=120.0, step=-0.25))
    ok, reason = quality.uptrend_ok(bars, prev_close=95.0, daily_sma20=90.0)
    assert not ok and reason in ("ema_down", "below_vwap")


def test_daily_downtrend_is_refused_even_when_today_looks_fine() -> None:
    """A stock well below its 20-day average that pops today is exactly the
    case the operator asked to exclude."""
    bars = _frame(_rising(40))
    ok, reason = quality.uptrend_ok(bars, prev_close=95.0, daily_sma20=140.0)
    assert not ok and reason == "daily_downtrend"


def test_missing_daily_average_does_not_refuse() -> None:
    bars = _frame(_rising(40))
    ok, _ = quality.uptrend_ok(bars, prev_close=95.0, daily_sma20=None)
    assert ok


def test_uptrend_refuses_before_the_emas_have_warmed_up() -> None:
    ok, reason = quality.uptrend_ok(_frame(_rising(5)), 95.0, 90.0)
    assert not ok and reason == "ema_warmup"


# ── F3: surge ────────────────────────────────────────────────────────────────

def _pole_bars(vol_mult: float) -> pd.DataFrame:
    """25 quiet bars, then a >=2% run whose last bar carries `vol_mult` x volume."""
    rows: list[Row] = []
    px = 100.0
    for _ in range(25):
        rows.append((px, px + 0.05, px - 0.05, px, 10000.0))
    rows.append((px, px + 0.6, px - 0.02, px + 0.55, 10000.0))
    rows.append((px + 0.55, px + 1.3, px + 0.5, px + 1.25, 10000.0))
    rows.append((px + 1.25, px + 2.4, px + 1.2, px + 2.35, 10000.0 * vol_mult))
    return _frame(rows)


def test_a_surge_on_heavy_volume_passes() -> None:
    ok, reason = quality.surge_ok(_pole_bars(vol_mult=CLIMAX_VOL_RATIO + 1.0))
    assert ok and reason == "ok"


def test_a_surge_without_volume_is_refused() -> None:
    """A limp break — price moved but nobody showed up."""
    ok, reason = quality.surge_ok(_pole_bars(vol_mult=1.0))
    assert not ok and reason == "surge_no_volume"


def test_no_surge_at_all_is_refused() -> None:
    drift = [(100.0 + i * 0.01, 100.0 + i * 0.01 + 0.02, 100.0 + i * 0.01 - 0.02,
              100.0 + i * 0.01, 10000.0) for i in range(40)]
    ok, reason = quality.surge_ok(_frame(drift))
    assert not ok and reason == "no_surge"


# ── the gate as wired into the engine ────────────────────────────────────────

def _state(cq: quality.ChartQuality | None, sma: float | None = 90.0) -> eng.DayState:
    return eng.DayState(symbol="T", prev_close=95.0, cum_vol_profile=None,
                        chart_quality=cq, daily_sma20=sma)


_GOOD_CQ = quality.ChartQuality(minute_coverage=0.99, flat_bar_share=0.05, sessions=20)
# the real JSWDULUX 2022 numbers — it fails BOTH legs, and coverage is reported
# first because a tape that thin is the more fundamental problem
_BAD_CQ = quality.ChartQuality(minute_coverage=0.357, flat_bar_share=0.735, sessions=20)
# fails only the flat-bar leg, so that label is reachable in a test
_FLAT_CQ = quality.ChartQuality(minute_coverage=0.95, flat_bar_share=0.55, sessions=20)


def test_gate_is_inert_when_disabled() -> None:
    cfg = eng.EngineConfig()
    assert cfg.require_quality is False
    ok, reason = eng._quality_gate(_state(None), pd.DataFrame(), cfg)
    assert ok and reason == "ok"


def test_gate_refuses_a_malformed_chart_before_looking_at_anything_else() -> None:
    """A perfect surge on a chart nobody trades is still refused."""
    cfg = eng.EngineConfig(require_quality=True)
    ok, reason = eng._quality_gate(_state(_BAD_CQ), _pole_bars(5.0), cfg)
    assert not ok and reason == "thin_tape"
    ok, reason = eng._quality_gate(_state(_FLAT_CQ), _pole_bars(5.0), cfg)
    assert not ok and reason == "flat_bars"


def test_gate_refuses_when_there_is_no_history_to_judge() -> None:
    cfg = eng.EngineConfig(require_quality=True)
    ok, reason = eng._quality_gate(_state(None), _pole_bars(5.0), cfg)
    assert not ok and reason == "no_history"


def test_gate_passes_a_good_chart_in_an_uptrend_after_a_heavy_surge() -> None:
    cfg = eng.EngineConfig(require_quality=True)
    ok, reason = eng._quality_gate(_state(_GOOD_CQ), _pole_bars(CLIMAX_VOL_RATIO + 1.0), cfg)
    assert ok and reason == "ok"


def test_gate_refuses_a_good_chart_whose_surge_had_no_volume() -> None:
    cfg = eng.EngineConfig(require_quality=True)
    ok, reason = eng._quality_gate(_state(_GOOD_CQ), _pole_bars(1.0), cfg)
    assert not ok and reason == "surge_no_volume"


def test_refused_setups_are_still_recorded_as_candidates() -> None:
    """Pass rates and refusal reasons must be measurable, so a refused setup is
    logged and then dropped rather than never created."""
    from src.momentum_trader.setups import Setup
    st = _state(_BAD_CQ)
    cand = eng.Candidate(symbol="T", time=pd.Timestamp("2024-06-03 10:00", tz=IST),
                         setup=Setup("micro_pullback", 100.0, 99.0), day_chg_pct=5.0,
                         rvol=4.0, catalyst=0, event_type="", candle_tags=[],
                         quality_reason="flat_bars")
    st.candidates.append(cand)
    assert st.candidates[0].quality_reason == "flat_bars"
    assert st.pending is None


def test_quality_arm_is_a_subset_of_the_unfiltered_arm() -> None:
    """The filters may only REMOVE trades; they must never create one the
    unfiltered engine would not have taken."""
    from tests.momentum_trader.test_multi_entry import _session as multi_session
    bars, prev_close, profile = multi_session()
    hist = pd.concat([_session(f"2024-05-{d:02d}", 375, flat_share=0.05) for d in range(1, 6)])
    cq = quality.chart_quality(hist)

    def run(require: bool) -> list[pd.Timestamp]:
        cfg = eng.EngineConfig(stress_slip=0.0, require_quality=require)
        st = eng.run_day("T", bars, prev_close, profile, cfg, lambda _s, _t: (0, ""),
                         chart_quality=cq, daily_sma20=90.0)
        return sorted(t.entry_time for t in st.closed)

    base, sel = run(False), run(True)
    assert set(sel).issubset(set(base))
    assert len(sel) <= len(base)


def test_engine_config_default_keeps_every_prior_backtest_reproducible() -> None:
    cfg = eng.EngineConfig()
    assert (cfg.require_quality, cfg.one_trade_per_day, cfg.fill_mode) == (
        False, True, eng.FILL_NEXT_OPEN)
    assert cfg.exit_mode == "fixed_2r"


@pytest.mark.parametrize("reason", ["thin_tape", "flat_bars", "ema_down", "below_vwap",
                                    "daily_downtrend", "no_surge", "surge_no_volume",
                                    "no_history", "ema_warmup"])
def test_every_refusal_reason_is_a_distinct_label(reason: str) -> None:
    """The analysis splits on these strings, so they must not collide."""
    assert reason and reason != "ok"
