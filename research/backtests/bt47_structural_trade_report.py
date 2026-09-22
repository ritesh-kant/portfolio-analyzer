# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "pyarrow"]
# ///
"""BT47 — ten structural-exit trades drawn as self-contained candlestick charts.

The report is a visual diagnostic, not a second performance measurement. It
uses the structural arm's recorded, entry-known support/resistance/target
levels and the exact intraday bars from the replay cache. The deterministic
sample prioritizes actual support-break exits, then resistance-capped targets,
and finally other trades with support above the hard stop.

Usage:
  uv run research/backtests/bt47_structural_trade_report.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

import bt32_strategy_report as bt32  # noqa: E402

KEY = ["date", "symbol", "setup", "entry_time", "entry"]


def _load(paths: list[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in paths:
        if not path.exists():
            raise SystemExit(f"missing {path}")
        try:
            frames.append(pd.read_csv(path))
        except pd.errors.EmptyDataError as exc:
            raise SystemExit(f"empty {path}") from exc
    return pd.concat(frames, ignore_index=True)


def _spread(rows: pd.DataFrame, count: int) -> pd.DataFrame:
    """Take evenly spaced deterministic rows, so one week cannot dominate."""
    ordered = rows.sort_values(["date", "symbol", "entry_time"]).reset_index(drop=True)
    if len(ordered) <= count:
        return ordered
    step = (len(ordered) - 1) / (count - 1)
    return ordered.iloc[[int(round(index * step)) for index in range(count)]]


def select_trades(control: pd.DataFrame, structural: pd.DataFrame, limit: int) -> pd.DataFrame:
    control_exit = control[KEY + ["exit", "exit_reason"]].rename(
        columns={"exit": "control_exit", "exit_reason": "control_exit_reason"}
    )
    rows = structural.merge(control_exit, on=KEY, how="inner", validate="one_to_one")
    usable_support = rows["structural_support"].notna() & (
        rows["structural_support"] > rows["stop"]
    )
    # Trade timestamps are bar starts. A support break is decided at the close
    # of that bar, while a target can fill at any point inside it. Both are
    # valid engine outcomes, but a chart cannot show their intrabar ordering
    # from OHLC alone. Keep the visual sample to separately labelled minutes.
    eligible = rows[usable_support & (rows["exit_time"] > rows["entry_time"])].copy()
    if eligible.empty:
        raise SystemExit(
            "no structural trades have a frozen support above the hard stop "
            "and a separately labelled exit minute"
        )

    def remaining_after(chosen: pd.DataFrame) -> pd.DataFrame:
        used_keys = set(map(tuple, chosen[KEY].to_numpy()))
        return eligible[
            ~eligible[KEY].apply(tuple, axis=1).isin(used_keys)
        ]

    selected: list[pd.DataFrame] = []
    support_breaks = eligible[eligible["exit_reason"] == "support_break"]
    if not support_breaks.empty:
        selected.append(_spread(support_breaks, min(5, limit)))

    used = pd.concat(selected, ignore_index=True) if selected else eligible.iloc[:0]
    remaining = remaining_after(used)
    capped_targets = remaining[
        remaining["target_source"].str.startswith("structural_resistance:", na=False)
    ]
    if not capped_targets.empty and len(used) < limit:
        selected.append(_spread(capped_targets, min(limit - len(used), 5)))

    used = pd.concat(selected, ignore_index=True) if selected else eligible.iloc[:0]
    remaining = remaining_after(used)
    if len(used) < limit and not remaining.empty:
        selected.append(_spread(remaining, limit - len(used)))

    out = pd.concat(selected, ignore_index=True).drop_duplicates(subset=KEY)
    return out.sort_values(["date", "symbol", "entry_time"]).head(limit).reset_index(drop=True)


def _level(price: object, kind: object, side: str) -> dict | None:
    if pd.isna(price):
        return None
    source = str(kind) if isinstance(kind, str) and kind else "unknown"
    return {
        "price": round(float(price), 2),
        "kind": f"frozen {side} ({source})",
        "touches": 0,
        "strength": 0.0,
        "structural": True,
        "side": side,
    }


def build_cards(rows: pd.DataFrame) -> list[dict]:
    cards: list[dict] = []
    for _, row in rows.iterrows():
        card = bt32.build_day(row)
        if card is None:
            continue
        support = _level(
            row["structural_support"], row["structural_support_kind"], "support"
        )
        resistance = _level(
            row["structural_resistance"], row["structural_resistance_kind"], "resistance"
        )
        card["target"] = round(float(row["target"]), 2)
        card["target_source"] = str(row["target_source"])
        target_is_resistance = (
            resistance is not None
            and abs(float(resistance["price"]) - float(card["target"])) < 0.01
        )
        card["target_label"] = "Target / resistance" if target_is_resistance else "Target"
        # The report is for reviewing the trade-management levels, not every
        # unrelated formation the generic review template can find. Pattern
        # labels overlap densely at one-minute resolution and obscure the
        # levels the reader needs to inspect.
        card["patterns"] = []
        card["levels"] = [
            level for level in (support, None if target_is_resistance else resistance)
            if level is not None
        ]
        cards.append(card)
    return cards


def build_html(cards: list[dict]) -> str:
    summary = bt32.summarise(cards)
    data = {"summary": summary, "trades": cards}
    sub = (
        "Ten deterministic structural-arm examples. Every chart draws the actual "
        "entry-known frozen support (blue), frozen resistance (pink), active target "
        "(green), hard stop (red), buy, and sell from the replay. To avoid implying "
        "intrabar ordering that OHLC data cannot show, the sample excludes entries "
        "and exits recorded in the same minute and opens at 1-minute resolution. "
        "This is a visual sample only; see the BT47 paired report for the full-"
        "population result."
    )
    report = bt32.build_html("BT47 — structural exit trades, chart review", sub, data)
    return (
        report.replace("Target 2R", "Target")
        .replace("2R target", "Target")
        .replace('var cards = [], tf = "5m";', 'var cards = [], tf = "1m";')
        .replace(
            '["Target", tr.target, "#34d399"]',
            '[tr.target_label || "Target", tr.target, "#34d399"]',
        )
        .replace(
            '<option value="1m">1-minute</option>',
            '<option value="1m" selected>1-minute (default)</option>',
        )
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--control",
        type=Path,
        nargs="+",
        default=[
            HERE / "bt17_trades_struct25_control.csv",
            HERE / "bt17_trades_struct26_control.csv",
        ],
    )
    ap.add_argument(
        "--structural",
        type=Path,
        nargs="+",
        default=[
            HERE / "bt17_trades_struct25_structural.csv",
            HERE / "bt17_trades_struct26_structural.csv",
        ],
    )
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument(
        "--output",
        type=Path,
        default=HERE / "bt47_structural_trade_report.html",
    )
    args = ap.parse_args()
    if args.limit < 1:
        ap.error("--limit must be positive")

    selected = select_trades(_load(args.control), _load(args.structural), args.limit)
    cards = build_cards(selected)
    if not cards:
        raise SystemExit("no chart cards could be built from the cached intraday bars")
    args.output.write_text(build_html(cards))
    print(f"wrote {args.output} ({len(cards)} charts)")
    print(
        selected[
            [
                "date",
                "symbol",
                "entry_time",
                "exit_reason",
                "target_source",
                "structural_support",
                "structural_resistance",
            ]
        ].to_string(index=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
