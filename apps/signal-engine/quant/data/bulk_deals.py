"""NSE Bulk Deal ingest — data pipeline for Strategy F (BDM).

NSE mandates that any entity buying or selling ≥ 0.5% of a listed company's
equity outstanding in a single exchange session must report the transaction to
NSE by 16:00 that day.  NSE discloses the data publicly the same evening.

Data acquisition — two paths
-----------------------------

**Daily update (programmatic, works automatically):**
  Today's bulk deal file is freely available (no bot protection):
    https://archives.nseindia.com/content/equities/bulk.csv
  Run after 16:30 IST each trading day:
    python -m quant.data.bulk_deals --today

**Historical backfill (one-time, manual browser download):**
  NSE's historical bulk deal API (www.nseindia.com/api/historical/bulk-deals)
  and archive files (archives.nseindia.com/content/historical/equities/bulk/)
  are both blocked for non-browser access (Akamai bot protection / IP restriction).

  To backfill 2015-2024:
  1. Open Chrome → https://www.nseindia.com/market-data/bulk-deals
  2. Set "From" and "To" dates (NSE allows up to 3 months per download)
  3. Click the download icon (Excel/CSV)
  4. Save to a single directory, e.g. ~/Downloads/nse_bulk_deals/
  5. Repeat in ~quarterly chunks until 2015-01-01 → 2024-06-30 is covered
     (~38 downloads × ~10 seconds each ≈ 6–8 minutes total)
  6. Import all at once:
     python -m quant.data.bulk_deals --ingest-dir ~/Downloads/nse_bulk_deals/

  The downloaded files are named like "Bulk-Deal.csv" or "Bulk-Deal (1).csv";
  the ingest-dir mode reads all .csv/.xls/.xlsx files in the directory.

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
  # Import manually downloaded NSE bulk deal CSV/Excel files:
  python -m quant.data.bulk_deals --ingest-dir ~/Downloads/nse_bulk_deals/

  # Fetch today's file (run after 16:30 IST):
  python -m quant.data.bulk_deals --today

  # Validate what's on disk:
  python -m quant.data.bulk_deals --validate
"""

from __future__ import annotations

import argparse
import io
import logging
from datetime import date
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR  = _REPO_ROOT / "data" / "lake" / "bulk_deals"
_PARQUET   = _DATA_DIR / "nse_bulk_deals.parquet"

# ── Today's bulk deal file (no bot protection, freely accessible) ──────────────
_TODAY_URL = "https://archives.nseindia.com/content/equities/bulk.csv"

# ── NSE downloaded CSV column names (exact headers from NSE website export) ───
# NSE exports have these exact column names; we also handle minor variants.
_COL_DATE     = "Date"
_COL_SYMBOL   = "Symbol"
_COL_CLIENT   = "Client Name"
_COL_SIDE     = "Buy / Sell"
_COL_QTY      = "Quantity Traded"
_COL_PRICE    = "Trade Price / Wght. Avg. Price"

# Alternate column names seen in different NSE export formats
_COL_ALIASES: dict[str, list[str]] = {
    "date":   [_COL_DATE, "date", "DATE", "Trade Date"],
    "symbol": [_COL_SYMBOL, "symbol", "SYMBOL", "Ticker"],
    "client": [_COL_CLIENT, "client_name", "CLIENT NAME", "Client"],
    "side":   [_COL_SIDE, "Buy/Sell", "BUY_SELL", "Type"],
    "qty":    [_COL_QTY, "quantity", "QUANTITY", "Qty", "No. of Shares"],
    "price":  [_COL_PRICE, "price", "PRICE", "Trade Price", "Avg Price"],
}


def _normalise_side(val) -> str:
    v = str(val).strip().upper()
    if v in ("B", "BUY", "BUY*"):
        return "BUY"
    if v in ("S", "SELL", "SELL*"):
        return "SELL"
    return v


def _parse_nse_date(val) -> date | None:
    """Parse NSE date strings: '01-Jan-2023', '2023-01-01', '01/01/2023'."""
    val = str(val).strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%b %d, %Y"):
        try:
            return pd.to_datetime(val, format=fmt).date()
        except (ValueError, TypeError):
            continue
    try:
        return pd.to_datetime(val).date()
    except Exception:
        return None


def _find_col(df: pd.DataFrame, key: str) -> str | None:
    """Return the first column name from _COL_ALIASES[key] that exists in df."""
    for alias in _COL_ALIASES.get(key, []):
        if alias in df.columns:
            return alias
    # Case-insensitive fallback
    lower_map = {c.lower(): c for c in df.columns}
    for alias in _COL_ALIASES.get(key, []):
        if alias.lower() in lower_map:
            return lower_map[alias.lower()]
    return None


