# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy", "pyarrow"]
# ///
"""BT35 secondary arm — same absorption base on DAILY bars (descriptive only).

Pre-registered in research/hypotheses/2026-09-15-absorption-base-failed-breakdown.md
as "secondary, descriptive": the pattern is timeframe-agnostic in principle, so the
daily frame is reported regardless of outcome. GROSS only — the intraday MIS cost
model does not apply to multi-day holds (delivery STT is 0.1%/side vs 0.025%
sell-only, so real net here would be materially worse than the intraday arm).
Parameters are identical to the primary and unchanged.
"""
from __future__ import annotations
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path
import numpy as np, pandas as pd

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "apps" / "signal-engine"))
from src.momentum_trader import universe                    # noqa: E402
from src.momentum_trader.indicators import atr              # noqa: E402
from src.momentum_trader.levels import PIVOT_K, swing_pivot_positions  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bt35_absorption_base import (                          # noqa: E402
    CACHE, _daily_from_1m, _detect, APPROACH_BARS, TARGET_R,
)

MAX_HOLD = 20   # trading days; the intraday arm's EOD-close analogue


def _scan(args):
    sym, start, end = args
    frames = []
    for y in range(start.year, end.year + 1):
        f = CACHE / f"{sym}_{y}.parquet"
        if f.exists():
            try: frames.append(pd.read_parquet(f))
            except Exception: pass
    if not frames: return []
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    if df.empty: return []
    dly = _daily_from_1m(df)
    if len(dly) < 60: return []
    a_v = atr(dly, 14).to_numpy()
    piv = swing_pivot_positions(dly, PIVOT_K)[1]
    turn20 = dly["turnover_cr"].rolling(20).mean().shift(1).to_numpy()
    h, lo, c = dly["high"].to_numpy(), dly["low"].to_numpy(), dly["close"].to_numpy()
    out, busy_until = [], -1
    for t in range(APPROACH_BARS + 15, len(dly)):
        if t <= busy_until: continue
        d0 = dly.index[t].date()
        if d0 < start or d0 > end: continue
        if not np.isfinite(a_v[t]) or not np.isfinite(turn20[t]): continue
        if not (universe.PRICE_MIN <= c[t] <= universe.PRICE_MAX): continue
        if not (universe.TURNOVER_MIN_CR <= turn20[t] <= universe.TURNOVER_MAX_CR): continue
        for mp in (2, 1):
            sig = _detect(dly, a_v, t, [j for j in piv if j <= t], mp)
            if sig is None: continue
            trig, stop = sig["trigger"], sig["stop"]
            tgt = trig + TARGET_R * (trig - stop)
            ei = None
            for j in range(t + 1, min(t + 1 + MAX_HOLD, len(dly))):
                if c[j] < sig["level"]: break
                if h[j] >= trig: ei = j; break
            if ei is None: continue
            ex, why = float(c[min(ei + MAX_HOLD, len(dly) - 1)]), "maxhold"
            for j in range(ei, min(ei + MAX_HOLD + 1, len(dly))):
                if lo[j] <= stop: ex, why = stop, "stop"; break
                if h[j] >= tgt: ex, why = tgt, "target"; break
            out.append({"symbol": sym, "date": str(d0), "year": d0.year,
                        "pool": "strict" if mp == 2 else "loose",
                        "n_probes": sig["n_probes"], "R": (trig - stop) / trig * 100,
                        "gross_pct": (ex - trig) / trig * 100, "reason": why})
            if mp == 2: busy_until = ei + MAX_HOLD
    return out


if __name__ == "__main__":
    start, end = date(2022, 1, 1), date(2023, 12, 31)
    syms = sorted({f.name.rsplit("_", 1)[0] for f in CACHE.glob("*.parquet")})
    rows = []
    with ProcessPoolExecutor(max_workers=10) as ex:
        for r in ex.map(_scan, [(s, start, end) for s in syms]): rows.extend(r)
    d = pd.DataFrame(rows)
    d.to_csv(Path(__file__).resolve().parent / "bt35_daily_trades.csv", index=False)
    print(f"rows {len(d)} · symbols {d.symbol.nunique() if len(d) else 0}\n")
    if len(d):
        print(d.groupby("pool").agg(n=("gross_pct","size"), gross=("gross_pct","mean"),
              win=("gross_pct", lambda s:(s>0).mean()*100), R=("R","mean")).round(4))
        S = d[d.pool=="strict"]
        print("\nper-year (strict):")
        print(S.groupby("year").agg(n=("gross_pct","size"), gross=("gross_pct","mean")).round(4))
        print("\nexits (strict):"); print(S.reason.value_counts(normalize=True).round(3))
        se = S.gross_pct.std()/np.sqrt(len(S))
        print(f"\nstrict gross {S.gross_pct.mean():+.4f}%  se {se:.4f}  t {S.gross_pct.mean()/se:+.2f}")
