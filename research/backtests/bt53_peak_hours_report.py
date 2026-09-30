"""BT53 — does the warrior_strict 11:00 entry cutoff refuse profitable trades?

Scores research/hypotheses/2026-09-30-peak-hours-cutoff.md. The 14:30 arm is a
superset of the live 11:00 control (nothing before 11:00 reads anything after
it), so the entries the cap refuses are exactly the arm's trades that the
control does not have. This script checks the superset claim, then scores those
ADDED trades against the four locked criteria at real MIS costs.

Inputs: control = the BT52 checkpoint runs (bt17_trades_3y_<Y>_cp.csv,
bt17_trades_ytd2026_checkpoint.csv); arm = bt17_trades_bt53_<Y>_1430.csv.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "apps" / "signal-engine"))

from src.news_trader.trailing_sl import calc_costs  # noqa: E402

KEY = ["date", "symbol", "entry_time", "entry"]
WINDOWS = {
    "2022-Q4": ("bt17_trades_3y_2022_cp.csv", "bt17_trades_bt53_2022_1430.csv"),
    "2023": ("bt17_trades_3y_2023_cp.csv", "bt17_trades_bt53_2023_1430.csv"),
    "2024": ("bt17_trades_3y_2024_cp.csv", "bt17_trades_bt53_2024_1430.csv"),
    "2026 YTD": ("bt17_trades_ytd2026_checkpoint.csv", "bt17_trades_bt53_2026_1430.csv"),
}


def real_net(t: pd.DataFrame) -> pd.Series:
    cost = [calc_costs(r.entry, r.exit, int(r.qty), direction="long")["total"]
            for r in t.itertuples()]
    return t["gross_inr"] - pd.Series(cost, index=t.index)


def main() -> None:
    ctl_all, added_all = [], []
    for win, (ctl_f, arm_f) in WINDOWS.items():
        ctl, arm = pd.read_csv(HERE / ctl_f), pd.read_csv(HERE / arm_f)
        ctl["net_real"], arm["net_real"] = real_net(ctl), real_net(arm)
        cols = KEY + ["exit", "exit_reason", "net_real"]
        m = ctl[cols].merge(arm[cols], on=KEY, how="left", suffixes=("", "_arm"),
                            indicator=True, validate="one_to_one")
        missing = int((m["_merge"] == "left_only").sum())
        drift = int(((m["_merge"] == "both")
                     & ((m["exit"] - m["exit_arm"]).abs() > 1e-9)).sum())
        added = arm.merge(ctl[KEY], on=KEY, how="left", indicator=True)
        added = added[added["_merge"] == "left_only"].drop(columns="_merge")
        late_in_ctl = int((ctl["entry_time"] >= "11:00").sum())
        print(f"{win:9s} control {len(ctl):4d} | arm {len(arm):4d} | added {len(added):4d} | "
              f"control trades missing from arm {missing}, exit changed {drift}, "
              f"control entries >=11:00 {late_in_ctl}")
        ctl["window"], added["window"] = win, win
        ctl_all.append(ctl)
        added_all.append(added)

    ctl = pd.concat(ctl_all, ignore_index=True)
    add = pd.concat(added_all, ignore_index=True)
    x = add["net_real"]

    print("\n== ADDED trades (entries the 11:00 cap refuses) ==")
    print(f"n {len(add)} | real net total ₹{x.sum():,.0f} | mean ₹{x.mean():.2f}/trade | "
          f"median ₹{x.median():.2f} | win% {(x > 0).mean() * 100:.1f} | "
          f"gross %/trade {add['gross_pct'].mean():+.3f}")
    print(f"control for comparison: n {len(ctl)} | mean ₹{ctl['net_real'].mean():.2f} | "
          f"median ₹{ctl['net_real'].median():.2f} | win% {(ctl['net_real'] > 0).mean() * 100:.1f} | "
          f"gross %/trade {ctl['gross_pct'].mean():+.3f}")

    p = float(stats.ttest_1samp(x, 0.0, alternative="greater").pvalue)
    by_win = add.groupby("window", sort=False)["net_real"].agg(["count", "sum", "mean"])
    drop5 = float(x.sort_values(ascending=False).iloc[5:].sum())
    c1 = x.mean() > 0 and p < 0.05
    c2 = int((by_win["sum"] > 0).sum()) >= 3
    c3 = drop5 > 0
    c4 = x.median() >= ctl["net_real"].median()

    print("\nby window (added trades, real net):")
    print(by_win.round(1).to_string())
    add["hour"] = add["entry_time"].str.slice(0, 2) + ":00"
    print("\nby entry hour (description only — NOT a tuning input):")
    print(add.groupby("hour")["net_real"].agg(["count", "sum", "mean", "median"]).round(1).to_string())
    print("\nby exit reason:")
    print(add.groupby("exit_reason")["net_real"].agg(["count", "mean"]).round(1).to_string())

    print("\n== locked criteria ==")
    print(f"1. mean > 0, one-sided p < 0.05 : mean ₹{x.mean():.2f}, p={p:.3f} -> {'PASS' if c1 else 'FAIL'}")
    print(f"2. positive in >=3/4 windows    : {int((by_win['sum'] > 0).sum())}/4 -> {'PASS' if c2 else 'FAIL'}")
    print(f"3. drop top-5 still > 0         : ₹{drop5:,.0f} -> {'PASS' if c3 else 'FAIL'}")
    print(f"4. median >= control median     : ₹{x.median():.2f} vs ₹{ctl['net_real'].median():.2f} "
          f"-> {'PASS' if c4 else 'FAIL'}")
    n_pass = sum([c1, c2, c3, c4])
    print(f"\nVERDICT: {n_pass}/4 -> {'earns a 2025 single-shot' if n_pass == 4 else 'KILL, 11:00 stays'}")


if __name__ == "__main__":
    main()
