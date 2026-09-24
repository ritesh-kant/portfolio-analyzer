# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy"]
# ///
"""BT38 — decide the volume-shelf level rule against its locked criteria.

Reads the two bt17 runs (shelves on / off) and prints the numbers that
research/hypotheses/2026-09-16-volume-shelf-levels.md §5 fixed BEFORE the run:

  C1 level  gross %/trade of the survivors must clear the 0.21% real
            round-trip cost ("the lift exceeds the cost in the direction
            that matters")
  C2 anti   p < 0.05 vs 1,000 random deletions of the same SIZE from the off
            run, scored on the refused trades only (a rule that only trades
            less is not a filter)
  C3 halves the lift must be positive in BOTH halves of the window

Nothing here may be edited to make a verdict come out differently.

Usage:  apps/signal-engine/.venv/bin/python research/backtests/bt38_volume_shelves.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ON = HERE / "bt17_trades_shelf_on.csv"
OFF = HERE / "bt17_trades_shelf_off.csv"
CANDS_ON = HERE / "bt17_candidates_shelf_on.csv"

REAL_COST_PCT = 0.21
C2_ALPHA = 0.05
DRAWS = 1000
SEED = 20260916           # fixed before the run


def _key(df: pd.DataFrame) -> pd.Series:
    return (df["date"].astype(str) + "|" + df["symbol"].astype(str)
            + "|" + df["entry_time"].astype(str))


def main() -> int:
    for p in (ON, OFF):
        if not p.exists():
            print(f"missing {p.name} — run bt17 for both arms first")
            return 1
    on, off = pd.read_csv(ON), pd.read_csv(OFF)
    on["k"], off["k"] = _key(on), _key(off)

    print("=" * 78)
    print("BT38 — volume-shelf levels vs their locked criteria")
    print("=" * 78)
    print(f"shelves OFF : {len(off):5d} trades")
    print(f"shelves ON  : {len(on):5d} trades  "
          f"({len(on) / max(len(off), 1) * 100:.1f}% survive)")

    extra = set(_key(on)) - set(_key(off))
    if extra:
        print(f"\n!!  {len(extra)} trades exist ON but not OFF — not a subset; "
              "C2 would be invalid.")

    g_on, g_off = on["gross_pct"].mean(), off["gross_pct"].mean()
    n_on = len(on)
    lift = g_on - g_off

    print(f"\ngross %/trade        off {g_off:+.4f}   on {g_on:+.4f}   "
          f"lift {lift:+.4f} pp")
    print(f"net at real {REAL_COST_PCT}%     off {g_off - REAL_COST_PCT:+.4f}   "
          f"on {g_on - REAL_COST_PCT:+.4f}")
    print(f"net at 0.80% stress  off {off['net_pct'].mean():+.4f}   "
          f"on {on['net_pct'].mean():+.4f}")
    print(f"win %                off {(off['gross_pct'] > 0).mean() * 100:.1f}   "
          f"on {(on['gross_pct'] > 0).mean() * 100:.1f}")

    # C2 anti-test: does random deletion of the same size do just as well?
    # Shelves change the level set, which changes exits and admits new setups, so
    # the ON arm is NOT a subset of OFF. Testing it against random subsets of OFF
    # would score those side effects as if they were selectivity. The filter
    # question is only about the trades the rule REFUSED, so isolate those.
    kept = off[off["k"].isin(set(_key(on)) & set(_key(off)))]
    n_removed = len(set(_key(off)) - set(_key(on)))
    g_kept = kept["gross_pct"].mean()
    rng = np.random.default_rng(SEED)
    pool = off["gross_pct"].to_numpy()
    if n_removed and n_removed < len(pool):
        draws = np.array([np.delete(pool, rng.choice(len(pool), n_removed,
                                                     replace=False)).mean()
                          for _ in range(DRAWS)])
        beat = float((draws < g_kept).mean())
        p_anti = 1.0 - beat
    else:
        beat, p_anti = float("nan"), float("nan")
    print(f"\nrefusal in isolation: off {g_off:+.4f} -> kept {g_kept:+.4f} "
          f"({n_removed} refused)  lift {g_kept - g_off:+.4f} pp")

    # C3: split the window by DATE at its midpoint. Splitting by calendar year
    # passes vacuously whenever the window is shorter than two years.
    dates = sorted(set(off["date"]) | set(on["date"]))
    mid = dates[len(dates) // 2]
    print(f"\nhalves (split at {mid}):")
    per_half_ok = True
    for lab, mo, mn in [("first ", off["date"] < mid, on["date"] < mid),
                        ("second", off["date"] >= mid, on["date"] >= mid)]:
        o, n_ = off[mo], on[mn]
        if len(o) == 0 or len(n_) == 0:
            print(f"  {lab}: insufficient data")
            per_half_ok = False
            continue
        ly = n_["gross_pct"].mean() - o["gross_pct"].mean()
        per_half_ok &= bool(ly > 0)
        print(f"  {lab}: off {o['gross_pct'].mean():+.4f} (n={len(o):4d})   "
              f"on {n_['gross_pct'].mean():+.4f} (n={len(n_):4d})   "
              f"lift {ly:+.4f} pp")

    rows = [
        ("C1 level", f"gross on {g_on:+.4f}%", f">= +{REAL_COST_PCT}", g_on >= REAL_COST_PCT),
        ("C2 anti", f"p = {p_anti:.4f} (beats {beat * 100:.1f}% of draws)",
         f"p < {C2_ALPHA}", bool(p_anti < C2_ALPHA)),
        ("C3 halves", f"lift positive in both halves: {per_half_ok}", "both", per_half_ok),
    ]
    print("\n" + "-" * 78)
    for name, got, want, ok in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {name:10s} {got:46s} required {want}")
    print("-" * 78)
    print("VERDICT:", "SHIP" if all(r[3] for r in rows) else "KILL")

    if CANDS_ON.exists():
        c = pd.read_csv(CANDS_ON)
        reasons = c["quality_reason"].value_counts()
        print("\nrefusal reasons (candidates, shelves ON), top 8:")
        for reason, n in reasons.head(8).items():
            print(f"  {str(reason):28s} {n:6d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
