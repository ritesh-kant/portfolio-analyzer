"""NSE sec_bhavdata_full ingest — delivery data for Strategy E (IDI).

NSE publishes daily equity delivery data at:
  https://archives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv

This file is distinct from the standard Bhavcopy — it includes:
  DELIV_QTY  : shares actually settled at T+2 (i.e., not squared off intraday)
  DELIV_PER  : delivery % = DELIV_QTY / TTL_TRD_QNTY × 100

Availability: approximately 2020-01-01 onwards (earlier dates return HTTP 404).

Storage layout
--------------
  data/lake/delivery/nse_delivery_YYYY.parquet   — one parquet per calendar year

Schema
------
  symbol          str      NSE trading symbol (upper-cased)
  business_date   date     trading date
  series          str      equity series code (EQ, BE, BT, …)
  open            float    open price
  close           float    close price
  prev_close      float    previous close
  volume          int      total traded quantity (TTL_TRD_QNTY)
  deliv_qty       int      delivered quantity
  deliv_pct       float    delivery percentage (0–100)
  as_of_timestamp datetime date at 20:00 IST (PIT boundary for signal generation)

Usage
-----
  # Bulk historical download (run once):
  python -m quant.data.delivery_ingest --start 2020-01-01 --end 2024-06-30

  # Incremental daily update (run after market close each trading day):
  python -m quant.data.delivery_ingest --start 2024-07-01

  # Validate what's on disk:
  python -m quant.data.delivery_ingest --validate
"""

from __future__ import annotations

import argparse
import io
import logging
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[3]   # …/signal-engine
_DATA_DIR = _REPO_ROOT / "data" / "lake" / "delivery"

# ── NSE URL template ──────────────────────────────────────────────────────────
# Date format in filename: DDMMYYYY  e.g. 15012024
_URL_TEMPLATE = (
    "https://archives.nseindia.com/products/content/"
    "sec_bhavdata_full_{date}.csv"
)

# NSE requires a browser-like User-Agent
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.nseindia.com/",
}

# ── Column mapping ─────────────────────────────────────────────────────────────
# Raw CSV columns from sec_bhavdata_full (with and without leading spaces)
_RAW_REQUIRED = {"SYMBOL", "SERIES", "DATE1", "PREV_CLOSE", "OPEN_PRICE",
                 "CLOSE_PRICE", "TTL_TRD_QNTY", "DELIV_QTY", "DELIV_PER"}

_COL_MAP = {
    "SYMBOL": "symbol",
    "SERIES": "series",
    "OPEN_PRICE": "open",
    "CLOSE_PRICE": "close",
    "PREV_CLOSE": "prev_close",
    "TTL_TRD_QNTY": "volume",
    "DELIV_QTY": "deliv_qty",
    "DELIV_PER": "deliv_pct",
}

# ── NSE public holiday list (supplemental) ────────────────────────────────────
# NSE doesn't provide a machine-readable holiday calendar in the archives.
# Weekends are filtered automatically; these are the known exchange holidays
# that fall on weekdays (updated through end of dev period 2024-06-30).
# A missing file (HTTP 404) is also treated as a non-trading day.
_KNOWN_HOLIDAYS: frozenset[date] = frozenset({
    # 2020
    date(2020, 2, 21), date(2020, 3, 10), date(2020, 4, 2), date(2020, 4, 6),
    date(2020, 4, 10), date(2020, 4, 14), date(2020, 5, 25), date(2020, 10, 2),
    date(2020, 11, 16), date(2020, 11, 30),
    # 2021
    date(2021, 1, 26), date(2021, 3, 11), date(2021, 3, 29), date(2021, 4, 2),
    date(2021, 4, 14), date(2021, 4, 21), date(2021, 5, 13), date(2021, 7, 21),
    date(2021, 8, 19), date(2021, 9, 10), date(2021, 10, 2), date(2021, 10, 15),
    date(2021, 11, 4), date(2021, 11, 5), date(2021, 11, 19),
    # 2022
    date(2022, 1, 26), date(2022, 3, 1), date(2022, 3, 18), date(2022, 4, 14),
    date(2022, 4, 15), date(2022, 5, 3), date(2022, 8, 9), date(2022, 8, 15),
    date(2022, 8, 31), date(2022, 10, 2), date(2022, 10, 5), date(2022, 10, 24),
    date(2022, 10, 26), date(2022, 11, 8),
    # 2023
    date(2023, 1, 26), date(2023, 3, 7), date(2023, 3, 30), date(2023, 4, 4),
    date(2023, 4, 7), date(2023, 4, 14), date(2023, 4, 22), date(2023, 5, 1),
    date(2023, 6, 28), date(2023, 8, 15), date(2023, 9, 19), date(2023, 10, 2),
    date(2023, 10, 24), date(2023, 11, 14), date(2023, 11, 27),
    date(2023, 12, 25),
    # 2024
    date(2024, 1, 22), date(2024, 1, 26), date(2024, 3, 8), date(2024, 3, 25),
    date(2024, 3, 29), date(2024, 4, 11), date(2024, 4, 14), date(2024, 4, 17),
    date(2024, 4, 21), date(2024, 5, 23), date(2024, 6, 17),
})


