"""NSE Bulk Deal ingest — data pipeline for Strategy F (BDM).

NSE mandates that any entity buying or selling ≥ 0.5% of a listed company's
equity outstanding in a single exchange session must report the transaction to
NSE by 16:00 that day.  NSE discloses the data publicly the same evening.

Historical bulk deal data is available from NSE archives.

Download URLs
-------------
Current day:
  https://archives.nseindia.com/content/equities/bulk.csv

Historical (individual daily files archived on NSE):
  https://archives.nseindia.com/content/equities/bulk_{DDMMYYYY}.csv
  (Availability: approximately 2004-01-01 onwards)

Storage
-------
  data/lake/bulk_deals/nse_bulk_deals.parquet   — single consolidated file

Schema
------
  symbol          str      NSE trading symbol (upper-cased)
  business_date   date     trade date
  client_name     str      buyer/seller entity name (free text from NSE)
  side            str      "BUY" or "SELL"
  quantity        int      shares traded in the bulk deal
  price           float    trade price (weighted avg if multiple lots)
  value_cr        float    approximate deal value in crore (qty × price / 1e7)
  as_of_timestamp datetime business_date at 16:00 IST (PIT boundary)

Usage
-----
  # Bulk historical download (run once):
  python -m quant.data.bulk_deals --start 2015-01-01 --end 2024-06-30

  # Incremental daily update (run after 16:00 IST each trading day):
  python -m quant.data.bulk_deals --start 2024-07-01

  # Validate what's on disk:
  python -m quant.data.bulk_deals --validate

  # Check events for a specific date range:
  python -m quant.data.bulk_deals --validate --start 2023-07-01 --end 2024-06-30
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
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = _REPO_ROOT / "data" / "lake" / "bulk_deals"
_PARQUET = _DATA_DIR / "nse_bulk_deals.parquet"

# ── NSE URL templates ─────────────────────────────────────────────────────────
_URL_CURRENT = "https://archives.nseindia.com/content/equities/bulk.csv"
_URL_HISTORICAL = "https://archives.nseindia.com/content/equities/bulk_{date}.csv"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.nseindia.com/",
}

# ── NSE known trading holidays (weekdays only; weekends auto-filtered) ─────────
# Same list used in delivery_ingest.py — keep in sync.
_KNOWN_HOLIDAYS: frozenset[date] = frozenset({
    # 2015
    date(2015, 1, 26), date(2015, 2, 19), date(2015, 3, 6), date(2015, 4, 2),
    date(2015, 4, 3), date(2015, 4, 14), date(2015, 5, 1), date(2015, 7, 17),
    date(2015, 8, 28), date(2015, 9, 17), date(2015, 10, 2), date(2015, 10, 22),
    date(2015, 11, 11), date(2015, 11, 12), date(2015, 12, 25),
    # 2016
    date(2016, 1, 26), date(2016, 3, 7), date(2016, 3, 24), date(2016, 3, 25),
    date(2016, 4, 14), date(2016, 4, 15), date(2016, 4, 19), date(2016, 5, 6),
    date(2016, 7, 6), date(2016, 8, 15), date(2016, 9, 5), date(2016, 9, 13),
    date(2016, 10, 2), date(2016, 10, 11), date(2016, 10, 31), date(2016, 11, 14),
    date(2016, 12, 26),
    # 2017
    date(2017, 1, 26), date(2017, 2, 24), date(2017, 3, 13), date(2017, 4, 4),
    date(2017, 4, 14), date(2017, 5, 1), date(2017, 6, 26), date(2017, 8, 15),
    date(2017, 8, 25), date(2017, 9, 2), date(2017, 10, 2), date(2017, 10, 19),
    date(2017, 10, 20), date(2017, 12, 25),
    # 2018
    date(2018, 1, 26), date(2018, 2, 13), date(2018, 3, 2), date(2018, 3, 29),
    date(2018, 3, 30), date(2018, 4, 2), date(2018, 5, 1), date(2018, 8, 15),
    date(2018, 8, 22), date(2018, 9, 13), date(2018, 9, 20), date(2018, 10, 2),
    date(2018, 11, 7), date(2018, 11, 8), date(2018, 11, 21), date(2018, 12, 25),
    # 2019
    date(2019, 1, 26), date(2019, 3, 4), date(2019, 3, 21), date(2019, 4, 17),
    date(2019, 4, 19), date(2019, 4, 29), date(2019, 5, 18), date(2019, 6, 5),
    date(2019, 8, 12), date(2019, 8, 15), date(2019, 9, 2), date(2019, 9, 10),
    date(2019, 10, 2), date(2019, 10, 7), date(2019, 10, 8), date(2019, 10, 28),
    date(2019, 11, 12), date(2019, 12, 25),
    # 2020–2024: same as delivery_ingest.py
    date(2020, 2, 21), date(2020, 3, 10), date(2020, 4, 2), date(2020, 4, 6),
    date(2020, 4, 10), date(2020, 4, 14), date(2020, 5, 25), date(2020, 10, 2),
    date(2020, 11, 16), date(2020, 11, 30),
    date(2021, 1, 26), date(2021, 3, 11), date(2021, 3, 29), date(2021, 4, 2),
    date(2021, 4, 14), date(2021, 4, 21), date(2021, 5, 13), date(2021, 7, 21),
    date(2021, 8, 19), date(2021, 9, 10), date(2021, 10, 2), date(2021, 10, 15),
    date(2021, 11, 4), date(2021, 11, 5), date(2021, 11, 19),
    date(2022, 1, 26), date(2022, 3, 1), date(2022, 3, 18), date(2022, 4, 14),
    date(2022, 4, 15), date(2022, 5, 3), date(2022, 8, 9), date(2022, 8, 15),
    date(2022, 8, 31), date(2022, 10, 2), date(2022, 10, 5), date(2022, 10, 24),
    date(2022, 10, 26), date(2022, 11, 8),
    date(2023, 1, 26), date(2023, 3, 7), date(2023, 3, 30), date(2023, 4, 4),
    date(2023, 4, 7), date(2023, 4, 14), date(2023, 4, 22), date(2023, 5, 1),
    date(2023, 6, 28), date(2023, 8, 15), date(2023, 9, 19), date(2023, 10, 2),
    date(2023, 10, 24), date(2023, 11, 14), date(2023, 11, 27), date(2023, 12, 25),
    date(2024, 1, 22), date(2024, 1, 26), date(2024, 3, 8), date(2024, 3, 25),
    date(2024, 3, 29), date(2024, 4, 11), date(2024, 4, 14), date(2024, 4, 17),
    date(2024, 4, 21), date(2024, 5, 23), date(2024, 6, 17),
})


def _is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _KNOWN_HOLIDAYS


def _date_str(d: date) -> str:
    """Format date as DDMMYYYY for NSE URL."""
    return d.strftime("%d%m%Y")


# ── Raw column normalisation ──────────────────────────────────────────────────
# NSE bulk deal CSV has inconsistent column names across years.  Try multiple
# variants and map to our canonical schema.

_SYMBOL_VARIANTS = ["Symbol", "SYMBOL", "symbol"]
_DATE_VARIANTS = ["Date", "DATE", "date", "Trade Date"]
_CLIENT_VARIANTS = ["Client Name", "CLIENT NAME", "Client", "Acquiror/Seller Name"]
_SIDE_VARIANTS = ["Buy / Sell", "BUY/SELL", "Buy/Sell", "Transaction Type"]
_QTY_VARIANTS = ["Quantity Traded", "QUANTITY", "Qty", "Quantity"]
_PRICE_VARIANTS = [
    "Trade Price / Wght. Avg. Price",
    "Wght. Avg. Price",
    "PRICE",
    "Price",
    "Trade Price",
]


def _find_col(df: pd.DataFrame, variants: list[str]) -> str | None:
    """Return the first matching column name, or None."""
    for v in variants:
        if v in df.columns:
            return v
    # Case-insensitive fallback
    lower_map = {c.lower(): c for c in df.columns}
    for v in variants:
        if v.lower() in lower_map:
            return lower_map[v.lower()]
    return None


def _normalise_side(val: str) -> str:
    """Normalise buy/sell strings to 'BUY' or 'SELL'."""
    v = str(val).strip().upper()
    if v in ("B", "BUY", "BUY*"):
        return "BUY"
    if v in ("S", "SELL", "SELL*"):
        return "SELL"
    return v  # keep unknown values for inspection


def _parse_raw(csv_text: str, trading_date: date) -> pd.DataFrame | None:
    """Parse a raw NSE bulk deal CSV string into a clean DataFrame.

    Returns None if empty or unparseable.
    """
    try:
        df = pd.read_csv(io.StringIO(csv_text), sep=",", on_bad_lines="skip")
    except Exception as exc:
        try:
            df = pd.read_csv(io.StringIO(csv_text), sep=",",
                             error_bad_lines=False, warn_bad_lines=False)
        except Exception:
            logger.warning("CSV parse error for %s: %s", trading_date, exc)
            return None

    if df.empty or len(df.columns) < 4:
        logger.debug("Empty or malformed file for %s", trading_date)
        return None

    df.columns = [c.strip() for c in df.columns]

    # Map to canonical columns
    sym_col    = _find_col(df, _SYMBOL_VARIANTS)
    side_col   = _find_col(df, _SIDE_VARIANTS)
    qty_col    = _find_col(df, _QTY_VARIANTS)
    price_col  = _find_col(df, _PRICE_VARIANTS)
    client_col = _find_col(df, _CLIENT_VARIANTS)

    if not all([sym_col, side_col, qty_col, price_col]):
        logger.warning("Missing required columns in %s (got: %s)",
                       trading_date, list(df.columns))
        return None

    rows = pd.DataFrame()
    rows["symbol"]      = df[sym_col].astype(str).str.strip().str.upper()
    rows["side"]        = df[side_col].astype(str).apply(_normalise_side)
    rows["quantity"]    = pd.to_numeric(df[qty_col], errors="coerce")
    rows["price"]       = pd.to_numeric(df[price_col], errors="coerce")
    rows["client_name"] = df[client_col].astype(str).str.strip() if client_col else ""

    rows["business_date"] = trading_date
    rows["value_cr"] = rows["quantity"] * rows["price"] / 1e7
    rows["as_of_timestamp"] = pd.Timestamp(trading_date) + pd.Timedelta(hours=16)

    # Drop rows with invalid numeric values
    rows = rows.dropna(subset=["quantity", "price"])
    rows = rows[rows["quantity"] > 0]
    rows = rows[rows["price"] > 0]
    rows = rows[rows["symbol"].str.len() > 0]

    return rows[[
        "symbol", "business_date", "client_name", "side",
        "quantity", "price", "value_cr", "as_of_timestamp",
    ]].copy()


def fetch_day(
    trading_date: date,
    session: requests.Session,
    retry: int = 2,
    sleep_s: float = 1.5,
) -> pd.DataFrame | None:
    """Fetch and parse bulk deals for a single trading date.

    Returns a clean DataFrame or None on error/no data.
    """
    url = _URL_HISTORICAL.format(date=_date_str(trading_date))
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
            logger.debug("[%s] 404 — no bulk deals or non-trading day", trading_date)
            return None

        if resp.status_code != 200:
            logger.warning("[%s] HTTP %s (attempt %d)",
                           trading_date, resp.status_code, attempt + 1)
            if attempt < retry:
                time.sleep(sleep_s)
            continue

        return _parse_raw(resp.text, trading_date)

    return None


def _load_existing() -> pd.DataFrame:
    if _PARQUET.exists():
        df = pd.read_parquet(_PARQUET)
        df["business_date"] = pd.to_datetime(df["business_date"]).dt.date
        return df
    return pd.DataFrame(columns=[
        "symbol", "business_date", "client_name", "side",
        "quantity", "price", "value_cr", "as_of_timestamp",
    ])


def _write(df: pd.DataFrame) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    df["business_date"] = pd.to_datetime(df["business_date"]).dt.date
    df = df.drop_duplicates(
        subset=["symbol", "business_date", "client_name", "side"]
    ).reset_index(drop=True)
    df.sort_values(["business_date", "symbol"], inplace=True)
    df.to_parquet(_PARQUET, index=False)


def ingest(
    start: str | date,
    end: str | date | None = None,
    delay_s: float = 0.3,
) -> dict[str, int]:
    """Download and store NSE bulk deals for date range [start, end].

    Parameters
    ----------
    start : str or date
        First date to fetch (inclusive).
    end : str or date or None
        Last date to fetch (inclusive).  Defaults to yesterday.
    delay_s : float
        Polite delay between requests.

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

    logger.info("Fetching bulk deals for %d trading days: %s → %s",
                len(dates), start_d, end_d)

    stats = {"dates_attempted": len(dates), "dates_fetched": 0,
             "rows_written": 0, "errors": 0}

    frames: list[pd.DataFrame] = []
    session = requests.Session()
    session.headers.update(_HEADERS)

    for d in dates:
        df = fetch_day(d, session, retry=2, sleep_s=delay_s * 3)
        if df is not None and not df.empty:
            frames.append(df)
            stats["dates_fetched"] += 1
            stats["rows_written"] += len(df)
        else:
            # 404 on non-trading days is normal; treat as non-error
            stats["errors"] += 1

        time.sleep(delay_s)

    if frames:
        existing = _load_existing()
        combined = pd.concat([existing] + frames, ignore_index=True)
        _write(combined)
        logger.info("Wrote %d total rows to %s", len(combined), _PARQUET)

    return stats


