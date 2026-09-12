"""The narrow pattern experiment must not assume favourable stop fills."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest
from src.momentum_trader.candles import PatternMatch


@pytest.mark.parametrize("next_open,expected_exit", [(97.0, 97.0), (99.0, 98.0)])
def test_pattern_replay_stop_fills_at_stop_or_worse(monkeypatch, next_open, expected_exit):
    path = (
        Path(__file__).resolve().parents[4]
        / "research/backtests/bt26_candlestick_pattern_replay.py"
    )
    spec = importlib.util.spec_from_file_location("pattern_replay_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    # The standalone script adjusts sys.path; preserve the test process state.
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec.loader.exec_module(module)
    bars = pd.DataFrame(
        [(100, 101, 99, 100.5, 1000), (next_open, 101, 96, 98, 1000)],
        columns=["open", "high", "low", "close", "volume"],
        index=pd.date_range("2026-09-11 10:00", periods=2, freq="1min", tz="Asia/Kolkata"),
    )
    match = PatternMatch(
        "morning_star", "5m", "2026-09-11T09:45:00+05:30", "2026-09-11T09:55:00+05:30", 100, 98
    )
    trade = module._trade_day(bars, match)
    assert trade is not None
    assert trade.entry_at == bars.index[0].isoformat()
    assert trade.reason == "stop"
    assert trade.exit == expected_exit
    assert trade.costs_inr > 0
