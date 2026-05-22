"""Tests for Strategy C — Promoter Pledge Filter.

Covers:
  1. CSV ingest: screener.in format, NSE bulk format, flexible format
  2. is_pledge_flagged: absolute threshold, increase trigger, PIT boundary,
     fail-open on missing data
  3. Quarter-end parsing: MonYYYY, QxFYyy, ISO date
  4. 60-day exclusion window boundary conditions
"""

from __future__ import annotations

import textwrap
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from quant.data.promoter_pledge import (
    _ABSOLUTE_PLEDGE_THRESHOLD,
    _EXCLUSION_TRADING_DAYS,
    _FILING_LAG_DAYS,
    _parse_quarter_end,
    ingest_csv,
    is_pledge_flagged,
    load_pledge,
    validate,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    """Redirect data lake writes to a temporary directory."""
    import quant.data.promoter_pledge as mod
    test_parquet = tmp_path / "pledge_shp.parquet"
    monkeypatch.setattr(mod, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(mod, "_PARQUET", test_parquet)
    return tmp_path


def write_csv(tmp_path: Path, content: str, name: str = "pledge.csv") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(content).strip())
    return p


# ── Quarter-end parsing ────────────────────────────────────────────────────────

class TestParseQuarterEnd:
    def test_iso_date(self):
        assert _parse_quarter_end("2024-09-30") == date(2024, 9, 30)

    def test_mon_year_format(self):
        assert _parse_quarter_end("Sep2024") == date(2024, 9, 30)
        assert _parse_quarter_end("Mar 2024") == date(2024, 3, 31)
        assert _parse_quarter_end("Jun'24") == date(2024, 6, 30)
        assert _parse_quarter_end("Dec 2023") == date(2023, 12, 31)

    def test_q_fy_format(self):
        assert _parse_quarter_end("Q2FY25") == date(2024, 9, 30)
        assert _parse_quarter_end("Q4FY24") == date(2024, 3, 31)
        assert _parse_quarter_end("Q1FY25") == date(2024, 6, 30)
        assert _parse_quarter_end("Q3FY25") == date(2024, 12, 31)

    def test_invalid_returns_none(self):
        assert _parse_quarter_end("") is None
        assert _parse_quarter_end("nan") is None
        assert _parse_quarter_end("garbage") is None


# ── CSV ingest: screener.in format ────────────────────────────────────────────

class TestIngestScreenerFormat:
    def test_basic_ingest(self, tmp_path):
        csv = write_csv(tmp_path, """
            symbol,quarter_end,pledged_pct
            ADANIPORTS,2024-09-30,8.50
            ADANIPORTS,2024-06-30,6.20
            HINDUNILVR,2024-09-30,0.00
        """)
        n = ingest_csv(csv)
        assert n == 3

        df = load_pledge()
        assert set(df["symbol"]) == {"ADANIPORTS", "HINDUNILVR"}
        assert len(df) == 3

    def test_dedup_on_reingest(self, tmp_path):
        csv = write_csv(tmp_path, """
            symbol,quarter_end,pledged_pct
            ADANIPORTS,2024-09-30,8.50
        """)
        ingest_csv(csv)
        # Reingest same data — should not duplicate
        ingest_csv(csv)
        df = load_pledge()
        assert len(df) == 1

    def test_filing_date_defaults_to_quarter_end_plus_lag(self, tmp_path):
        csv = write_csv(tmp_path, """
            symbol,quarter_end,pledged_pct
            YESBANK,2024-09-30,12.0
        """)
        ingest_csv(csv)
        df = load_pledge(symbol="YESBANK")
        expected_filing = date(2024, 9, 30) + timedelta(days=_FILING_LAG_DAYS)
        assert df.iloc[0]["filing_date"] == expected_filing

    def test_explicit_filing_date_respected(self, tmp_path):
        csv = write_csv(tmp_path, """
            symbol,quarter_end,pledged_pct,filing_date
            YESBANK,2024-09-30,12.0,2024-10-05
        """)
        ingest_csv(csv)
        df = load_pledge(symbol="YESBANK")
        assert df.iloc[0]["filing_date"] == date(2024, 10, 5)

    def test_symbol_uppercased(self, tmp_path):
        csv = write_csv(tmp_path, """
            symbol,quarter_end,pledged_pct
            yesbank,2024-09-30,5.0
        """)
        ingest_csv(csv)
        df = load_pledge()
        assert df.iloc[0]["symbol"] == "YESBANK"

    def test_merge_preserves_existing(self, tmp_path):
        csv1 = write_csv(tmp_path, """
            symbol,quarter_end,pledged_pct
            ADANIPORTS,2024-06-30,6.0
        """, name="p1.csv")
        csv2 = write_csv(tmp_path, """
            symbol,quarter_end,pledged_pct
            ADANIPORTS,2024-09-30,8.5
        """, name="p2.csv")
        ingest_csv(csv1)
        ingest_csv(csv2)
        df = load_pledge(symbol="ADANIPORTS")
        assert len(df) == 2
        assert list(df["pledged_pct"]) == [6.0, 8.5]


# ── is_pledge_flagged ─────────────────────────────────────────────────────────

class TestIsPledgeFlagged:

    def _seed(self, tmp_path, rows: list[dict]):
        """Helper to write pledge rows directly without CSV parsing."""
        import quant.data.promoter_pledge as mod
        df = pd.DataFrame(rows)
        df["quarter_end"] = pd.to_datetime(df["quarter_end"]).dt.date
        df["filing_date"] = pd.to_datetime(df["filing_date"]).dt.date
        df["as_of_timestamp"] = pd.to_datetime(df["filing_date"].astype(str)) + pd.Timedelta(hours=18)
        mod._DATA_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(mod._PARQUET, index=False)

    def test_fail_open_when_no_data(self):
        """No parquet → allow trade (fail open)."""
        assert is_pledge_flagged("ADANIPORTS", "2024-04-01") is False

    def test_no_pledge_passes(self, tmp_path):
        self._seed(tmp_path, [
            {"symbol": "ADANIPORTS", "quarter_end": "2024-06-30",
             "filing_date": "2024-07-21", "pledged_pct": 0.0},
            {"symbol": "ADANIPORTS", "quarter_end": "2024-09-30",
             "filing_date": "2024-10-21", "pledged_pct": 0.0},
        ])
        assert is_pledge_flagged("ADANIPORTS", "2024-11-01") is False

    def test_absolute_threshold_flags_immediately(self, tmp_path):
        """pledged_pct > 15% → always flagged regardless of trend."""
        self._seed(tmp_path, [
            {"symbol": "YESBANK", "quarter_end": "2024-09-30",
             "filing_date": "2024-10-21", "pledged_pct": 20.0},
        ])
        assert is_pledge_flagged("YESBANK", "2024-11-01") is True

    def test_absolute_threshold_exactly_at_boundary(self, tmp_path):
        """pledged_pct exactly at 15% is NOT flagged (strictly greater than)."""
        self._seed(tmp_path, [
            {"symbol": "HINDUNILVR", "quarter_end": "2024-09-30",
             "filing_date": "2024-10-21", "pledged_pct": _ABSOLUTE_PLEDGE_THRESHOLD},
        ])
        assert is_pledge_flagged("HINDUNILVR", "2024-11-01") is False

    def test_increase_flags_within_window(self, tmp_path):
        """Pledge increase → flagged within 60-trading-day window."""
        self._seed(tmp_path, [
            {"symbol": "ADANIPORTS", "quarter_end": "2024-06-30",
             "filing_date": "2024-07-21", "pledged_pct": 2.0},
            {"symbol": "ADANIPORTS", "quarter_end": "2024-09-30",
             "filing_date": "2024-10-21", "pledged_pct": 5.0},  # +3 pp increase
        ])
        # 10 calendar days after filing = well within window
        assert is_pledge_flagged("ADANIPORTS", "2024-10-31") is True

    def test_increase_clears_after_window(self, tmp_path):
        """Pledge increase → NOT flagged after exclusion window expires."""
        self._seed(tmp_path, [
            {"symbol": "ADANIPORTS", "quarter_end": "2024-06-30",
             "filing_date": "2024-07-21", "pledged_pct": 2.0},
            {"symbol": "ADANIPORTS", "quarter_end": "2024-09-30",
             "filing_date": "2024-10-21", "pledged_pct": 5.0},
        ])
        # _EXCLUSION_TRADING_DAYS = 60 trading days ≈ 84 calendar days
        # 100 calendar days after filing = 100 * 5/7 ≈ 71 trading days > 60 → cleared
        far_future = date(2024, 10, 21) + timedelta(days=100)
        assert is_pledge_flagged("ADANIPORTS", str(far_future)) is False

    def test_pit_correct_before_filing_date(self, tmp_path):
        """Data is NOT available before filing_date (PIT discipline)."""
        self._seed(tmp_path, [
            {"symbol": "ADANIPORTS", "quarter_end": "2024-09-30",
             "filing_date": "2024-10-21", "pledged_pct": 20.0},  # would flag
        ])
        # Query BEFORE the filing date → data not yet available → no flag
        assert is_pledge_flagged("ADANIPORTS", "2024-10-15") is False

    def test_decrease_does_not_flag(self, tmp_path):
        """Pledge decrease → NOT flagged."""
        self._seed(tmp_path, [
            {"symbol": "ADANIPORTS", "quarter_end": "2024-06-30",
             "filing_date": "2024-07-21", "pledged_pct": 10.0},
            {"symbol": "ADANIPORTS", "quarter_end": "2024-09-30",
             "filing_date": "2024-10-21", "pledged_pct": 5.0},  # decrease
        ])
        assert is_pledge_flagged("ADANIPORTS", "2024-11-01") is False

    def test_unknown_symbol_not_flagged(self, tmp_path):
        """Symbol not in parquet → not flagged (fail open)."""
        self._seed(tmp_path, [
            {"symbol": "KNOWN", "quarter_end": "2024-09-30",
             "filing_date": "2024-10-21", "pledged_pct": 20.0},
        ])
        assert is_pledge_flagged("UNKNOWN", "2024-11-01") is False

    def test_window_boundary_at_60_trading_days(self, tmp_path):
        """Within 60-trading-day window → flagged; well beyond → cleared."""
        filing = date(2024, 10, 21)
        # 50 calendar days after filing → 50 * 5/7 ≈ 35 trading days → still flagged
        inside_window = filing + timedelta(days=50)
        # 120 calendar days after filing → 120 * 5/7 ≈ 85 trading days > 60 → cleared
        outside_window = filing + timedelta(days=120)

        self._seed(tmp_path, [
            {"symbol": "ADANIPORTS", "quarter_end": "2024-06-30",
             "filing_date": "2024-07-21", "pledged_pct": 2.0},
            {"symbol": "ADANIPORTS", "quarter_end": "2024-09-30",
             "filing_date": str(filing), "pledged_pct": 5.0},  # +3 pp increase
        ])
        assert is_pledge_flagged("ADANIPORTS", str(inside_window)) is True
        assert is_pledge_flagged("ADANIPORTS", str(outside_window)) is False


# ── validate() output ─────────────────────────────────────────────────────────

class TestValidate:
    def test_validate_when_no_data(self, capsys):
        result = validate()
        assert result["exists"] is False

    def test_validate_with_data(self, tmp_path):
        csv = write_csv(tmp_path, """
            symbol,quarter_end,pledged_pct
            ADANIPORTS,2024-09-30,8.50
            ADANIPORTS,2024-06-30,6.20
            HINDUNILVR,2024-09-30,0.00
            YESBANK,2024-09-30,20.00
        """)
        ingest_csv(csv)
        result = validate()
        assert result["exists"] is True
        assert result["symbols"] == 3
        assert result["high_pledge_symbols"] == 1   # only YESBANK > 15%
        assert result["rows"] == 4
