# /// script
# requires-python = ">=3.11"
# dependencies = ["yfinance", "pymongo", "pandas"]
# ///
"""BT12 — event_type drift, signal-level replay
(hypothesis: research/hypotheses/2026-06-16-news-event-type-drift.md).

THE COMMITTED FINAL signal-side experiment. Locked criteria (no sweeps):
  Group A (information): m_and_a, earnings, order_win, regulatory, capital_action
  Group B (noise/ctrl):  rating_analyst, generic_pr, macro_sector, management, other
  PASS  if A gross >= +0.30%/trade AND (A-B) spread >= +0.30%/trade AND anti ~ 0
  KILL  if A gross <  +0.15%/trade OR  (A-B) spread <  +0.15%/trade OR perm_spread>=real
  else  NOT VIABLE AT CURRENT COSTS (+0.15..+0.30 band)

Cohort: actionable signals (bullish->long, bearish->short), confidence=high,
magnitude in {moderate,major}, event_type present, stocks present, created since
the field deploy (2026-06-17). NIFTY500 minus NIFTY50. Deployed exits, market entry.

Dev/hold-out (pre-registered temporal split): dev = first whole trading days whose
cumulative Group-A replayed trades reach >= 30; hold-out = the remainder, NOT read
unless dev is non-KILL. Single shot, no re-runs.
"""

import random
from datetime import datetime
from pathlib import Path

from replay_lib import (
    NIFTY_50, NIFTY_500, dump_trades, load_signals, simulate, summarize,
)

random.seed(42)

GROUP_A = {"m_and_a", "earnings", "order_win", "regulatory", "capital_action"}
GROUP_B = {"rating_analyst", "generic_pr", "macro_sector", "management", "other"}

FIELD_DEPLOY = datetime(2026, 6, 17)          # event_type first populated
BARS_START, BARS_END = "2026-06-16", "2026-06-27"
MIN_GROUP_A = 30

signals = load_signals(
    {
        "signal": {"$in": ["bullish", "bearish"]},
        "confidence": "high",
        "magnitude": {"$in": ["moderate", "major"]},
        "event_type": {"$exists": True},
        "created_at": {"$gte": FIELD_DEPLOY},
        "stocks.0": {"$exists": True},
    }
)
et_of = {str(s["_id"]): s["event_type"] for s in signals}
print(f"cohort: {len(signals)} actionable signals with event_type since 2026-06-17")
print(f"  Group A signals: {sum(1 for s in signals if s['event_type'] in GROUP_A)}")
print(f"  Group B signals: {sum(1 for s in signals if s['event_type'] in GROUP_B)}")

eligible = lambda sig: [s for s in sig["stocks"] if s in NIFTY_500 and s not in NIFTY_50]
direction = lambda sig: "long" if sig["signal"] == "bullish" else "short"

trades, drops = simulate(signals, eligible, direction, BARS_START, BARS_END)
print(f"drops: {drops}")
for t in trades:
    t.group = "A" if et_of[t.signal_id] in GROUP_A else "B"
    t.event_type = et_of[t.signal_id]

# ---- pre-registered temporal dev/hold-out split (first whole days reaching 30 A) ----
trades.sort(key=lambda t: t.entry_at)
days = sorted({t.entry_at.date() for t in trades})
cum_a, dev_cutoff = 0, days[-1]
for d in days:
    cum_a += sum(1 for t in trades if t.entry_at.date() == d and t.group == "A")
    if cum_a >= MIN_GROUP_A:
        dev_cutoff = d
        break
dev = [t for t in trades if t.entry_at.date() <= dev_cutoff]
holdout = [t for t in trades if t.entry_at.date() > dev_cutoff]
print(f"\ndev window: {days[0]} .. {dev_cutoff}  (hold-out: {dev_cutoff} exclusive .. {days[-1]})")


def gross_pcts(ts):
    return [(t.gross / (t.entry * t.qty)) * 100 for t in ts]


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def report(ts, tag):
    A = [t for t in ts if t.group == "A"]
    B = [t for t in ts if t.group == "B"]
    summarize(A, f"{tag} — GROUP A (information events) long/short, deployed exits")
    summarize(B, f"{tag} — GROUP B (noise control)")
    a_pct, b_pct = mean(gross_pcts(A)), mean(gross_pcts(B))
    spread = a_pct - b_pct
    print(f"\n  >>> {tag}: A gross {a_pct:+.3f}%/trade  B gross {b_pct:+.3f}%/trade  "
          f"spread(A-B) {spread:+.3f}%/trade  [n_A={len(A)} n_B={len(B)}]")
    # per event_type (reported, not gated)
    print("  per event_type gross %/trade:")
    for et in sorted(GROUP_A | GROUP_B):
        sub = [t for t in ts if t.event_type == et]
        if sub:
            grp = "A" if et in GROUP_A else "B"
            print(f"    [{grp}] {et:<15} n={len(sub):<3} {mean(gross_pcts(sub)):+.3f}%")
    # anti-strategy: permute labels preserving group sizes
    labels = [t.group for t in ts]
    pcts = gross_pcts(ts)
    nA = labels.count("A")
    idx = list(range(len(ts)))
    ge = 0
    PERMS = 2000
    for _ in range(PERMS):
        random.shuffle(idx)
        perm_a = mean([pcts[i] for i in idx[:nA]])
        perm_b = mean([pcts[i] for i in idx[nA:]])
        if (perm_a - perm_b) >= spread:
            ge += 1
    pval = ge / PERMS
    print(f"  anti-strategy: P(permuted spread >= real) = {pval:.3f}  "
          f"({'FAIL: noise' if pval > 0.05 else 'real spread beats shuffled labels'})")
    return a_pct, spread, pval


print("\n" + "=" * 70)
print("DEV READ (decision window) — single shot")
print("=" * 70)
a_pct, spread, pval = report(dev, "DEV")
dump_trades(dev, Path(__file__).parent / "bt12_trades_dev.csv")

# ---- locked decision ----
print("\n" + "=" * 70)
n_a = sum(1 for t in dev if t.group == "A")
if n_a < MIN_GROUP_A:
    verdict = f"INSUFFICIENT (n_A={n_a} < {MIN_GROUP_A}) — do not read yet"
elif a_pct < 0.15 or spread < 0.15 or pval > 0.05:
    verdict = "KILL"
elif a_pct >= 0.30 and spread >= 0.30 and pval <= 0.05:
    verdict = "PASS"
else:
    verdict = "NOT VIABLE AT CURRENT COSTS (+0.15..+0.30 band)"
print(f"VERDICT: {verdict}")
print(f"  A gross={a_pct:+.3f}% (kill<+0.15, pass>=+0.30) | spread={spread:+.3f}% "
      f"(kill<+0.15, pass>=+0.30) | anti p={pval:.3f} (kill>0.05)")
print("=" * 70)

if verdict == "KILL":
    print("\nKILL is terminal per the committed stop (2026-06-16): hold-out NOT read.")
    print("Even value-changing events show no entry-window drift on real bars ->")
    print("retail-latency news trading on NSE is a documented non-viable result.")
else:
    print("\nDev non-KILL -> reading HOLD-OUT as the confirmatory shot:")
    report(holdout, "HOLD-OUT")
    dump_trades(holdout, Path(__file__).parent / "bt12_trades_holdout.csv")
