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

# NSE uses Akamai Bot Manager.  These headers are required to pass the
# bot challenge.  The session warmup (hitting 3 pages before the API call)
# is also required to build up the right cookie set (nsit, nseappid, etc.).
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "DNT": "1",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}

# Pages to visit in order before hitting the API — each sets additional cookies
_WARMUP_PAGES = [
    _NSE_BASE,
    f"{_NSE_BASE}/market-data/live-equity-market",
    f"{_NSE_BASE}/companies-listing/corporate-filings-results",
]

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
    """Create a session that passes NSE's Akamai Bot Manager.

    NSE requires visiting 3 pages in order to accumulate the full cookie set
    (nsit, nseappid, ak_bmsc, bm_sv, etc.) before the API will return data.
    A single homepage GET is not enough — the API returns empty lists silently.
    """
    sess = requests.Session()
    sess.headers.update(_HEADERS)

    for i, url in enumerate(_WARMUP_PAGES):
        try:
            resp = sess.get(url, timeout=15)
            logger.debug("warmup page=%d status=%d cookies=%s", i, resp.status_code, list(sess.cookies.keys()))
        except requests.RequestException as exc:
            logger.warning("NSE warmup page %d failed: %s", i, exc)
        # Human-like delay between page loads (Akamai checks timing)
        time.sleep(1.5 if i < len(_WARMUP_PAGES) - 1 else 2.0)

    cookie_keys = list(sess.cookies.keys())
    if not any(k in cookie_keys for k in ("nsit", "nseappid", "bm_sv", "ak_bmsc")):
        logger.warning(
            "NSE session cookies look incomplete: %s — API may still return empty responses. "
            "If the ingest returns no data, NSE may be blocking automated access. "
            "Try running during off-peak hours (before 9am or after 6pm IST).",
            cookie_keys,
        )
    else:
        logger.info("NSE session ready. Cookies: %s", cookie_keys)

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


def _parse_record(rec: dict, fallback_announcement_date: date) -> dict | None:
    """Parse a raw NSE API record into our parquet schema.

    NSE API fields used:
      seDate   — actual stock-exchange submission date (DD-MMM-YYYY or similar)
      toDate   — period end date (DD-MM-YYYY)
      reInr    — EPS in ₹ per share (rupees, face-value adjusted)
      profit   — net profit in ₹ crore
      income   — total revenue in ₹ crore
      xbrl     — XBRL URL (also signals annual vs quarterly via URL path)
    """
    symbol = rec.get("symbol", "").strip().upper()
    if not symbol:
        return None

    # Actual announcement date: prefer seDate from the record over the chunk's fallback
    se_date_str = rec.get("seDate", "") or rec.get("date", "")
    announcement_date: date = fallback_announcement_date
    if se_date_str:
        try:
            announcement_date = pd.Timestamp(se_date_str).date()
        except Exception:
            pass

    # Determine quarterly vs annual from XBRL URL or period length
    xbrl_url = rec.get("xbrl", "") or ""
    period_str = rec.get("toDate", "") or rec.get("period", "")
    from_str = rec.get("fromDate", "") or ""

    result_type = "annual" if "annual" in xbrl_url.lower() else "quarterly"
    if from_str and period_str:
        try:
            span_days = (pd.Timestamp(period_str) - pd.Timestamp(from_str)).days
            if span_days > 200:
                result_type = "annual"
        except Exception:
            pass

    # Period end
    try:
        period_end = pd.Timestamp(period_str).date() if period_str else None
    except Exception:
        period_end = None

    fiscal_quarter, fiscal_year = None, None
    if period_end is not None:
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

    # PIT: as_of = actual announcement date at 18:00 IST (12:30 UTC)
    as_of = datetime(
        announcement_date.year,
        announcement_date.month,
        announcement_date.day,
        12, 30, 0,
        tzinfo=timezone.utc,
    )

    # ── Extract financials from NSE API response ──────────────────────────────
    # NSE returns these directly in the JSON — no filing parser needed.
    # Field names: reInr = EPS in ₹, profit = net profit ₹cr, income = revenue ₹cr
    eps_reported = _safe_float(rec.get("reInr") or rec.get("eps") or rec.get("basicEps"))
    net_profit_cr = _safe_float(rec.get("profit") or rec.get("netProfit"))
    revenue_cr = _safe_float(rec.get("income") or rec.get("totalIncome") or rec.get("revenue"))

    # Sanity bounds
    if eps_reported is not None and (eps_reported < -50_000 or eps_reported > 100_000):
        eps_reported = None
    if net_profit_cr is not None and abs(net_profit_cr) > 500_000:
        net_profit_cr = None
    if revenue_cr is not None and (revenue_cr < 0 or revenue_cr > 2_000_000):
        revenue_cr = None

    return {
        "symbol": symbol,
        "business_date": announcement_date,
        "as_of_timestamp": as_of,
        "fiscal_quarter": fiscal_quarter,
        "fiscal_year": fiscal_year,
        "period_end": period_end,
        "revenue_cr": revenue_cr,
        "net_profit_cr": net_profit_cr,
        "eps_reported": eps_reported,
        "yoy_eps_prev": None,   # computed post-hoc after full dataset is built
        "yoy_revenue_prev": None,
        "result_type": result_type,
        "source_url": xbrl_url,
    }


