"""Daily paper-trade runner for Strategy A (PEAD Midcap 150). Month 4.

Run once per trading day, after NSE close (~6pm IST):

    uv run python -m quant.execution.paper_runner

What it does each day
---------------------
1. ENTRIES — find today's earnings announcements that pass the PEAD gate;
   enter next-open positions via PaperBroker.
2. EXITS — close any position that has been held for MAX_HOLD_DAYS (5 trading
   days, per hypothesis §1 + §5.1); use today's closing price.
3. MARK-TO-MARKET — update unrealised PnL on all open positions.
4. STATUS — print a one-page daily summary.

PIT discipline
--------------
Entries use only data with as_of_timestamp <= today's date to avoid look-ahead.
OHLCV is loaded via pit_loader which enforces the same constraint.

Logging / persistence
---------------------
- PaperBroker persists orders to data/paper/orders.json.
- A daily run log is appended to data/paper/run_log.jsonl.
- Post-mortem agent fires automatically on each close_position() call
  (wired inside PaperBroker.close_position → agents.post_mortem.run).

Usage
-----
    # Standard daily run
    uv run python -m quant.execution.paper_runner

    # Run for a specific date (back-test paper mode, for debugging only)
    uv run python -m quant.execution.paper_runner --date 2024-08-15

    # Dry run: print what would be traded without placing orders
    uv run python -m quant.execution.paper_runner --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from quant.data.earnings_ingest import load_earnings
from quant.data.pit_loader import load as pit_load
from quant.execution.paper import PaperBroker, Position
from quant.research.holdout_lock import assert_no_holdout_access
from quant.strategies.pead_midcap import (
    _MAX_POSITION_FRACTION,
    _ROUND_TRIP_COST,
    select_candidates,
)

logger = logging.getLogger(__name__)

_STRATEGY_ID = "pead_midcap"
MAX_HOLD_DAYS = 5          # calendar days to close at (hypothesis §1)
_AUM_INR = 5_000_000       # ₹50L paper AUM (plan §7.1)
_MIN_PRICE = 50.0          # skip sub-₹50 stocks (liquidity floor)
_RUN_LOG = Path("data/paper/run_log.jsonl")


def _load_universe() -> list[str]:
    p = Path("data/lake/midcap150_constituents.csv")
    if not p.exists():
        logger.error("midcap150_constituents.csv not found at %s", p)
        return []
    return pd.read_csv(p)["symbol"].str.upper().tolist()


def _trading_days_held(entry_date: str, today: date) -> int:
    """Conservative proxy: calendar days minus weekends.  Good enough for paper."""
    entry = date.fromisoformat(entry_date)
    delta = (today - entry).days
    full_weeks = delta // 7
    remainder = delta % 7
    # weekday() of entry: Mon=0 … Fri=4
    extra_weekends = max(0, remainder - (5 - entry.weekday()))
    return max(0, delta - 2 * full_weeks - (1 if extra_weekends > 0 else 0))


def _close_price_for(symbol: str, as_of: str, ohlcv: pd.DataFrame) -> float | None:
    """Return the most recent close for symbol on or before as_of.

    ohlcv has a MultiIndex (business_date, symbol) from pit_loader.load().
    """
    as_of_dt = pd.Timestamp(as_of).date()
    try:
        # xs on the symbol level of the MultiIndex
        sym_rows = ohlcv.xs(symbol.upper(), level="symbol")
    except KeyError:
        return None
    sym_rows = sym_rows[sym_rows.index <= as_of_dt]
    if sym_rows.empty:
        return None
    return float(sym_rows.sort_index().iloc[-1]["close"])


def run_daily(
    run_date: date | None = None,
    dry_run: bool = False,
) -> dict:
    """Execute the daily paper-trade cycle.

    Parameters
    ----------
    run_date
        The trading date to process.  Defaults to today.
    dry_run
        If True, compute entries/exits but do not place any orders.

    Returns
    -------
    dict — summary suitable for JSON logging.
    """
    today = run_date or date.today()
    today_str = today.isoformat()
    logger.info("=== Paper runner — %s%s ===", today_str, " [DRY RUN]" if dry_run else "")

    # Hold-out guard: refuse to run if today is in the hold-out window.
    # The gate check and hold-out ceremony use separate data paths; the
    # paper runner must never process live hold-out dates before the
    # one-shot ceremony has been run.
    try:
        assert_no_holdout_access(today_str)
    except ValueError:
        logger.error(
            "Today (%s) is within the hold-out window (2024-07-01 → present).\n"
            "Paper trading in the hold-out period requires the ceremony to have\n"
            "passed first.  Run: QUANT_HOLDOUT_UNLOCK=pead_midcap "
            "uv run python -m quant.research.holdout_ceremony",
            today_str,
        )
        return {"error": "hold_out_blocked", "date": today_str}

    # ── Load data ──────────────────────────────────────────────────────────────
    universe = _load_universe()
    if not universe:
        logger.error("Empty universe — aborting.")
        return {"error": "empty_universe", "date": today_str}

    try:
        earnings_df = load_earnings(end=today_str)
    except Exception as exc:
        logger.error("earnings load failed: %s", exc)
        return {"error": str(exc), "date": today_str}

    try:
        ohlcv = pit_load(symbol=None, start="2015-01-01", end=today_str)
    except Exception as exc:
        logger.error("OHLCV load failed: %s", exc)
        return {"error": str(exc), "date": today_str}

    broker = PaperBroker()

    # ── 1. EXITS: close positions at MAX_HOLD_DAYS ─────────────────────────────
    exits = []
    open_positions = broker.get_positions()
    for pos in open_positions:
        if pos.strategy_id != _STRATEGY_ID:
            continue
        held = _trading_days_held(pos.entry_date, today)
        if held >= MAX_HOLD_DAYS:
            close_px = _close_price_for(pos.symbol, today_str, ohlcv)
            if close_px is None:
                logger.warning("No close price for %s on %s — skipping exit", pos.symbol, today_str)
                continue
            logger.info(
                "EXIT %s  entry=%s  held=%d days  close=%.2f",
                pos.symbol, pos.entry_date, held, close_px,
            )
            if not dry_run:
                broker.close_position(
                    pos.symbol,
                    strategy_id=_STRATEGY_ID,
                    exit_price=close_px,
                    exit_date=today_str,
                )
            exits.append({"symbol": pos.symbol, "entry_date": pos.entry_date,
                          "held_days": held, "close_px": close_px})

    # ── 2. ENTRIES: find today's PEAD candidates ───────────────────────────────
    candidates = select_candidates(
        announcement_date=today_str,
        earnings_df=earnings_df,
        ohlcv=ohlcv,
        midcap150_universe=universe,
    )
    logger.info("Candidates for %s: %s", today_str, candidates or "none")

    entries = []
    existing_symbols = {p.symbol for p in broker.get_positions() if p.strategy_id == _STRATEGY_ID}
    for sym in candidates:
        if sym in existing_symbols:
            logger.info("SKIP %s — already in position", sym)
            continue

        entry_px = _close_price_for(sym, today_str, ohlcv)
        if entry_px is None or entry_px < _MIN_PRICE:
            logger.warning("SKIP %s — no price or below minimum (%.2f)", sym, entry_px or 0)
            continue

        # Quarter-Kelly, capped at _MAX_POSITION_FRACTION (5% per plan §7.1)
        position_value = _AUM_INR * _MAX_POSITION_FRACTION
        qty = max(1, int(position_value // entry_px))

        logger.info("ENTRY %s  qty=%d  price=%.2f  value=₹%.0f",
                    sym, qty, entry_px, qty * entry_px)
        if not dry_run:
            broker.place_order(
                symbol=sym,
                side="BUY",
                qty=qty,
                price=entry_px,
                strategy_id=_STRATEGY_ID,
                notes=f"PEAD entry {today_str}",
            )
        entries.append({"symbol": sym, "qty": qty, "price": entry_px})

    # ── 3. MARK-TO-MARKET ─────────────────────────────────────────────────────
    exited_symbols = {e["symbol"] for e in exits}
    if dry_run:
        open_after = [
            p for p in open_positions
            if p.strategy_id == _STRATEGY_ID and p.symbol not in exited_symbols
        ]
    else:
        open_after = broker.get_positions(strategy_id=_STRATEGY_ID)
    mtm_rows = []
    for pos in open_after:
        px = _close_price_for(pos.symbol, today_str, ohlcv) or pos.entry_price
        unreal_pct = (px - pos.entry_price) / pos.entry_price * 100
        mtm_rows.append({"symbol": pos.symbol, "entry": pos.entry_price,
                         "current": px, "pct": unreal_pct,
                         "entry_date": pos.entry_date})

    # ── 4. STATUS REPORT ──────────────────────────────────────────────────────
    print()
    print(f"{'=' * 60}")
    print(f"  PEAD Paper Runner — {today_str}{' [DRY RUN]' if dry_run else ''}")
    print(f"{'=' * 60}")
    print(f"  Entries today:  {len(entries)}")
    for e in entries:
        print(f"    BUY  {e['symbol']:12s}  qty={e['qty']:4d}  ₹{e['price']:.2f}")
    print(f"  Exits today:    {len(exits)}")
    for x in exits:
        print(f"    SELL {x['symbol']:12s}  held={x['held_days']}d  ₹{x['close_px']:.2f}")
    print(f"  Open positions: {len(mtm_rows)}")
    for m in sorted(mtm_rows, key=lambda r: r["pct"], reverse=True):
        sign = "+" if m["pct"] >= 0 else ""
        print(f"    {m['symbol']:12s}  entry={m['entry_date']}  {sign}{m['pct']:.2f}%")
    print(f"{'=' * 60}")
    print()

    summary = {
        "date": today_str,
        "dry_run": dry_run,
        "entries": entries,
        "exits": exits,
        "open_positions": len(mtm_rows),
        "run_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    # ── 5. APPEND TO RUN LOG ──────────────────────────────────────────────────
    if not dry_run:
        _RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _RUN_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(summary) + "\n")

    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Daily PEAD paper-trade runner")
    parser.add_argument(
        "--date",
        default=None,
        help="Run for a specific date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute entries/exits but do not place orders",
    )
    args = parser.parse_args()

    run_date = date.fromisoformat(args.date) if args.date else None
    result = run_daily(run_date=run_date, dry_run=args.dry_run)

    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
