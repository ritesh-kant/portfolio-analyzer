"""Patch the NSE earnings parquet with actual quarterly EPS from screener.in HTML.

The existing parquet (built by screener_earnings.py) uses the chart API, which
returns TTM (trailing twelve months) EPS.  screener.in's company page also has
a quarterly table that shows individual-quarter EPS for the last ~13 quarters.
This script re-scrapes that table for every midcap 150 symbol and patches
eps_reported / revenue_cr in the parquet for the covered quarters.

Coverage: last ~13 quarters from today (≈ Q4 FY2023 onward), which fully covers
the dev period (2023-07-01 → 2024-06-30) and two years of the training tail.

Usage
-----
  cd apps/signal-engine

  # Dry run — show quality report, do not write
  python -m quant.data.screener_quarterly_patch --dry-run

  # Write patched parquet (backs up original first)
  python -m quant.data.screener_quarterly_patch

  # Limit to first N symbols (for testing)
  python -m quant.data.screener_quarterly_patch --max-symbols 10 --dry-run

Rate limiting: 1.5 s / symbol (2 requests each).  Full 150-symbol run ≈ 7 min.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import time
from pathlib import Path

import pandas as pd
import requests

from quant.data.earnings_ingest import _parquet_path, compute_yoy_columns
from quant.data.screener_earnings import (
    _HEADERS,
    _approx_fiscal_quarter_from_period_end,
    _parse_number,
    fetch_quarterly_html,
)

logger = logging.getLogger(__name__)

_RATE_LIMIT_SECS = 1.5
_CONSOLIDATED_BASE = "https://www.screener.in"


def _patch_parquet_with_quarterly(
    parquet_path: Path,
    symbols: list[str],
    dry_run: bool = False,
) -> pd.DataFrame:
    """Load parquet, overlay quarterly HTML data for each symbol, return patched df."""
    df = pd.read_parquet(parquet_path)
    logger.info("Loaded parquet: %d rows, %d symbols", len(df), df["symbol"].nunique())

    session = requests.Session()
    session.headers.update(_HEADERS)

    total_patched = 0
    failed = 0

    for i, sym in enumerate(symbols):
        logger.info("[%d/%d] %s", i + 1, len(symbols), sym)
        time.sleep(_RATE_LIMIT_SECS)

        try:
            html_df = fetch_quarterly_html(sym, session)
        except Exception as exc:
            logger.warning("  %s: fetch failed — %s", sym, exc)
            failed += 1
            continue

        if html_df.empty:
            logger.info("  %s: no quarterly HTML data", sym)
            continue

        patched_this = _apply_overlay(df, sym, html_df)
        total_patched += patched_this
        logger.info("  %s: patched %d rows", sym, patched_this)

    logger.info("Overlay complete: %d rows patched across %d symbols (%d symbols failed)",
                total_patched, len(symbols), failed)

    # Recompute yoy_eps_prev using the new quarterly values.
    # compute_yoy_columns looks up (symbol, fiscal_quarter, fiscal_year - 1) to find
    # the prior-year same-quarter EPS — now these will be quarterly-vs-quarterly.
    df = compute_yoy_columns(df)

    return df


def _apply_overlay(
    df: pd.DataFrame,
    symbol: str,
    html_df: pd.DataFrame,
) -> int:
    """Patch df in-place for one symbol.  Returns number of rows patched."""
    patched = 0
    for _, hrow in html_df.iterrows():
        period_label = hrow.get("period_label", "")
        if not period_label:
            continue
        try:
            # period_label is like "Sep 2023" — prepend "01 " to parse
            period_ts = pd.Timestamp("01 " + str(period_label))
        except Exception:
            continue

        fq, fy = _approx_fiscal_quarter_from_period_end(period_ts)
        mask = (
            (df["symbol"] == symbol)
            & (pd.to_numeric(df["fiscal_quarter"], errors="coerce") == fq)
            & (pd.to_numeric(df["fiscal_year"], errors="coerce") == fy)
        )
        if not mask.any():
            continue

        eps_val = _parse_number(hrow.get("eps_reported"))
        rev_val = _parse_number(hrow.get("sales_cr"))
        np_val = _parse_number(hrow.get("net_profit_cr"))

        if eps_val is not None:
            df.loc[mask, "eps_reported"] = eps_val
            df.loc[mask, "source_url"] = f"screener:{symbol}:quarterly_html"
            patched += mask.sum()
        if rev_val is not None:
            df.loc[mask, "revenue_cr"] = rev_val
        if np_val is not None:
            df.loc[mask, "net_profit_cr"] = np_val

    return patched


def quality_report(df: pd.DataFrame) -> None:
    total = len(df)
    q_patched = df["source_url"].str.contains("quarterly_html", na=False).sum()
    ttm_rows = df["source_url"].str.contains("ttm_eps", na=False).sum()
    eps_fill = df["eps_reported"].notna().mean()
    yoy_fill = df["yoy_eps_prev"].notna().mean()

    print("\n=== Quarterly Patch Quality ===")
    print(f"Total rows:             {total:,}")
    print(f"Quarterly HTML patched: {q_patched:,}  ({100*q_patched/total:.1f}%)")
    print(f"Remaining TTM rows:     {ttm_rows:,}  ({100*ttm_rows/total:.1f}%)")
    print(f"eps_reported fill rate: {eps_fill:.1%}")
    print(f"yoy_eps_prev fill rate: {yoy_fill:.1%}")

    # Dev period check
    dev = df[
        (pd.to_datetime(df["business_date"]) >= pd.Timestamp("2023-07-01"))
        & (pd.to_datetime(df["business_date"]) <= pd.Timestamp("2024-06-30"))
    ]
    if not dev.empty:
        dev_q = dev["source_url"].str.contains("quarterly_html", na=False).sum()
        print(f"\nDev period (2023-07 → 2024-06):")
        print(f"  Rows:                {len(dev):,}")
        print(f"  Quarterly patched:   {dev_q:,}  ({100*dev_q/len(dev):.1f}%)")
        print(f"  yoy fill rate:       {dev['yoy_eps_prev'].notna().mean():.1%}")
    print()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Patch NSE earnings parquet with screener.in quarterly EPS"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and report but do not overwrite parquet")
    parser.add_argument("--max-symbols", type=int, default=None,
                        help="Limit to first N symbols (for testing)")
    parser.add_argument("--parquet", type=Path, default=None,
                        help="Override parquet path")
    parser.add_argument("--universe", type=Path,
                        default=Path("data/lake/midcap150_constituents.csv"),
                        help="Universe CSV file (must have 'symbol' column)")
    args = parser.parse_args()

    parquet_path = args.parquet or _parquet_path()
    if not parquet_path.exists():
        logger.error("Parquet not found: %s — run screener_earnings ingest first", parquet_path)
        raise SystemExit(1)

    if not args.universe.exists():
        logger.error("Universe file not found: %s", args.universe)
        raise SystemExit(1)

    symbols = pd.read_csv(args.universe)["symbol"].str.upper().tolist()
    if args.max_symbols:
        symbols = symbols[:args.max_symbols]

    logger.info("Patching %d symbols with screener quarterly HTML", len(symbols))

    patched_df = _patch_parquet_with_quarterly(parquet_path, symbols, dry_run=args.dry_run)
    quality_report(patched_df)

    if args.dry_run:
        logger.info("Dry run — parquet not modified")
        return

    # Backup original before overwriting
    backup_path = parquet_path.with_suffix(".parquet.bak")
    shutil.copy2(parquet_path, backup_path)
    logger.info("Backed up original parquet to %s", backup_path)

    patched_df.to_parquet(parquet_path, index=False)
    logger.info("Written %d rows to %s", len(patched_df), parquet_path)

    q_count = patched_df["source_url"].str.contains("quarterly_html", na=False).sum()
    logger.info("Quarterly-patched rows: %d / %d (%.1f%%)",
                q_count, len(patched_df), 100 * q_count / len(patched_df))


if __name__ == "__main__":
    main()
