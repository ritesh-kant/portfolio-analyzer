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
from ...db.repositories.paper_orders import PaperOrdersRepository
from ...db.repositories.trading_signals import TradingSignalsRepository
from ...db.repositories.virtual_portfolio import VirtualPortfolioRepository
from ...db.repositories.agent_logs import AgentLogsRepository
from ...db.repositories.intraday_bars import IntradayBarsRepository

logger = logging.getLogger(__name__)

_AGENT_NAME = "monitor_agent"
_MAX_POSITION_DAYS = 10   # force-close positions held longer than this


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

        stop_loss = float(order.get("stop_loss", 0))
        target = float(order.get("target", float("inf")))
        run_id = order.get("run_id", "monitor")

        try:
            # Max age check: force-close positions open longer than _MAX_POSITION_DAYS
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

            if stop_loss > 0 and current_price <= stop_loss:
                await _close_position(orders_repo, portfolio_repo, signals_repo, order, current_price, "stop_loss", db=db)
                await logs_repo.log(run_id, _AGENT_NAME, "info", f"stop_loss triggered {symbol} @ {current_price}")
                closed += 1

            elif current_price >= target:
                await _close_position(orders_repo, portfolio_repo, signals_repo, order, current_price, "target_hit", db=db)
                await logs_repo.log(run_id, _AGENT_NAME, "info", f"target_hit {symbol} @ {current_price}")
                closed += 1
            else:
                # Position survives — accumulate at live market price for MTM update
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
