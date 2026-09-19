"""BT42 — did the two-close false break + stop-first change help on 2024?

Answers three questions for the 2x2 of the two independent edits:

  profit    total and per-trade gross/net INR and %
  firing    how often the false-break "bull trap" exit fired at all
  accuracy  win rate, and how often the exit was PREMATURE

Premature is measured by pairing the identical trade across arms: if the same
(date, symbol, entry) exits as `false_break` in one arm and the other arm's
later exit made more money, the earlier exit gave money away. That is the
closest available measure of a false positive for an EXIT detector.

⚠ This is an exit rule. It cannot change which entries were taken, so it
cannot reduce false-positive ENTRIES. It can only change what an open trade
does next — except through the reclaim path, which is checked explicitly.

Usage:
  apps/signal-engine/.venv/bin/python research/backtests/bt42_false_break_2024.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent

ARMS = {
    "A current (two-close, stop-first)": "fb24_new",
    "B legacy (one-close, pre-stop)": "fb24_old",
    "C one-close only": "fb24_oneclose",
    "D pre-stop ordering only": "fb24_prestop",
}
KEY = ["date", "symbol", "entry_time"]


def load(tag: str) -> pd.DataFrame:
    p = HERE / f"bt17_trades_{tag}.csv"
    if not p.exists():
        raise SystemExit(f"missing {p.name} — run the four bt17 arms first")
    return pd.read_csv(p)


def summarize(name: str, df: pd.DataFrame) -> dict:
    wins = (df["net_inr"] > 0).sum()
    return {
        "arm": name,
        "trades": len(df),
        "win%": round(wins / len(df) * 100, 2) if len(df) else 0.0,
        "gross%/tr": round(df["gross_pct"].mean(), 4),
        "net%/tr": round(df["net_pct"].mean(), 4),
        "net_inr": round(df["net_inr"].sum(), 0),
        "net_inr/tr": round(df["net_inr"].mean(), 1),
        "false_break_exits": int((df["exit_reason"] == "false_break").sum()),
        "fb%": round((df["exit_reason"] == "false_break").mean() * 100, 1),
    }


def main() -> int:
    frames = {name: load(tag) for name, tag in ARMS.items()}
    print("=" * 96)
    print("BT42 — false-break rule change, NSE 2024 full year, attention arm")
    print("=" * 96)
    table = pd.DataFrame([summarize(n, d) for n, d in frames.items()])
    print(table.to_string(index=False))

    a = frames["A current (two-close, stop-first)"]
    b = frames["B legacy (one-close, pre-stop)"]

    # 1. did the entry set move at all? (the reclaim path is the only channel)
    ka, kb = [set(map(tuple, d[KEY].astype(str).to_numpy())) for d in (a, b)]
    print("\n" + "-" * 96)
    print("ENTRIES")
    print(f"  identical in both arms : {len(ka & kb)}")
    print(f"  only in current arm    : {len(ka - kb)}")
    print(f"  only in legacy arm     : {len(kb - ka)}")
    if ka != kb:
        print("  ⚠ entry sets differ — a changed false-break classification feeds the")
        print("    reclaim re-entry path, so this is not a pure exit-only comparison.")

    # 2. profit and accuracy on the SHARED trades only (apples to apples)
    m = a.merge(b, on=KEY, suffixes=("_new", "_old"))
    print("\n" + "-" * 96)
    print(f"SHARED TRADES ({len(m)}) — identical entries, exits may differ")
    changed = m[(m["exit_reason_new"] != m["exit_reason_old"])
                | ((m["exit_new"] - m["exit_old"]).abs() > 1e-6)]
    delta = m["net_inr_new"] - m["net_inr_old"]
    print(f"  exits changed          : {len(changed)} of {len(m)}")
    print(f"  net INR delta (new-old): {delta.sum():+,.0f}   per changed trade "
          f"{(delta.sum() / len(changed)) if len(changed) else 0:+,.1f}")
    print(f"  win rate  old {(m['net_inr_old'] > 0).mean() * 100:5.2f}%   "
          f"new {(m['net_inr_new'] > 0).mean() * 100:5.2f}%")
    print(f"  gross%/tr old {m['gross_pct_old'].mean():+.4f}   "
          f"new {m['gross_pct_new'].mean():+.4f}")

    # 3. was the legacy false-break exit premature?
    legacy_fb = m[m["exit_reason_old"] == "false_break"]
    if len(legacy_fb):
        d = legacy_fb["net_inr_new"] - legacy_fb["net_inr_old"]
        better, worse = (d > 0.005).sum(), (d < -0.005).sum()
        print("\n" + "-" * 96)
        print(f"WAS THE LEGACY false_break EXIT PREMATURE?  ({len(legacy_fb)} such trades)")
        print(f"  holding longer earned MORE (exit was premature) : {better:4d}"
              f"  ({better / len(legacy_fb) * 100:.1f}%)  total {d[d > 0].sum():+,.0f} INR")
        print(f"  holding longer earned LESS (exit was right)     : {worse:4d}"
              f"  ({worse / len(legacy_fb) * 100:.1f}%)  total {d[d < 0].sum():+,.0f} INR")
        print(f"  unchanged                                       : "
              f"{len(legacy_fb) - better - worse:4d}")
        print(f"  net effect of relaxing the rule                 : {d.sum():+,.0f} INR")
        print("\n  where those trades ended up under the current rule:")
        for reason, n in legacy_fb["exit_reason_new"].value_counts().items():
            sub = legacy_fb[legacy_fb["exit_reason_new"] == reason]
            dd = (sub["net_inr_new"] - sub["net_inr_old"]).sum()
            print(f"    {reason:20s} {n:4d}   net delta {dd:+,.0f} INR")

    # 4. attribute each half independently
    print("\n" + "-" * 96)
    print("ATTRIBUTION (net INR per trade, all trades in each arm)")
    base = frames["B legacy (one-close, pre-stop)"]["net_inr"].mean()
    for name, df in frames.items():
        print(f"  {name:38s} {df['net_inr'].mean():+8.1f}   "
              f"vs legacy {df['net_inr'].mean() - base:+7.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
