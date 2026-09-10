# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy"]
# ///
"""BT21 — multi-entry vs single-entry on the same stock
(hypothesis: research/hypotheses/2026-09-06-multi-entry-same-stock.md §3).

Compares two arms of bt17 on the FRESH 2022–2023 dev window:

  single : first setup of the day per symbol wins, then the stock is done
  multi  : every setup that fires is taken, all day, no cap

All four LOCKED criteria must pass. Any one failing is a KILL.

  GATE A money   : stressed net INR per SYMBOL-DAY, multi − single > 0
  GATE B quality : gross%/trade of re-entries − first entries >= +0.30 pp, positive
  anti           : shuffle first/re-entry labels, p(shuffled >= observed) < 0.10
  n              : >= 100 re-entry trades

Gate A is per symbol-day, not per trade, on purpose: the whole point of the
change is taking MORE trades, so a per-trade average would flatter whichever arm
trades less.

Usage:
  uv run research/backtests/bt21_multi_entry.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent

# --- LOCKED (hypothesis §3) ---
QUALITY_FLOOR = 0.30     # percentage points
ANTI_ALPHA = 0.10
MIN_N_REENTRY = 100
SHUFFLES = 5000
SEED = 20260906


def _load(name: str) -> pd.DataFrame:
    p = HERE / name
    if not p.exists():
        raise SystemExit(f"bt21: missing {p.name} — run bt17 for that arm first.")
    df = pd.read_csv(p)
    df["symday"] = df["date"].astype(str) + "|" + df["symbol"].astype(str)
    df = df.sort_values(["symday", "entry_time"]).reset_index(drop=True)
    df["seq"] = df.groupby("symday").cumcount() + 1
    return df


def _arm_summary(df: pd.DataFrame, symdays: int) -> pd.Series:
    return pd.Series({
        "trades": len(df),
        "symbol-days": df["symday"].nunique(),
        "trades/symday": len(df) / max(symdays, 1),
        "gross%/tr": df["gross_pct"].mean(),
        "net%/tr": df["net_pct"].mean(),
        "win%": (df["net_inr"] > 0).mean() * 100,
        "net_inr_total": df["net_inr"].sum(),
        "net_inr/symday": df["net_inr"].sum() / max(symdays, 1),
        "gross_inr/symday": df["gross_inr"].sum() / max(symdays, 1),
    })


def run(single_name: str, multi_name: str) -> int:
    single, multi = _load(single_name), _load(multi_name)
    pd.set_option("display.width", 170)
    pd.set_option("display.float_format", lambda x: f"{x:,.3f}")

    sd_s, sd_m = set(single["symday"]), set(multi["symday"])
    symdays = len(sd_s | sd_m)

    print("=" * 78)
    print(f"BT21 — multi-entry vs single-entry | dev 2022-2023 | {symdays} traded symbol-days")
    print("=" * 78)
    if sd_s != sd_m:
        print(f"  NOTE symbol-day sets differ: single-only {len(sd_s - sd_m)}, "
              f"multi-only {len(sd_m - sd_s)}. Shared denominator ({symdays}) used for both.")

    print("\narm comparison (denominator = every traded symbol-day, both arms):")
    print(pd.DataFrame({"single": _arm_summary(single, symdays),
                        "multi": _arm_summary(multi, symdays)}).T.to_string())

    print("\ntrades per symbol-day in the multi arm:")
    print(multi.groupby("symday").size().value_counts().sort_index().to_string())

    first = multi[multi["seq"] == 1]
    reentry = multi[multi["seq"] > 1]
    print("\nfirst entry vs re-entries (multi arm):")
    print(pd.DataFrame({
        "first": _arm_summary(first, symdays), "re-entry": _arm_summary(reentry, symdays),
    }).T.to_string())

    print("\nby entry number within the day (multi arm):")
    print(multi.groupby("seq").apply(
        lambda g: pd.Series({"n": len(g), "gross%/tr": g["gross_pct"].mean(),
                             "net%/tr": g["net_pct"].mean(),
                             "win%": (g["net_inr"] > 0).mean() * 100}),
        include_groups=False).head(12).to_string())

    # ---- LOCKED criteria ----
    gate_a = (multi["net_inr"].sum() - single["net_inr"].sum()) / max(symdays, 1)
    gate_b = (reentry["gross_pct"].mean() - first["gross_pct"].mean()) if len(reentry) else np.nan

    anti_p = np.nan
    if len(reentry) and len(first):
        rng = np.random.default_rng(SEED)
        g = multi["gross_pct"].to_numpy()
        k = len(first)
        ge = 0
        for _ in range(SHUFFLES):
            idx = rng.permutation(len(g))
            ge += int(g[idx][k:].mean() - g[idx][:k].mean() >= gate_b)
        anti_p = ge / SHUFFLES

    def v(ok: bool) -> str:
        return "PASS" if ok else "FAIL -> KILL"

    ok_a = gate_a > 0
    ok_b = bool(gate_b >= QUALITY_FLOOR)
    ok_anti = bool(anti_p < ANTI_ALPHA)
    ok_n = len(reentry) >= MIN_N_REENTRY

    print("\n" + "=" * 78)
    print(f"BT21 verdict | single {len(single)} trades, multi {len(multi)} trades, "
          f"{len(reentry)} of them re-entries")
    print("=" * 78)
    print(f"  GATE A money    net INR/symbol-day, multi − single : {gate_a:+,.2f}   "
          f"(> 0)          {v(ok_a)}")
    print(f"  GATE B quality  gross%/tr re-entry − first         : {gate_b:+.4f} pp "
          f"(>= {QUALITY_FLOOR:+.2f})  {v(ok_b)}")
    print(f"  anti            p(shuffled >= observed)            : {anti_p:.4f}    "
          f"(< {ANTI_ALPHA})      {v(ok_anti)}")
    print(f"  n               re-entry trades                    : {len(reentry)}       "
          f"(>= {MIN_N_REENTRY})     {v(ok_n)}")
    allpass = ok_a and ok_b and ok_anti and ok_n
    print("\n  >>> " + ("ALL DEV CRITERIA PASS -> open the 2025 hold-out ONCE (not live money)"
                        if allpass else
                        "KILL — a locked criterion failed; no cap, no cool-down, no rescue rule"))

    cost_free = (multi["gross_inr"].sum() - single["gross_inr"].sum()) / max(symdays, 1)
    print(f"\nsecondary (NOT a gate): the same Gate-A comparison ignoring all costs: "
          f"{cost_free:+,.2f} INR/symbol-day")
    print("  If this is positive while Gate A is negative, the idea has merit that costs erase.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--single", default="bt17_trades_me_single.csv")
    ap.add_argument("--multi", default="bt17_trades_me_multi.csv")
    a = ap.parse_args()
    return run(a.single, a.multi)


if __name__ == "__main__":
    sys.exit(main())
