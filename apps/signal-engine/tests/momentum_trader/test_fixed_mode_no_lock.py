"""The fixed-target mode must have exactly four exits: false_break, stop, target,
15:15 close (spec §4). Regression test for the breakeven lock that leaked into
`fixed_2r` via `update_high` between 2026-09-05 and 2026-09-06.
"""

from __future__ import annotations

import pandas as pd
from src.momentum_trader import exits

IST = "Asia/Kolkata"


def _bar(open_: float, high: float, low: float, close: float) -> pd.Series:
    return pd.Series({"open": open_, "high": high, "low": low, "close": close, "volume": 1000.0})


def _state() -> exits.ExitState:
    # entry 100, hard stop 99 → 1R = ₹1
    return exits.initial_state(entry=100.0, hard_stop=99.0)


def test_fixed_mode_never_arms_and_never_locks_breakeven() -> None:
    cfg = exits.ExitConfig.for_mode(exits.MODE_FIXED)
    st = _state()
    exits.update_high(st, bar_high=101.5, cfg=cfg)     # 1.5R of open profit
    assert st.armed is False
    assert st.trail == st.hard_stop == 99.0            # stop has NOT moved to entry


def test_trend_mode_still_locks_breakeven_at_1r() -> None:
    """The lock is a trend-mode feature and must keep working there."""
    cfg = exits.ExitConfig.for_mode(exits.MODE_TREND_MIN)
    st = _state()
    exits.update_high(st, bar_high=101.5, cfg=cfg)
    assert st.armed is True
    assert st.trail == 100.0


def test_fixed_mode_pullback_to_entry_does_not_exit() -> None:
    """After a 1.5R excursion, price coming back to 99.5 is still above the hard
    stop. Fixed mode holds; a leaked breakeven lock would have fired trail_stop."""
    cfg = exits.ExitConfig.for_mode(exits.MODE_FIXED)
    st = _state()
    exits.update_high(st, bar_high=101.5, cfg=cfg)
    assert exits.check_stop(st, _bar(100.2, 100.3, 99.5, 99.6)) is None


def test_fixed_mode_exit_reasons_are_only_the_four_in_the_spec() -> None:
    cfg = exits.ExitConfig.for_mode(exits.MODE_FIXED)
    st = _state()
    exits.update_high(st, bar_high=101.5, cfg=cfg)
    sig = exits.check_stop(st, _bar(99.4, 99.5, 98.8, 98.9))
    assert sig is not None and sig.reason == "stop"    # never "trail_stop"
    tgt = exits.check_target(_bar(101.0, 102.2, 100.9, 102.0), target=102.0, cfg=cfg)
    assert tgt is not None and tgt.reason == "target"
