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
from src.news_trader.prices import get_ltp, get_market_snapshot
from src.news_trader.telegram import alert_trade_entered
from src.news_trader.trailing_sl import calc_qty, initial_trailing_sl
from src.news_trader.nifty500 import NIFTY_500
from src.scrapers.nse_market import fetch_nifty_vix_sync

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Conviction & regime gate thresholds. See plan notes — these are noise-reduction
# heuristics layered on top of the LLM signal, not formal model parameters.
_MEDIUM_CONF_MIN_SOURCES = 2   # medium-confidence signal needs ≥2 sources to trade
_NIFTY_CRASH_PCT = -1.5        # Nifty down >1.5% intraday → skip bullish entries
_VIX_ELEVATED = 22.0           # halve position size above this
_VIX_EXTREME = 28.0            # no entries at all above this


def _apply_conviction_gates(
    signal_doc: dict[str, Any],
    regime: dict[str, Any],
    base_position_size: float,
) -> tuple[bool, float, str]:
    """Pure decision function: should we trade this signal, and at what size?

    Returns (allow, effective_position_size, reason). Kept pure so it can be
    unit-tested without DB or yfinance mocks.
    """
    signal_id = str(signal_doc.get("_id", "?"))
    confidence = (signal_doc.get("confidence") or "").lower()
    direction = (signal_doc.get("signal") or "").lower()
    source_count = int(signal_doc.get("source_count", 1))

    # Gate 1 — conviction floor for medium-confidence signals.
    # Medium-conf + single-source has been the noisiest combination; require
    # at least 2 independent outlets to corroborate before risking capital.
    if confidence == "medium" and source_count < _MEDIUM_CONF_MIN_SOURCES:
        return False, 0.0, f"medium-conf single-source (sources={source_count})"

    # Gate C — magnitude floor.
    # LLM labels "minor" when expected move is <0.5%. Round-trip cost is ~0.3%
    # (STT 0.1% each side + brokerage + slippage), so minor signals can't break
    # even in expectation. Skip them regardless of confidence or source count.
    magnitude = (signal_doc.get("magnitude") or "").lower()
    if magnitude == "minor":
        return False, 0.0, "minor magnitude — expected <0.5% move, negative EV after costs"

    # Gate 2 — Nifty regime filter (applies to bullish entries only, since the
    # current trade_decision opens longs regardless of signal direction).
    nifty_change = regime.get("nifty_change_pct")
    nifty_above_ema50 = regime.get("nifty_above_ema50")
    if direction == "bullish":
        if nifty_change is not None and nifty_change < _NIFTY_CRASH_PCT:
            return False, 0.0, f"nifty crash gate (Δ={nifty_change:.2f}%)"
        if nifty_above_ema50 is False and confidence != "high":
            return False, 0.0, "Nifty below EMA50 + not high-conf"

    # Gate 3 — VIX-based sizing. Extreme vol = no entries; elevated = half size.
    vix = regime.get("vix")
    position_size = base_position_size
    if vix is not None:
        if vix > _VIX_EXTREME:
            return False, 0.0, f"VIX extreme ({vix:.1f})"
        if vix > _VIX_ELEVATED:
            position_size = base_position_size * 0.5

    logger.debug(
        "[TRADE] gates passed signal_id=%s conf=%s sources=%d nifty_Δ=%s vix=%s size=%.0f",
        signal_id, confidence, source_count, nifty_change, vix, position_size,
    )
    return True, position_size, "ok"


async def _process_signal(
    signal_doc: dict[str, Any],
    settings: Settings,
    regime: dict[str, Any] | None = None,
) -> int:
    """Returns number of positions opened."""
    db = get_db()
    paper = settings.trading_mode.lower() != "live"
    signal_id = str(signal_doc.get("_id", "?"))
    regime = regime or {}

    logger.info("[TRADE] evaluating signal_id=%s sector=%s signal=%s confidence=%s sources=%d stocks=%s",
                signal_id, signal_doc.get("sector"), signal_doc.get("signal"),
                signal_doc.get("confidence"), int(signal_doc.get("source_count", 1)),
                signal_doc.get("stocks"))

    # Conviction & regime gates run before any DB / price work — cheap rejects.
    allow, position_size_inr, reason = _apply_conviction_gates(
        signal_doc, regime, settings.nt_position_size_inr
    )
    if not allow:
        logger.info("[TRADE] skip signal_id=%s — gate rejected: %s", signal_id, reason)
        return 0
    if position_size_inr != settings.nt_position_size_inr:
        logger.info("[TRADE] signal_id=%s position size scaled %.0f → %.0f (reason: %s)",
                    signal_id, settings.nt_position_size_inr, position_size_inr, reason)

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

    # Filter D — Nifty 500 liquid universe whitelist.
    # Microcaps are excluded: at ₹50k position size, buying a thin stock can
    # represent a meaningful % of daily volume → poor fill + exit slippage.
    # Every stock the LLM names must be in the liquid universe to be traded.
    illiquid = [s for s in candidates if s not in NIFTY_500]
    candidates = [s for s in candidates if s in NIFTY_500]
    if illiquid:
        logger.info("[TRADE] signal_id=%s dropped illiquid stocks: %s", signal_id, illiquid)
    if not candidates:
        logger.info("[TRADE] skip signal_id=%s — no Nifty 500 stocks after universe filter", signal_id)
        return 0

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
        qty = calc_qty(position_size_inr, price)
        target = price * (1.0 + settings.nt_target_pct)
        sl = initial_trailing_sl(price, settings.nt_sl_pct)
        now = datetime.now(tz=timezone.utc)
        market_ctx = get_market_snapshot(sector=signal_doc.get("sector"))

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
            # Market context at entry — frozen snapshot for strategy evaluation
            "entry_nifty50": market_ctx["nifty50"],
            "entry_sector_index": market_ctx["sector_index"],
            # Gate inputs frozen at entry — lets post-hoc analysis bucket trades
            # by conviction (source_count) and regime (VIX, Nifty Δ).
            "entry_source_count": int(signal_doc.get("source_count", 1)),
            "entry_position_size_inr": position_size_inr,
            "entry_vix": regime.get("vix"),
            "entry_nifty_change_pct": regime.get("nifty_change_pct"),
            "entry_nifty_above_ema50": regime.get("nifty_above_ema50"),
            # Strategy params frozen at entry — allows backtesting "what if SL
            # was 2.5% instead of 1.5%" by querying nt_positions directly.
            "sl_pct_used": settings.nt_sl_pct,
            "target_pct_used": settings.nt_target_pct,
            "max_hold_days_used": settings.nt_max_hold_days,
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


