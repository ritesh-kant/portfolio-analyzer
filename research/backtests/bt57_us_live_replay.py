"""BT57 (descriptive): the deployed US long rule replayed over every cached 2026
session of every symbol in the IBKR cache. No hypothesis, no tuning.

There is no historical US screen snapshot before 2026-09-24, so the engine's own
attention gates (green candle, volume, micro-pullback, VWAP) are the only
selection: each symbol-day is replayed from the 09:30 open. The symbols are the
late-September watchlist names, so the pool is survivorship-biased.
Reuses the BT56 per-day machinery, current-rule arm only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bt56_ibkr_us_paired as B56  # noqa: E402

B56.ARMS = ("before",)  # re-applied in spawned workers, which re-import this module
# BT58 arm: BT57_FLOOR=5 swaps the attention day-change floor (default 10 = deployed).
FLOOR = float(os.environ.get("BT57_FLOOR", "10"))
if FLOOR != 10.0:
    _Base = B56.USUniverseConfig
    B56.USUniverseConfig = lambda: _Base(day_chg_min_pct=FLOOR)

TAG = "us_live_2026_20261003"
OUT = B56.OUT


def _job(path: str) -> dict:
    df = B56._history(Path(path))
    symbol = Path(path).name.split("_")[0]
    entries = [{"date": str(d), "first_passed_at": f"{d} 09:30:00"}
               for d in sorted(set(df.index.date))]
    res = B56._symbol_job(symbol, entries)
    res["rows"] = res["rows"].get("before", [])
    return res


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--jobs", type=int, default=6)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--tag", default=TAG)
    p.add_argument("--prefix", default="bt57")
    args = p.parse_args()
    files = sorted((B56.CACHE / "1m").glob("*_2026.parquet"))[:args.limit or None]
    rows, coverage = [], []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futs = [pool.submit(_job, str(f)) for f in files]
        for n, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            rows.extend(r.pop("rows"))
            coverage.append(r)
            print(f"{n}/{len(futs)} symbols, {len(rows)} trades", flush=True)
    frame = pd.DataFrame(rows)
    for col in B56.REQUIRED:
        if col not in frame:
            frame[col] = pd.Series(dtype=object)
    frame["currency"] = "USD"
    if args.limit:
        print(B56._metrics(rows))
        return
    dest = OUT / f"{args.prefix}_trades_{args.tag}.csv"
    if dest.exists():
        raise FileExistsError(dest)
    frame.sort_values(["date", "symbol", "entry_time"]).to_csv(dest, index=False)
    summary = {"tag": args.tag, "attention_day_chg_min": FLOOR, "currency": "USD", "descriptive": True,
               "symbols": len(files), "symbol_days": sum(c["days"] for c in coverage),
               "metrics": B56._metrics(rows), "coverage": coverage}
    (OUT / f"{args.prefix}_{args.tag}_summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "coverage"}, indent=2))


if __name__ == "__main__":
    main()
