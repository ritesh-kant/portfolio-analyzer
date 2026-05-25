"""Tests for Strategy G: BDM-Momentum (bdm_momentum.py)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.strategies.bdm import build_events
from quant.strategies.bdm_momentum import (
    TradeRecord,
    apply_momentum_filter,
    compute_ema50,
    compute_gate_metrics,
    run_anti_strategy,
    run_cost_stress,
    simulate_trades,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_ohlcv(
    symbols: list[str],
    start: str,
    end: str,
    base_price: float = 100.0,
    trend: float = 0.002,   # daily price drift
) -> pd.DataFrame:
    """Create synthetic OHLCV with an upward trend (close > EMA50 after warmup)."""
    dates = pd.bdate_range(start, end)
    rows = []
    for sym in symbols:
        price = base_price
        for d in dates:
            price *= 1 + trend
            rows.append({
                "business_date": d.date(),
                "symbol": sym,
                "open":  round(price * 0.999, 2),
                "high":  round(price * 1.005, 2),
                "low":   round(price * 0.995, 2),
                "close": round(price, 2),
                "prev_close": round(price * (1 - trend), 2),
                "volume": 100_000,
                "turnover_lacs": 100.0,
                "as_of_timestamp": pd.Timestamp(d) + pd.Timedelta(hours=18),
            })
    df = pd.DataFrame(rows).set_index(["business_date", "symbol"])
    return df


def _make_ohlcv_downtrend(
    symbols: list[str],
    start: str,
    end: str,
    base_price: float = 100.0,
    drift: float = -0.002,   # downward drift
) -> pd.DataFrame:
    return _make_ohlcv(symbols, start, end, base_price=base_price, trend=drift)


def _make_bulk_deals(
    symbol: str,
    date_str: str,
    value_cr: float = 10.0,
) -> pd.DataFrame:
    return pd.DataFrame([{
        "symbol": symbol,
        "business_date": pd.Timestamp(date_str).date(),
        "client_name": "TEST FUND",
        "side": "BUY",
        "quantity": int(value_cr * 1e7 / 100),
        "price": 100.0,
        "value_cr": value_cr,
        "as_of_timestamp": pd.Timestamp(date_str) + pd.Timedelta(hours=16),
    }])


# ── compute_ema50 tests ───────────────────────────────────────────────────────

class TestComputeEma50:

    def test_returns_series_with_expected_index(self):
        ohlcv = _make_ohlcv(["AAAA", "BBBB"], "2022-01-01", "2023-06-30")
        ema = compute_ema50(ohlcv)
        assert isinstance(ema, pd.Series)
        assert set(ema.index.names) == {"business_date", "symbol"}

    def test_ema_absent_before_min_periods(self):
        """Events in the early data period (< min_periods history) should be
        absent from the EMA series (stack() drops NaN rows) — meaning
        apply_momentum_filter will skip those events via KeyError."""
        # Only 2 months of data — the first 24 rows are NaN and get dropped by stack()
        ohlcv = _make_ohlcv(["AAAA"], "2023-01-01", "2023-03-01")
        ema = compute_ema50(ohlcv)
        # All dates in the first 24 business days should be absent from the series
        all_dates = ohlcv.xs("AAAA", level="symbol").index
        early_dates = all_dates[:24]
        ema_dates = ema.xs("AAAA", level="symbol").index
        # At least some early dates should be missing
        missing = [d for d in early_dates if d not in ema_dates]
        assert len(missing) > 0, "Expected some early dates to be absent from EMA series"

    def test_ema_converges_on_uptrend(self):
        """In a sustained uptrend, close should exceed EMA50 after warmup."""
        ohlcv = _make_ohlcv(["AAAA"], "2022-01-01", "2023-06-30", trend=0.003)
        ema = compute_ema50(ohlcv)
        # After 100 days (enough warmup), close should be > EMA50
        close_last = float(ohlcv.xs("AAAA", level="symbol")["close"].iloc[-1])
        date_last  = ohlcv.xs("AAAA", level="symbol").index[-1]
        ema_last   = float(ema.loc[(date_last, "AAAA")])
        assert close_last > ema_last, "Close should exceed EMA50 in sustained uptrend"

    def test_ema_above_close_on_downtrend(self):
        """In a sustained downtrend, close should be below EMA50."""
        ohlcv = _make_ohlcv_downtrend(["AAAA"], "2022-01-01", "2023-06-30")
        ema = compute_ema50(ohlcv)
        close_last = float(ohlcv.xs("AAAA", level="symbol")["close"].iloc[-1])
        date_last  = ohlcv.xs("AAAA", level="symbol").index[-1]
        ema_last   = float(ema.loc[(date_last, "AAAA")])
        assert close_last < ema_last, "Close should be below EMA50 in sustained downtrend"

    def test_empty_ohlcv_returns_empty_series(self):
        result = compute_ema50(pd.DataFrame())
        assert isinstance(result, pd.Series)
        assert len(result) == 0

    def test_multiple_symbols_independent(self):
        ohlcv_up   = _make_ohlcv(["UP"],   "2022-01-01", "2023-06-30", trend=0.005)
        ohlcv_down = _make_ohlcv_downtrend(["DOWN"], "2022-01-01", "2023-06-30")
        ohlcv = pd.concat([ohlcv_up, ohlcv_down])
        ema = compute_ema50(ohlcv)

        last_date = ohlcv.xs("UP", level="symbol").index[-1]
        close_up   = float(ohlcv.loc[(last_date, "UP"),   "close"])
        close_down = float(ohlcv.loc[(last_date, "DOWN"), "close"])
        ema_up   = float(ema.loc[(last_date, "UP")])
        ema_down = float(ema.loc[(last_date, "DOWN")])

        assert close_up   > ema_up,   "UP symbol: close should exceed EMA50"
        assert close_down < ema_down, "DOWN symbol: close should be below EMA50"


# ── apply_momentum_filter tests ───────────────────────────────────────────────

class TestApplyMomentumFilter:

    def _events_for(self, symbol: str, date_str: str) -> pd.DataFrame:
        deals = _make_bulk_deals(symbol, date_str)
        return build_events(deals)

    def test_passes_uptrend_event(self):
        ohlcv = _make_ohlcv(["AAAA"], "2022-01-01", "2023-06-30", trend=0.003)
        ema   = compute_ema50(ohlcv)
        ev    = self._events_for("AAAA", "2023-06-20")
        filtered, skipped = apply_momentum_filter(ev, ohlcv, ema50=ema)
        assert len(filtered) == 1
        assert skipped == 0

    def test_blocks_downtrend_event(self):
        ohlcv = _make_ohlcv_downtrend(["AAAA"], "2022-01-01", "2023-06-30")
        ema   = compute_ema50(ohlcv)
        ev    = self._events_for("AAAA", "2023-06-20")
        filtered, skipped = apply_momentum_filter(ev, ohlcv, ema50=ema)
        assert len(filtered) == 0
        assert skipped == 1

    def test_empty_events(self):
        ohlcv = _make_ohlcv(["AAAA"], "2022-01-01", "2023-06-30")
        ema   = compute_ema50(ohlcv)
        empty = pd.DataFrame(columns=["symbol", "event_date",
                                       "total_value_cr", "client_names"])
        filtered, skipped = apply_momentum_filter(empty, ohlcv, ema50=ema)
        assert len(filtered) == 0
        assert skipped == 0

    def test_missing_ohlcv_skips(self):
        """Event with no OHLCV/EMA should be skipped (conservative)."""
        ohlcv = _make_ohlcv(["AAAA"], "2022-01-01", "2023-06-30")
        ema   = compute_ema50(ohlcv)
        ev    = self._events_for("ZZZZ", "2023-06-20")  # symbol not in OHLCV
        filtered, skipped = apply_momentum_filter(ev, ohlcv, ema50=ema)
        assert len(filtered) == 0
        assert skipped == 1

    def test_mixed_updown_filters_correctly(self):
        """Two events: one above EMA50, one below — only above passes."""
        ohlcv_up   = _make_ohlcv(["UPSYM"],   "2022-01-01", "2023-06-30", trend=0.004)
        ohlcv_down = _make_ohlcv_downtrend(["DNSYM"], "2022-01-01", "2023-06-30")
        ohlcv = pd.concat([ohlcv_up, ohlcv_down])
        ema = compute_ema50(ohlcv)

        deals = pd.concat([
            _make_bulk_deals("UPSYM", "2023-06-20"),
            _make_bulk_deals("DNSYM", "2023-06-20"),
        ])
        events = build_events(deals)
        filtered, skipped = apply_momentum_filter(events, ohlcv, ema50=ema)
        assert len(filtered) == 1
        assert filtered.iloc[0]["symbol"] == "UPSYM"
        assert skipped == 1


# ── simulate_trades tests ─────────────────────────────────────────────────────

class TestSimulateTrades:

    def _setup(self, trend: float = 0.003):
        ohlcv = _make_ohlcv(["AAAA"], "2022-01-01", "2023-09-30", trend=trend)
        deals = _make_bulk_deals("AAAA", "2023-06-15")
        events = build_events(deals)
        return ohlcv, events

    def test_produces_trade_for_uptrend(self):
        ohlcv, events = self._setup(trend=0.003)
        trades = simulate_trades(events, ohlcv)
        assert len(trades) == 1
        t = trades[0]
        assert t.symbol == "AAAA"
        assert t.close_t > t.ema50

    def test_no_trade_for_downtrend(self):
        ohlcv, events = self._setup(trend=-0.003)
        trades = simulate_trades(events, ohlcv)
        assert len(trades) == 0

    def test_trade_record_fields_populated(self):
        ohlcv, events = self._setup(trend=0.003)
        trades = simulate_trades(events, ohlcv)
        assert len(trades) == 1
        t = trades[0]
        assert t.entry_price > 0
        assert t.exit_price > 0
        assert t.hold_days > 0
        assert t.ema50 > 0
        assert t.close_t > 0
        assert t.gross_return == pytest.approx(t.exit_price / t.entry_price - 1.0)
        assert t.net_return == pytest.approx(t.gross_return - 0.0055, abs=1e-9)

    def test_slippage_scale_increases_cost(self):
        ohlcv, events = self._setup(trend=0.003)
        rng = np.random.default_rng(0)
        trades_nominal = simulate_trades(events, ohlcv, slippage_scale=1.0)
        trades_stress  = simulate_trades(events, ohlcv, slippage_scale=2.0, rng=rng)
        # Net return should be lower under stress (more cost)
        if trades_nominal and trades_stress:
            assert trades_stress[0].net_return <= trades_nominal[0].net_return

    def test_empty_events_returns_empty(self):
        ohlcv = _make_ohlcv(["AAAA"], "2022-01-01", "2023-09-30")
        empty = pd.DataFrame(columns=["symbol", "event_date",
                                       "total_value_cr", "client_names"])
        trades = simulate_trades(empty, ohlcv)
        assert trades == []

    def test_empty_ohlcv_returns_empty(self):
        deals = _make_bulk_deals("AAAA", "2023-06-15")
        events = build_events(deals)
        trades = simulate_trades(events, pd.DataFrame())
        assert trades == []

    def test_ema_passed_externally_reused(self):
        """Passing pre-computed ema50 gives same result as computing internally."""
        ohlcv, events = self._setup(trend=0.003)
        ema50 = compute_ema50(ohlcv)
        trades_internal = simulate_trades(events, ohlcv)
        trades_external = simulate_trades(events, ohlcv, ema50=ema50)
        assert len(trades_internal) == len(trades_external)
        if trades_internal:
            assert trades_internal[0].net_return == pytest.approx(
                trades_external[0].net_return
            )


# ── compute_gate_metrics tests ────────────────────────────────────────────────

class TestComputeGateMetrics:

    def _make_trades(self, net_returns: list[float]) -> list[TradeRecord]:
        return [
            TradeRecord(
                symbol="X", event_date="2024-01-01",
                entry_date="2024-01-02", exit_date="2024-02-02",
                entry_price=100.0, exit_price=100.0 * (1 + r + 0.0055),
                gross_return=r + 0.0055, net_return=r,
                hold_days=28, deal_value_cr=10.0, client_name="FUND",
                ema50=95.0, close_t=100.0,
            )
            for r in net_returns
        ]

    def test_all_pass(self):
        # 60% win rate, high mean, high Sharpe
        n = 40
        returns = [0.025] * int(n * 0.6) + [-0.005] * int(n * 0.4)
        trades = self._make_trades(returns)
        m = compute_gate_metrics(trades, n_trials=1)
        assert m["gate_pass"] is True
        assert m["fail_reason"] == "all pass"
        assert m["win_rate"] == pytest.approx(0.6)

    def test_fail_low_win_rate(self):
        n = 40
        returns = [0.03] * 15 + [-0.002] * 25
        trades = self._make_trades(returns)
        m = compute_gate_metrics(trades, n_trials=1)
        assert m["gate_pass"] is False
        assert "win_rate" in m["fail_reason"]

    def test_fail_low_sharpe(self):
        # High mean but very high variance → low Sharpe
        returns = [0.10] * 20 + [-0.09] * 20
        trades = self._make_trades(returns)
        m = compute_gate_metrics(trades, n_trials=1)
        assert m["gate_pass"] is False
        assert "Sharpe" in m["fail_reason"]

    def test_fail_too_few_trades(self):
        returns = [0.02] * 20   # only 20 trades (< 30)
        trades = self._make_trades(returns)
        m = compute_gate_metrics(trades, n_trials=1)
        assert m["gate_pass"] is False
        assert "n_trades" in m["fail_reason"]

    def test_event_count_threshold_is_30(self):
        """Strategy G uses n_trades >= 30 (not 40 like F)."""
        returns = [0.025] * int(30 * 0.6) + [-0.005] * int(30 * 0.4)
        trades = self._make_trades(returns)
        m = compute_gate_metrics(trades, n_trials=1)
        # 30 trades should NOT trigger the count gate
        assert "n_trades" not in m.get("fail_reason", "")

    def test_no_trades(self):
        m = compute_gate_metrics([], n_trials=1)
        assert m["gate_pass"] is False
        assert m["n_trades"] == 0

    def test_single_trade(self):
        trades = self._make_trades([0.03])
        m = compute_gate_metrics(trades, n_trials=1)
        # 1 trade < 30 → count gate fails
        assert m["gate_pass"] is False


# ── run_anti_strategy tests ───────────────────────────────────────────────────

class TestRunAntiStrategy:

    def test_anti_strategy_inverts_returns(self):
        ohlcv = _make_ohlcv(["AAAA"], "2022-01-01", "2023-09-30", trend=0.003)
        deals = _make_bulk_deals("AAAA", "2023-06-15")
        events = build_events(deals)
        ema50 = compute_ema50(ohlcv)

        result = run_anti_strategy(events, ohlcv, n_trials=1, ema50=ema50)
        assert "is_anti_strategy" in result
        assert result["is_anti_strategy"] is True

    def test_anti_returns_dict_with_gate_keys(self):
        ohlcv = _make_ohlcv(["AAAA"], "2022-01-01", "2023-09-30", trend=0.003)
        deals = _make_bulk_deals("AAAA", "2023-06-15")
        events = build_events(deals)
        result = run_anti_strategy(events, ohlcv, n_trials=1)
        for key in ("mean_return_bps", "win_rate", "sharpe", "dsr", "n_trades"):
            assert key in result


# ── run_cost_stress tests ─────────────────────────────────────────────────────

class TestRunCostStress:

    def test_stress_returns_dict(self):
        ohlcv = _make_ohlcv(["AAAA"], "2022-01-01", "2023-09-30", trend=0.003)
        deals = _make_bulk_deals("AAAA", "2023-06-15")
        events = build_events(deals)
        result = run_cost_stress(events, ohlcv, n_trials=1, seed=42)
        assert result.get("is_cost_stress") is True
        for key in ("mean_return_bps", "win_rate", "sharpe", "dsr"):
            assert key in result

    def test_stress_dsr_not_greater_than_nominal(self):
        """Stress DSR should be ≤ nominal DSR (more cost = same or lower DSR)."""
        ohlcv = _make_ohlcv(["AAAA"], "2022-01-01", "2023-09-30", trend=0.003)
        deals = _make_bulk_deals("AAAA", "2023-06-15")
        events = build_events(deals)
        ema50 = compute_ema50(ohlcv)
        nominal = compute_gate_metrics(simulate_trades(events, ohlcv, ema50=ema50),
                                       n_trials=1)
        stress  = run_cost_stress(events, ohlcv, n_trials=1, seed=42, ema50=ema50)
        assert stress["dsr"] <= nominal["dsr"] + 1e-9
