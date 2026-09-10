# /// script
# requires-python = ">=3.11"
# dependencies = ["yfinance", "pandas", "numpy"]
# ///
"""BT16 — momentum + catalyst-gate forward logger + Gate-0 / spread / anti screen
(hypothesis: research/hypotheses/2026-07-03-momentum-catalyst-gate.md).

The user's idea, made falsifiable: (1) scan NIFTY 500 up-movers in the tradeable
band on high RVOL, (2) tag whether a FRESH HARD CATALYST exists (frozen definition
in the hypothesis §4), (3) the edge claim is that catalyst movers CONTINUE while
catalyst-less movers do NOT — a spread test, mirroring the event_type A-B test that
killed the news-trader. Forward-capture (like PEAD/bt13): intraday RVOL + early
entry are not available historically, so we accumulate live.

Capture: append one row per scanned mover to bt16_catalyst_log.csv with columns
  date,symbol,trigger_time,trigger_px,gap_pct,rvol,catalyst,close_px,event_type_note
  - trigger_px : the live scan/entry price (intraday-early, in the +2%..+10% band)
  - catalyst   : 1 = hard event in the 24h before trigger (results/order/approval/
                 corporate action); 0 = no such event (technical run / PR / rumour).
                 Ambiguous -> 0 (conservative, biases toward KILL). See §4.
  - close_px   : optional; if blank the script fetches the day's close from Yahoo.

Screen (LOCKED, runs only at n_catalyst >= MIN_N): long the catalyst movers
entry->close, MIS + 40bps/side stress, band-capped. KILL unless ALL hold:
  Gate 0 : catalyst-gated net/trade > 0
  spread : mean cont(catalyst) - mean cont(no-catalyst) >= +0.40%/trade, signed +
  anti   : label-shuffle p(shuffled spread >= observed) < 0.10
  beta   : catalyst-gated cont - same-day NIFTY 500 (^CRSLDX) open->close >= +0.30%
  n>=30, cohort net Sharpe >= 0.5

Usage:
  uv run bt16_momentum_catalyst.py            # status; runs the screen if ready
  uv run bt16_momentum_catalyst.py --status   # accumulation status only
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "apps" / "signal-engine" / "src"))

from news_trader.trailing_sl import calc_costs  # noqa: E402  (production intraday MIS)

LOG = Path(__file__).resolve().parent / "bt16_catalyst_log.csv"
CACHE = Path(__file__).resolve().parent / ".cache_daily_v"  # shared with bt15

# --- LOCKED parameters (from the hypothesis) ---
POSITION_INR = 50_000.0
STRESS_SLIP = 0.0040          # +40 bps/side
MIN_N = 30                    # catalyst-gated trades before the screen runs
SPREAD_FLOOR = 0.40           # %/trade, catalyst minus no-catalyst
BETA_FLOOR = 0.30             # %/trade, catalyst-gated minus universe
ANTI_ALPHA = 0.10             # shuffle p-value threshold
SHARPE_FLOOR = 0.5
SHUFFLES = 5000
SEED = 20260703              # fixed for reproducibility (no wall-clock randomness)
BAND_HI = 0.10               # tradeable-band ceiling (locked names excluded upstream)


def fetch_close(symbol: str, date: str) -> float | None:
    """Day close for symbol on date (cached daily bars; falls back to a fetch)."""
    cache = CACHE / f"{symbol.replace('&', '_')}.csv"
    df = None
    if cache.exists():
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
    if df is None or df.empty or pd.Timestamp(date) not in df.index:
        import yfinance as yf
        try:
            df = yf.download(f"{symbol}.NS", start=date, end=str(pd.Timestamp(date) + pd.Timedelta(days=4)),
                             interval="1d", progress=False, auto_adjust=False, multi_level_index=False)
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] {symbol} {date}: {exc}", file=sys.stderr)
            return None
    ts = pd.Timestamp(date)
    if df is None or df.empty or ts not in df.index:
        return None
    return float(df.loc[ts, "Close"])


def market_oc(date: str) -> float | None:
    """NIFTY 500 (^CRSLDX) open->close % for the beta control (index proxy for the
    equal-weight universe control named in the hypothesis; cap-weighted, noted)."""
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


def net_pct(entry: float, close: float) -> float:
    """Long entry->close, production MIS + 40bps/side stress, as % of notional."""
    qty = max(1, int(POSITION_INR // entry))
    gross = (close - entry) * qty
    costs = calc_costs(entry, close, qty, direction="long")["total"] + (entry + close) * qty * STRESS_SLIP
    return (gross - costs) / (entry * qty) * 100


def load_rows() -> pd.DataFrame:
    if not LOG.exists():
        return pd.DataFrame()
    df = pd.read_csv(LOG, comment="#")
    if df.empty:
        return df
    df["catalyst"] = df["catalyst"].astype(int)
    df["gap_pct"] = pd.to_numeric(df["gap_pct"], errors="coerce")
    df["trigger_px"] = pd.to_numeric(df["trigger_px"], errors="coerce")
    if "close_px" in df:
        df["close_px"] = pd.to_numeric(df["close_px"], errors="coerce")
    return df


def run() -> None:
    status_only = "--status" in sys.argv
    df = load_rows()
    if df.empty:
        print(f"bt16: capture log empty ({LOG.name}). Append scanned movers, then re-run.")
        print("  columns: date,symbol,trigger_time,trigger_px,gap_pct,rvol,catalyst,close_px,event_type_note")
        return

    # resolve close price (logged, else fetched), compute entry->close continuation
    cont, keep = [], []
    for _, r in df.iterrows():
        entry = r["trigger_px"]
        close = r.get("close_px")
        if pd.isna(close):
            close = fetch_close(str(r["symbol"]), str(r["date"]))
        if entry is None or close is None or not np.isfinite(entry) or entry <= 0:
            continue
        cont.append({
            "date": r["date"], "symbol": r["symbol"], "catalyst": int(r["catalyst"]),
            "gross_pct": (close / entry - 1.0) * 100, "net_pct": net_pct(entry, close),
        })
        keep.append(r["date"])
    c = pd.DataFrame(cont)
    if c.empty:
        print("bt16: no rows with usable entry/close yet.")
        return

    n_cat = int((c["catalyst"] == 1).sum())
    n_no = int((c["catalyst"] == 0).sum())
    print(f"bt16: {len(c)} scanned movers | catalyst={n_cat}  no-catalyst={n_no}  "
          f"(need catalyst >= {MIN_N} to run the screen)")
    if status_only or n_cat < MIN_N:
        if n_cat < MIN_N:
            print(f"  accumulating — {MIN_N - n_cat} more catalyst-gated movers needed.")
        return

    cat = c[c["catalyst"] == 1]
    noc = c[c["catalyst"] == 0]
    gated_net = cat["net_pct"].mean()
    spread = cat["gross_pct"].mean() - (noc["gross_pct"].mean() if len(noc) else 0.0)

    # label-shuffle anti: how often does a random split beat the observed spread?
    rng = np.random.default_rng(SEED)
    g = c["gross_pct"].to_numpy()
    labels = c["catalyst"].to_numpy().astype(bool)
    k = labels.sum()
    obs = g[labels].mean() - (g[~labels].mean() if (~labels).any() else 0.0)
    ge = 0
    for _ in range(SHUFFLES):
        idx = rng.permutation(len(g))
        sc = g[idx][:k].mean() - (g[idx][k:].mean() if len(g) > k else 0.0)
        if sc >= obs:
            ge += 1
    anti_p = ge / SHUFFLES

    # beta control: catalyst-gated continuation minus same-day market open->close
    mkt = [market_oc(str(d)) for d in cat["date"].unique()]
    mkt_mean = float(np.nanmean([m for m in mkt if m is not None])) if mkt else float("nan")
    beta_alpha = cat["gross_pct"].mean() - mkt_mean

    sharpe = (cat["net_pct"].mean() / cat["net_pct"].std() * np.sqrt(252)
              if cat["net_pct"].std() > 0 else 0.0)

    def verdict(ok: bool) -> str:
        return "PASS" if ok else "FAIL -> KILL"

    print("\n" + "=" * 66)
    print(f"BT16 — momentum + catalyst-gate | n_catalyst={n_cat} n_nocat={n_no}")
    print("=" * 66)
    print(f"  GATE 0  catalyst-gated NET/trade : {gated_net:+.4f}%   {verdict(gated_net > 0)}")
    print(f"  spread  cat - no-cat (gross)     : {spread:+.4f}%   "
          f"{verdict(spread >= SPREAD_FLOOR)} (floor +{SPREAD_FLOOR}, must be signed +)")
    print(f"  anti    shuffle p(shuf>=obs)     : {anti_p:.3f}     {verdict(anti_p < ANTI_ALPHA)}")
    print(f"  beta    cat - mkt(^CRSLDX o->c)  : {beta_alpha:+.4f}%   {verdict(beta_alpha >= BETA_FLOOR)}")
    print(f"  Sharpe  catalyst-gated (stress)  : {sharpe:+.3f}    {verdict(sharpe >= SHARPE_FLOOR)}")
    print(f"  n       catalyst-gated >= {MIN_N}      : {verdict(n_cat >= MIN_N)}")
    allpass = gated_net > 0 and spread >= SPREAD_FLOOR and anti_p < ANTI_ALPHA and beta_alpha >= BETA_FLOOR and sharpe >= SHARPE_FLOOR and n_cat >= MIN_N
    print("\n  >>> " + ("ALL DEV GATES PASS -> open hold-out confirm window (NOT live money)"
                        if allpass else "KILL — a locked gate failed; no second look, no relabelling"))


if __name__ == "__main__":
    run()
