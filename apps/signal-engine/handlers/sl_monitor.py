"""Lambda: sl-monitor — EventBridge every 3 min during market hours.

For each open position:
  1. Fetch current price
  2. Update highest_price and trailing SL (only moves up)
  3. Check exit conditions in priority order: sl_hit → target_hit → day5
  4. On exit: close position, calculate net P&L, send Telegram alert

Skips silently outside market hours.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from src.config import Settings
from src.db.client import get_db
from src.news_trader.db import ensure_indexes, positions
from src.news_trader.market_calendar import is_trading_day
from src.news_trader.prices import get_ltps
from src.news_trader.telegram import alert_sl_updated, alert_trade_closed
from src.news_trader.trailing_sl import (
    calc_pnl,
    check_exit,
    update_stop,
    update_stop_short,
    update_trailing_sl,
)

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

_IST_OFFSET = 5.5 * 3600


def _trading_days_held(entry_at: datetime) -> int:
    """Count trading sessions from entry_at (exclusive) to today (inclusive).

    Uses the NSE holiday calendar so weekends + public holidays don't count
    toward max_hold_days. A trade entered Monday morning and checked the
    following Monday = 5 sessions, regardless of calendar-day arithmetic.
    """
    entry_date: date = entry_at.astimezone(timezone.utc).date()
    today: date = datetime.now(tz=timezone.utc).date()
    if today <= entry_date:
        return 0
    count = 0
    d = entry_date
    while d < today:
        d += timedelta(days=1)
        if d.weekday() < 5 and is_trading_day(d):
            count += 1
    return count


def _is_market_hours(bypass_holiday: bool = False) -> bool:
    now_ist = datetime.fromtimestamp(
        datetime.now(tz=timezone.utc).timestamp() + _IST_OFFSET
    )
    if now_ist.weekday() >= 5:
        return False
    if not bypass_holiday and not is_trading_day(now_ist.date()):
        return False
    total_minutes = now_ist.hour * 60 + now_ist.minute
    # 09:15 to 15:25 (5 min before close — avoid last-minute market orders)
    return 9 * 60 + 15 <= total_minutes <= 15 * 60 + 25


async def _monitor_position(pos: dict, settings: Settings, price: float, nifty50: float | None = None, eod_close: bool = False) -> str:
    """Returns 'closed:<reason>', 'sl_raised', or 'unchanged'.

    The current price is supplied by the caller, which batch-fetches every open
    symbol in a single network call (see _run).
    """
    db = get_db()
    symbol = pos["symbol"]

    paper = pos.get("paper", True)

    # Trade side. Positions opened before shorts existed have no direction
    # field — they are all longs.
    direction = pos.get("direction", "long")

    # Update the stop using params frozen at entry, not live config — open
    # positions keep the geometry they were opened with, so a tuning change
    # never moves an existing trade's stop mid-flight.
    #
    # Positions opened after the split-stop change carry initial_sl_pct_used and
    # take the wide-initial → tight-trail path (update_stop). Older positions have
    # no such field and keep the original pure-trailing behavior (update_trailing_sl).
    initial_sl_pct = pos.get("initial_sl_pct_used")
    if direction == "short":
        # Shorts always postdate the split-stop change, so frozen params exist.
        # Favourable extreme is the LOW; the stop trails above it and only moves down.
        new_lowest, new_sl = update_stop_short(
            entry_price=pos["entry_price"],
            current_price=price,
            lowest_price=pos.get("lowest_price") or pos["entry_price"],
            current_sl=pos["trailing_sl"],
            initial_sl_pct=initial_sl_pct or settings.nt_initial_sl_pct,
            trail_sl_pct=pos.get("trail_sl_pct_used") or settings.nt_trail_sl_pct,
            trail_activate_pct=pos.get("trail_activate_pct_used") or settings.nt_trail_activate_pct,
        )
        new_highest = max(pos["highest_price"], price)  # MAE side for a short
        sl_improved = new_sl < pos["trailing_sl"]
    elif initial_sl_pct is not None:
        trail_sl_pct = pos.get("trail_sl_pct_used")
        if trail_sl_pct is None:
            trail_sl_pct = settings.nt_trail_sl_pct
        trail_activate_pct = pos.get("trail_activate_pct_used")
        if trail_activate_pct is None:
            trail_activate_pct = settings.nt_trail_activate_pct
        new_highest, new_sl = update_stop(
            entry_price=pos["entry_price"],
            current_price=price,
            highest_price=pos["highest_price"],
            current_sl=pos["trailing_sl"],
            initial_sl_pct=initial_sl_pct,
            trail_sl_pct=trail_sl_pct,
            trail_activate_pct=trail_activate_pct,
        )
        sl_improved = new_sl > pos["trailing_sl"]
    else:
        # Legacy pure-trail position (opened before the split-stop change).
        sl_pct = pos.get("sl_pct_used") or settings.nt_sl_pct
        new_highest, new_sl = update_trailing_sl(
            current_price=price,
            highest_price=pos["highest_price"],
            current_sl=pos["trailing_sl"],
            sl_pct=sl_pct,
        )
        sl_improved = new_sl > pos["trailing_sl"]

    # Track the low-water mark alongside the high. For longs this is the MAE
    # side; for shorts update_stop_short already computed it (MFE side).
    if direction != "short":
        new_lowest = min(pos.get("lowest_price") or pos["entry_price"], price)

    # Check exit — use params frozen at entry for the same reason as stop params.
    max_hold_days = pos.get("max_hold_days_used") or settings.nt_max_hold_days
    max_hold_minutes = pos.get("max_hold_minutes_used") or settings.nt_max_hold_minutes
    # Motor returns BSON dates as naive UTC datetimes; normalize before subtracting.
    entry_at: datetime = pos["entry_at"]
    if entry_at.tzinfo is None:
        entry_at = entry_at.replace(tzinfo=timezone.utc)
    held_minutes = (datetime.now(tz=timezone.utc) - entry_at).total_seconds() / 60
    exit_reason = check_exit(
        current_price=price,
        trailing_sl=new_sl,
        target_price=pos["target_price"],
        held_sessions=_trading_days_held(pos["entry_at"]),
        max_hold_days=max_hold_days,
        held_minutes=held_minutes,
        max_hold_minutes=max_hold_minutes,
        eod_close=eod_close,
        direction=direction,
    )

    if exit_reason:
        gross_pnl, net_pnl, costs = calc_pnl(
            pos["entry_price"], price, pos["qty"], direction=direction
        )
        now = datetime.now(tz=timezone.utc)
        await positions(db).update_one(
            {"_id": pos["_id"]},
            {
                "$set": {
                    "status": "closed",
                    "exit_reason": exit_reason,
                    "exit_price": price,
                    "exit_at": now,
                    "highest_price": new_highest,
                    "lowest_price": new_lowest,
                    "trailing_sl": new_sl,
                    "current_price": price,
                    "gross_pnl": gross_pnl,
                    "net_pnl": net_pnl,
                    "costs": costs,
                    # NIFTY 50 level at exit — pairs with entry_nifty50 to compute
                    # market-adjusted (alpha) P&L over the holding period.
                    "exit_nifty50": nifty50,
                },
                "$push": {
                    "price_snapshots": {
                        "$each": [{"t": now.isoformat(), "p": price}],
                        "$slice": -100,  # keep last 100 ticks (~5 hours at 3-min interval)
                    }
                },
            },
        )
        logger.info(
            "position_closed symbol=%s reason=%s entry=%.2f exit=%.2f net_pnl=%.0f paper=%s",
            symbol, exit_reason, pos["entry_price"], price, net_pnl, paper,
        )
        await asyncio.to_thread(
            alert_trade_closed,
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            symbol=symbol,
            exit_reason=exit_reason,
            entry_price=pos["entry_price"],
            exit_price=price,
            qty=pos["qty"],
            net_pnl=net_pnl,
            paper=paper,
        )
        return f"closed:{exit_reason}"

    # Position stays open — persist updated SL + latest price, append to history
    now = datetime.now(tz=timezone.utc)
    await positions(db).update_one(
        {"_id": pos["_id"]},
        {
            "$set": {"highest_price": new_highest, "lowest_price": new_lowest, "trailing_sl": new_sl, "current_price": price},
            "$push": {
                "price_snapshots": {
                    "$each": [{"t": now.isoformat(), "p": price}],
                    "$slice": -100,
                }
            },
        },
    )

    if sl_improved:
        logger.info("sl_raised symbol=%s direction=%s old=%.2f new=%.2f price=%.2f",
                    symbol, direction, pos["trailing_sl"], new_sl, price)
        await asyncio.to_thread(
            alert_sl_updated,
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            symbol=symbol,
            old_sl=pos["trailing_sl"],
            new_sl=new_sl,
            current_price=price,
        )
        return "sl_raised"

    return "unchanged"


async def _run(settings: Settings) -> dict:
    # Backstop run finalization runs on every tick, independent of the market-hours
    # gate below. On AWS, runs whose signals were all non-actionable never trigger
    # tradeDecision (nothing is enqueued to the signals queue), so they'd stay
    # 'running' forever without this sweep. See pipeline_lifecycle.sweep_stale_runs.
    try:
        from src.news_trader.pipeline_lifecycle import sweep_stale_runs
        swept = await sweep_stale_runs(settings.nt_news_delay_seconds)
        if swept:
            logger.info("sl_monitor_swept_stale_runs n=%d", swept)
    except Exception as exc:
        logger.error("sl_monitor_sweep_failed err=%s", exc)

    if not _is_market_hours(bypass_holiday=settings.nt_bypass_market_holiday):
        logger.info("sl_monitor_skipped outside_market_hours")
        return {"skipped": "outside_market_hours"}
    if settings.nt_bypass_market_holiday:
        logger.info("sl_monitor holiday check bypassed (NT_BYPASS_MARKET_HOLIDAY=true)")

    db = get_db()
    await ensure_indexes(db)

    open_positions = await positions(db).find({"status": "open"}).to_list(length=100)
    if not open_positions:
        return {"open_positions": 0}

    results: dict[str, int] = {"unchanged": 0, "sl_raised": 0, "closed": 0, "no_price": 0}

    # Batch-fetch every open symbol's price in ONE yf.download call rather than
    # N concurrent get_ltp() calls — a single round-trip, and it avoids many
    # parallel yfinance requests tripping Yahoo's rate limiter.
    # Fold the NIFTY 50 level into the same batch call (no extra round-trip) so
    # each closing trade can freeze the index level at exit. "^NSEI" is never a
    # position symbol, so it won't be mistaken for one in the loop below.
    symbols = list({pos["symbol"] for pos in open_positions})
    price_map = await asyncio.to_thread(get_ltps, symbols + ["^NSEI"])
    nifty50 = price_map.get("^NSEI")

    # EOD force-close: if nt_force_close_eod is enabled and wall-clock IST >= 15:15,
    # all open positions are closed regardless of SL/target state. Eliminates overnight
    # gap risk (the primary source of large losses in this system).
    eod_close = False
    if settings.nt_force_close_eod:
        now_ist = datetime.fromtimestamp(datetime.now(tz=timezone.utc).timestamp() + _IST_OFFSET)
        ist_min = now_ist.hour * 60 + now_ist.minute
        eod_close = ist_min >= 15 * 60 + 15  # 15:15 IST
        if eod_close:
            logger.info("sl_monitor_eod_close_active ist_min=%d forcing close of all %d positions",
                        ist_min, len(open_positions))

    priced: list[tuple[dict, float]] = []
    no_priced: list[dict] = []
    for pos in open_positions:
        price = price_map.get(pos["symbol"])
        if not price:
            logger.warning("sl_monitor_no_price symbol=%s pos_id=%s", pos["symbol"], pos["_id"])
            results["no_price"] += 1
            no_priced.append(pos)
        else:
            priced.append((pos, price))

    outcomes = await asyncio.gather(
        *[_monitor_position(pos, settings, price, nifty50, eod_close=eod_close) for pos, price in priced],
        return_exceptions=True,
    )
    for outcome in outcomes:
        if isinstance(outcome, Exception):
            logger.error("monitor_position_failed err=%s", outcome)
            continue
        if outcome.startswith("closed"):
            results["closed"] += 1
        elif outcome in results:
            results[outcome] += 1

    # Day5 force-close for positions where live price is unavailable.
    # SL/target can't be checked without a price, but time-based exit can still fire.
    # Use the last known current_price (from a previous tick) or fall back to entry_price
    # so P&L can be calculated — better a stale-price close than a stuck-open position.
    for pos in no_priced:
        held = _trading_days_held(pos["entry_at"])
        max_hold = pos.get("max_hold_days_used") or settings.nt_max_hold_days
        if held >= max_hold:
            fallback_price: float = pos.get("current_price") or pos["entry_price"]
            gross_pnl, net_pnl, costs = calc_pnl(
                pos["entry_price"], fallback_price, pos["qty"],
                direction=pos.get("direction", "long"),
            )
            now = datetime.now(tz=timezone.utc)
            await positions(db).update_one(
                {"_id": pos["_id"]},
                {
                    "$set": {
                        "status": "closed",
                        "exit_reason": "day5",
                        "exit_price": fallback_price,
                        "exit_at": now,
                        "gross_pnl": gross_pnl,
                        "net_pnl": net_pnl,
                        "costs": costs,
                        "exit_nifty50": nifty50,
                    }
                },
            )
            logger.warning(
                "day5_force_close_no_price symbol=%s held=%d fallback_price=%.2f net_pnl=%.0f",
                pos["symbol"], held, fallback_price, net_pnl,
            )
            await asyncio.to_thread(
                alert_trade_closed,
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
                symbol=pos["symbol"],
                exit_reason="day5",
                entry_price=pos["entry_price"],
                exit_price=fallback_price,
                qty=pos["qty"],
                net_pnl=net_pnl,
                paper=pos.get("paper", True),
            )
            results["closed"] += 1

    logger.info("sl_monitor_done open=%d %s", len(open_positions), results)
    return {"open_positions": len(open_positions), **results}


def handler(event: dict, context: object) -> dict:
    settings = Settings()
    return asyncio.run(_run(settings))
