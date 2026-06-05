"""Lambda: trade-decision — SQS trigger from news-signals queue (delayed 15 min).

Capital allocation: one knob, nt_total_capital_inr. Per-trade size is derived
as total / nt_max_positions (equal-weight per slot), so total exposure is
bounded by construction. A backstop additionally refuses any entry that would
push summed open entry_value over nt_total_capital_inr.

For each signal:
  1. Check open position count < nt_max_positions
  2. For each stock in signal (up to nt_max_stocks_per_signal):
     a. Fetch current price
     b. Calculate qty = floor(per_slot_size / price)
     c. Backstop: skip if it would breach nt_total_capital_inr
     d. In paper mode: insert open position to nt_positions
     e. Send Telegram alert
  3. Mark signal as acted_on
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, cast

from bson import ObjectId

from src.config import Settings
from src.db.client import get_db
from src.news_trader.db import ensure_indexes, positions, signals
from src.news_trader.telegram import alert_trade_entered
from src.news_trader.trailing_sl import calc_qty, initial_stop
from src.news_trader.nifty500 import NIFTY_500

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

_IST_OFFSET_SEC = 5 * 3600 + 30 * 60  # UTC+5:30


def _ist_minute_of_day() -> int:
    """Current IST time expressed as minutes since midnight (0–1439)."""
    ts = datetime.now(tz=timezone.utc).timestamp() + _IST_OFFSET_SEC
    ist = datetime.fromtimestamp(ts)
    return ist.hour * 60 + ist.minute


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

    # Gate 0 — direction filter. This is a long-only system: every position is
    # opened as a long (target above entry, SL below). A bearish thesis can't be
    # expressed without shorting, so trading a bearish signal long would bet
    # against the AI's own call. Skip anything that isn't bullish.
    if direction != "bullish":
        return False, 0.0, f"non-bullish signal ({direction or 'unknown'}) — long-only system"

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

    # Gate 2 — Nifty regime filter. All entries are bullish longs (guaranteed by
    # Gate 0), so a falling/weak market is a headwind we screen against.
    nifty_change = regime.get("nifty_change_pct")
    nifty_above_ema50 = regime.get("nifty_above_ema50")
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
    pipeline_run_id: str | None = None,
) -> tuple[int, str]:
    """Returns (positions_opened, gate_result). gate_result is 'ok' when conviction
    gates passed; otherwise a short rejection reason string stored on the signal."""
    db = get_db()
    paper = settings.trading_mode.lower() != "live"
    signal_id = str(signal_doc.get("_id", "?"))
    regime = regime or {}

    logger.info("[TRADE] evaluating signal_id=%s sector=%s signal=%s confidence=%s sources=%d stocks=%s",
                signal_id, signal_doc.get("sector"), signal_doc.get("signal"),
                signal_doc.get("confidence"), int(signal_doc.get("source_count", 1)),
                signal_doc.get("stocks"))

    # Entry cutoff — safety net for SQS messages that linger past the EventBridge
    # schedule cutoff (which already stops the ingester at 14:15 IST). If a signal
    # somehow reaches trade-decision after 14:30 IST, reject it rather than open a
    # position with <60 min left and overnight gap risk, no time to build cushion.
    # Bypassed in dev (nt_bypass_market_hours) so local testing isn't blocked.
    if not settings.nt_bypass_market_hours:
        ist_min = _ist_minute_of_day()
        if ist_min > settings.nt_entry_cutoff_ist:
            cutoff_hhmm = f"{settings.nt_entry_cutoff_ist // 60:02d}:{settings.nt_entry_cutoff_ist % 60:02d}"
            logger.info("[TRADE] skip signal_id=%s — entry cutoff (ist=%d > %d = %s IST)",
                        signal_id, ist_min, settings.nt_entry_cutoff_ist, cutoff_hhmm)
            return 0, f"entry_cutoff (after {cutoff_hhmm} IST)"

    # Single source of truth for sizing: total capital split equally across slots.
    # Logged loudly so the effective per-trade size is never invisible (the ₹50k
    # surprise happened because nobody could see what each trade was actually using).
    per_slot_size = settings.nt_total_capital_inr / settings.nt_max_positions
    logger.info("[TRADE] capital=₹%.0f → ₹%.0f/slot across %d slots",
                settings.nt_total_capital_inr, per_slot_size, settings.nt_max_positions)

    # Conviction & regime gates run before any DB / price work — cheap rejects.
    allow, position_size_inr, reason = _apply_conviction_gates(
        signal_doc, regime, per_slot_size
    )
    if not allow:
        logger.info("[TRADE] skip signal_id=%s — gate rejected: %s", signal_id, reason)
        return 0, reason
    if position_size_inr != per_slot_size:
        logger.info("[TRADE] signal_id=%s position size scaled %.0f → %.0f (reason: %s)",
                    signal_id, per_slot_size, position_size_inr, reason)

    # Check available capacity
    open_count = await positions(db).count_documents({"status": "open"})
    capacity = settings.nt_max_positions - open_count
    logger.info("[TRADE] capacity check open=%d max=%d available=%d",
                open_count, settings.nt_max_positions, capacity)
    if capacity <= 0:
        logger.info("[TRADE] skip — portfolio full open=%d max=%d", open_count, settings.nt_max_positions)
        return 0, "portfolio_full"

    stocks = signal_doc.get("stocks", [])
    if not stocks:
        logger.info("[TRADE] skip — no stocks in signal")
        return 0, "no_stocks"

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
        return 0, "no_nifty500_stocks"

    logger.info("[TRADE] evaluating %d stock(s): %s", len(candidates), candidates)

    # Pre-fetch all candidate prices and the market snapshot concurrently —
    # avoids N+1 serial yfinance calls (one per stock + one per iteration for snapshot).
    # 20-second hard timeout guards against yfinance hanging on Yahoo Finance throttling.
    sector = signal_doc.get("sector")
    from src.news_trader.prices import get_ltps, get_market_snapshot, get_volume_data  # lazy: yfinance/pandas
    # Kick off volume fetches before awaiting prices so both run concurrently.
    # Separate from the price gather to preserve mypy's typed inference on price_map/market_ctx
    # (asyncio.gather with *args spread loses all return-type info for every element).
    _vol_tasks = [asyncio.create_task(asyncio.to_thread(get_volume_data, sym)) for sym in candidates]
    try:
        price_map, market_ctx = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(get_ltps, candidates),
                asyncio.to_thread(get_market_snapshot, sector),
            ),
            timeout=20.0,
        )
    except TimeoutError:
        for t in _vol_tasks:
            t.cancel()
        logger.warning("[TRADE] price fetch timed out for signal_id=%s candidates=%s — skipping",
                       signal_id, candidates)
        return 0, "price_fetch_timeout"
    logger.info("[TRADE] prices fetched: %s", {s: f"₹{p:.2f}" for s, p in price_map.items()})

    volume_map: dict[str, dict[str, Any]] = {}
    try:
        _vol_results = await asyncio.wait_for(asyncio.gather(*_vol_tasks), timeout=10.0)
        volume_map = dict(zip(candidates, cast(list[dict[str, Any]], _vol_results)))
        logger.info("[TRADE] volume fetched: %s", {s: v.get("volume_ratio") for s, v in volume_map.items()})
    except Exception as exc:
        logger.warning("[TRADE] volume fetch failed err=%s — proceeding without", exc)

    # Backstop: total cash already deployed across open positions. Equal-weight
    # sizing already keeps us under nt_total_capital_inr by construction, so this
    # guard should never fire in normal operation — it's defense-in-depth against
    # out-of-band inserts or future sizing bugs. Basis is entry_value (cash
    # committed at entry), not mark-to-market, since the cap is about deployed cash.
    agg = await positions(db).aggregate([
        {"$match": {"status": "open"}},
        {"$group": {"_id": None, "total": {"$sum": "$entry_value"}}},
    ]).to_list(1)
    projected_invested = float(agg[0]["total"]) if agg else 0.0
    logger.info("[TRADE] deployed=₹%.0f / cap=₹%.0f", projected_invested, settings.nt_total_capital_inr)

    # Sector cap — count open positions in the same sector before the loop so we
    # don't open a 3rd Pharma position just because three signals fired in sequence.
    # We track `sector_entered_this_call` separately because a single SQS batch
    # can open multiple positions (up to nt_max_stocks_per_signal) and each one
    # bumps the effective sector count before the next DB read.
    # (`sector` was already assigned above for the market snapshot fetch.)
    sector_open = await positions(db).count_documents({"status": "open", "sector": sector}) if sector else 0
    sector_entered_this_call = 0
    logger.info("[TRADE] sector=%s open=%d cap=%d", sector, sector_open, settings.nt_max_positions_per_sector)

    for symbol in candidates:
        # Sector cap — reject if this entry would exceed nt_max_positions_per_sector.
        # Checked per-symbol so a single signal opening 2 stocks doesn't bypass the cap
        # (sector_entered_this_call tracks entries already committed this batch).
        if sector and (sector_open + sector_entered_this_call) >= settings.nt_max_positions_per_sector:
            logger.info("[TRADE] skip %s — sector cap hit sector=%s open=%d+%d >= %d",
                        symbol, sector, sector_open, sector_entered_this_call,
                        settings.nt_max_positions_per_sector)
            continue

        # Check if we already have an open position in this symbol
        existing = await positions(db).find_one({"symbol": symbol, "status": "open"})
        if existing:
            logger.info("[TRADE] skip %s — already have open position", symbol)
            continue

        price = price_map.get(symbol)
        if not price:
            logger.warning("[TRADE] no price for %s — skipping", symbol)
            continue

        if price > position_size_inr:
            logger.info("[TRADE] skip %s — LTP ₹%.2f exceeds position budget ₹%.0f",
                        symbol, price, position_size_inr)
            continue

        logger.info("[TRADE] %s LTP=%.2f — calculating position size...", symbol, price)
        qty = calc_qty(position_size_inr, price)
        trade_value = price * qty

        # Backstop: refuse (don't shrink) any entry that would breach the total
        # cap. Shrinking would let one early trade balloon and starve the rest —
        # the opposite of equal-weight diversification.
        if projected_invested + trade_value > settings.nt_total_capital_inr:
            logger.info("[TRADE] skip %s — would breach capital cap (deployed=₹%.0f + ₹%.0f > ₹%.0f)",
                        symbol, projected_invested, trade_value, settings.nt_total_capital_inr)
            continue

        target = price * (1.0 + settings.nt_target_pct)
        sl = initial_stop(price, settings.nt_initial_sl_pct)
        now = datetime.now(tz=timezone.utc)

        position_doc = {
            "symbol": symbol,
            "signal_id": str(signal_doc["_id"]),
            "signal": signal_doc.get("signal"),
            "sector": signal_doc.get("sector"),
            "confidence": signal_doc.get("confidence"),
            # Expected-move-size label frozen alongside confidence so closed-trade
            # analysis can bucket outcomes by magnitude (do "major" calls move more?).
            "magnitude": signal_doc.get("magnitude"),
            "entry_price": price,
            # Entry-timing analysis: how far did we chase the move during the
            # 15-min SQS delay? signal_price is the LTP at classification time;
            # entry_chase_pct > 0 means we bought after a rise (chasing).
            # None when price_at_signal wasn't captured (old signals, fetch fail).
            "signal_price": (signal_doc.get("price_at_signal") or {}).get(symbol),
            "entry_chase_pct": (
                round((price - sp) / sp * 100, 3)
                if (sp := (signal_doc.get("price_at_signal") or {}).get(symbol))
                else None
            ),
            # Volume confirmation: ratio > 1 means above-average activity on entry day.
            # Raw values stored separately so analysis can apply time-of-day normalization.
            # Note: yfinance volume is ~15 min delayed; ratio reflects delayed snapshot.
            "volume_current_day": volume_map.get(symbol, {}).get("current_day_volume"),
            "volume_avg_daily": volume_map.get(symbol, {}).get("avg_daily_volume"),
            "volume_ratio_at_entry": volume_map.get(symbol, {}).get("volume_ratio"),
            "qty": qty,
            "entry_value": trade_value,
            "entry_at": now,
            "highest_price": price,
            # Low-water mark (MAE) seed — mirrors highest_price. sl_monitor ratchets
            # this down each tick so we can later see how far winners dipped first.
            "lowest_price": price,
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
            # Split-stop params (see trailing_sl.update_stop). sl_monitor keys off
            # initial_sl_pct_used to pick the split-stop path; positions missing it
            # are treated as legacy pure-trail (sl_pct_used).
            "initial_sl_pct_used": settings.nt_initial_sl_pct,
            "trail_sl_pct_used": settings.nt_trail_sl_pct,
            "trail_activate_pct_used": settings.nt_trail_activate_pct,
            "sl_pct_used": settings.nt_sl_pct,
            "target_pct_used": settings.nt_target_pct,
            "max_hold_days_used": settings.nt_max_hold_days,
            "pipeline_run_id": pipeline_run_id,
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
        projected_invested += trade_value
        sector_entered_this_call += 1
        entered += 1

    logger.info("[TRADE] signal_id=%s done — %d position(s) opened", signal_id, entered)
    return entered, "ok"


async def _maybe_finalise_run(run_id: str, db: object) -> None:
    """Finalize nt_pipeline_runs when all signals for a run have been acted on."""
    from src.news_trader.pipeline_lifecycle import finalise_run

    run = await db["nt_pipeline_runs"].find_one(  # type: ignore[index]
        {"_id": ObjectId(run_id)}, {"triggered_at": 1, "status": 1, "new_articles": 1}
    )
    if not run:
        logger.warning("[TRADE] run_id=%s not found in nt_pipeline_runs — skipping finalise", run_id)
        return
    if run.get("status") != "running":
        logger.info("[TRADE] run_id=%s already in status=%s — skipping finalise", run_id, run.get("status"))
        return

    triggered_at = run["triggered_at"]

    remaining = await signals(db).count_documents(  # type: ignore[arg-type]
        {"pipeline_run_id": run_id, "acted_on": False}
    )
    if remaining > 0:
        logger.info("[TRADE] run_id=%s — %d signal(s) still pending, not finalising yet", run_id, remaining)
        return

    # All signals acted on — collect final counts and finalize.
    # new_articles was stored by the ingester via stamp_processing_done; fall back
    # to a bounded recount for runs that predate that field.
    now = datetime.now(timezone.utc)
    new_articles = run.get("new_articles") or await db["nt_news_raw"].count_documents(  # type: ignore[index]
        {"ingested_at": {"$gte": triggered_at, "$lte": now}}
    )
    signals_created = await signals(db).count_documents(  # type: ignore[arg-type]
        {"pipeline_run_id": run_id}
    )
    # positions carry pipeline_run_id — exact match, no time window needed
    positions_opened = await positions(db).count_documents(  # type: ignore[arg-type]
        {"pipeline_run_id": run_id}
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
    from src.scrapers.nse_market import fetch_nifty_vix_sync  # lazy: yfinance/pandas
    try:
        regime = await asyncio.wait_for(
            asyncio.to_thread(fetch_nifty_vix_sync), timeout=15.0
        )
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

            n, gate_result = await _process_signal(signal_doc, settings, regime=regime, pipeline_run_id=run_id)
            total_entered += n

            await signals_coll(db).update_one(
                {"_id": ObjectId(signal_id)},
                {"$set": {"acted_on": True, "gate_result": gate_result}},
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
