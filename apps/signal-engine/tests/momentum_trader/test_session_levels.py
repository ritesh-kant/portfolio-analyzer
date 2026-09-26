"""BT50: earlier sessions' highs cap the fixed target, and a capped target sits
just under its level (research/hypotheses/2026-09-25-session-resistance-target.md).

The GODREJIND 2026-09-25 case is the motivating trade: fill ₹1,133.1, stop
₹1,125.7, 2R ₹1,147.9, the Sep 17 high ₹1,147.0 unseen by the engine, and a
day high of ₹1,146.0 that missed the target by ₹1.9.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.momentum_trader import engine
from src.momentum_trader.levels import (
    Level,
    buffered_target,
    nearest_structural_resistance,
    resistance_target,
    session_resistance_levels,
)

IST = "Asia/Kolkata"
COLS = ["open", "high", "low", "close", "volume"]


def _session(day: str, high: float, base: float = 1100.0, bars: int = 375) -> pd.DataFrame:
    """One full 09:15-15:29 session that touches `high` once, mid-morning."""
    idx = pd.date_range(f"{day} 09:15", periods=bars, freq="1min", tz=IST)
    rows = [(base, base + 1.0, base - 1.0, base, 1_000.0)] * bars
    rows[60] = (base, high, base - 1.0, base + 2.0, 5_000.0)
    return pd.DataFrame(rows, index=idx, columns=COLS)


def _history(highs: dict[str, float]) -> pd.DataFrame:
    return pd.concat([_session(d, h) for d, h in highs.items()])


# ── levels.py ────────────────────────────────────────────────────────────────

def test_each_prior_session_high_becomes_a_structural_anchor() -> None:
    hist = _history({"2026-09-16": 1172.0, "2026-09-17": 1147.0})
    levels = session_resistance_levels(hist, engine.resample_5m(hist))
    highs = sorted(x.price for x in levels if x.kind == "session_high")
    assert highs == [1147.0, 1172.0]
    assert nearest_structural_resistance(levels, 1133.1).price == 1147.0


def test_a_single_old_pivot_is_context_not_structure() -> None:
    # A lone 5-minute pivot needs a second touch, like today's pivots do.
    levels = [Level(1140.0, "session_pivot", 1, 0.0, 1.0)]
    assert resistance_target(levels, 1133.1, 0.15) is None
    levels = [Level(1140.0, "session_pivot", 2, 0.0, 2.0)]
    assert resistance_target(levels, 1133.1, 0.15)[0].price == 1140.0


def test_no_history_means_no_session_levels() -> None:
    assert session_resistance_levels(pd.DataFrame(columns=COLS), pd.DataFrame()) == []


def test_buffered_target_sits_under_the_level_on_the_tick() -> None:
    # 1147.0 × (1 − 0.15%) = 1145.2795 → floored to the ₹0.05 tick
    assert buffered_target(1147.0, 0.15) == pytest.approx(1145.25)
    assert buffered_target(1150.0, 0.15) == pytest.approx(1148.25)


def test_zero_buffer_leaves_the_level_untouched() -> None:
    # Off-tick VWAP pivot prices must pass through unchanged, or the control
    # would not reproduce BT47.
    assert buffered_target(391.5537, 0.0) == 391.5537


def test_zero_buffer_picks_exactly_the_old_nearest_structural_resistance() -> None:
    levels = [Level(102.0, "prev_day", 1, 0.0, 1.5), Level(101.5, "pivot_high", 1, 0.0, 1.0),
              Level(103.0, "round", 1, 0.0, 0.8), Level(100.5, "orb", 1, 0.0, 1.5)]
    for fill in (100.0, 100.6, 102.5, 104.0):
        old = nearest_structural_resistance(levels, fill)
        new = resistance_target(levels, fill, 0.0)
        assert (new[0] if new else None) == old


def test_a_level_already_reached_after_the_buffer_is_skipped() -> None:
    # 1134.0 buffered = 1132.25, under the 1133.1 fill: price is already at it,
    # so the next level up governs.
    levels = [Level(1134.0, "session_high", 1, 0.0, 1.5),
              Level(1147.0, "session_high", 1, 0.0, 1.5)]
    level, target = resistance_target(levels, 1133.1, 0.15)
    assert level.price == 1147.0 and target == pytest.approx(1145.25)


# ── engine wiring ────────────────────────────────────────────────────────────

def _godrejind_open(cfg: engine.EngineConfig, session_levels: list[Level]) -> engine.DayState:
    state = engine.DayState("GODREJIND", prev_close=1098.2, cum_vol_profile=None,
                            prev_day={"high": 1109.1, "low": 1085.3, "close": 1091.0},
                            session_levels=session_levels)
    setup = engine.Setup("attention_1m_confirmation", trigger=1128.9, stop=1125.7)
    cand = engine.Candidate(
        symbol="GODREJIND", time=pd.Timestamp("2026-09-25 09:31", tz=IST), setup=setup,
        day_chg_pct=3.3, rvol=3.8, catalyst=None, event_type="", candle_tags=[],
    )
    plan = engine.plan_trade(1133.1, 1125.7, risk_inr=250.0, max_notional_inr=50_000.0)
    assert plan is not None and plan.target == pytest.approx(1147.9)
    idx = pd.date_range("2026-09-25 09:24", periods=8, freq="1min", tz=IST)
    bars = pd.DataFrame([(1120.0, 1128.9, 1118.0, 1127.0, 2_000.0)] * 8, index=idx, columns=COLS)
    engine._open_position(state, cand, idx[-1], 1133.1, plan, cfg, bars, None)
    assert state.position is not None
    return state


def test_godrejind_target_moves_under_the_sep_17_high() -> None:
    cfg = engine.EngineConfig(session_level_sessions=10, target_buffer_pct=0.15)
    hist = _history({"2026-09-16": 1172.0, "2026-09-17": 1147.0, "2026-09-24": 1109.1})
    state = _godrejind_open(cfg, engine.build_session_levels(hist, 10))
    pos = state.position
    assert pos.plan.target == pytest.approx(1145.25)
    assert pos.target_source == "structural_resistance:session_high"
    assert pos.exit_state.structural_resistance.price == 1147.0
    # The real 09:35 candle (high 1146.0) now fills the target.
    bar = pd.Series({"open": 1143.4, "high": 1146.0, "low": 1141.2, "close": 1144.7})
    sig = engine.exits.check_target(bar, pos.plan.target, cfg.exit_cfg)
    assert sig is not None and sig.reason == "target" and sig.price == pytest.approx(1145.25)


def test_without_session_levels_the_old_2r_target_stands() -> None:
    # The recorded live trade: nearest known ceiling was the round ₹1,150,
    # above 2R, so no cap.
    state = _godrejind_open(engine.EngineConfig(), [])
    assert state.position.plan.target == pytest.approx(1147.9)
    assert state.position.target_source == "fixed_2r"
    assert state.position.exit_state.structural_resistance.kind == "round"


def test_buffer_alone_can_cap_a_round_number_just_above_2r() -> None:
    # ₹1,150 buffered = ₹1,148.25, still above 2R ₹1,147.9 → no cap.
    state = _godrejind_open(engine.EngineConfig(target_buffer_pct=0.15), [])
    assert state.position.plan.target == pytest.approx(1147.9)


def test_build_session_levels_reads_only_the_last_n_sessions() -> None:
    hist = _history({"2026-09-14": 1190.0, "2026-09-15": 1180.0, "2026-09-16": 1150.0})
    highs = {x.price for x in engine.build_session_levels(hist, 2) if x.kind == "session_high"}
    assert highs == {1180.0, 1150.0}
    assert engine.build_session_levels(hist, 0) == []
    assert engine.build_session_levels(None, 10) == []


def test_session_levels_need_the_structural_target_cap() -> None:
    with pytest.raises(ValueError, match="need use_structural_exit_levels"):
        engine.EngineConfig(use_structural_exit_levels=False, session_level_sessions=10)
    with pytest.raises(ValueError, match="target_buffer_pct"):
        engine.EngineConfig(target_buffer_pct=-0.1)


def test_warrior_strict_turns_the_rule_on() -> None:
    from src.config import Settings
    from src.momentum_trader.scanner import STRATEGY_WARRIOR_STRICT, _strategy_config

    cfg = _strategy_config(Settings(mt_strategy=STRATEGY_WARRIOR_STRICT))
    assert cfg.session_level_sessions == 10
    assert cfg.target_buffer_pct == pytest.approx(0.15)
    # Other arms keep replaying what they were measured with.
    merged = _strategy_config(Settings(mt_strategy="attention_1m_merged"))
    assert merged.session_level_sessions == 0 and merged.target_buffer_pct == 0.0
