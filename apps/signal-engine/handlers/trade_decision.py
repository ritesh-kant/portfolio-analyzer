"""Lambda: trade-decision — SQS trigger from news-signals queue (delayed 15 min).

For each signal:
  1. Check open position count < nt_max_positions
  2. For each stock in signal (up to nt_max_stocks_per_signal):
     a. Fetch current price
     b. Calculate qty = floor(nt_position_size_inr / price)
     c. In paper mode: insert open position to nt_positions
     d. Send Telegram alert
  3. Mark signal as acted_on
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from src.config import Settings
from src.db.client import get_db
from src.news_trader.db import ensure_indexes, positions, signals
from src.news_trader.prices import get_ltp
from src.news_trader.telegram import alert_trade_entered
from src.news_trader.trailing_sl import calc_qty, initial_trailing_sl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _process_signal(signal_doc: dict[str, Any], settings: Settings) -> int:
    """Returns number of positions opened."""
    db = get_db()
    paper = settings.trading_mode.lower() != "live"
    signal_id = str(signal_doc.get("_id", "?"))

    logger.info("[TRADE] evaluating signal_id=%s sector=%s signal=%s confidence=%s stocks=%s",
                signal_id, signal_doc.get("sector"), signal_doc.get("signal"),
                signal_doc.get("confidence"), signal_doc.get("stocks"))

    # Check available capacity
    open_count = await positions(db).count_documents({"status": "open"})
    capacity = settings.nt_max_positions - open_count
    logger.info("[TRADE] capacity check open=%d max=%d available=%d",
                open_count, settings.nt_max_positions, capacity)
    if capacity <= 0:
        logger.info("[TRADE] skip — portfolio full open=%d max=%d", open_count, settings.nt_max_positions)
        return 0

    stocks = signal_doc.get("stocks", [])
    if not stocks:
        logger.info("[TRADE] skip — no stocks in signal")
        return 0

    entered = 0
    candidates = stocks[: min(settings.nt_max_stocks_per_signal, capacity)]
    logger.info("[TRADE] evaluating %d stock(s): %s", len(candidates), candidates)
    for symbol in candidates:
        # Check if we already have an open position in this symbol
        existing = await positions(db).find_one({"symbol": symbol, "status": "open"})
        if existing:
            logger.info("[TRADE] skip %s — already have open position", symbol)
            continue

        logger.info("[TRADE] fetching LTP for %s...", symbol)
        price = get_ltp(symbol)
        if not price:
            logger.warning("[TRADE] no price for %s — skipping", symbol)
            continue

        logger.info("[TRADE] %s LTP=%.2f — calculating position size...", symbol, price)
        qty = calc_qty(settings.nt_position_size_inr, price)
        target = price * (1.0 + settings.nt_target_pct)
        sl = initial_trailing_sl(price, settings.nt_sl_pct)
        now = datetime.now(tz=timezone.utc)

        position_doc = {
            "symbol": symbol,
            "signal_id": str(signal_doc["_id"]),
            "signal": signal_doc.get("signal"),
            "sector": signal_doc.get("sector"),
            "confidence": signal_doc.get("confidence"),
            "entry_price": price,
            "qty": qty,
            "entry_value": price * qty,
            "entry_at": now,
            "highest_price": price,
            "trailing_sl": sl,
            "target_price": target,
            "status": "open",
            "exit_reason": None,
            "exit_price": None,
            "exit_at": None,
            "gross_pnl": None,
            "net_pnl": None,
            "paper": paper,
        }

        if paper:
            await positions(db).insert_one(position_doc)
            logger.info(
                "[TRADE] paper position opened symbol=%s price=%.2f qty=%d sl=%.2f target=%.2f value=₹%.0f",
                symbol, price, qty, sl, target, price * qty,
            )
        else:
            # Live: GTT placement via Kite Connect (Month 5+)
            logger.warning("[TRADE] live trading not implemented for %s — falling back to paper", symbol)
            await positions(db).insert_one(position_doc)

        logger.info("[TRADE] sending Telegram alert for %s...", symbol)
        alert_trade_entered(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            symbol=symbol,
            signal=signal_doc.get("signal", ""),
            entry_price=price,
            qty=qty,
            trailing_sl=sl,
            target_price=target,
            confidence=signal_doc.get("confidence", ""),
            reasoning=signal_doc.get("reasoning", ""),
            paper=paper,
        )
        entered += 1

    logger.info("[TRADE] signal_id=%s done — %d position(s) opened", signal_id, entered)
    return entered


async def _run(event: dict[str, Any], settings: Settings) -> dict[str, int]:
    db = get_db()
    await ensure_indexes(db)

    records = event.get("Records", [])
    logger.info("[TRADE] processing %d SQS record(s)", len(records))
    total_entered = 0

    for record in records:
        try:
            msg = json.loads(record["body"])
            signal_id = msg.get("signal_id")
            if not signal_id:
                logger.warning("[TRADE] bad message — no signal_id: %s", msg)
                continue

            from src.news_trader.db import signals as signals_coll
            signal_doc = await signals_coll(db).find_one({"_id": ObjectId(signal_id)})
            if not signal_doc:
                logger.warning("[TRADE] signal not found id=%s", signal_id)
                continue

            if signal_doc.get("acted_on"):
                logger.info("[TRADE] signal already acted on id=%s — skipping", signal_id)
                continue

            n = await _process_signal(signal_doc, settings)
            total_entered += n

            await signals_coll(db).update_one(
                {"_id": ObjectId(signal_id)}, {"$set": {"acted_on": True}}
            )
        except Exception as exc:
            logger.error("[TRADE] error processing record err=%s", exc)

    logger.info("[TRADE] done — processed=%d total_positions_opened=%d", len(records), total_entered)
    return {"processed": len(records), "positions_opened": total_entered}


def handler(event: dict[str, Any], context: object) -> dict[str, int]:
    settings = Settings()
    return asyncio.run(_run(event, settings))