def _parse_nse_df(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalise a raw NSE bulk-deal DataFrame into our schema.

    Handles both the direct CSV download format and the NSE website export.
    Returns empty DataFrame if required columns are missing.
    """
    if raw.empty:
        return pd.DataFrame()

    col_date   = _find_col(raw, "date")
    col_symbol = _find_col(raw, "symbol")
    col_client = _find_col(raw, "client")
    col_side   = _find_col(raw, "side")
    col_qty    = _find_col(raw, "qty")
    col_price  = _find_col(raw, "price")

    missing = [k for k, v in [
        ("date", col_date), ("symbol", col_symbol),
        ("side", col_side), ("qty", col_qty), ("price", col_price),
    ] if v is None]
    if missing:
        logger.warning("Missing required columns: %s.  Available: %s",
                       missing, list(raw.columns))
        return pd.DataFrame()

    rows = []
    for _, row in raw.iterrows():
        sym    = str(row[col_symbol]).strip().upper()
        dt_raw = row[col_date]
        client = str(row[col_client]).strip() if col_client and pd.notna(row[col_client]) else ""
        side   = _normalise_side(row[col_side])
        qty    = row[col_qty]
        price  = row[col_price]

        if not sym or sym == "NAN":
            continue

        business_date = _parse_nse_date(dt_raw)
        if business_date is None:
            continue

        try:
            qty_int   = int(str(qty).replace(",", "").split(".")[0])
            price_flt = float(str(price).replace(",", ""))
        except (ValueError, TypeError):
            continue

        if qty_int <= 0 or price_flt <= 0:
            continue

        rows.append({
            "symbol":          sym,
            "business_date":   business_date,
            "client_name":     client,
            "side":            side,
            "quantity":        qty_int,
            "price":           price_flt,
            "value_cr":        qty_int * price_flt / 1e7,
            "as_of_timestamp": pd.Timestamp(business_date) + pd.Timedelta(hours=16),
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df[df["symbol"].str.len() > 0]
    return df


def _read_file(path: Path) -> pd.DataFrame:
    """Read a single NSE bulk-deal file (CSV or Excel) and return normalised df."""
    suffix = path.suffix.lower()
    try:
        if suffix in (".xls", ".xlsx"):
            raw = pd.read_excel(path, dtype=str)
        else:
            # Try comma-separated first; NSE sometimes uses tab
            try:
                raw = pd.read_csv(path, dtype=str, on_bad_lines="skip")
            except TypeError:
                # pandas < 1.3 fallback
                raw = pd.read_csv(path, dtype=str,
                                  error_bad_lines=False, warn_bad_lines=False)  # type: ignore[call-arg]
    except Exception as exc:
        logger.warning("Could not read %s: %s", path.name, exc)
        return pd.DataFrame()

    # Drop blank/all-NaN rows that NSE sometimes includes as footers
    raw = raw.dropna(how="all").reset_index(drop=True)

    df = _parse_nse_df(raw)
    if not df.empty:
        logger.info("  %s → %d records", path.name, len(df))
    else:
        logger.warning("  %s → 0 records (columns not recognised)", path.name)
    return df


def fetch_today() -> dict[str, int]:
    """Download today's NSE bulk deal file and merge into the parquet store.

    URL: https://archives.nseindia.com/content/equities/bulk.csv
    This file is freely accessible (no bot protection) and updated by ~16:30 IST.

    Returns
    -------
    dict with keys: rows_fetched, rows_written.
    """
    logger.info("Fetching today's bulk deal file from %s", _TODAY_URL)
    try:
        resp = requests.get(_TODAY_URL, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to fetch today's bulk deal file: %s", exc)
        return {"rows_fetched": 0, "rows_written": 0}

    try:
        raw = pd.read_csv(io.StringIO(resp.text), dtype=str, on_bad_lines="skip")
    except TypeError:
        raw = pd.read_csv(io.StringIO(resp.text), dtype=str,
                          error_bad_lines=False, warn_bad_lines=False)  # type: ignore[call-arg]

    raw = raw.dropna(how="all").reset_index(drop=True)
    df = _parse_nse_df(raw)

    if df.empty:
        logger.warning("Today's bulk deal file parsed to 0 rows.")
        return {"rows_fetched": 0, "rows_written": 0}

    rows_fetched = len(df)
    existing = _load_existing()
    combined = _merge(existing, df)
    _write(combined)
    rows_written = len(df)
    logger.info("Fetched %d rows; parquet now has %d total rows",
                rows_fetched, len(combined))
    return {"rows_fetched": rows_fetched, "rows_written": rows_written}


def ingest_dir(directory: str | Path) -> dict[str, int]:
    """Read all bulk-deal CSV/Excel files from a local directory and merge into parquet.

    Designed for historical backfill from manually downloaded NSE website exports.
    NSE website: https://www.nseindia.com/market-data/bulk-deals
    (download in quarterly chunks, save all files to one directory)

    Parameters
    ----------
    directory : str or Path
        Local directory containing downloaded NSE bulk deal files.
        All *.csv, *.xls, *.xlsx files are processed.

    Returns
    -------
    dict with keys: files_found, files_parsed, rows_ingested.
    """
    dir_path = Path(directory).expanduser().resolve()
    if not dir_path.is_dir():
        logger.error("Directory not found: %s", dir_path)
        return {"files_found": 0, "files_parsed": 0, "rows_ingested": 0}

    files = sorted([
        f for f in dir_path.iterdir()
        if f.suffix.lower() in (".csv", ".xls", ".xlsx") and f.is_file()
    ])

    if not files:
        logger.warning("No CSV/Excel files found in %s", dir_path)
        return {"files_found": 0, "files_parsed": 0, "rows_ingested": 0}

    logger.info("Found %d files in %s", len(files), dir_path)
    stats = {"files_found": len(files), "files_parsed": 0, "rows_ingested": 0}
    frames: list[pd.DataFrame] = []

    for f in files:
        df = _read_file(f)
        if not df.empty:
            frames.append(df)
            stats["files_parsed"] += 1
            stats["rows_ingested"] += len(df)

    if not frames:
        logger.warning("No valid records found in any file.")
        return stats

    new_data = pd.concat(frames, ignore_index=True)
    existing = _load_existing()
    combined = _merge(existing, new_data)
    _write(combined)

    logger.info(
        "Ingested %d rows from %d/%d files; parquet total: %d rows",
        stats["rows_ingested"], stats["files_parsed"], stats["files_found"],
        len(combined),
    )
    return stats


def _load_existing() -> pd.DataFrame:
    if _PARQUET.exists():
        df = pd.read_parquet(_PARQUET)
        df["business_date"] = pd.to_datetime(df["business_date"]).dt.date
        return df
    return pd.DataFrame(columns=[
        "symbol", "business_date", "client_name", "side",
        "quantity", "price", "value_cr", "as_of_timestamp",
    ])


def _merge(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Concatenate existing + new data, dedup, sort."""
    frames = [f for f in (existing, new) if not f.empty]
    if not frames:
        return existing
    return pd.concat(frames, ignore_index=True)


def _write(df: pd.DataFrame) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    df["business_date"] = pd.to_datetime(df["business_date"]).dt.date
    df = df.drop_duplicates(
        subset=["symbol", "business_date", "client_name", "side"]
    ).reset_index(drop=True)
    df.sort_values(["business_date", "symbol"], inplace=True)
    df.to_parquet(_PARQUET, index=False)


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
    start, end : str or date or None
    side : str or None
        "BUY", "SELL", or None for both.  Default "BUY".
    min_value_cr : float
        Minimum deal value in crore.

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
    """Print a summary of what's on disk."""
    if not _PARQUET.exists():
        print("No bulk deal parquet found at", _PARQUET)
        return {"exists": False}

    df = pd.read_parquet(_PARQUET)
    df["business_date"] = pd.to_datetime(df["business_date"]).dt.date

    if start:
        df = df[df["business_date"] >= pd.Timestamp(start).date()]
    if end:
        df = df[df["business_date"] <= pd.Timestamp(end).date()]

    buy_df = df[df["side"] == "BUY"]
    first  = df["business_date"].min() if not df.empty else None
    last   = df["business_date"].max() if not df.empty else None

    print("=" * 60)
    print("NSE Bulk Deal Data Summary")
    print("=" * 60)
    print(f"  File         : {_PARQUET}")
    print(f"  Date range   : {first} → {last}")
    print(f"  Trading days : {df['business_date'].nunique()}")
    print(f"  Symbols      : {df['symbol'].nunique()}")
    print(f"  Total rows   : {len(df):,}")
    print(f"  BUY rows     : {len(buy_df):,}")
    print(f"  SELL rows    : {len(df) - len(buy_df):,}")
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
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "NSE Bulk Deal data pipeline for Strategy F (BDM).\n\n"
            "Two modes:\n"
            "  --today        Download today's bulk.csv (run after 16:30 IST)\n"
            "  --ingest-dir   Import manually downloaded CSV/Excel files\n"
            "  --validate     Print summary of what's on disk"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--today", action="store_true",
        help="Fetch today's bulk deal file from NSE archives.",
    )
    parser.add_argument(
        "--ingest-dir", metavar="DIR",
        help=(
            "Directory containing manually downloaded NSE bulk deal files "
            "(CSV or Excel). All files are merged into the parquet store."
        ),
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Print summary of what's on disk and exit.",
    )
    parser.add_argument(
        "--start", default=None,
        help="(--validate only) Filter from date YYYY-MM-DD.",
    )
    parser.add_argument(
        "--end", default=None,
        help="(--validate only) Filter to date YYYY-MM-DD.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if args.validate:
        validate(start=args.start, end=args.end)
        return

    if args.today:
        stats = fetch_today()
        print("\nToday's fetch complete:")
        for k, v in stats.items():
            print(f"  {k}: {v}")
        return

    if args.ingest_dir:
        stats = ingest_dir(args.ingest_dir)
        print("\nDirectory ingest complete:")
        for k, v in stats.items():
            print(f"  {k}: {v}")
        return

    parser.print_help()
    print(
        "\nTip: to backfill historical data, manually download from:\n"
        "  https://www.nseindia.com/market-data/bulk-deals\n"
        "Then run:\n"
        "  python -m quant.data.bulk_deals --ingest-dir ~/Downloads/nse_bulk_deals/"
    )


if __name__ == "__main__":
    _cli()
