# /// script
# requires-python = ">=3.11"
# dependencies = ["yfinance", "pymongo", "pandas"]
# ///
"""BT9 — full bullish signal-level replay
(hypothesis: research/hypotheses/2026-06-11-bullish-signal-replay.md).

Cohort: bullish high-conf moderate/major signals 2026-06-02 -> 2026-06-11,
symbols in NIFTY500 minus NIFTY50, deployed exit policy, market entry.
Pre-registered splits ONLY: traded-overlap vs never-traded; major vs moderate.
"""

from datetime import datetime
from pathlib import Path

from pymongo import MongoClient

from replay_lib import (
    _REPO, NIFTY_50, NIFTY_500, dump_trades, load_signals, simulate, summarize,
)

BARS_START, BARS_END = "2026-06-01", "2026-06-12"

signals = load_signals(
    {
        "signal": "bullish",
        "confidence": "high",
        "magnitude": {"$in": ["moderate", "major"]},
        "created_at": {"$gte": datetime(2026, 6, 2)},
        "stocks.0": {"$exists": True},
    }
)
print(f"cohort: {len(signals)} bullish high-conf moderate/major signals with stocks")
mag_of = {str(s["_id"]): s["magnitude"] for s in signals}

eligible = lambda sig: [s for s in sig["stocks"] if s in NIFTY_500 and s not in NIFTY_50]

trades, drops = simulate(signals, eligible, lambda s: "long", BARS_START, BARS_END)
print(f"drops: {drops}")
summarize(trades, "BT9 COHORT — long bullish news (non-NIFTY50), deployed exits")
dump_trades(trades, Path(__file__).parent / "bt9_trades.csv")

# --- pre-registered split 1: traded-overlap vs never-traded ---
uri = next(
    line.split("=", 1)[1].strip().strip('"').strip("'")
    for line in (_REPO / ".env").read_text().splitlines()
    if line.startswith("MONGODB_URI=")
)
client = MongoClient(uri)
real = client.get_default_database().nt_positions.find({}, {"symbol": 1, "entry_at": 1})
traded = {(p["symbol"], p["entry_at"].date()) for p in real}
client.close()

overlap = [t for t in trades if (t.symbol, t.entry_at.date()) in traded]
fresh = [t for t in trades if (t.symbol, t.entry_at.date()) not in traded]
summarize(overlap, "BT9 split — traded-overlap (exit policy tuned on these paths)")
summarize(fresh, "BT9 split — NEVER-TRADED (clean subset, weightier number)")

# --- pre-registered split 2: magnitude major vs moderate ---
majors = [t for t in trades if mag_of[t.signal_id] == "major"]
mods = [t for t in trades if mag_of[t.signal_id] == "moderate"]
summarize(majors, "BT9 split — magnitude=major")
summarize(mods, "BT9 split — magnitude=moderate")
