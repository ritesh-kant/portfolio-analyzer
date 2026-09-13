# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy"]
# ///
"""BT30 — decide the one-minute agreement rule against its locked criteria.

Reads the two bt17 runs (rule on / rule off) and prints the four numbers that
research/hypotheses/2026-09-12-one-minute-agreement.md fixed BEFORE the run:

  G1 lift   ≥ +0.60 pp   (mean gross on minus mean gross off)
  G2 level  ≥ +0.35 %    (mean gross of the surviving trades)
  G3 anti   p < 0.05     (vs 1,000 random same-size subsets of the off run)
  G4 n      ≥ 100        (surviving trades)

Nothing here may be edited to make a verdict come out differently. Costs are
reported at the measured real MIS rate as well as the 0.80% stress bt17 applies.

Usage:  uv run research/backtests/bt30_one_minute_agreement.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ON = HERE / "bt17_trades_1ma_on.csv"
OFF = HERE / "bt17_trades_1ma_off.csv"
CANDS_ON = HERE / "bt17_candidates_1ma_on.csv"

G1_LIFT_PP = 0.60
G2_LEVEL_PCT = 0.35
G3_ALPHA = 0.05
G4_MIN_N = 100
REAL_COST_PCT = 0.21      # measured round-trip MIS cost
DRAWS = 1000
SEED = 20260912           # fixed before the run


def _key(df: pd.DataFrame) -> pd.Series:
    return df["date"].astype(str) + "|" + df["symbol"].astype(str) + "|" + df["entry_time"].astype(str)


def main() -> int:
    for p in (ON, OFF):
        if not p.exists():
            print(f"missing {p.name} — run bt17 for both arms first")
            return 1
    on, off = pd.read_csv(ON), pd.read_csv(OFF)
    on_k, off_k = set(_key(on)), set(_key(off))

    print("=" * 78)
    print("BT30 — one-minute agreement rule vs its locked criteria")
    print("=" * 78)
    print(f"rule OFF : {len(off):5d} trades")
    print(f"rule ON  : {len(on):5d} trades  ({len(on) / max(len(off), 1) * 100:.1f}% survive)")

    extra = on_k - off_k
    if extra:
        print(f"\n⚠️  {len(extra)} trades exist ON but not OFF — the gated set is NOT a subset.")
        print("    G3 is invalid in that case (both runs need --first-candidate-only).")

    g_on, g_off = on["gross_pct"].mean(), off["gross_pct"].mean()
    n_on = len(on)
    lift = g_on - g_off

    print(f"\ngross %/trade   off {g_off:+.4f}   on {g_on:+.4f}")
    print(f"net at real {REAL_COST_PCT}%   off {g_off - REAL_COST_PCT:+.4f}   on {g_on - REAL_COST_PCT:+.4f}")
    print(f"net at 0.80% stress  off {off['net_pct'].mean():+.4f}   on {on['net_pct'].mean():+.4f}")
    print(f"win %           off {(off['gross_pct'] > 0).mean() * 100:.1f}   on {(on['gross_pct'] > 0).mean() * 100:.1f}")

    rng = np.random.default_rng(SEED)
    pool = off["gross_pct"].to_numpy()
    if n_on and n_on <= len(pool):
        draws = np.array([rng.choice(pool, size=n_on, replace=False).mean() for _ in range(DRAWS)])
        beat = float((draws < g_on).mean())
        p_anti = 1.0 - beat
    else:
        beat, p_anti = float("nan"), float("nan")

    rows = [
        ("G1 lift", f"{lift:+.4f} pp", f">= +{G1_LIFT_PP}", lift >= G1_LIFT_PP),
        ("G2 level", f"{g_on:+.4f} %", f">= +{G2_LEVEL_PCT}", g_on >= G2_LEVEL_PCT),
        ("G3 anti", f"p = {p_anti:.4f} (beats {beat * 100:.1f}% of draws)", f"p < {G3_ALPHA}", p_anti < G3_ALPHA),
        ("G4 n", f"{n_on}", f">= {G4_MIN_N}", n_on >= G4_MIN_N),
    ]
    print("\n" + "-" * 78)
    for name, got, want, ok in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {name:9s} {got:42s} required {want}")
    print("-" * 78)
    print("VERDICT:", "PASS" if all(r[3] for r in rows) else "KILL")

    if CANDS_ON.exists():
        c = pd.read_csv(CANDS_ON)
        refusals = c["quality_reason"].value_counts()
        mine = refusals[refusals.index.astype(str).str.startswith("1m_")]
        if len(mine):
            print("\nwhy the rule refused (candidates, rule ON):")
            for reason, n in mine.items():
                print(f"  {reason:16s} {n:6d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
