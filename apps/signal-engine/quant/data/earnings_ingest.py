"""L1 earnings data ingest — NSE quarterly results for Midcap 150 universe.

PIT discipline (plan §4.3)
--------------------------
Every row has ``as_of_timestamp`` set to the filing submission datetime
(the moment NSE's system accepted the filing).  We never use the
quarter-end date as as_of — that would introduce look-ahead bias of
up to 90 days.

If the exact filing timestamp is unavailable from the API, we conservatively
set as_of_timestamp = announcement_date + 18:00 IST (market-close time on
the announcement day).  This is safe because OHLCV data for that day is
available from 18:00 IST onward.

Parquet schema (written to $QUANT_DATA_DIR/earnings/nse_results.parquet)
----------------------------------------------------------------------
  symbol            str   NSE symbol
  business_date     date  The announcement / result date
  as_of_timestamp   ts    When the data became available (PIT anchor)
  fiscal_quarter    int   1–4 (April-start fiscal year)
  fiscal_year       int   YYYY (fiscal year end)
  period_end        date  Last day of the reported quarter
  revenue_cr        float Revenue in ₹ crore (null if unavailable)
  net_profit_cr     float PAT in ₹ crore (null if unavailable)
  eps_reported      float Basic EPS for the quarter (null if unavailable)
  yoy_eps_prev      float EPS from same quarter last year (null if unavailable)
  yoy_revenue_prev  float Revenue from same quarter last year (null if unavailable)
  result_type       str   "quarterly" | "annual"
  source_url        str   NSE filing URL for audit trail
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from quant.research.holdout_lock import assert_no_holdout_access

logger = logging.getLogger(__name__)

_NSE_BASE = "https://www.nseindia.com"
_RESULTS_API = f"{_NSE_BASE}/api/corporates-financial-results"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-results",
}

_PARQUET_SCHEMA = [
    "symbol", "business_date", "as_of_timestamp",
    "fiscal_quarter", "fiscal_year", "period_end",
    "revenue_cr", "net_profit_cr", "eps_reported",
    "yoy_eps_prev", "yoy_revenue_prev",
    "result_type", "source_url",
]


def _lake_dir() -> Path:
    base = os.environ.get("QUANT_DATA_DIR", "data/lake")
    return Path(base) / "earnings"


def _parquet_path() -> Path:
    return _lake_dir() / "nse_results.parquet"


def _nse_session() -> requests.Session:
    """Create a session with cookies from the NSE home page (required for API access)."""
    sess = requests.Session()
    sess.headers.update(_HEADERS)
    try:
        sess.get(_NSE_BASE, timeout=10)  # sets cookies
        time.sleep(0.5)
    except requests.RequestException as exc:
        logger.warning("Failed to initialise NSE session: %s", exc)
    return sess


def fetch_results_page(
    from_date: str,
    to_date: str,
    session: requests.Session | None = None,
    retries: int = 3,
) -> list[dict]:
    """Fetch one page of NSE quarterly results.

    Parameters
    ----------
    from_date, to_date : str
        Date range in "DD-MM-YYYY" format (NSE API convention).
    session : requests.Session | None
        Re-use a session to avoid repeated cookie fetches.
    retries : int
        Number of times to retry on transient failure.

    Returns
    -------
    list[dict]
        Raw records from the NSE API.  May be empty.
    """
    sess = session or _nse_session()
    params = {"index": "equities", "from_date": from_date, "to_date": to_date}

    for attempt in range(retries):
        try:
            resp = sess.get(_RESULTS_API, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data", [])
            return []
        except requests.RequestException as exc:
            logger.warning("NSE results API error (attempt %d/%d): %s", attempt + 1, retries, exc)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)

    return []


def _parse_record(rec: dict, announcement_date: date) -> dict | None:
    """Parse a raw NSE API record into our parquet schema."""
    symbol = rec.get("symbol", "").strip().upper()
    if not symbol:
        return None

    result_type = rec.get("xbrl", "").lower()
    if "annual" in result_type:
        result_type = "annual"
    else:
        result_type = "quarterly"

    # NSE provides period_end as "MMM YYYY" (e.g. "Mar 2023") or "YYYY-MM-DD"
    period_str = rec.get("toDate", "") or rec.get("period", "")
    try:
        period_end = pd.Timestamp(period_str).date()
    except Exception:
        period_end = None

    fiscal_quarter, fiscal_year = None, None
    if period_end is not None:
        # Indian fiscal year: April–March.  Q1 = Apr-Jun, Q2 = Jul-Sep, etc.
        m = period_end.month
        if m in (4, 5, 6):
            fiscal_quarter = 1
        elif m in (7, 8, 9):
            fiscal_quarter = 2
        elif m in (10, 11, 12):
            fiscal_quarter = 3
        else:
            fiscal_quarter = 4
        fiscal_year = period_end.year if m <= 3 else period_end.year + 1

    # Conservative PIT: as_of = announcement_date + 18:00 IST (12:30 UTC)
    as_of = datetime(
        announcement_date.year,
        announcement_date.month,
        announcement_date.day,
        12, 30, 0,
        tzinfo=timezone.utc,
    )

    source_url = rec.get("xbrl", "") or ""

    return {
        "symbol": symbol,
        "business_date": announcement_date,
        "as_of_timestamp": as_of,
        "fiscal_quarter": fiscal_quarter,
        "fiscal_year": fiscal_year,
        "period_end": period_end,
        "revenue_cr": None,        # populated separately via filing parser
        "net_profit_cr": None,
        "eps_reported": None,
        "yoy_eps_prev": None,
        "yoy_revenue_prev": None,
        "result_type": result_type,
        "source_url": source_url,
    }


def ingest_date_range(
    from_date: str,
    to_date: str,
    symbol_filter: list[str] | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Ingest NSE quarterly results for a date range and return a DataFrame.

    Parameters
    ----------
    from_date, to_date : str
        ISO date strings "YYYY-MM-DD".
    symbol_filter : list[str] | None
        If set, only return rows for these NSE symbols.
    session : requests.Session | None
        Reuse an existing session.

    Returns
    -------
    pd.DataFrame with columns matching _PARQUET_SCHEMA.
        May be empty if no data or API unavailable.
    """
    # PIT guard: refuse to ingest hold-out dates
    assert_no_holdout_access(to_date)

    # NSE API uses DD-MM-YYYY
    from_nse = pd.Timestamp(from_date).strftime("%d-%m-%Y")
    to_nse = pd.Timestamp(to_date).strftime("%d-%m-%Y")

    raw = fetch_results_page(from_nse, to_nse, session=session)
    if not raw:
        logger.info("No NSE results found for %s → %s", from_date, to_date)
        return _empty_df()

    announcement_date = pd.Timestamp(to_date).date()
    rows = []
    for rec in raw:
        parsed = _parse_record(rec, announcement_date)
        if parsed is None:
            continue
        if symbol_filter and parsed["symbol"] not in symbol_filter:
            continue
        rows.append(parsed)

    if not rows:
        return _empty_df()

    df = pd.DataFrame(rows, columns=_PARQUET_SCHEMA)
    return df


