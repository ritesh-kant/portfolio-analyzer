"""Weight recalibration script — compute per-signal accuracy from MongoDB outcomes.

Run after 50+ closed signals exist:
  python apps/signal-engine/scripts/recalibrate_weights.py

Reads trading_signals documents where was_correct is set (i.e. monitor_agent has
back-filled the outcome). Computes the predictive lift (win-rate uplift when signal
fires vs when it doesn't) for each of the 5 deterministic technical signals, then
suggests new weights proportional to actual predictive power.

Current weights (total 96 base pts):
  rsi_oversold    12   above_ema20  10   volume_elevated  10
  macd_positive   12   above_ema50  10   near_75d_high     8
  news_positive   14   sector_bullish 12  market_positive   8
"""

import asyncio
import os
import sys
from typing import Any

try:
    from motor.motor_asyncio import AsyncIOMotorClient
except ImportError:
    print("motor not installed — run: pip install motor")
    sys.exit(1)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("TRADING_DB", "trading")
MIN_SAMPLE = 5  # minimum fired-count to include a signal in suggestions

# Deterministic signals extractable from stored fields
SIGNAL_PREDICATES: dict[str, Any] = {
    "rsi_oversold":    lambda s: isinstance(s.get("rsi"), (int, float)) and s["rsi"] < 40,
    "macd_positive":   lambda s: isinstance(s.get("macd_hist"), (int, float)) and s["macd_hist"] > 0,
    "above_ema20":     lambda s: bool(s.get("above_ema20")),
    "above_ema50":     lambda s: bool(s.get("above_ema50")),
    "volume_elevated": lambda s: isinstance(s.get("volume_ratio"), (int, float)) and s["volume_ratio"] > 1.5,
}

CURRENT_WEIGHTS = {
    "rsi_oversold": 12, "macd_positive": 12, "above_ema20": 10,
    "above_ema50": 10,  "volume_elevated": 10,
}


def _safe(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


async def main() -> None:
    client = AsyncIOMotorClient(MONGO_URI)
    col = client[DB_NAME]["trading_signals"]

    docs = await col.find(
        {"was_correct": {"$exists": True}, "actual_return_pct": {"$exists": True}},
        projection={
            "rsi": 1, "macd_hist": 1, "above_ema20": 1, "above_ema50": 1,
            "volume_ratio": 1, "was_correct": 1, "actual_return_pct": 1,
            "confidence": 1, "symbol": 1,
        },
    ).to_list(length=None)

    client.close()

    if not docs:
        print("No closed signals found. Run the pipeline and wait for monitor_agent to close positions.")
        return

    total = len(docs)
    overall_win_rate = sum(1 for d in docs if d.get("was_correct")) / total * 100
    overall_avg_ret = sum(_safe(d.get("actual_return_pct")) for d in docs) / total

    print(f"\n{'='*65}")
    print(f"Signal Weight Recalibration Report")
    print(f"{'='*65}")
    print(f"Total closed signals: {total}")
    print(f"Overall win rate:     {overall_win_rate:.1f}%")
    print(f"Overall avg return:   {overall_avg_ret:+.2f}%")
    print(f"{'='*65}\n")

    results: dict[str, dict] = {}
    for name, predicate in SIGNAL_PREDICATES.items():
        fired    = [d for d in docs if predicate(d)]
        not_fired = [d for d in docs if not predicate(d)]

        if not fired:
            results[name] = {"fired": 0, "win_rate": 0, "lift": 0, "avg_ret": 0}
            continue

        wins_fired    = [d for d in fired    if d.get("was_correct")]
        wins_notfired = [d for d in not_fired if d.get("was_correct")]

        win_rate_fired    = len(wins_fired)    / len(fired)    * 100
        win_rate_notfired = (len(wins_notfired) / len(not_fired) * 100) if not_fired else 0.0
        lift = win_rate_fired - win_rate_notfired
        avg_ret = sum(_safe(d.get("actual_return_pct")) for d in fired) / len(fired)

        results[name] = {
            "fired": len(fired), "win_rate": win_rate_fired,
            "lift": lift, "avg_ret": avg_ret,
        }

    print(f"{'Signal':<20} {'Fired':>6} {'Win%':>7} {'Lift':>8} {'AvgRet':>8} {'CurWt':>6}")
    print(f"{'-'*20} {'-'*6} {'-'*7} {'-'*8} {'-'*8} {'-'*6}")
    for name, r in results.items():
        cur_wt = CURRENT_WEIGHTS.get(name, "?")
        print(f"{name:<20} {r['fired']:>6} {r['win_rate']:>6.1f}% {r['lift']:>+7.1f}pp "
              f"{r['avg_ret']:>+7.2f}% {str(cur_wt):>6}")

    # Suggest weights proportional to positive predictive lift
    # Signals with negative lift get minimum weight of 5
    lifts = {n: max(0.0, r["lift"]) for n, r in results.items() if r["fired"] >= MIN_SAMPLE}
    total_lift = sum(lifts.values())

    # Total budget for these 5 signals is their current sum (54 pts)
    budget = sum(CURRENT_WEIGHTS.values())

    print(f"\n{'='*65}")
    print(f"Suggested weight adjustments (based on predictive lift, budget={budget}pts)")
    print(f"Note: apply only after ≥50 closed signals. Current sample: {total}")
    print(f"{'='*65}")
    print(f"{'Signal':<20} {'Current':>8} {'Suggested':>10} {'Change':>8}")
    print(f"{'-'*20} {'-'*8} {'-'*10} {'-'*8}")

    for name in CURRENT_WEIGHTS:
        cur = CURRENT_WEIGHTS[name]
        if name not in lifts or results[name]["fired"] < MIN_SAMPLE:
            print(f"{name:<20} {cur:>8}   {'N/A (insufficient data)':>18}")
            continue
        if total_lift > 0:
            suggested = max(5, round(lifts[name] / total_lift * budget))
        else:
            suggested = cur
        delta = suggested - cur
        flag = " ◀ adjust" if abs(delta) >= 2 else ""
        print(f"{name:<20} {cur:>8} {suggested:>10} {delta:>+8}{flag}")

    print(f"\nApply changes in signal_agent.py _SIGNAL_DEFINITIONS.")
    print(f"Re-run this script after each 30-signal batch to track drift.")


if __name__ == "__main__":
    asyncio.run(main())
