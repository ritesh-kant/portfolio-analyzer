"""Tests for Block 4: NSE Bhavcopy PIT ingest + pit_loader.

All tests are offline — they synthesise ZIP/CSV bytes rather than hitting NSE.
The network-facing code (ingest_range + _download_bhavcopy) is covered only
through integration tests run manually; the unit tests here validate:

1. CSV format detection and normalisation (old + new format).
2. as_of_timestamp stamping (18:00 IST = 12:30 UTC).
3. pit_loader.load() PIT filter: snapshot_at blocks future-dated rows.
4. pit_loader.load() symbol + series filters.
5. pit_loader.load() returns empty DataFrame (not an error) when lake is empty.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, timezone

import pandas as pd
import pytest

from quant.data.ingest import add_pit_timestamp, parse_zip
from quant.data.pit_loader import load


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_zip(csv_content: str, filename: str = "bhav.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(filename, csv_content)
    return buf.getvalue()


OLD_FORMAT_CSV = (
    "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,"
    "TIMESTAMP,TOTALTRADES,ISIN\n"
    "RELIANCE,EQ,2800.00,2850.00,2780.00,2830.00,2830.00,2790.00,1000000,28300000000,"
    "01-JAN-2020,5000,INE002A01018\n"
    "INFY,EQ,1500.00,1520.00,1490.00,1510.00,1510.00,1495.00,500000,7550000000,"
    "01-JAN-2020,3000,INE009A01021\n"
    "INFY,BE,1500.00,1520.00,1490.00,1510.00,1510.00,1495.00,1000,1510000000,"
    "01-JAN-2020,50,INE009A01021\n"
)

NEW_FORMAT_CSV = (
    "SYMBOL,SERIES,DATE1,PREV_CLOSE,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,LAST_PRICE,"
    "CLOSE_PRICE,AVG_PRICE,TTL_TRD_QNTY,TURNOVER_LACS,NO_OF_TRADES,DELIV_QTY,DELIV_PER\n"
    "TCS,EQ,01-JAN-2024,3900.00,3920.00,3950.00,3890.00,3930.00,3935.00,3920.00,"
    "800000,31480.00,4500,400000,50.00\n"
    "WIPRO,EQ,01-JAN-2024,450.00,455.00,460.00,448.00,457.00,458.00,455.00,"
    "2000000,9160.00,8000,1000000,50.00\n"
)

BUSINESS_DATE = date(2020, 1, 1)
BUSINESS_DATE_2024 = date(2024, 1, 1)


# ── Format detection + normalisation ─────────────────────────────────────────

class TestParseZip:
    def test_old_format_parses_columns(self):
        zip_bytes = _make_zip(OLD_FORMAT_CSV)
        df = parse_zip(zip_bytes, BUSINESS_DATE)
        assert df is not None
        assert set(["symbol", "series", "open", "high", "low", "close",
                    "prev_close", "volume", "turnover_lacs", "as_of_timestamp"]).issubset(df.columns)

    def test_old_format_row_count(self):
        zip_bytes = _make_zip(OLD_FORMAT_CSV)
        df = parse_zip(zip_bytes, BUSINESS_DATE)
        assert df is not None
        assert len(df) == 3  # RELIANCE EQ + INFY EQ + INFY BE

    def test_old_format_values(self):
        zip_bytes = _make_zip(OLD_FORMAT_CSV)
        df = parse_zip(zip_bytes, BUSINESS_DATE)
        assert df is not None
        reliance = df[df["symbol"] == "RELIANCE"].iloc[0]
        assert reliance["close"] == pytest.approx(2830.00)
        assert reliance["open"] == pytest.approx(2800.00)
        assert reliance["volume"] == 1_000_000
        # TOTTRDVAL = 28_300_000_000 rupees → 283_000 lacs
        assert reliance["turnover_lacs"] == pytest.approx(283_000.0)

    def test_new_format_parses_columns(self):
        zip_bytes = _make_zip(NEW_FORMAT_CSV, "BhavCopy_NSE_CM.csv")
        df = parse_zip(zip_bytes, BUSINESS_DATE_2024)
        assert df is not None
        assert "close" in df.columns  # mapped from CLOSE_PRICE

    def test_new_format_values(self):
        zip_bytes = _make_zip(NEW_FORMAT_CSV, "BhavCopy_NSE_CM.csv")
        df = parse_zip(zip_bytes, BUSINESS_DATE_2024)
        assert df is not None
        tcs = df[df["symbol"] == "TCS"].iloc[0]
        assert tcs["close"] == pytest.approx(3935.00)
        assert tcs["prev_close"] == pytest.approx(3900.00)
        assert tcs["turnover_lacs"] == pytest.approx(31480.00)

    def test_empty_zip_returns_none(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w"):
            pass
        assert parse_zip(buf.getvalue(), BUSINESS_DATE) is None

    def test_malformed_zip_returns_none(self):
        assert parse_zip(b"not a zip", BUSINESS_DATE) is None

    def test_business_date_recorded(self):
        zip_bytes = _make_zip(OLD_FORMAT_CSV)
        df = parse_zip(zip_bytes, BUSINESS_DATE)
        assert df is not None
        assert (df["business_date"] == BUSINESS_DATE).all()


# ── PIT timestamp ─────────────────────────────────────────────────────────────

class TestAddPitTimestamp:
    def test_timestamp_is_utc(self):
        df = pd.DataFrame({"business_date": [date(2020, 1, 1)], "close": [100.0]})
        df = add_pit_timestamp(df)
        ts = df["as_of_timestamp"].iloc[0]
        assert ts.tzinfo is not None
        assert str(ts.tzinfo) in ("UTC", "pytz.UTC")

    def test_timestamp_value(self):
        # 2020-01-01 18:00 IST = 2020-01-01 12:30 UTC
        df = pd.DataFrame({"business_date": [date(2020, 1, 1)], "close": [100.0]})
        df = add_pit_timestamp(df)
        ts = df["as_of_timestamp"].iloc[0]
        assert ts.hour == 12
        assert ts.minute == 30
        assert ts.date() == date(2020, 1, 1)

    def test_multiple_dates(self):
        dates = [date(2023, 3, 15), date(2023, 3, 16), date(2023, 3, 17)]
        df = pd.DataFrame({"business_date": dates, "close": [100.0, 101.0, 102.0]})
        df = add_pit_timestamp(df)
        for _, row in df.iterrows():
            assert row["as_of_timestamp"].hour == 12
            assert row["as_of_timestamp"].minute == 30


# ── pit_loader.load() PIT filter ─────────────────────────────────────────────

@pytest.fixture
def synthetic_lake(tmp_path, monkeypatch):
    """Write a two-year synthetic lake and patch QUANT_DATA_DIR to point at it."""
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    lake = tmp_path / "nse_bhavcopy"
    lake.mkdir()

    # 2022 data: RELIANCE EQ, 2022-01-03
    df_2022 = pd.DataFrame({
        "symbol": ["RELIANCE", "INFY"],
        "series": ["EQ", "EQ"],
        "business_date": [date(2022, 1, 3), date(2022, 1, 3)],
        "open": [2800.0, 1500.0],
        "high": [2850.0, 1520.0],
        "low": [2780.0, 1490.0],
        "close": [2830.0, 1510.0],
        "prev_close": [2790.0, 1495.0],
        "volume": [1_000_000, 500_000],
        "turnover_lacs": [283_000.0, 7_550.0],
        # as_of: 2022-01-03 12:30 UTC (18:00 IST)
        "as_of_timestamp": pd.to_datetime(["2022-01-03T12:30:00Z", "2022-01-03T12:30:00Z"]),
    })
    df_2022.to_parquet(lake / "2022.parquet", index=False)

    # 2023 data: two dates
    df_2023 = pd.DataFrame({
        "symbol": ["TCS", "TCS", "WIPRO"],
        "series": ["EQ", "BE", "EQ"],
        "business_date": [date(2023, 6, 30), date(2023, 6, 30), date(2023, 7, 1)],
        "open": [3900.0, 3900.0, 450.0],
        "high": [3950.0, 3950.0, 460.0],
        "low": [3890.0, 3890.0, 448.0],
        "close": [3935.0, 3935.0, 458.0],
        "prev_close": [3900.0, 3900.0, 450.0],
        "volume": [800_000, 100, 2_000_000],
        "turnover_lacs": [31_480.0, 393.5, 9_160.0],
        "as_of_timestamp": pd.to_datetime([
            "2023-06-30T12:30:00Z",
            "2023-06-30T12:30:00Z",
            "2023-07-01T12:30:00Z",
        ]),
    })
    df_2023.to_parquet(lake / "2023.parquet", index=False)

    return lake


class TestPitLoader:
    def test_basic_load(self, synthetic_lake):
        df = load(None, "2022-01-03", "2022-01-03")
        assert len(df) == 2
        assert "RELIANCE" in df.index.get_level_values("symbol")

    def test_symbol_filter(self, synthetic_lake):
        df = load("RELIANCE", "2022-01-03", "2022-01-03")
        assert len(df) == 1
        assert df.index.get_level_values("symbol")[0] == "RELIANCE"

    def test_symbol_list_filter(self, synthetic_lake):
        df = load(["RELIANCE", "INFY"], "2022-01-03", "2022-01-03")
        assert len(df) == 2

    def test_series_filter_default_eq(self, synthetic_lake):
        # 2023-06-30 has TCS in both EQ and BE; default series="EQ" should return only EQ
        df = load("TCS", "2023-06-30", "2023-06-30")
        assert len(df) == 1
        assert df.index.get_level_values("symbol")[0] == "TCS"

    def test_series_filter_none_returns_all(self, synthetic_lake):
        df = load("TCS", "2023-06-30", "2023-06-30", series=None)
        assert len(df) == 2  # EQ + BE

    def test_cross_year_range(self, synthetic_lake):
        df = load(None, "2022-01-03", "2023-06-30")
        # 2022: RELIANCE + INFY; 2023: TCS EQ + TCS BE (filtered to EQ by default)
        assert len(df) == 3  # RELIANCE, INFY, TCS

    def test_empty_when_no_lake(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
        df = load("RELIANCE", "2022-01-03", "2022-01-03")
        assert df.empty

    # ── PIT filter tests ──────────────────────────────────────────────────────

    def test_pit_filter_blocks_same_day_future_data(self, synthetic_lake):
        # snapshot_at = 2023-06-30 06:00 UTC — BEFORE Bhavcopy published (12:30 UTC)
        # Should return NO rows for 2023-06-30
        df = load(None, "2023-06-30", "2023-06-30", snapshot_at="2023-06-30T06:00:00Z")
        assert df.empty

    def test_pit_filter_allows_data_after_publication(self, synthetic_lake):
        # snapshot_at = 2023-06-30 13:00 UTC — AFTER Bhavcopy published (12:30 UTC)
        df = load("TCS", "2023-06-30", "2023-06-30", snapshot_at="2023-06-30T13:00:00Z")
        assert len(df) == 1

    def test_pit_filter_exact_boundary(self, synthetic_lake):
        # snapshot_at = exactly 12:30 UTC = publication time; row should be included
        df = load("TCS", "2023-06-30", "2023-06-30", snapshot_at="2023-06-30T12:30:00Z")
        assert len(df) == 1

    def test_pit_filter_none_returns_all(self, synthetic_lake):
        # No snapshot_at → all rows in date range returned
        df = load(None, "2023-06-30", "2023-07-01")
        # TCS EQ (2023-06-30) + WIPRO EQ (2023-07-01)
        assert len(df) == 2

    def test_pit_filter_blocks_future_year(self, synthetic_lake):
        # snapshot_at = 2022-12-31 — should return 2022 data but nothing from 2023
        df = load(None, "2022-01-03", "2023-06-30", snapshot_at="2022-12-31T23:59:59Z")
        symbols = set(df.index.get_level_values("symbol"))
        assert "RELIANCE" in symbols
        assert "TCS" not in symbols

    def test_index_columns(self, synthetic_lake):
        df = load("RELIANCE", "2022-01-03", "2022-01-03")
        assert df.index.names == ["business_date", "symbol"]

    def test_return_columns(self, synthetic_lake):
        df = load("RELIANCE", "2022-01-03", "2022-01-03")
        for col in ["open", "high", "low", "close", "prev_close", "volume",
                    "turnover_lacs", "as_of_timestamp"]:
            assert col in df.columns
