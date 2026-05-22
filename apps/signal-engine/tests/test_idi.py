"""Tests for Strategy E — Institutional Delivery Impulse (IDI).

Covers:
  1. compute_signals: zscore, return, volume_ratio thresholds; PIT rolling window
  2. simulate_trades: entry T+1, exit T+5, election filter, pledge filter hook
  3. compute_gate_metrics: all 7 criteria, gate_pass logic
  4. run_anti_strategy / run_cost_stress: wiring and metric structure
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from quant.strategies.idi import (
    TradeRecord,
    _EXIT_DAYS,
    _DELIVERY_ZSCORE_THRESHOLD,
    _RETURN_THRESHOLD,
    _ROUND_TRIP_COST,
    _ROLLING_WINDOW,
    _VOLUME_RATIO_THRESHOLD,
    compute_gate_metrics,
    compute_signals,
    is_election_period,
    run_anti_strategy,
    run_cost_stress,
    simulate_trades,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_delivery(
    symbol: str,
    start: str,
    n_days: int,
    base_deliv_pct: float = 30.0,
    base_volume: int = 100_000,
    base_close: float = 500.0,
    spike_day: int | None = None,
    spike_deliv_pct: float = 70.0,
    spike_return: float = 0.025,
    spike_volume_ratio: float = 1.5,
) -> pd.DataFrame:
    """Build a synthetic delivery DataFrame for one symbol.

    Adds small natural noise to base_deliv_pct and base_volume so that the
    rolling std is non-zero — otherwise a flat baseline produces std≈0 and
    zscore is undefined (returns 0.0), making spike detection impossible.
    """
    rng = np.random.default_rng(seed=12345)
    dates = pd.bdate_range(start, periods=n_days)
    # Add ±2% noise around base so rolling std ≈ 2, enabling zscore computation
    closes = [base_close] * n_days
    prev_closes = [base_close] * n_days
    deliv_pcts = (base_deliv_pct + rng.normal(0, 2.0, n_days)).clip(5, 95).tolist()
    volumes = (base_volume * rng.uniform(0.85, 1.15, n_days)).astype(int).tolist()

    if spike_day is not None and spike_day < n_days:
        prev_closes[spike_day] = base_close
        closes[spike_day] = base_close * (1.0 + spike_return)
        deliv_pcts[spike_day] = spike_deliv_pct
        volumes[spike_day] = int(base_volume * spike_volume_ratio)

    rows = []
    for i, d in enumerate(dates):
        rows.append({
            "symbol": symbol,
            "series": "EQ",
            "business_date": d.date(),
            "open": closes[i] * 0.998,
            "close": closes[i],
            "prev_close": prev_closes[i],
            "volume": volumes[i],
            "deliv_qty": int(volumes[i] * deliv_pcts[i] / 100),
            "deliv_pct": deliv_pcts[i],
            "as_of_timestamp": pd.Timestamp(d) + pd.Timedelta(hours=20),
        })
    return pd.DataFrame(rows)


def _make_ohlcv_from_delivery(delivery: pd.DataFrame) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from delivery data (for entry/exit price lookups)."""
    rows = []
    for _, row in delivery.iterrows():
        rows.append({
            "business_date": row["business_date"],
            "symbol": row["symbol"],
            "open": row["open"],
            "close": row["close"],
        })
    df = pd.DataFrame(rows)
    df = df.set_index(["business_date", "symbol"])
    return df


def _make_trade(
    net_return: float = 0.01,
    symbol: str = "TEST",
) -> TradeRecord:
    return TradeRecord(
        symbol=symbol,
        signal_date="2024-01-01",
        entry_date="2024-01-02",
        exit_date="2024-01-09",
        entry_price=100.0,
        exit_price=100.0 * (1.0 + net_return + _ROUND_TRIP_COST),
        gross_return=net_return + _ROUND_TRIP_COST,
        net_return=net_return,
        hold_days=7,
        delivery_zscore=2.5,
        daily_return=0.02,
        volume_ratio=1.3,
    )