async def _maybe_finalise_run(run_id: str, db: object) -> None:
    """Finalize nt_pipeline_runs when all signals for a run have been acted on."""
    from src.news_trader.pipeline_lifecycle import finalise_run

    run = await db["nt_pipeline_runs"].find_one(  # type: ignore[index]
        {"_id": ObjectId(run_id)}, {"triggered_at": 1, "status": 1}
    )
    if not run:
        logger.warning("[TRADE] run_id=%s not found in nt_pipeline_runs — skipping finalise", run_id)
        return
    if run.get("status") != "running":
        logger.info("[TRADE] run_id=%s already in status=%s — skipping finalise", run_id, run.get("status"))
        return

    triggered_at = run["triggered_at"]

    remaining = await signals(db).count_documents(  # type: ignore[arg-type]
        {"created_at": {"$gte": triggered_at}, "acted_on": False}
    )
    if remaining > 0:
        logger.info("[TRADE] run_id=%s — %d signal(s) still pending, not finalising yet", run_id, remaining)
        return

    # All signals acted on — collect final counts and finalize
    new_articles = await db["nt_news_raw"].count_documents(  # type: ignore[index]
        {"ingested_at": {"$gte": triggered_at}}
    )
    signals_created = await signals(db).count_documents(  # type: ignore[arg-type]
        {"created_at": {"$gte": triggered_at}}
    )
    positions_opened = await positions(db).count_documents(  # type: ignore[arg-type]
        {"entry_at": {"$gte": triggered_at}}
    )

    await finalise_run(run_id, {
        "new_articles": new_articles,
        "signals": signals_created,
        "positions_opened": positions_opened,
    })


async def _run(event: dict[str, Any], settings: Settings) -> dict[str, int]:
    db = get_db()
    await ensure_indexes(db)

    records = event.get("Records", [])
    logger.info("[TRADE] processing %d SQS record(s)", len(records))
    total_entered = 0
    run_ids_seen: set[str] = set()

    # Regime snapshot fetched once per batch (yfinance download ~250d, ~2-5s).
    # All signals in this batch trade against the same market state.
    try:
        regime = fetch_nifty_vix_sync()
    except Exception as exc:
        # Fail-open: never block trading because yfinance hiccuped. Gates that
        # depend on missing fields will simply be no-ops.
        logger.warning("[TRADE] regime fetch failed err=%s — proceeding without regime gates", exc)
        regime = {}
    logger.info("[TRADE] regime snapshot nifty_Δ=%s vix=%s above_ema50=%s",
                regime.get("nifty_change_pct"), regime.get("vix"), regime.get("nifty_above_ema50"))

    for record in records:
        try:
            attrs = record.get("messageAttributes", {})
            run_id: str | None = attrs.get("run_id", {}).get("stringValue")
            if run_id:
                run_ids_seen.add(run_id)

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

            n = await _process_signal(signal_doc, settings, regime=regime)
            total_entered += n

            await signals_coll(db).update_one(
                {"_id": ObjectId(signal_id)}, {"$set": {"acted_on": True}}
            )
        except Exception as exc:
            logger.error("[TRADE] error processing record err=%s", exc)

    # After processing the full SQS batch, check if any observed run is now complete
    for run_id in run_ids_seen:
        try:
            await _maybe_finalise_run(run_id, db)
        except Exception as exc:
            logger.error("[TRADE] finalise_run error run_id=%s err=%s", run_id, exc)

    logger.info("[TRADE] done — processed=%d total_positions_opened=%d", len(records), total_entered)
    return {"processed": len(records), "positions_opened": total_entered}


def handler(event: dict[str, Any], context: object) -> dict[str, int]:
    settings = Settings()
    return asyncio.run(_run(event, settings))
