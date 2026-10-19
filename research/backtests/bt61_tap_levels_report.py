# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "pyarrow", "scipy"]
# ///
"""BT61 - tap_high resistance levels vs the live rule.

Scores the four locked criteria in research/hypotheses/2026-10-08-tap-high-levels.md
at the REAL itemised MIS cost model, on the full pooled trade sets (the arm is not
a subset of the control: tap levels also move the headroom veto), plus the paired
view on trades both arms took.

Usage (repo root):
  apps/signal-engine/.venv/bin/python research/backtests/bt61_tap_levels_report.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "apps" / "signal-engine"))
from src.news_trader.trailing_sl import calc_costs  # noqa: E402

KEY = ["date", "symbol", "entry_time", "entry"]
YEARS = (2024, 2025, 2026)


def load(year: int, arm: str) -> pd.DataFrame:
    t = pd.read_csv(HERE / f"bt17_trades_bt61_tap_{year}_{arm}.csv")
    cost = [calc_costs(r.entry, r.exit, int(r.qty), direction="long")["total"]
            for r in t.itertuples()]
    t["net_real"] = t["gross_inr"] - pd.Series(cost, index=t.index)
    t["deployed"] = t["entry"] * t["qty"]
    t["year"] = year
    return t


def drop_top5_mean(x: pd.Series) -> float:
    return float(x.sort_values(ascending=False).iloc[5:].mean())


def stat_row(t: pd.DataFrame) -> dict:
    return {"n": len(t), "net/trade": t["net_real"].mean(), "median": t["net_real"].median(),
            "drop-top-5/trade": drop_top5_mean(t["net_real"]),
            "net ₹": t["net_real"].sum(), "deployed ₹": t["deployed"].sum(),
            "net per ₹100k deployed": 1e5 * t["net_real"].sum() / t["deployed"].sum(),
            "win %": 100 * (t["net_real"] > 0).mean()}


def main() -> int:
    for arm, label in (("t3", "PRIMARY --tap-levels 3"), ("t2", "SECONDARY --tap-levels 2")):
        data = {y: (load(y, "ctl"), load(y, arm)) for y in YEARS}
        ctl = pd.concat([d[0] for d in data.values()])
        new = pd.concat([d[1] for d in data.values()])
        print(f"\n================ {label} vs control ================")
        rows = []
        for y in YEARS:
            c, a = data[y]
            rows += [{"year": y, "side": "ctl", **stat_row(c)}, {"year": y, "side": "arm", **stat_row(a)}]
        rows += [{"year": "pooled", "side": "ctl", **stat_row(ctl)},
                 {"year": "pooled", "side": "arm", **stat_row(new)}]
        print(pd.DataFrame(rows).round(1).to_string(index=False))

        cs, ns = stat_row(ctl), stat_row(new)
        ratio = ns["deployed ₹"] / cs["deployed ₹"]
        per_rupee = abs(ratio - 1) > 0.05
        c1 = (ns["net per ₹100k deployed"] >= cs["net per ₹100k deployed"]) if per_rupee \
            else (ns["net/trade"] >= cs["net/trade"])
        worse_years = sum(data[y][1]["net_real"].mean() < data[y][0]["net_real"].mean() for y in YEARS)
        crit = [
            (f"1 pooled net {'per ₹ deployed' if per_rupee else 'per trade'} arm >= ctl", c1,
             f"{ns['net/trade']:.1f} vs {cs['net/trade']:.1f} per trade; capital ratio {ratio:.3f}"),
            ("2 pooled median arm >= ctl", ns["median"] >= cs["median"],
             f"{ns['median']:.1f} vs {cs['median']:.1f}"),
            ("3 arm worse in < 2 of 3 years", worse_years < 2, f"worse in {worse_years}/3"),
            ("4 drop-top-5 arm >= ctl", ns["drop-top-5/trade"] >= cs["drop-top-5/trade"],
             f"{ns['drop-top-5/trade']:.1f} vs {cs['drop-top-5/trade']:.1f}"),
        ]
        for name, ok, detail in crit:
            print(f"  [{'pass' if ok else 'REJECT'}] {name}: {detail}")
        print("  VERDICT:", "survives (still a spent window)" if all(c[1] for c in crit) else "REJECT (flag stays OFF)")

        m = new.merge(ctl[KEY + ["exit", "exit_reason", "target", "net_real", "structural_resistance",
                                 "structural_resistance_kind"]]
                      .rename(columns=lambda c: f"ctl_{c}" if c not in KEY else c),
                      on=KEY, how="inner")
        m["delta"] = m["net_real"] - m["ctl_net_real"]
        p = float(stats.ttest_1samp(m["delta"], 0.0).pvalue) if m["delta"].std() > 0 else 1.0
        chg = m[(m["exit_reason"] != m["ctl_exit_reason"]) | ((m["exit"] - m["ctl_exit"]).abs() > 1e-9)]
        print(f"\n  paired on {len(m)} common trades: mean Δ ₹{m['delta'].mean():+.2f}, p={p:.3f}; "
              f"exits changed {len(chg)}; entries only in ctl {len(ctl) - len(m)}, only in arm {len(new) - len(m)}")
        tap = new[new["structural_resistance_kind"] == "tap_high"]
        risk = (new["entry"] - new["stop"]).replace(0, np.nan)
        dist_new = (new["structural_resistance"] - new["entry"]) / risk
        print(f"  mechanism: tap_high is the binding resistance on {len(tap)}/{len(new)} arm trades "
              f"({100 * len(tap) / len(new):.1f}%); median headroom to it "
              f"{dist_new[new['structural_resistance_kind'] == 'tap_high'].median():.2f}R")
        print("  exit mix arm :", new["exit_reason"].value_counts().to_dict())
        print("  exit mix ctl :", ctl["exit_reason"].value_counts().to_dict())

    a = load(2024, "t3")
    lv = a[(a["symbol"] == "LATENTVIEW") & (a["date"] == "2024-03-28")]
    print("\nLATENTVIEW 2024-03-28 under --tap-levels 3:")
    print(lv[["setup", "entry", "target", "target_source", "structural_resistance",
              "structural_resistance_kind", "exit", "exit_reason"]].to_string(index=False)
          if len(lv) else "  (no trade: entry changed or vetoed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
