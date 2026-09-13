# /// script
# requires-python = ">=3.12"
# dependencies = ["pymongo", "python-dotenv"]
# ///
"""Forward paper check: how close does the resting buy-stop actually fill?

BT17's replay bought at the *next* 1-min open after the trigger bar closed and
paid a median +0.18% / mean +0.21% over the trigger — about one full round trip
of real MIS cost, lost silently at the door (see the 2026-09-12 chart review).
The live arms run `fill_mode="future_trigger"`: a resting buy-stop that fills on
the first quote at or above the trigger. This script measures what that is
actually worth on the forward log.

  uv run research/backtests/mt_forward_fill_quality.py

CAVEAT, and it is the whole point of reading the number carefully: paper fills
are taken at the *observed quote*, so this measures DECISION LATENCY (how far
price had travelled by the time the engine saw a qualifying quote), not true
execution slippage against a real book. Real orders add queue position, partial
fills and spread on top. This is the ceiling, not the fill you will get.
"""

from __future__ import annotations

import os
import statistics as st
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

REPO = Path(__file__).resolve().parents[2]
BT17_NEXT_OPEN_MEAN = 0.209   # mean entry slip %, bt17_trades_vs_b.csv (n=3,966)
BT17_NEXT_OPEN_MEDIAN = 0.177


def main() -> None:
    load_dotenv(REPO / ".env")
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise SystemExit("MONGODB_URI not set (.env)")
    db = MongoClient(uri).get_database(os.getenv("MONGODB_DB", "portfolio_analyzer"))
    rows = list(db.mt_positions.find({"status": "closed"}).sort("entry_time", 1))
    if not rows:
        raise SystemExit("no closed forward positions yet")

    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r.get("strategy", "(unlabelled)"), []).append(r)

    for strat, trades in by.items():
        slips = [
            100.0 * (t["entry_price"] / t["trigger_px"] - 1.0)
            for t in trades
            if t.get("trigger_px") and t.get("entry_price")
        ]
        net = sum(t.get("net_inr", 0.0) for t in trades)
        gross = sum(t.get("gross_inr", 0.0) for t in trades)
        wins = sum(1 for t in trades if t.get("net_inr", 0.0) > 0)
        days = sorted({t["entry_time"].date().isoformat() for t in trades})
        print(f"\n=== {strat} — {len(trades)} closed trades over {len(days)} day(s) "
              f"({days[0]}..{days[-1]}) ===")
        if slips:
            print(f"  fill vs trigger : mean {st.mean(slips):+.3f}%  median {st.median(slips):+.3f}%  "
                  f"worst {max(slips):+.3f}%")
            print(f"  bt17 next_open  : mean {BT17_NEXT_OPEN_MEAN:+.3f}%  median {BT17_NEXT_OPEN_MEDIAN:+.3f}%")
            print(f"  recovered       : {BT17_NEXT_OPEN_MEAN - st.mean(slips):+.3f} pp/trade "
                  f"(2024 A/B estimated +0.236)")
            print(f"  filled at or below trigger: {sum(1 for x in slips if x <= 0)}/{len(slips)}")
        print(f"  P&L             : gross ₹{gross:,.0f}  net ₹{net:,.0f}  "
              f"({wins}/{len(trades)} winners, ₹{net / len(trades):,.0f}/trade)")
        print(f"  exits           : {dict(Counter(t.get('exit_reason') for t in trades))}")


if __name__ == "__main__":
    main()
