"""test_backtest_parity — critical regression guard for the backtest harness.

These tests assert that backtest/indicators.py produces identical output to
production src/pipeline/agents/technical_agent.py for the same OHLCV input.

WHY THIS MATTERS:
    The backtest derives historical performance claims from indicators.py.
    If indicators.py diverges from production technical_agent.py — even slightly
    (e.g. a rounding difference, a different column name) — the backtest is
    measuring a different strategy than what runs in production, and the
    performance figures become meaningless.

    These tests are the single source of truth for that parity.

WHAT IS TESTED:
    1. Shared helper _safe_float behaves identically in both modules.
    2. compute_indicators() returns the exact same numeric values as
       technical_agent._compute_indicators() for synthetic OHLCV data.
    3. Edge cases: insufficient bars, all-NaN close, as_of_date slicing.
    4. The returned dict has exactly the keys production consumers expect.
    5. Batch computation returns results only for symbols with enough data.

IF A TEST FAILS AFTER A PRODUCTION CHANGE:
    Update backtest/indicators.py to match the new production logic, then
    re-run the tests. Never relax the assertions to make tests pass —
    fix the implementation.
"""

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pandas_ta  # noqa: F401 — must be importable for both modules
import pytest

# ── Production module under test (imported directly to share logic) ───────────
from src.pipeline.agents.technical_agent import _compute_indicators as prod_compute
from src.pipeline.agents.technical_agent import _safe_float as prod_safe_float

# ── Backtest module under test ─────────────────────────────────────────────────
from backtest.indicators import _safe_float as bt_safe_float
from backtest.indicators import compute_indicators, compute_indicators_batch

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_ohlcv(n: int = 80, seed: int = 42) -> pd.DataFrame:
    """Generate a reproducible synthetic OHLCV DataFrame with n rows.

    Uses a random-walk close so all derived indicators (EMA, RSI, MACD) are
    numerically well-defined and non-trivial.
    """
    rng = np.random.default_rng(seed)
    base = 2000.0
    returns = rng.normal(0.0005, 0.015, n)
    closes = base * np.cumprod(1 + returns)

    highs  = closes * (1 + rng.uniform(0.001, 0.02, n))
    lows   = closes * (1 - rng.uniform(0.001, 0.02, n))
    opens  = closes * (1 + rng.normal(0, 0.005, n))
    volumes = rng.integers(500_000, 5_000_000, n).astype(float)

    idx = pd.date_range(end=date.today() - timedelta(days=1), periods=n, freq="B")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=idx,
    )


