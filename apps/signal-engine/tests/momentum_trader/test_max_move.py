"""Strict max-move checklist guards (hypothesis 2026-09-07)."""

from __future__ import annotations

from src.momentum_trader import engine as eng
from src.momentum_trader.setups import Setup


def _gate(setup: Setup, *, macd: float | None = 0.1, round_dist: float | None = 0.2,
          resistance: float | None = 0.5, support: float | None = 0.2) -> tuple[bool, str]:
    return eng._max_move_gate(setup, macd, round_dist, resistance, support,
                              eng.EngineConfig(require_max_move=True))


def test_max_move_gate_is_off_by_default() -> None:
    setup = Setup("micro_pullback", 100.0, 99.0)
    assert eng._max_move_gate(setup, None, None, None, None, eng.EngineConfig()) == (True, "ok")


def test_max_move_gate_requires_each_operator_condition() -> None:
    good = Setup("ma9_pullback", 100.0, 99.0)
    assert _gate(good) == (True, "ok")
    assert _gate(Setup("micro_pullback", 100.0, 99.0)) == (False, "micro_excluded")
    assert _gate(good, macd=0.0) == (False, "macd_not_positive")
    assert _gate(good, round_dist=0.10) == (False, "near_round")
    assert _gate(good, resistance=0.35) == (False, "near_resistance")
    assert _gate(good, support=0.36) == (False, "not_near_support")


def test_first_candidate_only_defaults_off() -> None:
    assert eng.EngineConfig().first_candidate_only is False
