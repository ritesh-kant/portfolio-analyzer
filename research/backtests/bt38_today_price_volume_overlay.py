"""Trade-level replay of the experimental four-bar price/volume entry overlay.

This does not reconstruct replacement trades that might appear after a recorded
entry is refused.  It answers the narrower, auditable question: which positions
the live paper ledger actually opened would the extra entry filter have kept?

Example:
  apps/signal-engine/.venv/bin/python \
    research/backtests/bt38_today_price_volume_overlay.py --date 2026-09-18
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

from src.momentum_trader.indicators import price_volume_slopes  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def _mongo_uri() -> str:
    uri = os.getenv("MONGODB_URI")
    if uri:
        return uri
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("MONGODB_URI="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("MONGODB_URI is unavailable")


def _regime(price_slope: float, volume_slope: float) -> str:
    price = "up_price" if price_slope > 0.0 else "down_price"
    volume = "up_volume" if volume_slope > 0.0 else "down_volume"
    return f"{price}_{volume}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="NSE session date, YYYY-MM-DD")
    parser.add_argument("--lookback", type=int, default=4)
    parser.add_argument("--strategy", default="", help="optional exact ledger strategy")
    args = parser.parse_args()

    session = datetime.fromisoformat(args.date).replace(tzinfo=IST)
    query: dict[str, object] = {
        "status": "closed",
        "entry_time": {
            "$gte": session.astimezone(UTC),
            "$lt": (session + timedelta(days=1)).astimezone(UTC),
        },
    }
    if args.strategy:
        query["strategy"] = args.strategy

    db_name = os.getenv("MONGODB_DB_NAME", "portfolio_analyzer")
    db = MongoClient(_mongo_uri(), serverSelectionTimeoutMS=8_000)[db_name]
    docs = list(db.mt_positions.find(query).sort("entry_time", 1))

    rows: list[dict[str, object]] = []
    missing = 0
    for doc in docs:
        bars = pd.DataFrame(doc.get("chart", {}).get("bars", []))
        if bars.empty:
            missing += 1
            continue
        bars["time"] = pd.to_datetime(bars["time"], utc=True)
        bars = bars.set_index("time").sort_index()
        decision = pd.Timestamp(doc["time"], tz="UTC")
        known = bars.loc[:decision]
        slopes = price_volume_slopes(known, args.lookback)
        if slopes is None:
            missing += 1
            continue
        price_slope, volume_slope = slopes
        rows.append({
            "symbol": doc["symbol"],
            "decision": decision.tz_convert(IST).strftime("%H:%M"),
            "regime": _regime(price_slope, volume_slope),
            "price_slope_pct_per_bar": price_slope * 100.0,
            "volume_slope_per_bar": volume_slope,
            "gross_inr": float(doc.get("gross_inr", 0.0)),
            "net_inr": float(doc.get("net_inr", 0.0)),
            "exit_reason": doc.get("exit_reason", ""),
        })

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise SystemExit(f"no replayable closed trades for {args.date}")
    keep = frame["regime"].eq("up_price_up_volume")

    print(frame.to_string(index=False, formatters={
        "price_slope_pct_per_bar": "{:+.4f}".format,
        "volume_slope_per_bar": "{:+.4f}".format,
        "gross_inr": "{:+.2f}".format,
        "net_inr": "{:+.2f}".format,
    }))
    print("\nregime summary")
    print(frame.groupby("regime").agg(
        trades=("symbol", "size"),
        gross_inr=("gross_inr", "sum"),
        net_inr=("net_inr", "sum"),
        avg_net_inr=("net_inr", "mean"),
    ).round(2).to_string())
    print(f"\nrecorded: trades={len(frame)} gross=INR {frame.gross_inr.sum():+.2f} "
          f"net=INR {frame.net_inr.sum():+.2f}")
    print(f"overlay kept: trades={int(keep.sum())} "
          f"gross=INR {frame.loc[keep, 'gross_inr'].sum():+.2f} "
          f"net=INR {frame.loc[keep, 'net_inr'].sum():+.2f}")
    print(f"overlay refused: trades={int((~keep).sum())} "
          f"gross=INR {frame.loc[~keep, 'gross_inr'].sum():+.2f} "
          f"net=INR {frame.loc[~keep, 'net_inr'].sum():+.2f}")
    print(f"unscored_missing_chart_or_history={missing}")


if __name__ == "__main__":
    main()