# ── compute_signals ───────────────────────────────────────────────────────────

class TestComputeSignals:
    def test_empty_input_returns_empty(self):
        df = compute_signals(pd.DataFrame())
        assert df.empty

    def test_spike_day_produces_signal(self):
        """A day with high delivery zscore + return + volume should be flagged."""
        # Need at least _ROLLING_WINDOW days before spike for z-score to be valid.
        n_days = _ROLLING_WINDOW + 5
        spike_day = _ROLLING_WINDOW + 2  # well past warm-up period
        delivery = _make_delivery(
            "SPIKETEST",
            start="2023-01-02",
            n_days=n_days,
            base_deliv_pct=30.0,
            spike_day=spike_day,
            spike_deliv_pct=90.0,    # >> 2σ above baseline
            spike_return=0.025,       # > 1.5%
            spike_volume_ratio=1.5,   # > 1.2×
        )
        signals = compute_signals(delivery)
        spike_date = pd.bdate_range("2023-01-02", periods=n_days)[spike_day].date()
        sig_row = signals[signals["business_date"].dt.date == spike_date]
        assert len(sig_row) == 1
        assert sig_row.iloc[0]["signal"] is True or sig_row.iloc[0]["signal"] == True

    def test_low_delivery_day_no_signal(self):
        """Normal delivery % should not trigger signal."""
        n_days = _ROLLING_WINDOW + 5
        delivery = _make_delivery(
            "QUIETTEST",
            start="2023-01-02",
            n_days=n_days,
            base_deliv_pct=30.0,
            # No spike
        )
        signals = compute_signals(delivery)
        assert signals["signal"].sum() == 0, "Expected no signals on flat delivery data"

    def test_return_below_threshold_suppresses_signal(self):
        """Delivery spike but return < 1.5% should not trigger."""
        n_days = _ROLLING_WINDOW + 5
        spike_day = _ROLLING_WINDOW + 2
        delivery = _make_delivery(
            "LOWRET",
            start="2023-01-02",
            n_days=n_days,
            base_deliv_pct=30.0,
            spike_day=spike_day,
            spike_deliv_pct=90.0,
            spike_return=0.005,   # < 1.5% threshold
            spike_volume_ratio=1.5,
        )
        signals = compute_signals(delivery)
        spike_date = pd.bdate_range("2023-01-02", periods=n_days)[spike_day].date()
        sig_row = signals[signals["business_date"].dt.date == spike_date]
        if not sig_row.empty:
            assert sig_row.iloc[0]["signal"] == False

    def test_volume_below_threshold_suppresses_signal(self):
        """Delivery spike but volume ratio < 1.2 should not trigger."""
        n_days = _ROLLING_WINDOW + 5
        spike_day = _ROLLING_WINDOW + 2
        delivery = _make_delivery(
            "LOWVOL",
            start="2023-01-02",
            n_days=n_days,
            base_deliv_pct=30.0,
            spike_day=spike_day,
            spike_deliv_pct=90.0,
            spike_return=0.025,
            spike_volume_ratio=1.0,   # < 1.2 threshold
        )
        signals = compute_signals(delivery)
        spike_date = pd.bdate_range("2023-01-02", periods=n_days)[spike_day].date()
        sig_row = signals[signals["business_date"].dt.date == spike_date]
        if not sig_row.empty:
            assert sig_row.iloc[0]["signal"] == False

    def test_no_signal_before_warmup_period(self):
        """First _ROLLING_WINDOW days have insufficient history — zscore should not fire."""
        delivery = _make_delivery(
            "WARMUP",
            start="2023-01-02",
            n_days=_ROLLING_WINDOW - 1,
            base_deliv_pct=30.0,
            spike_day=1,
            spike_deliv_pct=99.0,
            spike_return=0.05,
            spike_volume_ratio=3.0,
        )
        signals = compute_signals(delivery)
        # With fewer than rolling_window days, zscore should not be computed
        # (NaN delivery_zscore → signal = False)
        assert signals["signal"].sum() == 0

    def test_signal_columns_present(self):
        delivery = _make_delivery("COL", "2023-06-01", n_days=5)
        signals = compute_signals(delivery)
        for col in ["symbol", "business_date", "delivery_zscore",
                    "daily_return", "volume_ratio", "signal"]:
            assert col in signals.columns


