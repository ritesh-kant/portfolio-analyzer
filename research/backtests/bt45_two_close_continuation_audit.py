# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy", "pyarrow"]
# ///
"""BT45 — retrospective audit of two-close exits during Rising Three pullbacks.

The current false-break rule is evaluated before a Rising Three can be
confirmed: the rule can fire after two red pullback closes, whereas Rising
Three requires a later final green candle. This script does NOT propose an
exception. It identifies actual current-rule false-break exits for which the
cached five-minute bars later completed a Rising Three containing that exit.

That label necessarily uses future data and is diagnostic only. It must never
be read by the live exit path or used to tune the rule on this already-spent
2024 sample.

The saved BT42 rows do not retain every setup's defended level. Starting from
the actual `false_break` exits keeps the classification faithful to the
original replay; the cache is used only to inspect what formed afterwards.

Usage:
  apps/signal-engine/.venv/bin/python research/backtests/bt45_two_close_continuation_audit.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

from src.momentum_trader.candles import PatternMatch, completed_pattern_matches  # noqa: E402
from src.momentum_trader.engine import ONE_MINUTE_SETUPS, resample_5m  # noqa: E402

CACHE = ROOT / "research" / "backtests" / ".cache_upstox" / "1m"
KEY = ["date", "symbol", "setup", "entry_time", "entry"]


def _timestamp(date: str, clock: str, tz: object) -> pd.Timestamp:
    return pd.Timestamp(f"{date} {clock}", tz=tz)


def _exit_observed_at(row: dict[str, object], tz: object) -> pd.Timestamp:
    """Return when the closing price evidence for the exit was observable."""
    minutes = 1 if str(row["setup"]) in ONE_MINUTE_SETUPS else 5
    return _timestamp(str(row["date"]), str(row["exit_time"]), tz) + pd.Timedelta(minutes=minutes)


def _rising_three_overlap(
    bars_1m: pd.DataFrame, row: dict[str, object]
) -> PatternMatch | None:
    """Find a post-exit confirmed Rising Three whose red pullback contains the exit.

    This deliberately scans completed formation prefixes after the exit. The
    caller already knows the trade exited as `false_break`; this function asks
    only whether subsequent data completed the specific continuation shape that
    would have required look-ahead to recognize in real time.
    """
    if bars_1m.empty or not isinstance(bars_1m.index, pd.DatetimeIndex):
        return None
    tz = bars_1m.index.tz
    if tz is None:
        return None
    entry_at = _timestamp(str(row["date"]), str(row["entry_time"]), tz)
    exit_at = _exit_observed_at(row, tz)
    day = bars_1m[bars_1m.index.normalize() == entry_at.normalize()]
    tf5 = resample_5m(day)
    if tf5.empty:
        return None

    for end in range(15, len(tf5) + 1):
        for match in completed_pattern_matches(tf5.iloc[:end], "5m"):
            if match.name != "rising_three":
                continue
            start = pd.Timestamp(match.start)
            final_start = pd.Timestamp(match.end)
            formed_at = pd.Timestamp(match.formed_at)
            pullback_start = start + pd.Timedelta(minutes=5)
            # The trade must already be open before the pullback. Its two-close
            # decision must fall within the three red candles, before the final
            # green candle makes the formation observable.
            if entry_at <= pullback_start <= exit_at < final_start < formed_at:
                return match
    return None


def _scan_row(row: dict[str, object]) -> dict[str, object]:
    path = CACHE / f"{row['symbol']}_{int(row['year'])}.parquet"
    base = {
        **row,
        "scan": "ok",
        "post_exit_rising_three": 0,
        "pattern_start": "",
        "pattern_end": "",
        "pattern_formed_at": "",
    }
    if not path.exists():
        return {**base, "scan": "no_cache"}
    try:
        bars = pd.read_parquet(path)
    except (OSError, ValueError, ImportError) as exc:
        return {**base, "scan": f"unreadable_cache:{type(exc).__name__}"}
    match = _rising_three_overlap(bars, row)
    if match is None:
        return base
    return {
        **base,
        "post_exit_rising_three": 1,
        "pattern_start": match.start,
        "pattern_end": match.end,
        "pattern_formed_at": match.formed_at,
    }


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"missing {path}")
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError as exc:
        raise SystemExit(f"empty {path}") from exc


def _paired(current: pd.DataFrame, legacy: pd.DataFrame) -> pd.DataFrame:
    old = legacy[KEY + ["exit_time", "exit", "exit_reason", "gross_inr", "net_inr"]].rename(
        columns={
            "exit_time": "legacy_exit_time",
            "exit": "legacy_exit",
            "exit_reason": "legacy_exit_reason",
            "gross_inr": "legacy_gross_inr",
            "net_inr": "legacy_net_inr",
        }
    )
    return current.merge(old, on=KEY, how="left", validate="one_to_one")


def _report(scored: pd.DataFrame) -> None:
    print("=" * 96)
    print("BT45 — two-close false-break exits followed by a completed Rising Three")
    print("=" * 96)
    print(f"actual current false_break exits: {len(scored)}")
    print("cache scan status:", scored["scan"].value_counts().to_dict())
    valid = scored[scored["scan"] == "ok"]
    hits = valid[valid["post_exit_rising_three"] == 1]
    print(f"Rising Three completed after exit, with exit in its red pullback: "
          f"{len(hits)} of {len(valid)} cache-readable exits")
    print("This is retrospective only: the final green candle had not closed at the exit.")
    if hits.empty:
        return
    paired = hits[hits["legacy_net_inr"].notna()].copy()
    print(f"paired legacy rows: {len(paired)} of {len(hits)}")
    if not paired.empty:
        delta = paired["net_inr"] - paired["legacy_net_inr"]
        print(f"current-minus-legacy net INR: total {delta.sum():+,.0f}; "
              f"mean {delta.mean():+,.1f}")
        print("legacy exit reasons:", paired["legacy_exit_reason"].value_counts().to_dict())
    print("\nOverlaps:")
    cols = [
        "date", "symbol", "setup", "entry_time", "exit_time", "pattern_start",
        "pattern_end", "pattern_formed_at", "legacy_exit_time", "legacy_exit_reason",
        "net_inr", "legacy_net_inr",
    ]
    print(hits[cols].to_string(index=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--current",
        type=Path,
        default=ROOT / "research" / "backtests" / "bt17_trades_fb24_fixed.csv",
        help="current two-close, stop-first, post-audit-fix BT42 trade CSV",
    )
    ap.add_argument(
        "--legacy",
        type=Path,
        default=ROOT / "research" / "backtests" / "bt17_trades_fb24_old.csv",
        help="legacy one-close, pre-stop BT42 trade CSV",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "research" / "backtests" / "bt45_two_close_continuation.csv",
    )
    args = ap.parse_args()

    current = _load(args.current)
    legacy = _load(args.legacy)
    exits = current[current["exit_reason"] == "false_break"].copy()
    if exits.empty:
        raise SystemExit(f"no false_break exits in {args.current}")
    scored = pd.DataFrame([_scan_row(row) for row in exits.to_dict("records")])
    scored = _paired(scored, legacy)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(args.out, index=False)
    _report(scored)
    print(f"\nwrote {args.out} ({len(scored)} false-break exits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
