"""BT46 — lift the breakeven stop at 1.5R instead of 1.0R?

Pairs two bt17 arms per year on identical entries (date, symbol, entry_time):

  ctl  = stop lifted to the ENTRY price once the trade is up 1.0R (frozen default)
  r15  = same lift, but only once the trade is up 1.5R  (--breakeven-at-r 1.5)

Only the threshold moves. The stop still goes to the entry price (the
cost-covering variant was killed by BT43 and is excluded by construction), the
0.5R arming of the trend exits is untouched, and sizing/entries/fills are frozen.

Prints the four locked criteria of
research/hypotheses/2026-09-20-breakeven-at-1p5r.md:

  C1  pooled paired mean net delta at REAL costs > 0, paired t-test p < 0.05
  C2  same sign in 2023 and 2024 separately
  C3  >= 1000 paired trades AND >= 150 trades whose exit actually moved (pooled)
  C4  deployed-capital ratio r15/ctl >= 0.95   (the BT44 guard)

"Real" net strips bt17's 40 bps/side research stress back out of costs_inr so
the number is what a Zerodha MIS round trip actually pays (~0.21%).

⚠️ 2023 and 2024 are SPENT windows. This can KILL but cannot BLESS: passing all
four criteria means "not killed", not "ship it".

Usage:
  apps/signal-engine/.venv/bin/python research/backtests/bt46_breakeven_at_1p5r.py
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
MIN_PAIRED = 1000
MIN_CHANGED = 150
MIN_CAPITAL_RATIO = 0.95


def load(prefix: str, year: int, arm: str) -> pd.DataFrame:
    p = HERE / f"bt17_trades_{prefix}{str(year)[2:]}_{arm}.csv"
    if not p.exists():
        raise SystemExit(f"missing {p.name} — run bt17 for that arm first")
    df = pd.read_csv(p)
    stress = (df["entry"] + df["exit"]) * df["qty"] * STRESS_SLIP
    df["real_costs_inr"] = df["costs_inr"] - stress
    df["real_net_inr"] = df["gross_inr"] - df["real_costs_inr"]
    df["real_net_pct"] = df["real_net_inr"] / (df["entry"] * df["qty"]) * 100.0
    df["capital_inr"] = df["entry"] * df["qty"]
    return df


def pair(ctl: pd.DataFrame, r15: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    kc = set(map(tuple, ctl[KEY].astype(str).to_numpy()))
    kt = set(map(tuple, r15[KEY].astype(str).to_numpy()))
    m = ctl.merge(r15, on=KEY, suffixes=("_c", "_t"))
    return m, len(kc - kt), len(kt - kc)


def changed_rows(m: pd.DataFrame) -> pd.DataFrame:
    ch = m[(m["exit_reason_c"] != m["exit_reason_t"])
           | ((m["exit_c"] - m["exit_t"]).abs() > 1e-6)].copy()
    ch["d_real"] = ch["real_net_inr_t"] - ch["real_net_inr_c"]
    return ch


def describe(m: pd.DataFrame) -> None:
    ch = changed_rows(m)
    print(f"  exits changed            : {len(ch)} of {len(m)} "
          f"({len(ch) / len(m) * 100:.1f}%)")
    if not len(ch):
        return
    better, worse = ch[ch["d_real"] > 0.005], ch[ch["d_real"] < -0.005]
    print(f"  1.5R arm did BETTER      : {len(better):4d}  "
          f"total {better['d_real'].sum():+,.0f} INR  mean {better['d_real'].mean():+,.1f}")
    print(f"  1.5R arm did WORSE       : {len(worse):4d}  "
          f"total {worse['d_real'].sum():+,.0f} INR  mean {worse['d_real'].mean():+,.1f}")
    # The population the rule is meant to rescue: control scratched at entry.
    scratch = ch[(ch["exit_reason_c"] == "trail_stop")
                 & ((ch["exit_c"] - ch["entry_c"]).abs() <= TICK + 1e-9)]
    print(f"  ctl scratched AT ENTRY   : {len(scratch):4d}  "
          f"delta {scratch['d_real'].sum():+,.0f} INR"
          + (f"  mean {scratch['d_real'].mean():+,.1f}" if len(scratch) else ""))
    # The population it puts at risk: unprotected, now riding the hard stop.
    to_stop = ch[(ch["exit_reason_c"] != "stop") & (ch["exit_reason_t"] == "stop")]
    print(f"  ctl protected → r15 full STOP : {len(to_stop):4d}  "
          f"delta {to_stop['d_real'].sum():+,.0f} INR"
          + (f"  mean {to_stop['d_real'].mean():+,.1f}" if len(to_stop) else ""))
    print("  ctl exit reasons of changed trades → delta INR:")
    for reason, grp in ch.groupby("exit_reason_c"):
        print(f"    {reason:20s} {len(grp):4d}   {grp['d_real'].sum():+,.0f}")
    print("  exit reasons, ctl → r15 (all shared trades):")
    vc, vt = m["exit_reason_c"].value_counts(), m["exit_reason_t"].value_counts()
    for reason in sorted(set(vc.index) | set(vt.index)):
        print(f"    {reason:20s} {int(vc.get(reason, 0)):5d} → {int(vt.get(reason, 0)):5d}")
    print(f"  mean winner (gross>0)  ctl {m.loc[m.gross_pct_c > 0, 'gross_pct_c'].mean():+.3f}%"
          f"   r15 {m.loc[m.gross_pct_t > 0, 'gross_pct_t'].mean():+.3f}%")
    print(f"  mean loser  (gross<0)  ctl {m.loc[m.gross_pct_c < 0, 'gross_pct_c'].mean():+.3f}%"
          f"   r15 {m.loc[m.gross_pct_t < 0, 'gross_pct_t'].mean():+.3f}%")
    print(f"  gross win rate         ctl {(m.gross_pct_c > 0).mean() * 100:.2f}%"
          f"   r15 {(m.gross_pct_t > 0).mean() * 100:.2f}%")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2023,2024")
    ap.add_argument("--prefix", default="be")
    a = ap.parse_args()
    years = [int(y) for y in a.years.split(",")]

    print("=" * 92)
    print("BT46 — breakeven lift at 1.5R vs 1.0R, attention arm (stop → entry price)")
    print("=" * 92)
    per_year: dict[int, pd.DataFrame] = {}
    for y in years:
        ctl, r15 = load(a.prefix, y, "ctl"), load(a.prefix, y, "r15")
        m, only_c, only_t = pair(ctl, r15)
        per_year[y] = m
        d_real = m["real_net_inr_t"] - m["real_net_inr_c"]
        t, p = stats.ttest_rel(m["real_net_inr_t"], m["real_net_inr_c"])
        print(f"\n{y}: ctl {len(ctl)} trades, r15 {len(r15)} trades, shared {len(m)}"
              f"  (only ctl {only_c}, only r15 {only_t})")
        if only_c or only_t:
            print("  ⚠ entry sets differ — an exit change fed the reclaim re-entry path;"
                  " everything below is paired on shared entries only")
        print(f"  gross %/trade      ctl {m['gross_pct_c'].mean():+.4f}   "
              f"r15 {m['gross_pct_t'].mean():+.4f}   "
              f"delta {m['gross_pct_t'].mean() - m['gross_pct_c'].mean():+.4f} pp")
        print(f"  REAL net INR/trade ctl {m['real_net_inr_c'].mean():+8.1f}   "
              f"r15 {m['real_net_inr_t'].mean():+8.1f}   delta {d_real.mean():+7.2f}"
              f"   t={t:+.2f} p={p:.4f}")
        print(f"  stressed net/trade ctl {m['net_inr_c'].mean():+8.1f}   "
              f"r15 {m['net_inr_t'].mean():+8.1f}   "
              f"delta {(m['net_inr_t'] - m['net_inr_c']).mean():+7.2f}")
        describe(m)

    pooled = pd.concat(per_year.values(), ignore_index=True)
    d = pooled["real_net_inr_t"] - pooled["real_net_inr_c"]
    t, p = stats.ttest_rel(pooled["real_net_inr_t"], pooled["real_net_inr_c"])
    se = d.std(ddof=1) / np.sqrt(len(d))
    lo, hi = d.mean() - 1.96 * se, d.mean() + 1.96 * se
    n_changed = sum(len(changed_rows(m)) for m in per_year.values())
    ratio = pooled["capital_inr_t"].sum() / pooled["capital_inr_c"].sum()
    print("\n" + "-" * 92)
    print(f"POOLED  shared trades {len(pooled)}   paired REAL net delta "
          f"{d.mean():+.2f} INR/trade   95% CI [{lo:+.2f}, {hi:+.2f}]   t={t:+.2f}  p={p:.4f}")
    print(f"        total delta {d.sum():+,.0f} INR   exits changed {n_changed}")
    print(f"        capital deployed ratio r15/ctl {ratio:.4f}")

    yearly = {y: (m["real_net_inr_t"] - m["real_net_inr_c"]).mean()
              for y, m in per_year.items()}
    c1 = bool(d.mean() > 0 and p < 0.05)
    c2 = (all(v > 0 for v in yearly.values()) or all(v < 0 for v in yearly.values()))
    c3 = bool(len(pooled) >= MIN_PAIRED and n_changed >= MIN_CHANGED)
    c4 = bool(ratio >= MIN_CAPITAL_RATIO)
    print("\nLOCKED CRITERIA (registered 2026-09-20, before the run)")
    print(f"  C1 pooled delta > 0 and p < 0.05        : {'PASS' if c1 else 'FAIL'}"
          f"   (delta {d.mean():+.2f}, p={p:.4f})")
    print(f"  C2 same sign in both years              : {'PASS' if c2 else 'FAIL'}   "
          + "  ".join(f"{y}: {v:+.2f}" for y, v in yearly.items()))
    print(f"  C3 >= {MIN_PAIRED} paired and >= {MIN_CHANGED} changed  : "
          f"{'PASS' if c3 else 'VOID'}   (paired {len(pooled)}, changed {n_changed})")
    print(f"  C4 capital ratio >= {MIN_CAPITAL_RATIO}              : "
          f"{'PASS' if c4 else 'FAIL'}   ({ratio:.4f})")
    if not c3:
        verdict = "VOID (sample too small)"
    elif not c4:
        verdict = ("KILL — the arms do not deploy comparable capital, so INR/trade "
                   "is not measuring the rule")
    elif c1 and c2:
        verdict = ("NOT KILLED — locked criteria pass. Spent windows: this is NOT a "
                   "ship, it needs an unspent/forward window.")
    else:
        verdict = "KILL"
    print(f"\nVERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
