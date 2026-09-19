"""Replay recorded momentum trades with 1R cost-aware stop-management overlays.

This is a trade-management overlay, not a signal-generation backtest. It uses
only positions actually opened in ``mt_positions`` for the requested NSE date:

* optionally sell half the shares when a full post-entry one-minute bar reaches
  1R;
* raise the remaining shares' stop to their round-trip cost breakeven, rounded
  up to the NSE tick and increased by one additional tick;
* retain the active strategy's hard-stop, false-break, and trend exits.

For example:
    apps/signal-engine/.venv/bin/python \
        research/backtests/bt39_hybrid_1r_cost_stop.py --date 2026-09-18
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

from src.config import Settings  # noqa: E402
from src.momentum_trader import exits  # noqa: E402
from src.momentum_trader.engine import EngineConfig  # noqa: E402
from src.momentum_trader.scanner import (  # noqa: E402
    STRATEGY_ATTENTION_1M_MERGED,
    _apply_env_overrides,
    _market_data_token,
    _strategy_config as scanner_strategy_config,
)
from src.momentum_trader.setups import Setup, false_break  # noqa: E402
from src.momentum_trader.upstox import UpstoxClient  # noqa: E402
from src.news_trader.trailing_sl import calc_costs  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
TICK_SIZE = 0.05
EXTRA_COST_STOP_TICKS = 1


@dataclass(frozen=True)
class ReplayResult:
    symbol: str
    entry_time: pd.Timestamp
    entry: float
    qty: int
    partial_exit: float | None
    partial_qty: int
    final_exit: float
    final_qty: int
    exit_time: pd.Timestamp
    exit_reason: str
    gross_inr: float
    costs_inr: float
    net_inr: float


def _mongo_uri() -> str:
    uri = os.getenv("MONGODB_URI")
    if uri:
        return uri
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("MONGODB_URI="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("MONGODB_URI is unavailable")


def _cost_break_even_stop(entry: float, qty: int, tick_size: float = TICK_SIZE) -> float:
    """Return a sell price that covers the allocated round-trip cost plus one tick."""
    if qty < 1:
        raise ValueError("cost stop needs at least one remaining share")

    def net(price: float) -> float:
        return (price - entry) * qty - calc_costs(entry, price, qty, direction="long")["total"]

    low, high = entry, entry + 10.0
    while net(high) < 0.0:
        high += 10.0
    for _ in range(60):
        mid = (low + high) / 2.0
        if net(mid) < 0.0:
            low = mid
        else:
            high = mid
    return (math.ceil(high / tick_size) + EXTRA_COST_STOP_TICKS) * tick_size


def _split_costs(entry: float, partial_exit: float | None, partial_qty: int,
                 final_exit: float, final_qty: int) -> float:
    if partial_exit is None:
        return calc_costs(entry, final_exit, final_qty, direction="long")["total"]
    return (
        calc_costs(entry, partial_exit, partial_qty, direction="long")["total"]
        + calc_costs(entry, final_exit, final_qty, direction="long")["total"]
    )


def _strategy_config() -> EngineConfig:
    settings = Settings()
    settings.mt_strategy = STRATEGY_ATTENTION_1M_MERGED
    cfg = scanner_strategy_config(settings)
    _apply_env_overrides(cfg, settings)
    return cfg


def _trade_result(doc: dict[str, object], bars: pd.DataFrame, warmup: pd.DataFrame,
                  cfg: EngineConfig, partial_at_1r: bool, cost_stop_at_1r: bool) -> ReplayResult:
    entry = float(doc["entry_price"])
    hard_stop = float(doc["stop"])
    qty = int(doc["qty"])
    entry_time = pd.Timestamp(doc["entry_time"])
    entry_time = (
        entry_time.tz_localize(UTC).tz_convert(IST)
        if entry_time.tzinfo is None
        else entry_time.tz_convert(IST)
    )
    level = doc.get("level")
    setup = Setup(
        name=str(doc["setup"]),
        trigger=float(doc["trigger_px"]),
        stop=hard_stop,
        level=float(level) if level is not None else None,
    )
    state = exits.initial_state(entry, hard_stop)
    partial_qty, final_qty = qty // 2, qty
    partial_exit: float | None = None
    exit_price: float | None = None
    exit_time: pd.Timestamp | None = None
    exit_reason = ""
    one_r = entry + (entry - hard_stop)
    entry_floor = entry_time
    cost_stop_armed = cost_stop_applied = False

    for i, now in enumerate(bars.index):
        if now < entry_time.ceil("min"):
            continue
        bar = bars.iloc[i]
        if cost_stop_armed and not cost_stop_applied:
            state.trail = max(state.trail, _cost_break_even_stop(entry, final_qty))
            cost_stop_applied = True
        exits.update_high(state, float(bar["high"]), cfg.exit_cfg)

        stop_signal = exits.check_stop(state, bar)
        if stop_signal is not None:
            exit_price, exit_time, exit_reason = stop_signal.price, now, stop_signal.reason
            break

        if partial_exit is None and float(bar["high"]) >= one_r:
            if partial_at_1r and partial_qty:
                partial_exit = max(one_r, float(bar["open"]))
                final_qty = qty - partial_qty
            if cost_stop_at_1r:
                # Arm here, raise on the NEXT bar (above, before check_stop).
                # Raising it now would let this bar's high justify a stop its
                # own low may already have traded through.
                cost_stop_armed = True

        seen = bars.iloc[: i + 1]
        if (
            setup.level is not None
            and len(seen) >= 3
            and seen.index[-1] >= entry_floor
            and false_break(seen, setup.level)
        ):
            exit_price, exit_time, exit_reason = float(bar["close"]), now, "false_break"
            break

        if seen.index[-1] != state.last_tf_seen:
            state.last_tf_seen = seen.index[-1]
            trend_signal = exits.check_trend(state, seen, warmup, cfg.exit_cfg)
            exits.update_trail(state, seen, cfg.exit_cfg)
            if trend_signal is not None:
                exit_price, exit_time, exit_reason = trend_signal.price, now, trend_signal.reason
                break

        if now.time() >= cfg.eod_close:
            exit_price, exit_time, exit_reason = float(bar["close"]), now, "eod_close"
            break

    if exit_price is None or exit_time is None:
        raise RuntimeError(f"{doc['symbol']}: no exit in supplied session candles")

    gross = (
        ((partial_exit - entry) * partial_qty if partial_exit is not None else 0.0)
        + (exit_price - entry) * final_qty
    )
    costs = _split_costs(entry, partial_exit, partial_qty, exit_price, final_qty)
    return ReplayResult(
        symbol=str(doc["symbol"]),
        entry_time=entry_time,
        entry=entry,
        qty=qty,
        partial_exit=partial_exit,
        partial_qty=partial_qty if partial_exit is not None else 0,
        final_exit=exit_price,
        final_qty=final_qty,
        exit_time=exit_time,
        exit_reason=exit_reason,
        gross_inr=gross,
        costs_inr=costs,
        net_inr=gross - costs,
    )


def _load_bars(client: UpstoxClient, instruments: dict[str, object], symbol: str,
               session: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    instrument = instruments.get(symbol)
    if instrument is None:
        raise RuntimeError(f"{symbol}: absent from the current Upstox NSE instrument master")
    history = client.historical_1m(instrument.key, session - timedelta(days=10), session - timedelta(days=1))
    today = client.historical_1m(instrument.key, session, session)
    if today.empty:
        raise RuntimeError(f"{symbol}: no Upstox historical candles for {session.isoformat()}")
    return today, history


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="NSE session date, YYYY-MM-DD")
    args = parser.parse_args()
    session = date.fromisoformat(args.date)
    start = datetime.combine(session, datetime.min.time(), tzinfo=IST).astimezone(UTC)
    end = start + timedelta(days=1)

    db_name = os.getenv("MONGODB_DB_NAME", "portfolio_analyzer")
    db = MongoClient(_mongo_uri(), serverSelectionTimeoutMS=8_000)[db_name]
    docs = list(db.mt_positions.find({
        "status": "closed",
        "strategy": STRATEGY_ATTENTION_1M_MERGED,
        "entry_time": {"$gte": start, "$lt": end},
    }).sort("entry_time", 1))
    if not docs:
        raise SystemExit(f"no closed {STRATEGY_ATTENTION_1M_MERGED} trades for {session.isoformat()}")

    settings = Settings()
    client = UpstoxClient(_market_data_token(settings))
    instruments = client.nse_equities()
    cfg = _strategy_config()
    bars_by_symbol: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    baseline_results: list[ReplayResult] = []
    cost_stop_results: list[ReplayResult] = []
    hybrid_results: list[ReplayResult] = []
    for doc in docs:
        symbol = str(doc["symbol"])
        if symbol not in bars_by_symbol:
            bars_by_symbol[symbol] = _load_bars(client, instruments, symbol, session)
        today, warmup = bars_by_symbol[symbol]
        baseline_results.append(_trade_result(
            doc, today, warmup, cfg, partial_at_1r=False, cost_stop_at_1r=False,
        ))
        cost_stop_results.append(_trade_result(
            doc, today, warmup, cfg, partial_at_1r=False, cost_stop_at_1r=True,
        ))
        hybrid_results.append(_trade_result(
            doc, today, warmup, cfg, partial_at_1r=True, cost_stop_at_1r=True,
        ))

    rows = pd.DataFrame([{
        "symbol": r.symbol,
        "entry": r.entry_time.strftime("%H:%M"),
        "partial": "-" if r.partial_exit is None else f"{r.partial_qty}@{r.partial_exit:.2f}",
        "final": f"{r.final_qty}@{r.final_exit:.2f}",
        "exit": r.exit_time.strftime("%H:%M"),
        "reason": r.exit_reason,
        "gross_inr": r.gross_inr,
        "costs_inr": r.costs_inr,
        "net_inr": r.net_inr,
    } for r in hybrid_results])
    original_gross = sum(float(doc["gross_inr"]) for doc in docs)
    original_costs = sum(float(doc["costs_inr"]) for doc in docs)
    original_net = sum(float(doc["net_inr"]) for doc in docs)
    print(rows.to_string(index=False, formatters={
        "gross_inr": "{:+.2f}".format,
        "costs_inr": "{:.2f}".format,
        "net_inr": "{:+.2f}".format,
    }))
    print("\nsummary")
    baseline_net = sum(r.net_inr for r in baseline_results)
    baseline_gross = sum(r.gross_inr for r in baseline_results)
    baseline_costs = sum(r.costs_inr for r in baseline_results)
    cost_stop_net = sum(r.net_inr for r in cost_stop_results)
    cost_stop_gross = sum(r.gross_inr for r in cost_stop_results)
    cost_stop_costs = sum(r.costs_inr for r in cost_stop_results)
    hybrid_net = sum(r.net_inr for r in hybrid_results)
    hybrid_gross = sum(r.gross_inr for r in hybrid_results)
    hybrid_costs = sum(r.costs_inr for r in hybrid_results)
    print(f"trades={len(hybrid_results)} symbols={rows['symbol'].nunique()}")
    print(f"recorded: gross=INR {original_gross:+.2f} costs=INR {original_costs:.2f} "
          f"net=INR {original_net:+.2f}")
    print(f"revised baseline: gross=INR {baseline_gross:+.2f} costs=INR {baseline_costs:.2f} "
          f"net=INR {baseline_net:+.2f}")
    print(f"cost stop only:  gross=INR {cost_stop_gross:+.2f} costs=INR {cost_stop_costs:.2f} "
          f"net=INR {cost_stop_net:+.2f}")
    print(f"cost stop vs revised baseline: net=INR {cost_stop_net - baseline_net:+.2f}")
    changed = pd.DataFrame([{
        "symbol": cost_stop.symbol,
        "entry": cost_stop.entry_time.strftime("%H:%M"),
        "baseline_exit": f"{baseline.exit_time:%H:%M} {baseline.exit_reason}@{baseline.final_exit:.2f}",
        "cost_stop_exit": f"{cost_stop.exit_time:%H:%M} {cost_stop.exit_reason}@{cost_stop.final_exit:.2f}",
        "net_difference_inr": cost_stop.net_inr - baseline.net_inr,
    } for baseline, cost_stop in zip(baseline_results, cost_stop_results, strict=True)
        if abs(cost_stop.net_inr - baseline.net_inr) > 0.005])
    if not changed.empty:
        print("\ncost-stop changes")
        print(changed.to_string(index=False, formatters={
            "net_difference_inr": "{:+.2f}".format,
        }))
    print(f"hybrid:           gross=INR {hybrid_gross:+.2f} costs=INR {hybrid_costs:.2f} "
          f"net=INR {hybrid_net:+.2f}")
    print(f"hybrid vs revised baseline: net=INR {hybrid_net - baseline_net:+.2f}")


if __name__ == "__main__":
    main()
