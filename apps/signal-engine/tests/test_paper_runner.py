"""Tests for quant.execution.paper_runner. Month 4."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant.execution.paper_runner import (
    _close_price_for,
    _trading_days_held,
)


class TestTradingDaysHeld:
    def test_same_day(self):
        assert _trading_days_held("2024-01-08", date(2024, 1, 8)) == 0

    def test_one_week(self):
        # Mon 2024-01-08 to Mon 2024-01-15: 5 trading days
        result = _trading_days_held("2024-01-08", date(2024, 1, 15))
        assert result == 5

    def test_five_trading_days_with_weekend(self):
        # Mon 2024-01-08 to Sat 2024-01-13 = 5 calendar days, weekend crosses
        result = _trading_days_held("2024-01-08", date(2024, 1, 13))
        assert result >= 4  # at least 4 trading days

    def test_across_weekend(self):
        # Fri 2024-01-12 to Mon 2024-01-15 = 1 trading day
        result = _trading_days_held("2024-01-12", date(2024, 1, 15))
        assert result <= 2


class TestClosePriceFor:
    def _make_ohlcv(self, rows: list[tuple]) -> pd.DataFrame:
        """Build a minimal ohlcv DataFrame with (business_date, symbol) MultiIndex."""
        records = []
        for symbol, bdate, close in rows:
            records.append({"business_date": pd.Timestamp(bdate).date(),
                            "symbol": symbol, "close": float(close)})
        df = pd.DataFrame(records).set_index(["business_date", "symbol"])
        return df

    def test_exact_date(self):
        ohlcv = self._make_ohlcv([("PIIND", "2024-01-10", 1500.0)])
        assert _close_price_for("PIIND", "2024-01-10", ohlcv) == pytest.approx(1500.0)

    def test_returns_most_recent_before_as_of(self):
        ohlcv = self._make_ohlcv([
            ("PIIND", "2024-01-08", 1490.0),
            ("PIIND", "2024-01-09", 1500.0),
            ("PIIND", "2024-01-10", 1510.0),
        ])
        # as_of is 2024-01-09 → should return 1500, not 1510
        assert _close_price_for("PIIND", "2024-01-09", ohlcv) == pytest.approx(1500.0)

    def test_missing_symbol_returns_none(self):
        ohlcv = self._make_ohlcv([("PIIND", "2024-01-10", 1500.0)])
        assert _close_price_for("UNKNOWN", "2024-01-10", ohlcv) is None

    def test_all_dates_after_as_of_returns_none(self):
        ohlcv = self._make_ohlcv([("PIIND", "2024-01-15", 1500.0)])
        assert _close_price_for("PIIND", "2024-01-10", ohlcv) is None

    def test_case_insensitive(self):
        ohlcv = self._make_ohlcv([("PIIND", "2024-01-10", 1500.0)])
        assert _close_price_for("piind", "2024-01-10", ohlcv) == pytest.approx(1500.0)
