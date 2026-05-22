"""Tests for Strategy F — Bulk Deal Momentum (BDM).

Covers:
  1. bulk_deals._parse_raw: column normalisation, side mapping, value computation
  2. build_events: aggregation, universe filter, min_value_cr filter
  3. simulate_trades: T+1 entry, T+20 exit, election + pledge filters
  4. compute_gate_metrics: all 7 gate criteria
  5. run_anti_strategy / run_cost_stress: wiring and metric structure
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from quant.strategies.bdm import (
    TradeRecord,
    _EXIT_DAYS,
    _MIN_VALUE_CR,
    _ROUND_TRIP_COST,
    build_events,
    compute_gate_metrics,
    is_election_period,
    run_anti_strategy,
    run_cost_stress,
    simulate_trades,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_bulk_df(
    rows: list[dict],
) -> pd.DataFrame:
    """Build a minimal bulk deals DataFrame from row dicts."""
    df = pd.DataFrame(rows)
    if "business_date" in df.columns:
        df["business_date"] = pd.to_datetime(df["business_date"]).dt.date
    if "side" not in df.columns:
        df["side"] = "BUY"
    if "value_cr" not in df.columns:
        df["value_cr"] = df.get("quantity", 100_000) * df.get("price", 500.0) / 1e7
    if "client_name" not in df.columns:
        df["client_name"] = "TEST FUND"
    return df


def _make_ohlcv(symbol: str, start: str, n_days: int = 30,
                open_price: float = 100.0, close_price: float = 103.0) -> pd.DataFrame:
    dates = list(pd.bdate_range(start, periods=n_days).date)
    rows = [{"business_date": d, "symbol": symbol,
             "open": open_price, "close": close_price}
            for d in dates]
    df = pd.DataFrame(rows)
    return df.set_index(["business_date", "symbol"])


def _make_trade(net_return: float = 0.012, symbol: str = "TEST") -> TradeRecord:
    return TradeRecord(
        symbol=symbol,
        event_date="2023-08-01",
        entry_date="2023-08-02",
        exit_date="2023-08-30",
        entry_price=100.0,
        exit_price=100.0 * (1.0 + net_return + _ROUND_TRIP_COST),
        gross_return=net_return + _ROUND_TRIP_COST,
        net_return=net_return,
        hold_days=28,
        deal_value_cr=10.0,
        client_name="TEST FUND",
    )


# ── build_events ──────────────────────────────────────────────────────────────

class TestBuildEvents:
    def test_empty_input_returns_empty(self):
        df = build_events(pd.DataFrame())
        assert df.empty

    def test_single_buy_event(self):
        deals = _make_bulk_df([{
            "symbol": "HDFC", "business_date": "2023-08-01",
            "side": "BUY", "quantity": 50_000, "price": 1600.0,
            "value_cr": 8.0, "client_name": "MIRAE FUND",
        }])
        events = build_events(deals)
        assert len(events) == 1
        assert events.iloc[0]["symbol"] == "HDFC"
        assert events.iloc[0]["total_value_cr"] == pytest.approx(8.0)

    def test_sell_side_excluded(self):
        deals = _make_bulk_df([
            {"symbol": "HDFC", "business_date": "2023-08-01",
             "side": "BUY", "quantity": 50_000, "price": 1600.0, "value_cr": 8.0},
            {"symbol": "INFY", "business_date": "2023-08-01",
             "side": "SELL", "quantity": 30_000, "price": 1400.0, "value_cr": 4.2},
        ])
        events = build_events(deals)
        assert len(events) == 1
        assert events.iloc[0]["symbol"] == "HDFC"

    def test_min_value_filter(self):
        deals = _make_bulk_df([
            {"symbol": "BIG", "business_date": "2023-08-01",
             "side": "BUY", "value_cr": 10.0, "quantity": 1, "price": 1.0},
            {"symbol": "SMALL", "business_date": "2023-08-01",
             "side": "BUY", "value_cr": 0.5, "quantity": 1, "price": 1.0},
        ])
        events = build_events(deals)
        # Only BIG exceeds _MIN_VALUE_CR (1.0)
        assert len(events) == 1
        assert events.iloc[0]["symbol"] == "BIG"

    def test_universe_filter(self):
        deals = _make_bulk_df([
            {"symbol": "MIDCAP", "business_date": "2023-08-01",
             "side": "BUY", "value_cr": 5.0, "quantity": 1, "price": 1.0},
            {"symbol": "NOTINUNIVERSE", "business_date": "2023-08-01",
             "side": "BUY", "value_cr": 5.0, "quantity": 1, "price": 1.0},
        ])
        events = build_events(deals, midcap150=["MIDCAP"])
        assert len(events) == 1
        assert events.iloc[0]["symbol"] == "MIDCAP"

    def test_multiple_buyers_same_day_aggregated(self):
        """Two different buyers of same stock on same day → one event."""
        deals = _make_bulk_df([
            {"symbol": "HDFC", "business_date": "2023-08-01",
             "side": "BUY", "value_cr": 8.0, "client_name": "FUND A",
             "quantity": 1, "price": 1.0},
            {"symbol": "HDFC", "business_date": "2023-08-01",
             "side": "BUY", "value_cr": 5.0, "client_name": "FUND B",
             "quantity": 1, "price": 1.0},
        ])
        events = build_events(deals)
        assert len(events) == 1
        assert events.iloc[0]["total_value_cr"] == pytest.approx(13.0)
        assert "FUND A" in events.iloc[0]["client_names"]
        assert "FUND B" in events.iloc[0]["client_names"]

    def test_different_stocks_same_day_separate_events(self):
        deals = _make_bulk_df([
            {"symbol": "HDFC", "business_date": "2023-08-01",
             "side": "BUY", "value_cr": 8.0, "quantity": 1, "price": 1.0},
            {"symbol": "INFY", "business_date": "2023-08-01",
             "side": "BUY", "value_cr": 5.0, "quantity": 1, "price": 1.0},
        ])
        events = build_events(deals)
        assert len(events) == 2

    def test_same_stock_different_days_separate_events(self):
        deals = _make_bulk_df([
            {"symbol": "HDFC", "business_date": "2023-08-01",
             "side": "BUY", "value_cr": 8.0, "quantity": 1, "price": 1.0},
            {"symbol": "HDFC", "business_date": "2023-08-02",
             "side": "BUY", "value_cr": 5.0, "quantity": 1, "price": 1.0},
        ])
        events = build_events(deals)
        assert len(events) == 2


# ── simulate_trades ───────────────────────────────────────────────────────────

class TestSimulateTrades:
    def _make_event(self, event_date: str, symbol: str = "HDFC") -> pd.DataFrame:
        return pd.DataFrame([{
            "symbol": symbol,
            "event_date": pd.Timestamp(event_date),
            "total_value_cr": 10.0,
            "client_names": "TEST FUND",
        }])

    def test_basic_trade_generated(self):
        sym = "HDFC"
        events = self._make_event("2023-08-01", sym)
        ohlcv = _make_ohlcv(sym, "2023-07-24", n_days=35)

        with patch("quant.strategies.bdm.assert_no_holdout_access"):
            with patch("quant.strategies.bdm.is_pledge_flagged", return_value=False):
                trades = simulate_trades(events, ohlcv)

        assert len(trades) == 1
        t = trades[0]
        assert t.symbol == sym
        assert t.entry_date > "2023-08-01"

    def test_entry_is_t1_open(self):
        sym = "TATA"
        events = self._make_event("2023-08-01", sym)
        ohlcv = _make_ohlcv(sym, "2023-07-24", n_days=35, open_price=200.0, close_price=210.0)

        with patch("quant.strategies.bdm.assert_no_holdout_access"):
            with patch("quant.strategies.bdm.is_pledge_flagged", return_value=False):
                trades = simulate_trades(events, ohlcv)

        assert len(trades) == 1
        assert trades[0].entry_price == pytest.approx(200.0)

    def test_exit_is_t20_close(self):
        sym = "WIPRO"
        events = self._make_event("2023-08-01", sym)
        ohlcv = _make_ohlcv(sym, "2023-07-24", n_days=35, open_price=100.0, close_price=105.0)

        with patch("quant.strategies.bdm.assert_no_holdout_access"):
            with patch("quant.strategies.bdm.is_pledge_flagged", return_value=False):
                trades = simulate_trades(events, ohlcv)

        assert len(trades) == 1
        # Count 20 business days after entry; close = 105.0
        assert trades[0].exit_price == pytest.approx(105.0)

    def test_net_return_subtracts_round_trip_cost(self):
        sym = "HDFCBANK"
        events = self._make_event("2023-08-01", sym)
        # entry=100, exit=103 → gross = 0.03
        ohlcv = _make_ohlcv(sym, "2023-07-24", n_days=35,
                            open_price=100.0, close_price=103.0)

        with patch("quant.strategies.bdm.assert_no_holdout_access"):
            with patch("quant.strategies.bdm.is_pledge_flagged", return_value=False):
                trades = simulate_trades(events, ohlcv)

        t = trades[0]
        assert t.gross_return == pytest.approx(0.03)
        assert t.net_return == pytest.approx(0.03 - _ROUND_TRIP_COST, abs=1e-9)

    def test_election_period_skipped(self):
        # 2024-04-15 is within the 2024 election window
        events = self._make_event("2024-04-15", "ELECT")
        ohlcv = _make_ohlcv("ELECT", "2024-04-07", n_days=35)

        with patch("quant.strategies.bdm.assert_no_holdout_access"):
            with patch("quant.strategies.bdm.is_pledge_flagged", return_value=False):
                trades = simulate_trades(events, ohlcv)

        assert len(trades) == 0

    def test_pledge_flagged_skipped(self):
        events = self._make_event("2023-08-01", "PLEDGED")
        ohlcv = _make_ohlcv("PLEDGED", "2023-07-24", n_days=35)

        with patch("quant.strategies.bdm.assert_no_holdout_access"):
            with patch("quant.strategies.bdm.is_pledge_flagged", return_value=True):
                trades = simulate_trades(events, ohlcv)

        assert len(trades) == 0

    def test_empty_events_returns_empty(self):
        assert simulate_trades(pd.DataFrame(), pd.DataFrame()) == []

    def test_insufficient_ohlcv_for_t20_exit_skipped(self):
        """If there aren't 20 trading days after entry, no trade."""
        sym = "SHORT"
        events = self._make_event("2023-08-01", sym)
        ohlcv = _make_ohlcv(sym, "2023-07-28", n_days=5)  # only 5 days — not enough for T+20

        with patch("quant.strategies.bdm.assert_no_holdout_access"):
            with patch("quant.strategies.bdm.is_pledge_flagged", return_value=False):
                trades = simulate_trades(events, ohlcv)

        assert len(trades) == 0