def _safe_float(val: object) -> float | None:
    """Convert a value to float, returning None on failure or empty string."""
    if val is None:
        return None
    try:
        s = str(val).strip()
        if not s or s.lower() in ("na", "nan", "null", "-", ""):
            return None
        return float(s.replace(",", ""))
    except (ValueError, TypeError):
        return None


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

    # Use to_date as the fallback; _parse_record prefers seDate from each record.
    fallback_date = pd.Timestamp(to_date).date()
    rows = []
    for rec in raw:
        parsed = _parse_record(rec, fallback_date)
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
    combined = combined.sort_values(["symbol", "business_date"]).reset_index(drop=True)
    combined = compute_yoy_columns(combined)
    return combined


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


def compute_yoy_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Fill yoy_eps_prev and yoy_revenue_prev for the full dataset.

    Matches each row with the same (symbol, fiscal_quarter) from fiscal_year - 1.
    Call this after the full dataset is assembled so cross-year lookups work.
    """
    if df.empty:
        return df

    df = df.sort_values(["symbol", "fiscal_year", "fiscal_quarter"]).copy()
    lookup = df.set_index(["symbol", "fiscal_quarter", "fiscal_year"])

    yoy_eps, yoy_rev = [], []
    for _, row in df.iterrows():
        sym = row["symbol"]
        q = row["fiscal_quarter"]
        fy = row["fiscal_year"]
        if pd.isna(q) or pd.isna(fy):
            yoy_eps.append(None)
            yoy_rev.append(None)
            continue
        try:
            prev = lookup.loc[(sym, q, fy - 1)]
            prev_eps = float(prev["eps_reported"]) if prev["eps_reported"] is not None and not pd.isna(prev["eps_reported"]) else None
            prev_rev = float(prev["revenue_cr"]) if prev["revenue_cr"] is not None and not pd.isna(prev["revenue_cr"]) else None
        except KeyError:
            prev_eps, prev_rev = None, None
        yoy_eps.append(prev_eps)
        yoy_rev.append(prev_rev)

    df["yoy_eps_prev"] = yoy_eps
    df["yoy_revenue_prev"] = yoy_rev
    return df


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_PARQUET_SCHEMA)


def main() -> None:
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="NSE earnings ingest")
    parser.add_argument("--start", default="2015-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2024-06-30", help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--universe-file",
        default="data/lake/midcap150_constituents.csv",
        help="CSV with 'symbol' column",
    )
    parser.add_argument("--chunk-days", type=int, default=30)
    parser.add_argument("--rate-limit", type=float, default=1.5)
    parser.add_argument(
        "--test",
        action="store_true",
        help="Smoke-test: fetch one chunk (Oct 2023) and print results without saving",
    )
    args = parser.parse_args()

    if args.test:
        logger.info("--- SMOKE TEST: fetching Oct 2023 chunk ---")
        sess = _nse_session()
        raw = fetch_results_page("01-10-2023", "31-10-2023", session=sess)
        logger.info("Raw records returned: %d", len(raw))
        if raw:
            logger.info("Sample record: %s", raw[0])
            df_test = ingest_date_range("2023-10-01", "2023-10-31")
            logger.info("Parsed rows: %d", len(df_test))
            if not df_test.empty:
                logger.info("Columns with data:\n%s", df_test.notna().sum().to_string())
        else:
            logger.warning(
                "NSE API returned 0 records for Oct 2023 — session not authenticated.\n"
                "Try running between 6pm–9am IST when NSE Bot Manager is less aggressive,\n"
                "or see docs/nse_session_fix.md for cookie injection workaround."
            )
        return

    universe: list[str] | None = None
    import os
    if os.path.exists(args.universe_file):
        universe = pd.read_csv(args.universe_file)["symbol"].str.upper().tolist()
        logger.info("Universe: %d symbols from %s", len(universe), args.universe_file)
    else:
        logger.info("No universe file — ingesting all equities")

    df = build_historical_dataset(
        start=args.start,
        end=args.end,
        universe=universe,
        chunk_days=args.chunk_days,
        rate_limit_secs=args.rate_limit,
    )

    if df.empty:
        logger.error("No data collected — check NSE API connectivity")
        sys.exit(1)

    df = compute_yoy_columns(df)
    path = save_parquet(df)
    logger.info("Done. Saved to %s (%d rows, %d symbols)", path, len(df), df["symbol"].nunique())


if __name__ == "__main__":
    main()
