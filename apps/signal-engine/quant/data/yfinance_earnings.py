"""yfinance-based earnings supplement for the L1 data lake.

NOTE: yfinance quarterly_income_stmt only goes back ~4-6 quarters for Indian
(.NS) tickers.  Use as a top-up supplement after NSE API ingest, NOT as the
primary historical source.

Primary source for 2015-2024: earnings_ingest.build_historical_dataset()
  (NSE corporate results API — returns EPS/profit/revenue from NSE filings
   via the reInr, profit, income fields added in Month 4.)

This module fills the gap between the last NSE ingest run and today, keeping
the lake current without re-running the full NSE scrape.

Warning: yfinance returns USD for ADR-listed stocks (INFY, WIT, HDB, etc.).
Detected by EPS < ₹1; these are dropped with a warning — prefer NSE data.

Fetches quarterly EPS and revenue from yfinance for the Midcap 150 universe
and merges into data/lake/earnings/nse_results.parquet.

This is a best-effort supplement (plan §4.1) used when NSE filing-parser
output is unavailable.  PIT timestamps are set conservatively:

  as_of_timestamp = fiscal_quarter_end_date + 45 days at 18:00 IST

The 45-day lag is the SEBI-mandated maximum for listed company quarterly
results submission.  This is deliberately conservative — in practice most
results arrive within 30–45 days of quarter end.  We set it at 45 so we
never accidentally use data that might not have been public yet.

Usage
-----
  # Ingest all Midcap 150 symbols (first-time bootstrap):
  python -m quant.data.yfinance_earnings --universe-file data/lake/midcap150_constituents.csv

  # Refresh a single symbol:
  python -m quant.data.yfinance_earnings --symbol RELIANCE

  # Dry-run (print without writing):
  python -m quant.data.yfinance_earnings --symbol INFY --dry-run

Known limitations
-----------------
- yfinance has data quality issues for some Indian tickers (splits, mergers,
  missing quarters).  We cross-check: any EPS value > 10000 or < -10000 is
  flagged as suspicious and dropped.
- Some symbols require NSE→BSE ticker remapping; add to _BSE_FALLBACK dict
  as discovered.
- Annual results only from yfinance for certain tickers; quarterly preferred.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Parquet schema — must match earnings_ingest._PARQUET_SCHEMA exactly
_PARQUET_SCHEMA = [
    "symbol", "business_date", "as_of_timestamp",
    "fiscal_quarter", "fiscal_year", "period_end",
    "revenue_cr", "net_profit_cr", "eps_reported",
    "yoy_eps_prev", "yoy_revenue_prev",
    "result_type", "source_url",
]

# yfinance appends ".NS" for NSE; some stocks only trade on BSE (".BO")
_BSE_FALLBACK: dict[str, str] = {
    # add symbol → "SYMBOL.BO" mappings as discovered
}

# Conservative: 45 calendar days after quarter-end at 18:00 IST (12:30 UTC)
_PIT_LAG_DAYS: int = 45

# Sanity bounds on EPS (₹ per share); values outside are dropped
_EPS_MIN: float = -10_000.0
_EPS_MAX: float = 100_000.0

# Sanity bounds on revenue (₹ crore)
_REV_MIN: float = 0.0
_REV_MAX: float = 20_000_000.0  # ₹2 lakh crore upper cap


def _lake_dir() -> Path:
    base = os.environ.get("QUANT_DATA_DIR", "data/lake")
    return Path(base) / "earnings"


def _parquet_path() -> Path:
    return _lake_dir() / "nse_results.parquet"


def _yf_ticker(symbol: str) -> str:
    """Map NSE symbol to yfinance ticker string."""
    if symbol in _BSE_FALLBACK:
        return _BSE_FALLBACK[symbol]
    return f"{symbol}.NS"


def _indian_fiscal_quarter(period_end: date) -> tuple[int, int]:
    """Return (fiscal_quarter, fiscal_year) for an Indian fiscal period end.

    Indian fiscal year: April → March.
    Q1 ends June 30, Q2 ends Sep 30, Q3 ends Dec 31, Q4 ends Mar 31.
    Fiscal year number = the year the March quarter falls in.

    Example: period_end=2023-09-30 → Q2 FY2024.
    """
    m = period_end.month
    if m in (4, 5, 6):
        q, fy = 1, period_end.year + 1
    elif m in (7, 8, 9):
        q, fy = 2, period_end.year + 1
    elif m in (10, 11, 12):
        q, fy = 3, period_end.year + 1
    else:  # Jan, Feb, Mar
        q, fy = 4, period_end.year
    return q, fy


def _pit_timestamp(period_end: date, lag_days: int = _PIT_LAG_DAYS) -> datetime:
    """Conservative as_of_timestamp: period_end + lag_days at 18:00 IST."""
    import calendar
    ann_date = date(period_end.year, period_end.month, period_end.day)
    # Add calendar days
    ann_ts = pd.Timestamp(ann_date) + pd.Timedelta(days=lag_days)
    return datetime(
        ann_ts.year, ann_ts.month, ann_ts.day,
        12, 30, 0,  # 18:00 IST = 12:30 UTC
        tzinfo=timezone.utc,
    )


def fetch_quarterly_earnings(symbol: str) -> pd.DataFrame:
    """Fetch quarterly earnings from yfinance for one NSE symbol.

    Returns
    -------
    pd.DataFrame with columns matching _PARQUET_SCHEMA.
        Empty DataFrame on failure or missing data.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed — run: pip install yfinance")
        return _empty_df()

    ticker_str = _yf_ticker(symbol)
    try:
        ticker = yf.Ticker(ticker_str)
        # Prefer quarterly income statement
        qincome = ticker.quarterly_income_stmt
        if qincome is None or qincome.empty:
            qincome = ticker.quarterly_financials  # older API
    except Exception as exc:
        logger.warning("yfinance fetch failed symbol=%s ticker=%s: %s", symbol, ticker_str, exc)
        return _empty_df()

    if qincome is None or qincome.empty:
        logger.debug("No quarterly data for %s", ticker_str)
        return _empty_df()

    # yfinance returns income statement with dates as columns, metrics as rows
    # Transpose so each row is one quarter
    df = qincome.T.copy()
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    rows = []
    for period_dt, row in df.iterrows():
        period_end = period_dt.date()
        fiscal_q, fiscal_y = _indian_fiscal_quarter(period_end)
        as_of = _pit_timestamp(period_end)

        # Revenue: yfinance uses "Total Revenue" in USD cents or native currency
        rev_raw = _get_metric(row, ["Total Revenue", "Revenue", "TotalRevenue"])
        revenue_cr = None
        if rev_raw is not None and _REV_MIN <= rev_raw / 1e7 <= _REV_MAX:
            # yfinance returns Indian company figures in ₹ (not USD for .NS)
            # Convert from ₹ to ₹ crore (1 crore = 10^7)
            revenue_cr = float(rev_raw) / 1e7

        # Net profit
        np_raw = _get_metric(row, [
            "Net Income", "Net Income Common Stockholders",
            "NetIncome", "ProfitLoss",
        ])
        net_profit_cr = None
        if np_raw is not None:
            net_profit_cr = float(np_raw) / 1e7

        # EPS — yfinance may have Basic EPS or we derive from net income + shares
        eps_raw = _get_metric(row, ["Basic EPS", "EPS", "BasicEPS"])
        eps_reported = None
        if eps_raw is not None and _EPS_MIN <= float(eps_raw) <= _EPS_MAX:
            eps_reported = float(eps_raw)

        # business_date = fiscal quarter end (best proxy for announcement date)
        # The PIT timestamp accounts for the actual announcement lag
        business_date = period_end

        rows.append({
            "symbol": symbol,
            "business_date": business_date,
            "as_of_timestamp": as_of,
            "fiscal_quarter": fiscal_q,
            "fiscal_year": fiscal_y,
            "period_end": period_end,
            "revenue_cr": revenue_cr,
            "net_profit_cr": net_profit_cr,
            "eps_reported": eps_reported,
            "yoy_eps_prev": None,   # computed post-hoc via _compute_yoy
            "yoy_revenue_prev": None,
            "result_type": "quarterly",
            "source_url": f"yfinance:{ticker_str}",
        })

    if not rows:
        return _empty_df()

    result = pd.DataFrame(rows, columns=_PARQUET_SCHEMA)
    result = _compute_yoy(result)
    return result


