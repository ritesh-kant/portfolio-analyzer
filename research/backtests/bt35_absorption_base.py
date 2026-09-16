# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy", "pyarrow"]
# ///
"""BT35 — absorption base / failed breakdown (spring).

Hypothesis: research/hypotheses/2026-09-15-absorption-base-failed-breakdown.md

Single-shot on 2022-23 (a spent window, deliberately: a contaminated window can
kill but cannot bless). Every parameter is fixed a priori in the hypothesis doc
and read off the operator's screenshots, not off data. No re-runs with adjusted
parameters.

Reads the existing bt17 parquet cache directly (research/backtests/.cache_upstox/1m)
so it needs no Upstox token.

  uv run research/backtests/bt35_absorption_base.py --start 2022-01-01 --end 2023-12-31
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "apps" / "signal-engine"))

from src.momentum_trader import universe                       # noqa: E402
from src.momentum_trader.engine import resample_5m             # noqa: E402
from src.momentum_trader.indicators import atr                 # noqa: E402
from src.momentum_trader.levels import (                       # noqa: E402
    CLUSTER_TOL_PCT, PIVOT_K, swing_pivot_positions,
)
from src.news_trader.trailing_sl import calc_costs             # noqa: E402
from src.news_trader.nifty500 import NIFTY_500                 # noqa: E402

CACHE = Path(__file__).resolve().parent / ".cache_upstox" / "1m"

# ── a-priori parameters (hypothesis §"Parameters"; do not tune) ──────────────
MIN_PROBES = 2             # charts 2 and 4 each mark two attempts
BASE_BARS = 20             # shelves run ~8-15 bars
CLOSE_LOC_MIN = 0.60       # long lower wicks, small bodies
APPROACH_ATR = 1.5         # steep drop into the level
APPROACH_BARS = 20
VOL_RATIO_MIN = 1.0        # "buyer is buying everything"
TARGET_R = 2.0             # system convention
ARM_CUTOFF = "14:45"       # system convention
EOD = "15:10"
NOTIONAL = 100_000.0

# The operator's stock-selection gates, taken from engine.py (not chosen here).
# "none" is what the pre-registered BT35 run used: price + 20d turnover only.
UNIVERSE_GATES = {
    "none":      None,
    "attention": {"chg_min": 1.5, "chg_max": None, "rvol_min": 1.5},   # DEPLOYED arm
    "warrior":   {"chg_min": 4.0, "chg_max": 8.0,  "rvol_min": 3.0},   # legacy path
}
STRESS_SLIP = 0.0040       # +40 bps/side, same stress as bt17


def _daily_from_1m(df: pd.DataFrame) -> pd.DataFrame:
    day = df.index.normalize()
    g = df.groupby(day)
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
        "close": g["close"].last(), "volume": g["volume"].sum(),
    })
    out["turnover_cr"] = (out["close"] * out["volume"]) / 1e7
    return out


def _active_levels(low: np.ndarray, vol: np.ndarray, piv_lows: list[int], t: int
                   ) -> list[tuple[float, int]]:
    """Clustered pivot-low levels confirmed at or before bar t. (price, touches).

    A pivot at index j is only confirmed once PIVOT_K bars have printed after it,
    so nothing here looks ahead."""
    pts = sorted(float(low[j]) for j in piv_lows if j + PIVOT_K <= t)
    if not pts:
        return []
    groups: list[list[float]] = [[pts[0]]]
    for p in pts[1:]:
        if abs(p - groups[-1][0]) / max(groups[-1][0], 1e-9) * 100.0 <= CLUSTER_TOL_PCT:
            groups[-1].append(p)
        else:
            groups.append([p])
    return [(sum(g) / len(g), len(g)) for g in groups]


def _detect(bars: pd.DataFrame, atr_v: np.ndarray, t: int, piv_lows: list[int],
            min_probes: int) -> dict | None:
    """Absorption base as of closed bar t, or None. No look-ahead: only bars[:t+1]."""
    o = bars["open"].to_numpy(); h = bars["high"].to_numpy()
    lo = bars["low"].to_numpy(); c = bars["close"].to_numpy()
    v = bars["volume"].to_numpy()

    b0 = max(0, t - BASE_BARS + 1)
    for L, _touches in _active_levels(lo, v, piv_lows, t):
        if not (c[t] > L):
            continue
        probes = [j for j in range(b0, t + 1) if lo[j] < L and c[j] > L]
        if len(probes) < min_probes:
            continue
        first = probes[0]
        base = slice(first, t + 1)
        # no follow-through: wicks below are allowed, closes are not
        if (c[base] < L).any():
            continue
        # close location: bars finish in the top of their own range
        rng = np.maximum(h[base] - lo[base], 1e-9)
        if float(np.mean((c[base] - lo[base]) / rng)) < CLOSE_LOC_MIN:
            continue
        # approach: sellers were in control coming in
        a0 = max(0, first - APPROACH_BARS)
        if a0 >= first:
            continue
        if float(h[a0:first].max()) - L < APPROACH_ATR * float(atr_v[first]):
            continue
        # absorption: volume did not fall off while price stopped falling
        va = float(v[a0:first].mean())
        if va <= 0 or float(v[base].mean()) / va < VOL_RATIO_MIN:
            continue

        trigger = float(h[base].max())
        stop = float(lo[base].min())
        if trigger <= c[t] or stop <= 0 or (trigger - stop) / trigger < 0.001:
            continue
        return {"level": L, "trigger": trigger, "stop": stop,
                "n_probes": len(probes), "base_bars": t + 1 - first,
                "vol_ratio": float(v[base].mean()) / va}
    return None


def _simulate(bars: pd.DataFrame, sig: dict, t: int, fill_mode: str) -> dict | None:
    """Arm the buy-stop at bar t; walk forward. Cancel if a bar closes below L."""
    h = bars["high"].to_numpy(); lo = bars["low"].to_numpy()
    c = bars["close"].to_numpy(); op = bars["open"].to_numpy()
    idx = bars.index
    trig, stop, L = sig["trigger"], sig["stop"], sig["level"]
    target = trig + TARGET_R * (trig - stop)

    entry = None; ei = None
    for j in range(t + 1, len(bars)):
        if idx[j].strftime("%H:%M") > EOD:
            break
        if c[j] < L and entry is None:
            return None                      # base failed before the break
        if h[j] >= trig:
            entry = trig if fill_mode == "trigger" else (
                float(op[j + 1]) if j + 1 < len(bars) else None)
            ei = j if fill_mode == "trigger" else j + 1
            break
    if entry is None or ei is None or ei >= len(bars):
        return None

    exit_px, reason, xi = None, "eod", len(bars) - 1
    for j in range(ei, len(bars)):
        if idx[j].strftime("%H:%M") > EOD:
            exit_px, reason, xi = float(c[j - 1]), "eod", j - 1
            break
        if lo[j] <= stop:                    # stop checked first (conservative)
            exit_px, reason, xi = stop, "stop", j
            break
        if h[j] >= target:
            exit_px, reason, xi = target, "target", j
            break
    if exit_px is None:
        exit_px, reason, xi = float(c[-1]), "eod", len(bars) - 1

    qty = max(int(NOTIONAL // entry), 1)
    gross_inr = (exit_px - entry) * qty
    cost_inr = calc_costs(entry, exit_px, qty, direction="long")["total"]
    stress_inr = cost_inr + (entry + exit_px) * qty * STRESS_SLIP
    turnover = entry * qty
    return {
        "entry": entry, "exit": exit_px, "reason": reason, "qty": qty,
        "entry_time": idx[ei].strftime("%H:%M"), "exit_time": idx[xi].strftime("%H:%M"),
        "trigger": trig, "stop": stop, "level": L, "target": target,
        "gross_pct": (exit_px - entry) / entry * 100.0,
        "cost_pct": cost_inr / turnover * 100.0,
        "net_pct": (gross_inr - cost_inr) / turnover * 100.0,
        "stress_pct": (gross_inr - stress_inr) / turnover * 100.0,
        "gross_inr": gross_inr, "net_inr": gross_inr - cost_inr,
        "R": (trig - stop) / trig * 100.0,
    }


def _day_context(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(cumulative session volume by clock time, 20-day trailing profile of the same).

    Profile is shifted one day, so a day never sees its own volume: time-of-day
    RVOL = today's cumulative volume at time T / the prior-20-day mean at T.
    """
    v = df["volume"].astype(float)
    dayk = df.index.normalize()
    cum = v.groupby(dayk).cumsum()
    piv = pd.DataFrame({"d": dayk, "t": df.index.time, "c": cum}).pivot_table(
        index="d", columns="t", values="c", aggfunc="last")
    prof = piv.shift(1).rolling(20, min_periods=5).mean()
    return piv, prof


