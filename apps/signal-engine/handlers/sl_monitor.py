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
from datetime import datetime, timezone

from src.config import Settings
from src.db.client import get_db
from src.news_trader.db import ensure_indexes, positions
from src.news_trader.market_calendar import is_trading_day
from src.news_trader.prices import get_ltp
from src.news_trader.telegram import alert_sl_updated, alert_trade_closed
from src.news_trader.trailing_sl import calc_pnl, check_exit, update_trailing_sl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_IST_OFFSET = 5.5 * 3600


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


async def _monitor_position(pos: dict, settings: Settings) -> str:
    """Returns 'closed:<reason>', 'sl_raised', or 'unchanged'."""
    db = get_db()
    symbol = pos["symbol"]
    price = get_ltp(symbol)

    if not price:
        logger.warning("sl_monitor_no_price symbol=%s pos_id=%s", symbol, pos["_id"])
        return "no_price"

    paper = pos.get("paper", True)

    # Update trailing SL
    new_highest, new_sl = update_trailing_sl(
        current_price=price,
        highest_price=pos["highest_price"],
        current_sl=pos["trailing_sl"],
        sl_pct=settings.nt_sl_pct,
    )

    sl_raised = new_sl > pos["trailing_sl"]

    # Check exit
    exit_reason = check_exit(
        current_price=price,
        trailing_sl=new_sl,
        target_price=pos["target_price"],
        entry_at=pos["entry_at"],
        max_hold_days=settings.nt_max_hold_days,
    )

    if exit_reason:
        gross_pnl, net_pnl = calc_pnl(pos["entry_price"], price, pos["qty"])
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
                }
            },
        )
        logger.info(
            "position_closed symbol=%s reason=%s entry=%.2f exit=%.2f net_pnl=%.0f paper=%s",
            symbol, exit_reason, pos["entry_price"], price, net_pnl, paper,
        )
        alert_trade_closed(
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

    # Position stays open — persist updated SL + latest price for unrealized P&L
    update: dict = {"highest_price": new_highest, "trailing_sl": new_sl, "current_price": price}
    await positions(db).update_one({"_id": pos["_id"]}, {"$set": update})

    if sl_raised:
        logger.info("sl_raised symbol=%s old=%.2f new=%.2f price=%.2f",
                    symbol, pos["trailing_sl"], new_sl, price)
        alert_sl_updated(
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
    for pos in open_positions:
        outcome = await _monitor_position(pos, settings)
        if outcome.startswith("closed"):
            results["closed"] += 1
        elif outcome in results:
            results[outcome] += 1

    logger.info("sl_monitor_done open=%d %s", len(open_positions), results)
    return {"open_positions": len(open_positions), **results}


def handler(event: dict, context: object) -> dict:
    settings = Settings()
    return asyncio.run(_run(settings))
