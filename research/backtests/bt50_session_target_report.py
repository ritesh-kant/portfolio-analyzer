# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy", "scipy"]
# ///
"""BT50 — prior-session resistance in the target cap, with a below-level buffer.

Scores the five locked criteria in
research/hypotheses/2026-09-25-session-resistance-target.md on same-entry
pairs (new rule minus control, net INR per trade).

Usage (repo root):
  uv run research/backtests/bt50_session_target_report.py \
    --control research/backtests/bt17_trades_bt50_25_ctl.csv \
              research/backtests/bt17_trades_bt50_26_ctl.csv \
    --experiment research/backtests/bt17_trades_bt50_25_new.csv \
                 research/backtests/bt17_trades_bt50_26_new.csv \
    [--reproduce research/backtests/bt17_trades_struct25_structural.csv \
                 research/backtests/bt17_trades_struct26_structural.csv]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

KEY = ["date", "symbol", "setup", "entry_time", "entry"]
DROP_TOP = 5


def load(paths: list[Path]) -> pd.DataFrame:
    return pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)


def check_reproduction(control: pd.DataFrame, reference: pd.DataFrame) -> None:
    """The control must be BT47's structural arm, trade for trade."""
    cols = KEY + ["exit", "exit_reason", "target", "net_inr"]
    a = control[cols].sort_values(KEY).reset_index(drop=True)
    b = reference[cols].sort_values(KEY).reset_index(drop=True)
    same = len(a) == len(b) and np.allclose(a["net_inr"], b["net_inr"]) and (
        a[KEY + ["exit_reason"]].astype(str).equals(b[KEY + ["exit_reason"]].astype(str)))
    print(f"control reproduces BT47 structural arm: {'YES' if same else 'NO'} "
          f"({len(a)} vs {len(b)} trades)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", type=Path, nargs="+", required=True)
    ap.add_argument("--experiment", type=Path, nargs="+", required=True)
    ap.add_argument("--reproduce", type=Path, nargs="*", default=[])
    ap.add_argument("--changed-csv", type=Path, default=None,
                    help="write the trades whose outcome changed")
    a = ap.parse_args()

    ctl, new = load(a.control), load(a.experiment)
    print("=" * 90)
    print("BT50 — session-high resistance + 0.15% buffer in the target cap")
    print("=" * 90)
    if a.reproduce:
        check_reproduction(ctl, load(a.reproduce))

    for name, f in (("control", ctl), ("new rule", new)):
        print(f"{name:9s} trades {len(f):4d}  gross {f['gross_inr'].sum():+9,.0f}  "
              f"costs {f['costs_inr'].sum():8,.0f}  net {f['net_inr'].sum():+9,.0f}  "
              f"net/trade {f['net_inr'].mean():+7.1f}  targets "
              f"{(f['exit_reason'] == 'target').sum():3d}  "
              f"capital {(f['entry'] * f['qty']).sum():,.0f}")
    print("\ntarget source, new rule:", new["target_source"].value_counts().to_dict())
    print("target source, control: ", ctl["target_source"].value_counts().to_dict())

    ck = set(map(tuple, ctl[KEY].astype(str).to_numpy()))
    nk = set(map(tuple, new[KEY].astype(str).to_numpy()))
    m = ctl.merge(new, on=KEY, suffixes=("_c", "_n"), validate="one_to_one")
    print(f"\nidentical entries {len(m)}  control-only {len(ck - nk)}  new-only {len(nk - ck)}")
    print(f"capital ratio new/control: "
          f"{(m['entry'] * m['qty_n']).sum() / (m['entry'] * m['qty_c']).sum():.3f}")

    d = m["net_inr_n"] - m["net_inr_c"]
    changed = m[(m["exit_reason_c"] != m["exit_reason_n"])
                | ((m["exit_c"] - m["exit_n"]).abs() > 1e-9)]
    dc = changed["net_inr_n"] - changed["net_inr_c"]
    t, p = stats.ttest_rel(m["net_inr_n"], m["net_inr_c"])
    se = d.std(ddof=1) / np.sqrt(len(d))
    print(f"\npaired delta: total {d.sum():+,.0f}  mean {d.mean():+.2f}/trade  "
          f"95% CI [{d.mean() - 1.96 * se:+.2f}, {d.mean() + 1.96 * se:+.2f}]  "
          f"t={t:+.3f} p={p:.4f}")
    print(f"changed trades {len(changed)}  better {(dc > 0).sum()}  worse {(dc < 0).sum()}  "
          f"median {dc.median() if len(dc) else float('nan'):+.2f}  "
          f"mean {dc.mean() if len(dc) else float('nan'):+.2f}")
    top = dc.sort_values(ascending=False)
    drop = d.sum() - top[top > 0].head(DROP_TOP).sum()
    print(f"summed delta without the top {DROP_TOP} gains: {drop:+,.0f}")
    print("worst 5 / best 5 changed deltas:",
          [round(x) for x in top.tail(5)], [round(x) for x in top.head(5)])
    tr = changed.groupby(["exit_reason_c", "exit_reason_n"]).size().sort_values(ascending=False)
    print("exit transitions:", {f"{x}->{y}": n for (x, y), n in tr.items()})

    yr = pd.to_datetime(m["date"]).dt.year
    halves = {y: float(d[yr == y].mean()) for y in sorted(yr.unique())}
    print("mean delta by year:", {y: round(v, 2) for y, v in halves.items()})

    crit = {
        "1 mean delta > 0": d.mean() > 0,
        "2 p < 0.05": p < 0.05,
        "3 median changed delta >= 0": len(dc) > 0 and dc.median() >= 0,
        f"4 sum without top {DROP_TOP} > 0": drop > 0,
        "5 same sign both years": len(halves) == 2 and len({np.sign(v) for v in halves.values()}) == 1,
    }
    print("\nLOCKED CRITERIA")
    for k, v in crit.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    verdict = "PASS" if all(crit.values()) else "NO IMPROVEMENT ESTABLISHED"
    if d.mean() < 0 and p < 0.05:
        verdict += " — significantly WORSE, revert recommended"
    print(f"VERDICT: {verdict}")

    if a.changed_csv is not None:
        cols = KEY + ["stop_c", "target_c", "target_n", "target_source_n",
                      "structural_resistance_n", "structural_resistance_kind_n",
                      "exit_reason_c", "exit_reason_n", "exit_c", "exit_n",
                      "net_inr_c", "net_inr_n"]
        out = changed.assign(delta=dc)[cols + ["delta"]].sort_values("delta")
        out.to_csv(a.changed_csv, index=False)
        print(f"wrote {a.changed_csv} ({len(out)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
