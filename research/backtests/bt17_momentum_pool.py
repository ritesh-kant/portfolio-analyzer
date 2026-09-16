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
import multiprocessing as mp
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
    GUIDE_PULLBACK_ORDINALS,
    STRESS_SLIP,
    VOL_BASELINE_MIN_BARS,
    ClosedTrade,
    EngineConfig,
    build_cum_volume_profile,
    run_day,
)
from src.momentum_trader.exits import MODES  # noqa: E402
from src.momentum_trader.indicators import atr  # noqa: E402
from src.momentum_trader.upstox import Instrument, UpstoxClient  # noqa: E402

# Measured on live resting buy-stop fills: the first quote at or above the trigger
# landed +0.03% over it (n=11, paper). Small n — treat as an estimate, not a
# constant of nature; raise it if the live sample says otherwise.
LIVE_ENTRY_SLIP_PCT = 0.03

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
    inst: Instrument, df: pd.DataFrame, start: date, end: date, cfg: EngineConfig, catalyst,
    prefilter_chg_min: float | None = None,
) -> tuple[list[ClosedTrade], list[dict]]:
    trades: list[ClosedTrade] = []
    cands: list[dict] = []
    if df.empty:
        return trades, cands
    daily = _daily_from_1m(df)
    days = list(daily.index)
    # The day-level floor. Under attention entries the engine's own floor is
    # 1.5%, not 4%: `eligible_span` already used the right one, but this inner
    # pre-filter did not, so an --attention run silently discarded most of the
    # days it was meant to replay. Defaults to `cfg.day_chg_min`, so every
    # legacy-path run is unchanged.
    floor_pct = cfg.day_chg_min if prefilter_chg_min is None else prefilter_chg_min
    for i, d in enumerate(days):
        if d.date() < start or d.date() > end or i < MIN_PROFILE_DAYS:
            continue
        prev = daily.iloc[i - 1]
        prev_close = float(prev["close"])
        row = daily.iloc[i]
        # cheap pre-filter: the day must have reached the floor at some point
        if prev_close <= 0 or float(row["high"]) / prev_close - 1.0 < floor_pct / 100.0:
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
        # Prior sessions, for warming up the exit indicators (entries untouched)
        # and, under `warm_context`, the 5-minute trend context too. The 5-min
        # 200 EMA needs ~2.7 sessions, so that arm takes 5; every other arm keeps
        # the frozen 3, because a longer warm-up reseeds the 5-minute EMAs and
        # would shift exits in runs that are meant to reproduce exactly.
        warm_days = days[max(0, i - (5 if cfg.warm_context else 3)):i]
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


def _trade_row(t: ClosedTrade) -> dict:
    """One CSV row for a closed trade.

    Split out of `trades_frame` so a worker process can return plain dicts
    instead of pickling engine objects across the process boundary.
    """
    c = t.cand
    return {
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
    }


# --------------------------------------------------------------------------
# Parallel execution
#
# Symbols are independent: each one reads its own cache file and produces its
# own trades, so the whole loop fans out with no shared state. Workers are made
# self-sufficient rather than relying on fork inheritance, because macOS spawns
# a fresh interpreter per worker.
# --------------------------------------------------------------------------

_W: dict = {}


def _init_worker(token: str, insts: dict, cfg: EngineConfig, catalyst, start: date,
                 end: date, fetch_start: date, prefilter_chg_min: float,
                 fetch_only: bool, log_level: int) -> None:
    """One Upstox client per process; everything else is plain data."""
    # A spawned worker does not inherit the parent's logging config, so without
    # this its HTTP traffic is silently invisible - which is exactly the thing
    # you look at the log to check.
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(message)s")
    _W.update(client=UpstoxClient(token, cache_dir=CACHE), insts=insts, cfg=cfg,
              catalyst=catalyst, start=start, end=end, fetch_start=fetch_start,
              prefilter_chg_min=prefilter_chg_min, fetch_only=fetch_only)


def _symbol_job(sym: str) -> dict:
    """Pre-filter, fetch and simulate one symbol. Never raises into the pool."""
    out = {"symbol": sym, "status": "ok", "bars": 0, "rows": [], "cands": []}
    inst = _W["insts"].get(sym)
    if inst is None:
        return {**out, "status": "no_instrument"}
    # Daily pre-filter (1 request): does this name have ANY day in the window
    # that reached the day-change floor inside the price/turnover bands?
    try:
        d = _W["client"].daily(inst.key, _W["fetch_start"], _W["end"])
    except Exception as exc:  # noqa: BLE001
        return {**out, "status": f"daily_failed: {exc}"}
    elig = eligible_span(d, _W["start"], _W["end"], _W["prefilter_chg_min"])
    if elig is None:
        return {**out, "status": "skipped_daily"}
    try:
        df = _W["client"].cached_1m(inst, elig[0] - timedelta(days=45), elig[1])
    except Exception as exc:  # noqa: BLE001
        return {**out, "status": f"fetch_failed: {exc}"}
    out["bars"] = len(df)
    if _W["fetch_only"]:
        return {**out, "status": "cached"}
    tr, cd = simulate_symbol(inst, df, _W["start"], _W["end"], _W["cfg"], _W["catalyst"],
                             _W["prefilter_chg_min"])
    out["rows"] = [_trade_row(t) for t in tr]
    out["cands"] = cd
    return out


