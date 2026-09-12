"""BT28 - candlestick pattern census + forward-return measurement.

Pre-registered question (written before the run):

    Given the contextual detector (``candles-v3-20260912``; the v2 run is kept
    in bt28_*_2026.csv), on completed
    NSE 5-minute bars, does the appearance of a named pattern carry ANY
    directional information over the next 5 / 15 / 30 / 60 minutes, measured
    against the unconditional return of entering at the same kind of bar open
    on the same symbol-days?

Design (fixed in advance, no variants):

  * Universe: cached Upstox 1m bars, resampled with the live ``resample_5m``.
  * Detection: prefix scan, so a formation is only known at its own close.
  * Entry: OPEN of the next 5m bar (the first price obtainable after the
    formation completes). No look-ahead.
  * Exit: CLOSE of the bar h bars later, h in {1, 3, 6, 12}; plus session close.
  * Directional return: ``+ret`` for bullish patterns, ``-ret`` for bearish.
    Neutral (indecision) patterns are reported unsigned, as |ret| dispersion
    plus raw mean, because they make no directional claim.
  * Control: EVERY eligible bar open in the same sessions forms the
    unconditional baseline, accumulated as running moments (not stored).
  * Decision rule: a pattern is "informative" only if its directional mean
    beats the baseline mean by more than one round-trip cost (0.21% real MIS)
    AND survives a Bonferroni correction over the number of pattern x horizon
    cells actually reported.

This is a measurement, not a strategy. It has no stop, no target, no sizing,
no selection of which pattern to trade.

Example:
  apps/signal-engine/.venv/bin/python research/backtests/bt28_pattern_census.py \
      --year 2026 --symbols 60 --out research/backtests/bt28_patterns_2026.csv
"""

from __future__ import annotations

import argparse
import math
import multiprocessing as mp
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import NormalDist

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

from src.momentum_trader.candles import (  # noqa: E402
    PATTERN_RULES_VERSION,
    completed_pattern_matches,
)
from src.momentum_trader.engine import resample_5m  # noqa: E402

CACHE = ROOT / "research" / "backtests" / ".cache_upstox" / "1m"
HORIZONS = (1, 3, 6, 12)  # 5m bars -> 5, 15, 30, 60 minutes
ROUND_TRIP_COST = 0.0021  # real MIS all-in, measured earlier in this repo
SESSION_END = pd.Timestamp("15:14").time()
MIN_HISTORY = 11  # ten baseline candles + one formation candle


class Moments:
    """Running count / mean / variance (Welford), so the control never
    materializes millions of rows."""

    __slots__ = ("n", "mean", "m2")

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0

    def add(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (x - self.mean)

    @property
    def std(self) -> float:
        return math.sqrt(self.m2 / (self.n - 1)) if self.n > 1 else float("nan")

    @property
    def stderr(self) -> float:
        return self.std / math.sqrt(self.n) if self.n > 1 else float("nan")

    def merge(self, other: Moments) -> None:
        """Chan/Golub/LeVeque parallel combination, for worker results."""
        if other.n == 0:
            return
        if self.n == 0:
            self.n, self.mean, self.m2 = other.n, other.mean, other.m2
            return
        total = self.n + other.n
        delta = other.mean - self.mean
        self.mean += delta * other.n / total
        self.m2 += other.m2 + delta * delta * self.n * other.n / total
        self.n = total

    def as_tuple(self) -> tuple[int, float, float]:
        return self.n, self.mean, self.m2

    @classmethod
    def from_tuple(cls, data: tuple[int, float, float]) -> Moments:
        moments = cls()
        moments.n, moments.mean, moments.m2 = data
        return moments


def forward_returns(tf5: pd.DataFrame, entry_idx: int) -> dict[str, float] | None:
    """Entry at the open of bar ``entry_idx``; exits h bars later, and EOD."""
    if entry_idx >= len(tf5):
        return None
    entry = float(tf5["open"].iloc[entry_idx])
    if not (entry > 0):
        return None
    out: dict[str, float] = {"entry": entry}
    for h in HORIZONS:
        j = entry_idx + h - 1
        out[f"r{h}"] = (
            float(tf5["close"].iloc[j]) / entry - 1.0 if j < len(tf5) else float("nan")
        )
    out["r_eod"] = float(tf5["close"].iloc[-1]) / entry - 1.0
    out["bars_left"] = float(len(tf5) - entry_idx)
    return out


def scan_symbol_year(
    symbol: str, year: int, baseline: dict[str, Moments]
) -> list[dict[str, object]]:
    path = CACHE / f"{symbol}_{year}.parquet"
    if not path.exists():
        return []
    try:
        bars = pd.read_parquet(path)
    except Exception:  # corrupt cache entry - skip, do not guess
        return []
    if bars.empty or not isinstance(bars.index, pd.DatetimeIndex):
        return []
    rows: list[dict[str, object]] = []
    for _, day in bars.groupby(bars.index.normalize()):
        day = day[day.index.time <= SESSION_END]
        if day.empty:
            continue
        tf5 = resample_5m(day)
        if len(tf5) < MIN_HISTORY + 1:
            continue
        # Control: every bar open that a detector could also have acted on.
        for entry_idx in range(MIN_HISTORY, len(tf5)):
            fwd = forward_returns(tf5, entry_idx)
            if fwd is None:
                continue
            for key in (*[f"r{h}" for h in HORIZONS], "r_eod"):
                value = fwd[key]
                if value == value:  # not NaN
                    baseline[key].add(value)
        seen: set[tuple[str, str, str]] = set()
        for end in range(MIN_HISTORY, len(tf5) + 1):
            for match in completed_pattern_matches(tf5.iloc[:end], "5m"):
                key = (match.name, match.start, match.end)
                if key in seen:
                    continue
                seen.add(key)
                fwd = forward_returns(tf5, end)  # entry = next bar's open
                if fwd is None:
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "year": year,
                        "date": str(tf5.index[end - 1].date()),
                        "formed_start": match.start,
                        "formed_end": match.end,
                        "entry_ts": tf5.index[end].isoformat(),
                        "pattern": match.name,
                        "direction": match.direction,
                        "kind": match.kind,
                        "prior_trend": match.prior_trend,
                        **fwd,
                    }
                )
    return rows


