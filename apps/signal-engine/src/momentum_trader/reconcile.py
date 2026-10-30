"""Close paper positions the scanner never got to close.

The scanner is the only thing that writes an exit, and it does so from its live
bar loop. If it dies or the machine sleeps (a laptop run, a crashed Fargate task)
the `mt_positions` row stays `status: "open"` with no exit, even though the
market has long since hit the stop or target. This strategy is intraday-only —
nothing legitimately carries overnight — so an `open` row from a past session is
always an orphan.

This replays the day's 1-minute bars through the SAME `engine.step()` the live
scanner uses, on a position rebuilt from the stored document, and writes the
result through `PaperLedger.closed()` tagged `reconciled: true`.

What is faithful and what is not
--------------------------------
* Faithful: stop, target, checkpoint stop, trail, breakeven, trend exits and the
  15:15 close all run through the engine's own code.
* Rebuilt, not stored: the engine's `ExitState` is never persisted. It is
  re-derived from the bars before the entry. The two structural levels the exits
  hold (support / resistance) are overwritten with the values the live scanner
  recorded at entry, so they match exactly. Prior-day anchors are not refetched,
  so the other derived levels can differ slightly from live.
* Candles: these are exchange 1-minute candles, not the scanner's own sampled
  bars, so a wick that only the exchange printed can change an exit.

Dry-run by default; `--apply` writes. Long NSE positions only — shorts
(`short_side.py`) and US (`us_ledger.py`) use different ledgers.

    uv run python -m src.momentum_trader.reconcile            # what would close
    uv run python -m src.momentum_trader.reconcile --apply
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from src.config import Settings

from . import exits
from .bars import IST
from .engine import (
    Candidate,
    ClosedTrade,
    DayState,
    EngineConfig,
    Position,
    _bars_5m,
    _is_one_minute_setup,
    force_close,
    resample_5m,
    step,
)
from .ledger import PaperLedger, strategy_label
from .levels import Level
from .risk import TradePlan
from .setups import Setup

logger = logging.getLogger("mt.reconcile")

WARMUP_DAYS = 12          # calendar days of prior bars to warm the exit indicators
MARKET_CLOSED_AFTER = (15, 35)   # a position opened TODAY is only an orphan after this


@dataclass
class Outcome:
    symbol: str
    entry_time: pd.Timestamp
    status: str                 # "closed" | "skipped"
    detail: str
    trade: ClosedTrade | None = None


def _ist(value: Any) -> pd.Timestamp:
    """Mongo hands back naive UTC unless the client is tz-aware; normalise both."""
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC").tz_convert(IST) if ts.tzinfo is None else ts.tz_convert(IST)


def _level(price: Any, kind: Any) -> Level | None:
    if price is None:
        return None
    return Level(price=float(price), kind=str(kind or ""), touches=0, volume=0.0, strength=0.0)


def candidate_from_doc(doc: dict[str, Any]) -> Candidate:
    """Inverse of `ledger._cand_doc`, which flattened the Setup into its parts."""
    setup = Setup(
        name=str(doc["setup"]), trigger=float(doc["trigger_px"]), stop=float(doc["stop_px"]),
        level=None if doc.get("level") is None else float(doc["level"]),
        meta=dict(doc.get("setup_meta") or {}),
    )
    known = {f.name for f in dataclasses.fields(Candidate)}
    extra = {k: v for k, v in doc.items() if k in known and k not in {"setup", "time", "symbol"}}
    extra["candle_tags"] = list(doc.get("candle_tags") or [])
    return Candidate(symbol=str(doc["symbol"]), time=_ist(doc["time"]), setup=setup, **extra)


def position_from_doc(doc: dict[str, Any], cfg: EngineConfig, bars_1m: pd.DataFrame,
                      full_5m: pd.DataFrame | None = None) -> Position:
    """Rebuild the live `Position` as it stood at entry.

    `bars_1m` must be the bars up to and including the entry minute, so the
    exit indicators start from the same history the scanner had.
    """
    cand = candidate_from_doc(doc)
    entry_time = _ist(doc["entry_time"])
    entry = float(doc["entry_price"])
    target = float(doc["target"])
    qty = int(doc["qty"])
    plan = TradePlan(
        entry=entry, stop=float(doc["stop"]), target=target, qty=qty,
        risk_inr=float(doc.get("risk_inr") or (entry - float(doc["stop"])) * qty),
        reward_inr=(target - entry) * qty,
        notional_inr=float(doc.get("notional_inr") or entry * qty),
    )
    ec = cfg.exit_cfg
    is_1m = _is_one_minute_setup(cand.setup.name)
    tf = bars_1m if is_1m else _bars_5m(bars_1m, full_5m)
    exit_state = exits.initial_state(
        entry=entry, hard_stop=cand.setup.stop, bars_tf=tf, prev_day=None,
        with_levels=ec.use_resistance_reject or cfg.use_structural_exit_levels,
        add_shelves=cfg.volume_shelf_levels,
        tap_min_touches=0 if is_1m else cfg.tap_levels_min_touches,
    )
    # The levels the live scanner actually held — exact, where the re-derived
    # ones are only close.
    if doc.get("structural_support") is not None:
        exit_state.structural_support = _level(
            doc["structural_support"], doc.get("structural_support_kind"))
    if doc.get("structural_resistance") is not None:
        exit_state.structural_resistance = _level(
            doc["structural_resistance"], doc.get("structural_resistance_kind"))
    return Position(
        cand=cand, entry_time=entry_time, plan=plan, highest=entry, exit_state=exit_state,
        target_source=str(doc.get("target_source") or "fixed_2r"),
        checkpoint=doc.get("checkpoint"), checkpoint_stop=doc.get("checkpoint_stop"),
    )


def replay_position(
    doc: dict[str, Any], day_bars: pd.DataFrame, cfg: EngineConfig,
    warmup_1m: pd.DataFrame | None = None,
) -> tuple[ClosedTrade | None, pd.DataFrame]:
    """Run one orphaned position to its exit. Pure: no network, no database.

    Returns the closed trade (None when there are no bars after the entry) and
    the bars up to the exit, for the review chart.
    """
    entry_time = _ist(doc["entry_time"])
    day_bars = day_bars[day_bars.index.date == entry_time.date()].sort_index()
    start = entry_time.floor("1min")
    pre = day_bars[day_bars.index <= start]
    after = day_bars[day_bars.index >= start]
    if after.empty:
        return None, day_bars

    state = DayState(
        symbol=str(doc["symbol"]), prev_close=float(day_bars["open"].iloc[0]),
        cum_vol_profile=None, warmup_1m=warmup_1m,
        warmup_5m=resample_5m(warmup_1m) if warmup_1m is not None else None,
    )
    state.position = position_from_doc(doc, cfg, pre)
    first = len(pre) - 1 if len(pre) else 0
    for i in range(first, len(day_bars)):
        window = day_bars.iloc[: i + 1]
        if window.index[-1] < start:
            continue
        step(state, window, cfg, lambda _s, _t: (0, ""), None)
        if state.position is None:
            return state.closed[-1], window
    # data ran out before the 15:15 close (quiet symbol): same rule as the live sweep
    last = day_bars.iloc[-1]
    return force_close(state, day_bars.index[-1], float(last["close"]), cfg), day_bars


def find_orphans(db: Any, strategy: str, now: pd.Timestamp,
                 only_date: date | None = None) -> list[dict[str, Any]]:
    """`open` long rows whose session is over."""
    out: list[dict[str, Any]] = []
    closed_t = datetime(2000, 1, 1, *MARKET_CLOSED_AFTER).time()
    query: dict[str, Any] = {"status": "open", "strategy": strategy_label(strategy, "long")}
    for doc in db["mt_positions"].find(query):
        if doc.get("side", "long") != "long":
            continue
        day = _ist(doc["entry_time"]).date()
        if only_date is not None and day != only_date:
            continue
        if day > now.date() or (day == now.date() and now.time() < closed_t):
            continue   # the live scanner may still own it
        out.append(doc)
    return out


def reconcile(
    db: Any, client: Any, instruments: dict[str, Any], cfg: EngineConfig, strategy: str,
    csv_path: Any, now: pd.Timestamp, *, apply: bool, only_date: date | None = None,
) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for doc in find_orphans(db, strategy, now, only_date):
        sym = str(doc["symbol"])
        entry_time = _ist(doc["entry_time"])
        inst = instruments.get(sym)
        if inst is None:
            outcomes.append(Outcome(sym, entry_time, "skipped", "no instrument key"))
            continue
        day = entry_time.date()
        bars = client.historical_1m(inst.key, day, day)
        if bars.empty:
            outcomes.append(Outcome(sym, entry_time, "skipped", "no 1-minute bars returned"))
            continue
        hist = client.historical_1m(inst.key, day - timedelta(days=WARMUP_DAYS),
                                    day - timedelta(days=1))
        trade, window = replay_position(doc, bars, cfg, hist if not hist.empty else None)
        if trade is None:
            outcomes.append(Outcome(sym, entry_time, "skipped", "no bars after entry"))
            continue
        if apply:
            ledger = PaperLedger(db, csv_path, bool(doc.get("float_filter_applied", False)),
                                 strategy)
            ledger.closed(trade, chart_bars=window, reconciled=True)
        detail = f"{trade.exit_reason} @ {trade.exit:.2f}  net ₹{trade.net_inr:,.0f}"
        outcomes.append(Outcome(sym, entry_time, "closed", detail, trade))
    return outcomes


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="write the exits (default: dry-run, prints what would close)")
    ap.add_argument("--date", type=date.fromisoformat, default=None,
                    help="only positions entered on this date (YYYY-MM-DD)")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    # Imported here: scanner pulls in the whole live stack, tests of the pure
    # replay above do not need it.
    from pymongo import MongoClient

    from .scanner import _apply_env_overrides, _market_data_token, _repo_path, _strategy_config
    from .upstox import UpstoxAuthError, UpstoxClient

    settings = Settings()
    cfg = _apply_env_overrides(_strategy_config(settings), settings)
    token = _market_data_token(settings)
    client = UpstoxClient(token, cache_dir=_repo_path(settings.mt_cache_dir))
    mongo: Any = MongoClient(settings.mongodb_uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    db = mongo[settings.mongodb_db_name]
    now = pd.Timestamp.now(tz=IST)
    try:
        outcomes = reconcile(db, client, client.nse_equities(), cfg, settings.mt_strategy,
                             _repo_path(settings.mt_log_csv), now,
                             apply=args.apply, only_date=args.date)
    except UpstoxAuthError as exc:
        print(f"Upstox rejected the token ({exc}). Refresh UPSTOX_ACCESS_TOKEN / "
              f"UPSTOX_ANALYTICS_TOKEN in .env and re-run.", file=sys.stderr)
        return 3

    if not outcomes:
        print("no orphaned open positions")
        return 0
    verb = "closed" if args.apply else "would close"
    for o in outcomes:
        tag = verb if o.status == "closed" else "skipped"
        print(f"{o.entry_time:%Y-%m-%d %H:%M}  {o.symbol:<12} {tag:<11} {o.detail}")
    n = sum(1 for o in outcomes if o.status == "closed")
    print(f"\n{n} {verb}, {len(outcomes) - n} skipped"
          + ("" if args.apply else "  (dry run — pass --apply to write)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