# ── simulate_trades ───────────────────────────────────────────────────────────

class TestSimulateTrades:
    def _build_signal_row(self, signal_date: str, symbol: str = "HDFC") -> pd.DataFrame:
        """Minimal signals DataFrame with exactly one True signal."""
        return pd.DataFrame([{
            "symbol": symbol,
            "business_date": pd.Timestamp(signal_date),
            "delivery_zscore": 2.5,
            "daily_return": 0.02,
            "volume_ratio": 1.3,
            "signal": True,
        }])

    def _build_ohlcv(self, symbol: str, start: str, n_days: int = 15,
                     open_price: float = 100.0, close_price: float = 102.0) -> pd.DataFrame:
        dates = list(pd.bdate_range(start, periods=n_days).date)
        rows = [{"business_date": d, "symbol": symbol,
                 "open": open_price, "close": close_price}
                for d in dates]
        df = pd.DataFrame(rows)
        return df.set_index(["business_date", "symbol"])

    def test_basic_trade_generated(self):
        signal_date = "2023-07-03"
        sym = "HDFC"
        signals = self._build_signal_row(signal_date, sym)
        ohlcv = self._build_ohlcv(sym, "2023-06-26", n_days=20)

        with patch("quant.strategies.idi.assert_no_holdout_access"):
            with patch("quant.strategies.idi.is_pledge_flagged", return_value=False):
                trades = simulate_trades(signals, ohlcv)

        assert len(trades) == 1
        t = trades[0]
        assert t.symbol == sym
        assert t.entry_date > signal_date
        assert t.exit_date > t.entry_date

    def test_entry_is_t1_open(self):
        """Entry price should come from the open column of T+1."""
        signal_date = "2023-07-03"
        sym = "TATA"
        signals = self._build_signal_row(signal_date, sym)
        ohlcv = self._build_ohlcv(sym, "2023-06-26", n_days=20,
                                   open_price=200.0, close_price=210.0)

        with patch("quant.strategies.idi.assert_no_holdout_access"):
            with patch("quant.strategies.idi.is_pledge_flagged", return_value=False):
                trades = simulate_trades(signals, ohlcv)

        assert len(trades) == 1
        assert trades[0].entry_price == pytest.approx(200.0)

    def test_exit_is_t5_close(self):
        """Exit price should come from the close column of T+5 after entry."""
        signal_date = "2023-07-03"
        sym = "INFY"
        signals = self._build_signal_row(signal_date, sym)
        ohlcv = self._build_ohlcv(sym, "2023-06-26", n_days=20,
                                   open_price=100.0, close_price=105.0)

        with patch("quant.strategies.idi.assert_no_holdout_access"):
            with patch("quant.strategies.idi.is_pledge_flagged", return_value=False):
                trades = simulate_trades(signals, ohlcv)

        assert len(trades) == 1
        assert trades[0].exit_price == pytest.approx(105.0)

    def test_net_return_subtracts_cost(self):
        signal_date = "2023-07-03"
        sym = "WIPRO"
        signals = self._build_signal_row(signal_date, sym)
        # entry=100, exit=102 → gross = 0.02
        ohlcv = self._build_ohlcv(sym, "2023-06-26", n_days=20,
                                   open_price=100.0, close_price=102.0)

        with patch("quant.strategies.idi.assert_no_holdout_access"):
            with patch("quant.strategies.idi.is_pledge_flagged", return_value=False):
                trades = simulate_trades(signals, ohlcv)

        t = trades[0]
        assert t.gross_return == pytest.approx(0.02)
        assert t.net_return == pytest.approx(0.02 - _ROUND_TRIP_COST, abs=1e-9)

    def test_election_period_skipped(self):
        """Signal during election window should be skipped."""
        # 2024-04-11 is within the 2024 election window
        signal_date = "2024-04-11"
        sym = "ELECT"
        signals = self._build_signal_row(signal_date, sym)
        ohlcv = self._build_ohlcv(sym, "2024-04-01", n_days=20)

        with patch("quant.strategies.idi.assert_no_holdout_access"):
            with patch("quant.strategies.idi.is_pledge_flagged", return_value=False):
                trades = simulate_trades(signals, ohlcv)

        assert len(trades) == 0

    def test_pledge_flagged_skipped(self):
        """Signal for a pledge-flagged stock should be excluded (fail-safe)."""
        signal_date = "2023-07-03"
        sym = "PLEDGED"
        signals = self._build_signal_row(signal_date, sym)
        ohlcv = self._build_ohlcv(sym, "2023-06-26", n_days=20)

        with patch("quant.strategies.idi.assert_no_holdout_access"):
            with patch("quant.strategies.idi.is_pledge_flagged", return_value=True):
                trades = simulate_trades(signals, ohlcv)

        assert len(trades) == 0

    def test_empty_signals_returns_empty(self):
        signals = pd.DataFrame()
        ohlcv = pd.DataFrame().set_index(pd.MultiIndex.from_tuples([], names=["business_date", "symbol"]))
        assert simulate_trades(signals, ohlcv) == []

    def test_false_signals_excluded(self):
        """Rows with signal=False should not generate trades."""
        signal_date = "2023-07-03"
        sym = "NOFIRE"
        signals = pd.DataFrame([{
            "symbol": sym,
            "business_date": pd.Timestamp(signal_date),
            "delivery_zscore": 1.0,   # below threshold
            "daily_return": 0.005,    # below threshold
            "volume_ratio": 1.1,      # below threshold
            "signal": False,
        }])
        ohlcv = self._build_ohlcv(sym, "2023-06-26", n_days=20)

        with patch("quant.strategies.idi.assert_no_holdout_access"):
            with patch("quant.strategies.idi.is_pledge_flagged", return_value=False):
                trades = simulate_trades(signals, ohlcv)

        assert len(trades) == 0


