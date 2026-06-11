# /// script
# requires-python = ">=3.11"
# dependencies = ["yfinance", "pymongo", "pandas"]
# ///
"""BT7 — news-shorts replay (hypothesis: research/hypotheses/2026-06-11-news-shorts-replay.md).

Cohort: bearish high-conf moderate/major signals, 2026-06-02 -> 2026-06-11,
symbols in NIFTY500 minus NIFTY50. Short side = the strategy; long side on the
identical cohort = the pre-registered anti-strategy check.
"""

from pathlib import Path

from replay_lib import NIFTY_50, NIFTY_500, dump_trades, load_signals, simulate, summarize

BARS_START, BARS_END = "2026-06-01", "2026-06-12"

signals = load_signals(
    {
        "signal": "bearish",
        "confidence": "high",
        "magnitude": {"$in": ["moderate", "major"]},
        "created_at": {"$gte": __import__("datetime").datetime(2026, 6, 2)},
        "stocks.0": {"$exists": True},
    }
)
print(f"cohort: {len(signals)} bearish high-conf moderate/major signals with stocks")

eligible = lambda sig: [s for s in sig["stocks"] if s in NIFTY_500 and s not in NIFTY_50]

# --- strategy: SHORT the bearish signal ---
trades, drops = simulate(signals, eligible, lambda s: "short", BARS_START, BARS_END)
print(f"drops: {drops}")
stats = summarize(trades, "BT7 STRATEGY — short bearish news (non-NIFTY50)")
dump_trades(trades, Path(__file__).parent / "bt7_trades_short.csv")

# --- anti-strategy: LONG the identical signals ---
anti, _ = simulate(signals, eligible, lambda s: "long", BARS_START, BARS_END)
summarize(anti, "BT7 ANTI-STRATEGY — long the same bearish signals")
dump_trades(anti, Path(__file__).parent / "bt7_trades_anti.csv")