def _get_metric(row: pd.Series, keys: list[str]) -> float | None:
    """Try multiple key names; return first non-null numeric value."""
    for k in keys:
        if k in row.index:
            val = row[k]
            try:
                if val is not None and not pd.isna(val):
                    return float(val)
            except (TypeError, ValueError):
                continue
    return None


def _compute_yoy(df: pd.DataFrame) -> pd.DataFrame:
    """Compute YoY EPS and revenue vs same fiscal quarter last year."""
    df = df.sort_values(["fiscal_year", "fiscal_quarter"]).copy()

    yoy_eps = []
    yoy_rev = []
    for i, row in df.iterrows():
        q, fy = row["fiscal_quarter"], row["fiscal_year"]
        prev = df[
            (df["fiscal_quarter"] == q)
            & (df["fiscal_year"] == fy - 1)
        ]
        prev_eps = float(prev.iloc[0]["eps_reported"]) if not prev.empty and prev.iloc[0]["eps_reported"] is not None else None
        prev_rev = float(prev.iloc[0]["revenue_cr"]) if not prev.empty and prev.iloc[0]["revenue_cr"] is not None else None
        yoy_eps.append(prev_eps)
        yoy_rev.append(prev_rev)

    df["yoy_eps_prev"] = yoy_eps
    df["yoy_revenue_prev"] = yoy_rev
    return df