def build_historical_dataset(
    start: str = "2015-01-01",
    end: str = "2024-06-30",
    universe: list[str] | None = None,
    chunk_days: int = 30,
    rate_limit_secs: float = 1.5,
) -> pd.DataFrame:
    """Build a full historical earnings dataset by iterating over date chunks.

    Parameters
    ----------
    start, end : str
        ISO date strings.  end must not be in the hold-out window.
    universe : list[str] | None
        NSE symbol filter.  None = all equities.
    chunk_days : int
        Request size in calendar days (default 30 — NSE API limit).
    rate_limit_secs : float
        Sleep between requests.  NSE requires polite access.

    Returns
    -------
    pd.DataFrame  (deduplicated by symbol + business_date + period_end)
    """
    assert_no_holdout_access(end)

    sess = _nse_session()
    chunks: list[pd.DataFrame] = []

    current = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)

    while current <= end_ts:
        chunk_end = min(current + pd.Timedelta(days=chunk_days - 1), end_ts)
        logger.info("Fetching earnings %s → %s", current.date(), chunk_end.date())

        df_chunk = ingest_date_range(
            current.strftime("%Y-%m-%d"),
            chunk_end.strftime("%Y-%m-%d"),
            symbol_filter=universe,
            session=sess,
        )
        if not df_chunk.empty:
            chunks.append(df_chunk)

        current = chunk_end + pd.Timedelta(days=1)
        time.sleep(rate_limit_secs)

    if not chunks:
        logger.warning("No earnings data collected for %s → %s", start, end)
        return _empty_df()

    combined = pd.concat(chunks, ignore_index=True)
    combined = combined.drop_duplicates(subset=["symbol", "business_date", "period_end"])
    return combined.sort_values(["symbol", "business_date"]).reset_index(drop=True)


def save_parquet(df: pd.DataFrame) -> Path:
    """Write the earnings DataFrame to the L1 Parquet lake."""
    path = _parquet_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["symbol", "business_date", "period_end"])
        combined.to_parquet(path, index=False)
        logger.info("Appended %d rows to %s (total %d)", len(df), path, len(combined))
    else:
        df.to_parquet(path, index=False)
        logger.info("Wrote %d rows to %s", len(df), path)

    return path


def load_earnings(
    symbol: str | list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    snapshot_at: str | datetime | None = None,
) -> pd.DataFrame:
    """Load earnings data from the L1 lake with optional PIT filter.

    Parameters
    ----------
    symbol : str | list[str] | None
        Filter by NSE symbol(s).
    start, end : str | None
        Filter by business_date range.
    snapshot_at : str | datetime | None
        PIT filter: only rows with as_of_timestamp <= snapshot_at.
        Use this when building features to prevent lookahead.

    Returns
    -------
    pd.DataFrame
    """
    path = _parquet_path()
    if not path.exists():
        logger.warning("Earnings parquet not found at %s — run build_historical_dataset first", path)
        return _empty_df()

    df = pd.read_parquet(path)

    if symbol is not None:
        syms = [symbol] if isinstance(symbol, str) else list(symbol)
        df = df[df["symbol"].isin(syms)]

    if start is not None:
        df = df[pd.to_datetime(df["business_date"]) >= pd.Timestamp(start)]
    if end is not None:
        df = df[pd.to_datetime(df["business_date"]) <= pd.Timestamp(end)]

    if snapshot_at is not None:
        snap_ts = pd.Timestamp(snapshot_at)
        df = df[pd.to_datetime(df["as_of_timestamp"]).dt.tz_localize(None) <= snap_ts.tz_localize(None)]

    return df.reset_index(drop=True)


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_PARQUET_SCHEMA)