def load_bulk_deals(
    symbol: str | None = None,
    start: str | date | None = None,
    end: str | date | None = None,
    side: str | None = "BUY",
    min_value_cr: float = 0.0,
) -> pd.DataFrame:
    """Load bulk deal data from the parquet store.

    Parameters
    ----------
    symbol : str or None
        Filter to single symbol.  None = all symbols.
    start, end : str or date or None
        Date range filter.
    side : str or None
        "BUY", "SELL", or None for both.  Default "BUY".
    min_value_cr : float
        Minimum deal value in crore.  Default 0 (no filter).

    Returns
    -------
    DataFrame sorted by (business_date, symbol).
    """
    if not _PARQUET.exists():
        return pd.DataFrame(columns=[
            "symbol", "business_date", "client_name", "side",
            "quantity", "price", "value_cr", "as_of_timestamp",
        ])

    df = pd.read_parquet(_PARQUET)
    df["business_date"] = pd.to_datetime(df["business_date"]).dt.date

    start_d = pd.Timestamp(start).date() if start else None
    end_d   = pd.Timestamp(end).date()   if end   else None

    if symbol:
        df = df[df["symbol"] == symbol.upper()]
    if side:
        df = df[df["side"] == side.upper()]
    if start_d:
        df = df[df["business_date"] >= start_d]
    if end_d:
        df = df[df["business_date"] <= end_d]
    if min_value_cr > 0:
        df = df[df["value_cr"] >= min_value_cr]

    return df.sort_values(["business_date", "symbol"]).reset_index(drop=True)


