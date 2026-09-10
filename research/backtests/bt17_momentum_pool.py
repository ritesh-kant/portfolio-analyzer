# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "numpy", "pyarrow", "httpx", "python-dotenv", "pydantic-settings", "pydantic"]
# ///
"""BT17 — momentum pool backtest on Upstox 1-minute bars
(hypothesis: research/hypotheses/2026-09-05-momentum-catalyst-upstox-v2.md §5.1).

This is the CONTEXT test, not a gate: it measures what the seven Warrior setups
do on NSE movers WITHOUT the catalyst split (the catalyst is forward-only), so we
know the fade the catalyst must overcome. It replays the *exact* engine the
live scanner runs (`src/momentum_trader/engine.py`), bar by bar, with the
+40 bps/side cost stress from Gate 0.

Universe here = NIFTY 500 (+ optional extra symbol lists) filtered by the
criteria that ARE knowable historically: price ₹60–2,000, 20-day turnover
₹3–50 cr, day-change 4–8% at trigger, RVOL ≥ 3× time-of-day. The free-float /
promoter / band filters are forward-only (no PIT history held) and are NOT
applied here — the report says so.

Usage (from repo root; needs UPSTOX_ACCESS_TOKEN in .env for the history API):
  uv run research/backtests/bt17_momentum_pool.py --start 2024-01-01 --end 2024-12-31 --limit 50
  uv run research/backtests/bt17_momentum_pool.py --start 2022-01-01 --end 2025-12-31
  uv run research/backtests/bt17_momentum_pool.py ... --events research/data/results_dates.csv

First full pull is ~40k paced requests (hours); every rerun reads the parquet
cache in research/backtests/.cache_upstox/1m/.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "apps" / "signal-engine"))

from src.momentum_trader import universe  # noqa: E402
from src.momentum_trader.quality import chart_quality  # noqa: E402
from src.momentum_trader.catalyst import DatedEventLookup, no_catalyst  # noqa: E402
from src.momentum_trader.engine import (  # noqa: E402
    FILL_MODES,
    STRESS_SLIP,
    ClosedTrade,
    EngineConfig,
    build_cum_volume_profile,
    run_day,
)
from src.momentum_trader.exits import MODES  # noqa: E402
from src.momentum_trader.indicators import atr  # noqa: E402
from src.momentum_trader.upstox import Instrument, UpstoxClient  # noqa: E402

CACHE = Path(__file__).resolve().parent / ".cache_upstox"
OUT_TRADES = Path(__file__).resolve().parent / "bt17_trades.csv"
OUT_CANDS = Path(__file__).resolve().parent / "bt17_candidates.csv"
PROFILE_DAYS = 20
MIN_PROFILE_DAYS = 10

log = logging.getLogger("bt17")


def _token() -> str:
    import os

    from dotenv import load_dotenv
    load_dotenv(_REPO / ".env")
    load_dotenv(_REPO / "apps" / "signal-engine" / ".env", override=True)
    return os.getenv("UPSTOX_ACCESS_TOKEN", "")


def _daily_from_1m(df: pd.DataFrame) -> pd.DataFrame:
    day = df.index.normalize()
    g = df.groupby(day)
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
        "close": g["close"].last(), "volume": g["volume"].sum(),
        "turnover_cr": (df["close"] * df["volume"]).groupby(day).sum() / 1e7,
    })
    return out


def eligible_span(daily: pd.DataFrame, start: date, end: date, chg_min: float) -> tuple[date, date] | None:
    """First/last date in [start, end] where high/prev_close−1 ≥ chg_min and the
    price + 20-day-turnover bands pass. None if no such day."""
    if daily.empty or len(daily) < 25:
        return None
    d = daily.copy()
    d["prev_close"] = d["close"].shift(1)
    d["turnover_cr"] = (d["close"] * d["volume"]).rolling(20).mean().shift(1) / 1e7
    d = d[(d.index.date >= start) & (d.index.date <= end)]
    reached = d["high"] / d["prev_close"] - 1.0 >= chg_min / 100.0
    price_ok = d["prev_close"].between(universe.PRICE_MIN, universe.PRICE_MAX)
    turn_ok = d["turnover_cr"].between(universe.TURNOVER_MIN_CR, universe.TURNOVER_MAX_CR)
    ok = d[reached & price_ok & turn_ok]
    if ok.empty:
        return None
    return ok.index[0].date(), ok.index[-1].date()


def simulate_symbol(
    inst: Instrument, df: pd.DataFrame, start: date, end: date, cfg: EngineConfig, catalyst
) -> tuple[list[ClosedTrade], list[dict]]:
    trades: list[ClosedTrade] = []
    cands: list[dict] = []
    if df.empty:
        return trades, cands
    daily = _daily_from_1m(df)
    days = list(daily.index)
    for i, d in enumerate(days):
        if d.date() < start or d.date() > end or i < MIN_PROFILE_DAYS:
            continue
        prev = daily.iloc[i - 1]
        prev_close = float(prev["close"])
        row = daily.iloc[i]
        # cheap pre-filter: the day must have reached +4% at some point
        if prev_close <= 0 or float(row["high"]) / prev_close - 1.0 < cfg.day_chg_min / 100.0:
            continue
        turnover = float(daily["turnover_cr"].iloc[max(0, i - 20):i].mean())
        ok, _ = universe.passes_dynamic(prev_close, turnover)
        if not ok:
            continue
        hist_days = days[max(0, i - PROFILE_DAYS):i]
        hist = df[df.index.normalize().isin(hist_days)]
        profile = build_cum_volume_profile(hist, PROFILE_DAYS)
        # F2 reads only PRIOR sessions; F1's daily leg only prior daily closes
        cq = chart_quality(hist)
        sma20 = float(daily["close"].iloc[max(0, i - 20):i].mean()) if i >= 20 else None
        # 20-day daily ATR as % of prev close, from sessions strictly BEFORE today.
        # Recorded only (volatility-scaled-entry hypothesis §2); nothing gates on it.
        atr_pct = None
        if i >= 20:
            a = atr(daily.iloc[max(0, i - 40):i], period=14)
            if len(a) and not pd.isna(a.iloc[-1]) and prev_close > 0:
                atr_pct = float(a.iloc[-1]) / prev_close * 100.0
        day_bars = df[df.index.normalize() == d]
        if len(day_bars) < 30:
            continue
        prev_gainer = i >= 2 and float(prev["close"] / daily.iloc[i - 2]["close"] - 1.0) >= 0.04
        # prior sessions, for warming up the exit indicators only (entries untouched)
        warm_days = days[max(0, i - 3):i]
        warmup = df[df.index.normalize().isin(warm_days)]
        st = run_day(inst.symbol, day_bars, prev_close, profile, cfg, catalyst, prev_gainer,
                     warmup_1m=warmup if not warmup.empty else None,
                     prev_day={"high": float(prev["high"]), "low": float(prev["low"]),
                               "close": prev_close},
                     chart_quality=cq, daily_sma20=sma20, daily_atr_pct=atr_pct)
        trades.extend(st.closed)
        for c in st.candidates:
            cands.append({
                "date": str(d.date()), "symbol": c.symbol, "setup": c.setup.name,
                "time": c.time.strftime("%H:%M"), "trigger": c.setup.trigger, "stop": c.setup.stop,
                "day_chg_pct": c.day_chg_pct, "rvol": c.rvol, "catalyst": c.catalyst,
                "event_type": c.event_type, "tags": "|".join(c.candle_tags),
                "prev_day_gainer": int(c.prev_day_gainer), "pullback_ord": c.pullback_ord,
                "quality_reason": c.quality_reason, "atr_pct": c.atr_pct,
                "macd_hist": c.macd_hist, "dist_to_round_pct": c.dist_to_round_pct,
                "round_head_pct": c.round_head_pct,
                "resist_head_pct": c.resist_head_pct,
                "support_drop_pct": c.support_drop_pct,
            })
    return trades, cands


def trades_frame(trades: list[ClosedTrade]) -> pd.DataFrame:
    rows = []
    for t in trades:
        c = t.cand
        rows.append({
            "date": str(c.time.date()), "year": c.time.year, "symbol": c.symbol, "setup": c.setup.name,
            "trigger_time": c.time.strftime("%H:%M"), "entry_time": t.entry_time.strftime("%H:%M"),
            "trigger": c.setup.trigger, "entry": t.entry, "stop": c.setup.stop,
            "exit_time": t.exit_time.strftime("%H:%M"),
            "exit": t.exit, "exit_reason": t.exit_reason, "qty": t.qty,
            "day_chg_pct": c.day_chg_pct, "rvol": c.rvol, "catalyst": c.catalyst,
            "event_type": c.event_type, "tags": "|".join(c.candle_tags),
            "prev_day_gainer": int(c.prev_day_gainer), "pullback_ord": c.pullback_ord,
            "quality_reason": c.quality_reason, "atr_pct": c.atr_pct, "macd_hist": c.macd_hist,
            "dist_to_round_pct": c.dist_to_round_pct,
            "round_head_pct": c.round_head_pct, "resist_head_pct": c.resist_head_pct,
            "support_drop_pct": c.support_drop_pct,
            "gross_pct": t.gross_pct, "net_pct": t.net_pct,
            "gross_inr": t.gross_inr, "costs_inr": t.costs_inr, "net_inr": t.net_inr,
        })
    return pd.DataFrame(rows)


def _summ(g: pd.DataFrame) -> pd.Series:
    n = len(g)
    net = g["net_pct"]
    sharpe = float(net.mean() / net.std() * np.sqrt(252)) if n > 1 and net.std() > 0 else float("nan")
    return pd.Series({
        "n": n, "win%": (g["net_inr"] > 0).mean() * 100, "gross%/tr": g["gross_pct"].mean(),
        "net%/tr": net.mean(), "net₹/tr": g["net_inr"].mean(), "net₹": g["net_inr"].sum(),
        "sharpe": sharpe, "false_break%": (g["exit_reason"] == "false_break").mean() * 100,
        "stop%": (g["exit_reason"] == "stop").mean() * 100, "target%": (g["exit_reason"] == "target").mean() * 100,
    })


def report(tr: pd.DataFrame, n_symbols: int, start: date, end: date, events: bool,
           exit_mode: str = "fixed_2r", fill_mode: str = "next_open") -> None:
    pd.set_option("display.width", 160)
    pd.set_option("display.float_format", lambda x: f"{x:,.2f}")
    print("\n" + "=" * 78)
    print(f"BT17 — momentum POOL (no catalyst split) | {start}..{end} | {n_symbols} symbols"
          f" | exit={exit_mode} fill={fill_mode}")
    print("   context test for hypothesis v2 §5.1 — NOT a gate; float/band filters not applied")
    print("=" * 78)
    if tr.empty:
        print("no trades.")
        return
    print("\nALL trades:");           print(_summ(tr).to_frame("all").T)
    print("\nby setup:");             print(tr.groupby("setup").apply(_summ).sort_values("n", ascending=False))
    print("\nby year:");              print(tr.groupby("year").apply(_summ))
    print("\nby exit reason (share of trades, mean net%):")
    print(tr.groupby("exit_reason")["net_pct"].agg(["count", "mean"]))
    print("\nprev-day gainer vs fresh:"); print(tr.groupby("prev_day_gainer").apply(_summ))
    if events and tr["catalyst"].sum() > 0:
        print("\nEARNINGS-CATALYST subset (dated events file) vs rest:")
        print(tr.groupby("catalyst").apply(_summ))
        cat, noc = tr[tr.catalyst == 1], tr[tr.catalyst == 0]
        spread = cat["gross_pct"].mean() - noc["gross_pct"].mean()
        print(f"\n  spread (cat − no-cat, gross): {spread:+.3f}%/trade  n_cat={len(cat)}")
    if "trigger" in tr.columns:
        gap = (tr["entry"] / tr["trigger"] - 1.0) * 100.0
        print("\nfill gap vs trigger level (entry/trigger − 1, %) — the latency cost:")
        print(f"  mean {gap.mean():+.4f}  median {gap.median():+.4f}  p90 {gap.quantile(.9):+.4f}"
              f"  max {gap.max():+.4f}  at-or-below-trigger {(gap <= 0).mean() * 100:.1f}%")
        print(f"  mean qty {tr['qty'].mean():,.0f}")
    net = tr["net_pct"].mean()
    print(f"\n>>> POOL stressed net/trade = {net:+.3f}%  "
          f"({'positive — setups beat the fade before any catalyst filter' if net > 0 else 'negative — this is the fade the catalyst must overcome'})")
    print(f"    wins needed at 2:1 to break even = 33%; observed win rate = {(tr.net_inr > 0).mean() * 100:.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--limit", type=int, default=0, help="first N symbols only (smoke test)")
    ap.add_argument("--symbols", default="", help="comma-separated override")
    ap.add_argument("--extra", default="", help="comma-separated extra symbol-list files")
    ap.add_argument("--events", default="", help="CSV symbol,date[,event_type] for a dated catalyst subset")
    ap.add_argument("--exit-mode", default="fixed_2r", choices=list(MODES),
                    help="fixed_2r = target at 2x risk; trend_min/trend_full = indicator exits")
    ap.add_argument("--quality", action="store_true",
                    help="apply the F1/F2/F3 selectivity filters "
                         "(hypothesis 2026-09-06-quality-selectivity)")
    ap.add_argument("--max-move", action="store_true",
                    help="apply the full strict checklist registered in "
                         "2026-09-07-max-move-checklist")
    ap.add_argument("--first-candidate-only", action="store_true",
                    help="do not replace a refused first setup with a later setup that day")
    ap.add_argument("--multi-entry", action="store_true",
                    help="take EVERY setup a symbol gives all day instead of only the first "
                         "(hypothesis 2026-09-06-multi-entry-same-stock)")
    ap.add_argument("--fill-mode", default="next_open", choices=list(FILL_MODES),
                    help="next_open = fill at the bar after the trigger (BT17); "
                         "trigger = resting buy-stop at the trigger level, zero slippage")
    ap.add_argument("--day-chg-min", type=float, default=None,
                    help="override the day-change floor (default 4.0). The 2.0 arm of "
                         "research/hypotheses/2026-09-06-volatility-scaled-entry.md")
    ap.add_argument("--day-chg-max", type=float, default=None,
                    help="override the day-change ceiling (default 8.0)")
    ap.add_argument("--tag", default="", help="suffix for the output CSV names")
    ap.add_argument("--fetch-only", action="store_true", help="just fill the parquet cache")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.v else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    fetch_start = start - timedelta(days=45)   # profile + turnover warm-up
    client = UpstoxClient(_token(), cache_dir=CACHE)
    insts = client.nse_equities()
    if a.symbols:
        symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    else:
        symbols = universe.base_symbols([Path(p) for p in a.extra.split(",") if p.strip()])
    if a.limit:
        symbols = symbols[: a.limit]
    catalyst = no_catalyst
    if a.events:
        catalyst = DatedEventLookup(pd.read_csv(a.events))
    cfg_kw: dict[str, float] = {}
    if a.day_chg_min is not None:
        cfg_kw["day_chg_min"] = a.day_chg_min
    if a.day_chg_max is not None:
        cfg_kw["day_chg_max"] = a.day_chg_max
    cfg = EngineConfig(stress_slip=STRESS_SLIP, exit_mode=a.exit_mode,
                       fill_mode=a.fill_mode, one_trade_per_day=not a.multi_entry,
                       require_quality=a.quality, require_max_move=a.max_move,
                       first_candidate_only=a.first_candidate_only, **cfg_kw)

    all_trades: list[ClosedTrade] = []
    all_cands: list[dict] = []
    done = skipped_daily = 0
    t0 = time.time()
    for sym in symbols:
        inst = insts.get(sym)
        if inst is None:
            log.debug("no instrument for %s", sym)
            continue
        # Daily pre-filter (1 request): does this name have ANY day in the window that
        # (a) reached +4% vs prev close and (b) sat inside the price/turnover bands?
        # Large caps fail (b) on every day and cost 27 one-minute requests each otherwise.
        try:
            d = client.daily(inst.key, fetch_start, end)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: daily fetch failed (%s)", sym, exc)
            continue
        elig = eligible_span(d, start, end, cfg.day_chg_min)
        if elig is None:
            skipped_daily += 1
            log.debug("%s: no eligible day on daily pre-filter", sym)
            continue
        try:
            df = client.cached_1m(inst, elig[0] - timedelta(days=45), elig[1])
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: fetch failed (%s)", sym, exc)
            continue
        done += 1
        if a.fetch_only:
            log.info("[%d/%d] %s cached %d bars", done, len(symbols), sym, len(df))
            continue
        tr, cd = simulate_symbol(inst, df, start, end, cfg, catalyst)
        all_trades.extend(tr)
        all_cands.extend(cd)
        log.info("[%d/%d] %s bars=%d trades=%d (%.0fs)", done, len(symbols), sym, len(df), len(tr), time.time() - t0)
    log.info("daily pre-filter skipped %d symbols with no eligible day", skipped_daily)
    if a.fetch_only:
        return 0
    tag = a.tag or (a.exit_mode if a.fill_mode == "next_open"
                    else f"{a.exit_mode}_{a.fill_mode}")
    out_tr = OUT_TRADES.with_name(f"bt17_trades_{tag}.csv")
    out_cd = OUT_CANDS.with_name(f"bt17_candidates_{tag}.csv")
    tr = trades_frame(all_trades)
    tr.to_csv(out_tr, index=False)
    pd.DataFrame(all_cands).to_csv(out_cd, index=False)
    print(f"\nwrote {out_tr.name} ({len(tr)} trades), {out_cd.name} ({len(all_cands)} candidates)")
    report(tr, done, start, end, bool(a.events), a.exit_mode, a.fill_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
