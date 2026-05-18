"""monitor_agent — thesis-break detection; closes paper positions at stop-loss or target.

Runs independently of the main LangGraph pipeline, triggered every 5 minutes
during NSE market hours (09:15–15:30 IST, Monday–Friday).

For each OPEN paper order it:
  1. Fetches the latest price via yfinance (period="1d", interval="1m").
  2. Checks stop-loss breach: current_price ≤ stop_loss  → close as STOPPED.
  3. Checks target hit:       current_price ≥ target     → close as CLOSED (profit).
  4. Writes exit data back to paper_orders and updates virtual_portfolio.

Returns a summary dict for the /pipeline/monitor response.
"""

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any

import yfinance as yf

from ...db.client import get_db
from ...db.repositories.paper_orders import PaperOrdersRepository, cooloff_until_date
from ...db.repositories.trading_signals import TradingSignalsRepository
from ...db.repositories.virtual_portfolio import VirtualPortfolioRepository
from ...db.repositories.agent_logs import AgentLogsRepository
from ...db.repositories.intraday_bars import IntradayBarsRepository

logger = logging.getLogger(__name__)

_AGENT_NAME = "monitor_agent"
_MAX_POSITION_DAYS = 30   # extended for chandelier trail to ride trends
_TP1_FRACTION = 0.5       # fraction of shares sold at TP1
_TRAIL_ATR_MULTIPLE = 3.0 # chandelier: trail = highest_close − N × ATR


_PRICE_FETCH_TIMEOUT_S = 10.0


