# /// script
# requires-python = ">=3.11"
# dependencies = ["yfinance", "pymongo", "pandas"]
# ///
"""BT8 — large-cap fade replay (hypothesis: research/hypotheses/2026-06-11-largecap-fade.md).

Cohort: high-conf moderate/major bullish OR bearish signals naming >=1 NIFTY50
stock, 2026-06-02 -> 2026-06-11; only the NIFTY50 names are traded.
Strategy = FADE (short bullish news, long bearish news).
Pre-registered control = momentum side (with the signal): must be <= 0 gross
for the fade to be believed.
Also reported: fade excluding symbol-days where a real BT5 large-cap position
existed (the n=7 genesis observation), to show the untainted subset.
"""

from datetime import datetime
from pathlib import Path

from replay_lib import NIFTY_50, dump_trades, load_signals, simulate, summarize

BARS_START, BARS_END = "2026-06-01", "2026-06-12"

signals = load_signals(
    {
        "signal": {"$in": ["bullish", "bearish"]},
        "confidence": "high",
        "magnitude": {"$in": ["moderate", "major"]},
        "created_at": {"$gte": datetime(2026, 6, 2)},
        "stocks.0": {"$exists": True},
    }
)
signals = [s for s in signals if any(x in NIFTY_50 for x in s["stocks"])]
print(f"cohort: {len(signals)} high-conf mod/major signals naming a NIFTY50 stock")

eligible = lambda sig: [s for s in sig["stocks"] if s in NIFTY_50]
fade = lambda sig: "short" if sig["signal"] == "bullish" else "long"
momentum = lambda sig: "long" if sig["signal"] == "bullish" else "short"

trades, drops = simulate(signals, eligible, fade, BARS_START, BARS_END)
print(f"drops: {drops}")
summarize(trades, "BT8 STRATEGY — fade large-cap news")
dump_trades(trades, Path(__file__).parent / "bt8_trades_fade.csv")

mom, _ = simulate(signals, eligible, momentum, BARS_START, BARS_END)
summarize(mom, "BT8 CONTROL — momentum side (must be <= 0 gross)")
dump_trades(mom, Path(__file__).parent / "bt8_trades_momentum.csv")

# untainted subset: drop symbol-days that overlap real large-cap positions (BT5 genesis)
from pymongo import MongoClient  # noqa: E402
from replay_lib import _REPO  # noqa: E402

uri = next(
    line.split("=", 1)[1].strip().strip('"').strip("'")
    for line in (_REPO / ".env").read_text().splitlines()
    if line.startswith("MONGODB_URI=")
)
client = MongoClient(uri)
db = client.get_default_database()
real = db.nt_positions.find({"symbol": {"$in": sorted(NIFTY_50)}}, {"symbol": 1, "entry_at": 1})
tainted = {(p["symbol"], p["entry_at"].date()) for p in real}
client.close()

clean = [t for t in trades if (t.symbol, t.entry_at.date()) not in tainted]
summarize(clean, f"BT8 STRATEGY excl. {len(trades) - len(clean)} BT5-overlap symbol-days")
