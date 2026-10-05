"""BT57 diagnostic: where do US mover days die in the live-rule funnel?

Runs the BT56 per-day machinery on every cached symbol-day whose high is >=10%
above the prior close at a $1-20 prior close, and records for each day whether
the engine promoted it to attention, which refusals fired, and whether it traded.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bt56_ibkr_us_paired as B  # noqa: E402
from src.momentum_trader import engine as E  # noqa: E402

B.ARMS = ("before",)
DAYS: list[dict] = []
_orig = E.run_day


def _capture(symbol, day, *a, **k):
    state = _orig(symbol, day, *a, **k)
    DAYS.append({
        "date": str(day.index[0].date()), "symbol": symbol,
        "promoted": len(state.attention_events),
        "first_promo": str(state.attention_events[0].time.time()) if state.attention_events else None,
        "reasons": dict(Counter(r.reason for r in state.rejections)),
        "trades": len(state.closed),
    })
    return state


E.run_day = _capture


def _job(path: str) -> list[dict]:
    DAYS.clear()
    df = B._history(Path(path))
    dates = sorted(set(df.index.date))
    entries = []
    for i, d in enumerate(dates[1:], 1):
        pc = float(df[df.index.date == dates[i - 1]].close.iloc[-1])
        day = df[df.index.date == d]
        if 1 <= pc <= 20 and len(day) >= 30 and day.high.max() / pc - 1 >= 0.10:
            entries.append({"date": str(d), "first_passed_at": f"{d} 09:30:00"})
    if entries:
        res = B._symbol_job(Path(path).name.split("_")[0], entries)
        short = set(res.get("short_profiles", []))
        for r in DAYS:
            r["short_profile"] = r["date"] in short
    return list(DAYS)


def main() -> None:
    files = sorted((B.CACHE / "1m").glob("*_2026.parquet"))
    out: list[dict] = []
    with ProcessPoolExecutor(max_workers=6) as pool:
        for n, rows in enumerate(pool.map(_job, map(str, files)), 1):
            out.extend(rows)
            if n % 10 == 0:
                print(f"{n}/{len(files)} symbols, {len(out)} mover days", flush=True)
    df = pd.DataFrame(out)
    df.to_json(B.OUT / "bt57_funnel_days.json", orient="records", indent=1)
    for label, sub in (("all mover days", df), ("full 15+ session profile", df[~df.short_profile])):
        promoted = sub[sub.promoted > 0]
        print(f"\n== {label}: {len(sub)}")
        print(f"promoted to attention: {len(promoted)}   traded: {int((sub.trades > 0).sum())}")
        c = Counter()
        for r in promoted.reasons:
            c.update(r.keys())
        print("refusal reasons on promoted days (days with >=1):", c.most_common(10))
    print("\nwhat the engine says on days it never promoted:")
    c = Counter()
    for r in df[df.promoted == 0].reasons:
        c.update(r.keys())
    print(c.most_common(6), "| days with no refusal at all:", int((df[df.promoted == 0].reasons.map(len) == 0).sum()))


if __name__ == "__main__":
    main()
