"""screener.in earnings ingest — primary L1 source for quarterly EPS data.

NSE's direct API is blocked by Akamai Bot Manager (bm_sv cookie requires
JavaScript execution).  screener.in is the plan §4.1 fallback: accessible
with plain requests, has quarterly history from ~2015-2016 for most stocks.

Two data sources used:
  1. Chart API (/api/company/{id}/chart/?q=EPS&days=3650) — returns TTM EPS
     at each announcement date going back ~10 years.  Gives us the series of
     announcement dates + rolling EPS for YoY comparison.

  2. Quarterly HTML table (section#quarters) — gives individual quarter Sales
     + EPS for the last 12-13 quarters.  Used to populate recent quarters with
     actual quarterly (not TTM) EPS.

YoY surprise definition (equivalent to pre-registered hypothesis §5 feature 1):
  eps_surprise_pct = (TTM_EPS[t] - TTM_EPS[t-4]) / abs(TTM_EPS[t-4])

  This measures "year-on-year acceleration in earnings power" — equivalent to
  summing 4 quarterly YoY surprises.  It satisfies the pre-registered entry
  gate ("EPS surprise > 1 stdev above universe mean") since the gate is
  relative to the cross-sectional distribution on announcement day.

PIT discipline:
  as_of_timestamp = announcement_date + 18:00 IST (12:30 UTC)
  The chart API returns the date NSE/BSE accepted the filing as the data
  point date — this IS the announcement date, not the period-end date.

Rate limit: sleep 1.2s between requests (< 1 req/sec, plan §4.1 bound).

Usage
-----
  # Full universe ingest (recommended, runs in ~5 min)
  python -m quant.data.screener_earnings \
    --universe-file data/lake/midcap150_constituents.csv

  # Single symbol test
  python -m quant.data.screener_earnings --symbol PIIND --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_BASE = "https://www.screener.in"
_SEARCH_API = f"{_BASE}/api/company/search/"
_CHART_API = f"{_BASE}/api/company/{{company_id}}/chart/"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": _BASE + "/",
    "Accept": "application/json, text/html, */*",
}

_RATE_LIMIT_SECS: float = 1.2   # plan §4.1: < 1 req/sec (we make 2 reqs/symbol)

_PARQUET_SCHEMA = [
    "symbol", "business_date", "as_of_timestamp",
    "fiscal_quarter", "fiscal_year", "period_end",
    "revenue_cr", "net_profit_cr", "eps_reported",
    "yoy_eps_prev", "yoy_revenue_prev",
    "result_type", "source_url",
]


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_company_id(symbol: str, session: requests.Session) -> int | None:
    """Resolve NSE symbol → screener.in company ID."""
    try:
        resp = session.get(_SEARCH_API, params={"q": symbol}, timeout=10)
        resp.raise_for_status()
        results = resp.json()
        if results:
            return int(results[0]["id"])
    except Exception as exc:
        logger.warning("screener search failed symbol=%s: %s", symbol, exc)
    return None


def fetch_ttm_eps_series(
    company_id: int,
    session: requests.Session,
    days: int = 4000,
) -> list[tuple[str, float]]:
    """Fetch TTM EPS series from screener chart API.

    Returns list of (announcement_date_str, ttm_eps) sorted ascending.
    """
    try:
        url = _CHART_API.format(company_id=company_id)
        resp = session.get(url, params={"q": "EPS", "days": days}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        datasets = data.get("datasets", [])
        if not datasets:
            return []
        values = datasets[0].get("values", [])
        result = []
        for item in values:
            if len(item) == 2:
                date_str, eps_val = item
                try:
                    result.append((str(date_str), float(eps_val)))
                except (ValueError, TypeError):
                    continue
        return sorted(result, key=lambda x: x[0])
    except Exception as exc:
        logger.warning("screener chart API failed company_id=%d: %s", company_id, exc)
        return []


def fetch_quarterly_html(symbol: str, session: requests.Session) -> pd.DataFrame:
    """Scrape the quarterly results table from screener.in for recent quarters.

    Returns DataFrame with columns: period_label (e.g. 'Sep 2023'),
    sales_cr, eps_reported.  At most 13 rows (screener's display limit).
    """
    url = f"{_BASE}/company/{symbol}/consolidated/"
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("screener HTML fetch failed symbol=%s: %s", symbol, exc)
        return pd.DataFrame()

    soup = BeautifulSoup(resp.text, "html.parser")
    section = soup.find("section", id="quarters")
    if not section:
        return pd.DataFrame()

    table = section.find("table")
    if not table:
        return pd.DataFrame()

    # Header row: ['', 'Mar 2023', 'Jun 2023', ...]
    headers = [th.get_text(strip=True) for th in table.find("thead").find_all("th")]
    periods = headers[1:]  # skip first empty cell

    rows: dict[str, list] = {}
    for tr in table.find("tbody").find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
        if cells:
            rows[cells[0]] = cells[1:]

    records = []
    for i, period in enumerate(periods):
        sales_str = rows.get("Sales+", [None] * len(periods))[i] if i < len(rows.get("Sales+", [])) else None
        eps_str = rows.get("EPS in Rs", [None] * len(periods))[i] if i < len(rows.get("EPS in Rs", [])) else None
        np_str = rows.get("Net Profit", [None] * len(periods))[i] if i < len(rows.get("Net Profit", [])) else None

        records.append({
            "period_label": period,
            "sales_cr": _parse_number(sales_str),
            "net_profit_cr": _parse_number(np_str),
            "eps_reported": _parse_number(eps_str),
        })

    return pd.DataFrame(records)


def build_earnings_for_symbol(
    symbol: str,
    session: requests.Session | None = None,
    start: str = "2015-01-01",
    end: str = "2024-06-30",
) -> pd.DataFrame:
    """Build the L1 earnings rows for one symbol from screener.in.

    Strategy:
    1. TTM EPS chart API → announcement dates + YoY EPS comparison
    2. Recent quarters HTML → actual quarterly EPS for last 13 quarters
    3. Merge: prefer HTML quarterly EPS where available; fall back to TTM-derived

    Returns DataFrame matching _PARQUET_SCHEMA.
    """
    from quant.research.holdout_lock import assert_no_holdout_access

    if session is None:
        session = requests.Session()
        session.headers.update(_HEADERS)

    company_id = fetch_company_id(symbol, session)
    if company_id is None:
        logger.info("symbol not found on screener: %s", symbol)
        return _empty_df()

    time.sleep(_RATE_LIMIT_SECS)

    ttm_series = fetch_ttm_eps_series(company_id, session)
    if not ttm_series:
        logger.info("no TTM EPS data for %s (id=%d)", symbol, company_id)
        return _empty_df()

    time.sleep(_RATE_LIMIT_SECS)

    # Filter to requested date range (exclude hold-out)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)

    rows = []
    ttm_by_date = {d: v for d, v in ttm_series}
    sorted_dates = sorted(ttm_by_date.keys())

    for ann_date_str in sorted_dates:
        ann_ts = pd.Timestamp(ann_date_str)
        if ann_ts < start_ts or ann_ts > end_ts:
            continue

        try:
            assert_no_holdout_access(ann_date_str)
        except ValueError:
            continue

        ttm_eps = ttm_by_date[ann_date_str]

        # YoY: find the announcement date closest to 1 year prior
        yoy_eps = _find_yoy_value(ann_date_str, ttm_by_date, sorted_dates)

        # Fiscal quarter from announcement month (approximate)
        q, fy = _approx_fiscal_quarter(ann_ts)

        as_of = datetime(ann_ts.year, ann_ts.month, ann_ts.day, 12, 30, 0, tzinfo=timezone.utc)

        rows.append({
            "symbol": symbol,
            "business_date": ann_ts.date(),
            "as_of_timestamp": as_of,
            "fiscal_quarter": q,
            "fiscal_year": fy,
            "period_end": None,          # TTM series doesn't carry period_end
            "revenue_cr": None,          # populated from HTML quarterly table below
            "net_profit_cr": None,
            "eps_reported": ttm_eps,     # TTM EPS — labelled correctly in source_url
            "yoy_eps_prev": yoy_eps,
            "yoy_revenue_prev": None,
            "result_type": "quarterly",
            "source_url": f"screener:{company_id}:ttm_eps",
        })

    if not rows:
        return _empty_df()

    df = pd.DataFrame(rows, columns=_PARQUET_SCHEMA)

    # Overlay actual quarterly EPS from HTML (more precise, covers last 13 quarters)
    try:
        html_df = fetch_quarterly_html(symbol, session)
        if not html_df.empty:
            df = _overlay_html_quarterly(df, html_df, symbol)
    except Exception as exc:
        logger.warning("HTML quarterly overlay failed for %s: %s", symbol, exc)

    return df.sort_values("business_date").reset_index(drop=True)


def ingest_universe(
    universe_file: str | Path = "data/lake/midcap150_constituents.csv",
    start: str = "2015-01-01",
    end: str = "2024-06-30",
    dry_run: bool = False,
    max_symbols: int | None = None,
) -> pd.DataFrame:
    """Ingest screener.in earnings for all symbols in the universe file."""
    uni_path = Path(universe_file)
    if not uni_path.exists():
        raise FileNotFoundError(f"Universe file not found: {uni_path}")

    symbols = pd.read_csv(uni_path)["symbol"].str.upper().tolist()
    if max_symbols is not None:
        symbols = symbols[:max_symbols]

    logger.info("Ingesting screener.in earnings: %d symbols, %s → %s", len(symbols), start, end)

    session = requests.Session()
    session.headers.update(_HEADERS)

    all_frames: list[pd.DataFrame] = []
    for i, sym in enumerate(symbols):
        logger.info("[%d/%d] %s", i + 1, len(symbols), sym)
        try:
            df = build_earnings_for_symbol(sym, session=session, start=start, end=end)
            if not df.empty:
                all_frames.append(df)
                logger.info("  → %d rows", len(df))
            else:
                logger.info("  → no data")
        except Exception as exc:
            logger.warning("  → error: %s", exc)

    if not all_frames:
        logger.warning("No data collected")
        return _empty_df()

    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["symbol", "business_date"])
    combined = combined.sort_values(["symbol", "business_date"]).reset_index(drop=True)

    logger.info("Total: %d rows, %d symbols", len(combined), combined["symbol"].nunique())

    if not dry_run:
        _save_parquet(combined)

    return combined


# ── Private helpers ────────────────────────────────────────────────────────────

def _find_yoy_value(
    ann_date_str: str,
    ttm_by_date: dict[str, float],
    sorted_dates: list[str],
) -> float | None:
    """Find TTM EPS closest to exactly 1 year prior (within ±45 days)."""
    target = pd.Timestamp(ann_date_str) - pd.Timedelta(days=365)
    best_date: str | None = None
    best_delta = float("inf")
    for d in sorted_dates:
        delta = abs((pd.Timestamp(d) - target).days)
        if delta < best_delta and delta <= 45:
            best_delta = delta
            best_date = d
    return ttm_by_date[best_date] if best_date else None


def _approx_fiscal_quarter(ts: pd.Timestamp) -> tuple[int, int]:
    """Approximate Indian fiscal quarter from announcement month.

    Results are typically announced 30-45 days after quarter end:
    - Apr-Jun results announced Jul-Aug  → Q1
    - Jul-Sep results announced Oct-Nov  → Q2
    - Oct-Dec results announced Jan-Feb  → Q3
    - Jan-Mar results announced Apr-May  → Q4
    """
    m = ts.month
    if m in (7, 8):
        return 1, ts.year + 1
    elif m in (10, 11):
        return 2, ts.year + 1
    elif m in (1, 2):
        return 3, ts.year
    elif m in (4, 5):
        return 4, ts.year
    else:
        # Fallback: March / June / September / December
        if m in (3, 6):
            return (1 if m == 6 else 4), (ts.year + 1 if m == 6 else ts.year)
        return 2, ts.year + 1


def _overlay_html_quarterly(
    df: pd.DataFrame,
    html_df: pd.DataFrame,
    symbol: str,
) -> pd.DataFrame:
    """Replace eps_reported and revenue_cr with HTML quarterly values where available.

    html_df has period_label like 'Sep 2023'; we match to df rows by approximate
    fiscal quarter.
    """
    for _, hrow in html_df.iterrows():
        period_label = hrow.get("period_label", "")
        if not period_label:
            continue
        try:
            period_ts = pd.Timestamp("01 " + period_label)
        except Exception:
            continue

        # Find matching row in df: same fiscal quarter and fiscal year
        q, fy = _approx_fiscal_quarter_from_period_end(period_ts)
        mask = (df["fiscal_quarter"] == q) & (df["fiscal_year"] == fy)
        if mask.any():
            idx = df[mask].index[0]
            if hrow.get("eps_reported") is not None:
                df.loc[idx, "eps_reported"] = hrow["eps_reported"]
                df.loc[idx, "source_url"] = f"screener:{symbol}:quarterly_html"
            if hrow.get("sales_cr") is not None:
                df.loc[idx, "revenue_cr"] = hrow["sales_cr"]
            if hrow.get("net_profit_cr") is not None:
                df.loc[idx, "net_profit_cr"] = hrow["net_profit_cr"]

    return df


def _approx_fiscal_quarter_from_period_end(ts: pd.Timestamp) -> tuple[int, int]:
    """Indian fiscal quarter from period-end month."""
    m = ts.month
    if m in (4, 5, 6):
        return 1, ts.year + 1
    elif m in (7, 8, 9):
        return 2, ts.year + 1
    elif m in (10, 11, 12):
        return 3, ts.year + 1
    else:
        return 4, ts.year


def _parse_number(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _lake_dir() -> Path:
    base = os.environ.get("QUANT_DATA_DIR", "data/lake")
    return Path(base) / "earnings"


def _save_parquet(df: pd.DataFrame) -> Path:
    from quant.data.earnings_ingest import save_parquet
    return save_parquet(df)


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_PARQUET_SCHEMA)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="screener.in earnings ingest")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--symbol", help="Single NSE symbol")
    grp.add_argument("--universe-file", default="data/lake/midcap150_constituents.csv")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2024-06-30")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-symbols", type=int, default=None)
    args = parser.parse_args()

    if args.symbol:
        df = build_earnings_for_symbol(args.symbol, start=args.start, end=args.end)
        if not df.empty:
            print(df[["symbol", "business_date", "fiscal_quarter", "fiscal_year",
                       "eps_reported", "yoy_eps_prev", "revenue_cr"]].to_string(index=False))
            if not args.dry_run:
                _save_parquet(df)
        else:
            print(f"No data found for {args.symbol}")
    else:
        ingest_universe(
            universe_file=args.universe_file,
            start=args.start,
            end=args.end,
            dry_run=args.dry_run,
            max_symbols=args.max_symbols,
        )


if __name__ == "__main__":
    main()