def _scan_symbol(args: tuple[str, date, date, str]) -> list[dict]:
    sym, start, end, gate_name = args
    gate = UNIVERSE_GATES[gate_name]
    if gate is not None and sym not in NIFTY_500:
        return []
    frames = []
    for year in range(start.year, end.year + 1):
        f = CACHE / f"{sym}_{year}.parquet"
        if f.exists():
            try:
                frames.append(pd.read_parquet(f))
            except Exception:
                pass
    if not frames:
        return []
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    if df.empty:
        return []

    daily = _daily_from_1m(df)
    turn20 = daily["turnover_cr"].rolling(20).mean().shift(1)
    prev_close = daily["close"].shift(1)

    bars5_all = resample_5m(df)
    if bars5_all.empty:
        return []
    piv, prof = _day_context(df) if gate is not None else (None, None)
    atr_all = atr(bars5_all, 14).to_numpy()
    pos = {ts: i for i, ts in enumerate(bars5_all.index)}

    out: list[dict] = []
    for d, drow in daily.iterrows():
        dd = d.date()
        if dd < start or dd > end:
            continue
        pc = prev_close.get(d, np.nan); tv = turn20.get(d, np.nan)
        if not (np.isfinite(pc) and np.isfinite(tv)):
            continue
        if not (universe.PRICE_MIN <= pc <= universe.PRICE_MAX):
            continue
        if not (universe.TURNOVER_MIN_CR <= tv <= universe.TURNOVER_MAX_CR):
            continue

        day = bars5_all[bars5_all.index.normalize() == d]
        if len(day) < 15:
            continue
        a_v = atr_all[[pos[ts] for ts in day.index]]
        if not np.isfinite(a_v).any():
            continue
        day = day.reset_index().set_index("timestamp") if "timestamp" in day.columns else day
        piv_lows = swing_pivot_positions(day, PIVOT_K)[1]

        chg_v = rv_v = None
        if gate is not None:
            if d not in piv.index or prof.loc[d].isna().all():
                continue
            # the 5m bar starting at T ends with the 1-min bar starting at T+4
            tt = [(ts + pd.Timedelta(minutes=4)).time() for ts in day.index]
            cum_row, prof_row = piv.loc[d], prof.loc[d]
            chg_v = (day["close"].to_numpy() / pc - 1.0) * 100.0
            rv_v = np.array([
                (cum_row[t] / prof_row[t])
                if (t in cum_row.index and t in prof_row.index
                    and np.isfinite(prof_row[t]) and prof_row[t] > 0) else np.nan
                for t in tt])

        # first STRICT and first LOOSE candidate of the day
        got = {2: False, 1: False}
        for t in range(2 * PIVOT_K + 1, len(day)):
            if day.index[t].strftime("%H:%M") > ARM_CUTOFF:
                break
            if not np.isfinite(a_v[t]):
                continue
            if gate is not None:
                if not np.isfinite(rv_v[t]) or rv_v[t] < gate["rvol_min"]:
                    continue
                if chg_v[t] < gate["chg_min"]:
                    continue
                if gate["chg_max"] is not None and chg_v[t] > gate["chg_max"]:
                    continue
            for mp in (2, 1):
                if got[mp]:
                    continue
                sig = _detect(day, a_v, t, piv_lows, mp)
                if sig is None:
                    continue
                got[mp] = True
                for fm in ("trigger", "nextopen"):
                    tr = _simulate(day, sig, t, fm)
                    if tr is None:
                        continue
                    out.append({"symbol": sym, "date": str(dd), "year": dd.year,
                                "pool": "strict" if mp == 2 else "loose",
                                "fill": fm, "bar": day.index[t].strftime("%H:%M"),
                                "gate": gate_name,
                                "day_chg_pct": (float(chg_v[t]) if chg_v is not None
                                                else float((day["close"].iloc[t] / pc - 1) * 100)),
                                "rvol": float(rv_v[t]) if rv_v is not None else float("nan"),
                                **{k: v for k, v in sig.items()
                                   if k not in ("trigger", "stop", "level")},
                                **tr})
            if all(got.values()):
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="bt35_trades.csv")
    ap.add_argument("--universe", default="none", choices=sorted(UNIVERSE_GATES),
                    help="operator stock-selection gate: none (price+turnover only, "
                         "the pre-registered run), attention (>=1.5%% day chg, RVOL>=1.5, "
                         "the DEPLOYED arm), warrior (4-8%% day chg, RVOL>=3, legacy path)")
    a = ap.parse_args()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)

    syms = sorted({f.name.rsplit("_", 1)[0] for f in CACHE.glob("*.parquet")
                   if any((CACHE / f"{f.name.rsplit('_', 1)[0]}_{y}.parquet").exists()
                          for y in range(start.year, end.year + 1))})
    if a.limit:
        syms = syms[:a.limit]
    print(f"BT35 · {len(syms)} symbols · {start}..{end} · jobs={a.jobs} · universe={a.universe}", flush=True)

    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        for i, res in enumerate(ex.map(_scan_symbol, [(s, start, end, a.universe) for s in syms]), 1):
            rows.extend(res)
            if i % 25 == 0:
                print(f"  {i}/{len(syms)} symbols · {len(rows)} rows", flush=True)

    df = pd.DataFrame(rows)
    out = Path(__file__).resolve().parent / a.out
    df.to_csv(out, index=False)
    print(f"\nwrote {out} · {len(df)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
