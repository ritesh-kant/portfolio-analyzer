"""Lambda: opt-position-monitor — EventBridge every 3 min during market hours.

Two jobs in one Lambda (same schedule, same price data):
  1. Reprice open straddles → check exits → update / close opt_paper_positions
  2. Log opt_chain_snapshots for every signal active in the last 2 hours
     (BS synthetic prices always; NSE real IV best-effort)
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from src.config import Settings
from src.db.client import get_db
from src.news_trader.market_calendar import is_trading_day
from src.news_trader.prices import get_ltp, get_ltps
from src.options_trader import db as opt_db
from src.options_trader.nfo_specs import has_options, lot_size, strike_step
from src.options_trader.nse_chain import extract_atm, fetch_option_chain
from src.options_trader.paper_straddle import (
    check_exit,
    compute_close_update,
    expiry_datetime,
    nearest_monthly_expiry,
    price_straddle,
)
from src.options_trader.pricing import atm_strike, year_fraction

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _ist_now() -> datetime:
    return datetime.now(tz=timezone.utc).astimezone(_IST)


def _is_market_hours(cfg: Settings) -> bool:
    if cfg.nt_bypass_market_hours:
        return True
    n = _ist_now()
    if n.weekday() >= 5:
        return False
    if not cfg.nt_bypass_market_holiday and not is_trading_day(n.date()):
        return False
    m = n.hour * 60 + n.minute
    return 570 <= m <= 930


# ── Job 1: reprice & exit open straddles ─────────────────────────────────────

async def _monitor_positions(db, cfg: Settings, now: datetime) -> None:
    open_pos = await opt_db.paper_positions(db).find({"status": "open"}).to_list(length=200)
    if not open_pos:
        return

    symbols = list({p["symbol"] for p in open_pos})
    prices = get_ltps(symbols)

    exp_date = nearest_monthly_expiry()
    exp_dt = expiry_datetime(exp_date)

    for pos in open_pos:
        sym = pos["symbol"]
        spot = prices.get(sym) or get_ltp(sym)
        if not spot:
            logger.warning("opt_monitor: no price sym=%s", sym)
            continue

        strike = pos["strike"]
        t = year_fraction(now, exp_dt)
        iv = cfg.opt_iv_baseline  # flat IV — real IV comes from chain logger
        ce_p, pe_p, total_p = price_straddle(spot, strike, t, iv)

        exit_reason = check_exit(pos, total_p, now)
        if exit_reason:
            update = compute_close_update(pos, spot, ce_p, pe_p, exit_reason, now)
            await opt_db.paper_positions(db).update_one(
                {"_id": pos["_id"]}, {"$set": update}
            )
            logger.info(
                "opt_monitor: closed sym=%s reason=%s gross=%.0f net=%.0f",
                sym, exit_reason, update["gross_pnl"], update["net_pnl"],
            )
        else:
            current_pnl = round(
                (pos["entry_total_prem"] - total_p) * pos["lots"] * pos["lot_size"], 2
            )
            await opt_db.paper_positions(db).update_one(
                {"_id": pos["_id"]},
                {"$set": {
                    "current_spot": spot,
                    "current_ce_prem": ce_p,
                    "current_pe_prem": pe_p,
                    "current_total_prem": total_p,
                    "current_pnl": current_pnl,
                    "last_updated_at": now,
                }},
            )


# ── Job 2: chain snapshot logger ─────────────────────────────────────────────

async def _log_chain_snapshots(db, cfg: Settings, now: datetime) -> None:
    # Log for signals fired in the last 2 hours (covers the full monitoring window)
    since = now - timedelta(hours=2)
    signals = await db["nt_signals"].find(
        {
            "signal": {"$in": ["bullish", "bearish"]},
            "confidence": "high",
            "created_at": {"$gte": since},
            "stocks.0": {"$exists": True},
        }
    ).to_list(length=30)

    if not signals:
        return

    exp_date = nearest_monthly_expiry()
    exp_dt = expiry_datetime(exp_date)

    # Collect unique F&O-eligible symbols, carrying the signal anchor so each
    # snapshot is self-contained for backtesting (exact minutes-since-signal,
    # robust to any future change/pruning of nt_signals).
    sym_signal_map: dict[str, dict] = {}
    for sig in signals:
        for s in (sig.get("stocks") or []):
            if has_options(s) and s not in sym_signal_map:
                sym_signal_map[s] = {
                    "signal_id": str(sig["_id"]),
                    "signal_created_at": sig.get("created_at"),
                    "signal_type": sig.get("signal"),
                }

    if not sym_signal_map:
        return

    prices = get_ltps(list(sym_signal_map.keys()))
    t = year_fraction(now, exp_dt)
    iv = cfg.opt_iv_baseline

    docs = []
    for sym, sig_meta in sym_signal_map.items():
        spot = prices.get(sym)
        if not spot:
            continue
        step = strike_step(sym)
        K = atm_strike(spot, step)

        # Synthetic BS prices (always available)
        from src.options_trader.pricing import price_option
        ce_bs = round(price_option(spot, K, t, iv, "CE"), 2)
        pe_bs = round(price_option(spot, K, t, iv, "PE"), 2)

        snap: dict = {
            "signal_id": sig_meta["signal_id"],
            "signal_created_at": sig_meta["signal_created_at"],
            "signal_type": sig_meta["signal_type"],
            "symbol": sym,
            "snapshot_at": now,
            "spot_price": spot,
            "expiry": exp_date.isoformat(),
            "atm_strike": K,
            "bs_ce_prem": ce_bs,
            "bs_pe_prem": pe_bs,
            "bs_straddle_mid": round(ce_bs + pe_bs, 2),
            "bs_iv_used": iv,
            "iv_source": "synthetic",
        }

        # Best-effort real IV from NSE chain
        chain = fetch_option_chain(sym, timeout=8)
        if chain:
            row = extract_atm(chain, spot, step)
            if row:
                snap.update({
                    "ce_bid": row["ce_bid"],
                    "ce_ask": row["ce_ask"],
                    "ce_iv": row["ce_iv"],
                    "ce_oi": row["ce_oi"],
                    "pe_bid": row["pe_bid"],
                    "pe_ask": row["pe_ask"],
                    "pe_iv": row["pe_iv"],
                    "pe_oi": row["pe_oi"],
                    "nse_straddle_mid": round(
                        (row["ce_bid"] + row["ce_ask"]) / 2
                        + (row["pe_bid"] + row["pe_ask"]) / 2, 2
                    ),
                    "iv_source": "nse",
                })

        docs.append(snap)

    if docs:
        try:
            await opt_db.chain_snapshots(db).insert_many(docs, ordered=False)
            logger.info("opt_monitor: logged %d chain snapshots iv_src=%s", len(docs),
                        docs[0].get("iv_source", "?"))
        except Exception as exc:
            logger.warning("opt_monitor: snapshot_insert_failed err=%s", exc)


# ── Entry point ───────────────────────────────────────────────────────────────

async def _run(cfg: Settings) -> None:
    db = await get_db()
    try:
        await opt_db.ensure_indexes(db)
    except Exception as exc:
        # Index setup must never take down monitoring/snapshot logging.
        logger.warning("opt_monitor: ensure_indexes failed (continuing): %s", exc)
    now = datetime.now(tz=timezone.utc)
    await asyncio.gather(
        _monitor_positions(db, cfg, now),
        _log_chain_snapshots(db, cfg, now),
    )


def handler(event: dict, context: object) -> None:
    cfg = Settings()
    if not _is_market_hours(cfg):
        return
    asyncio.run(_run(cfg))