def trades_frame(trades: list[ClosedTrade]) -> pd.DataFrame:
    return pd.DataFrame([_trade_row(t) for t in trades])


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
    if fill_mode == "next_open":
        print("   !! fill=next_open is the LEGACY model and is NOT how production fills:")
        print("      the live scanner rests a buy-stop at the trigger (FILL_FUTURE_TRIGGER).")
        print("      next_open pays the trigger minute's own run — ~0.22%/trade, about one")
        print("      whole round trip (BT36). Use --live-fill for the production model.")
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
    ap.add_argument("--resistance-v2", action="store_true",
                    help="BT33 variant of the local-resistance rule: no 5m level "
                         "merge, round marks excluded from the headroom test, and a "
                         "refusal ends the day instead of freeing it.")
    ap.add_argument("--require-1m-agreement", action="store_true",
                    help="refuse a 5-min entry whose own 1-min chart disagrees (1-min "
                         "EMA9>EMA20, green trigger minute closing in its top 40% on "
                         ">=2.5x its recent 1-min volume). Off by default, matching the "
                         "MT_REQUIRE_1M_AGREEMENT env switch: BT30 measured it as a "
                         "~43% trade-count cut, not a filter (anti p=0.526) "
                         "(hypothesis 2026-09-12-one-minute-agreement)")
    ap.add_argument("--first-candidate-only", action="store_true",
                    help="do not replace a refused first setup with a later setup that day")
    ap.add_argument("--multi-entry", action="store_true",
                    help="take EVERY setup a symbol gives all day instead of only the first "
                         "(hypothesis 2026-09-06-multi-entry-same-stock)")
    ap.add_argument("--fill-mode", default="next_open", choices=list(FILL_MODES),
                    help="next_open = fill at the bar after the trigger (BT17 legacy; this is "
                         "NOT what production does and costs ~0.22%%/trade in decision "
                         "latency — see BT36); trigger = fill at the level but keep the "
                         "next-open entry gate (upper bound, uses future information); "
                         "resting_sized = buy-stop at the level gated on the price actually "
                         "paid — this is what the live scanner does; future_trigger = the "
                         "conservative replay of the live quote path")
    ap.add_argument("--entry-slip-pct", type=float, default=0.0,
                    help="slippage added to RESTING fills, %% of the trigger. A live buy-stop "
                         "is filled by the first quote at or above the level, not at the "
                         "level. 0 reproduces older runs; live measures ~0.03")
    ap.add_argument("--live-fill", action="store_true",
                    help="replay entries the way production fills them: --fill-mode "
                         "resting_sized with the live-measured entry slippage")
    ap.add_argument("--day-chg-min", type=float, default=None,
                    help="override the day-change floor (default 4.0). The 2.0 arm of "
                         "research/hypotheses/2026-09-06-volatility-scaled-entry.md")
    ap.add_argument("--day-chg-max", type=float, default=None,
                    help="override the day-change ceiling (default 8.0)")
    ap.add_argument("--attention", action="store_true",
                    help="replay the DEPLOYED arm (attention_1m_merged) instead of the "
                         "legacy 4-8%%/RVOL>=3 setup scan: soft +1.5%%/1.5x promotion, "
                         "5-min trend context, high-volume 1-min confirmation, resting "
                         "buy-stop fills, resistance-breakout requirement, one false-break "
                         "reclaim, resistance-state exits. Sets --fill-mode/--exit-mode "
                         "unless you pass them explicitly")
    ap.add_argument("--warrior-strict", action="store_true",
                    help="replay the warrior_strict arm: --attention plus the guide's "
                         "own entry checklist — micro pullback on light volume, 1-min "
                         "MACD positive and open, first/second pullback only, entries "
                         "confined to the morning peak window, and a 2:1 target kept "
                         "alongside the trend exits. Implies --attention. The daily "
                         "guardrails (3 strikes / 50%% give-back / size ladder) are "
                         "scanner-only and are NOT replayed here: a pool backtest walks "
                         "one symbol at a time, so it has no coherent day-level P&L to "
                         "apply them to")
    ap.add_argument("--tag", default="", help="suffix for the output CSV names")
    ap.add_argument("--fetch-only", action="store_true", help="just fill the parquet cache")
    ap.add_argument("--jobs", type=int, default=min(8, mp.cpu_count()),
                    help="worker processes; symbols are independent so this is a "
                         "straight speed-up. 1 runs in-process for debugging.")
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
    cfg_kw: dict[str, object] = {}
    if a.day_chg_min is not None:
        cfg_kw["day_chg_min"] = a.day_chg_min
    if a.day_chg_max is not None:
        cfg_kw["day_chg_max"] = a.day_chg_max
    if a.attention or a.warrior_strict:
        # Mirror scanner._strategy_config(STRATEGY_ATTENTION_1M_MERGED). Only
        # defaulted, so an explicit --exit-mode/--fill-mode still wins and the
        # components can be isolated.
        if "--exit-mode" not in sys.argv:
            a.exit_mode = "trend_resistance_state"
        if "--fill-mode" not in sys.argv:
            a.fill_mode = "future_trigger"
        cfg_kw.update(use_attention_entries=True, require_resistance_breakout=True,
                      allow_false_break_reentry=True)
    if a.warrior_strict:
        # Mirror scanner._strategy_config(STRATEGY_WARRIOR_STRICT)'s entry side.
        cfg_kw.update(require_micro_pullback=True, require_light_pullback_volume=True,
                      require_macd_positive_open=True,
                      allowed_pullback_ordinals=GUIDE_PULLBACK_ORDINALS,
                      peak_hours_only=True, warm_context=True,
                      vol_baseline_min_bars=VOL_BASELINE_MIN_BARS,
                      use_fixed_target=True)
    if a.live_fill:
        if "--fill-mode" not in sys.argv:
            a.fill_mode = "resting_sized"
        if "--entry-slip-pct" not in sys.argv:
            a.entry_slip_pct = LIVE_ENTRY_SLIP_PCT
    cfg = EngineConfig(stress_slip=STRESS_SLIP, exit_mode=a.exit_mode,
                       fill_mode=a.fill_mode, entry_slip_pct=a.entry_slip_pct, one_trade_per_day=not a.multi_entry,
                       require_quality=a.quality, require_max_move=a.max_move,
                       first_candidate_only=a.first_candidate_only,
                       require_1m_agreement=a.require_1m_agreement,
                       resistance_veto_v2=a.resistance_v2, **cfg_kw)

    # The pre-filter exists to skip symbols the engine could never trade. Under
    # attention entries the floor is 1.5%, not 4%, so using day_chg_min here
    # would silently discard most eligible days.
    prefilter_chg_min = (cfg.attention_day_chg_min if cfg.use_attention_entries
                         else cfg.day_chg_min)

    all_rows: list[dict] = []
    all_cands: list[dict] = []
    done = skipped_daily = 0
    t0 = time.time()
    jobs = max(1, a.jobs)
    initargs = (_token(), insts, cfg, catalyst, start, end, fetch_start,
                prefilter_chg_min, a.fetch_only,
                logging.DEBUG if a.v else logging.INFO)
    log.info("simulating %d symbols on %d worker%s",
             len(symbols), jobs, "" if jobs == 1 else "s")

    def absorb(res: dict) -> None:
        nonlocal done, skipped_daily
        status = res["status"]
        if status == "skipped_daily":
            skipped_daily += 1
            log.debug("%s: no eligible day on daily pre-filter", res["symbol"])
            return
        if status == "no_instrument":
            log.debug("no instrument for %s", res["symbol"])
            return
        if status.startswith(("daily_failed", "fetch_failed")):
            log.warning("%s: %s", res["symbol"], status)
            return
        done += 1
        if a.fetch_only:
            log.info("[%d/%d] %s cached %d bars", done, len(symbols),
                     res["symbol"], res["bars"])
            return
        all_rows.extend(res["rows"])
        all_cands.extend(res["cands"])
        log.info("[%d/%d] %s bars=%d trades=%d (%.0fs)", done, len(symbols),
                 res["symbol"], res["bars"], len(res["rows"]), time.time() - t0)

    if jobs == 1:
        _init_worker(*initargs)
        for sym in symbols:
            absorb(_symbol_job(sym))
    else:
        with mp.Pool(jobs, initializer=_init_worker, initargs=initargs) as pool:
            for res in pool.imap_unordered(_symbol_job, symbols, chunksize=1):
                absorb(res)
    log.info("daily pre-filter skipped %d symbols with no eligible day", skipped_daily)
    if a.fetch_only:
        return 0
    tag = a.tag or (a.exit_mode if a.fill_mode == "next_open"
                    else f"{a.exit_mode}_{a.fill_mode}")
    out_tr = OUT_TRADES.with_name(f"bt17_trades_{tag}.csv")
    out_cd = OUT_CANDS.with_name(f"bt17_candidates_{tag}.csv")
    # Workers finish out of order, so sort to a fixed key: the CSV must not
    # depend on --jobs or on which symbol happened to finish first.
    tr = pd.DataFrame(all_rows)
    if not tr.empty:
        tr = tr.sort_values(["symbol", "date", "trigger_time"]).reset_index(drop=True)
    all_cands.sort(key=lambda c: (c["symbol"], c["date"], c["time"]))
    tr.to_csv(out_tr, index=False)
    pd.DataFrame(all_cands).to_csv(out_cd, index=False)
    print(f"\nwrote {out_tr.name} ({len(tr)} trades), {out_cd.name} ({len(all_cands)} candidates)")
    report(tr, done, start, end, bool(a.events), a.exit_mode, a.fill_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
