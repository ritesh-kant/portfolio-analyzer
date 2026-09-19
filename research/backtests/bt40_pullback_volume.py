"""Fixed exploratory comparison; never enables a live trading variant.

Run from the repository with apps/signal-engine/.venv/bin/python and --jobs 4.
Outputs JSON: production-engine symbol-session replays, not portfolio P&L.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))
sys.path.insert(0, str(ROOT / "research" / "backtests"))

from bt17_momentum_pool import _trade_row, simulate_symbol  # noqa: E402
from bt38_today_price_volume_overlay import _mongo_uri  # noqa: E402
from src.config import Settings  # noqa: E402
from src.momentum_trader import engine  # noqa: E402
from src.momentum_trader.scanner import _strategy_config  # noqa: E402
from src.momentum_trader.upstox import Instrument  # noqa: E402
from src.momentum_trader.volume_confirmation import volume_confirmation_evidence  # noqa: E402

CACHE = ROOT / "research/backtests/.cache_upstox/1m"
STRATEGIES = ("attention_1m_merged", "warrior_strict")
EXTRA_SLIP = 0.0005  # 5 basis points per side on top of production costs


def complete_session(bars: pd.DataFrame, day: pd.Timestamp) -> bool:
    expected = pd.date_range(day + pd.Timedelta(hours=9, minutes=15), periods=375, freq="min")
    # Parquet may use microseconds while date_range uses nanoseconds; compare
    # timestamps, not storage resolution (DatetimeIndex.equals checks both).
    return bool(len(bars) == len(expected) and (bars.index == expected).all()
                and not bars.isna().any().any() and (bars["volume"] >= 0).all())


def alternative_pass(evidence: dict) -> bool:
    if evidence.get("data_error") or not evidence.get("consecutive_1m"):
        return False
    if evidence["pattern"] == "micro_pullback":
        return bool(evidence["pattern_pass"])
    return bool(evidence.get("slope_pass", False))


def comparison_confirmation(original, variant: str, decisions: list[dict]):
    """Scoped research adapter at the existing confirmation point, including reclaims."""
    def confirm(bars, min_volume_ratio, guide=None, require_rising_price_volume=False):
        current, reason = original(bars, min_volume_ratio, guide, require_rising_price_volume)
        if not require_rising_price_volume:
            return current, reason
        otherwise, _ = original(bars, min_volume_ratio, guide, False)
        if otherwise is None:
            return current, reason
        evidence = volume_confirmation_evidence(bars)
        proposed = alternative_pass(evidence)
        decisions.append({
            "time": bars.index[-1].isoformat(), "current_pass": current is not None,
            "alternative_pass": proposed, "current_reason": reason,
            "trigger": otherwise.trigger, "stop": otherwise.stop,
            "close": float(bars["close"].iloc[-1]), "evidence": evidence,
        })
        if variant == "current":
            return current, reason
        if not proposed:
            return None, "research_pullback_volume_not_confirmed"
        return otherwise, "ok"
    return confirm


def add_forward_returns(decisions: list[dict], bars: pd.DataFrame) -> None:
    """Offline close-to-close opportunity measurements, never inputs to decisions."""
    for row in decisions:
        at = pd.Timestamp(row["time"])
        for minutes in (5, 15, 30):
            future = at + pd.Timedelta(minutes=minutes)
            value = None
            if future.date() == at.date() and future in bars.index:
                value = (float(bars.loc[future, "close"]) / row["close"] - 1) * 100
            row[f"forward_{minutes}m_pct"] = value


def replay_symbol(task: tuple[str, str, str]) -> dict:
    path, start_str, end_str = task
    start, end = date.fromisoformat(start_str), date.fromisoformat(end_str)
    symbol = Path(path).stem.rsplit("_", 1)[0]
    frame = pd.read_parquet(path).sort_index()
    frame.index = frame.index.tz_convert("Asia/Kolkata")
    frame = frame[frame.index.date <= end]
    full_days, excluded = [], []
    for day, bars in frame.groupby(frame.index.normalize()):
        if not start <= day.date() <= end:
            continue
        valid = complete_session(bars, day)
        (full_days if valid else excluded).append(day)
    result = {"symbol": symbol, "complete_sessions": len(full_days),
              "excluded_sessions": [str(d.date()) for d in excluded], "runs": []}
    if not full_days:
        return result
    inst = Instrument(symbol=symbol, isin="offline", key="offline", name=symbol, tick_size=0.05)
    for strategy in STRATEGIES:
        settings = Settings(_env_file=None, mt_strategy=strategy,
                            mt_risk_inr=500, mt_max_notional_inr=50_000,
                            mt_attention_day_chg_min=1.5, mt_attention_rvol_min=1.5)
        # Pinned ON: this study compared the slope gate against a pullback-phase
        # alternative, both with the gate active. The scanner default became OFF
        # on 2026-09-19, so pin it here to keep the recorded result reproducible.
        cfg = replace(_strategy_config(settings), entry_slip_pct=0.03,
                      require_rising_price_volume=True)
        for variant in ("current", "alternative"):
            decisions: list[dict] = []
            confirmation = comparison_confirmation(
                engine._attention_confirmation, variant, decisions)
            with patch.object(engine, "_attention_confirmation", confirmation):
                trades = []
                # Preserve historical sessions for correct previous-close/profile
                # inputs, but evaluate only complete days.
                for day in full_days:
                    daily_trades, _ = simulate_symbol(
                        inst, frame, day.date(), day.date(), cfg, lambda _s, _t: (0, ""),
                        prefilter_chg_min=cfg.attention_day_chg_min,
                    )
                    trades.extend(daily_trades)
            rows = [_trade_row(t) for t in trades]
            for row in rows:
                row["stress_net_inr"] = row["net_inr"] - EXTRA_SLIP * (
                    row["entry"] + row["exit"]) * row["qty"]
            add_forward_returns(decisions, frame)
            result["runs"].append({"strategy": strategy, "variant": variant,
                                   "trades": rows, "decisions": decisions})
    return result


def summarize(results: list[dict]) -> dict:
    summary = {}
    denominator = sum(r["complete_sessions"] for r in results)
    for strategy in STRATEGIES:
        totals = {}
        for variant in ("current", "alternative"):
            runs = [run for r in results for run in r["runs"]
                    if run["strategy"] == strategy and run["variant"] == variant]
            trades = [t for run in runs for t in run["trades"]]
            totals[variant] = {"trades": len(trades), **{
                key: round(sum(t[key] for t in trades), 2)
                for key in ("gross_inr", "costs_inr", "net_inr", "stress_net_inr")}}
        changed = [d for r in results for run in r["runs"]
                   if run["strategy"] == strategy and run["variant"] == "current"
                   for d in run["decisions"] if d["current_pass"] != d["alternative_pass"]]
        delta = totals["alternative"]["net_inr"] - totals["current"]["net_inr"]
        stress_delta = (totals["alternative"]["stress_net_inr"]
                        - totals["current"]["stress_net_inr"])
        dates = len({d["time"][:10] for d in changed})
        verdict = "inconclusive_sample"
        if delta < 0 or stress_delta < 0:
            verdict = "reject_exploratory_version"
        elif delta > 0 and stress_delta > 0 and len(changed) >= 30 and dates >= 5:
            verdict = "exploratory_support_only"
        summary[strategy] = {
            **totals, "net_delta_inr": round(delta, 2),
            "stress_net_delta_inr": round(stress_delta, 2),
            "net_delta_per_cached_symbol_session": round(delta / max(denominator, 1), 4),
            "changed_confirmation_decisions": len(changed), "changed_dates": dates,
            "verdict": verdict,
        }
    return {"cached_complete_symbol_sessions": denominator,
            "excluded_incomplete_sessions": sum(len(r["excluded_sessions"]) for r in results),
            "strategies": summary}


def ledger_diagnostic(day: str) -> dict:
    from pymongo import MongoClient

    start = pd.Timestamp(day, tz="Asia/Kolkata")
    with MongoClient(_mongo_uri(), serverSelectionTimeoutMS=8000) as client:
        docs = list(client["portfolio_analyzer"].mt_positions.find({
            "status": "closed", "entry_time": {
                "$gte": start.to_pydatetime(),
                "$lt": (start + pd.Timedelta(days=1)).to_pydatetime()},
        }))
    rows, missing = [], 0
    for doc in docs:
        bars = pd.DataFrame(doc.get("chart", {}).get("bars", []))
        if bars.empty:
            missing += 1
            continue
        bars["time"] = pd.to_datetime(bars["time"], utc=True).dt.tz_convert("Asia/Kolkata")
        bars = bars.set_index("time").sort_index()
        # Ledger candidate time is the START of its completed decision candle.
        decision = pd.Timestamp(doc["time"])
        decision = decision.tz_localize("UTC") if decision.tzinfo is None else decision
        known = bars.loc[:decision]
        evidence = volume_confirmation_evidence(known)
        rows.append({"symbol": doc["symbol"], "time": decision.isoformat(),
                     "current_pass": bool(evidence.get("slope_pass", False)),
                     "alternative_pass": alternative_pass(evidence),
                     "gross_inr": float(doc.get("gross_inr", 0)),
                     "net_inr": float(doc.get("net_inr", 0)), "evidence": evidence})
    totals = {}
    for name, field in (("recorded", None), ("current", "current_pass"),
                        ("alternative", "alternative_pass")):
        subset = [r for r in rows if field is None or r[field]]
        totals[name] = {
            "trades": len(subset), "net_inr": round(sum(r["net_inr"] for r in subset), 2),
            "gross_inr": round(sum(r["gross_inr"] for r in subset), 2)}
    return {"date": day, "missing": missing, "totals": totals, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-08-24")
    parser.add_argument("--end", default="2026-09-04")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--ledger-date", default="2026-09-18")
    parser.add_argument("--skip-ledger", action="store_true")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).with_name("bt40_pullback_volume_results.json"))
    args = parser.parse_args()
    paths = sorted(CACHE.glob("*_2026.parquet"))
    results = []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(replay_symbol, (str(p), args.start, args.end)): p for p in paths}
        for future in as_completed(futures):
            results.append(future.result())
            if len(results) % 10 == 0:
                print(f"completed {len(results)}/{len(paths)} symbols", flush=True)
    report = {"start": args.start, "end": args.end,
              "scope": "exploratory symbol-session replay; no shared scanner risk limits",
              "summary": summarize(results), "symbols": sorted(results, key=lambda r: r["symbol"])}
    if not args.skip_ledger:
        report["ledger_diagnostic"] = ledger_diagnostic(args.ledger_date)
    args.output.write_text(json.dumps(report, indent=2, default=str, allow_nan=False) + "\n")
    print(json.dumps(report["summary"], indent=2))
    if "ledger_diagnostic" in report:
        print("ledger diagnostic", json.dumps(report["ledger_diagnostic"]["totals"]))


if __name__ == "__main__":
    main()
