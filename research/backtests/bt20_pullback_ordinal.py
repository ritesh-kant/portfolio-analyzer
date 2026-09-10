# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy"]
# ///
"""BT20 — pullback-ordinal split
(hypothesis: research/hypotheses/2026-09-06-pullback-ordinal.md §4).

Observational split of the trades BT17 already took, by which pullback of the
day's move the entry sat on. Nothing is filtered: `engine.py` records
`pullback_ord` on every candidate and gates nothing, so the trade set here is
identical to the fill-latency baseline (992 trades on 2024).

All four LOCKED criteria must pass. Any one failing is a KILL.

  PRIMARY-spread : gross%(ord 1-3) − gross%(ord 4+) ≥ +0.50 pp, positive sign
  PRIMARY-level  : gross%(ord 1-3) ≥ +0.30%
  anti           : label-shuffle p(shuffled spread ≥ observed) < 0.10
  n              : ord 4+ group ≥ 30 trades (§3 feasibility precondition)

Usage:
  uv run research/backtests/bt20_pullback_ordinal.py
  uv run research/backtests/bt20_pullback_ordinal.py --trades bt17_trades_pbord.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent

# --- LOCKED (hypothesis §3, §4) ---
CUT = 3                 # ordinal 1..CUT is the "keep" side; CUT+1.. is the comparison
SPREAD_FLOOR = 0.50     # percentage points
LEVEL_FLOOR = 0.30      # percent gross on the keep side
ANTI_ALPHA = 0.10
MIN_N_LATE = 30
SHUFFLES = 5000
SEED = 20260906


def _summ(g: pd.DataFrame) -> pd.Series:
    n = len(g)
    net = g["net_pct"]
    return pd.Series({
        "n": n,
        "win%": (g["net_inr"] > 0).mean() * 100,
        "gross%/tr": g["gross_pct"].mean(),
        "net%/tr": net.mean(),
        "net_inr": g["net_inr"].sum(),
        "target%": (g["exit_reason"] == "target").mean() * 100,
        "stop%": (g["exit_reason"] == "stop").mean() * 100,
    })


def run(path: Path) -> int:
    tr = pd.read_csv(path)
    if "pullback_ord" not in tr.columns:
        print(f"bt20: {path.name} has no pullback_ord column — re-run bt17 after the engine change.")
        return 1
    pd.set_option("display.width", 170)
    pd.set_option("display.float_format", lambda x: f"{x:,.3f}")

    total = len(tr)
    anchored = tr[tr["pullback_ord"].notna()].copy()
    anchored["pullback_ord"] = anchored["pullback_ord"].astype(int)
    unanchored = tr[tr["pullback_ord"].isna()]

    print("=" * 78)
    print(f"BT20 — pullback-ordinal split | {path.name} | {total} trades")
    print("  observational split, no filtering; ordinal recorded by engine.py, gated by nothing")
    print("=" * 78)
    print(f"\nanchored (a pole formed before entry): {len(anchored)}"
          f"   no-anchor bucket (excluded from primary, §0): {len(unanchored)}")
    if len(unanchored):
        print(f"  no-anchor gross%/tr {unanchored['gross_pct'].mean():+.3f}  "
              f"net%/tr {unanchored['net_pct'].mean():+.3f}")

    print("\ndistribution of pullback ordinal:")
    print(anchored["pullback_ord"].value_counts().sort_index().to_string())

    print("\nper ordinal (stressed):")
    print(anchored.groupby("pullback_ord").apply(_summ, include_groups=False).to_string())

    keep = anchored[anchored["pullback_ord"] <= CUT]
    late = anchored[anchored["pullback_ord"] > CUT]
    print(f"\nkeep (ordinal 1-{CUT}) vs late (ordinal {CUT + 1}+):")
    print(pd.DataFrame({f"1-{CUT}": _summ(keep), f"{CUT + 1}+": _summ(late)}).T.to_string())

    # ---- feasibility precondition (§3) ----
    if len(late) < MIN_N_LATE:
        print("\n" + "=" * 78)
        print(f"  n(ordinal {CUT + 1}+) = {len(late)} < {MIN_N_LATE} required by §3.")
        print("  >>> NOT TESTABLE on this data. Per the locked rules this may NOT be rescued")
        print("      by moving the cut, merging buckets, or widening the window.")
        print("=" * 78)
        return 0

    spread = keep["gross_pct"].mean() - late["gross_pct"].mean()
    level = keep["gross_pct"].mean()

    rng = np.random.default_rng(SEED)
    g = anchored["gross_pct"].to_numpy()
    k = len(keep)
    ge = 0
    for _ in range(SHUFFLES):
        idx = rng.permutation(len(g))
        ge += int(g[idx][:k].mean() - g[idx][k:].mean() >= spread)
    anti_p = ge / SHUFFLES

    def v(ok: bool) -> str:
        return "PASS" if ok else "FAIL -> KILL"

    ok_spread = spread >= SPREAD_FLOOR
    ok_level = level >= LEVEL_FLOOR
    ok_anti = anti_p < ANTI_ALPHA
    ok_n = len(late) >= MIN_N_LATE

    print("\n" + "=" * 78)
    print(f"BT20 verdict | n_keep={len(keep)} n_late={len(late)}")
    print("=" * 78)
    print(f"  PRIMARY-spread  gross(1-{CUT}) − gross({CUT + 1}+) : {spread:+.4f} pp "
          f"(≥ {SPREAD_FLOOR:+.2f})   {v(ok_spread)}")
    print(f"  PRIMARY-level   gross(1-{CUT})                  : {level:+.4f} %  "
          f"(≥ {LEVEL_FLOOR:+.2f})   {v(ok_level)}")
    print(f"  anti            p(shuffled ≥ observed)        : {anti_p:.4f}    "
          f"(< {ANTI_ALPHA})     {v(ok_anti)}")
    print(f"  n               ordinal {CUT + 1}+ trades            : {len(late)}       "
          f"(≥ {MIN_N_LATE})       {v(ok_n)}")
    allpass = ok_spread and ok_level and ok_anti and ok_n
    print("\n  >>> " + ("ALL DEV CRITERIA PASS -> open the 2025 hold-out ONCE (not live money)"
                        if allpass else
                        "KILL — a locked criterion failed; no second look, no moving the cut"))

    print("\nsecondary (observations, NOT filters):")
    print("\nby setup within keep:")
    print(keep.groupby("setup").apply(_summ, include_groups=False)
          .sort_values("n", ascending=False).to_string())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="bt17_trades_pbord.csv")
    a = ap.parse_args()
    p = Path(a.trades)
    return run(p if p.is_absolute() else HERE / p)


if __name__ == "__main__":
    sys.exit(main())
