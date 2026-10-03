"""Offline, paired 2026 replay of fill resistance and 0.20% VWAP retention.

Uses current scanner settings, normal paper costs, and cached 1-minute bars.
The VWAP buffer is a research overlay: retention only, no entries below VWAP.
Account-wide discipline and historical catalyst/float filters are not replayed.

Run from repo root:
  apps/signal-engine/.venv/bin/python research/backtests/bt54_fill_resistance_vwap.py
"""
from __future__ import annotations

import argparse
import gzip
import json
import multiprocessing as mp
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bt17_momentum_pool as BT  # noqa: E402
from src.config import Settings  # noqa: E402
from src.momentum_trader import engine as E  # noqa: E402
from src.momentum_trader.scanner import _apply_env_overrides, _strategy_config  # noqa: E402

ARMS = ("before", "resistance_only", "vwap_only", "both")
BUFFER = 0.002  # 0.20%, not 20%
_STEP, _CONTEXT = E.step, E._attention_context
_CONFIRM, _FILL, _DAY = E._attention_confirmation, E._fill_resistance_evidence, E.run_day
_ACTIVE = None
_FULL5 = None
_BUFFER_ON = _FILL_ON = False
_WOULD_BUFFER = _WOULD_FILL = False


def _step(state, bars, cfg, catalyst, full_5m=None, **kwargs):
    global _ACTIVE, _FULL5
    _ACTIVE, _FULL5 = state, full_5m
    return _STEP(state, bars, cfg, catalyst, full_5m, **kwargs)


def _context(tf5, warmup_5m=None):
    global _WOULD_BUFFER
    ok, reason = _CONTEXT(tf5, warmup_5m)
    if _ACTIVE.attention and not ok and reason == "below_vwap" and len(tf5):
        vwap = float(E.session_vwap(tf5).iloc[-1])
        if float(tf5.close.iloc[-1]) >= vwap * (1.0 - BUFFER):
            _WOULD_BUFFER = True
            if _BUFFER_ON:
                return True, "vwap_buffer"
    return ok, reason


def _confirm(bars, min_volume_ratio, guide=None, require_rising_price_volume=False):
    if _BUFFER_ON and _ACTIVE.attention:
        tf5 = E._bars_5m(bars, _FULL5)
        if len(tf5) and float(tf5.close.iloc[-1]) <= float(E.session_vwap(tf5).iloc[-1]):
            return None, "waiting_vwap_reclaim"
    return _CONFIRM(bars, min_volume_ratio, guide, require_rising_price_volume)


def _fill(*args, **kwargs):
    global _WOULD_FILL
    evidence = _FILL(*args, **kwargs)
    if evidence is not None:
        _WOULD_FILL = True
    return evidence if _FILL_ON else None


def _symbol_job(job):
    global _BUFFER_ON, _FILL_ON, _WOULD_BUFFER, _WOULD_FILL
    path, inst, cfg, start, end = job
    df = pd.read_parquet(path)
    prior = path.with_name(path.name.replace("_2026.parquet", "_2025.parquet"))
    if prior.exists():
        warm = pd.read_parquet(prior)
        warm = warm[warm.index.date >= date(2025, 11, 17)]
        df = pd.concat([warm, df]).sort_index()
        df = df[~df.index.duplicated(keep="last")]
    out = {arm: [] for arm in ARMS}
    out.update(symbol=inst.symbol, days=0, replays=0, rejections={arm: 0 for arm in ARMS})
    refusals = []

    def replay(arm, args, kwargs):
        global _BUFFER_ON, _FILL_ON, _WOULD_BUFFER, _WOULD_FILL
        _BUFFER_ON = arm in ("vwap_only", "both")
        _FILL_ON = arm in ("resistance_only", "both")
        _WOULD_BUFFER = _WOULD_FILL = False
        state = _DAY(*args, **kwargs)
        out["replays"] += 1
        rows = [BT._trade_row(t) for t in state.closed]
        out[arm].extend(rows)
        rejects = [r for r in state.rejections if r.reason == "fill_resistance_headroom"]
        out["rejections"][arm] += len(rejects)
        for r in rejects:
            refusals.append({"arm": arm, "symbol": r.symbol, "time": r.time.isoformat(),
                             "price": r.observed_price, **r.evidence})
        return state, rows, _WOULD_BUFFER, _WOULD_FILL

    def paired_day(*args, **kwargs):
        out["days"] += 1
        baseline, rows, buffer_diverges, fill_diverges = replay("before", args, kwargs)
        for arm, needed in (("resistance_only", fill_diverges),
                            ("vwap_only", buffer_diverges),
                            ("both", buffer_diverges or fill_diverges)):
            if needed:
                replay(arm, args, kwargs)
            else:
                out[arm].extend(dict(row) for row in rows)
        return baseline

    E.step, E._attention_context = _step, _context
    E._attention_confirmation, E._fill_resistance_evidence = _confirm, _fill
    BT.run_day = paired_day
    try:
        BT.simulate_symbol(inst, df, start, end, cfg, BT.no_catalyst,
                           cfg.attention_day_chg_min)
    finally:
        E.step, E._attention_context = _STEP, _CONTEXT
        E._attention_confirmation, E._fill_resistance_evidence = _CONFIRM, _FILL
        BT.run_day = _DAY
    out["refusals"] = refusals
    current = df[df.index.year == 2026]
    out["first_bar"], out["last_bar"] = str(current.index.min()), str(current.index.max())
    return out


