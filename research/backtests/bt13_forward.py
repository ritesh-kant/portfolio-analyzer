# /// script
# requires-python = ">=3.11"
# dependencies = ["yfinance", "pandas"]
# ///
"""BT13 — forward paper-test logger + Gate-0 screen for earnings-surprise PEAD.
Hypothesis: research/hypotheses/2026-06-26-earnings-surprise-pead-smallcap.md

WHY forward-logging (not a historical backtest):
  Trendlyne StratQ shows consensus estimates DISPLAY-ONLY with only ~3 quarters of
  visible history — too shallow + not bulk-exportable for a credible historical
  backtest (verified 2026-06-26 in-browser). So we accumulate events FORWARD: each
  earnings season, log the pre-result consensus (read off Trendlyne) + the actual;
  this script fetches prices (Yahoo daily) and computes SUE + the T+1->T+5
  market-adjusted drift. Once we have MIN_EVENTS valid events, it runs the LOCKED
  cost-stress Gate 0 (KILL if long-short net <= 0 under stressed smaller-cap costs).
  This is the hypothesis's pre-registered "forward-only temporal split" path, and it
  is ToS-clean: you read live estimates you paid for, you do not scrape a blocked
  archive.

CAPTURE (estimates are display-only; two ways to read them):
  FAST (bulk, recommended) — the Forecaster screener lists every surprise in one page:
    beat:   trendlyne.com/equity/consensus-estimates/dashboard/forecaster/eps-quarter-surprise-above-0/
    missed: trendlyne.com/equity/consensus-estimates/dashboard/forecaster/eps-quarter-surprise-below-0/
    (filter to a watchlist/index via the page's "Choose Watchlist/Index" control.)
    For each smaller-cap name: log ticker + result date + the "Qtr EPS Surprise %" -> column surprise_pct.
  PRECISE (per stock) — stock page -> FORECASTER tab -> 'Surprises'/'Range Estimates':
    log eps_avg/eps_high/eps_low/n_analysts/eps_actual for a dispersion-standardized SUE.
  Either path works. surprise_pct is convenient but is Trendlyne's raw %, which distorts on
  near-zero-EPS names (flagged method=screener_pct); the per-stock raw numbers are cleaner.
  result_date = board-meeting/result announcement date (YYYY-MM-DD), needed for T+1 entry.
  Everything else (prices, SUE, drift, costs) is computed here.

Usage:
  uv run bt13_forward.py            # accumulation status + Gate-0 screen if ready
  uv run bt13_forward.py --status   # just the status
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_CSV = HERE / "bt13_forward_log.csv"
WATCHLIST = HERE / "bt13_watchlist.csv"
CACHE = HERE / ".cache"
CACHE.mkdir(exist_ok=True)

# --- LOCKED config (mirrors the hypothesis) ---
ENTRY_OFFSET_DAYS = 1          # enter T+1 (first session after the result)
HOLD_DAYS = 5                  # close on the 5th session
MIN_EVENTS = 30                # per the n>=30 discipline (per side)
BENCHMARK = "^CRSLDX"          # NIFTY 500; fallback ^NSEI
BENCHMARK_FALLBACK = "^NSEI"
# Gate-0 STRESSED smaller-cap delivery cost, round trip per leg:
#   STT delivery 0.2% (0.1% buy + 0.1% sell) + wide small-cap spread ~0.5% + misc.
STRESS_ROUNDTRIP_PCT = 0.0075  # 0.75% per leg, round trip (stressed)


def fetch_daily(symbol: str, start: str, end: str):
    import pandas as pd
    cache = CACHE / f"DAILY_{symbol.replace('^','IDX_').replace('&','_')}_{start}_{end}.csv"
    if cache.exists():
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
        return None if df.empty else df
    import yfinance as yf
    try:
        df = yf.download(symbol, start=start, end=end, interval="1d",
                         progress=False, auto_adjust=False, multi_level_index=False)
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] fetch failed {symbol}: {exc}", file=sys.stderr)
        df = None
    if df is None or df.empty:
        pd.DataFrame().to_csv(cache)
        return None
    df = df[["Open", "High", "Low", "Close"]].copy()
    df.to_csv(cache)
    return df


def _window(date_str: str) -> tuple[str, str]:
    import pandas as pd
    d = pd.to_datetime(date_str)
    return (d - pd.Timedelta(days=7)).strftime("%Y-%m-%d"), (d + pd.Timedelta(days=25)).strftime("%Y-%m-%d")


def _ret(bars, entry_date) -> tuple[float, str, str] | None:
    """T+1 open -> T+HOLD close return. Returns (ret, entry_dt, exit_dt) or None."""
    import pandas as pd
    after = bars[bars.index > pd.to_datetime(entry_date)]
    if len(after) < ENTRY_OFFSET_DAYS + HOLD_DAYS:
        return None
    entry_row = after.iloc[ENTRY_OFFSET_DAYS - 1]
    exit_row = after.iloc[ENTRY_OFFSET_DAYS - 1 + HOLD_DAYS]
    e, x = float(entry_row["Open"]), float(exit_row["Close"])
    if e <= 0:
        return None
    return x / e - 1.0, after.index[ENTRY_OFFSET_DAYS - 1].strftime("%Y-%m-%d"), \
        after.index[ENTRY_OFFSET_DAYS - 1 + HOLD_DAYS].strftime("%Y-%m-%d")


def _sue(avg, high, low, n_analysts, actual, entry_px):
    """Primary: dispersion-standardized SUE when >=3 analysts and a real range.
    Fallback: price-scaled surprise (robust to near-zero EPS). Returns (sue, method)."""
    if avg is None or actual is None:
        return None, "no_data"
    if high is not None and low is not None and high > low and (n_analysts or 0) >= 3:
        dispersion = (high - low) / 4.0  # range ~= 4 sigma proxy
        if dispersion > 0:
            return (actual - avg) / dispersion, "dispersion"
    if entry_px and entry_px > 0:
        return (actual - avg) / entry_px * 100.0, "price_scaled"  # surprise as % of price
    return None, "no_px"


def _spearman(xs, ys) -> float:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for rank, i in enumerate(order):
            r[i] = rank
        return r
    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((r - mx) ** 2 for r in rx) ** 0.5
    vy = sum((r - my) ** 2 for r in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else 0.0


def load_events():
    if not LOG_CSV.exists():
        print(f"no log at {LOG_CSV}", file=sys.stderr)
        return []
    rows = []
    with LOG_CSV.open() as f:
        for line in f:
            if line.lstrip().startswith("#") or not line.strip():
                continue
            rows.append(line)
    reader = csv.DictReader(rows)
    def num(v):
        v = (v or "").strip()
        try:
            return float(v)
        except ValueError:
            return None
    out = []
    for r in reader:
        out.append({
            "result_date": (r.get("result_date") or "").strip(),
            "ticker": (r.get("ticker") or "").strip().upper(),
            "period": (r.get("period") or "").strip(),
            "eps_avg": num(r.get("eps_avg")), "eps_high": num(r.get("eps_high")),
            "eps_low": num(r.get("eps_low")), "n_analysts": num(r.get("n_analysts")),
            "eps_actual": num(r.get("eps_actual")),
            "surprise_pct": num(r.get("surprise_pct")),
        })
    return out


def main():
    status_only = "--status" in sys.argv
    events = load_events()
    logged = [e for e in events if e["ticker"]]
    dated = [e for e in logged if e["result_date"] and (
        e["surprise_pct"] is not None
        or (e["eps_avg"] is not None and e["eps_actual"] is not None))]

    print(f"=== BT13 forward log: {LOG_CSV.name} ===")
    print(f"rows logged: {len(logged)}  |  with date+estimate+actual: {len(dated)}")
    if WATCHLIST.exists():
        wl = set()
        for ln in WATCHLIST.read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and not ln.lower().startswith("ticker,"):
                wl.add(ln.split(",")[0].strip().upper())
        done = {e["ticker"] for e in logged} & wl
        print(f"watchlist coverage: {len(done)}/{len(wl)} names logged "
              f"({len(wl) - len(done)} still to capture this season)")

    bench = fetch_daily(BENCHMARK, "2025-06-01", "2027-01-01") if dated else None
    if dated and bench is None:
        bench = fetch_daily(BENCHMARK_FALLBACK, "2025-06-01", "2027-01-01")
        print(f"  [note] benchmark {BENCHMARK} empty; using {BENCHMARK_FALLBACK}")

    priced = []
    for e in dated:
        s, en = _window(e["result_date"])
        bars = fetch_daily(f"{e['ticker']}.NS", s, en)
        if bars is None:
            continue
        r = _ret(bars, e["result_date"])
        if r is None:
            continue  # not enough bars yet (recent/future event) -> still "pending"
        stock_ret, edt, xdt = r
        bench_ret = None
        if bench is not None:
            br = _ret(bench, e["result_date"])
            bench_ret = br[0] if br else None
        mktadj = stock_ret - (bench_ret or 0.0)
        entry_px = float(bars[bars.index > __import__("pandas").to_datetime(e["result_date"])]
                         .iloc[ENTRY_OFFSET_DAYS - 1]["Open"])
        if e["eps_avg"] is not None and e["eps_actual"] is not None:
            sue, method = _sue(e["eps_avg"], e["eps_high"], e["eps_low"],
                               e["n_analysts"], e["eps_actual"], entry_px)
        elif e["surprise_pct"] is not None:
            sue, method = e["surprise_pct"], "screener_pct"  # Trendlyne raw % (lower quality)
        else:
            sue, method = None, "no_data"
        if sue is None:
            continue
        priced.append({**e, "sue": sue, "method": method, "stock_ret": stock_ret,
                       "mktadj": mktadj, "entry": edt, "exit": xdt})

    print(f"priced events (drift computable): {len(priced)}  |  need {MIN_EVENTS} for Gate 0")
    if priced:
        thin = sum(1 for p in priced if p["method"] != "dispersion")
        print(f"  SUE method: dispersion={len(priced)-thin}, price_scaled(thin coverage)={thin}")

    if status_only or len(priced) < MIN_EVENTS:
        remaining = max(0, MIN_EVENTS - len(priced))
        print(f"\nSTATUS: accumulating. {remaining} more priced events needed before the "
              f"Gate-0 screen runs.\nKeep logging results each earnings season into "
              f"{LOG_CSV.name}.")
        return

    # --- LOCKED Gate 0: cost-stress, evaluated FIRST ---
    priced.sort(key=lambda p: p["sue"])
    k = len(priced) // 3
    short_leg = priced[:k]          # lowest SUE  -> short
    long_leg = priced[-k:]          # highest SUE -> long
    mean = lambda xs: sum(xs) / len(xs)
    long_adj = mean([p["mktadj"] for p in long_leg])
    short_adj = mean([p["mktadj"] for p in short_leg])
    gross_spread = long_adj - short_adj
    cost = 2 * STRESS_ROUNDTRIP_PCT     # long leg + short leg, each round trip
    net_spread = gross_spread - cost
    rho = _spearman([p["sue"] for p in priced], [p["mktadj"] for p in priced])

    print("\n" + "=" * 64)
    print("GATE 0 — cost-stress screen (long top-SUE tercile vs short bottom)")
    print("=" * 64)
    print(f"  n per leg: {k}   (total priced {len(priced)})")
    print(f"  long  mkt-adj 5d drift: {long_adj*100:+.2f}%")
    print(f"  short mkt-adj 5d drift: {short_adj*100:+.2f}%")
    print(f"  gross long-short spread: {gross_spread*100:+.2f}%")
    print(f"  stressed cost (2 legs):  {cost*100:.2f}%")
    print(f"  NET spread after cost:   {net_spread*100:+.2f}%")
    print(f"  SUE~drift Spearman rho:  {rho:+.3f}  (supporting; want > 0)")
    verdict = "KILL (Gate 0 failed)" if net_spread <= 0 else "PROCEED past Gate 0 -> run full DSR/anti-strategy battery"
    print(f"\n  VERDICT: {verdict}")
    print("=" * 64)


if __name__ == "__main__":
    main()
