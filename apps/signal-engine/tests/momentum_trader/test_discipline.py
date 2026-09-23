"""Daily guardrails: 3 strikes, 50% give-back, starter size, size down on a loss.

These are the transcript's account-level rules, so every test here is phrased in
terms of a sequence of closed-trade P&L values rather than of bars.
"""

from __future__ import annotations

import pytest
from src.momentum_trader.discipline import (
    FULL_SIZE_FRAC,
    HALT_GIVEBACK,
    HALT_THREE_STRIKES,
    DayDiscipline,
    DisciplineConfig,
)


def _day(**kw) -> DayDiscipline:
    return DayDiscipline(DisciplineConfig(enabled=True, **kw))


# ── 3-strikes ────────────────────────────────────────────────────────────────

def test_three_consecutive_losses_stop_the_day():
    d = _day()
    for _ in range(2):
        d.record(-100.0)
        assert d.can_trade() == (True, "ok")
    d.record(-100.0)
    assert d.can_trade() == (False, HALT_THREE_STRIKES)


def test_a_winner_resets_the_strike_count():
    d = _day()
    d.record(-100.0)
    d.record(-100.0)
    d.record(50.0)
    assert d.consecutive_losses == 0
    d.record(-100.0)
    d.record(-100.0)
    assert d.can_trade()[0], "two fresh losses are not three"
    d.record(-100.0)
    assert d.can_trade() == (False, HALT_THREE_STRIKES)


def test_a_halt_is_permanent_for_the_day():
    d = _day()
    for _ in range(3):
        d.record(-100.0)
    d.record(10_000.0)
    assert d.can_trade() == (False, HALT_THREE_STRIKES), "no trading back out of it"


# ── 50% give-back ────────────────────────────────────────────────────────────

def test_giving_back_half_of_peak_profit_stops_the_day():
    d = _day()
    d.record(1_000.0)
    assert d.peak_inr == 1_000.0
    d.record(-400.0)                     # net 600 > 500
    assert d.can_trade() == (True, "ok")
    d.record(-150.0)                     # net 450 <= 500
    assert d.can_trade() == (False, HALT_GIVEBACK)


def test_a_day_that_is_never_green_cannot_trip_the_giveback_rule():
    d = _day()
    d.record(-500.0)
    d.record(-500.0)
    assert d.halted_reason != HALT_GIVEBACK, "you cannot give back what you never made"


def test_giveback_boundary_is_inclusive():
    d = _day()
    d.record(1_000.0)
    d.record(-500.0)                     # exactly half given back
    assert d.can_trade() == (False, HALT_GIVEBACK)


# ── size ladder ──────────────────────────────────────────────────────────────

def test_the_day_opens_at_starter_size():
    d = _day()
    assert d.risk_inr(500.0) == pytest.approx(250.0)


def test_full_size_only_after_a_green_cushion():
    d = _day()
    d.record(300.0)                      # a winner AND the day is green
    assert d.risk_inr(500.0) == pytest.approx(500.0)


def test_a_winner_that_leaves_the_day_red_does_not_earn_full_size():
    d = _day()
    d.record(-400.0)                     # size 25%
    d.record(100.0)                      # a winner, but the day is still -300
    assert d.net_inr == pytest.approx(-300.0)
    assert d.risk_inr(500.0) == pytest.approx(125.0), "no cushion, no step up"


def test_size_always_falls_after_a_loss_and_never_reaches_zero():
    d = _day(max_consecutive_losses=99)   # isolate sizing from the halt
    seen = [d.risk_inr(500.0)]
    for _ in range(6):
        d.record(-10.0)
        seen.append(d.risk_inr(500.0))
    assert seen == sorted(seen, reverse=True), "size must be non-increasing"
    assert seen[-1] == pytest.approx(125.0), "floored at min_size_frac"
    assert seen[-1] > 0.0


def test_size_never_exceeds_full():
    d = _day()
    for _ in range(5):
        d.record(1_000.0)
    assert d.risk_inr(500.0) == pytest.approx(500.0)


def test_a_scratch_counts_as_a_loss():
    d = _day()
    d.record(0.0)
    assert d.consecutive_losses == 1
    assert d.risk_inr(500.0) == pytest.approx(125.0)


# ── disabled is a true no-op ─────────────────────────────────────────────────

def test_disabled_never_halts_and_never_resizes():
    d = DayDiscipline(DisciplineConfig(enabled=False))
    for _ in range(10):
        d.record(-1_000.0)
    assert d.can_trade() == (True, "ok")
    assert d.risk_inr(500.0) == pytest.approx(500.0)
    assert d.size_frac == FULL_SIZE_FRAC
    # Bookkeeping still runs, so the numbers are reportable either way.
    assert d.trades == 10 and d.net_inr == pytest.approx(-10_000.0)


# ── configuration is validated, not silently coerced ─────────────────────────

@pytest.mark.parametrize("kw", [
    {"max_consecutive_losses": 0},
    {"giveback_frac": 0.0},
    {"giveback_frac": 1.5},
    {"starter_frac": 0.1, "min_size_frac": 0.5},
])
def test_invalid_configuration_raises(kw):
    with pytest.raises(ValueError):
        DisciplineConfig(enabled=True, **kw)


def test_summary_mentions_the_halt():
    d = _day()
    for _ in range(3):
        d.record(-100.0)
    assert HALT_THREE_STRIKES in d.summary()


def test_giveback_halt_can_be_switched_off_alone():
    d = _day(giveback_halt=False)
    d.record(196.0)
    d.record(-267.0)            # the 2026-09-23 day: would have tripped the give-back
    assert d.can_trade() == (True, "ok")
    d.record(-10.0)
    d.record(-10.0)
    assert d.can_trade() == (False, HALT_THREE_STRIKES), "strikes rule still on"
