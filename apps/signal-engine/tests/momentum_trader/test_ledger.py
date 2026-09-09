"""Durability contracts for the paper forward ledger."""

from __future__ import annotations

import csv

import pandas as pd
from src.momentum_trader.engine import Candidate, ClosedTrade
from src.momentum_trader.ledger import CSV_COLUMNS, PaperLedger
from src.momentum_trader.setups import Setup

IST = "Asia/Kolkata"


def _trade() -> ClosedTrade:
    decision = pd.Timestamp("2026-09-08 10:00:02", tz=IST)
    candidate = Candidate(
        symbol="ABC", time=pd.Timestamp("2026-09-08 09:59", tz=IST),
        setup=Setup(name="ma9_pullback", trigger=100.0, stop=99.0),
        day_chg_pct=5.0, rvol=3.0, catalyst=1, event_type="earnings", candle_tags=[],
        catalyst_status="present", catalyst_source_id="event-1", decision_time=decision,
    )
    return ClosedTrade(
        cand=candidate, entry_time=decision + pd.Timedelta(seconds=1), entry=100.0,
        exit_time=decision + pd.Timedelta(minutes=2), exit=101.0, exit_reason="ema9_break",
        qty=100, gross_inr=100.0, costs_inr=10.0, net_inr=90.0, target=None,
        fill_source="observed_quote", decision_time=decision,
        fill_exchange_time=decision + pd.Timedelta(seconds=1),
        fill_receipt_time=decision + pd.Timedelta(seconds=1),
    )


def test_closed_trade_csv_is_versioned_and_idempotent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "forward.csv"
    ledger = PaperLedger(None, path, float_filter_applied=True)
    trade = _trade()

    ledger.closed(trade)
    ledger.closed(trade)  # retry must not duplicate a forward observation

    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == CSV_COLUMNS
    assert len(rows) == 1
    assert rows[0]["fill_source"] == "observed_quote"
    assert rows[0]["target_px"] == ""
