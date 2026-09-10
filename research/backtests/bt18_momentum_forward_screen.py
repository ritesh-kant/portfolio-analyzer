# /// script
# requires-python = ">=3.12"
# dependencies = ["yfinance", "pandas", "numpy"]
# ///
"""BT18 — forward screen for the momentum-trader paper log
(hypothesis: research/hypotheses/2026-09-05-momentum-catalyst-upstox-v2.md §3).

Reads the scanner's forward log (`mt_forward_log.csv`, spec §6 schema — one row
per CLOSED paper trade, already net of production MIS costs) and applies the
LOCKED gates once n(catalyst-gated trades) ≥ 30. Same gate logic as bt16, re-
pointed at the new schema; the +40 bps/side stress is added here because the
scanner logs real-cost P&L only.

  Gate 0 : catalyst-gated NET/trade (stressed) > 0
  spread : mean gross%(catalyst) − mean gross%(no-catalyst) ≥ +0.40, signed +
  anti   : label-shuffle p(shuffled spread ≥ observed) < 0.10
  beta   : catalyst-gated gross − same-day NIFTY 500 (^CRSLDX) open→close ≥ +0.30
  Sharpe : catalyst-gated stressed net, per-trade annualised ≥ 0.5
  n      : catalyst-gated ≥ 30

Secondaries (reported, not gates): per-setup, per-candle-tag, win rate vs 33%,
exit-reason mix, float_filter_applied split.

Usage:
  uv run research/backtests/bt18_momentum_forward_screen.py            # screen if ready
  uv run research/backtests/bt18_momentum_forward_screen.py --status   # accumulation only
  uv run research/backtests/bt18_momentum_forward_screen.py --log path/to/other.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

LOG = Path(__file__).resolve().parent / "mt_forward_log.csv"

# --- LOCKED (hypothesis v2 §3) ---
MIN_N = 30
SPREAD_FLOOR = 0.40
BETA_FLOOR = 0.30
ANTI_ALPHA = 0.10
SHARPE_FLOOR = 0.5
STRESS_SLIP = 0.0040
SHUFFLES = 5000
SEED = 20260905


def market_oc(date: str) -> float | None:
    import yfinance as yf
    try:
        df = yf.download("^CRSLDX", start=date, end=str(pd.Timestamp(date) + pd.Timedelta(days=4)),
                         interval="1d", progress=False, auto_adjust=False, multi_level_index=False)
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    except Exception:  # noqa: BLE001
        return None
    ts = pd.Timestamp(date)
    if df.empty or ts not in df.index:
        return None
    o, c = float(df.loc[ts, "Open"]), float(df.loc[ts, "Close"])
    return (c / o - 1.0) * 100 if o > 0 else None


def load(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    num = ["fill_px", "exit_px", "qty", "gross_inr", "costs_inr", "net_inr", "catalyst", "rvol",
           "day_chg_pct_at_trigger", "float_filter_applied", "prev_day_gainer"]
    for c in num:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["fill_px", "exit_px", "qty", "net_inr"])
    notional = df["fill_px"] * df["qty"]
    df["gross_pct"] = (df["exit_px"] / df["fill_px"] - 1.0) * 100
    stress = (df["fill_px"] + df["exit_px"]) * df["qty"] * STRESS_SLIP
    df["net_stressed_inr"] = df["net_inr"] - stress
    df["net_pct"] = df["net_stressed_inr"] / notional * 100
    df["catalyst"] = df["catalyst"].fillna(0).astype(int)
    return df


def _summ(g: pd.DataFrame) -> pd.Series:
    n = len(g)
    s = g["net_pct"]
    return pd.Series({
        "n": n, "win%": (g["net_stressed_inr"] > 0).mean() * 100,
        "gross%/tr": g["gross_pct"].mean(), "net%/tr": s.mean(),
        "net₹": g["net_stressed_inr"].sum(),
        "sharpe": float(s.mean() / s.std() * np.sqrt(252)) if n > 1 and s.std() > 0 else np.nan,
    })


def run(path: Path, status_only: bool) -> int:
    df = load(path)
    if df.empty:
        print(f"bt18: forward log empty or missing ({path}). The scanner writes one row per closed trade.")
        return 0
    cat, noc = df[df.catalyst == 1], df[df.catalyst == 0]
    print(f"bt18: {len(df)} closed paper trades | catalyst={len(cat)} no-catalyst={len(noc)} "
          f"| float filter applied on {int(df['float_filter_applied'].fillna(0).sum())} rows")
    pd.set_option("display.width", 160)
    pd.set_option("display.float_format", lambda x: f"{x:,.2f}")
    print("\nby setup (all trades, stressed):");   print(df.groupby("setup").apply(_summ))
    print("\nby exit reason:");                     print(df.groupby("exit_reason").apply(_summ))
    if status_only or len(cat) < MIN_N:
        print(f"\n  accumulating — {max(0, MIN_N - len(cat))} more catalyst-gated trades needed before the screen runs.")
        return 0

    gated_net = cat["net_pct"].mean()
    spread = cat["gross_pct"].mean() - (noc["gross_pct"].mean() if len(noc) else 0.0)

    rng = np.random.default_rng(SEED)
    g = df["gross_pct"].to_numpy()
    labels = (df["catalyst"] == 1).to_numpy()
    k = int(labels.sum())
    obs = g[labels].mean() - (g[~labels].mean() if (~labels).any() else 0.0)
    ge = 0
    for _ in range(SHUFFLES):
        idx = rng.permutation(len(g))
        sc = g[idx][:k].mean() - (g[idx][k:].mean() if len(g) > k else 0.0)
        ge += int(sc >= obs)
    anti_p = ge / SHUFFLES

    mkt = [market_oc(str(d)) for d in cat["date"].unique()]
    mkt_mean = float(np.nanmean([m for m in mkt if m is not None])) if any(m is not None for m in mkt) else np.nan
    beta_alpha = cat["gross_pct"].mean() - mkt_mean
    sharpe = float(cat["net_pct"].mean() / cat["net_pct"].std() * np.sqrt(252)) if cat["net_pct"].std() > 0 else 0.0

    def v(ok: bool) -> str:
        return "PASS" if ok else "FAIL -> KILL"

    print("\n" + "=" * 70)
    print(f"BT18 — momentum-catalyst v2 forward screen | n_cat={len(cat)} n_nocat={len(noc)}")
    print("=" * 70)
    print(f"  GATE 0  catalyst-gated NET/trade (stressed): {gated_net:+.4f}%   {v(gated_net > 0)}")
    print(f"  spread  cat − no-cat (gross)               : {spread:+.4f}%   {v(spread >= SPREAD_FLOOR)}")
    print(f"  anti    shuffle p(shuf ≥ obs)              : {anti_p:.3f}     {v(anti_p < ANTI_ALPHA)}")
    print(f"  beta    cat − mkt(^CRSLDX o→c)             : {beta_alpha:+.4f}%   {v(beta_alpha >= BETA_FLOOR)}")
    print(f"  Sharpe  catalyst-gated (stressed)          : {sharpe:+.3f}    {v(sharpe >= SHARPE_FLOOR)}")
    print(f"  n       catalyst-gated ≥ {MIN_N}                : {v(len(cat) >= MIN_N)}")
    allpass = (gated_net > 0 and spread >= SPREAD_FLOOR and anti_p < ANTI_ALPHA
               and beta_alpha >= BETA_FLOOR and sharpe >= SHARPE_FLOOR and len(cat) >= MIN_N)
    print("\n  >>> " + ("ALL DEV GATES PASS -> open the hold-out confirm window (NOT live money)"
                        if allpass else "KILL — a locked gate failed; no second look, no relabelling"))
    print(f"\n  secondary: win rate {(cat['net_stressed_inr'] > 0).mean() * 100:.1f}% vs 33% breakeven at 2:1")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(LOG))
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    return run(Path(a.log), a.status)


if __name__ == "__main__":
    sys.exit(main())
