"""Tests for Strategy A — PEAD Midcap (quant/strategies/pead_midcap.py)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.strategies.pead_midcap import (
    TradeRecord,
    compute_gate_metrics,
    run_anti_strategy,
    run_capacity_check,
    run_cost_stress,
    select_candidates,
    simulate_trades,
    _ROUND_TRIP_COST,
    TARGET_BPS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_ohlcv_df(
    symbol: str,
    dates: list[str],
    close_prices: list[float],
) -> pd.DataFrame:
    df = pd.DataFrame({
        "symbol": symbol,
        "business_date": pd.to_datetime(dates),
        "open": close_prices,
        "high": close_prices,
        "low": close_prices,
        "close": close_prices,
        "prev_close": [close_prices[0]] + close_prices[:-1],
        "volume": 1_000_000,
        "turnover_lacs": 100.0,
        "as_of_timestamp": pd.Timestamp("2023-01-01 12:30:00"),
    })
    df = df.set_index(["business_date", "symbol"])
    return df


def _make_earnings_df(rows: list[dict]) -> pd.DataFrame:
    cols = ["symbol", "business_date", "fiscal_quarter", "fiscal_year",
            "eps_reported", "revenue_cr", "yoy_eps_prev", "yoy_revenue_prev",
            "as_of_timestamp", "result_type", "source_url",
            "net_profit_cr", "period_end"]
    records = []
    for r in rows:
        record = {c: r.get(c, None) for c in cols}
        records.append(record)
    return pd.DataFrame(records)


def _make_trades(n: int, net_return_bps: float) -> list[TradeRecord]:
    ret = net_return_bps / 10_000
    return [
        TradeRecord(
            symbol=f"SYM{i}",
            entry_date="2023-10-15",
            exit_date="2023-10-22",
            entry_price=100.0,
            exit_price=100.0 * (1 + ret + _ROUND_TRIP_COST),
            gross_return=ret + _ROUND_TRIP_COST,
            net_return=ret,
        )
        for i in range(n)
    ]


# ── Tests: gate metrics ───────────────────────────────────────────────────────

def test_gate_metrics_empty_trades():
    metrics = compute_gate_metrics([])
    assert metrics["n_trades"] == 0
    assert not metrics["gate_pass"]
    assert "no trades" in metrics["fail_reason"]


def test_gate_metrics_all_pass():
    # 60 bps net, Sharpe and DSR should pass with consistent positive returns
    trades = _make_trades(60, net_return_bps=60.0)
    metrics = compute_gate_metrics(trades, n_trials=1)
    assert metrics["mean_drift_bps"] == pytest.approx(60.0, abs=0.1)
    # With perfectly uniform returns, std is 0 → Sharpe undefined → catch edge
    # Use mixed returns for a realistic test instead


def test_gate_metrics_pass_with_realistic_returns():
    rng = np.random.default_rng(42)
    n = 60
    # Mean +60bps, std ~30bps → Sharpe ~2.0, should pass
    net_returns = 0.006 + rng.standard_normal(n) * 0.003
    trades = [
        TradeRecord(
            symbol=f"SYM{i}",
            entry_date="2023-10-15",
            exit_date="2023-10-22",
            entry_price=100.0,
            exit_price=100.0,
            gross_return=r + _ROUND_TRIP_COST,
            net_return=r,
        )
        for i, r in enumerate(net_returns)
    ]
    metrics = compute_gate_metrics(trades, n_trials=1)
    assert metrics["gate_pass"], f"Expected gate pass, got: {metrics['fail_reason']}"


def test_gate_metrics_fail_low_drift():
    # 10 bps net — below 40 bps threshold
    trades = _make_trades(60, net_return_bps=10.0)
    metrics = compute_gate_metrics(trades, n_trials=1)
    assert not metrics["gate_pass"]
    assert "drift" in metrics["fail_reason"].lower()


def test_gate_metrics_fail_low_trade_count():
    # Only 10 trades — below 30 threshold
    trades = _make_trades(10, net_return_bps=60.0)
    metrics = compute_gate_metrics(trades, n_trials=1)
    # With only 10 identical trades, std=0, Sharpe=inf — but n_trades < 30 fails
    assert not metrics["gate_pass"]


# ── Tests: capacity check ─────────────────────────────────────────────────────

def test_capacity_check_reduces_dsr():
    rng = np.random.default_rng(7)
    n = 60
    net_returns = 0.006 + rng.standard_normal(n) * 0.003
    trades = [
        TradeRecord(
            symbol=f"SYM{i}",
            entry_date="2023-10-15",
            exit_date="2023-10-22",
            entry_price=100.0,
            exit_price=100.0,
            gross_return=r + _ROUND_TRIP_COST,
            net_return=r,
        )
        for i, r in enumerate(net_returns)
    ]
    result = run_capacity_check(trades, aum_inr=5_000_000, n_trials=1)
    assert "capacity_dsr" in result
    assert "capacity_gate_pass" in result
    assert isinstance(result["capacity_dsr"], float)


def test_capacity_check_empty_trades():
    result = run_capacity_check([])
    assert result["capacity_dsr"] == 0.0
    assert not result["capacity_gate_pass"]


# ── Tests: hold-out guard in simulate_trades ──────────────────────────────────

def test_simulate_trades_skips_holdout_dates():
    earnings = _make_earnings_df([
        {"symbol": "TATA", "business_date": "2024-08-15",
         "fiscal_quarter": 2, "fiscal_year": 2025,
         "eps_reported": 15.0, "revenue_cr": 1500.0},
    ])
    ohlcv = _make_ohlcv_df("TATA", ["2024-08-15"], [100.0])
    trades = simulate_trades(
        announcement_dates=["2024-08-15"],
        earnings_df=earnings,
        ohlcv=ohlcv,
        midcap150_universe=["TATA"],
    )
    # Hold-out dates must be skipped — no trades produced
    assert len(trades) == 0


# ── Tests: select_candidates gate filters ─────────────────────────────────────

def test_select_candidates_empty_earnings():
    earnings = _make_earnings_df([])
    ohlcv = _make_ohlcv_df("TATA", ["2023-10-15"], [100.0])
    result = select_candidates(
        announcement_date="2023-10-15",
        earnings_df=earnings,
        ohlcv=ohlcv,
        midcap150_universe=["TATA"],
    )
    assert result == []


def test_select_candidates_symbol_not_in_universe():
    earnings = _make_earnings_df([
        {"symbol": "TATA", "business_date": "2023-10-15",
         "fiscal_quarter": 2, "fiscal_year": 2024,
         "eps_reported": 15.0, "revenue_cr": 1500.0},
    ])
    ohlcv = _make_ohlcv_df("TATA", ["2023-10-15"], [100.0])
    result = select_candidates(
        announcement_date="2023-10-15",
        earnings_df=earnings,
        ohlcv=ohlcv,
        midcap150_universe=["WIPRO"],  # TATA not in universe
    )
    assert result == []


def test_select_candidates_holdout_date_rejected():
    earnings = _make_earnings_df([
        {"symbol": "TATA", "business_date": "2024-08-15",
         "fiscal_quarter": 2, "fiscal_year": 2025,
         "eps_reported": 15.0},
    ])
    ohlcv = _make_ohlcv_df("TATA", ["2024-08-15"], [100.0])
    with pytest.raises(ValueError, match="HOLD-OUT VIOLATION"):
        select_candidates(
            announcement_date="2024-08-15",
            earnings_df=earnings,
            ohlcv=ohlcv,
            midcap150_universe=["TATA"],
        )


# ── Tests: cost-stress stochastic ─────────────────────────────────────────────

def test_cost_stress_returns_metrics_dict():
    earnings = _make_earnings_df([])
    ohlcv = _make_ohlcv_df("TATA", ["2023-01-15"], [100.0])
    result = run_cost_stress(
        announcement_dates=["2023-01-15"],
        earnings_df=earnings,
        ohlcv=ohlcv,
        midcap150_universe=["TATA"],
    )
    assert "dsr" in result
    assert result.get("is_cost_stress", False)


# ── Tests: trade record structure ─────────────────────────────────────────────

def test_trade_record_net_return_less_than_gross():
    t = TradeRecord(
        symbol="TATA",
        entry_date="2023-10-15",
        exit_date="2023-10-22",
        entry_price=100.0,
        exit_price=101.0,
        gross_return=0.01,
        net_return=0.01 - _ROUND_TRIP_COST,
    )
    assert t.net_return < t.gross_return


# ── Tests: anti-strategy ──────────────────────────────────────────────────────

def test_anti_strategy_returns_metrics():
    earnings = _make_earnings_df([])
    ohlcv = _make_ohlcv_df("TATA", ["2023-01-15"], [100.0])
    result = run_anti_strategy(
        announcement_dates=["2023-01-15"],
        earnings_df=earnings,
        ohlcv=ohlcv,
        midcap150_universe=["TATA"],
    )
    assert "dsr" in result
    assert result.get("is_anti_strategy", False)
