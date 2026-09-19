"""Phase-aware research evidence must not change the active entry gate."""

import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
from src.momentum_trader import engine
from src.momentum_trader.ledger import PaperLedger
from src.momentum_trader.volume_confirmation import volume_confirmation_evidence


def pullback(volumes=(10_000, 8_000, 1_000, 6_000)):
    rows = [(100, 100.1, 99.9, 100, 100)] * 20
    rows += [
        (100, 100.35, 99.95, 100.3, volumes[0]),
        (100.3, 100.65, 100.25, 100.6, volumes[1]),
        (100.6, 100.65, 100.35, 100.45, volumes[2]),
        (100.45, 100.85, 100.4, 100.8, volumes[3]),
    ]
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"],
                        index=pd.date_range("2026-09-18 10:00", periods=len(rows),
                                            freq="min", tz="Asia/Kolkata"))


def research(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[4] / "research/backtests"))
    return importlib.import_module("bt40_pullback_volume")


def test_light_pause_can_confirm_while_four_bar_volume_falls(monkeypatch):
    bars = pullback()
    evidence = volume_confirmation_evidence(bars)
    assert evidence["pattern"] == "micro_pullback"
    assert evidence["pattern_pass"] is True
    assert evidence["slope_pass"] is False
    assert evidence["breakout_to_pause_volume"] == 6
    json.dumps(evidence, allow_nan=False)
    live, reason = engine._attention_confirmation(bars, 2.5, require_rising_price_volume=True)
    assert live is None and reason == "attention_price_volume_not_confirmed"
    adapter = research(monkeypatch).comparison_confirmation(
        engine._attention_confirmation, "alternative", [])
    candidate, reason = adapter(bars, 2.5, require_rising_price_volume=True)
    assert candidate is not None, reason
    unchanged, _ = engine._attention_confirmation(bars, 2.5)
    assert candidate.trigger == unchanged.trigger
    assert candidate.stop == unchanged.stop


@pytest.mark.parametrize("volumes", [(1000, 1000, 900, 6000), (10000, 8000, 1000, 1000)])
def test_heavy_pause_or_no_expansion_fails(volumes):
    assert volume_confirmation_evidence(pullback(volumes))["pattern_pass"] is False


def test_wick_without_breakout_close_fails():
    bars = pullback()
    bars.iloc[-1, bars.columns.get_loc("close")] = 100.6
    assert volume_confirmation_evidence(bars)["pattern_pass"] is False


def test_missing_minute_and_previous_session_cannot_form_pattern():
    bars = pullback()
    assert volume_confirmation_evidence(bars.drop(bars.index[-2]))["pattern"] == "none"
    idx = bars.index.to_list()
    idx[-1] += pd.Timedelta(days=1)
    bars.index = pd.DatetimeIndex(idx)
    evidence = volume_confirmation_evidence(bars)
    assert evidence["pattern"] == "none"
    assert "slope_pass" not in evidence
    assert len(evidence["bars"]) == 1


def test_nonfinite_volume_is_not_confirmed():
    bars = pullback()
    bars.loc[bars.index[-1], "volume"] = float("nan")
    evidence = volume_confirmation_evidence(bars)
    assert evidence["data_error"] == "nonfinite"
    assert evidence["pattern_pass"] is False
    json.dumps(evidence, allow_nan=False)


def test_existing_above_average_volume_requirement_is_preserved(monkeypatch):
    bars = pullback((100, 100, 10, 110))
    assert volume_confirmation_evidence(bars)["pattern_pass"] is True
    adapter = research(monkeypatch).comparison_confirmation(
        engine._attention_confirmation, "alternative", [])
    candidate, reason = adapter(bars, 2.5, require_rising_price_volume=True)
    assert candidate is None and reason == "attention_low_1m_volume"


def test_no_pattern_uses_original_slope_and_does_not_mutate_engine(monkeypatch):
    bars = pullback()
    bars.loc[bars.index[-2], "open"] = 100.3  # green continuation, no pause
    module = research(monkeypatch)
    original = engine._attention_confirmation
    adapted = module.comparison_confirmation(original, "alternative", [])
    assert adapted(bars, 2.5, require_rising_price_volume=True)[0] is None
    assert engine._attention_confirmation is original


def test_rejection_evidence_persists():
    bars = pullback()
    evidence = volume_confirmation_evidence(bars)
    db = MagicMock()
    ledger = PaperLedger(db, None, False)
    rejection = engine.Rejection("TEST", bars.index[-1], "attention_price_volume_not_confirmed",
                                 "attention", 100.85, evidence=evidence)
    ledger.rejected(rejection)
    doc = db["mt_rejections"].insert_one.call_args.args[0]
    assert doc["evidence"] == evidence


def test_forward_outcomes_are_separate_and_never_cross_session(monkeypatch):
    module = research(monkeypatch)
    bars = pullback()
    row = {"time": bars.index[-6].isoformat(), "close": float(bars["close"].iloc[-6])}
    module.add_forward_returns([row], bars)
    assert row["forward_5m_pct"] == pytest.approx(0.8)
    assert row["forward_15m_pct"] is None


def test_complete_session_handles_parquet_timestamp_precision(monkeypatch):
    module = research(monkeypatch)
    idx = pd.date_range("2026-09-18 09:15", periods=375, freq="min", tz="Asia/Kolkata")
    bars = pd.DataFrame(100.0, index=idx.as_unit("us"),
                        columns=["open", "high", "low", "close", "volume"])
    assert module.complete_session(bars, idx[0].normalize())
    assert not module.complete_session(bars.iloc[:-1], idx[0].normalize())