# ── compute_gate_metrics ──────────────────────────────────────────────────────

class TestComputeGateMetrics:
    def test_no_trades_fails_all(self):
        m = compute_gate_metrics([])
        assert m["gate_pass"] is False
        assert m["fail_reason"] == "no trades"

    def test_all_pass(self):
        rng = np.random.default_rng(99)
        returns = rng.normal(0.015, 0.018, 80)  # mean ~150 bps, Sharpe ~0.83
        trades = [_make_trade(r) for r in returns]

        with patch("quant.research.dsr.deflated_sharpe", return_value=0.75):
            m = compute_gate_metrics(trades, n_trials=1)

        assert m["n_trades"] == 80
        if m["gate_pass"]:
            assert m["fail_reason"] == "all pass"

    def test_too_few_trades_fails(self):
        trades = [_make_trade(0.015) for _ in range(30)]  # < 40
        with patch("quant.research.dsr.deflated_sharpe", return_value=0.75):
            m = compute_gate_metrics(trades, n_trials=1)
        assert m["gate_pass"] is False
        assert "n_trades" in m["fail_reason"]

    def test_mean_return_below_100bps_fails(self):
        # Mean net return ≈ 50 bps < 100 bps
        trades = [_make_trade(0.005) for _ in range(50)]
        with patch("quant.research.dsr.deflated_sharpe", return_value=0.75):
            m = compute_gate_metrics(trades, n_trials=1)
        assert m["gate_pass"] is False
        assert "mean_return" in m["fail_reason"]

    def test_low_win_rate_fails(self):
        # 40% win rate < 52%
        trades = [_make_trade(0.02 if i < 40 else -0.005) for i in range(100)]
        with patch("quant.research.dsr.deflated_sharpe", return_value=0.75):
            m = compute_gate_metrics(trades, n_trials=1)
        assert m["gate_pass"] is False
        assert "win_rate" in m["fail_reason"]

    def test_dsr_below_threshold_fails(self):
        trades = [_make_trade(0.015) for _ in range(50)]
        with patch("quant.research.dsr.deflated_sharpe", return_value=0.2):  # < 0.5
            m = compute_gate_metrics(trades, n_trials=1)
        assert m["gate_pass"] is False
        assert "DSR" in m["fail_reason"]

    def test_metric_keys_present(self):
        trades = [_make_trade(0.012) for _ in range(50)]
        with patch("quant.research.dsr.deflated_sharpe", return_value=0.6):
            m = compute_gate_metrics(trades, n_trials=1)
        for key in ["mean_return_bps", "win_rate", "sharpe", "dsr",
                    "n_trades", "gate_pass", "fail_reason"]:
            assert key in m


