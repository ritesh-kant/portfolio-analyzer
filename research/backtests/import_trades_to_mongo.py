# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "pymongo", "python-dotenv"]
# ///
"""Load a BT17 trades CSV into Mongo so the momentum analytics dashboard can read it.

The dashboard's default source is the live paper ledger (`mt_positions`), which
only fills a few rows a day.  Backtest runs hold thousands of trades with the
same fields, so importing one makes every breakdown (time of day, price band,
weekday) readable immediately — as long as the two can never be mistaken for
each other.  They can't: backtest rows live in their own collection
(`mt_backtest_trades`), carry a `run_tag`, and the API labels every one of them
BACKTEST.

Cost model: bt17 adds a +40 bps/side stress slip on top of real MIS costs
(engine.STRESS_SLIP), the live ledger adds none.  The per-trade stress rupees
are stored separately as `stress_inr` so the dashboard can show the same run at
stressed or at real broker costs without re-running the backtest.

Usage (from the repo root):

    apps/signal-engine/.venv/bin/python research/backtests/import_trades_to_mongo.py \
        --trades research/backtests/bt17_trades.csv \
        --tag bt17_2024 --label "BT17 pool · 2024"

    # re-import after a rerun of the same backtest
    ... --tag bt17_2024 --replace

    --list       show what is already imported
    --delete TAG remove one imported run
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from pymongo import MongoClient

_REPO = Path(__file__).resolve().parents[2]

TRADES = "mt_backtest_trades"
RUNS = "mt_backtest_runs"

IST = "Asia/Kolkata"

#: bt17's cost stress, mirrored from src.momentum_trader.engine.STRESS_SLIP.
#: Duplicated rather than imported so this script stays runnable with only
#: pandas + pymongo on PATH; asserted against the engine when it is importable.
STRESS_SLIP = 0.0040


def _num(row: pd.Series, key: str) -> float | None:
    """Read one numeric cell, tolerating columns older CSVs never wrote."""
    if key not in row.index:
        return None
    value = row[key]
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(row: pd.Series, key: str) -> str | None:
    if key not in row.index:
        return None
    value = row[key]
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    return str(value)


def _stamp(day: str, clock: str | None) -> datetime | None:
    """Combine the CSV's separate date and HH:MM columns into one UTC instant."""
    if not clock:
        return None
    try:
        local = pd.Timestamp(f"{day} {clock}", tz=IST)
    except ValueError:
        return None
    return local.tz_convert("UTC").to_pydatetime()


def to_doc(row: pd.Series, tag: str) -> dict[str, object]:
    """Shape one CSV row like an `mt_positions` document.

    Field names follow the live ledger, not the CSV, so the dashboard reads a
    single schema.  Anything the CSV never recorded stays absent rather than
    being filled with a zero that would read as a measurement.
    """
    day = str(row["date"])
    entry = float(row["entry"])
    exit_px = float(row["exit"])
    qty = int(row["qty"])
    tags = _text(row, "tags")

    doc: dict[str, object] = {
        "run_tag": tag,
        "source": "backtest",
        "symbol": str(row["symbol"]),
        "setup": str(row["setup"]),
        "status": "closed",
        "trade_date": day,
        "entry_time": _stamp(day, _text(row, "entry_time")),
        "exit_time": _stamp(day, _text(row, "exit_time")),
        "time": _stamp(day, _text(row, "trigger_time")) or _stamp(day, _text(row, "entry_time")),
        "entry_price": entry,
        "exit_price": exit_px,
        "stop": float(row["stop"]),
        "qty": qty,
        "notional_inr": entry * qty,
        "exit_reason": str(row["exit_reason"]),
        "gross_inr": float(row["gross_inr"]),
        "costs_inr": float(row["costs_inr"]),
        "net_inr": float(row["net_inr"]),
        # The portion of costs_inr that is bt17's cost stress rather than a
        # real broker charge. Backing this out is how the dashboard shows the
        # same run at the ~0.21% real MIS cost it would have actually paid.
        "stress_inr": (entry + exit_px) * qty * STRESS_SLIP,
        "stress_slip": STRESS_SLIP,
        "candle_tags": tags.split("|") if tags else [],
    }

    trigger = _num(row, "trigger")
    if trigger is not None:
        doc["trigger_px"] = trigger
    risk_per_share = entry - float(row["stop"])
    if risk_per_share > 0:
        doc["risk_inr"] = risk_per_share * qty
        doc["target"] = entry + risk_per_share * 2

    for csv_key, doc_key in (
        ("day_chg_pct", "day_chg_pct"),
        ("rvol", "rvol"),
        ("catalyst", "catalyst"),
        ("pullback_ord", "pullback_ord"),
        ("atr_pct", "atr_pct"),
        ("macd_hist", "macd_hist"),
        ("dist_to_round_pct", "dist_to_round_pct"),
        ("round_head_pct", "round_head_pct"),
        ("resist_head_pct", "resist_head_pct"),
        ("support_drop_pct", "support_drop_pct"),
        ("prev_day_gainer", "prev_day_gainer"),
    ):
        value = _num(row, csv_key)
        if value is not None:
            doc[doc_key] = value

    for csv_key, doc_key in (("event_type", "event_type"), ("quality_reason", "quality_reason")):
        value = _text(row, csv_key)
        if value is not None:
            doc[doc_key] = value

    return doc