def _is_trading_day(d: date) -> bool:
    """Return True if d is a weekday not in the known holiday list."""
    return d.weekday() < 5 and d not in _KNOWN_HOLIDAYS


def _date_str(d: date) -> str:
    """Format date as DDMMYYYY for the NSE URL."""
    return d.strftime("%d%m%Y")


def _parse_raw(csv_text: str, trading_date: date) -> pd.DataFrame | None:
    """Parse a raw sec_bhavdata_full CSV string into a clean DataFrame.

    Returns None if the CSV is empty or malformed.
    """
    try:
        df = pd.read_csv(io.StringIO(csv_text), sep=",")
    except Exception as exc:
        logger.warning("CSV parse error for %s: %s", trading_date, exc)
        return None

    # Strip leading/trailing whitespace from column names
    df.columns = [c.strip() for c in df.columns]

    missing = _RAW_REQUIRED - set(df.columns)
    if missing:
        logger.warning("Missing columns %s in %s — skipping", missing, trading_date)
        return None

    if df.empty:
        logger.debug("Empty file for %s", trading_date)
        return None

    # Coerce types
    df["SYMBOL"] = df["SYMBOL"].str.strip().str.upper()
    df["SERIES"] = df["SERIES"].str.strip().str.upper()
    df["DATE1"] = df["DATE1"].str.strip()

    numeric_cols = ["PREV_CLOSE", "OPEN_PRICE", "CLOSE_PRICE",
                    "TTL_TRD_QNTY", "DELIV_QTY", "DELIV_PER"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows with no delivery data
    df = df.dropna(subset=["DELIV_PER", "TTL_TRD_QNTY", "CLOSE_PRICE"])

    # Basic sanity bounds
    df = df[(df["DELIV_PER"] >= 0) & (df["DELIV_PER"] <= 100)]
    df = df[df["TTL_TRD_QNTY"] > 0]
    df = df[df["CLOSE_PRICE"] > 0]

    # Rename + select
    df = df.rename(columns=_COL_MAP)
    df["business_date"] = trading_date
    df["as_of_timestamp"] = pd.Timestamp(trading_date) + pd.Timedelta(hours=20)

    keep = ["symbol", "series", "business_date", "open", "close", "prev_close",
            "volume", "deliv_qty", "deliv_pct", "as_of_timestamp"]
    df = df[[c for c in keep if c in df.columns]].copy()

    df["volume"] = df["volume"].astype("int64")
    df["deliv_qty"] = df["deliv_qty"].astype("int64")

    return df


def fetch_day(trading_date: date, session: requests.Session,
              retry: int = 2, sleep_s: float = 1.5) -> pd.DataFrame | None:
    """Fetch and parse sec_bhavdata_full for a single trading date.

    Returns a clean DataFrame or None on error/missing data.
    Does NOT write to disk — caller accumulates and writes per-year parquets.
    """
    url = _URL_TEMPLATE.format(date=_date_str(trading_date))
    for attempt in range(retry + 1):
        try:
            resp = session.get(url, headers=_HEADERS, timeout=30)
        except requests.RequestException as exc:
            logger.warning("[%s] Request error (attempt %d): %s",
                           trading_date, attempt + 1, exc)
            if attempt < retry:
                time.sleep(sleep_s)
            continue

        if resp.status_code == 404:
            logger.debug("[%s] 404 — probably a holiday/non-trading day", trading_date)
            return None

        if resp.status_code != 200:
            logger.warning("[%s] HTTP %s (attempt %d)",
                           trading_date, resp.status_code, attempt + 1)
            if attempt < retry:
                time.sleep(sleep_s)
            continue

        df = _parse_raw(resp.text, trading_date)
        return df  # may be None if CSV is malformed

    return None


def _parquet_path(year: int) -> Path:
    return _DATA_DIR / f"nse_delivery_{year}.parquet"


def _load_existing(year: int) -> pd.DataFrame:
    p = _parquet_path(year)
    if p.exists():
        return pd.read_parquet(p)
    return pd.DataFrame(columns=["symbol", "series", "business_date", "open",
                                  "close", "prev_close", "volume", "deliv_qty",
                                  "deliv_pct", "as_of_timestamp"])


def _write_year(year: int, df: pd.DataFrame) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    df["business_date"] = pd.to_datetime(df["business_date"]).dt.date
    df = df.drop_duplicates(subset=["symbol", "business_date"]).reset_index(drop=True)
    df.sort_values(["business_date", "symbol"], inplace=True)
    df.to_parquet(_parquet_path(year), index=False)


def ingest(
    start: str | date,
    end: str | date | None = None,
    series_filter: str | None = "EQ",
    delay_s: float = 0.5,
) -> dict[str, int]:
    """Download and store sec_bhavdata_full for date range [start, end].

    Parameters
    ----------
    start : str or date
        First date to fetch (inclusive).
    end : str or date or None
        Last date to fetch (inclusive).  Defaults to yesterday.
    series_filter : str or None
        Keep only this equity series (e.g. "EQ").  None = keep all.
    delay_s : float
        Polite delay between requests in seconds.

    Returns
    -------
    dict with keys: dates_attempted, dates_fetched, rows_written, errors.
    """
    start_d = pd.Timestamp(start).date() if isinstance(start, str) else start
    if end is None:
        end_d = date.today() - timedelta(days=1)
    else:
        end_d = pd.Timestamp(end).date() if isinstance(end, str) else end

    dates = [
        start_d + timedelta(days=i)
        for i in range((end_d - start_d).days + 1)
        if _is_trading_day(start_d + timedelta(days=i))
    ]

    logger.info("Fetching %d trading days: %s → %s", len(dates), start_d, end_d)

    # Group into years for efficient parquet updates
    from collections import defaultdict
    year_buffers: dict[int, list[pd.DataFrame]] = defaultdict(list)

    stats = {"dates_attempted": len(dates), "dates_fetched": 0,
             "rows_written": 0, "errors": 0}

    session = requests.Session()
    session.headers.update(_HEADERS)

    for d in dates:
        df = fetch_day(d, session, retry=2, sleep_s=delay_s * 2)
        if df is not None and not df.empty:
            if series_filter:
                df = df[df["series"] == series_filter]
            if not df.empty:
                year_buffers[d.year].append(df)
                stats["dates_fetched"] += 1
                stats["rows_written"] += len(df)
        else:
            stats["errors"] += 1

        time.sleep(delay_s)

    # Write per-year parquets (merge with existing)
    for year, frames in year_buffers.items():
        existing = _load_existing(year)
        combined = pd.concat([existing] + frames, ignore_index=True)
        _write_year(year, combined)
        logger.info("Year %d: wrote %d rows to %s",
                    year, len(combined), _parquet_path(year))

    return stats


def load_delivery(
    symbol: str | None = None,
    start: str | date | None = None,
    end: str | date | None = None,
    series: str = "EQ",
) -> pd.DataFrame:
    """Load delivery data from parquet store.

    Parameters
    ----------
    symbol : str or None
        Filter to single symbol.  None = all symbols.
    start, end : str or date or None
        Date range filter.
    series : str
        Equity series to include.  Default "EQ".

    Returns
    -------
    DataFrame sorted by (business_date, symbol).
    """
    start_d = pd.Timestamp(start).date() if start else None
    end_d = pd.Timestamp(end).date() if end else None

    frames: list[pd.DataFrame] = []
    for path in sorted(_DATA_DIR.glob("nse_delivery_*.parquet")):
        # Skip years clearly out of range
        try:
            year = int(path.stem.split("_")[-1])
        except ValueError:
            continue
        if start_d and year < start_d.year:
            continue
        if end_d and year > end_d.year:
            continue
        df = pd.read_parquet(path)
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["symbol", "series", "business_date", "open",
                                      "close", "prev_close", "volume", "deliv_qty",
                                      "deliv_pct", "as_of_timestamp"])

    df = pd.concat(frames, ignore_index=True)
    df["business_date"] = pd.to_datetime(df["business_date"]).dt.date

    if series:
        df = df[df["series"] == series]
    if symbol:
        df = df[df["symbol"] == symbol.upper()]
    if start_d:
        df = df[df["business_date"] >= start_d]
    if end_d:
        df = df[df["business_date"] <= end_d]

    return df.sort_values(["business_date", "symbol"]).reset_index(drop=True)


