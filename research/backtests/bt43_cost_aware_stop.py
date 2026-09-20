"""BT43 — does lifting the 1R breakeven stop to the true after-fees breakeven help?

Pairs two bt17 arms per year on identical entries (date, symbol, entry_time):

  base  = rule off  (stop lifted to the ENTRY price once the trade is up 1R)
  cost  = rule on   (stop lifted to the first tick that covers real fees, +1 tick)

and prints the four locked criteria of
research/hypotheses/2026-09-19-cost-aware-breakeven-stop.md:

  C1  pooled paired mean net delta at REAL costs > 0, paired t-test p < 0.05
  C2  pooled paired delta >= +10 INR per trade
  C3  delta > 0 in every year separately
  C4  >= 500 shared trades in every year (else the test is void)

"Real" net strips bt17's 40 bps/side research stress back out of costs_inr so
the number is what a Zerodha MIS round trip actually pays (~0.21%).

Usage:
  apps/signal-engine/.venv/bin/python research/backtests/bt43_cost_aware_stop.py
  apps/signal-engine/.venv/bin/python research/backtests/bt43_cost_aware_stop.py \
      --years 2023,2024 --prefix cs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
KEY = ["date", "symbol", "entry_time"]
STRESS_SLIP = 0.0040          # bt17's research stress, per side (engine.STRESS_SLIP)
TICK = 0.05
MIN_SHARED = 500
MIN_DELTA_INR = 10.0


def load(prefix: str, year: int, arm: str) -> pd.DataFrame:
    p = HERE / f"bt17_trades_{prefix}{str(year)[2:]}_{arm}.csv"
    if not p.exists():
        raise SystemExit(f"missing {p.name} — run bt17 for that arm first")
    df = pd.read_csv(p)
    stress = (df["entry"] + df["exit"]) * df["qty"] * STRESS_SLIP
    df["real_costs_inr"] = df["costs_inr"] - stress
    df["real_net_inr"] = df["gross_inr"] - df["real_costs_inr"]
    df["real_net_pct"] = df["real_net_inr"] / (df["entry"] * df["qty"]) * 100.0
    return df


def pair(base: pd.DataFrame, cost: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    kb = set(map(tuple, base[KEY].astype(str).to_numpy()))
    kc = set(map(tuple, cost[KEY].astype(str).to_numpy()))
    m = base.merge(cost, on=KEY, suffixes=("_b", "_c"))
    return m, len(kb - kc), len(kc - kb)


def describe_changes(m: pd.DataFrame) -> None:
    d_real = m["real_net_inr_c"] - m["real_net_inr_b"]
    changed = m[(m["exit_reason_b"] != m["exit_reason_c"])
                | ((m["exit_b"] - m["exit_c"]).abs() > 1e-6)].copy()
    changed["d_real"] = changed["real_net_inr_c"] - changed["real_net_inr_b"]
    print(f"  exits changed            : {len(changed)} of {len(m)} "
          f"({len(changed) / len(m) * 100:.1f}%)")
    if not len(changed):
        return
    # Where did the base arm exit those trades?  A trail_stop within a tick of
    # the entry price is the old breakeven scratch the rule is meant to fix.
    scratch = changed[(changed["exit_reason_b"] == "trail_stop")
                      & ((changed["exit_b"] - changed["entry_b"]).abs() <= TICK + 1e-9)]
    cut = changed[(changed["exit_reason_c"] == "trail_stop") & (changed["d_real"] < -0.005)]
    saved = changed[changed["d_real"] > 0.005]
    print(f"  base exited AT ENTRY (scratch) : {len(scratch):4d}  "
          f"delta {scratch['d_real'].sum():+,.0f} INR")
    print(f"  cost arm did BETTER      : {len(saved):4d}  "
          f"total {saved['d_real'].sum():+,.0f} INR  mean {saved['d_real'].mean():+,.1f}")
    worse = changed[changed["d_real"] < -0.005]
    print(f"  cost arm did WORSE       : {len(worse):4d}  "
          f"total {worse['d_real'].sum():+,.0f} INR  mean {worse['d_real'].mean():+,.1f}")
    print(f"    of which cut by the cost stop (trail_stop in cost arm): {len(cut)}  "
          f"total {cut['d_real'].sum():+,.0f} INR")
    print("  base exit reasons of changed trades → delta INR:")
    for reason, grp in changed.groupby("exit_reason_b"):
        print(f"    {reason:20s} {len(grp):4d}   {grp['d_real'].sum():+,.0f}")
    print("  exit reasons, base vs cost (all shared trades):")
    vb = m["exit_reason_b"].value_counts()
    vc = m["exit_reason_c"].value_counts()
    for reason in sorted(set(vb.index) | set(vc.index)):
        print(f"    {reason:20s} {int(vb.get(reason, 0)):5d} → {int(vc.get(reason, 0)):5d}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2023,2024")
    ap.add_argument("--prefix", default="cs")
    a = ap.parse_args()
    years = [int(y) for y in a.years.split(",")]

    print("=" * 92)
    print("BT43 — cost-aware breakeven stop vs plain breakeven lock, attention arm")
    print("=" * 92)
    per_year: dict[int, pd.DataFrame] = {}
    for y in years:
        base, cost = load(a.prefix, y, "base"), load(a.prefix, y, "cost")
        m, only_b, only_c = pair(base, cost)
        per_year[y] = m
        d_real = m["real_net_inr_c"] - m["real_net_inr_b"]
        d_stress = m["net_inr_c"] - m["net_inr_b"]
        t, p = stats.ttest_rel(m["real_net_inr_c"], m["real_net_inr_b"])
        print(f"\n{y}: base {len(base)} trades, cost {len(cost)} trades, shared {len(m)}"
              f"  (only base {only_b}, only cost {only_c})")
        if only_b or only_c:
            print("  ⚠ entry sets differ — an exit change fed the reclaim re-entry path;"
                  " everything below is paired on shared entries only")
        print(f"  gross %/trade      base {m['gross_pct_b'].mean():+.4f}   "
              f"cost {m['gross_pct_c'].mean():+.4f}   "
              f"delta {m['gross_pct_c'].mean() - m['gross_pct_b'].mean():+.4f} pp")
        print(f"  REAL net INR/trade base {m['real_net_inr_b'].mean():+8.1f}   "
              f"cost {m['real_net_inr_c'].mean():+8.1f}   delta {d_real.mean():+7.2f}"
              f"   t={t:+.2f} p={p:.3f}")
        print(f"  REAL net %/trade   base {m['real_net_pct_b'].mean():+.4f}   "
              f"cost {m['real_net_pct_c'].mean():+.4f}")
        print(f"  stressed net/trade base {m['net_inr_b'].mean():+8.1f}   "
              f"cost {m['net_inr_c'].mean():+8.1f}   delta {d_stress.mean():+7.2f}")
        print(f"  win rate (real net>0) base {(m['real_net_inr_b'] > 0).mean() * 100:.2f}%"
              f"   cost {(m['real_net_inr_c'] > 0).mean() * 100:.2f}%")
        describe_changes(m)

    pooled = pd.concat(per_year.values(), ignore_index=True)
    d = pooled["real_net_inr_c"] - pooled["real_net_inr_b"]
    t, p = stats.ttest_rel(pooled["real_net_inr_c"], pooled["real_net_inr_b"])
    se = d.std(ddof=1) / np.sqrt(len(d))
    lo, hi = d.mean() - 1.96 * se, d.mean() + 1.96 * se
    print("\n" + "-" * 92)
    print(f"POOLED  shared trades {len(pooled)}   paired REAL net delta "
          f"{d.mean():+.2f} INR/trade   95% CI [{lo:+.2f}, {hi:+.2f}]   t={t:+.2f}  p={p:.4f}")
    print(f"        total delta {d.sum():+,.0f} INR over {len(pooled)} trades")

    c1 = d.mean() > 0 and p < 0.05
    c2 = d.mean() >= MIN_DELTA_INR
    yearly = {y: (m["real_net_inr_c"] - m["real_net_inr_b"]).mean() for y, m in per_year.items()}
    c3 = all(v > 0 for v in yearly.values())
    c4 = all(len(m) >= MIN_SHARED for m in per_year.values())
    print("\nLOCKED CRITERIA")
    print(f"  C1 pooled delta > 0 and p < 0.05        : {'PASS' if c1 else 'FAIL'}"
          f"   (delta {d.mean():+.2f}, p={p:.4f})")
    print(f"  C2 pooled delta >= +{MIN_DELTA_INR:.0f} INR/trade      : {'PASS' if c2 else 'FAIL'}")
    print(f"  C3 delta > 0 in every year              : {'PASS' if c3 else 'FAIL'}   "
          + "  ".join(f"{y}: {v:+.2f}" for y, v in yearly.items()))
    print(f"  C4 >= {MIN_SHARED} shared trades every year   : "
          f"{'PASS' if c4 else 'VOID'}   "
          + "  ".join(f"{y}: {len(m)}" for y, m in per_year.items()))
    if not c4:
        verdict = "VOID (sample too small)"
    elif c1 and c2 and c3:
        verdict = "NOT KILLED — all locked criteria pass (spent windows: not a ship)"
    else:
        verdict = "KILL"
    print(f"\nVERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