async def _fetch_price(symbol: str) -> tuple[float | None, list[dict[str, Any]] | None]:
    """Fetch latest trade price and full 1m bar list for a symbol.

    Returns (latest_close, bars) where bars is a list of {time, open, high, low, close, volume}
    dicts suitable for archival. Both values are None on failure or timeout.
    """
    loop = asyncio.get_event_loop()
    try:
        def _download() -> tuple[float | None, list[dict[str, Any]] | None]:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1d", interval="1m")
            if hist.empty:
                return None, None
            latest_close = float(hist["Close"].iloc[-1])
            bars = [
                {
                    "time": idx.strftime("%H:%M"),
                    "open": round(float(row["Open"]), 2),
                    "high": round(float(row["High"]), 2),
                    "low": round(float(row["Low"]), 2),
                    "close": round(float(row["Close"]), 2),
                    "volume": int(row["Volume"]),
                }
                for idx, row in hist.iterrows()
            ]
            return latest_close, bars

        return await asyncio.wait_for(
            loop.run_in_executor(None, _download),
            timeout=_PRICE_FETCH_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning("monitor price_fetch_timeout symbol=%s after %.0fs", symbol, _PRICE_FETCH_TIMEOUT_S)
        return None, None
    except Exception as exc:
        logger.warning("monitor price_fetch_failed symbol=%s error=%s", symbol, exc)
        return None, None


_EXIT_NOTES = {
    "stop_loss": "Stop-loss triggered — thesis failed. Price fell to stop level.",
    "target_hit": "Target hit — thesis correct. Price reached upside target.",
    "tp1": "TP1 partial profit taken at +1.5R. Remainder rides chandelier trail.",
    "trail": "Chandelier trailing stop hit — trend exit on remainder after TP1.",
    "max_age": f"Force-closed after {_MAX_POSITION_DAYS} days — maximum hold period reached.",
}


async def _log_thesis_break(
    db: Any,
    order: dict[str, Any],
    exit_price: float,
    return_pct: float,
) -> None:
    """Record a stop-loss event in trading_thesis_breaks so future signals on the
    same stock can factor in a recent thesis failure via _fetch_hist_context()."""
    try:
        col = db["trading_thesis_breaks"]
        await col.insert_one({
            "symbol": order["symbol"],
            "date": datetime.now(timezone.utc).date().isoformat(),
            "run_id": order.get("run_id", ""),
            "entry_price": float(order["entry_price"]),
            "exit_price": exit_price,
            "return_pct": return_pct,
            "original_reasoning": order.get("reasoning", ""),
            "createdAt": datetime.now(timezone.utc),
        })
    except Exception as exc:
        logger.warning("monitor thesis_break_log_failed symbol=%s error=%s", order["symbol"], exc)


async def _partial_close_at_tp1(
    orders_repo: PaperOrdersRepository,
    portfolio_repo: VirtualPortfolioRepository,
    *,
    order: dict[str, Any],
    exit_price: float,
    shares_to_close: int,
) -> None:
    """Take half (TP1_FRACTION) off at +1.5R; mutate the order to ride remainder.

    The closed slice is appended to the order's `partial_exits` array (audit
    trail). `original_stop` is moved to breakeven on the remainder so the
    remaining shares cannot go below entry — the worst case after TP1 is now
    a flat trade on the remainder plus the TP1 partial gain.
    """
    entry_price = float(order["entry_price"])
    total_shares = int(order["shares"])

    cost_basis_closed = round(shares_to_close * entry_price, 2)
    exit_value = round(shares_to_close * exit_price, 2)
    pnl = round(exit_value - cost_basis_closed, 2)
    return_pct = round((pnl / cost_basis_closed) * 100, 4) if cost_basis_closed else 0.0

    remaining_shares = total_shares - shares_to_close
    remaining_position_value = round(remaining_shares * entry_price, 2)

    partial_exit = {
        "shares_closed": shares_to_close,
        "exit_price": round(exit_price, 2),
        "pnl": pnl,
        "return_pct": return_pct,
        "reason": "tp1",
        "exit_date": datetime.now(timezone.utc).date().isoformat(),
        "createdAt": datetime.now(timezone.utc),
    }

    await orders_repo.partial_close(
        order["_id"],
        shares_closed=shares_to_close,
        remaining_shares=remaining_shares,
        remaining_position_value=remaining_position_value,
        partial_exit=partial_exit,
        new_original_stop=round(entry_price, 2),
    )
    # Release cash from the closed slice and book the gain. position_value
    # passed is the cost basis of the closed slice so the portfolio repo's
    # invested-counter decrements correctly.
    await portfolio_repo.close_position(exit_value, cost_basis_closed, pnl > 0)
    await portfolio_repo.record_close_pnl(pnl)

    logger.info(
        "monitor tp1_partial symbol=%s sold=%d/%d entry=%.2f exit=%.2f pnl=%.2f (%.2f%%)",
        order["symbol"], shares_to_close, total_shares,
        entry_price, exit_price, pnl, return_pct,
    )


async def _close_position(
    orders_repo: PaperOrdersRepository,
    portfolio_repo: VirtualPortfolioRepository,
    signals_repo: TradingSignalsRepository,
    order: dict[str, Any],
    exit_price: float,
    reason: str,
    db: Any = None,
) -> None:
    """Write exit data to paper_orders, release cash, and back-fill trading_signals."""
    order_id = order["_id"]
    entry_price = float(order["entry_price"])
    shares = int(order["shares"])
    position_value = float(order["position_value"])

    exit_value = round(shares * exit_price, 2)
    pnl = exit_value - position_value
    return_pct = round((pnl / position_value) * 100, 4) if position_value else 0.0
    was_correct = pnl > 0
    outcome_date = datetime.now(timezone.utc).date().isoformat()

    exit_note = (
        f"{_EXIT_NOTES.get(reason, reason)} "
        f"Entry ₹{entry_price:.2f} → Exit ₹{exit_price:.2f} ({return_pct:+.2f}%)"
    )
    await orders_repo.close_order(order_id, exit_price, return_pct, was_correct, exit_note=exit_note, exit_reason=reason)
    logger.info(
        "monitor closed symbol=%s reason=%s entry=%.2f exit=%.2f pnl=%.2f (%.2f%%)",
        order["symbol"], reason, entry_price, exit_price, pnl, return_pct,
    )

    # Back-fill outcome onto the originating trading_signal document.
    run_id = order.get("run_id", "")
    if run_id:
        try:
            await signals_repo.update_outcome(
                run_id=run_id,
                symbol=order["symbol"],
                actual_return_pct=return_pct,
                was_correct=was_correct,
                outcome_date=outcome_date,
            )
        except Exception as exc:
            logger.warning(
                "monitor outcome_backfill_failed symbol=%s run_id=%s error=%s",
                order["symbol"], run_id, exc,
            )

    # Log thesis breaks (stop-loss events) for future signal calibration
    if reason == "stop_loss" and db is not None:
        await _log_thesis_break(db, order, exit_price, return_pct)
        # Set cooloff: block re-entry on this symbol for 15 business days
        cooloff = cooloff_until_date(business_days=15)
        await orders_repo.set_cooloff(order["_id"], cooloff)
        logger.info(
            "monitor cooloff_set symbol=%s cooloff_until=%s",
            order["symbol"], cooloff,
        )

    # Return cash and update portfolio stats
    await portfolio_repo.close_position(exit_value, position_value, was_correct)
    # Update daily circuit-breaker counter (pnl negative on losses)
    await portfolio_repo.record_close_pnl(pnl)


async def run_monitor() -> dict[str, Any]:
    """Scan all open positions, trigger stops/targets. Returns a summary."""
    db = get_db()
    orders_repo = PaperOrdersRepository(db)
    portfolio_repo = VirtualPortfolioRepository(db)
    signals_repo = TradingSignalsRepository(db)
    logs_repo = AgentLogsRepository(db)
    bars_repo = IntradayBarsRepository(db)

    open_orders = await orders_repo.get_open_orders()
    if not open_orders:
        logger.info("monitor no open positions")
        return {"checked": 0, "closed": 0, "errors": []}

    symbols = list({o["symbol"] for o in open_orders})
    fetch_results: list[tuple[float | None, list[dict[str, Any]] | None]] = await asyncio.gather(
        *[_fetch_price(s) for s in symbols]
    )
    prices: dict[str, float | None] = {}
    today = date.today().isoformat()
    for symbol, (latest_close, bars) in zip(symbols, fetch_results):
        prices[symbol] = latest_close
        if bars:
            try:
                await bars_repo.upsert(symbol, today, "1m", bars)
            except Exception as exc:
                logger.warning("monitor bars_save_failed symbol=%s error=%s", symbol, exc)

    closed = 0
    errors: list[str] = []
    remaining_market_value = 0.0  # live value of positions that survive this cycle

    for order in open_orders:
        symbol = order["symbol"]
        current_price = prices.get(symbol)

        if current_price is None:
            errors.append(f"{symbol}: price fetch failed")
            remaining_market_value += float(order.get("position_value", 0.0))
            continue

        # Trailing-stop + partial-profit ladder state. Legacy orders (placed
        # before this feature) will lack these fields — fall back gracefully:
        #   tp1_price == 0  → no TP1, behave as old (stop / target / max_age).
        #   tp1_taken absent → treated as False.
        stop_loss = float(order.get("stop_loss", 0))
        target = float(order.get("target", float("inf")))
        atr_at_entry = float(order.get("atr_at_entry", 0) or 0)
        original_stop = float(order.get("original_stop", stop_loss))
        tp1_price = float(order.get("tp1_price", 0) or 0)
        tp1_taken = bool(order.get("tp1_taken", False))
        highest_close = float(order.get("highest_close", float(order.get("entry_price", 0))))
        trailing_stop = float(order.get("trailing_stop", original_stop))
        run_id = order.get("run_id", "monitor")

        try:
            # Update chandelier high-water mark + trailing stop (ratchet up only).
            # Done daily on every cycle regardless of exit branch so the state is
            # always fresh when TP1 fires or trail eventually triggers.
            if atr_at_entry > 0:
                new_highest = max(highest_close, current_price)
                candidate_trail = new_highest - _TRAIL_ATR_MULTIPLE * atr_at_entry
                new_trail = max(trailing_stop, candidate_trail)
                if new_highest != highest_close or new_trail != trailing_stop:
                    await orders_repo.update_trail(
                        order["_id"],
                        highest_close=round(new_highest, 2),
                        trailing_stop=round(new_trail, 2),
                    )
                    highest_close = new_highest
                    trailing_stop = new_trail

            # Max-age check first — applies regardless of TP1 state.
            order_date_str = order.get("date", "")
            if order_date_str:
                try:
                    order_date = date.fromisoformat(order_date_str)
                    age_days = (date.today() - order_date).days
                    if age_days >= _MAX_POSITION_DAYS:
                        await _close_position(orders_repo, portfolio_repo, signals_repo, order, current_price, "max_age", db=db)
                        await logs_repo.log(run_id, _AGENT_NAME, "warn", f"max_age {symbol} held {age_days}d @ {current_price}")
                        closed += 1
                        continue
                except ValueError:
                    pass

            # Exit ladder. Order matters: pre-TP1 stop must be checked before
            # TP1 to avoid promoting a stopped-out trade into a partial.
            exit_reason: str | None = None
            if tp1_taken:
                if trailing_stop > 0 and current_price <= trailing_stop:
                    exit_reason = "trail"
            else:
                if original_stop > 0 and current_price <= original_stop:
                    exit_reason = "stop_loss"
                elif tp1_price > 0 and current_price >= tp1_price:
                    # TP1 partial close — halve the position, flip flags, ride remainder.
                    shares = int(order.get("shares", 0))
                    shares_to_close = max(1, int(shares * _TP1_FRACTION))
                    if shares_to_close >= shares:
                        # Position too small to split → treat as full close at TP1.
                        await _close_position(orders_repo, portfolio_repo, signals_repo, order, current_price, "tp1", db=db)
                        closed += 1
                    else:
                        await _partial_close_at_tp1(
                            orders_repo, portfolio_repo,
                            order=order,
                            exit_price=current_price,
                            shares_to_close=shares_to_close,
                        )
                        await logs_repo.log(
                            run_id, _AGENT_NAME, "info",
                            f"tp1 {symbol} sold={shares_to_close}/{shares} @ {current_price:.2f}",
                        )
                        # Remainder continues — accumulate at live price for MTM.
                        remaining_market_value += current_price * (shares - shares_to_close)
                    continue
                elif tp1_price == 0 and current_price >= target:
                    # Legacy path: orders without TP1 fall back to the fixed target.
                    exit_reason = "target_hit"

            if exit_reason:
                await _close_position(orders_repo, portfolio_repo, signals_repo, order, current_price, exit_reason, db=db)
                await logs_repo.log(run_id, _AGENT_NAME, "info", f"{exit_reason} {symbol} @ {current_price:.2f}")
                closed += 1
            else:
                remaining_market_value += current_price * int(order.get("shares", 0))

        except Exception as exc:
            err = f"{symbol}: {exc!s}"
            errors.append(err)
            logger.error("monitor error %s", err)

    # Update portfolio total_value to live market prices so the portfolio floor
    # circuit-breaker in order_agent responds to unrealized losses, not just realized ones.
    try:
        await portfolio_repo.update_mark_to_market(remaining_market_value)
    except Exception as exc:
        logger.error("monitor mark_to_market_update_failed error=%s", exc)
        errors.append(f"mark_to_market_update failed: {exc!s}")

    summary = {
        "checked": len(open_orders),
        "closed": closed,
        "errors": errors,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    logger.info("monitor summary checked=%d closed=%d errors=%d", len(open_orders), closed, len(errors))
    return summary
