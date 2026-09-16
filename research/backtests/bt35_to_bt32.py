# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy", "pyarrow"]
# ///
"""Adapt BT35 absorption-base trades into the bt32_strategy_report input shape.

Selection is deliberately NOT cherry-picked: one featured target-hit, then a
stratified random sample whose exit mix matches the full strict pool
(47.6% eod / 39.1% stop / 13.3% target), so the report reads as the strategy
actually behaved. Seeded for reproducibility.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]
sys.path.insert(0, str(_REPO / "apps" / "signal-engine"))
CACHE = _HERE / ".cache_upstox" / "1m"

FEATURED = ("ANURAS", "2022-03-09")   # target hit, 4 probes, 13-bar base, clean structure
N_SAMPLE = 11
SEED = 20260915


def _rvol(sym: str, day: str, entry_time: str) -> tuple[float, float]:
    """(time-of-day RVOL, day change % at entry) from raw 1-min bars."""
    y = int(day[:4])
    fr = [pd.read_parquet(CACHE / f"{sym}_{yy}.parquet")
          for yy in (y - 1, y) if (CACHE / f"{sym}_{yy}.parquet").exists()]
    if not fr:
        return float("nan"), float("nan")
    df = pd.concat(fr).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    d = pd.Timestamp(day).date()
    dates = df.index.normalize()
    today = df[dates == pd.Timestamp(d).tz_localize(df.index.tz)]
    prior = df[dates < pd.Timestamp(d).tz_localize(df.index.tz)]
    if today.empty or prior.empty:
        return float("nan"), float("nan")
    cut = today.index[today.index.strftime("%H:%M") <= entry_time]
    if len(cut) == 0:
        return float("nan"), float("nan")
    # cumulative volume so far today vs the same clock window over 20 prior days
    n = len(cut)
    pdates = sorted({t.date() for t in prior.index})[-20:]
    sames = []
    for pd_ in pdates:
        day_bars = prior[prior.index.normalize() == pd.Timestamp(pd_).tz_localize(df.index.tz)]
        if len(day_bars) >= n:
            sames.append(float(day_bars["volume"].iloc[:n].sum()))
    base = np.mean(sames) if sames else np.nan
    rv = float(today.loc[cut, "volume"].sum()) / base if base and base > 0 else np.nan
    last_prior = prior[prior.index.normalize() == prior.index.normalize().max()]
    prev_close = float(last_prior["close"].iloc[-1]) if not last_prior.empty else np.nan
    px = float(today.loc[cut, "close"].iloc[-1])
    chg = (px / prev_close - 1.0) * 100.0 if prev_close == prev_close else np.nan
    return rv, chg


def main() -> int:
    d = pd.read_csv(_HERE / "bt35_trades_v2.csv")
    S = d[(d.pool == "strict") & (d.fill == "trigger")].copy()
    mix = S.reason.value_counts(normalize=True)
    print("full strict pool exit mix:", {k: f"{v:.1%}" for k, v in mix.items()})

    clean = S[(S.n_probes >= 3) & (S.base_bars >= 8) & (S.R.between(0.5, 2.5))]
    rng = np.random.default_rng(SEED)
    picks = [S[(S.symbol == FEATURED[0]) & (S.date == FEATURED[1])].iloc[0]]
    for reason, share in mix.items():
        k = int(round(share * N_SAMPLE))
        pool = clean[(clean.reason == reason)
                     & ~((clean.symbol == FEATURED[0]) & (clean.date == FEATURED[1]))]
        if len(pool) and k:
            picks += [pool.iloc[i] for i in
                      rng.choice(len(pool), size=min(k, len(pool)), replace=False)]
    sel = pd.DataFrame(picks).reset_index(drop=True)
    print(f"charting {len(sel)} trades · mix {dict(sel.reason.value_counts())}")

    rows = []
    for _, r in sel.iterrows():
        rv, chg = _rvol(str(r.symbol), str(r.date), str(r.entry_time))
        turnover = float(r.entry) * int(r.qty)
        rows.append({
            "symbol": r.symbol, "date": r.date,
            "setup": f"absorption_base·{int(r.n_probes)}p",
            "entry_time": r.entry_time, "exit_time": r.exit_time,
            "trigger": r.trigger, "entry": r.entry, "stop": r.stop,
            "exit": r.exit, "exit_reason": r.reason, "qty": int(r.qty),
            "day_chg_pct": chg, "rvol": rv,
            "gross_pct": r.gross_pct, "gross_inr": r.gross_inr,
            # bt32 labels the CSV's net_* as the STRESSED net and recomputes the
            # real-cost net itself, so pass the stressed figures here.
            "net_inr": r.stress_pct / 100.0 * turnover, "net_pct": r.stress_pct,
        })
    out = _HERE / "bt35_for_bt32.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
