"""
Ingest annual P&L from Screener.in for all Midcap 150 symbols.

Outputs:
  data/lake/earnings/screener_annual.parquet
  Columns: symbol, fiscal_year (e.g. 2015), sales_cr, net_profit_cr, eps, opm_pct

Free, no auth required. Scrapes the annual P&L table (Table index 1) from
each company's consolidated page on Screener.in.

Rate-limiting: 1-second delay between requests to stay polite.

Usage:
    python scripts/ingest_screener_annual.py
    python scripts/ingest_screener_annual.py --limit 5   # dry run on 5 symbols
    python scripts/ingest_screener_annual.py --resume    # skip already-fetched symbols
"""

import argparse
import logging
import os
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
CONSTITUENTS = BASE_DIR / "data" / "lake" / "midcap150_constituents.csv"
OUT_PATH = BASE_DIR / "data" / "lake" / "earnings" / "screener_annual.parquet"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Screener.in annual P&L is Table index 1 on the consolidated page.
# Rows we care about (Screener labels):
ROW_MAP = {
    "Sales +": "sales_cr",
    "Sales":   "sales_cr",
    "Net Profit +": "net_profit_cr",
    "Net Profit": "net_profit_cr",
    "EPS in Rs": "eps",
    "OPM %": "opm_pct",
}


def fetch_annual(symbol: str, session: requests.Session) -> pd.DataFrame:
    """Return annual P&L DataFrame for one symbol, or empty DataFrame on failure."""
    url = f"https://www.screener.in/company/{symbol}/consolidated/"
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code == 404:
            # Try standalone (some companies have no consolidated view)
            resp = session.get(
                f"https://www.screener.in/company/{symbol}/", timeout=15
            )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("%s: HTTP error — %s", symbol, e)
        return pd.DataFrame()

    try:
        tables = pd.read_html(StringIO(resp.text))
    except ValueError:
        log.warning("%s: no tables found", symbol)
        return pd.DataFrame()

    # Annual P&L table: has columns named "Mar YYYY" and rows like "Sales +", "Net Profit +"
    annual_table = None
    for t in tables:
        cols = list(t.columns)
        # Must have at least one column matching "Mar 20XX" and go back to ≤2017
        year_cols = [c for c in cols if str(c).startswith("Mar 20")]
        years = [int(str(c).split()[1]) for c in year_cols if str(c).split()[1].isdigit()]
        if years and min(years) <= 2017 and len(year_cols) >= 5:
            # Verify it has P&L rows (normalize \xa0 non-breaking spaces)
            first_col = " ".join(str(v).replace("\xa0", " ") for v in t.iloc[:, 0].tolist())
            if "Sales" in first_col or "Net Profit" in first_col:
                annual_table = t
                break

    if annual_table is None:
        # Consolidated page may exist but lack P&L (standalone-only companies) — retry
        log.debug("%s: consolidated had no P&L table, trying standalone", symbol)
        try:
            resp2 = session.get(f"https://www.screener.in/company/{symbol}/", timeout=15)
            resp2.raise_for_status()
            tables2 = pd.read_html(StringIO(resp2.text))
        except Exception:
            tables2 = []
        for t in tables2:
            cols = list(t.columns)
            year_cols = [c for c in cols if str(c).startswith("Mar 20")]
            years = [int(str(c).split()[1]) for c in year_cols if str(c).split()[1].isdigit()]
            if years and min(years) <= 2017 and len(year_cols) >= 5:
                first_col = " ".join(str(v).replace("\xa0", " ") for v in t.iloc[:, 0].tolist())
                if "Sales" in first_col or "Net Profit" in first_col:
                    annual_table = t
                    break

    if annual_table is None:
        log.warning("%s: annual P&L table not found in consolidated or standalone", symbol)
        return pd.DataFrame()

    # Extract year columns and map rows
    year_cols = [c for c in annual_table.columns if str(c).startswith("Mar 20")]
    rows = {}
    for _, row in annual_table.iterrows():
        label = str(row.iloc[0]).strip().replace("\xa0", " ")
        if label in ROW_MAP:
            field = ROW_MAP[label]
            rows[field] = row

    if not rows:
        log.warning("%s: no matching rows in annual table", symbol)
        return pd.DataFrame()

    records = []
    for col in year_cols:
        year = int(str(col).split()[1])
        rec = {"symbol": symbol, "fiscal_year": year}
        for field, row in rows.items():
            val = row[col]
            if isinstance(val, str):
                # Strip % and commas
                val = val.replace("%", "").replace(",", "").strip()
                try:
                    val = float(val)
                except ValueError:
                    val = None
            rec[field] = val
        records.append(rec)

    df = pd.DataFrame(records)
    log.info("%s: %d years (%d–%d)", symbol, len(df), df.fiscal_year.min(), df.fiscal_year.max())
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only process N symbols (dry run)")
    parser.add_argument("--resume", action="store_true", help="Skip symbols already in output file")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between requests")
    args = parser.parse_args()

    constituents = pd.read_csv(CONSTITUENTS)
    symbols = constituents["symbol"].tolist()

    if args.resume and OUT_PATH.exists():
        existing = pd.read_parquet(OUT_PATH)
        done = set(existing["symbol"].unique())
        symbols = [s for s in symbols if s not in done]
        log.info("Resume: %d symbols remaining (skipping %d already fetched)", len(symbols), len(done))
    else:
        existing = pd.DataFrame()

    if args.limit:
        symbols = symbols[: args.limit]
        log.info("Dry run: processing %d symbols", len(symbols))

    session = requests.Session()
    session.headers.update(HEADERS)

    all_frames = [existing] if not existing.empty else []
    failed = []

    for i, sym in enumerate(symbols, 1):
        log.info("[%d/%d] %s", i, len(symbols), sym)
        df = fetch_annual(sym, session)
        if df.empty:
            failed.append(sym)
        else:
            all_frames.append(df)

        # Checkpoint every 25 symbols
        if all_frames and i % 25 == 0:
            combined = pd.concat(all_frames, ignore_index=True)
            OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            combined.to_parquet(OUT_PATH, index=False)
            log.info("Checkpoint saved: %d rows for %d symbols", len(combined), combined["symbol"].nunique())

        time.sleep(args.delay)

    if all_frames:
        combined = pd.concat(all_frames, ignore_index=True)
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        combined.to_parquet(OUT_PATH, index=False)
        log.info("Done. Saved %d rows for %d symbols → %s", len(combined), combined["symbol"].nunique(), OUT_PATH)
    else:
        log.error("No data retrieved for any symbol.")

    if failed:
        log.warning("Failed symbols (%d): %s", len(failed), ", ".join(failed))

    # Quick depth summary
    if all_frames:
        combined = pd.concat(all_frames, ignore_index=True)
        print("\n=== Depth Summary ===")
        print(f"Symbols:        {combined['symbol'].nunique()}")
        print(f"Year range:     {combined['fiscal_year'].min()} – {combined['fiscal_year'].max()}")
        print(f"Total rows:     {len(combined)}")
        coverage = combined.groupby("fiscal_year")["symbol"].count()
        print("\nSymbols per fiscal year:")
        print(coverage.to_string())


if __name__ == "__main__":
    main()