def validate(start: str | None = None, end: str | None = None) -> dict:
    """Print a summary of what's on disk and return a stats dict."""
    if not _PARQUET.exists():
        print("No bulk deal parquet found at", _PARQUET)
        return {"exists": False}

    df = pd.read_parquet(_PARQUET)
    df["business_date"] = pd.to_datetime(df["business_date"]).dt.date

    if start:
        df = df[df["business_date"] >= pd.Timestamp(start).date()]
    if end:
        df = df[df["business_date"] <= pd.Timestamp(end).date()]

    buy_df  = df[df["side"] == "BUY"]
    sell_df = df[df["side"] == "SELL"]

    first = df["business_date"].min() if not df.empty else None
    last  = df["business_date"].max() if not df.empty else None

    print("=" * 60)
    print("NSE Bulk Deal Data Summary")
    if start or end:
        print(f"  Filter: {start or '(all)'} → {end or '(all)'}")
    print("=" * 60)
    print(f"  File         : {_PARQUET}")
    print(f"  Date range   : {first} → {last}")
    print(f"  Trading days : {df['business_date'].nunique()}")
    print(f"  Symbols      : {df['symbol'].nunique()}")
    print(f"  Total rows   : {len(df):,}")
    print(f"  BUY rows     : {len(buy_df):,}")
    print(f"  SELL rows    : {len(sell_df):,}")
    if not buy_df.empty:
        print(f"  BUY value    : ₹{buy_df['value_cr'].sum():,.1f} Cr total")
        print(f"  Avg BUY value: ₹{buy_df['value_cr'].mean():.1f} Cr/deal")
    print("=" * 60)

    return {
        "exists": True,
        "first_date": first,
        "last_date": last,
        "trading_days": int(df["business_date"].nunique()),
        "symbols": int(df["symbol"].nunique()),
        "total_rows": len(df),
        "buy_rows": len(buy_df),
        "sell_rows": len(sell_df),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Download NSE bulk deal historical data.",
    )
    parser.add_argument("--start", default="2015-01-01",
                        help="Start date YYYY-MM-DD (default: 2015-01-01)")
    parser.add_argument("--end", default=None,
                        help="End date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Polite delay between HTTP requests in seconds (default: 0.3)")
    parser.add_argument("--validate", action="store_true",
                        help="Print summary of what's on disk and exit.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if args.validate:
        validate(start=args.start if args.start != "2015-01-01" else None,
                 end=args.end)
        return

    stats = ingest(start=args.start, end=args.end, delay_s=args.delay)
    print("\nIngest complete:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    _cli()