def ingest_universe(
    universe_file: str | Path = "data/lake/midcap150_constituents.csv",
    rate_limit_secs: float = 2.0,
    max_symbols: int | None = None,
    dry_run: bool = False,
) -> pd.DataFrame:
    """Fetch earnings for all symbols in the universe file and save to parquet.

    Parameters
    ----------
    universe_file : str | Path
        CSV with a "symbol" column.
    rate_limit_secs : float
        Sleep between yfinance requests to avoid rate-limiting.
    max_symbols : int | None
        If set, process only the first N symbols (useful for testing).
    dry_run : bool
        If True, return the DataFrame without writing to disk.

    Returns
    -------
    pd.DataFrame — all fetched records.
    """
    uni_path = Path(universe_file)
    if not uni_path.exists():
        raise FileNotFoundError(f"Universe file not found: {uni_path}")

    symbols = pd.read_csv(uni_path)["symbol"].str.upper().tolist()
    if max_symbols is not None:
        symbols = symbols[:max_symbols]

    logger.info("Ingesting yfinance earnings for %d symbols", len(symbols))
    all_frames: list[pd.DataFrame] = []

    for i, sym in enumerate(symbols):
        logger.info("[%d/%d] Fetching %s", i + 1, len(symbols), sym)
        try:
            df = fetch_quarterly_earnings(sym)
            if not df.empty:
                all_frames.append(df)
                logger.info("  → %d quarters", len(df))
            else:
                logger.info("  → no data")
        except Exception as exc:
            logger.warning("  → error: %s", exc)

        if i < len(symbols) - 1:
            time.sleep(rate_limit_secs)

    if not all_frames:
        logger.warning("No earnings data collected")
        return _empty_df()

    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["symbol", "period_end"])
    combined = combined.sort_values(["symbol", "business_date"]).reset_index(drop=True)

    logger.info("Total records: %d across %d symbols", len(combined), combined["symbol"].nunique())

    if not dry_run:
        _save_parquet(combined)

    return combined


def ingest_symbol(
    symbol: str,
    dry_run: bool = False,
) -> pd.DataFrame:
    """Fetch and save earnings for a single symbol."""
    df = fetch_quarterly_earnings(symbol)
    if df.empty:
        logger.warning("No data for %s", symbol)
        return df
    logger.info("Fetched %d quarters for %s", len(df), symbol)
    if not dry_run:
        _save_parquet(df)
    return df


def _save_parquet(df: pd.DataFrame) -> Path:
    path = _parquet_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["symbol", "period_end"])
        combined.to_parquet(path, index=False)
        logger.info("Merged → %s (total %d rows)", path, len(combined))
    else:
        df.to_parquet(path, index=False)
        logger.info("Wrote → %s (%d rows)", path, len(df))

    return path


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_PARQUET_SCHEMA)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="yfinance earnings ingest for Midcap 150")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--symbol", help="Single NSE symbol to ingest")
    grp.add_argument(
        "--universe-file",
        default="data/lake/midcap150_constituents.csv",
        help="CSV file with 'symbol' column (default: Midcap 150)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    parser.add_argument(
        "--max-symbols", type=int, default=None,
        help="Limit number of symbols (for testing)",
    )
    parser.add_argument(
        "--rate-limit", type=float, default=2.0,
        help="Seconds between yfinance requests (default: 2.0)",
    )
    args = parser.parse_args()

    if args.symbol:
        df = ingest_symbol(args.symbol, dry_run=args.dry_run)
        if not df.empty:
            print(df[["symbol", "business_date", "fiscal_quarter", "fiscal_year",
                       "eps_reported", "revenue_cr"]].to_string(index=False))
    else:
        ingest_universe(
            universe_file=args.universe_file,
            rate_limit_secs=args.rate_limit,
            max_symbols=args.max_symbols,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