def _to_lowercase(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with lowercase column names (as data_loader produces)."""
    out = df.copy()
    out.columns = [c.lower() for c in out.columns]
    return out


@pytest.fixture
def ohlcv_uppercase() -> pd.DataFrame:
    """OHLCV with uppercase columns — as yfinance returns."""
    return _make_ohlcv(80)


@pytest.fixture
def ohlcv_lowercase(ohlcv_uppercase) -> pd.DataFrame:
    """OHLCV with lowercase columns — as data_loader caches to parquet."""
    return _to_lowercase(ohlcv_uppercase)


# ─────────────────────────────────────────────────────────────────────────────
# 1. _safe_float parity
# ─────────────────────────────────────────────────────────────────────────────

class TestSafeFloatParity:
    """Both modules must have identical _safe_float behaviour."""

    @pytest.mark.parametrize("val,default,expected", [
        (42.0,          0.0, 42.0),
        ("3.14",        0.0, 3.14),
        (None,          0.0, 0.0),
        (float("nan"),  0.0, 0.0),
        (float("inf"),  0.0, 0.0),
        (float("-inf"), 0.0, 0.0),
        ("bad",         9.9, 9.9),
        (0,             1.0, 0.0),
    ])
    def test_safe_float_identical(self, val, default, expected):
        prod_result = prod_safe_float(val, default)
        bt_result   = bt_safe_float(val, default)
        assert prod_result == bt_result == expected, (
            f"_safe_float({val!r}, {default}) diverged: prod={prod_result} bt={bt_result}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Core indicator parity — same numerics as production
# ─────────────────────────────────────────────────────────────────────────────

class TestIndicatorParity:
    """compute_indicators must produce the same values as production _compute_indicators.

    We feed both functions the same OHLCV data and compare each numeric field.
    PCR is excluded (backtest returns None; production fetches live data).
    Stale-data logic is excluded (the backtest replaces it with as_of_date).
    """

    # Fields that must match exactly (after rounding to production precision)
    _FLOAT_FIELDS = [
        "close", "change_pct_today", "five_day_ret",
        "rsi", "macd", "macd_signal", "macd_hist",
        "ema20", "ema50",
        "volume_ratio",
        "bb_upper", "bb_lower", "bb_pct", "bb_width",
        "high_75d", "low_75d", "pct_from_high", "pct_from_low",
    ]
    _BOOL_FIELDS = ["above_ema20", "above_ema50"]

    def _run_production(self, symbol: str, df_uppercase: pd.DataFrame) -> dict:
        """Run production compute with a monkeypatched yfinance download."""
        import unittest.mock as mock

        # production _compute_indicators calls yf.download internally.
        # We intercept it and return our synthetic DataFrame.
        with mock.patch(
            "src.pipeline.agents.technical_agent.yf.download",
            return_value=df_uppercase,
        ):
            # Also suppress the PCR fetch (not relevant here)
            result = prod_compute(symbol)
        return result

    def test_float_fields_match(self, ohlcv_uppercase, ohlcv_lowercase):
        symbol = "TEST.NS"
        prod = self._run_production(symbol, ohlcv_uppercase)
        bt   = compute_indicators(symbol, ohlcv_lowercase)

        assert prod, "Production compute returned empty — check fixture"
        assert bt,   "Backtest compute returned empty — check fixture"

        for field in self._FLOAT_FIELDS:
            p_val = prod.get(field)
            b_val = bt.get(field)
            assert p_val is not None, f"Field {field!r} missing from production output"
            assert b_val is not None, f"Field {field!r} missing from backtest output"
            assert math.isclose(float(p_val), float(b_val), rel_tol=1e-5, abs_tol=1e-6), (
                f"Field {field!r} diverged: production={p_val} backtest={b_val}"
            )

    def test_bool_fields_match(self, ohlcv_uppercase, ohlcv_lowercase):
        symbol = "TEST.NS"
        prod = self._run_production(symbol, ohlcv_uppercase)
        bt   = compute_indicators(symbol, ohlcv_lowercase)

        for field in self._BOOL_FIELDS:
            assert prod.get(field) == bt.get(field), (
                f"Bool field {field!r} diverged: production={prod.get(field)} backtest={bt.get(field)}"
            )

    def test_volume_matches(self, ohlcv_uppercase, ohlcv_lowercase):
        symbol = "TEST.NS"
        prod = self._run_production(symbol, ohlcv_uppercase)
        bt   = compute_indicators(symbol, ohlcv_lowercase)
        # Volume is the raw last-bar value — must be identical
        assert math.isclose(float(prod["volume"]), float(bt["volume"]), rel_tol=1e-9), (
            f"volume diverged: prod={prod['volume']} bt={bt['volume']}"
        )

    def test_symbol_field_preserved(self, ohlcv_lowercase):
        result = compute_indicators("RELIANCE.NS", ohlcv_lowercase)
        assert result["symbol"] == "RELIANCE.NS"

    def test_pcr_is_none_in_backtest(self, ohlcv_lowercase):
        """Backtest always returns pcr=None; production fetches it live."""
        result = compute_indicators("TEST.NS", ohlcv_lowercase)
        assert result.get("pcr") is None, (
            "Backtest indicators.py should never populate 'pcr' — callers use neutral default"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_insufficient_bars_returns_empty(self):
        """Fewer than _MIN_BARS (55) rows → empty dict, no exception."""
        df = _to_lowercase(_make_ohlcv(40))
        result = compute_indicators("SHORT.NS", df)
        assert result == {}, f"Expected {{}} for short DataFrame, got {result}"

    def test_exactly_min_bars_returns_result(self):
        """Exactly 55 rows must produce a valid result (boundary condition)."""
        df = _to_lowercase(_make_ohlcv(55))
        result = compute_indicators("BORDER.NS", df)
        assert result and not result.get("stale"), (
            "Expected valid result for exactly 55 bars"
        )

    def test_as_of_date_slicing(self):
        """as_of_date should use only history up to that date, not future bars."""
        df = _to_lowercase(_make_ohlcv(80))
        # Use the 60th bar as "today" — indicators must be computed from bars 0–60
        as_of = df.index[59]  # 0-indexed → 60th bar
        result_60 = compute_indicators("SLICE.NS", df, as_of_date=as_of)

        # Use the 70th bar — a different close and indicator set
        as_of_70 = df.index[69]
        result_70 = compute_indicators("SLICE.NS", df, as_of_date=as_of_70)

        assert result_60 and result_70, "Both sliced results should be non-empty"
        # Closes must differ (random walk)
        assert result_60["close"] != result_70["close"], (
            "as_of_date slicing must use different 'last' rows"
        )
        # result_60 must reflect the 60th bar's close
        expected_close_60 = round(float(df["close"].iloc[59]), 2)
        assert math.isclose(result_60["close"], expected_close_60, rel_tol=1e-5), (
            f"Sliced close mismatch: expected {expected_close_60} got {result_60['close']}"
        )

    def test_as_of_date_before_all_data_returns_stale(self):
        """as_of_date before the first bar → stale marker."""
        df = _to_lowercase(_make_ohlcv(80))
        before_start = df.index[0] - pd.Timedelta(days=1)
        result = compute_indicators("EARLY.NS", df, as_of_date=before_start)
        assert result.get("stale"), "Expected stale=True when as_of_date is before all data"

    def test_returns_all_expected_keys(self):
        """The returned dict must contain all keys that signal_replay.py will access."""
        required_keys = {
            "symbol", "close", "change_pct_today", "five_day_ret",
            "rsi", "macd", "macd_signal", "macd_hist",
            "ema20", "ema50", "volume", "volume_ratio",
            "above_ema20", "above_ema50",
            "bb_upper", "bb_lower", "bb_pct", "bb_width",
            "high_75d", "low_75d", "pct_from_high", "pct_from_low",
            "pcr",
        }
        df = _to_lowercase(_make_ohlcv(80))
        result = compute_indicators("KEYS.NS", df)
        missing = required_keys - set(result.keys())
        assert not missing, f"Missing keys in backtest indicator output: {missing}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Batch computation
# ─────────────────────────────────────────────────────────────────────────────

class TestBatchComputation:

    def test_batch_returns_results_for_all_valid_symbols(self):
        """Batch should return one entry per symbol with sufficient data."""
        syms = ["SYM_A.NS", "SYM_B.NS", "SYM_C.NS"]
        data = {s: _to_lowercase(_make_ohlcv(80, seed=i)) for i, s in enumerate(syms)}
        as_of = data["SYM_A.NS"].index[-1]
        results = compute_indicators_batch(data, as_of_date=as_of)
        assert set(results.keys()) == set(syms), (
            f"Batch missing symbols: {set(syms) - set(results.keys())}"
        )

    def test_batch_omits_symbols_with_insufficient_data(self):
        """Symbols with < 55 bars must be silently omitted."""
        data = {
            "GOOD.NS": _to_lowercase(_make_ohlcv(80)),
            "BAD.NS":  _to_lowercase(_make_ohlcv(30)),   # too short
        }
        as_of = data["GOOD.NS"].index[-1]
        results = compute_indicators_batch(data, as_of_date=as_of)
        assert "GOOD.NS" in results, "Valid symbol should be in batch results"
        assert "BAD.NS" not in results, "Symbol with <55 bars should be omitted"

    def test_batch_as_of_date_is_respected(self):
        """All symbols should be computed as of the same date."""
        data = {"A.NS": _to_lowercase(_make_ohlcv(80)), "B.NS": _to_lowercase(_make_ohlcv(80, seed=99))}
        # Use a mid-history date for both
        as_of = data["A.NS"].index[60]
        results = compute_indicators_batch(data, as_of_date=as_of)
        for sym, ind in results.items():
            assert ind["close"] == round(float(data[sym].loc[data[sym].index <= as_of, "close"].iloc[-1]), 2), (
                f"{sym}: batch close doesn't match as_of_date row"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Numeric sanity (catch obvious calculation errors)
# ─────────────────────────────────────────────────────────────────────────────

class TestNumericSanity:
    """Guard against obviously wrong values that might slip past parity tests
    if both modules have the same bug."""

    @pytest.fixture
    def result(self):
        df = _to_lowercase(_make_ohlcv(80))
        return compute_indicators("SANITY.NS", df)

    def test_rsi_in_valid_range(self, result):
        assert 0.0 <= result["rsi"] <= 100.0, f"RSI out of range: {result['rsi']}"

    def test_ema50_positive(self, result):
        assert result["ema50"] > 0, f"EMA50 must be positive: {result['ema50']}"

    def test_bb_pct_bounded(self, result):
        # bb_pct can be slightly outside 0-1 for extreme moves, but shouldn't be wildly wrong
        assert -0.5 <= result["bb_pct"] <= 1.5, f"BB pct out of expected range: {result['bb_pct']}"

    def test_high_75d_gte_low_75d(self, result):
        assert result["high_75d"] >= result["low_75d"], (
            f"75d high ({result['high_75d']}) < 75d low ({result['low_75d']})"
        )

    def test_pct_from_high_non_positive(self, result):
        # Price can only be at or below the 75d high
        assert result["pct_from_high"] <= 0.01, (  # 0.01 tolerance for same-day high
            f"pct_from_high should be ≤ 0: {result['pct_from_high']}"
        )

    def test_volume_ratio_positive(self, result):
        assert result["volume_ratio"] > 0, f"volume_ratio must be positive: {result['volume_ratio']}"

    def test_close_positive(self, result):
        assert result["close"] > 0, f"close price must be positive: {result['close']}"