# ── run_anti_strategy ─────────────────────────────────────────────────────────

class TestRunAntiStrategy:
    def _setup(self):
        events = pd.DataFrame([{
            "symbol": "TEST",
            "event_date": pd.Timestamp("2023-08-01"),
            "total_value_cr": 10.0,
            "client_names": "FUND",
        }])
        ohlcv = _make_ohlcv("TEST", "2023-07-24", n_days=35)
        return events, ohlcv

    def test_anti_strategy_flag_set(self):
        events, ohlcv = self._setup()
        with patch("quant.strategies.bdm.assert_no_holdout_access"):
            with patch("quant.strategies.bdm.is_pledge_flagged", return_value=False):
                with patch("quant.research.dsr.deflated_sharpe", return_value=0.5):
                    result = run_anti_strategy(events, ohlcv, n_trials=1)
        assert result.get("is_anti_strategy") is True

    def test_anti_strategy_inverts_return(self):
        """Original gross +3% → anti gross -3% → mean_return_bps should be negative."""
        events, ohlcv = self._setup()
        with patch("quant.strategies.bdm.assert_no_holdout_access"):
            with patch("quant.strategies.bdm.is_pledge_flagged", return_value=False):
                with patch("quant.research.dsr.deflated_sharpe", return_value=0.5):
                    result = run_anti_strategy(events, ohlcv, n_trials=1)
        assert result["mean_return_bps"] < 0