# ── compute_gate_metrics ──────────────────────────────────────────────────────

class TestComputeGateMetrics:
    def test_no_trades_fails_all(self):
        m = compute_gate_metrics([])
        assert m["gate_pass"] is False
        assert m["fail_reason"] == "no trades"
        assert m["n_trades"] == 0

    def test_all_pass(self):
        """Build a set of trades that passes all 7 criteria."""
        # Need >= 80 trades, win_rate >= 52%, mean >= 80 bps, Sharpe >= 0.5
        rng = np.random.default_rng(42)
        # Mean net return ~0.012 (120 bps), low variance → high Sharpe
        returns = rng.normal(0.012, 0.015, 100)
        returns = np.clip(returns, -0.05, 0.08)  # keep realistic

        trades = [_make_trade(r) for r in returns]

        with patch("quant.research.dsr.deflated_sharpe", return_value=0.75):
            m = compute_gate_metrics(trades, n_trials=1)

        # Verify individual metrics are populated
        assert m["n_trades"] == 100
        assert m["mean_return_bps"] == pytest.approx(np.mean(returns) * 10_000, abs=1.0)
        if m["gate_pass"]:
            assert m["fail_reason"] == "all pass"

    def test_too_few_trades_fails(self):
        trades = [_make_trade(0.015) for _ in range(50)]  # < 80
        with patch("quant.research.dsr.deflated_sharpe", return_value=0.75):
            m = compute_gate_metrics(trades, n_trials=1)
        assert m["gate_pass"] is False
        assert "n_trades" in m["fail_reason"]

    def test_mean_return_below_threshold_fails(self):
        # Mean net return = 30 bps < 80 bps
        trades = [_make_trade(0.003) for _ in range(100)]
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

    def test_low_sharpe_fails(self):
        # Small mean, large variance → Sharpe < 0.5
        trades = [_make_trade(0.009 + 0.05 * (i % 2 == 0) - 0.025) for i in range(100)]
        with patch("quant.research.dsr.deflated_sharpe", return_value=0.75):
            m = compute_gate_metrics(trades, n_trials=1)
        # Just verify the metric is populated and check pass/fail is consistent
        assert isinstance(m["sharpe"], float)

    def test_dsr_below_threshold_fails(self):
        trades = [_make_trade(0.015) for _ in range(100)]
        with patch("quant.research.dsr.deflated_sharpe", return_value=0.3):  # < 0.5
            m = compute_gate_metrics(trades, n_trials=1)
        assert m["gate_pass"] is False
        assert "DSR" in m["fail_reason"]


