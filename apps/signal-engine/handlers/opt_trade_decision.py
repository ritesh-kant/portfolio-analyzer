"""Lambda: opt-trade-decision — EventBridge every 5 min during market hours.

Reads recent nt_signals (read-only), opens paper short-straddle positions in
opt_paper_positions. Completely isolated from the equity trade_decision Lambda.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from src.config import Settings
from src.db.client import get_db
from src.news_trader.market_calendar import is_trading_day
from src.news_trader.prices import get_ltp
from src.options_trader import db as opt_db
from src.options_trader.nfo_specs import has_options, lot_size
from src.options_trader.paper_straddle import (
    build_entry_doc,
    nearest_monthly_expiry,
)

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _ist_now() -> datetime:
    return datetime.now(tz=timezone.utc).astimezone(_IST)


def _ist_minute_of_day() -> int:
    n = _ist_now()
    return n.hour * 60 + n.minute


def _is_market_hours(cfg: Settings) -> bool:
    if cfg.nt_bypass_market_hours:
        return True
    now_ist = _ist_now()
    if now_ist.weekday() >= 5:
        return False
    if not cfg.nt_bypass_market_holiday and not is_trading_day(now_ist.date()):
        return False
    m = now_ist.hour * 60 + now_ist.minute
    return 570 <= m <= 930  # 09:30–15:30 IST


async def _run(cfg: Settings) -> None:
    db = await get_db()
    await opt_db.ensure_indexes(db)

    # Find signals from the last signal window (last 30 min after 15-min entry delay)
    since = datetime.now(tz=timezone.utc) - timedelta(minutes=30)
    signal_docs = await db["nt_signals"].find(
        {
            "signal": {"$in": ["bullish", "bearish"]},
            "confidence": "high",
            "magnitude": {"$in": ["moderate", "major"]},
            "created_at": {"$gte": since},
            "stocks.0": {"$exists": True},
        }
    ).sort("created_at", 1).to_list(length=50)

    if not signal_docs:
        logger.info("opt_trade_decision: no eligible signals")
        return

    # Check current open count
    open_count = await opt_db.paper_positions(db).count_documents({"status": "open"})
    if open_count >= cfg.opt_max_positions:
        logger.info("opt_trade_decision: at capacity open=%d max=%d", open_count, cfg.opt_max_positions)
        return

    exp_date = nearest_monthly_expiry()
    per_slot = cfg.opt_total_capital_inr / cfg.opt_max_positions

    for sig in signal_docs:
        if open_count >= cfg.opt_max_positions:
            break
        sig_id = str(sig["_id"])

        # Eligible stocks: NIFTY500 ∩ has_options, up to opt_max_stocks_per_signal
        from src.news_trader.nifty500 import NIFTY_500
        from src.news_trader.nifty50 import NIFTY_50
        candidates = [
            s for s in (sig.get("stocks") or [])
            if s in NIFTY_500
            and (not cfg.nt_nifty50_exclusion or s not in NIFTY_50)
            and has_options(s)
        ][: cfg.opt_max_stocks_per_signal]

        for sym in candidates:
            if open_count >= cfg.opt_max_positions:
                break

            # Dedup: skip if already have a position for this signal+symbol
            exists = await opt_db.paper_positions(db).find_one({"signal_id": sig_id, "symbol": sym})
            if exists:
                continue

            spot = get_ltp(sym)
            if not spot:
                logger.warning("opt_trade_decision: no price for %s", sym)
                continue

            ls = lot_size(sym)
            # Approximate margin: 15% of contract value (simplified)
            margin_per_lot = round(spot * ls * 0.15, 0)
            lots = max(1, int(per_slot / margin_per_lot))
            lots = min(lots, cfg.opt_max_lots_per_position)

            now = datetime.now(tz=timezone.utc)
            doc = build_entry_doc(
                signal_doc=sig,
                symbol=sym,
                spot=spot,
                iv=cfg.opt_iv_baseline,
                lots=lots,
                lot_size=ls,
                now=now,
                exp_date=exp_date,
                target_pct=cfg.opt_target_pct,
                stop_pct=cfg.opt_stop_pct,
                max_hold_minutes=cfg.opt_max_hold_minutes,
                force_close_eod=cfg.opt_force_close_eod,
            )
            try:
                await opt_db.paper_positions(db).insert_one(doc)
                open_count += 1
                logger.info(
                    "opt_trade_decision: opened straddle sym=%s strike=%.1f "
                    "entry_prem=%.2f lots=%d exp=%s",
                    sym, doc["strike"], doc["entry_total_prem"], lots, exp_date.isoformat()
                )
            except Exception as exc:
                logger.warning("opt_trade_decision: insert_failed sym=%s err=%s", sym, exc)


def handler(event: dict, context: object) -> None:
    cfg = Settings()
    if not _is_market_hours(cfg):
        return
    # Entry cutoff: don't open positions too late for the monitor to act
    if _ist_minute_of_day() > cfg.opt_entry_cutoff_ist:
        return
    asyncio.run(_run(cfg))
