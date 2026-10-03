"""BT56: paired US watchlist replay from read-only IBKR one-minute bars.

Registered in research/hypotheses/2026-10-03-ibkr-us-vwap-resistance.md.
The four arms share the current US engine, one cost model and each recorded
first-passed watchlist time. No order API is used.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from collections import Counter

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bt49_us_watchlist_audit import PROD_ENV  # noqa: E402
for key, value in PROD_ENV.items():
    os.environ.setdefault(key, value)

import bt17_momentum_pool as BT  # noqa: E402
import bt54_fill_resistance_vwap as RULES  # noqa: E402
from src.config import Settings  # noqa: E402
from src.momentum_trader import engine as E  # noqa: E402
from src.momentum_trader import us_session as US  # noqa: E402
from src.momentum_trader.catalyst import no_catalyst  # noqa: E402
from src.momentum_trader.quality import chart_quality  # noqa: E402
from src.momentum_trader.us_universe import USUniverseConfig  # noqa: E402

ARMS = RULES.ARMS
CACHE = ROOT / "research" / "backtests" / ".cache_ibkr_us"
OUT = ROOT / "research" / "backtests"
TAG = "us_20260924_1002"
ET = "America/New_York"
REQUIRED = ("date", "symbol", "entry_time", "exit_time", "entry", "stop", "exit",
            "exit_reason", "qty", "gross_pct", "net_pct", "gross_inr", "net_inr")


def _history(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path).sort_index()
    if df.index.tz is None:
        raise ValueError(f"untimed cache: {path}")
    df.index = df.index.tz_convert(ET)
    df = df.between_time("09:30", "15:59")
    return df[~df.index.duplicated(keep="last")]


def _run_arm(symbol: str, day: pd.DataFrame, first: pd.Timestamp,
             previous: pd.DataFrame, cfg, arm: str, profile,
             prev_close: float, prev_gainer: bool, prev_day: dict,
             quality, sma20, levels) -> tuple[list[dict], int, dict]:
    RULES._BUFFER_ON = arm in ("vwap_only", "both")
    RULES._FILL_ON = arm in ("resistance_only", "both")
    RULES._WOULD_BUFFER = RULES._WOULD_FILL = False
    old_step, old_context = E.step, E._attention_context
    old_confirm, old_fill = E._attention_confirmation, E._fill_resistance_evidence

    def after_watchlist(state, bars, config, catalyst, full_5m=None, **kwargs):
        if bars.index[-1] < first:
            return
        return RULES._step(state, bars, config, catalyst, full_5m, **kwargs)

    E.step, E._attention_context = after_watchlist, RULES._context
    E._attention_confirmation, E._fill_resistance_evidence = RULES._confirm, RULES._fill
    try:
        state = E.run_day(symbol, day, prev_close, profile, cfg, no_catalyst,
                          prev_gainer, warmup_1m=previous if not previous.empty else None,
                          prev_day=prev_day, chart_quality=quality,
                          daily_sma20=sma20, session_levels=levels)
        rows = [BT._trade_row(t) for t in state.closed]
        reasons = dict(Counter(r.reason for r in state.rejections))
        return rows, reasons.get("fill_resistance_headroom", 0), reasons
    finally:
        E.step, E._attention_context = old_step, old_context
        E._attention_confirmation, E._fill_resistance_evidence = old_confirm, old_fill


def _symbol_job(symbol: str, entries: list[dict]) -> dict:
    matches = sorted((CACHE / "1m").glob(f"{symbol}_*_2026.parquet"))
    if len(matches) != 1:
        return {"symbol": symbol, "status": "missing_or_ambiguous_cache", "days": 0,
                "rows": {a: [] for a in ARMS}}
    df = _history(matches[0])
    settings = Settings(_env_file=ROOT / ".env")
    ucfg = USUniverseConfig()
    result = {"symbol": symbol, "status": "ok", "days": 0,
              "bars": len(df), "rows": {a: [] for a in ARMS},
              "rejections": {a: 0 for a in ARMS}, "rejection_reasons": {},
              "missing": []}
    dates = sorted(set(df.index.date))
    for entry in sorted(entries, key=lambda x: x["date"]):
        d = date.fromisoformat(entry["date"])
        if d not in dates or dates.index(d) == 0:
            result["missing"].append(entry["date"])
            continue
        i = dates.index(d)
        day = df[df.index.date == d]
        if len(day) < 30:
            result["missing"].append(entry["date"])
            continue
        prior_day = df[df.index.date == dates[i - 1]]
        prev_close = float(prior_day.close.iloc[-1])
        history_dates = dates[max(0, i - 20):i]
        history = df[pd.Index(df.index.date).isin(history_dates)]
        warm_dates = dates[max(0, i - 5):i]
        warm = df[pd.Index(df.index.date).isin(warm_dates)]
        if len(history_dates) < 15:
            result.setdefault("short_profiles", []).append(entry["date"])
        profile = E.build_cum_volume_profile(history)
        quality = chart_quality(history) if not history.empty else None
        sma20 = float(history.groupby(history.index.date).close.last().tail(20).mean()) if len(history_dates) >= 20 else None
        prev_day = {"high": float(prior_day.high.max()), "low": float(prior_day.low.min()),
                    "close": prev_close}
        prev_gainer = False
        if i >= 2:
            second_prior = df[df.index.date == dates[i - 2]]
            prev_gainer = prev_close / float(second_prior.close.iloc[-1]) - 1 >= 0.04
        first = pd.Timestamp(entry["first_passed_at"])
        if first.tzinfo is None:
            first = first.tz_localize(ET)
        else:
            first = first.tz_convert(ET)
        cfg = US.build_engine_config(settings, d, ucfg)
        levels = E.build_session_levels(history, cfg.session_level_sessions)
        for arm in ARMS:
            rows, rejected, reasons = _run_arm(symbol, day, first, warm, cfg, arm, profile,
                                       prev_close, prev_gainer, prev_day,
                                       quality, sma20, levels)
            result["rows"][arm].extend(rows)
            result["rejections"][arm] += rejected
            if arm == "before":
                result["rejection_reasons"].update(Counter(result["rejection_reasons"]) + Counter(reasons))
        result["days"] += 1
    return result


def _metrics(rows: list[dict]) -> dict:
    if not rows:
        return {"trades": 0, "total_net_usd": 0.0, "mean_net_usd": None,
                "median_net_usd": None, "win_pct": None, "drop_top_5_net_usd": 0.0}
    net = pd.Series([r["net_inr"] for r in rows], dtype=float)
    return {"trades": len(net), "total_net_usd": round(float(net.sum()), 2),
            "mean_net_usd": round(float(net.mean()), 2),
            "median_net_usd": round(float(net.median()), 2),
            "win_pct": round(float((net > 0).mean() * 100), 2),
            "drop_top_5_net_usd": round(float(net.sort_values().iloc[:-5].sum()), 2)}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--limit", type=int, default=0, help="smoke-test symbol limit")
    args = p.parse_args()
    snapshot = json.loads((CACHE / "us_watchlist_snapshot_2026-09-24_to_10-02.json").read_text())
    selected = set((CACHE / "us_watchlist_2026-09-24_to_10-02.txt").read_text().split())
    by_symbol: dict[str, list[dict]] = {}
    for item in snapshot:
        if item["symbol"] in selected:
            by_symbol.setdefault(item["symbol"], []).append(item)
    jobs = sorted(by_symbol.items())[:args.limit or None]
    outputs = {a: [] for a in ARMS}
    coverage = []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(_symbol_job, symbol, entries): symbol
                   for symbol, entries in jobs}
        for number, future in enumerate(as_completed(futures), 1):
            result = future.result()
            for arm in ARMS:
                outputs[arm].extend(result["rows"][arm])
            coverage.append({k: v for k, v in result.items() if k != "rows"})
            if number % 10 == 0 or number == len(futures):
                print(f"{number}/{len(futures)} symbols; "
                      f"{sum(c['days'] for c in coverage)} covered symbol-days", flush=True)
    if args.limit:
        print(json.dumps({a: _metrics(outputs[a]) for a in ARMS}, indent=2))
        return
    for arm in ARMS:
        dest = OUT / f"bt56_trades_{TAG}_{arm}.csv"
        if dest.exists():
            raise FileExistsError(dest)
        frame = pd.DataFrame(outputs[arm])
        for col in REQUIRED:
            if col not in frame:
                frame[col] = pd.Series(dtype=float if col.endswith(("pct", "inr")) else object)
        frame["currency"] = "USD"
        frame = frame.sort_values(["date", "symbol", "entry_time"])
        frame.to_csv(dest, index=False)
    summary = {"dates": "2026-09-24..2026-10-02", "currency": "USD",
               "registered": "research/hypotheses/2026-10-03-ibkr-us-vwap-resistance.md",
               "symbols_requested": len(jobs),
               "symbol_days_covered": sum(c["days"] for c in coverage),
               "metrics": {a: _metrics(outputs[a]) for a in ARMS},
               "coverage": coverage}
    (OUT / "bt56_us_20260924_1002_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "coverage"}, indent=2))


if __name__ == "__main__":
    main()
