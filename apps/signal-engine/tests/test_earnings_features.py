"""Tests for earnings feature builders (quant/features/earnings.py)."""

from __future__ import annotations

import math
import pytest
import numpy as np
import pandas as pd

from quant.features.earnings import (
    TARGET_BPS,
    TARGET_RETURN,
    REGISTERED_FEATURES,
    MAX_FEATURES,
    build_earnings_features,
    build_target,
    compute_naive_surprise,
    compute_day0_reaction,
    compute_days_since_last_result,
    _fiscal_quarter,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_ohlcv(symbol: str, dates: list[str], close_prices: list[float]) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame indexed by (business_date, symbol)."""
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


def _make_earnings(rows: list[dict]) -> pd.DataFrame:
    """Build minimal earnings DataFrame."""
    required = ["symbol", "business_date", "fiscal_quarter", "fiscal_year",
                "eps_reported", "revenue_cr", "as_of_timestamp"]
    records = []
    for r in rows:
        record = {k: r.get(k) for k in required}
        record["yoy_eps_prev"] = r.get("yoy_eps_prev")
        record["yoy_revenue_prev"] = r.get("yoy_revenue_prev")
        records.append(record)
    return pd.DataFrame(records)


# ── Tests: constants and registration ─────────────────────────────────────────

def test_feature_count_within_limit():
    assert len(REGISTERED_FEATURES) <= MAX_FEATURES


def test_target_return_matches_bps():
    assert abs(TARGET_RETURN - TARGET_BPS / 10_000) < 1e-10


def test_registered_features_no_duplicates():
    assert len(REGISTERED_FEATURES) == len(set(REGISTERED_FEATURES))


# ── Tests: fiscal quarter helper ──────────────────────────────────────────────

@pytest.mark.parametrize("month,expected_q", [
    (4, 1), (5, 1), (6, 1),
    (7, 2), (8, 2), (9, 2),
    (10, 3), (11, 3), (12, 3),
    (1, 4), (2, 4), (3, 4),
])
def test_fiscal_quarter(month, expected_q):
    ts = pd.Timestamp(f"2023-{month:02d}-15")
    assert _fiscal_quarter(ts) == expected_q


# ── Tests: naive EPS surprise ─────────────────────────────────────────────────

def test_compute_naive_surprise_yoy():
    earnings = _make_earnings([
        # Same quarter last year: Q2 FY2022 EPS = 10
        {"symbol": "TATA", "business_date": "2022-10-15",
         "fiscal_quarter": 2, "fiscal_year": 2023,
         "eps_reported": 10.0, "revenue_cr": 1000.0,
         "as_of_timestamp": pd.Timestamp("2022-10-15 12:30:00")},
        # Current quarter: Q2 FY2023 EPS = 12 → +20% surprise
        {"symbol": "TATA", "business_date": "2023-10-15",
         "fiscal_quarter": 2, "fiscal_year": 2024,
         "eps_reported": 12.0, "revenue_cr": 1200.0,
         "as_of_timestamp": pd.Timestamp("2023-10-15 12:30:00")},
    ])
    eps_surp, rev_surp = compute_naive_surprise("TATA", earnings, "2023-10-15")
    assert eps_surp is not None
    assert abs(eps_surp - 0.20) < 1e-6, f"Expected 0.20, got {eps_surp}"
    assert rev_surp is not None
    assert abs(rev_surp - 0.20) < 1e-6, f"Expected 0.20, got {rev_surp}"


def test_compute_naive_surprise_missing_prior():
    earnings = _make_earnings([
        {"symbol": "TATA", "business_date": "2023-10-15",
         "fiscal_quarter": 2, "fiscal_year": 2024,
         "eps_reported": 12.0, "revenue_cr": 1200.0,
         "as_of_timestamp": pd.Timestamp("2023-10-15 12:30:00")},
    ])
    eps_surp, rev_surp = compute_naive_surprise("TATA", earnings, "2023-10-15")
    assert eps_surp is None
    assert rev_surp is None


def test_compute_naive_surprise_unknown_symbol():
    earnings = _make_earnings([
        {"symbol": "TATA", "business_date": "2023-10-15",
         "fiscal_quarter": 2, "fiscal_year": 2024,
         "eps_reported": 12.0, "revenue_cr": 1200.0,
         "as_of_timestamp": pd.Timestamp("2023-10-15 12:30:00")},
    ])
    eps_surp, rev_surp = compute_naive_surprise("WIPRO", earnings, "2023-10-15")
    assert eps_surp is None
    assert rev_surp is None


def test_compute_naive_surprise_negative_eps_prev():
    earnings = _make_earnings([
        {"symbol": "XYZ", "business_date": "2022-10-15",
         "fiscal_quarter": 2, "fiscal_year": 2023,
         "eps_reported": -5.0, "revenue_cr": 500.0,
         "as_of_timestamp": pd.Timestamp("2022-10-15 12:30:00")},
        {"symbol": "XYZ", "business_date": "2023-10-15",
         "fiscal_quarter": 2, "fiscal_year": 2024,
         "eps_reported": 3.0, "revenue_cr": 600.0,
         "as_of_timestamp": pd.Timestamp("2023-10-15 12:30:00")},
    ])
    eps_surp, _ = compute_naive_surprise("XYZ", earnings, "2023-10-15")
    # (3 - (-5)) / |-5| = 8/5 = 1.6
    assert eps_surp is not None
    assert abs(eps_surp - 1.6) < 1e-6


# ── Tests: day-0 price reaction ───────────────────────────────────────────────

def test_compute_day0_reaction_positive():
    dates = ["2023-10-13", "2023-10-14", "2023-10-15", "2023-10-16"]
    prices = [100.0, 105.0, 110.0, 112.0]
    ohlcv = _make_ohlcv("TATA", dates, prices)
    rxn = compute_day0_reaction("TATA", "2023-10-15", ohlcv)
    # prev_close for 2023-10-15 is 105 (from _make_ohlcv), close is 110
    # log(110/105) ≈ 0.0465
    assert rxn is not None
    assert abs(rxn - math.log(110.0 / 105.0)) < 1e-6


def test_compute_day0_reaction_missing_date():
    dates = ["2023-10-13", "2023-10-14"]
    prices = [100.0, 105.0]
    ohlcv = _make_ohlcv("TATA", dates, prices)
    rxn = compute_day0_reaction("TATA", "2023-10-15", ohlcv)
    assert rxn is None


def test_compute_day0_reaction_unknown_symbol():
    dates = ["2023-10-15"]
    prices = [100.0]
    ohlcv = _make_ohlcv("TATA", dates, prices)
    rxn = compute_day0_reaction("WIPRO", "2023-10-15", ohlcv)
    assert rxn is None


# ── Tests: days since last result ─────────────────────────────────────────────

def test_days_since_last_result():
    earnings = _make_earnings([
        {"symbol": "TATA", "business_date": "2023-07-15",
         "fiscal_quarter": 1, "fiscal_year": 2024,
         "eps_reported": 10.0, "revenue_cr": 1000.0,
         "as_of_timestamp": pd.Timestamp("2023-07-15 12:30:00")},
    ])
    days = compute_days_since_last_result("TATA", "2023-10-15", earnings)
    assert days is not None
    # 2023-10-15 - 2023-07-15 = 92 days
    assert abs(days - 92) < 2


def test_days_since_last_result_no_prior():
    earnings = _make_earnings([])
    days = compute_days_since_last_result("TATA", "2023-10-15", earnings)
    assert days is None


# ── Tests: hold-out guard ─────────────────────────────────────────────────────

def test_build_earnings_features_holdout_rejected():
    earnings = _make_earnings([])
    ohlcv = _make_ohlcv("TATA", ["2024-08-15"], [100.0])
    with pytest.raises(ValueError, match="HOLD-OUT VIOLATION"):
        build_earnings_features(
            symbol="TATA",
            announcement_date="2024-08-15",
            earnings_df=earnings,
            ohlcv=ohlcv,
        )


def test_build_target_holdout_rejected():
    ohlcv = _make_ohlcv("TATA", ["2024-08-15"], [100.0])
    with pytest.raises(ValueError, match="HOLD-OUT VIOLATION"):
        build_target("TATA", "2024-08-15", ohlcv)


# ── Tests: build_target ───────────────────────────────────────────────────────

def test_build_target_positive_return():
    # 5-day forward return > 40 bps → target = 1
    dates = [f"2023-{m:02d}-{d:02d}" for m, d in [
        (10, 10), (10, 11), (10, 12), (10, 13), (10, 15), (10, 16)
    ]]
    # Entry at 2023-10-10 close=100, exit at 2023-10-15 close=100.6 → +60bps
    prices = [100.0, 100.1, 100.2, 100.3, 100.5, 100.6]
    ohlcv = _make_ohlcv("TATA", dates, prices)
    target = build_target("TATA", "2023-10-10", ohlcv)
    assert target == 1.0


def test_build_target_negative_return():
    dates = [f"2023-{m:02d}-{d:02d}" for m, d in [
        (10, 10), (10, 11), (10, 12), (10, 13), (10, 15), (10, 16)
    ]]
    # Entry close=100, exit close=99.8 → negative return
    prices = [100.0, 99.9, 99.85, 99.82, 99.81, 99.8]
    ohlcv = _make_ohlcv("TATA", dates, prices)
    target = build_target("TATA", "2023-10-10", ohlcv)
    assert target == 0.0


def test_build_target_insufficient_history():
    dates = ["2023-10-10", "2023-10-11"]
    prices = [100.0, 100.5]
    ohlcv = _make_ohlcv("TATA", dates, prices)
    target = build_target("TATA", "2023-10-10", ohlcv)
    assert target is None


# ── Tests: build_earnings_features returns correct keys ───────────────────────

def test_build_earnings_features_returns_all_keys():
    earnings = _make_earnings([
        {"symbol": "TATA", "business_date": "2022-10-15",
         "fiscal_quarter": 2, "fiscal_year": 2023,
         "eps_reported": 10.0, "revenue_cr": 1000.0,
         "as_of_timestamp": pd.Timestamp("2022-10-15 12:30:00")},
        {"symbol": "TATA", "business_date": "2023-10-15",
         "fiscal_quarter": 2, "fiscal_year": 2024,
         "eps_reported": 12.0, "revenue_cr": 1200.0,
         "as_of_timestamp": pd.Timestamp("2023-10-15 12:30:00")},
    ])
    dates = pd.date_range("2023-09-01", "2023-10-20", freq="B").strftime("%Y-%m-%d").tolist()
    prices = list(range(100, 100 + len(dates)))
    ohlcv = _make_ohlcv("TATA", dates, prices)

    result = build_earnings_features(
        symbol="TATA",
        announcement_date="2023-10-15",
        earnings_df=earnings,
        ohlcv=ohlcv,
    )

    for feat in REGISTERED_FEATURES:
        assert feat in result, f"Feature {feat!r} missing from build_earnings_features output"


def test_build_earnings_features_seasonality_bounded():
    earnings = _make_earnings([
        {"symbol": "TATA", "business_date": "2023-10-15",
         "fiscal_quarter": 2, "fiscal_year": 2024,
         "eps_reported": 12.0, "revenue_cr": 1200.0,
         "as_of_timestamp": pd.Timestamp("2023-10-15 12:30:00")},
    ])
    ohlcv = _make_ohlcv("TATA", ["2023-10-15"], [100.0])
    result = build_earnings_features("TATA", "2023-10-15", earnings, ohlcv)
    assert -1 <= result["quarter_sin"] <= 1
    assert -1 <= result["quarter_cos"] <= 1
