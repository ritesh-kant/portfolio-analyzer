# /// script
# requires-python = ">=3.11"
# dependencies = ["yfinance", "pymongo", "pandas"]
# ///
"""BT10 — limit-order entry vs market entry
(hypothesis: research/hypotheses/2026-06-11-limit-entry.md).

Same cohort as BT9. Execution-layer comparison: total cohort net P&L,
limit vs market, missed trades counted against the limit variant.
"""

from datetime import datetime
from pathlib import Path

from replay_lib import NIFTY_50, NIFTY_500, dump_trades, load_signals, simulate, summarize

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
eligible = lambda sig: [s for s in sig["stocks"] if s in NIFTY_500 and s not in NIFTY_50]

market, _ = simulate(signals, eligible, lambda s: "long", BARS_START, BARS_END, "market")
m = summarize(market, "BT10 BASELINE — market entry")

limit, drops = simulate(signals, eligible, lambda s: "long", BARS_START, BARS_END, "limit")
print(f"limit drops: {drops}")
l = summarize(limit, "BT10 STRATEGY — 15-min resting limit at entry-bar open")
dump_trades(limit, Path(__file__).parent / "bt10_trades_limit.csv")

filled_keys = {(t.signal_id, t.symbol) for t in limit}
missed = [t for t in market if (t.signal_id, t.symbol) not in filled_keys]
summarize(missed, "BT10 — missed by the limit (market-entry counterfactual)")

n_orders = len(market)
print(f"\nfill rate: {len(limit)}/{n_orders} ({len(limit) / n_orders * 100:.0f}%)")
print(f"COHORT TOTAL NET — market ₹{m['net']:+,.0f} vs limit ₹{l['net']:+,.0f}")
print("(criterion: KILL if limit <= market)")
