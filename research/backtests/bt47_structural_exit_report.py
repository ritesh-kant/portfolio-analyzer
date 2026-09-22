# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy", "scipy"]
# ///
"""BT47 — compare the fixed-2R/two-close arm with structural exit levels.

The experimental arm replaces the two-close false-break exit with a close below
support frozen at entry and caps a live fixed target at resistance frozen at
entry. It can alter the false-break reclaim path, so whole-arm totals are
descriptive and outcome attribution is restricted to identical entries.

Usage:
  uv run research/backtests/bt47_structural_exit_report.py \
    --control research/backtests/bt17_trades_struct25_control.csv \
              research/backtests/bt17_trades_struct26_control.csv \
    --experiment research/backtests/bt17_trades_struct25_structural.csv \
                 research/backtests/bt17_trades_struct26_structural.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

KEY = ["date", "symbol", "setup", "entry_time", "entry"]


def load(paths: list[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in paths:
        if not path.exists():
            raise SystemExit(f"missing {path}")
        try:
            frame = pd.read_csv(path)
        except pd.errors.EmptyDataError as exc:
            raise SystemExit(f"empty {path}") from exc
        required = KEY + ["exit", "exit_reason", "net_inr", "gross_inr", "costs_inr"]
        missing = set(required) - set(frame)
        if missing:
            raise SystemExit(f"{path} is missing columns: {', '.join(sorted(missing))}")
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def describe(name: str, frame: pd.DataFrame) -> None:
    print(
        f"{name}: trades {len(frame):,}  gross INR {frame['gross_inr'].sum():+,.0f}  "
        f"costs INR {frame['costs_inr'].sum():,.0f}  net INR {frame['net_inr'].sum():+,.0f}  "
        f"net/trade {frame['net_inr'].mean():+.1f}  "
        f"gross win rate {(frame['gross_inr'] > 0).mean() * 100:.2f}%"
    )
    print(f"  exits: {frame['exit_reason'].value_counts().to_dict()}")
    if "structural_support" in frame:
        usable_support = frame["structural_support"].notna() & (
            frame["structural_support"] > frame["stop"]
        )
        print(f"  support above hard stop: {int(usable_support.sum()):,} "
              f"({usable_support.mean() * 100:.2f}%)")
    if "target_source" in frame:
        print(f"  target source: {frame['target_source'].value_counts().to_dict()}")


def paired(control: pd.DataFrame, experiment: pd.DataFrame) -> pd.DataFrame:
    control_rows = control[KEY + ["exit", "exit_reason", "gross_inr", "net_inr"]]
    experiment_rows = experiment[KEY + ["exit", "exit_reason", "gross_inr", "net_inr"]]
    try:
        return control_rows.merge(
            experiment_rows,
            on=KEY,
            suffixes=("_control", "_structural"),
            validate="one_to_one",
        )
    except pd.errors.MergeError as exc:
        raise SystemExit("duplicate entry keys prevent paired attribution") from exc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", type=Path, nargs="+", required=True)
    ap.add_argument("--experiment", type=Path, nargs="+", required=True)
    args = ap.parse_args()

    control = load(args.control)
    experiment = load(args.experiment)
    print("=" * 96)
    print("BT47 — structural support exit and resistance-capped target")
    print("=" * 96)
    describe("control", control)
    describe("structural", experiment)

    control_keys = set(map(tuple, control[KEY].astype(str).to_numpy()))
    experiment_keys = set(map(tuple, experiment[KEY].astype(str).to_numpy()))
    shared = paired(control, experiment)
    print(
        f"\nidentical entries: {len(shared):,}  "
        f"control-only: {len(control_keys - experiment_keys):,}  "
        f"structural-only: {len(experiment_keys - control_keys):,}"
    )
    if shared.empty:
        print("no shared entries; no paired outcome can be reported")
        return 0

    delta = shared["net_inr_structural"] - shared["net_inr_control"]
    changed = shared[
        (shared["exit_reason_control"] != shared["exit_reason_structural"])
        | ((shared["exit_control"] - shared["exit_structural"]).abs() > 1e-9)
    ]
    if len(shared) >= 2:
        statistic, pvalue = stats.ttest_rel(
            shared["net_inr_structural"], shared["net_inr_control"]
        )
        stderr = delta.std(ddof=1) / np.sqrt(len(delta))
        ci = (delta.mean() - 1.96 * stderr, delta.mean() + 1.96 * stderr)
        inference = f"95% CI [{ci[0]:+.2f}, {ci[1]:+.2f}]  t={statistic:+.3f}  p={pvalue:.4f}"
    else:
        inference = "paired inference unavailable (fewer than two entries)"
    print(
        f"paired net delta (structural - control): total {delta.sum():+,.0f} INR  "
        f"mean {delta.mean():+.2f} INR/trade  {inference}"
    )
    print(f"changed exits: {len(changed):,} of {len(shared):,}")
    if not changed.empty:
        changed_delta = changed["net_inr_structural"] - changed["net_inr_control"]
        print(
            f"changed-exit net delta: total {changed_delta.sum():+,.0f} INR  "
            f"mean {changed_delta.mean():+.2f} INR/trade"
        )
        transitions = (
            changed.groupby(["exit_reason_control", "exit_reason_structural"])
            .size()
            .sort_values(ascending=False)
        )
        print("changed exit transitions:")
        for (before, after), count in transitions.items():
            print(f"  {before} -> {after}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
