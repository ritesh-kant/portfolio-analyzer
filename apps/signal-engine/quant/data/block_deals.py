"""NSE Block Deal loader for Strategy I (Block Deal Momentum).

Block deals are pre-negotiated off-market transactions ≥ ₹10 crore executed at
the NSE block deal window (8:45–9:00 AM IST).  Unlike bulk deals, block deals are
ALWAYS institutional — algorithmic/HNI traders do not use the block deal window.

Storage
-------
  data/lake/block_deals/nse_block_deals.parquet

Schema (same as bulk_deals)
----------------------------
  symbol          str      NSE trading symbol (upper-cased)
  business_date   date     trade date
  client_name     str      buyer/seller entity name
  side            str      "BUY" or "SELL"
  quantity        int      shares traded
  price           float    trade price
  value_cr        float    approximate deal value in crore (qty × price / 1e7)
  as_of_timestamp datetime business_date at 09:30 IST (post block-window)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR  = _REPO_ROOT / "data" / "lake" / "block_deals"
_PARQUET   = _DATA_DIR / "nse_block_deals.parquet"


def load_block_deals(
    symbol: str | None = None,
    start: str | date | None = None,
    end: str | date | None = None,
    side: str | None = "BUY",
    min_value_cr: float = 0.0,
) -> pd.DataFrame:
    """Load block deal data from the parquet store.

    Parameters
    ----------
    symbol, start, end, side, min_value_cr : same semantics as bulk_deals.load_bulk_deals

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
    """Print a summary of block deal data on disk."""
    if not _PARQUET.exists():
        print("No block deal parquet found at", _PARQUET)
        return {"exists": False}

    df = pd.read_parquet(_PARQUET)
    df["business_date"] = pd.to_datetime(df["business_date"]).dt.date
    if start:
        df = df[df["business_date"] >= pd.Timestamp(start).date()]
    if end:
        df = df[df["business_date"] <= pd.Timestamp(end).date()]

    buy_df = df[df["side"] == "BUY"]
    print("=" * 60)
    print("NSE Block Deal Data Summary")
    print("=" * 60)
    print(f"  File         : {_PARQUET}")
    print(f"  Date range   : {df['business_date'].min()} → {df['business_date'].max()}")
    print(f"  Trading days : {df['business_date'].nunique()}")
    print(f"  Symbols      : {df['symbol'].nunique()}")
    print(f"  Total rows   : {len(df):,}")
    print(f"  BUY rows     : {len(buy_df):,}")
    print(f"  SELL rows    : {len(df) - len(buy_df):,}")
    if not buy_df.empty:
        print(f"  Avg BUY value: Rs{buy_df['value_cr'].mean():.1f} Cr/deal")
    print("=" * 60)
    return {"exists": True, "buy_rows": len(buy_df), "total_rows": len(df)}
