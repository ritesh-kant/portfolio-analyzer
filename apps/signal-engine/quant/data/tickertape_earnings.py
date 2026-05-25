"""Tickertape Pro quarterly earnings ingest.

Tickertape Pro provides actual quarterly EPS (not TTM) for NSE-listed companies.
This module parses Tickertape CSV exports and writes a merged parquet where:
  - Announcement dates (business_date, as_of_timestamp) come from the existing
    NSE/screener parquet (those dates are PIT-correct even if EPS values were TTM)
  - eps_reported is replaced with true quarterly EPS from Tickertape
  - yoy_eps_prev is recomputed using quarterly EPS

Why this matters
----------------
screener.in provides TTM (trailing twelve months) EPS.  When Q3 beats, TTM
barely moves because 3 other quarters dilute the signal.  Quarterly EPS
(Q3-FY25 vs Q3-FY24) gives a clean, undiluted YoY comparison — the standard
PEAD signal in the academic literature.

Supported input formats
-----------------------
A) Per-company long CSV (Tickertape company page → Financials → Quarterly → Export):
     Rows = individual quarters, columns include a period column and EPS.
     Period strings: "Mar '24", "Dec '24", "Q3 FY25", "Q3FY25", etc.
     Filename convention: {NSE_SYMBOL}.csv (case-insensitive, e.g. MPHASIS.csv).

B) Bulk screener CSV (Tickertape Screener → Nifty Midcap 150 → add EPS columns → Export):
     Wide format: one row per company; columns include "NSE Symbol" and per-quarter
     EPS columns like "EPS Q3FY25", "EPS Q3 FY25", "EPS Dec '24", etc.

C) Transposed wide CSV (Tickertape company page, alternative export):
     Rows = metrics (Revenue, Net Profit, EPS, …), columns = quarter periods.
     Auto-detected when the first column contains "EPS" or "Earnings Per Share".

Export instructions for Tickertape Pro
---------------------------------------
  Option A (recommended — deepest history):
    1. Open a company page: https://www.tickertape.in/stocks/[SYMBOL]
    2. Click "Financials" → "Quarterly" tab
    3. Click the download icon (↓) → "Export CSV"
    4. Save as {SYMBOL}.csv to a single directory (e.g. ~/Downloads/tt_quarterly/)
    5. Repeat for all 150 midcap stocks (or batch via --help for automation tips)

  Option B (bulk, but may be limited to ~12 recent quarters):
    1. Go to https://www.tickertape.in/screener
    2. Filter: Index = "Nifty Midcap 150"
    3. Add column: EPS (scroll through quarterly options back to FY2015 if available)
    4. Export → save as tickertape_midcap150.csv

Usage
-----
  # Per-company CSVs in a directory
  python -m quant.data.tickertape_earnings \\
    --input-dir ~/Downloads/tt_quarterly/

  # Single bulk screener CSV
  python -m quant.data.tickertape_earnings \\
    --input-file ~/Downloads/tickertape_midcap150.csv

  # Probe format of a single file (no writes)
  python -m quant.data.tickertape_earnings --probe ~/Downloads/MPHASIS.csv

  # Dry-run: parse + merge but don't overwrite parquet
  python -m quant.data.tickertape_earnings \\
    --input-dir ~/Downloads/tt_quarterly/ --dry-run

Output
------
  Overwrites (or creates) data/lake/earnings/nse_results.parquet with:
    - Same schema as earnings_ingest.py
    - eps_reported = true quarterly EPS (from Tickertape)
    - yoy_eps_prev = eps_reported[t] from same (symbol, fiscal_quarter, fiscal_year - 1)
    - Rows without a Tickertape match retain their original (TTM) eps_reported so
      the parquet stays complete; source_url is updated to "tickertape:quarterly"
      for rows that were successfully patched.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from quant.data.earnings_ingest import _parquet_path, _empty_df, compute_yoy_columns

logger = logging.getLogger(__name__)

_PARQUET_SCHEMA = [
    "symbol", "business_date", "as_of_timestamp",
    "fiscal_quarter", "fiscal_year", "period_end",
    "revenue_cr", "net_profit_cr", "eps_reported",
    "yoy_eps_prev", "yoy_revenue_prev",
    "result_type", "source_url",
]

# ── Period string parsing ──────────────────────────────────────────────────────

# Month name → month number
_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Patterns for "Q3 FY25", "Q3FY25", "Q3 FY2025"
_QFY_RE = re.compile(r"Q([1-4])\s*FY\s*(\d{2,4})", re.IGNORECASE)
# Patterns for "Mar '24", "Mar '2024", "Mar 2024", "Mar-24"
_MMM_YEAR_RE = re.compile(r"([A-Za-z]{3})[\s'\-]+'?(\d{2,4})", re.IGNORECASE)


def _two_digit_year(y: str) -> int:
    n = int(y)
    if n < 100:
        return 2000 + n if n <= 50 else 1900 + n
    return n


def parse_period(s: str) -> tuple[int, int] | None:
    """Parse a period string to (fiscal_quarter, fiscal_year).

    Indian fiscal year convention: Q1=Apr-Jun, Q2=Jul-Sep, Q3=Oct-Dec, Q4=Jan-Mar.
    fiscal_year is the calendar year in which March falls (i.e. FY2025 ends Mar 2025).

    Returns None if the string cannot be parsed.

    Examples
    --------
    >>> parse_period("Q3 FY25")   # → (3, 2025)
    >>> parse_period("Dec '24")   # → (3, 2025)   # Dec 2024 = Q3 of FY2025
    >>> parse_period("Mar '24")   # → (4, 2024)   # Mar 2024 = Q4 of FY2024
    >>> parse_period("Jun 2024")  # → (1, 2025)   # Jun 2024 = Q1 of FY2025
    """
    s = str(s).strip()

    m = _QFY_RE.search(s)
    if m:
        return int(m.group(1)), _two_digit_year(m.group(2))

    m = _MMM_YEAR_RE.search(s)
    if m:
        mon_str, yr_str = m.group(1).lower(), m.group(2)
        month = _MONTH_MAP.get(mon_str)
        if month is None:
            return None
        year = _two_digit_year(yr_str)
        # Map calendar-month-of-quarter-end → (fiscal_quarter, fiscal_year)
        if month in (4, 5, 6):
            return 1, year + 1
        elif month in (7, 8, 9):
            return 2, year + 1
        elif month in (10, 11, 12):
            return 3, year + 1
        else:  # Jan, Feb, Mar
            return 4, year

    return None


# ── Format detection ───────────────────────────────────────────────────────────

def detect_format(path_or_df) -> str:
    """Return 'long', 'wide_row', or 'wide_col'.

    long      — per-company CSV; rows = quarters, one "period" column
    wide_row  — bulk screener CSV; rows = companies, columns = quarter EPS
    wide_col  — transposed company CSV; rows = metrics, columns = quarter periods

    Accepts either a Path (reads file directly, handles Tickertape 4-row header)
    or a pre-loaded DataFrame (legacy usage in tests).
    """
    if isinstance(path_or_df, Path):
        # Peek at raw file to detect Tickertape Income Statement header
        try:
            raw = pd.read_csv(path_or_df, header=None, nrows=5)
            first_col = raw.iloc[:, 0].astype(str)
            if first_col.str.contains("income statement by tickertape", case=False).any():
                return "wide_col"
        except Exception:
            pass
        try:
            df = pd.read_csv(path_or_df, nrows=5, thousands=",")
        except Exception:
            return "long"
    else:
        df = path_or_df

    cols_lower = [c.lower().strip() for c in df.columns]

    # Bulk screener: has "nse symbol" or "ticker" column + EPS quarter columns
    if any(c in ("nse symbol", "nse_symbol", "ticker", "symbol") for c in cols_lower):
        eps_cols = [c for c in cols_lower if "eps" in c and parse_period(c) is not None]
        if eps_cols:
            return "wide_row"
        return "wide_row"

    # Transposed company CSV: first column contains metric names, columns are periods
    first_col_vals = df.iloc[:, 0].astype(str).str.lower().tolist()
    if any("eps" in v or "earnings per share" in v for v in first_col_vals):
        if sum(1 for c in df.columns[1:] if parse_period(str(c)) is not None) >= 2:
            return "wide_col"

    return "long"


# ── Format-specific parsers ────────────────────────────────────────────────────

def _safe_float(val: object) -> float | None:
    if val is None:
        return None
    try:
        s = str(val).strip().replace(",", "").replace("₹", "").strip()
        if not s or s.lower() in ("na", "nan", "null", "-", "–", "—", ""):
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def _find_period_col(df: pd.DataFrame) -> str | None:
    """Find the column that contains quarter period strings."""
    candidates = ["period", "quarter", "date", "qtr", "months", "year"]
    cols_lower = {c.lower().strip(): c for c in df.columns}
    for cand in candidates:
        if cand in cols_lower:
            return cols_lower[cand]
    # Try to find by content: column where ≥ 50% of values are parseable periods
    for col in df.columns:
        vals = df[col].dropna().astype(str)
        if len(vals) == 0:
            continue
        parseable = sum(1 for v in vals if parse_period(v) is not None)
        if parseable / len(vals) >= 0.5:
            return col
    return None


def _find_eps_col(df: pd.DataFrame) -> str | None:
    """Find the EPS column in a long-format per-company CSV."""
    eps_candidates = ["eps", "eps (rs)", "eps (₹)", "earnings per share",
                      "diluted eps", "basic eps", "eps_reported"]
    cols_lower = {c.lower().strip(): c for c in df.columns}
    for cand in eps_candidates:
        if cand in cols_lower:
            return cols_lower[cand]
    # Fuzzy: column whose name contains "eps"
    for col in df.columns:
        if "eps" in col.lower():
            return col
    return None


def _find_revenue_col(df: pd.DataFrame) -> str | None:
    revenue_candidates = [
        "revenue", "net sales", "total revenue", "sales", "total income",
        "revenue (cr)", "net sales (cr)", "sales (cr)", "revenue (₹ cr)",
    ]
    cols_lower = {c.lower().strip(): c for c in df.columns}
    for cand in revenue_candidates:
        if cand in cols_lower:
            return cols_lower[cand]
    for col in df.columns:
        if "revenue" in col.lower() or "sales" in col.lower() or "income" in col.lower():
            return col
    return None


def parse_long_csv(path: Path, symbol: str) -> pd.DataFrame:
    """Parse a per-company long-format quarterly CSV.

    Expected: rows = individual quarters, one period column, one EPS column.
    Returns DataFrame with columns: symbol, fiscal_quarter, fiscal_year,
                                    eps_quarterly, revenue_quarterly_cr.
    """
    try:
        df = pd.read_csv(path, thousands=",")
    except Exception as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return pd.DataFrame()

    period_col = _find_period_col(df)
    eps_col = _find_eps_col(df)
    rev_col = _find_revenue_col(df)

    if period_col is None:
        logger.warning("%s: could not find period column. Columns: %s", path.name, list(df.columns))
        return pd.DataFrame()
    if eps_col is None:
        logger.warning("%s: could not find EPS column. Columns: %s", path.name, list(df.columns))
        return pd.DataFrame()

    rows = []
    for _, row in df.iterrows():
        period = parse_period(str(row[period_col]))
        if period is None:
            continue
        fq, fy = period
        eps = _safe_float(row[eps_col])
        rev = _safe_float(row[rev_col]) if rev_col else None
        rows.append({
            "symbol": symbol.upper(),
            "fiscal_quarter": fq,
            "fiscal_year": fy,
            "eps_quarterly": eps,
            "revenue_quarterly_cr": rev,
        })

    if not rows:
        logger.warning("%s: no parseable quarter rows found", path.name)
        return pd.DataFrame()

    return pd.DataFrame(rows)


def _read_tickertape_wide_col(path: Path) -> tuple[pd.DataFrame, str | None]:
    """Read a Tickertape Income Statement CSV handling the 4-row header.

    Tickertape exports look like:
      Row 0: "Income Statement by Tickertape ,,,..."
      Row 1: "for: Mphasis Ltd (MPHASIS),,,..."
      Row 2: "Visit https://...,,,..."
      Row 3: empty
      Row 4: "Financial type,DEC 2023,MAR 2024,..."  ← actual header
      Row 5+: data rows, metric names have leading ' (e.g. "'EPS'", "'Total Revenue'")

    Returns (DataFrame with proper headers, nse_symbol_or_None).
    """
    # Scan first 8 rows for the header row and NSE symbol
    raw = pd.read_csv(path, header=None, nrows=8)
    symbol_from_content: str | None = None
    header_row: int = 0

    for i, row in raw.iterrows():
        first_cell = str(row.iloc[0]).strip()
        # Row like "for: Mphasis Ltd (MPHASIS)"
        m = re.search(r'\(([A-Z]{2,10})\)', first_cell)
        if m:
            symbol_from_content = m.group(1)
        # Header row starts with "Financial type" or similar metric-label column
        if first_cell.lower().startswith("financial") or (
            # fallback: row where ≥ 3 subsequent cells parse as periods
            sum(1 for v in row.iloc[1:] if parse_period(str(v)) is not None) >= 3
        ):
            header_row = int(str(i))
            break

    df = pd.read_csv(path, skiprows=header_row, index_col=0, thousands=",")
    # Strip Tickertape's leading/trailing ' from metric names like "'EPS'" → "EPS"
    df.index = df.index.str.strip().str.strip("'").str.strip()
    # Strip from column names too (rare but defensive)
    df.columns = df.columns.str.strip().str.strip("'").str.strip()
    return df, symbol_from_content


def parse_wide_col_csv(path: Path, symbol: str) -> pd.DataFrame:
    """Parse a transposed company CSV: rows = metrics, columns = quarter periods.

    Handles Tickertape's 4-row header and single-quote-prefixed metric names.
    NSE symbol is extracted from file content if available (overrides filename).
    """
    try:
        df, content_symbol = _read_tickertape_wide_col(path)
    except Exception as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return pd.DataFrame()

    resolved_symbol = (content_symbol or symbol).upper()

    # Normalise index for lookups
    idx_norm = {str(v).strip().lower(): v for v in df.index}

    def _find_row(keywords: list[str]) -> str | None:
        for kw in keywords:
            if kw in idx_norm:
                return idx_norm[kw]
        for idx_val in df.index:
            if any(kw in str(idx_val).lower() for kw in keywords):
                return idx_val
        return None

    eps_row = _find_row(["eps", "earnings per share", "basic eps", "diluted eps"])
    rev_row = _find_row(["total revenue", "net sales", "revenue", "total income", "sales"])
    net_row = _find_row(["net income", "net profit", "profit after tax", "pat", "= net income"])

    if eps_row is None:
        logger.warning("%s (wide_col): no EPS row found. Rows: %s", path.name, list(df.index))
        return pd.DataFrame()

    rows = []
    for col in df.columns:
        period = parse_period(str(col))
        if period is None:
            continue
        fq, fy = period
        eps = _safe_float(df.at[eps_row, col])
        rev = _safe_float(df.at[rev_row, col]) if rev_row else None
        net = _safe_float(df.at[net_row, col]) if net_row else None
        rows.append({
            "symbol": resolved_symbol,
            "fiscal_quarter": fq,
            "fiscal_year": fy,
            "eps_quarterly": eps,
            "revenue_quarterly_cr": rev,
            "net_profit_quarterly_cr": net,
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def parse_wide_row_csv(path: Path) -> pd.DataFrame:
    """Parse a bulk screener CSV: one row per company, EPS columns per quarter."""
    try:
        df = pd.read_csv(path, thousands=",")
    except Exception as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return pd.DataFrame()

    # Find symbol column
    symbol_col = None
    for col in df.columns:
        if col.lower().strip() in ("nse symbol", "nse_symbol", "symbol", "ticker"):
            symbol_col = col
            break
    if symbol_col is None:
        logger.warning("wide_row: could not find NSE Symbol column. Columns: %s", list(df.columns))
        return pd.DataFrame()

    # Identify EPS quarter columns: any column whose name contains "eps" and parses as a period
    eps_cols: dict[tuple[int, int], str] = {}
    for col in df.columns:
        if "eps" not in col.lower():
            continue
        period = parse_period(col)
        if period is not None:
            eps_cols[period] = col

    if not eps_cols:
        logger.warning("wide_row: no EPS quarter columns found. Columns: %s", list(df.columns))
        return pd.DataFrame()

    rows = []
    for _, company_row in df.iterrows():
        symbol = str(company_row[symbol_col]).strip().upper()
        if not symbol:
            continue
        for (fq, fy), col in eps_cols.items():
            eps = _safe_float(company_row[col])
            rows.append({
                "symbol": symbol,
                "fiscal_quarter": fq,
                "fiscal_year": fy,
                "eps_quarterly": eps,
                "revenue_quarterly_cr": None,
            })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ── Symbol extraction from filename ───────────────────────────────────────────

def _symbol_from_filename(path: Path) -> str:
    """Extract NSE symbol from filename, e.g. 'MPHASIS.csv' → 'MPHASIS'."""
    return path.stem.upper().strip()


# ── Main parse pipeline ────────────────────────────────────────────────────────

def parse_directory(input_dir: Path) -> pd.DataFrame:
    """Parse all CSV files in a directory (per-company format A or C)."""
    csvs = sorted(input_dir.glob("*.csv")) + sorted(input_dir.glob("*.CSV"))
    if not csvs:
        logger.error("No CSV files found in %s", input_dir)
        return pd.DataFrame()

    all_dfs = []
    for csv_path in csvs:
        symbol = _symbol_from_filename(csv_path)
        fmt = detect_format(csv_path)
        if fmt == "wide_col":
            df = parse_wide_col_csv(csv_path, symbol)
        else:
            df = parse_long_csv(csv_path, symbol)

        if not df.empty:
            all_dfs.append(df)
            logger.debug("Parsed %s: %d rows (%s)", csv_path.name, len(df), fmt)

    if not all_dfs:
        return pd.DataFrame()

    combined = pd.concat(all_dfs, ignore_index=True)
    return combined.drop_duplicates(subset=["symbol", "fiscal_quarter", "fiscal_year"])


def parse_single_file(input_file: Path) -> pd.DataFrame:
    """Parse a single CSV file (bulk screener or per-company)."""
    fmt = detect_format(input_file)
    logger.info("Detected format: %s in %s", fmt, input_file.name)

    if fmt == "wide_row":
        return parse_wide_row_csv(input_file)
    elif fmt == "wide_col":
        symbol = _symbol_from_filename(input_file)
        return parse_wide_col_csv(input_file, symbol)
    else:
        symbol = _symbol_from_filename(input_file)
        return parse_long_csv(input_file, symbol)


# ── Merge with NSE announcement dates ─────────────────────────────────────────

def merge_with_existing_parquet(
    tt_df: pd.DataFrame,
    parquet_path: Path | None = None,
) -> pd.DataFrame:
    """Replace eps_reported in the existing parquet with Tickertape quarterly EPS.

    Strategy:
      - Join key: (symbol, fiscal_quarter, fiscal_year)
      - existing parquet has correct PIT announcement dates (business_date,
        as_of_timestamp) from screener.in; only eps_reported was TTM, not quarterly
      - For rows where a Tickertape match exists, overwrite eps_reported and
        optionally revenue_cr
      - source_url updated to "tickertape:quarterly" for patched rows
      - yoy_eps_prev recomputed for the entire dataset
    """
    if parquet_path is None:
        parquet_path = _parquet_path()

    if not parquet_path.exists():
        logger.warning(
            "No existing parquet at %s — building from Tickertape data only "
            "(no announcement dates; business_date will be period_end approximation).",
            parquet_path,
        )
        return _build_from_tickertape_only(tt_df)

    existing = pd.read_parquet(parquet_path)
    logger.info("Existing parquet: %d rows, %d symbols", len(existing), existing["symbol"].nunique())

    tt_df = tt_df.copy()
    tt_df["fiscal_quarter"] = tt_df["fiscal_quarter"].astype(int)
    tt_df["fiscal_year"] = tt_df["fiscal_year"].astype(int)

    existing["fiscal_quarter"] = pd.to_numeric(existing["fiscal_quarter"], errors="coerce").astype("Int64")
    existing["fiscal_year"] = pd.to_numeric(existing["fiscal_year"], errors="coerce").astype("Int64")

    # Build a lookup: (symbol, fq, fy) → row index in existing
    tt_lookup = tt_df.set_index(["symbol", "fiscal_quarter", "fiscal_year"])

    patched = 0
    eps_col = existing["eps_reported"].copy()
    rev_col = existing["revenue_cr"].copy()
    src_col = existing["source_url"].copy()

    for idx, row in existing.iterrows():
        key = (row["symbol"], row["fiscal_quarter"], row["fiscal_year"])
        if any(pd.isna(k) for k in key):
            continue
        try:
            key_int = (str(key[0]), int(key[1]), int(key[2]))
            tt_row = tt_lookup.loc[key_int]
            eps_q = tt_row["eps_quarterly"] if hasattr(tt_row, "__getitem__") else tt_row.iloc[0]["eps_quarterly"]
            rev_q = tt_row["revenue_quarterly_cr"] if hasattr(tt_row, "__getitem__") else tt_row.iloc[0]["revenue_quarterly_cr"]
        except KeyError:
            continue

        # If Tickertape returned multiple rows for the same key, take the first
        if isinstance(eps_q, pd.Series):
            eps_q = eps_q.iloc[0]
        if isinstance(rev_q, pd.Series):
            rev_q = rev_q.iloc[0]

        new_eps = _safe_float(eps_q)
        if new_eps is not None:
            eps_col.at[idx] = new_eps
            if rev_q is not None:
                rv = _safe_float(rev_q)
                if rv is not None:
                    rev_col.at[idx] = rv
            src_col.at[idx] = "tickertape:quarterly"
            patched += 1

    existing["eps_reported"] = eps_col
    existing["revenue_cr"] = rev_col
    existing["source_url"] = src_col

    logger.info("Patched %d / %d rows with Tickertape quarterly EPS", patched, len(existing))

    # Check for Tickertape rows that have NO match in the existing parquet
    # (i.e., new symbols or new quarters not captured by screener)
    existing_keys = set(
        zip(
            existing["symbol"].astype(str),
            existing["fiscal_quarter"].astype(str),
            existing["fiscal_year"].astype(str),
        )
    )
    tt_keys = set(
        zip(
            tt_df["symbol"].astype(str),
            tt_df["fiscal_quarter"].astype(str),
            tt_df["fiscal_year"].astype(str),
        )
    )
    unmatched = tt_keys - existing_keys
    if unmatched:
        logger.info(
            "%d Tickertape rows have no NSE parquet match (new symbols/quarters) — "
            "these will be added with period_end as approximate business_date (not PIT-ideal).",
            len(unmatched),
        )
        new_rows = _build_from_tickertape_only(
            tt_df[
                tt_df.apply(
                    lambda r: (str(r["symbol"]), str(r["fiscal_quarter"]), str(r["fiscal_year"])) in unmatched,
                    axis=1,
                )
            ]
        )
        if not new_rows.empty:
            existing = pd.concat([existing, new_rows], ignore_index=True)

    existing = existing.drop_duplicates(
        subset=["symbol", "fiscal_quarter", "fiscal_year"], keep="last"
    ).sort_values(["symbol", "business_date"]).reset_index(drop=True)

    # Recompute yoy_eps_prev using the new quarterly EPS values
    existing = compute_yoy_columns(existing)

    return existing


def _build_from_tickertape_only(tt_df: pd.DataFrame) -> pd.DataFrame:
    """Build parquet rows from Tickertape data alone (no NSE announcement dates).

    business_date is approximated as the period-end date.  This is NOT PIT-correct
    (results are typically announced 30–60 days after quarter end), but it is
    better than discarding the data.  These rows are flagged in source_url.
    """
    rows = []
    for _, r in tt_df.iterrows():
        fq = int(r["fiscal_quarter"])
        fy = int(r["fiscal_year"])
        # Approximate period-end date from (fiscal_quarter, fiscal_year)
        q_end_month = {1: 6, 2: 9, 3: 12, 4: 3}[fq]
        q_end_year = (fy - 1) if fq == 1 else fy
        if fq == 4:
            q_end_year = fy
        # Q1 FY25: ends Jun 2024 → year = 2024; Q4 FY25: ends Mar 2025 → year = 2025
        if fq == 1:
            q_end_year = fy - 1
        elif fq == 4:
            q_end_year = fy
        else:
            q_end_year = fy - 1  # Q2/Q3 also fall in year before FY end

        # Wait, let me recalculate:
        # Q1 FY25 = Apr-Jun 2024 → end Jun 2024 → calendar year 2024 = fy-1
        # Q2 FY25 = Jul-Sep 2024 → end Sep 2024 → calendar year 2024 = fy-1
        # Q3 FY25 = Oct-Dec 2024 → end Dec 2024 → calendar year 2024 = fy-1
        # Q4 FY25 = Jan-Mar 2025 → end Mar 2025 → calendar year 2025 = fy
        if fq in (1, 2, 3):
            q_end_year = fy - 1
        else:
            q_end_year = fy

        import calendar
        last_day = calendar.monthrange(q_end_year, q_end_month)[1]
        period_end = pd.Timestamp(year=q_end_year, month=q_end_month, day=last_day).date()

        # Approximate announcement: ~45 days after period end (Indian filing norms)
        ann_date = (pd.Timestamp(period_end) + pd.Timedelta(days=45)).date()
        as_of = datetime(ann_date.year, ann_date.month, ann_date.day, 12, 30, tzinfo=timezone.utc)

        rows.append({
            "symbol": str(r["symbol"]).upper(),
            "business_date": ann_date,
            "as_of_timestamp": as_of,
            "fiscal_quarter": fq,
            "fiscal_year": fy,
            "period_end": period_end,
            "revenue_cr": _safe_float(r.get("revenue_quarterly_cr")),
            "net_profit_cr": _safe_float(r.get("net_profit_quarterly_cr")),
            "eps_reported": _safe_float(r.get("eps_quarterly")),
            "yoy_eps_prev": None,
            "yoy_revenue_prev": None,
            "result_type": "quarterly",
            "source_url": "tickertape:quarterly:approx_date",
        })

    return pd.DataFrame(rows, columns=_PARQUET_SCHEMA) if rows else _empty_df()


# ── Quality report ─────────────────────────────────────────────────────────────

def quality_report(df: pd.DataFrame) -> None:
    """Print a quick quality check of the merged parquet."""
    total = len(df)
    tt_patched = (df["source_url"].str.startswith("tickertape")).sum()
    eps_fill = df["eps_reported"].notna().mean()
    yoy_fill = df["yoy_eps_prev"].notna().mean()
    symbols = df["symbol"].nunique()

    print(f"\n=== Merged Parquet Quality ===")
    print(f"Total rows:           {total:,}")
    print(f"Symbols:              {symbols}")
    print(f"Tickertape-patched:   {tt_patched:,}  ({100*tt_patched/total:.1f}%)")
    print(f"eps_reported fill:    {eps_fill:.1%}")
    print(f"yoy_eps_prev fill:    {yoy_fill:.1%}")
    print(f"Date range:           {df['business_date'].min()} → {df['business_date'].max()}")

    # Check TTM vs quarterly: if Tickertape quarterly EPS << TTM EPS for same
    # (symbol, fq, fy), that's suspicious — might mean Tickertape exported annually
    tt_rows = df[df["source_url"].str.startswith("tickertape", na=False)]
    if not tt_rows.empty:
        abs_eps_med = tt_rows["eps_reported"].abs().median()
        print(f"Tickertape EPS median abs: {abs_eps_med:.2f} ₹")
        if abs_eps_med > 500:
            print("WARNING: Median EPS > 500 — may be annual EPS, not quarterly. "
                  "Verify Tickertape export is quarterly, not annual.")
    print()


# ── Probe mode ─────────────────────────────────────────────────────────────────

def probe_file(path: Path) -> None:
    """Print detected format + sample parsed rows without writing anything."""
    fmt = detect_format(path)
    print(f"\nFile: {path.name}")
    print(f"Detected format: {fmt}")

    try:
        df = pd.read_csv(path, nrows=20, thousands=",")
        print(f"Raw columns: {list(df.columns)}")
        print(f"Raw sample rows (first 5):")
        print(df.head().to_string())
    except Exception as exc:
        print(f"ERROR reading raw: {exc}")
        return

    symbol = _symbol_from_filename(path)
    print(f"\nParsing as symbol: {symbol}")

    if fmt == "wide_row":
        parsed = parse_wide_row_csv(path)
    elif fmt == "wide_col":
        parsed = parse_wide_col_csv(path, symbol)
    else:
        parsed = parse_long_csv(path, symbol)

    print(f"\nParsed {len(parsed)} quarter rows:")
    if not parsed.empty:
        print(parsed.to_string())
    else:
        print("  (no rows parsed — check column names above)")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Ingest Tickertape Pro quarterly EPS CSV exports",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input-dir", type=Path, metavar="DIR",
        help="Directory containing per-company quarterly CSVs (format A or C)",
    )
    group.add_argument(
        "--input-file", type=Path, metavar="FILE",
        help="Single bulk screener CSV (format B)",
    )
    group.add_argument(
        "--probe", type=Path, metavar="FILE",
        help="Probe format of a single CSV file (no writes)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and merge but do not write to parquet",
    )
    parser.add_argument(
        "--parquet", type=Path, default=None,
        help="Override parquet path (default: data/lake/earnings/nse_results.parquet)",
    )
    args = parser.parse_args()

    if args.probe:
        probe_file(args.probe)
        return

    # Parse Tickertape CSVs
    if args.input_dir:
        logger.info("Parsing per-company CSVs from %s", args.input_dir)
        tt_df = parse_directory(args.input_dir)
    else:
        logger.info("Parsing bulk screener CSV: %s", args.input_file)
        tt_df = parse_single_file(args.input_file)

    if tt_df.empty:
        logger.error("No data parsed from Tickertape CSVs — check input format")
        sys.exit(1)

    logger.info(
        "Parsed Tickertape data: %d rows, %d symbols, %d unique (symbol, fq, fy) keys",
        len(tt_df), tt_df["symbol"].nunique(),
        tt_df.drop_duplicates(["symbol", "fiscal_quarter", "fiscal_year"]).shape[0],
    )

    # Merge with existing parquet
    merged = merge_with_existing_parquet(tt_df, parquet_path=args.parquet)
    quality_report(merged)

    if args.dry_run:
        logger.info("Dry run — not writing parquet")
        return

    parquet_path = args.parquet or _parquet_path()
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(parquet_path, index=False)
    logger.info(
        "Written %d rows to %s (%d symbols, %d Tickertape-patched)",
        len(merged),
        parquet_path,
        merged["symbol"].nunique(),
        (merged["source_url"].str.startswith("tickertape", na=False)).sum(),
    )


if __name__ == "__main__":
    main()