#: Share of breakeven `trail_stop` scratches above which a CSV is assumed to
#: carry the breakeven-lock defect fixed on 2026-09-06 (it logged ~40% of real
#: 2R winners as flat scratches). Mirrors bt29's MAX_SCRATCH_SHARE.
MAX_SCRATCH_SHARE = 0.005


def defects(frame: pd.DataFrame) -> list[str]:
    """Known reasons a CSV's P&L would mislead the dashboard.

    Both of these were real findings, not hypotheticals: multi-entry was killed
    on 2022-23 and would over-weight repeat trades in every breakdown, and CSVs
    written before the 2026-09-06 breakeven-lock fix understate gross P&L.
    """
    found = []
    if "date" in frame.columns and frame.groupby(["date", "symbol"]).size().max() > 1:
        found.append("more than one trade per symbol-day (multi-entry arm — killed on 2022-23)")
    if {"exit_reason", "gross_pct"} <= set(frame.columns):
        scratch = ((frame["exit_reason"] == "trail_stop") & (frame["gross_pct"].abs() < 0.005)).mean()
        if scratch > MAX_SCRATCH_SHARE:
            found.append(f"{scratch:.1%} breakeven scratches (pre-2026-09-06 breakeven-lock defect)")
    return found


def load(path: Path, tag: str, force: bool) -> list[dict[str, object]]:
    frame = pd.read_csv(path)
    required = {"date", "symbol", "setup", "entry", "exit", "stop", "qty",
                "exit_reason", "gross_inr", "costs_inr", "net_inr"}
    missing = required - set(frame.columns)
    if missing:
        raise SystemExit(f"{path.name} is missing required columns: {sorted(missing)}")
    problems = defects(frame)
    if problems:
        joined = "\n  - ".join(problems)
        if not force:
            raise SystemExit(
                f"{path.name} would import misleading P&L:\n  - {joined}\n"
                "Import a clean run instead, or pass --force if you know why you want this one."
            )
        print(f"WARNING: importing {path.name} despite:\n  - {joined}")
    return [to_doc(row, tag) for _, row in frame.iterrows()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trades", help="path to a bt17_trades*.csv")
    parser.add_argument("--tag", help="short id for this run, e.g. bt17_2024")
    parser.add_argument("--label", help="human label shown in the dashboard's source picker")
    parser.add_argument("--replace", action="store_true", help="delete an existing run with this tag first")
    parser.add_argument("--force", action="store_true", help="import even if the CSV has a known P&L defect")
    parser.add_argument("--list", action="store_true", help="list imported runs and exit")
    parser.add_argument("--delete", metavar="TAG", help="delete one imported run and exit")
    args = parser.parse_args()

    load_dotenv(_REPO / ".env")
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        raise SystemExit("MONGODB_URI is not set (expected in .env at the repo root)")
    db = MongoClient(uri, serverSelectionTimeoutMS=8000)[os.environ.get("MONGODB_DB_NAME", "portfolio_analyzer")]

    if args.list:
        runs = list(db[RUNS].find().sort("imported_at", -1))
        if not runs:
            print("no backtest runs imported yet")
        for run in runs:
            print(f"{run['_id']:<24} {run.get('trades', 0):>6} trades  "
                  f"{run.get('from', '?')} → {run.get('to', '?')}  {run.get('label', '')}")
        return 0

    if args.delete:
        removed = db[TRADES].delete_many({"run_tag": args.delete}).deleted_count
        db[RUNS].delete_one({"_id": args.delete})
        print(f"deleted run {args.delete} ({removed} trades)")
        return 0

    if not args.trades or not args.tag:
        parser.error("--trades and --tag are required unless --list or --delete is used")

    path = Path(args.trades)
    if not path.is_absolute():
        path = _REPO / path
    if not path.exists():
        raise SystemExit(f"no such file: {path}")

    existing = db[TRADES].count_documents({"run_tag": args.tag})
    if existing and not args.replace:
        raise SystemExit(f"run '{args.tag}' already holds {existing} trades — pass --replace to overwrite")
    if existing:
        db[TRADES].delete_many({"run_tag": args.tag})

    docs = load(path, args.tag, args.force)
    if not docs:
        raise SystemExit(f"{path.name} has no trade rows")
    db[TRADES].insert_many(docs)
    db[TRADES].create_index([("run_tag", 1), ("entry_time", -1)])

    dates = sorted(str(doc["trade_date"]) for doc in docs)
    net = sum(float(doc["net_inr"]) for doc in docs)
    gross = sum(float(doc["gross_inr"]) for doc in docs)
    db[RUNS].replace_one(
        {"_id": args.tag},
        {
            "_id": args.tag,
            "label": args.label or f"{path.stem} ({dates[0]} → {dates[-1]})",
            "source_file": str(path.relative_to(_REPO)) if path.is_relative_to(_REPO) else str(path),
            "trades": len(docs),
            "from": dates[0],
            "to": dates[-1],
            "gross_inr": gross,
            "net_inr": net,
            "stress_slip": STRESS_SLIP,
            "imported_at": datetime.now(timezone.utc),
        },
        upsert=True,
    )

    print(f"imported {len(docs)} trades as '{args.tag}' ({dates[0]} → {dates[-1]})")
    print(f"  gross ₹{gross:,.0f} · net ₹{net:,.0f} (at bt17's +{STRESS_SLIP * 1e4:.0f} bps/side stress)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