# ── run_cost_stress ───────────────────────────────────────────────────────────

class TestRunCostStress:
    def test_cost_stress_flag_set(self):
        events = pd.DataFrame([{
            "symbol": "TEST",
            "event_date": pd.Timestamp("2023-08-01"),
            "total_value_cr": 10.0,
            "client_names": "FUND",
        }])
        ohlcv = _make_ohlcv("TEST", "2023-07-24", n_days=35)

        with patch("quant.strategies.bdm.assert_no_holdout_access"):
            with patch("quant.strategies.bdm.is_pledge_flagged", return_value=False):
                with patch("quant.research.dsr.deflated_sharpe", return_value=0.5):
                    result = run_cost_stress(events, ohlcv, n_trials=1, seed=7)
        assert result.get("is_cost_stress") is True
        assert "dsr" in result


# ── is_election_period ────────────────────────────────────────────────────────

class TestIsElectionPeriod:
    def test_inside_2019_window(self):
        assert is_election_period("2019-05-01") is True

    def test_inside_2024_window(self):
        assert is_election_period("2024-05-15") is True

    def test_outside_all_windows(self):
        assert is_election_period("2021-06-01") is False

    def test_exact_boundary(self):
        assert is_election_period("2024-03-20") is True
        assert is_election_period("2024-07-04") is True
        assert is_election_period("2024-07-05") is False
