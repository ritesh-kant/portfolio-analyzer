"""Scanner-level wiring for the daily guardrails.

`test_discipline.py` proves the state machine. This file proves the scanner
consults it — that a halted day actually cancels armed entries, and that the
size ladder actually reaches `plan_trade` through `cfg.risk_inr`. A correct
state machine nobody calls would pass every test in the other file.

The scanner is constructed without running `__init__`, so no Upstox client,
network, token or Mongo connection is involved.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.config import Settings
from src.momentum_trader.discipline import (
    HALT_GIVEBACK,
    HALT_THREE_STRIKES,
    DayDiscipline,
    DisciplineConfig,
)
from src.momentum_trader.engine import (
    Candidate,
    ClosedTrade,
    DayState,
    Pending,
    Position,
    Setup,
)
from src.momentum_trader.scanner import (
    STRATEGY_WARRIOR_STRICT,
    Scanner,
    _strategy_config,
    entry_message,
)

IST = "Asia/Kolkata"
NOW = pd.Timestamp("2026-09-15 10:00", tz=IST)


def _scanner(enabled: bool = True, risk_inr: float = 500.0) -> Scanner:
    settings = Settings(mt_strategy=STRATEGY_WARRIOR_STRICT, mt_risk_inr=risk_inr,
                        mt_discipline=enabled)
    s = Scanner.__new__(Scanner)
    s.s = settings
    s.cfg = _strategy_config(settings)
    s.discipline = DayDiscipline(DisciplineConfig(enabled=enabled))
    s._full_risk_inr = risk_inr
    s._sync_risk()
    return s


def _pending_state() -> DayState:
    setup = Setup(name="attention_1m_confirmation", trigger=101.0, stop=100.0, level=101.0)
    cand = Candidate(symbol="TEST", time=NOW, setup=setup, day_chg_pct=3.0, rvol=2.0,
                     catalyst=0, event_type="", candle_tags=[])
    st = DayState(symbol="TEST", prev_close=100.0, cum_vol_profile=None)
    st.pending = Pending(cand, decision_time=NOW)
    return st


def _closed(net_inr: float) -> ClosedTrade:
    setup = Setup(name="attention_1m_confirmation", trigger=101.0, stop=100.0)
    cand = Candidate(symbol="TEST", time=NOW, setup=setup, day_chg_pct=3.0, rvol=2.0,
                     catalyst=0, event_type="", candle_tags=[])
    return ClosedTrade(cand=cand, entry_time=NOW, entry=101.0, exit_time=NOW,
                       exit=101.0, exit_reason="stop", qty=1,
                       gross_inr=net_inr, costs_inr=0.0, net_inr=net_inr)


# ── halts cancel armed entries ───────────────────────────────────────────────

def test_an_armed_buy_stop_is_cancelled_once_the_day_is_halted():
    s = _scanner()
    st = _pending_state()
    assert not s._guard_pending(st, NOW, 101.5), "not halted yet"
    assert st.pending is not None

    for _ in range(3):
        s._record_close(_closed(-100.0))
    assert s.discipline.halted_reason == HALT_THREE_STRIKES

    assert s._guard_pending(st, NOW, 101.5) is True
    assert st.pending is None, "a stop armed before the halt must not fill after it"
    assert st.rejections[-1].reason == f"halted:{HALT_THREE_STRIKES}"
    assert st.rejections[-1].observed_price == pytest.approx(101.5)


def test_giveback_halt_also_cancels():
    s = _scanner()
    s._record_close(_closed(1_000.0))
    s._record_close(_closed(-600.0))
    assert s.discipline.halted_reason == HALT_GIVEBACK
    st = _pending_state()
    assert s._guard_pending(st, NOW) is True


def test_guard_is_a_no_op_with_nothing_armed():
    s = _scanner()
    st = DayState(symbol="TEST", prev_close=100.0, cum_vol_profile=None)
    for _ in range(3):
        s._record_close(_closed(-100.0))
    assert s._guard_pending(st, NOW) is False
    assert not st.rejections


def test_disabled_guardrails_never_cancel():
    s = _scanner(enabled=False)
    for _ in range(10):
        s._record_close(_closed(-1_000.0))
    st = _pending_state()
    assert s._guard_pending(st, NOW) is False
    assert st.pending is not None


# ── the size ladder reaches the engine ───────────────────────────────────────

def test_the_engine_opens_the_day_at_starter_size():
    s = _scanner(risk_inr=500.0)
    assert s.cfg.risk_inr == pytest.approx(250.0)


def test_a_loss_shrinks_the_engines_risk_and_a_cushion_restores_it():
    s = _scanner(risk_inr=500.0)
    s._record_close(_closed(-100.0))
    assert s.cfg.risk_inr == pytest.approx(125.0)
    s._record_close(_closed(1_000.0))          # day is green again
    assert s.cfg.risk_inr == pytest.approx(250.0)
    s._record_close(_closed(1_000.0))
    assert s.cfg.risk_inr == pytest.approx(500.0), "full size after the cushion"


def test_disabled_guardrails_leave_risk_at_the_configured_full_size():
    s = _scanner(enabled=False, risk_inr=500.0)
    assert s.cfg.risk_inr == pytest.approx(500.0)
    s._record_close(_closed(-100.0))
    assert s.cfg.risk_inr == pytest.approx(500.0)


def test_closes_are_folded_in_chronological_order():
    """'3 consecutive losses' must mean what it says when trades close together."""
    s = _scanner()
    trades = [_closed(-100.0), _closed(500.0), _closed(-100.0)]
    for i, t in enumerate(trades):
        object.__setattr__(t, "exit_time", NOW + pd.Timedelta(minutes=i))
    for t in sorted(trades, key=lambda x: x.exit_time):
        s._record_close(t)
    assert s.discipline.consecutive_losses == 1, "the winner in the middle resets"
    assert not s.discipline.halted


def _iks_position(symbol: str = "IKS") -> Position:
    from src.momentum_trader.exits import ExitState
    from src.momentum_trader.risk import TradePlan

    setup = Setup(name="attention_1m_confirmation", trigger=1935.10, stop=1921.40)
    cand = Candidate(symbol=symbol, time=NOW, setup=setup, day_chg_pct=5.0, rvol=3.0,
                     catalyst=0, event_type="", candle_tags=[])
    plan = TradePlan(entry=1935.10, stop=1921.40, target=1962.50, qty=18,
                     risk_inr=246.6, reward_inr=493.2, notional_inr=34831.8)
    return Position(cand=cand, entry_time=NOW, plan=plan, highest=1935.10,
                    exit_state=ExitState.__new__(ExitState), target_source="disabled")


def test_entry_message_shows_target_on_its_own_line():
    msg = entry_message(_iks_position(), fixed_exit=False)
    lines = msg.split("\n")
    assert lines[0] == "🟢 <b>ENTER IKS</b>"
    assert "Stop: <b>₹1,921.40</b> (-0.71%, risk ₹247)" in lines
    target = next(line for line in lines if line.startswith("Target:"))
    assert "<b>₹1,962.50</b>" in target and "2.0R" in target
    assert "reference only" in target, "a signal exit must not imply a resting target"


def test_entry_message_fixed_exit_names_the_target_source():
    msg = entry_message(_iks_position(), fixed_exit=True)
    assert "reference only" not in msg


def test_entry_message_escapes_symbols_for_telegram_html():
    assert "ENTER M&amp;M" in entry_message(_iks_position("M&M"), fixed_exit=False)
