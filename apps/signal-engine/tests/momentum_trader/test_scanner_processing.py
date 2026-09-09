"""Scanner-loop invariants that do not require market or database I/O."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import cast

import pandas as pd
from src.momentum_trader import exits
from src.momentum_trader import scanner as scanner_module
from src.momentum_trader.engine import Candidate, DayState, EngineConfig, Pending, Position, step
from src.momentum_trader.risk import plan_trade
from src.momentum_trader.setups import Setup

IST = "Asia/Kolkata"


class _Builder:
    def __init__(self, bars: pd.DataFrame) -> None:
        self._bars = bars

    def closed_bars(self, _key: str, _now: pd.Timestamp) -> pd.DataFrame:
        return self._bars


class _Ledger:
    def acquire_session(self, _session_id: str, _owner: str) -> bool:
        return True

    def candidate(self, _candidate: object) -> None:
        pass

    def opened(self, _position: object) -> None:
        pass

    def closed(self, _trade: object, _round_level: object) -> None:
        pass


def _bars(n: int) -> pd.DataFrame:
    index = pd.date_range("2026-09-08 10:00", periods=n, freq="1min", tz=IST)
    return pd.DataFrame(
        [(100.0, 101.0, 99.0, 100.5, 10.0)] * n,
        index=index,
        columns=["open", "high", "low", "close", "volume"],
    )


def _scanner(
    states: dict[str, DayState], bars: pd.DataFrame, max_positions: int = 5
) -> scanner_module.Scanner:
    scanner = object.__new__(scanner_module.Scanner)
    scanner._ledger = _Ledger()
    scanner._session_id = "test-session"
    scanner._lease_owner = "test-owner"
    scanner._state_lock = threading.RLock()
    scanner._stop = threading.Event()
    scanner._fatal_error = False
    scanner.builder = _Builder(bars)
    scanner.states = states
    scanner.cfg = EngineConfig()
    scanner.s = SimpleNamespace(mt_max_positions=max_positions,
                                mt_total_capital_inr=100000, mt_daily_loss_limit_inr=2500)
    scanner._catalyst = lambda _symbol, _at: (0, "")
    scanner._tg = lambda _text: None
    return scanner


def test_processes_each_unseen_bar_once_and_in_order(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    state = DayState(symbol="ABC", prev_close=100.0, cum_vol_profile=None)
    scanner = _scanner({"abc": state}, _bars(2))
    seen: list[pd.Timestamp] = []

    def fake_step(
        _state: DayState,
        history: pd.DataFrame,
        _cfg: EngineConfig,
        _catalyst: object,
        **_kwargs: object,
    ) -> None:
        seen.append(history.index[-1])

    monkeypatch.setattr(scanner_module, "step", fake_step)
    now = pd.Timestamp("2026-09-08 10:03", tz=IST)
    scanner._process(now)
    scanner._process(now)

    assert seen == list(_bars(2).index)
    assert state.last_processed_bar == _bars(2).index[-1]


def test_drops_pending_before_fill_when_the_slot_cap_is_reached(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pending = DayState(symbol="PENDING", prev_close=100.0, cum_vol_profile=None)
    pending.pending = Pending(cast(Candidate, object()))
    occupied = DayState(symbol="OPEN", prev_close=100.0, cum_vol_profile=None)
    occupied.position = cast(Position, SimpleNamespace(pending_exit=None))
    scanner = _scanner({"pending": pending, "occupied": occupied}, _bars(1), max_positions=1)
    pending_values_seen_by_step: list[object] = []

    def fake_step(
        state: DayState,
        _history: pd.DataFrame,
        _cfg: EngineConfig,
        _catalyst: object,
        **_kwargs: object,
    ) -> None:
        if state.symbol == "PENDING":
            pending_values_seen_by_step.append(state.pending)

    monkeypatch.setattr(scanner_module, "step", fake_step)
    scanner._process(pd.Timestamp("2026-09-08 10:02", tz=IST))

    assert pending_values_seen_by_step == [None]
    assert pending.pending is None


def test_ohlc_stop_is_resolved_before_a_same_bar_breakeven_trail() -> None:
    """OHLC does not establish whether a high happened before a low.

    The conservative convention must leave the pre-existing hard stop active
    for this bar rather than use its high to retroactively lift the stop.
    """
    setup = Setup(name="test", trigger=100.0, stop=99.0)
    candidate = Candidate(
        symbol="ABC",
        time=pd.Timestamp("2026-09-08 10:00", tz=IST),
        setup=setup,
        day_chg_pct=5.0,
        rvol=3.0,
        catalyst=0,
        event_type="",
        candle_tags=[],
    )
    plan = plan_trade(100.0, 99.0, risk_inr=500.0, max_notional_inr=50_000.0)
    assert plan is not None
    state = DayState(symbol="ABC", prev_close=100.0, cum_vol_profile=None)
    state.position = Position(
        cand=candidate,
        entry_time=pd.Timestamp("2026-09-08 10:00", tz=IST),
        plan=plan,
        highest=100.0,
        exit_state=exits.initial_state(entry=100.0, hard_stop=99.0),
    )
    bar = pd.DataFrame(
        [(100.0, 101.1, 98.8, 100.0, 10.0)],
        index=pd.DatetimeIndex([pd.Timestamp("2026-09-08 10:01", tz=IST)]),
        columns=["open", "high", "low", "close", "volume"],
    )

    step(state, bar, EngineConfig(exit_mode=exits.MODE_TREND_MIN), lambda _symbol, _at: (0, ""))

    assert state.position is None
    assert state.closed[0].exit_reason == "stop"
    assert state.closed[0].exit == 99.0
    assert state.closed[0].target is None