def _worker(
    task: tuple[str, int],
) -> tuple[list[dict[str, object]], dict[str, tuple[int, float, float]]]:
    symbol, year = task
    local: dict[str, Moments] = defaultdict(Moments)
    rows = scan_symbol_year(symbol, year, local)
    return rows, {key: m.as_tuple() for key, m in local.items()}


def summarize(frame: pd.DataFrame, baseline: dict[str, Moments]) -> pd.DataFrame:
    """Directional mean per pattern x horizon, with the control subtracted."""
    out: list[dict[str, object]] = []
    for pattern, grp in frame.groupby("pattern"):
        direction = str(grp["direction"].iloc[0])
        sign = {"bullish": 1.0, "bearish": -1.0}.get(direction, 1.0)
        for key in (*[f"r{h}" for h in HORIZONS], "r_eod"):
            series = grp[key].dropna()
            if series.empty:
                continue
            directional = sign * series
            base = baseline[key]
            edge = directional.mean() - sign * base.mean
            se = directional.std(ddof=1) / math.sqrt(len(directional))
            out.append(
                {
                    "pattern": pattern,
                    "direction": direction,
                    "horizon": key,
                    "n": len(directional),
                    "mean_pct": 100 * directional.mean(),
                    "baseline_pct": 100 * sign * base.mean,
                    "edge_pct": 100 * edge,
                    "t_stat": (edge / se) if se and se == se and se > 0 else float("nan"),
                    "win_rate": float((directional > 0).mean()),
                    "beats_cost": bool(edge > ROUND_TRIP_COST),
                }
            )
    return pd.DataFrame(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--symbols", type=int, default=60, help="0 = all cached")
    ap.add_argument("--seed", type=int, default=20260912)
    ap.add_argument("--jobs", type=int, default=max(1, (mp.cpu_count() or 2) - 2))
    ap.add_argument("--min-n", type=int, default=50,
                    help="minimum detections before a cell may be called informative")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--summary-out", type=Path, default=None)
    args = ap.parse_args()

    names = sorted(p.name.split("_")[0] for p in CACHE.glob(f"*_{args.year}.parquet"))
    names = sorted(set(names))
    if args.symbols and args.symbols < len(names):
        random.Random(args.seed).shuffle(names)
        names = sorted(names[: args.symbols])
    print(f"BT28 {PATTERN_RULES_VERSION}: {len(names)} symbols, year {args.year}")

    baseline: dict[str, Moments] = defaultdict(Moments)
    rows: list[dict[str, object]] = []
    tasks = [(symbol, args.year) for symbol in names]
    if args.jobs == 1:
        results = map(_worker, tasks)
    else:
        pool = mp.Pool(args.jobs)
        results = pool.imap_unordered(_worker, tasks, chunksize=1)
    for i, (got, local) in enumerate(results, 1):
        rows.extend(got)
        for key, data in local.items():
            baseline[key].merge(Moments.from_tuple(data))
        if i % 10 == 0 or i == len(tasks):
            print(f"  [{i}/{len(tasks)}] {len(rows):,} detections so far", flush=True)
    if args.jobs != 1:
        pool.close()
        pool.join()
    frame = pd.DataFrame(rows)
    if frame.empty:
        print("no detections")
        return 1

    out = args.out or (ROOT / "research" / "backtests" / f"bt28_patterns_{args.year}.csv")
    frame.to_csv(out, index=False)
    print(f"\nwrote {out} ({len(frame):,} detections)")

    print("\nbaseline (unconditional, same bars):")
    for key in (*[f"r{h}" for h in HORIZONS], "r_eod"):
        base = baseline[key]
        print(f"  {key}: n={base.n:,} mean={100 * base.mean:+.4f}% sd={100 * base.std:.3f}%")

    summary = summarize(frame, baseline)
    summary_out = args.summary_out or (
        ROOT / "research" / "backtests" / f"bt28_summary_{args.year}.csv"
    )
    summary.to_csv(summary_out, index=False)
    print(f"\nwrote {summary_out}")
    with pd.option_context("display.width", 200):
        print(
            summary.sort_values(["horizon", "edge_pct"], ascending=[True, False])
            .round(4)
            .to_string(index=False)
        )
    cells = max(len(summary), 1)
    threshold = NormalDist().inv_cdf(1 - 0.05 / (2 * cells))
    print(f"\ncells reported: {cells}; Bonferroni(0.05) |t| threshold = {threshold:.2f}")
    print(f"round-trip cost floor used: {100 * ROUND_TRIP_COST:.2f}%")
    survivors = summary[
        (summary.t_stat.abs() >= threshold) & summary.beats_cost & (summary.n >= args.min_n)
    ]
    print(
        f"cells clearing the cost floor, the corrected t threshold, and n>={args.min_n}: "
        f"{len(survivors)}"
    )
    if not survivors.empty:
        print(survivors.round(4).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
