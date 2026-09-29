"""BT52: the resistance-capped target becomes a stop checkpoint instead of a
sell (research/hypotheses/2026-09-27-resistance-checkpoint-stop.md).

Reuses the GODREJIND 2026-09-25 fixture from BT50: fill ₹1,133.1, stop
₹1,125.7, 2R ₹1,147.9, the Sep 17 high ₹1,147.0 → BT50 sells at ₹1,145.25.
With the checkpoint the target stays ₹1,147.9 and reaching ₹1,145.25 lifts the
stop to ₹1,145.25 × (1 − 0.15%) floored to the tick = ₹1,143.50.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.momentum_trader import engine
from src.momentum_trader.catalyst import no_catalyst

from .test_session_levels import COLS, IST, _godrejind_open, _history

_HIST = {"2026-09-16": 1172.0, "2026-09-17": 1147.0, "2026-09-24": 1109.1}


def _open(checkpoint: bool) -> tuple[engine.DayState, engine.EngineConfig]:
    cfg = engine.EngineConfig(session_level_sessions=10, target_buffer_pct=0.15,
                              resistance_checkpoint_stop=checkpoint)
    return _godrejind_open(cfg, engine.build_session_levels(_history(_HIST), 10)), cfg


def _run(state: engine.DayState, cfg: engine.EngineConfig,
         candles: list[tuple[float, float, float, float]]) -> None:
    idx = pd.date_range("2026-09-25 09:32", periods=len(candles), freq="1min", tz=IST)
    bars = pd.DataFrame([(*c, 2_000.0) for c in candles], index=idx, columns=COLS)
    for i in range(1, len(bars) + 1):
        engine.step(state, bars.iloc[:i], cfg, no_catalyst)
        if state.position is None:
            return


def test_target_stays_2r_and_the_cap_becomes_the_checkpoint() -> None:
    state, _ = _open(True)
    pos = state.position
    assert pos.plan.target == pytest.approx(1147.9)
    assert pos.checkpoint == pytest.approx(1145.25)
    assert pos.checkpoint_stop == pytest.approx(1143.50)
    assert pos.target_source == "fixed_2r+checkpoint:session_high"


def test_off_by_default_the_bt50_cap_still_sells() -> None:
    state, cfg = _open(False)
    assert state.position.plan.target == pytest.approx(1145.25)
    assert state.position.checkpoint is None
    _run(state, cfg, [(1143.4, 1146.0, 1141.2, 1144.7)])
    assert state.closed[-1].exit_reason == "target"
    assert state.closed[-1].exit == pytest.approx(1145.25)


def test_reaching_the_checkpoint_does_not_sell_and_lifts_the_stop_next_bar() -> None:
    state, cfg = _open(True)
    # The bar that reaches ₹1,145.25 also dips to ₹1,141.2, under the future
    # checkpoint stop: it must NOT fill there (same-bar look-ahead, BT46).
    _run(state, cfg, [(1143.4, 1146.0, 1141.2, 1144.7)])
    assert state.position is not None and state.position.checkpoint_armed
    assert state.position.exit_state.stop < 1143.50
    # Next bar: the lift binds before its low is tested, and fills.
    _run(state, cfg, [(1144.5, 1144.9, 1143.0, 1143.2)])
    t = state.closed[-1]
    assert t.exit_reason == "checkpoint_stop"
    assert t.exit == pytest.approx(1143.50)
    assert t.checkpoint == pytest.approx(1145.25) and t.checkpoint_hit


def test_a_run_past_the_checkpoint_sells_at_2r() -> None:
    state, cfg = _open(True)
    _run(state, cfg, [(1143.4, 1146.0, 1144.0, 1145.8), (1145.8, 1148.5, 1145.0, 1148.2)])
    t = state.closed[-1]
    assert t.exit_reason == "target" and t.exit == pytest.approx(1147.9)


def test_warrior_strict_turns_the_checkpoint_on_and_other_arms_do_not() -> None:
    from src.config import Settings
    from src.momentum_trader.scanner import STRATEGY_WARRIOR_STRICT, _strategy_config

    strict = _strategy_config(Settings(mt_strategy=STRATEGY_WARRIOR_STRICT))
    assert strict.resistance_checkpoint_stop
    merged = _strategy_config(Settings(mt_strategy="attention_1m_merged"))
    assert merged.resistance_checkpoint_stop is False


def test_checkpoint_needs_the_structural_cap() -> None:
    with pytest.raises(ValueError, match="resistance_checkpoint_stop"):
        engine.EngineConfig(use_structural_exit_levels=False, resistance_checkpoint_stop=True)
