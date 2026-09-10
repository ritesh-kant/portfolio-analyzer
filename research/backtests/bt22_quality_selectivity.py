# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy"]
# ///
"""BT22 — quality-selectivity bundle
(hypothesis: research/hypotheses/2026-09-06-quality-selectivity.md §4).

Three arms on the 2022-2023 dev window:

  base       current unfiltered entries, fixed 2R target
  sel        F1 uptrend + F2 chart quality + F3 volume surge, fixed 2R target
  sel_trend  same entries, indicator exits (SECONDARY, not gating)

All four LOCKED criteria must pass. Any one failing is a KILL.

  PRIMARY-lift  : gross%/trade(sel) - gross%/trade(base) >= +0.60 pp, positive
  PRIMARY-level : gross%/trade(sel) >= +0.60%
  anti          : shuffle pass/fail labels over base, p(shuffled >= obs) < 0.10
  n             : sel >= 150 trades (feasibility precondition, hypothesis section 3)

The anti test shuffles WITHIN the base arm's trades, because `sel` is a strict
subset of `base`: the question is whether the filters picked a better subset than
a random subset of the same size would have.

Usage:
  uv run research/backtests/bt22_quality_selectivity.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent

# --- LOCKED (hypothesis sections 3 and 4) ---
LIFT_FLOOR = 0.60        # percentage points
LEVEL_FLOOR = 0.60       # percent gross
ANTI_ALPHA = 0.10
MIN_N_SEL = 150
SHUFFLES = 5000
SEED = 20260906
COST_PCT = 1.0           # indicative stressed round trip, for the "is it enough" note


def _load(name: str) -> pd.DataFrame:
    p = HERE / name
    if not p.exists():
        raise SystemExit(f"bt22: missing {p.name} — run that arm of bt17 first.")
    df = pd.read_csv(p)
    df["key"] = (df["date"].astype(str) + "|" + df["symbol"].astype(str) + "|"
                 + df["setup"].astype(str) + "|" + df["trigger_time"].astype(str))
    return df


def _summ(g: pd.DataFrame) -> pd.Series:
    n = len(g)
    net = g["net_pct"]
    sharpe = float(net.mean() / net.std() * np.sqrt(252)) if n > 1 and net.std() > 0 else np.nan
    return pd.Series({
        "n": n,
        "win%": (g["net_inr"] > 0).mean() * 100,
        "gross%/tr": g["gross_pct"].mean(),
        "net%/tr": net.mean(),
        "net_inr": g["net_inr"].sum(),
        "sharpe": sharpe,
        "target%": (g["exit_reason"] == "target").mean() * 100,
        "stop%": (g["exit_reason"] == "stop").mean() * 100,
    })


def run(base_n: str, sel_n: str, trend_n: str, cands_n: str) -> int:
    base, sel = _load(base_n), _load(sel_n)
    pd.set_option("display.width", 175)
    pd.set_option("display.float_format", lambda x: f"{x:,.3f}")

    months = len(pd.to_datetime(base["date"]).dt.to_period("M").unique())
    print("=" * 78)
    print(f"BT22 — quality selectivity | dev 2022-2023 | {months} months")
    print("=" * 78)

    arms = {"base": base, "sel": sel}
    trend_p = HERE / trend_n
    if trend_p.exists():
        arms["sel_trend"] = _load(trend_n)

    print("\narms:")
    tbl = pd.DataFrame({k: _summ(v) for k, v in arms.items()}).T
    tbl["trades/month"] = [len(v) / max(months, 1) for v in arms.values()]
    print(tbl.to_string())

    subset = set(sel["key"]).issubset(set(base["key"]))
    print(f"\nsel is a strict subset of base: {subset}"
          f"   (filters may only remove trades)")
    if not subset:
        print("  WARNING: not a subset — the filters changed which trades are taken, "
              "which breaks the anti test's assumption.")

    cands_p = HERE / cands_n
    if cands_p.exists():
        c = pd.read_csv(cands_p)
        if "quality_reason" in c.columns:
            print("\nwhy setups were refused (all candidates in the filtered run):")
            vc = c["quality_reason"].value_counts()
            print((vc.to_frame("n").assign(pct=lambda d: d["n"] / len(c) * 100)).to_string())

    # ---- LOCKED criteria ----
    lift = sel["gross_pct"].mean() - base["gross_pct"].mean()
    level = sel["gross_pct"].mean()

    anti_p = np.nan
    if 0 < len(sel) < len(base):
        rng = np.random.default_rng(SEED)
        g = base["gross_pct"].to_numpy()
        k = len(sel)
        base_mean = g.mean()
        ge = 0
        for _ in range(SHUFFLES):
            pick = rng.choice(len(g), size=k, replace=False)
            ge += int(g[pick].mean() - base_mean >= lift)
        anti_p = ge / SHUFFLES

    def v(ok: bool) -> str:
        return "PASS" if ok else "FAIL -> KILL"

    ok_n = len(sel) >= MIN_N_SEL
    ok_lift = bool(lift >= LIFT_FLOOR)
    ok_level = bool(level >= LEVEL_FLOOR)
    ok_anti = bool(anti_p < ANTI_ALPHA)

    print("\n" + "=" * 78)
    print(f"BT22 verdict | base {len(base)} trades -> sel {len(sel)} "
          f"({len(sel) / max(len(base), 1) * 100:.1f}% kept)")
    print("=" * 78)
    if not ok_n:
        print(f"  n  sel trades: {len(sel)} < {MIN_N_SEL} required by section 3.")
        print("  >>> NOT TESTABLE on this window. Per the locked rules this may NOT be")
        print("      rescued by loosening a threshold, dropping a filter, or widening")
        print("      the window — that would be selecting the filters by trade count.")
        return 0
    print(f"  PRIMARY-lift   gross(sel) - gross(base) : {lift:+.4f} pp "
          f"(>= {LIFT_FLOOR:+.2f})  {v(ok_lift)}")
    print(f"  PRIMARY-level  gross(sel)               : {level:+.4f} %  "
          f"(>= {LEVEL_FLOOR:+.2f})  {v(ok_level)}")
    print(f"  anti           p(random subset >= obs)  : {anti_p:.4f}    "
          f"(< {ANTI_ALPHA})      {v(ok_anti)}")
    print(f"  n              sel trades               : {len(sel)}       "
          f"(>= {MIN_N_SEL})     {v(ok_n)}")
    allpass = ok_lift and ok_level and ok_anti and ok_n
    print("\n  >>> " + (
        "ALL DEV CRITERIA PASS -> ask the operator before reading the 2025 hold-out"
        if allpass else
        "KILL — a locked criterion failed; the filter bundle is not shipped, and the "
        "subset that looked best is an observation, not a rescue"))

    print(f"\nsecondary (NOT gates). Indicative stressed round trip ~{COST_PCT:.1f}%; "
          f"sel gross is {level:+.3f}%.")
    if "sel_trend" in arms:
        st = arms["sel_trend"]
        print(f"  exit change on the SAME filtered entries (request 4): "
              f"gross {st['gross_pct'].mean():+.4f}% vs {level:+.4f}% "
              f"= {st['gross_pct'].mean() - level:+.4f} pp")
    print("\nby setup (sel):")
    print(sel.groupby("setup").apply(_summ, include_groups=False)
          .sort_values("n", ascending=False).to_string())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="bt17_trades_qs_base.csv")
    ap.add_argument("--sel", default="bt17_trades_qs_sel.csv")
    ap.add_argument("--trend", default="bt17_trades_qs_seltrend.csv")
    ap.add_argument("--cands", default="bt17_candidates_qs_sel.csv")
    a = ap.parse_args()
    return run(a.base, a.sel, a.trend, a.cands)


if __name__ == "__main__":
    sys.exit(main())