def metrics(rows):
    if not rows:
        return {"trades": 0, "net_inr": 0.0, "win_pct": 0.0, "avg_net_inr": 0.0}
    df = pd.DataFrame(rows)
    wins = df.loc[df.net_inr > 0, "net_inr"].sum()
    losses = -df.loc[df.net_inr < 0, "net_inr"].sum()
    return {"trades": len(df), "net_inr": float(df.net_inr.sum()),
            "gross_inr": float(df.gross_inr.sum()), "costs_inr": float(df.costs_inr.sum()),
            "win_pct": float((df.net_inr > 0).mean() * 100),
            "avg_net_inr": float(df.net_inr.mean()),
            "profit_factor": float(wins / losses) if losses else None}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jobs", type=int, default=min(8, mp.cpu_count()))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 25))
    ap.add_argument("--output", type=Path, default=ROOT / "research" / "backtests" / "bt54_results")
    args = ap.parse_args()
    settings = Settings(_env_file=ROOT / ".env")
    cfg = _apply_env_overrides(_strategy_config(settings), settings)
    cache = ROOT / "research" / "backtests" / ".cache_upstox"
    master = json.loads(gzip.decompress((cache / "NSE.json.gz").read_bytes()))
    insts = {r["trading_symbol"]: BT.Instrument(r["trading_symbol"], r.get("isin", ""),
             r["instrument_key"], r.get("name", ""), float(r.get("tick_size", 5)) / 100)
             for r in master if r.get("segment") == "NSE_EQ" and r.get("instrument_type") == "EQ"}
    jobs = [(p, insts[p.stem[:-5].replace("_", "&")], cfg, date(2026, 1, 1), args.end)
            for p in sorted((cache / "1m").glob("*_2026.parquet"))
            if p.stem[:-5].replace("_", "&") in insts]
    if args.limit:
        jobs = jobs[:args.limit]
    rows = {arm: [] for arm in ARMS}
    coverage, refusals, replays, days = [], [], 0, 0
    counts = {arm: 0 for arm in ARMS}
    started = time.monotonic()
    print(f"Replaying {len(jobs)} cached NSE stocks, four arms, {args.jobs} processes", flush=True)
    with ProcessPoolExecutor(max_workers=args.jobs, mp_context=mp.get_context("spawn")) as pool:
        futures = {pool.submit(_symbol_job, job): job[1].symbol for job in jobs}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()  # abort on any error; never silently drop stocks
            for arm in ARMS:
                rows[arm].extend(result[arm])
                counts[arm] += result["rejections"][arm]
            days += result["days"]
            replays += result["replays"]
            refusals.extend(result["refusals"])
            coverage.append({key: result[key] for key in ("symbol", "days", "first_bar", "last_bar")})
            if i % 10 == 0 or i == len(jobs):
                print(f"{i}/{len(jobs)} stocks | {days} symbol-days | "
                      f"before={len(rows['before'])} both={len(rows['both'])} trades | "
                      f"{time.monotonic() - started:.0f}s", flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {"start": "2026-01-01", "end": str(args.end), "symbols": len(jobs),
               "symbol_days": days, "day_replays": replays,
               "seconds": time.monotonic() - started, "vwap_buffer_pct": BUFFER * 100,
               "settings": {k: str(v) if hasattr(v, "isoformat") else v
                            for k, v in asdict(cfg).items() if k not in ("market", "exit_cfg")},
               "metrics": {arm: metrics(rows[arm]) for arm in ARMS},
               "fill_refusals": counts,
               "limitations": ["Cached stocks only; per-stock date coverage varies.",
                   "One-minute future close approximates post-decision quotes; no tick tape.",
                   "Fixed configured risk, no account-wide daily guardrails or size ladder.",
                   "Historical float/circuit/catalyst eligibility not reconstructed.",
                   "VWAP retention buffer is a temporary research overlay, not a source edit."]}
    for arm in ARMS:
        frame = pd.DataFrame(rows[arm])
        if not frame.empty:
            frame = frame.sort_values(["date", "symbol", "entry_time"])
        frame.to_csv(args.output / f"{arm}.csv", index=False)
    pd.DataFrame(coverage).sort_values("symbol").to_csv(args.output / "coverage.csv", index=False)
    pd.DataFrame(refusals).to_csv(args.output / "fill_refusals.csv", index=False)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