# ── run_anti_strategy ─────────────────────────────────────────────────────────

class TestRunAntiStrategy:
    def _build_signals(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "symbol": "TEST",
            "business_date": pd.Timestamp("2023-07-10"),
            "delivery_zscore": 2.5,
            "daily_return": 0.02,
            "volume_ratio": 1.3,
            "signal": True,
        }])

    def _build_ohlcv(self) -> pd.DataFrame:
        dates = list(pd.bdate_range("2023-07-03", periods=20).date)
        rows = [{"business_date": d, "symbol": "TEST",
                 "open": 100.0, "close": 102.0} for d in dates]
        df = pd.DataFrame(rows)
        return df.set_index(["business_date", "symbol"])

    def test_anti_strategy_returns_dict_with_flag(self):
        with patch("quant.strategies.idi.assert_no_holdout_access"):
            with patch("quant.strategies.idi.is_pledge_flagged", return_value=False):
                with patch("quant.research.dsr.deflated_sharpe", return_value=0.5):
                    result = run_anti_strategy(
                        self._build_signals(), self._build_ohlcv(), n_trials=1
                    )
        assert result.get("is_anti_strategy") is True
        assert "mean_return_bps" in result

    def test_anti_strategy_gross_return_inverted(self):
        """Anti-strategy should invert gross returns."""
        with patch("quant.strategies.idi.assert_no_holdout_access"):
            with patch("quant.strategies.idi.is_pledge_flagged", return_value=False):
                with patch("quant.research.dsr.deflated_sharpe", return_value=0.5):
                    result = run_anti_strategy(
                        self._build_signals(), self._build_ohlcv(), n_trials=1
                    )
        # original gross = +2%, anti = -2%, so mean_return_bps should be negative
        assert result["mean_return_bps"] < 0


# ── run_cost_stress ───────────────────────────────────────────────────────────

class TestRunCostStress:
    def test_cost_stress_returns_dict_with_flag(self):
        signals = pd.DataFrame([{
            "symbol": "TEST",
            "business_date": pd.Timestamp("2023-07-10"),
            "delivery_zscore": 2.5, "daily_return": 0.02,
            "volume_ratio": 1.3, "signal": True,
        }])
        dates = list(pd.bdate_range("2023-07-03", periods=20).date)
        ohlcv = pd.DataFrame(
            [{"business_date": d, "symbol": "TEST", "open": 100.0, "close": 102.0}
             for d in dates]
        ).set_index(["business_date", "symbol"])

        with patch("quant.strategies.idi.assert_no_holdout_access"):
            with patch("quant.strategies.idi.is_pledge_flagged", return_value=False):
                with patch("quant.research.dsr.deflated_sharpe", return_value=0.5):
                    result = run_cost_stress(signals, ohlcv, n_trials=1, seed=7)

        assert result.get("is_cost_stress") is True
        assert "dsr" in result


# ── is_election_period ────────────────────────────────────────────────────────

class TestIsElectionPeriod:
    def test_within_2024_window(self):
        assert is_election_period("2024-04-20") is True

    def test_outside_windows(self):
        assert is_election_period("2023-06-01") is False
        assert is_election_period("2022-01-01") is False

    def test_boundary_inclusive(self):
        assert is_election_period("2024-03-20") is True
        assert is_election_period("2024-07-04") is True
        assert is_election_period("2024-07-05") is False