def validate() -> dict:
    """Print a summary of what's on disk and return a stats dict."""
    parquets = sorted(_DATA_DIR.glob("nse_delivery_*.parquet"))
    if not parquets:
        print("No delivery parquets found in", _DATA_DIR)
        return {"exists": False}

    total_rows = 0
    all_dates: list[date] = []
    all_symbols: set[str] = set()
    year_stats: list[dict] = []

    for p in parquets:
        df = pd.read_parquet(p)
        df["business_date"] = pd.to_datetime(df["business_date"]).dt.date
        eq_df = df[df["series"] == "EQ"]
        n = len(eq_df)
        total_rows += n
        if not eq_df.empty:
            all_dates.extend(eq_df["business_date"].unique().tolist())
            all_symbols.update(eq_df["symbol"].unique().tolist())
        year_stats.append({
            "file": p.name,
            "rows_eq": n,
            "trading_days": eq_df["business_date"].nunique() if n else 0,
        })

    all_dates_sorted = sorted(set(all_dates))
    first = all_dates_sorted[0] if all_dates_sorted else None
    last = all_dates_sorted[-1] if all_dates_sorted else None
    trading_days = len(set(all_dates))

    print("=" * 60)
    print("NSE Delivery Data Summary")
    print("=" * 60)
    print(f"  Files : {len(parquets)}")
    print(f"  Range : {first} → {last}")
    print(f"  Days  : {trading_days} trading days")
    print(f"  Symbols: {len(all_symbols)}")
    print(f"  Rows (EQ): {total_rows:,}")
    print()
    for ys in year_stats:
        print(f"  {ys['file']}: {ys['rows_eq']:>8,} rows, {ys['trading_days']} days")
    print("=" * 60)

    return {
        "exists": True,
        "files": len(parquets),
        "first_date": first,
        "last_date": last,
        "trading_days": trading_days,
        "symbols": len(all_symbols),
        "total_rows_eq": total_rows,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Download NSE sec_bhavdata_full delivery files.",
    )
    parser.add_argument("--start", default="2020-01-01",
                        help="Start date YYYY-MM-DD (default: 2020-01-01)")
    parser.add_argument("--end", default=None,
                        help="End date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--series", default="EQ",
                        help="Series filter (default: EQ). Use 'ALL' for no filter.")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Polite delay between HTTP requests in seconds (default: 0.5)")
    parser.add_argument("--validate", action="store_true",
                        help="Just print a summary of what's on disk, then exit.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if args.validate:
        validate()
        return

    series_filter = None if args.series.upper() == "ALL" else args.series.upper()
    stats = ingest(
        start=args.start,
        end=args.end,
        series_filter=series_filter,
        delay_s=args.delay,
    )
    print("\nIngest complete:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    _cli()
