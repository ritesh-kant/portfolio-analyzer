"""BT44 — does buying a second tranche once a trade reaches 1R pay for itself?

Two arms, both compared against the COST-AWARE-STOP arm (not the plain base),
so the add-on is isolated from the stop change tested in BT43:

  P  full size + add an equal-risk tranche at 1R   -> up to 2x the capital
  H  half size at entry + the other half at 1R     -> about today's capital

Locked criteria live in research/hypotheses/2026-09-19-add-to-winner-at-1r.md.
P is judged primarily on the ADD-ON TRANCHE'S OWN net P&L, which no amount of
extra capital can flatter. H is judged on total net per trade plus a mechanism
check: the gain must come from cheaper losers, not from bigger winners.

"Real" net strips bt17's 40 bps/side research stress back out, per tranche, so
the figures are what a Zerodha MIS round trip actually pays (~0.21%).

Usage:
  apps/signal-engine/.venv/bin/python research/backtests/bt44_add_to_winner.py
  apps/signal-engine/.venv/bin/python research/backtests/bt44_add_to_winner.py \
      --years 2023,2024 --control cost --arms pyr,half
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
STRESS_SLIP = 0.0040
MIN_SHARED = 500
MIN_ADDS = 300
MIN_DELTA_INR = 10.0


def load(prefix: str, year: int, arm: str) -> pd.DataFrame:
    p = HERE / f"bt17_trades_{prefix}{str(year)[2:]}_{arm}.csv"
    if not p.exists():
        raise SystemExit(f"missing {p.name} — run that bt17 arm first")
    df = pd.read_csv(p)
    for col, default in (("add_qty", 0), ("add_entry", 0.0),
                         ("base_net_inr", np.nan), ("add_net_inr", 0.0)):
        if col not in df.columns:
            df[col] = default
    df["add_qty"] = df["add_qty"].fillna(0).astype(int)
    df["add_entry"] = df["add_entry"].fillna(0.0)
    df["add_net_inr"] = df["add_net_inr"].fillna(0.0)
    # Stress was charged per tranche at exit, so it is removed per tranche here.
    base_stress = (df["entry"] + df["exit"]) * df["qty"] * STRESS_SLIP
    add_stress = (df["add_entry"] + df["exit"]) * df["add_qty"] * STRESS_SLIP
    df["real_net_inr"] = df["gross_inr"] - (df["costs_inr"] - base_stress - add_stress)
    df["real_add_net_inr"] = df["add_net_inr"] + add_stress
    df["real_base_net_inr"] = df["real_net_inr"] - df["real_add_net_inr"]
    df["capital_inr"] = df["entry"] * df["qty"] + df["add_entry"] * df["add_qty"]
    df["real_net_pct"] = df["real_net_inr"] / df["capital_inr"] * 100.0
    df["added"] = df["add_qty"] > 0
    return df


def pair(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    return a.merge(b, on=KEY, suffixes=("_c", "_t"))


def arm_p(per_year: dict[int, pd.DataFrame]) -> None:
    """P1-P4: is the add-on tranche a profitable trade on its own?"""
    print("\n" + "=" * 92)
    print("ARM P — full size + an equal-risk add-on at 1R   (up to 2x capital)")
    print("=" * 92)
    adds_by_year, pooled = {}, []
    for y, m in per_year.items():
        added = m[m["added_t"]]
        adds_by_year[y] = added
        pooled.append(added)
        if not len(added):
            print(f"{y}: no adds")
            continue
        d = added["real_add_net_inr_t"]
        t, p = stats.ttest_1samp(d, 0.0)
        print(f"\n{y}: {len(added)} of {len(m)} trades added "
              f"({len(added) / len(m) * 100:.1f}%)")
        print(f"  add-on net INR   mean {d.mean():+8.2f}   total {d.sum():+,.0f}"
              f"   t={t:+.2f} p={p:.4f}")
        print(f"  add-on win rate  {(d > 0).mean() * 100:.2f}%   "
              f"median {d.median():+.1f}")
        print(f"  add price vs entry  mean {((added['add_entry_t'] / added['entry_t'] - 1) * 100).mean():+.3f}%")
        print(f"  add qty vs first    mean {(added['add_qty_t'] / added['qty_t']).mean():.2f}x")
        print(f"  first tranche net on the SAME trades "
              f"{added['real_base_net_inr_t'].mean():+8.2f}")
    allf = pd.concat(pooled, ignore_index=True)
    d = allf["real_add_net_inr_t"]
    t, p = stats.ttest_1samp(d, 0.0)
    se = d.std(ddof=1) / np.sqrt(len(d))
    print("\n" + "-" * 92)
    print(f"POOLED add-ons {len(d)}   mean {d.mean():+.2f} INR   "
          f"95% CI [{d.mean() - 1.96 * se:+.2f}, {d.mean() + 1.96 * se:+.2f}]   "
          f"t={t:+.2f} p={p:.4f}")

    # P2 — total net per trade must not be worse than the control
    tot_c = np.concatenate([m["real_net_inr_c"].to_numpy() for m in per_year.values()])
    tot_t = np.concatenate([m["real_net_inr_t"].to_numpy() for m in per_year.values()])
    dt = tot_t - tot_c
    t2, p2 = stats.ttest_rel(tot_t, tot_c)
    print(f"TOTAL net/trade   control {tot_c.mean():+8.2f}   arm P {tot_t.mean():+8.2f}"
          f"   delta {dt.mean():+7.2f}  t={t2:+.2f} p={p2:.4f}")
    print("  (descriptive only — arm P deploys up to twice the capital)")

    p1 = d.mean() > 0 and p < 0.05
    p2ok = dt.mean() >= 0
    p3 = all(len(v) and v["real_add_net_inr_t"].mean() > 0 for v in adds_by_year.values())
    p4 = all(len(v) >= MIN_ADDS for v in adds_by_year.values())
    print("\nLOCKED CRITERIA — ARM P")
    print(f"  P1 add-on net > 0, p < 0.05     : {'PASS' if p1 else 'FAIL'}"
          f"   (mean {d.mean():+.2f}, p={p:.4f})")
    print(f"  P2 total net not worse           : {'PASS' if p2ok else 'FAIL'}"
          f"   (delta {dt.mean():+.2f})")
    print(f"  P3 add-on net > 0 in every year  : {'PASS' if p3 else 'FAIL'}   "
          + "  ".join(f"{y}: {v['real_add_net_inr_t'].mean():+.2f}"
                      for y, v in adds_by_year.items() if len(v)))
    print(f"  P4 >= {MIN_ADDS} adds every year        : {'PASS' if p4 else 'VOID'}   "
          + "  ".join(f"{y}: {len(v)}" for y, v in adds_by_year.items()))
    verdict = ("VOID (too few adds)" if not p4
               else "NOT KILLED (spent windows: not a ship)" if p1 and p2ok and p3
               else "KILL")
    print(f"\nARM P VERDICT: {verdict}")


def arm_h(per_year: dict[int, pd.DataFrame]) -> None:
    """H1-H4: is half-now-half-on-proof better per rupee of capital?"""
    print("\n" + "=" * 92)
    print("ARM H — half size at entry + the other half at 1R   (about today's capital)")
    print("=" * 92)
    yearly = {}
    for y, m in per_year.items():
        d = m["real_net_inr_t"] - m["real_net_inr_c"]
        t, p = stats.ttest_rel(m["real_net_inr_t"], m["real_net_inr_c"])
        yearly[y] = d.mean()
        added = m["added_t"]
        print(f"\n{y}: {len(m)} shared trades, {added.sum()} added "
              f"({added.mean() * 100:.1f}%)")
        print(f"  capital deployed/trade  control {m['capital_inr_c'].mean():>9,.0f}"
              f"   arm H {m['capital_inr_t'].mean():>9,.0f}"
              f"   ({m['capital_inr_t'].mean() / m['capital_inr_c'].mean():.2f}x)")
        print(f"  REAL net INR/trade      control {m['real_net_inr_c'].mean():+8.2f}"
              f"   arm H {m['real_net_inr_t'].mean():+8.2f}"
              f"   delta {d.mean():+7.2f}  t={t:+.2f} p={p:.4f}")
        print(f"  REAL net %/capital      control {m['real_net_pct_c'].mean():+.4f}"
              f"   arm H {m['real_net_pct_t'].mean():+.4f}")
        print(f"  win rate                control {(m['real_net_inr_c'] > 0).mean() * 100:.2f}%"
              f"   arm H {(m['real_net_inr_t'] > 0).mean() * 100:.2f}%")

    pooled = pd.concat(per_year.values(), ignore_index=True)
    d = pooled["real_net_inr_t"] - pooled["real_net_inr_c"]
    t, p = stats.ttest_rel(pooled["real_net_inr_t"], pooled["real_net_inr_c"])
    se = d.std(ddof=1) / np.sqrt(len(d))
    print("\n" + "-" * 92)
    print(f"POOLED {len(pooled)} shared trades   delta {d.mean():+.2f} INR/trade   "
          f"95% CI [{d.mean() - 1.96 * se:+.2f}, {d.mean() + 1.96 * se:+.2f}]   "
          f"t={t:+.2f} p={p:.4f}")

    # H4 mechanism: the gain must come from the trades that never added, i.e.
    # the early failures that arm H deliberately takes at half size.
    never = pooled[~pooled["added_t"]]
    did = pooled[pooled["added_t"]]
    d_never = (never["real_net_inr_t"] - never["real_net_inr_c"]).mean() if len(never) else 0.0
    d_did = (did["real_net_inr_t"] - did["real_net_inr_c"]).mean() if len(did) else 0.0
    share_never = (d_never * len(never)) / (d.mean() * len(pooled)) if d.mean() else 0.0
    print(f"\nMECHANISM  trades that never added ({len(never)}): delta {d_never:+.2f}"
          f"   |  trades that added ({len(did)}): delta {d_did:+.2f}")
    print(f"           share of the total gain coming from the never-added set: "
          f"{share_never * 100:.1f}%")
    if len(never):
        print(f"           control loss on never-added trades "
              f"{never['real_net_inr_c'].mean():+.2f} -> arm H "
              f"{never['real_net_inr_t'].mean():+.2f}")

    # PREMISE CHECK. H1 counts rupees per trade, which is only a fair measure if
    # the arm deploys comparable capital. The hypothesis asserted it did "by
    # construction". It does not: only the trades that reach 1R are ever topped
    # up, so an arm that starts at half size stays at half size on most trades.
    # When this premise fails, H1 rewards trading smaller, not trading better.
    ratio = pooled["capital_inr_t"].sum() / pooled["capital_inr_c"].sum()
    size_only = -pooled["real_net_inr_c"].mean() * (1.0 - ratio)
    print("\n" + "-" * 92)
    print("PREMISE CHECK — is this arm capital-comparable?")
    print(f"  capital deployed  arm H / control = {ratio:.3f}")
    print(f"  delta predicted by SIZE ALONE (control loss x (1 - ratio)) : {size_only:+.2f} INR/trade")
    print(f"  delta observed                                             : {d.mean():+.2f} INR/trade")
    print(f"  attributable to anything other than size                   : "
          f"{d.mean() - size_only:+.2f} INR/trade")
    print("\nSIZE-NEUTRAL MEASURE — net per RUPEE of capital deployed")
    for lbl, sub in (("all", pooled), ("never added", never), ("added", did)):
        if not len(sub):
            continue
        rc = sub["real_net_inr_c"].sum() / sub["capital_inr_c"].sum() * 100
        rt = sub["real_net_inr_t"].sum() / sub["capital_inr_t"].sum() * 100
        print(f"  {lbl:12s} n={len(sub):5d}   control {rc:+.4f}%   arm H {rt:+.4f}%"
              f"   delta {rt - rc:+.4f} pp")
    premise_ok = ratio >= 0.9
    if not premise_ok:
        print("\n  ⚠ PREMISE FAILED: this arm is NOT capital-comparable, so an H1 pass")
        print("    measures position size, not edge. Read the size-neutral rows above.")

    h1 = d.mean() >= MIN_DELTA_INR and p < 0.05
    h2 = all(v > 0 for v in yearly.values())
    h3 = all(len(m) >= MIN_SHARED for m in per_year.values())
    h4 = d_never > 0 and share_never > 0.5
    print("\nLOCKED CRITERIA — ARM H")
    print(f"  H1 delta >= +{MIN_DELTA_INR:.0f} INR and p < 0.05  : {'PASS' if h1 else 'FAIL'}"
          f"   (delta {d.mean():+.2f}, p={p:.4f})")
    print(f"  H2 better in every year          : {'PASS' if h2 else 'FAIL'}   "
          + "  ".join(f"{y}: {v:+.2f}" for y, v in yearly.items()))
    print(f"  H3 >= {MIN_SHARED} shared trades every year : {'PASS' if h3 else 'VOID'}   "
          + "  ".join(f"{y}: {len(m)}" for y, m in per_year.items()))
    print(f"  H4 gain comes from cheaper losers: {'PASS' if h4 else 'FAIL'}"
          f"   (never-added delta {d_never:+.2f}, {share_never * 100:.0f}% of total)")
    print(f"  PREMISE capital ratio >= 0.90    : {'PASS' if premise_ok else 'FAIL'}"
          f"   (ratio {ratio:.3f})")
    verdict = ("VOID (sample too small)" if not h3
               else "KILL — locked criteria pass only because the arm trades smaller; "
                    "its premise of capital comparability is false" if not premise_ok
               else "NOT KILLED (spent windows: not a ship)" if h1 and h2 and h4
               else "KILL")
    print(f"\nARM H VERDICT: {verdict}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2023,2024")
    ap.add_argument("--prefix", default="cs")
    ap.add_argument("--control", default="cost")
    ap.add_argument("--arms", default="pyr,half")
    a = ap.parse_args()
    years = [int(y) for y in a.years.split(",")]
    arms = [x.strip() for x in a.arms.split(",") if x.strip()]

    print("=" * 92)
    print("BT44 — add a second tranche once the trade has paid for itself")
    print(f"control arm = '{a.control}' (cost-aware stop, single tranche)")
    print("=" * 92)

    for arm, runner in (("pyr", arm_p), ("half", arm_h)):
        if arm not in arms:
            continue
        per_year = {}
        for y in years:
            c, t = load(a.prefix, y, a.control), load(a.prefix, y, arm)
            m = pair(c, t)
            only_c = len(c) - len(m)
            only_t = len(t) - len(m)
            if only_c or only_t:
                print(f"\n⚠ {y} {arm}: entry sets differ "
                      f"(control-only {only_c}, arm-only {only_t}). Sizing can drop a "
                      f"trade whose quantity rounds to zero; paired on shared entries.")
            per_year[y] = m
        runner(per_year)
    return 0


if __name__ == "__main__":
    sys.exit(main())
