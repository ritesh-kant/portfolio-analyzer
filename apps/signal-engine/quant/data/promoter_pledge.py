"""Promoter pledge ingest and filter — Strategy C.

Strategy C is NOT a standalone strategy.  It is a defensive filter integrated
into all long strategies: any stock with a new pledge or pledge-increase by
promoters is excluded from long candidates for 60 trading days.

Mechanism
---------
Under SEBI SAST Regulations 2011, promoters must disclose when they pledge
shares as collateral for loans.  Pledging signals:
  - Promoter needs liquidity (cash crunch)
  - Pledged shares at risk of forced liquidation if loan margin is breached
  - Forced liquidation creates a predictable overhang / negative price pressure

Academic grounding:
  - Kaur & Singla (2020): Promoter pledging is significantly negatively associated
    with stock returns in India over 3–12 month horizons.
  - Gopalan, Nanda & Seru (2007): Pledging and tunneling are correlated in Indian
    business groups.
  - Shah & Thomas (2020): Pledge-increase events around earnings underperform by
    ~200–400 bps over 60 days.

Filter logic (pre-registered, immutable)
-----------------------------------------
For any (symbol, inference_date):
  1. Look up the most recent quarterly pledged % as of inference_date.
     (quarterly, not daily — SEBI requires quarterly disclosure by all promoters)
  2. Compare to the prior quarter's pledged %.
  3. If pledged_pct INCREASED (or went from 0 → positive), set a 60-trading-day
     exclusion flag starting from the filing_disclosure_date.
  4. If pledged_pct is > _ABSOLUTE_PLEDGE_THRESHOLD (15%) regardless of direction,
     also flag the stock (sustained high pledge = structural risk).
  5. Return True (flagged = exclude) if within any active exclusion window.

Exclusion effect:
  - Improves Sortino (avoids forced-liquidation tail events).
  - Minimal Sharpe impact (pledged stocks are a small fraction of universe).
  - Tested on Strategy A/B retrospectively after the filter is built.

Data source
-----------
Free: quarterly shareholding pattern CSV exported from screener.in
      (logged-in user: Company page → Shareholding → Download) or from
      Tickertape (Screener → add "Pledged %" column → Export).

Expected CSV format (screener.in bulk export):
    symbol,quarter_end,pledged_pct
    YESBANK,2024-09-30,0.00
    YESBANK,2024-06-30,0.00
    ADANIPORTS,2024-09-30,8.32

The `pledged_pct` column is "% of promoter holding pledged" — the standard
NSE/BSE quarterly SHP field "Shares pledged as % of promoter holding".

Parquet schema (written to data/lake/promoter_pledge/pledge_shp.parquet):
    symbol          : str
    quarter_end     : date       (period-end date of the quarterly SHP)
    filing_date     : date       (date the SHP was filed with NSE/BSE;
                                  defaults to quarter_end + 21 days if unknown)
    pledged_pct     : float      (% of promoter holding pledged; 0–100)
    as_of_timestamp : datetime   (PIT: filing_date 18:00 IST)

Usage
-----
  # Ingest a screener.in bulk export
  python -m quant.data.promoter_pledge \\
      --ingest path/to/pledge_export.csv

  # Validate stored data
  python -m quant.data.promoter_pledge --validate

  # Check filter status for a symbol on a date
  python -m quant.data.promoter_pledge --check ADANIPORTS 2024-04-01
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_DATA_DIR = Path(__file__).parents[2] / "data" / "lake" / "promoter_pledge"
_PARQUET = _DATA_DIR / "pledge_shp.parquet"

# Pre-registered, immutable filter parameters (§C.4 of hypothesis)
_EXCLUSION_TRADING_DAYS: int = 60          # trading-day exclusion window after signal
_ABSOLUTE_PLEDGE_THRESHOLD: float = 15.0   # % of promoter holding; always flag if above
_MIN_PLEDGE_INCREASE: float = 0.01         # minimum increase to trigger (avoids rounding noise)

# Default filing-date lag when only quarter_end is known
_FILING_LAG_DAYS: int = 21  # SEBI allows 21 calendar days after quarter end

# ── Public API ─────────────────────────────────────────────────────────────────


def load_pledge(
    symbol: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """Load stored pledge quarterly data.

    Parameters
    ----------
    symbol : str or None
        Filter to a specific NSE symbol.  None returns all symbols.
    start, end : str or None
        ISO date strings to filter by ``quarter_end``.

    Returns
    -------
    pd.DataFrame with columns:
        symbol, quarter_end (date), filing_date (date), pledged_pct (float),
        as_of_timestamp (datetime)
    Empty DataFrame (correct schema) if file not found.
    """
    if not _PARQUET.exists():
        return _empty_pledge_df()

    df = pd.read_parquet(_PARQUET)
    df["quarter_end"] = pd.to_datetime(df["quarter_end"]).dt.date
    df["filing_date"] = pd.to_datetime(df["filing_date"]).dt.date

    if symbol:
        df = df[df["symbol"] == symbol.upper()]
    if start:
        df = df[df["quarter_end"] >= pd.Timestamp(start).date()]
    if end:
        df = df[df["quarter_end"] <= pd.Timestamp(end).date()]

    return df.sort_values(["symbol", "quarter_end"]).reset_index(drop=True)


def is_pledge_flagged(symbol: str, as_of_date: str | date) -> bool:
    """Return True if ``symbol`` should be excluded on ``as_of_date``.

    A stock is flagged if, as of ``as_of_date``, any of these are true:
      1. The most recent quarter's pledged_pct > _ABSOLUTE_PLEDGE_THRESHOLD
      2. The most recent quarter's pledged_pct increased vs the prior quarter
         by at least _MIN_PLEDGE_INCREASE percentage points, and
         as_of_date is within _EXCLUSION_TRADING_DAYS trading days of the
         filing_date for that quarter.

    PIT-safe: uses ``filing_date`` (not quarter_end) as the information
    availability date.  filing_date defaults to quarter_end + 21 days if
    not explicitly known.

    Parameters
    ----------
    symbol : str
        NSE symbol (case-insensitive).
    as_of_date : str or date
        The inference date.  Must be ≤ today.

    Returns
    -------
    bool — True means exclude from long candidates.
    """
    as_of = pd.Timestamp(as_of_date).date() if isinstance(as_of_date, str) else as_of_date

    if not _PARQUET.exists():
        return False  # no data → don't filter (fail open on missing data)

    df = load_pledge(symbol=symbol)
    if df.empty:
        return False

    # Only look at quarters whose filing_date ≤ as_of_date (PIT)
    known = df[df["filing_date"] <= as_of].copy()
    if known.empty:
        return False

    # Sort ascending by quarter_end
    known = known.sort_values("quarter_end").reset_index(drop=True)
    latest = known.iloc[-1]

    # Rule 1: absolute threshold
    if latest["pledged_pct"] > _ABSOLUTE_PLEDGE_THRESHOLD:
        logger.debug(
            "Pledge flag (absolute): %s pledged_pct=%.2f%% > %.0f%% threshold on %s",
            symbol, latest["pledged_pct"], _ABSOLUTE_PLEDGE_THRESHOLD, as_of,
        )
        return True

    # Rule 2: increase-triggered exclusion window
    if len(known) >= 2:
        prior = known.iloc[-2]
        increase = latest["pledged_pct"] - prior["pledged_pct"]
        if increase >= _MIN_PLEDGE_INCREASE:
            # Check if we're within the exclusion window
            filing_date = latest["filing_date"]
            # Count trading days since filing (approx: 5/7 of calendar days)
            calendar_days_since = (as_of - filing_date).days
            approx_trading_days = int(calendar_days_since * 5 / 7)
            if approx_trading_days <= _EXCLUSION_TRADING_DAYS:
                logger.debug(
                    "Pledge flag (increase): %s +%.2f pp pledged_pct, "
                    "filing=%s, ~%d trading days ago (window=%d)",
                    symbol, increase, filing_date, approx_trading_days,
                    _EXCLUSION_TRADING_DAYS,
                )
                return True

    return False


def ingest_csv(csv_path: str | Path, overwrite: bool = False) -> int:
    """Parse a pledge CSV export and append/overwrite the parquet store.

    Supported CSV formats:

    A) Screener.in / Tickertape bulk export (one row per symbol per quarter):
        symbol,quarter_end,pledged_pct[,filing_date]

    B) NSE shareholding pattern quarterly bulk CSV (NSE bulk download format):
        SYMBOL,COMPANYNAME,ISIN,CATG,QTRID,QTRDTL,PROMOTER_TOTAL,PLEDGED_SHARES,
        TOTAL_SHARES,PLEDGED_PCT,...

    The function auto-detects format from column names.

    Parameters
    ----------
    csv_path : str or Path
    overwrite : bool
        If True, replace the parquet.  If False, merge (deduplicating by
        symbol + quarter_end).

    Returns
    -------
    int — number of new rows written.
    """
    path = Path(csv_path)
    raw = pd.read_csv(path, dtype=str)
    raw.columns = raw.columns.str.strip().str.lower().str.replace(" ", "_")

    # ── Format detection ───────────────────────────────────────────────────────
    cols = set(raw.columns)

    if "pledged_pct" in cols and "symbol" in cols and "quarter_end" in cols:
        df = _parse_screener_format(raw)
    elif "pledged_shares" in cols or "qtrid" in cols:
        df = _parse_nse_bulk_format(raw)
    else:
        # Flexible: try to find pledge %, symbol, and date columns
        df = _parse_flexible_format(raw)

    if df.empty:
        logger.warning("No valid rows parsed from %s", path)
        return 0

    # ── Merge with existing parquet ────────────────────────────────────────────
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not overwrite and _PARQUET.exists():
        existing = pd.read_parquet(_PARQUET)
        existing["quarter_end"] = pd.to_datetime(existing["quarter_end"]).dt.date
        combined = pd.concat([existing, df], ignore_index=True)
        # Dedup: keep latest row for each (symbol, quarter_end)
        combined = (
            combined
            .sort_values("as_of_timestamp")
            .drop_duplicates(subset=["symbol", "quarter_end"], keep="last")
            .reset_index(drop=True)
        )
    else:
        combined = df

    combined = combined.sort_values(["symbol", "quarter_end"]).reset_index(drop=True)
    new_rows = len(combined) - (len(pd.read_parquet(_PARQUET)) if _PARQUET.exists() and not overwrite else 0)

    combined.to_parquet(_PARQUET, index=False)
    logger.info("Pledge parquet written: %d total rows (%+d new)", len(combined), new_rows)
    return max(new_rows, 0)


def validate() -> dict:
    """Print and return a validation summary of the pledge store."""
    if not _PARQUET.exists():
        print("Pledge parquet not found:", _PARQUET)
        return {"exists": False}

    df = load_pledge()
    symbols = df["symbol"].nunique()
    quarters = df["quarter_end"].nunique()
    date_range = (df["quarter_end"].min(), df["quarter_end"].max())
    flagged = df[df["pledged_pct"] > _ABSOLUTE_PLEDGE_THRESHOLD]["symbol"].nunique()
    any_pledged = df[df["pledged_pct"] > 0]["symbol"].nunique()

    print("\n" + "=" * 60)
    print("Promoter Pledge Store — Validation Report")
    print("=" * 60)
    print(f"  File:          {_PARQUET}")
    print(f"  Rows:          {len(df)}")
    print(f"  Symbols:       {symbols}")
    print(f"  Quarters:      {quarters}")
    print(f"  Date range:    {date_range[0]} → {date_range[1]}")
    print(f"  Symbols > {_ABSOLUTE_PLEDGE_THRESHOLD:.0f}% pledge: {flagged}")
    print(f"  Symbols with any pledge: {any_pledged}")
    print("=" * 60 + "\n")

    return {
        "exists": True,
        "rows": len(df),
        "symbols": symbols,
        "quarters": quarters,
        "date_range": date_range,
        "high_pledge_symbols": flagged,
    }


# ── CSV format parsers ─────────────────────────────────────────────────────────

def _parse_screener_format(raw: pd.DataFrame) -> pd.DataFrame:
    """Parse screener.in / Tickertape bulk CSV.

    Required columns: symbol, quarter_end, pledged_pct
    Optional: filing_date
    """
    df = raw[["symbol", "quarter_end", "pledged_pct"]].copy()
    df["symbol"] = df["symbol"].str.upper().str.strip()
    df["quarter_end"] = pd.to_datetime(df["quarter_end"], dayfirst=False).dt.date
    df["pledged_pct"] = pd.to_numeric(df["pledged_pct"], errors="coerce").fillna(0.0)

    if "filing_date" in raw.columns:
        df["filing_date"] = pd.to_datetime(raw["filing_date"], dayfirst=False).dt.date
    else:
        df["filing_date"] = df["quarter_end"].apply(
            lambda d: d + timedelta(days=_FILING_LAG_DAYS)
        )

    df["as_of_timestamp"] = pd.to_datetime(df["filing_date"].astype(str)) + pd.Timedelta(hours=18)
    return df.dropna(subset=["symbol", "quarter_end"])


def _parse_nse_bulk_format(raw: pd.DataFrame) -> pd.DataFrame:
    """Parse NSE quarterly shareholding bulk CSV format.

    NSE publishes quarterly bulk downloads with columns:
    SYMBOL, COMPANYNAME, ISIN, CATG, QTRID (e.g. "Sep2024"),
    PROMOTER_TOTAL (promoter shares), PLEDGED_SHARES, TOTAL_SHARES
    """
    rows = []
    for _, r in raw.iterrows():
        symbol = str(r.get("symbol", r.get("scrip_symbol", ""))).upper().strip()
        if not symbol:
            continue

        # Parse quarter string: "Sep2024", "Q2FY25", "2024Q2", etc.
        qtrid = str(r.get("qtrid", r.get("quarter", ""))).strip()
        quarter_end = _parse_quarter_end(qtrid)
        if quarter_end is None:
            continue

        # Pledged shares as % of promoter holding
        pledged_shares = pd.to_numeric(r.get("pledged_shares", 0), errors="coerce") or 0.0
        promoter_total = pd.to_numeric(r.get("promoter_total", r.get("total_promoter_shares", 0)), errors="coerce") or 0.0

        if promoter_total > 0:
            pledged_pct = (pledged_shares / promoter_total) * 100.0
        elif "pledged_pct" in raw.columns:
            pledged_pct = pd.to_numeric(r.get("pledged_pct", 0), errors="coerce") or 0.0
        else:
            pledged_pct = 0.0

        filing_date = quarter_end + timedelta(days=_FILING_LAG_DAYS)
        rows.append({
            "symbol": symbol,
            "quarter_end": quarter_end,
            "filing_date": filing_date,
            "pledged_pct": round(pledged_pct, 4),
            "as_of_timestamp": pd.Timestamp(filing_date) + pd.Timedelta(hours=18),
        })

    return pd.DataFrame(rows) if rows else _empty_pledge_df()


def _parse_flexible_format(raw: pd.DataFrame) -> pd.DataFrame:
    """Attempt to parse any tabular pledge CSV by heuristic column matching.

    Looks for columns containing 'symbol'/'scrip', 'quarter'/'period'/'date',
    and 'pledge'.  Logs warnings for ambiguous matches.
    """
    col_map: dict[str, str] = {}

    for col in raw.columns:
        cl = col.lower()
        if "symbol" in cl or "scrip" in cl or "ticker" in cl:
            col_map.setdefault("symbol", col)
        elif "pledge" in cl and "pct" in cl:
            col_map["pledged_pct"] = col
        elif "pledge" in cl and "%" in col:
            col_map["pledged_pct"] = col
        elif "pledg" in cl and col_map.get("pledged_pct") is None:
            col_map["pledged_pct"] = col
        elif "quarter" in cl or "period" in cl or "qtr" in cl:
            col_map.setdefault("quarter_end", col)
        elif "date" in cl and "filing" in cl:
            col_map["filing_date"] = col

    missing = [k for k in ("symbol", "pledged_pct", "quarter_end") if k not in col_map]
    if missing:
        logger.error(
            "Cannot parse CSV: missing mappable columns for %s. "
            "Available columns: %s",
            missing, list(raw.columns),
        )
        return _empty_pledge_df()

    logger.info("Flexible column map: %s", col_map)

    df = raw[[col_map["symbol"], col_map["quarter_end"], col_map["pledged_pct"]]].copy()
    df.columns = ["symbol", "quarter_end", "pledged_pct"]
    df["symbol"] = df["symbol"].str.upper().str.strip()
    df["pledged_pct"] = pd.to_numeric(df["pledged_pct"], errors="coerce").fillna(0.0)

    # Parse quarter_end: try common formats
    df["quarter_end"] = pd.to_datetime(df["quarter_end"], dayfirst=False, errors="coerce").dt.date
    df = df.dropna(subset=["quarter_end"])

    if "filing_date" in col_map:
        df["filing_date"] = pd.to_datetime(raw[col_map["filing_date"]], dayfirst=False, errors="coerce").dt.date
    else:
        df["filing_date"] = df["quarter_end"].apply(
            lambda d: d + timedelta(days=_FILING_LAG_DAYS)
        )

    df["as_of_timestamp"] = pd.to_datetime(df["filing_date"].astype(str)) + pd.Timedelta(hours=18)
    return df


def _parse_quarter_end(qtrid: str) -> date | None:
    """Convert a quarter identifier string to the quarter-end date.

    Examples: "Sep2024" → 2024-09-30, "Q2FY25" → 2024-09-30,
              "2024-09-30" → 2024-09-30, "Jun 2024" → 2024-06-30
    """
    if not qtrid or qtrid.lower() in ("nan", "none", ""):
        return None

    qtrid = qtrid.strip()

    # MonYYYY → quarter-end  (try BEFORE ISO parse; "Sep2024" is valid ISO → Sep 1 2024)
    # Must be tried first or pandas will return the 1st of the month.
    _MONTH_END = {
        "jan": (1, 31), "feb": (2, 28), "mar": (3, 31), "apr": (4, 30),
        "may": (5, 31), "jun": (6, 30), "jul": (7, 31), "aug": (8, 31),
        "sep": (9, 30), "oct": (10, 31), "nov": (11, 30), "dec": (12, 31),
    }
    # NSE quarter-ends: Mar, Jun, Sep, Dec
    _QUARTER_END_MONTHS = {3: 31, 6: 30, 9: 30, 12: 31}

    import re

    # QxFYyy → quarter-end (try before MonYYYY to avoid regex ambiguity)
    m2 = re.match(r"Q([1-4])\s*FY\s*(\d{2,4})", qtrid, re.IGNORECASE)
    if m2:
        quarter = int(m2.group(1))
        fy_end = int(m2.group(2))
        if fy_end < 100:
            fy_end += 2000
        # Indian FY ends Mar; Q1=Apr-Jun, Q2=Jul-Sep, Q3=Oct-Dec, Q4=Jan-Mar
        fy_start = fy_end - 1
        qmap = {1: (fy_start, 6, 30), 2: (fy_start, 9, 30), 3: (fy_start, 12, 31), 4: (fy_end, 3, 31)}
        yr, mo, day = qmap[quarter]
        return date(yr, mo, day)

    # MonYYYY / Mon YYYY / Mon'YY  (e.g. "Sep2024", "Mar 2024", "Jun'24")
    m = re.match(r"([A-Za-z]+)\s*['\"]?(\d{2,4})$", qtrid)
    if m:
        mon_str = m.group(1).lower()[:3]
        year_str = m.group(2)
        year = int(year_str) if len(year_str) == 4 else 2000 + int(year_str)
        if mon_str in _MONTH_END:
            month, day = _MONTH_END[mon_str]
            # Adjust Feb for leap years
            if month == 2 and year % 4 == 0:
                day = 29
            try:
                return date(year, month, day)
            except Exception:
                pass

    # Direct ISO date (last resort — catches "2024-09-30", "2024/09/30")
    try:
        return pd.Timestamp(qtrid).date()
    except Exception:
        pass

    logger.warning("Cannot parse quarter string: %r", qtrid)
    return None


def _empty_pledge_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["symbol", "quarter_end", "filing_date", "pledged_pct", "as_of_timestamp"])


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Promoter pledge ingest and filter (Strategy C)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ingest", metavar="CSV_PATH", help="Ingest a pledge CSV export")
    group.add_argument("--validate", action="store_true", help="Validate stored pledge data")
    group.add_argument("--check", nargs=2, metavar=("SYMBOL", "DATE"),
                       help="Check if SYMBOL is pledge-flagged on DATE")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite parquet instead of merging (--ingest only)")
    args = parser.parse_args()

    if args.ingest:
        n = ingest_csv(args.ingest, overwrite=args.overwrite)
        print(f"Ingested {n} new rows from {args.ingest}")

    elif args.validate:
        validate()

    elif args.check:
        symbol, as_of = args.check
        flagged = is_pledge_flagged(symbol, as_of)
        status = "FLAGGED (exclude)" if flagged else "CLEAR (allow)"
        print(f"{symbol} on {as_of}: {status}")

        # Show recent pledge history for context
        df = load_pledge(symbol=symbol)
        if not df.empty:
            print("\nRecent pledge history:")
            print(df[["quarter_end", "filing_date", "pledged_pct"]].tail(8).to_string(index=False))


if __name__ == "__main__":
    main()
