"""Point-in-time loader for the L1 data lake.

Public API
----------
load(symbol, start, end, snapshot_at=None, series="EQ") -> pd.DataFrame

Parameters
----------
symbol      : str | list[str] | None
    NSE symbol ("RELIANCE"), list of symbols, or None for the full
    universe across the date range.
start       : str  — "YYYY-MM-DD", first business_date to include.
end         : str  — "YYYY-MM-DD", last business_date to include.
snapshot_at : str | datetime | None
    ISO datetime (e.g. "2023-06-30T12:30:00Z") or datetime object.
    When set, only rows with as_of_timestamp <= snapshot_at are returned.
    This is the PIT filter — always set it to the inference_date when
    building features to prevent lookahead bias.
    When None, all rows in the date range are returned (useful for bulk
    backfills that are known to be already PIT-correct).
series      : str | None
    NSE series filter. Default "EQ". Pass None to include all series
    (EQ, BE, BL, SM, ST, …).

Returns
-------
pd.DataFrame indexed by (business_date, symbol), columns:
    open, high, low, close, prev_close, volume, turnover_lacs,
    series, as_of_timestamp.

The critical guarantee
----------------------
With snapshot_at set, this function CANNOT return data that was
unavailable at snapshot_at. The as_of_timestamp column (stamped by
ingest.py at 18:00 IST = 12:30 UTC on each business_date) enforces this.

See ~/.claude/plans/based-on-the-full-harmonic-gosling.md §4.3.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_EMPTY_COLS = [
    "symbol", "series", "business_date", "open", "high", "low",
    "close", "prev_close", "volume", "turnover_lacs", "as_of_timestamp",
]
_INDEX_COLS = ["business_date", "symbol"]


def _lake_dir() -> Path:
    base = os.environ.get("QUANT_DATA_DIR", "data/lake")
    return Path(base) / "nse_bhavcopy"


def _year_path(year: int) -> Path:
    return _lake_dir() / f"{year}.parquet"


def _parse_snapshot_at(snapshot_at: str | datetime | None) -> pd.Timestamp | None:
    if snapshot_at is None:
        return None
    if isinstance(snapshot_at, str):
        ts = pd.Timestamp(snapshot_at)
        return ts if ts.tzinfo is not None else ts.tz_localize("UTC")
    if isinstance(snapshot_at, datetime):
        return pd.Timestamp(snapshot_at, tz="UTC") if snapshot_at.tzinfo is None else pd.Timestamp(snapshot_at)
    raise TypeError(f"snapshot_at must be str or datetime, got {type(snapshot_at)}")


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=_EMPTY_COLS).set_index(_INDEX_COLS)


def load(
    symbol: str | list[str] | None,
    start: str | date,
    end: str | date,
    snapshot_at: str | datetime | None = None,
    *,
    series: str | None = "EQ",
) -> pd.DataFrame:
    """Return PIT-correct OHLCV rows from the L1 Bhavcopy lake.

    With snapshot_at provided, no row whose as_of_timestamp > snapshot_at
    is ever returned — this is the single guarantee that eliminates lookahead.
    """
    if isinstance(start, str):
        start = date.fromisoformat(start)
    if isinstance(end, str):
        end = date.fromisoformat(end)

    snap_ts = _parse_snapshot_at(snapshot_at)

    sym_filter: set[str] | None = None
    if symbol is not None:
        sym_filter = {symbol} if isinstance(symbol, str) else set(symbol)

    frames: list[pd.DataFrame] = []
    for year in range(start.year, end.year + 1):
        path = _year_path(year)
        if not path.exists():
            logger.warning("pit_loader missing_year year=%d expected=%s", year, path)
            continue
        df = pd.read_parquet(path)
        if df.empty:
            continue
        # Ensure business_date is Python date for comparison
        df["business_date"] = pd.to_datetime(df["business_date"]).dt.date
        mask = (df["business_date"] >= start) & (df["business_date"] <= end)
        df = df[mask]
        if not df.empty:
            frames.append(df)

    if not frames:
        return _empty()

    result = pd.concat(frames, ignore_index=True)

    # ── PIT filter — the core correctness guarantee ───────────────────────────
    if snap_ts is not None:
        if result["as_of_timestamp"].dt.tz is None:
            result["as_of_timestamp"] = result["as_of_timestamp"].dt.tz_localize("UTC")
        result = result[result["as_of_timestamp"] <= snap_ts]

    # ── Series filter ─────────────────────────────────────────────────────────
    if series is not None:
        result = result[result["series"] == series]

    # ── Symbol filter ─────────────────────────────────────────────────────────
    if sym_filter is not None:
        result = result[result["symbol"].isin(sym_filter)]

    if result.empty:
        return _empty()

    return (
        result
        .sort_values(_INDEX_COLS)
        .set_index(_INDEX_COLS)
    )
