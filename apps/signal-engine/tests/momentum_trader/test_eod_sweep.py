"""The 15:15 close must hold even when a symbol stops printing.

`step()` exits on bars that ARRIVE and reads the clock off the last bar's own
timestamp, so a position in a name whose last trade was before 15:15 never
reaches the `eod_close` branch. This strategy is intraday-only (MIS costs, no
overnight carry), so the scanner sweeps on the wall clock instead.
"""

from __future__ import annotations

import pandas as pd
from src.momentum_trader import engine as eng
from src.momentum_trader import scanner as scn

IST = "Asia/Kolkata"


def _state_with_open_position() -> eng.DayState:
    st = eng.DayState(symbol="QUIET", prev_close=100.0, cum_vol_profile=None)
    setup = eng.Setup(name="attention_1m_confirmation", trigger=105.0, stop=104.0, level=105.0)
    cand = eng.Candidate(
        symbol="QUIET", time=pd.Timestamp("2024-06-03 11:00", tz=IST), setup=setup,
        day_chg_pct=5.0, rvol=3.0, catalyst=0, event_type="", candle_tags=[],
    )
    plan = eng.plan_trade(105.0, 104.0, risk_inr=500.0, max_notional_inr=50_000.0)
    assert plan is not None
    st.position = eng.Position(
        cand=cand, entry_time=cand.time, plan=plan, highest=105.0,
        exit_state=eng.exits.initial_state(
            entry=105.0, hard_stop=104.0,
            bars_tf=pd.DataFrame(columns=["open", "high", "low", "close", "volume"]),
            prev_day=None, with_levels=False,
        ),
    )
    return st


def test_step_alone_cannot_close_a_symbol_that_stopped_printing() -> None:
    """The gap the sweep exists for: last bar 15:05, so `t` never reaches 15:14."""
    st = _state_with_open_position()
    cfg = eng.EngineConfig(stress_slip=0.0)
    idx = pd.DatetimeIndex([pd.Timestamp("2024-06-03 15:05", tz=IST)])
    bars = pd.DataFrame([{"open": 106.0, "high": 106.1, "low": 105.9, "close": 106.0,
                          "volume": 1000.0}], index=idx)
    eng.step(st, bars, cfg, lambda _s, _t: (0, ""))
    assert st.position is not None
    assert st.closed == []


def test_force_close_marks_the_trade_and_tags_it() -> None:
    st = _state_with_open_position()
    cfg = eng.EngineConfig(stress_slip=0.0)
    when = pd.Timestamp("2024-06-03 15:16", tz=IST)
    trade = eng.force_close(st, when, 106.0, cfg)
    assert trade is not None
    assert st.position is None
    assert trade.exit_reason == "eod_sweep"
    assert trade.exit == 106.0
    assert trade.gross_inr == (106.0 - 105.0) * trade.qty
    # costs are real MIS costs, so net is strictly below gross
    assert trade.net_inr < trade.gross_inr


def test_force_close_is_a_no_op_without_a_position() -> None:
    st = eng.DayState(symbol="FLAT", prev_close=100.0, cum_vol_profile=None)
    cfg = eng.EngineConfig()
    assert eng.force_close(st, pd.Timestamp("2024-06-03 15:16", tz=IST), 100.0, cfg) is None
    assert st.closed == []


class _FakeBuilder:
    def __init__(self, price: float | None) -> None:
        self._price = price

    def latest_close(self, _key: str) -> float | None:
        return self._price


class _FakeScanner:
    """Exercises Scanner._eod_sweep without any Upstox/Mongo wiring."""

    _eod_sweep = scn.Scanner._eod_sweep

    def __init__(self, st: eng.DayState, price: float | None) -> None:
        import threading
        self.states = {"NSE_EQ|QUIET": st}
        self.builder = _FakeBuilder(price)
        self.cfg = eng.EngineConfig(stress_slip=0.0)
        self._state_lock = threading.Lock()


def test_sweep_does_nothing_before_the_cutoff() -> None:
    st = _state_with_open_position()
    sc = _FakeScanner(st, 106.0)
    assert sc._eod_sweep(pd.Timestamp("2024-06-03 15:15", tz=IST), {}) == []
    assert st.position is not None


def test_sweep_closes_the_stranded_position_at_the_last_seen_price() -> None:
    st = _state_with_open_position()
    sc = _FakeScanner(st, 106.0)
    swept = sc._eod_sweep(pd.Timestamp("2024-06-03 15:16", tz=IST), {})
    assert len(swept) == 1
    assert st.position is None
    assert swept[0].exit_reason == "eod_sweep"
    assert swept[0].exit == 106.0


def test_sweep_falls_back_to_the_last_bar_when_no_tick_price_is_held() -> None:
    st = _state_with_open_position()
    sc = _FakeScanner(st, None)
    idx = pd.DatetimeIndex([pd.Timestamp("2024-06-03 15:05", tz=IST)])
    bars = pd.DataFrame([{"open": 106.0, "high": 106.1, "low": 105.9, "close": 106.5,
                          "volume": 1000.0}], index=idx)
    swept = sc._eod_sweep(pd.Timestamp("2024-06-03 15:16", tz=IST), {"NSE_EQ|QUIET": bars})
    assert len(swept) == 1
    assert swept[0].exit == 106.5


def test_sweep_leaves_the_position_open_when_it_cannot_be_priced() -> None:
    """Better a loud, reconcilable open position than an invented exit price."""
    st = _state_with_open_position()
    sc = _FakeScanner(st, None)
    assert sc._eod_sweep(pd.Timestamp("2024-06-03 15:16", tz=IST), {}) == []
    assert st.position is not None


def test_sweep_runs_after_the_ordinary_eod_close_has_had_its_chance() -> None:
    """15:15 bar-driven close first, 15:16 sweep second — so a printing symbol
    exits at its real 15:15 price and never reaches the sweep."""
    assert scn.EOD_SWEEP > (15, 15)
    assert eng.EOD_CLOSE.hour == 15 and eng.EOD_CLOSE.minute == 14   # bar START; closes 15:15
