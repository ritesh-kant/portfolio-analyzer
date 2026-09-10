# /// script
# requires-python = ">=3.11"
# dependencies = ["yfinance", "pandas", "numpy"]
# ///
"""BT15 — low-float gap-momentum (continuation) cost-stress screen
(hypothesis: research/hypotheses/2026-07-03-lowfloat-gap-momentum.md).

⚠️ DO NOT RUN until the hypothesis is status: registered + hash-locked. This is
the Warrior-Trading "low float + high demand = rate of change" setup, tested as
the CHEAP DAILY-BAR PROXY: it can only REJECT (KILL) or LICENSE Phase 2 — never
SHIP (a daily PASS only justifies building the intraday 5-min + real-float test).

Gates, evaluated in this order (LOCKED in the hypothesis before this ran):

  Gate -1 FEASIBILITY : % selected gap-days that are T2T / band-locked. KILL if
                        >= 50%. Segment (T2T) data needs the NSE sec_list file
                        (TODO below); the daily bars give only a band-lock PROXY.
  Gate 0   COST-STRESS: long top-K up-gappers, enter open[D] exit close[D],
                        intraday MIS + STRESS slippage +40 bps/side. KILL if
                        mean net per-trade <= 0.
  battery  (only if -1 & 0 pass): gross >= +0.50%/trade; FADE anti <= 0 & <
                        continuation; beta-control alpha (continuation - same-day
                        universe mean) >= +0.30%/trade; cohort net Sharpe >= 0.5;
                        n trade-days >= 30.

Selection (LOCKED): up-gapper = gap_D = open[D]/close[D-1]-1 >= +3% AND prior
volume surge vol[D-1]/mean(vol[D-6..D-1]) >= 1.5x; take top K=10 by gap_D.
All rank inputs known at/before open[D] (PIT). DEV window only (2025-07-01..
2025-12-31); the 2026 hold-out is NOT touched here. Run: `uv run bt15_lowfloat_gap.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "apps" / "signal-engine" / "src"))

from news_trader.nifty500 import NIFTY_500  # noqa: E402
from news_trader.trailing_sl import calc_costs  # noqa: E402  (production intraday MIS)

CACHE = Path(__file__).resolve().parent / ".cache_daily_v"  # v = with Volume (bt14 cache lacks it)
CACHE.mkdir(exist_ok=True)

# --- LOCKED parameters (from the hypothesis) ---
DEV_START, DEV_END = "2025-07-01", "2025-12-31"
FETCH_START, FETCH_END = "2025-06-01", "2026-01-08"  # padding for D-1 and the 5-day vol window
K = 10                          # top up-gappers per day
MIN_GAP = 0.03                  # +3% minimum open gap
MIN_VOL_SURGE = 1.5             # vol[D-1] / mean(vol[D-6..D-1])
POSITION_INR = 50_000.0         # per-name notional
STRESS_SLIP = 0.0040            # +40 bps/side on top of the model (4x the BT14 reversal stress)

# Gate -1 band data, read live from NSE band buckets on 2026-07-03 (see hypothesis
# §3): a +3% gap is truncated only by the 2% or 5% band. Of NIFTY 500, ZERO are in
# the 2% band and only these 7 are in the 5% band (surveillance/ASM, rotates daily).
# ~98.6% of the universe has a 10%/20%/no-band that a +3% gap clears -> Gate -1
# passes trivially for NIFTY 500. ⚠️ This set is date-specific; a production run
# must re-pull the daily NSE price-band file. T2T (BE-series) in NIFTY 500 ≈ 0.
BAND_5PCT_NIFTY500 = frozenset({
    "ADANIENT", "BBTC", "GMDCLTD", "GODREJIND", "KEI", "RKFORGE", "SAREGAMA",
})
BAND_2PCT_NIFTY500: frozenset[str] = frozenset()  # empty on 2026-07-03


def fetch_daily(symbol: str) -> pd.DataFrame | None:
    cache = CACHE / f"{symbol.replace('&', '_')}.csv"
    if cache.exists():
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
        return None if df.empty else df
    import yfinance as yf

    try:
        df = yf.download(
            f"{symbol}.NS", start=FETCH_START, end=FETCH_END, interval="1d",
            progress=False, auto_adjust=False, multi_level_index=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] {symbol}: {exc}", file=sys.stderr)
        df = None
    if df is None or df.empty:
        pd.DataFrame().to_csv(cache)
        return None
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.to_csv(cache)
    return df


def intraday_net(entry: float, exit_: float, qty: int, direction: str) -> float:
    """Production MIS round-trip + STRESS_SLIP/side (the LOCKED Gate-0 cost)."""
    gross = (entry - exit_) * qty if direction == "short" else (exit_ - entry) * qty
    costs = calc_costs(entry, exit_, qty, direction=direction)["total"]
    costs += (entry + exit_) * qty * STRESS_SLIP
    return gross - costs


def band_lock_proxy(o: float, h: float, low: float, c: float) -> bool:
    """Crude daily-bar band-lock flag: fully locked bar (opened and stayed there).
    ⚠️ PROXY ONLY — the real Gate -1 needs the NSE T2T/call-auction sec_list +
    per-symbol price-band file (TODO). Reported, not authoritative."""
    return bool(np.isfinite([o, h, low, c]).all() and o == h == low == c)


def load_panel() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    o, h, lo, c, v = {}, {}, {}, {}, {}
    miss = 0
    for sym in NIFTY_500:
        df = fetch_daily(sym)
        if df is None or len(df) < 10:
            miss += 1
            continue
        o[sym], h[sym], lo[sym], c[sym], v[sym] = (
            df["Open"], df["High"], df["Low"], df["Close"], df["Volume"],
        )
    print(f"universe: {len(c)}/{len(NIFTY_500)} symbols with bars ({miss} missing)")
    frame = lambda d: pd.DataFrame(d).sort_index()  # noqa: E731
    return frame(o), frame(h), frame(lo), frame(c), frame(v)


def run() -> None:
    open_px, high_px, low_px, close_px, vol = load_panel()
    prev_close = close_px.shift(1)
    gap = open_px / prev_close - 1.0                       # known at open[D]
    vol_surge = vol.shift(1) / vol.shift(1).rolling(5).mean()  # vol[D-1]/mean(D-6..D-1), known pre-open
    oc_ret = close_px / open_px - 1.0                      # same-day open->close (trade horizon)

    dates = [d for d in close_px.index if DEV_START <= d.strftime("%Y-%m-%d") <= DEV_END]

    rows: list[dict] = []
    daily: list[dict] = []

    for d in dates:
        g = gap.loc[d]
        vs = vol_surge.loc[d]
        cand = g[(g >= MIN_GAP) & (vs >= MIN_VOL_SURGE)].dropna()
        if cand.empty:
            continue
        picks = cand.nlargest(K).index

        univ_oc = oc_ret.loc[d].dropna()                  # beta control: same-day universe mean o->c
        beta_mean_pct = float(univ_oc.mean() * 100) if len(univ_oc) else np.nan

        cont_grosspct, cont_net, fade_grosspct, locked, tight = [], [], [], 0, 0
        for sym in picks:
            e, x = open_px.at[d, sym], close_px.at[d, sym]
            if not (np.isfinite(e) and np.isfinite(x)) or e <= 0:
                continue
            qty = max(1, int(POSITION_INR // e))
            g_long = (x - e) * qty                        # continuation = long the up-gapper
            if band_lock_proxy(e, high_px.at[d, sym], low_px.at[d, sym], x):
                locked += 1
            if sym in BAND_5PCT_NIFTY500 or sym in BAND_2PCT_NIFTY500:
                tight += 1                                # tight band would truncate a +3% gap
            row = {
                "day": d.date(), "symbol": sym, "gap_pct": round(float(g[sym]) * 100, 2),
                "vol_surge": round(float(vs[sym]), 2), "entry": round(e, 2), "exit": round(x, 2),
                "qty": qty, "cont_ret_pct": round(g_long / (e * qty) * 100, 4),
                "cont_net_stress": round(intraday_net(e, x, qty, "long"), 2),
            }
            rows.append(row)
            cont_grosspct.append(row["cont_ret_pct"])
            cont_net.append(row["cont_net_stress"])
            fade_grosspct.append(-row["cont_ret_pct"])    # anti = short the up-gapper
        if cont_grosspct:
            daily.append({
                "day": d.date(), "n": len(cont_grosspct),
                "cont_gross_pct": float(np.mean(cont_grosspct)),
                "fade_gross_pct": float(np.mean(fade_grosspct)),
                "cont_net_stress_inr": float(np.mean(cont_net)),
                "beta_mean_pct": beta_mean_pct,
                "locked_frac": locked / len(cont_grosspct),
                "tight_band_frac": tight / len(cont_grosspct),
            })

    if not daily:
        print("no gap-days selected — check thresholds / data coverage")
        return

    dd = pd.DataFrame(daily)
    trades = pd.DataFrame(rows)
    trades.to_csv(Path(__file__).parent / "bt15_trades_dev.csv", index=False)

    def sharpe(s: pd.Series) -> float:
        s = s.dropna()
        return float(s.mean() / s.std() * np.sqrt(252)) if len(s) > 1 and s.std() > 0 else 0.0

    n_days = len(dd)
    cont_gross = trades["cont_ret_pct"].mean()
    cont_net = trades["cont_net_stress"].mean()
    fade_gross = dd["fade_gross_pct"].mean()
    beta_alpha = cont_gross - dd["beta_mean_pct"].mean()
    locked_frac = float(np.average(dd["locked_frac"], weights=dd["n"]))
    tight_frac = float(np.average(dd["tight_band_frac"], weights=dd["n"]))
    shp = sharpe(dd["cont_net_stress_inr"])

    print("\n" + "=" * 70)
    print(f"BT15 — low-float gap momentum | DEV {DEV_START}..{DEV_END} | K={K}/day")
    print(f"gap>=+{MIN_GAP:.0%}  vol_surge>={MIN_VOL_SURGE}x  "
          f"trade-days={n_days}  trades={len(trades)}  pos=Rs{POSITION_INR:,.0f}")
    print("=" * 70)
    print("\n-- GATE -1 (FEASIBILITY) --")
    print(f"  tight-band (2%/5%) fraction, measured : {tight_frac:.1%}  "
          f"{'FAIL -> KILL' if tight_frac >= 0.50 else 'pass'}  "
          f"(only 7/504 NIFTY 500 in 5% band on 2026-07-03; expect ~0)")
    print(f"  band-locked-at-close fraction (proxy) : {locked_frac:.1%}  "
          f"{'FAIL -> KILL' if locked_frac >= 0.50 else 'pass (proxy)'}")
    print("  ⚠️ Interpretation: a low tight-band frac means the test is TRADEABLE but is")
    print("     measuring LIQUID-name gap continuation, NOT Warrior's low-float runners")
    print("     (which live in the SME/T2T/5%-band microcaps NIFTY 500 excludes). See hypothesis §3.")
    print("\n-- GATE 0 (COST-STRESS, +40bps/side) --")
    print(f"  continuation NET/trade : Rs{cont_net:+,.1f}  "
          f"{'PASS' if cont_net > 0 else 'FAIL -> KILL'}")
    print("\n-- battery (only meaningful if -1 & 0 pass) --")
    print(f"  continuation gross     : {cont_gross:+.4f}%/trade  "
          f"({'PASS' if cont_gross >= 0.50 else 'FAIL'} vs +0.50 floor)")
    print(f"  FADE anti gross        : {fade_gross:+.4f}%/trade  "
          f"({'OK <=0 & < cont' if fade_gross <= 0 and fade_gross < cont_gross else 'WARN not distinct'})")
    print(f"  beta-control alpha     : {beta_alpha:+.4f}%/trade  "
          f"(cont {cont_gross:+.3f} - univ {dd['beta_mean_pct'].mean():+.3f}; "
          f"{'PASS' if beta_alpha >= 0.30 else 'FAIL'} vs +0.30 floor)")
    print(f"  cohort net Sharpe      : {shp:+.3f}  ({'PASS' if shp >= 0.5 else 'FAIL'} vs 0.5)")
    print(f"  n trade-days >= 30     : {'PASS' if n_days >= 30 else 'FAIL'}")
    print(f"\n  trades -> bt15_trades_dev.csv")


if __name__ == "__main__":
    run()
