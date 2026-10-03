"""Resistance admission is checked again at the executable fill price."""

from __future__ import annotations

import pandas as pd
import pytest
from src.momentum_trader import engine as eng
from src.momentum_trader.short_side import Reflection, _evidence

IST = "Asia/Kolkata"


def _pending(
    trigger: float = 100.0, stop: float = 99.0, ceiling: float = 102.0,
    close: float = 100.0,
) -> tuple[eng.DayState, eng.EngineConfig, pd.DataFrame, pd.Timestamp]:
    at = pd.Timestamp("2026-10-01 10:04", tz=IST)
    bars = pd.DataFrame(
        [{"open": close - 0.1, "high": close + 0.1, "low": close - 0.2,
          "close": close, "volume": 1000.0}], index=pd.DatetimeIndex([at]),
    )
    cand = eng.Candidate(
        "TEST", at, eng.Setup(eng.ATTENTION_SETUP, trigger, stop),
        day_chg_pct=2.0, rvol=3.0, catalyst=0, event_type="", candle_tags=[],
    )
    decision = at + pd.Timedelta(minutes=1)
    state = eng.DayState(
        "TEST", 98.0, None, prev_day={"high": ceiling, "low": 95.0, "close": 98.0},
        pending=eng.Pending(cand, decision, decision + pd.Timedelta(minutes=3)),
    )
    cfg = eng.EngineConfig(
        fill_mode=eng.FILL_FUTURE_TRIGGER, require_resistance_breakout=True,
    )
    return state, cfg, bars, decision + pd.Timedelta(seconds=3)


@pytest.mark.parametrize("fill", [1236.7, 1237.7])
def test_schneider_price_slip_cancels_a_signal_that_had_room_at_its_trigger(fill):
    # The signal's 0.50 risk fits comfortably under this known ceiling. Once
    # price has moved, the actual 6.60/7.60 risk no longer fits the remaining room.
    state, cfg, bars, when = _pending(1230.6, 1230.1, 1238.83, 1236.9)
    assert eng.fill_pending_quote(state, when, fill, cfg, bars) is None
    assert state.position is None and state.pending is None
    rejection = state.rejections[-1]
    assert rejection.reason == "fill_resistance_headroom"
    assert rejection.observed_price == fill
    assert rejection.evidence["level"] == pytest.approx(1238.83)
    assert rejection.evidence["initial_risk"] == pytest.approx(fill - 1230.1)
    assert rejection.evidence["headroom_r"] < 1.0


@pytest.mark.parametrize("fill,allowed", [(100.5, True), (100.6, False)])
def test_exactly_one_r_of_room_is_allowed_but_less_is_refused(fill, allowed):
    state, cfg, bars, when = _pending()
    opened = eng.fill_pending_quote(state, when, fill, cfg, bars)
    assert (opened is not None) == allowed


def test_quote_jump_does_not_count_as_a_confirmed_resistance_breakout():
    state, cfg, bars, when = _pending(ceiling=100.9)
    assert eng.fill_pending_quote(state, when, 100.95, cfg, bars) is None
    assert state.rejections[-1].evidence["headroom"] < 0.0


def test_a_completed_close_through_the_level_uses_the_next_ceiling():
    state, cfg, bars, when = _pending(trigger=101.0, ceiling=100.9, close=101.0)
    assert eng.fill_pending_quote(state, when, 101.0, cfg, bars) is not None


def test_a_completed_close_exactly_on_resistance_does_not_clear_it():
    state, cfg, bars, when = _pending(trigger=100.9, ceiling=100.9, close=100.9)
    assert eng.fill_pending_quote(state, when, 100.95, cfg, bars) is None
    assert state.rejections[-1].evidence["level"] == pytest.approx(100.9)


def test_unclosed_future_candle_cannot_clear_resistance_for_a_quote_fill():
    state, cfg, bars, when = _pending(ceiling=100.9)
    future = bars.copy()
    future.index += pd.Timedelta(minutes=1)
    future.loc[:, ["open", "high", "low", "close"]] = [100.0, 106.0, 99.9, 105.0]
    bars = pd.concat([bars, future])
    assert eng.fill_pending_quote(state, when, 100.95, cfg, bars) is None
    assert state.rejections[-1].evidence["level"] == pytest.approx(100.9)


def test_strategies_without_the_resistance_rule_keep_their_fill_behavior():
    state, cfg, bars, when = _pending(ceiling=100.9)
    cfg.require_resistance_breakout = False
    assert eng.fill_pending_quote(state, when, 100.95, cfg, bars) is not None


@pytest.mark.parametrize("v2,allowed", [(False, False), (True, True)])
def test_fill_keeps_the_strategy_choice_about_round_number_levels(v2, allowed):
    state, cfg, bars, when = _pending(109.0, 108.0, 120.0, 109.0)
    cfg.resistance_veto_v2 = v2
    opened = eng.fill_pending_quote(state, when, 109.8, cfg, bars)
    assert (opened is not None) == allowed


@pytest.mark.parametrize("mode", [eng.FILL_FUTURE_TRIGGER, eng.FILL_NEXT_OPEN,
                                  eng.FILL_RESTING, eng.FILL_RESTING_SIZED])
def test_replay_fill_paths_also_refuse_the_schneider_price_slip(mode):
    state, cfg, bars, _ = _pending(1230.6, 1230.1, 1238.83, 1236.9)
    cfg.fill_mode = mode
    later = pd.DataFrame(
        [{"open": 1236.7, "high": 1238.0, "low": 1236.7,
          "close": 1236.7, "volume": 600.0}],
        index=pd.DatetimeIndex([bars.index[-1] + pd.Timedelta(minutes=1)]),
    )
    eng.step(state, pd.concat([bars, later]), cfg, lambda *_: (0, ""))
    assert state.position is None and state.pending is None
    assert state.rejections[-1].reason == "fill_resistance_headroom"


def test_short_reflection_keeps_fill_rejection_prices_in_real_units():
    doc = {"price": 101.0, "stop": 99.0, "level": 101.5,
           "level_kind": "pivot_high", "headroom_r": 0.25}
    real = _evidence(doc, Reflection(100.0))
    assert real["price"] == 99.0
    assert real["stop"] == 101.0
    assert real["level"] == 98.5
    assert real["level_kind"] == "pivot_low"
    assert real["headroom_r"] == 0.25
