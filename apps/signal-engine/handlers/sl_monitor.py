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
from src.news_trader.trailing_sl import calc_pnl, check_exit, update_trailing_sl

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


async def _monitor_position(pos: dict, settings: Settings, price: float) -> str:
    """Returns 'closed:<reason>', 'sl_raised', or 'unchanged'.

    The current price is supplied by the caller, which batch-fetches every open
    symbol in a single network call (see _run).
    """
    db = get_db()
    symbol = pos["symbol"]

    paper = pos.get("paper", True)

    # Update trailing SL — use the sl_pct frozen at entry, not the live config.
    # If nt_sl_pct is changed during tuning, open positions keep their original SL width.
    sl_pct = pos.get("sl_pct_used") or settings.nt_sl_pct
    new_highest, new_sl = update_trailing_sl(
        current_price=price,
        highest_price=pos["highest_price"],
        current_sl=pos["trailing_sl"],
        sl_pct=sl_pct,
    )

    sl_raised = new_sl > pos["trailing_sl"]

    # Check exit — use max_hold_days frozen at entry for the same reason.
    max_hold_days = pos.get("max_hold_days_used") or settings.nt_max_hold_days
    exit_reason = check_exit(
        current_price=price,
        trailing_sl=new_sl,
        target_price=pos["target_price"],
        held_sessions=_trading_days_held(pos["entry_at"]),
        max_hold_days=max_hold_days,
    )

    if exit_reason:
        gross_pnl, net_pnl, costs = calc_pnl(pos["entry_price"], price, pos["qty"])
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
                    "trailing_sl": new_sl,
                    "current_price": price,
                    "gross_pnl": gross_pnl,
                    "net_pnl": net_pnl,
                    "costs": costs,
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
            "$set": {"highest_price": new_highest, "trailing_sl": new_sl, "current_price": price},
            "$push": {
                "price_snapshots": {
                    "$each": [{"t": now.isoformat(), "p": price}],
                    "$slice": -100,
                }
            },
        },
    )

    if sl_raised:
        logger.info("sl_raised symbol=%s old=%.2f new=%.2f price=%.2f",
                    symbol, pos["trailing_sl"], new_sl, price)
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
    symbols = list({pos["symbol"] for pos in open_positions})
    price_map = await asyncio.to_thread(get_ltps, symbols)

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
        *[_monitor_position(pos, settings, price) for pos, price in priced],
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
            gross_pnl, net_pnl, costs = calc_pnl(pos["entry_price"], fallback_price, pos["qty"])
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
