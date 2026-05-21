"""Loader and validator for NSE index reconstitution events.

Data file: data/lake/index_changes/nse_recon_events.csv

Schema (one row per event):
    announcement_date  YYYY-MM-DD — date NSE/IISL public press release
    effective_date     YYYY-MM-DD — date change takes effect
    index_name         "NIFTY 50" | "NIFTY NEXT 50" | "NIFTY MIDCAP 150"
    event_type         "inclusion" | "exclusion"
    symbol             NSE EQ series symbol

Sources for manual data entry:
  - NSE Indices press releases: https://www.niftyindices.com/indices/equity
  - NSE circular archive:       https://www.nseindia.com/regulations/circulars
  - IISL index announcements go back to at least 2010 for Nifty 50;
    Nifty Midcap 150 launched 2016-04-01.

Coverage target: 2015-01-01 → 2024-06-30 (train + dev), ≥ 200 events.

CLI:
  python -m quant.data.nse_index_changes --validate
  python -m quant.data.nse_index_changes --validate --start 2023-07-01 --end 2024-06-30
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_VALID_INDICES = {"NIFTY 50", "NIFTY NEXT 50", "NIFTY MIDCAP 150"}
_VALID_EVENT_TYPES = {"inclusion", "exclusion"}
_REQUIRED_COLS = {"announcement_date", "effective_date", "index_name", "event_type", "symbol"}


def _data_path() -> Path:
    base = os.environ.get("QUANT_DATA_DIR", "data/lake")
    return Path(base) / "index_changes" / "nse_recon_events.csv"


def load_events(
    start: str | None = None,
    end: str | None = None,
    event_type: str | None = "inclusion",
    indices: list[str] | None = None,
) -> pd.DataFrame:
    """Load NSE index reconstitution events.

    Parameters
    ----------
    start : str | None
        ISO date — filter announcement_date >= start.
    end : str | None
        ISO date — filter announcement_date <= end.
    event_type : str | None
        "inclusion", "exclusion", or None for both.
    indices : list[str] | None
        Subset of _VALID_INDICES to return.  None returns all three.

    Returns
    -------
    pd.DataFrame with columns:
        announcement_date (datetime64), effective_date (datetime64),
        index_name, event_type, symbol
    """
    path = _data_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Recon events file not found: {path}\n"
            "Assemble it from NSE Indices press releases — see module docstring."
        )

    df = pd.read_csv(path, parse_dates=["announcement_date", "effective_date"])

    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"nse_recon_events.csv is missing columns: {missing}")

    if df.empty:
        return df

    if start:
        df = df[df["announcement_date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["announcement_date"] <= pd.Timestamp(end)]
    if event_type:
        df = df[df["event_type"] == event_type]
    if indices:
        df = df[df["index_name"].isin(indices)]

    df = df.sort_values("announcement_date").reset_index(drop=True)
    return df


def validate_events(
    ohlcv: pd.DataFrame | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Validate the recon events CSV for coverage and data integrity.

    Parameters
    ----------
    ohlcv : pd.DataFrame | None
        OHLCV data from pit_loader (indexed by business_date, symbol).
        When provided, checks that every event has matching OHLCV rows
        within ±5 trading days of both announcement_date and effective_date.
    start, end : str | None
        Date range to restrict validation to.

    Returns
    -------
    dict with keys:
        n_events, date_range, missing_ohlcv, index_counts,
        bad_rows (list of (index, reason)), valid (bool)
    """
    path = _data_path()
    result: dict = {
        "n_events": 0,
        "date_range": None,
        "missing_ohlcv": [],
        "index_counts": {},
        "bad_rows": [],
        "valid": False,
    }

    if not path.exists():
        result["bad_rows"].append((-1, f"file not found: {path}"))
        return result

    all_events = load_events(start=start, end=end, event_type=None)

    if all_events.empty:
        result["bad_rows"].append((-1, "no events in date range — populate nse_recon_events.csv"))
        return result

    result["n_events"] = len(all_events)
    result["date_range"] = (
        str(all_events["announcement_date"].min().date()),
        str(all_events["announcement_date"].max().date()),
    )
    result["index_counts"] = all_events["index_name"].value_counts().to_dict()

    for i, row in all_events.iterrows():
        if row["index_name"] not in _VALID_INDICES:
            result["bad_rows"].append((i, f"unknown index_name: {row['index_name']!r}"))
        if row["event_type"] not in _VALID_EVENT_TYPES:
            result["bad_rows"].append((i, f"unknown event_type: {row['event_type']!r}"))
        if pd.isna(row["announcement_date"]) or pd.isna(row["effective_date"]):
            result["bad_rows"].append((i, "missing announcement_date or effective_date"))
        elif row["effective_date"] <= row["announcement_date"]:
            result["bad_rows"].append(
                (i, f"effective_date {row['effective_date'].date()} <= announcement_date {row['announcement_date'].date()}")
            )
        if not isinstance(row["symbol"], str) or len(row["symbol"].strip()) == 0:
            result["bad_rows"].append((i, "empty symbol"))

    if ohlcv is not None and not ohlcv.empty:
        ohlcv_idx = ohlcv.index  # MultiIndex(business_date, symbol)
        all_biz_dates = ohlcv_idx.get_level_values("business_date")

        for _, row in all_events.iterrows():
            sym = str(row["symbol"]).upper()

            # Check T+1 open exists (within +5 trading days of announcement)
            ann_ts = row["announcement_date"]
            future_dates = sorted(d for d in all_biz_dates.unique() if d > ann_ts)
            if not future_dates:
                result["missing_ohlcv"].append((sym, str(ann_ts.date()), "no T+1 trading day"))
                continue
            entry_date = future_dates[0]
            if (sym, entry_date) not in ohlcv_idx or (entry_date, sym) not in ohlcv_idx:
                try:
                    _ = ohlcv.loc[(entry_date, sym), "open"]
                except KeyError:
                    result["missing_ohlcv"].append(
                        (sym, str(ann_ts.date()), f"no OHLCV on T+1 {entry_date.date()}")
                    )

            # Check effective_date close exists
            eff_ts = pd.Timestamp(row["effective_date"])
            try:
                _ = ohlcv.loc[(eff_ts, sym), "close"]
            except KeyError:
                result["missing_ohlcv"].append(
                    (sym, str(ann_ts.date()), f"no OHLCV on effective_date {eff_ts.date()}")
                )

    result["valid"] = len(result["bad_rows"]) == 0
    return result


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Validate NSE index reconstitution events CSV")
    parser.add_argument("--validate", action="store_true", default=True)
    parser.add_argument("--start", default=None, help="Filter start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="Filter end date (YYYY-MM-DD)")
    parser.add_argument(
        "--with-ohlcv",
        action="store_true",
        help="Also check OHLCV coverage for every event (slow, loads all bhavcopy)",
    )
    args = parser.parse_args()

    ohlcv = None
    if args.with_ohlcv:
        from quant.data.pit_loader import load as pit_load
        start = args.start or "2015-01-01"
        end = args.end or "2024-06-30"
        logger.info("Loading OHLCV %s → %s for coverage check ...", start, end)
        ohlcv = pit_load(symbol=None, start=start, end=end)

    result = validate_events(ohlcv=ohlcv, start=args.start, end=args.end)

    print("\n" + "=" * 60)
    print("NSE Recon Events — Validation Report")
    print("=" * 60)
    print(f"  File:          {_data_path()}")
    print(f"  Events:        {result['n_events']}")
    print(f"  Date range:    {result['date_range']}")
    print(f"  Index counts:  {result['index_counts']}")

    if result["bad_rows"]:
        print(f"\n  BAD ROWS ({len(result['bad_rows'])}):")
        for idx, reason in result["bad_rows"][:20]:
            print(f"    row {idx}: {reason}")
        if len(result["bad_rows"]) > 20:
            print(f"    ... and {len(result['bad_rows']) - 20} more")
    else:
        print("\n  Schema: OK")

    if result["missing_ohlcv"]:
        print(f"\n  MISSING OHLCV ({len(result['missing_ohlcv'])}):")
        for sym, ann, reason in result["missing_ohlcv"][:10]:
            print(f"    {sym} ann={ann}: {reason}")
    elif ohlcv is not None:
        print("  OHLCV coverage: OK")

    if result["n_events"] == 0:
        print("\nNEXT STEP: populate nse_recon_events.csv from NSE Indices press releases.")
        print("  Sources:")
        print("    https://www.niftyindices.com/indices/equity  (press releases tab)")
        print("    https://www.nseindia.com/regulations/circulars")
        print("  Target: ≥ 200 events, 2015-01-01 → 2024-06-30")
    elif result["n_events"] < 15:
        print(f"\nWARNING: only {result['n_events']} events — dev gate requires ≥ 15.")

    print("=" * 60 + "\n")

    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
